"""
core/sca/uid_source_watch.py - read the text of a service's UID page, and hand it over
=======================================================================================
Settings -> SCA lists the registered services; each can have a UID page link (services.uid_page_link):
the page where staff look at a client's UID. A daemon thread with its own UIA object
(core/sca/page_nodes.UiaReader). Each tick:

  1. no service has a UID page, or SCA off  -> sleep (IDLE_S), no UIA at all;
  2. the window in front is not a browser    -> sleep (TICK_S);
  3. its address bar (a property read) is not one of the saved pages -> sleep;
  4. only then read the page and collect the text of its nodes;
  5. text that differs from the last read on this page -> on_texts(list of strings).

The watcher does NOT decide what a UID is: the caller (clipboard_watch) matches the strings against the
client id index. on_texts runs on THIS thread: hand it over (clipboard_watch emits a Qt signal). It only
READS: never clicks, types or focuses; a password box's value is never read (core/sca/page_nodes).
Failures are silent and cost one tick.
"""

import re
import threading
from typing import Any, Callable, Dict, List, Optional

from core.sdis import keys

TICK_S = 1.2
IDLE_S = 3.0
MIN_LEN, MAX_LEN = 3, 80
MAX_TEXTS = 600
_SPLIT = re.compile(r"[\s,;|:/()\[\]]+")


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


def page_texts(docs: List[List[Dict[str, Any]]]) -> List[str]:
    """Distinct strings of 3..80 characters a page shows: each node's name or value, and its words (a UID
    inside a longer label is still found). Order kept, capped at MAX_TEXTS."""
    seen: Dict[str, None] = {}
    for doc in docs:
        for n in doc:
            for raw in (n.get("value"), n.get("name")):
                text = " ".join(str(raw or "").split())
                if not text:
                    continue
                for piece in [text] + _SPLIT.split(text):
                    if MIN_LEN <= len(piece) <= MAX_LEN:
                        seen.setdefault(piece, None)
                if len(seen) >= MAX_TEXTS:
                    return list(seen)
    return list(seen)


class UidSourceWatcher:
    def __init__(self, pages: Callable[[], List[str]], active: Callable[[], bool],
                 on_texts: Callable[[List[str]], None], reader: Any = None,
                 foreground: Callable[[], int] = _foreground_browser) -> None:
        self._pages, self._active, self._on_texts = pages, active, on_texts
        self._reader, self._foreground = reader, foreground
        self._last: Dict[str, List[str]] = {}    # page link -> the texts last reported on it
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.stats = {"reads": 0, "reports": 0, "errors": 0}

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

    def tick(self) -> bool:
        """One look. True when a saved UID page was in front (so the next look comes sooner)."""
        pages = {keys.page_link(p) for p in (self._pages() or []) if p}
        pages.discard("")
        if not pages or not self._active():
            self._last.clear()
            return False
        hwnd = self._foreground()
        if not hwnd:
            return False
        if self._reader is None:
            from core.sca.page_nodes import UiaReader
            self._reader = UiaReader()
        link = keys.page_link(self._reader.read_url(hwnd))
        if link not in pages:
            self._last.clear()                   # leaving the page forgets it: coming back reports again
            return False
        self.stats["reads"] += 1
        texts = page_texts(self._reader.read_docs(hwnd))
        if texts != self._last.get(link):
            self._last[link] = texts
            self.stats["reports"] += 1
            if texts:
                self._on_texts(texts)
        return True
