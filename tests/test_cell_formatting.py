"""
test_cell_formatting.py
------------------------
Unit tests for Search Grid cell background fill and text color formatting.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database import SeraDatabase
import security


class TestCellFormatting(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_master.db")
        self.salt_path = os.path.join(self.temp_dir, security.SALT_FILE)
        
        security.generate_and_save_salt(self.salt_path)
        salt = security.load_salt(self.salt_path)
        self.key = security.derive_key_hex("testpass123", salt)
        
        self.db = SeraDatabase(self.db_path, self.key)

    def test_cell_formatting_crud(self):
        pan_col = next((c for c in self.db.get_mcl_columns() if c["label"].strip().upper() == "PAN"), None)
        pan_id = pan_col["id"] if pan_col else 5
        client_id = self.db.add_client(values={pan_id: "ABCDE1234F"}, notes="Formatting test", service_ids=[])
        
        # 1. Set formatting
        fmt_data = [
            {"client_id": client_id, "column_key": "col_1", "bg_color": "#FFF3CD", "fg_color": "#721C24"},
            {"client_id": client_id, "column_key": "services", "bg_color": "#D4EDDA", "fg_color": "#155724"}
        ]
        self.db.bulk_set_cell_formatting(fmt_data)
        
        # 2. Retrieve formatting
        retrieved = self.db.get_cell_formatting_for_clients([client_id])
        self.assertEqual(len(retrieved), 2)
        self.assertEqual(retrieved[(client_id, "col_1")]["bg_color"], "#FFF3CD")
        self.assertEqual(retrieved[(client_id, "col_1")]["fg_color"], "#721C24")
        self.assertEqual(retrieved[(client_id, "services")]["bg_color"], "#D4EDDA")
        
        # 3. Clear formatting
        self.db.clear_cell_formatting([(client_id, "col_1")])
        retrieved_after_clear = self.db.get_cell_formatting_for_clients([client_id])
        self.assertEqual(len(retrieved_after_clear), 1)
        self.assertNotIn((client_id, "col_1"), retrieved_after_clear)
        self.assertIn((client_id, "services"), retrieved_after_clear)


if __name__ == "__main__":
    unittest.main()
