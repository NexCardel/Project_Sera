"""Tests for sync_transport.py (Sera Sync v3, WP P2-3): mutual TLS + framing on localhost.

Device identities come from sync_identity (P2-1) under pytest's tmp dirs; the real data
folder is never touched. Servers bind 127.0.0.1 on a free port, never 49159.
"""

import ast
import hashlib
import os
import socket
import sqlite3
import struct
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import sync_identity
import sync_transport
from sync_transport import (Busy, FrameTimeout, FrameTooLarge, MemberSet, NotAMember, ProtocolError,
                            SessionDeadline, SyncTransport, TransportError, WrongPeer)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="device identities use DPAPI (Windows-only)")

HOST = "127.0.0.1"


class Node:
    def __init__(self, app_dir: Path):
        self.app_dir = app_dir
        self.identity = sync_identity.ensure_device_identity(app_dir)
        self.device_id = self.identity.device_id
        self.cert_pem = self.identity.cert_pem.decode("ascii")
        self.chain = sync_identity.load_cert_chain_args(app_dir)


@pytest.fixture(scope="module")
def nodes(tmp_path_factory):
    return {name: Node(tmp_path_factory.mktemp(name)) for name in ("a", "b", "c", "outsider")}


def _members(nodes, *names, own):
    return MemberSet([(nodes[n].device_id, nodes[n].cert_pem) for n in names], own_cert_pem=nodes[own].cert_pem)


def _transport(nodes, own, *members, **kw):
    return SyncTransport(nodes[own].chain, _members(nodes, *members, own=own), **kw)


class Recorder:
    """Server handler + on_reject callback that records what happened."""

    def __init__(self, body=None):
        self.body = body or self.echo
        self.sessions = []
        self.errors = []
        self.rejects = []
        self.done = threading.Event()

    @staticmethod
    def echo(session):
        while True:
            frame = session.recv()
            if frame.get("t") == "bye":
                return
            session.send({"t": "echo", "peer": session.peer_device_id, "frame": frame})

    def handler(self, session):
        self.sessions.append(session.peer_device_id)
        try:
            self.body(session)
        except Exception as exc:
            self.errors.append(exc)
            raise
        finally:
            self.done.set()

    def on_reject(self, address, reason):
        self.rejects.append(reason)


