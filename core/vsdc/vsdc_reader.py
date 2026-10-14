"""
core/vsdc/vsdc_reader.py - Reader v2: page reads are tickets, one read lane per browser window
================================================================================================
SGT never-miss blueprint (docs/sgt-never-miss-blueprint.md) sections 4.2, 4.3, 4.5, 4.6 with the
review corrections R2, R3, R9.

Before: one UIA worker thread did everything for every window, a read that took longer than 3 s
was thrown away, and a heavy page on one window made every other window wait. Now:

  - A read is asked for with a ReadContext (window, tab, URL, title, portal, time, why) and is
    answered later by a ReadResult that carries that context back. The capture thread never
    waits: request() returns at once, results() drains what has finished.
  - Each watched window has its own lane with TWO worker threads, both MTA (owner answer D2),
    each with its own UIA objects: a content worker (page reads) and a scope worker (URL-only
    jobs: tab id, Document URLs, address bar - no page content). A URL check never waits behind
    a page read (R2).
  - A page read is one cached UIA call per Document (core/sgt_i/uia_nodes.read_cached_subtree)
    and gives both the lines and the nodes, so SGT-I needs no second read.
  - The URL comes from the Document element's Value first (owner answer D1), the address bar only
    when no on-screen Document has a portal URL and one has none at all. Only Documents whose own
    URL passes vsdc_scope.is_in_scope_url are read: a sidebar, a translate bar or an extension
    pane is a Document of its own with its own URL and is never read.
  - The tab id and the URL are taken before and after the read, so a read the user switched away
    from mid-way is reported torn ("tab" / "url"), never attributed to the wrong page.
  - At most one queued page read per lane: a newer one replaces it, a URL-only job never does
    (R3). A page whose last read took over HEAVY_READ_MS waits while another window of the same
    browser process is being read, because one browser answers all its windows on one UI thread
    (R9).
  - A read running WEDGE_SEC is abandoned (its worker replaced, its late result marked
    "timeout-abandoned" and never absorbed); MAX_WEDGED_WORKERS stuck at once pause UIA reads for
    PAUSE_SEC - the same rules as vsdc_uia_text, per worker.

Strictly passive, like every UIA read in the app: property reads only, nothing is clicked,
focused, scrolled or typed. Never logs page text, URLs or values (vsdc.log gets counts only).
The old vsdc_uia_text.read_page_text / selected_tab_id stay for the tripwire, the crosshair
engines and the tests; both readers use the same helpers there.
"""

import ctypes
import queue
import threading
import time
from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional, Protocol, Tuple

from core.sgt_i import uia_nodes

from . import vsdc_diag

MAX_LANES = 3                 # watched windows at once (foreground + 2 visible); D6
WEDGE_SEC = 20.0              # a read running this long is abandoned (vsdc_uia_text.SLOW_GRACE_SEC)
MAX_WEDGED_WORKERS = 5        # as vsdc_uia_text
PAUSE_SEC = 300.0             # as vsdc_uia_text
RESULT_MAX_AGE_SEC = 60.0     # a result older than this is logged and dropped, never absorbed
LANE_IDLE_SEC = 120.0         # a lane whose window was not asked about for this long is closed
PAGE_TIMEOUT_SEC = 3.0        # a read longer than this is "late" (health state "slow"); it is still delivered
URL_FRESH_SEC = 2.0           # how old a scope result may be for the loop's scope gate (WP4)
HEAVY_READ_MS = 700.0         # R9: a page read slower than this is not read beside another of the same browser
PAGE_MS_KEPT = 50             # last read time remembered for this many pages per lane

WORKER_APARTMENT = "mta"      # D2 (owner, 2026-10-14). "sta" = the old CoInitialize().

REASON_SCOPE = "scope"        # a URL-only job (scope worker); every other reason is a page read

CONTENT, SCOPE = "content", "scope"


@dataclass(frozen=True)
class ReadContext:
    """What the capture loop believed when it asked for a read. Frozen: a result can never be
    re-pointed at another page."""
    seq: int                    # increasing, unique per process run
    hwnd: int
    title: str = ""             # window title at request time (no browser suffix)
    portal: str = ""            # "GST Portal" / "Income Tax" / registered portal name
    url: str = ""               # sanitized URL the loop believed at request time ("" if none)
    url_source: str = ""        # "document" | "address_bar" | "title"
    tab: Optional[str] = None   # tab id if the loop already knows it (None = worker finds it)
    frame_sig: Optional[bytes] = None   # 1024 nibbles (blueprint 4.4; None = no screenshot)
    asked_at: float = 0.0       # the reader's clock (time.monotonic())
    reason: str = "frame"       # "scope" | "url" | "frame" | "periodic" | "burst" | "torn-retry" | "blind-retry" | "wants_read"
    background: bool = False    # window was not in front when asked


