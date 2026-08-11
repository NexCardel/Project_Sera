"""
test_window_mode.py
-------------------
Unit tests for Window Display Mode setting configuration.
"""

import os
import tempfile
import unittest
from database import SeraDatabase
import security

class TestWindowModeSetting(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_master.db")
        self.salt_path = os.path.join(self.temp_dir, security.SALT_FILE)
        
        security.generate_and_save_salt(self.salt_path)
        salt = security.load_salt(self.salt_path)
        self.key = security.derive_key_hex("testpass123", salt)
        self.db = SeraDatabase(self.db_path, self.key)

    def test_window_mode_default(self):
        # Default should be fullscreen when not set
        mode = self.db.get_setting("window_mode", "fullscreen")
        self.assertEqual(mode, "fullscreen")

    def test_window_mode_save_and_retrieve(self):
        # Test saving square mode
        self.db.set_setting("window_mode", "square")
        mode = self.db.get_setting("window_mode", "fullscreen")
        self.assertEqual(mode, "square")

        # Test saving rectangular mode
        self.db.set_setting("window_mode", "rectangular")
        mode = self.db.get_setting("window_mode", "fullscreen")
        self.assertEqual(mode, "rectangular")

        # Test restoring fullscreen mode
        self.db.set_setting("window_mode", "fullscreen")
        mode = self.db.get_setting("window_mode", "fullscreen")
        self.assertEqual(mode, "fullscreen")

if __name__ == "__main__":
    unittest.main()
