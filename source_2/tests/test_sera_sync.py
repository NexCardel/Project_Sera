import os
import tempfile
import shutil
import time
import unittest
from sync_peer import SyncPeerService, PeerInfo, SERA_SYNC_MAGIC


class TestSeraSync(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.sender_db = os.path.join(self.temp_dir, "sender_master.db")
        self.sender_salt = os.path.join(self.temp_dir, "sender_sera.salt")
        self.receiver_db = os.path.join(self.temp_dir, "receiver_master.db")
        self.receiver_salt = os.path.join(self.temp_dir, "receiver_sera.salt")

        with open(self.sender_db, "wb") as f:
            f.write(b"SENDER_DATABASE_CONTENT_HEADER_12345")
        with open(self.sender_salt, "wb") as f:
            f.write(b"SENDER_SALT_BYTES_67890")

        with open(self.receiver_db, "wb") as f:
            f.write(b"OLD_RECEIVER_DATABASE_CONTENT")
        with open(self.receiver_salt, "wb") as f:
            f.write(b"OLD_RECEIVER_SALT_BYTES")

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

    def test_loopback_tcp_push(self):
        sync_received_flag = []

        def on_sync_received():
            sync_received_flag.append(True)

        receiver_service = SyncPeerService(
            db_path=self.receiver_db,
            salt_path=self.receiver_salt,
            username="ReceiverUser",
            sync_port=0,
            on_sync_received=on_sync_received,
        )
        receiver_service.start()

        try:
            sender_service = SyncPeerService(
                db_path=self.sender_db,
                salt_path=self.sender_salt,
                username="SenderUser",
            )

            res = sender_service.push_to("127.0.0.1", receiver_service._tcp_server.getsockname()[1])
            self.assertIn("successfully", res.lower())

            time.sleep(0.5)

            # Check receiver database was overwritten with sender content
            with open(self.receiver_db, "rb") as f:
                rec_db_data = f.read()
            self.assertEqual(rec_db_data, b"SENDER_DATABASE_CONTENT_HEADER_12345")

            with open(self.receiver_salt, "rb") as f:
                rec_salt_data = f.read()
            self.assertEqual(rec_salt_data, b"SENDER_SALT_BYTES_67890")

            # Check pre-sync backup safety snapshot was generated
            backups = [f for f in os.listdir(self.temp_dir) if "pre-sync" in f]
            self.assertGreaterEqual(len(backups), 1)

            # Check restart signal was triggered
            self.assertTrue(len(sync_received_flag) > 0)

        finally:
            receiver_service.stop()


if __name__ == "__main__":
    unittest.main()
