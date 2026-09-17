"""
tests/test_vsdc_hud_pill.py — Unit Tests for VSDC/VSDC-X HUD source differentiation

Covers the two things that determine whether a user can actually SEE which engine
(VSDC-X exact UIA text vs VSDC/OCR) supplied a given capture:
  1. VSDCHudPill._format_subtitle_html — the rendering side (colors the tag).
  2. VSDCRouter._source_tag_from_capture_method — the routing side (derives the
     tag text for toasts that fire from an already-assembled dataset payload,
     where the fresh per-tick uia_fields_used list from that read is no longer
     in scope).
Neither requires a QApplication event loop — both are pure functions/staticmethods.
"""

import unittest

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


if __name__ == "__main__":
    unittest.main()
