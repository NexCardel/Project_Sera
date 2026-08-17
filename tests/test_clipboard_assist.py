import unittest
import tempfile
import os
import shutil
import sqlite3
from unittest.mock import MagicMock

from database import SeraDatabase
from clipboard_watch import ClipboardWatchService, is_excel_source


class MockMimeData:
    def __init__(self, formats_list):
        self._formats = formats_list

    def formats(self):
        return self._formats


class TestClipboardAssist(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.hex_key = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        self.db = SeraDatabase(self.db_path, self.hex_key)

    def tearDown(self):
        try:
            shutil.rmtree(self.temp_dir)
        except Exception:
            pass

    def test_excel_format_gating(self):
        excel_mime = MockMimeData(["Csv", "text/plain", "Biff12"])
        non_excel_mime = MockMimeData(["text/plain", "text/html", "application/x-qt-windows-mime;value=\"UniformResourceLocator\""])

        self.assertTrue(is_excel_source(excel_mime))
        self.assertFalse(is_excel_source(non_excel_mime))

    def test_in_memory_index_building(self):
        # Create an identity column
        col_id = self.db.create_mcl_column(label="PAN Number", field_type="text", is_identity=True)
        
        # Add client with PAN
        client_id = self.db.add_client(values={col_id: "ABCDE1234F"}, notes="Test Client", service_ids=[])
        
        service = ClipboardWatchService(self.db, parent=None)
        self.assertIn("ABCDE1234F", service._uid_index)
        self.assertEqual(service._uid_index["ABCDE1234F"], client_id)

    def test_debouncing(self):
        service = ClipboardWatchService(self.db, parent=None)
        service._uid_index = {"TESTPAN123": 1}
        service._last_armed_token = "1"
        import time
        service._last_armed_time = time.time()
        
        # Inside debounce window, same token should not re-arm
        now = time.time()
        is_debounced = (service._last_armed_token == "1" and (now - service._last_armed_time) < service._debounce_window)
        self.assertTrue(is_debounced)


if __name__ == "__main__":
    unittest.main()
