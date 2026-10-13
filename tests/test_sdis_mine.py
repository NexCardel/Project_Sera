"""
tests/test_sdis_mine.py - SDIS Part O engine: the state on disk and incremental mining (fictional fixtures only).
"""

import gzip
import json
import threading
from pathlib import Path
from typing import Any, Dict

import pytest

from core.sdis import identity, keys, link_map, mine, store
from core.sdis.memory import PageMemory

FIX = Path(__file__).resolve().parent / "class_diff_align"
# The fictional pages show no PAN, so a session's id is the client digit of its stamp: 20260101_<client>00000.
A1, B1, C1, A2 = "20260101_100000", "20260101_200000", "20260101_300000", "20260102_100000"


@pytest.fixture(autouse=True)
def fictional_ids(monkeypatch):
    monkeypatch.setattr(keys, "VIEW", "raw")
    monkeypatch.setattr(identity, "client_ids", lambda lm: {"pan:P" + s.split()[-1][-6] for s in lm.sessions})


def _rec(name: str, shift: int = 0) -> Dict[str, Any]:
    """Fixture client A or B; shift > 0 turns every digit in its texts into another one."""
    rec = json.loads((FIX / f"client_{name}.json").read_text(encoding="utf-8"))
    rec["page"] = "test.local/p.html"                         # one page for every client
    if shift:
        for doc in rec["docs"]:
            for n in doc:
                n["name"] = "".join(str((int(c) + shift) % 10) if c.isdigit() else c for c in n.get("name", ""))
    return rec


def _write(d: Path, stamp: str, rec: Dict[str, Any]) -> Path:
    p = d / f"key_probe_{stamp}.json"
    p.write_text(json.dumps(rec), encoding="utf-8")
    return p


def _clients(path: Path) -> int:
    return len({c for pm in store.load(path)["memories"] for c in pm.clients})


@pytest.fixture
def caps(tmp_path):
    d = tmp_path / "caps"
    d.mkdir()
    return d


def test_second_run_processes_nothing(caps, tmp_path):
    _write(caps, A1, _rec("A"))
    _write(caps, B1, _rec("B"))
    st = tmp_path / "memory.json.gz"
    first = mine.mine(caps, st)
    assert first["processed"] == 2 and first["clients"] == 2 and first["pages"] == 1
    second = mine.mine(caps, st)
    assert second["processed"] == 0 and second["clients"] == 0 and not second["rebuilt"]
    assert second["verdicts"] == first["verdicts"]


def test_one_new_capture_processes_one(caps, tmp_path):
    _write(caps, A1, _rec("A"))
    _write(caps, B1, _rec("B"))
    st = tmp_path / "memory.json.gz"
    mine.mine(caps, st)
    _write(caps, C1, _rec("A", 3))
    r = mine.mine(caps, st)
    assert r["processed"] == 1 and r["clients"] == 1 and not r["rebuilt"]
    assert _clients(st) == 3


def test_incremental_counts_equal_a_fresh_rebuild(caps, tmp_path):
    st = tmp_path / "memory.json.gz"
    _write(caps, A1, _rec("A"))
    _write(caps, B1, _rec("B"))
    mine.mine(caps, st)
    _write(caps, C1, _rec("A", 3))
    mine.mine(caps, st)
    _write(caps, A2, _rec("A"))                               # a second capture of client 1: the same client again
    r = mine.mine(caps, st)
    assert r["processed"] == 1 and r["clients"] == 1 and not r["rebuilt"]
    fresh = tmp_path / "fresh.json.gz"
    f = mine.mine(caps, fresh, rebuild=True)
    assert f["processed"] == 4 and f["rebuild_all"]
    assert r["verdicts"] == f["verdicts"] and r["verdicts"]
    assert _clients(st) == _clients(fresh) == 3
    assert [(d["key"], d["relevance_pct"]) for d in store.load(st)["datapoints"]] == \
           [(d["key"], d["relevance_pct"]) for d in store.load(fresh)["datapoints"]]


def test_cancel_after_the_first_client_keeps_it_and_resumes(caps, tmp_path):
    for stamp, rec in ((A1, _rec("A")), (B1, _rec("B")), (C1, _rec("A", 3))):
        _write(caps, stamp, rec)
    st = tmp_path / "memory.json.gz"
    stop = threading.Event()
    calls = []

    def progress(done, total, page):
        calls.append((done, total))
        stop.set()

    r = mine.mine(caps, st, progress=progress, cancel=stop)
    assert r["cancelled"] and calls == [(1, 3)]
    assert _clients(st) == 1 and len(store.load(st)["todo"]) == 2
    r2 = mine.mine(caps, st)
    assert not r2["cancelled"] and r2["processed"] == 0 and r2["clients"] == 2
    assert _clients(st) == 3 and store.load(st)["todo"] == []
    f = mine.mine(caps, tmp_path / "fresh.json.gz", rebuild=True)
    assert r2["verdicts"] == f["verdicts"]


def test_corrupt_state_is_rebuilt(caps, tmp_path):
    _write(caps, A1, _rec("A"))
    _write(caps, B1, _rec("B"))
    st = tmp_path / "memory.json.gz"
    good = mine.mine(caps, st)
    st.write_bytes(b"this is not gzip")
    r = mine.mine(caps, st)
    assert r["rebuild_all"] and r["processed"] == 2 and r["verdicts"] == good["verdicts"]
    assert store.load(st) is not None


