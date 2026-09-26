"""Tests for Sera Sync v3 P3-7b: turning shadow mode on (sync_shadow start / reset).

Owner decisions (2026-09-26): start point **1a** -- the admin PC turns shadow on from its own
live DBs; every other PC downloads the admin PC's *replica* and, after a restart, uses it as its
live DB, replica and baseline. Edits only on that PC are counted first (salvage-style dry run)
and can then be imported with the P2-8 salvage code, so they replicate as normal changes.
The non-own-origin guard applies only where a replica is built from the live DBs (the admin
path); office snapshots are exported with mode ``off``.

Accept (blueprint §5 P3-7b):
  - harness: 3 nodes turn shadow on by the chosen method, edit on each, sync -> replica digests
    equal (``test_three_nodes_start_shadow_edit_sync_and_replicas_converge``);
  - mode set to ``off`` and then back on -> refused
    (``test_mode_off_then_on_again_is_refused_on_the_admin_pc``,
    ``test_mode_off_then_on_again_is_refused_on_a_non_admin_pc``);
  - an edit made on a non-admin PC before turning shadow on -> listed in the dialog's count,
    not in the replica (``test_edit_made_before_start_is_counted_and_not_in_the_replica``).

Nodes are real harness nodes on 127.0.0.1 (P2-4 pairing, P2-3 mutual TLS). tmp_path only
(§0 rule 2); invented data only (§0 rule 12).
"""

import ast
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")

import sync_capture  # noqa: E402
import sync_office  # noqa: E402
import sync_shadow  # noqa: E402
import sync_snapshot  # noqa: E402
from tests.sync_harness import SyncHarness  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- helpers

@pytest.fixture
def cluster(tmp_path):
    made = []

    def make(n=3):
        h = SyncHarness(num_nodes=n, base_dir=tmp_path / f"h{len(made)}")
        made.append(h)
        for node in h.nodes:
            # Production PCs are in mode "off" until shadow mode is turned on (the harness
            # starts its nodes in "live").
            node.db.set_sync_mode("off")
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


def _replica_pans(node) -> set:
    replica_master, _ = sync_shadow.replica_paths(node.app_dir)
    conn = sync_capture._open(str(replica_master), node.hex_key, 5.0)
    try:
        return _pans(conn)
    finally:
        conn.close()


def _live_pans(node) -> set:
    with node.open_db() as conn:
        return _pans(conn)


def _wire(node):
    """The main.py wiring for a PC in mode shadow: replica apply + mirror own seals."""
    node.engine.db = node.db
    node.engine.shadow_apply = sync_shadow.make_shadow_apply(node.db, node.app_dir)

    def _listener(results, _node=node):
        sync_shadow.mirror_own_changes_to_replica(_node.db, _node.app_dir)
        _node.engine.notify_local_change()
    node.db.set_seal_listener(_listener)


def _serve_dispatch(h, node):
    """A second server on *node* routed like main.py's permanent 49159 server."""
    server = node.transport.serve(
        lambda s: sync_office.dispatch_session(s, node.app_dir, node.engine),
        host="127.0.0.1", port=0)
    h.__dict__.setdefault("_extra_servers", []).append(server)
    return server


def _request(node, admin, server):
    session = node.transport.connect("127.0.0.1", server.address[1], admin.device_id)
    try:
        return sync_shadow.request_shadow_start(node.db, node.app_dir, session)
    finally:
        session.close()


def _restart(node):
    """What a restart does: nothing holds the DB open, the pending start is applied before the
    DB is opened again, then the app re-wires the engine."""
    node.db.set_seal_listener(None)
    node._db = None
    outcome = sync_shadow.apply_pending_shadow_start(node.app_dir)
    _wire(node)
    return outcome


def _start_non_admin(h, node, admin, server):
    plan = _request(node, admin, server)
    sync_shadow.stage_shadow_start(node.app_dir, plan)
    outcome = _restart(node)
    assert outcome is not None
    return plan, outcome


def _replica_digest(node) -> str:
    return sync_shadow.digest_of_replica(node.app_dir, node.hex_key)


