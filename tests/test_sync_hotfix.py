import os
import time
import pytest
from unittest.mock import MagicMock
from main import SeraApp

# FakeSyncService, DummyApp and test_write_does_not_push_database tested
# SeraApp._broadcast_live_update_to_peers, the debounced telemetry broadcast (tracker dumps /
# audit logs to LAN peers) that P4-1 removed along with the rest of the legacy v2 protocol
# (push_to, request_pull_from, broadcast_tracker_dumps, broadcast_audit_logs) once Sera Sync
# v3 went live everywhere. Deleted rather than weakened (blueprint §0 rule 4).


def test_sync_metrics_counts_clients(tmp_path):
    import security
    from database import SeraDatabase

    db_path = str(tmp_path / "test_master.db")
    salt_path = str(tmp_path / "test.salt")
    security.generate_and_save_salt(salt_path)
    salt = security.load_salt(salt_path)
    hex_key = security.derive_key_hex("testpass123", salt)

    db = SeraDatabase(db_path, hex_key, defer_startup_maintenance=True)
    pan_col = next((c for c in db.get_mcl_columns() if c["label"].strip().upper() == "PAN"), None)
    pan_id = pan_col["id"] if pan_col else 5

    c1 = db.add_client({pan_id: "AAAAA0001A"}, "active 1", [])
    c2 = db.add_client({pan_id: "AAAAA0002A"}, "active 2", [])
    c3 = db.add_client({pan_id: "AAAAA0003A"}, "archived 1", [])
    db.archive_client(c3)

    # Invalidate cache if needed
    if hasattr(db, "_sync_metrics_cache_ts"):
        db._sync_metrics_cache_ts = 0.0

    metrics = db.get_sync_metrics()
    assert metrics["client_count"] == 2
    assert metrics["archived_count"] == 1


def test_snapshot_includes_wal_changes(tmp_path):
    import os
    import security
    import sqlcipher3.dbapi2 as sqlite3
    from database import SeraDatabase, make_snapshot

    db_path = str(tmp_path / "test_master.db")
    salt_path = str(tmp_path / "test.salt")
    security.generate_and_save_salt(salt_path)
    salt = security.load_salt(salt_path)
    hex_key = security.derive_key_hex("testpass123", salt)

    db = SeraDatabase(db_path, hex_key, defer_startup_maintenance=True)

    # Open a connection to write uncheckpointed rows in WAL mode
    conn_writer = sqlite3.connect(db_path)
    conn_writer.execute(f"PRAGMA key = \"x'{hex_key}'\";")
    conn_writer.execute("PRAGMA journal_mode = WAL;")
    conn_writer.execute("PRAGMA user_version = 1337;")
    conn_writer.execute("CREATE TABLE wal_test (id INT, note TEXT);")
    conn_writer.execute("INSERT INTO wal_test VALUES (1, 'wal row 1');")
    conn_writer.execute("INSERT INTO wal_test VALUES (2, 'wal row 2');")
    conn_writer.commit()

    # The WAL file exists while uncheckpointed
    wal_file = db_path + "-wal"
    assert os.path.exists(wal_file)
    assert os.path.getsize(wal_file) > 0

    # Destination snapshot path lives under incoming/out/
    snap_dir = tmp_path / "incoming" / "out"
    snap_dir.mkdir(parents=True, exist_ok=True)
    dest_path = str(snap_dir / "snapshot.db")

    make_snapshot(db, dest_path)
    conn_writer.close()

    assert os.path.exists(dest_path)
    assert os.path.getsize(dest_path) > 0

    # The snapshot opened with the same key contains the uncheckpointed WAL rows
    snap_conn = sqlite3.connect(dest_path)
    try:
        snap_conn.execute(f"PRAGMA key = \"x'{hex_key}'\";")
        # PRAGMA cipher_integrity_check returns no rows (empty list on success)
        integrity_rows = snap_conn.execute("PRAGMA cipher_integrity_check;").fetchall()
        assert integrity_rows == []

        # Check user_version was copied
        uv = snap_conn.execute("PRAGMA user_version;").fetchone()[0]
        assert uv == 1337

        # Check uncheckpointed WAL rows exist in snapshot
        rows = snap_conn.execute("SELECT note FROM wal_test ORDER BY id ASC;").fetchall()
        notes = [r[0] for r in rows]
        assert notes == ["wal row 1", "wal row 2"]
    finally:
        snap_conn.close()


# test_push_to_streams_snapshot_and_cleans_up exercised push_to (the legacy whole-database TCP
# push), removed in P4-1 with the rest of the legacy v2 protocol; make_snapshot's own streaming
# and cleanup is still covered by test_snapshot_includes_wal_changes and
# test_prune_outgoing_snapshots below. Deleted rather than weakened (blueprint §0 rule 4).


def test_recv_exact_uses_bytearray():
    import socket
    import threading
    from sync_peer import _recv_exact

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.bind(("127.0.0.1", 0))
    server_sock.listen(1)
    port = server_sock.getsockname()[1]

    client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client_sock.connect(("127.0.0.1", port))
    server_conn, _ = server_sock.accept()

    try:
        test_data = b"X" * (2 * 1024 * 1024)  # 2 MB
        t = threading.Thread(target=lambda: server_conn.sendall(test_data))
        t.start()

        received = _recv_exact(client_sock, len(test_data))
        t.join()
        assert received == test_data
        assert isinstance(received, bytes)
    finally:
        client_sock.close()
        server_conn.close()
        server_sock.close()


def test_make_snapshot_raises_if_dest_exists(tmp_path):
    import os
    import pytest
    import security
    from database import SeraDatabase, make_snapshot

    db_path = str(tmp_path / "test_orig.db")
    salt_path = str(tmp_path / "test.salt")
    security.generate_and_save_salt(salt_path)
    salt = security.load_salt(salt_path)
    hex_key = security.derive_key_hex("testpass123", salt)

    db = SeraDatabase(db_path, hex_key, defer_startup_maintenance=True)

    dest_path = str(tmp_path / "existing_snapshot.db")
    with open(dest_path, "wb") as f:
        f.write(b"EXISTING_VALUABLE_DATA_DO_NOT_DELETE")

    with pytest.raises(FileExistsError):
        make_snapshot(db, dest_path)

    # Verify original file content was not overwritten or deleted
    with open(dest_path, "rb") as f:
        assert f.read() == b"EXISTING_VALUABLE_DATA_DO_NOT_DELETE"


def test_prune_outgoing_snapshots(tmp_path):
    import os
    import time
    from sync_peer import prune_outgoing_snapshots

    out_dir = tmp_path / "incoming" / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    stale_dir = out_dir / "snap_stale_123"
    stale_dir.mkdir()
    (stale_dir / "master.db").write_bytes(b"stale_db")

    fresh_dir = out_dir / "snap_fresh_456"
    fresh_dir.mkdir()
    (fresh_dir / "master.db").write_bytes(b"fresh_db")

    # Set stale_dir mtime to 600s in the past
    stale_time = time.time() - 600.0
    os.utime(str(stale_dir), (stale_time, stale_time))

    # Prune with 300s max age: stale_dir should be deleted, fresh_dir kept
    prune_outgoing_snapshots(out_dir, max_age_seconds=300.0)
    assert not stale_dir.exists()
    assert fresh_dir.exists()

    # Prune with 0.0s (startup mode): all snap_* folders deleted
    prune_outgoing_snapshots(out_dir, max_age_seconds=0.0)
    assert not fresh_dir.exists()


