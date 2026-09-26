"""Tests for sync_office.py (Sera Sync v3, WP P2-7): first-run office-mode wiring.

Covers "New Office" (create_new_office), "Add workstation" (AddWorkstationSession -- P2-6
shipped with nothing serving snapshots, see its progress-log notes) and "Join Office"
(join_office: pairing + snapshot download in one call) end to end over 127.0.0.1, under
pytest's tmp_path (§0 rule 2: the real data folder is never touched).
"""

import sys
from pathlib import Path

import pytest
import sqlcipher3.dbapi2 as sqlite3

import sera_keys
import sync_admin
import sync_identity
import sync_office
import sync_pairing
import sync_snapshot

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
pytestmark = windows_only

MASTER_PW = "OfficeMaster#2026"


def _wrong(code: str) -> str:
    return "%06d" % ((int(code) + 1) % 10 ** 6)


def _open_db_factory(db_path: Path, hex_key: str):
    def _open():
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA key = \"x'%s'\";" % hex_key)
        return conn
    return _open


# ------------------------------------------------------------------ validate_new_office

def test_validate_new_office_requires_a_name():
    assert sync_office.validate_new_office("", MASTER_PW, MASTER_PW) is not None
    assert sync_office.validate_new_office("   ", MASTER_PW, MASTER_PW) is not None


def test_validate_new_office_checks_password_strength():
    err = sync_office.validate_new_office("Office", "short", "short")
    assert err is not None and "8" in err


def test_validate_new_office_refuses_default_password():
    err = sync_office.validate_new_office("Office", "admin123", "admin123")
    assert err is not None


def test_validate_new_office_checks_password_match():
    err = sync_office.validate_new_office("Office", MASTER_PW, "different")
    assert err is not None and "match" in err.lower()


def test_validate_new_office_accepts_good_input():
    assert sync_office.validate_new_office("Aman Associates", MASTER_PW, MASTER_PW) is None


# ------------------------------------------------------------------ create_new_office

def test_create_new_office_creates_db_key_and_admin_membership(tmp_path):
    app = tmp_path / "admin_pc"
    office = sync_office.create_new_office(app, "Aman Associates", MASTER_PW, "Front Desk")

    assert (sera_keys.keys_dir(app) / sera_keys.OFFICE_FILE).exists()
    assert (app / sync_office.MASTER_DB_NAME).exists()
    assert office.office_name == "Aman Associates"
    assert office.admin_pubkey

    dek = sera_keys.load_dek(app)
    assert sera_keys.key_id(dek) == office.key_id

    identity = sync_identity.load_device_identity(app)
    assert identity is not None

    conn = _open_db_factory(app / sync_office.MASTER_DB_NAME, sera_keys.dek_hex(dek))()
    try:
        members = sync_admin.list_members(conn, office.admin_pubkey)
        assert len(members) == 1
        assert members[0]["device_id"] == identity.device_id
        assert members[0]["role"] == sync_admin.ROLE_ADMIN
        assert members[0]["token_letter"] == "A"
        office_admin = sync_admin.get_office_admin(conn, office.admin_pubkey)
        assert office_admin["device_id"] == identity.device_id
    finally:
        conn.close()


def test_create_new_office_refuses_if_office_already_exists(tmp_path):
    app = tmp_path / "admin_pc"
    sync_office.create_new_office(app, "Office A", MASTER_PW, "Front Desk")
    with pytest.raises(sync_office.SyncOfficeError):
        sync_office.create_new_office(app, "Office B", MASTER_PW, "Front Desk")


def test_create_new_office_refuses_if_database_already_exists(tmp_path):
    app = tmp_path / "admin_pc"
    app.mkdir(parents=True)
    (app / sync_office.MASTER_DB_NAME).write_bytes(b"not a real database")
    with pytest.raises(sync_office.SyncOfficeError):
        sync_office.create_new_office(app, "Office", MASTER_PW, "Front Desk")
    # Refused before anything else was written.
    assert not (sera_keys.keys_dir(app) / sera_keys.OFFICE_FILE).exists()


def test_create_new_office_refuses_bad_password(tmp_path):
    app = tmp_path / "admin_pc"
    with pytest.raises(sync_office.SyncOfficeError):
        sync_office.create_new_office(app, "Office", "short", "Front Desk")
    assert not (app / sync_office.MASTER_DB_NAME).exists()


