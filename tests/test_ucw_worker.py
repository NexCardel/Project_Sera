"""UCW (docs/ucw/ucw-plan.md): the capture worker, its frames and RemoteReader's supervisor.

WP1 frames and the worker process; WP2 restart, backoff and local answers; WP3 statuses that are never
blind; WP4 a stalled worker never holds the tick; WP5 the SDIS raw read; WP6 flashes; WP7 page events;
WP8 the numbers. Fake backends only - no browser, no portal, no COM; every page and line is made up.
"""

import io
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.capture import protocol, remote
from core.capture.page_events import EVENT_OBJECT_NAMECHANGE, EVENT_OBJECT_VALUECHANGE, NullEvents, kind_of
from core.capture.rawfilter import control_view_of
from core.capture.reader import ReadContext, ReadResult
from core.capture.remote import RemoteReader, ThreadChild, WorkerProcess
from core.sgt_i import uia_nodes
from core.sgt_i.uia_events import Flash
from tests import ucw_fake_backend
from tests.test_vsdc_reader import PORTAL, Clock, Doc, FakeBackend, Window, wait_until

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HWND = 7
_seq = iter(range(1, 10 ** 6))


def ctx(clock, hwnd=HWND, reason="frame", url=PORTAL):
    return ReadContext(seq=next(_seq), hwnd=hwnd, url=url, asked_at=clock(), reason=reason)


def collect(reader, n, timeout=5.0):
    out, end = [], time.monotonic() + timeout
    while len(out) < n and time.monotonic() < end:
        out.extend(reader.results())
        if len(out) < n:
            time.sleep(0.005)
    assert len(out) >= n, f"only {len(out)} of {n} results arrived"
    return out


class RealClock:
    """The supervisor's clock, moved by hand."""

    def __init__(self):
        self.t = 5000.0

    def __call__(self):
        return self.t


@pytest.fixture
def made():
    readers = []
    gates = []
    yield readers, gates
    for g in gates:
        g.set()
    for r in readers:
        r.close()


def thread_reader(made, backend, clock, real=None, **child_kw):
    chans = []

    def spawn(on_message):
        chans.append(ThreadChild(backend, on_message, clock=clock, **child_kw))
        return chans[-1]
    reader = RemoteReader(spawn=spawn, clock=clock, real_clock=real or time.monotonic, diag=lambda t: None,
                          crash_log=lambda t: None, supervise=False)
    made[0].append(reader)
    wait_until(lambda: reader._ready)
    return reader, chans


def one_window(*docs):
    clock = Clock()
    win = Window(pid=1, tab="tab-1", docs=list(docs) or [Doc(PORTAL, ["Fictional GST page"])])
    return clock, win, FakeBackend(clock, {HWND: win})


# ---- WP1: frames and the worker process ---------------------------------------------------------

def test_a_frame_round_trips_a_read_result():
    c = ReadContext(seq=3, hwnd=HWND, url=PORTAL, asked_at=1.0, reason="frame")
    r = ReadResult(c, lines=["Fictional"], docs=[[{"parent": -1, "depth": 0, "ctype": 1, "name": "Fictional"}]])
    buf = io.BytesIO()
    protocol.write(buf, ("result", 2.0, r))
    protocol.write(buf, ("beat", 2.5, {}))
    buf.seek(0)
    kind, at, back = protocol.read(buf)
    assert kind == "result" and at == 2.0 and back.ctx == c and back.lines == ["Fictional"] and back.docs == r.docs
    assert protocol.read(buf)[0] == "beat"
    assert protocol.read(buf) is None                       # closed cleanly between frames


def test_a_frame_cut_short_is_an_error_not_a_message():
    data = protocol.encode(("beat", 1.0, {}))
    with pytest.raises(protocol.ProtocolError):
        protocol.read(io.BytesIO(data[:-2]))
    with pytest.raises(protocol.ProtocolError):
        protocol.read(io.BytesIO(protocol.HEADER.pack(protocol.MAX_FRAME + 1)))


def fake_worker_command():
    return [sys.executable, os.path.join(ROOT, "main.py"), protocol.FLAG, "--fake-backend", "tests.ucw_fake_backend:make"]


