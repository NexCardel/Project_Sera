"""
tests/test_vsdc_with_sample_html.py — Integration Test of VSDC using Sample HTML Files
=====================================================================================
Renders real sample portal HTML pages (test_page_personal_info.html and
test_page_submit_success.html) with Playwright, runs VSDC regional frame cropping,
native Windows.Media.Ocr recognition, regex extraction, and visual session assembly.
"""

import os
import io
import unittest
from PIL import Image
from playwright.sync_api import sync_playwright

from core.vsdc.vsdc_crosshairs import match_url_crosshair
from core.vsdc.vsdc_ocr import VSDCOcrEngine
from core.vsdc.vsdc_regex import (
    repair_numeric_ack,
    extract_pan,
    classify_verification_status,
)
from core.vsdc.vsdc_name_parser import extract_name_from_ocr_lines
from core.vsdc.vsdc_assembler import VisualSessionAssembler


class TestVSDCSampleHtml(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.ocr = VSDCOcrEngine()
        if not cls.ocr.is_available:
            raise unittest.SkipTest("Windows.Media.Ocr engine not available on this environment")

    def test_end_to_end_with_sample_html(self):
        assembler = VisualSessionAssembler()

        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 800})

            # ─── Stage 1: Personal Information HTML Page ─────────────────────
            info_html_path = os.path.abspath("tests/test_page_personal_info.html")
            info_url = f"file:///{info_html_path.replace(os.sep, '/')}"
            page.goto(info_url)

            # Match crosshair against URL/title
            crosshair_1 = match_url_crosshair(page.url) or match_url_crosshair(page.title())
            self.assertIsNotNone(crosshair_1, "Expected crosshair match for personal info page")
            assembler.record_step(info_url, crosshair_1.id)

            # Take page screenshot and convert to PIL Image
            info_bytes = page.screenshot()
            info_img = Image.open(io.BytesIO(info_bytes)).convert("RGB")

            # OCR header region for Name and PAN
            header_scan = self.ocr.scan_image(info_img, region_type="header")
            print("\n--- Stage 1 (Header OCR Output) ---")
            print("Lines:", header_scan["lines"])

            pan_found = extract_pan(header_scan["text"])
            name_found = extract_name_from_ocr_lines(header_scan["lines"])

            # Also scan center card if not in header
            if not pan_found or not name_found:
                center_scan = self.ocr.scan_image(info_img, region_type="center_card")
                if not pan_found:
                    pan_found = extract_pan(center_scan["text"])
                if not name_found:
                    name_found = extract_name_from_ocr_lines(center_scan["lines"])

            print("Extracted PAN:", pan_found)
            print("Extracted Name:", name_found)

            self.assertEqual(pan_found, "GZEPM6367M", "PAN must match GZEPM6367M")
            self.assertEqual(name_found, "WASIL AMAN MANDAL", "Name must match WASIL AMAN MANDAL")

            assembler.update_identity(pan=pan_found, name=name_found, portal="Income Tax")

            # ─── Stage 2: Return Submit Success HTML Page ────────────────────
            success_html_path = os.path.abspath("tests/test_page_submit_success.html")
            success_url = f"file:///{success_html_path.replace(os.sep, '/')}"
            page.goto(success_url)
            # The page builds today's ack on load (an ack carries its own filing date), so the
            # ground truth is whatever the page itself shows.
            expected_ack = page.inner_text("#ackNumber").strip()
            self.assertRegex(expected_ack, r"^\d{15}$")

            # Route hash in sample file: #/foreturns-ay26/fo-itr1-ay2026/fo-e-verify-later
            page.wait_for_timeout(300) # wait for hash to settle
            full_route = page.evaluate("() => window.location.href")

            crosshair_2 = match_url_crosshair(full_route) or match_url_crosshair(page.title())
            self.assertIsNotNone(crosshair_2, "Expected crosshair match for submit success page")
            self.assertEqual(crosshair_2.id, "itr_submitted_pending")
            self.assertTrue(crosshair_2.is_terminal_submission)

            assembler.record_step(full_route, crosshair_2.id)

            # Take screenshot and scan receipt card
            success_bytes = page.screenshot()
            success_img = Image.open(io.BytesIO(success_bytes)).convert("RGB")

            receipt_scan = self.ocr.scan_image(success_img, region_type="receipt_card")
            print("\n--- Stage 2 (Receipt Card OCR Output) ---")
            print("Text:\n", receipt_scan["text"])

            ack_found = repair_numeric_ack(receipt_scan["text"])
            status_found = classify_verification_status(receipt_scan["text"])

            print("Extracted Ack Number:", ack_found)
            print("Extracted Status:", status_found)

            self.assertEqual(ack_found, expected_ack, "Ack number must match the one the page shows")
            self.assertEqual(status_found, "Submitted (Not e-Verified)")

            # Record submission & flush master payload
            assembler.record_submission(
                ack_number=ack_found,
                status=status_found,
                filing_type="ITR-1",
                period_label="AY 2026-27",
                raw_text=receipt_scan["text"],
                crosshair_id=crosshair_2.id,
            )
            master_payload = assembler.seal_and_flush()

            browser.close()

        # ─── Assert Final Master Payload Contract ────────────────────────────
        print("\n--- Final Assembled Master Payload ---")
        print(master_payload)

        self.assertIsNotNone(master_payload)
        self.assertEqual(master_payload["source"], "vsdc_optical")
        self.assertEqual(master_payload["pan"], "GZEPM6367M")
        self.assertEqual(master_payload["client_name"], "WASIL AMAN MANDAL")
        self.assertEqual(master_payload["arn"], expected_ack)
        self.assertEqual(master_payload["status"], "Submitted (Not e-Verified)")
        self.assertEqual(len(master_payload["raw_payload"]["assembler_captures"]), 1)
        print("\n[SUCCESS] VSDC End-to-End Test with Sample HTML Passed Successfully!")

    def test_direct_single_page_landing_on_submit_success(self):
        """
        Simulates the user opening ONLY test_page_submit_success.html directly in a browser.
        Verifies that VSDC resolves both the receipt card (Ack) and header fallback (PAN/Name)
        in a single page landing without having visited the personal info page first.
        """
        assembler = VisualSessionAssembler()

        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 800})

            success_html_path = os.path.abspath("tests/test_page_submit_success.html")
            success_url = f"file:///{success_html_path.replace(os.sep, '/')}"
            page.goto(success_url)
            page.wait_for_timeout(300)
            expected_ack = page.inner_text("#ackNumber").strip()

            full_route = page.evaluate("() => window.location.href")
            crosshair = match_url_crosshair(full_route) or match_url_crosshair(page.title())
            self.assertIsNotNone(crosshair)

            # Take page screenshot
            success_bytes = page.screenshot()
            success_img = Image.open(io.BytesIO(success_bytes)).convert("RGB")

            # 1. Receipt card scan
            receipt_scan = self.ocr.scan_image(success_img, region_type=crosshair.target_crop)
            ack_found = repair_numeric_ack(receipt_scan["text"])
            status_found = classify_verification_status(receipt_scan["text"])
            self.assertEqual(ack_found, expected_ack)

            # 2. Header fallback scan on the SAME image (because PAN is in header profile pill)
            header_scan = self.ocr.scan_image(success_img, region_type="header")
            pan_found = extract_pan(header_scan["text"])
            name_found = extract_name_from_ocr_lines(header_scan["lines"])
            self.assertEqual(pan_found, "GZEPM6367M")
            self.assertEqual(name_found, "WASIL AMAN MANDAL")

            assembler.update_identity(pan=pan_found, name=name_found, portal="Income Tax")
            assembler.record_submission(
                ack_number=ack_found,
                status=status_found,
                filing_type="ITR-1",
                period_label="AY 2026-27",
                raw_text=receipt_scan["text"],
                crosshair_id=crosshair.id,
            )
            payload = assembler.seal_and_flush()

            browser.close()

        self.assertIsNotNone(payload)
        self.assertEqual(payload["pan"], "GZEPM6367M")
        self.assertEqual(payload["client_name"], "WASIL AMAN MANDAL")
        self.assertEqual(payload["arn"], expected_ack)
        print("\n[SUCCESS] Direct single-page landing test passed seamlessly!")


if __name__ == "__main__":
    unittest.main()
