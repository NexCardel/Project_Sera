"""core/drs/engine.py - DRS data extraction and aggregation engine.

Links detected filing forms to portals and master.db services.
Enumerates all registered clients for each service.
Maps latest period and 4-level status ladder from tracker dump.
Populates uncaptured clients with 'no captures yet'.
Handles multiple GSTIN registrations per client for GST returns.
"""
from __future__ import annotations

import calendar
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
from .config import get_active_columns

NO_CAPTURES = "no captures yet"


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

    for c in (containers or []):
        for h in c.get("history", []):
            raw = entry_form(h).strip()
            key = canon(raw)
            if not key:
                continue
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


def _extract_client_gstins(client: dict, mcl_cols: list, containers_by_pan: dict) -> list[str]:
    """Finds all distinct well-formed GSTINs associated with a client from MCL values and captures."""
    gstins = set()
    vals = client.get("values", {})

    # Check MCL columns for GSTIN
    for c in mcl_cols:
        lbl = c.get("label", "").lower()
        if any(kw in lbl for kw in ("gstin", "gst no", "gst_no", "gst number")):
            raw = str(vals.get(c["id"]) or "").strip().upper()
            if len(raw) == 15 and re.match(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$", raw):
                gstins.add(raw)

    # Check captures for this client's PAN
    pan = ""
    for c in mcl_cols:
        if "pan" in c.get("label", "").lower():
            p_val = str(vals.get(c["id"]) or "").strip().upper()
            if len(p_val) == 10 and re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", p_val):
                pan = p_val
                break

    if pan and pan in containers_by_pan:
        cont = containers_by_pan[pan]
        for h in cont.get("history", []):
            g = _gstin_of(h, "")
            if g and len(g) == 15:
                gstins.add(g)

    return sorted(list(gstins))


def _resolve_pan(client: dict, mcl_cols: list) -> str:
    """Extracts or derives a 10-char PAN for a client."""
    vals = client.get("values", {})
    # 1. Direct PAN column
    for c in mcl_cols:
        if "pan" in c.get("label", "").lower():
            val = str(vals.get(c["id"]) or "").strip().upper()
            if len(val) == 10 and re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", val):
                return val
    # 2. Derive from any 15-char GSTIN column
    for c in mcl_cols:
        if "gst" in c.get("label", "").lower():
            val = str(vals.get(c["id"]) or "").strip().upper()
            if len(val) == 15 and re.match(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$", val):
                return val[2:12]
    # 3. Any value matching PAN format
    for val in vals.values():
        val_str = str(val or "").strip().upper()
        if len(val_str) == 10 and re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", val_str):
            return val_str
    return ""


def build_drs_dataset(db, active_cols: list[dict[str, Any]] | None = None) -> dict[str, list[dict[str, Any]]]:
    """Builds the complete DRS dataset for all detected filing form sheets.

    Returns:
    {
        "GSTR-1": [row1, row2, ...],
        "GSTR-3B": [row1, row2, ...],
        "ITR": [row1, row2, ...],
    }
    """
    if active_cols is None:
        active_cols = get_active_columns(db)

    # 1. Fetch SRPF containers for capture history
    containers = []
    if hasattr(db, "get_srpf_containers"):
        try:
            containers = db.get_srpf_containers(limit=1_000_000, slim=True)
        except Exception:
            containers = []

    # Index containers by PAN and by client_id
    containers_by_pan: dict[str, dict] = {}
    containers_by_cid: dict[int, dict] = {}
    for c in containers:
        p = _pan_of(c)
        if p:
            if p not in containers_by_pan:
                containers_by_pan[p] = {"name": c.get("company_name") or c.get("proprietor_name") or "", "history": []}
            containers_by_pan[p]["history"].extend(c.get("history", []))
        cid = c.get("client_id")
        if cid:
            if cid not in containers_by_cid:
                containers_by_cid[cid] = {"history": []}
            containers_by_cid[cid]["history"].extend(c.get("history", []))

    # 2. Detect forms and map to services
    form_defs = detect_forms_and_services(db, containers)
    mcl_cols = db.get_mcl_columns() if hasattr(db, "get_mcl_columns") else []
    all_clients = db.search_clients("", include_archived=False) if hasattr(db, "search_clients") else []

    sheets_data: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for fdef in form_defs:
        form_name = fdef["form"]
        canon_f = fdef["canon_form"]
        svc = fdef["service"]
        is_gst = fdef["is_gst"]
        svc_id = svc["id"] if svc else None

        # Filter registered clients who have this service attached
        matching_clients = []
        if svc_id is not None:
            matching_clients = [cl for cl in all_clients if svc_id in cl.get("service_ids", [])]
        else:
            # If service could not be matched, fallback to all clients
            matching_clients = list(all_clients)

        # Build rows for this sheet
        sheet_rows = []
        for client in matching_clients:
            cid = client["id"]
            cvals = client.get("values", {})
            pan = _resolve_pan(client, mcl_cols)

            # Collect filing history for this client
            histories = []
            if pan and pan in containers_by_pan:
                histories.extend(containers_by_pan[pan]["history"])
            if cid and cid in containers_by_cid:
                histories.extend(containers_by_cid[cid]["history"])

            # Deduplicate history entries by (period_label, arn, created_at)
            seen_entries = set()
            deduped_hist = []
            for h in histories:
                if canon(entry_form(h)) != canon_f:
                    continue
                k = (str(h.get("period_label") or ""), str(h.get("arn") or ""), str(h.get("created_at") or ""))
                if k not in seen_entries:
                    seen_entries.add(k)
                    deduped_hist.append(h)

            if is_gst:
                gstins = _extract_client_gstins(client, mcl_cols, containers_by_pan)
                # If no GSTIN found, produce at least one row
                if not gstins:
                    gstins = [""]
                for gstin in gstins:
                    # Filter history matching this specific GSTIN if available
                    matched_h = []
                    for h in deduped_hist:
                        h_gst = _gstin_of(h, "")
                        if not gstin or not h_gst or h_gst == gstin:
                            matched_h.append(h)
                    row = _build_single_row(active_cols, client, mcl_cols, pan, gstin, matched_h, canon_f)
                    sheet_rows.append(row)
            else:
                row = _build_single_row(active_cols, client, mcl_cols, pan, "", deduped_hist, canon_f)
                sheet_rows.append(row)

        # Sort sheet rows alphabetically by Client Name or PAN
        def _sort_key(r):
            # Prefer sorting by Name column if present, then PAN, then GSTIN
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
    """Assembles a single row according to active column order and maps tracker status."""
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
            # Within this period, pick best ladder status and newest capture
            pick = None
            for h, _, at, label in best_group:
                lvl = ladder(h.get("status"), h.get("arn"))
                when = _capture_time(at)
                if pick is None or when > pick[0]:
                    pick = (when, lvl, label, h)
            if pick:
                best_capture = pick

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
            row[lbl] = val

        elif ctype == "fixed":
            if key == "fixed:latest_period":
                row[lbl] = best_capture[2] if best_capture else NO_CAPTURES
            elif key == "fixed:status":
                row[lbl] = best_capture[1] if best_capture else NO_CAPTURES

        elif ctype == "tracker":
            field = col.get("field", "")
            if not best_capture:
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
                elif field == "portal":
                    row[lbl] = str(h.get("portal") or "")
                else:
                    row[lbl] = str(h.get(field) or "")
        else:
            row[lbl] = ""

    return row
