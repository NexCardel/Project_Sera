import time
import pytest
from unittest.mock import MagicMock
from main import SeraApp

class FakeSyncService:
    def __init__(self):
        self.push_to_calls = 0
        self.request_pull_calls = 0
        self.broadcast_tracker_calls = 0
        self.broadcast_audit_calls = 0

    def get_peers(self):
        return [{"ip": "192.168.1.5", "host": "Test-PC"}]

    def push_to(self, *args, **kwargs):
        self.push_to_calls += 1

    def request_pull_from(self, *args, **kwargs):
        self.request_pull_calls += 1

    def broadcast_tracker_dumps(self, *args, **kwargs):
        self.broadcast_tracker_calls += 1

    def broadcast_audit_logs(self, *args, **kwargs):
        self.broadcast_audit_calls += 1

class DummyApp:
    _telemetry_delay = 0.5
    
    def __init__(self):
        import threading
        self.sync_service = FakeSyncService()
        self.db = MagicMock()
        self.db.get_tracker_dumps.return_value = [{"dump": "1"}]
        self.db.get_audit_logs.return_value = [{"log": "1"}]
        self._telemetry_lock = threading.Lock()
        self._telemetry_timer = None
        self._last_telemetry_time = 0.0

def test_write_does_not_push_database():
    win = DummyApp()
    
    # Track call times to verify the gap
    call_times = []
    original_broadcast = win.sync_service.broadcast_tracker_dumps
    def tracking_broadcast(*args, **kwargs):
        call_times.append(time.monotonic())
        original_broadcast(*args, **kwargs)
    win.sync_service.broadcast_tracker_dumps = tracking_broadcast

    # Bind the method from SeraApp to our dummy instance
    method = SeraApp._broadcast_live_update_to_peers.__get__(win, DummyApp)
    
    # Fire 10 calls instantly
    for _ in range(10):
        method()
        
    # Wait for the first immediate thread to run
    time.sleep(0.1)
    
    # Verify that push_to and request_pull_from were NEVER called
    assert win.sync_service.push_to_calls == 0
    assert win.sync_service.request_pull_calls == 0
    
    # Verify the first call fired immediately
    assert win.sync_service.broadcast_tracker_calls == 1
    
    # Wait for the debounce timer to end the first window
    time.sleep(0.5)
    
    # The debounced run should have fired now
    assert win.sync_service.broadcast_tracker_calls == 2
    
    # If we fire again right after the timer, it should NOT fire immediately 
    # since the window was just reset by the timer run setting _last_telemetry_time.
    method()
    time.sleep(0.1)
    assert win.sync_service.broadcast_tracker_calls == 2
    
    # Fire more calls in this new window
    for _ in range(5):
        method()
        
    # End of second window
    time.sleep(0.5)
    assert win.sync_service.broadcast_tracker_calls == 3
    assert win.sync_service.broadcast_audit_calls == 3
    
    # Check that no two sends are closer than the window (with small slack)
    for i in range(1, len(call_times)):
        assert call_times[i] - call_times[i-1] >= DummyApp._telemetry_delay - 0.05


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
    assert metrics["sync_revision"] > 0


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


