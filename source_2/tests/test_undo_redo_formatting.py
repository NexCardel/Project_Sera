"""
test_undo_redo_formatting.py
-----------------------------
Unit tests for Search Grid formatting Undo and Redo operations.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database import SeraDatabase
import security


class TestUndoRedoFormatting(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_master.db")
        self.salt_path = os.path.join(self.temp_dir, security.SALT_FILE)
        
        security.generate_and_save_salt(self.salt_path)
        salt = security.load_salt(self.salt_path)
        self.key = security.derive_key_hex("testpass123", salt)
        
        self.db = SeraDatabase(self.db_path, self.key)

    def test_undo_redo_simulation(self):
        client_id = self.db.add_client(values={}, notes="Undo test", service_ids=[])
        
        # 1. Initial State: no formatting
        retrieved = self.db.get_cell_formatting_for_clients([client_id])
        self.assertEqual(len(retrieved), 0)
        
        # 2. Action 1: Set fill to yellow
        op1 = {
            "prev": [{"client_id": client_id, "column_key": "col_1", "bg_color": "", "fg_color": ""}],
            "new": [{"client_id": client_id, "column_key": "col_1", "bg_color": "#FFF200", "fg_color": ""}]
        }
        self.db.bulk_set_cell_formatting(op1["new"])
        retrieved1 = self.db.get_cell_formatting_for_clients([client_id])
        self.assertEqual(retrieved1[(client_id, "col_1")]["bg_color"], "#FFF200")
        
        # 3. Undo Action 1: Restore prev
        to_clear = [(st["client_id"], st["column_key"]) for st in op1["prev"] if not (st.get("bg_color") or st.get("fg_color"))]
        if to_clear:
            self.db.clear_cell_formatting(to_clear)
            
        retrieved_undone = self.db.get_cell_formatting_for_clients([client_id])
        self.assertEqual(len(retrieved_undone), 0)
        
        # 4. Redo Action 1: Reapply new
        to_set = [st for st in op1["new"] if st.get("bg_color") or st.get("fg_color")]
        if to_set:
            self.db.bulk_set_cell_formatting(to_set)
            
        retrieved_redone = self.db.get_cell_formatting_for_clients([client_id])
        self.assertEqual(retrieved_redone[(client_id, "col_1")]["bg_color"], "#FFF200")


if __name__ == "__main__":
    unittest.main()
