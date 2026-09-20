"""
tests/test_vsdc_everify_picker.py - the e-Verify RETURN PICKER ("e-Verify / Discard Return").

The picker lists every filed return that still awaits e-Verification, one card each. Every card
is therefore "Submitted, e-Verification pending" and is captured as such - with its form, its
assessment year, its filing type (Original / Revised / ...), its ack and the portal's own
filed-on date.

The page shares one URL (.../eVerifyReturn/eVerifyReturn-al) with the OTP step and the
confirmation, so it is recognised by its own heading and cards, not by the address bar. The line
layout used here is the one UI Automation really reports for the page (recorded in
tools/vsdc_uia_probe_output): every label and its value are separate lines.
"""

import unittest
from unittest.mock import MagicMock, patch

from PIL import Image

from core.vsdc.vsdc_assembler import VisualSessionAssembler
from core.vsdc.vsdc_crosshairs import get_crosshair, match_url_crosshair
from core.vsdc.vsdc_regex import (
    extract_everify_picker_cards,
    extract_everify_picker_count,
    is_everify_picker_page,
)
from core.vsdc.vsdc_router import VSDCRouter

URL = "https://eportal.incometax.gov.in/iec/foservices/#/dashboard/eVerifyReturn/eVerifyReturn-al"
TITLE = "Income Tax Portal, Government of India - Google Chrome"

PAN = "ABCPD5678E"
ACK_1 = "123456789290826"
ACK_2 = "111111111111111"
ACK_3 = "222222222222222"

STEPPER = ["e-Verify Return", "Current Step 1 of 3 in stepper", "Select The Return To Be Verified", "1",
           "Unvisited Step 2 of 3 in stepper", "Select Method For Return Verification", "2",
           "Unvisited Step 3 of 3 in stepper", "Return Successfully Verified", "3"]


def head(n):
    return ["e-Verify / Discard Return", "Please select the return you would like to verify/discard",
            f"Showing ({n}) returns", "Search Box Input Field"]


def card(ay="2026-27", form="ITR 4", ftype="Original", pan=PAN, ack=ACK_1, filed="Aug 29, 2026"):
    return ["Assessment Year", ay, "ITR", form, "Filing Type", ftype, "PAN :", pan,
            "Acknowledgement Number :", ack, "Filed On :", filed, "Applicable Act :", "Income Tax Act 1961",
            "e verify", "Discard"]


def picker(*cards):
    lines = list(STEPPER) + head(len(cards))
    for c in cards:
        lines += c
    return lines


class TestPickerParsing(unittest.TestCase):
    def test_the_real_page_layout_gives_one_exact_card(self):
        self.assertEqual(extract_everify_picker_cards(picker(card())), [{
            "ay": "2026-27", "form": "ITR-4", "filing_preference": "Original", "pan": PAN,
            "ack": ACK_1, "filed_on": "2026-08-29"}])

    def test_the_ocr_layout_with_label_and_value_on_one_line_reads_the_same(self):
        ocr = ["Assessment Year 2026-27", "ITR 4", "Filing Type Original", f"PAN : {PAN}",
               f"Acknowledgement Number : {ACK_1}", "Filed On : Aug 29, 2026"]
        self.assertEqual(extract_everify_picker_cards(ocr)[0]["ack"], ACK_1)
        self.assertEqual(extract_everify_picker_cards(ocr)[0]["form"], "ITR-4")

    def test_several_cards_come_out_in_page_order_each_with_its_own_fields(self):
        cards = extract_everify_picker_cards(picker(
            card(form="ITR 3", ftype="Revised", ack=ACK_2, filed="Sep 02, 2026"),
            card(ay="2025-26", form="ITR 4", ftype="Belated", ack=ACK_3, filed="Jul 30, 2026")))
        self.assertEqual([(c["form"], c["ay"], c["filing_preference"], c["ack"], c["filed_on"]) for c in cards],
                         [("ITR-3", "2026-27", "Revised", ACK_2, "2026-09-02"),
                          ("ITR-4", "2025-26", "Belated", ACK_3, "2026-07-30")])

    def test_one_year_heading_over_two_cards_applies_to_both(self):
        lines = ["Assessment Year", "2026-27",
                 "ITR", "ITR 4", "Filing Type", "Original", "PAN :", PAN, "Acknowledgement Number :", ACK_1, "Filed On :", "Aug 29, 2026",
                 "ITR", "ITR 4", "Filing Type", "Revised", "PAN :", PAN, "Acknowledgement Number :", ACK_2, "Filed On :", "Sep 02, 2026"]
        got = extract_everify_picker_cards(lines)
        self.assertEqual([(c["ay"], c["filing_preference"], c["ack"]) for c in got],
                         [("2026-27", "Original", ACK_1), ("2026-27", "Revised", ACK_2)])

    def test_a_card_without_an_ack_is_dropped(self):
        lines = ["Assessment Year", "2026-27", "ITR", "ITR 4", "Filing Type", "Original", "PAN :", PAN,
                 "Acknowledgement Number :", "Filed On :", "Aug 29, 2026"]
        self.assertEqual(extract_everify_picker_cards(lines), [])

    def test_the_heading_and_count_identify_the_picker_and_nothing_else_does(self):
        self.assertTrue(is_everify_picker_page("\n".join(picker(card()))))
        self.assertEqual(extract_everify_picker_count("\n".join(picker(card(), card(ack=ACK_2)))), 2)
        for other in ("Enter the 6-digit OTP received on your mobile", "Return e-Verified Successfully",
                      "Select Method For Return Verification", "e-Verify Return"):
            self.assertFalse(is_everify_picker_page(other), other)

    def test_updated_return_filing_type_wording_is_normalised(self):
        got = extract_everify_picker_cards(picker(card(ftype="Updated Return u/s 139(8A)")))
        self.assertEqual(got[0]["filing_preference"], "Updated")


