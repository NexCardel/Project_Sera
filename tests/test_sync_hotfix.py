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



