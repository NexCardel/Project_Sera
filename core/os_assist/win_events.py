"""
OS Assist WinEvent hub: Windows' own window events (SetWinEventHook, out of context) so the cards
react at once instead of waiting for their next poll (responsiveness pass, 2026-11-09).

These are window-manager events (move, minimise, title, foreground, focus, value), not the
browser's UIA events: the snapshot-on-change test saw UIA events catch only 4 of 13 page changes,
while these come from the window manager and MSAA and are dependable. Every user of the hub keeps
its poll as a slower safety net, so a missed event only costs the old delay.

One daemon thread owns every hook and runs the message loop they need. `watch()` may be called from
any thread; the callback runs on the hub thread and must be quick (set a flag, emit a Qt signal).
Read-only: nothing here touches a page or a window.
"""

import ctypes
import itertools
import queue
import sys
import threading
from ctypes import wintypes
from typing import Callable, Dict, Iterable, List, Optional, Tuple

EVENT_SYSTEM_FOREGROUND = 0x0003
EVENT_SYSTEM_MINIMIZESTART = 0x0016
EVENT_SYSTEM_MINIMIZEEND = 0x0017
EVENT_OBJECT_DESTROY = 0x8001
EVENT_OBJECT_FOCUS = 0x8005
EVENT_OBJECT_LOCATIONCHANGE = 0x800B
EVENT_OBJECT_NAMECHANGE = 0x800C
EVENT_OBJECT_VALUECHANGE = 0x800E

OBJID_WINDOW = 0
CHILDID_SELF = 0

WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002
WM_APP = 0x8000
WM_QUIT = 0x0012
GA_ROOT = 2

# cb(event, hwnd, id_object, id_child)
Callback = Callable[[int, int, int, int], None]
Range = Tuple[int, int]

_PROTO = (ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND, wintypes.LONG,
                             wintypes.LONG, wintypes.DWORD, wintypes.DWORD)
          if sys.platform == "win32" else None)


def window_pid(hwnd: int) -> int:
    """The process id that owns the window; 0 when unknown (or not on Windows)."""
    if sys.platform != "win32" or not hwnd:
        return 0
    try:
        u = ctypes.windll.user32
        if not u.IsWindow(wintypes.HWND(hwnd)):
            return 0
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
        return int(pid.value)
    except Exception:
        return 0


def root_of(hwnd: int) -> int:
    """The top-level window of `hwnd` (itself when it is one)."""
    if sys.platform != "win32" or not hwnd:
        return int(hwnd or 0)
    try:
        return int(ctypes.windll.user32.GetAncestor(wintypes.HWND(hwnd), GA_ROOT) or hwnd)
    except Exception:
        return int(hwnd)


def is_window_event(id_object: int, id_child: int) -> bool:
    """The event is about the window itself, not an object inside it (a caret, a page node)."""
    return id_object == OBJID_WINDOW and id_child == CHILDID_SELF


