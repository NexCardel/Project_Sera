"""core/drs/engine.py - DRS data extraction and aggregation engine.

Links detected filing forms to portals and master.db services.
Supports per-form column mapping, full MCL roster population,
and 'extract from tracker dump only' (LTT mode).
Maps latest period and 4-level status ladder from tracker dump.
Populates uncaptured clients with 'no captures yet'.
Handles multiple GSTIN registrations per client for GST returns.
"""
from __future__ import annotations

import calendar
import json
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any

from core.ltt.feed import (
    LEVELS,
    NOT_APPLICABLE,
    clean_name,
    entry_form,
    ladder,
    local_time,
    parse_period,
    _pan_of,
)
from core.ltt.monthly import (
    is_gst_form,
    period_key,
    _capture_time,
    _gstin_of,
)
from core.ltt.rules import FY_START_MONTH, canon
from .config import (
    SCOPE_ALL_MCL,
    SCOPE_SERVICE_ONLY,
    get_active_columns,
    get_active_columns_for_form,
    get_active_columns_by_form,
    is_tracker_only_for_form,
    load_drs_config,
)

NO_CAPTURES = "no captures yet"

_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def _history_of(container: dict) -> list[dict]:
    """SRPF containers store captures under 'filing_history'; simple/test containers may use 'history'."""
    hist = container.get("filing_history")
    if hist is None:
        hist = container.get("history")
    return hist or []


def _tracker_history_entries(db) -> list[tuple[Any, str, dict]]:
    """Reads raw tracker_dump rows as a fallback for captures SRPF has not aggregated yet."""
    if not hasattr(db, "get_tracker_dumps"):
        return []
    try:
        rows = db.get_tracker_dumps(limit=-1, slim=True)
    except Exception:
        return []

    out: list[tuple[Any, str, dict]] = []
    for row in rows or []:
        ident = str(row.get("unassigned_identity") or "").strip().upper()
        raw_json_str = row.get("raw_payload_json") or ""
        orig_author = str(row.get("original_author") or row.get("payload_original_author") or "").strip()
        pj = {}
        if not orig_author and raw_json_str:
            try:
                pj = json.loads(raw_json_str) if isinstance(raw_json_str, str) else raw_json_str
                if isinstance(pj, dict):
                    orig_author = (
                        pj.get("original_author")
                        or pj.get("raw_payload", {}).get("original_author")
                        or ""
                    )
            except Exception:
                pass

        notes_val = str(row.get("notes") or "").strip()
        if not notes_val and isinstance(pj, dict):
            notes_val = str(pj.get("notes") or "").strip()

        entry = {
            "portal": row.get("portal") or "",
            "period_label": row.get("period_label") or "",
            "status": row.get("status") or "",
            "arn": row.get("arn_number") or row.get("arn") or "",
            "created_at": row.get("created_at") or "",
            "captured_by": row.get("captured_by") or "",
            "capture_method": row.get("capture_method") or "",
            "original_author": orig_author,
            "notes": notes_val,
            "unassigned_identity": ident,
            "dataset_key": row.get("dataset_key") or "",
        }
        if len(ident) == 15:
            entry["gstin"] = ident
        elif len(ident) == 10:
            entry["pan"] = ident
        out.append((row.get("client_id"), ident, entry))
    return out


def _portal_base_name(portal: str) -> str:
    """Extracts base portal name from strings like 'GST (GSTR-3B)' -> 'GST'."""
    p = str(portal or "").strip()
    idx = p.find("(")
    if idx != -1:
        p = p[:idx].strip()
    return p


