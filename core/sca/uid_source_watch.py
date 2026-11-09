"""
core/sca/uid_source_watch.py - read the picked UID node of a service's UID page, and hand the UID over
=====================================================================================================
docs/sca-uid-node-plan.md WP4. Settings -> SCA: each service can have a UID page (services.uid_page_link,
the login page that shows the password box with the UID already set) and the node on it that shows the
UID (services.uid_node, picked once with "Pick UID"; core/sca/uid_node). A daemon thread with its own UIA
object (core/sca/page_nodes.UiaReader). Each tick:

  1. no service has a UID page + node, or SCA off -> sleep (IDLE_S), no UIA at all;
  2. the window in front is not a browser          -> sleep;
  3. its address bar (a property read) is not one of the saved pages -> sleep;
  4. fast path: the node has an HTML id -> one provider-side search for it and the password boxes;
     full path (no id, or not exactly one hit): one page read, the node found by uid_node.find;
  5. no on-screen password box -> not the UID page yet (page 1 of a two-page login): nothing;
  6. the node not found / ambiguous -> on_warn(service_id, reason), once per page visit; never a guess;
     the node shows text but the rule finds no UID in it (for NO_UID_READS reads) -> on_warn "no_uid";
     a box is always read by its typed value (core/sca/uid_node.read_text), never its label;
  7. a UID in a BOX must read the same twice in a row (a one-page login's box is being TYPED into); a UID
     shown as text (page 2 of a two-page login, Google's password page) reports on the first read.
     Then on_uid(service_id, uid) - once per visit and UID; a new UID reports again.

Timing (no reliable "page changed" event exists: UIA events caught 4 of 13 live page changes): while a
browser is in front the address bar (a ~1 ms cached property read) is checked every BROWSER_S, so the
page is read at most BROWSER_S after it appears; IDLE_S only when no browser is in front. A Windows
foreground-window hook (EVENT_SYSTEM_FOREGROUND, its own thread) wakes the watcher at once when a window
comes to the front, so switching to the browser costs no wait at all.

Callbacks run on THIS thread: hand them over (clipboard_watch emits Qt signals). It only READS: never
clicks, types or focuses; a password box's value is never read. Failures are silent and cost one tick.
"""

import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.sca import sca_log, uid_node
from core.sdis import keys

TICK_S = 0.25         # while a saved UID page is in front
BROWSER_S = 0.25      # a browser in front on another page: an address-bar check only
IDLE_S = 3.0          # no browser in front, no UID page saved, or SCA off
STABLE_READS = 2      # a TYPED UID (a box); a text UID needs one read
MISSING_READS = 4     # about 1 s without the node before "missing" (a page still loading is not lost)
NO_UID_READS = 8      # about 2 s of the same text with no UID in it (typing pauses are shorter)
LEAVE_READS = 2       # the page must be missing this many reads in a row to count as left: one
                      # odd address-bar read used to end the visit, re-report the UID and re-arm
                      # (the SCA card slid out and in again: the 2026-11-09 flicker)

EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT = 0x0000
WM_QUIT = 0x0012


class ForegroundHook:
    """Calls on_change() (on the hook's own thread) whenever a window comes to the front. Windows only;
    any failure leaves it off - the watcher's polling still covers everything."""

    def __init__(self, on_change: Callable[[], None]) -> None:
        self._on_change = on_change
        self._thread: Optional[threading.Thread] = None
        self._tid = 0
        self._proc = None                        # keeps the ctypes callback alive

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="sca-uid-foreground", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        if self._tid:
            try:
                import ctypes
                ctypes.windll.user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            except Exception:
                pass

    def _run(self) -> None:
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            proto = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND, wintypes.LONG,
                                       wintypes.LONG, wintypes.DWORD, wintypes.DWORD)

            def cb(_hook, _event, _hwnd, _obj, _child, _thread, _time):
                try:
                    self._on_change()
                except Exception:
                    pass

            self._proc = proto(cb)
            user32.SetWinEventHook.restype = wintypes.HANDLE
            user32.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, proto,
                                               wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
            hook = user32.SetWinEventHook(EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND, None, self._proc,
                                          0, 0, WINEVENT_OUTOFCONTEXT)
            if not hook:
                return
            self._tid = ctypes.windll.kernel32.GetCurrentThreadId()
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:     # the hook needs a message loop
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            user32.UnhookWinEvent(hook)
        except Exception:
            pass


