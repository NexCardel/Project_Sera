"""
test_update_agent.py
--------------------
App side of the silent updater: when the app closes itself for a staged update.
"""

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import version
from core import update_agent


def _newer():
    major, minor, patch_ = version.parse_version(version.APP_VERSION)[:3]
    return f"{major}.{minor}.{patch_ + 1}"


class TestUpdateAgent(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.state = root / "state"
        self.state.mkdir()
        self.app_dir = root / "app"
        self.app_dir.mkdir()
        p = patch.object(update_agent, "STATE_DIR", self.state)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)

    def _stage(self, ver, hours_ago=0.0, bom=True, mandatory=None):
        staged = (datetime.now().astimezone() - timedelta(hours=hours_ago)).isoformat()
        data = {"version": ver, "staged_at": staged}
        if mandatory is not None:
            data["mandatory"] = mandatory
        text = json.dumps(data)
        # PowerShell 5.1's Set-Content -Encoding UTF8 writes a BOM.
        (self.state / "pending.json").write_text(text, encoding="utf-8-sig" if bom else "utf-8")

    def _idle(self, seconds):
        return patch.object(update_agent, "seconds_since_input", return_value=seconds)

    def test_no_pending_file(self):
        with self._idle(9999):
            self.assertIsNone(update_agent.should_close_now(self.app_dir, captures_busy=False))

    def test_pending_not_newer_is_ignored(self):
        self._stage(version.APP_VERSION)
        with self._idle(9999):
            self.assertIsNone(update_agent.should_close_now(self.app_dir, captures_busy=False))

    def test_closes_when_idle(self):
        self._stage(_newer())
        with self._idle(update_agent.IDLE_SECONDS):
            self.assertEqual(update_agent.should_close_now(self.app_dir, captures_busy=False), _newer())

    def test_waits_while_in_use(self):
        self._stage(_newer())
        with self._idle(60):
            self.assertIsNone(update_agent.should_close_now(self.app_dir, captures_busy=False))

    def test_waits_for_capture_queue(self):
        self._stage(_newer())
        with self._idle(9999):
            self.assertIsNone(update_agent.should_close_now(self.app_dir, captures_busy=True))

    def test_long_wait_needs_only_short_pause(self):
        self._stage(_newer(), hours_ago=update_agent.LONG_WAIT_HOURS + 1)
        with self._idle(update_agent.LONG_WAIT_IDLE_SECONDS):
            self.assertEqual(update_agent.should_close_now(self.app_dir, captures_busy=False), _newer())

    def test_powershell_timestamp_parses(self):
        data = {"version": _newer(), "staged_at": "2026-10-02T17:01:02.1234567+05:30"}
        self.assertGreater(update_agent._staged_hours(data), 0)

    def test_close_limit_per_day(self):
        self._stage(_newer())
        for _ in range(update_agent.MAX_CLOSES_PER_DAY):
            update_agent.record_close(self.app_dir, _newer())
        with self._idle(9999):
            self.assertIsNone(update_agent.should_close_now(self.app_dir, captures_busy=False))

    def test_mandatory_update_closes_at_30s_idle(self):
        self._stage(_newer(), mandatory=True)
        with self._idle(update_agent.LONG_WAIT_IDLE_SECONDS):
            self.assertEqual(update_agent.should_close_now(self.app_dir, captures_busy=False), _newer())
        with self._idle(update_agent.LONG_WAIT_IDLE_SECONDS - 1):
            self.assertIsNone(update_agent.should_close_now(self.app_dir, captures_busy=False))

    def test_a_normal_update_still_needs_the_long_idle(self):
        for flag in (None, False, "true"):
            self._stage(_newer(), mandatory=flag)
            with self._idle(update_agent.LONG_WAIT_IDLE_SECONDS):
                self.assertIsNone(update_agent.should_close_now(self.app_dir, captures_busy=False))
            self.assertFalse(update_agent.is_mandatory(update_agent.pending_update()))

    def test_never_closes_with_a_session_open_or_rows_queued(self):
        self._stage(_newer(), mandatory=True)
        with self._idle(update_agent.LONG_WAIT_IDLE_SECONDS):
            self.assertIsNone(update_agent.should_close_now(self.app_dir, captures_busy=True))
            self.assertIsNone(update_agent.should_close_now(self.app_dir, captures_busy=False, sessions_open=True))
            self.assertEqual(update_agent.should_close_now(self.app_dir, captures_busy=False, sessions_open=False),
                             _newer())

    def test_a_mandatory_update_with_a_session_open_is_never_slower_than_a_normal_one(self):
        # A portal page left in front keeps its session "open"; the usual idle rule still closes the app.
        self._stage(_newer(), mandatory=True)
        with self._idle(update_agent.IDLE_SECONDS):
            self.assertEqual(update_agent.should_close_now(self.app_dir, captures_busy=False, sessions_open=True),
                             _newer())
        with self._idle(9999):
            self.assertIsNone(update_agent.should_close_now(self.app_dir, captures_busy=True, sessions_open=True))

    def test_mandatory_update_keeps_the_daily_close_limit(self):
        self._stage(_newer(), mandatory=True)
        for _ in range(update_agent.MAX_CLOSES_PER_DAY):
            update_agent.record_close(self.app_dir, _newer())
        with self._idle(9999):
            self.assertIsNone(update_agent.should_close_now(self.app_dir, captures_busy=False))

    def test_hud_update_ready_once_per_version(self):
        seen = set()
        self.assertTrue(update_agent.should_announce(seen, "9.9.1"))
        self.assertFalse(update_agent.should_announce(seen, "9.9.1"))
        self.assertTrue(update_agent.should_announce(seen, "9.9.2"))
        self.assertFalse(update_agent.should_announce(seen, ""))
        self.assertFalse(update_agent.should_announce(seen, None))

    def test_install_in_progress(self):
        self.assertFalse(update_agent.install_in_progress())
        (self.state / "installing.flag").write_text("x")
        self.assertTrue(update_agent.install_in_progress())

    def test_unavailable_from_source(self):
        self.assertFalse(update_agent.is_available())


if __name__ == "__main__":
    unittest.main()
