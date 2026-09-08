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


if __name__ == "__main__":
    unittest.main()