# ---------------------------------------------------------------- Accept

def test_three_nodes_start_shadow_edit_sync_and_replicas_converge(cluster):
    h = cluster(3)
    admin, b, c = h.nodes
    _add_client(admin, "AAAPA0001A")                  # office data before shadow

    sync_shadow.start_shadow_mode(admin.db, admin.app_dir)
    _wire(admin)
    assert admin.db.get_sync_mode() == "shadow"
    _add_client(admin, "AAAPA0002A")                  # after the admin start, before the others
    server = _serve_dispatch(h, admin)

    for node in (b, c):
        _start_non_admin(h, node, admin, server)
        assert node.db.get_sync_mode() == "shadow"
        # All three copies start identical: live, replica and baseline.
        assert sync_shadow.digest_of_live(node.db) == _replica_digest(node)
        assert _replica_pans(node) >= {"AAAPA0001A", "AAAPA0002A"}
        assert sync_shadow.capture_check(node.db, node.app_dir).ok

    _add_client(admin, "AAAPA0003A")
    _add_client(b, "AAAPB0001B")
    _add_client(c, "AAAPC0001C")
    h.run_until_quiet(timeout=15.0)

    digests = {n.name: _replica_digest(n) for n in (admin, b, c)}
    assert len(set(digests.values())) == 1, digests
    expected = {"AAAPA0001A", "AAAPA0002A", "AAAPA0003A", "AAAPB0001B", "AAAPC0001C"}
    for n in (admin, b, c):
        assert _replica_pans(n) >= expected
        assert sync_shadow.capture_check(n.db, n.app_dir).ok, n.name
    # Remote changes never reach a live DB in shadow mode.
    assert "AAAPB0001B" not in _live_pans(admin)
    assert "AAAPA0003A" not in _live_pans(b)


def test_mode_off_then_on_again_is_refused_on_the_admin_pc(cluster):
    h = cluster(2)
    admin = h.nodes[0]
    sync_shadow.start_shadow_mode(admin.db, admin.app_dir)
    replica_master, _ = sync_shadow.replica_paths(admin.app_dir)
    before = replica_master.read_bytes()

    admin.db.set_sync_mode("off")
    with pytest.raises(sync_shadow.ShadowStartRefused):
        sync_shadow.start_shadow_mode(admin.db, admin.app_dir)
    with pytest.raises(sync_shadow.ShadowStartRefused):
        sync_shadow.enable_shadow_mode(admin.db, admin.app_dir)
    # Refused before touching anything.
    assert admin.db.get_sync_mode() == "off"
    assert replica_master.read_bytes() == before
    assert not list(replica_master.parent.glob("*.bak-*"))


def test_admin_start_refused_when_live_vector_has_a_remote_origin(cluster):
    """The replica is gone (moved by hand) but the live DB already counts remote changes as
    received: a replica built from it would never get them, so refuse."""
    h = cluster(2)
    admin, b = h.nodes
    sync_shadow.start_shadow_mode(admin.db, admin.app_dir)
    _wire(admin)
    server = _serve_dispatch(h, admin)
    _start_non_admin(h, b, admin, server)
    _add_client(b, "AAAPB0009B")
    h.run_until_quiet(timeout=10.0)

    admin.db.set_sync_mode("off")
    shadow_dir = Path(admin.app_dir) / sync_shadow.SHADOW_DIRNAME
    shadow_dir.rename(shadow_dir.with_name("shadow-moved-by-hand"))
    with pytest.raises(sync_shadow.ShadowStartRefused, match="other PCs"):
        sync_shadow.start_shadow_mode(admin.db, admin.app_dir)
    assert not shadow_dir.exists()


def test_mode_off_then_on_again_is_refused_on_a_non_admin_pc(cluster):
    h = cluster(2)
    admin, b = h.nodes
    sync_shadow.start_shadow_mode(admin.db, admin.app_dir)
    _wire(admin)
    server = _serve_dispatch(h, admin)
    _start_non_admin(h, b, admin, server)

    b.db.set_sync_mode("off")
    session = b.transport.connect("127.0.0.1", server.address[1], admin.device_id)
    try:
        with pytest.raises(sync_shadow.ShadowStartRefused):
            sync_shadow.request_shadow_start(b.db, b.app_dir, session)
    finally:
        session.close()
    with pytest.raises(sync_shadow.ShadowStartRefused):
        sync_shadow.enable_shadow_mode(b.db, b.app_dir)
    assert not sync_shadow.has_pending_shadow_start(b.app_dir)


