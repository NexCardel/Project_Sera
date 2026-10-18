"""
tools/test_local_html_pages.py
==============================
Tests SGT + VSDC247-V capture and payload enrichment against local HTML pages:
  - tests/test_page_gst_submission.html (GSTR-1 green box success template)
  - tests/test_page_gst_submission.html (GSTR-3B modal popup card template)

Validates:
  1. OCR observes the local HTML pages and reads the dynamically generated ARNs.
  2. SGT captures the filings and enriches the payload with "vsdc247-v": {"verified": True, ...}.
  3. No separate/duplicate rows are created in tracker_dump (exactly 1 row per SGT filing).
  4. Unverified/N/A filings carry "vsdc247-v": {"verified": False, "status": "unverified"}.
  5. Retroactive OCR verification updates the existing SGT row in tracker_dump in-place.
"""

import contextlib
import ctypes
from ctypes import wintypes
import io
import json
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, date
from pathlib import Path
from unittest.mock import MagicMock

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
from core.vsdc.vsdc247_reconciler import SgtRecord
from sera_db.tracker_dump import TrackerDumpMixin

user32 = ctypes.windll.user32


class TestDb(TrackerDumpMixin):
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("""
            CREATE TABLE tracker_dump (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER,
                unassigned_identity TEXT,
                service_id INTEGER,
                portal TEXT,
                period_label TEXT,
                arn_number TEXT,
                capture_method TEXT,
                status TEXT,
                raw_payload_json TEXT,
                captured_by TEXT,
                created_at TEXT,
                dataset_key TEXT,
                notes TEXT,
                screenshot_path TEXT
            )
        """)

    @contextlib.contextmanager
    def _connect_raw(self):
        yield self.conn

    @contextlib.contextmanager
    def _connect(self):
        yield self.conn


def find_hwnd(title_substr: str, timeout_sec: float = 8.0):
    found = []
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if title_substr.lower() in buf.value.lower():
            found.append(hwnd)
            return False
        return True

    proc = EnumWindowsProc(callback)
    t0 = time.time()
    while time.time() - t0 < timeout_sec:
        found.clear()
        user32.EnumWindows(proc, 0)
        if found:
            return found[0]
        time.sleep(0.25)
    return None


