"""
core/sgt_i/host.py - SGT-I's host: its own thread, a time budget, and an off switch
===================================================================================
Blueprint 14.2, the contract between SGT-C (the Core) and SGT-I (Intelligence), in code:

  1. One way. The Core hands over an Observation (a frozen copy) and carries on. submit()
     never blocks and never raises; nothing here holds a reference to a Core object.
  2. The Core only changes through the user's approval (the SGT lab, step 11) - so there is no
     call here that writes into the Core.
  3. Look harder, never less. The advisory channel can only ASK for extra reads of a session
     (wants_read: counted reads, or a bounded read-every-tick window); it has no way to skip a read, veto a capture or change a value.
  4. Beside, never instead. The enrichment channel collects each component's output per session;
     the Core puts it in raw_payload["sgt_i"] of the row and nowhere else.
  5. Isolated failure. Components run on this host's own thread, after the Core's tick, with a
     per-page time budget. An exception, a budget overrun, or a page still running after
     HANG_SEC (checked each time the Core hands over the next page, the way vsdc_uia_text
     abandons a wedged read) trips SGT-I off for the rest of the run. The Core never notices.
  6. One switch: set_enabled() follows Settings -> Tracker -> SGT-I. Off = the Core's behaviour
     exactly as before: nothing is queued, no read is asked for, no enrichment is handed out.

A component is any object with a `name` and `observe(obs, ctx)`; ctx is a _Context bound to
the observation's session. No components are registered yet - they come with the later steps.
"""

import json
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional

from .observation import Observation

PAGE_BUDGET_SEC = 2.0          # all components together, one page
HANG_SEC = 15.0                # a page still running this long = hung
QUEUE_CAP = 8                  # pages waiting; the oldest is dropped when full
MAX_EXTRA_READS = 3            # outstanding extra reads one session may ask for
EXTRA_READ_TTL_SEC = 60.0      # an unused request expires
READ_HARDER_MAX_SEC = 30.0     # longest "read on every tick" window one ask can open (user, W6-2)
ENRICH_MAX_BYTES = 4096        # one component's enrichment for one session, as JSON


class _Context:
    """What a component may do with one observation: enrich its row, ask for more reads."""

    def __init__(self, host: "SgtIntelligence", component: str, session_id: str) -> None:
        self._host = host
        self._component = component
        self._session_id = session_id

    def enrich(self, data: Dict[str, Any]) -> None:
        """This component's facts for the session's rows (replaces its previous ones).
        Structure, counts and masked shapes only - never a raw value (blueprint 14.5)."""
        self._host._enrich(self._session_id, self._component, data)

    def ask_more_reads(self, count: int = 1) -> None:
        """Ask the Core to read this session's page again even if nothing seems to change."""
        self._host._ask_reads(self._session_id, count)

    def read_harder(self, seconds: float = READ_HARDER_MAX_SEC) -> None:
        """Ask the Core to read this session's page on every tick for a while (at most
        READ_HARDER_MAX_SEC from now), whether or not it seems to change. Never shortens a window."""
        self._host._read_harder(self._session_id, seconds)


