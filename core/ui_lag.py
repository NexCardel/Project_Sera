"""
core/ui_lag.py - how responsive the window is, and what made it lag
====================================================================
hang_watchdog catches a frozen window (4 s and more). This catches the small hitches a person feels as
"slow" - a click answered 300 ms late, a grid that stutters while it fills - and says where each came
from, so the slow parts can be found and fixed instead of guessed (owner request 2026-11-22: is the UI
slow on an i3 / 8 GB PC, and why).

How it measures:
  * A 50 ms precise QTimer on the Qt main thread. How late each tick arrives is how long anything the
    user did would have waited: that is the lag figure (p50 / p95 / p99 / max per minute).
  * A sampler thread checks the main thread's heartbeat every 25 ms. While the main thread is more than
    STALL_MS behind, it samples the main thread's Python stack (a statistical profiler that only runs
    during a hitch) and which other threads were running Python at that moment (they compete for the GIL).
  * When the tick arrives late (a hitch, >= HITCH_MS), the samples taken during it are attributed to it:
    the innermost frame in Sera's own code ("where"), and its nearest Sera caller ("via").
    No sample in Sera's code means the main thread was inside Qt (layout, painting, stylesheets) or
    waiting for the GIL - the busy threads then say who held it.

Written to ~/AmanAssociates_Sera/logs/ui_lag.log (1 MB x 3), by the sampler thread, never the UI thread:
  MIN   once a minute when something lagged, else every 10 minutes: the lag figures, hitches, time
        blocked, whether Sera's window was in front (in_use), CPU, page faults, and the top causes.
  HITCH one line per hitch >= DETAIL_MS (at most MAX_DETAILS a minute): its causes and Sera stack.
tools/ui_lag_report.py ranks the causes over days of logs.

Privacy: code locations (file:line function), thread names, times and counts only - never page text,
client data, window titles or values. A gap over SLEEP_GAP_SEC (the PC slept) is not lag and is skipped.
SERA_UI_LAG=off switches it off. Costs one timer tick per 50 ms and a thread waking every 25 ms.
"""

import ctypes
import os
import sys
import threading
import time
from collections import Counter, defaultdict, deque
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

TICK_MS = 50
SAMPLE_MS = 25
STALL_MS = 60            # main thread behind by this much: start sampling its stack
HITCH_MS = 100           # a tick this late is a hitch
DETAIL_MS = 250          # a hitch this long gets its own HITCH line
SLEEP_GAP_SEC = 30.0     # a gap this long is the PC asleep, not lag
QUIET_EVERY_MIN = 10     # a minute with no hitch is written only this often
MAX_DETAILS = 30         # HITCH lines per minute at most
TOP_CAUSES = 5
STACK_DEPTH = 8          # Sera frames kept for a HITCH line
LOG_MAX_BYTES = 1_000_000
LOG_KEEP = 3

QT_KEY = "[Qt / event loop: layout, painting, styles, or waiting for the GIL]"
NO_SAMPLE_KEY = "[not sampled: main thread held the GIL in Qt/C++]"

# Innermost frames that mean "this thread is waiting, not running Python".
_IDLE_FUNCS = frozenset({
    "wait", "sleep", "acquire", "get", "select", "read", "readinto", "recv", "recv_into", "accept",
    "_wait_for_tstate_lock", "join", "readline", "poll", "_recv", "_recv_bytes", "get_nowait", "WaitOne",
    "run_forever", "_run_once", "_exactly", "Take", "exec", "exec_", "communicate", "result",
})

_REPO = Path(__file__).resolve().parents[1]
_SERA_DIRS = frozenset({"core", "ui", "sera_db", "tracker_dump_parser", "tools", "build_tools"})
_STDLIB = frozenset(getattr(sys, "stdlib_module_names", ()))

_meter: Optional["UiLagMeter"] = None


def log_path() -> Path:
    return Path.home() / "AmanAssociates_Sera" / "logs" / "ui_lag.log"


# ---- which frames are Sera's own code --------------------------------------------------------------

_rel_cache: Dict[str, Optional[str]] = {}


