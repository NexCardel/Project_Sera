"""
tools/vsdc247_verify_e2e_test.py — Real-Window End-to-End Verifier Test
=======================================================================
Spawns a real browser window (Playwright / msedge) showing simulated GST pages:
  1. GST Login page with username
  2. GSTR-1 submission confirmation with ARN
  3. GSTR-3B submission confirmation with ARN

Feeds the window to the VSDCRouter with VSDC247_VERIFY=1 enabled.
Validates:
  - Verifier detects the GST login page and records client username link
  - Verifier detects GSTR-1 and GSTR-3B filings via real OCR frames
  - Verifier ledger observes the ARNs
  - Reconciler matches when SGT delivers or flags as missed with ntfy alert
    when SGT misses it.

Usage:
    python tools/vsdc247_verify_e2e_test.py
"""

import ctypes
from ctypes import wintypes
import io
import os
import sys
import tempfile
import time
from datetime import datetime, date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

os.environ["VSDC_ALLOW_LOCAL_TEST"] = "1"
os.environ["VSDC247_VERIFY"] = "1"
os.environ["VSDC_ALERT_CONFIG"] = os.path.join(os.environ.get("TEMP", "."), "no_such_vsdc_alert.json")
for _v in ("VSDC_ALERT_TOPIC", "VSDC_ALERT_SERVER"):
    os.environ.pop(_v, None)

from playwright.sync_api import sync_playwright
from core.vsdc.vsdc_router import VSDCRouter
from core.vsdc.vsdc_ocr import VSDCOcrEngine
from core.vsdc.vsdc_assembler import VisualSessionAssembler
from core.vsdc.vsdc247_verify import Verifier
from core.vsdc.vsdc247_reconciler import Reconciler

user32 = ctypes.windll.user32

GST_USERNAME = "AMANA9876F"
GSTR1_ARN = "AA2703260123456"
GSTR3B_ARN = "AA2703260222222"

SHELL = """<!DOCTYPE html><html><head><meta charset="UTF-8"><title>{title}</title>
<style>
body {{ font-family: Segoe UI, Arial, sans-serif; margin: 0; background: #f3f4f6; color: #1f2937; }}
header {{ background: #0b5394; color: #fff; padding: 14px 28px; font-size: 18px; display: flex; justify-content: space-between; }}
.c {{ max-width: 1000px; margin: 26px auto; padding: 0 24px; }}
.card {{ background: #ffffff; border: 1px solid #d1d5db; border-radius: 8px; padding: 24px 32px; margin: 20px 0; }}
.ok {{ background: #c8e6c9; border: 2px solid #2e7d32; border-radius: 8px; padding: 22px 28px; margin: 20px 0; }}
.ok h2 {{ color: #1b5e20; margin: 0 0 12px; font-size: 24px; }}
.field {{ margin: 16px 0; font-size: 18px; }}
.label {{ font-weight: bold; margin-bottom: 6px; }}
.val {{ font-size: 20px; color: #111827; }}
</style></head><body>
<header><div>Goods and Services Tax Portal</div><div>Dashboard &nbsp; Services &nbsp; Returns</div></header>
<div class="c">{body}</div></body></html>"""

PAGES = {
    "login.html": (
        "Goods and Services Tax - Login",
        f"""<div class="card">
        <h2>Login to your GST Account</h2>
        <div class="field"><div class="label">Username*</div><div class="val">{GST_USERNAME}</div></div>
        <div class="field"><div class="label">Password*</div><div class="val">••••••••</div></div>
        </div>"""
    ),
    "gstr1_filed.html": (
        "Goods and Services Tax - Returns - GSTR-1",
        f"""<div class="ok">
        <h2>GSTR1 Filing Successful</h2>
        <p>GSTR1 of GSTIN - 00AAAAA0000A1Z0 for the Return Period - March - 2025-26 has been successfully filed.</p>
        <div class="field">The Acknowledgment Reference Number: is <strong>{GSTR1_ARN}</strong>.</div>
        <div class="field">Return Type - GSTR1</div>
        <div class="field">FY - 2025-26</div>
        <div class="field">Return Period - March(Q)</div>
        <div class="field">Status - Filed</div>
        <div class="field">Date of Filing: {date.today().strftime('%d/%m/%Y')}</div>
        </div>"""
    ),
    "gstr3b_filed.html": (
        "Goods and Services Tax - Returns - GSTR-3B",
        f"""<div class="ok">
        <h2>Filing Successful</h2>
        <p>GSTR-3B of GSTIN 00AAAAA0000A1Z0 for the period Jan-Mar - 2026 has been successfully filed on {date.today().strftime('%d/%m/%Y')} at 12:00.</p>
        <div class="field">The Acknowledgment Reference Number: is <strong>{GSTR3B_ARN}</strong>.</div>
        <div class="field">Status - Filed</div>
        </div>"""
    ),
}

results = []


def check(label, ok, detail=""):
    results.append(ok)
    print(f"  [{'OK ' if ok else 'XX '}] {label}{('  ' + str(detail)) if detail else ''}")


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