class SgtIntelligence:
    def __init__(self, components: Optional[List[Any]] = None, enabled: bool = False,
                 budget_sec: float = PAGE_BUDGET_SEC, hang_sec: float = HANG_SEC,
                 echo: Callable[[str], None] = print,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self._components: List[Any] = list(components or [])
        self._enabled = bool(enabled)
        self._budget = float(budget_sec)
        self._hang = float(hang_sec)
        self._echo = echo
        self._now = monotonic
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._queue: Deque[Observation] = deque()
        self._thread: Optional[threading.Thread] = None
        self._busy_since: Optional[float] = None
        self._enrichment: Dict[str, Dict[str, Any]] = {}
        self._reads: Dict[str, List[float]] = {}     # session -> expiry times of asked-for reads
        self._harder: Dict[str, float] = {}          # session -> end of its read-every-tick window
        self.tripped: Optional[str] = None            # why SGT-I switched itself off, this run
        self.pages = 0
        self.dropped = 0

    # ── The switch ───────────────────────────────────────────────────────────────
    def register(self, component: Any) -> None:
        with self._lock:
            self._components.append(component)

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
        self._enrichment.clear()
        self._reads.clear()
        self._harder.clear()

    def _trip(self, why: str) -> None:
        with self._lock:
            if self.tripped is not None:
                return
            self.tripped = why
            self._clear()
            self._wake.notify_all()
        self._echo(f"[SGT-I] switched off for the rest of this run: {why} (SGT capture is not affected)")

    # ── Core side (the Core's thread): never blocks, never raises ────────────────
    def submit(self, obs: Observation) -> None:
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
                self._queue.append(obs)
                if self._thread is None:
                    self._thread = threading.Thread(target=self._run, name="sgt-i", daemon=True)
                    self._thread.start()
                self._wake.notify()
        except Exception:
            pass

    def wants_read(self, session_id: str) -> bool:
        """Advisory channel: True when SGT-I asked for another read of this session - inside a
        read-harder window, or by consuming one counted extra read."""
        try:
            if not self.active:
                return False
            with self._lock:
                now = self._now()
                until = self._harder.get(session_id)
                if until is not None:
                    if until > now:
                        return True
                    del self._harder[session_id]
                live = [t for t in self._reads.get(session_id, ()) if t > now]
                if not live:
                    self._reads.pop(session_id, None)
                    return False
                live.pop(0)
                if live:
                    self._reads[session_id] = live
                else:
                    self._reads.pop(session_id, None)
                return True
        except Exception:
            return False

    def enrichment(self, session_id: str) -> Dict[str, Any]:
        """Enrichment channel: a copy of what the components added for this session ({} = nothing)."""
        try:
            if not self.active:
                return {}
            with self._lock:
                got = self._enrichment.get(session_id)
                return json.loads(json.dumps(got)) if got else {}
        except Exception:
            return {}

    # ── SGT-I side (its own thread) ──────────────────────────────────────────────
    def _run(self) -> None:
        while True:
            with self._lock:
                while self.tripped is None and not self._queue:
                    self._wake.wait()
                if self.tripped is not None:
                    self._thread = None
                    return
                obs = self._queue.popleft()
                components = list(self._components)
                self._busy_since = self._now()
            try:
                self._page(obs, components)
            finally:
                self._busy_since = None

    def _page(self, obs: Observation, components: List[Any]) -> None:
        start = self._now()
        for comp in components:
            name = str(getattr(comp, "name", type(comp).__name__))
            try:
                comp.observe(obs, _Context(self, name, obs.session_id))
            except Exception as e:
                self._trip(f"{name} failed: {type(e).__name__}: {e}")
                return
            if self.tripped is not None:
                return
            spent = self._now() - start
            if spent > self._budget:
                self._trip(f"{name} took the page to {spent:.2f} s (budget {self._budget:.2f} s)")
                return
        self.pages += 1

    def _enrich(self, session_id: str, component: str, data: Dict[str, Any]) -> None:
        if not self.active:
            return
        text = json.dumps(data, ensure_ascii=False, sort_keys=True)   # raises on what is not JSON
        if len(text.encode("utf-8")) > ENRICH_MAX_BYTES:
            raise ValueError(f"enrichment over {ENRICH_MAX_BYTES} bytes")
        with self._lock:
            self._enrichment.setdefault(session_id, {})[component] = json.loads(text)

    def _ask_reads(self, session_id: str, count: int) -> None:
        if not self.active:
            return
        with self._lock:
            now = self._now()
            live = [t for t in self._reads.get(session_id, ()) if t > now]
            live += [now + EXTRA_READ_TTL_SEC] * max(0, int(count))
            self._reads[session_id] = live[:MAX_EXTRA_READS]

    def _read_harder(self, session_id: str, seconds: float) -> None:
        if not self.active:
            return
        span = min(max(0.0, float(seconds)), READ_HARDER_MAX_SEC)
        with self._lock:
            until = self._now() + span
            if until > self._harder.get(session_id, 0.0):
                self._harder[session_id] = until
