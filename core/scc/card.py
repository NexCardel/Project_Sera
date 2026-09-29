"""
core/scc/card.py - SCC-U step 3 (D1 = the extension's MECP card): feed the card, keep the ledger
=================================================================================================
Autofill-tweaks blueprint G.2 step 3 as re-planned 2026-09-29. When AttemptOpener opens an attempt,
SccCard sends the MECP message (scc_mode true) to the browser: the combinations from
db.generate_scc_passwords (empty ones left out, #11) plus the client's saved password (D3), and the
attempt id. The card lives in the portal tab that is already open; the extension watches nothing.

The rows live only here, in memory, for the life of the attempt. Copies are recognised by comparing
the clipboard text with those rows in memory (note_clipboard); the ledger keeps labels ("copied:
Combo 2"), never the text, and nothing here prints or logs a password. "This one worked" arrives as
a staff click (scc_row_worked); W5-6 turns it into the save through on_worked.
"""

import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from .attempts import ATTEMPT_TTL_SEC, Attempt

SAVED_ROW_LABEL = "Saved password"
REPEAT_COPY_S = 2.0          # the clipboard fires dataChanged more than once per copy

OpenCard = Callable[[dict, str, list, str, Optional[int], str], bool]   # service, pan, rows, title, client_id, attempt_id


class SccCard:
    def __init__(self, db: Any, open_card: OpenCard, close_card: Callable[[str], Any],
                 suppress: Optional[Callable[[int, float], Any]] = None,
                 release: Optional[Callable[[int], Any]] = None,
                 on_worked: Optional[Callable[[Attempt, str], Any]] = None,
                 on_closed: Optional[Callable[[Attempt], Any]] = None,
                 echo: Callable[[str], Any] = print, clock: Callable[[], float] = time.monotonic) -> None:
        self._db = db
        self._open_card = open_card
        self._close_card = close_card
        self._suppress = suppress
        self._release = release
        self._on_worked = on_worked
        self._on_closed = on_closed
        self._echo = echo
        self._now = clock
        self._lock = threading.Lock()
        self._attempts: Dict[str, Attempt] = {}
        self._rows: Dict[str, List[Tuple[str, str]]] = {}     # attempt id -> [(label, text)]
        self._last_copy: Dict[str, Tuple[str, float]] = {}    # attempt id -> (label, time)

    # ── AttemptOpener callbacks (SCC-U's thread) ─────────────────────────────────
    def open(self, att: Attempt) -> bool:
        """on_open: build the rows, keep them in memory, push the card. Returns whether a browser got it."""
        try:
            service = self._itr_service()
            if service is None:
                self._echo("[SCC] No Income Tax service is set up - no card.")
                return False
            rows = self._build_rows(att, service)
            if not rows:
                self._echo("[SCC] No password rows to show - no card.")
                return False
            with self._lock:
                self._attempts[att.attempt_id] = att
                self._rows[att.attempt_id] = rows
            if self._suppress and att.client_id is not None:
                self._suppress(att.client_id, ATTEMPT_TTL_SEC)
            title = f"PAN: {att.pan}" + ("" if att.client_id is not None else " (Unregistered)")
            sent = bool(self._open_card(service, att.pan, [
                {"id": i + 1, "label": label, "value": text} for i, (label, text) in enumerate(rows)
            ], title, att.client_id, att.attempt_id))
            if not sent:
                self._echo("[SCC] No browser connected - the card was not shown.")
            return sent
        except Exception as e:
            self._echo(f"[SCC] Card failed: {type(e).__name__}")
            return False

    def end(self, att: Attempt, reason: str) -> None:
        """on_end: forget the rows, let SCA arm on this client again, close the card if still up."""
        with self._lock:
            known = self._attempts.pop(att.attempt_id, None)
            self._rows.pop(att.attempt_id, None)
            self._last_copy.pop(att.attempt_id, None)
        if known is None:
            return
        if self._release and att.client_id is not None:
            self._release(att.client_id)
        if reason != "closed":
            try:
                self._close_card(att.attempt_id)
            except Exception as e:
                self._echo(f"[SCC] Card close failed: {type(e).__name__}")

    # ── Ledger (clipboard thread) ────────────────────────────────────────────────
    def note_clipboard(self, text: str) -> Optional[str]:
        """Compare clipboard text with the open attempts' rows, in memory. A match is recorded as
        "copied: <label>" in that attempt's ledger. Returns the label, or None. Nothing is stored."""
        if not text:
            return None
        with self._lock:
            for aid, rows in self._rows.items():
                for label, value in rows:
                    if text == value:
                        last = self._last_copy.get(aid)
                        now = self._now()
                        if last and last[0] == label and now - last[1] < REPEAT_COPY_S:
                            return label
                        self._last_copy[aid] = (label, now)
                        self._attempts[aid].ledger.append(f"copied: {label}")
                        return label
        return None

    def ledger(self, attempt_id: str) -> List[str]:
        with self._lock:
            att = self._attempts.get(attempt_id)
            return list(att.ledger) if att else []

    # ── Messages from the card (Qt thread) ───────────────────────────────────────
    def row_worked(self, attempt_id: str, row_label: str) -> Optional[Attempt]:
        """Staff pressed "This one worked" on a row. Only an open attempt and one of its own rows count."""
        with self._lock:
            att = self._attempts.get(attempt_id)
            if att is None or row_label not in [lb for lb, _ in self._rows.get(attempt_id, [])]:
                return None
            att.ledger.append(f"worked: {row_label}")
        if self._on_worked:
            self._on_worked(att, row_label)
        return att

    def card_closed(self, attempt_id: str) -> Optional[Attempt]:
        """Staff pressed x on the card."""
        with self._lock:
            att = self._attempts.get(attempt_id)
        if att is not None and self._on_closed:
            self._on_closed(att)
        return att

    # ── Rows ─────────────────────────────────────────────────────────────────────
    def _itr_service(self) -> Optional[dict]:
        import automation
        for svc in self._db.get_services():
            if automation.is_itr_service(svc):
                return svc
        return None

    def _build_rows(self, att: Attempt, service: dict) -> List[Tuple[str, str]]:
        rows: List[Tuple[str, str]] = []
        seen = set()
        for combo in self._db.generate_scc_passwords(att.pan):
            text = str(combo.get("value") or "")
            if not text or text in seen:
                continue
            label = str(combo.get("label") or f"Combo {combo.get('id')}")
            if label in [lb for lb, _ in rows]:
                label = f"{label} #{combo.get('id')}"
            seen.add(text)
            rows.append((label, text))
        if att.saved_row and att.client_id is not None:
            col = service.get("password_column_id")
            client = self._db.get_client(att.client_id) or {}
            saved = str((client.get("values") or {}).get(col) or "").strip() if col else ""
            if saved and saved not in seen:
                rows.append((SAVED_ROW_LABEL, saved))
        return rows
