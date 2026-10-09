"""Shared mail pool: connect accounts, fetch, mutate."""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, TypeVar

from client import AccountClient, MessageInfo, is_retriable_imap_error
from config import Account, CredsConfig, load_creds
from unsubscribe import attempt_unsubscribe

T = TypeVar('T')
ACCOUNT_ATTEMPTS = 3
# Default inbox window — full history is opt-in (slow on large Archives).
RECENT_DAYS = 183  # ~6 months
HISTORY_RECENT = 'recent'
HISTORY_FULL = 'full'


def recent_since_date() -> date:
    return date.today() - timedelta(days=RECENT_DAYS)


def normalize_history(history: str | None) -> str:
    if history == HISTORY_FULL:
        return HISTORY_FULL
    return HISTORY_RECENT


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def filter_messages_since(
    messages: list[MessageInfo],
    since: date,
) -> list[MessageInfo]:
    cutoff = datetime(since.year, since.month, since.day, tzinfo=timezone.utc)
    return [m for m in messages if _aware(m.date) >= cutoff]


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


@dataclass
class CachePool:
    messages: list[MessageInfo] | None = None
    errors: list[str] = field(default_factory=list)
    fetching: bool = False
    generation: int = 0


class MailService:
    def __init__(self):
        self._creds: CredsConfig | None = None
        self._clients: dict[str, AccountClient] = {}
        # Serialize all IMAP use — IMAPClient is not thread-safe, and the job
        # queue can run alongside request handlers.
        self._imap_lock = threading.RLock()
        self._cache_lock = threading.RLock()
        self._pools: dict[str, CachePool] = {
            HISTORY_RECENT: CachePool(),
            HISTORY_FULL: CachePool(),
        }

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

    def _fresh_account(self, account_key: str) -> Account:
        """Reload .creds.yml so password/token config changes are picked up."""
        try:
            self.reload_creds()
        except Exception:
            pass
        acc = self.account_map().get(account_key)
        if not acc:
            raise KeyError(f'Unknown account {account_key}')
        return acc

    def _get_client_unlocked(self, account_key: str, force_new: bool = False) -> AccountClient:
        if force_new:
            self._drop_client_unlocked(account_key)

        client = self._clients.get(account_key)
        if client is not None:
            # Keep Account object current (password/app_password edits).
            acc = self.account_map().get(account_key)
            if acc:
                client.account = acc
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

    def _with_account_retry(self, account_key: str, op: Callable[[AccountClient], T]) -> T:
        """Run an IMAP op; on flaky auth/socket errors, re-auth and retry."""
        last: BaseException | None = None
        for attempt in range(ACCOUNT_ATTEMPTS):
            try:
                with self._imap_lock:
                    if attempt > 0:
                        self._fresh_account(account_key)
                        client = self._get_client_unlocked(account_key, force_new=True)
                    else:
                        client = self._get_client_unlocked(account_key)
                    return op(client)
            except Exception as e:
                last = e
                with self._imap_lock:
                    self._drop_client_unlocked(account_key)
                if not is_retriable_imap_error(e) or attempt + 1 >= ACCOUNT_ATTEMPTS:
                    raise
                time.sleep(1.2 * (attempt + 1))
        if last:
            raise last
        raise RuntimeError('unreachable')

    def get_client(self, account_key: str) -> AccountClient:
        with self._imap_lock:
            return self._get_client_unlocked(account_key)

    def get_message_detail(self, account_key: str, folder: str, uid: int) -> dict:
        def _detail(client: AccountClient):
            detail = client.get_message_detail(folder, uid)
            # Don't leave connections idle after a one-off read
            self._drop_client_unlocked(account_key)
            return detail

        return self._with_account_retry(account_key, _detail)

    def invalidate_cache(self):
        with self._cache_lock:
            for pool in self._pools.values():
                pool.messages = None
                pool.errors = []

    def snapshot(
        self,
        history: str = HISTORY_RECENT,
    ) -> tuple[list[MessageInfo], list[str], bool, bool, int, str]:
        """Non-blocking view of one history pool.

        Returns (messages, errors, fetching, has_cache, generation, history).
        """
        key = normalize_history(history)
        with self._cache_lock:
            pool = self._pools[key]
            has_cache = pool.messages is not None
            messages = list(pool.messages or [])
            errors = list(pool.errors)
            fetching = pool.fetching
            generation = pool.generation
        return messages, errors, fetching, has_cache, generation, key

    def pool_flags(self) -> dict:
        """Status bits for both pools (for UI banner / readiness)."""
        with self._cache_lock:
            recent = self._pools[HISTORY_RECENT]
            full = self._pools[HISTORY_FULL]
            return {
                'recent_ready': recent.messages is not None,
                'recent_fetching': recent.fetching,
                'full_ready': full.messages is not None and not full.fetching,
                'full_fetching': full.fetching,
                'full_has_cache': full.messages is not None,
            }

    def all_cached_messages(self) -> list[MessageInfo]:
        """Messages from both pools, deduped by account/folder/uid."""
        with self._cache_lock:
            seen: set[tuple[str, str, int]] = set()
            out: list[MessageInfo] = []
            for key in (HISTORY_RECENT, HISTORY_FULL):
                for m in self._pools[key].messages or []:
                    ref = (m.account_key, m.folder, m.uid)
                    if ref in seen:
                        continue
                    seen.add(ref)
                    out.append(m)
            return out

    def _publish_pool(
        self,
        history: str,
        messages: list[MessageInfo],
        errors: list[str],
    ):
        key = normalize_history(history)
        with self._cache_lock:
            pool = self._pools[key]
            pool.messages = list(messages)
            pool.errors = list(errors)

    def fetch_all(
        self,
        force: bool = False,
        progress_cb=None,
        full_history: bool = False,
    ) -> tuple[list[MessageInfo], list[str]]:
        accounts = self.accounts()
        total = len(accounts)
        want_history = HISTORY_FULL if full_history else HISTORY_RECENT
        since = None if full_history else recent_since_date()

        with self._cache_lock:
            pool = self._pools[want_history]
            # An empty pool with account errors is not a usable cache — refetch
            # so a transient DNS/auth blip doesn't stick forever.
            usable_cache = (
                pool.messages is not None
                and not force
                and not pool.fetching
                and (len(pool.messages) > 0 or not pool.errors)
            )
            if usable_cache:
                cached = list(pool.messages or [])
                errors = list(pool.errors)
                if progress_cb:
                    progress_cb({
                        'done': total,
                        'total': total,
                        'current': None,
                        'account_key': None,
                        'cached': True,
                        'history': want_history,
                        'full_history': full_history,
                    })
                return cached, errors

        messages: list[MessageInfo] = []
        errors: list[str] = []
        with self._cache_lock:
            pool = self._pools[want_history]
            pool.fetching = True
            pool.generation += 1
            generation = pool.generation
            # Fresh list for this pool only — the other pool stays intact.
            pool.messages = []
            pool.errors = []

        if progress_cb:
            progress_cb({
                'done': 0,
                'total': total,
                'current': None,
                'account_key': None,
                'generation': generation,
                'history': want_history,
                'full_history': full_history,
                'since': since.isoformat() if since else None,
            })

        try:
            for i, acc in enumerate(accounts):
                with self._cache_lock:
                    if generation != self._pools[want_history].generation:
                        # A newer force-fetch for this pool superseded this run.
                        break
                if progress_cb:
                    progress_cb({
                        'done': i,
                        'total': total,
                        'current': acc.email,
                        'account_key': acc.key,
                        'generation': generation,
                        'history': want_history,
                        'full_history': full_history,
                    })
                try:
                    # Snapshot length so a mid-account retry can't duplicate msgs.
                    base_len = len(messages)

                    def _fetch_one(client: AccountClient):
                        del messages[base_len:]
                        inbox = client.list_messages(
                            client.inbox_folder,
                            archived=False,
                            since=since,
                        )
                        messages.extend(inbox)
                        # Publish inbox immediately so the UI can show groups
                        # before a huge Archive folder is scanned.
                        self._publish_pool(want_history, messages, errors)
                        if progress_cb:
                            progress_cb({
                                'done': i,
                                'total': total,
                                'current': acc.email,
                                'account_key': acc.key,
                                'phase': 'inbox',
                                'total_messages': len(messages),
                                'generation': generation,
                                'history': want_history,
                                'full_history': full_history,
                            })
                        if client.archive_folder:
                            archived = client.list_messages(
                                client.archive_folder,
                                archived=True,
                                since=since,
                            )
                            messages.extend(archived)
                            self._publish_pool(want_history, messages, errors)
                        # Close after each account so we don't hold ~15 idle IMAP
                        # sessions (providers drop them → NONAUTH on reuse).
                        self._drop_client_unlocked(acc.key)

                    self._with_account_retry(acc.key, _fetch_one)
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
                            'history': want_history,
                            'full_history': full_history,
                        })
                except Exception as e:
                    errors.append(f'{acc.key}: {_friendly_error(acc, e)}')
                    with self._imap_lock:
                        self._drop_client_unlocked(acc.key)
                    self._publish_pool(want_history, messages, errors)
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
                            'history': want_history,
                            'full_history': full_history,
                        })
                # Brief pause between accounts to reduce provider rate-limits
                if i + 1 < total:
                    time.sleep(0.35)

            self._publish_pool(want_history, messages, errors)
            return messages, errors
        finally:
            with self._cache_lock:
                pool = self._pools[want_history]
                if generation == pool.generation:
                    pool.fetching = False

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
                def _act(client: AccountClient, _folder=folder, _uids=uids):
                    if action == 'delete':
                        client.trash_messages(_folder, _uids)
                    elif action == 'archive':
                        client.archive_messages(_folder, _uids)
                    elif action == 'star':
                        client.set_flagged(_folder, _uids, True)
                    elif action == 'unstar':
                        client.set_flagged(_folder, _uids, False)
                    else:
                        raise ValueError(f'Unknown action {action}')

                self._with_account_retry(account_key, _act)
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
                def _headers(client: AccountClient):
                    nonlocal header, post
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

                self._with_account_retry(account_key, _headers)
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
