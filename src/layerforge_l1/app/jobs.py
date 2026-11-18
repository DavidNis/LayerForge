"""Background job runner for the L4 application.

Planning, validation, CAD export and print kits take seconds to minutes and are
memory-heavy, so they run one at a time on a single worker thread; HTTP requests
are served from other threads and only read job state.  Each job records the
progress events the core functions already emit (``progress(stage, **fields)``).
"""

from __future__ import annotations

import itertools
import queue
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from ..core import cancel
from ..core.model import StackError

QUEUED, RUNNING, DONE, FAILED, CANCELLED = "queued", "running", "done", "failed", "cancelled"
MAX_EVENTS = 400


class JobConflict(Exception):
    """A job for this project is already queued or running."""


@dataclass
class Job:
    id: str
    kind: str
    project: str
    status: str = QUEUED
    events: list[dict[str, Any]] = field(default_factory=list)
    result: Any = None
    error: dict[str, Any] | None = None
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    # Planning jobs can be stopped (``core/cancel.py``): the event is checked between steps of the work.
    cancellable: bool = False
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def progress(self, stage: str, **details: Any) -> None:
        elapsed = round(time.time() - (self.started or self.created), 1)
        self.events.append({"stage": stage, "elapsed_s": elapsed, **_plain(details)})
        del self.events[:-MAX_EVENTS]
        cancel.check()  # every progress event is a point where an interruptible step may stop

    def to_json(self) -> dict[str, Any]:
        end = self.finished or time.time()
        return {
            "id": self.id, "kind": self.kind, "project": self.project, "status": self.status,
            "events": self.events, "last_event": self.events[-1] if self.events else None,
            "result": self.result, "error": self.error,
            "elapsed_s": round(end - self.started, 1) if self.started else 0.0,
            "cancellable": self.cancellable, "cancel_requested": self.cancel_event.is_set(),
        }


def _plain(value: Any) -> Any:
    """JSON-safe copy of progress fields (numpy scalars, tuples, paths)."""

    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        return value.item()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def describe_error(error: BaseException) -> dict[str, Any]:
    """User-facing error: core errors carry actionable messages; anything else is a bug."""

    if isinstance(error, StackError):
        payload = {"type": type(error).__name__, "message": str(error), "expected": True}
        report = getattr(error, "report", None)
        if isinstance(report, dict):
            payload["report"] = _plain({k: v for k, v in report.items() if k != "reveal_config"})
        return payload
    return {"type": type(error).__name__, "message": str(error) or type(error).__name__, "expected": False,
            "hint": "Unexpected error inside LayerForge; the server console has the traceback."}


class JobRunner:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._queue: queue.Queue[tuple[Job, Callable[[Callable[..., None]], Any]]] = queue.Queue()
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._worker = threading.Thread(target=self._run, name="layerforge-jobs", daemon=True)
        self._worker.start()

    def submit(self, kind: str, project: str, work: Callable[[Callable[..., None]], Any],
               cancellable: bool = False) -> Job:
        with self._lock:
            active = self.active(project)
            if active is not None:
                raise JobConflict(f"'{active.kind}' is still {active.status} for this project; wait for it to finish")
            job = Job(id=f"job{next(self._ids)}", kind=kind, project=project, cancellable=cancellable)
            self._jobs[job.id] = job
        self._queue.put((job, work))
        return job

    def cancel(self, job_id: str) -> Job:
        """Ask a queued or running cancellable job to stop.  A queued job never starts; a running one stops at
        its next check and is reported "cancelled" once its clean-up has finished (never before)."""

        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if job.status not in (QUEUED, RUNNING):
                raise JobConflict(f"'{job.kind}' has already {job.status}")
            if not job.cancellable:
                raise JobConflict(f"'{job.kind}' cannot be stopped; wait for it to finish")
            job.cancel_event.set()
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def active(self, project: str) -> Job | None:
        for job in self._jobs.values():
            if job.project == project and job.status in (QUEUED, RUNNING):
                return job
        return None

    def running(self) -> list[Job]:
        """Queued or running jobs of every project."""

        return [job for job in list(self._jobs.values()) if job.status in (QUEUED, RUNNING)]

    def latest(self, project: str) -> Job | None:
        jobs = [job for job in self._jobs.values() if job.project == project]
        return max(jobs, key=lambda job: job.created) if jobs else None

    def _run(self) -> None:
        while True:
            job, work = self._queue.get()
            job.status, job.started = RUNNING, time.time()
            if job.cancel_event.is_set():  # stopped while queued: nothing ran
                job.status, job.finished = CANCELLED, time.time()
                job.events.append({"stage": "CANCELLED", "elapsed_s": 0.0, "while": "queued"})
                continue
            job.progress("STARTED", kind=job.kind)
            try:
                with cancel.bound(job.cancel_event if job.cancellable else None):
                    job.result = _plain(work(job.progress))
                job.status = DONE
                job.progress("FINISHED")
            except cancel.JobCancelled:
                job.status = CANCELLED  # the work's own clean-up (staging folders) has already run
                job.events.append({"stage": "CANCELLED", "elapsed_s": round(time.time() - job.started, 1)})
            except BaseException as error:  # noqa: BLE001 - every failure is reported to the UI
                job.error = describe_error(error)
                job.status = FAILED
                job.progress("FAILED", message=job.error["message"])
                if not job.error["expected"]:
                    traceback.print_exc()
            finally:
                job.finished = time.time()
