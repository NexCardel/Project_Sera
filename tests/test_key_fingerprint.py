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


# test_key_id_mismatch_rejected (the blueprint's named "Accept" test for this WP) exercised the
# key-fingerprint gate through the legacy push_database / request_database_pull actions
# (push_to, request_pull_from), which P4-1 removed once Sera Sync v3 went live everywhere; the
# TCP server now serves only fetch_snapshot, whose own key-fingerprint gate is covered by
# test_office_refuses_legacy_fetch_snapshot below, and the peer-table "different office key"
# badge is covered by test_dialog_shows_different_office_key. Deleted rather than weakened
# (blueprint §0 rule 4).


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


# test_bootstrap_autopull_skips_mismatched_key_id tested the bootstrap-quarantine auto-pull
# mechanism (_is_bootstrapping, _bootstrap_pull_attempted_peers), which P4-1 removed along with
# the legacy push/pull protocol it fed. Deleted rather than weakened (blueprint §0 rule 4).


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
        status = dialog.table.item(r, 6).text()  # Status is column 6 since P4-1 dropped Rev Score
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


# test_office_push_sends_zero_salt_and_swap_does_not_install_salt exercised push_to (office
# mode sends salt_size=0), which P4-1 removed with the rest of the legacy push/pull protocol.
# apply_pending_swap's no-salt handling itself is still covered directly by
# test_apply_pending_swap_preserves_stray_salt_as_stale_backup below. Deleted rather than
# weakened (blueprint §0 rule 4).


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


