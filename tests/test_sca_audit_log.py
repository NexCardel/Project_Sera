import os
import shutil
import tempfile
import unittest

from database import SeraDatabase
from security import generate_and_save_salt, derive_key_hex
from clipboard_watch import ClipboardWatchService, Arm
from ui.dialogs.audit_log_dialog import _get_action_badge_style
from ui.services.alert_service import ActionAlertFormatter


class TestScaAuditLog(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.live_db_path = os.path.join(self.temp_dir, "master.db")
        self.live_salt_path = os.path.join(self.temp_dir, "sera.salt")

        generate_and_save_salt(self.live_salt_path)
        with open(self.live_salt_path, "rb") as f:
            salt_bytes = f.read()

        self.password = "TestPass123!"
        self.hex_key = derive_key_hex(self.password, salt_bytes)
        self.db = SeraDatabase(self.live_db_path, self.hex_key)

        # Setup column, client, and service
        pan_col = next((c for c in self.db.get_mcl_columns() if c["label"].strip().upper() == "PAN"), None)
        pan_id = pan_col["id"] if pan_col else 5
        name_id = self.db.create_mcl_column("Client Name", "text", is_identity=True)
        self.client_id = self.db.add_client({name_id: "Test Corp", pan_id: "ABCDE1234F"}, notes="Test notes", service_ids=[])

        services = self.db.get_services()
        self.service = services[0] if services else {"id": 1, "name": "GST"}
        self.service_id = self.service["id"]
        self.service_name = self.service["name"]

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_sca_ambient_autofill_logs_action(self):
        watcher = ClipboardWatchService(self.db, actor="Staff_Alice")
        arm = Arm(
            arm_id="arm_test_1",
            client_id=self.client_id,
            client_token=f"CLI-{self.client_id:05d}",
            matched_uid="ABCDE1234F",
            services={self.service_id: {"name": self.service_name, "url": "https://services.gst.gov.in"}},
            expires_at=9999999999.0,
            uses_remaining=1,
            max_uses=1,
            sca_mode="autofill"
        )
        watcher._arm = arm

        msg = {
            "type": "SCA_FILL_RESULT",
            "arm_id": "arm_test_1",
            "service_id": self.service_id,
            "result": "filled"
        }
        watcher.handle_fill_result(msg)

        logs = self.db.get_audit_logs(action="sca_autofill", resolve_names=True)
        self.assertEqual(len(logs), 1)
        entry = logs[0]
        self.assertEqual(entry["actor"], "Staff_Alice")
        self.assertEqual(entry["action"], "sca_autofill")
        self.assertEqual(entry["client_id"], self.client_id)
        self.assertEqual(entry["service_id"], self.service_id)
        self.assertEqual(entry["service_name"], self.service_name)
        self.assertIn(f"SCA Ambient Autofill filled password for {self.service_name}", entry["detail"])

    def test_sca_widget_logs_action(self):
        watcher = ClipboardWatchService(self.db, actor="Staff_Bob")
        arm = Arm(
            arm_id="arm_test_2",
            client_id=self.client_id,
            client_token=f"CLI-{self.client_id:05d}",
            matched_uid="ABCDE1234F",
            services={self.service_id: {"name": self.service_name, "url": "https://services.gst.gov.in"}},
            expires_at=9999999999.0,
            uses_remaining=1,
            max_uses=1,
            sca_mode="widget"
        )
        watcher._arm = arm

        msg = {
            "type": "SCA_FILL_RESULT",
            "arm_id": "arm_test_2",
            "service_id": self.service_id,
            "result": "filled"
        }
        watcher.handle_fill_result(msg)

        logs = self.db.get_audit_logs(action="sca_widget", resolve_names=True)
        self.assertEqual(len(logs), 1)
        entry = logs[0]
        self.assertEqual(entry["actor"], "Staff_Bob")
        self.assertEqual(entry["action"], "sca_widget")
        self.assertEqual(entry["client_id"], self.client_id)
        self.assertEqual(entry["service_id"], self.service_id)
        self.assertEqual(entry["service_name"], self.service_name)
        self.assertIn(f"SCA Widget filled password for {self.service_name}", entry["detail"])

    def test_get_audit_logs_sca_all_filter(self):
        # Insert both sca_autofill and sca_widget entries
        self.db.log_action("Staff1", "sca_autofill", client_id=self.client_id, service_id=self.service_id, detail="Fill 1")
        self.db.log_action("Staff2", "sca_widget", client_id=self.client_id, service_id=self.service_id, detail="Fill 2")
        self.db.log_action("Staff3", "view", client_id=self.client_id, detail="View 1")

        all_sca_logs = self.db.get_audit_logs(action="SCA (All)")
        self.assertEqual(len(all_sca_logs), 2)
        actions = {l["action"] for l in all_sca_logs}
        self.assertEqual(actions, {"sca_autofill", "sca_widget"})

    def test_audit_badge_styles(self):
        lbl_auto, fg_auto, bg_auto = _get_action_badge_style("sca_autofill")
        self.assertEqual(lbl_auto, "🪄 SCA Autofill")

        lbl_widg, fg_widg, bg_widg = _get_action_badge_style("sca_widget")
        self.assertEqual(lbl_widg, "⚡ SCA Widget")

        lbl_sca, fg_sca, bg_sca = _get_action_badge_style("sca")
        self.assertEqual(lbl_sca, "⚡ SCA")

    def test_alert_formatter_sca(self):
        res_auto = ActionAlertFormatter.format("sca_autofill", "Test Corp")
        self.assertIsNotNone(res_auto)
        msg_auto, level_auto = res_auto
        self.assertIn("Test Corp", msg_auto)
        self.assertEqual(level_auto, "success")

        res_widg = ActionAlertFormatter.format("sca_widget", "Test Corp")
        self.assertIsNotNone(res_widg)
        msg_widg, level_widg = res_widg
        self.assertIn("Test Corp", msg_widg)
        self.assertEqual(level_widg, "success")


if __name__ == "__main__":
    unittest.main()
