"""
tools/pre_dev/os_assist/fill_spike.py - OS Assist WP0 spike (docs/os_assist/os-assist-blueprint.md)
=====================================================================================================
EXPERIMENT ONLY. Fills a login page's boxes from OUTSIDE the browser, with no extension:

    method 1  UI Automation ValuePattern.SetValue        (no focus, no keystrokes)
    method 2  SendInput Unicode keystrokes               (fallback: focus the box, Ctrl+A, type)

It uses three IMAGINARY clients (below). Nothing real is ever filled, and the script:
  * never clicks, never presses Enter/Tab, never submits or dismisses anything;
  * fills only when the address bar shows that service's portal (re-read right before each write);
  * never prints a password; a password box is checked only by its bullet count (decision D2).

Usage (run from the APP folder, then switch to the browser within the wait time):

    python tools/pre_dev/os_assist/fill_spike.py                     # service picked from the address bar
    python tools/pre_dev/os_assist/fill_spike.py --field pass        # Income Tax page 2 (password page)
    python tools/pre_dev/os_assist/fill_spike.py --method keys       # method 2 only
    python tools/pre_dev/os_assist/fill_spike.py --method setvalue   # method 1 only, no fallback
    python tools/pre_dev/os_assist/fill_spike.py --focus             # Flutter (TRACES): click a box, it gets typed
    python tools/pre_dev/os_assist/fill_spike.py --list              # only list the boxes found, fill nothing
    python tools/pre_dev/os_assist/fill_spike.py --service gst --local   # the offline test page (test_login.html)

Every run appends one row per box to tools/pre_dev/os_assist/out/results.csv for the WP0 table.
"""

import argparse
import csv
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
APP = HERE.parents[2]
sys.path.insert(0, str(APP))

# ----------------------------------------------------------------------------- imaginary clients
SERVICES = {
    "it": {
        "name": "Income Tax",
        "hosts": ("eportal.incometax.gov.in", "incometax.gov.in"),
        "uid": "ABCPZ1234K",            # PAN shape, not a real PAN
        "password": "Sera#Test-IT1",
    },
    "gst": {
        "name": "GST",
        "hosts": ("services.gst.gov.in", "gst.gov.in"),
        "uid": "seratest_gst01",
        "password": "Sera#Test-GST2",
    },
    "traces": {
        "name": "TRACES",
        "hosts": ("www.tdscpc.gov.in", "tdscpc.gov.in"),
        "uid": "SERATRC01",
        "password": "Sera#Test-TR3",
    },
}

LOCAL_TITLE = "Sera OS Assist test"
BROWSERS = ("chrome.exe", "msedge.exe", "firefox.exe", "brave.exe")

CT_EDIT, CT_DOCUMENT = 50004, 50030
VERIFY_AFTER_S = (0.15, 1.0)     # first check, then again after the page had time to reset it

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


# ----------------------------------------------------------------------------- windows / process
def foreground() -> int:
    return int(user32.GetForegroundWindow() or 0)


def proc_name(hwnd: int) -> str:
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


def wait_for_browser(seconds: float) -> int:
    print(f"Switch to the browser showing the login page (waiting up to {seconds:.0f} s)...")
    end = time.time() + seconds
    while time.time() < end:
        hwnd = foreground()
        if hwnd and proc_name(hwnd) in BROWSERS:
            time.sleep(1.0)                       # let the alt-tab settle
            if foreground() == hwnd:
                return hwnd
        time.sleep(0.2)
    return 0


# ----------------------------------------------------------------------------- SendInput (method 2)
ULONG_PTR = ctypes.c_size_t


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0002, 0x0004
VK_CONTROL, VK_A, VK_DELETE = 0x11, 0x41, 0x2E


def _send(*events) -> int:
    arr = (INPUT * len(events))(*events)
    return user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))


def _vk(vk: int, up: bool = False) -> INPUT:
    return INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, 0)))


def _uni(code: int, up: bool = False) -> INPUT:
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
    return INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=KEYBDINPUT(0, code, flags, 0, 0)))


def type_text(text: str, hwnd: int) -> str:
    """Ctrl+A, Delete, then each character as a Unicode key press. Stops if the window changes."""
    _send(_vk(VK_CONTROL), _vk(VK_A), _vk(VK_A, True), _vk(VK_CONTROL, True))
    time.sleep(0.03)
    _send(_vk(VK_DELETE), _vk(VK_DELETE, True))
    for ch in text:
        if foreground() != hwnd:
            return "aborted: window changed while typing"
        units = ch.encode("utf-16-le")
        for i in range(0, len(units), 2):
            code = int.from_bytes(units[i:i + 2], "little")
            _send(_uni(code), _uni(code, True))
        time.sleep(0.012)
    return "typed"


