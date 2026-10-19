"""
tests/test_vsdc247_verify_reconciler.py - VSDC247-V WP3: SGT lookup, reconciler, and outcomes.
Tests all scenarios from plan sections 6.2–6.3 and 6.5:
  - SGT lookup (outbox, sessions, shelf, database tracker_dump)
  - Client comparison (agree, conflict, unknown)
  - Outcomes: matched, matched_client_differs, missed, sgt_only
  - Timing: immediate match, match within grace, grace expiry, late SGT arrival
  - Verifier integration
"""

import datetime
import json
import sqlite3
import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

from core.vsdc.vsdc247_reconciler import (
    DEFAULT_GRACE_SEC,
    OUTCOME_MATCHED,
    OUTCOME_MATCHED_CLIENT_DIFFERS,
    OUTCOME_MISSED,
    OUTCOME_PENDING,
    OUTCOME_SGT_ONLY,
    ReconciliationResult,
    Reconciler,
    SgtRecord,
    build_flagged_row,
    build_vsdc247_block,
    compare_clients,
    find_sgt_in_db,
    find_sgt_in_router,
)
from core.vsdc.vsdc247_verify import Verifier

ARN_1 = "AB000000000000U"
ARN_2 = "AB0000000000000"
ARN_SGT_ONLY = "AB9999999999999"

GSTIN_A = "00AAAAA0000A1Z0"
PAN_A = "AAAAA0000A"
GSTIN_B = "99BBBBB9999B1Z9"
PAN_B = "BBBBB9999B"


class Clock:
    def __init__(self, t: float = 1_800_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, sec: float) -> float:
        self.t += sec
        return self.t


def _sample_ocr_entry(
    arn: str = ARN_1,
    seen_epoch: float = 1_800_000_000.0,
    username: Optional[str] = "EXAMPLEUSER01",
    client_id: Optional[int] = 42,
    agreement: str = "agrees",
    form: str = "GSTR-1",
    period: str = "September - 2026-27",
) -> Dict[str, Any]:
    return {
        "v": 1,
        "seen_at": datetime.datetime.fromtimestamp(seen_epoch).isoformat(),
        "seen_epoch": seen_epoch,
        "arn": arn,
        "form": form,
        "period": period,
        "page_kind": "gstr1_submit",
        "page_url": "return.gst.gov.in/returns/auth/file",
        "hwnd": "12345",
        "score": 85,
        "confirmed_by": "single_frame",
        "client": {
            "username": username,
            "client_id": client_id,
            "link": "window" if username else None,
            "agreement": agreement,
            "match": "exact" if username else None,
        },
        "device_name": "TEST-PC",
    }


# ── Mock SGT objects for in-memory lookup testing ──────────────────────────────
class MockSlot:
    def __init__(self, arn: str, form: str = "GSTR-1", period: str = "September - 2026-27") -> None:
        self.arn = arn
        self.values = {"arn": arn, "form": form, "period": period}
        self.first_page = "https://return.gst.gov.in/returns/auth/file"


class MockSession:
    def __init__(self, slots: List[MockSlot], name: str = "EXAMPLE TAXPAYER",
                 pan: str = PAN_A, gstin: str = GSTIN_A, last_seen: float = 1_800_000_010.0) -> None:
        self.slots = slots
        self.profile = {
            "name": {"value": name},
            "pan": {"value": pan},
            "gstin": {"value": gstin},
        }
        self.last_seen = last_seen
        self.started = last_seen - 100.0


class MockRouter:
    def __init__(self) -> None:
        self._outbox: Dict[int, Any] = {}
        self._sessions: Dict[int, Any] = {}
        self._shelf: Dict[int, List[Any]] = {}


