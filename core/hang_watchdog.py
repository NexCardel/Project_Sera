"""
core/hang_watchdog.py — what the app was doing when Windows said "Not responding"
================================================================================
The Qt main thread ticks a heartbeat every 500 ms. A background thread watches it: once the
heartbeat is more than STALL_S old (Windows shows "Not responding" at ~5 s), it writes every
thread's Python stack and how much CPU each thread burned over the last second to
~/AmanAssociates_Sera/logs/hang.log - then again every REPEAT_S while the stall lasts, so a
stuck loop (same stack each time) can be told from slow work (stack moves on).

Added 2026-09-26 after a minimised app sat at one full core, "Not responding", with nothing in
memory.log to say why. Costs one QTimer tick and one thread waking twice a second. Never raises.
"""

import ctypes
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

STALL_S = 4.0          # main thread silent this long = stalled
REPEAT_S = 10.0        # while stalled, dump again this often
MAX_DUMPS_PER_STALL = 4
LOG_MAX_BYTES = 1_000_000

_started = False


def _log_path() -> Path:
    return Path.home() / "AmanAssociates_Sera" / "logs" / "hang.log"


def _thread_cpu_seconds(native_id: int) -> float:
    """User + kernel CPU seconds a Windows thread has used, or -1 if unavailable."""
    try:
        k = ctypes.WinDLL("kernel32")
        k.OpenThread.restype = wintypes.HANDLE
        h = k.OpenThread(0x0800, False, native_id)      # THREAD_QUERY_LIMITED_INFORMATION
        if not h:
            return -1.0
        try:
            c, e, kt, ut = (wintypes.FILETIME() for _ in range(4))
            if not k.GetThreadTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kt), ctypes.byref(ut)):
                return -1.0
            ticks = lambda ft: (ft.dwHighDateTime << 32) | ft.dwLowDateTime
            return (ticks(kt) + ticks(ut)) / 1e7
        finally:
            k.CloseHandle(h)
    except Exception:
        return -1.0


def _append(text: str) -> None:
    print(text)
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
        path.replace(path.with_suffix(".log.1"))
    with open(path, "a", encoding="utf-8") as f:
        f.write(text)


def _native_dump() -> str:
    """The main thread stuck inside Qt or SQLite shows no useful Python stack (seen 2026-09-26:
    a lone stale frame), so also ask py-spy for native frames when it is installed."""
    exe = shutil.which("py-spy") or os.path.join(os.path.dirname(sys.executable), "py-spy.exe")
    if not os.path.exists(exe):
        return ""
    try:
        out = subprocess.run([exe, "dump", "--pid", str(os.getpid()), "--native"],
                             capture_output=True, text=True, timeout=30,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return "--- py-spy --native\n" + (out.stdout or out.stderr)
    except Exception as e:
        return f"--- py-spy --native failed: {e}"


def _dump(stalled_for: float, native: bool = False) -> None:
    try:
        native_text = _native_dump() if native else ""     # first: the stall may end any moment
        threads = {t.ident: t for t in threading.enumerate()}
        before = {i: _thread_cpu_seconds(t.native_id) for i, t in threads.items() if t.native_id}
        t0 = time.monotonic()
        time.sleep(1.0)
        frames = sys._current_frames()
        lines = [f"==== {datetime.now():%Y-%m-%d %H:%M:%S}  main thread stalled {stalled_for:.1f} s ===="]
        for ident, frame in frames.items():
            t = threads.get(ident)
            name = t.name if t else f"thread {ident}"
            cpu = ""
            if t and t.native_id and before.get(ident, -1) >= 0:
                after = _thread_cpu_seconds(t.native_id)
                if after >= 0:
                    cpu = f"  cpu {100 * (after - before[ident]) / (time.monotonic() - t0):.0f}% of a core"
            lines.append(f"--- {name}{cpu}")
            lines.extend(s.rstrip("\n") for s in traceback.format_stack(frame))
        if native_text:
            lines.append(native_text)
        _append("\n".join(lines) + "\n\n")
    except Exception:
        pass


def start(app) -> None:
    """Call once, on the main thread, after the QApplication exists."""
    global _started
    if _started:
        return
    _started = True
    from PySide6.QtCore import QTimer

    last_beat = [time.monotonic()]
    timer = QTimer(app)
    timer.timeout.connect(lambda: last_beat.__setitem__(0, time.monotonic()))
    timer.start(500)
    app._hang_watchdog_timer = timer      # keep it alive

    def watch():
        dumped_for_beat, dumps, last_dump = None, 0, 0.0
        while True:
            time.sleep(0.5)
            beat = last_beat[0]
            stalled = time.monotonic() - beat
            if stalled < STALL_S:
                if dumped_for_beat is not None and beat != dumped_for_beat:
                    # The stall that was dumped has ended: log how long it really lasted.
                    try:
                        _append(f"==== {datetime.now():%Y-%m-%d %H:%M:%S}  stall ended after "
                                f"{beat - dumped_for_beat:.1f} s ====\n\n")
                    except Exception:
                        pass
                    dumped_for_beat = None
                continue
            if beat != dumped_for_beat:
                dumped_for_beat, dumps, last_dump = beat, 0, 0.0
            if dumps < MAX_DUMPS_PER_STALL and time.monotonic() - last_dump >= REPEAT_S:
                _dump(stalled, native=(dumps == 0))
                dumps += 1
                last_dump = time.monotonic()

    threading.Thread(target=watch, name="hang-watchdog", daemon=True).start()
