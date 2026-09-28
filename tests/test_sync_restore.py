"""Tests for Sera Sync v3 P4-3b: restoring a backup becomes the state on every PC (sync_restore, D9).

Accept (blueprint §5 P4-3b), with 3 harness nodes:
  - restore on A, and B and C converge to the backup state
    (``test_restore_on_a_and_b_and_c_converge_to_the_backup_state``);
  - an edit on B made after the restore survives (same test);
  - a client created on offline C before the restore survives, and the conflict log shows no
    silent loss (``test_client_created_on_offline_c_survives_the_restore``).
Plus: dry-run counts, a client deleted after the backup comes back, the change set is signed,
own streams have no gaps, audit entry, refusals, a failed install changes nothing.

Real harness nodes (P2-4 pairing, P2-3 mutual TLS); tmp_path only (§0 rule 2); invented data
only (§0 rule 12).
"""

import json
import shutil
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")

import sync_backup  # noqa: E402
import sync_capture  # noqa: E402
import sync_restore  # noqa: E402
from tests.sync_harness import SyncHarness  # noqa: E402


@pytest.fixture
def cluster(tmp_path):
    made = []

    def make(n=3):
        h = SyncHarness(num_nodes=n, base_dir=tmp_path / f"h{len(made)}")
        made.append(h)
        return h
    yield make
    for h in made:
        h.close()


# ---------------------------------------------------------------- helpers

def _name_column(node) -> int:
    with node.open_db() as conn:
        # Not "No." (the admin PC renumbers it) and not the internal-PK column (clients merge on it).
        return conn.execute("SELECT id FROM mcl_columns WHERE label = 'NAME OF COMPANY' AND is_internal_pk = 0"
                            ).fetchone()[0]


def _add_client(node, token, name):
    col = _name_column(node)

    def fn(conn):
        cid = conn.execute(
            "INSERT INTO clients (notes, is_archived, created_at, updated_at, client_id_token) "
            "VALUES ('n', 0, '2026-09-28T10:00:00Z', '2026-09-28T10:00:00Z', ?)", (token,)).lastrowid
        conn.execute("INSERT INTO client_values (client_id, column_id, value) VALUES (?, ?, ?)", (cid, col, name))
    node.write(fn)


def _set_name(node, token, name):
    col = _name_column(node)

    def fn(conn):
        cid = conn.execute("SELECT id FROM clients WHERE client_id_token = ?", (token,)).fetchone()[0]
        conn.execute("UPDATE client_values SET value = ? WHERE client_id = ? AND column_id = ?", (name, cid, col))
    node.write(fn)


def _name(node, token):
    col = _name_column(node)
    with node.open_db() as conn:
        row = conn.execute("SELECT cv.value FROM clients c JOIN client_values cv ON cv.client_id = c.id "
                           "WHERE c.client_id_token = ? AND cv.column_id = ?", (token, col)).fetchone()
    return row[0] if row else None


def _has(node, token) -> bool:
    with node.open_db() as conn:
        return conn.execute("SELECT 1 FROM clients WHERE client_id_token = ?", (token,)).fetchone() is not None


def _gid(node, token):
    with node.open_db() as conn:
        row = conn.execute("SELECT gid FROM clients WHERE client_id_token = ?", (token,)).fetchone()
    return row[0] if row else None


def _backup(node):
    return sync_backup.create_backup(node.app_dir, hex_key=node.hex_key, prefix="test-")


def _restore(h, node, backup, actor="Tester"):
    sync_restore.stage_restore(node.db, backup, actor=actor)
    return h.restart_node(node, before_open=lambda n: sync_restore.apply_pending_restore(n.app_dir, n.hex_key))


def _own_stream_state(node):
    with node.open_db() as conn:
        stream = sync_capture._meta(conn, "stream_id")
        vec = conn.execute("SELECT max_seq FROM _sync_vector WHERE origin = ?", (stream,)).fetchone()[0]
        nxt = int(sync_capture._meta(conn, "next_seq"))
        seqs = [r[0] for r in conn.execute("SELECT origin_seq FROM _sync_changes WHERE origin = ? ORDER BY origin_seq",
                                           (stream,))]
    return stream, vec, nxt, seqs


# ---------------------------------------------------------------- Accept

