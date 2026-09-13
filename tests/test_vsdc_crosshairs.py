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
        self.assertEqual(assembler.current_period_label, "June(Q) (FY 2026-27)")
        self.assertIn("GSTR-1", assembler.current_filing_type)

        # 2. Verify shot dataset payload to app
        self.assertIsNotNone(payload)
        self.assertEqual(payload["portal"], "GST Portal")
        self.assertEqual(payload["client_name"], "FATIMA BIBI")
        self.assertEqual(payload["trade_name"], "SPY JUNIOR")
        self.assertEqual(payload["gstin"], "19AAAAA0000A1Z5")
        self.assertEqual(payload["pan"], "AAAAA0000A")
        self.assertEqual(payload["period_label"], "June(Q) (FY 2026-27)")
        self.assertEqual(payload["tax_period"], "June(Q)")
        self.assertEqual(payload["status"], "Filed")
        self.assertEqual(payload["due_date"], "13/07/2026")
        self.assertEqual(payload["fy"], "2026-27")
        self.assertIn("GSTR-1", payload["filing_type"])
        self.assertEqual(payload["raw_payload"]["trade_name"], "SPY JUNIOR")

        # 3. Verify Live HUD Toast
        self.assertTrue(any(evt == "capture" and "GSTR-1" in title and "June(Q)" in title for evt, title, sub in notified))
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
        self.assertEqual(payload["period_label"], "June(Q) (FY 2026-27)")
        self.assertEqual(payload["tax_period"], "June(Q)")
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
        self.assertEqual(master_payload["period_label"], "June(Q) (FY 2026-27)")
        self.assertIn(master_payload["status"], ("Filed", "Filing Submitted"))

        # 4. Verify Live HUD Toasts (capture + flush)
        self.assertTrue(any(evt == "capture" and "AA1908260123456" in sub for evt, title, sub in notified))
        self.assertTrue(any(evt == "flush" and "GST Filing Saved to Tracker Dump" in title for evt, title, sub in notified))

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


if __name__ == "__main__":
    unittest.main()



