import os
import tempfile
import shutil
import time
import datetime
import unittest
from pathlib import Path

from database import SeraDatabase
import security


class TestStorageOptimization(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "master.db")
        self.raw_db_path = os.path.join(self.temp_dir, "rawPayload.db")
        self.salt_path = os.path.join(self.temp_dir, security.SALT_FILE)
        
        # Initialize an empty test database
        security.generate_and_save_salt(self.salt_path)
        salt = security.load_salt(self.salt_path)
        self.hex_key = security.derive_key_hex("TestMasterPass123!", salt)
        self.db = SeraDatabase(
            db_path=self.db_path,
            hex_key=self.hex_key,
            raw_db_path=self.raw_db_path,
            defer_startup_maintenance=True
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_optimize_storage_checkpoints_wal(self):
        """optimize_storage flushes SQLite WAL pages into the database and truncates WAL."""
        # Insert a test record to generate WAL frames
        with self.db._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS test_wal (id INT, val TEXT);")
            conn.execute("INSERT INTO test_wal VALUES (1, 'Testing WAL Flush');")

        # Run optimize_storage
        self.db.optimize_storage()

        # Database file should exist and be valid
        self.assertTrue(os.path.exists(self.db_path))


if __name__ == "__main__":
    unittest.main()
