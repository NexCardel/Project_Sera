"""The capture loop (core/vsdc/vsdc_capture): SGT's path out of the router.

SGT never-miss blueprint WP4 with R1, R2, R7, R10. A fake platform (windows, screenshots) and the
WP3 fake reader backend - no browser, no portal, no COM; every page, URL and line here is made up.
"""

import os
import sys
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.vsdc import vsdc_capture, vsdc_diag, vsdc_gate
from core.vsdc.vsdc_capture import CaptureLoop
from core.vsdc.vsdc_reader import PageReader
from core.vsdc.vsdc_windows import WindowInfo
from tests.test_vsdc_reader import PORTAL, PORTAL_2, Clock, Doc, FakeBackend, Window, wait_until

HWND = 7
TITLE = "GST Portal - Google Chrome"
TICK = vsdc_capture.FRAME_TICK_SEC


class FakePlatform:
    def __init__(self, clock, hwnd=HWND, title=TITLE, proc="chrome.exe"):
        self.clock = clock
        self.fg = hwnd
        self.wins = {hwnd: WindowInfo(hwnd=hwnd, title=title, proc_name=proc)}
        self.alive = {hwnd}
        self.img = Image.new("RGB", (320, 320), (240, 240, 240))
        self.shots = 0

    def foreground(self):
        return self.fg

    def windows(self, foreground):
        w = self.wins.get(foreground)
        return [w] if w else []

    def screenshot(self, hwnd):
        self.shots += 1
        return self.img.copy()

    def is_window(self, hwnd):
        return hwnd in self.alive

    def now(self):
        return self.clock()

    def paint(self, box, shade=20):
        """Changes part of the screen: box in pixels (a 320 px picture: one cell is 10x10)."""
        img = self.img.copy()
        ImageDraw.Draw(img).rectangle(box, fill=(shade, shade, shade))
        self.img = img


class FakeSgt:
    def __init__(self):
        self.observed = []
        self.ended = []
        self.want = False

    def observe_window(self, hwnd, tab, portal, url, title=""):
        return self.session

    session = object()                                        # None = a logout page

    def request_made(self, ctx, s):
        pass

    def absorb(self, r, frame=None, ocr=None, now=None, blind_retries=0):
        from core.vsdc import vsdc_scope
        self.observed.append(SimpleNamespace(hwnd=r.ctx.hwnd, portal=vsdc_scope.portal_for_url(r.url) or r.ctx.portal,
                                             url=vsdc_scope.sanitize_page_url(r.url), frame=frame, title=r.ctx.title,
                                             tab=r.tab_after, read={"lines": list(r.lines), "docs": r.docs}))

    def wants_read(self, hwnd):
        return self.want

    def end_session(self, hwnd, reason):
        self.ended.append((hwnd, reason))

    def window_gone(self, hwnd):
        self.ended.append((hwnd, "window closed"))

    def end_all(self, reason):
        self.ended.append(("all", reason))


class Rig:
    def __init__(self, docs=None, title=TITLE, bar="", tab="tab-1"):
        self.clock = Clock()
        self.win = Window(pid=1, tab=tab, docs=docs if docs is not None else [Doc(PORTAL, ["Fictional GST page"])],
                          bar=bar)
        self.backend = FakeBackend(self.clock, {HWND: self.win})
        self.reader = PageReader(backend=self.backend, clock=self.clock, diag=lambda t: None)
        self.platform = FakePlatform(self.clock, title=title)
        self.sgt = FakeSgt()
        self.health = MagicMock()
        self.out_of_scope = []
        self.loop = CaptureLoop(self.platform, self.reader, self.sgt, ocr=None, health=self.health,
                                clock=self.clock, on_out_of_scope=lambda h, u: self.out_of_scope.append(h))
        self.asked = []
        request = self.reader.request
        self.reader.request = lambda ctx: (self.asked.append(ctx), request(ctx))[1]

    def settle(self):
        wait_until(lambda: all(w.job is None and w.queued is None
                               for lane in list(self.reader._lanes.values()) for w in lane.workers()))

    def step(self, n=1, dt=TICK):
        for _ in range(n):
            self.clock.advance(dt)
            self.loop.tick()
            self.settle()

    def page_reads(self, reason=None):
        return [c for c in self.asked if c.reason != "scope" and (reason is None or c.reason == reason)]

    def warm(self):
        """URL known, the burst after it done: a still page from here on."""
        self.step(5)
        self.asked.clear()
        self.backend.reads.clear()

    def close(self):
        self.reader.close()


