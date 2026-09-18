"""
tests/test_vsdc_hud_pill.py — Unit Tests for VSDC/VSDC-X HUD source differentiation
and the capture pulse.

Covers the two things that determine whether a user can actually SEE which engine
(VSDC-X exact UIA text vs VSDC/OCR) supplied a given capture:
  1. VSDCHudPill._format_subtitle_html — the rendering side (colors the tag).
  2. VSDCRouter._source_tag_from_capture_method — the routing side (derives the
     tag text for toasts that fire from an already-assembled dataset payload,
     where the fresh per-tick uia_fields_used list from that read is no longer
     in scope).
Plus the legacy-SDC-toast-style behavior (TestHudCapturePulse): hidden while VSDC
is only polling, shown and pulsed on a capture, updated in place with a fresh pulse
if another event lands while it is up. That needs a real QApplication since it
drives the actual widget/animations.
"""

import sys
import unittest

from PySide6.QtWidgets import QApplication

from ui.components.vsdc_hud_pill import VSDCHudPill
from core.vsdc.vsdc_router import VSDCRouter


class TestHudSubtitleFormatting(unittest.TestCase):
    def test_vsdc_x_tag_colored_distinctly(self):
        html = VSDCHudPill._format_subtitle_html("WASIL AMAN MANDAL • VSDC-X (Exact)")
        self.assertIn("VSDC-X (Exact)", html)
        self.assertIn("#58A6FF", html)  # VSDC-X gets the distinct blue

    def test_vsdc_visual_tag_colored_distinctly(self):
        html = VSDCHudPill._format_subtitle_html("WASIL AMAN MANDAL • VSDC (Visual)")
        self.assertIn("VSDC (Visual)", html)
        self.assertIn("#8B949E", html)  # plain OCR gets the muted gray

    def test_vsdc_x_and_vsdc_visual_never_cross_colored(self):
        # Guards against a regex that's too greedy and paints VSDC (Visual) with
        # VSDC-X's color just because "VSDC" is a substring of "VSDC-X".
        html = VSDCHudPill._format_subtitle_html("Portal: Income Tax • VSDC (Visual)")
        self.assertNotIn("#58A6FF", html)

    def test_ack_and_source_tag_both_render_independently(self):
        html = VSDCHudPill._format_subtitle_html("Ack: 198273645019283 • VSDC-X (Exact)")
        self.assertIn("198273645019283", html)
        self.assertIn("#39FF14", html)   # ack chip color
        self.assertIn("#58A6FF", html)   # source tag color

    def test_plain_subtitle_without_source_tag_is_unaffected(self):
        html = VSDCHudPill._format_subtitle_html("Portal: Income Tax")
        self.assertEqual(html, "Portal: Income Tax")

    def test_empty_subtitle_returns_empty(self):
        self.assertEqual(VSDCHudPill._format_subtitle_html(""), "")


class TestSourceTagFromCaptureMethod(unittest.TestCase):
    def test_vsdc_x_prefixed_capture_method(self):
        tag = VSDCRouter._source_tag_from_capture_method("VSDC-X_itr_submitted_pending")
        self.assertEqual(tag, " • VSDC-X (Exact)")

    def test_plain_vsdc_capture_method(self):
        tag = VSDCRouter._source_tag_from_capture_method("VSDC_itr_submitted_pending")
        self.assertEqual(tag, " • VSDC (Visual)")

    def test_gst_capture_methods_use_same_convention(self):
        self.assertEqual(
            VSDCRouter._source_tag_from_capture_method("VSDC-X_gst_form_details"),
            " • VSDC-X (Exact)",
        )
        self.assertEqual(
            VSDCRouter._source_tag_from_capture_method("VSDC_gst_form_details"),
            " • VSDC (Visual)",
        )

    def test_missing_or_empty_capture_method_defaults_to_visual(self):
        self.assertEqual(VSDCRouter._source_tag_from_capture_method(None), " • VSDC (Visual)")
        self.assertEqual(VSDCRouter._source_tag_from_capture_method(""), " • VSDC (Visual)")


