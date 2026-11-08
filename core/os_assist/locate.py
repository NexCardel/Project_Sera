"""
OS Assist locate: find the browser window, its page Document and the login boxes (blueprint 4.2).
Runs on the OS Assist worker thread (core/os_assist/worker.py). Read-only: no pattern method is
called here, nothing is focused, clicked or typed.
"""

import ctypes
import ctypes.wintypes as wt
import os
import re
from typing import Any, List, Optional, Tuple

from core.os_assist import worker
from core.vsdc.vsdc_router import BROWSER_EXE_NAMES
from sca_protocol import host_matches, service_host

BROWSER_OWN_CLASS = "NativeViewHost"          # the browser's own panes, not the page
_ID_SELECTOR = re.compile(r"^#([A-Za-z_][\w\-]*)$")

user32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None
kernel32 = ctypes.windll.kernel32 if hasattr(ctypes, "windll") else None


def _safe(fn, default):
    try:
        return fn()
    except Exception:
        return default


def _proc_name(hwnd: int) -> str:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)       # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520)
        size = wt.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
    finally:
        kernel32.CloseHandle(h)
    return ""


def _is_browser(hwnd: int) -> bool:
    return bool(hwnd) and bool(user32.IsWindowVisible(hwnd)) and _proc_name(hwnd) in BROWSER_EXE_NAMES


def browser_windows() -> List[int]:
    """Visible top-level browser windows, front-most first (EnumWindows order is z-order)."""
    found: List[int] = []
    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def _cb(hwnd, _lp):
        h = int(hwnd or 0)
        if user32.IsWindowVisible(h) and user32.GetWindowTextLengthW(h) > 0 and _proc_name(h) in BROWSER_EXE_NAMES:
            found.append(h)
        return True

    user32.EnumWindows(proto(_cb), 0)
    return found


def read_url(hwnd: int) -> str:
    """The window's address-bar text ('' when unreadable)."""
    return _safe(lambda: worker.current_uia().url(hwnd), "") or ""


def _host_of(text: str) -> str:
    return service_host(text or "")


def window_for_host(host_or_url: str) -> int:
    """The browser window showing that portal; the one in front wins (D4). 0 when none."""
    want = _host_of(host_or_url)
    if not want:
        return 0
    fg = int(user32.GetForegroundWindow() or 0)
    ordered = ([fg] if _is_browser(fg) else []) + [h for h in browser_windows() if h != fg]
    for hwnd in ordered:
        if host_matches(_host_of(read_url(hwnd)), want):
            return hwnd
    return 0


def host_matches_service(hwnd: int, service: Any) -> bool:
    """The window's address bar is on the service's login-page portal."""
    link = service.get("login_page_link") if isinstance(service, dict) else getattr(service, "login_page_link", "")
    link = (link or "").strip()
    if not hwnd or not link:
        return False
    return host_matches(_host_of(read_url(hwnd)), link)


def page_documents(hwnd: int) -> list:
    """The on-screen page Documents of the window (never a background tab, never browser UI)."""
    uia = worker.current_uia()
    out = []
    for doc in _safe(lambda: uia.documents(hwnd), []):
        if _safe(lambda: doc.CurrentIsOffscreen, False):
            continue
        if _safe(lambda: doc.CurrentClassName, "") == BROWSER_OWN_CLASS:
            continue
        out.append(doc)
    return out


def page_rect(hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    """(left, top, right, bottom) of the first on-screen Document, for docking; None if none."""
    for doc in page_documents(hwnd):
        r = _safe(lambda: doc.CurrentBoundingRectangle, None)
        if r is not None and r.right > r.left and r.bottom > r.top:
            return (int(r.left), int(r.top), int(r.right), int(r.bottom))
    return None


def describe(el) -> dict:
    r = _safe(lambda: el.CurrentBoundingRectangle, None)
    return {
        "id": _safe(lambda: el.CurrentAutomationId, "") or "",
        "name": (_safe(lambda: el.CurrentName, "") or "")[:40],
        "password": bool(_safe(lambda: el.CurrentIsPassword, False)),
        "offscreen": bool(_safe(lambda: el.CurrentIsOffscreen, False)),
        "enabled": bool(_safe(lambda: el.CurrentIsEnabled, True)),
        "aria": _safe(lambda: el.CurrentAriaProperties, "") or "",
        "size": ((r.right - r.left), (r.bottom - r.top)) if r else (0, 0),
    }


def visible(d: dict) -> bool:
    """The sera_dom.isVisible rules that UIA can see."""
    if d["offscreen"] or not d["enabled"] or d["size"][0] <= 0 or d["size"][1] <= 0:
        return False
    if d["id"].lower() == "hiddenpassword" or d["name"].lower() == "hiddenpassword":
        return False
    return "hidden=true" not in d["aria"].replace(" ", "").lower()


def _selector_id(selector: Optional[str]) -> str:
    m = _ID_SELECTOR.match((selector or "").strip())
    return m.group(1) if m else ""


def find_fields(hwnd: int, username_selector: Optional[str] = None,
                password_selector: Optional[str] = None) -> Tuple[Any, Any]:
    """(user_el, pass_el); either may be None. A `#id` selector wins (AutomationId); any other
    selector shape is ignored. Otherwise: password = first visible password box; username = the
    visible non-password box closest before it, or the first one when there is no password box."""
    uia = worker.current_uia()
    seen = []
    for doc in page_documents(hwnd):
        for el in _safe(lambda: uia.edits(doc), []):
            d = describe(el)
            if visible(d):
                seen.append((el, d))

    def by_id(selector, want_password):
        wanted = _selector_id(selector)
        if not wanted:
            return None
        for i, (_el, d) in enumerate(seen):
            if d["id"] == wanted and d["password"] == want_password:
                return i
        return None

    pass_i = by_id(password_selector, True)
    if pass_i is None:
        pass_i = next((i for i, (_e, d) in enumerate(seen) if d["password"]), None)
    user_i = by_id(username_selector, False)
    if user_i is None:
        texts = [i for i, (_e, d) in enumerate(seen) if not d["password"]]
        if pass_i is not None:
            before = [i for i in texts if i < pass_i]
            user_i = before[-1] if before else None
        else:
            user_i = texts[0] if texts else None
    return (seen[user_i][0] if user_i is not None else None,
            seen[pass_i][0] if pass_i is not None else None)
