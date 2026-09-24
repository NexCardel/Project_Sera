"""Sera Sync v3 — gid columns, back-fill, deterministic seed gids, _sync_* tables (blueprint §5, WP P3-2).

This module manages the schema foundations for change replication:
  - Adding `gid TEXT` and unique index `ux_t_gid` to every replicated table whose row key is `gid`;
  - Backfilling existing rows with deterministic seed gids or `lower(hex(randomblob(16)))`;
  - Creating `_gid_ai_<t>` triggers to assign gids on insert when omitted;
  - Creating the 11 `_sync_*` tables in both `master.db` and `rawPayload.db`.

No PySide6 import (blueprint §0 rule 7).
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Optional

import sync_schema
from sync_schema import MASTER_DB, RAW_DB, SERA_NS, seed_gid

_log = logging.getLogger("sera.sync.tables")

# Set of all 11 internal sync v3 tables created in each database
SYNC_TABLE_NAMES = frozenset({
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
})

# Canonical names for default seeded staff users (database.py:898-903)
STAFF_SEED_NAMES = tuple(f"User {i}" for i in range(1, 7))

# Canonical default MCL column labels (database.py:1068-1080)
DEFAULT_MCL_LABELS = (
    "No.",
    "NAME OF COMPANY",
    "NAME OF PROPRIETOR",
    "GSTIN",
    "PAN",
    "PH. NO.",
    "USER ID",
    "EMAIL",
    "GST_Password",
    "IT_Password",
    "Email_Password",
)

# Canonical default service names (database.py:1090-1094)
DEFAULT_SERVICE_NAMES = ("GST", "Income Tax", "Email")


def stream_id_for(device_id: str, db_name: str) -> str:
    """Computes the origin stream ID for a given database file (blueprint §5 P3-2).

    master.db:     f"{device_id}:m"
    rawPayload.db: f"{device_id}:r"
    """
    suffix = "m" if db_name in (MASTER_DB, "master") else "r"
    return f"{device_id}:{suffix}"


def set_sync_device_id(conn: sqlite3.Connection, db_name: str, device_id: str) -> None:
    """Updates device_id and stream_id in _sync_meta."""
    st_id = stream_id_for(device_id, db_name)
    conn.execute(
        "INSERT INTO _sync_meta (key, value) VALUES ('device_id', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (device_id,),
    )
    conn.execute(
        "INSERT INTO _sync_meta (key, value) VALUES ('stream_id', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (st_id,),
    )


def ensure_gid_columns(conn: sqlite3.Connection, db_name: str) -> list[str]:
    """Ensures every replicated table in `db_name` with row_key `('gid',)` has:
      1. `gid TEXT` column;
      2. Unique index `ux_<t>_gid` on `t(gid)`;
      3. Backfilled deterministic gids for known seeds and randomblob(16) for others;
      4. Trigger `_gid_ai_<t>` assigning randomblob(16) when NEW.gid IS NULL.

    Returns the list of table names processed.
    """
    processed = []
    # Identify non-local tables whose row key is 'gid'
    replicated = sync_schema.replicated_tables_for(db_name)
    gid_tables = [t.name for t in replicated if t.row_key == ("gid",)]

    live_tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    for tbl in gid_tables:
        if tbl not in live_tables:
            continue

        # 1. Add gid column if missing
        col_info = conn.execute(f"PRAGMA table_info({tbl})").fetchall()
        existing_cols = {c[1] for c in col_info}
        if "gid" not in existing_cols:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN gid TEXT")

        # 2. Deterministic seed backfill for known seed tables:
        # Only assign seed_gid to the lowest-id matching row if that seed_gid is not
        # already assigned elsewhere in the table. Any duplicate rows remain unassigned
        # and are backfilled with random gids in step 3 below.
        if tbl == "staff_users":
            for sname in STAFF_SEED_NAMES:
                sgid = seed_gid("staff_users", sname)
                already = conn.execute("SELECT 1 FROM staff_users WHERE gid = ?", (sgid,)).fetchone()
                if not already:
                    min_row = conn.execute(
                        "SELECT MIN(id) FROM staff_users WHERE name = ? AND (gid IS NULL OR gid = '')",
                        (sname,),
                    ).fetchone()
                    if min_row and min_row[0] is not None:
                        conn.execute("UPDATE staff_users SET gid = ? WHERE id = ?", (sgid, min_row[0]))
        elif tbl == "mcl_columns":
            for lbl in DEFAULT_MCL_LABELS:
                sgid = seed_gid("mcl_columns", lbl)
                already = conn.execute("SELECT 1 FROM mcl_columns WHERE gid = ?", (sgid,)).fetchone()
                if not already:
                    min_row = conn.execute(
                        "SELECT MIN(id) FROM mcl_columns WHERE label = ? AND (gid IS NULL OR gid = '')",
                        (lbl,),
                    ).fetchone()
                    if min_row and min_row[0] is not None:
                        conn.execute("UPDATE mcl_columns SET gid = ? WHERE id = ?", (sgid, min_row[0]))
        elif tbl == "services":
            for sname in DEFAULT_SERVICE_NAMES:
                sgid = seed_gid("services", sname)
                already = conn.execute("SELECT 1 FROM services WHERE gid = ?", (sgid,)).fetchone()
                if not already:
                    min_row = conn.execute(
                        "SELECT MIN(id) FROM services WHERE name = ? AND (gid IS NULL OR gid = '')",
                        (sname,),
                    ).fetchone()
                    if min_row and min_row[0] is not None:
                        conn.execute("UPDATE services SET gid = ? WHERE id = ?", (sgid, min_row[0]))

        # 3. Backfill any remaining rows with random gids
        conn.execute(
            f"UPDATE {tbl} SET gid = lower(hex(randomblob(16))) WHERE gid IS NULL OR gid = ''"
        )

        # 4. Create unique index on gid (after all rows have valid non-null unique gids)
        conn.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS ux_{tbl}_gid ON {tbl}(gid)")

        # 5. Add insert trigger for automatic gid generation when omitted
        conn.execute(f"""
            CREATE TRIGGER IF NOT EXISTS _gid_ai_{tbl} AFTER INSERT ON {tbl}
            WHEN NEW.gid IS NULL
            BEGIN
                UPDATE {tbl} SET gid = lower(hex(randomblob(16))) WHERE rowid = NEW.rowid;
            END;
        """)

        processed.append(tbl)

    return processed


def ensure_sync_tables(conn: sqlite3.Connection, db_name: str, device_id: Optional[str] = None) -> None:
    """Creates the 11 _sync_* internal tables in the given database connection."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _sync_meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS _sync_flags (
            name  TEXT PRIMARY KEY,
            value INTEGER
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS _sync_pending (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            tbl      TEXT,
            rid      INTEGER,
            op       TEXT,
            key_json TEXT
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS _sync_changes (
            seq        INTEGER PRIMARY KEY AUTOINCREMENT,
            origin     TEXT,
            origin_seq INTEGER,
            hlc        TEXT,
            tbl        TEXT,
            row_key    TEXT,
            op         TEXT,
            data       TEXT,
            sig        TEXT,
            UNIQUE(origin, origin_seq)
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS _sync_clock (
            tbl     TEXT,
            row_key TEXT,
            col     TEXT,
            hlc     TEXT,
            origin  TEXT,
            vhash   TEXT,
            PRIMARY KEY(tbl, row_key, col)
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS _sync_tombstones (
            tbl     TEXT,
            row_key TEXT,
            hlc     TEXT,
            origin  TEXT,
            PRIMARY KEY(tbl, row_key)
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS _sync_vector (
            origin  TEXT PRIMARY KEY,
            max_seq INTEGER NOT NULL
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS _sync_peer_vectors (
            device_id TEXT,
            origin    TEXT,
            max_seq   INTEGER,
            seen_at   TEXT,
            PRIMARY KEY(device_id, origin)
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS _sync_parked (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            change_json TEXT,
            reason      TEXT,
            first_at    TEXT,
            tries       INTEGER
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS _sync_conflicts (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            at        TEXT,
            tbl       TEXT,
            row_key   TEXT,
            col       TEXT,
            kept      TEXT,
            discarded TEXT,
            reason    TEXT
        );
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS _sync_alias (
            tbl       TEXT,
            alias_gid TEXT PRIMARY KEY,
            gid       TEXT
        );
    """)

    # Seed default flag values: applying flag defaults to 0
    conn.execute(
        "INSERT OR IGNORE INTO _sync_flags (name, value) VALUES ('applying', 0)"
    )

    # Seed default metadata
    conn.execute(
        "INSERT OR IGNORE INTO _sync_meta (key, value) VALUES ('schema_version', '1')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO _sync_meta (key, value) VALUES ('mode', 'off')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO _sync_meta (key, value) VALUES ('next_seq', '1')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO _sync_meta (key, value) VALUES ('last_hlc', '')"
    )

    if device_id:
        set_sync_device_id(conn, db_name, device_id)


def ensure_sync_infrastructure(conn: sqlite3.Connection, db_name: str, device_id: Optional[str] = None) -> list[str]:
    """Runs full sync infrastructure initialization (gid columns + triggers + sync tables)."""
    processed = ensure_gid_columns(conn, db_name)
    ensure_sync_tables(conn, db_name, device_id=device_id)
    return processed
