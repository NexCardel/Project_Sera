"""Tests for Sera Sync v3 P3-9: go live.

Blueprint §5 P3-9:
  - On each PC, at the next start-up, a staged swap (the P0-4 mechanism) installs the shadow
    replicas (which already contain everyone's merged changes) as the live DBs. Set mode=live.
    From then on, remote changes apply to the live DBs.
  - Disable legacy 49157 pushes in this release (the P4-1 removal follows).

§5 gives no "Accept:" list for P3-9, so these tests follow the two sentences above plus the
items earlier WPs handed to P3-9 (§9 P3-7, P3-7b review #4 and #8) and the reviewed problems of
an earlier, unmerged P3-9 attempt (docs/sera-sync-v3-open-problems.txt C3, S2, S3, S4, S6).

Nodes are real harness nodes on 127.0.0.1 (P2-4 pairing, P2-3 mutual TLS). tmp_path only
(§0 rule 2); invented data only (§0 rule 12).
"""

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")

import sync_capture  # noqa: E402
import sync_office  # noqa: E402
import sync_shadow  # noqa: E402
import sync_snapshot  # noqa: E402
from tests.sync_harness import SyncHarness  # noqa: E402


# ---------------------------------------------------------------- helpers

@pytest.fixture
def cluster(tmp_path):
    made = []

    def make(n=3):
        h = SyncHarness(num_nodes=n, base_dir=tmp_path / f"h{len(made)}")
        made.append(h)
        for node in h.nodes:
            node.db.set_sync_mode("off")      # production PCs start in "off" (P3-7b)
        return h
    yield make
    for h in made:
        for s in getattr(h, "_extra_servers", []):
            try:
                s.stop()
            except Exception:
                pass
        h.close()


def _pk_column(node) -> int:
    with node.open_db() as conn:
        return conn.execute("SELECT id FROM mcl_columns WHERE is_internal_pk = 1").fetchone()[0]


def _add_client(node, pan: str, notes: str = "Note") -> int:
    return node.db.add_client({_pk_column(node): pan}, notes, [])


def _pans(conn) -> set:
    pk = conn.execute("SELECT id FROM mcl_columns WHERE is_internal_pk = 1").fetchone()[0]
    return {r[0] for r in conn.execute("SELECT value FROM client_values WHERE column_id = ?", (pk,))}


def _live_pans(node) -> set:
    with node.open_db() as conn:
        return _pans(conn)


def _replica_pans(node) -> set:
    conn = sync_capture._open(str(sync_shadow.replica_paths(node.app_dir)[0]), node.hex_key, 5.0)
    try:
        return _pans(conn)
    finally:
        conn.close()


def _wire(node):
    """main.py's wiring: replica apply (used only in mode shadow) + the seal listener."""
    node.engine.db = node.db
    node.engine.shadow_apply = sync_shadow.make_shadow_apply(node.db, node.app_dir)

    def _listener(results, _node=node):
        sync_shadow.mirror_own_changes_to_replica(_node.db, _node.app_dir)
        _node.engine.notify_local_change()
    node.db.set_seal_listener(_listener)


def _serve_dispatch(h, node):
    server = node.transport.serve(
        lambda s: sync_office.dispatch_session(s, node.app_dir, node.engine),
        host="127.0.0.1", port=0)
    h.__dict__.setdefault("_extra_servers", []).append(server)
    return server


def _start_non_admin(node, admin, server):
    session = node.transport.connect("127.0.0.1", server.address[1], admin.device_id)
    try:
        plan = sync_shadow.request_shadow_start(node.db, node.app_dir, session)
    finally:
        session.close()
    sync_shadow.stage_shadow_start(node.app_dir, plan)
    node.db.set_seal_listener(None)
    node._db = None
    assert sync_shadow.apply_pending_shadow_start(node.app_dir) is not None
    sync_shadow.skip_shadow_salvage(node.app_dir)      # the start-up salvage offer, answered
    _wire(node)


