"""
core/scc/save.py - SCC-U step 6: the guarded save
==================================================
Autofill-tweaks blueprint G.2 step 6. The one way a working SCC password reaches master.db (it was
main._handle_scc_password_verified). Called when the card credits a row: the automatic pick (step 5)
and the "This one worked" button both come through SccCard.credit -> SccSaver.on_worked.

  password equal to the saved one   -> mark "Password verified via SCC" only
  no saved password                 -> save it + mark verified
  a DIFFERENT saved password        -> D2 (asked 2026-09-29: replace automatically) - replaced, and the
                                       audit line says "replaced"
  PAN not in Sera                   -> D4 (asked 2026-09-29: add automatically) - client added

Income Tax only. The audit log gets who, the row label and when (log_action stamps the time) - never
the password. It goes through the normal database calls, so Sera Sync carries it.
"""

from dataclasses import dataclass
from typing import Any, Callable, Optional

from .attempts import Attempt

CREATED, SAVED, REPLACED, VERIFIED = "created", "saved", "replaced", "verified"


@dataclass
class SaveResult:
    outcome: str            # created / saved / replaced / verified
    client_id: int
    client_name: str
    pan: str


def is_itr_portal(portal: str) -> bool:
    p = (portal or "").strip().lower()
    return "gst" not in p and any(k in p for k in ("income", "itr", "tax"))


def _password_column(db: Any, service_id: Optional[int], portal: str) -> Optional[int]:
    col = None
    if service_id:
        svc = db.get_service(service_id)
        col = svc.get("password_column_id") if svc else None
    if not col:
        svc = db.get_service_for_portal(portal)
        if svc:
            col = svc.get("password_column_id")
    if not col:
        for c in db.get_mcl_columns():
            lbl = (c.get("label") or "").strip().lower()
            if ("itr" in lbl or "income" in lbl) and "pass" in lbl:
                return c["id"]
    return col


def _create_client(db: Any, pan: str, name: str, pwd_col_id: Optional[int], password: str,
                   portal: str, actor: str) -> int:
    mcl = db.get_mcl_columns()
    pan_col_id = next((c["id"] for c in mcl if c.get("is_internal_pk")), None)
    if not pan_col_id:
        for c in mcl:
            if "pan" in (c.get("label") or "").lower().split():
                pan_col_id = c["id"]
                break
    if not pan_col_id:
        for c in mcl:
            lbl = (c.get("label") or "").lower()
            if "pan" in lbl and "pass" not in lbl and "company" not in lbl:
                pan_col_id = c["id"]
                break
    name_col_id = None
    for c in mcl:
        lbl = (c.get("label") or "").lower()
        if c["id"] != pan_col_id and any(k in lbl for k in ("company", "name", "client", "proprietor")):
            name_col_id = c["id"]
            break

    values = {}
    for c in mcl:
        if c.get("is_internal_pk") and pan:
            values[c["id"]] = pan
    if pan_col_id and pan:
        values[pan_col_id] = pan
    if name_col_id:
        values[name_col_id] = name or f"Client ({pan})"
    if pwd_col_id:
        values[pwd_col_id] = password
    svc = db.get_service_for_portal(portal)
    return db.add_client(values=values, notes="Password verified via SCC",
                         service_ids=[svc["id"]] if svc else [], actor=actor)


def _client_name(db: Any, client_id: int, given: str) -> str:
    if given:
        return given
    client = db.get_client(client_id) or {}
    for c in db.get_mcl_columns():
        lbl = (c.get("label") or "").lower()
        if "name" in lbl or "client" in lbl or "proprietor" in lbl:
            val = str((client.get("values") or {}).get(c["id"]) or "").strip()
            if val:
                return val
    return "Client"


def save_verified(db: Any, password: str, pan: str = "", client_id: Any = None, service_id: Any = None,
                  row_label: str = "SCC", portal: str = "Income Tax", client_name: str = "",
                  actor: str = "Staff") -> Optional[SaveResult]:
    """The guarded save. None = nothing to save (empty password, not Income Tax). Raises on a database error."""
    password = str(password or "").strip()
    if not password or not is_itr_portal(portal):
        return None
    pan = str(pan or "").strip().upper()
    row_label = str(row_label or "SCC").strip()
    client_name = str(client_name or "").strip()

    try:
        client_id = int(client_id) if client_id is not None else None
    except (ValueError, TypeError):
        client_id = None
    try:
        service_id = int(service_id) if service_id is not None else None
    except (ValueError, TypeError):
        service_id = None
    if not client_id and pan:
        found = db.get_client_by_pan(pan)
        client_id = found.get("id") if found else None

    pwd_col_id = _password_column(db, service_id, portal)

    if not client_id:
        client_id = _create_client(db, pan, client_name, pwd_col_id, password, portal, actor)
        db.log_action(actor=actor, action="create", client_id=client_id,
                      detail=f"Client added from an SCC login ({row_label})")
        return SaveResult(CREATED, client_id, _client_name(db, client_id, client_name), pan)

    outcome = VERIFIED
    if pwd_col_id:
        current = str(((db.get_client(client_id) or {}).get("values") or {}).get(pwd_col_id) or "").strip()
        if current != password:
            db.update_client_single_field(client_id=client_id, column_id=pwd_col_id, value=password,
                                          actor=actor, log_action=False)
            outcome = REPLACED if current else SAVED
    if outcome == VERIFIED:
        detail = f"SCC: the saved password already matches ({row_label}); marked verified"
    elif outcome == SAVED:
        detail = f"SCC: password saved and marked verified ({row_label})"
    else:
        detail = f"SCC: a different saved password was replaced ({row_label}); marked verified"
    db.tag_client_scc_verified(client_id=client_id, combo_label=row_label, actor=actor)
    db.log_action(actor=actor, action="update", client_id=client_id, detail=detail)
    return SaveResult(outcome, client_id, _client_name(db, client_id, client_name), pan)


class SccSaver:
    """SccCard's on_worked. `row_text(attempt_id, label)` gives the row's text from the card's memory;
    `after(SaveResult)` refreshes the UI (main puts it on the Qt thread)."""

    def __init__(self, db: Any, row_text: Callable[[str, str], Optional[str]], actor: Callable[[], str],
                 after: Optional[Callable[[SaveResult], Any]] = None, echo: Callable[[str], Any] = print) -> None:
        self._db = db
        self._row_text = row_text
        self._actor = actor
        self._after = after
        self._echo = echo

    def on_worked(self, att: Attempt, row_label: str) -> Optional[SaveResult]:
        text = self._row_text(att.attempt_id, row_label)
        if not text:
            return None
        try:
            result = save_verified(self._db, text, pan=att.pan, client_id=att.client_id, row_label=row_label,
                                   actor=self._actor())
        except Exception as e:
            self._echo(f"[SCC] Save failed: {type(e).__name__}")
            return None
        if result is None:
            return None
        self._echo(f"[SCC] {result.outcome} for client #{result.client_id} via {row_label}")
        if self._after:
            try:
                self._after(result)
            except Exception as e:
                self._echo(f"[SCC] Refresh failed: {type(e).__name__}")
        return result
