"""
tools/vsdc247_e2e_test.py — VSDC247 end to end, on the real pipeline
=====================================================================
Real Edge window -> real screenshot -> real Windows OCR -> real green-box detection ->
the real VSDCRouter. Every page sits on a route no crosshair recognises, so anything captured
here was captured by VSDC247 alone.

It renders its own confirmation-style pages (green banner, ack/ARN, form, period) and checks
both directions: real submissions ARE captured, and the pages that look similar but are not
submissions - a revised-return wizard printing the ORIGINAL return's ack, a history list, a
failure message, help copy about the future - are NOT.

Usage:
    python tools/vsdc247_e2e_test.py

Nothing is written to any database; the tracker is never touched.
"""

import ctypes
from ctypes import wintypes
import io
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
# These pages are local files, which VSDC refuses by default - it only ever looks at the two
# tax portals. See core/vsdc/vsdc_scope.py.
os.environ.setdefault("VSDC_ALLOW_LOCAL_TEST", "1")
os.environ["VSDC_ALERT_CONFIG"] = os.path.join(os.environ.get("TEMP", "."), "no_such_vsdc_alert.json")   # dev tools must never push real phone alerts
for _v in ("VSDC_ALERT_TOPIC", "VSDC_ALERT_SERVER"):
    os.environ.pop(_v, None)
os.environ.pop("VSDC247_MODE", None)

from playwright.sync_api import sync_playwright

from core.vsdc.vsdc_router import VSDCRouter
from core.vsdc.vsdc_ocr import VSDCOcrEngine
from core.vsdc.vsdc_assembler import VisualSessionAssembler

user32 = ctypes.windll.user32


def ack_on(d):
    """A real-shaped ITR ack: nine digits plus the filing date as DDMMYY."""
    return "901036690" + d.strftime("%d%m%y")


NEW_ACK = ack_on(datetime.now())
OLD_ACK = ack_on(datetime.now() - timedelta(days=350))
OTHER_ACK = ack_on(datetime.now() - timedelta(days=40))

SHELL = """<!DOCTYPE html><html><head><meta charset="UTF-8"><title>{title}</title>
<style>body{{font-family:Segoe UI,Arial,sans-serif;margin:0;background:#f3f4f6;color:#1f2937}}
header{{background:#0b5394;color:#fff;padding:14px 28px;font-size:18px;display:flex;justify-content:space-between}}
.c{{max-width:1000px;margin:26px auto;padding:0 24px}}
.ok{{background:#dff0e3;border:2px solid #2e7d32;border-radius:8px;padding:22px 28px;margin:20px 0}}
.ok h2{{color:#1b5e20;margin:0 0 12px;font-size:26px}}
.bad{{background:#fbe4e6;border:2px solid #c62828;border-radius:8px;padding:22px 28px;margin:20px 0}}
.info{{background:#e8f0fe;border:2px solid #4a6fd8;border-radius:8px;padding:22px 28px;margin:20px 0}}
.row{{margin:8px 0;font-size:19px}}</style></head><body>
<header><div>e-Filing Anywhere Anytime</div><div>Dashboard &nbsp; e-File &nbsp; Services &nbsp; Help</div></header>
<div class="c">{body}</div></body></html>"""

PAGES = {
    "s0_ordinary.html": ("Income Tax Portal - Home", "<h1>Welcome</h1><p>No pending actions.</p>"),
    "a_itr_confirm.html": ("Income Tax Portal - Submission",
        f'<div class="ok"><h2>Your return has been submitted successfully</h2>'
        f'<div class="row">Acknowledgement Number : {NEW_ACK}</div>'
        f'<div class="row">ITR-4 &nbsp; Assessment Year 2026-27</div></div>'),
    "b_revised_wizard.html": ("Income Tax Portal - Revised Return",
        f'<h1>File revised return u/s 139(5)</h1><div class="info">'
        f'<div class="row">Acknowledgement Number of Original Return : {OLD_ACK}</div>'
        f'<div class="row">Date of Original Filing : 15-Sep-2025</div></div>'),
    "c_history_list.html": ("Income Tax Portal - Filed Returns",
        f'<h1>14 Filings till date</h1><div class="info"><div class="row">Acknowledgement No : {NEW_ACK}</div>'
        f'<div class="row">Successfully e-verified</div></div>'
        f'<div class="info"><div class="row">Acknowledgement No : {OTHER_ACK}</div></div>'),
    "d_failure.html": ("Income Tax Portal - Error",
        f'<div class="bad"><h2>Submission failed. Please try again</h2>'
        f'<div class="row">Acknowledgement Number : {NEW_ACK}</div></div>'),
    "e_gst_confirm.html": ("Goods and Services Tax - Returns",
        '<div class="ok"><h2>Your return has been filed successfully</h2>'
        '<div class="row">ARN : AA270826000123Z</div>'
        '<div class="row">GSTR-3B &nbsp; Return Period : August 2026</div></div>'),
    "f_help_copy.html": ("Income Tax Portal - Help",
        f'<h1>Before you submit</h1><div class="info"><div class="row">You will be notified once your return '
        f'has been submitted successfully</div><div class="row">Acknowledgement Number : {NEW_ACK}</div></div>'),
}

