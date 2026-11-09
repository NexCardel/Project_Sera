"""
startup_boost.py
----------------
Boot-time behaviour for a launch made by the logon task / Run entry (the --startup argument).

  * raise the process priority while Windows is still loading everything else, drop it back later
  * claim a per-session "Sera is running" mutex, so a logon launch never opens a second copy
  * retry a flaky step (database locked by a virus scan, ...) instead of giving up at once
"""

from __future__ import annotations

import sys
import time
from typing import Callable, TypeVar

STARTUP_ARG = "--startup"
BOOST_SECONDS = 90
_ABOVE_NORMAL = 0x00008000
_NORMAL = 0x00000020
_ERROR_ALREADY_EXISTS = 183
_MUTEX_NAME = "Local\\AmasSeraRunning"

_mutex_handle = None   # kept for the life of the process; closing it would release the claim

T = TypeVar("T")


def launched_at_startup(argv=None) -> bool:
    return STARTUP_ARG in (sys.argv if argv is None else argv)


def _set_priority(priority_class: int) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        return bool(kernel32.SetPriorityClass(ctypes.c_void_p(kernel32.GetCurrentProcess()), priority_class))
    except Exception:
        return False


def raise_priority() -> bool:
    return _set_priority(_ABOVE_NORMAL)


def restore_priority() -> bool:
    return _set_priority(_NORMAL)


def claim_instance() -> bool:
    """True if this is the first Sera in this Windows session, False if one already runs."""
    global _mutex_handle
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        already = ctypes.get_last_error() == _ERROR_ALREADY_EXISTS
        if handle:
            _mutex_handle = handle
        return not already
    except Exception:
        return True


def retry(step: Callable[[], T], attempts: int = 4, delay: float = 3.0,
          pump: Callable[[], None] | None = None) -> T:
    """Run `step`; on an exception wait `delay` s and try again, up to `attempts` times in all.
    `pump` is called about every 100 ms while waiting (keeps the loading dialog alive)."""
    for attempt in range(1, attempts + 1):
        try:
            return step()
        except Exception:
            if attempt == attempts:
                raise
            end = time.monotonic() + delay
            while time.monotonic() < end:
                if pump:
                    pump()
                time.sleep(0.1)
    raise RuntimeError("unreachable")
