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

    def test_sgt_dispatch_enriched_with_vsdc247_v(self):
        self.router.apply_engine_settings(vsdc=False, vsdc_x=False, vsdc247=False, sgt="live", vsdc247_verify=True)

        arn = "AA191026999932T"
        # Pre-seed verifier ledger
        self.router._verifier.saved[arn] = {
            "arn": arn,
            "form": "GSTR-1",
            "period": "September - 2026-27",
            "page_kind": "gst_ack",
            "score": 95,
            "tier": "certain",
            "seen_at": "2026-10-18T12:00:00",
            "client": {"username": "TESTUSER", "agreement": "agrees"},
        }

        # Mock SGT pending dispatch
        mock_sgt_row = {
            "source": "sgt",
            "portal": "GST Portal",
            "arn": arn,
            "filing_type": "GSTR-1",
            "period_label": "September - 2026-27",
            "status": "Submitted",
            "capture_method": "SGT_live",
            "raw_payload": {},
        }
        self.router._sgt = MagicMock()
        self.router._sgt.pending.return_value = True
        self.router._sgt.pop_dispatch.return_value = dict(mock_sgt_row)

        initial_dispatched = len(self.router._dispatched_ids)
        row = self.router.evaluate_tick()

        self.assertIsNotNone(row)
        self.assertEqual(row["arn"], arn)
        self.assertIn("vsdc247-v", row)
        self.assertTrue(row["vsdc247-v"]["verified"])
        self.assertEqual(row["vsdc247-v"]["status"], "verified")
        self.assertEqual(row["vsdc247-v"]["score"], 95)
        self.assertEqual(row["raw_payload"]["vsdc247-v"]["verified"], True)
        self.assertEqual(len(self.router._dispatched_ids), initial_dispatched)

    def test_standalone_flagged_rows_not_emitted(self):
        self.router.apply_engine_settings(vsdc=False, vsdc_x=False, vsdc247=False, sgt="live", vsdc247_verify=True)
        # Standalone rows must NOT be emitted from evaluate_tick
        self.router._verifier_outbox.append({"arn": "STANDALONE_ARN", "capture_method": "VSDC247_verify"})
        self.router._sgt = MagicMock()
        self.router._sgt.pending.return_value = False
        row = self.router.evaluate_tick()
        self.assertIsNone(row)

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