def _wait(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


@pytest.fixture
def servers():
    started = []
    yield started
    for srv in started:
        srv.stop()


def _serve(servers, transport, recorder, **kw):
    srv = transport.serve(recorder.handler, host=HOST, port=0, on_reject=recorder.on_reject, **kw)
    servers.append(srv)
    return srv


def _port(srv):
    return srv.address[1]


# ---------------------------------------------------------------- Accept: two members connect

def test_two_members_connect(nodes, servers):
    ta = _transport(nodes, "a", "a", "b")
    tb = _transport(nodes, "b", "a", "b")
    rec = Recorder()
    srv = _serve(servers, ta, rec)

    with tb.connect(HOST, _port(srv), nodes["a"].device_id) as session:
        assert session.peer_device_id == nodes["a"].device_id
        assert session.tls_version == "TLSv1.3"
        session.send({"t": "ping", "n": 1, "text": "héllo"})
        reply = session.recv()
        assert reply == {"t": "echo", "peer": nodes["b"].device_id, "frame": {"t": "ping", "n": 1, "text": "héllo"}}
        session.send({"t": "bye"})
    assert rec.done.wait(5)
    assert rec.sessions == [nodes["b"].device_id]
    assert rec.errors == [] and rec.rejects == []


def test_contexts_are_tls13_mutual_and_ticketless(nodes):
    import ssl
    t = _transport(nodes, "a", "a", "b")
    server, client = t.contexts
    for ctx in (server, client):
        assert ctx.minimum_version == ssl.TLSVersion.TLSv1_3
        assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert client.check_hostname is False
    assert server.num_tickets == 0


# ---------------------------------------------------------------- Accept: non-member refused (both sides)

def test_non_member_refused_by_server(nodes, servers):
    ta = _transport(nodes, "a", "a", "b")
    # The outsider trusts a, so its own handshake would succeed; only a's check can stop it.
    to = _transport(nodes, "outsider", "a", "outsider")
    rec = Recorder()
    srv = _serve(servers, ta, rec)

    with pytest.raises(TransportError):
        with to.connect(HOST, _port(srv), nodes["a"].device_id) as session:
            session.send({"t": "ping"})
            session.recv()
    assert _wait(lambda: rec.rejects)
    assert rec.sessions == []
    assert _wait(lambda: srv.active_sessions == 0)


def test_non_member_refused_by_client(nodes, servers):
    # The outsider runs a server that would accept b, but b doesn't trust it.
    to = _transport(nodes, "outsider", "outsider", "b")
    tb = _transport(nodes, "b", "a", "b")
    rec = Recorder()
    srv = _serve(servers, to, rec)

    with pytest.raises(NotAMember):
        tb.connect(HOST, _port(srv), nodes["outsider"].device_id)
    assert rec.sessions == []


def test_client_refuses_a_member_it_did_not_mean_to_reach(nodes, servers):
    ta = _transport(nodes, "a", "a", "b", "c")
    tb = _transport(nodes, "b", "a", "b", "c")
    rec = Recorder()
    srv = _serve(servers, ta, rec)

    with pytest.raises(WrongPeer):
        tb.connect(HOST, _port(srv), nodes["c"].device_id)
    assert rec.sessions == []


def _cert_issued_by(issuer: Node, tmp_path: Path):
    """A leaf cert signed by a member's own key. The member certs are CA certs, so a chain to them
    verifies; only the fingerprint check can refuse it."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    issuer_key = serialization.load_pem_private_key(issuer.identity.key_pem, password=issuer.identity.passphrase)
    issuer_cert = x509.load_pem_x509_certificate(issuer.identity.cert_pem)
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "f" * 32)]))
        .issuer_name(issuer_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .sign(issuer_key, hashes.SHA256())
    )
    certfile = tmp_path / "forged_cert.pem"
    keyfile = tmp_path / "forged_key.pem"
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    return str(certfile), str(keyfile), None


def test_cert_issued_by_a_member_is_refused_by_server(nodes, servers, tmp_path):
    ta = _transport(nodes, "a", "a", "b")
    forged = SyncTransport(_cert_issued_by(nodes["b"], tmp_path), _members(nodes, "a", "b", own="b"))
    rec = Recorder()
    srv = _serve(servers, ta, rec)

    with pytest.raises(TransportError):
        with forged.connect(HOST, _port(srv), nodes["a"].device_id) as session:
            session.send({"t": "ping"})
            session.recv()
    assert _wait(lambda: rec.rejects)
    assert rec.sessions == []


def test_cert_issued_by_a_member_is_refused_by_client(nodes, servers, tmp_path):
    forged = SyncTransport(_cert_issued_by(nodes["a"], tmp_path), _members(nodes, "a", "b", own="a"))
    tb = _transport(nodes, "b", "a", "b")
    rec = Recorder()
    srv = _serve(servers, forged, rec)

    with pytest.raises(NotAMember):
        tb.connect(HOST, _port(srv), nodes["a"].device_id)
    assert rec.sessions == []


# ---------------------------------------------------------------- Accept: revoked member after rebuild

def test_revoked_member_refused_after_context_rebuild(nodes, servers):
    ta = _transport(nodes, "a", "a", "b")
    tb = _transport(nodes, "b", "a", "b")
    release = threading.Event()

    def hold(session):
        session.send({"t": "hello"})
        release.wait(10)
        session.recv()

    rec = Recorder(hold)
    srv = _serve(servers, ta, rec)

    open_session = tb.connect(HOST, _port(srv), nodes["a"].device_id)
    assert open_session.recv() == {"t": "hello"}
    old_server_ctx = ta.contexts[0]

    ta.update_members(_members(nodes, "a", own="a"))  # b revoked
    assert ta.contexts[0] is not old_server_ctx

    # The session that was already open with b is closed.
    with pytest.raises(TransportError):
        open_session.recv()
    open_session.close()
    release.set()

    # A new connection from b is refused.
    rec.rejects.clear()
    with pytest.raises(TransportError):
        with tb.connect(HOST, _port(srv), nodes["a"].device_id) as session:
            session.send({"t": "ping"})
            session.recv()
    assert _wait(lambda: rec.rejects)
    assert rec.sessions == [nodes["b"].device_id]  # only the first one
    assert _wait(lambda: srv.active_sessions == 0)


def test_client_refuses_revoked_server_after_rebuild(nodes, servers):
    ta = _transport(nodes, "a", "a", "b")
    tb = _transport(nodes, "b", "a", "b")
    srv = _serve(servers, ta, Recorder())
    tb.connect(HOST, _port(srv), nodes["a"].device_id).close()

    tb.update_members(_members(nodes, "b", own="b"))  # a revoked, as seen by b
    with pytest.raises(NotAMember):
        tb.connect(HOST, _port(srv), nodes["a"].device_id)


def test_member_set_from_db_skips_revoked_and_unverified(nodes, tmp_path):
    import sync_admin
    admin_key = sync_admin.generate_admin_key()
    pub = sync_admin.public_key_b64(admin_key)
    conn = sqlite3.connect(str(tmp_path / "master.db"))

    def member(node, letter, revoked_at=None):
        return sync_admin.sign_record({
            "type": "member", "device_id": nodes[node].device_id, "name": node, "cert_pem": nodes[node].cert_pem,
            "role": "member", "token_letter": letter, "added_at": "2026-09-24T00:00:00Z",
            "revoked_at": revoked_at, "rev": 1,
        }, admin_key)

    for rec in (member("a", "A"), member("b", "B"), member("c", "C", revoked_at="2026-09-24T01:00:00Z")):
        assert sync_admin.store_record(conn, rec, pub)
    conn.commit()

    ms = MemberSet.from_db(conn, pub, own_cert_pem=nodes["a"].cert_pem)
    assert ms.device_ids == frozenset({nodes["a"].device_id, nodes["b"].device_id})

    # A row that no longer verifies (edited in the DB) is not trusted.
    conn.execute("UPDATE _sync_members SET record_json = replace(record_json, '\"name\":\"b\"', '\"name\":\"x\"') "
                 "WHERE device_id = ?", (nodes["b"].device_id,))
    conn.commit()
    ms = MemberSet.from_db(conn, pub, own_cert_pem=nodes["a"].cert_pem)
    assert ms.device_ids == frozenset({nodes["a"].device_id})
    conn.close()


def test_member_set_rejects_cert_not_matching_device_id(nodes):
    with pytest.raises(ValueError):
        MemberSet([(nodes["b"].device_id, nodes["a"].cert_pem)], own_cert_pem=nodes["a"].cert_pem)


# ---------------------------------------------------------------- Accept: oversized frame

def test_oversized_frame_refused_by_receiver(nodes, servers):
    ta = _transport(nodes, "a", "a", "b")
    tb = _transport(nodes, "b", "a", "b")
    rec = Recorder(lambda s: s.recv())
    srv = _serve(servers, ta, rec)

    with tb.connect(HOST, _port(srv), nodes["a"].device_id) as session:
        session._sock.sendall(struct.pack(">I", sync_transport.MAX_FRAME_BYTES + 1))
        assert rec.done.wait(5)
    assert len(rec.errors) == 1 and isinstance(rec.errors[0], FrameTooLarge)
    assert _wait(lambda: srv.active_sessions == 0)


def test_oversized_frame_refused_by_sender(nodes, servers):
    ta = _transport(nodes, "a", "a", "b")
    tb = _transport(nodes, "b", "a", "b")
    rec = Recorder()
    srv = _serve(servers, ta, rec)

    with tb.connect(HOST, _port(srv), nodes["a"].device_id) as session:
        with pytest.raises(FrameTooLarge):
            session.send({"t": "big", "x": "a" * sync_transport.MAX_FRAME_BYTES})
        with pytest.raises(FrameTooLarge):
            session.send_chunk(bytearray(sync_transport.MAX_FRAME_BYTES + 1))
        session.send({"t": "ping"})  # nothing was sent: the session is still usable
        assert session.recv()["t"] == "echo"


def test_oversized_chunk_header_refused(nodes, servers):
    ta = _transport(nodes, "a", "a", "b")
    tb = _transport(nodes, "b", "a", "b")
    rec = Recorder(lambda s: s.recv())
    srv = _serve(servers, ta, rec)

    with tb.connect(HOST, _port(srv), nodes["a"].device_id) as session:
        session._send_json({"t": "chunk", "n": sync_transport.MAX_FRAME_BYTES + 1})
        assert rec.done.wait(5)
    assert len(rec.errors) == 1 and isinstance(rec.errors[0], FrameTooLarge)


@pytest.mark.parametrize("payload", [b"[1, 2]", b"not json", b"\xff\xfe", b'{"no_type": 1}', b"[" * 200000],
                         ids=["array", "not-json", "not-utf8", "no-type", "deeply-nested"])
def test_malformed_frame_is_a_protocol_error(nodes, servers, payload):
    ta = _transport(nodes, "a", "a", "b")
    tb = _transport(nodes, "b", "a", "b")
    rec = Recorder(lambda s: s.recv())
    srv = _serve(servers, ta, rec)

    with tb.connect(HOST, _port(srv), nodes["a"].device_id) as session:
        session._sock.sendall(struct.pack(">I", len(payload)) + payload)
        assert rec.done.wait(5)
    assert len(rec.errors) == 1 and isinstance(rec.errors[0], ProtocolError)


# ---------------------------------------------------------------- Accept: slow peer times out

def test_slow_peer_times_out(nodes, servers):
    ta = _transport(nodes, "a", "a", "b", frame_timeout=0.5)
    tb = _transport(nodes, "b", "a", "b")
    rec = Recorder(lambda s: s.recv())
    srv = _serve(servers, ta, rec)

    with tb.connect(HOST, _port(srv), nodes["a"].device_id):
        t0 = time.monotonic()
        assert rec.done.wait(5)
        assert time.monotonic() - t0 < 4
    assert len(rec.errors) == 1 and isinstance(rec.errors[0], FrameTimeout)
    assert _wait(lambda: srv.active_sessions == 0)


def test_dripping_peer_times_out_per_frame(nodes, servers):
    """A frame must arrive whole within the frame timeout, not just one byte per timeout."""
    ta = _transport(nodes, "a", "a", "b", frame_timeout=1.0)
    tb = _transport(nodes, "b", "a", "b")
    rec = Recorder(lambda s: s.recv())
    srv = _serve(servers, ta, rec)

    payload = b'{"t":"ping"}'
    data = struct.pack(">I", len(payload)) + payload
    with tb.connect(HOST, _port(srv), nodes["a"].device_id) as session:
        for i in range(len(data)):
            if rec.done.is_set():
                break
            session._sock.sendall(data[i:i + 1])
            time.sleep(0.3)
        assert rec.done.wait(5)
    assert len(rec.errors) == 1 and isinstance(rec.errors[0], FrameTimeout)


def test_session_deadline(nodes, servers):
    ta = _transport(nodes, "a", "a", "b", session_deadline=1.0)
    tb = _transport(nodes, "b", "a", "b")

    def loop(session):
        while True:
            session.recv()

    rec = Recorder(loop)
    srv = _serve(servers, ta, rec)
    with tb.connect(HOST, _port(srv), nodes["a"].device_id) as session:
        for _ in range(30):
            if rec.done.is_set():
                break
            try:
                session.send({"t": "tick"})
            except TransportError:
                break
            time.sleep(0.2)
        assert rec.done.wait(5)
    assert len(rec.errors) == 1 and isinstance(rec.errors[0], SessionDeadline)


def test_silent_tcp_client_does_not_block_others_and_frees_its_slot(nodes, servers):
    ta = _transport(nodes, "a", "a", "b", handshake_timeout=0.5)
    tb = _transport(nodes, "b", "a", "b")
    rec = Recorder()
    srv = _serve(servers, ta, rec)

    raw = socket.create_connection((HOST, _port(srv)))  # never starts TLS
    try:
        # The handshake runs in a worker thread, so a member still gets through at once.
        with tb.connect(HOST, _port(srv), nodes["a"].device_id) as session:
            session.send({"t": "ping"})
            assert session.recv()["t"] == "echo"
            session.send({"t": "bye"})
        assert _wait(lambda: rec.rejects)  # handshake timeout
        assert _wait(lambda: srv.active_sessions == 0 and srv.pending_handshakes == 0)
    finally:
        raw.close()


def test_idle_tcp_flood_does_not_take_session_slots(nodes, servers):
    """Review finding 1: connections that never start TLS must not use the 4 session slots
    (or block members), whatever their number."""
    ta = _transport(nodes, "a", "a", "b")  # default (10 s) handshake timeout: the flood stays pending
    tb = _transport(nodes, "b", "a", "b")
    release = threading.Event()

    def hold(session):
        session.send({"t": "hello"})
        release.wait(10)

    rec = Recorder(hold)
    srv = _serve(servers, ta, rec)

    flood = []
    held = []
    try:
        for _ in range(30):  # from another address than the member (127.0.0.1)
            raw = socket.socket()
            raw.bind(("127.0.0.2", 0))
            raw.connect((HOST, _port(srv)))
            flood.append(raw)
        assert _wait(lambda: sum("too many pending" in r for r in rec.rejects) >= 30 - sync_transport.MAX_PENDING_PER_ADDRESS)
        assert srv.pending_handshakes <= sync_transport.MAX_PENDING_PER_ADDRESS
        assert srv.active_sessions == 0

        for _ in range(sync_transport.MAX_SESSIONS):  # all 4 slots are still free for members
            s = tb.connect(HOST, _port(srv), nodes["a"].device_id)
            assert s.recv() == {"t": "hello"}
            held.append(s)
        assert srv.active_sessions == sync_transport.MAX_SESSIONS
        with tb.connect(HOST, _port(srv), nodes["a"].device_id) as extra:
            with pytest.raises(Busy):
                extra.recv()
    finally:
        release.set()
        for s in held:
            s.close()
        for raw in flood:
            raw.close()
    assert _wait(lambda: srv.active_sessions == 0 and srv.pending_handshakes == 0)


def test_recv_wait_allows_a_slow_start_but_not_a_slow_frame(nodes, servers):
    """Review finding 4: ``wait`` covers the time before a frame starts (e.g. a snapshot export)."""
    ta = _transport(nodes, "a", "a", "b")
    tb = _transport(nodes, "b", "a", "b", frame_timeout=0.5)

    def slow_start(session):
        session.recv()
        time.sleep(1.2)  # longer than the client's frame timeout
        session.send({"t": "manifest"})
        session.recv()

    rec = Recorder(slow_start)
    srv = _serve(servers, ta, rec)
    with tb.connect(HOST, _port(srv), nodes["a"].device_id) as session:
        session.send({"t": "snapshot"})
        assert session.recv(wait=5) == {"t": "manifest"}
        t0 = time.monotonic()
        with pytest.raises(FrameTimeout):
            session.recv(wait=0.3)
        assert time.monotonic() - t0 < 2
    with tb.connect(HOST, _port(srv), nodes["a"].device_id) as session:
        with pytest.raises(ValueError):
            session.recv(wait=0)


def test_connect_requires_expected_device_id(nodes):
    tb = _transport(nodes, "b", "a", "b")
    for bad in (None, ""):
        with pytest.raises(ValueError):
            tb.connect(HOST, 1, bad)


# ---------------------------------------------------------------- session limit

def test_fifth_session_gets_busy(nodes, servers):
    ta = _transport(nodes, "a", "a", "b")
    tb = _transport(nodes, "b", "a", "b")
    release = threading.Event()

    def hold(session):
        session.send({"t": "hello"})
        release.wait(10)

    rec = Recorder(hold)
    srv = _serve(servers, ta, rec)
    assert sync_transport.MAX_SESSIONS == 4

    held = []
    try:
        for _ in range(4):
            s = tb.connect(HOST, _port(srv), nodes["a"].device_id)
            assert s.recv() == {"t": "hello"}
            held.append(s)
        assert srv.active_sessions == 4
        with tb.connect(HOST, _port(srv), nodes["a"].device_id) as extra:
            with pytest.raises(Busy):
                extra.recv()
    finally:
        release.set()
        for s in held:
            s.close()
    assert _wait(lambda: srv.active_sessions == 0)
    with tb.connect(HOST, _port(srv), nodes["a"].device_id) as s:
        assert s.recv() == {"t": "hello"}


# ---------------------------------------------------------------- binary chunks / files

def test_file_streams_in_chunks(nodes, servers, tmp_path):
    src = tmp_path / "snapshot.bin"
    src.write_bytes(os.urandom(3 * sync_transport.CHUNK_SIZE + 12345))
    dest = tmp_path / "received.bin"
    result = {}

    def receive(session):
        head = session.recv()
        result["sha"] = session.recv_file(dest, head["size"])
        session.send({"t": "ok"})

    ta = _transport(nodes, "a", "a", "b")
    tb = _transport(nodes, "b", "a", "b")
    rec = Recorder(receive)
    srv = _serve(servers, ta, rec)

    with tb.connect(HOST, _port(srv), nodes["a"].device_id) as session:
        session.send({"t": "file", "size": src.stat().st_size})
        size, sha = session.send_file(src)
        assert session.recv() == {"t": "ok"}
    assert rec.errors == []
    assert size == src.stat().st_size
    assert sha == result["sha"] == hashlib.sha256(src.read_bytes()).hexdigest()
    assert dest.read_bytes() == src.read_bytes()


def test_sink_failure_closes_session(nodes, servers):
    """Review finding 3: if writing the chunk fails, the stream is out of step, so the session closes."""
    closed_after = []

    class DiskFull:
        @staticmethod
        def write(data):
            raise OSError("disk full")

    def receive(session):
        assert session.recv()["t"] == "chunk"
        try:
            session.read_chunk(DiskFull)
        finally:
            closed_after.append(session.closed)

    ta = _transport(nodes, "a", "a", "b")
    tb = _transport(nodes, "b", "a", "b")
    rec = Recorder(receive)
    srv = _serve(servers, ta, rec)
    with tb.connect(HOST, _port(srv), nodes["a"].device_id) as session:
        try:
            session.send_chunk(b"x" * 300000)
        except TransportError:
            pass  # the server closes mid-chunk, as it should
        assert rec.done.wait(5)
    assert len(rec.errors) == 1 and type(rec.errors[0]) is OSError
    assert closed_after == [True]


def test_recv_file_refuses_empty_chunks(nodes, servers, tmp_path):
    """Review finding 5: a member can't stall ``recv_file`` with zero-length chunks."""
    dest = tmp_path / "received.bin"
    ta = _transport(nodes, "a", "a", "b")
    tb = _transport(nodes, "b", "a", "b")
    rec = Recorder(lambda s: s.recv_file(dest, 10))
    srv = _serve(servers, ta, rec)

    with tb.connect(HOST, _port(srv), nodes["a"].device_id) as session:
        session.send_chunk(b"")
        assert rec.done.wait(5)
    assert len(rec.errors) == 1 and isinstance(rec.errors[0], ProtocolError)
    assert not dest.exists()


def test_recv_file_refuses_more_bytes_than_announced(nodes, servers, tmp_path):
    dest = tmp_path / "received.bin"
    ta = _transport(nodes, "a", "a", "b")
    tb = _transport(nodes, "b", "a", "b")
    rec = Recorder(lambda s: s.recv_file(dest, 10))
    srv = _serve(servers, ta, rec)

    with tb.connect(HOST, _port(srv), nodes["a"].device_id) as session:
        session.send_chunk(b"x" * 11)
        assert rec.done.wait(5)
    assert len(rec.errors) == 1 and isinstance(rec.errors[0], ProtocolError)


# ---------------------------------------------------------------- module rules

def test_no_pyside6_or_heavy_top_level_imports():
    tree = ast.parse(Path(sync_transport.__file__).read_text(encoding="utf-8"))
    top = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top.add(node.module.split(".")[0])
    all_names = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    all_names |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not any(name.startswith("PySide6") for name in all_names)
    assert "cryptography" not in top and "sync_admin" not in top
