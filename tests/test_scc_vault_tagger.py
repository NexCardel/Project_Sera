"""
test_scc_vault_tagger.py
------------------------
Unit tests for SCC (Sera Credential Capture / In-Browser SMTI):
- Database methods (get_scc_settings, save_scc_settings, get_client_by_pan,
  get_service_for_portal, update_client_single_field, is_client_scc_verified, tag_client_scc_verified).
- SMTI SCC payload generation with scc_mode and 4 combos.
- Password verification via link mutation handler (_handle_scc_password_verified) in main.py.
- Settings dialog SCC tab and dirty-state tracking.
"""

import unittest
import tempfile
import os
import shutil
import sys
from unittest.mock import MagicMock, patch

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

from database import SeraDatabase
import automation


class TestSccVaultTagger(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if QApplication.instance() is None:
            cls.app = QApplication(sys.argv)
        else:
            cls.app = QApplication.instance()

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_scc.db")
        self.hex_key = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        self.db = SeraDatabase(self.db_path, self.hex_key)

        # Resolve existing columns and service created by default settings.ini
        cols = {c["label"]: c["id"] for c in self.db.get_mcl_columns()}
        self.pan_col_id = cols.get("PAN") or self.db.create_mcl_column("PAN", "alphanumeric", is_identity=1, is_internal_pk=1)
        self.name_col_id = cols.get("NAME OF COMPANY") or self.db.create_mcl_column("NAME OF COMPANY", "text", is_identity=1)
        self.pwd_col_id = cols.get("IT_Password") or self.db.create_mcl_column("IT_Password", "password", is_identity=0)

        svc = self.db.get_service_for_portal("Income Tax")
        if svc:
            self.svc_id = svc["id"]
        else:
            self.svc_id = self.db.create_service(
                name="Income Tax",
                login_page_link="https://eportal.incometax.gov.in/iec/foservices/#/login",
                userid_column_id=self.pan_col_id,
                password_column_id=self.pwd_col_id,
                username_selector="#panAdhaarUserId",
                password_selector="input[type='password']",
                automation_mode="extension",
                extension_flow="double"
            )

    def tearDown(self):
        try:
            shutil.rmtree(self.temp_dir)
        except Exception:
            pass

    def test_scc_settings_defaults_and_updates(self):
        cfg = self.db.get_scc_settings()
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["opt1_label"], "Combo 1")
        self.assertEqual(cfg["opt1_fixed_str"], "@")
        self.assertEqual(cfg["opt2_label"], "Combo 2")
        self.assertEqual(cfg["opt2_fixed_str"], "")
        self.assertEqual(cfg["opt3_label"], "Combo 3")
        self.assertEqual(cfg["opt3_fixed_str"], "")
        self.assertEqual(cfg["opt4_label"], "Combo 4")
        self.assertEqual(cfg["opt4_fixed_str"], "")

        # Update settings
        self.db.save_scc_settings({
            "enabled": True,
            "opt1_label": "Formula 1",
            "opt1_fixed_str": "#",
            "opt2_label": "Formula 2",
            "opt2_fixed_str": "TaxPass@",
            "opt3_label": "Fixed Master",
            "opt3_fixed_str": "FirmMaster@2026",
            "opt4_label": "Fixed Backup",
            "opt4_fixed_str": "BackupPass#1"
        })

        updated = self.db.get_scc_settings()
        self.assertTrue(updated["enabled"])
        self.assertEqual(updated["opt1_label"], "Formula 1")
        self.assertEqual(updated["opt1_fixed_str"], "#")
        self.assertEqual(updated["opt2_label"], "Formula 2")
        self.assertEqual(updated["opt2_fixed_str"], "TaxPass@")
        self.assertEqual(updated["opt3_label"], "Fixed Master")
        self.assertEqual(updated["opt3_fixed_str"], "FirmMaster@2026")
        self.assertEqual(updated["opt4_label"], "Fixed Backup")
        self.assertEqual(updated["opt4_fixed_str"], "BackupPass#1")

    def test_extract_pan_and_generate_scc_passwords(self):
        pan = "ABCDE1234F"
        letters, digits = self.db.extract_pan_components(pan)
        self.assertEqual(letters, "abcd")
        self.assertEqual(digits, "1234")

        self.db.save_scc_settings({
            "enabled": True,
            "opt1_label": "Combo 1",
            "opt1_fixed_str": "@",
            "opt2_label": "Combo 2",
            "opt2_fixed_str": "Tax#",
            "opt3_label": "Combo 3",
            "opt3_fixed_str": "FirmMaster@2026",
            "opt4_label": "Combo 4",
            "opt4_fixed_str": "BackupPass#1"
        })

        combos = self.db.generate_scc_passwords(pan)
        self.assertEqual(len(combos), 4)
        # Option 1: first 4 letters (lowercase) + fixed string + 4 digits
        self.assertEqual(combos[0]["label"], "Combo 1")
        self.assertEqual(combos[0]["value"], "abcd@1234")
        # Option 2: fixed string + 4 digits
        self.assertEqual(combos[1]["label"], "Combo 2")
        self.assertEqual(combos[1]["value"], "Tax#1234")
        # Option 3: plain fixed string
        self.assertEqual(combos[2]["label"], "Combo 3")
        self.assertEqual(combos[2]["value"], "FirmMaster@2026")
        # Option 4: plain fixed string
        self.assertEqual(combos[3]["label"], "Combo 4")
        self.assertEqual(combos[3]["value"], "BackupPass#1")

    def test_get_client_by_pan(self):
        client_id = self.db.add_client(
            values={
                self.pan_col_id: "ABCDE1234F",
                self.name_col_id: "Acme Corp"
            },
            notes="Test client",
            service_ids=[self.svc_id]
        )

        # Lookup by exact PAN
        client = self.db.get_client_by_pan("ABCDE1234F")
        self.assertIsNotNone(client)
        self.assertEqual(client["id"], client_id)
        self.assertEqual(client["values"].get(self.pan_col_id), "ABCDE1234F")

        # Lookup by lowercase PAN (case-insensitive)
        client_lower = self.db.get_client_by_pan("abcde1234f")
        self.assertIsNotNone(client_lower)
        self.assertEqual(client_lower["id"], client_id)

        # Lookup non-existent PAN
        self.assertIsNone(self.db.get_client_by_pan("NONEXIST99"))

    def test_get_service_for_portal(self):
        # By exact name
        s1 = self.db.get_service_for_portal("Income Tax")
        self.assertIsNotNone(s1)
        self.assertEqual(s1["id"], self.svc_id)
        self.assertEqual(s1["password_column_id"], self.pwd_col_id)

        # By alias keyword
        s2 = self.db.get_service_for_portal("ITR")
        self.assertIsNotNone(s2)
        self.assertEqual(s2["id"], self.svc_id)

    def test_update_client_single_field(self):
        client_id = self.db.add_client(
            values={
                self.pan_col_id: "ABCDE1234F",
                self.name_col_id: "Acme Corp"
            },
            notes="Test client without password",
            service_ids=[self.svc_id]
        )

        client = self.db.get_client(client_id)
        self.assertNotIn(self.pwd_col_id, client["values"])

        # Update password single field
        res = self.db.update_client_single_field(
            client_id=client_id,
            column_id=self.pwd_col_id,
            value="ResolvedPassword@2026",
            actor="Operator-1"
        )
        self.assertTrue(res)

        # Verify updated in client values
        refreshed = self.db.get_client(client_id)
        self.assertEqual(refreshed["values"].get(self.pwd_col_id), "ResolvedPassword@2026")

        # Verify audit log entry
        audit_entries = self.db.get_audit_logs(client_id=client_id)
        self.assertTrue(any("SCC Quick-Tag" in (e.get("detail") or "") for e in audit_entries))

    def test_is_client_scc_verified_by_pan_and_client_id(self):
        pan = "GZEPM6367M"
        cid = self.db.add_client(
            values={
                self.pan_col_id: pan,
                self.name_col_id: "Wasil Mandal"
            },
            notes="Initial notes.",
            service_ids=[self.svc_id]
        )

        # Before tagging
        self.assertFalse(self.db.is_client_scc_verified(pan=pan))
        self.assertFalse(self.db.is_client_scc_verified(client_id=cid))

        # Tag client
        self.db.tag_client_scc_verified(cid, combo_label="Combo 1")

        # After tagging
        self.assertTrue(self.db.is_client_scc_verified(pan=pan))
        self.assertTrue(self.db.is_client_scc_verified(client_id=cid))

        client = self.db.get_client(cid)
        self.assertIn("Password verified via SCC", client["notes"])

    def test_automation_manual_assist_payload_with_scc(self):
        service = {
            "id": self.svc_id,
            "name": "Income Tax",
            "login_page_link": "https://eportal.incometax.gov.in/iec/foservices/#/login",
            "username_selector": "#panAdhaarUserId",
            "password_selector": "#passwordInput",
        }
        combos = [
            {"label": "Combo 1", "value": "Income@2024"},
            {"label": "Combo 2", "value": "Aman@123"},
        ]

        captured_payloads = []
        with patch.object(automation, "_send_to_extension", side_effect=lambda *args, **kwargs: captured_payloads.append((args, kwargs))):
            automation.trigger_manual_assist(
                service, "ABCDE1234F", "", 123,
                scc_mode=True, scc_combos=combos
            )

        self.assertEqual(len(captured_payloads), 1)
        args, kwargs = captured_payloads[0]
        self.assertEqual(args[0]["name"], "Income Tax")
        self.assertEqual(args[1], "ABCDE1234F")
        self.assertEqual(kwargs.get("scc_mode"), True)
        self.assertEqual(kwargs.get("scc_combos"), combos)

    def test_unified_settings_dialog_general_page(self):
        from ui.dialogs.unified_settings_dialog import UnifiedSettingsDialog
        dlg = UnifiedSettingsDialog(self.db, actor="Admin", page="general")
        self.assertIsNotNone(dlg)
        self.assertTrue(dlg.scc_check.isChecked())
        self.assertIsNotNone(dlg.scc_opt1_label_edit)
        self.assertIsNotNone(dlg.scc_opt1_str_edit)
        self.assertIsNotNone(dlg.scc_opt2_label_edit)
        self.assertIsNotNone(dlg.scc_opt2_str_edit)
        self.assertIsNotNone(dlg.scc_opt3_label_edit)
        self.assertIsNotNone(dlg.scc_opt3_str_edit)
        self.assertIsNotNone(dlg.scc_opt4_label_edit)
        self.assertIsNotNone(dlg.scc_opt4_str_edit)
        dlg.close()

    def test_unified_settings_scc_page_and_save(self):
        from ui.dialogs.unified_settings_dialog import UnifiedSettingsDialog, _P_SCC

        dlg = UnifiedSettingsDialog(self.db, actor="Admin", page="scc")
        self.assertIsNotNone(dlg)
        self.assertEqual(dlg._stack.currentWidget(), dlg._page_widgets[_P_SCC])
        self.assertFalse(dlg._btn_save.isEnabled(), "Save button should be disabled initially")

        # Edit Option 1 through 4
        dlg.scc_opt1_label_edit.setText("Primary Opt")
        dlg.scc_opt1_str_edit.setText("#")
        dlg.scc_opt2_label_edit.setText("Secondary Opt")
        dlg.scc_opt2_str_edit.setText("Aman@")
        dlg.scc_opt3_label_edit.setText("Master Pass")
        dlg.scc_opt3_str_edit.setText("FirmAdmin#2026")
        dlg.scc_opt4_label_edit.setText("Legacy Pass")
        dlg.scc_opt4_str_edit.setText("OldFirm@123")

        self.assertTrue(dlg._btn_save.isEnabled(), "Save button must be enabled after editing options")

        # Save settings
        dlg._on_save_settings()
        self.assertFalse(dlg._btn_save.isEnabled(), "Save button should be disabled after save")

        # Verify persisted values in database
        scc_settings = self.db.get_scc_settings()
        self.assertTrue(scc_settings["enabled"])
        self.assertEqual(scc_settings["opt1_label"], "Primary Opt")
        self.assertEqual(scc_settings["opt1_fixed_str"], "#")
        self.assertEqual(scc_settings["opt2_label"], "Secondary Opt")
        self.assertEqual(scc_settings["opt2_fixed_str"], "Aman@")
        self.assertEqual(scc_settings["opt3_label"], "Master Pass")
        self.assertEqual(scc_settings["opt3_fixed_str"], "FirmAdmin#2026")
        self.assertEqual(scc_settings["opt4_label"], "Legacy Pass")
        self.assertEqual(scc_settings["opt4_fixed_str"], "OldFirm@123")
        dlg.close()

        # Re-open dialog to verify controls load the saved options
        dlg2 = UnifiedSettingsDialog(self.db, actor="Admin", page="general")
        self.assertEqual(dlg2.scc_opt1_label_edit.text(), "Primary Opt")
        self.assertEqual(dlg2.scc_opt1_str_edit.text(), "#")
        self.assertEqual(dlg2.scc_opt2_label_edit.text(), "Secondary Opt")
        self.assertEqual(dlg2.scc_opt2_str_edit.text(), "Aman@")
        self.assertEqual(dlg2.scc_opt3_label_edit.text(), "Master Pass")
        self.assertEqual(dlg2.scc_opt3_str_edit.text(), "FirmAdmin#2026")
        self.assertEqual(dlg2.scc_opt4_label_edit.text(), "Legacy Pass")
        self.assertEqual(dlg2.scc_opt4_str_edit.text(), "OldFirm@123")
        dlg2.close()

    def test_handle_scc_password_verified_saves_and_marks_notes(self):
        pan = "TESTP9999Z"
        cid = self.db.add_client(
            values={
                self.pan_col_id: pan,
                self.name_col_id: "Wasil Taxpayer",
            },
            notes="Active client.",
            service_ids=[self.svc_id]
        )

        import main
        mock_app = MagicMock()
        mock_app.db = self.db
        mock_app.actor = "Operator"
        mock_app.tray_icon = None
        mock_app.client_detail_win = None
        mock_app.shell = None

        # Simulate message received from extension link mutation
        msg = {
            "type": "scc_password_verified",
            "client_id": cid,
            "service_id": self.svc_id,
            "userid": pan,
            "password": "CorrectWorkingPass#2026",
            "combo_label": "Combo 3",
            "portal": "Income Tax"
        }

        main.SeraApp._handle_scc_password_verified(mock_app, msg)

        # Verify client password and notes updated in database
        updated_client = self.db.get_client(cid)
        self.assertEqual(updated_client["values"].get(self.pwd_col_id), "CorrectWorkingPass#2026")
        self.assertIn("Password verified via SCC", updated_client["notes"])
        self.assertTrue(self.db.is_client_scc_verified(pan=pan))
        self.assertTrue(self.db.is_client_scc_verified(client_id=cid))

    def test_get_client_pan_avoids_company_name_collision(self):
        # Client where 'NAME OF COMPANY' contains 'PAN' substring (e.g. 'Panchayat Dresses' or any company)
        cid = self.db.add_client(
            values={
                self.name_col_id: "Panchayat Dresses",
                self.pan_col_id: "ABCDE1234F",
            },
            notes="",
            service_ids=[self.svc_id]
        )
        resolved_pan = self.db.get_client_pan(cid)
        self.assertEqual(resolved_pan, "ABCDE1234F")

        combos = self.db.generate_scc_passwords(pan=resolved_pan)
        self.assertEqual(combos[0]["value"], "abcd@1234")


if __name__ == "__main__":
    unittest.main()
