"""Tests for Sera Sync v3 P3-5: session protocol, forwarding, poke and scheduler (sync_engine).

Accept (blueprint §5 P3-5): P3-0 tests (a) and (f) (tests/test_sync_convergence.py, now driven
by these sessions); a session interrupted mid-transfer resumes correctly next round
(`test_session_interrupted_mid_transfer_resumes_next_round`); the poke path syncs within 3 s in
the harness (`test_poke_path_syncs_within_3_seconds`).

Nodes are real harness nodes on 127.0.0.1 (P2-4 pairing, P2-3 mutual TLS). tmp_path only
(§0 rule 2); invented data only (§0 rule 12).
"""

import ast
import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")

import sync_engine  # noqa: E402
from tests.sync_harness import SyncHarness  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- helpers

@pytest.fixture
def cluster(tmp_path):
    made = []

    def make(n=2):
        h = SyncHarness(num_nodes=n, base_dir=tmp_path / f"h{len(made)}")
        made.append(h)
        return h
    yield make
    for h in made:
        h.close()


def _add_clients(node, n, prefix="C"):
    def fn(conn):
        conn.executemany(
            "INSERT INTO clients (notes, is_archived, created_at, updated_at, client_id_token) "
            "VALUES ('n', 0, '2026-09-25T10:00:00Z', '2026-09-25T10:00:00Z', ?)",
            [(f"{prefix}-{i:05d}",) for i in range(n)])
    node.write(fn)


def _count(node, prefix):
    with node.open_db() as c:
        return c.execute("SELECT count(*) FROM clients WHERE client_id_token LIKE ?", (prefix + "-%",)).fetchone()[0]


def _vector(node, stream):
    with node.open_db() as c:
        row = c.execute("SELECT max_seq FROM _sync_vector WHERE origin = ?", (stream,)).fetchone()
    return row[0] if row else 0


def _events(node, kind):
    return [info for k, info in node.events if k == kind]


def _mstream(node):
    return node.device_id + ":m"


# ---------------------------------------------------------------- Accept

def test_session_interrupted_mid_transfer_resumes_next_round(cluster):
    h = cluster(2)
    n0, n1 = h.nodes
    _add_clients(n0, 1200, "BIG")          # one seal -> 1200 changes -> 3 batches of <= 500

    real = n1.db.apply_changes
    calls = []

    def cut_after_first_batch(which, changes, **kw):
        r = real(which, changes, **kw)
        calls.append(len(changes))
        if len(calls) == 1:
            h.partition(n0, n1)            # closes the session while batch 2 is on its way
        return r

    n1.db.apply_changes = cut_after_first_batch
    result = n0.sync_with(n1)
    assert not result.ok
    got = _count(n1, "BIG")
    assert 0 < got < 1200
    assert calls[0] <= sync_engine.MAX_BATCH_CHANGES
    # The applied batch and the vector were committed together.
    assert _vector(n1, _mstream(n0)) == got

    n1.db.apply_changes = real
    h.heal()
    h.run_until_quiet(timeout=20.0)
    assert _count(n1, "BIG") == 1200
    with n1.open_db() as c:
        assert c.execute("SELECT count(DISTINCT gid) FROM clients WHERE client_id_token LIKE 'BIG-%'").fetchone()[0] == 1200
    assert _vector(n1, _mstream(n0)) == _vector(n0, _mstream(n0))
    assert h.digest(n0) == h.digest(n1)


def test_poke_path_syncs_within_3_seconds(cluster):
    h = cluster(2)
    n0, n1 = h.nodes
    for n in (n0, n1):
        n.engine.interval = 3600.0         # only a poke can make them sync
        n.engine.jitter = 0.0
        n.engine.start()
    t0 = time.monotonic()
    _add_clients(n0, 1, "POKE")            # seal after commit -> poke -> n1 syncs within 1 s
    while time.monotonic() - t0 < 3.0 and _count(n1, "POKE") == 0:
        time.sleep(0.05)
    assert _count(n1, "POKE") == 1, "poke did not bring the change over within 3 s"
    # n1 started the session (it was poked): its "synced" event follows the session's BYE.
    while time.monotonic() - t0 < 5.0 and not _events(n1, "synced"):
        time.sleep(0.02)
    assert any(e["device_id"] == n0.device_id and e["received"] for e in _events(n1, "synced"))


