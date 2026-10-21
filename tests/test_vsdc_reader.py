"""Reader v2 (core/vsdc/vsdc_reader): tickets, one lane per window with a content and a scope
worker, the cached read, torn detection, the Document URL first.

SGT never-miss blueprint WP3 with R2, R3, R9. A fake backend only - no browser, no portal, no COM;
every page, URL and line here is made up.
"""

import os
import sys
import threading
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.sgt_i import uia_nodes
from core.vsdc import vsdc_diag, vsdc_reader
from core.vsdc import vsdc_uia_text as uia_text
from core.vsdc.vsdc_reader import PageReader, ReadContext

PORTAL = "https://services.gst.gov.in/services/auth/fowelcome"
PORTAL_2 = "https://services.gst.gov.in/services/auth/returns"
SIDEBAR = "https://sidebar.example.test/chat"
CT_TEXT = 50020


class Clock:
    def __init__(self):
        self.t = 1000.0
        self._lock = threading.Lock()

    def __call__(self):
        with self._lock:
            return self.t

    def advance(self, sec):
        with self._lock:
            self.t += sec


class Doc:
    """A Document element: its URL (Value), whether it is off screen, its text, and optional
    gates (one threading.Event per read: the read waits for it) and hooks (run mid-read)."""

    def __init__(self, url, lines=("Fictional page",), offscreen=False):
        self.url, self.offscreen, self.lines = url, offscreen, list(lines)
        self.gates = []
        self.hooks = []
        self.cost_sec = 0.0       # fake clock time one read of it takes
        self.current_url_error = False   # A1: simulate GetCurrentPropertyValue failure


class Window:
    def __init__(self, pid=1, tab="tab-1", docs=(), bar=""):
        self.pid, self.tab, self.docs, self.bar = pid, tab, list(docs), bar
        self.closed = False


class FakeBackend:
    def __init__(self, clock, windows):
        self.clock = clock
        self.windows = windows
        self.reads = []                 # (hwnd, url) per page read
        self.calls = []                 # (method, hwnd) for every call
        self.bar_reads = 0
        self.init_threads = []
        self.active = {}                # pid -> page reads running now
        self.max_active = {}
        self._lock = threading.Lock()

    def init_thread(self):
        self.init_threads.append(threading.current_thread().name)

    def process_id(self, hwnd):
        return self.windows[hwnd].pid

    def root(self, hwnd):
        w = self.windows.get(hwnd)
        return None if w is None or w.closed else (hwnd, w)

    def tab_id(self, hwnd, root):
        self.calls.append(("tab", hwnd))
        return root[1].tab

    def documents(self, root):
        self.calls.append(("documents", root[0]))
        return [(doc, doc.url, doc.offscreen) for doc in root[1].docs]

    def address_bar(self, hwnd, root):
        self.bar_reads += 1
        return root[1].bar

    def read_document(self, doc):
        hwnd = next(h for h, w in self.windows.items() if doc in w.docs)
        pid = self.windows[hwnd].pid
        with self._lock:
            self.reads.append((hwnd, doc.url))
            self.active[pid] = self.active.get(pid, 0) + 1
            self.max_active[pid] = max(self.max_active.get(pid, 0), self.active[pid])
            gate = doc.gates.pop(0) if doc.gates else None
            hook = doc.hooks.pop(0) if doc.hooks else None
        try:
            if gate is not None:
                assert gate.wait(10), "test gate never released"
            if hook is not None:
                hook()
            self.clock.advance(doc.cost_sec)
            return [{"parent": -1, "depth": 0, "ctype": CT_TEXT, "name": line} for line in doc.lines]
        finally:
            with self._lock:
                self.active[pid] -= 1

    def document_url(self, doc):
        """A1: cheap post-read URL check. Simulates GetCurrentPropertyValue(PID_VALUE)."""
        self.calls.append(("document_url", id(doc)))
        if getattr(doc, "current_url_error", False):
            return None
        return doc.url


def make(windows, in_scope=None):
    clock = Clock()
    backend = FakeBackend(clock, windows)
    notes = []
    reader = PageReader(backend=backend, clock=clock, diag=notes.append, in_scope=in_scope)
    return reader, backend, clock, notes


_seq = [0]