def test_restore_on_a_and_b_and_c_converge_to_the_backup_state(cluster):
    h = cluster(3)
    a, b, c = h.nodes
    _add_client(a, "R-X", "X original")
    _add_client(a, "R-Y", "Y original")
    h.run_until_quiet(timeout=20.0)
    backup = _backup(a)

    _set_name(a, "R-X", "X edited after backup")
    _add_client(b, "R-Z", "Z created after backup")
    h.run_until_quiet(timeout=20.0)
    assert _has(c, "R-Z") and _name(c, "R-X") == "X edited after backup"

    plan = sync_restore.plan_restore(a.db, backup)
    # At least X's name reverts; the admin PC's serial-number renumbering after Z arrived ("No."
    # column) reverts too, so the exact count depends on that.
    assert (plan.clients_back, plan.clients_removed) == (0, 1) and plan.fields_revert >= 1
    info = _restore(h, a, backup)
    assert info["clients_removed"] == 1 and info["fields_revert"] == plan.fields_revert

    h.run_until_quiet(timeout=20.0)
    for node in (a, b, c):
        assert not _has(node, "R-Z")
        assert _name(node, "R-X") == "X original"
        assert _name(node, "R-Y") == "Y original"
    assert h.digest(a) == h.digest(b) == h.digest(c)

    # An edit on B after the restore survives everywhere.
    _set_name(b, "R-X", "X edited on B after the restore")
    h.run_until_quiet(timeout=20.0)
    for node in (a, b, c):
        assert _name(node, "R-X") == "X edited on B after the restore"
    assert h.digest(a) == h.digest(b) == h.digest(c)

    # One audit entry for the restore, on every PC.
    for node in (a, b, c):
        with node.open_db() as conn:
            rows = conn.execute("SELECT actor, detail FROM audit_log WHERE action = ?",
                                (sync_restore.AUDIT_ACTION,)).fetchall()
        assert len(rows) == 1 and rows[0][0] == "Tester" and backup.name in rows[0][1]


def test_client_created_on_offline_c_survives_the_restore(cluster):
    h = cluster(3)
    a, b, c = h.nodes
    _add_client(a, "O-Y", "Y original")
    h.run_until_quiet(timeout=20.0)
    backup = _backup(a)

    h.partition(a, c)
    h.partition(b, c)
    _add_client(c, "O-W", "W created offline")          # the admin PC never sees it
    _set_name(c, "O-Y", "Y edited offline before the restore")
    _add_client(a, "O-Z", "Z created after backup")
    h.run_until_quiet(timeout=20.0)

    _restore(h, a, backup)
    h.run_until_quiet(timeout=20.0)
    assert not _has(b, "O-Z")

    h.heal()
    h.run_until_quiet(timeout=20.0)
    assert h.digest(a) == h.digest(b) == h.digest(c)
    w_gid = _gid(c, "O-W")
    for node in (a, b, c):
        assert _has(node, "O-W") and _name(node, "O-W") == "W created offline"
        assert _name(node, "O-Y") == "Y original"        # older than the restore: loses
        assert not _has(node, "O-Z")
        with node.open_db() as conn:
            assert conn.execute("SELECT count(*) FROM _sync_conflicts WHERE row_key LIKE ?",
                                (f'%{w_gid}%',)).fetchone()[0] == 0
            assert conn.execute("SELECT count(*) FROM _sync_parked").fetchone()[0] == 0


# ---------------------------------------------------------------- details

def test_a_client_deleted_after_the_backup_comes_back_on_every_pc(cluster):
    h = cluster(2)
    a, b = h.nodes
    _add_client(a, "D-1", "comes back")
    h.run_until_quiet(timeout=20.0)
    backup = _backup(a)
    old_gid = _gid(a, "D-1")
    b.write(lambda conn: conn.execute("DELETE FROM clients WHERE client_id_token = 'D-1'"))
    h.run_until_quiet(timeout=20.0)
    assert not _has(a, "D-1")

    assert sync_restore.plan_restore(a.db, backup).clients_back == 1
    _restore(h, a, backup)
    h.run_until_quiet(timeout=20.0)
    for node in (a, b):
        assert _has(node, "D-1") and _name(node, "D-1") == "comes back"
    # Every PC drops an upsert for a tombstoned gid, so it came back under a new one.
    assert _gid(a, "D-1") != old_gid and _gid(a, "D-1") == _gid(b, "D-1")
    assert h.digest(a) == h.digest(b)


def test_restore_change_set_is_signed_and_the_own_stream_has_no_gap(cluster):
    h = cluster(2)
    a, b = h.nodes
    _add_client(a, "S-1", "one")
    h.run_until_quiet(timeout=20.0)
    backup = _backup(a)
    _add_client(a, "S-2", "two")
    stream, _vec, before_next, _ = _own_stream_state(a)

    _restore(h, a, backup)
    stream, vec, nxt, seqs = _own_stream_state(a)
    assert vec == nxt - 1 and nxt > before_next
    assert seqs == list(range(seqs[0], vec + 1))
    assert sync_capture.read_seq_mark(sync_capture.seq_state_path(str(a.db_path)), stream) == vec
    with a.open_db() as conn:
        rows = conn.execute("SELECT origin, origin_seq, hlc, tbl, row_key, op, data, sig FROM _sync_changes "
                            "WHERE origin = ? AND origin_seq >= ?", (stream, before_next)).fetchall()
    assert rows
    for r in rows:
        ch = dict(zip(("origin", "origin_seq", "hlc", "tbl", "row_key", "op", "data", "sig"), r))
        assert sync_capture.verify_change(ch, a.office.admin_pubkey), ch["tbl"]
    h.run_until_quiet(timeout=20.0)
    assert h.digest(a) == h.digest(b)


