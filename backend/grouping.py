"""Subject normalisation and email grouping across inboxes.

When a known name from creds appears in a subject, that subject becomes a
regex template with a name-slot wildcard. Other subjects that match the
template (even with names not listed in creds) join the same group.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass

from client import MessageInfo

WHITESPACE_RE = re.compile(r'\s+')
NON_ALNUM_RE = re.compile(r'[^a-z0-9]+')
NAME_SLOT = '{{NAME}}'
# 1–3 capitalisable word tokens (covers "Sam", "Mary-Jane", "John Martin")
NAME_WILDCARD = (
    r"[A-Za-z][A-Za-z'\u2019\-]*(?:\s+[A-Za-z][A-Za-z'\u2019\-]*){0,2}"
)


def _prepared_names(names: list[str]) -> list[str]:
    cleaned = {n.strip() for n in names if n and n.strip()}
    return sorted(cleaned, key=len, reverse=True)


def _is_word_boundary(text: str, start: int, end: int) -> bool:
    if start > 0 and text[start - 1].isalpha():
        return False
    if end < len(text) and text[end].isalpha():
        return False
    return True


def find_name_spans(subject: str, prepared_names: list[str]) -> list[tuple[int, int]]:
    """Non-overlapping spans of known names in subject (longest match wins)."""
    if not subject or not prepared_names:
        return []
    lower = subject.casefold()
    occupied = [False] * len(subject)
    spans: list[tuple[int, int]] = []

    for name in prepared_names:
        needle = name.casefold()
        start = 0
        while True:
            idx = lower.find(needle, start)
            if idx < 0:
                break
            end = idx + len(needle)
            if not _is_word_boundary(lower, idx, end) or any(occupied[idx:end]):
                start = idx + 1
                continue
            for i in range(idx, end):
                occupied[i] = True
            spans.append((idx, end))
            start = end

    spans.sort()
    return spans


def subject_template(subject: str, names: list[str]) -> str | None:
    """
    Replace known-name spans with NAME_SLOT. Returns None if no known name
    appears — only name-bearing subjects seed regex templates.
    """
    return _subject_template_prepared(subject, _prepared_names(names))


def _subject_template_prepared(subject: str, prepared: list[str]) -> str | None:
    spans = find_name_spans(subject, prepared)
    if not spans:
        return None
    parts: list[str] = []
    cursor = 0
    for start, end in spans:
        parts.append(subject[cursor:start])
        parts.append(NAME_SLOT)
        cursor = end
    parts.append(subject[cursor:])
    return ''.join(parts)


def canonicalize_template(template: str) -> str:
    # Protect slots, then casefold + collapse punctuation/whitespace.
    protected = template.replace(NAME_SLOT, '\x00')
    text = protected.casefold()
    text = NON_ALNUM_RE.sub(' ', text)
    text = WHITESPACE_RE.sub(' ', text).strip()
    text = text.replace('\x00', f' {NAME_SLOT} ')
    text = WHITESPACE_RE.sub(' ', text).strip()
    return text or NAME_SLOT


def _escape_literal(part: str) -> str:
    """Escape a literal subject fragment; treat whitespace runs as \\s+."""
    pieces = WHITESPACE_RE.split(part)
    return r'\s+'.join(re.escape(p) for p in pieces)


def template_to_regex(template: str) -> re.Pattern[str]:
    parts = template.split(NAME_SLOT)
    body = NAME_WILDCARD.join(_escape_literal(p) for p in parts)
    return re.compile(rf'^{body}$', re.IGNORECASE)


def normalize_subject(subject: str, names: list[str]) -> str:
    """
    Strip known names (and collapse noise) for exact-ish equality when no
    template applies. Prefer subject_template + regex for cross-name matches.
    """
    return _normalize_subject_prepared(subject, _prepared_names(names))


def _normalize_subject_prepared(subject: str, prepared: list[str]) -> str:
    text = (subject or '').casefold()
    for name in prepared:
        needle = name.casefold()
        # Fast path: plain find with word-boundary checks (avoids re.sub per name).
        out = []
        i = 0
        while True:
            idx = text.find(needle, i)
            if idx < 0:
                out.append(text[i:])
                break
            end = idx + len(needle)
            if _is_word_boundary(text, idx, end):
                out.append(text[i:idx])
                out.append(' ')
                i = end
            else:
                out.append(text[i:idx + 1])
                i = idx + 1
        text = ''.join(out)
    text = WHITESPACE_RE.sub(' ', text).strip()
    compact = NON_ALNUM_RE.sub(' ', text)
    compact = WHITESPACE_RE.sub(' ', compact).strip()
    return compact or '(no subject)'


def group_key_from_template(template: str) -> str:
    canon = canonicalize_template(template)
    return 't:' + hashlib.sha1(canon.encode()).hexdigest()[:16]


def group_key_from_norm(norm: str) -> str:
    return 'n:' + hashlib.sha1(norm.encode()).hexdigest()[:16]


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


def assign_group_ids(subjects: list[str], names: list[str]) -> list[str]:
    """
    Cluster subjects by unique title then template/normalised key.

    Work is O(unique_subjects × templates), not O(messages²) — important when
    streaming large Yahoo/Gmail mailboxes into the UI.
    """
    n = len(subjects)
    if n == 0:
        return []

    prepared = _prepared_names(names)
    by_subject: dict[str, list[int]] = defaultdict(list)
    for i, subject in enumerate(subjects):
        by_subject[subject or ''].append(i)

    unique_subjects = list(by_subject.keys())

    # Seed: name-bearing titles get a template key immediately.
    templates: dict[str, re.Pattern[str]] = {}
    subject_key: dict[str, str] = {}
    for subject in unique_subjects:
        tmpl = _subject_template_prepared(subject, prepared)
        if tmpl is None:
            continue
        key = group_key_from_template(tmpl)
        templates.setdefault(key, template_to_regex(tmpl))
        subject_key[subject] = key

    # Only try regex templates against titles that didn't already seed a key
    # (covers unknown names like "Priya" in an otherwise identical title).
    unkeyed = [s for s in unique_subjects if s not in subject_key]
    if unkeyed and templates:
        for key, rx in templates.items():
            still = []
            for subject in unkeyed:
                if rx.match(subject):
                    subject_key[subject] = key
                else:
                    still.append(subject)
            unkeyed = still
            if not unkeyed:
                break

    for subject in unkeyed:
        subject_key[subject] = group_key_from_norm(
            _normalize_subject_prepared(subject, prepared),
        )

    return [subject_key[subject or ''] for subject in subjects]


def build_groups(
    messages: list[MessageInfo],
    names: list[str],
    *,
    archived_view: bool,
    query: str | None = None,
) -> list[EmailGroup]:
    """
    Group by subject template / normalised subject. A group appears in the
    archived view if any member is archived, and in the inbox view if any
    member is not — so mixed groups show in both.
    """
    prepared = _prepared_names(names)
    group_ids = assign_group_ids([m.subject for m in messages], prepared)
    by_key: dict[str, list[MessageInfo]] = defaultdict(list)
    for msg, gk in zip(messages, group_ids):
        by_key[gk].append(msg)

    q = (query or '').strip().casefold()
    groups: list[EmailGroup] = []

    for gk, members in by_key.items():
        visible = [m for m in members if m.archived == archived_view]
        if not visible:
            continue

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

        tmpl = _subject_template_prepared(primary.subject, prepared)
        normalized = (
            canonicalize_template(tmpl)
            if tmpl
            else _normalize_subject_prepared(primary.subject, prepared)
        )

        groups.append(
            EmailGroup(
                id=gk,
                subject=primary.subject,
                subject_normalized=normalized,
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
