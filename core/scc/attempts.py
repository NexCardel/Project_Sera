"""
core/scc/attempts.py - SCC-U steps 1-2: notice a password page, decide whether it needs an attempt
==================================================================================================
Autofill-tweaks blueprint G.2. A handler for SccHost (host.py), so it runs on SCC-U's own thread with
a read-only Observation and never touches SGT.

  1 NOTICE  SGT-C's own itr_pan result on the Income Tax password page ("#/login/password") names
            the PAN being logged into. The attempt belongs to that window (hwnd) and SGT session.
  2 DECIDE  a read-only client lookup: registered + SCC-verified -> nothing; registered, not
            verified -> attempt with the saved password as an extra row (D3, answered Yes);
            unregistered -> attempt (D4 decides on save). One card per PAN per window: closing it
            keeps it closed until the password page is reached again.

An attempt ends with no conclusion when SGT ends the session (a login link), when another PAN
shows on the password page in the same window, or after ATTEMPT_TTL_SEC. Outcomes from the page
wording (scc_rules.py), the card and the save come with the later steps; they call end() / close().
No password is held here: the saved-password row is read by the card when it is drawn.
"""

import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

ATTEMPT_TTL_SEC = 600.0                     # G.2 step 4: no conclusion after 10 minutes
PASSWORD_PAGE = re.compile(r"#/login/password", re.IGNORECASE)
PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
ITR_PORTALS = {"income tax", "itr"}
PAN_SPEC = "itr_pan"


@dataclass(frozen=True)
class ClientInfo:
    """What the lookup says about a PAN. Never a password."""
    client_id: int
    verified: bool


@dataclass
class Attempt:
    pan: str
    hwnd: int
    session_id: str
    client_id: Optional[int]        # None = the PAN is not a registered client (D4 on save)
    saved_row: bool                 # D3: the card shows the client's saved (unverified) password
    opened_at: float
    attempt_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ledger: List[str] = field(default_factory=list)   # "copied: <row label>" - labels only, never text
    manual: bool = False            # opened from Client Detail with no SGT window: hwnd < 0, only "This one worked" saves


Lookup = Callable[[str], Optional[ClientInfo]]


def db_lookup(db: Any) -> Lookup:
    """Read-only lookup on the Sera database: get_client_by_pan + is_client_scc_verified."""
    def lookup(pan: str) -> Optional[ClientInfo]:
        client = db.get_client_by_pan(pan)
        if not client or client.get("id") is None:
            return None
        return ClientInfo(int(client["id"]), bool(db.is_client_scc_verified(client_id=client["id"])))
    return lookup


def password_page_pan(obs: Any) -> Tuple[bool, str]:
    """(on the Income Tax password page, the PAN SGT read there or ""). Only SGT's itr_pan counts."""
    if str(obs.portal or "").strip().lower() not in ITR_PORTALS or not PASSWORD_PAGE.search(obs.url or ""):
        return False, ""
    hit = (obs.result.get("profile") or {}).get("pan") or {}
    pan = str(hit.get("value") or "").strip().upper()
    if hit.get("spec") != PAN_SPEC or not PAN_RE.match(pan):
        return True, ""
    return True, pan


