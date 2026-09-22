"""
automation.py
--------------
Routes autofill, SMTI (Manual Assist), and MECP (Manual Extension Copy/Paste)
payloads to the Companion Extension over the WebSocket bridge (ui/ws_bridge.py).
"""

import threading
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

BASE_PORTAL_DOMAINS = (
    "incometax.gov.in",
    "incometaxindiaefiling.gov.in",
    "gst.gov.in",
    "tdscpc.gov.in",
    "mca.gov.in",
)


def allowed_portal_domains(services: Optional[list] = None) -> list:
    """The portal hosts the extension may act on: the base government portals plus every
    configured service's login host."""
    from urllib.parse import urlparse
    domains = list(BASE_PORTAL_DOMAINS)
    for s in services or []:
        link = (s or {}).get("login_page_link") or (s or {}).get("url") or ""
        if not link:
            continue
        try:
            host = urlparse(link if link.startswith("http") else f"https://{link}").hostname
        except Exception:
            host = None
        if host and host.lower() not in domains:
            domains.append(host.lower())
    return domains


# Registry for tracking extension acknowledgements
_pending_acks = set()
_pending_acks_lock = threading.Lock()

def register_ack(command_id: str):
    """Called by ws_bridge when an SCA_ACK is received."""
    with _pending_acks_lock:
        if command_id in _pending_acks:
            _pending_acks.remove(command_id)

class _AutofillBridge(QObject):
    failed = Signal(str, str)


def is_manual_portal(service: dict) -> bool:
    mode = str(service.get("automation_mode") or "extension").strip().lower()
    return mode == "manual"


def is_extension_portal(service: dict) -> bool:
    mode = str(service.get("automation_mode") or "extension").strip().lower()
    return mode != "manual"


def is_itr_service(service: dict) -> bool:
    """Returns True ONLY if the service corresponds to the Income Tax (ITR) portal."""
    if not service or not isinstance(service, dict):
        return False
    name = str(service.get("name") or "").strip().lower()
    link = str(service.get("login_page_link") or "").strip().lower()
    # Negative filters: portals that are explicitly NOT Income Tax
    non_itr_keywords = ("gst", "traces", "tds", "epfo", "pf", "mca", "icegate", "esi", "gem")
    if any(kw in name or kw in link for kw in non_itr_keywords):
        return False
    return (
        any(kw in name for kw in ("income tax", "incometax", "itr", "income-tax", "income_tax", "eportal"))
        or any(kw in link for kw in ("incometax.gov.in", "incometaxindiaefiling.gov.in", "eportal.incometax"))
    )


def is_gst_service(service: dict) -> bool:
    """Returns True if the service corresponds to the Goods and Services Tax (GST) portal."""
    if not service or not isinstance(service, dict):
        return False
    name = str(service.get("name") or "").strip().lower()
    link = str(service.get("login_page_link") or "").strip().lower()
    return "gst" in name or "gst.gov.in" in link


def get_login_url(service: dict) -> str:
    return service["login_page_link"]


def autofill_login(service: dict, user_id: str, password: str, client_id: int, on_error=None):
    _send_to_extension(service, user_id, password, client_id, on_error, mode="autofill")


def trigger_manual_assist(service: dict, user_id: str, password: str, client_id: int, on_error=None):
    """Open the portal and ask the companion extension to show SMTI Manual Assist."""
    _send_to_extension(service, user_id, password, client_id, on_error, mode="manual_assist")


def trigger_mecp(service: dict, user_id: str, password: str, client_id: int, on_error=None, scc_mode: bool = False, scc_combos: list | None = None):
    """Open the portal and ask the companion extension to show the MECP floating card widget."""
    if scc_mode and not is_itr_service(service):
        scc_mode = False
        scc_combos = None
    _send_to_extension(service, user_id, password, client_id, on_error, mode="mecp", scc_mode=scc_mode, scc_combos=scc_combos)


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


def _send_to_extension(service: dict, user_id: str, password: str, client_id: int, on_error=None, mode="autofill", scc_mode: bool = False, scc_combos: list | None = None):
    """Sends the autofill/SMTI/MECP payload to the extension via the WebSocket bridge, with retry & auto-launch fallback."""
    # Defense-in-depth: SCC is strictly an ITR-only one-time utility
    if scc_mode and not is_itr_service(service):
        scc_mode = False
        scc_combos = []

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
        "scc_mode": scc_mode,
        "scc_combos": scc_combos or [],
    }

    def _attempt_send():
        from ui import ws_bridge
        max_attempts = 20
        launched_browser = False

        for attempt in range(1, max_attempts + 1):
            bridge = ws_bridge.get_active_bridge()
            if bridge and bridge.send_first(payload):
                return
            if not launched_browser:
                launched_browser = True
                try:
                    open_in_default_browser(service.get("login_page_link", ""))
                except Exception:
                    pass
            time.sleep(0.5)
            if on_error and attempt == max_attempts:
                on_error(
                    "Could not connect to the Sera Extension after 10 seconds.\n"
                    "Please ensure the extension is installed and enabled in your browser."
                )

    threading.Thread(target=_attempt_send, daemon=True).start()


def arm_sca(arm_request: dict, attempts_s: int = 35):
    """Sends an SCA arm (protocol v2: NO passwords - see sca_protocol) to every connected
    browser, once a second until one acknowledges it (a browser may still be starting)."""
    from ui import ws_bridge
    cmd_id = arm_request["command_id"]
    with _pending_acks_lock:
        _pending_acks.add(cmd_id)

    def _do_send():
        for _ in range(max(1, int(attempts_s))):
            with _pending_acks_lock:
                if cmd_id not in _pending_acks:
                    return  # acknowledged
            bridge = ws_bridge.get_active_bridge()
            if bridge:
                bridge.broadcast(arm_request)
            time.sleep(1.0)
        print(f"automation.py: no browser acknowledged SCA arm {cmd_id}")
        with _pending_acks_lock:
            _pending_acks.discard(cmd_id)

    threading.Thread(target=_do_send, daemon=True).start()


def update_extension_settings(fst_enabled: bool = True, sdc_enabled: bool = True, vsdc_enabled: bool = True, tracker_enabled: Optional[bool] = None, sca_enabled: bool = True, sca_mode: str = "autofill", allowed_services: Optional[list[dict]] = None, sca_max_uses: int = 1, registered_pans: Optional[list[str]] = None, scc_settings: Optional[dict] = None):
    """Sends immediate setting updates to every connected browser's background.js."""
    from ui import ws_bridge
    if tracker_enabled is None:
        tracker_enabled = sdc_enabled or fst_enabled or vsdc_enabled

    allowed_domains = allowed_portal_domains(allowed_services)

    payload = {
        "type": "update_settings",
        "tracker_enabled": tracker_enabled,
        "sdc_enabled": sdc_enabled,
        "vsdc_enabled": vsdc_enabled,
        "fst_enabled": fst_enabled,
        "sca_enabled": sca_enabled,
        "sca_mode": sca_mode,
        "sca_max_uses": max(1, min(int(sca_max_uses), 20)),
        "allowed_domains": allowed_domains,
    }
    if registered_pans is not None:
        payload["registered_pans"] = registered_pans
    if scc_settings is not None:
        payload["scc_settings"] = scc_settings

    def _do_send():
        for _ in range(5):
            bridge = ws_bridge.get_active_bridge()
            if bridge and bridge.broadcast(payload) > 0:
                return
            time.sleep(0.2)
    threading.Thread(target=_do_send, daemon=True).start()
