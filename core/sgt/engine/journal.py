"""
core/sgt/engine/journal.py - what SGT tells the outside
=======================================================
The HUD pill, the local log (one JSON object per line), the daily digest and the portal-change
watch, and the capture-worker heartbeat counts. None of these may ever disturb capture.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .policy import HUD_TAG, LOG_PREFIX, sgt_data_dir
from .session import Session


class Journal:
    """HUD events, the log, the digest and the heartbeat counts (part of SgtEngine)."""

    # ── HUD pill ─────────────────────────────────────────────────────────────────
    def _hud(self, s: Session, event_type: str, title: str, subtitle: str,
             values: Optional[Dict[str, str]] = None) -> None:
        if self._notify is None:
            return
        v = values or {}
        ctx = {"portal": s.portal, "form": v.get("form"), "filing_pref": v.get("filing_type"),
               "period": v.get("period")}
        try:
            self._notify(event_type, title, f"{subtitle} • {HUD_TAG}".strip(" •"), ctx)
        except Exception as e:                  # the pill must never break capture
            self._echo(f"[SGT] {self._tag} HUD event failed: {e}")

    @staticmethod
    def _who(s: Session) -> str:
        p = s.profile
        return (p.get("name") or p.get("pan") or p.get("gstin") or {}).get("value", "")

    # ── Portal-change watch ──────────────────────────────────────────────────────
    def set_digest(self, digest: Optional[Callable[[date], Optional[Dict[str, Any]]]]) -> None:
        """digest(today) -> {"title", "subtitle", "counts"} when a daily capture digest was made, else
        None (not the admin PC, or already made today). main.py sets it; the text of the digest is
        kept by the digest itself, only counts reach the log."""
        self._digest = digest

    def _daily_digest(self, today: date) -> None:
        """Starts the day's digest on a thread of its own: it reads the whole tracker and writes
        settings, which must never hold up the capture tick it was called from."""
        digest = self._digest
        if digest is None:
            return

        def run() -> None:
            try:
                self._digest_made = digest(today)
            except Exception as ex:
                self._echo(f"[SGT] {self._tag} the daily digest failed: {type(ex).__name__}")

        import threading
        self._digest_thread = threading.Thread(target=run, name="sgt-daily-digest", daemon=True)
        self._digest_thread.start()

    def _report_digest(self) -> None:
        """A digest made since the last tick reaches the log and the pill (counts only)."""
        made, self._digest_made = self._digest_made, None
        if not made:
            return
        self._log({"event": "digest", **(made.get("counts") or {})})
        if self._notify is not None:
            try:
                self._notify("prompt", made.get("title") or "Capture digest ready", made.get("subtitle") or "", {})
            except Exception:
                pass

    def _daily_health_check(self) -> None:
        self._report_digest()
        today = self._today()
        if self._checked_day == today:
            return
        if self._stats is None and self._digest is None:
            return
        self._checked_day = today
        self._daily_digest(today)
        if self._stats is None:
            return
        for alert in self._stats.alerts(today):
            self._echo(f"[SGT] portal-change watch: {alert['detail']}")
            self._log({"event": "health", **alert})
            if self._notify is not None:
                try:
                    self._notify("prompt", "SGT: a portal may have changed", alert["detail"], {"portal": alert["portal"]})
                except Exception:
                    pass

    def _append_log(self, rec: Dict[str, Any]) -> None:
        try:
            d = self._log_dir or sgt_data_dir()
            d.mkdir(parents=True, exist_ok=True)
            path = d / f"{LOG_PREFIX}{date.today().isoformat()}.jsonl"
            if str(path) not in self._log_checked:
                self._log_checked.add(str(path))
                self._seal_torn_line(path)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:
            self._echo(f"[SGT] {self._tag} could not write the log: {e}")

    @staticmethod
    def _seal_torn_line(path: Path) -> None:
        """An app killed mid-write leaves the log's last line without its newline; the next record would
        be glued onto it and both lines would be unreadable. Ends the torn line first."""
        try:
            if path.exists() and path.stat().st_size:
                with open(path, "rb+") as f:
                    f.seek(-1, os.SEEK_END)
                    if f.read(1) != b"\n":
                        f.write(b"\n")
        except OSError:
            pass

    def _log(self, rec: Dict[str, Any]) -> None:
        self._append_log({"ts": datetime.now().isoformat(timespec="seconds"), **rec})

    def _event(self, s: Session, event: str, **data: Any) -> None:
        self._append_log({"ts": datetime.now().isoformat(timespec="seconds"), "event": event,
                          "session": s.session_id, "portal": s.portal, "capture_browser": s.browser, **data})

    @staticmethod
    def _report(callback: Optional[Callable[..., None]], *args: Any) -> None:
        """Tells the health monitor something; it must never disturb capture."""
        if callback is not None:
            try:
                callback(*args)
            except Exception:
                pass

    @staticmethod
    def _fresh_diag() -> Dict[str, Any]:
        # "skipped" stays 0 (blind pages of every portal go to OCR now); kept for the heartbeat's format.
        return {"reads": 0, "blind": 0, "skipped": 0, "ocr": 0, "busy": 0, "dropped": 0,
                "last_lines": None, "last_source": None, "portal": None}

    def take_diag(self) -> Dict[str, Any]:
        """Page reads since the last call - total, blind (UIA gave nothing usable), read by OCR,
        dropped (absorb: torn, too old, owner gone), skipped because UIA was still busy -
        the last read's line count and source, the open sessions. Resets the counts."""
        out, self._diag = self._diag, self._fresh_diag()
        out["sessions"] = len(self._sessions)
        return out
