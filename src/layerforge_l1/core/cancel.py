"""Cooperative cancellation of a running job (the app's Stop button for planning), D-V3-055.

The app runs one job at a time on its worker thread (``app/jobs.py``).  When a job is cancellable, the worker
binds the job's cancel event to its thread; code that may be interrupted runs inside ``interruptible()``, and
``check()`` raises ``JobCancelled`` there once a cancel was requested.  Checks are placed where the work is
between two steps (every progress event of the proposal, and the start of every planner evaluation), never in the
middle of writing a file.  Outside ``interruptible()`` - in particular while a finished plan is being published -
``check()`` does nothing, so a cancel can only stop work whose output is still in a staging folder.

``JobCancelled`` derives from ``BaseException`` so that ``except Exception`` handlers in the computation never
turn a cancel into an ordinary failure; the callers' ``except BaseException`` clean-up (removing the staging
folder) runs and the exception reaches the job runner, which records the job as cancelled.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

_local = threading.local()


class JobCancelled(BaseException):
    """The user stopped the job; nothing it was producing is kept."""


@contextmanager
def bound(event: threading.Event | None) -> Iterator[None]:
    """Bind a job's cancel event to the current (worker) thread for the duration of the job."""

    previous = getattr(_local, "event", None)
    _local.event = event
    try:
        yield
    finally:
        _local.event = previous


@contextmanager
def interruptible() -> Iterator[None]:
    """Allow ``check()`` to stop the work inside this block (its output must be disposable)."""

    previous = getattr(_local, "armed", False)
    _local.armed = True
    try:
        check()  # a cancel that arrived while the job was still preparing
        yield
    finally:
        _local.armed = previous


def check() -> None:
    """Raise ``JobCancelled`` if the current job was cancelled and the work may be interrupted here."""

    if getattr(_local, "armed", False):
        event = getattr(_local, "event", None)
        if event is not None and event.is_set():
            raise JobCancelled()
