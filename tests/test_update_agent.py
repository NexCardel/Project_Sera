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

QUIET = update_agent.CAPTURE_QUIET_SECONDS


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

    def _close(self, quiet, busy=False):
        return update_agent.should_close_now(self.app_dir, captures_busy=busy, seconds_since_capture=quiet)

    def test_no_pending_file(self):
        self.assertIsNone(self._close(9999))

    def test_pending_not_newer_is_ignored(self):
        self._stage(version.APP_VERSION)
        self.assertIsNone(self._close(9999))

    def test_closes_after_30s_without_a_capture(self):
        self._stage(_newer())
        self.assertEqual(self._close(QUIET), _newer())

    def test_waits_while_captures_keep_coming(self):
        self._stage(_newer())
        self.assertIsNone(self._close(QUIET - 1))
        self.assertIsNone(self._close(0))

    def test_pc_in_use_does_not_matter(self):
        # The old rule waited for 3 minutes without keyboard/mouse input; that is gone.
        self._stage(_newer())
        self.assertFalse(hasattr(update_agent, "seconds_since_input"))
        self.assertEqual(self._close(QUIET), _newer())

    def test_waits_for_capture_queue(self):
        self._stage(_newer())
        self.assertIsNone(self._close(9999, busy=True))

    def test_mandatory_flag_follows_the_same_rule(self):
        for flag in (None, False, True, "true"):
            self._stage(_newer(), mandatory=flag)
            self.assertIsNone(self._close(QUIET - 1))
            self.assertEqual(self._close(QUIET), _newer())

    def test_powershell_timestamp_is_accepted(self):
        (self.state / "pending.json").write_text(
            json.dumps({"version": _newer(), "staged_at": "2026-10-02T17:01:02.1234567+05:30"}),
            encoding="utf-8-sig")
        self.assertEqual(self._close(QUIET), _newer())

    def test_close_limit_per_day(self):
        self._stage(_newer())
        for _ in range(update_agent.MAX_CLOSES_PER_DAY):
            update_agent.record_close(self.app_dir, _newer())
        self.assertIsNone(self._close(9999))

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
