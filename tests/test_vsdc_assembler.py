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

    def test_dob_captured_and_flows_into_sealed_payload(self):
        self.assembler.record_step("https://eportal.incometax.gov.in/dashboard", "itr_landing")
        self.assembler.update_identity(pan="AHJPR0846B", name="AMAN ASSOCIATES", dob="06-Aug-1971")
        self.assertEqual(self.assembler.dob, "06-Aug-1971")

        self.assembler.record_step("https://eportal.incometax.gov.in/fo-return-success", "itr_filed_verified")
        self.assembler.record_submission(
            ack_number="982348123456789",
            status="Submitted (e-Verified)",
            crosshair_id="itr_filed_verified",
        )

        payload = self.assembler.seal_and_flush()
        self.assertIsNotNone(payload)
        self.assertEqual(payload["dob"], "06-Aug-1971")

    def test_dob_is_fixed_once_set_not_overwritten(self):
        self.assembler.update_identity(pan="AHJPR0846B", dob="06-Aug-1971")
        # A later, different DOB read (e.g. a mis-OCR) must not overwrite the
        # first valid value the same way name/pref can be upgraded
        self.assembler.update_identity(dob="01-Jan-1999")
        self.assertEqual(self.assembler.dob, "06-Aug-1971")

    def test_dob_resets_on_pan_context_switch(self):
        self.assembler.update_identity(pan="AHJPR0846B", dob="06-Aug-1971")
        self.assembler.update_identity(pan="BBBBB2222B", name="CLIENT TWO")
        self.assertIsNone(self.assembler.dob)

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

    def test_period_normalization_and_predefined_month_list(self):
        from core.vsdc.vsdc_assembler import normalize_period, MONTHS, MONTH_MAP

        # Verify predefined months exist
        self.assertEqual(len(MONTHS), 12)
        self.assertIn("June", MONTHS)
        self.assertIn("September", MONTHS)

        # 1. Quarters resolve to statutory terminal months
        m1, fy1, lbl1 = normalize_period("Apr-Jun", "2026-27", portal="GST Portal")
        self.assertEqual(m1, "June")
        self.assertEqual(fy1, "2026-27")
        self.assertEqual(lbl1, "June (FY 2026-27)")

        m2, fy2, lbl2 = normalize_period("Jul-Sep", "2026-27", portal="GST Portal")
        self.assertEqual(m2, "September")
        self.assertEqual(lbl2, "September (FY 2026-27)")

        m3, fy3, lbl3 = normalize_period("Oct-Dec", "2026-27", portal="GST Portal")
        self.assertEqual(m3, "December")
        self.assertEqual(lbl3, "December (FY 2026-27)")

        m4, fy4, lbl4 = normalize_period("Jan-Mar", "2026-27", portal="GST Portal")
        self.assertEqual(m4, "March")
        self.assertEqual(lbl4, "March (FY 2026-27)")

        # 2. Monthly returns
        m5, fy5, lbl5 = normalize_period("September", "2026-27", portal="GST Portal")
        self.assertEqual(m5, "September")
        self.assertEqual(lbl5, "September (FY 2026-27)")

        # 3. Income Tax AY
        m_itr, fy_itr, lbl_itr = normalize_period("AY 2026-27", portal="Income Tax")
        self.assertEqual(lbl_itr, "AY 2026-27")

    def test_strict_pan_entity_id_resolution(self):
        from core.vsdc.vsdc_assembler import resolve_entity_pan

        # 1. Extracted strictly from GSTIN characters 2..12
        pan1 = resolve_entity_pan(pan=None, gstin="19CJLPM0265MIZO")
        self.assertEqual(pan1, "CJLPM0265M")

        # 2. Directly provided PAN takes precedence
        pan2 = resolve_entity_pan(pan="AHJPR0846B", gstin="27AHJPR0846B1Z5")
        self.assertEqual(pan2, "AHJPR0846B")

        # 3. Invalid or arbitrary string rejected from being entity_id
        pan3 = resolve_entity_pan(pan=None, gstin="INVALID")
        self.assertEqual(pan3, "UNKNOWN")

    def test_multi_filing_in_single_session(self):
        # Assessee logs in
        self.assembler.update_identity(name="ARIF MOHAMMAD MOLLA", gstin="19CJLPM0265MIZO", portal="GST Portal")
        self.assertEqual(self.assembler.client_pan, "CJLPM0265M")

        # Filing 1: GSTR-1 for Apr-Jun (Q1)
        self.assembler.update_selection(filing_type="GSTR-1", period_label="Apr-Jun (FY 2026-27)")
        self.assembler.record_submission(
            ack_number="AA1904260011111",
            status="Filed",
            filing_type="GSTR-1",
            period_label="Apr-Jun (FY 2026-27)",
        )
        self.assertEqual(len(self.assembler.records), 1)

        # Assessee navigates back to Returns Dashboard (clear_workflow_selection)
        self.assembler.clear_workflow_selection()
        # Identity and completed GSTR-1 record MUST BE PRESERVED!
        self.assertEqual(self.assembler.client_pan, "CJLPM0265M")
        self.assertEqual(self.assembler.client_name, "ARIF MOHAMMAD MOLLA")
        self.assertEqual(len(self.assembler.records), 1)

        # Filing 2: GSTR-3B for Apr-Jun (Q1)
        self.assembler.update_selection(filing_type="GSTR-3B", period_label="Apr-Jun (FY 2026-27)")
        self.assembler.record_submission(
            ack_number="AA1907260022222",
            status="Filed",
            filing_type="GSTR-3B",
            period_label="Apr-Jun (FY 2026-27)",
        )
        # Both records co-exist in the registry!
        self.assertEqual(len(self.assembler.records), 2)

        # Conclude session and flush
        payload = self.assembler.seal_and_flush()
        self.assertIsNotNone(payload)
        self.assertEqual(payload["pan"], "CJLPM0265M")
        self.assertEqual(payload["client_name"], "ARIF MOHAMMAD MOLLA")

        # Master payload captures BOTH returns cleanly
        captures = payload["raw_payload"]["assembler_captures"]
        self.assertEqual(len(captures), 2)
        forms_captured = {c["filing_type"]: c["arn"] for c in captures}
        self.assertEqual(forms_captured["GSTR-1"], "AA1904260011111")
        self.assertEqual(forms_captured["GSTR-3B"], "AA1907260022222")


if __name__ == "__main__":
    unittest.main()
