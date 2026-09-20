"""
tests/test_vsdc247.py - VSDC 24x7, the crosshair-independent submission safety net.

Every "must NOT capture" case here is a mistake this project actually made or nearly made.
"""

import unittest
from datetime import date
from unittest.mock import MagicMock

from PIL import Image, ImageDraw

from core.vsdc.vsdc247 import (
    CERTAIN_SCORE,
    GreenBox,
    Vsdc247Scanner,
    analyze_frame,
    find_green_boxes,
    itr_ack_date,
)

TODAY = date(2026, 8, 28)
NEW_ACK = "901036690280826"     # filed 28-Aug-2026 (the live Mohammad Molla card)
OLD_ACK = "598290000150925"     # filed 15-Sep-2025


def frame(lines, portal="Income Tax", boxes=(), words=(), today=TODAY, **kw):
    return analyze_frame("\n".join(lines), lines, list(words), list(boxes), portal, today=today, **kw)


ITR_CONFIRMATION = [
    "e-Filing Anywhere Anytime",
    "Your return has been submitted successfully",
    f"Acknowledgement Number : {NEW_ACK}",
    "ITR-4  Assessment Year 2026-27",
    "Filing Date : 28-Aug-2026",
]


class TestAckDate(unittest.TestCase):
    def test_every_live_ack_carries_its_own_filing_date(self):
        # (ack, filing date the portal showed next to it) - all taken from live screens.
        for ack, expect in (
            ("598290000150925", date(2025, 9, 15)), ("901036690280826", date(2026, 8, 28)),
            ("827916720300726", date(2026, 7, 30)), ("234567890310724", date(2024, 7, 31)),
            ("345678901290723", date(2023, 7, 29)), ("456789012270722", date(2022, 7, 27)),
        ):
            self.assertEqual(itr_ack_date(ack), expect, ack)

    def test_a_number_that_is_not_an_ack_has_no_date(self):
        self.assertIsNone(itr_ack_date("123456789012345"))    # 34th month
        self.assertIsNone(itr_ack_date("12345"))
        self.assertIsNone(itr_ack_date(""))


class TestCapturesARealSubmission(unittest.TestCase):
    def test_itr_confirmation_is_certain_and_carries_everything(self):
        d = frame(ITR_CONFIRMATION)
        self.assertEqual(d.tier, "certain")
        self.assertEqual(d.identifier, NEW_ACK)
        self.assertEqual((d.form, d.period, d.filing_date), ("ITR-4", "AY 2026-27", "2026-08-28"))
        self.assertGreaterEqual(d.score, 95)          # strong enough for a single frame
        self.assertTrue(d.status.startswith("Submitted") or d.status == "Filing Submitted")

    def test_evidence_is_structured_and_never_page_text(self):
        ev = frame(ITR_CONFIRMATION).evidence
        self.assertEqual(set(ev), {"score", "tier", "reasons", "vetoes", "green_box"})
        self.assertNotIn("Anywhere", " ".join(ev["reasons"]))

    def test_ocr_digit_confusions_in_the_ack_are_repaired_and_still_validated(self):
        misread = NEW_ACK.replace("0", "O", 2)         # 9O1O36690280826
        lines = list(ITR_CONFIRMATION)
        lines[2] = f"Acknowledgement Number : {misread}"
        self.assertEqual(frame(lines).identifier, NEW_ACK)

    def test_label_on_the_previous_line_counts(self):
        lines = ["Return submitted successfully", "Acknowledgement Number", NEW_ACK, "ITR-4  AY 2026-27"]
        self.assertEqual(frame(lines).identifier, NEW_ACK)

    def test_a_revised_return_captures_the_new_ack_and_ignores_the_original(self):
        lines = [
            "Your return has been submitted successfully",
            f"Acknowledgement Number : {NEW_ACK}",
            f"Acknowledgement Number of Original Return : {OLD_ACK}",
            "ITR-4  Assessment Year 2025-26",
        ]
        d = frame(lines)
        self.assertEqual((d.tier, d.identifier), ("certain", NEW_ACK))

    def test_gst_arn_with_its_words_and_form(self):
        lines = ["Your return has been filed successfully", "ARN : AA070826000001Z", "GSTR-3B  Return Period: June 2026"]
        d = frame(lines, portal="GST Portal")
        self.assertEqual(d.identifier, "AA070826000001Z")
        self.assertEqual(d.form, "GSTR-3B")
        self.assertIn(d.tier, ("certain", "probable"))

    def test_gst_check_letter_is_never_repaired_into_a_digit(self):
        # Z is a genuine check character; only the numeric body may be repaired.
        lines = ["Filed successfully", "ARN : AA070826000001Z", "GSTR-1"]
        self.assertTrue(frame(lines, portal="GST Portal").identifier.endswith("Z"))

    def test_other_arn_bearing_submissions_are_captured_too(self):
        # Decision: ALL ARN-bearing submissions, not only returns (registration, refund...).
        lines = ["Your application has been submitted successfully", "Application Reference Number (ARN) : AA2708260012345",
                 "REG-01"]
        d = frame(lines, portal="GST Portal")
        self.assertEqual(d.identifier, "AA2708260012345")
        self.assertEqual(d.form, "REG-01")

    def test_a_green_box_around_the_message_adds_confidence(self):
        words = [
            {"text": "successfully", "x": 200, "y": 100, "width": 90, "height": 20},
            {"text": NEW_ACK, "x": 300, "y": 130, "width": 160, "height": 20},
        ]
        box = GreenBox(150, 80, 500, 100)
        with_box = frame(ITR_CONFIRMATION, boxes=[box], words=words)
        without = frame(ITR_CONFIRMATION)
        self.assertTrue(with_box.in_green_box)
        self.assertEqual(with_box.score, without.score + 15)
        # A green box elsewhere on screen does not count.
        far = frame(ITR_CONFIRMATION, boxes=[GreenBox(900, 600, 200, 60)], words=words)
        self.assertFalse(far.in_green_box)


