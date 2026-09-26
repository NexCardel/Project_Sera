"""Tests for Sera Sync v3 P3-7: shadow mode (sync_shadow).

Accept (blueprint §5 P3-7): a harness test where a deliberately missed write (trigger dropped)
makes the capture check fail (``test_capture_check_fails_when_a_trigger_is_dropped``).

Also covers: ``enable_shadow_mode`` (baseline + replica snapshot, mode switch), own changes
mirrored into the replica, remote changes applied to the replica and never the live DB,
digest agreement between a fully-mirrored replica and its live DB, and ``convergence_check``.

Nodes are real harness nodes on 127.0.0.1 (P2-4 pairing, P2-3 mutual TLS). tmp_path only
(§0 rule 2); invented data only (§0 rule 12).
"""

import ast
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")

import sync_capture  # noqa: E402
import sync_shadow  # noqa: E402
from tests.sync_harness import SyncHarness  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- helpers

def _insert_client(conn, token: str, notes: str = "Note", ts: str = "2026-09-24T10:00:00Z"):
    cur = conn.execute(
        "INSERT INTO clients (notes, is_archived, created_at, updated_at, client_id_token) "
        "VALUES (?, 0, ?, ?, ?)",
        (notes, ts, ts, token),
    )
    return cur.lastrowid


@pytest.fixture
def cluster(tmp_path):
    made = []

    def make(n=1):
        h = SyncHarness(num_nodes=n, base_dir=tmp_path / f"h{len(made)}")
        made.append(h)
        return h
    yield make


def _wire_shadow(node, seal_mirrors_replica=True):
    """Turns *node* (a live-mode harness node) into a shadow-mode node with a working
    ``shadow_apply`` and (optionally) a seal listener that mirrors its own changes to the
    replica, mirroring the P3-7 main.py wiring without touching tests/sync_harness.py (a P3-0
    file; changing it needs the owner's OK per §0 rule 4)."""
    sync_shadow.enable_shadow_mode(node.db, node.app_dir)
    node.engine.shadow_apply = sync_shadow.make_shadow_apply(node.db, node.app_dir)
    if seal_mirrors_replica:
        def _listener(results, _node=node):
            sync_shadow.mirror_own_changes_to_replica(_node.db, _node.app_dir)
            _node.engine.notify_local_change()
        node.db.set_seal_listener(_listener)


# ---------------------------------------------------------------- enable_shadow_mode

def test_enable_shadow_mode_creates_baseline_and_replica(cluster):
    h = cluster(1)
    node = h.nodes[0]

    assert node.db.get_sync_mode() != "shadow"
    sync_shadow.enable_shadow_mode(node.db, node.app_dir)

    replica_master, replica_raw = sync_shadow.replica_paths(node.app_dir)
    baseline_master, baseline_raw = sync_shadow.baseline_paths(node.app_dir)
    assert replica_master.exists()
    assert baseline_master.exists()
    assert replica_raw.exists()
    assert baseline_raw.exists()
    assert node.db.get_sync_mode() == "shadow"
    assert replica_master.read_bytes() != b""

    # The baseline is a frozen, separate copy: touching the replica must not touch it.
    baseline_bytes_before = baseline_master.read_bytes()

    def do_write(conn):
        _insert_client(conn, "A-1")
    node.write(do_write)
    sync_shadow.mirror_own_changes_to_replica(node.db, node.app_dir)
    assert baseline_master.read_bytes() == baseline_bytes_before


def test_enable_shadow_mode_refuses_while_an_old_replica_exists(cluster):
    """P3-7b (v1.8, owner-approved rewrite 2026-09-26 of the P3-7 test that expected this to
    succeed): mode off and then shadow on again must be refused while an old replica exists,
    and nothing may be touched. The §0 rule 3 part of the old test (old shadow files are
    renamed to *.bak-<ts>, never deleted) now belongs to the admin-only "Reset shadow mode":
    tests/test_sync_shadow_start.py::test_reset_renames_shadow_files_and_resets_the_week."""
    h = cluster(1)
    node = h.nodes[0]
    sync_shadow.enable_shadow_mode(node.db, node.app_dir)
    replica_master, _ = sync_shadow.replica_paths(node.app_dir)
    first_bytes = replica_master.read_bytes()

    node.db.set_sync_mode("off")
    with pytest.raises(sync_shadow.ShadowStartRefused):
        sync_shadow.enable_shadow_mode(node.db, node.app_dir)

    shadow_dir = Path(node.app_dir) / sync_shadow.SHADOW_DIRNAME
    assert not list(shadow_dir.glob("replica_master.db.bak-*"))
    assert replica_master.read_bytes() == first_bytes
    assert node.db.get_sync_mode() == "off"