def test_push_to_streams_snapshot_and_cleans_up(tmp_path):
    import os
    import time
    import security
    import sqlcipher3.dbapi2 as sqlite3
    from database import SeraDatabase
    from sync_peer import SyncPeerService

    sender_dir = tmp_path / "sender"
    receiver_dir = tmp_path / "receiver"
    sender_dir.mkdir()
    receiver_dir.mkdir()

    salt_path = str(sender_dir / "sera.salt")
    security.generate_and_save_salt(salt_path)
    salt = security.load_salt(salt_path)
    hex_key = security.derive_key_hex("testpass123", salt)

    sender_db_path = str(sender_dir / "master.db")
    sender_db = SeraDatabase(sender_db_path, hex_key, defer_startup_maintenance=True)
    pan_col = next((c for c in sender_db.get_mcl_columns() if c["label"].strip().upper() == "PAN"), None)
    pan_id = pan_col["id"] if pan_col else 5
    sender_db.add_client({pan_id: "CCCCC3333C"}, "Stream Test Client", [])

    receiver_db_path = str(receiver_dir / "master.db")
    receiver_salt_path = str(receiver_dir / "sera.salt")
    (receiver_dir / "sera.key").write_text("testpass123", encoding="utf-8")

    sync_received_flag = []
    receiver_service = SyncPeerService(
        db_path=receiver_db_path,
        salt_path=receiver_salt_path,
        username="Receiver",
        sync_port=0,
        on_sync_received=lambda: sync_received_flag.append(True),
    )
    receiver_service.start()

    try:
        sender_service = SyncPeerService(
            db_path=sender_db_path,
            salt_path=salt_path,
            username="Sender",
            db=sender_db,
        )

        res = sender_service.push_to("127.0.0.1", receiver_service._tcp_server.getsockname()[1], force_override=True)
        assert "successfully" in res.lower()

        # Check that temp directory under sender incoming/out/ was deleted in finally
        sender_out_dir = sender_dir / "incoming" / "out"
        if sender_out_dir.exists():
            remaining_files = list(sender_out_dir.iterdir())
            assert remaining_files == []

        time.sleep(0.5)

        # Receiver push was staged (pending_swap.json exists, restart triggered)
        assert (receiver_dir / "incoming" / "pending_swap.json").exists()
        assert len(sync_received_flag) >= 1

        # Swap is applied at startup via apply_pending_swap
        from sync_peer import apply_pending_swap
        assert apply_pending_swap(receiver_dir) is True

        # Receiver DB opens with the same hex_key and has the client
        rec_conn = sqlite3.connect(receiver_db_path)
        try:
            rec_conn.execute(f"PRAGMA key = \"x'{hex_key}'\";")
            integrity = rec_conn.execute("PRAGMA cipher_integrity_check;").fetchall()
            assert integrity == []
            count = rec_conn.execute("SELECT count(*) FROM clients;").fetchone()[0]
            assert count >= 1
        finally:
            rec_conn.close()
    finally:
        receiver_service.stop()


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


