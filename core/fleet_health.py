"""
core/fleet_health.py - every PC's capture health, read from the shared settings table.
======================================================================================
Each PC shares its latest CaptureHealth record as the app_settings row `capture_health:<device_id>`
(Sera Sync replicates app_settings by key, so every PC sees every PC's record). This module only
turns those rows into panel lines and a copyable report. A record holds counts, times and fixed
sentences, never client data (core/vsdc/vsdc_health.py), and the report adds nothing to it.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.vsdc.vsdc_alerts import redact_identifiers
from core.vsdc.vsdc_health import FAILURE_STATES, SYNC_MIN_DEFAULT, SYNC_MIN_RANGE

KEY_PREFIX = "capture_health:"
SYNC_MIN_KEY = "capture_health_sync_min"
FRESH_EXTRA_MIN = 3             # a record is fresh for the sharing interval plus this
STALE_MIN_FLOOR = 10            # ... and silent after twice the interval, never under this
LAST_SEEN_DAYS = 7              # older than this, a PC is shown as "last seen", not as a problem
_INFO_STATES = frozenset({"slow", "alerts-unconfigured", "old-build"})
_QUIET_STATES = frozenset({"ok", "no-portal"})

# Panel levels: green fine, amber to look at, red act, grey off or gone
OK, WARN, ALERT, OFF = "ok", "warn", "alert", "off"


def key_for(device_id: str) -> str:
    return KEY_PREFIX + str(device_id)


def read_sync_min(get_setting) -> int:
    """The sharing interval in minutes from the app setting (1 to 60, default 15)."""
    try:
        n = int(str(get_setting(SYNC_MIN_KEY, str(SYNC_MIN_DEFAULT)) or SYNC_MIN_DEFAULT).strip())
    except (TypeError, ValueError):
        n = SYNC_MIN_DEFAULT
    return max(SYNC_MIN_RANGE[0], min(SYNC_MIN_RANGE[1], n))


class HealthWrite:
    """A record on its way to the settings table through the capture queue, so the capture thread
    never touches the database itself. It is not a tracker row: never dead-lettered or replayed."""
    __slots__ = ("key", "value")

    def __init__(self, key: str, value: str) -> None:
        self.key, self.value = key, value


@dataclass
class FleetRow:
    device_id: str
    device: str
    version: str
    state: str
    since: str
    at: str
    age_min: float
    sync_min: int
    level: str
    note: str                   # one short fixed phrase on why the line has its colour
    blind_min: int
    stalled_min: int
    rows_sent: int
    pending_update: str
    alerts_configured: bool
    blind_yesterday_min: int = 0
    stalled_yesterday_min: int = 0
    unattributed_rows: int = 0
    ocr_only_rows: int = 0
    rows_dead_letter: int = 0


def _when(text: Any) -> Optional[datetime]:
    try:
        d = datetime.fromisoformat(str(text))
        return d.replace(tzinfo=None) if d.tzinfo else d
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int:
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return 0


def thresholds(sync_min: int) -> tuple:
    """(fresh, stale) in minutes for a record shared every `sync_min` minutes."""
    return sync_min + FRESH_EXTRA_MIN, max(STALE_MIN_FLOOR, 2 * sync_min)


def parse_row(device_id: str, value: Any, now: datetime) -> Optional[FleetRow]:
    """One settings row as a FleetRow, or None when the value is not a usable record."""
    try:
        rec = json.loads(value) if isinstance(value, str) else None
    except ValueError:
        return None
    if not isinstance(rec, dict):
        return None
    at = _when(rec.get("at"))
    if at is None:
        return None
    sync_min = _int(rec.get("sync_min")) or SYNC_MIN_DEFAULT
    age = max(0.0, (now - at).total_seconds() / 60.0)
    state = str(rec.get("state") or "ok")
    fresh, stale = thresholds(sync_min)
    if age > LAST_SEEN_DAYS * 1440:
        level, note = OFF, "last seen"
    elif age > stale:
        level, note = (OFF, "off") if state in _QUIET_STATES else (ALERT, "not reporting")
    elif state in FAILURE_STATES:
        level, note = ALERT, state
    elif state in _INFO_STATES:
        level, note = WARN, state
    elif age > fresh:
        level, note = WARN, "late"
    else:
        level, note = OK, state
    return FleetRow(
        device_id=str(device_id), device=redact_identifiers(str(rec.get("device") or device_id))[:40],
        version=str(rec.get("version") or "")[:20], state=state, since=str(rec.get("since") or ""),
        at=str(rec.get("at") or ""), age_min=age, sync_min=sync_min, level=level, note=note,
        blind_min=_int(rec.get("blind_sec_today")) // 60, stalled_min=_int(rec.get("stalled_sec_today")) // 60,
        rows_sent=_int(rec.get("rows_sent")), pending_update=str(rec.get("pending_update") or "")[:20],
        alerts_configured=bool(rec.get("alerts_configured")),
        blind_yesterday_min=_int(rec.get("blind_sec_yesterday")) // 60,
        stalled_yesterday_min=_int(rec.get("stalled_sec_yesterday")) // 60,
        unattributed_rows=_int(rec.get("unattributed_rows")), ocr_only_rows=_int(rec.get("ocr_only_rows")),
        rows_dead_letter=_int(rec.get("rows_dead_letter")))


def read_fleet(settings: Dict[str, Any], now: Optional[datetime] = None) -> List[FleetRow]:
    """Every PC's line, worst first, then by name. `settings` is the app_settings table as a dict."""
    now = now or datetime.now()
    rows = []
    for key, value in (settings or {}).items():
        if isinstance(key, str) and key.startswith(KEY_PREFIX):
            row = parse_row(key[len(KEY_PREFIX):], value, now)
            if row is not None:
                rows.append(row)
    order = {ALERT: 0, WARN: 1, OK: 2, OFF: 3}
    rows.sort(key=lambda r: (order[r.level], r.device.lower(), r.device_id))
    return rows


def age_text(minutes: float) -> str:
    m = int(minutes)
    if m < 1:
        return "now"
    if m < 60:
        return f"{m} min ago"
    if m < 1440:
        return f"{m // 60} h ago"
    return f"{m // 1440} d ago"


def report_text(rows: List[FleetRow], now: Optional[datetime] = None) -> str:
    """The panel as plain text for Copy report. Device names, builds, states and counts only."""
    now = now or datetime.now()
    out = [f"Capture health at {now.strftime('%Y-%m-%d %H:%M')}"]
    if not rows:
        out.append("No PC has shared a capture health record yet.")
    for r in rows:
        extra = f", update {r.pending_update} waiting" if r.pending_update else ""
        out.append(f"{r.device} | build {r.version or '?'} | {r.state} ({r.note}) | since {r.since[11:16] or '?'}"
                   f" | record {age_text(r.age_min)} | blind {r.blind_min} min, stalled {r.stalled_min} min today"
                   f" | rows {r.rows_sent}{extra}")
    return redact_identifiers("\n".join(out))
