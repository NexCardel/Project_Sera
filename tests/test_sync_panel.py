"""Tests for Sera Sync v3 P3-8: sync_panel.py, the Sera Sync dialog panel's data helpers.

Accept (blueprint SS5 P3-8, panel bullet): pending outgoing count, parked count, the
conflicts list, its "keep" / "use discarded value" resolution (written as a normal edit
that the P3-3 capture triggers pick up), and the shadow check summary.

tmp_path only (SS0 rule 2). Invented test data only (SS0 rule 12).
"""

import json

import pytest

import sync_panel
from tests.test_sync_capture import PAN_A, PAN_B, PAN_C, _add_client, _gid, _make_db


@pytest.fixture
def db(tmp_path):
    d = _make_db(tmp_path / "pc")
    yield d
    d.stop_seal_timer()


def _make_client_with_notes(db, pan, notes):
    client_id = _add_client(db, pan)
    with db._connect() as conn:
        conn.execute("UPDATE clients SET notes = ? WHERE id = ?", (notes, client_id))
    return client_id


def _insert_conflict(db, tbl, row_key, col, kept, discarded, reason="lww"):
    with db._connect() as conn:
        conn.execute(
            "INSERT INTO _sync_conflicts(at, tbl, row_key, col, kept, discarded, reason) "
            "VALUES ('2026-09-25T00:00:00', ?, ?, ?, ?, ?, ?)",
            (tbl, row_key, col, json.dumps(kept), json.dumps(discarded), reason))
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


# ---------------------------------------------------------------- parked_count / stats / format_age

def test_parked_count_zero_by_default(db):
    with db._connect() as conn:
        assert sync_panel.parked_count(conn) == 0
        assert sync_panel.parked_stats(conn) == (0, None)


def test_parked_count_counts_rows(db):
    with db._connect() as conn:
        conn.execute("INSERT INTO _sync_parked(change_json, reason, first_at, tries) "
                     "VALUES ('{}', 'parent_missing', '2026-09-25T00:00:00', 0)")
        conn.execute("INSERT INTO _sync_parked(change_json, reason, first_at, tries) "
                     "VALUES ('{}', 'parent_missing', '2026-09-25T00:00:01', 0)")
        assert sync_panel.parked_count(conn) == 2


def test_parked_stats_computes_oldest_age(db):
    import datetime
    now = datetime.datetime(2026, 9, 25, 2, 0, 0, tzinfo=datetime.timezone.utc)
    with db._connect() as conn:
        # oldest is 2026-09-25T00:00:00Z (2 hours = 7200s old)
        conn.execute("INSERT INTO _sync_parked(change_json, reason, first_at, tries) "
                     "VALUES ('{}', 'parent_missing', '2026-09-25T00:00:00Z', 0)")
        # newer is 2026-09-25T01:30:00Z (30 min = 1800s old)
        conn.execute("INSERT INTO _sync_parked(change_json, reason, first_at, tries) "
                     "VALUES ('{}', 'parent_missing', '2026-09-25T01:30:00Z', 0)")
        count, oldest_age = sync_panel.parked_stats(conn, now=now)
        assert count == 2
        assert oldest_age == 7200


def test_parked_summary_combines_connections(db):
    import datetime
    now = datetime.datetime(2026, 9, 25, 2, 0, 0, tzinfo=datetime.timezone.utc)
    with db._connect() as conn1, db._connect_raw() as conn2:
        conn1.execute("INSERT INTO _sync_parked(change_json, reason, first_at, tries) "
                      "VALUES ('{}', 'parent_missing', '2026-09-25T01:00:00Z', 0)")
        conn2.execute("INSERT INTO _sync_parked(change_json, reason, first_at, tries) "
                      "VALUES ('{}', 'parent_missing', '2026-09-25T00:00:00Z', 0)")
        count, oldest_age = sync_panel.parked_summary([conn1, conn2], now=now)
        assert count == 2
        assert oldest_age == 7200  # from conn2


def test_format_age():
    assert sync_panel.format_age(0) == "0s"
    assert sync_panel.format_age(45) == "45s"
    assert sync_panel.format_age(60) == "1m"
    assert sync_panel.format_age(3599) == "59m"
    assert sync_panel.format_age(3600) == "1h"
    assert sync_panel.format_age(3660) == "1h 1m"
    assert sync_panel.format_age(7200) == "2h"
    assert sync_panel.format_age(86400) == "1d"
    assert sync_panel.format_age(90000) == "1d 1h"
    assert sync_panel.format_age(7 * 86400) == "7d"
    assert sync_panel.format_age(None) == "unknown"


# ---------------------------------------------------------------- vectors / pending_outgoing