# test_push_is_staged_not_live exercised push_to's staging behaviour (the legacy whole-database
# TCP push writes to incoming/, never in place), removed in P4-1 along with the inbound
# push_database handler; pending_swap.json is now only ever produced by a pre-P4-1 sender, and
# apply_pending_swap's own staged-file handling is covered by test_apply_pending_swap below.
# Deleted rather than weakened (blueprint §0 rule 4).


def test_apply_pending_swap(tmp_path):
    import json
    from sync_peer import apply_pending_swap

    app_dir = tmp_path / "app"
    app_dir.mkdir()
    incoming_dir = app_dir / "incoming"
    incoming_dir.mkdir()

    # Create original live files
    live_db = app_dir / "master.db"
    live_salt = app_dir / "sera.salt"
    live_db.write_bytes(b"ORIGINAL_LIVE_DATABASE_BYTES")
    live_salt.write_bytes(b"ORIGINAL_LIVE_SALT_BYTES")

    # Create dummy WAL and SHM files
    wal_file = app_dir / "master.db-wal"
    shm_file = app_dir / "master.db-shm"
    wal_file.write_bytes(b"DUMMY_WAL_DATA")
    shm_file.write_bytes(b"DUMMY_SHM_DATA")
    assert wal_file.exists()
    assert shm_file.exists()

    # Create staged incoming files
    staged_db = incoming_dir / "master.db"
    staged_salt = incoming_dir / "sera.salt"
    staged_db.write_bytes(b"NEW_STAGED_DATABASE_BYTES")
    staged_salt.write_bytes(b"NEW_STAGED_SALT_BYTES")

    pending_swap = incoming_dir / "pending_swap.json"
    pending_swap.write_text(json.dumps({
        "db": "master.db",
        "salt": "sera.salt",
        "from": "RemoteHost",
        "at": "2026-09-23T18:00:00Z"
    }), encoding="utf-8")

    # Run apply_pending_swap
    applied = apply_pending_swap(app_dir)
    assert applied is True

    # 1. Live files have swapped content
    assert live_db.read_bytes() == b"NEW_STAGED_DATABASE_BYTES"
    assert live_salt.read_bytes() == b"NEW_STAGED_SALT_BYTES"

    # 2. Staged files no longer in incoming/ (they were moved into live)
    assert not staged_db.exists()
    assert not staged_salt.exists()

    # 3. pending_swap.json was deleted
    assert not pending_swap.exists()

    # 4. Backups exist with original content
    backup_dbs = list(app_dir.glob("master.db.pre-sync-*.db"))
    assert len(backup_dbs) >= 1
    assert backup_dbs[0].read_bytes() == b"ORIGINAL_LIVE_DATABASE_BYTES"

    backup_salts = list(app_dir.glob("sera.salt.pre-sync-*"))
    assert len(backup_salts) >= 1
    assert backup_salts[0].read_bytes() == b"ORIGINAL_LIVE_SALT_BYTES"

    # 5. WAL and SHM files are removed
    assert not wal_file.exists()
    assert not shm_file.exists()


# test_push_with_other_password_rejected exercised the inbound push_database handler's
# password-mismatch verification, removed in P4-1 along with the rest of the legacy v2 protocol
# (the TCP server now serves only fetch_snapshot, whose own password/key verification is
# covered by tests/test_key_fingerprint.py and the join-flow tests below). Deleted rather than
# weakened (blueprint §0 rule 4).


def test_apply_pending_swap_backup_failure_preserves_live_db(tmp_path, monkeypatch):
    """Blocking 1 regression: A failed backup copy must never delete the live DB or salt."""
    import shutil
    import json
    from sync_peer import apply_pending_swap

    live_db = tmp_path / "master.db"
    live_salt = tmp_path / "sera.salt"
    live_db.write_bytes(b"LIVE_DB_ORIGINAL_BYTES")
    live_salt.write_bytes(b"LIVE_SALT_ORIGINAL_BYTES")

    incoming_dir = tmp_path / "incoming"
    incoming_dir.mkdir()
    (incoming_dir / "master.db").write_bytes(b"STAGED_NEW_DB_BYTES")
    (incoming_dir / "sera.salt").write_bytes(b"STAGED_NEW_SALT_BYTES")
    pending_json = incoming_dir / "pending_swap.json"
    pending_json.write_text(json.dumps({"db": "master.db", "salt": "sera.salt"}), encoding="utf-8")

    # Simulate disk full / permission error during backup copy
    orig_copy2 = shutil.copy2
    def failing_copy2(src, dst):
        if "pre-sync" in str(dst):
            raise OSError("Disk full during pre-sync backup")
        return orig_copy2(src, dst)

    monkeypatch.setattr(shutil, "copy2", failing_copy2)

    import pytest
    with pytest.raises(OSError, match="Disk full"):
        apply_pending_swap(tmp_path)

    # Live DB and salt must be intact and NOT unlinked
    assert live_db.exists(), "Live DB must NOT be deleted when backup fails"
    assert live_db.read_bytes() == b"LIVE_DB_ORIGINAL_BYTES"
    assert live_salt.exists(), "Live salt must NOT be deleted when backup fails"
    assert live_salt.read_bytes() == b"LIVE_SALT_ORIGINAL_BYTES"
    # pending_swap.json must be renamed to .failed
    assert (incoming_dir / "pending_swap.json.failed").exists()


def test_apply_pending_swap_strict_incoming_and_no_wal_deletion(tmp_path):
    """Blocking 2 regression: Path traversal or missing staged files must not fall back to app_path or delete live WAL."""
    import json
    from sync_peer import apply_pending_swap

    live_db = tmp_path / "master.db"
    live_wal = tmp_path / "master.db-wal"
    live_shm = tmp_path / "master.db-shm"
    live_salt = tmp_path / "sera.salt"
    live_db.write_bytes(b"LIVE_DB_CONTENT")
    live_wal.write_bytes(b"LIVE_WAL_CONTENT")
    live_shm.write_bytes(b"LIVE_SHM_CONTENT")
    live_salt.write_bytes(b"LIVE_SALT_CONTENT")

    incoming_dir = tmp_path / "incoming"
    incoming_dir.mkdir()
    pending_json = incoming_dir / "pending_swap.json"

    # Subtest A: Traversal attack in pending_swap.json
    pending_json.write_text(json.dumps({"db": "../master.db", "salt": "sera.salt"}), encoding="utf-8")
    assert apply_pending_swap(tmp_path) is False
    assert live_db.exists()
    assert live_wal.exists()
    assert live_shm.exists()
    assert (incoming_dir / "pending_swap.json.failed").exists()
    (incoming_dir / "pending_swap.json.failed").unlink()

    # Subtest B: Staged files missing from incoming/
    pending_json.write_text(json.dumps({"db": "master.db", "salt": "sera.salt"}), encoding="utf-8")
    assert apply_pending_swap(tmp_path) is False
    # Crucial: Live files and WAL/SHM must still exist, not replaced with themselves or sidecars unlinked
    assert live_db.read_bytes() == b"LIVE_DB_CONTENT"
    assert live_wal.read_bytes() == b"LIVE_WAL_CONTENT"
    assert live_shm.read_bytes() == b"LIVE_SHM_CONTENT"
    assert (incoming_dir / "pending_swap.json.failed").exists()