def test_push_is_staged_not_live(tmp_path):
    import json
    import time
    import security
    from database import SeraDatabase
    from sync_peer import SyncPeerService

    sender_dir = tmp_path / "sender"
    receiver_dir = tmp_path / "receiver"
    sender_dir.mkdir()
    receiver_dir.mkdir()

    password = "office_password_123"

    # Sender DB & Salt
    sender_salt_path = str(sender_dir / "sera.salt")
    security.generate_and_save_salt(sender_salt_path)
    sender_salt = security.load_salt(sender_salt_path)
    sender_hex = security.derive_key_hex(password, sender_salt)
    sender_db_path = str(sender_dir / "master.db")
    sender_db = SeraDatabase(sender_db_path, sender_hex, defer_startup_maintenance=True)
    pan_col = next((c for c in sender_db.get_mcl_columns() if c["label"].strip().upper() == "PAN"), None)
    pan_id = pan_col["id"] if pan_col else 5
    sender_db.add_client({pan_id: "AAAAA1111A"}, "Sender Client", [])

    # Receiver DB, Salt & sera.key
    receiver_salt_path = str(receiver_dir / "sera.salt")
    security.generate_and_save_salt(receiver_salt_path)
    receiver_salt = security.load_salt(receiver_salt_path)
    receiver_hex = security.derive_key_hex(password, receiver_salt)
    receiver_db_path = str(receiver_dir / "master.db")
    receiver_db = SeraDatabase(receiver_db_path, receiver_hex, defer_startup_maintenance=True)
    receiver_db.add_client({pan_id: "BBBBB2222B"}, "Receiver Client", [])

    # Save password in receiver's sera.key
    (receiver_dir / "sera.key").write_text(password, encoding="utf-8")

    # Record initial receiver live master.db bytes
    initial_receiver_db_bytes = (receiver_dir / "master.db").read_bytes()
    initial_receiver_salt_bytes = (receiver_dir / "sera.salt").read_bytes()

    restart_called = []
    receiver_service = SyncPeerService(
        db_path=receiver_db_path,
        salt_path=receiver_salt_path,
        username="ReceiverUser",
        sync_port=0,
        on_sync_received=lambda: restart_called.append(True),
    )
    receiver_service.start()

    try:
        sender_service = SyncPeerService(
            db_path=sender_db_path,
            salt_path=sender_salt_path,
            username="SenderUser",
            db=sender_db,
        )

        port = receiver_service._tcp_server.getsockname()[1]
        res = sender_service.push_to("127.0.0.1", port, force_override=True)
        assert "successfully" in res.lower()

        time.sleep(0.5)

        # 1. Live master.db bytes are completely unchanged
        assert (receiver_dir / "master.db").read_bytes() == initial_receiver_db_bytes
        assert (receiver_dir / "sera.salt").read_bytes() == initial_receiver_salt_bytes

        # 2. pending_swap.json exists in incoming/
        pending_swap = receiver_dir / "incoming" / "pending_swap.json"
        assert pending_swap.exists()
        swap_info = json.loads(pending_swap.read_text(encoding="utf-8"))
        assert "db" in swap_info
        assert "salt" in swap_info
        assert swap_info["from"] == sender_service.host_name
        assert "at" in swap_info

        # 3. Staged DB and salt exist in incoming/
        staged_db = receiver_dir / "incoming" / "master.db"
        staged_salt = receiver_dir / "incoming" / "sera.salt"
        assert staged_db.exists()
        assert staged_salt.exists()
        assert not (receiver_dir / "incoming" / "master.db.part").exists()
        assert not (receiver_dir / "incoming" / "sera.salt.part").exists()

        # 4. Restart notification callback was called
        assert len(restart_called) >= 1
    finally:
        receiver_service.stop()


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


def test_push_with_other_password_rejected(tmp_path):
    import time
    import security
    from database import SeraDatabase
    from sync_peer import SyncPeerService

    sender_dir = tmp_path / "sender"
    receiver_dir = tmp_path / "receiver"
    sender_dir.mkdir()
    receiver_dir.mkdir()

    sender_password = "sender_secret_password"
    receiver_password = "receiver_different_password"

    # Sender DB & Salt with sender_password
    sender_salt_path = str(sender_dir / "sera.salt")
    security.generate_and_save_salt(sender_salt_path)
    sender_salt = security.load_salt(sender_salt_path)
    sender_hex = security.derive_key_hex(sender_password, sender_salt)
    sender_db_path = str(sender_dir / "master.db")
    sender_db = SeraDatabase(sender_db_path, sender_hex, defer_startup_maintenance=True)
    pan_col = next((c for c in sender_db.get_mcl_columns() if c["label"].strip().upper() == "PAN"), None)
    pan_id = pan_col["id"] if pan_col else 5
    sender_db.add_client({pan_id: "CCCCC5555C"}, "Sender Client", [])

    # Receiver DB & Salt with receiver_password
    receiver_salt_path = str(receiver_dir / "sera.salt")
    security.generate_and_save_salt(receiver_salt_path)
    receiver_salt = security.load_salt(receiver_salt_path)
    receiver_hex = security.derive_key_hex(receiver_password, receiver_salt)
    receiver_db_path = str(receiver_dir / "master.db")
    receiver_db = SeraDatabase(receiver_db_path, receiver_hex, defer_startup_maintenance=True)

    # Save receiver_password in sera.key
    (receiver_dir / "sera.key").write_text(receiver_password, encoding="utf-8")

    receiver_errors = []
    receiver_service = SyncPeerService(
        db_path=receiver_db_path,
        salt_path=receiver_salt_path,
        username="ReceiverUser",
        sync_port=0,
        on_error=lambda msg: receiver_errors.append(msg),
    )
    receiver_service.start()

    try:
        sender_service = SyncPeerService(
            db_path=sender_db_path,
            salt_path=sender_salt_path,
            username="SenderUser",
            db=sender_db,
        )

        port = receiver_service._tcp_server.getsockname()[1]
        res = sender_service.push_to("127.0.0.1", port, force_override=True)

        # Sender gets failure with reason PASSWORD_MISMATCH
        assert "password_mismatch" in res.lower()

        time.sleep(0.5)

        # Receiver staging files are completely deleted/cleaned up
        incoming_dir = receiver_dir / "incoming"
        if incoming_dir.exists():
            assert not (incoming_dir / "master.db").exists()
            assert not (incoming_dir / "sera.salt").exists()
            assert not (incoming_dir / "master.db.part").exists()
            assert not (incoming_dir / "sera.salt.part").exists()
            assert not (incoming_dir / "pending_swap.json").exists()

        # Receiver error callback was notified
        assert any("password mismatch" in str(err).lower() for err in receiver_errors)
    finally:
        receiver_service.stop()


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


