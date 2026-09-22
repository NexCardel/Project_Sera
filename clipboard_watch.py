"""
clipboard_watch.py
------------------
Desktop side of Sera Clipboard Assist (SCA), protocol v2 - see sca_protocol.py.

Copying a client's PAN / GSTIN / login id (from Excel, or any exact match) ARMS SCA: the
extension learns which client and portals, but receives no password. Only when that id is
entered on the matching portal's login page does the extension ask for one password, and
handle_password_request() decides - arm live, uses left, page host belongs to that portal -
reading the password from the vault at that moment.
"""

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Signal, QTimer
from PySide6.QtWidgets import QApplication

import automation
import sca_protocol

# Precompiled regexes for common UID formats
RE_PAN = sca_protocol.RE_PAN
RE_GSTIN = sca_protocol.RE_GSTIN
# Excel clipboard MIME format markers
EXCEL_MIME_MARKERS = {
    "csv",
    "biff12",
    "biff8",
    "biff5",
    "xml spreadsheet",
    "application/x-qt-windows-mime;value=\"csv\"",
    "application/x-qt-windows-mime;value=\"biff12\"",
    "application/x-qt-windows-mime;value=\"biff8\"",
    "application/x-qt-windows-mime;value=\"xml spreadsheet\"",
    "application/x-qt-windows-mime;value=\"link\"",
    "link",
}
SERA_CLIPBOARD_MARKER = "application/x-sera-uid"
IDENTITY_LABEL_TERMS = ("pan", "gstin", "user id", "userid", "username", "login id", "login user")

REPEAT_COPY_S = 2.0          # Excel fires dataChanged more than once per copy
UNUSED_ARMS_BEFORE_PAUSE = 5  # guardrail: a client armed this often without a fill is paused


def is_allowed_clipboard_identifier(value: str) -> bool:
    """Shape gate for PAN/GSTIN and configured service user IDs."""
    return sca_protocol.looks_like_uid(value)


def is_excel_source(mime_data) -> bool:
    """Checks for Excel data or a UID explicitly copied from Sera."""
    if not mime_data:
        return False
    for fmt in mime_data.formats():
        fmt_lower = fmt.lower()
        if SERA_CLIPBOARD_MARKER in fmt_lower or any(marker in fmt_lower for marker in EXCEL_MIME_MARKERS):
            return True
    return False


@dataclass
class Arm:
    arm_id: str
    client_id: int
    client_token: str
    matched_uid: str
    services: Dict[Any, dict]          # service_id -> {"url", "name", "password_selector"}
    expires_at: float                  # time.time()
    uses_remaining: int
    grants: int = 0
    fills: List[str] = field(default_factory=list)


