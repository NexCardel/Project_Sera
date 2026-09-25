"""tests/test_sync_maintenance.py
--------------------------------
Tests for Sera Sync v3 WP P3-6:
Make start-up maintenance admin-only / id-independent (F12, F13).

Verifies:
1. is_admin_pc: admin PC vs joiner PC vs legacy mode.
2. Data-rewriting maintenance in run_startup_maintenance and _init_raw_schema
   runs only on the admin PC; non-admin runs sync_fst_reports & optimize_storage.
3. Tie-breakers: deduplicate_tracker_dumps and _init_raw_schema purge use
   (created_at, gid) instead of MAX(id).
4. resequence_client_serial_numbers orders by (created_at, gid), not id.
5. add_client: token_letter (A-1, A-2... B-1...) on office PCs, str(client_id) on legacy;
   ID column auto-assigns max(existing numeric serials) + 1.
6. dataset_key uses f"CLI_{gid}" instead of f"CLI_{client_id}".
7. debounced resequence after apply_changes touching clients on admin PC.
8. Accept: harness shows two nodes produce identical digests after both restart.
9. No PySide6 import in sync_admin.py.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import sera_keys
import sync_admin
import sync_identity
from database import SeraDatabase

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
pytestmark = windows_only

from tests.sync_harness import SyncHarness, digest, open_db_conn


def test_no_pyside6_in_sync_admin():
    """Rule 7: sync_admin.py must not import PySide6."""
    src = Path("sync_admin.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "PySide6" not in alias.name
        elif isinstance(node, ast.ImportFrom):
            assert node.module is not None
            assert "PySide6" not in node.module


def test_is_admin_pc_and_token_letter(tmp_path):
    """Verifies is_admin_pc and get_token_letter in legacy vs admin vs joiner modes."""
    # 1. Legacy mode (no office.json) -> is_admin_pc is True, token_letter is None
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    assert sync_admin.is_admin_pc(legacy_dir) is True
    assert sync_admin.get_token_letter(legacy_dir) is None

    # 2. Office mode harness with Node 0 (Admin) and Node 1 (Joiner)
    with SyncHarness(num_nodes=2, base_dir=tmp_path / "harness_admin_check") as harness:
        n0, n1 = harness.nodes[0], harness.nodes[1]

        # Admin node holds admin_key.dpapi and has office_admin record
        assert sync_admin.is_admin_pc(n0.app_dir, device_id=n0.device_id) is True
        with n0.open_db() as conn:
            assert sync_admin.is_admin_pc(n0.app_dir, conn=conn, device_id=n0.device_id) is True
            assert sync_admin.get_token_letter(n0.app_dir, conn=conn, device_id=n0.device_id) == "A"
            assert n0.db.is_admin_pc(conn=conn) is True
            assert n0.db.get_token_letter(conn=conn) == "A"

        # Joiner node does not hold admin_key.dpapi
        assert sync_admin.is_admin_pc(n1.app_dir, device_id=n1.device_id) is False
        with n1.open_db() as conn:
            assert sync_admin.is_admin_pc(n1.app_dir, conn=conn, device_id=n1.device_id) is False
            assert sync_admin.get_token_letter(n1.app_dir, conn=conn, device_id=n1.device_id) == "B"
            assert n1.db.is_admin_pc(conn=conn) is False
            assert n1.db.get_token_letter(conn=conn) == "B"


def test_startup_maintenance_runs_only_on_admin(tmp_path):
    """Verifies that the 5 data-rewriting maintenance methods run only on admin PC,
    while sync_fst_reports and optimize_storage run on all PCs."""
    with SyncHarness(num_nodes=2, base_dir=tmp_path / "harness_maint") as harness:
        n0, n1 = harness.nodes[0], harness.nodes[1]

        admin_db = n0.db
        joiner_db = n1.db

        with patch.object(admin_db, "resequence_client_serial_numbers") as a_reseq, \
             patch.object(admin_db, "_clean_ligature_noise_from_names") as a_lig, \
             patch.object(admin_db, "deduplicate_tracker_dumps") as a_dedup, \
             patch.object(admin_db, "upgrade_all_placeholder_client_names") as a_upg, \
             patch.object(admin_db, "re_resolve_all_tracker_dumps") as a_reres, \
             patch.object(admin_db, "sync_fst_reports") as a_fst, \
             patch.object(admin_db, "optimize_storage") as a_opt:
            admin_db.run_startup_maintenance()
            assert a_reseq.called
            assert a_lig.called
            assert a_dedup.called
            assert a_upg.called
            assert a_reres.called
            assert a_fst.called
            assert a_opt.called

        with patch.object(joiner_db, "resequence_client_serial_numbers") as j_reseq, \
             patch.object(joiner_db, "_clean_ligature_noise_from_names") as j_lig, \
             patch.object(joiner_db, "deduplicate_tracker_dumps") as j_dedup, \
             patch.object(joiner_db, "upgrade_all_placeholder_client_names") as j_upg, \
             patch.object(joiner_db, "re_resolve_all_tracker_dumps") as j_reres, \
             patch.object(joiner_db, "sync_fst_reports") as j_fst, \
             patch.object(joiner_db, "optimize_storage") as j_opt:
            joiner_db.run_startup_maintenance()
            assert not j_reseq.called
            assert not j_lig.called
            assert not j_dedup.called
            assert not j_upg.called
            assert not j_reres.called
            assert j_fst.called
            assert j_opt.called


def test_deduplicate_tracker_dumps_tie_breaker_created_at_and_gid(tmp_path):
    """Verifies that deduplicate_tracker_dumps keeps the row with the newest
    (created_at, gid) rather than MAX(id)."""
    db_path = tmp_path / "master.db"
    raw_path = tmp_path / "rawPayload.db"
    hex_key = "a" * 64

    db = SeraDatabase(str(db_path), hex_key, raw_db_path=str(raw_path), defer_startup_maintenance=True)

    with db._connect_raw() as conn:
        # Insert two rows with the same dataset_key
        # Row 1: lower id (1), newer created_at ('2026-09-25T12:00:00Z'), gid='bbbb'
        # Row 2: higher id (2), older created_at ('2026-09-24T12:00:00Z'), gid='aaaa'
        conn.execute("""
            INSERT INTO tracker_dump (id, portal, period_label, status, dataset_key, created_at, gid)
            VALUES (1, 'Income Tax', 'AY 2026-27', 'submitted', 'ITD:PAN1:FORM:2026', '2026-09-25T12:00:00Z', 'bbbb')
        """)
        conn.execute("""
            INSERT INTO tracker_dump (id, portal, period_label, status, dataset_key, created_at, gid)
            VALUES (2, 'Income Tax', 'AY 2026-27', 'submitted', 'ITD:PAN1:FORM:2026', '2026-09-24T12:00:00Z', 'aaaa')
        """)

    # Run deduplication
    db.deduplicate_tracker_dumps()

    with db._connect_raw() as conn:
        rows = conn.execute("SELECT id, created_at, gid FROM tracker_dump").fetchall()
        assert len(rows) == 1
        # Row 1 (id=1, newer created_at) survived, NOT row 2 (MAX id)
        assert rows[0][0] == 1
        assert rows[0][1] == "2026-09-25T12:00:00Z"
        assert rows[0][2] == "bbbb"

    db.close()


def test_resequence_client_serial_numbers_orders_by_created_at_and_gid(tmp_path):
    """Verifies that resequence_client_serial_numbers orders clients by
    (created_at, gid) rather than id."""
    db_path = tmp_path / "master.db"
    raw_path = tmp_path / "rawPayload.db"
    hex_key = "a" * 64

    db = SeraDatabase(str(db_path), hex_key, raw_db_path=str(raw_path), defer_startup_maintenance=True)

    # Ensure an ID column exists
    with db._connect() as conn:
        conn.execute("INSERT OR REPLACE INTO mcl_columns (id, label, field_type, is_identity, sort_order) VALUES (1, 'No.', 'id', 1, 1)")

        # Client A: id=1, newer created_at ('2026-09-25T12:00:00Z'), gid='bbbb'
        # Client B: id=2, older created_at ('2026-09-25T10:00:00Z'), gid='aaaa'
        conn.execute("INSERT INTO clients (id, created_at, updated_at, is_archived, gid) VALUES (1, '2026-09-25T12:00:00Z', '2026-09-25T12:00:00Z', 0, 'bbbb')")
        conn.execute("INSERT INTO clients (id, created_at, updated_at, is_archived, gid) VALUES (2, '2026-09-25T10:00:00Z', '2026-09-25T10:00:00Z', 0, 'aaaa')")

    db.resequence_client_serial_numbers()

    with db._connect() as conn:
        # Client B (id=2, older created_at) must receive serial '1'
        # Client A (id=1, newer created_at) must receive serial '2'
        s1 = conn.execute("SELECT value FROM client_values WHERE client_id=1 AND column_id=1").fetchone()[0]
        s2 = conn.execute("SELECT value FROM client_values WHERE client_id=2 AND column_id=1").fetchone()[0]
        assert s2 == "1"
        assert s1 == "2"

    db.close()


def test_add_client_token_and_serial_number(tmp_path):
    """Verifies add_client produces D8 letter tokens on office PCs and max(serials)+1 in ID column."""
    with SyncHarness(num_nodes=2, base_dir=tmp_path / "harness_tokens") as harness:
        admin, joiner = harness.nodes[0], harness.nodes[1]

        # 1. Admin adds clients (letter 'A')
        with admin.open_db() as conn:
            # Ensure ID column is present
            conn.execute("INSERT OR REPLACE INTO mcl_columns (id, label, field_type, is_identity, sort_order) VALUES (1, 'No.', 'id', 1, 1)")
            pan_col = conn.execute("SELECT id FROM mcl_columns WHERE is_internal_pk = 1").fetchone()
            pan_id = pan_col[0] if pan_col else 2

        cid1 = admin.db.add_client(values={pan_id: "ABCDE1234F"}, notes="First on A", service_ids=[])
        cid2 = admin.db.add_client(values={pan_id: "BCDEF2345G"}, notes="Second on A", service_ids=[])

        with admin.open_db() as conn:
            tok1 = conn.execute("SELECT client_id_token FROM clients WHERE id=?", (cid1,)).fetchone()[0]
            tok2 = conn.execute("SELECT client_id_token FROM clients WHERE id=?", (cid2,)).fetchone()[0]
            assert tok1 == "A-1"
            assert tok2 == "A-2"

            ser1 = conn.execute("SELECT value FROM client_values WHERE client_id=? AND column_id=1", (cid1,)).fetchone()[0]
            ser2 = conn.execute("SELECT value FROM client_values WHERE client_id=? AND column_id=1", (cid2,)).fetchone()[0]
            assert ser1 == "1"
            assert ser2 == "2"

        # 2. Joiner adds a client (letter 'B')
        with joiner.open_db() as conn:
            conn.execute("INSERT OR REPLACE INTO mcl_columns (id, label, field_type, is_identity, sort_order) VALUES (1, 'No.', 'id', 1, 1)")
            # Pre-populate an existing serial 2 on joiner so max is 2
            conn.execute("INSERT INTO clients (id, notes, is_archived, created_at, updated_at) VALUES (999, 'tmp', 0, '2026-09-25T00:00:00Z', '2026-09-25T00:00:00Z')")
            conn.execute("INSERT INTO client_values (client_id, column_id, value) VALUES (999, 1, '2') ON CONFLICT DO NOTHING")
            pan_col = conn.execute("SELECT id FROM mcl_columns WHERE is_internal_pk = 1").fetchone()
            j_pan_id = pan_col[0] if pan_col else 2

        cid3 = joiner.db.add_client(values={j_pan_id: "CDEFG3456H"}, notes="First on B", service_ids=[])
        with joiner.open_db() as conn:
            tok3 = conn.execute("SELECT client_id_token FROM clients WHERE id=?", (cid3,)).fetchone()[0]
            assert tok3 == "B-1"
            ser3 = conn.execute("SELECT value FROM client_values WHERE client_id=? AND column_id=1", (cid3,)).fetchone()[0]
            assert ser3 == "3"

    # 3. Legacy mode fallback
    leg_dir = tmp_path / "legacy_tokens"
    leg_dir.mkdir()
    leg_db = SeraDatabase(str(leg_dir / "master.db"), "a" * 64, raw_db_path=str(leg_dir / "rawPayload.db"), defer_startup_maintenance=True)
    with leg_db._connect() as conn:
        conn.execute("INSERT OR REPLACE INTO mcl_columns (id, label, field_type, is_identity, sort_order) VALUES (1, 'No.', 'id', 1, 1)")
        pan_col = conn.execute("SELECT id FROM mcl_columns WHERE is_internal_pk = 1").fetchone()
        l_pan_id = pan_col[0] if pan_col else 2
    c_leg = leg_db.add_client(values={l_pan_id: "DEFGH4567I"}, notes="Legacy client", service_ids=[])
    with leg_db._connect() as conn:
        tok_leg = conn.execute("SELECT client_id_token FROM clients WHERE id=?", (c_leg,)).fetchone()[0]
        assert tok_leg == str(c_leg)
    leg_db.close()


def test_dataset_key_uses_client_gid(tmp_path):
    """Verifies that dataset_key computation in insert_tracker_dump and _init_raw_schema uses f'CLI_{gid}'."""
    db_path = tmp_path / "master.db"
    raw_path = tmp_path / "rawPayload.db"
    hex_key = "a" * 64

    db = SeraDatabase(str(db_path), hex_key, raw_db_path=str(raw_path), defer_startup_maintenance=True)

    with db._connect() as conn:
        conn.execute("INSERT INTO clients (id, created_at, updated_at, is_archived, gid) VALUES (42, '2026-09-25T12:00:00Z', '2026-09-25T12:00:00Z', 0, 'deadbeefcafebabe0123456789abcdef')")

    # Store tracker dump for client 42 without PAN
    dump = db.insert_tracker_dump(
        portal="Income Tax",
        period_label="AY 2026-27",
        client_id=42,
        status="submitted",
        filing_type="ITR-1"
    )

    expected_key = db.compute_dataset_key(
        portal="Income Tax",
        identifier="CLI_deadbeefcafebabe0123456789abcdef",
        form_type="ITR-1",
        period_label="AY 2026-27"
    )
    assert dump["dataset_key"] == expected_key
    assert "DEADBEEFCAFEBABE0123456789ABCDEF" in dump["dataset_key"]

    # Also test _init_raw_schema recompute upgrading legacy f'CLI_{id}' to f'CLI_{gid}'
    with db._connect_raw() as r_conn:
        r_conn.execute("UPDATE tracker_dump SET dataset_key = 'ITR:CLI42:ITR1:AY_2026_27' WHERE id = ?", (dump["id"],))

    # Re-running _init_raw_schema recomputes it
    db._init_raw_schema()

    with db._connect_raw() as r_conn:
        row = r_conn.execute("SELECT dataset_key FROM tracker_dump WHERE id = ?", (dump["id"],)).fetchone()
        assert row[0] == expected_key

    db.close()


def test_debounced_serial_resequence_on_admin(tmp_path):
    """Verifies that apply_changes touching 'clients' triggers debounced resequence on admin PC."""
    with SyncHarness(num_nodes=2, base_dir=tmp_path / "harness_reseq_debounce") as harness:
        admin = harness.nodes[0]
        joiner = harness.nodes[1]

        with patch.object(admin.db, "resequence_client_serial_numbers") as a_reseq:
            # Simulate apply_changes touching clients on admin
            mock_res = MagicMock()
            mock_res.tables = {"clients"}
            mock_res.applied = 1
            mock_res.unparked = 0
            mock_res.merges = []

            changes = [{"tbl": "clients", "op": "upsert"}]

            with patch("sync_apply.apply_batch", return_value=mock_res), \
                 patch("sync_apply.run_raw_repoints"), \
                 patch("sync_apply.retry_parked", return_value=MagicMock(tables=set(), unparked=0)):
                admin.db.apply_changes("master", changes)
                assert a_reseq.call_count == 1
                assert not admin.db._resequence_pending

                # Immediate second call is debounced (< 600s), but records pending flag
                admin.db.apply_changes("master", changes)
                assert a_reseq.call_count == 1
                assert admin.db._resequence_pending is True

                # After 601s, it runs again and clears pending flag
                admin.db._last_resequence_ts -= 601.0
                admin.db.apply_changes("master", [])
                assert a_reseq.call_count == 2
                assert not admin.db._resequence_pending

        # On joiner (non-admin), resequence is NEVER triggered by apply_changes
        with patch.object(joiner.db, "resequence_client_serial_numbers") as j_reseq:
            with patch("sync_apply.apply_batch", return_value=mock_res), \
                 patch("sync_apply.run_raw_repoints"), \
                 patch("sync_apply.retry_parked", return_value=MagicMock(tables=set(), unparked=0)):
                joiner.db.apply_changes("master", [{"tbl": "clients", "op": "upsert"}])
                assert not j_reseq.called


def test_harness_nodes_produce_identical_digests_after_both_restart(tmp_path):
    """Acceptance test for P3-6:
    The harness shows two nodes produce identical digests after both restart
    (maintenance doesn't make them diverge).
    Exercises:
    - Different local client IDs across the two nodes.
    - Out-of-order serials on admin PC repaired by startup maintenance.
    - A client without a PAN where dataset_key is repaired to CLI_{gid}.
    """
    with SyncHarness(num_nodes=2, base_dir=tmp_path / "harness_restart_digest") as harness:
        n0 = harness.nodes[0]  # Admin
        n1 = harness.nodes[1]  # Joiner

        # Node 0 has local IDs: 10, 20, 30
        # Node 1 has local IDs: 101, 102, 103
        # Both share the same gids: gid-client-1, gid-client-2, gid-client-3
        # Client 3 has NO PAN.
        def populate_admin(conn):
            conn.execute("UPDATE mcl_columns SET field_type = 'id', label = 'No.' WHERE id = 1")
            pan_col = conn.execute("SELECT id FROM mcl_columns WHERE is_internal_pk = 1").fetchone()
            pan_id = pan_col[0] if pan_col else 5
            # Client 1: 10:00:00Z
            conn.execute("INSERT INTO clients (id, notes, is_archived, created_at, updated_at, client_id_token, gid) VALUES (10, 'Client 1', 0, '2026-09-25T10:00:00Z', '2026-09-25T10:00:00Z', 'A-1', 'gid-client-1')")
            conn.execute("INSERT INTO client_values (client_id, column_id, value) VALUES (10, 1, '99')")  # Out of order serial
            conn.execute("INSERT INTO client_values (client_id, column_id, value) VALUES (10, ?, 'ABCDE1234F')", (pan_id,))
            # Client 2: 11:00:00Z
            conn.execute("INSERT INTO clients (id, notes, is_archived, created_at, updated_at, client_id_token, gid) VALUES (20, 'Client 2', 0, '2026-09-25T11:00:00Z', '2026-09-25T11:00:00Z', 'A-2', 'gid-client-2')")
            conn.execute("INSERT INTO client_values (client_id, column_id, value) VALUES (20, 1, '1')")  # Out of order serial
            conn.execute("INSERT INTO client_values (client_id, column_id, value) VALUES (20, ?, 'BCDEF2345G')", (pan_id,))
            # Client 3: 12:00:00Z (NO PAN)
            conn.execute("INSERT INTO clients (id, notes, is_archived, created_at, updated_at, client_id_token, gid) VALUES (30, 'Client 3', 0, '2026-09-25T12:00:00Z', '2026-09-25T12:00:00Z', 'A-3', 'gid-client-3')")
            conn.execute("INSERT INTO client_values (client_id, column_id, value) VALUES (30, 1, '50')")  # Out of order serial

        def populate_joiner(conn):
            conn.execute("UPDATE mcl_columns SET field_type = 'id', label = 'No.' WHERE id = 1")
            pan_col = conn.execute("SELECT id FROM mcl_columns WHERE is_internal_pk = 1").fetchone()
            pan_id = pan_col[0] if pan_col else 5
            # Client 1: 10:00:00Z (Converged serial 1)
            conn.execute("INSERT INTO clients (id, notes, is_archived, created_at, updated_at, client_id_token, gid) VALUES (101, 'Client 1', 0, '2026-09-25T10:00:00Z', '2026-09-25T10:00:00Z', 'A-1', 'gid-client-1')")
            conn.execute("INSERT INTO client_values (client_id, column_id, value) VALUES (101, 1, '1')")
            conn.execute("INSERT INTO client_values (client_id, column_id, value) VALUES (101, ?, 'ABCDE1234F')", (pan_id,))
            # Client 2: 11:00:00Z (Converged serial 2)
            conn.execute("INSERT INTO clients (id, notes, is_archived, created_at, updated_at, client_id_token, gid) VALUES (102, 'Client 2', 0, '2026-09-25T11:00:00Z', '2026-09-25T11:00:00Z', 'A-2', 'gid-client-2')")
            conn.execute("INSERT INTO client_values (client_id, column_id, value) VALUES (102, 1, '2')")
            conn.execute("INSERT INTO client_values (client_id, column_id, value) VALUES (102, ?, 'BCDEF2345G')", (pan_id,))
            # Client 3: 12:00:00Z (NO PAN, Converged serial 3)
            conn.execute("INSERT INTO clients (id, notes, is_archived, created_at, updated_at, client_id_token, gid) VALUES (103, 'Client 3', 0, '2026-09-25T12:00:00Z', '2026-09-25T12:00:00Z', 'A-3', 'gid-client-3')")
            conn.execute("INSERT INTO client_values (client_id, column_id, value) VALUES (103, 1, '3')")

        n0.write(populate_admin)
        n1.write(populate_joiner)

        # Dataset keys for Client 1 and Client 2
        dkey1 = n0.db.compute_dataset_key(portal="Income Tax", identifier="ABCDE1234F", form_type="ITR-1", period_label="AY 2026-27")
        dkey2 = n0.db.compute_dataset_key(portal="Income Tax", identifier="BCDEF2345G", form_type="ITR-1", period_label="AY 2026-27")
        # Canonical dataset key for Client 3 (no PAN -> CLI_<gid>)
        dkey3_canonical = n0.db.compute_dataset_key(portal="Income Tax", identifier="CLI_gid-client-3", form_type="ITR-1", period_label="AY 2026-27")

        payload1 = json.dumps({"pan": "ABCDE1234F", "filing_type": "ITR-1"})
        payload2 = json.dumps({"pan": "BCDEF2345G", "filing_type": "ITR-1"})
        payload3 = json.dumps({"filing_type": "ITR-1"})  # No PAN

        with n0.open_raw_db() as r_conn:
            r_conn.execute("INSERT INTO tracker_dump (id, client_id, portal, period_label, status, dataset_key, created_at, gid, raw_payload_json) VALUES (1, 10, 'Income Tax', 'AY 2026-27', 'submitted', ?, '2026-09-25T10:30:00Z', 'gid-dump-1', ?)", (dkey1, payload1))
            r_conn.execute("INSERT INTO tracker_dump (id, client_id, portal, period_label, status, dataset_key, created_at, gid, raw_payload_json) VALUES (2, 20, 'Income Tax', 'AY 2026-27', 'submitted', ?, '2026-09-25T11:30:00Z', 'gid-dump-2', ?)", (dkey2, payload2))
            # On Node 0 (Admin), Client 3 has legacy dataset_key with local ID 'CLI_30'
            legacy_dkey3 = "ITD:CLI_30:ITR-1:AY 2026-27"
            r_conn.execute("INSERT INTO tracker_dump (id, client_id, portal, period_label, status, dataset_key, created_at, gid, raw_payload_json) VALUES (3, 30, 'Income Tax', 'AY 2026-27', 'submitted', ?, '2026-09-25T12:30:00Z', 'gid-dump-3', ?)", (legacy_dkey3, payload3))

        with n1.open_raw_db() as r_conn:
            r_conn.execute("INSERT INTO tracker_dump (id, client_id, portal, period_label, status, dataset_key, created_at, gid, raw_payload_json) VALUES (101, 101, 'Income Tax', 'AY 2026-27', 'submitted', ?, '2026-09-25T10:30:00Z', 'gid-dump-1', ?)", (dkey1, payload1))
            r_conn.execute("INSERT INTO tracker_dump (id, client_id, portal, period_label, status, dataset_key, created_at, gid, raw_payload_json) VALUES (102, 102, 'Income Tax', 'AY 2026-27', 'submitted', ?, '2026-09-25T11:30:00Z', 'gid-dump-2', ?)", (dkey2, payload2))
            # On Node 1 (Joiner), Client 3 already has the canonical dataset_key
            r_conn.execute("INSERT INTO tracker_dump (id, client_id, portal, period_label, status, dataset_key, created_at, gid, raw_payload_json) VALUES (103, 103, 'Income Tax', 'AY 2026-27', 'submitted', ?, '2026-09-25T12:30:00Z', 'gid-dump-3', ?)", (dkey3_canonical, payload3))

        # Checkpoint WAL
        with n0.open_db() as conn: conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        with n0.open_raw_db() as rconn: rconn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        with n1.open_db() as conn: conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        with n1.open_raw_db() as rconn: rconn.execute("PRAGMA wal_checkpoint(TRUNCATE);")

        # Close running databases on both nodes
        n0.db.close()
        n1.db.close()
        n0._db = None
        n1._db = None

        # Reopen with defer_startup_maintenance=False (full startup maintenance runs!)
        db0 = SeraDatabase(
            str(n0.db_path),
            n0.hex_key,
            raw_db_path=str(n0.raw_db_path),
            defer_startup_maintenance=False,
            key_mode="office",
        )
        db1 = SeraDatabase(
            str(n1.db_path),
            n1.hex_key,
            raw_db_path=str(n1.raw_db_path),
            defer_startup_maintenance=False,
            key_mode="office",
        )

        n0._db = db0
        n1._db = db1

        # Checkpoint WAL frames
        with n0.open_db() as conn: conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        with n0.open_raw_db() as rconn: rconn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        with n1.open_db() as conn: conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        with n1.open_raw_db() as rconn: rconn.execute("PRAGMA wal_checkpoint(TRUNCATE);")

        # Verify Node 0's serials were resequenced in order of (created_at, gid):
        # Client 1 -> '1', Client 2 -> '2', Client 3 -> '3'
        with n0.open_db() as conn:
            s1 = conn.execute("SELECT value FROM client_values WHERE client_id=10 AND column_id=1").fetchone()[0]
            s2 = conn.execute("SELECT value FROM client_values WHERE client_id=20 AND column_id=1").fetchone()[0]
            s3 = conn.execute("SELECT value FROM client_values WHERE client_id=30 AND column_id=1").fetchone()[0]
            assert s1 == "1"
            assert s2 == "2"
            assert s3 == "3"

        # Verify Node 0's legacy dataset key for Client 3 was updated to canonical CLI_{gid}
        with n0.open_raw_db() as rconn:
            td3_key = rconn.execute("SELECT dataset_key FROM tracker_dump WHERE id=3").fetchone()[0]
            assert td3_key == dkey3_canonical

        # Digests MUST be identical after both nodes restart and run maintenance
        d0_after = harness.digest(n0)
        d1_after = harness.digest(n1)
        assert d0_after == d1_after
