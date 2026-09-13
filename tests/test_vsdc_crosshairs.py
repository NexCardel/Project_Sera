"""
tests/test_vsdc_crosshairs.py — Unit Tests for VSDC Crosshair Matching
"""

import unittest
from core.vsdc.vsdc_crosshairs import match_url_crosshair, ITR_CROSSHAIRS, GST_CROSSHAIRS


class TestVSDCCrosshairs(unittest.TestCase):

    def test_itr_crosshair_matching(self):
        # 1. itr_filed_verified
        c = match_url_crosshair("https://eportal.incometax.gov.in/iec/foservices/#/dashboard/fo-return-success")
        self.assertIsNotNone(c)
        self.assertEqual(c.id, "itr_filed_verified")
        self.assertTrue(c.is_terminal_submission)

        c = match_url_crosshair("https://eportal.incometax.gov.in/iec/foservices/#/dashboard/fo-e-verify-now-success")
        self.assertEqual(c.id, "itr_filed_verified")

        # 2. itr_everify_return
        c = match_url_crosshair("https://eportal.incometax.gov.in/iec/foservices/#/dashboard/eVerifyReturn")
        self.assertEqual(c.id, "itr_everify_return")

        # 3. itr_submitted_pending
        c = match_url_crosshair("https://eportal.incometax.gov.in/iec/foservices/#/dashboard/fo-e-verify-later")
        self.assertEqual(c.id, "itr_submitted_pending")

        # 4. itr_view_filed_returns
        c = match_url_crosshair("https://eportal.incometax.gov.in/iec/foservices/#/dashboard/fo-view-filed-returns")
        self.assertEqual(c.id, "itr_view_filed_returns")

        # 5. itr_personal_info
        c = match_url_crosshair("https://eportal.incometax.gov.in/iec/foservices/#/dashboard/personal_information")
        self.assertEqual(c.id, "itr_personal_info")

        # 6. itr_form_select
        c = match_url_crosshair("https://eportal.incometax.gov.in/iec/foservices/#/dashboard/fo-select-itr-form")
        self.assertEqual(c.id, "itr_form_select")

        # 7. itr_landing
        c = match_url_crosshair("https://eportal.incometax.gov.in/iec/foservices/#/dashboard/fileIncomeTaxReturn")
        self.assertEqual(c.id, "itr_landing")

        # 8. itr_login_auth (Identity Seeding - NOT session boundary)
        c = match_url_crosshair("https://eportal.incometax.gov.in/iec/foservices/#/login")
        self.assertEqual(c.id, "itr_login_auth")
        self.assertFalse(c.is_session_boundary)
        self.assertEqual(c.target_crop, "center_card")

        c = match_url_crosshair("https://eportal.incometax.gov.in/iec/foservices/#/login/password")
        self.assertEqual(c.id, "itr_login_auth")
        self.assertFalse(c.is_session_boundary)
        self.assertEqual(c.target_crop, "center_card")

        # 9. itr_logout (Session boundary)
        c = match_url_crosshair("https://eportal.incometax.gov.in/iec/foservices/#/logout")
        self.assertEqual(c.id, "itr_logout")
        self.assertTrue(c.is_session_boundary)

    def test_gst_crosshair_matching(self):
        # 1. gst_filing_success
        c = match_url_crosshair("https://services.gst.gov.in/services/auth/gstr1-success")
        self.assertIsNotNone(c)
        self.assertEqual(c.id, "gst_filing_success")
        self.assertTrue(c.is_terminal_submission)

        # 2. gst_filing_file_success
        c = match_url_crosshair("https://return.gst.gov.in/returns/auth/file")
        self.assertEqual(c.id, "gst_filing_file_success")

        # 3. gst_form_details
        c = match_url_crosshair("https://return.gst.gov.in/returns/auth/gstr-3b")
        self.assertEqual(c.id, "gst_form_details")

        c = match_url_crosshair("https://return.gst.gov.in/returns/auth/cmp-08")
        self.assertEqual(c.id, "gst_form_details")

        # 4. gst_returns_dashboard
        c = match_url_crosshair("https://services.gst.gov.in/services/auth/returns")
        self.assertEqual(c.id, "gst_returns_dashboard")

        # 5. gst_welcome_calendar
        c = match_url_crosshair("https://services.gst.gov.in/services/auth/fowelcome")
        self.assertEqual(c.id, "gst_welcome_calendar")

        # 6. gst_logout
        c = match_url_crosshair("https://services.gst.gov.in/services/auth/logout")
        self.assertEqual(c.id, "gst_logout")
        self.assertTrue(c.is_session_boundary)

    def test_local_path_and_title_matching(self):
        # Local file path with file:///
        c = match_url_crosshair("file:///C:/Users/Nex/Downloads/Project Sera/APP/tests/test_page_submit_success.html")
        self.assertIsNotNone(c)
        self.assertEqual(c.id, "itr_submitted_pending")

        # Local Windows path with backslashes
        c = match_url_crosshair("C:\\Users\\Nex\\Downloads\\Project Sera\\APP\\tests\\test_page_submit_success.html")
        self.assertIsNotNone(c)
        self.assertEqual(c.id, "itr_submitted_pending")

        # Window title fallback matching
        c = match_url_crosshair("e-Filing: Filing Confirmation - Personal - Microsoft Edge")
        self.assertIsNotNone(c)
        self.assertEqual(c.id, "itr_submitted_pending")

        # Personal info local path
        c = match_url_crosshair("file:///C:/Users/Nex/Downloads/Project Sera/APP/tests/test_page_personal_info.html")
        self.assertIsNotNone(c)
        self.assertEqual(c.id, "itr_personal_info")

    def test_itr_login_auth_captures_pan_only(self):
        """Verifies that on itr_login_auth (password page), only PAN is captured and names are strictly ignored."""
        from unittest.mock import MagicMock
        from core.vsdc.vsdc_router import VSDCRouter
        from core.vsdc.vsdc_assembler import VisualSessionAssembler
        from PIL import Image

        mock_ocr = MagicMock()
        mock_ocr.capture_window_image.return_value = Image.new("RGB", (800, 600), color="white")
        # Text simulating the e-Filing password page with PAN and a Secure Access Message
        mock_ocr.scan_image.return_value = {
            "text": "User ID : AHJPR0846B\nPlease confirm your Secure Access Message :\nAMAN ENTERPRISES PRIVATE LIMITED\nPassword :",
            "lines": [
                "User ID : AHJPR0846B",
                "Please confirm your Secure Access Message :",
                "AMAN ENTERPRISES PRIVATE LIMITED",
                "Password :"
            ]
        }

        assembler = VisualSessionAssembler()
        notified = []
        router = VSDCRouter(
            ocr_engine=mock_ocr,
            assembler=assembler,
            on_activity=lambda evt, title, sub: notified.append((evt, title, sub)),
        )

        # Mock foreground window and browser address bar for itr_login_auth
        router.get_foreground_info = MagicMock(return_value=(12345, "e-Filing Login - Google Chrome", "chrome.exe"))
        router.extract_browser_url = MagicMock(return_value="https://eportal.incometax.gov.in/iec/foservices/#/login/password")

        router.evaluate_tick()

        # PAN must be seeded
        self.assertEqual(assembler.client_pan, "AHJPR0846B")
        # Name must NOT be registered (strictly None) to prevent breadcrumb spam
        self.assertIsNone(assembler.client_name)

    def test_itr_login_auth_standard_hash_and_view_filed_returns_journey(self):
        from unittest.mock import MagicMock
        from PIL import Image
        from core.vsdc.vsdc_router import VSDCRouter
        from core.vsdc.vsdc_assembler import VisualSessionAssembler

        mock_ocr = MagicMock()
        mock_ocr.capture_window_image.return_value = Image.new("RGB", (800, 600), color="white")

        assembler = VisualSessionAssembler()
        router = VSDCRouter(
            ocr_engine=mock_ocr,
            assembler=assembler,
            on_activity=lambda *args: None,
        )

        # Step 1: User is on standard live login URL: https://eportal.incometax.gov.in/iec/foservices/#/login
        router.get_foreground_info = MagicMock(return_value=(12345, "e-Filing Login - Google Chrome", "chrome.exe"))
        router.extract_browser_url = MagicMock(return_value="https://eportal.incometax.gov.in/iec/foservices/#/login")
        mock_ocr.scan_image.return_value = {
            "text": "User ID : AHJPR0846B\nPlease confirm your Secure Access Message\nPassword :",
            "lines": ["User ID : AHJPR0846B", "Please confirm your Secure Access Message", "Password :"]
        }

        router.evaluate_tick()

        # PAN must be preserved from login step
        self.assertEqual(assembler.client_pan, "AHJPR0846B")

        # Step 2: User logs in and visits View Filed Returns: https://eportal.incometax.gov.in/iec/foservices/#/dashboard/itrStatus
        router.extract_browser_url = MagicMock(return_value="https://eportal.incometax.gov.in/iec/foservices/#/dashboard/itrStatus")
        mock_ocr.scan_image.side_effect = [
            # center_card scan (View Filed Returns)
            {
                "text": "View Filed Returns A.Y. 2026-27 ITR : ITR-4 Acknowledgement No : 163894330310826 Filed Date : Aug 31, 2026",
                "lines": [
                    "View Filed Returns",
                    "A.Y. 2026-27",
                    "ITR : ITR-4",
                    "Acknowledgement No : 163894330310826",
                    "Filed Date : Aug 31, 2026"
                ]
            },
            # header scan (taxpayer pill)
            {
                "text": "e-Filing Income Tax Department PINKI ROY AIS Help Session Time 15.00",
                "lines": ["e-Filing Income Tax Department", "PINKI ROY", "AIS Help"]
            }
        ]

        payload = router.evaluate_tick()

        # Master payload verification
        self.assertIsNotNone(payload)
        self.assertEqual(payload["pan"], "AHJPR0846B")
        self.assertEqual(payload["client_name"], "PINKI ROY")
        self.assertEqual(payload["arn"], "163894330310826")
        self.assertEqual(payload["filing_type"], "ITR-4")
        self.assertEqual(payload["raw_payload"]["assembler_captures"][0]["dataset_key"], "AHJPR0846B|ITR-4|AY 2026-27")

    def test_gst_welcome_calendar_calibration_with_popup(self):
        """
        Verifies that on services.gst.gov.in/services/auth/fowelcome, even when an Aadhaar/E-KYC
        modal popup dims the screen, the router captures:
        1. Taxpayer Name (ISMAIL BAGANI)
        2. GSTIN (19ADRPB1234F1Z5) and derived PAN (ADRPB1234F)
        3. Return filing preference (Quarterly)
        without interference or degradation of ITR pipelines.
        """
        from unittest.mock import MagicMock
        from PIL import Image
        from core.vsdc.vsdc_router import VSDCRouter
        from core.vsdc.vsdc_assembler import VisualSessionAssembler

        mock_ocr = MagicMock()
        mock_ocr.capture_window_image.return_value = Image.new("RGB", (1200, 800), color="white")

        # OCR scan simulates the exact OCR text extracted from the user's screenshot with modal popup
        mock_ocr.scan_image.return_value = {
            "text": (
                "Goods and Services Tax Help and Taxpayer Facilities\n"
                "Dashboard ISMAIL LAGAN' v\n"
                "Welcome ISMAIL BAGANI to GST Common Portal\n"
                "Would you like to Authenticate Aadhaar or Upload E-KYC Documents for\n"
                "Goods and Services Tax Identification Number (GSTIN) 19ADRPB1234F1Z5?\n"
                "REMIND ME LATER\n"
                "Return filing preference (Jul-Sep 2026) : Quarterly (Change)\n"
            ),
            "lines": [
                "Goods and Services Tax Help and Taxpayer Facilities",
                "Dashboard ISMAIL LAGAN' v",
                "Welcome ISMAIL BAGANI to GST Common Portal",
                "Would you like to Authenticate Aadhaar or Upload E-KYC Documents for",
                "Goods and Services Tax Identification Number (GSTIN) 19ADRPB1234F1Z5?",
                "REMIND ME LATER",
                "Return filing preference (Jul-Sep 2026) : Quarterly (Change)",
            ]
        }

        assembler = VisualSessionAssembler()
        notified = []
        router = VSDCRouter(
            ocr_engine=mock_ocr,
            assembler=assembler,
            on_activity=lambda evt, title, sub: notified.append((evt, title, sub)),
        )

        router.get_foreground_info = MagicMock(return_value=(12345, "GST Common Portal - Google Chrome", "chrome.exe"))
        router.extract_browser_url = MagicMock(return_value="https://services.gst.gov.in/services/auth/fowelcome")

        router.evaluate_tick()

        # Verify the 3 core items
        # 1. Taxpayer Name
        self.assertEqual(assembler.client_name, "ISMAIL BAGANI")
        # 2. GSTIN & derived PAN
        self.assertEqual(assembler.gstin, "19ADRPB1234F1Z5")
        self.assertEqual(assembler.client_pan, "ADRPB1234F")
        # 3. Return filing preference
        self.assertEqual(assembler.filing_preference, "Quarterly")

        # Verify HUD activity notification was emitted
        self.assertTrue(any(evt == "identity" and "ISMAIL BAGANI" in title for evt, title, sub in notified))
        self.assertTrue(any("19ADRPB1234F1Z5" in sub and "Quarterly" in sub for evt, title, sub in notified))

    def test_gst_welcome_calendar_filing_preference_monthly(self):
        """Verifies that Monthly filing preference is correctly captured and normalized."""
        from unittest.mock import MagicMock
        from PIL import Image
        from core.vsdc.vsdc_router import VSDCRouter
        from core.vsdc.vsdc_assembler import VisualSessionAssembler

        mock_ocr = MagicMock()
        mock_ocr.capture_window_image.return_value = Image.new("RGB", (1200, 800), color="white")
        mock_ocr.scan_image.return_value = {
            "text": (
                "Welcome GLOBAL LOGISTICS LLP to GST Common Portal\n"
                "GSTIN: 27AABCU9603R1ZM\n"
                "Return filing preference (Oct-Dec 2026) : Monthly (Change)\n"
            ),
            "lines": [
                "Welcome GLOBAL LOGISTICS LLP to GST Common Portal",
                "GSTIN: 27AABCU9603R1ZM",
                "Return filing preference (Oct-Dec 2026) : Monthly (Change)",
            ]
        }

        assembler = VisualSessionAssembler()
        router = VSDCRouter(
            ocr_engine=mock_ocr,
            assembler=assembler,
            on_activity=lambda *args: None,
        )

        router.get_foreground_info = MagicMock(return_value=(12345, "GST Common Portal - Google Chrome", "chrome.exe"))
        router.extract_browser_url = MagicMock(return_value="https://services.gst.gov.in/services/auth/fowelcome")

        router.evaluate_tick()

        self.assertEqual(assembler.client_name, "GLOBAL LOGISTICS LLP")
        self.assertEqual(assembler.gstin, "27AABCU9603R1ZM")
        self.assertEqual(assembler.client_pan, "AABCU9603R")
        self.assertEqual(assembler.filing_preference, "Monthly")

    def test_gst_form_details_calibration(self):
        """
        Verifies that on return.gst.gov.in/returns/auth/gstr1 (or gstr3b, cmp08, iff),
        the router captures the complete 4-column metadata table:
        1. GSTIN (19AAAAA0000A1Z5) and derived PAN (AAAAA0000A)
        2. Legal Name (FATIMA BIBI)
        3. Trade Name (SPY JUNIOR)
        4. Form Type (GSTR-1/IFF)
        5. FY (2026-27)
        6. Tax Period (June(Q))
        7. Status (Filed)
        8. Due Date (13/07/2026)
        and shoots the complete dataset payload to the app with live HUD toast.
        """
        from unittest.mock import MagicMock
        from PIL import Image
        from core.vsdc.vsdc_router import VSDCRouter
        from core.vsdc.vsdc_assembler import VisualSessionAssembler

        mock_ocr = MagicMock()
        mock_ocr.capture_window_image.return_value = Image.new("RGB", (1366, 768), color="white")

        # Simulated OCR text matching the user's screenshot (media_1789328507578.png)
        mock_ocr.scan_image.return_value = {
            "text": (
                "Goods and Services Tax\n"
                "FATIMA BIBI v\n"
                "Dashboard > Returns > GSTR-1/IFF\n"
                "GSTR-1 - Details of outward supplies of goods or services\n"
                "GSTIN - 19AAAAA0000A1Z5\n"
                "Legal Name - FATIMA BIBI\n"
                "Trade Name - SPY JUNIOR\n"
                "* Indicates Mandatory Fields\n"
                "FY - 2026-27\n"
                "Tax Period - June(Q)\n"
                "Status - Filed\n"
                "Due Date - 13/07/2026\n"
                "File Nil GSTR-1\n"
                "ADD RECORD DETAILS\n"
            ),
            "lines": [
                "Goods and Services Tax",
                "FATIMA BIBI v",
                "Dashboard > Returns > GSTR-1/IFF",
                "GSTR-1 - Details of outward supplies of goods or services",
                "GSTIN - 19AAAAA0000A1Z5",
                "Legal Name - FATIMA BIBI",
                "Trade Name - SPY JUNIOR",
                "* Indicates Mandatory Fields",
                "FY - 2026-27",
                "Tax Period - June(Q)",
                "Status - Filed",
                "Due Date - 13/07/2026",
                "File Nil GSTR-1",
                "ADD RECORD DETAILS",
            ]
        }

        assembler = VisualSessionAssembler()
        notified = []
        router = VSDCRouter(
            ocr_engine=mock_ocr,
            assembler=assembler,
            on_activity=lambda evt, title, sub: notified.append((evt, title, sub)),
        )

        router.get_foreground_info = MagicMock(return_value=(12345, "Goods & Services Tax (GST) | User - Google Chrome", "chrome.exe"))
        router.extract_browser_url = MagicMock(return_value="https://return.gst.gov.in/returns/auth/gstr1")

        payload = router.evaluate_tick()

        # 1. Verify Assembler state
        self.assertEqual(assembler.client_name, "FATIMA BIBI")
        self.assertEqual(assembler.trade_name, "SPY JUNIOR")
        self.assertEqual(assembler.gstin, "19AAAAA0000A1Z5")
        self.assertEqual(assembler.client_pan, "AAAAA0000A")
        self.assertEqual(assembler.fy, "2026-27")
        self.assertEqual(assembler.due_date, "13/07/2026")
        self.assertEqual(assembler.current_period_label, "June(Q)")
        self.assertIn("GSTR-1", assembler.current_filing_type)

        # 2. Verify shot dataset payload to app
        self.assertIsNotNone(payload)
        self.assertEqual(payload["portal"], "GST Portal")
        self.assertEqual(payload["client_name"], "FATIMA BIBI")
        self.assertEqual(payload["trade_name"], "SPY JUNIOR")
        self.assertEqual(payload["gstin"], "19AAAAA0000A1Z5")
        self.assertEqual(payload["pan"], "AAAAA0000A")
        self.assertEqual(payload["period_label"], "June(Q)")
        self.assertEqual(payload["status"], "Filed")
        self.assertEqual(payload["due_date"], "13/07/2026")
        self.assertEqual(payload["fy"], "2026-27")
        self.assertIn("GSTR-1", payload["filing_type"])
        self.assertEqual(payload["raw_payload"]["trade_name"], "SPY JUNIOR")

        # 3. Verify Live HUD Toast
        self.assertTrue(any(evt == "capture" and "GSTR-1" in title and "June(Q)" in title for evt, title, sub in notified))
        self.assertTrue(any("FATIMA BIBI" in sub and "SPY JUNIOR" in sub and "Filed" in sub for evt, title, sub in notified))


if __name__ == "__main__":
    unittest.main()