def _shadow_cluster(cluster, n=3):
    """n nodes in shadow mode by P3-7b method 1a (admin first, the others from its replica)."""
    h = cluster(n)
    admin = h.nodes[0]
    _add_client(admin, "AAAPA0001A")
    sync_shadow.start_shadow_mode(admin.db, admin.app_dir)
    _wire(admin)
    server = _serve_dispatch(h, admin)
    for node in h.nodes[1:]:
        _start_non_admin(node, admin, server)
    return h


def _restart(node):
    """What a restart does: nothing holds the DB open, the staged go-live is installed before
    the DB is opened again, then the app re-wires the engine."""
    node.db.set_seal_listener(None)
    node._db = None
    outcome = sync_shadow.apply_pending_go_live(node.app_dir)
    _wire(node)
    return outcome


def _go_live(node):
    sync_shadow.stage_go_live(node.db, node.app_dir)
    outcome = _restart(node)
    assert outcome is not None
    return outcome


def _own_seqs(node, which="master") -> list:
    origin = f"{node.device_id}:{'m' if which == 'master' else 'r'}"
    opener = node.open_db if which == "master" else node.open_raw_db
    with opener() as conn:
        return [r[0] for r in conn.execute(
            "SELECT origin_seq FROM _sync_changes WHERE origin = ? ORDER BY origin_seq", (origin,))]


def _live_file_digest(node) -> str:
    """The live DBs' replicated data, read from the files (the mode may change, the data not)."""
    return sync_shadow.digest_of_files(Path(node.app_dir) / "master.db", node.hex_key,
                                       Path(node.app_dir) / "rawPayload.db")


def _file_mode(path, hex_key) -> str:
    conn = sync_capture._open(str(path), hex_key, 5.0)
    try:
        return sync_capture._meta(conn, "mode")
    finally:
        conn.close()


# ---------------------------------------------------------------- the go-live swap itself

def test_three_nodes_go_live_and_then_remote_changes_apply_to_the_live_dbs(cluster):
    h = _shadow_cluster(cluster, 3)
    admin, b, c = h.nodes
    _add_client(admin, "AAAPA0002A")
    _add_client(b, "AAAPB0001B")
    _add_client(c, "AAAPC0001C")
    h.run_until_quiet(timeout=15.0)
    replica_digests = {n.name: sync_shadow.digest_of_replica(n.app_dir, n.hex_key) for n in h.nodes}
    assert len(set(replica_digests.values())) == 1
    # Shadow mode: other PCs' clients are in the replica, not the live DB.
    assert "AAAPB0001B" not in _live_pans(admin)

    for node in h.nodes:
        _go_live(node)
        assert node.db.get_sync_mode() == "live"
        # The live DBs are now the converged replicas.
        assert sync_shadow.digest_of_live(node.db) == replica_digests[node.name]
        assert _live_pans(node) >= {"AAAPA0001A", "AAAPA0002A", "AAAPB0001B", "AAAPC0001C"}

    # From now on remote changes apply to the live DBs.
    _add_client(admin, "AAAPA0003A")
    _add_client(b, "AAAPB0002B")
    _add_client(c, "AAAPC0002C")
    h.run_until_quiet(timeout=15.0)
    live_digests = {sync_shadow.digest_of_live(n.db) for n in h.nodes}
    assert len(live_digests) == 1
    for node in h.nodes:
        assert _live_pans(node) >= {"AAAPA0003A", "AAAPB0002B", "AAAPC0002C"}
        for which in ("master", "raw"):
            seqs = _own_seqs(node, which)
            assert seqs == list(range(1, len(seqs) + 1)), (node.name, which)


