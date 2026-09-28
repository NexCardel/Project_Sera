import unittest
import tempfile
import os
import shutil
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

from database import SeraDatabase
import automation
from ui.dialogs.service_manager_dialog import ServiceEditDialog, ServiceManagerDialog


class TestServiceAutomationMode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if QApplication.instance() is None:
            cls.app = QApplication(sys.argv)
        else:
            cls.app = QApplication.instance()

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_services.db")
        self.hex_key = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        self.db = SeraDatabase(self.db_path, self.hex_key)

    def tearDown(self):
        try:
            shutil.rmtree(self.temp_dir)
        except Exception:
            pass

    def test_automation_mode_normalization_in_database(self):
        sid1 = self.db.create_service(
            name="Test Service Auto",
            login_page_link="https://example.com/login",
            userid_column_id=1,
            password_column_id=2,
            username_selector="#user",
            password_selector="#pass",
            automation_mode="automated"
        )
        s1 = self.db.get_service(sid1)
        self.assertEqual(s1["automation_mode"], "extension")

        sid2 = self.db.create_service(
            name="Test Service Manual",
            login_page_link="https://example.com/manual_login",
            userid_column_id=1,
            password_column_id=2,
            username_selector="",
            password_selector="",
            automation_mode="manual"
        )
        s2 = self.db.get_service(sid2)
        self.assertEqual(s2["automation_mode"], "manual")

    def test_is_extension_and_manual_portal(self):
        ext_svc = {"name": "GST", "automation_mode": "extension"}
        man_svc = {"name": "Manual Bank", "automation_mode": "manual"}
        legacy_svc = {"name": "Legacy Portal", "automation_mode": "automated"}
        empty_svc = {"name": "Default Portal"}

        self.assertTrue(automation.is_extension_portal(ext_svc))
        self.assertFalse(automation.is_manual_portal(ext_svc))

        self.assertTrue(automation.is_manual_portal(man_svc))
        self.assertFalse(automation.is_extension_portal(man_svc))

        self.assertTrue(automation.is_extension_portal(legacy_svc))
        self.assertFalse(automation.is_manual_portal(legacy_svc))

        self.assertTrue(automation.is_extension_portal(empty_svc))
        self.assertFalse(automation.is_manual_portal(empty_svc))

    def test_service_action_mode(self):
        self.assertEqual(automation.service_action_mode({"automation_mode": "extension"}), automation.ACTION_AUTOFILL)
        self.assertEqual(automation.service_action_mode({"automation_mode": "automated"}), automation.ACTION_AUTOFILL)
        self.assertEqual(automation.service_action_mode({}), automation.ACTION_AUTOFILL)
        self.assertEqual(automation.service_action_mode({"automation_mode": "smti"}), automation.ACTION_SMTI)
        self.assertEqual(automation.service_action_mode({"automation_mode": "manual"}), automation.ACTION_MECP)
        self.assertTrue(automation.is_manual_portal({"automation_mode": "smti"}))

    def test_smti_mode_round_trips_through_database(self):
        sid = self.db.create_service(
            name="Assist Portal", login_page_link="https://example.com/login",
            userid_column_id=1, password_column_id=2,
            username_selector="", password_selector="", automation_mode="smti")
        self.assertEqual(self.db.get_service(sid)["automation_mode"], "smti")

    def test_service_edit_dialog_mode_options_and_toggling(self):
        dlg = ServiceEditDialog(self.db)

        modes = [dlg.mode_combo.itemData(i) for i in range(dlg.mode_combo.count())]
        self.assertEqual(modes, ["extension", "smti", "manual"])
        for gone in ("uid_sel", "pwd_sel", "success_sel", "arn_sel"):
            self.assertFalse(hasattr(dlg, gone))

        self.assertEqual(dlg.mode_combo.currentData(), "extension")
        self.assertTrue(dlg.ext_flow_combo.isEnabled())

        for mode in ("smti", "manual"):
            dlg.mode_combo.setCurrentIndex(dlg.mode_combo.findData(mode))
            self.assertEqual(dlg.result_data()["automation_mode"], mode)
            self.assertFalse(dlg.ext_flow_combo.isEnabled())

        dlg.mode_combo.setCurrentIndex(dlg.mode_combo.findData("extension"))
        self.assertTrue(dlg.ext_flow_combo.isEnabled())

    def test_edit_keeps_existing_selectors(self):
        svc = {"id": 1, "name": "Custom", "login_page_link": "https://custom.example/login",
               "userid_column_id": None, "password_column_id": None,
               "username_selector": "#u", "password_selector": "#p",
               "success_selector": ".ok", "arn_selector": "#arn",
               "automation_mode": "smti", "extension_flow": "single"}
        dlg = ServiceEditDialog(self.db, None, svc)
        self.assertEqual(dlg.mode_combo.currentData(), "smti")
        data = dlg.result_data()
        self.assertEqual((data["username_selector"], data["password_selector"],
                          data["success_selector"], data["arn_selector"]),
                         ("#u", "#p", ".ok", "#arn"))


if __name__ == "__main__":
    unittest.main()
