"""
Stage D: Identity Resolution & Confidence Tagging
Resolves client entity ownership across header client IDs, JSON body client IDs, and extracted PANs.
"""

import re
from collections import defaultdict
from datetime import datetime
from typing import Dict, Any, Optional, List, Set, Tuple

PAN_REGEX = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
PAN_SEARCH_REGEX = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", re.IGNORECASE)
GSTIN_REGEX = re.compile(r"\b[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b", re.IGNORECASE)


def _walk_values(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key), child
            yield from _walk_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_values(child)


def extract_identity_evidence(header_fields: Dict[str, Any], json_body: Dict[str, Any]) -> Dict[str, Any]:
    """Extract all identity evidence without choosing an owner.

    This is deliberately separate from ``resolve_identity`` so existing callers
    keep their behavior while the full-dump orchestrator can perform a global,
    evidence-first second pass.
    """
    pans: Set[str] = set()
    gstins: Set[str] = set()
    sources: Dict[str, Set[str]] = defaultdict(set)

    def scan(value: Any, source: str):
        if value is None:
            return
        text = str(value).strip().upper()
        if PAN_REGEX.match(text):
            pans.add(text)
            sources[text].add(source)
        for match in PAN_SEARCH_REGEX.findall(text):
            pan = match.upper()
            pans.add(pan)
            sources[pan].add(source)
        for match in GSTIN_REGEX.findall(text):
            gstin = match.upper()
            gstins.add(gstin)
            sources[gstin].add(source)

    scan(header_fields.get("header_pan"), "header_pan")
    if isinstance(json_body, dict):
        for key, value in _walk_values(json_body):
            key_lower = key.lower()
            if key_lower in {
                "pan", "pan_val", "pannumber", "taxpayerpan", "entitynum",
                "userid", "user_id", "submituserid", "loggedinuserid", "pan_no",
                "gstin", "gstinno", "gstin_num"
            }:
                scan(value, f"payload.{key}")
            elif isinstance(value, str):
                scan(value, f"deep.{key}")

    for gstin in list(gstins):
        derived_pan = gstin[2:12]
        if PAN_REGEX.match(derived_pan):
            pans.add(derived_pan)
            sources[derived_pan].add(f"gstin:{gstin}")

    return {
        "pans": sorted(pans),
        "gstins": sorted(gstins),
        "sources": {key: sorted(value) for key, value in sources.items()},
    }


def extract_session_token(json_body: Dict[str, Any]) -> Optional[str]:
    """Find a portal session token while avoiding generic transaction IDs."""
    if not isinstance(json_body, dict):
        return None
    preferred = {"session_id", "sessionid", "session_token", "sessiontoken", "sessionkey", "session_key"}
    for key, value in _walk_values(json_body):
        if key.lower() in preferred and value not in (None, ""):
            return str(value).strip()
    return None


def _event_datetime(event: Dict[str, Any]) -> Optional[datetime]:
    try:
        value = str(event.get("timestamp") or "").replace("Z", "+00:00")
        return datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def resolve_context_identities(
    events: List[Dict[str, Any]],
    timestamp_window_sec: int = 90,
    temporal_window_sec: int = 900,
) -> List[Dict[str, Any]]:
    """Apply three-layer identity context without removing uncertain events.

    Layer 1: exact session-token ownership.
    Layer 2: nearest same-portal timestamp context.
    Layer 3: wider temporal context, choosing the nearest candidate but
    retaining all candidates and marking the event ambiguous when necessary.
    """
    token_to_pans: Dict[str, Set[str]] = defaultdict(set)
    ack_to_pans: Dict[str, Set[str]] = defaultdict(set)
    explicit = []

    for event in events:
        token = event.get("session_id")
        if token and event.get("resolved_pan"):
            token_to_pans[str(token)].add(event["resolved_pan"])
        if event.get("arn") and event.get("resolved_pan"):
            ack_to_pans[str(event["arn"])].add(event["resolved_pan"])
        if event.get("resolved_pan"):
            explicit.append(event)

    for event in events:
        direct_pan = event.get("resolved_pan")
        event.setdefault("identity_flags", [])
        event["identity_candidates"] = []
        event["timeline_pan"] = direct_pan
        event["timeline_key"] = f"PAN-{direct_pan}" if direct_pan else None

        if direct_pan:
            event.update(identity_method="explicit_pan", identity_status="resolved", identity_confidence="high")
            continue

        token = str(event.get("session_id") or "")
        token_pans = token_to_pans.get(token, set()) if token else set()
        if len(token_pans) == 1:
            pan = next(iter(token_pans))
            event.update(timeline_pan=pan, timeline_key=f"PAN-{pan}", identity_method="session_token", identity_status="inferred", identity_confidence="high")
            event["identity_candidates"] = sorted(token_pans)
            event["identity_flags"].append("identity_inferred_from_session_token")
            continue

        ack_pans = ack_to_pans.get(str(event.get("arn") or ""), set()) if event.get("arn") else set()
        if len(ack_pans) == 1:
            pan = next(iter(ack_pans))
            event.update(timeline_pan=pan, timeline_key=f"PAN-{pan}", identity_method="ack_link", identity_status="inferred", identity_confidence="high")
            event["identity_candidates"] = sorted(ack_pans)
            event["identity_flags"].append("identity_inferred_from_ack")
            continue

        current_dt = _event_datetime(event)
        candidates: List[Tuple[float, str]] = []
        if current_dt:
            for anchor in explicit:
                anchor_dt = _event_datetime(anchor)
                if not anchor_dt or anchor.get("portal") != event.get("portal"):
                    continue
                distance = abs((current_dt - anchor_dt).total_seconds())
                if distance <= temporal_window_sec:
                    candidates.append((distance, anchor["resolved_pan"]))

        candidate_distances: Dict[str, float] = {}
        for distance, pan in candidates:
            candidate_distances[pan] = min(distance, candidate_distances.get(pan, float("inf")))
        ordered = sorted(candidate_distances.items(), key=lambda item: item[1])
        close = [pan for pan, distance in ordered if distance <= timestamp_window_sec]
        if close:
            selected = close[0]
            method = "timestamp_context"
        elif ordered:
            selected = ordered[0][0]
            method = "temporal_context"
        else:
            selected = None
            method = "unresolved"

        if selected:
            event.update(timeline_pan=selected, timeline_key=f"PAN-{selected}", identity_method=method, identity_status="ambiguous", identity_confidence="low" if len(ordered) > 1 else "medium")
            event["identity_candidates"] = [pan for pan, _ in ordered]
            event["identity_flags"].append(f"ambiguous_identity_{method}")
            if len(ordered) > 1:
                event["identity_flags"].append("multiple_temporal_candidates")
        else:
            event.update(identity_method=method, identity_status="ambiguous", identity_confidence="none", timeline_key="UNRESOLVED_ENTITY")
            event["identity_flags"].append("no_identity_context")

    return events


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
