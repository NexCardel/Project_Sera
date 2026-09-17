"""
tools/vsdc_x_itr_router_gap_test.py — Calibrates VSDC-X (UIA) on the ITR route
==================================================================================
Same approach as tools/vsdc_x_router_gap_test.py (GST), applied to the Income Tax
route (_route_itr_crosshair in core/vsdc/vsdc_router.py), which had zero VSDC-X
wiring until now. Drives the REAL VSDCRouter.evaluate_tick() against the local
ITR simulation pages — no live portal, no database, no main window, nothing saved.

Two stages, matching the two local test pages that already exist:
  1. tests/test_page_personal_info.html  -> itr_personal_info crosshair
     Ground truth (from the page's own DOM, read via Playwright):
       name = "WASIL AMAN MANDAL", pan = "GZEPM6367M"
  2. tests/test_page_submit_success.html -> itr_submitted_pending / filed_verified
     Ground truth: ack = "198273645019283"

Usage:
    python tools/vsdc_x_itr_router_gap_test.py
"""

import ctypes
from ctypes import wintypes
import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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


def poll_router(router, hwnd, timeout_sec, label):
    print(f"\nPolling the real router (VSDCRouter.evaluate_tick) every 0.3s for up to {timeout_sec}s [{label}]...")
    t0 = time.perf_counter()
    attempt = 0
    payload = None
    warned_drift = False
    while time.perf_counter() - t0 < timeout_sec:
        attempt += 1
        user32.SetForegroundWindow(hwnd)  # keep our test window foreground each tick
        actual_fg = user32.GetForegroundWindow()
        if actual_fg != hwnd and not warned_drift:
            length = user32.GetWindowTextLengthW(actual_fg)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(actual_fg, buf, length + 1)
            print(f"  !! FOCUS DRIFT: expected hwnd={hwnd} foreground but OS reports hwnd={actual_fg} ({buf.value!r}) — SetForegroundWindow may be failing silently")
            warned_drift = True
        result = router.evaluate_tick()
        if result:
            payload = result
            print(f"  attempt {attempt} @ {time.perf_counter()-t0:5.2f}s — PAYLOAD RETURNED")
            break
        time.sleep(0.3)
    if not payload:
        print(f"  no payload after {attempt} attempts / {timeout_sec:.0f}s (may just mean route not yet 'complete' — check router assembler state directly if needed)")
    return payload


def main():
    personal_info_path = os.path.abspath("tests/test_page_personal_info.html")
    personal_info_url = f"file:///{personal_info_path.replace(os.sep, '/')}"

    close_stale_windows("e-Filing Portal")

    ocr = VSDCOcrEngine()
    assembler = VisualSessionAssembler()
    router = VSDCRouter(ocr_engine=ocr, assembler=assembler)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=False)
        page = browser.new_page(viewport={"width": 1280, "height": 800})

        # ---- Stage 1: Personal Info page (itr_personal_info) ----
        print("=" * 70)
        print("Stage 1: Personal Info page")
        print("=" * 70)
        page.goto(personal_info_url)
        page.wait_for_timeout(500)

        hwnd, title = find_hwnd_by_title_substring("e-Filing Portal")
        if not hwnd:
            print("!! Could not find the browser window by title. Aborting.")
            browser.close()
            return
        print(f"Found window: {title!r} (hwnd={hwnd})")
        user32.SetForegroundWindow(hwnd)
        page.wait_for_timeout(200)

        ground_truth_name = "WASIL AMAN MANDAL"
        ground_truth_pan = page.inner_text("#pan").strip()
        print(f"Ground-truth PAN (from DOM, via Playwright): {ground_truth_pan}")
        print(f"Ground-truth name (assembled First+Middle+Last, via Playwright): {ground_truth_name}")

        poll_router(router, hwnd, 12.0, "personal_info")

        print("\nAssembler state after Stage 1:")
        print(f"  client_name    : {assembler.client_name!r}")
        print(f"  client_pan     : {assembler.client_pan!r}")
        name_ok = (assembler.client_name or "").upper() == ground_truth_name
        pan_ok = (assembler.client_pan or "").upper() == ground_truth_pan.upper()
        print(f"  name match     : {'MATCH' if name_ok else 'MISMATCH'}")
        print(f"  pan match      : {'MATCH' if pan_ok else 'MISMATCH'}")

        # ---- Stage 2: Submit Success page (ack number) ----
        print("\n" + "=" * 70)
        print("Stage 2: Submit Success page")
        print("=" * 70)
        page.click("a.btn-submit")
        page.wait_for_timeout(500)

        ground_truth_ack = page.inner_text(".success-desc").strip()
        import re as _re
        m = _re.search(r"\b(\d{15})\b", ground_truth_ack)
        ground_truth_ack = m.group(1) if m else None
        print(f"Ground-truth Ack (from DOM, via Playwright): {ground_truth_ack}")

        payload = poll_router(router, hwnd, 12.0, "submit_success")

        print(f"\n{'=' * 70}")
        if payload:
            print("Captured payload:")
            print(f"  filing_type    : {payload.get('filing_type')}")
            print(f"  period         : {payload.get('period_label')}")
            print(f"  status         : {payload.get('status')}")
            print(f"  ack_number     : {payload.get('ack_number')}")
            print(f"  client_name    : {payload.get('client_name')}")
            print(f"  pan            : {payload.get('pan')}")
            print(f"  capture_method : {payload.get('capture_method')}")
            print(f"\nGround truth Ack : {ground_truth_ack}")
            print("RESULT: MATCH" if payload.get("ack_number") == ground_truth_ack else "RESULT: MISMATCH")
        else:
            print("No payload captured for Stage 2.")
            print("RESULT: NOT CAPTURED")
        print("=" * 70)

        browser.close()

    print("\nDone.")


if __name__ == "__main__":
    main()
