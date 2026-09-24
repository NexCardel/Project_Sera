"""Tests for WP P1-5: Key-fingerprint gate everywhere.

Blueprint §5 WP P1-5:
- Add key_id to legacy beacons and headers. A legacy PC (no key id) and an office-mode PC
  never exchange databases; the dialog shows "different office key — rejoin needed".
- _auto_heal_raw_db: in office mode, never auto-heal. Raise a clear error naming the file.
- Accept: test_key_id_mismatch_rejected.
"""

import json
import os
import socket
import pytest
from pathlib import Path
import sqlcipher3.dbapi2 as sqlite3

import security
import sera_keys
from sync_peer import SyncPeerService, _send_framed, _recv_framed


def _init_test_db(db_path: Path, hex_key: str):
    """Creates a valid encrypted SQLite DB with at least one table."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(f"PRAGMA key = \"x'{hex_key}'\";")
    conn.execute("CREATE TABLE test_table (id INTEGER PRIMARY KEY, val TEXT);")
    conn.execute("INSERT INTO test_table (val) VALUES ('test');")
    conn.commit()
    conn.close()


def test_key_id_mismatch_rejected(tmp_path):
    """
    Accept test for P1-5:
    1. Legacy sender (no key_id) pushing to office-mode receiver (with key_id) is REJECTED (KEY_ID_MISMATCH).
    2. Office-mode sender pushing to legacy receiver (no key_id) is REJECTED (KEY_ID_MISMATCH).
    3. Office-mode sender pushing to office-mode receiver with DIFFERENT key_id is REJECTED (KEY_ID_MISMATCH).
    4. request_database_pull with mismatched key_id is REJECTED (KEY_ID_MISMATCH).
    5. Two office-mode peers with MATCHING key_id can successfully exchange databases.
    """
    # Keys setup
    office_dek_1 = sera_keys.new_dek()
    key_id_1 = sera_keys.key_id(office_dek_1)
    hex_key_1 = sera_keys.dek_hex(office_dek_1)

    office_dek_2 = sera_keys.new_dek()
    key_id_2 = sera_keys.key_id(office_dek_2)
    hex_key_2 = sera_keys.dek_hex(office_dek_2)

    # 1. Receiver in office mode (key_id_1)
    receiver_dir = tmp_path / "receiver_office1"
    receiver_dir.mkdir()
    recv_db = receiver_dir / "master.db"
    recv_salt = receiver_dir / "sera.salt"
    security.generate_and_save_salt(str(recv_salt))
    _init_test_db(recv_db, hex_key_1)

    receiver_service = SyncPeerService(
        db_path=str(recv_db),
        salt_path=str(recv_salt),
        username="ReceiverOffice1",
        sync_port=0,
        hex_key=hex_key_1,
        key_id=key_id_1,
    )
    receiver_service.start()

    try:
        recv_port = receiver_service._tcp_server.getsockname()[1]

        # Case 1: Legacy sender (no key_id) pushes to office-mode receiver
        sender_legacy_dir = tmp_path / "sender_legacy"
        sender_legacy_dir.mkdir()
        send_leg_salt = sender_legacy_dir / "sera.salt"
        security.generate_and_save_salt(str(send_leg_salt))
        send_leg_salt_bytes = security.load_salt(str(send_leg_salt))
        legacy_hex_key = security.derive_key_hex("legacy_pwd", send_leg_salt_bytes)
        send_leg_db = sender_legacy_dir / "master.db"
        _init_test_db(send_leg_db, legacy_hex_key)

        sender_legacy_service = SyncPeerService(
            db_path=str(send_leg_db),
            salt_path=str(send_leg_salt),
            username="SenderLegacy",
            sync_port=0,
            hex_key=legacy_hex_key,
            key_id=None,  # Legacy mode
        )
        res1 = sender_legacy_service.push_to("127.0.0.1", recv_port, force_override=True)
        assert "KEY_ID_MISMATCH" in res1
        assert "different office key" in res1
        assert not (receiver_dir / "incoming" / "pending_swap.json").exists()

        # Case 2: Office sender with DIFFERENT key_id pushes to receiver
        sender_diff_dir = tmp_path / "sender_office2"
        sender_diff_dir.mkdir()
        send_diff_salt = sender_diff_dir / "sera.salt"
        security.generate_and_save_salt(str(send_diff_salt))
        send_diff_db = sender_diff_dir / "master.db"
        _init_test_db(send_diff_db, hex_key_2)

        sender_diff_service = SyncPeerService(
            db_path=str(send_diff_db),
            salt_path=str(send_diff_salt),
            username="SenderOffice2",
            sync_port=0,
            hex_key=hex_key_2,
            key_id=key_id_2,
        )
        res2 = sender_diff_service.push_to("127.0.0.1", recv_port, force_override=True)
        assert "KEY_ID_MISMATCH" in res2
        assert "different office key" in res2
        assert not (receiver_dir / "incoming" / "pending_swap.json").exists()

        # Case 3: Pull request with mismatched key_id
        pull_ok = sender_diff_service.request_pull_from("127.0.0.1", recv_port)
        assert pull_ok is False

        # Case 4: Office sender pushes to legacy receiver
        receiver_legacy_dir = tmp_path / "receiver_legacy"
        receiver_legacy_dir.mkdir()
        recv_leg_salt = receiver_legacy_dir / "sera.salt"
        security.generate_and_save_salt(str(recv_leg_salt))
        recv_leg_salt_bytes = security.load_salt(str(recv_leg_salt))
        recv_leg_hex_key = security.derive_key_hex("legacy_pwd_2", recv_leg_salt_bytes)
        recv_leg_db = receiver_legacy_dir / "master.db"
        _init_test_db(recv_leg_db, recv_leg_hex_key)

        receiver_legacy_service = SyncPeerService(
            db_path=str(recv_leg_db),
            salt_path=str(recv_leg_salt),
            username="ReceiverLegacy",
            sync_port=0,
            hex_key=recv_leg_hex_key,
            key_id=None,
        )
        receiver_legacy_service.start()
        try:
            recv_leg_port = receiver_legacy_service._tcp_server.getsockname()[1]
            res4 = sender_diff_service.push_to("127.0.0.1", recv_leg_port, force_override=True)
            assert "KEY_ID_MISMATCH" in res4
            assert "different office key" in res4
            assert not (receiver_legacy_dir / "incoming" / "pending_swap.json").exists()
        finally:
            receiver_legacy_service.stop()

        # Case 5: Matching key_id successfully accepted and staged in office mode
        sender_match_dir = tmp_path / "sender_match"
        sender_match_dir.mkdir()
        send_match_salt = sender_match_dir / "sera.salt"
        security.generate_and_save_salt(str(send_match_salt))
        send_match_db = sender_match_dir / "master.db"
        _init_test_db(send_match_db, hex_key_1)

        sender_match_service = SyncPeerService(
            db_path=str(send_match_db),
            salt_path=str(send_match_salt),
            username="SenderMatch",
            sync_port=0,
            hex_key=hex_key_1,
            key_id=key_id_1,
        )
        res5 = sender_match_service.push_to("127.0.0.1", recv_port, force_override=True)
        assert "successfully" in res5.lower()
        assert (receiver_dir / "incoming" / "pending_swap.json").exists()

    finally:
        receiver_service.stop()


def test_auto_heal_disabled_in_office_mode(tmp_path):
    """
    Blueprint §5 WP P1-5:
    _auto_heal_raw_db: in office mode, never auto-heal. Raise a clear error naming the file.
    """
    from database import SeraDatabase

    app_dir = tmp_path / "office_app"
    app_dir.mkdir()
    keys_dir = app_dir / "keys"
    keys_dir.mkdir()

    dek = sera_keys.new_dek()
    kid = sera_keys.key_id(dek)
    hex_key = sera_keys.dek_hex(dek)

    # Save office.json to mark office mode
    sera_keys.save_office(
        app_dir,
        sera_keys.OfficeInfo(office_id="test-office", office_name="Test", key_id=kid),
    )

    master_db = app_dir / "master.db"
    _init_test_db(master_db, hex_key)

    raw_db = app_dir / "rawPayload.db"
    # Create an encrypted rawPayload.db with a DIFFERENT key (key mismatch)
    other_dek = sera_keys.new_dek()
    _init_test_db(raw_db, sera_keys.dek_hex(other_dek))
    orig_bytes = raw_db.read_bytes()

    # Opening database in office mode must NOT auto-heal; it must raise RuntimeError naming the file
    with pytest.raises(RuntimeError) as exc_info:
        SeraDatabase(str(master_db), hex_key, defer_startup_maintenance=True)

    err = str(exc_info.value)
    assert str(raw_db) in err
    assert "rawPayload.db" in err

    # The file must NOT have been deleted, moved, or overwritten
    assert raw_db.exists()
    assert raw_db.read_bytes() == orig_bytes

    # No *.bak backup should have been created
    bak_files = list(app_dir.glob("rawPayload.db*.bak"))
    assert len(bak_files) == 0

    # Also test calling _auto_heal_raw_db directly raises in office mode
    temp_raw = app_dir / "temp_raw.db"
    _init_test_db(temp_raw, hex_key)
    db = SeraDatabase(str(master_db), hex_key, raw_db_path=str(temp_raw), defer_startup_maintenance=True, key_mode="office")
    db.raw_db_path = str(raw_db)
    with pytest.raises(RuntimeError) as exc_info2:
        db._auto_heal_raw_db()
    assert str(raw_db) in str(exc_info2.value)


def test_key_id_in_beacons_and_headers(tmp_path):
    """Beacons and outgoing headers include key_id when set, and omit it when in legacy mode."""
    dek = sera_keys.new_dek()
    kid = sera_keys.key_id(dek)
    hex_k = sera_keys.dek_hex(dek)

    office_dir = tmp_path / "office"
    office_dir.mkdir()
    office_db = office_dir / "master.db"
    office_salt = office_dir / "sera.salt"
    security.generate_and_save_salt(str(office_salt))
    _init_test_db(office_db, hex_k)

    office_service = SyncPeerService(
        db_path=str(office_db),
        salt_path=str(office_salt),
        username="OfficeUser",
        hex_key=hex_k,
        key_id=kid,
    )

    # Beacon payload has key_id
    payload = json.loads(office_service._beacon_payload().decode("utf-8"))
    assert payload.get("key_id") == kid

    # Signed header has key_id
    header = {"action": "push_database", "host": "test"}
    signed = office_service._sign_header(header)
    assert signed.get("key_id") == kid

    # Legacy service
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    leg_db = legacy_dir / "master.db"
    leg_salt = legacy_dir / "sera.salt"
    security.generate_and_save_salt(str(leg_salt))
    _init_test_db(leg_db, hex_k)

    legacy_service = SyncPeerService(
        db_path=str(leg_db),
        salt_path=str(leg_salt),
        username="LegacyUser",
        hex_key=hex_k,
        key_id=None,
    )

    leg_payload = json.loads(legacy_service._beacon_payload().decode("utf-8"))
    assert leg_payload.get("key_id") is None

    leg_signed = legacy_service._sign_header(header)
    assert leg_signed.get("key_id") is None


def test_bootstrap_autopull_skips_mismatched_key_id(tmp_path):
    """An empty bootstrapping node does not trigger auto-pull from peers with mismatched key_id."""
    dek1 = sera_keys.new_dek()
    kid1 = sera_keys.key_id(dek1)
    hex_k1 = sera_keys.dek_hex(dek1)

    dek2 = sera_keys.new_dek()
    kid2 = sera_keys.key_id(dek2)

    app_dir = tmp_path / "bootstrap_node"
    app_dir.mkdir()
    db_path = app_dir / "master.db"
    salt_path = app_dir / "sera.salt"
    security.generate_and_save_salt(str(salt_path))
    # Empty DB with 0 clients
    conn = sqlite3.connect(str(db_path))
    conn.execute(f"PRAGMA key = \"x'{hex_k1}'\";")
    conn.execute("CREATE TABLE clients (id INTEGER PRIMARY KEY, is_archived INTEGER DEFAULT 0);")
    conn.commit()
    conn.close()

    service = SyncPeerService(
        db_path=str(db_path),
        salt_path=str(salt_path),
        username="Bootstrapper",
        hex_key=hex_k1,
        key_id=kid1,
    )
    assert service._is_bootstrapping is True

    # Simulate beacon from peer with mismatched key_id (kid2) and client_count > 0
    beacon_mismatched = {
        "magic": "sera-sync-v2",
        "username": "PeerDiffKey",
        "host": "RemoteHost1",
        "sync_port": 49157,
        "client_count": 50,
        "sync_revision": 10,
        "key_id": kid2,
    }
    service._handle_beacon(json.dumps(beacon_mismatched).encode("utf-8"), "192.168.1.100")
    # Must NOT have attempted bootstrap pull to this IP
    assert "192.168.1.100" not in service._bootstrap_pull_attempted_peers

    # Simulate beacon from peer with NO key_id (legacy)
    beacon_legacy = {
        "magic": "sera-sync-v2",
        "username": "PeerLegacy",
        "host": "RemoteHost2",
        "sync_port": 49157,
        "client_count": 50,
        "sync_revision": 10,
    }
    service._handle_beacon(json.dumps(beacon_legacy).encode("utf-8"), "192.168.1.101")
    assert "192.168.1.101" not in service._bootstrap_pull_attempted_peers

    # Simulate beacon from peer with MATCHING key_id (kid1)
    beacon_matching = {
        "magic": "sera-sync-v2",
        "username": "PeerMatch",
        "host": "RemoteHost3",
        "sync_port": 49157,
        "client_count": 50,
        "sync_revision": 10,
        "key_id": kid1,
    }
    service._handle_beacon(json.dumps(beacon_matching).encode("utf-8"), "192.168.1.102")
    assert "192.168.1.102" in service._bootstrap_pull_attempted_peers


def test_dialog_shows_different_office_key(tmp_path):
    """The SeraSyncDialog shows 'different office key — rejoin needed' for peers with mismatched key_id."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog

    dek = sera_keys.new_dek()
    kid = sera_keys.key_id(dek)
    hex_k = sera_keys.dek_hex(dek)

    app_dir = tmp_path / "app"
    app_dir.mkdir()
    db_path = app_dir / "master.db"
    salt_path = app_dir / "sera.salt"
    security.generate_and_save_salt(str(salt_path))
    _init_test_db(db_path, hex_k)

    service = SyncPeerService(
        db_path=str(db_path),
        salt_path=str(salt_path),
        username="LocalUser",
        hex_key=hex_k,
        key_id=kid,
    )

    # Inject mock peers into service
    with service._peers_lock:
        from sync_peer import PeerInfo
        service._peers["HostMatch"] = PeerInfo(
            username="MatchUser",
            host="HostMatch",
            ip="192.168.1.10",
            sync_port=49157,
            key_id=kid,
        )
        service._peers["HostDiff"] = PeerInfo(
            username="DiffUser",
            host="HostDiff",
            ip="192.168.1.20",
            sync_port=49157,
            key_id="different_key_id_hex_string_1234",
        )
        service._peers["HostLegacy"] = PeerInfo(
            username="LegacyUser",
            host="HostLegacy",
            ip="192.168.1.30",
            sync_port=49157,
            key_id=None,
        )

    dialog = SeraSyncDialog(sync_service=service)
    dialog._refresh_peers()

    # Find rows for each peer
    rows = {}
    for r in range(dialog.table.rowCount()):
        host = dialog.table.item(r, 1).text()
        status = dialog.table.item(r, 7).text()
        rows[host] = status

    assert "Normal" in rows["HostMatch"]
    assert "different office key — rejoin needed" in rows["HostDiff"]
    assert "different office key — rejoin needed" in rows["HostLegacy"]


