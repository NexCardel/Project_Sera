"""
tests/test_vsdc_assembler.py — Unit Tests for Visual Session Assembler
"""

import unittest
from core.vsdc.vsdc_assembler import VisualSessionAssembler


class TestVSDCAssembler(unittest.TestCase):

    def setUp(self):
        self.assembler = VisualSessionAssembler()

    def test_multi_screen_session_assembly(self):
        # Screen 1: Landing / Identity
        self.assembler.record_step("https://eportal.incometax.gov.in/dashboard", "itr_landing")
        self.assembler.update_identity(pan="AHJPR0846B", name="AMAN ASSOCIATES", portal="Income Tax")

        # Screen 2: Form & Period Selection
        self.assembler.record_step("https://eportal.incometax.gov.in/fo-select-itr-form", "itr_form_select")
        self.assembler.update_selection(filing_type="ITR-4", period_label="AY 2026-27")

        # Screen 3: Submission & Ack Receipt
        self.assembler.record_step("https://eportal.incometax.gov.in/fo-return-success", "itr_filed_verified")
        self.assembler.record_submission(
            ack_number="982348123456789",
            status="Submitted (e-Verified)",
            crosshair_id="itr_filed_verified"
        )

        # Seal and verify master payload
        payload = self.assembler.seal_and_flush()
        self.assertIsNotNone(payload)
        self.assertEqual(payload["source"], "vsdc_optical")
        self.assertEqual(payload["pan"], "AHJPR0846B")
        self.assertEqual(payload["client_name"], "AMAN ASSOCIATES")
        self.assertEqual(payload["filing_type"], "ITR-4")
        self.assertEqual(payload["period_label"], "AY 2026-27")
        self.assertEqual(payload["arn"], "982348123456789")
        self.assertEqual(payload["status"], "Submitted (e-Verified)")

        # Verify raw_payload structure matches SDC contract
        raw_p = payload["raw_payload"]
        self.assertEqual(raw_p["client_temp_name"], "AMAN ASSOCIATES")
        self.assertEqual(len(raw_p["assembler_captures"]), 1)
        self.assertEqual(raw_p["assembler_captures"][0]["dataset_key"], "AHJPR0846B|ITR-4|AY 2026-27")
        self.assertEqual(len(raw_p["timeline"]), 3)

    def test_pan_context_switch_guard(self):
        # Client 1 session
        self.assembler.update_identity(pan="AHJPR0846B", name="CLIENT ONE")
        self.assembler.record_submission("111111111111111", "Submitted")

        # Switch to Client 2
        flushed_prior = self.assembler.update_identity(pan="BBBBB2222B", name="CLIENT TWO")
        self.assertIsNotNone(flushed_prior)
        self.assertEqual(flushed_prior["pan"], "AHJPR0846B")
        self.assertEqual(self.assembler.client_pan, "BBBBB2222B")
        # Ensure previous captures are cleared for the new client
        self.assertEqual(len(self.assembler.captures), 0)

    def test_no_dummy_visited_flush_without_submission(self):
        # Taxpayer logged in and navigated without filing
        self.assembler.update_identity(pan="AKIPG9686N", name="MOHAMMED GAZI", portal="Income Tax")
        self.assembler.update_selection(filing_type="ITR-1", period_label="AY 2025-26")
        self.assembler.record_step("https://eportal.incometax.gov.in/dashboard", "itr_landing")

        # On logout / session boundary: no filing was captured, returns None (no dummy records)
        payload = self.assembler.seal_and_flush()
        self.assertIsNone(payload)

    def test_dataset_completion_principle(self):
        # 1. Initially incomplete (no Period or Form)
        self.assembler.update_identity(pan="GZEPM6417A", name="MD SAYID MOLLA")
        self.assertIsNone(self.assembler.get_completed_dataset_payload())

        # 2. Add AY only -> still incomplete
        self.assembler.update_selection(period_label="AY 2026-27")
        self.assertIsNone(self.assembler.get_completed_dataset_payload())

        # 3. Add Form -> still incomplete because submit status is NOT captured from portal
        self.assembler.update_selection(filing_type="ITR-1")
        self.assertIsNone(self.assembler.get_completed_dataset_payload("itr_form_select"))

        # 4. Authoritative name upgrade -> still incomplete until submission
        self.assembler.update_identity(name="MD SAYID MOLLA UPDATED")
        self.assertIsNone(self.assembler.get_completed_dataset_payload("itr_personal_info"))

        # 5. Submission captured from portal -> Dataset is COMPLETE and shoots immediately!
        self.assembler.record_submission(
            ack_number="198273645019283",
            status="Submitted (Not e-Verified)",
            crosshair_id="itr_submitted_pending"
        )
        p1 = self.assembler.get_completed_dataset_payload("itr_submitted_pending")
        self.assertIsNotNone(p1)
        self.assertEqual(p1["pan"], "GZEPM6417A")
        self.assertEqual(p1["client_name"], "MD SAYID MOLLA UPDATED")
        self.assertEqual(p1["filing_type"], "ITR-1")
        self.assertEqual(p1["period_label"], "AY 2026-27")
        self.assertEqual(p1["arn"], "198273645019283")
        self.assertEqual(p1["status"], "Submitted (Not e-Verified)")

        # 6. Repeated call after submission -> Deduplicated (returns None)
        self.assertIsNone(self.assembler.get_completed_dataset_payload())

        # 7. Status Promotion: Can promote higher to 'Submitted (e-Verified)'
        self.assembler.record_submission(
            ack_number="198273645019283",
            status="Submitted (e-Verified)",
            crosshair_id="itr_filed_verified"
        )
        p2 = self.assembler.get_completed_dataset_payload("itr_filed_verified")
        self.assertIsNotNone(p2)
        self.assertEqual(p2["status"], "Submitted (e-Verified)")
        self.assertEqual(p2["arn"], "198273645019283")

        # 10. Cannot demote from 'Submitted (e-Verified)' down to 'Submitted (Not e-Verified)'
        self.assembler.record_submission(
            ack_number="198273645019283",
            status="Submitted (Not e-Verified)",
            crosshair_id="itr_submitted_pending"
        )
        # Capture retains higher rank
        cap = self.assembler.captures["GZEPM6417A|ITR-1|AY 2026-27"]
        self.assertEqual(cap["status"], "Submitted (e-Verified)")

    def test_seal_and_flush_rejects_dummy_capture_without_ack_or_valid_filing(self):
        # Emulate dummy capture when viewing returns screen with 0 filings
        self.assembler.update_identity(pan="GZEPM6367M", name="WASIL MANDAL", portal="Income Tax")
        self.assembler.record_step("https://eportal.incometax.gov.in/fo-view-filed-returns", "itr_view_filed_returns")
        self.assembler.record_submission(
            ack_number="N/A",
            status="Submitted",
            filing_type="Return",
            period_label="September 2026",
            crosshair_id="itr_view_filed_returns"
        )
        # seal_and_flush must reject this dummy capture and return None
        payload = self.assembler.seal_and_flush()
        self.assertIsNone(payload)

    def test_boundary_reset_and_workflow_clearing(self):
        # Setup active session with client and form selection
        self.assembler.update_identity(pan="GZEPM6367M", name="WASIL AMAN MANDAL", portal="Income Tax")
        self.assembler.update_selection(filing_type="ITR-1", period_label="AY 2026-27")
        self.assertEqual(self.assembler.current_filing_type, "ITR-1")
        self.assertEqual(self.assembler.current_period_label, "AY 2026-27")

        # 1. Test clear_workflow_selection(): preserves identity, wipes workflow
        self.assembler.clear_workflow_selection()
        self.assertIsNone(self.assembler.current_filing_type)
        self.assertIsNone(self.assembler.current_period_label)
        self.assertEqual(self.assembler.client_pan, "GZEPM6367M")
        self.assertEqual(self.assembler.client_name, "WASIL AMAN MANDAL")

        # 2. Test seal_and_flush() clears workflow variables automatically
        self.assembler.update_selection(filing_type="ITR-4", period_label="AY 2026-27")
        self.assembler.record_submission(
            ack_number="123456789012345",
            status="Submitted (e-Verified)",
            crosshair_id="itr_filed_verified"
        )
        payload = self.assembler.seal_and_flush()
        self.assertIsNotNone(payload)
        self.assertEqual(payload["filing_type"], "ITR-4")
        # Workflow variables must be cleared to avoid bleeding into next screen
        self.assertIsNone(self.assembler.current_filing_type)
        self.assertIsNone(self.assembler.current_period_label)
        self.assertEqual(len(self.assembler.captures), 0)

        # 3. Test full session reset() at boundary
        self.assembler.reset()
        self.assertIsNone(self.assembler.client_pan)
        self.assertIsNone(self.assembler.client_name)
        self.assertIsNone(self.assembler.current_filing_type)


if __name__ == "__main__":
    unittest.main()
