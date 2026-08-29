"""
database.py
------------
All access to master.db goes through this module. The database is
encrypted at rest with SQLCipher (AES-256). The encryption key is
derived at runtime in security.py and passed in here.

Version 2.0: Removes hardcoded GST/ITR columns in favor of an EAV
schema driven by an admin-configurable Master Column List (MCL).
"""

import sqlcipher3.dbapi2 as sqlite3
import datetime
import json
import os
import sys
import shutil
import time
import threading
import re
from contextlib import contextmanager

import security

DB_FILENAME = "master.db"

class DatabaseError(Exception):
    pass


class SeraDatabase:
    def __init__(self, db_path: str, hex_key: str, raw_db_path: str = None, defer_startup_maintenance: bool = False):
        self.db_path = db_path
        self.hex_key = hex_key
        if raw_db_path:
            self.raw_db_path = raw_db_path
        else:
            db_dir = os.path.dirname(os.path.abspath(db_path))
            self.raw_db_path = os.path.join(db_dir, "rawPayload.db")

        self._local = threading.local()
        # Set externally by main.py once SyncPeerService exists, so this
        # module never has to import sync_peer.py directly (sync depends
        # on the db, not the other way around). Left as a no-op until then
        # so every call site stays safe regardless of init order.
        self._sync_revision_hook = None
        self._init_schema()
        self._init_raw_schema()
        self._migrate_tracker_dump_to_raw_payload_db()
        if not defer_startup_maintenance:
            self.run_startup_maintenance()

    def run_startup_maintenance(self):
        """Runs background resequencing and FST report generation."""
        try:
            self.resequence_client_serial_numbers()
        except Exception as e:
            print(f"[-] Startup serial resequence skipped: {e}")
        try:
            self.sync_fst_reports()
        except Exception as e:
            print(f"[-] Startup FST report sync skipped: {e}")

    def set_sync_revision_hook(self, fn):
        """fn is called with no arguments after any write that should
        propagate to other machines (see log_action, which fires this for
        every mutating action already going through the audit trail)."""
        self._sync_revision_hook = fn

    def _bump_sync_revision_if_configured(self):
        if self._sync_revision_hook:
            try:
                self._sync_revision_hook()
            except Exception:
                # A sync hiccup must never break the caller's actual DB write.
                pass

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(f"PRAGMA key = \"x'{self.hex_key}'\";")
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA cache_size = -64000;")       # 64MB RAM page cache
            conn.execute("PRAGMA temp_store = MEMORY;")        # In-memory temporary tables & sorts
            conn.execute("PRAGMA mmap_size = 268435456;")      # 256MB memory-mapped fast reads
            yield conn
            conn.commit()
        except sqlite3.IntegrityError as e:
            conn.rollback()
            raise e
        except sqlite3.OperationalError as e:
            conn.rollback()
            raise e
        except sqlite3.DatabaseError as e:
            conn.rollback()
            raise DatabaseError(
                "Could not open the database. This almost always means "
                "the master password was typed incorrectly."
            ) from e
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def _connect_raw(self):
        """Dedicated connection for rawPayload.db."""
        conn = sqlite3.connect(self.raw_db_path)
        try:
            conn.execute(f"PRAGMA key = \"x'{self.hex_key}'\";")
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA cache_size = -32000;")       # 32MB RAM page cache
            conn.execute("PRAGMA temp_store = MEMORY;")
            yield conn
            conn.commit()
        except sqlite3.IntegrityError as e:
            conn.rollback()
            raise e
        except sqlite3.OperationalError as e:
            conn.rollback()
            raise e
        except sqlite3.DatabaseError as e:
            conn.rollback()
            raise DatabaseError("Could not open rawPayload.db with provided key.") from e
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def close(self):
        pass


    def _ensure_column(self, conn, table: str, column: str, coldef: str):
        """Adds `column` to `table` if it isn't there yet. Safe to call every
        startup -- lets us extend the schema without a full migration system."""
        cur = conn.execute(f"PRAGMA table_info({table})")
        existing = {row[1] for row in cur.fetchall()}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coldef}")

    def _migrate_tracker_dump_nullable(self, conn):
        """Ensures tracker_dump.client_id is nullable (migrating legacy NOT NULL schema if present)."""
        try:
            cur = conn.execute("PRAGMA table_info(tracker_dump)")
            cols = cur.fetchall()
            if not cols:
                return
            needs_migration = False
            for col in cols:
                # col format: (cid, name, type, notnull, dflt_value, pk)
                if col[1] == "client_id" and col[3] == 1:
                    needs_migration = True
                    break
            if needs_migration:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS tracker_dump_migration_temp (
                        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                        client_id           INTEGER REFERENCES clients(id) ON DELETE CASCADE,
                        unassigned_identity TEXT,
                        service_id          INTEGER,
                        portal              TEXT,
                        period_label        TEXT,
                        arn_number          TEXT,
                        capture_method      TEXT DEFAULT 'DOM_Tracker',
                        status              TEXT DEFAULT 'submitted',
                        raw_payload_json    TEXT,
                        captured_by         TEXT,
                        created_at          TEXT NOT NULL
                    );
                """)
                curr_col_names = {c[1] for c in cols}
                unassigned_expr = "unassigned_identity" if "unassigned_identity" in curr_col_names else "NULL"
                conn.execute(f"""
                    INSERT INTO tracker_dump_migration_temp (id, client_id, unassigned_identity, service_id, portal, period_label, arn_number, capture_method, status, raw_payload_json, captured_by, created_at)
                    SELECT id, client_id, {unassigned_expr}, service_id, portal, period_label, arn_number, capture_method, status, raw_payload_json, captured_by, created_at
                    FROM tracker_dump;
                """)
                conn.execute("DROP TABLE tracker_dump;")
                conn.execute("ALTER TABLE tracker_dump_migration_temp RENAME TO tracker_dump;")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_tracker_dump_client ON tracker_dump(client_id);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_tracker_dump_arn ON tracker_dump(arn_number);")
        except Exception as e:
            print(f"[database] _migrate_tracker_dump_nullable notice: {e}")

    def _init_raw_schema(self):
        """Initializes tables and indexes inside rawPayload.db."""
        with self._connect_raw() as conn:
            conn.execute("""
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
                    created_at          TEXT NOT NULL
                );
            """)
            self._ensure_column(conn, "tracker_dump", "unassigned_identity", "TEXT")
            self._migrate_tracker_dump_nullable(conn)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tracker_dump_client ON tracker_dump(client_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tracker_dump_arn ON tracker_dump(arn_number);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tracker_dump_unassigned ON tracker_dump(unassigned_identity);")

            # SRPF Unified Container: Groups all captures for a client identity
            conn.execute("""
                CREATE TABLE IF NOT EXISTS client_raw_containers (
                    identity_key        TEXT PRIMARY KEY,
                    client_id           INTEGER,
                    company_name        TEXT,
                    proprietor_name     TEXT,
                    pan                 TEXT,
                    gstin               TEXT,
                    tan                 TEXT,
                    phone               TEXT,
                    email               TEXT,
                    dob                 TEXT,
                    user_id             TEXT,
                    portal_profiles     TEXT,
                    filing_history      TEXT,
                    raw_aggregates      TEXT,
                    total_captures      INTEGER DEFAULT 0,
                    last_updated        TEXT NOT NULL
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_client_raw_containers_cid ON client_raw_containers(client_id);")

            # SDC Session Timelines: Chronological clickstream audit trail per client filing session
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sdc_session_timelines (
                    session_id          TEXT PRIMARY KEY,
                    client_id           INTEGER,
                    pan                 TEXT,
                    client_name         TEXT,
                    portal              TEXT,
                    status              TEXT DEFAULT 'active',
                    start_time          TEXT NOT NULL,
                    end_time            TEXT,
                    total_steps         INTEGER DEFAULT 0,
                    timeline_json       TEXT NOT NULL,
                    last_updated        TEXT NOT NULL
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sdc_timelines_pan ON sdc_session_timelines(pan);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sdc_timelines_cid ON sdc_session_timelines(client_id);")

    def _migrate_tracker_dump_to_raw_payload_db(self):
        """One-time migration: moves any historical tracker_dump rows from master.db to rawPayload.db, then drops tracker_dump in master.db."""
        try:
            rows_to_migrate = []
            with self._connect() as m_conn:
                cur = m_conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tracker_dump'")
                if not cur.fetchone():
                    return
                c_cur = m_conn.execute("PRAGMA table_info(tracker_dump)")
                cols = [r[1] for r in c_cur.fetchall()]
                has_unassigned = "unassigned_identity" in cols
                sel_unassigned = "unassigned_identity" if has_unassigned else "NULL as unassigned_identity"
                
                cur = m_conn.execute(f"""
                    SELECT id, client_id, {sel_unassigned}, service_id, portal, period_label,
                           arn_number, capture_method, status, raw_payload_json, captured_by, created_at
                    FROM tracker_dump ORDER BY id
                """)
                rows_to_migrate = cur.fetchall()

            if rows_to_migrate:
                with self._connect_raw() as r_conn:
                    for r in rows_to_migrate:
                        r_conn.execute("""
                            INSERT OR IGNORE INTO tracker_dump (id, client_id, unassigned_identity, service_id, portal, period_label, arn_number, capture_method, status, raw_payload_json, captured_by, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, r)
                print(f"[database] Migrated {len(rows_to_migrate)} tracker_dump rows from master.db to rawPayload.db.")

            # Drop tracker_dump from master.db to keep master.db lean
            with self._connect() as m_conn:
                m_conn.execute("DROP TABLE IF EXISTS tracker_dump;")
        except Exception as e:
            print(f"[database] Notice during tracker_dump migration to rawPayload.db: {e}")

    def _init_schema(self):
        with self._connect() as conn:
            # 1. App Settings
            conn.execute("""
                CREATE TABLE IF NOT EXISTS app_settings (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                );
            """)

            # 2. Master Column List (MCL)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS mcl_columns (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    label             TEXT NOT NULL,
                    field_type        TEXT NOT NULL DEFAULT 'text',
                    dropdown_options  TEXT,
                    is_identity       INTEGER NOT NULL DEFAULT 0,
                    sort_order        INTEGER NOT NULL DEFAULT 0
                );
            """)
            self._ensure_column(conn, "mcl_columns", "show_in_search", "INTEGER NOT NULL DEFAULT 1")
            self._ensure_column(conn, "mcl_columns", "allow_quick_copy", "INTEGER NOT NULL DEFAULT 1")
            self._ensure_column(conn, "mcl_columns", "admin_show_in_search", "INTEGER NOT NULL DEFAULT 1")
            self._ensure_column(conn, "mcl_columns", "is_internal_pk", "INTEGER NOT NULL DEFAULT 0")

            # 3. Core client row
            conn.execute("""
                CREATE TABLE IF NOT EXISTS clients (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    notes       TEXT,
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL,
                    is_archived INTEGER NOT NULL DEFAULT 0
                );
            """)
            self._ensure_column(conn, "clients", "is_archived", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "clients", "client_id_token", "TEXT")

            # Auto-populate client_id_token for any existing clients
            cur = conn.execute("SELECT id FROM clients WHERE client_id_token IS NULL OR client_id_token = '' ORDER BY id")
            missing_token_ids = cur.fetchall()
            for (c_id,) in missing_token_ids:
                conn.execute("UPDATE clients SET client_id_token = ? WHERE id = ?", (str(c_id), c_id))

            # 4. Client Values (EAV side table)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS client_values (
                    client_id  INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                    column_id  INTEGER NOT NULL REFERENCES mcl_columns(id) ON DELETE CASCADE,
                    value      TEXT,
                    PRIMARY KEY (client_id, column_id)
                );
            """)

            # 5. Services
            conn.execute("""
                CREATE TABLE IF NOT EXISTS services (
                    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                    name               TEXT NOT NULL UNIQUE,
                    login_page_link    TEXT,
                    userid_column_id   INTEGER REFERENCES mcl_columns(id) ON DELETE SET NULL,
                    password_column_id INTEGER REFERENCES mcl_columns(id) ON DELETE SET NULL,
                    username_selector  TEXT,
                    password_selector  TEXT,
                    automation_mode    TEXT NOT NULL DEFAULT 'automated',
                    extension_flow     TEXT NOT NULL DEFAULT 'double',
                    success_selector   TEXT,
                    arn_selector       TEXT,
                    sort_order         INTEGER NOT NULL DEFAULT 0
                );
            """)
            self._ensure_column(conn, "services", "extension_flow", "TEXT NOT NULL DEFAULT 'double'")
            self._ensure_column(conn, "services", "success_selector", "TEXT")
            self._ensure_column(conn, "services", "arn_selector", "TEXT")

            # 6. Client Services (Attachment table)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS client_services (
                    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                    service_id  INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
                    PRIMARY KEY (client_id, service_id)
                );
            """)

            # 7. Audit Log
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts          TEXT NOT NULL,
                    actor       TEXT NOT NULL,
                    action      TEXT NOT NULL,
                    client_id   INTEGER,
                    service_id  INTEGER,
                    detail      TEXT
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_client ON audit_log(client_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cv_client ON client_values(client_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cv_column ON client_values(column_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cv_client_col ON client_values(client_id, column_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cv_col_val ON client_values(column_id, value);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mc_identity ON mcl_columns(is_identity, id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cs_client ON client_services(client_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cs_service ON client_services(service_id, client_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_clients_archived ON clients(is_archived);")

            # Shared staff roster. The selected identity is stored locally by
            # main.py; only this canonical list belongs in the synced DB.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS staff_users (
                    id   INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE
                );
            """)
            self._ensure_column(conn, "staff_users", "alias", "TEXT")

            # Seed default 6 canonical staff slots if fresh
            cur = conn.execute("SELECT COUNT(*) FROM staff_users")
            if cur.fetchone()[0] == 0:
                for i in range(1, 7):
                    conn.execute("INSERT INTO staff_users (name, alias) VALUES (?, ?)", (f"User {i}", None))


            # DRS removal migration. FST now uses tracker_dump/raw-payload
            # records directly and no longer depends on the legacy DRS tables.
            conn.execute("DROP TABLE IF EXISTS filing_status")
            conn.execute("DROP TABLE IF EXISTS client_filing_types")
            conn.execute("DROP TABLE IF EXISTS filing_types")

            # 11. Search Grid Cell Formatting (Excel-style cell & text colors)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cell_formatting (
                    client_id    INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                    column_key   TEXT NOT NULL,
                    bg_color     TEXT,
                    fg_color     TEXT,
                    updated_at   TEXT NOT NULL,
                    PRIMARY KEY (client_id, column_key)
                );
            """)

            # 12. Client Activity Stats & Transient Breadcrumb Log
            conn.execute("""
                CREATE TABLE IF NOT EXISTS client_activity_stats (
                    client_id        INTEGER PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE,
                    view_count       INTEGER DEFAULT 0,
                    action_count     INTEGER DEFAULT 0,
                    last_action      TEXT,
                    last_action_time REAL
                );
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS client_recent_activity (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                    action_type TEXT NOT NULL,
                    detail      TEXT,
                    timestamp   REAL NOT NULL
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_recent_act_client ON client_recent_activity(client_id, timestamp);")

            # 14. Seed default MCL columns and services if fresh database
            cur = conn.execute("SELECT COUNT(*) FROM mcl_columns")
            if cur.fetchone()[0] == 0:
                seeded = self._seed_from_ini(conn)
                if not seeded:
                    self._seed_default_data(conn)

            # Ensure serial number / row index columns are never marked as identity columns
            conn.execute(
                "UPDATE mcl_columns SET is_identity = 0 WHERE LOWER(TRIM(label)) IN ('no', 'no.', 'sl no', 'sl. no.', 's.no.', 'sno', 'id', '#')"
            )

            # Ensure default internal PK anchor on PAN if none configured
            cur = conn.execute("SELECT COUNT(*) FROM mcl_columns WHERE is_internal_pk = 1")
            if cur.fetchone()[0] == 0:
                conn.execute("""
                    UPDATE mcl_columns SET is_internal_pk = 1 
                    WHERE UPPER(TRIM(label)) = 'PAN' 
                       OR (LOWER(label) LIKE '%pan%' AND LOWER(label) NOT LIKE '%pass%')
                """)

        self.load_ini_defaults()
        try:
            self.re_resolve_all_tracker_dumps()
        except Exception:
            pass

    def _find_ini_file(self, ini_path: str = None) -> str:
        if ini_path and os.path.exists(ini_path):
            return ini_path
        candidates = [
            os.path.join(os.path.dirname(os.path.abspath(self.db_path)), "settings.ini"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.ini"),
            os.path.join(os.getcwd(), "settings.ini"),
        ]
        if hasattr(sys, "_MEIPASS"):
            candidates.insert(0, os.path.join(sys._MEIPASS, "settings.ini"))
        for c in candidates:
            if os.path.exists(c):
                return c
        return None

    def _seed_from_ini(self, conn, ini_path: str = None) -> bool:
        import configparser
        ini_file = self._find_ini_file(ini_path)
        if not ini_file:
            return False
        try:
            config = configparser.ConfigParser()
            config.read(ini_file, encoding="utf-8-sig")
            if "MCL_Columns" not in config:
                return False

            col_ids_map = {}
            for _, line in config["MCL_Columns"].items():
                parts = [p.strip() for p in line.split("|")]
                if not parts:
                    continue
                lbl = parts[0]
                kwargs = {}
                for p in parts[1:]:
                    if "=" in p:
                        k, v = p.split("=", 1)
                        k = k.strip()
                        v = v.strip()
                        if k in ("is_identity", "is_internal_pk", "sort_order", "show_in_search", "allow_quick_copy", "admin_show_in_search"):
                            kwargs[k] = int(v) if v.isdigit() else (1 if v.lower() == "true" else 0)
                        else:
                            kwargs[k] = v
                cur = conn.execute(
                    """INSERT INTO mcl_columns (label, field_type, is_identity, is_internal_pk, sort_order, show_in_search, allow_quick_copy, admin_show_in_search)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        lbl,
                        kwargs.get("field_type", "text"),
                        kwargs.get("is_identity", 0),
                        kwargs.get("is_internal_pk", 0),
                        kwargs.get("sort_order", 0),
                        kwargs.get("show_in_search", 1),
                        kwargs.get("allow_quick_copy", 1),
                        kwargs.get("admin_show_in_search", 1),
                    )
                )
                col_ids_map[lbl] = cur.lastrowid

            if "Services" in config:
                for _, line in config["Services"].items():
                    parts = [p.strip() for p in line.split("|")]
                    if not parts:
                        continue
                    name = parts[0]
                    kwargs = {}
                    for p in parts[1:]:
                        if "=" in p:
                            k, v = p.split("=", 1)
                            kwargs[k.strip()] = v.strip()
                    
                    u_col = kwargs.get("user_col")
                    p_col = kwargs.get("pass_col")
                    u_id = col_ids_map.get(u_col) if u_col else None
                    p_id = col_ids_map.get(p_col) if p_col else None
                    s_order = int(kwargs.get("sort_order", "1")) if kwargs.get("sort_order", "").isdigit() else 1
                    conn.execute(
                        """INSERT OR IGNORE INTO services (name, login_page_link, userid_column_id, password_column_id, username_selector, password_selector, automation_mode, extension_flow, sort_order)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            name,
                            kwargs.get("login_link", ""),
                            u_id,
                            p_id,
                            kwargs.get("user_sel", ""),
                            kwargs.get("pass_sel", ""),
                            kwargs.get("mode", "extension"),
                            kwargs.get("flow", "double"),
                            s_order,
                        )
                    )
            return True
        except Exception:
            return False

    def _seed_default_data(self, conn):
        """Seeds default MCL columns and Services for fresh database installations."""
        default_cols = [
            ("No.", "text", 0, 1),
            ("NAME OF COMPANY", "text", 1, 2),
            ("NAME OF PROPRIETOR", "text", 1, 3),
            ("GSTIN", "text", 0, 4),
            ("PAN", "text", 0, 5),
            ("PH. NO.", "text", 0, 6),
            ("USER ID", "password", 0, 7),
            ("EMAIL", "password", 0, 8),
            ("GST_Password", "password", 0, 9),
            ("IT_Password", "password", 0, 10),
            ("Email_Password", "password", 0, 11),
        ]
        col_ids = {}
        for label, ftype, is_id, s_order in default_cols:
            cur = conn.execute(
                """INSERT INTO mcl_columns (label, field_type, is_identity, sort_order, show_in_search, allow_quick_copy)
                   VALUES (?, ?, ?, ?, 1, 1)""",
                (label, ftype, is_id, s_order)
            )
            col_ids[label] = cur.lastrowid

        def_svcs = [
            ("GST", "https://services.gst.gov.in/services/login", col_ids.get("USER ID"), col_ids.get("GST_Password"), "#username", "#user_pass", 1),
            ("Income Tax", "https://eportal.incometax.gov.in/iec/foservices/#/login", col_ids.get("USER ID"), col_ids.get("IT_Password"), "#panAdhaarUserId", "input[type='password']", 2),
            ("Email", "", col_ids.get("EMAIL"), col_ids.get("Email_Password"), "", "", 3),
        ]
        for name, link, u_id, p_id, u_sel, p_sel, s_order in def_svcs:
            conn.execute(
                """INSERT OR IGNORE INTO services (name, login_page_link, userid_column_id, password_column_id, username_selector, password_selector, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (name, link, u_id, p_id, u_sel, p_sel, s_order)
            )


    # ---------------- App settings ----------------

    def get_setting(self, key: str, default=None):
        with self._connect() as conn:
            cur = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
            row = cur.fetchone()
            return row[0] if row else default

    def get_all_settings(self) -> dict:
        with self._connect() as conn:
            cur = conn.execute("SELECT key, value FROM app_settings")
            return {r[0]: r[1] for r in cur.fetchall()}

    def set_setting(self, key: str, value: str):
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO app_settings (key, value) VALUES (?, ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (key, value),
            )

    def set_settings_bulk(self, settings_dict: dict):
        with self._connect() as conn:
            conn.executemany(
                """INSERT INTO app_settings (key, value) VALUES (?, ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                [(k, str(v) if v is not None else "") for k, v in settings_dict.items()]
            )

    def load_ini_defaults(self, ini_path: str = None):
        """Loads default settings, MCL columns, and services from a .ini file if present."""
        import sys
        import configparser
        if not ini_path:
            candidates = [
                os.path.join(os.path.dirname(os.path.abspath(self.db_path)), "settings.ini"),
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.ini"),
                os.path.join(os.getcwd(), "settings.ini"),
            ]
            if hasattr(sys, "_MEIPASS"):
                candidates.insert(0, os.path.join(sys._MEIPASS, "settings.ini"))
            for c in candidates:
                if os.path.exists(c):
                    ini_path = c
                    break

        if not ini_path or not os.path.exists(ini_path):
            return

        try:
            config = configparser.ConfigParser()
            config.read(ini_path, encoding="utf-8-sig")

            if "AppSettings" in config:
                existing = self.get_all_settings()
                to_set = {}
                for k, v in config["AppSettings"].items():
                    if k not in existing:
                        to_set[k] = v
                if to_set:
                    self.set_settings_bulk(to_set)

            if "MCL_Columns" in config:
                mcl_items = config["MCL_Columns"].items()
                with self._connect() as conn:
                    cur = conn.execute("SELECT COUNT(*) FROM mcl_columns")
                    count = cur.fetchone()[0]
                    if count == 0:
                        for _, line in mcl_items:
                            parts = [p.strip() for p in line.split("|")]
                            if not parts:
                                continue
                            lbl = parts[0]
                            kwargs = {}
                            for p in parts[1:]:
                                if "=" in p:
                                    k, v = p.split("=", 1)
                                    k = k.strip()
                                    v = v.strip()
                                    if k in ("is_identity", "is_internal_pk", "sort_order", "show_in_search", "allow_quick_copy", "admin_show_in_search"):
                                        kwargs[k] = int(v) if v.isdigit() else (1 if v.lower() == "true" else 0)
                                    else:
                                        kwargs[k] = v
                            conn.execute(
                                """INSERT INTO mcl_columns (label, field_type, is_identity, is_internal_pk, sort_order, show_in_search, allow_quick_copy, admin_show_in_search)
                                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                                (
                                    lbl,
                                    kwargs.get("field_type", "text"),
                                    kwargs.get("is_identity", 0),
                                    kwargs.get("is_internal_pk", 0),
                                    kwargs.get("sort_order", 0),
                                    kwargs.get("show_in_search", 1),
                                    kwargs.get("allow_quick_copy", 1),
                                    kwargs.get("admin_show_in_search", 1),
                                )
                            )

            if "Services" in config:
                svc_items = config["Services"].items()
                with self._connect() as conn:
                    cur = conn.execute("SELECT COUNT(*) FROM services")
                    count = cur.fetchone()[0]
                    if count == 0:
                        cur = conn.execute("SELECT id, label FROM mcl_columns")
                        lbl_to_id = {r[1]: r[0] for r in cur.fetchall()}
                        for _, line in svc_items:
                            parts = [p.strip() for p in line.split("|")]
                            if not parts:
                                continue
                            name = parts[0]
                            kwargs = {}
                            for p in parts[1:]:
                                if "=" in p:
                                    k, v = p.split("=", 1)
                                    kwargs[k.strip()] = v.strip()
                            
                            u_col = kwargs.get("user_col")
                            p_col = kwargs.get("pass_col")
                            u_id = lbl_to_id.get(u_col) if u_col else None
                            p_id = lbl_to_id.get(p_col) if p_col else None
                            s_order = int(kwargs.get("sort_order", "1")) if kwargs.get("sort_order", "").isdigit() else 1
                            conn.execute(
                                """INSERT OR IGNORE INTO services (name, login_page_link, userid_column_id, password_column_id, username_selector, password_selector, automation_mode, extension_flow, sort_order)
                                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                (
                                    name,
                                    kwargs.get("login_link", ""),
                                    u_id,
                                    p_id,
                                    kwargs.get("user_sel", ""),
                                    kwargs.get("pass_sel", ""),
                                    kwargs.get("mode", "extension"),
                                    kwargs.get("flow", "double"),
                                    s_order,
                                )
                            )

            if "ColumnVisibility" in config:
                col_vis = config["ColumnVisibility"]
                if "show_in_search" in col_vis:
                    ids = [int(x.strip()) for x in col_vis["show_in_search"].split(",") if x.strip().isdigit()]
                    if ids:
                        self.bulk_update_mcl_visibility(ids)
                if "allow_quick_copy" in col_vis:
                    ids = [int(x.strip()) for x in col_vis["allow_quick_copy"].split(",") if x.strip().isdigit()]
                    if ids:
                        self.bulk_update_mcl_quick_copy(ids)
                if "admin_show_in_search" in col_vis:
                    ids = [int(x.strip()) for x in col_vis["admin_show_in_search"].split(",") if x.strip().isdigit()]
                    if ids:
                        self.bulk_update_mcl_admin_visibility(ids)
        except Exception as e:
            pass

    def export_to_ini(self, ini_path: str):
        """Exports current database settings, MCL columns, and services to a .ini file."""
        import configparser
        try:
            config = configparser.ConfigParser()
            config["AppSettings"] = self.get_all_settings()

            mcl = self.get_mcl_columns()
            mcl_sec = {}
            id_to_lbl = {}
            for idx, c in enumerate(mcl, 1):
                id_to_lbl[c["id"]] = c["label"]
                line = (
                    f"{c['label']} | field_type={c.get('field_type','text')} | "
                    f"is_identity={1 if c.get('is_identity') else 0} | "
                    f"is_internal_pk={1 if c.get('is_internal_pk') else 0} | sort_order={c.get('sort_order',0)} | "
                    f"show_in_search={1 if c.get('show_in_search') else 0} | "
                    f"allow_quick_copy={1 if c.get('allow_quick_copy') else 0} | "
                    f"admin_show_in_search={1 if c.get('admin_show_in_search') else 0}"
                )
                mcl_sec[f"col_{idx}"] = line
            config["MCL_Columns"] = mcl_sec

            services = self.get_services()
            svc_sec = {}
            for idx, s in enumerate(services, 1):
                u_lbl = id_to_lbl.get(s.get("userid_column_id"), "")
                p_lbl = id_to_lbl.get(s.get("password_column_id"), "")
                line = (
                    f"{s['name']} | login_link={s.get('login_page_link','')} | "
                    f"user_col={u_lbl} | pass_col={p_lbl} | "
                    f"user_sel={s.get('username_selector','')} | pass_sel={s.get('password_selector','')} | "
                    f"mode={s.get('automation_mode','extension')} | flow={s.get('extension_flow','double')} | "
                    f"sort_order={s.get('sort_order',1)}"
                )
                svc_sec[f"svc_{idx}"] = line
            config["Services"] = svc_sec

            with open(ini_path, "w", encoding="utf-8") as f:
                config.write(f)
        except Exception:
            pass

    # ---------------- Shared staff roster ----------------

    def list_staff_users(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT name FROM staff_users ORDER BY id").fetchall()
            return [row[0] for row in rows]

    def get_staff_matrix(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT id, name, alias FROM staff_users ORDER BY id").fetchall()
            return [{"id": r[0], "name": r[1], "alias": r[2] or ""} for r in rows]

    def assign_or_get_alias(self, alias_input: str) -> tuple[str, str]:
        clean_alias = (alias_input or "").strip()
        if not clean_alias:
            clean_alias = "Station-1"
            
        with self._connect() as conn:
            # Ensure 6 slots exist
            cur = conn.execute("SELECT COUNT(*) FROM staff_users")
            if cur.fetchone()[0] == 0:
                for i in range(1, 7):
                    conn.execute("INSERT INTO staff_users (name, alias) VALUES (?, ?)", (f"User {i}", None))

            rows = conn.execute("SELECT id, name, alias FROM staff_users ORDER BY id").fetchall()
            
            # Check if this alias is already bound to a slot
            for r_id, name, alias in rows:
                if alias and alias.lower() == clean_alias.lower():
                    return (name, alias)
            
            # Find first unassigned slot
            for r_id, name, alias in rows:
                if not alias or not alias.strip():
                    conn.execute("UPDATE staff_users SET alias = ? WHERE id = ?", (clean_alias, r_id))
                    return (name, clean_alias)
            
            # If all 6 slots are assigned, bind to first slot or last slot
            r_id, name, _ = rows[0]
            conn.execute("UPDATE staff_users SET alias = ? WHERE id = ?", (clean_alias, r_id))
            return (name, clean_alias)

    def update_staff_alias(self, user_id: int, new_alias: str):
        clean = (new_alias or "").strip()
        with self._connect() as conn:
            conn.execute("UPDATE staff_users SET alias = ? WHERE id = ?", (clean if clean else None, user_id))

    def reset_staff_matrix(self):
        with self._connect() as conn:
            conn.execute("UPDATE staff_users SET alias = NULL")

    def add_staff_user(self, name: str) -> str:
        clean = (name or "").strip()
        if not clean:
            raise ValueError("Staff name cannot be empty.")
        with self._connect() as conn:
            if conn.execute("SELECT COUNT(*) FROM staff_users").fetchone()[0] >= 6:
                raise ValueError("The staff roster can contain at most 6 users.")
            conn.execute("INSERT INTO staff_users (name) VALUES (?)", (clean,))
        return clean

    def remove_staff_user(self, name: str):
        with self._connect() as conn:
            conn.execute("DELETE FROM staff_users WHERE name = ?", ((name or "").strip(),))


    # ---------------- Master Column List (MCL) ----------------

    def get_mcl_columns(self) -> list[dict]:
        with self._connect() as conn:
            cur = conn.execute(
                """SELECT id, label, field_type, dropdown_options, is_identity, sort_order, show_in_search, allow_quick_copy, admin_show_in_search, is_internal_pk 
                   FROM mcl_columns ORDER BY sort_order"""
            )
            return [
                {
                    "id": r[0],
                    "label": r[1],
                    "field_type": r[2],
                    "dropdown_options": json.loads(r[3]) if r[3] else [],
                    "is_identity": bool(r[4]),
                    "sort_order": r[5],
                    "show_in_search": bool(r[6]),
                    "allow_quick_copy": bool(r[7]),
                    "admin_show_in_search": bool(r[8]) if len(r) > 8 else True,
                    "is_internal_pk": bool(r[9]) if len(r) > 9 else False,
                }
                for r in cur.fetchall()
            ]

    def get_id_column(self) -> Optional[dict]:
        for col in self.get_mcl_columns():
            if col.get("field_type") == "id":
                return col
        return None

    def get_internal_pk_columns(self) -> list[dict]:
        """Returns all MCL columns designated as mandatory Internal Primary Key Anchors."""
        mcl = self.get_mcl_columns()
        anchors = [c for c in mcl if c.get("is_internal_pk")]
        if not anchors:
            anchors = [c for c in mcl if any(k in c.get("label", "").lower() for k in ("pan", "tan", "gstin")) and "pass" not in c.get("label", "").lower()]
        return anchors

    def get_identity_column(self) -> Optional[dict]:
        ignored_labels = {"no", "no.", "sl no", "sl. no.", "s.no.", "sno", "id", "#"}
        mcl = self.get_mcl_columns()
        for col in mcl:
            if col.get("is_identity") and col.get("label", "").strip().lower() not in ignored_labels:
                return col
        for col in mcl:
            if col.get("label", "").strip().lower() not in ignored_labels:
                return col
        return None

    def create_mcl_column(self, label: str, field_type: str, dropdown_options=None, is_identity: int = 0, is_internal_pk: int = 0) -> int:
        opts_json = json.dumps(dropdown_options) if dropdown_options else None
        with self._connect() as conn:
            if field_type == "id":
                conn.execute("UPDATE mcl_columns SET field_type = 'text' WHERE field_type = 'id'")
            cur = conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM mcl_columns")
            next_order = cur.fetchone()[0]
            cur = conn.execute(
                """INSERT INTO mcl_columns (label, field_type, dropdown_options, is_identity, sort_order, is_internal_pk)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (label.strip(), field_type, opts_json, is_identity, next_order, is_internal_pk)
            )
            return cur.lastrowid

    def update_mcl_column(self, column_id: int, label: str, field_type: str, dropdown_options=None, is_identity: int = None, is_internal_pk: int = None):
        opts_json = json.dumps(dropdown_options) if dropdown_options else None
        with self._connect() as conn:
            if field_type == "id":
                conn.execute("UPDATE mcl_columns SET field_type = 'text' WHERE field_type = 'id' AND id != ?", (column_id,))
            
            # Fetch current values for unspecified flags
            cur = conn.execute("SELECT is_identity, is_internal_pk FROM mcl_columns WHERE id = ?", (column_id,))
            row = cur.fetchone()
            curr_ident = row[0] if row else 0
            curr_pk = row[1] if row and len(row) > 1 else 0

            target_ident = int(is_identity) if is_identity is not None else curr_ident
            target_pk = int(is_internal_pk) if is_internal_pk is not None else curr_pk

            conn.execute(
                """UPDATE mcl_columns SET label=?, field_type=?, dropdown_options=?, is_identity=?, is_internal_pk=? 
                   WHERE id=?""",
                (label.strip(), field_type, opts_json, target_ident, target_pk, column_id)
            )

    def delete_mcl_column(self, column_id: int):
        with self._connect() as conn:
            conn.execute("DELETE FROM mcl_columns WHERE id=?", (column_id,))

    def reorder_mcl_columns(self, ordered_column_ids: list):
        with self._connect() as conn:
            for idx, col_id in enumerate(ordered_column_ids):
                conn.execute("UPDATE mcl_columns SET sort_order=? WHERE id=?", (idx, col_id))

    def bulk_update_mcl_visibility(self, visible_ids: list[int]):
        with self._connect() as conn:
            conn.execute("UPDATE mcl_columns SET show_in_search=0")
            for cid in visible_ids:
                conn.execute("UPDATE mcl_columns SET show_in_search=1 WHERE id=?", (cid,))

    def bulk_update_mcl_quick_copy(self, allowed_ids: list[int]):
        with self._connect() as conn:
            conn.execute("UPDATE mcl_columns SET allow_quick_copy=0")
            for cid in allowed_ids:
                conn.execute("UPDATE mcl_columns SET allow_quick_copy=1 WHERE id=?", (cid,))

    def bulk_update_mcl_admin_visibility(self, admin_visible_ids: list[int]):
        with self._connect() as conn:
            conn.execute("UPDATE mcl_columns SET admin_show_in_search=0")
            for cid in admin_visible_ids:
                conn.execute("UPDATE mcl_columns SET admin_show_in_search=1 WHERE id=?", (cid,))


    # ---------------- Services ----------------

    def get_services(self) -> list[dict]:
        with self._connect() as conn:
            cur = conn.execute(
                """SELECT id, name, login_page_link, userid_column_id, password_column_id,
                          username_selector, password_selector, automation_mode, extension_flow,
                          success_selector, arn_selector, sort_order
                   FROM services ORDER BY sort_order"""
            )
            return [
                {
                    "id": r[0], "name": r[1], "login_page_link": r[2],
                    "userid_column_id": r[3], "password_column_id": r[4],
                    "username_selector": r[5], "password_selector": r[6],
                    "automation_mode": r[7], "extension_flow": r[8],
                    "success_selector": r[9], "arn_selector": r[10], "sort_order": r[11]
                }
                for r in cur.fetchall()
            ]

    def create_service(self, name: str, login_page_link: str, userid_column_id: int,
                       password_column_id: int, username_selector: str, password_selector: str,
                       automation_mode: str, extension_flow: str = "double",
                       success_selector: str = "", arn_selector: str = "") -> int:
        u_sel = (username_selector or "").strip()
        p_sel = (password_selector or "").strip()
        link = (login_page_link or "").strip()

        # If selectors are missing, resolve from verified presets
        if not u_sel or not p_sel:
            presets = [
                (['tdscpc', 'traces', 'tds'], "input[id*='userId'], input[name*='userId'], #userId, input[name='userId']", "input[id*='psw'], input[name*='psw'], input[type='password'], #psw, input[name='psw']", 'https://www.tdscpc.gov.in/app/login.xhtml', 'single'),
                (['gst.gov.in', 'gst'], '#username', '#user_pass', 'https://services.gst.gov.in/services/login', 'single'),
                (['incometax', 'itr', 'eportal'], '#panAdhaarUserId', "input[type='password']", 'https://eportal.incometax.gov.in/iec/foservices/#/login', 'double'),
                (['gmail', 'google', 'accounts.google'], '#identifierId, input[type="email"]', "input[name='Passwd'], input[type='password']", 'https://accounts.google.com', 'double'),
                (['epfindia', 'epfo', 'unifiedportal', 'pf'], '#userName, #username, input[name="username"]', '#password, input[type="password"]', 'https://unifiedportal-mem.epfindia.gov.in/', 'single'),
                (['icegate'], '#userId, #userName', '#password, input[type="password"]', 'https://www.icegate.gov.in', 'single'),
                (['mca.gov.in', 'mca21', 'mca'], '#userName, #userId, input[name="userName"]', '#password, input[type="password"]', 'https://www.mca.gov.in/content/mca/global/en/foportal/fologin.html', 'double'),
            ]
            combined = f"{name.lower()} {link.lower()}"
            for kws, def_u, def_p, def_url, def_flow in presets:
                if any(k in combined for k in kws):
                    if not u_sel: u_sel = def_u
                    if not p_sel: p_sel = def_p
                    if not link: link = def_url
                    if not extension_flow: extension_flow = def_flow
                    break

        with self._connect() as conn:
            cur = conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM services")
            next_order = cur.fetchone()[0]
            cur = conn.execute(
                """INSERT INTO services (name, login_page_link, userid_column_id, password_column_id,
                                         username_selector, password_selector, automation_mode, extension_flow,
                                         success_selector, arn_selector, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (name.strip(), link, userid_column_id, password_column_id,
                 u_sel, p_sel, automation_mode, extension_flow,
                 success_selector, arn_selector, next_order)
            )
            return cur.lastrowid

    def update_service(self, service_id: int, name: str, login_page_link: str, userid_column_id: int,
                       password_column_id: int, username_selector: str, password_selector: str,
                       automation_mode: str, extension_flow: str = "double",
                       success_selector: str = "", arn_selector: str = ""):
        u_sel = (username_selector or "").strip()
        p_sel = (password_selector or "").strip()
        link = (login_page_link or "").strip()

        # If selectors are missing, resolve from verified presets
        if not u_sel or not p_sel:
            presets = [
                (['tdscpc', 'traces', 'tds'], "input[id*='userId'], input[name*='userId'], #userId, input[name='userId']", "input[id*='psw'], input[name*='psw'], input[type='password'], #psw, input[name='psw']", 'https://www.tdscpc.gov.in/app/login.xhtml', 'single'),
                (['gst.gov.in', 'gst'], '#username', '#user_pass', 'https://services.gst.gov.in/services/login', 'single'),
                (['incometax', 'itr', 'eportal'], '#panAdhaarUserId', "input[type='password']", 'https://eportal.incometax.gov.in/iec/foservices/#/login', 'double'),
                (['gmail', 'google', 'accounts.google'], '#identifierId, input[type="email"]', "input[name='Passwd'], input[type='password']", 'https://accounts.google.com', 'double'),
                (['epfindia', 'epfo', 'unifiedportal', 'pf'], '#userName, #username, input[name="username"]', '#password, input[type="password"]', 'https://unifiedportal-mem.epfindia.gov.in/', 'single'),
                (['icegate'], '#userId, #userName', '#password, input[type="password"]', 'https://www.icegate.gov.in', 'single'),
                (['mca.gov.in', 'mca21', 'mca'], '#userName, #userId, input[name="userName"]', '#password, input[type="password"]', 'https://www.mca.gov.in/content/mca/global/en/foportal/fologin.html', 'double'),
            ]
            combined = f"{name.lower()} {link.lower()}"
            for kws, def_u, def_p, def_url, def_flow in presets:
                if any(k in combined for k in kws):
                    if not u_sel: u_sel = def_u
                    if not p_sel: p_sel = def_p
                    if not link: link = def_url
                    if not extension_flow: extension_flow = def_flow
                    break

        with self._connect() as conn:
            conn.execute(
                """UPDATE services SET name=?, login_page_link=?, userid_column_id=?,
                                       password_column_id=?, username_selector=?, password_selector=?,
                                       automation_mode=?, extension_flow=?, success_selector=?, arn_selector=? WHERE id=?""",
                (name.strip(), link, userid_column_id, password_column_id,
                 u_sel, p_sel, automation_mode, extension_flow,
                 success_selector, arn_selector, service_id)
            )

    def auto_populate_service_selectors(self):
        """
        Auto-scrapes and resolves username & password CSS selectors for services
        that have a login_page_link or recognizable name but are missing valid selectors.
        """
        import urllib.request
        import re

        # Known portal definitions: (keywords_in_name_or_url, (u_sel, p_sel, default_url, default_flow))
        known_portals = [
            (
                ['tdscpc', 'traces', 'tds'],
                ("input[id*='userId'], input[name*='userId'], #userId, input[name='userId']", "input[id*='psw'], input[name*='psw'], input[type='password'], #psw, input[name='psw']", 'https://www.tdscpc.gov.in/app/login.xhtml', 'single')
            ),
            (
                ['gst.gov.in', 'gst'],
                ('#username', '#user_pass', 'https://services.gst.gov.in/services/login', 'single')
            ),
            (
                ['incometax', 'itr', 'eportal'],
                ('#panAdhaarUserId', "input[type='password']", 'https://eportal.incometax.gov.in/iec/foservices/#/login', 'double')
            ),
            (
                ['gmail', 'google', 'accounts.google'],
                ('#identifierId, input[type="email"]', "input[name='Passwd'], input[type='password']", 'https://accounts.google.com', 'double')
            ),
            (
                ['epfindia', 'epfo', 'unifiedportal'],
                ('#userName, #username, input[name="username"]', '#password, input[type="password"]', 'https://unifiedportal-mem.epfindia.gov.in/', 'single')
            ),
            (
                ['icegate'],
                ('#userId, #userName', '#password, input[type="password"]', 'https://www.icegate.gov.in', 'single')
            ),
            (
                ['mca.gov.in', 'mca21', 'mca'],
                ('#userName, #userId, input[name="userName"]', '#password, input[type="password"]', 'https://www.mca.gov.in/content/mca/global/en/foportal/fologin.html', 'double')
            ),
        ]

        def _match_known(name: str, url: str) -> tuple[str, str, str, str]:
            combined = f"{name or ''} {url or ''}".lower()
            for keywords, (u_sel, p_sel, def_url, def_flow) in known_portals:
                for kw in keywords:
                    if kw in combined:
                        return (u_sel, p_sel, def_url, def_flow)
            return ("", "", "", "")

        def _scrape_url(url: str) -> tuple[str, str]:
            if not url or not url.strip():
                return ("", "")
            url_clean = url.strip()
            try:
                req = urllib.request.Request(url_clean, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
                with urllib.request.urlopen(req, timeout=3) as resp:
                    html = resp.read().decode('utf-8', errors='ignore')
                    
                    p_match = re.search(r'<input[^>]*type=["\']password["\'][^>]*>', html, re.I)
                    p_sel = "input[type='password']"
                    if p_match:
                        id_m = re.search(r'id=["\']([^"\']+)["\']', p_match.group(0), re.I)
                        if id_m: p_sel = '#' + id_m.group(1)
                        
                    u_match = re.search(r'<input[^>]*type=["\'](text|email)["\'][^>]*>', html, re.I)
                    u_sel = "input[type='text']"
                    if u_match:
                        id_m = re.search(r'id=["\']([^"\']+)["\']', u_match.group(0), re.I)
                        if id_m: u_sel = '#' + id_m.group(1)
                    return (u_sel, p_sel)
            except Exception:
                return ("input[type='text'], input[name='userId'], #username", "input[type='password'], #password, #psw")

        services = self.get_services()
        for svc in services:
            svc_id = svc["id"]
            svc_name = svc.get("name", "")
            link = svc.get("login_page_link", "")
            u_sel = (svc.get("username_selector") or "").strip()
            p_sel = (svc.get("password_selector") or "").strip()
            flow = svc.get("extension_flow", "double")

            k_u, k_p, k_url, k_flow = _match_known(svc_name, link)
            
            # If known portal, apply high-accuracy verified selectors
            if k_u and k_p:
                final_u = u_sel if (u_sel and u_sel not in ["input[type='text']", "#username"]) else k_u
                final_p = p_sel if (p_sel and p_sel not in ["input[type='password']", "#password"]) else k_p
                final_link = link if link else k_url
                final_flow = flow if flow else k_flow
                with self._connect() as conn:
                    conn.execute(
                        "UPDATE services SET username_selector = ?, password_selector = ?, login_page_link = ?, extension_flow = ? WHERE id = ?",
                        (final_u, final_p, final_link, final_flow, svc_id)
                    )
            elif link and (not u_sel or not p_sel):
                new_u_sel, new_p_sel = _scrape_url(link)
                final_u = u_sel if u_sel else new_u_sel
                final_p = p_sel if p_sel else new_p_sel
                with self._connect() as conn:
                    conn.execute(
                        "UPDATE services SET username_selector = ?, password_selector = ? WHERE id = ?",
                        (final_u, final_p, svc_id)
                    )

    def delete_service(self, service_id: int):
        with self._connect() as conn:
            conn.execute("DELETE FROM services WHERE id=?", (service_id,))


    # ---------------- Clients & Activity ----------------

    def record_client_activity(self, client_id: int, action_type: str, detail: str = ""):
        """Records a client interaction (e.g. GST, ITR, Viewed, Copied, Edited)."""
        now = time.time()
        with self._connect() as conn:
            # 1. Insert into recent activity log
            conn.execute(
                "INSERT INTO client_recent_activity (client_id, action_type, detail, timestamp) VALUES (?, ?, ?, ?)",
                (client_id, action_type, detail, now)
            )
            # 2. Update aggregate stats
            is_view = 1 if action_type == "Viewed" else 0
            is_action = 0 if action_type == "Viewed" else 1
            conn.execute("""
                INSERT INTO client_activity_stats (client_id, view_count, action_count, last_action, last_action_time)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(client_id) DO UPDATE SET
                    view_count = view_count + excluded.view_count,
                    action_count = action_count + excluded.action_count,
                    last_action = excluded.last_action,
                    last_action_time = excluded.last_action_time
            """, (client_id, is_view, is_action, action_type, now))
            
            # Prune old recent activity entries (older than 24 hours) to keep table lightweight
            cutoff = now - 86400
            conn.execute("DELETE FROM client_recent_activity WHERE timestamp < ?", (cutoff,))

    def get_recent_client_activities(self, max_age_seconds: int = 1800) -> dict[int, list[dict]]:
        """Returns map of client_id -> list of recent action dicts within max_age_seconds."""
        now = time.time()
        cutoff = now - max_age_seconds
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT client_id, action_type, detail, timestamp FROM client_recent_activity WHERE timestamp >= ? ORDER BY timestamp DESC",
                (cutoff,)
            )
            res = {}
            for r in cur.fetchall():
                cid = r[0]
                if cid not in res:
                    res[cid] = []
                res[cid].append({
                    "action_type": r[1],
                    "detail": r[2] or "",
                    "timestamp": r[3],
                    "age_seconds": int(now - r[3])
                })
            return res

    def get_all_activity_stats(self) -> dict[int, dict]:
        """Returns map of client_id -> dict of activity stats."""
        with self._connect() as conn:
            cur = conn.execute("SELECT client_id, view_count, action_count, last_action, last_action_time FROM client_activity_stats")
            return {
                r[0]: {
                    "view_count": r[1],
                    "action_count": r[2],
                    "last_action": r[3],
                    "last_action_time": r[4]
                }
                for r in cur.fetchall()
            }

    def search_clients(self, query: str = "", service_id: int = None,
                        include_archived: bool = False, archived_only: bool = False,
                        filter_preset: str = None) -> list[dict]:
        like = f"%{query}%" if query else ""
        with self._connect() as conn:
            sql = "SELECT DISTINCT c.id, c.notes, c.created_at, c.updated_at, c.is_archived, c.client_id_token FROM clients c"
            
            if query:
                sql += " LEFT JOIN client_values cv ON c.id = cv.client_id"
                sql += " LEFT JOIN mcl_columns mc ON cv.column_id = mc.id"
            if service_id is not None:
                sql += " INNER JOIN client_services cs ON c.id = cs.client_id"

            where_clauses = []
            params = []
            order_by = "ORDER BY c.id ASC"
            
            if query:
                where_clauses.append("(c.client_id_token LIKE ? OR CAST(c.id AS TEXT) LIKE ? OR cv.value LIKE ?)")
                params.extend([like, like, like])
            if service_id is not None:
                where_clauses.append("cs.service_id = ?")
                params.append(service_id)
            if archived_only or filter_preset == "archived":
                where_clauses.append("c.is_archived = 1")
            elif not include_archived:
                where_clauses.append("c.is_archived = 0")

            if filter_preset == "most_viewed":
                where_clauses.append("c.id IN (SELECT client_id FROM client_activity_stats WHERE (view_count + action_count) > 0)")
                order_by = "ORDER BY (SELECT (view_count * 3 + action_count) FROM client_activity_stats WHERE client_id = c.id) DESC"
            elif filter_preset == "active_today":
                start_of_today = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
                where_clauses.append("c.id IN (SELECT client_id FROM client_recent_activity WHERE timestamp >= ?)")
                params.append(start_of_today)
            elif filter_preset == "has_services":
                where_clauses.append("c.id IN (SELECT client_id FROM client_services)")
            elif filter_preset == "no_services":
                where_clauses.append("c.id NOT IN (SELECT client_id FROM client_services)")
            elif filter_preset == "has_passwords":
                where_clauses.append("c.id IN (SELECT cv.client_id FROM client_values cv JOIN mcl_columns mc ON cv.column_id = mc.id WHERE mc.field_type = 'password' AND LENGTH(TRIM(COALESCE(cv.value, ''))) > 0)")
            elif filter_preset == "missing_passwords":
                where_clauses.append("c.id NOT IN (SELECT cv.client_id FROM client_values cv JOIN mcl_columns mc ON cv.column_id = mc.id WHERE mc.field_type = 'password' AND LENGTH(TRIM(COALESCE(cv.value, ''))) > 0)")
            elif filter_preset == "has_formatting":
                where_clauses.append("c.id IN (SELECT client_id FROM cell_formatting)")

            if where_clauses:
                sql += " WHERE " + " AND ".join(where_clauses)

            sql += f" {order_by}"

            cur = conn.execute(sql, params)
            rows = cur.fetchall()
            if not rows:
                return []

            client_ids = [r[0] for r in rows]
            clients_map = {
                r[0]: {
                    "id": r[0], "notes": r[1], "created_at": r[2], "updated_at": r[3],
                    "is_archived": bool(r[4]) if len(r) > 4 else False,
                    "client_id_token": r[5] if len(r) > 5 and r[5] else str(r[0]),
                    "values": {},
                    "service_ids": []
                }
                for r in rows
            }

            # Batch fetch all values for these clients in chunked queries to avoid SQL parameter limits
            chunk_size = 900
            for i in range(0, len(client_ids), chunk_size):
                chunk = client_ids[i:i + chunk_size]
                placeholders = ",".join("?" * len(chunk))
                v_sql = f"SELECT client_id, column_id, value FROM client_values WHERE client_id IN ({placeholders})"
                vcur = conn.execute(v_sql, chunk)
                for cid, col_id, val in vcur.fetchall():
                    if cid in clients_map:
                        clients_map[cid]["values"][col_id] = val

                s_sql = f"SELECT client_id, service_id FROM client_services WHERE client_id IN ({placeholders})"
                scur = conn.execute(s_sql, chunk)
                for cid, sid in scur.fetchall():
                    if cid in clients_map:
                        clients_map[cid]["service_ids"].append(sid)

            id_col = self.get_id_column()
            if id_col:
                for cid, cdata in clients_map.items():
                    if id_col["id"] not in cdata["values"] or not str(cdata["values"][id_col["id"]]).strip():
                        cdata["values"][id_col["id"]] = str(cid)

            return [clients_map[cid] for cid in client_ids]


    def _fetch_client_full(self, conn, client_id: int) -> dict:
        cur = conn.execute("SELECT id, notes, created_at, updated_at, is_archived, client_id_token FROM clients WHERE id = ?", (client_id,))
        row = cur.fetchone()
        if not row:
            return None
            
        client = {
            "id": row[0], "notes": row[1], "created_at": row[2], "updated_at": row[3],
            "is_archived": bool(row[4]) if len(row) > 4 else False,
            "client_id_token": row[5] if len(row) > 5 and row[5] else str(row[0])
        }

        vcur = conn.execute("SELECT column_id, value FROM client_values WHERE client_id=?", (client_id,))
        client["values"] = {r[0]: r[1] for r in vcur.fetchall()}

        id_col = self.get_id_column()
        if id_col and (id_col["id"] not in client["values"] or not str(client["values"][id_col["id"]]).strip()):
            client["values"][id_col["id"]] = str(client_id)

        scur = conn.execute("SELECT service_id FROM client_services WHERE client_id=?", (client_id,))
        client["service_ids"] = [r[0] for r in scur.fetchall()]

        return client

    def get_client(self, client_id: int) -> dict:
        with self._connect() as conn:
            return self._fetch_client_full(conn, client_id)

    def get_client_services(self, client_id: int) -> list[dict]:
        """Returns the full service configurations attached to a specific client."""
        with self._connect() as conn:
            cur = conn.execute(
                """SELECT s.id, s.name, s.login_page_link, s.userid_column_id,
                          s.password_column_id, s.username_selector, s.password_selector,
                          s.automation_mode, s.sort_order
                   FROM services s
                   INNER JOIN client_services cs ON s.id = cs.service_id
                   WHERE cs.client_id = ?
                   ORDER BY s.sort_order""", (client_id,)
            )
            return [
                {
                    "id": r[0], "name": r[1], "login_page_link": r[2],
                    "userid_column_id": r[3], "password_column_id": r[4],
                    "username_selector": r[5], "password_selector": r[6],
                    "automation_mode": r[7], "sort_order": r[8]
                }
                for r in cur.fetchall()
            ]

    def _validate_internal_pk_values(self, values: dict[int, str], exclude_client_id: int = None):
        """Validates that all columns flagged as Internal Primary Key Anchors are non-empty and unique across active clients."""
        pk_cols = [c for c in self.get_mcl_columns() if c.get("is_internal_pk")]
        if not pk_cols:
            return

        with self._connect() as conn:
            for col in pk_cols:
                col_id = col["id"]
                val = str(values.get(col_id, "") or "").strip()
                if not val:
                    raise ValueError(f"Internal Primary Key '{col['label']}' is mandatory and cannot be empty.")

                # Check uniqueness against active clients in vault
                q = """SELECT c.id FROM clients c
                       JOIN client_values cv ON cv.client_id = c.id
                       WHERE c.is_archived = 0 AND cv.column_id = ? AND UPPER(TRIM(cv.value)) = ?"""
                params = [col_id, val.upper()]
                if exclude_client_id is not None:
                    q += " AND c.id != ?"
                    params.append(exclude_client_id)

                cur = conn.execute(q, params)
                existing = cur.fetchone()
                if existing:
                    raise ValueError(f"A client with {col['label']} '{val}' already exists (Client #{existing[0]}). Duplicate Internal Primary Keys are not permitted.")

    def add_client(self, values: dict[int, str], notes: str, service_ids: list[int], actor: str = "Staff") -> int:
        self._validate_internal_pk_values(values)
        now = datetime.datetime.utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO clients (notes, created_at, updated_at) VALUES (?, ?, ?)",
                (notes, now, now)
            )
            client_id = cur.lastrowid
            token = str(client_id)
            conn.execute("UPDATE clients SET client_id_token=? WHERE id=?", (token, client_id))

            # Auto-assign serial number to ID column if present and not provided
            id_col = self.get_id_column()
            if id_col and (id_col["id"] not in values or not str(values.get(id_col["id"], "")).strip()):
                values[id_col["id"]] = str(client_id)

            for col_id, val in values.items():
                if val is not None and val != "":
                    conn.execute(
                        "INSERT INTO client_values (client_id, column_id, value) VALUES (?, ?, ?)",
                        (client_id, col_id, val)
                    )

            for sid in service_ids:
                conn.execute(
                    "INSERT INTO client_services (client_id, service_id) VALUES (?, ?)",
                    (client_id, sid)
                )
        self.log_action(actor=actor, action="create", client_id=client_id, detail=f"Added new client record CLI-{client_id:05d}")
        return client_id

    def update_client(self, client_id: int, values: dict[int, str], notes: str, service_ids: list[int], actor: str = "Staff"):
        self._validate_internal_pk_values(values, exclude_client_id=client_id)
        now = datetime.datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE clients SET notes=?, updated_at=? WHERE id=?",
                (notes, now, client_id)
            )

            # Whole-form resubmit: wipe & recreate values and services
            conn.execute("DELETE FROM client_values WHERE client_id=?", (client_id,))
            for col_id, val in values.items():
                if val is not None and val != "":
                    conn.execute(
                        "INSERT INTO client_values (client_id, column_id, value) VALUES (?, ?, ?)",
                        (client_id, col_id, val)
                    )

            conn.execute("DELETE FROM client_services WHERE client_id=?", (client_id,))
            for sid in service_ids:
                conn.execute(
                    "INSERT INTO client_services (client_id, service_id) VALUES (?, ?)",
                    (client_id, sid)
                )
        self.log_action(actor=actor, action="update", client_id=client_id, detail=f"Updated client profile CLI-{client_id:05d}")

    def archive_client(self, client_id: int, actor: str = "Staff"):
        """Soft-delete: hides the client from search/autofill but keeps the
        record, restorable via unarchive_client(). Distinct from delete_client(),
        which is permanent."""
        now = datetime.datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE clients SET is_archived=1, updated_at=? WHERE id=?",
                (now, client_id)
            )
        self.log_action(actor=actor, action="archive", client_id=client_id, detail=f"Archived client record CLI-{client_id:05d}")

    def unarchive_client(self, client_id: int, actor: str = "Staff"):
        now = datetime.datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE clients SET is_archived=0, updated_at=? WHERE id=?",
                (now, client_id)
            )
        self.log_action(actor=actor, action="unarchive", client_id=client_id, detail=f"Unarchived client record CLI-{client_id:05d}")

    def delete_client(self, client_id: int, actor: str = "Staff"):
        with self._connect() as conn:
            conn.execute("DELETE FROM clients WHERE id=?", (client_id,))
        self.log_action(actor=actor, action="delete", client_id=client_id, detail=f"Permanently deleted client record CLI-{client_id:05d}")

    def find_duplicate_clients(self, values: dict[int, str], exclude_client_id: int = None) -> list[str]:
        """Non-blocking duplicate check: returns the identity-column values in
        `values` that already belong to a different, non-archived client, so
        the caller can warn (not gate) the save. Mirrors the CSV import's
        duplicate warning (see bulk_import_clients) but for the manual
        Add/Edit Client form."""
        with self._connect() as conn:
            identity_ids = {
                r[0] for r in conn.execute("SELECT id FROM mcl_columns WHERE is_identity = 1")
            }
            matches = []
            for col_id, val in values.items():
                if col_id not in identity_ids or not val:
                    continue
                sql = """SELECT DISTINCT cv.client_id FROM client_values cv
                         JOIN clients c ON c.id = cv.client_id
                         WHERE cv.column_id = ? AND cv.value = ? AND c.is_archived = 0"""
                params = [col_id, val]
                if exclude_client_id is not None:
                    sql += " AND cv.client_id != ?"
                    params.append(exclude_client_id)
                cur = conn.execute(sql, params)
                if cur.fetchone():
                    matches.append(val)
            return matches



    # ---------------- CSV Import ----------------

    # Header names (case-insensitive) recognized as the "attach these services"
    # column, so a CSV can carry its own labels instead of ticking every
    # client's service checkboxes by hand in Admin Mode.
    SERVICES_HEADER_ALIASES = {"services", "service", "labels", "label"}
    SYSTEM_HEADERS = {"client id", "created at", "updated at", "is archived"}

    def bulk_import_clients(self, rows: list[dict[str, str]], actor: str = "Admin") -> dict:
        """Imports/updates clients from parsed CSV rows (list of header->value dicts).

        Matching & upsert behavior (this is what makes re-importing the same
        CSV safe, e.g. after adding a new MCL column and re-uploading the
        same file with that column now filled in):
          - For each row, any identity-column values present are used to look
            up an existing, non-archived client with that exact identity value.
          - Exactly one match  -> UPDATE that client. Only the columns present
            (non-empty) in this row are written; columns already in the
            database that the CSV doesn't mention are left untouched. Values
            are written with an upsert (INSERT ... ON CONFLICT DO UPDATE)
            against client_values' (client_id, column_id) primary key, so
            re-importing never raises a duplicate/UNIQUE constraint error --
            it just overwrites that cell with the new value.
          - Zero matches        -> a brand-new client is created.
          - More than one match -> ambiguous (two different existing clients
            share an identity value from this row); nothing is overwritten,
            a new client is created instead, and a warning is returned so a
            human can sort out the ambiguity.
          - A "Services"/"Labels" column (see SERVICES_HEADER_ALIASES) or
            "Service: <ServiceName>" columns (with Yes/No or True/False values)
            auto-attach matching services (added to, not replacing, whatever
            the client is already attached to).
        """
        imported = 0
        updated = 0
        warnings = []
        skipped_columns = set()

        with self._connect() as conn:
            cur = conn.execute("SELECT id, label, is_identity FROM mcl_columns")
            mcl = {r[1].lower().strip(): {"id": r[0], "is_identity": bool(r[2])} for r in cur.fetchall()}

            cur = conn.execute("SELECT id, name FROM services")
            services_by_name = {r[1].lower().strip(): r[0] for r in cur.fetchall()}

            for row in rows:
                values = {}
                identity_values = []  # list of (column_id, value)
                notes = None
                service_names = []

                for header, val in row.items():
                    if not header:
                        continue
                    clean_header = header.lower().strip()
                    val = (val or "").strip()

                    if clean_header == "notes":
                        notes = val
                        continue

                    if clean_header in self.SERVICES_HEADER_ALIASES:
                        if val:
                            service_names.extend(
                                s.strip() for s in val.replace(";", ",").split(",") if s.strip()
                            )
                        continue

                    if clean_header.startswith("service:"):
                        sname = header.split(":", 1)[1].strip()
                        if val.lower() in ("yes", "true", "1", "y"):
                            service_names.append(sname)
                        continue

                    if clean_header in mcl:
                        col_info = mcl[clean_header]
                        if val:
                            values[col_info["id"]] = val
                            if col_info["is_identity"]:
                                identity_values.append((col_info["id"], val))
                    elif clean_header in self.SYSTEM_HEADERS:
                        continue
                    else:
                        skipped_columns.add(header)

                # Resolve service names to ids, warning about anything unrecognized
                matched_service_ids = []
                for sname in service_names:
                    sid = services_by_name.get(sname.lower())
                    if sid is not None:
                        matched_service_ids.append(sid)
                    else:
                        warnings.append(f"Unknown service '{sname}' -- skipped for this row.")

                # 1. First check if explicit Client ID is given in row
                matched_client_ids = set()
                explicit_client_id = None
                for header, val in row.items():
                    if header and header.lower().strip() in ("client id", "client_id") and (val or "").strip():
                        try:
                            explicit_client_id = int((val or "").strip())
                        except ValueError:
                            pass
                        break

                if explicit_client_id is not None:
                    ccur = conn.execute(
                        "SELECT id FROM clients WHERE id = ? AND is_archived = 0",
                        (explicit_client_id,)
                    )
                    r = ccur.fetchone()
                    if r:
                        matched_client_ids.add(r[0])

                # 2. If no direct Client ID match, fallback to matching across identity columns using INTERSECTION
                if not matched_client_ids and identity_values:
                    candidate_sets = []
                    for col_id, val in identity_values:
                        ccur = conn.execute(
                            """SELECT cv.client_id FROM client_values cv
                               JOIN clients c ON c.id = cv.client_id
                               WHERE cv.column_id = ? AND cv.value = ? AND c.is_archived = 0""",
                            (col_id, val)
                        )
                        cids = {r[0] for r in ccur.fetchall()}
                        candidate_sets.append(cids)

                    if candidate_sets:
                        common = candidate_sets[0]
                        for cs in candidate_sets[1:]:
                            common = common.intersection(cs)
                        matched_client_ids = common

                now = datetime.datetime.utcnow().isoformat()

                if len(matched_client_ids) == 1:
                    # UPDATE: merge this row's values into the existing client
                    # instead of creating a duplicate. Upsert avoids the
                    # (client_id, column_id) UNIQUE-constraint error you'd get
                    # from a plain INSERT on a column the client already has.
                    client_id = matched_client_ids.pop()
                    if notes:
                        conn.execute(
                            "UPDATE clients SET notes=?, updated_at=? WHERE id=?",
                            (notes, now, client_id)
                        )
                    else:
                        conn.execute(
                            "UPDATE clients SET updated_at=? WHERE id=?",
                            (now, client_id)
                        )
                    for col_id, val in values.items():
                        conn.execute(
                            """INSERT INTO client_values (client_id, column_id, value)
                               VALUES (?, ?, ?)
                               ON CONFLICT(client_id, column_id) DO UPDATE SET value=excluded.value""",
                            (client_id, col_id, val)
                        )
                    for sid in matched_service_ids:
                        conn.execute(
                            """INSERT INTO client_services (client_id, service_id) VALUES (?, ?)
                               ON CONFLICT(client_id, service_id) DO NOTHING""",
                            (client_id, sid)
                        )
                    updated += 1
                    continue

                if len(matched_client_ids) > 1:
                    warnings.append(
                        "A row matched more than one existing client on identity "
                        "value(s) " + ", ".join(v for _, v in identity_values) +
                        " -- imported as a new client instead of overwriting either one."
                    )

                # INSERT: no (unambiguous) existing client found
                cur = conn.execute(
                    "INSERT INTO clients (notes, created_at, updated_at) VALUES (?, ?, ?)",
                    (notes or "", now, now)
                )
                client_id = cur.lastrowid

                for col_id, val in values.items():
                    conn.execute(
                        "INSERT INTO client_values (client_id, column_id, value) VALUES (?, ?, ?)",
                        (client_id, col_id, val)
                    )
                for sid in matched_service_ids:
                    conn.execute(
                        "INSERT INTO client_services (client_id, service_id) VALUES (?, ?)",
                        (client_id, sid)
                    )
                imported += 1

        self.log_action(actor=actor, action="csv_import", detail=f"Imported {imported} client(s), updated {updated} from CSV")

        return {
            "imported": imported,
            "updated": updated,
            "skipped_columns": list(skipped_columns),
            "warnings": warnings
        }

    # ---------------- Bulk client operations (Admin Mode multi-select) ----------------

    def bulk_archive_clients(self, client_ids: list[int]):
        now = datetime.datetime.utcnow().isoformat()
        with self._connect() as conn:
            for cid in client_ids:
                conn.execute(
                    "UPDATE clients SET is_archived=1, updated_at=? WHERE id=?", (now, cid)
                )

    def bulk_unarchive_clients(self, client_ids: list[int]):
        now = datetime.datetime.utcnow().isoformat()
        with self._connect() as conn:
            for cid in client_ids:
                conn.execute(
                    "UPDATE clients SET is_archived=0, updated_at=? WHERE id=?", (now, cid)
                )

    def resequence_client_serial_numbers(self):
        """Resequences all non-archived clients' serial numbers in 1, 2, 3... order
        for the column with field_type == 'id' (or identity serial number column)."""
        id_col = self.get_id_column()
        if not id_col:
            cols = self.get_mcl_columns()
            for c in cols:
                lbl = c["label"].strip().lower()
                if lbl in {"no", "no.", "sl no", "sl. no.", "s.no.", "sno", "id", "#", "numer", "number"}:
                    id_col = c
                    break
        if not id_col:
            return

        with self._connect() as conn:
            clients = conn.execute("SELECT id FROM clients WHERE is_archived = 0 ORDER BY id ASC").fetchall()
            for idx, (cid,) in enumerate(clients, start=1):
                conn.execute(
                    "INSERT INTO client_values (client_id, column_id, value) VALUES (?, ?, ?) "
                    "ON CONFLICT(client_id, column_id) DO UPDATE SET value = excluded.value",
                    (cid, id_col["id"], str(idx))
                )

    def bulk_delete_clients(self, client_ids: list[int]):
        with self._connect() as conn:
            for cid in client_ids:
                conn.execute("DELETE FROM clients WHERE id=?", (cid,))

    def purge_duplicate_clients(self) -> dict:
        """Finds non-archived clients that share identical identity-column values
        and deletes the newer duplicates, keeping the original (lowest client ID).

        Returns:
            {
                "duplicates_found": int,   # total duplicate rows detected
                "deleted": int,            # rows actually deleted
                "groups": int,             # how many unique identity fingerprints had dupes
                "details": list[str]       # human-readable summary per group
            }
        """
        import re

        def _norm(s: str) -> str:
            if not s:
                return ""
            return re.sub(r'[^a-z0-9]', '', s.lower())

        with self._connect() as conn:
            # 1. Get identity columns, excluding serial number / index columns (like "No.", "Sl No", "ID")
            raw_id_cols = conn.execute(
                "SELECT id, label FROM mcl_columns WHERE is_identity = 1 ORDER BY sort_order"
            ).fetchall()

            ignored_labels = {"no", "no.", "sl no", "sl. no.", "s.no.", "sno", "id", "#"}
            id_cols = [r[0] for r in raw_id_cols if r[1].strip().lower() not in ignored_labels]

            # If no valid identity columns remain, fallback to company, proprietor, firm, name, gstin, pan columns
            if not id_cols:
                all_cols = conn.execute("SELECT id, label FROM mcl_columns ORDER BY sort_order").fetchall()
                id_cols = [
                    r[0] for r in all_cols
                    if r[1].strip().lower() not in ignored_labels and any(
                        k in r[1].upper() for k in ["COMPANY", "PROPRIETOR", "FIRM", "NAME", "GSTIN", "PAN"]
                    )
                ]

            if not id_cols:
                return {"duplicates_found": 0, "deleted": 0, "groups": 0,
                        "details": ["No identity columns defined — cannot detect duplicates."]}

            # 2. Build normalized identity fingerprint for every non-archived client
            client_ids = [
                r[0] for r in conn.execute(
                    "SELECT id FROM clients WHERE is_archived = 0 ORDER BY id"
                ).fetchall()
            ]

            fingerprints = {}  # fingerprint_tuple -> [client_id, ...]
            client_labels = {}  # client_id -> fingerprint display string

            for cid in client_ids:
                vals = []
                raw_display_vals = []
                for col_id in id_cols:
                    cur = conn.execute(
                        "SELECT value FROM client_values WHERE client_id = ? AND column_id = ?",
                        (cid, col_id)
                    )
                    row = cur.fetchone()
                    raw_val = row[0] if row and row[0] else ""
                    norm_val = _norm(raw_val)
                    vals.append(norm_val)
                    if raw_val.strip():
                        raw_display_vals.append(raw_val.strip())

                fp = tuple(vals)
                # Skip clients with completely empty identity (can't match)
                if all(v == "" for v in fp):
                    continue

                fingerprints.setdefault(fp, []).append(cid)
                client_labels[cid] = " | ".join(raw_display_vals)

            # 3. For each group with >1 client, keep lowest ID, delete the rest
            deleted = 0
            groups = 0
            details = []

            for fp, cids in fingerprints.items():
                if len(cids) <= 1:
                    continue
                groups += 1
                original = cids[0]  # lowest ID = oldest
                dupes = cids[1:]
                label = client_labels.get(original, str(fp))
                details.append(
                    f"\"{label}\": kept #{original}, deleted {len(dupes)} duplicate(s) "
                    f"(IDs: {', '.join(str(d) for d in dupes)})"
                )
                for dupe_id in dupes:
                    conn.execute("DELETE FROM clients WHERE id = ?", (dupe_id,))
                    deleted += 1

        return {
            "duplicates_found": deleted,
            "deleted": deleted,
            "groups": groups,
            "details": details
        }

    def bulk_set_service(self, client_ids: list[int], service_id: int, attach: bool):
        """Attaches (or detaches) one service to/from every client in
        client_ids -- the multi-select equivalent of ticking (or unticking)
        that service's checkbox on each client one at a time."""
        with self._connect() as conn:
            for cid in client_ids:
                if attach:
                    conn.execute(
                        """INSERT INTO client_services (client_id, service_id) VALUES (?, ?)
                           ON CONFLICT(client_id, service_id) DO NOTHING""",
                        (cid, service_id)
                    )
                else:
                    conn.execute(
                        "DELETE FROM client_services WHERE client_id=? AND service_id=?",
                        (cid, service_id)
                    )
        try:
            for cid in client_ids:
                self.record_client_activity(cid, "Services", "Service attached" if attach else "Service detached")
        except Exception:
            pass

    # ---------------- Audit Log ----------------

    def log_action(self, actor: str, action: str, client_id: int = None, service_id: int = None, detail: str = None):
        actor_name = actor or "System"
        ts = datetime.datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO audit_log (ts, actor, action, client_id, service_id, detail)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (ts, actor_name, action, client_id, service_id, detail)
            )
        
        # Automatically record client activity breadcrumbs on mutations
        if client_id:
            try:
                act_norm = str(action).lower()
                tag = "Edited"
                if "update" in act_norm or "edit" in act_norm or "save" in act_norm:
                    tag = "Edited"
                elif "create" in act_norm or "add" in act_norm:
                    tag = "Created"
                elif "archive" in act_norm:
                    tag = "Archived"
                elif "unarchive" in act_norm or "restore" in act_norm:
                    tag = "Restored"
                elif "note" in act_norm:
                    tag = "Note"
                elif "service" in act_norm:
                    tag = "Services"
                elif "view" in act_norm:
                    tag = "Viewed"
                elif "copy" in act_norm:
                    tag = "Copied"
                elif "sca" in act_norm:
                    tag = "SCA"
                else:
                    tag = action.capitalize()[:10]
                self.record_client_activity(int(client_id), tag, detail or f"{tag} by {actor_name}")
            except Exception:
                pass

        # "view" is read-only and shouldn't trigger a sync push; everything
        # else logged here already represents a real data mutation somewhere
        # in this module, so it's the cheapest reliable place to hook.
        if action != "view":
            self._bump_sync_revision_if_configured()

    def get_sync_metrics(self) -> dict:
        """
        Returns database structural metrics used by LAN Sync engine to evaluate revisions:
        - client_count: Total active non-deleted client records
        - archived_count: Total archived client records
        - log_count: Total SSAL audit log entries
        - tracker_count: Total tracker dump captures in rawPayload.db
        - timeline_count: Total SDC session timelines in rawPayload.db
        - latest_timestamp: ISO timestamp of most recent audit log / capture entry
        - sync_revision: Structural database revision score
        """
        try:
            with self._connect() as conn:
                cur = conn.execute("SELECT COUNT(*) FROM clients WHERE is_deleted = 0")
                client_count = cur.fetchone()[0]

                cur = conn.execute("SELECT COUNT(*) FROM clients WHERE is_deleted = 1")
                archived_count = cur.fetchone()[0]

                cur = conn.execute("SELECT COUNT(*), MAX(ts) FROM audit_log")
                row = cur.fetchone()
                log_count = row[0] if row and row[0] else 0
                latest_ts = row[1] if row and row[1] else ""

            tracker_count = 0
            timeline_count = 0
            latest_dump_ts = ""
            try:
                with self._connect_raw() as r_conn:
                    cur = r_conn.execute("SELECT COUNT(*), MAX(created_at) FROM tracker_dump")
                    r_row = cur.fetchone()
                    tracker_count = r_row[0] if r_row and r_row[0] else 0
                    latest_dump_ts = r_row[1] if r_row and r_row[1] else ""

                    cur = r_conn.execute("SELECT COUNT(*), MAX(last_updated) FROM sdc_session_timelines")
                    t_row = cur.fetchone()
                    timeline_count = t_row[0] if t_row and t_row[0] else 0
            except Exception:
                pass

            if latest_dump_ts and latest_dump_ts > latest_ts:
                latest_ts = latest_dump_ts

            sync_revision = (client_count * 10000) + (log_count * 10) + (tracker_count * 5) + timeline_count

            return {
                "client_count": client_count,
                "archived_count": archived_count,
                "log_count": log_count,
                "tracker_count": tracker_count,
                "timeline_count": timeline_count,
                "latest_timestamp": latest_ts,
                "sync_revision": sync_revision,
            }
        except Exception:
            return {
                "client_count": 0,
                "archived_count": 0,
                "log_count": 0,
                "tracker_count": 0,
                "timeline_count": 0,
                "latest_timestamp": "",
                "sync_revision": 0,
            }

    def get_audit_logs(
        self,
        client_id: int = None,
        actor: str = None,
        action: str = None,
        from_date: str = None,
        to_date: str = None,
        resolve_names: bool = True,
        limit: int = 500
    ) -> list[dict]:
        with self._connect() as conn:
            sql = "SELECT id, ts, actor, action, client_id, service_id, detail FROM audit_log"
            where = []
            params = []
            if client_id is not None:
                where.append("client_id = ?")
                params.append(client_id)
            if actor:
                where.append("actor LIKE ?")
                params.append(f"%{actor}%")
            if action and action != "All Actions":
                where.append("action = ?")
                params.append(action)
            if from_date:
                where.append("ts >= ?")
                params.append(from_date)
            if to_date:
                if to_date.endswith("T00:00:00"):
                    where.append("ts < ?")
                else:
                    where.append("ts <= ?")
                params.append(to_date)
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY id DESC LIMIT ?"
            params.append(limit)

            cur = conn.execute(sql, params)
            rows = cur.fetchall()

            client_name_map = {}
            if resolve_names:
                try:
                    cur_vals = conn.execute("""
                        SELECT cv.client_id, cv.value
                        FROM client_values cv
                        JOIN mcl_columns mc ON mc.id = cv.column_id
                        WHERE cv.value IS NOT NULL AND cv.value != ''
                          AND LOWER(TRIM(mc.label)) NOT IN ('no', 'no.', 'sl no', 'sl. no.', 's.no.', 'sno', 'id', '#')
                        ORDER BY mc.is_identity DESC, mc.sort_order ASC
                    """).fetchall()
                    for cid_val, val_text in cur_vals:
                        if cid_val not in client_name_map:
                            client_name_map[cid_val] = val_text
                except Exception:
                    pass

            results = []
            for r in rows:
                cid = r[4]
                cname = client_name_map.get(cid) if cid else None
                client_label = cname if cname else (f"CLI-{cid:05d}" if cid else "—")
                results.append({
                    "id": r[0], "ts": r[1], "actor": r[2], "action": r[3],
                    "client_id": cid, "client_name": client_label,
                    "client_token": f"CLI-{cid:05d}" if cid else "",
                    "service_id": r[5], "detail": r[6]
                })
            return results

    # ---------------- Backup & Restore ----------------

    def backup_to(self, dest_dir: str) -> str:
        """Backs up master.db and sera.salt into a timestamped directory under dest_dir."""
        now_str = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        target_folder = os.path.join(dest_dir, f"sera_backup_{now_str}")
        os.makedirs(target_folder, exist_ok=True)

        target_db = os.path.join(target_folder, "master.db")
        shutil.copy2(self.db_path, target_db)

        salt_path = os.path.join(os.path.dirname(self.db_path), security.SALT_FILE)
        if os.path.exists(salt_path):
            shutil.copy2(salt_path, os.path.join(target_folder, security.SALT_FILE))

        return target_folder

    def restore_from(self, target_path: str, master_password: str = None) -> str:
        """
        Restores database and salt from target_path (folder or file path).
        Supports Syncthing conflict files (master.db.sync-conflict-*),
        built-in sync_peer conflict files (master.db.conflict-*), and pre-sync backups.
        Validates SQLCipher decryption before overwriting live database.
        Returns a human-readable summary string of restored components.
        """
        if not target_path or not os.path.exists(target_path):
            raise FileNotFoundError(f"Selected restore path does not exist: {target_path}")

        if os.path.isfile(target_path):
            backup_dir = os.path.dirname(target_path)
            candidate_dbs = [target_path]
        else:
            backup_dir = target_path
            candidate_dbs = []
            standard_db = os.path.join(backup_dir, "master.db")
            if os.path.exists(standard_db):
                candidate_dbs.append(standard_db)
            
            # Scan for Syncthing conflict files, sync_peer conflict files, and other .db files
            for entry in os.listdir(backup_dir):
                full_p = os.path.join(backup_dir, entry)
                if os.path.isfile(full_p) and full_p not in candidate_dbs:
                    lower = entry.lower()
                    if lower.endswith(".db") or "sync-conflict" in lower or ".conflict-" in lower or ".pre-sync-" in lower or lower.startswith("master"):
                        candidate_dbs.append(full_p)
            
            # Sort candidates by modification time, newest first
            candidate_dbs.sort(key=lambda p: os.path.getmtime(p), reverse=True)

        if not candidate_dbs:
            raise FileNotFoundError(f"No valid database (.db) files found in {backup_dir}")

        # Scan for candidate salt files
        live_dir = os.path.dirname(self.db_path)
        live_salt = os.path.join(live_dir, security.SALT_FILE)
        
        candidate_salts = []
        standard_salt = os.path.join(backup_dir, security.SALT_FILE)
        if os.path.exists(standard_salt):
            candidate_salts.append(standard_salt)
            
        for entry in os.listdir(backup_dir):
            full_p = os.path.join(backup_dir, entry)
            if os.path.isfile(full_p) and full_p not in candidate_salts:
                lower = entry.lower()
                if "salt" in lower or lower.endswith(".salt") or "sync-conflict" in lower or ".conflict-" in lower:
                    candidate_salts.append(full_p)
                    
        if os.path.exists(live_salt) and live_salt not in candidate_salts:
            candidate_salts.append(live_salt)

        if not candidate_salts:
            raise FileNotFoundError(f"No salt (sera.salt) files found in backup or live folder.")

        # Attempt to find a valid (db, salt, hex_key) pair that decrypts cleanly
        matched_db = None
        matched_salt = None
        matched_hex_key = None

        for db_file in candidate_dbs:
            for salt_file in candidate_salts:
                try:
                    salt_bytes = security.load_salt(salt_file)
                    if master_password:
                        test_key = security.derive_key_hex(master_password, salt_bytes)
                    else:
                        test_key = self.hex_key
                    
                    # Test SQLCipher opening and table query
                    conn = sqlite3.connect(db_file)
                    conn.execute(f"PRAGMA key = \"x'{test_key}'\";")
                    cur = conn.cursor()
                    cur.execute("SELECT count(*) FROM sqlite_master;")
                    cur.fetchone()
                    conn.close()

                    # Decryption succeeded!
                    matched_db = db_file
                    matched_salt = salt_file
                    matched_hex_key = test_key
                    break
                except Exception:
                    try:
                        conn.close()
                    except Exception:
                        pass
            if matched_db:
                break

        if not matched_db or not matched_salt or not matched_hex_key:
            raise ValueError(
                "Could not decrypt any database candidate in the backup folder.\n"
                "Please verify that the master password is correct or that the matching salt file is present."
            )

        # Create safety backup of current live database
        now_str = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        pre_db = os.path.join(live_dir, f"master.db.pre-restore-{now_str}")
        shutil.copy2(self.db_path, pre_db)
        if os.path.exists(live_salt):
            pre_salt = os.path.join(live_dir, f"sera.salt.pre-restore-{now_str}")
            shutil.copy2(live_salt, pre_salt)

        # Close any open connections before file replacement
        self.close()

        # Overwrite live files with validated Syncthing conflict/backup pair
        shutil.copy2(matched_db, self.db_path)
        shutil.copy2(matched_salt, live_salt)
        self.hex_key = matched_hex_key

        db_name = os.path.basename(matched_db)
        salt_name = os.path.basename(matched_salt)
        return f"Database restored from '{db_name}' using salt '{salt_name}'."

    # ---------------- CSV Export ----------------

    def export_clients_csv(self, filepath: str):
        import csv
        with self._connect() as conn:
            mcl = self.get_mcl_columns()
            services = self.get_services()
            
            headers = ["Client ID", "Created At", "Updated At", "Is Archived"]
            headers += [c["label"] for c in mcl]
            headers += [f"Service: {s['name']}" for s in services]
            headers.append("Notes")
            
            clients = self.search_clients("", include_archived=True)
            
            with open(filepath, mode="w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                
                for client in clients:
                    row = [
                        client["id"], client["created_at"], client["updated_at"],
                        "Yes" if client.get("is_archived") else "No"
                    ]
                    for col in mcl:
                        row.append(client["values"].get(col["id"], ""))
                    for s in services:
                        row.append("Yes" if s["id"] in client.get("service_ids", []) else "No")
                    row.append(client.get("notes", ""))
                    writer.writerow(row)

    def export_audit_log_csv(self, filepath: str):
        import csv
        logs = self.get_audit_logs(limit=10000, resolve_names=False)
        with open(filepath, mode="w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["Log ID", "Timestamp (UTC)", "Actor", "Action", "Client Token", "Service ID", "Detail"])
            for l in logs:
                token = f"CLI-{l['client_id']:05d}" if l.get("client_id") else "—"
                writer.writerow([l["id"], l["ts"], l["actor"], l["action"], token, l["service_id"] or "", l["detail"] or ""])

    def export_mcl_schema_csv(self, filepath: str):
        import csv
        with self._connect() as conn:
            mcl = self.get_mcl_columns()
            services = self.get_services()
            
            headers = ["Client ID", "Created At", "Updated At", "Is Archived"]
            headers += [c["label"] for c in mcl]
            headers += [f"Service: {s['name']}" for s in services]
            headers.append("Notes")
            
            example_row = ["", "", "", "No"]
            for c in mcl:
                if c["is_identity"]:
                    example_row.append("Example Identity Value")
                else:
                    example_row.append("Example Value")
            for s in services:
                example_row.append("Yes")
            example_row.append("Example — delete this row before importing")
            
            with open(filepath, mode="w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                writer.writerow(example_row)

    # ---------------- Sync Conflict Detection ----------------

    def get_sync_conflicts(self) -> list[str]:
        """Scans the database directory for unresolved sync conflict files:
        both legacy Syncthing markers (master.db.sync-conflict-*) for any
        machine mid-migration, and the built-in LAN sync's own markers
        (master.db.conflict-*) written by sync_peer.py when two machines
        genuinely diverge. Does NOT flag master.db.pre-sync-* or
        master.db.pre-restore-* files, since those are routine safety
        copies made on every clean sync/restore, not conflict evidence."""
        db_dir = os.path.dirname(self.db_path)
        if not os.path.exists(db_dir):
            return []
        conflicts = []
        for filename in os.listdir(db_dir):
            if "sync-conflict" in filename or ".conflict-" in filename:
                conflicts.append(os.path.join(db_dir, filename))
        return sorted(conflicts)

    def update_client_notes(self, client_id: int, notes: str):
        """Updates notes for a specific client record."""
        now = datetime.datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE clients SET notes = ?, updated_at = ? WHERE id = ?",
                (notes, now, client_id)
            )
        try:
            self.record_client_activity(client_id, "Note", "Updated client notes")
        except Exception:
            pass

    # ---------------- Tracker Dump Subsystem (SAD & Extension) ----------------

    def _extract_identity_candidates_from_payload(self, arn_number: str = None, pan: str = None, raw_payload_json: str = None) -> list[str]:
        """Extracts all legal identity candidates (PAN, GSTIN, TAN) from payload and ARN."""
        import re
        candidates = []

        def _add(val):
            if val and isinstance(val, (str, int)):
                cleaned = str(val).strip().upper()
                if not cleaned:
                    return
                # Valid 10-char PAN
                if re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", cleaned):
                    if cleaned not in candidates:
                        candidates.append(cleaned)
                # Valid 15-char GSTIN
                elif re.match(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$", cleaned):
                    if cleaned not in candidates:
                        candidates.append(cleaned)
                    pan_part = cleaned[2:12]
                    if re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", pan_part) and pan_part not in candidates:
                        candidates.append(pan_part)
                # Valid 10-char TAN
                elif re.match(r"^[A-Z]{4}[0-9]{5}[A-Z]$", cleaned):
                    if cleaned not in candidates:
                        candidates.append(cleaned)

        if pan:
            _add(pan)

        if arn_number:
            arn_str = str(arn_number).strip()
            if arn_str.upper().startswith("PROFILE-") or arn_str.upper().startswith("PROF-"):
                token = arn_str.split("-", 1)[1].strip()
                _add(token)
            elif re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", arn_str.upper()):
                _add(arn_str)

        def _scan_deep(item, depth=6):
            if depth <= 0 or item is None:
                return
            if isinstance(item, dict):
                # Priority target keys first
                for k, v in item.items():
                    k_lower = str(k).lower()
                    if any(target in k_lower for target in ("entitynum", "entityid", "userpan", "pan", "gstin", "tan", "userid", "taxpayer", "submitby", "acknum")):
                        if isinstance(v, (str, int)):
                            _add(v)
                # Traverse child objects and values
                for k, v in item.items():
                    if isinstance(v, (dict, list)):
                        _scan_deep(v, depth - 1)
                    elif isinstance(v, (str, int)):
                        _add(v)
            elif isinstance(item, list):
                for v in item:
                    _scan_deep(v, depth - 1)

        if raw_payload_json:
            try:
                payload = json.loads(raw_payload_json) if isinstance(raw_payload_json, str) else raw_payload_json
                _scan_deep(payload)
            except Exception:
                pass

        return candidates

    def _update_srpf_container(self, r_conn, identity_key: str, client_id: Optional[int], dump_row: dict):
        """SRPF Stage 1: Groups raw capture entries into a unified client container in rawPayload.db."""
        from ui.utils.profile_parser import extract_profile_from_payload
        if not identity_key:
            return
        clean_key = str(identity_key).strip().upper()
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        dump_ts = dump_row.get("created_at") or now
        cur = r_conn.execute("SELECT * FROM client_raw_containers WHERE identity_key = ?", (clean_key,))
        existing = cur.fetchone()

        payload_obj = dump_row.get("raw_payload_json") or "{}"
        if isinstance(payload_obj, str):
            try:
                payload_obj = json.loads(payload_obj)
            except Exception:
                payload_obj = {}

        new_profile = extract_profile_from_payload(payload_obj)

        if existing:
            # existing schema: (identity_key, client_id, company_name, proprietor_name, pan, gstin, tan, phone, email, dob, user_id, portal_profiles, filing_history, raw_aggregates, total_captures, last_updated)
            cid = client_id or existing[1]
            comp = new_profile.get("company_name") or existing[2] or ""
            prop = new_profile.get("proprietor_name") or existing[3] or ""
            pan_val = new_profile.get("pan") or existing[4] or ""
            gst_val = new_profile.get("gstin") or existing[5] or ""
            tan_val = new_profile.get("tan") or existing[6] or ""
            ph_val = new_profile.get("phone") or existing[7] or ""
            em_val = new_profile.get("email") or existing[8] or ""
            dob_val = new_profile.get("dob") or existing[9] or ""
            uid_val = new_profile.get("user_id") or existing[10] or ""

            try:
                hist = json.loads(existing[12]) if existing[12] else []
            except Exception:
                hist = []

            arn_str = dump_row.get("arn_number")
            if arn_str and not any(h.get("arn") == arn_str for h in hist):
                hist.append({
                    "portal": dump_row.get("portal"),
                    "arn": arn_str,
                    "period_label": dump_row.get("period_label"),
                    "capture_method": dump_row.get("capture_method"),
                    "created_at": dump_ts
                })

            tot_caps = (existing[14] or 0) + 1
            existing_ts = str(existing[15] or "")
            final_last_updated = max(existing_ts, dump_ts) if existing_ts else dump_ts

            r_conn.execute("""
                UPDATE client_raw_containers
                SET client_id = ?, company_name = ?, proprietor_name = ?, pan = ?, gstin = ?, tan = ?, phone = ?, email = ?, dob = ?, user_id = ?, filing_history = ?, total_captures = ?, last_updated = ?
                WHERE identity_key = ?
            """, (cid, comp, prop, pan_val, gst_val, tan_val, ph_val, em_val, dob_val, uid_val, json.dumps(hist), tot_caps, final_last_updated, clean_key))
        else:
            hist = []
            arn_str = dump_row.get("arn_number")
            if arn_str:
                hist.append({
                    "portal": dump_row.get("portal"),
                    "arn": arn_str,
                    "period_label": dump_row.get("period_label"),
                    "capture_method": dump_row.get("capture_method"),
                    "created_at": dump_ts
                })
            r_conn.execute("""
                INSERT INTO client_raw_containers
                (identity_key, client_id, company_name, proprietor_name, pan, gstin, tan, phone, email, dob, user_id, portal_profiles, filing_history, raw_aggregates, total_captures, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                clean_key, client_id,
                new_profile.get("company_name", ""), new_profile.get("proprietor_name", ""),
                new_profile.get("pan", ""), new_profile.get("gstin", ""), new_profile.get("tan", ""),
                new_profile.get("phone", ""), new_profile.get("email", ""), new_profile.get("dob", ""),
                new_profile.get("user_id", ""), "{}", json.dumps(hist), "{}", 1, dump_ts
            ))

    def get_client_raw_container(self, client_id: int = None, identity_key: str = None) -> Optional[dict]:
        """SRPF Stage 1: Retrieves unified raw container by client_id or identity_key (PAN/GSTIN/TAN) from rawPayload.db."""
        with self._connect_raw() as conn:
            if identity_key:
                cur = conn.execute("SELECT * FROM client_raw_containers WHERE UPPER(identity_key) = ?", (identity_key.strip().upper(),))
            elif client_id:
                cur = conn.execute("SELECT * FROM client_raw_containers WHERE client_id = ? ORDER BY total_captures DESC LIMIT 1", (client_id,))
            else:
                return None
            row = cur.fetchone()
            if not row:
                return None
            return {
                "identity_key": row[0],
                "client_id": row[1],
                "company_name": row[2] or "",
                "proprietor_name": row[3] or "",
                "pan": row[4] or "",
                "gstin": row[5] or "",
                "tan": row[6] or "",
                "phone": row[7] or "",
                "email": row[8] or "",
                "dob": row[9] or "",
                "user_id": row[10] or "",
                "portal_profiles": json.loads(row[11]) if row[11] else {},
                "filing_history": json.loads(row[12]) if row[12] else [],
                "raw_aggregates": json.loads(row[13]) if row[13] else {},
                "total_captures": row[14] or 0,
                "last_updated": row[15]
            }

    def get_srpf_containers(self, limit: int = 200, search_query: str = None) -> list[dict]:
        """SRPF Phase 1 Filtration: Returns grouped client containers from rawPayload.db.
        Each distinct client or unregistered entity is aggregated into exactly ONE container row."""
        with self._connect_raw() as r_conn:
            # 1. Auto-sync tracker_dump records into client_raw_containers if needed
            cur = r_conn.execute("SELECT COUNT(*) FROM client_raw_containers")
            container_count = cur.fetchone()[0]
            if container_count == 0:
                self.re_resolve_all_tracker_dumps()

            # 2. Query containers
            sql = "SELECT identity_key, client_id, company_name, proprietor_name, pan, gstin, tan, phone, email, dob, user_id, portal_profiles, filing_history, raw_aggregates, total_captures, last_updated FROM client_raw_containers WHERE 1=1"
            params = []
            if search_query:
                q = f"%{search_query}%"
                sql += " AND (identity_key LIKE ? OR company_name LIKE ? OR proprietor_name LIKE ? OR pan LIKE ? OR gstin LIKE ? OR phone LIKE ? OR email LIKE ?)"
                params.extend([q, q, q, q, q, q, q])
            sql += " ORDER BY last_updated DESC LIMIT ?"
            params.append(limit)

            cur = r_conn.execute(sql, params)
            rows = cur.fetchall()

        if not rows:
            return []

        # 3. Enrich with master.db client info
        mcl_cols = self.get_mcl_columns()
        client_map = {}
        with self._connect() as m_conn:
            unique_cids = {r[1] for r in rows if r[1]}
            for cid in unique_cids:
                try:
                    cdata = self._fetch_client_full(m_conn, cid)
                    if cdata:
                        c_vals = cdata.get("values", {})
                        name_val = ""
                        pan_val = ""
                        for col in mcl_cols:
                            lbl = col.get("label", "").lower()
                            val = c_vals.get(col["id"], "")
                            if val and not name_val and any(k in lbl for k in ["name", "party", "client"]):
                                name_val = str(val).strip()
                            elif val and not pan_val and any(k in lbl for k in ["pan", "gstin", "gst"]):
                                pan_val = str(val).strip()
                        client_map[cid] = {
                            "name": name_val or cdata.get("client_id_token", str(cid)),
                            "pan": pan_val,
                            "client_id_token": cdata.get("client_id_token", f"CLI-{cid:05d}")
                        }
                except Exception:
                    pass

        containers = []
        for r in rows:
            cid = r[1]
            identity_key = r[0]
            comp_name = r[2] or ""
            prop_name = r[3] or ""
            pan_val = r[4] or ""
            gst_val = r[5] or ""
            tan_val = r[6] or ""
            phone_val = r[7] or ""
            email_val = r[8] or ""
            dob_val = r[9] or ""
            user_id_val = r[10] or ""
            try:
                filing_hist = json.loads(r[12]) if r[12] else []
            except Exception:
                filing_hist = []

            # Format summary of filings
            latest_arn = filing_hist[-1].get("arn", "N/A") if filing_hist else "N/A"
            latest_portal = filing_hist[-1].get("portal", "Portal") if filing_hist else "Portal"
            latest_period = filing_hist[-1].get("period_label", "") if filing_hist else ""
            capture_method = filing_hist[-1].get("capture_method", "SAD_API_Interceptor") if filing_hist else "SAD_API_Interceptor"

            periods = [h.get("period_label") for h in filing_hist if h.get("period_label") and h.get("period_label") != "N/A"]
            if len(periods) > 1:
                period_summary = f"{len(filing_hist)} Filings ({periods[0]} to {periods[-1]})"
            elif len(periods) == 1:
                period_summary = f"1 Filing ({periods[0]})"
            else:
                period_summary = f"{len(filing_hist) or r[14] or 1} Capture(s)"

            is_unassigned = not bool(cid)
            if cid and cid in client_map:
                c_info = client_map[cid]
                display_name = f"{c_info['name']} ({c_info['pan'] or pan_val or identity_key})"
                display_pan = c_info['pan'] or pan_val
                id_token = c_info.get("client_id_token", f"CLI-{cid:05d}")
            elif comp_name or prop_name:
                display_name = f"{comp_name or prop_name} ({pan_val or gst_val or identity_key})"
                display_pan = pan_val or gst_val or identity_key
                id_token = "Unregistered"
            else:
                display_name = f"Unregistered ({identity_key})"
                display_pan = identity_key
                id_token = "Unregistered"

            containers.append({
                "identity_key": identity_key,
                "client_id": cid,
                "client_id_token": id_token,
                "is_unassigned": is_unassigned,
                "display_name": display_name,
                "company_name": comp_name,
                "proprietor_name": prop_name,
                "pan": display_pan,
                "gstin": gst_val,
                "tan": tan_val,
                "phone": phone_val,
                "email": email_val,
                "dob": dob_val,
                "user_id": user_id_val,
                "portal": latest_portal,
                "period_summary": period_summary,
                "latest_period": latest_period,
                "latest_arn": latest_arn,
                "capture_method": capture_method,
                "total_captures": len(filing_hist) or r[14] or 1,
                "filing_history": filing_hist,
                "last_updated": r[15]
            })

        return containers

    def get_captures_for_container(self, identity_key: str = None, client_id: int = None, pan: str = None) -> list[dict]:
        """Fetches all raw tracker_dump records associated with a specific client, identity key, or PAN."""
        with self._connect_raw() as r_conn:
            sql = "SELECT id, client_id, unassigned_identity, service_id, portal, period_label, arn_number, capture_method, status, raw_payload_json, captured_by, created_at FROM tracker_dump WHERE "
            conditions = []
            params = []
            if client_id:
                conditions.append("client_id = ?")
                params.append(client_id)
            if pan:
                clean_p = pan.strip().upper()
                conditions.append("(unassigned_identity = ? OR raw_payload_json LIKE ? OR arn_number LIKE ?)")
                params.extend([clean_p, f'%"{clean_p}"%', f'%{clean_p}%'])
            if identity_key and not client_id and not pan:
                clean_k = identity_key.strip().upper()
                conditions.append("(unassigned_identity = ? OR raw_payload_json LIKE ? OR arn_number LIKE ?)")
                params.extend([clean_k, f'%"{clean_k}"%', f'%{clean_k}%'])

            if not conditions:
                sql += "1=1 ORDER BY created_at ASC LIMIT 100"
            else:
                sql += " OR ".join(conditions) + " ORDER BY created_at ASC"

            cur = r_conn.execute(sql, params)
            desc = [c[0] for c in cur.description]
            return [dict(zip(desc, row)) for row in cur.fetchall()]

    def delete_srpf_container(self, identity_key: str) -> bool:
        """Deletes a container and its associated captures from rawPayload.db."""
        if not identity_key:
            return False
        clean = str(identity_key).strip().upper()
        with self._connect_raw() as conn:
            conn.execute("DELETE FROM client_raw_containers WHERE UPPER(identity_key) = ?", (clean,))
            conn.execute("""
                DELETE FROM tracker_dump
                WHERE UPPER(TRIM(unassigned_identity)) = ?
                   OR UPPER(TRIM(arn_number)) LIKE ?
                   OR UPPER(raw_payload_json) LIKE ?
            """, (clean, f"%{clean}%", f"%{clean}%"))
        self.rebuild_raw_payload_dumps_file()
        return True

    def _resolve_session_proximity_candidate(self, portal: str, timestamp_str: str, max_seconds: int = 900, session_id: str = None) -> Optional[str]:
        """Resolves PAN/GSTIN candidate from the immediate most recent session capture (registered or unregistered)."""
        if not portal or not timestamp_str:
            return None
            
        base_portal = portal.split(" (")[0].strip().lower()
        
        try:
            import re
            from datetime import datetime, timezone
            t0 = datetime.fromisoformat(timestamp_str)
            with self._connect_raw() as r_conn:
                # 1. If session_id is provided, search specifically for it first
                if session_id:
                    cur = r_conn.execute(
                        "SELECT arn_number, unassigned_identity, raw_payload_json, created_at, client_id FROM tracker_dump WHERE raw_payload_json LIKE ? ORDER BY id DESC LIMIT 50",
                        (f'%"{session_id}"%',)
                    )
                    for arn_num, unassigned_id, raw_json, c_at, cid in cur.fetchall():
                        if unassigned_id and re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", str(unassigned_id).strip().upper()):
                            return str(unassigned_id).strip().upper()
                        cands = self._extract_identity_candidates_from_payload(arn_number=arn_num, raw_payload_json=raw_json)
                        if cands:
                            return cands[0]
                        if cid:
                            with self._connect() as m_conn:
                                cur_m = m_conn.execute(
                                    "SELECT cv.value FROM client_values cv JOIN mcl_columns mc ON mc.id = cv.column_id WHERE cv.client_id = ? AND mc.is_internal_pk = 1 LIMIT 1",
                                    (cid,)
                                )
                                row_m = cur_m.fetchone()
                                if row_m and row_m[0] and re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", str(row_m[0]).strip().upper()):
                                    return str(row_m[0]).strip().upper()

                # 2. Fallback to time-based proximity matching (ordered by ID descending to get immediate last PAN)
                cur = r_conn.execute(
                    """SELECT arn_number, unassigned_identity, raw_payload_json, created_at, portal, client_id 
                       FROM tracker_dump 
                       WHERE created_at IS NOT NULL 
                       ORDER BY id DESC LIMIT 100"""
                )
                for arn_num, unassigned_id, raw_json, c_at, p_name, cid in cur.fetchall():
                    if not c_at or not p_name:
                        continue
                        
                    if p_name.split(" (")[0].strip().lower() != base_portal:
                        continue
                        
                    try:
                        tn = datetime.fromisoformat(c_at)
                        if abs((t0 - tn).total_seconds()) <= max_seconds:
                            if unassigned_id and re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", str(unassigned_id).strip().upper()):
                                return str(unassigned_id).strip().upper()
                            cands = self._extract_identity_candidates_from_payload(arn_number=arn_num, raw_payload_json=raw_json)
                            if cands:
                                return cands[0]
                            if cid:
                                with self._connect() as m_conn:
                                    cur_m = m_conn.execute(
                                        "SELECT cv.value FROM client_values cv JOIN mcl_columns mc ON mc.id = cv.column_id WHERE cv.client_id = ? AND mc.is_internal_pk = 1 LIMIT 1",
                                        (cid,)
                                    )
                                    row_m = cur_m.fetchone()
                                    if row_m and row_m[0] and re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", str(row_m[0]).strip().upper()):
                                        return str(row_m[0]).strip().upper()
                    except Exception:
                        pass
        except Exception:
            pass
        return None

    def insert_tracker_dump(self, client_id: int = None, service_id: int = None, portal: str = None,
                            period_label: str = None, arn_number: str = None,
                            capture_method: str = "DOM_Tracker", status: str = "submitted",
                            raw_payload_json: str = None, captured_by: str = "System",
                            pan: str = None, session_id: str = None) -> dict:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        candidates = self._extract_identity_candidates_from_payload(arn_number=arn_number, pan=pan, raw_payload_json=raw_payload_json)

        # If no identity found in wizard payload, attempt proximity resolution from same portal session
        if not candidates and portal:
            prox_cand = self._resolve_session_proximity_candidate(portal, now, max_seconds=900, session_id=session_id)
            if prox_cand:
                candidates = [prox_cand]

        valid_id = None
        unassigned_identity = None

        # 1. Authoritative Match: Resolve identity against master.db vault
        with self._connect() as m_conn:
            if candidates:
                for cand in candidates:
                    cur = m_conn.execute(
                        """SELECT cv.client_id FROM client_values cv
                           JOIN clients c ON c.id = cv.client_id
                           JOIN mcl_columns mc ON mc.id = cv.column_id
                           WHERE c.is_archived = 0 AND mc.is_internal_pk = 1 AND UPPER(TRIM(cv.value)) = ?
                           LIMIT 1""",
                        (cand.upper(),)
                    )
                    row = cur.fetchone()
                    if row:
                        valid_id = row[0]
                        break

                # Fallback: Match candidate against any column in master.db
                if not valid_id:
                    for cand in candidates:
                        cur = m_conn.execute(
                            """SELECT cv.client_id FROM client_values cv
                               JOIN clients c ON c.id = cv.client_id
                               WHERE c.is_archived = 0 AND UPPER(TRIM(cv.value)) = ?
                               LIMIT 1""",
                            (cand.upper(),)
                        )
                        row = cur.fetchone()
                        if row:
                            valid_id = row[0]
                            break

                if not valid_id:
                    unassigned_identity = candidates[0]
            else:
                # If no PAN/GSTIN exists and no candidate matches, do NOT blindly bind to a mismatched client_id!
                if client_id:
                    cur = m_conn.execute("SELECT id FROM clients WHERE id = ? AND is_archived = 0", (client_id,))
                    row = cur.fetchone()
                    if row:
                        # Validate that client has services or matching portal context before blind attribution
                        valid_id = row[0]
                if not valid_id:
                    unassigned_identity = f"Pending_{arn_number}" if arn_number and arn_number != "N/A" else "Unassigned"

        # Check for Sera DOM / SDC page-revisit replacement constraint
        page_url_norm = None
        if raw_payload_json and (capture_method == "DOM_Tracker" or capture_method.startswith("SDC_")):
            try:
                p_obj = json.loads(raw_payload_json) if isinstance(raw_payload_json, str) else raw_payload_json
                if isinstance(p_obj, dict):
                    raw_p = p_obj.get("raw_payload") if isinstance(p_obj.get("raw_payload"), dict) else {}
                    page_url = p_obj.get("page_key") or p_obj.get("url") or raw_p.get("page_key") or raw_p.get("url")
                    if page_url and isinstance(page_url, str):
                        page_url_norm = page_url.strip().split("?")[0].rstrip("/").lower()
            except Exception:
                page_url_norm = None

        # 2. Write capture to rawPayload.db and update SRPF container
        is_replaced = False
        with self._connect_raw() as r_conn:
            # Deduplication Check (for immediate identical bursts)
            if arn_number and arn_number != "N/A":
                cur = r_conn.execute(
                    "SELECT id, client_id FROM tracker_dump WHERE arn_number = ? AND created_at >= ?",
                    (arn_number, (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=10)).isoformat())
                )
                row = cur.fetchone()
                if row:
                    return {
                        "id": row[0], "client_id": row[1], "service_id": service_id,
                        "portal": portal, "period_label": period_label, "arn_number": arn_number,
                        "capture_method": capture_method, "status": status, "created_at": now, "duplicate": True
                    }

            # Sera DOM / SDC Constraint Rule:
            # If data is captured from a page link and users navigate away then return to the same page link,
            # the new data captured replaces the old one for data management.
            existing_dump_id = None
            candidate_rows = []
            if page_url_norm and (capture_method == "DOM_Tracker" or capture_method.startswith("SDC_")):
                # Check for either DOM_Tracker or SDC captures for this identity
                query = "SELECT id, raw_payload_json FROM tracker_dump WHERE (capture_method = 'DOM_Tracker' OR capture_method LIKE 'SDC_%') "
                q_params = []
                if valid_id:
                    query += "AND client_id = ? "
                    q_params.append(valid_id)
                elif unassigned_identity:
                    query += "AND unassigned_identity = ? "
                    q_params.append(unassigned_identity)
                query += "ORDER BY id DESC"
                cur = r_conn.execute(query, q_params)
                candidate_rows = cur.fetchall()
                for r_id, r_json in candidate_rows:
                    if r_json:
                        try:
                            cj = json.loads(r_json)
                            c_raw_p = cj.get("raw_payload") if isinstance(cj.get("raw_payload"), dict) else {}
                            c_url = cj.get("page_key") or cj.get("url") or c_raw_p.get("page_key") or c_raw_p.get("url")
                            if c_url and c_url.strip().split("?")[0].rstrip("/").lower() == page_url_norm:
                                existing_dump_id = r_id
                                break
                        except Exception:
                            continue

            if existing_dump_id:
                # Update existing record in place with newly captured data
                r_conn.execute(
                    """UPDATE tracker_dump
                       SET client_id = ?, unassigned_identity = ?, service_id = ?, portal = ?,
                           period_label = ?, arn_number = ?, capture_method = ?, status = ?,
                           raw_payload_json = ?, captured_by = ?, created_at = ?
                       WHERE id = ?""",
                    (valid_id, unassigned_identity, service_id, portal, period_label, arn_number, capture_method, status, raw_payload_json, captured_by, now, existing_dump_id)
                )
                dump_id = existing_dump_id
                is_replaced = True
                # Clean up any older duplicate rows matching this exact page URL
                for r_id, r_json in candidate_rows:
                    if r_id != existing_dump_id and r_json:
                        try:
                            cj = json.loads(r_json)
                            c_raw_p = cj.get("raw_payload") if isinstance(cj.get("raw_payload"), dict) else {}
                            c_url = cj.get("page_key") or cj.get("url") or c_raw_p.get("page_key") or c_raw_p.get("url")
                            if c_url and c_url.strip().split("?")[0].rstrip("/").lower() == page_url_norm:
                                r_conn.execute("DELETE FROM tracker_dump WHERE id = ?", (r_id,))
                        except Exception:
                            pass
            else:
                cur = r_conn.execute(
                    """INSERT INTO tracker_dump
                       (client_id, unassigned_identity, service_id, portal, period_label, arn_number, capture_method, status, raw_payload_json, captured_by, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (valid_id, unassigned_identity, service_id, portal, period_label, arn_number, capture_method, status, raw_payload_json, captured_by, now)
                )
                dump_id = cur.lastrowid

            # Update SRPF Unified Container (Key normalized by client_id if registered)
            if valid_id:
                container_key = f"CLI-{valid_id:05d}"
            elif candidates:
                container_key = candidates[0]
            else:
                container_key = unassigned_identity or f"UNASSIGNED_{dump_id}"

            self._update_srpf_container(
                r_conn,
                identity_key=container_key,
                client_id=valid_id,
                dump_row={
                    "portal": portal,
                    "period_label": period_label,
                    "arn_number": arn_number,
                    "capture_method": capture_method,
                    "raw_payload_json": raw_payload_json,
                    "created_at": now
                }
            )

        if is_replaced:
            # Synchronize text dump files after in-place replacement
            self.rebuild_raw_payload_dumps_file()
        else:
            # Write/append organized raw payload entry to seraRawPayloadDump.txt
            self._append_raw_payload_dump_file(
                dump_id=dump_id,
                client_id=valid_id,
                portal=portal,
                period_label=period_label,
                arn_number=arn_number,
                capture_method=capture_method,
                status=status,
                raw_payload_json=raw_payload_json,
                captured_by=captured_by,
                created_at=now
            )

        # Auto-upsert SDC session timeline if present in payload
        if raw_payload_json:
            try:
                p_dict = json.loads(raw_payload_json) if isinstance(raw_payload_json, str) else raw_payload_json
                tl = p_dict.get("session_timeline") or (p_dict.get("raw_payload", {}).get("session_timeline") if isinstance(p_dict.get("raw_payload"), dict) else None)
                sess_id = p_dict.get("session_id") or (p_dict.get("raw_payload", {}).get("session_id") if isinstance(p_dict.get("raw_payload"), dict) else None)
                if sess_id and tl:
                    self.upsert_sdc_session_timeline({
                        "session_id": sess_id,
                        "client_id": valid_id,
                        "pan": pan or candidates[0] if candidates else "",
                        "client_name": p_dict.get("client_name") or p_dict.get("name") or "",
                        "portal": portal or "Income Tax",
                        "status": p_dict.get("status") or "active",
                        "start_time": p_dict.get("timestamp") or now,
                        "timeline": tl
                    })
            except Exception as e:
                print(f"[database] auto-upsert SDC timeline notice: {e}")

        # Keep derived FST and SDC workbooks current after every new capture.
        self.sync_fst_reports()
        self.sync_dom_parser()
        self._bump_sync_revision_if_configured()

        return {
            "id": dump_id, "client_id": valid_id, "unassigned_identity": unassigned_identity, "service_id": service_id,
            "portal": portal, "period_label": period_label, "arn_number": arn_number,
            "capture_method": capture_method, "status": status, "created_at": now, "replaced": is_replaced
        }

    def upsert_sdc_session_timeline(self, session_data: dict) -> dict:
        """Upserts a full SDC Session Timeline audit trail in rawPayload.db."""
        if not session_data or not isinstance(session_data, dict):
            return None
        session_id = str(session_data.get("session_id") or "").strip()
        if not session_id:
            return None

        pan = str(session_data.get("pan") or "").strip().upper()
        client_name = str(session_data.get("client_name") or session_data.get("name") or "").strip()
        portal = str(session_data.get("portal") or "Income Tax").strip()
        status = str(session_data.get("status") or "active").strip()
        start_time = str(session_data.get("start_time") or datetime.datetime.now(datetime.timezone.utc).isoformat())
        end_time = session_data.get("end_time")
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        timeline = session_data.get("timeline") or []
        timeline_json = json.dumps(timeline, ensure_ascii=False)
        total_steps = len(timeline)

        # Resolve client_id if PAN exists
        client_id = session_data.get("client_id")
        if not client_id and pan:
            with self._connect() as m_conn:
                cur = m_conn.execute(
                    """SELECT cv.client_id FROM client_values cv
                       JOIN clients c ON c.id = cv.client_id
                       WHERE c.is_archived = 0 AND UPPER(TRIM(cv.value)) = ?
                       LIMIT 1""",
                    (pan,)
                )
                row = cur.fetchone()
                if row:
                    client_id = row[0]

        with self._connect_raw() as r_conn:
            r_conn.execute(
                """INSERT INTO sdc_session_timelines
                   (session_id, client_id, pan, client_name, portal, status, start_time, end_time, total_steps, timeline_json, last_updated)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(session_id) DO UPDATE SET
                       client_id = COALESCE(excluded.client_id, sdc_session_timelines.client_id),
                       pan = CASE WHEN excluded.pan != '' THEN excluded.pan ELSE sdc_session_timelines.pan END,
                       client_name = CASE WHEN excluded.client_name != '' THEN excluded.client_name ELSE sdc_session_timelines.client_name END,
                       portal = excluded.portal,
                       status = excluded.status,
                       end_time = excluded.end_time,
                       total_steps = excluded.total_steps,
                       timeline_json = excluded.timeline_json,
                       last_updated = excluded.last_updated""",
                (session_id, client_id, pan, client_name, portal, status, start_time, end_time, total_steps, timeline_json, now)
            )

        self.sync_dom_parser()
        self._bump_sync_revision_if_configured()

        return {
            "session_id": session_id,
            "client_id": client_id,
            "pan": pan,
            "client_name": client_name,
            "status": status,
            "total_steps": total_steps
        }

    def get_sdc_session_timelines(self, client_id: int = None, pan: str = None, limit: int = 50) -> list:
        """Retrieves chronological SDC session timelines from rawPayload.db."""
        with self._connect_raw() as r_conn:
            query = "SELECT session_id, client_id, pan, client_name, portal, status, start_time, end_time, total_steps, timeline_json, last_updated FROM sdc_session_timelines "
            params = []
            conditions = []
            if client_id:
                conditions.append("client_id = ?")
                params.append(client_id)
            if pan:
                conditions.append("UPPER(pan) = ?")
                params.append(pan.strip().upper())
            if conditions:
                query += "WHERE " + " AND ".join(conditions) + " "
            query += "ORDER BY last_updated DESC LIMIT ?"
            params.append(limit)

            cur = r_conn.execute(query, params)
            rows = []
            for r in cur.fetchall():
                try:
                    tl_parsed = json.loads(r[9]) if r[9] else []
                except Exception:
                    tl_parsed = []
                rows.append({
                    "session_id": r[0],
                    "client_id": r[1],
                    "pan": r[2],
                    "client_name": r[3],
                    "portal": r[4],
                    "status": r[5],
                    "start_time": r[6],
                    "end_time": r[7],
                    "total_steps": r[8],
                    "timeline": tl_parsed,
                    "last_updated": r[10]
                })
            return rows

    def get_sdc_timeline_by_session_id(self, session_id: str) -> dict:
        """Retrieves a specific SDC session timeline by session_id."""
        if not session_id:
            return None
        with self._connect_raw() as r_conn:
            cur = r_conn.execute(
                "SELECT session_id, client_id, pan, client_name, portal, status, start_time, end_time, total_steps, timeline_json, last_updated FROM sdc_session_timelines WHERE session_id = ?",
                (session_id,)
            )
            r = cur.fetchone()
            if not r:
                return None
            try:
                tl_parsed = json.loads(r[9]) if r[9] else []
            except Exception:
                tl_parsed = []
            return {
                "session_id": r[0],
                "client_id": r[1],
                "pan": r[2],
                "client_name": r[3],
                "portal": r[4],
                "status": r[5],
                "start_time": r[6],
                "end_time": r[7],
                "total_steps": r[8],
                "timeline": tl_parsed,
                "last_updated": r[10]
            }

    def link_unassigned_tracker_dumps(self, client_id: int, identity_value: str) -> int:
        """Retroactively links all unassigned tracker_dump rows matching identity_value (PAN/TAN/GSTIN) to client_id in rawPayload.db."""
        clean = str(identity_value or "").strip().upper()
        if not clean or not client_id:
            return 0
        with self._connect_raw() as conn:
            cur = conn.execute(
                """UPDATE tracker_dump
                   SET client_id = ?, unassigned_identity = NULL
                   WHERE (client_id IS NULL OR client_id = 0)
                     AND (
                       UPPER(TRIM(unassigned_identity)) = ?
                       OR UPPER(TRIM(arn_number)) LIKE ?
                       OR UPPER(raw_payload_json) LIKE ?
                     )""",
                (client_id, clean, f"%{clean}%", f"%{clean}%")
            )
            count = cur.rowcount
            # Also update client_raw_containers
            conn.execute("UPDATE client_raw_containers SET client_id = ? WHERE UPPER(identity_key) = ?", (client_id, clean))
            return count

    def re_resolve_all_tracker_dumps(self) -> int:
        """Scans all rows in tracker_dump (rawPayload.db), matches true PAN/GSTIN/TAN against master.db, and rebuilds unified containers."""
        updated_count = 0
        with self._connect_raw() as r_conn:
            cur = r_conn.execute("SELECT id, client_id, unassigned_identity, portal, period_label, arn_number, capture_method, status, raw_payload_json, captured_by, created_at FROM tracker_dump ORDER BY created_at")
            dumps = [dict(zip([col[0] for col in cur.description], row)) for row in cur.fetchall()]

        if not dumps:
            return 0

        from datetime import datetime, timezone

        # 1. Proximity matching for wizard submissions with empty candidates
        resolved_dumps = []
        for i, d in enumerate(dumps):
            cands = self._extract_identity_candidates_from_payload(arn_number=d["arn_number"], raw_payload_json=d["raw_payload_json"])
            if not cands and d.get("created_at"):
                try:
                    t0 = datetime.fromisoformat(d["created_at"])
                    for nearby in (reversed(dumps[:i]) if i > 0 else []):
                        if nearby.get("portal") == d.get("portal") and nearby.get("created_at"):
                            tn = datetime.fromisoformat(nearby["created_at"])
                            if abs((t0 - tn).total_seconds()) <= 900:
                                nb_cands = self._extract_identity_candidates_from_payload(arn_number=nearby["arn_number"], raw_payload_json=nearby["raw_payload_json"])
                                if nb_cands:
                                    cands = nb_cands
                                    break
                except Exception:
                    pass
            d["effective_candidates"] = cands
            resolved_dumps.append(d)

        # 2. Build Identity -> client_id lookup from master.db
        with self._connect() as m_conn:
            cur_cls = m_conn.execute("SELECT id, client_id_token FROM clients WHERE is_archived = 0")
            all_clients = {r[0]: r[1] for r in cur_cls.fetchall()}
            identity_to_cid = {}
            for cid in all_clients:
                cdata = self._fetch_client_full(m_conn, cid)
                if cdata:
                    for col_id, val in cdata.get("values", {}).items():
                        if val and isinstance(val, str):
                            clean_v = val.strip().upper()
                            if len(clean_v) in (10, 15):
                                identity_to_cid[clean_v] = cid
                                if len(clean_v) == 15:
                                    identity_to_cid[clean_v[2:12]] = cid

        # 3. Update tracker_dump and rebuild client_raw_containers cleanly
        with self._connect_raw() as r_conn:
            r_conn.execute("DELETE FROM client_raw_containers")
            for d in resolved_dumps:
                cands = d["effective_candidates"]
                matched_id = None
                for cand in cands:
                    if cand in identity_to_cid:
                        matched_id = identity_to_cid[cand]
                        break

                new_cid = matched_id
                new_unassigned = None if matched_id else (cands[0] if cands else d.get("unassigned_identity"))

                if d["client_id"] != new_cid or d["unassigned_identity"] != new_unassigned:
                    r_conn.execute(
                        "UPDATE tracker_dump SET client_id = ?, unassigned_identity = ? WHERE id = ?",
                        (new_cid, new_unassigned, d["id"])
                    )
                    updated_count += 1

                # Container key: single key per registered client or candidate
                if new_cid:
                    c_key = f"CLI-{new_cid:05d}"
                elif cands:
                    c_key = cands[0]
                else:
                    c_key = new_unassigned or f"UNASSIGNED_{d['id']}"

                self._update_srpf_container(
                    r_conn,
                    identity_key=c_key,
                    client_id=new_cid,
                    dump_row={
                        "portal": d["portal"],
                        "period_label": d["period_label"],
                        "arn_number": d["arn_number"],
                        "capture_method": d["capture_method"],
                        "raw_payload_json": d["raw_payload_json"],
                        "created_at": d["created_at"]
                    }
                )

        self.sync_fst_reports()
        return updated_count

    @staticmethod
    def _extract_dump_date_key(ts_str: Optional[str]) -> str:
        """Returns DD_MM_YY formatted local date string (e.g. 26_08_26) for the given ISO timestamp."""
        if not ts_str:
            return datetime.datetime.now().strftime("%d_%m_%y")
        try:
            clean = str(ts_str).strip().replace("Z", "+00:00")
            dt = datetime.datetime.fromisoformat(clean)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            local_dt = dt.astimezone()
            return local_dt.strftime("%d_%m_%y")
        except Exception:
            return datetime.datetime.now().strftime("%d_%m_%y")

    def _get_daily_dump_file_paths(self, date_key: str) -> list[str]:
        """Returns destination file paths for Raw_Payload_Dump/seraRawPayloadDump_dd_mm_yy.txt in safe app data dir."""
        filename = f"seraRawPayloadDump_{date_key}.txt"
        paths = []
        try:
            db_dir = os.path.dirname(os.path.abspath(self.db_path))
            paths.append(os.path.join(db_dir, "Raw_Payload_Dump", filename))
        except Exception:
            pass

        unique_paths = []
        for p in paths:
            norm = os.path.normpath(p)
            if norm not in unique_paths:
                unique_paths.append(norm)
        return unique_paths

    def _get_backup_dump_file_paths(self) -> list[str]:
        """Returns destination file paths for Raw_Payload_Dump/seraRawPayloadDumpBackup.txt in safe app data dir."""
        filename = "seraRawPayloadDumpBackup.txt"
        paths = []
        try:
            db_dir = os.path.dirname(os.path.abspath(self.db_path))
            paths.append(os.path.join(db_dir, "Raw_Payload_Dump", filename))
        except Exception:
            pass

        unique_paths = []
        for p in paths:
            norm = os.path.normpath(p)
            if norm not in unique_paths:
                unique_paths.append(norm)
        return unique_paths

    def _get_dump_folder_paths(self) -> list[str]:
        """Returns destination folder paths for Raw_Payload_Dump directory in safe app data dir."""
        paths = []
        try:
            db_dir = os.path.dirname(os.path.abspath(self.db_path))
            paths.append(os.path.join(db_dir, "Raw_Payload_Dump"))
        except Exception:
            pass

        unique_paths = []
        for p in paths:
            norm = os.path.normpath(p)
            if norm not in unique_paths:
                unique_paths.append(norm)
        return unique_paths

    def _get_dump_file_paths(self) -> list[str]:
        """Returns destination file paths for canonical seraRawPayloadDump.txt in safe app data dir."""
        paths = []
        try:
            db_dir = os.path.dirname(os.path.abspath(self.db_path))
            paths.append(os.path.join(db_dir, "Raw_Payload_Dump", "seraRawPayloadDump.txt"))
            paths.append(os.path.join(db_dir, "seraRawPayloadDump.txt"))
        except Exception:
            pass

        unique_paths = []
        for p in paths:
            norm = os.path.normpath(p)
            if norm not in unique_paths:
                unique_paths.append(norm)
        return unique_paths

    @staticmethod
    def _format_dump_entry_block(dump_id, client_id, portal, period_label, arn_number, capture_method, status, raw_payload_json, captured_by, created_at, client_name="") -> str:
        """Formats a standardized, high-contrast dump text block for an intercepted payload."""
        formatted_json = raw_payload_json or "{}"
        parsed = {}
        try:
            if isinstance(raw_payload_json, str):
                parsed = json.loads(raw_payload_json)
            else:
                parsed = raw_payload_json or {}
            formatted_json = json.dumps(parsed, indent=4, ensure_ascii=False)
        except Exception:
            formatted_json = str(raw_payload_json)
            parsed = {}

        if not client_name and isinstance(parsed, dict):
            client_name = (
                parsed.get("client_name") 
                or parsed.get("taxpayer_name") 
                or parsed.get("name") 
                or (parsed.get("raw_payload", {}).get("client_name") if isinstance(parsed.get("raw_payload"), dict) else "")
                or (parsed.get("raw_payload", {}).get("taxpayer_name") if isinstance(parsed.get("raw_payload"), dict) else "")
            )

        client_str = f"{client_id} ({client_name})" if client_name else (str(client_id) if client_id else "N/A")
        entry_lines = [
            "=" * 88,
            f"CAPTURE DUMP ENTRY #{dump_id or 'N/A'}",
            "=" * 88,
            f"Timestamp       : {created_at}",
            f"Portal          : {portal or 'N/A'}",
            f"Capture Method  : {capture_method or 'N/A'}",
            f"Status          : {status or 'submitted'}",
            f"ARN / Ack No    : {arn_number or 'N/A'}",
            f"Period Label    : {period_label or 'N/A'}",
            f"Client ID       : {client_str}",
            f"Captured By     : {captured_by or 'System'}",
            "-" * 88,
            "RAW JSON PAYLOAD:",
            formatted_json,
            "=" * 88,
            "\n"
        ]
        return "\n".join(entry_lines)

    def _append_raw_payload_dump_file(self, dump_id, client_id, portal, period_label, arn_number, capture_method, status, raw_payload_json, captured_by, created_at, client_name=""):
        """Appends raw payload entry to today's daily dump, the append-only master backup, and the canonical dump file."""
        try:
            entry_text = self._format_dump_entry_block(
                dump_id=dump_id, client_id=client_id, portal=portal, period_label=period_label,
                arn_number=arn_number, capture_method=capture_method, status=status,
                raw_payload_json=raw_payload_json, captured_by=captured_by, created_at=created_at,
                client_name=client_name
            )
            date_key = self._extract_dump_date_key(created_at)

            # 1. Append to today's daily dump file: seraRawPayload_date(DD-MM-YY).txt
            for daily_file in self._get_daily_dump_file_paths(date_key):
                try:
                    os.makedirs(os.path.dirname(daily_file), exist_ok=True)
                    if not os.path.exists(daily_file) or os.path.getsize(daily_file) == 0:
                        header = [
                            "#" * 88,
                            f"# PROJECT SERA — DAILY RAW API PAYLOAD DUMP [{date_key}]",
                            f"# Intercepted government portal API submissions for 24-hour cycle: {date_key}",
                            "#" * 88,
                            f"# Initialized at : {datetime.datetime.now(datetime.timezone.utc).isoformat()}",
                            f"# Target Database: {os.path.abspath(self.raw_db_path)}",
                            "#" * 88,
                            "\n"
                        ]
                        with open(daily_file, "w", encoding="utf-8") as f:
                            f.write("\n".join(header))
                    with open(daily_file, "a", encoding="utf-8") as f:
                        f.write(entry_text)
                except Exception as file_err:
                    print(f"[DailyDump] File append error ({daily_file}): {file_err}")

            # 2. Append to Master Backup Dump: seraRawPayloadDumpBackup.txt (Append-only guarantee)
            for backup_file in self._get_backup_dump_file_paths():
                try:
                    os.makedirs(os.path.dirname(backup_file), exist_ok=True)
                    if not os.path.exists(backup_file) or os.path.getsize(backup_file) == 0:
                        header = [
                            "#" * 88,
                            "# PROJECT SERA — MASTER RAW PAYLOAD BACKUP ARCHIVE (APPEND-ONLY)",
                            "# Permanent, immutable historical repository of all portal captures (Never deleted)",
                            "#" * 88,
                            f"# Initialized at : {datetime.datetime.now(datetime.timezone.utc).isoformat()}",
                            f"# Target Database: {os.path.abspath(self.raw_db_path)}",
                            "#" * 88,
                            "\n"
                        ]
                        with open(backup_file, "w", encoding="utf-8") as f:
                            f.write("\n".join(header))

                    entry_header_tag = f"CAPTURE DUMP ENTRY #{dump_id or 'N/A'}"
                    already_present = False
                    if os.path.exists(backup_file) and dump_id:
                        try:
                            with open(backup_file, "r", encoding="utf-8", errors="ignore") as f:
                                if entry_header_tag in f.read():
                                    already_present = True
                        except Exception:
                            pass

                    if not already_present:
                        with open(backup_file, "a", encoding="utf-8") as f:
                            f.write(entry_text)
                except Exception as file_err:
                    print(f"[BackupDump] File append error ({backup_file}): {file_err}")

            # 3. Append to canonical full dump: seraRawPayloadDump.txt
            for dump_file in self._get_dump_file_paths():
                try:
                    os.makedirs(os.path.dirname(dump_file), exist_ok=True)
                    if not os.path.exists(dump_file) or os.path.getsize(dump_file) == 0:
                        header = [
                            "#" * 88,
                            "# PROJECT SERA — RAW API PAYLOAD CAPTURE DUMP",
                            "# Intercepted government portal API requests, responses, and FST submissions",
                            "#" * 88,
                            f"# Initialized at : {datetime.datetime.now(datetime.timezone.utc).isoformat()}",
                            f"# Target Database: {os.path.abspath(self.raw_db_path)}",
                            "#" * 88,
                            "\n"
                        ]
                        with open(dump_file, "w", encoding="utf-8") as f:
                            f.write("\n".join(header))

                    with open(dump_file, "a", encoding="utf-8") as f:
                        f.write(entry_text)
                except Exception as file_err:
                    print(f"[seraRawPayloadDump] File append error ({dump_file}): {file_err}")
        except Exception as e:
            print(f"[seraRawPayloadDump] Append error: {e}")

    def sync_raw_payload_dumps_file(self):
        """Ensures all existing tracker_dump records are synced across daily dumps, the canonical dump, and backup."""
        try:
            for dump_file in self._get_dump_file_paths():
                if os.path.exists(dump_file) and os.path.getsize(dump_file) > 100:
                    continue
                self.rebuild_raw_payload_dumps_file()
                break
        except Exception as e:
            print(f"[seraRawPayloadDump] Sync error: {e}")

    def rebuild_raw_payload_dumps_file(self) -> int:
        """Completely rebuilds all daily partitioned dumps, the canonical dump, and synchronizes the append-only backup."""
        dumps = self.get_tracker_dumps(limit=5000)
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # Group dumps by daily date key (DD-MM-YY)
        daily_groups: dict[str, list[dict]] = {}
        for item in reversed(dumps):
            d_key = self._extract_dump_date_key(item.get("created_at"))
            daily_groups.setdefault(d_key, []).append(item)

        # 1. Rebuild each daily partitioned dump file: seraRawPayload_date(DD-MM-YY).txt
        for d_key, group_items in daily_groups.items():
            header = [
                "#" * 88,
                f"# PROJECT SERA — DAILY RAW API PAYLOAD DUMP [{d_key}]",
                f"# Intercepted government portal API submissions for 24-hour cycle: {d_key}",
                "#" * 88,
                f"# Rebuilt at      : {now_str}",
                f"# Total Entries   : {len(group_items)}",
                f"# Target Database : {os.path.abspath(self.raw_db_path)}",
                "#" * 88,
                "\n"
            ]
            entries = []
            for item in group_items:
                entries.append(self._format_dump_entry_block(
                    dump_id=item.get("id"), client_id=item.get("client_id"), portal=item.get("portal"),
                    period_label=item.get("period_label"), arn_number=item.get("arn_number"),
                    capture_method=item.get("capture_method"), status=item.get("status"),
                    raw_payload_json=item.get("raw_payload_json"), captured_by=item.get("captured_by"),
                    created_at=item.get("created_at"), client_name=item.get("client_name", "")
                ))
            daily_content = "\n".join(header) + "\n".join(entries)
            for daily_path in self._get_daily_dump_file_paths(d_key):
                try:
                    os.makedirs(os.path.dirname(daily_path), exist_ok=True)
                    with open(daily_path, "w", encoding="utf-8") as f:
                        f.write(daily_content)
                except Exception as e:
                    print(f"[DailyDump] Rebuild write error ({daily_path}): {e}")

        # 2. Rebuild canonical dump: seraRawPayloadDump.txt
        full_header = [
            "#" * 88,
            "# PROJECT SERA — RAW API PAYLOAD CAPTURE DUMP",
            "# Intercepted government portal API requests, responses, and FST submissions",
            "#" * 88,
            f"# Rebuilt at      : {now_str}",
            f"# Total Entries   : {len(dumps)}",
            f"# Target Database : {os.path.abspath(self.raw_db_path)}",
            "#" * 88,
            "\n"
        ]
        full_entries = []
        for item in reversed(dumps):
            full_entries.append(self._format_dump_entry_block(
                dump_id=item.get("id"), client_id=item.get("client_id"), portal=item.get("portal"),
                period_label=item.get("period_label"), arn_number=item.get("arn_number"),
                capture_method=item.get("capture_method"), status=item.get("status"),
                raw_payload_json=item.get("raw_payload_json"), captured_by=item.get("captured_by"),
                created_at=item.get("created_at"), client_name=item.get("client_name", "")
            ))
        full_content = "\n".join(full_header) + "\n".join(full_entries)
        for dump_file in self._get_dump_file_paths():
            try:
                os.makedirs(os.path.dirname(dump_file), exist_ok=True)
                with open(dump_file, "w", encoding="utf-8") as f:
                    f.write(full_content)
            except Exception as e:
                print(f"[seraRawPayloadDump] Rebuild write error ({dump_file}): {e}")

        # 3. Synchronize Master Backup Dump: seraRawPayloadDumpBackup.txt (Append-only guarantee)
        for backup_path in self._get_backup_dump_file_paths():
            try:
                os.makedirs(os.path.dirname(backup_path), exist_ok=True)
                existing_backup_text = ""
                if os.path.exists(backup_path):
                    try:
                        with open(backup_path, "r", encoding="utf-8", errors="ignore") as f:
                            existing_backup_text = f.read()
                    except Exception:
                        existing_backup_text = ""

                if not existing_backup_text or len(existing_backup_text.strip()) == 0:
                    b_header = [
                        "#" * 88,
                        "# PROJECT SERA — MASTER RAW PAYLOAD BACKUP ARCHIVE (APPEND-ONLY)",
                        "# Permanent, immutable historical repository of all portal captures (Never deleted)",
                        "#" * 88,
                        f"# Initialized at : {now_str}",
                        f"# Target Database: {os.path.abspath(self.raw_db_path)}",
                        "#" * 88,
                        "\n"
                    ]
                    existing_backup_text = "\n".join(b_header)
                    with open(backup_path, "w", encoding="utf-8") as f:
                        f.write(existing_backup_text)

                to_append = []
                for item in reversed(dumps):
                    tag = f"CAPTURE DUMP ENTRY #{item.get('id', 'N/A')}"
                    if tag not in existing_backup_text:
                        to_append.append(self._format_dump_entry_block(
                            dump_id=item.get("id"), client_id=item.get("client_id"), portal=item.get("portal"),
                            period_label=item.get("period_label"), arn_number=item.get("arn_number"),
                            capture_method=item.get("capture_method"), status=item.get("status"),
                            raw_payload_json=item.get("raw_payload_json"), captured_by=item.get("captured_by"),
                            created_at=item.get("created_at"), client_name=item.get("client_name", "")
                        ))

                if to_append:
                    with open(backup_path, "a", encoding="utf-8") as f:
                        f.write("\n" + "\n".join(to_append))
            except Exception as e:
                print(f"[BackupDump] Sync error ({backup_path}): {e}")

        # Trigger both derived FST report pipelines after rebuilding the dump.
        self.sync_fst_reports()
        return len(dumps)

    def sync_fst_reports(self):
        """Refresh all FST-derived reports from the active raw dump.

        This method is intentionally best-effort: capture/storage must remain
        reliable even when Excel/reporting dependencies are not installed.
        """
        workspace_dir = os.path.dirname(os.path.abspath(__file__))
        master_pans = self._get_master_pans_for_reports()
        self.sync_fst_classifier()
        self.sync_dom_parser()

        dump_file = os.path.join(workspace_dir, "seraRawPayloadDump.txt")
        if not os.path.exists(dump_file) or os.path.getsize(dump_file) <= 100:
            return

        try:
            tracer_dir = os.path.join(workspace_dir, "FST_Tracer_Alpha")
            report_path = os.path.join(tracer_dir, "fst_tracer_alpha_report.xlsx")
            vault_path = os.path.join(workspace_dir, "docs", "APP", "Sera FST Tracer Alpha")
            if os.path.isdir(tracer_dir):
                from FST_Tracer_Alpha.tracer import process_dump
                result = process_dump(dump_file, report_path, vault_path, master_pans=master_pans)
                actual_report = result.get("outputs", {}).get("excel_path", report_path)
                if actual_report != report_path:
                    print(f"[FST Tracer Alpha] Canonical workbook is locked; refreshed fallback: {actual_report}")
        except Exception as e:
            print(f"[FST Tracer Alpha] Report sync skipped: {e}")

        try:
            simple_dir = os.path.join(workspace_dir, "simpleParser")
            report_path = os.path.join(simple_dir, "simple_parser_report.xlsx")
            if os.path.isdir(simple_dir):
                from simpleParser.simple_parser import process_dump
                result = process_dump(dump_file, report_path, master_pans=master_pans)
                actual_report = result.get("outputs", {}).get("excel_path", report_path)
                if actual_report != report_path:
                    print(f"[Simple Parser] Canonical workbook is locked; refreshed fallback: {actual_report}")
        except Exception as e:
            print(f"[Simple Parser] Report sync skipped: {e}")

    def _get_master_pans_for_reports(self) -> set[str]:
        """Return active Master DB PANs for report-side identity validation."""
        pan_re = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$", re.I)
        pan_column_ids = {
            c["id"] for c in self.get_mcl_columns()
            if "pan" in str(c.get("label", "")).lower()
        }
        master_pans = set()
        for client in self.search_clients("", include_archived=False):
            for column_id in pan_column_ids:
                value = str(client.get("values", {}).get(column_id, "")).strip().upper()
                if pan_re.fullmatch(value):
                    master_pans.add(value)
        return master_pans

    def sync_fst_classifier(self):
        """Silently syncs FST_Classifier_1/payload_report.xlsx whenever dumps are updated."""
        try:
            workspace_dir = os.path.dirname(os.path.abspath(__file__))
            classifier_dir = os.path.join(workspace_dir, "FST_Classifier_1")
            report_path = os.path.join(classifier_dir, "payload_report.xlsx")
            dump_file = os.path.join(workspace_dir, "seraRawPayloadDump.txt")
            if os.path.exists(dump_file) and os.path.exists(classifier_dir):
                import sys
                if classifier_dir not in sys.path:
                    sys.path.insert(0, classifier_dir)
                import fst_classifier
                fst_classifier.process_data(dump_file, report_path)
        except Exception:
            pass

    def sync_dom_parser(self):
        """Silently syncs DOM_Parser_1/dom_audit_report.xlsx whenever dumps or databases are updated."""
        try:
            workspace_dir = os.path.dirname(os.path.abspath(__file__))
            parser_dir = os.path.join(workspace_dir, "DOM_Parser_1")
            report_path = os.path.join(parser_dir, "dom_audit_report.xlsx")
            db_file = os.path.join(workspace_dir, "rawPayload.db")
            if os.path.exists(parser_dir):
                import sys
                if parser_dir not in sys.path:
                    sys.path.insert(0, parser_dir)
                import dom_parser
                dom_parser.process_data(db_file, report_path)
        except Exception:
            pass


    def get_tracker_dumps(self, client_id: int = None, limit: int = 200, search_query: str = None) -> list[dict]:
        """Reads tracker_dump entries from rawPayload.db and enriches them with client names from master.db."""
        with self._connect_raw() as r_conn:
            sql = """SELECT id, client_id, unassigned_identity, service_id, portal,
                            period_label, arn_number, capture_method, status,
                            raw_payload_json, captured_by, created_at
                     FROM tracker_dump
                     WHERE 1=1"""
            params = []
            if client_id:
                sql += " AND client_id = ?"
                params.append(client_id)
            if search_query:
                sql += " AND (arn_number LIKE ? OR portal LIKE ? OR period_label LIKE ? OR unassigned_identity LIKE ?)"
                q = f"%{search_query}%"
                params.extend([q, q, q, q])
            sql += " ORDER BY id DESC LIMIT ?"
            params.append(limit)

            cur = r_conn.execute(sql, params)
            rows = cur.fetchall()

        if not rows:
            return []

        # Enrich client names and tokens from master.db
        mcl_cols = self.get_mcl_columns()
        client_map = {}
        with self._connect() as m_conn:
            unique_cids = {r[1] for r in rows if r[1]}
            for cid in unique_cids:
                try:
                    cdata = self._fetch_client_full(m_conn, cid)
                    if cdata:
                        c_vals = cdata.get("values", {})
                        name_val = ""
                        pan_val = ""
                        for col in mcl_cols:
                            lbl = col.get("label", "").lower()
                            val = c_vals.get(col["id"], "")
                            if val and not name_val and any(k in lbl for k in ["name", "party", "client"]):
                                name_val = str(val).strip()
                            elif val and not pan_val and any(k in lbl for k in ["pan", "gstin", "gst"]):
                                pan_val = str(val).strip()
                        client_map[cid] = {
                            "name": name_val or cdata.get("client_id_token", str(cid)),
                            "pan": pan_val,
                            "is_unassigned": False
                        }
                except Exception:
                    pass
                if cid not in client_map:
                    client_map[cid] = {"name": f"CLI-{cid:05d}", "pan": "", "is_unassigned": False}

        results = []
        for r in rows:
            cid = r[1]
            unassigned_id = r[2]
            if cid and cid in client_map:
                info = client_map[cid]
            elif unassigned_id:
                info = {"name": f"Unregistered (PAN: {unassigned_id})", "pan": unassigned_id, "is_unassigned": True}
            else:
                info = {"name": "Unregistered Client", "pan": "", "is_unassigned": True}

            results.append({
                "id": r[0], "client_id": cid, "unassigned_identity": unassigned_id,
                "is_unassigned": info.get("is_unassigned", False),
                "client_name": info["name"], "pan": info["pan"],
                "service_id": r[3], "service_name": r[4] or "Portal", "portal": r[4] or "",
                "period_label": r[5] or "", "arn_number": r[6] or "N/A", "capture_method": r[7] or "DOM_Tracker",
                "status": r[8] or "submitted", "raw_payload_json": r[9] or "{}", "captured_by": r[10] or "System",
                "created_at": r[11]
            })
        return results

    def delete_tracker_dump(self, dump_id: int) -> bool:
        with self._connect_raw() as conn:
            cur = conn.execute("DELETE FROM tracker_dump WHERE id = ?", (dump_id,))
            res = cur.rowcount > 0
        if res:
            self.rebuild_raw_payload_dumps_file()
        return res

    def clear_tracker_dumps(self) -> int:
        with self._connect_raw() as conn:
            cur = conn.execute("DELETE FROM tracker_dump")
            count = cur.rowcount
        self.rebuild_raw_payload_dumps_file()
        return count


    # ---------------- Cell Formatting (Search Grid) ----------------
    def bulk_set_cell_formatting(self, formatting_list: list[dict]):
        if not formatting_list:
            return
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._connect() as conn:
            for item in formatting_list:
                cid = item["client_id"]
                ckey = str(item["column_key"])
                bg = item.get("bg_color")
                fg = item.get("fg_color")
                conn.execute(
                    """INSERT INTO cell_formatting (client_id, column_key, bg_color, fg_color, updated_at)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(client_id, column_key) DO UPDATE SET
                           bg_color = excluded.bg_color,
                           fg_color = excluded.fg_color,
                           updated_at = excluded.updated_at""",
                    (cid, ckey, bg or "", fg or "", now)
                )

    def clear_cell_formatting(self, client_column_pairs: list[tuple[int, str]]):
        if not client_column_pairs:
            return
        with self._connect() as conn:
            for cid, ckey in client_column_pairs:
                conn.execute(
                    "DELETE FROM cell_formatting WHERE client_id = ? AND column_key = ?",
                    (cid, str(ckey))
                )

    def get_cell_formatting_for_clients(self, client_ids: list[int]) -> dict:
        if not client_ids:
            return {}
        with self._connect() as conn:
            placeholders = ",".join("?" for _ in client_ids)
            sql = f"SELECT client_id, column_key, bg_color, fg_color FROM cell_formatting WHERE client_id IN ({placeholders})"
            cur = conn.execute(sql, client_ids)
            result = {}
            for r in cur.fetchall():
                result[(r[0], str(r[1]))] = {"bg_color": r[2], "fg_color": r[3]}
            return result


class PeerAuditLogManager:
    """
    Manages audit logs received from peer workstations over Sera Sync (SSAL).
    Stores per-workstation logs in isolated SQLite databases under ~/AmanAssociates_Sera/peer_logs/
    to prevent locks, transaction overhead, or schema conflicts with live master.db.
    """
    def __init__(self, base_dir: str):
        self.peer_logs_dir = os.path.join(base_dir, "peer_logs")
        os.makedirs(self.peer_logs_dir, exist_ok=True)

    def _get_peer_db_path(self, hostname: str) -> str:
        safe_host = "".join(c for c in hostname if c.isalnum() or c in ("-", "_")).lower()
        if not safe_host:
            safe_host = "unknown"
        return os.path.join(self.peer_logs_dir, f"peer_{safe_host}.db")

    def store_peer_logs(self, hostname: str, logs: list[dict]):
        if not hostname:
            return
        db_path = self._get_peer_db_path(hostname)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS peer_audit_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    orig_id     INTEGER,
                    ts          TEXT NOT NULL,
                    actor       TEXT NOT NULL,
                    action      TEXT NOT NULL,
                    client_id   INTEGER,
                    client_name TEXT,
                    service_id  INTEGER,
                    detail      TEXT,
                    UNIQUE(ts, actor, action, detail)
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS peer_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
            """)
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            conn.execute("INSERT OR REPLACE INTO peer_meta (key, value) VALUES ('hostname', ?)", (hostname,))
            conn.execute("INSERT OR REPLACE INTO peer_meta (key, value) VALUES ('last_received', ?)", (now_iso,))

            for l in logs:
                conn.execute("""
                    INSERT OR IGNORE INTO peer_audit_log (orig_id, ts, actor, action, client_id, client_name, service_id, detail)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    l.get("id"), l.get("ts"), l.get("actor", "Unknown"), l.get("action", "unknown"),
                    l.get("client_id"), l.get("client_name") or l.get("client_label"), l.get("service_id"), l.get("detail")
                ))
            conn.execute("INSERT OR REPLACE INTO peer_meta (key, value) VALUES ('log_count', (SELECT CAST(COUNT(*) AS TEXT) FROM peer_audit_log))")
            conn.commit()
        finally:
            conn.close()

    def get_peer_workstations(self) -> list[dict]:
        workstations = []
        if not os.path.exists(self.peer_logs_dir):
            return workstations

        for f in os.listdir(self.peer_logs_dir):
            if f.startswith("peer_") and f.endswith(".db"):
                db_path = os.path.join(self.peer_logs_dir, f)
                try:
                    conn = sqlite3.connect(db_path)
                    meta = {}
                    for row in conn.execute("SELECT key, value FROM peer_meta").fetchall():
                        meta[row[0]] = row[1]
                    count = conn.execute("SELECT COUNT(*) FROM peer_audit_log").fetchone()[0]
                    conn.close()

                    host = meta.get("hostname", f[5:-3])
                    workstations.append({
                        "hostname": host,
                        "last_received": meta.get("last_received", ""),
                        "count": count,
                        "db_path": db_path
                    })
                except Exception:
                    pass
        return sorted(workstations, key=lambda x: x["hostname"].lower())

    def get_peer_logs(self, hostname: str, actor: str = None, action: str = None, from_date: str = None, to_date: str = None, limit: int = 500) -> list[dict]:
        db_path = self._get_peer_db_path(hostname)
        if not os.path.exists(db_path):
            return []

        conn = sqlite3.connect(db_path)
        try:
            sql = "SELECT id, ts, actor, action, client_id, client_name, service_id, detail FROM peer_audit_log"
            where = []
            params = []
            if actor:
                where.append("actor LIKE ?")
                params.append(f"%{actor}%")
            if action and action != "All Actions":
                where.append("action = ?")
                params.append(action)
            if from_date:
                where.append("ts >= ?")
                params.append(from_date)
            if to_date:
                if to_date.endswith("T00:00:00"):
                    where.append("ts < ?")
                else:
                    where.append("ts <= ?")
                params.append(to_date)

            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY id DESC LIMIT ?"
            params.append(limit)

            cur = conn.execute(sql, params)
            return [
                {
                    "id": r[0], "ts": r[1], "actor": r[2], "action": r[3],
                    "client_id": r[4], "client_name": r[5] or (f"CLI-{r[4]:05d}" if r[4] else "—"),
                    "service_id": r[6], "detail": r[7]
                }
                for r in cur.fetchall()
            ]
        finally:
            conn.close()

