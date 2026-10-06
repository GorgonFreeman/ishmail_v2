"""In-process background job queue so the UI stays interactive."""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable


@dataclass
class Job:
    id: str
    kind: str
    status: str = 'pending'  # pending | running | completed | failed
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: str | None = None
    finished_at: str | None = None
    progress: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'kind': self.kind,
            'status': self.status,
            'created_at': self.created_at,
            'started_at': self.started_at,
            'finished_at': self.finished_at,
            'progress': self.progress,
            'result': self.result,
            'error': self.error,
        }


class JobQueue:
    def __init__(self, max_workers: int = 4):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=max_workers)

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_recent(self, limit: int = 30) -> list[Job]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
            return jobs[:limit]

    def submit(self, kind: str, fn: Callable[[Job], dict | None], progress: dict | None = None) -> Job:
        job = Job(id=str(uuid.uuid4()), kind=kind, progress=progress or {})
        with self._lock:
            self._jobs[job.id] = job

        def runner():
            job.status = 'running'
            job.started_at = datetime.now(timezone.utc).isoformat()
            try:
                result = fn(job)
                job.result = result or {}
                job.status = 'completed'
            except Exception as e:
                job.status = 'failed'
                job.error = f'{e}\n{traceback.format_exc()}'
            finally:
                job.finished_at = datetime.now(timezone.utc).isoformat()

        self._pool.submit(runner)
        return job


# Shared singleton for the API process
queue = JobQueue(max_workers=6)
