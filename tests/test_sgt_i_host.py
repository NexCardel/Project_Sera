"""
SGT-I host tests (blueprint 14.2, W1-1): the contract between SGT-C (the Core) and SGT-I in code.
A failing, slow or hung SGT-I component switches SGT-I off and the Core never notices; SGT-I On
vs Off leaves every tracker row the Core writes byte-identical over the golden replays.
Everything here is fictional.
"""
import json
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import pytest

import core.sgt.sgt_shadow as sgt_shadow
from core.sgt.sgt_replay import replay_session
from core.sgt.sgt_shadow import SgtShadow
from core.sgt.sgt_specs import BUILTIN_FIELDS_PATH, SpecStore
from core.sgt_i import SgtIntelligence, make_observation
from core.vsdc.vsdc_engines import SGT_I_DEFAULT, read_sgt_i_mode

GOLDEN = Path(__file__).resolve().parent / "sgt_golden"
SCENARIOS = sorted(GOLDEN.glob("*.json"))
STORE = SpecStore([BUILTIN_FIELDS_PATH], log=lambda m: None)


def obs(session="s1", lines=("Dashboard",)):
    return make_observation(session_id=session, portal="itr", url="https://example.test/x", title="t",
                            source="uia", lines=list(lines), result={"profile": {}, "datasets": []},
                            profile={"pan": "AAAPZ0000Z"}, draft={"form": "ITR-1"}, ts=1.0, today="2026-09-27")


def wait_for(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


class Comp:
    def __init__(self, fn, name="comp"):
        self.fn, self.name = fn, name

    def observe(self, o, ctx):
        self.fn(o, ctx)


def host(*comps, **kw):
    return SgtIntelligence(list(comps), enabled=True, echo=lambda m: None, **kw)


# ── The switch and the setting ────────────────────────────────────────────────────
def test_setting_defaults_to_off():
    assert SGT_I_DEFAULT == "off"
    assert read_sgt_i_mode(lambda k, d=None: d) == "off"
    assert read_sgt_i_mode(lambda k, d=None: "On") == "on"
    assert read_sgt_i_mode(lambda k, d=None: "garbage") == "off"


def test_off_host_does_nothing():
    seen = []
    h = SgtIntelligence([Comp(lambda o, c: seen.append(o))], enabled=False, echo=lambda m: None)
    h.submit(obs())
    time.sleep(0.05)
    assert seen == [] and h._thread is None
    assert h.wants_read("s1") is False and h.enrichment("s1") == {}


# ── Observation is a read-only copy ──────────────────────────────────────────────
def test_observation_is_frozen():
    profile = {"pan": "AAAPZ0000Z"}
    o = make_observation(session_id="s", portal="p", url="u", title="", source="uia", lines=["a"],
                         result={"datasets": [{"arn": "X"}]}, profile=profile, draft={}, ts=0, today="")
    profile["pan"] = "changed"
    assert o.profile["pan"] == "AAAPZ0000Z"
    with pytest.raises(TypeError):
        o.profile["pan"] = "x"
    assert isinstance(o.result["datasets"], tuple) and isinstance(o.lines, tuple)
    with pytest.raises(Exception):
        o.url = "other"


# ── Isolation: exception, budget overrun, hang ───────────────────────────────────
def test_exception_switches_sgt_i_off():
    def boom(o, c):
        raise RuntimeError("fictional failure")
    h = host(Comp(boom))
    h.submit(obs())
    assert wait_for(lambda: h.tripped is not None)
    assert "fictional failure" in h.tripped and not h.active
    h.set_enabled(True)                     # stays off for the rest of the run
    assert not h.active


def test_budget_overrun_switches_sgt_i_off():
    h = host(Comp(lambda o, c: time.sleep(0.15)), budget_sec=0.05)
    h.submit(obs())
    assert wait_for(lambda: h.tripped is not None)
    assert "budget" in h.tripped


def test_hang_switches_sgt_i_off_without_blocking_the_core():
    release = threading.Event()
    h = host(Comp(lambda o, c: release.wait(5)), hang_sec=0.1)
    h.submit(obs())
    assert wait_for(lambda: h._busy_since is not None)
    time.sleep(0.15)
    t0 = time.perf_counter()
    h.submit(obs())                         # the Core hands over the next page: hang noticed
    assert time.perf_counter() - t0 < 0.05
    assert h.tripped and "hung" in h.tripped
    release.set()
    assert wait_for(lambda: h._thread is None)


def test_enrichment_that_is_not_json_or_too_big_trips():
    h = host(Comp(lambda o, c: c.enrich({"x": object()})))
    h.submit(obs())
    assert wait_for(lambda: h.tripped is not None)
    h2 = host(Comp(lambda o, c: c.enrich({"x": "9" * 5000})))
    h2.submit(obs())
    assert wait_for(lambda: h2.tripped is not None)


# ── Channels ─────────────────────────────────────────────────────────────────────
def test_enrichment_and_more_reads():
    def comp(o, c):
        c.enrich({"lines": len(o.lines)})
        c.ask_more_reads(10)
    h = host(Comp(comp, "counter"))
    h.submit(obs())
    assert wait_for(lambda: h.pages == 1)
    assert h.enrichment("s1") == {"counter": {"lines": 1}}
    got = [h.wants_read("s1") for _ in range(5)]
    assert got == [True, True, True, False, False]      # capped
    assert h.wants_read("other") is False
    h.set_enabled(False)
    assert h.enrichment("s1") == {}


class FakeHost:
    """Stands in for the host inside the Core: deterministic channels."""
    active = True

    def __init__(self, extra=None, reads=0):
        self.extra, self.reads, self.submitted = extra or {}, reads, []

    def submit(self, o):
        self.submitted.append(o)

    def wants_read(self, sid):
        if self.reads:
            self.reads -= 1
            return True
        return False

    def enrichment(self, sid):
        return dict(self.extra)


class Frame:
    def convert(self, *_):
        return self

    def resize(self, *_):
        return self

    def tobytes(self):
        return b"same"


def core(log_dir, host_obj=None):
    lines = json.loads(SCENARIOS[0].read_text(encoding="utf-8"))["pages"][0]["lines"]
    reads = []

    def read_uia(_h):
        reads.append(1)
        return {"lines": lines}
    s = SgtShadow(store=STORE, read_uia=read_uia, log_dir=log_dir, echo=lambda m: None, intelligence=host_obj)
    return s, reads


def test_core_hands_a_copy_and_reads_more_only_when_asked(tmp_path):
    s, reads = core(tmp_path)
    s.observe(1, "itr", "https://example.test/a", frame=Frame())
    s.observe(1, "itr", "https://example.test/a", frame=Frame())
    assert len(reads) == 1                              # unchanged page: not read again
    fake = FakeHost(reads=1)
    s2, reads2 = core(tmp_path, fake)
    s2.observe(1, "itr", "https://example.test/a", frame=Frame())
    s2.observe(1, "itr", "https://example.test/a", frame=Frame())
    s2.observe(1, "itr", "https://example.test/a", frame=Frame())
    assert len(reads2) == 2                             # one extra read, as asked, no more
    assert len(fake.submitted) == 2 and fake.submitted[0].source == "uia"


def test_router_switch_attaches_and_detaches_one_host(tmp_path):
    import os
    from unittest.mock import patch
    from tests.test_vsdc247_integration import Harness
    with patch.dict(os.environ):
        for var in ("VSDC247_MODE", "VSDC247_ONLY", "VSDC_UIA_ONLY", "SGT_MODE"):
            os.environ.pop(var, None)
        r = Harness().router
        r.apply_engine_settings(False, False, False, sgt="live")
        r._sgt, _ = core(tmp_path)
        r._apply_sgt_i()
        assert r._sgt._sgt_i is None                    # default: off
        r.apply_engine_settings(False, False, False, sgt="live", sgt_i=True)
        first = r._sgt._sgt_i
        assert first is not None and first.active
        r.apply_engine_settings(False, False, False, sgt="live", sgt_i=False)
        assert r._sgt._sgt_i is None and not first.active
        r.apply_engine_settings(False, False, False, sgt="live", sgt_i=True)
        assert r._sgt._sgt_i is first                   # one host per run


# ── Core output byte-identical, SGT-I On vs Off ──────────────────────────────────
class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 27, 10, 0, 0)


