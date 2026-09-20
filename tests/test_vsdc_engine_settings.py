"""
tests/test_vsdc_engine_settings.py - Settings -> Tracker: the three engine switches.

VSDC (crosshair pipeline, OCR), VSDC-X (UI Automation) and VSDC 24/7 (the crosshair-independent
safety net) are independent: turning one off must not turn another off. Switching them applies
live, on the running router, with no restart.
"""

import os
import unittest
from unittest.mock import MagicMock, patch

from core.vsdc.vsdc_engines import ENGINE_DEFAULTS, read_engine_flags
from tests.test_vsdc247_integration import ITR_CONFIRMATION, PORTAL_ITR, TITLE_ITR, TODAY_ACK, Harness
from tests.test_vsdc_everify_picker import (
    ACK_1, PAN, TITLE, URL as PICKER_URL, Harness as PickerHarness, STEPPER, card, head, picker,
)

ITR_FORM_URL = "https://eportal.incometax.gov.in/iec/foservices/#/foreturns-ay26/fo-itr1-ay2026/fo-e-verify-later"


class TestReadingTheSettings(unittest.TestCase):
    def test_a_switch_never_saved_uses_the_default_the_settings_page_shows(self):
        self.assertEqual(read_engine_flags(lambda key, default=None: default), (True, True, False))
        self.assertEqual(ENGINE_DEFAULTS, {"vsdc_enabled": "1", "vsdc_x_enabled": "1", "vsdc247_enabled": "0"})

    def test_saved_values_are_read_as_saved(self):
        for stored, want in (({"vsdc_enabled": "0", "vsdc_x_enabled": "0", "vsdc247_enabled": "1"}, (False, False, True)),
                             ({"vsdc_enabled": "1", "vsdc_x_enabled": "0", "vsdc247_enabled": "0"}, (True, False, False)),
                             ({"vsdc_enabled": "0", "vsdc_x_enabled": "1", "vsdc247_enabled": "1"}, (False, True, True))):
            self.assertEqual(read_engine_flags(lambda k, d=None, s=stored: s.get(k, d)), want, stored)

    def test_odd_stored_values_and_a_failing_lookup_fall_back_to_the_defaults(self):
        self.assertEqual(read_engine_flags(lambda k, d=None: None), (True, True, False))
        self.assertEqual(read_engine_flags(lambda k, d=None: "TRUE"), (True, True, True))

        def boom(k, d=None):
            raise RuntimeError("db locked")
        self.assertEqual(read_engine_flags(boom), (True, True, False))


class _Base(unittest.TestCase):
    def setUp(self):
        self._env = patch.dict(os.environ)
        self._env.start()
        for var in ("VSDC247_MODE", "VSDC247_ONLY", "VSDC_UIA_ONLY"):
            os.environ.pop(var, None)

    def tearDown(self):
        self._env.stop()


def tick(h, uia_lines=(), ocr_lines=()):
    """One picker-harness tick that also reports whether UI Automation was read."""
    h.uia_lines = list(uia_lines)
    h.ocr.scan_image.return_value = {"text": "\n".join(ocr_lines), "lines": list(ocr_lines), "words": []}
    r = h.router
    r.route_captured, r.last_screen_hash, r.static_since = False, None, None
    uia = {"text": "\n".join(uia_lines), "lines": list(uia_lines)}
    with patch("core.vsdc.vsdc_router.vsdc_uia_text.is_available", return_value=True), \
            patch("core.vsdc.vsdc_router.vsdc_uia_text.read_page_text", return_value=uia) as read:
        out = r.evaluate_tick()
    return out, read


def ocr_picker():
    return list(STEPPER) + head(1) + ["Assessment Year 2026-27", "ITR 4", "Filing Type Original", "PAN : " + PAN,
                                      "Acknowledgement Number : " + ACK_1, "Filed On : Aug 29, 2026"]


class TestVsdcAndVsdcXAreIndependent(_Base):
    def test_everything_on_reads_the_page_through_ui_automation(self):
        h = PickerHarness()
        h.router.apply_engine_settings(True, True, False)
        out, read = tick(h, uia_lines=picker(card()))
        self.assertTrue(read.called)
        self.assertEqual(out["capture_method"], "VSDC-X_itr_everify_pending")

    def test_vsdc_x_off_never_reads_ui_automation_and_vsdc_carries_on_from_the_screen(self):
        h = PickerHarness()
        h.router.apply_engine_settings(True, False, False)
        outs = []
        for _ in range(4):
            out, read = tick(h, uia_lines=picker(card()), ocr_lines=ocr_picker())
            self.assertFalse(read.called, "VSDC-X is off: UI Automation must not be read")
            if out:
                outs.append(out)
        self.assertEqual(len(outs), 1)
        self.assertEqual(outs[0]["capture_method"], "VSDC_itr_everify_pending")
        self.assertEqual(outs[0]["arn"], ACK_1)

    def test_vsdc_off_and_vsdc_x_on_works_from_ui_automation_alone(self):
        h = PickerHarness()
        h.router.apply_engine_settings(False, True, False)
        # Screen text alone captures nothing ...
        for _ in range(3):
            out, _ = tick(h, uia_lines=[], ocr_lines=ocr_picker())
            self.assertIsNone(out)
        # ... UI Automation does.
        outs = [o for o, _ in (tick(h, uia_lines=picker(card()), ocr_lines=ocr_picker()) for _ in range(4)) if o]
        self.assertEqual([o["capture_method"] for o in outs], ["VSDC-X_itr_everify_pending"])

    def test_turning_vsdc_off_does_not_turn_vsdc_x_off(self):
        h = PickerHarness()
        h.router.apply_engine_settings(False, True, False)
        self.assertTrue(h.router._vsdc_x_enabled)
        self.assertFalse(h.router._engines_off)