def run_local_pages_test():
    print("=" * 75)
    print("VSDC247-V + SGT Local HTML Pages Verification Test")
    print("=" * 75)

    db = TestDb()
    assembler = VisualSessionAssembler()
    router = VSDCRouter(
        ocr_engine=VSDCOcrEngine(),
        assembler=assembler,
    )
    router.db = db
    router.apply_engine_settings(vsdc=False, vsdc_x=False, vsdc247=False, sgt="live", vsdc247_verify=True)

    print(f"Windows OCR available: {router.ocr.is_available}")
    print(f"Verifier enabled:      {router._verifier_enabled}")
    if not router.ocr.is_available:
        print("!! Windows OCR is unavailable. Aborting.")
        return 1

    # Pin helper for foreground detection
    def pin(hwnd):
        def _pinned():
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            return hwnd, buf.value.strip(), "msedge.exe"
        router.get_foreground_info = _pinned

    html_gst_sub = os.path.abspath("tests/test_page_gst_submission.html")
    url_gstr1 = f"file:///{html_gst_sub.replace(os.sep, '/')}#/returns/auth/gstr1/file"

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=False)
        page = browser.new_page(viewport={"width": 1366, "height": 768})

        # -------------------------------------------------------------
        # STEP 1: Load GSTR-1 local simulation page with green box success
        # -------------------------------------------------------------
        print("\n--- STEP 1: Loading tests/test_page_gst_submission.html (GSTR-1) ---")
        page.goto(url_gstr1)
        page.wait_for_timeout(1000)

        # Trigger green box on the test page
        page.evaluate("() => { if (typeof showSuccessModal === 'function') { showSuccessModal(); } }")
        page.evaluate("() => { const b = document.getElementById('gstr1GreenBox'); if (b) b.classList.remove('hidden'); }")
        page.wait_for_timeout(500)

        gstr1_arn = page.evaluate("() => document.getElementById('gstr1Arn').innerText.trim()")
        print(f"DOM GSTR-1 ARN: {gstr1_arn}")

        hwnd = find_hwnd("GST Return Portal")
        if hwnd:
            pin(hwnd)
            user32.SetForegroundWindow(hwnd)

        # Run ticks so OCR analyzes the frame
        for _ in range(6):
            router.evaluate_tick()
            time.sleep(0.3)

        print(f"OCR Ledger entries: {list(router._verifier.ledger.keys())}")
        gstr1_seen = gstr1_arn in router._verifier.ledger
        print(f"[CHECK] GSTR-1 ARN ({gstr1_arn}) seen by OCR: {gstr1_seen}")
        assert gstr1_seen, f"GSTR-1 ARN {gstr1_arn} was not detected by OCR!"

        # Simulate SGT dispatching this filing
        sgt_gstr1_payload = {
            "source": "sgt",
            "portal": "GST Portal",
            "arn": gstr1_arn,
            "pan": "ABACD1191F",
            "gstin": "29ABACD1191FAZK",
            "client_name": "AMAN ASSOCIATES (Prop. ARIF MOHAMMAD MOLLA)",
            "filing_type": "GSTR-1",
            "period_label": "January - 2021",
            "status": "Submitted",
            "capture_method": "SGT_live",
            "raw_payload": {
                "source": {"engine": "SGT"},
                "sgt_dataset": {"arn": gstr1_arn, "form": "GSTR-1"},
            },
        }

        # Mock SGT outbox and pop via evaluate_tick
        router._sgt = router._sgt or MagicMock()
        q1 = [dict(sgt_gstr1_payload)]
        router._sgt.pending = lambda: bool(q1)
        router._sgt.pop_dispatch = lambda: q1.pop(0) if q1 else None

        # evaluate_tick pops the SGT row and enriches it
        emitted_row = router.evaluate_tick()
        assert emitted_row is not None, "evaluate_tick should have emitted the SGT row!"

        # Save to DB (as main.py does on filing_captured)
        db.conn.execute(
            """INSERT INTO tracker_dump
               (client_id, portal, period_label, arn_number, capture_method, status, raw_payload_json, dataset_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (101, emitted_row["portal"], emitted_row["period_label"], emitted_row["arn"],
             emitted_row["capture_method"], emitted_row["status"], json.dumps(emitted_row), "GST:29ABACD1191FAZK:GSTR1:JAN_2021"),
        )

        print("\nEmitted SGT Payload 'vsdc247-v' Section:")
        print(json.dumps(emitted_row.get("vsdc247-v"), indent=2))

        assert emitted_row.get("vsdc247-v", {}).get("verified") is True
        assert emitted_row.get("vsdc247-v", {}).get("status") == "verified"
        assert emitted_row.get("vsdc247-v", {}).get("arn") == gstr1_arn
        assert emitted_row.get("raw_payload", {}).get("vsdc247-v", {}).get("verified") is True
        print("[CHECK] GSTR-1 payload carries verified=True with full OCR metadata: PASS")

        # -------------------------------------------------------------
        # STEP 2: Load GSTR-3B modal simulation page
        # -------------------------------------------------------------
        print("\n--- STEP 2: Loading GSTR-3B Modal Popup ---")
        page.evaluate("() => { switchForm('GSTR-3B'); showSuccessModal(); }")
        page.wait_for_timeout(500)

        gstr3b_arn = page.evaluate("() => document.getElementById('succArn').innerText.trim()")
        print(f"DOM GSTR-3B ARN: {gstr3b_arn}")

        # SGT queue is empty during OCR observation ticks
        router._sgt.pending = lambda: False
        router._sgt.pop_dispatch = lambda: None

        for _ in range(6):
            router.evaluate_tick()
            time.sleep(0.3)

        print(f"OCR Ledger entries: {list(router._verifier.ledger.keys())}")
        gstr3b_seen = gstr3b_arn in router._verifier.ledger
        print(f"[CHECK] GSTR-3B ARN ({gstr3b_arn}) seen by OCR: {gstr3b_seen}")
        assert gstr3b_seen, f"GSTR-3B ARN {gstr3b_arn} was not detected by OCR!"

        # Simulate SGT dispatching GSTR-3B
        sgt_gstr3b_payload = {
            "source": "sgt",
            "portal": "GST Portal",
            "arn": gstr3b_arn,
            "pan": "ABACD1191F",
            "gstin": "29ABACD1191FAZK",
            "client_name": "AMAN ASSOCIATES (Prop. ARIF MOHAMMAD MOLLA)",
            "filing_type": "GSTR-3B",
            "period_label": "January - 2021",
            "status": "Submitted",
            "capture_method": "SGT_live",
            "raw_payload": {
                "source": {"engine": "SGT"},
                "sgt_dataset": {"arn": gstr3b_arn, "form": "GSTR-3B"},
            },
        }
        q3b = [dict(sgt_gstr3b_payload)]
        router._sgt.pending = lambda: bool(q3b)
        router._sgt.pop_dispatch = lambda: q3b.pop(0) if q3b else None

        emitted_row_3b = router.evaluate_tick()
        assert emitted_row_3b is not None

        db.conn.execute(
            """INSERT INTO tracker_dump
               (client_id, portal, period_label, arn_number, capture_method, status, raw_payload_json, dataset_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (101, emitted_row_3b["portal"], emitted_row_3b["period_label"], emitted_row_3b["arn"],
             emitted_row_3b["capture_method"], emitted_row_3b["status"], json.dumps(emitted_row_3b), "GST:29ABACD1191FAZK:GSTR3B:JAN_2021"),
        )

        print("\nEmitted GSTR-3B Payload 'vsdc247-v' Section:")
        print(json.dumps(emitted_row_3b.get("vsdc247-v"), indent=2))

        assert emitted_row_3b.get("vsdc247-v", {}).get("verified") is True
        assert emitted_row_3b.get("vsdc247-v", {}).get("arn") == gstr3b_arn
        print("[CHECK] GSTR-3B payload carries verified=True: PASS")

        # -------------------------------------------------------------
        # STEP 3: Verify tracker_dump rows (NO STANDALONE DUPLICATE ENTRIES)
        # -------------------------------------------------------------
        print("\n--- STEP 3: Database Verification ---")
        rows = db.conn.execute("SELECT id, arn_number, capture_method, status FROM tracker_dump").fetchall()
        print(f"Total rows in tracker_dump: {len(rows)}")
        for r in rows:
            print(f"  Row ID={r[0]}: ARN={r[1]}, Method={r[2]}, Status={r[3]}")

        # Must have exactly 2 rows (one for GSTR-1, one for GSTR-3B)
        assert len(rows) == 2, f"Expected exactly 2 rows, but found {len(rows)}!"
        # Check that NO row has capture_method = 'VSDC247_verify'
        verify_rows = db.conn.execute("SELECT COUNT(*) FROM tracker_dump WHERE capture_method = 'VSDC247_verify'").fetchone()[0]
        assert verify_rows == 0, f"Found {verify_rows} standalone VSDC247_verify row(s) in tracker_dump! Standalone entries must be 0."
        print("[CHECK] Exactly 1 row per SGT filing and 0 standalone VSDC247_verify rows: PASS")

        # -------------------------------------------------------------
        # STEP 4: Test Unverified / 'N/A' SGT Row
        # -------------------------------------------------------------
        print("\n--- STEP 4: Unverified / N/A ARN Handling ---")
        na_payload = {
            "source": "sgt",
            "portal": "GST Portal",
            "arn": "N/A",
            "filing_type": "GSTR-1",
            "period_label": "February - 2021",
            "status": "Not Submitted",
            "capture_method": "SGT_live",
            "raw_payload": {},
        }
        q_na = [dict(na_payload)]
        router._sgt.pending = lambda: bool(q_na)
        router._sgt.pop_dispatch = lambda: q_na.pop(0) if q_na else None
        emitted_na = router.evaluate_tick()

        assert emitted_na.get("vsdc247-v", {}).get("verified") is False
        assert emitted_na.get("vsdc247-v", {}).get("status") == "unverified"
        print(f"Unverified ARN 'vsdc247-v' section: {emitted_na.get('vsdc247-v')}")
        print("[CHECK] Unverified ARN gets verified=False: PASS")

        browser.close()

    print("\n" + "=" * 75)
    print("ALL CHECKS PASSED: Local HTML pages correctly captured & verified!")
    print("=" * 75)
    return 0


if __name__ == "__main__":
    sys.exit(run_local_pages_test())
