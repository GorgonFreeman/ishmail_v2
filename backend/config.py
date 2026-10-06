from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from providers import ProviderConfigError, resolve_connection_info

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CREDS = ROOT / '.creds.yml'


@dataclass
class Account:
    provider: str
    name: str
    email: str
    password: str
    imap_host: str
    imap_port: int
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_starttls: bool = True
    auth: str = 'password'
    client_id: str | None = None

    @property
    def key(self) -> str:
        return f'{self.provider}/{self.name}'

    @property
    def label(self) -> str:
        return f'{self.email}  [{self.provider}/{self.name}]'

    @property
    def uses_oauth(self) -> bool:
        return self.auth == 'oauth'


@dataclass
class CredsConfig:
    accounts: list[Account]
    names: list[str] = field(default_factory=list)


class CredsError(Exception):
    pass


def load_creds(path: str | Path | None = None) -> CredsConfig:
    path = Path(path) if path else DEFAULT_CREDS
    if not path.exists():
        raise CredsError(f'Credentials file not found: {path}')

    with open(path, 'r') as f:
        data = yaml.safe_load(f) or {}

    names_raw = data.get('names') or []
    if not isinstance(names_raw, list):
        raise CredsError("'names' in .creds.yml must be a list of strings")
    names = [str(n).strip() for n in names_raw if str(n).strip()]

    accounts: list[Account] = []
    for provider, entries in data.items():
        if provider == 'names' or not isinstance(entries, dict):
            continue
        for name, cfg in entries.items():
            if not isinstance(cfg, dict) or 'email' not in cfg:
                continue

            email = cfg['email']
            # Prefer app_password; strip spaces (Gmail shows app passwords as
            # "xxxx xxxx xxxx xxxx" — IMAP accepts either, but spaced form has
            # caused flaky AUTH with some servers/clients).
            raw_password = cfg.get('app_password') or cfg.get('password') or ''
            password = str(raw_password).replace(' ', '').strip()
            client_id = cfg.get('client_id') or os.environ.get('ISHMAIL_OUTLOOK_CLIENT_ID')
            auth = (cfg.get('auth') or '').strip().lower()
            if not auth:
                if provider.lower() in ('outlook', 'hotmail') and client_id:
                    auth = 'oauth'
                else:
                    auth = 'password'

            if auth == 'oauth':
                if not client_id:
                    raise CredsError(
                        f'Account {provider}/{name} uses oauth but has no client_id'
                    )
            elif not password:
                raise CredsError(
                    f'Account {provider}/{name} has no password or app_password set.'
                )

            try:
                conn = resolve_connection_info(provider, cfg)
            except ProviderConfigError as e:
                raise CredsError(str(e)) from e

            accounts.append(
                Account(
                    provider=provider,
                    name=name,
                    email=email,
                    password=password,
                    imap_host=conn['imap_host'],
                    imap_port=conn.get('imap_port', 993),
                    smtp_host=conn.get('smtp_host'),
                    smtp_port=conn.get('smtp_port'),
                    smtp_starttls=conn.get('smtp_starttls', True),
                    auth=auth,
                    client_id=client_id,
                )
            )

    if not accounts:
        raise CredsError(f'No valid accounts found in {path}')

    return CredsConfig(accounts=accounts, names=names)
