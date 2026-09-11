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

    def test_service_edit_dialog_mode_options_and_toggling(self):
        dlg = ServiceEditDialog(self.db)
        
        modes = [dlg.mode_combo.itemData(i) for i in range(dlg.mode_combo.count())]
        self.assertEqual(modes, ["extension", "manual"])
        
        self.assertEqual(dlg.mode_combo.currentData(), "extension")
        self.assertTrue(dlg.uid_sel.isEnabled())
        self.assertTrue(dlg.pwd_sel.isEnabled())
        self.assertTrue(dlg.ext_flow_combo.isEnabled())

        idx_manual = dlg.mode_combo.findData("manual")
        dlg.mode_combo.setCurrentIndex(idx_manual)
        self.assertEqual(dlg.mode_combo.currentData(), "manual")
        self.assertFalse(dlg.uid_sel.isEnabled())
        self.assertFalse(dlg.pwd_sel.isEnabled())
        self.assertFalse(dlg.ext_flow_combo.isEnabled())

        idx_ext = dlg.mode_combo.findData("extension")
        dlg.mode_combo.setCurrentIndex(idx_ext)
        self.assertTrue(dlg.uid_sel.isEnabled())
        self.assertTrue(dlg.pwd_sel.isEnabled())
        self.assertTrue(dlg.ext_flow_combo.isEnabled())


if __name__ == "__main__":
    unittest.main()