class TestNeverCapturesWrongStuff(unittest.TestCase):
    def assertVetoed(self, lines, portal="Income Tax", reason_part=None, **kw):
        d = frame(lines, portal=portal, **kw)
        self.assertIsNotNone(d, "expected a veto record explaining why")
        self.assertEqual(d.tier, "vetoed", d.reasons)
        if reason_part:
            self.assertTrue(any(reason_part in v for v in d.vetoes), d.vetoes)
        return d

    def test_the_original_returns_ack_on_a_revised_return_wizard(self):
        # THE live false capture: a revised-return wizard page printed the original ack.
        self.assertVetoed(
            ["Are you filing a revised return u/s 139(5)?", f"Acknowledgement Number of Original Return : {OLD_ACK}",
             "Date of Original Filing : 15-Sep-2025"],
            reason_part="another filing")

    def test_an_old_ack_is_not_a_live_submission_even_with_success_wording(self):
        self.assertVetoed(
            ["Return submitted successfully", f"Acknowledgement Number : {OLD_ACK}", "ITR-4"],
            reason_part="not today")

    def test_view_filed_returns_history_is_a_list_not_a_submission(self):
        self.assertVetoed(
            ["14 Filings till date", f"Acknowledgement No : {NEW_ACK}", "ITR : ITR-4",
             "Acknowledgement No : 234567890310724", "Successfully e-verified"],
            reason_part="different identifiers")

    def test_the_e_verify_picker_and_stepper_labels(self):
        lines = ["Select The Return To Be Verified", "Select Method For Return Verification",
                 "Return Successfully Verified", f"Acknowledgement Number : {NEW_ACK}", "E-Verify"]
        d = frame(lines)
        # Stepper labels are stripped, so there is no success wording -> never CERTAIN.
        self.assertNotEqual(d.tier, "certain")
        self.assertFalse(d.has_success_wording)

    def test_a_bare_number_with_no_label(self):
        self.assertVetoed(["Return submitted successfully", NEW_ACK, "Total tax 5000"], reason_part="label")

    def test_a_random_fifteen_digit_number_that_is_not_a_date_stamped_ack(self):
        self.assertVetoed(["Successfully submitted", "Acknowledgement Number : 123456789012345"], reason_part="valid filing date")

    def test_failure_wording(self):
        self.assertVetoed(["Submission failed. Please try again", f"Acknowledgement Number : {NEW_ACK}"],
                          reason_part="failure")
        self.assertVetoed(["Error: return not submitted", f"Acknowledgement Number : {NEW_ACK}"], reason_part="failure")

    def test_help_text_describing_the_future(self):
        # The success words are there, but they describe what WILL happen.
        d = frame([f"Acknowledgement Number : {NEW_ACK}",
                   "You will be notified once your return has been submitted successfully"])
        self.assertNotEqual(d.tier, "certain")
        self.assertFalse(d.has_success_wording)

    def test_the_same_words_stated_as_fact_are_accepted(self):
        d = frame([f"Acknowledgement Number : {NEW_ACK}", "Your return has been submitted successfully", "ITR-4 AY 2026-27"])
        self.assertEqual(d.tier, "certain")

    def test_drafts_and_saves(self):
        self.assertVetoed(["Return saved as draft", f"Acknowledgement Number : {NEW_ACK}", "saved successfully"],
                          reason_part="draft")

    def test_no_success_wording_is_never_certain(self):
        d = frame(["Acknowledgement Number", NEW_ACK, "ITR-4 AY 2026-27"])
        self.assertNotEqual(d.tier, "certain")

    def test_a_page_with_no_identifier_at_all(self):
        self.assertIsNone(frame(["Welcome to the portal", "Your return has been submitted successfully"]))
        self.assertIsNone(frame([]))

    def test_a_gstin_or_pan_is_not_an_identifier(self):
        self.assertIsNone(frame(["Filed successfully", "GSTIN : 19CJLPM0265M1ZO", "PAN : ABCPE1234F"], portal="GST Portal"))

    def test_gst_list_of_several_arns(self):
        self.assertVetoed(["ARN : AA070826000001Z", "ARN : AA070826000002Y", "Filed"], portal="GST Portal",
                          reason_part="different identifiers")

    def test_other_portals_are_not_analysed(self):
        self.assertIsNone(frame(ITR_CONFIRMATION, portal="Some Bank"))


