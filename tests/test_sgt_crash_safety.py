"""
An app killed or a window closed abruptly must not cost SGT captures or leave unreadable files.
All identifiers are fictional.
"""
import json
from datetime import date

from PIL import Image

from core.sgt.sgt_shadow import SgtShadow
from core.sgt.sgt_specs import BUILTIN_FIELDS_PATH, SpecStore

GST = "GST Portal"
URL = "https://return.gst.gov.in/returns/auth/dashboard"
HEADER = "ASHOK KUMAR SEN 19ABCPD1234E1ZB"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make(tmp_path, page, clock):
    return SgtShadow(store=SpecStore([BUILTIN_FIELDS_PATH], log=lambda m: None),
                     read_uia=lambda h: {"lines": list(page)}, log_dir=tmp_path / "log",
                     state_path=tmp_path / "sessions_state.json", echo=lambda m: None, mode="live",
                     today=lambda: date(2026, 10, 7), clock=clock)


def see(sgt, shade):
    sgt.observe(1, GST, URL, frame=Image.new("RGB", (64, 64), (shade % 250,) * 3))


def records(tmp_path):
    out = []
    for f in (tmp_path / "log").glob("*.jsonl"):
        out += [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
    return out


def snapshot(tmp_path):
    return json.loads((tmp_path / "sessions_state.json").read_text(encoding="utf-8"))


def test_a_change_held_back_by_the_throttle_is_saved_on_the_next_tick(tmp_path):
    clock, page = Clock(), [HEADER, "Dashboard", "Returns"]
    sgt = make(tmp_path, page, clock)
    see(sgt, 10)                                    # first snapshot
    clock.now += 0.5
    page += ["Aggregate Turnover", "1,00,000"]      # a new datapoint, inside the 2 s throttle
    see(sgt, 70)
    assert "aggregate_turnover" not in snapshot(tmp_path)["sessions"][0]["profile"]    # held back, as before
    clock.now += 3.0
    see(sgt, 70)                                    # the same page: nothing new to read, yet the snapshot is written
    assert snapshot(tmp_path)["sessions"][0]["profile"]["aggregate_turnover"]["value"] == "100000"


def test_a_torn_log_line_does_not_swallow_the_next_record(tmp_path):
    log = tmp_path / "log"
    log.mkdir()
    path = log / f"sgt_shadow_{date.today().isoformat()}.jsonl"
    path.write_text('{"event": "x"}\n{"event": "torn', encoding="utf-8")
    sgt = make(tmp_path, [HEADER, "Dashboard", "Returns"], Clock())
    see(sgt, 10)
    sgt.end_all("quit")
    bad = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            json.loads(line)
        except ValueError:
            bad.append(line)
    assert bad == ['{"event": "torn']               # only the torn line itself is lost; every later record reads


def test_a_torn_snapshot_falls_back_to_the_previous_one(tmp_path):
    clock, page = Clock(), [HEADER, "Dashboard", "Returns"]
    sgt = make(tmp_path, page, clock)
    see(sgt, 10)
    clock.now += 3.0
    page += ["Aggregate Turnover", "1,00,000"]
    see(sgt, 70)                                    # a second snapshot: the first becomes .prev
    state = tmp_path / "sessions_state.json"
    assert state.with_suffix(".prev").exists()
    state.write_text('{"saved": "2026-10-07T10:00:00", "sessions": [{"sess', encoding="utf-8")   # killed mid-write
    make(tmp_path, [], Clock())
    ended = [r for r in records(tmp_path) if r.get("event") == "session_end" and "recovered" in r.get("reason", "")]
    assert len(ended) == 1 and ended[0]["payload"]["client_profile"]["gstin"] == "19ABCPD1234E1ZB"
    assert state.with_suffix(".corrupt").exists() and not state.exists()


def test_an_unreadable_snapshot_with_nothing_before_it_is_set_aside_not_fatal(tmp_path):
    state = tmp_path / "sessions_state.json"
    state.write_text("{", encoding="utf-8")
    make(tmp_path, [], Clock())
    assert state.with_suffix(".corrupt").exists() and not state.exists()
