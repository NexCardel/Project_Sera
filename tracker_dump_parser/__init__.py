"""
Sera Tracker Dump Parser & Action Decoder Pipeline
Main package entry point integrating Stages A through H.
"""

import os
from typing import Dict, Any, List, Optional

from tracker_dump_parser.entry_splitter import split_entries
from tracker_dump_parser.header_parser import parse_header
from tracker_dump_parser.json_parser import parse_json_body
from tracker_dump_parser.identity_resolver import resolve_identity
from tracker_dump_parser.action_decoder import decode_action
from tracker_dump_parser.session_stitcher import stitch_sessions
from tracker_dump_parser.timeline_assembler import assemble_client_timelines, detect_data_quality_flags


def parse_dump_to_timelines(
    dump_source: str,
    client_roster: Optional[Dict[str, Any]] = None,
    rolling_window_sec: int = 90
) -> Dict[str, Any]:
    """
    Executes the full 8-Stage Tracker Dump Parsing and Action Decoding Pipeline:
    [raw text/file] -> Stage A (Split) -> Stage B (Header) -> Stage C (JSON)
                    -> Stage D (Identity) -> Stage E (Action) -> Stage F (Sessions)
                    -> Stage G (Timelines) -> Stage H (Quality Flags & Output)

    Returns:
    {
        "total_entries": int,
        "valid_events_count": int,
        "quarantine_count": int,
        "quarantine": List[Dict[str, Any]],
        "clients": Dict[str, Any],  # Per-client timeline outputs
        "global_flags": List[str]
    }
    """
    # 1. Load raw content
    if os.path.isfile(dump_source):
        with open(dump_source, "r", encoding="utf-8", errors="ignore") as f:
            raw_text = f.read()
    else:
        raw_text = str(dump_source)

    # Stage A: Split Entries
    chunks = split_entries(raw_text)

    decoded_events: List[Dict[str, Any]] = []
    quarantine: List[Dict[str, Any]] = []

    for chunk in chunks:
        # Stage B: Parse Header
        header = parse_header(chunk)
        entry_num = header.get("entry_num")

        # Stage C: Parse JSON Body
        json_body, parse_err = parse_json_body(chunk)
        if parse_err or json_body is None:
            quarantine.append({
                "source_entry": entry_num,
                "header": header,
                "error": parse_err,
                "raw_chunk": chunk
            })
            continue

        # Stage D: Identity Resolution
        ident = resolve_identity(header, json_body, client_roster=client_roster)

        # Stage E: Action Decoding & Outcome Assessment
        url = json_body.get("url") or ""
        act_info = decode_action(url, json_body)

        # Extract effective session_id from json_body or raw_payload
        raw_p = json_body.get("raw_payload") or {}
        if isinstance(raw_p, str):
            try:
                import json as _j
                raw_p = _j.loads(raw_p)
            except Exception:
                raw_p = {}
        ses_id = json_body.get("session_id") or (raw_p.get("session_id") if isinstance(raw_p, dict) else None)

        event_record = {
            "source_entry": entry_num,
            "timestamp": header.get("timestamp"),
            "portal": header.get("portal") or act_info.get("portal_code"),
            "portal_code": act_info.get("portal_code"),
            "category": act_info.get("category"),
            "action": act_info.get("action"),
            "outcome": act_info.get("outcome"),
            "reason": act_info.get("reason"),
            "arn": header.get("arn_ack_no") or json_body.get("arn"),
            "session_id": ses_id,
            "capture_method": header.get("capture_method"),
            "status": header.get("status"),
            "url": url,
            "is_unknown_endpoint": act_info.get("is_unknown_endpoint"),
            
            # Identity details
            "resolved_client_id": ident.get("resolved_client_id"),
            "resolved_pan": ident.get("resolved_pan"),
            "identity_confidence": ident.get("identity_confidence"),
            "identity_flags": ident.get("identity_flags"),
            "body_client_id": ident.get("body_client_id"),
            "header_client_id": ident.get("header_client_id"),

            "json_payload": json_body
        }
        decoded_events.append(event_record)

    # Stage G & H: Assemble per-client timelines and detect flags
    clients = assemble_client_timelines(decoded_events, rolling_window_sec=rolling_window_sec)

    # Collect global flags
    global_flags = []
    for c_data in clients.values():
        for f in c_data.get("flags", []):
            if f not in global_flags:
                global_flags.append(f)

    return {
        "total_entries": len(chunks),
        "valid_events_count": len(decoded_events),
        "quarantine_count": len(quarantine),
        "quarantine": quarantine,
        "clients": clients,
        "global_flags": global_flags
    }