def test_apply_pending_swap_db_replace_failure_restores_wal_and_shm(tmp_path, monkeypatch):
    """Review regression: A failed os.replace on live DB must restore live WAL and SHM sidecars."""
    import os
    import json
    import pytest
    from sync_peer import apply_pending_swap

    live_db = tmp_path / "master.db"
    live_wal = tmp_path / "master.db-wal"
    live_shm = tmp_path / "master.db-shm"
    live_salt = tmp_path / "sera.salt"
    live_db.write_bytes(b"LIVE_DB_ORIGINAL_BYTES")
    live_wal.write_bytes(b"LIVE_WAL_ORIGINAL_BYTES")
    live_shm.write_bytes(b"LIVE_SHM_ORIGINAL_BYTES")
    live_salt.write_bytes(b"LIVE_SALT_ORIGINAL_BYTES")

    incoming_dir = tmp_path / "incoming"
    incoming_dir.mkdir()
    (incoming_dir / "master.db").write_bytes(b"STAGED_NEW_DB_BYTES")
    (incoming_dir / "sera.salt").write_bytes(b"STAGED_NEW_SALT_BYTES")
    pending_json = incoming_dir / "pending_swap.json"
    pending_json.write_text(json.dumps({"db": "master.db", "salt": "sera.salt"}), encoding="utf-8")

    # Simulate antivirus lock failing os.replace on the live DB
    orig_replace = os.replace
    def failing_replace(src, dst):
        if str(dst).endswith("master.db"):
            raise PermissionError("Access denied by antivirus lock")
        return orig_replace(src, dst)

    monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(PermissionError, match="antivirus lock"):
        apply_pending_swap(tmp_path)

    # 1. Live DB and salt must be untouched
    assert live_db.exists()
    assert live_db.read_bytes() == b"LIVE_DB_ORIGINAL_BYTES"
    assert live_salt.exists()
    assert live_salt.read_bytes() == b"LIVE_SALT_ORIGINAL_BYTES"

    # 2. Live WAL and SHM MUST be restored from pre-sync backup and still exist!
    assert live_wal.exists(), "Live WAL must be restored when DB replacement fails"
    assert live_wal.read_bytes() == b"LIVE_WAL_ORIGINAL_BYTES"
    assert live_shm.exists(), "Live SHM must be restored when DB replacement fails"
    assert live_shm.read_bytes() == b"LIVE_SHM_ORIGINAL_BYTES"

    # 3. pending_swap.json was renamed to .failed
    assert (incoming_dir / "pending_swap.json.failed").exists()


# test_push_rejected_on_zero_byte_or_zero_table, test_push_rejected_when_busy and
# test_push_rejected_when_restart_pending exercised the inbound push_database handler's
# payload/staging guards, removed in P4-1 along with the rest of the legacy v2 protocol (the
# TCP server now serves only fetch_snapshot). Deleted rather than weakened (blueprint §0 rule 4).


def test_raw_db_reset_is_reported(tmp_path):
    """Test that rawPayload.db reset is reported when backup succeeds."""
    import os
    import security
    import sqlcipher3.dbapi2 as sqlite3
    from database import SeraDatabase

    db_path = str(tmp_path / "master.db")
    raw_db_path = str(tmp_path / "rawPayload.db")
    salt_path = str(tmp_path / "sera.salt")
    password = "testpass123"

    security.generate_and_save_salt(salt_path)
    salt = security.load_salt(salt_path)
    hex_key = security.derive_key_hex(password, salt)

    db = SeraDatabase(db_path, hex_key, raw_db_path=raw_db_path, defer_startup_maintenance=True)
    assert os.path.exists(raw_db_path)

    # Corrupt rawPayload.db
    with open(raw_db_path, "wb") as f:
        f.write(b"CORRUPTED_DATA")
    del db

    # Auto-heal should create a backup
    db2 = SeraDatabase(db_path, hex_key, raw_db_path=raw_db_path, defer_startup_maintenance=True)

    # Should have backup set to an actual file (not "reset_without_backup")
    assert db2.raw_db_was_reset is not None
    assert db2.raw_db_was_reset != "reset_without_backup"
    assert os.path.exists(db2.raw_db_was_reset)
    assert os.path.getsize(db2.raw_db_was_reset) > 0

    # Backup should contain corrupted data
    with open(db2.raw_db_was_reset, "rb") as f:
        assert b"CORRUPTED_DATA" in f.read()

    # New DB should be valid
    conn = sqlite3.connect(raw_db_path)
    conn.execute(f"PRAGMA key = \"x'{hex_key}'\";")
    conn.execute("SELECT count(*) FROM sqlite_master;")
    conn.close()

    # Audit log should record backup
    with db2._connect() as conn:
        rows = conn.execute(
            "SELECT action, detail FROM audit_log WHERE action = 'raw_db_reset' ORDER BY id DESC LIMIT 1"
        ).fetchall()

    assert len(rows) > 0
    action, detail = rows[0]
    assert action == "raw_db_reset"
    assert os.path.basename(db2.raw_db_was_reset) in detail


def test_raw_db_reset_without_backup(tmp_path):
    """Test that rawPayload.db reset is reported even when no backup exists."""
    import os
    import security
    import sqlcipher3.dbapi2 as sqlite3
    from database import SeraDatabase

    db_path = str(tmp_path / "master.db")
    raw_db_path = str(tmp_path / "rawPayload.db")
    salt_path = str(tmp_path / "sera.salt")
    password = "testpass123"

    security.generate_and_save_salt(salt_path)
    salt = security.load_salt(salt_path)
    hex_key = security.derive_key_hex(password, salt)

    db = SeraDatabase(db_path, hex_key, raw_db_path=raw_db_path, defer_startup_maintenance=True)
    del db

    # Create 0-byte rawPayload.db (no backup can be made from empty file)
    with open(raw_db_path, "wb") as f:
        pass  # Write nothing, creating a 0-byte file
    assert os.path.getsize(raw_db_path) == 0

    # Auto-heal should recreate without a backup
    db2 = SeraDatabase(db_path, hex_key, raw_db_path=raw_db_path, defer_startup_maintenance=True)

    # Should indicate reset without backup
    assert db2.raw_db_was_reset == "reset_without_backup"

    # New DB should be recreated and valid
    assert os.path.exists(raw_db_path)
    assert os.path.getsize(raw_db_path) > 0
    conn = sqlite3.connect(raw_db_path)
    conn.execute(f"PRAGMA key = \"x'{hex_key}'\";")
    conn.execute("SELECT count(*) FROM sqlite_master;")
    conn.close()

    # Audit log should record the reset
    with db2._connect() as conn:
        rows = conn.execute(
            "SELECT action, detail FROM audit_log WHERE action = 'raw_db_reset' ORDER BY id DESC LIMIT 1"
        ).fetchall()

    assert len(rows) > 0
    action, detail = rows[0]
    assert action == "raw_db_reset"
    assert "no backup" in detail.lower()


