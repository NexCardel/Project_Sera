import os
import tempfile
import shutil
import time
import unittest
from sync_peer import SyncPeerService, PeerInfo, SERA_SYNC_MAGIC, prune_pre_sync_backups


class TestSeraSync(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.sender_db = os.path.join(self.temp_dir, "sender_master.db")
        self.sender_salt = os.path.join(self.temp_dir, "sender_sera.salt")
        self.receiver_db = os.path.join(self.temp_dir, "receiver_master.db")
        self.receiver_salt = os.path.join(self.temp_dir, "receiver_sera.salt")

        import security
        import sqlcipher3.dbapi2 as sqlite3
        from pathlib import Path
        self.password = "admin123"
        (Path(self.temp_dir) / "sera.key").write_text(self.password, encoding="utf-8")

        security.generate_and_save_salt(self.sender_salt)
        security.generate_and_save_salt(self.receiver_salt)
        sender_key = security.derive_key_hex(self.password, security.load_salt(self.sender_salt))
        receiver_key = security.derive_key_hex(self.password, security.load_salt(self.receiver_salt))

        s_conn = sqlite3.connect(self.sender_db)
        s_conn.execute(f"PRAGMA key = \"x'{sender_key}'\";")
        s_conn.execute("CREATE TABLE test_data (id INT, val TEXT);")
        s_conn.execute("INSERT INTO test_data VALUES (1, 'sender_content');")
        s_conn.commit()
        s_conn.close()

        r_conn = sqlite3.connect(self.receiver_db)
        r_conn.execute(f"PRAGMA key = \"x'{receiver_key}'\";")
        r_conn.execute("CREATE TABLE test_data (id INT, val TEXT);")
        r_conn.execute("INSERT INTO test_data VALUES (1, 'receiver_content');")
        r_conn.commit()
        r_conn.close()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_peer_info_serialization(self):
        peer = PeerInfo(
            username="TestUser",
            host="TEST-HOST",
            ip="192.168.1.50",
            sync_port=49157,
            app_version="2.3.4.1",
            db_mtime="2026-08-13 14:00",
            last_seen=1000.0,
        )
        d = peer.as_dict()
        self.assertEqual(d["username"], "TestUser")
        self.assertEqual(d["host"], "TEST-HOST")
        self.assertEqual(d["ip"], "192.168.1.50")
        self.assertEqual(d["app_version"], "2.3.4.1")
        self.assertEqual(d["db_mtime"], "2026-08-13 14:00")

    def test_beacon_payload_generation(self):
        service = SyncPeerService(
            db_path=self.sender_db,
            salt_path=self.sender_salt,
            username="SenderUser",
        )
        payload = service._beacon_payload()
        self.assertIn(SERA_SYNC_MAGIC.encode("utf-8"), payload)
        self.assertIn(b"SenderUser", payload)

    # test_loopback_tcp_push, test_inv_frames_node_rejects_incoming_push,
    # test_inv_frames_node_can_push_to_normal_node, test_multi_inv_frames_freezes_lan_sync,
    # test_telemetry_bypasses_inv_frames and test_raw_payload_preserved_during_database_push
    # exercised the legacy v2 whole-database push/pull protocol (push_to, request_pull_from,
    # inv_frames, push_tracker_dumps_to_host, push_audit_logs_to_host) removed in P4-1; the
    # TCP server now serves only fetch_snapshot (P0-6/P2-6), covered by test_sync_hotfix.py's
    # join-flow tests. Deleted rather than weakened (blueprint §0 rule 4).

    def test_prune_pre_sync_backups(self):
        """FIFO pruning should retain only the most recent max_keep pre-sync files."""
        # Create 10 dummy snapshots with sequential mtimes
        for i in range(10):
            db_snap = os.path.join(self.temp_dir, f"master.db.pre-sync-2026-09-01_{i:02d}0000.db")
            salt_snap = os.path.join(self.temp_dir, f"sera.salt.pre-sync-2026-09-01_{i:02d}0000")
            with open(db_snap, "wb") as f:
                f.write(b"SNAP_DB")
            with open(salt_snap, "wb") as f:
                f.write(b"SNAP_SALT")
            os.utime(db_snap, (1000 + i * 10, 1000 + i * 10))
            os.utime(salt_snap, (1000 + i * 10, 1000 + i * 10))

        # Prune keeping 5
        prune_pre_sync_backups(self.temp_dir, max_keep=5)

        remaining_dbs = [f for f in os.listdir(self.temp_dir) if f.startswith("master.db.pre-sync-")]
        remaining_salts = [f for f in os.listdir(self.temp_dir) if f.startswith("sera.salt.pre-sync-")]
        self.assertEqual(len(remaining_dbs), 5)
        self.assertEqual(len(remaining_salts), 5)

        # Ensure the 5 remaining are the newest ones (indices 5, 6, 7, 8, 9)
        for i in range(5, 10):
            self.assertIn(f"master.db.pre-sync-2026-09-01_{i:02d}0000.db", remaining_dbs)

    def test_store_peer_tracker_dumps_deduplication(self):
        """Verifies that store_peer_tracker_dumps deduplicates by dataset_key, keeping the latest record."""
        import tempfile
        import shutil
        from database import SeraDatabase
        import security

        temp_dir = tempfile.mkdtemp(prefix="sera_test_dedup_")
        try:
            salt_path = os.path.join(temp_dir, "sera.salt")
            security.generate_and_save_salt(salt_path)
            salt = security.load_salt(salt_path)
            hex_key = security.derive_key_hex("testpass123", salt)
            db_path = os.path.join(temp_dir, "master.db")
            raw_db_path = os.path.join(temp_dir, "rawPayload.db")
            db = SeraDatabase(db_path, hex_key, raw_db_path=raw_db_path, defer_startup_maintenance=True)

            # Create 4 successive peer dumps with the same dataset_key (e.g. from GST portal page navigation)
            dataset_key = "GST:27AAAAA0000A1Z5:GSTR3B:OCT_2025"
            batch_dumps = [
                {
                    "portal": "GST Portal",
                    "period_label": "Oct 2025",
                    "arn_number": "N/A",
                    "capture_method": "DOM_Tracker",
                    "status": "Not submitted",
                    "captured_by": "NodeA",
                    "created_at": "2026-09-04T12:00:01",
                    "dataset_key": dataset_key,
                    "raw_payload_json": '{"status": "Not submitted"}'
                },
                {
                    "portal": "GST Portal",
                    "period_label": "Oct 2025",
                    "arn_number": "N/A",
                    "capture_method": "DOM_Tracker",
                    "status": "Not submitted",
                    "captured_by": "NodeA",
                    "created_at": "2026-09-04T12:00:05",
                    "dataset_key": dataset_key,
                    "raw_payload_json": '{"status": "Not submitted"}'
                },
                {
                    "portal": "GST Portal",
                    "period_label": "Oct 2025",
                    "arn_number": "N/A",
                    "capture_method": "DOM_Tracker",
                    "status": "Initiated",
                    "captured_by": "NodeA",
                    "created_at": "2026-09-04T12:00:10",
                    "dataset_key": dataset_key,
                    "raw_payload_json": '{"status": "Initiated"}'
                },
                {
                    "portal": "GST Portal",
                    "period_label": "Oct 2025",
                    "arn_number": "N/A",
                    "capture_method": "DOM_Tracker",
                    "status": "Filed",
                    "captured_by": "NodeA",
                    "created_at": "2026-09-04T12:00:20",
                    "dataset_key": dataset_key,
                    "raw_payload_json": '{"status": "Filed"}'
                }
            ]

            # Store peer tracker dumps
            db.store_peer_tracker_dumps(batch_dumps)

            # Must have only 1 record for this dataset_key, and it must be the latest (status: Filed)
            records = db.get_tracker_dumps()
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["dataset_key"], dataset_key)
            self.assertEqual(records[0]["status"], "Filed")
            self.assertEqual(records[0]["created_at"], "2026-09-04T12:00:20")

            # Running deduplicate_tracker_dumps() should report 0 duplicates
            cleaned = db.deduplicate_tracker_dumps()
            self.assertEqual(cleaned, 0)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_get_network_category_reflects_monitor(self):
        service = SyncPeerService(
            db_path=self.sender_db,
            salt_path=self.sender_salt,
            username="SenderUser",
        )
        self.assertFalse(service.get_network_category()["is_public"])

        service._network_monitor.probe = lambda: {
            "is_public": True, "categories": ["public"], "method": "fake", "error": None,
        }
        service._network_monitor.poll_once()
        self.assertTrue(service.get_network_category()["is_public"])

    def test_network_monitor_started_and_stopped_with_service(self):
        service = SyncPeerService(
            db_path=self.receiver_db,
            salt_path=self.receiver_salt,
            username="ReceiverUser",
            sync_port=0,
        )
        service._network_monitor.probe = lambda: {
            "is_public": False, "categories": ["private"], "method": "fake", "error": None,
        }
        service.start()
        try:
            self.assertIsNotNone(service._network_monitor._thread)
            self.assertTrue(service._network_monitor._thread.is_alive())
        finally:
            service.stop()
        self.assertFalse(service._network_monitor._thread.is_alive())


if __name__ == "__main__":
    unittest.main()

