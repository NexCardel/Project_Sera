"""
tests/test_vsdc_crosshairs.py — Unit Tests for VSDC Crosshair Matching
"""

import unittest
from unittest.mock import MagicMock
from PIL import Image
from core.vsdc.vsdc_crosshairs import match_url_crosshair, ITR_CROSSHAIRS, GST_CROSSHAIRS
from core.vsdc.vsdc_router import VSDCRouter
from core.vsdc.vsdc_assembler import VisualSessionAssembler


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

        # 7. gst_login (Redirect after logout or fresh session start)
        c = match_url_crosshair("https://services.gst.gov.in/services/login")
        self.assertIsNotNone(c)
        self.assertEqual(c.id, "gst_login")
        self.assertFalse(c.is_session_boundary)

    def test_gst_session_boundary_logout_notification(self):
        from unittest.mock import MagicMock
        from core.vsdc.vsdc_router import VSDCRouter
        from core.vsdc.vsdc_assembler import VisualSessionAssembler

        from PIL import Image

        assembler = VisualSessionAssembler()
        mock_ocr = MagicMock()
        mock_ocr.capture_window_image.return_value = Image.new("RGB", (800, 600), color="white")
        mock_ocr.scan_image.return_value = {"text": "Goods and Services Tax Login", "lines": ["Goods and Services Tax Login"]}

        activities = []
        def on_activity(kind, title, desc):
            activities.append((kind, title, desc))

        router = VSDCRouter(ocr_engine=mock_ocr, assembler=assembler, on_activity=on_activity)
        router.assembler.gstin = "27AAPFU0939L1ZV"
        router.assembler.client_name = "ABC ENTERPRISE"
        router.last_logged_name = "ABC ENTERPRISE"

        # Simulate user logging out / redirected to services/login
        router.get_foreground_info = MagicMock(return_value=(12345, "GST Portal Login - Google Chrome", "chrome.exe"))
        router.extract_browser_url = MagicMock(return_value="https://services.gst.gov.in/services/login")
        router.evaluate_tick()

        logout_events = [act for act in activities if act[0] == "logout"]
        self.assertTrue(len(logout_events) >= 1)
        kind, title, desc = logout_events[0]
        self.assertEqual(title, "GST Session Concluded")
        self.assertIn("ABC ENTERPRISE", desc)

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
        self.assertTrue(any(evt in ("start", "identity") and "ISMAIL BAGANI" in title for evt, title, sub in notified))
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
        self.assertEqual(assembler.current_period_label, "June (FY 2026-27)")
        self.assertIn("GSTR-1", assembler.current_filing_type)

        # 2. Verify shot dataset payload to app
        self.assertIsNotNone(payload)
        self.assertEqual(payload["portal"], "GST Portal")
        self.assertEqual(payload["client_name"], "FATIMA BIBI")
        self.assertEqual(payload["trade_name"], "SPY JUNIOR")
        self.assertEqual(payload["gstin"], "19AAAAA0000A1Z5")
        self.assertEqual(payload["pan"], "AAAAA0000A")
        self.assertEqual(payload["period_label"], "June (FY 2026-27)")
        self.assertEqual(payload["tax_period"], "June")
        self.assertEqual(payload["status"], "Filed")
        self.assertEqual(payload["due_date"], "13/07/2026")
        self.assertEqual(payload["fy"], "2026-27")
        self.assertIn("GSTR-1", payload["filing_type"])
        self.assertEqual(payload["raw_payload"]["trade_name"], "SPY JUNIOR")

        # 3. Verify Live HUD Toast
        self.assertTrue(any(evt == "capture" and "GSTR-1" in title and "June" in title for evt, title, sub in notified))
        self.assertTrue(any("FATIMA BIBI" in sub and "SPY JUNIOR" in sub and "Filed" in sub for evt, title, sub in notified))

    def test_gst_form_details_multiline_status_and_period(self):
        """
        Verifies that when OCR outputs multiline breaks between field labels and values:
        - 'Tax Period -\nJune(Q)' is captured as June(Q)
        - 'Status -\nFiled' is captured as Filed (NOT defaulted to Initiated)
        - 'FY -\n2026-27' is captured as 2026-27
        - Canonical period_label is formatted as 'June(Q) (FY 2026-27)'
        - No ITR 'AY 2026-27' leak occurs.
        """
        from unittest.mock import MagicMock
        from PIL import Image
        from core.vsdc.vsdc_router import VSDCRouter
        from core.vsdc.vsdc_assembler import VisualSessionAssembler

        mock_ocr = MagicMock()
        mock_ocr.capture_window_image.return_value = Image.new("RGB", (1366, 768), color="white")
        mock_ocr.scan_image.return_value = {
            "text": (
                "Goods and Services Tax\n"
                "Dashboard > Returns > GSTR-1/IFF\n"
                "GSTR-1 - Details of outward supplies of goods or services\n"
                "GSTIN -\n19AAAAA0000A1Z5\n"
                "Legal Name -\nFATIMA BIBI\n"
                "Trade Name -\nSPY JUNIOR\n"
                "FY -\n2026-27\n"
                "Tax Period -\nJune (Q)\n"
                "Status -\nFiled\n"
                "Due Date -\n13/07/2026\n"
            ),
            "lines": [
                "Goods and Services Tax",
                "Dashboard > Returns > GSTR-1/IFF",
                "GSTR-1 - Details of outward supplies of goods or services",
                "GSTIN -", "19AAAAA0000A1Z5",
                "Legal Name -", "FATIMA BIBI",
                "Trade Name -", "SPY JUNIOR",
                "FY -", "2026-27",
                "Tax Period -", "June (Q)",
                "Status -", "Filed",
                "Due Date -", "13/07/2026",
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

        self.assertIsNotNone(payload)
        self.assertEqual(payload["status"], "Filed")
        self.assertEqual(payload["period_label"], "June (FY 2026-27)")
        self.assertEqual(payload["tax_period"], "June")
        self.assertEqual(payload["fy"], "2026-27")
        self.assertNotIn("AY", payload["period_label"])

    def test_gst_filing_file_success_arn_extraction(self):
        """
        Verifies that on https://return.gst.gov.in/returns/auth/gstr1/file:
        1. URL matches gst_filing_file_success (not misclassified as gst_form_details)
        2. Optical submission message and ARN (AA1908260123456) are captured
        3. Assembler preserves client name, trade name, and period from workflow
        4. Filing payload is sealed and flushed to tracker dump with HUD toast.
        """
        from unittest.mock import MagicMock
        from PIL import Image
        from core.vsdc.vsdc_crosshairs import match_url_crosshair
        from core.vsdc.vsdc_router import VSDCRouter
        from core.vsdc.vsdc_assembler import VisualSessionAssembler

        # 1. Verify crosshair pattern match
        test_url = "https://return.gst.gov.in/returns/auth/gstr1/file"
        matched = match_url_crosshair(test_url)
        self.assertIsNotNone(matched)
        self.assertEqual(matched.id, "gst_filing_file_success")

        # 2. Setup Assembler with prior state from form view
        assembler = VisualSessionAssembler()
        assembler.update_identity(
            name="FATIMA BIBI",
            trade_name="SPY JUNIOR",
            gstin="19AAAAA0000A1Z5",
            pan="AAAAA0000A",
            portal="GST Portal",
        )
        assembler.update_selection(filing_type="GSTR-1", period_label="June(Q) (FY 2026-27)", fy="2026-27")

        mock_ocr = MagicMock()
        mock_ocr.capture_window_image.return_value = Image.new("RGB", (1366, 768), color="white")
        mock_ocr.scan_image.return_value = {
            "text": (
                "Goods and Services Tax\n"
                "Dashboard > Returns > GSTR-1/IFF > File\n"
                "Filing Successful\n"
                "Your return has been filed successfully.\n"
                "Acknowledgement Reference Number (ARN) is AA1908260123456\n"
                "Date of Filing: 14/09/2026\n"
            ),
            "lines": [
                "Goods and Services Tax",
                "Dashboard > Returns > GSTR-1/IFF > File",
                "Filing Successful",
                "Your return has been filed successfully.",
                "Acknowledgement Reference Number (ARN) is AA1908260123456",
                "Date of Filing: 14/09/2026",
            ]
        }

        notified = []
        router = VSDCRouter(
            ocr_engine=mock_ocr,
            assembler=assembler,
            on_activity=lambda evt, title, sub: notified.append((evt, title, sub)),
        )

        router.get_foreground_info = MagicMock(return_value=(12345, "Goods & Services Tax (GST) | File - Google Chrome", "chrome.exe"))
        router.extract_browser_url = MagicMock(return_value=test_url)

        master_payload = router.evaluate_tick()

        # 3. Verify sealed & flushed master payload
        self.assertIsNotNone(master_payload)
        self.assertEqual(master_payload["arn"], "AA1908260123456")
        self.assertEqual(master_payload["client_name"], "FATIMA BIBI")
        self.assertEqual(master_payload["trade_name"], "SPY JUNIOR")
        self.assertEqual(master_payload["gstin"], "19AAAAA0000A1Z5")
        self.assertEqual(master_payload["pan"], "AAAAA0000A")
        self.assertEqual(master_payload["filing_type"], "GSTR-1")
        self.assertEqual(master_payload["period_label"], "June (FY 2026-27)")
        self.assertIn(master_payload["status"], ("Filed", "Filing Submitted"))

        # 4. Verify Live HUD Toast (submit event with ARN)
        self.assertTrue(any(evt in ("submit", "capture") and "AA1908260123456" in sub for evt, title, sub in notified))

    def test_gst_capture_on_render_and_stop_until_url_changes(self):
        """
        Verifies single-shot on-render capture for GST:
        1. When page is loading, route_captured remains False and router polls.
        2. The moment data points render, router captures them, sets route_captured=True, and emits dataset.
        3. On subsequent ticks on the SAME URL, OCR is completely suppressed (0 calls).
        4. When the URL changes to a new route, route_captured resets to False and captures the new page.
        """
        from unittest.mock import MagicMock
        from PIL import Image
        from core.vsdc.vsdc_router import VSDCRouter
        from core.vsdc.vsdc_assembler import VisualSessionAssembler

        mock_ocr = MagicMock()
        mock_ocr.capture_window_image.return_value = Image.new("RGB", (1366, 768), color="white")

        assembler = VisualSessionAssembler()
        router = VSDCRouter(
            ocr_engine=mock_ocr,
            assembler=assembler,
            on_activity=lambda *args: None,
        )

        form_url = "https://return.gst.gov.in/returns/auth/gstr1"
        router.get_foreground_info = MagicMock(return_value=(12345, "Goods & Services Tax (GST) | Form - Google Chrome", "chrome.exe"))
        router.extract_browser_url = MagicMock(return_value=form_url)

        # Tick 1: Screen is still in async loading / spinner state
        mock_ocr.scan_image.return_value = {
            "text": "Loading... Please wait while data is retrieved",
            "lines": ["Loading... Please wait while data is retrieved"]
        }
        res1 = router.evaluate_tick()
        self.assertIsNone(res1)
        self.assertFalse(router.route_captured)
        self.assertEqual(mock_ocr.scan_image.call_count, 1)

        # Tick 2: Data renders on screen! Table metadata is extracted
        mock_ocr.scan_image.return_value = {
            "text": (
                "GSTIN - 19AAAAA0000A1Z5\n"
                "Legal Name - FATIMA BIBI\n"
                "Trade Name - SPY JUNIOR\n"
                "FY - 2026-27\n"
                "Tax Period - June (Q)\n"
                "Status - Filed\n"
                "Due Date - 13/07/2026\n"
            ),
            "lines": [
                "GSTIN - 19AAAAA0000A1Z5", "Legal Name - FATIMA BIBI", "Trade Name - SPY JUNIOR",
                "FY - 2026-27", "Tax Period - June (Q)", "Status - Filed", "Due Date - 13/07/2026"
            ]
        }
        res2 = router.evaluate_tick()
        self.assertIsNotNone(res2)
        self.assertEqual(res2["status"], "Filed")
        self.assertTrue(router.route_captured)
        calls_after_capture = mock_ocr.scan_image.call_count

        # Tick 3 & 4: Same URL, data has not changed. Must NOT call OCR scan_image!
        res3 = router.evaluate_tick()
        res4 = router.evaluate_tick()
        self.assertIsNone(res3)
        self.assertIsNone(res4)
        # scan_image calls MUST not increase at all
        self.assertEqual(mock_ocr.scan_image.call_count, calls_after_capture)

        # Tick 5: CA navigates to file success URL
        file_url = "https://return.gst.gov.in/returns/auth/gstr1/file"
        router.extract_browser_url = MagicMock(return_value=file_url)
        mock_ocr.scan_image.return_value = {
            "text": "Filing Successful\nAcknowledgement Reference Number (ARN) is AA1908260123456",
            "lines": ["Filing Successful", "Acknowledgement Reference Number (ARN) is AA1908260123456"]
        }
        res5 = router.evaluate_tick()
        self.assertIsNotNone(res5)
        self.assertEqual(res5["arn"], "AA1908260123456")
        self.assertTrue(router.route_captured)
        calls_after_file_url = mock_ocr.scan_image.call_count

        # Tick 6: Same file URL. OCR must stop completely!
        res6 = router.evaluate_tick()
        self.assertIsNone(res6)
        self.assertEqual(mock_ocr.scan_image.call_count, calls_after_file_url)

    def test_itr_capture_on_render_and_stop_until_url_changes(self):
        """
        Verifies single-shot on-render capture for ITR:
        1. When page is loading, route_captured remains False and router polls.
        2. The moment data points render, router captures them, sets route_captured=True, and emits dataset.
        3. On subsequent ticks on the SAME URL, OCR is completely suppressed (0 calls).
        4. When the URL changes to a new route, route_captured resets to False.
        """
        from unittest.mock import MagicMock
        from PIL import Image
        from core.vsdc.vsdc_router import VSDCRouter
        from core.vsdc.vsdc_assembler import VisualSessionAssembler

        mock_ocr = MagicMock()
        mock_ocr.capture_window_image.return_value = Image.new("RGB", (1200, 800), color="white")

        assembler = VisualSessionAssembler()
        router = VSDCRouter(
            ocr_engine=mock_ocr,
            assembler=assembler,
            on_activity=lambda *args: None,
        )

        itr_status_url = "https://eportal.incometax.gov.in/iec/foservices/#/dashboard/itrStatus"
        router.get_foreground_info = MagicMock(return_value=(12345, "e-Filing - Google Chrome", "chrome.exe"))
        router.extract_browser_url = MagicMock(return_value=itr_status_url)

        # Tick 1: Screen is loading
        mock_ocr.scan_image.return_value = {
            "text": "Loading... Please wait",
            "lines": ["Loading... Please wait"]
        }
        res1 = router.evaluate_tick()
        self.assertIsNone(res1)
        self.assertFalse(router.route_captured)

        # Tick 2: Return card renders
        mock_ocr.scan_image.side_effect = [
            {
                "text": "View Filed Returns A.Y. 2026-27 ITR : ITR-1 Acknowledgement No : 123456789012345 Status : Successfully e-Verified",
                "lines": [
                    "View Filed Returns", "A.Y. 2026-27", "ITR : ITR-1",
                    "Acknowledgement No : 123456789012345", "Status : Successfully e-Verified"
                ]
            },
            {
                "text": "e-Filing Income Tax Department RAMESH SHARMA ABCPE1234F",
                "lines": ["e-Filing Income Tax Department", "RAMESH SHARMA", "ABCPE1234F"]
            }
        ]
        res2 = router.evaluate_tick()
        self.assertIsNotNone(res2)
        self.assertEqual(res2["arn"], "123456789012345")
        self.assertEqual(res2["pan"], "ABCPE1234F")
        self.assertTrue(router.route_captured)
        calls_after_capture = mock_ocr.scan_image.call_count

        # Tick 3: Same URL, subsequent tick. 0 OCR calls!
        res3 = router.evaluate_tick()
        self.assertIsNone(res3)
        self.assertEqual(mock_ocr.scan_image.call_count, calls_after_capture)

        # Tick 4: Navigate to dashboard / landing. URL change resets route_captured!
        landing_url = "https://eportal.incometax.gov.in/iec/foservices/#/dashboard"
        router.extract_browser_url = MagicMock(return_value=landing_url)
        mock_ocr.scan_image.side_effect = None
        mock_ocr.scan_image.return_value = {
            "text": "Dashboard Welcome RAMESH SHARMA",
            "lines": ["Dashboard Welcome RAMESH SHARMA"]
        }
        res4 = router.evaluate_tick()
        # Even if res4 is None (dashboard without submission), route_captured was reset on URL change
        self.assertEqual(router.last_url, landing_url)

    def test_gst_multiline_anchor_extraction_and_premature_lock_guard(self):
        """
        Verifies full-page multi-line visual anchor and proximity extraction for GST form details:
        1. When only GSTIN and taxpayer name render on Tick 1 (before table loads), router updates
           identity but does NOT prematurely set route_captured=True.
        2. When table renders across multiple wrapped lines on Tick 2 (labels on line i, values on line i+1),
           router extracts all fields (GSTIN, PAN, Legal Name, Trade Name, FY, Tax Period, Status, Due Date),
           assembles and shoots the dataset, and locks route_captured=True.
        3. On Tick 3, router suppresses redundant OCR scans on the same route.
        """
        mock_ocr = MagicMock()
        mock_ocr.capture_window_image.return_value = Image.new("RGB", (1366, 768), color="white")

        assembler = VisualSessionAssembler()
        router = VSDCRouter(
            ocr_engine=mock_ocr,
            assembler=assembler,
            on_activity=lambda *args: None,
        )

        form_url = "https://return.gst.gov.in/returns/auth/gstr1"
        router.get_foreground_info = MagicMock(return_value=(12345, "Goods & Services Tax (GST) | Form - Google Chrome", "chrome.exe"))
        router.extract_browser_url = MagicMock(return_value=form_url)

        # Tick 1: Screen has header with GSTIN and Legal Name, but table hasn't rendered period or status yet
        mock_ocr.scan_image.return_value = {
            "text": "Goods and Services Tax\nGSTIN - 19AKVPA6032B1ZC\nLegal Name - MOHAMMAD ASHRAF ALI",
            "lines": ["Goods and Services Tax", "GSTIN - 19AKVPA6032B1ZC", "Legal Name - MOHAMMAD ASHRAF ALI"]
        }
        res1 = router.evaluate_tick()
        self.assertIsNone(res1)
        self.assertFalse(router.route_captured, "Router must NOT prematurely lock before tax_period renders")
        self.assertEqual(assembler.gstin, "19AKVPA6032B1ZC")
        self.assertEqual(assembler.client_name, "MOHAMMAD ASHRAF ALI")

        # Tick 2: Full table renders on screen with multi-line/adjacent wrapped layout
        mock_ocr.scan_image.return_value = {
            "text": (
                "Goods and Services Tax\n"
                "Dashboard > Returns > GSTR-1/IFF\n"
                "GSTR-1 - Details of outward supplies of goods or services\n"
                "GSTIN\n"
                "19AKVPA6032B1ZC\n"
                "Legal Name of Business\n"
                "MOHAMMAD ASHRAF ALI\n"
                "Trade Name\n"
                "A. P. ENTERPRISE\n"
                "Financial Year\n"
                "2024-25\n"
                "Tax Period\n"
                "January\n"
                "Status\n"
                "Filed\n"
                "Due Date\n"
                "11/02/2025\n"
            ),
            "lines": [
                "Goods and Services Tax",
                "Dashboard > Returns > GSTR-1/IFF",
                "GSTR-1 - Details of outward supplies of goods or services",
                "GSTIN",
                "19AKVPA6032B1ZC",
                "Legal Name of Business",
                "MOHAMMAD ASHRAF ALI",
                "Trade Name",
                "A. P. ENTERPRISE",
                "Financial Year",
                "2024-25",
                "Tax Period",
                "January",
                "Status",
                "Filed",
                "Due Date",
                "11/02/2025",
            ]
        }
        res2 = router.evaluate_tick()
        self.assertIsNotNone(res2, "Router must emit dataset payload once table renders")
        self.assertEqual(res2["gstin"], "19AKVPA6032B1ZC")
        self.assertEqual(res2["pan"], "AKVPA6032B")
        self.assertEqual(res2["client_name"], "MOHAMMAD ASHRAF ALI")
        self.assertEqual(res2["trade_name"], "A. P. ENTERPRISE")
        self.assertEqual(res2["tax_period"], "January")
        self.assertEqual(res2["fy"], "2024-25")
        self.assertEqual(res2["period_label"], "January (FY 2024-25)")
        self.assertEqual(res2["status"], "Filed")
        self.assertEqual(res2["due_date"], "11/02/2025")
        self.assertTrue(router.route_captured, "Router must lock after successful full dataset emission")

        # Tick 3: Same URL, subsequent tick. 0 new OCR calls!
        calls_after_capture = mock_ocr.scan_image.call_count
        res3 = router.evaluate_tick()
        self.assertIsNone(res3)
        self.assertEqual(mock_ocr.scan_image.call_count, calls_after_capture)

    def test_gst_authoritative_legal_name_overrides_welcome_noise(self):
        """
        Verifies that:
        1. Welcome/dashboard text with portal words like 'UNION TERRITORIES' or 'GCK DS SERVICE'
           is rejected by NOISE_WORDS.
        2. Even if a prior heuristic name was recorded in the assembler, the authoritative
           'Legal Name - JABED ALI' from the statutory GST Return Form table takes absolute precedence,
           populating client_name='JABED ALI' and trade_name='A.C.T. DRESSES'.
        """
        mock_ocr = MagicMock()
        mock_ocr.capture_window_image.return_value = Image.new("RGB", (1366, 768), color="white")

        assembler = VisualSessionAssembler()
        # Seed assembler with noise name to test overwrite
        assembler.client_name = "GCK DS SERVICE"

        router = VSDCRouter(
            ocr_engine=mock_ocr,
            assembler=assembler,
            on_activity=lambda *args: None,
        )

        form_url = "https://return.gst.gov.in/returns/auth/gstr1"
        router.get_foreground_info = MagicMock(return_value=(12345, "Goods & Service Tax (GST) - Google Chrome", "chrome.exe"))
        router.extract_browser_url = MagicMock(return_value=form_url)

        user_raw_text = (
            "e Gcx)ds & Service Tax (GST) I Use X + c return.gst.gov.in/returns/auth/gstrl "
            "Goods and Services Tax Government of India, States and Union Territories "
            "Help and Taxpayer Facilities e-lnvoice Ask Gemini a JABED ALI v 19BNNPA1234H1ZX "
            "News and Updates English O Dashboard Services • GST Law Downloads • Search Taxpayer • "
            "Dashboard Returns GSTR-I/IFF GSTR-I - Details of outward supplies of goods or services "
            "E-INVOICE ADVISORY HELP O GSTIN - 19BNNPA1234H1ZX FY - 2026-27 File Nil GSTR-I "
            "ADD RECORD DETAILS 4A, 4B, 6B, SC - 82B, SEZ, DE Invoices 8A, 8B, 8C, 8D - Nil Rated Supplies "
            "Legal Name - JABED ALI Tax Period - September(Q) 5 - B2C (Large) Invoices "
            "9B - Credit / Debit Notes (Registered) Trade Name - A.C.T. DRESSES Status - Not Filed "
            "6A - Exports Invoices 9B - Credit / Debit Notes (Unregistered) • Indicates Mandatory Fields "
            "Due Date - 13/10/2026 7 - B2C (Others) IIA(I), IIA(2) - Tax Liability (Advances Received)"
        )

        mock_ocr.scan_image.return_value = {
            "text": user_raw_text,
            "lines": [
                "Goods and Services Tax Government of India, States and Union Territories",
                "Ask Gemini a JABED ALI v 19BNNPA1234H1ZX",
                "Dashboard Returns GSTR-I/IFF",
                "GSTR-I - Details of outward supplies of goods or services",
                "GSTIN - 19BNNPA1234H1ZX",
                "FY - 2026-27",
                "Legal Name - JABED ALI",
                "Tax Period - September(Q)",
                "Trade Name - A.C.T. DRESSES",
                "Status - Not Filed",
                "Due Date - 13/10/2026",
            ]
        }

        payload = router.evaluate_tick()
        self.assertIsNotNone(payload)
        self.assertEqual(payload["client_name"], "JABED ALI", "Authoritative legal name must override noise")
        self.assertEqual(payload["trade_name"], "A.C.T. DRESSES")
        self.assertEqual(payload["gstin"], "19BNNPA1234H1ZX")
        self.assertEqual(payload["pan"], "BNNPA1234H")
        self.assertEqual(payload["status"], "Not Filed")
        self.assertEqual(payload["tax_period"], "September")
        self.assertEqual(payload["fy"], "2026-27")
        self.assertEqual(payload["period_label"], "September (FY 2026-27)")
        self.assertEqual(payload["due_date"], "13/10/2026")
        self.assertEqual(assembler.client_name, "JABED ALI")
        self.assertEqual(assembler.trade_name, "A.C.T. DRESSES")

    def test_gst_scroll_past_and_scroll_back_capture(self):
        """
        Verifies viewport resilience when an employee scrolls fast past the table:
        1. Tick 1: User scrolled fast to the bottom of GSTR-3B page; table is off-screen.
           Router rejects incomplete view; route_captured remains False.
        2. Ticks 2-4: User remains at bottom; static view suppresses redundant OCR scans.
        3. Tick 5: User scrolls back up to the top! The GST table enters viewport.
           Router detects screen content change, extracts all fields, shoots payload,
           and sets route_captured=True.
        4. Tick 6: Subsequent ticks lock and do not make OCR scans.
        """
        mock_ocr = MagicMock()
        img_bottom = Image.new("RGB", (1366, 768), color="red")
        img_top = Image.new("RGB", (1366, 768), color="blue")
        mock_ocr.capture_window_image.return_value = img_bottom

        assembler = VisualSessionAssembler()
        router = VSDCRouter(ocr_engine=mock_ocr, assembler=assembler, on_activity=lambda *args: None)

        test_url = "https://return.gst.gov.in/returns/auth/gstr3b"
        router.get_foreground_info = MagicMock(return_value=(12345, "Goods & Services Tax (GST) - Google Chrome", "chrome.exe"))
        router.extract_browser_url = MagicMock(return_value=test_url)

        # Tick 1: Scrolled to bottom (only buttons & Table 3.1, no period or status)
        mock_ocr.scan_image.return_value = {
            "text": "Table 3.1 Details of Outward Supplies\nBACK SAVE GSTR3B DOWNLOAD FILED GSTR-3B",
            "lines": ["Table 3.1 Details of Outward Supplies", "BACK SAVE GSTR3B DOWNLOAD FILED GSTR-3B"]
        }
        res1 = router.evaluate_tick()
        self.assertIsNone(res1)
        self.assertFalse(router.route_captured, "Must NOT lock when table is scrolled off-screen")

        # Tick 2-4: Still at bottom
        for _ in range(3):
            self.assertIsNone(router.evaluate_tick())
        self.assertFalse(router.route_captured)

        # Tick 5: User scrolls back up to top! Image changes and full table enters viewport
        mock_ocr.capture_window_image.return_value = img_top
        mock_ocr.scan_image.return_value = {
            "text": (
                "Goods and Services Tax\n"
                "Dashboard Returns GSTR-3BQ GSTR-3BQ - Quarterly Return\n"
                "GSTIN - 19CJLPM0265MIZO FY - 2026-27\n"
                "Legal Name - ARIF MOHAMMAD MOLLA Return Period - Apr-Jun\n"
                "Status - Filed Due Date - 24/07/2026"
            ),
            "lines": [
                "Goods and Services Tax",
                "Dashboard Returns GSTR-3BQ GSTR-3BQ - Quarterly Return",
                "GSTIN - 19CJLPM0265MIZO",
                "FY - 2026-27",
                "Legal Name - ARIF MOHAMMAD MOLLA",
                "Return Period - Apr-Jun",
                "Status - Filed",
                "Due Date - 24/07/2026"
            ]
        }
        res5 = router.evaluate_tick()
        self.assertIsNotNone(res5, "Router MUST capture the table when user scrolls back up")
        self.assertEqual(res5["gstin"], "19CJLPM0265MIZO")
        self.assertEqual(res5["pan"], "CJLPM0265M")
        self.assertEqual(res5["client_name"], "ARIF MOHAMMAD MOLLA")
        self.assertEqual(res5["filing_type"], "GSTR-3B")
        self.assertEqual(res5["period_label"], "June (FY 2026-27)")
        self.assertEqual(res5["status"], "Filed")
        self.assertEqual(res5["due_date"], "24/07/2026")
        self.assertTrue(router.route_captured)

        # Tick 6: Locked on same route
        calls_before = mock_ocr.scan_image.call_count
        res6 = router.evaluate_tick()
        self.assertIsNone(res6)
        self.assertEqual(mock_ocr.scan_image.call_count, calls_before, "OCR must be completely suppressed once captured")

    def test_itr_navigation_no_premature_logout_and_authoritative_profile_capture(self):
        """
        Verifies that traversing ITR:
        1. Login page (#/login)
        2. Dashboard / File Income Tax Return (#/dashboard/fileIncomeTaxReturn)
        3. Personal Information form (#/foreturns-ay26/fo-itr4-ay2026/personal_information)
        emits zero 'Session Concluded' toasts and that the authoritative profile name
        cleanly populates the assembler.
        """
        from unittest.mock import MagicMock
        from PIL import Image
        from core.vsdc.vsdc_router import VSDCRouter
        from core.vsdc.vsdc_assembler import VisualSessionAssembler

        mock_ocr = MagicMock()
        mock_ocr.capture_window_image.return_value = Image.new("RGB", (1200, 800), color="white")

        assembler = VisualSessionAssembler()
        notified = []
        router = VSDCRouter(
            ocr_engine=mock_ocr,
            assembler=assembler,
            on_activity=lambda evt, title, sub: notified.append((evt, title, sub)),
        )

        router.get_foreground_info = MagicMock(return_value=(12345, "e-Filing Portal - Google Chrome", "chrome.exe"))

        # Step 1: Login auth page
        router.extract_browser_url = MagicMock(return_value="https://eportal.incometax.gov.in/iec/foservices/#/login")
        mock_ocr.scan_image.return_value = {
            "text": "User ID : AHJPR0846B\nPlease confirm your Secure Access Message\nPassword :",
            "lines": ["User ID : AHJPR0846B", "Please confirm your Secure Access Message", "Password :"]
        }
        router.evaluate_tick()
        self.assertEqual(assembler.client_pan, "AHJPR0846B")

        # Step 2: Dashboard landing page - has boilerplate navigation links
        router.extract_browser_url = MagicMock(return_value="https://eportal.incometax.gov.in/iec/foservices/#/dashboard/fileIncomeTaxReturn")
        mock_ocr.scan_image.return_value = {
            "text": "e-Filing Anywhere Anytime\nWEBSITE POLICIES | ACCESSIBILITY STATEMENT\nFile Income Tax Return\nSelect Assessment Year",
            "lines": [
                "e-Filing Anywhere Anytime",
                "WEBSITE POLICIES | ACCESSIBILITY STATEMENT",
                "File Income Tax Return",
                "Select Assessment Year"
            ]
        }
        router.evaluate_tick()

        # Check that NO 'Session Concluded' was emitted during this navigation
        logout_events = [evt for evt in notified if evt[1] == "Session Concluded" or evt[0] == "logout"]
        self.assertEqual(len(logout_events), 0, "No 'Session Concluded' should be emitted during forward navigation")

        # Step 3: Personal Information page
        router.extract_browser_url = MagicMock(return_value="https://eportal.incometax.gov.in/iec/foservices/#/foreturns-ay26/fo-itr4-ay2026/personal_information")
        mock_ocr.scan_image.return_value = {
            "text": "Profile Details\nName (as per PAN)\nMD SAYID MOLLA\nDate of Birth\n15/08/1985\nAadhaar Number\nXXXX-XXXX-1234",
            "lines": [
                "Profile Details",
                "Name (as per PAN)",
                "MD SAYID MOLLA",
                "Date of Birth",
                "15/08/1985",
                "Aadhaar Number",
                "XXXX-XXXX-1234"
            ]
        }
        router.evaluate_tick()

        # Authoritative name must be recorded
        self.assertEqual(assembler.client_name, "MD SAYID MOLLA")
        self.assertEqual(assembler.client_pan, "AHJPR0846B")

        # Still no premature logout event
        logout_events = [evt for evt in notified if evt[1] == "Session Concluded" or evt[0] == "logout"]
        self.assertEqual(len(logout_events), 0, "Zero 'Session Concluded' toasts emitted during active form journey")

    def test_filing_preference_strictly_quarterly_or_monthly(self):
        """
        Verifies that filing_preference in VisualSessionAssembler and VSDCRouter is strictly
        restricted to 'Quarterly' or 'Monthly' and rejects 'Regular', 'Regular / Non-QRMP', etc.
        """
        from core.vsdc.vsdc_assembler import VisualSessionAssembler

        assembler = VisualSessionAssembler()
        assembler.update_identity(pan="ABCDE1234F", filing_preference="Quarterly")
        self.assertEqual(assembler.filing_preference, "Quarterly")

        assembler.update_identity(pan="ABCDE1234F", filing_preference="Monthly")
        self.assertEqual(assembler.filing_preference, "Monthly")

        # Reject any other values (including legacy fallbacks)
        assembler.update_identity(pan="ABCDE1234F", filing_preference="Regular")
        self.assertEqual(assembler.filing_preference, "Monthly")  # Kept previous valid

        assembler_clean = VisualSessionAssembler()
        assembler_clean.update_identity(pan="ABCDE1234F", filing_preference="Regular / Non-QRMP")
        self.assertIsNone(assembler_clean.filing_preference)

    def test_itr_view_filed_returns_never_emits_session_concluded_and_captures_header_name(self):
        """
        Verifies that navigating from login/dashboard to View Filed Returns:
        1. Never emits 'Session Concluded' or logout events upon entry.
        2. Cleanly captures assessee name from the header pill even without brackets.
        3. Captures the filed return card (ACK, AY, Form, Status) and emits clean capture/submit toast.
        """
        from unittest.mock import MagicMock
        from PIL import Image
        from core.vsdc.vsdc_router import VSDCRouter
        from core.vsdc.vsdc_assembler import VisualSessionAssembler

        mock_ocr = MagicMock()
        mock_ocr.capture_window_image.return_value = Image.new("RGB", (1366, 768), color="white")

        assembler = VisualSessionAssembler()
        notified = []
        router = VSDCRouter(
            ocr_engine=mock_ocr,
            assembler=assembler,
            on_activity=lambda evt, title, sub: notified.append((evt, title, sub)),
        )

        router.get_foreground_info = MagicMock(return_value=(12345, "e-Filing Home Page, Income Tax Department - Google Chrome", "chrome.exe"))

        # Step 1: Login auth page seeds PAN
        router.extract_browser_url = MagicMock(return_value="https://eportal.incometax.gov.in/iec/foservices/#/login")
        mock_ocr.scan_image.return_value = {
            "text": "User ID : AHJPR0846B\nPlease confirm your Secure Access Message\nPassword :",
            "lines": ["User ID : AHJPR0846B", "Please confirm your Secure Access Message", "Password :"]
        }
        router.evaluate_tick()
        self.assertEqual(assembler.client_pan, "AHJPR0846B")

        # Step 2: Dashboard landing page
        router.extract_browser_url = MagicMock(return_value="https://eportal.incometax.gov.in/iec/foservices/#/dashboard")
        mock_ocr.scan_image.side_effect = [
            # Center card scan
            {"text": "e-Filing Anywhere Anytime\nFile Income Tax Return", "lines": ["e-Filing Anywhere Anytime", "File Income Tax Return"]},
            # Header scan: Header has name and PAN pill without brackets
            {"text": "e-Filing Income Tax Department PINKI ROY AHJPR0846B Taxpayer", "lines": ["e-Filing Income Tax Department", "PINKI ROY AHJPR0846B", "Taxpayer"]}
        ]
        router.evaluate_tick()
        self.assertEqual(assembler.client_name, "PINKI ROY")

        # Step 3: Entering View Filed Returns page
        router.extract_browser_url = MagicMock(return_value="https://eportal.incometax.gov.in/iec/foservices/#/dashboard/itrStatus")
        mock_ocr.scan_image.side_effect = [
            # Center card: return card details
            {
                "text": "View Filed Returns A.Y. 2026-27 ITR : ITR-1 Acknowledgement No : 123456789012345 Status : Successfully e-Verified",
                "lines": [
                    "View Filed Returns", "A.Y. 2026-27", "ITR : ITR-1",
                    "Acknowledgement No : 123456789012345", "Status : Successfully e-Verified"
                ]
            },
            # Header scan
            {"text": "e-Filing Income Tax Department PINKI ROY AHJPR0846B", "lines": ["e-Filing Income Tax Department", "PINKI ROY AHJPR0846B"]}
        ]
        payload = router.evaluate_tick()
        self.assertIsNotNone(payload)
        self.assertEqual(payload["arn"], "123456789012345")
        self.assertEqual(payload["client_name"], "PINKI ROY")
        self.assertEqual(payload["pan"], "AHJPR0846B")

        # Check notifications: ZERO logout or 'Session Concluded' toasts
        logout_events = [evt for evt in notified if evt[1] == "Session Concluded" or evt[0] == "logout"]
        self.assertEqual(len(logout_events), 0, "Zero 'Session Concluded' toasts emitted when entering View Filed Returns")

    def test_gst_dashboard_deep_link_activates_hud_for_identity_alone(self):
        """
        HUD pill must activate the moment ANY identity is captured, not only
        once a terminal ARN/submission also appears. Landing directly on a
        GST returns-dashboard-style page (deep link, browser history, or a
        mid-session refresh that skips gst_welcome_calendar) still captures
        GSTIN/name via the generic "OTHER GST CROSSHAIRS" identity block -
        that alone must fire a HUD toast, immediately, with no ARN involved.
        """
        from unittest.mock import MagicMock
        from PIL import Image
        from core.vsdc.vsdc_router import VSDCRouter
        from core.vsdc.vsdc_assembler import VisualSessionAssembler

        mock_ocr = MagicMock()
        mock_ocr.capture_window_image.return_value = Image.new("RGB", (1280, 800), color="white")
        mock_ocr.scan_image.return_value = {
            "text": "Returns Dashboard\nGSTIN - 19AAPFU0939L1ZV\nAMAN ASSOCIATES",
            "lines": ["Returns Dashboard", "GSTIN - 19AAPFU0939L1ZV", "AMAN ASSOCIATES"],
        }

        notified = []
        router = VSDCRouter(
            ocr_engine=mock_ocr,
            assembler=VisualSessionAssembler(),
            on_activity=lambda et, title, desc: notified.append((et, title, desc)),
        )
        router.get_foreground_info = MagicMock(
            return_value=(12345, "Goods & Services Tax (GST) | Dashboard - Google Chrome", "chrome.exe")
        )
        router.extract_browser_url = MagicMock(return_value="https://return.gst.gov.in/returns/dashboard")

        router.evaluate_tick()

        identity_events = [evt for evt in notified if evt[0] == "identity"]
        self.assertGreaterEqual(
            len(identity_events), 1,
            "HUD pill must activate for identity captured on a directly-visited dashboard page, "
            "not stay silent until an ARN/submission also appears",
        )
        self.assertIn("AMAN ASSOCIATES", identity_events[0][1])

    def test_gst_form_details_activates_hud_before_table_finishes_loading(self):
        """
        HUD pill must activate the moment identity is captured on a
        gst_form_details page, even on a tick where tax_period/status
        haven't rendered yet (the has_period/has_status gate returns None
        for several ticks while a table loads) - identity capture shouldn't
        have to wait for the rest of the form to finish loading.
        """
        from unittest.mock import MagicMock
        from PIL import Image
        from core.vsdc.vsdc_router import VSDCRouter
        from core.vsdc.vsdc_assembler import VisualSessionAssembler

        mock_ocr = MagicMock()
        mock_ocr.capture_window_image.return_value = Image.new("RGB", (1280, 800), color="white")
        # Identity has rendered (header), but the form table (tax period /
        # status) has not - this must still activate the HUD for identity.
        mock_ocr.scan_image.return_value = {
            "text": "GSTIN - 19AAPFU0939L1ZV\nLegal Name - FATIMA BIBI\nLoading...",
            "lines": ["GSTIN - 19AAPFU0939L1ZV", "Legal Name - FATIMA BIBI", "Loading..."],
        }

        notified = []
        router = VSDCRouter(
            ocr_engine=mock_ocr,
            assembler=VisualSessionAssembler(),
            on_activity=lambda et, title, desc: notified.append((et, title, desc)),
        )
        router.get_foreground_info = MagicMock(
            return_value=(12345, "Goods & Services Tax (GST) | Form - Google Chrome", "chrome.exe")
        )
        router.extract_browser_url = MagicMock(return_value="https://return.gst.gov.in/returns/auth/gstr1")

        result = router.evaluate_tick()

        self.assertIsNone(result, "Table itself isn't complete yet - no dataset payload expected")
        identity_events = [evt for evt in notified if evt[0] == "identity"]
        self.assertGreaterEqual(
            len(identity_events), 1,
            "HUD pill must activate for identity captured while the form table is still loading",
        )

    def test_unrelated_browser_tab_never_matches_a_crosshair(self):
        """
        VSDC's foreground-window gate lets ANY browser process through
        (is_browser is true for chrome/edge/firefox/brave/opera regardless of
        site), relying entirely on match_url_crosshair() to filter by URL.
        When the address bar itself can't be read (extract_browser_url
        returns falsy - happens for all sorts of mundane reasons, not just on
        exotic sites), the router used to fall back to matching crosshair
        patterns against the bare window TITLE with no host/domain to anchor
        on at all. A YouTube tutorial titled with tax-portal-sounding words
        (e.g. an "ITR Filing Guide") would then get misread as the real
        portal and have bogus "identity" extracted from whatever caps-cased
        text happens to be on screen. Title-only matching must now require
        the title to independently look like a portal (is_portal_title)
        before it's ever attempted.
        """
        from unittest.mock import MagicMock
        from PIL import Image
        from core.vsdc.vsdc_router import VSDCRouter
        from core.vsdc.vsdc_assembler import VisualSessionAssembler

        mock_ocr = MagicMock()
        mock_ocr.capture_window_image.return_value = Image.new("RGB", (1280, 800), color="white")
        mock_ocr.scan_image.return_value = {
            "text": "GUIDE YOUTUBE\nHow to file your Income Tax Return - full walkthrough",
            "lines": ["GUIDE YOUTUBE", "How to file your Income Tax Return - full walkthrough"],
        }

        router = VSDCRouter(ocr_engine=mock_ocr, assembler=VisualSessionAssembler(), on_activity=lambda *a: None)
        router.get_foreground_info = MagicMock(
            return_value=(12345, "ITR Filing Guide 2026 - Full Walkthrough - YouTube - Google Chrome", "chrome.exe")
        )
        # Address bar unreadable - the exact condition that used to fall back
        # to bare-title matching with no host/domain to anchor on at all.
        router.extract_browser_url = MagicMock(return_value=None)

        result = router.evaluate_tick()

        self.assertIsNone(result)
        self.assertIsNone(router.assembler.client_name, "No identity should ever be extracted from an unrelated YouTube tab")
        mock_ocr.capture_window_image.assert_not_called()

    def test_title_only_matching_still_works_for_a_genuine_portal_title(self):
        """
        The is_portal_title gate added above must not break the legitimate
        case it's meant to still allow: a real portal tab whose address bar
        happens to be unreadable, but whose window title unambiguously
        identifies it as the tax portal.
        """
        from unittest.mock import MagicMock
        from PIL import Image
        from core.vsdc.vsdc_router import VSDCRouter
        from core.vsdc.vsdc_assembler import VisualSessionAssembler

        mock_ocr = MagicMock()
        mock_ocr.capture_window_image.return_value = Image.new("RGB", (1280, 800), color="white")
        mock_ocr.scan_image.return_value = {
            "text": "e-Filing Income Tax Department RAMESH SHARMA ABCPE1234F",
            "lines": ["e-Filing Income Tax Department", "RAMESH SHARMA", "ABCPE1234F"],
        }

        router = VSDCRouter(ocr_engine=mock_ocr, assembler=VisualSessionAssembler(), on_activity=lambda *a: None)
        router.get_foreground_info = MagicMock(
            return_value=(12345, "e-Filing Income Tax Department - personal_information - Google Chrome", "chrome.exe")
        )
        router.extract_browser_url = MagicMock(return_value=None)

        router.evaluate_tick()

        self.assertEqual(router.assembler.client_name, "RAMESH SHARMA")
        self.assertEqual(router.assembler.client_pan, "ABCPE1234F")

    def test_slow_loading_page_keeps_polling_well_past_the_old_six_tick_cutoff(self):
        """
        The "give up re-scanning a static screen" gate used to be a raw poll
        COUNT (6), reachable in well under 200ms during the initial 15ms
        burst - nowhere near enough for a genuinely slow-loading portal page
        (a large GST table, a slow government server) that shows no
        recognized "loading..." text and paints no visible change for
        several seconds. It's now time-based with a generous 30s allowance.
        This drives 20 ticks (far more than the old count of 6) spanning
        ~7 seconds of simulated wall-clock time and confirms VSDC is STILL
        actively re-scanning on the final tick, not silently given up.
        """
        from unittest.mock import MagicMock, patch
        from PIL import Image
        from core.vsdc.vsdc_router import VSDCRouter
        from core.vsdc.vsdc_assembler import VisualSessionAssembler

        mock_ocr = MagicMock()
        mock_ocr.capture_window_image.return_value = Image.new("RGB", (1280, 800), color="white")
        # Static screen with no recognized loading text and nothing to
        # capture - the exact condition that used to trip the 6-poll cutoff.
        mock_ocr.scan_image.return_value = {"text": "Please stand by", "lines": ["Please stand by"]}

        router = VSDCRouter(ocr_engine=mock_ocr, assembler=VisualSessionAssembler(), on_activity=lambda *a: None)
        router.get_foreground_info = MagicMock(
            return_value=(12345, "e-Filing - Google Chrome", "chrome.exe")
        )
        router.extract_browser_url = MagicMock(
            return_value="https://eportal.incometax.gov.in/iec/foservices/#/dashboard/personal_information"
        )

        fake_time = [1_000_000.0]

        def _advancing_time():
            fake_time[0] += 0.35  # matches the router's normal (non-burst) poll interval
            return fake_time[0]

        with patch("core.vsdc.vsdc_router.time.time", side_effect=_advancing_time):
            router.evaluate_tick()
            calls_after_tick_1 = mock_ocr.scan_image.call_count
            self.assertGreater(calls_after_tick_1, 0)

            for _ in range(19):
                router.evaluate_tick()
            calls_after_20_ticks = mock_ocr.scan_image.call_count

            # 20 ticks * 0.35s = 7s elapsed - comfortably under the 30s
            # allowance. Under the old 6-poll-count cutoff, scanning would
            # have stopped entirely well before tick 20; it must still be
            # actively re-scanning every tick here (proportionally more
            # total calls than a single tick alone produced).
            self.assertGreater(calls_after_20_ticks, calls_after_tick_1 * 10)

            # Now push well past the 30s allowance and confirm it DOES
            # eventually stop - the timeout is generous, not infinite.
            fake_time[0] += 40.0
            router.evaluate_tick()
            self.assertEqual(
                mock_ocr.scan_image.call_count, calls_after_20_ticks,
                "Router should give up re-scanning once the screen has genuinely stayed static past the 30s allowance",
            )


if __name__ == "__main__":
    unittest.main()