def test_office_refuses_legacy_fetch_snapshot(tmp_path):
    """An office-mode node strictly refuses legacy fetch_snapshot join requests."""
    from sync_peer import join_office_fetch_snapshot

    office_dek = sera_keys.new_dek()
    key_id = sera_keys.key_id(office_dek)
    hex_key = sera_keys.dek_hex(office_dek)

    app_dir = tmp_path / "office_node"
    app_dir.mkdir()
    db_path = app_dir / "master.db"
    _init_test_db(db_path, hex_key)

    service = SyncPeerService(
        db_path=str(db_path),
        salt_path=str(app_dir / "sera.salt"),  # Does not exist
        username="OfficeNode",
        sync_port=0,
        hex_key=hex_key,
        key_id=key_id,
    )
    service.start()
    try:
        port = service._tcp_server.getsockname()[1]
        joiner_dir = tmp_path / "joiner"
        joiner_dir.mkdir()

        ok, reason, staged_db, staged_salt = join_office_fetch_snapshot(
            peer_ip="127.0.0.1",
            peer_port=port,
            host_name="LegacyJoiner",
            username="JoinerUser",
            code="123456",
            app_dir=joiner_dir,
            timeout=5.0,
        )
        assert ok is False
        assert "KEY_ID_MISMATCH" in reason
        assert "different office key — rejoin needed" in reason
        assert staged_db is None
        assert staged_salt is None
    finally:
        service.stop()


