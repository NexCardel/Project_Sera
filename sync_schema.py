"""Sera Sync v3 — table/column classification registry (blueprint §5, WP P3-1).

Declares, for every table Sera Sync v3 replicates, how it participates in
sync: merge mode (§4.4), row key and foreign-key columns. Also lists tables
that exist in the schema but are deliberately excluded, with the reason.

This module is data only: no schema migration, no triggers, no I/O beyond
reading `sqlite_master`/`PRAGMA table_info` for verification. Nothing here
talks to Qt (blueprint §0 rule 7 — no PySide6 import in any sync_*.py module).
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Optional

MASTER_DB = "master"
RAW_DB = "raw"

# Fixed namespace UUID for deterministic seed row gids (blueprint §5, WP P3-2)
SERA_NS = uuid.UUID("3e5c9f56-6a4a-4a27-814d-3d4d3d789012")


def seed_gid(table: str, natural_key: str) -> str:
    """Deterministic gid for default/seed rows (blueprint §5, WP P3-2)."""
    return uuid.uuid5(SERA_NS, f"seed:{table}:{natural_key}").hex

# Merge modes, see blueprint §4.4.
LWW = "lww"                # per column, highest (hlc, origin) wins
ADMIN_LWW = "admin_lww"    # like lww, but only accepted with a valid admin signature
APPEND = "append"          # insert-only, union by gid, can never conflict
SET = "set"                # add/remove per membership pair, row-level LWW
LOCAL = "local"            # never replicated

VALID_MODES = {LWW, ADMIN_LWW, APPEND, SET, LOCAL}


@dataclass(frozen=True)
class TableSpec:
    name: str
    db: str                                  # MASTER_DB | RAW_DB
    mode: str                                # one of VALID_MODES
    row_key: tuple = ()                      # column(s) identifying a row for sync purposes
    fk: MappingProxyType = field(default_factory=lambda: MappingProxyType({}))
    natural_merge_on: Optional[str] = None   # UNIQUE column used for natural-key merge (P3-4)
    notes: str = ""
    pending_creation: bool = False           # True: registered now, table doesn't exist yet

    def __post_init__(self):
        if self.mode not in VALID_MODES:
            raise ValueError(f"{self.name}: unknown sync mode {self.mode!r}")
        if self.mode != LOCAL and not self.row_key:
            raise ValueError(f"{self.name}: a replicated table needs a row_key")
        if not isinstance(self.fk, MappingProxyType):
            object.__setattr__(self, "fk", MappingProxyType(dict(self.fk)))

    @property
    def key_columns(self) -> set:
        """Every column name this spec references: row_key plus fk columns."""
        return set(self.row_key) | set(self.fk)

    @property
    def expected_live_columns(self) -> set:
        """key_columns that must already exist in today's schema.

        Excludes the literal "gid" row-key placeholder: gid-mode tables don't
        get a real `gid` column until P3-2 runs its migration, so a P3-1-era
        database legitimately doesn't have it yet. Every fk column and every
        non-gid row_key column (natural/composite keys) is already real today
        and belongs in the check.
        """
        return self.key_columns - {"gid"}


_REGISTRY: dict = {}


def _register(spec: TableSpec) -> TableSpec:
    if spec.name in _REGISTRY:
        raise ValueError(f"duplicate registry entry for {spec.name!r}")
    _REGISTRY[spec.name] = spec
    return spec


# ---------------------------------------------------------------- master.db

clients = _register(TableSpec(
    "clients", MASTER_DB, LWW, row_key=("gid",),
))

client_values = _register(TableSpec(
    "client_values", MASTER_DB, LWW, row_key=("client_id", "column_id"),
    fk={"client_id": "clients", "column_id": "mcl_columns"},
))

mcl_columns = _register(TableSpec(
    "mcl_columns", MASTER_DB, LWW, row_key=("gid",),
))

services = _register(TableSpec(
    "services", MASTER_DB, LWW, row_key=("gid",),
    fk={"userid_column_id": "mcl_columns", "password_column_id": "mcl_columns"},
    natural_merge_on="name",
))

client_services = _register(TableSpec(
    "client_services", MASTER_DB, SET, row_key=("client_id", "service_id"),
    fk={"client_id": "clients", "service_id": "services"},
))

cell_formatting = _register(TableSpec(
    "cell_formatting", MASTER_DB, LWW, row_key=("client_id", "column_key"),
    fk={"client_id": "clients", "column_key": "mcl_columns"},
    notes="Verified 2026-09-24 against ui/windows/search_window.py:618,663: "
          "column_key is str(mcl_columns.id) for a normal MCL column, but the "
          "literal string \"services\" for the services column (which has no "
          "mcl_columns row). The fk entry above applies only to the numeric "
          "case. Owner decision 2026-09-24: document this now, translate in "
          "P3-2/P3-4 rather than change the encoding here — the apply engine's "
          "gid-translation step for this column must special-case the literal "
          "\"services\" (pass through unchanged) and only translate column_key "
          "to a gid when it parses as an integer.",
))

audit_log = _register(TableSpec(
    "audit_log", MASTER_DB, APPEND, row_key=("gid",),
    fk={"client_id": "clients", "service_id": "services"},
    notes="client_id/service_id are nullable; if the referent is unknown after "
          "parking, store NULL instead of dropping the entry.",
))

staff_users = _register(TableSpec(
    "staff_users", MASTER_DB, ADMIN_LWW, row_key=("gid",),
    natural_merge_on="name",
))

app_settings = _register(TableSpec(
    "app_settings", MASTER_DB, LWW, row_key=("key",),
    notes="Every key is office-wide (D7): no per-PC list, no exceptions "
          "(includes theme, window_mode, sync_manual_peers, inv_frames). "
          "Not admin_lww: any admin-mode PC may change settings (D7/v1.2).",
))

client_activity_stats = _register(TableSpec(
    "client_activity_stats", MASTER_DB, LOCAL,
    notes="Per-PC view/action counters, not shared state.",
))

client_recent_activity = _register(TableSpec(
    "client_recent_activity", MASTER_DB, LOCAL,
    notes="Transient breadcrumb log, not shared state.",
))

# -------------------------------------------------------------- rawPayload.db

tracker_dump = _register(TableSpec(
    "tracker_dump", RAW_DB, LWW, row_key=("gid",),
    fk={"client_id": "clients", "service_id": "services"},
    notes="Cross-DB FKs: client_id/service_id are resolved against master.db "
          "gids with a separate read connection (§4.4).",
))

sdc_session_timelines = _register(TableSpec(
    "sdc_session_timelines", RAW_DB, LWW, row_key=("session_id",),
    fk={"client_id": "clients"},
))

client_raw_containers = _register(TableSpec(
    "client_raw_containers", RAW_DB, LOCAL, row_key=("identity_key",),
    fk={"client_id": "clients"},
    notes="History, corrected across two review rounds — read this before "
          "touching this table's classification again. Round 1: classified "
          "`local`, but that's wrong as stated because notes/screenshot_path "
          "(typed in by staff through save_srpf_container_media(), verified "
          "against ui/windows/tracker_dump_window.py:1371) have no source in "
          "tracker_dump, so the table didn't meet local's 'fully rebuilt' "
          "condition. Round 2: switched to `lww` on identity_key to replicate "
          "those fields — but that's wrong too, and worse: "
          "re_resolve_all_tracker_dumps() (database.py ~4443) does `DELETE "
          "FROM client_raw_containers` then re-inserts every row. Under "
          "P3-3's capture triggers + P3-4's tombstone rule, that DELETE would "
          "seal a tombstone for every identity_key, and the following INSERT "
          "would be dropped everywhere else ('if a tombstone exists for the "
          "row, drop the upsert', P3-4). Every PC's containers — and any "
          "notes on them — would be permanently deleted on the next rebuild. "
          "This isn't rare: re_resolve_all_tracker_dumps() runs on EVERY PC "
          "today (database.py ~94 at every start-up, plus ~3394, ~4226, "
          "main.py's legacy-sync handler, and the Tracker window) — it is "
          "NOT admin-PC-only yet; that's only what P3-6 proposes to make it, "
          "and P3-6 hasn't been implemented. Owner decision 2026-09-24 "
          "(final, after this was caught in review): split instead of "
          "syncing the whole row. client_raw_containers goes back to `local` "
          "— it's an honestly-derived cache of tracker_dump once "
          "notes/screenshot_path move out — and only the hand-typed fields "
          "sync, via the new client_container_notes TableSpec below, whose "
          "rebuild-shaped table this DELETE+rebuild never touches. "
          "screenshot_path replicating is still only a path string: it won't "
          "resolve on another PC until Phase 3 has real file/blob sync — "
          "accepted as a separate, known limitation either way. "
          "identity_key sometimes embeds the local client_id "
          "(f'CLI-{client_id:05d}', an id-leak like F13) — moot for THIS "
          "table now that it's local, but the same identity_key is reused as "
          "client_container_notes's row key below, so the caveat carries "
          "over there.",
))

client_container_notes = _register(TableSpec(
    "client_container_notes", RAW_DB, LWW, row_key=("identity_key",),
    fk={"client_id": "clients"},
    pending_creation=True,
    notes="Added 2026-09-24 (P3-1 review round 2) as the owner-approved fix "
          "for client_raw_containers being unsafe to sync as a whole row (see "
          "its TableSpec.notes for the full history). This table does NOT "
          "exist in the schema yet — `pending_creation=True` — this is a "
          "registry declaration of intent, not a migration; P3-1 is a "
          "registry-only WP (§5) and building the table is schema work. "
          "Whoever picks this up (P3-2, since it already does schema/table "
          "additions, or a short dedicated WP before it) needs to: (1) "
          "`CREATE TABLE client_container_notes (identity_key TEXT PRIMARY "
          "KEY, client_id INTEGER, notes TEXT, screenshot_path TEXT, "
          "updated_at TEXT NOT NULL)` in rawPayload.db's _init_raw_schema; "
          "(2) one-time-migrate any existing "
          "client_raw_containers.notes/screenshot_path values into it, then "
          "stop writing those two columns on client_raw_containers (leave "
          "them as dead legacy columns rather than dropping them — SQLite "
          "DROP COLUMN needs care with existing indexes/triggers); (3) point "
          "save_srpf_container_media()/get_srpf_container_media() (database.py "
          "~4791-4807) at the new table instead. Until that lands, the "
          "existing notes-wipe protection in re_resolve_all_tracker_dumps() "
          "(the preserved_notes logic added this session) keeps guarding the "
          "old columns locally — it's still correct and still needed in the "
          "meantime, just not sufficient on its own for sync. Same "
          "identity_key caveat as client_raw_containers: it sometimes embeds "
          "the local client_id (CLI-NNNNN), which the apply engine's FK "
          "translation (keyed per fk column, not per row key) can't fix "
          "mechanically — needs its own translation rule, same as noted "
          "there.",
))


# Tables the schema creates that are deliberately NOT part of this registry.
EXCLUDED_TABLES = {
    "tracker_dump_migration_temp":
        "One-shot in-place migration scratch table (database.py "
        "_migrate_tracker_dump_nullable): created, populated, then renamed over "
        "tracker_dump within the same function call. Never present as a "
        "steady-state table.",
    "peer_audit_log":
        "Legacy PeerAuditLogManager store (blueprint F11). Unencrypted, removed "
        "in P4-1; not part of sync v3.",
    "peer_meta":
        "Legacy PeerAuditLogManager bookkeeping table. Removed in P4-1; not "
        "part of sync v3.",
}

REGISTRY = MappingProxyType(_REGISTRY)


def tables_for(db: str) -> list:
    """All registry entries for one database file (MASTER_DB or RAW_DB)."""
    return [t for t in REGISTRY.values() if t.db == db]


def replicated_tables_for(db: str) -> list:
    """Registry entries for one database file whose mode is not LOCAL."""
    return [t for t in tables_for(db) if t.mode != LOCAL]


def pending_tables_for(db: str) -> list:
    """Registry entries for one database file that are declared but not yet
    created (pending_creation=True) — work still owed to a later WP."""
    return [t for t in tables_for(db) if t.pending_creation]


def get(name: str) -> TableSpec:
    return REGISTRY[name]


def _live_table_names(conn: sqlite3.Connection) -> set:
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND NOT ("
        "  name GLOB 'sqlite_*' OR name GLOB '_sync_*' OR name GLOB '_local_*'"
        ")"
    ).fetchall()
    return {r[0] for r in rows}


def _live_column_names(conn: sqlite3.Connection, table: str) -> set:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def verify_against_live_schema(conn: sqlite3.Connection, db: str) -> None:
    """Raises AssertionError if `conn` has a table this registry neither
    classifies nor excludes for the given database (MASTER_DB or RAW_DB), or
    if a registered table is missing a row_key/fk column it claims to have.

    Per §5 P3-1: "the implementer verifies each row against the code and
    reports any table or column not listed here, stop and ask for those."
    This is the machine-checkable half of that verification; it's meant to be
    run in a test against a real, fully-initialized SeraDatabase.
    """
    known = {t.name for t in tables_for(db)} | set(EXCLUDED_TABLES)
    live = _live_table_names(conn)
    unknown = live - known
    if unknown:
        raise AssertionError(
            f"sync_schema.py does not classify these {db} tables, found live "
            f"in the schema: {sorted(unknown)}. Per blueprint §5 P3-1, stop "
            f"and ask the owner before adding them to the registry."
        )

    problems = []
    for spec in tables_for(db):
        if spec.name not in live:
            # Either this DB file doesn't create the table at all, or (for a
            # pending_creation=True spec like client_container_notes) it's a
            # registered future table that hasn't been built yet. Neither is
            # this check's job.
            continue
        live_cols = _live_column_names(conn, spec.name)
        missing = spec.expected_live_columns - live_cols
        if missing:
            problems.append(f"{spec.name}: registry references column(s) "
                             f"{sorted(missing)} not present in the live table "
                             f"(has {sorted(live_cols)})")
    if problems:
        raise AssertionError(
            "sync_schema.py references columns that don't exist in the live "
            "schema: " + "; ".join(problems)
        )
