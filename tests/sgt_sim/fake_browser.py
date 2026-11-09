"""
A scripted browser for the SGT simulation (never-miss blueprint section 8).

Everything is a function of time: a tab's pages, its toasts and typing, a window's selected tab, the
window in front. A profile writes the whole day down first (navigate(t, page), toast(t, ...),
focus(t, window)); the fake backend and the fake platform then look the state up at whatever virtual
time the capture loop or a reader job asks, including a moment in the middle of a slow read.
Times are seconds from the start of the run. All data is fictional.
"""
import bisect
import itertools
from dataclasses import dataclass, field
from typing import Dict, List, Optional

PORTAL_ITR = "itr"
PORTAL_GST = "gst"
OUTSIDE = ""                      # a page outside both portals
FULL = (0, 0, 1920, 1040)         # a maximised window on the first monitor (every window's default)
OTHER_APP = 0                     # focus(t, None): another program is in front (Excel, Tally, a signing dialog)
ACTIVE_BEFORE_SEC = 8.0           # the harness runs at the capture loop's own pace this long around a change
ACTIVE_AFTER_SEC = 3.0


@dataclass(eq=False)
class Page:
    url: str
    lines: List[str] = field(default_factory=list)
    portal: str = PORTAL_ITR
    title: str = ""
    read_ms: float = 40.0             # how long a UIA read of this page takes
    wedge: bool = False               # the read never returns
    blind: bool = False               # UIA sees only the accessibility stub, OCR sees the page
    partial_first_read: bool = False  # the first read of this page comes back with two lines
    nodes: Optional[list] = None      # raw UIA nodes, instead of one text node per line
    reads: int = 0
    _shade: int = field(default=0, repr=False)

    _made = itertools.count()

    def __post_init__(self) -> None:
        self._shade = 24 + 40 * (next(Page._made) % 6)   # pages made one after another differ by 2+ gate levels

    def window_title(self) -> str:
        if self.title:
            return self.title
        if self.portal == PORTAL_GST:
            return "GST Portal - Google Chrome"
        if self.portal == PORTAL_ITR:
            return "Income Tax - Google Chrome"
        return "New Tab - Google Chrome"

    def shade(self) -> int:
        return self._shade


@dataclass(eq=False)
class Toast:
    lines: List[str]
    lifetime_sec: float = 3.0
    cells: int = 12                   # how many 10 x 10 cells of the frame it paints


class Tab:
    _ids = itertools.count(1)

    def __init__(self) -> None:
        self.id = f"tab-{next(Tab._ids)}"
        self._nav_t: List[float] = []
        self._nav_p: List[Page] = []
        self._toasts: List[tuple] = []
        self._typing: List[tuple] = []
        self.marks: List[float] = []

    def navigate(self, t: float, page: Page) -> Page:
        assert not self._nav_t or t >= self._nav_t[-1], "navigations are written in time order"
        self._nav_t.append(t)
        self._nav_p.append(page)
        self.marks.append(t)
        return page

    def toast(self, t: float, toast: Toast) -> Toast:
        self._toasts.append((t, toast))
        self.marks += [t, t + toast.lifetime_sec]
        return toast

    def typing(self, t0: float, t1: float, period: float = 0.45) -> None:
        """Characters typed between t0 and t1: one more cell of the frame changes every `period`."""
        self._typing.append((t0, t1, period))
        self.marks += [t0 + k * 2.0 for k in range(int((t1 - t0) / 2.0) + 1)] + [t1]

    def page_at(self, t: float) -> Optional[Page]:
        i = bisect.bisect_right(self._nav_t, t) - 1
        return self._nav_p[i] if i >= 0 else None

    def toasts_at(self, t: float) -> List[Toast]:
        return [x for t0, x in self._toasts if t0 <= t < t0 + x.lifetime_sec]

    def typed_cells_at(self, t: float) -> int:
        i = bisect.bisect_right(self._nav_t, t) - 1
        since = self._nav_t[i] if i >= 0 else 0.0
        n = 0
        for t0, t1, period in self._typing:
            if t0 >= since and t >= t0:
                n += int((min(t, t1) - t0) / period) + 1
        return min(n, 60)


