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


class TestHudContextChips(unittest.TestCase):
    """The pill's return-context row: Form / Type (ITR) or Pref (GST) / Period."""

    def test_itr_context_uses_type_label(self):
        html = VSDCHudPill._format_context_html(
            {"form": "ITR-4", "filing_pref": "Updated", "period": "AY 2025-26"})
        for text in ("Form", "ITR-4", "Type", "Updated", "Period", "AY&nbsp;2025-26"):
            self.assertIn(text, html)
        self.assertNotIn("Pref", html)

    def test_gst_context_uses_pref_label(self):
        html = VSDCHudPill._format_context_html(
            {"form": "GSTR-3B", "filing_pref": "Quarterly", "period": "June (FY 2026-27)"})
        self.assertIn("Pref", html)
        self.assertNotIn("Type", html)

    def test_only_captured_values_are_shown(self):
        html = VSDCHudPill._format_context_html({"form": "ITR-4", "filing_pref": None, "period": "AY 2026-27"})
        self.assertIn("ITR-4", html)
        self.assertNotIn("Type", html)
        self.assertEqual(VSDCHudPill._format_context_html({"form": None, "filing_pref": None, "period": None}), "")
        self.assertEqual(VSDCHudPill._format_context_html(None), "")

    def test_values_are_escaped(self):
        # Values come straight off a web page; they must never be parsed as markup.
        html = VSDCHudPill._format_context_html({"form": "<b>ITR</b>"})
        self.assertIn("&lt;b&gt;ITR&lt;/b&gt;", html)


class TestHudContextRouting(unittest.TestCase):
    """How the router attaches the return context to HUD events."""

    def setUp(self):
        from unittest.mock import MagicMock
        from core.vsdc.vsdc_assembler import VisualSessionAssembler
        self.events = []
        self.assembler = VisualSessionAssembler()
        self.router = VSDCRouter(
            ocr_engine=MagicMock(), assembler=self.assembler,
            on_activity=lambda et, title, sub: self.events.append((et, title, sub, self.router.activity_context)),
        )
        self.assembler.update_identity(pan="BJYPM4326D", name="WASIL AMAN MANDAL", portal="Income Tax")

    def _tick(self, body):
        """Runs body() the way evaluate_tick does: events held, then flushed."""
        self.router._hud_buffer = []
        self.router._hud_buffering = True
        try:
            body()
        finally:
            self.router._hud_buffering = False
            self.router._flush_hud_events()

    def test_a_silent_capture_of_form_and_period_still_shows_the_pill(self):
        self._tick(lambda: self.assembler.update_selection(filing_type="ITR-4", period_label="AY 2026-27"))
        self.assertEqual(len(self.events), 1)
        et, title, _, ctx = self.events[0]
        self.assertEqual((et, title), ("update", "Return Details Captured"))
        self.assertEqual((ctx["form"], ctx["period"]), ("ITR-4", "AY 2026-27"))

    def test_an_event_raised_before_the_form_was_read_still_shows_it(self):
        def body():
            self.router.notify_activity("identity", "Assessee: WASIL AMAN MANDAL", "")
            self.assembler.update_selection(filing_type="ITR-4", period_label="AY 2025-26", filing_preference="Updated")
        self._tick(body)
        self.assertEqual(len(self.events), 1)  # rides on the identity event, no extra pulse
        ctx = self.events[0][3]
        self.assertEqual((ctx["form"], ctx["filing_pref"], ctx["period"]), ("ITR-4", "Updated", "AY 2025-26"))

    def test_a_capture_keeps_its_return_even_after_the_selection_is_cleared(self):
        # Sealing a submission clears the selection in the same tick; the capture toast
        # must still show the return it captured.
        self.assembler.update_selection(filing_type="ITR-4", period_label="AY 2025-26", filing_preference="Updated")
        def body():
            self.router.notify_activity("capture", "Captured ITR-4", "Ack: 774193820150925")
            self.assembler.clear_workflow_selection()
        self._tick(body)
        ctx = self.events[-1][3]
        self.assertEqual((ctx["form"], ctx["filing_pref"], ctx["period"]), ("ITR-4", "Updated", "AY 2025-26"))

    def test_an_unchanged_selection_is_not_announced_again(self):
        self._tick(lambda: self.assembler.update_selection(filing_type="ITR-4", period_label="AY 2026-27"))
        self._tick(lambda: None)
        self._tick(lambda: self.assembler.update_selection(filing_type="ITR-4", period_label="AY 2026-27"))
        self.assertEqual(len(self.events), 1)

    def test_a_cleared_selection_is_not_announced_but_reselecting_it_is(self):
        self._tick(lambda: self.assembler.update_selection(filing_type="ITR-4", period_label="AY 2026-27"))
        self._tick(self.assembler.clear_workflow_selection)
        self.assertEqual(len(self.events), 1)  # clearing is silent
        self._tick(lambda: self.assembler.update_selection(filing_type="ITR-4", period_label="AY 2026-27"))
        self.assertEqual(len(self.events), 2)  # coming back to it is a new capture

    def test_the_same_selection_for_the_next_client_is_announced(self):
        self._tick(lambda: self.assembler.update_selection(filing_type="ITR-4", period_label="AY 2026-27"))
        self.assembler.reset(new_pan="BEBPM9120B")  # new session, new session_id
        self._tick(lambda: self.assembler.update_selection(filing_type="ITR-4", period_label="AY 2026-27"))
        self.assertEqual(len(self.events), 2)

    def test_an_event_outside_a_tick_is_sent_at_once_with_context(self):
        self.assembler.update_selection(filing_type="ITR-4", period_label="AY 2026-27")
        self.router.notify_activity("logout", "Session Concluded", "")
        self.assertEqual(self.events[-1][3]["form"], "ITR-4")


class TestItrFilingTypeDoesNotLeakIntoTheNextReturn(unittest.TestCase):
    def test_itr_filing_type_is_cleared_with_the_workflow_but_gst_preference_is_kept(self):
        from core.vsdc.vsdc_assembler import VisualSessionAssembler
        itr = VisualSessionAssembler()
        itr.update_selection(filing_type="ITR-4", period_label="AY 2025-26", filing_preference="Updated")
        itr.clear_workflow_selection()
        self.assertIsNone(itr.filing_preference)

        gst = VisualSessionAssembler()
        gst.update_identity(gstin="19CJLPM0265M1ZO", name="AMAN ASSOCIATES", portal="GST Portal",
                            filing_preference="Quarterly")
        gst.clear_workflow_selection()
        self.assertEqual(gst.filing_preference, "Quarterly")


if __name__ == "__main__":
    unittest.main()
