"""Capture health: states, HUD events, phone alerts (core/vsdc/vsdc_health). Fictional data only."""

import json
import os
import re
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.vsdc import vsdc_health as vh
from core.vsdc.vsdc_alerts import AlertSender


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.day = datetime(2026, 10, 14, 10, 0, 0)

    def __call__(self):
        return self.t

    def wall(self):
        return self.day + timedelta(seconds=self.t - 1000.0)

    def advance(self, sec, monitor=None, step=1.0):
        """Moves time on in steps, ticking the monitor like the capture loop does."""
        end = self.t + sec
        while self.t < end:
            self.t = min(end, self.t + step)
            if monitor is not None:
                monitor.tick()


class Rig:
    def __init__(self, alerts=True):
        self.clock = Clock()
        self.hud, self.phone, self.logs, self.records = [], [], [], []
        self.m = vh.HealthMonitor(
            clock=self.clock, wall=self.clock.wall,
            hud=lambda title, sub="": self.hud.append((title, sub)),
            phone=lambda device, state, detail: self.phone.append((device, state, detail)),
            alerts_enabled=lambda: alerts, write=self.records.append, log=self.logs.append,
            device="PC-One", version="9.9.9")

    def page(self, hwnd=1, usable=True, ms=100.0, source="uia"):
        """One tick of the router for an in-scope window with a read."""
        self.m.window(hwnd, True, "GST Portal")
        self.m.read_result(usable, ms, source)

    def blind_for(self, sec, hwnd=1):
        end = self.clock.t + sec
        while self.clock.t < end:
            self.clock.advance(1.0)
            self.page(hwnd, usable=False)
            self.m.tick()


