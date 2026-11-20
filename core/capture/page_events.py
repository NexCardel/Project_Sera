"""
core/capture/page_events.py - "this page just changed", from Windows' accessibility events (UCW WP7)
===================================================================================================
Inside the capture worker. The capture loop asks for a window's URL every SCOPE_POLL_SEC; this makes
a page change start a URL read at once instead. It listens to two WinEvents of the watched browser
windows - the same read-only channel screen readers and core/os_assist's listener use:

  * EVENT_OBJECT_NAMECHANGE on the window itself (OBJID_WINDOW): the title changed - a new page;
  * EVENT_OBJECT_VALUECHANGE inside the window: the address bar's value changed (a single-page portal
    changes its route without a new title). Page fields fire it too while someone types; the event
    only starts a URL-only read, at most once per EVENT_MIN_GAP_SEC per window (core/capture/remote).

Strictly passive: a hook out of context (WINEVENT_OUTOFCONTEXT) on its own thread with a message loop;
the callback looks at the event's numbers only (window handle, object id) - no call into the browser,
no text. Polling stays as the safety net: a missed event costs at most one poll interval.
"""

import ctypes
import threading
from ctypes import wintypes
from typing import Callable, Iterable, Optional, Set

EVENT_OBJECT_NAMECHANGE = 0x800C
EVENT_OBJECT_VALUECHANGE = 0x800E
OBJID_WINDOW = 0
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002
GA_ROOT = 2
WM_QUIT = 0x0012
DEBOUNCE_SEC = 0.1           # the same window and kind again this soon is one event

_WinEventProc = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND, wintypes.LONG,
                                   wintypes.LONG, wintypes.DWORD, wintypes.DWORD)


def kind_of(event: int, hwnd: int, root: int, id_object: int) -> str:
    """'title' / 'value' / '' (not ours) for one WinEvent, from its numbers only."""
    if event == EVENT_OBJECT_NAMECHANGE and hwnd == root and id_object == OBJID_WINDOW:
        return "title"
    if event == EVENT_OBJECT_VALUECHANGE:
        return "value"
    return ""


class PageEvents:
    """sink(hwnd, kind) for every page change in a watched window, on the listener's own thread."""

    def __init__(self, sink: Callable[[int, str], None], diag: Optional[Callable[[str], None]] = None,
                 monotonic: Optional[Callable[[], float]] = None):
        import time
        self._sink = sink
        self._diag = diag or (lambda t: None)
        self._now = monotonic or time.monotonic
        self._watched: Set[int] = set()
        self._last: dict = {}
        self._lock = threading.Lock()
        self._thread_id = 0
        self._thread: Optional[threading.Thread] = None
        self._proc = _WinEventProc(self._callback)     # kept alive as long as the hook

    def watch(self, hwnds: Iterable[int]) -> None:
        with self._lock:
            self._watched = {int(h) for h in hwnds if h}
        if self._watched and self._thread is None:
            self._thread = threading.Thread(target=self._run, name="ucw-page-events", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)

    def _callback(self, _hook, event, hwnd, id_object, _id_child, _thread, _time) -> None:
        try:
            if not hwnd:
                return
            root = int(ctypes.windll.user32.GetAncestor(hwnd, GA_ROOT) or hwnd)
            with self._lock:
                if root not in self._watched:
                    return
            kind = kind_of(int(event), int(hwnd), root, int(id_object))
            if not kind:
                return
            now = self._now()
            key = (root, kind)
            if now - self._last.get(key, -1e9) < DEBOUNCE_SEC:
                return
            self._last[key] = now
            self._sink(root, kind)
        except Exception:
            pass

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        user32.SetWinEventHook.restype = wintypes.HANDLE
        hook = user32.SetWinEventHook(EVENT_OBJECT_NAMECHANGE, EVENT_OBJECT_VALUECHANGE, 0, self._proc, 0, 0,
                                      WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS)
        if not hook:
            self._diag("page-change events unavailable (SetWinEventHook failed): polling only")
            return
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            user32.UnhookWinEvent(hook)


class NullEvents:
    """No page-change events (tests, and a worker whose events are switched off)."""

    def __init__(self, sink: Callable[[int, str], None], diag: Optional[Callable[[str], None]] = None):
        self.sink = sink
        self.watched: Set[int] = set()

    def watch(self, hwnds: Iterable[int]) -> None:
        self.watched = set(hwnds)

    def stop(self) -> None:
        pass
