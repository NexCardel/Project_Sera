"""core/drs/config.py - Deadline Reminder System configuration.

Manages active columns, ordering, per-form layout configuration, and persistence in master.db.
Allows users to arrange the complete MCL roster of columns, fixed DRS status/period columns,
and comprehensive tracker dump fields per return form sheet.
Supports 'extract from tracker dump only' (LTT mode) and client roster scope toggling.
"""
from __future__ import annotations

import json
from typing import Any

from core.ltt.monthly import is_gst_form
from core.ltt.rules import canon

SETTING_KEY = "drs_config"

# Client Roster Population scopes
SCOPE_ALL_MCL = "all_mcl"           # Whole MCL roster: every registered client in master.db
SCOPE_SERVICE_ONLY = "service_only" # Only clients enrolled in the matching service

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

# Comprehensive Tracker Dump fields
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
        "key": "tracker:period_label",
        "label": "Filing Period",
        "type": "tracker",
        "field": "period_label",
        "enabled": False,
        "description": "Raw return period string from portal capture",
    },
    {
        "key": "tracker:status",
        "label": "Portal Status",
        "type": "tracker",
        "field": "status",
        "enabled": False,
        "description": "Raw submission status from portal",
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
        "key": "tracker:capture_method",
        "label": "Capture method",
        "type": "tracker",
        "field": "capture_method",
        "enabled": False,
        "description": "Engine method used for capture (DOM_Tracker, VSDC247, etc.)",
    },
    {
        "key": "tracker:original_author",
        "label": "Original author",
        "type": "tracker",
        "field": "original_author",
        "enabled": False,
        "description": "Workstation that first created the return dataset",
    },
    {
        "key": "tracker:portal",
        "label": "Portal",
        "type": "tracker",
        "field": "portal",
        "enabled": False,
        "description": "Portal name / filing source",
    },
    {
        "key": "tracker:unassigned_identity",
        "label": "Portal ID",
        "type": "tracker",
        "field": "unassigned_identity",
        "enabled": False,
        "description": "Identity key / number recorded during capture",
    },
    {
        "key": "tracker:dataset_key",
        "label": "Dataset Key",
        "type": "tracker",
        "field": "dataset_key",
        "enabled": False,
        "description": "Canonical deduplication key of the dataset",
    },
    {
        "key": "tracker:notes",
        "label": "Notes",
        "type": "tracker",
        "field": "notes",
        "enabled": False,
        "description": "User notes and remarks recorded for this capture/client",
    },
]


_NAME_LABELS = {
    "name", "client", "client name", "company", "company name", "trade", "trade name",
    "proprietor", "proprietor name", "legal name", "business name", "firm name", "party name",
}


def _mcl_role(label: str) -> str:
    """Classifies primary identity labels, ensuring identity fields are cleanly prioritised."""
    text = " ".join(str(label or "").strip().lower().replace("_", " ").split()).rstrip(":.")
    if text in ("pan", "pan no", "pan number", "permanent account number") or text.startswith("pan "):
        return "pan"
    if (text in ("gstin", "gst no", "gst number", "gstin number", "gst registration no",
                 "gst registration number")
            or text.startswith("gstin") or text.startswith("gst no") or text.startswith("gst number")):
        return "gstin"
    if text in _NAME_LABELS:
        return "name"
    return ""


def default_drs_columns_for_form(db, form_name: str = "", is_gst: bool | None = None) -> list[dict[str, Any]]:
    """Builds the default column order tailored for a specific filing form:
    1. Primary identity MCL columns (PAN, Name; GSTIN enabled only on GST forms)
    2. Fixed DRS columns (Latest period, Status)
    3. Enabled Tracker Dump columns (ARN, Captured on)
    4. Remaining non-password MCL columns from the full roster (disabled by default)
    5. Remaining Tracker Dump columns (disabled by default)
    """
    if is_gst is None:
        c_name = canon(form_name)
        is_gst = is_gst_form(c_name) if c_name else True

    mcl_cols = db.get_mcl_columns() if hasattr(db, "get_mcl_columns") else []

    priority_order = {"pan": 0, "name": 1, "gstin": 2}
    ordered_mcl = []
    rest_mcl = []
    for c in mcl_cols:
        # Never expose password fields in workbooks or exports
        if c.get("field_type") == "password":
            continue
        lbl = (c.get("label") or "").strip()
        col_def = {
            "key": f"mcl:{c['id']}",
            "label": lbl,
            "type": "mcl",
            "mcl_id": c["id"],
            "field_type": c.get("field_type", "text"),
            "enabled": False,
            "description": f"Master Client List ({c.get('field_type', 'text')})",
        }
        role = _mcl_role(lbl)
        if role:
            # For non-GST forms, keep GSTIN disabled by default
            if role == "gstin" and not is_gst:
                rest_mcl.append(col_def)
            else:
                col_def["enabled"] = True
                ordered_mcl.append((priority_order[role], col_def))
        else:
            rest_mcl.append(col_def)

    ordered_mcl.sort(key=lambda pair: pair[0])

    columns = []
    columns.extend(col_def for _, col_def in ordered_mcl)
    columns.extend([dict(f) for f in FIXED_COLUMNS])
    columns.extend([dict(t) for t in TRACKER_FIELDS if t["enabled"]])
    columns.extend(rest_mcl)
    columns.extend([dict(t) for t in TRACKER_FIELDS if not t["enabled"]])
    return columns