class Hub:
    """The hook thread. `watch(ranges, pid, cb)` -> token or None (no hooks possible here)."""

    def __init__(self) -> None:
        self._ops: "queue.Queue[tuple]" = queue.Queue()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._tid = 0
        self._ready = threading.Event()
        self._seq = itertools.count(1)
        self._subs: Dict[int, List[int]] = {}        # token -> hook handles (hub thread only)
        self._by_hook: Dict[int, Callback] = {}      # hook handle -> callback (hub thread only)
        self._proc = None
        self.available = sys.platform == "win32"

    # -- any thread ---------------------------------------------------------------------------
    def watch(self, ranges: Iterable[Range], pid: int, cb: Callback) -> Optional[int]:
        """Hook each (event_min, event_max) range for one process (pid 0 = every process).
        Returns a token for unwatch(), or None when hooks are not available."""
        if not self.available or not self._start():
            return None
        token = next(self._seq)
        done = threading.Event()
        result: list = []
        self._post(("watch", token, list(ranges), int(pid or 0), cb, done, result))
        done.wait(2.0)
        return token if result and result[0] else None

    def unwatch(self, token: Optional[int]) -> None:
        if token is None or not self.available:
            return
        with self._lock:
            alive = self._thread is not None and self._thread.is_alive()
        if alive:
            self._post(("unwatch", token))

    def stop(self) -> None:
        with self._lock:
            thread, tid = self._thread, self._tid
            self._thread = None
        if thread is not None and tid:
            try:
                ctypes.windll.user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)
            except Exception:
                pass
            thread.join(timeout=2.0)

    def _start(self) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self._tid != 0
            self._ready.clear()
            self._tid = 0
            self._thread = threading.Thread(target=self._run, name="os-assist-winevents", daemon=True)
            self._thread.start()
        self._ready.wait(2.0)
        return self._tid != 0

    def _post(self, op: tuple) -> None:
        self._ops.put(op)
        try:
            ctypes.windll.user32.PostThreadMessageW(self._tid, WM_APP, 0, 0)
        except Exception:
            pass

    # -- hub thread ----------------------------------------------------------------------------
    def _run(self) -> None:
        try:
            u = ctypes.windll.user32
            u.SetWinEventHook.restype = wintypes.HANDLE
            u.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, _PROTO,
                                          wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
            u.UnhookWinEvent.argtypes = [wintypes.HANDLE]
            self._proc = _PROTO(self._dispatch)
            msg = wintypes.MSG()
            u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)          # creates this thread's queue
            self._tid = int(ctypes.windll.kernel32.GetCurrentThreadId())
        except Exception:
            self._ready.set()
            return
        self._ready.set()
        try:
            while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if not msg.hWnd and msg.message == WM_APP:
                    self._drain(u)
                    continue
                u.TranslateMessage(ctypes.byref(msg))
                u.DispatchMessageW(ctypes.byref(msg))
        finally:
            for hooks in self._subs.values():
                for h in hooks:
                    u.UnhookWinEvent(h)
            self._subs.clear()
            self._by_hook.clear()

    def _drain(self, u) -> None:
        while True:
            try:
                op = self._ops.get_nowait()
            except queue.Empty:
                return
            if op[0] == "watch":
                _, token, ranges, pid, cb, done, result = op
                hooks = []
                for lo, hi in ranges:
                    h = u.SetWinEventHook(lo, hi, None, self._proc, pid, 0,
                                          WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS)
                    if h:
                        hooks.append(int(h))
                        self._by_hook[int(h)] = cb
                self._subs[token] = hooks
                result.append(bool(hooks))
                done.set()
            elif op[0] == "unwatch":
                for h in self._subs.pop(op[1], []):
                    self._by_hook.pop(h, None)
                    u.UnhookWinEvent(h)

    def _dispatch(self, hook, event, hwnd, id_object, id_child, _thread, _time) -> None:
        cb = self._by_hook.get(int(hook or 0))
        if cb is None:
            return
        try:
            cb(int(event), int(hwnd or 0), int(id_object), int(id_child))
        except Exception:
            pass


_default: Optional[Hub] = None
_default_lock = threading.Lock()


def default() -> Hub:
    global _default
    with _default_lock:
        if _default is None:
            _default = Hub()
        return _default


# ----------------------------------------------------------------------------- browser window watch
class WindowWatch:
    """The events of one browser window that the cards care about, as plain calls on the hub thread:
    on_move (moved / resized / minimised / restored / closed), on_title (tab switch, navigation),
    on_focus (a box inside the window took focus). `active` says whether the hooks went in."""

    def __init__(self, hwnd: int, on_move: Callable[[], None] = None, on_title: Callable[[], None] = None,
                 on_focus: Callable[[], None] = None, hub: Optional[Hub] = None) -> None:
        self.hwnd = int(hwnd or 0)
        self._hub = hub if hub is not None else default()
        self._on_move, self._on_title, self._on_focus = on_move, on_title, on_focus
        self._token = None
        pid = window_pid(self.hwnd)
        if not pid:
            return
        ranges: List[Range] = [(EVENT_SYSTEM_MINIMIZESTART, EVENT_SYSTEM_MINIMIZEEND),
                               (EVENT_OBJECT_DESTROY, EVENT_OBJECT_DESTROY),
                               (EVENT_OBJECT_LOCATIONCHANGE, EVENT_OBJECT_NAMECHANGE)]
        if on_focus is not None:
            ranges.append((EVENT_OBJECT_FOCUS, EVENT_OBJECT_FOCUS))
        self._token = self._hub.watch(ranges, pid, self._event)

    @property
    def active(self) -> bool:
        return self._token is not None

    def close(self) -> None:
        token, self._token = self._token, None
        self._hub.unwatch(token)

    def _event(self, event: int, hwnd: int, id_object: int, id_child: int) -> None:
        if event == EVENT_OBJECT_FOCUS:
            if self._on_focus is not None and hwnd and (hwnd == self.hwnd or root_of(hwnd) == self.hwnd):
                self._on_focus()
            return
        if hwnd != self.hwnd or not is_window_event(id_object, id_child):
            return
        if event == EVENT_OBJECT_NAMECHANGE:
            if self._on_title is not None:
                self._on_title()
            return
        if self._on_move is not None:
            self._on_move()