def test_one_pc_live_while_the_others_are_still_in_shadow_mode(cluster):
    """Go-live is per PC: a live PC and shadow PCs keep exchanging changes."""
    h = _shadow_cluster(cluster, 2)
    admin, b = h.nodes
    _go_live(admin)
    _add_client(b, "AAAPB0005B")
    _add_client(admin, "AAAPA0005A")
    h.run_until_quiet(timeout=15.0)
    assert "AAAPB0005B" in _live_pans(admin)            # applied to admin's live DB
    assert "AAAPA0005A" in _replica_pans(b)             # b still applies to its replica
    assert "AAAPA0005A" not in _live_pans(b)
    assert sync_shadow.digest_of_live(admin.db) == sync_shadow.digest_of_replica(b.app_dir, b.hex_key)
    assert sync_shadow.capture_check(b.db, b.app_dir).ok


def test_staged_files_are_mode_live_and_verified(cluster):
    h = _shadow_cluster(cluster, 2)
    admin = h.nodes[0]
    pending = sync_shadow.stage_go_live(admin.db, admin.app_dir)
    info = json.loads(Path(pending).read_text(encoding="utf-8"))
    names = sorted(f["name"] for f in info["files"])
    assert names == ["master.db", "rawPayload.db"]
    staged_dir = Path(pending).parent / "new"
    for f in info["files"]:
        assert _file_mode(staged_dir / f["name"], admin.hex_key) == "live"
        assert len(f["sha256"]) == 64
    # Staging switched the running PC to mode off (nothing captured until the restart).
    assert admin.db.get_sync_mode() == "off"
    # A backup was made first (P4-3a backup_before_golive).
    assert list((Path(admin.app_dir) / "backups").glob("pre-golive-*"))


def test_old_live_dbs_and_shadow_files_are_kept_not_deleted(cluster):
    h = _shadow_cluster(cluster, 2)
    admin = h.nodes[0]
    outcome = _go_live(admin)
    pre = Path(outcome["pre_golive_dir"])
    assert pre.parent == Path(admin.app_dir) / sync_shadow.SHADOW_DIRNAME
    assert (pre / "master.db").exists() and (pre / "rawPayload.db").exists()
    assert (pre / sync_shadow.REPLICA_MASTER_NAME).exists()
    assert (pre / sync_shadow.BASELINE_MASTER_NAME).exists()
    assert not sync_shadow.replica_paths(admin.app_dir)[0].exists()
    st = sync_shadow.shadow_status(admin.db, admin.app_dir)
    assert st["mode"] == "live" and st["went_live_at"] and not st["pending_golive"]


def test_shadow_mode_can_not_be_turned_on_again_after_go_live(cluster):
    h = _shadow_cluster(cluster, 2)
    admin = h.nodes[0]
    _go_live(admin)
    with pytest.raises(sync_shadow.ShadowStartRefused):
        sync_shadow.enable_shadow_mode(admin.db, admin.app_dir)
    admin.db.set_sync_mode("off")                         # even through mode off
    with pytest.raises(sync_shadow.ShadowStartRefused):
        sync_shadow.enable_shadow_mode(admin.db, admin.app_dir)
    with pytest.raises(sync_shadow.ShadowStartRefused):
        sync_shadow.start_shadow_mode(admin.db, admin.app_dir)


# ---------------------------------------------------------------- staging refusals and failures

def test_stage_refused_outside_shadow_mode(cluster):
    h = cluster(1)
    node = h.nodes[0]
    with pytest.raises(sync_shadow.GoLiveRefused, match="shadow"):
        sync_shadow.stage_go_live(node.db, node.app_dir)
    assert not sync_shadow.has_pending_go_live(node.app_dir)


def test_stage_refused_without_a_replica(cluster):
    h = _shadow_cluster(cluster, 2)
    b = h.nodes[1]
    replica_master, _ = sync_shadow.replica_paths(b.app_dir)
    replica_master.rename(replica_master.with_name("moved-by-hand.db"))
    with pytest.raises(sync_shadow.GoLiveRefused, match="replica"):
        sync_shadow.stage_go_live(b.db, b.app_dir)
    assert b.db.get_sync_mode() == "shadow"


