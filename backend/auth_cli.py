#!/usr/bin/env python3
"""Interactive Outlook OAuth for one account: python3 auth_cli.py outlook/name"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import load_creds


def main():
    if len(sys.argv) != 2:
        print('Usage: python3 auth_cli.py provider/account_name')
        sys.exit(1)
    key = sys.argv[1]
    creds = load_creds()
    account = next((a for a in creds.accounts if a.key == key), None)
    if not account:
        print(f'Unknown account {key}')
        sys.exit(1)
    if not account.uses_oauth:
        print(f'{key} is not an oauth account')
        sys.exit(1)
    from oauth_outlook import acquire_access_token

    acquire_access_token(
        email=account.email,
        client_id=account.client_id or '',
        account_key=account.key,
        interactive=True,
    )
    print(f'OK: {key}')


if __name__ == '__main__':
    main()