@dataclass
class ReadResult:
    ctx: ReadContext
    source: str = "uia"                                     # "uia" | "ocr"
    lines: List[str] = field(default_factory=list)
    docs: List[List[Dict[str, Any]]] = field(default_factory=list)   # node lists per Document read
    doc_urls: List[str] = field(default_factory=list)       # the URL each Document read was judged by
    tab_before: Optional[str] = None
    tab_after: Optional[str] = None
    url_before: str = ""        # what the worker saw just before reading (document, else address bar)
    url_after: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    error: str = ""             # "" | "no-window" | "uia-init" | "timeout-abandoned" | "exception:<Name>"
    scope_rule: str = ""        # "document" | "address_bar" | "" (nothing in scope: nothing read)

    @property
    def ms(self) -> float:
        return max(0.0, (self.finished_at - self.started_at) * 1000.0)

    @property
    def torn(self) -> str:
        """"tab" when the tab in front changed during the read, else "url" when the page's URL
        did; "" for a clean read. Blueprint 4.6 with R1: both are dropped and asked again."""
        if self.error:
            return ""
        if self.tab_after != self.tab_before:
            return "tab"
        if self.url_after != self.url_before:
            return "url"
        return ""

    @property
    def late(self) -> bool:
        """Took longer than PAGE_TIMEOUT_SEC (still delivered: a late read is evidence, not waste)."""
        return self.ms > PAGE_TIMEOUT_SEC * 1000.0

    @property
    def url_only(self) -> bool:
        return self.ctx.reason == REASON_SCOPE

    @property
    def url(self) -> str:
        """The page's URL as the worker last saw it."""
        return self.url_after or self.url_before


class Backend(Protocol):
    """Everything the reader asks of UI Automation, so it can be tested without Windows. Every
    method but process_id runs on a lane's worker thread, after init_thread on that thread."""
    def init_thread(self) -> None: ...
    def process_id(self, hwnd: int) -> int: ...
    def root(self, hwnd: int) -> Any: ...
    def tab_id(self, hwnd: int, root: Any) -> Optional[str]: ...
    def documents(self, root: Any) -> List[Tuple[Any, str, bool]]: ...     # (element, url, offscreen)
    def address_bar(self, hwnd: int, root: Any) -> str: ...
    def read_document(self, element: Any) -> List[Dict[str, Any]]: ...     # nodes


def init_com(apartment: str = WORKER_APARTMENT) -> None:
    """COM on a lane's worker thread: MTA (Microsoft's guidance for UIA clients; owner answer D2),
    or the old single-threaded apartment."""
    import comtypes
    if apartment == "mta":
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
    else:
        comtypes.CoInitialize()


class UiaBackend:
    """The real backend: Windows UI Automation through comtypes, one UIA object per thread."""

    def __init__(self, apartment: str = WORKER_APARTMENT):
        self._apartment = apartment
        self._tls = threading.local()

    def init_thread(self) -> None:
        import comtypes.client
        init_com(self._apartment)
        client = comtypes.client.GetModule("UIAutomationCore.dll")
        uia = comtypes.client.CreateObject(client.CUIAutomation, interface=client.IUIAutomation)
        self._tls.uia, self._tls.client = uia, client
        self._tls.tabs, self._tls.bars = {}, {}

    def process_id(self, hwnd: int) -> int:
        try:
            from ctypes import wintypes
            pid = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            return int(pid.value)
        except Exception:
            return 0

    def root(self, hwnd: int) -> Any:
        if not hwnd or not ctypes.windll.user32.IsWindow(hwnd):
            return None
        try:
            return self._tls.uia.ElementFromHandle(hwnd)
        except Exception:
            return None

    def tab_id(self, hwnd: int, root: Any) -> Optional[str]:
        from . import vsdc_uia_text
        try:
            return vsdc_uia_text.selected_tab_id_here(self._tls.uia, self._tls.client, hwnd, self._tls.tabs, root)
        except Exception:
            return None

    def documents(self, root: Any) -> List[Tuple[Any, str, bool]]:
        from . import vsdc_uia_text
        return vsdc_uia_text.documents_with_urls(self._tls.uia, self._tls.client, root)

    def address_bar(self, hwnd: int, root: Any) -> str:
        from . import vsdc_uia_text
        return vsdc_uia_text.address_bar_here(self._tls.uia, self._tls.client, hwnd, self._tls.bars, root) or ""

    def read_document(self, element: Any) -> List[Dict[str, Any]]:
        return uia_nodes.read_cached_subtree(self._tls.uia, self._tls.client, element)