def test_the_worker_process_answers_a_request_with_the_context_it_was_asked_with(made):
    reader = RemoteReader(spawn=lambda m: WorkerProcess(fake_worker_command(), m), diag=lambda t: None,
                          crash_log=lambda t: None)
    made[0].append(reader)
    wait_until(lambda: reader._ready, timeout=30.0)
    c = ctx(time.monotonic)
    assert reader.request(c) == "queued"
    (r,) = collect(reader, 1, timeout=10.0)
    assert r.ctx == c and not r.error and r.lines == list(ucw_fake_backend.LINES)
    assert r.started_at >= c.asked_at - 0.1 and r.finished_at >= r.started_at
    raw = reader.read_raw(HWND)                             # WP5, through the real pipe
    assert raw["docs"] and raw["docs"][0][0]["name"] == ucw_fake_backend.LINES[0] and "ms" in raw
    wait_until(lambda: reader.stats().get("lanes") == 1, timeout=5.0)   # numbers come with the beats


def test_a_stray_print_in_the_worker_never_breaks_a_frame(made, capfd):
    proc = WorkerProcess(fake_worker_command())
    try:
        assert proc.wait_ready(30.0)
        assert proc.echo_ms({"x": 1}) >= 0                  # frames still parse after the worker's start-up
    finally:
        proc.kill()


# ---- WP2: the supervisor --------------------------------------------------------------------------

def test_a_killed_worker_is_replaced_and_its_open_reads_come_back_worker_restart(made):
    gate = threading.Event()
    made[1].append(gate)
    doc = Doc(PORTAL, ["Fictional GST page"])
    doc.gates.append(gate)
    clock, win, backend = one_window(doc)
    reader, chans = thread_reader(made, backend, clock)
    c = ctx(clock)
    reader.request(c)
    wait_until(lambda: backend.reads)
    assert reader.inflight(HWND) == c
    chans[-1].kill()
    reader.supervise_once()                                  # sees it gone: restart
    (r,) = collect(reader, 1)
    assert r.ctx == c and r.error == "worker-restart"
    assert reader.inflight(HWND) is None
    reader.supervise_once()                                  # starts the next one
    wait_until(lambda: reader._ready)
    assert len(chans) == 2 and reader.stats()["restarts"] == 1
    c2 = ctx(clock)
    reader.request(c2)
    (r2,) = collect(reader, 1)
    assert r2.ctx == c2 and not r2.error


def test_a_silent_worker_is_replaced(made):
    clock, win, backend = one_window()
    real = RealClock()
    reader, chans = thread_reader(made, backend, clock, real=real)
    real.t += remote.SILENCE_SEC - 0.5
    reader.supervise_once()
    assert len(chans) == 1
    real.t += 1.0                                            # beats keep coming in real time, but this
    reader._last_beat = real.t - remote.SILENCE_SEC - 0.1    # supervisor's clock says none for 3 s
    reader.supervise_once()
    assert reader._chan is None and not chans[0].alive()


def test_three_restarts_in_a_minute_wait_before_the_next_start(made):
    clock, win, backend = one_window()
    real = RealClock()
    reader, chans = thread_reader(made, backend, clock, real=real)
    for i in range(remote.RESTART_BURST):
        chans[-1].kill()
        reader.supervise_once()                              # restart
        if i < remote.RESTART_BURST - 1:
            reader.supervise_once()                          # start
            wait_until(lambda: reader._ready)
        real.t += 5.0
    assert reader.paused() and reader.restarting()           # OCR takes over; health uia-restarting
    assert reader.request(ctx(clock)) == "paused"
    n = len(chans)
    real.t += remote.RESTART_BACKOFF_SEC - 5 - 1               # the loop above already moved 5 s
    reader.supervise_once()
    assert len(chans) == n                                   # still waiting
    real.t += 2
    reader.supervise_once()
    wait_until(lambda: reader._ready)
    assert not reader.paused() and len(chans) == n + 1       # never off for the run


def test_inflight_and_page_ms_need_no_round_trip(made):
    gate = threading.Event()
    made[1].append(gate)
    first, second = Doc(PORTAL, ["Fictional GST page"]), None
    clock, win, backend = one_window(first)
    reader, chans = thread_reader(made, backend, clock)
    reader.request(ctx(clock))
    collect(reader, 1)
    first.gates.append(gate)
    c = ctx(clock)
    reader.request(c)
    wait_until(lambda: len(backend.reads) == 2)
    sent = []
    chans[-1].send = lambda msg, real=chans[-1].send: (sent.append(msg), real(msg))[1]
    for _ in range(50):
        assert reader.inflight(HWND) == c
        assert reader.page_ms(HWND, PORTAL) >= 0.0
    assert sent == []
    gate.set()
    collect(reader, 1)
    assert reader.inflight(HWND) is None