def _raw_reset_env(tmp_path):
    import security
    from database import SeraDatabase

    db_path = str(tmp_path / "master.db")
    raw_db_path = str(tmp_path / "rawPayload.db")
    salt_path = str(tmp_path / "sera.salt")
    security.generate_and_save_salt(salt_path)
    hex_key = security.derive_key_hex("testpass123", security.load_salt(salt_path))
    db = SeraDatabase(db_path, hex_key, raw_db_path=raw_db_path, defer_startup_maintenance=True)
    del db
    return db_path, raw_db_path, hex_key


def test_raw_db_reset_backs_up_sidecars(tmp_path):
    """-wal/-shm next to an un-decryptable rawPayload.db are backed up, not just deleted."""
    import os
    from database import SeraDatabase

    db_path, raw_db_path, hex_key = _raw_reset_env(tmp_path)
    db2 = SeraDatabase(db_path, hex_key, raw_db_path=raw_db_path, defer_startup_maintenance=True)
    # Called directly: a fake -wal with an invalid header would be discarded by SQLite's
    # own open probe before the heal runs.
    with open(raw_db_path, "wb") as f:
        f.write(b"CORRUPTED_DATA")
    with open(raw_db_path + "-wal", "wb") as f:
        f.write(b"WAL_PAGES")

    assert db2._auto_heal_raw_db() is True

    assert not os.path.exists(raw_db_path + "-wal")
    backup = db2.raw_db_was_reset
    assert backup and backup != "reset_without_backup"
    with open(backup + "-wal", "rb") as f:
        assert f.read() == b"WAL_PAGES"


def test_raw_db_left_alone_when_backup_impossible(tmp_path, monkeypatch):
    """If neither copy nor move works, the file stays, nothing is reported as reset and start-up
    stops with a clear error (blueprint §0 rule 3)."""
    import os
    import shutil
    import pytest
    import database
    from database import SeraDatabase

    db_path, raw_db_path, hex_key = _raw_reset_env(tmp_path)
    with open(raw_db_path, "wb") as f:
        f.write(b"CORRUPTED_DATA")

    def _fail(*a, **k):
        raise OSError("simulated")
    monkeypatch.setattr(shutil, "copy2", _fail)
    monkeypatch.setattr(database.os, "replace", _fail)

    with pytest.raises(RuntimeError, match="left untouched"):
        SeraDatabase(db_path, hex_key, raw_db_path=raw_db_path, defer_startup_maintenance=True)

    monkeypatch.undo()
    with open(raw_db_path, "rb") as f:
        assert f.read() == b"CORRUPTED_DATA"
    import sqlcipher3.dbapi2 as sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute(f"PRAGMA key = \"x'{hex_key}'\";")
    rows = conn.execute("SELECT count(*) FROM audit_log WHERE action = 'raw_db_reset'").fetchone()[0]
    conn.close()
    assert rows == 0


def test_join_approval_dialog_ui():
    """Verifies JoinApprovalDialog formats code and responds to accept/reject."""
    from PySide6.QtWidgets import QApplication, QDialog
    from ui.dialogs.first_run_dialog import JoinApprovalDialog
    app = QApplication.instance() or QApplication([])

    dlg = JoinApprovalDialog(host="OfficePC", username="John", code="123456", timeout_seconds=120)

    # Check that code is formatted nicely
    assert "123  456" in dlg.code_label.text()
    assert "OfficePC" in dlg.windowTitle() or "Sera" in dlg.windowTitle()

    # Simulate Allow button click
    dlg.allow_btn.click()
    assert dlg.result() == QDialog.Accepted

    # Test Reject
    dlg_reject = JoinApprovalDialog(host="OfficePC2", username="Jane", code="654321", timeout_seconds=120)
    dlg_reject.reject_btn.click()
    assert dlg_reject.result() == QDialog.Rejected

    # Test timeout tick
    dlg_timeout = JoinApprovalDialog(host="OfficePC3", username="Bob", code="999888", timeout_seconds=2)
    dlg_timeout._on_tick()
    assert dlg_timeout.timeout_remaining == 1
    dlg_timeout._on_tick()
    assert dlg_timeout.timeout_remaining == 0
    assert dlg_timeout.result() == QDialog.Rejected


# ---------------- P0-7: authenticate legacy sync messages (HMAC) ----------------

# test_authenticated_push_accepted sent an authenticated push_database through push_to and
# expected the legacy staged-swap acceptance path to run; P4-1 removed push_to and the inbound
# push_database handler along with the rest of the legacy v2 protocol (the TCP server now
# serves only fetch_snapshot). The HMAC signing/verification machinery itself
# (_sign_header/_verify_header/_auth_key) is still exercised by
# test_unauthenticated_push_rejected and test_stale_timestamp_rejected below, which construct
# their own wire messages rather than going through push_to. Deleted rather than weakened
# (blueprint §0 rule 4).


def test_unauthenticated_push_rejected(tmp_path):
    """Once a receiver is configured with an office key, a push_database header with no
    mac (or a mac signed with the wrong key) is rejected before anything is staged."""
    import socket
    import json
    import hmac
    import hashlib
    import time as _time
    import security
    from sync_peer import SyncPeerService, _send_framed, _recv_framed, canonical_json, SERA_SYNC_AUTH_INFO

    receiver_dir = tmp_path / "receiver"
    receiver_dir.mkdir()
    receiver_salt_path = str(receiver_dir / "sera.salt")
    security.generate_and_save_salt(receiver_salt_path)
    salt_bytes = security.load_salt(receiver_salt_path)
    hex_key = security.derive_key_hex("office_pass_1", salt_bytes)

    receiver_service = SyncPeerService(
        db_path=str(receiver_dir / "master.db"),
        salt_path=receiver_salt_path,
        username="Receiver",
        sync_port=0,
        hex_key=hex_key,
    )
    receiver_service.start()

    try:
        port = receiver_service._tcp_server.getsockname()[1]

        # Case 1: no ts/mac at all.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", port))
        header = {
            "action": "push_database",
            "host": "Attacker-PC",
            "db_size": 100,
            "salt_size": 16,
            "client_count": 5,
            "sync_revision": 5,
        }
        _send_framed(sock, json.dumps(header).encode("utf-8"))
        resp = json.loads(_recv_framed(sock).decode("utf-8"))
        sock.close()
        assert resp.get("status") == "rejected"
        assert resp.get("reason") == "UNAUTHENTICATED"
        assert not (receiver_dir / "incoming" / "pending_swap.json").exists()

        # Case 2: mac present but signed with the wrong key (forged).
        wrong_hex_key = security.derive_key_hex("wrong_pass", salt_bytes)
        wrong_auth_key = hmac.new(bytes.fromhex(wrong_hex_key), SERA_SYNC_AUTH_INFO, hashlib.sha256).digest()
        forged = dict(header)
        forged["ts"] = int(_time.time())
        forged["mac"] = hmac.new(wrong_auth_key, canonical_json(forged), hashlib.sha256).hexdigest()

        sock2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock2.connect(("127.0.0.1", port))
        _send_framed(sock2, json.dumps(forged).encode("utf-8"))
        resp2 = json.loads(_recv_framed(sock2).decode("utf-8"))
        sock2.close()
        assert resp2.get("status") == "rejected"
        assert resp2.get("reason") == "UNAUTHENTICATED"
    finally:
        receiver_service.stop()