# ----------------------------------------------------------------------------- UIA
class Uia:
    def __init__(self):
        import comtypes
        import comtypes.client
        comtypes.CoInitialize()
        self.c = comtypes.client.GetModule("UIAutomationCore.dll")
        try:
            self.uia = comtypes.client.CreateObject(self.c.CUIAutomation8, interface=self.c.IUIAutomation)
        except Exception:
            self.uia = comtypes.client.CreateObject(self.c.CUIAutomation, interface=self.c.IUIAutomation)
        self._bar_cache = {}

    def url(self, hwnd: int) -> str:
        from core.vsdc.vsdc_uia_text import address_bar_here
        return address_bar_here(self.uia, self.c, hwnd, self._bar_cache) or ""

    def documents(self, hwnd: int):
        root = self.uia.ElementFromHandle(hwnd)
        cond = self.uia.CreatePropertyCondition(self.c.UIA_ControlTypePropertyId, CT_DOCUMENT)
        found = root.FindAll(self.c.TreeScope_Descendants, cond)
        docs = [found.GetElement(i) for i in range(found.Length)] if found else []
        return [d for d in docs if not _safe(lambda: d.CurrentIsOffscreen, False)]

    def edits(self, doc):
        cond = self.uia.CreatePropertyCondition(self.c.UIA_ControlTypePropertyId, CT_EDIT)
        found = doc.FindAll(self.c.TreeScope_Descendants, cond)
        return [found.GetElement(i) for i in range(found.Length)] if found else []

    def value_pattern(self, el):
        p = el.GetCurrentPattern(self.c.UIA_ValuePatternId)
        return p.QueryInterface(self.c.IUIAutomationValuePattern) if p else None

    def focused(self):
        return _safe(lambda: self.uia.GetFocusedElement(), None)

    def same(self, a, b) -> bool:
        return bool(a is not None and b is not None and _safe(lambda: self.uia.CompareElements(a, b), False))


def _safe(fn, default):
    try:
        return fn()
    except Exception:
        return default


def describe(el) -> dict:
    r = _safe(lambda: el.CurrentBoundingRectangle, None)
    w = (r.right - r.left) if r else 0
    h = (r.bottom - r.top) if r else 0
    return {
        "id": _safe(lambda: el.CurrentAutomationId, "") or "",
        "name": (_safe(lambda: el.CurrentName, "") or "")[:40],
        "password": bool(_safe(lambda: el.CurrentIsPassword, False)),
        "offscreen": bool(_safe(lambda: el.CurrentIsOffscreen, False)),
        "enabled": bool(_safe(lambda: el.CurrentIsEnabled, True)),
        "aria": _safe(lambda: el.CurrentAriaProperties, "") or "",
        "size": (w, h),
    }


def visible(d: dict) -> bool:
    """The sera_dom.isVisible rules that UIA can see."""
    if d["offscreen"] or not d["enabled"] or d["size"][0] <= 0 or d["size"][1] <= 0:
        return False
    if d["id"].lower() == "hiddenpassword" or "hidden=true" in d["aria"].replace(" ", "").lower():
        return False
    return True


def find_fields(uia: Uia, hwnd: int, show: bool):
    """(user_el, pass_el) on the page; either may be None. Username = the visible non-password box
    closest before the password box, or the first one when there is no password box yet."""
    boxes = []
    for doc in uia.documents(hwnd):
        for el in uia.edits(doc):
            boxes.append((el, describe(el)))
    if show:
        print(f"\n{len(boxes)} edit box(es) on the page:")
        for i, (_el, d) in enumerate(boxes):
            print(f"  [{i}] {'PASSWORD' if d['password'] else 'text    '} visible={visible(d)!s:5} "
                  f"id={d['id']!r} name={d['name']!r} size={d['size']}")
    seen = [(el, d) for el, d in boxes if visible(d)]
    pass_i = next((i for i, (_e, d) in enumerate(seen) if d["password"]), None)
    pass_el = seen[pass_i][0] if pass_i is not None else None
    texts = [i for i, (_e, d) in enumerate(seen) if not d["password"]]
    if pass_i is not None:
        before = [i for i in texts if i < pass_i]
        user_i = before[-1] if before else None
    else:
        user_i = texts[0] if texts else None
    return (seen[user_i][0] if user_i is not None else None), pass_el


