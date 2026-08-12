"""
test_purge_duplicates.py
-------------------------
Unit tests for purge_duplicate_clients in database.py
"""

import os
import tempfile
import unittest
from database import SeraDatabase
import security

class TestPurgeDuplicates(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_master.db")
        self.salt_path = os.path.join(self.temp_dir, security.SALT_FILE)
        
        security.generate_and_save_salt(self.salt_path)
        salt = security.load_salt(self.salt_path)
        self.key = security.derive_key_hex("testpass123", salt)
        self.db = SeraDatabase(self.db_path, self.key)

    def test_purge_duplicates_with_different_serial_numbers(self):
        # Create 2 clients with different serial numbers ("No.") but identical Firm & Proprietor Name
        cols = self.db.get_mcl_columns()
        col_map = {c["label"]: c["id"] for c in cols}

        # Client 1
        c1_vals = {
            col_map["No."]: "1",
            col_map["NAME OF COMPANY"]: "A. H. Ranu Dresses",
            col_map["NAME OF PROPRIETOR"]: "Sk. Saheb Jada",
            col_map["GSTIN"]: "19AWBPJ5782H1Z8"
        }
        cid1 = self.db.add_client(c1_vals, notes="", service_ids=[])

        # Client 2 (Duplicate created later with different serial number 15)
        c2_vals = {
            col_map["No."]: "15",
            col_map["NAME OF COMPANY"]: "A H Ranu Dresses",
            col_map["NAME OF PROPRIETOR"]: "Sk Saheb Jada",
            col_map["GSTIN"]: "19AWBPJ5782H1Z8"
        }
        cid2 = self.db.add_client(c2_vals, notes="", service_ids=[])

        self.assertIsNotNone(cid1)
        self.assertIsNotNone(cid2)

        # Execute duplicate purging
        results = self.db.purge_duplicate_clients()

        self.assertEqual(results["deleted"], 1)
        self.assertEqual(results["groups"], 1)
        
        # Verify oldest client (cid1) was kept and cid2 was deleted
        c1 = self.db.get_client(cid1)
        c2 = self.db.get_client(cid2)
        self.assertIsNotNone(c1)
        self.assertIsNone(c2)

if __name__ == "__main__":
    unittest.main()
