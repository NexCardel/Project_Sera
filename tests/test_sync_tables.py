"""Tests for Sera Sync v3 P3-2: gid columns, back-fill, deterministic seed gids, _sync_* tables.

Acceptance criteria from blueprint §5 P3-2:
  - migration runs twice without error;
  - every replicated row has a unique gid;
  - seeded rows have identical gids on two fresh DBs.

Rules:
  - tmp_path only (§0 rule 2).
  - No PySide6 import in sync_*.py (§0 rule 7).
"""

import ast
import re
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

import sync_schema
from sync_schema import MASTER_DB, RAW_DB, SERA_NS, seed_gid

HEX32_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def _scratch_db(tmp_path, name="master.db", raw_name="rawPayload.db"):
    import security
    from database import SeraDatabase

    tmp_path = Path(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    db_path = str(tmp_path / name)
    raw_path = str(tmp_path / raw_name)
    salt_path = str(tmp_path / "test.salt")
    security.generate_and_save_salt(salt_path)
    salt = security.load_salt(salt_path)
    hex_key = security.derive_key_hex("testpass123", salt)
    return SeraDatabase(db_path, hex_key, raw_db_path=raw_path, defer_startup_maintenance=True)


def test_no_pyside6_import():
    """Verify sync_tables.py does not import PySide6 (blueprint §0 rule 7)."""
    import sync_tables as mod

    with open(mod.__file__, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "PySide6" not in alias.name
        elif isinstance(node, ast.ImportFrom):
            assert not (node.module and "PySide6" in node.module)


def test_sera_ns_and_seed_gid_deterministic():
    """Verify SERA_NS is a fixed UUID and seed_gid produces deterministic 32-char lowercase hex."""
    assert isinstance(SERA_NS, uuid.UUID)
    g1 = seed_gid("staff_users", "User 1")
    g2 = seed_gid("staff_users", "User 1")
    g_other = seed_gid("staff_users", "User 2")
    assert g1 == g2
    assert g1 != g_other
    assert HEX32_PATTERN.match(g1)
    assert HEX32_PATTERN.match(g_other)


def test_seeded_rows_have_identical_gids_on_two_fresh_dbs(tmp_path):
    """Acceptance criterion: seeded rows have identical gids on two fresh DBs."""
    db1 = _scratch_db(tmp_path / "office1", "master.db", "rawPayload.db")
    db2 = _scratch_db(tmp_path / "office2", "master.db", "rawPayload.db")

    # 1. Staff users
    with db1._connect() as c1, db2._connect() as c2:
        staff1 = c1.execute("SELECT name, gid FROM staff_users ORDER BY id").fetchall()
        staff2 = c2.execute("SELECT name, gid FROM staff_users ORDER BY id").fetchall()

        assert len(staff1) == 6
        assert len(staff2) == 6
        for (n1, g1), (n2, g2) in zip(staff1, staff2):
            assert n1 == n2
            assert g1 == g2
            assert g1 == seed_gid("staff_users", n1)
            assert HEX32_PATTERN.match(g1)

        # 2. MCL columns
        mcl1 = c1.execute("SELECT label, gid FROM mcl_columns ORDER BY id").fetchall()
        mcl2 = c2.execute("SELECT label, gid FROM mcl_columns ORDER BY id").fetchall()

        assert len(mcl1) > 0
        assert len(mcl1) == len(mcl2)
        for (l1, g1), (l2, g2) in zip(mcl1, mcl2):
            assert l1 == l2
            assert g1 == g2
            assert g1 == seed_gid("mcl_columns", l1)
            assert HEX32_PATTERN.match(g1)

        # 3. Services
        svc1 = c1.execute("SELECT name, gid FROM services ORDER BY id").fetchall()
        svc2 = c2.execute("SELECT name, gid FROM services ORDER BY id").fetchall()

        assert len(svc1) > 0
        assert len(svc1) == len(svc2)
        for (s1, g1), (s2, g2) in zip(svc1, svc2):
            assert s1 == s2
            assert g1 == g2
            assert g1 == seed_gid("services", s1)
            assert HEX32_PATTERN.match(g1)


def test_migration_runs_twice_without_error(tmp_path):
    """Acceptance criterion: migration runs twice without error (idempotent)."""
    import sync_tables

    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))

    # Create legacy schema without gid columns or sync tables
    conn.execute("""
        CREATE TABLE clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id_token TEXT,
            notes TEXT,
            created_at TEXT,
            updated_at TEXT,
            is_archived INTEGER DEFAULT 0
        );
    """)
    conn.execute("INSERT INTO clients (client_id_token, notes) VALUES ('1', 'Legacy client 1')")
    conn.execute("INSERT INTO clients (client_id_token, notes) VALUES ('2', 'Legacy client 2')")

    conn.execute("""
        CREATE TABLE mcl_columns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            field_type TEXT DEFAULT 'text',
            sort_order INTEGER DEFAULT 0
        );
    """)
    conn.execute("INSERT INTO mcl_columns (label) VALUES ('No.')")
    conn.execute("INSERT INTO mcl_columns (label) VALUES ('NAME OF COMPANY')")

    conn.execute("""
        CREATE TABLE services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );
    """)
    conn.execute("INSERT INTO services (name) VALUES ('GST')")
    conn.execute("INSERT INTO services (name) VALUES ('Custom Service')")

    conn.execute("""
        CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            client_id INTEGER,
            service_id INTEGER,
            detail TEXT
        );
    """)
    conn.execute("INSERT INTO audit_log (ts, actor, action) VALUES ('2026-09-24T00:00:00Z', 'Admin', 'test')")

    conn.execute("""
        CREATE TABLE staff_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            alias TEXT
        );
    """)
    conn.execute("INSERT INTO staff_users (name) VALUES ('User 1')")
    conn.execute("INSERT INTO staff_users (name) VALUES ('User 2')")

    conn.commit()

    # First run of migration
    sync_tables.ensure_sync_infrastructure(conn, MASTER_DB, device_id="testdev01")
    conn.commit()

    # Verify gid columns and backfill
    for tbl in ["clients", "mcl_columns", "services", "audit_log", "staff_users"]:
        col_names = {r[1] for r in conn.execute(f"PRAGMA table_info({tbl})").fetchall()}
        assert "gid" in col_names, f"{tbl} missing gid column after run 1"

        null_gids = conn.execute(f"SELECT COUNT(*) FROM {tbl} WHERE gid IS NULL OR gid = ''").fetchone()[0]
        assert null_gids == 0, f"{tbl} has NULL/empty gid after run 1"

    # Known seeds must match seed_gid
    user1_gid = conn.execute("SELECT gid FROM staff_users WHERE name = 'User 1'").fetchone()[0]
    assert user1_gid == seed_gid("staff_users", "User 1")

    gst_gid = conn.execute("SELECT gid FROM services WHERE name = 'GST'").fetchone()[0]
    assert gst_gid == seed_gid("services", "GST")

    # Second run of migration — MUST succeed with no error and preserve existing gids
    sync_tables.ensure_sync_infrastructure(conn, MASTER_DB, device_id="testdev01")
    conn.commit()

    assert conn.execute("SELECT gid FROM staff_users WHERE name = 'User 1'").fetchone()[0] == user1_gid
    assert conn.execute("SELECT gid FROM services WHERE name = 'GST'").fetchone()[0] == gst_gid

    conn.close()