def test_unauthenticated_pull_rejected(tmp_path):
    """request_database_pull with no mac is rejected. (P4-1 removed the inbound
    request_database_pull handler entirely along with push_to/the reverse-push it used to
    trigger (F9); this now checks only that the auth gate still rejects the message before
    any other handling, since fetch_snapshot is the only action the server still serves.)"""
    import socket
    import json
    import security
    from sync_peer import SyncPeerService, _send_framed, _recv_framed

    receiver_dir = tmp_path / "receiver"
    receiver_dir.mkdir()
    receiver_salt_path = str(receiver_dir / "sera.salt")
    security.generate_and_save_salt(receiver_salt_path)
    salt_bytes = security.load_salt(receiver_salt_path)
    hex_key = security.derive_key_hex("office_pass_2", salt_bytes)

    receiver_service = SyncPeerService(
        db_path=str(receiver_dir / "master.db"),
        salt_path=receiver_salt_path,
        username="Receiver",
        sync_port=0,
        hex_key=hex_key,
    )
    receiver_service.start()

    try:
        port = receiver_service._tcp_server.getsockname()[1]
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", port))
        header = {
            "action": "request_database_pull",
            "host": "Attacker-PC",
            "sync_port": 0,
        }
        _send_framed(sock, json.dumps(header).encode("utf-8"))
        resp = json.loads(_recv_framed(sock).decode("utf-8"))
        sock.close()
        assert resp.get("status") == "rejected"
        assert resp.get("reason") == "UNAUTHENTICATED"
    finally:
        receiver_service.stop()


def test_stale_timestamp_rejected(tmp_path):
    """A correctly-signed header whose ts is outside the 120s tolerance is rejected,
    even though the mac itself is valid (replay protection)."""
    import socket
    import json
    import hmac
    import hashlib
    import security
    from sync_peer import SyncPeerService, _send_framed, _recv_framed, canonical_json, SERA_SYNC_AUTH_INFO

    receiver_dir = tmp_path / "receiver"
    receiver_dir.mkdir()
    receiver_salt_path = str(receiver_dir / "sera.salt")
    security.generate_and_save_salt(receiver_salt_path)
    salt_bytes = security.load_salt(receiver_salt_path)
    hex_key = security.derive_key_hex("office_pass_3", salt_bytes)

    receiver_service = SyncPeerService(
        db_path=str(receiver_dir / "master.db"),
        salt_path=receiver_salt_path,
        username="Receiver",
        sync_port=0,
        hex_key=hex_key,
    )
    receiver_service.start()

    try:
        port = receiver_service._tcp_server.getsockname()[1]

        auth_key = hmac.new(bytes.fromhex(hex_key), SERA_SYNC_AUTH_INFO, hashlib.sha256).digest()
        header = {
            "action": "request_database_pull",
            "host": "Stale-PC",
            "sync_port": 0,
            "ts": int(time.time()) - 300,  # 5 minutes old: outside AUTH_TS_TOLERANCE_SEC (120s)
        }
        header["mac"] = hmac.new(auth_key, canonical_json(header), hashlib.sha256).hexdigest()

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", port))
        _send_framed(sock, json.dumps(header).encode("utf-8"))
        resp = json.loads(_recv_framed(sock).decode("utf-8"))
        sock.close()
        assert resp.get("status") == "rejected"
        assert resp.get("reason") == "STALE_TIMESTAMP"
    finally:
        receiver_service.stop()


# ---------------- P0-10: Discovery via directed broadcast, Add PC by IP, Host-keyed peers ----------------

def test_peer_ip_change_updates_entry(tmp_path):
    """
    Acceptance test for P0-10:
    1. PeerInfo.key() returns self.host (not host:ip).
    2. An IP change from the same host updates the existing entry instead of adding a ghost.
    """
    import json
    from sync_peer import SyncPeerService, PeerInfo, SERA_SYNC_MAGIC

    peer_info = PeerInfo(
        username="StaffMember",
        host="WORKSTATION-A",
        ip="192.168.1.50",
        sync_port=49157,
    )
    # PeerInfo.key() must return the hostname
    assert peer_info.key() == "WORKSTATION-A", f"Expected 'WORKSTATION-A', got '{peer_info.key()}'"

    service_dir = tmp_path / "service_p010"
    service_dir.mkdir()
    service = SyncPeerService(
        db_path=str(service_dir / "master.db"),
        salt_path=str(service_dir / "sera.salt"),
        username="LocalUser",
        host_name="LOCAL-HOST",
        sync_port=0,
        enable_broadcast=False,
    )

    # First beacon from WORKSTATION-A at 192.168.1.50
    beacon_body_1 = {
        "magic": SERA_SYNC_MAGIC,
        "username": "StaffMember",
        "host": "WORKSTATION-A",
        "sync_port": 49157,
        "client_count": 5,
    }
    service._handle_beacon(json.dumps(beacon_body_1).encode("utf-8"), "192.168.1.50")

    peers = service.get_peers()
    assert len(peers) == 1
    assert peers[0]["host"] == "WORKSTATION-A"
    assert peers[0]["ip"] == "192.168.1.50"

    # Second beacon from SAME WORKSTATION-A, but its IP changed to 192.168.1.75 (e.g. DHCP renewal)
    beacon_body_2 = {
        "magic": SERA_SYNC_MAGIC,
        "username": "StaffMember",
        "host": "WORKSTATION-A",
        "sync_port": 49157,
        "client_count": 6,
    }
    service._handle_beacon(json.dumps(beacon_body_2).encode("utf-8"), "192.168.1.75")

    # Entry must be updated in-place: still exactly 1 peer, no ghost with the old IP
    peers_after = service.get_peers()
    assert len(peers_after) == 1, f"Expected 1 peer after IP change, got {len(peers_after)}"
    assert peers_after[0]["host"] == "WORKSTATION-A"
    assert peers_after[0]["ip"] == "192.168.1.75"
    assert peers_after[0]["client_count"] == 6


