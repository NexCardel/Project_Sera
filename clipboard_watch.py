"""
clipboard_watch.py
------------------
Desktop-side event-driven clipboard watcher for Sera Clipboard Assist (SCA).

Listens to QApplication.clipboard().dataChanged, filters for Excel copy events,
matches against an in-memory UID index in O(1), and silently dispatches SCA_ARM
payloads to the companion browser extension over TCP 49153.
"""

import re
import time
from typing import Optional, Dict, Any, List
from PySide6.QtCore import QObject, Signal, QTimer
from PySide6.QtWidgets import QApplication

# Precompiled regexes for common UID formats
RE_PAN = re.compile(r'^[A-Z]{5}[0-9]{4}[A-Z]$')
RE_GSTIN = re.compile(r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$')

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


def is_allowed_clipboard_identifier(value: str) -> bool:
    """Shape gate for PAN/GSTIN and configured service user IDs."""
    clean = str(value or "").strip().upper()
    # Service user IDs are configurable and may contain dots, hyphens,
    # underscores, @, colon, or slashes. They are still constrained to a
    # compact token so ordinary prose is never treated as a UID.
    return bool(RE_PAN.fullmatch(clean) or RE_GSTIN.fullmatch(clean) or (3 <= len(clean) <= 80 and re.fullmatch(r"[A-Z0-9][A-Z0-9._@:/-]*", clean)))


def is_excel_source(mime_data) -> bool:
    """Checks for Excel data or a UID explicitly copied from Sera."""
    if not mime_data:
        return False
    formats = mime_data.formats()
    for fmt in formats:
        fmt_lower = fmt.lower()
        if SERA_CLIPBOARD_MARKER in fmt_lower or any(marker in fmt_lower for marker in EXCEL_MIME_MARKERS):
            return True
    return False


class ClipboardWatchService(QObject):
    """
    Monitors Windows clipboard for client User IDs copied from Excel.
    Silently arms the matching client password for autofill when user pastes into web portals.
    """
    sca_armed = Signal(int, str, list)  # client_id, client_token, services_list

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.enabled = True
        self._uid_index: Dict[str, int] = {}  # {normalized_uid: client_id}
        self._last_armed_token: Optional[str] = None
        self._last_armed_time: float = 0.0
        self._debounce_window = 15.0  # seconds; suppress repeat copy/re-arm noise
        
        self.refresh_index()
        self._connect_clipboard()
        # Proactively check clipboard on startup in case a UID was already copied
        QTimer.singleShot(600, self._on_clipboard_changed)

    def _connect_clipboard(self):
        app = QApplication.instance()
        if app:
            clipboard = app.clipboard()
            clipboard.dataChanged.connect(self._on_clipboard_changed)

    def set_enabled(self, enabled: bool):
        self.enabled = bool(enabled)

    def refresh_index(self):
        """
        Builds the in-memory {normalized_uid: client_id} index from non-archived clients.
        Indexes all identity columns, service user IDs, and client values.
        """
        try:
            clients = self.db.search_clients("", include_archived=False)
            mcl_cols = {c["id"]: c for c in self.db.get_mcl_columns()}
            allowed_column_ids = set()
            for col_id, col in mcl_cols.items():
                label = str(col.get("label", "")).lower()
                if any(term in label for term in ("pan", "gstin", "user id", "userid", "username", "login id", "login user")):
                    allowed_column_ids.add(col_id)

            # Service-specific user-ID columns are authoritative even when
            # their labels are custom (for example, "ITD Login").
            for service in self.db.get_services():
                if service.get("userid_column_id"):
                    allowed_column_ids.add(service["userid_column_id"])

            import sca_protocol
            new_index = {}
            collisions = set()
            for c in clients:
                cid = c["id"]
                vals = c.get("values", {})
                for col_id, val in vals.items():
                    if col_id in allowed_column_ids and val is not None:
                        clean_val = sca_protocol.normalize_uid(val)
                        if is_allowed_clipboard_identifier(clean_val):
                            if clean_val in new_index and new_index[clean_val] != cid:
                                collisions.add(clean_val)
                            else:
                                new_index[clean_val] = cid

                token = c.get("client_id_token")
                if token and isinstance(token, str):
                    clean_token = sca_protocol.normalize_uid(token)
                    if clean_token:
                        new_index[clean_token] = cid

            for duplicate in collisions:
                new_index.pop(duplicate, None)
            self._uid_index = new_index
            print(f"[SCA] In-memory approved-identifier index built with {len(self._uid_index)} keys; {len(collisions)} ambiguous duplicates suppressed.")
        except Exception as e:
            print(f"[SCA] Index build error: {e}")

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

        import sca_protocol
        normalized_candidate = sca_protocol.normalize_uid(text)
        length = len(normalized_candidate)
        if length < 3 or length > 80:
            return

        # Sera/Excel MIME markers are preferred, but browser copies and some
        # Qt table paths can expose plain text only. An exact match in the
        # approved UID index is sufficient and remains safe because arbitrary
        # unmatched clipboard text is still ignored below.
        if not is_excel_source(clipboard.mimeData()) and normalized_candidate not in self._uid_index:
            return

        if not is_allowed_clipboard_identifier(normalized_candidate):
            return

        # In-memory dictionary lookup
        client_id = self._uid_index.get(normalized_candidate)
        if not client_id:
            return

        # Debounce / repeat suppression within window
        now = time.time()
        client_token = str(client_id)
        if self._last_armed_token == client_token and (now - self._last_armed_time) < self._debounce_window:
            return

        self._last_armed_token = client_token
        self._last_armed_time = now

        print(f"[SCA] Matched UID '{normalized_candidate}' -> Client ID {client_id}. Arming...")
        # Fetch client's services & registered credentials
        self._arm_client_services(client_id, client_token, normalized_candidate)

    def _arm_client_services(self, client_id: int, client_token: str, matched_uid: str):
        try:
            client = self.db.get_client(client_id)
            if not client:
                return

            client_services = self.db.get_client_services(client_id)
            all_services = self.db.get_services()
            # If client has specific services attached, use them; otherwise allow all configured services as candidate portals
            target_services = client_services if client_services else all_services
            if not target_services:
                print(f"[SCA] Client {client_id} has no attached or configured services; not arming.")
                return

            mcl_cols = {c["id"]: c for c in self.db.get_mcl_columns()}
            values = client.get("values", {})

            # Extract business name and owner name from client values
            business_name = ""
            owner_name = ""
            for c_id, col in mcl_cols.items():
                lbl = col.get("label", "").lower()
                val = str(values.get(c_id, "")).strip()
                if not val:
                    continue
                if any(k in lbl for k in ["business", "company", "firm", "trade", "client name", "name"]) and not business_name:
                    business_name = val
                elif any(k in lbl for k in ["owner", "proprietor", "director", "partner", "contact", "person"]) and not owner_name:
                    owner_name = val

            if not business_name:
                business_name = f"Client #{client_token}"

            # Collect candidate UIDs (PAN, GSTIN, User IDs, Client Token)
            candidate_uids = [matched_uid.upper()]
            service_uid_column_ids = {s.get("userid_column_id") for s in target_services if s.get("userid_column_id")}
            for c_id, val in values.items():
                if val is not None and 3 <= len(str(val).strip()) <= 80:
                    clean_v = str(val).strip().upper()
                    if (c_id in service_uid_column_ids or is_allowed_clipboard_identifier(clean_v)) and clean_v not in candidate_uids:
                        candidate_uids.append(clean_v)
            if client_token and str(client_token).upper() not in candidate_uids:
                candidate_uids.append(str(client_token).upper())

            # Find potential password columns
            pwd_col_ids = [c["id"] for c in mcl_cols.values() if "pass" in c.get("label", "").lower() or c.get("field_type") == "password"]

            # General password fallback
            general_pwd = ""
            for p_id in pwd_col_ids:
                if values.get(p_id):
                    general_pwd = str(values.get(p_id))
                    break

            service_payloads = []

            for svc in target_services:
                link = svc.get("login_page_link")
                if not link:
                    continue

                pwd_col_id = svc.get("password_column_id")
                password_val = values.get(pwd_col_id, "") if pwd_col_id else general_pwd
                
                uid_col_id = svc.get("userid_column_id")
                user_id_val = values.get(uid_col_id, "") if uid_col_id else matched_uid

                if not password_val:
                    password_val = general_pwd

                service_payloads.append({
                    "service_id": svc.get("id"),
                    "name": svc.get("name", "Portal"),
                    "url": link,
                    "username_selector": (svc.get("username_selector") or "").strip(),
                    "password_selector": (svc.get("password_selector") or "").strip(),
                    "extension_flow": svc.get("extension_flow", "double"),
                    "success_selector": svc.get("success_selector", ""),
                    "arn_selector": svc.get("arn_selector", ""),
                    "user_id": user_id_val,
                    "password": password_val,
                    "matched_uid": matched_uid,
                })

            if service_payloads:
                sca_mode = self.db.get_setting("sca_action_mode", "autofill")
                try:
                    max_uses = max(1, min(int(self.db.get_setting("sca_max_uses", "1")), 20))
                except (TypeError, ValueError):
                    max_uses = 1
                print(f"[SCA] Arming {len(service_payloads)} services for client {client_token} ({business_name}) [mode: {sca_mode}]")
                from automation import arm_sca
                arm_sca(
                    client_id=client_id,
                    client_token=client_token,
                    matched_uid=matched_uid,
                    services=service_payloads,
                    business_name=business_name,
                    owner_name=owner_name,
                    ttl_ms=45000,
                    sca_mode=sca_mode,
                    max_uses=max_uses,
                    candidate_uids=candidate_uids
                )
                try:
                    self.db.record_client_activity(client_id, "SCA", f"Copied UID: {matched_uid}")
                except Exception:
                    pass
                self.sca_armed.emit(client_id, client_token, service_payloads)
        except Exception as e:
            print(f"[SCA] _arm_client_services error: {e}")
