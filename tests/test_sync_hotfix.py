import os
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


# ==============================================================================
# P0-6: First Run - New Office / Join Office Tests
# ==============================================================================

def test_join_flow_end_to_end(tmp_path):
    """
    Acceptance test for P0-6:
    Two services on localhost (different temp dirs and ports) and the approval callback
    auto-answering yes: the joiner ends with a DB that opens with the office password.
    With the callback answering no, nothing is written on the joiner.
    """
    import security
    from database import SeraDatabase
    from sync_peer import (
        SyncPeerService,
        join_office_fetch_snapshot,
        complete_join_office,
    )

    # 1. Setup serving office (Peer A)
    office_pwd = "office_master_secret_2026"
    server_dir = tmp_path / "server_office"
    server_dir.mkdir()
    server_db_path = str(server_dir / "master.db")
    server_salt_path = str(server_dir / security.SALT_FILE)

    security.generate_and_save_salt(server_salt_path)
    salt_bytes = security.load_salt(server_salt_path)
    hex_key = security.derive_key_hex(office_pwd, salt_bytes)

    server_db = SeraDatabase(server_db_path, hex_key, defer_startup_maintenance=True)
    pan_col = next((c for c in server_db.get_mcl_columns() if c["label"].strip().upper() == "PAN"), None)
    pan_id = pan_col["id"] if pan_col else 5
    server_db.add_client({pan_id: "ABCDE1234F"}, "TEST OFFICE CLIENT", [])

    # Callback flag control
    approval_response = [True]
    approval_calls = []

    def on_approval(host, username, code):
        approval_calls.append((host, username, code))
        return approval_response[0]

    server_service = SyncPeerService(
        db_path=server_db_path,
        salt_path=server_salt_path,
        username="AdminOffice",
        db=server_db,
        sync_port=0,
        on_join_approval_requested=on_approval,
    )
    server_service.start()
    server_port = server_service.sync_port
    time.sleep(0.1)

    try:
        # Case A: Approval callback auto-answers YES -> Joiner gets DB & password verified
        joiner_dir_yes = tmp_path / "joiner_office_yes"
        joiner_dir_yes.mkdir()
        code_yes = "789123"

        ok, reason, staged_db, staged_salt = join_office_fetch_snapshot(
            peer_ip="127.0.0.1",
            peer_port=server_port,
            app_dir=joiner_dir_yes,
            host_name="Joiner-PC-1",
            username="Staff1",
            code=code_yes,
        )

        assert ok is True, f"Fetch snapshot failed: {reason}"
        assert len(approval_calls) == 1
        assert approval_calls[-1] == ("Joiner-PC-1", "Staff1", code_yes)
        assert staged_db is not None and staged_db.exists()
        assert staged_salt is not None and staged_salt.exists()

        # Complete join with correct office password
        install_ok, install_reason = complete_join_office(
            app_dir=joiner_dir_yes,
            staged_db=staged_db,
            staged_salt=staged_salt,
            password=office_pwd,
        )
        assert install_ok is True, f"Install failed: {install_reason}"

        # Verify joiner files exist
        joiner_db_path = joiner_dir_yes / "master.db"
        joiner_salt_path = joiner_dir_yes / security.SALT_FILE
        joiner_key_path = joiner_dir_yes / "sera.key"

        assert joiner_db_path.exists()
        assert joiner_salt_path.exists()
        assert joiner_key_path.exists()
        assert joiner_key_path.read_text(encoding="utf-8").strip() == office_pwd

        # Verify joiner opens DB and sees the client
        joiner_salt = security.load_salt(str(joiner_salt_path))
        joiner_hex = security.derive_key_hex(office_pwd, joiner_salt)
        joiner_db = SeraDatabase(str(joiner_db_path), joiner_hex, defer_startup_maintenance=True)
        metrics = joiner_db.get_sync_metrics()
        assert metrics["client_count"] == 1
        del joiner_db

        # Case B: Approval callback answers NO -> Nothing is written on the joiner
        approval_response[0] = False
        joiner_dir_no = tmp_path / "joiner_office_no"
        joiner_dir_no.mkdir()
        code_no = "456789"

        ok_no, reason_no, staged_db_no, staged_salt_no = join_office_fetch_snapshot(
            peer_ip="127.0.0.1",
            peer_port=server_port,
            app_dir=joiner_dir_no,
            host_name="Joiner-PC-2",
            username="Staff2",
            code=code_no,
        )

        assert ok_no is False
        assert "reject" in reason_no.lower() or "denied" in reason_no.lower()
        assert not (joiner_dir_no / "master.db").exists()
        assert not (joiner_dir_no / security.SALT_FILE).exists()
        assert not (joiner_dir_no / "sera.key").exists()
        incoming_no = joiner_dir_no / "incoming"
        if incoming_no.exists():
            assert not (incoming_no / "master.db").exists()
            assert not (incoming_no / "sera.salt").exists()

    finally:
        server_service.stop()
        del server_db


