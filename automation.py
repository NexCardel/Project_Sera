"""
automation.py
--------------
Launches a persistent browser and injects credentials dynamically based 
on the configuration of a Service object, or routes them to the browser extension.
"""

import threading
import queue
import socket
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from PySide6.QtCore import QObject, Signal

SERA_CHROME_PROFILE_DIR = Path.home() / "AmanAssociates_Sera" / "chrome_profile"
BROWSER_CHANNEL = "chrome"

class _AutofillBridge(QObject):
    failed = Signal(str, str) 

class _BrowserManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None
        self._running = False
        self._jobs = queue.Queue()

    def request_autofill(self, service: dict, user_id: str, password: str, client_id: int, on_error=None):
        with self._lock:
            if not self._running:
                self._running = True
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()
        self._jobs.put((service, user_id, password, on_error))

    def _run(self):
        playwright = sync_playwright().start()
        try:
            SERA_CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(SERA_CHROME_PROFILE_DIR),
                channel=BROWSER_CHANNEL,
                headless=False,
                no_viewport=True,
                args=["--start-maximized", "--window-position=0,0"],
            )
            closed = threading.Event()
            context.on("close", lambda *_: closed.set())

            while not closed.is_set():
                try:
                    service, user_id, password, on_error = self._jobs.get(timeout=0.25)
                except queue.Empty:
                    continue
                try:
                    self._fill_new_tab(context, service, user_id, password)
                except Exception as e:
                    if on_error:
                        on_error(str(e))
        finally:
            with self._lock:
                self._running = False
            try:
                playwright.stop()
            except Exception:
                pass

    @staticmethod
    def _fill_new_tab(context, service: dict, user_id: str, password: str):
        try:
            context.clear_cookies()
        except Exception:
            pass

        try:
            page = context.new_page()
            page.bring_to_front()
            page.goto(service["login_page_link"], wait_until="domcontentloaded")
        except Exception as e:
            if "closed" in str(e).lower():
                return
            raise e

        u_sel = (service.get("username_selector") or "#panAdhaarUserId").strip()
        p_sel = (service.get("password_selector") or "#passwordInput").strip()

        # Append :visible filter to avoid hidden dummy inputs (like Google's name='hiddenPassword')
        u_sel_vis = f"{u_sel}:visible" if ":visible" not in u_sel else u_sel
        p_sel_vis = f"{p_sel}:visible" if ":visible" not in p_sel else p_sel

        # Step 1: Username Fill
        try:
            page.wait_for_selector(u_sel_vis, timeout=12000, state="visible")
            page.fill(u_sel_vis, user_id)
            try:
                page.dispatch_event(u_sel_vis, "blur")
            except Exception:
                pass
        except Exception as e:
            if "closed" in str(e).lower():
                return
            try:
                page.fill(u_sel, user_id)
            except Exception:
                pass

        # Step 2: Check if password field is immediately present (1-step login like GST)
        is_filled = False
        try:
            page.wait_for_selector(p_sel_vis, timeout=2000, state="visible")
            page.fill(p_sel_vis, password)
            is_filled = True
        except Exception as e:
            if "closed" in str(e).lower():
                return

        if not is_filled:
            # 2-Step Login Portal (like Google Sign-In, Income Tax ITR, etc.)
            try:
                next_clicked = False
                try:
                    next_btns = page.query_selector_all("button:has-text('Next'), button:has-text('Continue'), button:has-text('Submit'), #idSubmit_NAV_CD, button[type='submit']")
                    for btn in next_btns:
                        if btn.is_visible():
                            btn.click()
                            next_clicked = True
                            break
                except Exception:
                    pass

                if not next_clicked:
                    page.keyboard.press("Enter")

                # Wait for visible password field on the 2nd step
                page.wait_for_selector(p_sel_vis, timeout=10000, state="visible")

                # Check secure access checkbox if present
                try:
                    chk = page.query_selector("#checkSecureAccess, mat-checkbox")
                    if chk and chk.is_visible():
                        chk.click()
                except Exception:
                    pass

                page.fill(p_sel_vis, password)
                is_filled = True
            except Exception as e:
                err_msg = str(e).lower()
                if "closed" in err_msg or "target page" in err_msg:
                    return
                print(f"2-step automation filling status for {service.get('name', 'portal')}: {e}")

        # Dispatch Angular-compatible events for filled password element
        if is_filled:
            try:
                page.evaluate("""(sel, val) => {
                    const el = document.querySelector(sel) || document.querySelector("input[type='password']:not([tabindex='-1'])");
                    if (el) {
                        el.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }));
                        el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: val }));
                        el.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true, data: val }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                        el.dispatchEvent(new Event('blur', { bubbles: true }));
                    }
                }""", [p_sel, password])
            except Exception:
                pass


_manager = _BrowserManager()

def is_manual_portal(service: dict) -> bool:
    return service.get("automation_mode") == "manual"

def is_extension_portal(service: dict) -> bool:
    return service.get("automation_mode") == "extension"

def get_login_url(service: dict) -> str:
    return service["login_page_link"]

def autofill_login(service: dict, user_id: str, password: str, client_id: int, on_error=None):
    if is_manual_portal(service):
        raise ValueError(
            f"{service['name']} is configured as a manual-only portal -- use get_login_url() instead."
        )
    elif is_extension_portal(service):
        _send_to_extension(service, user_id, password, client_id, on_error)
    else:
        _manager.request_autofill(service, user_id, password, client_id, on_error=on_error)

def trigger_manual_assist(service: dict, user_id: str, password: str, client_id: int, on_error=None):
    """Open the portal and ask the companion extension to show Manual Assist.

    The extension keeps the values in its injected function closure; the
    visible widget only ever contains the generic User ID and Password labels.
    """
    # Manual Assist is an extension feature regardless of the portal's normal
    # autofill mode. Manual/automated portals can still use the companion
    # widget, so do not short-circuit based on automation_mode.
    _send_to_extension(service, user_id, password, client_id, on_error, mode="manual_assist")

import time
import webbrowser

def _send_to_extension(service: dict, user_id: str, password: str, client_id: int, on_error=None, mode="autofill"):
    """Sends the autofill payload to the native_host via TCP with retry & auto-launch fallback."""
    u_sel = (service.get("username_selector") or "").strip().replace("input [", "input[").replace("input ", "input")
    p_sel = (service.get("password_selector") or "").strip().replace("input [", "input[").replace("input ", "input")
    payload = {
        "type": "autofill",
        "mode": mode,
        "service_id": service["id"],
        "userid": user_id,
        "password": password,
        "portal": service["name"].lower(),
        "url": service["login_page_link"],
        "username_selector": u_sel,
        "password_selector": p_sel,
        "extension_flow": service.get("extension_flow", "double"),
        "success_selector": service.get("success_selector", ""),
        "arn_selector": service.get("arn_selector", ""),
        "client_id": client_id,
        "client_name": service.get("_client_name", service["name"]),
        "tracker_enabled": service.get("_tracker_enabled", True)
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
                    return  # Successfully sent payload
            except (ConnectionRefusedError, socket.timeout, OSError):
                # If host isn't listening, open browser to start background worker + native host
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

def update_extension_settings(tracker_enabled: bool):
    """Sends immediate setting updates to native_host -> background.js"""
    payload = {
        "type": "update_settings",
        "tracker_enabled": tracker_enabled
    }
    def _do_send():
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1.0)
                s.connect(('127.0.0.1', 49153))
                s.sendall(json.dumps(payload).encode('utf-8'))
        except Exception:
            pass
    threading.Thread(target=_do_send, daemon=True).start()
