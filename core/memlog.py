"""
core/memlog.py — where the app's memory goes
=============================================
Records the process's memory at named moments (start-up stages, a window opening, a periodic
sample) so a real PC's numbers can be broken down instead of guessed.

Two figures per line:
  ws    working set - roughly what Task Manager's Memory column shows
  priv  private commit - memory Windows has promised the process, including reservations
        that are not in use (a numpy/OpenBLAS thread pool once cost ~225 MB of this alone)

Written to the console and to ~/AmanAssociates_Sera/logs/memory.log (kept under ~1 MB by
rolling to memory.log.1). Costs one Windows call per line. Never raises.
"""

import ctypes
import os
import time
from contextlib import contextmanager
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

LOG_MAX_BYTES = 1_000_000


def _seconds_since_process_start() -> float:
    """How long ago Windows created this process - so the log's "+N s" includes launching
    Python / unpacking the exe and importing, not just the time since this module loaded."""
    try:
        k = ctypes.WinDLL("kernel32")
        k.GetCurrentProcess.restype = wintypes.HANDLE
        created, _e, _k, _u = (wintypes.FILETIME() for _ in range(4))
        k.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        if not k.GetProcessTimes(k.GetCurrentProcess(), ctypes.byref(created), ctypes.byref(_e),
                                 ctypes.byref(_k), ctypes.byref(_u)):
            return 0.0
        now = wintypes.FILETIME()
        k.GetSystemTimePreciseAsFileTime(ctypes.byref(now))
        ticks = lambda ft: (ft.dwHighDateTime << 32) | ft.dwLowDateTime     # 100 ns units
        return max(0.0, (ticks(now) - ticks(created)) / 1e7)
    except Exception:
        return 0.0


_T0 = time.monotonic() - _seconds_since_process_start()      # "+N s" = seconds since launch
_last_ws: Optional[float] = None


class _Counters(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t)]


def snapshot() -> Optional[Tuple[float, float, float]]:
    """(working set MB, private commit MB, peak working set MB), or None if unavailable."""
    try:
        k = ctypes.WinDLL("kernel32")
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Counters), wintypes.DWORD]
        k.K32GetProcessMemoryInfo.restype = wintypes.BOOL
        c = _Counters()
        c.cb = ctypes.sizeof(_Counters)
        if not k.K32GetProcessMemoryInfo(k.GetCurrentProcess(), ctypes.byref(c), c.cb):
            return None
        mb = 1024 * 1024
        return c.WorkingSetSize / mb, c.PrivateUsage / mb, c.PeakWorkingSetSize / mb
    except Exception:
        return None


TRIM_CEILING_MB = 120.0         # periodic samples trim only when the working set is above this
                                # (the app settles at ~55 MB after a trim; measured 2026-09-21)


def trim_working_set(reason: str) -> Optional[float]:
    """
    Hands back to Windows the memory the app touched once and no longer uses (start-up code,
    first-draw buffers, a finished export). Measured 2026-09-21: 134 MB -> 63 MB right after
    a trim, 75 MB after the window redrew - i.e. ~58 MB was never going to be used again.
    Nothing is lost: a page that IS needed later comes straight back from memory (standby list),
    not from disk. Logs the working set before and after. Returns MB released, or None.
    """
    before = snapshot()
    try:
        k = ctypes.WinDLL("kernel32")
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.K32EmptyWorkingSet.argtypes = [wintypes.HANDLE]
        k.K32EmptyWorkingSet.restype = wintypes.BOOL
        if not k.K32EmptyWorkingSet(k.GetCurrentProcess()):
            return None
    except Exception:
        return None
    after = snapshot()
    if before is None or after is None:
        return None
    mark(f"trim ({reason}): released {before[0] - after[0]:.1f} MB")
    return before[0] - after[0]


def sample_and_maybe_trim(stage: str, may_trim=lambda: True) -> None:
    """A periodic sample; trims only when the working set is above TRIM_CEILING_MB (a heavy
    one-off such as an Excel export) AND may_trim() - the app passes "the window is not in use":
    a trim drops the working set to ~2 MB and the next click pages it all back in, which is
    felt as a slow screen (reported 2026-09-22). Not "growth since the last trim": normal use
    pages ~50 MB straight back in, so that rule trimmed every 10 min for nothing."""
    mark(stage)
    snap = snapshot()
    if snap and snap[0] >= TRIM_CEILING_MB and may_trim():
        trim_working_set(f"working set {snap[0]:.0f} MB, above {TRIM_CEILING_MB:.0f} MB")


SLOW_MS = 150.0     # a screen switch slower than this is felt - log it


@contextmanager
def timed(label: str):
    """Logs "slow: <label> took N ms" (with the memory at that moment) when the block takes
    SLOW_MS or longer - so "the grid feels slow" can be checked in memory.log."""
    t = time.perf_counter()
    try:
        yield
    finally:
        ms = (time.perf_counter() - t) * 1000
        if ms >= SLOW_MS:
            mark(f"slow: {label} took {ms:.0f} ms")


def _log_path() -> Path:
    return Path.home() / "AmanAssociates_Sera" / "logs" / "memory.log"


def mark(stage: str) -> None:
    """One line: the stage, memory now, and the working-set change since the previous line."""
    global _last_ws
    snap = snapshot()
    if snap is None:
        return
    ws, priv, peak = snap
    delta = "" if _last_ws is None else f" ({ws - _last_ws:+.1f})"
    _last_ws = ws
    line = (f"{datetime.now():%Y-%m-%d %H:%M:%S} +{time.monotonic() - _T0:7.1f}s  "
            f"ws {ws:6.1f} MB{delta:>9}  priv {priv:6.1f} MB  peak {peak:6.1f} MB  {stage}")
    print(f"[Memory] {line}")
    try:
        p = _log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and p.stat().st_size > LOG_MAX_BYTES:
            os.replace(p, p.with_suffix(".log.1"))
        with open(p, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
