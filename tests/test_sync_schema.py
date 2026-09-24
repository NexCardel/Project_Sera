"""Tests for sync_schema.py (Sera Sync v3, WP P3-1): table/setting classification registry.

Everything lives under pytest's tmp_path; the real data folder is never touched (§0 rule 2).
"""

import sqlite3

import pytest

import sync_schema
from sync_schema import (
    ADMIN_LWW,
    APPEND,
    LOCAL,
    LWW,
    MASTER_DB,
    RAW_DB,
    SET,
    TableSpec,
    VALID_MODES,
)


def _scratch_db(tmp_path, name="test_master.db"):
    import security
    from database import SeraDatabase

    db_path = str(tmp_path / name)
    salt_path = str(tmp_path / "test.salt")
    security.generate_and_save_salt(salt_path)
    salt = security.load_salt(salt_path)
    hex_key = security.derive_key_hex("testpass123", salt)
    return SeraDatabase(db_path, hex_key, defer_startup_maintenance=True)


def test_no_pyside6_import():
    import ast
    import sync_schema as mod

    with open(mod.__file__, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "PySide6" not in alias.name
        elif isinstance(node, ast.ImportFrom):
            assert not (node.module and "PySide6" in node.module)


def test_registry_covers_expected_tables():
    expected_master = {
        "clients", "client_values", "mcl_columns", "services", "client_services",
        "cell_formatting", "audit_log", "staff_users", "app_settings",
        "client_activity_stats", "client_recent_activity",
    }
    expected_raw = {
        "tracker_dump", "sdc_session_timelines", "client_raw_containers",
        "client_container_notes",
    }

    assert {t.name for t in sync_schema.tables_for(MASTER_DB)} == expected_master
    assert {t.name for t in sync_schema.tables_for(RAW_DB)} == expected_raw


def test_every_spec_has_a_valid_mode_and_row_key():
    for spec in sync_schema.REGISTRY.values():
        assert spec.mode in VALID_MODES
        if spec.mode != LOCAL:
            assert spec.row_key, f"{spec.name} is replicated but has no row_key"


def test_modes_match_blueprint_section_5():
    # client_raw_containers: back to local (round 2 of review) — syncing the
    # whole row as lww was unsafe: re_resolve_all_tracker_dumps()'s DELETE +
    # rebuild would tombstone every container under P3-3/P3-4's rules. Owner
    # decision 2026-09-24 (final): split instead — client_raw_containers stays
    # local, and only the hand-typed notes/screenshot_path sync, through the
    # new (not-yet-created) client_container_notes table. See both TableSpecs'
    # notes for the full history.
    expect = {
        "clients": LWW,
        "client_values": LWW,
        "mcl_columns": LWW,
        "services": LWW,
        "client_services": SET,
        "cell_formatting": LWW,
        "audit_log": APPEND,
        "staff_users": ADMIN_LWW,
        "app_settings": LWW,
        "client_activity_stats": LOCAL,
        "client_recent_activity": LOCAL,
        "tracker_dump": LWW,
        "sdc_session_timelines": LWW,
        "client_raw_containers": LOCAL,
        "client_container_notes": LWW,
    }
    for name, mode in expect.items():
        assert sync_schema.get(name).mode == mode, name


def test_natural_merge_columns():
    assert sync_schema.get("services").natural_merge_on == "name"
    assert sync_schema.get("staff_users").natural_merge_on == "name"
    assert sync_schema.get("clients").natural_merge_on is None


def test_fk_columns_match_blueprint_section_5():
    assert dict(sync_schema.get("client_values").fk) == {
        "client_id": "clients", "column_id": "mcl_columns",
    }
    assert dict(sync_schema.get("services").fk) == {
        "userid_column_id": "mcl_columns", "password_column_id": "mcl_columns",
    }
    assert dict(sync_schema.get("client_services").fk) == {
        "client_id": "clients", "service_id": "services",
    }
    # column_key is str(mcl_columns.id), except the literal "services" (no
    # mcl_columns row) — see the cell_formatting TableSpec's notes.
    assert dict(sync_schema.get("cell_formatting").fk) == {
        "client_id": "clients", "column_key": "mcl_columns",
    }
    assert dict(sync_schema.get("audit_log").fk) == {
        "client_id": "clients", "service_id": "services",
    }
    assert dict(sync_schema.get("tracker_dump").fk) == {
        "client_id": "clients", "service_id": "services",
    }
    assert dict(sync_schema.get("sdc_session_timelines").fk) == {"client_id": "clients"}
    assert dict(sync_schema.get("client_raw_containers").fk) == {"client_id": "clients"}
    assert dict(sync_schema.get("client_container_notes").fk) == {"client_id": "clients"}


def test_fk_dict_is_immutable():
    spec = sync_schema.get("clients")
    with pytest.raises(TypeError):
        sync_schema.get("client_values").fk["client_id"] = "somewhere_else"
    assert spec.fk == {}


def test_registry_is_immutable():
    with pytest.raises(TypeError):
        sync_schema.REGISTRY["clients"] = None


def test_replicated_tables_excludes_local():
    master_replicated = {t.name for t in sync_schema.replicated_tables_for(MASTER_DB)}
    assert "client_activity_stats" not in master_replicated
    assert "client_recent_activity" not in master_replicated
    assert "clients" in master_replicated

    raw_replicated = {t.name for t in sync_schema.replicated_tables_for(RAW_DB)}
    assert "client_raw_containers" not in raw_replicated
    assert "client_container_notes" in raw_replicated
    assert "tracker_dump" in raw_replicated


def test_pending_tables_for_lists_not_yet_created_tables():
    pending = {t.name for t in sync_schema.pending_tables_for(RAW_DB)}
    assert pending == {"client_container_notes"}
    assert sync_schema.get("client_container_notes").pending_creation is True
    assert sync_schema.get("client_raw_containers").pending_creation is False
    assert sync_schema.pending_tables_for(MASTER_DB) == []


def test_duplicate_registration_rejected():
    with pytest.raises(ValueError):
        sync_schema._register(TableSpec("clients", MASTER_DB, LWW, row_key=("gid",)))


def test_local_table_needs_no_row_key():
    TableSpec("scratch_local", MASTER_DB, LOCAL)  # must not raise


def test_replicated_table_needs_row_key():
    with pytest.raises(ValueError):
        TableSpec("scratch_lww", MASTER_DB, LWW)


def test_unknown_mode_rejected():
    with pytest.raises(ValueError):
        TableSpec("scratch", MASTER_DB, "not_a_mode", row_key=("gid",))


def test_key_columns_combines_row_key_and_fk():
    spec = sync_schema.get("client_values")
    assert spec.key_columns == {"client_id", "column_id"}
    spec = sync_schema.get("cell_formatting")
    assert spec.key_columns == {"client_id", "column_key"}


def test_expected_live_columns_excludes_gid():
    spec = sync_schema.get("clients")
    assert spec.key_columns == {"gid"}
    assert spec.expected_live_columns == set()

    spec = sync_schema.get("client_values")
    assert spec.expected_live_columns == {"client_id", "column_id"}


def test_verify_against_live_schema_accepts_real_master_db(tmp_path):
    db = _scratch_db(tmp_path)
    with db._connect() as conn:
        sync_schema.verify_against_live_schema(conn, MASTER_DB)


def test_verify_against_live_schema_accepts_real_raw_db(tmp_path):
    db = _scratch_db(tmp_path)
    with db._connect_raw() as conn:
        # client_container_notes is pending_creation=True: doesn't exist in a
        # real rawPayload.db yet, and that must not be flagged as a problem.
        live = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "client_container_notes" not in live
        sync_schema.verify_against_live_schema(conn, RAW_DB)


def test_verify_against_live_schema_flags_unclassified_table():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE some_new_table (id INTEGER PRIMARY KEY)")
    with pytest.raises(AssertionError, match="some_new_table"):
        sync_schema.verify_against_live_schema(conn, MASTER_DB)


def test_verify_against_live_schema_flags_missing_column():
    """A table present under a registered name but missing a column the
    registry claims (row_key or fk) must be reported, not silently accepted —
    this is the check that would have caught cell_formatting.column_key
    or client_raw_containers.notes being misdescribed. gid-mode tables (e.g.
    'clients') are exempt for their gid column: P3-2 hasn't added it yet."""
    conn = sqlite3.connect(":memory:")
    # cell_formatting registered with fk column_key -> mcl_columns: missing it must fail.
    conn.execute("CREATE TABLE cell_formatting (client_id INTEGER)")
    with pytest.raises(AssertionError, match="cell_formatting"):
        sync_schema.verify_against_live_schema(conn, MASTER_DB)


def test_verify_against_live_schema_does_not_require_gid_yet():
    """P3-1 ships before P3-2 adds gid columns, so a gid-mode table without a
    gid column yet must still pass."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE clients (id INTEGER PRIMARY KEY)")  # no gid column
    sync_schema.verify_against_live_schema(conn, MASTER_DB)


def test_verify_against_live_schema_ignores_sqlite_internal_tables():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE clients (id INTEGER PRIMARY KEY AUTOINCREMENT)")
    conn.execute("INSERT INTO clients DEFAULT VALUES")  # AUTOINCREMENT creates sqlite_sequence
    assert "sqlite_sequence" in {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    sync_schema.verify_against_live_schema(conn, MASTER_DB)