# ------------------------------------------------------------------ AddWorkstationSession + join_office

def _make_admin(tmp_path) -> tuple[Path, sera_keys.OfficeInfo, sync_identity.DeviceIdentity]:
    admin_dir = tmp_path / "admin_pc"
    office = sync_office.create_new_office(admin_dir, "Test Office", MASTER_PW, "Admin PC")
    identity = sync_identity.load_device_identity(admin_dir)
    return admin_dir, office, identity


def test_add_workstation_and_join_office_end_to_end(tmp_path):
    """The full P2-7 loop: New Office -> Add workstation -> a brand-new PC pairs and
    downloads the snapshot. P2-6 shipped with nothing listening on 49159 for this."""
    admin_dir, office, identity = _make_admin(tmp_path)
    hex_key = sera_keys.dek_hex(sera_keys.load_dek(admin_dir))
    own_cert = identity.cert_pem.decode("ascii")

    joined = []
    closed = []
    session = sync_office.AddWorkstationSession(
        admin_dir, _open_db_factory(admin_dir / sync_office.MASTER_DB_NAME, hex_key),
        identity.device_id, own_cert,
        on_joined=joined.append, on_closed=closed.append,
        host="127.0.0.1", port=0, sync_host="127.0.0.1", sync_port=0,
    )
    try:
        session.start()
        assert session.window.is_open
        assert session.sync_port

        joiner_dir = tmp_path / "joiner_pc"
        progress_calls = []
        result = sync_office.join_office(
            joiner_dir, "127.0.0.1", session.window.code, "Back Office",
            port=session.window.port, sync_port=session.sync_port,
            on_progress=lambda *a: progress_calls.append(a),
        )

        assert result.office_id == office.office_id
        assert result.token_letter == "B"
        assert (joiner_dir / sync_office.MASTER_DB_NAME).exists()
        assert (sera_keys.keys_dir(joiner_dir) / sera_keys.OFFICE_FILE).exists()
        assert not sync_snapshot.has_pending_join(joiner_dir)
        assert progress_calls  # on_progress was called at least once

        joiner_dek = sera_keys.load_dek(joiner_dir)
        assert joiner_dek == sera_keys.load_dek(admin_dir)

        # The pairing window closed itself after the join, and the admin's trust store
        # was rebuilt to include the new PC (on_joined callback).
        assert not session.window.is_open
        assert len(joined) == 1
        assert joined[0]["device_id"] == result.device_id
    finally:
        session.close()


def test_add_workstation_session_stops_snapshot_server_when_window_closes(tmp_path):
    admin_dir, office, identity = _make_admin(tmp_path)
    hex_key = sera_keys.dek_hex(sera_keys.load_dek(admin_dir))
    own_cert = identity.cert_pem.decode("ascii")

    session = sync_office.AddWorkstationSession(
        admin_dir, _open_db_factory(admin_dir / sync_office.MASTER_DB_NAME, hex_key),
        identity.device_id, own_cert,
        host="127.0.0.1", port=0, sync_host="127.0.0.1", sync_port=0,
    )
    session.start()
    assert session._server is not None
    session.close("cancelled")
    assert session._server is None
    assert session.window.close_reason == "cancelled"


def test_join_office_wraps_pairing_error_without_writing_anything(tmp_path):
    admin_dir, office, identity = _make_admin(tmp_path)
    hex_key = sera_keys.dek_hex(sera_keys.load_dek(admin_dir))
    own_cert = identity.cert_pem.decode("ascii")

    session = sync_office.AddWorkstationSession(
        admin_dir, _open_db_factory(admin_dir / sync_office.MASTER_DB_NAME, hex_key),
        identity.device_id, own_cert,
        host="127.0.0.1", port=0, sync_host="127.0.0.1", sync_port=0,
    )
    try:
        session.start()
        joiner_dir = tmp_path / "joiner_pc"
        with pytest.raises(sync_pairing.PairingError):
            sync_office.join_office(joiner_dir, "127.0.0.1", _wrong(session.window.code), "Wrong Code",
                                    port=session.window.port, sync_port=session.sync_port)
        assert not (sera_keys.keys_dir(joiner_dir) / sera_keys.OFFICE_FILE).exists()
        assert not (joiner_dir / sync_office.MASTER_DB_NAME).exists()
    finally:
        session.close()


