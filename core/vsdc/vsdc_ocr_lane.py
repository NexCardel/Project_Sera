"""
core/vsdc/vsdc_ocr_lane.py - the OCR lane: screenshots read on their own thread
================================================================================
SGT never-miss blueprint section 5.1 / WP7. A toast on a heavy page is gone before the page read
returns, so the capture loop sends the screenshot it already took to this lane. One worker thread
runs the engine (45 to 100 ms a scan), apart from every page read, so a browser that is slow to
answer UIA never slows OCR down. A window is asked for at most once per OCR_MIN_GAP_SEC.

The result is a vsdc_reader.ReadResult with source="ocr": no documents, no URL read of its own, the
tab and URL the loop believed when it took the picture. SGT absorbs it under SOURCE_POLICY["ocr"]
(add-only, R6). Nothing here reads page text into a log.
"""

import time
from concurrent.futures import Future
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import vsdc_reader
from .vsdc_reader import ReadContext, ReadResult

OCR_MIN_GAP_SEC = 1.0           # per window
MAX_OCR_PENDING = 4             # scans waiting or running; a busier lane refuses (the loop asks again)
OCR_STUCK_SEC = 30.0            # a scan unanswered this long is given up on


class OcrLane:
    """request() and results() are called from the capture thread and never block; the scan runs
    on the lane's own daemon thread (started with the first request)."""

    def __init__(self, engine: Any, clock: Callable[[], float] = time.monotonic):
        self._engine = engine
        self._clock = clock
        self._thread: Optional[Any] = None
        self._last: Dict[int, float] = {}                       # hwnd -> when a scan was last accepted
        self._jobs: List[Tuple[ReadContext, Future, float]] = []
        self._scans = 0
        self._refused = 0

    def available(self) -> bool:
        return self._engine is not None and bool(getattr(self._engine, "is_available", True))

    def ready(self, hwnd: int) -> bool:
        """request() would accept this window now (no picture needed to ask)."""
        if not self.available() or len(self._jobs) >= MAX_OCR_PENDING:
            return False
        last = self._last.get(hwnd)
        return last is None or self._clock() - last >= OCR_MIN_GAP_SEC

    def request(self, img: Any, ctx: ReadContext) -> bool:
        """False when the picture is missing, OCR is not available, this window had a scan within
        OCR_MIN_GAP_SEC, or the lane is full. The result comes back through results()."""
        if img is None or not self.ready(ctx.hwnd):
            self._refused += 1
            return False
        now = self._clock()
        self._last[ctx.hwnd] = now
        if len(self._last) > 64:
            self._last.pop(next(iter(self._last)))
        if self._thread is None:
            self._thread = vsdc_reader._OneThread("sgt-ocr-lane")
        self._jobs.append((ctx, self._thread.submit(self._scan, img), now))
        self._scans += 1
        return True

    def results(self) -> List[ReadResult]:
        """Every finished scan, oldest first, drained. A scan that failed is dropped."""
        out: List[ReadResult] = []
        keep: List[Tuple[ReadContext, Future, float]] = []
        now = self._clock()
        for ctx, fut, started in self._jobs:
            if not fut.done():
                if now - started > OCR_STUCK_SEC:
                    self._thread = None         # a stuck engine call: its thread is let go, the next scan gets a new one
                else:
                    keep.append((ctx, fut, started))
                continue
            try:
                lines = fut.result()
            except Exception:
                continue
            out.append(ReadResult(ctx=ctx, source="ocr", lines=lines, docs=[], doc_urls=[], tab_before=ctx.tab,
                                  tab_after=ctx.tab, url_before=ctx.url, url_after=ctx.url, started_at=started,
                                  finished_at=self._clock(), scope_rule=ctx.url_source or "title"))
        self._jobs = keep
        return out

    def close_window(self, hwnd: int) -> None:
        self._last.pop(hwnd, None)

    def close(self) -> None:
        thread, self._thread = self._thread, None
        self._jobs = []
        if thread is not None:
            try:
                thread.shutdown()
            except Exception:
                pass

    def stats(self) -> Dict[str, int]:
        return {"ocr_scans": self._scans, "ocr_refused": self._refused, "ocr_pending": len(self._jobs)}

    def _scan(self, img: Any) -> List[str]:
        out = self._engine.scan_image(img, region_type="full") or {}
        return [str(x) for x in (out.get("lines") or [])]