class TestCrosshairDefinition(unittest.TestCase):
    def test_the_picker_crosshair_exists_and_is_a_submission_crosshair(self):
        c = get_crosshair("itr_everify_pending")
        self.assertIsNotNone(c)
        self.assertEqual(c.protocol, "Income Tax")
        self.assertTrue(c.is_terminal_submission)

    def test_the_shared_wizard_url_still_routes_to_the_wizard_not_to_the_picker(self):
        self.assertEqual(match_url_crosshair(URL).id, "itr_everify_return")

    def test_no_url_can_ever_route_to_the_picker_crosshair(self):
        # It is reached by content only. A URL pattern would be a guess at portal routes, and a
        # wrong guess would pull the OTP / confirmation steps out of the wizard handler.
        base = "https://eportal.incometax.gov.in/iec/foservices/#/dashboard/"
        urls = [base + t for t in ("eVerifyReturn-select", "eVerifyReturn/picker", "everify_picker", "eVerifyReturn-pending",
                                   "eVerifyReturn-list", "eVerifyReturn/eVerifyReturn-al")]
        urls.append("C:/x/tests/test_page_everify_picker.html#/a")
        for u in urls:
            c = match_url_crosshair(u)
            self.assertTrue(c is None or c.id != "itr_everify_pending", u)


class Harness:
    def __init__(self, name="ARJUN VERMA", pan=PAN):
        self.ocr = MagicMock()
        self.ocr.capture_window_image.return_value = Image.new("RGB", (1366, 768), (250, 250, 250))
        self.ocr.scan_image.return_value = {"text": "", "lines": [], "words": []}
        self.events = []
        self.assembler = VisualSessionAssembler()
        self.assembler.portal = "Income Tax"
        if pan:
            self.assembler.client_pan = pan
        if name:
            self.assembler.client_name = name
        self.router = VSDCRouter(ocr_engine=self.ocr, assembler=self.assembler,
                                 on_activity=lambda *a: self.events.append((a, dict(self.router.activity_context))))
        self.router.get_foreground_info = MagicMock(return_value=(4242, TITLE, "chrome.exe"))
        self.router.extract_browser_url = MagicMock(return_value=URL)
        self.uia_lines = []

    def tick(self, uia_lines=None, ocr_lines=None):
        """One tick over a screen. UIA and OCR are given separately, like the real two channels."""
        self.uia_lines = list(uia_lines or [])
        text = "\n".join(self.uia_lines)
        self.ocr.scan_image.return_value = {"text": "\n".join(ocr_lines or []), "lines": list(ocr_lines or []), "words": []}
        r = self.router
        r.route_captured = False
        r.last_screen_hash = None
        r.static_since = None
        uia = {"text": text, "lines": self.uia_lines}
        with patch("core.vsdc.vsdc_router.vsdc_uia_text.is_available", return_value=bool(self.uia_lines)), \
                patch("core.vsdc.vsdc_router.vsdc_uia_text.read_page_text", return_value=uia):
            return r.evaluate_tick()

    def run(self, uia_lines, ticks=4, **kw):
        out = []
        for _ in range(ticks):
            p = self.tick(uia_lines, **kw)
            if p:
                out.append(p)
        return out


def capture(p):
    return p["raw_payload"]["assembler_captures"][0]


