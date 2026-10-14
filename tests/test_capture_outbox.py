"""core/capture_outbox.py: a captured row survives a locked or failing database (SGT never-miss WP2)."""
import json
import threading
import time

import pytest
import sqlcipher3.dbapi2 as sqlite3

from core import capture_outbox
from core.capture_outbox import CaptureOutbox, DeadLetter


def _msg(n, **extra):
    return {"portal": "Test Portal", "arn": f"TEST{n:04d}", "n": n, **extra}


class Health:
    def __init__(self):
        self.rows, self.disk = 0, 0

    def row(self, kind):
        assert kind == "dead_letter"
        self.rows += 1

    def disk_failed(self):
        self.disk += 1


class Store:
    """A fake database: fails with `error` while `down` is true; records what it wrote, in order."""

    def __init__(self):
        self.down = False
        self.error = sqlite3.OperationalError("database is locked")
        self.written = []
        self.calls = 0

    def __call__(self, msg):
        self.calls += 1
        if self.down:
            raise self.error
        self.written.append(msg["n"])
        return {"id": msg["n"]}


def _outbox(tmp_path, store, health=None, delays=(0, 0, 0, 0, 0)):
    dead = DeadLetter(tmp_path / capture_outbox.DEAD_LETTER_NAME)
    return CaptureOutbox(store, dead, health=(lambda: health), delays=delays, sleep=lambda s: None), dead


def test_a_locked_database_is_retried_then_written(tmp_path):
    store = Store()
    outbox, dead = _outbox(tmp_path, store)
    fails = {"left": 3}
    real = store.__call__

    def flaky(msg):
        if fails["left"]:
            fails["left"] -= 1
            raise store.error
        return real(msg)

    outbox._process = flaky
    assert outbox.handle(_msg(1)) == {"id": 1}
    assert store.written == [1]
    assert dead.count() == 0


def test_after_retries_the_row_goes_to_the_dead_letter(tmp_path):
    store, health = Store(), Health()
    store.down = True
    outbox, dead = _outbox(tmp_path, store, health)
    assert outbox.handle(_msg(1)) is None
    assert store.calls == 1 + len(capture_outbox.RETRY_DELAYS_SEC)
    assert dead.count() == 1 and health.rows == 1 and health.disk == 0
    line = json.loads((tmp_path / capture_outbox.DEAD_LETTER_NAME).read_text(encoding="utf-8"))
    assert line["msg"]["n"] == 1 and line["why"].startswith("database")


def test_the_retry_pauses_are_the_specified_ones(tmp_path):
    store = Store()
    store.down = True
    waits = []
    dead = DeadLetter(tmp_path / capture_outbox.DEAD_LETTER_NAME)
    CaptureOutbox(store, dead, sleep=waits.append).handle(_msg(1))
    assert tuple(waits) == capture_outbox.RETRY_DELAYS_SEC == (0.5, 1, 2, 4, 8)


def test_dead_letter_is_replayed_at_startup(tmp_path):
    store = Store()
    store.down = True
    outbox, dead = _outbox(tmp_path, store)
    outbox.handle(_msg(1))
    outbox.handle(_msg(2))
    assert store.written == [] and dead.count() == 2
    # a new run of the app, database healthy again
    store2 = Store()
    outbox2, dead2 = _outbox(tmp_path, store2)
    told = []
    assert outbox2.replay(on_result=lambda m, r: told.append(m["n"])) == 2
    assert store2.written == [1, 2] and told == [1, 2]
    assert dead2.count() == 0 and not dead2.pending()


def test_a_failed_replay_keeps_the_rest_in_order(tmp_path):
    store = Store()
    store.down = True
    outbox, dead = _outbox(tmp_path, store)
    for n in (1, 2, 3):
        outbox.handle(_msg(n))
    store.down = False
    calls = {"n": 0}
    real = store.__call__

    def second_fails(msg):
        calls["n"] += 1
        if calls["n"] == 2:
            raise store.error
        return real(msg)

    outbox._process = second_fails
    assert outbox.replay() == 1
    assert store.written == [1]
    outbox._process = real
    assert outbox.replay() == 2
    assert store.written == [1, 2, 3]