# ----------------------------------------------------------------------------- fill + verify
def check(uia: Uia, el, text: str, is_password: bool) -> str:
    vp = _safe(lambda: uia.value_pattern(el), None)
    if vp is None:
        return "no ValuePattern"
    got = _safe(lambda: vp.CurrentValue, None)
    if got is None:
        return "unreadable"
    if not is_password:
        return "OK" if got == text else f"mismatch ({len(got)} chars)"
    # D2: a password box is judged by its bullet count only; the text is never compared or shown.
    if got == text:
        return "WARNING plaintext exposed"
    if got == "":
        return "empty"
    if len(got) == len(text) and len(set(got)) == 1:
        return "OK (masked, length matches)"
    return f"masked, length {len(got)} vs {len(text)}"


def verify(uia: Uia, el, text: str, is_password: bool) -> str:
    out = ""
    for wait in VERIFY_AFTER_S:
        time.sleep(wait)
        out = check(uia, el, text, is_password)
        if not out.startswith("OK"):
            return out + f" (at {wait}s)"
    return out


def host_ok(uia: Uia, hwnd: int, svc: dict, local: bool) -> str:
    """'' when this window may be filled for svc, else the reason it may not."""
    url = uia.url(hwnd)
    if local:
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, 512)
        return "" if LOCAL_TITLE in title.value else f"not the local test page (title {title.value!r})"
    host = (urlparse(url if "://" in url else "https://" + url).hostname or "").lower()
    if any(host == h or host.endswith("." + h) for h in svc["hosts"]):
        return ""
    return f"address bar host {host!r} is not {svc['name']}"


def fill_one(uia: Uia, hwnd: int, el, text: str, is_password: bool, method: str, svc: dict, local: bool) -> dict:
    row = {"setvalue": "-", "verify1": "-", "keys": "-", "verify2": "-", "final": "failed"}
    if method in ("auto", "setvalue"):
        why = host_ok(uia, hwnd, svc, local)
        if why:
            row["final"] = "refused: " + why
            return row
        try:
            vp = uia.value_pattern(el)
            if vp is None:
                row["setvalue"] = "no ValuePattern"
            elif vp.CurrentIsReadOnly:
                row["setvalue"] = "read-only"
            else:
                vp.SetValue(text)
                row["setvalue"] = "called"
                row["verify1"] = verify(uia, el, text, is_password)
                if row["verify1"].startswith("OK"):
                    row["final"] = "OK setvalue"
                    return row
        except Exception as e:
            row["setvalue"] = f"error {type(e).__name__}: {str(e)[:60]}"
        if method == "setvalue":
            return row
    # method 2
    why = host_ok(uia, hwnd, svc, local)
    if why:
        row["final"] = "refused: " + why
        return row
    if foreground() != hwnd:
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.2)
    if foreground() != hwnd:
        row["keys"] = "browser not in front"
        return row
    _safe(lambda: el.SetFocus(), None)
    time.sleep(0.15)
    if not uia.same(uia.focused(), el):
        row["keys"] = "box did not take focus"
        return row
    row["keys"] = type_text(text, hwnd)
    if row["keys"] == "typed":
        row["verify2"] = verify(uia, el, text, is_password)
        if row["verify2"].startswith("OK"):
            row["final"] = "OK keys"
    return row


# ----------------------------------------------------------------------------- focus mode (Flutter)
def focus_mode(uia: Uia, hwnd: int, svc: dict, local: bool, seconds: float = 90) -> list:
    """Step 1: click the user box -> user ID typed. Step 2: click the password box -> password typed."""
    steps = [("user", svc["uid"], False), ("pass", svc["password"], True)]
    rows, done_el = [], None
    print("Step 1: click inside the User ID box on the page.")
    end = time.time() + seconds
    while steps and time.time() < end:
        time.sleep(0.2)
        if foreground() != hwnd:
            continue
        el = uia.focused()
        if el is None or _safe(lambda: el.CurrentControlType, 0) != CT_EDIT or uia.same(el, done_el):
            continue
        d = describe(el)
        name_id = (d["name"] + " " + d["id"]).lower()
        if any(k in name_id for k in ("address", "search", "url")):
            continue                                   # the browser's own address bar
        kind, text, is_pw = steps[0]
        why = host_ok(uia, hwnd, svc, local)
        if why:
            print("  refused:", why)
            return rows
        row = {"setvalue": "-", "verify1": "-", "keys": type_text(text, hwnd), "verify2": "-", "final": "failed"}
        if row["keys"] == "typed":
            row["verify2"] = verify(uia, el, text, is_pw or d["password"])
            row["final"] = "OK keys" if row["verify2"].startswith("OK") else "failed"
        rows.append((kind, row))
        print(f"  {kind}: {row['final']}  ({row['verify2']})")
        done_el = el
        steps.pop(0)
        if steps:
            print("Step 2: click inside the Password box on the page.")
    if steps:
        print("  timed out")
    return rows


