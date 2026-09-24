"""Convergence tests for Sera Sync v3 (blueprint §5 WP P3-0).

Each test ends with all node digests equal.
Tests (a)–(f) are written first in P3-0; they fail before P3-3 starts and become the
definition of done for P3-3 (triggers/HLC), P3-4 (apply engine), and P3-5 (session protocol).
Test (g) exercises compaction / NEED_SNAPSHOT from P4-4.

Requirements from blueprint §5 P3-0:
  (a) 3 nodes, random 200 operations (create/edit/archive/delete client, set values,
      add services, audit entries, tracker dumps), random partitions, then heal.
  (b) the same field edited on two partitioned nodes: the higher HLC wins everywhere.
  (c) different fields of the same client edited on two nodes: both edits survive.
  (d) delete versus concurrent edit: delete wins and a _sync_conflicts row exists.
  (e) a crash in the middle of applying a batch (injected exception) leaves no partial
      state, and resync completes.
  (f) A->B->C forwarding with A and C never connected.
  (g) a node offline for a long time (compaction, P4-4) gets NEED_SNAPSHOT and still
      converges without losing its own unsent edits.

Schema notes (confirmed against live DB in P3-0):
  - services: columns (id, name, login_page_link, userid_column_id, password_column_id,
      username_selector, password_selector, automation_mode, extension_flow,
      success_selector, arn_selector, sort_order)  — no "portal" column.
  - audit_log: columns (id, ts, actor, action, client_id, service_id, detail)
      — column is "ts", not "timestamp".
  - tracker_dump: columns (id, client_id, unassigned_identity, service_id, portal,
      period_label, arn_number, capture_method, status, raw_payload_json, captured_by,
      created_at, dataset_key, notes, screenshot_path)
"""

import random
import sys
from pathlib import Path

import pytest

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
pytestmark = windows_only

from tests.sync_harness import SyncHarness, digest  # noqa: E402


# ------------------------------------------------------------------ helpers

def _insert_client(conn, token: str, notes: str = "Note", ts: str = "2026-09-24T10:00:00Z"):
    """Insert a client row into master.db using the real schema."""
    cur = conn.execute(
        "INSERT INTO clients (notes, is_archived, created_at, updated_at, client_id_token) "
        "VALUES (?, 0, ?, ?, ?)",
        (notes, ts, ts, token),
    )
    return cur.lastrowid


