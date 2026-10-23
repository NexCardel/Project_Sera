"""
tests/test_vsdc247_verify_ledger.py - VSDC247-V WP1 (page registry, login username, window link) and
WP2 (ARN observation, two-read rule, ledger file). Synthetic fixtures only.
"""

import json
import os
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from core.vsdc.vsdc247_verify import LINK_IDLE_SEC, Verifier, _norm_form, lookup_client
from core.vsdc.vsdc247_verify_pages import classify_page, read_login_username

FIX = Path(__file__).parent / "fixtures" / "vsdc247_verify"
TODAY = date(2026, 10, 17)
GSTIN = "00AAAAA0000A1Z0"
OTHER_GSTIN = "99ZZZZZ9999Z1Z9"

_LOCAL_TEST_ENV = "VSDC_ALLOW_LOCAL_TEST"
_saved_local_test = None


def setUpModule():
    """These tests check exact host/path registration, so local-test URL matching must be off."""
    global _saved_local_test
    _saved_local_test = os.environ.pop(_LOCAL_TEST_ENV, None)


def tearDownModule():
    if _saved_local_test is not None:
        os.environ[_LOCAL_TEST_ENV] = _saved_local_test


def _load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class Sink:
    def __init__(self):
        self.rows = []

    def __call__(self, entry):
        self.rows.append(entry)
        return True


def _verifier(clock=None, lookup=None):
    sink = Sink()
    return Verifier(client_lookup=lookup, sink=sink, clock=clock or Clock()), sink


def _observe(v, hwnd, name, lines=None):
    rec = _load(name)
    return v.observe_frame(hwnd, rec["url"], lines if lines is not None else rec["lines"], today=TODAY)


# ── WP1: page registry ───────────────────────────────────────────────────────────
class PageRegistry(unittest.TestCase):

    def test_the_three_real_paths_are_registered_with_their_cues(self):
        self.assertEqual(classify_page("services.gst.gov.in/services/login", _load("gst_login_01.json")["lines"]).kind,
                         "gst_login")
        self.assertEqual(classify_page("https://return.gst.gov.in/returns/auth/file",
                                       _load("gstr1_submit_success_01.json")["lines"]).kind, "gstr1_submit")
        self.assertEqual(classify_page("return.gst.gov.in/returns/auth/gstr3b/filing",
                                       _load("gstr3b_submit_success_01.json")["lines"]).kind, "gstr3b_submit")

    def test_the_url_alone_does_not_register_a_page(self):
        # Right path, but the frame does not show the GSTR-1 cue.
        self.assertIsNone(classify_page("return.gst.gov.in/returns/auth/file", ["Dashboard", "Returns"]))
        # The login path without the username and password cues.
        self.assertIsNone(classify_page("services.gst.gov.in/services/login", ["Welcome"]))

    def test_list_and_other_gst_pages_are_not_registered(self):
        self.assertIsNone(classify_page("return.gst.gov.in/returns/auth/gstr3b",
                                        _load("gstr3b_list_negative_01.json")["lines"]))
        self.assertIsNone(classify_page("services.gst.gov.in/services/auth/dashboard", ["GSTR-1"]))

    def test_look_alike_hosts_and_extra_path_segments_are_refused(self):
        lines = _load("gstr1_submit_success_01.json")["lines"]
        for url in ("return.gst.gov.in.example.com/returns/auth/file",
                    "return-gst.gov.in/returns/auth/file",
                    "evil.example/return.gst.gov.in/returns/auth/file",
                    "return.gst.gov.in/returns/auth/file/extra"):
            self.assertIsNone(classify_page(url, lines), url)


# ── WP1: login username ──────────────────────────────────────────────────────────
class LoginUsername(unittest.TestCase):

    def test_empty_field_gives_none_not_the_placeholder(self):
        self.assertIsNone(read_login_username(_load("gst_login_01.json")["lines"]))

    def test_typed_value_on_the_line_below_the_label(self):
        self.assertEqual(read_login_username(_load("gst_login_02.json")["lines"]), "EXAMPLEUSER01")

    def test_value_on_the_label_line(self):
        self.assertEqual(read_login_username(["Username EXAMPLEUSER01", "Password*"]), "EXAMPLEUSER01")

    def test_extension_label_and_marks_are_ignored(self):
        self.assertEqual(read_login_username(["? Username Injected", "EXAMPLEUSER01", "Password"]), "EXAMPLEUSER01")
        self.assertEqual(read_login_username(["Username ?", "EXAMPLEUSER01"]), "EXAMPLEUSER01")

    def test_forgot_username_link_is_not_a_field(self):
        self.assertIsNone(read_login_username(["Forgot Username", "Forgot Password"]))

    def test_a_password_value_is_never_taken_as_the_username(self):
        # The username field is empty; the next field is the password. Nothing may be returned.
        self.assertIsNone(read_login_username(["Username*", "Password*", "Hunter2secret"]))
        self.assertIsNone(read_login_username(["Password*", "Hunter2secret"]))


