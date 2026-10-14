"""
tools/sgt_probe_pack.py - the measurements only a Windows PC with a real browser can take
==========================================================================================
Standalone diagnostic, like tools/sgt_i_key_probe.py. NOT wired into the app. Blueprint:
docs/sgt-never-miss-blueprint.md section 9 (WP0), section 12 (the table these numbers fill),
and review rules R9 and R10 in section 10a.

Strictly passive: it only reads (UI Automation properties, a screenshot of the window in front).
It never clicks, types, scrolls, focuses or submits. A page is read only when its address is on
the scope allow-list (core/vsdc/vsdc_scope.is_in_scope_url); other windows are only counted.

Six probes, each appends one section to ONE report per day:

    doc-url                   Does the Document element's Value carry the page URL (D1)?
    read-timing               How long a real page read takes, and does it drift over hours?
    read-timing --mta         The same with the read worker in a multithreaded apartment (D2)
    read-timing --two-windows Two windows of one browser read at once vs one at a time (R9)
    gate                      Cost of a frame tick, and how many 32x32 cells typing changes (D4, D7, R10)
    toast                     How long an ARN stays visible to UIA vs OCR at a filing (C1 to C3)
    switches                  Foreground and tab switches per minute (harness profile 1)

Usage (the browser must show a portal page; the probe waits --delay seconds so you can switch):

    python tools/sgt_probe_pack.py doc-url
    python tools/sgt_probe_pack.py read-timing --minutes 240
    python tools/sgt_probe_pack.py read-timing --minutes 240 --mta
    python tools/sgt_probe_pack.py read-timing --two-windows --minutes 10
    python tools/sgt_probe_pack.py gate --minutes 5
    python tools/sgt_probe_pack.py toast --minutes 20
    python tools/sgt_probe_pack.py switches --minutes 60

Report: ~/AmanAssociates_Sera/logs/probe_pack_<date>.txt (outside the repo). It holds numbers,
counts and redacted samples only: no page text beyond the 20 redacted PDF lines, no window titles,
no links except portal addresses without their query, and nothing shaped like a PAN, GSTIN or ARN
(every line is redacted before it is written). Ctrl+C ends a probe early and still writes its summary.
"""

import argparse
import ctypes
import os
import platform
import re
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, NamedTuple, Optional, Sequence, Set, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# The blueprint's gate numbers (section 4.4). core/vsdc/vsdc_gate.py does not exist yet; WP7 builds
# it from the values this probe confirms.
GATE_SIZE = 32
GATE_LEVELS = 16
GATE_MIN_CELLS = 3
FLASH_MIN_CELLS = 8
GATE_LEVEL_DELTA = 2
FRAME_TICK_SEC = 0.2
HIST_BUCKETS = ((0, 0), (1, 1), (2, 2), (3, 4), (5, 7), (8, 15), (16, 63), (64, GATE_SIZE * GATE_SIZE))

READ_TIMEOUT_SEC = 20.0
READ_EVERY_SEC = 30.0
TOAST_TICK_SEC = 0.1
SWITCH_POLL_SEC = 0.25
PDF_SAMPLE_LINES = 20
SAMPLE_CLIP = 60
SLOW_PAGE_MS = 150.0            # two-windows: pages quicker than this cannot show serialisation

# UIA ids (UIAutomationClient.h), as in core/sgt_i/uia_nodes.py.
PID_CONTROL_TYPE = 30003
PID_IS_OFFSCREEN = 30022
PID_FRAMEWORK_ID = 30024
PID_VALUE = 30045
CT_DOCUMENT = 50030
_SCOPE_ELEMENT = 1
_SCOPE_DESCENDANTS = 4

_ARN_RE = re.compile(r"\b[A-Z]{2}\d{11,14}[A-Z0-9]?\b", re.IGNORECASE)
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.IGNORECASE)


# ---- pure helpers (tested) --------------------------------------------------------------------

def median(values: Sequence[float]) -> float:
    return statistics.median(values) if values else 0.0


def pctl(values: Sequence[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, max(0, round(p / 100 * (len(s) - 1))))]


def is_url_shaped(value: str) -> bool:
    return bool(_SCHEME_RE.match((value or "").strip()))


def _norm_url(url: str) -> str:
    u = _SCHEME_RE.sub("", (url or "").strip().lower())
    u = u.split("?", 1)[0]
    return u[4:] if u.startswith("www.") else u.rstrip("/")


def urls_agree(a: str, b: str) -> bool:
    """The address bar drops "https://" and the Document keeps it; compare without scheme, query
    and trailing slash."""
    return bool(a and b) and _norm_url(a).rstrip("/") == _norm_url(b).rstrip("/")


def describe_url(url: Optional[str], in_scope: Optional[bool] = None) -> str:
    """A URL as it may appear in the report: a portal address without its query; anything else only
    as its shape (never the host or path)."""
    if not url:
        return "none"
    from core.vsdc import vsdc_scope
    if in_scope is None:
        in_scope = vsdc_scope.is_in_scope_url(url)
    if in_scope:
        return vsdc_scope.sanitize_page_url(url) or "[portal address, unreadable]"
    kind = "url" if is_url_shaped(url) else "not a url"
    return f"[out of scope: {kind}, {len(url)} chars]"


def arn_tokens(lines: Iterable[str]) -> Set[str]:
    out: Set[str] = set()
    for ln in lines:
        out.update(m.group(0).upper() for m in _ARN_RE.finditer(ln or ""))
    return out