def default_drs_columns(db) -> list[dict[str, Any]]:
    """Global default column order (backwards compatibility)."""
    return default_drs_columns_for_form(db, "", is_gst=True)


def default_drs_config(db, detected_forms: list[str] | None = None) -> dict[str, Any]:
    """Generates the baseline configuration structure supporting per-form layouts."""
    forms_cfg = {}
    for f in (detected_forms or []):
        forms_cfg[f] = {
            "tracker_only": False,
            "columns": default_drs_columns_for_form(db, f),
        }

    return {
        "client_roster_scope": SCOPE_ALL_MCL,
        "forms": forms_cfg,
        "columns": default_drs_columns(db),
    }


def _reconcile_column_list(saved_cols: list[dict], db) -> list[dict[str, Any]]:
    """Reconciles a saved column sequence with live MCL columns and TRACKER_FIELDS."""
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

    return result_cols


def load_drs_config(db, detected_forms: list[str] | None = None) -> dict[str, Any]:
    """Loads DRS configuration from master.db settings, reconciling with live MCL columns
    and supporting per-form layouts.
    """
    raw = None
    if hasattr(db, "get_setting"):
        try:
            raw = db.get_setting(SETTING_KEY, None)
        except Exception:
            raw = None

    if not raw:
        return default_drs_config(db, detected_forms)

    try:
        saved = json.loads(raw)
    except Exception:
        return default_drs_config(db, detected_forms)

    if not isinstance(saved, dict):
        return default_drs_config(db, detected_forms)

    # Reconcile client_roster_scope
    scope = saved.get("client_roster_scope", SCOPE_ALL_MCL)
    if scope not in (SCOPE_ALL_MCL, SCOPE_SERVICE_ONLY):
        scope = SCOPE_ALL_MCL

    # Reconcile global fallback columns
    global_cols = _reconcile_column_list(saved.get("columns", []), db) if "columns" in saved else default_drs_columns(db)

    # Reconcile per-form configurations
    saved_forms = saved.get("forms", {})
    reconciled_forms = {}

    all_form_keys = set(saved_forms.keys()) | set(detected_forms or [])
    for f in all_form_keys:
        f_cfg = saved_forms.get(f)
        if isinstance(f_cfg, dict):
            f_cols = f_cfg.get("columns")
            reconciled_cols = _reconcile_column_list(f_cols, db) if f_cols else default_drs_columns_for_form(db, f)
            tracker_only = bool(f_cfg.get("tracker_only", False))
        else:
            reconciled_cols = default_drs_columns_for_form(db, f)
            tracker_only = False

        reconciled_forms[f] = {
            "tracker_only": tracker_only,
            "columns": reconciled_cols,
        }

    return {
        "client_roster_scope": scope,
        "forms": reconciled_forms,
        "columns": global_cols,
    }


def save_drs_config(db, config: dict[str, Any]) -> None:
    """Saves DRS configuration into master.db settings."""
    if hasattr(db, "set_setting"):
        db.set_setting(SETTING_KEY, json.dumps(config, indent=2))


def get_active_columns_for_form(db, form_name: str) -> list[dict[str, Any]]:
    """Returns only the columns currently marked enabled=True for the specified form."""
    cfg = load_drs_config(db, [form_name])
    form_cfg = cfg.get("forms", {}).get(form_name)
    if form_cfg and "columns" in form_cfg:
        return [c for c in form_cfg["columns"] if c.get("enabled", True)]
    # Fallback to global active columns
    return [c for c in cfg.get("columns", []) if c.get("enabled", True)]


def get_active_columns_by_form(db, forms: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Returns a mapping of form -> active enabled columns for all requested forms."""
    cfg = load_drs_config(db, forms)
    out = {}
    for f in forms:
        f_cfg = cfg.get("forms", {}).get(f)
        if f_cfg and "columns" in f_cfg:
            out[f] = [c for c in f_cfg["columns"] if c.get("enabled", True)]
        else:
            out[f] = [c for c in cfg.get("columns", []) if c.get("enabled", True)]
    return out


def is_tracker_only_for_form(db, form_name: str) -> bool:
    """Returns whether the specified form sheet extracts strictly from Tracker Dump (LTT mode)."""
    cfg = load_drs_config(db, [form_name])
    return bool(cfg.get("forms", {}).get(form_name, {}).get("tracker_only", False))


def get_active_columns(db) -> list[dict[str, Any]]:
    """Returns the global active columns (backwards compatibility)."""
    cfg = load_drs_config(db)
    return [c for c in cfg.get("columns", []) if c.get("enabled", True)]