class HealthStateTests(unittest.TestCase):
    def test_blind_in_scope_raises_within_20s(self):
        r = Rig()
        r.page()
        r.blind_for(19)
        self.assertEqual(r.m.state, "ok")
        r.blind_for(3)
        self.assertEqual(r.m.state, "blind")
        self.assertEqual(len(r.hud), 1)
        self.assertEqual(r.hud[0][0], "Capture paused on this PC")
        self.assertEqual(len(r.phone), 1)
        self.assertEqual(r.phone[0][:2], ("PC-One", "blind"))
        self.assertTrue(any(line.startswith("health state=blind") for line in r.logs))

    def test_reads_count_for_the_window_named_by_reading(self):
        """WP10: the window in front reads fine, a window behind it (looked at last) does not - the
        blind reads land on neither by accident: each read names its window."""
        r = Rig()
        for _ in range(30):
            r.clock.advance(1.0)
            r.m.window(1, True, "GST Portal")
            r.m.window(2, False, "GST Portal")       # behind, address bar only: not counted in scope
            r.m.reading(1)
            r.m.read_result(True, 100.0)
            r.m.reading(2)
            r.m.read_result(False, 100.0)
            r.m.tick()
        self.assertEqual(r.m.state, "ok")
        for _ in range(25):                            # the window in front turns blind: noticed
            r.clock.advance(1.0)
            r.m.window(1, True, "GST Portal")
            r.m.window(2, False, "GST Portal")
            r.m.reading(1)
            r.m.read_result(False, 100.0)
            r.m.tick()
        self.assertEqual(r.m.state, "blind")

    def test_blind_needs_a_read_attempt(self):
        r = Rig()
        for _ in range(40):
            r.clock.advance(1.0)
            r.m.window(1, True, "GST Portal")
            r.m.tick()
        self.assertEqual(r.m.state, "ok")

    def test_blind_clears_on_a_usable_read(self):
        r = Rig()
        r.page()
        r.blind_for(25)
        self.assertEqual(r.m.state, "blind")
        r.clock.advance(1.0)
        r.page(usable=True)
        r.m.tick()
        self.assertEqual(r.m.state, "ok")
        self.assertEqual(r.hud[-1][0], "Capture back to normal")

    def test_leaving_the_window_ends_blind(self):
        r = Rig()
        r.page()
        r.blind_for(25)
        r.clock.advance(10.0, r.m)
        self.assertNotEqual(r.m.state, "blind")

    def test_coming_back_to_a_window_starts_a_fresh_stretch(self):
        r = Rig()
        r.page()
        r.blind_for(25)
        r.clock.advance(60.0, r.m)
        r.page(usable=False)
        r.m.tick()
        self.assertEqual(r.m.state, "ok")

    def test_a_persisting_failure_repeats_hud_every_5_min_only(self):
        r = Rig()
        r.page()
        r.blind_for(25)
        self.assertEqual(len(r.hud), 1)
        r.blind_for(290)
        self.assertEqual(len(r.hud), 1)
        r.blind_for(30)
        self.assertEqual(len(r.hud), 2)
        r.blind_for(300)
        self.assertEqual(len(r.hud), 3)

    def test_phone_alert_rate_limited(self):
        r = Rig()
        r.page()
        r.blind_for(25)
        r.blind_for(1700)
        self.assertEqual(len(r.phone), 1)
        r.blind_for(200)
        self.assertEqual(len(r.phone), 2)

    def test_flapping_state_is_shown_at_most_once_a_minute(self):
        r = Rig()
        for _ in range(3):
            r.page()
            r.blind_for(21)
            r.clock.advance(1.0)
            r.page(usable=True)
            r.m.tick()
        failures = [h for h in r.hud if h[0] == "Capture paused on this PC"]
        self.assertEqual(len(failures), 1)
        self.assertEqual(len(r.phone), 1)

    def test_stall_from_the_watch_thread_is_a_state(self):
        r = Rig()
        r.clock.advance(5.0, r.m)
        r.m.stall(31.0)
        self.assertEqual(r.m.state, "stalled")
        self.assertEqual(r.phone[0][1], "stalled")
        r.clock.advance(5.0)
        r.m.stall(0.0)
        self.assertEqual(r.m.state, "ok")
        self.assertEqual(r.hud[-1][0], "Capture back to normal")

    def test_no_portal_is_never_alerted(self):
        r = Rig()
        r.clock.advance(vh.NO_PORTAL_SEC + 30, r.m, step=5.0)
        self.assertEqual(r.m.state, "no-portal")
        self.assertEqual(r.hud, [])
        self.assertEqual(r.phone, [])

    def test_informational_states_never_raise(self):
        r = Rig()
        r.m.set_pending_update("9.9.10")
        r.clock.advance(2.0, r.m)
        self.assertEqual(r.m.state, "old-build")
        r.page(ms=4500.0)
        r.m.tick()
        self.assertEqual(r.m.state, "slow")
        self.assertEqual((r.hud, r.phone), ([], []))

    def test_priority_stalled_over_blind(self):
        r = Rig()
        r.page()
        r.blind_for(25)
        self.assertEqual(r.m.state, "blind")
        r.m.stall(40.0)
        self.assertEqual(r.m.state, "stalled")
        self.assertEqual([p[1] for p in r.phone], ["blind", "stalled"])

    def test_uia_paused_state(self):
        r = Rig()
        r.m.uia_paused(True)
        r.m.tick()
        self.assertEqual(r.m.state, "uia-paused")
        r.m.uia_paused(False)
        r.m.tick()
        self.assertEqual(r.m.state, "ok")

    def test_busy_long_after_a_minute_of_skipped_reads(self):
        r = Rig()
        r.m.window(1, True, "GST Portal")
        for _ in range(61):
            r.clock.advance(1.0)
            r.m.window(1, True, "GST Portal")
            r.m.read_result(False, 0.0, "busy")
            r.m.tick()
        self.assertEqual(r.m.state, "busy-long")
        r.m.read_result(True, 200.0, "uia")
        r.m.tick()
        self.assertEqual(r.m.state, "ok")

    def test_scope_lost_after_30s_of_a_portal_title_with_no_address(self):
        r = Rig()
        for _ in range(35):
            r.clock.advance(1.0)
            r.m.window(7, False, "", titled_portal=True, url_readable=False)
            r.m.tick()
        self.assertEqual(r.m.state, "scope-lost")

    def test_specs_silent_needs_five_quiet_minutes_on_a_logged_in_page(self):
        r = Rig()
        for _ in range(290):
            r.clock.advance(1.0)
            r.m.hits("s1", False, True)
            r.m.tick()
        self.assertEqual(r.m.state, "ok")
        r.clock.advance(15.0)
        r.m.hits("s1", False, True)
        r.m.tick()
        self.assertEqual(r.m.state, "specs-silent")
        r.m.hits("s1", True, True)
        r.m.tick()
        self.assertEqual(r.m.state, "ok")

    def test_login_pages_never_count_as_silent(self):
        r = Rig()
        for _ in range(400):
            r.clock.advance(1.0)
            r.m.hits("s1", False, False)
            r.m.tick()
        self.assertEqual(r.m.state, "ok")

    def test_unlisted_portal_and_disk_are_failures(self):
        r = Rig()
        r.m.unlisted_portal()
        r.m.tick()
        self.assertEqual(r.m.state, "unlisted-portal")
        self.assertEqual(r.hud, [])                       # the router's own prompt is the HUD for this one
        self.assertEqual([p[1] for p in r.phone], ["unlisted-portal"])
        r.clock.advance(vh.UNLISTED_HOLD_SEC + 1, r.m, step=30.0)
        self.assertNotEqual(r.m.state, "unlisted-portal")
        r.m.disk_failed()
        r.m.tick()
        self.assertEqual(r.m.state, "disk")

    def test_alerts_unconfigured_is_said_once_and_never_phoned(self):
        r = Rig(alerts=False)
        r.clock.advance(vh.ALERTS_NOTICE_SEC - 5, r.m)
        self.assertEqual(r.hud, [])
        r.clock.advance(10.0, r.m)
        r.clock.advance(vh.HUD_REPEAT_SEC * 3, r.m, step=10.0)
        self.assertEqual(r.m.state, "alerts-unconfigured")
        self.assertEqual(len(r.hud), 1)
        self.assertEqual(r.phone, [])

    def test_counters_reset_at_midnight(self):
        r = Rig()
        r.clock.day = datetime(2026, 10, 14, 23, 58, 0)
        r.page()
        r.blind_for(60)
        self.assertGreater(r.m.blind_sec_today, 30)
        r.m.stall(0.0)
        r.clock.advance(120.0, r.m)
        self.assertLess(r.m.blind_sec_today, 5)

    def test_the_monitor_never_raises(self):
        m = vh.HealthMonitor(hud=lambda *a: 1 / 0, phone=lambda *a: 1 / 0, log=lambda s: 1 / 0)
        m.stall(99.0)
        m.window(1, True, "x")
        m.read_result(False, 1.0, "uia")
        m.tick()
        m.write()


