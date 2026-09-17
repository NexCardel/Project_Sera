"""
tools/vsdc_x_html_sample_test.py — VSDC-X extraction test against local sample HTML
======================================================================================
Tests whether VSDC-X (UI Automation) can correctly read submission/ARN data from
tests/test_portal_success.html — a static GST return-success mock (Transaction ID
AA27032419827364, GSTR-3B, March 2024) — WITHOUT needing a live portal session.

UI Automation reads a real OS window's accessibility tree, so it can't be pointed
at a headless Playwright screenshot the way the existing OCR test harness
(tests/test_vsdc_with_sample_html.py) is. This opens the sample in a real, visible
browser window via Playwright, finds its native Win32 window handle, and feeds it
through the exact same core/vsdc/vsdc_uia_text.py + core/vsdc/vsdc_regex.py path
the live pipeline uses.

Usage:
    python tools/vsdc_x_html_sample_test.py
"""

import ctypes
from ctypes import wintypes
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright

from core.vsdc import vsdc_uia_text
from core.vsdc.vsdc_regex import repair_gst_arn, repair_numeric_ack, classify_verification_status

user32 = ctypes.windll.user32


def find_hwnd_by_title_substring(substr: str, timeout_sec: float = 10.0):
    """Enumerates top-level windows looking for one whose title contains substr."""
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


def main():
    html_path = os.path.abspath("tests/test_portal_success.html")
    url = f"file:///{html_path.replace(os.sep, '/')}"

    print("=" * 70)
    print("VSDC-X sample HTML test — GST submission/ARN extraction")
    print("=" * 70)
    print(f"Opening: {url}\n")

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

        print("Reading via VSDC-X (UI Automation)...\n")
        res = vsdc_uia_text.read_page_text(hwnd)
        text = res.get("text", "")
        lines = res.get("lines", [])

        print("--- UIA-read lines ---")
        for ln in lines:
            print(f"  {ln!r}")

        arn = repair_gst_arn(text) or repair_numeric_ack(text)
        status = classify_verification_status(text)

        print("\n--- Extraction Results ---")
        print(f"ARN / Transaction ID : {arn}")
        print(f"Status                : {status}")

        expected_arn = "AA27032419827364"
        print(f"\nExpected ARN          : {expected_arn}")
        print("RESULT: MATCH" if arn == expected_arn else "RESULT: MISMATCH")

        browser.close()

    print("\nDone.")


if __name__ == "__main__":
    main()