def main():
    workdir = tempfile.mkdtemp(prefix="vsdc247_verify_e2e_")
    for name, (title, body) in PAGES.items():
        with open(os.path.join(workdir, name), "w", encoding="utf-8") as f:
            f.write(SHELL.format(title=title, body=body))

    alerts_sent = []
    events = []
    assembler = VisualSessionAssembler()
    router = VSDCRouter(
        ocr_engine=VSDCOcrEngine(),
        assembler=assembler,
        on_activity=lambda et, title, sub: events.append((et, title, sub))
    )

    print("Verifier enabled in router:", router._verifier_enabled)
    print("Windows OCR available:", router.ocr.is_available)
    if not router.ocr.is_available:
        print("Windows OCR is not available on this machine — cannot run real E2E.")
        return 0

    # In-memory mock SGT store
    mock_sgt_store = {}

    # Wire alert interceptor and custom lookup to reconciler
    if router._verifier is not None:
        router._verifier.reconciler._sgt_lookup = lambda arn: mock_sgt_store.get(arn)
        orig_alert = router._verifier.reconciler._on_alert
        def _alert_hook(kind, data):
            alerts_sent.append(("missed" if kind == "sgt_missed" else kind, data.get("arn"), data))
            if orig_alert:
                orig_alert(kind, data)
        router._verifier.reconciler._on_alert = _alert_hook

    def pin(hwnd):
        def _pinned():
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            return hwnd, buf.value.strip(), "msedge.exe"
        router.get_foreground_info = _pinned

    def show(page, name, simulated_url, title_part, ticks=6):
        page.goto("file:///" + os.path.join(workdir, name).replace(os.sep, "/") + "#" + simulated_url)
        page.wait_for_timeout(800)
        h = find_hwnd(title_part)
        if h:
            pin(h)
        for _ in range(ticks):
            router.evaluate_tick()
            time.sleep(0.3)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=False)
        page = browser.new_page(viewport={"width": 1366, "height": 768})

        print("\n== Step 1: GST Login page (registers username to window)")
        show(page, "login.html", "/services/login", "Goods and Services Tax", ticks=4)
        hwnd, _, _ = router.get_foreground_info()
        linked_client = router._verifier.client_for_window(hwnd) if router._verifier else None
        check("GST Login read by OCR", router._verifier_stats["logins"] >= 1, router._verifier_stats["logins"])
        check("Window linked to username", linked_client == GST_USERNAME, linked_client)

        print("\n== Step 2: GSTR-1 submission confirmation")
        show(page, "gstr1_filed.html", "/returns/auth/file", "Goods and Services Tax", ticks=8)
        check("GSTR-1 observed", GSTR1_ARN in router._verifier.ledger, f"Ledger keys: {list(router._verifier.ledger.keys())}")
        if GSTR1_ARN in router._verifier.ledger:
            entry1 = router._verifier.ledger[GSTR1_ARN]
            check("GSTR-1 form recognised", entry1.get("form") == "GSTR-1", entry1.get("form"))
            check("GSTR-1 username linked", (entry1.get("client") or {}).get("username") == GST_USERNAME, (entry1.get("client") or {}).get("username"))

        print("\n== Step 3: GSTR-3B submission confirmation")
        show(page, "gstr3b_filed.html", "/returns/auth/gstr3b/filing", "Goods and Services Tax", ticks=8)
        check("GSTR-3B observed", GSTR3B_ARN in router._verifier.ledger, f"Ledger keys: {list(router._verifier.ledger.keys())}")
        if GSTR3B_ARN in router._verifier.ledger:
            entry3 = router._verifier.ledger[GSTR3B_ARN]
            check("GSTR-3B form recognised", entry3.get("form") == "GSTR-3B", entry3.get("form"))

        print("\n== Step 4: Reconciliation — SGT matched for GSTR-1, missed for GSTR-3B")
        # Simulate SGT having delivered GSTR-1
        from core.vsdc.vsdc247_reconciler import SgtRecord
        mock_sgt_store[GSTR1_ARN] = SgtRecord(
            arn=GSTR1_ARN,
            form="GSTR-1",
            pan="ABCDE1234F",
            period="March 2026",
            captured_at=time.time(),
            source="sgt_test",
        )

        # Advance clock past grace period (default 90s)
        future_time = time.time() + 150.0
        resolved = router._verifier.reconcile_tick(future_time)
        outcomes = {r.arn: r.outcome for r in resolved}
        print("Reconciliation outcomes:", outcomes)

        check("GSTR-1 matched SGT", outcomes.get(GSTR1_ARN) in ("matched", "matched_client_differs"), outcomes.get(GSTR1_ARN))
        check("GSTR-3B flagged as missed", outcomes.get(GSTR3B_ARN) == "missed", outcomes.get(GSTR3B_ARN))
        check("Alert triggered for missed GSTR-3B", any(a[0] == "missed" and a[1] == GSTR3B_ARN for a in alerts_sent))

        browser.close()

    print("\n" + "=" * 60)
    print(f"{sum(results)}/{len(results)} checks passed")
    print("RESULT:", "PASS" if all(results) else "FAIL")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
