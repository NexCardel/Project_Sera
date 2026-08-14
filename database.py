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
import shutil
from contextlib import contextmanager

import security

DB_FILENAME = "master.db"

class DatabaseError(Exception):
    pass


class SeraDatabase:
    def __init__(self, db_path: str, hex_key: str):
        self.db_path = db_path
        self.hex_key = hex_key
        # Set externally by main.py once SyncPeerService exists, so this
        # module never has to import sync_peer.py directly (sync depends
        # on the db, not the other way around). Left as a no-op until then
        # so every call site stays safe regardless of init order.
        self._sync_revision_hook = None
        self._init_schema()
        self.resequence_client_serial_numbers()

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
            yield conn
            conn.commit()
        except sqlite3.IntegrityError as e:
            # FIX: Properly bubble up data constraint errors (like NOT NULL)
            # instead of masking them as a wrong master password.
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
        finally:
            conn.close()


    def _ensure_column(self, conn, table: str, column: str, coldef: str):
        """Adds `column` to `table` if it isn't there yet. Safe to call every
        startup -- lets us extend the schema without a full migration system."""
        cur = conn.execute(f"PRAGMA table_info({table})")
        existing = {row[1] for row in cur.fetchall()}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coldef}")

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
                conn.execute("UPDATE clients SET client_id_token = ? WHERE id = ?", (f"CLI-{c_id:05d}", c_id))

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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cs_client ON client_services(client_id);")
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


            # 8. DRS: Filing Types (Compliance Obligations attached to Services)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS filing_types (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    service_id       INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
                    code             TEXT NOT NULL,
                    name             TEXT NOT NULL,
                    frequency        TEXT NOT NULL,
                    start_period     TEXT NOT NULL,
                    due_day          INTEGER,
                    due_day_absolute TEXT,
                    grace_days       INTEGER DEFAULT 0,
                    notes            TEXT,
                    variants_json    TEXT,
                    active           INTEGER DEFAULT 1,
                    imported_at      TEXT,
                    imported_by      TEXT,
                    UNIQUE(service_id, code)
                );
            """)

            # 9. DRS: Client Filing Types (Client Attachment & Variant Assignment)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS client_filing_types (
                    client_id        INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                    filing_type_id   INTEGER NOT NULL REFERENCES filing_types(id) ON DELETE CASCADE,
                    variant_tag      TEXT,
                    is_enabled       INTEGER DEFAULT 1,
                    PRIMARY KEY (client_id, filing_type_id)
                );
            """)
            self._ensure_column(conn, "client_filing_types", "is_enabled", "INTEGER DEFAULT 1")

            # 10. DRS: Filing Status (Period Filing Statuses)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS filing_status (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_id        INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                    filing_type_id   INTEGER NOT NULL REFERENCES filing_types(id) ON DELETE CASCADE,
                    period_label     TEXT NOT NULL,
                    status           TEXT NOT NULL DEFAULT 'pending',
                    arn_number       TEXT,
                    submitted_at     TEXT,
                    updated_at       TEXT NOT NULL,
                    updated_by       TEXT NOT NULL,
                    UNIQUE(client_id, filing_type_id, period_label)
                );
            """)

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

            # 12. Client Tracker Dump (SAD API Interceptor & Extension captures)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tracker_dump (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_id       INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                    service_id      INTEGER,
                    portal          TEXT,
                    period_label    TEXT,
                    arn_number      TEXT,
                    capture_method  TEXT DEFAULT 'DOM_Tracker',
                    status          TEXT DEFAULT 'submitted',
                    raw_payload_json TEXT,
                    captured_by     TEXT,
                    created_at      TEXT NOT NULL
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tracker_dump_client ON tracker_dump(client_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tracker_dump_arn ON tracker_dump(arn_number);")


            # 12. Seed default MCL columns and services if fresh database
            cur = conn.execute("SELECT COUNT(*) FROM mcl_columns")
            if cur.fetchone()[0] == 0:
                self._seed_default_data(conn)

            # Ensure serial number / row index columns are never marked as identity columns
            conn.execute(
                "UPDATE mcl_columns SET is_identity = 0 WHERE LOWER(TRIM(label)) IN ('no', 'no.', 'sl no', 'sl. no.', 's.no.', 'sno', 'id', '#')"
            )

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

    def set_setting(self, key: str, value: str):
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO app_settings (key, value) VALUES (?, ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (key, value),
            )

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
                """SELECT id, label, field_type, dropdown_options, is_identity, sort_order, show_in_search, allow_quick_copy, admin_show_in_search 
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
                    "admin_show_in_search": bool(r[8]) if len(r) > 8 else True
                }
                for r in cur.fetchall()
            ]

    def get_id_column(self) -> Optional[dict]:
        for col in self.get_mcl_columns():
            if col.get("field_type") == "id":
                return col
        return None

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

    def create_mcl_column(self, label: str, field_type: str, dropdown_options=None, is_identity: int = 0) -> int:
        opts_json = json.dumps(dropdown_options) if dropdown_options else None
        with self._connect() as conn:
            if field_type == "id":
                conn.execute("UPDATE mcl_columns SET field_type = 'text' WHERE field_type = 'id'")
            cur = conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM mcl_columns")
            next_order = cur.fetchone()[0]
            cur = conn.execute(
                """INSERT INTO mcl_columns (label, field_type, dropdown_options, is_identity, sort_order)
                   VALUES (?, ?, ?, ?, ?)""",
                (label.strip(), field_type, opts_json, is_identity, next_order)
            )
            return cur.lastrowid

    def update_mcl_column(self, column_id: int, label: str, field_type: str, dropdown_options=None, is_identity: int = None):
        opts_json = json.dumps(dropdown_options) if dropdown_options else None
        with self._connect() as conn:
            if field_type == "id":
                conn.execute("UPDATE mcl_columns SET field_type = 'text' WHERE field_type = 'id' AND id != ?", (column_id,))
            if is_identity is not None:
                conn.execute(
                    """UPDATE mcl_columns SET label=?, field_type=?, dropdown_options=?, is_identity=? 
                       WHERE id=?""",
                    (label.strip(), field_type, opts_json, int(is_identity), column_id)
                )
            else:
                conn.execute(
                    """UPDATE mcl_columns SET label=?, field_type=?, dropdown_options=? 
                       WHERE id=?""",
                    (label.strip(), field_type, opts_json, column_id)
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
        with self._connect() as conn:
            cur = conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM services")
            next_order = cur.fetchone()[0]
            cur = conn.execute(
                """INSERT INTO services (name, login_page_link, userid_column_id, password_column_id,
                                         username_selector, password_selector, automation_mode, extension_flow,
                                         success_selector, arn_selector, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (name.strip(), login_page_link, userid_column_id, password_column_id,
                 username_selector, password_selector, automation_mode, extension_flow,
                 success_selector, arn_selector, next_order)
            )
            return cur.lastrowid

    def update_service(self, service_id: int, name: str, login_page_link: str, userid_column_id: int,
                       password_column_id: int, username_selector: str, password_selector: str,
                       automation_mode: str, extension_flow: str = "double",
                       success_selector: str = "", arn_selector: str = ""):
        with self._connect() as conn:
            conn.execute(
                """UPDATE services SET name=?, login_page_link=?, userid_column_id=?,
                                       password_column_id=?, username_selector=?, password_selector=?,
                                       automation_mode=?, extension_flow=?, success_selector=?, arn_selector=? WHERE id=?""",
                (name.strip(), login_page_link, userid_column_id, password_column_id,
                 username_selector, password_selector, automation_mode, extension_flow,
                 success_selector, arn_selector, service_id)
            )

    def auto_populate_service_selectors(self):
        """
        Auto-scrapes and resolves username & password CSS selectors for services
        that have a login_page_link but are missing valid selectors.
        """
        import urllib.request
        import re

        known_portals = {
            'gst.gov.in': ('#username', '#user_pass'),
            'incometax.gov.in': ('#panAdhaarUserId', "input[type='password']"),
            'epfindia.gov.in': ('#userName', '#password'),
            'tdscpc.gov.in': ('#userId', '#password'),
            'icegate.gov.in': ('#userId', '#password'),
        }

        def _scrape_url(url: str) -> tuple[str, str]:
            if not url or not url.strip():
                return ("", "")
            url_clean = url.strip()
            for domain, (u_sel, p_sel) in known_portals.items():
                if domain in url_clean.lower():
                    return (u_sel, p_sel)
            try:
                req = urllib.request.Request(url_clean, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
                with urllib.request.urlopen(req, timeout=4) as resp:
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
                return ("input[type='text']", "input[type='password']")

        services = self.get_services()
        for svc in services:
            svc_id = svc["id"]
            link = svc.get("login_page_link", "")
            u_sel = (svc.get("username_selector") or "").strip()
            p_sel = (svc.get("password_selector") or "").strip()

            if link and (not u_sel or not p_sel):
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


    # ---------------- Clients ----------------

    def search_clients(self, query: str = "", service_id: int = None,
                        include_archived: bool = False, archived_only: bool = False) -> list[dict]:
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
            
            if query:
                where_clauses.append("mc.is_identity = 1 AND cv.value LIKE ?")
                params.append(like)
            if service_id is not None:
                where_clauses.append("cs.service_id = ?")
                params.append(service_id)
            if archived_only:
                where_clauses.append("c.is_archived = 1")
            elif not include_archived:
                where_clauses.append("c.is_archived = 0")

            if where_clauses:
                sql += " WHERE " + " AND ".join(where_clauses)

            sql += " ORDER BY c.id ASC"

            cur = conn.execute(sql, params)
            rows = cur.fetchall()
            if not rows:
                return []

            client_ids = [r[0] for r in rows]
            clients_map = {
                r[0]: {
                    "id": r[0], "notes": r[1], "created_at": r[2], "updated_at": r[3],
                    "is_archived": bool(r[4]) if len(r) > 4 else False,
                    "client_id_token": r[5] if len(r) > 5 and r[5] else f"CLI-{r[0]:05d}",
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
            "client_id_token": row[5] if len(row) > 5 and row[5] else f"CLI-{row[0]:05d}"
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

    def add_client(self, values: dict[int, str], notes: str, service_ids: list[int], actor: str = "Staff") -> int:
        now = datetime.datetime.utcnow().isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO clients (notes, created_at, updated_at) VALUES (?, ?, ?)",
                (notes, now, now)
            )
            client_id = cur.lastrowid
            token = f"CLI-{client_id:05d}"
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
        - latest_timestamp: ISO timestamp of most recent audit log entry
        - sync_revision: Structural database revision score (client_count * 1000 + log_count)
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

                sync_revision = (client_count * 1000) + log_count

                return {
                    "client_count": client_count,
                    "archived_count": archived_count,
                    "log_count": log_count,
                    "latest_timestamp": latest_ts,
                    "sync_revision": sync_revision,
                }
        except Exception:
            return {
                "client_count": 0,
                "archived_count": 0,
                "log_count": 0,
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

    # ---------------- File Submission Tracker (FST) ----------------

    def get_client_filing_types(self, client_id: int, enabled_only: bool = False) -> list[dict]:
        with self._connect() as conn:
            sql = """SELECT ft.id, ft.service_id, s.name as service_name, ft.code, ft.name,
                            ft.frequency, ft.start_period, ft.due_day, ft.due_day_absolute,
                            ft.grace_days, ft.notes, ft.variants_json, cft.variant_tag,
                            COALESCE(cft.is_enabled, 1) as is_enabled
                     FROM client_services cs
                     JOIN filing_types ft ON ft.service_id = cs.service_id
                     JOIN services s ON s.id = ft.service_id
                     LEFT JOIN client_filing_types cft ON (cft.client_id = cs.client_id AND cft.filing_type_id = ft.id)
                     WHERE cs.client_id = ? AND ft.active = 1"""
            params = [client_id]
            if enabled_only:
                sql += " AND COALESCE(cft.is_enabled, 1) = 1"
            sql += " ORDER BY s.sort_order, ft.code"

            cur = conn.execute(sql, params)
            return [
                {
                    "id": r[0], "service_id": r[1], "service_name": r[2], "code": r[3], "name": r[4],
                    "frequency": r[5], "start_period": r[6], "due_day": r[7], "due_day_absolute": r[8],
                    "grace_days": r[9], "notes": r[10],
                    "variants": json.loads(r[11]) if r[11] else [],
                    "variant_tag": r[12],
                    "is_enabled": bool(r[13])
                }
                for r in cur.fetchall()
            ]

    def set_filing_status(self, client_id: int, filing_type_id: int, period_label: str,
                          status: str, updated_by: str, arn_number: str = None, submitted_at: str = None) -> dict:
        now = datetime.datetime.utcnow().isoformat()
        if status == "submitted" and not submitted_at:
            submitted_at = now
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO filing_status
                   (client_id, filing_type_id, period_label, status, arn_number, submitted_at, updated_at, updated_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(client_id, filing_type_id, period_label) DO UPDATE SET
                       status = excluded.status,
                       arn_number = COALESCE(excluded.arn_number, filing_status.arn_number),
                       submitted_at = COALESCE(excluded.submitted_at, filing_status.submitted_at),
                       updated_at = excluded.updated_at,
                       updated_by = excluded.updated_by""",
                (client_id, filing_type_id, period_label, status, arn_number, submitted_at, now, updated_by)
            )
            
        # Log action in audit trail outside of _connect to avoid recursive connection DB lock
        action_type = "filing_submitted" if status == "submitted" else "filing_status_update"
        arn_info = f" (ARN: {arn_number})" if arn_number else ""
        self.log_action(
            actor=updated_by,
            action=action_type,
            client_id=client_id,
            detail=f"Filing status for period '{period_label}' set to '{status}'{arn_info}"
        )
        return {
            "client_id": client_id, "filing_type_id": filing_type_id,
            "period_label": period_label, "status": status,
            "arn_number": arn_number, "submitted_at": submitted_at,
            "updated_at": now, "updated_by": updated_by
        }

    # ---------------- Tracker Dump Subsystem (SAD & Extension) ----------------

    def insert_tracker_dump(self, client_id: int, service_id: int = None, portal: str = None,
                            period_label: str = None, arn_number: str = None,
                            capture_method: str = "DOM_Tracker", status: str = "submitted",
                            raw_payload_json: str = None, captured_by: str = "System") -> dict:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tracker_dump (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_id       INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                    service_id      INTEGER,
                    portal          TEXT,
                    period_label    TEXT,
                    arn_number      TEXT,
                    capture_method  TEXT DEFAULT 'DOM_Tracker',
                    status          TEXT DEFAULT 'submitted',
                    raw_payload_json TEXT,
                    captured_by     TEXT,
                    created_at      TEXT NOT NULL
                );
            """)

            # Deduplication Check: Ignore identical ARN received within last 10 seconds
            if arn_number and arn_number != "N/A":
                cur = conn.execute(
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

            # Resolve client_id: 1. Direct ID, 2. Token (CLI-00370), 3. MCL Serial No (No. 370)
            valid_id = None
            if client_id:
                # 1. Direct ID match
                cur = conn.execute("SELECT id FROM clients WHERE id = ?", (client_id,))
                row = cur.fetchone()
                if row:
                    valid_id = row[0]

                # 2. Token match (CLI-00370 or CLI-370)
                if not valid_id:
                    try:
                        token_str = f"CLI-{int(client_id):05d}"
                        cur = conn.execute("SELECT id FROM clients WHERE client_id_token = ? OR client_id_token = ?", (token_str, f"CLI-{client_id}"))
                        row = cur.fetchone()
                        if row:
                            valid_id = row[0]
                    except Exception:
                        pass

                # 3. MCL Serial No / SL No column match (e.g. No. 370)
                if not valid_id:
                    try:
                        cur = conn.execute(
                            """SELECT cv.client_id
                               FROM client_values cv
                               JOIN mcl_columns mc ON mc.id = cv.column_id
                               WHERE TRIM(cv.value) = ? AND LOWER(TRIM(mc.label)) IN ('no', 'no.', 'sl no', 'sl. no.', 's.no.', 'sno', 'id', '#')""",
                            (str(client_id).strip(),)
                        )
                        row = cur.fetchone()
                        if row:
                            valid_id = row[0]
                    except Exception:
                        pass

                # 4. Search Grid Row Number match (e.g. Row #370 in Search Grid -> 370th client in DB)
                if not valid_id:
                    try:
                        row_num = int(client_id)
                        if row_num > 0:
                            cur = conn.execute("SELECT id FROM clients WHERE is_archived = 0 ORDER BY id ASC LIMIT 1 OFFSET ?", (row_num - 1,))
                            row = cur.fetchone()
                            if row:
                                valid_id = row[0]
                    except Exception:
                        pass

                # 5. Name / PAN / GSTIN substring match in client_values
                if not valid_id:
                    try:
                        search_str = f"%{str(client_id).strip()}%"
                        cur = conn.execute(
                            "SELECT client_id FROM client_values WHERE value LIKE ? LIMIT 1",
                            (search_str,)
                        )
                        row = cur.fetchone()
                        if row:
                            valid_id = row[0]
                    except Exception:
                        pass

            if not valid_id:
                cur = conn.execute("SELECT id FROM clients ORDER BY id ASC LIMIT 1")
                row = cur.fetchone()
                if row:
                    valid_id = row[0]
                else:
                    cur = conn.execute("INSERT INTO clients (notes, created_at, updated_at) VALUES ('Default System Client', ?, ?)", (now, now))
                    valid_id = cur.lastrowid

            cur = conn.execute(
                """INSERT INTO tracker_dump
                   (client_id, service_id, portal, period_label, arn_number, capture_method, status, raw_payload_json, captured_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (valid_id, service_id, portal, period_label, arn_number, capture_method, status, raw_payload_json, captured_by, now)
            )
            dump_id = cur.lastrowid
        return {
            "id": dump_id, "client_id": valid_id, "service_id": service_id,
            "portal": portal, "period_label": period_label, "arn_number": arn_number,
            "capture_method": capture_method, "status": status, "created_at": now
        }

    def get_tracker_dumps(self, client_id: int = None, limit: int = 200, search_query: str = None) -> list[dict]:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tracker_dump (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_id       INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
                    service_id      INTEGER,
                    portal          TEXT,
                    period_label    TEXT,
                    arn_number      TEXT,
                    capture_method  TEXT DEFAULT 'DOM_Tracker',
                    status          TEXT DEFAULT 'submitted',
                    raw_payload_json TEXT,
                    captured_by     TEXT,
                    created_at      TEXT NOT NULL
                );
            """)
            sql = """SELECT td.id, td.client_id, c.client_id_token, td.service_id, s.name as service_name,
                            td.portal, td.period_label, td.arn_number, td.capture_method, td.status,
                            td.raw_payload_json, td.captured_by, td.created_at
                     FROM tracker_dump td
                     LEFT JOIN clients c ON c.id = td.client_id
                     LEFT JOIN services s ON s.id = td.service_id
                     WHERE 1=1"""
            params = []
            if client_id:
                sql += " AND td.client_id = ?"
                params.append(client_id)
            if search_query:
                sql += " AND (td.arn_number LIKE ? OR td.portal LIKE ? OR td.period_label LIKE ?)"
                q = f"%{search_query}%"
                params.extend([q, q, q])
            sql += " ORDER BY td.id DESC LIMIT ?"
            params.append(limit)

            cur = conn.execute(sql, params)
            rows = cur.fetchall()

            # Pre-fetch MCL columns metadata once
            mcl_cols = self.get_mcl_columns()

            # Build client display name & pan map for all unique client_ids in results
            client_map = {}
            for r in rows:
                cid = r[1]
                if cid and cid not in client_map:
                    try:
                        cdata = self._fetch_client_full(conn, cid)
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
                                "name": name_val or cdata.get("client_id_token", f"CLI-{cid:05d}"),
                                "pan": pan_val
                            }
                    except Exception:
                        pass
                if cid not in client_map:
                    client_map[cid] = {"name": f"CLI-{cid:05d}" if cid else "Unknown Client", "pan": ""}

            results = []
            for r in rows:
                cid = r[1]
                info = client_map.get(cid, {"name": "Unknown Client", "pan": ""})
                results.append({
                    "id": r[0], "client_id": cid,
                    "client_name": info["name"], "pan": info["pan"],
                    "service_id": r[3], "service_name": r[4] or r[5] or "Portal", "portal": r[5] or "",
                    "period_label": r[6] or "", "arn_number": r[7] or "N/A", "capture_method": r[8] or "DOM_Tracker",
                    "status": r[9] or "submitted", "raw_payload_json": r[10] or "{}", "captured_by": r[11] or "System",
                    "created_at": r[12]
                })
            return results

    def delete_tracker_dump(self, dump_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM tracker_dump WHERE id = ?", (dump_id,))
            return cur.rowcount > 0

    def clear_tracker_dumps(self) -> int:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM tracker_dump")
            return cur.rowcount


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

