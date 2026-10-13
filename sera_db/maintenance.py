"""
sera_db/maintenance.py - Startup maintenance, name clean-up and storage optimisation.

Split out of database.py; methods are unchanged. Mixed into SeraDatabase.
"""

import json
import re

from sera_db.common import SKELETON_NAME_REGEX


class MaintenanceMixin:

    def run_startup_maintenance(self):
        """Runs background resequencing and storage maintenance."""
        # Data-rewriting maintenance runs ONLY on the admin PC (blueprint §5 P3-6)
        if self.is_admin_pc():
            try:
                self.re_resolve_all_tracker_dumps()
            except Exception as e:
                print(f"[-] Startup tracker re-resolve skipped: {e}")
            try:
                self.resequence_client_serial_numbers()
            except Exception as e:
                print(f"[-] Startup serial resequence skipped: {e}")
            try:
                self._clean_ligature_noise_from_names()
            except Exception as e:
                print(f"[-] Startup ligature name cleanup skipped: {e}")
            try:
                self.deduplicate_tracker_dumps()
            except Exception as e:
                print(f"[-] Startup tracker dump deduplication skipped: {e}")
            try:
                self.upgrade_all_placeholder_client_names()
            except Exception as e:
                print(f"[-] Startup placeholder client name upgrade skipped: {e}")
            try:
                from core.sgt.sgt_repair import run_pending
                run_pending(self)               # GST rows SGT moved to another period (plans put in sgt_repair/plans)
            except Exception as e:
                print(f"[-] Startup SGT row repair skipped: {e}")

        # Non-data rewriting tasks stay on all PCs
        try:
            self.optimize_storage()
        except Exception as e:
            print(f"[-] Startup storage optimization skipped: {e}")
        try:
            self._migrate_legacy_peer_logs()
        except Exception as e:
            print(f"[-] Legacy peer_logs migration skipped: {e}")

    # ─── Material Icon Ligature Noise Cleanup ─────────────────────────────────
    _ICON_LIGATURE_RE = re.compile(
        r'(?:EXPAND[\s_]*MORE|EXPANDMORE|EXPAND[\s_]*LESS|EXPANDLESS'
        r'|KEYBOARD[\s_]*ARROW[\s_]*(?:DOWN|UP|RIGHT|LEFT)'
        r'|ARROW[\s_]*(?:DOWN|UP|DROP)'
        r'|MORE[\s_]*VERT|MORE[\s_]*HORIZ'
        r'|ACCOUNT[\s_]*CIRCLE|MAT[\s_]*ICON'
        r'|PERSON(?=\s|$)|USER(?=\s|$))',
        re.IGNORECASE,
    )
    _ROLE_TAG_RE = re.compile(
        r'\b(?:Individual|Taxpayer|HUFs?|Company|Representative|Director|Partners?|Proprietor)\b',
        re.IGNORECASE,
    )

    def _clean_name_retrofix(self, raw: str) -> str:
        """Strips Material Icon ligature strings from a stored name value."""
        if not raw:
            return raw
        # Strip ligatures FIRST — they concatenate directly onto names/role-tags without spaces
        s = self._ICON_LIGATURE_RE.sub(' ', raw)
        # Now strip role tags — word boundaries now work correctly
        s = self._ROLE_TAG_RE.sub('', s)
        s = re.sub(r'\.\.\.$', '', s)
        s = re.sub(r'[^A-Za-z\s.\'\-]', ' ', s)
        s = re.sub(r'\s+', ' ', s).strip().upper()
        return s if s else raw

    def _clean_ligature_noise_from_names(self):
        """
        One-time retrospective fix: scans tracker_dump (rawPayload.db) and
        sdc_session_timelines (rawPayload.db) for client_name values that
        contain Material Icon ligature noise (e.g. 'SERAJ MOLLAEXPAND MORE')
        and re-cleans them in-place.
        Idempotent — safe to run on every startup.
        """
        total_fixed = 0
        try:
            with self._connect_raw() as conn:
                # Fix tracker_dump (if client_name column exists)
                t_cols = {r[1] for r in conn.execute("PRAGMA table_info(tracker_dump)").fetchall()}
                if "client_name" in t_cols:
                    rows = conn.execute(
                        "SELECT id, client_name FROM tracker_dump WHERE client_name IS NOT NULL AND client_name != ''"
                    ).fetchall()
                    for row_id, cname in rows:
                        if self._ICON_LIGATURE_RE.search(cname or ''):
                            fixed = self._clean_name_retrofix(cname)
                            conn.execute("UPDATE tracker_dump SET client_name = ? WHERE id = ?", (fixed, row_id))
                            print(f"[database] Ligature fix tracker_dump #{row_id}: {cname!r} -> {fixed!r}")
                            total_fixed += 1

                # Fix sdc_session_timelines
                s_cols = {r[1] for r in conn.execute("PRAGMA table_info(sdc_session_timelines)").fetchall()}
                if "client_name" in s_cols:
                    rows = conn.execute(
                        "SELECT session_id, client_name FROM sdc_session_timelines WHERE client_name IS NOT NULL AND client_name != ''"
                    ).fetchall()
                    for sid, cname in rows:
                        if self._ICON_LIGATURE_RE.search(cname or ''):
                            fixed = self._clean_name_retrofix(cname)
                            conn.execute("UPDATE sdc_session_timelines SET client_name = ? WHERE session_id = ?", (fixed, sid))
                            print(f"[database] Ligature fix sdc_timelines {sid}: {cname!r} -> {fixed!r}")
                            total_fixed += 1
        except Exception as e:
            print(f"[database] _clean_ligature_noise_from_names notice: {e}")
        if total_fixed:
            print(f"[database] Retrospective ligature cleanup: {total_fixed} name(s) fixed.")

    def _upgrade_client_name_if_placeholder(self, client_id: int, real_name: str) -> bool:
        """Promotes a scraped real taxpayer name into master.db if client currently has a placeholder name."""
        if not client_id or not real_name:
            return False
        clean_name = self._clean_name_retrofix(str(real_name).strip())
        if not clean_name or SKELETON_NAME_REGEX.search(clean_name):
            return False

        try:
            with self._connect() as m_conn:
                mcl = self.get_mcl_columns()
                # Find all name-like columns (company, proprietor, client, name)
                name_col_ids = []
                for c in mcl:
                    lbl = (c.get("label") or "").lower()
                    if any(k in lbl for k in ("company", "name", "client", "proprietor")) and "pass" not in lbl:
                        name_col_ids.append(c["id"])

                if not name_col_ids:
                    id_col = self.get_identity_column()
                    if id_col:
                        name_col_ids.append(id_col["id"])
                if not name_col_ids:
                    return False

                # Check which column currently holds a placeholder
                target_col_id = None
                curr_val = ""
                for col_id in name_col_ids:
                    cur = m_conn.execute(
                        "SELECT value FROM client_values WHERE client_id = ? AND column_id = ?",
                        (client_id, col_id)
                    )
                    row = cur.fetchone()
                    val = (row[0] or "").strip() if row else ""
                    if val and SKELETON_NAME_REGEX.search(val):
                        target_col_id = col_id
                        curr_val = val
                        break

                # If no column currently holds a placeholder, pick first empty name column
                if not target_col_id:
                    for col_id in name_col_ids:
                        cur = m_conn.execute(
                            "SELECT value FROM client_values WHERE client_id = ? AND column_id = ?",
                            (client_id, col_id)
                        )
                        row = cur.fetchone()
                        val = (row[0] or "").strip() if row else ""
                        if not val:
                            target_col_id = col_id
                            break

                if not target_col_id:
                    return False

                m_conn.execute(
                    """INSERT INTO client_values (client_id, column_id, value)
                       VALUES (?, ?, ?)
                       ON CONFLICT(client_id, column_id) DO UPDATE SET value = excluded.value""",
                    (client_id, target_col_id, clean_name)
                )
                print(f"[database] Promoted real taxpayer name for client #{client_id} (col #{target_col_id}): {curr_val!r} -> {clean_name!r}")
                self._bump_sync_revision_if_configured()
                return True
        except Exception as e:
            print(f"[database] _upgrade_client_name_if_placeholder notice: {e}")
        return False

    def upgrade_all_placeholder_client_names(self) -> int:
        """
        Scans master.db for clients with placeholder names (e.g. 'Client (ABCDE1234F)')
        and resolves their real names from rawPayload.db (sdc_session_timelines, client_raw_containers, tracker_dump).
        """
        upgraded = 0
        try:
            mcl = self.get_mcl_columns()
            pan_col_id = None
            for c in mcl:
                lbl = (c.get("label") or "").lower()
                if re.search(r'\bpan\b', lbl) and "pass" not in lbl:
                    pan_col_id = c["id"]
                    break

            if not pan_col_id:
                return 0

            with self._connect() as m_conn:
                cur = m_conn.execute(
                    """SELECT c.id, cv.column_id, mc.label, cv.value
                       FROM clients c
                       JOIN client_values cv ON cv.client_id = c.id
                       JOIN mcl_columns mc ON mc.id = cv.column_id
                       WHERE c.is_archived = 0"""
                )
                from collections import defaultdict
                client_records = defaultdict(dict)
                for cid, col_id, col_label, val in cur.fetchall():
                    client_records[cid][col_id] = (col_label, val or "")

                candidates = []
                for cid, cols in client_records.items():
                    pan_val = ""
                    placeholder_cols = []
                    for col_id, (label, val) in cols.items():
                        lbl = label.lower()
                        if col_id == pan_col_id or (re.search(r'\bpan\b', lbl) and "pass" not in lbl):
                            pan_val = val.strip().upper()
                        elif any(k in lbl for k in ("company", "name", "client", "proprietor")) and "pass" not in lbl:
                            if val and SKELETON_NAME_REGEX.search(val.strip()):
                                placeholder_cols.append((col_id, val.strip()))

                    if pan_val and placeholder_cols:
                        candidates.append((cid, pan_val, placeholder_cols))

            if not candidates:
                return 0

            with self._connect_raw() as r_conn:
                for cid, cpan, ph_cols in candidates:
                    real_name = None
                    # 1. Try sdc_session_timelines
                    cur = r_conn.execute(
                        """SELECT client_name FROM sdc_session_timelines
                           WHERE pan = ? AND client_name IS NOT NULL AND client_name != ''
                           ORDER BY start_time DESC LIMIT 1""",
                        (cpan,)
                    )
                    row = cur.fetchone()
                    if row and row[0] and not SKELETON_NAME_REGEX.search(row[0]):
                        real_name = row[0].strip()

                    # 2. Try client_raw_containers
                    if not real_name:
                        cur = r_conn.execute(
                            """SELECT company_name, proprietor_name FROM client_raw_containers
                               WHERE pan = ? LIMIT 1""",
                            (cpan,)
                        )
                        row = cur.fetchone()
                        if row:
                            co = (row[0] or "").strip()
                            prop = (row[1] or "").strip()
                            cand = co if (co and not SKELETON_NAME_REGEX.search(co)) else prop
                            if cand and not SKELETON_NAME_REGEX.search(cand):
                                real_name = cand

                    # 3. Try tracker_dump raw_payload_json
                    if not real_name:
                        cur = r_conn.execute(
                            """SELECT raw_payload_json FROM tracker_dump
                               WHERE (unassigned_identity = ? OR client_id = ?)
                               ORDER BY created_at DESC, gid DESC LIMIT 5""",
                            (cpan, cid)
                        )
                        for (r_json,) in cur.fetchall():
                            if r_json:
                                try:
                                    p_obj = json.loads(r_json)
                                    p_name = p_obj.get("client_name") or p_obj.get("name") or p_obj.get("taxpayer_name") or ""
                                    if p_name and not SKELETON_NAME_REGEX.search(p_name):
                                        real_name = p_name.strip()
                                        break
                                except Exception:
                                    pass

                    if real_name:
                        clean_real = self._clean_name_retrofix(real_name)
                        if clean_real and not SKELETON_NAME_REGEX.search(clean_real):
                            with self._connect() as m_conn:
                                for col_id, old_val in ph_cols:
                                    m_conn.execute(
                                        """UPDATE client_values SET value = ?
                                           WHERE client_id = ? AND column_id = ?""",
                                        (clean_real, cid, col_id)
                                    )
                                    print(f"[database] Upgraded client #{cid} ({cpan}) col #{col_id}: {old_val!r} -> {clean_real!r}")
                            upgraded += 1
            if upgraded:
                self._bump_sync_revision_if_configured()
        except Exception as e:
            print(f"[database] Notice during upgrade_all_placeholder_client_names: {e}")
        return upgraded

    def optimize_storage(self):
        """
        Performs SQLite WAL truncation and checkpoint maintenance on both master.db and rawPayload.db.
        Flushes all journaled WAL frames into the database files and truncates WAL logs to zero bytes.
        """
        try:
            with self._connect() as conn:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        except Exception as e:
            print(f"[database] master.db WAL checkpoint notice: {e}")

        try:
            with self._connect_raw() as conn:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        except Exception as e:
            print(f"[database] rawPayload.db WAL checkpoint notice: {e}")
