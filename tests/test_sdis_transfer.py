"""SDIS Part P: captures travel staff PC -> admin PC over Sera Sync's mutual-TLS transport.
Hand-made files only (no client data)."""

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
from sync_transport import MemberSet, SyncTransport

from core.sdis import transfer

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
    def __init__(self, nodes, srv, app_dir):
        self.nodes, self.srv = nodes, srv
        self.app_dir = str(app_dir)
        self.device_id = nodes["staff"].device_id
        self.admin_pubkey = "pub"

    @contextmanager
    def _conn(self, which):
        yield None

    def _open_session_to(self, device_id):
        assert device_id == self.nodes["admin"].device_id
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


def test_pusher_deletes_exactly_the_acked_files(nodes, admin, tmp_path, staff_pc, monkeypatch):
    srv, _results = admin
    cap = tmp_path / "cap"
    good = _write(cap, "2026-10-01", b"good\n")
    bad = _write(cap, "2026-10-02", b"bad\n")
    today = cap / f"sdis_{date.today().isoformat()}.jsonl"
    today.write_bytes(b"open\n")
    real = transfer._sha256
    monkeypatch.setattr(transfer, "_sha256", lambda p: "0" * 64 if Path(p).name == bad.name else real(p))
    pusher = transfer.Pusher(FakeEngine(nodes, srv, nodes["staff"].app_dir), folder=cap)
    assert pusher.run() == 1
    assert not good.exists() and bad.exists() and today.exists()
    corpus = tmp_path / "sdis" / "corpus" / nodes["staff"].device_id
    assert sorted(p.name for p in corpus.iterdir()) == [good.name]      # no .part left behind
    assert pusher.stats["acked"] == 1 and pusher.stats["deleted"] == 1


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


def test_pusher_throttles_and_skips_on_the_admin_pc(nodes, tmp_path, monkeypatch):
    monkeypatch.setattr(sync_admin, "is_admin_pc", lambda app_dir=None, **kw: True)
    _write(tmp_path, "2026-10-01", b"a")
    engine = FakeEngine(nodes, None, nodes["admin"].app_dir)
    pusher = transfer.Pusher(engine, folder=tmp_path)
    assert pusher.run() == 0                                   # admin PC: no push
    assert pusher.on_synced() is True
    assert _wait(lambda: not pusher._thread.is_alive())
    assert pusher.on_synced() is False                         # within 15 minutes
    assert pusher.stats["pushes"] == 0 and pusher.stats["failed"] == 0
