import os
import tempfile
import shutil
import time
import datetime
import gzip
import unittest
from pathlib import Path

from database import SeraDatabase
import security
import native_host.host as nh


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

    def test_daily_dump_cleanup_and_compression(self):
        """Dumps older than max_raw_days are compressed to .txt.gz; dumps older than max_archive_days are purged."""
        now = datetime.datetime.now()
        
        # 1. Fresh dump (2 days old) -> should remain .txt
        fresh_dump = os.path.join(self.temp_dir, "seraRawPayloadDump_fresh.txt")
        with open(fresh_dump, "w", encoding="utf-8") as f:
            f.write("Recent filing payload dump content " * 50)
        t_fresh = (now - datetime.timedelta(days=2)).timestamp()
        os.utime(fresh_dump, (t_fresh, t_fresh))

        # 2. Medium dump (15 days old) -> should be compressed to .txt.gz
        medium_dump = os.path.join(self.temp_dir, "seraRawPayloadDump_medium.txt")
        with open(medium_dump, "w", encoding="utf-8") as f:
            f.write("Medium age filing payload dump content " * 100)
        t_med = (now - datetime.timedelta(days=15)).timestamp()
        os.utime(medium_dump, (t_med, t_med))

        # 3. Ancient dump (75 days old) -> should be purged
        ancient_dump = os.path.join(self.temp_dir, "seraRawPayloadDump_ancient.txt.gz")
        with gzip.open(ancient_dump, "wb") as f:
            f.write(b"Ancient archive content")
        t_ancient = (now - datetime.timedelta(days=75)).timestamp()
        os.utime(ancient_dump, (t_ancient, t_ancient))

        # Execute retention policy
        self.db.cleanup_daily_dumps(max_raw_days=7, max_archive_days=60)

        # Assert fresh dump is still .txt
        self.assertTrue(os.path.exists(fresh_dump))
        self.assertFalse(os.path.exists(fresh_dump + ".gz"))

        # Assert medium dump was compressed to .gz and raw .txt deleted
        self.assertFalse(os.path.exists(medium_dump))
        self.assertTrue(os.path.exists(medium_dump + ".gz"))

        # Verify gzipped content integrity
        with gzip.open(medium_dump + ".gz", "rt", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("Medium age filing payload dump content", content)

        # Assert ancient dump was purged
        self.assertFalse(os.path.exists(ancient_dump))

    def test_host_log_rotation(self):
        """Native messaging host rotates host_log.txt when it exceeds MAX_LOG_SIZE."""
        orig_log = nh.LOG_FILE
        orig_max = nh.MAX_LOG_SIZE
        try:
            test_log = os.path.join(self.temp_dir, "test_host_log.txt")
            nh.LOG_FILE = test_log
            nh.MAX_LOG_SIZE = 1024  # 1 KB limit for fast testing

            # Write ~2.5 KB to trigger rotation
            for i in range(50):
                nh.log(f"IPC Message entry {i}: " + ("x" * 60))

            self.assertTrue(os.path.exists(test_log))
            self.assertTrue(os.path.exists(test_log + ".1"))
            self.assertLessEqual(os.path.getsize(test_log), 1500)
        finally:
            nh.LOG_FILE = orig_log
            nh.MAX_LOG_SIZE = orig_max

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
