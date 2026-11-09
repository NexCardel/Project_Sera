"""
core/vsdc/vsdc_windows.py - which browser windows the capture loop watches
===========================================================================
SGT never-miss blueprint section 4.7 and WP10 (owner answer D6). Win32 only (window handle,
rectangle, title, process name): cheap, nothing here can block on a browser, and nothing reads a page.

The window in front is watched, as before. Besides it, up to MAX_WATCHED_WINDOWS - 1 browser windows
that are on screen but not in front (a second monitor, side by side, behind a signing dialog): the loop
looks at those every BG_TICK_SEC. A window is left out when it is minimised, on another virtual desktop
(cloaked), off every monitor, hung, or covered - its rectangle entirely hidden by windows above it in
z-order (Chromium builds no tree for a covered window anyway, measured 2026-10-04).
BROWSER_EXE_NAMES is not changed (owner rejected D11).
"""

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

MAX_WATCHED_WINDOWS = 3
BG_TICK_SEC = 1.0                # a window not in front is considered this often
COVER_SLACK_PX = 2               # a strip of a window this thin left uncovered still counts as covered

_TITLE_CHARS = 512
_WS_EX_TRANSPARENT = 0x20        # click-through overlays hide nothing
_WS_EX_TOOLWINDOW = 0x80         # floating tool palettes and the like
_DWMWA_CLOAKED = 14
_DWMWA_EXTENDED_FRAME_BOUNDS = 9
_MONITOR_DEFAULTTONULL = 0

Rect = Tuple[int, int, int, int]     # left, top, right, bottom


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str                   # the window title as Windows gives it (browser suffix included)
    proc_name: str               # the process exe, lower case ("msedge.exe")
    background: bool = False     # not the window in front


@dataclass(frozen=True)
class TopWindow:
    """One shown top-level window, as EnumWindows lists them (top of the z-order first)."""
    hwnd: int
    rect: Rect
    title: str = ""
    proc_name: str = ""
    covers: bool = True          # it hides what lies under it (not a click-through overlay or tool palette)
    watchable: bool = True       # may itself be watched when not in front (not hung, on a monitor)


def _title(user32, hwnd: int) -> str:
    # GetWindowTextW reads another process's caption without sending it a message, so a hung window cannot
    # block here (GetWindowTextLengthW would send WM_GETTEXTLENGTH).
    buff = ctypes.create_unicode_buffer(_TITLE_CHARS)
    user32.GetWindowTextW(hwnd, buff, _TITLE_CHARS)
    return buff.value.strip()


_exe_of_pid: Dict[int, str] = {}


def _proc_name(hwnd: int) -> str:
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    name = _exe_of_pid.get(pid.value)
    if name is not None:
        return name
    name = ""
    hproc = kernel32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
    if hproc:
        name_buff = ctypes.create_unicode_buffer(512)
        size = wintypes.DWORD(512)
        if kernel32.QueryFullProcessImageNameW(hproc, 0, name_buff, ctypes.byref(size)):
            name = name_buff.value.split("\\")[-1].lower()
        kernel32.CloseHandle(hproc)
    if name:
        if len(_exe_of_pid) >= 256:                         # process ids are reused: keep the cache short-lived
            _exe_of_pid.clear()
        _exe_of_pid[pid.value] = name
    return name


def window_info(hwnd: int) -> WindowInfo:
    """Title and process exe of a top-level window ("" for each when they cannot be read)."""
    return WindowInfo(hwnd=hwnd, title=_title(ctypes.windll.user32, hwnd), proc_name=_proc_name(hwnd))


def is_watchable(info: WindowInfo) -> bool:
    """A browser window, or a window titled like a portal (the router's quick gate), with a title."""
    from .vsdc_router import BROWSER_EXE_NAMES, PORTAL_KEYWORDS
    if not info.hwnd or not info.title:
        return False
    title = info.title.lower()
    return any(b in info.proc_name for b in BROWSER_EXE_NAMES) or any(k in title for k in PORTAL_KEYWORDS)


def _minus(rect: Rect, cut: Rect) -> List[Rect]:
    """What is left of `rect` once `cut` is taken out (0 to 4 rectangles)."""
    l, t, r, b = rect
    cl, ct, cr, cb = max(l, cut[0]), max(t, cut[1]), min(r, cut[2]), min(b, cut[3])
    if cl >= cr or ct >= cb:
        return [rect]
    out = []
    if t < ct:
        out.append((l, t, r, ct))
    if cb < b:
        out.append((l, cb, r, b))
    if l < cl:
        out.append((l, ct, cl, cb))
    if cr < r:
        out.append((cr, ct, r, cb))
    return out