class HealthRecordTests(unittest.TestCase):
    def test_record_holds_no_client_data(self):
        r = Rig()
        r.page()
        r.blind_for(25)
        r.m.stall(40.0)
        r.m.row("sent")
        rec = r.m.write()
        text = rec.to_json()
        json.loads(text)
        self.assertLess(len(text), 1024)
        for shape in (r"\b[A-Z]{5}\d{4}[A-Z]\b", r"\b\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]\b",
                      r"\b[A-Z]{2}\d{11,14}[A-Z0-9]?\b", r"https?://"):
            self.assertIsNone(re.search(shape, text), shape)
        for line in r.logs:
            self.assertIsNone(re.search(r"\b[A-Z]{5}\d{4}[A-Z]\b", line))

    def test_detail_is_redacted_and_short(self):
        self.assertEqual(vh.HealthMonitor._detail("nonexistent"), "")
        for state in vh.SENTENCES:
            self.assertLessEqual(len(vh.HealthMonitor._detail(state)), 120)

    def test_write_returns_the_record_and_logs_one_line(self):
        r = Rig()
        r.page()
        before = len(r.logs)
        rec = r.m.write({"lanes": 2})
        self.assertEqual((rec.device, rec.version, rec.state, rec.lanes), ("PC-One", "9.9.9", "ok", 2))
        self.assertEqual(r.records, [rec])
        self.assertEqual(len(r.logs) - before, 1)
        self.assertTrue(r.logs[-1].startswith("health state=ok"))

    def test_due_after_a_minute_or_a_change(self):
        r = Rig()
        self.assertFalse(r.m.due())
        r.clock.advance(61.0)
        self.assertTrue(r.m.due())
        r.m.write()
        self.assertFalse(r.m.due())
        r.m.uia_paused(True)
        r.m.tick()
        self.assertTrue(r.m.due())

    def test_row_counts(self):
        r = Rig()
        for kind in ("sent", "sent", "dead_letter", "unattributed", "ocr_only", "nonsense"):
            r.m.row(kind)
        rec = r.m.record()
        self.assertEqual((rec.rows_sent, rec.rows_dead_letter, rec.unattributed_rows, rec.ocr_only_rows),
                         (2, 1, 1, 1))