# ---------------------------------------------------------------- own changes -> replica

def test_own_changes_are_mirrored_to_the_replica(cluster):
    h = cluster(1)
    node = h.nodes[0]
    _wire_shadow(node)

    def do_write(conn):
        _insert_client(conn, "A-1")
    node.write(do_write)

    # The seal listener mirrored it automatically; the replica's digest must now equal live's.
    live_digest = sync_shadow.digest_of_live(node.db)
    replica_digest = sync_shadow.digest_of_replica(node.app_dir, node.hex_key)
    assert live_digest == replica_digest

    import sqlcipher3.dbapi2 as sqlite3
    replica_master, _ = sync_shadow.replica_paths(node.app_dir)
    conn = sqlite3.connect(str(replica_master))
    try:
        conn.execute(f"PRAGMA key = \"x'{node.hex_key}'\";")
        row = conn.execute("SELECT client_id_token FROM clients WHERE client_id_token = 'A-1'").fetchone()
        assert row is not None
    finally:
        conn.close()


def test_replica_mirroring_is_a_noop_outside_shadow_mode(cluster):
    h = cluster(1)
    node = h.nodes[0]
    # mode is "live" (set by the harness); mirroring must not create a replica or raise.
    sync_shadow.mirror_own_changes_to_replica(node.db, node.app_dir)
    replica_master, _ = sync_shadow.replica_paths(node.app_dir)
    assert not replica_master.exists()


# ---------------------------------------------------------------- remote changes -> replica

def test_shadow_apply_writes_to_the_replica_never_the_live_db(cluster):
    h = cluster(2)
    admin, joiner = h.nodes[0], h.nodes[1]
    _wire_shadow(joiner)

    def do_write(conn):
        _insert_client(conn, "A-1")
    admin.write(do_write)

    h.run_until_quiet(timeout=10.0)

    # The live DB on the joiner never saw it (mode shadow routes remote changes to the replica).
    with joiner.open_db() as conn:
        row = conn.execute("SELECT 1 FROM clients WHERE client_id_token = 'A-1'").fetchone()
    assert row is None

    # The replica did.
    import sqlcipher3.dbapi2 as sqlite3
    replica_master, _ = sync_shadow.replica_paths(joiner.app_dir)
    conn = sqlite3.connect(str(replica_master))
    try:
        conn.execute(f"PRAGMA key = \"x'{joiner.hex_key}'\";")
        row = conn.execute("SELECT 1 FROM clients WHERE client_id_token = 'A-1'").fetchone()
        assert row is not None
    finally:
        conn.close()


def test_capture_check_still_passes_after_receiving_a_remote_change(cluster):
    """P3-7 review (2026-09-25), blocking finding 1: capture_check used to replay every change
    in _sync_changes, including forwarded copies of remote changes that never reach the live
    DB's replicated tables in mode shadow (they go to the replica only). That made the check
    fail on any PC that had ever received anything. It must only replay this device's own-origin
    changes."""
    h = cluster(2)
    admin, joiner = h.nodes[0], h.nodes[1]
    _wire_shadow(joiner)

    # The joiner's own edit: capture_check must pass on its own changes alone.
    def do_own_write(conn):
        _insert_client(conn, "B-1")
    joiner.write(do_own_write)
    assert sync_shadow.capture_check(joiner.db, joiner.app_dir).ok is True

    # A remote edit, received and mirrored into the joiner's replica (not its live DB).
    def do_remote_write(conn):
        _insert_client(conn, "A-1")
    admin.write(do_remote_write)
    h.run_until_quiet(timeout=10.0)

    result = sync_shadow.capture_check(joiner.db, joiner.app_dir)
    assert result.ok is True, result.detail


