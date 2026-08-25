"""
Stage F: Session Stitcher
Groups decoded events into isolated sessions by session_id or rolling temporal window,
and stitches multi-step wizard workflows into logical session nodes.
"""

from typing import List, Dict, Any
from datetime import datetime, timezone


def _parse_ts(ts_str: str) -> Optional[datetime]:
    if not ts_str:
        return None
    try:
        clean = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(clean)
    except Exception:
        return None


def stitch_sessions(
    events: List[Dict[str, Any]],
    rolling_window_sec: int = 90
) -> List[Dict[str, Any]]:
    """
    Groups a list of decoded events belonging to a client into cohesive session objects.
    Priority:
    1. Direct session_id token clustering
    2. Rolling temporal gap (> rolling_window_sec) / portal change clustering
    """
    if not events:
        return []

    # Sort chronologically by timestamp
    def _sort_key(e):
        dt = _parse_ts(e.get("timestamp"))
        return dt.timestamp() if dt else 0.0

    sorted_events = sorted(events, key=_sort_key)

    raw_sessions: List[List[Dict[str, Any]]] = []
    curr_group: List[Dict[str, Any]] = []
    curr_session_id = None
    last_dt = None

    for ev in sorted_events:
        ev_ses_id = ev.get("session_id")
        dt = _parse_ts(ev.get("timestamp"))

        is_new = False
        if not curr_group:
            is_new = True
        elif ev_ses_id and curr_session_id and ev_ses_id != curr_session_id:
            is_new = True
        elif (not ev_ses_id or not curr_session_id) and dt and last_dt:
            gap = (dt.timestamp() - last_dt.timestamp())
            if gap > rolling_window_sec:
                is_new = True

        if is_new and curr_group:
            raw_sessions.append(curr_group)
            curr_group = []
            curr_session_id = None

        curr_group.append(ev)
        if ev_ses_id:
            curr_session_id = ev_ses_id
        if dt:
            last_dt = dt

    if curr_group:
        raw_sessions.append(curr_group)

    # Build structured session objects
    structured_sessions = []
    for s_idx, s_events in enumerate(raw_sessions, start=1):
        t0 = _parse_ts(s_events[0].get("timestamp"))
        t_end = _parse_ts(s_events[-1].get("timestamp"))
        duration_sec = (t_end.timestamp() - t0.timestamp()) if (t0 and t_end) else 0.0

        # Effective session token
        eff_ses_id = None
        for e in s_events:
            if e.get("session_id"):
                eff_ses_id = e.get("session_id")
                break

        # Check for wizard flow nodes (Validate -> Draft -> Submit)
        wizard_steps = [e for e in s_events if "Wizard" in e.get("category", "") or "Submission" in e.get("category", "")]
        has_wizard_flow = len(wizard_steps) >= 2

        structured_sessions.append({
            "session_num": s_idx,
            "session_id": eff_ses_id or f"Session-{s_idx}",
            "start_time": s_events[0].get("timestamp"),
            "end_time": s_events[-1].get("timestamp"),
            "duration_seconds": max(0.0, duration_sec),
            "event_count": len(s_events),
            "has_wizard_flow": has_wizard_flow,
            "events": s_events
        })

    return structured_sessions
