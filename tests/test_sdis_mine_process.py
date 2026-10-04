"""
tests/test_sdis_mine_process.py - SDIS Part O: mining in its own process (fictional fixtures only).
"""

import json
import sys
import threading
from pathlib import Path
from typing import Any, Dict

import pytest

from core.sdis import identity, keys, mine, mine_process, store
from core.sdis.miner_client import REPO, MinerClient

FIX = Path(__file__).resolve().parent / "class_diff_align"
STAMPS = ("20260101_100000", "20260101_200000", "20260101_300000")

# The child cannot see monkeypatches: this wrapper applies the same fictional ids (a session's id is the
# client digit of its stamp, as in test_sdis_mine.py) and can slow each client map down, then runs run().
WRAPPER = """
import sys, time
sys.path.insert(0, {repo!r})
from core.sdis import identity, keys, mine
keys.VIEW = "raw"
identity.client_ids = lambda lm: {{"pan:P" + s.split()[-1][-6] for s in lm.sessions}}
_place = mine.place_map
def slow(*a, **k):
    time.sleep({delay})
    return _place(*a, **k)
mine.place_map = slow
from core.sdis.mine_process import run
sys.exit(run(sys.argv))
"""


@pytest.fixture(autouse=True)
def fictional_ids(monkeypatch):
    monkeypatch.setattr(keys, "VIEW", "raw")
    monkeypatch.setattr(identity, "client_ids", lambda lm: {"pan:P" + s.split()[-1][-6] for s in lm.sessions})


def _rec(name: str, shift: int = 0) -> Dict[str, Any]:
    rec = json.loads((FIX / f"client_{name}.json").read_text(encoding="utf-8"))
    rec["page"] = "test.local/p.html"
    if shift:
        for doc in rec["docs"]:
            for n in doc:
                n["name"] = "".join(str((int(c) + shift) % 10) if c.isdigit() else c for c in n.get("name", ""))
    return rec


@pytest.fixture
def caps(tmp_path):
    d = tmp_path / "caps"
    d.mkdir()
    for stamp, rec in zip(STAMPS, (_rec("A"), _rec("B"), _rec("A", 3))):
        (d / f"key_probe_{stamp}.json").write_text(json.dumps(rec), encoding="utf-8")
    return d


def _lines(out: str):
    return [json.loads(l) for l in out.splitlines() if l.strip()]


def _clients(path: Path) -> int:
    return len({c for pm in store.load(path)["memories"] for c in pm.clients})


class _Wrapped(MinerClient):
    def __init__(self, script: Path, **kw: Any) -> None:
        super().__init__(**kw)
        self.script = script

    def command(self, captures, state, rebuild=False):
        return [sys.executable, str(self.script)] + super().command(captures, state, rebuild)[-5:]


def _wrapper(tmp_path: Path, delay: float) -> Path:
    p = tmp_path / "child.py"
    p.write_text(WRAPPER.format(repo=str(REPO), delay=delay), encoding="utf-8")
    return p


def test_run_prints_progress_then_ok(caps, tmp_path, capsys):
    st = tmp_path / "memory.json.gz"
    code = mine_process.run(["main.py", "--sdis-mine", "--captures", str(caps), "--state", str(st)])
    lines = _lines(capsys.readouterr().out)
    assert code == 0
    assert [(l["done"], l["total"]) for l in lines[:-1]] == [(1, 3), (2, 3), (3, 3)]
    assert all(l["page"] for l in lines[:-1])
    assert lines[-1]["result"] == "ok" and lines[-1]["clients"] == 3 and lines[-1]["pages"] == 1
    assert _clients(st) == 3


def test_rebuild_flag(caps, tmp_path, capsys):
    st = tmp_path / "memory.json.gz"
    mine.mine(caps, st)
    assert mine_process.run(["x.exe", "--sdis-mine", "--captures", str(caps), "--state", str(st), "--rebuild"]) == 0
    last = _lines(capsys.readouterr().out)[-1]
    assert last["result"] == "ok" and last["rebuild_all"] and last["clients"] == 3


def test_bad_captures_folder_is_an_error(tmp_path, capsys):
    code = mine_process.run(["x.exe", "--sdis-mine", "--captures", str(tmp_path / "nope"),
                             "--state", str(tmp_path / "m.json.gz")])
    lines = _lines(capsys.readouterr().out)
    assert code == 1 and len(lines) == 1 and lines[0]["result"] == "error" and lines[0]["message"]
    assert not (tmp_path / "m.json.gz").exists()


def test_missing_captures_argument_is_an_error(capsys):
    assert mine_process.run(["x.exe", "--sdis-mine"]) == 1
    assert _lines(capsys.readouterr().out)[-1]["result"] == "error"


def test_command_frozen_and_from_source(monkeypatch):
    c = MinerClient()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert c.command("C", "S")[1:] == ["--sdis-mine", "--captures", "C", "--state", "S"]
    monkeypatch.setattr(sys, "frozen", False)
    cmd = c.command("C", "S", rebuild=True)
    assert cmd[1] == str(REPO / "main.py") and cmd[2:] == ["--sdis-mine", "--captures", "C", "--state", "S", "--rebuild"]


def test_client_end_to_end(caps, tmp_path):
    st = tmp_path / "memory.json.gz"
    seen, done = [], []
    c = _Wrapped(_wrapper(tmp_path, 0), on_progress=lambda *a: seen.append(a[:2]), on_done=done.append)
    c.start(caps, st)
    assert c.wait(120)
    assert seen == [(1, 3), (2, 3), (3, 3)]
    assert done[0]["result"] == "ok" and done[0]["clients"] == 3
    assert _clients(st) == 3 and not c.running()


def test_cancel_mid_run_leaves_a_loadable_state_and_the_next_run_continues(caps, tmp_path):
    st = tmp_path / "memory.json.gz"
    first = threading.Event()
    done = []

    def progress(n, total, page):
        first.set()

    c = _Wrapped(_wrapper(tmp_path, 3), on_progress=progress, on_done=done.append)
    c.start(caps, st)
    assert first.wait(120)
    c.cancel()
    assert c.wait(30)
    assert done == [{"result": "cancelled"}]
    s = store.load(st)
    before = _clients(st)
    assert s is not None and 1 <= before < 3 and s["todo"]
    r = mine.mine(caps, st)                                   # the next run continues where it stopped
    assert r["processed"] == 0 and r["clients"] == 3 - before
    assert _clients(st) == 3 and store.load(st)["todo"] == []


def test_main_py_dispatches_before_qt(caps, tmp_path):
    """The real entry point: `python main.py --sdis-mine` answers with JSON lines (no ids patched here)."""
    done = []
    c = MinerClient(on_done=done.append)
    c.start(caps, tmp_path / "memory.json.gz")
    assert c.wait(180)
    assert done and done[0]["result"] == "ok"