def ctx(hwnd, clock, reason="frame", url=PORTAL, background=False):
    _seq[0] += 1
    return ReadContext(seq=_seq[0], hwnd=hwnd, url=url, asked_at=clock(), reason=reason, background=background)


def collect(reader, n, timeout=5.0):
    """Drain results until n have arrived (real time: the lanes are real threads)."""
    out, end = [], time.monotonic() + timeout
    while len(out) < n and time.monotonic() < end:
        out.extend(reader.results())
        if len(out) < n:
            time.sleep(0.005)
    assert len(out) >= n, f"only {len(out)} of {n} results arrived"
    return out


def wait_until(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "condition never held"
        time.sleep(0.005)


@pytest.fixture
def cleanup():
    readers, gates = [], []
    yield readers, gates
    for g in gates:
        g.set()
    for r in readers:
        r.close()


# ---- tickets ------------------------------------------------------------------------------

def test_a_result_carries_its_context_back(cleanup):
    reader, backend, clock, _ = make({1: Window(docs=[Doc(PORTAL, ["Return filed", "Status"])])})
    cleanup[0].append(reader)
    c = ctx(1, clock)
    assert reader.request(c) == "started"
    (r,) = collect(reader, 1)
    assert r.ctx is c
    assert r.error == "" and r.source == "uia"
    assert r.lines == ["Return filed", "Status"]
    assert r.doc_urls == [PORTAL] and r.scope_rule == "document"
    assert r.url_before == r.url_after == PORTAL and r.tab_before == r.tab_after == "tab-1"
    assert r.torn == "" and len(r.docs) == 1
    with pytest.raises(Exception):
        c.url = "elsewhere"          # frozen: a result can never be re-pointed at another page


def test_a_slow_read_never_blocks_another_windows_lane(cleanup):
    slow = Doc(PORTAL)
    gate = threading.Event()
    slow.gates.append(gate)
    reader, backend, clock, _ = make({1: Window(pid=1, docs=[slow]), 2: Window(pid=2, docs=[Doc(PORTAL_2)])})
    cleanup[0].append(reader)
    cleanup[1].append(gate)
    assert reader.request(ctx(1, clock)) == "started"
    assert reader.request(ctx(2, clock)) == "started"
    (r,) = collect(reader, 1)
    assert r.ctx.hwnd == 2 and not gate.is_set()
    assert reader.inflight(1) is not None and reader.inflight(2) is None
    gate.set()
    (r1,) = collect(reader, 1)
    assert r1.ctx.hwnd == 1 and r1.error == ""


def test_a_queued_context_replaces_the_older_one(cleanup):
    doc = Doc(PORTAL)
    gate = threading.Event()
    doc.gates.append(gate)
    reader, backend, clock, _ = make({1: Window(docs=[doc])})
    cleanup[0].append(reader)
    cleanup[1].append(gate)
    c1, c2, c3 = ctx(1, clock), ctx(1, clock), ctx(1, clock)
    assert reader.request(c1) == "started"
    assert reader.request(c2) == "queued"
    assert reader.request(c3) == "queued"
    gate.set()
    got = collect(reader, 2)
    assert [r.ctx for r in got] == [c1, c3]
    assert reader.stats()["queued_replaced"] == 1


def test_a_url_only_request_never_replaces_a_queued_page_read(cleanup):
    doc = Doc(PORTAL)
    gate = threading.Event()
    doc.gates.append(gate)
    reader, backend, clock, _ = make({1: Window(docs=[doc])})
    cleanup[0].append(reader)
    cleanup[1].append(gate)
    page1, page2, scope = ctx(1, clock), ctx(1, clock), ctx(1, clock, reason="scope")
    assert reader.request(page1) == "started"
    assert reader.request(page2) == "queued"
    assert reader.request(scope) == "started"         # its own worker (R2), not the content queue
    (r,) = collect(reader, 1)
    assert r.ctx is scope and r.url_only
    gate.set()
    assert [r.ctx for r in collect(reader, 2)] == [page1, page2]
    assert reader.stats()["queued_replaced"] == 0


# ---- wedges and the pause -----------------------------------------------------------------

def test_a_wedged_lane_is_replaced_and_its_late_result_is_marked_abandoned(cleanup):
    doc = Doc(PORTAL)
    gate = threading.Event()
    doc.gates.append(gate)
    reader, backend, clock, notes = make({1: Window(docs=[doc])})
    cleanup[0].append(reader)
    cleanup[1].append(gate)
    c1 = ctx(1, clock)
    assert reader.request(c1) == "started"
    wait_until(lambda: backend.reads)
    clock.advance(vsdc_reader.WEDGE_SEC)
    c2 = ctx(1, clock)
    assert reader.request(c2) == "started"            # a fresh worker took it
    (r2,) = collect(reader, 1)
    assert r2.ctx is c2 and r2.error == ""
    st = reader.stats()
    assert st["abandoned"] == 1 and st["stuck"] == 1
    gate.set()
    (r1,) = collect(reader, 1)
    assert r1.ctx is c1 and r1.error == "timeout-abandoned"
    assert reader.stats()["stuck"] == 0
    assert any("stuck" in n for n in notes)


def test_five_wedged_workers_pause_then_resume(cleanup):
    doc = Doc(PORTAL)
    gates = [threading.Event() for _ in range(vsdc_reader.MAX_WEDGED_WORKERS)]
    doc.gates.extend(gates)
    reader, backend, clock, notes = make({1: Window(docs=[doc])})
    cleanup[0].append(reader)
    cleanup[1].extend(gates)
    for i in range(vsdc_reader.MAX_WEDGED_WORKERS):
        assert reader.request(ctx(1, clock)) == "started"
        wait_until(lambda: len(backend.reads) == i + 1)
        clock.advance(vsdc_reader.WEDGE_SEC)
    assert reader.request(ctx(1, clock)) == "paused"
    assert reader.paused() and reader.stats()["stuck"] == vsdc_reader.MAX_WEDGED_WORKERS
    clock.advance(vsdc_reader.PAUSE_SEC / 2)
    assert reader.request(ctx(1, clock)) == "paused"
    clock.advance(vsdc_reader.PAUSE_SEC / 2)
    c = ctx(1, clock)
    assert reader.request(c) == "started"             # tried again, never off for the run
    assert any(r.ctx is c and r.error == "" for r in collect(reader, 1))
    assert any("paused" in n for n in notes) and any("pause over" in n for n in notes)


# ---- A1: one document locate per clean content read ----------------------------------------

def test_a_clean_read_asks_the_document_list_once(cleanup):
    """A1: a clean content read calls Backend.documents exactly once and document_url once."""
    win = Window(docs=[Doc(PORTAL, ["ARN: AA2709260123456"])])
    reader, backend, clock, _ = make({1: win})
    cleanup[0].append(reader)
    reader.request(ctx(1, clock))
    (r,) = collect(reader, 1)
    assert sum(1 for m, _ in backend.calls if m == "documents") == 1
    assert sum(1 for m, _ in backend.calls if m == "document_url") == 1
    assert r.torn == "" and r.lines == ["ARN: AA2709260123456"]


def test_tab_change_skips_the_second_document_scan(cleanup):
    """A1: when the tab changes mid-read, url_after = url_before and only one documents call."""
    win = Window(docs=[Doc(PORTAL)])
    win.docs[0].hooks.append(lambda: setattr(win, "tab", "tab-2"))
    reader, backend, clock, _ = make({1: win})
    cleanup[0].append(reader)
    reader.request(ctx(1, clock))
    (r,) = collect(reader, 1)
    assert (r.tab_before, r.tab_after, r.torn) == ("tab-1", "tab-2", "tab")
    assert sum(1 for m, _ in backend.calls if m == "documents") == 1
    assert sum(1 for m, _ in backend.calls if m == "document_url") == 0


def test_url_change_still_scans_twice(cleanup):
    """A1: when document_url returns a different URL, fall back to a second full locate."""
    win = Window(docs=[Doc(PORTAL)])
    win.docs[0].hooks.append(lambda: setattr(win.docs[0], "url", PORTAL_2))
    reader, backend, clock, _ = make({1: win})
    cleanup[0].append(reader)
    reader.request(ctx(1, clock))
    (r,) = collect(reader, 1)
    assert (r.url_before, r.url_after, r.torn) == (PORTAL, PORTAL_2, "url")
    assert sum(1 for m, _ in backend.calls if m == "documents") == 2


def test_an_unavailable_current_url_falls_back_to_a_full_locate(cleanup):
    """A1: if document_url returns None (property error), fall back to full locate — no data lost."""
    win = Window(docs=[Doc(PORTAL, ["Success page"])])
    win.docs[0].current_url_error = True
    reader, backend, clock, _ = make({1: win})
    cleanup[0].append(reader)
    reader.request(ctx(1, clock))
    (r,) = collect(reader, 1)
    assert r.torn == "" and r.lines == ["Success page"]
    assert sum(1 for m, _ in backend.calls if m == "documents") == 2
    assert sum(1 for m, _ in backend.calls if m == "document_url") == 1


# ---- torn reads ---------------------------------------------------------------------------

def test_tab_change_during_read_is_torn_tab(cleanup):
    win = Window(docs=[Doc(PORTAL)])
    win.docs[0].hooks.append(lambda: setattr(win, "tab", "tab-2"))
    reader, backend, clock, _ = make({1: win})
    cleanup[0].append(reader)
    reader.request(ctx(1, clock))
    (r,) = collect(reader, 1)
    assert (r.tab_before, r.tab_after, r.torn) == ("tab-1", "tab-2", "tab")


def test_url_change_during_read_is_torn_url(cleanup):
    win = Window(docs=[Doc(PORTAL)])
    win.docs[0].hooks.append(lambda: setattr(win.docs[0], "url", PORTAL_2))
    reader, backend, clock, _ = make({1: win})
    cleanup[0].append(reader)
    reader.request(ctx(1, clock))
    (r,) = collect(reader, 1)
    assert (r.url_before, r.url_after, r.torn) == (PORTAL, PORTAL_2, "url")


# ---- the scope gate inside the job --------------------------------------------------------

def test_only_in_scope_documents_are_read(cleanup):
    docs = [Doc(SIDEBAR, ["sidebar text"]), Doc("chrome-extension://abcdefgh/panel.html", ["extension"]),
            Doc("about:blank", ["blank"]), Doc(PORTAL_2, ["background tab"], offscreen=True),
            Doc(PORTAL, ["portal text"])]
    reader, backend, clock, _ = make({1: Window(docs=docs, bar=PORTAL)})   # the real vsdc_scope gate
    cleanup[0].append(reader)
    reader.request(ctx(1, clock))
    (r,) = collect(reader, 1)
    assert backend.reads == [(1, PORTAL)]
    assert r.lines == ["portal text"] and r.doc_urls == [PORTAL] and r.scope_rule == "document"
    assert backend.bar_reads == 0                     # a Document gave the URL: no address bar


def test_nothing_is_read_when_no_document_is_in_scope(cleanup):
    reader, backend, clock, _ = make({1: Window(docs=[Doc(SIDEBAR)], bar=PORTAL)})
    cleanup[0].append(reader)
    reader.request(ctx(1, clock))
    (r,) = collect(reader, 1)
    assert backend.reads == [] and r.lines == [] and r.scope_rule == "" and r.url == SIDEBAR


def test_url_only_job_reads_no_content(cleanup):
    reader, backend, clock, _ = make({1: Window(docs=[Doc(PORTAL)])})
    cleanup[0].append(reader)
    assert reader.request(ctx(1, clock, reason="scope", url="")) == "started"
    (r,) = collect(reader, 1)
    assert backend.reads == []
    assert r.url_only and r.lines == [] and r.docs == []
    assert r.url == PORTAL and r.tab_after == "tab-1" and r.scope_rule == "document"
    assert reader.stats()["scope_reads"] == 1 and reader.stats()["reads"] == 0


def test_address_bar_is_used_when_no_document_has_a_url(cleanup):
    reader, backend, clock, _ = make({1: Window(docs=[Doc("", ["page text"])], bar=PORTAL),
                                      2: Window(pid=2, docs=[Doc(SIDEBAR, ["sidebar"]), Doc("", ["page"])],
                                                bar=PORTAL)})
    cleanup[0].append(reader)
    reader.request(ctx(1, clock))
    reader.request(ctx(2, clock))
    got = {r.ctx.hwnd: r for r in collect(reader, 2)}
    assert got[1].scope_rule == "address_bar" and got[1].lines == ["page text"] and got[1].url == PORTAL
    # a Document with its own URL off the portals is never read, even when the bar is in scope
    assert got[2].lines == ["page"] and (2, SIDEBAR) not in backend.reads


def test_address_bar_out_of_scope_reads_nothing(cleanup):
    reader, backend, clock, _ = make({1: Window(docs=[Doc("")], bar="https://news.example.test/")})
    cleanup[0].append(reader)
    reader.request(ctx(1, clock))
    (r,) = collect(reader, 1)
    assert backend.reads == [] and r.lines == [] and r.scope_rule == ""


# ---- the cached read equals the old reader ------------------------------------------------

class _CacheRequest:
    def __init__(self):
        self.properties = []

    def AddProperty(self, pid):
        self.properties.append(pid)


class _CachingUia:
    def CreateCacheRequest(self):
        return _CacheRequest()

    RawViewCondition = object()


def test_lines_from_the_cached_read_equal_the_old_reader_on_the_fixture_tree():
    from tools import sgt_lines_equivalence as eq

    old_uia, client = eq._FakeUia(), eq._FakeUiaClient()
    for name, root in eq.synthetic_pages():
        root.BuildUpdatedCache = lambda _request, el=root: el   # the fake already answers cached reads
        nodes = uia_nodes.read_cached_subtree(_CachingUia(), client, root)
        for selection in (True, False):
            today = uia_text._collect_descendant_lines(old_uia, client, root, include_selection=selection)
            assert uia_nodes.lines_from_nodes([nodes], include_selection=selection) == today, name


def test_the_url_only_cache_request_holds_no_page_content():
    req = uia_nodes.build_cache_request(_CachingUia(), False, uia_nodes.URL_ONLY_PROPERTIES)
    assert req.properties == list(uia_nodes.URL_ONLY_PROPERTIES)
    assert req.TreeScope == uia_nodes._SCOPE_ELEMENT          # the Document itself, not its subtree
    assert not hasattr(req, "AutomationElementMode")           # keeps the live reference for the read
    assert uia_nodes.PID_NAME not in req.properties


def test_documents_with_urls_reads_value_and_offscreen_from_the_cache():
    class El:
        def __init__(self, cached):
            self._c = cached

        def GetCachedPropertyValue(self, pid):
            if pid not in self._c:
                raise RuntimeError("not cached")
            return self._c[pid]

    els = [El({uia_nodes.PID_VALUE: " " + PORTAL + " ", uia_nodes.PID_IS_OFFSCREEN: False}),
           El({uia_nodes.PID_VALUE: None, uia_nodes.PID_IS_OFFSCREEN: True})]

    class Root:
        def FindAllBuildCache(self, scope, cond, request):
            self.request = request
            return SimpleNamespace(Length=len(els), GetElement=lambda i: els[i])

    uia = _CachingUia()
    uia.CreatePropertyCondition = lambda prop, ctype: (prop, ctype)
    client = SimpleNamespace(UIA_ControlTypePropertyId=30003, TreeScope_Descendants=4)
    root = Root()
    got = uia_text.documents_with_urls(uia, client, root)
    assert [(u, off) for _e, u, off in got] == [(PORTAL, False), ("", True)]
    assert root.request.properties == list(uia_nodes.URL_ONLY_PROPERTIES)


# ---- lanes --------------------------------------------------------------------------------

def test_background_window_gets_no_lane_past_the_cap(cleanup):
    windows = {h: Window(pid=h, docs=[Doc(PORTAL)]) for h in range(1, vsdc_reader.MAX_LANES + 3)}
    reader, backend, clock, _ = make(windows)
    cleanup[0].append(reader)
    for h in range(1, vsdc_reader.MAX_LANES + 1):
        assert reader.request(ctx(h, clock, background=(h != 1))) == "started"
        clock.advance(1)
    collect(reader, vsdc_reader.MAX_LANES)
    extra = vsdc_reader.MAX_LANES + 1
    assert reader.request(ctx(extra, clock, background=True)) == "no-lane"
    assert reader.stats()["no_lane"] == 1
    # the window in front always gets a lane: the least recently used background lane goes
    assert reader.request(ctx(extra + 1, clock)) == "started"
    assert reader.stats()["lanes"] == vsdc_reader.MAX_LANES
    assert reader.page_ms(2, PORTAL) == 0.0 and reader.page_ms(1, PORTAL) >= 0.0
    collect(reader, 1)


def test_a_closed_window_gives_no_window_and_closes_its_lane(cleanup):
    win = Window(docs=[Doc(PORTAL)])
    reader, backend, clock, _ = make({1: win})
    cleanup[0].append(reader)
    win.closed = True
    reader.request(ctx(1, clock))
    (r,) = collect(reader, 1)
    assert r.error == "no-window" and r.lines == []
    assert reader.stats()["lanes"] == 0


def test_an_idle_lane_is_closed(cleanup):
    reader, backend, clock, _ = make({1: Window(docs=[Doc(PORTAL)])})
    cleanup[0].append(reader)
    reader.request(ctx(1, clock))
    collect(reader, 1)
    clock.advance(vsdc_reader.LANE_IDLE_SEC)
    reader.results()
    assert reader.stats()["lanes"] == 0


def test_a_result_too_old_is_dropped(cleanup):
    doc = Doc(PORTAL)
    doc.cost_sec = vsdc_reader.RESULT_MAX_AGE_SEC + 1
    reader, backend, clock, notes = make({1: Window(docs=[doc])})
    cleanup[0].append(reader)
    reader.request(ctx(1, clock))
    wait_until(lambda: reader.inflight(1) is None)
    assert reader.results() == []
    assert reader.stats()["too_old"] == 1


# ---- R2: the scope worker keeps the URL fresh during a heavy read -------------------------

def _heavy_read_in_flight(cleanup, seconds=5):
    doc = Doc(PORTAL)
    gate = threading.Event()
    doc.gates.append(gate)
    reader, backend, clock, _ = make({1: Window(docs=[doc])})
    cleanup[0].append(reader)
    cleanup[1].append(gate)
    page = ctx(1, clock)
    assert reader.request(page) == "started"
    wait_until(lambda: backend.reads)
    return reader, backend, clock, gate, page


def test_scope_stays_fresh_while_a_5s_read_runs(cleanup):
    reader, backend, clock, gate, page = _heavy_read_in_flight(cleanup)
    for _ in range(5):
        clock.advance(1.0)
        assert reader.request(ctx(1, clock, reason="scope")) == "started"
        (r,) = collect(reader, 1)
        assert r.url_only and r.url == PORTAL
        assert clock() - r.finished_at < vsdc_reader.URL_FRESH_SEC
        assert reader.inflight(1) is page                # the page read is still running
    gate.set()
    (r,) = collect(reader, 1)
    assert r.ctx is page and r.error == ""


def test_flash_ocr_fires_during_a_heavy_read(cleanup):
    """Blueprint 5.1's flash trigger needs, during a heavy read: the scope gate open (a URL younger
    than URL_FRESH_SEC) and the lane known to be busy. The reader gives both; the trigger itself
    is the loop's (WP4/WP7)."""
    reader, backend, clock, gate, page = _heavy_read_in_flight(cleanup)
    clock.advance(3.0)
    reader.request(ctx(1, clock, reason="scope"))
    (r,) = collect(reader, 1)
    scope_open = clock() - r.finished_at < vsdc_reader.URL_FRESH_SEC and r.url == PORTAL
    lane_busy = reader.inflight(1) is not None
    assert scope_open and lane_busy
    from core.vsdc import vsdc_gate
    big = vsdc_gate.FLASH_MIN_CELLS
    assert vsdc_gate.flash_wanted(big, reader.inflight(1) is not None, reader.page_ms(1, PORTAL), reader.paused())
    gate.set()
    collect(reader, 1)
    assert reader.page_ms(1, PORTAL) >= 3000          # the next frame on this page counts as slow too
    # the read is over, but the page is known to be slow: the flash trigger still fires for the next toast
    assert vsdc_gate.flash_wanted(big, reader.inflight(1) is not None, reader.page_ms(1, PORTAL), reader.paused())
    assert not vsdc_gate.flash_wanted(big - 1, True, reader.page_ms(1, PORTAL), reader.paused())


# ---- R9: at most one heavy read per browser process ---------------------------------------

def test_two_heavy_reads_of_one_browser_never_run_at_once(cleanup):
    a, b = Doc(PORTAL), Doc(PORTAL_2)
    a.cost_sec = b.cost_sec = 1.0          # 1000 ms each: heavy
    reader, backend, clock, _ = make({1: Window(pid=7, docs=[a]), 2: Window(pid=7, docs=[b]),
                                      3: Window(pid=8, docs=[Doc(PORTAL)])})
    cleanup[0].append(reader)
    reader.request(ctx(1, clock, url=PORTAL))
    collect(reader, 1)
    reader.request(ctx(2, clock, url=PORTAL_2))
    collect(reader, 1)
    assert reader.page_ms(1, PORTAL) > vsdc_reader.HEAVY_READ_MS

    gate = threading.Event()
    a.gates.append(gate)
    cleanup[1].append(gate)
    first, second = ctx(1, clock, url=PORTAL), ctx(2, clock, url=PORTAL_2)
    assert reader.request(first) == "started"
    wait_until(lambda: backend.active.get(7) == 1)
    assert reader.request(second) == "queued"          # held: the same browser is busy
    assert reader.stats()["held"] == 1
    assert reader.request(ctx(3, clock)) == "started"  # another browser process is not held
    collect(reader, 1)
    time.sleep(0.05)
    assert backend.max_active[7] == 1 and reader.inflight(2) is None
    gate.set()
    got = collect(reader, 2)
    assert [r.ctx for r in got] == [first, second]
    assert backend.max_active[7] == 1


def test_a_light_read_of_the_same_browser_is_not_held(cleanup):
    a = Doc(PORTAL)
    gate = threading.Event()
    a.gates.append(gate)
    reader, backend, clock, _ = make({1: Window(pid=7, docs=[a]), 2: Window(pid=7, docs=[Doc(PORTAL_2)])})
    cleanup[0].append(reader)
    cleanup[1].append(gate)
    reader.request(ctx(1, clock))
    wait_until(lambda: backend.reads)
    assert reader.request(ctx(2, clock, url=PORTAL_2)) == "started"
    collect(reader, 1)
    gate.set()
    collect(reader, 1)


# ---- worker threads, shutdown, heartbeat --------------------------------------------------

def test_workers_are_mta_by_default_and_sta_on_request(monkeypatch):
    import comtypes
    calls = []
    monkeypatch.setattr(comtypes, "CoInitializeEx", lambda flags: calls.append(("ex", flags)))
    monkeypatch.setattr(comtypes, "CoInitialize", lambda: calls.append(("sta", None)))
    assert vsdc_reader.WORKER_APARTMENT == "mta"
    vsdc_reader.init_com()
    vsdc_reader.init_com("sta")
    assert calls == [("ex", comtypes.COINIT_MULTITHREADED), ("sta", None)]


@pytest.mark.skipif(sys.platform != "win32", reason="real UI Automation")
def test_the_real_backend_starts_on_an_mta_thread_and_reads_no_window():
    backend, out = vsdc_reader.UiaBackend(), {}

    def run():
        try:
            backend.init_thread()
            out["root"] = backend.root(0)           # no window: nothing is asked of any browser
        except Exception as e:
            out["error"] = e
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(30)
    assert "error" not in out and out.get("root", "unset") is None


def test_each_worker_initialises_its_own_thread(cleanup):
    reader, backend, clock, _ = make({1: Window(docs=[Doc(PORTAL)])})
    cleanup[0].append(reader)
    reader.request(ctx(1, clock))
    reader.request(ctx(1, clock, reason="scope"))
    collect(reader, 2)
    reader.request(ctx(1, clock))
    collect(reader, 1)
    assert sorted(backend.init_threads) == ["sgt-read-content-1", "sgt-read-scope-1"]


def test_a_failed_uia_init_is_an_error_result(cleanup):
    reader, backend, clock, notes = make({1: Window(docs=[Doc(PORTAL)])})
    cleanup[0].append(reader)

    def boom():
        raise OSError("no UIA")
    backend.init_thread = boom
    reader.request(ctx(1, clock))
    (r,) = collect(reader, 1)
    assert r.error == "uia-init" and backend.reads == []
    assert any("init failed" in n for n in notes)


def test_close_leaves_wedged_threads_as_daemons(cleanup):
    doc = Doc(PORTAL)
    gate = threading.Event()
    doc.gates.append(gate)
    reader, backend, clock, _ = make({1: Window(docs=[doc])})
    cleanup[1].append(gate)
    reader.request(ctx(1, clock))
    wait_until(lambda: backend.reads)
    reader.close()
    stuck = [t for t in threading.enumerate() if t.name == "sgt-read-content-1"]
    assert stuck and all(t.daemon for t in stuck)
    assert reader.request(ctx(1, clock)) == "no-lane"


def test_heartbeat_takes_read_stats_from_a_registered_reader(cleanup):
    reader, backend, clock, _ = make({1: Window(docs=[Doc(PORTAL)])})
    cleanup[0].append(reader)
    reader.request(ctx(1, clock))
    collect(reader, 1)
    try:
        vsdc_diag.register_reader(reader)
        st = vsdc_diag.take_read_stats()
        assert st["reads"] == 1 and st["lanes"] == 1
        assert vsdc_diag.take_read_stats()["reads"] == 0          # per interval
    finally:
        vsdc_diag.register_reader(None)


def test_diag_lines_hold_no_urls(cleanup):
    doc = Doc(PORTAL)
    gate = threading.Event()
    doc.gates.append(gate)
    reader, backend, clock, notes = make({1: Window(docs=[doc])})
    cleanup[0].append(reader)
    cleanup[1].append(gate)
    reader.request(ctx(1, clock))
    wait_until(lambda: backend.reads)
    clock.advance(vsdc_reader.WEDGE_SEC + vsdc_reader.RESULT_MAX_AGE_SEC)
    reader.request(ctx(1, clock))
    gate.set()
    collect(reader, 1)
    wait_until(lambda: reader.stats()["stuck"] == 0)
    reader.results()
    assert notes and not any("gst.gov.in" in n or "http" in n for n in notes)


# ---- B5: separate latency abandon from wedged workers -------------------------------------

def test_b5_early_abandon_frees_worker_without_wedging(cleanup):
    doc = Doc(PORTAL)
    gate = threading.Event()
    doc.gates.append(gate)
    reader, backend, clock, notes = make({1: Window(docs=[doc])})
    cleanup[0].append(reader)
    cleanup[1].append(gate)
    c1 = ctx(1, clock)
    assert reader.request(c1) == "started"
    wait_until(lambda: backend.reads)
    clock.advance(1.0)
    assert not reader.abandon_slow_content(1, min_running_sec=3.0)
    clock.advance(2.5)
    assert reader.abandon_slow_content(1, min_running_sec=3.0)
    c2 = ctx(1, clock)
    assert reader.request(c2) == "started"
    st = reader.stats()
    assert st["abandoned"] == 1 and st["stuck"] == 0
    gate.set()
    (r1, r2) = collect(reader, 2)
    assert r1.ctx is c1 and r1.error == "timeout-abandoned"
    assert r2.ctx is c2 and r2.error == ""
    assert reader.stats()["stuck"] == 0


def test_b5_early_abandon_promoted_to_wedged_if_running_past_20s(cleanup):
    doc = Doc(PORTAL)
    gate = threading.Event()
    doc.gates.append(gate)
    reader, backend, clock, notes = make({1: Window(docs=[doc])})
    cleanup[0].append(reader)
    cleanup[1].append(gate)
    c1 = ctx(1, clock)
    assert reader.request(c1) == "started"
    wait_until(lambda: backend.reads)
    clock.advance(3.5)
    assert reader.abandon_slow_content(1, min_running_sec=3.0)
    assert reader.stats()["stuck"] == 0
    clock.advance(17.0)
    reader.results()
    assert reader.stats()["stuck"] == 1
    gate.set()


def test_b5_early_abandon_respects_thread_cap(cleanup):
    doc = Doc(PORTAL)
    gates = [threading.Event() for _ in range(vsdc_reader.MAX_EARLY_ABANDONED + 1)]
    doc.gates.extend(gates)
    reader, backend, clock, notes = make({1: Window(docs=[doc])})
    cleanup[0].append(reader)
    for g in gates:
        cleanup[1].append(g)
    for _ in range(vsdc_reader.MAX_EARLY_ABANDONED):
        c = ctx(1, clock)
        assert reader.request(c) == "started"
        wait_until(lambda: backend.reads)
        clock.advance(3.5)
        assert reader.abandon_slow_content(1, min_running_sec=3.0)
    c_last = ctx(1, clock)
    assert reader.request(c_last) == "started"
    wait_until(lambda: backend.reads)
    clock.advance(3.5)
    assert not reader.abandon_slow_content(1, min_running_sec=3.0)
    for g in gates:
        g.set()
