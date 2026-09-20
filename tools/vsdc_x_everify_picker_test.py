"""
tools/vsdc_x_everify_picker_test.py - the e-Verify return picker, end to end
============================================================================
Real Edge window -> real UI Automation read (VSDC-X) -> the real router, on a simulation of
the portal's "e-Verify / Discard Return" page (tests/test_page_everify_picker.html). Checks that
the card is captured as "Submitted, e-Verification pending" with its form, assessment year,
filing type, ack and portal date - and that the client's name is the person in the page header,
never a UI control's wording.

Nothing is written to any database and no phone alert can fire.

Usage:
    python tools/vsdc_x_everify_picker_test.py
"""

import ctypes
from ctypes import wintypes
import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
os.environ["VSDC_ALLOW_LOCAL_TEST"] = "1"          # the page is a local file (see core/vsdc/vsdc_scope.py)
os.environ["VSDC_ALERT_CONFIG"] = os.path.join(os.environ.get("TEMP", "."), "no_such_vsdc_alert.json")
for _v in ("VSDC_ALERT_TOPIC", "VSDC_ALERT_SERVER", "VSDC_UIA_ONLY", "VSDC247_ONLY", "VSDC247_MODE"):
    os.environ.pop(_v, None)

from playwright.sync_api import sync_playwright

from core.vsdc.vsdc_assembler import VisualSessionAssembler
from core.vsdc.vsdc_ocr import VSDCOcrEngine
from core.vsdc.vsdc_router import VSDCRouter

user32 = ctypes.windll.user32
PAGE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tests", "test_page_everify_picker.html"))
URL = "file:///" + PAGE.replace(os.sep, "/") + "#/dashboard/eVerifyReturn/eVerifyReturn-al"

results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"  [{'OK ' if ok else 'XX '}] {label}{('  ' + str(detail)) if detail != '' else ''}")


def find_hwnd(substr, must="Edge", timeout=10.0):
    found = {}
    Proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _):
        if user32.IsWindowVisible(h):
            n = user32.GetWindowTextLengthW(h)
            if n:
                b = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(h, b, n + 1)
                if substr.lower() in b.value.lower() and must.lower() in b.value.lower():
                    found["h"] = h
                    return False
        return True

    proc = Proc(cb)
    t0 = time.time()
    while time.time() - t0 < timeout:
        found.clear()
        user32.EnumWindows(proc, 0)
        if found:
            return found["h"]
        time.sleep(0.25)
    return None


def title_of(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value.strip()


def main():
    events = []
    assembler = VisualSessionAssembler()
    router = VSDCRouter(ocr_engine=VSDCOcrEngine(), assembler=assembler,
                        on_activity=lambda *a: events.append((a, dict(router.activity_context))))

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=False)
        page = browser.new_page(viewport={"width": 1366, "height": 900})
        page.goto(URL)
        page.wait_for_timeout(1200)
        hwnd = find_hwnd("e-Verify Return")
        if not hwnd:
            print("Could not find the Edge window.")
            return 2
        router.get_foreground_info = lambda: (hwnd, title_of(hwnd), "msedge.exe")

        print("\n== The picker, read through real UI Automation")
        got = []
        for _ in range(12):
            r = router.evaluate_tick()
            if r:
                got.append(r)
            time.sleep(0.4)
        browser.close()

    check("exactly one dataset captured", len(got) == 1, len(got))
    if got:
        d = got[0]
        check("status is pending e-verification", d["status"] == "Submitted (Not e-Verified)", d["status"])
        check("ack read exactly", d["arn"] == "123456789290826", d["arn"])
        check("PAN read", d["pan"] == "ABCPD5678E", d["pan"])
        check("form = ITR-4", d["filing_type"] == "ITR-4", d["filing_type"])
        check("period = AY 2026-27", d["period_label"] == "AY 2026-27", d["period_label"])
        check("filing type (preference) = Original", d["filing_preference"] == "Original", d["filing_preference"])
        check("client name is the header's, not a control's", d["client_name"] == "ARJUN VERMA", d["client_name"])
        check("filed-on date is the portal's own", d["raw_payload"]["assembler_captures"][0]["filing_date"].startswith("2026-08-29"),
              d["raw_payload"]["assembler_captures"][0]["filing_date"])
        check("captured by VSDC-X", d["capture_method"] == "VSDC-X_itr_everify_pending", d["capture_method"])
    hud = [(a[1], ctx) for a, ctx in events if a[0] == "capture"]
    check("HUD announced the capture with form / filing type / period",
          bool(hud) and hud[0][1].get("form") == "ITR-4" and hud[0][1].get("filing_pref") == "Original"
          and hud[0][1].get("period") == "AY 2026-27", hud[0] if hud else None)

    print("\n" + "=" * 60)
    print(f"{sum(results)}/{len(results)} checks passed")
    print("RESULT:", "PASS" if all(results) else "FAIL")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
