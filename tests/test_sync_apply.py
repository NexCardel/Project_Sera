"""Tests for Sera Sync v3 P3-4: the apply engine (sync_apply.apply_batch).

Accept (blueprint §5 P3-4): P3-0 tests (b), (c), (d), (e), plus unit tests for each bullet.

The P3-0 tests in test_sync_convergence.py move changes through sync sessions, which only exist
from P3-5 on, so they still fail. The same scenarios run here with the changes handed straight
from one node's _sync_changes to the other's apply (owner decision 2026-09-25):
test_p30_b_*, test_p30_c_*, test_p30_d_*, test_p30_e_*.

Owner decisions covered here: newer re-create wins for natural/composite keys (2026-09-25) and
the client merge on the internal PK (2026-09-24, built in P3-4 on 2026-09-25).

tmp_path only (§0 rule 2). Invented test data only (§0 rule 12).
"""

import ast
import json
import logging
import time
from pathlib import Path

import pytest

import sync_apply
import sync_capture
import sync_schema
from tests.sync_harness import digest
from tests.test_sync_capture import PAN_A, PAN_B, _add_tracker, _col, _make_db, _service, _values

ROOT = Path(__file__).resolve().parent.parent
CHANGE_COLS = ["origin", "origin_seq", "hlc", "tbl", "row_key", "op", "data", "sig"]


# ---------------------------------------------------------------- nodes and hand-off

@pytest.fixture
def admin_key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    return Ed25519PrivateKey.generate()


@pytest.fixture
def admin_pub(admin_key):
    import sync_admin
    return sync_admin.public_key_b64(admin_key)


class Node:
    """A SeraDatabase in shadow mode plus the admin public key it trusts."""

    def __init__(self, path, admin_pub=None, signer=None):
        self.db = _make_db(path)
        self.db._admin_signer = lambda: signer
        self.admin_pub = admin_pub
        self.results = []

    def __getattr__(self, name):
        return getattr(self.db, name)


@pytest.fixture
def nodes(tmp_path, admin_key, admin_pub):
    made = []

    def make(n=2, admin_index=0):
        for i in range(n):
            made.append(Node(tmp_path / f"n{i}", admin_pub, admin_key if i == admin_index else None))
        return made
    yield make
    for n in made:
        n.db.stop_seal_timer()


def _vector(node, which):
    opener = node.db._connect if which == "master" else node.db._connect_raw
    with opener() as c:
        return dict(c.execute("SELECT origin, max_seq FROM _sync_vector").fetchall())


def _outgoing(node, which, after=None):
    """Changes in node's _sync_changes (received ones too, for forwarding), newer than `after`."""
    after = after or {}
    opener = node.db._connect if which == "master" else node.db._connect_raw
    with opener() as c:
        rows = c.execute(f"SELECT {', '.join(CHANGE_COLS)} FROM _sync_changes ORDER BY seq").fetchall()
    return [dict(zip(CHANGE_COLS, r)) for r in rows if r[1] > after.get(r[0], 0)]


def pull(dst, src, which=("master", "raw"), now_ms=None):
    """dst receives what src has and dst doesn't (what a P3-5 session will do)."""
    src.db.seal_pending()
    total = 0
    for w in which:
        changes = _outgoing(src, w, _vector(dst, w))
        if changes:
            r = dst.db.apply_changes(w, changes, admin_pubkey=dst.admin_pub, now_ms=now_ms)
            dst.results.append(r)
            total += len(changes) - r.skipped
    return total


def sync_all(*ns, rounds=6):
    for _ in range(rounds):
        moved = 0
        for a in ns:
            for b in ns:
                if a is not b:
                    moved += pull(a, b)
        if not moved:
            return
    raise AssertionError("nodes did not settle")


def _q1(node, sql, params=(), raw=False):
    opener = node.db._connect_raw if raw else node.db._connect
    with opener() as c:
        row = c.execute(sql, params).fetchone()
    return row[0] if row else None


def _rows(node, sql, params=(), raw=False):
    opener = node.db._connect_raw if raw else node.db._connect
    with opener() as c:
        return c.execute(sql, params).fetchall()


def _exec(node, sql, params=(), raw=False):
    opener = node.db._connect_raw if raw else node.db._connect
    with opener() as c:
        c.execute(sql, params)


def _client_id(node, gid):
    return _q1(node, "SELECT id FROM clients WHERE gid = ?", (gid,))


def _value(node, gid, label):
    return _q1(node, "SELECT cv.value FROM client_values cv JOIN clients c ON c.id = cv.client_id "
                     "WHERE c.gid = ? AND cv.column_id = ?", (gid, _col(node.db, label)))


def _add(node, pan, company="Invented Traders", services=()):
    cid = node.db.add_client({_col(node.db, "PAN"): pan, _col(node.db, "NAME OF COMPANY"): company},
                             "", list(services))
    return _q1(node, "SELECT gid FROM clients WHERE id = ?", (cid,))


def _conflicts(node, raw=False):
    return _rows(node, "SELECT tbl, row_key, col, kept, discarded, reason FROM _sync_conflicts", raw=raw)


def _flag(node, raw=False):
    return _q1(node, "SELECT value FROM _sync_flags WHERE name = 'applying'", raw=raw)


def _fake(origin, seq, hlc_ms, tbl, row_key, op="upsert", data=None, dev="f" * 32, sig=None):
    return {"origin": origin, "origin_seq": seq, "hlc": str(sync_capture.HLC(hlc_ms, 0, dev)),
            "tbl": tbl, "row_key": row_key, "op": op, "data": data, "sig": sig}


def _set_time(monkeypatch, t):
    monkeypatch.setattr(sync_capture, "_now_ms", lambda: int(t * 1000))


# ---------------------------------------------------------------- basics

