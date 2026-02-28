from __future__ import annotations

import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def create(self) -> dict[str, Any]:
        job_id = str(uuid.uuid4())
        with self._lock:
            job = {
                "job_id": job_id,
                "status": "queued",
                "progress": 0,
                "stage": "queued",
                "stage_detail": "Waiting in queue.",
                "warnings": [],
                "error": None,
                "upload_id": None,
                "graph": None,
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
            }
            self._jobs[job_id] = job
            return deepcopy(job)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return deepcopy(job) if job else None

    def update(self, job_id: str, **changes: Any) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            job.update(changes)
            job["updated_at"] = _now_iso()
            return deepcopy(job)

    def append_warning(self, job_id: str, warning: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            warnings = list(job.get("warnings") or [])
            warnings.append(warning)
            job["warnings"] = warnings
            job["updated_at"] = _now_iso()
            return deepcopy(job)
