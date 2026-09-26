"""Tests for Sera Sync v3 P3-7a: peer digest exchange for the shadow convergence check
(sync_engine's digest_request/digest_reply frames + sync_shadow.replica_snapshot).

Accept (blueprint §5 P3-7a):
  - harness: two nodes in shadow mode converge -> a digest exchange logs OK
    (test_digest_exchange_converges_and_logs_ok)
  - a row changed directly in one replica (bypassing sync) -> mismatch logged
    (test_digest_exchange_logs_mismatch_when_a_replica_is_edited_directly)
  - a local edit between the request and the reply -> "skipped", not a mismatch
    (test_digest_exchange_skipped_when_a_local_edit_races_the_reply)
  - a peer without digest: true in HELLO -> no digest frames sent, the session still completes
    (test_no_digest_frames_sent_when_peer_does_not_advertise_digest)

Also covers the P3-7a review's non-blocking findings, all fixed here: a malformed digest_reply
is now logged instead of silently ignored (#2); a remote-applied merge no longer stamps
"last local seal" (#3); at most one digest_request is answered per session, extras get an
immediate busy (#4); the wording "vectors moved" was replaced with "vectors differ", since a
non-matching comparison can equally mean the session simply didn't leave both PCs caught up with
each other this round, not only a genuine race (#1); and the "no digest frames" test's spy now
sits on the responder, which is the side that would actually be asked (#5).

Nodes are real harness nodes on 127.0.0.1 (P2-4 pairing, P2-3 mutual TLS). tmp_path only
(§0 rule 2); invented data only (§0 rule 12).
"""

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")

import sync_engine  # noqa: E402
import sync_shadow  # noqa: E402
from tests.sync_harness import SyncHarness  # noqa: E402


@pytest.fixture
def cluster(tmp_path):
    made = []

    def make(n=2):
        h = SyncHarness(num_nodes=n, base_dir=tmp_path / f"h{len(made)}")
        made.append(h)
        return h
    yield make
    for h in made:
        h.close()


def _wire_shadow(node):
    """Same wiring as tests/test_sync_shadow.py's helper: turns a live-mode harness node into a
    shadow-mode node with a working ``shadow_apply`` and a seal listener that mirrors this
    device's own changes into the replica, without touching tests/sync_harness.py (§0 rule 4)."""
    sync_shadow.enable_shadow_mode(node.db, node.app_dir)
    node.engine.shadow_apply = sync_shadow.make_shadow_apply(node.db, node.app_dir)

    def _listener(results, _node=node):
        sync_shadow.mirror_own_changes_to_replica(_node.db, _node.app_dir)
        _node.engine.notify_local_change()
    node.db.set_seal_listener(_listener)


def _insert_client(conn, token: str, notes: str = "Note", ts: str = "2026-09-26T10:00:00Z"):
    conn.execute(
        "INSERT INTO clients (notes, is_archived, created_at, updated_at, client_id_token) "
        "VALUES (?, 0, ?, ?, ?)", (notes, ts, ts, token))


def _log_text(node) -> str:
    log_path = Path(node.app_dir) / "logs" / sync_shadow.SHADOW_LOG_NAME
    return log_path.read_text(encoding="utf-8") if log_path.exists() else ""


# ---------------------------------------------------------------- accept


def test_digest_exchange_converges_and_logs_ok(cluster):
    h = cluster(2)
    admin, joiner = h.nodes
    _wire_shadow(admin)
    _wire_shadow(joiner)

    admin.write(lambda conn: _insert_client(conn, "A-1"))
    result = h.run_sync_round()[0]        # round 0: admin dials joiner (even round)
    assert result.ok

    assert "peer_digest_check OK" in _log_text(admin)
    assert sync_shadow.digest_of_replica(admin.app_dir, admin.hex_key) == \
        sync_shadow.digest_of_replica(joiner.app_dir, joiner.hex_key)


def test_digest_exchange_logs_mismatch_when_a_replica_is_edited_directly(cluster):
    h = cluster(2)
    admin, joiner = h.nodes
    _wire_shadow(admin)
    _wire_shadow(joiner)

    admin.write(lambda conn: _insert_client(conn, "A-1"))
    assert h.run_sync_round()[0].ok        # round 0: converges, logs OK on admin
    assert "peer_digest_check OK" in _log_text(admin)

    # A row changed directly in the joiner's replica, bypassing sync entirely: the vectors never
    # move (nothing was captured/exchanged), but the row data now differs.
    import sqlcipher3.dbapi2 as sqlite3
    replica_master, _ = sync_shadow.replica_paths(joiner.app_dir)
    conn = sqlite3.connect(str(replica_master))
    try:
        conn.execute(f"PRAGMA key = \"x'{joiner.hex_key}'\";")
        conn.execute("UPDATE clients SET notes = 'tampered' WHERE client_id_token = 'A-1'")
        conn.commit()
    finally:
        conn.close()

    result = h.run_sync_round()[0]         # round 1: joiner dials admin (odd round)
    assert result.ok
    assert "peer_digest_check MISMATCH" in _log_text(joiner)


def test_digest_exchange_skipped_when_a_local_edit_races_the_reply(cluster):
    h = cluster(2)
    admin, joiner = h.nodes
    _wire_shadow(admin)
    _wire_shadow(joiner)

    real = admin.engine._replica_snapshot_with_timeout
    injected = []

    def racing_snapshot():
        # Simulates a local edit landing on the requester between it sending digest_request and
        # it reading its own replica snapshot for comparison (the requester reads its own
        # snapshot only after the reply arrives -- see sync_engine._run_digest_exchange).
        if not injected:
            injected.append(True)
            admin.write(lambda conn: _insert_client(conn, "RACE-1"))
        return real()

    admin.engine._replica_snapshot_with_timeout = racing_snapshot
    result = h.run_sync_round()[0]          # round 0: admin dials joiner, admin is the requester
    assert result.ok
    log = _log_text(admin)
    assert "peer_digest_check skipped" in log
    assert "vectors differ" in log
    assert "MISMATCH" not in log


