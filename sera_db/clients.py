"""
sera_db/clients.py - Client CRUD, bulk operations, activity, duplicates, notes, cell formatting, CSV export.

Split out of database.py; methods are unchanged. Mixed into SeraDatabase.
"""

import datetime
import time
import re


class ClientsMixin:


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
                          s.automation_mode, s.sort_order, s.extension_flow, s.success_selector, s.arn_selector,
                          s.automation_mode_2
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
                    "automation_mode": ("extension" if r[7] in ("automated", "playwright") or not r[7] else r[7]),
                    "sort_order": r[8],
                    "extension_flow": r[9] if len(r) > 9 else "double",
                    "success_selector": r[10] if len(r) > 10 else "",
                    "arn_selector": r[11] if len(r) > 11 else "",
                    "automation_mode_2": r[12] if len(r) > 12 and r[12] else "",
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
            letter = self.get_token_letter(conn=conn)
            if letter:
                prefix = f"{letter}-"
                rows = conn.execute(
                    "SELECT client_id_token FROM clients WHERE client_id_token LIKE ? AND id != ?",
                    (f"{prefix}%", client_id)
                ).fetchall()
                max_n = 0
                for (tok,) in rows:
                    if tok and tok.startswith(prefix):
                        suffix = tok[len(prefix):]
                        if suffix.isdigit():
                            max_n = max(max_n, int(suffix))
                token = f"{letter}-{max_n + 1}"
            else:
                token = str(client_id)
            conn.execute("UPDATE clients SET client_id_token=? WHERE id=?", (token, client_id))

            # Auto-assign serial number to ID column if present and not provided
            id_col = self.get_id_column()
            if not id_col:
                cols = self.get_mcl_columns()
                for c in cols:
                    lbl = c.get("label", "").strip().lower()
                    if lbl in {"no", "no.", "sl no", "sl. no.", "s.no.", "sno", "id", "#", "numer", "number"}:
                        id_col = c
                        break
            if id_col and (id_col["id"] not in values or not str(values.get(id_col["id"], "")).strip()):
                rows = conn.execute(
                    "SELECT value FROM client_values WHERE column_id = ? AND client_id != ?",
                    (id_col["id"], client_id)
                ).fetchall()
                max_serial = 0
                for (v,) in rows:
                    if v is not None:
                        v_str = str(v).strip()
                        if v_str.isdigit():
                            max_serial = max(max_serial, int(v_str))
                values[id_col["id"]] = str(max_serial + 1)

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

    def get_client_by_pan(self, pan: str) -> dict | None:
        """Finds active client by PAN and returns full client dict, or None."""
        if not pan or not str(pan).strip():
            return None
        clean_pan = str(pan).strip().upper()
        with self._connect() as conn:
            cur = conn.execute(
                """SELECT cv.client_id FROM client_values cv
                   JOIN clients c ON c.id = cv.client_id
                   JOIN mcl_columns mc ON mc.id = cv.column_id
                   WHERE c.is_archived = 0 AND UPPER(TRIM(cv.value)) = ?
                   LIMIT 1""",
                (clean_pan,)
            )
            row = cur.fetchone()
            if not row:
                return None
            return self._fetch_client_full(conn, row[0])

    def get_client_pan(self, client_or_id) -> str:
        """Resolves 10-character PAN for a client, checking MCL PAN column, values, or deriving from GSTIN."""
        client = client_or_id if isinstance(client_or_id, dict) else self.get_client(client_or_id)
        if not client or not isinstance(client, dict):
            return ""
        values = client.get("values", {})
        mcl = self.get_mcl_columns()
        # 1. Direct PAN column matching exact 'PAN' or word boundary \bPAN\b (excluding 'COMPANY' and 'PASSWORD')
        pan_col = next((
            c["id"] for c in mcl
            if c.get("label", "").strip().upper() == "PAN"
            or (re.search(r"\bPAN\b", c.get("label", "").upper())
                and "PASS" not in c.get("label", "").upper()
                and "COMPAN" not in c.get("label", "").upper())
        ), None)
        if pan_col and pan_col in values:
            val = str(values[pan_col]).strip().upper()
            if re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", val):
                return val
            if val:
                return val
        # 2. Check any value in values that matches 10-character PAN format
        for v in values.values():
            val = str(v).strip().upper()
            if re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", val):
                return val
        # 3. Derive from GSTIN in values (chars 2:12)
        for v in values.values():
            val = str(v).strip().upper()
            if re.match(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$", val):
                return val[2:12]
        return ""

    def update_client_single_field(self, client_id: int, column_id: int, value: str, actor: str = "Staff", log_action: bool = True) -> bool:
        """Updates or inserts a single column value for a client in client_values."""
        now = datetime.datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO client_values (client_id, column_id, value)
                   VALUES (?, ?, ?)
                   ON CONFLICT(client_id, column_id) DO UPDATE SET value = excluded.value""",
                (client_id, column_id, str(value or "").strip())
            )
            conn.execute("UPDATE clients SET updated_at = ? WHERE id = ?", (now, client_id))
        if log_action:
            self.log_action(actor=actor, action="update", client_id=client_id, detail=f"Updated password via SCC Quick-Tag for client CLI-{client_id:05d}")
        return True

    def is_client_scc_verified(self, pan: str = "", client_id: int | None = None) -> bool:
        """Checks if a client has already been verified via SCC."""
        client = None
        if client_id is not None:
            client = self.get_client(client_id)
        elif pan and str(pan).strip():
            client = self.get_client_by_pan(pan)
        if not client:
            return False
        notes = str(client.get("notes") or "")
        if "password verified via scc" in notes.lower():
            return True
        for val in client.get("values", {}).values():
            if val and "password verified via scc" in str(val).lower():
                return True
        return False

    def tag_client_scc_verified(self, client_id: int, combo_label: str = "", actor: str = "Staff") -> bool:
        """Marks client notes as verified via SCC and syncs any MCL notes column."""
        client = self.get_client(client_id)
        if not client:
            return False
        tag_text = "Password verified via SCC"
        current_notes = str(client.get("notes") or "").strip()
        if tag_text.lower() not in current_notes.lower():
            updated_notes = f"{current_notes}\n{tag_text}".strip() if current_notes else tag_text
            self.update_client_notes(client_id, updated_notes)

        # Also sync any MCL column labeled 'notes' or 'remarks'
        try:
            for c in self.get_mcl_columns():
                lbl = (c.get("label") or "").strip().lower()
                if lbl in ("notes", "remarks", "note", "remark"):
                    val = str(client.get("values", {}).get(c["id"]) or "").strip()
                    if tag_text.lower() not in val.lower():
                        new_val = f"{val}\n{tag_text}".strip() if val else tag_text
                        self.update_client_single_field(client_id, c["id"], new_val, actor=actor, log_action=False)
        except Exception:
            pass
        return True



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
            cols = {r[1] for r in conn.execute("PRAGMA table_info(clients)").fetchall()}
            order_sec = "gid ASC" if "gid" in cols else "id ASC"
            clients = conn.execute(f"SELECT id FROM clients WHERE is_archived = 0 ORDER BY created_at ASC, {order_sec}, id ASC").fetchall()
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
            c_cols = {r[1] for r in conn.execute("PRAGMA table_info(clients)").fetchall()}
            c_order_sec = "gid ASC" if "gid" in c_cols else "id ASC"
            client_ids = [
                r[0] for r in conn.execute(
                    f"SELECT id FROM clients WHERE is_archived = 0 ORDER BY created_at ASC, {c_order_sec}, id ASC"
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