def test_order_of_superseding_rows_is_kept(tmp_path):
    """Row 2 supersedes row 1. While row 1 waits in the dead letter, row 2 queues behind it, so the
    database never sees them the other way round."""
    store = Store()
    store.down = True
    outbox, dead = _outbox(tmp_path, store)
    outbox.handle(_msg(1, dataset_key="SGT:a"))
    outbox.handle(_msg(2, supersedes_dataset_key="SGT:a"))
    assert store.written == [] and dead.count() == 2
    store.down = False
    outbox._backlog_failed_at = None
    assert outbox.handle(_msg(3)) == {"id": 3}          # drains 1 and 2 first, then writes 3
    assert store.written == [1, 2, 3]


def test_a_new_row_does_not_retry_the_backlog_at_once_again(tmp_path):
    store = Store()
    store.down = True
    now = {"t": 100.0}
    dead = DeadLetter(tmp_path / capture_outbox.DEAD_LETTER_NAME)
    outbox = CaptureOutbox(store, dead, delays=(), sleep=lambda s: None, clock=lambda: now["t"])
    outbox.handle(_msg(1))
    calls = store.calls
    outbox.handle(_msg(2))                  # drain tried (and failed) once; row 2 waits behind row 1
    after_drain = store.calls
    outbox.handle(_msg(3))                  # inside the gap: no database call at all
    assert store.calls == after_drain > calls
    assert dead.count() == 3
    now["t"] += capture_outbox.BACKLOG_RETRY_GAP_SEC + 1
    store.down = False
    assert outbox.handle(_msg(4)) == {"id": 4}
    assert store.written == [1, 2, 3, 4]


def test_a_malformed_row_is_held_not_retried_or_replayed(tmp_path):
    store, health = Store(), Health()
    store.error = ValueError("bad payload")
    store.down = True
    outbox, dead = _outbox(tmp_path, store, health)
    assert outbox.handle(_msg(1)) is None
    assert store.calls == 1                         # not retried
    assert dead.count() == 1 and not dead.pending() and health.rows == 1
    assert outbox.replay() == 0 and store.calls == 1
    held = json.loads(dead.held_path.read_text(encoding="utf-8"))
    assert held["why"] == "malformed"


def test_an_integrity_error_is_held_not_retried(tmp_path):
    store = Store()
    store.error = sqlite3.IntegrityError("constraint")
    store.down = True
    outbox, dead = _outbox(tmp_path, store)
    outbox.handle(_msg(1))
    assert store.calls == 1 and not dead.pending() and dead.count() == 1


def test_quit_with_rows_queued_leaves_them_in_the_dead_letter(tmp_path):
    store = Store()
    store.down = True
    outbox, dead = _outbox(tmp_path, store)
    outbox._current = _msg(1)               # the row the worker is on
    assert outbox.stop_and_flush([_msg(2), _msg(3)]) == 3
    assert [e["msg"]["n"] for e in DeadLetter._read(dead.path)] == [1, 2, 3]
    # the next start writes them
    store2 = Store()
    outbox2, _ = _outbox(tmp_path, store2)
    assert outbox2.replay() == 3 and store2.written == [1, 2, 3]


def test_quit_stops_the_retry_waits(tmp_path):
    store = Store()
    store.down = True
    dead = DeadLetter(tmp_path / capture_outbox.DEAD_LETTER_NAME)
    outbox = CaptureOutbox(store, dead, delays=(30, 30))        # real waits, interrupted by quit
    t = threading.Thread(target=outbox.handle, args=(_msg(1),))
    t.start()
    time.sleep(0.2)
    outbox.stop_and_flush([])
    t.join(5)
    assert not t.is_alive()


