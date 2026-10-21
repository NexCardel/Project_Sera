"""core/drs/config.py - Deadline Reminder System configuration.

Manages active columns, ordering, and persistence in master.db.
Allows users to arrange MCL columns, fixed DRS status/period columns,
and tracker dump fields.
"""
from __future__ import annotations

import json
from typing import Any

SETTING_KEY = "drs_config"

# Fixed DRS columns
FIXED_COLUMNS = [
    {
        "key": "fixed:latest_period",
        "label": "Latest period",
        "type": "fixed",
        "enabled": True,
        "description": "Latest filing period ingested from portal (e.g. Sep 2026)",
    },
    {
        "key": "fixed:status",
        "label": "Status",
        "type": "fixed",
        "enabled": True,
        "description": "Filing status ladder (Submitted & Verified, Draft, etc.)",
    },
]

# Available Tracker Dump fields
TRACKER_FIELDS = [
    {
        "key": "tracker:arn",
        "label": "ARN",
        "type": "tracker",
        "field": "arn",
        "enabled": True,
        "description": "Acknowledgement Reference Number from portal",
    },
    {
        "key": "tracker:captured_on",
        "label": "Captured on",
        "type": "tracker",
        "field": "captured_on",
        "enabled": True,
        "description": "Timestamp when filing was captured",
    },
    {
        "key": "tracker:captured_by",
        "label": "Captured by",
        "type": "tracker",
        "field": "captured_by",
        "enabled": False,
        "description": "Office workstation that captured the return",
    },
    {
        "key": "tracker:portal",
        "label": "Portal",
        "type": "tracker",
        "field": "portal",
        "enabled": False,
        "description": "Portal name / filing source",
    },
]


def default_drs_columns(db) -> list[dict[str, Any]]:
    """Builds the default column order:
    1. Identity / Name MCL columns (PAN, GSTIN, Name, Trade Name)
    2. Fixed DRS columns (Latest period, Status)
    3. Tracker Dump columns (ARN, Captured on)
    4. Remaining non-password MCL columns (disabled by default)
    5. Remaining Tracker Dump columns
    """
    mcl_cols = db.get_mcl_columns() if hasattr(db, "get_mcl_columns") else []
    
    # Priority MCL columns
    ordered_mcl = []
    rest_mcl = []
    for c in mcl_cols:
        # Never expose password fields in workbooks
        if c.get("field_type") == "password":
            continue
        lbl = (c.get("label") or "").strip()
        lbl_lower = lbl.lower()
        col_def = {
            "key": f"mcl:{c['id']}",
            "label": lbl,
            "type": "mcl",
            "mcl_id": c["id"],
            "field_type": c.get("field_type", "text"),
            "enabled": False,
            "description": f"Master Client List ({c.get('field_type', 'text')})",
        }
        if any(kw in lbl_lower for kw in ("pan", "gst", "name", "client", "company", "trade", "proprietor")):
            col_def["enabled"] = True
            ordered_mcl.append(col_def)
        else:
            rest_mcl.append(col_def)

    # Sort priority MCL so PAN / GSTIN / Name come first
    def _mcl_priority(item):
        l = item["label"].lower()
        if "pan" in l: return 0
        if "gst" in l: return 1
        if "name" in l or "client" in l: return 2
        return 3

    ordered_mcl.sort(key=_mcl_priority)

    # Combine: Selected MCL -> Fixed Columns -> Enabled Tracker -> Rest MCL -> Rest Tracker
    columns = []
    columns.extend(ordered_mcl)
    columns.extend([dict(f) for f in FIXED_COLUMNS])
    columns.extend([dict(t) for t in TRACKER_FIELDS if t["enabled"]])
    columns.extend(rest_mcl)
    columns.extend([dict(t) for t in TRACKER_FIELDS if not t["enabled"]])
    return columns


def load_drs_config(db) -> dict[str, Any]:
    """Loads DRS configuration from master.db settings, reconciling with live MCL columns."""
    raw = None
    if hasattr(db, "get_setting"):
        try:
            raw = db.get_setting(SETTING_KEY, None)
        except Exception:
            raw = None

    if not raw:
        return {"columns": default_drs_columns(db)}

    try:
        saved = json.loads(raw)
        saved_cols = saved.get("columns", [])
    except Exception:
        return {"columns": default_drs_columns(db)}

    # Reconcile saved list with current MCL columns and tracker fields
    mcl_cols = {c["id"]: c for c in (db.get_mcl_columns() if hasattr(db, "get_mcl_columns") else [])}
    result_cols = []
    seen_keys = set()

    for col in saved_cols:
        key = col.get("key", "")
        if not key or key in seen_keys:
            continue
        if key.startswith("mcl:"):
            try:
                mcl_id = int(key.split(":", 1)[1])
            except ValueError:
                continue
            if mcl_id in mcl_cols:
                c = mcl_cols[mcl_id]
                if c.get("field_type") == "password":
                    continue
                col["label"] = c.get("label", col.get("label", ""))
                col["field_type"] = c.get("field_type", "text")
                col["mcl_id"] = mcl_id
                result_cols.append(col)
                seen_keys.add(key)
        elif key.startswith("fixed:"):
            # Update label if missing
            match = next((f for f in FIXED_COLUMNS if f["key"] == key), None)
            if match:
                col["label"] = match["label"]
                col["description"] = match.get("description", "")
                result_cols.append(col)
                seen_keys.add(key)
        elif key.startswith("tracker:"):
            match = next((t for t in TRACKER_FIELDS if t["key"] == key), None)
            if match:
                col["label"] = match["label"]
                col["field"] = match["field"]
                col["description"] = match.get("description", "")
                result_cols.append(col)
                seen_keys.add(key)

    # Append any missing fixed columns
    for f in FIXED_COLUMNS:
        if f["key"] not in seen_keys:
            result_cols.append(dict(f))
            seen_keys.add(f["key"])

    # Append any missing MCL columns (disabled by default)
    for mcl_id, c in mcl_cols.items():
        if c.get("field_type") == "password":
            continue
        k = f"mcl:{mcl_id}"
        if k not in seen_keys:
            result_cols.append({
                "key": k,
                "label": c.get("label", ""),
                "type": "mcl",
                "mcl_id": mcl_id,
                "field_type": c.get("field_type", "text"),
                "enabled": False,
                "description": f"Master Client List ({c.get('field_type', 'text')})",
            })
            seen_keys.add(k)

    # Append any missing tracker fields (disabled by default)
    for t in TRACKER_FIELDS:
        if t["key"] not in seen_keys:
            item = dict(t)
            item["enabled"] = False
            result_cols.append(item)
            seen_keys.add(t["key"])

    return {"columns": result_cols}


def save_drs_config(db, config: dict[str, Any]) -> None:
    """Saves DRS configuration into master.db settings."""
    if hasattr(db, "set_setting"):
        db.set_setting(SETTING_KEY, json.dumps(config, indent=2))


def get_active_columns(db) -> list[dict[str, Any]]:
    """Returns only the columns currently marked enabled=True, in their configured order."""
    cfg = load_drs_config(db)
    return [c for c in cfg.get("columns", []) if c.get("enabled", True)]
