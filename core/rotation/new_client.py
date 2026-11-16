"""
core/rotation/new_client.py - what SCK does with a login that worked for an ID Sera does not know
=================================================================================================
Runs on the Qt thread (DB and vault writes), after the portal accepted the password (or staff
pressed Save Directly).

* The internal-PK value (``identity``) **and** a client name (company or owner) are known -> create
  the client with the mapped profile, the service's user-ID column and its password column, attach
  the service, tag it verified when the password was tested, and link earlier tracker-dump rows.
* Otherwise nothing is created: the details go to the tracker dump as an ``SCK_Login`` row
  (``sck_login`` in the payload), so a person can create the client later from that row.
* A duplicate PK (another PC created the client meanwhile) falls back to the rotation save on the
  existing client.

The password is held in the ``SckAttempt`` (memory) until this module writes it. It goes to the
vault (client) or into the tracker-dump row (``sck_login.password``, redacted by
``sera_db.tracker_dump.redact_payload`` in every reader - decision D2). It is never logged and never
put in an audit detail.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

SCK_LOGIN = "SCK_Login"
SCK_LOGIN_STATUS = "Login only"       # rank 0: is_complete() is False; not a filing (D3)
_COMPANY_KEYS = ("company_name", "trade_name", "legal_name", "business_name", "firm_name")
_OWNER_KEYS = ("proprietor_name", "owner_name", "taxpayer_name", "name")


@dataclass
class NewClientResult:
    outcome: str                      # created | existing | dumped | failed
    message: str = ""
    client_id: Optional[int] = None
    client_name: str = ""
    missing: List[str] = field(default_factory=list)       # "pk" | "name"
    save: Any = None                  # RotationSave when outcome == "existing"


def profile_for_mapping(profile: Optional[Dict[str, Any]], identity: str) -> Dict[str, str]:
    """The watcher's {key: value} profile in the keys ``map_profile_to_mcl_columns`` reads."""
    src = {str(k).lower(): str(v).strip() for k, v in (profile or {}).items() if str(v or "").strip()}
    out = dict(src)
    for key in _COMPANY_KEYS:
        if src.get(key):
            out["company_name"] = src[key]
            break
    for key in _OWNER_KEYS:
        if src.get(key):
            out["proprietor_name"] = src[key]
            break
    if identity and not out.get("pan"):
        if len(identity) == 10 and identity[3:4].isalpha() and identity[:5].isalpha() and identity[5:9].isdigit():
            out["pan"] = identity
        elif identity[:4].isalpha() and identity[4:9].isdigit():
            out.setdefault("tan", identity)
    return out


def _name_of(mapped_profile: Dict[str, str]) -> str:
    return mapped_profile.get("company_name") or mapped_profile.get("proprietor_name") or ""


def handle_login(db: Any, *, service_id: int, portal: str, identity: str, user_id: str,
                 password: str, tested: bool, profile: Optional[Dict[str, Any]], actor: str,
                 service: Optional[dict] = None) -> NewClientResult:
    """Create the client, or keep the details in the tracker dump. Never raises."""
    identity = str(identity or "").strip().upper()
    user_id = str(user_id or "").strip()
    service = service or _service(db, service_id)
    mapped_profile = profile_for_mapping(profile, identity)
    name = _name_of(mapped_profile)
    missing = []
    mcl_cols = _cols(db)
    pk_cols = [c for c in mcl_cols if c.get("is_internal_pk")]
    if not identity:
        missing.append("pk")
    if not name:
        missing.append("name")
    if not password:
        return NewClientResult("failed", "There is no password to keep.", missing=missing)

    if not missing:
        try:
            return _create(db, service_id, portal, identity, user_id, password, tested,
                           mapped_profile, mcl_cols, pk_cols, actor, service, name)
        except ValueError:
            return _existing(db, service_id, portal, identity, password, tested, actor, name)
        except Exception as exc:
            print(f"[new_client] create error {type(exc).__name__}")
            return NewClientResult("failed", "Sera could not create the client. Try again.", missing=missing)
    return _dump(db, service_id, portal, identity, user_id, password, name, missing, actor)


