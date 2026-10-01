"""Background jobs for long-running API operations (runs, optimisation, tornado, validation)."""

from __future__ import annotations

import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Job:
    job_id: str
    kind: str
    status: str = "queued"          # queued | running | done | error
    result: Any = None
    error: str | None = None
    progress: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"job_id": self.job_id, "kind": self.kind, "status": self.status, "error": self.error, "progress": self.progress,
                "result": self.result if self.status == "done" else None}


class JobRegistry:
    def __init__(self, workers: int = 2) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=workers)

    def submit(self, kind: str, fn: Callable[[Job], Any]) -> Job:
        job = Job(str(uuid.uuid4())[:8], kind)
        with self._lock:
            self._jobs[job.job_id] = job

        def run() -> None:
            job.status = "running"
            try:
                job.result = fn(job)
                job.status = "done"
            except Exception as e:  # noqa: BLE001
                job.error = f"{type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}"
                job.status = "error"

        self._pool.submit(run)
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def all(self) -> list[Job]:
        return list(self._jobs.values())
