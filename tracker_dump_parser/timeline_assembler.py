"""
Stage G & H: Timeline Assembler & Flag Detector
Assembles chronological per-client timelines, stitches sessions, and detects data-quality audit flags.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime
from tracker_dump_parser.session_stitcher import stitch_sessions, _parse_ts
from tracker_dump_parser.name_resolver import choose_client_name


def detect_data_quality_flags(events: List[Dict[str, Any]]) -> List[str]:
    """
    Scans a client's chronological events for data quality anomalies:
    1. identity_mismatch
    2. repeat_action_within_N_sec (<= 180s)
    3. failure_with_no_retry
    4. unknown_endpoint
    """
    flags = []
    
    # 1. Identity mismatches
    for e in events:
        for f in e.get("identity_flags", []):
            if f not in flags:
                flags.append(f)

    # 2. Unknown endpoints
    unknown_urls = set()
    for e in events:
        if e.get("is_unknown_endpoint"):
            unknown_urls.add(e.get("url", "N/A"))
    for u in unknown_urls:
        flags.append(f"unknown_endpoint ({u})")

    # 3. Repeat action within 180s
    for i in range(len(events) - 1):
        e1 = events[i]
        e2 = events[i + 1]
        t1 = _parse_ts(e1.get("timestamp"))
        t2 = _parse_ts(e2.get("timestamp"))
        if t1 and t2 and abs((t2.timestamp() - t1.timestamp())) <= 180:
            if e1.get("action") == e2.get("action") and e1.get("arn") and e1.get("arn") == e2.get("arn"):
                flag_msg = f"repeat_action_within_3min: '{e1.get('action')}' (Entries #{e1.get('source_entry')} & #{e2.get('source_entry')})"
                if flag_msg not in flags:
                    flags.append(flag_msg)

    # 4. Failure with no subsequent retry/success
    failed_actions = {}
    for idx, e in enumerate(events):
        act = e.get("action")
        if e.get("outcome") == "failure":
            failed_actions[act] = (idx, e.get("source_entry"), e.get("reason"))
        elif e.get("outcome") == "success" and act in failed_actions:
            # Resolved by subsequent success
            del failed_actions[act]

    for act, (idx, src_entry, reason) in failed_actions.items():
        flags.append(f"failure_with_no_retry: '{act}' at Entry #{src_entry} (Reason: {reason})")

    return flags


def assemble_client_timelines(
    decoded_events: List[Dict[str, Any]],
    rolling_window_sec: int = 90,
    temporal_context_sec: int = 900
) -> Dict[str, Any]:
    """
    Groups all decoded dump events by client/PAN, stitches into sessions,
    and returns a structured multi-client timeline dictionary.
    """
    # Group events by client key (resolved_client_id or PAN)
    grouped: Dict[str, List[Dict[str, Any]]] = {}

    for ev in decoded_events:
        cid = ev.get("resolved_client_id")
        pan = ev.get("resolved_pan")
        timeline_key = ev.get("timeline_key")

        if timeline_key:
            group_key = timeline_key
            grouped.setdefault(group_key, []).append(ev)
            continue
        
        if cid is not None:
            group_key = f"CLI-{cid:05d}" if isinstance(cid, int) else f"CLI-{cid}"
        elif pan:
            group_key = f"PAN-{pan}"
        else:
            group_key = "UNRESOLVED_ENTITY"

        if group_key not in grouped:
            grouped[group_key] = []
        grouped[group_key].append(ev)

    clients_output = {}

    for key, client_events in grouped.items():
        # Sort chronologically
        client_events.sort(key=lambda e: _parse_ts(e.get("timestamp")).timestamp() if _parse_ts(e.get("timestamp")) else 0.0)

        # Primary metadata
        primary_cid = None
        primary_pan = None
        name_candidates = []
        confidence_levels = set()

        for e in client_events:
            if primary_cid is None and e.get("resolved_client_id") is not None:
                primary_cid = e.get("resolved_client_id")
            if primary_pan is None and e.get("resolved_pan"):
                primary_pan = e.get("resolved_pan")
            name_candidates.extend(e.get("client_name_candidates", []))
            confidence_levels.add(e.get("identity_confidence", "unresolved"))

        # Best confidence, retaining the new evidence vocabulary while
        # remaining compatible with older confidence labels.
        confidence_rank = {"high": 4, "exact_id": 4, "header_id": 3, "medium": 3, "pan_match": 3, "low": 2, "none": 1, "unresolved": 1}
        best_conf = max(confidence_levels or {"unresolved"}, key=lambda value: confidence_rank.get(value, 0))
        name_info = choose_client_name(name_candidates)

        # Stitched sessions
        sessions = stitch_sessions(client_events, rolling_window_sec=rolling_window_sec, temporal_context_sec=temporal_context_sec)

        # Data quality audit flags
        flags = detect_data_quality_flags(client_events)

        clients_output[key] = {
            "entity_key": key,
            "client_id": primary_cid,
            "pan": primary_pan,
            "client_name": name_info["client_name"],
            "client_name_confidence": name_info["client_name_confidence"],
            "client_name_candidates": name_info["client_name_candidates"],
            "identity_confidence": best_conf,
            "total_events": len(client_events),
            "total_sessions": len(sessions),
            "flags": flags,
            "quarantine_events": [e.get("source_entry") for e in client_events if e.get("identity_status") == "ambiguous"],
            "sessions": sessions,
            "events": client_events
        }

    return clients_output