def test_stage_refused_twice(cluster):
    h = _shadow_cluster(cluster, 2)
    admin = h.nodes[0]
    sync_shadow.stage_go_live(admin.db, admin.app_dir)
    admin.db.set_sync_mode("shadow")      # as if someone flipped it back by hand
    with pytest.raises(sync_shadow.GoLiveRefused, match="restart"):
        sync_shadow.stage_go_live(admin.db, admin.app_dir)


def test_reset_refused_while_go_live_is_staged(cluster):
    h = _shadow_cluster(cluster, 2)
    b = h.nodes[1]
    sync_shadow.stage_go_live(b.db, b.app_dir)
    b.db.set_sync_mode("shadow")
    with pytest.raises(sync_shadow.ShadowStartRefused, match="restart"):
        sync_shadow.reset_shadow_mode(b.db, b.app_dir)


def test_failed_staging_restores_shadow_mode_and_stages_nothing(cluster, monkeypatch):
    h = _shadow_cluster(cluster, 2)
    b = h.nodes[1]
    import sync_backup

    def boom(*a, **k):
        raise OSError("disk full (simulated)")
    monkeypatch.setattr(sync_backup, "export_database", boom)
    with pytest.raises(sync_shadow.GoLiveError, match="disk full"):
        sync_shadow.stage_go_live(b.db, b.app_dir)
    assert b.db.get_sync_mode() == "shadow"
    assert not sync_shadow.has_pending_go_live(b.app_dir)
    assert not (Path(b.app_dir) / "incoming" / sync_shadow.GOLIVE_DIRNAME).exists()
    # Still a working shadow PC: an edit is captured and mirrored.
    monkeypatch.undo()
    _add_client(b, "AAAPB0077B")
    assert "AAAPB0077B" in _replica_pans(b)
    assert sync_shadow.capture_check(b.db, b.app_dir).ok


def test_own_changes_not_yet_mirrored_are_mirrored_before_the_export(cluster):
    """Staging seals what is pending and mirrors it, so the last edits are in the new live DB."""
    h = _shadow_cluster(cluster, 2)
    b = h.nodes[1]
    b.db.set_seal_listener(None)          # the mirror didn't run for this edit
    _add_client(b, "AAAPB0042B")
    assert "AAAPB0042B" not in _replica_pans(b)
    _go_live(b)
    assert "AAAPB0042B" in _live_pans(b)
    seqs = _own_seqs(b)
    assert seqs == list(range(1, len(seqs) + 1))


def test_write_after_staging_is_not_captured_and_leaves_no_gap(cluster):
    """open-problems C3: after staging, a background write must not take a sequence number
    that the new live DB doesn't have. It isn't captured (mode off) and stays in the old DB."""
    h = _shadow_cluster(cluster, 2)
    admin, b = h.nodes
    sync_shadow.stage_go_live(b.db, b.app_dir)
    _add_client(b, "AAAPB0099B")          # e.g. the extension bridge, before the process exits
    before = _own_seqs(b)
    outcome = _restart(b)
    assert "AAAPB0099B" not in _live_pans(b)
    old = sync_capture._open(str(Path(outcome["pre_golive_dir"]) / "master.db"), b.hex_key, 5.0)
    try:
        assert "AAAPB0099B" in _pans(old)
    finally:
        old.close()
    assert _own_seqs(b) == before
    _add_client(b, "AAAPB0100B")          # the next own edit continues without a gap
    seqs = _own_seqs(b)
    assert seqs == list(range(1, len(seqs) + 1))
    h.run_until_quiet(timeout=15.0)
    assert "AAAPB0100B" in _replica_pans(admin)


def test_shadow_apply_refuses_once_staging_switched_mode_off(cluster):
    h = _shadow_cluster(cluster, 2)
    b = h.nodes[1]
    apply = sync_shadow.make_shadow_apply(b.db, b.app_dir)
    sync_shadow.stage_go_live(b.db, b.app_dir)
    with pytest.raises(RuntimeError, match="shadow"):
        apply("master", [])