# ── WP3: SGT Lookup Tests ──────────────────────────────────────────────────────
class TestSgtLookup(unittest.TestCase):

    def test_find_in_outbox(self):
        router = MockRouter()
        slot = MockSlot(ARN_1)
        sess = MockSession([slot])
        router._outbox[1] = (sess, slot)

        rec = find_sgt_in_router(router, ARN_1)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.arn, ARN_1)
        self.assertEqual(rec.pan, PAN_A)
        self.assertEqual(rec.gstin, GSTIN_A)
        self.assertEqual(rec.source, "sgt_outbox")

    def test_find_in_sessions(self):
        router = MockRouter()
        slot = MockSlot(ARN_2, form="GSTR-3B", period="Jul-Sep - 2026")
        sess = MockSession([slot])
        router._sessions[10] = sess

        rec = find_sgt_in_router(router, ARN_2)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.arn, ARN_2)
        self.assertEqual(rec.form, "GSTR-3B")
        self.assertEqual(rec.source, "sgt_session")

    def test_find_in_shelf(self):
        router = MockRouter()
        slot = MockSlot(ARN_1)
        sess = MockSession([slot])
        router._shelf[99] = [sess]

        rec = find_sgt_in_router(router, ARN_1)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.arn, ARN_1)
        self.assertEqual(rec.source, "sgt_session")

    def test_find_in_db_tracker_dump(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE tracker_dump (
                id INTEGER PRIMARY KEY,
                dataset_key TEXT,
                unassigned_identity TEXT,
                arn_number TEXT,
                capture_method TEXT,
                raw_payload_json TEXT,
                created_at TEXT,
                client_id INTEGER
            )
        """)
        payload = {
            "filing_type": "GSTR-1",
            "period_label": "September - 2026-27",
            "pan": PAN_A,
            "gstin": GSTIN_A,
            "client_name": "EXAMPLE TAXPAYER",
        }
        conn.execute(
            "INSERT INTO tracker_dump (id, dataset_key, unassigned_identity, arn_number, "
            "capture_method, raw_payload_json, created_at, client_id) "
            "VALUES (1, 'SGT:GST:1', ?, ?, 'SGT_live', ?, '2026-10-18T12:00:10', 42)",
            (GSTIN_A, ARN_1, json.dumps(payload)),
        )

        rec = find_sgt_in_db(conn, ARN_1)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.arn, ARN_1)
        self.assertEqual(rec.client_id, 42)
        self.assertEqual(rec.source, "tracker_dump")
        self.assertEqual(rec.form, "GSTR-1")

    def test_precedence_outbox_over_db(self):
        router = MockRouter()
        outbox_slot = MockSlot(ARN_1)
        outbox_sess = MockSession([outbox_slot], name="OUTBOX USER")
        router._outbox[1] = (outbox_sess, outbox_slot)

        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE tracker_dump (
                id INTEGER PRIMARY KEY, dataset_key TEXT, unassigned_identity TEXT,
                arn_number TEXT, capture_method TEXT,
                raw_payload_json TEXT, created_at TEXT, client_id INTEGER
            )
        """)
        conn.execute(
            "INSERT INTO tracker_dump VALUES (1, 'K', 'ID', ?, 'SGT', '{}', '', 1)",
            (ARN_1,),
        )


        reconciler = Reconciler(sgt_router=router, db=conn)
        rec = reconciler.lookup_sgt(ARN_1)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.source, "sgt_outbox")
        self.assertEqual(rec.client_name, "OUTBOX USER")