class TestGreenBoxDetection(unittest.TestCase):
    W, H = 1366, 768

    def _page(self, draw_fn=None):
        img = Image.new("RGB", (self.W, self.H), (245, 246, 250))
        if draw_fn:
            draw_fn(ImageDraw.Draw(img))
        return img

    def test_a_pale_green_banner_is_found(self):
        img = self._page(lambda d: d.rounded_rectangle((120, 200, 1240, 420), radius=8, fill=(220, 240, 224)))
        boxes = find_green_boxes(img)
        self.assertEqual(len(boxes), 1)
        b = boxes[0]
        self.assertTrue(b.contains(600, 300))
        self.assertFalse(b.contains(600, 600))

    def test_a_saturated_green_banner_is_found(self):
        self.assertEqual(len(find_green_boxes(self._page(lambda d: d.rectangle((100, 100, 900, 200), fill=(40, 167, 69))))), 1)

    def test_a_plain_page_has_none(self):
        self.assertEqual(find_green_boxes(self._page()), [])

    def test_small_green_things_are_not_banners(self):
        def draw(d):
            for x in (100, 300, 500):
                d.ellipse((x, 300, x + 28, 328), fill=(46, 125, 50))     # check-mark circles
            d.rectangle((20, 20, 60, 30), fill=(0, 150, 60))              # a thin logo strip
        self.assertEqual(find_green_boxes(self._page(draw)), [])

    def test_a_blue_or_red_banner_is_not_green(self):
        self.assertEqual(find_green_boxes(self._page(lambda d: d.rectangle((100, 100, 900, 200), fill=(220, 230, 250)))), [])
        self.assertEqual(find_green_boxes(self._page(lambda d: d.rectangle((100, 100, 900, 200), fill=(248, 215, 218)))), [])

    def test_tiny_or_missing_frames_are_safe(self):
        self.assertEqual(find_green_boxes(None), [])
        self.assertEqual(find_green_boxes(Image.new("RGB", (10, 10))), [])


