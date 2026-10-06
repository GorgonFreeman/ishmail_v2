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


def find_name_spans(subject: str, names: list[str]) -> list[tuple[int, int]]:
    """Non-overlapping spans of known names in subject (longest match wins)."""
    if not subject or not names:
        return []
    lower = subject.casefold()
    occupied = [False] * len(subject)
    spans: list[tuple[int, int]] = []

    for name in _prepared_names(names):
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
    spans = find_name_spans(subject, names)
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
    text = (subject or '').casefold()
    for name in _prepared_names(names):
        text = re.sub(
            rf'(?<![a-z]){re.escape(name.casefold())}(?![a-z])',
            ' ',
            text,
            flags=re.IGNORECASE,
        )
    text = WHITESPACE_RE.sub(' ', text).strip()
    compact = NON_ALNUM_RE.sub(' ', text)
    compact = WHITESPACE_RE.sub(' ', compact).strip()
    return compact or '(no subject)'


def group_key_from_template(template: str) -> str:
    canon = canonicalize_template(template)
    return 't:' + hashlib.sha1(canon.encode()).hexdigest()[:16]


def group_key_from_norm(norm: str) -> str:
    return 'n:' + hashlib.sha1(norm.encode()).hexdigest()[:16]


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            self.parent[ra] = rb
        elif self.rank[ra] > self.rank[rb]:
            self.parent[rb] = ra
        else:
            self.parent[rb] = ra
            self.rank[ra] += 1


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
    Cluster subjects: seed regexes from titles that mention a known name,
    then pull in any other title that matches those regexes (unknown names
    included). Fall back to normalised exact match for the rest.
    """
    n = len(subjects)
    if n == 0:
        return []

    uf = _UnionFind(n)

    # Seed templates from subjects that mention a configured name
    templates: dict[str, re.Pattern[str]] = {}
    template_members: dict[str, list[int]] = defaultdict(list)
    for i, subject in enumerate(subjects):
        tmpl = subject_template(subject, names)
        if tmpl is None:
            continue
        key = group_key_from_template(tmpl)
        if key not in templates:
            templates[key] = template_to_regex(tmpl)
        template_members[key].append(i)

    # Every subject matching a template joins that template's cluster
    for key, rx in templates.items():
        matched = [i for i, subject in enumerate(subjects) if rx.match(subject or '')]
        if not matched:
            matched = template_members[key]
        root = matched[0]
        for i in matched[1:]:
            uf.union(root, i)

    # Exact normalised subject (known names stripped) for remaining merges
    by_norm: dict[str, list[int]] = defaultdict(list)
    for i, subject in enumerate(subjects):
        by_norm[normalize_subject(subject, names)].append(i)
    for indices in by_norm.values():
        root = indices[0]
        for i in indices[1:]:
            uf.union(root, i)

    # Stable id per component: prefer a template key if any member seeded one,
    # else normalised hash of the representative subject.
    component_ids: dict[int, str] = {}
    for i, subject in enumerate(subjects):
        root = uf.find(i)
        if root in component_ids:
            continue
        tmpl = subject_template(subject, names)
        if tmpl is not None:
            component_ids[root] = group_key_from_template(tmpl)
            continue
        # If another member of this component seeded a template, reuse it
        seeded = None
        for j, other in enumerate(subjects):
            if uf.find(j) != root:
                continue
            other_tmpl = subject_template(other, names)
            if other_tmpl is not None:
                seeded = group_key_from_template(other_tmpl)
                break
        if seeded:
            component_ids[root] = seeded
        else:
            component_ids[root] = group_key_from_norm(normalize_subject(subject, names))

    # Second pass: members that only matched via regex may share a root with a
    # template seed — already handled. Ensure all roots have ids.
    return [component_ids[uf.find(i)] for i in range(n)]


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
    group_ids = assign_group_ids([m.subject for m in messages], names)
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

        tmpl = subject_template(primary.subject, names)
        normalized = canonicalize_template(tmpl) if tmpl else normalize_subject(primary.subject, names)

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