class Window:
    def __init__(self, hwnd: int, pid: int, tabs: List[Tab], opened: float = 0.0) -> None:
        self.hwnd, self.pid, self.tabs = hwnd, pid, list(tabs)
        self.opened, self.closed = opened, None
        self.rect = FULL                  # where it is on screen (another monitor: (1920, 0, 3840, 1040))
        self._sel_t: List[float] = [opened]
        self._sel: List[Tab] = [self.tabs[0]]
        self.marks: List[float] = [opened]
        self._bar: List[tuple] = []

    def type_in_bar(self, t0: float, t1: float, text: str) -> None:
        """The user types `text` into the address bar between t0 and t1 (the page itself is unchanged)."""
        self._bar.append((t0, t1, text))
        self.marks += [t0, t1]

    def bar_text_at(self, t: float) -> Optional[str]:
        return next((x for a, b, x in self._bar if a <= t < b), None)

    def add_tab(self, tab: Tab) -> Tab:
        self.tabs.append(tab)
        return tab

    def select(self, t: float, tab: Tab) -> None:
        assert tab in self.tabs and t >= self._sel_t[-1]
        self._sel_t.append(t)
        self._sel.append(tab)
        self.marks.append(t)

    def close(self, t: float) -> None:
        self.closed = t
        self.marks.append(t)

    def alive_at(self, t: float) -> bool:
        return self.opened <= t and (self.closed is None or t < self.closed)

    def selected_at(self, t: float) -> Tab:
        return self._sel[max(0, bisect.bisect_right(self._sel_t, t) - 1)]

    def page_at(self, t: float) -> Optional[Page]:
        return self.selected_at(t).page_at(t)


class Browser:
    def __init__(self) -> None:
        self.windows: Dict[int, Window] = {}
        self._hwnds = itertools.count(1001)
        self._pids = itertools.count(5001)
        self._front_t: List[float] = []
        self._front: List[int] = []
        self._other_rect: List[tuple] = []        # the other program's rectangle, per focus(t, None)
        self._marks: List[float] = []
        self._tabs: List[Tab] = []
        self._sorted_marks: List[float] = []
        self._marks_key: tuple = ()

    def tab(self) -> Tab:
        t = Tab()
        self._tabs.append(t)
        return t

    def window(self, tabs: int = 1, opened: float = 0.0, pid: Optional[int] = None) -> Window:
        w = Window(next(self._hwnds), pid or next(self._pids), [self.tab() for _ in range(tabs)], opened)
        self.windows[w.hwnd] = w
        return w

    def focus(self, t: float, window: Optional[Window], other_rect: tuple = FULL) -> None:
        """`window` comes in front at t; None: another program does, maximised unless `other_rect` says
        otherwise (a small signing dialog covers nothing)."""
        assert not self._front_t or t >= self._front_t[-1]
        self._front_t.append(t)
        self._front.append(window.hwnd if window else OTHER_APP)
        self._other_rect.append(other_rect)
        self._marks.append(t)

    def z_order_at(self, t: float) -> List[tuple]:
        """(hwnd, rect) of the browser windows open at t and of the other program, top first: the most
        recently focused on top, windows never focused at the bottom."""
        out, seen = [], set()
        i = bisect.bisect_right(self._front_t, t) - 1
        for k in range(i, -1, -1):
            hwnd = self._front[k]
            if hwnd in seen:
                continue
            seen.add(hwnd)
            if hwnd == OTHER_APP:
                out.append((OTHER_APP, self._other_rect[k]))
            elif self.windows[hwnd].alive_at(t):
                out.append((hwnd, self.windows[hwnd].rect))
        out += [(h, w.rect) for h, w in sorted(self.windows.items()) if h not in seen and w.alive_at(t)]
        return out

    def window_of_tab(self, tab: Tab) -> Optional[Window]:
        return next((w for w in self.windows.values() if tab in w.tabs), None)

    def crash(self, t: float) -> None:
        """The browser is gone (a power cut, a crash): every window closes."""
        for w in self.windows.values():
            if w.closed is None or w.closed > t:
                w.close(t)
        self.focus(t, None)

    def front_at(self, t: float) -> int:
        i = bisect.bisect_right(self._front_t, t) - 1
        hwnd = self._front[i] if i >= 0 else 0
        w = self.windows.get(hwnd)
        return hwnd if w is not None and w.alive_at(t) else 0

    def window_of(self, hwnd: int) -> Optional[Window]:
        return self.windows.get(hwnd)

    def active_at(self, t: float) -> bool:
        """Something changes within a few seconds of t: the harness ticks at the capture loop's pace."""
        key = (len(self._marks), sum(len(x.marks) for x in self._tabs), sum(len(w.marks) for w in self.windows.values()))
        if key != self._marks_key:
            marks = list(self._marks)
            for tab in self._tabs:
                marks += tab.marks
            for w in self.windows.values():
                marks += w.marks
            self._sorted_marks, self._marks_key = sorted(marks), key
        i = bisect.bisect_left(self._sorted_marks, t - ACTIVE_BEFORE_SEC)
        return i < len(self._sorted_marks) and self._sorted_marks[i] <= t + ACTIVE_AFTER_SEC