results = []


def check(label, ok, detail=""):
    results.append(ok)
    print(f"  [{'OK ' if ok else 'XX '}] {label}{('  ' + detail) if detail else ''}")


def find_hwnd(substr, timeout_sec=10.0):
    found = {}
    Proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if substr.lower() in buf.value.lower():
            found["hwnd"] = hwnd
            return False
        return True

    proc = Proc(cb)
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout_sec:
        found.clear()
        user32.EnumWindows(proc, 0)
        if found:
            return found["hwnd"]
        time.sleep(0.25)
    return None


def close_stale():
    for t in ("Income Tax Portal", "Goods and Services Tax"):
        while True:
            h = find_hwnd(t, timeout_sec=0.4)
            if not h:
                break
            user32.PostMessageW(h, 0x0010, 0, 0)
            time.sleep(0.8)


def main():
    workdir = tempfile.mkdtemp(prefix="vsdc247_e2e_")
    for name, (title, body) in PAGES.items():
        with open(os.path.join(workdir, name), "w", encoding="utf-8") as f:
            f.write(SHELL.format(title=title, body=body))

    close_stale()
    events = []
    assembler = VisualSessionAssembler()
    router = VSDCRouter(ocr_engine=VSDCOcrEngine(), assembler=assembler,
                        on_activity=lambda et, title, sub: events.append((et, title, sub)))
    print("VSDC247 mode:", router._mode_247, "| OCR available:", router.ocr.is_available)
    if not router.ocr.is_available:
        print("Windows OCR is not available on this machine - cannot run.")
        return 2

    def pin(hwnd):
        def _pinned():
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            return hwnd, buf.value.strip(), "msedge.exe"
        router.get_foreground_info = _pinned

    def show(page, name, title_part, ticks=10):
        page.goto("file:///" + os.path.join(workdir, name).replace(os.sep, "/") + "#/some/unmapped/route")
        page.wait_for_timeout(900)
        pin(find_hwnd(title_part))
        got = []
        for _ in range(ticks):
            r = router.evaluate_tick()
            if r:
                got.append(r)
            time.sleep(0.4)
        return got

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=False)
        page = browser.new_page(viewport={"width": 1366, "height": 768})

        print("\n== S0  ordinary page: the baseline")
        got = show(page, "s0_ordinary.html", "Income Tax Portal", ticks=4)
        check("nothing captured, nothing shown", not got and not events)

        print("\n== A  ITR confirmation on a route with NO crosshair, client not known yet")
        del events[:]
        got = show(page, "a_itr_confirm.html", "Income Tax Portal")
        a = got[0] if got else None
        check("captured with no crosshair", bool(a))
        if a:
            check("ARN read by real OCR", a["arn"] == NEW_ACK, a["arn"])
            check("tagged VSDC247", a["capture_method"] == "VSDC247_itr_ack", a["capture_method"])
            check("form + period read off the screen", (a["filing_type"], a["period_label"]) == ("ITR-4", "AY 2026-27"))
            check("unattributed: pan empty", a["pan"] == "" and a["identity_resolved"] is False)
            check("no page text stored", a["raw_text"] == "" and a["raw_payload"]["raw_text"] == "")
            check("green box recognised", a["raw_payload"]["vsdc247"]["green_box"] is True)
        check("HUD prompt: captured but client unknown",
              any(e[0] == "prompt" and "client unknown" in e[1] for e in events))

        print("\n== A2  the client becomes known: the same ARN is re-sent attributed, once")
        assembler.update_identity(pan="BJYPM4326D", name="ARJUN KUMAR VERMA", portal="Income Tax")
        again = [r for r in (router.evaluate_tick() or None for _ in range(4)) if r]
        check("attributed re-send", bool(again) and again[0]["pan"] == "BJYPM4326D" and again[0]["arn"] == NEW_ACK)
        check("never sent a third time", not any(router.evaluate_tick() for _ in range(3)))

        for label, name, title in (
            ("B  revised-return wizard prints the ORIGINAL (old) ack", "b_revised_wizard.html", "Income Tax Portal"),
            ("C  history list: several acks, one dated today", "c_history_list.html", "Income Tax Portal"),
            ("D  failure page carrying an ack", "d_failure.html", "Income Tax Portal"),
            ("F  help copy about the future, with an ack", "f_help_copy.html", "Income Tax Portal"),
        ):
            print(f"\n== {label}")
            check("nothing captured", not show(page, name, title))

        print("\n== E  GST confirmation: ARN + wording + form + green box")
        assembler.update_identity(gstin="19CJLPM0265M1ZO", name="AMAN ASSOCIATES", portal="GST Portal")
        got = show(page, "e_gst_confirm.html", "Goods and Services Tax", ticks=12)
        check("GST ARN captured", bool(got) and got[0]["arn"] == "AA270826000123Z" and got[0]["filing_type"] == "GSTR-3B")

        browser.close()

    print("\n" + "=" * 60)
    print(f"{sum(results)}/{len(results)} checks passed")
    print("RESULT:", "PASS" if all(results) else "FAIL")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