# ----------------------------------------------------------------------------- main
def save(rows: list, browser: str, svc_key: str, url_host: str, method: str):
    out = HERE / "out"
    out.mkdir(exist_ok=True)
    path = out / "results.csv"
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "browser", "service", "host", "field", "method",
                        "setvalue", "verify1", "keys", "verify2", "final"])
        for field, r in rows:
            w.writerow([datetime.now().isoformat(timespec="seconds"), browser, svc_key, url_host, field, method,
                        r["setvalue"], r["verify1"], r["keys"], r["verify2"], r["final"]])
    print(f"\nSaved {len(rows)} row(s) to {path}")


def main() -> int:
    ap = argparse.ArgumentParser(description="OS Assist WP0 spike: fill a login page from outside the browser.")
    ap.add_argument("--service", choices=sorted(SERVICES), help="force a service (default: from the address bar)")
    ap.add_argument("--field", choices=("user", "pass", "both"), default="both")
    ap.add_argument("--method", choices=("auto", "setvalue", "keys"), default="auto")
    ap.add_argument("--focus", action="store_true", help="Flutter mode: type into the box you click")
    ap.add_argument("--list", action="store_true", help="only list the boxes found")
    ap.add_argument("--local", action="store_true", help="the offline test page (test_login.html)")
    ap.add_argument("--wait", type=float, default=15.0, help="seconds to wait for the browser to be in front")
    a = ap.parse_args()

    hwnd = wait_for_browser(a.wait)
    if not hwnd:
        print("No browser came to the front. Nothing done.")
        return 1
    browser = proc_name(hwnd)
    uia = Uia()
    url = uia.url(hwnd)
    host = (urlparse(url if "://" in url else "https://" + url).hostname or "").lower()

    svc_key = a.service
    if not svc_key and not a.local:
        svc_key = next((k for k, s in SERVICES.items()
                        if any(host == h or host.endswith("." + h) for h in s["hosts"])), None)
    if a.local and not svc_key:
        svc_key = "it"
    if not svc_key:
        print(f"{browser}: address bar host {host!r} is none of {', '.join(s['name'] for s in SERVICES.values())}. "
              "Nothing done.")
        return 1
    svc = SERVICES[svc_key]
    why = host_ok(uia, hwnd, svc, a.local)
    if why and not a.list:
        print(f"Refused: {why}. Nothing done.")
        return 1
    print(f"{browser} | {svc['name']} (imaginary client {svc['uid']}) | host {host or '(local)'}")

    if a.focus:
        rows = focus_mode(uia, hwnd, svc, a.local)
        if rows:
            save(rows, browser, svc_key, host, "focus")
        return 0

    user_el, pass_el = find_fields(uia, hwnd, show=True)
    print(f"\nUser box: {'found' if user_el is not None else 'NOT found'} | "
          f"Password box: {'found' if pass_el is not None else 'NOT found'}")
    if a.list:
        return 0

    rows = []
    if a.field in ("user", "both"):
        if user_el is not None:
            rows.append(("user", fill_one(uia, hwnd, user_el, svc["uid"], False, a.method, svc, a.local)))
        else:
            print("user: no box to fill")
    if a.field in ("pass", "both"):
        if pass_el is not None:
            rows.append(("pass", fill_one(uia, hwnd, pass_el, svc["password"], True, a.method, svc, a.local)))
        else:
            print("pass: no box to fill (Income Tax: press Continue yourself, then run again with --field pass)")

    print()
    for field, r in rows:
        print(f"{field:5} -> {r['final']:<14} setvalue={r['setvalue']} | verify={r['verify1']} | "
              f"keys={r['keys']} | verify={r['verify2']}")
    print("\nNow look at the page: does it accept the values (no 'required' error, the button enables)?")
    if rows:
        save(rows, browser, svc_key, host, a.method)
    return 0


if __name__ == "__main__":
    sys.exit(main())