def sera_rel(filename: str) -> Optional[str]:
    """The repo-relative path of a frame's file when it is Sera's own code, else None (stdlib, PySide6,
    site-packages). Works from source (absolute paths under the repo) and frozen (relative paths)."""
    hit = _rel_cache.get(filename, "?")
    if hit != "?":
        return hit
    rel: Optional[str] = None
    f = filename.replace("\\", "/")
    if "site-packages/" not in f and not f.startswith("<"):
        p = Path(filename)
        if p.is_absolute():
            try:
                rel = p.resolve().relative_to(_REPO).as_posix()
            except Exception:
                rel = None
            if rel and (rel.startswith("venv/") or rel.startswith(".")):
                rel = None
        else:
            parts = f.split("/")
            if len(parts) > 1:
                rel = f if parts[0] in _SERA_DIRS else None
            elif f.endswith(".py") and f[:-3] not in _STDLIB:
                rel = f
    if len(_rel_cache) > 4000:
        _rel_cache.clear()
    _rel_cache[filename] = rel
    return rel


def sera_frames(frame: Any) -> List[str]:
    """Sera's frames of a stack, innermost first, as "path:line function"."""
    out: List[str] = []
    f = frame
    while f is not None:
        rel = sera_rel(f.f_code.co_filename)
        if rel is not None:
            out.append(f"{rel}:{f.f_lineno} {f.f_code.co_name}")
        f = f.f_back
    return out


def _is_idle(frame: Any) -> bool:
    return frame is None or frame.f_code.co_name in _IDLE_FUNCS


def main_key(frames: List[str]) -> str:
    """The cause a main-thread sample is counted under: the innermost Sera frame, via its Sera caller.
    The bottom of the stack (main.py's app.exec) alone means Qt / the event loop."""
    if not frames or (len(frames) == 1 and frames[0].startswith("main.py:") and frames[0].endswith(" run")):
        return QT_KEY
    inner = frames[0]
    if inner.startswith("main.py:") and inner.endswith((" run", " <module>")):
        return QT_KEY
    via = next((f for f in frames[1:] if f.split(" ", 1)[1] != inner.split(" ", 1)[1]), "")
    if via.startswith("main.py:") and via.endswith((" run", " <module>")):
        via = ""                                  # called straight from the event loop
    return f"{inner} (via {via})" if via else inner


# ---- figures -------------------------------------------------------------------------------------------

def percentile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * q))]


def _process_cpu_seconds() -> float:
    try:
        k = ctypes.windll.kernel32
        k.GetCurrentProcess.restype = wintypes.HANDLE
        c, e, kt, ut = (wintypes.FILETIME() for _ in range(4))
        if not k.GetProcessTimes(k.GetCurrentProcess(), ctypes.byref(c), ctypes.byref(e), ctypes.byref(kt), ctypes.byref(ut)):
            return -1.0
        ticks = lambda ft: (ft.dwHighDateTime << 32) | ft.dwLowDateTime
        return (ticks(kt) + ticks(ut)) / 1e7
    except Exception:
        return -1.0