def test_old_version_is_rebuilt(caps, tmp_path):
    _write(caps, A1, _rec("A"))
    _write(caps, B1, _rec("B"))
    st = tmp_path / "memory.json.gz"
    mine.mine(caps, st)
    with gzip.open(st, "wt", encoding="utf-8") as f:
        json.dump({"version": store.STATE_VERSION - 1, "memories": []}, f)
    assert store.load(st) is None
    r = mine.mine(caps, st)
    assert r["rebuild_all"] and r["processed"] == 2 and r["pages"] == 1


def test_rebuild_keeps_the_users_decisions(caps, tmp_path):
    _write(caps, A1, _rec("A"))
    _write(caps, B1, _rec("B"))
    st = tmp_path / "memory.json.gz"
    mine.mine(caps, st)
    s = store.load(st)
    s["rejected"], s["picked"] = [("amount", "number")], [("x", "y")]
    store.save(s, st)
    mine.mine(caps, st, rebuild=True)
    s = store.load(st)
    assert s["rejected"] == [("amount", "number")] and s["picked"] == [("x", "y")]


def test_save_is_atomic_and_round_trips(caps, tmp_path):
    _write(caps, A1, _rec("A"))
    _write(caps, B1, _rec("B"))
    st = tmp_path / "sub" / "memory.json.gz"
    mine.mine(caps, st)
    assert st.is_file() and not st.with_name(st.name + ".tmp").exists()
    a = store.load(st)
    store.save(a, st)
    b = store.load(st)
    assert json.dumps(store.to_json(a), sort_keys=True) == json.dumps(store.to_json(b), sort_keys=True)
    pa, pb = a["memories"][0], b["memories"][0]
    assert mine.counts(a) == mine.counts(b) and mine.counts(a)
    assert [pa.label(i) for i in range(len(pa.nodes))] == [pb.label(i) for i in range(len(pb.nodes))]
    assert [pa.status(i) for i in range(len(pa.nodes))] == [pb.status(i) for i in range(len(pb.nodes))]


def test_page_memory_add_again_counts_the_client_once():
    maps = {}
    for client, rec in (("a", _rec("A")), ("b", _rec("B")), ("c", _rec("A", 3))):
        m = link_map.LinkMap(client, "test.local/p", link="test.local/p")
        m.add(rec, "20260101_000000")
        maps[client] = m
    once = PageMemory("test.local/p", 2)
    twice = PageMemory("test.local/p", 2)
    for c in "abc":
        once.add(maps[c].to_flat(), c)
        twice.add(maps[c].to_flat(), c)
    for c in "cab":                                          # every client added again, in another order
        twice.add(maps[c].to_flat(), c)
    assert twice.clients == once.clients == ["a", "b", "c"]
    assert twice.summary() == once.summary()
    assert [len(nd["clients"]) for nd in twice.nodes] == [len(nd["clients"]) for nd in once.nodes]
    assert all(len(nd["clients"]) <= 3 for nd in twice.nodes)


def test_changed_owner_of_an_added_session_rebuilds_the_page(caps, tmp_path):
    st = tmp_path / "memory.json.gz"
    _write(caps, A1, _rec("A"))
    _write(caps, B1, _rec("B"))
    mine.mine(caps, st)
    assert store.load(st)["owners"] == {f"read {A1}": "client 1", f"read {B1}": "client 2"}
    _write(caps, "20251231_200000", _rec("B"))               # older, and the id of B: B becomes "client 1", A "client 2"
    r = mine.mine(caps, st)
    assert r["processed"] == 1 and len(r["rebuilt"]) == 1
    assert store.load(st)["owners"][f"read {A1}"] == "client 2"
    f = mine.mine(caps, tmp_path / "fresh.json.gz", rebuild=True)
    assert r["verdicts"] == f["verdicts"] and _clients(st) == 2


def test_grown_capture_file_takes_only_the_new_snapshots(caps, tmp_path):
    st = tmp_path / "memory.json.gz"
    a = _rec("A")
    cap = {"session": A1, "page": a["page"], "browser": "chrome", "snapshots": [{"t": 0.0, "docs": a["docs"]}]}
    path = caps / f"capture_{A1}.json"
    path.write_text(json.dumps(cap), encoding="utf-8")
    _write(caps, B1, _rec("B"))
    mine.mine(caps, st)
    cap["snapshots"].append({"t": 5.0, "docs": a["docs"]})
    path.write_text(json.dumps(cap), encoding="utf-8")
    r = mine.mine(caps, st)
    assert r["processed"] == 1 and r["files"] == 1
    assert mine.mine(caps, st)["processed"] == 0


def test_unreadable_file_is_skipped_and_taken_later(caps, tmp_path):
    st = tmp_path / "memory.json.gz"
    _write(caps, A1, _rec("A"))
    half = caps / f"key_probe_{B1}.json"
    half.write_text('{"page": "test.local/p.html", "docs": [', encoding="utf-8")
    r = mine.mine(caps, st)
    assert r["processed"] == 1 and r["skipped"] == 1
    _write(caps, B1, _rec("B"))
    r = mine.mine(caps, st)
    assert r["processed"] == 1 and r["skipped"] == 0 and _clients(st) == 2