class TestHudCapturePulse(unittest.TestCase):
    """
    Mirrors the legacy SDC toast (sdc_toast.js): nothing is on screen while VSDC is
    merely polling; a capture makes the pill appear and pulse; a further event while
    it is still up updates it in place with a fresh pulse and a restarted dismiss timer.
    """

    @classmethod
    def setUpClass(cls):
        if QApplication.instance() is None:
            cls.app = QApplication(sys.argv)
        else:
            cls.app = QApplication.instance()

    def setUp(self):
        self.pill = VSDCHudPill()

    def _running(self):
        return self.pill.pulse_anim.state() == self.pill.pulse_anim.State.Running

    def test_hidden_until_something_is_captured(self):
        self.assertFalse(self.pill.isVisible())
        self.assertFalse(self.pill.dismiss_timer.isActive())

    def test_capture_shows_pill_and_auto_dismisses(self):
        self.pill.show_event("capture", "Captured ITR-1", "Ack: 123456789012345")
        self.assertTrue(self.pill.isVisible())
        self.assertEqual(self.pill.title_label.text(), "Captured ITR-1")
        self.assertTrue(self.pill.dismiss_timer.isActive())

    def test_first_capture_pulses_only_after_the_fade_in_finishes(self):
        self.pill.show_event("capture", "Captured ITR-1", "")
        # Pulse is deferred so it is visible rather than hidden under the fade-in.
        self.assertTrue(self.pill._pulse_pending)
        self.assertFalse(self._running())
        self.pill.opacity_effect.setOpacity(1.0)
        self.pill._on_fade_finished()
        self.assertFalse(self.pill._pulse_pending)
        self.assertTrue(self._running())

    def test_a_new_event_while_showing_updates_in_place_and_pulses(self):
        self.pill.show_event("capture", "Captured ITR-1", "")
        self.pill.opacity_effect.setOpacity(1.0)
        self.pill.show_event("update", "DOB: 28-Feb-1983", "RAHUL MONDAL")
        self.assertEqual(self.pill.title_label.text(), "DOB: 28-Feb-1983")
        self.assertTrue(self._running())
        self.assertTrue(self.pill.dismiss_timer.isActive())

    def test_identical_event_does_not_pulse_again(self):
        self.pill.show_event("capture", "Captured ITR-1", "")
        self.pill.opacity_effect.setOpacity(1.0)
        self.pill.pulse_anim.stop()
        self.pill.show_event("capture", "Captured ITR-1", "")
        self.assertFalse(self._running())
        self.assertTrue(self.pill.dismiss_timer.isActive())

    def test_pulse_grows_the_pill_slightly_then_returns_to_exact_size(self):
        self.pill.show_event("capture", "Captured ITR-1", "Ack: 123456789012345")
        self.pill.opacity_effect.setOpacity(1.0)
        self.pill._start_pulse()
        base = self.pill.card.width()
        self.pill._apply_pulse(1.0)
        peak = self.pill.card.width()
        self.assertGreater(peak, base)
        self.assertLessEqual(peak - base, round(VSDCHudPill._BASE_WIDTH * 0.05))
        self.pill.pulse_anim.stop()
        self.pill._reset_pulse_size()
        self.assertEqual(self.pill.card.width(), VSDCHudPill._BASE_WIDTH)
        self.assertEqual(self.pill.card.minimumHeight(), 0)

    def test_pulse_finishing_leaves_no_residual_size(self):
        self.pill.show_event("capture", "Captured ITR-1", "")
        self.pill.opacity_effect.setOpacity(1.0)
        self.pill._start_pulse()
        self.pill._apply_pulse(0.7)
        self.pill.pulse_anim.finished.emit()
        self.assertEqual(self.pill.card.width(), VSDCHudPill._BASE_WIDTH)

    def test_no_watching_state_remains(self):
        self.assertFalse(hasattr(self.pill, "stop_watching"))
        self.assertNotIn("watching", VSDCHudPill.EVENT_THEMES)


if __name__ == "__main__":
    unittest.main()