def test_the_worker_exits_rather_than_pausing_uia_for_minutes(made):
    clock, win, backend = one_window()
    reader, chans = thread_reader(made, backend, clock)
    chans[-1].reader._paused_until = clock() + 300.0         # as after MAX_WEDGED_WORKERS stuck reads
    chans[-1]._worker._wake.set()
    wait_until(lambda: not chans[-1].alive())
    assert chans[-1].exit_codes == [3]
    reader.supervise_once()
    reader.supervise_once()
    wait_until(lambda: reader._ready)
    assert len(chans) == 2 and not reader.paused()


def test_a_queued_read_replaced_in_the_worker_is_no_longer_in_flight(made):
    gate = threading.Event()
    made[1].append(gate)
    doc = Doc(PORTAL, ["Fictional GST page"])
    doc.gates.append(gate)
    clock, win, backend = one_window(doc)
    reader, chans = thread_reader(made, backend, clock)
    c1, c2, c3 = ctx(clock), ctx(clock), ctx(clock)
    for c in (c1, c2, c3):
        reader.request(c)
    wait_until(lambda: c2.seq not in reader._sent)           # the worker said: replaced by c3
    gate.set()
    got = collect(reader, 2)
    assert {r.ctx for r in got} == {c1, c3}
    assert not reader._sent


# ---- WP3: never blind -----------------------------------------------------------------------------

def test_an_empty_document_right_after_a_url_change_is_loading_not_blind(made):
    clock, win, backend = one_window(Doc(PORTAL, []))
    reader, chans = thread_reader(made, backend, clock)
    reader.request(ctx(clock, reason="url"))
    reader.request(ctx(clock, reason="frame"))
    a, b = collect(reader, 2)
    assert a.error == "loading"
    assert b.error == "" and b.lines == []                   # later reads: an empty page is just empty


def test_the_loop_never_counts_loading_or_a_restart_as_blind_and_asks_again(made, monkeypatch):
    import tests.test_vsdc_capture as cap
    monkeypatch.setattr(cap, "READER", "remote")
    r = cap.Rig(docs=[Doc(PORTAL, [])])
    try:
        r.step(4)                                            # scope, then the burst: all "loading"
        assert r.sgt.observed == []
        assert r.loop._counts["loading"] >= 1 and r.loop._counts["errors"] == 0
        asked = len(r.page_reads())
        r.win.docs[0].lines = ["Fictional GST page"]
        r.step(3)
        assert len(r.page_reads()) > asked and r.sgt.observed and r.sgt.observed[-1].read["lines"]
    finally:
        r.close()


def test_a_restart_mid_read_is_asked_again_and_never_absorbed(made, monkeypatch):
    import tests.test_vsdc_capture as cap
    monkeypatch.setattr(cap, "READER", "remote")
    r = cap.Rig()
    try:
        r.warm()
        gate = threading.Event()
        made[1].append(gate)
        r.win.docs[0].gates.append(gate)
        r.clock.advance(cap.loop.REREAD_AFTER_SEC)
        r.loop.tick()
        wait_until(lambda: r.backend.reads)
        seen = len(r.sgt.observed)
        r.reader._chan.kill()
        r.reader.supervise_once()
        r.reader.supervise_once()
        wait_until(lambda: r.reader._ready)
        gate.set()
        r.step()                                             # takes the worker-restart result
        assert len(r.sgt.observed) == seen
        assert r.loop._counts["restart"] == 1 and r.loop._counts["errors"] == 0
        r.step(3)
        assert len(r.sgt.observed) > seen                    # asked again and read
    finally:
        r.close()


# ---- WP4: the capture thread never waits on the worker -------------------------------------------

