"""
The reader's Backend (core/vsdc/vsdc_reader.Backend) over the scripted browser, on a virtual clock.

No thread is ever started: a reader job runs at once, inline, on the harness's thread, at the virtual
time it starts. What it costs in browser time is added to the clock's `offset` by the backend calls
(a read of a 2 s page spends 2 s), the page is looked up at the middle of the read, and the job's future
completes at start + cost through the clock's event list. So a 5 s read is 5 s of virtual time in which
the capture loop keeps ticking, and nothing depends on the machine's speed.
"""
import heapq
import itertools
from collections import Counter
from concurrent.futures import Future
from typing import Any, Callable, List, Optional, Tuple

from .fake_browser import Browser, Tab

T0 = 1_000_000.0                  # the run's first instant; the app's own clock never sees 0
CT_TEXT = 50020
ACCESSIBILITY_STUB = ["Enable accessibility"]
COST = {"root": 0.001, "tab": 0.002, "documents": 0.004, "bar": 0.003, "document_url": 0.001}


class VirtualClock:
    def __init__(self) -> None:
        self.t = T0
        self.offset = 0.0
        self._events: List[Tuple[float, int, Callable[[], None]]] = []
        self._n = itertools.count()

    def now(self) -> float:
        return self.t + self.offset

    def rel(self) -> float:
        return self.t + self.offset - T0

    def spend(self, sec: float) -> None:
        self.offset += sec

    def schedule(self, at: float, fn: Callable[[], None]) -> None:
        heapq.heappush(self._events, (at, next(self._n), fn))

    def advance_to(self, t: float) -> None:
        """Run every event due by t in time order (a finished read's callback may start the next read)."""
        while self._events and self._events[0][0] <= t:
            at, _n, fn = heapq.heappop(self._events)
            self.t = max(self.t, at)
            fn()
        self.t = max(self.t, t)


class SimThread:
    """Stands in for vsdc_reader._OneThread: runs the job now, completes its Future later."""

    def __init__(self, clock: VirtualClock) -> None:
        self._clock = clock

    def submit(self, fn: Callable, *args) -> Future:
        vc = self._clock
        f: Future = Future()
        vc.offset = 0.0
        try:
            result, error = fn(*args), None
        except BaseException as e:      # noqa: BLE001 - delivered as the future's error, like the real thread
            result, error = None, e
        spent, vc.offset = vc.offset, 0.0
        if spent == float("inf"):
            return f                    # a wedged read: the future never completes

        def finish() -> None:
            f.set_running_or_notify_cancel()
            if error is not None:
                f.set_exception(error)
            else:
                f.set_result(result)

        vc.schedule(vc.t + spent, finish)
        return f

    def shutdown(self) -> None:
        pass


class FakeBackend:
    def __init__(self, browser: Browser, clock: VirtualClock) -> None:
        self._b, self._vc = browser, clock
        self.reads = 0
        self.calls: Counter = Counter()
        self.scope_times: List[float] = []        # virtual seconds at which a Document list was asked for
        self.spans: List[Tuple[int, float, float]] = []   # (browser pid, start, end) of every page read (R9)

    def init_thread(self) -> None:
        pass

    def process_id(self, hwnd: int) -> int:
        w = self._b.window_of(hwnd)
        return w.pid if w else 0

    def root(self, hwnd: int) -> Any:
        self._vc.spend(COST["root"])
        w = self._b.window_of(hwnd)
        return hwnd if w is not None and w.alive_at(self._vc.rel()) else None

    def tab_id(self, hwnd: int, root: Any) -> Any:
        self._vc.spend(COST["tab"])
        self.calls["tab_id"] += 1
        w = self._b.window_of(hwnd)
        return w.selected_at(self._vc.rel()).id if w is not None else None

    def documents(self, root: Any) -> List[Tuple[Any, str, bool]]:
        self._vc.spend(COST["documents"])
        t = self._vc.rel()
        self.calls["documents"] += 1
        self.scope_times.append(t)
        w = self._b.window_of(root)
        if w is None:
            return []
        shown = w.selected_at(t)
        out = []
        for tab in w.tabs:
            page = tab.page_at(t)
            if page is not None:
                out.append((tab, page.url, tab is not shown))
        return out

    def address_bar(self, hwnd: int, root: Any) -> str:
        self._vc.spend(COST["bar"])
        self.calls["address_bar"] += 1
        w = self._b.window_of(hwnd)
        if w is not None and w.bar_text_at(self._vc.rel()) is not None:
            return w.bar_text_at(self._vc.rel())
        page = w.page_at(self._vc.rel()) if w is not None else None
        return page.url if page is not None else ""

    def read_document(self, element: Tab) -> List[dict]:
        """The page as it is in the middle of the read; the whole read's time is spent here."""
        self.reads += 1
        t = self._vc.rel()
        page = element.page_at(t)
        if page is None:
            return []
        if page.wedge:
            self._vc.spend(float("inf"))
            return []
        mid = t + page.read_ms / 2000.0
        seen = element.page_at(mid) or page
        lines = list(seen.lines) + [ln for x in element.toasts_at(mid) for ln in x.lines]
        seen.reads += 1
        if seen.blind:
            lines = list(ACCESSIBILITY_STUB)
        elif seen.partial_first_read and seen.reads == 1:
            lines = lines[:2]
        self._vc.spend(page.read_ms / 1000.0)
        w = self._b.window_of_tab(element)
        self.spans.append((w.pid if w else 0, t, t + page.read_ms / 1000.0))
        return seen.nodes or [{"parent": -1, "depth": 0, "ctype": CT_TEXT, "name": ln} for ln in lines]

    def document_url(self, element: Tab) -> Optional[str]:
        """A1: cheap post-read URL check. Spends a small virtual cost like a live property read."""
        self._vc.spend(COST["document_url"])
        self.calls["document_url"] += 1
        t = self._vc.rel()
        page = element.page_at(t)
        return page.url if page is not None else ""