def test_edit_made_before_start_is_counted_and_not_in_the_replica(cluster):
    h = cluster(2)
    admin, b = h.nodes
    _add_client(b, "AAAPB0100B")                      # mode off: never captured
    sync_shadow.start_shadow_mode(admin.db, admin.app_dir)
    _wire(admin)
    server = _serve_dispatch(h, admin)

    plan = _request(b, admin, server)
    assert [c.key for c in plan.report.to_insert] == ["AAAPB0100B"]
    assert plan.report.dry_run is True
    # The dry run wrote nothing: still mode off, no replica, nothing staged yet.
    assert b.db.get_sync_mode() == "off"
    assert not sync_shadow.replica_paths(b.app_dir)[0].exists()

    sync_shadow.stage_shadow_start(b.app_dir, plan)
    outcome = _restart(b)
    assert "AAAPB0100B" not in _replica_pans(b)
    assert "AAAPB0100B" not in _live_pans(b)
    # The old live DB is kept (§0 rule 3) and still holds it.
    old_dir = Path(outcome["pre_start_dir"])
    assert (old_dir / "master.db").exists()
    assert sync_shadow.pending_shadow_salvage(b.app_dir) is not None


# ---------------------------------------------------------------- salvage (owner decision)

def test_salvage_after_start_imports_local_edits_and_they_replicate(cluster):
    h = cluster(3)
    admin, b, c = h.nodes
    _add_client(b, "AAAPB0200B")
    sync_shadow.start_shadow_mode(admin.db, admin.app_dir)
    _wire(admin)
    server = _serve_dispatch(h, admin)
    _start_non_admin(h, b, admin, server)
    _start_non_admin(h, c, admin, server)

    dry = sync_shadow.plan_shadow_salvage(b.app_dir)
    assert [x.key for x in dry.to_insert] == ["AAAPB0200B"]
    done = sync_shadow.apply_shadow_salvage(b.app_dir)
    assert [x.key for x in done.to_insert] == ["AAAPB0200B"]
    assert done.report_path and Path(done.report_path).exists()
    assert sync_shadow.pending_shadow_salvage(b.app_dir) is None

    # Opening the DB and sealing turns the import into normal captured changes.
    b.db.seal_pending()
    assert "AAAPB0200B" in _live_pans(b)
    assert "AAAPB0200B" in _replica_pans(b)
    assert sync_shadow.capture_check(b.db, b.app_dir).ok
    h.run_until_quiet(timeout=15.0)
    for n in (admin, c):
        assert "AAAPB0200B" in _replica_pans(n), n.name
    assert len({_replica_digest(n) for n in (admin, b, c)}) == 1


def test_skip_salvage_finishes_without_importing(cluster):
    h = cluster(2)
    admin, b = h.nodes
    _add_client(b, "AAAPB0300B")
    sync_shadow.start_shadow_mode(admin.db, admin.app_dir)
    _wire(admin)
    server = _serve_dispatch(h, admin)
    _start_non_admin(h, b, admin, server)

    path = sync_shadow.skip_shadow_salvage(b.app_dir)
    assert path is not None and Path(path).exists()
    assert sync_shadow.pending_shadow_salvage(b.app_dir) is None
    assert "AAAPB0300B" not in _live_pans(b)


# ---------------------------------------------------------------- server side

def test_non_admin_start_refused_when_admin_is_not_in_shadow_mode(cluster):
    h = cluster(2)
    admin, b = h.nodes
    server = _serve_dispatch(h, admin)
    with pytest.raises(sync_shadow.ShadowStartError, match="shadow mode"):
        _request(b, admin, server)
    assert not sync_shadow.has_pending_shadow_start(b.app_dir)
    assert not (Path(b.app_dir) / "incoming" / sync_shadow.START_DIRNAME).exists()


