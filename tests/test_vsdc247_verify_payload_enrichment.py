"""
tests/test_vsdc247_verify_payload_enrichment.py
================================================
Tests for SGT payload enrichment with VSDC247-V verification block.
Validates:
  1. SGT payload includes top-level and raw_payload vsdc247-v section when verified.
  2. SGT payload includes unverified section when ARN was not seen by OCR or is N/A.
  3. VSDC247-V does NOT create standalone duplicate entries in tracker_dump or evaluate_tick.
  4. Retroactive reconciliation via annotate_vsdc247 enriches the existing SGT row.
"""

import contextlib
import json
import sqlite3
import unittest
from unittest.mock import MagicMock

from core.vsdc.vsdc_router import VSDCRouter
from core.vsdc.vsdc247_verify import Verifier
from core.vsdc.vsdc247_reconciler import Reconciler, SgtRecord, build_vsdc247_block, OUTCOME_MATCHED
from sera_db.tracker_dump import TrackerDumpMixin


class FakeDb(TrackerDumpMixin):
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("""
            CREATE TABLE tracker_dump (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER,
                unassigned_identity TEXT,
                service_id INTEGER,
                portal TEXT,
                period_label TEXT,
                arn_number TEXT,
                capture_method TEXT,
                status TEXT,
                raw_payload_json TEXT,
                captured_by TEXT,
                created_at TEXT,
                dataset_key TEXT,
                notes TEXT,
                screenshot_path TEXT
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                is_archived INTEGER DEFAULT 0
            )
        """)

    @contextlib.contextmanager
    def _connect_raw(self):
        yield self.conn

    @contextlib.contextmanager
    def _connect(self):
        yield self.conn

    def _extract_identity_candidates_from_payload(self, **kwargs):
        return []

    def _upgrade_client_name_if_placeholder(self, *args, **kwargs):
        pass

    def _update_srpf_container(self, *args, **kwargs):
        pass

    def _bump_sync_revision_if_configured(self):
        pass

    def enrich_client_from_capture(self, *args, **kwargs):
        pass


