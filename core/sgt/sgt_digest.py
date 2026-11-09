"""
core/sgt/sgt_digest.py - the daily capture digest (layer 4: what Sera cannot watch is listed).
=============================================================================================
Once a day, on the admin PC (every PC when the office has no admin), the digest counts what the
other layers could only notice, not fix: PCs with blind or stalled minutes, rows without a client,
rows only OCR found, rows that could not be saved, ITR returns still "Submitted (Not Verified)"
after DIGEST_UNVERIFIED_DAYS days, PCs on an old build and PCs with phone alerts not set up.

The text holds device names and counts, never a client value. SgtShadow runs it on its first tick
of each day (set_digest); the app keeps the text for Settings -> Tracker -> Capture health.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from core.fleet_health import FleetRow, read_fleet

DIGEST_UNVERIFIED_DAYS = 7
DAY_KEY = "capture_digest_day"
TEXT_KEY = "capture_digest_text"
_HUD_TITLE = "Capture digest ready"
_NOT_VERIFIED = "Submitted (Not Verified)"


@dataclass
class Digest:
    day: str
    lines: List[str] = field(default_factory=list)
    counts: Dict[str, int] = field(default_factory=dict)

    def text(self) -> str:
        return "\n".join([f"Capture digest for {self.day}"] + self.lines)

    def subtitle(self) -> str:
        parts = [f"{n} {label}" for key, label in _HUD_LABELS if (n := self.counts.get(key, 0))]
        return (", ".join(parts) or "Nothing needs attention")[:140]


_HUD_LABELS = (
    ("blind_or_stalled_pcs", "PC(s) blind or stalled"),
    ("unattributed_rows", "row(s) without a client"),
    ("ocr_only_rows", "row(s) found only by OCR"),
    ("rows_not_saved", "row(s) not saved"),
    ("unverified_itr_old", "ITR return(s) unverified for days"),
    ("old_build_pcs", "PC(s) on an old build"),
    ("alerts_unconfigured_pcs", "PC(s) without phone alerts"),
)


def _vkey(version: str) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", version or ""))


def _names(pairs: List[tuple]) -> str:
    return ", ".join(f"{n} {m}" if m is not None else n for n, m in pairs[:6]) + (" ..." if len(pairs) > 6 else "")


def build_digest(rows: List[FleetRow], unverified_old: int, today: date) -> Digest:
    """One line per category, every category always listed. `rows` are the PCs' latest records."""
    d = Digest(day=today.isoformat())
    blind = []
    for r in rows:
        if r.at[:10] < today.isoformat():       # its counters have not rolled over midnight yet: they are yesterday's
            mins = r.blind_min + r.stalled_min
        else:
            mins = r.blind_yesterday_min + r.stalled_yesterday_min
        if mins:
            blind.append((r.device, f"{mins} min"))
    newest = max((_vkey(r.version) for r in rows), default=())
    old = [(r.device, None) for r in rows if r.pending_update or (_vkey(r.version) and _vkey(r.version) < newest)]
    no_alerts = [(r.device, None) for r in rows if not r.alerts_configured]
    unattr = sum(r.unattributed_rows for r in rows)
    ocr = sum(r.ocr_only_rows for r in rows)
    lost = sum(r.rows_dead_letter for r in rows)
    d.counts = {"blind_or_stalled_pcs": len(blind), "unattributed_rows": unattr, "ocr_only_rows": ocr,
                "rows_not_saved": lost, "unverified_itr_old": int(unverified_old),
                "old_build_pcs": len(old), "alerts_unconfigured_pcs": len(no_alerts)}
    d.lines = [
        f"PCs with blind or stalled minutes: {len(blind)}" + (f" ({_names(blind)})" if blind else ""),
        f"Rows without a client (counted since each PC last started): {unattr}",
        f"Rows found only by OCR (counted since each PC last started): {ocr}",
        f"Rows that could not be saved and wait to be retried: {lost}",
        f"ITR returns still \"{_NOT_VERIFIED}\" after {DIGEST_UNVERIFIED_DAYS} days: {int(unverified_old)}",
        f"PCs on an old build: {len(old)}" + (f" ({_names(old)})" if old else ""),
        f"PCs with phone alerts not set up: {len(no_alerts)}" + (f" ({_names(no_alerts)})" if no_alerts else ""),
    ]
    return d


def count_old_unverified(containers: List[dict], now: Optional[datetime] = None,
                         days: int = DIGEST_UNVERIFIED_DAYS) -> int:
    """ITR returns (client, form, period) whose best status is still "Submitted (Not Verified)" and
    whose newest capture is older than `days`. `containers` are the tracker's slim client containers."""
    from core.ltt.feed import LEVELS, entry_form, ladder
    cutoff = ((now or datetime.now()) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    best: Dict[tuple, list] = {}
    for i, c in enumerate(containers or []):
        who = c.get("identity_key") or c.get("pan") or c.get("gstin") or i
        for h in c.get("filing_history") or []:
            if not str(h.get("portal") or "").strip().upper().startswith("ITR"):
                continue
            lvl = ladder(h.get("status"), h.get("arn"))
            if lvl not in LEVELS:
                continue
            at = str(h.get("created_at") or "").replace("T", " ")[:16]
            key = (who, entry_form(h), str(h.get("period_label") or ""))
            cur = best.get(key)
            rank = LEVELS.index(lvl)
            if cur is None or rank > cur[0] or (rank == cur[0] and at > cur[1]):
                best[key] = [rank, at]
    return sum(1 for rank, at in best.values() if rank == LEVELS.index(_NOT_VERIFIED) and at and at < cutoff)


class DailyDigest:
    """The callable SgtShadow runs once a day. Everything it needs comes in as functions so a test
    needs no database: settings() -> the whole settings dict, save(key, value), is_admin() ->
    bool, unverified() -> the count from count_old_unverified."""

    def __init__(self, settings: Callable[[], Dict[str, Any]], save: Callable[[str, str], None],
                 is_admin: Callable[[], bool], unverified: Callable[[], int],
                 now: Callable[[], datetime] = datetime.now) -> None:
        self._settings, self._save, self._is_admin, self._unverified, self._now = settings, save, is_admin, unverified, now

    def __call__(self, today: date) -> Optional[Dict[str, Any]]:
        if not self._is_admin():
            return None
        everything = self._settings()
        if everything.get(DAY_KEY) == today.isoformat():
            return None
        try:
            old = int(self._unverified())
        except Exception:
            old = 0
        digest = build_digest(read_fleet(everything, self._now()), old, today)
        self._save(TEXT_KEY, digest.text())
        self._save(DAY_KEY, today.isoformat())
        return {"title": _HUD_TITLE, "subtitle": digest.subtitle(), "counts": digest.counts}
