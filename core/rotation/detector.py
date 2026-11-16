"""
core/rotation/detector.py — Sera Credential Keeper (SCK) failure detector.

Bridges SGT / UIA page-observation events to SCK rotation triggers and
provides the one guarded path through which a rotation-verified password
reaches the Sera vault.

Public API
----------
save_verified_for_rotation(db, password, pan, client_id, portal, actor)
    Portal-agnostic thin wrapper that writes a verified password to
    ``master.db`` through the normal ``db.*`` method calls — bypassing the
    Income-Tax-only guard in ``core/scc/save.py`` — and logs the event
    with metadata only (never the password itself).

Security invariants
-------------------
* ``save_verified_for_rotation`` never puts the password in any log,
  detail string, or return value visible outside volatile memory.
* All public methods are thread-safe.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

# ── Portal-agnostic guarded save ─────────────────────────────────────────────

@dataclass(frozen=True)
class RotationSave:
    """What ``save_verified_for_rotation`` did. Never holds the password."""
    outcome: str            # "saved" / "replaced" / "verified"
    client_id: int
    client_name: str
    pan: str
    portal: str


def save_verified_for_rotation(
    db: Any,
    password: str,
    pan: str,
    client_id: Optional[int],
    portal: str,
    actor: str,
    service_id: Optional[int] = None,
    client_name: str = "",
    method: str = "SCK-Widget",
    verified: bool = True,
) -> Optional[RotationSave]:
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
        pan:        PAN string (uppercase), or ``""`` when the portal has none.
        client_id:  Sera client id.  ``None`` → skip (no client to update).
        portal:     Human-readable portal name, e.g. ``"GST"``, ``"TRACES"``.
        actor:      Device/user name for the audit log.
        service_id: The service row the password belongs to (preferred over the
                    portal name for resolving the password column).
        client_name: For the result/toast; read from the database when empty.
        method:     Shown in the audit line ("SCK-Widget", "SMTI", "MECP", "SCA").
        verified:   False for "Save Directly" (the password was never tested on the portal):
                    the client is not tagged SCC-verified and the audit line says ``untested``.

    Returns:
        ``RotationSave`` (outcome saved / replaced / verified), or ``None`` when
        there was nothing to write (no password, no client, no password column).
    """
    password = str(password or "").strip()
    if not password or client_id is None:
        return None
    try:
        client_id = int(client_id)
    except (TypeError, ValueError):
        return None

    pan = str(pan or "").strip().upper()
    portal = str(portal or "").strip()
    pwd_col_id = _password_column(db, service_id, portal)
    if not pwd_col_id:
        return None

    current = ""
    try:
        client = db.get_client(client_id) or {}
        current = str((client.get("values") or {}).get(pwd_col_id) or "").strip()
    except Exception:
        current = ""

    if current == password:
        outcome = "verified"
    else:
        db.update_client_single_field(
            client_id=client_id,
            column_id=pwd_col_id,
            value=password,
            actor=actor,
            log_action=False,
        )
        outcome = "replaced" if current else "saved"

    # A tested password (it came through Fill & Test and the portal accepted it) is verified:
    # SCC's card stops asking about it. An untested Save Directly is not tagged.
    if verified:
        try:
            db.tag_client_scc_verified(client_id=client_id, combo_label=method, actor=actor)
        except Exception:
            pass

    # Audit: metadata only — password MUST NOT appear in detail.
    db.log_action(
        actor=actor,
        action="update",
        client_id=client_id,
        detail=(
            f"SCK: {outcome} password entered on the portal widget "
            f"(portal={portal}, pan={pan}, method={method}"
            + ("" if verified else ", untested") + ")"
        ),
    )
    return RotationSave(outcome=outcome, client_id=client_id,
                        client_name=str(client_name or "").strip() or _client_name(db, client_id),
                        pan=pan, portal=portal)


def _password_column(db: Any, service_id: Optional[int], portal: str) -> Optional[int]:
    """The master-column that holds this service's password."""
    col = None
    if service_id:
        try:
            svc = db.get_service(service_id)
        except Exception:
            svc = None
        col = (svc or {}).get("password_column_id")
    if not col:
        svc = db.get_service_for_portal(portal)
        col = (svc or {}).get("password_column_id")
    if not col:
        # Fallback: scan MCL columns for a label that contains the portal
        # name abbreviation and "pass".
        portal_lower = portal.strip().lower()
        for c in db.get_mcl_columns() or []:
            lbl = (c.get("label") or "").strip().lower()
            if "pass" in lbl and (portal_lower in lbl or _abbrev_match(portal_lower, lbl)):
                return c["id"]
    return col


def _client_name(db: Any, client_id: int) -> str:
    try:
        client = db.get_client(client_id) or {}
        for c in db.get_mcl_columns() or []:
            lbl = (c.get("label") or "").lower()
            if any(k in lbl for k in ("company", "name", "client", "proprietor")) and "pass" not in lbl:
                val = str((client.get("values") or {}).get(c["id"]) or "").strip()
                if val:
                    return val
    except Exception:
        pass
    return "Client"


def _abbrev_match(portal_lower: str, label: str) -> bool:
    """True if the first word of the portal name appears in the column label."""
    first = portal_lower.split()[0] if portal_lower.split() else ""
    return bool(first) and first in label