class TestPickerCapture(unittest.TestCase):
    def test_one_pending_return_is_captured_with_everything_the_card_shows(self):
        h = Harness()
        got = h.run(picker(card()))
        self.assertEqual(len(got), 1)
        p = got[0]
        self.assertEqual(p["status"], "Submitted (Not e-Verified)")     # the tracker shows "e-verification pending"
        self.assertEqual((p["pan"], p["arn"], p["filing_type"], p["period_label"]), (PAN, ACK_1, "ITR-4", "AY 2026-27"))
        self.assertEqual(p["filing_preference"], "Original")
        self.assertEqual(p["client_name"], "ARJUN VERMA")
        self.assertEqual(p["capture_method"], "VSDC-X_itr_everify_pending")
        self.assertEqual(p["raw_payload"]["source"]["engine"], "VSDC-X")
        self.assertTrue(capture(p)["filing_date"].startswith("2026-08-29"), "the portal's own filed-on date, not the capture time")

    def test_the_hud_shows_what_was_captured(self):
        h = Harness()
        h.run(picker(card(form="ITR 4", ftype="Revised")))
        captures = [(a, ctx) for a, ctx in h.events if a[0] == "capture"]
        self.assertEqual(len(captures), 1)
        (_, title, sub), ctx = captures[0]
        self.assertIn("ITR-4", title)
        self.assertIn("AY 2026-27", title)
        self.assertIn("Pending e-Verify", sub)
        self.assertEqual((ctx["form"], ctx["filing_pref"], ctx["period"]), ("ITR-4", "Revised", "AY 2026-27"))

    def test_the_same_page_read_again_is_never_sent_twice(self):
        h = Harness()
        self.assertEqual(len(h.run(picker(card()), ticks=2)), 1)
        self.assertEqual(h.run(picker(card()), ticks=6), [])

    def test_selection_is_cleared_afterwards_so_nothing_leaks_into_the_next_page(self):
        h = Harness()
        h.run(picker(card(ftype="Revised")))
        a = h.assembler
        self.assertFalse(a.current_filing_type or a.current_period_label or a.filing_preference)

    def test_the_route_is_not_latched_so_the_otp_and_confirmation_steps_are_still_read(self):
        h = Harness()
        h.run(picker(card()))
        # ...the next screen on the same URL is the confirmation; it must still promote the return.
        confirmation = STEPPER + ["Return e-Verified Successfully",
                                  "Your ITR-4 Assessment Year 2026-27 has been successfully e-verified",
                                  "Transaction ID: EVERIFY000944284493"]
        got = h.run([], ocr_lines=confirmation, ticks=3)
        self.assertTrue(got, "the confirmation after the picker must still be captured")
        self.assertEqual(got[0]["status"], "Submitted (e-Verified)")
        self.assertEqual(got[0]["arn"], ACK_1)

    def test_the_otp_step_captures_nothing(self):
        h = Harness()
        otp = STEPPER + ["Aadhaar OTP", "EVC through Net Banking", "Enter the 6-digit OTP received on your mobile"]
        self.assertEqual(h.run(otp), [])
        self.assertEqual(h.assembler.records, {})


class TestSeveralReturns(unittest.TestCase):
    def test_each_return_is_sent_on_its_own_with_its_own_filing_type_and_date(self):
        h = Harness()
        got = h.run(picker(card(form="ITR 3", ftype="Revised", ack=ACK_2, filed="Sep 02, 2026"),
                           card(ay="2025-26", form="ITR 4", ftype="Belated", ack=ACK_3, filed="Jul 30, 2026")), ticks=6)
        self.assertEqual([(p["arn"], p["filing_type"], p["period_label"], p["filing_preference"]) for p in got],
                         [(ACK_2, "ITR-3", "AY 2026-27", "Revised"), (ACK_3, "ITR-4", "AY 2025-26", "Belated")])
        self.assertTrue(capture(got[0])["filing_date"].startswith("2026-09-02"))
        self.assertTrue(capture(got[1])["filing_date"].startswith("2026-07-30"))

    def test_two_returns_for_the_same_form_and_year_do_not_overwrite_each_other(self):
        h = Harness()
        got = h.run(picker(card(ftype="Original", ack=ACK_1), card(ftype="Revised", ack=ACK_2, filed="Sep 02, 2026")), ticks=6)
        self.assertEqual([(p["arn"], p["filing_preference"]) for p in got], [(ACK_1, "Original"), (ACK_2, "Revised")])

    def test_a_half_rendered_list_is_waited_for_then_captured(self):
        h = Harness()
        half = head(2) + card()                    # says 2 returns, only one drawn so far
        half = list(STEPPER) + half
        self.assertEqual(h.run(half, ticks=8), [], "waits while the list may still be rendering")
        full = picker(card(), card(ack=ACK_2, ftype="Revised"))
        got = h.run(full, ticks=6)
        self.assertEqual({p["arn"] for p in got}, {ACK_1, ACK_2})

    def test_a_list_that_never_completes_is_captured_as_far_as_it_could_be_read(self):
        h = Harness()
        half = list(STEPPER) + head(2) + card()
        got = h.run(half, ticks=14)
        self.assertEqual([p["arn"] for p in got], [ACK_1])


