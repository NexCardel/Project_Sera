"""Sera Sync v3 -- Sync Status panel data helpers (blueprint SS5, WP P3-8).

Pure DB-reading/writing functions behind the Sera Sync dialog's panel: pending
outgoing change counts per member, the parked-change count, the conflicts
list and its "keep" / "use discarded value" resolution, and the shadow-check
summary. No PySide6 import here (SS0 rule 7) -- ui/dialogs/sera_sync_dialog.py
calls these and does the Qt part.
"""

from __future__ import annotations

import datetime
import json
import os
from typing import Optional

import sync_schema

# Every reason sync_apply.py currently writes to _sync_conflicts (P3-4 log note 10): both are
# written the instant a row is deleted/tombstoned, for the columns a losing concurrent edit
# touched. By the time anyone looks at the panel, the row named in the conflict is gone --
# "use discarded value" can't just UPDATE it back into existence (see that function's docstring
# for why re-creating it isn't done here). Exported so callers can give a specific explanation
# instead of a generic "could not apply".
DELETED_ROW_REASONS = frozenset({"edit discarded by delete", "edit after delete discarded"})


def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _parse_iso(ts_str: str) -> datetime.datetime:
    ts_str = ts_str.strip()
    if ts_str.endswith("Z") or ts_str.endswith("z"):
        ts_str = ts_str[:-1] + "+00:00"
    dt = datetime.datetime.fromisoformat(ts_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def parked_count(conn) -> int:
    row = conn.execute("SELECT COUNT(*) FROM _sync_parked").fetchone()
    return int(row[0]) if row else 0


def parked_stats(conn, now: Optional[datetime.datetime] = None) -> tuple[int, Optional[int]]:
    """Returns ``(count, oldest_age_seconds)`` for ``_sync_parked`` in ``conn``.
    If count is 0 or no timestamps exist, oldest_age_seconds is None.
    """
    count_row = conn.execute("SELECT COUNT(*) FROM _sync_parked").fetchone()
    count = int(count_row[0]) if count_row else 0
    if count == 0:
        return 0, None

    rows = conn.execute("SELECT first_at FROM _sync_parked WHERE first_at IS NOT NULL").fetchall()
    if not rows:
        return count, None

    if now is None:
        now = _now_utc()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)

    oldest_dt = None
    for (fa,) in rows:
        if not fa:
            continue
        try:
            dt = _parse_iso(str(fa))
            if oldest_dt is None or dt < oldest_dt:
                oldest_dt = dt
        except Exception:
            continue

    if oldest_dt is None:
        return count, None

    age_sec = max(0, int((now - oldest_dt).total_seconds()))
    return count, age_sec


def parked_summary(conns, now: Optional[datetime.datetime] = None) -> tuple[int, Optional[int]]:
    """Aggregates parked stats across multiple DB connections (e.g. master + raw),
    returning ``(total_count, oldest_age_seconds)``."""
    total_count = 0
    oldest_age = None
    for conn in conns:
        if conn is None:
            continue
        cnt, age = parked_stats(conn, now=now)
        total_count += cnt
        if age is not None:
            if oldest_age is None or age > oldest_age:
                oldest_age = age
    return total_count, oldest_age


def format_age(seconds: int | float | None) -> str:
    """Formats age in seconds into human-readable duration (e.g. '0s', '45s', '1m', '1h', '2h', '1d', '7d')."""
    if seconds is None or seconds < 0:
        return "unknown"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    rem_min = minutes % 60
    if hours < 24:
        return f"{hours}h {rem_min}m" if rem_min else f"{hours}h"
    days = hours // 24
    rem_hr = hours % 24
    return f"{days}d {rem_hr}h" if rem_hr else f"{days}d"



def own_vector(conn) -> dict:
    """``{origin: max_seq}`` this PC's own change log for one DB file."""
    return {o: int(s) for o, s in conn.execute("SELECT origin, max_seq FROM _sync_vector")}


def peer_vector(conn, device_id: str) -> dict:
    """``{origin: max_seq}`` last recorded as seen by ``device_id`` for one DB file (P3-5
    stores this at the end of every session, in ``_sync_peer_vectors``)."""
    return {o: int(s) for o, s in conn.execute(
        "SELECT origin, max_seq FROM _sync_peer_vectors WHERE device_id = ?", (device_id,))}