def test_only_the_admin_pc_serves_its_replica(cluster):
    h = cluster(3)
    admin, b, c = h.nodes
    sync_shadow.start_shadow_mode(admin.db, admin.app_dir)
    _wire(admin)
    server = _serve_dispatch(h, admin)
    _start_non_admin(h, b, admin, server)

    b_server = _serve_dispatch(h, b)
    with pytest.raises(sync_shadow.ShadowStartError, match="admin"):
        _request(c, b, b_server)


def test_admin_start_refused_on_a_non_admin_pc(cluster):
    h = cluster(2)
    b = h.nodes[1]
    with pytest.raises(sync_shadow.ShadowStartRefused, match="admin"):
        sync_shadow.start_shadow_mode(b.db, b.app_dir)
    assert b.db.get_sync_mode() == "off"


def test_request_on_the_admin_pc_is_refused(cluster):
    h = cluster(2)
    admin = h.nodes[0]
    with pytest.raises(sync_shadow.ShadowStartRefused, match="admin"):
        sync_shadow.request_shadow_start(admin.db, admin.app_dir, session=None)


def test_add_workstation_snapshot_is_exported_with_mode_off(cluster):
    """Owner decision 2026-09-26: a PC added during the shadow week starts in mode off and turns
    shadow on like the others (it can't inherit the admin's 'shadow' without a replica)."""
    h = cluster(1)
    admin = h.nodes[0]
    sync_shadow.start_shadow_mode(admin.db, admin.app_dir)
    manifest, files = sync_snapshot.make_office_snapshot(admin.app_dir, Path(admin.app_dir) / "snapout")
    for f in files:
        conn = sync_capture._open(str(f), admin.hex_key, 5.0)
        try:
            assert sync_capture.read_mode(conn) == "off", f.name
        finally:
            conn.close()


# ---------------------------------------------------------------- start-up swap

def test_start_up_swap_keeps_this_pcs_address_book_and_newer_member_records(cluster):
    h = cluster(3)
    admin, b, c = h.nodes
    sync_shadow.start_shadow_mode(admin.db, admin.app_dir)
    _wire(admin)
    server = _serve_dispatch(h, admin)
    with b.open_db() as conn:
        addresses_before = conn.execute(
            "SELECT device_id, ip, port FROM _local_addresses ORDER BY device_id").fetchall()
        members_before = dict(conn.execute("SELECT device_id, rev FROM _sync_members").fetchall())
    assert addresses_before

    _start_non_admin(h, b, admin, server)
    with b.open_db() as conn:
        assert conn.execute(
            "SELECT device_id, ip, port FROM _local_addresses ORDER BY device_id").fetchall() == addresses_before
        members_after = dict(conn.execute("SELECT device_id, rev FROM _sync_members").fetchall())
        own = conn.execute("SELECT value FROM _sync_meta WHERE key = 'device_id'").fetchone()[0]
        peer_vectors = conn.execute("SELECT COUNT(*) FROM _sync_peer_vectors").fetchone()[0]
    for dev, rev in members_before.items():
        assert members_after.get(dev, -1) >= rev
    assert own == b.device_id
    assert peer_vectors == 0
    # The replica/baseline are not this PC's live file: no address book in them.
    replica_master, _ = sync_shadow.replica_paths(b.app_dir)
    conn = sync_capture._open(str(replica_master), b.hex_key, 5.0)
    try:
        assert sync_capture._meta(conn, "device_id") == b.device_id
        assert sync_capture.read_mode(conn) == "off"
    finally:
        conn.close()


