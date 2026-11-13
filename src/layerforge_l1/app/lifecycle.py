"""When the desktop app may stop on its own: no LayerForge tab is open any more (D-V3-050).

Each open page (tab) posts a heartbeat (``POST /api/presence {"tab"}``) every ``HEARTBEAT_S`` seconds and
a "leaving" beacon when it is unloaded (closed, reloaded, navigated away).  The launcher asks
``Lifecycle.try_close`` once a second; it says yes only when

* no tab is open: every tab has left, or has not been heard from for ``STALE_S`` (a crashed or killed
  browser sends nothing; hidden tabs still send about one heartbeat a minute).  A tab the browser puts to
  sleep (Edge sleeping tabs, Chrome freezing) sends nothing either, but says so first (the page's ``freeze``
  event posts ``{"tab", "sleeping": true}``): it counts as open for ``SLEEP_S``, so a sleeping tab never
  stops the app while the user may still come back to it;
* the last request of any kind is at least ``GRACE_S`` old (a reload's new page checks in well within it);
* no job is queued or running (planning, export, ...), and no request is being handled (a save, an upload,
  a download); and
* after a start (or a second double-click of LayerForge.exe) no tab has checked in for ``AWAIT_S``: the
  browser may be slow to open, so the app waits that long for the first page before giving up.

Deciding to close and refusing new requests happen under one lock, so a request that arrives just then is
answered 503 ("LayerForge is closing") instead of being cut off half-way.  Pure logic with an injectable
clock, so the timing rules are unit-tested without waiting.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

HEARTBEAT_S = 15.0
STALE_S = 30 * 60.0  # no heartbeat and no "sleeping" notice: the browser crashed or was killed
SLEEP_S = 8 * 3600.0  # a sleeping tab may be woken any time in a working day
GRACE_S = 10.0
AWAIT_S = 180.0
MAX_TABS = 200  # a misbehaving client cannot grow the table without bound


class Lifecycle:
    def __init__(self, clock: Callable[[], float] = time.monotonic, *, stale_s: float | None = None,
                 grace_s: float | None = None, await_s: float | None = None, sleep_s: float | None = None) -> None:
        self.clock = clock
        self.stale_s = STALE_S if stale_s is None else stale_s
        self.sleep_s = SLEEP_S if sleep_s is None else sleep_s
        self.grace_s = GRACE_S if grace_s is None else grace_s
        self.await_s = AWAIT_S if await_s is None else await_s
        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)
        self._tabs: dict[str, tuple[float, bool]] = {}  # tab -> (last heard from, asleep)
        now = clock()
        self._last_activity = now
        self._await_until = now + self.await_s
        self._inflight = 0
        self.closing = False
        self.reason = ""

    # -- events from the server ------------------------------------------------------------------------------
    def seen(self, tab: str, sleeping: bool = False) -> bool:
        """A heartbeat from an open page (``sleeping``: its last word before the browser freezes it);
        False when the app is already closing."""

        with self._lock:
            if self.closing:
                return False
            now = self.clock()
            if tab in self._tabs or len(self._tabs) < MAX_TABS:
                self._tabs[tab] = (now, sleeping)
            self._last_activity, self._await_until = now, 0.0
            return True

    def left(self, tab: str) -> None:
        with self._lock:
            self._tabs.pop(tab, None)
            self._last_activity = self.clock()

    def expect_page(self) -> None:
        """LayerForge.exe was started again and reopens the browser: wait for that page as after a start."""

        with self._lock:
            self._await_until = max(self._await_until, self.clock() + self.await_s)

    def request_started(self) -> bool:
        with self._lock:
            if self.closing:
                return False
            self._inflight += 1
            self._last_activity = self.clock()
            return True

    def request_finished(self) -> None:
        with self._lock:
            self._inflight -= 1
            self._last_activity = self.clock()
            self._idle.notify_all()

    # -- decisions -------------------------------------------------------------------------------------------
    def open_tabs(self) -> int:
        with self._lock:
            return len(self._live(self.clock()))

    def _live(self, now: float) -> dict[str, float]:
        self._tabs = {tab: (seen, asleep) for tab, (seen, asleep) in self._tabs.items()
                      if now - seen < (self.sleep_s if asleep else self.stale_s)}
        return self._tabs

    def try_close(self, jobs_running: bool) -> str | None:
        """Close now if nothing needs the app: returns the reason (and refuses new requests), else None."""

        with self._lock:
            if self.closing:
                return None
            now = self.clock()
            if self._live(now) or jobs_running or self._inflight:
                return None
            if now < self._await_until or now - self._last_activity < self.grace_s:
                return None
            self.closing, self.reason = True, "no LayerForge tab is open"
            return self.reason

    def close_now(self, reason: str) -> None:
        """Explicit Exit: refuse new requests at once."""

        with self._lock:
            self.closing, self.reason = True, reason

    def wait_idle(self, timeout: float) -> bool:
        """After closing: wait until requests being handled have been answered."""

        deadline = time.monotonic() + timeout
        with self._lock:
            while self._inflight:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._idle.wait(remaining)
            return True