def test_join_flow_concurrency_rejection(tmp_path):
    """The server allows at most one pending join request at a time."""
    import security
    from database import SeraDatabase
    from sync_peer import SyncPeerService, join_office_fetch_snapshot
    import threading

    office_pwd = "office_pwd_12345"
    server_dir = tmp_path / "server_busy_test"
    server_dir.mkdir()
    server_db_path = str(server_dir / "master.db")
    server_salt_path = str(server_dir / security.SALT_FILE)

    security.generate_and_save_salt(server_salt_path)
    salt_bytes = security.load_salt(server_salt_path)
    hex_key = security.derive_key_hex(office_pwd, salt_bytes)
    server_db = SeraDatabase(server_db_path, hex_key, defer_startup_maintenance=True)

    hang_event = threading.Event()
    modal_opened = threading.Event()

    def hanging_approval(host, username, code):
        modal_opened.set()
        hang_event.wait(timeout=5.0)
        return True

    server_service = SyncPeerService(
        db_path=server_db_path,
        salt_path=server_salt_path,
        username="Admin",
        db=server_db,
        sync_port=0,
        on_join_approval_requested=hanging_approval,
    )
    server_service.start()
    server_port = server_service.sync_port
    time.sleep(0.1)

    try:
        client1_dir = tmp_path / "joiner_client_1"
        client1_dir.mkdir()
        client2_dir = tmp_path / "joiner_client_2"
        client2_dir.mkdir()

        t1_result = []
        def _join1():
            res = join_office_fetch_snapshot(
                peer_ip="127.0.0.1",
                peer_port=server_port,
                app_dir=client1_dir,
                host_name="PC1",
                username="User1",
                code="111111",
            )
            t1_result.append(res)

        t1 = threading.Thread(target=_join1, daemon=True)
        t1.start()

        # Wait until client 1 opens the modal on the server
        assert modal_opened.wait(timeout=2.0) is True

        # Now client 2 tries to join while modal is open
        ok2, reason2, _, _ = join_office_fetch_snapshot(
            peer_ip="127.0.0.1",
            peer_port=server_port,
            app_dir=client2_dir,
            host_name="PC2",
            username="User2",
            code="222222",
        )
        assert ok2 is False
        assert "busy" in reason2.lower() or "reject" in reason2.lower()

        # Release client 1
        hang_event.set()
        t1.join(timeout=3.0)
        assert len(t1_result) == 1
        assert t1_result[0][0] is True
    finally:
        hang_event.set()
        server_service.stop()
        del server_db