def _insert_service(conn, name: str):
    """Insert a service row (no portal column)."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO services (name) VALUES (?)",
        (name,),
    )
    return cur.lastrowid


# ------------------------------------------------------------------ Test (a)

def test_convergence_random_operations_and_partitions(tmp_path):
    """(a) 3 nodes, random 200 operations (create/edit/archive/delete client,
    set values, add services, audit entries, tracker dumps), random partitions, then heal.
    Ends with all digests equal.
    """
    with SyncHarness(num_nodes=3, base_dir=tmp_path / "conv_a") as harness:
        nodes = harness.nodes
        rng = random.Random(1337)

        created_tokens: list[str] = []

        for i in range(200):
            node = rng.choice(nodes)
            op = rng.choice([
                "create_client",
                "edit_client",
                "archive_client",
                "delete_client",
                "set_value",
                "add_service",
                "audit_entry",
                "tracker_dump",
            ])

            if op == "create_client":
                token = f"TOK-{len(created_tokens):04d}"
                created_tokens.append(token)

                def do_create(conn, t=token):
                    cid = _insert_client(conn, t)
                    conn.execute(
                        "INSERT OR IGNORE INTO client_values (client_id, column_id, value) "
                        "VALUES (?, 1, ?)",
                        (cid, f"PAN{t}"),
                    )

                node.write(do_create)

            elif op == "edit_client" and created_tokens:
                token = rng.choice(created_tokens)
                new_notes = rng.choice(["active", "pending", "reviewed", "urgent"])

                def do_edit(conn, t=token, n=new_notes):
                    conn.execute(
                        "UPDATE clients SET notes = ?, updated_at = ? WHERE client_id_token = ?",
                        (n, "2026-09-24T12:01:00Z", t),
                    )

                node.write(do_edit)

            elif op == "archive_client" and created_tokens:
                token = rng.choice(created_tokens)

                def do_archive(conn, t=token):
                    conn.execute(
                        "UPDATE clients SET is_archived = 1, updated_at = ? "
                        "WHERE client_id_token = ?",
                        ("2026-09-24T12:02:00Z", t),
                    )

                node.write(do_archive)

            elif op == "delete_client" and created_tokens:
                token = created_tokens.pop()

                def do_delete(conn, t=token):
                    conn.execute("DELETE FROM clients WHERE client_id_token = ?", (t,))

                node.write(do_delete)

            elif op == "set_value" and created_tokens:
                token = rng.choice(created_tokens)

                def do_val(conn, t=token):
                    row = conn.execute(
                        "SELECT id FROM clients WHERE client_id_token = ?", (t,)
                    ).fetchone()
                    if row:
                        conn.execute(
                            "INSERT OR REPLACE INTO client_values (client_id, column_id, value) "
                            "VALUES (?, 2, 'TestVal')",
                            (row[0],),
                        )

                node.write(do_val)

            elif op == "add_service":
                svc_name = f"Service_{rng.randint(1, 10)}"

                def do_svc(conn, s=svc_name):
                    _insert_service(conn, s)

                node.write(do_svc)

            elif op == "audit_entry":
                def do_audit(conn):
                    conn.execute(
                        "INSERT INTO audit_log (actor, action, ts, detail) "
                        "VALUES ('tester', 'op', ?, 'detail')",
                        ("2026-09-24T12:04:00Z",),
                    )

                node.write(do_audit)

            elif op == "tracker_dump":
                def do_raw(node_ref):
                    with node_ref.open_raw_db() as rconn:
                        rconn.execute(
                            "INSERT INTO tracker_dump "
                            "(portal, arn_number, created_at, status) "
                            "VALUES ('GST', 'ARN12345678', '2026-09-24T12:05:00Z', 'submitted')"
                        )

                node.write(do_raw)

            # Random partitions and heals throughout execution.
            if i % 25 == 0:
                p_nodes = rng.sample(nodes, 2)
                harness.partition(p_nodes[0], p_nodes[1])
            elif i % 40 == 0:
                harness.heal()

        # Heal all partitions and wait for replication to settle.
        harness.heal()
        harness.run_until_quiet(timeout=5.0)

        # Definition of done: all node digests must converge.
        d0 = harness.digest(nodes[0])
        d1 = harness.digest(nodes[1])
        d2 = harness.digest(nodes[2])
        assert d0 == d1 == d2, (
            "All 3 nodes must converge to identical digests after random operations and heal"
        )


# ------------------------------------------------------------------ Test (b)

def test_convergence_same_field_concurrent_edit_higher_hlc_wins(tmp_path, monkeypatch):
    """(b) The same field edited on two partitioned nodes: the higher HLC wins everywhere.

    Setup: create the client on node 0 only, sync to node 1 so both nodes hold the
    same row (identical gid from P3-2), then partition.  Node 0 edits notes at t0,
    and node 1 edits notes at a later clock time (guaranteed by monkeypatching time.time
    so node 1's edit has a strictly higher HLC physical component).  After healing,
    both nodes must converge and node 1's edit must win on both.
    """
    with SyncHarness(num_nodes=2, base_dir=tmp_path / "conv_b") as harness:
        n0, n1 = harness.nodes[0], harness.nodes[1]
        token = "TOK-B-9999"

        # 1. Create client on node 0 only
        n0.write(lambda conn: _insert_client(conn, token, notes="Baseline", ts="2026-09-24T10:00:00Z"))

        # 2. Sync to node 1 so both nodes share the identical client row (same gid)
        harness.run_until_quiet(timeout=5.0)

        # Confirm node 1 has it before partitioning (fails in P3-0 until sync is implemented)
        with n1.open_db() as c1:
            row1 = c1.execute("SELECT id FROM clients WHERE client_id_token = ?", (token,)).fetchone()
            assert row1 is not None, f"Node 1 must have received {token} via sync before partitioning"

        # 3. Partition the nodes
        harness.partition(n0, n1)

        # 4. Node 0 writes edit with lower HLC / earlier clock
        import time as _time
        t_base = _time.time()
        monkeypatch.setattr("time.time", lambda: t_base)
        n0.write(lambda conn: conn.execute(
            "UPDATE clients SET notes = 'Node0 edit (loses)', updated_at = '2026-09-24T11:00:00Z' "
            "WHERE client_id_token = ?",
            (token,),
        ))

        # 5. Node 1 writes edit with strictly higher HLC / later clock (+1000s)
        monkeypatch.setattr("time.time", lambda: t_base + 1000.0)
        n1.write(lambda conn: conn.execute(
            "UPDATE clients SET notes = 'Node1 edit (wins)', updated_at = '2026-09-24T12:00:00Z' "
            "WHERE client_id_token = ?",
            (token,),
        ))

        # 6. Heal and sync
        harness.heal()
        harness.run_until_quiet(timeout=5.0)

        # Both nodes must reach identical digests
        assert harness.digest(n0) == harness.digest(n1), (
            "Both nodes must converge to the same digest"
        )

        # The higher-timestamp edit must win on both nodes
        for node in (n0, n1):
            with node.open_db() as c:
                val = c.execute(
                    "SELECT notes FROM clients WHERE client_id_token = ?", (token,)
                ).fetchone()[0]
            assert val == "Node1 edit (wins)", (
                f"Higher-HLC edit must win on {node.name}, got {val!r}"
            )


# ------------------------------------------------------------------ Test (c)

def test_convergence_different_fields_concurrent_edits_both_survive(tmp_path):
    """(c) Different fields of the same client edited on two nodes: both edits survive (D2).

    Setup: create the client on node 0 only, sync to node 1 so both nodes hold the
    same row (identical gid from P3-2), then partition.  Node 0 edits column 1,
    node 1 edits column 2.  After healing, both edits must survive on both nodes.
    """
    with SyncHarness(num_nodes=2, base_dir=tmp_path / "conv_c") as harness:
        n0, n1 = harness.nodes[0], harness.nodes[1]
        token = "TOK-C-8888"

        # 1. Create client on node 0 only
        n0.write(lambda conn: _insert_client(conn, token, notes="Base", ts="2026-09-24T10:00:00Z"))

        # 2. Sync to node 1 so both nodes share the identical client row (same gid)
        harness.run_until_quiet(timeout=5.0)

        with n1.open_db() as c1:
            row1 = c1.execute("SELECT id FROM clients WHERE client_id_token = ?", (token,)).fetchone()
            assert row1 is not None, f"Node 1 must have received {token} via sync before partitioning"

        # 3. Partition the nodes
        harness.partition(n0, n1)

        # Node 0 edits column_id 1 in client_values
        def edit_col1(conn):
            cid = conn.execute(
                "SELECT id FROM clients WHERE client_id_token = ?", (token,)
            ).fetchone()[0]
            conn.execute(
                "INSERT OR REPLACE INTO client_values (client_id, column_id, value) "
                "VALUES (?, 1, 'Value From Node 0')",
                (cid,),
            )

        n0.write(edit_col1)

        # Node 1 edits column_id 2 in client_values
        def edit_col2(conn):
            cid = conn.execute(
                "SELECT id FROM clients WHERE client_id_token = ?", (token,)
            ).fetchone()[0]
            conn.execute(
                "INSERT OR REPLACE INTO client_values (client_id, column_id, value) "
                "VALUES (?, 2, 'Value From Node 1')",
                (cid,),
            )

        n1.write(edit_col2)

        # Heal and sync
        harness.heal()
        harness.run_until_quiet(timeout=5.0)

        assert harness.digest(n0) == harness.digest(n1), (
            "Both nodes must converge to the same digest"
        )

        # Verify both field edits survived on both nodes
        for node in (n0, n1):
            with node.open_db() as conn:
                cid = conn.execute(
                    "SELECT id FROM clients WHERE client_id_token = ?", (token,)
                ).fetchone()[0]
                rows = dict(conn.execute(
                    "SELECT column_id, value FROM client_values WHERE client_id = ?",
                    (cid,),
                ).fetchall())
            assert rows.get(1) == "Value From Node 0", (
                f"Field 1 edit must survive on {node.name}"
            )
            assert rows.get(2) == "Value From Node 1", (
                f"Field 2 edit must survive on {node.name}"
            )


# ------------------------------------------------------------------ Test (d)

def test_convergence_delete_vs_concurrent_edit(tmp_path):
    """(d) Delete versus concurrent edit: delete wins and a _sync_conflicts row exists.

    Setup: create the client on node 0 only, sync to node 1 so both nodes operate on
    the same logical row (identical gid from P3-2), then partition.  Node 0 deletes
    the client while node 1 edits it.
    """
    with SyncHarness(num_nodes=2, base_dir=tmp_path / "conv_d") as harness:
        n0, n1 = harness.nodes[0], harness.nodes[1]
        token = "TOK-D-7777"

        # 1. Create client on node 0 only
        n0.write(lambda conn: _insert_client(conn, token, notes="Pre-delete", ts="2026-09-24T10:00:00Z"))

        # 2. Sync to node 1 so both nodes share the identical client row (same gid)
        harness.run_until_quiet(timeout=5.0)

        with n1.open_db() as c1:
            row1 = c1.execute("SELECT id FROM clients WHERE client_id_token = ?", (token,)).fetchone()
            assert row1 is not None, f"Node 1 must have received {token} via sync before partitioning"

        # 3. Partition the nodes
        harness.partition(n0, n1)

        # Node 0 deletes the client
        n0.write(lambda conn: conn.execute(
            "DELETE FROM clients WHERE client_id_token = ?", (token,)
        ))

        # Node 1 edits it concurrently
        n1.write(lambda conn: conn.execute(
            "UPDATE clients SET notes = 'Concurrent Edit On Node 1', "
            "updated_at = '2026-09-24T11:00:00Z' WHERE client_id_token = ?",
            (token,),
        ))

        harness.heal()
        harness.run_until_quiet(timeout=5.0)

        assert harness.digest(n0) == harness.digest(n1), (
            "Both nodes must converge to the same digest"
        )

        # Delete must win on both nodes
        for node in (n0, n1):
            with node.open_db() as conn:
                row = conn.execute(
                    "SELECT id FROM clients WHERE client_id_token = ?", (token,)
                ).fetchone()
                assert row is None, f"Deleted client must remain deleted on {node.name}"

        # A conflict row must exist on node 0 recording the discarded concurrent edit
        with n0.open_db() as conn:
            conflicts = conn.execute(
                "SELECT count(*) FROM _sync_conflicts"
            ).fetchone()[0]
            assert conflicts > 0, "Conflict must be recorded in _sync_conflicts on n0"


# ------------------------------------------------------------------ Test (e)

def test_convergence_batch_crash_leaves_no_partial_state_and_resyncs(tmp_path):
    """(e) A crash in the middle of applying a batch (injected exception) leaves no partial
    state, and resync completes.

    Node 0 writes 5 clients while partitioned from node 1 so they accumulate into a single
    batch.  On node 1, a BEFORE INSERT ON clients trigger is installed that raises an ABORT
    exception once 3 of the batch's rows exist, simulating a crash in the middle of applying
    the batch.  The partition is healed and sync is triggered.  Because batch application
    must be atomic in a transaction, none of the batch rows should land on node 1 (0 rows),
    and node 1's _sync_vector for node 0 must not advance.  Then the trigger is dropped,
    resync completes, and both nodes converge with all 5 rows present.
    """
    with SyncHarness(num_nodes=2, base_dir=tmp_path / "conv_e") as harness:
        n0, n1 = harness.nodes[0], harness.nodes[1]

        # 1. Partition nodes first so node 0's writes accumulate into one batch
        harness.partition(n0, n1)

        # 2. Node 0 writes 5 batch clients while partitioned
        for idx in range(5):
            n0.write(lambda conn, i=idx: conn.execute(
                "INSERT INTO clients "
                "(notes, is_archived, created_at, updated_at, client_id_token) "
                "VALUES ('Batch client', 0, '2026-09-24T10:00:00Z', "
                "'2026-09-24T10:00:00Z', ?)",
                (f"BATCH-{i:05d}",),
            ))

        # 3. Install failure trigger on node 1 that aborts mid-batch (after 3 rows)
        with n1.open_db() as c1:
            c1.execute(
                """
                CREATE TRIGGER inject_crash_mid_batch
                BEFORE INSERT ON clients
                WHEN (SELECT count(*) FROM clients WHERE client_id_token LIKE 'BATCH-%') >= 3
                BEGIN
                    SELECT RAISE(ABORT, 'injected crash in middle of applying batch');
                END;
                """
            )

        # 4. Record node 1's initial vector for node 0 before sync
        initial_vector = {}
        with n1.open_db() as c1:
            if c1.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='_sync_vector'").fetchone():
                initial_vector = dict(c1.execute("SELECT origin, max_seq FROM _sync_vector").fetchall())

        # 5. Heal partition and trigger sync — expected to fail or time out due to injected crash
        harness.heal(n0, n1)
        try:
            harness.run_until_quiet(timeout=2.0)
        except (TimeoutError, Exception):
            pass

        # 6. Verify no partial state: exactly 0 batch rows must exist on node 1
        with n1.open_db() as c1:
            count = c1.execute(
                "SELECT count(*) FROM clients WHERE client_id_token LIKE 'BATCH-%'"
            ).fetchone()[0]
            assert count == 0, f"No partial batch rows must land on n1 after a crash: found {count}"

            # Verify that node 1's _sync_vector for node 0 did not advance
            has_sv = c1.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='_sync_vector'"
            ).fetchone()
            if has_sv:
                current_vector = dict(c1.execute("SELECT origin, max_seq FROM _sync_vector").fetchall())
                for origin, seq in current_vector.items():
                    if n0.device_id in origin:
                        init_seq = initial_vector.get(origin, 0)
                        assert seq == init_seq, (
                            f"_sync_vector for {origin} advanced from {init_seq} to {seq} despite aborted batch"
                        )

        # 7. Remove the failure trigger
        with n1.open_db() as c1:
            c1.execute("DROP TRIGGER IF EXISTS inject_crash_mid_batch")

        # 8. Resync cleanly
        harness.run_until_quiet(timeout=5.0)

        # After recovery / resync, both digests must match and all 5 rows must exist on n1
        assert harness.digest(n0) == harness.digest(n1), (
            "Both nodes must converge after crash recovery"
        )
        with n1.open_db() as c1:
            count_final = c1.execute(
                "SELECT count(*) FROM clients WHERE client_id_token LIKE 'BATCH-%'"
            ).fetchone()[0]
            assert count_final == 5, (
                f"All 5 batch rows must be present on n1 after resync, found {count_final}"
            )


# ------------------------------------------------------------------ Test (f)

def test_convergence_abc_forwarding_never_connected(tmp_path):
    """(f) A->B->C forwarding with A and C never connected.

    The harness propagates the full member roster to all nodes, so node B (index 1)
    knows about both A (index 0) and C (index 2) and can relay between them.
    """
    with SyncHarness(num_nodes=3, base_dir=tmp_path / "conv_f") as harness:
        nA, nB, nC = harness.nodes[0], harness.nodes[1], harness.nodes[2]

        # A and C are permanently partitioned (can only communicate via B).
        harness.partition(nA, nC)

        # Node A writes a client.
        nA.write(lambda conn: conn.execute(
            "INSERT INTO clients "
            "(notes, is_archived, created_at, updated_at, client_id_token) "
            "VALUES ('From A', 0, '2026-09-24T10:00:00Z', '2026-09-24T10:00:00Z', 'TOK-AAAA')"
        ))

        # Node C writes a client.
        nC.write(lambda conn: conn.execute(
            "INSERT INTO clients "
            "(notes, is_archived, created_at, updated_at, client_id_token) "
            "VALUES ('From C', 0, '2026-09-24T10:00:00Z', '2026-09-24T10:00:00Z', 'TOK-CCCC')"
        ))

        # Let forwarding occur via B.
        harness.run_until_quiet(timeout=5.0)

        dA = harness.digest(nA)
        dB = harness.digest(nB)
        dC = harness.digest(nC)
        assert dA == dB == dC, "All nodes must converge via transit forwarding through B"


# ------------------------------------------------------------------ Test (g)

@pytest.mark.skip(reason="Compaction / NEED_SNAPSHOT is implemented in Phase 4 (P4-4)")
def test_convergence_offline_node_snapshot_recovery(tmp_path):
    """(g) A node offline for a long time (compaction, P4-4) gets NEED_SNAPSHOT and still
    converges without losing its own unsent edits.
    """
    with SyncHarness(num_nodes=2, base_dir=tmp_path / "conv_g") as harness:
        n0, n1 = harness.nodes[0], harness.nodes[1]
        harness.partition(n0, n1)

        # Node 0 does extensive operations past the compaction threshold.
        # Node 1 writes an unsent local edit.
        # Reconnect: Node 1 receives NEED_SNAPSHOT, merges its unsent edits, and converges.
        harness.heal()
        harness.run_until_quiet(timeout=5.0)
        assert harness.digest(n0) == harness.digest(n1)