def test_every_replicated_row_has_a_unique_gid(tmp_path):
    """Acceptance criterion: every replicated row has a unique gid."""
    db = _scratch_db(tmp_path)

    # Find internal PK column (PAN)
    pk_cols = [c for c in db.get_mcl_columns() if c.get("is_internal_pk")]
    pan_col_id = pk_cols[0]["id"] if pk_cols else 1

    with db._connect() as conn:
        # Add 10 clients
        cids = []
        for i in range(10):
            cid = db.add_client(values={pan_col_id: f"ABCDE{i:04d}F"}, notes=f"Client {i}", service_ids=[], actor="Staff")
            cids.append(cid)

        # Add custom service and staff user
        conn.execute("INSERT INTO services (name) VALUES ('Service Extra')")
        conn.execute("INSERT INTO staff_users (name) VALUES ('Extra Staff')")
        conn.execute("INSERT INTO audit_log (ts, actor, action) VALUES ('2026-09-24T12:00:00Z', 'Staff', 'test')")

    with db._connect_raw() as r_conn:
        # Add tracker_dump entries
        for i in range(5):
            r_conn.execute("""
                INSERT INTO tracker_dump (portal, period_label, arn_number, created_at)
                VALUES ('GST', '092026', ?, '2026-09-24T12:00:00Z')
            """, (f"ARN{i}",))

    # Verify all gid tables
    with db._connect() as conn:
        for tbl in ["clients", "mcl_columns", "services", "audit_log", "staff_users"]:
            rows = conn.execute(f"SELECT id, gid FROM {tbl}").fetchall()
            assert len(rows) > 0, f"{tbl} has no rows"
            gids = [r[1] for r in rows]
            for g in gids:
                assert g is not None, f"NULL gid in {tbl}"
                assert HEX32_PATTERN.match(g), f"Invalid gid format in {tbl}: {g}"
            assert len(gids) == len(set(gids)), f"Duplicate gids found within {tbl}"

    with db._connect_raw() as r_conn:
        rows = r_conn.execute("SELECT id, gid FROM tracker_dump").fetchall()
        assert len(rows) >= 5
        gids = [r[1] for r in rows]
        for g in gids:
            assert g is not None and HEX32_PATTERN.match(g)
        assert len(gids) == len(set(gids)), "Duplicate gids in tracker_dump"


