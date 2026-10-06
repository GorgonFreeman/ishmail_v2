"""Robust List-Unsubscribe handling (RFC 2369 + RFC 8058 one-click)."""

from __future__ import annotations

import re
import smtplib
import ssl
from collections.abc import Callable
from email.mime.text import MIMEText
from urllib.parse import parse_qs, unquote

import certifi
import httpx

from config import Account
from oauth_outlook import acquire_access_token, xoauth2_string

LINK_RE = re.compile(r'<([^>]+)>')
BARE_URL_RE = re.compile(r'(https?://[^\s,>]+)', re.I)
BARE_MAILTO_RE = re.compile(r'(mailto:[^\s,>]+)', re.I)


def parse_targets(header_value: str) -> list[tuple[str, str]]:
    """Return list of (kind, value) where kind in {'http','mailto'}."""
    if not header_value:
        return []

    targets: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(kind: str, value: str):
        value = value.strip().rstrip('>')
        key = f'{kind}:{value}'
        if value and key not in seen:
            seen.add(key)
            targets.append((kind, value))

    for match in LINK_RE.findall(header_value):
        match = match.strip()
        lower = match.lower()
        if lower.startswith('http'):
            add('http', match)
        elif lower.startswith('mailto:'):
            add('mailto', match[len('mailto:'):])

    # Some senders omit angle brackets
    for match in BARE_URL_RE.findall(header_value):
        add('http', match)
    for match in BARE_MAILTO_RE.findall(header_value):
        add('mailto', match[len('mailto:'):])

    return targets


def attempt_unsubscribe(
    account: Account,
    list_unsubscribe: str,
    list_unsubscribe_post: str | None = None,
) -> str:
    """
    Prefer RFC 8058 one-click POST when List-Unsubscribe-Post is present.
    Otherwise try each HTTP target (POST then GET), then mailto.
    """
    targets = parse_targets(list_unsubscribe)
    if not targets:
        return 'No List-Unsubscribe link found on messages from this sender.'

    http_targets = [t for t in targets if t[0] == 'http']
    mailto_targets = [t for t in targets if t[0] == 'mailto']
    one_click = bool(
        list_unsubscribe_post
        and 'list-unsubscribe=one-click' in list_unsubscribe_post.lower()
    )

    errors: list[str] = []

    if http_targets:
        with httpx.Client(
            timeout=httpx.Timeout(20.0, connect=10.0),
            follow_redirects=True,
            headers={
                'User-Agent': (
                    'Mozilla/5.0 (compatible; ishmail_v2/1.0; '
                    '+https://github.com/GorgonFreeman/ishmail_v2)'
                ),
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            },
            verify=certifi.where(),
        ) as client:
            for _kind, url in http_targets:
                # Always try one-click POST first when advertised, else still try POST
                # then GET — many ESPs accept either.
                attempts: list[tuple[str, Callable]] = []
                if one_click:
                    attempts.append((
                        'POST (one-click)',
                        lambda u=url: client.post(
                            u,
                            data={'List-Unsubscribe': 'One-Click'},
                            headers={'Content-Type': 'application/x-www-form-urlencoded'},
                        ),
                    ))
                else:
                    # Speculative one-click POST still helps when Post header was missing
                    attempts.append((
                        'POST (one-click speculative)',
                        lambda u=url: client.post(
                            u,
                            data={'List-Unsubscribe': 'One-Click'},
                            headers={'Content-Type': 'application/x-www-form-urlencoded'},
                        ),
                    ))
                attempts.append(('GET', lambda u=url: client.get(u)))

                for method_label, do_request in attempts:
                    try:
                        resp = do_request()
                        # Treat 2xx/3xx as success; some ESPs return 200 with a confirm page
                        if resp.status_code < 400:
                            return (
                                f'Requested unsubscribe via {method_label} {url} '
                                f'(HTTP {resp.status_code})'
                            )
                        errors.append(f'{method_label} {url} → HTTP {resp.status_code}')
                    except Exception as e:
                        errors.append(f'{method_label} {url} → {e}')

    if mailto_targets:
        result = _send_mailto(account, mailto_targets[0][1])
        if result.startswith('Sent'):
            return result
        errors.append(result)

    if errors:
        return 'Unsubscribe failed: ' + '; '.join(errors[:4])
    return 'Found a List-Unsubscribe header but could not complete unsubscribe.'


def _send_mailto(account: Account, mailto_addr: str) -> str:
    to_addr = mailto_addr
    subject = 'unsubscribe'
    body = 'Please unsubscribe me from this mailing list.'
    if '?' in mailto_addr:
        to_addr, query = mailto_addr.split('?', 1)
        params = parse_qs(query, keep_blank_values=True)
        if 'subject' in params:
            subject = unquote(params['subject'][0])
        if 'body' in params:
            body = unquote(params['body'][0])

    to_addr = to_addr.strip()
    if not to_addr or '@' not in to_addr:
        return f'Invalid mailto unsubscribe address: {mailto_addr}'

    if not account.smtp_host:
        return (
            f'Would email {to_addr} to unsubscribe, but no smtp_host is '
            f'configured for {account.label}.'
        )

    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = account.email
    msg['To'] = to_addr

    try:
        context = ssl.create_default_context(cafile=certifi.where())
        if account.smtp_starttls:
            server = smtplib.SMTP(account.smtp_host, account.smtp_port or 587, timeout=20)
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
        else:
            server = smtplib.SMTP_SSL(
                account.smtp_host,
                account.smtp_port or 465,
                timeout=20,
                context=context,
            )

        if account.uses_oauth:
            token = acquire_access_token(
                email=account.email,
                client_id=account.client_id or '',
                account_key=account.key,
                interactive=False,
            )
            auth_string = xoauth2_string(account.email, token)
            code, response = server.docmd('AUTH', 'XOAUTH2 ' + auth_string)
            if code != 235:
                server.quit()
                return f'OAuth SMTP auth failed for {account.email}: {code} {response}'
        else:
            server.login(account.email, account.password)

        server.sendmail(account.email, [to_addr], msg.as_string())
        server.quit()
        return f'Sent unsubscribe email to {to_addr}'
    except Exception as e:
        return f'Failed to send unsubscribe email to {to_addr}: {e}'