class TestWhoTheCardBelongsTo(unittest.TestCase):
    def test_a_card_for_a_different_pan_is_never_attributed_to_the_client_in_the_window(self):
        # Called directly: the router's own identity read runs first in a real tick and would
        # already have adopted the page's PAN, so this guard is the last line of defence.
        h = Harness(pan="BJYPM4326D", name="SOMEONE ELSE")
        lines = picker(card(pan=PAN))
        self.assertIsNone(h.router._capture_everify_picker(chr(10).join(lines), lines, "", []))
        self.assertEqual(h.assembler.records, {})

    def test_the_pan_on_the_card_identifies_the_client_when_none_is_known_yet(self):
        h = Harness(pan=None)
        h.assembler.client_name = "ARJUN VERMA"
        got = h.run(picker(card()))
        self.assertEqual([(p["pan"], p["arn"]) for p in got], [(PAN, ACK_1)])

    def test_without_the_clients_name_nothing_is_sent_until_it_is_known(self):
        h = Harness(name=None)
        self.assertEqual(h.run(picker(card()), ticks=4), [])
        h.assembler.client_name = "ARJUN VERMA"
        got = h.run(picker(card()), ticks=3)
        self.assertEqual([p["arn"] for p in got], [ACK_1])


class TestNamesAreNotTakenFromPageChrome(unittest.TestCase):
    """UI Automation names controls by what they are; none of that is a client name."""

    def test_no_button_or_label_on_the_picker_becomes_a_client_name(self):
        from core.vsdc.vsdc_name_parser import extract_name_from_ocr_lines
        self.assertIsNone(extract_name_from_ocr_lines(picker(card())))

    def test_the_headers_real_name_is_still_found(self):
        from core.vsdc.vsdc_name_parser import extract_name_from_ocr_lines
        self.assertEqual(extract_name_from_ocr_lines(["ARJUN VERMA", "Individual"] + picker(card())), "ARJUN VERMA")

    def test_control_descriptions_are_never_names(self):
        from core.vsdc.vsdc_name_parser import extract_name_from_ocr_lines
        for line in ("Search Box Input Field", "Text Box Input", "Password Input Field", "Select Dropdown Menu"):
            self.assertIsNone(extract_name_from_ocr_lines([line, "Individual"]), line)

    def test_the_letters_of_a_pan_are_never_a_client_name(self):
        # Stripped of its digits a PAN looks like a name ("ABCPD5678E" -> "ABCPD E"); it once did.
        from core.vsdc.vsdc_name_parser import extract_name_from_ocr_lines
        for pan in ("ABCPD5678E", "XYZPL9876M", "QRSTP1234K", PAN):
            self.assertIsNone(extract_name_from_ocr_lines(picker(card(pan=pan))), pan)
            self.assertIsNone(extract_name_from_ocr_lines([pan]), pan)

    def test_a_pan_still_anchors_a_real_name_next_to_it(self):
        from core.vsdc.vsdc_name_parser import extract_name_from_ocr_lines
        self.assertEqual(extract_name_from_ocr_lines(["ARJUN VERMA (ABCPD5678E)"]), "ARJUN VERMA")
        self.assertEqual(extract_name_from_ocr_lines(["ARJUN VERMA", "ABCPD5678E"]), "ARJUN VERMA")

    def test_firm_names_that_merely_contain_such_words_are_kept(self):
        from core.vsdc.vsdc_name_parser import extract_name_from_ocr_lines
        self.assertEqual(extract_name_from_ocr_lines(["Legal Name", "BOX INDUSTRIES PRIVATE LIMITED"]),
                         "BOX INDUSTRIES PRIVATE LIMITED")


class TestOcrFallback(unittest.TestCase):
    def test_when_ui_automation_returns_nothing_the_screen_is_read_by_ocr(self):
        h = Harness()
        ocr = list(STEPPER) + head(1) + ["Assessment Year 2026-27", "ITR 4", "Filing Type Original", f"PAN : {PAN}",
                                          f"Acknowledgement Number : {ACK_1}", "Filed On : Aug 29, 2026"]
        got = h.run([], ocr_lines=ocr, ticks=4)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["capture_method"], "VSDC_itr_everify_pending")
        self.assertEqual((got[0]["arn"], got[0]["filing_type"]), (ACK_1, "ITR-4"))


class TestNothingElseChanged(unittest.TestCase):
    def test_a_different_page_on_another_route_is_not_treated_as_the_picker(self):
        h = Harness()
        h.router.extract_browser_url.return_value = "https://eportal.incometax.gov.in/iec/foservices/#/dashboard"
        self.assertEqual(h.run(picker(card())), [])


if __name__ == "__main__":
    unittest.main()