def test_unique_gid_index_enforces_uniqueness(tmp_path):
    """Verify ux_t_gid unique index rejects duplicate gids."""
    db = _scratch_db(tmp_path)
    with db._connect() as conn:
        existing_gid = conn.execute("SELECT gid FROM clients LIMIT 1").fetchone()
        if existing_gid is None:
            conn.execute("INSERT INTO clients (notes, is_archived, created_at, updated_at) VALUES ('n', 0, 'ts', 'ts')")
            existing_gid = conn.execute("SELECT gid FROM clients LIMIT 1").fetchone()
        gid_val = existing_gid[0]

        try:
            import sqlcipher3.dbapi2 as sqlcipher_dbapi
            IntegrityErrors = (sqlite3.IntegrityError, sqlcipher_dbapi.IntegrityError)
        except Exception:
            IntegrityErrors = (sqlite3.IntegrityError,)

        with pytest.raises(IntegrityErrors):
            conn.execute(
                "INSERT INTO clients (notes, is_archived, created_at, updated_at, gid) VALUES ('dup', 0, 'ts', 'ts', ?)",
                (gid_val,),
            )


def test_trigger_generates_random_gid_when_omitted(tmp_path):
    """Verify _gid_ai_<t> trigger automatically assigns lower(hex(randomblob(16))) when gid is NULL."""
    db = _scratch_db(tmp_path)
    with db._connect() as conn:
        cur = conn.execute(
            "INSERT INTO clients (notes, is_archived, created_at, updated_at) VALUES ('Trigger test', 0, 'ts', 'ts')"
        )
        rowid = cur.lastrowid
        gid = conn.execute("SELECT gid FROM clients WHERE rowid = ?", (rowid,)).fetchone()[0]
        assert gid is not None
        assert HEX32_PATTERN.match(gid)


def test_trigger_preserves_explicit_gid(tmp_path):
    """Verify _gid_ai_<t> trigger does not overwrite an explicitly provided gid."""
    db = _scratch_db(tmp_path)
    custom_gid = "a" * 32
    with db._connect() as conn:
        cur = conn.execute(
            "INSERT INTO clients (notes, is_archived, created_at, updated_at, gid) VALUES ('Explicit test', 0, 'ts', 'ts', ?)",
            (custom_gid,),
        )
        rowid = cur.lastrowid
        gid = conn.execute("SELECT gid FROM clients WHERE rowid = ?", (rowid,)).fetchone()[0]
        assert gid == custom_gid


