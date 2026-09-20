"""
tests/test_vsdc_alerts.py - the phone alert for a submission captured with no client.

Nothing here touches the network: the transport is a fake, and conftest.py points every test at a
non-existent alert config so a developer's real topic can never receive test traffic.
"""

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from core.vsdc.vsdc_alerts import AlertSender, device_name, load_alert_config, redact_identifiers
from core.vsdc.vsdc_assembler import VisualSessionAssembler
from core.vsdc.vsdc_router import VSDCRouter

TOPIC = "sera-alerts-9f3k2m8q7x1z5w0p"
CONFIG = {"topic": TOPIC, "server": "https://ntfy.sh", "machine": "Front desk PC"}


def sender(**kw):
    post = MagicMock()
    s = AlertSender(config=kw.pop("config", CONFIG), post=post, sync=True, sleep=MagicMock(), **kw)
    return s, post


class TestWhatMayLeaveTheMachine(unittest.TestCase):
    def test_identifier_shaped_text_is_removed(self):
        for raw, gone in (
            ("https://x.gov.in/ack/901036690200926", "901036690200926"),
            ("https://x.gov.in/returns/AA270826000123Z/view", "AA270826000123Z"),
            ("https://x.gov.in/u/BJYPM4326D/profile", "BJYPM4326D"),
            ("https://x.gov.in/g/19CJLPM0265M1ZO", "19CJLPM0265M1ZO"),
        ):
            out = redact_identifiers(raw)
            self.assertNotIn(gone, out, raw)

    def test_ordinary_route_names_are_untouched(self):
        url = "https://eportal.incometax.gov.in/iec/foservices/#/foreturns-ay26/fo-itr1-ay2026/fo-e-verify-later"
        self.assertEqual(redact_identifiers(url), url)

    def test_the_message_carries_no_client_data(self):
        s, post = sender()
        s.notify_unattributed_submission(
            "ITR-4", "https://eportal.incometax.gov.in/iec/foservices/#/ack/901036690200926/BJYPM4326D",
            when=datetime(2026, 9, 20, 14, 5))
        url, title, body, priority, tags = post.call_args.args
        blob = f"{url}\n{title}\n{body}"
        for secret in ("901036690200926", "BJYPM4326D"):
            self.assertNotIn(secret, blob)
        self.assertIn("PC: Front desk PC", body)
        self.assertIn("ITR-4", body)
        self.assertIn("20-Sep 14:05", body)
        self.assertEqual(url, f"https://ntfy.sh/{TOPIC}")
        self.assertEqual(priority, "high")

    def test_header_values_are_plain_ascii(self):
        s, post = sender()
        s.notify_unattributed_submission("ITR-4", "https://a.gov.in/x")
        _, title, _, priority, tags = post.call_args.args
        for h in (title, priority, tags):
            h.encode("ascii")

    def test_an_unreadable_address_bar_says_so_instead_of_leaving_a_gap(self):
        s, post = sender()
        s.notify_unattributed_submission("ITR-4", "")
        self.assertIn("address bar not readable", post.call_args.args[2])