def test_start_up_swap_with_a_damaged_download_leaves_the_live_db_alone(cluster):
    h = cluster(2)
    admin, b = h.nodes
    _add_client(b, "AAAPB0400B")
    sync_shadow.start_shadow_mode(admin.db, admin.app_dir)
    _wire(admin)
    server = _serve_dispatch(h, admin)
    plan = _request(b, admin, server)
    sync_shadow.stage_shadow_start(b.app_dir, plan)
    staged = Path(plan.new_dir) / "master.db"
    with open(staged, "r+b") as f:
        f.seek(4096)
        f.write(b"\x00" * 64)

    b.db.set_seal_listener(None)
    b._db = None
    with pytest.raises(sync_shadow.ShadowStartError):
        sync_shadow.apply_pending_shadow_start(b.app_dir)
    assert not sync_shadow.has_pending_shadow_start(b.app_dir)
    assert b.db.get_sync_mode() == "off"
    assert "AAAPB0400B" in _live_pans(b)
    assert not sync_shadow.replica_paths(b.app_dir)[0].exists()


def test_no_pending_start_is_a_cheap_no_op(tmp_path):
    assert sync_shadow.apply_pending_shadow_start(tmp_path) is None
    assert sync_shadow.pending_shadow_salvage(tmp_path) is None


def test_cancel_removes_the_staged_download_only(cluster):
    h = cluster(2)
    admin, b = h.nodes
    sync_shadow.start_shadow_mode(admin.db, admin.app_dir)
    _wire(admin)
    server = _serve_dispatch(h, admin)
    plan = _request(b, admin, server)
    sync_shadow.cancel_shadow_start(b.app_dir)
    assert not Path(plan.new_dir).exists()
    assert not sync_shadow.has_pending_shadow_start(b.app_dir)
    assert (Path(b.app_dir) / "master.db").exists()


# ---------------------------------------------------------------- review fixes (2026-09-26)

def test_reset_then_start_keeps_own_changes_the_admin_never_received(cluster):
    """Review blocking #1: B saves changes the admin PC never got, then Reset + Start. The
    admin's replica stops B's stream earlier; the swap must carry B's later own changes over,
    otherwise B's next edit leaves a permanent gap and the office never converges."""
    h = cluster(2)
    admin, b = h.nodes
    sync_shadow.start_shadow_mode(admin.db, admin.app_dir)
    _wire(admin)
    server = _serve_dispatch(h, admin)
    _start_non_admin(h, b, admin, server)
    _add_client(b, "AAAPB0600B")
    h.run_until_quiet(timeout=10.0)

    h.partition(admin, b)
    _add_client(b, "AAAPB0601B")                      # sealed on B, never sent
    _add_client(b, "AAAPB0602B")
    h.heal(admin, b)
    sync_shadow.reset_shadow_mode(b.db, b.app_dir)    # mode off: those can't be sent now

    _start_non_admin(h, b, admin, server)
    assert {"AAAPB0601B", "AAAPB0602B"} <= _live_pans(b)
    assert {"AAAPB0601B", "AAAPB0602B"} <= _replica_pans(b)
    assert sync_shadow.capture_check(b.db, b.app_dir).ok
    _add_client(b, "AAAPB0603B")
    h.run_until_quiet(timeout=15.0)
    assert {"AAAPB0601B", "AAAPB0602B", "AAAPB0603B"} <= _replica_pans(admin)
    assert _replica_digest(admin) == _replica_digest(b)


def test_reset_refused_on_the_admin_pc_once_it_has_received_changes(cluster):
    """Review #2: after such a reset the admin could never start again and nobody could
    download; refuse instead."""
    h = cluster(2)
    admin, b = h.nodes
    sync_shadow.start_shadow_mode(admin.db, admin.app_dir)
    _wire(admin)
    server = _serve_dispatch(h, admin)
    _start_non_admin(h, b, admin, server)
    _add_client(b, "AAAPB0700B")
    h.run_until_quiet(timeout=10.0)
    with pytest.raises(sync_shadow.ShadowStartRefused, match="admin PC"):
        sync_shadow.reset_shadow_mode(admin.db, admin.app_dir)
    assert admin.db.get_sync_mode() == "shadow"
    assert sync_shadow.replica_paths(admin.app_dir)[0].exists()


def test_reset_allowed_on_the_admin_pc_before_it_received_anything(cluster):
    h = cluster(1)
    admin = h.nodes[0]
    sync_shadow.start_shadow_mode(admin.db, admin.app_dir)
    sync_shadow.reset_shadow_mode(admin.db, admin.app_dir)
    sync_shadow.start_shadow_mode(admin.db, admin.app_dir)
    assert admin.db.get_sync_mode() == "shadow"