def detect_forms_and_services(db, containers: list | None = None) -> list[dict[str, Any]]:
    """Discovers all filing forms across tracker dump / SRPF containers and maps each
    to its respective portal and master.db service.

    Returns list of dicts:
    [
        {
            "form": "GSTR-3B",
            "canon_form": "GSTR3B",
            "portal": "GST",
            "service": {"id": 1, "name": "GST", ...},
            "is_gst": True,
        },
        ...
    ]
    """
    if containers is None and hasattr(db, "get_srpf_containers"):
        try:
            containers = db.get_srpf_containers(limit=1_000_000, slim=True)
        except Exception:
            containers = []

    services = db.get_services() if hasattr(db, "get_services") else []

    # Map canon_form -> (display_form, portal_base, sample_entry)
    detected: dict[str, tuple[str, str, dict]] = {}

    _NON_RETURN_LABELS = {
        "portal", "gst portal", "income tax portal", "income tax", "gst",
        "gst return", "gst returns", "gst returns calendar", "gst submission",
        "dashboard", "returns dashboard", "track return status"
    }

    for c in (containers or []):
        for h in _history_of(c):
            raw = entry_form(h).strip()
            key = canon(raw)
            if not key or raw.lower() in _NON_RETURN_LABELS or key in (
                "PORTAL", "GSTPORTAL", "INCOMETAXPORTAL", "GSTRETURN", "GSTRETURNS",
                "GSTRETURNSCALENDAR", "GSTSUBMISSION", "DASHBOARD", "RETURNSDASHBOARD", "TRACKRETURNSTATUS"
            ):
                continue
            if key == "GSTRIA":
                key = "GSTR1A"
                raw = "GSTR-1A"
            elif key == "GSTR38":
                key = "GSTR3B"
                raw = "GSTR-3B"
            portal_raw = str(h.get("portal") or "")
            base_portal = _portal_base_name(portal_raw)
            if key not in detected or ("-" in raw and "-" not in detected[key][0]):
                detected[key] = (raw.upper() if raw.upper() == raw or raw.islower() else raw, base_portal, h)

    # If no captures detected yet, provide standard default forms for existing services
    if not detected:
        for s in services:
            sname = (s.get("name") or "").lower()
            if "gst" in sname:
                detected["GSTR1"] = ("GSTR-1", "GST", {})
                detected["GSTR3B"] = ("GSTR-3B", "GST", {})
            elif any(kw in sname for kw in ("income", "itr", "tax")):
                detected["ITR"] = ("ITR", "Income Tax", {})

    forms_list = []
    for key, (display_name, base_portal, sample) in detected.items():
        # Match service
        svc = None
        if hasattr(db, "get_service_for_portal"):
            svc = db.get_service_for_portal(base_portal)
            if not svc:
                svc = db.get_service_for_portal(display_name)
        if not svc and services:
            # Fallback keyword match against known services
            for s in services:
                sn = (s.get("name") or "").lower()
                if "gst" in sn and (is_gst_form(key) or "gst" in base_portal.lower()):
                    svc = s
                    break
                if any(kw in sn for kw in ("income", "itr", "tax")) and ("itr" in key.lower() or "income" in base_portal.lower()):
                    svc = s
                    break

        forms_list.append({
            "form": display_name,
            "canon_form": key,
            "portal": base_portal or (svc.get("name") if svc else "Portal"),
            "service": svc,
            "is_gst": is_gst_form(key),
        })

    # Order: GST forms first (GSTR-1, GSTR-3B, etc.), then ITR, then others
    def _form_sort_key(item):
        f = item["form"].upper()
        if "GSTR-1" in f: return (0, f)
        if "GSTR-3B" in f: return (1, f)
        if f.startswith("GSTR"): return (2, f)
        if f.startswith("ITR"): return (3, f)
        return (4, f)

    forms_list.sort(key=_form_sort_key)
    return forms_list


def _mcl_column_ids(mcl_cols: list) -> tuple[list, list, list]:
    """Splits MCL columns into PAN, GSTIN and GST id lists once per build."""
    pan_ids, gstin_ids, gst_ids = [], [], []
    for c in mcl_cols:
        lbl = str(c.get("label", "")).lower()
        if "pan" in lbl:
            pan_ids.append(c["id"])
        if any(kw in lbl for kw in ("gstin", "gst no", "gst_no", "gst number")):
            gstin_ids.append(c["id"])
        if "gst" in lbl:
            gst_ids.append(c["id"])
    return pan_ids, gstin_ids, gst_ids


