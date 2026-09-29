"""
core/scc/outcome.py - SCC-U step 4: read how a login attempt ended
==================================================================
Autofill-tweaks blueprint G.2 step 4. A handler for SccHost, registered after AttemptOpener
(attempts.py). It reads the later pages of the same window (hwnd) AND the same SGT session as the
password page, classifies each with scc_rules.json (scc_rules.py) and moves the attempt on:

  worked          the logged-in header; any PAN or name the page shows must agree with the attempt's
                  PAN / client, else no conclusion. Reported once (step 5 decides which row).
  wrong_password  x on the last Sera password copied since the previous refusal, the next row is
                  highlighted. The same message still on screen on the next read is not a new refusal.
  locked          stop: the card says so and suggests no row. Nothing more is read for the attempt.
  neutral         OTP, secure access, e-verification: keep waiting.
  no_conclusion   forgot / reset password, another PAN, the window closed. A login link (new SGT
                  session), another PAN on the password page and 10 minutes are ended by AttemptOpener.

The wording shown to staff comes from scc_rules.json ("messages") only. Extra reads after each copy
are asked for by the card's on_copy (SccHost.open_read_window), not here. Page text stays in memory;
nothing here prints or logs it, and no password is ever seen (both UIA readers skip password boxes).
"""

import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .attempts import ITR_PORTALS, PAN_RE, Attempt, AttemptOpener
from .scc_rules import RuleSet, get_rules

_WORD = re.compile(r"[A-Z]{2,}")


@dataclass
class _State:
    consumed: int = 0                  # ledger entries already accounted for by a refusal
    last_wrong_line: Optional[str] = None
    done: str = ""                     # "worked" / "locked": nothing more to read
    failed: List[str] = field(default_factory=list)


def _default_is_window(hwnd: int) -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.user32.IsWindow(int(hwnd)))
    except Exception:
        return True


def db_client_names(db: Any) -> Callable[[int], List[str]]:
    """The client's name-like values (company / name / client / proprietor columns), read-only."""
    def names(client_id: int) -> List[str]:
        client = db.get_client(client_id) or {}
        values = client.get("values") or {}
        out = []
        for col in db.get_mcl_columns() or []:
            lbl = str(col.get("label") or "").lower()
            if any(k in lbl for k in ("company", "name", "client", "proprietor")) and "pass" not in lbl:
                v = str(values.get(col.get("id")) or "").strip()
                if v:
                    out.append(v)
        return out
    return names


def names_disagree(page_name: str, client_names: List[str]) -> bool:
    """True only when the page's name shares no word with any of the client's names. Unknown on
    either side agrees: a stored name may be a placeholder or a firm's trading name."""
    page = set(_WORD.findall(page_name.upper()))
    known = set()
    for n in client_names:
        known |= set(_WORD.findall(str(n).upper()))
    return bool(page and known and not (page & known))


class OutcomeReader:
    name = "scc-outcome"

    def __init__(self, opener: AttemptOpener, card: Any = None,
                 rules: Optional[Callable[[], RuleSet]] = None,
                 on_outcome: Optional[Callable[[Attempt, str, str], Any]] = None,
                 client_names: Optional[Callable[[int], List[str]]] = None,
                 is_window: Callable[[int], bool] = _default_is_window) -> None:
        self._opener = opener
        self._card = card                        # SccCard: mark_failed / show_stop
        self._rules = rules or get_rules
        self._on_outcome = on_outcome            # (attempt, kind, detail) - steps 5 and 7
        self._client_names = client_names
        self._is_window = is_window
        self._lock = threading.Lock()
        self._states: Dict[str, _State] = {}     # attempt id -> what has been read so far

    def failed(self, attempt_id: str) -> List[str]:
        """Rows refused in this attempt (step 5 never credits one)."""
        with self._lock:
            st = self._states.get(attempt_id)
            return list(st.failed) if st else []

    # ── Handler (SCC-U's thread) ─────────────────────────────────────────────────
    def observe(self, obs: Any, hwnd: int, ctx: Any = None) -> Optional[str]:
        """Returns what it concluded, for tests: worked / wrong_password / wrong_password_typed /
        same_message / locked / neutral / no_conclusion / another_pan / other_name /
        (None = nothing to do). Closed windows are ended by the sweep, whatever window this page is."""
        if str(obs.portal or "").strip().lower() not in ITR_PORTALS:
            return None
        self._sweep()
        att = self._opener.attempt_for(hwnd)
        if att is None or att.session_id != obs.session_id:
            return None
        with self._lock:
            st = self._states.setdefault(att.attempt_id, _State())
        if st.done:
            return None

        hit = (obs.result.get("profile") or {}).get("pan") or {}
        page_pan = str(hit.get("value") or "").strip().upper()
        if PAN_RE.match(page_pan) and page_pan != att.pan:
            self._end(att, "no_conclusion", "another PAN")
            return "another_pan"

        out = self._rules().classify(obs.lines, obs.url, obs.portal)
        kind = out.kind if out else None
        if kind != "wrong_password":
            st.last_wrong_line = None
        if out is None:
            return None

        if kind == "wrong_password":
            if out.line == st.last_wrong_line:
                return "same_message"
            st.last_wrong_line = out.line
            ledger = list(att.ledger)
            copies = [e[len("copied: "):] for e in ledger[st.consumed:] if e.startswith("copied: ")]
            st.consumed = len(ledger)
            if not copies:
                self._report(att, "wrong_password", "")       # a password staff typed themselves
                return "wrong_password_typed"
            label = copies[-1]
            with self._lock:
                if label not in st.failed:
                    st.failed.append(label)
            if self._card is not None:
                self._card.mark_failed(att, label, self._rules().message("wrong_password"))
            self._report(att, "wrong_password", label)
            return "wrong_password"

        if kind == "locked":
            st.done = "locked"
            if self._card is not None:
                self._card.show_stop(att, self._rules().message("locked"))
            self._report(att, "locked", "")
            return "locked"

        if kind == "worked":
            if out.name and att.client_id is not None and self._client_names is not None \
                    and names_disagree(out.name, self._client_names(att.client_id)):
                self._end(att, "no_conclusion", "another name")
                return "other_name"
            st.done = "worked"
            self._report(att, "worked", out.name)
            return "worked"

        if kind == "neutral":
            return "neutral"

        # no_conclusion: forgot / reset password (the rules read the address or the exact heading,
        # so the password page's own 'Forgot Password?' link never matches).
        self._end(att, "no_conclusion", out.rule)
        return "no_conclusion"

    # ── Ending ───────────────────────────────────────────────────────────────────
    def _sweep(self) -> None:
        """Ends attempts whose browser window has closed and forgets finished attempts."""
        live = self._opener.open_attempts()
        for att in live:
            if not self._is_window(att.hwnd):
                self._end(att, "no_conclusion", "window closed")
        ids = {a.attempt_id for a in self._opener.open_attempts()}
        with self._lock:
            for aid in [a for a in self._states if a not in ids]:
                del self._states[aid]

    def _end(self, att: Attempt, reason: str, why: str) -> None:
        if self._opener.attempt_for(att.hwnd) is not att:
            return
        self._report(att, reason, why)
        self._opener.end(att.hwnd, reason)

    def _report(self, att: Attempt, kind: str, detail: str) -> None:
        if self._on_outcome:
            self._on_outcome(att, kind, detail)