class TestVsdc247Switch(_Base):
    def _confirm(self, h):
        h.screen(["Dashboard"], shade=245)
        h.tick()
        h.screen(ITR_CONFIRMATION)
        return h.run(4)

    def test_off_by_default_it_captures_nothing_on_an_unmapped_page(self):
        h = Harness()
        h.router.apply_engine_settings(True, True, False)
        self.assertEqual(self._confirm(h), [])

    def test_switched_on_it_captures_live_without_a_restart(self):
        h = Harness()
        h.router.apply_engine_settings(True, True, False)
        h.screen(["Dashboard"], shade=245)
        h.tick()
        h.screen(["Still nothing"])
        self.assertEqual(h.run(3), [])
        h.router.apply_engine_settings(True, True, True)          # the user ticks "Enable VSDC 24/7" and saves
        h.screen(ITR_CONFIRMATION)
        got = h.run(4)
        self.assertEqual([g["capture_method"] for g in got], ["VSDC247_itr_ack"])
        self.assertEqual(got[0]["arn"], TODAY_ACK)

    def test_switched_off_again_it_stops(self):
        h = Harness()
        h.router.apply_engine_settings(True, True, True)
        h.router.apply_engine_settings(True, True, False)
        self.assertEqual(self._confirm(h), [])

    def test_on_alone_it_works_without_any_crosshair(self):
        h = Harness(url=ITR_FORM_URL)                 # a URL a crosshair DOES match
        h.router.apply_engine_settings(False, False, True)
        got = self._confirm(h)
        self.assertEqual([g["capture_method"] for g in got], ["VSDC247_itr_ack"])
        self.assertEqual(got[0]["raw_payload"]["timeline"], [], "no crosshair was routed")

    def test_the_env_override_can_still_put_it_in_shadow(self):
        os.environ["VSDC247_MODE"] = "shadow"
        h = Harness()
        h.router.apply_engine_settings(True, True, True)
        self.assertEqual(h.router._mode_247, "shadow")
        self.assertEqual(self._confirm(h), [])


class TestAllOff(_Base):
    def test_with_everything_off_the_tick_does_nothing_at_all(self):
        h = Harness()
        h.router.apply_engine_settings(False, False, False)
        h.screen(ITR_CONFIRMATION)
        self.assertIsNone(h.tick())
        h.router.get_foreground_info.assert_not_called()
        h.ocr.capture_window_image.assert_not_called()
        h.ocr.scan_image.assert_not_called()

    def test_turning_one_back_on_wakes_it_up(self):
        h = Harness()
        h.router.apply_engine_settings(False, False, False)
        h.router.apply_engine_settings(True, True, False)
        self.assertFalse(h.router._engines_off)
        h.router.get_foreground_info.reset_mock()
        h.tick()
        h.router.get_foreground_info.assert_called()


class TestTheUiAutomationSwitchCoversTheAddressTripwire(_Base):
    def test_vsdc_x_off_means_no_ui_automation_read_even_on_an_unlisted_gov_host(self):
        h = Harness(url="https://efile.newtax.gov.in/dashboard", title="Efile - Google Chrome")
        h.router.apply_engine_settings(True, False, False)
        with patch("core.vsdc.vsdc_router.vsdc_uia_text.read_page_text") as read:
            for _ in range(3):
                h.tick(advance=6)
        read.assert_not_called()

    def test_vsdc_x_on_still_runs_the_tripwire(self):
        h = Harness(url="https://efile.newtax.gov.in/dashboard", title="Efile - Google Chrome")
        h.router.apply_engine_settings(True, True, False)
        with patch("core.vsdc.vsdc_router.vsdc_uia_text.read_page_text", return_value={"text": "", "lines": ["E Filing logo"]}) as read:
            h.tick(advance=6)
        self.assertTrue(read.called)


class TestDeveloperOverridesStillWork(_Base):
    def test_vsdc247_only_env_wins_over_the_switches(self):
        os.environ["VSDC247_ONLY"] = "1"
        h = Harness(url=ITR_FORM_URL)
        h.router.apply_engine_settings(True, True, True)
        h.screen(["Dashboard"], shade=245)
        h.tick()
        h.screen(ITR_CONFIRMATION)
        got = h.run(4)
        self.assertEqual([g["capture_method"] for g in got], ["VSDC247_itr_ack"])

    def test_uia_only_env_keeps_the_screen_text_discarded(self):
        os.environ["VSDC_UIA_ONLY"] = "1"
        h = PickerHarness()
        h.router.apply_engine_settings(True, True, False)
        self.assertTrue(h.router._uia_only_mode)
        for _ in range(3):
            out, _ = tick(h, uia_lines=[], ocr_lines=ocr_picker())
            self.assertIsNone(out)


if __name__ == "__main__":
    unittest.main()
