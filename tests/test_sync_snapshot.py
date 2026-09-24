"""Tests for sync_snapshot.py (Sera Sync v3, WP P2-6): Snapshot service and joiner install.

Everything runs on 127.0.0.1 with PCs under pytest's tmp_path; the real data folder is
never touched (§0 rule 2). Databases use SQLCipher encryption with the office DEK.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import sqlcipher3.dbapi2 as sqlite3

import sera_keys
import sync_admin
import sync_identity
import sync_pairing
import sync_snapshot
import sync_transport
from sync_snapshot import (
    SnapshotError,
    SnapshotVerificationError,
    download_snapshot,
    handle_snapshot_session,
    has_pending_join,
    make_office_snapshot,
    resume_join_snapshot,
)

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
pytestmark = windows_only

MASTER_PW = "OfficeMaster#2026"


# ------------------------------------------------------------------ helpers

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _make_office(app: Path) -> tuple[sera_keys.OfficeInfo, bytes]:
    dek = sera_keys.new_dek()
    info = sera_keys.OfficeInfo(
        office_id=sera_keys.OfficeInfo.new_office_id(),
        office_name="Test Snapshot Office",
        key_id=sera_keys.key_id(dek),
    )
    sera_keys.store_dek(app, dek, MASTER_PW, info.office_id)
    sera_keys.save_office(app, info)
    return info, dek


class AdminPC:
    def __init__(self, app: Path):
        self.app = app
        app.mkdir(parents=True, exist_ok=True)
        self.office, self.dek = _make_office(app)
        self.dek_hex = self.dek.hex()
        self.pubkey = sync_admin.create_admin_key(app, MASTER_PW)
        self.identity = sync_identity.ensure_device_identity(app)
        self.device_id = self.identity.device_id
        self.cert_chain = sync_identity.load_cert_chain_args(app)

        self.db_path = app / "master.db"
        self.raw_path = app / "rawPayload.db"

        # Initialize SQLCipher master.db
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(f"PRAGMA key = \"x'{self.dek_hex}'\";")
        conn.execute("PRAGMA user_version = 42;")

        # Initialize office membership
        sync_admin.init_office_membership(
            app, conn, self.identity.cert_pem.decode("ascii"), "Front desk Admin"
        )

        # Clients and settings
        conn.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id_token TEXT,
                name TEXT NOT NULL,
                pan TEXT UNIQUE,
                is_archived INTEGER DEFAULT 0
            );
        """)
        conn.execute("INSERT INTO clients (client_id_token, name, pan) VALUES ('A-1', 'ALPHA CORP', 'ABCDE1234F');")
        conn.execute("INSERT INTO clients (client_id_token, name, pan) VALUES ('A-2', 'BETA TRADERS', 'BCDEF2345G');")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        conn.execute("INSERT INTO app_settings (key, value) VALUES ('theme', 'dark');")
        conn.execute("INSERT INTO app_settings (key, value) VALUES ('window_mode', 'maximized');")

        # Local-mode tables (rows should be stripped in snapshot)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS client_activity_stats (
                client_id INTEGER PRIMARY KEY,
                view_count INTEGER DEFAULT 0,
                action_count INTEGER DEFAULT 0
            );
        """)
        conn.execute("INSERT INTO client_activity_stats (client_id, view_count, action_count) VALUES (1, 15, 3);")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS client_recent_activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                action_type TEXT,
                detail TEXT,
                timestamp REAL
            );
        """)
        conn.execute("INSERT INTO client_recent_activity (client_id, action_type, detail, timestamp) VALUES (1, 'VIEW', 'viewed', 1700000000.0);")

        # Local_* tables (tables should be dropped in snapshot)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS _local_addresses (
                device_id TEXT PRIMARY KEY,
                ip TEXT,
                port INTEGER,
                last_ok_at TEXT,
                source TEXT
            );
        """)
        conn.execute("INSERT INTO _local_addresses (device_id, ip, port, last_ok_at, source) VALUES ('dev1', '10.0.0.1', 49159, '2026-09-24T12:00:00Z', 'beacon');")

        conn.commit()
        conn.close()

        # Initialize SQLCipher rawPayload.db
        rconn = sqlite3.connect(str(self.raw_path))
        rconn.execute(f"PRAGMA key = \"x'{self.dek_hex}'\";")
        rconn.execute("PRAGMA user_version = 10;")
        rconn.execute("""
            CREATE TABLE IF NOT EXISTS tracker_dump (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id           INTEGER,
                unassigned_identity TEXT,
                service_id          INTEGER,
                portal              TEXT,
                period_label        TEXT,
                arn_number          TEXT,
                capture_method      TEXT DEFAULT 'DOM_Tracker',
                status              TEXT DEFAULT 'submitted',
                raw_payload_json    TEXT,
                captured_by         TEXT,
                dataset_key         TEXT,
                created_at          TEXT NOT NULL DEFAULT '2026-09-24T12:00:00Z'
            );
        """)
        rconn.execute("INSERT INTO tracker_dump (client_id, dataset_key, raw_payload_json) VALUES (1, 'pan_1', '{\"status\": \"active\"}');")
        rconn.commit()
        rconn.close()

        self.joined = []
        self.closed = []
        self._server = None
        self._transport = None

    def open_db(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(f"PRAGMA key = \"x'{self.dek_hex}'\";")
        return conn

    def get_members(self):
        conn = self.open_db()
        try:
            return sync_admin.list_members(conn, self.pubkey, include_revoked=False)
        finally:
            conn.close()

    def member_set(self) -> sync_transport.MemberSet:
        records = self.get_members()
        return sync_transport.MemberSet.from_records(records, own_cert_pem=self.identity.cert_pem)

    def start_sync_server(self, host="127.0.0.1", port=0) -> sync_transport.SyncServer:
        members = self.member_set()
        self._transport = sync_transport.SyncTransport(self.cert_chain, members)

        def handler(session):
            handle_snapshot_session(session, self.app)

        self._server = self._transport.serve(handler, host=host, port=port)
        return self._server

    @property
    def port(self) -> int:
        return self._server.address[1] if self._server else 0

    def update_transport_members(self):
        if self._transport is not None:
            self._transport.update_members(self.member_set())

    def window(self, **kw) -> sync_pairing.PairingWindow:
        kw.setdefault("host", "127.0.0.1")
        kw.setdefault("port", 0)

        def on_joined_cb(record):
            self.joined.append(record)
            self.update_transport_members()

        w = sync_pairing.PairingWindow(
            self.app, self.open_db, self.device_id,
            on_joined=on_joined_cb,
            on_closed=self.closed.append,
            **kw,
        )
        w.start()
        return w

    def stop(self):
        if self._server is not None:
            self._server.stop()
            self._server = None


@pytest.fixture
def admin(tmp_path):
    adm = AdminPC(tmp_path / "admin_pc")
    yield adm
    adm.stop()


# ------------------------------------------------------------------ acceptance tests

def test_join_snapshot_end_to_end(tmp_path, admin):
    """§5 P2-6 Accept test:
    Two services on localhost: pair, snapshot, joiner opens the DB and sees the same clients.
    Also verifies:
    - _local_* tables are dropped
    - local-mode table rows are cleared
    - app_settings rows are preserved
    - rawPayload.db is exported and verified
    - pairing.json is cleaned up after install
    - schema_version matches PRAGMA user_version
    """
    # 1. Start Admin services on localhost
    sync_srv = admin.start_sync_server()
    sync_port = sync_srv.address[1]

    p_win = admin.window()
    pairing_port = p_win.port
    code = p_win.code

    # 2. Joiner PC setup
    joiner_dir = tmp_path / "joiner_pc"
    joiner_dir.mkdir(parents=True, exist_ok=True)

    # 3. Joiner pairs using 6-digit code
    join_res = sync_pairing.join_office(
        joiner_dir,
        "127.0.0.1",
        code,
        "Workstation B",
        port=pairing_port,
    )

    assert join_res.office_id == admin.office.office_id
    assert join_res.key_id == admin.office.key_id
    assert has_pending_join(joiner_dir) is True

    # 4. Joiner downloads snapshot from Admin
    installed = download_snapshot(
        joiner_dir,
        "127.0.0.1",
        admin.device_id,
        join_res.records,
        port=sync_port,
        timeout=30.0,
    )

    assert "master.db" in installed
    assert "rawPayload.db" in installed
    assert (joiner_dir / "master.db").exists()
    assert (joiner_dir / "rawPayload.db").exists()
    assert has_pending_join(joiner_dir) is False

    # 5. Joiner opens master.db with DEK
    joiner_dek = sera_keys.load_dek(joiner_dir)
    conn = sqlite3.connect(str(joiner_dir / "master.db"))
    conn.execute(f"PRAGMA key = \"x'{joiner_dek.hex()}'\";")

    # Check clients
    cur = conn.execute("SELECT name, pan FROM clients ORDER BY id;")
    clients = cur.fetchall()
    assert clients == [("ALPHA CORP", "ABCDE1234F"), ("BETA TRADERS", "BCDEF2345G")]

    # Check app_settings
    cur = conn.execute("SELECT key, value FROM app_settings ORDER BY key;")
    settings = dict(cur.fetchall())
    assert settings.get("theme") == "dark"
    assert settings.get("window_mode") == "maximized"

    # Check local-mode table rows are emptied
    cur = conn.execute("SELECT count(*) FROM client_activity_stats;")
    assert cur.fetchone()[0] == 0
    cur = conn.execute("SELECT count(*) FROM client_recent_activity;")
    assert cur.fetchone()[0] == 0

    # Check _local_* tables are dropped
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '_local%';")
    assert cur.fetchall() == []

    # Check user_version was preserved
    cur = conn.execute("PRAGMA user_version;")
    assert cur.fetchone()[0] == 42

    conn.close()

    # Check rawPayload.db
    rconn = sqlite3.connect(str(joiner_dir / "rawPayload.db"))
    rconn.execute(f"PRAGMA key = \"x'{joiner_dek.hex()}'\";")
    cur = rconn.execute("SELECT client_id, dataset_key FROM tracker_dump;")
    assert cur.fetchall() == [(1, "pan_1")]
    cur = rconn.execute("PRAGMA user_version;")
    assert cur.fetchone()[0] == 10
    rconn.close()

    p_win.close()


def test_corrupt_snapshot_rejected(tmp_path, admin):
    """§5 P2-6 Accept test:
    test_corrupt_snapshot_rejected.
    Tests:
    1. Checksum mismatch: If a streamed DB file has SHA-256 differing from manifest,
       download is rejected and nothing is installed.
    2. Decryption failure / integrity check failure: If the database is corrupt or cannot
       be opened with the DEK, download is rejected and nothing is installed.
    3. Manifest mismatch: If office_id or key_id does not match office.json, rejected.
    """
    # 1. Pair joiner first
    p_win = admin.window()
    joiner_dir = tmp_path / "joiner_pc"
    joiner_dir.mkdir(parents=True, exist_ok=True)
    join_res = sync_pairing.join_office(joiner_dir, "127.0.0.1", p_win.code, "Workstation C", port=p_win.port)
    p_win.close()

    # Custom sync server simulating corruption
    joiner_identity = sync_identity.ensure_device_identity(joiner_dir)
    member_recs = [r for r in join_res.records if "cert_pem" in r]
    admin_members = sync_transport.MemberSet.from_records(member_recs, own_cert_pem=admin.identity.cert_pem)
    transport = sync_transport.SyncTransport(admin.cert_chain, admin_members)

    # Subtest A: Hash mismatch
    def hash_mismatch_handler(session):
        req = session.recv()
        assert req.get("t") == "snapshot"
        # Export genuine DBs
        with sync_snapshot.temp_snapshot_export(admin.app, admin.dek) as (manifest, files):
            # Tamper with the announced sha256 in manifest
            bad_manifest = dict(manifest)
            bad_manifest["files"] = [
                dict(f, sha256="0" * 64) for f in manifest["files"]
            ]
            session.send(bad_manifest)
            for f in files:
                session.send_file(f)

    srv = transport.serve(hash_mismatch_handler, host="127.0.0.1", port=0)
    try:
        with pytest.raises(SnapshotVerificationError, match="SHA-256 mismatch"):
            download_snapshot(joiner_dir, "127.0.0.1", admin.device_id, join_res.records, port=srv.address[1], timeout=10.0)
        assert not (joiner_dir / "master.db").exists()
    finally:
        srv.stop()

    # Subtest B: Corrupt database content (cipher integrity check fails / cannot decrypt)
    corrupt_db_file = tmp_path / "corrupt_master.db"
    corrupt_db_file.write_bytes(b"THIS IS NOT A VALID SQLCIPHER DATABASE CONTENT AT ALL" * 20)
    corrupt_size = corrupt_db_file.stat().st_size
    corrupt_sha = _sha256_file(corrupt_db_file)

    def corrupt_db_handler(session):
        req = session.recv()
        assert req.get("t") == "snapshot"
        manifest = {
            "t": "manifest",
            "office_id": admin.office.office_id,
            "key_id": admin.office.key_id,
            "schema_version": 1,
            "vectors": {},
            "files": [{"name": "master.db", "size": corrupt_size, "sha256": corrupt_sha}],
        }
        session.send(manifest)
        session.send_file(corrupt_db_file)

    srv2 = transport.serve(corrupt_db_handler, host="127.0.0.1", port=0)
    try:
        with pytest.raises(SnapshotVerificationError, match="cipher_integrity_check|could not be decrypted"):
            download_snapshot(joiner_dir, "127.0.0.1", admin.device_id, join_res.records, port=srv2.address[1], timeout=10.0)
        assert not (joiner_dir / "master.db").exists()
    finally:
        srv2.stop()

    # Subtest C: Key ID mismatch in manifest
    def key_mismatch_handler(session):
        req = session.recv()
        assert req.get("t") == "snapshot"
        manifest = {
            "t": "manifest",
            "office_id": admin.office.office_id,
            "key_id": "different_key_id_12345678",
            "schema_version": 1,
            "vectors": {},
            "files": [{"name": "master.db", "size": corrupt_size, "sha256": corrupt_sha}],
        }
        session.send(manifest)

    srv3 = transport.serve(key_mismatch_handler, host="127.0.0.1", port=0)
    try:
        with pytest.raises(SnapshotVerificationError, match="key_id"):
            download_snapshot(joiner_dir, "127.0.0.1", admin.device_id, join_res.records, port=srv3.address[1], timeout=10.0)
        assert not (joiner_dir / "master.db").exists()
    finally:
        srv3.stop()


def test_resume_interrupted_join(tmp_path, admin):
    """P2-4 / P2-6 note:
    A join interrupted after pairing leaves office.json without master.db, with pairing.json.
    P2-6 detects pairing.json and resume_join_snapshot completes the download.
    """
    p_win = admin.window()
    joiner_dir = tmp_path / "joiner_resume_pc"
    joiner_dir.mkdir(parents=True, exist_ok=True)

    join_res = sync_pairing.join_office(joiner_dir, "127.0.0.1", p_win.code, "Workstation Resume", port=p_win.port)
    p_win.close()

    assert has_pending_join(joiner_dir) is True

    # Start sync server on the admin PC
    sync_srv = admin.start_sync_server()

    # Resume join snapshot
    installed = resume_join_snapshot(joiner_dir, port=sync_srv.address[1], timeout=30.0)
    assert "master.db" in installed
    assert (joiner_dir / "master.db").exists()
    assert has_pending_join(joiner_dir) is False


def test_path_traversal_in_manifest_rejected(tmp_path, admin):
    """A manifest attempting path traversal (e.g. ../evil.db) is rejected."""
    p_win = admin.window()
    joiner_dir = tmp_path / "joiner_path_pc"
    joiner_dir.mkdir(parents=True, exist_ok=True)
    join_res = sync_pairing.join_office(joiner_dir, "127.0.0.1", p_win.code, "Workstation Path", port=p_win.port)
    p_win.close()

    member_recs = [r for r in join_res.records if "cert_pem" in r]
    admin_members = sync_transport.MemberSet.from_records(member_recs, own_cert_pem=admin.identity.cert_pem)
    transport = sync_transport.SyncTransport(admin.cert_chain, admin_members)

    def traversal_handler(session):
        session.recv()
        manifest = {
            "t": "manifest",
            "office_id": admin.office.office_id,
            "key_id": admin.office.key_id,
            "schema_version": 1,
            "vectors": {},
            "files": [{"name": "../escaped.db", "size": 100, "sha256": "0" * 64}],
        }
        session.send(manifest)

    srv = transport.serve(traversal_handler, host="127.0.0.1", port=0)
    try:
        with pytest.raises(SnapshotError, match="invalid file name"):
            download_snapshot(joiner_dir, "127.0.0.1", admin.device_id, join_res.records, port=srv.address[1], timeout=10.0)
    finally:
        srv.stop()


def test_untrusted_peer_rejected(tmp_path, admin):
    """An outsider PC that has not paired cannot request a snapshot."""
    sync_srv = admin.start_sync_server()
    outsider_dir = tmp_path / "outsider_pc"
    outsider_dir.mkdir(parents=True, exist_ok=True)
    outsider_id = sync_identity.ensure_device_identity(outsider_dir)
    outsider_chain = sync_identity.load_cert_chain_args(outsider_dir)

    # Outsider only trusts itself and tries to connect to admin
    outsider_members = sync_transport.MemberSet([], own_cert_pem=outsider_id.cert_pem)
    out_transport = sync_transport.SyncTransport(outsider_chain, outsider_members)

    with pytest.raises(sync_transport.TransportError):
        out_transport.connect("127.0.0.1", sync_srv.address[1], expected_device_id=admin.device_id, connect_timeout=2.0)


def test_make_office_snapshot_helper(tmp_path, admin):
    """Direct test of make_office_snapshot export helper."""
    dest_dir = tmp_path / "manual_snap"
    manifest, files = make_office_snapshot(admin.app, dest_dir)
    assert manifest["t"] == "manifest"
    assert manifest["office_id"] == admin.office.office_id
    assert manifest["key_id"] == admin.office.key_id
    assert manifest["schema_version"] == 42
    assert manifest["vectors"] == {}
    assert len(manifest["files"]) == 2
    assert {f["name"] for f in manifest["files"]} == {"master.db", "rawPayload.db"}
    for f in files:
        assert f.exists()


def test_joiner_never_seeds_default_rows(tmp_path, admin):
    """F14 requirement: The joiner never seeds default rows; DB comes only from the snapshot."""
    import database

    # Add custom staff and columns on admin
    conn = admin.open_db()
    conn.execute("CREATE TABLE IF NOT EXISTS staff_users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, alias TEXT);")
    conn.execute("INSERT INTO staff_users (name) VALUES ('Admin Custom User');")
    conn.commit()
    conn.close()

    p_win = admin.window()
    joiner_dir = tmp_path / "joiner_f14_pc"
    joiner_dir.mkdir(parents=True, exist_ok=True)
    join_res = sync_pairing.join_office(joiner_dir, "127.0.0.1", p_win.code, "Workstation F14", port=p_win.port)
    p_win.close()

    sync_srv = admin.start_sync_server()
    download_snapshot(joiner_dir, "127.0.0.1", admin.device_id, join_res.records, port=sync_srv.address[1], timeout=30.0)

    # Open with SeraDatabase on joiner
    joiner_dek = sera_keys.load_dek(joiner_dir)
    db = database.SeraDatabase(str(joiner_dir / "master.db"), joiner_dek.hex(), defer_startup_maintenance=True, key_mode="office")

    # Verify staff_users only has the Admin Custom User and User 1-6 (from admin), no duplicates seeded
    with db._connect() as c:
        cur = c.execute("SELECT name FROM staff_users WHERE name = 'Admin Custom User';")
        assert len(cur.fetchall()) == 1


def test_empty_files_in_manifest_rejected(tmp_path, admin):
    """Manifest with empty files list is rejected."""
    p_win = admin.window()
    joiner_dir = tmp_path / "joiner_empty_pc"
    joiner_dir.mkdir(parents=True, exist_ok=True)
    join_res = sync_pairing.join_office(joiner_dir, "127.0.0.1", p_win.code, "Workstation Empty", port=p_win.port)
    p_win.close()

    member_recs = [r for r in join_res.records if "cert_pem" in r]
    admin_members = sync_transport.MemberSet.from_records(member_recs, own_cert_pem=admin.identity.cert_pem)
    transport = sync_transport.SyncTransport(admin.cert_chain, admin_members)

    def empty_files_handler(session):
        session.recv()
        manifest = {
            "t": "manifest",
            "office_id": admin.office.office_id,
            "key_id": admin.office.key_id,
            "schema_version": 1,
            "vectors": {},
            "files": [],
        }
        session.send(manifest)

    srv = transport.serve(empty_files_handler, host="127.0.0.1", port=0)
    try:
        with pytest.raises(SnapshotVerificationError, match="contains no database files"):
            download_snapshot(joiner_dir, "127.0.0.1", admin.device_id, join_res.records, port=srv.address[1], timeout=10.0)
    finally:
        srv.stop()


def test_existing_database_and_sidecars_backed_up(tmp_path, admin):
    """B1 requirement: Pre-existing databases and sidecars must be renamed to *.bak-<ts> (§0 rule 3)."""
    p_win = admin.window()
    joiner_dir = tmp_path / "joiner_backup_pc"
    joiner_dir.mkdir(parents=True, exist_ok=True)
    join_res = sync_pairing.join_office(joiner_dir, "127.0.0.1", p_win.code, "Workstation B1", port=p_win.port)
    p_win.close()

    # Pre-create a previous master.db and sidecars on joiner (e.g. from an earlier diverged install)
    old_master = joiner_dir / "master.db"
    old_master.write_bytes(b"OLD PREEXISTING MASTER DATA")
    old_wal = joiner_dir / "master.db-wal"
    old_wal.write_bytes(b"OLD WAL DATA")
    old_shm = joiner_dir / "master.db-shm"
    old_shm.write_bytes(b"OLD SHM DATA")

    sync_srv = admin.start_sync_server()
    download_snapshot(joiner_dir, "127.0.0.1", admin.device_id, join_res.records, port=sync_srv.address[1], timeout=30.0)

    # Verify new master.db is in place
    assert (joiner_dir / "master.db").stat().st_size > 0
    assert (joiner_dir / "master.db").read_bytes() != b"OLD PREEXISTING MASTER DATA"

    # Verify backup files were created (§0 rule 3)
    baks = list(joiner_dir.glob("master.db.bak-*"))
    assert len(baks) >= 1
    assert baks[0].read_bytes() == b"OLD PREEXISTING MASTER DATA"

    wal_baks = list(joiner_dir.glob("master.db-wal.bak-*"))
    assert len(wal_baks) >= 1
    assert wal_baks[0].read_bytes() == b"OLD WAL DATA"

    shm_baks = list(joiner_dir.glob("master.db-shm.bak-*"))
    assert len(shm_baks) >= 1
    assert shm_baks[0].read_bytes() == b"OLD SHM DATA"


def test_unreplaced_local_raw_payload_db_set_aside(tmp_path, admin):
    """B1 requirement: If local rawPayload.db exists but snapshot has none, back it up and set it aside."""
    p_win = admin.window()
    joiner_dir = tmp_path / "joiner_orphan_raw_pc"
    joiner_dir.mkdir(parents=True, exist_ok=True)
    join_res = sync_pairing.join_office(joiner_dir, "127.0.0.1", p_win.code, "Workstation Orphan Raw", port=p_win.port)
    p_win.close()

    # Pre-create an old local rawPayload.db (e.g. encrypted with old password)
    old_raw = joiner_dir / "rawPayload.db"
    old_raw.write_bytes(b"OLD ENCRYPTED RAW CONTENT")

    # Custom server that exports only master.db
    member_recs = [r for r in join_res.records if "cert_pem" in r]
    admin_members = sync_transport.MemberSet.from_records(member_recs, own_cert_pem=admin.identity.cert_pem)
    transport = sync_transport.SyncTransport(admin.cert_chain, admin_members)

    def master_only_handler(session):
        session.recv()
        with sync_snapshot.temp_snapshot_export(admin.app, admin.dek) as (manifest, files):
            # Send only master.db
            single_manifest = dict(manifest)
            single_manifest["files"] = [f for f in manifest["files"] if f["name"] == "master.db"]
            session.send(single_manifest)
            for f in files:
                if f.name == "master.db":
                    session.send_file(f)

    srv = transport.serve(master_only_handler, host="127.0.0.1", port=0)
    try:
        download_snapshot(joiner_dir, "127.0.0.1", admin.device_id, join_res.records, port=srv.address[1], timeout=30.0)
        # Verify local rawPayload.db is gone from active location so office startup doesn't choke on it
        assert not (joiner_dir / "rawPayload.db").exists()
        # But backed up (§0 rule 3)
        raw_baks = list(joiner_dir.glob("rawPayload.db.bak-*"))
        assert len(raw_baks) >= 1
        assert raw_baks[0].read_bytes() == b"OLD ENCRYPTED RAW CONTENT"
    finally:
        srv.stop()


def test_progress_callback_receives_updates(tmp_path, admin):
    """Verify on_progress callback receives chunk-level progress updates."""
    p_win = admin.window()
    joiner_dir = tmp_path / "joiner_prog_pc"
    joiner_dir.mkdir(parents=True, exist_ok=True)
    join_res = sync_pairing.join_office(joiner_dir, "127.0.0.1", p_win.code, "Workstation Prog", port=p_win.port)
    p_win.close()

    progress_events = []
    def on_prog(fname, received, total):
        progress_events.append((fname, received, total))

    sync_srv = admin.start_sync_server()
    download_snapshot(joiner_dir, "127.0.0.1", admin.device_id, join_res.records, port=sync_srv.address[1], timeout=30.0, on_progress=on_prog)

    assert len(progress_events) >= 2
    # Check that final event for each file reports 100%
    master_events = [e for e in progress_events if e[0] == "master.db"]
    assert master_events[0][1] == 0
    assert master_events[-1][1] == master_events[-1][2]

