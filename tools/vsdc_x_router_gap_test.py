"""
tools/vsdc_x_router_gap_test.py — Tests the Gap 2 / Gap 4 fixes through the REAL router
============================================================================================
Unlike tools/vsdc_x_modal_test.py (which calls vsdc_uia_text directly, bypassing the
router entirely), this drives the actual VSDCRouter.evaluate_tick() against the local
GST submission simulation — exercising the exact code paths that were just changed:
  - Gap 4: ARN detection now tries VSDC-X before OCR (was the reverse).
  - Gap 2: the gst_filing_file_success/gst_filing_success identity block now merges
    UIA-derived fields, not just OCR's.

No database, no main window — same no-side-effects approach as vsdc_console_watch.py.

Usage:
    python tools/vsdc_x_router_gap_test.py            # GSTR-3B modal
    VSDC_TEST_MODE=gstr1_inline python tools/vsdc_x_router_gap_test.py   # GSTR-1 inline
"""

import ctypes
from ctypes import wintypes
import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# These tools drive local simulation pages (file://), which VSDC refuses by default -
# it only ever looks at the two tax portals. See core/vsdc/vsdc_scope.py.
os.environ.setdefault("VSDC_ALLOW_LOCAL_TEST", "1")
os.environ["VSDC_ALERT_CONFIG"] = os.path.join(os.environ.get("TEMP", "."), "no_such_vsdc_alert.json")   # dev tools must never push real phone alerts
for _v in ("VSDC_ALERT_TOPIC", "VSDC_ALERT_SERVER"):
    os.environ.pop(_v, None)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright

from core.vsdc.vsdc_router import VSDCRouter
from core.vsdc.vsdc_ocr import VSDCOcrEngine
from core.vsdc.vsdc_assembler import VisualSessionAssembler

user32 = ctypes.windll.user32


def find_hwnd_by_title_substring(substr: str, timeout_sec: float = 10.0):
    found = {}
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if substr.lower() in buf.value.lower():
            found["hwnd"] = hwnd
            found["title"] = buf.value
            return False
        return True

    proc = EnumWindowsProc(callback)
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout_sec:
        found.clear()
        user32.EnumWindows(proc, 0)
        if found:
            return found["hwnd"], found["title"]
        time.sleep(0.3)
    return None, None


def close_stale_windows(substr: str):
    WM_CLOSE = 0x0010
    closed_any = False
    while True:
        hwnd, title = find_hwnd_by_title_substring(substr, timeout_sec=0.5)
        if not hwnd:
            break
        print(f"Closing stale leftover window from a previous run: {title!r} (hwnd={hwnd})")
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        closed_any = True
        time.sleep(1.0)
    if closed_any:
        time.sleep(1.0)


def main():
    html_path = os.path.abspath("tests/test_page_gst_submission.html")
    url = f"file:///{html_path.replace(os.sep, '/')}"
    mode = os.environ.get("VSDC_TEST_MODE", "gstr3b_modal")

    close_stale_windows("GST Return Portal")

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=False)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(url)
        page.wait_for_timeout(500)

        hwnd, title = find_hwnd_by_title_substring("GST Return Portal")
        if not hwnd:
            print("!! Could not find the browser window by title. Aborting.")
            browser.close()
            return
        print(f"Found window: {title!r} (hwnd={hwnd})")
        user32.SetForegroundWindow(hwnd)
        page.wait_for_timeout(200)

        if mode == "gstr1_inline":
            print("Mode: GSTR-1 inline green-box success (not a modal)")
            page.click("button:has-text('GSTR-1 Simulation')")
            page.wait_for_timeout(300)
            page.click("button:has-text('2. Trigger Filing Success')")
            page.wait_for_timeout(400)
            ground_truth_arn = page.inner_text("#gstr1Arn").strip()
        else:
            print("Mode: GSTR-3B modal popup success")
            page.click("button:has-text('GSTR-3B Simulation')")
            page.wait_for_timeout(300)
            page.click("button:has-text('2. Trigger Filing Success')")
            page.wait_for_timeout(400)
            ground_truth_arn = page.inner_text("#succArn").strip()

        print(f"Ground-truth ARN (from DOM, via Playwright): {ground_truth_arn}")

        # Drive the REAL router — same class the production app uses.
        ocr = VSDCOcrEngine()
        assembler = VisualSessionAssembler()
        router = VSDCRouter(ocr_engine=ocr, assembler=assembler)

        print("\nPolling the real router (VSDCRouter.evaluate_tick) every 0.3s for up to 15s...")
        t0 = time.perf_counter()
        attempt = 0
        payload = None
        while time.perf_counter() - t0 < 15.0:
            attempt += 1
            user32.SetForegroundWindow(hwnd)  # keep our test window foreground each tick
            result = router.evaluate_tick()
            if result:
                payload = result
                print(f"  attempt {attempt} @ {time.perf_counter()-t0:5.2f}s — PAYLOAD RETURNED")
                break
            time.sleep(0.3)

        print(f"\n{'=' * 70}")
        if payload:
            print("Captured payload:")
            print(f"  filing_type : {payload.get('filing_type')}")
            print(f"  period      : {payload.get('period_label')}")
            print(f"  status      : {payload.get('status')}")
            print(f"  arn         : {payload.get('arn')}")
            print(f"  client_name : {payload.get('client_name')}")
            print(f"  trade_name  : {payload.get('trade_name')}")
            print(f"  gstin       : {payload.get('gstin')}")
            print(f"  capture_method : {payload.get('capture_method')}")
            print(f"\nGround truth ARN : {ground_truth_arn}")
            print("RESULT: MATCH" if payload.get("arn") == ground_truth_arn else "RESULT: MISMATCH")
        else:
            print(f"No payload captured after {attempt} attempts / 15s.")
            print("RESULT: NOT CAPTURED")
        print("=" * 70)

        browser.close()

    print("\nDone.")


if __name__ == "__main__":
    main()