class TestPcName(unittest.TestCase):
    """Each PC names itself from the device_identity.txt in the Sera data folder."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "device_identity.txt"
        self._env = patch.dict(os.environ, {"VSDC_DEVICE_IDENTITY_FILE": str(self.path)})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self.tmp.cleanup()

    def test_the_name_is_read_from_the_identity_file(self):
        self.path.write_text("DESKTOP-62FSJVL", encoding="utf-8")
        self.assertEqual(device_name(), "DESKTOP-62FSJVL")

    def test_a_renamed_pc_is_reported_under_its_new_name(self):
        self.path.write_text("Front desk PC\n", encoding="utf-8")
        self.assertEqual(device_name(), "Front desk PC")

    def test_only_the_first_line_is_used_and_it_is_cleaned_and_capped(self):
        self.path.write_text("  Aman<script>alert(1)</script> PC  \nsecond line", encoding="utf-8")
        self.assertEqual(device_name(), "Amanscriptalert1script PC")
        self.path.write_text("X" * 200, encoding="utf-8")
        self.assertEqual(len(device_name()), 40)

    def test_a_missing_or_empty_file_falls_back_to_the_hostname(self):
        import socket
        self.assertEqual(device_name(), socket.gethostname()[:40])
        self.path.write_text("   \n", encoding="utf-8")
        self.assertEqual(device_name(), socket.gethostname()[:40])

    def test_each_alert_is_sent_under_the_pc_it_came_from(self):
        post = MagicMock()
        s = AlertSender(config={"topic": TOPIC, "server": "https://ntfy.sh"}, post=post, sync=True, sleep=MagicMock())
        self.path.write_text("PC-ONE", encoding="utf-8")
        s.notify_unattributed_submission("ITR-4", "https://a.gov.in/x")
        self.assertIn("PC: PC-ONE", post.call_args.args[2])
        self.path.write_text("PC-TWO", encoding="utf-8")            # renamed while the app is running
        s.notify_unattributed_submission("ITR-4", "https://a.gov.in/y")
        self.assertIn("PC: PC-TWO", post.call_args.args[2])


class TestConfiguration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "vsdc_alert.json"

    def tearDown(self):
        self.tmp.cleanup()

    def _load(self, obj):
        self.path.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")
        return load_alert_config(self.path)

    def test_not_configured_means_off_and_nothing_is_sent(self):
        self.assertEqual(load_alert_config(self.path), {})
        s, post = sender(config={})
        self.assertFalse(s.enabled)
        self.assertFalse(s.notify_unattributed_submission("ITR-4", "https://a.gov.in/x"))
        post.assert_not_called()

    def test_a_valid_file_turns_it_on(self):
        cfg = self._load({"topic": TOPIC})
        self.assertEqual(cfg, {"topic": TOPIC, "server": "https://ntfy.sh"})

    def test_a_machine_name_in_the_shared_json_is_ignored(self):
        # One JSON is copied to every PC; a name in it would make every PC report the same one.
        self.assertNotIn("machine", self._load({"topic": TOPIC, "machine": "Everyone"}))

    def test_a_short_or_odd_topic_is_refused(self):
        for topic in ("sera", "has spaces in it 1234567890", "x" * 65, "../etc/passwd/../../aaaa", "topic/with/slash-1234567"):
            self.assertEqual(self._load({"topic": topic}), {}, topic)

    def test_a_non_https_or_odd_server_is_refused(self):
        for server in ("http://ntfy.sh", "ntfy.sh", "https://ntfy.sh/extra/path", "https://evil.example@ntfy.sh", "ftp://x"):
            self.assertEqual(self._load({"topic": TOPIC, "server": server}), {}, server)

    def test_a_self_hosted_https_server_is_accepted(self):
        self.assertEqual(self._load({"topic": TOPIC, "server": "https://ntfy.myoffice.example:8443"})["server"],
                         "https://ntfy.myoffice.example:8443")

    def test_a_broken_file_keeps_alerts_off(self):
        self.assertEqual(self._load("not json"), {})
        self.assertEqual(self._load("[]"), {})

    def test_environment_variables_win_over_the_file(self):
        self.path.write_text(json.dumps({"topic": "file-topic-aaaaaaaaaaaa"}), encoding="utf-8")
        with patch.dict(os.environ, {"VSDC_ALERT_TOPIC": TOPIC}):
            self.assertEqual(load_alert_config(self.path)["topic"], TOPIC)


class TestConfigAddedAfterStartup(unittest.TestCase):
    """The app is often started before vsdc_alert.json exists; that must not mean silence until a restart."""

    def test_a_config_created_while_the_app_is_running_is_picked_up(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "vsdc_alert.json"
            with patch.dict(os.environ, {"VSDC_ALERT_CONFIG": str(path)}):
                post = MagicMock()
                s = AlertSender(post=post, sync=True, sleep=MagicMock())
                s.RELOAD_INTERVAL_SEC = 0
                self.assertFalse(s.enabled)
                self.assertFalse(s.notify_unattributed_submission("ITR-4", "https://a.gov.in/x"))   # still off: no file
                post.assert_not_called()
                path.write_text(json.dumps({"topic": TOPIC}), encoding="utf-8")
                self.assertTrue(s.notify_unattributed_submission("ITR-4", "https://a.gov.in/x"))
                self.assertEqual(post.call_count, 1)

    def test_the_file_is_not_re_read_on_every_capture(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.dict(os.environ, {"VSDC_ALERT_CONFIG": str(Path(d) / "vsdc_alert.json")}):
                s = AlertSender(post=MagicMock(), sync=True, sleep=MagicMock())
                with patch("core.vsdc.vsdc_alerts.load_alert_config", return_value={}) as load:
                    for _ in range(5):
                        s.notify_unattributed_submission("ITR-4", "https://a.gov.in/x")
                self.assertEqual(load.call_count, 0, "inside the reload interval it must not touch the disk")

    def test_an_explicit_config_never_reads_the_file(self):
        s, post = sender(config={})
        with patch("core.vsdc.vsdc_alerts.load_alert_config") as load:
            s.notify_unattributed_submission("ITR-4", "https://a.gov.in/x")
        load.assert_not_called()


class TestDelivery(unittest.TestCase):
    def test_a_failure_is_retried_once_then_given_up_without_raising(self):
        s, post = sender()
        post.side_effect = OSError("network down")
        sleeper = MagicMock()
        s._sleep = sleeper
        self.assertTrue(s.notify_unattributed_submission("ITR-4", "https://a.gov.in/x"))   # queued; never raises
        self.assertEqual(post.call_count, 2)
        sleeper.assert_called_once()

    def test_a_retry_that_succeeds_stops(self):
        s, post = sender()
        post.side_effect = [OSError("blip"), None]
        s.notify_unattributed_submission("ITR-4", "https://a.gov.in/x")
        self.assertEqual(post.call_count, 2)

    def test_it_is_rate_limited(self):
        s, post = sender()
        for _ in range(AlertSender.MAX_PER_HOUR + 5):
            s.notify_unattributed_submission("ITR-4", "https://a.gov.in/x")
        self.assertEqual(post.call_count, AlertSender.MAX_PER_HOUR)

    def test_the_real_send_does_not_block_the_caller(self):
        import threading
        gate, started = threading.Event(), threading.Event()

        def slow_post(*a):
            started.set()
            gate.wait(5)

        s = AlertSender(config=CONFIG, post=slow_post, sync=False, sleep=MagicMock())
        t0 = __import__("time").time()
        self.assertTrue(s.notify_unattributed_submission("ITR-4", "https://a.gov.in/x"))
        self.assertLess(__import__("time").time() - t0, 1.0)
        self.assertTrue(started.wait(2))
        gate.set()


class TestRouterTrigger(unittest.TestCase):
    """Alerts fire for a capture with no client - and only for that."""

    from tests.test_vsdc247_integration import Harness, ITR_CONFIRMATION            # noqa: E402

    def _harness(self):
        h = self.Harness()
        post = MagicMock()
        h.router.alerts = AlertSender(config=CONFIG, post=post, sync=True, sleep=MagicMock())
        return h, post

    def test_a_capture_with_no_client_raises_one_alert(self):
        h, post = self._harness()
        h.screen(["Dashboard"], shade=245)
        h.tick()
        h.screen(self.ITR_CONFIRMATION)
        self.assertEqual(len(h.run()), 1)
        self.assertEqual(post.call_count, 1)
        body = post.call_args.args[2]
        self.assertIn("ITR-4", body)
        self.assertIn("unmapped/route", body)
        self.assertNotIn("901036690", body)

    def test_a_capture_that_already_has_its_client_raises_no_alert(self):
        h, post = self._harness()
        h.router.assembler.update_identity(pan="BJYPM4326D", name="MOHAMMAD MOLLA", portal="Income Tax")
        h.screen(["Dashboard"], shade=245)
        h.tick()
        h.screen(self.ITR_CONFIRMATION)
        self.assertEqual(len(h.run()), 1)
        post.assert_not_called()

    def test_attributing_it_later_does_not_alert_again(self):
        h, post = self._harness()
        h.screen(["Dashboard"], shade=245)
        h.tick()
        h.screen(self.ITR_CONFIRMATION)
        h.run()
        h.router.assembler.update_identity(pan="BJYPM4326D", name="MOHAMMAD MOLLA", portal="Income Tax")
        h.screen(["Dashboard"])
        h.run(4)
        self.assertEqual(post.call_count, 1)

    def test_a_possible_but_unsaved_submission_does_not_alert(self):
        h, post = self._harness()
        h.screen(["Dashboard"], shade=245)
        h.tick()
        h.screen(["Acknowledgement Number", self.ITR_CONFIRMATION[1].split(": ")[1], "ITR-4 AY 2026-27"])
        h.run(4)
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