# ── WP3: Client Comparison Tests ───────────────────────────────────────────────
class TestClientComparison(unittest.TestCase):

    def setUp(self):
        self.lookup = lambda portal, user: {
            "client_id": 42,
            "gstin": GSTIN_A,
            "pan": PAN_A,
            "name": "EXAMPLE TAXPAYER",
        } if user == "EXAMPLEUSER01" else None

    def test_agrees_when_client_ids_match(self):
        entry = _sample_ocr_entry(client_id=42)
        sgt = SgtRecord(arn=ARN_1, client_id=42)
        self.assertEqual(compare_clients(entry, sgt, self.lookup), "agrees")

    def test_conflicts_when_client_ids_differ(self):
        entry = _sample_ocr_entry(client_id=42)
        sgt = SgtRecord(arn=ARN_1, client_id=99)
        self.assertEqual(compare_clients(entry, sgt, self.lookup), "conflict")

    def test_agrees_when_gstins_match(self):
        entry = _sample_ocr_entry(client_id=None, username="EXAMPLEUSER01")
        sgt = SgtRecord(arn=ARN_1, gstin=GSTIN_A)
        self.assertEqual(compare_clients(entry, sgt, self.lookup), "agrees")

    def test_agrees_when_ocr_gstin_matches_sgt_pan(self):
        entry = _sample_ocr_entry(client_id=None, username="EXAMPLEUSER01")
        sgt = SgtRecord(arn=ARN_1, pan=PAN_A)
        self.assertEqual(compare_clients(entry, sgt, self.lookup), "agrees")

    def test_conflicts_when_pans_differ(self):
        entry = _sample_ocr_entry(client_id=None, username="EXAMPLEUSER01")
        sgt = SgtRecord(arn=ARN_1, pan=PAN_B, gstin=GSTIN_B)
        self.assertEqual(compare_clients(entry, sgt, self.lookup), "conflict")

    def test_unknown_when_ocr_has_no_client(self):
        entry = _sample_ocr_entry(username=None, client_id=None, agreement="unknown")
        sgt = SgtRecord(arn=ARN_1, pan=PAN_A, gstin=GSTIN_A)
        self.assertEqual(compare_clients(entry, sgt, self.lookup), "unknown")

    def test_unknown_when_sgt_has_no_client(self):
        entry = _sample_ocr_entry(client_id=42, username="EXAMPLEUSER01")
        sgt = SgtRecord(arn=ARN_1, pan=None, gstin=None, client_id=None)
        self.assertEqual(compare_clients(entry, sgt, self.lookup), "unknown")

    def test_unknown_when_both_have_no_client(self):
        entry = _sample_ocr_entry(username=None, client_id=None, agreement="unknown")
        sgt = SgtRecord(arn=ARN_1)
        self.assertEqual(compare_clients(entry, sgt, self.lookup), "unknown")

    def test_ocr_page_conflict_persists(self):
        entry = _sample_ocr_entry(client_id=42, agreement="conflict")
        sgt = SgtRecord(arn=ARN_1, client_id=42)
        self.assertEqual(compare_clients(entry, sgt, self.lookup), "conflict")