class PhoneAlertTests(unittest.TestCase):
    def _sender(self, posts, **kw):
        return AlertSender(config={"topic": "t" * 20, "server": "https://ntfy.example", "machine": "PC-A"},
                           post=lambda url, title, body, prio, tags: posts.append((url, title, body)),
                           sync=True, **kw)

    def test_capture_health_alert_text(self):
        posts = []
        s = self._sender(posts)
        self.assertTrue(s.notify_capture_health("PC-A", "blind", "Sera cannot read this page."))
        url, title, body = posts[0]
        self.assertEqual(title, "Sera: capture problem on PC-A")
        self.assertIn("State: blind", body)

    def test_capture_health_alert_is_redacted(self):
        posts = []
        s = self._sender(posts)
        s.notify_capture_health("PC-A", "blind", "ABCDE1234F and 27ABCDE1234F1Z5 and AA1234567890123")
        body = posts[0][2]
        self.assertNotIn("ABCDE1234F", body)
        self.assertNotIn("27ABCDE1234F1Z5", body)
        self.assertNotIn("AA1234567890123", body)

    def test_shares_the_hourly_limit_and_stays_quiet_when_unconfigured(self):
        posts = []
        s = self._sender(posts)
        for _ in range(AlertSender.MAX_PER_HOUR):
            self.assertTrue(s.notify_capture_health("PC-A", "blind"))
        self.assertFalse(s.notify_capture_health("PC-A", "blind"))
        self.assertFalse(s.notify_unattributed_submission("ITR-1", "/x"))
        off = AlertSender(config={}, post=lambda *a: posts.append(a), sync=True)
        self.assertFalse(off.notify_capture_health("PC-A", "blind"))
        self.assertEqual(len(posts), AlertSender.MAX_PER_HOUR)