def test_a_replay_that_crashed_midway_is_picked_up_again(tmp_path):
    dead = DeadLetter(tmp_path / capture_outbox.DEAD_LETTER_NAME)
    dead.put(_msg(1), "database")
    assert [m["n"] for m in dead.take_all()] == [1]         # taken, then the app died
    dead.put(_msg(2), "database")
    assert [m["n"] for m in dead.take_all()] == [1, 2]
    dead.finish_replay()
    assert dead.count() == 0


def test_a_half_written_last_line_is_skipped(tmp_path):
    dead = DeadLetter(tmp_path / capture_outbox.DEAD_LETTER_NAME)
    dead.put(_msg(1), "database")
    with open(dead.path, "a", encoding="utf-8") as f:
        f.write('{"ts": 1, "why": "database", "ms')
    assert [m["n"] for m in dead.take_all()] == [1]


def test_a_dead_letter_that_cannot_be_written_reports_the_disk(tmp_path):
    blocker = tmp_path / "not_a_folder"
    blocker.write_text("x")
    dead = DeadLetter(blocker / capture_outbox.DEAD_LETTER_NAME)
    store, health = Store(), Health()
    store.down = True
    outbox = CaptureOutbox(store, dead, health=lambda: health, delays=(), sleep=lambda s: None)
    assert outbox.handle(_msg(1)) is None       # never raises
    assert health.disk == 1


def test_the_default_folder_is_the_shadow_folder():
    from core.sgt.sgt_shadow import shadow_dir
    assert capture_outbox.default_dir() == shadow_dir()


def test_is_retryable_picks_database_failures_only():
    from sera_db.common import DatabaseError
    assert capture_outbox.is_retryable(sqlite3.OperationalError("database is locked"))
    assert capture_outbox.is_retryable(DatabaseError("could not open"))
    assert not capture_outbox.is_retryable(sqlite3.IntegrityError("x"))
    assert not capture_outbox.is_retryable(ValueError("x"))
    assert not capture_outbox.is_retryable(KeyError("x"))


def _real_db(tmp_path):
    import security
    from database import SeraDatabase
    salt = str(tmp_path / "t.salt")
    security.generate_and_save_salt(salt)
    key = security.derive_key_hex("testpass123", security.load_salt(salt))
    return SeraDatabase(str(tmp_path / "m.db"), key, raw_db_path=str(tmp_path / "r.db")), key


def test_a_row_lands_after_the_raw_database_is_locked_longer_than_5_seconds(tmp_path):
    """The connection waits 5 s on a lock, then fails. A writer holds rawPayload.db for 6.5 s while
    the row is queued: the first attempt fails, the retry lands it."""
    db, key = _real_db(tmp_path)
    locked, release_after = threading.Event(), 6.5

    def hold_lock():
        conn = sqlite3.connect(db.raw_db_path, isolation_level=None)
        conn.execute(f"PRAGMA key = \"x'{key}'\";")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("BEGIN IMMEDIATE")
        locked.set()
        time.sleep(release_after)
        conn.execute("COMMIT")
        conn.close()

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert locked.wait(10)

    def write(msg):
        return db.insert_tracker_dump(
            portal="Test Portal (Test Form)", period_label="Test Period", arn_number=msg["arn"],
            capture_method="SGT_live", status="Submitted", pan="ABCPD1234E", filing_type="Test Form",
            raw_payload_json=json.dumps({"pan": "ABCPD1234E"}))

    dead = DeadLetter(tmp_path / capture_outbox.DEAD_LETTER_NAME)
    outbox = CaptureOutbox(write, dead)
    started = time.monotonic()
    result = outbox.handle(_msg(1, arn="TEST0001"))
    took = time.monotonic() - started
    holder.join()
    assert result and not result.get("duplicate")
    assert took > 5.0                       # it really had to wait out the lock
    assert dead.count() == 0
    with db._connect_raw() as r:
        assert r.execute("SELECT COUNT(*) FROM tracker_dump WHERE arn_number = 'TEST0001'").fetchone()[0] == 1
