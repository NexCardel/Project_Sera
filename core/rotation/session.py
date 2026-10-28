"""
core/rotation/session.py — Sera Credential Keeper (SCK) attempt lifecycle.

Manages the in-RAM lifecycle of a single credential-rotation attempt:

  AttemptSession  immutable snapshot of everything needed for one rotation
                  round (client, PAN, portal, browser hwnd).  The live
                  *candidate* password is held in a mutable slot
                  (_candidate) that is zeroed as soon as the attempt
                  completes, so it lives in volatile process memory only.

  RotationSession orchestrates multiple concurrent attempts:
                  - suppressed_clients: the set of client_ids for which
                    normal SCA autofill must be skipped while a rotation
                    is in progress.
                  - TTL sweep: stale sessions (>180 s idle) are evicted
                    automatically on each open().
                  - All public methods are thread-safe via a single lock.

No password is ever written to disk, logged, or passed through any
interface that touches persistent storage; the candidate travels only
through commit_candidate(), which is called immediately before the DB
write and zeroed right after.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional

ATTEMPT_TTL_SEC: float = 180.0   # 3-minute idle auto-close


@dataclass
class AttemptSession:
    """Volatile snapshot of one rotation attempt.

    Attributes:
        attempt_id:  Random UUID hex, unique per attempt.
        client_id:   Sera client id, or None if the PAN is not registered.
        pan:         PAN being rotated (uppercase, validated by caller).
        portal:      Human-readable portal name ("GST", "TRACES", …).
        hwnd:        Win32 HWND of the target browser window.
        opened_at:   ``time.monotonic()`` timestamp of creation.
        _candidate:  Volatile candidate password in plain text.  Do NOT
                     log, print, or pass to anything that touches disk.
                     Cleared by ``RotationSession.discard_candidate`` and
                     ``RotationSession.commit_candidate``.
    """

    attempt_id: str
    client_id: Optional[int]
    pan: str
    portal: str
    hwnd: int
    opened_at: float
    _candidate: str = field(default="", repr=False)


class RotationSession:
    """Thread-safe manager for concurrent credential-rotation attempts.

    Tracks which clients are being rotated so that the normal SCA
    autofill loop (``SccHost`` / ``SccCard``) skips them while a
    rotation bar is open.

    Usage::

        session = RotationSession()
        att = session.open(client_id=42, pan="ABCDE1234F",
                           portal="GST", hwnd=0x1A2B3C)
        # … inject candidate into the portal …
        pwd = session.commit_candidate(att.attempt_id)
        save_to_db(pwd)
        pwd = ""          # zero local copy immediately after use
        session.close(att.attempt_id)
    """

    def __init__(self) -> None:
        self._lock: threading.Lock = threading.Lock()
        self._attempts: Dict[str, AttemptSession] = {}   # attempt_id -> session
        self.suppressed_clients: set[int] = set()

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def open(
        self,
        client_id: Optional[int],
        pan: str,
        portal: str,
        hwnd: int,
    ) -> AttemptSession:
        """Open a new rotation attempt, suppressing the client from SCA autofill.

        A TTL sweep runs first to evict any sessions older than
        :data:`ATTEMPT_TTL_SEC`.

        Args:
            client_id:  Sera client id (``None`` if unregistered).
            pan:        PAN string (caller must ensure it is valid).
            portal:     Portal name, e.g. ``"GST"``, ``"TRACES"``.
            hwnd:       Win32 HWND of the target browser.

        Returns:
            The new :class:`AttemptSession`.
        """
        self._sweep()
        att = AttemptSession(
            attempt_id=uuid.uuid4().hex,
            client_id=client_id,
            pan=str(pan or "").strip().upper(),
            portal=str(portal or "").strip(),
            hwnd=int(hwnd),
            opened_at=time.monotonic(),
        )
        with self._lock:
            self._attempts[att.attempt_id] = att
            if client_id is not None:
                self.suppressed_clients.add(client_id)
        return att

    def set_candidate(self, attempt_id: str, candidate: str) -> None:
        """Store a candidate password in volatile RAM for the given attempt.

        The value is held in ``AttemptSession._candidate`` only.  It is
        never written to disk.

        Args:
            attempt_id:  The attempt to update.
            candidate:   The candidate password string (plaintext, volatile).

        Raises:
            KeyError: If ``attempt_id`` is not an open attempt.
        """
        with self._lock:
            att = self._attempts[attempt_id]
            att._candidate = str(candidate)

    def discard_candidate(self, attempt_id: str) -> None:
        """Zero the candidate password from RAM without committing it.

        Safe to call even if the attempt has already been closed.
        """
        with self._lock:
            att = self._attempts.get(attempt_id)
            if att is not None:
                att._candidate = ""

    def commit_candidate(self, attempt_id: str) -> str:
        """Return the candidate password and zero it from RAM immediately.

        Call this **only** immediately before the DB write; assign the
        returned string to a local variable, pass it to the save
        function, then overwrite the local with ``""`` right away.

        Args:
            attempt_id:  The attempt whose candidate to commit.

        Returns:
            The candidate password string (plaintext, volatile).

        Raises:
            KeyError: If ``attempt_id`` is not an open attempt.
        """
        with self._lock:
            att = self._attempts[attempt_id]
            pwd = att._candidate
            att._candidate = ""
        return pwd

    def close(self, attempt_id: str) -> None:
        """Remove the attempt from the active map and un-suppress the client.

        Safe to call multiple times; subsequent calls are no-ops.
        """
        with self._lock:
            att = self._attempts.pop(attempt_id, None)
            if att is not None:
                att._candidate = ""          # zero any lingering candidate
                if att.client_id is not None:
                    self.suppressed_clients.discard(att.client_id)

    def get(self, attempt_id: str) -> Optional[AttemptSession]:
        """Return the open :class:`AttemptSession` or ``None`` if not found."""
        with self._lock:
            return self._attempts.get(attempt_id)

    # ── TTL sweep ────────────────────────────────────────────────────────────

    def _sweep(self) -> None:
        """Evict attempts older than :data:`ATTEMPT_TTL_SEC` from the map."""
        now = time.monotonic()
        with self._lock:
            stale = [
                aid for aid, att in self._attempts.items()
                if (now - att.opened_at) > ATTEMPT_TTL_SEC
            ]
            for aid in stale:
                att = self._attempts.pop(aid)
                att._candidate = ""
                if att.client_id is not None:
                    self.suppressed_clients.discard(att.client_id)