@pytest.fixture
def rig():
    made = []

    def make(**kw):
        r = Rig(**kw)
        made.append(r)
        return r
    yield make
    for r in made:
        r.close()


def test_no_content_is_read_before_the_url_is_known(rig):
    r = rig()
    r.step()
    assert [c.reason for c in r.asked] == ["scope"]           # the URL first: no page content
    assert r.backend.reads == [] and r.platform.shots == 0    # not even a screenshot
    r.step()
    assert r.page_reads() and r.page_reads()[0].reason == "url"
    assert r.backend.reads == [(HWND, PORTAL)]


def test_a_page_off_the_portals_is_never_read_nor_screenshotted(rig):
    r = rig(docs=[Doc("https://video.example.test/watch", ["Not a portal"])], title="Income Tax guide - Google Chrome")
    r.step(20)
    assert r.page_reads() == [] and r.backend.reads == [] and r.platform.shots == 0
    assert r.out_of_scope                                     # the router's tripwire is told


def test_title_grace_is_never_refreshed_by_a_title(rig):
    r = rig()
    r.warm()
    r.win.docs = []                                           # the URL can no longer be read
    scope = []
    for _ in range(40):                                       # 14 s, the title still says portal
        r.step()
        scope.append((r.clock(), r.health.window.call_args.args[1]))
    lost = r.clock.t - 40 * TICK
    assert any(ok for t, ok in scope if t - lost < vsdc_capture.SCOPE_GRACE_SEC)
    assert not any(ok for t, ok in scope if t - lost > vsdc_capture.SCOPE_GRACE_SEC + vsdc_capture.URL_FRESH_SEC)


def test_a_url_change_bursts_three_reads(rig):
    r = rig()
    r.warm()
    r.win.docs[0].url = PORTAL_2                              # same picture, another page
    r.step(8)                                                 # under REREAD_AFTER_SEC
    reads = [c for c in r.page_reads() if c.url.endswith("/returns")]
    assert [c.reason for c in reads] == ["url", "burst", "burst"]
    assert len(r.backend.reads) == 3


def test_a_still_page_costs_one_signature_per_tick(rig, monkeypatch):
    r = rig()
    r.warm()
    signed = []
    real = vsdc_gate.frame_signature
    monkeypatch.setattr(vsdc_gate, "frame_signature", lambda img: (signed.append(1), real(img))[1])
    shots = r.platform.shots
    r.step(10)                                                # 3.5 s: still page, no periodic read yet
    assert len(signed) == 10 and r.platform.shots - shots == 10
    assert r.page_reads() == [] and r.backend.reads == []


def test_typing_under_the_cell_threshold_does_not_read(rig):
    r = rig()
    r.warm()
    r.platform.paint((0, 0, 19, 9))                           # two cells: a typed character, a caret
    r.step(3)
    assert r.page_reads() == []


def test_a_toast_sized_change_reads(rig):
    r = rig()
    r.warm()
    r.platform.paint((100, 100, 159, 129))                   # 6x3 cells: a toast
    r.step()
    assert [c.reason for c in r.page_reads()] == ["frame"]


def test_reads_are_spaced_by_the_last_read_duration(rig):
    r = rig()
    r.win.docs[0].cost_sec = 1.2                              # every read takes 1.2 s
    shade = 0
    for _ in range(30):
        shade = (shade + 40) % 240
        r.platform.paint((0, 0, 319, 319), shade)            # the whole screen changes every tick
        r.step()
    asked = [c.asked_at for c in r.page_reads()]
    assert len(asked) >= 3
    # each read waits for the previous one to finish (1.2 s) and then as long again (READ_GAP_FACTOR)
    assert all(b - a >= 2.4 - 1e-6 for a, b in zip(asked, asked[1:]))


