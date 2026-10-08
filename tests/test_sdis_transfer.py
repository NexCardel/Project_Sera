"""SDIS Part P + excavator: captures travel staff PC -> admin PC over Sera Sync's mutual-TLS transport,
driven by the excavator (parks when the admin PC is off, streams live lines, checks what arrived on
the admin PC). Hand-made files only (no client data)."""

import os
import sys
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pytest

import sync_admin
import sync_identity
import sync_office
from sync_transport import MemberSet, SyncTransport, TransportError

from core.sdis import excavator, transfer

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="device identities use DPAPI (Windows-only)")

HOST = "127.0.0.1"
OLD = time.time() - 3 * 86400


class Node:
    def __init__(self, app_dir: Path):
        self.app_dir = app_dir
        identity = sync_identity.ensure_device_identity(app_dir)
        self.device_id = identity.device_id
        self.cert_pem = identity.cert_pem.decode("ascii")
        self.chain = sync_identity.load_cert_chain_args(app_dir)


@pytest.fixture(scope="module")
def nodes(tmp_path_factory):
    return {name: Node(tmp_path_factory.mktemp(name)) for name in ("admin", "staff")}


def _transport(nodes, own):
    members = MemberSet([(n.device_id, n.cert_pem) for n in nodes.values()], own_cert_pem=nodes[own].cert_pem)
    return SyncTransport(nodes[own].chain, members)


@pytest.fixture
def admin(nodes, tmp_path, monkeypatch):
    """The admin PC's 49159 server, routed by sync_office.dispatch_session like main.py does."""
    monkeypatch.setenv("SDIS_DATA_DIR", str(tmp_path / "sdis"))
    results = []

    def handler(session):
        try:
            sync_office.dispatch_session(session, nodes["admin"].app_dir, engine=None)
            results.append("ok")
        except Exception as exc:
            results.append(exc)

    srv = _transport(nodes, "admin").serve(handler, host=HOST, port=0)
    yield srv, results
    srv.stop()


def _connect(nodes, srv):
    return _transport(nodes, "staff").connect(HOST, srv.address[1], nodes["admin"].device_id)


def _write(folder: Path, day: str, body: bytes) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / f"sdis_{day}.jsonl"
    p.write_bytes(body)
    os.utime(p, (OLD, OLD))
    return p


