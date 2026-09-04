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
        self.assertFalse(d["inv_frames"])
        self.assertEqual(d["sync_revision"], 0)

    def test_beacon_payload_generation(self):
        service = SyncPeerService(
            db_path=self.sender_db,
            salt_path=self.sender_salt,
            username="SenderUser",
            inv_frames=True,
        )
        payload = service._beacon_payload()
        self.assertIn(SERA_SYNC_MAGIC.encode("utf-8"), payload)
        self.assertIn(b"SenderUser", payload)
        self.assertIn(b'"inv_frames":true', payload)

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

            res = sender_service.push_to("127.0.0.1", receiver_service._tcp_server.getsockname()[1], force_override=True)
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

    def test_inv_frames_node_rejects_incoming_push(self):
        """A node with inv_frames = True must reject all incoming database pushes."""
        sync_received_flag = []
        receiver_service = SyncPeerService(
            db_path=self.receiver_db,
            salt_path=self.receiver_salt,
            username="ReceiverUser",
            sync_port=0,
            inv_frames=True,
            on_sync_received=lambda: sync_received_flag.append(True),
        )
        receiver_service.start()

        try:
            sender_service = SyncPeerService(
                db_path=self.sender_db,
                salt_path=self.sender_salt,
                username="SenderUser",
            )

            res = sender_service.push_to("127.0.0.1", receiver_service._tcp_server.getsockname()[1], force_override=True)
            self.assertIn("skipped", res.lower())
            self.assertIn("inv_frames", res.lower())

            time.sleep(0.3)

            # Check receiver database was NOT overwritten
            with open(self.receiver_db, "rb") as f:
                rec_db_data = f.read()
            self.assertEqual(rec_db_data, b"OLD_RECEIVER_DATABASE_CONTENT")
            self.assertEqual(len(sync_received_flag), 0)

        finally:
            receiver_service.stop()

    def test_inv_frames_node_can_push_to_normal_node(self):
        """A node with inv_frames = True can push database updates to normal nodes."""
        live_sync_flag = []
        receiver_service = SyncPeerService(
            db_path=self.receiver_db,
            salt_path=self.receiver_salt,
            username="NormalFollower",
            sync_port=0,
            inv_frames=False,
            on_live_sync_received=lambda u, h: live_sync_flag.append((u, h)),
        )
        receiver_service.start()

        try:
            sender_service = SyncPeerService(
                db_path=self.sender_db,
                salt_path=self.sender_salt,
                username="InvFramesMaster",
                inv_frames=True,
            )

            # Live sync push from inv_frames master
            res = sender_service.push_to("127.0.0.1", receiver_service._tcp_server.getsockname()[1], live_update=True, force_override=True)
            self.assertIn("successfully", res.lower())

            time.sleep(0.4)

            with open(self.receiver_db, "rb") as f:
                rec_db_data = f.read()
            self.assertEqual(rec_db_data, b"SENDER_DATABASE_CONTENT_HEADER_12345")
            self.assertEqual(len(live_sync_flag), 1)

        finally:
            receiver_service.stop()

    def test_multi_inv_frames_freezes_lan_sync(self):
        """When >1 node on LAN has inv_frames active, LAN sync is frozen."""
        service = SyncPeerService(
            db_path=self.receiver_db,
            salt_path=self.receiver_salt,
            username="LocalUser",
            inv_frames=True,
        )
        service._is_bootstrapping = False
        # Register a remote peer that also has inv_frames = True
        service._peers["REMOTE-HOST:192.168.1.100"] = PeerInfo(
            username="RemoteUser",
            host="REMOTE-HOST",
            ip="192.168.1.100",
            sync_port=49157,
            last_seen=time.time(),
            inv_frames=True,
        )

        state = service.get_sync_state()
        self.assertEqual(state["status"], "LAN_SYNC_FROZEN_MULTI_INV")
        self.assertGreater(state["total_inv_frames_count"], 1)

        # Outbound sync should be blocked under frozen LAN state
        res = service.push_to("192.168.1.100", 49157)
        self.assertIn("frozen", res.lower())

    def test_telemetry_bypasses_inv_frames(self):
        """A sovereign node (inv_frames = True) must accept audit logs and tracker dumps while rejecting DB pushes."""
        tracker_received = []
        audit_received = []

        class MockDb:
            def __init__(self):
                self.dumps = []
            def store_peer_tracker_dumps(self, d):
                self.dumps.extend(d)
            def get_sync_metrics(self):
                return {"client_count": 10, "sync_revision": 100000}

        mock_db = MockDb()

        receiver_service = SyncPeerService(
            db_path=self.receiver_db,
            salt_path=self.receiver_salt,
            username="SovereignAdmin",
            sync_port=0,
            inv_frames=True,  # Sovereign Master ON
            db=mock_db,
            on_tracker_dump_received=lambda host, count: tracker_received.append((host, count)),
            on_peer_logs_received=lambda host: audit_received.append(host),
        )
        receiver_service.start()

        try:
            port = receiver_service._tcp_server.getsockname()[1]
            sender_service = SyncPeerService(
                db_path=self.sender_db,
                salt_path=self.sender_salt,
                username="StaffPC",
            )

            # 1. DB push must be rejected by sovereign master
            res_db = sender_service.push_to("127.0.0.1", port, force_override=True)
            self.assertIn("skipped", res_db.lower())
            self.assertIn("inv_frames", res_db.lower())

            # 2. Tracker dump must be accepted
            test_dumps = [{
                "portal": "Income Tax",
                "arn_number": "ARN123456",
                "captured_by": "StaffPC",
                "created_at": "2026-09-04T00:30:00"
            }]
            ok_dump = sender_service.push_tracker_dumps_to_host("127.0.0.1", test_dumps, host_port=port)
            self.assertTrue(ok_dump)
            time.sleep(0.2)
            self.assertEqual(len(mock_db.dumps), 1)
            self.assertEqual(len(tracker_received), 1)

            # 3. Audit log push must be accepted
            test_logs = [{
                "ts": "2026-09-04T00:30:00",
                "actor": "StaffUser",
                "action": "view",
                "client_id": 1,
                "detail": "Viewed client profile"
            }]
            ok_audit = sender_service.push_audit_logs_to_host("127.0.0.1", test_logs, host_port=port)
            self.assertTrue(ok_audit)
            time.sleep(0.2)
            self.assertEqual(len(audit_received), 1)

        finally:
            receiver_service.stop()

    def test_raw_payload_preserved_during_database_push(self):
        """Incoming push_database must NEVER overwrite local rawPayload.db."""
        receiver_raw = os.path.join(self.temp_dir, "rawPayload.db")
        with open(receiver_raw, "wb") as f:
            f.write(b"MY_VALUABLE_LOCAL_TRACKER_DUMPS")

        receiver_service = SyncPeerService(
            db_path=self.receiver_db,
            salt_path=self.receiver_salt,
            username="ReceiverNode",
            sync_port=0,
            inv_frames=False,
        )
        receiver_service.start()

        try:
            port = receiver_service._tcp_server.getsockname()[1]
            sender_service = SyncPeerService(
                db_path=self.sender_db,
                salt_path=self.sender_salt,
                username="SenderNode",
            )

            res = sender_service.push_to("127.0.0.1", port, force_override=True)
            self.assertIn("successfully", res.lower())
            time.sleep(0.3)

            # Check that master.db was updated
            with open(self.receiver_db, "rb") as f:
                self.assertEqual(f.read(), b"SENDER_DATABASE_CONTENT_HEADER_12345")

            # Check that local rawPayload.db was NOT overwritten
            with open(receiver_raw, "rb") as f:
                self.assertEqual(f.read(), b"MY_VALUABLE_LOCAL_TRACKER_DUMPS")

        finally:
            receiver_service.stop()


if __name__ == "__main__":
    unittest.main()