# ── WP2: observation and the two-read ledger ─────────────────────────────────────
class FilingObservation(unittest.TestCase):

    def test_gstr1_needs_a_second_frame_then_is_saved_once(self):
        v, sink = _verifier()
        self.assertEqual(_observe(v, 1, "gstr1_submit_success_01.json")["status"], "pending_second_read")
        self.assertEqual(sink.rows, [])
        res = _observe(v, 1, "gstr1_submit_success_02.json")
        self.assertEqual(res["status"], "saved")
        self.assertTrue(res["written"])
        self.assertEqual(len(sink.rows), 1)
        row = sink.rows[0]
        self.assertEqual((row["arn"], row["form"], row["period"]), (GSTR1_ARN, "GSTR-1", "September - 2026-27"))
        self.assertEqual(row["confirmed_by"], "two_reads")
        self.assertEqual(_observe(v, 1, "gstr1_submit_success_02.json")["status"], "duplicate")
        self.assertEqual(len(sink.rows), 1)

    def test_the_same_frame_read_twice_is_still_one_read(self):
        v, sink = _verifier()
        _observe(v, 1, "gstr3b_submit_success_01.json")
        self.assertEqual(_observe(v, 1, "gstr3b_submit_success_01.json")["status"], "pending_second_read")
        self.assertEqual(sink.rows, [])

    def test_gstr3b_saves_on_a_second_different_frame_with_its_period(self):
        v, sink = _verifier()
        rec = _load("gstr3b_submit_success_01.json")
        _observe(v, 1, "gstr3b_submit_success_01.json")
        second = rec["lines"] + ["Dashboard"]
        res = _observe(v, 1, "gstr3b_submit_success_01.json", lines=second)
        self.assertEqual(res["status"], "saved")
        self.assertEqual((sink.rows[0]["form"], sink.rows[0]["period"]), ("GSTR-3B", "Jul-Sep - 2026"))

    def test_pre_filing_pages_give_no_reading_and_no_row(self):
        v, sink = _verifier()
        for name in ("gstr1_list_negative_01.json", "gstr3b_prefiling_negative_01.json"):
            self.assertEqual(_observe(v, 1, name)["status"], "no_reading", name)
        self.assertEqual(sink.rows, [])

    def test_unregistered_page_is_not_read_at_all(self):
        v, sink = _verifier()
        self.assertEqual(_observe(v, 1, "gstr3b_list_negative_01.json")["status"], "not_registered")
        self.assertEqual(sink.rows, [])

    def test_a_filing_wording_for_another_form_is_a_conflict_not_a_row(self):
        v, sink = _verifier()
        lines = ["GSTR-3B of GSTIN 00AAAAA0000A1Z0 for the period Jul-Sep - 2026 has been successfully filed.",
                 "The Acknowledgment Reference Number is AB0000000000000.",
                 "", "", "", "", "", "", "", "", "Returns Filing for GST GSTR1"]
        first = v.observe_frame(1, "return.gst.gov.in/returns/auth/file", lines, today=TODAY)
        second = v.observe_frame(1, "return.gst.gov.in/returns/auth/file", lines + ["x"], today=TODAY)
        self.assertEqual(first["status"], "form_conflict")
        self.assertEqual(second["status"], "form_conflict")
        self.assertEqual(sink.rows, [])