def _foreground_browser() -> int:
    """The window in front when it is a browser, else 0."""
    import ctypes
    from core.vsdc import vsdc_windows
    from core.vsdc.vsdc_router import BROWSER_EXE_NAMES
    hwnd = int(ctypes.windll.user32.GetForegroundWindow() or 0)
    if not hwnd:
        return 0
    info = vsdc_windows.window_info(hwnd)
    return hwnd if info.title and any(b in info.proc_name for b in BROWSER_EXE_NAMES) else 0


class UidSourceWatcher:
    def __init__(self, sources: Callable[[], List[Dict[str, Any]]], active: Callable[[], bool],
                 on_uid: Callable[[Any, str], None], on_warn: Callable[[Any, str], None] = lambda s, r: None,
                 reader: Any = None, foreground: Callable[[], int] = _foreground_browser) -> None:
        """sources() -> [{"id", "uid_page_link", "uid_node"}] (the services); only those with both count."""
        self._sources, self._active = sources, active
        self._on_uid, self._on_warn = on_uid, on_warn
        self._reader, self._foreground = reader, foreground
        self._visit: Optional[str] = None                     # page link in front, or None
        self._gone = 0                                        # reads in a row with the visit's page not seen
        self._seen: Dict[Any, Tuple[str, int]] = {}           # service -> (uid, reads in a row) this visit
        self._reported: Dict[Any, str] = {}                   # service -> uid reported this visit
        self._warned: set = set()                             # services warned this visit
        self._missing: Dict[Any, int] = {}                    # service -> full reads in a row without the node
        self._steps: Dict[Any, str] = {}                      # service -> last state logged (sca.log) this visit
        self._stop = threading.Event()
        self._wake = threading.Event()                        # stop, or a window came to the front
        self._thread: Optional[threading.Thread] = None
        self._hook: Optional[ForegroundHook] = None
        self.browser_in_front = False                         # set by tick(): picks the next wait
        self.stats = {"reads": 0, "fast": 0, "full": 0, "reports": 0, "warnings": 0, "errors": 0, "wakes": 0}

    def start(self, hook: bool = True) -> None:
        if self._thread is None:
            if hook:
                self._hook = ForegroundHook(self.wake)
                self._hook.start()
            self._thread = threading.Thread(target=self._run, name="sca-uid-source", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._hook is not None:
            self._hook.stop()

    def wake(self) -> None:
        """Look now instead of at the end of the current wait (a window came to the front)."""
        self.stats["wakes"] += 1
        self._wake.set()

    def next_wait(self, busy: bool) -> float:
        return TICK_S if busy else (BROWSER_S if self.browser_in_front else IDLE_S)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                busy = self.tick()
            except Exception:
                self.stats["errors"] += 1
                busy = False
            self._wake.wait(self.next_wait(busy))
            self._wake.clear()

    def _pages(self) -> Dict[str, List[Tuple[Any, Dict[str, Any]]]]:
        out: Dict[str, List[Tuple[Any, Dict[str, Any]]]] = {}
        for s in self._sources() or []:
            link = keys.page_link(str(s.get("uid_page_link") or "").strip())
            fp = uid_node.loads(s.get("uid_node"))
            if link and fp:
                out.setdefault(link, []).append((s.get("id"), fp))
        return out

    def _step(self, service_id: Any, state: str) -> None:
        """logs/sca.log, only when a service's state on this visit changes (reads come every 0.25 s)."""
        if self._steps.get(service_id) != state:
            self._steps[service_id] = state
            sca_log.note(f"watch service {service_id}: {state}")

    def _leave(self) -> None:
        if self._visit is not None:
            sca_log.note(f"watch: left UID page {self._visit}")
        self._steps.clear()
        self._visit = None
        self._gone = 0
        self._seen.clear()
        self._reported.clear()
        self._warned.clear()
        self._missing.clear()

    def tick(self) -> bool:
        """One look. True when a saved UID page was in front (so the next look comes sooner)."""
        pages = self._pages() if self._active() else {}
        if not pages:
            self._leave()
            return False
        hwnd = self._foreground()
        self.browser_in_front = bool(hwnd)
        if not hwnd:
            return False
        if self._reader is None:
            from core.sca.page_nodes import UiaReader
            self._reader = UiaReader()
        link = keys.page_link(self._reader.read_url(hwnd))
        if link not in pages:
            if self._visit is not None and self._gone + 1 < LEAVE_READS:
                self._gone += 1                 # one odd read is not leaving: the visit is kept
                return False
            self._leave()                       # leaving the page forgets it: coming back reports again
            return False
        self._gone = 0
        if link != self._visit:
            self._leave()
            self._visit = link
            sca_log.note(f"watch: UID page in front {link} (services {[sid for sid, _ in pages[link]]})")
        self.stats["reads"] += 1
        docs = None                             # one full read per tick at most, shared by the services
        for service_id, fp in pages[link]:
            hit, pw = None, 0
            if fp.get("id"):
                hit, pw = uid_node.fast_node(self._reader.find_fast(hwnd, fp["id"]), fp)
                if not pw:
                    hit = None                  # no FLAGGED password box: a full read finds an unflagged one
            if hit is not None:
                self.stats["fast"] += 1
            else:
                if docs is None:
                    docs = self._reader.read_docs(hwnd)
                    self.stats["full"] += 1
                pw = 1 if uid_node.has_password_box(docs) else 0
                if pw:
                    hit, why = uid_node.find(docs, fp)
                    if hit is None:
                        self._seen.pop(service_id, None)
                        n = self._missing[service_id] = self._missing.get(service_id, 0) + 1
                        self._step(service_id, f"picked node {why}" + (" (warned)" if n >= MISSING_READS else ""))
                        if n >= MISSING_READS:        # not a page still being built (reads come fast now)
                            self._warn(service_id, why)
                        continue
                    self._missing.pop(service_id, None)
            if not pw:
                self._seen.pop(service_id, None)      # not the UID page (yet): page 1 of a two-page login
                self._step(service_id, "no password box on the page (yet)")
                continue
            text = " ".join(uid_node.read_text(hit, fp).split())
            uid = uid_node.uid_of(hit, fp)
            self._step(service_id, (f"node found, UID of {len(uid)} characters" if uid else
                                    f"node found, no UID in its {len(text)} characters (rule {fp.get('rule')})")
                       + (" [fast]" if fp.get("id") and docs is None else " [full read]"))
            self._settle(service_id, uid, text, STABLE_READS if uid_node.is_box(hit) else 1)
        return True

    def _warn(self, service_id: Any, why: str) -> None:
        self._seen.pop(service_id, None)
        if service_id in self._warned:
            return
        self._warned.add(service_id)
        self.stats["warnings"] += 1
        self._on_warn(service_id, why)

    def _settle(self, service_id: Any, uid: str, text: str = "", need: int = STABLE_READS) -> None:
        """uid: the cleaned UID ('' = none); text: what the node shows. The UID must read the same `need`
        times in a row (STABLE_READS for a typed box, 1 for text). A node that shows text with no UID in it for NO_UID_READS reads (not an empty box,
        not a UID still being typed) warns "no_uid" once per visit: never silent."""
        last, count = self._seen.get(service_id, ("", 0))
        cur = uid or ("text:" + text)
        count = count + 1 if cur == last else 1
        self._seen[service_id] = (cur, count)
        if uid and count >= need and self._reported.get(service_id) != uid:
            sca_log.note(f"watch service {service_id}: report UID of {len(uid)} characters"
                         + (" (a DIFFERENT UID on the same visit)" if service_id in self._reported else ""))
            self._reported[service_id] = uid
            self.stats["reports"] += 1
            self._on_uid(service_id, uid)
        elif not uid and text and count >= NO_UID_READS:
            self._warn(service_id, "no_uid")
