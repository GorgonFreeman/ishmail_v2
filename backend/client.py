from __future__ import annotations

import socket
import ssl
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, TypeVar

import certifi
from imapclient import IMAPClient
from imapclient.exceptions import IMAPClientAbortError, IMAPClientError

from config import Account

T = TypeVar('T')
IMAP_TIMEOUT_SECONDS = 60

ARCHIVE_FALLBACK_NAMES = ['Archive', 'Archived', 'All Mail']
TRASH_FALLBACK_NAMES = ['Trash', 'Deleted Items', 'Deleted', 'Bin']


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
    if isinstance(exc, IMAPClientError):
        msg = str(exc).lower()
        needles = (
            'socket', 'connection', 'timed out', 'timeout', 'broken pipe',
            'reset by peer', 'eof', 'not connected', 'server closed', 'bye', 'gone',
        )
        return any(n in msg for n in needles)
    cause = getattr(exc, '__cause__', None) or getattr(exc, '__context__', None)
    if cause is not None and cause is not exc:
        return _is_connection_error(cause)
    return False


class AccountClient:
    def __init__(self, account: Account):
        self.account = account
        self.conn: IMAPClient | None = None
        self.archive_folder: str | None = None
        self.trash_folder: str | None = None
        self.inbox_folder = 'INBOX'

    def connect(self):
        self._open()
        self._discover_folders()

    def _open(self):
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
            raise ConnectError(f'{self.account.label}: {e}') from e

    def _reconnect(self):
        if self.conn:
            try:
                self.conn.logout()
            except Exception:
                pass
            try:
                self.conn.shutdown()
            except Exception:
                pass
            self.conn = None
        self._open()

    def _call(self, fn: Callable[[], T]) -> T:
        try:
            return fn()
        except Exception as e:
            if not _is_connection_error(e):
                raise
            self._reconnect()
            return fn()

    def _discover_folders(self):
        try:
            folders = self.conn.list_folders()
        except IMAPClientError:
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
        if self.conn:
            try:
                self.conn.logout()
            except Exception:
                pass
            try:
                self.conn.shutdown()
            except Exception:
                pass
            self.conn = None

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