# ── WP1: window -> client link ───────────────────────────────────────────────────
class WindowLink(unittest.TestCase):

    def _login_twice(self, v, hwnd, user_lines=None):
        lines = user_lines or _load("gst_login_02.json")["lines"]
        first = _observe(v, hwnd, "gst_login_02.json", lines=lines)
        second = _observe(v, hwnd, "gst_login_02.json", lines=lines)
        return first, second

    def test_login_is_linked_only_after_two_frames_agree(self):
        v, _ = _verifier()
        first, second = self._login_twice(v, 1)
        self.assertEqual(first["status"], "login_seen")
        self.assertEqual(second["status"], "login_linked")

    def test_the_linked_username_reaches_the_ledger_entry_with_page_identity(self):
        lookup = lambda portal, user: {"client_id": 7, "gstin": GSTIN, "pan": GSTIN[2:12]} if user == "EXAMPLEUSER01" else None
        v, sink = _verifier(lookup=lookup)
        self._login_twice(v, 1)
        _observe(v, 1, "gstr1_submit_success_01.json")
        _observe(v, 1, "gstr1_submit_success_02.json")
        client = sink.rows[0]["client"]
        self.assertEqual((client["username"], client["client_id"]), ("EXAMPLEUSER01", 7))
        self.assertEqual((client["link"], client["agreement"]), ("page", "agrees"))

    def test_page_gstin_that_contradicts_the_linked_client_is_a_conflict(self):
        lookup = lambda portal, user: {"client_id": 7, "gstin": OTHER_GSTIN, "pan": OTHER_GSTIN[2:12]}
        v, sink = _verifier(lookup=lookup)
        self._login_twice(v, 1)
        _observe(v, 1, "gstr1_submit_success_01.json")
        _observe(v, 1, "gstr1_submit_success_02.json")
        self.assertEqual(sink.rows[0]["client"]["agreement"], "conflict")

    def test_no_client_match_or_no_login_is_unknown_never_a_guess(self):
        v, sink = _verifier(lookup=lambda portal, user: None)
        self._login_twice(v, 1)
        _observe(v, 1, "gstr1_submit_success_01.json")
        _observe(v, 1, "gstr1_submit_success_02.json")
        self.assertEqual(sink.rows[0]["client"]["agreement"], "unknown")
        self.assertIsNone(sink.rows[0]["client"]["client_id"])
        self.assertEqual(sink.rows[0]["client"]["link"], "window")

        v2, sink2 = _verifier()
        _observe(v2, 9, "gstr1_submit_success_01.json")
        _observe(v2, 9, "gstr1_submit_success_02.json")
        self.assertIsNone(sink2.rows[0]["client"]["username"])

    def test_page_gstin_is_kept_when_no_username_is_readable(self):
        v, sink = _verifier()
        _observe(v, 9, "gstr1_submit_success_01.json")
        _observe(v, 9, "gstr1_submit_success_02.json")
        self.assertEqual(sink.rows[0]["client"]["page_gstin"], GSTIN)

    def test_link_belongs_to_its_window_only(self):
        v, sink = _verifier()
        self._login_twice(v, 1)
        _observe(v, 2, "gstr1_submit_success_01.json")
        _observe(v, 2, "gstr1_submit_success_02.json")
        self.assertIsNone(sink.rows[0]["client"]["username"])

    def test_link_expires_after_four_idle_hours(self):
        clock = Clock()
        v, sink = _verifier(clock=clock)
        self._login_twice(v, 1)
        clock.t += LINK_IDLE_SEC + 1
        _observe(v, 1, "gstr1_submit_success_01.json")
        _observe(v, 1, "gstr1_submit_success_02.json")
        self.assertIsNone(sink.rows[0]["client"]["username"])

    def test_an_empty_login_page_drops_the_old_link(self):
        v, sink = _verifier()
        self._login_twice(v, 1)
        _observe(v, 1, "gst_login_01.json")
        _observe(v, 1, "gstr1_submit_success_01.json")
        _observe(v, 1, "gstr1_submit_success_02.json")
        self.assertIsNone(sink.rows[0]["client"]["username"])