def test_enable_shadow_mode_refuses_when_already_on(cluster):
    """P3-7 review (2026-09-25), blocking finding 2: re-running enable_shadow_mode while shadow
    mode is already on rebuilds the replica from the live DBs, which never hold remote-origin
    data -- but the live DB's _sync_vector already marks that data received, so no peer would
    ever resend it. The new replica would silently and permanently diverge. Must refuse."""
    h = cluster(2)
    admin, joiner = h.nodes[0], h.nodes[1]
    _wire_shadow(joiner)

    def do_remote_write(conn):
        _insert_client(conn, "A-1")
    admin.write(do_remote_write)
    h.run_until_quiet(timeout=10.0)

    replica_master, _ = sync_shadow.replica_paths(joiner.app_dir)
    bytes_before = replica_master.read_bytes()

    with pytest.raises(RuntimeError):
        sync_shadow.enable_shadow_mode(joiner.db, joiner.app_dir)

    # Refused before touching anything.
    assert joiner.db.get_sync_mode() == "shadow"
    assert replica_master.read_bytes() == bytes_before


def test_shadow_hello_reports_accepts_true_once_a_replica_exists(cluster):
    h = cluster(2)
    admin, joiner = h.nodes[0], h.nodes[1]
    sync_shadow.enable_shadow_mode(joiner.db, joiner.app_dir)
    assert joiner.engine._accepts() is False  # shadow mode, but shadow_apply is still None
    joiner.engine.shadow_apply = sync_shadow.make_shadow_apply(joiner.db, joiner.app_dir)
    assert joiner.engine._accepts() is True


# ---------------------------------------------------------------- digest

def test_digest_from_conns_agrees_with_itself_across_a_fully_mirrored_replica(cluster):
    h = cluster(1)
    node = h.nodes[0]
    _wire_shadow(node)

    def do_write(conn):
        cid = _insert_client(conn, "A-1")
        conn.execute(
            "INSERT INTO mcl_columns (label, sort_order) VALUES ('PAN', 1)")
    node.write(do_write)

    assert sync_shadow.digest_of_live(node.db) == sync_shadow.digest_of_replica(node.app_dir, node.hex_key)


def test_digest_changes_when_data_changes(cluster):
    h = cluster(1)
    node = h.nodes[0]
    d0 = sync_shadow.digest_of_live(node.db)

    def do_write(conn):
        _insert_client(conn, "A-1")
    node.write(do_write)

    d1 = sync_shadow.digest_of_live(node.db)
    assert d0 != d1


# ---------------------------------------------------------------- capture_check

def test_capture_check_ok_with_no_baseline_reports_not_ok(cluster):
    h = cluster(1)
    node = h.nodes[0]
    result = sync_shadow.capture_check(node.db, node.app_dir)
    assert result.ok is False
    assert "baseline" in result.detail


def test_capture_check_passes_after_normal_writes(cluster):
    h = cluster(1)
    node = h.nodes[0]
    sync_shadow.enable_shadow_mode(node.db, node.app_dir)

    def do_write(conn):
        _insert_client(conn, "A-1")
        _insert_client(conn, "A-2", notes="Second")
    node.write(do_write)

    result = sync_shadow.capture_check(node.db, node.app_dir)
    assert result.ok is True
    assert result.live_digest == result.replay_digest

    log_path = Path(node.app_dir) / "logs" / sync_shadow.SHADOW_LOG_NAME
    assert log_path.exists()
    assert "capture_check OK" in log_path.read_text(encoding="utf-8")


def test_capture_check_fails_when_a_trigger_is_dropped(cluster):
    """Blueprint §5 P3-7 Accept: a deliberately missed write (trigger dropped) makes the
    capture check fail."""
    h = cluster(1)
    node = h.nodes[0]
    sync_shadow.enable_shadow_mode(node.db, node.app_dir)

    # A normal, captured write first, so the baseline-vs-replay path is exercised for real.
    def do_write(conn):
        _insert_client(conn, "A-1")
    node.write(do_write)
    assert sync_shadow.capture_check(node.db, node.app_dir).ok is True

    # Drop every capture trigger for "clients" -- the next insert bypasses capture entirely.
    # (Dropping only the AFTER INSERT trigger still gets captured: the P3-2 gid back-fill
    # trigger's UPDATE on the same row fires the AFTER UPDATE capture trigger.)
    def drop_triggers(conn):
        for name in sync_capture.trigger_names("clients"):
            conn.execute(f"DROP TRIGGER IF EXISTS {name}")
    node.write(drop_triggers)

    def do_uncaptured_write(conn):
        _insert_client(conn, "A-2", notes="Never captured")
    node.write(do_uncaptured_write)

    result = sync_shadow.capture_check(node.db, node.app_dir)
    assert result.ok is False
    assert result.live_digest != result.replay_digest

    log_path = Path(node.app_dir) / "logs" / sync_shadow.SHADOW_LOG_NAME
    assert "capture_check MISMATCH" in log_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------- convergence_check

