"""
sera_db/srpf.py - SRPF client containers and identity resolution for captures.

Split out of database.py; methods are unchanged. Mixed into SeraDatabase.
"""

import datetime
import json
import re
from functools import lru_cache
from typing import Optional


@lru_cache(maxsize=8)
def _profile_of_json(raw_payload_json: str) -> dict:
    """extract_profile_from_payload for a JSON string, remembered: one capture is profiled
    by both the container update and the client enrichment. Callers must not mutate it."""
    from ui.utils.profile_parser import extract_profile_from_payload
    return extract_profile_from_payload(raw_payload_json)


def _light_history(hist: list) -> list:
    """A filing history without the (large) raw payload of each filing."""
    return [{k: v for k, v in h.items() if k != "raw_payload_json"} for h in hist]


def _latest_payload_status(hist: list) -> Optional[str]:
    """The status the latest filing's payload states itself (what the tracker's status
    resolver looks for), or None."""
    if not hist:
        return None
    raw = hist[-1].get("raw_payload_json")
    try:
        obj = json.loads(raw) if isinstance(raw, str) and raw else raw
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    inner = obj.get("raw_payload") if isinstance(obj.get("raw_payload"), dict) else {}
    return obj.get("status") or inner.get("status") or None


class SrpfMixin:

    # ---------------- Tracker Dump Subsystem (VSDC & Extension) ----------------

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
                # SGT-I's advisory notes are never identity evidence (blueprint 14.2 rule 4)
                item = {k: v for k, v in item.items() if k != "sgt_i"}
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

        raw_for_profile = dump_row.get("raw_payload_json")
        new_profile = (_profile_of_json(raw_for_profile) if isinstance(raw_for_profile, str) and raw_for_profile
                       else extract_profile_from_payload(payload_obj))

        if existing:
            # existing schema: (identity_key, client_id, company_name, proprietor_name, pan, gstin, tan, phone, email, dob, user_id, portal_profiles, filing_history, raw_aggregates, total_captures, last_updated)
            cid = client_id or existing[1]
            comp = existing[2] or new_profile.get("company_name") or ""
            prop = existing[3] or new_profile.get("proprietor_name") or ""
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

            tot_caps = (existing[14] or 0) + 1
            existing_ts = str(existing[15] or "")
            final_last_updated = max(existing_ts, dump_ts) if existing_ts else dump_ts

            arn_str = dump_row.get("arn_number")
            status_val = dump_row.get("status") or ""
            payload_str = dump_row.get("raw_payload_json") or ""

            if arn_str:
                matched_idx = None
                for idx, h in enumerate(hist):
                    if h.get("arn") == arn_str:
                        matched_idx = idx
                        break
                if matched_idx is not None:
                    if status_val:
                        hist[matched_idx]["status"] = status_val
                    if payload_str:
                        hist[matched_idx]["raw_payload_json"] = payload_str
                    if dump_row.get("period_label"):
                        hist[matched_idx]["period_label"] = dump_row.get("period_label")
                    hist[matched_idx]["created_at"] = dump_ts
                else:
                    hist.append({
                        "portal": dump_row.get("portal"),
                        "arn": arn_str,
                        "period_label": dump_row.get("period_label"),
                        "capture_method": dump_row.get("capture_method"),
                        "status": status_val,
                        "raw_payload_json": payload_str,
                        "created_at": dump_ts
                    })
            elif dump_row.get("period_label"):
                hist.append({
                    "portal": dump_row.get("portal"),
                    "arn": "N/A",
                    "period_label": dump_row.get("period_label"),
                    "capture_method": dump_row.get("capture_method"),
                    "status": status_val,
                    "raw_payload_json": payload_str,
                    "created_at": dump_ts
                })

            hist.sort(key=lambda h: str(h.get("created_at") or ""))

            r_conn.execute("""
                UPDATE client_raw_containers
                SET client_id = ?, company_name = ?, proprietor_name = ?, pan = ?, gstin = ?, tan = ?, phone = ?, email = ?, dob = ?, user_id = ?, filing_history = ?, total_captures = ?, last_updated = ?
                WHERE identity_key = ?
            """, (cid, comp, prop, pan_val, gst_val, tan_val, ph_val, em_val, dob_val, uid_val, json.dumps(hist), tot_caps, final_last_updated, clean_key))
            self._write_container_light(r_conn, clean_key, hist, tot_caps, final_last_updated)
        else:
            hist = []
            arn_str = dump_row.get("arn_number")
            status_val = dump_row.get("status") or ""
            payload_str = dump_row.get("raw_payload_json") or ""
            if arn_str:
                hist.append({
                    "portal": dump_row.get("portal"),
                    "arn": arn_str,
                    "period_label": dump_row.get("period_label"),
                    "capture_method": dump_row.get("capture_method"),
                    "status": status_val,
                    "raw_payload_json": payload_str,
                    "created_at": dump_ts
                })
            elif dump_row.get("period_label"):
                hist.append({
                    "portal": dump_row.get("portal"),
                    "arn": "N/A",
                    "period_label": dump_row.get("period_label"),
                    "capture_method": dump_row.get("capture_method"),
                    "status": status_val,
                    "raw_payload_json": payload_str,
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
            self._write_container_light(r_conn, clean_key, hist, 1, dump_ts)

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

    def _fetch_clients_batch(self, m_conn, client_ids: set[int]) -> dict[int, dict]:
        """High-performance batch fetcher for multiple client records in a single query."""
        if not client_ids:
            return {}
            
        mcl_cols = self.get_mcl_columns()
        c_list = list(client_ids)
        placeholders = ",".join("?" for _ in c_list)
        
        # 1. Fetch client tokens and archived status
        cur = m_conn.execute(f"SELECT id, client_id_token FROM clients WHERE id IN ({placeholders})", c_list)
        client_tokens = {row[0]: row[1] for row in cur.fetchall()}
        
        # 2. Fetch all values in one batch
        cur = m_conn.execute(f"SELECT client_id, column_id, value FROM client_values WHERE client_id IN ({placeholders})", c_list)
        client_values = {}
        for cid, col_id, val in cur.fetchall():
            if cid not in client_values:
                client_values[cid] = {}
            client_values[cid][col_id] = val
            
        # 3. Assemble map
        client_map = {}
        for cid in client_ids:
            c_vals = client_values.get(cid, {})
            name_val = ""
            pan_val = ""
            gst_val = ""
            for col in mcl_cols:
                lbl = col.get("label", "").lower()
                val = str(c_vals.get(col["id"], "") or "").strip()
                if val and not name_val and any(k in lbl for k in ["name", "party", "client"]):
                    name_val = val
                elif val and not pan_val and re.search(r'\bpan\b', lbl) and "pass" not in lbl:
                    cleaned_pan = val.upper()
                    if len(cleaned_pan) == 10 and re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", cleaned_pan):
                        pan_val = cleaned_pan
                    elif len(cleaned_pan) == 15 and re.match(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]", cleaned_pan):
                        pan_val = cleaned_pan[2:12]
                elif val and not gst_val and any(k in lbl for k in ["gstin", "gst"]):
                    gst_val = val.upper()

            # If no direct PAN found, derive from GSTIN if available
            if not pan_val and gst_val and len(gst_val) >= 12:
                derived = gst_val[2:12]
                if re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", derived):
                    pan_val = derived

            token = client_tokens.get(cid, f"CLI-{cid:05d}")
            client_map[cid] = {
                "name": name_val or token,
                "pan": pan_val,
                "client_id_token": token,
                "is_unassigned": False
            }
        return client_map

    @staticmethod
    def _write_container_light(r_conn, identity_key: str, hist: list, total_captures: int, last_updated: str) -> None:
        """Mirrors what the tracker LIST needs from a container into client_raw_container_light.

        The list used to read these from client_raw_containers, but SQLite keeps a row's big
        filing_history column in overflow pages, and reading any column stored AFTER it
        (total_captures, last_updated, notes) means walking that whole overflow chain."""
        r_conn.execute(
            """INSERT INTO client_raw_container_light (identity_key, history_light, payload_status, total_captures, last_updated, notes)
               VALUES (?, ?, ?, ?, ?, (SELECT notes FROM client_raw_containers WHERE identity_key = ?))
               ON CONFLICT(identity_key) DO UPDATE SET history_light = excluded.history_light,
                   payload_status = excluded.payload_status, total_captures = excluded.total_captures,
                   last_updated = excluded.last_updated, notes = excluded.notes""",
            (identity_key, json.dumps(_light_history(hist)), _latest_payload_status(hist), total_captures, last_updated, identity_key),
        )

    def _backfill_container_light(self, r_conn) -> None:
        """Containers written before the light table existed (or restored from a backup that
        lacks it): build their light rows once from the full container."""
        todo = r_conn.execute(
            """SELECT c.identity_key, c.filing_history, c.total_captures, c.last_updated FROM client_raw_containers c
               WHERE NOT EXISTS (SELECT 1 FROM client_raw_container_light l WHERE l.identity_key = c.identity_key)"""
        ).fetchall()
        for key, raw, total, last_updated in todo:
            try:
                hist = json.loads(raw) if raw else []
            except Exception:
                hist = []
            self._write_container_light(r_conn, key, hist, total or 0, last_updated)

    def get_srpf_containers(self, limit: int = 200, search_query: str = None, slim: bool = False, identity_key: str = None) -> list[dict]:
        """SRPF Phase 1 Filtration: Returns grouped client containers from rawPayload.db.
        Each distinct client or unregistered entity is aggregated into exactly ONE container row.

        slim=True is for list views. The filings' raw payloads make up nearly all of a
        container's size, so a slim read takes the payload-free history_light column instead of
        filing_history (SQLite never reads the big column's pages) plus the latest payload's own
        status as "payload_status". Slim rows have raw_payload_json == "" and "_slim": True; fetch
        one container with identity_key=... (slim=False) when the full payloads are needed."""
        with self._connect_raw() as r_conn:
            # 1. Auto-sync tracker_dump records into client_raw_containers if needed
            cur = r_conn.execute("SELECT COUNT(*) FROM client_raw_containers")
            container_count = cur.fetchone()[0]
            if container_count == 0:
                self.re_resolve_all_tracker_dumps()
            if slim:
                self._backfill_container_light(r_conn)

            # 2. Query containers
            early = "c.identity_key, c.client_id, c.company_name, c.proprietor_name, c.pan, c.gstin, c.tan, c.phone, c.email, c.dob, c.user_id, c.portal_profiles"
            if slim:
                # nothing stored after filing_history is read from the main table (see _write_container_light)
                sql = (f"SELECT {early}, l.history_light, '{{}}', l.total_captures, l.last_updated, l.notes, l.payload_status "
                       "FROM client_raw_containers c JOIN client_raw_container_light l ON l.identity_key = c.identity_key WHERE 1=1")
                order = "l.last_updated"
            else:
                sql = (f"SELECT {early}, c.filing_history, c.raw_aggregates, c.total_captures, c.last_updated, c.notes, NULL "
                       "FROM client_raw_containers c WHERE 1=1")
                order = "c.last_updated"
            params = []
            if identity_key:
                sql += " AND c.identity_key = ?"
                params.append(identity_key)
            if search_query:
                q = f"%{search_query}%"
                sql += " AND (c.identity_key LIKE ? OR c.company_name LIKE ? OR c.proprietor_name LIKE ? OR c.pan LIKE ? OR c.gstin LIKE ? OR c.phone LIKE ? OR c.email LIKE ?)"
                params.extend([q, q, q, q, q, q, q])
            sql += f" ORDER BY {order} DESC LIMIT ?"
            params.append(limit)

            cur = r_conn.execute(sql, params)
            rows = cur.fetchall()

        if not rows:
            return []

        # 3. Enrich with master.db client info (Batch Optimized)
        client_map = {}
        with self._connect() as m_conn:
            unique_cids = {r[1] for r in rows if r[1]}
            if unique_cids:
                try:
                    client_map = self._fetch_clients_batch(m_conn, unique_cids)
                except Exception:
                    client_map = {}

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
            capture_method = filing_hist[-1].get("capture_method", "") if filing_hist else ""

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
                c_pan = c_info.get("pan") or pan_val or ""
                if len(c_pan) == 15 and re.match(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]", c_pan):
                    c_pan = c_pan[2:12]
                display_name = f"{c_info['name']} ({c_pan or identity_key})"
                display_pan = c_pan
                id_token = c_info.get("client_id_token", f"CLI-{cid:05d}")
            elif comp_name or prop_name:
                cand_pan = pan_val
                if (not cand_pan or len(cand_pan) != 10) and gst_val and len(gst_val) >= 12:
                    cand_pan = gst_val[2:12]
                chosen_nm = prop_name or comp_name
                display_name = f"{chosen_nm} ({cand_pan or gst_val or identity_key})"
                display_pan = cand_pan or gst_val or identity_key
                id_token = "Unregistered"
            else:
                display_name = f"Unregistered ({identity_key})"
                display_pan = identity_key
                id_token = "Unregistered"

            latest_status = filing_hist[-1].get("status", "") if filing_hist else ""
            latest_payload = filing_hist[-1].get("raw_payload_json", "") if filing_hist else ""
            payload_status = r[17] if len(r) > 17 else None

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
                "status": latest_status,
                "latest_status": latest_status,
                "raw_payload_json": latest_payload,
                "capture_method": capture_method,
                "total_captures": len(filing_hist) or r[14] or 1,
                "filing_history": filing_hist,
                "last_updated": r[15],
                "notes": r[16] if len(r) > 16 else "",
                "payload_status": payload_status,
                "_slim": slim,
            })

        # Default chronological entry organisation: latest entry at top
        containers.sort(key=lambda c: str(c.get("last_updated") or ""), reverse=True)
        return containers

    def delete_srpf_container(self, identity_key: str) -> bool:
        """Deletes a container and ONLY the captures that belong to it from rawPayload.db.

        A capture belongs to a container when it files under the same container key as
        _update_srpf_container: "CLI-<id>" for a registered client, otherwise the first
        identity candidate (or the unassigned identity). Captures of other containers that
        merely mention this identity in their payload (e.g. a GSTIN capture mentioning the
        PAN) are left alone.
        """
        if not identity_key:
            return False
        clean = str(identity_key).strip().upper()
        with self._connect_raw() as conn:
            conn.execute("DELETE FROM client_raw_container_light WHERE UPPER(identity_key) = ?", (clean,))
            conn.execute("DELETE FROM client_raw_containers WHERE UPPER(identity_key) = ?", (clean,))
            owned_ids = []
            m = re.match(r"^CLI-(\d+)$", clean)
            if m:
                owned_ids = [r[0] for r in conn.execute(
                    "SELECT id FROM tracker_dump WHERE client_id = ?", (int(m.group(1)),)
                ).fetchall()]
            else:
                m = re.match(r"^UNASSIGNED_(\d+)$", clean)
                if m:
                    owned_ids = [int(m.group(1))]
                else:
                    rows = conn.execute(
                        "SELECT id, unassigned_identity, arn_number, raw_payload_json FROM tracker_dump "
                        "WHERE client_id IS NULL OR client_id = 0"
                    ).fetchall()
                    for rid, unassigned, arn, payload in rows:
                        cands = self._extract_identity_candidates_from_payload(arn_number=arn, raw_payload_json=payload)
                        row_key = cands[0] if cands else str(unassigned or "").strip().upper()
                        if row_key == clean:
                            owned_ids.append(rid)
            for i in range(0, len(owned_ids), 500):
                chunk = owned_ids[i:i + 500]
                conn.execute(
                    f"DELETE FROM tracker_dump WHERE id IN ({','.join('?' for _ in chunk)})", chunk
                )
        return True

    def _resolve_session_proximity_candidate(self, portal: str, timestamp_str: str, max_seconds: int = 900, session_id: str = None) -> Optional[str]:
        """Resolves PAN/GSTIN candidate from the immediate most recent session capture (registered or unregistered)."""
        if not portal or not timestamp_str:
            return None
            
        base_portal = portal.split(" (")[0].strip().lower()
        
        try:
            import re
            from datetime import datetime
            t0 = datetime.fromisoformat(timestamp_str)
            with self._connect_raw() as r_conn:
                t_cols = {r[1] for r in r_conn.execute("PRAGMA table_info(tracker_dump)").fetchall()}
                order_sec = "gid DESC" if "gid" in t_cols else "id DESC"

                # 1. If session_id is provided, search specifically for it first
                if session_id:
                    cur = r_conn.execute(
                        f"SELECT arn_number, unassigned_identity, raw_payload_json, created_at, client_id FROM tracker_dump WHERE raw_payload_json LIKE ? ORDER BY created_at DESC, {order_sec}, id DESC LIMIT 50",
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

                # 2. Fallback to time-based proximity matching (ordered by (created_at, gid) descending)
                cur = r_conn.execute(
                    f"""SELECT arn_number, unassigned_identity, raw_payload_json, created_at, portal, client_id 
                       FROM tracker_dump 
                       WHERE created_at IS NOT NULL 
                       ORDER BY created_at DESC, {order_sec}, id DESC LIMIT 100"""
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

    def get_srpf_container_media(self, identity_key: str) -> dict:
        with self._connect_raw() as conn:
            cur = conn.execute("SELECT notes, screenshot_path FROM client_raw_containers WHERE identity_key = ?", (identity_key,))
            row = cur.fetchone()
            if row:
                return {"notes": row[0] or "", "screenshot_path": row[1] or ""}
            return {"notes": "", "screenshot_path": ""}

    def save_srpf_container_media(self, identity_key: str, notes: str, screenshot_path: str) -> bool:
        with self._connect_raw() as conn:
            cur = conn.execute("UPDATE client_raw_containers SET notes = ?, screenshot_path = ? WHERE identity_key = ?", (notes, screenshot_path, identity_key))
            conn.execute("UPDATE client_raw_container_light SET notes = ? WHERE identity_key = ?", (notes, identity_key))
            return cur.rowcount > 0

    # ---------------- Capture -> MCL mapping (client record enrichment) ----------------

    _MCL_MAPPING_SETTING = "tracker_mcl_mappings"

    @staticmethod
    def _flatten_payload_leaves(payload, depth: int = 6) -> list[tuple[str, str]]:
        """Returns [(leaf_key_lower, value)] for every scalar in a payload (sgt_i notes excluded)."""
        out: list[tuple[str, str]] = []

        def walk(item, d):
            if d <= 0 or item is None:
                return
            if isinstance(item, dict):
                for k, v in item.items():
                    if k == "sgt_i":
                        continue
                    if isinstance(v, (dict, list)):
                        walk(v, d - 1)
                    elif isinstance(v, (str, int, float)) and not isinstance(v, bool):
                        sv = str(v).strip()
                        if sv and sv.lower() not in ("null", "none", "undefined", "n/a"):
                            out.append((str(k).strip().lower(), sv))
            elif isinstance(item, list):
                for v in item:
                    walk(v, d - 1)

        walk(payload, depth)
        return out

    @staticmethod
    def _payload_to_obj(raw_payload_json):
        if isinstance(raw_payload_json, dict):
            return raw_payload_json
        try:
            obj = json.loads(raw_payload_json) if raw_payload_json else {}
        except Exception:
            return {}
        return obj if isinstance(obj, (dict, list)) else {}

    def get_capture_mcl_mappings(self) -> list[dict]:
        """User-defined mappings [{"source": <captured datapoint key>, "column_id": <MCL column id>}]."""
        try:
            data = json.loads(self.get_setting(self._MCL_MAPPING_SETTING, "") or "[]")
        except Exception:
            return []
        result = []
        for m in data if isinstance(data, list) else []:
            if isinstance(m, dict) and m.get("source") and isinstance(m.get("column_id"), int):
                result.append({"source": str(m["source"]).strip().lower(), "column_id": m["column_id"]})
        return result

    def save_capture_mcl_mappings(self, mappings: list[dict]) -> None:
        clean, seen = [], set()
        for m in mappings or []:
            src = str(m.get("source") or "").strip().lower()
            cid = m.get("column_id")
            if src and isinstance(cid, int) and (src, cid) not in seen:
                seen.add((src, cid))
                clean.append({"source": src, "column_id": cid})
        self.set_setting(self._MCL_MAPPING_SETTING, json.dumps(clean))

    def get_capture_datapoints(self, limit: int = 300, auto_limit: int = 40) -> list[dict]:
        """Lists the datapoints (leaf keys) seen in recent captures for the mapping dialog.

        Each entry: {"key", "count", "sample", "auto_column_id"} where auto_column_id is the MCL
        column the built-in auto-mapper already sends this datapoint's value to (or None).
        """
        from ui.utils.profile_parser import extract_profile_from_payload, map_profile_to_mcl_columns
        mcl_cols = self.get_mcl_columns()
        with self._connect_raw() as conn:
            rows = conn.execute(
                "SELECT raw_payload_json FROM tracker_dump WHERE raw_payload_json IS NOT NULL "
                "ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        points: dict[str, dict] = {}
        for n, (raw,) in enumerate(rows):
            payload = self._payload_to_obj(raw)
            if not payload:
                continue
            auto_by_value = {}
            # the profile parser is the costly part: only the newest payloads are probed for
            # "auto-mapped to"; every payload still counts towards the key statistics
            if n < auto_limit:
                try:
                    mapped = map_profile_to_mcl_columns(extract_profile_from_payload(payload), mcl_cols)
                    auto_by_value = {str(v).strip(): c for c, v in mapped.items() if str(v).strip()}
                except Exception:
                    pass
            for key, val in self._flatten_payload_leaves(payload):
                if len(val) > 200:
                    continue
                pt = points.setdefault(key, {"key": key, "count": 0, "sample": val, "auto_column_id": None})
                pt["count"] += 1
                if pt["auto_column_id"] is None and val in auto_by_value:
                    pt["auto_column_id"] = auto_by_value[val]
        return sorted(points.values(), key=lambda p: (-p["count"], p["key"]))

    def _build_client_enrichment(self, client_id: int, payload, raw_json: Optional[str] = None) -> dict[int, str]:
        """Safe MCL changes for one registered client from one capture payload: fills blank
        columns, or improves an incomplete name. User mappings win over the auto-mapper."""
        from tracker_dump_parser.mcl_enricher import build_mcl_updates
        from ui.utils.profile_parser import extract_profile_from_payload
        mcl_cols = self.get_mcl_columns()
        with self._connect() as m_conn:
            client = self._fetch_client_full(m_conn, client_id)
        if not client:
            return {}
        existing = client.get("values", {})
        id_col_ids = {c["id"] for c in mcl_cols if c.get("field_type") == "id"}

        updates: dict[int, str] = {}
        mappings = self.get_capture_mcl_mappings()
        if mappings:
            leaves = self._flatten_payload_leaves(payload)
            for m in mappings:
                col_id = m["column_id"]
                if col_id in updates or col_id in id_col_ids:
                    continue
                if str(existing.get(col_id) or "").strip():
                    continue
                for key, val in leaves:
                    if key == m["source"]:
                        updates[col_id] = val
                        break

        label_by_id = {c["id"]: (c.get("label") or "").lower() for c in mcl_cols}
        profile = _profile_of_json(raw_json) if isinstance(raw_json, str) and raw_json else extract_profile_from_payload(payload)
        for col_id, val in build_mcl_updates(mcl_cols, existing, [profile]).items():
            lbl = label_by_id.get(col_id, "")
            # a generic "...date..." column is not a date-of-birth column
            if "date" in lbl and not any(t in lbl for t in ("dob", "birth", "incorp")):
                continue
            current = str(existing.get(col_id) or "").strip()
            if current:
                # an existing name is only completed (e.g. "Simran" -> "Simran Kaur"), never replaced
                cur_tokens = set(re.findall(r"[a-z0-9]+", current.lower()))
                if not cur_tokens <= set(re.findall(r"[a-z0-9]+", val.lower())):
                    continue
            updates.setdefault(col_id, val)
        return {c: v for c, v in updates.items() if c not in id_col_ids}

    def enrich_client_from_capture(self, client_id: int, raw_payload_json) -> dict[int, str]:
        """Writes newly captured details of an already registered client into its MCL record."""
        if not client_id:
            return {}
        payload = self._payload_to_obj(raw_payload_json)
        if not payload:
            return {}
        updates = self._build_client_enrichment(client_id, payload, raw_payload_json)
        if not updates:
            return {}
        for col_id, val in updates.items():
            self.update_client_single_field(client_id, col_id, val, log_action=False)
        labels = {c["id"]: c.get("label") for c in self.get_mcl_columns()}
        try:
            self.log_action(
                actor="System", action="update", client_id=client_id,
                detail="Updated from capture: " + ", ".join(str(labels.get(c, c)) for c in updates),
            )
        except Exception:
            pass
        self._bump_sync_revision_if_configured()
        return updates

    def enrich_registered_clients_from_containers(self) -> int:
        """Retrospective pass: applies every registered client's captured payloads to its MCL
        record (oldest first, so the newest capture's improvements win). Returns clients changed."""
        with self._connect_raw() as conn:
            rows = conn.execute(
                "SELECT client_id, raw_payload_json FROM tracker_dump "
                "WHERE client_id IS NOT NULL AND client_id != 0 AND raw_payload_json IS NOT NULL "
                "ORDER BY created_at"
            ).fetchall()
        changed = set()
        for cid, raw in rows:
            try:
                if self.enrich_client_from_capture(cid, raw):
                    changed.add(cid)
            except Exception:
                pass
        return len(changed)