def frame_signature(img: Any) -> Optional[bytes]:
    """A 32x32 grid of 16-level greys (blueprint 4.4); None when the picture cannot be converted."""
    try:
        from PIL import Image
        small = img.convert("L").resize((GATE_SIZE, GATE_SIZE), Image.BOX)
        step = 256 // GATE_LEVELS
        return small.point([v // step for v in range(256)]).tobytes()
    except Exception:
        return None


def cells_changed(a: Optional[bytes], b: Optional[bytes]) -> int:
    if a is None or b is None or len(a) != len(b):
        return GATE_SIZE * GATE_SIZE
    return sum(1 for x, y in zip(a, b) if abs(x - y) >= GATE_LEVEL_DELTA)


def legacy_hash(img: Any) -> Optional[int]:
    """Today's gate: hash of the picture shrunk to 32x32 RGB (core/vsdc/vsdc247._frame_hash)."""
    try:
        return hash(img.convert("RGB").resize((32, 32)).tobytes())
    except Exception:
        return None


def _bucket_label(lo: int, hi: int) -> str:
    return str(lo) if lo == hi else f"{lo}-{hi}"


class GateStats:
    """Frame-tick results grouped per minute: cost, histogram of changed cells, and how many frames
    each threshold would have passed."""

    def __init__(self) -> None:
        self.minutes: Dict[int, List[Tuple[float, float, Optional[int], bool, float]]] = {}
        self.skipped = 0

    def add(self, t: float, shot_ms: float, sig_ms: float, cells: Optional[int], legacy_fired: bool) -> None:
        self.minutes.setdefault(int(t // 60), []).append((shot_ms, sig_ms, cells, legacy_fired, t))

    @staticmethod
    def _lines(label: str, rows: List[Tuple[float, float, Optional[int], bool, float]]) -> List[str]:
        shots = [r[0] for r in rows]
        sigs = [r[1] for r in rows]
        cells = [r[2] for r in rows if r[2] is not None]
        hist = []
        for lo, hi in HIST_BUCKETS:
            hist.append(f"{_bucket_label(lo, hi)}:{sum(1 for c in cells if lo <= c <= hi)}")
        return [
            f"{label}: frames={len(rows)}  screenshot ms median/p95/max="
            f"{median(shots):.1f}/{pctl(shots, 95):.1f}/{max(shots, default=0):.1f}  signature ms "
            f"median/p95/max={median(sigs):.1f}/{pctl(sigs, 95):.1f}/{max(sigs, default=0):.1f}",
            f"    frame tick ms (screenshot+signature) median={median([a + b for a, b in zip(shots, sigs)]):.1f}"
            f"  p95={pctl([a + b for a, b in zip(shots, sigs)], 95):.1f}",
            f"    cells changed histogram  {'  '.join(hist)}",
            f"    frames >= GATE_MIN_CELLS({GATE_MIN_CELLS}): {sum(1 for c in cells if c >= GATE_MIN_CELLS)}"
            f"   >= FLASH_MIN_CELLS({FLASH_MIN_CELLS}): {sum(1 for c in cells if c >= FLASH_MIN_CELLS)}"
            f"   legacy 32x32 hash() fired: {sum(1 for r in rows if r[3])}",
        ]

    def minute_lines(self, minute: int) -> List[str]:
        return self._lines(f"minute {minute + 1}", self.minutes.get(minute, []))

    def total_lines(self) -> List[str]:
        rows = [r for m in sorted(self.minutes) for r in self.minutes[m]]
        out = self._lines("TOTAL", rows) if rows else ["TOTAL: no frames (no portal page was in front)"]
        biggest = sorted((r for r in rows if r[2] is not None), key=lambda r: -r[2])[:5]
        if biggest:
            first = rows[0][4]
            out.append("    largest changes (seconds since start: cells): "
                       + "  ".join(f"+{r[4] - first:.1f}s: {r[2]}" for r in biggest))
        if self.skipped:
            out.append(f"    ticks with no portal page in front: {self.skipped}")
        return out


class Span(NamedTuple):
    first: float
    last: float


def first_run_span(samples: Sequence[Tuple[float, bool]]) -> Optional[Span]:
    """The first unbroken run of hits in time-ordered (time, hit) samples."""
    first = last = None
    for t, hit in samples:
        if hit:
            if first is None:
                first = t
            last = t
        elif first is not None:
            break
    return None if first is None else Span(first, last)


def arn_visibility(uia: Sequence[Tuple[float, frozenset]], ocr: Sequence[Tuple[float, frozenset]],
                   flashes: Sequence[Tuple[float, str, frozenset]], t0: float) -> List[str]:
    """How long each ARN-shaped token was visible to each channel. Tokens are numbered in order of
    first sight and never printed; an OCR misread therefore shows up as its own token."""
    tokens: Dict[str, float] = {}
    for channel in (uia, ocr):
        for t, found in channel:
            for tok in found:
                tokens[tok] = min(tokens.get(tok, t), t)
    for t, _kind, found in flashes:
        for tok in found:
            tokens[tok] = min(tokens.get(tok, t), t)
    if not tokens:
        return ["no ARN-shaped token was seen by any channel (a filing must be completed during the run)"]

    def interval(channel: Sequence[Tuple[float, frozenset]]) -> float:
        return median([b[0] - a[0] for a, b in zip(channel, channel[1:])])

    out = []
    for n, tok in enumerate(sorted(tokens, key=lambda k: tokens[k]), 1):
        parts = []
        for name, channel in (("UIA", uia), ("OCR", ocr)):
            span = first_run_span([(t, tok in found) for t, found in channel])
            if span is None:
                parts.append(f"{name}: never seen")
            else:
                parts.append(f"{name}: first +{span.first - t0:.1f}s, visible {span.last - span.first:.2f}s "
                             f"(+- {interval(channel):.2f}s sample interval)")
        hits = [t for t, _k, found in flashes if tok in found]
        parts.append(f"FlashWatcher: caught +{min(hits) - t0:.1f}s" if hits else "FlashWatcher: did not catch it")
        out.append(f"ARN-{n}: " + "; ".join(parts))
    return out


def read_summary(rows: List[Dict[str, Any]]) -> List[str]:
    """The end-of-run numbers of read-timing from its sample rows."""
    ok = [r for r in rows if r["status"] == "ok"]
    out = [f"samples={len(rows)}  ok={len(ok)}  "
           + "  ".join(f"{k}={sum(1 for r in rows if r['status'] == k)}" for k in ("busy", "empty", "timeout"))]
    for key, label in (("ms_text", "read_page_text (element by element)"), ("ms_nodes", "read_page_nodes (cached)")):
        v = [r[key] for r in ok]
        if not v:
            continue
        line = f"{label}: ms median/p95/max = {median(v):.0f}/{pctl(v, 95):.0f}/{max(v):.0f}"
        q = max(1, len(v) // 4)
        if len(v) >= 8:
            early, late = median(v[:q]), median(v[-q:])
            line += f"   first quarter {early:.0f} -> last quarter {late:.0f} ({late / early:.2f}x)" if early else ""
        out.append(line)
    if ok:
        out.append(f"elements median/max = {median([r['elements'] for r in ok]):.0f}/{max(r['elements'] for r in ok)}"
                   f"   lines median/max = {median([r['lines_text'] for r in ok]):.0f}/{max(r['lines_text'] for r in ok)}"
                   f"   identical lines: {sum(1 for r in ok if r['identical'])}/{len(ok)}")
    ws = [r["ws_mb"] for r in rows if r.get("ws_mb") is not None]
    if ws:
        out.append(f"browser process working set MB first/last/max = {ws[0]:.0f}/{ws[-1]:.0f}/{max(ws):.0f}")
    return out


def serialisation_verdict(rounds: List[Dict[str, float]]) -> List[str]:
    """R9: compare the wall time of two reads started together with the two read one after another.
    ratio = concurrent wall / (sequential sum): 1.0 = the browser answered them one at a time."""
    if not rounds:
        return ["no complete round"]
    seq = [r["seq_sum_ms"] for r in rounds]
    conc = [r["conc_wall_ms"] for r in rounds]
    ratios = [c / s for c, s in zip(conc, seq) if s]
    ratio = median(ratios)
    out = [f"rounds={len(rounds)}  one at a time (sum of both) median={median(seq):.0f} ms  "
           f"both at once (wall) median={median(conc):.0f} ms  ratio={ratio:.2f}"]
    if median(seq) < SLOW_PAGE_MS:
        out.append(f"verdict: pages too light to tell (a pair reads in under {SLOW_PAGE_MS:.0f} ms); "
                   "repeat on the heaviest pages (GSTR-1 B2B with hundreds of rows)")
    elif ratio >= 0.85:
        out.append("verdict: SERIALISED - the browser answers one window at a time, so R9 holds "
                   "(at most one heavy read per browser process)")
    elif ratio <= 0.65:
        out.append("verdict: PARALLEL - two windows of one browser are read at the same time; R9 is not needed")
    else:
        out.append("verdict: PARTLY PARALLEL - keep R9 but note the ratio in section 12")
    return out


class SwitchCounter:
    """Foreground-window and tab changes per minute. Feeds are (time, foreground hwnd, is it a
    browser, its selected tab id)."""

    FIELDS = ("fg", "browser_window", "in_out_of_browser", "tab")

    def __init__(self) -> None:
        self.minutes: Dict[int, Dict[str, int]] = {}
        self._hwnd = 0
        self._browser = False
        self._tabs: Dict[int, str] = {}

    def _m(self, t: float) -> Dict[str, int]:
        return self.minutes.setdefault(int(t // 60), dict.fromkeys(self.FIELDS, 0))

    def feed(self, t: float, hwnd: int, is_browser: bool, tab_id: Optional[str]) -> None:
        m = self._m(t)
        if hwnd and self._hwnd and hwnd != self._hwnd:
            m["fg"] += 1
            if is_browser and self._browser:
                m["browser_window"] += 1
            elif is_browser != self._browser:
                m["in_out_of_browser"] += 1
        if hwnd:
            self._hwnd, self._browser = hwnd, is_browser
        if is_browser and tab_id:
            before = self._tabs.get(hwnd)
            if before is not None and before != tab_id:
                m["tab"] += 1
            self._tabs[hwnd] = tab_id

    def lines(self) -> List[str]:
        out = []
        for minute in sorted(self.minutes):
            m = self.minutes[minute]
            out.append(f"minute {minute + 1}: foreground changes={m['fg']}  between browser windows={m['browser_window']}"
                       f"  into/out of a browser={m['in_out_of_browser']}  tab changes={m['tab']}")
        if self.minutes:
            for f, label in (("fg", "foreground changes"), ("tab", "tab changes")):
                v = [m[f] for m in self.minutes.values()]
                out.append(f"TOTAL {label}: {sum(v)}  per minute median/max = {median(v):.0f}/{max(v)}")
        return out


# ---- the report -------------------------------------------------------------------------------

def default_report_path(now: Optional[datetime] = None) -> Path:
    now = now or datetime.now()
    return Path.home() / "AmanAssociates_Sera" / "logs" / f"probe_pack_{now:%Y%m%d}.txt"


class Report:
    """One text file per day; every probe appends a section. Each line is redacted on its way in
    (PAN, GSTIN, ARN and long numbers) and written at once, so a Ctrl+C loses nothing."""

    def __init__(self, path: Path, echo: Callable[[str], None] = lambda s: print(s, flush=True)) -> None:
        self.path = Path(path)
        self._echo = echo
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def line(self, text: Any = "") -> None:
        from core.vsdc.vsdc_alerts import redact_identifiers
        text = redact_identifiers(str(text))
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(text + "\n")
        self._echo(text)

    def section(self, title: str) -> None:
        self.line()
        self.line("=" * 100)
        self.line(f"{title}   [{datetime.now():%Y-%m-%d %H:%M:%S}]")
        self.line("=" * 100)
        self.line(f"python {platform.python_version()}  {platform.platform()}  cpus={os.cpu_count()}")


# ---- Windows plumbing -------------------------------------------------------------------------

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class _MemoryCounters(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


class Win(NamedTuple):
    hwnd: int
    exe: str
    pid: int
    url: str = ""


def window_process(hwnd: int) -> Tuple[int, str]:
    """(pid, exe name in lower case) of a window; (0, "") when it cannot be told."""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)      # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return pid.value, ""
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = wintypes.DWORD(512)
        ok = kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        return pid.value, (buf.value.split("\\")[-1].lower() if ok else "")
    finally:
        kernel32.CloseHandle(h)


def working_set_mb(pid: int) -> Optional[float]:
    try:
        import psutil
        return psutil.Process(pid).memory_info().rss / 1048576
    except ImportError:
        pass
    except Exception:
        return None
    h = kernel32.OpenProcess(0x1000 | 0x0010, False, pid)  # QUERY_LIMITED_INFORMATION | VM_READ
    if not h:
        return None
    try:
        counters = _MemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        ok = psapi.GetProcessMemoryInfo(h, ctypes.byref(counters), counters.cb)
        return counters.WorkingSetSize / 1048576 if ok else None
    finally:
        kernel32.CloseHandle(h)


def browser_names() -> Tuple[str, ...]:
    from core.vsdc.vsdc_router import BROWSER_EXE_NAMES
    return tuple(BROWSER_EXE_NAMES)


def browser_windows() -> List[Win]:
    """Visible, titled, not minimised top-level windows of a browser."""
    names = browser_names()
    found: List[Win] = []

    def visit(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            pid, exe = window_process(hwnd)
            if exe in names:
                found.append(Win(int(hwnd), exe, pid))
        return True

    user32.EnumWindows(_WNDENUMPROC(visit), 0)
    return found


def window_shape(hwnd: int) -> str:
    """Size, whether it covers its whole monitor (F11 / kiosk) and whether it has a title bar
    (a popup window usually has no address bar)."""
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))

    class _MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD)]

    info = _MonitorInfo()
    info.cbSize = ctypes.sizeof(info)
    monitor = user32.MonitorFromWindow(hwnd, 2)             # MONITOR_DEFAULTTONEAREST
    full = bool(monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info))
                and (rect.left, rect.top, rect.right, rect.bottom) ==
                (info.rcMonitor.left, info.rcMonitor.top, info.rcMonitor.right, info.rcMonitor.bottom))
    style = user32.GetWindowLongW(hwnd, -16) & 0xFFFFFFFF   # GWL_STYLE
    caption = (style & 0x00C00000) == 0x00C00000            # WS_CAPTION
    cls = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(hwnd, cls, 128)
    return (f"class={cls.value}  size={rect.right - rect.left}x{rect.bottom - rect.top}  "
            f"covers-monitor={'yes' if full else 'no'}  title-bar={'yes' if caption else 'no'}")


class ProbeTimeout(Exception):
    pass


class ComCtx:
    """What a COM worker thread owns: its own UIA objects (apartment-bound, so never shared)."""

    def __init__(self) -> None:
        self._uia = None
        self._client = None
        self._router = None

    def uia_objects(self) -> Tuple[Any, Any]:
        if self._uia is None:
            import comtypes.client
            self._client = comtypes.client.GetModule("UIAutomationCore.dll")
            self._uia = comtypes.client.CreateObject(self._client.CUIAutomation,
                                                     interface=self._client.IUIAutomation)
        return self._uia, self._client

    def router(self) -> Any:
        """A VSDCRouter with only its address-bar reader set up, so doc-url can print what
        extract_browser_url returns without starting the router's engines."""
        if self._router is None:
            from core.vsdc.vsdc_router import VSDCRouter
            r = VSDCRouter.__new__(VSDCRouter)
            r._cached_address_elements = {}
            r._uia = None
            r._init_uia()
            self._router = r
        return self._router


class ComWorker:
    """One thread with its own COM apartment (STA like the app's read worker, or MTA). A call that
    times out abandons the thread, like the app does; the next call gets a fresh one."""

    def __init__(self, mta: bool = False) -> None:
        self._mta = mta
        self._local = threading.local()
        self._pool: Optional[ThreadPoolExecutor] = None

    def _init(self) -> None:
        import comtypes
        if self._mta:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        else:
            comtypes.CoInitialize()
        self._local.ctx = ComCtx()

    def submit(self, fn: Callable[[ComCtx], Any]):
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=1, initializer=self._init)
        return self._pool.submit(lambda: fn(self._local.ctx))

    def call(self, fn: Callable[[ComCtx], Any], timeout: float = READ_TIMEOUT_SEC) -> Any:
        future = self.submit(fn)
        try:
            return future.result(timeout=timeout)
        except FutureTimeoutError:
            old, self._pool = self._pool, None
            old.shutdown(wait=False, cancel_futures=True)
            raise ProbeTimeout(f"no answer in {timeout:g} s") from None


def _cached(el: Any, pid: int) -> Any:
    try:
        return el.GetCachedPropertyValue(pid)
    except Exception:
        return None


def document_values(ctx: ComCtx, hwnd: int) -> List[Dict[str, Any]]:
    """Every Document element of a browser window with its cached Value, IsOffscreen and framework.
    No page content is fetched: the cache scope is the Document element itself."""
    uia, client = ctx.uia_objects()
    root = uia.ElementFromHandle(hwnd)
    if not root:
        return []
    request = uia.CreateCacheRequest()
    for pid in (PID_VALUE, PID_IS_OFFSCREEN, PID_FRAMEWORK_ID, PID_CONTROL_TYPE):
        request.AddProperty(pid)
    request.TreeScope = _SCOPE_ELEMENT
    request.AutomationElementMode = 0
    cond = uia.CreatePropertyCondition(client.UIA_ControlTypePropertyId, CT_DOCUMENT)
    found = root.FindAllBuildCache(_SCOPE_DESCENDANTS, cond, request)
    out = []
    for i in range(found.Length if found else 0):
        el = found.GetElement(i)
        value = _cached(el, PID_VALUE)
        out.append({"value": value.strip() if isinstance(value, str) else "",
                    "offscreen": bool(_cached(el, PID_IS_OFFSCREEN)),
                    "framework": _cached(el, PID_FRAMEWORK_ID) or ""})
    return out


def document_subtree_lines(ctx: ComCtx, hwnd: int, index: int, value: str, limit: int) -> Tuple[int, List[str]]:
    """(element count, first `limit` lines) of one Document's cached subtree, the way the app's
    reader lists it. Used for the PDF viewer sample only."""
    from core.sgt_i import uia_nodes
    uia, client = ctx.uia_objects()
    root = uia.ElementFromHandle(hwnd)
    cond = uia.CreatePropertyCondition(client.UIA_ControlTypePropertyId, CT_DOCUMENT)
    found = root.FindAllBuildCache(_SCOPE_DESCENDANTS, cond, uia_nodes.build_cache_request(uia)) if root else None
    if not found or index >= found.Length:
        return 0, []
    el = found.GetElement(index)
    now_value = _cached(el, PID_VALUE)
    if (now_value or "").strip() != value:
        return 0, []                       # the tab changed between the two reads
    nodes: List[Dict[str, Any]] = []
    uia_nodes._walk(el, -1, 0, nodes)
    return len(nodes), uia_nodes.lines_from_nodes([nodes], max_lines=limit)


def window_url(ctx: ComCtx, hwnd: int) -> str:
    """The address bar first (what the app does today), else the first on-screen Document's Value."""
    url = ctx.router().extract_browser_url(hwnd)
    if url:
        return url
    for d in document_values(ctx, hwnd):
        if not d["offscreen"] and is_url_shaped(d["value"]):
            return d["value"]
    return ""


def foreground_portal(worker: ComWorker) -> Optional[Win]:
    """The window in front when it is a browser showing a portal page, else None. Nothing else is
    ever read."""
    hwnd = int(user32.GetForegroundWindow() or 0)
    if not hwnd:
        return None
    pid, exe = window_process(hwnd)
    if exe not in browser_names():
        return None
    try:
        url = worker.call(lambda ctx: window_url(ctx, hwnd), timeout=5.0)
    except ProbeTimeout:
        return None
    from core.vsdc import vsdc_scope
    return Win(hwnd, exe, pid, url) if url and vsdc_scope.is_in_scope_url(url) else None


def _countdown(seconds: int) -> None:
    print(f"Switch to the portal page in the browser now - starting in {seconds}s...", flush=True)
    for left in range(seconds, 0, -1):
        print(f"  {left}...", flush=True)
        time.sleep(1)


def _sleep_until(t: float) -> None:
    gap = t - time.monotonic()
    if gap > 0:
        time.sleep(gap)


# ---- probe: doc-url ---------------------------------------------------------------------------

def probe_doc_url(args: argparse.Namespace, rep: Report) -> int:
    rep.section("doc-url: does the Document element's Value carry the page URL?")
    wins = browser_windows()
    if not wins:
        rep.line("No browser window is open. Open Edge, Chrome and Firefox on a portal page and run again.")
        return 1
    from core.vsdc import vsdc_scope
    worker = ComWorker(mta=False)
    verdict: Dict[str, List[int]] = {}      # exe -> [on-screen docs, url-shaped, in scope, bar agrees]
    for n, w in enumerate(wins, 1):
        rep.line("")
        rep.line(f"window {n}: {w.exe}  pid={w.pid}  hwnd=0x{w.hwnd:X}  {window_shape(w.hwnd)}")
        try:
            worker.call(lambda ctx: document_values(ctx, w.hwnd), timeout=15)       # wakes Chromium's tree
            docs = worker.call(lambda ctx: document_values(ctx, w.hwnd), timeout=15)
            bar = worker.call(lambda ctx: ctx.router().extract_browser_url(w.hwnd), timeout=15)
        except ProbeTimeout as e:
            rep.line(f"  TIMED OUT: {e}")
            continue
        counts = verdict.setdefault(w.exe, [0, 0, 0, 0])
        rep.line(f"  VSDCRouter.extract_browser_url (address bar): {describe_url(bar)}")
        if not docs:
            rep.line("  no Document element found")
        for i, d in enumerate(docs):
            shaped = is_url_shaped(d["value"])
            scope = bool(shaped and vsdc_scope.is_in_scope_url(d["value"]))
            agree = "n/a" if not (bar and shaped) else ("yes" if urls_agree(bar, d["value"]) else "NO")
            rep.line(f"  document {i + 1}: on-screen={'no' if d['offscreen'] else 'yes'}  framework={d['framework'] or '?'}"
                     f"  value={'empty' if not d['value'] else describe_url(d['value'], scope)}"
                     f"  url-shaped={'yes' if shaped else 'no'}  in-scope={'yes' if scope else 'no'}"
                     f"  agrees-with-address-bar={agree}")
            if not d["offscreen"]:
                counts[0] += 1
                counts[1] += shaped
                counts[2] += scope
                counts[3] += agree == "yes"
            if shaped and not args.no_pdf_sample and d["value"].split("?", 1)[0].split("#", 1)[0].lower().endswith(".pdf"):
                _pdf_sample(rep, worker, w.hwnd, i, d["value"])
    rep.line("")
    rep.line("SUMMARY per browser (on-screen documents only):")
    for exe, (docs_n, shaped_n, scope_n, agree_n) in sorted(verdict.items()):
        rep.line(f"  {exe}: documents={docs_n}  Value is a URL={shaped_n}  in scope={scope_n}  "
                 f"equal to the address bar={agree_n}")
    rep.line("Fill in blueprint section 12: per browser and per window kind (normal, F11, popup), "
             "whether 'Value is a URL' equals 'documents' and 'in scope' matches the portal page.")
    return 0


def _pdf_sample(rep: Report, worker: ComWorker, hwnd: int, index: int, value: str) -> None:
    try:
        count, lines = worker.call(lambda ctx: document_subtree_lines(ctx, hwnd, index, value, PDF_SAMPLE_LINES), 20)
    except ProbeTimeout as e:
        rep.line(f"    PDF viewer: TIMED OUT ({e})")
        return
    rep.line(f"    PDF viewer document: {count} elements in its subtree, text exposed through UIA: "
             f"{'YES' if lines else 'NO'}")
    for ln in lines:
        rep.line(f"      | {' '.join(ln.split())[:SAMPLE_CLIP]}")


# ---- probe: read-timing -----------------------------------------------------------------------

def _use_mta_pool() -> None:
    """Make the app's own read worker (vsdc_uia_text._POOL) an MTA thread, replacements included."""
    import comtypes
    from core.vsdc import vsdc_uia_text as u

    def init_mta():
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)

    u._init_com_on_worker_thread = init_mta
    with u._pool_lock:
        old = u._POOL
        u._POOL = ThreadPoolExecutor(max_workers=1, initializer=init_mta)
    old.shutdown(wait=False)


