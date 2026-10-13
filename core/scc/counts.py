"""
core/scc/counts.py - SCC-U step 7: close-out and counts
========================================================
Autofill-tweaks blueprint G.2 step 7. Counts are recorded locally in
~/AmanAssociates_Sera/scc/counts.json and shown in Settings → SCC.

Counts tracked:
  attempts       - attempts opened
  worked         - login worked (outcome = "worked")
  failed         - wrong password (outcome = "wrong_password")
  locked         - locked out (outcome = "locked")
  no_conclusion  - no conclusion (outcome = "no_conclusion")
  asked          - card asked which row
  saved          - password saved
  not_understood - outcome was None (portal wording changed or not recognized)

The counter hooks into:
  1. AttemptOpener.on_open -> count attempts
  2. WhichOne(then=) -> count outcomes
  3. SccCard.on_worked / SccSaver.after -> count saves
  4. SccCard.ask_which -> count asked
  5. SccCard.card_closed / end -> close card and clear clipboard
"""

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional


@dataclass
class Counts:
    attempts: int = 0
    worked: int = 0
    failed: int = 0
    locked: int = 0
    no_conclusion: int = 0
    asked: int = 0
    saved: int = 0
    not_understood: int = 0

    def to_dict(self) -> dict:
        return {
            "attempts": self.attempts,
            "worked": self.worked,
            "failed": self.failed,
            "locked": self.locked,
            "no_conclusion": self.no_conclusion,
            "asked": self.asked,
            "saved": self.saved,
            "not_understood": self.not_understood,
        }

    @staticmethod
    def from_dict(d: dict) -> "Counts":
        return Counts(
            attempts=d.get("attempts", 0),
            worked=d.get("worked", 0),
            failed=d.get("failed", 0),
            locked=d.get("locked", 0),
            no_conclusion=d.get("no_conclusion", 0),
            asked=d.get("asked", 0),
            saved=d.get("saved", 0),
            not_understood=d.get("not_understood", 0),
        )


def _sera_scc_dir() -> Path:
    """Get ~/AmanAssociates_Sera/scc directory, creating it if needed."""
    base = Path.home() / "AmanAssociates_Sera" / "scc"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _counts_file() -> Path:
    """Get ~/AmanAssociates_Sera/scc/counts.json."""
    return _sera_scc_dir() / "counts.json"


def load_counts() -> Counts:
    """Load counts from disk, or return empty counts if the file doesn't exist."""
    path = _counts_file()
    if path.exists():
        try:
            with open(path, "r") as f:
                data = json.load(f)
                return Counts.from_dict(data)
        except Exception:
            pass
    return Counts()


def save_counts(counts: Counts) -> None:
    """Save counts to disk."""
    path = _counts_file()
    with open(path, "w") as f:
        json.dump(counts.to_dict(), f)


class SccCounter:
    """Counts SCC outcomes. Hooked into the flow via callbacks."""

    def __init__(self, echo: Callable[[str], Any] = print) -> None:
        self._counts = load_counts()
        self._echo = echo
        self._lock = threading.Lock()
        self._on_counts_changed: Optional[Callable[[Counts], Any]] = None

    def set_counts_changed_callback(self, callback: Callable[[Counts], Any]) -> None:
        """Set a callback to be called when counts change (for UI updates)."""
        with self._lock:
            self._on_counts_changed = callback

    def get_counts(self) -> Counts:
        """Get the current counts."""
        with self._lock:
            return Counts(**self._counts.to_dict())

    def on_attempt_opened(self) -> None:
        """Increment attempts count."""
        with self._lock:
            self._counts.attempts += 1
            self._save_and_notify()

    def on_card_asked(self) -> None:
        """Increment asked count."""
        with self._lock:
            self._counts.asked += 1
            self._save_and_notify()

    def on_password_saved(self) -> None:
        """Increment saved count."""
        with self._lock:
            self._counts.saved += 1
            self._save_and_notify()

    def on_outcome(self, kind: Optional[str]) -> None:
        """Increment outcome count."""
        if kind is None:
            kind = "not_understood"

        with self._lock:
            if kind == "worked":
                self._counts.worked += 1
            elif kind == "wrong_password":
                self._counts.failed += 1
            elif kind == "locked":
                self._counts.locked += 1
            elif kind == "no_conclusion":
                self._counts.no_conclusion += 1
            elif kind == "not_understood":
                self._counts.not_understood += 1
            # Other kinds (neutral, same_message, etc.) are not counted
            self._save_and_notify()

    def _save_and_notify(self) -> None:
        """Save counts to disk and notify listeners."""
        try:
            save_counts(self._counts)
        except Exception as e:
            self._echo(f"[SCC] Failed to save counts: {type(e).__name__}")

        if self._on_counts_changed:
            try:
                self._on_counts_changed(Counts(**self._counts.to_dict()))
            except Exception as e:
                self._echo(f"[SCC] Counts callback failed: {type(e).__name__}")
