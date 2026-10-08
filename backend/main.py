"""FastAPI entrypoint for ishmail_v2."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Allow `uvicorn main:app` from backend/
sys.path.insert(0, str(Path(__file__).resolve().parent))

from api_v1 import router as v1_router
from grouping import build_groups
from jobs import queue
from mail_service import mail_service

app = FastAPI(
    title='ishmail_v2',
    version='0.1.0',
    description=(
        'Multi-inbox email organiser. UI uses /api/* (async jobs). '
        'Scripts and agents can use sync /v1/* while the app is running — '
        'see /docs.'
    ),
)
app.include_router(v1_router)

_fetch_job_lock = threading.Lock()
_active_fetch_job_id: str | None = None


def _current_fetch_job():
    if not _active_fetch_job_id:
        return None
    job = queue.get(_active_fetch_job_id)
    if not job or job.kind != 'fetch':
        return None
    if job.status in ('pending', 'running'):
        return job
    return None

app.add_middleware(
    CORSMiddleware,
    allow_origins=['http://localhost:5173', 'http://127.0.0.1:5173'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


class MessageRef(BaseModel):
    account_key: str
    folder: str
    uid: int


class ActionRequest(BaseModel):
    action: str  # delete | archive | star | unstar
    messages: list[MessageRef]
    account_keys: list[str] | None = Field(
        default=None,
        description='If set, only apply to these inboxes (others untouched)',
    )


class SenderActionRequest(BaseModel):
    sender: str
    account_keys: list[str] | None = None


@app.on_event('startup')
def startup():
    mail_service.reload_creds()


@app.get('/api/health')
def health():
    return {'ok': True}


@app.get('/api/accounts')
def list_accounts():
    accounts = [
        {
            'key': a.key,
            'email': a.email,
            'provider': a.provider,
            'name': a.name,
            'auth': a.auth,
        }
        for a in mail_service.accounts()
    ]
    return {
        'accounts': accounts,
        'names': mail_service.creds.names,
    }


_groups_cache: dict = {}


def _emails_payload(
    messages,
    errors,
    *,
    archived: bool,
    q: str | None,
    fetching: bool,
    generation: int,
    history: str,
):
    cache_key = (generation, len(messages), archived, q or '', history)
    cached = _groups_cache.get(cache_key)
    if cached is not None:
        groups = cached
    else:
        groups = build_groups(
            messages,
            mail_service.creds.names,
            archived_view=archived,
            query=q,
        )
        _groups_cache.clear()
        _groups_cache[cache_key] = groups
    return {
        'emails': [
            {
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
            for g in groups
        ],
        'errors': errors,
        'total_messages': len(messages),
        'fetching': fetching,
        'history': history,
    }


@app.get('/api/emails')
def list_emails(
    archived: bool = Query(False),
    q: str | None = Query(None),
    refresh: bool = Query(False),
):
    # Prefer the live snapshot so the UI can show groups while a background
    # fetch is still walking accounts. Never block this request on IMAP.
    if refresh:
        # Kick a background job if one isn't already running; response still
        # comes from whatever is already in the cache.
        start_fetch(force=True, full_history=False)

    messages, errors, fetching, has_cache, generation, history = mail_service.snapshot()
    if has_cache or fetching:
        return _emails_payload(
            messages,
            errors,
            archived=archived,
            q=q,
            fetching=fetching,
            generation=generation,
            history=history,
        )

    # Cold start with no job yet — return empty rather than blocking.
    return _emails_payload(
        [], [], archived=archived, q=q, fetching=False, generation=0, history='recent',
    )


@app.get('/api/message')
def message_detail(
    account_key: str = Query(...),
    folder: str = Query(...),
    uid: int = Query(...),
):
    try:
        detail = mail_service.get_message_detail(account_key, folder, uid)
    except KeyError:
        raise HTTPException(404, f'Unknown account {account_key}') from None
    except Exception as e:
        raise HTTPException(502, str(e)) from e
    return detail


@app.get('/api/senders/{sender_email}')
def sender_emails(sender_email: str, refresh: bool = Query(False)):
    sender = sender_email.lower().strip()
    if refresh:
        start_fetch(force=True, full_history=False)
    messages, errors, _fetching, has_cache, _generation, _history = mail_service.snapshot()
    if not has_cache and not _fetching:
        messages, errors = mail_service.fetch_all(force=False)
    matched = [m for m in messages if m.from_email.lower() == sender]
    matched.sort(key=lambda m: m.date, reverse=True)
    return {
        'sender': sender,
        'count': len(matched),
        'account_keys': sorted({m.account_key for m in matched}),
        'messages': [
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
            for m in matched
        ],
        'errors': errors,
    }


@app.get('/api/jobs/fetch/active')
def active_fetch_job():
    """Return the in-flight fetch job, if any (for UI coalescing)."""
    job = _current_fetch_job()
    if not job:
        return {'job': None}
    return {'job': job.to_dict()}


@app.post('/api/jobs/fetch')
def start_fetch(
    force: bool = Query(True),
    full_history: bool = Query(False),
):
    """Background multi-account fetch with per-inbox progress (single-flight).

    Default loads ~6 months (SINCE). Pass full_history=true for the whole mailbox.
    """
    global _active_fetch_job_id

    with _fetch_job_lock:
        existing = _current_fetch_job()
        if existing:
            existing_full = bool(
                existing.progress.get('full_history')
                or existing.progress.get('history') == 'full'
            )
            # Coalesce same-or-broader in-flight fetches; supersede recent→full.
            if existing_full or not full_history:
                return existing.to_dict()
            _active_fetch_job_id = None

        total = len(mail_service.accounts())
        history = 'full' if full_history else 'recent'

        def run(job):
            global _active_fetch_job_id
            try:
                def progress_cb(p):
                    job.progress = p

                messages, errors = mail_service.fetch_all(
                    force=force or full_history,
                    progress_cb=progress_cb,
                    full_history=full_history,
                )
                return {
                    'total_messages': len(messages),
                    'errors': errors,
                    'history': history,
                }
            finally:
                with _fetch_job_lock:
                    if _active_fetch_job_id == job.id:
                        _active_fetch_job_id = None

        job = queue.submit(
            'fetch',
            run,
            progress={
                'done': 0,
                'total': total,
                'full_history': full_history,
                'history': history,
            },
        )
        _active_fetch_job_id = job.id
        return job.to_dict()


@app.post('/api/jobs/action')
def start_action(body: ActionRequest):
    if body.action not in ('delete', 'archive', 'star', 'unstar'):
        raise HTTPException(400, f'Unknown action {body.action}')

    refs = [m.model_dump() for m in body.messages]
    account_keys = body.account_keys

    def run(job):
        messages, _ = mail_service.fetch_all()
        targets = mail_service.messages_for_refs(messages, refs, account_keys)

        # Expand: when operating on a group, clients send all dupe message refs;
        # account_keys filter which inboxes participate.
        def progress_cb(p):
            job.progress = p

        return mail_service.apply_action(body.action, targets, progress_cb=progress_cb)

    job = queue.submit(body.action, run, progress={'done': 0, 'total': 0})
    return job.to_dict()


@app.post('/api/jobs/unsubscribe')
def start_unsubscribe(body: SenderActionRequest):
    def run(job):
        def progress_cb(p):
            job.progress = p
        return mail_service.unsubscribe_sender(body.sender, body.account_keys, progress_cb)

    job = queue.submit('unsubscribe', run)
    return job.to_dict()


@app.post('/api/jobs/delete-sender')
def start_delete_sender(body: SenderActionRequest):
    def run(job):
        def progress_cb(p):
            job.progress = p
        return mail_service.delete_sender(body.sender, body.account_keys, progress_cb)

    job = queue.submit('delete-sender', run)
    return job.to_dict()


@app.get('/api/jobs')
def list_jobs():
    return {'jobs': [j.to_dict() for j in queue.list_recent()]}


@app.get('/api/jobs/{job_id}')
def get_job(job_id: str):
    job = queue.get(job_id)
    if not job:
        raise HTTPException(404, 'Job not found')
    return job.to_dict()


@app.post('/api/refresh')
def refresh(full_history: bool = Query(False)):
    messages, errors = mail_service.fetch_all(force=True, full_history=full_history)
    return {
        'total_messages': len(messages),
        'errors': errors,
        'history': 'full' if full_history else 'recent',
    }
