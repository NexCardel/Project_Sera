"""
tests/test_vsdc247_verify_db.py - VSDC247-V WP4: DB annotation, flagged row sink, and removal.
Tests:
  - annotate_vsdc247 updates raw_payload_json without touching other columns or keys
  - insert_vsdc247_flagged_row creates independent row with V247:GST:<ARN> key
  - remove_vsdc247_flagged_row deletes flagged row without touching real SGT rows
  - Reconciler actions trigger corresponding DB updates
"""

import contextlib
import json
import sqlite3
import unittest

from typing import Any, Dict

from core.dataset_key import NOT_CARRIER_SQL, NOT_VSDC247_VERIFY_SQL, VSDC247_VERIFY_METHOD
from core.vsdc.vsdc247_reconciler import (
    OUTCOME_MATCHED,
    OUTCOME_MISSED,
    Reconciler,
    SgtRecord,
    build_flagged_row,
    build_vsdc247_block,
)
from sera_db.tracker_dump import TrackerDumpMixin


class FakeDb(TrackerDumpMixin):
    """Minimal fake database harness that mixes in TrackerDumpMixin."""

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

    @contextlib.contextmanager
    def _connect_raw(self):
        yield self.conn

    @contextlib.contextmanager
    def _connect(self):
        yield self.conn