def _worker_apartment() -> str:
    from core.vsdc import vsdc_uia_text as u

    def ask():
        kind, qualifier = ctypes.c_int(), ctypes.c_int()
        hr = ctypes.windll.ole32.CoGetApartmentType(ctypes.byref(kind), ctypes.byref(qualifier))
        return {0: "STA", 1: "MTA", 2: "neutral", 3: "main STA"}.get(kind.value, "unknown") if hr == 0 else "unknown"

    return u._run_with_timeout(ask, 5.0) or "unknown"


def probe_read_timing(args: argparse.Namespace, rep: Report) -> int:
    if args.two_windows:
        return _two_windows(args, rep)
    from core.sgt_i import uia_nodes
    from core.vsdc import vsdc_uia_text as u
    if args.mta:
        _use_mta_pool()
    rep.section(f"read-timing{' --mta' if args.mta else ''}: {args.minutes} min, one read pair every {args.every:g} s")
    rep.line(f"read worker apartment: {_worker_apartment()}")
    rep.line("columns: minute | text ms | nodes ms | elements | lines | identical | browser MB | status")
    _countdown(args.delay)
    url_worker = ComWorker(mta=False)
    start = time.monotonic()
    end = start + args.minutes * 60
    rows: List[Dict[str, Any]] = []
    skipped = 0
    slot = start
    try:
        while time.monotonic() < end:
            win = foreground_portal(url_worker)
            if win is None:
                skipped += 1
            else:
                row = _read_pair(win, u, uia_nodes)
                row["minute"] = (time.monotonic() - start) / 60
                rows.append(row)
                rep.line(f"{row['minute']:7.1f} | {row['ms_text']:7.0f} | {row['ms_nodes']:7.0f} | {row['elements']:6d} | "
                         f"{row['lines_text']:5d} | {'same' if row['identical'] else 'DIFF'} | "
                         f"{row['ws_mb'] if row['ws_mb'] is not None else float('nan'):7.0f} | {row['status']}")
            slot += args.every
            _sleep_until(slot)
    except KeyboardInterrupt:
        rep.line("(stopped early)")
    rep.line("")
    rep.line("SUMMARY")
    for ln in read_summary(rows):
        rep.line("  " + ln)
    rep.line(f"  ticks with no portal page in front: {skipped}")
    health = u.worker_health()
    stats = u.take_read_stats()
    rep.line(f"  read worker: abandoned={health['abandoned_workers']} stuck={health['stuck_workers']} "
             f"paused={'yes' if health['disabled'] else 'no'}  late reads={stats['late']}  busy skips={stats['busy']}")
    rep.line("Fill in blueprint section 12: ms and element count for the page you read; whether the last quarter "
             "is slower than the first (drift); compare this run with the --mta run over the same hours.")
    return 0