class _Crash(BaseException):
    """Stands in for the process dying: nothing after it runs."""


def test_crash_after_the_files_moved_is_finished_at_the_next_start(cluster, monkeypatch):
    """Review #3: Sera dies after the swap moved the files but before it finished. The next
    start completes the install instead of reporting a false 'nothing changed'."""
    h = cluster(2)
    admin, b = h.nodes
    _add_client(b, "AAAPB0800B")
    sync_shadow.start_shadow_mode(admin.db, admin.app_dir)
    _wire(admin)
    server = _serve_dispatch(h, admin)
    plan = _request(b, admin, server)
    sync_shadow.stage_shadow_start(b.app_dir, plan)
    b.db.set_seal_listener(None)
    b._db = None

    def _die(*a, **k):
        raise _Crash()
    monkeypatch.setattr(sync_shadow, "_finish_start", _die)
    with pytest.raises(_Crash):
        sync_shadow.apply_pending_shadow_start(b.app_dir)
    monkeypatch.undo()

    outcome = sync_shadow.apply_pending_shadow_start(b.app_dir)
    assert outcome is not None and Path(outcome["pre_start_dir"]).exists()
    assert not sync_shadow.has_pending_shadow_start(b.app_dir)
    assert sync_shadow.pending_shadow_salvage(b.app_dir) is not None
    assert sync_shadow.shadow_status(b.db, b.app_dir)["started_at"]
    _wire(b)
    assert b.db.get_sync_mode() == "shadow"
    assert [x.key for x in sync_shadow.plan_shadow_salvage(b.app_dir).to_insert] == ["AAAPB0800B"]


def test_crash_halfway_through_the_moves_is_put_back_at_the_next_start(cluster, monkeypatch):
    h = cluster(2)
    admin, b = h.nodes
    _add_client(b, "AAAPB0900B")
    sync_shadow.start_shadow_mode(admin.db, admin.app_dir)
    _wire(admin)
    server = _serve_dispatch(h, admin)
    plan = _request(b, admin, server)
    sync_shadow.stage_shadow_start(b.app_dir, plan)
    b.db.set_seal_listener(None)
    b._db = None

    real_replace = sync_shadow.os.replace
    shadow_dir = Path(b.app_dir) / sync_shadow.SHADOW_DIRNAME

    def _replace(src, dst):
        # Dies at the first install move: the live files are already moved aside, nothing new
        # is in place yet.
        if Path(dst).parent == shadow_dir and Path(dst).name.startswith("baseline_"):
            raise _Crash()
        return real_replace(src, dst)
    monkeypatch.setattr(sync_shadow.os, "replace", _replace)
    with pytest.raises(_Crash):
        sync_shadow.apply_pending_shadow_start(b.app_dir, _undo_on_error=False)
    monkeypatch.undo()
    assert not (Path(b.app_dir) / "master.db").exists()      # the state a real crash leaves

    with pytest.raises(sync_shadow.ShadowStartError, match="put back"):
        sync_shadow.apply_pending_shadow_start(b.app_dir)
    assert not sync_shadow.has_pending_shadow_start(b.app_dir)
    assert b.db.get_sync_mode() == "off"
    assert "AAAPB0900B" in _live_pans(b)
    assert not sync_shadow.replica_paths(b.app_dir)[0].exists()


def test_download_without_raw_is_refused(cluster, monkeypatch):
    """Review #5: B's rawPayload.db would be moved aside with nothing in its place."""
    h = cluster(2)
    admin, b = h.nodes
    sync_shadow.start_shadow_mode(admin.db, admin.app_dir)
    _wire(admin)
    server = _serve_dispatch(h, admin)
    real_export = sync_shadow._export_replica
    monkeypatch.setattr(sync_shadow, "_export_replica",
                        lambda app_dir, hex_key, dest: real_export(app_dir, hex_key, dest)[:1])
    with pytest.raises(sync_shadow.ShadowStartError, match="rawPayload.db"):
        _request(b, admin, server)
    assert not sync_shadow.has_pending_shadow_start(b.app_dir)