# ── WP3: Outcome & Timing Tests ────────────────────────────────────────────────
class TestReconciliationScenarios(unittest.TestCase):

    def setUp(self):
        self.clock = Clock()
        self.alerts = []
        self.on_alert = lambda kind, data: self.alerts.append((kind, data))
        self.lookup = lambda portal, user: {
            "client_id": 42,
            "gstin": GSTIN_A,
            "pan": PAN_A,
            "name": "EXAMPLE TAXPAYER",
        } if user == "EXAMPLEUSER01" else None

    def test_immediate_match_when_sgt_already_present(self):
        sgt_store = {ARN_1: SgtRecord(arn=ARN_1, pan=PAN_A, gstin=GSTIN_A, client_id=42)}
        reconciler = Reconciler(
            sgt_lookup=lambda a: sgt_store.get(a),
            client_lookup=self.lookup,
            clock=self.clock,
            on_alert=self.on_alert,
        )

        entry = _sample_ocr_entry(arn=ARN_1, seen_epoch=self.clock.t)
        res = reconciler.observe_arn(entry, now=self.clock.t)

        self.assertEqual(res.outcome, OUTCOME_MATCHED)
        self.assertFalse(res.late)
        self.assertFalse(res.alert)
        self.assertIsNotNone(res.block)
        self.assertTrue(res.block["checked"])
        self.assertEqual(res.block["result"], "matched")
        self.assertEqual(res.block["client"], "agrees")
        self.assertEqual(res.block["sgt_delay_sec"], 0)
        self.assertFalse(reconciler.is_pending(ARN_1))

    def test_delayed_match_within_grace_period(self):
        sgt_store = {}
        reconciler = Reconciler(
            sgt_lookup=lambda a: sgt_store.get(a),
            client_lookup=self.lookup,
            grace_sec=90.0,
            clock=self.clock,
            on_alert=self.on_alert,
        )

        entry = _sample_ocr_entry(arn=ARN_1, seen_epoch=self.clock.t)
        res1 = reconciler.observe_arn(entry, now=self.clock.t)
        self.assertEqual(res1.outcome, OUTCOME_PENDING)
        self.assertTrue(reconciler.is_pending(ARN_1))

        # 30 seconds pass: SGT still not there
        self.clock.advance(30.0)
        ticks1 = reconciler.check_pending(now=self.clock.t)
        self.assertEqual(len(ticks1), 0)
        self.assertTrue(reconciler.is_pending(ARN_1))

        # 45 seconds pass: SGT arrives
        self.clock.advance(15.0)
        sgt_store[ARN_1] = SgtRecord(arn=ARN_1, pan=PAN_A, gstin=GSTIN_A, client_id=42)

        ticks2 = reconciler.check_pending(now=self.clock.t)
        self.assertEqual(len(ticks2), 1)
        res2 = ticks2[0]
        self.assertEqual(res2.outcome, OUTCOME_MATCHED)
        self.assertEqual(res2.sgt_delay_sec, 45)
        self.assertFalse(res2.late)
        self.assertFalse(reconciler.is_pending(ARN_1))
        self.assertEqual(len(self.alerts), 0)

    def test_matched_client_differs_triggers_alert(self):
        sgt_store = {ARN_1: SgtRecord(arn=ARN_1, pan=PAN_B, gstin=GSTIN_B, client_id=99)}
        reconciler = Reconciler(
            sgt_lookup=lambda a: sgt_store.get(a),
            client_lookup=self.lookup,
            clock=self.clock,
            on_alert=self.on_alert,
        )

        entry = _sample_ocr_entry(arn=ARN_1, seen_epoch=self.clock.t)
        res = reconciler.observe_arn(entry, now=self.clock.t)

        self.assertEqual(res.outcome, OUTCOME_MATCHED_CLIENT_DIFFERS)
        self.assertTrue(res.alert)
        self.assertEqual(res.alert_kind, "client_differs")
        self.assertEqual(res.block["client"], "conflict")
        self.assertEqual(len(self.alerts), 1)
        self.assertEqual(self.alerts[0][0], "client_differs")

    def test_grace_expiry_declares_missed_and_alerts(self):
        sgt_store = {}
        reconciler = Reconciler(
            sgt_lookup=lambda a: sgt_store.get(a),
            client_lookup=self.lookup,
            grace_sec=90.0,
            clock=self.clock,
            on_alert=self.on_alert,
        )

        entry = _sample_ocr_entry(arn=ARN_1, seen_epoch=self.clock.t)
        reconciler.observe_arn(entry, now=self.clock.t)

        # 89 seconds: still pending
        self.clock.advance(89.0)
        self.assertEqual(len(reconciler.check_pending(now=self.clock.t)), 0)
        self.assertTrue(reconciler.is_pending(ARN_1))

        # 90 seconds: grace expired!
        self.clock.advance(1.0)
        ticks = reconciler.check_pending(now=self.clock.t)
        self.assertEqual(len(ticks), 1)
        res = ticks[0]

        self.assertEqual(res.outcome, OUTCOME_MISSED)
        self.assertTrue(res.alert)
        self.assertEqual(res.alert_kind, "sgt_missed")
        self.assertFalse(reconciler.is_pending(ARN_1))
        self.assertTrue(reconciler.is_missed(ARN_1))
        self.assertIsNotNone(res.flagged_row)
        self.assertEqual(res.flagged_row["capture_method"], "VSDC247_verify")
        self.assertEqual(res.flagged_row["dataset_key"], f"V247:GST:{ARN_1}")
        self.assertEqual(len(self.alerts), 1)
        self.assertEqual(self.alerts[0][0], "sgt_missed")

    def test_late_sgt_arrival_after_missed(self):
        sgt_store = {}
        reconciler = Reconciler(
            sgt_lookup=lambda a: sgt_store.get(a),
            client_lookup=self.lookup,
            grace_sec=90.0,
            clock=self.clock,
            on_alert=self.on_alert,
        )

        entry = _sample_ocr_entry(arn=ARN_1, seen_epoch=self.clock.t)
        reconciler.observe_arn(entry, now=self.clock.t)

        # Expire grace period
        self.clock.advance(95.0)
        reconciler.check_pending(now=self.clock.t)
        self.assertTrue(reconciler.is_missed(ARN_1))

        # Now SGT arrives at t = 130s
        self.clock.advance(35.0)
        sgt_store[ARN_1] = SgtRecord(arn=ARN_1, pan=PAN_A, gstin=GSTIN_A, client_id=42)

        ticks = reconciler.check_pending(now=self.clock.t)
        self.assertEqual(len(ticks), 1)
        res = ticks[0]

        self.assertEqual(res.outcome, OUTCOME_MATCHED)
        self.assertTrue(res.late)
        self.assertTrue(res.remove_flagged_row)
        self.assertEqual(res.sgt_delay_sec, 130)
        self.assertTrue(res.block.get("late"))
        self.assertFalse(reconciler.is_missed(ARN_1))

    def test_sgt_only_outcome(self):
        reconciler = Reconciler(clock=self.clock)
        sgt_row = {"arn": ARN_SGT_ONLY, "capture_method": "SGT_live", "client_name": "SOME CLIENT"}

        res = reconciler.check_sgt_row(sgt_row, now=self.clock.t)
        self.assertEqual(res.outcome, OUTCOME_SGT_ONLY)
        self.assertFalse(res.alert)
        self.assertFalse(res.block["checked"])
        self.assertEqual(res.block["reason"], "not seen by OCR")


