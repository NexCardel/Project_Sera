"""
core/vsdc/vsdc_capture.py - the capture loop: SGT's path, out of the router
============================================================================
SGT never-miss blueprint sections 4.4, 4.7 (foreground only for now), 4.8 and WP4, with the
review corrections R2, R7 and R10.

Before: SGT's page reads ran inside the router's tick, behind the router's own address-bar read
on the capture thread, and a read that took longer than 3 s was thrown away. Now, each tick:

  1. Every read the reader (core/vsdc/vsdc_reader) finished since the last tick is taken: its URL
     and tab update what the loop knows of that window; a clean page read is handed to SGT.
  2. For each watched window (core/vsdc/vsdc_windows): the scope gate first. The URL is the one
     the window's last read reported (Document first, address bar second), if it is younger than
     URL_FRESH_SEC; the reader's scope worker is asked for a URL-only read often enough to keep it
     so, even while a heavy page read runs (R2). Without a fresh URL, the title grace as before
     (SCOPE_GRACE_SEC, never refreshed by a title). A window never verified on a portal is not
     read and not even screenshotted.
  3. In scope: one screenshot, one frame signature (core/vsdc/vsdc_gate). A page read is asked for
     when the URL changed (then BURST_READS reads), the frame changed by GATE_MIN_CELLS or more,
     REREAD_AFTER_SEC passed, a torn read needs asking again, or SGT-I / SCC-U want one - spaced by
     the last read's own duration, and never while the window's previous read is unanswered.

The capture thread never waits on the browser: Win32 calls and a screenshot only. The crosshair
engines keep the router's own URL read (R7); this loop is used only while SGT is on.
Never logs page text, values or links outside the portals (the heartbeat gets counts).
"""

import ctypes
import itertools
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Protocol

from . import vsdc_gate, vsdc_scope, vsdc_windows
from .vsdc_reader import (REASON_SCOPE, RESULT_MAX_AGE_SEC, URL_FRESH_SEC, WEDGE_SEC, PageReader, ReadContext,
                          ReadResult)
from .vsdc_windows import WindowInfo

FRAME_TICK_SEC = 0.35         # R10 / D7: the fallback until WP0's gate probe measures a tick under 15 ms
FRAME_TICK_FAST_SEC = 0.2     # D7's frame tick, once that is measured
REREAD_AFTER_SEC = 5.0        # a still page is read again this often (D5)
MIN_READ_GAP_SEC = 0.35
READ_GAP_FACTOR = 1.0         # a 500 ms page is read at most every ~1 s: 50 % duty per lane (D5)
BURST_READS = 3               # after a URL change: 3 reads, BURST_GAP_SEC apart, whatever the frame does
BURST_GAP_SEC = 0.3
TORN_RETRIES = 3              # a torn read is asked again at most this often per page visit (R1)
REASON_BLIND_RETRY = "blind-retry"   # SGT's absorb asks for it (sgt_shadow.BLIND_RETRIES, then OCR)
SCOPE_POLL_SEC = 1.0          # a URL-only read is asked once the window's URL is this old (< URL_FRESH_SEC)
SCOPE_GRACE_SEC = 8.0         # the router's _SCOPE_GRACE_SEC: no readable URL, portal title, verified this recently
STATE_KEEP_SEC = 600.0        # a window not watched for this long is forgotten (its SGT sessions are not ended)
PRUNE_EVERY_SEC = 5.0

_seq = itertools.count(1)     # ReadContext.seq: unique per process run, shared by every loop


class Platform(Protocol):
    """Everything the loop asks of Windows, so it can be tested without it."""
    def foreground(self) -> int: ...
    def windows(self, foreground: int) -> List[WindowInfo]: ...
    def screenshot(self, hwnd: int) -> Any: ...
    def is_window(self, hwnd: int) -> bool: ...
    def now(self) -> float: ...


