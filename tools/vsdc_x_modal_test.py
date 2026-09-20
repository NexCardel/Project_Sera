"""
tools/vsdc_x_modal_test.py — VSDC-X extraction test against the GSTR-3B modal popup
======================================================================================
Drives tests/test_page_gst_submission.html to the GSTR-3B "Filing Successful" modal
state (the exact popup from the user's live-portal screenshot) and checks whether
VSDC-X (UI Automation) can read the ARN through it, in a real visible browser window.

Usage:
    python tools/vsdc_x_modal_test.py
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

# This terminal's codepage can't encode some characters the sample HTML uses
# (e.g. the check-mark glyph in the success modal) — re-wrap stdout so a
# print() with an unusual character doesn't crash the script mid-run.
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright

from core.vsdc import vsdc_uia_text
from core.vsdc.vsdc_regex import repair_gst_arn, repair_numeric_ack, classify_verification_status

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


def read_and_report(hwnd, label):
    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")
    res = vsdc_uia_text.read_page_text(hwnd)
    text = res.get("text", "")
    lines = res.get("lines", [])

    print("--- UIA-read lines ---")
    for ln in lines:
        print(f"  {ln!r}")

    arn = repair_gst_arn(text) or repair_numeric_ack(text)
    status = classify_verification_status(text)
    print(f"\nExtracted ARN    : {arn}")
    print(f"Extracted Status : {status}")
    return arn, text


def close_stale_windows(substr: str):
    """
    Closes any leftover window from a previous run of this script before
    starting a new one — separate script launches got the identical hwnd
    across runs, which means find_hwnd_by_title_substring was picking up a
    stale window instead of the freshly-launched one, silently comparing
    against the wrong browser instance.
    """
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

    close_stale_windows("GST Return Portal")

    print("Opening test harness and driving to GSTR-3B Filing Successful modal...")

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

        mode = os.environ.get("VSDC_TEST_MODE", "gstr3b_modal")
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
            # Trigger the success modal directly (skip OTP step — OTP-modal
            # UIA readability was already confirmed by the probe run)
            page.click("button:has-text('2. Trigger Filing Success')")
            page.wait_for_timeout(400)
            ground_truth_arn = page.inner_text("#succArn").strip()

        print(f"\nGround-truth ARN (from DOM, via Playwright): {ground_truth_arn}")

        # Poll REPEATEDLY, like the real router does (every ~0.35s, faster in
        # bursts) — not "wait once then read once". Chromium's accessibility
        # tree is known to build out incrementally in response to actual
        # queries, not purely elapsed time, so repeated FindAll calls might
        # succeed where a single delayed read didn't.
        poll_interval_sec = 1.0
        max_duration_sec = 6.0
        print(f"\nPolling every {poll_interval_sec}s for up to {max_duration_sec}s (like the live router would)...")

        t0 = time.perf_counter()
        attempt = 0
        arn = None
        last_lines = []
        while time.perf_counter() - t0 < max_duration_sec:
            attempt += 1
            res = vsdc_uia_text.read_page_text(hwnd)
            text = res.get("text", "")
            last_lines = res.get("lines", [])
            arn = repair_gst_arn(text) or repair_numeric_ack(text)
            elapsed = time.perf_counter() - t0
            matching_lines = [ln for ln in last_lines if "Filing Successful" in ln or "Acknowledgment" in ln or "ARN" in ln.upper()]
            print(f"  attempt {attempt:>3} @ {elapsed:5.2f}s — matching lines: {matching_lines} — ARN: {arn}")
            if arn:
                break
            time.sleep(poll_interval_sec)

        print("\n--- Full line dump of the LAST attempt ---")
        for ln in last_lines:
            print(f"  {ln!r}")

        print(f"\n{'=' * 70}")
        print(f"Ground truth        : {ground_truth_arn}")
        print(f"VSDC-X (polled)      : {arn}")
        print(f"Attempts needed      : {attempt}")
        print("RESULT: MATCH (repeated polling worked)" if arn == ground_truth_arn else "RESULT: STILL NOT CAPTURED after sustained polling")
        print("=" * 70)

        browser.close()

    print("\nDone.")


if __name__ == "__main__":
    main()