# ── WP2: the ledger file ─────────────────────────────────────────────────────────
class LedgerFile(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="vsdc247v_")
        self.path = Path(self.dir) / "logs" / "vsdc247_verify.jsonl"

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_default_sink_writes_one_structured_line_and_no_page_text(self):
        with patch.dict(os.environ, {"VSDC247_VERIFY_LOG": str(self.path)}):
            v = Verifier(clock=Clock())
        _observe(v, 1, "gstr1_submit_success_01.json")
        _observe(v, 1, "gstr1_submit_success_02.json")
        text = self.path.read_text(encoding="utf-8")
        lines = [ln for ln in text.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1)
        row = json.loads(lines[0])
        self.assertEqual(row["arn"], GSTR1_ARN)
        for key in ("lines", "text", "password", "Hunter2secret"):
            self.assertNotIn(key, row)
        self.assertNotIn("Dashboard Login", text)     # no sentence from the page
        self.assertNotIn("Email ID", text)

    def test_a_password_seen_on_the_login_page_is_never_kept(self):
        with patch.dict(os.environ, {"VSDC247_VERIFY_LOG": str(self.path)}):
            v = Verifier(clock=Clock())
        leaky = ["Username*", "EXAMPLEUSER01", "Password*", "Hunter2secret", "LOGIN"]
        _observe(v, 1, "gst_login_02.json", lines=leaky)
        _observe(v, 1, "gst_login_02.json", lines=leaky)
        self.assertNotIn("Hunter2secret", repr(v._links.__dict__))
        self.assertFalse(self.path.exists())          # login frames never write a row

    def test_a_write_error_does_not_lose_the_in_memory_entry(self):
        v = Verifier(clock=Clock(), sink=lambda entry: False)
        _observe(v, 1, "gstr1_submit_success_01.json")
        res = _observe(v, 1, "gstr1_submit_success_02.json")
        self.assertEqual(res["status"], "saved")
        self.assertFalse(res["written"])
        self.assertIn(GSTR1_ARN, v.saved)


class RealOcrTolerance(unittest.TestCase):
    """Frames taken from REAL Windows OCR output of the rendered synthetic pages (2026-10-18). These are the
    cases the text-only fixtures could not show: GSTR1 read as GSTRI, 0 read as O, EXAMPLEUSER01 read as
    EXAMPLEUSEROI."""

    def test_ocr_gstri_still_registers_the_gstr1_page(self):
        spec = classify_page("return.gst.gov.in/returns/auth/file", _load("gstr1_ocr_real_01.json")["lines"])
        self.assertIsNotNone(spec)
        self.assertEqual(spec.kind, "gstr1_submit")

    def test_form_readings_normalise(self):
        self.assertEqual(_norm_form("GSTRI"), "GSTR-1")
        self.assertEqual(_norm_form("GSTR1"), "GSTR-1")
        self.assertEqual(_norm_form("GSTR-3BQ"), "GSTR-3B")
        self.assertEqual(_norm_form("GSTR-38"), "GSTR-3B")
        self.assertIsNone(_norm_form("GSTR-9B"))

    def test_real_ocr_gstr1_frames_save_with_the_ocr_variant_client(self):
        db = {"EXAMPLEUSER01": {"client_id": 7}}
        v, sink = _verifier(lookup=lambda portal, user: db.get(user) and {"client_id": 7, "gstin": "", "pan": ""})
        _observe(v, 1, "gst_login_ocr_real_02.json")
        self.assertEqual(_observe(v, 1, "gst_login_ocr_real_02.json")["status"], "login_linked")
        self.assertEqual(_observe(v, 1, "gstr1_ocr_real_01.json")["status"], "pending_second_read")
        res = _observe(v, 1, "gstr1_ocr_real_02.json")
        self.assertEqual(res["status"], "saved")
        row = sink.rows[0]
        self.assertEqual((row["arn"], row["form"], row["period"]),
                         (GSTR1_ARN, "GSTR-1", "September - 2026-27"))
        self.assertEqual(row["client"]["username"], "EXAMPLEUSEROI")
        self.assertEqual((row["client"]["client_id"], row["client"]["match"]), (7, "ocr_variant"))

    def test_ambiguous_variants_never_pick_a_client(self):
        db = {"EXAMPLEUSER01": {"client_id": 7}, "EXAMPLEUSER0I": {"client_id": 8}}
        info, match = lookup_client(lambda p, u: db.get(u), "EXAMPLEUSEROI")
        self.assertIsNone(info)
        self.assertIsNone(match)

    def test_exact_match_is_marked_exact(self):
        info, match = lookup_client(lambda p, u: {"client_id": 7} if u == "EXAMPLEUSEROI" else None, "EXAMPLEUSEROI")
        self.assertEqual((info["client_id"], match), (7, "exact"))

    def test_no_client_at_all_is_none(self):
        self.assertEqual(lookup_client(lambda p, u: None, "EXAMPLEUSEROI"), (None, None))


GSTR1_ARN = "AB000000000000U"


if __name__ == "__main__":
    unittest.main()
