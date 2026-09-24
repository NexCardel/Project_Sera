"""Tests for tests/sync_harness.py (Sera Sync v3, WP P3-0).

Verifies the multi-node test harness functionality:
1. Starting N in-process nodes on localhost under temp dirs.
2. Office key and device identity generation, pairing joiners through real P2-4 code.
3. Node write execution and persistence in master.db.
4. Network partition and heal controls.
5. Deterministic SHA-256 database digest calculation (P3-7).
6. Clean shutdown and resource reclamation.
"""

import ast
import sys
import time
from pathlib import Path

import pytest

import sera_keys
import sync_identity

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
pytestmark = windows_only

from tests.sync_harness import SyncHarness, digest  # noqa: E402


def test_harness_starts_nodes_and_pairs_via_p2_4(tmp_path):
    """Verifies that SyncHarness starts N nodes, pairs joiners via real P2-4 code,
    and sets up keys, identity, and database files for all nodes.
    """
    with SyncHarness(num_nodes=3, base_dir=tmp_path / "harness_setup") as harness:
        assert len(harness.nodes) == 3
        admin = harness.nodes[0]
        joiner_1 = harness.nodes[1]
        joiner_2 = harness.nodes[2]

        # All nodes have unique directories and device identities
        device_ids = {n.device_id for n in harness.nodes}
        assert len(device_ids) == 3

        # Admin node has admin key and office info
        office_a = sera_keys.load_office(admin.app_dir)
        assert office_a is not None
        assert office_a.admin_pubkey is not None
        assert (sera_keys.keys_dir(admin.app_dir) / sera_keys.ADMIN_KEY_DPAPI_FILE).exists()

        # Joiner nodes share the same office_id and key_id, but do not have admin_key.dpapi
        for j in (joiner_1, joiner_2):
            office_j = sera_keys.load_office(j.app_dir)
            assert office_j is not None
            assert office_j.office_id == office_a.office_id
            assert office_j.key_id == office_a.key_id
            assert office_j.admin_pubkey == office_a.admin_pubkey
            assert not (sera_keys.keys_dir(j.app_dir) / sera_keys.ADMIN_KEY_DPAPI_FILE).exists()
            # Verified pairing state exists
            assert (j.app_dir / "incoming" / "join" / "pairing.json").exists()

        # All nodes have distinct ports and servers
        ports = {n.port for n in harness.nodes}
        assert len(ports) == 3
        assert all(p > 0 for p in ports)

        # Baseline databases open and are identical initially
        d0 = harness.digest(admin)
        d1 = harness.digest(joiner_1)
        d2 = harness.digest(joiner_2)
        assert d0 == d1 == d2
        assert len(d0) == 64  # SHA-256 hex string


def test_harness_node_write_and_digest(tmp_path):
    """Verifies node.write(fn) executes mutations and digest(node) detects changes."""
    with SyncHarness(num_nodes=2, base_dir=tmp_path / "harness_write") as harness:
        n0, n1 = harness.nodes[0], harness.nodes[1]
        initial_digest = harness.digest(n0)
        assert harness.digest(n1) == initial_digest

        # Perform a write on node 0
        def write_op(conn):
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?)",
                ("office_theme", "dark"),
            )

        n0.write(write_op)

        # Node 0 digest changed, Node 1 digest unchanged
        d0_after = harness.digest(n0)
        d1_after = harness.digest(n1)
        assert d0_after != initial_digest
        assert d1_after == initial_digest
        assert d0_after != d1_after

        # Performing identical write on Node 1 brings digests back into equality
        n1.write(write_op)
        assert harness.digest(n0) == harness.digest(n1)


def test_harness_partition_and_heal(tmp_path):
    """Verifies partition(a, b) blocks communication and heal() restores it."""
    with SyncHarness(num_nodes=2, base_dir=tmp_path / "harness_net") as harness:
        n0, n1 = harness.nodes[0], harness.nodes[1]
        assert not harness.is_partitioned(0, 1)
        assert not harness.is_partitioned(1, 0)

        # Before partition: connection succeeds
        session = n0.connect(n1)
        assert session.peer_device_id == n1.device_id
        session.close()

        # Partition nodes
        harness.partition(0, 1)
        assert harness.is_partitioned(0, 1)
        assert harness.is_partitioned(1, 0)
        assert harness.is_partitioned(n0, n1)

        # Connect fails while partitioned
        with pytest.raises(Exception):
            n0.connect(n1)

        with pytest.raises(Exception):
            n1.connect(n0)

        # Heal restores connectivity
        harness.heal(0, 1)
        assert not harness.is_partitioned(0, 1)

        session2 = n0.connect(n1)
        assert session2.peer_device_id == n1.device_id
        session2.close()


def test_harness_context_manager_cleanup(tmp_path):
    """Verifies that SyncHarness context manager cleans up properly upon exit."""
    harness = SyncHarness(num_nodes=2, base_dir=tmp_path / "harness_cm")
    with harness:
        assert len(harness.nodes) == 2
        for n in harness.nodes:
            assert n.server is not None

    # After exit, servers are stopped
    for n in harness.nodes:
        assert n.server._server_socket is None or n.server._server_socket.fileno() == -1


def test_harness_raw_db_digest_and_helpers(tmp_path):
    """Verifies that modifications to rawPayload.db update digest and get_node works."""
    with SyncHarness(num_nodes=2, base_dir=tmp_path / "harness_raw") as harness:
        n0, n1 = harness.nodes[0], harness.nodes[1]
        assert harness.get_node(0) is n0
        assert harness.get_node(n0.device_id) is n0
        assert harness.get_node(n1.name) is n1

        with pytest.raises(KeyError):
            harness.get_node("non_existent_device_id")

        d_init = harness.digest(n0)

        # Write to rawPayload.db using real tracker_dump columns.
        # Schema: (id, client_id, unassigned_identity, service_id, portal,
        #          period_label, arn_number, capture_method, status, raw_payload_json,
        #          captured_by, created_at, dataset_key, notes, screenshot_path)
        def write_tracker(node):
            with node.open_raw_db() as rconn:
                rconn.execute(
                    "INSERT INTO tracker_dump (portal, arn_number, created_at, status, gid) "
                    "VALUES ('IncomeTax', 'ARN98765432', '2026-09-24T12:00:00Z', 'submitted', '11112222333344445555666677778888')"
                )

        n0.write(write_tracker)
        d_after = harness.digest(n0)
        assert d_after != d_init
        assert d_after != harness.digest(n1)

        # Same write on n1 restores equality.
        n1.write(write_tracker)
        assert harness.digest(n0) == harness.digest(n1)


def test_harness_run_until_quiet(tmp_path):
    """Verifies run_until_quiet returns promptly when there is no sync traffic."""
    with SyncHarness(num_nodes=2, base_dir=tmp_path / "harness_quiet") as harness:
        t0 = time.monotonic()
        harness.run_until_quiet(timeout=1.0, quiet_period=0.05)
        elapsed = time.monotonic() - t0
        assert elapsed < 0.5


def test_no_pyside6_import():
    """Verify tests/sync_harness.py does not import PySide6 (§0 rule 7)."""
    path = Path(__file__).resolve().parent / "sync_harness.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "PySide6" not in alias.name, f"Forbidden import: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                assert "PySide6" not in node.module, f"Forbidden import from: {node.module}"
