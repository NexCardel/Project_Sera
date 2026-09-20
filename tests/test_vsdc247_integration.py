"""
tests/test_vsdc247_integration.py - VSDC247 inside the router, the assembler, the HUD and
the tracker database.

The pure logic (what counts as a submission) is in test_vsdc247.py; this file is about how it
is wired: when it runs, what it dispatches, what it must never do twice, and what the user sees.
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from PIL import Image

from core.vsdc.vsdc_assembler import VisualSessionAssembler
from core.vsdc.vsdc_router import VSDCRouter


def ack_on(d):
    """A real-shaped ITR ack: nine digits plus the filing date as DDMMYY."""
    return "901036690" + d.strftime("%d%m%y")


TODAY_ACK = ack_on(datetime.now())
OLD_ACK = ack_on(datetime.now() - timedelta(days=200))
GST_ARN = "AA270826000123Z"

ITR_CONFIRMATION = [
    "Your return has been submitted successfully",
    f"Acknowledgement Number : {TODAY_ACK}",
    "ITR-4  Assessment Year 2026-27",
]
GST_CONFIRMATION = ["Your return has been filed successfully", f"ARN : {GST_ARN}", "GSTR-3B  Return Period : June 2026"]

PORTAL_ITR = "https://eportal.incometax.gov.in/iec/foservices/#/some/unmapped/route"
PORTAL_GST = "https://services.gst.gov.in/services/auth/some/unmapped/route"
TITLE_ITR = "Income Tax Portal, Government of India - Google Chrome"
TITLE_GST = "Goods and Services Tax - Google Chrome"


class Harness:
    """A router on an in-scope portal page, with a scriptable screen and a controllable clock."""

    def __init__(self, url=PORTAL_ITR, title=TITLE_ITR, assembler=None):
        self.ocr = MagicMock()
        self.ocr.capture_window_image.return_value = Image.new("RGB", (1366, 768), (245, 246, 250))
        self.ocr.scan_image.return_value = {"text": "", "lines": [], "words": []}
        self.events = []
        self.router = VSDCRouter(
            ocr_engine=self.ocr, assembler=assembler or VisualSessionAssembler(),
            on_activity=lambda *a: self.events.append(a),
        )
        self.router.get_foreground_info = MagicMock(return_value=(4242, title, "chrome.exe"))
        self.router.extract_browser_url = MagicMock(return_value=url)
        # Starts at the real time: patching time.time also moves date.today(), and an ack is
        # judged against today's date, so a clock stuck in 1970 would call every ack "old".
        self.clock = [datetime.now().timestamp()]
        self._shade = 245

    def screen(self, lines, shade=None):
        """Shows a new screen: a visibly different frame whose OCR yields `lines`."""
        self._shade = shade if shade is not None else self._shade - 7
        self.ocr.capture_window_image.return_value = Image.new("RGB", (1366, 768), (self._shade,) * 3)
        self.ocr.scan_image.return_value = {"text": "\n".join(lines), "lines": list(lines), "words": []}

    def tick(self, advance=0.5):
        self.clock[0] += advance
        with patch("core.vsdc.vsdc_router.time.time", side_effect=lambda: self.clock[0]):
            return self.router.evaluate_tick()

    def run(self, ticks=3):
        out = []
        for _ in range(ticks):
            r = self.tick()
            if r:
                out.append(r)
        return out

    def kinds(self):
        return [e[0] for e in self.events]


class TestRunsOnPagesWithNoCrosshair(unittest.TestCase):
    def test_a_confirmation_on_an_unmapped_route_is_captured(self):
        h = Harness()
        h.screen(["Dashboard"], shade=245)
        h.tick()                                   # baseline frame
        h.screen(ITR_CONFIRMATION)
        got = h.run()
        self.assertEqual(len(got), 1)
        p = got[0]
        self.assertEqual((p["arn"], p["filing_type"], p["period_label"]), (TODAY_ACK, "ITR-4", "AY 2026-27"))
        self.assertEqual(p["capture_method"], "VSDC247_itr_ack")
        self.assertEqual(p["raw_payload"]["source"]["engine"], "VSDC247")
        self.assertEqual(p["raw_text"], "")

    def test_the_same_filing_is_never_sent_twice(self):
        h = Harness()
        h.screen(["x"], shade=245)
        h.tick()
        h.screen(ITR_CONFIRMATION)
        self.assertEqual(len(h.run()), 1)
        h.screen(ITR_CONFIRMATION)                 # the page repaints; the message is still there
        self.assertEqual(h.run(4), [])

    def test_gst_arn_is_captured_too_with_the_portal_read_from_the_host(self):
        h = Harness(url=PORTAL_GST, title=TITLE_GST)
        h.screen(["Returns"], shade=245)
        h.tick()
        h.screen(GST_CONFIRMATION)
        got = h.run(4)
        self.assertTrue(got)
        self.assertEqual((got[0]["arn"], got[0]["portal"], got[0]["filing_type"]), (GST_ARN, "GST Portal", "GSTR-3B"))

    def test_an_ordinary_page_costs_nothing_and_says_nothing(self):
        h = Harness()
        h.screen(["Dashboard"], shade=245)
        for _ in range(6):
            h.tick()
        # One read when the window is first seen, then a static page costs nothing more.
        self.assertEqual(h.ocr.scan_image.call_count, 1)
        self.assertEqual(h.events, [])

    def test_the_title_alone_no_longer_routes_an_unmapped_page_to_a_crosshair(self):
        # A readable URL that matches no crosshair is an unmapped page. Matching its
        # "...Dashboard" title anyway routed it to the landing handler.
        h = Harness(title="Dashboard, Income Tax Portal, Government of India - Google Chrome")
        h.screen(["WASIL AMAN MANDAL", "ABCPE1234F"], shade=245)
        h.run(3)
        self.assertIsNone(h.router.assembler.client_pan)
        self.assertIsNone(h.router.assembler.client_name)


class TestNeverOutsideThePortals(unittest.TestCase):
    def test_no_screenshot_no_ocr_no_capture_on_any_other_site(self):
        for url in ("https://www.google.com/search?q=incometax+acknowledgement", "https://mail.google.com/mail/u/0/#inbox",
                    "https://netbanking.example.com/receipt"):
            h = Harness(url=url, title="Some page - Google Chrome")
            h.screen(ITR_CONFIRMATION, shade=245)
            self.assertEqual(h.run(4), [], url)
            h.ocr.capture_window_image.assert_not_called()
            h.ocr.scan_image.assert_not_called()


class TestCrosshairPipelineAndVsdc247Agree(unittest.TestCase):
    def test_an_ack_the_crosshair_pipeline_already_dispatched_is_not_dispatched_again(self):
        h = Harness()
        h.router._remember_dispatch({"arn": TODAY_ACK, "raw_payload": {"assembler_captures": []}})
        h.screen(["x"], shade=245)
        h.tick()
        h.screen(ITR_CONFIRMATION)
        self.assertEqual(h.run(4), [])

    def test_a_frame_the_crosshair_pipeline_handled_is_marked_seen_not_re_read(self):
        h = Harness()
        h.screen(["x"], shade=245)
        h.tick()
        h.screen(["x"], shade=200)
        # The crosshair pipeline produces a payload on this changed frame...
        with patch.object(VSDCRouter, "_evaluate_tick_locked",
                          side_effect=lambda *a, **k: (setattr(h.router, "_scope_ok_this_tick", True)
                                                       or h.router._capture_window(4242) and {"arn": "1"})):
            h.tick()
        calls = h.ocr.scan_image.call_count
        # ...so the next tick, on the very same frame, must not spend an OCR pass on it.
        h.tick()
        self.assertEqual(h.ocr.scan_image.call_count, calls)


class TestModes(unittest.TestCase):
    def _capture(self, mode):
        with patch.dict(os.environ, {"VSDC247_MODE": mode}):
            h = Harness()
        h.screen(["x"], shade=245)
        h.tick()
        h.screen(ITR_CONFIRMATION)
        return h, h.run(4)

    def test_live_saves(self):
        self.assertEqual(len(self._capture("live")[1]), 1)

    def test_shadow_observes_but_saves_and_shows_nothing(self):
        h, got = self._capture("shadow")
        self.assertEqual(got, [])
        self.assertEqual(h.events, [])

    def test_off_never_even_looks(self):
        h, got = self._capture("off")
        self.assertEqual(got, [])
        h.ocr.scan_image.assert_not_called()


class TestUiaOnlyIsolation(unittest.TestCase):
    """VSDC_UIA_ONLY exists to isolate VSDC-X; VSDC247 must not mask a VSDC-X failure."""

    def _mode(self, **env):
        base = {k: v for k, v in os.environ.items() if k not in ("VSDC247_MODE", "VSDC_UIA_ONLY")}
        with patch.dict(os.environ, {**base, **env}, clear=True):
            return Harness().router._mode_247

    def test_off_by_default_when_vsdc_x_is_being_isolated(self):
        self.assertEqual(self._mode(VSDC_UIA_ONLY="1"), "off")

    def test_an_explicit_mode_still_wins(self):
        self.assertEqual(self._mode(VSDC_UIA_ONLY="1", VSDC247_MODE="live"), "live")
        self.assertEqual(self._mode(VSDC_UIA_ONLY="1", VSDC247_MODE="shadow"), "shadow")

    def test_live_by_default_in_normal_operation(self):
        self.assertEqual(self._mode(), "live")


class TestAttributionAndHud(unittest.TestCase):
    def test_no_client_known_still_captures_and_prompts(self):
        h = Harness()
        h.screen(["x"], shade=245)
        h.tick()
        h.screen(ITR_CONFIRMATION)
        p = h.run(4)[0]
        self.assertEqual(p["pan"], "")
        self.assertFalse(p["identity_resolved"])
        prompts = [e for e in h.events if e[0] == "prompt"]
        self.assertEqual(len(prompts), 1)
        self.assertIn("client unknown", prompts[0][1])

    def test_the_client_arriving_later_re_sends_the_same_ack_attributed_once(self):
        h = Harness()
        h.screen(["x"], shade=245)
        h.tick()
        h.screen(ITR_CONFIRMATION)
        h.run(3)
        h.router.assembler.update_identity(pan="BJYPM4326D", name="MOHAMMAD MOLLA", portal="Income Tax")
        again = h.run(4)
        self.assertEqual(len(again), 1)
        self.assertEqual((again[0]["pan"], again[0]["arn"]), ("BJYPM4326D", TODAY_ACK))
        self.assertTrue(again[0]["identity_resolved"])
        self.assertEqual(h.run(4), [])

    def test_a_known_client_is_captured_attributed_straight_away(self):
        asm = VisualSessionAssembler()
        asm.update_identity(pan="BJYPM4326D", name="MOHAMMAD MOLLA", portal="Income Tax")
        h = Harness(assembler=asm)
        h.screen(["x"], shade=245)
        h.tick()
        h.screen(ITR_CONFIRMATION)
        p = h.run(4)[0]
        self.assertEqual(p["pan"], "BJYPM4326D")
        self.assertIn("submit", h.kinds())
        self.assertNotIn("prompt", h.kinds())

    def test_a_moderate_candidate_only_prompts_and_prompts_once(self):
        # ARN + wording but nothing else: probable. It must never be saved on one read.
        lines = ["Your return has been filed successfully", f"ARN : {GST_ARN}"]
        h = Harness(url=PORTAL_GST, title=TITLE_GST)
        h.screen(["x"], shade=245)
        h.tick()
        h.screen(lines)
        got = []
        h.events.clear()
        got += h.run(1)
        self.assertEqual(got, [])
        self.assertEqual([e[0] for e in h.events], ["prompt"])
        self.assertIn("not saved", h.events[0][1])
        h.events.clear()
        h.run(1)                                     # the re-read (agreement) may now promote it
        # once promoted it is saved; either way there is no second 'possible' prompt
        self.assertNotIn("Possible submission seen - not saved", [e[1] for e in h.events])


class TestWhereItWasCaptured(unittest.TestCase):
    def test_the_payload_says_which_page_the_submission_was_seen_on(self):
        h = Harness(url=PORTAL_ITR + "?token=SECRET&x=1")
        h.screen(["Dashboard"], shade=245)
        h.tick()
        h.screen(ITR_CONFIRMATION)
        p = h.run()[0]
        self.assertEqual(p["page_url"], PORTAL_ITR)
        self.assertEqual(p["raw_payload"]["vsdc247"]["page_url"], PORTAL_ITR)
        self.assertNotIn("SECRET", json.dumps(p))

    def test_the_attributed_resend_keeps_the_page_the_submission_was_actually_seen_on(self):
        h = Harness()
        h.screen(["Dashboard"], shade=245)
        h.tick()
        h.screen(ITR_CONFIRMATION)
        first = h.run()[0]
        h.router.extract_browser_url.return_value = "https://eportal.incometax.gov.in/iec/foservices/#/dashboard"
        h.router.assembler.update_identity(pan="BJYPM4326D", name="MOHAMMAD MOLLA", portal="Income Tax")
        h.screen(["Dashboard"])
        resend = [r for r in h.run(4)]
        self.assertTrue(resend)
        self.assertEqual(resend[0]["page_url"], first["page_url"])
        self.assertIn("unmapped/route", resend[0]["page_url"])

    def test_an_unreadable_address_bar_gives_an_empty_url_not_the_window_title(self):
        h = Harness(title="Income Tax Portal - Filing - Google Chrome")
        h.screen(["Dashboard"], shade=245)
        h.tick()                                             # address bar readable: window verified
        h.router.extract_browser_url.return_value = None     # then it cannot be read for a moment
        h.screen(ITR_CONFIRMATION)
        got = h.run()
        self.assertEqual(len(got), 1, "still inside the scope grace period, so it is captured")
        self.assertEqual(got[0]["page_url"], "")


class TestPayloadShape(unittest.TestCase):
    def test_the_payload_carries_only_structured_fields(self):
        from datetime import date
        from core.vsdc.vsdc247 import analyze_frame
        det = analyze_frame("\\n".join(ITR_CONFIRMATION), ITR_CONFIRMATION, [], [], "Income Tax")
        asm = VisualSessionAssembler()
        p = asm.build_247_payload(det)
        blob = json.dumps(p)
        self.assertNotIn("Your return has been submitted", blob)
        self.assertEqual(p["raw_payload"]["vsdc247"]["tier"], "certain")
        # never touches the crosshair pipeline's own records
        self.assertEqual(asm.records, {})


class TestTrackerDatabase(unittest.TestCase):
    def setUp(self):
        import security
        from database import SeraDatabase
        self.tmp = tempfile.TemporaryDirectory()
        salt_path = os.path.join(self.tmp.name, "s.salt")
        security.generate_and_save_salt(salt_path)
        key = security.derive_key_hex("pw", security.load_salt(salt_path))
        self.db = SeraDatabase(os.path.join(self.tmp.name, "m.db"), key,
                               raw_db_path=os.path.join(self.tmp.name, "raw.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def _payloads(self):
        from core.vsdc.vsdc247 import analyze_frame
        det = analyze_frame("\\n".join(ITR_CONFIRMATION), ITR_CONFIRMATION, [], [], "Income Tax")
        asm = VisualSessionAssembler()
        asm.portal = "Income Tax"
        orphan = asm.build_247_payload(det)
        asm.update_identity(pan="BJYPM4326D", name="MOHAMMAD MOLLA", portal="Income Tax")
        return orphan, asm.build_247_payload(det)

    def _send(self, p):
        return self.db.insert_tracker_dump(
            client_id=None, portal=f"{p['portal']} ({p['filing_type']})", period_label=p["period_label"],
            arn_number=p["arn"], capture_method=p["capture_method"], status=p["status"],
            raw_payload_json=json.dumps(p), captured_by="test", pan=p["pan"], session_id=p["session_id"],
            filing_type=p["filing_type"], dataset_key=p["raw_payload"]["assembler_captures"][0].get("dataset_key"),
        )

    def _rows(self):
        with self.db._connect_raw() as c:
            return c.execute("SELECT arn_number, unassigned_identity FROM tracker_dump").fetchall()

    def test_an_unattributed_capture_is_stored_as_pending(self):
        orphan, _ = self._payloads()
        self._send(orphan)
        self.assertEqual(self._rows(), [(TODAY_ACK, f"Pending_{TODAY_ACK}")])

    def test_a_capture_with_no_period_anywhere_is_stored_blank_not_guessed(self):
        from core.vsdc.vsdc247 import analyze_frame
        lines = ["You have successfully submitted your return!", f"Acknowledgement Number : {TODAY_ACK}"]
        det = analyze_frame("\n".join(lines), lines, [], [], "Income Tax")
        asm = VisualSessionAssembler()
        asm.portal = "Income Tax"
        orphan = asm.build_247_payload(det)
        self.assertEqual((orphan["period_label"], orphan["fy"]), ("", ""))
        asm.update_identity(pan="BJYPM4326D", name="MOHAMMAD MOLLA", portal="Income Tax")
        attributed = asm.build_247_payload(det)
        self._send(orphan)
        self.assertFalse(self._send(attributed).get("duplicate"))
        rows = self._rows()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0][1], "BJYPM4326D")

    def test_the_attributed_resend_replaces_the_pending_row_instead_of_being_dropped(self):
        orphan, attributed = self._payloads()
        self._send(orphan)
        result = self._send(attributed)          # within the 10s duplicate window on purpose
        self.assertFalse(result.get("duplicate"))
        rows = self._rows()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0][1], "BJYPM4326D")

    def test_resending_the_same_unattributed_capture_is_still_a_duplicate(self):
        orphan, _ = self._payloads()
        self._send(orphan)
        self.assertTrue(self._send(orphan).get("duplicate"))
        self.assertEqual(len(self._rows()), 1)


class TestHudPrompt(unittest.TestCase):
    def test_prompt_theme_and_source_tag(self):
        from ui.components.vsdc_hud_pill import VSDCHudPill
        self.assertEqual(VSDCHudPill.EVENT_THEMES["prompt"][2], "ACTION NEEDED")
        html = VSDCHudPill._format_subtitle_html("ARN: AA270826000123Z • VSDC247 (Visual)")
        self.assertIn("VSDC247 (Visual)", html)
        self.assertIn("#D2A8FF", html)
        # the plain-OCR and VSDC-X tags keep their own colours
        self.assertIn("#8B949E", VSDCHudPill._format_subtitle_html("x • VSDC (Visual)"))
        self.assertIn("#58A6FF", VSDCHudPill._format_subtitle_html("x • VSDC-X (Exact)"))


if __name__ == "__main__":
    unittest.main()


class TestVsdc247OnlyTestMode(unittest.TestCase):
    """VSDC247_ONLY=1: every other scanner is off, so 247 can be tested by itself."""

    LOCAL_PAGE = ("file:///C:/Users/Nex/Downloads/Project%20Sera/APP/tests/test_page_submit_success.html"
                  "#/foreturns-ay26/fo-itr1-ay2026/fo-e-verify-later")      # a URL a crosshair DOES match
    TITLE = "e-Filing Portal | Submit Success - Microsoft Edge"
    PAGE = ["You have successfully submitted your return!", f"Acknowledgement Number : {TODAY_ACK}"]

    def setUp(self):
        self._env = patch.dict(os.environ, {"VSDC_ALLOW_LOCAL_TEST": "1"})
        self._env.start()
        os.environ.pop("VSDC247_MODE", None)
        os.environ.pop("VSDC247_ONLY", None)

    def tearDown(self):
        self._env.stop()

    def _capture(self, **env):
        os.environ.update(env)
        h = Harness(url=self.LOCAL_PAGE, title=self.TITLE)
        h.screen(["Dashboard"], shade=245)
        h.tick()
        h.screen(self.PAGE)
        return h, h.run(4)

    def test_without_the_switch_the_crosshair_pipeline_is_involved_with_this_page(self):
        h, got = self._capture()
        timeline = got[0]["raw_payload"]["timeline"]
        self.assertIn("itr_submitted_pending", [t["crosshair_id"] for t in timeline])

    def test_with_the_switch_only_vsdc247_captures_it(self):
        h, got = self._capture(VSDC247_ONLY="1")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["capture_method"], "VSDC247_itr_ack")
        self.assertEqual(got[0]["arn"], TODAY_ACK)
        self.assertEqual(got[0]["raw_payload"]["timeline"], [], "no crosshair was routed")

    def test_the_switch_overrides_uia_only_mode_which_normally_turns_247_off(self):
        h, got = self._capture(VSDC247_ONLY="1", VSDC_UIA_ONLY="1")
        self.assertEqual([g["capture_method"] for g in got], ["VSDC247_itr_ack"])

    def test_the_switch_never_reads_uia_or_routes_a_crosshair(self):
        os.environ["VSDC247_ONLY"] = "1"
        h = Harness(url=self.LOCAL_PAGE, title=self.TITLE)
        h.screen(["Dashboard"], shade=245)
        with patch("core.vsdc.vsdc_router.vsdc_uia_text.read_page_text") as uia:
            h.tick()
            h.screen(self.PAGE)
            h.run(4)
        uia.assert_not_called()

    def test_an_explicit_off_is_still_respected(self):
        h, got = self._capture(VSDC247_ONLY="1", VSDC247_MODE="off")
        self.assertEqual(got, [])