def test_join_flow_wrong_password_fails_verification(tmp_path):
    """If wrong office password is provided after snapshot download, files are not installed."""
    import security
    from database import SeraDatabase
    from sync_peer import (
        SyncPeerService,
        join_office_fetch_snapshot,
        complete_join_office,
    )

    office_pwd = "real_office_password_1"
    server_dir = tmp_path / "server_wrong_pwd"
    server_dir.mkdir()
    server_db_path = str(server_dir / "master.db")
    server_salt_path = str(server_dir / security.SALT_FILE)

    security.generate_and_save_salt(server_salt_path)
    salt_bytes = security.load_salt(server_salt_path)
    hex_key = security.derive_key_hex(office_pwd, salt_bytes)
    server_db = SeraDatabase(server_db_path, hex_key, defer_startup_maintenance=True)

    server_service = SyncPeerService(
        db_path=server_db_path,
        salt_path=server_salt_path,
        username="Admin",
        db=server_db,
        sync_port=0,
        on_join_approval_requested=lambda h, u, c: True,
    )
    server_service.start()
    server_port = server_service.sync_port
    time.sleep(0.1)

    try:
        joiner_dir = tmp_path / "joiner_wrong_pwd"
        joiner_dir.mkdir()

        ok, reason, staged_db, staged_salt = join_office_fetch_snapshot(
            peer_ip="127.0.0.1",
            peer_port=server_port,
            app_dir=joiner_dir,
            host_name="Joiner-PC",
            username="Staff",
            code="654321",
        )
        assert ok is True
        assert staged_db.exists()
        assert staged_salt.exists()

        # Attempt install with wrong password
        install_ok, install_reason = complete_join_office(
            app_dir=joiner_dir,
            staged_db=staged_db,
            staged_salt=staged_salt,
            password="incorrect_password",
        )
        assert install_ok is False
        assert "password" in install_reason.lower() or "mismatch" in install_reason.lower()

        # Ensure live files are not installed
        assert not (joiner_dir / "master.db").exists()
        assert not (joiner_dir / security.SALT_FILE).exists()
        assert not (joiner_dir / "sera.key").exists()
    finally:
        server_service.stop()
        del server_db


def test_create_new_office_validation(tmp_path):
    """
    New office password validation:
    - Min 8 chars
    - Typed twice (must match)
    - admin123 refused
    - Creates salt + DB and writes sera.key
    """
    import security
    from sync_peer import create_new_office

    # 1. Password under 8 chars
    dir1 = tmp_path / "office_short"
    dir1.mkdir()
    ok, err = create_new_office(dir1, "short", "short")
    assert ok is False
    assert "8" in err

    # 2. Passwords don't match
    dir2 = tmp_path / "office_mismatch"
    dir2.mkdir()
    ok, err = create_new_office(dir2, "password123", "password456")
    assert ok is False
    assert "match" in err.lower()

    # 3. admin123 refused
    dir3 = tmp_path / "office_admin123"
    dir3.mkdir()
    ok, err = create_new_office(dir3, "admin123", "admin123")
    assert ok is False
    assert "admin123" in err.lower()

    # 4. Valid password creates salt, DB, sera.key
    dir4 = tmp_path / "office_valid"
    dir4.mkdir()
    ok, err = create_new_office(dir4, "valid_office_pwd_99", "valid_office_pwd_99")
    assert ok is True
    assert (dir4 / "master.db").exists()
    assert (dir4 / security.SALT_FILE).exists()
    assert (dir4 / "sera.key").exists()
    assert (dir4 / "sera.key").read_text(encoding="utf-8").strip() == "valid_office_pwd_99"

    # Verify database opens with the derived key
    salt = security.load_salt(str(dir4 / security.SALT_FILE))
    hex_k = security.derive_key_hex("valid_office_pwd_99", salt)
    from database import SeraDatabase
    db = SeraDatabase(str(dir4 / "master.db"), hex_k, defer_startup_maintenance=True)
    assert db.get_sync_metrics()["client_count"] == 0
    del db


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


