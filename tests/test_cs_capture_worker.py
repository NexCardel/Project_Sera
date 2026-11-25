"""The C# capture worker (native/capture_worker, core/capture/cs_worker): frames, the adapter's translation
back to ReadContext / ReadResult, and the real exe answering Sera's messages. No browser and no page: a
read of a window that does not exist must come back as "no-window", with Sera's own ReadContext.

Page reads themselves (lines and nodes identical to the Python worker on the same pages) need a browser
and are compared by hand; see docs/ucw/ucw-plan.md, "C# worker".
"""

import io
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.capture import cs_worker
from core.capture.reader import ReadContext, ReadResult
from core.capture.remote import RemoteReader
from core.vsdc.vsdc_engines import read_capture_worker

EXE = cs_worker.exe_path()
needs_exe = pytest.mark.skipif(EXE is None, reason="SeraCaptureWorker.exe not built (native/capture_worker/build.bat)")

NO_WINDOW = 0x7FFF0001        # not a window on any PC


def frame_of(obj):
    body = json.dumps(obj).encode("utf-8")
    return cs_worker._HEADER.pack(len(body)) + body


# ---- frames and the adapter (no exe) -----------------------------------------------------------

def test_request_frame_carries_only_what_the_worker_reads():
    ctx = ReadContext(seq=12, hwnd=34, title="Returns", portal="GST Portal", url="https://x.gst.gov.in/a",
                      url_source="document", tab="1.2", frame_sig=b"\x01\x02", asked_at=99.0, reason="burst",
                      background=True)
    data = cs_worker.encode(("request", ctx))
    body = json.loads(data[4:])
    assert cs_worker._HEADER.unpack(data[:4])[0] == len(data) - 4
    assert body == ["request", {"seq": 12, "hwnd": 34, "url": "https://x.gst.gov.in/a", "reason": "burst",
                                "background": True}]


def test_echo_and_watch_frames():
    assert json.loads(cs_worker.encode(("echo", (3, None)))[4:]) == ["echo", [3, None]]
    assert json.loads(cs_worker.encode(("watch_events", (1, 2)))[4:]) == ["watch_events", [1, 2]]
    assert json.loads(cs_worker.encode(("stop",))[4:]) == ["stop"]


def test_nodes_come_back_with_tuples_and_key_order():
    node = {"parent": -1, "depth": 0, "ctype": 50029, "name": "Filed", "rect": [1, 2, 3, 4], "grid": [5, 6]}
    got = cs_worker.read_frame(io.BytesIO(frame_of(["raw", 1, {"docs": [[node]], "ms": 2.5}])))
    n = got[2]["docs"][0][0]
    assert n["rect"] == (1, 2, 3, 4) and n["grid"] == (5, 6)
    assert list(n) == ["parent", "depth", "ctype", "name", "rect", "grid"]


def test_closed_pipe_between_frames_is_none():
    assert cs_worker.read_frame(io.BytesIO(b"")) is None


def test_a_result_gets_seras_own_context_back():
    proc = cs_worker.CsWorkerProcess.__new__(cs_worker.CsWorkerProcess)
    proc._ctx_lock = __import__("threading").Lock()
    proc._ctxs = {}
    proc._ctx_clock = time.monotonic
    ctx = ReadContext(seq=7, hwnd=8, title="T", portal="GST Portal", url="u", frame_sig=b"x", asked_at=1.0)
    out = io.BytesIO()
    proc._write_msg(out, ("request", ctx))
    msg = proc._decode(["result", 5.0, {"seq": 7, "source": "uia", "lines": ["a"], "docs": [[]], "doc_urls": ["u"],
                                        "tab_before": None, "tab_after": "1", "url_before": "u", "url_after": "u",
                                        "started_at": 1.5, "finished_at": 2.0, "error": "", "scope_rule": "document"}])
    kind, now, r = msg
    assert kind == "result" and now == 5.0 and isinstance(r, ReadResult)
    assert r.ctx is ctx
    assert (r.lines, r.docs, r.doc_urls, r.tab_before, r.tab_after, r.scope_rule) == (["a"], [[]], ["u"], None, "1", "document")
    assert r.ms == 500.0
    assert proc._ctxs == {}


def test_setting_accepts_csharp(monkeypatch):
    monkeypatch.delenv("SERA_CAPTURE_WORKER", raising=False)
    assert read_capture_worker(lambda k, d: "csharp") == "csharp"
    monkeypatch.setenv("SERA_CAPTURE_WORKER", "csharp")
    assert read_capture_worker(None) == "csharp"


def test_csharp_is_the_default(monkeypatch):
    monkeypatch.delenv("SERA_CAPTURE_WORKER", raising=False)
    assert read_capture_worker(None) == "csharp"
    assert read_capture_worker(lambda k, d: d) == "csharp"
    assert read_capture_worker(lambda k, d: "process") == "process"


def test_missing_exe_is_none(monkeypatch, tmp_path):
    monkeypatch.setenv(cs_worker.EXE_ENV, str(tmp_path / "nope.exe"))
    monkeypatch.setattr(cs_worker, "REPO", tmp_path)
    assert cs_worker.exe_path() is None


# ---- the real exe ------------------------------------------------------------------------------

@pytest.fixture
def worker():
    notes = []
    r = RemoteReader(spawn=cs_worker.spawn, diag=notes.append, crash_log=notes.append, supervise=False)
    end = time.monotonic() + 15
    while not r._ready and time.monotonic() < end:
        time.sleep(0.01)
    assert r._ready, "the C# worker never said ready"
    yield r, notes
    r.close()


def wait_result(r, seq, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        for res in r.results():
            if res.ctx.seq == seq:
                return res
        time.sleep(0.01)
    raise AssertionError("no result")


@needs_exe
def test_exe_answers_with_no_window_and_the_original_context(worker):
    r, notes = worker
    for reason in ("frame", "scope"):
        ctx = ReadContext(seq=1000 + len(reason), hwnd=NO_WINDOW, url="https://services.gst.gov.in/x", title="t",
                          frame_sig=b"\x00", asked_at=time.monotonic(), reason=reason)
        assert r.request(ctx) == "queued"
        res = wait_result(r, ctx.seq)
        assert res.ctx is ctx
        assert res.error == "no-window"
        assert res.lines == [] and res.docs == []
    assert r.inflight(NO_WINDOW) is None
    assert not [n for n in notes if "crash" in n.lower()]


@needs_exe
def test_exe_beats_and_echoes(worker):
    r, _notes = worker
    beat = r._last_beat
    time.sleep(1.2)
    assert r._last_beat > beat
    assert r._chan.echo_ms(None, timeout=5) >= 0
    st = r.stats()
    assert {"reads", "scope_reads", "lanes", "stuck", "abandoned"} <= set(st)


@needs_exe
def test_exe_raw_read_without_a_lane_is_refused(worker):
    r, _notes = worker
    assert r.read_raw(NO_WINDOW, timeout=5) == {"error": "no-lane", "docs": []}


@needs_exe
def test_exe_exits_when_sera_closes_the_pipe(worker):
    r, _notes = worker
    chan = r._chan
    chan.proc.stdin.close()
    chan.proc.wait(timeout=5)
    assert chan.exit_code() == 0