class _OneThread:
    """A one-thread executor whose thread is a daemon: a read wedged inside the browser never
    holds the app open at exit (ThreadPoolExecutor's threads are joined at exit)."""

    def __init__(self, name: str):
        self._q: "queue.SimpleQueue" = queue.SimpleQueue()
        threading.Thread(target=self._run, name=name, daemon=True).start()

    def submit(self, fn: Callable, *args) -> Future:
        f: Future = Future()
        self._q.put((f, fn, args))
        return f

    def shutdown(self) -> None:
        self._q.put(None)

    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            f, fn, args = item
            if not f.set_running_or_notify_cancel():
                continue
            try:
                f.set_result(fn(*args))
            except BaseException as e:      # noqa: BLE001 - delivered as the result's error
                f.set_exception(e)


class _Job:
    __slots__ = ("ctx", "kind", "future", "submitted", "abandoned")

    def __init__(self, ctx: ReadContext, kind: str, submitted: float):
        self.ctx, self.kind, self.submitted = ctx, kind, submitted
        self.future: Optional[Future] = None
        self.abandoned = False


class _Worker:
    """One of a lane's two threads: what runs on it now, and the one context waiting for it."""

    def __init__(self, kind: str):
        self.kind = kind
        self.thread: Optional[_OneThread] = None
        self.job: Optional[_Job] = None
        self.queued: Optional[ReadContext] = None


class _Lane:
    def __init__(self, hwnd: int, pid: int, now: float):
        self.hwnd, self.pid = hwnd, pid
        self.content, self.scope = _Worker(CONTENT), _Worker(SCOPE)
        self.last_used = now
        self.background = False
        self.page_ms: Dict[str, float] = {}       # page URL -> how long its last read took

    def workers(self) -> Tuple[_Worker, _Worker]:
        return self.content, self.scope

    def idle(self) -> bool:
        return all(w.job is None and w.queued is None for w in self.workers())


def _default_in_scope(url: str) -> bool:
    from . import vsdc_scope
    return vsdc_scope.is_in_scope_url(url)