# ---------------------------------------------------------------- per-PC state carried over

def test_local_tables_are_carried_over_with_client_ids_translated_by_gid(cluster):
    h = _shadow_cluster(cluster, 2)
    admin, b = h.nodes
    # Make the replica's local ids differ from the live DB's: a remote client lands in the
    # admin's replica first, then an own client gets id N+1 live but N+2 in the replica.
    _add_client(b, "AAAPB0003B")
    h.run_until_quiet(timeout=10.0)
    own_id = _add_client(admin, "AAAPA0009A")
    with admin.open_db() as conn:
        gid = conn.execute("SELECT gid FROM clients WHERE id = ?", (own_id,)).fetchone()[0]
        conn.execute("INSERT OR REPLACE INTO client_activity_stats(client_id, view_count, action_count) "
                     "VALUES (?, 7, 3)", (own_id,))
        conn.execute("INSERT INTO client_recent_activity(client_id, action_type, detail, timestamp) "
                     "VALUES (?, 'VIEW', 'opened', 1.0)", (own_id,))
    with admin.open_raw_db() as conn:
        conn.execute("INSERT OR REPLACE INTO client_raw_containers(identity_key, client_id, company_name, "
                     "notes, last_updated) VALUES ('PAN:AAAPA0009A', ?, 'Invented Traders', 'staff note', "
                     "'2026-09-26T10:00:00')", (own_id,))
    with admin.open_db() as conn:
        stats_before = conn.execute("SELECT COUNT(*) FROM client_activity_stats").fetchone()[0]
        recent_before = conn.execute("SELECT COUNT(*) FROM client_recent_activity").fetchone()[0]
    replica_master, _ = sync_shadow.replica_paths(admin.app_dir)
    conn = sync_capture._open(str(replica_master), admin.hex_key, 5.0)
    try:
        replica_id = conn.execute("SELECT id FROM clients WHERE gid = ?", (gid,)).fetchone()[0]
    finally:
        conn.close()
    assert replica_id != own_id               # the scenario this test is about

    _go_live(admin)
    with admin.open_db() as conn:
        new_id = conn.execute("SELECT id FROM clients WHERE gid = ?", (gid,)).fetchone()[0]
        assert new_id == replica_id
        assert conn.execute("SELECT view_count, action_count FROM client_activity_stats "
                            "WHERE client_id = ?", (new_id,)).fetchone() == (7, 3)
        # Every row came over (the first client's too), each under its client's new id.
        assert conn.execute("SELECT COUNT(*) FROM client_activity_stats").fetchone()[0] == stats_before
        assert conn.execute("SELECT COUNT(*) FROM client_activity_stats s JOIN clients c "
                            "ON c.id = s.client_id").fetchone()[0] == stats_before
        assert conn.execute("SELECT client_id FROM client_recent_activity "
                            "WHERE detail = 'opened'").fetchall() == [(new_id,)]
        assert conn.execute("SELECT COUNT(*) FROM client_recent_activity").fetchone()[0] == recent_before
    with admin.open_raw_db() as conn:
        row = conn.execute("SELECT client_id, notes FROM client_raw_containers "
                           "WHERE identity_key = 'PAN:AAAPA0009A'").fetchone()
        assert row == (new_id, "staff note")


