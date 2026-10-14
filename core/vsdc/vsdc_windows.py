"""
core/vsdc/vsdc_windows.py - which browser windows the capture loop watches
===========================================================================
SGT never-miss blueprint section 4.7. Win32 only (window handle, title, process name): cheap,
nothing here can block on a browser, and nothing reads a page.

WP4 ships the foreground-only version: browser_windows() returns at most the window in front.
WP10 adds the visible windows that are not in front (owner answer D6), behind the same function.
BROWSER_EXE_NAMES is not changed (owner rejected D11).
"""

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from typing import List

MAX_WATCHED_WINDOWS = 3
BG_TICK_SEC = 1.0                # a window not in front is considered this often (WP10)


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str                   # the window title as Windows gives it (browser suffix included)
    proc_name: str               # the process exe, lower case ("msedge.exe")
    background: bool = False     # not the window in front


def window_info(hwnd: int) -> WindowInfo:
    """Title and process exe of a top-level window ("" for each when they cannot be read)."""
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    length = user32.GetWindowTextLengthW(hwnd)
    buff = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buff, length + 1)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    proc_name = ""
    hproc = kernel32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
    if hproc:
        name_buff = ctypes.create_unicode_buffer(512)
        size = wintypes.DWORD(512)
        if kernel32.QueryFullProcessImageNameW(hproc, 0, name_buff, ctypes.byref(size)):
            proc_name = name_buff.value.split("\\")[-1].lower()
        kernel32.CloseHandle(hproc)
    return WindowInfo(hwnd=hwnd, title=buff.value.strip(), proc_name=proc_name)


def is_watchable(info: WindowInfo) -> bool:
    """A browser window, or a window titled like a portal (the router's quick gate), with a title."""
    from .vsdc_router import BROWSER_EXE_NAMES, PORTAL_KEYWORDS
    if not info.hwnd or not info.title:
        return False
    title = info.title.lower()
    return any(b in info.proc_name for b in BROWSER_EXE_NAMES) or any(k in title for k in PORTAL_KEYWORDS)


def browser_windows(foreground: int) -> List[WindowInfo]:
    """The windows to watch this tick: for now only the one in front, when it is watchable."""
    if not foreground:
        return []
    try:
        info = window_info(foreground)
    except Exception:
        return []
    return [info] if is_watchable(info) else []
