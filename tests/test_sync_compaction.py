"""Tests for Sera Sync v3 P4-4: change-log compaction and catch-up (sync_compaction).

Accept (blueprint §5 P4-4): harness test (g),
``tests/test_sync_convergence.py::test_convergence_offline_node_snapshot_recovery``. The tests
here cover the rules: the floor is the lowest ack of the active members, members not seen for
60 days are left out, a member never seen counts from its ``added_at``, nothing is compacted
when no other member was seen recently, tombstones are kept 180 days, a peer below the floor
gets ``need_snapshot``, and a catch-up is only started once the peer holds this PC's own
changes.

Real harness nodes (P2-4 pairing, P2-3 mutual TLS); tmp_path only (§0 rule 2); invented data
only (§0 rule 12).
"""

import datetime
import json
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")

import sync_compaction  # noqa: E402
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


def _add_clients(node, n, prefix):
    def fn(conn):
        conn.executemany(
            "INSERT INTO clients (notes, is_archived, created_at, updated_at, client_id_token) "
            "VALUES ('n', 0, '2026-09-28T10:00:00Z', '2026-09-28T10:00:00Z', ?)",
            [(f"{prefix}-{i:05d}",) for i in range(n)])
    node.write(fn)


def _age(node, peer, days):
    stamp = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    for opener in (node.open_db, node.open_raw_db):
        with opener() as conn:
            conn.execute("UPDATE _sync_peer_vectors SET seen_at = ? WHERE device_id = ?", (stamp, peer.device_id))
    with sync_compaction_conn(node) as conn:
        sync_compaction.note_peer_seen(conn, peer.device_id, stamp)


def sync_compaction_conn(node):
    """Autocommit connection to the node's master.db (note_peer_seen runs its own transaction)."""
    import contextlib
    import sync_capture

    @contextlib.contextmanager
    def cm():
        conn = sync_capture._open(str(node.db_path), node.hex_key, 5.0)
        try:
            yield conn
        finally:
            conn.close()
    return cm()


def _changes(node, stream):
    with node.open_db() as conn:
        return [r[0] for r in conn.execute(
            "SELECT origin_seq FROM _sync_changes WHERE origin = ? ORDER BY origin_seq", (stream,))]


def _vector(node, stream):
    with node.open_db() as conn:
        row = conn.execute("SELECT max_seq FROM _sync_vector WHERE origin = ?", (stream,)).fetchone()
    return row[0] if row else 0


def _ms(node):
    return node.device_id + ":m"


# ---------------------------------------------------------------- floors

def test_floor_is_the_lowest_ack_of_the_active_members(cluster):
    h = cluster(3)
    n0, n1, n2 = h.nodes
    _add_clients(n0, 5, "A")
    h.run_until_quiet(timeout=20.0)
    h.partition(n0, n2)
    h.partition(n1, n2)
    _add_clients(n0, 5, "B")
    h.run_until_quiet(timeout=20.0)          # n1 has all 10, n2 only the first 5
    stream = _ms(n0)
    have = _vector(n0, stream)
    n2_has = _vector(n2, stream)
    assert n2_has < have

    out = n0.engine.compact()
    assert out["skipped"] is None
    floors = n0.engine.compaction_floors()
    assert floors[stream] == n2_has
    assert _changes(n0, stream)[0] == n2_has + 1
    assert out["master"]["deleted"] >= n2_has


def test_a_member_not_seen_for_60_days_is_left_out(cluster):
    h = cluster(3)
    n0, n1, n2 = h.nodes
    _add_clients(n0, 3, "A")
    h.run_until_quiet(timeout=20.0)
    h.partition(n0, n2)
    h.partition(n1, n2)
    _add_clients(n0, 3, "B")
    h.run_until_quiet(timeout=20.0)
    stream = _ms(n0)
    _age(n0, n2, days=sync_compaction.STALE_MEMBER_DAYS - 1)
    n0.engine.compact()
    assert n0.engine.compaction_floors()[stream] == _vector(n2, stream)      # still counted

    _age(n0, n2, days=sync_compaction.STALE_MEMBER_DAYS + 1)
    n0.engine.compact()
    assert n0.engine.compaction_floors()[stream] == _vector(n0, stream)      # left out now
    assert _changes(n0, stream) == []


