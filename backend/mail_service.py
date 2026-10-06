"""Shared mail pool: connect accounts, fetch, mutate."""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Iterable

from client import AccountClient, ConnectError, MessageInfo
from config import Account, CredsConfig, load_creds
from unsubscribe import attempt_unsubscribe


class MailService:
    def __init__(self):
        self._creds: CredsConfig | None = None
        self._clients: dict[str, AccountClient] = {}
        self._lock = threading.Lock()
        self._cache: list[MessageInfo] | None = None
        self._cache_errors: list[str] = []

    def reload_creds(self) -> CredsConfig:
        self._creds = load_creds()
        return self._creds

    @property
    def creds(self) -> CredsConfig:
        if self._creds is None:
            self.reload_creds()
        return self._creds  # type: ignore

    def accounts(self) -> list[Account]:
        return self.creds.accounts

    def account_map(self) -> dict[str, Account]:
        return {a.key: a for a in self.accounts()}

    def get_client(self, account_key: str) -> AccountClient:
        with self._lock:
            if account_key in self._clients:
                return self._clients[account_key]
            acc = self.account_map().get(account_key)
            if not acc:
                raise KeyError(f'Unknown account {account_key}')
            client = AccountClient(acc)
            client.connect()
            self._clients[account_key] = client
            return client

    def invalidate_cache(self):
        self._cache = None

    def fetch_all(self, force: bool = False) -> tuple[list[MessageInfo], list[str]]:
        if self._cache is not None and not force:
            return self._cache, self._cache_errors

        messages: list[MessageInfo] = []
        errors: list[str] = []
        for acc in self.accounts():
            try:
                client = self.get_client(acc.key)
                inbox = client.list_messages(client.inbox_folder, archived=False)
                messages.extend(inbox)
                if client.archive_folder:
                    archived = client.list_messages(client.archive_folder, archived=True)
                    messages.extend(archived)
            except Exception as e:
                errors.append(f'{acc.key}: {e}')
                # Drop dead client so next attempt reconnects
                with self._lock:
                    dead = self._clients.pop(acc.key, None)
                    if dead:
                        try:
                            dead.close()
                        except Exception:
                            pass

        self._cache = messages
        self._cache_errors = errors
        return messages, errors

    def messages_for_refs(
        self,
        all_messages: list[MessageInfo],
        refs: list[dict],
        account_keys: list[str] | None = None,
    ) -> list[MessageInfo]:
        wanted = {
            (r['account_key'], r['folder'], int(r['uid']))
            for r in refs
        }
        allowed = set(account_keys) if account_keys is not None else None
        out = []
        for m in all_messages:
            if allowed is not None and m.account_key not in allowed:
                continue
            if (m.account_key, m.folder, m.uid) in wanted:
                out.append(m)
        return out

    def apply_action(
        self,
        action: str,
        messages: list[MessageInfo],
        progress_cb=None,
    ) -> dict:
        """
        action: delete | archive | star | unstar
        Groups by account+folder for efficient IMAP batches.
        """
        by_bucket: dict[tuple[str, str], list[MessageInfo]] = defaultdict(list)
        for m in messages:
            by_bucket[(m.account_key, m.folder)].append(m)

        results = []
        total = len(by_bucket)
        done = 0
        for (account_key, folder), bucket in by_bucket.items():
            uids = [m.uid for m in bucket]
            try:
                client = self.get_client(account_key)
                if action == 'delete':
                    client.trash_messages(folder, uids)
                elif action == 'archive':
                    client.archive_messages(folder, uids)
                elif action == 'star':
                    client.set_flagged(folder, uids, True)
                elif action == 'unstar':
                    client.set_flagged(folder, uids, False)
                else:
                    raise ValueError(f'Unknown action {action}')
                results.append({
                    'account_key': account_key,
                    'folder': folder,
                    'count': len(uids),
                    'ok': True,
                })
            except Exception as e:
                results.append({
                    'account_key': account_key,
                    'folder': folder,
                    'count': len(uids),
                    'ok': False,
                    'error': str(e),
                })
            done += 1
            if progress_cb:
                progress_cb({'done': done, 'total': total, 'results': results})

        self.invalidate_cache()
        return {
            'action': action,
            'affected': sum(r['count'] for r in results if r['ok']),
            'results': results,
        }

    def unsubscribe_sender(
        self,
        sender_email: str,
        account_keys: list[str] | None,
        progress_cb=None,
    ) -> dict:
        sender = sender_email.lower().strip()
        messages, _errors = self.fetch_all()
        by_account: dict[str, list[MessageInfo]] = defaultdict(list)
        for m in messages:
            if m.from_email.lower() != sender:
                continue
            if account_keys is not None and m.account_key not in account_keys:
                continue
            by_account[m.account_key].append(m)

        results = []
        keys = list(by_account.keys())
        for i, account_key in enumerate(keys):
            samples = by_account[account_key]
            # Prefer newest message that may carry List-Unsubscribe
            samples = sorted(samples, key=lambda m: m.date, reverse=True)[:5]
            header = None
            post = None
            try:
                client = self.get_client(account_key)
                for sample in samples:
                    headers = client.get_headers(
                        sample.folder,
                        sample.uid,
                        ['List-Unsubscribe', 'List-Unsubscribe-Post'],
                    )
                    if headers.get('list-unsubscribe'):
                        header = headers['list-unsubscribe']
                        post = headers.get('list-unsubscribe-post')
                        break
                if not header:
                    results.append({
                        'account_key': account_key,
                        'ok': False,
                        'detail': 'No List-Unsubscribe header on recent messages.',
                    })
                else:
                    account = self.account_map()[account_key]
                    detail = attempt_unsubscribe(account, header, post)
                    ok = detail.startswith('Requested') or detail.startswith('Sent')
                    results.append({
                        'account_key': account_key,
                        'ok': ok,
                        'detail': detail,
                    })
            except Exception as e:
                results.append({
                    'account_key': account_key,
                    'ok': False,
                    'detail': str(e),
                })
            if progress_cb:
                progress_cb({'done': i + 1, 'total': len(keys), 'results': results})

        return {'sender': sender, 'results': results}

    def delete_sender(
        self,
        sender_email: str,
        account_keys: list[str] | None,
        progress_cb=None,
    ) -> dict:
        sender = sender_email.lower().strip()
        messages, _ = self.fetch_all()
        targets = [
            m for m in messages
            if m.from_email.lower() == sender
            and (account_keys is None or m.account_key in account_keys)
        ]
        return self.apply_action('delete', targets, progress_cb=progress_cb)


mail_service = MailService()