def test_request_refused_when_the_session_is_not_to_the_admin_pc(cluster):
    """Review #6: checked on this PC before anything is sent."""
    h = cluster(3)
    admin, b, c = h.nodes
    b_server = _serve_dispatch(h, b)
    session = c.transport.connect("127.0.0.1", b_server.address[1], b.device_id)
    try:
        with pytest.raises(sync_shadow.ShadowStartError, match="not the office admin PC"):
            sync_shadow.request_shadow_start(c.db, c.app_dir, session)
    finally:
        session.close()


# ---------------------------------------------------------------- reset + status

def test_reset_renames_shadow_files_and_resets_the_week(cluster):
    h = cluster(2)
    admin, b = h.nodes
    sync_shadow.start_shadow_mode(admin.db, admin.app_dir)
    _wire(admin)
    server = _serve_dispatch(h, admin)
    _start_non_admin(h, b, admin, server)
    status = sync_shadow.shadow_status(b.db, b.app_dir)
    assert status["mode"] == "shadow" and status["started_at"] and status["day"] == 1

    sync_shadow.reset_shadow_mode(b.db, b.app_dir)
    shadow_dir = Path(b.app_dir) / sync_shadow.SHADOW_DIRNAME
    for name in ("replica_master.db", "replica_raw.db", "baseline_master.db", "baseline_raw.db",
                 sync_shadow.STARTED_FILE):
        assert not (shadow_dir / name).exists(), name
        assert list(shadow_dir.glob(f"{name}.bak-*")), name   # renamed, never deleted
    assert b.db.get_sync_mode() == "off"
    status = sync_shadow.shadow_status(b.db, b.app_dir)
    assert status["started_at"] is None and status["replica"] is False

    # After a reset a non-admin PC can start again from the admin's replica.
    _start_non_admin(h, b, admin, server)
    assert b.db.get_sync_mode() == "shadow"


def test_reset_is_refused_while_a_pending_start_is_staged(cluster):
    h = cluster(2)
    admin, b = h.nodes
    sync_shadow.start_shadow_mode(admin.db, admin.app_dir)
    _wire(admin)
    server = _serve_dispatch(h, admin)
    plan = _request(b, admin, server)
    sync_shadow.stage_shadow_start(b.app_dir, plan)
    with pytest.raises(sync_shadow.ShadowStartRefused):
        sync_shadow.reset_shadow_mode(b.db, b.app_dir)


def test_started_file_records_method_and_source(cluster):
    h = cluster(2)
    admin, b = h.nodes
    sync_shadow.start_shadow_mode(admin.db, admin.app_dir)
    _wire(admin)
    server = _serve_dispatch(h, admin)
    _start_non_admin(h, b, admin, server)
    a = json.loads((Path(admin.app_dir) / "shadow" / sync_shadow.STARTED_FILE).read_text("utf-8"))
    s = json.loads((Path(b.app_dir) / "shadow" / sync_shadow.STARTED_FILE).read_text("utf-8"))
    assert a["method"] == "admin" and a["source_device"] == admin.device_id
    assert s["method"] == "admin_replica" and s["source_device"] == admin.device_id


def test_shadow_log_has_no_row_contents(cluster):
    h = cluster(2)
    admin, b = h.nodes
    _add_client(b, "AAAPB0500B", notes="Secret note text")
    sync_shadow.start_shadow_mode(admin.db, admin.app_dir)
    _wire(admin)
    server = _serve_dispatch(h, admin)
    _start_non_admin(h, b, admin, server)
    sync_shadow.apply_shadow_salvage(b.app_dir)
    for node in (admin, b):
        log = Path(node.app_dir) / "logs" / sync_shadow.SHADOW_LOG_NAME
        text = log.read_text("utf-8")
        assert "Secret note text" not in text
        assert "AAAPB0500B" not in text


def test_no_pyside6_in_sync_shadow():
    tree = ast.parse((ROOT / "sync_shadow.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.startswith("PySide6") for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("PySide6")
