"""
sera_db/schema.py - Schema creation, migrations, auto-heal and first-run seeding.

Split out of database.py; methods are unchanged. Mixed into SeraDatabase.
"""

import datetime
import json
import os
import sys
import shutil


class SchemaMixin:

    def _migrate_legacy_peer_logs(self):
        """P4-1: peer_logs/ (per-workstation SQLite files written by the legacy
        PeerAuditLogManager, §5 P0) is no longer used -- audit_log itself replicates
        under Sera Sync v3. Blueprint §0 rule 3 forbids deleting data, so a one-time,
        idempotent move renames any existing peer_logs/ directory to
        backups/peer_logs-<YYYYmmdd_HHMMSS>/ instead of removing it."""
        peer_logs_dir = os.path.join(self.app_dir, "peer_logs")
        if not os.path.isdir(peer_logs_dir):
            return
        backups_dir = os.path.join(self.app_dir, "backups")
        os.makedirs(backups_dir, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = os.path.join(backups_dir, f"peer_logs-{ts}")
        suffix = 0
        while os.path.exists(dest):
            suffix += 1
            dest = os.path.join(backups_dir, f"peer_logs-{ts}-{suffix}")
        shutil.move(peer_logs_dir, dest)
        print(f"[database] Moved legacy peer_logs/ to {dest}")


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

    def _auto_heal_raw_db(self):
        """Backs up an un-decryptable rawPayload.db (and its -wal/-shm/-journal sidecars) and lets
        _init_raw_schema recreate it with the active vault key. Nothing is removed unless every
        non-empty file was first copied or moved to a *.bak file (blueprint §0 rule 3).
        Returns True if the old files are out of the way, False if they were left untouched."""
        if self._is_office_mode():
            raise RuntimeError(
                f"rawPayload.db could not be opened with this office's key (auto-heal is disabled in office mode): {self.raw_db_path}"
            )
        import datetime, shutil
        now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{self.raw_db_path}.key_mismatch_{now_str}.bak"
        suffixes = ["", "-wal", "-shm", "-journal"]
        # Only files with content are worth keeping; empty ones are removed without a backup.
        to_keep = [s for s in suffixes
                   if os.path.exists(self.raw_db_path + s) and os.path.getsize(self.raw_db_path + s) > 0]

        backed_up = []
        try:
            for s in to_keep:
                shutil.copy2(self.raw_db_path + s, backup_name + s)
                if os.path.getsize(backup_name + s) != os.path.getsize(self.raw_db_path + s):
                    raise OSError(f"backup of rawPayload.db{s} is incomplete")
                backed_up.append(s)
        except Exception as copy_err:
            print(f"[database] Backup copy failed: {copy_err}. Trying to move the files instead.")
            try:
                for s in to_keep:
                    if s not in backed_up:
                        os.replace(self.raw_db_path + s, backup_name + s)
            except Exception as move_err:
                print(f"[database] Could not back up rawPayload.db ({move_err}). Leaving it untouched.")
                return False

        for s in suffixes:
            p = self.raw_db_path + s
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception as e:
                    print(f"[database] Could not remove rawPayload.db{s}: {e}")
                    return False
        print("[database] Auto-recovery: Re-initializing fresh rawPayload.db with active vault key.")

        # Record what happened. No backup means the main file was empty: nothing to keep.
        has_backup = "" in to_keep
        self.raw_db_was_reset = backup_name if has_backup else "reset_without_backup"
        if not self._master_db_failed:
            try:
                if has_backup:
                    detail = f"Tracker database was reset. Backup: {os.path.basename(backup_name)}"
                else:
                    detail = "Tracker database was reset (no backup created)"
                self.log_action("System", "raw_db_reset", detail=detail)
            except Exception as e:
                print(f"[database] Could not write raw_db_reset audit entry: {e}")
        return True

    def _init_raw_schema(self):
        """Initializes tables and indexes inside rawPayload.db with auto-healing recovery."""
        # 1. A 0-byte dummy file is reset (and reported) too.
        if os.path.exists(self.raw_db_path) and os.path.getsize(self.raw_db_path) == 0:
            if not self._master_db_failed:
                if self._is_office_mode():
                    raise RuntimeError(
                        f"rawPayload.db is 0 bytes and auto-heal is disabled in office mode: {self.raw_db_path}"
                    )
                print(f"[database] rawPayload.db is 0 bytes. Performing automatic recovery...")
                self._auto_heal_raw_db()

        # 2. Test decryption. If key mismatch occurs (e.g. after transporting salt to a secondary PC),
        # auto-heal so staff is never locked out with 'Could not open rawPayload.db with provided key'.
        try:
            with self._connect_raw() as test_conn:
                test_conn.execute("SELECT count(*) FROM sqlite_master;")
        except Exception as e:
            err_msg = str(e).lower()
            if "key" in err_msg or "not a database" in err_msg or "encrypted" in err_msg or "codec" in err_msg:
                # Skip auto-heal if master.db itself failed to open
                if self._master_db_failed:
                    print(f"[database] rawPayload.db key mismatch, but master.db failed to open. Skipping auto-heal.")
                else:
                    if self._is_office_mode():
                        raise RuntimeError(
                            f"rawPayload.db could not be opened with this office's key (auto-heal is disabled in office mode): {self.raw_db_path}"
                        ) from e
                    print(f"[database] rawPayload.db key mismatch: {e}. Performing automatic recovery...")
                    if not self._auto_heal_raw_db():
                        raise RuntimeError(
                            f"rawPayload.db could not be opened with this office's key and could not be "
                            f"backed up, so it was left untouched: {self.raw_db_path}"
                        ) from e

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
            self._ensure_column(conn, "tracker_dump", "dataset_key", "TEXT")
            self._migrate_tracker_dump_nullable(conn)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tracker_dump_client ON tracker_dump(client_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tracker_dump_arn ON tracker_dump(arn_number);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tracker_dump_unassigned ON tracker_dump(unassigned_identity);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tracker_dump_client_period ON tracker_dump(client_id, period_label);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tracker_dump_unassigned_period ON tracker_dump(unassigned_identity, period_label);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tracker_dump_dataset_key ON tracker_dump(dataset_key);")

            # Recompute canonical dataset_key and purge duplicate rows (Admin PC only, blueprint §5 P3-6)
            try:
                if self.is_admin_pc(conn=conn):
                    cid_to_gid = {}
                    try:
                        with self._connect() as m_conn:
                            m_cols = {r[1] for r in m_conn.execute("PRAGMA table_info(clients)").fetchall()}
                            if "gid" in m_cols:
                                cid_to_gid = dict(
                                    m_conn.execute("SELECT id, gid FROM clients WHERE gid IS NOT NULL").fetchall()
                                )
                    except Exception:
                        pass

                    # Old shadow-mode SGT rows become live rows first (status-aware merge).
                    self._convert_sgt_shadow_rows(conn)
                    # SGT builds its own keys (core/dataset_key.py for live rows, its "SGT:"
                    # namespace for shadow rows, a session-scoped identifier while the client is
                    # unknown, "ARN_<n>" for a dataset known only by its ARN). Recomputing them
                    # from the row would break SGT's supersede-by-key and, for shadow rows, fold
                    # them into another engine's row in the purge below - so they are left alone.
                    rows_to_update = conn.execute("SELECT id, portal, client_id, unassigned_identity, period_label, raw_payload_json, dataset_key FROM tracker_dump "
                                                  "WHERE capture_method IS NULL OR capture_method NOT LIKE 'SGT%'").fetchall()
                    for r_id, r_port, r_cid, r_unassigned, r_period, r_json, r_dkey in rows_to_update:
                        client_gid = cid_to_gid.get(r_cid) if r_cid else None
                        cand_id = r_unassigned or (f"CLI_{client_gid}" if client_gid else (f"CLI_{r_cid}" if r_cid else "UNKNOWN"))
                        cand_form = ""
                        if r_json:
                            try:
                                cj = json.loads(r_json)
                                c_raw = cj.get("raw_payload") if isinstance(cj.get("raw_payload"), dict) else {}
                                cand_id = cj.get("gstin") or cj.get("pan") or c_raw.get("gstin") or c_raw.get("pan") or cand_id
                                cand_form = cj.get("filing_type") or c_raw.get("filing_type") or ""
                            except Exception:
                                pass
                        if not cand_form and r_dkey and r_dkey.count(":") >= 3:
                            parts = r_dkey.split(":")
                            if parts[2] and parts[2] != "FORM":
                                cand_form = parts[2]
                        d_key = self.compute_dataset_key(r_port, cand_id, cand_form, r_period)
                        if d_key != r_dkey:
                            conn.execute("UPDATE tracker_dump SET dataset_key = ? WHERE id = ?", (d_key, r_id))

                    t_cols = {r[1] for r in conn.execute("PRAGMA table_info(tracker_dump)").fetchall()}
                    order_sec = "gid DESC" if "gid" in t_cols else "id DESC"

                    # Purge duplicate entries, keeping strictly the newest by (created_at DESC, gid DESC)
                    conn.execute(f"""
                        DELETE FROM tracker_dump 
                        WHERE rowid NOT IN (
                            SELECT rowid FROM (
                                SELECT rowid, ROW_NUMBER() OVER (
                                    PARTITION BY dataset_key
                                    ORDER BY created_at DESC, {order_sec}, id DESC
                                ) AS rn
                                FROM tracker_dump
                                WHERE dataset_key IS NOT NULL AND dataset_key != ''
                            ) WHERE rn = 1
                        ) AND dataset_key IS NOT NULL AND dataset_key != '';
                    """)

                    # Purge legacy non-filing dummy rows (ITR landing page and profile views)
                    conn.execute("""
                        DELETE FROM tracker_dump 
                        WHERE capture_method = 'SDC_itr_landing' 
                           OR portal LIKE '%Landing / e-File%' 
                           OR portal LIKE '%Profile / Identity%'
                           OR dataset_key LIKE '%:PROFILEIDENTITY:%'
                           OR dataset_key LIKE '%:ITRLANDINGEFILE:%'
                           OR period_label LIKE '%Due Date -%' 
                           OR status = 'FY -' 
                           OR portal LIKE '%Status -%'
                           OR (period_label = 'Current Period' AND status = 'Initiated')
                           OR raw_payload_json LIKE '%Indicates Mandatory Fields%';
                    """)
            except Exception as e:
                print(f"[database] startup deduplication notice: {e}")

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
            
            self._ensure_column(conn, "tracker_dump", "notes", "TEXT")
            self._ensure_column(conn, "tracker_dump", "screenshot_path", "TEXT")
            self._ensure_column(conn, "client_raw_containers", "notes", "TEXT")
            self._ensure_column(conn, "client_raw_containers", "screenshot_path", "TEXT")
            
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sdc_timelines_pan ON sdc_session_timelines(pan);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sdc_timelines_cid ON sdc_session_timelines(client_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_client_raw_containers_last_updated ON client_raw_containers(last_updated DESC);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tracker_dump_created ON tracker_dump(created_at DESC);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sdc_timelines_start ON sdc_session_timelines(start_time DESC);")

            # Sera Distill (SDIS Part Q): registered datapoints and the user's decisions, synced
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sdis_fields (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    gid         TEXT,
                    name        TEXT NOT NULL,
                    portal      TEXT,
                    section     TEXT,
                    spec_json   TEXT NOT NULL,
                    label       TEXT,
                    status      TEXT DEFAULT 'active',
                    created_by  TEXT,
                    updated_at  TEXT NOT NULL
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sdis_decisions (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    gid         TEXT,
                    signature   TEXT NOT NULL,
                    decision    TEXT NOT NULL,
                    label       TEXT,
                    updated_at  TEXT NOT NULL
                );
            """)
            # SDIS Part U / S.3: the field library and the containers document (one row)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sdis_mcl (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    gid         TEXT,
                    name        TEXT NOT NULL,
                    label       TEXT,
                    value_type  TEXT,
                    class       TEXT,
                    portal      TEXT,
                    status      TEXT DEFAULT 'active',
                    created_by  TEXT,
                    updated_at  TEXT NOT NULL
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sdis_config (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    gid         TEXT,
                    name        TEXT NOT NULL,
                    doc_json    TEXT NOT NULL,
                    version     INTEGER DEFAULT 0,
                    updated_by  TEXT,
                    updated_at  TEXT NOT NULL
                );
            """)
            self._ensure_column(conn, "sdis_fields", "mcl_gid", "TEXT")

            import sync_tables
            sync_tables.ensure_sync_infrastructure(conn, "raw", device_id=self._device_id)
            # rawPayload.db follows master.db's sync mode (P3-3 review): an auto-healed
            # (P0-8) or replaced rawPayload.db starts in 'off' and would stop capturing.
            if not self._master_db_failed:
                raw_mode = conn.execute("SELECT value FROM _sync_meta WHERE key = 'mode'").fetchone()
                if ((raw_mode[0] if raw_mode else None) or "off") != self._sync_mode:
                    conn.execute(
                        "INSERT INTO _sync_meta(key, value) VALUES ('mode', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (self._sync_mode,))
                    print(f"[database] rawPayload.db sync mode set to master.db's: {self._sync_mode}")

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
                    automation_mode    TEXT NOT NULL DEFAULT 'extension',
                    automation_mode_2  TEXT DEFAULT '',
                    extension_flow     TEXT NOT NULL DEFAULT 'double',
                    success_selector   TEXT,
                    arn_selector       TEXT,
                    sort_order         INTEGER NOT NULL DEFAULT 0
                );
            """)
            self._ensure_column(conn, "services", "extension_flow", "TEXT NOT NULL DEFAULT 'double'")
            self._ensure_column(conn, "services", "success_selector", "TEXT")
            self._ensure_column(conn, "services", "arn_selector", "TEXT")
            self._ensure_column(conn, "services", "automation_mode_2", "TEXT DEFAULT ''")
            conn.execute("UPDATE services SET automation_mode = 'extension' WHERE automation_mode IN ('automated', 'playwright')")

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
                    name TEXT NOT NULL UNIQUE,
                    gid  TEXT
                );
            """)
            self._ensure_column(conn, "staff_users", "alias", "TEXT")
            self._ensure_column(conn, "staff_users", "gid", "TEXT")

            # Seed default 6 canonical staff slots if fresh
            cur = conn.execute("SELECT COUNT(*) FROM staff_users")
            if cur.fetchone()[0] == 0:
                import sync_schema
                for i in range(1, 7):
                    sname = f"User {i}"
                    sgid = sync_schema.seed_gid("staff_users", sname)
                    conn.execute("INSERT INTO staff_users (name, alias, gid) VALUES (?, ?, ?)", (sname, None, sgid))


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

            import sync_tables
            sync_tables.ensure_sync_infrastructure(conn, "master", device_id=self._device_id)
            mode_row = conn.execute("SELECT value FROM _sync_meta WHERE key = 'mode'").fetchone()
            self._sync_mode = (mode_row[0] if mode_row else None) or "off"

        self.load_ini_defaults()
        # re_resolve_all_tracker_dumps() used to run here, on every open: ~3 s of the app's
        # start-up spent before the window appeared (measured 2026-09-22). It is part of
        # run_startup_maintenance() now, which the app runs in the background after the window
        # shows; callers that don't defer maintenance still get it before the constructor returns.

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

            import sync_schema
            self._ensure_column(conn, "mcl_columns", "gid", "TEXT")
            self._ensure_column(conn, "services", "gid", "TEXT")

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
                sgid = sync_schema.seed_gid("mcl_columns", lbl)
                if conn.execute("SELECT 1 FROM mcl_columns WHERE gid = ?", (sgid,)).fetchone():
                    sgid = None
                cur = conn.execute(
                    """INSERT INTO mcl_columns (label, field_type, is_identity, is_internal_pk, sort_order, show_in_search, allow_quick_copy, admin_show_in_search, gid)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        lbl,
                        kwargs.get("field_type", "text"),
                        kwargs.get("is_identity", 0),
                        kwargs.get("is_internal_pk", 0),
                        kwargs.get("sort_order", 0),
                        kwargs.get("show_in_search", 1),
                        kwargs.get("allow_quick_copy", 1),
                        kwargs.get("admin_show_in_search", 1),
                        sgid,
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
                    sgid = sync_schema.seed_gid("services", name)
                    if conn.execute("SELECT 1 FROM services WHERE gid = ?", (sgid,)).fetchone():
                        sgid = None
                    conn.execute(
                        """INSERT OR IGNORE INTO services (name, login_page_link, userid_column_id, password_column_id, username_selector, password_selector, automation_mode, extension_flow, sort_order, gid)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                            sgid,
                        )
                    )
            return True
        except Exception:
            return False

    def _seed_default_data(self, conn):
        """Seeds default MCL columns and Services for fresh database installations."""
        import sync_schema
        self._ensure_column(conn, "mcl_columns", "gid", "TEXT")
        self._ensure_column(conn, "services", "gid", "TEXT")
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
                """INSERT INTO mcl_columns (label, field_type, is_identity, sort_order, show_in_search, allow_quick_copy, gid)
                   VALUES (?, ?, ?, ?, 1, 1, ?)""",
                (label, ftype, is_id, s_order, sync_schema.seed_gid("mcl_columns", label))
            )
            col_ids[label] = cur.lastrowid

        def_svcs = [
            ("GST", "https://services.gst.gov.in/services/login", col_ids.get("USER ID"), col_ids.get("GST_Password"), "#username", "#user_pass", 1),
            ("Income Tax", "https://eportal.incometax.gov.in/iec/foservices/#/login", col_ids.get("USER ID"), col_ids.get("IT_Password"), "#panAdhaarUserId", "input[type='password']", 2),
            ("Email", "", col_ids.get("EMAIL"), col_ids.get("Email_Password"), "", "", 3),
        ]
        for name, link, u_id, p_id, u_sel, p_sel, s_order in def_svcs:
            conn.execute(
                """INSERT OR IGNORE INTO services (name, login_page_link, userid_column_id, password_column_id, username_selector, password_selector, sort_order, gid)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (name, link, u_id, p_id, u_sel, p_sel, s_order, sync_schema.seed_gid("services", name))
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
                            sgid = sync_schema.seed_gid("mcl_columns", lbl)
                            if conn.execute("SELECT 1 FROM mcl_columns WHERE gid = ?", (sgid,)).fetchone():
                                sgid = None
                            conn.execute(
                                """INSERT INTO mcl_columns (label, field_type, is_identity, is_internal_pk, sort_order, show_in_search, allow_quick_copy, admin_show_in_search, gid)
                                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                (
                                    lbl,
                                    kwargs.get("field_type", "text"),
                                    kwargs.get("is_identity", 0),
                                    kwargs.get("is_internal_pk", 0),
                                    kwargs.get("sort_order", 0),
                                    kwargs.get("show_in_search", 1),
                                    kwargs.get("allow_quick_copy", 1),
                                    kwargs.get("admin_show_in_search", 1),
                                    sgid,
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
                            sgid = sync_schema.seed_gid("services", name)
                            if conn.execute("SELECT 1 FROM services WHERE gid = ?", (sgid,)).fetchone():
                                sgid = None
                            conn.execute(
                                """INSERT OR IGNORE INTO services (name, login_page_link, userid_column_id, password_column_id, username_selector, password_selector, automation_mode, extension_flow, sort_order, gid)
                                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                                    sgid,
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
