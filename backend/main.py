"""FastAPI entrypoint for ishmail_v2."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Allow `uvicorn main:app` from backend/
sys.path.insert(0, str(Path(__file__).resolve().parent))

from grouping import build_groups
from jobs import queue
from mail_service import mail_service

app = FastAPI(title='ishmail_v2', version='0.1.0')

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


@app.get('/api/emails')
def list_emails(
    archived: bool = Query(False),
    q: str | None = Query(None),
    refresh: bool = Query(False),
):
    messages, errors = mail_service.fetch_all(force=refresh)
    groups = build_groups(
        messages,
        mail_service.creds.names,
        archived_view=archived,
        query=q,
    )
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
    }


@app.get('/api/senders/{sender_email}')
def sender_emails(sender_email: str, refresh: bool = Query(False)):
    sender = sender_email.lower().strip()
    messages, errors = mail_service.fetch_all(force=refresh)
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
def refresh():
    messages, errors = mail_service.fetch_all(force=True)
    return {'total_messages': len(messages), 'errors': errors}
