import os
import tempfile
import shutil
import unittest
from database import SeraDatabase
from security import generate_and_save_salt, derive_key_hex

class TestSyncthingRestore(unittest.TestCase):
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
        self.col_id = self.db.create_mcl_column("Company Name", "text")
        self.db.add_client({self.col_id: "Original Live Company"}, notes="", service_ids=[])

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_restore_syncthing_conflict_folder_and_file(self):
        # Create a backup folder simulating Syncthing conflict files
        backup_dir = os.path.join(self.temp_dir, "syncthing_backups")
        os.makedirs(backup_dir, exist_ok=True)
        
        conflict_db = os.path.join(backup_dir, "master.sync-conflict-20260812-195836-WORKSTATION.db")
        conflict_salt = os.path.join(backup_dir, "sera.salt.sync-conflict-20260812-195836-WORKSTATION")
        
        # Save salt and create backup database
        generate_and_save_salt(conflict_salt)
        with open(conflict_salt, "rb") as f:
            conflict_salt_bytes = f.read()
            
        conflict_hex_key = derive_key_hex(self.password, conflict_salt_bytes)
        
        backup_db_inst = SeraDatabase(conflict_db, conflict_hex_key)
        col_id_bk = backup_db_inst.create_mcl_column("Company Name", "text", is_identity=True)
        backup_db_inst.add_client({col_id_bk: "Syncthing Conflict Restored Client"}, notes="", service_ids=[])
        
        # Test 1: Restore by selecting the folder
        res_summary = self.db.restore_from(backup_dir, master_password=self.password)
        self.assertIn("master.sync-conflict", res_summary)
        
        clients = self.db.search_clients("Syncthing Conflict")
        self.assertEqual(len(clients), 1)
        self.assertEqual(clients[0]["values"].get(col_id_bk), "Syncthing Conflict Restored Client")

        # Test 2: Restore by selecting the specific conflict .db file directly
        res_summary2 = self.db.restore_from(conflict_db, master_password=self.password)
        self.assertIn("master.sync-conflict", res_summary2)
        
        clients2 = self.db.search_clients("Syncthing Conflict")
        self.assertEqual(len(clients2), 1)

if __name__ == "__main__":
    unittest.main()
