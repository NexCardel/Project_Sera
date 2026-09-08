"""
test_scc_vault_tagger.py
------------------------
Unit tests for SCC (Sera Credential Capture / Session Tagging):
- Database methods (get_scc_settings, save_scc_settings, get_client_by_pan,
  get_service_for_portal, update_client_single_field).
- SccQuickTagBanner floating widget layout and 20-second timer behavior.
"""

import unittest
import tempfile
import os
import shutil
import sys

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

from database import SeraDatabase
from ui.components.scc_tag_banner import SccQuickTagBanner


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
        self.assertEqual(len(cfg["combos"]), 4)
        self.assertEqual(cfg["combos"][0]["label"], "Combo 1")

        # Update settings
        self.db.save_scc_settings({
            "enabled": True,
            "combos": [
                {"label": "Standard-A", "value": "Aman@2024"},
                {"label": "Standard-B", "value": "Tax@12345"},
                {"label": "Standard-C", "value": "Pass#2026"},
                {"label": "Standard-D", "value": "Client@098"}
            ]
        })

        updated = self.db.get_scc_settings()
        self.assertTrue(updated["enabled"])
        self.assertEqual(updated["combos"][0]["label"], "Standard-A")
        self.assertEqual(updated["combos"][0]["value"], "Aman@2024")
        self.assertEqual(updated["combos"][1]["label"], "Standard-B")
        self.assertEqual(updated["combos"][1]["value"], "Tax@12345")

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

    def test_scc_banner_prompt_and_duration(self):
        banner = SccQuickTagBanner()
        self.assertEqual(banner._total_duration_ms, 20000)

        combos = [
            {"id": 1, "label": "Combo 1", "value": "Pass1"},
            {"id": 2, "label": "Combo 2", "value": "Pass2"},
            {"id": 3, "label": "Combo 3", "value": "Pass3"},
            {"id": 4, "label": "Combo 4", "value": "Pass4"},
        ]

        received_signals = []
        banner.password_selected.connect(
            lambda pan, name, col, pwd, lbl: received_signals.append((pan, name, col, pwd, lbl))
        )

        banner.show_prompt(
            pan="ABCDE1234F",
            client_name="Acme Corp",
            column_id=self.pwd_col_id,
            portal="Income Tax",
            combos=combos
        )

        self.assertEqual(banner._pan, "ABCDE1234F")
        self.assertEqual(banner._client_name, "Acme Corp")
        self.assertEqual(banner.combo_buttons[0].text(), "Combo 1")

        # Simulate clicking Combo 2 button
        banner._on_combo_clicked(1)

        self.assertEqual(len(received_signals), 1)
        self.assertEqual(received_signals[0][0], "ABCDE1234F")
        self.assertEqual(received_signals[0][3], "Pass2")
        self.assertEqual(received_signals[0][4], "Combo 2")

        banner.dismiss()

    def test_unified_settings_dialog_general_page(self):
        from ui.dialogs.unified_settings_dialog import UnifiedSettingsDialog
        dlg = UnifiedSettingsDialog(self.db, actor="Admin", page="general")
        self.assertIsNotNone(dlg)
        self.assertTrue(dlg.scc_check.isChecked())
        self.assertEqual(len(dlg.scc_combo_edits), 4)
        dlg.close()

    def test_unified_settings_scc_page_and_save(self):
        from ui.dialogs.unified_settings_dialog import UnifiedSettingsDialog, _P_SCC

        dlg = UnifiedSettingsDialog(self.db, actor="Admin", page="scc")
        self.assertIsNotNone(dlg)
        self.assertEqual(dlg._stack.currentWidget(), dlg._page_widgets[_P_SCC])
        self.assertFalse(dlg._btn_save.isEnabled(), "Save button should be disabled initially")

        # Edit combo 1 and combo 2
        lbl1_edit, val1_edit = dlg.scc_combo_edits[0]
        lbl2_edit, val2_edit = dlg.scc_combo_edits[1]

        lbl1_edit.setText("Firm Alpha")
        val1_edit.setText("AlphaPass@2026")
        lbl2_edit.setText("Firm Beta")
        val2_edit.setText("BetaPass#2026")

        self.assertTrue(dlg._btn_save.isEnabled(), "Save button must be enabled after editing combos")

        # Save settings
        dlg._on_save_settings()
        self.assertFalse(dlg._btn_save.isEnabled(), "Save button should be disabled after save")

        # Verify persisted values in database
        scc_settings = self.db.get_scc_settings()
        self.assertTrue(scc_settings["enabled"])
        self.assertEqual(scc_settings["combos"][0]["label"], "Firm Alpha")
        self.assertEqual(scc_settings["combos"][0]["value"], "AlphaPass@2026")
        self.assertEqual(scc_settings["combos"][1]["label"], "Firm Beta")
        self.assertEqual(scc_settings["combos"][1]["value"], "BetaPass#2026")
        dlg.close()

        # Re-open dialog to verify controls load the saved combos
        dlg2 = UnifiedSettingsDialog(self.db, actor="Admin", page="general")
        self.assertEqual(dlg2.scc_combo_edits[0][0].text(), "Firm Alpha")
        self.assertEqual(dlg2.scc_combo_edits[0][1].text(), "AlphaPass@2026")
        self.assertEqual(dlg2.scc_combo_edits[1][0].text(), "Firm Beta")
        self.assertEqual(dlg2.scc_combo_edits[1][1].text(), "BetaPass#2026")
        dlg2.close()

    def test_scc_verified_once_flow(self):
        # 1. Add a client that already has a password documented
        pan = "TESTP1234Z"
        cid = self.db.add_client(
            values={
                self.pan_col_id: pan,
                self.name_col_id: "Test Taxpayer",
                self.pwd_col_id: "OldKnownPassword@123"
            },
            notes="Regular client notes.",
            service_ids=[self.svc_id]
        )

        # 2. Verify initially is_client_scc_verified is False even though password exists
        self.assertFalse(self.db.is_client_scc_verified(pan))

        # 3. Simulate SeraApp trigger logic
        import main
        from unittest.mock import MagicMock
        mock_app = MagicMock()
        mock_app.db = self.db
        mock_app.actor = "Admin"
        mock_app.scc_banner = None
        mock_app._show_scc_quick_tag_banner = MagicMock()

        # Call _check_scc_quick_tag -> should trigger even though password exists!
        main.SeraApp._check_scc_quick_tag(mock_app, "Income Tax", pan, "Test Taxpayer")
        mock_app._show_scc_quick_tag_banner.assert_called_once()
        self.assertEqual(mock_app._show_scc_quick_tag_banner.call_args[1]["pan"], pan)

        # 4. Simulate selecting password via SCC
        mock_app._show_scc_quick_tag_banner.reset_mock()
        main.SeraApp._on_scc_password_saved(mock_app, pan, "Test Taxpayer", self.pwd_col_id, "NewVerifiedPass#2026", "Combo 3")

        # Verify client password and notes updated
        updated_client = self.db.get_client(cid)
        self.assertEqual(updated_client["values"].get(self.pwd_col_id), "NewVerifiedPass#2026")
        self.assertIn("Password verified via SCC", updated_client["notes"])
        self.assertIn("Regular client notes.", updated_client["notes"])

        # 5. Verify is_client_scc_verified is now True
        self.assertTrue(self.db.is_client_scc_verified(pan))

        # 6. Call _check_scc_quick_tag again -> should NEVER trigger!
        main.SeraApp._check_scc_quick_tag(mock_app, "Income Tax", pan, "Test Taxpayer")
        mock_app._show_scc_quick_tag_banner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
