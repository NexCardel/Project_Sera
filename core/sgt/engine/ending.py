"""
core/sgt/engine/ending.py - how a session ends
==============================================
Idle sessions are parked (resumable), a finished session writes what is still waiting (unattributed
if its client was never known), logs its payload and raises the client-unknown alert.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Set

from core.sgt.specs.toolbox import submit_level
from core.vsdc.vsdc_alerts import device_name
from .policy import IDENTITY_PROBLEM, IDLE_END_SEC, IDLE_REASON, RESUME_WINDOW_SEC
from .session import Session, absorbs_after, client_value, session_to_json


class Ending:
    """Ends, parks and finishes sessions (part of SgtEngine)."""

    # ── Ending ───────────────────────────────────────────────────────────────────
    def _end_idle(self, now: float) -> None:
        for h in list(self._parked):
            kept = [c for c in self._parked[h] if now - c[0] <= RESUME_WINDOW_SEC]
            if kept:
                self._parked[h] = kept
            else:
                self._parked.pop(h, None)
        idle = [(hwnd, s, False) for hwnd, s in self._sessions.items() if now - s.last_seen > IDLE_END_SEC]
        idle += [(hwnd, s, True) for hwnd, lst in self._shelf.items() for s in lst
                 if now - s.last_seen > IDLE_END_SEC]
        for hwnd, s, aside in idle:
            if aside:
                lst = self._shelf.get(hwnd) or []
                if s in lst:
                    lst.remove(s)
                if not lst:
                    self._shelf.pop(hwnd, None)
            else:
                self._sessions.pop(hwnd, None)
            self._park(hwnd, s, now)

    def _park(self, hwnd: int, s: Session, now: float) -> None:
        """`s` (already off the window's lists) sat idle: it ends, and when it had a client (a PAN or
        GSTIN) and content it is also kept in `_parked` - and on disk - to be resumed within
        RESUME_WINDOW_SEC (same window and tab, or the same client named by any window)."""
        self._trail(s, "parked")
        snap = copy.deepcopy(session_to_json(s))
        has_content = s.has_content
        gap = now - s.last_seen
        self._end_one(s, IDLE_REASON)
        if has_content and client_value(snap) and gap <= RESUME_WINDOW_SEC:
            self._parked.setdefault(hwnd, []).append((now, snap))
            self._event(s, "session_parked", idle_sec=round(gap, 1))
            self._save_state(force=True)

    def would_be_payload(self, s: Session) -> Dict[str, Any]:
        dispatched: Set[str] = {str(x) for x in (self._dispatched_ids() or ())}
        datasets = []
        for slot in s.slots:
            d = dict(slot.values)
            d["record"] = slot.record
            if slot.arn:
                d["live_pipeline_also_captured"] = slot.arn in dispatched
            if slot.held:
                d["held"] = list(slot.held)
            datasets.append(d)
        profile = {k: v["value"] for k, v in s.profile.items()}
        return {
            "portal": s.portal,
            "client_profile": profile,
            "client_known": bool(self._client_ids(s)),
            "identity": s.confirm_note or "not confirmed",
            "datasets": datasets,
            "timeline": list(s.timeline),
            "device_name": device_name(),
        }

    def _finish(self, s: Session, reason: str) -> None:
        self._trail(s, "end", None, reason)
        self._retire_tabs(s)
        # Kept a while for a read it asked for that has not come back (absorb); after a logout, a
        # login page or the like, such a read is dropped instead (R1).
        self._closed[s.session_id] = (self._clock(), s, reason)
        self._closed.move_to_end(s.session_id)
        if not absorbs_after(reason):
            self._cut(s.session_id, reason)
        if not s.has_content:
            return
        if not self._client_pan(s):
            # The client was never identified: datasets that were waiting for it are written
            # UNATTRIBUTED (like VSDC247's client-unknown rows) rather than lost. The dataset
            # rules still apply.
            for slot in s.slots:
                if slot.held and slot.claimed:
                    slot.claimed = None
                    self._queue(s, slot)
        # A row still waiting for its form and period is written now, flagged: a filing is never lost.
        for slot in s.slots:
            if slot.held and not slot.waived and any(IDENTITY_PROBLEM in p for p in slot.held):
                slot.waived = True
                self._queue(s, slot)
                self._event(s, "dataset", change="written without form and period - session ended",
                            values=dict(slot.values))
                self._echo(f"[SGT] {self._tag} {self._label(slot.values)} written without form and period")
        # The dataset still being built is dispatched only if it is complete.
        self._close_draft(s, self.store.get().current_rules, f"session end ({reason})", s.last_url)
        self._carry_sdis(s)
        self._alert_unknown_client(s)
        payload = self.would_be_payload(s)
        stats = {"reads": s.reads, "ocr_reads": s.ocr_reads, "blind_pages": s.blind_pages,
                 "read_ms_total": round(s.read_ms, 1), "conflicts": s.conflicts,
                 "minutes": round((s.last_seen - s.started) / 60, 1)}
        self._event(s, "session_end", reason=reason, payload=payload, stats=stats)
        who = payload["client_profile"].get("name") or payload["client_profile"].get("pan") \
            or payload["client_profile"].get("gstin") or "client unknown"
        note = ""
        if payload["datasets"] and not payload["client_known"]:
            note = " - no PAN/GSTIN: written unattributed"
        self._echo(f"[SGT] {self._tag} session ended ({reason}): {who}, "
                   f"{len(payload['datasets'])} dataset(s), {len(payload['client_profile'])} profile field(s){note}")
        # Not on app quit or when SGT is switched off: nobody is looking at the pill then.
        quiet = ("shutdown", "switched off", "recovered")
        if not any(q in reason.lower() for q in quiet):
            n = len(payload["datasets"])
            if payload["datasets"] and not payload["client_known"]:
                self._hud(s, "prompt", "SGT session ended - client unknown", f"{n} dataset(s) with no PAN/GSTIN")
            else:
                self._hud(s, "logout", "SGT session logged", f"{who} • {n} dataset(s)")

    def _alert_unknown_client(self, s: Session) -> None:
        """A submission written with no client needs a human to attach it - the phone
        alert VSDC247 raises for its own client-unknown rows. Once per dataset; never for one
        held back by a dataset rule (it was not written)."""
        # Its rows carry the client only once confirmed (_client_ids), so a PAN merely seen does
        # not count: those rows went out unassigned too.
        if self._alert_unattributed is None or self._client_ids(s):
            return
        for slot in s.slots:
            if slot.held or slot.alerted or submit_level(slot.values.get("status")) < 2:
                continue
            slot.alerted = True
            self._event(s, "alert", change="submission with no client - phone alert", values=dict(slot.values))
            try:
                self._alert_unattributed(slot.values.get("form") or "", slot.first_page or s.last_url)
            except Exception as e:              # an alert must never break capture
                self._echo(f"[SGT] {self._tag} phone alert failed: {e}")
