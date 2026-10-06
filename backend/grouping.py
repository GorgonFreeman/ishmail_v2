"""Subject normalisation and email grouping across inboxes."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass

from client import MessageInfo

WHITESPACE_RE = re.compile(r'\s+')
NON_ALNUM_RE = re.compile(r'[^a-z0-9]+')


def normalize_subject(subject: str, names: list[str]) -> str:
    """
    Strip personal names from a subject so near-dupes like
    "Your order, John" / "Your order, Martin" collapse together.
    """
    text = (subject or '').casefold()
    # longest names first so "john martin" beats "john"
    for name in sorted({n.strip().casefold() for n in names if n and n.strip()}, key=len, reverse=True):
        if not name:
            continue
        text = re.sub(re.escape(name), ' ', text, flags=re.IGNORECASE)
    text = WHITESPACE_RE.sub(' ', text).strip()
    # collapse punctuation noise for comparison
    compact = NON_ALNUM_RE.sub(' ', text)
    compact = WHITESPACE_RE.sub(' ', compact).strip()
    return compact or '(no subject)'


def group_key(subject: str, names: list[str]) -> str:
    norm = normalize_subject(subject, names)
    return hashlib.sha1(norm.encode()).hexdigest()[:16]


@dataclass
class EmailGroup:
    id: str
    subject: str
    subject_normalized: str
    from_email: str
    from_name: str
    date: str
    flagged: bool
    archived: bool
    duplicate_count: int
    messages: list[dict]
    account_keys: list[str]


def build_groups(
    messages: list[MessageInfo],
    names: list[str],
    *,
    archived_view: bool,
    query: str | None = None,
) -> list[EmailGroup]:
    """
    Group by normalised subject. A group appears in the archived view if any
    member is archived, and in the inbox view if any member is not — so mixed
    groups show in both.
    """
    by_key: dict[str, list[MessageInfo]] = defaultdict(list)
    for msg in messages:
        by_key[group_key(msg.subject, names)].append(msg)

    q = (query or '').strip().casefold()
    groups: list[EmailGroup] = []

    for gk, members in by_key.items():
        visible = [m for m in members if m.archived == archived_view]
        if not visible:
            continue

        # Prefer newest visible message for display fields
        visible_sorted = sorted(visible, key=lambda m: m.date, reverse=True)
        all_sorted = sorted(members, key=lambda m: m.date, reverse=True)
        primary = visible_sorted[0]

        if q:
            hay = ' '.join([
                primary.subject,
                primary.from_email,
                primary.from_name,
                primary.account_email,
            ]).casefold()
            if q not in hay:
                continue

        groups.append(
            EmailGroup(
                id=gk,
                subject=primary.subject,
                subject_normalized=normalize_subject(primary.subject, names),
                from_email=primary.from_email,
                from_name=primary.from_name,
                date=primary.date.isoformat(),
                flagged=any(m.flagged for m in visible),
                archived=archived_view,
                duplicate_count=len(members),
                messages=[
                    {
                        'account_key': m.account_key,
                        'account_email': m.account_email,
                        'folder': m.folder,
                        'uid': m.uid,
                        'date': m.date.isoformat(),
                        'from_email': m.from_email,
                        'from_name': m.from_name,
                        'subject': m.subject,
                        'flagged': m.flagged,
                        'archived': m.archived,
                    }
                    for m in all_sorted
                ],
                account_keys=sorted({m.account_key for m in members}),
            )
        )

    groups.sort(key=lambda g: g.date, reverse=True)
    return groups