class AttemptOpener:
    name = "scc-attempts"

    def __init__(self, lookup: Lookup, on_open: Optional[Callable[[Attempt], None]] = None,
                 on_end: Optional[Callable[[Attempt, str], None]] = None,
                 ttl_sec: float = ATTEMPT_TTL_SEC, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._lookup = lookup
        self._on_open = on_open
        self._on_end = on_end
        self._ttl = float(ttl_sec)
        self._now = monotonic
        self._lock = threading.Lock()
        self._open: Dict[int, Attempt] = {}          # hwnd -> its attempt (manual ones: a negative id)
        self._manual_seq = 0
        self._closed: Set[Tuple[int, str]] = set()   # (hwnd, pan) the card was closed for
        self._verified: Set[Tuple[int, str, str]] = set()   # (hwnd, pan, session) already looked up as verified

    # ── Read side (any thread) ───────────────────────────────────────────────────
    def attempt_for(self, hwnd: int) -> Optional[Attempt]:
        with self._lock:
            return self._open.get(hwnd)

    def open_attempts(self) -> List[Attempt]:
        with self._lock:
            return list(self._open.values())

    def register_manual(self, pan: str, client_id: Optional[int]) -> Tuple[Attempt, bool]:
        """Client Detail's MECP on an unverified Income Tax client. (attempt, is_new). If SGT already
        has an attempt for this PAN in a window, that one is returned (its card is up: is_new False).
        Otherwise a manual attempt is registered under a made-up negative window id; SGT adopts it
        if it later reads this PAN's password page (observe), else it lives ATTEMPT_TTL_SEC and only
        the card's "This one worked" can save. A second manual click replaces the first."""
        pan = str(pan or "").strip().upper()
        self.expire()
        replaced = None
        with self._lock:
            same = next((a for a in self._open.values() if a.pan == pan), None)
            if same is not None and not same.manual:
                return same, False
            if same is not None:
                replaced = self._open.pop(same.hwnd)
            self._manual_seq += 1
            att = Attempt(pan=pan, hwnd=-self._manual_seq, session_id="", client_id=client_id,
                          saved_row=client_id is not None, opened_at=self._now(), manual=True)
            self._open[att.hwnd] = att
        if replaced:
            self._ended(replaced, "no_conclusion")
        timer = threading.Timer(self._ttl + 1.0, self.expire)
        timer.daemon = True
        timer.start()
        return att, True

    # ── Handler (SCC-U's thread) ─────────────────────────────────────────────────
    def observe(self, obs: Any, hwnd: int, ctx: Any = None) -> Optional[str]:
        """Returns what it did, for tests: opened / verified / kept / closed_before / ended /
        (None = nothing to do)."""
        if str(obs.portal or "").strip().lower() not in ITR_PORTALS:
            return None
        self.expire()
        on_page, pan = password_page_pan(obs)
        with self._lock:
            att = self._open.get(hwnd)
            if not on_page:
                self._closed = {k for k in self._closed if k[0] != hwnd}
                self._verified = {k for k in self._verified if k[0] != hwnd}
            ended = None
            if att and (att.session_id != obs.session_id or (pan and pan != att.pan)):
                ended = self._open.pop(hwnd)
            elif att:
                return "kept"
        if ended:
            self._ended(ended, "no_conclusion")
        if not pan:
            return "ended" if ended else None
        with self._lock:
            manual = next((a for a in self._open.values() if a.manual and a.pan == pan), None)
            if manual is not None:
                # Client Detail's card is already up for this PAN: SGT now has its window, so it
                # becomes this window's attempt (the outcome reader follows it) - no second card.
                del self._open[manual.hwnd]
                manual.hwnd, manual.session_id, manual.manual = hwnd, obs.session_id, False
                self._open[hwnd] = manual
                return "adopted"
            if (hwnd, pan) in self._closed:
                return "closed_before"
            if (hwnd, pan, obs.session_id) in self._verified:
                return "verified"
        info = self._lookup(pan)
        if info is not None and info.verified:
            with self._lock:
                self._verified.add((hwnd, pan, obs.session_id))
            return "verified"
        att = Attempt(pan=pan, hwnd=hwnd, session_id=obs.session_id,
                      client_id=info.client_id if info else None, saved_row=info is not None,
                      opened_at=self._now())
        with self._lock:
            if hwnd in self._open:
                return "kept"
            self._open[hwnd] = att
        if self._on_open:
            self._on_open(att)
        return "opened"

    # ── Ending (the card's close, later steps, the clock) ────────────────────────
    def close(self, hwnd: int) -> None:
        """Staff closed the card: no new one for this PAN in this window until the password page
        is reached again."""
        with self._lock:
            att = self._open.pop(hwnd, None)
            if att:
                self._closed.add((hwnd, att.pan))
        if att:
            self._ended(att, "closed")

    def end(self, hwnd: int, reason: str) -> None:
        with self._lock:
            att = self._open.pop(hwnd, None)
        if att:
            self._ended(att, reason)

    def expire(self) -> None:
        now = self._now()
        with self._lock:
            old = [h for h, a in self._open.items() if now - a.opened_at > self._ttl]
            gone = [self._open.pop(h) for h in old]
        for att in gone:
            self._ended(att, "no_conclusion")

    def _ended(self, att: Attempt, reason: str) -> None:
        if self._on_end:
            self._on_end(att, reason)
