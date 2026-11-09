"""The OCR lane (core/vsdc/vsdc_ocr_lane): screenshots scanned on their own thread, once a second per window.

SGT never-miss blueprint 5.1 / WP7. A fake engine and made-up lines; no portal, no real OCR.
"""

import os
import sys
import threading
import time

import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.vsdc import vsdc_ocr_lane
from core.vsdc.vsdc_ocr_lane import OcrLane
from tests.test_vsdc_reader import Clock, ctx, wait_until


class FakeEngine:
    is_available = True

    def __init__(self, lines=("Fictional line",)):
        self.lines = list(lines)
        self.calls = []
        self.gate = None
        self.fail = False

    def scan_image(self, img, region_type="full"):
        self.calls.append(region_type)
        if self.gate is not None:
            self.gate.wait(5.0)
        if self.fail:
            raise RuntimeError("engine down")
        return {"lines": self.lines}


IMG = Image.new("RGB", (64, 64), (255, 255, 255))


@pytest.fixture
def lane():
    made = []

    def make(engine=None):
        clock = Clock()
        engine = engine or FakeEngine()
        ln = OcrLane(engine, clock)
        made.append(ln)
        return ln, engine, clock
    yield make
    for ln in made:
        ln.close()


def drain(ln, n=1):
    out = []
    wait_until(lambda: (out.extend(ln.results()), len(out) >= n)[1])
    return out


def test_a_scan_comes_back_as_an_ocr_result_with_no_documents(lane):
    ln, engine, clock = lane(FakeEngine(["One", "Two"]))
    c = ctx(5, clock, reason="flash")
    assert ln.request(IMG, c) is True
    (r,) = drain(ln)
    assert r.source == "ocr" and r.ctx is c
    assert r.lines == ["One", "Two"] and r.docs == [] and r.doc_urls == []
    assert r.tab_before == r.tab_after == c.tab and r.url == c.url
    assert engine.calls == ["full"]


def test_ocr_is_rate_limited_per_window(lane):
    ln, engine, clock = lane()
    assert ln.request(IMG, ctx(1, clock)) is True
    assert ln.request(IMG, ctx(1, clock)) is False                     # the same window, no time passed
    assert ln.request(IMG, ctx(2, clock)) is True                      # another window has its own gap
    clock.advance(vsdc_ocr_lane.OCR_MIN_GAP_SEC - 0.1)
    assert ln.ready(1) is False and ln.request(IMG, ctx(1, clock)) is False
    clock.advance(0.1)
    assert ln.ready(1) is True and ln.request(IMG, ctx(1, clock)) is True
    drain(ln, 3)
    assert len(engine.calls) == 3


def test_a_refused_request_is_not_scanned_and_is_counted(lane):
    ln, engine, clock = lane()
    assert ln.request(None, ctx(1, clock)) is False                    # no picture
    ln.request(IMG, ctx(1, clock))
    ln.request(IMG, ctx(1, clock))
    drain(ln)
    assert len(engine.calls) == 1
    assert ln.stats()["ocr_refused"] == 2 and ln.stats()["ocr_scans"] == 1


def test_an_unavailable_engine_takes_nothing(lane):
    engine = FakeEngine()
    engine.is_available = False
    ln, _e, clock = lane(engine)
    assert ln.available() is False and ln.ready(1) is False
    assert ln.request(IMG, ctx(1, clock)) is False
    assert engine.calls == []


def test_a_failed_scan_is_dropped_and_the_lane_keeps_working(lane):
    ln, engine, clock = lane()
    engine.fail = True
    assert ln.request(IMG, ctx(1, clock)) is True
    wait_until(lambda: len(engine.calls) == 1)
    time.sleep(0.05)
    assert ln.results() == []
    engine.fail = False
    clock.advance(2.0)
    assert ln.request(IMG, ctx(1, clock)) is True
    assert len(drain(ln)) == 1


def test_the_lane_never_holds_more_than_a_few_scans(lane):
    engine = FakeEngine()
    engine.gate = threading.Event()
    ln, _e, clock = lane(engine)
    try:
        accepted = sum(ln.request(IMG, ctx(h, clock)) for h in range(1, 10))
        assert accepted == vsdc_ocr_lane.MAX_OCR_PENDING
    finally:
        engine.gate.set()
    drain(ln, vsdc_ocr_lane.MAX_OCR_PENDING)


def test_a_stuck_scan_is_given_up_after_a_while(lane):
    engine = FakeEngine()
    engine.gate = threading.Event()
    ln, _e, clock = lane(engine)
    try:
        assert ln.request(IMG, ctx(1, clock)) is True
        clock.advance(vsdc_ocr_lane.OCR_STUCK_SEC + 1)
        assert ln.results() == []
        assert ln.stats()["ocr_pending"] == 0
    finally:
        engine.gate.set()


def test_closing_a_window_forgets_its_gap(lane):
    ln, _e, clock = lane()
    ln.request(IMG, ctx(1, clock))
    assert ln.ready(1) is False
    ln.close_window(1)
    assert ln.ready(1) is True
    drain(ln)