def test_office_push_sends_zero_salt_and_swap_does_not_install_salt(tmp_path):
    """
    In office mode:
    1. push_to sends salt_size=0 and no salt bytes.
    2. Receiver accepts the snapshot and writes pending_swap.json with NO salt field.
    3. apply_pending_swap replaces master.db without creating or installing sera.salt.
    """
    from sync_peer import apply_pending_swap

    office_dek = sera_keys.new_dek()
    key_id = sera_keys.key_id(office_dek)
    hex_key = sera_keys.dek_hex(office_dek)

    # Receiver in office mode with NO sera.salt
    recv_dir = tmp_path / "recv_office"
    recv_dir.mkdir()
    recv_db = recv_dir / "master.db"
    _init_test_db(recv_db, hex_key)
    assert not (recv_dir / "sera.salt").exists()

    recv_service = SyncPeerService(
        db_path=str(recv_db),
        salt_path=str(recv_dir / "sera.salt"),
        username="RecvOffice",
        sync_port=0,
        hex_key=hex_key,
        key_id=key_id,
    )
    recv_service.start()
    try:
        recv_port = recv_service._tcp_server.getsockname()[1]

        # Sender in office mode with NO sera.salt
        send_dir = tmp_path / "send_office"
        send_dir.mkdir()
        send_db = send_dir / "master.db"
        # Create a DB with a new row to distinguish it
        conn = sqlite3.connect(str(send_db))
        conn.execute(f"PRAGMA key = \"x'{hex_key}'\";")
        conn.execute("CREATE TABLE test_table (id INTEGER PRIMARY KEY, val TEXT);")
        conn.execute("INSERT INTO test_table (val) VALUES ('new_office_content');")
        conn.commit()
        conn.close()
        assert not (send_dir / "sera.salt").exists()

        send_service = SyncPeerService(
            db_path=str(send_db),
            salt_path=str(send_dir / "sera.salt"),
            username="SendOffice",
            sync_port=0,
            hex_key=hex_key,
            key_id=key_id,
        )
        res = send_service.push_to("127.0.0.1", recv_port, force_override=True)
        assert "successfully" in res.lower()

        # Check receiver staging
        pending_json = recv_dir / "incoming" / "pending_swap.json"
        assert pending_json.exists()
        with open(pending_json, "r", encoding="utf-8") as f:
            swap_info = json.load(f)
        assert "salt" not in swap_info
        assert not (recv_dir / "incoming" / "sera.salt").exists()

        # Stop receiver service before applying swap
        recv_service.stop()

        # Apply pending swap
        swapped = apply_pending_swap(recv_dir)
        assert swapped is True
        assert not pending_json.exists()

        # Verify sera.salt WAS NOT created on receiver
        assert not (recv_dir / "sera.salt").exists()

        # Verify updated DB opens with office DEK and has the new content
        conn2 = sqlite3.connect(str(recv_db))
        conn2.execute(f"PRAGMA key = \"x'{hex_key}'\";")
        row = conn2.execute("SELECT val FROM test_table;").fetchone()
        conn2.close()
        assert row is not None and row[0] == "new_office_content"

    finally:
        try:
            recv_service.stop()
        except Exception:
            pass


