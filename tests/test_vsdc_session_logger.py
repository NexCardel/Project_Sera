"""
tests/test_vsdc_session_logger.py
=================================
Automated verification for VSDC session logging to 'Vsdc_Captures/<Client Name> (<PAN>).txt'.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from core.vsdc.vsdc_session_logger import VSDCSessionLogger, sanitize_filename
from core.vsdc.vsdc_assembler import VisualSessionAssembler


class TestVSDCSessionLogger(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_vsdc_captures_")
        self.logger = VSDCSessionLogger(output_dir=self.test_dir)

    def tearDown(self):
        try:
            if hasattr(self.logger, "_file_handle") and self.logger._file_handle and not self.logger._file_handle.closed:
                self.logger._file_handle.close()
            shutil.rmtree(self.test_dir, ignore_errors=True)
        except Exception:
            pass

    def test_sanitize_filename(self):
        self.assertEqual(sanitize_filename("M/S ABC & CO."), "M_S ABC & CO")
        self.assertEqual(sanitize_filename('Test: "Company" <Pvt> | Ltd? *'), "Test_ _Company_ _Pvt_ _ Ltd_ _")
        self.assertEqual(sanitize_filename("ARIF MOHAMMAD MOLLA"), "ARIF MOHAMMAD MOLLA")
        self.assertEqual(sanitize_filename(""), "UNKNOWN")

    def test_session_lifecycle_and_file_creation(self):
        # 1. Start session
        self.logger.start_session(
            session_id="sess_12345",
            portal="GST Common Portal",
            client_name="ARIF MOHAMMAD MOLLA",
            pan="CJLPM0265M",
            gstin="19CJLPM0265MIZO",
            filing_preference="Quarterly",
            initial_url="https://services.gst.gov.in/services/auth/fowelcome",
        )

        expected_filename = "ARIF MOHAMMAD MOLLA (CJLPM0265M).txt"
        target_file = Path(self.test_dir) / expected_filename
        self.assertTrue(target_file.exists(), f"Log file {expected_filename} must exist")

        # 2. Milestones
        self.logger.log_route_transition(
            crosshair_id="gst_form_details",
            url="https://return.gst.gov.in/returns/auth/gstr3b",
            description="GST return form table & period details",
        )

        self.logger.log_milestone(
            category="FORM CAPTURED",
            title="Captured GSTR-3B (Apr-Jun)",
            details={
                "form_type": "GSTR-3B",
                "period": "Apr-Jun (FY 2026-27)",
                "status": "Filed",
                "due_date": "24/07/2026",
            },
        )

        self.logger.log_milestone(
            category="SUBMISSION / ARN",
            title="GSTR-3B Submitted",
            details={
                "form_type": "GSTR-3B",
                "arn": "AA1907260012345",
                "status": "Submitted",
            },
        )

        # 3. End Session
        self.logger.end_session(
            reason="GST Logout",
            summary_items=[{
                "filing_type": "GSTR-3B",
                "period_label": "Apr-Jun (FY 2026-27)",
                "status": "Submitted",
                "arn": "AA1907260012345",
            }],
        )

        content = target_file.read_text(encoding="utf-8")
        self.assertIn("SERA VSDC AUDIT CAPTURE LOG", content)
        self.assertIn("ARIF MOHAMMAD MOLLA", content)
        self.assertIn("CJLPM0265M", content)
        self.assertIn("19CJLPM0265MIZO", content)
        self.assertIn("Quarterly", content)
        self.assertIn("[SESSION START]", content)
        self.assertIn("[ROUTE TRANSITION]", content)
        self.assertIn("[MILESTONE: FORM CAPTURED]", content)
        self.assertIn("[MILESTONE: SUBMISSION / ARN]", content)
        self.assertIn("AA1907260012345", content)
        self.assertIn("[SESSION END]", content)
        self.assertIn("SESSION CONCLUDED", content)

    def test_dynamic_file_rename_on_name_discovery(self):
        # Starts with only PAN
        self.logger.start_session(
            session_id="sess_dyn_001",
            portal="Income Tax",
            client_name=None,
            pan="AHJPR0846B",
        )

        initial_file = Path(self.test_dir) / "UNKNOWN (AHJPR0846B).txt"
        self.assertTrue(initial_file.exists())

        # Discovers name later
        self.logger.update_identity(name="AMAN ENTERPRISES", pan="AHJPR0846B")

        renamed_file = Path(self.test_dir) / "AMAN ENTERPRISES (AHJPR0846B).txt"
        self.assertTrue(renamed_file.exists(), "File should have been renamed to include client name")
        self.assertFalse(initial_file.exists(), "Old UNKNOWN file should no longer exist after rename")

        self.logger.end_session(reason="User Logout")
        content = renamed_file.read_text(encoding="utf-8")
        self.assertIn("AMAN ENTERPRISES", content)
        self.assertIn("[FILE RENAMED]", content)

    def test_multiple_sessions_same_client_generates_indexed_file(self):
        # Session 1
        logger1 = VSDCSessionLogger(output_dir=self.test_dir)
        logger1.start_session(
            session_id="sess_1",
            portal="Income Tax",
            client_name="JOHN DOE",
            pan="ABCDE1234F",
        )
        logger1.end_session(reason="Logout 1")

        file1 = Path(self.test_dir) / "JOHN DOE (ABCDE1234F).txt"
        self.assertTrue(file1.exists())

        # Session 2
        logger2 = VSDCSessionLogger(output_dir=self.test_dir)
        logger2.start_session(
            session_id="sess_2",
            portal="Income Tax",
            client_name="JOHN DOE",
            pan="ABCDE1234F",
        )
        logger2.end_session(reason="Logout 2")

        file2 = Path(self.test_dir) / "JOHN DOE (ABCDE1234F) (2).txt"
        self.assertTrue(file2.exists(), "Second session should create indexed filename without overwriting the first")

    def test_assembler_integration(self):
        assembler = VisualSessionAssembler(session_logger=self.logger)
        assembler.update_identity(name="TEST TRADERS", gstin="27AAPFU0939L1ZV", portal="GST Portal")
        assembler.update_selection(filing_type="GSTR-1", period_label="September 2026")
        assembler.record_gst_form_details(
            form_type="GSTR-1",
            tax_period="September",
            status="Filed",
            due_date="11/10/2026",
            fy="2026-27",
            period_label="September (FY 2026-27)",
        )
        assembler.record_submission(
            ack_number="AA2709260099999",
            status="Filed & Verified",
            filing_type="GSTR-1",
            period_label="September (FY 2026-27)",
        )
        assembler.seal_and_flush()
        assembler.reset()

        expected_file = Path(self.test_dir) / "TEST TRADERS (AAPFU0939L).txt"
        self.assertTrue(expected_file.exists(), f"File {expected_file} must exist from assembler integration")

        content = expected_file.read_text(encoding="utf-8")
        self.assertIn("TEST TRADERS", content)
        self.assertIn("AAPFU0939L", content)
        self.assertIn("GSTR-1", content)
        self.assertIn("AA2709260099999", content)
        self.assertIn("SESSION SEALED", content)


if __name__ == "__main__":
    unittest.main()
