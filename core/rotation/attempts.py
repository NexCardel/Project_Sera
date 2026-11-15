"""
core/rotation/attempts.py - SCK's attempt model
===============================================
One ``SckAttempt`` per ``(service_id, hwnd)``: the card staff are looking at, the client it is for
(or ``None`` for an ID Sera does not know), and the password candidate staff typed.

Why per window
--------------
The old ``_sck_candidates`` dict was keyed by service id alone: a login that worked in browser
window B saved the candidate typed for window A. Here a ``worked`` outcome only reaches the attempt
bound to the window it was seen in (extension mode has no window: ``hwnd == 0``).

Rules
-----
* The candidate is memory only and expires after ``CANDIDATE_TTL_SEC``; an expired candidate is
  dropped on every access.
* ``take_candidate`` returns the password and blanks the slot.
* Closing an attempt blanks its candidate.
* ``suppressed()`` lists the ``(client_id, service_id)`` pairs with an open attempt, so SCA does
  not refill the old password while a card is open.
* ``lock`` / ``is_locked`` keep "the portal reports this account locked" for the run.

Thread-safe. Never logs, never raises on a missing attempt.
"""

from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set, Tuple

CANDIDATE_TTL_SEC = 600.0

KIND_ROTATION = "rotation"
KIND_NEW_CLIENT = "new_client"


@dataclass
class SckAttempt:
    attempt_id: str
    service_id: int
    hwnd: int = 0
    client_id: Optional[int] = None
    client_name: str = ""
    portal: str = ""
    identity: str = ""              # PK-shaped value read on the page, upper-case, may be ""
    user_id: str = ""               # the login ID typed, may be ""
    kind: str = KIND_ROTATION
    opened_at: float = 0.0
    candidate: str = field(default="", repr=False)
    candidate_at: float = 0.0
    tested: bool = False
    closed: bool = False

    def __repr__(self) -> str:                      # never show the candidate
        return (f"SckAttempt({self.attempt_id!r}, service={self.service_id}, hwnd={self.hwnd}, "
                f"client={self.client_id}, kind={self.kind!r})")