class TestScannerCadence(unittest.TestCase):
    """When to look (cheap) and when to believe (agreement)."""

    def _ocr(self, lines, words=()):
        ocr = MagicMock()
        ocr.scan_image.return_value = {"text": "\n".join(lines), "lines": list(lines), "words": list(words)}
        return ocr

    def _img(self, shade):
        return Image.new("RGB", (1366, 768), (shade, shade, shade))

    def test_the_first_frame_is_read_once_and_a_static_screen_afterwards_costs_nothing(self):
        s, ocr = Vsdc247Scanner(), self._ocr(["Dashboard"])
        first = s.scan(self._img(240), ocr, "Income Tax", 0.0, today=TODAY)
        self.assertTrue(first.scanned)
        self.assertEqual(ocr.scan_image.call_count, 1)
        for t in (0.4, 0.8, 1.2, 60.0):
            self.assertFalse(s.scan(self._img(240), ocr, "Income Tax", t, today=TODAY).scanned)
        self.assertEqual(ocr.scan_image.call_count, 1)

    def test_a_confirmation_already_on_screen_at_first_sight_is_not_missed(self):
        s, ocr = Vsdc247Scanner(), self._ocr(ITR_CONFIRMATION)
        out = s.scan(self._img(240), ocr, "Income Tax", 0.0, today=TODAY)
        self.assertEqual(out.detection.tier, "certain")

    def test_a_frame_the_crosshair_pipeline_already_handled_is_not_read_again(self):
        s, ocr = Vsdc247Scanner(), self._ocr(ITR_CONFIRMATION)
        s.observe(self._img(240))
        self.assertFalse(s.scan(self._img(240), ocr, "Income Tax", 5.0, today=TODAY).scanned)
        ocr.scan_image.assert_not_called()

    def test_a_changed_frame_is_read_and_a_strong_capture_needs_no_second_read(self):
        s, ocr = Vsdc247Scanner(), self._ocr(ITR_CONFIRMATION)
        s.scan(self._img(240), ocr, "Income Tax", 0.0, today=TODAY)
        out = s.scan(self._img(200), ocr, "Income Tax", 0.5, today=TODAY)
        self.assertTrue(out.scanned)
        self.assertEqual(out.detection.tier, "certain")

    def test_scans_are_rate_limited(self):
        s, ocr = Vsdc247Scanner(), self._ocr(["nothing here"])
        s.scan(self._img(240), ocr, "Income Tax", 0.0, today=TODAY)
        s.scan(self._img(200), ocr, "Income Tax", 0.5, today=TODAY)
        out = s.scan(self._img(160), ocr, "Income Tax", 0.6, today=TODAY)      # only 0.1s later
        self.assertFalse(out.scanned)

    def test_a_green_box_appearing_is_read_at_once_even_inside_the_interval(self):
        s, ocr = Vsdc247Scanner(), self._ocr(ITR_CONFIRMATION)
        s.scan(self._img(240), ocr, "Income Tax", 0.0, today=TODAY)
        s.scan(self._img(200), ocr, "Income Tax", 0.5, today=TODAY)
        banner = Image.new("RGB", (1366, 768), (245, 246, 250))
        ImageDraw.Draw(banner).rectangle((100, 100, 900, 220), fill=(220, 240, 224))
        out = s.scan(banner, ocr, "Income Tax", 0.55, today=TODAY)              # 0.05s later
        self.assertTrue(out.scanned)
        self.assertIn("green", out.reason)

    def test_a_moderate_candidate_is_believed_only_after_a_second_read(self):
        # GST ARN + wording, no form/period/green: 70 -> probable, then agreement.
        lines = ["Your return has been filed successfully", "ARN : AA070826000001Z"]
        s, ocr = Vsdc247Scanner(), self._ocr(["Dashboard"])
        s.scan(self._img(240), ocr, "GST Portal", 0.0, today=TODAY)         # first frame: read once, nothing on it
        ocr.scan_image.return_value = self._ocr(lines).scan_image.return_value
        first = s.scan(self._img(200), ocr, "GST Portal", 0.5, today=TODAY)
        self.assertEqual(first.detection.tier, "probable")
        second = s.scan(self._img(200), ocr, "GST Portal", 1.0, today=TODAY)   # same frame, re-read
        self.assertTrue(second.scanned)
        self.assertEqual(second.detection.tier, "certain")
        self.assertTrue(second.promoted_by_agreement)

    def test_agreement_never_promotes_a_bare_number_without_success_wording(self):
        lines = ["Acknowledgement Number", NEW_ACK, "ITR-4 AY 2026-27"]
        s, ocr = Vsdc247Scanner(), self._ocr(lines)
        s.scan(self._img(240), ocr, "Income Tax", 0.0, today=TODAY)
        tiers = []
        for i, t in enumerate((0.5, 1.0, 1.5, 2.0, 2.5)):
            out = s.scan(self._img(200 - i * 10), ocr, "Income Tax", t, today=TODAY)
            if out.detection:
                tiers.append(out.detection.tier)
        self.assertTrue(tiers)
        self.assertNotIn("certain", tiers)

    def test_a_vetoed_frame_resets_pending_agreement(self):
        good = ["Your return has been filed successfully", "ARN : AA070826000001Z"]
        ocr = MagicMock()
        s = Vsdc247Scanner()
        s.scan(self._img(240), self._ocr(good), "GST Portal", 0.0, today=TODAY)
        s.scan(self._img(200), self._ocr(good), "GST Portal", 0.5, today=TODAY)
        s.scan(self._img(160), self._ocr(["Submission failed", "ARN : AA070826000001Z"]), "GST Portal", 1.0, today=TODAY)
        again = s.scan(self._img(120), self._ocr(good), "GST Portal", 1.5, today=TODAY)
        self.assertEqual(again.detection.tier, "probable")     # counting from scratch


