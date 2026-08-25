"""
Stage D: Identity Resolution & Confidence Tagging
Resolves client entity ownership across header client IDs, JSON body client IDs, and extracted PANs.
"""

import re
from typing import Dict, Any, Optional

PAN_REGEX = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


def extract_pan_from_payload(payload: Any) -> Optional[str]:
    """
    Recursively scans top-level and nested raw_payload keys for valid PAN formats.
    """
    if not isinstance(payload, dict):
        return None

    # Check immediate keys
    direct_keys = (
        "pan", "pan_val", "pannumber", "taxpayerpan", "entitynum",
        "userid", "user_id", "submituserid", "loggedinuserid", "pan_no"
    )
    for k in direct_keys:
        v = payload.get(k)
        if isinstance(v, str) and len(v.strip()) == 10 and PAN_REGEX.match(v.strip().upper()):
            return v.strip().upper()

    # Check inside raw_payload sub-dictionary
    raw_p = payload.get("raw_payload")
    if isinstance(raw_p, dict):
        for k in direct_keys:
            v = raw_p.get(k)
            if isinstance(v, str) and len(v.strip()) == 10 and PAN_REGEX.match(v.strip().upper()):
                return v.strip().upper()
        # Check inside nested header
        head = raw_p.get("header")
        if isinstance(head, dict):
            for k in direct_keys:
                v = head.get(k)
                if isinstance(v, str) and len(v.strip()) == 10 and PAN_REGEX.match(v.strip().upper()):
                    return v.strip().upper()

    # Fallback deep string search in JSON
    json_str = str(payload)
    m = re.search(r"\b([A-Z]{5}[0-9]{4}[A-Z])\b", json_str)
    if m:
        return m.group(1).upper()

    return None


def resolve_identity(
    header_fields: Dict[str, Any],
    json_body: Dict[str, Any],
    client_roster: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Resolves client entity ownership and assigns an identity confidence tag.
    Returns:
    {
        "resolved_client_id": int | None,
        "resolved_pan": str | None,
        "identity_confidence": "exact_id" | "header_id" | "pan_match" | "unresolved",
        "identity_flags": list[str],
        "body_client_id": int | None,
        "header_client_id": int | None
    }
    """
    header_cid = header_fields.get("header_client_id")
    header_pan = header_fields.get("header_pan")
    
    body_cid = json_body.get("client_id") if isinstance(json_body, dict) else None
    if isinstance(body_cid, str) and body_cid.isdigit():
        body_cid = int(body_cid)
    elif not isinstance(body_cid, int):
        body_cid = None

    body_pan = extract_pan_from_payload(json_body) if isinstance(json_body, dict) else None

    flags = []
    
    # Check for Identity Mismatch
    if header_cid is not None and body_cid is not None and header_cid != body_cid:
        flags.append(f"identity_mismatch (Header: {header_cid} vs Body: {body_cid})")

    resolved_cid = None
    resolved_pan = body_pan or header_pan
    confidence = "unresolved"

    # 1. Body client_id priority
    if body_cid is not None and body_cid > 0:
        resolved_cid = body_cid
        confidence = "exact_id"
    # 2. Header client_id fallback
    elif header_cid is not None and header_cid > 0:
        resolved_cid = header_cid
        confidence = "header_id"
    # 3. PAN match via roster or direct PAN presence
    elif resolved_pan:
        if client_roster and resolved_pan in client_roster:
            resolved_cid = client_roster[resolved_pan].get("client_id")
            confidence = "pan_match"
        else:
            confidence = "pan_match"
    else:
        confidence = "unresolved"

    return {
        "resolved_client_id": resolved_cid,
        "resolved_pan": resolved_pan,
        "identity_confidence": confidence,
        "identity_flags": flags,
        "body_client_id": body_cid,
        "header_client_id": header_cid
    }