class TestTrackerDumpWP4(unittest.TestCase):

    def setUp(self):
        self.db = FakeDb()
        self.arn = "AA191026999932T"
        self.other_arn = "AA191026999933U"

        # Insert a sample live SGT row
        self.sgt_payload = {
            "source": "sgt",
            "filing_type": "GSTR-1",
            "period_label": "September - 2026-27",
            "arn": self.arn,
            "raw_payload": {
                "source": {"engine": "SGT"},
                "sgt_dataset": {"form": "GSTR-1", "arn": self.arn},
            },
        }
        self.db.conn.execute(
            """INSERT INTO tracker_dump
               (client_id, unassigned_identity, portal, period_label, arn_number, capture_method, status, raw_payload_json, dataset_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (42, "00AAAAA0000A1Z0", "GST Portal", "September - 2026-27", self.arn,
             "SGT_live", "Filed", json.dumps(self.sgt_payload), "GST:00AAAAA0000A1Z0:GSTR1:SEP_2026"),
        )

        # Insert a sample non-SGT row (e.g. DOM_Tracker) with other ARN
        self.db.conn.execute(
            """INSERT INTO tracker_dump
               (client_id, unassigned_identity, portal, period_label, arn_number, capture_method, status, raw_payload_json, dataset_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (43, "00BBBBB0000B1Z0", "GST Portal", "September - 2026-27", self.other_arn,
             "DOM_Tracker", "Filed", json.dumps({"source": "dom"}), "GST:00BBBBB0000B1Z0:GSTR1:SEP_2026"),
        )

    def test_annotate_vsdc247_merges_without_clobbering_existing_keys(self):
        block = build_vsdc247_block(
            result=OUTCOME_MATCHED,
            arn_seen=self.arn,
            seen_at="2026-10-18T12:00:00",
            form="GSTR-1",
            period="September - 2026-27",
            client="agrees",
            sgt_delay_sec=15,
        )

        count = self.db.annotate_vsdc247(self.arn, block)
        self.assertEqual(count, 1)

        # Verify row in DB
        row = self.db.conn.execute(
            "SELECT raw_payload_json, status, dataset_key, capture_method FROM tracker_dump WHERE arn_number = ?",
            (self.arn,),
        ).fetchone()

        payload = json.loads(row[0])
        self.assertEqual(row[1], "Filed")                     # status untouched
        self.assertEqual(row[2], "GST:00AAAAA0000A1Z0:GSTR1:SEP_2026") # dataset_key untouched
        self.assertEqual(row[3], "SGT_live")                  # capture_method untouched

        # Raw payload contains vsdc247 beside sgt_dataset per 6.4
        self.assertIn("vsdc247", payload["raw_payload"])
        self.assertIn("sgt_dataset", payload["raw_payload"])  # preserved!
        self.assertEqual(payload["raw_payload"]["vsdc247"]["result"], OUTCOME_MATCHED)
        self.assertEqual(payload["raw_payload"]["vsdc247"]["sgt_delay_sec"], 15)

    def test_annotate_vsdc247_is_idempotent(self):
        block = build_vsdc247_block(result=OUTCOME_MATCHED, arn_seen=self.arn)
        self.assertEqual(self.db.annotate_vsdc247(self.arn, block), 1)
        self.assertEqual(self.db.annotate_vsdc247(self.arn, block), 1)

        row = self.db.conn.execute(
            "SELECT raw_payload_json FROM tracker_dump WHERE arn_number = ?", (self.arn,)
        ).fetchone()
        payload = json.loads(row[0])
        self.assertEqual(payload["vsdc247"]["result"], OUTCOME_MATCHED)

    def test_annotate_vsdc247_ignores_non_sgt_rows(self):
        block = build_vsdc247_block(result=OUTCOME_MATCHED, arn_seen=self.other_arn)
        # other_arn is DOM_Tracker, not SGT
        count = self.db.annotate_vsdc247(self.other_arn, block)
        self.assertEqual(count, 0)

    def test_insert_and_remove_vsdc247_flagged_row(self):
        missed_arn = "AA9999999999999"
        block = build_vsdc247_block(result=OUTCOME_MISSED, arn_seen=missed_arn)
        entry = {
            "arn": missed_arn,
            "form": "GSTR-3B",
            "period": "Jul-Sep - 2026",
            "client": {"username": "MISSING_USER"},
        }
        flagged_row = build_flagged_row(entry, block)

        # 1. Insert flagged row
        row_id = self.db.insert_vsdc247_flagged_row(flagged_row)
        self.assertGreater(row_id, 0)

        # Verify row exists in DB
        cur = self.db.conn.execute(
            "SELECT capture_method, dataset_key, status, arn_number FROM tracker_dump WHERE id = ?",
            (row_id,),
        )
        row = cur.fetchone()
        self.assertEqual(row[0], VSDC247_VERIFY_METHOD)
        self.assertEqual(row[1], f"V247:GST:{missed_arn}")
        self.assertEqual(row[2], "Submitted")
        self.assertEqual(row[3], missed_arn)

        # Calling insert again updates without creating duplicate
        row_id_2 = self.db.insert_vsdc247_flagged_row(flagged_row)
        self.assertEqual(row_id_2, row_id)
        count = self.db.conn.execute(
            "SELECT COUNT(*) FROM tracker_dump WHERE dataset_key = ?", (f"V247:GST:{missed_arn}",)
        ).fetchone()[0]
        self.assertEqual(count, 1)

        # 2. Remove flagged row (e.g. on late SGT delivery)
        deleted = self.db.remove_vsdc247_flagged_row(missed_arn)
        self.assertEqual(deleted, 1)

        # Confirm deleted
        count_after = self.db.conn.execute(
            "SELECT COUNT(*) FROM tracker_dump WHERE dataset_key = ?", (f"V247:GST:{missed_arn}",)
        ).fetchone()[0]
        self.assertEqual(count_after, 0)

    def test_remove_vsdc247_flagged_row_does_not_touch_real_sgt_row(self):
        # Even if called with self.arn (which is SGT_live), it will NOT delete it!
        deleted = self.db.remove_vsdc247_flagged_row(self.arn)
        self.assertEqual(deleted, 0)
        still_there = self.db.conn.execute(
            "SELECT COUNT(*) FROM tracker_dump WHERE arn_number = ?", (self.arn,)
        ).fetchone()[0]
        self.assertEqual(still_there, 1)

    def test_reconciler_automatic_db_updates(self):
        # When Reconciler is provided db, it automatically annotates on match,
        # inserts flagged row on miss, and removes flagged row on late arrival.
        class Clock:
            def __init__(self): self.t = 1_800_000_000.0
            def __call__(self): return self.t
            def advance(self, s): self.t += s

        clock = Clock()
        reconciler = Reconciler(db=self.db, grace_sec=60.0, clock=clock, allow_flagged_rows=True)

        # 1. Match: OCR sees self.arn (which is already in self.db as SGT_live)
        entry1 = {
            "arn": self.arn,
            "form": "GSTR-1",
            "period": "September - 2026-27",
            "seen_at": "2026-10-18T12:00:00",
            "seen_epoch": clock.t,
            "client": {"username": "TESTUSER", "client_id": 42},
        }
        res1 = reconciler.observe_arn(entry1)
        self.assertEqual(res1.outcome, OUTCOME_MATCHED)

        # DB row was annotated!
        row = self.db.conn.execute(
            "SELECT raw_payload_json FROM tracker_dump WHERE arn_number = ?", (self.arn,)
        ).fetchone()
        payload = json.loads(row[0])
        self.assertEqual(payload["vsdc247"]["result"], OUTCOME_MATCHED)

        # 2. Miss: OCR sees a brand new ARN not in SGT
        new_arn = "AA9999999999999"
        entry2 = {
            "arn": new_arn,
            "form": "GSTR-3B",
            "period": "Jul-Sep - 2026",
            "seen_at": "2026-10-18T12:01:00",
            "seen_epoch": clock.t,
            "client": {"username": "UNSEEN"},
        }
        reconciler.observe_arn(entry2)

        # Advance past grace
        clock.advance(65.0)
        reconciler.check_pending()

        # Flagged row was inserted in DB!
        flagged = self.db.conn.execute(
            "SELECT capture_method, dataset_key FROM tracker_dump WHERE arn_number = ?", (new_arn,)
        ).fetchone()
        self.assertIsNotNone(flagged)
        self.assertEqual(flagged[0], VSDC247_VERIFY_METHOD)

        # 3. Late arrival: SGT row now gets added to DB for new_arn
        self.db.conn.execute(
            """INSERT INTO tracker_dump
               (client_id, unassigned_identity, portal, period_label, arn_number, capture_method, status, raw_payload_json, dataset_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (99, "99ZZZZZ9999Z1Z9", "GST Portal", "Jul-Sep - 2026", new_arn,
             "SGT_live", "Filed", json.dumps({"source": "sgt"}), f"GST:99ZZZZZ9999Z1Z9:GSTR3B:JUL_SEP_2026"),
        )

        # Next tick detects late arrival
        clock.advance(5.0)
        resolved = reconciler.check_pending()
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0].outcome, OUTCOME_MATCHED)
        self.assertTrue(resolved[0].late)

        # Flagged row was deleted from DB!
        flagged_count = self.db.conn.execute(
            "SELECT COUNT(*) FROM tracker_dump WHERE capture_method = ? AND arn_number = ?",
            (VSDC247_VERIFY_METHOD, new_arn),
        ).fetchone()[0]
        self.assertEqual(flagged_count, 0)

        # Real SGT row was annotated!
        sgt_row = self.db.conn.execute(
            "SELECT raw_payload_json FROM tracker_dump WHERE capture_method = 'SGT_live' AND arn_number = ?",
            (new_arn,),
        ).fetchone()
        sgt_payload = json.loads(sgt_row[0])
        self.assertEqual(sgt_payload["vsdc247"]["result"], OUTCOME_MATCHED)
        self.assertTrue(sgt_payload["vsdc247"]["late"])

    def test_reconciler_default_does_not_insert_flagged_row(self):
        class Clock:
            def __init__(self): self.t = 1_800_000_000.0
            def __call__(self): return self.t
            def advance(self, s): self.t += s

        clock = Clock()
        # Default allow_flagged_rows is False
        reconciler = Reconciler(db=self.db, grace_sec=60.0, clock=clock)

        unseen_arn = "AA8888888888888"
        entry = {
            "arn": unseen_arn,
            "form": "GSTR-3B",
            "period": "Jul-Sep - 2026",
            "seen_at": "2026-10-18T12:01:00",
            "seen_epoch": clock.t,
            "client": {"username": "UNSEEN"},
        }
        reconciler.observe_arn(entry)
        clock.advance(65.0)
        res_list = reconciler.check_pending()
        self.assertEqual(len(res_list), 1)
        self.assertEqual(res_list[0].outcome, OUTCOME_MISSED)

        # Confirm NO row was inserted into tracker_dump!
        flagged = self.db.conn.execute(
            "SELECT id FROM tracker_dump WHERE arn_number = ?", (unseen_arn,)
        ).fetchone()
        self.assertIsNone(flagged)


if __name__ == "__main__":
    unittest.main()