def test_periodic_reread_every_5s(rig):
    r = rig()
    r.warm()
    r.step(33)                                                # 11.5 s of a still page
    assert [c.reason for c in r.page_reads()] == ["periodic", "periodic"]


def test_results_are_absorbed_on_the_next_tick(rig):
    r = rig()
    r.step()                                                  # URL-only read
    r.step()                                                  # the page read is asked and finishes
    assert r.backend.reads and r.sgt.observed == []
    r.step()
    assert len(r.sgt.observed) == 1
    o = r.sgt.observed[0]
    assert (o.hwnd, o.portal, o.tab, o.title) == (HWND, "GST Portal", "tab-1", TITLE)
    assert o.url.startswith("https://services.gst.gov.in/") and o.frame is not None
    assert o.read["lines"] == ["Fictional GST page"] and o.read["docs"]


def test_torn_read_is_dropped_and_asked_again(rig):
    r = rig()
    r.step()
    r.win.docs[0].hooks.append(lambda: setattr(r.win, "tab", "tab-2"))   # the user switches tab mid-read
    r.step(2)
    assert r.sgt.observed == []                               # R1: never absorbed
    assert r.page_reads()[-1].reason == "torn-retry"
    r.step()
    assert [o.tab for o in r.sgt.observed] == ["tab-2"]


def test_scope_stays_fresh_while_a_heavy_read_runs(rig):
    """R2: the URL-only reads go to the scope worker, so a page read that runs for 5 s never closes
    the scope gate (and the screenshot that WP7's OCR flash trigger needs keeps being taken)."""
    r = rig()
    r.step()
    gate = threading.Event()
    r.win.docs[0].gates.append(gate)
    r.clock.advance(TICK)
    r.loop.tick()                                             # the page read starts and blocks
    wait_until(lambda: r.reader.inflight(HWND) is not None)
    shots = r.platform.shots
    for _ in range(15):                                       # 5 s
        r.clock.advance(TICK)
        r.loop.tick()
        wait_until(lambda: r.reader._lanes[HWND].scope.job is None)
        assert r.health.window.call_args.args[1] is True
        assert r.health.window.call_args.kwargs["url_readable"] is True
    assert r.platform.shots - shots == 15
    gate.set()
    r.settle()


def test_switching_sgt_off_never_absorbs_a_read_asked_before(rig):
    r = rig()
    r.step(2)                                                 # a page read finished, not yet taken
    r.loop.end_all("SGT switched off")
    r.step()
    assert ("all", "SGT switched off") in r.sgt.ended
    assert r.sgt.observed == []


def test_a_closed_window_ends_its_sgt_sessions(rig):
    r = rig()
    r.warm()
    r.platform.alive.clear()
    r.win.closed = True
    r.platform.fg = 0
    r.step(int(vsdc_capture.PRUNE_EVERY_SEC / TICK) + 2)
    assert (HWND, "window closed") in r.sgt.ended


def test_sgt_i_can_ask_for_a_read(rig):
    r = rig()
    r.warm()
    r.sgt.want = True
    r.step()
    assert [c.reason for c in r.page_reads()] == ["wants_read"]


# ── The OCR triggers (blueprint 5.1, WP7) ───────────────────────────────────────────────────────

class FakeLane:
    """Stands in for the OCR lane: records what the loop sends; `accept` plays the per-window gap."""

    def __init__(self):
        self.requests = []
        self.accept = True
        self.finished = []
        self.closed = []

    def available(self):
        return True

    def ready(self, hwnd):
        return self.accept

    def request(self, img, ctx):
        if not self.accept:
            return False
        self.requests.append(ctx)
        return True

    def results(self):
        out, self.finished = self.finished, []
        return out

    def close_window(self, hwnd):
        self.closed.append(hwnd)

    def close(self):
        pass

    def finish(self, ctx, lines=("Fictional toast",), tab=None):
        from core.vsdc.vsdc_reader import ReadResult
        tab = ctx.tab if tab is None else tab
        self.finished.append(ReadResult(ctx=ctx, source="ocr", lines=list(lines), docs=[], doc_urls=[], tab_before=tab,
                                        tab_after=tab, url_before=ctx.url, url_after=ctx.url, started_at=ctx.asked_at,
                                        finished_at=ctx.asked_at, scope_rule=ctx.url_source or "title"))