def test_floors_never_go_down_and_never_pass_what_this_pc_holds(cluster):
    h = cluster(2)
    n0, n1 = h.nodes
    _add_clients(n0, 4, "A")
    h.run_until_quiet(timeout=20.0)
    n0.engine.compact()
    stream = _ms(n0)
    floor = n0.engine.compaction_floors()[stream]
    assert floor == _vector(n0, stream)
    # A peer claiming more than this PC holds doesn't move the floor past it.
    with n0.open_db() as conn:
        conn.execute("UPDATE _sync_peer_vectors SET max_seq = max_seq + 100 WHERE device_id = ?", (n1.device_id,))
    n0.engine.compact()
    assert n0.engine.compaction_floors()[stream] == floor
    # ...and a lower ack later (e.g. a vector reset) doesn't lower it.
    with n0.open_db() as conn:
        conn.execute("UPDATE _sync_peer_vectors SET max_seq = 0 WHERE device_id = ?", (n1.device_id,))
    n0.engine.compact()
    assert n0.engine.compaction_floors()[stream] == floor


def test_nothing_is_compacted_when_no_other_member_was_seen_recently(cluster):
    h = cluster(2)
    n0, n1 = h.nodes
    _add_clients(n0, 3, "A")
    h.run_until_quiet(timeout=20.0)
    h.partition(n0, n1)
    _add_clients(n0, 3, "OWN")            # own changes nobody has acked yet
    _age(n0, n1, days=sync_compaction.STALE_MEMBER_DAYS + 5)
    out = n0.engine.compact()
    assert out["skipped"]
    assert n0.engine.compaction_floors() == {}
    assert len(_changes(n0, _ms(n0))) == _vector(n0, _ms(n0))


def test_a_member_never_seen_counts_from_its_added_at(cluster):
    h = cluster(2)
    n0, n1 = h.nodes
    _add_clients(n0, 3, "A")
    with n0.open_db() as conn:
        conn.execute("DELETE FROM _sync_peer_vectors")
        conn.execute("DELETE FROM _sync_meta WHERE key = ?", (sync_compaction.PEER_SEEN_META_KEY,))
    with n0.open_raw_db() as conn:
        conn.execute("DELETE FROM _sync_peer_vectors")
    # n1 was added just now and never synced: its acks count as 0, nothing goes.
    assert n0.engine.compact()["skipped"] is None
    assert n0.engine.compaction_floors() == {}
    assert len(_changes(n0, _ms(n0))) == _vector(n0, _ms(n0))


def test_tombstones_are_kept_180_days(cluster):
    h = cluster(2)
    n0, n1 = h.nodes
    h.run_until_quiet(timeout=20.0)
    now = datetime.datetime.now(datetime.timezone.utc)

    def hlc(days):
        ms = int((now - datetime.timedelta(days=days)).timestamp() * 1000)
        return f"{ms:013d}.0000.{n0.device_id}"
    with n0.open_db() as conn:
        conn.executemany("INSERT INTO _sync_tombstones(tbl, row_key, hlc, origin) VALUES ('clients', ?, ?, ?)",
                         [(json.dumps(["a" * 32]), hlc(sync_compaction.TOMBSTONE_KEEP_DAYS + 1), _ms(n0)),
                          (json.dumps(["b" * 32]), hlc(sync_compaction.TOMBSTONE_KEEP_DAYS - 1), _ms(n0))])
    out = n0.engine.compact()
    assert out["master"]["tombstones"] == 1
    with n0.open_db() as conn:
        left = [json.loads(r[0])[0] for r in conn.execute("SELECT row_key FROM _sync_tombstones")]
    assert left == ["b" * 32]


def test_compaction_only_runs_in_mode_live(cluster):
    h = cluster(2)
    n0, _n1 = h.nodes
    n0.db.set_sync_mode("off")
    assert n0.engine.compact()["skipped"] == "mode off"


# ---------------------------------------------------------------- need_snapshot and catch-up

