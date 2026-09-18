"""
tests/test_vsdc_hud_pill.py — Unit Tests for VSDC/VSDC-X HUD source differentiation
and the persistent "watching" polling indicator.

Covers the two things that determine whether a user can actually SEE which engine
(VSDC-X exact UIA text vs VSDC/OCR) supplied a given capture:
  1. VSDCHudPill._format_subtitle_html — the rendering side (colors the tag).
  2. VSDCRouter._source_tag_from_capture_method — the routing side (derives the
     tag text for toasts that fire from an already-assembled dataset payload,
     where the fresh per-tick uia_fields_used list from that read is no longer
     in scope).
Plus the breathing "watching" indicator that persists while VSDC is actively
polling a page with nothing captured yet (TestHudWatchingIndicator), which
needs a real QApplication since it drives the actual widget/animations.
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


class TestHudWatchingIndicator(unittest.TestCase):
    """
    The pill must show a persistent breathing dot while VSDC is actively
    polling a page it hasn't captured anything from yet, distinct from the
    momentary auto-dismissing toast used for actual captures.
    """

    @classmethod
    def setUpClass(cls):
        if QApplication.instance() is None:
            cls.app = QApplication(sys.argv)
        else:
            cls.app = QApplication.instance()

    def setUp(self):
        self.pill = VSDCHudPill()

    def test_watching_shows_pulse_dot_and_never_auto_dismisses(self):
        self.pill.show_event("watching", "Taxpayer landing dashboard", "")
        self.assertTrue(self.pill._is_watching)
        self.assertTrue(self.pill.pulse_dot.isVisible())
        self.assertFalse(self.pill.icon_label.isVisible())
        self.assertFalse(self.pill.dismiss_timer.isActive())

    def test_re_announcing_the_same_watching_route_does_not_restart_pulse(self):
        self.pill.show_event("watching", "Taxpayer landing dashboard", "")
        self.pill.show_event("watching", "Taxpayer landing dashboard", "")
        self.assertTrue(self.pill._is_watching)
        self.assertTrue(self.pill.pulse_anim.state() == self.pill.pulse_anim.State.Running)

    def test_watching_a_different_route_keeps_pulsing_but_updates_title(self):
        self.pill.show_event("watching", "Taxpayer landing dashboard", "")
        self.pill.show_event("watching", "GST return form table & period details", "")
        self.assertTrue(self.pill._is_watching)
        self.assertEqual(self.pill.title_label.text(), "GST return form table & period details")

    def test_real_capture_event_supersedes_watching(self):
        self.pill.show_event("watching", "Taxpayer landing dashboard", "")
        self.pill.show_event("capture", "Captured ITR-1", "WASIL AMAN MANDAL • Ack: 123456789012345")
        self.assertFalse(self.pill._is_watching)
        self.assertFalse(self.pill.pulse_dot.isVisible())
        self.assertTrue(self.pill.dismiss_timer.isActive())

    def test_stop_watching_fades_out_the_pulse(self):
        self.pill.show_event("watching", "Taxpayer landing dashboard", "")
        self.pill.stop_watching()
        self.assertFalse(self.pill._is_watching)

    def test_stop_watching_is_a_harmless_no_op_when_not_watching(self):
        # Never entered watching mode at all - must not raise.
        self.pill.stop_watching()
        self.assertFalse(self.pill._is_watching)

        # Also harmless after a real event already superseded watching.
        self.pill.show_event("watching", "Taxpayer landing dashboard", "")
        self.pill.show_event("capture", "Captured ITR-1", "Ack: 123456789012345")
        self.pill.stop_watching()
        self.assertFalse(self.pill._is_watching)


if __name__ == "__main__":
    unittest.main()
