"""
sera_db/tracker_dump.py - Tracker Dump captures: insert, de-duplicate, peers, re-resolve, read, delete.

Split out of database.py; methods are unchanged. Mixed into SeraDatabase.
"""

import datetime
import json
import time
from core.dataset_key import NOT_CARRIER_SQL, compute_dataset_key as _compute_dataset_key

from sera_db.common import SKELETON_NAME_REGEX


class TrackerDumpMixin:

    # The canonical key lives in core/dataset_key.py so SGT (live) builds exactly the same one.
    compute_dataset_key = staticmethod(_compute_dataset_key)

    def delete_sgt_rows_by_dataset_key(self, dataset_key: str) -> int:
        """Removes SGT's own row(s) for a dataset key it has superseded. Only rows SGT wrote
        (capture method "SGT...") are touched, so another engine's row under the same key -
        possible now that live SGT rows share the canonical keys - is never deleted."""
        if not dataset_key:
            return 0
        with self._connect_raw() as r_conn:
            cur = r_conn.execute(
                "DELETE FROM tracker_dump WHERE dataset_key = ? AND capture_method LIKE 'SGT%'", (dataset_key,))
            return cur.rowcount or 0

    @staticmethod
    def _convert_sgt_shadow_rows(conn) -> int:
        """
        SGT went live (2026-09-26): the rows it wrote in shadow mode become ordinary live rows.
        Each takes the canonical key (core/dataset_key.py) and merges with whatever row already
        holds that key - another engine's or another SGT row. The row with the higher submit
        status wins (the newer one on a tie), and the winner keeps the loser's ARN if it has
        none. Idempotent: once no "SGT_shadow" row is left it does nothing.
        """
        from core.vsdc.vsdc_assembler import get_status_rank
        rows = conn.execute(
            "SELECT id, portal, period_label, arn_number, status, raw_payload_json, created_at, dataset_key, unassigned_identity "
            "FROM tracker_dump WHERE capture_method = 'SGT_shadow' ORDER BY created_at, id").fetchall()
        converted = 0
        for r_id, r_port, r_period, r_arn, r_status, r_json, r_created, r_key, r_unassigned in rows:
            if conn.execute("SELECT 1 FROM tracker_dump WHERE id = ?", (r_id,)).fetchone() is None:
                continue                        # lost a merge to an earlier row in this loop
            try:
                p = json.loads(r_json) if r_json else {}
            except Exception:
                p = {}
            if not isinstance(p, dict):
                p = {}
            raw = p.get("raw_payload") if isinstance(p.get("raw_payload"), dict) else {}
            ident = p.get("gstin") or p.get("pan")
            if not ident:
                # Written before the client was known: keep it apart per SGT session, as live does.
                old = str(r_key or "").split(":")
                ident = old[2] if len(old) >= 5 and old[0] == "SGT" else (r_unassigned or "UNKNOWN")
            form = p.get("filing_type") or ""
            period = r_period or ""
            if not period and r_arn and r_arn != "N/A":
                period = f"ARN {r_arn}"
            key = _compute_dataset_key(r_port, ident, form, period)

            winner = (r_id, r_status, r_arn, r_created)
            losers = []
            for o_id, o_status, o_arn, o_created in conn.execute(
                    "SELECT id, status, arn_number, created_at FROM tracker_dump WHERE dataset_key = ? AND id != ?",
                    (key, r_id)).fetchall():
                challenger = (o_id, o_status, o_arn, o_created)
                if (get_status_rank(o_status), str(o_created or "")) > (get_status_rank(winner[1]), str(winner[3] or "")):
                    losers.append(winner)
                    winner = challenger
                else:
                    losers.append(challenger)
            arn = winner[2]
            if not arn or arn == "N/A":
                arn = next((l[2] for l in losers if l[2] and l[2] != "N/A"), arn)
            for l in losers:
                conn.execute("DELETE FROM tracker_dump WHERE id = ?", (l[0],))
            if winner[0] == r_id:
                p["capture_method"] = "SGT_live"
                p["dataset_key"] = key
                p.pop("supersedes_dataset_key", None)
                if raw:
                    raw["dataset_key"] = key
                    if isinstance(raw.get("source"), dict):
                        raw["source"]["mode"] = "live"
                conn.execute(
                    "UPDATE tracker_dump SET capture_method = 'SGT_live', dataset_key = ?, arn_number = ?, raw_payload_json = ? WHERE id = ?",
                    (key, arn, json.dumps(p, ensure_ascii=False), r_id))
            elif arn != winner[2]:
                conn.execute("UPDATE tracker_dump SET arn_number = ? WHERE id = ?", (arn, winner[0]))
            converted += 1
        if converted:
            print(f"[SGT] {converted} shadow-mode tracker row(s) converted to live rows")
        return converted

    def insert_tracker_dump(self, client_id: int = None, service_id: int = None, portal: str = None,
                            period_label: str = None, arn_number: str = None,
                            capture_method: str = "DOM_Tracker", status: str = "submitted",
                            raw_payload_json: str = None, captured_by: str = "System",
                            pan: str = None, session_id: str = None, filing_type: str = None,
                            dataset_key: str = None) -> dict:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        candidates = self._extract_identity_candidates_from_payload(arn_number=arn_number, pan=pan, raw_payload_json=raw_payload_json)

        # If no identity found in wizard payload, attempt proximity resolution from same portal session.
        # Never for SGT: a row it sends without a PAN is one whose client it has NOT identified,
        # and "whoever was captured on this portal in the last 15 minutes" is how a filing lands
        # on the previous client. SGT re-sends the row under its client once it reads the PAN.
        if not candidates and portal and not str(capture_method or "").startswith("SGT"):
            prox_cand = self._resolve_session_proximity_candidate(portal, now, max_seconds=900, session_id=session_id)
            if prox_cand:
                candidates = [prox_cand]

        valid_id = None
        valid_gid = None
        unassigned_identity = None

        # 1. Authoritative Match: Resolve identity against master.db vault
        with self._connect() as m_conn:
            m_cols = {r[1] for r in m_conn.execute("PRAGMA table_info(clients)").fetchall()}
            has_gid = "gid" in m_cols
            gid_col = ", c.gid" if has_gid else ""
            for cand in candidates:
                cand_clean = cand.strip().upper()
                row = m_conn.execute(
                    f"""SELECT cv.client_id{gid_col} FROM client_values cv
                       JOIN clients c ON c.id = cv.client_id
                       WHERE c.is_archived = 0 AND UPPER(TRIM(cv.value)) = ?
                       LIMIT 1""",
                    (cand_clean,)
                ).fetchone()
                if row:
                    valid_id = row[0]
                    if has_gid and len(row) > 1:
                        valid_gid = row[1]
                    break

            if not valid_id:
                if candidates:
                    unassigned_identity = candidates[0]
                elif client_id:
                    c_gid_col = ", gid" if has_gid else ""
                    row = m_conn.execute(
                        f"SELECT id{c_gid_col} FROM clients WHERE id = ? AND is_archived = 0", (client_id,)
                    ).fetchone()
                    if row:
                        valid_id = row[0]
                        if has_gid and len(row) > 1:
                            valid_gid = row[1]
                if not valid_id and not unassigned_identity:
                    unassigned_identity = f"Pending_{arn_number}" if arn_number and arn_number != "N/A" else "Unassigned"

        # Resolve incoming dataset key components
        page_url_norm = None
        incoming_form = filing_type or ""
        incoming_pref = ""
        status_correction = False
        cli_key = f"CLI_{valid_gid}" if valid_gid else (f"CLI_{valid_id}" if valid_id else None)
        taxpayer_id = pan or unassigned_identity or (candidates[0] if candidates else None) or cli_key
        
        if raw_payload_json:
            try:
                p_obj = json.loads(raw_payload_json) if isinstance(raw_payload_json, str) else raw_payload_json
                if isinstance(p_obj, dict):
                    raw_p = p_obj.get("raw_payload") if isinstance(p_obj.get("raw_payload"), dict) else {}
                    dataset_key = dataset_key or p_obj.get("dataset_key") or raw_p.get("dataset_key")
                    incoming_form = p_obj.get("filing_type") or raw_p.get("filing_type") or incoming_form
                    incoming_pref = p_obj.get("filing_preference") or raw_p.get("filing_preference") or ""
                    status_correction = bool(p_obj.get("status_correction"))
                    taxpayer_id = p_obj.get("gstin") or p_obj.get("pan") or raw_p.get("gstin") or raw_p.get("pan") or taxpayer_id
                    page_url = p_obj.get("page_key") or p_obj.get("url") or raw_p.get("page_key") or raw_p.get("url")
                    if page_url and isinstance(page_url, str):
                        page_url_norm = page_url.strip().split("?")[0].rstrip("/").lower()
                    if valid_id:
                        cand_name = (
                            p_obj.get("client_name") or p_obj.get("name") or p_obj.get("taxpayer_name") or
                            raw_p.get("client_name") or raw_p.get("taxpayer_name") or raw_p.get("name") or ""
                        )
                        if cand_name and not SKELETON_NAME_REGEX.search(cand_name):
                            self._upgrade_client_name_if_placeholder(valid_id, cand_name)
            except Exception:
                page_url_norm = None

        final_dataset_key = dataset_key
        if not final_dataset_key or final_dataset_key.count(":") < 3 or "\n" in final_dataset_key:
            final_dataset_key = self.compute_dataset_key(
                portal=portal,
                identifier=taxpayer_id,
                form_type=incoming_form or filing_type,
                period_label=period_label,
                preference=incoming_pref
            )

        # 2. Write capture to rawPayload.db and update SRPF container
        is_replaced = False
        # SGT in SHADOW mode keeps its own rows. Every clean-up below - pending placeholders,
        # burst duplicates, draft supersession - only ever looks at rows of the SAME side, so a
        # shadow row can never replace, drop or purge another engine's capture and no other
        # engine can remove one. LIVE SGT rows (capture method "SGT_live", canonical keys) are
        # an ordinary engine's rows and merge with everyone else's.
        is_sgt_shadow = str(capture_method or "").startswith("SGT_shadow")
        same_engine = ("capture_method LIKE 'SGT_shadow%'" if is_sgt_shadow
                       else "(capture_method IS NULL OR capture_method NOT LIKE 'SGT_shadow%')")
        # SGT re-sends a row only when that dataset really changed (e.g. its status climbed
        # seconds after the ARN was read), under a stable key that already replaces the old
        # row - the 10-second ARN burst guard would throw exactly that update away.
        burst_guard = not str(capture_method or "").startswith("SGT")
        with self._connect_raw() as r_conn:
            td_cols = {r[1] for r in r_conn.execute("PRAGMA table_info(tracker_dump)").fetchall()}
            order_sec = "gid DESC" if "gid" in td_cols else "id DESC"

            # An unattributed capture (VSDC247 saw an ARN before it knew the client) is stored
            # as "Pending_<ARN>". When the SAME ARN arrives again with an identity it replaces
            # that placeholder - it must neither be dropped as a duplicate by the check below
            # nor leave the placeholder behind as a second row for one filing.
            incoming_resolved = bool(valid_id) or bool(
                unassigned_identity and not str(unassigned_identity).startswith("Pending_")
                and unassigned_identity != "Unassigned"
            )
            if arn_number and arn_number != "N/A" and incoming_resolved:
                r_conn.execute(
                    f"DELETE FROM tracker_dump WHERE unassigned_identity = ? AND arn_number = ? AND {same_engine}",
                    (f"Pending_{arn_number}", arn_number),
                )

            # Deduplication Check (for immediate identical bursts within 10s)
            if burst_guard and arn_number and arn_number != "N/A":
                cur = r_conn.execute(
                    f"SELECT id, client_id FROM tracker_dump WHERE arn_number = ? AND created_at >= ? AND {same_engine}",
                    (arn_number, (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=10)).isoformat())
                )
                row = cur.fetchone()
                if row:
                    return {
                        "id": row[0], "client_id": row[1], "service_id": service_id,
                        "portal": portal, "period_label": period_label, "arn_number": arn_number,
                        "capture_method": capture_method, "status": status, "created_at": now, "duplicate": True
                    }

            # Exact Dataset Matching: Unconditionally purge ALL older duplicates for this dataset_key
            # Monotonicity Guard: A submitted status must NEVER demote back to 'Not Submitted'!
            if final_dataset_key and not final_dataset_key.endswith(":UNKNOWN:FORM:CURRENT"):
                ex_row = r_conn.execute(
                    f"SELECT status, arn_number FROM tracker_dump WHERE dataset_key = ? ORDER BY created_at DESC, {order_sec}, id DESC LIMIT 1",
                    (final_dataset_key,)
                ).fetchone()
                if ex_row:
                    ex_status, ex_arn = ex_row
                    try:
                        from core.vsdc.vsdc_assembler import get_status_rank
                        if get_status_rank(ex_status) > get_status_rank(status) and not status_correction:
                            status = ex_status
                            if (not arn_number or arn_number == "N/A") and ex_arn and ex_arn != "N/A":
                                arn_number = ex_arn
                    except Exception:
                        pass
                del_cur = r_conn.execute("DELETE FROM tracker_dump WHERE dataset_key = ?", (final_dataset_key,))
                if del_cur.rowcount > 0:
                    is_replaced = True

            # Unsubmitted Draft Supersession Guard:
            # Only ONE unsubmitted draft can exist per (assessee, portal, period).
            # If an unsubmitted draft arrives, check if a submitted return already exists
            # for this assessee and period (if so, promote to submitted to avoid regressing).
            # Otherwise, purge any older unsubmitted draft rows for this assessee and period.
            if period_label:
                assessee_conds = []
                assessee_params = [portal, period_label]
                if valid_id:
                    assessee_conds.append("client_id = ?")
                    assessee_params.append(valid_id)
                if taxpayer_id:
                    assessee_conds.append("unassigned_identity = ?")
                    assessee_params.append(taxpayer_id)
                    assessee_conds.append("dataset_key LIKE ?")
                    assessee_params.append(f"%:{taxpayer_id}:%")

                if assessee_conds:
                    is_draft = status in ("Not Submitted", "Draft", "Visited / In Progress", "Draft / Personal Info", "Form Selected", "Pending")
                    if is_draft:
                        # Check if already submitted under any form for this period
                        chk_sql = f"""
                            SELECT id, client_id, status, arn_number, dataset_key FROM tracker_dump
                            WHERE portal = ?
                              AND period_label = ?
                              AND status NOT IN ('Not Submitted', 'Draft', '', 'Pending', 'Visited / In Progress', 'Draft / Personal Info', 'Form Selected')
                              AND status IS NOT NULL
                              AND ({' OR '.join(assessee_conds)})
                              AND {same_engine}
                            ORDER BY created_at DESC, {order_sec}, id DESC LIMIT 1
                        """
                        filed_row = r_conn.execute(chk_sql, assessee_params).fetchone()
                        if filed_row:
                            # Monotonicity Guard: Return is ALREADY filed for this assessee & period!
                            # Do not insert an unsubmitted draft alongside an already completed return.
                            return {
                                "id": filed_row[0], "client_id": filed_row[1], "service_id": service_id,
                                "portal": portal, "period_label": period_label, "arn_number": filed_row[3],
                                "capture_method": capture_method, "status": filed_row[2], "created_at": now,
                                "duplicate": True, "dataset_key": filed_row[4]
                            }

                    # Purge prior unsubmitted drafts for this assessee & period
                    purge_sql = f"""
                        DELETE FROM tracker_dump
                        WHERE portal = ?
                          AND period_label = ?
                          AND (status IN ('Not Submitted', 'Draft', 'Visited / In Progress', 'Draft / Personal Info', 'Form Selected', 'Pending') OR status IS NULL OR status = '')
                          AND ({' OR '.join(assessee_conds)})
                          AND {same_engine}
                    """
                    del_unsub = r_conn.execute(purge_sql, assessee_params)
                    if del_unsub.rowcount > 0:
                        is_replaced = True

            # Fallback: URL check if dataset_key was insufficient on legacy rows
            if not is_replaced and page_url_norm and (capture_method == "DOM_Tracker" or capture_method.startswith("SDC_")):
                id_clauses = []
                id_vals = []
                if valid_id:
                    id_clauses.append("client_id = ?")
                    id_vals.append(valid_id)
                if unassigned_identity:
                    id_clauses.append("unassigned_identity = ?")
                    id_vals.append(unassigned_identity)
                if id_clauses:
                    url_sql = (f"SELECT id, raw_payload_json FROM tracker_dump WHERE ({' OR '.join(id_clauses)}) "
                               f"AND {same_engine} ORDER BY created_at DESC, {order_sec}, id DESC")
                    cur = r_conn.execute(url_sql, id_vals)
                    for r_id, r_json in cur.fetchall():
                        if r_json:
                            try:
                                c_obj = json.loads(r_json)
                                c_raw = c_obj.get("raw_payload") if isinstance(c_obj.get("raw_payload"), dict) else {}
                                c_url = c_obj.get("page_key") or c_obj.get("url") or c_raw.get("page_key") or c_raw.get("url")
                                if c_url and c_url.strip().split("?")[0].rstrip("/").lower() == page_url_norm:
                                    r_conn.execute("DELETE FROM tracker_dump WHERE id = ?", (r_id,))
                                    is_replaced = True
                                    break
                            except Exception:
                                pass

            cur = r_conn.execute(
                """INSERT INTO tracker_dump
                   (client_id, unassigned_identity, service_id, portal, period_label, arn_number, capture_method, status, raw_payload_json, captured_by, created_at, dataset_key)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (valid_id, unassigned_identity, service_id, portal, period_label, arn_number, capture_method, status, raw_payload_json, captured_by, now, final_dataset_key)
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
                    "status": status,
                    "raw_payload_json": raw_payload_json,
                    "captured_by": captured_by,
                    "created_at": now
                }
            )

        # A capture for an already registered client may carry details its MCL record lacks
        if valid_id and raw_payload_json:
            try:
                self.enrich_client_from_capture(valid_id, raw_payload_json)
            except Exception as e:
                print(f"[database] MCL enrichment from capture notice: {e}")

        self._bump_sync_revision_if_configured()

        return {
            "id": dump_id, "client_id": valid_id, "unassigned_identity": unassigned_identity, "service_id": service_id,
            "portal": portal, "period_label": period_label, "arn_number": arn_number,
            "capture_method": capture_method, "status": status, "created_at": now, "replaced": is_replaced,
            "dataset_key": final_dataset_key
        }

    def deduplicate_tracker_dumps(self) -> int:
        """
        Cleans up duplicate tracker_dump records in rawPayload.db, retaining
        only the most recent record per dataset_key or identical capture burst.
        Returns the number of deleted duplicate rows.
        """
        deleted_count = 0
        with self._connect_raw() as r_conn:
            cols = {r[1] for r in r_conn.execute("PRAGMA table_info(tracker_dump)").fetchall()}
            order_sec = "gid DESC" if "gid" in cols else "id DESC"

            # 1. Deduplicate by non-empty dataset_key (keep newest by created_at, gid)
            cur = r_conn.execute(f"""
                DELETE FROM tracker_dump
                WHERE rowid NOT IN (
                    SELECT rowid FROM (
                        SELECT rowid, ROW_NUMBER() OVER (
                            PARTITION BY dataset_key
                            ORDER BY created_at DESC, {order_sec}, id DESC
                        ) AS rn
                        FROM tracker_dump
                        WHERE dataset_key IS NOT NULL AND dataset_key != '' AND dataset_key NOT LIKE '%:UNKNOWN:FORM:CURRENT'
                    ) WHERE rn = 1
                )
                AND dataset_key IS NOT NULL AND dataset_key != '' AND dataset_key NOT LIKE '%:UNKNOWN:FORM:CURRENT'
            """)
            deleted_count += cur.rowcount

            # 2. Deduplicate exact duplicates by non-empty ARN
            cur2 = r_conn.execute(f"""
                DELETE FROM tracker_dump
                WHERE rowid NOT IN (
                    SELECT rowid FROM (
                        SELECT rowid, ROW_NUMBER() OVER (
                            PARTITION BY portal, arn_number
                            ORDER BY created_at DESC, {order_sec}, id DESC
                        ) AS rn
                        FROM tracker_dump
                        WHERE arn_number IS NOT NULL AND arn_number != '' AND arn_number != 'N/A'
                    ) WHERE rn = 1
                )
                AND arn_number IS NOT NULL AND arn_number != '' AND arn_number != 'N/A'
            """)
            deleted_count += cur2.rowcount

            # 3. Deduplicate exact duplicate bursts by (captured_by, created_at)
            cur3 = r_conn.execute(f"""
                DELETE FROM tracker_dump
                WHERE rowid NOT IN (
                    SELECT rowid FROM (
                        SELECT rowid, ROW_NUMBER() OVER (
                            PARTITION BY captured_by, created_at
                            ORDER BY created_at DESC, {order_sec}, id DESC
                        ) AS rn
                        FROM tracker_dump
                    ) WHERE rn = 1
                )
            """)
            deleted_count += cur3.rowcount

            # 4. Deduplicate unsubmitted drafts: Keep newest per (portal, period_label, client)
            cur4 = r_conn.execute(f"""
                DELETE FROM tracker_dump
                WHERE (status = 'Not Submitted' OR status IS NULL OR status = '' OR status = 'Pending')
                  AND rowid NOT IN (
                      SELECT rowid FROM (
                          SELECT rowid, ROW_NUMBER() OVER (
                              PARTITION BY portal, period_label, COALESCE(client_id, unassigned_identity)
                              ORDER BY created_at DESC, {order_sec}, id DESC
                          ) AS rn
                          FROM tracker_dump
                          WHERE (status = 'Not Submitted' OR status IS NULL OR status = '' OR status = 'Pending')
                      ) WHERE rn = 1
                  )
            """)
            deleted_count += cur4.rowcount

            # 5. Purge unsubmitted drafts if a submitted return already exists for that assessee + period
            cur5 = r_conn.execute("""
                DELETE FROM tracker_dump
                WHERE (status = 'Not Submitted' OR status IS NULL OR status = '' OR status = 'Pending')
                  AND EXISTS (
                      SELECT 1 FROM tracker_dump sub
                      WHERE sub.portal = tracker_dump.portal
                        AND sub.period_label = tracker_dump.period_label
                        AND COALESCE(sub.client_id, sub.unassigned_identity) = COALESCE(tracker_dump.client_id, tracker_dump.unassigned_identity)
                        AND sub.status NOT IN ('Not Submitted', '', 'Pending')
                        AND sub.status IS NOT NULL
                  )
            """)
            deleted_count += cur5.rowcount

        if deleted_count > 0:
            print(f"[database] deduplicate_tracker_dumps: Purged {deleted_count} duplicate tracker dump row(s).")
        return deleted_count

    def store_peer_tracker_dumps(self, dumps: list[dict]):
        """
        Receives pushed tracker_dump records from a peer workstation (via LAN sync)
        and inserts them into the local rawPayload.db, deduplicating by dataset_key,
        ARN, or capture timestamps and updating SRPF unified containers.
        """
        if not dumps:
            return
        
        inserted_any = False
        with self._connect_raw() as r_conn:
            for d in dumps:
                captured_by = d.get("captured_by")
                created_at = d.get("created_at")
                arn_number = (d.get("arn_number") or "").strip()
                portal = (d.get("portal") or "").strip()
                dataset_key = (d.get("dataset_key") or "").strip()

                # 1. Dataset Key Deduplication:
                # If dataset_key is present and specific, replace older duplicates or skip if incoming is older
                if dataset_key and not dataset_key.endswith(":UNKNOWN:FORM:CURRENT"):
                    td_cols = {r[1] for r in r_conn.execute("PRAGMA table_info(tracker_dump)").fetchall()}
                    order_sec = "gid DESC" if "gid" in td_cols else "id DESC"
                    cur = r_conn.execute(f"SELECT id, created_at FROM tracker_dump WHERE dataset_key = ? ORDER BY created_at DESC, {order_sec}, id DESC", (dataset_key,))
                    existing = cur.fetchall()
                    if existing:
                        existing_newest_created = existing[0][1] or ""
                        # If incoming record is newer or same timestamp, purge older duplicates and replace
                        if str(created_at or "") >= str(existing_newest_created):
                            r_conn.execute("DELETE FROM tracker_dump WHERE dataset_key = ?", (dataset_key,))
                        else:
                            # Incoming snapshot is older than what we already have
                            continue
                elif arn_number and arn_number != "N/A":
                    # 2. Duplicate check by (portal, arn_number) or (captured_by, created_at)
                    cur = r_conn.execute(
                        "SELECT id FROM tracker_dump WHERE (portal = ? AND arn_number = ?) OR (captured_by = ? AND created_at = ?)",
                        (portal, arn_number, captured_by, created_at)
                    )
                    if cur.fetchone():
                        continue
                else:
                    # 3. Fallback duplicate check by exact (captured_by, created_at)
                    cur = r_conn.execute(
                        "SELECT id FROM tracker_dump WHERE captured_by = ? AND created_at = ?",
                        (captured_by, created_at)
                    )
                    if cur.fetchone():
                        continue

                cur = r_conn.execute(
                    """INSERT INTO tracker_dump
                       (client_id, unassigned_identity, service_id, portal, period_label, arn_number, capture_method, status, raw_payload_json, captured_by, created_at, dataset_key)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        d.get("client_id"), d.get("unassigned_identity"), d.get("service_id"),
                        d.get("portal"), d.get("period_label"), d.get("arn_number"),
                        d.get("capture_method"), d.get("status"), d.get("raw_payload_json"),
                        d.get("captured_by"), d.get("created_at"), d.get("dataset_key")
                    )
                )
                dump_id = cur.lastrowid
                inserted_any = True

                # Update SRPF Unified Container on receiving node
                valid_id = d.get("client_id")
                unassigned_identity = d.get("unassigned_identity")
                if valid_id:
                    container_key = f"CLI-{valid_id:05d}"
                elif unassigned_identity:
                    container_key = unassigned_identity
                else:
                    container_key = f"UNASSIGNED_{dump_id}"

                try:
                    self._update_srpf_container(
                        r_conn,
                        identity_key=container_key,
                        client_id=valid_id,
                        dump_row={
                            "portal": portal,
                            "period_label": d.get("period_label"),
                            "arn_number": d.get("arn_number"),
                            "capture_method": d.get("capture_method"),
                            "status": d.get("status"),
                            "raw_payload_json": d.get("raw_payload_json"),
                            "created_at": created_at or ""
                        }
                    )
                except Exception as exc:
                    pass

        if inserted_any:
            # Clear metrics cache so local node immediately registers the new tracker count
            if hasattr(self, "_sync_metrics_cache"):
                try:
                    del self._sync_metrics_cache
                except AttributeError:
                    pass
            if hasattr(self, "_sync_metrics_cache_ts"):
                try:
                    del self._sync_metrics_cache_ts
                except AttributeError:
                    pass

            # Trigger resolution in case new unassigned dumps matched an existing client
            try:
                now_ts = time.time()
                if now_ts - self._last_reresolve_ts > 10.0:
                    self.re_resolve_all_tracker_dumps()
                    self._last_reresolve_ts = now_ts
            except Exception:
                pass

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

        # Upgrade placeholder client name in master.db if a real scraped name arrived
        if client_id and client_name and not SKELETON_NAME_REGEX.search(client_name):
            try:
                self._upgrade_client_name_if_placeholder(client_id, client_name)
            except Exception as e:
                print(f"[database] Notice upgrading placeholder name for client #{client_id}: {e}")

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

        self._bump_sync_revision_if_configured()

        return {
            "session_id": session_id,
            "client_id": client_id,
            "pan": pan,
            "client_name": client_name,
            "status": status,
            "total_steps": total_steps
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

        from datetime import datetime

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
            # notes/screenshot_path are user-entered, not derived from tracker_dump,
            # so the DELETE below must not lose them (they're re-applied by identity_key
            # once the rebuild is done).
            preserved_notes = {
                row[0]: (row[1], row[2])
                for row in r_conn.execute(
                    "SELECT identity_key, notes, screenshot_path FROM client_raw_containers "
                    "WHERE notes IS NOT NULL OR screenshot_path IS NOT NULL"
                ).fetchall()
            }
            r_conn.execute("DELETE FROM client_raw_containers")
            r_conn.execute("DELETE FROM client_raw_container_light")
            for d in resolved_dumps:
                cands = d["effective_candidates"]
                matched_id = None
                for cand in cands:
                    if cand in identity_to_cid:
                        matched_id = identity_to_cid[cand]
                        break

                if not matched_id and d.get("client_id") and d["client_id"] in all_clients:
                    matched_id = d["client_id"]

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
                        "status": d.get("status"),
                        "raw_payload_json": d["raw_payload_json"],
                        "captured_by": d.get("captured_by"),
                        "created_at": d["created_at"]
                    }
                )

            for identity_key, (notes, screenshot_path) in preserved_notes.items():
                cur = r_conn.execute(
                    "UPDATE client_raw_containers SET notes = ?, screenshot_path = ? WHERE identity_key = ?",
                    (notes, screenshot_path, identity_key)
                )
                r_conn.execute("UPDATE client_raw_container_light SET notes = ? WHERE identity_key = ?", (notes, identity_key))
                if cur.rowcount == 0:
                    # The identity_key this note was filed under no longer exists after
                    # the rebuild (e.g. the underlying dumps re-resolved to a client, so
                    # the container moved from an unassigned key to "CLI-00005"). There's
                    # no reliable way to know which new row inherited it, so it's dropped;
                    # this is logged rather than silent so support can trace a lost note.
                    print(f"[database] re_resolve_all_tracker_dumps: notes/screenshot for "
                          f"identity_key={identity_key!r} could not be carried over (no "
                          f"container has that key after the rebuild); the note was lost.")

        return updated_count

    def get_tracker_dumps(self, client_id: int = None, limit: int = 200, search_query: str = None) -> list[dict]:
        """Reads tracker_dump entries from rawPayload.db and enriches them with client names from master.db."""
        with self._connect_raw() as r_conn:
            sql = """SELECT id, client_id, unassigned_identity, service_id, portal,
                            period_label, arn_number, capture_method, status,
                            raw_payload_json, captured_by, created_at, dataset_key, notes
                     FROM tracker_dump
                     WHERE """ + NOT_CARRIER_SQL     # SDIS carrier rows are not datasets (S.4)
            params = []
            if client_id:
                sql += " AND client_id = ?"
                params.append(client_id)
            if search_query:
                sql += " AND (arn_number LIKE ? OR portal LIKE ? OR period_label LIKE ? OR unassigned_identity LIKE ? OR dataset_key LIKE ?)"
                q = f"%{search_query}%"
                params.extend([q, q, q, q, q])
            sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
            params.append(limit)

            cur = r_conn.execute(sql, params)
            rows = cur.fetchall()

        if not rows:
            return []

        # Enrich client names and tokens from master.db (Batch Optimized)
        client_map = {}
        with self._connect() as m_conn:
            unique_cids = {r[1] for r in rows if r[1]}
            if unique_cids:
                try:
                    client_map = self._fetch_clients_batch(m_conn, unique_cids)
                except Exception:
                    client_map = {}
            for cid in unique_cids:
                if cid not in client_map:
                    client_map[cid] = {"name": f"CLI-{cid:05d}", "pan": "", "is_unassigned": False}

        # Batch resolve names for unassigned identities from client_raw_containers
        unassigned_map = {}
        unique_unassigned = {r[2] for r in rows if not r[1] and r[2]}
        if unique_unassigned:
            with self._connect_raw() as r_conn:
                try:
                    placeholders = ",".join("?" for _ in unique_unassigned)
                    c_rows = r_conn.execute(
                        f"SELECT identity_key, company_name, proprietor_name FROM client_raw_containers WHERE identity_key IN ({placeholders})",
                        list(unique_unassigned)
                    ).fetchall()
                    for c_row in c_rows:
                        c_key = c_row[0]
                        c_comp = c_row[1] or ""
                        c_prop = c_row[2] or ""
                        cand = c_prop or c_comp
                        if cand and not SKELETON_NAME_REGEX.search(cand):
                            unassigned_map[c_key] = cand
                except Exception:
                    pass

        results = []
        for r in rows:
            cid = r[1]
            unassigned_id = r[2]
            raw_json_str = r[9] or ""
            p_obj = None
            if raw_json_str and raw_json_str != "{}":
                try:
                    p_obj = json.loads(raw_json_str) if isinstance(raw_json_str, str) else raw_json_str
                except Exception:
                    pass

            if cid and cid in client_map:
                info = client_map[cid]
            elif unassigned_id:
                # 1. Try container map
                c_name = unassigned_map.get(unassigned_id, "")
                # 2. Try raw_payload_json if container lookup didn't yield a real name
                if not c_name or SKELETON_NAME_REGEX.search(c_name):
                    if isinstance(p_obj, dict):
                        c_name = (
                            p_obj.get("client_name") or p_obj.get("name") or p_obj.get("taxpayer_name") or
                            (p_obj.get("raw_payload", {}).get("client_name") if isinstance(p_obj.get("raw_payload"), dict) else "") or
                            (p_obj.get("raw_payload", {}).get("client_temp_name") if isinstance(p_obj.get("raw_payload"), dict) else "") or ""
                        )
                if c_name and not SKELETON_NAME_REGEX.search(c_name):
                    info = {"name": f"{c_name} ({unassigned_id})", "pan": unassigned_id, "is_unassigned": True}
                else:
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
                "created_at": r[11], "dataset_key": r[12] if len(r) > 12 else "", "notes": r[13] if len(r) > 13 else ""
            })

        # Default chronological entry organisation: latest entry at top
        results.sort(key=lambda x: (str(x.get("created_at") or ""), x.get("id") or 0), reverse=True)
        return results

    def get_tracker_dump_media(self, dump_id: int) -> dict:
        with self._connect_raw() as conn:
            cur = conn.execute("SELECT notes, screenshot_path FROM tracker_dump WHERE id = ?", (dump_id,))
            row = cur.fetchone()
            if row:
                return {"notes": row[0] or "", "screenshot_path": row[1] or ""}
            return {"notes": "", "screenshot_path": ""}

    def save_tracker_dump_media(self, dump_id: int, notes: str, screenshot_path: str) -> bool:
        with self._connect_raw() as conn:
            cur = conn.execute("UPDATE tracker_dump SET notes = ?, screenshot_path = ? WHERE id = ?", (notes, screenshot_path, dump_id))
            return cur.rowcount > 0

    def delete_tracker_dump(self, dump_id: int) -> bool:
        with self._connect_raw() as conn:
            cur = conn.execute("DELETE FROM tracker_dump WHERE id = ?", (dump_id,))
            res = cur.rowcount > 0
        return res

    def clear_tracker_dumps(self) -> int:
        with self._connect_raw() as conn:
            cur = conn.execute("DELETE FROM tracker_dump")
            count = cur.rowcount
        return count
