"""
core/vsdc/vsdc_diag.py — what the capture engine was doing, on disk
====================================================================
The installed app has no console, so every print() the capture engine makes is lost on a real PC.
Added 2026-10-13 after an office PC stopped capturing from the GST portal 3-3.5 hours into two
evenings in a row with nothing on disk to say why. Three things go to
~/AmanAssociates_Sera/logs/vsdc.log (rolled to vsdc.log.1 past LOG_MAX_BYTES):

  events     UI Automation reads that ran past their timeout, workers replaced, UIA paused or
             resumed, capture-tick errors (each distinct one at most once a minute), engine
             settings applied, the capture worker starting / stopping / stalling.
  heartbeat  once a minute from the capture worker: ticks and errors since the last beat, the
             slowest tick, what was in front (browser exe, address bar readable, in scope, portal,
             page link - only for pages inside the scope gate), SGT's reads since the last beat
             and UIA's read times / skips / stuck workers. A missing heartbeat = a dead or stuck
             worker thread.
  stall      a watcher thread: the capture worker not finishing a tick for STALL_S writes every
             thread's stack to logs/hang.log (core/hang_watchdog) and a line here.

Never written: page text, values, client names, PANs/GSTINs, links of pages outside the portals.
Never raises.
"""

import os
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

LOG_ENV = "VSDC_DIAG_LOG"
LOG_MAX_BYTES = 2_000_000      # ~5 days of heartbeats per file, plus vsdc.log.1
HEARTBEAT_SEC = 60.0
STALL_S = 30.0                 # a capture tick this long is stuck, not slow
STALL_REPEAT_S = 120.0         # while stuck, dump again this often
MAX_STALL_DUMPS = 3

_lock = threading.Lock()
_limited: Dict[str, list] = {}     # key -> [last written (monotonic), suppressed since]


def log_path() -> Path:
    env = os.environ.get(LOG_ENV)
    return Path(env) if env else Path.home() / "AmanAssociates_Sera" / "logs" / "vsdc.log"


def note(text: str) -> None:
    """One timestamped line (or block) in vsdc.log, and on the console."""
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {text}"
    try:
        print(f"[VSDC diag] {text}")
    except Exception:
        pass
    try:
        with _lock:
            path = log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
                path.replace(path.with_suffix(".log.1"))
            with open(path, "a", encoding="utf-8") as f:
                f.write(line.rstrip("\n") + "\n")
    except Exception:
        pass


def note_limited(key: str, text: str, every_sec: float = 60.0) -> None:
    """note(), but one line per key per every_sec; the lines held back are counted on the next."""
    now = time.monotonic()
    with _lock:
        state = _limited.get(key)
        if state is not None and now - state[0] < every_sec:
            state[1] += 1
            return
        held = state[1] if state else 0
        _limited[key] = [now, 0]
    note(text + (f" (+{held} like it since the last line)" if held else ""))


def _fmt(fields: Dict[str, Any]) -> str:
    parts = []
    for k, v in fields.items():
        if v is None or v == "":
            continue
        if isinstance(v, float):
            v = f"{v:.0f}" if abs(v) >= 10 else f"{v:.1f}"
        parts.append(f"{k}={v}")
    return " ".join(parts)


class Heartbeat:
    """Counts the capture worker's ticks; write() once a minute (called from the worker's loop)."""

    def __init__(self, every_sec: float = HEARTBEAT_SEC, clock=time.monotonic):
        self._every = every_sec
        self._clock = clock
        self._last = clock()
        self.ticks = 0
        self.errors = 0
        self.max_tick_ms = 0.0

    def tick(self, ms: float, error: bool = False) -> None:
        self.ticks += 1
        if error:
            self.errors += 1
        if ms > self.max_tick_ms:
            self.max_tick_ms = ms

    def due(self) -> bool:
        return self._clock() - self._last >= self._every

    def write(self, router: Any, paused: bool = False) -> None:
        try:
            fields: Dict[str, Any] = {"ticks": self.ticks, "errors": self.errors,
                                      "max_tick_ms": round(self.max_tick_ms), "paused": "yes" if paused else None,
                                      "threads": threading.active_count()}
            fields.update(getattr(router, "diag_tick", None) or {})
            sgt = getattr(router, "_sgt", None)
            if sgt is not None and hasattr(sgt, "take_diag"):
                fields.update({f"sgt_{k}": v for k, v in sgt.take_diag().items()})
            try:
                from core.vsdc import vsdc_uia_text
                fields.update({f"uia_{k}": v for k, v in vsdc_uia_text.take_read_stats().items()})
            except Exception:
                pass
            note("beat " + _fmt(fields))
        except Exception as e:
            note(f"beat failed: {type(e).__name__}: {str(e)[:120]}")
        self._last = self._clock()
        self.ticks = self.errors = 0
        self.max_tick_ms = 0.0


def tick_error(exc: BaseException) -> None:
    """A capture-tick exception: the first of each kind in a minute with its stack (code
    locations only; the message is cut short)."""
    msg = f"{type(exc).__name__}: {str(exc)[:160]}"
    stack = "".join(traceback.format_tb(exc.__traceback__)[-6:]).rstrip()
    note_limited("tick-error:" + msg, f"tick error {msg}\n{stack}")


def watch_worker(worker: Any, stall_s: float = STALL_S) -> Optional[threading.Thread]:
    """Starts a thread that watches worker.last_progress (time.monotonic(), set after every tick)
    while worker._running and not worker._paused. A stall dumps every thread's stack to hang.log."""
    def run():
        dumps, last_dump, stalled_since = 0, 0.0, None
        while getattr(worker, "_running", False):
            time.sleep(1.0)
            try:
                if getattr(worker, "_paused", False):
                    continue
                last = float(getattr(worker, "last_progress", 0.0) or 0.0)
                stalled = time.monotonic() - last if last else 0.0
                if stalled_since is not None and last != stalled_since:
                    # A tick finished since the stall was reported (even if the next one is slow).
                    note(f"capture worker moving again after {last - stalled_since:.0f} s")
                    stalled_since = None
                if stalled < stall_s:
                    continue
                if stalled_since is None:
                    stalled_since, dumps, last_dump = last, 0, 0.0
                if dumps < MAX_STALL_DUMPS and time.monotonic() - last_dump >= STALL_REPEAT_S:
                    note(f"capture worker STALLED: no tick finished for {stalled:.0f} s - stacks in hang.log")
                    try:
                        from core import hang_watchdog
                        hang_watchdog.dump_threads(f"capture worker stalled {stalled:.0f} s",
                                                   native=(dumps == 0))
                    except Exception:
                        pass
                    dumps += 1
                    last_dump = time.monotonic()
            except Exception:
                pass

    try:
        t = threading.Thread(target=run, name="vsdc-stall-watch", daemon=True)
        t.start()
        return t
    except Exception:
        return None
