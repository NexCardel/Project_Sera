"""
core/rotation/detector.py — Sera Credential Keeper (SCK) failure detector.

Bridges SGT / UIA page-observation events to SCK rotation triggers and
provides the one guarded path through which a rotation-verified password
reaches the Sera vault.

Public API
----------
FailureDetector
    Watches page observations, classifies them with the SCC rules engine,
    and fires an ``on_trigger`` callback when a ``wrong_password`` outcome
    is detected for a client that is not already being rotated.

save_verified_for_rotation(db, password, pan, client_id, portal, actor)
    Portal-agnostic thin wrapper that writes a verified password to
    ``master.db`` through the normal ``db.*`` method calls — bypassing the
    Income-Tax-only guard in ``core/scc/save.py`` — and logs the event
    with metadata only (never the password itself).

Security invariants
-------------------
* ``save_verified_for_rotation`` never puts the password in any log,
  detail string, or return value visible outside volatile memory.
* ``FailureDetector.observe`` discards captcha lines before classifying;
  a captcha failure is neutral, not a password failure.
* All public methods are thread-safe.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional

from core.scc.scc_rules import get_rules

from .session import RotationSession

# Lines that look like a password failure but are actually captcha errors.
# We exclude them so a bad CAPTCHA never triggers a rotation.
_CAPTCHA_FRAGMENTS = (
    "invalid captcha",
    "enter valid captcha",
    "captcha is invalid",
    "please enter captcha",
)


def _is_captcha_line(line: str) -> bool:
    lower = line.strip().lower()
    return any(frag in lower for frag in _CAPTCHA_FRAGMENTS)


# ── Portal-agnostic guarded save ─────────────────────────────────────────────

def save_verified_for_rotation(
    db: Any,
    password: str,
    pan: str,
    client_id: Optional[int],
    portal: str,
    actor: str,
) -> None:
    """Write a rotation-verified password to the Sera vault for any portal.

    This is a thin, portal-agnostic wrapper around the ``db.*`` API that
    intentionally bypasses the Income-Tax-only guard in
    ``core.scc.save.save_verified``.  It is the **only** place SCK writes
    a password to persistent storage.

    The audit log entry records *metadata only*: client id, portal, method,
    and actor.  The password string is **never** included in any log line.

    Args:
        db:         Sera database handle (provides ``update_client_single_field``
                    and ``log_action``).
        password:   The verified password (plaintext, volatile — zeroed by caller
                    immediately after this call returns).
        pan:        PAN string (uppercase).
        client_id:  Sera client id.  ``None`` → skip (no client to update).
        portal:     Human-readable portal name, e.g. ``"GST"``, ``"TRACES"``.
        actor:      Device/user name for the audit log.
    """
    if not password or client_id is None:
        return

    # Resolve the password column for this portal.
    pwd_col_id: Optional[int] = None
    svc = db.get_service_for_portal(portal)
    if svc:
        pwd_col_id = svc.get("password_column_id")
    if not pwd_col_id:
        # Fallback: scan MCL columns for a label that contains the portal
        # name abbreviation and "pass".
        portal_lower = portal.strip().lower()
        for col in db.get_mcl_columns() or []:
            lbl = (col.get("label") or "").strip().lower()
            if "pass" in lbl and (portal_lower in lbl or _abbrev_match(portal_lower, lbl)):
                pwd_col_id = col["id"]
                break

    if pwd_col_id:
        db.update_client_single_field(
            client_id=client_id,
            column_id=pwd_col_id,
            value=password,
            actor=actor,
            log_action=False,
        )

    # Audit: metadata only — password MUST NOT appear in detail.
    db.log_action(
        actor=actor,
        action="update",
        client_id=client_id,
        detail=(
            f"SCK rotation: password verified and saved via Test & Fill bar "
            f"(portal={portal}, pan={pan}, method=SCK-DesktopBar)"
        ),
    )


def _abbrev_match(portal_lower: str, label: str) -> bool:
    """True if the first word of the portal name appears in the column label."""
    first = portal_lower.split()[0] if portal_lower.split() else ""
    return bool(first) and first in label


# ── Failure detector ─────────────────────────────────────────────────────────

class FailureDetector:
    """Watches SGT/UIA page events and fires rotation triggers on ``wrong_password``.

    Instantiate once, wire up ``on_trigger``, then call :meth:`observe` for
    every page read.

    Args:
        session:     The shared :class:`~core.rotation.session.RotationSession`.
        on_trigger:  Called with ``(client_id, pan, portal, hwnd)`` when a
                     fresh wrong-password outcome is detected and the client
                     is not already suppressed.  The callback runs on the
                     calling thread, so keep it lightweight.

    Thread safety:
        :meth:`observe` and :meth:`stop_suppression` are guarded by an
        internal lock and are safe to call from multiple threads.
    """

    def __init__(
        self,
        session: RotationSession,
        on_trigger: Callable[[Optional[int], str, str, int], None],
    ) -> None:
        self._session = session
        self._on_trigger = on_trigger
        self._lock = threading.Lock()
        # Maps client_id -> True once we have fired on_trigger for them,
        # to avoid double-firing before the rotation bar suppresses them.
        self._pending: Dict[Optional[int], bool] = {}

    def observe(
        self,
        lines: List[str],
        url: str,
        portal: str,
        hwnd: int,
        client_id: Optional[int],
        pan: str,
    ) -> None:
        """Classify a page observation and fire the rotation trigger if needed.

        Captcha-error lines are stripped from ``lines`` before classification
        so that a bad CAPTCHA entry is never treated as a password failure.

        Args:
            lines:      UIA text lines from the page.
            url:        Current browser URL / address.
            portal:     Human-readable portal name.
            hwnd:       Win32 HWND of the browser window.
            client_id:  Sera client id (``None`` if unregistered).
            pan:        PAN being logged in.
        """
        # Strip captcha noise before classification.
        clean_lines = [l for l in lines if not _is_captcha_line(l)]

        outcome = get_rules().classify(clean_lines, url, portal)
        if outcome is None or outcome.kind != "wrong_password":
            return

        with self._lock:
            # Skip if already suppressed (rotation bar is open) or already pending.
            if client_id is not None and client_id in self._session.suppressed_clients:
                return
            if self._pending.get(client_id):
                return
            self._pending[client_id] = True

        try:
            self._on_trigger(client_id, pan, portal, hwnd)
        except Exception:
            # Never let a callback exception suppress further detection.
            with self._lock:
                self._pending.pop(client_id, None)

    def stop_suppression(self, client_id: int) -> None:
        """Release suppression for ``client_id`` after rotation completes or fails.

        Also clears the pending-trigger flag so a subsequent genuine failure
        will fire again.

        Args:
            client_id:  The client to un-suppress.
        """
        self._session.suppressed_clients.discard(client_id)
        with self._lock:
            self._pending.pop(client_id, None)
