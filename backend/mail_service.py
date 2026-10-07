"""Shared mail pool: connect accounts, fetch, mutate."""

from __future__ import annotations

import threading
import time
from collections import defaultdict

from client import AccountClient, MessageInfo
from config import Account, CredsConfig, load_creds
from unsubscribe import attempt_unsubscribe


def _friendly_error(account: Account, exc: BaseException) -> str:
    msg = str(exc)
    low = msg.lower()
    if 'basic authentication is disabled' in low or (
        account.provider.lower() in ('outlook', 'hotmail') and 'login failed' in low
    ):
        return (
            f'{msg} — Outlook/Hotmail need OAuth (auth: oauth + client_id), '
            f'then: python auth_cli.py {account.key}'
        )
    return msg


class MailService:
    def __init__(self):
        self._creds: CredsConfig | None = None
        self._clients: dict[str, AccountClient] = {}
        # Serialize all IMAP use — IMAPClient is not thread-safe, and the job
        # queue can run alongside request handlers.
        self._imap_lock = threading.RLock()
        self._cache_lock = threading.RLock()
        self._cache: list[MessageInfo] | None = None
        self._cache_errors: list[str] = []
        self._fetching = False
        self._fetch_generation = 0

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

    def _drop_client_unlocked(self, account_key: str):
        dead = self._clients.pop(account_key, None)
        if dead:
            try:
                dead.close()
            except Exception:
                pass

    def _get_client_unlocked(self, account_key: str) -> AccountClient:
        client = self._clients.get(account_key)
        if client is not None:
            try:
                client.ensure_alive()
                return client
            except Exception:
                self._drop_client_unlocked(account_key)

        acc = self.account_map().get(account_key)
        if not acc:
            raise KeyError(f'Unknown account {account_key}')
        client = AccountClient(acc)
        client.connect()
        self._clients[account_key] = client
        return client

    def get_client(self, account_key: str) -> AccountClient:
        with self._imap_lock:
            return self._get_client_unlocked(account_key)

    def get_message_detail(self, account_key: str, folder: str, uid: int) -> dict:
        with self._imap_lock:
            client = self._get_client_unlocked(account_key)
            detail = client.get_message_detail(folder, uid)
            # Don't leave connections idle after a one-off read
            self._drop_client_unlocked(account_key)
            return detail

    def invalidate_cache(self):
        with self._cache_lock:
            self._cache = None

    def snapshot(self) -> tuple[list[MessageInfo], list[str], bool, bool, int]:
        """Non-blocking view of whatever mail has been fetched so far.

        Returns (messages, errors, fetching, has_cache, generation).
        """
        with self._cache_lock:
            has_cache = self._cache is not None
            messages = list(self._cache or [])
            errors = list(self._cache_errors)
            fetching = self._fetching
            generation = self._fetch_generation
        return messages, errors, fetching, has_cache, generation

    def _publish_cache(self, messages: list[MessageInfo], errors: list[str]):
        with self._cache_lock:
            self._cache = list(messages)
            self._cache_errors = list(errors)

    def fetch_all(
        self,
        force: bool = False,
        progress_cb=None,
    ) -> tuple[list[MessageInfo], list[str]]:
        accounts = self.accounts()
        total = len(accounts)

        with self._cache_lock:
            if self._cache is not None and not force and not self._fetching:
                if progress_cb:
                    progress_cb({
                        'done': total,
                        'total': total,
                        'current': None,
                        'account_key': None,
                        'cached': True,
                    })
                return list(self._cache), list(self._cache_errors)

        messages: list[MessageInfo] = []
        errors: list[str] = []
        with self._cache_lock:
            self._fetching = True
            self._fetch_generation += 1
            generation = self._fetch_generation
            # Stream into a fresh list so the UI fills as accounts complete.
            self._cache = []
            self._cache_errors = []

        if progress_cb:
            progress_cb({
                'done': 0,
                'total': total,
                'current': None,
                'account_key': None,
                'generation': generation,
            })

        try:
            for i, acc in enumerate(accounts):
                with self._cache_lock:
                    if generation != self._fetch_generation:
                        # A newer force-fetch superseded this run.
                        break
                if progress_cb:
                    progress_cb({
                        'done': i,
                        'total': total,
                        'current': acc.email,
                        'account_key': acc.key,
                        'generation': generation,
                    })
                try:
                    with self._imap_lock:
                        client = self._get_client_unlocked(acc.key)
                        inbox = client.list_messages(client.inbox_folder, archived=False)
                        messages.extend(inbox)
                        # Publish inbox immediately so the UI can stream groups
                        # before a huge Archive folder is scanned.
                        self._publish_cache(messages, errors)
                        if progress_cb:
                            progress_cb({
                                'done': i,
                                'total': total,
                                'current': acc.email,
                                'account_key': acc.key,
                                'phase': 'inbox',
                                'total_messages': len(messages),
                                'generation': generation,
                            })
                        if client.archive_folder:
                            archived = client.list_messages(
                                client.archive_folder, archived=True,
                            )
                            messages.extend(archived)
                            self._publish_cache(messages, errors)
                        # Close after each account so we don't hold ~15 idle IMAP
                        # sessions (providers drop them → NONAUTH on reuse).
                        self._drop_client_unlocked(acc.key)
                    if progress_cb:
                        progress_cb({
                            'done': i + 1,
                            'total': total,
                            'current': acc.email,
                            'account_key': acc.key,
                            'ok': True,
                            'phase': 'done',
                            'total_messages': len(messages),
                            'generation': generation,
                        })
                except Exception as e:
                    errors.append(f'{acc.key}: {_friendly_error(acc, e)}')
                    with self._imap_lock:
                        self._drop_client_unlocked(acc.key)
                    self._publish_cache(messages, errors)
                    if progress_cb:
                        progress_cb({
                            'done': i + 1,
                            'total': total,
                            'current': acc.email,
                            'account_key': acc.key,
                            'ok': False,
                            'error': str(e),
                            'total_messages': len(messages),
                            'generation': generation,
                        })
                # Brief pause between accounts to reduce provider rate-limits
                if i + 1 < total:
                    time.sleep(0.35)

            self._publish_cache(messages, errors)
            return messages, errors
        finally:
            with self._cache_lock:
                if generation == self._fetch_generation:
                    self._fetching = False

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
                with self._imap_lock:
                    client = self._get_client_unlocked(account_key)
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
                with self._imap_lock:
                    self._drop_client_unlocked(account_key)
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
                with self._imap_lock:
                    client = self._get_client_unlocked(account_key)
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
                with self._imap_lock:
                    self._drop_client_unlocked(account_key)
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
