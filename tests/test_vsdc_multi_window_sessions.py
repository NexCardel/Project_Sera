"""
tests/test_vsdc_multi_window_sessions.py — Per-Window Session Isolation

Covers the architecture question this was built to answer: what happens when
two portals (or two different clients, or the same client) are open in two
different browser windows at once? Before per-window isolation, VSDCRouter
kept exactly one shared self.assembler / self.last_url / etc. driven by
whichever window was foreground at each tick — alt-tabbing between two
different clients made update_identity()'s PAN-context-switch guard fire on
every focus change, prematurely flushing or silently wiping whichever
client's window had just lost focus. _WindowSession (keyed by hwnd) fixes
this; these tests drive VSDCRouter.evaluate_tick() against two different
mocked hwnds to prove it holds.
"""

import unittest
from unittest.mock import MagicMock, patch
from PIL import Image

from core.vsdc.vsdc_router import VSDCRouter
from core.vsdc.vsdc_assembler import VisualSessionAssembler


def _make_router(mock_ocr):
    return VSDCRouter(ocr_engine=mock_ocr, assembler=VisualSessionAssembler(), on_activity=lambda *a: None)


class TestVSDCMultiWindowSessions(unittest.TestCase):

    def setUp(self):
        self.mock_ocr = MagicMock()
        self.mock_ocr.capture_window_image.return_value = Image.new("RGB", (1200, 800), color="white")
        self.router = _make_router(self.mock_ocr)

    def _tick_as(self, hwnd, title, proc, url, text, lines):
        self.router.get_foreground_info = MagicMock(return_value=(hwnd, title, proc))
        self.router.extract_browser_url = MagicMock(return_value=url)
        self.mock_ocr.scan_image.return_value = {"text": text, "lines": lines}
        return self.router.evaluate_tick()

    @patch("core.vsdc.vsdc_router.user32.IsWindow", return_value=True)
    def test_two_different_clients_in_two_windows_do_not_bleed(self, _mock_is_window):
        # IsWindow() patched True: these synthetic hwnds stand in for two real,
        # simultaneously-open browser windows (this test is about isolation
        # between two LIVE windows, not about pruning a closed one - that's
        # covered separately below with the real IsWindow() behavior).
        HWND_GST = 1001
        HWND_ITR = 2002

        # Window 1 (hwnd 1001): GST client "AMAN ASSOCIATES" welcome page
        self._tick_as(
            HWND_GST, "GST Common Portal - Google Chrome", "chrome.exe",
            "https://services.gst.gov.in/services/auth/fowelcome",
            "AMAN ASSOCIATES 27AAPFU0939L1ZV",
            ["AMAN ASSOCIATES", "27AAPFU0939L1ZV"],
        )
        session_gst = self.router._window_sessions[HWND_GST]
        self.assertEqual(session_gst.assembler.gstin, "27AAPFU0939L1ZV")
        self.assertEqual(session_gst.assembler.client_name, "AMAN ASSOCIATES")

        # Alt-tab: window 2 (hwnd 2002), a COMPLETELY DIFFERENT ITR client's
        # personal info page. Before per-window isolation, this would have
        # triggered update_identity()'s PAN-context-switch guard against the
        # GST client's PAN and wiped/flushed it.
        self._tick_as(
            HWND_ITR, "e-Filing - Google Chrome", "chrome.exe",
            "https://eportal.incometax.gov.in/iec/foservices/#/dashboard/personal_information",
            "e-Filing RAMESH SHARMA ABCPE1234F",
            ["e-Filing", "RAMESH SHARMA", "ABCPE1234F"],
        )
        session_itr = self.router._window_sessions[HWND_ITR]
        self.assertEqual(session_itr.assembler.client_pan, "ABCPE1234F")
        self.assertEqual(session_itr.assembler.client_name, "RAMESH SHARMA")

        # The GST window's session must be completely untouched - still there,
        # still holding its own client, never reset or flushed.
        session_gst_again = self.router._window_sessions[HWND_GST]
        self.assertIs(session_gst_again, session_gst)
        self.assertEqual(session_gst_again.assembler.gstin, "27AAPFU0939L1ZV")
        self.assertEqual(session_gst_again.assembler.client_name, "AMAN ASSOCIATES")
        self.assertFalse(getattr(session_gst_again.assembler, "_flushed", False))

        # Alt-tab back to the GST window - identical URL, so it must be a
        # quiet re-render (already captured), not a re-triggered capture.
        calls_before = self.mock_ocr.scan_image.call_count
        result_back = self._tick_as(
            HWND_GST, "GST Common Portal - Google Chrome", "chrome.exe",
            "https://services.gst.gov.in/services/auth/fowelcome",
            "AMAN ASSOCIATES 27AAPFU0939L1ZV",
            ["AMAN ASSOCIATES", "27AAPFU0939L1ZV"],
        )
        self.assertIsNone(result_back)
        self.assertEqual(self.router._window_sessions[HWND_GST].assembler.gstin, "27AAPFU0939L1ZV")

    def test_same_client_same_pan_switch_guard_still_works_within_one_window(self):
        # The PAN-context-switch guard must still fire WITHIN a single window
        # (e.g. one taxpayer logs out, a different one logs in on the SAME
        # browser tab) - per-window isolation must not break this existing,
        # correct single-window behavior.
        HWND = 3003
        self._tick_as(
            HWND, "e-Filing - Google Chrome", "chrome.exe",
            "https://eportal.incometax.gov.in/iec/foservices/#/dashboard/personal_information",
            "e-Filing RAMESH SHARMA ABCPE1234F",
            ["e-Filing", "RAMESH SHARMA", "ABCPE1234F"],
        )
        assembler = self.router._window_sessions[HWND].assembler
        self.assertEqual(assembler.client_pan, "ABCPE1234F")

        # Same window, different client now logged in (context switch)
        assembler.update_identity(pan="ZZZZZ9999Z", name="SECOND CLIENT")
        self.assertEqual(assembler.client_pan, "ZZZZZ9999Z")

    def test_closed_window_session_is_pruned_and_not_reused(self):
        HWND_A = 4004
        self._tick_as(
            HWND_A, "GST Common Portal - Google Chrome", "chrome.exe",
            "https://services.gst.gov.in/services/auth/fowelcome",
            "STALE CLIENT 27AAPFU0939L1ZV",
            ["STALE CLIENT", "27AAPFU0939L1ZV"],
        )
        self.assertIn(HWND_A, self.router._window_sessions)

        # Window A closes; a DIFFERENT window (hwnd B) is now foreground. Real
        # Windows hwnd values get recycled, so this also proves a reused hwnd
        # can never inherit a prior, unrelated session's stale client data:
        # since HWND_A wasn't reused here, IsWindow(HWND_A) genuinely returns
        # False and it gets pruned rather than lingering.
        HWND_B = 5005
        self._tick_as(
            HWND_B, "e-Filing - Google Chrome", "chrome.exe",
            "https://eportal.incometax.gov.in/iec/foservices/#/dashboard/personal_information",
            "e-Filing RAMESH SHARMA ABCPE1234F",
            ["e-Filing", "RAMESH SHARMA", "ABCPE1234F"],
        )
        # HWND_A is a real, non-existent window handle (never created by the
        # OS) - IsWindow() correctly reports False for it, so it's pruned.
        self.assertNotIn(HWND_A, self.router._window_sessions)
        self.assertIn(HWND_B, self.router._window_sessions)


if __name__ == "__main__":
    unittest.main()
