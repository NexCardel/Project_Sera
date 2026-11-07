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
  7. the cleaned UID must read the same twice in a row (a one-page login's box is being TYPED into),
     then on_uid(service_id, uid) - once per visit and UID; a new UID reports again.

Callbacks run on THIS thread: hand them over (clipboard_watch emits Qt signals). It only READS: never
clicks, types or focuses; a password box's value is never read. Failures are silent and cost one tick.
"""

import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.sca import uid_node
from core.sdis import keys

TICK_S = 0.4          # while a saved UID page is in front
IDLE_S = 3.0
STABLE_READS = 2


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
        self._seen: Dict[Any, Tuple[str, int]] = {}           # service -> (uid, reads in a row) this visit
        self._reported: Dict[Any, str] = {}                   # service -> uid reported this visit
        self._warned: set = set()                             # services warned this visit
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.stats = {"reads": 0, "fast": 0, "full": 0, "reports": 0, "warnings": 0, "errors": 0}

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="sca-uid-source", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                busy = self.tick()
            except Exception:
                self.stats["errors"] += 1
                busy = False
            self._stop.wait(TICK_S if busy else IDLE_S)

    def _pages(self) -> Dict[str, List[Tuple[Any, Dict[str, Any]]]]:
        out: Dict[str, List[Tuple[Any, Dict[str, Any]]]] = {}
        for s in self._sources() or []:
            link = keys.page_link(str(s.get("uid_page_link") or "").strip())
            fp = uid_node.loads(s.get("uid_node"))
            if link and fp:
                out.setdefault(link, []).append((s.get("id"), fp))
        return out

    def _leave(self) -> None:
        self._visit = None
        self._seen.clear()
        self._reported.clear()
        self._warned.clear()

    def tick(self) -> bool:
        """One look. True when a saved UID page was in front (so the next look comes sooner)."""
        pages = self._pages() if self._active() else {}
        if not pages:
            self._leave()
            return False
        hwnd = self._foreground()
        if not hwnd:
            return False
        if self._reader is None:
            from core.sca.page_nodes import UiaReader
            self._reader = UiaReader()
        link = keys.page_link(self._reader.read_url(hwnd))
        if link not in pages:
            self._leave()                       # leaving the page forgets it: coming back reports again
            return False
        if link != self._visit:
            self._leave()
            self._visit = link
        self.stats["reads"] += 1
        docs = None                             # one full read per tick at most, shared by the services
        for service_id, fp in pages[link]:
            uid, pw = None, 0
            if fp.get("id"):
                uid, pw = uid_node.fast_uid(self._reader.find_fast(hwnd, fp["id"]), fp)
            if uid is not None:
                self.stats["fast"] += 1
            else:
                if docs is None:
                    docs = self._reader.read_docs(hwnd)
                    self.stats["full"] += 1
                pw = 1 if uid_node.has_password_box(docs) else 0
                if pw:
                    node, why = uid_node.find(docs, fp)
                    if node is None:
                        self._warn(service_id, why)
                        continue
                    uid = uid_node.uid_of(node, fp)
            if not pw:
                self._seen.pop(service_id, None)      # not the UID page (yet): page 1 of a two-page login
                continue
            self._settle(service_id, uid or "")
        return True

    def _warn(self, service_id: Any, why: str) -> None:
        self._seen.pop(service_id, None)
        if service_id in self._warned:
            return
        self._warned.add(service_id)
        self.stats["warnings"] += 1
        self._on_warn(service_id, why)

    def _settle(self, service_id: Any, uid: str) -> None:
        last, count = self._seen.get(service_id, ("", 0))
        count = count + 1 if uid == last else 1
        self._seen[service_id] = (uid, count)
        if uid and count >= STABLE_READS and self._reported.get(service_id) != uid:
            self._reported[service_id] = uid
            self.stats["reports"] += 1
            self._on_uid(service_id, uid)