# ---------------------------------------------------------------- HELLO checks

def test_schema_mismatch_ends_the_session_and_says_who_needs_updating(cluster):
    h = cluster(2)
    n0, n1 = h.nodes
    _add_clients(n0, 3, "S")
    with n1.open_db() as c:
        c.execute("UPDATE _sync_meta SET value = '2' WHERE key = 'schema_version'")
    r = n0.sync_with(n1)
    assert r.error == "schema"
    assert _count(n1, "S") == 0
    mine = _events(n0, "needs_update")
    theirs = _events(n1, "needs_update")
    assert mine and mine[-1]["who"] == "this_pc" and mine[-1]["mine"] == 1 and mine[-1]["yours"] == 2
    assert theirs and theirs[-1]["who"] == "peer" and theirs[-1]["device_id"] == n0.device_id
    assert n1.engine.peer_status()[n0.device_id]["needs_update"]["who"] == "peer"


def test_wrong_office_tag_is_refused(cluster):
    h = cluster(2)
    n0, n1 = h.nodes
    n1.engine.office_tag = "0" * 16
    r = n0.sync_with(n1)
    assert r.error == "office"


def test_non_member_is_not_dialled(cluster):
    h = cluster(2)
    n0, _ = h.nodes
    assert n0.engine.sync_with("e" * 32).error == "not_a_member"
    assert n0.engine.sync_with(n0.device_id).error == "not_a_member"


# ---------------------------------------------------------------- membership

def _new_identity(tmp_path, name):
    import sync_identity
    d = tmp_path / name
    d.mkdir()
    ident = sync_identity.ensure_device_identity(d)
    return ident.device_id, ident.cert_pem.decode("ascii")


def test_new_member_record_reaches_other_pcs_and_rebuilds_trust(cluster, tmp_path):
    import sync_admin
    h = cluster(3)
    n0, n1, n2 = h.nodes
    new_id, new_pem = _new_identity(tmp_path, "newpc")
    with n0.open_db() as c:
        sync_admin.add_member(n0.app_dir, c, n0.device_id, new_pem, "New PC")
    assert new_id not in n1.transport.members.device_ids

    r = n0.sync_with(n1)
    assert r.ok and r.members_exchanged and not r.members_changed   # n1 changed, not n0
    assert new_id in n1.transport.members.device_ids
    assert any(new_id in e["members"] for e in _events(n1, "members_changed"))
    # Forwarded on: n2 learns it from n1, never having talked to n0.
    h.partition(n0, n2)
    r = n2.sync_with(n1)
    assert r.ok and r.members_changed
    assert new_id in n2.transport.members.device_ids
    # Same records everywhere now: the next session doesn't exchange them again.
    r = n1.sync_with(n2)
    assert r.ok and not r.members_exchanged


def test_revoke_reaches_other_pcs_and_the_removed_pc_is_refused(cluster):
    import sync_admin
    h = cluster(3)
    n0, n1, n2 = h.nodes
    with n0.open_db() as c:
        sync_admin.revoke_member(n0.app_dir, c, n0.device_id, n2.device_id)
    r = n1.sync_with(n0)
    assert r.ok and r.members_changed
    assert n2.device_id not in n1.transport.members.device_ids
    _add_clients(n1, 1, "REV")
    r = n2.sync_with(n1)                   # n2 still trusts n1, but n1 refuses n2 now
    assert not r.ok
    assert _count(n2, "REV") == 0