class SckAttempts:
    """The open attempts. All methods are safe from any thread."""

    def __init__(self, monotonic: Callable[[], float] = time.monotonic,
                 ttl_sec: float = CANDIDATE_TTL_SEC) -> None:
        self._now = monotonic
        self._ttl = float(ttl_sec)
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._by_key: Dict[Tuple[int, int], SckAttempt] = {}
        self._locked: Set[Tuple[int, int]] = set()       # (client_id, service_id)

    # -- lifecycle -----------------------------------------------------------------------------
    def open(self, service_id: int, hwnd: int = 0, client_id: Optional[int] = None,
             client_name: str = "", portal: str = "", identity: str = "", user_id: str = "",
             kind: str = KIND_ROTATION) -> SckAttempt:
        """Open (or replace) the attempt of that window. A replaced attempt's candidate is blanked."""
        key = (int(service_id), int(hwnd or 0))
        with self._lock:
            old = self._by_key.get(key)
            if old is not None:
                self._blank(old)
                old.closed = True
            att = SckAttempt(attempt_id=f"sck{next(self._ids)}", service_id=key[0], hwnd=key[1],
                             client_id=client_id, client_name=str(client_name or ""),
                             portal=str(portal or ""), identity=str(identity or "").strip().upper(),
                             user_id=str(user_id or "").strip(), kind=kind, opened_at=self._now())
            self._by_key[key] = att
            return att

    def close(self, attempt: Optional[SckAttempt]) -> None:
        if attempt is None:
            return
        with self._lock:
            self._blank(attempt)
            attempt.closed = True
            key = (attempt.service_id, attempt.hwnd)
            if self._by_key.get(key) is attempt:
                del self._by_key[key]

    def close_service(self, service_id: int) -> None:
        with self._lock:
            for key in [k for k in self._by_key if k[0] == service_id]:
                att = self._by_key.pop(key)
                self._blank(att)
                att.closed = True

    def get(self, service_id: int, hwnd: int = 0) -> Optional[SckAttempt]:
        with self._lock:
            return self._by_key.get((int(service_id), int(hwnd or 0)))

    def by_id(self, attempt_id: str) -> Optional[SckAttempt]:
        with self._lock:
            for att in self._by_key.values():
                if att.attempt_id == attempt_id:
                    return att
        return None

    def for_service(self, service_id: int) -> List[SckAttempt]:
        with self._lock:
            return [a for k, a in self._by_key.items() if k[0] == int(service_id)]

    def find_for_outcome(self, service_id: int, hwnd: int, identity: str = "") -> Optional[SckAttempt]:
        """The attempt a page outcome belongs to: the same window; or, for an extension attempt
        (hwnd 0, the extension reports no window), the one whose identity equals the page's
        identity or when the page shows none."""
        sid, hwnd = int(service_id), int(hwnd or 0)
        identity = str(identity or "").strip().upper()
        with self._lock:
            if hwnd:
                hit = self._by_key.get((sid, hwnd))
                if hit is not None:
                    return hit
            att = self._by_key.get((sid, 0))
            if att is None:
                return None
            if not identity or not att.identity or att.identity == identity:
                return att
            return None

    # -- candidate -----------------------------------------------------------------------------
    def set_candidate(self, attempt: SckAttempt, password: str, tested: bool) -> None:
        with self._lock:
            if attempt.closed:
                return
            attempt.candidate = str(password or "")
            attempt.candidate_at = self._now()
            attempt.tested = bool(tested)

    def has_candidate(self, attempt: SckAttempt) -> bool:
        with self._lock:
            self._expire(attempt)
            return bool(attempt.candidate)

    def take_candidate(self, attempt: SckAttempt) -> str:
        """The candidate (or "" when none / expired); the slot is blanked."""
        with self._lock:
            self._expire(attempt)
            pw, attempt.candidate = attempt.candidate, ""
            return pw

    # -- SCA pause / lock ----------------------------------------------------------------------
    def suppressed(self) -> Set[Tuple[int, int]]:
        with self._lock:
            return {(a.client_id, a.service_id) for a in self._by_key.values()
                    if a.client_id is not None and not a.closed}

    def lock(self, client_id: Optional[int], service_id: int) -> None:
        if client_id is None:
            return
        with self._lock:
            self._locked.add((int(client_id), int(service_id)))

    def unlock(self, client_id: Optional[int], service_id: int) -> None:
        if client_id is None:
            return
        with self._lock:
            self._locked.discard((int(client_id), int(service_id)))

    def is_locked(self, client_id, service_id) -> bool:
        try:
            key = (int(client_id), int(service_id))
        except (TypeError, ValueError):
            return False
        with self._lock:
            return key in self._locked

    # -- internals (lock held) -----------------------------------------------------------------
    def _expire(self, attempt: SckAttempt) -> None:
        if attempt.candidate and self._now() - attempt.candidate_at > self._ttl:
            self._blank(attempt)

    @staticmethod
    def _blank(attempt: SckAttempt) -> None:
        attempt.candidate = ""
        attempt.candidate_at = 0.0
        attempt.tested = False


# One registry for the app (main.py and clipboard_watch share it).
_default = SckAttempts()


def default() -> SckAttempts:
    return _default


def suppression_reason(client_id, service_id) -> str:
    """"" when SCA may fill; otherwise the reason SCA must wait (a card is open, or locked)."""
    try:
        cid, sid = int(client_id), int(service_id)
    except (TypeError, ValueError):
        return ""
    if _default.is_locked(cid, sid):
        return "the portal reported this account locked (SCK)"
    if (cid, sid) in _default.suppressed():
        return "a new-password card is open for this client (SCK)"
    return ""