class WinPlatform:
    """The real one: Win32 and VSDCOcrEngine.capture_window_image (PrintWindow fallback included)."""

    def __init__(self, ocr: Any):
        self._ocr = ocr

    def foreground(self) -> int:
        return int(ctypes.windll.user32.GetForegroundWindow() or 0)

    def windows(self, foreground: int) -> List[WindowInfo]:
        return vsdc_windows.browser_windows(foreground)

    def screenshot(self, hwnd: int) -> Any:
        return self._ocr.capture_window_image(hwnd)

    def is_window(self, hwnd: int) -> bool:
        return bool(ctypes.windll.user32.IsWindow(hwnd))

    def now(self) -> float:
        return time.monotonic()


@dataclass
class WindowState:
    hwnd: int
    tab: Optional[str] = None
    url: str = ""                       # the page's URL as the last read reported it ("" = none found)
    url_source: str = ""                # "document" | "address_bar" | ""
    url_at: float = 0.0                 # when that read finished (0 = no read yet)
    portal: str = ""
    scope_verified_at: float = 0.0
    scope_asked_at: float = 0.0         # a URL-only read asked and not answered yet (0 = none)
    last_sig: Optional[bytes] = None    # the frame when the last page read was asked
    last_read_finished: float = 0.0
    last_read_ms: float = 0.0
    last_request_at: float = 0.0
    burst_left: int = 0
    torn_retries: int = 0
    blind_retries: int = 0              # WP5 / WP8
    retry: str = ""                     # "torn-retry" waiting to be asked
    wanted: str = ""                    # a read was due but could not be asked yet: its reason
    pending_seq: int = 0                # the page read asked and not answered yet (0 = none)
    pending_at: float = 0.0
    frame: Any = None                   # the screenshot taken when frame_seq was asked (OCR of a blind page)
    frame_seq: int = 0
    last_bg_tick: float = 0.0           # WP10
    last_seen: float = 0.0


def _title_portal(title_lower: str) -> str:
    """The router's dialect guess from a window title ("" = it says nothing)."""
    if "gst" in title_lower or "goods and services" in title_lower:
        return "GST Portal"
    if "income tax" in title_lower or "e-filing" in title_lower or "itr" in title_lower:
        return "Income Tax"
    return ""