def _read_pair(win: Win, u: Any, uia_nodes: Any) -> Dict[str, Any]:
    t0 = time.perf_counter()
    page = u.read_page_text(win.hwnd, timeout_sec=READ_TIMEOUT_SEC)
    ms_text = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    nodes = uia_nodes.read_page_nodes(win.hwnd, timeout_sec=READ_TIMEOUT_SEC)
    ms_nodes = (time.perf_counter() - t0) * 1000
    lines = page.get("lines", [])
    docs = nodes.get("docs", [])
    rebuilt = uia_nodes.lines_from_nodes(docs) if docs else []
    if page.get("busy"):
        status = "busy"
    elif not lines and not docs:
        status = "timeout" if max(ms_text, ms_nodes) >= READ_TIMEOUT_SEC * 1000 * 0.95 else "empty"
    else:
        status = "ok"
    return {"ms_text": ms_text, "ms_nodes": ms_nodes, "elements": sum(len(d) for d in docs),
            "lines_text": len(lines), "lines_nodes": len(rebuilt), "identical": rebuilt == lines,
            "ws_mb": working_set_mb(win.pid), "status": status}


def _two_windows(args: argparse.Namespace, rep: Report) -> int:
    """R9: two portal windows of one browser process, read one after the other and then both at once."""
    from core.sgt_i import uia_nodes
    from core.vsdc import vsdc_scope
    rep.section(f"read-timing --two-windows: {args.minutes} min, one round every {args.every:g} s")
    rep.line("Open two windows of the SAME browser, both on portal pages (the heaviest pages you have), "
             "neither minimised, both visible.")
    _countdown(args.delay)
    url_worker = ComWorker(mta=False)
    by_pid: Dict[Tuple[int, str], List[Win]] = {}
    for w in browser_windows():
        try:
            url = url_worker.call(lambda ctx: window_url(ctx, w.hwnd), timeout=5.0)
        except ProbeTimeout:
            continue
        if url and vsdc_scope.is_in_scope_url(url):
            by_pid.setdefault((w.pid, w.exe), []).append(w)
    pair = next((ws[:2] for ws in by_pid.values() if len(ws) >= 2), None)
    if pair is None:
        rep.line("Found no browser process with two visible portal windows. Nothing was read.")
        return 1
    a, b = pair
    rep.line(f"windows: {a.exe} pid={a.pid} hwnd=0x{a.hwnd:X} and hwnd=0x{b.hwnd:X} (same process: yes)")
    workers = [ComWorker(mta=True), ComWorker(mta=True)]

    def read_one(win: Win, barrier: Optional[threading.Barrier]) -> Callable[[ComCtx], Tuple[float, float, int]]:
        def run(ctx: ComCtx) -> Tuple[float, float, int]:
            uia, client = ctx.uia_objects()
            if barrier is not None:
                barrier.wait(10)
            t0 = time.perf_counter()
            docs = uia_nodes.read_page_nodes_here(uia, client, win.hwnd).get("docs", [])
            return t0, time.perf_counter(), sum(len(d) for d in docs)
        return run

    rounds: List[Dict[str, float]] = []
    start = time.monotonic()
    end = start + args.minutes * 60
    slot = start
    n = 0
    rep.line("columns: round | A alone ms | B alone ms | sum | both at once: A ms, B ms, wall | ratio | elements A/B")
    try:
        while time.monotonic() < end:
            n += 1
            first, second = (a, b) if n % 2 else (b, a)             # alternate who goes first
            try:
                s1 = workers[0].call(read_one(first, None), READ_TIMEOUT_SEC)
                s2 = workers[0].call(read_one(second, None), READ_TIMEOUT_SEC)
                barrier = threading.Barrier(2)
                f1 = workers[0].submit(read_one(first, barrier))
                f2 = workers[1].submit(read_one(second, barrier))
                c1, c2 = f1.result(READ_TIMEOUT_SEC), f2.result(READ_TIMEOUT_SEC)
            except (ProbeTimeout, FutureTimeoutError, threading.BrokenBarrierError):
                rep.line(f"{n:5d} | TIMED OUT - the read hung; this round is not counted")
                workers = [ComWorker(mta=True), ComWorker(mta=True)]
            else:
                seq_ms = [(s[1] - s[0]) * 1000 for s in (s1, s2)]
                conc_ms = [(c[1] - c[0]) * 1000 for c in (c1, c2)]
                wall = (max(c1[1], c2[1]) - min(c1[0], c2[0])) * 1000
                rounds.append({"seq_sum_ms": sum(seq_ms), "conc_wall_ms": wall})
                rep.line(f"{n:5d} | {seq_ms[0]:8.0f} | {seq_ms[1]:8.0f} | {sum(seq_ms):6.0f} | {conc_ms[0]:6.0f}, "
                         f"{conc_ms[1]:6.0f}, {wall:6.0f} | {wall / sum(seq_ms) if sum(seq_ms) else 0:.2f} | {s1[2]}/{s2[2]}")
            slot += args.every
            _sleep_until(slot)
    except KeyboardInterrupt:
        rep.line("(stopped early)")
    rep.line("")
    rep.line("SUMMARY (R9)")
    for ln in serialisation_verdict(rounds):
        rep.line("  " + ln)
    rep.line("Fill in blueprint section 12: the ratio and the verdict, with the element counts of the two pages.")
    return 0


