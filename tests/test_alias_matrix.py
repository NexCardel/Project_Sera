import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import security
from database import SeraDatabase


class TestAliasMatrix(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_alias.db")
        self.salt_path = os.path.join(self.temp_dir, security.SALT_FILE)
        
        security.generate_and_save_salt(self.salt_path)
        salt = security.load_salt(self.salt_path)
        self.key = security.derive_key_hex("testpass123", salt)
        self.db = SeraDatabase(self.db_path, self.key)

    def test_alias_matrix_initialization(self):
        matrix = self.db.get_staff_matrix()
        self.assertEqual(len(matrix), 6)
        self.assertEqual(matrix[0]["name"], "User 1")
        self.assertEqual(matrix[5]["name"], "User 6")

    def test_assign_or_get_alias(self):
        # 1. Assign first workstation alias
        user_name, alias = self.db.assign_or_get_alias("FrontDesk-1")
        self.assertEqual(user_name, "User 1")
        self.assertEqual(alias, "FrontDesk-1")

        # 2. Re-fetching same alias returns same canonical user
        user_name2, alias2 = self.db.assign_or_get_alias("FrontDesk-1")
        self.assertEqual(user_name2, "User 1")

        # 3. Assign second workstation alias
        user_name3, alias3 = self.db.assign_or_get_alias("Billing-PC")
        self.assertEqual(user_name3, "User 2")
        self.assertEqual(alias3, "Billing-PC")

    def test_update_and_reset_matrix(self):
        self.db.assign_or_get_alias("Station-A")
        matrix = self.db.get_staff_matrix()
        user1_id = matrix[0]["id"]

        # Update alias manually
        self.db.update_staff_alias(user1_id, "TaxDesk-Updated")
        updated_matrix = self.db.get_staff_matrix()
        self.assertEqual(updated_matrix[0]["alias"], "TaxDesk-Updated")

        # Reset matrix
        self.db.reset_staff_matrix()
        reset_matrix = self.db.get_staff_matrix()
        self.assertEqual(reset_matrix[0]["alias"], "")

if __name__ == "__main__":
    unittest.main()
