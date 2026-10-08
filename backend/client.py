from __future__ import annotations

import socket
import ssl
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, TypeVar

import certifi
from imapclient import IMAPClient
from imapclient.exceptions import IMAPClientAbortError, IMAPClientError

from config import Account

T = TypeVar('T')
IMAP_TIMEOUT_SECONDS = 60
# Providers often flap AUTH / drop idle sockets; retry before surfacing.
CONNECT_ATTEMPTS = 3
CALL_ATTEMPTS = 3

ARCHIVE_FALLBACK_NAMES = ['Archive', 'Archived', 'All Mail']
TRASH_FALLBACK_NAMES = ['Trash', 'Deleted Items', 'Deleted', 'Bin']

_CONNECTION_NEEDLES = (
    'socket', 'connection', 'timed out', 'timeout', 'broken pipe',
    'reset by peer', 'eof', 'not connected', 'server closed', 'bye',
    'gone', 'nonauth', 'illegal in state', 'logged out', 'unavailable',
    'temporary', 'try again', 'rate', 'throttle', 'too many',
)

# Many "invalid credentials" responses are flaky (rate-limits, stale tokens,
# half-closed sockets). Treat as retriable unless clearly permanent.
_AUTH_RETRY_NEEDLES = (
    'authenticationfailed',
    'authentication failed',
    'invalid credentials',
    'invalid login',
    'invalid user',
    'login failed',
    'auth failed',
    'authenticate failed',
    'authentication error',
    'wrong password',
    'bad password',
    'incorrect password',
    '[auth]',
    'oauthbearer',
    'xoauth',
    'unauthorized',
    'not authenticated',
)

_PERMANENT_AUTH_NEEDLES = (
    'basic authentication is disabled',
    'no cached outlook token',
    'needs client_id',
    'device flow failed',
    'token acquisition failed',
    'unknown account',
)


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


@dataclass
class MessageInfo:
    account_key: str
    account_email: str
    folder: str
    uid: int
    date: datetime
    from_email: str
    from_name: str
    subject: str
    flagged: bool
    archived: bool

    @property
    def sender_display(self) -> str:
        return self.from_name or self.from_email

    def ref(self) -> dict:
        return {
            'account_key': self.account_key,
            'folder': self.folder,
            'uid': self.uid,
        }


class ConnectError(Exception):
    pass


def _exc_text(exc: BaseException) -> str:
    parts = [str(exc)]
    cause = getattr(exc, '__cause__', None) or getattr(exc, '__context__', None)
    if cause is not None and cause is not exc:
        parts.append(_exc_text(cause))
    return ' '.join(parts).lower()


def _is_permanent_auth_error(exc: BaseException) -> bool:
    msg = _exc_text(exc)
    return any(n in msg for n in _PERMANENT_AUTH_NEEDLES)


def _is_connection_error(exc: BaseException) -> bool:
    if isinstance(
        exc,
        (
            socket.timeout,
            socket.error,
            OSError,
            BrokenPipeError,
            ConnectionResetError,
            ConnectionAbortedError,
            ConnectionRefusedError,
            ssl.SSLError,
            IMAPClientAbortError,
        ),
    ):
        return True
    if isinstance(exc, (IMAPClientError, ConnectError)):
        msg = _exc_text(exc)
        if any(n in msg for n in _CONNECTION_NEEDLES):
            return True
    cause = getattr(exc, '__cause__', None) or getattr(exc, '__context__', None)
    if cause is not None and cause is not exc:
        return _is_connection_error(cause)
    return False


def _is_retriable_auth_error(exc: BaseException) -> bool:
    if _is_permanent_auth_error(exc):
        return False
    msg = _exc_text(exc)
    return any(n in msg for n in _AUTH_RETRY_NEEDLES)


def is_retriable_imap_error(exc: BaseException) -> bool:
    """True for flaky socket/auth failures worth a fresh login."""
    if _is_permanent_auth_error(exc):
        return False
    return _is_connection_error(exc) or _is_retriable_auth_error(exc)