# ── create ────────────────────────────────────────────────────────────────────────────────
def _create(db, service_id, portal, identity, user_id, password, tested, mapped_profile,
            mcl_cols, pk_cols, actor, service, name) -> NewClientResult:
    from ui.utils.profile_parser import map_profile_to_mcl_columns
    values = {int(k): str(v) for k, v in map_profile_to_mcl_columns(mapped_profile, mcl_cols).items()}
    for col in pk_cols:                                     # the PK column holds the identity
        values[int(col["id"])] = identity
    uid_col = (service or {}).get("userid_column_id")
    pwd_col = (service or {}).get("password_column_id")
    if uid_col:
        values[int(uid_col)] = user_id or identity
    if pwd_col:
        values[int(pwd_col)] = password
    client_id = db.add_client(values=values, notes="Created by SCK from a portal login",
                              service_ids=[service_id], actor=actor)     # ValueError: duplicate PK
    if tested:
        try:
            db.tag_client_scc_verified(client_id=client_id, combo_label="SCK-New", actor=actor)
        except Exception:
            pass
    try:
        db.link_unassigned_tracker_dumps(client_id, identity)
    except Exception:
        pass
    try:
        db.log_action(actor=actor, action="create", client_id=client_id,
                      detail=f"SCK: client created from a portal login (portal={portal}, id={identity}"
                             + ("" if tested else ", untested") + ")")
    except Exception:
        pass
    return NewClientResult("created", f"New client created: {name} ({portal})",
                           client_id=client_id, client_name=name)


def _existing(db, service_id, portal, identity, password, tested, actor, name) -> NewClientResult:
    """Another PC created the client meanwhile: save the password on that client."""
    from core.rotation.detector import save_verified_for_rotation
    client = db.get_client_by_pan(identity) if hasattr(db, "get_client_by_pan") else None
    if not client or client.get("id") is None:
        return NewClientResult("failed", "That client already exists but Sera could not find it.")
    save = save_verified_for_rotation(db=db, password=password, pan=identity, client_id=int(client["id"]),
                                      portal=portal, actor=actor, service_id=service_id,
                                      method="SCK-Widget", verified=tested)
    if save is None:
        return NewClientResult("failed", "This service has no password column in the vault. Set one in Services.")
    return NewClientResult("existing", "Saved password replaced", client_id=save.client_id,
                           client_name=save.client_name, save=save)


# ── tracker dump ──────────────────────────────────────────────────────────────────────────
def _dump(db, service_id, portal, identity, user_id, password, name, missing, actor) -> NewClientResult:
    payload = {"sck_login": {"user_id": user_id, "identity": identity, "name_found": name,
                             "missing": list(missing), "password": password}}
    try:
        db.insert_tracker_dump(client_id=None, service_id=service_id, portal=portal,
                               pan=identity or None, capture_method=SCK_LOGIN,
                               status=SCK_LOGIN_STATUS, captured_by=actor,
                               raw_payload_json=json.dumps(payload))
    except Exception as exc:
        print(f"[new_client] dump error {type(exc).__name__}")
        return NewClientResult("failed", "Sera could not keep the login details. Try again.", missing=missing)
    what = " and ".join({"pk": "the client ID", "name": "the client name"}[m] for m in missing)
    return NewClientResult("dumped", f"Login details kept in Tracker Dump: {what} missing", missing=missing)


# ── helpers ───────────────────────────────────────────────────────────────────────────────
def _cols(db) -> list:
    try:
        return list(db.get_mcl_columns() or [])
    except Exception:
        return []


def _service(db, service_id) -> dict:
    try:
        return db.get_service(service_id) or {}
    except Exception:
        return {}