def test_hand_over_admin_tells_the_named_pc(cluster):
    import sync_admin
    h = cluster(2)
    n0, n1 = h.nodes
    with n0.open_db() as c:
        sync_admin.hand_over_admin(n0.app_dir, c, n0.device_id, n1.device_id)
    r = n0.sync_with(n1)
    assert r.ok and r.members_exchanged
    assert _events(n1, "admin_named_here")
    with n1.open_db() as c:
        assert sync_admin.get_office_admin(c, n1.office.admin_pubkey)["device_id"] == n1.device_id


def test_forged_member_record_is_ignored(cluster):
    h = cluster(2)
    n0, n1 = h.nodes
    recs = n0.engine._member_records()
    forged = dict(recs[0], name="Evil", rev=recs[0]["rev"] + 5)
    real = n0.engine._member_records
    n0.engine._member_records = lambda: real() + [forged]
    r = n0.sync_with(n1)
    assert r.ok and r.members_exchanged
    assert not any(k == "members_changed" for k, _ in n1.events)
    assert all(m.get("name") != "Evil" for m in n1.engine._member_records())


# ---------------------------------------------------------------- addresses and vectors

def test_peer_vectors_and_working_address_are_recorded(cluster):
    h = cluster(2)
    n0, n1 = h.nodes
    _add_clients(n0, 2, "V")
    r = n0.sync_with(n1)
    assert r.ok and r.sent == 2
    with n0.open_db() as c:
        pv = dict(c.execute("SELECT origin, max_seq FROM _sync_peer_vectors WHERE device_id = ?",
                            (n1.device_id,)).fetchall())
        local_ok = c.execute("SELECT local_ok_at FROM _local_addresses WHERE device_id = ?",
                             (n1.device_id,)).fetchone()[0]
    assert pv[_mstream(n0)] == _vector(n0, _mstream(n0))
    assert local_ok
    with n1.open_db() as c:
        pv1 = dict(c.execute("SELECT origin, max_seq FROM _sync_peer_vectors WHERE device_id = ?",
                             (n0.device_id,)).fetchall())
    assert pv1[_mstream(n0)] == _vector(n0, _mstream(n0))
    assert n0.engine.peer_status()[n1.device_id]["online"]


def test_hello_gossips_addresses(cluster):
    import sync_discovery
    h = cluster(3)
    n0, n1, n2 = h.nodes
    now = datetime.now(timezone.utc).isoformat()
    with n0.open_db() as c:
        sync_discovery.upsert_address(c, n2.device_id, "10.20.30.40", 49159, source="manual", last_ok_at=now)
    assert n0.sync_with(n1).ok
    with n1.open_db() as c:
        rows = c.execute("SELECT ip, source FROM _local_addresses WHERE device_id = ? AND ip = '10.20.30.40'",
                         (n2.device_id,)).fetchall()
    assert rows == [("10.20.30.40", "gossip")]


# ---------------------------------------------------------------- batches

def test_batches_hold_at_most_500_changes(cluster):
    h = cluster(2)
    n0, _ = h.nodes
    _add_clients(n0, 1200, "B")
    vec = json.dumps({})
    cursor, sizes, seen = ("", "", 0), [], set()
    while True:
        items, cursor = n0.engine._next_batch("master", vec, cursor)
        if not items:
            break
        sizes.append(len(items))
        seen.update((i["origin"], i["origin_seq"]) for i in items)
        hlcs = [(i["hlc"], i["origin"], i["origin_seq"]) for i in items]
        assert hlcs == sorted(hlcs)
    assert max(sizes) <= 500 and sum(sizes) == len(seen) >= 1200


