"""
automation.py
--------------
Routes autofill, SMTI (Manual Assist), and MECP (Manual Extension Copy/Paste)
payloads to the Chrome Extension via the native host socket.
"""

import threading
import socket
import json
import time
import webbrowser
from typing import Optional
from PySide6.QtCore import QObject, Signal

class _AutofillBridge(QObject):
    failed = Signal(str, str)


def is_manual_portal(service: dict) -> bool:
    mode = service.get("automation_mode", "extension")
    return mode == "manual"


def is_extension_portal(service: dict) -> bool:
    mode = service.get("automation_mode", "extension")
    return mode in ("extension", "playwright")


def get_login_url(service: dict) -> str:
    return service["login_page_link"]


def autofill_login(service: dict, user_id: str, password: str, client_id: int, on_error=None):
    _send_to_extension(service, user_id, password, client_id, on_error, mode="autofill")


def trigger_manual_assist(service: dict, user_id: str, password: str, client_id: int, on_error=None):
    """Open the portal and ask the companion extension to show SMTI Manual Assist."""
    _send_to_extension(service, user_id, password, client_id, on_error, mode="manual_assist")


def trigger_mecp(service: dict, user_id: str, password: str, client_id: int, on_error=None):
    """Open the portal and ask the companion extension to show the MECP floating card widget."""
    _send_to_extension(service, user_id, password, client_id, on_error, mode="mecp")


def _send_to_extension(service: dict, user_id: str, password: str, client_id: int, on_error=None, mode="autofill"):
    """Sends the autofill/SMTI/MECP payload to the native_host via TCP with retry & auto-launch fallback."""
    u_sel = (service.get("username_selector") or "").strip().replace("input [", "input[").replace("input ", "input")
    p_sel = (service.get("password_selector") or "").strip().replace("input [", "input[").replace("input ", "input")
    payload = {
        "type": "autofill",
        "mode": mode,
        "service_id": service.get("id"),
        "userid": user_id,
        "password": password,
        "portal": service.get("name", "portal").lower(),
        "url": service.get("login_page_link", ""),
        "username_selector": u_sel,
        "password_selector": p_sel,
        "extension_flow": service.get("extension_flow", "double"),
        "success_selector": service.get("success_selector", ""),
        "arn_selector": service.get("arn_selector", ""),
        "client_id": client_id,
        "client_name": service.get("_client_name", service.get("name", "Client")),
        "tracker_enabled": service.get("_tracker_enabled", True),
        "fst_enabled": service.get("_fst_enabled", True),
        "sad_enabled": service.get("_sad_enabled", True),
    }

    def _attempt_send():
        max_attempts = 20
        launched_browser = False

        for attempt in range(1, max_attempts + 1):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(2.0)
                    s.connect(('127.0.0.1', 49153))
                    s.sendall(json.dumps(payload).encode('utf-8'))
                    return
            except (ConnectionRefusedError, socket.timeout, OSError):
                if not launched_browser:
                    launched_browser = True
                    try:
                        webbrowser.open(service["login_page_link"])
                    except Exception:
                        pass
                time.sleep(0.5)

        if on_error:
            on_error(
                "Could not connect to Sera Extension host after 10 seconds.\n"
                "Please ensure the extension is installed and enabled in Edge/Chrome."
            )

    threading.Thread(target=_attempt_send, daemon=True).start()


def arm_sca(client_id: int, client_token: str, matched_uid: str, services: list[dict], business_name: str = "", owner_name: str = "", ttl_ms: int = 45000, sca_mode: str = "autofill"):
    """Sends SCA_ARM payload to native_host -> background.js over TCP 49153."""
    payload = {
        "type": "SCA_ARM",
        "client_id": client_id,
        "client_id_token": client_token,
        "matched_uid": matched_uid,
        "business_name": business_name,
        "owner_name": owner_name,
        "services": services,
        "ttl_ms": ttl_ms,
        "sca_mode": sca_mode,
    }
    def _do_send():
        # Retry for up to 35 seconds so startup copy reaches the extension as soon as browser opens
        max_attempts = int(min(ttl_ms, 35000) / 500)
        for _ in range(max_attempts):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(1.0)
                    s.connect(('127.0.0.1', 49153))
                    s.sendall(json.dumps(payload).encode('utf-8'))
                    return
            except Exception:
                time.sleep(0.5)
    threading.Thread(target=_do_send, daemon=True).start()


def update_extension_settings(fst_enabled: bool = True, sad_enabled: bool = True, tracker_enabled: Optional[bool] = None, sca_enabled: bool = True, sca_mode: str = "autofill"):
    """Sends immediate setting updates to native_host -> background.js"""
    if tracker_enabled is None:
        tracker_enabled = fst_enabled or sad_enabled
    payload = {
        "type": "update_settings",
        "tracker_enabled": tracker_enabled,
        "fst_enabled": fst_enabled,
        "sad_enabled": sad_enabled,
        "sca_enabled": sca_enabled,
        "sca_mode": sca_mode,
    }
    def _do_send():
        for _ in range(5):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(1.0)
                    s.connect(('127.0.0.1', 49153))
                    s.sendall(json.dumps(payload).encode('utf-8'))
                    return
            except Exception:
                time.sleep(0.2)
    threading.Thread(target=_do_send, daemon=True).start()