# ------------------------------------------------------------------ ensure_office_identity

def _converted_pc(tmp_path, *, with_admin_key: bool = True) -> tuple[Path, sera_keys.OfficeInfo, str]:
    """The shape "Convert to office key" (sync_migrate) leaves behind: office key, admin key
    and office.json, a database -- but no device identity and no membership records."""
    app = tmp_path / "converted_pc"
    app.mkdir(parents=True)
    dek = sera_keys.new_dek()
    office_id = sera_keys.OfficeInfo.new_office_id()
    sera_keys.store_dek(app, dek, MASTER_PW, office_id)
    admin_pubkey = None
    if with_admin_key:
        admin_pubkey = sync_admin.write_admin_key_files(app, sync_admin.generate_admin_key(), MASTER_PW, office_id)
    office = sera_keys.OfficeInfo(office_id=office_id, office_name="Office", key_id=sera_keys.key_id(dek),
                                  admin_pubkey=admin_pubkey)
    sera_keys.save_office(app, office)
    hex_key = sera_keys.dek_hex(dek)
    from database import SeraDatabase
    db = SeraDatabase(str(app / sync_office.MASTER_DB_NAME), hex_key, defer_startup_maintenance=True)
    del db
    return app, office, hex_key


def test_ensure_office_identity_sets_up_a_converted_admin_pc(tmp_path):
    app, office, hex_key = _converted_pc(tmp_path)
    assert sync_identity.load_device_identity(app) is None

    conn = _open_db_factory(app / sync_office.MASTER_DB_NAME, hex_key)()
    try:
        identity = sync_office.ensure_office_identity(app, conn, "Front Desk")
        assert sync_identity.load_device_identity(app).device_id == identity.device_id
        members = sync_admin.list_members(conn, office.admin_pubkey)
        assert [(m["device_id"], m["role"], m["token_letter"], m["name"]) for m in members] == [
            (identity.device_id, sync_admin.ROLE_ADMIN, "A", "Front Desk")]
        assert sync_admin.get_office_admin(conn, office.admin_pubkey)["device_id"] == identity.device_id
    finally:
        conn.close()


def test_ensure_office_identity_is_idempotent(tmp_path):
    app, office, hex_key = _converted_pc(tmp_path)
    conn = _open_db_factory(app / sync_office.MASTER_DB_NAME, hex_key)()
    try:
        first = sync_office.ensure_office_identity(app, conn, "Front Desk")
        second = sync_office.ensure_office_identity(app, conn, "Another Name")
        assert second.device_id == first.device_id
        members = sync_admin.list_members(conn, office.admin_pubkey)
        assert len(members) == 1 and members[0]["name"] == "Front Desk" and members[0]["rev"] == 1
    finally:
        conn.close()


def test_ensure_office_identity_leaves_a_new_office_unchanged(tmp_path):
    admin_dir, office, identity = _make_admin(tmp_path)
    hex_key = sera_keys.dek_hex(sera_keys.load_dek(admin_dir))
    conn = _open_db_factory(admin_dir / sync_office.MASTER_DB_NAME, hex_key)()
    try:
        before = sync_admin.list_members(conn, office.admin_pubkey)
        again = sync_office.ensure_office_identity(admin_dir, conn, "Renamed")
        assert again.device_id == identity.device_id
        assert sync_admin.list_members(conn, office.admin_pubkey) == before
    finally:
        conn.close()


def test_ensure_office_identity_writes_no_records_without_the_admin_key(tmp_path):
    app, office, hex_key = _converted_pc(tmp_path, with_admin_key=False)
    conn = _open_db_factory(app / sync_office.MASTER_DB_NAME, hex_key)()
    try:
        identity = sync_office.ensure_office_identity(app, conn, "Back Office")
        assert identity is not None
        assert conn.execute("SELECT COUNT(*) FROM %s" % sync_admin.MEMBERS_TABLE).fetchone()[0] == 0
    finally:
        conn.close()