def test_manual_peer_unicast_beacon(tmp_path):
    """
    Acceptance test for P0-10:
    Two services on localhost, broadcast disabled by a flag (enable_broadcast=False),
    discovery via the manual list (sync_manual_peers / manual_peers):
    - Service A sends a unicast beacon to Service B.
    - Service B receives the unicast beacon, records Service A, and answers with a unicast beacon back.
    - Service A receives the reply and records Service B.
    - Both services discover each other without broadcast.
    """
    import socket
    from sync_peer import SyncPeerService

    dir_a = tmp_path / "service_a"
    dir_a.mkdir()
    dir_b = tmp_path / "service_b"
    dir_b.mkdir()

    # Find two free UDP ports for beacon testing
    def _find_free_udp_port():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("", 0))
        port = s.getsockname()[1]
        s.close()
        return port

    port_b = _find_free_udp_port()
    port_a = _find_free_udp_port()
    while port_a == port_b:
        port_a = _find_free_udp_port()

    # Service B: broadcast disabled, no manual peers configured
    service_b = SyncPeerService(
        db_path=str(dir_b / "master.db"),
        salt_path=str(dir_b / "sera.salt"),
        username="UserB",
        host_name="HOST-BETA",
        sync_port=0,
        beacon_port=port_b,
        enable_broadcast=False,
    )

    # Service A: broadcast disabled, configured with Service B's address in manual_peers
    service_a = SyncPeerService(
        db_path=str(dir_a / "master.db"),
        salt_path=str(dir_a / "sera.salt"),
        username="UserA",
        host_name="HOST-ALPHA",
        sync_port=0,
        beacon_port=port_a,
        enable_broadcast=False,
        manual_peers=[f"127.0.0.1:{port_b}"],
    )

    service_b.start()
    service_a.start()

    try:
        # Service A sends unicast beacon to manual peers
        service_a.send_manual_beacons()

        # Wait up to 3 seconds for exchange to complete
        start = time.monotonic()
        discovered_a = False
        discovered_b = False
        while time.monotonic() - start < 3.0:
            peers_on_b = [p["host"] for p in service_b.get_peers()]
            peers_on_a = [p["host"] for p in service_a.get_peers()]
            if "HOST-ALPHA" in peers_on_b:
                discovered_a = True
            if "HOST-BETA" in peers_on_a:
                discovered_b = True
            if discovered_a and discovered_b:
                break
            time.sleep(0.1)

        assert discovered_a, f"Service B failed to discover HOST-ALPHA via unicast beacon. Peers on B: {service_b.get_peers()}"
        assert discovered_b, f"Service A failed to discover HOST-BETA via unicast reply. Peers on A: {service_a.get_peers()}"

    finally:
        service_a.stop()
        service_b.stop()


def test_directed_broadcast_addresses_skips_loopback_and_link_local():
    """
    P0-10: Directed broadcast calculation using ifaddr:
    - Calculates ip | ~netmask for IPv4 adapters.
    - Skips 127.* (loopback) and 169.254.* (APIPA link-local).
    """
    from unittest.mock import patch, MagicMock
    from sync_peer import _get_directed_broadcast_addresses

    # Mock ifaddr.get_adapters() with various interfaces
    class MockIP:
        def __init__(self, ip, prefix, is_ipv4=True):
            self.ip = ip
            self.network_prefix = prefix
            self.is_IPv4 = is_ipv4
            self.is_IPv6 = not is_ipv4

    class MockAdapter:
        def __init__(self, name, ips):
            self.name = name
            self.ips = ips

    mock_adapters = [
        MockAdapter("eth0", [
            MockIP("192.168.1.100", 24),     # Valid LAN -> 192.168.1.255
            MockIP("fe80::1", 64, is_ipv4=False),  # IPv6 -> skip
        ]),
        MockAdapter("wlan0", [
            MockIP("10.0.5.20", 16),          # Valid Wi-Fi -> 10.0.255.255
        ]),
        MockAdapter("lo", [
            MockIP("127.0.0.1", 8),           # Loopback -> skip
        ]),
        MockAdapter("auto_ip", [
            MockIP("169.254.12.34", 16),       # Link-local APIPA -> skip
        ]),
    ]

    with patch("ifaddr.get_adapters", return_value=mock_adapters):
        addrs = _get_directed_broadcast_addresses()
        assert "192.168.1.255" in addrs
        assert "10.0.255.255" in addrs
        # Must not contain loopback or link-local
        for addr in addrs:
            assert not addr.startswith("127.")
            assert not addr.startswith("169.254.")


def test_sync_manual_peers_setting_and_skips_own_address(tmp_path):
    """
    P0-10:
    1. sync_manual_peers setting in database is read by get_manual_peers().
    2. PC skips its own address when sending manual beacons.
    """
    import json
    from unittest.mock import patch, MagicMock
    from sync_peer import SyncPeerService

    dir_p = tmp_path / "sync_peers_db_test"
    dir_p.mkdir()

    class MockDB:
        def __init__(self):
            self.settings = {
                "sync_manual_peers": json.dumps(["192.168.1.50", "192.168.2.99"])
            }
        def get_setting(self, key, default=None):
            return self.settings.get(key, default)
        def set_setting(self, key, val):
            self.settings[key] = val

    mock_db = MockDB()
    service = SyncPeerService(
        db_path=str(dir_p / "master.db"),
        salt_path=str(dir_p / "sera.salt"),
        username="LocalUser",
        db=mock_db,
        enable_broadcast=False,
    )

    peers = service.get_manual_peers()
    assert "192.168.1.50" in peers
    assert "192.168.2.99" in peers

    # If 192.168.1.50 is this machine's own IP, it must be recognized as own address
    with patch("sync_peer._get_own_ips", return_value={"192.168.1.50", "127.0.0.1"}):
        assert service.is_own_address("192.168.1.50", service.beacon_port) is True
        assert service.is_own_address("192.168.2.99", service.beacon_port) is False

        # Sending manual beacons should only send to 192.168.2.99, skipping 192.168.1.50
        mock_sock = MagicMock()
        service.send_manual_beacons(sock=mock_sock)
        sent_destinations = [call[0][1] for call in mock_sock.sendto.call_args_list]
        assert ("192.168.2.99", service.beacon_port) in sent_destinations
        assert ("192.168.1.50", service.beacon_port) not in sent_destinations