def test_batches_hold_about_1_mb_and_a_big_change_still_goes(cluster):
    h = cluster(2)
    n0, _ = h.nodes
    big = "x" * 300_000
    n0.write(lambda conn: conn.executemany(
        "INSERT INTO clients (notes, is_archived, created_at, updated_at, client_id_token) "
        "VALUES (?, 0, '2026-09-25T10:00:00Z', '2026-09-25T10:00:00Z', ?)",
        [(big, f"MB-{i}") for i in range(7)]))
    n0.write(lambda conn: conn.execute(
        "INSERT INTO clients (notes, is_archived, created_at, updated_at, client_id_token) "
        "VALUES (?, 0, '2026-09-25T10:00:00Z', '2026-09-25T10:00:00Z', 'HUGE')", ("y" * 1_500_000,)))
    cursor, batches = ("", "", 0), []
    while True:
        items, cursor = n0.engine._next_batch("master", "{}", cursor)
        if not items:
            break
        batches.append(items)
    for b in batches:
        size = sum(len(json.dumps(i, ensure_ascii=False)) + 1 for i in b)
        assert size <= sync_engine.MAX_BATCH_BYTES or len(b) == 1
    assert sum(len(b) for b in batches) == 8
    assert len(batches) >= 3


def test_only_what_the_peer_lacks_is_sent(cluster):
    h = cluster(2)
    n0, n1 = h.nodes
    _add_clients(n0, 5, "L")
    assert n0.sync_with(n1).sent == 5
    _add_clients(n0, 2, "M")
    r = n1.sync_with(n0)
    assert r.ok and r.received == 2 and r.sent == 0
    r = n0.sync_with(n1)
    assert r.ok and r.moved == 0


def test_malformed_batch_drops_the_session_and_backs_off(cluster):
    h = cluster(2)
    n0, n1 = h.nodes
    _add_clients(n0, 2, "X")
    real = n0.engine._next_batch

    def bad(which, vec, cursor):
        items, cur = real(which, vec, cursor)
        if items:
            items[0] = dict(items[0], op="explode")
        return items, cur

    n0.engine._next_batch = bad
    r = n0.sync_with(n1)
    assert r.error == "bad_batch"
    assert _count(n1, "X") == 0
    # The receiver doesn't retry that peer blindly.
    assert n1.sync_with(n0).error == "backoff"
    n0.engine._next_batch = real
    n1.engine._backoff.clear()
    assert n1.sync_with(n0).ok and _count(n1, "X") == 2


def test_change_sent_under_the_other_database_is_refused(cluster):
    h = cluster(2)
    n0, n1 = h.nodes
    _add_clients(n0, 1, "O")
    real = n0.engine._next_batch

    def wrong_db(which, vec, cursor):
        items, cur = real(which, vec, cursor)
        return [dict(i, origin=i["origin"][:-2] + ":r") for i in items], cur

    n0.engine._next_batch = wrong_db
    assert n0.sync_with(n1).error == "bad_batch"
    assert _count(n1, "O") == 0


def test_apply_failure_rolls_back_and_the_next_round_retries(cluster):
    h = cluster(2)
    n0, n1 = h.nodes
    _add_clients(n0, 3, "F")
    real = n1.db.apply_changes

    def fail(*a, **kw):
        raise RuntimeError("disk full (test)")

    n1.db.apply_changes = fail
    r = n0.sync_with(n1)
    assert r.error == "apply_failed"
    assert _count(n1, "F") == 0 and _vector(n1, _mstream(n0)) == 0
    n1.db.apply_changes = real
    assert n0.sync_with(n1).ok                 # no backoff after a failure that isn't the peer's fault
    assert _count(n1, "F") == 3


def test_need_snapshot_when_the_peer_is_below_the_compaction_floor(cluster):
    h = cluster(2)
    n0, n1 = h.nodes
    _add_clients(n0, 2, "N")
    stream = _mstream(n0)
    n0.engine.compaction_floors = lambda: {stream: 10 ** 6}
    r = n1.sync_with(n0)
    assert r.ok and r.need_snapshot == [stream]
    assert _count(n1, "N") == 0
    assert _events(n1, "need_snapshot")[-1]["streams"] == [stream]


# ---------------------------------------------------------------- modes