def test_no_pyside6_import():
    tree = ast.parse((ROOT / "sync_apply.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any("PySide6" in a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert not (node.module and "PySide6" in node.module)


def test_new_client_arrives_with_values_and_nothing_echoes_back(nodes):
    a, b = nodes(2)
    gid = _add(a, PAN_A, services=[_service(a.db, "GST")])
    pull(b, a)
    assert _value(b, gid, "PAN") == PAN_A
    assert _q1(b, "SELECT COUNT(*) FROM client_services cs JOIN clients c ON c.id = cs.client_id "
                  "WHERE c.gid = ?", (gid,)) == 1
    assert digest(a.db) == digest(b.db)
    # Applied values carry the sealer's vhash, so b's next seal has nothing to send.
    res = b.db.seal_pending()
    assert res["master"].changes == 0 and res["raw"].changes == 0
    assert _q1(b, "SELECT COUNT(*) FROM _sync_pending") == 0


def test_insert_gets_its_own_local_id(nodes):
    a, b = nodes(2)
    _add(b, PAN_B)                               # b's next local id differs from a's
    gid = _add(a, PAN_A)
    pull(b, a)
    assert _client_id(b, gid) != _client_id(a, gid)
    assert digest(a.db) != digest(b.db)          # b also has its own client
    pull(a, b)
    assert digest(a.db) == digest(b.db)


def test_applying_flag_is_zero_after_success_and_after_error(nodes):
    a, b = nodes(2)
    _add(a, PAN_A)
    pull(b, a)
    assert _flag(b) == 0 and _flag(b, raw=True) == 0
    _exec(b, "CREATE TRIGGER boom BEFORE INSERT ON clients BEGIN SELECT RAISE(ABORT, 'boom'); END;")
    _add(a, PAN_B)
    with pytest.raises(Exception):
        pull(b, a)
    assert _flag(b) == 0


def test_changes_applied_are_not_captured(nodes):
    a, b = nodes(2)
    gid = _add(a, PAN_A)
    pull(b, a)
    a.db.delete_client(_client_id(a, gid))       # cascades on b too
    pull(b, a)
    assert _client_id(b, gid) is None
    assert _q1(b, "SELECT COUNT(*) FROM _sync_pending") == 0
    assert _q1(b, "SELECT COUNT(*) FROM _sync_changes WHERE origin = ?",
               (_q1(b, "SELECT value FROM _sync_meta WHERE key='stream_id'"),)) == 0


def test_idempotent_second_apply_skips_everything(nodes):
    a, b = nodes(2)
    _add(a, PAN_A)
    a.db.seal_pending()
    changes = _outgoing(a, "master")
    r1 = b.db.apply_changes("master", changes, admin_pubkey=b.admin_pub)
    d1 = digest(b.db)
    r2 = b.db.apply_changes("master", changes, admin_pubkey=b.admin_pub)
    assert r1.applied == len(changes)
    assert r2.skipped == len(changes) and r2.applied == 0
    assert digest(b.db) == d1


def test_batch_is_sorted_by_hlc(nodes):
    a, b = nodes(2)
    gid = _add(a, PAN_A)
    a.db.seal_pending()
    changes = list(reversed(_outgoing(a, "master")))   # children before their client
    r = b.db.apply_changes("master", changes, admin_pubkey=b.admin_pub)
    assert r.parked == 0 and r.applied == len(changes)
    assert _value(b, gid, "PAN") == PAN_A


def test_changes_are_recorded_unchanged_for_forwarding(nodes):
    a, b = nodes(2)
    _add(a, PAN_A)
    a.db.seal_pending()
    sent = _outgoing(a, "master")
    pull(b, a)
    got = {(c["origin"], c["origin_seq"]): c for c in _outgoing(b, "master")}
    for c in sent:
        k = got[(c["origin"], c["origin_seq"])]
        assert (k["hlc"], k["tbl"], k["row_key"], k["op"], k["sig"]) == \
               (c["hlc"], c["tbl"], c["row_key"], c["op"], c["sig"])
        assert json.loads(k["data"] or "null") == json.loads(c["data"] or "null")


def test_forwarding_a_to_c_through_b(nodes):
    """P3-0 (f) shape: A and C never talk; B carries both ways."""
    a, b, c = nodes(3)
    ga = _add(a, PAN_A)
    gc = _add(c, PAN_B)
    pull(b, a)
    pull(b, c)
    pull(c, b)
    pull(a, b)
    assert _client_id(c, ga) and _client_id(a, gc)
    assert digest(a.db) == digest(b.db) == digest(c.db)


def test_vector_advances_only_while_contiguous(nodes):
    a, b = nodes(2)
    for pan in ("AAAAA0001A", "AAAAA0002A", "AAAAA0003A"):
        _add(a, pan)
    a.db.seal_pending()
    changes = _outgoing(a, "master")
    origin = changes[0]["origin"]
    seqs = sorted(c["origin_seq"] for c in changes)
    gap = seqs[len(seqs) // 2]
    first = [c for c in changes if c["origin_seq"] != gap]
    b.db.apply_changes("master", first, admin_pubkey=b.admin_pub)
    assert _vector(b, "master")[origin] == gap - 1
    b.db.apply_changes("master", [c for c in changes if c["origin_seq"] == gap], admin_pubkey=b.admin_pub)
    assert _vector(b, "master")[origin] == seqs[-1]


def test_observe_moves_the_local_clock_past_remote_changes(nodes, monkeypatch):
    a, b = nodes(2)
    now = time.time()
    _set_time(monkeypatch, now + 600)            # a is 10 minutes ahead (under the 1 h guard)
    gid = _add(a, PAN_A)
    a.db.seal_pending()
    remote = max(c["hlc"] for c in _outgoing(a, "master"))
    _set_time(monkeypatch, now)
    pull(b, a)
    assert _q1(b, "SELECT value FROM _sync_meta WHERE key='last_hlc'") > remote
    b.db.update_client(_client_id(b, gid), _values(b.db, _client_id(b, gid), **{"NAME OF COMPANY": "Renamed Co"}),
                       "", [])
    b.db.seal_pending()
    mine = [c for c in _outgoing(b, "master") if c["origin"].startswith(b.db.get_sync_device_id())]
    assert mine and min(c["hlc"] for c in mine) > remote


def test_clock_ahead_is_parked_and_the_clock_does_not_move(nodes):
    _a, b = nodes(2)
    now_ms = int(time.time() * 1000)
    before = _q1(b, "SELECT value FROM _sync_meta WHERE key='last_hlc'")
    ch = _fake("e" * 32 + ":m", 1, now_ms + 2 * 3_600_000, "app_settings", '["invented_key"]',
               data={"value": "x"})
    r = b.db.apply_changes("master", [ch], admin_pubkey=b.admin_pub, now_ms=now_ms)
    assert r.parked == 1 and r.clock_ahead and r.clock_ahead[0][1] >= 2 * 3_600_000 - 1
    assert _q1(b, "SELECT value FROM app_settings WHERE key='invented_key'") is None
    assert _q1(b, "SELECT value FROM _sync_meta WHERE key='last_hlc'") == before
    assert _q1(b, "SELECT reason FROM _sync_parked") == "clock_ahead"
    # Recorded (the vector moves) and applied once this PC's clock has caught up.
    assert _vector(b, "master")["e" * 32 + ":m"] == 1
    later = now_ms + 2 * 3_600_000
    r = sync_apply.retry_parked(b.db.db_path, b.db.hex_key, "master", now_ms=later)
    assert r.unparked == 1
    assert _q1(b, "SELECT value FROM app_settings WHERE key='invented_key'") == "x"


def test_malformed_change_rejects_the_whole_batch(nodes):
    _a, b = nodes(2)
    good = _fake("e" * 32 + ":m", 1, int(time.time() * 1000), "app_settings", '["k1"]', data={"value": "1"})
    bad = dict(good, origin_seq=2, row_key="not json")
    with pytest.raises(sync_apply.ApplyError):
        b.db.apply_changes("master", [good, bad], admin_pubkey=b.admin_pub)
    assert _q1(b, "SELECT value FROM app_settings WHERE key='k1'") is None


def test_pending_local_edit_is_sealed_before_applying(nodes):
    """A local delete not sealed yet must not be undone by an incoming edit of that row."""
    a, b = nodes(2)
    gid = _add(a, PAN_A)
    pull(b, a)
    a.db.update_client_notes(_client_id(a, gid), "edited on a")
    a.db.seal_pending()
    # A raw write on b (another process): only the timer would seal it.
    conn = sync_capture._open(b.db.db_path, b.db.hex_key, 5.0)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("DELETE FROM clients WHERE gid = ?", (gid,))
    conn.close()
    assert _q1(b, "SELECT COUNT(*) FROM _sync_pending") > 0
    r = b.db.apply_changes("master", _outgoing(a, "master", _vector(b, "master")), admin_pubkey=b.admin_pub)
    assert r.sealed >= 1
    assert _client_id(b, gid) is None
    pull(a, b)
    assert _client_id(a, gid) is None
    assert digest(a.db) == digest(b.db)


# ---------------------------------------------------------------- P3-0 scenarios, handed over directly

def test_p30_b_same_field_higher_hlc_wins(nodes, monkeypatch):
    a, b = nodes(2)
    gid = _add(a, PAN_A)
    pull(b, a)
    now = time.time()
    _set_time(monkeypatch, now)
    a.db.update_client_notes(_client_id(a, gid), "a edit (loses)")
    _set_time(monkeypatch, now + 1000)
    b.db.update_client_notes(_client_id(b, gid), "b edit (wins)")
    sync_all(a, b)
    for n in (a, b):
        assert _q1(n, "SELECT notes FROM clients WHERE gid = ?", (gid,)) == "b edit (wins)"
    assert digest(a.db) == digest(b.db)


def test_p30_c_different_fields_both_survive(nodes):
    a, b = nodes(2)
    gid = _add(a, PAN_A)
    pull(b, a)
    a.db.update_client_single_field(_client_id(a, gid), _col(a.db, "NAME OF COMPANY"), "Name From A")
    b.db.update_client_single_field(_client_id(b, gid), _col(b.db, "GSTIN"), "27ABCDE1234F1Z5")
    b.db.update_client_notes(_client_id(b, gid), "note from b")
    sync_all(a, b)
    for n in (a, b):
        assert _value(n, gid, "NAME OF COMPANY") == "Name From A"
        assert _value(n, gid, "GSTIN") == "27ABCDE1234F1Z5"
        assert _q1(n, "SELECT notes FROM clients WHERE gid = ?", (gid,)) == "note from b"
    assert digest(a.db) == digest(b.db)


def test_p30_d_delete_wins_over_concurrent_edit_with_conflict_row(nodes):
    a, b = nodes(2)
    gid = _add(a, PAN_A)
    pull(b, a)
    # A plain DELETE, as in P3-0 (d). delete_client() also writes an audit row that keeps the
    # deleted client's local id on this PC only (an F13 id leak, noted for P3-6).
    _exec(a, "DELETE FROM clients WHERE gid = ?", (gid,))
    b.db.update_client_notes(_client_id(b, gid), "edit on b after the delete")
    sync_all(a, b)
    for n in (a, b):
        assert _client_id(n, gid) is None
    assert any(r[0] == "clients" and r[5] == "edit after delete discarded" for r in _conflicts(a))
    assert any(r[0] == "clients" and r[5] == "edit discarded by delete" for r in _conflicts(b))
    assert digest(a.db) == digest(b.db)


def test_p30_e_crash_mid_batch_leaves_nothing_and_resync_completes(nodes):
    a, b = nodes(2)
    for i in range(5):
        _exec(a, "INSERT INTO clients (notes, is_archived, created_at, updated_at, client_id_token) "
                 "VALUES ('Batch client', 0, '2026-09-25T10:00:00Z', '2026-09-25T10:00:00Z', ?)",
              (f"BATCH-{i:05d}",))
    a.db.seal_pending()
    _exec(b, "CREATE TRIGGER inject_crash_mid_batch BEFORE INSERT ON clients "
             "WHEN (SELECT count(*) FROM clients WHERE client_id_token LIKE 'BATCH-%') >= 3 "
             "BEGIN SELECT RAISE(ABORT, 'injected crash in middle of applying batch'); END;")
    before_vec = _vector(b, "master")
    before_changes = _q1(b, "SELECT COUNT(*) FROM _sync_changes")
    with pytest.raises(Exception, match="injected crash"):
        pull(b, a)
    assert _q1(b, "SELECT COUNT(*) FROM clients WHERE client_id_token LIKE 'BATCH-%'") == 0
    assert _vector(b, "master") == before_vec
    assert _q1(b, "SELECT COUNT(*) FROM _sync_changes") == before_changes
    assert _q1(b, "SELECT COUNT(*) FROM _sync_parked") == 0
    assert _flag(b) == 0
    _exec(b, "DROP TRIGGER inject_crash_mid_batch")
    pull(b, a)
    assert _q1(b, "SELECT COUNT(*) FROM clients WHERE client_id_token LIKE 'BATCH-%'") == 5
    assert digest(a.db) == digest(b.db)


# ---------------------------------------------------------------- admin scope

def test_signed_staff_change_is_applied(nodes):
    a, b = nodes(2)                              # a holds the admin key
    a.db.rename_staff_user("User 2", "Invented Staff") if hasattr(a.db, "rename_staff_user") else \
        _exec(a, "UPDATE staff_users SET name = 'Invented Staff' WHERE name = 'User 2'")
    pull(b, a)
    assert _q1(b, "SELECT COUNT(*) FROM staff_users WHERE name = 'Invented Staff'") == 1
    assert digest(a.db) == digest(b.db)


def test_bad_or_missing_admin_signature_is_dropped_and_logged(nodes, caplog, admin_key):
    a, b = nodes(2)
    _exec(a, "UPDATE staff_users SET name = 'Invented Staff' WHERE name = 'User 2'")
    a.db.seal_pending()
    ch = [c for c in _outgoing(a, "master") if c["tbl"] == "staff_users"][0]
    tampered = dict(ch, data=json.dumps({"name": "Forged Name", "alias": None}))
    unsigned = dict(ch, sig=None)
    with caplog.at_level(logging.WARNING, logger="sera.sync.apply"):
        r = b.db.apply_changes("master", [tampered], admin_pubkey=b.admin_pub)
        r2 = b.db.apply_changes("master", [unsigned], admin_pubkey=b.admin_pub)
    assert r.rejected == 1 and r2.rejected == 1
    assert "signature" in caplog.text
    assert _q1(b, "SELECT COUNT(*) FROM staff_users WHERE name IN ('Forged Name', 'Invented Staff')") == 0
    # Not recorded, so the genuine change can still take that (origin, origin_seq).
    r3 = b.db.apply_changes("master", [ch], admin_pubkey=b.admin_pub)
    assert r3.applied == 1
    assert _q1(b, "SELECT COUNT(*) FROM staff_users WHERE name = 'Invented Staff'") == 1


def test_admin_change_without_a_known_admin_key_is_rejected(nodes):
    a, b = nodes(2)
    _exec(a, "UPDATE staff_users SET name = 'Invented Staff' WHERE name = 'User 2'")
    a.db.seal_pending()
    changes = [c for c in _outgoing(a, "master") if c["tbl"] == "staff_users"]
    b.db._office_admin_pubkey = lambda: None
    r = b.db.apply_changes("master", changes)
    assert r.rejected == len(changes)


# ---------------------------------------------------------------- missing parents, parking

def test_missing_parent_is_parked_then_applied(nodes):
    a, b = nodes(2)
    gid = _add(a, PAN_A)
    a.db.seal_pending()
    changes = _outgoing(a, "master")
    parent = [c for c in changes if c["tbl"] == "clients"]
    children = [c for c in changes if c["tbl"] != "clients"]
    r = b.db.apply_changes("master", children, admin_pubkey=b.admin_pub)
    assert r.parked == len(children)             # client_values and the audit row need the client
    reasons = {x for (x,) in _rows(b, "SELECT reason FROM _sync_parked")}
    assert reasons == {"missing_parent"}
    r = b.db.apply_changes("master", parent, admin_pubkey=b.admin_pub)
    assert r.unparked == len(children)
    assert _q1(b, "SELECT COUNT(*) FROM _sync_parked") == 0
    assert _value(b, gid, "PAN") == PAN_A
    assert digest(a.db) == digest(b.db)


def test_later_change_of_a_parked_row_waits_behind_it(nodes):
    a, b = nodes(2)
    gid = _add(a, PAN_A)
    a.db.seal_pending()
    first = _outgoing(a, "master")
    a.db.update_client_single_field(_client_id(a, gid), _col(a.db, "PAN"), "ABCDE1234G")
    a.db.seal_pending()
    second = _outgoing(a, "master", _vector(a, "master") and {c["origin"]: max(x["origin_seq"] for x in first)
                                                              for c in first})
    pan_rows = [c for c in first + second if c["tbl"] == "client_values"
                and json.loads(c["row_key"])[1] == _q1(a, "SELECT gid FROM mcl_columns WHERE label='PAN'")]
    assert len(pan_rows) == 2
    b.db.apply_changes("master", [pan_rows[0]], admin_pubkey=b.admin_pub)
    r = b.db.apply_changes("master", [pan_rows[1]], admin_pubkey=b.admin_pub)
    assert r.parked == 1
    assert _q1(b, "SELECT reason FROM _sync_parked WHERE id = (SELECT MAX(id) FROM _sync_parked)") == "behind_parked"
    pull(b, a)
    assert _value(b, gid, "PAN") == "ABCDE1234G"
    assert _q1(b, "SELECT COUNT(*) FROM _sync_parked") == 0


def test_edit_before_create_from_another_stream_is_parked_as_incomplete(nodes):
    a, b, c = nodes(3)
    gid = _add(a, PAN_A)
    pull(b, a)
    b.db.update_client_notes(_client_id(b, gid), "note from b")
    b.db.seal_pending()
    b_stream = _q1(b, "SELECT value FROM _sync_meta WHERE key='stream_id'")
    only_b = [x for x in _outgoing(b, "master") if x["origin"] == b_stream]
    r = c.db.apply_changes("master", only_b, admin_pubkey=c.admin_pub)
    assert r.parked == 1
    assert _q1(c, "SELECT reason FROM _sync_parked") == "incomplete_row"
    pull(c, a)
    assert _q1(c, "SELECT notes FROM clients WHERE gid = ?", (gid,)) == "note from b"
    assert digest(b.db) == digest(c.db)


def test_parked_changes_surface_after_seven_days(nodes):
    import datetime
    a, b = nodes(2)
    _add(a, PAN_A)
    a.db.seal_pending()
    b.db.apply_changes("master", [c for c in _outgoing(a, "master") if c["tbl"] == "client_values"],
                       admin_pubkey=b.admin_pub)
    now = datetime.datetime.now(datetime.timezone.utc)
    assert sync_apply.parked_changes(b.db.db_path, b.db.hex_key, sync_apply.PARK_SURFACE_SECONDS, now=now) == []
    old = sync_apply.parked_changes(b.db.db_path, b.db.hex_key, sync_apply.PARK_SURFACE_SECONDS,
                                    now=now + datetime.timedelta(days=7, minutes=1))
    assert old and {p["reason"] for p in old} == {"missing_parent"}
    assert all(set(p) == {"id", "tbl", "row_key", "origin", "origin_seq", "reason", "first_at", "tries"}
               for p in old)          # no values (§0 rule 12)
    tries = old[0]["tries"]
    sync_apply.retry_parked(b.db.db_path, b.db.hex_key, "master")
    assert sync_apply.parked_changes(b.db.db_path, b.db.hex_key)[0]["tries"] == tries + 1


def test_tracker_row_waits_for_its_client_across_databases(nodes):
    a, b = nodes(2)
    gid = _add(a, PAN_A)
    dump = _add_tracker(a.db, client_id=_client_id(a, gid), service_id=_service(a.db, "GST"))
    a.db.seal_pending()
    r = b.db.apply_changes("raw", _outgoing(a, "raw"), admin_pubkey=b.admin_pub)
    assert r.parked == 1
    pull(b, a, which=("master",))             # the client arrives; raw's parked row is retried
    assert _q1(b, "SELECT COUNT(*) FROM _sync_parked", raw=True) == 0
    tgid = _q1(a, "SELECT gid FROM tracker_dump WHERE id = ?", (dump,), raw=True)
    assert _q1(b, "SELECT client_id FROM tracker_dump WHERE gid = ?", (tgid,), raw=True) == _client_id(b, gid)
    assert digest(a.db) == digest(b.db)


# ---------------------------------------------------------------- LWW and FK translation

def test_older_column_change_does_not_overwrite_a_newer_one(nodes, monkeypatch):
    a, b = nodes(2)
    gid = _add(a, PAN_A)
    pull(b, a)
    now = time.time()
    _set_time(monkeypatch, now + 50)
    b.db.update_client_notes(_client_id(b, gid), "newer on b")
    _set_time(monkeypatch, now + 10)
    a.db.update_client_notes(_client_id(a, gid), "older on a")
    pull(b, a)
    assert _q1(b, "SELECT notes FROM clients WHERE gid = ?", (gid,)) == "newer on b"
    pull(a, b)
    assert _q1(a, "SELECT notes FROM clients WHERE gid = ?", (gid,)) == "newer on b"


def test_fk_columns_are_translated_to_local_ids(nodes):
    a, b = nodes(2)
    _add(b, PAN_B)
    col = a.db.create_mcl_column("Invented Login", "text")
    sid = a.db.create_service("Invented Portal", "https://example.invalid", col, None, "", "",
                              "extension") if a.db.create_service.__code__.co_argcount > 4 else None
    if sid is None:
        _exec(a, "INSERT INTO services (name, userid_column_id) VALUES ('Invented Portal', ?)", (col,))
    sync_all(a, b)
    bcol = _q1(b, "SELECT id FROM mcl_columns WHERE label = 'Invented Login'")
    assert _q1(b, "SELECT userid_column_id FROM services WHERE name = 'Invented Portal'") == bcol
    assert digest(a.db) == digest(b.db)
    assert b.db.seal_pending()["master"].changes == 0


def test_cell_formatting_keys_numeric_and_services_literal(nodes):
    a, b = nodes(2)
    gid = _add(a, PAN_A)
    cid = _client_id(a, gid)
    a.db.bulk_set_cell_formatting([
        {"client_id": cid, "column_key": str(_col(a.db, "PAN")), "bg_color": "#112233", "fg_color": ""},
        {"client_id": cid, "column_key": "services", "bg_color": "#445566", "fg_color": ""}])
    pull(b, a)
    rows = dict(_rows(b, "SELECT column_key, bg_color FROM cell_formatting WHERE client_id = ?",
                      (_client_id(b, gid),)))
    assert rows == {str(_col(b.db, "PAN")): "#112233", "services": "#445566"}
    assert digest(a.db) == digest(b.db)


def test_append_row_is_inserted_once(nodes):
    a, b = nodes(2)
    a.db.log_action("Tester", "invented-action", detail="first")
    a.db.seal_pending()
    audit = [c for c in _outgoing(a, "master") if c["tbl"] == "audit_log"]
    b.db.apply_changes("master", audit, admin_pubkey=b.admin_pub)
    again = [dict(c, origin_seq=c["origin_seq"] + 1000) for c in audit]   # same gid, other seq
    b.db.apply_changes("master", again, admin_pubkey=b.admin_pub)
    gid = json.loads(audit[0]["row_key"])[0]
    assert _q1(b, "SELECT COUNT(*) FROM audit_log WHERE gid = ?", (gid,)) == 1


# ---------------------------------------------------------------- tombstones

def test_gid_row_upsert_after_its_delete_is_dropped(nodes):
    a, b = nodes(2)
    gid = _add(a, PAN_A)
    pull(b, a)
    b.db.update_client_notes(_client_id(b, gid), "older edit on b")
    b.db.seal_pending()
    a.db.delete_client(_client_id(a, gid))
    pull(a, b)                                   # older edit meets a's newer tombstone
    assert _client_id(a, gid) is None
    assert not [r for r in _conflicts(a) if r[0] == "clients"]   # older than the delete: no conflict


def test_natural_key_cleared_then_refilled_reaches_other_pcs(nodes):
    """Owner decision 2026-09-25: a newer re-create wins over the tombstone."""
    a, b = nodes(2)
    gid = _add(a, PAN_A)
    a.db.update_client_single_field(_client_id(a, gid), _col(a.db, "GSTIN"), "27ABCDE1234F1Z5")
    pull(b, a)
    cid = _client_id(a, gid)
    a.db.update_client(cid, _values(a.db, cid, GSTIN=None), "", [])        # cleared -> deleted row
    pull(b, a)
    assert _value(b, gid, "GSTIN") is None
    a.db.update_client_single_field(cid, _col(a.db, "GSTIN"), "27ABCDE1234F1Z6")
    pull(b, a)
    assert _value(b, gid, "GSTIN") == "27ABCDE1234F1Z6"
    assert digest(a.db) == digest(b.db)


def test_natural_key_concurrent_clear_and_newer_edit_converge(nodes, monkeypatch):
    a, b = nodes(2)
    gid = _add(a, PAN_A)
    a.db.update_client_single_field(_client_id(a, gid), _col(a.db, "GSTIN"), "27ABCDE1234F1Z5")
    pull(b, a)
    now = time.time()
    _set_time(monkeypatch, now + 10)
    cid = _client_id(a, gid)
    a.db.update_client(cid, _values(a.db, cid, GSTIN=None), "", [])        # older delete
    _set_time(monkeypatch, now + 20)
    b.db.update_client_single_field(_client_id(b, gid), _col(b.db, "GSTIN"), "NEWER VALUE")
    sync_all(a, b)
    for n in (a, b):
        assert _value(n, gid, "GSTIN") == "NEWER VALUE"
    assert digest(a.db) == digest(b.db)


def test_natural_key_newer_delete_beats_older_edit(nodes, monkeypatch):
    a, b = nodes(2)
    gid = _add(a, PAN_A)
    a.db.update_client_single_field(_client_id(a, gid), _col(a.db, "GSTIN"), "27ABCDE1234F1Z5")
    pull(b, a)
    now = time.time()
    _set_time(monkeypatch, now + 10)
    b.db.update_client_single_field(_client_id(b, gid), _col(b.db, "GSTIN"), "OLDER EDIT")
    _set_time(monkeypatch, now + 20)
    cid = _client_id(a, gid)
    a.db.update_client(cid, _values(a.db, cid, GSTIN=None), "", [])
    sync_all(a, b)
    for n in (a, b):
        assert _value(n, gid, "GSTIN") is None
    assert digest(a.db) == digest(b.db)


def test_partial_row_losing_delete_converges(nodes, monkeypatch):
    """cell_formatting has several columns: a delete older than one column's edit keeps the row,
    resets the older columns, and both orders give the same row."""
    a, b = nodes(2)
    gid = _add(a, PAN_A)
    cid = _client_id(a, gid)
    key = str(_col(a.db, "PAN"))
    a.db.bulk_set_cell_formatting([{"client_id": cid, "column_key": key, "bg_color": "#111111",
                                    "fg_color": "#222222"}])
    pull(b, a)
    now = time.time()
    _set_time(monkeypatch, now + 10)
    a.db.clear_cell_formatting([(cid, key)])
    _set_time(monkeypatch, now + 20)
    _exec(b, "UPDATE cell_formatting SET bg_color = '#333333' WHERE client_id = ? AND column_key = ?",
          (_client_id(b, gid), str(_col(b.db, "PAN"))))
    sync_all(a, b)
    for n in (a, b):
        rows = _rows(n, "SELECT bg_color, fg_color FROM cell_formatting WHERE client_id = ?", (_client_id(n, gid),))
        assert rows and rows[0][0] == "#333333"
    assert digest(a.db) == digest(b.db)


def test_set_mode_add_and_remove_row_level_lww(nodes, monkeypatch):
    a, b = nodes(2)
    gst = _service(a.db, "GST")
    gid = _add(a, PAN_A, services=[gst])
    pull(b, a)
    now = time.time()
    _set_time(monkeypatch, now + 10)
    a.db.update_client(_client_id(a, gid), _values(a.db, _client_id(a, gid)), "", [])      # remove
    _set_time(monkeypatch, now + 20)
    _exec(b, "DELETE FROM client_services WHERE client_id = ?", (_client_id(b, gid),))
    _exec(b, "INSERT INTO client_services (client_id, service_id) VALUES (?, ?)",
          (_client_id(b, gid), _service(b.db, "GST")))                                       # newer re-add
    sync_all(a, b)
    for n in (a, b):
        assert _q1(n, "SELECT COUNT(*) FROM client_services WHERE client_id = ?", (_client_id(n, gid),)) == 1
    _set_time(monkeypatch, now + 30)
    a.db.update_client(_client_id(a, gid), _values(a.db, _client_id(a, gid)), "", [])      # newest: remove
    sync_all(a, b)
    for n in (a, b):
        assert _q1(n, "SELECT COUNT(*) FROM client_services WHERE client_id = ?", (_client_id(n, gid),)) == 0
    assert digest(a.db) == digest(b.db)


def test_app_setting_deleted_and_set_again(nodes):
    a, b = nodes(2)
    a.db.set_setting("invented_key", "one")
    pull(b, a)
    _exec(a, "DELETE FROM app_settings WHERE key = 'invented_key'")
    pull(b, a)
    assert _q1(b, "SELECT value FROM app_settings WHERE key = 'invented_key'") is None
    a.db.set_setting("invented_key", "two")
    pull(b, a)
    assert _q1(b, "SELECT value FROM app_settings WHERE key = 'invented_key'") == "two"


def test_delete_of_a_row_this_pc_never_had_leaves_a_tombstone(nodes):
    a, b, c = nodes(3)
    gid = _add(a, PAN_A)
    pull(b, a)
    _exec(b, "DELETE FROM clients WHERE gid = ?", (gid,))
    b.db.seal_pending()
    b_stream = _q1(b, "SELECT value FROM _sync_meta WHERE key='stream_id'")
    c.db.apply_changes("master", [x for x in _outgoing(b, "master") if x["origin"] == b_stream],
                       admin_pubkey=c.admin_pub)
    pull(c, a)                                   # the create arrives after the delete
    assert _client_id(c, gid) is None
    assert digest(a.db) != digest(c.db)          # a hasn't seen the delete yet
    pull(a, b)
    assert digest(a.db) == digest(c.db)


# ---------------------------------------------------------------- natural-key merge (services / staff)

def test_same_service_created_on_two_pcs_merges_to_the_smaller_gid(nodes):
    a, b = nodes(2)
    _exec(a, "INSERT INTO services (name, login_page_link) VALUES ('Invented Portal', 'https://a.invalid')")
    _exec(b, "INSERT INTO services (name, login_page_link) VALUES ('Invented Portal', 'https://b.invalid')")
    ga = _q1(a, "SELECT gid FROM services WHERE name = 'Invented Portal'")
    gb = _q1(b, "SELECT gid FROM services WHERE name = 'Invented Portal'")
    gid = _add(a, PAN_A, services=[_service(a.db, "Invented Portal")])
    sync_all(a, b)
    winner = min(ga, gb)
    for n in (a, b):
        assert _rows(n, "SELECT gid FROM services WHERE name = 'Invented Portal'") == [(winner,)]
        assert _q1(n, "SELECT gid FROM _sync_alias WHERE alias_gid = ?", (max(ga, gb),)) == winner
        assert _q1(n, "SELECT COUNT(*) FROM client_services cs JOIN services s ON s.id = cs.service_id "
                      "JOIN clients c ON c.id = cs.client_id WHERE s.name = 'Invented Portal' AND c.gid = ?",
                   (gid,)) == 1
    assert digest(a.db) == digest(b.db)
    # A later change that still names the losing gid lands on the winner.
    loser_node = a if ga > gb else b
    other = b if loser_node is a else a
    sid = _service(loser_node.db, "Invented Portal")
    _exec(loser_node, "UPDATE services SET arn_selector = '#arn' WHERE id = ?", (sid,))
    sync_all(a, b)
    assert _q1(other, "SELECT arn_selector FROM services WHERE name = 'Invented Portal'") == "#arn"


def test_rename_onto_an_existing_name_merges_instead_of_failing(nodes):
    a, b = nodes(2)
    _exec(a, "INSERT INTO services (name) VALUES ('Old Invented')")
    pull(b, a)
    _exec(a, "UPDATE services SET name = 'New Invented' WHERE name = 'Old Invented'")
    _exec(b, "INSERT INTO services (name) VALUES ('New Invented')")
    sync_all(a, b)
    for n in (a, b):
        assert _q1(n, "SELECT COUNT(*) FROM services WHERE name IN ('Old Invented', 'New Invented')") == 1
    assert digest(a.db) == digest(b.db)


def test_same_staff_name_on_two_pcs_merges(nodes, admin_key):
    a, b = nodes(2)
    b.db._admin_signer = lambda: admin_key       # e.g. admin handed over while apart
    _exec(a, "INSERT INTO staff_users (name) VALUES ('Invented Person')")
    _exec(b, "INSERT INTO staff_users (name) VALUES ('Invented Person')")
    sync_all(a, b)
    for n in (a, b):
        assert _q1(n, "SELECT COUNT(*) FROM staff_users WHERE name = 'Invented Person'") == 1
    assert digest(a.db) == digest(b.db)


# ---------------------------------------------------------------- client merge on the internal PK

def test_same_pan_added_on_two_pcs_becomes_one_client(nodes):
    a, b = nodes(2)
    ga = _add(a, PAN_A, company="Company From A", services=[_service(a.db, "GST")])
    gb = _add(b, PAN_A.lower() + " ", company="Company From B", services=[_service(b.db, "Income Tax")])
    tok_a = _q1(a, "SELECT client_id_token FROM clients WHERE gid = ?", (ga,))
    tok_b = _q1(b, "SELECT client_id_token FROM clients WHERE gid = ?", (gb,))
    b.db.update_client_single_field(_client_id(b, gb), _col(b.db, "GSTIN"), "27ABCDE1234F1Z5")
    dump = _add_tracker(b.db, client_id=_client_id(b, gb))
    sync_all(a, b)
    winner, loser = min(ga, gb), max(ga, gb)
    win_tok, lose_tok = (tok_a, tok_b) if winner == ga else (tok_b, tok_a)
    tgid = _q1(b, "SELECT gid FROM tracker_dump WHERE id = ?", (dump,), raw=True)
    for n in (a, b):
        assert _rows(n, "SELECT gid FROM clients") == [(winner,)]
        assert _q1(n, "SELECT client_id_token FROM clients") == win_tok
        assert _q1(n, "SELECT gid FROM _sync_alias WHERE alias_gid = ?", (loser,)) == winner
        assert _value(n, winner, "GSTIN") == "27ABCDE1234F1Z5"
        svc = {s for (s,) in _rows(n, "SELECT s.name FROM client_services cs JOIN services s ON s.id = cs.service_id")}
        assert svc == {"GST", "Income Tax"}
        assert _q1(n, "SELECT client_id FROM tracker_dump WHERE gid = ?", (tgid,), raw=True) == _client_id(n, winner)
        merged = _rows(n, "SELECT detail, client_id FROM audit_log WHERE action = ?", (sync_apply.MERGE_ACTION,))
        assert merged == [(f"Duplicate client {lose_tok} merged (same internal PK)", _client_id(n, winner))]
        assert not _conflicts(n)
    assert digest(a.db) == digest(b.db)


def test_edit_to_merged_away_client_is_redirected(nodes):
    a, b, c = nodes(3)
    ga = _add(a, PAN_A, company="From A")
    gb = _add(b, PAN_A, company="From B")
    loser = max(ga, gb)
    holder = b if loser == gb else a
    pull(c, holder)                              # c only knows the losing copy for now
    cid = _client_id(c, loser)
    assert cid is not None
    c.db.update_client_notes(cid, "edited on c before it heard of the merge")
    sync_all(a, b, c)
    winner = min(ga, gb)
    for n in (a, b, c):
        assert _rows(n, "SELECT gid FROM clients") == [(winner,)]
        assert _q1(n, "SELECT notes FROM clients") == "edited on c before it heard of the merge"
        assert not [r for r in _conflicts(n) if r[0] == "clients"]
    assert digest(a.db) == digest(b.db) == digest(c.db)


def test_archived_client_with_the_same_pan_is_not_merged(nodes):
    a, b = nodes(2)
    ga = _add(a, PAN_A)
    a.db.archive_client(_client_id(a, ga))
    gb = _add(b, PAN_A)
    sync_all(a, b)
    for n in (a, b):
        assert {g for (g,) in _rows(n, "SELECT gid FROM clients")} == {ga, gb}
    assert digest(a.db) == digest(b.db)


def test_three_way_merge_is_the_same_whatever_the_order(nodes):
    a, b, c = nodes(3)
    gids = [_add(n, PAN_B, company=f"From {i}") for i, n in enumerate((a, b, c))]
    pull(a, b)                                   # a merges a+b first, c comes later
    pull(a, c)
    pull(c, b)                                   # c merges b+c first
    sync_all(a, b, c)
    for n in (a, b, c):
        assert _rows(n, "SELECT gid FROM clients") == [(min(gids),)]
        assert _q1(n, "SELECT COUNT(*) FROM audit_log WHERE action = ?", (sync_apply.MERGE_ACTION,)) == 2
    assert digest(a.db) == digest(b.db) == digest(c.db)


def test_raw_repoint_job_survives_until_rawpayload_commits(nodes, monkeypatch):
    a, b = nodes(2)
    ga = _add(a, PAN_A)
    gb = _add(b, PAN_A)
    loser, winner = max(ga, gb), min(ga, gb)
    holder = a if loser == ga else b
    receiver = b if holder is a else a
    dump = _add_tracker(holder.db, client_id=_client_id(holder, loser))
    real = sync_apply.run_raw_repoints
    monkeypatch.setattr(sync_apply, "run_raw_repoints", lambda *a, **k: 0)
    pull(holder, receiver)                       # holder merges; raw re-point "crashes"
    assert json.loads(_q1(holder, "SELECT value FROM _sync_meta WHERE key = ?", (sync_apply.REPOINT_META_KEY,)))
    monkeypatch.setattr(sync_apply, "run_raw_repoints", real)
    assert sync_apply.run_raw_repoints(holder.db.db_path, holder.db.raw_db_path, holder.db.hex_key) >= 1
    assert json.loads(_q1(holder, "SELECT value FROM _sync_meta WHERE key = ?", (sync_apply.REPOINT_META_KEY,))) == []
    assert _q1(holder, "SELECT client_id FROM tracker_dump WHERE id = ?", (dump,), raw=True) == _client_id(holder, winner)
    # The re-point isn't a local edit: nothing to send for the tracker row.
    assert holder.db.seal_pending()["raw"].changes == 0


def test_merge_decision_is_replicated_so_a_rename_race_converges(nodes):
    """a renames its 'P' service to 'Q' while b creates its own 'P'. b sees a's 'P' before the
    rename and merges; a never sees two 'P's. The merge travels as a change, so a merges too."""
    a, b = nodes(2)
    _exec(a, "INSERT INTO services (name) VALUES ('Invented P')")
    a.db.seal_pending()
    early = _outgoing(a, "master")
    _exec(a, "UPDATE services SET name = 'Invented Q' WHERE name = 'Invented P'")
    _exec(b, "INSERT INTO services (name) VALUES ('Invented P')")
    b.db.seal_pending()
    r = b.db.apply_changes("master", early, admin_pubkey=b.admin_pub)
    assert r.merges and r.emitted == 1
    sync_all(a, b)
    for n in (a, b):
        assert _q1(n, "SELECT COUNT(*) FROM services WHERE name IN ('Invented P', 'Invented Q')") == 1
    assert digest(a.db) == digest(b.db)


def test_merge_change_reaches_a_pc_that_never_saw_both_rows(nodes):
    a, b, c = nodes(3)
    ga = _add(a, PAN_A, company="From A")
    gb = _add(b, PAN_A, company="From B")
    pull(b, a)                                   # b merges and records the decision
    merges = [x for x in _outgoing(b, "master") if x["op"] == "merge"]
    assert len(merges) == 1 and json.loads(merges[0]["data"])["into"] == min(ga, gb)
    pull(c, b)
    assert _rows(c, "SELECT gid FROM clients") == [(min(ga, gb),)]
    pull(a, b)
    assert digest(a.db) == digest(b.db) == digest(c.db)


def test_merge_racing_a_delete_of_one_copy_ends_the_same_everywhere(nodes):
    """w merges h's copy into its own while h deletes its copy. A delete of either copy deletes
    the merged client, in whatever order a PC learns the merge and the delete."""
    a, b, c = nodes(3)
    ga = _add(a, PAN_A)
    gb = _add(b, PAN_A)
    h, w = (a, b) if ga > gb else (b, a)          # h holds the losing (larger) gid
    loser = max(ga, gb)
    h.db.seal_pending()
    early = _outgoing(h, "master")
    r = w.db.apply_changes("master", early, admin_pubkey=w.admin_pub)
    assert r.merges
    _exec(h, "DELETE FROM clients WHERE gid = ?", (loser,))
    pull(c, h)                                   # c: loser created, then deleted
    assert _q1(c, "SELECT COUNT(*) FROM clients") == 0
    pull(c, w)                                   # c: winner + the merge -> merged row deleted
    sync_all(a, b, c)
    for n in (a, b, c):
        assert _q1(n, "SELECT COUNT(*) FROM clients") == 0
    assert digest(a.db) == digest(b.db) == digest(c.db)


def test_copy_deleted_before_anyone_saw_both_is_not_merged(nodes):
    a, b = nodes(2)
    ga = _add(a, PAN_A)
    gb = _add(b, PAN_A)
    _exec(a, "DELETE FROM clients WHERE gid = ?", (ga,))
    sync_all(a, b)
    for n in (a, b):
        assert _rows(n, "SELECT gid FROM clients") == [(gb,)]
    assert digest(a.db) == digest(b.db)


@pytest.mark.parametrize("seed", [1, 7, 42])
def test_random_operations_and_partial_syncs_converge(nodes, seed):
    """P3-0 (a) shape, handed over directly: 3 PCs, random edits on shared and duplicate
    clients (same PANs on purpose), services, settings, formatting, audit and tracker rows,
    with random one-way syncs in between. All digests end equal."""
    import random
    rng = random.Random(seed)
    ns = nodes(3)
    pans = ["AAAAA1111A", "BBBBB2222B", "CCCCC3333C", "DDDDD4444D"]

    def clients(n, active_only=False):
        sql = "SELECT id FROM clients" + (" WHERE is_archived = 0" if active_only else "")
        return [r[0] for r in _rows(n, sql)]

    for step in range(120):
        n = rng.choice(ns)
        op = rng.choice(["add", "add", "notes", "value", "clear", "archive", "delete", "service",
                         "rename", "attach", "setting", "format", "audit", "tracker", "sync", "sync"])
        cs = clients(n)
        try:
            if op == "add":
                pan = rng.choice(pans)
                if _q1(n, "SELECT COUNT(*) FROM clients c JOIN client_values v ON v.client_id = c.id "
                          "WHERE c.is_archived = 0 AND v.column_id = ? AND v.value = ?",
                       (_col(n.db, "PAN"), pan)) == 0:
                    _add(n, pan, company=f"Invented {step}")
            elif op == "notes" and cs:
                n.db.update_client_notes(rng.choice(cs), f"note {step}")
            elif op == "value" and cs:
                n.db.update_client_single_field(rng.choice(cs), _col(n.db, "GSTIN"), f"G{step}")
            elif op == "clear" and cs:
                cid = rng.choice(cs)
                n.db.update_client(cid, _values(n.db, cid, GSTIN=None), "", [])
            elif op == "archive" and cs:
                _exec(n, "UPDATE clients SET is_archived = 1 WHERE id = ?", (rng.choice(cs),))
            elif op == "delete" and cs and rng.random() < 0.5:
                _exec(n, "DELETE FROM clients WHERE id = ?", (rng.choice(cs),))
            elif op == "service":
                _exec(n, "INSERT OR IGNORE INTO services (name) VALUES (?)", (f"Svc {rng.randint(1, 4)}",))
            elif op == "rename":
                _exec(n, "UPDATE OR IGNORE services SET name = ? WHERE name = ?",
                      (f"Svc {rng.randint(1, 4)}", f"Svc {rng.randint(1, 4)}"))
            elif op == "attach" and cs:
                sid = _q1(n, "SELECT id FROM services ORDER BY random() LIMIT 1")
                _exec(n, "INSERT OR IGNORE INTO client_services (client_id, service_id) VALUES (?, ?)",
                      (rng.choice(cs), sid))
            elif op == "setting":
                n.db.set_setting(f"invented_{rng.randint(1, 3)}", str(step))
            elif op == "format" and cs:
                n.db.bulk_set_cell_formatting([{"client_id": rng.choice(cs), "column_key": rng.choice(
                    [str(_col(n.db, "PAN")), "services"]), "bg_color": f"#{step:06d}", "fg_color": ""}])
            elif op == "audit":
                n.db.log_action("Tester", "invented", client_id=rng.choice(cs) if cs else None, detail=str(step))
            elif op == "tracker":
                _add_tracker(n.db, client_id=rng.choice(cs) if cs else None, arn_number=f"AA{step:013d}")
            elif op == "sync":
                x, y = rng.sample(ns, 2)
                pull(x, y)
        except ValueError:
            pass                                 # the app refused the edit (e.g. duplicate PK)
    sync_all(*ns)
    d = [digest(n.db) for n in ns]
    assert d[0] == d[1] == d[2]
    for n in ns:
        assert _q1(n, "SELECT COUNT(*) FROM _sync_parked") == 0
        assert _q1(n, "SELECT COUNT(*) FROM _sync_parked", raw=True) == 0


def test_emit_false_merges_here_without_adding_to_the_stream(nodes):
    """For P3-7 shadow replicas, which share the live DB's stream id."""
    a, b = nodes(2)
    ga = _add(a, PAN_A)
    gb = _add(b, PAN_A)
    a.db.seal_pending()
    before = _q1(b, "SELECT COUNT(*) FROM _sync_changes")
    r = sync_apply.apply_batch(b.db.db_path, b.db.hex_key, "master", _outgoing(a, "master"),
                               admin_pubkey=b.admin_pub, emit=False)
    assert r.merges and r.emitted == 0
    assert _rows(b, "SELECT gid FROM clients") == [(min(ga, gb),)]
    assert _q1(b, "SELECT COUNT(*) FROM _sync_changes WHERE op = 'merge'") == 0
    assert _q1(b, "SELECT COUNT(*) FROM _sync_changes") == before + len(_outgoing(a, "master"))


def test_merge_decision_uses_the_next_own_sequence_number(nodes):
    a, b = nodes(2)
    _add(a, PAN_A)
    _add(b, PAN_A)
    b.db.seal_pending()
    stream = _q1(b, "SELECT value FROM _sync_meta WHERE key='stream_id'")
    last = _q1(b, "SELECT MAX(origin_seq) FROM _sync_changes WHERE origin = ?", (stream,))
    pull(b, a)
    mine = _rows(b, "SELECT origin_seq, op, tbl FROM _sync_changes WHERE origin = ? AND origin_seq > ? "
                    "ORDER BY origin_seq", (stream, last))
    assert [s for s, _o, _t in mine] == list(range(last + 1, last + 1 + len(mine)))
    assert ("merge", "clients") in {(o, t) for _s, o, t in mine}
    assert _vector(b, "master")[stream] == mine[-1][0]
    assert sync_capture.read_seq_mark(sync_capture.seq_state_path(b.db.db_path), stream) == mine[-1][0]
    # The sealer carries on after the emitted numbers.
    b.db.update_client_notes(_q1(b, "SELECT id FROM clients"), "after the merge")
    b.db.seal_pending()
    assert _q1(b, "SELECT MAX(origin_seq) FROM _sync_changes WHERE origin = ?", (stream,)) == mine[-1][0] + 1


def test_delete_of_an_append_row_is_ignored(nodes, caplog):
    """Review 2026-09-25: append tables are insert-only (§4.4)."""
    a, b = nodes(2)
    a.db.log_action("Tester", "invented-action", detail="kept")
    pull(b, a)
    gid = _q1(b, "SELECT gid FROM audit_log WHERE detail = 'kept'")
    ch = _fake("e" * 32 + ":m", 1, int(time.time() * 1000), "audit_log", json.dumps([gid]), op="delete")
    with caplog.at_level(logging.WARNING, logger="sera.sync.apply"):
        r = b.db.apply_changes("master", [ch], admin_pubkey=b.admin_pub)
    assert r.ignored == 1 and "append-only" in caplog.text
    assert _q1(b, "SELECT COUNT(*) FROM audit_log WHERE gid = ?", (gid,)) == 1


def test_merge_for_a_table_that_is_never_merged_is_ignored(nodes):
    """Review 2026-09-25: only clients, services and staff_users take merges."""
    _a, b = nodes(2)
    gids = [g for (g,) in _rows(b, "SELECT gid FROM mcl_columns ORDER BY id LIMIT 2")]
    loser, winner = max(gids), min(gids)
    ch = _fake("e" * 32 + ":m", 1, int(time.time() * 1000), "mcl_columns", json.dumps([loser]),
               op="merge", data={"into": winner})
    r = b.db.apply_changes("master", [ch], admin_pubkey=b.admin_pub)
    assert r.ignored == 1 and not r.merges
    assert _q1(b, "SELECT COUNT(*) FROM mcl_columns WHERE gid IN (?, ?)", (loser, winner)) == 2
    assert _q1(b, "SELECT COUNT(*) FROM _sync_alias") == 0


def test_change_under_a_merged_away_gid_waits_behind_the_winners_parked_change(nodes):
    """Review 2026-09-25: the behind-parked check follows _sync_alias."""
    _a, b = nodes(2)
    _exec(b, "INSERT INTO services (name) VALUES ('Invented Portal')")
    winner = _q1(b, "SELECT gid FROM services WHERE name = 'Invented Portal'")
    loser = "f" * 32 if winner < "f" * 32 else "0" * 32
    loser, winner_ = max(winner, loser), min(winner, loser)
    if winner_ != winner:
        pytest.skip("random gid happened to sort after the invented one")
    _exec(b, "INSERT INTO _sync_alias (tbl, alias_gid, gid) VALUES ('services', ?, ?)", (loser, winner))
    now = int(time.time() * 1000)
    first = _fake("e" * 32 + ":m", 1, now, "services", json.dumps([winner]),
                  data={"userid_column_id": "9" * 32})              # column not here yet
    later = _fake("d" * 32 + ":m", 1, now + 1, "services", json.dumps([loser]),
                  data={"arn_selector": "#arn"})
    r = b.db.apply_changes("master", [first, later], admin_pubkey=b.admin_pub)
    assert r.parked == 2
    assert {x for (x,) in _rows(b, "SELECT reason FROM _sync_parked")} == {"missing_parent", "behind_parked"}
    assert _q1(b, "SELECT arn_selector FROM services WHERE gid = ?", (winner,)) is None