def test_a_tick_with_a_stuck_worker_stays_inside_its_budget(made, monkeypatch):
    import tests.test_vsdc_capture as cap
    monkeypatch.setattr(cap, "READER", "remote")
    r = cap.Rig()
    try:
        r.warm()
        gate = threading.Event()
        made[1].append(gate)
        for _ in range(5):
            r.win.docs[0].gates.append(gate)                 # every read now hangs in the "browser"
        worst = 0.0
        for _ in range(30):
            r.clock.advance(cap.TICK)
            t0 = time.perf_counter()
            r.loop.tick()
            worst = max(worst, time.perf_counter() - t0)
        assert worst < 0.05, f"a tick took {worst * 1000:.0f} ms"
        gate.set()
    finally:
        r.close()


# ---- WP5: SDIS ------------------------------------------------------------------------------------

class RawBackend(FakeBackend):
    def __init__(self, clock, windows, raw_gate=None):
        super().__init__(clock, windows)
        self.raw_reads = []
        self.raw_gate = raw_gate

    def read_raw_page(self, hwnd):
        self.raw_reads.append(hwnd)
        if self.raw_gate is not None:
            assert self.raw_gate.wait(10)
        return {"docs": [[{"parent": -1, "depth": 0, "ctype": 50020, "name": "Fictional raw", "rect": (0, 0, 1, 1)}]]}


def test_the_sdis_raw_read_waits_behind_a_page_read_and_reports_its_own_time(made):
    gate = threading.Event()
    made[1].append(gate)
    clock = Clock()
    doc = Doc(PORTAL, ["Fictional GST page"])
    win = Window(pid=1, tab="tab-1", docs=[doc])
    backend = RawBackend(clock, {HWND: win})
    reader, chans = thread_reader(made, backend, clock)
    reader.request(ctx(clock))
    collect(reader, 1)
    doc.gates.append(gate)
    reader.request(ctx(clock))
    wait_until(lambda: len(backend.reads) == 2)
    box = []
    t = threading.Thread(target=lambda: box.append(reader.read_raw(HWND)))
    t.start()
    time.sleep(0.1)
    assert backend.raw_reads == []                           # one queue: SGT's read first
    gate.set()
    t.join(5)
    assert box and box[0]["docs"][0][0]["name"] == "Fictional raw" and box[0]["ms"] >= 0.0
    assert backend.raw_reads == [HWND]


def test_the_recorder_judges_its_budget_by_the_read_not_the_wait(tmp_path):
    from core.sdis.recorder import SdisRecorder
    rec = SdisRecorder(directory=tmp_path, budget_s=0.05, browser_of=lambda h: "chrome")

    def slow_wait_fast_read(hwnd):
        time.sleep(0.1)                                      # waited in the lane
        return {"docs": [[{"parent": -1, "depth": 0, "ctype": 50020, "name": "Fictional", "rect": None}]], "ms": 10.0}
    rec.set_reader(slow_wait_fast_read)
    assert rec._take(HWND, "s1", "GST Portal", PORTAL, "Fictional") is not None
    assert rec.stats["written"] == 1 and rec.stats["slow"] == 0


def test_the_control_view_rebuilt_from_raw_nodes_gives_the_same_lines():
    raw = [
        {"parent": -1, "depth": 0, "ctype": 50026, "name": "", "ctl": False},          # a raw-only group
        {"parent": 0, "depth": 1, "ctype": 50020, "name": "Fictional heading", "ctl": True},
        {"parent": 0, "depth": 1, "ctype": 50026, "name": "", "ctl": False},
        {"parent": 2, "depth": 2, "ctype": 50004, "name": "Fictional field", "value": "AB12", "ctl": True},
        {"parent": 3, "depth": 3, "ctype": 50020, "name": "Fictional hint", "ctl": True},
    ]
    control = [
        {"parent": -1, "depth": 0, "ctype": 50020, "name": "Fictional heading"},
        {"parent": -1, "depth": 0, "ctype": 50004, "name": "Fictional field", "value": "AB12"},
        {"parent": 1, "depth": 1, "ctype": 50020, "name": "Fictional hint"},
    ]
    got = control_view_of(raw)
    assert got == control
    assert uia_nodes.lines_from_nodes([got], include_selection=True) == \
        uia_nodes.lines_from_nodes([control], include_selection=True)


# ---- WP6: flashes ---------------------------------------------------------------------------------

