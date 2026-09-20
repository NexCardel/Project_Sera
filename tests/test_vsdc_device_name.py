"""
tests/test_vsdc_device_name.py - every capture says which PC produced it.

The name is the first line of that PC's own device_identity.txt (the same file the phone alert
uses), stamped into the payload's JSON at the top level and inside raw_payload. It is never
taken from a shared config, so a payload copied between PCs keeps the PC it came from.
"""

import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.vsdc.vsdc_alerts import stamp_device_name
from tests.test_vsdc247_integration import ITR_CONFIRMATION, Harness
from tests.test_vsdc_everify_picker import Harness as PickerHarness, card, picker


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.identity = Path(self.tmp.name) / "device_identity.txt"
        self.identity.write_text("FRONT-DESK-PC", encoding="utf-8")
        self._env = patch.dict(os.environ, {"VSDC_DEVICE_IDENTITY_FILE": str(self.identity)})
        self._env.start()
        for var in ("VSDC247_MODE", "VSDC247_ONLY", "VSDC_UIA_ONLY"):
            os.environ.pop(var, None)

    def tearDown(self):
        self._env.stop()
        self.tmp.cleanup()


class TestTheStampItself(_Base):
    def test_it_lands_at_the_top_level_and_inside_the_raw_payload(self):
        p = stamp_device_name({"arn": "1", "raw_payload": {"timeline": []}})
        self.assertEqual(p["device_name"], "FRONT-DESK-PC")
        self.assertEqual(p["raw_payload"]["device_name"], "FRONT-DESK-PC")

    def test_a_payload_without_a_raw_payload_still_gets_the_top_level_field(self):
        self.assertEqual(stamp_device_name({"arn": "1"})["device_name"], "FRONT-DESK-PC")

    def test_an_existing_value_is_never_overwritten(self):
        p = stamp_device_name({"device_name": "OTHER-PC", "raw_payload": {"device_name": "OTHER-PC"}})
        self.assertEqual((p["device_name"], p["raw_payload"]["device_name"]), ("OTHER-PC", "OTHER-PC"))

    def test_things_that_are_not_payloads_pass_through_untouched(self):
        for value in (None, "text", 7, [1, 2]):
            self.assertEqual(stamp_device_name(value), value)

    def test_a_missing_identity_file_falls_back_to_the_computer_name(self):
        self.identity.unlink()
        self.assertEqual(stamp_device_name({})["device_name"], socket.gethostname()[:40])

    def test_the_name_is_cleaned_and_survives_json(self):
        self.identity.write_text("  Desk<1>\nsecond line", encoding="utf-8")
        p = stamp_device_name({})
        self.assertEqual(p["device_name"], "Desk1")
        json.dumps(p)

    def test_a_renamed_pc_is_reported_under_its_new_name(self):
        self.assertEqual(stamp_device_name({})["device_name"], "FRONT-DESK-PC")
        self.identity.write_text("BACK-OFFICE-PC", encoding="utf-8")
        self.assertEqual(stamp_device_name({})["device_name"], "BACK-OFFICE-PC")


class TestEveryVsdcPayloadCarriesIt(_Base):
    def test_a_vsdc247_capture(self):
        h = Harness()
        h.screen(["Dashboard"], shade=245)
        h.tick()
        h.screen(ITR_CONFIRMATION)
        got = h.run(4)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["device_name"], "FRONT-DESK-PC")
        self.assertEqual(got[0]["raw_payload"]["device_name"], "FRONT-DESK-PC")
        self.assertIn('"device_name": "FRONT-DESK-PC"', json.dumps(got[0]))

    def test_a_crosshair_capture(self):
        h = PickerHarness()
        outs = []
        for _ in range(4):
            r = h.tick(picker(card()))
            if r:
                outs.append(r)
        self.assertEqual(len(outs), 1)
        self.assertEqual(outs[0]["device_name"], "FRONT-DESK-PC")
        self.assertEqual(outs[0]["raw_payload"]["device_name"], "FRONT-DESK-PC")

    def test_every_payload_of_a_multi_return_page_is_stamped(self):
        h = PickerHarness()
        outs = []
        for _ in range(6):
            r = h.tick(picker(card(), card(ack="111111111111111", ftype="Revised")))
            if r:
                outs.append(r)
        self.assertEqual(len(outs), 2)
        self.assertTrue(all(o["device_name"] == "FRONT-DESK-PC" for o in outs))

    def test_the_name_follows_a_rename_while_the_app_is_running(self):
        h = PickerHarness()
        first = [r for r in (h.tick(picker(card())) for _ in range(4)) if r][0]
        self.identity.write_text("BACK-OFFICE-PC", encoding="utf-8")
        second = [r for r in (h.tick(picker(card(ack="111111111111111", ftype="Revised"))) for _ in range(4)) if r][0]
        self.assertEqual((first["device_name"], second["device_name"]), ("FRONT-DESK-PC", "BACK-OFFICE-PC"))


if __name__ == "__main__":
    unittest.main()