def test_sera_sync_dialog_add_pc_by_ip_validates_and_stores():
    """
    P0-10: 'Add PC by IP' dialog logic:
    - Prompts user, validates IPv4 address.
    - Saves valid IP to DB setting sync_manual_peers (JSON list).
    - Notifies sync_service and sends unicast beacon.
    """
    import json
    from unittest.mock import patch, MagicMock
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    from ui.dialogs.sera_sync_dialog import SeraSyncDialog

    mock_sync_service = MagicMock()
    mock_sync_service.get_sync_state.return_value = {"status": "NORMAL"}
    mock_sync_service.get_peers.return_value = []
    mock_sync_service.get_activity_history.return_value = []
    mock_sync_service.get_network_category.return_value = {"is_public": False}
    mock_sync_service.inv_frames = False

    class MockDB:
        def __init__(self):
            self.settings = {}
        def get_setting(self, key, default=None):
            return self.settings.get(key, default)
        def set_setting(self, key, val):
            self.settings[key] = val

    mock_db = MockDB()
    dialog = SeraSyncDialog(sync_service=mock_sync_service, db=mock_db)

    # 1. Invalid IP entry -> warning shown, nothing added
    with patch("PySide6.QtWidgets.QInputDialog.getText", return_value=("not-an-ip", True)), \
         patch("PySide6.QtWidgets.QMessageBox.warning") as mock_warn:
        dialog._on_add_pc_by_ip()
        assert mock_warn.called
        assert "sync_manual_peers" not in mock_db.settings

    # 2. Valid IP entry -> stored in DB as JSON list, added to sync_service
    with patch("PySide6.QtWidgets.QInputDialog.getText", return_value=("192.168.5.120", True)), \
         patch("PySide6.QtWidgets.QMessageBox.information") as mock_info:
        dialog._on_add_pc_by_ip()
        assert mock_info.called
        assert "sync_manual_peers" in mock_db.settings
        saved_list = json.loads(mock_db.settings["sync_manual_peers"])
        assert "192.168.5.120" in saved_list
        mock_sync_service.add_manual_peer.assert_called_with("192.168.5.120")

    # 3. Duplicate IP entry -> informative message, not added twice
    with patch("PySide6.QtWidgets.QInputDialog.getText", return_value=("192.168.5.120", True)), \
         patch("PySide6.QtWidgets.QMessageBox.information") as mock_info:
        dialog._on_add_pc_by_ip()
        saved_list = json.loads(mock_db.settings["sync_manual_peers"])
        assert saved_list.count("192.168.5.120") == 1

    # 4. Out-of-range port entry (e.g. :70000) -> warning shown, not saved
    with patch("PySide6.QtWidgets.QInputDialog.getText", return_value=("192.168.1.5:70000", True)), \
         patch("PySide6.QtWidgets.QMessageBox.warning") as mock_warn:
        dialog._on_add_pc_by_ip()
        assert mock_warn.called
        saved_list = json.loads(mock_db.settings["sync_manual_peers"])
        assert "192.168.1.5:70000" not in saved_list

    dialog.close()


def test_manual_beacon_bad_port_isolation(tmp_path):
    """
    P0-10 review fix:
    A malformed or out-of-range port (e.g. 70000) must not cause an unhandled exception
    that skips subsequent peers in send_manual_beacons.
    """
    import socket
    from sync_peer import SyncPeerService

    dir_a = tmp_path / "service_bad_port_a"
    dir_a.mkdir()
    dir_b = tmp_path / "service_bad_port_b"
    dir_b.mkdir()

    def _find_free_udp_port():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("", 0))
        port = s.getsockname()[1]
        s.close()
        return port

    port_b = _find_free_udp_port()
    port_a = _find_free_udp_port()
    while port_a == port_b:
        port_a = _find_free_udp_port()

    service_b = SyncPeerService(
        db_path=str(dir_b / "master.db"),
        salt_path=str(dir_b / "sera.salt"),
        username="UserB",
        host_name="HOST-VALID-TARGET",
        sync_port=0,
        beacon_port=port_b,
        enable_broadcast=False,
    )

    # First entry has an invalid port, second entry is the valid target
    service_a = SyncPeerService(
        db_path=str(dir_a / "master.db"),
        salt_path=str(dir_a / "sera.salt"),
        username="UserA",
        host_name="HOST-SENDER",
        sync_port=0,
        beacon_port=port_a,
        enable_broadcast=False,
        manual_peers=["127.0.0.1:70000", f"127.0.0.1:{port_b}"],
    )

    service_b.start()
    service_a.start()

    try:
        service_a.send_manual_beacons()

        start = time.monotonic()
        discovered = False
        while time.monotonic() - start < 3.0:
            peers_on_b = [p["host"] for p in service_b.get_peers()]
            if "HOST-SENDER" in peers_on_b:
                discovered = True
                break
            time.sleep(0.1)

        assert discovered, "Target service failed to discover sender because bad port aborted the loop"
    finally:
        service_a.stop()
        service_b.stop()


def test_malformed_beacon_does_not_crash_listener(tmp_path):
    """
    P0-10 review fix:
    Malformed numeric fields (non-numeric beacon_port, sync_revision, etc.)
    must not crash the UDP listener thread.
    """
    import socket
    import json
    from sync_peer import SyncPeerService, SERA_SYNC_MAGIC

    svc_dir = tmp_path / "malformed_beacon_svc"
    svc_dir.mkdir()

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()

    service = SyncPeerService(
        db_path=str(svc_dir / "master.db"),
        salt_path=str(svc_dir / "sera.salt"),
        username="ListenerUser",
        host_name="LISTENER-HOST",
        sync_port=0,
        beacon_port=port,
        enable_broadcast=False,
    )
    service.start()

    try:
        # Send a malformed beacon with non-numeric beacon_port and string sync_revision
        malformed_body = {
            "magic": SERA_SYNC_MAGIC,
            "username": "Attacker",
            "host": "MALFORMED-PC",
            "sync_port": "not-a-number",
            "beacon_port": "bad-port",
            "sync_revision": "invalid",
            "request_reply": True,
        }
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(json.dumps(malformed_body).encode("utf-8"), ("127.0.0.1", port))
        sock.close()

        time.sleep(0.2)

        # Now send a valid beacon
        valid_body = {
            "magic": SERA_SYNC_MAGIC,
            "username": "ValidUser",
            "host": "VALID-PC",
            "sync_port": 49157,
            "sync_revision": 5,
            "client_count": 2,
        }
        sock2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock2.sendto(json.dumps(valid_body).encode("utf-8"), ("127.0.0.1", port))
        sock2.close()

        start = time.monotonic()
        discovered = False
        while time.monotonic() - start < 2.0:
            peers = [p["host"] for p in service.get_peers()]
            if "VALID-PC" in peers:
                discovered = True
                break
            time.sleep(0.05)

        assert discovered, "Listener thread crashed on malformed beacon and could not process valid beacon"
    finally:
        service.stop()


def test_unicast_beacon_reply_rate_limit(tmp_path):
    """
    P0-10 review fix:
    Unicast beacon replies must be rate-limited (cooldown per IP) to prevent reflection abuse.
    """
    import socket
    import json
    from sync_peer import SyncPeerService, SERA_SYNC_MAGIC

    svc_dir = tmp_path / "rate_limit_svc"
    svc_dir.mkdir()

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()

    service = SyncPeerService(
        db_path=str(svc_dir / "master.db"),
        salt_path=str(svc_dir / "sera.salt"),
        username="RateLimitedUser",
        host_name="RL-HOST",
        sync_port=0,
        beacon_port=port,
        enable_broadcast=False,
    )
    service.start()

    # Create a listener to receive the unicast replies
    receiver_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver_sock.bind(("127.0.0.1", 0))
    receiver_sock.settimeout(0.5)
    reply_port = receiver_sock.getsockname()[1]

    try:
        req = {
            "magic": SERA_SYNC_MAGIC,
            "username": "Probe",
            "host": "PROBE-PC",
            "beacon_port": reply_port,
            "request_reply": True,
        }
        data = json.dumps(req).encode("utf-8")

        # Send 5 rapid requests from 127.0.0.1
        sender_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for _ in range(5):
            sender_sock.sendto(data, ("127.0.0.1", port))
        sender_sock.close()

        # Count how many replies come back
        replies_received = 0
        start = time.monotonic()
        while time.monotonic() - start < 1.0:
            try:
                receiver_sock.recvfrom(2048)
                replies_received += 1
            except socket.timeout:
                break

        # Must only get 1 reply (remaining 4 dropped due to 5s cooldown per IP)
        assert replies_received == 1, f"Expected 1 rate-limited reply, got {replies_received}"
    finally:
        receiver_sock.close()
        service.stop()