class _SeqUuid:
    """Session ids are random; each replay gets the same sequence so runs compare byte for byte."""
    n = 0

    @classmethod
    def uuid4(cls):
        cls.n += 1
        return uuid.UUID(int=cls.n)


def _payloads(doc, intelligence, strip=False):
    _SeqUuid.n = 0
    rows =replay_session(doc["pages"], STORE, intelligence=intelligence, keep_payloads=True)["payloads"]
    if strip:
        for r in rows:
            r["raw_payload"].pop("sgt_i", None)
    return json.dumps(rows, sort_keys=True, ensure_ascii=False)


@pytest.mark.parametrize("path", SCENARIOS, ids=[p.stem for p in SCENARIOS])
def test_core_rows_identical_with_sgt_i_on_and_off(path, monkeypatch):
    monkeypatch.setattr(sgt_shadow, "datetime", _FixedDatetime)
    monkeypatch.setattr(sgt_shadow, "uuid", _SeqUuid)
    doc = json.loads(path.read_text(encoding="utf-8"))
    off = _payloads(doc, None)
    # On, with no components (today's app): byte-identical, nothing added.
    assert _payloads(doc, SgtIntelligence([], enabled=True, echo=lambda m: None)) == off

    # On, with components that enrich, ask for reads and even fail: Core fields unchanged.
    def busy(o, c):
        c.enrich({"lines": len(o.lines), "kinds": sorted(o.result.keys())})
        c.ask_more_reads(3)
    live = SgtIntelligence([Comp(busy)], enabled=True, echo=lambda m: None)
    assert _payloads(doc, live, strip=True) == off
    failing = SgtIntelligence([Comp(lambda o, c: 1 / 0)], enabled=True, echo=lambda m: None)
    assert _payloads(doc, failing, strip=True) == off
    # Enrichment, when there is some, lands in raw_payload["sgt_i"] only.
    fake = FakeHost(extra={"comp": {"n": 1}})
    rows = replay_session(doc["pages"], STORE, intelligence=fake, keep_payloads=True)["payloads"]
    assert all(r["raw_payload"]["sgt_i"] == {"comp": {"n": 1}} for r in rows)
    assert _payloads(doc, fake, strip=True) == off