class CaptureLoop:
    """One per router while SGT is on. tick() runs on the capture thread and never raises."""

    def __init__(self, platform: Platform, reader: PageReader, sgt: Any, ocr: Any, health: Any,
                 scope: Any = vsdc_scope, clock: Optional[Callable[[], float]] = None,
                 on_out_of_scope: Optional[Callable[[int, str], None]] = None):
        """clock must be the reader's clock (a ReadContext's asked_at is checked against it).
        on_out_of_scope(hwnd, url): a window's own URL is off the portals (the router's tripwire)."""
        self._platform = platform
        self._reader = reader
        self._sgt = sgt
        self._ocr = ocr
        self._health = health
        self._scope = scope
        self._clock = clock or platform.now
        self._on_out_of_scope = on_out_of_scope
        self._states: Dict[int, WindowState] = {}
        self._ended_seq = 0                 # results asked at or before this were ended by end_all
        self._pruned_at = 0.0
        self._diag: Dict[str, Any] = {}
        self._counts = {"asked": 0, "absorbed": 0, "torn": 0, "errors": 0}

    # ---- the tick ----------------------------------------------------------------------------

    def tick(self) -> None:
        now = self._clock()
        self._diag = {}
        for r in self._reader.results():
            try:
                self._take(r)
            except Exception as e:
                self._counts["errors"] += 1
                self._diag["error"] = type(e).__name__
        foreground = self._platform.foreground()
        windows = self._platform.windows(foreground)
        if not windows:
            self._diag["front"] = "none" if not foreground else "other"
        for w in windows:
            ws = self._states.get(w.hwnd)
            if ws is None:
                ws = self._states[w.hwnd] = WindowState(hwnd=w.hwnd)
            if w.background:                # WP10: a window not in front is looked at every BG_TICK_SEC
                if now - ws.last_bg_tick < vsdc_windows.BG_TICK_SEC:
                    continue
                ws.last_bg_tick = now
            ws.last_seen = now
            try:
                self._window(w, ws, now)
            except Exception as e:
                self._counts["errors"] += 1
                self._diag["error"] = type(e).__name__
        if now - self._pruned_at >= PRUNE_EVERY_SEC:
            self._pruned_at = now
            self._prune(now, {w.hwnd for w in windows})

    def end_all(self, reason: str) -> None:
        """SGT switched off or to another mode, or shutdown: every session ends, every lane closes,
        and a read asked before now is never absorbed."""
        self._ended_seq = next(_seq)
        for hwnd in list(self._states):
            self._reader.close_lane(hwnd)
        self._states.clear()
        if self._sgt is not None:
            self._sgt.end_all(reason)

    def close(self) -> None:
        """The loop is no longer used: the reader's threads are let go (wedged ones are daemons)."""
        try:
            self._reader.close()
        except Exception:
            pass

    def paused(self) -> bool:
        """UIA reads paused after repeated hangs (health state uia-paused)."""
        try:
            return self._reader.paused()
        except Exception:
            return False

    def diag(self) -> Dict[str, Any]:
        """For the heartbeat: this tick's window (exe, scope, portal; the page only when in scope)
        and the loop's counts since the last call."""
        out = dict(self._diag)
        out.update({f"loop_{k}": v for k, v in self._counts.items() if v})
        self._counts = dict.fromkeys(self._counts, 0)
        return out

    def health_extra(self) -> Dict[str, Any]:
        """The reader's numbers for the health record (vsdc_health.CaptureHealth)."""
        st = self._reader.stats()
        return {"lanes": st.get("lanes", 0), "abandoned": st.get("abandoned", 0),
                "stuck": st.get("stuck", 0), "late": st.get("late", 0)}

    # ---- one result ----------------------------------------------------------------------------

    def _take(self, r: ReadResult) -> None:
        hwnd = r.ctx.hwnd
        if r.ctx.seq <= self._ended_seq:
            return
        ws = self._states.get(hwnd)
        if r.error == "no-window":
            if not self._platform.is_window(hwnd):
                self._window_gone(hwnd)
            return
        if ws is None:
            return
        frame = None
        if r.url_only:
            if ws.scope_asked_at and r.ctx.asked_at >= ws.scope_asked_at:
                ws.scope_asked_at = 0.0
        else:
            self._answered()
            if r.ctx.seq == ws.frame_seq:
                frame, ws.frame, ws.frame_seq = ws.frame, None, 0
            if r.ctx.seq >= ws.pending_seq:
                ws.pending_seq, ws.pending_at = 0, 0.0
        if r.error:                         # timeout-abandoned, uia-init, exception: never absorbed
            self._counts["errors"] += 1
            if not r.url_only:
                ws.wanted = ws.wanted or "frame"
            return
        newest = r.finished_at >= ws.url_at
        if newest:
            self._learn_url(ws, r)
        if r.url_only:
            return
        ws.last_read_finished, ws.last_read_ms = r.finished_at, r.ms
        if r.torn:                          # R1: both kinds are dropped and asked again
            self._counts["torn"] += 1
            ws.torn_retries += 1
            if ws.torn_retries <= TORN_RETRIES:
                ws.retry = "torn-retry"
            return
        if not r.scope_rule:                # nothing on the page was in scope: nothing was read
            return
        self._absorb(r, ws, frame, newest)

    def _answered(self) -> None:
        try:
            self._health.answered()
        except Exception:
            pass

    def _learn_url(self, ws: WindowState, r: ReadResult) -> None:
        url = r.url
        ws.url_at = r.finished_at
        ws.url_source = r.scope_rule
        ws.tab = r.tab_after
        if url != ws.url:
            ws.url = url
            ws.torn_retries = ws.blind_retries = 0
            ws.retry = ""
            if url and self._scope.is_in_scope_url(url):
                ws.burst_left = BURST_READS

    def _absorb(self, r: ReadResult, ws: WindowState, frame: Any, newest: bool) -> None:
        """SGT takes the read into the session that asked for it (blueprint 6.1). When this read is
        the newest word on the window's page, SGT's bookkeeping sees that page first, so a logout or
        login page reached during the read ends the session before its text could land there (R1)."""
        if self._sgt is None:
            return
        if newest:
            url = self._scope.sanitize_page_url(r.url)
            portal = self._scope.portal_for_url(r.url) or r.ctx.portal
            self._sgt.observe_window(r.ctx.hwnd, r.tab_after, portal, url, r.ctx.title)
        self._counts["absorbed"] += 1
        out = self._sgt.absorb(r, frame=frame, ocr=self._ocr, now=self._clock(), blind_retries=ws.blind_retries)
        if isinstance(out, str) and out == REASON_BLIND_RETRY:
            ws.blind_retries += 1           # a page that read fine before came back blind: again, 0.3 s on
            ws.retry = ws.retry or REASON_BLIND_RETRY
        else:
            from core.sgt.sgt_shadow import is_blind
            if not is_blind(list(r.lines)):
                ws.blind_retries = 0

    # ---- one window ----------------------------------------------------------------------------

    def _window(self, w: WindowInfo, ws: WindowState, now: float) -> None:
        title_lower = w.title.lower()
        from .vsdc_router import PORTAL_KEYWORDS
        titled_portal = any(k in title_lower for k in PORTAL_KEYWORDS)
        ws.portal = _title_portal(title_lower) or ws.portal or "Income Tax"

        # Scope: the URL the window's last read reported, kept fresh by URL-only reads (R2).
        if ws.scope_asked_at and now - ws.scope_asked_at >= URL_FRESH_SEC:
            ws.scope_asked_at = 0.0         # unanswered (a stuck scope worker): ask again
        if not ws.scope_asked_at and (not ws.url_at or now - ws.url_at >= SCOPE_POLL_SEC):
            self._request(w, ws, now, REASON_SCOPE, None)
        fresh = bool(ws.url) and ws.url_at > 0 and now - ws.url_at < URL_FRESH_SEC
        if fresh:
            in_scope = self._scope.is_in_scope_url(ws.url)
            if in_scope:
                ws.scope_verified_at = now
            elif self._on_out_of_scope is not None:
                self._on_out_of_scope(w.hwnd, ws.url)
        else:
            in_scope = (titled_portal and ws.scope_verified_at > 0
                        and now - ws.scope_verified_at <= SCOPE_GRACE_SEC)
        portal = (self._scope.portal_for_url(ws.url) if fresh else "") or ws.portal
        self._health.window(w.hwnd, in_scope, portal, titled_portal=titled_portal, url_readable=fresh)
        self._diag.update(front=w.proc_name or "?", url=(ws.url_source or "read") if fresh else "none",
                          in_scope="yes" if in_scope else "no", tab="known" if ws.tab else "unknown")
        if not in_scope:
            ws.last_sig = None              # nothing of a page outside the portals is kept
            return
        self._diag.update(portal=portal, page=self._scope.sanitize_page_url(ws.url) if fresh else None)
        owner = None
        if self._sgt is not None:
            # SGT's session bookkeeping for the page in view; a read asked now belongs to `owner`.
            owner = self._sgt.observe_window(w.hwnd, ws.tab, portal,
                                             self._scope.sanitize_page_url(ws.url) if ws.url else "", w.title)
            if owner is None:
                return                      # a logout page: nothing about a client is read there

        # Pixels only: one screenshot and its signature.
        try:
            img = self._platform.screenshot(w.hwnd)
        except Exception:
            img = None
        sig = vsdc_gate.frame_signature(img)

        if ws.pending_seq and now - ws.pending_at > RESULT_MAX_AGE_SEC:
            ws.pending_seq, ws.pending_at = 0, 0.0      # the reader dropped it as too old
        elif ws.pending_seq and now - ws.pending_at > WEDGE_SEC and self._reader.inflight(w.hwnd) is None:
            ws.pending_seq, ws.pending_at = 0, 0.0      # its worker was abandoned: the answer may never come
        reason = self._read_reason(w, ws, sig, now)
        if not reason:
            return
        if ws.pending_seq or not self._spaced(ws, reason, now):
            ws.wanted = reason              # asked as soon as the read in flight is answered
            return
        status = self._request(w, ws, now, reason, sig, img, portal, owner)
        if status in ("started", "queued"):
            ws.wanted = ""
            ws.last_sig, ws.last_request_at = sig, now
            if reason in ("url", "burst"):
                ws.burst_left -= 1
            elif reason in ("torn-retry", REASON_BLIND_RETRY):
                ws.retry = ""
        else:
            ws.wanted = reason              # paused / no lane: health shows it; OCR is WP7's

    def _read_reason(self, w: WindowInfo, ws: WindowState, sig: Optional[bytes], now: float) -> str:
        if ws.retry:                        # before the burst: a torn read is not one of its reads
            return ws.retry
        if ws.burst_left > 0:
            return "url" if ws.burst_left == BURST_READS else "burst"
        if sig is None or ws.last_sig is None or vsdc_gate.cells_changed(ws.last_sig, sig) >= vsdc_gate.GATE_MIN_CELLS:
            return "frame"
        if ws.wanted:
            return ws.wanted
        if now - ws.last_read_finished >= REREAD_AFTER_SEC:
            return "periodic"
        if self._sgt is not None and getattr(self._sgt, "wants_read", None) is not None and self._sgt.wants_read(w.hwnd):
            return "wants_read"
        return ""

    @staticmethod
    def _spaced(ws: WindowState, reason: str, now: float) -> bool:
        if not ws.last_read_finished:
            return True
        quick = reason in ("url", "burst", "torn-retry", REASON_BLIND_RETRY)
        gap = max(BURST_GAP_SEC if quick else MIN_READ_GAP_SEC, READ_GAP_FACTOR * ws.last_read_ms / 1000.0)
        return now - ws.last_read_finished >= gap

    def _request(self, w: WindowInfo, ws: WindowState, now: float, reason: str, sig: Optional[bytes],
                 img: Any = None, portal: str = "", owner: Any = None) -> str:
        url = self._scope.sanitize_page_url(ws.url) if ws.url else ""
        ctx = ReadContext(seq=next(_seq), hwnd=w.hwnd, title=w.title, portal=portal or ws.portal, url=url,
                          url_source=ws.url_source or ("title" if not url else ""), tab=ws.tab, frame_sig=sig,
                          asked_at=now, reason=reason, background=w.background)
        status = self._reader.request(ctx)
        if status not in ("started", "queued"):
            return status
        if reason == REASON_SCOPE:
            ws.scope_asked_at = now
        else:
            ws.pending_seq, ws.pending_at = ctx.seq, now
            ws.frame, ws.frame_seq = img, ctx.seq
            self._counts["asked"] += 1
            if self._sgt is not None and owner is not None:
                self._sgt.request_made(ctx, owner)
            try:
                self._health.requested(ctx)
            except Exception:
                pass
        return status

    # ---- windows that went away ------------------------------------------------------------------

    def _prune(self, now: float, watched: set) -> None:
        for hwnd, ws in list(self._states.items()):
            if hwnd in watched:
                continue
            if not self._platform.is_window(hwnd):
                self._window_gone(hwnd)
            elif now - ws.last_seen > STATE_KEEP_SEC:
                self._states.pop(hwnd, None)
                self._reader.close_lane(hwnd)

    def _window_gone(self, hwnd: int) -> None:
        self._states.pop(hwnd, None)
        self._reader.close_lane(hwnd)
        if self._sgt is not None:
            self._sgt.end_session(hwnd, "window closed")