if __name__ == "__main__":
    unittest.main()


class TestArnShapeIsEvidenceNotARule(unittest.TestCase):
    """'Starts with two letters' is an observed pattern, not a documented rule."""

    GOOD = ["Your return has been filed successfully", "ARN : AA070826000001Z", "GSTR-3B  Return Period: June 2026"]

    def test_the_usual_shape_is_saved(self):
        d = frame(self.GOOD, portal="GST Portal")
        self.assertEqual((d.tier, d.usual_shape), ("certain", True))

    def test_an_arn_that_breaks_the_shape_is_shown_but_never_saved(self):
        lines = ["Your return has been filed successfully", "ARN : 070826000001ZQ9", "GSTR-3B  Return Period: June 2026"]
        d = frame(lines, portal="GST Portal")
        self.assertIsNotNone(d, "a labelled number that breaks the pattern must not be silently missed")
        self.assertEqual(d.tier, "probable")
        self.assertFalse(d.usual_shape)
        self.assertTrue(any("usual" in r for r in d.reasons))

    def test_a_loose_number_is_not_found_when_a_usual_one_is_present(self):
        lines = self.GOOD + ["Application Reference No : 5551234567890123"]
        d = frame(lines, portal="GST Portal")
        self.assertEqual((d.tier, d.identifier), ("certain", "AA070826000001Z"))

    def test_an_odd_shaped_arn_is_never_promoted_by_agreement(self):
        lines = ["Your return has been filed successfully", "ARN : 070826000001ZQ9"]
        ocr = MagicMock()
        ocr.scan_image.return_value = {"text": "\n".join(lines), "lines": lines, "words": []}
        s = Vsdc247Scanner()
        s.scan(Image.new("RGB", (400, 300), (240,) * 3), ocr, "GST Portal", 0.0, today=TODAY)
        tiers = []
        for i in range(1, 6):
            out = s.scan(Image.new("RGB", (400, 300), (240 - 9 * i,) * 3), ocr, "GST Portal", 0.5 * i, today=TODAY)
            if out.detection:
                tiers.append(out.detection.tier)
        self.assertTrue(tiers)
        self.assertNotIn("certain", tiers)

    def test_each_portal_only_reads_its_own_identifier(self):
        self.assertIsNone(frame(["Filed successfully", "ARN : AA070826000001Z", "GSTR-3B"], portal="Income Tax"))
        self.assertIsNone(frame(["Submitted successfully", f"Acknowledgement Number : {NEW_ACK}", "ITR-4"], portal="GST Portal"))
