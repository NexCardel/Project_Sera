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


def is_excel_source(mime_data) -> bool:
    """Checks if the clipboard mimeData carries Excel-specific format signatures."""
    if not mime_data:
        return False
    formats = mime_data.formats()
    for fmt in formats:
        fmt_lower = fmt.lower()
        if any(marker in fmt_lower for marker in EXCEL_MIME_MARKERS):
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
        self._debounce_window = 2.0  # seconds (allows quick re-copy to re-arm)
        
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
            new_index = {}
            for c in clients:
                cid = c["id"]
                vals = c.get("values", {})
                for col_id, val in vals.items():
                    if val and isinstance(val, str):
                        clean_val = val.strip().upper()
                        if 3 <= len(clean_val) <= 100:
                            new_index[clean_val] = cid

                token = c.get("client_id_token")
                if token and isinstance(token, str):
                    new_index[token.strip().upper()] = cid

            self._uid_index = new_index
            print(f"[SCA] In-memory UID index built with {len(self._uid_index)} keys.")
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

        text_clean = text.strip()
        length = len(text_clean)
        if length < 3 or length > 100:
            return

        normalized_candidate = text_clean.upper()

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
            # If client has no specific services attached, allow all configured services as candidate portals
            target_services = client_services if client_services else all_services

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
                    sca_mode=sca_mode
                )
                try:
                    self.db.record_client_activity(client_id, "SCA", f"Copied UID: {matched_uid}")
                except Exception:
                    pass
                self.sca_armed.emit(client_id, client_token, service_payloads)
        except Exception as e:
            print(f"[SCA] _arm_client_services error: {e}")
