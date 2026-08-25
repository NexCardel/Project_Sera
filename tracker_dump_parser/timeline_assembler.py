"""
Stage G & H: Timeline Assembler & Flag Detector
Assembles chronological per-client timelines, stitches sessions, and detects data-quality audit flags.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime
from tracker_dump_parser.session_stitcher import stitch_sessions, _parse_ts


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
    rolling_window_sec: int = 90
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
        confidence_levels = set()

        for e in client_events:
            if primary_cid is None and e.get("resolved_client_id") is not None:
                primary_cid = e.get("resolved_client_id")
            if primary_pan is None and e.get("resolved_pan"):
                primary_pan = e.get("resolved_pan")
            confidence_levels.add(e.get("identity_confidence", "unresolved"))

        # Best confidence
        if "exact_id" in confidence_levels:
            best_conf = "exact_id"
        elif "header_id" in confidence_levels:
            best_conf = "header_id"
        elif "pan_match" in confidence_levels:
            best_conf = "pan_match"
        else:
            best_conf = "unresolved"

        # Stitched sessions
        sessions = stitch_sessions(client_events, rolling_window_sec=rolling_window_sec)

        # Data quality audit flags
        flags = detect_data_quality_flags(client_events)

        clients_output[key] = {
            "entity_key": key,
            "client_id": primary_cid,
            "pan": primary_pan,
            "identity_confidence": best_conf,
            "total_events": len(client_events),
            "total_sessions": len(sessions),
            "flags": flags,
            "sessions": sessions,
            "events": client_events
        }

    return clients_output