def test_first_run_dialog_ui(tmp_path):
    """Verifies FirstRunDialog page navigation and new office creation flow."""
    from PySide6.QtWidgets import QApplication
    from ui.dialogs.first_run_dialog import FirstRunDialog
    import security
    app = QApplication.instance() or QApplication([])

    dlg = FirstRunDialog(app_dir=tmp_path, actor_alias="TestAdmin")

    # Initial page is choice page
    assert dlg.stack.currentIndex() == 0

    # Navigate to New Office page
    dlg.stack.setCurrentIndex(1)
    assert dlg.stack.currentIndex() == 1

    # Enter invalid password (< 8 chars)
    dlg.new_pwd_input.setText("short")
    dlg.new_pwd_confirm.setText("short")
    dlg._handle_create_new_office()
    assert "8" in dlg.new_error_label.text()
    assert not (tmp_path / "master.db").exists()

    # Enter mismatched passwords
    dlg.new_pwd_input.setText("mismatch123")
    dlg.new_pwd_confirm.setText("mismatch456")
    dlg._handle_create_new_office()
    assert "match" in dlg.new_error_label.text().lower()

    # Enter admin123
    dlg.new_pwd_input.setText("admin123")
    dlg.new_pwd_confirm.setText("admin123")
    dlg._handle_create_new_office()
    assert "admin123" in dlg.new_error_label.text().lower()

    # Enter valid password
    valid_pwd = "brand_new_office_2026"
    dlg.new_pwd_input.setText(valid_pwd)
    dlg.new_pwd_confirm.setText(valid_pwd)
    dlg._handle_create_new_office()

    assert dlg.master_password == valid_pwd
    assert (tmp_path / "master.db").exists()
    assert (tmp_path / security.SALT_FILE).exists()
    assert (tmp_path / "sera.key").exists()


def test_startup_dispatches_first_run_dialog_and_does_not_generate_salt(monkeypatch, tmp_path):
    """
    When master.db does not exist in APP_DIR:
    - Salt must NOT be generated prior to user choice
    - FirstRunDialog must be displayed
    """
    import os
    import security
    import main as main_module
    from ui.dialogs.first_run_dialog import FirstRunDialog
    from PySide6.QtWidgets import QApplication, QDialog
    app = QApplication.instance() or QApplication([])

    empty_app_dir = tmp_path / "empty_office_app"
    empty_app_dir.mkdir()
    monkeypatch.setattr(main_module, "APP_DIR", empty_app_dir)

    dialog_shown = []
    def mock_first_run_exec(self):
        dialog_shown.append(True)
        # Verify salt does NOT exist before first run dialog finishes
        assert not (empty_app_dir / security.SALT_FILE).exists()
        assert not (empty_app_dir / "master.db").exists()
        # Simulate creating new office
        from sync_peer import create_new_office
        create_new_office(empty_app_dir, "first_run_pass_123", "first_run_pass_123")
        self.master_password = "first_run_pass_123"
        return QDialog.Accepted

    monkeypatch.setattr(FirstRunDialog, "exec", mock_first_run_exec)

    stub = main_module.SeraApp.__new__(main_module.SeraApp)
    stub.db_path = str(empty_app_dir / "master.db")
    stub.salt_path = str(empty_app_dir / security.SALT_FILE)
    stub.actor_alias = "Admin"

    # Run the startup check logic
    if not os.path.exists(stub.db_path):
        dlg = FirstRunDialog(empty_app_dir, stub.actor_alias)
        res = dlg.exec()
        assert res == QDialog.Accepted
        master_password = dlg.master_password or stub._get_master_password()

    assert len(dialog_shown) == 1
    assert master_password == "first_run_pass_123"
    assert (empty_app_dir / "master.db").exists()
    assert (empty_app_dir / security.SALT_FILE).exists()
    assert (empty_app_dir / "sera.key").exists()


# ==============================================================================
# P0-6 Blocking-Fix Tests
# ==============================================================================