def test_no_digest_frames_sent_when_peer_does_not_advertise_digest(cluster):
    h = cluster(2)
    admin, joiner = h.nodes
    _wire_shadow(admin)
    _wire_shadow(joiner)

    real_hello = joiner.engine._hello

    def hello_without_digest():
        hello = real_hello()
        hello.pop("digest", None)
        return hello
    joiner.engine._hello = hello_without_digest

    # The spy belongs on the responder (joiner): admin is the round's initiator/dialer, so it is
    # admin that would decide whether to send digest_request, and joiner that would be asked to
    # answer one -- spying on admin's own _answer_digest_request would never fire regardless of
    # this test's outcome (review finding #5, P3-7a: the original spy sat on the wrong side).
    seen_digest_frames = []
    real_answer = joiner.engine._answer_digest_request

    def spy_answer(session):
        seen_digest_frames.append(True)
        return real_answer(session)
    joiner.engine._answer_digest_request = spy_answer

    admin.write(lambda conn: _insert_client(conn, "A-1"))
    result = h.run_sync_round()[0]          # round 0: admin dials joiner
    assert result.ok
    assert not seen_digest_frames
    assert "peer_digest_check" not in _log_text(admin)
    assert "peer_digest_check" not in _log_text(joiner)


# ---------------------------------------------------------------- review fixes


def test_digest_exchange_logs_skipped_for_a_malformed_reply(cluster):
    """P3-7a review finding #2: a malformed digest_reply used to be silently ignored. Now it logs
    "skipped: malformed reply" instead of nothing, without aborting the session."""
    h = cluster(2)
    admin, joiner = h.nodes
    _wire_shadow(admin)
    _wire_shadow(joiner)

    def bad_answer(session):
        session.send({"t": "digest_reply", "vectors": {}, "digest": "not-a-real-sha256-digest"})
    joiner.engine._answer_digest_request = bad_answer

    admin.write(lambda conn: _insert_client(conn, "A-1"))
    result = h.run_sync_round()[0]         # round 0: admin dials joiner, admin is the requester
    assert result.ok
    log = _log_text(admin)
    assert "peer_digest_check skipped" in log
    assert "malformed reply" in log


def test_digest_request_answered_at_most_once_per_session(cluster):
    """P3-7a review finding #4: a peer sending more than one digest_request in the same session
    used to get a full answer (a fresh worker thread) for each one, unbounded. Now only the first
    is actually computed; any extra gets an immediate busy reply."""
    h = cluster(1)
    node = h.nodes[0]
    _wire_shadow(node)
    calls = []
    real_answer = node.engine._answer_digest_request

    def spy(session):
        calls.append(True)
        return real_answer(session)
    node.engine._answer_digest_request = spy

    class _FakeSession:
        def __init__(self, incoming):
            self._incoming = list(incoming)
            self.sent = []

        def recv(self, wait=None):
            return self._incoming.pop(0)

        def send(self, obj):
            self.sent.append(obj)

    fake = _FakeSession([
        {"t": "digest_request"},
        {"t": "digest_request"},
        {"t": "bye", "reason": "done", "vectors": {}},
    ])
    node.engine._recv_final_bye(fake)

    assert len(calls) == 1                       # only the first was actually computed
    assert fake.sent[0]["t"] == "digest_reply" and "busy" not in fake.sent[0]
    assert fake.sent[1] == {"t": "digest_reply", "busy": True}   # the extra was capped, not computed


def test_finish_with_emitted_merges_wakes_poke_without_stamping_last_local_change(cluster):
    """P3-7a review finding #3: applying a REMOTE batch that produces a merge decision
    (result.emitted) used to stamp _last_local_change_at, understating "seconds since last local
    seal" in the digest-check log. Only this PC's own edits (via notify_local_change, called from
    the DB's seal listener) should stamp it; a remote-driven merge only wakes the poke sender."""
    h = cluster(1)
    node = h.nodes[0]
    node.engine._last_local_change_at = None

    result = sync_engine.SessionResult(node.device_id, initiator=True)
    result.emitted = 1
    node.engine._finish(result)
    assert node.engine._last_local_change_at is None

    node.engine.notify_local_change()
    assert node.engine._last_local_change_at is not None


# ---------------------------------------------------------------- sync_shadow.replica_snapshot


def test_replica_snapshot_reads_vectors_and_digest_together(cluster):
    h = cluster(1)
    node = h.nodes[0]
    _wire_shadow(node)
    node.write(lambda conn: _insert_client(conn, "A-1"))

    vectors, digest = sync_shadow.replica_snapshot(node.app_dir, node.hex_key)
    assert isinstance(vectors, dict)
    assert digest == sync_shadow.digest_of_replica(node.app_dir, node.hex_key)


def test_replica_snapshot_raises_without_a_replica(tmp_path):
    with pytest.raises(RuntimeError):
        sync_shadow.replica_snapshot(str(tmp_path), "00" * 32)


def test_log_peer_digest_check_writes_expected_line(tmp_path):
    sync_shadow.log_peer_digest_check(str(tmp_path), "PC-2", "OK", "5s since last local seal")
    text = (Path(tmp_path) / "logs" / sync_shadow.SHADOW_LOG_NAME).read_text(encoding="utf-8")
    assert "peer_digest_check OK (peer PC-2): 5s since last local seal" in text
