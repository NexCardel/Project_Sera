"""
tests/test_vsdc247_verify_fixtures.py - VSDC247-V on SYNTHETIC fixtures shaped like the owner's real GST pages.

The fixtures in tests/fixtures/vsdc247_verify are placeholders (see docs/vsdc247-verify-wp0-fixtures.md).
The first group pins what the SHARED ARN reader (vsdc247.analyze_frame) does with them, so a change to the
shared engine shows up here. The second group checks what the verifier does with them.
"""

import json
import unittest
from datetime import date
from pathlib import Path

from core.vsdc.vsdc247 import CERTAIN_SCORE, analyze_frame
from core.vsdc.vsdc247_verify import Verifier, read_filing_period

TODAY = date(2026, 10, 17)
FIX = Path(__file__).parent / "fixtures" / "vsdc247_verify"
GSTR1_ARN = "AB000000000000U"       # placeholder, same shape as the real GSTR-1 ARN (AB + 12 digits + letter)
GSTR3B_ARN = "AB0000000000000"      # placeholder, same shape as the real GSTR-3B ARN (AB + 13 digits)


def _load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _read(rec):
    lines = rec["lines"]
    return analyze_frame("\n".join(lines), lines, [], [], "GST Portal", today=TODAY)


class SharedReaderOnFixtures(unittest.TestCase):

    def test_every_fixture_is_marked_synthetic_and_uses_placeholders(self):
        for path in sorted(FIX.glob("*.json")):
            rec = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(rec.get("synthetic"), path.name)
            self.assertNotIn("29ABACD1191FAZK", "\n".join(rec["lines"]), path.name)

    def test_login_frames_give_no_arn_reading(self):
        for name in ("gst_login_01.json", "gst_login_02.json"):
            self.assertIsNone(_read(_load(name)), name)

    def test_negative_pages_give_no_reading(self):
        for name in ("gstr1_list_negative_01.json", "gstr3b_list_negative_01.json",
                     "gstr3b_prefiling_negative_01.json"):
            self.assertIsNone(_read(_load(name)), name)

    def test_success_frames_find_the_placeholder_arn(self):
        cases = (("gstr1_submit_success_01.json", GSTR1_ARN, "GSTR-1"),
                 ("gstr1_submit_success_02.json", GSTR1_ARN, "GSTR-1"),
                 ("gstr3b_submit_success_01.json", GSTR3B_ARN, "GSTR-3B"))
        for name, arn, form in cases:
            det = _read(_load(name))
            self.assertIsNotNone(det, name)
            self.assertEqual(det.identifier, arn, name)
            self.assertEqual(det.kind, "gst_arn", name)
            self.assertTrue(det.has_success_wording, name)
            # The real banner says "GSTR1" with no hyphen; compare without the hyphen.
            self.assertEqual(det.form.replace("-", ""), form.replace("-", ""), name)
            self.assertTrue(det.usual_shape, name)

    def test_shared_reader_is_certain_on_the_real_wording_shapes(self):
        # Corrects the WP0 note: with the wording the portal actually shows, the shared reader gives
        # CERTAIN (85) on all three success frames. The earlier PROBABLE came from invented wording.
        for name in ("gstr1_submit_success_01.json", "gstr1_submit_success_02.json",
                     "gstr3b_submit_success_01.json"):
            det = _read(_load(name))
            self.assertEqual(det.tier, "certain", name)
            self.assertGreaterEqual(det.score, CERTAIN_SCORE, name)
        # The shared period drops the year on the 3B wording ("Jul-Sep"); the verifier keeps it.
        self.assertEqual(_read(_load("gstr3b_submit_success_01.json")).period, "Jul-Sep")

    def test_two_arns_on_one_frame_is_a_list_not_a_submission(self):
        rec = _load("gstr1_submit_success_01.json")
        rec["lines"] = rec["lines"] + ["Acknowledgment Reference Number (ARN): AB0000000000001"]
        self.assertEqual(_read(rec).tier, "vetoed")

    def test_future_tense_wording_is_vetoed(self):
        rec = _load("gstr1_submit_success_01.json")
        rec["lines"] = ["GSTR1 of GSTIN - 00AAAAA0000A1Z0 for the Return Period - 'September - 2026-27 "
                        "will be successfully filed once you submit. The Acknowledgment Reference Number",
                        "is AB000000000000U ."]
        self.assertEqual(_read(rec).tier, "vetoed")


class PeriodReader(unittest.TestCase):

    def test_reads_the_period_wording_the_portal_uses(self):
        self.assertEqual(read_filing_period(["GSTR-3B of GSTIN 00AAAAA0000A1Z0 for the period Jul-Sep - 2026 has been"]),
                         "Jul-Sep - 2026")
        self.assertEqual(read_filing_period(["GSTR1 of GSTIN for the Return Period - 'September - 2026-27 has been"]),
                         "September - 2026-27")
        self.assertEqual(read_filing_period(["Return Period - September(Q)"]), "September(Q)")

    def test_never_takes_a_plain_verb_as_a_period(self):
        self.assertIsNone(read_filing_period(["the period has been noted", "Status - Filed"]))

    def test_no_period_stated_gives_none(self):
        self.assertIsNone(read_filing_period(["GSTR-1 has been successfully filed"]))


if __name__ == "__main__":
    unittest.main()
