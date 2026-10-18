"""
tests/test_vsdc247_verify_router.py - VSDC247-V WP5: Router lane, engine switch, and alert dispatch.
Tests:
  - apply_engine_settings toggles vsdc247_verify and env override VSDC247_VERIFY works
  - Verifier runs in all SGT modes (alone, with crosshairs, all engines off)
  - Flagged rows leave via evaluate_tick dispatch path without touching _dispatched_ids
  - Alerts are dispatched to ntfy / HUD
  - Exceptions inside verifier never break evaluate_tick
"""

import os
import unittest
from unittest.mock import MagicMock, patch

from core.vsdc.vsdc_engines import read_verifier_enabled
from core.vsdc.vsdc_router import VSDCRouter


class FakeOcr:
    def capture_window_image(self, hwnd):
        return None

    def scan_image(self, img):
        return {"lines": [], "words": [], "green_boxes": []}


class TestVerifierRouterLane(unittest.TestCase):

    def setUp(self):
        self.patcher = patch.dict(os.environ)
        self.patcher.start()
        os.environ.pop("VSDC247_VERIFY", None)
        os.environ.pop("SGT_MODE", None)

        self.ocr = FakeOcr()
        self.router = VSDCRouter(ocr_engine=self.ocr)
        self.router.alerts = MagicMock()
        self.router._health_hud = MagicMock()

    def tearDown(self):
        self.patcher.stop()

    def test_read_verifier_enabled(self):
        self.assertFalse(read_verifier_enabled(lambda k, d="0": "0"))
        self.assertTrue(read_verifier_enabled(lambda k, d="0": "1"))

        # Env override
        os.environ["VSDC247_VERIFY"] = "1"
        self.assertTrue(read_verifier_enabled(lambda k, d="0": "0"))
        os.environ["VSDC247_VERIFY"] = "0"
        self.assertFalse(read_verifier_enabled(lambda k, d="0": "1"))

    def test_apply_engine_settings_activates_and_deactivates(self):
        # 1. Start with verifier off
        self.router.apply_engine_settings(vsdc=False, vsdc_x=False, vsdc247=False, sgt="live", vsdc247_verify=False)
        self.assertFalse(self.router._verifier_enabled)
        self.assertIsNone(self.router._verifier)

        # 2. Toggle verifier on
        self.router.apply_engine_settings(vsdc=False, vsdc_x=False, vsdc247=False, sgt="live", vsdc247_verify=True)
        self.assertTrue(self.router._verifier_enabled)
        self.assertIsNotNone(self.router._verifier)

        # 3. Toggle verifier off
        self.router.apply_engine_settings(vsdc=False, vsdc_x=False, vsdc247=False, sgt="live", vsdc247_verify=False)
        self.assertFalse(self.router._verifier_enabled)
        self.assertIsNone(self.router._verifier)

    def test_env_override_controls_verifier_live(self):
        os.environ["VSDC247_VERIFY"] = "1"
        self.router.apply_engine_settings(vsdc=False, vsdc_x=False, vsdc247=False, sgt="live", vsdc247_verify=False)
        self.assertTrue(self.router._verifier_enabled)
        self.assertIsNotNone(self.router._verifier)

    def test_verifier_runs_when_sgt_alone(self):
        self.router.apply_engine_settings(vsdc=False, vsdc_x=False, vsdc247=False, sgt="live", vsdc247_verify=True)
        self.assertTrue(self.router._sgt_alone)

        # Mock _run_verifier to ensure it gets invoked
        self.router._run_verifier = MagicMock()
        self.router.evaluate_tick()
        self.router._run_verifier.assert_called_once()

    def test_verifier_runs_when_all_engines_off(self):
        self.router.apply_engine_settings(vsdc=False, vsdc_x=False, vsdc247=False, sgt="off", vsdc247_verify=True)
        self.assertTrue(self.router._engines_off)

        self.router._run_verifier = MagicMock()
        self.router.evaluate_tick()
        self.router._run_verifier.assert_called_once()

    def test_verifier_outbox_dispatches_flagged_row(self):
        self.router.apply_engine_settings(vsdc=False, vsdc_x=False, vsdc247=False, sgt="live", vsdc247_verify=True)

        flagged = {
            "source": "vsdc247_verify",
            "portal": "GST Portal",
            "arn": "AA191026999932T",
            "status": "Submitted",
            "capture_method": "VSDC247_verify",
            "dataset_key": "V247:GST:AA191026999932T",
        }
        self.router._verifier_outbox.append(flagged)

        initial_dispatched = len(self.router._dispatched_ids)
        row = self.router.evaluate_tick()

        self.assertIsNotNone(row)
        self.assertEqual(row["arn"], "AA191026999932T")
        self.assertEqual(row["capture_method"], "VSDC247_verify")
        self.assertEqual(len(self.router._verifier_outbox), 0)
        # Did not poison capture dedupe set
        self.assertEqual(len(self.router._dispatched_ids), initial_dispatched)

    def test_verifier_alert_dispatch(self):
        self.router.apply_engine_settings(vsdc=False, vsdc_x=False, vsdc247=False, sgt="live", vsdc247_verify=True)

        # Missed alert
        self.router._on_verifier_alert("sgt_missed", {"block": {"form": "GSTR-3B", "page_url": "return.gst.gov.in"}})
        self.router.alerts.notify_sgt_missed.assert_called_once()
        self.router._health_hud.assert_called_with("SGT Verifier", "Seen only by OCR — SGT missed it")

        # Client conflict alert
        self.router._on_verifier_alert("client_differs", {"block": {"form": "GSTR-1", "page_url": "return.gst.gov.in"}})
        self.router.alerts.notify_client_conflict.assert_called_once()

    def test_verifier_exception_does_not_break_router_tick(self):
        self.router.apply_engine_settings(vsdc=False, vsdc_x=False, vsdc247=False, sgt="live", vsdc247_verify=True)

        # Force an error in verifier
        self.router._verifier.reconcile_tick = MagicMock(side_effect=RuntimeError("Simulated OCR failure"))
        # Must not raise
        result = self.router.evaluate_tick()
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