class PageReader:
    """The reader. request() and results() are called from the capture thread and never block
    on the browser; everything that can block runs on the lanes' worker threads."""

    def __init__(self, backend: Optional[Backend] = None, clock: Callable[[], float] = time.monotonic,
                 diag: Callable[[str], None] = vsdc_diag.note, in_scope: Optional[Callable[[str], bool]] = None):
        self._backend = backend if backend is not None else UiaBackend()
        self._clock = clock
        self._diag = diag
        self._in_scope = in_scope or _default_in_scope
        self._lock = threading.RLock()      # a finished future's callback may run inside request()
        self._lanes: Dict[int, _Lane] = {}
        self._done: Deque[ReadResult] = deque()
        self._wedged: List[_Job] = []
        self._paused_until = 0.0
        self._closed = False
        self._tls = threading.local()       # per worker thread: backend initialised or not
        self._abandoned = 0
        self._stats: Dict[str, Any] = {}
        self._reset_interval_stats()

    # ---- the capture thread's side ----------------------------------------------------------

    def request(self, ctx: ReadContext) -> str:
        """'started' (a worker took it), 'queued' (that worker is busy, or R9 holds it: ctx
        replaces any earlier queued ctx of the same kind), 'paused' (UIA paused), 'no-lane'
        (MAX_LANES reached and this is a background window)."""
        with self._lock:
            if self._closed:
                return "no-lane"
            now = self._clock()
            self._sweep(now)
            if self._paused_until:
                self._stats["paused_requests"] += 1
                return "paused"
            lane = self._lanes.get(ctx.hwnd)
            if lane is None:
                if len(self._lanes) >= MAX_LANES:
                    if ctx.background:
                        self._stats["no_lane"] += 1
                        return "no-lane"
                    self._evict()
                lane = self._lanes[ctx.hwnd] = _Lane(ctx.hwnd, self._pid(ctx.hwnd), now)
            lane.last_used = now
            lane.background = ctx.background
            worker = lane.scope if ctx.reason == REASON_SCOPE else lane.content
            if worker.queued is not None:
                self._stats["queued_replaced"] += 1     # newer wins; same kind only (R3)
            worker.queued = ctx
            if any(c is ctx for c in self._pump(now)):
                return "started"
            if worker.job is None:
                self._stats["held"] += 1                # R9: waits for the same browser's other read
            return "queued"

    def results(self) -> List[ReadResult]:
        """Every finished read, oldest first, drained. Never blocks. A result older than
        RESULT_MAX_AGE_SEC is counted and dropped."""
        with self._lock:
            now = self._clock()
            self._sweep(now)
            self._pump(now)
            out = list(self._done)
            self._done.clear()
        kept = []
        for r in out:
            if now - (r.ctx.asked_at or r.started_at) > RESULT_MAX_AGE_SEC:
                with self._lock:
                    self._stats["too_old"] += 1
                self._diag_safe(f"reader dropped a result {now - (r.ctx.asked_at or r.started_at):.0f} s old "
                                f"({r.error or 'ok'})")
                continue
            kept.append(r)
        return kept

    def inflight(self, hwnd: int) -> Optional[ReadContext]:
        """The page read running for this window now (None when its content worker is free)."""
        with self._lock:
            lane = self._lanes.get(hwnd)
            job = lane.content.job if lane else None
            return job.ctx if job is not None and not job.abandoned else None

    def page_ms(self, hwnd: int, url: str) -> float:
        """How long the last read of this page in this window took (0.0 if never read)."""
        with self._lock:
            lane = self._lanes.get(hwnd)
            return lane.page_ms.get(url, 0.0) if lane else 0.0

    def paused(self) -> bool:
        with self._lock:
            return bool(self._paused_until)

    def close_lane(self, hwnd: int) -> None:
        """The window is gone (or no longer watched): its lane and threads go; a read still running
        comes back marked "timeout-abandoned"."""
        with self._lock:
            lane = self._lanes.pop(hwnd, None)
            if lane is not None:
                self._retire(lane)

    def stats(self, reset: bool = False) -> Dict[str, Any]:
        """For the heartbeat and health: reads (page reads finished), scope_reads, max_ms, last_ms,
        late, queued_replaced, held (R9), paused_requests, no_lane, too_old since the last reset;
        abandoned (this run), stuck (now), paused ("yes" while paused), lanes (now)."""
        with self._lock:
            out = dict(self._stats)
            out["max_ms"] = round(out["max_ms"])
            out["last_ms"] = round(out["last_ms"])
            out.update(abandoned=self._abandoned, stuck=self._stuck(),
                       paused="yes" if self._paused_until else None, lanes=len(self._lanes))
            if reset:
                self._reset_interval_stats(keep_last=self._stats["last_ms"])
            return out

    def close(self) -> None:
        """Shutdown. Threads still wedged in a UIA call are daemons and are left behind."""
        with self._lock:
            self._closed = True
            for lane in list(self._lanes.values()):
                self._retire(lane)
            self._lanes.clear()

    # ---- inside the lock --------------------------------------------------------------------

    def _reset_interval_stats(self, keep_last: float = 0.0) -> None:
        self._stats = {"reads": 0, "scope_reads": 0, "max_ms": 0.0, "last_ms": keep_last, "late": 0,
                       "queued_replaced": 0, "held": 0, "paused_requests": 0, "no_lane": 0, "too_old": 0}

    def _pid(self, hwnd: int) -> int:
        try:
            return int(self._backend.process_id(hwnd) or 0)
        except Exception:
            return 0

    def _stuck(self) -> int:
        self._wedged[:] = [j for j in self._wedged if j.future is not None and not j.future.done()]
        return len(self._wedged)

    def _sweep(self, now: float) -> None:
        """Wedged reads abandoned, the pause ended or started, idle lanes closed."""
        if self._paused_until and now >= self._paused_until:
            self._paused_until = 0.0
            self._diag_safe(f"reader pause over - reading again ({self._stuck()} worker(s) still stuck)")
        wedged_now = False
        for lane in list(self._lanes.values()):
            for worker in lane.workers():
                job = worker.job
                if job is not None and not job.future.done() and now - job.submitted >= WEDGE_SEC:
                    wedged_now = True
                    self._abandon(worker, job)
                    self._abandoned += 1
                    self._wedged.append(job)
                    self._diag_safe(f"reader {worker.kind} read stuck {now - job.submitted:.0f} s - replaced "
                                    f"its worker ({self._abandoned} this run, {self._stuck()} still stuck)")
            if now - lane.last_used >= LANE_IDLE_SEC and lane.idle():
                self._lanes.pop(lane.hwnd, None)
                self._retire(lane)
        # Only a new wedge can pause: after a pause, reads are tried again even if old ones are still stuck.
        if wedged_now and not self._paused_until and self._stuck() >= MAX_WEDGED_WORKERS:
            self._paused_until = now + PAUSE_SEC
            self._diag_safe(f"{len(self._wedged)} reader workers stuck - UIA reads paused for "
                            f"{PAUSE_SEC / 60:.0f} min (OCR still works)")

    def _abandon(self, worker: _Worker, job: _Job) -> None:
        """Leave a running job behind: its thread is let go (a new one is made for the next job,
        and the queued context is carried to it); its result is marked when it comes back."""
        job.abandoned = True
        if worker.job is job:
            worker.job = None
        if worker.thread is not None:
            worker.thread.shutdown()
            worker.thread = None

    def _retire(self, lane: _Lane) -> None:
        for worker in lane.workers():
            worker.queued = None
            if worker.job is not None:
                self._abandon(worker, worker.job)
            elif worker.thread is not None:
                worker.thread.shutdown()
                worker.thread = None

    def _evict(self) -> None:
        """MAX_LANES reached and a window in front needs a lane: the least recently used
        background lane goes (else the least recently used lane)."""
        lanes = sorted(self._lanes.values(), key=lambda l: (not l.background, l.last_used))
        if lanes:
            self._lanes.pop(lanes[0].hwnd, None)
            self._retire(lanes[0])

    def _heavy(self, lane: _Lane, ctx: ReadContext) -> bool:
        return lane.page_ms.get(ctx.url, 0.0) > HEAVY_READ_MS

    def _same_browser_busy(self, lane: _Lane) -> bool:
        """R9: another window of this browser process has a page read running."""
        if not lane.pid:
            return False
        return any(o is not lane and o.pid == lane.pid and o.content.job is not None
                   for o in self._lanes.values())

    def _pump(self, now: float) -> List[ReadContext]:
        """Start every queued context whose worker is free, oldest first. Returns those started."""
        started: List[ReadContext] = []
        if self._closed or self._paused_until:
            return started
        waiting = [(w.queued.asked_at, w.queued.seq, lane, w) for lane in self._lanes.values()
                   for w in lane.workers() if w.queued is not None and w.job is None]
        for _at, _seq, lane, worker in sorted(waiting, key=lambda t: (t[0], t[1])):
            ctx = worker.queued
            if ctx is None or worker.job is not None or self._lanes.get(lane.hwnd) is not lane:
                continue
            if worker.kind == CONTENT and self._heavy(lane, ctx) and self._same_browser_busy(lane):
                continue
            self._start(lane, worker, ctx, now)
            started.append(ctx)
        return started

    def _start(self, lane: _Lane, worker: _Worker, ctx: ReadContext, now: float) -> None:
        if worker.thread is None:
            worker.thread = _OneThread(f"sgt-read-{worker.kind}-{lane.hwnd}")
        job = _Job(ctx, worker.kind, now)
        worker.queued, worker.job = None, job
        run = self._content_job if worker.kind == CONTENT else self._scope_job
        job.future = worker.thread.submit(run, ctx)
        job.future.add_done_callback(lambda f, l=lane, w=worker, j=job: self._finished(l, w, j, f))

    def _finished(self, lane: _Lane, worker: _Worker, job: _Job, future: Future) -> None:
        try:
            result = future.result()
        except BaseException as e:      # noqa: BLE001 - a job never raises; this is belt and braces
            t = self._clock()
            result = ReadResult(job.ctx, started_at=t, finished_at=t, error=f"exception:{type(e).__name__}")
        with self._lock:
            if job.abandoned:
                result.error = "timeout-abandoned"
                self._stuck()
            else:
                if worker.job is job:
                    worker.job = None
                if job.kind == CONTENT:
                    self._count_read(lane, result)
                else:
                    self._stats["scope_reads"] += 1
                if result.error == "no-window" and self._lanes.get(lane.hwnd) is lane:
                    self._lanes.pop(lane.hwnd, None)
                    self._retire(lane)
            self._done.append(result)
            self._pump(self._clock())

    def _count_read(self, lane: _Lane, r: ReadResult) -> None:
        ms = r.ms
        self._stats["reads"] += 1
        self._stats["last_ms"] = ms
        self._stats["max_ms"] = max(self._stats["max_ms"], ms)
        if r.late:
            self._stats["late"] += 1
        if not r.error:
            if len(lane.page_ms) >= PAGE_MS_KEPT and r.ctx.url not in lane.page_ms:
                lane.page_ms.pop(next(iter(lane.page_ms)))
            lane.page_ms[r.ctx.url] = ms

    def _diag_safe(self, text: str) -> None:
        try:
            self._diag(text)
        except Exception:
            pass

    # ---- on a lane's worker thread ----------------------------------------------------------

    def _thread_ready(self) -> bool:
        ready = getattr(self._tls, "ready", None)
        if ready is None:
            try:
                self._backend.init_thread()
                ready = True
            except Exception as e:
                ready = False
                self._diag_safe(f"reader UI Automation init failed: {type(e).__name__}")
            self._tls.ready = ready
        return ready

    def _locate(self, hwnd: int, root: Any) -> Tuple[str, List[Tuple[Any, str]], str]:
        """(the page's URL, the Documents that may be read with the URL each is judged by, the rule).

        Document first (D1): the on-screen Documents whose own URL is in scope. Only when none is,
        and at least one on-screen Document gives no URL (a browser that exposes none, a page still
        loading) or there is none at all, the address bar decides - and then only those URL-less
        Documents may be read: a Document whose own URL is off the portals never is."""
        docs = [(el, url) for el, url, offscreen in self._backend.documents(root) if not offscreen]
        in_scope = [(el, url) for el, url in docs if url and self._in_scope(url)]
        if in_scope:
            return in_scope[0][1], in_scope, "document"
        unnamed = [(el, url) for el, url in docs if not url]
        if docs and not unnamed:
            return docs[0][1], [], ""
        bar = self._backend.address_bar(hwnd, root) or ""
        if bar and self._in_scope(bar):
            return bar, [(el, bar) for el, _url in unnamed], "address_bar"
        return bar or (docs[0][1] if docs else ""), [], ""

    def _begin(self, ctx: ReadContext) -> Tuple[ReadResult, Any]:
        r = ReadResult(ctx, started_at=self._clock())
        if not self._thread_ready():
            r.error = "uia-init"
            return r, None
        root = self._backend.root(ctx.hwnd)
        if root is None:
            r.error = "no-window"
        return r, root

    def _scope_job(self, ctx: ReadContext) -> ReadResult:
        """URL-only: the tab id and the page's URL. No page content is fetched."""
        r, root = self._begin(ctx)
        try:
            if root is not None:
                r.tab_before = r.tab_after = self._backend.tab_id(ctx.hwnd, root)
                url, to_read, r.scope_rule = self._locate(ctx.hwnd, root)
                r.url_before = r.url_after = url
                r.doc_urls = [u for _el, u in to_read]
        except Exception as e:
            r.error = f"exception:{type(e).__name__}"
        r.finished_at = self._clock()
        return r

    def _content_job(self, ctx: ReadContext) -> ReadResult:
        """Tab and URL, the in-scope Documents (one cached call each), tab and URL again."""
        r, root = self._begin(ctx)
        try:
            if root is not None:
                r.tab_before = self._backend.tab_id(ctx.hwnd, root)
                r.url_before, to_read, r.scope_rule = self._locate(ctx.hwnd, root)
                for element, url in to_read:
                    r.docs.append(self._backend.read_document(element))
                    r.doc_urls.append(url)
                r.lines = uia_nodes.lines_from_nodes(r.docs, include_selection=True)
                r.tab_after = self._backend.tab_id(ctx.hwnd, root)
                r.url_after = self._locate(ctx.hwnd, root)[0]
        except Exception as e:
            r.error = f"exception:{type(e).__name__}"
        r.finished_at = self._clock()
        return r