def test_registered_client_container_key_follows_the_client_id(cluster):
    """P3-9 review #1: a registered client's container is keyed "CLI-<local id>". When the
    client's id differs between the live DB and the replica, the key must follow the new id,
    or the notes end up on whichever client has the old id in the new DB."""
    h = _shadow_cluster(cluster, 2)
    admin, b = h.nodes
    _add_client(b, "AAAPB0004B")              # lands in admin's replica first
    h.run_until_quiet(timeout=10.0)
    own_id = _add_client(admin, "AAAPA0010A")  # id N+1 live, N+2 in the replica
    with admin.open_db() as conn:
        gid = conn.execute("SELECT gid FROM clients WHERE id = ?", (own_id,)).fetchone()[0]
    with admin.open_raw_db() as conn:
        conn.execute("DELETE FROM client_raw_containers")
        conn.execute("INSERT INTO client_raw_containers(identity_key, client_id, notes, last_updated) "
                     "VALUES (?, ?, 'staff note', '2026-09-26T10:00:00')", (f"CLI-{own_id:05d}", own_id))
        # A container of a client the new DB doesn't have (id 999 exists nowhere).
        conn.execute("INSERT INTO client_raw_containers(identity_key, client_id, notes, last_updated) "
                     "VALUES ('CLI-00999', 999, 'orphan note', '2026-09-26T10:00:00')")
        conn.execute("INSERT INTO client_raw_containers(identity_key, client_id, notes, last_updated) "
                     "VALUES ('PAN:AAAPZ9999Z', NULL, 'unassigned note', '2026-09-26T10:00:00')")

    outcome = _go_live(admin)
    with admin.open_db() as conn:
        new_id = conn.execute("SELECT id FROM clients WHERE gid = ?", (gid,)).fetchone()[0]
    assert new_id != own_id                   # the scenario this test is about
    with admin.open_raw_db() as conn:
        rows = dict((k, (cid, n)) for k, cid, n in conn.execute(
            "SELECT identity_key, client_id, notes FROM client_raw_containers"))
    assert rows == {f"CLI-{new_id:05d}": (new_id, "staff note"),
                    "PAN:AAAPZ9999Z": (None, "unassigned note")}
    # The dropped orphan is still in the kept old database.
    old = sync_capture._open(str(Path(outcome["pre_golive_dir"]) / "rawPayload.db"), admin.hex_key, 5.0)
    try:
        assert old.execute("SELECT notes FROM client_raw_containers WHERE identity_key = 'CLI-00999'"
                           ).fetchone() == ("orphan note",)
    finally:
        old.close()


def test_address_book_and_sequence_state_are_kept(cluster):
    h = _shadow_cluster(cluster, 2)
    admin, b = h.nodes
    h.run_until_quiet(timeout=10.0)
    with b.open_db() as conn:
        addresses = conn.execute("SELECT COUNT(*) FROM _local_addresses").fetchone()[0]
        peer_vectors = conn.execute("SELECT COUNT(*) FROM _sync_peer_vectors").fetchone()[0]
    _go_live(b)
    with b.open_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM _local_addresses").fetchone()[0] == addresses
        assert conn.execute("SELECT COUNT(*) FROM _sync_peer_vectors").fetchone()[0] == peer_vectors
        assert conn.execute("SELECT COUNT(*) FROM _sync_pending").fetchone()[0] == 0


# ---------------------------------------------------------------- install failures

def test_tampered_staged_file_is_refused_and_the_pc_stays_in_shadow_mode(cluster):
    h = _shadow_cluster(cluster, 2)
    b = h.nodes[1]
    pending = sync_shadow.stage_go_live(b.db, b.app_dir)
    staged = Path(pending).parent / "new" / "master.db"
    with open(staged, "ab") as f:
        f.write(b"\0" * 16)
    live_before = _live_file_digest(b)
    b.db.set_seal_listener(None)
    b._db = None
    with pytest.raises(sync_shadow.GoLiveError):
        sync_shadow.apply_pending_go_live(b.app_dir)
    assert _live_file_digest(b) == live_before
    assert not sync_shadow.has_pending_go_live(b.app_dir)
    assert (Path(b.app_dir) / "incoming" / f"{sync_shadow.GOLIVE_DIRNAME}.pending.json.failed").exists()
    _wire(b)
    # Put back to shadow mode, so it keeps working as a shadow PC and can try again.
    assert b.db.get_sync_mode() == "shadow"
    assert sync_shadow.replica_paths(b.app_dir)[0].exists()