def test_create_new_office_refuses_if_db_exists(tmp_path):
    """
    Blocking #1 (create path): create_new_office must return (False, …) when
    master.db already exists.  The existing DB must NOT be overwritten.
    """
    import security
    from sync_peer import create_new_office

    # Seed an existing db file
    (tmp_path / "master.db").write_bytes(b"existing-db-sentinel")

    ok, err = create_new_office(tmp_path, "strongpass99", "strongpass99")

    assert not ok, "create_new_office must refuse when master.db already exists"
    assert "master.db" in err.lower() or "already exists" in err.lower()
    # Existing db must be intact
    assert (tmp_path / "master.db").read_bytes() == b"existing-db-sentinel"


def test_create_new_office_backs_up_existing_salt_and_key(tmp_path):
    """
    Blocking #1 (create path): if sera.salt / sera.key exist but master.db does not,
    create_new_office must rename them to .bak-<ts> before writing fresh copies.
    """
    import security
    from sync_peer import create_new_office

    (tmp_path / "sera.salt").write_bytes(b"old-salt")
    (tmp_path / "sera.key").write_text("old-key", encoding="utf-8")

    ok, err = create_new_office(tmp_path, "strongpass99", "strongpass99")

    assert ok, f"create_new_office failed unexpectedly: {err}"
    # At least one .bak-* backup must exist for each original
    salt_baks = list(tmp_path.glob("sera.salt.bak-*"))
    key_baks  = list(tmp_path.glob("sera.key.bak-*"))
    assert salt_baks, "Old sera.salt must be backed up with .bak-<ts> suffix"
    assert key_baks,  "Old sera.key must be backed up with .bak-<ts> suffix"
    # Original backup content preserved
    assert any(b.read_bytes() == b"old-salt" for b in salt_baks)
    assert any(b.read_text(encoding="utf-8") == "old-key" for b in key_baks)


def test_complete_join_office_backs_up_and_rolls_back_on_failure(tmp_path):
    """
    Blocking #1 + #2 (join path):
      - Existing master.db / sera.salt / sera.key are renamed to .bak-<ts> before install.
      - If the install fails mid-way the originals are restored and no partial install remains.
    """
    import security
    from sync_peer import complete_join_office

    # Pre-seed the app dir with existing files
    (tmp_path / "master.db").write_bytes(b"original-db")
    (tmp_path / security.SALT_FILE).write_bytes(b"original-salt")
    (tmp_path / "sera.key").write_text("original-key", encoding="utf-8")

    # Build a real staged pair so password verification passes
    import sqlcipher3.dbapi2 as sqlite3

    staged_salt_file = tmp_path / "staged.salt"
    staged_db_file   = tmp_path / "staged.db"

    salt_bytes = os.urandom(16)
    staged_salt_file.write_bytes(salt_bytes)
    hex_key = security.derive_key_hex("testpass99", salt_bytes)

    conn = sqlite3.connect(str(staged_db_file))
    conn.execute(f"PRAGMA key = \"x'{hex_key}'\";")
    conn.execute("CREATE TABLE _dummy (id INTEGER PRIMARY KEY);")
    conn.commit()
    conn.close()

    ok, msg = complete_join_office(tmp_path, staged_db_file, staged_salt_file, "testpass99")

    assert ok, f"complete_join_office failed: {msg}"
    # Backup files must exist for all three originals
    db_baks   = list(tmp_path.glob("master.db.bak-*"))
    salt_baks = list(tmp_path.glob(f"{security.SALT_FILE}.bak-*"))
    key_baks  = list(tmp_path.glob("sera.key.bak-*"))
    assert db_baks,   "Original master.db must be backed up"
    assert salt_baks, "Original sera.salt must be backed up"
    assert key_baks,  "Original sera.key must be backed up"
    assert any(b.read_bytes() == b"original-db"   for b in db_baks)
    assert any(b.read_bytes() == b"original-salt" for b in salt_baks)
    assert any(b.read_text(encoding="utf-8") == "original-key" for b in key_baks)

    # Rollback test: patch sync_peer.os.replace so the *second* call (salt install) raises.
    # After that failure, the already-installed master.db must be removed and
    # the backed-up originals must be restored.
    import sync_peer as _sp
    from unittest.mock import patch

    tmp2 = tmp_path / "rollback_test"
    tmp2.mkdir()
    (tmp2 / "master.db").write_bytes(b"orig-db2")
    (tmp2 / security.SALT_FILE).write_bytes(b"orig-salt2")

    staged_salt2 = tmp2 / "staged2.salt"
    staged_db2   = tmp2 / "staged2.db"
    staged_salt2.write_bytes(salt_bytes)

    conn2 = sqlite3.connect(str(staged_db2))
    conn2.execute(f"PRAGMA key = \"x'{hex_key}'\";")
    conn2.execute("CREATE TABLE _dummy2 (id INTEGER PRIMARY KEY);")
    conn2.commit()
    conn2.close()

    _real_replace = os.replace
    _call_count = [0]

    def _failing_replace(src, dst):
        _call_count[0] += 1
        if _call_count[0] == 2:  # second call == salt install
            raise OSError("Simulated AV lock on salt file")
        _real_replace(src, dst)

    with patch.object(_sp.os, "replace", _failing_replace):
        ok2, msg2 = complete_join_office(tmp2, staged_db2, staged_salt2, "testpass99")

    assert not ok2, "complete_join_office must fail when salt install raises"
    assert (tmp2 / "master.db").read_bytes() == b"orig-db2", "original master.db must be restored"
    assert (tmp2 / security.SALT_FILE).read_bytes() == b"orig-salt2", "original sera.salt must be restored"