class _PMC(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + \
               [(n, ctypes.c_size_t) for n in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                                               "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                                               "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]


def _page_faults() -> int:
    try:
        k = ctypes.windll.kernel32
        k.GetCurrentProcess.restype = wintypes.HANDLE
        pmc = _PMC()
        pmc.cb = ctypes.sizeof(_PMC)
        if ctypes.windll.psapi.GetProcessMemoryInfo(k.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return int(pmc.PageFaultCount)
    except Exception:
        pass
    return -1


def _sera_in_front() -> bool:
    try:
        u = ctypes.windll.user32
        hwnd = u.GetForegroundWindow()
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value) == os.getpid()
    except Exception:
        return False


# ---- the meter -----------------------------------------------------------------------------------------

class _Hitch:
    __slots__ = ("ms", "at", "in_use", "causes", "busy", "stack")

    def __init__(self, ms: float, at: float, in_use: bool):
        self.ms, self.at, self.in_use = ms, at, in_use
        self.causes: Dict[str, float] = {}
        self.busy: Dict[str, float] = {}
        self.stack: List[str] = []


class UiLagMeter:
    """Start with start(app); everything after runs on its own. Never raises into the app."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, write: Optional[Callable[[str], None]] = None,
                 in_front: Callable[[], bool] = _sera_in_front, main_ident: Optional[int] = None):
        self._clock = clock
        self._write = write or _append
        self._in_front = in_front
        self._main_ident = main_ident or threading.main_thread().ident
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._last_beat = clock()
        self._samples: List[Tuple[str, List[str], Tuple[Tuple[str, str], ...]]] = []   # since the last tick
        self._lates: List[float] = []                # this minute's tick lateness, ms
        self._hitches: List[_Hitch] = []             # this minute's hitches
        self._front_seen = False
        self._minute_start = clock()
        self._quiet_minutes = 0
        self._cpu0 = _process_cpu_seconds()
        self._faults0 = _page_faults()
        self._timer: Any = None
        self._thread: Optional[threading.Thread] = None

    # ---- main thread ---------------------------------------------------------------------------------

    def start(self, app: Any) -> None:
        from PySide6.QtCore import Qt, QTimer
        self._last_beat = self._clock()
        self._timer = QTimer(app)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self.tick)
        self._timer.start(TICK_MS)
        self._thread = threading.Thread(target=self._sample_loop, name="ui-lag-sampler", daemon=True)
        self._thread.start()
        try:
            app.aboutToQuit.connect(self.stop)
        except Exception:
            pass

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._timer is not None:
                self._timer.stop()
        except Exception:
            pass

    def tick(self) -> None:
        """The QTimer's slot: how late this tick is. Kept tiny - it runs 20 times a second on the UI thread."""
        try:
            now = self._clock()
            late = (now - self._last_beat) * 1000.0 - TICK_MS
            self._last_beat = now
            with self._lock:
                samples, self._samples = self._samples, []
                if late > SLEEP_GAP_SEC * 1000.0:
                    return                               # the PC slept (or the clock jumped): not lag
                late = max(0.0, late)
                self._lates.append(late)
                if late >= HITCH_MS:
                    self._hitches.append(self._attribute(late, now, samples))
        except Exception:
            pass

    def _attribute(self, late: float, now: float, samples: List[Tuple[str, List[str], Tuple]]) -> _Hitch:
        h = _Hitch(late, now, self._in_front_safe())
        if not samples:
            h.causes[NO_SAMPLE_KEY] = late
            return h
        share = late / len(samples)
        for key, frames, busy in samples:
            h.causes[key] = h.causes.get(key, 0.0) + share
            for name, where in busy:
                k = f"{name} @ {where}"
                h.busy[k] = h.busy.get(k, 0.0) + share
        longest = Counter(key for key, _f, _b in samples).most_common(1)[0][0]
        h.stack = next(f for k, f, _b in samples if k == longest)[:STACK_DEPTH]
        return h

    def _in_front_safe(self) -> bool:
        try:
            return bool(self._in_front())
        except Exception:
            return False

    # ---- sampler thread --------------------------------------------------------------------------------

    def _sample_loop(self) -> None:
        next_front = 0.0
        while not self._stop.wait(SAMPLE_MS / 1000.0):
            try:
                now = self._clock()
                if now >= next_front:
                    next_front = now + 1.0
                    if self._in_front_safe():
                        self._front_seen = True
                behind = (now - self._last_beat) * 1000.0 - TICK_MS
                if STALL_MS <= behind <= SLEEP_GAP_SEC * 1000.0:
                    self._sample()
                if now - self._minute_start >= 60.0:
                    self._flush_minute(now)
            except Exception:
                pass

    def _sample(self) -> None:
        frames = sys._current_frames()
        main = frames.get(self._main_ident)
        stack = sera_frames(main) if main is not None else []
        busy = []
        names = {t.ident: t.name for t in threading.enumerate()}
        me = threading.get_ident()
        for ident, f in frames.items():
            if ident in (self._main_ident, me) or _is_idle(f):
                continue
            where = sera_frames(f)
            busy.append((names.get(ident, "thread"), where[0] if where else f"{f.f_code.co_name} (library)"))
        with self._lock:
            self._samples.append((main_key(stack), stack, tuple(busy)))

    def _flush_minute(self, now: float) -> None:
        with self._lock:
            lates, self._lates = self._lates, []
            hitches, self._hitches = self._hitches, []
            in_use, self._front_seen = self._front_seen, False
            started, self._minute_start = self._minute_start, now
        cpu1, faults1 = _process_cpu_seconds(), _page_faults()
        cpu = 100.0 * (cpu1 - self._cpu0) / max(1e-6, now - started) if cpu1 >= 0 and self._cpu0 >= 0 else -1.0
        faults = faults1 - self._faults0 if faults1 >= 0 and self._faults0 >= 0 else -1
        self._cpu0, self._faults0 = cpu1, faults1
        if not hitches:
            self._quiet_minutes += 1
            if self._quiet_minutes < QUIET_EVERY_MIN:
                return
        self._quiet_minutes = 0
        self._write(format_minute(lates, hitches, in_use, cpu, faults))
        for h in sorted((h for h in hitches if h.ms >= DETAIL_MS), key=lambda h: -h.ms)[:MAX_DETAILS]:
            self._write(format_hitch(h))


def _top(d: Dict[str, float], n: int) -> List[Tuple[str, float]]:
    return sorted(d.items(), key=lambda kv: -kv[1])[:n]


def format_minute(lates: List[float], hitches: List[_Hitch], in_use: bool, cpu: float, faults: int) -> str:
    causes: Dict[str, float] = defaultdict(float)
    counts: Counter = Counter()
    for h in hitches:
        for k, ms in h.causes.items():
            causes[k] += ms
            counts[k] += 1
    top = " | ".join(f"{k} {ms:.0f}ms/{counts[k]}x" for k, ms in _top(causes, TOP_CAUSES))
    return (f"{datetime.now():%Y-%m-%d %H:%M:%S} MIN ticks={len(lates)} p50={percentile(lates, 0.5):.0f} "
            f"p95={percentile(lates, 0.95):.0f} p99={percentile(lates, 0.99):.0f} max={max(lates, default=0):.0f} "
            f"hitch100={sum(1 for h in hitches if h.ms >= 100)} hitch250={sum(1 for h in hitches if h.ms >= 250)} "
            f"hitch1s={sum(1 for h in hitches if h.ms >= 1000)} blocked_ms={sum(h.ms for h in hitches):.0f} "
            f"in_use={'yes' if in_use else 'no'} cpu={cpu:.1f}% faults={faults}"
            + (f" top= {top}" if top else ""))


def format_hitch(h: _Hitch) -> str:
    causes = " | ".join(f"{k} {ms:.0f}ms" for k, ms in _top(h.causes, 3))
    busy = " | ".join(f"{k} {ms:.0f}ms" for k, ms in _top(h.busy, 3))
    stack = " < ".join(h.stack)
    return (f"{datetime.now():%Y-%m-%d %H:%M:%S} HITCH ms={h.ms:.0f} in_use={'yes' if h.in_use else 'no'} "
            f"main= {causes}" + (f" busy= {busy}" if busy else "") + (f" stack= {stack}" if stack else ""))


def _append(line: str) -> None:
    try:
        p = log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and p.stat().st_size > LOG_MAX_BYTES:
            for i in range(LOG_KEEP - 1, 0, -1):
                older, newer = p.with_name(f"{p.name}.{i}"), p.with_name(f"{p.name}.{i + 1}")
                if older.exists():
                    older.replace(newer)
            p.replace(p.with_name(p.name + ".1"))
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def start(app: Any) -> Optional[UiLagMeter]:
    """Start the meter once for the app (SERA_UI_LAG=off: not at all). Never raises."""
    global _meter
    if _meter is not None or os.environ.get("SERA_UI_LAG", "").strip().lower() in ("off", "0", "no"):
        return _meter
    try:
        _meter = UiLagMeter()
        _meter.start(app)
        _append(f"{datetime.now():%Y-%m-%d %H:%M:%S} START pid={os.getpid()} "
                f"cpus={os.cpu_count()} frozen={'yes' if getattr(sys, 'frozen', False) else 'no'}")
    except Exception:
        _meter = None
    return _meter
