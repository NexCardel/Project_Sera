"""
core/scc/host.py - SCC-U's host: its own thread, a time budget, and an off switch
=================================================================================
Autofill-tweaks blueprint G.1, the contract between SGT-C (the Core) and SCC-U, built like
SGT-I's host (core/sgt_i/host.py, docs/sgt-blueprint.md 14.2):

  1. Read-only copy. The Core hands over an Observation (core/sgt_i/observation.py) plus the
     window it was read in, and carries on. submit() never blocks and never raises; nothing here
     holds a reference to a Core object, and there is no call that writes into the Core.
  2. More reads, never fewer. While an attempt waits for an outcome, SCC-U may ask the Core to
     read a session's page on every tick for at most READ_WINDOW_MAX_SEC after each copy
     (wants_read). It has no way to skip a read, veto a capture or change a value.
  3. Own thread, budget and trip. Handlers run on this host's thread with a per-page time
     budget. An exception, a budget overrun, or a page still running after HANG_SEC (checked
     each time the Core hands over the next page) trips SCC-U's automatic part off for the rest
     of the run. The Core never notices; the card and the manual button keep working.
  4. One switch: set_enabled() follows Settings -> SCC -> Detect login automatically
     (scc_detect_mode). Off = nothing is queued and no read is asked for.

A handler is any object with a `name` and `observe(obs, hwnd, ctx)`; ctx is a _Context bound to
the observation's session. None are registered yet - they come with the later WPs (G.2).
"""

import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from core.sgt_i.observation import Observation

PAGE_BUDGET_SEC = 1.0          # all handlers together, one page
HANG_SEC = 15.0                # a page still running this long = hung
QUEUE_CAP = 8                  # pages waiting; the oldest is dropped when full
READ_WINDOW_MAX_SEC = 30.0     # longest "read on every tick" window one copy can open (G.1 rule 2)


class _Context:
    """What a handler may do with one observation: ask for more reads of its session."""

    def __init__(self, host: "SccHost", session_id: str) -> None:
        self._host = host
        self._session_id = session_id

    def read_harder(self, seconds: float = READ_WINDOW_MAX_SEC) -> None:
        """Read this session's page on every tick for a while (at most READ_WINDOW_MAX_SEC)."""
        self._host.open_read_window(self._session_id, seconds)


class SccHost:
    def __init__(self, handlers: Optional[List[Any]] = None, enabled: bool = False,
                 budget_sec: float = PAGE_BUDGET_SEC, hang_sec: float = HANG_SEC,
                 echo: Callable[[str], None] = print,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self._handlers: List[Any] = list(handlers or [])
        self._enabled = bool(enabled)
        self._budget = float(budget_sec)
        self._hang = float(hang_sec)
        self._echo = echo
        self._now = monotonic
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._queue: Deque[Tuple[int, Observation]] = deque()
        self._thread: Optional[threading.Thread] = None
        self._busy_since: Optional[float] = None
        self._windows: Dict[str, float] = {}          # session -> end of its read-every-tick window
        self.tripped: Optional[str] = None             # why SCC-U switched itself off, this run
        self.pages = 0
        self.dropped = 0

    # ── The switch ───────────────────────────────────────────────────────────────
    def register(self, handler: Any) -> None:
        with self._lock:
            self._handlers.append(handler)

    def set_enabled(self, on: bool) -> None:
        with self._lock:
            self._enabled = bool(on)
            if not self._enabled:
                self._clear()

    @property
    def active(self) -> bool:
        return self._enabled and self.tripped is None

    def _clear(self) -> None:
        self._queue.clear()
        self._windows.clear()

    def _trip(self, why: str) -> None:
        with self._lock:
            if self.tripped is not None:
                return
            self.tripped = why
            self._clear()
            self._wake.notify_all()
        self._echo(f"[SCC-U] automatic login detection switched off for the rest of this run: {why} "
                   f"(SGT capture is not affected)")

    # ── Core side (the Core's thread): never blocks, never raises ────────────────
    def submit(self, obs: Observation, hwnd: int = 0) -> None:
        try:
            if not self.active:
                return
            busy = self._busy_since
            if busy is not None and self._now() - busy > self._hang:
                self._trip(f"a page has been running for over {self._hang:.0f} s (hung)")
                return
            with self._lock:
                if len(self._queue) >= QUEUE_CAP:
                    self._queue.popleft()
                    self.dropped += 1
                self._queue.append((int(hwnd or 0), obs))
                if self._thread is None:
                    self._thread = threading.Thread(target=self._run, name="scc-u", daemon=True)
                    self._thread.start()
                self._wake.notify()
        except Exception:
            pass

    def wants_read(self, session_id: str) -> bool:
        """True inside a read window SCC-U opened for this session (after a copy)."""
        try:
            if not self.active:
                return False
            with self._lock:
                until = self._windows.get(session_id)
                if until is None:
                    return False
                if until > self._now():
                    return True
                del self._windows[session_id]
                return False
        except Exception:
            return False

    # ── Any thread (the card's copy, a handler) ──────────────────────────────────
    def open_read_window(self, session_id: str, seconds: float = READ_WINDOW_MAX_SEC) -> None:
        """Ask the Core to read this session's page on every tick for `seconds` from now
        (clamped to READ_WINDOW_MAX_SEC). Never shortens a window already open."""
        try:
            if not self.active or not session_id:
                return
            span = min(max(0.0, float(seconds)), READ_WINDOW_MAX_SEC)
            with self._lock:
                until = self._now() + span
                if until > self._windows.get(session_id, 0.0):
                    self._windows[session_id] = until
        except Exception:
            pass

    # ── SCC-U side (its own thread) ──────────────────────────────────────────────
    def _run(self) -> None:
        while True:
            with self._lock:
                while self.tripped is None and not self._queue:
                    self._wake.wait()
                if self.tripped is not None:
                    self._thread = None
                    return
                hwnd, obs = self._queue.popleft()
                handlers = list(self._handlers)
                self._busy_since = self._now()
            try:
                self._page(obs, hwnd, handlers)
            finally:
                self._busy_since = None

    def _page(self, obs: Observation, hwnd: int, handlers: List[Any]) -> None:
        start = self._now()
        for handler in handlers:
            name = str(getattr(handler, "name", type(handler).__name__))
            try:
                handler.observe(obs, hwnd, _Context(self, obs.session_id))
            except Exception as e:
                self._trip(f"{name} failed: {type(e).__name__}")
                return
            if self.tripped is not None:
                return
            spent = self._now() - start
            if spent > self._budget:
                self._trip(f"{name} took the page to {spent:.2f} s (budget {self._budget:.2f} s)")
                return
        self.pages += 1