def test_push_rejected_on_zero_byte_or_zero_table(tmp_path):
    """Blocking 3 regression: 0-byte payload or 0-table database must be rejected by receiver."""
    import socket
    import json
    import security
    import sqlcipher3.dbapi2 as sqlite3
    from sync_peer import SyncPeerService, _send_framed, _recv_framed

    receiver_dir = tmp_path / "receiver"
    receiver_dir.mkdir()
    receiver_salt_path = str(receiver_dir / "sera.salt")
    security.generate_and_save_salt(receiver_salt_path)
    (receiver_dir / "sera.key").write_text("receiver_pass_123", encoding="utf-8")

    receiver_errors = []
    receiver_service = SyncPeerService(
        db_path=str(receiver_dir / "master.db"),
        salt_path=receiver_salt_path,
        username="ReceiverUser",
        sync_port=0,
        on_error=lambda msg: receiver_errors.append(msg),
    )
    receiver_service.start()

    try:
        port = receiver_service._tcp_server.getsockname()[1]

        # Case 1: Send header with db_size = 0
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", port))
        header = {
            "action": "push_database",
            "host": "TestSender",
            "db_size": 0,
            "salt_size": 16,
            "client_count": 1,
            "sync_revision": 1,
        }
        _send_framed(sock, json.dumps(header).encode("utf-8"))
        resp = json.loads(_recv_framed(sock).decode("utf-8"))
        sock.close()
        assert resp.get("status") == "rejected"
        assert resp.get("reason") == "INVALID_PAYLOAD"

        # Case 2: Send encrypted DB that has 0 tables in sqlite_master
        sender_dir = tmp_path / "sender"
        sender_dir.mkdir()
        sender_salt_path = str(sender_dir / "sera.salt")
        security.generate_and_save_salt(sender_salt_path)
        salt_bytes = security.load_salt(sender_salt_path)
        hex_key = security.derive_key_hex("receiver_pass_123", salt_bytes)

        zero_table_db = sender_dir / "zero_table.db"
        conn = sqlite3.connect(str(zero_table_db))
        conn.execute(f"PRAGMA key = \"x'{hex_key}'\";")
        # Empty DB, create no tables
        conn.execute("VACUUM;")
        conn.close()

        sender_service = SyncPeerService(
            db_path=str(zero_table_db),
            salt_path=sender_salt_path,
            username="SenderZeroTable",
        )
        res = sender_service.push_to("127.0.0.1", port, force_override=True)
        assert "password_mismatch" in res.lower()

        # Receiver staging area must be cleaned up
        incoming_dir = receiver_dir / "incoming"
        if incoming_dir.exists():
            assert not (incoming_dir / "master.db").exists()
            assert not (incoming_dir / "pending_swap.json").exists()
    finally:
        receiver_service.stop()


