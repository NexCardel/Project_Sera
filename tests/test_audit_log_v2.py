import os
import tempfile
import shutil
import unittest
from database import SeraDatabase, PeerAuditLogManager
from security import generate_and_save_salt, derive_key_hex


class TestAuditLogV2(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.live_db_path = os.path.join(self.temp_dir, "master.db")
        self.live_salt_path = os.path.join(self.temp_dir, "sera.salt")

        generate_and_save_salt(self.live_salt_path)
        with open(self.live_salt_path, "rb") as f:
            salt_bytes = f.read()

        self.password = "TestPassword123!"
        self.hex_key = derive_key_hex(self.password, salt_bytes)
        self.db = SeraDatabase(self.live_db_path, self.hex_key)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_client_name_resolution_and_token_export(self):
        pan_col = next((c for c in self.db.get_mcl_columns() if c["label"].strip().upper() == "PAN"), None)
        pan_id = pan_col["id"] if pan_col else 5
        col_id = self.db.create_mcl_column("Company Name", "text", is_identity=True)
        cid = self.db.add_client({col_id: "Acme Corp", pan_id: "AAAAA1111A"}, notes="Test notes", service_ids=[])

        self.db.log_action("Admin", "view", client_id=cid, detail="Viewed profile")
        self.db.log_action("Admin", "manual_copy", client_id=cid, detail="Copied password")

        logs = self.db.get_audit_logs(client_id=cid, resolve_names=True)
        self.assertGreaterEqual(len(logs), 2)
        self.assertEqual(logs[0]["client_name"], "Acme Corp")
        self.assertEqual(logs[0]["client_token"], f"CLI-{cid:05d}")

        # Test CSV export token anonymization
        csv_path = os.path.join(self.temp_dir, "test_audit.csv")
        self.db.export_audit_log_csv(csv_path)

        with open(csv_path, "r", encoding="utf-8-sig") as f:
            csv_text = f.read()

        self.assertIn(f"CLI-{cid:05d}", csv_text)
        # Raw company name must not appear in exported CSV per blueprint §2.A
        self.assertNotIn("Acme Corp", csv_text)

    def test_peer_audit_log_manager(self):
        mgr = PeerAuditLogManager(self.temp_dir)
        logs = [
            {"id": 1, "ts": "2026-08-13T12:00:00", "actor": "OutsideUser", "action": "view", "client_id": 5, "detail": "Viewed client"},
            {"id": 2, "ts": "2026-08-13T12:05:00", "actor": "OutsideUser", "action": "filing_submitted", "client_id": 5, "detail": "Submitted GST R1"},
        ]
        mgr.store_peer_logs("Outside_PC", logs)

        workstations = mgr.get_peer_workstations()
        self.assertEqual(len(workstations), 1)
        self.assertEqual(workstations[0]["hostname"], "Outside_PC")

        peer_logs = mgr.get_peer_logs("Outside_PC")
        self.assertEqual(len(peer_logs), 2)
        self.assertEqual(peer_logs[0]["action"], "filing_submitted")
        self.assertEqual(peer_logs[0]["actor"], "OutsideUser")


if __name__ == "__main__":
    unittest.main()