def test_nothing_staged_returns_none(tmp_path):
    assert sync_shadow.apply_pending_go_live(tmp_path) is None


def test_install_waits_while_the_old_process_still_holds_a_file(cluster, monkeypatch):
    """P3-9 review #2: the new process starts before the old one has closed the DBs; a rename
    that hits a sharing violation is retried instead of failing the go-live."""
    h = _shadow_cluster(cluster, 2)
    b = h.nodes[1]
    sync_shadow.stage_go_live(b.db, b.app_dir)
    b.db.set_seal_listener(None)
    b._db = None
    import sync_shadow as ss
    real_replace = ss.os.replace
    locked = {"n": 0}

    def locked_for_a_moment(src, dst):
        if str(src).endswith("master.db") and locked["n"] < 3:
            locked["n"] += 1
            raise PermissionError(32, "The process cannot access the file")
        return real_replace(src, dst)
    monkeypatch.setattr(ss.os, "replace", locked_for_a_moment)
    assert sync_shadow.apply_pending_go_live(b.app_dir) is not None
    monkeypatch.setattr(ss.os, "replace", real_replace)
    assert locked["n"] == 3
    _wire(b)
    assert b.db.get_sync_mode() == "live"


def test_install_still_locked_after_the_wait_is_put_back(cluster, monkeypatch):
    h = _shadow_cluster(cluster, 2)
    b = h.nodes[1]
    sync_shadow.stage_go_live(b.db, b.app_dir)
    b.db.set_seal_listener(None)
    b._db = None
    before = _live_file_digest(b)
    import sync_shadow as ss
    real_replace = ss.os.replace

    def always_locked(src, dst):
        if str(src).endswith("rawPayload.db") and "pre-golive" in str(dst):
            raise PermissionError(32, "The process cannot access the file")
        return real_replace(src, dst)
    monkeypatch.setattr(ss, "GOLIVE_LOCK_WAIT_SECONDS", 0.5)
    monkeypatch.setattr(ss.os, "replace", always_locked)
    with pytest.raises(sync_shadow.GoLiveError, match="put back"):
        sync_shadow.apply_pending_go_live(b.app_dir)
    monkeypatch.setattr(ss.os, "replace", real_replace)
    assert _live_file_digest(b) == before
    _wire(b)
    assert b.db.get_sync_mode() == "shadow"


@pytest.mark.skipif(sys.platform != "win32", reason="waiting on a process handle is Windows-only")
def test_install_waits_for_the_process_that_staged_it_to_exit(cluster):
    """The staging process's id is recorded; the install at the next start waits for that
    process to exit before moving anything, instead of racing its shutdown."""
    import subprocess
    h = _shadow_cluster(cluster, 2)
    b = h.nodes[1]
    sync_shadow.stage_go_live(b.db, b.app_dir)
    b.db.set_seal_listener(None)
    b._db = None
    pending_path = Path(b.app_dir) / "incoming" / "golive" / "pending.json"
    pending = json.loads(pending_path.read_text(encoding="utf-8"))
    assert pending["pid"] == os.getpid()
    old = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"])
    try:
        pending["pid"] = old.pid                  # stands in for the old Sera process
        pending_path.write_text(json.dumps(pending), encoding="utf-8")
        assert sync_shadow.apply_pending_go_live(b.app_dir) is not None
        assert old.poll() is not None             # it had exited before the install finished
    finally:
        old.kill()
        old.wait()
    _wire(b)
    assert b.db.get_sync_mode() == "live"


@pytest.mark.skipif(sys.platform != "win32", reason="waiting on a process handle is Windows-only")
def test_wait_for_process_exit_times_out_and_ignores_its_own_process():
    import subprocess
    assert sync_shadow._wait_for_process_exit(os.getpid(), 0.1) is True
    assert sync_shadow._wait_for_process_exit(None, 0.1) is True
    running = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert sync_shadow._wait_for_process_exit(running.pid, 0.2) is False
    finally:
        running.kill()
        running.wait()
    assert sync_shadow._wait_for_process_exit(running.pid, 0.2) is True