def _wait(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not pred():
        time.sleep(0.02)
    return pred()


class FakeEngine:
    """The slice of SyncEngine the excavator uses. srv None = the admin PC is switched off."""

    def __init__(self, nodes, srv, app_dir):
        self.nodes, self.srv = nodes, srv
        self.app_dir = str(app_dir)
        self.device_id = nodes["staff"].device_id
        self.admin_pubkey = "pub"
        self.opened = 0

    @contextmanager
    def _conn(self, which):
        yield None

    def _open_session_to(self, device_id):
        assert device_id == self.nodes["admin"].device_id
        self.opened += 1
        if self.srv is None:
            raise TransportError("PC is not reachable (offline)")
        return _connect(self.nodes, self.srv)


@pytest.fixture
def staff_pc(nodes, monkeypatch):
    real = sync_admin.is_admin_pc
    monkeypatch.setattr(sync_admin, "is_admin_pc",
                        lambda app_dir=None, **kw: False if str(app_dir) == str(nodes["staff"].app_dir) else real(app_dir, **kw))
    monkeypatch.setattr(sync_admin, "get_office_admin", lambda conn, pub: {"device_id": nodes["admin"].device_id})


def test_push_two_files_both_acked_and_stored(nodes, admin, tmp_path):
    srv, results = admin
    a = _write(tmp_path / "cap", "2026-10-01", b'{"v":1}\n' * 1000)
    b = _write(tmp_path / "cap", "2026-10-02", b"")
    with _connect(nodes, srv) as session:
        acked = transfer.push(session, [a, b])
    assert sorted(acked) == [a.name, b.name]
    assert _wait(lambda: results == ["ok"])
    corpus = tmp_path / "sdis" / "corpus" / nodes["staff"].device_id
    assert sorted(p.name for p in corpus.iterdir()) == [a.name, b.name]
    assert (corpus / a.name).read_bytes() == a.read_bytes()
    assert (corpus / b.name).read_bytes() == b""


def test_push_reports_progress_per_file(nodes, admin, tmp_path):
    srv, _results = admin
    files = [_write(tmp_path / "cap", f"2026-10-0{i}", b"x\n") for i in (1, 2, 3)]
    seen = []
    with _connect(nodes, srv) as session:
        transfer.push(session, files, progress=lambda i, n: seen.append((i, n)))
    assert seen == [(1, 3), (2, 3), (3, 3)]


def test_non_admin_receiver_acks_nothing(nodes, admin, tmp_path, monkeypatch):
    srv, results = admin
    monkeypatch.setattr(sync_admin, "is_admin_pc", lambda app_dir=None, **kw: False)
    a = _write(tmp_path / "cap", "2026-10-01", b"x" * 5000)
    with _connect(nodes, srv) as session:
        assert transfer.push(session, [a]) == []
    assert _wait(lambda: results == ["ok"])
    assert not (tmp_path / "sdis" / "corpus").exists()
    assert a.exists()


def test_bad_manifest_is_refused(nodes, admin, tmp_path):
    srv, results = admin
    with _connect(nodes, srv) as session:
        session.send({"t": transfer.FRAME_PUSH, "files": [{"name": "../x.jsonl", "size": 1, "sha256": "0" * 64}]})
        assert session.recv(wait=5) == {"t": transfer.FRAME_ACK, "files": []}
    assert not (tmp_path / "sdis" / "corpus").exists()


def test_finished_files_skips_today_recent_and_other_names(tmp_path):
    old = _write(tmp_path, "2026-10-01", b"a")
    recent = _write(tmp_path, "2026-10-02", b"b")
    os.utime(recent, None)
    _write(tmp_path, "2026-10-04", b"c")
    other = tmp_path / "sdis_notes.jsonl"
    other.write_bytes(b"d")
    os.utime(other, (OLD, OLD))
    assert transfer.finished_files(tmp_path, today=date(2026, 10, 4)) == [old]


def test_finished_files_uncapped_lists_everything(tmp_path, monkeypatch):
    for i in range(1, 4):
        _write(tmp_path, f"2026-10-0{i}", b"a" * i)
    monkeypatch.setattr(transfer, "MAX_FILES", 2)
    assert len(transfer.finished_files(tmp_path, today=date(2026, 10, 9))) == 2
    assert len(transfer.finished_files(tmp_path, today=date(2026, 10, 9), cap=False)) == 3


# ----------------------------------------------------------------- excavator

class Events:
    def __init__(self):
        self.lines = []

    def __call__(self, cat, title, detail=""):
        self.lines.append((cat, title, detail))

    def titles(self):
        """The titles in order, a title said again straight away counted once."""
        out = []
        for _c, t, _d in self.lines:
            if not out or out[-1] != t:
                out.append(t)
        return out


def _dig_setup(nodes, srv, tmp_path, events):
    cap = tmp_path / "cap"
    cap.mkdir(exist_ok=True)
    engine = FakeEngine(nodes, srv, nodes["staff"].app_dir)
    exc = excavator.Excavator(engine, folder=cap, on_event=events, received_root=tmp_path / "recv")
    return cap, engine, exc


def test_excavator_sends_deletes_exactly_the_acked_and_streams_lines(nodes, admin, tmp_path, staff_pc, monkeypatch):
    srv, _results = admin
    ev = Events()
    cap, engine, exc = _dig_setup(nodes, srv, tmp_path, ev)
    good = _write(cap, "2026-10-01", b"good\n")
    bad = _write(cap, "2026-10-02", b"bad\n")
    today = cap / f"sdis_{date.today().isoformat()}.jsonl"
    today.write_bytes(b"open\n")
    real = transfer._sha256
    monkeypatch.setattr(transfer, "_sha256", lambda p: "0" * 64 if Path(p).name == bad.name else real(p))
    status = exc.dig()
    assert not good.exists() and bad.exists() and today.exists()
    corpus = tmp_path / "sdis" / "corpus" / nodes["staff"].device_id
    assert sorted(p.name for p in corpus.iterdir()) == [good.name]      # no .part left behind
    assert exc.stats["acked"] == 1 and exc.stats["deleted"] == 1
    assert "Corpus sending" in ev.titles() and "Corpus deleted on this PC" in ev.titles()
    assert status["state"] == "parked" and status["unsent_files"] == 1   # the bad file stays, with a reason
    assert all(cat == "SDIS" for cat, _t, _d in ev.lines)


def test_excavator_idle_opens_no_connection_and_says_nothing(nodes, tmp_path, staff_pc):
    ev = Events()
    cap, engine, exc = _dig_setup(nodes, None, tmp_path, ev)
    (cap / f"sdis_{date.today().isoformat()}.jsonl").write_bytes(b"open\n")
    assert exc.dig()["state"] == "idle"
    assert engine.opened == 0 and ev.lines == []


def test_excavator_parks_when_admin_is_off_and_retries_with_backoff(nodes, tmp_path, staff_pc):
    ev = Events()
    cap, engine, exc = _dig_setup(nodes, None, tmp_path, ev)
    f = _write(cap, "2026-10-01", b"keep me\n")
    for _ in range(3):
        status = exc.dig()
    assert status["state"] == "parked" and "not reachable" in status["reason"]
    assert status["unsent_files"] == 1 and f.exists()
    assert ev.titles() == ["Corpus parked"]                 # said once, not on every retry
    assert exc._fails == 3 and engine.opened == 3
    assert exc.backoff[min(exc._fails - 1, len(exc.backoff) - 1)] == 300.0
    assert "1 file" in ev.lines[0][2] and "kept on this PC" in ev.lines[0][2]


def test_excavator_parks_when_the_admin_pc_is_unknown(nodes, tmp_path, staff_pc, monkeypatch):
    monkeypatch.setattr(sync_admin, "get_office_admin", lambda conn, pub: None)
    ev = Events()
    cap, engine, exc = _dig_setup(nodes, None, tmp_path, ev)
    f = _write(cap, "2026-10-01", b"x\n")
    assert exc.dig()["state"] == "parked"
    assert "does not know the admin PC" in exc.status["reason"] and f.exists() and engine.opened == 0


def test_excavator_recovers_after_the_admin_comes_back(nodes, admin, tmp_path, staff_pc):
    srv, _results = admin
    ev = Events()
    cap, engine, exc = _dig_setup(nodes, None, tmp_path, ev)
    f = _write(cap, "2026-10-01", b"later\n")
    assert exc.dig()["state"] == "parked"
    engine.srv = srv                                         # the admin PC is switched on
    assert exc.dig()["state"] == "idle"
    assert not f.exists() and exc._fails == 0
    assert ev.titles() == ["Corpus parked", "Corpus sending", "Corpus sent to admin PC", "Corpus deleted on this PC"]


def test_excavator_nudge_keeps_a_gap(nodes, tmp_path, staff_pc):
    cap, engine, exc = _dig_setup(nodes, None, tmp_path, Events())
    exc.dig()
    assert exc.nudge() is False                              # just looked
    exc._last_pass -= excavator.NUDGE_GAP_S + 1
    exc._fails = 4
    assert exc.nudge() is True and exc._fails == 0 and exc._wake.is_set()


def test_excavator_on_the_admin_pc_checks_what_arrived(nodes, tmp_path, monkeypatch):
    monkeypatch.setattr(sync_admin, "is_admin_pc", lambda app_dir=None, **kw: True)
    ev = Events()
    cap, engine, exc = _dig_setup(nodes, None, tmp_path, ev)
    recv = tmp_path / "recv"
    assert exc.dig()["state"] == "admin" and ev.lines[-1][2] == "no staff capture has arrived yet"
    d1, d2 = recv / "pc1", recv / "pc2"
    d1.mkdir(parents=True)
    d2.mkdir(parents=True)
    (d1 / "sdis_2026-10-01.jsonl").write_bytes(b'{"a":1}\n{"b":2}\n')
    (d2 / "sdis_2026-10-01.jsonl").write_bytes(b'{"a":1}\n')
    status = exc.dig()
    assert status["received_files"] == 2 and not status["reason"]
    assert ev.lines[-1][1] == "Corpus held on admin PC" and "2 files" in ev.lines[-1][2] and "2 PCs" in ev.lines[-1][2]
    n = len(ev.lines)
    exc.dig()
    assert len(ev.lines) == n                                # unchanged: said once
    (d2 / "sdis_2026-10-02.jsonl").write_bytes(b'{"cut":')   # truncated in transit
    (d2 / "sdis_2026-10-03.jsonl.part").write_bytes(b"half")
    status = exc.dig()
    assert status["reason"] == "unreadable files"
    assert ev.lines[-1][1].endswith("check") and "1 file unreadable" in ev.lines[-1][2] and "half-received" in ev.lines[-1][2]
    assert engine.opened == 0