def ocr_rig(rig, **kw):
    r = rig(**kw)
    r.lane = FakeLane()
    r.loop._ocr_lane = r.lane
    r.absorbed = []
    absorb = r.sgt.absorb
    r.sgt.absorb = lambda res, *a, **k: (r.absorbed.append(res.source), absorb(res, *a, **k))[1]
    return r


TOAST = (0, 0, 79, 39)                                            # 8 x 4 cells of a 320 px picture


def test_a_toast_on_a_quick_idle_page_is_left_to_uia(rig):
    r = ocr_rig(rig)
    r.warm()
    r.platform.paint(TOAST)
    r.step(2)
    assert r.lane.requests == []
    assert r.page_reads("frame")                                  # the page read is what sees it


def test_flash_ocr_fires_when_uia_is_inflight(rig):
    r = ocr_rig(rig)
    r.warm()
    gate = threading.Event()
    r.win.docs[0].gates.append(gate)
    r.platform.paint((0, 200, 39, 209))                           # a small change: the page read starts and blocks
    r.clock.advance(TICK)
    r.loop.tick()
    wait_until(lambda: r.reader.inflight(HWND) is not None)
    assert r.lane.requests == []
    r.platform.paint(TOAST)
    r.clock.advance(TICK)
    r.loop.tick()
    gate.set()
    r.settle()
    assert [c.reason for c in r.lane.requests] == ["flash"]
    assert r.lane.requests[0].hwnd == HWND and r.lane.requests[0].tab == "tab-1"


def test_flash_ocr_fires_when_uia_is_slow(rig):
    r = ocr_rig(rig)
    r.warm()
    r.reader.page_ms = lambda hwnd, url: vsdc_gate.FLASH_UIA_SLOW_MS + 100
    r.platform.paint(TOAST)
    r.step(1)
    assert [c.reason for c in r.lane.requests] == ["flash"]


def test_ocr_runs_while_uia_is_paused(rig):
    r = ocr_rig(rig)
    r.warm()
    r.reader.paused = lambda: True
    r.reader.request = lambda ctx: "paused"                       # what the reader answers while paused
    r.platform.paint(TOAST)
    r.step(1)
    assert [c.reason for c in r.lane.requests] == ["paused"]
    r.platform.paint((0, 100, 159, 199), shade=60)                # every further big change asks again
    r.step(1)
    assert [c.reason for c in r.lane.requests] == ["paused", "paused"]


def test_green_box_triggers_ocr_once(rig):
    r = ocr_rig(rig)
    r.warm()
    r.platform.paint((20, 150, 300, 230), shade=0)
    r.platform.img = _green(r.platform.img, (20, 40, 300, 100))
    r.step(1)
    assert [c.reason for c in r.lane.requests] == ["green"]
    r.step(20)                                                    # the banner stays: no second scan
    assert [c.reason for c in r.lane.requests] == ["green"]


def _green(img, box):
    img = img.copy()
    ImageDraw.Draw(img).rectangle(box, fill=(30, 150, 70))
    return img


def test_a_scan_the_lane_refuses_is_asked_again(rig):
    r = ocr_rig(rig)
    r.warm()
    r.lane.accept = False                                         # the window had a scan within the last second
    r.reader.page_ms = lambda hwnd, url: vsdc_gate.FLASH_UIA_SLOW_MS + 100
    r.platform.paint(TOAST)
    r.step(2)
    assert r.lane.requests == []
    r.lane.accept = True
    r.step(1)
    assert [c.reason for c in r.lane.requests] == ["flash"]
    r.step(3)
    assert len(r.lane.requests) == 1                              # asked once, not again and again