def test_mode_off_exchanges_membership_but_no_changes(cluster):
    h = cluster(2)
    n0, n1 = h.nodes
    _add_clients(n0, 2, "OFF")
    n1.db.set_sync_mode("off")
    r = n0.sync_with(n1)
    assert r.ok and r.sent == 0 and r.received == 0
    assert _count(n1, "OFF") == 0


def test_shadow_mode_without_a_replica_accepts_nothing_and_with_one_uses_it(cluster):
    h = cluster(2)
    n0, n1 = h.nodes
    _add_clients(n0, 2, "SH")
    n1.db.set_sync_mode("shadow")
    r = n0.sync_with(n1)
    assert r.ok and r.sent == 0
    got = []

    def shadow_apply(which, items):
        got.extend(items)
        import sync_apply
        return sync_apply.ApplyResult()

    n1.engine.shadow_apply = shadow_apply
    r = n0.sync_with(n1)
    assert r.ok and r.sent == 2 and len(got) == 2
    assert _count(n1, "SH") == 0               # the live DB is untouched in shadow mode


# ---------------------------------------------------------------- session limits

def test_one_session_per_peer(cluster):
    h = cluster(2)
    n0, n1 = h.nodes
    lock = n1.engine._peer_lock(n0.device_id)
    lock.acquire()
    try:
        assert n0.sync_with(n1).error == "busy"
        assert n1.sync_with(n0).error == "busy"
    finally:
        lock.release()
    assert n0.sync_with(n1).ok


def test_at_most_three_sessions_in_total(cluster):
    h = cluster(2)
    n0, n1 = h.nodes
    for _ in range(3):
        assert n1.engine._slots.acquire(blocking=False)
    try:
        assert not n1.engine._slots.acquire(blocking=False)
        assert n0.sync_with(n1).error == "busy"
        assert n1.sync_with(n0).error == "busy"
    finally:
        for _ in range(3):
            n1.engine._slots.release()
    assert n0.sync_with(n1).ok


def test_crossed_dials_the_smaller_device_id_keeps_its_session(cluster):
    h = cluster(2)
    lo, hi = sorted(h.nodes, key=lambda n: n.device_id)
    # hi is "dialling" lo and holds its own peer lock for a moment: lo's incoming session waits
    # for it (hi > lo) instead of both refusing each other.
    lock = hi.engine._peer_lock(lo.device_id)
    lock.acquire()
    hi.engine._outgoing.add(lo.device_id)

    def release():
        time.sleep(0.5)
        hi.engine._outgoing.discard(lo.device_id)
        lock.release()

    t = threading.Thread(target=release)
    t.start()
    assert lo.sync_with(hi).ok
    t.join()
    # lo is "dialling" hi: hi's incoming session is refused at once (lo < hi).
    lock = lo.engine._peer_lock(hi.device_id)
    lock.acquire()
    lo.engine._outgoing.add(hi.device_id)
    try:
        t0 = time.monotonic()
        assert hi.sync_with(lo).error == "busy"
        assert time.monotonic() - t0 < sync_engine.RESPONDER_WAIT_SECONDS
    finally:
        lo.engine._outgoing.discard(hi.device_id)
        lock.release()


# ---------------------------------------------------------------- scheduler and pokes

def test_scheduler_round_syncs_with_reachable_members(cluster):
    h = cluster(2)
    n0, n1 = h.nodes
    _add_clients(n0, 1, "RND")                 # n0's engine isn't running: no poke
    n1.engine.interval = 1.0
    n1.engine.jitter = 0.0
    n1.engine.start()
    t0 = time.monotonic()
    while time.monotonic() - t0 < 5.0 and _count(n1, "RND") == 0:
        time.sleep(0.05)
    assert _count(n1, "RND") == 1
    assert n1.engine.reachable_members() == [n0.device_id]


