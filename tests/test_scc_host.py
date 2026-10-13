"""
SCC-U host tests (autofill-tweaks blueprint G.1, W5-1): SCC-U gets read-only copies of SGT's pages
on its own thread; a failing, slow or hung handler switches it off and the Core never notices; its
read window only adds reads, for at most 30 s; SCC-U On vs Off leaves every tracker row the Core
writes byte-identical over the golden replays. Everything here is fictional.
"""
import json
import threading
import time

import pytest

import core.sgt.sgt_shadow as sgt_shadow
from core.scc import SccHost
from core.scc.host import READ_WINDOW_MAX_SEC, _Context
from core.sgt.sgt_replay import replay_session
from core.sgt.sgt_shadow import SgtShadow
from core.vsdc.vsdc_engines import SCC_DETECT_DEFAULT, read_scc_detect_mode
from tests.test_sgt_i_host import (SCENARIOS, STORE, Changing, Frame, _FixedDatetime, _SeqUuid, obs,
                                   wait_for)


class Handler:
    def __init__(self, fn, name="handler"):
        self.fn, self.name = fn, name

    def observe(self, o, hwnd, ctx):
        self.fn(o, hwnd, ctx)


def host(*handlers, **kw):
    return SccHost(list(handlers), enabled=True, echo=lambda m: None, **kw)


def _clocked():
    clock = [100.0]
    return host(monotonic=lambda: clock[0]), clock


# ── The switch and the setting ────────────────────────────────────────────────────
def test_setting_defaults_to_off():
    assert SCC_DETECT_DEFAULT == "off"
    assert read_scc_detect_mode(lambda k, d=None: d) == "off"
    assert read_scc_detect_mode(lambda k, d=None: "On") == "on"
    assert read_scc_detect_mode(lambda k, d=None: "garbage") == "off"


def test_off_host_does_nothing():
    seen = []
    h = SccHost([Handler(lambda o, w, c: seen.append(o))], enabled=False, echo=lambda m: None)
    h.submit(obs(), 7)
    h.open_read_window("s1")
    time.sleep(0.05)
    assert seen == [] and h._thread is None and h.wants_read("s1") is False


def test_handler_gets_the_copy_and_the_window():
    got = []
    h = host(Handler(lambda o, w, c: got.append((o.session_id, w))))
    h.submit(obs("sgt-session"), 4242)
    assert wait_for(lambda: h.pages == 1)
    assert got == [("sgt-session", 4242)]


# ── Isolation: exception, budget overrun, hang ───────────────────────────────────
def test_exception_switches_scc_u_off_for_the_run():
    def boom(o, w, c):
        raise RuntimeError("fictional failure")
    h = host(Handler(boom))
    h.submit(obs(), 1)
    assert wait_for(lambda: h.tripped is not None)
    assert "RuntimeError" in h.tripped and not h.active
    h.set_enabled(True)
    assert not h.active


def test_budget_overrun_switches_scc_u_off():
    h = host(Handler(lambda o, w, c: time.sleep(0.15)), budget_sec=0.05)
    h.submit(obs(), 1)
    assert wait_for(lambda: h.tripped is not None)
    assert "budget" in h.tripped


def test_hang_switches_scc_u_off_without_blocking_the_core():
    release = threading.Event()
    h = host(Handler(lambda o, w, c: release.wait(5)), hang_sec=0.1)
    h.submit(obs(), 1)
    assert wait_for(lambda: h._busy_since is not None)
    time.sleep(0.15)
    t0 = time.perf_counter()
    h.submit(obs(), 1)
    assert time.perf_counter() - t0 < 0.05
    assert h.tripped and "hung" in h.tripped
    release.set()
    assert wait_for(lambda: h._thread is None)


# ── The read window ──────────────────────────────────────────────────────────────
def test_read_window_is_bounded_and_never_shortened():
    h, clock = _clocked()
    h.open_read_window("s1", 3600)                       # clamped to 30 s
    assert all(h.wants_read("s1") for _ in range(20))
    assert h.wants_read("other") is False
    clock[0] += READ_WINDOW_MAX_SEC - 1
    _Context(h, "s1").read_harder(1)                     # a shorter ask cannot close it early
    assert h.wants_read("s1") is True
    clock[0] += 1.5
    assert h.wants_read("s1") is False
    h.open_read_window("s1", -5)
    assert h.wants_read("s1") is False