def test_convergence_check_ok_when_digests_match():
    result = sync_shadow.convergence_check("dev-a", "deadbeef", {"dev-b": "deadbeef", "dev-c": "deadbeef"})
    assert result.ok is True
    assert result.detail == ""


def test_convergence_check_fails_when_digests_differ():
    result = sync_shadow.convergence_check("dev-a", "deadbeef", {"dev-b": "not-the-same"})
    assert result.ok is False
    assert "distinct replica digests" in result.detail


def test_run_shadow_checks_runs_convergence_when_peer_digests_given(cluster):
    h = cluster(1)
    node = h.nodes[0]
    sync_shadow.enable_shadow_mode(node.db, node.app_dir)
    own_digest = sync_shadow.digest_of_replica(node.app_dir, node.hex_key)

    out = sync_shadow.run_shadow_checks(node.db, node.app_dir, own_device_id=node.device_id,
                                         peer_digests={"other-device": own_digest})
    assert out["capture"].ok is True
    assert out["convergence"].ok is True


def test_run_shadow_checks_logs_parked_count_and_oldest_age(cluster):
    h = cluster(1)
    node = h.nodes[0]
    sync_shadow.enable_shadow_mode(node.db, node.app_dir)
    replica_master, _ = sync_shadow.replica_paths(node.app_dir)

    # Insert a parked change directly into replica_master.db
    conn = sync_capture._open(str(replica_master), node.hex_key, 5.0)
    try:
        conn.execute("INSERT INTO _sync_parked(change_json, reason, first_at, tries) "
                     "VALUES ('{}', 'parent_missing', '2026-09-25T00:00:00Z', 0)")
        conn.commit()
    finally:
        conn.close()

    sync_shadow.run_shadow_checks(node.db, node.app_dir)

    log_path = Path(node.app_dir) / "logs" / sync_shadow.SHADOW_LOG_NAME
    content = log_path.read_text(encoding="utf-8")
    assert "parked_changes count=1" in content


# ---------------------------------------------------------------- seal timing

def test_seal_timing_logger_aggregates_and_rate_limits(tmp_path):
    logger = sync_shadow.SealTimingLogger()
    app_dir = tmp_path

    # Record 3 measurements within the same minute
    logger.record(10.0, app_dir=app_dir, now_fn=lambda: 1000.0)
    logger.record(20.0, app_dir=app_dir, now_fn=lambda: 1010.0)
    logger.record(30.0, app_dir=app_dir, now_fn=lambda: 1020.0)

    log_path = app_dir / "logs" / sync_shadow.SHADOW_LOG_NAME
    # Less than 60s elapsed -> no log line written yet
    assert not log_path.exists()

    # 4th measurement occurs at 1065.0 (> 60s since 1000.0)
    logger.record(40.0, app_dir=app_dir, now_fn=lambda: 1065.0)
    assert log_path.exists()
    lines = [ln for ln in log_path.read_text(encoding="utf-8").splitlines() if "seal_timing" in ln]
    assert len(lines) == 1
    # 4 measurements: 10, 20, 30, 40 -> mean 25.0ms, max 40.0ms, count 4
    assert "seal_timing count=4 mean=25.0ms max=40.0ms" in lines[0]


def test_seal_timing_log_contains_no_row_contents(tmp_path):
    logger = sync_shadow.SealTimingLogger()
    app_dir = tmp_path
    logger.record(15.5, app_dir=app_dir, now_fn=lambda: 1000.0)
    logger.flush(app_dir=app_dir)

    log_path = app_dir / "logs" / sync_shadow.SHADOW_LOG_NAME
    content = log_path.read_text(encoding="utf-8")
    assert "seal_timing count=1 mean=15.5ms max=15.5ms" in content
    # Ensure no row or sensitive fields exist
    for forbidden in ("pan", "name", "token", "clients", "SELECT", "INSERT"):
        assert forbidden not in content


# ---------------------------------------------------------------- hygiene

def test_no_pyside6_in_sync_shadow():
    src = (ROOT / "sync_shadow.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "PySide6" not in alias.name
        elif isinstance(node, ast.ImportFrom):
            assert not (node.module and "PySide6" in node.module)