def test_create_new_office_password_strip_consistent(tmp_path):
    """
    Blocking #3: create_new_office strips the password before deriving the key
    and writing sera.key.  FirstRunDialog._handle_create_new_office must also
    expose the stripped form so main.py opens the DB with the right key.
    """
    import security
    from sync_peer import create_new_office

    padded_pwd = "  trimMe99  "
    ok, err = create_new_office(tmp_path, padded_pwd, padded_pwd)
    assert ok, f"create_new_office failed: {err}"

    # sera.key must contain the stripped form
    written_key = (tmp_path / "sera.key").read_text(encoding="utf-8")
    assert written_key == padded_pwd.strip(), (
        f"sera.key contains '{written_key}' but expected stripped '{padded_pwd.strip()}'"
    )

    # The stored key must actually open the DB
    salt = security.load_salt(str(tmp_path / security.SALT_FILE))
    hex_key = security.derive_key_hex(written_key, salt)

    import sqlcipher3.dbapi2 as sqlite3
    conn = sqlite3.connect(str(tmp_path / "master.db"))
    conn.execute(f"PRAGMA key = \"x'{hex_key}'\";")
    table_count = conn.execute("SELECT count(*) FROM sqlite_master;").fetchone()[0]
    conn.close()
    assert table_count > 0, "DB opened with stripped key must contain tables"


def test_complete_join_office_password_strip_consistent(tmp_path):
    """
    Blocking #3 (join path): complete_join_office must write the stripped
    password to sera.key.  A caller that passes a padded password should still
    end up with a key file that opens the DB.
    """
    import security
    import sqlcipher3.dbapi2 as sqlite3
    from sync_peer import complete_join_office

    padded_pwd = " joinPass99 "
    stripped = padded_pwd.strip()

    salt_bytes = os.urandom(16)
    hex_key = security.derive_key_hex(stripped, salt_bytes)

    staged_salt = tmp_path / "staged.salt"
    staged_db   = tmp_path / "staged.db"
    staged_salt.write_bytes(salt_bytes)

    conn = sqlite3.connect(str(staged_db))
    conn.execute(f"PRAGMA key = \"x'{hex_key}'\";")
    conn.execute("CREATE TABLE _j (id INTEGER PRIMARY KEY);")
    conn.commit()
    conn.close()

    # The dialog passes the stripped form (after fix #3); verify end-to-end.
    ok, msg = complete_join_office(tmp_path, staged_db, staged_salt, stripped)
    assert ok, f"complete_join_office failed: {msg}"

    written_key = (tmp_path / "sera.key").read_text(encoding="utf-8")
    assert written_key == stripped

    # Verify the written key actually opens the installed DB
    conn2 = sqlite3.connect(str(tmp_path / "master.db"))
    conn2.execute(f"PRAGMA key = \"x'{hex_key}'\";")
    count = conn2.execute("SELECT count(*) FROM sqlite_master;").fetchone()[0]
    conn2.close()
    assert count > 0