def _backoff_seconds(attempt_index: int) -> float:
    # 0.8s, 1.6s, 3.2s…
    return 0.8 * (2 ** attempt_index)


class AccountClient:
    def __init__(self, account: Account):
        self.account = account
        self.conn: IMAPClient | None = None
        self.archive_folder: str | None = None
        self.trash_folder: str | None = None
        self.inbox_folder = 'INBOX'

    def connect(self):
        """Open IMAP with retries; force OAuth refresh after the first failure."""
        last: BaseException | None = None
        for attempt in range(CONNECT_ATTEMPTS):
            try:
                self._open(force_token_refresh=attempt > 0 and self.account.uses_oauth)
                self._discover_folders()
                return
            except Exception as e:
                last = e
                self._hard_close()
                if not is_retriable_imap_error(e) or attempt + 1 >= CONNECT_ATTEMPTS:
                    raise
                time.sleep(_backoff_seconds(attempt))
        if last:
            raise last

    def ensure_alive(self):
        """NOOP (or reconnect). Call before reusing a long-lived client."""
        if self.conn is None:
            self.connect()
            return
        try:
            self.conn.noop()
        except Exception:
            self._reconnect(force_token_refresh=self.account.uses_oauth)
            if self.archive_folder is None and self.trash_folder is None:
                self._discover_folders()

    def _open(self, force_token_refresh: bool = False):
        try:
            self.conn = IMAPClient(
                self.account.imap_host,
                port=self.account.imap_port,
                ssl=True,
                ssl_context=_ssl_context(),
                timeout=IMAP_TIMEOUT_SECONDS,
            )
            if self.account.uses_oauth:
                from oauth_outlook import OutlookOauthError, acquire_access_token

                try:
                    token = acquire_access_token(
                        email=self.account.email,
                        client_id=self.account.client_id or '',
                        account_key=self.account.key,
                        interactive=False,
                        force_refresh=force_token_refresh,
                    )
                except OutlookOauthError as e:
                    raise ConnectError(str(e)) from e
                self.conn.oauth2_login(self.account.email, token)
            else:
                self.conn.login(self.account.email, self.account.password)
        except ConnectError:
            self.conn = None
            raise
        except Exception as e:
            self.conn = None
            msg = str(e)
            if 'basic authentication is disabled' in msg.lower():
                msg += (
                    ' — set auth: oauth and client_id for this Outlook account, '
                    f'then run: python auth_cli.py {self.account.key}'
                )
            raise ConnectError(f'{self.account.label}: {msg}') from e

    def _hard_close(self):
        if not self.conn:
            return
        try:
            self.conn.logout()
        except Exception:
            pass
        try:
            self.conn.shutdown()
        except Exception:
            pass
        self.conn = None

    def _reconnect(self, force_token_refresh: bool = False):
        self._hard_close()
        self._open(force_token_refresh=force_token_refresh)

    def _call(self, fn: Callable[[], T]) -> T:
        last: BaseException | None = None
        for attempt in range(CALL_ATTEMPTS):
            try:
                return fn()
            except Exception as e:
                last = e
                if not is_retriable_imap_error(e) or attempt + 1 >= CALL_ATTEMPTS:
                    raise
                try:
                    self._reconnect(
                        force_token_refresh=(
                            self.account.uses_oauth and _is_retriable_auth_error(e)
                        ),
                    )
                except Exception as re_e:
                    last = re_e
                    if not is_retriable_imap_error(re_e) or attempt + 1 >= CALL_ATTEMPTS:
                        raise
                time.sleep(_backoff_seconds(attempt))
        if last:
            raise last
        raise RuntimeError('unreachable')

    def _discover_folders(self):
        try:
            folders = self.conn.list_folders()
        except IMAPClientError as e:
            # Auth/connection failures must not be swallowed — otherwise later
            # EXAMINE fails with a cryptic NONAUTH error.
            msg = str(e).lower()
            if any(n in msg for n in ('nonauth', 'auth', 'login', 'illegal in state')):
                raise ConnectError(f'{self.account.label}: {e}') from e
            folders = []

        names = []
        for flags, _delim, name in folders:
            names.append(name)
            flagset = {f.decode() if isinstance(f, bytes) else f for f in flags}
            if r'\Archive' in flagset:
                self.archive_folder = name
            if r'\Trash' in flagset:
                self.trash_folder = name

        if not self.archive_folder:
            for candidate in ARCHIVE_FALLBACK_NAMES:
                if candidate in names:
                    self.archive_folder = candidate
                    break
        if not self.trash_folder:
            for candidate in TRASH_FALLBACK_NAMES:
                if candidate in names:
                    self.trash_folder = candidate
                    break

        if not self.archive_folder:
            try:
                self.conn.create_folder('Archive')
                self.archive_folder = 'Archive'
            except IMAPClientError:
                pass

    def close(self):
        self._hard_close()

    def list_messages(self, folder: str, criteria='ALL', archived: bool = False) -> list[MessageInfo]:
        def op():
            self.conn.select_folder(folder, readonly=True)
            uids = self.conn.search(criteria)
            if not uids:
                return []

            result = []
            # chunk large fetches
            chunk_size = 200
            for i in range(0, len(uids), chunk_size):
                chunk = uids[i:i + chunk_size]
                fetched = self.conn.fetch(chunk, ['ENVELOPE', 'FLAGS', 'INTERNALDATE'])
                for uid, data in fetched.items():
                    env = data.get(b'ENVELOPE')
                    flags = data.get(b'FLAGS', ())
                    idate = data.get(b'INTERNALDATE')

                    from_email, from_name = 'unknown@unknown', ''
                    if env and env.from_:
                        f = env.from_[0]
                        mailbox = f.mailbox.decode() if f.mailbox else 'unknown'
                        host = f.host.decode() if f.host else 'unknown'
                        from_email = f'{mailbox}@{host}'
                        from_name = f.name.decode(errors='replace') if f.name else ''

                    subject = ''
                    if env and env.subject:
                        subject = _decode_maybe(env.subject)

                    result.append(
                        MessageInfo(
                            account_key=self.account.key,
                            account_email=self.account.email,
                            folder=folder,
                            uid=uid,
                            date=idate or datetime.min,
                            from_email=from_email.lower(),
                            from_name=from_name,
                            subject=subject or '(no subject)',
                            flagged=b'\\Flagged' in flags,
                            archived=archived,
                        )
                    )
            return result

        return self._call(op)

    def get_headers(self, folder: str, uid: int, header_names: list[str]) -> dict[str, str]:
        """Return a map of lowercased header name -> value for the given message."""
        names = ' '.join(header_names)

        def op():
            self.conn.select_folder(folder, readonly=True)
            data = self.conn.fetch([uid], [f'BODY.PEEK[HEADER.FIELDS ({names})]'])
            if uid not in data:
                return {}
            raw = None
            for k, v in data[uid].items():
                key = k.decode() if isinstance(k, bytes) else str(k)
                if 'HEADER.FIELDS' in key.upper():
                    raw = v
                    break
            if not raw:
                return {}
            text = raw.decode(errors='replace')
            out: dict[str, str] = {}
            current = None
            for line in text.splitlines():
                if not line.strip():
                    continue
                if line[0] in ' \t' and current:
                    out[current] = out[current] + ' ' + line.strip()
                    continue
                if ':' in line:
                    name, val = line.split(':', 1)
                    current = name.strip().lower()
                    out[current] = val.strip()
            return out

        return self._call(op)

    def get_message_detail(self, folder: str, uid: int) -> dict:
        """Fetch envelope fields + a plain-text body preview for one message."""
        def op():
            self.conn.select_folder(folder, readonly=True)
            data = self.conn.fetch([uid], ['ENVELOPE', 'FLAGS', 'INTERNALDATE', 'RFC822'])
            if uid not in data:
                raise RuntimeError(f'Message {uid} not found in {folder}')
            item = data[uid]
            env = item.get(b'ENVELOPE')
            flags = item.get(b'FLAGS', ())
            idate = item.get(b'INTERNALDATE')
            raw = item.get(b'RFC822') or b''

            from_email, from_name = 'unknown@unknown', ''
            if env and env.from_:
                f = env.from_[0]
                mailbox = f.mailbox.decode() if f.mailbox else 'unknown'
                host = f.host.decode() if f.host else 'unknown'
                from_email = f'{mailbox}@{host}'
                from_name = f.name.decode(errors='replace') if f.name else ''

            subject = ''
            if env and env.subject:
                subject = _decode_maybe(env.subject)

            body_text, body_html = _extract_bodies(raw)

            return {
                'account_key': self.account.key,
                'account_email': self.account.email,
                'folder': folder,
                'uid': uid,
                'date': (idate or datetime.min).isoformat(),
                'from_email': from_email.lower(),
                'from_name': from_name,
                'subject': subject or '(no subject)',
                'flagged': b'\\Flagged' in flags,
                'body_text': body_text,
                'body_html': body_html,
            }

        return self._call(op)

    def set_flagged(self, folder: str, uids: list[int], flagged: bool):
        def op():
            self.conn.select_folder(folder)
            if flagged:
                self.conn.add_flags(uids, [b'\\Flagged'])
            else:
                self.conn.remove_flags(uids, [b'\\Flagged'])

        self._call(op)

    def move_messages(self, folder: str, uids: list[int], dest_folder: str):
        def op():
            self.conn.select_folder(folder)
            try:
                self.conn.move(uids, dest_folder)
            except IMAPClientError:
                self.conn.copy(uids, dest_folder)
                self.conn.delete_messages(uids)
                self.conn.expunge()

        self._call(op)

    def archive_messages(self, folder: str, uids: list[int]):
        if not self.archive_folder:
            raise RuntimeError(f'{self.account.label}: no Archive folder available')
        self.move_messages(folder, uids, self.archive_folder)

    def trash_messages(self, folder: str, uids: list[int]):
        if not self.trash_folder:
            raise RuntimeError(f'{self.account.label}: no Trash folder available')
        self.move_messages(folder, uids, self.trash_folder)