def test_the_restored_databases_keep_this_pcs_sync_state_and_the_old_ones_are_kept(cluster):
    h = cluster(2)
    a, b = h.nodes
    _add_client(a, "K-1", "one")
    h.run_until_quiet(timeout=20.0)
    backup = _backup(a)
    with a.open_db() as conn:
        members_before = conn.execute("SELECT count(*) FROM _sync_members").fetchone()[0]
        addresses_before = conn.execute("SELECT count(*) FROM _local_addresses").fetchone()[0]
    info = _restore(h, a, backup)
    with a.open_db() as conn:
        assert conn.execute("SELECT count(*) FROM _sync_members").fetchone()[0] == members_before
        assert conn.execute("SELECT count(*) FROM _local_addresses").fetchone()[0] >= addresses_before
        assert sync_capture._meta(conn, "mode") == "live"
        assert sync_capture._meta(conn, "device_id") == a.device_id
    replaced = Path(info["replaced_dir"])
    assert (replaced / "master.db").exists() and (replaced / "rawPayload.db").exists()
    assert any(p.name.startswith("pre-restore-") for p in (a.app_dir / "backups").iterdir())


# ---------------------------------------------------------------- refusals and failures

def test_only_the_admin_pc_may_restore(cluster):
    h = cluster(2)
    a, b = h.nodes
    backup = _backup(b)
    with pytest.raises(sync_restore.RestoreRefused, match="admin PC"):
        sync_restore.plan_restore(b.db, backup)


def test_restore_needs_mode_live(cluster):
    h = cluster(2)
    a, _b = h.nodes
    backup = _backup(a)
    a.db.set_sync_mode("off")
    with pytest.raises(sync_restore.RestoreRefused, match="live"):
        sync_restore.plan_restore(a.db, backup)


def test_a_backup_without_the_tracker_database_is_refused(cluster, tmp_path):
    h = cluster(2)
    a, _b = h.nodes
    backup = _backup(a)
    partial = tmp_path / "partial"
    partial.mkdir()
    shutil.copy2(backup / "master.db", partial / "master.db")
    with pytest.raises(sync_restore.RestoreRefused, match="rawPayload.db"):
        sync_restore.plan_restore(a.db, partial)


def test_a_backup_with_another_key_is_refused(cluster, tmp_path):
    import sqlcipher3.dbapi2 as sqlite3
    h = cluster(2)
    a, _b = h.nodes
    other = tmp_path / "other"
    other.mkdir()
    for name in ("master.db", "rawPayload.db"):
        conn = sqlite3.connect(str(other / name))
        conn.execute("PRAGMA key = \"x'" + "ab" * 32 + "'\";")
        conn.execute("CREATE TABLE t (x)")
        conn.commit()
        conn.close()
    with pytest.raises(sync_restore.RestoreRefused, match="key"):
        sync_restore.plan_restore(a.db, other)


def test_a_failed_install_changes_nothing(cluster):
    h = cluster(2)
    a, _b = h.nodes
    _add_client(a, "F-1", "one")
    backup = _backup(a)
    _add_client(a, "F-2", "two")
    sync_restore.stage_restore(a.db, backup, actor="Tester")
    staged = sync_restore._stage_dir(a.app_dir) / "new" / "master.db"
    staged.write_bytes(staged.read_bytes()[:-10] + b"0123456789")
    before = h.digest(a)

    def install(node):
        with pytest.raises(sync_restore.RestoreError):
            sync_restore.apply_pending_restore(node.app_dir, node.hex_key)
    h.restart_node(a, before_open=install)
    assert h.digest(a) == before and _has(a, "F-2")
    assert not sync_restore.has_pending_restore(a.app_dir)


def test_confirmation_text_names_the_counts_and_the_known_limit():
    plan = sync_restore.RestorePlan("x", "daily-2026-09-20", "2026-09-20 13:05", 2, 3, 4)
    text = plan.confirmation_text()
    assert "2 client(s) will come back" in text and "3 client(s) created since 2026-09-20 13:05" in text
    assert "4 field(s) will revert" in text and "offline" in text
    assert json.dumps(sync_restore.CONFIRM_WORD) == '"RESTORE"'
