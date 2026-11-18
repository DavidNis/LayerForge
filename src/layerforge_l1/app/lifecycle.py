"""Requests in progress when the app is stopped (D-V3-050, reduced by D-V3-055).

The launcher window's "Stop LayerForge" (or closing the window) stops the server.  Requests being answered at that
moment - a save, an upload, a download - finish first, and a request that arrives once stopping has begun is
answered 503 ("LayerForge is closing") instead of being cut off half-way.  Deciding to close and refusing new
requests happen under one lock.

(The browser-tab heartbeats and the automatic stop when the last tab closed were removed at the user's request:
the server keeps running until it is stopped in the launcher window.)
"""

from __future__ import annotations

import threading
import time


class Lifecycle:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)
        self._inflight = 0
        self.closing = False
        self.reason = ""

    def request_started(self) -> bool:
        """Count a request; False once the app is closing (the request is then refused)."""

        with self._lock:
            if self.closing:
                return False
            self._inflight += 1
            return True

    def request_finished(self) -> None:
        with self._lock:
            self._inflight -= 1
            self._idle.notify_all()

    def close_now(self, reason: str) -> None:
        """Stopping: refuse new requests from now on."""

        with self._lock:
            self.closing, self.reason = True, reason

    def wait_idle(self, timeout: float) -> bool:
        """After closing: wait until the requests being handled have been answered."""

        deadline = time.monotonic() + timeout
        with self._lock:
            while self._inflight:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._idle.wait(remaining)
            return True