def test_an_ocr_scan_is_absorbed_only_after_a_read_confirms_its_tab(rig):
    r = ocr_rig(rig)
    r.warm()
    r.reader.page_ms = lambda hwnd, url: vsdc_gate.FLASH_UIA_SLOW_MS + 100
    r.platform.paint(TOAST)
    r.step(1)
    (ctx,) = r.lane.requests
    r.lane.finish(ctx)
    r.loop._release_ocr(r.clock())
    assert "ocr" not in r.absorbed                                # nothing yet proves the tab was in front
    r.step(2)
    assert r.absorbed.count("ocr") == 1


def test_an_ocr_scan_taken_across_a_tab_switch_is_dropped(rig):
    r = ocr_rig(rig)
    r.warm()
    r.reader.page_ms = lambda hwnd, url: vsdc_gate.FLASH_UIA_SLOW_MS + 100
    r.win.tab = "tab-2"                                           # the user just switched tab: the loop does not know yet
    r.platform.paint(TOAST)                                       # so this picture is the new page, taken as the old one
    r.step(1)
    (ctx,) = r.lane.requests
    assert ctx.tab == "tab-1"
    r.lane.finish(ctx)
    r.step(3)
    assert "ocr" not in r.absorbed


def test_an_ocr_scan_from_before_sgt_was_switched_off_is_never_absorbed(rig):
    r = ocr_rig(rig)
    r.warm()
    r.reader.page_ms = lambda hwnd, url: vsdc_gate.FLASH_UIA_SLOW_MS + 100
    r.platform.paint(TOAST)
    r.step(1)
    (ctx,) = r.lane.requests
    r.lane.finish(ctx)
    r.loop.end_all("SGT switched off")
    r.step(3)
    assert "ocr" not in r.absorbed


def test_a_closed_window_is_forgotten_by_the_lane(rig):
    r = ocr_rig(rig)
    r.warm()
    r.platform.alive.clear()
    r.win.closed = True
    r.platform.fg = 0
    r.step(int(vsdc_capture.PRUNE_EVERY_SEC / TICK) + 2)
    assert HWND in r.lane.closed


# ── The router ───────────────────────────────────────────────────────────────────────────────

def attach_fake_capture(router, hwnd, title, url, lines, img=None):
    """The router's capture loop on a fake platform and the fake reader backend. Returns a function
    that ticks the router once and waits for the reads it asked for."""
    clock = Clock()
    win = Window(pid=1, tab="tab-1", docs=[Doc(url, lines)])
    plat = FakePlatform(clock, hwnd=hwnd, title=title)
    if img is not None:
        plat.img = img
    router.capture_platform = plat
    router.capture_backend = FakeBackend(clock, {hwnd: win})
    router.capture_clock = clock

    def settle():
        reader = router.capture._reader if router.capture is not None else None
        if reader is not None:
            wait_until(lambda: all(w.job is None and w.queued is None
                                   for lane in list(reader._lanes.values()) for w in lane.workers()))
    return clock, win, settle


@pytest.fixture
def harness():
    from tests.test_vsdc247_integration import Harness
    with patch.dict(os.environ):
        for var in ("VSDC247_MODE", "VSDC247_ONLY", "VSDC_UIA_ONLY", "SGT_MODE"):
            os.environ.pop(var, None)
        h = Harness()
        yield h
        h.router._drop_capture()
        vsdc_diag.register_reader(None)


def test_crosshair_path_reads_the_url_with_sgt_off(harness):
    """R7: the crosshair engines keep the router's own URL read; no capture loop is built."""
    r = harness.router
    r.apply_engine_settings(True, True, False, sgt="off")
    harness.screen(["Dashboard"])
    harness.tick()
    r.extract_browser_url.assert_called()
    assert r.capture is None


def test_sgt_alone_uses_only_the_capture_loop(harness):
    r = harness.router
    r.apply_engine_settings(False, False, False, sgt="shadow")
    r._sgt = FakeSgt()
    r._sgt.pending = lambda: 0
    attach_fake_capture(r, 4242, TITLE, PORTAL, ["Fictional GST page"])
    harness.tick()
    r.extract_browser_url.assert_not_called()
    assert r.capture is not None
