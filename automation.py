"""
automation.py
--------------
Routes autofill, SMTI (Manual Assist), and MECP (Manual Extension Copy/Paste)
payloads to the Companion Extension via the native host socket.
"""

import threading
import socket
import json
import time
import webbrowser
import os
import shutil
import subprocess
from typing import Optional
from pathlib import Path
from PySide6.QtCore import QObject, Signal

try:
    import sca_protocol
except ImportError:
    sca_protocol = None

# Registry for tracking extension acknowledgements
_pending_acks = set()
_pending_acks_lock = threading.Lock()

def register_ack(command_id: str):
    """Called by extension_listener when an SCA_ACK is received."""
    with _pending_acks_lock:
        if command_id in _pending_acks:
            _pending_acks.remove(command_id)

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


def open_in_default_browser(url: str, preferred_browser: Optional[str] = None):
    """
    Opens the URL in the single designated default browser to avoid opening duplicate tabs
    or causing double-login confusion when multiple browsers have the Sera extension enabled.
    """
    if not url:
        return

    browser_pref = str(preferred_browser or "").strip().lower()
    if not browser_pref or browser_pref == "system_default":
        try:
            app_dir = Path.home() / "AmanAssociates_Sera"
            settings_file = app_dir / "settings.ini"
            if settings_file.exists():
                import configparser
                cfg = configparser.ConfigParser()
                cfg.read(str(settings_file))
                browser_pref = cfg.get("Automation", "browser", fallback="system_default").lower()
        except Exception:
            browser_pref = "system_default"

    paths = {
        "chrome": [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            shutil.which("chrome"),
            shutil.which("google-chrome")
        ],
        "firefox": [
            r"C:\Program Files\Mozilla Firefox\firefox.exe",
            r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
            shutil.which("firefox")
        ],
        "edge": [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            shutil.which("msedge")
        ]
    }

    if browser_pref in paths:
        for candidate in paths[browser_pref]:
            if candidate and os.path.exists(candidate):
                try:
                    subprocess.Popen([candidate, url])
                    return
                except Exception:
                    pass

    try:
        webbrowser.open(url)
    except Exception:
        pass


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
                        open_in_default_browser(service.get("login_page_link", ""))
                    except Exception:
                        pass
                time.sleep(0.5)
                if on_error and attempt == max_attempts:
                    on_error(
                        "Could not connect to Sera Extension host after 10 seconds.\n"
                        "Please ensure the extension is installed and enabled in your browser."
                    )

    threading.Thread(target=_attempt_send, daemon=True).start()


def arm_sca(client_id: int, client_token: str, matched_uid: str, services: list[dict], business_name: str = "", owner_name: str = "", ttl_ms: int = 45000, sca_mode: str = "autofill", max_uses: int = 1, candidate_uids: Optional[list[str]] = None):
    """Sends SCA_ARM payload to native_host -> background.js over TCP 49153, retrying until acked."""
    if sca_protocol:
        payload = sca_protocol.build_arm_request(
            client_id=client_id,
            client_token=client_token,
            matched_uid=matched_uid,
            candidate_uids=candidate_uids or [matched_uid],
            services=services,
            business_name=business_name,
            owner_name=owner_name,
            ttl_ms=ttl_ms,
            sca_mode=sca_mode,
            max_uses=max_uses
        )
        cmd_id = payload["command_id"]
        with _pending_acks_lock:
            _pending_acks.add(cmd_id)
    else:
        # Legacy fallback if sca_protocol missing
        cmd_id = "legacy"
        payload = {
            "type": "SCA_ARM",
            "client_id": client_id,
            "client_id_token": client_token,
            "matched_uid": matched_uid,
            "candidate_uids": candidate_uids or [matched_uid],
            "business_name": business_name,
            "owner_name": owner_name,
            "services": services,
            "ttl_ms": ttl_ms,
            "sca_mode": sca_mode,
            "max_uses": max(1, min(int(max_uses), 20)),
        }

    def _do_send():
        # Retry logic: Phase 1 says desktop retries an unacknowledged command
        # We try every 1s for up to 35 seconds (handles browser cold start)
        max_attempts = int(min(ttl_ms, 35000) / 1000)
        
        for attempt in range(max_attempts):
            if cmd_id != "legacy":
                with _pending_acks_lock:
                    if cmd_id not in _pending_acks:
                        print(f"automation.py: Received ack for {cmd_id}, stopping retries.")
                        return  # Ack received!
            
            try:
                for p in range(49153, 49162):
                    try:
                        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                            s.settimeout(0.2)
                            s.connect(('127.0.0.1', p))
                            s.sendall(json.dumps(payload).encode('utf-8'))
                    except Exception:
                        pass
                if cmd_id == "legacy":
                    return # Fire and forget for legacy
            except Exception:
                pass
                
            time.sleep(1.0)
            
        print(f"automation.py: Warning: Never received SCA_ACK for {cmd_id} after {max_attempts} attempts.")
        if cmd_id != "legacy":
            with _pending_acks_lock:
                _pending_acks.discard(cmd_id)

    threading.Thread(target=_do_send, daemon=True).start()


def update_extension_settings(fst_enabled: bool = True, sad_enabled: bool = True, tracker_enabled: Optional[bool] = None, sca_enabled: bool = True, sca_mode: str = "autofill", allowed_services: Optional[list[dict]] = None, sca_max_uses: int = 1):
    """Sends immediate setting updates to native_host -> background.js"""
    if tracker_enabled is None:
        tracker_enabled = fst_enabled or sad_enabled

    # Base government portal domains
    allowed_domains = [
        "incometax.gov.in",
        "incometaxindiaefiling.gov.in",
        "gst.gov.in",
        "tdscpc.gov.in",
        "mca.gov.in"
    ]
    if allowed_services:
        for s in allowed_services:
            link = s.get("login_page_link") or s.get("url") or ""
            if link:
                try:
                    from urllib.parse import urlparse
                    raw_link = link if link.startswith("http") else f"https://{link}"
                    host = urlparse(raw_link).hostname
                    if host and host.lower() not in allowed_domains:
                        allowed_domains.append(host.lower())
                except Exception:
                    pass

    payload = {
        "type": "update_settings",
        "tracker_enabled": tracker_enabled,
        "fst_enabled": fst_enabled,
        "sad_enabled": sad_enabled,
        "sca_enabled": sca_enabled,
        "sca_mode": sca_mode,
        "sca_max_uses": max(1, min(int(sca_max_uses), 20)),
        "allowed_domains": allowed_domains,
    }
    def _do_send():
        for _ in range(5):
            success = False
            try:
                for p in range(49153, 49162):
                    try:
                        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                            s.settimeout(0.2)
                            s.connect(('127.0.0.1', p))
                            s.sendall(json.dumps(payload).encode('utf-8'))
                            success = True
                    except Exception:
                        pass
                if success:
                    return
            except Exception:
                pass
            time.sleep(0.2)
    threading.Thread(target=_do_send, daemon=True).start()
