"""Microsoft Outlook/Hotmail OAuth2 (device-code) for IMAP/SMTP XOAUTH2."""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

import msal

OUTLOOK_SCOPES = [
    'https://outlook.office.com/IMAP.AccessAsUser.All',
    'https://outlook.office.com/SMTP.Send',
]

DEFAULT_AUTHORITY = 'https://login.microsoftonline.com/consumers'
TOKEN_CACHE_DIR = Path(__file__).resolve().parent.parent / '.outlook_tokens'


class OutlookOauthError(Exception):
    pass


def token_cache_path(account_key: str) -> Path:
    safe = account_key.replace('/', '_').replace('\\', '_')
    return TOKEN_CACHE_DIR / f'{safe}.json'


def _load_cache(path: Path) -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    if path.exists():
        cache.deserialize(path.read_text())
    return cache


def _save_cache(cache: msal.SerializableTokenCache, path: Path):
    if not cache.has_state_changed:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cache.serialize())


def _public_app(client_id: str, cache: msal.SerializableTokenCache) -> msal.PublicClientApplication:
    return msal.PublicClientApplication(
        client_id=client_id,
        authority=DEFAULT_AUTHORITY,
        token_cache=cache,
    )


def acquire_access_token(
    email: str,
    client_id: str,
    account_key: str,
    interactive: bool = False,
    force_refresh: bool = False,
) -> str:
    if not client_id:
        raise OutlookOauthError(
            f'{account_key}: Outlook OAuth needs client_id in .creds.yml'
        )

    path = token_cache_path(account_key)
    cache = _load_cache(path)
    app = _public_app(client_id, cache)

    result = None
    accounts = app.get_accounts(username=email) or app.get_accounts()
    if accounts:
        account = next(
            (a for a in accounts if (a.get('username') or '').lower() == email.lower()),
            accounts[0],
        )
        result = app.acquire_token_silent(
            OUTLOOK_SCOPES,
            account=account,
            force_refresh=force_refresh,
        )

    if result and 'access_token' in result:
        _save_cache(cache, path)
        return result['access_token']

    can_prompt = interactive or (sys.stdin.isatty() and sys.stdout.isatty())
    if not can_prompt:
        raise OutlookOauthError(
            f'{account_key}: no cached Outlook token. Run:\n'
            f'  python3 -m backend.auth_cli {account_key}'
        )

    flow = app.initiate_device_flow(scopes=OUTLOOK_SCOPES)
    if 'user_code' not in flow:
        raise OutlookOauthError(
            f'{account_key}: device flow failed: '
            f'{flow.get("error_description") or flow.get("error") or flow}'
        )

    print(flow['message'], flush=True)
    print(f'Open: {flow.get("verification_uri", "https://microsoft.com/devicelogin")}', flush=True)
    print(f'Code: {flow["user_code"]}', flush=True)
    print(f'Sign in as: {email}', flush=True)

    result = app.acquire_token_by_device_flow(flow)
    if not result or 'access_token' not in result:
        raise OutlookOauthError(
            f'{account_key}: token acquisition failed: '
            f'{result.get("error_description") if result else "no result"}'
        )

    _save_cache(cache, path)
    return result['access_token']


def xoauth2_string(email: str, access_token: str) -> str:
    """SASL XOAUTH2 initial client response (for SMTP AUTH)."""
    raw = f'user={email}\x01auth=Bearer {access_token}\x01\x01'
    return base64.b64encode(raw.encode()).decode()