def test_push_rejected_when_busy(tmp_path):
    """Should Fix 1 regression: Staging lock rejects concurrent transfers with BUSY."""
    import security
    from sync_peer import SyncPeerService
    from database import SeraDatabase

    receiver_dir = tmp_path / "receiver"
    receiver_dir.mkdir()
    receiver_salt_path = str(receiver_dir / "sera.salt")
    security.generate_and_save_salt(receiver_salt_path)
    (receiver_dir / "sera.key").write_text("common_pass_123", encoding="utf-8")

    receiver_service = SyncPeerService(
        db_path=str(receiver_dir / "master.db"),
        salt_path=receiver_salt_path,
        username="ReceiverUser",
        sync_port=0,
    )
    receiver_service.start()

    try:
        sender_dir = tmp_path / "sender"
        sender_dir.mkdir()
        sender_salt_path = str(sender_dir / "sera.salt")
        security.generate_and_save_salt(sender_salt_path)
        salt_bytes = security.load_salt(sender_salt_path)
        hex_key = security.derive_key_hex("common_pass_123", salt_bytes)
        sender_db_path = str(sender_dir / "master.db")
        sender_db = SeraDatabase(sender_db_path, hex_key, defer_startup_maintenance=True)

        sender_service = SyncPeerService(
            db_path=sender_db_path,
            salt_path=sender_salt_path,
            username="SenderUser",
            db=sender_db,
        )

        port = receiver_service._tcp_server.getsockname()[1]

        # Hold the staging lock on receiver to simulate an ongoing transfer
        assert receiver_service._staging_lock.acquire(blocking=False) is True
        try:
            res = sender_service.push_to("127.0.0.1", port, force_override=True)
            assert "busy" in res.lower(), f"Expected busy in response, got {res}"
        finally:
            receiver_service._staging_lock.release()
    finally:
        receiver_service.stop()


def test_push_rejected_when_restart_pending(tmp_path):
    """Worth fixing 1 regression: Incoming push is rejected when pending_swap.json already exists."""
    import json
    import security
    from sync_peer import SyncPeerService
    from database import SeraDatabase

    receiver_dir = tmp_path / "receiver"
    receiver_dir.mkdir()
    incoming_dir = receiver_dir / "incoming"
    incoming_dir.mkdir()

    # Pre-existing accepted swap
    (incoming_dir / "master.db").write_bytes(b"STAGED_A_DB")
    (incoming_dir / "sera.salt").write_bytes(b"STAGED_A_SALT")
    pending_json = incoming_dir / "pending_swap.json"
    pending_json.write_text(json.dumps({"db": "master.db", "salt": "sera.salt", "from": "PC_A"}), encoding="utf-8")

    receiver_salt_path = str(receiver_dir / "sera.salt")
    security.generate_and_save_salt(receiver_salt_path)
    (receiver_dir / "sera.key").write_text("common_pass_123", encoding="utf-8")

    receiver_service = SyncPeerService(
        db_path=str(receiver_dir / "master.db"),
        salt_path=receiver_salt_path,
        username="ReceiverUser",
        sync_port=0,
    )
    receiver_service.start()

    try:
        sender_dir = tmp_path / "sender"
        sender_dir.mkdir()
        sender_salt_path = str(sender_dir / "sera.salt")
        security.generate_and_save_salt(sender_salt_path)
        salt_bytes = security.load_salt(sender_salt_path)
        hex_key = security.derive_key_hex("common_pass_123", salt_bytes)
        sender_db_path = str(sender_dir / "master.db")
        sender_db = SeraDatabase(sender_db_path, hex_key, defer_startup_maintenance=True)

        sender_service = SyncPeerService(
            db_path=sender_db_path,
            salt_path=sender_salt_path,
            username="SenderUser",
            db=sender_db,
        )

        port = receiver_service._tcp_server.getsockname()[1]
        res = sender_service.push_to("127.0.0.1", port, force_override=True)
        assert "restart_pending" in res.lower(), f"Expected restart_pending in response, got {res}"

        # Crucial: Pre-existing accepted staged files and pending_swap.json are untouched!
        assert (incoming_dir / "master.db").read_bytes() == b"STAGED_A_DB"
        assert (incoming_dir / "pending_swap.json").exists()
    finally:
        receiver_service.stop()