class FakeFlashWatcher:
    def __init__(self):
        self.watched = {}
        self.ready = []

    def watch(self, hwnd, key=""):
        self.watched[hwnd] = key
        self.ready.append(Flash("live_region", ("Fictional ARN AA0000000000000",), time.monotonic()))

    def take(self, hwnd):
        got, self.ready = self.ready, []
        return got

    def stop(self):
        self.watched.clear()


def test_flashes_caught_in_the_worker_reach_sgt_i(made):
    clock, win, backend = one_window()
    reader, chans = thread_reader(made, backend, time.monotonic, flash_factory=FakeFlashWatcher)
    flashes = reader.flashes
    flashes.watch(HWND, PORTAL)
    wait_until(lambda: flashes.pending(HWND))
    (f,) = flashes.take(HWND)
    assert f.kind == "live_region" and f.lines == ("Fictional ARN AA0000000000000",)
    assert not flashes.pending(HWND) and flashes.caught == 1
    assert chans[-1]._worker._flash_windows == {HWND: PORTAL}


def test_sgt_i_host_takes_the_worker_flash_listener():
    from core.sgt_i.host import SgtIntelligence
    stopped = []
    old = type("Old", (), {"stop": lambda self: stopped.append(1)})()
    host = SgtIntelligence([], flashes=old)
    new = object()
    host.set_flashes(new)
    assert host._flashes is new and stopped == [1]


# ---- WP7: page events -----------------------------------------------------------------------------

def test_which_winevents_are_page_changes():
    assert kind_of(EVENT_OBJECT_NAMECHANGE, 5, 5, 0) == "title"
    assert kind_of(EVENT_OBJECT_NAMECHANGE, 6, 5, 0) == ""          # a name inside the page: not a title
    assert kind_of(EVENT_OBJECT_NAMECHANGE, 5, 5, -4) == ""
    assert kind_of(EVENT_OBJECT_VALUECHANGE, 6, 5, -4) == "value"


def test_a_page_event_asks_for_the_url_at_once_and_at_most_so_often(made):
    clock, win, backend = one_window()
    events = []

    def factory(sink, diag=None):
        events.append(NullEvents(sink, diag))
        return events[-1]
    reader, chans = thread_reader(made, backend, clock, events_factory=factory)
    reader.request(ctx(clock, reason="scope"))
    collect(reader, 1)
    wait_until(lambda: events and HWND in events[0].watched)
    events[0].sink(HWND, "title")
    (r,) = collect(reader, 1)
    assert r.url_only and r.url == PORTAL and r.ctx.seq >= 10 ** 9
    events[0].sink(HWND, "value")                            # too soon after: no second read
    time.sleep(0.1)
    assert reader.results() == []
    clock.advance(remote.EVENT_MIN_GAP_SEC + 0.01)
    events[0].sink(HWND, "value")
    assert collect(reader, 1)[0].url_only


# ---- WP8: the numbers ------------------------------------------------------------------------------

def test_the_heartbeat_numbers_cover_restarts_and_read_times(made):
    clock, win, backend = one_window()
    reader, chans = thread_reader(made, backend, clock)
    for _ in range(4):
        reader.request(ctx(clock))
        collect(reader, 1)
    st = reader.stats(reset=True)
    assert st["restarts"] == 0 and "read_p50" in st and "read_p95" in st and "pipe_ms" in st
    assert "read_p50" not in reader.stats()                  # reset with the interval


def test_health_names_the_restarting_state():
    from core.vsdc import vsdc_health
    assert "uia-restarting" in vsdc_health.STATES and "uia-restarting" in vsdc_health.FAILURE_STATES
    assert vsdc_health.SENTENCES["uia-restarting"]


def test_the_setting_and_its_override(monkeypatch):
    from core.vsdc.vsdc_engines import read_capture_worker
    monkeypatch.delenv("SERA_CAPTURE_WORKER", raising=False)
    assert read_capture_worker(lambda k, d: d) == "csharp"          # the C# worker is the default
    assert read_capture_worker(lambda k, d: "process") == "process"
    assert read_capture_worker(lambda k, d: "inproc") == "inproc"
    assert read_capture_worker(lambda k, d: "nonsense") == "csharp"
    monkeypatch.setenv("SERA_CAPTURE_WORKER", "inproc")
    assert read_capture_worker(lambda k, d: "process") == "inproc"
