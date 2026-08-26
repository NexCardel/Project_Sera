"""
Stage F: Session Stitcher
Groups decoded events into isolated sessions by session_id or rolling temporal window,
and stitches multi-step wizard workflows into logical session nodes.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timezone


def _portal_group(event: Dict[str, Any]) -> str:
    value = event.get("portal_code") or event.get("portal") or ""
    text = str(value).strip().lower()
    if "income tax" in text or "incometax" in text or text == "it":
        return "IT"
    if "gst" in text:
        return "GST"
    if "traces" in text:
        return "TRACES"
    return text


def _event_text(event: Dict[str, Any]) -> str:
    return " ".join(str(event.get(key) or "") for key in ("action", "category", "url", "status")).lower()


def _is_lifecycle_boundary(event: Dict[str, Any], group: List[Dict[str, Any]]) -> bool:
    """Identify explicit workflow boundaries, without treating token refreshes as one."""
    text = _event_text(event)
    if any(term in text for term in ("logout", "logged out", "signout", "sign out")):
        return True
    # A later login after an established non-authenticated workflow is a new
    # visit; the first login in a group is, of course, its starting event.
    is_login = any(term in text for term in ("login", "logged in", "authentication"))
    has_non_auth = any("authentication" not in _event_text(previous) for previous in group)
    return is_login and has_non_auth


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
    rolling_window_sec: int = 90,
    temporal_context_sec: int = 900
) -> List[Dict[str, Any]]:
    """
    Groups a list of decoded events belonging to a client into cohesive session objects.
    Priority:
    1. Direct session token
    2. Same-portal timestamp context (<= rolling_window_sec)
    3. Same-portal temporal context (<= temporal_context_sec), marked low confidence

    Events are never discarded because their session evidence is weak.
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
        layer = "session_token" if ev_ses_id else "timestamp_context"
        if not curr_group:
            is_new = True
        elif dt and last_dt:
            gap = (dt.timestamp() - last_dt.timestamp())
            # Header labels may include endpoint-specific descriptions. Use
            # the normalized portal code when available (e.g. both entries
            # can be Income Tax/IT while their display labels differ).
            same_portal = _portal_group(curr_group[-1]) == _portal_group(ev)

            # A new token can be issued during one continuous portal visit.
            # Keep it in the current session when the surrounding identity
            # timeline and portal context already agree. A long gap or portal
            # change still starts a genuinely new session.
            if gap > temporal_context_sec or not same_portal:
                is_new = True
            elif _is_lifecycle_boundary(ev, curr_group) and gap > rolling_window_sec:
                is_new = True
            elif ev_ses_id and curr_session_id and ev_ses_id != curr_session_id:
                layer = "token_refresh"
            elif gap > rolling_window_sec:
                layer = "temporal_context"
        elif ev_ses_id and curr_session_id and ev_ses_id != curr_session_id:
            # Without timestamps, retain the conservative hard boundary.
            is_new = True

        if is_new and curr_group:
            raw_sessions.append(curr_group)
            curr_group = []
            curr_session_id = None

        curr_group.append(ev)
        ev["session_stitch_layer"] = layer
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
        stitch_layers = {e.get("session_stitch_layer") for e in s_events}
        if "session_token" in stitch_layers:
            stitch_confidence = "high"
        elif "timestamp_context" in stitch_layers:
            stitch_confidence = "medium"
        else:
            stitch_confidence = "low"

        structured_sessions.append({
            "session_num": s_idx,
            "session_id": eff_ses_id or f"Session-{s_idx}",
            "start_time": s_events[0].get("timestamp"),
            "end_time": s_events[-1].get("timestamp"),
            "duration_seconds": max(0.0, duration_sec),
            "event_count": len(s_events),
            "has_wizard_flow": has_wizard_flow,
            "stitch_confidence": stitch_confidence,
            "stitch_layers": sorted(layer for layer in stitch_layers if layer),
            "events": s_events
        })

    return structured_sessions