def test_raw_db_reset_is_reported(tmp_path):
    import os
    import glob
    import security
    import sqlcipher3.dbapi2 as sqlite3
    from database import SeraDatabase

    db_path = str(tmp_path / "master.db")
    raw_db_path = str(tmp_path / "rawPayload.db")
    salt_path = str(tmp_path / "sera.salt")
    password = "testpass123"

    # Create salt and master.db
    security.generate_and_save_salt(salt_path)
    salt = security.load_salt(salt_path)
    hex_key = security.derive_key_hex(password, salt)

    db = SeraDatabase(db_path, hex_key, raw_db_path=raw_db_path, defer_startup_maintenance=True)

    # Verify rawPayload.db was created and accessible
    assert os.path.exists(raw_db_path)
    assert os.path.getsize(raw_db_path) > 0

    # Now corrupt the rawPayload.db by truncating it
    with open(raw_db_path, "wb") as f:
        f.write(b"CORRUPTED_DATA_NOT_A_REAL_DB")

    # Close the first db instance to avoid locking issues
    del db

    # Now create a new SeraDatabase instance - it should auto-heal
    db2 = SeraDatabase(db_path, hex_key, raw_db_path=raw_db_path, defer_startup_maintenance=True)

    # 1. Check that raw_db_was_reset was set
    assert hasattr(db2, "raw_db_was_reset"), "SeraDatabase should have raw_db_was_reset attribute"
    assert db2.raw_db_was_reset is not None, "raw_db_was_reset should be set when auto-heal happens"
    backup_name = db2.raw_db_was_reset

    # 2. Verify backup file exists and contains the corrupted data
    assert os.path.exists(backup_name), f"Backup file {backup_name} should exist"
    with open(backup_name, "rb") as f:
        backup_content = f.read()
    assert backup_content == b"CORRUPTED_DATA_NOT_A_REAL_DB", "Backup should contain the corrupted data"

    # 3. Verify rawPayload.db is now a valid database
    try:
        conn = sqlite3.connect(raw_db_path)
        conn.execute(f"PRAGMA key = \"x'{hex_key}'\";")
        count = conn.execute("SELECT count(*) FROM sqlite_master;").fetchone()[0]
        conn.close()
    except Exception as e:
        raise AssertionError(f"rawPayload.db should be a valid database after auto-heal: {e}")

    # 4. Verify audit log entry was written
    with db2._connect() as conn:
        rows = conn.execute(
            "SELECT action, detail FROM audit_log WHERE action = 'raw_db_reset' ORDER BY id DESC LIMIT 1"
        ).fetchall()

    assert len(rows) > 0, "An audit log entry with action='raw_db_reset' should exist"
    action, detail = rows[0]
    assert action == "raw_db_reset"
    backup_basename = os.path.basename(backup_name)
    assert backup_basename in detail, f"Audit detail should mention the backup name: {detail}"