def _decode_maybe(raw_bytes: bytes) -> str:
    try:
        from email.header import decode_header

        decoded = decode_header(raw_bytes.decode(errors='replace'))
        out = ''
        for text, enc in decoded:
            if isinstance(text, bytes):
                out += text.decode(enc or 'utf-8', errors='replace')
            else:
                out += text
        return out
    except Exception:
        return raw_bytes.decode(errors='replace')


def _decode_part_payload(part) -> str:
    raw = part.get_payload(decode=True)
    if raw is None:
        payload = part.get_payload()
        return payload if isinstance(payload, str) else ''
    charset = part.get_content_charset() or 'utf-8'
    try:
        return raw.decode(charset, errors='replace')
    except LookupError:
        return raw.decode('utf-8', errors='replace')


def _extract_bodies(raw: bytes) -> tuple[str, str | None]:
    """Return (plain_text, html_or_none) from an RFC822 blob."""
    from email import message_from_bytes

    if not raw:
        return ('', None)

    msg = message_from_bytes(raw)
    text_parts: list[str] = []
    html_parts: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == 'multipart':
                continue
            disp = (part.get('Content-Disposition') or '').lower()
            if 'attachment' in disp:
                continue
            ctype = part.get_content_type()
            if ctype == 'text/plain':
                text_parts.append(_decode_part_payload(part))
            elif ctype == 'text/html':
                html_parts.append(_decode_part_payload(part))
    else:
        ctype = msg.get_content_type()
        content = _decode_part_payload(msg)
        if ctype == 'text/html':
            html_parts.append(content)
        else:
            text_parts.append(content)

    text = '\n\n'.join(p.strip() for p in text_parts if p and p.strip())
    html = '\n'.join(p for p in html_parts if p and p.strip()) or None
    if not text and html:
        # crude fallback so the UI always has something readable
        import re
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'\s+', ' ', text).strip()
    return (text, html)