def _extract_client_gstins(client: dict, mcl_cols: list, containers_by_pan: dict,
                           pan: str | None = None, gstin_ids: list | None = None) -> list[str]:
    """Finds all distinct well-formed GSTINs associated with a client from MCL values and captures."""
    gstins = set()
    vals = client.get("values", {})
    if gstin_ids is None:
        _, gstin_ids, _ = _mcl_column_ids(mcl_cols)

    # Check MCL columns for GSTIN
    for mcl_id in gstin_ids:
        raw = str(vals.get(mcl_id) or "").strip().upper()
        if _GSTIN_RE.match(raw):
            gstins.add(raw)

    # Check captures for this client's PAN
    if pan is None:
        pan = _resolve_pan(client, mcl_cols)

    if pan and pan in containers_by_pan:
        cont = containers_by_pan[pan]
        for h in _history_of(cont):
            g = _gstin_of(h, "")
            if g and len(g) == 15:
                gstins.add(g)

    return sorted(list(gstins))


def _resolve_pan(client: dict, mcl_cols: list, pan_ids: list | None = None,
                 gst_ids: list | None = None) -> str:
    """Extracts or derives a 10-char PAN for a client."""
    vals = client.get("values", {})
    if pan_ids is None or gst_ids is None:
        pan_ids, _, gst_ids = _mcl_column_ids(mcl_cols)
    # 1. Direct PAN column
    for mcl_id in pan_ids:
        val = str(vals.get(mcl_id) or "").strip().upper()
        if _PAN_RE.match(val):
            return val
    # 2. Derive from any 15-char GSTIN column
    for mcl_id in gst_ids:
        val = str(vals.get(mcl_id) or "").strip().upper()
        if _GSTIN_RE.match(val):
            return val[2:12]
    # 3. Any value matching PAN format
    for val in vals.values():
        val_str = str(val or "").strip().upper()
        if _PAN_RE.match(val_str):
            return val_str
    return ""