def test_wrong_saved_password_prompts(tmp_path, monkeypatch):
    import os
    import security
    from database import SeraDatabase
    from main import SeraApp

    # 1. Part A: A missing DB -> no DB file created by _get_master_password
    missing_db_dir = tmp_path / "missing_db_env"
    missing_db_dir.mkdir()
    monkeypatch.setattr("main.APP_DIR", missing_db_dir)

    class StubApp:
        _verify_master_password = SeraApp._verify_master_password

        def __init__(self, folder):
            self.db_path = str(folder / "master.db")
            self.salt_path = str(folder / security.SALT_FILE)
            self.prompt_calls = []

        def _prompt_master_password(self, prompt_text="Enter Master Password:"):
            self.prompt_calls.append(prompt_text)
            return "some_pass"

    stub_missing = StubApp(missing_db_dir)
    res_missing = SeraApp._get_master_password(stub_missing)

    assert not (missing_db_dir / "master.db").exists(), "A missing DB must NOT be created by _get_master_password"
    assert res_missing == "", "_get_master_password should return empty string when DB is missing"
    assert len(stub_missing.prompt_calls) == 0, "No prompt should be shown when DB is missing"

    # 2. Part B: A wrong sera.key plus a correct prompt answer -> returns the right password and rewrites sera.key
    valid_db_dir = tmp_path / "valid_db_env"
    valid_db_dir.mkdir()
    monkeypatch.setattr("main.APP_DIR", valid_db_dir)

    correct_password = "correct_office_pass_123"
    wrong_password = "wrong_saved_password_456"

    salt_path = str(valid_db_dir / security.SALT_FILE)
    security.generate_and_save_salt(salt_path)
    salt = security.load_salt(salt_path)
    hex_key = security.derive_key_hex(correct_password, salt)

    db_path = str(valid_db_dir / "master.db")
    db = SeraDatabase(db_path, hex_key, defer_startup_maintenance=True)
    del db  # close connection

    # Write wrong password to sera.key
    key_file = valid_db_dir / "sera.key"
    key_file.write_text(wrong_password, encoding="utf-8")

    stub_valid = StubApp(valid_db_dir)
    stub_valid._prompt_master_password = lambda prompt_text="": (
        stub_valid.prompt_calls.append(prompt_text) or correct_password
    )

    res_valid = SeraApp._get_master_password(stub_valid)

    assert res_valid == correct_password, "Should return the correct password"
    assert key_file.read_text(encoding="utf-8").strip() == correct_password, "sera.key should be rewritten with correct password"
    assert len(stub_valid.prompt_calls) == 1, "Prompt should have been shown once"
    expected_prompt_text = "This PC's saved password doesn't open the office database. Enter the office master password."
    assert expected_prompt_text in stub_valid.prompt_calls[0], f"Prompt text should match spec: {stub_valid.prompt_calls[0]}"

    # 3. Part C: Correct saved password in sera.key -> opens DB without prompt
    stub_correct = StubApp(valid_db_dir)
    res_correct = SeraApp._get_master_password(stub_correct)
    assert res_correct == correct_password
    assert len(stub_correct.prompt_calls) == 0, "Should not prompt when saved password is valid"

    # 4. Part D: Prompt gives wrong password up to 3 tries, then returns empty and does NOT rewrite sera.key
    key_file.write_text("another_wrong_pass", encoding="utf-8")
    stub_fail = StubApp(valid_db_dir)
    stub_fail._prompt_master_password = lambda prompt_text="": (
        stub_fail.prompt_calls.append(prompt_text) or "still_wrong_pass"
    )
    res_fail = SeraApp._get_master_password(stub_fail)
    assert res_fail == "", "Should return empty string after 3 failed attempts"
    assert len(stub_fail.prompt_calls) == 3, f"Should attempt prompt up to 3 times, got {len(stub_fail.prompt_calls)}"
    assert key_file.read_text(encoding="utf-8").strip() == "another_wrong_pass", "sera.key must not be overwritten with failing password"

    # 5. Part E: Prompt cancelled on first try -> returns empty immediately without 3 prompts
    key_file.write_text("another_wrong_pass", encoding="utf-8")
    stub_cancel = StubApp(valid_db_dir)
    stub_cancel._prompt_master_password = lambda prompt_text="": (
        stub_cancel.prompt_calls.append(prompt_text) or ""
    )
    res_cancel = SeraApp._get_master_password(stub_cancel)
    assert res_cancel == ""
    assert len(stub_cancel.prompt_calls) == 1, "Should exit immediately on cancel without looping"

    # 6. Part F: Default password admin123 tried when master.db exists and sera.key does not
    default_db_dir = tmp_path / "default_db_env"
    default_db_dir.mkdir()
    default_salt_path = str(default_db_dir / security.SALT_FILE)
    security.generate_and_save_salt(default_salt_path)
    default_salt = security.load_salt(default_salt_path)
    default_hex = security.derive_key_hex("admin123", default_salt)
    default_db_path = str(default_db_dir / "master.db")
    default_db = SeraDatabase(default_db_path, default_hex, defer_startup_maintenance=True)
    del default_db

    stub_default = StubApp(default_db_dir)
    res_default = SeraApp._get_master_password(stub_default)
    assert res_default == "admin123", "admin123 should be tried and accepted for existing DB"
    assert len(stub_default.prompt_calls) == 0, "No prompt needed when admin123 opens DB"
    assert (default_db_dir / "sera.key").read_text(encoding="utf-8").strip() == "admin123", "sera.key should be saved with admin123"

    # 7. Part G: master.db exists but sera.salt is missing -> MISSING_SALT, no prompts
    missing_salt_dir = tmp_path / "missing_salt_env"
    missing_salt_dir.mkdir()
    (missing_salt_dir / "master.db").write_bytes(b"EXISTING_DB_BYTES")
    (missing_salt_dir / "sera.key").write_text("some_key", encoding="utf-8")
    stub_missing_salt = StubApp(missing_salt_dir)
    res_missing_salt = SeraApp._get_master_password(stub_missing_salt)
    assert res_missing_salt == ""
    assert getattr(stub_missing_salt, "_last_auth_error", None) == "MISSING_SALT"
    assert len(stub_missing_salt.prompt_calls) == 0, "Must not prompt when salt is missing"

    # 8. Part H: Database locked -> sets DATABASE_LOCKED and halts without 3 prompts
    locked_db_dir = tmp_path / "locked_db_env"
    locked_db_dir.mkdir()
    locked_salt_path = str(locked_db_dir / security.SALT_FILE)
    security.generate_and_save_salt(locked_salt_path)
    (locked_db_dir / "master.db").write_bytes(b"LOCKED_DB_BYTES")
    (locked_db_dir / "sera.key").write_text("saved_pass", encoding="utf-8")
    stub_locked = StubApp(locked_db_dir)

    import sqlcipher3.dbapi2 as sqlite3
    def failing_connect(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(sqlite3, "connect", failing_connect)

    res_locked = SeraApp._get_master_password(stub_locked)
    assert res_locked == ""
    assert getattr(stub_locked, "_last_auth_error", None) == "DATABASE_LOCKED"
    assert len(stub_locked.prompt_calls) == 0, "Must not repeatedly prompt for password when DB is locked"

    # 9. Part I: _show_startup_auth_error shows QMessageBox.critical for errors and suppresses on cancel
    dialog_calls = []
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "critical", lambda parent, title, msg: dialog_calls.append((title, msg)))

    stub_valid._last_auth_error = "MISSING_SALT"
    SeraApp._show_startup_auth_error(stub_valid)
    assert len(dialog_calls) == 1
    assert "Missing Salt" in dialog_calls[-1][0]

    stub_valid._last_auth_error = "DATABASE_LOCKED"
    SeraApp._show_startup_auth_error(stub_valid)
    assert len(dialog_calls) == 2
    assert "Database Locked" in dialog_calls[-1][0]

    stub_valid._last_auth_error = "WRONG_PASSWORD"
    SeraApp._show_startup_auth_error(stub_valid)
    assert len(dialog_calls) == 3
    assert "Authentication Failed" in dialog_calls[-1][0]

    stub_valid._last_auth_error = "CANCELLED"
    SeraApp._show_startup_auth_error(stub_valid)
    assert len(dialog_calls) == 3, "CANCELLED must not show a critical error dialog"