def test_sync_peer_service_remove_manual_peer(tmp_path):
    """
    Test that SyncPeerService.remove_manual_peer removes an address from memory,
    DB settings, and active peers.
    """
    import json
    from sync_peer import SyncPeerService, PeerInfo

    class MockDB:
        def __init__(self):
            self.settings = {"sync_manual_peers": json.dumps(["192.168.1.10", "192.168.1.20"])}
        def get_setting(self, key, default=None):
            return self.settings.get(key, default)
        def set_setting(self, key, val):
            self.settings[key] = val

    salt_file = tmp_path / "salt.bin"
    salt_file.write_bytes(b"0" * 32)
    db = MockDB()
    srv = SyncPeerService(
        db_path=str(tmp_path / "test.db"),
        salt_path=str(salt_file),
        username="tester",
        db=db,
        enable_broadcast=False,
    )
    try:
        srv.add_manual_peer("192.168.1.30")
        assert "192.168.1.10" in srv.get_manual_peers()
        assert "192.168.1.30" in srv.get_manual_peers()

        # Add a peer to _peers with matching IP
        srv._peers["TEST-HOST"] = PeerInfo(
            username="test",
            host="TEST-HOST",
            ip="192.168.1.10",
            sync_port=50001,
            last_seen=time.time(),
        )
        assert any(p["host"] == "TEST-HOST" for p in srv.get_peers())

        # Remove 192.168.1.10
        srv.remove_manual_peer("192.168.1.10")
        assert "192.168.1.10" not in srv.get_manual_peers()
        assert "192.168.1.10" not in json.loads(db.settings["sync_manual_peers"])
        assert not any(p["host"] == "TEST-HOST" for p in srv.get_peers())
        assert "TEST-HOST" not in srv._peers
        assert "192.168.1.30" in srv.get_manual_peers()
    finally:
        srv.stop()


def test_sera_sync_dialog_remove_pc_by_ip():
    """
    Test that SeraSyncDialog allows removing configured manual peer IPs.
    """
    import json
    from unittest.mock import MagicMock, patch
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance() or QApplication([])

    from ui.dialogs.sera_sync_dialog import SeraSyncDialog

    mock_sync_service = MagicMock()
    mock_sync_service.get_sync_state.return_value = {"status": "NORMAL"}
    mock_sync_service.get_peers.return_value = []
    mock_sync_service.get_activity_history.return_value = []
    mock_sync_service.get_network_category.return_value = {"is_public": False}
    mock_sync_service.inv_frames = False
    mock_sync_service.get_manual_peers.return_value = []

    class MockDB:
        def __init__(self):
            self.settings = {}
        def get_setting(self, key, default=None):
            return self.settings.get(key, default)
        def set_setting(self, key, val):
            self.settings[key] = val

    mock_db = MockDB()
    dialog = SeraSyncDialog(sync_service=mock_sync_service, db=mock_db)

    # 1. No manual IPs configured -> informative message shown
    with patch("PySide6.QtWidgets.QMessageBox.information") as mock_info:
        dialog._on_remove_pc_by_ip()
        assert mock_info.called
        assert "No manual" in mock_info.call_args[0][2]

    # Populate manual peers
    mock_db.settings["sync_manual_peers"] = json.dumps(["192.168.1.50", "10.0.0.99"])

    # 2. User cancels selection dialog -> nothing removed
    with patch("PySide6.QtWidgets.QInputDialog.getItem", return_value=("192.168.1.50", False)):
        dialog._on_remove_pc_by_ip()
        saved = json.loads(mock_db.settings["sync_manual_peers"])
        assert "192.168.1.50" in saved

    # 3. User selects item but cancels confirmation -> nothing removed
    with patch("PySide6.QtWidgets.QInputDialog.getItem", return_value=("192.168.1.50", True)), \
         patch("PySide6.QtWidgets.QMessageBox.question", return_value=QMessageBox.No):
        dialog._on_remove_pc_by_ip()
        saved = json.loads(mock_db.settings["sync_manual_peers"])
        assert "192.168.1.50" in saved

    # 4. User selects item and confirms -> removed from DB and service
    with patch("PySide6.QtWidgets.QInputDialog.getItem", return_value=("192.168.1.50", True)), \
         patch("PySide6.QtWidgets.QMessageBox.question", return_value=QMessageBox.Yes), \
         patch("PySide6.QtWidgets.QMessageBox.information") as mock_info:
        dialog._on_remove_pc_by_ip()
        assert mock_info.called
        saved = json.loads(mock_db.settings["sync_manual_peers"])
        assert "192.168.1.50" not in saved
        assert "10.0.0.99" in saved
        mock_sync_service.remove_manual_peer.assert_called_with("192.168.1.50")

    dialog.close()


def test_sera_sync_dialog_table_context_menu_remove():
    """
    Test that right-clicking a manual peer row provides a context menu option to remove it.
    """
    import json
    from unittest.mock import MagicMock, patch
    from PySide6.QtWidgets import QApplication, QMessageBox, QTableWidgetItem
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog

    app = QApplication.instance() or QApplication([])

    mock_sync_service = MagicMock()
    mock_sync_service.get_sync_state.return_value = {"status": "NORMAL"}
    mock_sync_service.get_peers.return_value = []
    mock_sync_service.get_activity_history.return_value = []
    mock_sync_service.get_network_category.return_value = {"is_public": False}
    mock_sync_service.inv_frames = False
    mock_sync_service.get_manual_peers.return_value = ["192.168.1.50"]

    class MockDB:
        def __init__(self):
            self.settings = {"sync_manual_peers": json.dumps(["192.168.1.50"])}
        def get_setting(self, key, default=None):
            return self.settings.get(key, default)
        def set_setting(self, key, val):
            self.settings[key] = val

    mock_db = MockDB()
    dialog = SeraSyncDialog(sync_service=mock_sync_service, db=mock_db)

    # Insert a row into table for 192.168.1.50
    dialog.table.setRowCount(1)
    dialog.table.setItem(0, 1, QTableWidgetItem("REMOTE-PC"))
    dialog.table.setItem(0, 2, QTableWidgetItem("192.168.1.50"))

    # Context menu triggers menu exec
    with patch("ui.dialogs.sera_sync_dialog.QMenu") as mock_menu_cls:
        mock_instance = MagicMock()
        mock_instance.exec.return_value = None
        mock_menu_cls.return_value = mock_instance
        dialog._on_table_context_menu(dialog.table.visualItemRect(dialog.table.item(0, 1)).center())
        assert mock_instance.addAction.called

    dialog.close()



