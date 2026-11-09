"""automation.assist_engine / set_assist_engine and the Settings → Action Buttons control."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import automation
import security
from database import SeraDatabase
from PySide6.QtWidgets import QApplication

from ui.dialogs import unified_settings_dialog as usd

_app = QApplication.instance() or QApplication([])


class TestAssistEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ini = Path(self.tmp.name) / "sub" / "settings.ini"
        patcher = mock.patch.object(automation, "_settings_ini_path", lambda: self.ini)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_default_is_extension(self):
        self.assertEqual(automation.assist_engine(), "extension")

    def test_round_trip_and_other_keys_kept(self):
        self.ini.parent.mkdir(parents=True)
        self.ini.write_text("[Automation]\nbrowser = chrome\n")
        self.assertEqual(automation.set_assist_engine("os"), "os")
        self.assertEqual(automation.assist_engine(), "os")
        self.assertIn("browser = chrome", self.ini.read_text())
        automation.set_assist_engine("extension")
        self.assertEqual(automation.assist_engine(), "extension")

    def test_bad_value_falls_back_to_extension(self):
        self.assertEqual(automation.set_assist_engine("nonsense"), "extension")

    def test_listener_fires_only_on_change(self):
        seen = []
        automation.add_engine_listener(seen.append)
        self.addCleanup(automation.remove_engine_listener, seen.append)
        automation.set_assist_engine("os")
        automation.set_assist_engine("os")
        automation.set_assist_engine("extension")
        self.assertEqual(seen, ["os", "extension"])


class TestAssistEngineDialog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ini = Path(self.tmp.name) / "settings.ini"
        patcher = mock.patch.object(automation, "_settings_ini_path", lambda: self.ini)
        patcher.start()
        self.addCleanup(patcher.stop)
        salt_path = os.path.join(self.tmp.name, "s.salt")
        security.generate_and_save_salt(salt_path)
        key = security.derive_key_hex("pw", security.load_salt(salt_path))
        self.db = SeraDatabase(os.path.join(self.tmp.name, "m.db"), key,
                               raw_db_path=os.path.join(self.tmp.name, "r.db"))
        self.dlg = usd.UnifiedSettingsDialog(self.db, actor="test")

    def tearDown(self):
        self.dlg.deleteLater()
        self.tmp.cleanup()

    def test_control_is_lazy_and_saves(self):
        self.assertFalse(hasattr(self.dlg, "assist_engine_combo"))
        self.dlg._switch_page(usd._P_ACTIONS)
        self.assertEqual(self.dlg.assist_engine_combo.currentData(), "extension")
        self.assertFalse(self.dlg._btn_save.isEnabled())
        self.dlg.assist_engine_combo.setCurrentIndex(self.dlg.assist_engine_combo.findData("os"))
        self.assertTrue(self.dlg._btn_save.isEnabled())
        self.dlg._on_save_settings()
        self.assertEqual(automation.assist_engine(), "os")
        self.assertFalse(self.dlg._btn_save.isEnabled())


if __name__ == "__main__":
    unittest.main()