def is_covered(rect: Rect, above: Sequence[Rect]) -> bool:
    """Every part of `rect` (but strips under COVER_SLACK_PX) lies under one of the rectangles above it."""
    left = [rect]
    for cut in above:
        left = [p for piece in left for p in _minus(piece, cut)]
        left = [p for p in left if p[2] - p[0] > COVER_SLACK_PX and p[3] - p[1] > COVER_SLACK_PX]
        if not left:
            return True
    return False


def pick_windows(foreground: int, tops: Sequence[TopWindow]) -> List[WindowInfo]:
    """The windows to watch: the window in front first (when watchable), then the browser windows behind it
    that are not covered - those titled like a portal first, then by z-order - up to MAX_WATCHED_WINDOWS."""
    from .vsdc_router import PORTAL_KEYWORDS
    front: List[WindowInfo] = []
    behind: List[WindowInfo] = []
    above: List[Rect] = []
    for top in tops:
        if top.hwnd == foreground:
            info = WindowInfo(hwnd=top.hwnd, title=top.title, proc_name=top.proc_name)
            if is_watchable(info):
                front.append(info)
        elif top.watchable and top.rect[2] > top.rect[0] and top.rect[3] > top.rect[1] \
                and not is_covered(top.rect, above):
            info = WindowInfo(hwnd=top.hwnd, title=top.title, proc_name=top.proc_name, background=True)
            if is_watchable(info):
                behind.append(info)
        if top.covers:
            above.append(top.rect)
    behind.sort(key=lambda i: not any(k in i.title.lower() for k in PORTAL_KEYWORDS))
    return (front + behind)[:MAX_WATCHED_WINDOWS]


def _rect(user32, dwmapi, hwnd: int) -> Rect:
    """The window as drawn (without Windows 10's invisible resize borders), else GetWindowRect."""
    rc = wintypes.RECT()
    if dwmapi is None or dwmapi.DwmGetWindowAttribute(hwnd, _DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rc),
                                                      ctypes.sizeof(rc)) != 0:
        user32.GetWindowRect(hwnd, ctypes.byref(rc))
    return rc.left, rc.top, rc.right, rc.bottom


def window_rect(hwnd: int) -> Rect:
    """The whole window as drawn on screen, for callers that need the browser frame too
    (evidence screenshots include the title bar, tabs and address bar, not just the page)."""
    user32 = ctypes.windll.user32
    try:
        dwmapi = ctypes.windll.dwmapi
    except OSError:
        dwmapi = None
    return _rect(user32, dwmapi, int(hwnd))


def _cloaked(dwmapi, hwnd: int) -> bool:
    if dwmapi is None:
        return False
    val = wintypes.DWORD(0)
    if dwmapi.DwmGetWindowAttribute(hwnd, _DWMWA_CLOAKED, ctypes.byref(val), ctypes.sizeof(val)) != 0:
        return False
    return bool(val.value)


_EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def top_windows(foreground: int) -> List[TopWindow]:
    """Shown top-level windows, top of the z-order first: visible, not minimised, not cloaked. Process
    names are looked up only for titled windows; the foreground is always listed when it is shown."""
    user32 = ctypes.windll.user32
    try:
        dwmapi = ctypes.windll.dwmapi
    except OSError:
        dwmapi = None
    order: List[int] = []

    def _each(hwnd, _lparam):
        order.append(int(hwnd or 0))
        return True

    user32.EnumWindows(_EnumProc(_each), 0)
    gwl_exstyle = -20
    out: List[TopWindow] = []
    for hwnd in order:
        if not hwnd or not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd) or _cloaked(dwmapi, hwnd):
            continue
        ex = user32.GetWindowLongW(hwnd, gwl_exstyle) & 0xFFFFFFFF
        title = _title(user32, hwnd)
        watchable = bool(title) and (hwnd == foreground or (
            not user32.IsHungAppWindow(hwnd)
            and user32.MonitorFromWindow(hwnd, _MONITOR_DEFAULTTONULL)))
        out.append(TopWindow(hwnd=hwnd, rect=_rect(user32, dwmapi, hwnd), title=title,
                             proc_name=_proc_name(hwnd) if title else "",
                             covers=not (ex & (_WS_EX_TRANSPARENT | _WS_EX_TOOLWINDOW)), watchable=watchable))
    return out


def browser_windows(foreground: int) -> List[WindowInfo]:
    """The windows to watch this tick (section 4.7): the one in front first, then visible ones not in
    front. Never raises; on any Win32 trouble it falls back to the window in front alone."""
    try:
        tops = top_windows(foreground)
        if foreground and not any(t.hwnd == foreground for t in tops):
            info = window_info(foreground)                  # in front but not listed (it just appeared)
            tops = [TopWindow(hwnd=foreground, rect=(0, 0, 0, 0), title=info.title, proc_name=info.proc_name,
                              covers=False)] + tops
        return pick_windows(foreground, tops)
    except Exception:
        pass
    if not foreground:
        return []
    try:
        info = window_info(foreground)
    except Exception:
        return []
    return [info] if is_watchable(info) else []