def test_crash_after_the_files_moved_is_finished_at_the_next_start(cluster, monkeypatch):
    h = _shadow_cluster(cluster, 2)
    b = h.nodes[1]
    sync_shadow.stage_go_live(b.db, b.app_dir)
    b.db.set_seal_listener(None)
    b._db = None
    import sync_shadow as ss
    real = ss._finish_go_live
    monkeypatch.setattr(ss, "_finish_go_live", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        sync_shadow.apply_pending_go_live(b.app_dir, _undo_on_error=False)
    monkeypatch.setattr(ss, "_finish_go_live", real)
    outcome = sync_shadow.apply_pending_go_live(b.app_dir)
    assert outcome is not None
    _wire(b)
    assert b.db.get_sync_mode() == "live"
    assert not sync_shadow.has_pending_go_live(b.app_dir)


def test_crash_halfway_through_the_moves_is_put_back_at_the_next_start(cluster, monkeypatch):
    h = _shadow_cluster(cluster, 2)
    b = h.nodes[1]
    sync_shadow.stage_go_live(b.db, b.app_dir)
    b.db.set_seal_listener(None)
    b._db = None
    live_before = _live_file_digest(b)
    import os as _os
    import sync_shadow as ss
    real_replace = _os.replace
    calls = {"n": 0}

    def crashing_replace(src, dst):
        calls["n"] += 1
        if calls["n"] == 3:
            raise KeyboardInterrupt()
        return real_replace(src, dst)
    monkeypatch.setattr(ss.os, "replace", crashing_replace)
    with pytest.raises(KeyboardInterrupt):
        sync_shadow.apply_pending_go_live(b.app_dir, _undo_on_error=False)
    monkeypatch.setattr(ss.os, "replace", real_replace)
    with pytest.raises(sync_shadow.GoLiveError, match="put back"):
        sync_shadow.apply_pending_go_live(b.app_dir)
    assert _live_file_digest(b) == live_before
    assert sync_shadow.replica_paths(b.app_dir)[0].exists()
    _wire(b)
    assert b.db.get_sync_mode() == "shadow"


# ---------------------------------------------------------------- snapshots follow the office mode

def test_add_workstation_snapshot_follows_the_live_mode(cluster, tmp_path):
    """P3-7b review #4: after go-live a PC added through "Add workstation" must start live."""
    h = _shadow_cluster(cluster, 2)
    admin, b = h.nodes
    out = tmp_path / "snap_shadow"
    sync_snapshot.make_office_snapshot(b.app_dir, out)
    assert _file_mode(out / "master.db", b.hex_key) == "off"      # shadow week: off (P3-7b)
    _go_live(admin)
    out = tmp_path / "snap_live"
    sync_snapshot.make_office_snapshot(admin.app_dir, out)
    assert _file_mode(out / "master.db", admin.hex_key) == "live"
    assert _file_mode(out / "rawPayload.db", admin.hex_key) == "live"


# ---------------------------------------------------------------- legacy 49157 pushes disabled
#
# P4-1 removed the legacy v2 whole-database push/pull protocol these tests exercised
# (push_to, push_to_all, request_pull_from, push_tracker_dumps_to_host,
# broadcast_tracker_dumps, _legacy_db_sync_blocked, and the inbound push_database /
# request_database_pull / push_tracker_dump actions) now that Sera Sync v3 is live on
# every PC. Deleted rather than weakened (blueprint §0 rule 4): the behaviour under test
# (legacy sync refusing to run once v3 is active) no longer applies because the legacy
# sync methods themselves are gone. The TCP server now serves only fetch_snapshot,
# covered by tests/test_sync_hotfix.py's join-flow tests.
