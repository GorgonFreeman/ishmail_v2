"""Synchronous local API for scripts / agents while ishmail is running.

UI uses the async /api/jobs/* endpoints. This /v1 surface returns results
inline so curl and tooling don't need to poll jobs.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from grouping import EmailGroup, build_groups
from mail_service import mail_service

router = APIRouter(prefix='/v1', tags=['v1'])

ACTIONS = frozenset({'delete', 'archive', 'star', 'unstar'})


class ActionBody(BaseModel):
    action: str = Field(description='delete | archive | star | unstar')
    group_ids: list[str] | None = None
    messages: list[dict] | None = Field(
        default=None,
        description='[{account_key, folder, uid}, ...]',
    )
    account_keys: list[str] | None = None
    archived: bool = False


class GroupActionBody(BaseModel):
    account_keys: list[str] | None = None
    archived: bool = False


def _snapshot_groups(
    *,
    archived: bool,
    q: str | None = None,
) -> tuple[list[EmailGroup], list[str], bool, str, list]:
    messages, errors, fetching, _has_cache, _generation, history = mail_service.snapshot()
    groups = build_groups(
        messages,
        mail_service.creds.names,
        archived_view=archived,
        query=q,
    )
    return groups, errors, fetching, history, messages


def _group_to_dict(g: EmailGroup) -> dict:
    return {
        'id': g.id,
        'subject': g.subject,
        'subject_normalized': g.subject_normalized,
        'from_email': g.from_email,
        'from_name': g.from_name,
        'date': g.date,
        'flagged': g.flagged,
        'archived': g.archived,
        'duplicate_count': g.duplicate_count,
        'messages': g.messages,
        'account_keys': g.account_keys,
    }


def _find_group(group_id: str, archived: bool) -> tuple[EmailGroup | None, list]:
    """Find a group; if missing in the requested view, try the other view."""
    groups, _errors, _fetching, _history, messages = _snapshot_groups(archived=archived)
    for g in groups:
        if g.id == group_id:
            return g, messages
    other = not archived
    groups2, _e, _f, _h, messages2 = _snapshot_groups(archived=other)
    for g in groups2:
        if g.id == group_id:
            return g, messages2
    return None, messages


def _apply_to_groups(
    action: str,
    group_ids: list[str],
    *,
    archived: bool,
    account_keys: list[str] | None,
) -> dict:
    if action not in ACTIONS:
        raise HTTPException(400, f'Unknown action {action}')
    if not group_ids:
        raise HTTPException(400, 'group_ids required')

    all_targets = []
    found = []
    missing = []
    cache_messages = None
    for gid in group_ids:
        group, messages = _find_group(gid, archived)
        cache_messages = messages
        if group is None:
            missing.append(gid)
            continue
        found.append(gid)
        refs = [
            {
                'account_key': m['account_key'],
                'folder': m['folder'],
                'uid': m['uid'],
            }
            for m in group.messages
        ]
        targets = mail_service.messages_for_refs(
            messages,
            refs,
            account_keys,
        )
        all_targets.extend(targets)

    if missing and not found:
        raise HTTPException(404, f'Group(s) not found: {", ".join(missing)}')

    result = mail_service.apply_action(action, all_targets) if all_targets else {
        'action': action,
        'affected': 0,
        'results': [],
    }
    return {
        **result,
        'group_ids': found,
        'missing_group_ids': missing,
        'message_count': len(all_targets),
    }


@router.get('/status')
def status():
    messages, errors, fetching, has_cache, generation, history = mail_service.snapshot()
    return {
        'ok': True,
        'fetching': fetching,
        'has_cache': has_cache,
        'history': history,
        'generation': generation,
        'total_messages': len(messages),
        'errors': errors,
        'accounts': len(mail_service.accounts()),
    }


@router.get('/accounts')
def accounts():
    return {
        'accounts': [
            {
                'key': a.key,
                'email': a.email,
                'provider': a.provider,
                'name': a.name,
                'auth': a.auth,
            }
            for a in mail_service.accounts()
        ],
        'names': mail_service.creds.names,
    }


@router.get('/search')
def search(
    q: str | None = Query(None, description='Match subject / sender (substring)'),
    archived: bool = Query(False),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Search grouped emails from the live cache (non-blocking)."""
    groups, errors, fetching, history, _messages = _snapshot_groups(
        archived=archived,
        q=q,
    )
    slice_ = groups[offset:offset + limit]
    return {
        'q': q or '',
        'archived': archived,
        'history': history,
        'fetching': fetching,
        'total': len(groups),
        'offset': offset,
        'limit': limit,
        'emails': [_group_to_dict(g) for g in slice_],
        'errors': errors,
    }


@router.get('/groups/{group_id}')
def get_group(group_id: str, archived: bool = Query(False)):
    group, _messages = _find_group(group_id, archived)
    if group is None:
        raise HTTPException(404, f'Group not found: {group_id}')
    return _group_to_dict(group)


def _group_action(group_id: str, action: str, body: GroupActionBody | None):
    body = body or GroupActionBody()
    return _apply_to_groups(
        action,
        [group_id],
        archived=body.archived,
        account_keys=body.account_keys,
    )


@router.post('/groups/{group_id}/star')
def star_group(group_id: str, body: GroupActionBody | None = None):
    return _group_action(group_id, 'star', body)


@router.post('/groups/{group_id}/unstar')
def unstar_group(group_id: str, body: GroupActionBody | None = None):
    return _group_action(group_id, 'unstar', body)


@router.post('/groups/{group_id}/archive')
def archive_group(group_id: str, body: GroupActionBody | None = None):
    return _group_action(group_id, 'archive', body)


@router.post('/groups/{group_id}/delete')
def delete_group(group_id: str, body: GroupActionBody | None = None):
    return _group_action(group_id, 'delete', body)


@router.post('/action')
def action(body: ActionBody):
    """Run delete | archive | star | unstar and wait for IMAP to finish."""
    if body.action not in ACTIONS:
        raise HTTPException(400, f'Unknown action {body.action}')

    if body.group_ids:
        return _apply_to_groups(
            body.action,
            body.group_ids,
            archived=body.archived,
            account_keys=body.account_keys,
        )

    if not body.messages:
        raise HTTPException(400, 'Provide group_ids or messages')

    messages, _errors, _fetching, _has, _gen, _hist = mail_service.snapshot()
    targets = mail_service.messages_for_refs(
        messages,
        body.messages,
        body.account_keys,
    )
    if not targets:
        raise HTTPException(404, 'No matching messages in cache')
    result = mail_service.apply_action(body.action, targets)
    return {**result, 'message_count': len(targets)}


@router.post('/fetch')
def fetch(
    force: bool = Query(True),
    full_history: bool = Query(False),
    wait: bool = Query(
        False,
        description='If true, block until fetch finishes (can be slow)',
    ),
):
    """Kick a background fetch, or wait for a synchronous one."""
    if wait:
        messages, errors = mail_service.fetch_all(
            force=force or full_history,
            full_history=full_history,
        )
        return {
            'ok': True,
            'waited': True,
            'total_messages': len(messages),
            'errors': errors,
            'history': 'full' if full_history else 'recent',
        }

    # Reuse the UI job endpoint's single-flight helper via lazy import.
    from main import start_fetch
    job = start_fetch(force=force, full_history=full_history)
    return {'ok': True, 'waited': False, 'job': job}