def test_peer_below_the_real_floor_gets_need_snapshot_and_no_changes(cluster):
    h = cluster(3)
    n0, n1, n2 = h.nodes
    h.run_until_quiet(timeout=20.0)          # n2 has been seen once (peer vectors exist)
    h.partition(n0, n2)
    h.partition(n1, n2)
    _add_clients(n0, 4, "F")
    h.run_until_quiet(timeout=20.0)
    _age(n0, n2, days=sync_compaction.STALE_MEMBER_DAYS + 1)
    n0.engine.compact()
    h.heal()
    r = n0.sync_with(n2)
    assert r.ok and r.sent == 0
    with n2.open_db() as conn:
        assert conn.execute("SELECT count(*) FROM clients WHERE client_id_token LIKE 'F-%'").fetchone()[0] == 0
    assert [i for k, i in n2.events if k == "need_snapshot"][-1]["streams"] == [_ms(n0)]


def test_catch_up_waits_until_the_peer_holds_this_pcs_own_changes(cluster, monkeypatch):
    h = cluster(2)
    n0, n1 = h.nodes
    _add_clients(n1, 2, "OWN")
    started = []
    monkeypatch.setattr(n1.engine, "_catch_up", lambda dev, own: started.append(dev))
    own_stream = _ms(n1)
    assert n1.engine._maybe_catch_up(n0.device_id, {own_stream: 0}) is False
    assert not started
    assert n1.engine._maybe_catch_up(n0.device_id, n1.engine.own_vectors()) is True
    n1.engine._catch_up_thread.join(5)
    assert started == [n0.device_id]


def test_download_refuses_a_snapshot_without_this_pcs_own_changes(cluster):
    h = cluster(2)
    n0, n1 = h.nodes
    h.run_until_quiet(timeout=20.0)
    need = {_ms(n1): _vector(n1, _ms(n1)) + 5}
    with pytest.raises(sync_compaction.CatchUpError):
        sync_compaction.download_catch_up(n1.app_dir, n1.hex_key,
                                          lambda: n1.engine._open_session_to(n0.device_id),
                                          n0.device_id, need)
    assert not sync_compaction.has_pending_catch_up(n1.app_dir)


def test_failed_catch_up_install_is_put_back(cluster):
    h = cluster(2)
    n0, n1 = h.nodes
    _add_clients(n0, 2, "S")
    h.run_until_quiet(timeout=20.0)
    sync_compaction.download_catch_up(n1.app_dir, n1.hex_key,
                                      lambda: n1.engine._open_session_to(n0.device_id),
                                      n0.device_id, {})
    before = h.digest(n1)
    staged = sync_compaction._stage_dir(n1.app_dir) / "new" / "master.db"
    staged.write_bytes(staged.read_bytes()[:-10] + b"0123456789")      # changed since staged

    def install(node):
        with pytest.raises(sync_compaction.CatchUpError):
            sync_compaction.apply_pending_catch_up(node.app_dir, node.hex_key)
    h.restart_node(n1, before_open=install)
    assert not sync_compaction.has_pending_catch_up(n1.app_dir)
    assert h.digest(n1) == before


def test_interrupted_catch_up_install_is_finished_at_the_next_start(cluster):
    h = cluster(2)
    n0, n1 = h.nodes
    _add_clients(n0, 2, "J")
    h.run_until_quiet(timeout=20.0)
    sync_compaction.download_catch_up(n1.app_dir, n1.hex_key,
                                      lambda: n1.engine._open_session_to(n0.device_id),
                                      n0.device_id, {})

    def crash_then_start(node):
        import sync_shadow
        real = sync_shadow._replace_when_unlocked
        calls = []

        def flaky(src, dst, wait=None):
            calls.append(src)
            real(src, dst, wait)
            if len(calls) == 3:
                raise KeyboardInterrupt("simulated crash")
        sync_shadow._replace_when_unlocked = flaky
        try:
            with pytest.raises(KeyboardInterrupt):
                sync_compaction.apply_pending_catch_up(node.app_dir, node.hex_key, _undo_on_error=False)
        finally:
            sync_shadow._replace_when_unlocked = real
        # Next start: the journal says it was interrupted halfway, so everything is put back.
        with pytest.raises(sync_compaction.CatchUpError):
            sync_compaction.apply_pending_catch_up(node.app_dir, node.hex_key)
    before = h.digest(n1)
    h.restart_node(n1, before_open=crash_then_start)
    assert h.digest(n1) == before
    assert not sync_compaction.has_pending_catch_up(n1.app_dir)