class ClipboardWatchService(QObject):
    """Watches the clipboard for client ids and runs the desktop half of SCA."""
    sca_armed = Signal(int, str, list)  # client_id, client_token, services (no passwords)
    sca_notice = Signal(str, str)       # message for staff, level ("info" | "warning")

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.enabled = True
        self._uid_index: Dict[str, int] = {}
        self._identity_column_ids: set = set()
        self._index_generation = None
        self._arm: Optional[Arm] = None
        self._last_copy = ("", 0.0)
        self._unused_streak: Dict[int, int] = {}
        self._paused_clients: set = set()

        self.refresh_index()
        self._connect_clipboard()
        # Proactively check clipboard on startup in case a UID was already copied
        QTimer.singleShot(600, self._on_clipboard_changed)

    # ------------------------------------------------------------------ index
    def _connect_clipboard(self):
        app = QApplication.instance()
        if app:
            app.clipboard().dataChanged.connect(self._on_clipboard_changed)

    def set_enabled(self, enabled: bool):
        self.enabled = bool(enabled)
        if not self.enabled:
            self._arm = None

    def _generation(self):
        gen = getattr(self.db, "data_generation", None)
        return gen() if callable(gen) else None

    def refresh_index(self):
        """{normalized uid: client_id} for non-archived clients: identity columns (PAN, GSTIN,
        user/login ids, each service's user-id column) and the client token."""
        try:
            self._index_generation = self._generation()
            clients = self.db.search_clients("", include_archived=False)
            allowed = set()
            for col in self.db.get_mcl_columns():
                label = str(col.get("label", "")).lower()
                if any(term in label for term in IDENTITY_LABEL_TERMS):
                    allowed.add(col["id"])
            for service in self.db.get_services():
                if service.get("userid_column_id"):
                    allowed.add(service["userid_column_id"])
            self._identity_column_ids = allowed

            new_index, collisions = {}, set()
            for c in clients:
                cid = c["id"]
                keys = [v for col_id, v in c.get("values", {}).items() if col_id in allowed and v is not None]
                if c.get("client_id_token"):
                    keys.append(c["client_id_token"])
                for raw in keys:
                    key = sca_protocol.normalize_uid(raw)
                    if not sca_protocol.looks_like_uid(key):
                        continue
                    if key in new_index and new_index[key] != cid:
                        collisions.add(key)
                    else:
                        new_index[key] = cid
            for duplicate in collisions:
                new_index.pop(duplicate, None)
            self._uid_index = new_index
            print(f"[SCA] Index: {len(new_index)} ids; {len(collisions)} shared by two clients left out.")
        except Exception as e:
            print(f"[SCA] Index build error: {e}")

    def _ensure_index_fresh(self):
        """Clients added or edited anywhere (editor, CSV import, another PC via sync) are armable
        at once - the index was only rebuilt on start-up and settings save before."""
        gen = self._generation()
        if gen is None or gen != self._index_generation:
            self.refresh_index()

    # ------------------------------------------------------------------ copy -> arm
    def _on_clipboard_changed(self):
        if not self.enabled:
            return
        app = QApplication.instance()
        if not app:
            return
        clipboard = app.clipboard()
        text = clipboard.text()
        if not text:
            return
        candidate = sca_protocol.normalize_uid(text)
        if not (3 <= len(candidate) <= 80) or not sca_protocol.looks_like_uid(candidate):
            return
        self._ensure_index_fresh()
        # Excel/Sera markers are preferred, but browser copies and some Qt table paths expose
        # plain text only; an exact match in the index is enough - other text is ignored.
        if not is_excel_source(clipboard.mimeData()) and candidate not in self._uid_index:
            return
        client_id = self._uid_index.get(candidate)
        if not client_id:
            return
        last_uid, last_t = self._last_copy
        now = time.time()
        if last_uid == candidate and now - last_t < REPEAT_COPY_S:
            return
        self._last_copy = (candidate, now)
        if client_id in self._paused_clients:
            print(f"[SCA] Client {client_id} armed {UNUSED_ARMS_BEFORE_PAUSE} times without a fill - paused this session.")
            return
        print(f"[SCA] Copied id matches client {client_id}. Arming...")
        self._arm_client_services(client_id, str(client_id), candidate)

    def _client_context(self, client_id: int):
        client = self.db.get_client(client_id)
        if not client:
            return None
        mcl_cols = {c["id"]: c for c in self.db.get_mcl_columns()}
        return client, mcl_cols, client.get("values", {})

    def _password_status(self, svc: dict, client_id: int, values: dict, mcl_cols: dict):
        """(saved password for this portal, caveat). Caveats:
          "no_password"    - nothing saved; shown to staff (it used to fail silently)
          "scc_unverified" - an Income Tax password SCC has not verified: given out only on a
                             request confirmed by a click on the page card (user choice,
                             2026-09-22 - before, it was never given, so SCA did nothing on the
                             IT portal for 468 of 521 clients).

        A portal with its own password column uses ONLY that column. The old "general password"
        fallback took the first column typed as password - USER ID, EMAIL and TAN are typed that
        way here - so an empty IT_Password would have typed the GST user id into the box."""
        pwd_col_id = svc.get("password_column_id")
        if pwd_col_id:
            password = str(values.get(pwd_col_id) or "").strip()
        else:
            labelled = [c["id"] for c in mcl_cols.values() if "pass" in c.get("label", "").lower()]
            password = next((str(values[p]).strip() for p in labelled if values.get(p)), "")
        if not password:
            return "", "no_password"
        if automation.is_itr_service(svc) and not self.db.is_client_scc_verified(client_id=client_id):
            return password, "scc_unverified"
        return password, ""

    def _retire_current_arm(self):
        old = self._arm
        if old is None:
            return
        if old.grants == 0:
            streak = self._unused_streak.get(old.client_id, 0) + 1
            self._unused_streak[old.client_id] = streak
            if streak >= UNUSED_ARMS_BEFORE_PAUSE:
                self._paused_clients.add(old.client_id)
        self._arm = None

    def _arm_client_services(self, client_id: int, client_token: str, matched_uid: str):
        try:
            ctx = self._client_context(client_id)
            if not ctx:
                return
            client, mcl_cols, values = ctx
            client_services = self.db.get_client_services(client_id)
            target_services = client_services or self.db.get_services()
            if not target_services:
                print(f"[SCA] Client {client_id} has no attached or configured services; not arming.")
                return

            business_name = owner_name = ""
            for c_id, col in mcl_cols.items():
                lbl = col.get("label", "").lower()
                val = str(values.get(c_id, "") or "").strip()
                if not val:
                    continue
                if any(k in lbl for k in ["business", "company", "firm", "trade", "client name", "name"]) and not business_name:
                    business_name = val
                elif any(k in lbl for k in ["owner", "proprietor", "director", "partner", "contact", "person"]) and not owner_name:
                    owner_name = val
            business_name = business_name or f"Client #{client_token}"

            # Only identity values trigger a fill - not every short value in the row (city,
            # phone, "YES" used to).
            identity_cols = set(self._identity_column_ids)
            identity_cols |= {s.get("userid_column_id") for s in target_services if s.get("userid_column_id")}
            candidates = [matched_uid]
            for c_id, val in values.items():
                key = sca_protocol.normalize_uid(val)
                if c_id in identity_cols and sca_protocol.looks_like_uid(key) and key not in candidates:
                    candidates.append(key)
            token = sca_protocol.normalize_uid(client.get("client_id_token") or "")
            if token and sca_protocol.looks_like_uid(token) and token not in candidates:
                candidates.append(token)

            services, arm_services = [], {}
            for svc in target_services:
                password, caveat = self._password_status(svc, client_id, values, mcl_cols)
                entry = sca_protocol.build_arm_service(svc, bool(password), caveat if not password else "",
                                                       needs_confirm=(caveat == "scc_unverified"))
                if entry is None:
                    continue
                services.append(entry)
                arm_services[svc.get("id")] = {"url": entry["url"], "name": entry["name"],
                                               "password_selector": entry["password_selector"]}
            if not services:
                print(f"[SCA] Client {client_id}: no portal with a login page; not arming.")
                return
            # Armed even when no portal has a usable password: the extension then explains, on
            # the login page, why nothing was filled (it used to fail silently).

            sca_mode = self.db.get_setting("sca_action_mode", "autofill")
            try:
                max_uses = max(1, min(int(self.db.get_setting("sca_max_uses", "1")), 20))
            except (TypeError, ValueError):
                max_uses = 1
            request = sca_protocol.build_arm_request(
                client_id=client_id, client_token=client_token, matched_uid=matched_uid,
                candidate_uids=candidates, services=services, business_name=business_name,
                owner_name=owner_name, sca_mode=sca_mode, max_uses=max_uses)
            self._retire_current_arm()
            arm = request["arm"]
            self._arm = Arm(arm_id=arm["arm_id"], client_id=client_id, client_token=client_token,
                            matched_uid=matched_uid, services=arm_services,
                            expires_at=arm["expires_at"] / 1000.0, uses_remaining=max_uses)
            print(f"[SCA] Armed client {client_token}: {len(services)} portal(s) [mode: {sca_mode}]")
            automation.arm_sca(request)
            self.sca_armed.emit(client_id, client_token, services)
        except Exception as e:
            print(f"[SCA] _arm_client_services error: {e}")

    # ------------------------------------------------------------------ extension -> desktop
    def handle_password_request(self, msg: dict) -> dict:
        """Answer to SCA_PASSWORD_REQUEST {request_id, arm_id, service_id, page_host,
        matched_uid}: a GRANT with the one password, or a DENIED with the reason."""
        request_id = msg.get("request_id")

        def denied(reason: str) -> dict:
            print(f"[SCA] Password request refused: {reason}")
            return {"type": sca_protocol.MSG_SCA_PASSWORD_DENIED, "request_id": request_id,
                    "arm_id": msg.get("arm_id"), "reason": reason}

        arm = self._arm
        if not self.enabled:
            return denied("SCA is turned off")
        if arm is None or msg.get("arm_id") != arm.arm_id:
            return denied("no such arm (a newer copy replaced it, or the app restarted)")
        if time.time() > arm.expires_at:
            self._retire_current_arm()
            return denied("the arm expired")
        if arm.uses_remaining <= 0:
            return denied("no uses left for this copy")
        svc_id = msg.get("service_id")
        svc = arm.services.get(svc_id)
        if svc is None:
            return denied("that portal is not part of this arm")
        if not sca_protocol.host_matches(str(msg.get("page_host") or ""), svc["url"]):
            return denied("the page is not that portal's site")
        ctx = self._client_context(arm.client_id)
        if not ctx:
            return denied("the client no longer exists")
        _client, mcl_cols, values = ctx
        full_svc = next((s for s in self.db.get_services() if s.get("id") == svc_id), None)
        password, caveat = (self._password_status(full_svc, arm.client_id, values, mcl_cols)
                            if full_svc else ("", "no_password"))
        if not password:
            return denied("no password SCA may use for that portal")
        if caveat == "scc_unverified" and msg.get("confirmed") is not True:
            return denied("this Income Tax password is not SCC-verified - it needs a click on the page card")
        arm.uses_remaining -= 1
        arm.grants += 1
        self._unused_streak.pop(arm.client_id, None)
        return {"type": sca_protocol.MSG_SCA_PASSWORD_GRANT, "request_id": request_id,
                "arm_id": arm.arm_id, "service_id": svc_id, "password": password,
                "password_selector": svc.get("password_selector", "")}

    def handle_fill_result(self, msg: dict) -> None:
        """SCA_FILL_RESULT {arm_id, service_id, result: filled|failed, reason} - audit only."""
        arm = self._arm
        client_id = arm.client_id if arm and arm.arm_id == msg.get("arm_id") else None
        portal = (arm.services.get(msg.get("service_id"), {}).get("name") if client_id else None) or "portal"
        if msg.get("result") == "filled":
            print(f"[SCA] Password filled on {portal}")
            if client_id:
                arm.fills.append(portal)
                try:
                    self.db.record_client_activity(client_id, "SCA", f"Password filled on {portal}")
                except Exception:
                    pass
        else:
            reason = msg.get("reason") or "unknown"
            print(f"[SCA] Fill on {portal} failed: {reason}")
            self.sca_notice.emit(self.explain(portal, reason), "warning")

    @staticmethod
    def explain(portal: str, reason: str) -> str:
        if reason == "scc_unverified":
            return (f"SCA did not fill the {portal} password: it has not been verified yet. "
                    "Log in once with SCC (Smart Credential Combinations) to verify it.")
        if reason == "no_password":
            return f"SCA did not fill {portal}: no password is saved for this client."
        return f"SCA could not fill the {portal} password: {reason}."
