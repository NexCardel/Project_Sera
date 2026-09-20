"""
tests/test_vsdc_hud_switch.py - Settings -> Tracker "Show HUD pill".

The switch only decides whether the pill is SHOWN. It must never affect capturing, and turning
it off while the pill is up must dismiss it at once. Needs a real QApplication because it drives
the actual widget.
"""

import os
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from core.vsdc.vsdc_engines import HUD_DEFAULT, HUD_SETTING, read_hud_enabled
from ui.components.vsdc_hud_pill import VSDCHudPill


class TestReadingTheHudSetting(unittest.TestCase):
    def test_on_unless_it_was_saved_off(self):
        self.assertEqual((HUD_SETTING, HUD_DEFAULT), ("vsdc_hud_enabled", "1"))
        self.assertTrue(read_hud_enabled(lambda k, d=None: d))
        self.assertTrue(read_hud_enabled(lambda k, d=None: "1"))
        self.assertFalse(read_hud_enabled(lambda k, d=None: "0"))
        self.assertTrue(read_hud_enabled(lambda k, d=None: None))

    def test_a_failing_lookup_leaves_the_pill_on(self):
        def boom(k, d=None):
            raise RuntimeError("db locked")
        self.assertTrue(read_hud_enabled(boom))


class TestThePillSwitch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.pill = VSDCHudPill()

    def test_it_is_on_by_default(self):
        self.assertTrue(self.pill.is_enabled)
        self.pill.show_event("capture", "Captured ITR-4", "Ack: 123456789290826")
        self.assertTrue(self.pill.isVisible())

    def test_switched_off_no_event_ever_shows_the_pill(self):
        self.pill.set_enabled(False)
        for kind in ("capture", "submit", "prompt", "start", "update", "logout"):
            self.pill.show_event(kind, "Something happened", "detail")
            self.assertFalse(self.pill.isVisible(), kind)
        self.assertFalse(self.pill.dismiss_timer.isActive())

    def test_switching_off_while_it_is_showing_dismisses_it_at_once(self):
        self.pill.show_event("capture", "Captured ITR-4", "Ack: 123456789290826")
        self.assertTrue(self.pill.isVisible())
        self.pill.set_enabled(False)
        self.assertFalse(self.pill.isVisible())
        self.assertFalse(self.pill.dismiss_timer.isActive())
        self.assertEqual(self.pill.opacity_effect.opacity(), 0.0)

    def test_switched_back_on_it_works_again_without_a_restart(self):
        self.pill.set_enabled(False)
        self.pill.show_event("capture", "Ignored", "")
        self.pill.set_enabled(True)
        self.pill.show_event("capture", "Captured ITR-4", "Ack: 123456789290826")
        self.assertTrue(self.pill.isVisible())
        self.assertEqual(self.pill.title_label.text(), "Captured ITR-4")

    def test_switching_off_leaves_no_pulse_or_size_residue(self):
        self.pill.show_event("capture", "Captured ITR-4", "Ack: 123456789290826")
        self.pill.set_enabled(False)
        self.assertNotEqual(self.pill.pulse_anim.state(), self.pill.pulse_anim.State.Running)
        self.pill.set_enabled(True)
        self.pill.show_event("submit", "Submitted", "Ack: 123456789290826")
        self.assertTrue(self.pill.isVisible())


class TestTheSettingsPageHasTheSwitch(unittest.TestCase):
    """Smoke test: the Tracker tab builds with the new row, and saves / loads it."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        import security
        from database import SeraDatabase
        self.tmp = tempfile.TemporaryDirectory()
        salt_path = os.path.join(self.tmp.name, "s.salt")
        security.generate_and_save_salt(salt_path)
        key = security.derive_key_hex("pw", security.load_salt(salt_path))
        self.db = SeraDatabase(os.path.join(self.tmp.name, "m.db"), key,
                               raw_db_path=os.path.join(self.tmp.name, "raw.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def _dialog(self):
        from ui.dialogs.unified_settings_dialog import UnifiedSettingsDialog
        return UnifiedSettingsDialog(self.db, actor="Admin", page="tracker")

    def test_the_switch_exists_and_defaults_to_on(self):
        dlg = self._dialog()
        self.assertTrue(hasattr(dlg, "vsdc_hud_check"))
        self.assertTrue(dlg.vsdc_hud_check.isChecked())

    def test_the_saved_value_is_shown_when_the_page_is_opened(self):
        self.db.set_setting("vsdc_hud_enabled", "0")
        dlg = self._dialog()
        self.assertFalse(dlg.vsdc_hud_check.isChecked())


if __name__ == "__main__":
    unittest.main()