# ── WP3: Verifier Integration Tests ───────────────────────────────────────────
class TestVerifierWithReconciler(unittest.TestCase):

    def test_verifier_observe_and_reconcile_tick(self):
        clock = Clock()
        sgt_store = {}
        reconciler = Reconciler(sgt_lookup=lambda a: sgt_store.get(a), grace_sec=60.0, clock=clock)
        v = Verifier(clock=clock, reconciler=reconciler)

        # Feed frame 1 (first reading)
        lines = [
            "services.gst.gov.in",
            "GSTR-1 of GSTIN - 00AAAAA0000A1Z0 for the Return Period - 'September - 2026-27' has been successfully filed.",
            "The Acknowledgment Reference Number is AA0000000000000.",
        ]
        url = "return.gst.gov.in/returns/auth/file"

        res1 = v.observe_frame(1, url, lines)
        self.assertEqual(res1["status"], "pending_second_read")

        # Feed frame 2 (second distinct frame -> saved & registered with reconciler)
        res2 = v.observe_frame(1, url, lines + ["Dashboard"])
        self.assertEqual(res2["status"], "saved")
        self.assertEqual(res2.get("reconcile"), OUTCOME_PENDING)

        # Tick within grace (30s)
        clock.advance(30.0)
        self.assertEqual(len(v.reconcile_tick()), 0)

        # Tick past grace (31s -> 61s total >= 60s grace)
        clock.advance(31.0)
        results = v.reconcile_tick()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].outcome, OUTCOME_MISSED)