def test_pending_outgoing_counts_what_a_peer_has_not_acked(db):
    with db._connect() as conn:
        conn.execute("INSERT INTO _sync_vector(origin, max_seq) VALUES (?, ?)", ("dev-a", 10))
        conn.execute("INSERT INTO _sync_vector(origin, max_seq) VALUES (?, ?)", ("dev-b", 3))
        conn.execute(
            "INSERT INTO _sync_peer_vectors(device_id, origin, max_seq, seen_at) VALUES (?,?,?,?)",
            ("peer-1", "dev-a", 7, "2026-09-25T00:00:00"))
        own = sync_panel.own_vector(conn)
        peer = sync_panel.peer_vector(conn, "peer-1")
    assert own == {"dev-a": 10, "dev-b": 3}
    assert peer == {"dev-a": 7}
    # dev-a: 10-7=3 unseen; dev-b: peer has never acked any -> all 3 unseen
    assert sync_panel.pending_outgoing(own, peer) == 6


def test_pending_outgoing_never_goes_negative_if_peer_is_ahead_of_a_stale_vector(db):
    own = {"dev-a": 5}
    peer = {"dev-a": 9}
    assert sync_panel.pending_outgoing(own, peer) == 0


def test_peer_vector_unknown_peer_is_empty(db):
    with db._connect() as conn:
        assert sync_panel.peer_vector(conn, "never-seen") == {}


# ---------------------------------------------------------------- list_conflicts

def test_list_conflicts_newest_first_and_limit(db):
    for i in range(3):
        _insert_conflict(db, "clients", json.dumps([f"gid-{i}"]), "notes", f"kept-{i}", f"discarded-{i}")
    with db._connect() as conn:
        rows = sync_panel.list_conflicts(conn)
        assert [r["kept"] for r in rows] == ["kept-2", "kept-1", "kept-0"]
        limited = sync_panel.list_conflicts(conn, limit=2)
        assert len(limited) == 2
        assert limited[0]["kept"] == "kept-2"


def test_list_conflicts_decodes_json_values(db):
    _insert_conflict(db, "clients", json.dumps(["gid-x"]), "notes", "old text", "new text")
    with db._connect() as conn:
        rows = sync_panel.list_conflicts(conn)
    assert rows[0]["table"] == "clients"
    assert rows[0]["column"] == "notes"
    assert rows[0]["kept"] == "old text"
    assert rows[0]["discarded"] == "new text"
    assert rows[0]["reason"] == "lww"


# ---------------------------------------------------------------- dismiss_conflict ("keep")

def test_dismiss_conflict_removes_the_row_without_touching_data(db):
    client_id = _make_client_with_notes(db, PAN_A, "original notes")
    gid = _gid(db, "clients", client_id)
    cid = _insert_conflict(db, "clients", json.dumps([gid]), "notes", "original notes", "someone else's notes")
    with db._connect() as conn:
        sync_panel.dismiss_conflict(conn, cid)
        assert sync_panel.list_conflicts(conn) == []
        notes = conn.execute("SELECT notes FROM clients WHERE id = ?", (client_id,)).fetchone()[0]
    assert notes == "original notes"


# ---------------------------------------------------------------- use_discarded_value
#
# sync_apply.py only ever writes a _sync_conflicts row the instant it deletes/tombstones the
# row (P3-4 log note 10, DELETED_ROW_REASONS below) -- by the time anyone looks at the panel
# the row is already gone, so use_discarded_value's UPDATE can never reach it for a conflict
# sync_apply.py actually produces (P3-8 review). test_use_discarded_value_against_a_real_delete_conflict
# below exercises that real shape via sync_apply.apply_batch and expects False + the conflict
# preserved. The "writes the field" test here is a defensive/generic check of the UPDATE logic
# itself for a still-live row, a shape no current caller produces but the function still
# handles correctly if one ever does.

def test_use_discarded_value_writes_the_field_and_removes_the_conflict(db):
    client_id = _make_client_with_notes(db, PAN_A, "original notes")
    gid = _gid(db, "clients", client_id)
    cid = _insert_conflict(db, "clients", json.dumps([gid]), "notes", "original notes", "the discarded text")

    with db._connect() as conn:
        assert sync_panel.use_discarded_value(conn, cid) is True
        notes = conn.execute("SELECT notes FROM clients WHERE id = ?", (client_id,)).fetchone()[0]
        assert sync_panel.list_conflicts(conn) == []
    assert notes == "the discarded text"

    # Written as a normal edit: the P3-3 capture trigger queued it, and _connect()'s own
    # post-commit seal (database.py's _seal_after_commit) turned it into a change, exactly
    # as a hand-typed edit would produce -- no special-cased sync path here.
    with db._connect() as conn:
        rows = conn.execute(
            "SELECT tbl, op FROM _sync_changes WHERE tbl = 'clients' ORDER BY seq DESC LIMIT 1"
        ).fetchone()
    assert rows == ("clients", "upsert")


def test_use_discarded_value_missing_conflict_returns_false(db):
    with db._connect() as conn:
        assert sync_panel.use_discarded_value(conn, 999999) is False


def test_use_discarded_value_unknown_table_returns_false(db):
    cid = _insert_conflict(db, "not_a_real_table", json.dumps(["x"]), "col", "a", "b")
    with db._connect() as conn:
        assert sync_panel.use_discarded_value(conn, cid) is False
        # the conflict is left for a human to look at, not silently dropped
        assert len(sync_panel.list_conflicts(conn)) == 1