class TestVsdc247PayloadEnrichment(unittest.TestCase):

    def setUp(self):
        from unittest.mock import patch
        self._dev_patch = patch("core.vsdc.vsdc_router.device_name", return_value="TestPC")
        self._dev_patch.start()
        self.addCleanup(self._dev_patch.stop)

        self.db = FakeDb()
        self.router = VSDCRouter(ocr_engine=MagicMock())
        self.router.alerts = MagicMock()
        self.router._health_hud = MagicMock()
        self.router.db = self.db

    def test_sgt_payload_enriched_when_verified_by_ocr(self):
        """When OCR has verified the ARN, SGT payload gets vsdc247-v section with verified=True."""
        self.router.apply_engine_settings(vsdc=False, vsdc_x=False, vsdc247=False, sgt="live", vsdc247_verify=True)
        arn = "AA2703260123456"

        # Register ARN in verifier ledger
        self.router._verifier.saved[arn] = {
            "arn": arn,
            "form": "GSTR-1",
            "period": "September (FY 2026-27)",
            "page_kind": "gst_ack",
            "score": 98,
            "tier": "certain",
            "seen_at": "2026-10-18T16:30:00",
            "client": {"username": "TEST_USER_01", "agreement": "agrees"},
            "page_url": "services.gst.gov.in/services/auth/fowelcome",
            "device_name": "TestPC",
            "confirmed_by": "single_frame",
        }

        # Mock SGT payload waiting in dispatch outbox
        sgt_payload = {
            "source": "sgt",
            "portal": "GST Portal",
            "arn": arn,
            "filing_type": "GSTR-1",
            "period_label": "September (FY 2026-27)",
            "status": "Submitted",
            "capture_method": "SGT_live",
            "client_name": "TEST CLIENT",
            "raw_payload": {
                "source": {"engine": "SGT"},
                "sgt_dataset": {"arn": arn, "form": "GSTR-1"},
            },
        }
        self.router._sgt = MagicMock()
        self.router._sgt.pending.return_value = True
        self.router._sgt.pop_dispatch.return_value = dict(sgt_payload)

        row = self.router.evaluate_tick()
        self.assertIsNotNone(row)
        self.assertEqual(row["arn"], arn)

        # Check top-level vsdc247-v section
        self.assertIn("vsdc247-v", row)
        v = row["vsdc247-v"]
        self.assertTrue(v["verified"])
        self.assertEqual(v["status"], "verified")
        self.assertEqual(v["arn"], arn)
        self.assertEqual(v["form"], "GSTR-1")
        self.assertEqual(v["period"], "September (FY 2026-27)")
        self.assertEqual(v["score"], 98)
        self.assertEqual(v["page_kind"], "gst_ack")
        self.assertEqual(v["client"]["username"], "TEST_USER_01")

        # Check raw_payload nested section
        self.assertIn("vsdc247-v", row["raw_payload"])
        self.assertTrue(row["raw_payload"]["vsdc247-v"]["verified"])
        # Backwards compatible key
        self.assertIn("vsdc247", row["raw_payload"])

        # Check original_author field (the PC that filed the return and captured ARN live)
        self.assertEqual(row.get("original_author"), "TestPC")
        self.assertEqual(row["raw_payload"].get("original_author"), "TestPC")
        # original_author is decoupled from vsdc247-v verification block
        self.assertNotIn("original_author", v)

    def test_sgt_payload_marked_unverified_when_arn_not_seen(self):
        """When ARN is not seen by OCR, vsdc247-v section has verified=False."""
        self.router.apply_engine_settings(vsdc=False, vsdc_x=False, vsdc247=False, sgt="live", vsdc247_verify=True)
        arn = "AA2703260999999"

        sgt_payload = {
            "source": "sgt",
            "portal": "GST Portal",
            "arn": arn,
            "filing_type": "GSTR-3B",
            "period_label": "September (FY 2026-27)",
            "status": "Submitted",
            "capture_method": "SGT_live",
            "raw_payload": {},
        }
        self.router._sgt = MagicMock()
        self.router._sgt.pending.return_value = True
        self.router._sgt.pop_dispatch.return_value = dict(sgt_payload)

        # Verifier does NOT have this ARN
        row = self.router.evaluate_tick()
        self.assertIsNotNone(row)
        self.assertIn("vsdc247-v", row)
        self.assertFalse(row["vsdc247-v"]["verified"])
        self.assertEqual(row["vsdc247-v"]["status"], "unverified")
        self.assertEqual(row["vsdc247-v"]["arn"], arn)

    def test_sgt_payload_marked_unverified_when_arn_na(self):
        """When SGT emits row with ARN='N/A' (e.g. calendar Not Submitted), vsdc247-v is unverified."""
        self.router.apply_engine_settings(vsdc=False, vsdc_x=False, vsdc247=False, sgt="live", vsdc247_verify=True)

        sgt_payload = {
            "source": "sgt",
            "portal": "GST Portal",
            "arn": "N/A",
            "filing_type": "GSTR-1",
            "period_label": "September (FY 2026-27)",
            "status": "Not Submitted",
            "capture_method": "SGT_live",
            "raw_payload": {},
        }
        self.router._sgt = MagicMock()
        self.router._sgt.pending.return_value = True
        self.router._sgt.pop_dispatch.return_value = dict(sgt_payload)

        row = self.router.evaluate_tick()
        self.assertIsNotNone(row)
        self.assertIn("vsdc247-v", row)
        self.assertFalse(row["vsdc247-v"]["verified"])
        self.assertEqual(row["vsdc247-v"]["status"], "unverified")

    def test_no_standalone_entry_created_in_tracker_dump(self):
        """Verifier never creates standalone rows in tracker_dump when OCR captures an ARN."""
        reconciler = Reconciler(db=self.db, grace_sec=10.0)
        verifier = Verifier(reconciler=reconciler, sink=lambda e: True)

        arn = "AA2703260777777"
        entry = {
            "arn": arn,
            "form": "GSTR-1",
            "period": "September (FY 2026-27)",
            "page_kind": "gst_ack",
            "score": 96,
            "tier": "certain",
            "seen_at": "2026-10-18T16:35:00",
            "client": {"username": "TEST_USER_02"},
        }

        # Verifier observes ARN (and SGT has not captured it yet)
        res = reconciler.observe_arn(entry)
        self.assertEqual(res.outcome, "pending_grace")

        # Confirm 0 rows in tracker_dump
        count = self.db.conn.execute("SELECT COUNT(*) FROM tracker_dump").fetchone()[0]
        self.assertEqual(count, 0)

        # Let grace expire
        res_list = reconciler.check_pending(now=10000000000.0)
        self.assertEqual(len(res_list), 1)
        self.assertEqual(res_list[0].outcome, "missed")

        # Confirm STILL 0 rows in tracker_dump: no standalone row was inserted!
        count_after = self.db.conn.execute("SELECT COUNT(*) FROM tracker_dump").fetchone()[0]
        self.assertEqual(count_after, 0)

    def test_retroactive_reconciliation_annotates_existing_sgt_row(self):
        """When SGT captured first and OCR sees it later, annotate_vsdc247 updates the existing SGT row."""
        arn = "AA2703260888888"
        initial_sgt_payload = {
            "source": "sgt",
            "portal": "GST Portal",
            "arn": arn,
            "filing_type": "GSTR-1",
            "period_label": "September (FY 2026-27)",
            "status": "Submitted",
            "capture_method": "SGT_live",
            "original_author": "TestPC",
            "vsdc247-v": {"verified": False, "status": "unverified", "arn": arn},
            "raw_payload": {
                "source": {"engine": "SGT"},
                "original_author": "TestPC",
                "vsdc247-v": {"verified": False, "status": "unverified", "arn": arn},
            },
        }

        # SGT row inserted in DB
        self.db.conn.execute(
            """INSERT INTO tracker_dump
               (client_id, portal, period_label, arn_number, capture_method, status, raw_payload_json, dataset_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (42, "GST Portal", "September (FY 2026-27)", arn, "SGT_live", "Submitted",
             json.dumps(initial_sgt_payload), f"GST:TEST:GSTR1:SEP_2026"),
        )

        # Later, OCR verifies it (vsdc247-v verification block has NO original_author)
        block = {
            "verified": True,
            "status": "verified",
            "arn": arn,
            "form": "GSTR-1",
            "period": "September (FY 2026-27)",
            "score": 99,
            "tier": "certain",
            "seen_at": "2026-10-18T16:36:00",
            "confirmed_by": "single_frame",
            "device_name": "TestPC",
        }
        updated = self.db.annotate_vsdc247(arn, block)
        self.assertEqual(updated, 1)

        # Check DB row
        row = self.db.conn.execute(
            "SELECT raw_payload_json FROM tracker_dump WHERE arn_number = ?", (arn,)
        ).fetchone()
        payload = json.loads(row[0])

        self.assertTrue(payload["vsdc247-v"]["verified"])
        self.assertEqual(payload["vsdc247-v"]["status"], "verified")
        self.assertEqual(payload["vsdc247-v"]["score"], 99)
        self.assertTrue(payload["raw_payload"]["vsdc247-v"]["verified"])
        self.assertEqual(payload.get("original_author"), "TestPC")
        self.assertEqual(payload["raw_payload"].get("original_author"), "TestPC")

        # Still exactly 1 row in tracker_dump!
        total_rows = self.db.conn.execute("SELECT COUNT(*) FROM tracker_dump").fetchone()[0]
        self.assertEqual(total_rows, 1)

    def test_original_author_preserved_from_live_filing_not_overwritten_by_history(self):
        """When an ARN was captured live by PC-1, a subsequent capture from history by PC-2 preserves PC-1 as original_author."""
        arn = "AA2703260555555"
        d_key = f"GST:TEST:GSTR1:SEP_2026_HIST"

        # 1. PC-1 files return live
        live_payload = {
            "source": "sgt",
            "portal": "GST Portal",
            "arn": arn,
            "filing_type": "GSTR-1",
            "period_label": "September (FY 2026-27)",
            "status": "Submitted",
            "capture_method": "SGT_live",
            "device_name": "DESK-PC-1",
            "original_author": "DESK-PC-1",
            "raw_payload": {
                "source": {"engine": "SGT", "record": "gst_submit_success"},
                "device_name": "DESK-PC-1",
                "original_author": "DESK-PC-1",
            },
        }

        self.db.insert_tracker_dump(
            client_id=101,
            portal="GST Portal",
            period_label="September (FY 2026-27)",
            arn_number=arn,
            capture_method="SGT_live",
            status="Submitted",
            raw_payload_json=json.dumps(live_payload),
            dataset_key=d_key,
        )

        # Confirm inserted with original_author = "DESK-PC-1"
        row = self.db.conn.execute("SELECT raw_payload_json FROM tracker_dump WHERE dataset_key = ?", (d_key,)).fetchone()
        self.assertIsNotNone(row)
        p = json.loads(row[0])
        self.assertEqual(p["original_author"], "DESK-PC-1")

        # 2. Later, PC-2 browses portal history (e.g. GST returns calendar / view filed returns)
        history_payload = {
            "source": "sgt",
            "portal": "GST Portal",
            "arn": arn,
            "filing_type": "GSTR-1",
            "period_label": "September (FY 2026-27)",
            "status": "Submitted",
            "capture_method": "SGT_calendar_history",
            "device_name": "LAPTOP-PC-2",
            "original_author": None,  # Captured from history, NOT live
            "raw_payload": {
                "source": {"engine": "SGT", "record": "gst_returns_calendar"},
                "device_name": "LAPTOP-PC-2",
                "original_author": None,
            },
        }

        self.db.insert_tracker_dump(
            client_id=101,
            portal="GST Portal",
            period_label="September (FY 2026-27)",
            arn_number=arn,
            capture_method="SGT_calendar_history",
            status="Submitted",
            raw_payload_json=json.dumps(history_payload),
            dataset_key=d_key,
        )

        # Verify that original_author is STILL DESK-PC-1 (the PC that filed it live), NOT LAPTOP-PC-2
        row2 = self.db.conn.execute("SELECT raw_payload_json FROM tracker_dump WHERE dataset_key = ?", (d_key,)).fetchone()
        p2 = json.loads(row2[0])
        self.assertEqual(p2["original_author"], "DESK-PC-1")
        self.assertEqual(p2["raw_payload"]["original_author"], "DESK-PC-1")

    def test_history_capture_does_not_set_original_author(self):
        """When an SGT row is emitted from a history record, original_author is None."""
        self.router.apply_engine_settings(vsdc=False, vsdc_x=False, vsdc247=False, sgt="live", vsdc247_verify=True)
        arn = "AA2703260444444"

        # SGT row from history (calendar)
        sgt_history_payload = {
            "source": "sgt",
            "portal": "GST Portal",
            "arn": arn,
            "filing_type": "GSTR-1",
            "period_label": "September (FY 2026-27)",
            "status": "Submitted",
            "capture_method": "SGT_live",
            "raw_payload": {
                "source": {"engine": "SGT", "record": "gst_returns_calendar"},
            },
        }
        self.router._sgt = MagicMock()
        self.router._sgt.pending.return_value = True
        self.router._sgt.pop_dispatch.return_value = dict(sgt_history_payload)

        row = self.router.evaluate_tick()
        self.assertIsNotNone(row)
        # Because it came from history, original_author must NOT be set to the scanning PC
        self.assertIsNone(row.get("original_author"))
        self.assertIsNone(row["raw_payload"].get("original_author"))


    def test_verifier_reconciler_follows_sgt_built_later(self):
        """The verifier is created before SGT exists, so its lookup must resolve SGT lazily."""
        self.router.apply_engine_settings(vsdc=False, vsdc_x=False, vsdc247=False, sgt="live",
                                          vsdc247_verify=True)
        reconciler = self.router._verifier.reconciler
        self.assertIs(reconciler._db, self.db)
        arn = "AA2703260555555"

        class _Slot:
            def __init__(self, arn):
                self.arn = arn
                self.values = {"arn": arn, "form": "GSTR-1", "period": "September - 2026-27"}

        class _Session:
            def __init__(self, arn):
                self.slots = [_Slot(arn)]
                self.profile = {}

        class _Sgt:
            def __init__(self, arn):
                self._outbox = {}
                self._sessions = {1: _Session(arn)}
                self._shelf = {}

        self.assertIsNone(reconciler.lookup_sgt(arn))
        self.router._sgt = _Sgt(arn)
        record = reconciler.lookup_sgt(arn)
        self.assertIsNotNone(record)
        self.assertEqual(record.source, "sgt_session")

    def test_vsdc_assembler_completed_dataset_stamps_original_author(self):
        """When VSDC assembler finishes a complete dataset, original_author is set to device_name()."""
        from core.vsdc.vsdc_assembler import VisualSessionAssembler
        assembler = VisualSessionAssembler()
        assembler.record_step("https://eportal.incometax.gov.in/dashboard", "itr_landing")
        assembler.update_identity(pan="AHJPR0846B", name="AMAN ASSOCIATES", portal="Income Tax")
        assembler.update_selection(filing_type="ITR-4", period_label="AY 2026-27")
        assembler.record_submission(
            ack_number="982348123456789",
            status="Submitted (e-Verified)",
            crosshair_id="itr_filed_verified",
        )

        from unittest.mock import patch
        with patch("core.vsdc.vsdc_assembler.device_name", return_value="TestPC"):
            payload = assembler.get_completed_dataset_payload()
        self.assertIsNotNone(payload)
        self.assertEqual(payload.get("original_author"), "TestPC")
        self.assertEqual(payload["raw_payload"].get("original_author"), "TestPC")
        self.assertNotIn("original_author", payload.get("vsdc247-v", {}))

    @staticmethod
    def _sgt_live_payload(arn, device, author):
        return {
            "source": "sgt",
            "portal": "GST Portal",
            "arn": arn,
            "filing_type": "GSTR-1",
            "period_label": "September (FY 2026-27)",
            "status": "Submitted",
            "capture_method": "SGT_live",
            "device_name": device,
            "original_author": author,
            "raw_payload": {
                "source": {"engine": "SGT", "record": "gst_submit_success"},
                "device_name": device,
                "original_author": author,
            },
        }

    def test_live_recapture_from_another_pc_keeps_original_author(self):
        """A second PC's LIVE capture of the same dataset must not become the original author."""
        arn = "AA2703260777777"
        d_key = "GST:TEST:GSTR1:SEP_2026"
        self.db.insert_tracker_dump(
            client_id=101, portal="GST Portal", period_label="September (FY 2026-27)",
            arn_number=arn, capture_method="SGT_live", status="Submitted",
            raw_payload_json=json.dumps(self._sgt_live_payload(arn, "DESK-PC-1", "DESK-PC-1")),
            dataset_key=d_key,
        )
        self.db.insert_tracker_dump(
            client_id=101, portal="GST Portal", period_label="September (FY 2026-27)",
            arn_number=arn, capture_method="SGT_live", status="Submitted",
            raw_payload_json=json.dumps(self._sgt_live_payload(arn, "LAPTOP-PC-2", "LAPTOP-PC-2")),
            dataset_key=d_key,
        )
        rows = self.db.conn.execute("SELECT raw_payload_json FROM tracker_dump WHERE dataset_key = ?", (d_key,)).fetchall()
        self.assertEqual(len(rows), 1)
        payload = json.loads(rows[0][0])
        self.assertEqual(payload["original_author"], "DESK-PC-1")
        self.assertEqual(payload["raw_payload"]["original_author"], "DESK-PC-1")

    def test_incomplete_capture_does_not_own_author_of_the_pc_that_files_it(self):
        """PC1's draft capture must not keep authorship when PC2 submits the same dataset."""
        arn = "AA2703260666666"
        d_key = "GST:TEST:GSTR1:SEP_2026_DRAFT"
        draft = self._sgt_live_payload("", "DESK-PC-1", "DESK-PC-1")
        draft.update({"arn": "N/A", "status": "Draft"})
        self.db.insert_tracker_dump(
            client_id=101, portal="GST Portal", period_label="September (FY 2026-27)",
            arn_number="N/A", capture_method="SGT_live", status="Draft",
            raw_payload_json=json.dumps(draft), dataset_key=d_key,
        )
        self.db.insert_tracker_dump(
            client_id=101, portal="GST Portal", period_label="September (FY 2026-27)",
            arn_number=arn, capture_method="SGT_live", status="Submitted",
            raw_payload_json=json.dumps(self._sgt_live_payload(arn, "LAPTOP-PC-2", "LAPTOP-PC-2")),
            dataset_key=d_key,
        )
        rows = self.db.conn.execute("SELECT raw_payload_json FROM tracker_dump WHERE dataset_key = ?", (d_key,)).fetchall()
        self.assertEqual(len(rows), 1)
        payload = json.loads(rows[0][0])
        self.assertEqual(payload["original_author"], "LAPTOP-PC-2")
        self.assertEqual(payload["raw_payload"]["original_author"], "LAPTOP-PC-2")

    def test_peer_tracker_dump_keeps_original_author(self):
        """A peer-synced newer row for the same dataset keeps the first live author."""
        arn = "AA2703260888888"
        d_key = "GST:TEST:GSTR1:SEP_2026_PEER"
        self.db.insert_tracker_dump(
            client_id=101, portal="GST Portal", period_label="September (FY 2026-27)",
            arn_number=arn, capture_method="SGT_live", status="Submitted",
            raw_payload_json=json.dumps(self._sgt_live_payload(arn, "DESK-PC-1", "DESK-PC-1")),
            dataset_key=d_key,
        )
        self.db.store_peer_tracker_dumps([{
            "client_id": 101, "portal": "GST Portal", "period_label": "September (FY 2026-27)",
            "arn_number": arn, "capture_method": "SGT_live", "status": "Submitted",
            "raw_payload_json": json.dumps(self._sgt_live_payload(arn, "LAPTOP-PC-2", "LAPTOP-PC-2")),
            "captured_by": "LAPTOP-PC-2", "created_at": "2099-01-01T00:00:00+00:00",
            "dataset_key": d_key,
        }])
        rows = self.db.conn.execute("SELECT raw_payload_json FROM tracker_dump WHERE dataset_key = ?", (d_key,)).fetchall()
        self.assertEqual(len(rows), 1)
        payload = json.loads(rows[0][0])
        self.assertEqual(payload["original_author"], "DESK-PC-1")
        self.assertEqual(payload["raw_payload"]["original_author"], "DESK-PC-1")

    def test_deduplicate_tracker_dumps_keeps_original_author(self):
        """Collapsing duplicates must carry the earliest author onto the surviving newest row."""
        arn = "AA2703260999999"
        d_key = "GST:TEST:GSTR1:SEP_2026_DEDUP"
        for created_at, device in (("2026-01-01T00:00:00+00:00", "DESK-PC-1"),
                                   ("2026-02-01T00:00:00+00:00", "LAPTOP-PC-2")):
            self.db.conn.execute(
                """INSERT INTO tracker_dump
                   (client_id, portal, period_label, arn_number, capture_method, status,
                    raw_payload_json, captured_by, created_at, dataset_key)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (101, "GST Portal", "September (FY 2026-27)", arn, "SGT_live", "Submitted",
                 json.dumps(self._sgt_live_payload(arn, device, device)), device, created_at, d_key),
            )
        self.db.deduplicate_tracker_dumps()
        rows = self.db.conn.execute("SELECT raw_payload_json FROM tracker_dump WHERE dataset_key = ?", (d_key,)).fetchall()
        self.assertEqual(len(rows), 1)
        payload = json.loads(rows[0][0])
        self.assertEqual(payload["original_author"], "DESK-PC-1")
        self.assertEqual(payload["raw_payload"]["original_author"], "DESK-PC-1")


if __name__ == "__main__":
    unittest.main()
