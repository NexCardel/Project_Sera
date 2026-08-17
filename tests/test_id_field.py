import os
import tempfile
import unittest
from database import SeraDatabase
import security

class TestIdFieldAndTokens(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_sera.db")
        self.salt_path = os.path.join(self.tmp_dir.name, "test_sera.salt")

        security.generate_and_save_salt(self.salt_path)
        salt = security.load_salt(self.salt_path)
        hex_key = security.derive_key_hex("testpass123", salt)
        self.db = SeraDatabase(self.db_path, hex_key)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_id_field_creation_and_exclusivity(self):
        # Create a new column with field_type 'id'
        col_id_1 = self.db.create_mcl_column(label="Serial No", field_type="id")
        id_col = self.db.get_id_column()
        self.assertIsNotNone(id_col)
        self.assertEqual(id_col["id"], col_id_1)
        self.assertEqual(id_col["label"], "Serial No")

        # Create a second column with field_type 'id' - should displace the first
        col_id_2 = self.db.create_mcl_column(label="Client Token ID", field_type="id")
        id_col_new = self.db.get_id_column()
        self.assertIsNotNone(id_col_new)
        self.assertEqual(id_col_new["id"], col_id_2)

        # Check first column reverted to text
        cols = {c["id"]: c["field_type"] for c in self.db.get_mcl_columns()}
        self.assertEqual(cols[col_id_1], "text")
        self.assertEqual(cols[col_id_2], "id")

    def test_client_id_token_and_auto_serial(self):
        col_id = self.db.create_mcl_column(label="Ref No", field_type="id")
        
        # Add client without providing ref no value
        cid = self.db.add_client(values={}, notes="Test Client", service_ids=[])
        
        client = self.db.get_client(cid)
        self.assertIsNotNone(client)
        self.assertEqual(client["client_id_token"], str(cid))
        self.assertEqual(client["values"].get(col_id), str(cid))

if __name__ == "__main__":
    unittest.main()
