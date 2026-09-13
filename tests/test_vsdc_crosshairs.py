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


if __name__ == "__main__":
    unittest.main()