class WiringTests(unittest.TestCase):
    """The pieces in place: SgtShadow's callbacks, the router's tick, the watch thread."""

    def test_shadow_reports_each_read_and_each_busy_skip(self):
        import tempfile
        from datetime import date
        from pathlib import Path
        from PIL import Image
        from core.sgt.sgt_shadow import SgtShadow
        from core.sgt.sgt_specs import BUILTIN_FIELDS_PATH, SpecStore
        seen, hits, clock, page = [], [], [1_000_000.0], {"v": {"lines": []}}
        with tempfile.TemporaryDirectory() as tmp:
            sgt = SgtShadow(store=SpecStore([BUILTIN_FIELDS_PATH], log=lambda m: None),
                            read_uia=lambda h: page["v"], log_dir=Path(tmp), clock=lambda: clock[0],
                            today=lambda: date(2026, 7, 15), echo=lambda m: None,
                            on_read=lambda *a: seen.append(a), on_hits=lambda *a: hits.append(a))
            url = "https://services.gst.gov.in/services/auth/fowelcome"

            def see(shade):
                clock[0] += 0.4
                sgt.observe(1, "GST Portal", url, frame=Image.new("RGB", (32, 32), (shade,) * 3))

            see(10)                                                  # nothing readable
            page["v"] = {"busy": True}
            see(60)                                                  # the previous read still runs
            page["v"] = {"lines": ["Welcome", "Dashboard", "Returns", "Services"]}
            see(120)
        self.assertEqual([(u, s) for u, _ms, s in seen], [(False, "uia"), (False, "busy"), (True, "uia")])
        self.assertTrue(all(isinstance(ms, float) for _u, ms, _s in seen))
        self.assertTrue(hits and all(h[2] is True for h in hits))

    def test_a_callback_that_raises_never_breaks_the_shadow(self):
        from core.sgt.sgt_shadow import SgtShadow
        SgtShadow._report(lambda *a: 1 / 0, 1)
        SgtShadow._report(None, 1)

    def test_the_watch_thread_tells_the_monitor_and_the_heartbeat_names_the_state(self):
        import threading
        import time as _t
        from core.vsdc import vsdc_diag

        class Worker:
            _running = True
            _paused = False
            last_progress = _t.monotonic() - 100

        told = []
        mon = type("M", (), {"state": "stalled", "stall": lambda self, s: told.append(s)})()
        w = Worker()
        os.environ[vsdc_diag.LOG_ENV] = os.path.join(os.environ.get("TEMP", "."), "vsdc_health_test.log")
        try:
            t = vsdc_diag.watch_worker(w, stall_s=1.0, monitor=mon)
            deadline = _t.time() + 5
            while not told and _t.time() < deadline:
                _t.sleep(0.1)
            w.last_progress = _t.monotonic()
            while 0.0 not in told and _t.time() < deadline:
                _t.sleep(0.1)
            w._running = False
            router = type("R", (), {"health": mon, "diag_tick": {}})()
            vsdc_diag.Heartbeat().write(router)
            with open(os.environ[vsdc_diag.LOG_ENV], encoding="utf-8") as f:
                self.assertIn("health_state=stalled", f.read())
        finally:
            os.environ.pop(vsdc_diag.LOG_ENV, None)
            try:
                os.remove(os.path.join(os.environ.get("TEMP", "."), "vsdc_health_test.log"))
            except OSError:
                pass
        self.assertGreater(told[0], 0.0)
        self.assertIn(0.0, told)
        self.assertIsInstance(t, threading.Thread)

    def test_the_router_feeds_the_monitor_every_tick_even_when_a_tick_fails(self):
        import tempfile
        from unittest.mock import MagicMock, patch
        from PIL import Image
        from core.vsdc.vsdc_assembler import VisualSessionAssembler
        from core.vsdc.vsdc_router import VSDCRouter
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"VSDC_DIAG_LOG": os.path.join(tmp, "v.log")}):
            ocr = MagicMock()
            ocr.capture_window_image.return_value = Image.new("RGB", (1280, 800), color="white")
            ocr.scan_image.return_value = {"text": "", "lines": []}
            router = VSDCRouter(ocr_engine=ocr, assembler=VisualSessionAssembler(), on_activity=lambda *a: None)
            router.get_foreground_info = MagicMock(return_value=(4242, "Income Tax Portal - Google Chrome", "chrome.exe"))
            router.extract_browser_url = MagicMock(
                return_value="https://eportal.incometax.gov.in/iec/foservices/#/dashboard")
            calls = []
            router.health.window = lambda *a, **k: calls.append(("window", a, k))
            router.health.tick = lambda: calls.append(("tick",))
            router.evaluate_tick()
            self.assertEqual([c[0] for c in calls], ["window", "tick"])
            self.assertTrue(calls[0][1][1])                           # in scope
            calls.clear()
            with patch.object(router, "_evaluate_tick", side_effect=RuntimeError("boom")):
                with self.assertRaises(RuntimeError):
                    router.evaluate_tick()
            self.assertEqual(calls, [("tick",)])


if __name__ == "__main__":
    unittest.main()