def build_drs_dataset(
    db,
    active_cols_by_form: dict[str, list[dict[str, Any]]] | list[dict[str, Any]] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Builds the complete DRS dataset for all detected filing form sheets.
    Supports per-form active columns, whole MCL roster or service-scoped population,
    and tracker-only mode (LTT style: omitting uncaptured clients).

    Returns:
    {
        "GSTR-1": [row1, row2, ...],
        "GSTR-3B": [row1, row2, ...],
        "ITR": [row1, row2, ...],
    }
    """
    # 1. Fetch SRPF containers for capture history
    containers = []
    if hasattr(db, "get_srpf_containers"):
        try:
            containers = db.get_srpf_containers(limit=1_000_000, slim=True)
        except Exception:
            containers = []

    # Index containers by PAN and by client_id. The canonical form and GSTIN of every capture
    # are computed once here: the same entries are inspected for every form sheet otherwise.
    entry_meta: dict[int, tuple[str, str]] = {}

    def _meta(entry: dict) -> tuple[str, str]:
        key = id(entry)
        found = entry_meta.get(key)
        if found is None:
            found = (canon(entry_form(entry)), _gstin_of(entry, ""))
            entry_meta[key] = found
        return found

    containers_by_pan: dict[str, dict] = {}
    containers_by_cid: dict[int, dict] = {}
    for c in containers:
        history = _history_of(c)
        for h in history:
            _meta(h)
        p = _pan_of(c)
        if p:
            if p not in containers_by_pan:
                containers_by_pan[p] = {"name": c.get("company_name") or c.get("proprietor_name") or "", "history": []}
            containers_by_pan[p]["history"].extend(history)
        cid = c.get("client_id")
        if cid:
            if cid not in containers_by_cid:
                containers_by_cid[cid] = {"history": []}
            containers_by_cid[cid]["history"].extend(history)

    # 1b. Read tracker_dump directly as well: captures present before SRPF container rebuild
    tracker_rows = _tracker_history_entries(db)
    for cid, ident, entry in tracker_rows:
        _meta(entry)
        if cid:
            containers_by_cid.setdefault(cid, {"history": []})["history"].append(entry)
        entry_pan = ident if len(ident) == 10 else (ident[2:12] if len(ident) == 15 else "")
        if entry_pan:
            containers_by_pan.setdefault(entry_pan, {"name": "", "history": []})["history"].append(entry)

    # 2. Detect forms and map to services
    tracker_entries = [entry for _, _, entry in tracker_rows]
    detection_containers = list(containers)
    if tracker_entries:
        detection_containers.append({"history": tracker_entries})
    form_defs = detect_forms_and_services(db, detection_containers)
    detected_form_names = [f["form"] for f in form_defs]

    # Load configuration
    cfg = config or load_drs_config(db, detected_forms=detected_form_names)
    scope = cfg.get("client_roster_scope", SCOPE_ALL_MCL)
    forms_cfg = cfg.get("forms", {})

    # Resolve per-form columns
    if isinstance(active_cols_by_form, list):
        # Legacy list passed: apply to all forms
        resolved_cols_map = {f["form"]: active_cols_by_form for f in form_defs}
    elif isinstance(active_cols_by_form, dict):
        resolved_cols_map = active_cols_by_form
    else:
        resolved_cols_map = get_active_columns_by_form(db, detected_form_names)

    mcl_cols = db.get_mcl_columns() if hasattr(db, "get_mcl_columns") else []
    all_clients = db.search_clients("", include_archived=False) if hasattr(db, "search_clients") else []
    pan_ids, gstin_ids, gst_ids = _mcl_column_ids(mcl_cols)

    # Per-client cache: PAN plus captures already deduplicated and grouped by canonical form.
    # Resolving the same client for every form sheet was the dominant cost of this build.
    client_cache: dict[int, tuple[str, dict[str, list[dict]]]] = {}
    gstin_cache: dict[int, list[str]] = {}

    def _client_data(client: dict) -> tuple[str, dict[str, list[dict]]]:
        key = id(client)
        cached = client_cache.get(key)
        if cached is not None:
            return cached
        pan = _resolve_pan(client, mcl_cols, pan_ids, gst_ids)
        cid = client.get("id")
        histories = []
        if pan and pan in containers_by_pan:
            histories.extend(containers_by_pan[pan]["history"])
        if cid and cid in containers_by_cid:
            histories.extend(containers_by_cid[cid]["history"])

        grouped: dict[str, list[dict]] = {}
        seen: set = set()
        for h in histories:
            canon_h, _ = _meta(h)
            arn = str(h.get("arn") or "").strip().upper()
            dedupe_key = (canon_h, str(h.get("period_label") or ""),
                          "" if arn == "N/A" else arn, str(h.get("created_at") or ""))
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            grouped.setdefault(canon_h, []).append(h)
        cached = (pan, grouped)
        client_cache[key] = cached
        return cached

    def _client_gstins(client: dict, pan: str) -> list[str]:
        key = id(client)
        cached = gstin_cache.get(key)
        if cached is None:
            cached = _extract_client_gstins(client, mcl_cols, containers_by_pan, pan, gstin_ids)
            gstin_cache[key] = cached
        return cached

    # Map registered client PANs
    registered_pans = set()
    for cl in all_clients:
        p, _ = _client_data(cl)
        if p:
            registered_pans.add(p)

    sheets_data: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for fdef in form_defs:
        form_name = fdef["form"]
        canon_f = fdef["canon_form"]
        svc = fdef["service"]
        is_gst = fdef["is_gst"]
        svc_id = svc["id"] if svc else None

        form_cols = resolved_cols_map.get(form_name) or get_active_columns_for_form(db, form_name)
        f_settings = forms_cfg.get(form_name, {})
        tracker_only = bool(f_settings.get("tracker_only", False))

        # Select candidate clients according to scope
        if scope == SCOPE_SERVICE_ONLY and svc_id is not None:
            candidate_clients = [cl for cl in all_clients if svc_id in cl.get("service_ids", [])]
        else:
            # Whole MCL roster
            candidate_clients = list(all_clients)

        sheet_rows = []

        for client in candidate_clients:
            pan, captures_by_form = _client_data(client)
            deduped_hist = captures_by_form.get(canon_f, [])

            # In tracker_only (LTT mode), omit clients who have zero captures for this form
            if tracker_only and not deduped_hist:
                continue

            if is_gst:
                gstins = _client_gstins(client, pan) or [""]
                for gstin in gstins:
                    matched_h = []
                    for h in deduped_hist:
                        h_gst = _meta(h)[1]
                        if not gstin or not h_gst or h_gst == gstin:
                            matched_h.append(h)

                    # In tracker_only mode, omit specific registration row if it has no captures
                    if tracker_only and not matched_h:
                        continue

                    row = _build_single_row(form_cols, client, mcl_cols, pan, gstin, matched_h, canon_f)
                    sheet_rows.append(row)
            else:
                row = _build_single_row(form_cols, client, mcl_cols, pan, "", deduped_hist, canon_f)
                sheet_rows.append(row)

        # In tracker_only mode, also include any unassigned captures matching this form
        if tracker_only:
            seen_unassigned_pans = set()
            for pan_key, cdata in containers_by_pan.items():
                if pan_key in registered_pans or pan_key in seen_unassigned_pans:
                    continue
                unassigned_h = [
                    h for h in cdata.get("history", [])
                    if _meta(h)[0] == canon_f
                ]
                if not unassigned_h:
                    continue
                seen_unassigned_pans.add(pan_key)

                # Deduplicate unassigned entries
                deduped_u = []
                seen_u = set()
                for h in unassigned_h:
                    arn = str(h.get("arn") or "").strip().upper()
                    k = (str(h.get("period_label") or ""), "" if arn == "N/A" else arn, str(h.get("created_at") or ""))
                    if k not in seen_u:
                        seen_u.add(k)
                        deduped_u.append(h)

                synth_client = {
                    "id": None,
                    "values": {},
                    "service_ids": [],
                    "name": cdata.get("name") or "",
                }
                if is_gst:
                    captured_gstins = list({g for h in deduped_u if (g := _meta(h)[1])}) or [""]
                    for g in captured_gstins:
                        matched_u = [h for h in deduped_u if not g or not _meta(h)[1] or _meta(h)[1] == g]
                        row = _build_single_row(form_cols, synth_client, mcl_cols, pan_key, g, matched_u, canon_f)
                        sheet_rows.append(row)
                else:
                    row = _build_single_row(form_cols, synth_client, mcl_cols, pan_key, "", deduped_u, canon_f)
                    sheet_rows.append(row)

        # Sort sheet rows alphabetically by Client Name or PAN
        def _sort_key(r):
            name_val = r.get("Name") or r.get("Client Name") or r.get("Company Name") or ""
            pan_val = r.get("PAN") or ""
            gst_val = r.get("GSTIN") or ""
            return (str(name_val).lower(), str(pan_val), str(gst_val))

        sheet_rows.sort(key=_sort_key)
        sheets_data[form_name] = sheet_rows

    return dict(sheets_data)


def _build_single_row(
    active_cols: list[dict[str, Any]],
    client: dict,
    mcl_cols: list,
    pan: str,
    gstin: str,
    form_history: list[dict],
    canon_f: str,
) -> dict[str, Any]:
    """Assembles a single row according to the form's active column order and maps tracker status."""
    vals = client.get("values", {})
    row: dict[str, Any] = {}

    # 1. Resolve newest capture for this return form
    best_capture = None
    if form_history:
        # Group by period label and find latest
        groups: dict = {}
        for h in form_history:
            label = str(h.get("period_label") or "").strip()
            if not label:
                continue
            end = period_key(label, canon_f)
            at = str(h.get("created_at") or "")
            groups.setdefault(end or label.lower(), []).append((h, end, at, label))

        if groups:
            # Find newest period group
            best_group = max(groups.values(), key=lambda entries: (entries[0][1] or date.min, max(_capture_time(e[2]) for e in entries)))
            # Within this period, pick best ladder status (breaking ties by newest capture)
            pick = None
            for h, _, at, label in best_group:
                lvl = ladder(h.get("status"), h.get("arn"))
                rank = LEVELS.index(lvl) if lvl in LEVELS else -1
                when = _capture_time(at)
                if pick is None or (rank, when) > (pick[0], pick[1]):
                    pick = (rank, when, at, lvl, label, h)
            if pick:
                best_capture = (pick[2], pick[3], pick[4], pick[5])

    # 2. Build row values for each configured column
    for col in active_cols:
        key = col["key"]
        lbl = col["label"]
        ctype = col.get("type", "")

        if ctype == "mcl":
            mcl_id = col.get("mcl_id")
            val = str(vals.get(mcl_id) or "").strip()
            # If this column is GSTIN and we have a specific registration for this row:
            if gstin and any(kw in lbl.lower() for kw in ("gst", "gstin")):
                val = gstin
            # If this column is PAN and we resolved a pan:
            elif pan and "pan" in lbl.lower() and not val:
                val = pan
            # If synthetic client has name:
            elif not val and any(kw in lbl.lower() for kw in ("name", "client", "company")):
                val = client.get("name", "")
            row[lbl] = val

        elif ctype == "fixed":
            if key == "fixed:latest_period":
                row[lbl] = best_capture[2] if best_capture else NO_CAPTURES
            elif key == "fixed:status":
                row[lbl] = best_capture[1] if best_capture else NO_CAPTURES

        elif ctype == "tracker":
            field = col.get("field", "")
            if not best_capture:
                if field == "notes":
                    row[lbl] = str(client.get("notes") or "").strip()
                else:
                    row[lbl] = ""
            else:
                h = best_capture[3]
                if field == "arn":
                    arn = str(h.get("arn") or "").strip()
                    row[lbl] = "" if arn.upper() == "N/A" else arn
                elif field == "captured_on":
                    at = str(h.get("created_at") or "")
                    row[lbl] = local_time(at) if at else ""
                elif field == "captured_by":
                    row[lbl] = str(h.get("captured_by") or "")
                elif field == "capture_method":
                    row[lbl] = str(h.get("capture_method") or "")
                elif field == "original_author":
                    orig_a = str(h.get("original_author") or "").strip()
                    if not orig_a and h.get("raw_payload_json"):
                        try:
                            rp = json.loads(h["raw_payload_json"]) if isinstance(h["raw_payload_json"], str) else h["raw_payload_json"]
                            if isinstance(rp, dict):
                                orig_a = str(rp.get("original_author") or rp.get("raw_payload", {}).get("original_author") or "").strip()
                        except Exception:
                            pass
                    row[lbl] = orig_a
                elif field == "notes":
                    c_note = str(h.get("notes") or "").strip()
                    if not c_note:
                        c_note = str(client.get("notes") or "").strip()
                    row[lbl] = c_note
                elif field == "portal":
                    row[lbl] = str(h.get("portal") or "")
                elif field == "status":
                    row[lbl] = str(h.get("status") or "")
                elif field == "period_label":
                    row[lbl] = str(h.get("period_label") or "")
                elif field == "unassigned_identity":
                    row[lbl] = str(h.get("unassigned_identity") or "")
                elif field == "dataset_key":
                    row[lbl] = str(h.get("dataset_key") or "")
                else:
                    row[lbl] = str(h.get(field) or "")
        else:
            row[lbl] = ""

    return row