def test_apply_pending_swap_preserves_stray_salt_as_stale_backup(tmp_path):
    """
    Blueprint §0 rule 3:
    When a swap has no salt (office mode), any stray sera.salt* files in incoming/
    must not be unlinked; they are renamed to <name>.stale-<ts> to prevent data loss.
    """
    from sync_peer import apply_pending_swap

    app_dir = tmp_path / "app_node"
    app_dir.mkdir()
    live_db = app_dir / "master.db"
    live_db.write_bytes(b"initial_live_db_bytes")

    incoming_dir = app_dir / "incoming"
    incoming_dir.mkdir()
    staged_db = incoming_dir / "master.db"
    staged_db.write_bytes(b"staged_db_bytes_to_install")

    # Put a stray salt in incoming/
    stray_salt = incoming_dir / "sera.salt"
    stray_salt.write_bytes(b"stray_salt_preexisting_bytes_1234")

    # Write pending_swap.json with NO salt field
    pending_json = incoming_dir / "pending_swap.json"
    with open(pending_json, "w", encoding="utf-8") as f:
        json.dump({"db": "master.db", "from": "Peer1", "at": "2026-09-24T12:00:00Z"}, f)

    # Execute swap
    res = apply_pending_swap(app_dir)
    assert res is True

    # Live DB was replaced
    assert live_db.read_bytes() == b"staged_db_bytes_to_install"

    # Live salt was NOT created
    assert not (app_dir / "sera.salt").exists()

    # Original stray salt was NOT unlinked; it was preserved as .stale-<ts>
    assert not stray_salt.exists()
    stale_files = list(incoming_dir.glob("sera.salt.stale-*"))
    assert len(stale_files) == 1
    assert stale_files[0].read_bytes() == b"stray_salt_preexisting_bytes_1234"