def test_poke_format_and_validation(cluster):
    h = cluster(3)
    n0, n1, n2 = h.nodes
    eng = n1.engine
    good = sync_engine.make_poke(eng.office_tag, n0.device_id)
    assert sync_engine.parse_poke(good) == {"office": eng.office_tag, "dev": n0.device_id}
    assert json.loads(good) == {"magic": "sera-sync-v3", "office": eng.office_tag,
                                "dev": n0.device_id, "poke": True}
    for bad in (b"", b"{}", b"not json", good.replace(b"true", b"false"),
                json.dumps({"magic": "x", "office": eng.office_tag, "dev": n0.device_id, "poke": True}).encode(),
                b"x" * 600):
        assert sync_engine.parse_poke(bad) is None
    assert not eng.handle_poke(good), "ignored while the engine isn't running"
    eng.interval = 3600.0
    eng.start()
    try:
        assert not eng.handle_poke(sync_engine.make_poke("f" * 16, n0.device_id))       # other office
        assert not eng.handle_poke(sync_engine.make_poke(eng.office_tag, "e" * 32))    # not a member
        assert not eng.handle_poke(sync_engine.make_poke(eng.office_tag, n1.device_id))  # itself
        eng.poke_debounce = 30.0               # hold it so the due entry can be inspected
        assert eng.handle_poke(good) and eng.handle_poke(good)
        assert list(eng._poke_due) == [n0.device_id]
    finally:
        eng.stop()


def test_discovery_passes_pokes_to_the_engine():
    import sync_discovery
    got = []
    svc = sync_discovery.DiscoveryService(office_tag="a" * 16, device_id="b" * 32, device_name="PC",
                                          listen=False, on_poke=lambda data, ip: got.append((data, ip)))
    poke = sync_engine.make_poke("a" * 16, "c" * 32)
    svc.handle_datagram(poke, "10.0.0.5")
    assert got == [(poke, "10.0.0.5")]
    assert svc.get_beacon_sighting("c" * 32) is None


def test_seal_listener_is_told_about_local_changes(cluster):
    h = cluster(2)
    n0, _ = h.nodes
    seen = []
    n0.db.set_seal_listener(lambda results: seen.append({k: r.changes for k, r in results.items()}))
    _add_clients(n0, 2, "SL")
    assert seen and seen[-1]["master"] >= 2
    n0.write(lambda conn: conn.execute("SELECT 1"))
    assert len(seen) == 1                      # nothing sealed, nothing told


# ---------------------------------------------------------------- stuck streams

def test_a_stream_stuck_at_a_gap_is_reported_and_nothing_is_skipped(cluster):
    h = cluster(2)
    n0, n1 = h.nodes
    _add_clients(n0, 3, "G")
    assert n0.sync_with(n1).ok
    stream = _mstream(n0)
    at = _vector(n1, stream)
    with n1.open_db() as c:                    # a later change of n0's stream, the one before it missing
        c.execute("INSERT INTO _sync_changes(origin, origin_seq, hlc, tbl, row_key, op, data, sig) "
                  "VALUES (?, ?, '0000000000001.0000.x', 'clients', '[\"%s\"]', 'delete', NULL, NULL)"
                  % ("0" * 32), (stream, at + 2))
    now = time.monotonic()
    assert n1.engine._check_stalls(now) == []
    stalled = n1.engine._check_stalls(now + sync_engine.STALL_SECONDS + 1)
    assert [(s["stream"], s["waiting_for"], s["have_up_to"]) for s in stalled] == [(stream, at + 1, at + 2)]
    assert _events(n1, "stalled")[-1]["stream"] == stream
    n1.engine._check_stalls(now + sync_engine.STALL_SECONDS + 5)
    assert len(_events(n1, "stalled")) == 1    # reported once
    assert _vector(n1, stream) == at           # not skipped


# ---------------------------------------------------------------- module rules

def test_no_pyside6_or_heavy_top_level_imports():
    tree = ast.parse((ROOT / "sync_engine.py").read_text(encoding="utf-8"))
    top = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            top.add((node.module or "").split(".")[0])
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            assert not any(n.startswith("PySide6") for n in names)
    assert not top & {"cryptography", "sqlcipher3", "database", "sync_apply", "sync_admin"}