def test_read_window_is_inert_when_off_or_tripped():
    h, _ = _clocked()
    h.open_read_window("s1")
    h.set_enabled(False)
    h.set_enabled(True)
    assert h.wants_read("s1") is False                   # the switch closes every open window
    h.open_read_window("s1")
    h._trip("fictional")
    assert h.wants_read("s1") is False


# ── Inside the Core ──────────────────────────────────────────────────────────────
def _core(tmp_path, scc):
    lines = json.loads(SCENARIOS[0].read_text(encoding="utf-8"))["pages"][0]["lines"]
    reads = []

    def read_uia(_h):
        reads.append(1)
        return {"lines": lines}
    return SgtShadow(store=STORE, read_uia=read_uia, log_dir=tmp_path, echo=lambda m: None, scc=scc), reads


def test_core_hands_a_copy_with_hwnd_and_session(tmp_path):
    got = []
    h = host(Handler(lambda o, w, c: got.append((o.session_id, w, o.source))))
    s, _ = _core(tmp_path, h)
    s.observe(9, "itr", "https://example.test/a", frame=Frame())
    assert wait_for(lambda: len(got) == 1)
    assert got[0] == (s._sessions[9].session_id, 9, "uia")


def test_read_window_only_adds_reads(tmp_path):
    keys = ["a", "a", "b", "b", "b", "c", "c", "a", "a", "a"]

    def ticks(scc, open_at=None):
        s, reads = _core(tmp_path, scc)
        read_at = []
        for i, key in enumerate(keys):
            if i == open_at and scc is not None:
                scc.open_read_window(s._sessions[1].session_id)
            before = len(reads)
            s.observe(1, "itr", "https://example.test/a", frame=Changing(key))
            if len(reads) > before:
                read_at.append(i)
        return read_at

    plain = ticks(None)
    assert plain == [0, 2, 5, 7]
    off = SccHost([], enabled=False, echo=lambda m: None)
    assert ticks(off, open_at=1) == plain
    h, _ = _clocked()
    assert ticks(h, open_at=1) == list(range(len(keys)))
    h2, _ = _clocked()
    assert ticks(h2) == plain


def test_router_switch_attaches_and_detaches_one_host(tmp_path):
    import os
    from unittest.mock import patch
    from tests.test_vsdc247_integration import Harness
    with patch.dict(os.environ):
        for var in ("VSDC247_MODE", "VSDC247_ONLY", "VSDC_UIA_ONLY", "SGT_MODE"):
            os.environ.pop(var, None)
        r = Harness().router
        r.apply_engine_settings(False, False, False, sgt="live")
        r._sgt, _ = _core(tmp_path, None)
        r._apply_scc()
        assert r._sgt._scc is None                       # default: off
        r.apply_engine_settings(False, False, False, sgt="live", scc_detect=True)
        first = r._sgt._scc
        assert first is not None and first.active
        r.apply_engine_settings(False, False, False, sgt="live", scc_detect=False)
        assert r._sgt._scc is None and not first.active
        r.apply_engine_settings(False, False, False, sgt="live", scc_detect=True)
        assert r._sgt._scc is first                      # one host per run


# ── Core output byte-identical, SCC-U On vs Off ──────────────────────────────────
def _payloads(doc, scc):
    _SeqUuid.n = 0
    rows = replay_session(doc["pages"], STORE, keep_payloads=True, scc=scc)["payloads"]
    return json.dumps(rows, sort_keys=True, ensure_ascii=False)


@pytest.mark.parametrize("path", SCENARIOS, ids=[p.stem for p in SCENARIOS])
def test_core_rows_identical_with_scc_u_on_and_off(path, monkeypatch):
    monkeypatch.setattr(sgt_shadow, "datetime", _FixedDatetime)
    monkeypatch.setattr(sgt_shadow, "uuid", _SeqUuid)
    doc = json.loads(path.read_text(encoding="utf-8"))
    off = _payloads(doc, None)
    assert _payloads(doc, SccHost([], enabled=True, echo=lambda m: None)) == off
    reader = SccHost([Handler(lambda o, w, c: c.read_harder())], enabled=True, echo=lambda m: None)
    assert _payloads(doc, reader) == off
    failing = SccHost([Handler(lambda o, w, c: 1 / 0)], enabled=True, echo=lambda m: None)
    assert _payloads(doc, failing) == off