def test_use_discarded_value_row_key_length_mismatch_returns_false(db):
    # clients.row_key is ("gid",) -- a two-element key can't be this table's row
    cid = _insert_conflict(db, "clients", json.dumps(["a", "b"]), "notes", "x", "y")
    with db._connect() as conn:
        assert sync_panel.use_discarded_value(conn, cid) is False


def test_use_discarded_value_bad_column_does_not_touch_the_table(db):
    client_id = _make_client_with_notes(db, PAN_B, "original notes")
    gid = _gid(db, "clients", client_id)
    # A column that doesn't exist on clients -- must be refused, not built into raw SQL.
    cid = _insert_conflict(db, "clients", json.dumps([gid]), 'notes" ; DROP TABLE clients; --',
                            "x", "y")
    with db._connect() as conn:
        assert sync_panel.use_discarded_value(conn, cid) is False
        row = conn.execute("SELECT notes FROM clients WHERE id = ?", (client_id,)).fetchone()
    assert row == ("original notes",)


def test_use_discarded_value_row_no_longer_exists_returns_false_and_keeps_the_conflict(db):
    client_id = _make_client_with_notes(db, PAN_C, "original notes")
    gid = _gid(db, "clients", client_id)
    cid = _insert_conflict(db, "clients", json.dumps([gid]), "notes", "original notes", "new text")
    with db._connect() as conn:
        conn.execute("DELETE FROM clients WHERE id = ?", (client_id,))
    with db._connect() as conn:
        assert sync_panel.use_discarded_value(conn, cid) is False
        # A failed attempt must never destroy the only record of the discarded edit
        # (P3-8 review, item 1): the row stays for a human to look at.
        assert len(sync_panel.list_conflicts(conn)) == 1


def test_use_discarded_value_against_a_real_delete_conflict(tmp_path):
    """sync_apply.py's only two conflict-writing call sites both fire the instant the row is
    deleted/tombstoned (P3-4 log note 10), so the conflict this produces always points at a
    row that's already gone by the time anything reads it back. Exercises that real shape
    end to end via sync_apply.apply_batch, rather than a hand-inserted conflict row."""
    import secrets
    import time
    import sync_apply
    from sync_capture import HLC
    from tests.test_sync_capture import PAN_A as _PAN, _add_client, _gid as _client_gid, _make_db

    node = _make_db(tmp_path / "node", mode="shadow")
    try:
        client_id = _add_client(node, _PAN)
        gid = _client_gid(node, "clients", client_id)
        with node._connect() as conn:
            conn.execute("UPDATE clients SET notes = 'kept locally' WHERE id = ?", (client_id,))
        node.seal_pending()

        # A remote delete for the same client, timestamped *before* the local edit above: a
        # delete always wins over an older edit, but a still-newer local edit than the delete
        # is exactly what sync_apply.py records as a discarded conflict (its "newest > stamp"
        # check in _delete -- the conflict fires when the row's own clock is newer than the
        # delete it's losing to, not the other way around).
        past_hlc = str(HLC(int(time.time() * 1000) - 60_000, 0, "past-device"))
        delete_change = {
            "origin": secrets.token_hex(16), "origin_seq": 1, "hlc": past_hlc,
            "tbl": "clients", "row_key": json.dumps([gid]), "op": "delete", "data": None, "sig": None,
        }
        # Every column touched since the baseline (add_client's own writes plus the notes
        # update) has a clock newer than the delete, so each becomes its own conflict row --
        # only "notes" is this test's concern.
        result = sync_apply.apply_batch(node.db_path, node.hex_key, "master", [delete_change])
        assert result.conflicts >= 1

        with node._connect() as conn:
            conflicts = sync_panel.list_conflicts(conn)
            notes_conflicts = [c for c in conflicts if c["column"] == "notes"]
            assert len(notes_conflicts) == 1
            conflict = notes_conflicts[0]
            assert conflict["reason"] in sync_panel.DELETED_ROW_REASONS
            assert conflict["discarded"] == "kept locally"

            applied = sync_panel.use_discarded_value(conn, conflict["id"])
            assert applied is False
            # nothing left to write it back onto -- and the conflict is preserved, not erased
            assert any(c["id"] == conflict["id"] for c in sync_panel.list_conflicts(conn))
            assert conn.execute("SELECT COUNT(*) FROM clients WHERE id = ?", (client_id,)).fetchone()[0] == 0
    finally:
        node.stop_seal_timer()


# ---------------------------------------------------------------- shadow_check_summary

def test_shadow_check_summary_missing_file_is_empty(tmp_path):
    assert sync_panel.shadow_check_summary(tmp_path) == []


def test_shadow_check_summary_returns_the_tail(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    lines = [f"2026-09-25T00:0{i}:00 capture check ok" for i in range(8)]
    (log_dir / "sync_shadow.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    tail = sync_panel.shadow_check_summary(tmp_path, tail_lines=3)
    assert tail == lines[-3:]
