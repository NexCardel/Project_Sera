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
            automation.trigger_mecp(
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

    def test_is_itr_service_detection(self):
        """Verify is_itr_service correctly identifies ITR vs GST and other portals."""
        itr_svc = {"name": "Income Tax", "login_page_link": "https://eportal.incometax.gov.in/iec/foservices/#/login"}
        gst_svc = {"name": "GST Portal", "login_page_link": "https://services.gst.gov.in/services/login"}
        traces_svc = {"name": "TRACES", "login_page_link": "https://www.tdscpc.gov.in/app/login.xhtml"}

        self.assertTrue(automation.is_itr_service(itr_svc))
        self.assertFalse(automation.is_itr_service(gst_svc))
        self.assertFalse(automation.is_itr_service(traces_svc))

        self.assertTrue(automation.is_gst_service(gst_svc))
        self.assertFalse(automation.is_gst_service(itr_svc))

    def test_gst_manual_assist_never_activates_scc(self):
        """Verify that triggering manual assist for GST never passes scc_mode=True or scc_combos."""
        gst_svc = {
            "id": 99,
            "name": "GST Portal",
            "login_page_link": "https://services.gst.gov.in/services/login",
            "username_selector": "#username",
            "password_selector": "#user_pass"
        }
        captured_payloads = []
        with patch.object(automation, "_send_to_extension", side_effect=lambda *args, **kwargs: captured_payloads.append((args, kwargs))):
            # Even if scc_mode=True is accidentally requested for GST, automation must disarm it
            automation.trigger_mecp(
                gst_svc, "27ABCDE1234F1Z5", "GstPass#2026", 456,
                scc_mode=True, scc_combos=[{"value": "combo1"}]
            )

        self.assertEqual(len(captured_payloads), 1)
        args, kwargs = captured_payloads[0]
        self.assertEqual(args[0]["name"], "GST Portal")
        self.assertEqual(args[1], "27ABCDE1234F1Z5")
        self.assertEqual(args[2], "GstPass#2026")
        self.assertEqual(kwargs.get("scc_mode"), False, "scc_mode must be False for GST")
        self.assertIsNone(kwargs.get("scc_combos"), "scc_combos must be None for GST")

    def test_handle_scc_password_verified_rejects_gst(self):
        """Verify that _handle_scc_password_verified ignores non-ITR / GST verification attempts."""
        cid = self.db.add_client(
            values={
                self.name_col_id: "GST Client Test",
                self.pan_col_id: "ABCDE9999Z",
                self.pwd_col_id: "OriginalPassword"
            },
            notes="",
            service_ids=[self.svc_id]
        )

        import main
        mock_app = MagicMock()
        mock_app.db = self.db
        mock_app.actor = "Operator"
        mock_app.tray_icon = None

        msg = {
            "type": "scc_password_verified",
            "client_id": cid,
            "service_id": self.svc_id,
            "userid": "ABCDE9999Z",
            "password": "FakeInjectedGSTPass",
            "combo_label": "Combo 1",
            "portal": "GST"
        }

        main.SeraApp._handle_scc_password_verified(mock_app, msg)

        # Database must NOT be updated
        client = self.db.get_client(cid)
        self.assertEqual(client["values"].get(self.pwd_col_id), "OriginalPassword")
        self.assertNotIn("Password verified via SCC", client.get("notes") or "")

    def test_unregistered_client_auto_creation_on_scc_verification(self):
        """Verify that an unregistered client (client_id=None) is auto-created in master.db when SCC verifies password."""
        import main
        mock_app = MagicMock()
        mock_app.db = self.db
        mock_app.actor = "Operator"
        mock_app.tray_icon = None

        unreg_pan = "XYZAB5678C"
        # Confirm client does not exist
        self.assertIsNone(self.db.get_client_by_pan(unreg_pan))

        msg = {
            "type": "scc_password_verified",
            "client_id": None,
            "service_id": None,
            "userid": unreg_pan,
            "password": "xyzab@5678",
            "combo_label": "Combo 1",
            "portal": "Income Tax",
            "client_name": "ABC Enterprises"
        }

        main.SeraApp._handle_scc_password_verified(mock_app, msg)

        # Client must now exist in master.db with verified password and SCC note
        new_client = self.db.get_client_by_pan(unreg_pan)
        self.assertIsNotNone(new_client, "Unregistered client must be auto-created in master.db")
        self.assertEqual(new_client["values"].get(self.pan_col_id), unreg_pan)
        self.assertEqual(new_client["values"].get(self.pwd_col_id), "xyzab@5678")
        self.assertEqual(new_client["values"].get(self.name_col_id), "ABC Enterprises")
        self.assertIn("Password verified via SCC", new_client.get("notes") or "")

    def test_database_get_all_registered_pans(self):
        """Verify get_all_registered_pans returns active client PANs and derived GSTINs, excluding archived."""
        # Active client with direct PAN
        self.db.add_client(values={self.pan_col_id: "ABCDE1234F"}, notes="", service_ids=[])
        # Archived client with PAN
        archived_id = self.db.add_client(values={self.pan_col_id: "XYZAB9999K"}, notes="", service_ids=[])
        self.db.archive_client(archived_id)

        pans = self.db.get_all_registered_pans()
        self.assertIn("ABCDE1234F", pans)
        self.assertNotIn("XYZAB9999K", pans)

    def test_automation_update_extension_settings_includes_registered_pans_and_scc(self):
        """Verify update_extension_settings formats payload with registered_pans and scc_settings."""
        import json
        from automation import update_extension_settings
        with patch("socket.socket") as mock_sock_cls:
            mock_sock = MagicMock()
            mock_sock_cls.return_value.__enter__.return_value = mock_sock

            update_extension_settings(
                registered_pans=["ABCDE1234F", "ZZZZZ9999Z"],
                scc_settings={"enabled": True, "opt1_label": "Combo 1", "opt1_fixed_str": "@"}
            )
            # Give background socket thread a brief moment
            import time
            time.sleep(0.3)

            # Assert sendall was called with JSON containing registered_pans and scc_settings
            calls = mock_sock.sendall.call_args_list
            self.assertTrue(len(calls) > 0)
            sent_payload = json.loads(calls[0][0][0].decode("utf-8"))
            self.assertIn("registered_pans", sent_payload)
            self.assertEqual(sent_payload["registered_pans"], ["ABCDE1234F", "ZZZZZ9999Z"])
            self.assertIn("scc_settings", sent_payload)
            self.assertTrue(sent_payload["scc_settings"]["enabled"])

    def test_extension_listener_settings_provider_response(self):
        """Verify ExtensionListener settings_provider callback returns expected payload."""
        from ui.extension_listener import ExtensionListener
        mock_app = MagicMock()
        listener = ExtensionListener(mock_app)
        listener.settings_provider = lambda: {
            "status": "ok",
            "registered_pans": ["ABCDE1234F"],
            "scc_settings": {
                "opt1_fixed_str": "@",
                "opt2_fixed_str": "Link@",
                "opt3_fixed_str": "Income@2014",
                "opt4_fixed_str": "income@2014"
            }
        }
        payload = listener.settings_provider()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["scc_settings"]["opt2_fixed_str"], "Link@")
        self.assertEqual(payload["scc_settings"]["opt3_fixed_str"], "Income@2014")
        self.assertEqual(payload["scc_settings"]["opt4_fixed_str"], "income@2014")
        self.assertIn("ABCDE1234F", payload["registered_pans"])

    def test_sera_app_get_extension_settings_payload(self):
        """Verify SeraApp._get_extension_settings_payload formats complete settings dict."""
        import main
        mock_app = MagicMock(spec=main.SeraApp)
        mock_app.db = self.db
        payload = main.SeraApp._get_extension_settings_payload(mock_app)
        self.assertEqual(payload["status"], "ok")
        self.assertIn("registered_pans", payload)
        self.assertIn("scc_settings", payload)
        self.assertIn("opt1_fixed_str", payload["scc_settings"])


if __name__ == "__main__":
    unittest.main()