def test_sync_tables_created_in_both_databases(tmp_path):
    """Verify all 11 _sync_* tables exist with expected schema in master.db and rawPayload.db."""
    import sync_tables

    db = _scratch_db(tmp_path)
    expected_tables = {
        "_sync_meta",
        "_sync_flags",
        "_sync_pending",
        "_sync_changes",
        "_sync_clock",
        "_sync_tombstones",
        "_sync_vector",
        "_sync_peer_vectors",
        "_sync_parked",
        "_sync_conflicts",
        "_sync_alias",
    }

    assert expected_tables == sync_tables.SYNC_TABLE_NAMES

    for db_label, conn in [("master", db._connect()), ("raw", db._connect_raw())]:
        with conn as c:
            live = {
                r[0]
                for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            for tbl in expected_tables:
                assert tbl in live, f"{tbl} missing in {db_label}"

            # Check _sync_flags applying initial value
            flag_val = c.execute("SELECT value FROM _sync_flags WHERE name = 'applying'").fetchone()
            assert flag_val is not None
            assert flag_val[0] == 0

            # Check _sync_meta default keys
            meta_rows = dict(c.execute("SELECT key, value FROM _sync_meta").fetchall())
            assert "schema_version" in meta_rows
            assert "mode" in meta_rows
            assert "next_seq" in meta_rows
            assert meta_rows["mode"] in ("off", "shadow", "live")


def test_stream_id_and_device_id_in_sync_meta(tmp_path):
    """Verify origin stream id is f'{device_id}:m' for master and f'{device_id}:r' for raw."""
    import sync_tables

    device_id = "abcd1234abcd1234abcd1234abcd1234"
    assert sync_tables.stream_id_for(device_id, MASTER_DB) == f"{device_id}:m"
    assert sync_tables.stream_id_for(device_id, RAW_DB) == f"{device_id}:r"

    db = _scratch_db(tmp_path)
    with db._connect() as c:
        sync_tables.set_sync_device_id(c, MASTER_DB, device_id)
        meta = dict(c.execute("SELECT key, value FROM _sync_meta").fetchall())
        assert meta.get("device_id") == device_id
        assert meta.get("stream_id") == f"{device_id}:m"

    with db._connect_raw() as c:
        sync_tables.set_sync_device_id(c, RAW_DB, device_id)
        meta = dict(c.execute("SELECT key, value FROM _sync_meta").fetchall())
        assert meta.get("device_id") == device_id
        assert meta.get("stream_id") == f"{device_id}:r"


def test_non_gid_replicated_tables_do_not_have_gid(tmp_path):
    """Verify tables whose row key is natural/composite do NOT get a gid column."""
    db = _scratch_db(tmp_path)
    with db._connect() as c:
        for tbl in ["client_values", "client_services", "cell_formatting", "app_settings"]:
            cols = {r[1] for r in c.execute(f"PRAGMA table_info({tbl})").fetchall()}
            assert "gid" not in cols, f"{tbl} should not have a gid column"

    with db._connect_raw() as c:
        for tbl in ["sdc_session_timelines", "client_raw_containers"]:
            cols = {r[1] for r in c.execute(f"PRAGMA table_info({tbl})").fetchall()}
            assert "gid" not in cols, f"{tbl} should not have a gid column"


def test_backfill_duplicate_labels_and_names_does_not_crash(tmp_path):
    """B1 regression: duplicate label in mcl_columns or duplicate name in services/staff_users
    must not violate the unique index on gid during back-fill. The lowest-id row gets seed_gid,
    and subsequent duplicate rows get unique random gids.
    """
    import sync_tables
    db_file = tmp_path / "test_dup.db"
    conn = sqlite3.connect(str(db_file))

    # Pre-create tables without gid column, with duplicate values
    conn.execute("""
        CREATE TABLE mcl_columns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT
        );
    """)
    conn.execute("INSERT INTO mcl_columns (id, label) VALUES (1, 'PAN')")
    conn.execute("INSERT INTO mcl_columns (id, label) VALUES (2, 'PAN')")
    conn.execute("INSERT INTO mcl_columns (id, label) VALUES (3, 'GSTIN')")

    conn.execute("""
        CREATE TABLE services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT
        );
    """)
    conn.execute("INSERT INTO services (id, name) VALUES (1, 'GST')")
    conn.execute("INSERT INTO services (id, name) VALUES (2, 'GST')")

    conn.execute("""
        CREATE TABLE staff_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT
        );
    """)
    conn.execute("INSERT INTO staff_users (id, name) VALUES (1, 'User 1')")
    conn.execute("INSERT INTO staff_users (id, name) VALUES (2, 'User 1')")

    # Run ensure_gid_columns -- must not crash with UNIQUE constraint failed
    processed = sync_tables.ensure_gid_columns(conn, MASTER_DB)
    assert "mcl_columns" in processed
    assert "services" in processed
    assert "staff_users" in processed

    # Verify mcl_columns
    mcl_rows = conn.execute("SELECT id, label, gid FROM mcl_columns ORDER BY id").fetchall()
    assert len(mcl_rows) == 3
    pan_seed = seed_gid("mcl_columns", "PAN")
    assert mcl_rows[0] == (1, "PAN", pan_seed)
    assert mcl_rows[1][0] == 2 and mcl_rows[1][1] == "PAN"
    assert mcl_rows[1][2] != pan_seed
    assert HEX32_PATTERN.match(mcl_rows[1][2])
    assert mcl_rows[2] == (3, "GSTIN", seed_gid("mcl_columns", "GSTIN"))

    # Verify services
    svc_rows = conn.execute("SELECT id, name, gid FROM services ORDER BY id").fetchall()
    assert len(svc_rows) == 2
    gst_seed = seed_gid("services", "GST")
    assert svc_rows[0] == (1, "GST", gst_seed)
    assert svc_rows[1][0] == 2 and svc_rows[1][1] == "GST"
    assert svc_rows[1][2] != gst_seed
    assert HEX32_PATTERN.match(svc_rows[1][2])

    # Verify staff_users
    staff_rows = conn.execute("SELECT id, name, gid FROM staff_users ORDER BY id").fetchall()
    assert len(staff_rows) == 2
    u1_seed = seed_gid("staff_users", "User 1")
    assert staff_rows[0] == (1, "User 1", u1_seed)
    assert staff_rows[1][0] == 2 and staff_rows[1][1] == "User 1"
    assert staff_rows[1][2] != u1_seed
    assert HEX32_PATTERN.match(staff_rows[1][2])

    # Verify unique index exists and was not aborted
    for tbl in ["mcl_columns", "services", "staff_users"]:
        indices = conn.execute(f"PRAGMA index_list({tbl})").fetchall()
        idx_names = {r[1] for r in indices}
        assert f"ux_{tbl}_gid" in idx_names

    # Idempotent second run also succeeds
    processed2 = sync_tables.ensure_gid_columns(conn, MASTER_DB)
    assert set(processed2) == set(processed)
    conn.close()


def test_load_device_id_cheap(tmp_path):
    """Verify load_device_id_cheap reads device_id from office.json and device_cert.pem."""
    import sync_identity
    import sera_keys

    # 1. Non-existent returns None
    assert sync_identity.load_device_id_cheap(tmp_path) is None

    # 2. Generates identity, extracts from device_cert.pem
    ident = sync_identity.ensure_device_identity(tmp_path)
    extracted = sync_identity.load_device_id_cheap(tmp_path)
    assert extracted == ident.device_id

    # 3. If office.json exists with device_id, it is preferred
    sub_dir = tmp_path / "office_sub"
    sub_dir.mkdir()
    keys_dir = sub_dir / "keys"
    keys_dir.mkdir()
    (keys_dir / "office.json").write_text(
        '{"format": 1, "office_id": "off1", "office_name": "Test Office", "key_id": "key1", "device_id": "customdevid123456"}',
        encoding="utf-8",
    )
    assert sync_identity.load_device_id_cheap(sub_dir) == "customdevid123456"


def test_fresh_db_seeded_from_ini_with_duplicate_label_succeeds(tmp_path):
    """Verify that a fresh DB initialized from a settings.ini containing duplicate labels
    does not crash on unique index creation, leaving the duplicate to get a random gid.
    """
    ini_content = """[AppSettings]
theme = Light

[MCL_Columns]
col_1 = PAN | field_type=text | is_identity=1
col_2 = PAN | field_type=text | is_identity=0
col_3 = GSTIN | field_type=text

[Services]
s_1 = GST | login_link=https://example.com
s_2 = Income Tax | login_link=https://example.com/2
"""
    ini_path = tmp_path / "settings.ini"
    ini_path.write_text(ini_content, encoding="utf-8")

    import security
    from database import SeraDatabase

    db_path = str(tmp_path / "master.db")
    raw_path = str(tmp_path / "rawPayload.db")
    salt_path = str(tmp_path / "test.salt")
    security.generate_and_save_salt(salt_path)
    salt = security.load_salt(salt_path)
    hex_key = security.derive_key_hex("testpass123", salt)

    # Instantiate SeraDatabase with the custom settings.ini in the directory
    db = SeraDatabase(db_path, hex_key, raw_db_path=raw_path, defer_startup_maintenance=True)

    with db._connect() as conn:
        cols = conn.execute("SELECT id, label, gid FROM mcl_columns ORDER BY id").fetchall()
        assert len(cols) == 3
        assert cols[0][1] == "PAN"
        assert cols[1][1] == "PAN"
        assert cols[0][2] == seed_gid("mcl_columns", "PAN")
        assert cols[1][2] != cols[0][2]
        assert HEX32_PATTERN.match(cols[1][2])

        svcs = conn.execute("SELECT id, name, gid FROM services ORDER BY id").fetchall()
        assert len(svcs) == 2
        assert svcs[0][1] == "GST"
        assert svcs[1][1] == "Income Tax"
        assert svcs[0][2] == seed_gid("services", "GST")
        assert svcs[1][2] == seed_gid("services", "Income Tax")
        assert HEX32_PATTERN.match(svcs[0][2])
        assert HEX32_PATTERN.match(svcs[1][2])