class TestAccountantReconciliationLog(unittest.TestCase):

    def setUp(self):
        import tempfile
        from pathlib import Path
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.log_file = Path(self.tmp_dir.name) / "test_gst_reconciliation.txt"
        self.clock = Clock()
        self.clock.t = 1000.0

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_log_sgt_missed_entry(self):
        from core.vsdc.vsdc247_reconciler import append_accountant_reconciliation_log
        append_accountant_reconciliation_log(
            outcome=OUTCOME_MISSED,
            arn="AA290121009125S",
            form="GSTR-1",
            period="September - 2026-27",
            username="29AABCU9603R1ZM",
            client_name="ABC Enterprises",
            filing_pc="Laptop-Office",
            seen_at="2026-10-19 15:39:46",
            path=self.log_file,
        )
        self.assertTrue(self.log_file.exists())
        content = self.log_file.read_text(encoding="utf-8")
        self.assertIn("SERA GST FILING RECONCILIATION LOG", content)
        self.assertIn("[WARNING] SGT MISSED RETURN - CAPTURED BY OCR ONLY", content)
        self.assertIn("AA290121009125S", content)
        self.assertIn("GSTR-1", content)
        self.assertIn("ABC Enterprises (29AABCU9603R1ZM)", content)
        self.assertIn("Laptop-Office", content)
        self.assertIn("Pending_AA290121009125S", content)

    def test_normal_matched_not_logged(self):
        """Normal matched returns are not logged to prevent bloating the accountant text file."""
        from core.vsdc.vsdc247_reconciler import append_accountant_reconciliation_log
        res = append_accountant_reconciliation_log(
            outcome=OUTCOME_MATCHED,
            arn="AA2703260123456",
            form="GSTR-1",
            period="March 2026",
            client_name="Normal Client",
            filing_pc="Office-PC-1",
            sgt_delay_sec=3,
            late=False,
            path=self.log_file,
        )
        self.assertFalse(res)
        self.assertFalse(self.log_file.exists())

    def test_log_client_mismatch_entry(self):
        from core.vsdc.vsdc247_reconciler import append_accountant_reconciliation_log
        append_accountant_reconciliation_log(
            outcome=OUTCOME_MATCHED_CLIENT_DIFFERS,
            arn="AA2703260222222",
            form="GSTR-3B",
            period="March 2026",
            username="portal_user_b",
            sgt_client="SGT Client A",
            sgt_pan="AAAAA0000A",
            filing_pc="Office-PC-2",
            path=self.log_file,
        )
        content = self.log_file.read_text(encoding="utf-8")
        self.assertIn("[ATTENTION] CLIENT MISMATCH", content)
        self.assertIn("SGT Client:      SGT Client A (PAN: AAAAA0000A)", content)
        self.assertIn("Portal User:     portal_user_b", content)

    def test_log_late_arrival_entry(self):
        from core.vsdc.vsdc247_reconciler import append_accountant_reconciliation_log
        append_accountant_reconciliation_log(
            outcome=OUTCOME_MATCHED,
            arn="AA290121009125S",
            form="GSTR-1",
            period="September - 2026-27",
            client_name="ABC Enterprises",
            filing_pc="Office-PC-1",
            sgt_delay_sec=145,
            late=True,
            path=self.log_file,
        )
        content = self.log_file.read_text(encoding="utf-8")
        self.assertIn("[RESOLVED] SGT LATE ARRIVAL", content)
        self.assertIn("145 seconds", content)
        self.assertIn("cleared automatically", content)

    def test_reconciler_writes_to_accountant_log_automatically(self):
        reconciler = Reconciler(
            grace_sec=30.0,
            clock=self.clock,
            accountant_log_path=self.log_file,
        )
        entry = _sample_ocr_entry(arn="AA8888888888888", seen_epoch=self.clock.t)
        reconciler.observe_arn(entry, now=self.clock.t)

        # Advance clock to expire grace
        self.clock.advance(35.0)
        reconciler.check_pending(now=self.clock.t)

        self.assertTrue(self.log_file.exists())
        content = self.log_file.read_text(encoding="utf-8")
        self.assertIn("[WARNING] SGT MISSED RETURN", content)
        self.assertIn("AA8888888888888", content)


if __name__ == "__main__":
    unittest.main()