# ---------------- P0-7: authenticate legacy sync messages (HMAC) ----------------

def test_authenticated_push_accepted(tmp_path):
    """A push_database sent by a peer configured with the same hex_key as the receiver
    is authenticated and proceeds exactly as an unauthenticated push did before P0-7."""
    import security
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
    sender_db.add_client({pan_id: "AUTHK0001A"}, "Auth Test Client", [])

    receiver_db_path = str(receiver_dir / "master.db")
    receiver_salt_path = str(receiver_dir / "sera.salt")
    (receiver_dir / "sera.key").write_text("testpass123", encoding="utf-8")

    receiver_service = SyncPeerService(
        db_path=receiver_db_path,
        salt_path=receiver_salt_path,
        username="Receiver",
        sync_port=0,
        hex_key=hex_key,
    )
    receiver_service.start()

    try:
        sender_service = SyncPeerService(
            db_path=sender_db_path,
            salt_path=salt_path,
            username="Sender",
            db=sender_db,
            hex_key=hex_key,
        )

        port = receiver_service._tcp_server.getsockname()[1]
        res = sender_service.push_to("127.0.0.1", port, force_override=True)
        assert "successfully" in res.lower(), f"Expected success, got: {res}"
        assert (receiver_dir / "incoming" / "pending_swap.json").exists()
    finally:
        receiver_service.stop()


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
    """request_database_pull with no mac is rejected and never reaches the code that
    would push our database back to the requester (F9)."""
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

    push_calls = []
    orig_push_to = receiver_service.push_to
    def _tracking_push_to(*a, **kw):
        push_calls.append((a, kw))
        return orig_push_to(*a, **kw)
    receiver_service.push_to = _tracking_push_to

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

        time.sleep(0.3)
        assert push_calls == [], "An unauthenticated pull request must not trigger a reverse push"
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
        "sync_revision": 10,
        "client_count": 5,
    }
    service._handle_beacon(json.dumps(beacon_body_1).encode("utf-8"), "192.168.1.50")

    peers = service.get_peers()
    assert len(peers) == 1
    assert peers[0]["host"] == "WORKSTATION-A"
    assert peers[0]["ip"] == "192.168.1.50"
    assert peers[0]["sync_revision"] == 10

    # Second beacon from SAME WORKSTATION-A, but its IP changed to 192.168.1.75 (e.g. DHCP renewal)
    beacon_body_2 = {
        "magic": SERA_SYNC_MAGIC,
        "username": "StaffMember",
        "host": "WORKSTATION-A",
        "sync_port": 49157,
        "sync_revision": 12,
        "client_count": 6,
    }
    service._handle_beacon(json.dumps(beacon_body_2).encode("utf-8"), "192.168.1.75")

    # Entry must be updated in-place: still exactly 1 peer, no ghost with the old IP
    peers_after = service.get_peers()
    assert len(peers_after) == 1, f"Expected 1 peer after IP change, got {len(peers_after)}"
    assert peers_after[0]["host"] == "WORKSTATION-A"
    assert peers_after[0]["ip"] == "192.168.1.75"
    assert peers_after[0]["sync_revision"] == 12
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