# ---- probe: gate ------------------------------------------------------------------------------

def probe_gate(args: argparse.Namespace, rep: Report) -> int:
    from core.vsdc.vsdc_ocr import VSDCOcrEngine
    rep.section(f"gate: {args.minutes} min, a frame every {args.tick:g} s")
    rep.line(f"cells = {GATE_SIZE}x{GATE_SIZE} grid, {GATE_LEVELS} levels, a cell changed when its level moved by "
             f">= {GATE_LEVEL_DELTA}. 'legacy' = hash() of the picture shrunk to 32x32 RGB (today's gate).")
    rep.line("Work normally while it runs: type invoice rows, let a toast, banner or modal appear. "
             "Write down the time of what you did, to match the 'largest changes' list.")
    _countdown(args.delay)
    engine = VSDCOcrEngine()
    url_worker = ComWorker(mta=False)
    stats = GateStats()
    start = time.monotonic()
    end = start + args.minutes * 60
    slot = start
    prev: Optional[bytes] = None
    prev_hash: Optional[int] = None
    prev_hwnd = 0
    shown = -1
    try:
        while time.monotonic() < end:
            win = foreground_portal(url_worker)
            if win is None:
                stats.skipped += 1
                prev, prev_hwnd = None, 0
            else:
                t0 = time.perf_counter()
                img = engine.capture_window_image(win.hwnd)
                t1 = time.perf_counter()
                sig = frame_signature(img) if img is not None else None
                t2 = time.perf_counter()
                digest = legacy_hash(img) if img is not None else None
                if img is not None and sig is not None:
                    comparable = prev is not None and prev_hwnd == win.hwnd
                    cells = cells_changed(prev, sig) if comparable else None
                    fired = comparable and digest != prev_hash
                    stats.add(time.monotonic(), (t1 - t0) * 1000, (t2 - t1) * 1000, cells, bool(fired))
                    prev, prev_hash, prev_hwnd = sig, digest, win.hwnd
            minute = int((time.monotonic() - start) // 60)
            if minute != shown and shown >= 0 and shown in stats.minutes:
                for ln in stats.minute_lines(shown):
                    rep.line(ln)
            shown = minute
            slot += args.tick
            _sleep_until(slot)
    except KeyboardInterrupt:
        rep.line("(stopped early)")
    if shown in stats.minutes:
        for ln in stats.minute_lines(shown):
            rep.line(ln)
    rep.line("")
    rep.line("SUMMARY")
    for ln in stats.total_lines():
        rep.line("  " + ln)
    rep.line("Fill in blueprint section 12: frame tick ms (D7: 0.2 s if under 15 ms, else 0.35 s); the histogram rows "
             "for typing / caret / spinner / toast / banner / modal (D4).")
    return 0


# ---- probe: toast -----------------------------------------------------------------------------

def probe_toast(args: argparse.Namespace, rep: Report) -> int:
    from core.sgt_i import uia_events
    from core.vsdc import vsdc_uia_text as u
    rep.section(f"toast: {args.minutes} min, a UIA sample every {args.tick:g} s (+ OCR + FlashWatcher)")
    rep.line("Run during a real filing. Nothing is submitted by this tool; ARN-shaped tokens are only numbered.")
    _countdown(args.delay)
    url_worker = ComWorker(mta=False)
    watcher = uia_events.FlashWatcher(echo=lambda s: rep.line(f"  FlashWatcher: {s}"))
    state = {"hwnd": 0}
    stop = threading.Event()
    ocr_samples: List[Tuple[float, frozenset]] = []
    ocr_ms: List[float] = []
    ocr_thread = None
    if not args.no_ocr:
        ocr_thread = threading.Thread(target=_ocr_loop, args=(state, stop, ocr_samples, ocr_ms, rep), daemon=True)
        ocr_thread.start()
    uia_samples: List[Tuple[float, frozenset]] = []
    ticks: List[float] = []
    lines_seen: List[int] = []
    flashes: List[Tuple[float, str, frozenset]] = []
    tab_changes = url_changes = skipped = unreadable = 0
    last_tab = last_url = None
    start = time.monotonic()
    end = start + args.minutes * 60
    slot = start
    try:
        while time.monotonic() < end:
            win = foreground_portal(url_worker)
            if win is None:
                state["hwnd"] = 0
                skipped += 1
            else:
                state["hwnd"] = win.hwnd
                now = time.monotonic()
                tab = u.selected_tab_id(win.hwnd, timeout_sec=1.0)
                page = u.read_page_text(win.hwnd, timeout_sec=5.0)
                if page.get("busy") or not page.get("lines"):
                    unreadable += 1
                else:
                    lines = page["lines"]
                    uia_samples.append((now, frozenset(arn_tokens(lines))))
                    lines_seen.append(len(lines))
                    ticks.append(now)
                key = describe_url(win.url)
                if last_tab is not None and tab != last_tab:
                    tab_changes += 1
                if last_url is not None and key != last_url:
                    url_changes += 1
                last_tab, last_url = tab, key
                watcher.watch(win.hwnd, key)
                for f in watcher.take(win.hwnd):
                    flashes.append((f.ts, f.kind, frozenset(arn_tokens(f.lines))))
            slot += args.tick
            _sleep_until(slot)
    except KeyboardInterrupt:
        rep.line("(stopped early)")
    finally:
        stop.set()
        watcher.stop()
    rep.line("")
    rep.line("SUMMARY")
    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    rep.line(f"  UIA samples={len(uia_samples)} (unreadable {unreadable}, no portal in front {skipped})  "
             f"interval s median/p95/max={median(gaps):.2f}/{pctl(gaps, 95):.2f}/{max(gaps, default=0):.2f}  "
             f"lines min/median/max={min(lines_seen, default=0)}/{median(lines_seen):.0f}/{max(lines_seen, default=0)}")
    rep.line(f"  tab changes={tab_changes}  page changes={url_changes}")
    if args.no_ocr:
        rep.line("  OCR: skipped (--no-ocr)")
    else:
        rep.line(f"  OCR scans={len(ocr_samples)}  ms median/p95/max={median(ocr_ms):.0f}/{pctl(ocr_ms, 95):.0f}/"
                 f"{max(ocr_ms, default=0):.0f}")
    kinds = {k: sum(1 for _t, kk, _a in flashes if kk == k) for k in {kk for _t, kk, _a in flashes}}
    rep.line(f"  FlashWatcher batches={len(flashes)} {kinds or ''} with an ARN-shaped token="
             f"{sum(1 for _t, _k, found in flashes if found)}  disabled={watcher.disabled or 'no'}")
    rep.line("ARN visibility (times are seconds since the probe started):")
    for ln in arn_visibility(uia_samples, ocr_samples, flashes, start):
        rep.line("  " + ln)
    rep.line("Fill in blueprint section 12: how long each ARN was visible by UIA vs OCR, and whether FlashWatcher caught it.")
    return 0


def _ocr_loop(state: Dict[str, int], stop: threading.Event, samples: List[Tuple[float, frozenset]],
              latencies: List[float], rep: Report) -> None:
    """Scans the portal window in front as fast as OCR allows. Only a window that is in front is
    captured (a plain screen copy)."""
    try:
        from core.vsdc.vsdc_ocr import VSDCOcrEngine
        engine = VSDCOcrEngine()
        if not engine.is_available:
            rep.line("  OCR is not available on this PC - UIA and FlashWatcher only")
            return
    except Exception as e:
        rep.line(f"  OCR could not start ({type(e).__name__}) - UIA and FlashWatcher only")
        return
    while not stop.is_set():
        hwnd = state["hwnd"]
        if not hwnd or user32.GetForegroundWindow() != hwnd:
            time.sleep(0.05)
            continue
        t = time.monotonic()
        img = engine.capture_window_image(hwnd)
        if img is None:
            time.sleep(0.05)
            continue
        result = engine.scan_image(img, "full")
        samples.append((t, frozenset(arn_tokens(result.get("lines", [])))))
        latencies.append(float(result.get("latency_ms") or (time.monotonic() - t) * 1000))


# ---- probe: switches --------------------------------------------------------------------------

def probe_switches(args: argparse.Namespace, rep: Report) -> int:
    from core.vsdc import vsdc_uia_text as u
    rep.section(f"switches: {args.minutes} min, polled every {SWITCH_POLL_SEC:g} s")
    rep.line("Work normally. Only the number of switches is kept: no title, address or page text is read.")
    _countdown(args.delay)
    names = browser_names()
    counter = SwitchCounter()
    start = time.monotonic()
    end = start + args.minutes * 60
    slot = start
    try:
        while time.monotonic() < end:
            hwnd = int(user32.GetForegroundWindow() or 0)
            is_browser = bool(hwnd) and window_process(hwnd)[1] in names
            tab = u.selected_tab_id(hwnd, timeout_sec=1.0) if is_browser else None
            counter.feed(time.monotonic() - start, hwnd, is_browser, tab)
            slot += SWITCH_POLL_SEC
            _sleep_until(slot)
    except KeyboardInterrupt:
        rep.line("(stopped early)")
    for ln in counter.lines():
        rep.line(ln)
    rep.line("Fill in blueprint section 12: foreground changes and tab changes per minute during B2B work.")
    return 0


# ---- command line -----------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--delay", type=int, default=5, help="seconds to switch to the browser (default 5)")
    common.add_argument("--out", default="", help="report file (default ~/AmanAssociates_Sera/logs/probe_pack_<date>.txt)")
    ap = argparse.ArgumentParser(description="Read-only measurements for the SGT never-miss work (blueprint section 12).")
    sub = ap.add_subparsers(dest="probe", required=True)

    p = sub.add_parser("doc-url", parents=[common], help="Document Value vs address bar, per browser window")
    p.add_argument("--no-pdf-sample", action="store_true", help="skip the 20 redacted lines of an open PDF viewer")
    p.set_defaults(run=probe_doc_url)

    p = sub.add_parser("read-timing", parents=[common], help="time the page readers over minutes or hours")
    p.add_argument("--minutes", type=float, default=30)
    p.add_argument("--every", type=float, default=READ_EVERY_SEC, help="seconds between reads (default 30)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--mta", action="store_true", help="run the app's read worker as a multithreaded apartment (D2)")
    mode.add_argument("--two-windows", action="store_true", help="R9: two windows of one browser, at once vs one at a time")
    p.set_defaults(run=probe_read_timing)

    p = sub.add_parser("gate", parents=[common], help="screenshot + signature cost and cells changed (D4, D7)")
    p.add_argument("--minutes", type=float, default=5)
    p.add_argument("--tick", type=float, default=FRAME_TICK_SEC)
    p.set_defaults(run=probe_gate)

    p = sub.add_parser("toast", parents=[common], help="how long an ARN is visible by UIA vs OCR, and FlashWatcher")
    p.add_argument("--minutes", type=float, default=10)
    p.add_argument("--tick", type=float, default=TOAST_TICK_SEC)
    p.add_argument("--no-ocr", action="store_true")
    p.set_defaults(run=probe_toast)

    p = sub.add_parser("switches", parents=[common], help="foreground and tab changes per minute")
    p.add_argument("--minutes", type=float, default=30)
    p.set_defaults(run=probe_switches)
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    rep = Report(Path(args.out) if args.out else default_report_path())
    try:
        rc = args.run(args, rep)
    except KeyboardInterrupt:
        rc = 130
    print(f"\nReport: {rep.path}", flush=True)
    return rc


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)          # a wedged UIA worker thread would otherwise keep the process alive