def pending_outgoing(own: dict, peer: dict) -> int:
    """How many of this PC's changes (one or more DB files combined by the caller) a member
    hasn't seen yet, i.e. hasn't acked in a session."""
    return sum(max(0, seq - peer.get(origin, 0)) for origin, seq in own.items())


def list_conflicts(conn, limit: int = 200) -> list:
    """Most recent ``_sync_conflicts`` rows, newest first."""
    rows = conn.execute(
        "SELECT id, at, tbl, row_key, col, kept, discarded, reason FROM _sync_conflicts "
        "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for cid, at, tbl, row_key, col, kept, discarded, reason in rows:
        out.append({
            "id": cid,
            "at": at,
            "table": tbl,
            "row_key": row_key,
            "column": col,
            "kept": json.loads(kept) if kept is not None else None,
            "discarded": json.loads(discarded) if discarded is not None else None,
            "reason": reason,
        })
    return out


def dismiss_conflict(conn, conflict_id: int) -> None:
    """"Keep": acknowledges the row already holds the value that won. No data change."""
    conn.execute("DELETE FROM _sync_conflicts WHERE id = ?", (conflict_id,))


def use_discarded_value(conn, conflict_id: int) -> bool:
    """"Use discarded value": writes the conflict's discarded value back with a plain SQL
    UPDATE, the same as a person retyping the field -- the P3-3 capture triggers pick it up
    like any other edit and it replicates normally.

    Every conflict sync_apply.py writes today (``DELETED_ROW_REASONS``) is recorded the instant
    its row is deleted, so by the time this runs the row is already gone and the UPDATE can't
    reach it -- re-creating a deleted row (un-tombstoning it, re-inserting with the right gid,
    translating any wire-form gid values in FK columns back to local ids for composite keys)
    is real new scope, not a one-line fix, and isn't done here (P3-8 review, deviation). This
    still checks generically (rather than special-casing those two reasons) so it also does the
    right thing for any future conflict type sync_apply.py might write against a row that's
    still live.

    Returns False (nothing changed, and the conflict is left in place -- NOT deleted, so a
    failed attempt never destroys the only record of the discarded edit) if the conflict is
    gone, its table isn't in the registry, its column isn't a real column, or the row it
    pointed at doesn't exist (the common case today, see above). Only deletes the conflict row
    when the UPDATE actually changed something.
    """
    row = conn.execute(
        "SELECT tbl, row_key, col, discarded FROM _sync_conflicts WHERE id = ?",
        (conflict_id,)).fetchone()
    if row is None:
        return False
    tbl, row_key_json, col, discarded_json = row
    spec = sync_schema.REGISTRY.get(tbl)
    if spec is None or not spec.row_key:
        return False
    try:
        key_values = json.loads(row_key_json)
        value = json.loads(discarded_json) if discarded_json is not None else None
    except (TypeError, ValueError):
        return False
    if not isinstance(key_values, list) or len(key_values) != len(spec.row_key):
        return False
    live_columns = {r[1] for r in conn.execute(f'PRAGMA table_info("{tbl}")')}
    if col not in live_columns:
        return False
    where = " AND ".join(f'"{c}" = ?' for c in spec.row_key)
    cur = conn.execute(f'UPDATE "{tbl}" SET "{col}" = ? WHERE {where}', [value, *key_values])
    applied = cur.rowcount > 0
    if applied:
        conn.execute("DELETE FROM _sync_conflicts WHERE id = ?", (conflict_id,))
    return applied


def shadow_check_summary(app_dir, tail_lines: int = 5) -> list:
    """Last few lines of ``logs/sync_shadow.log`` (P3-7), oldest of the tail first. Empty
    list if shadow mode has never logged a check on this PC."""
    log_path = os.path.join(str(app_dir), "logs", "sync_shadow.log")
    if not os.path.exists(log_path):
        return []
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = [ln.rstrip("\n") for ln in f if ln.strip()]
    except OSError:
        return []
    return lines[-tail_lines:]
