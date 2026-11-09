"""
core/capture_outbox.py - a captured row is never lost to a busy or failing database.

The capture worker (main.py) writes one queued capture at a time. A database lock longer than
the connection's own 5 s wait, a full disk or any other write failure used to drop the row.
Now the row is tried again with growing pauses and, if it still fails, appended to the dead
letter file (~/AmanAssociates_Sera/sgt_shadow/undelivered_rows.jsonl). The dead letter is
replayed at start-up and every 10 minutes.

Rows are idempotent (the tracker keeps one row per dataset_key), so a row written twice is safe.
Order is kept: while the dead letter holds rows, a new row waits behind them, so a row that
supersedes another never lands before it.

The files hold captured data like the SGT shadow logs next to them; log lines hold only error
class names, never values.
"""

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, List, Optional

DEAD_LETTER_NAME = "undelivered_rows.jsonl"
HELD_NAME = "undelivered_rows_held.jsonl"        # rows a retry cannot fix (see HELD_WHY)
RETRY_DELAYS_SEC = (0.5, 1, 2, 4, 8)             # then dead letter
REPLAY_EVERY_SEC = 600
BACKLOG_RETRY_GAP_SEC = 30.0                     # between drain attempts while the database keeps failing
HELD_WHY = ("malformed", "error")                # kept for the digest, never replayed by themselves
REPLAY = object()                                # queue marker: replay the dead letter now


def default_dir() -> Path:
    """Same folder as sgt_shadow.shadow_dir(), without importing the SGT engine at start-up."""
    env = os.environ.get("SGT_SHADOW_DIR")
    return Path(env) if env else Path.home() / "AmanAssociates_Sera" / "sgt_shadow"


def is_retryable(exc: BaseException) -> bool:
    """A database failure: a lock, a busy file, a disk or open error. Retrying can fix these."""
    import sqlcipher3.dbapi2 as sqlite3
    from sera_db.common import DatabaseError
    if isinstance(exc, (sqlite3.IntegrityError, sqlite3.ProgrammingError, sqlite3.DataError)):
        return False                # the same row fails the same way every time
    return isinstance(exc, (sqlite3.OperationalError, sqlite3.DatabaseError, DatabaseError))


class DeadLetter:
    """One JSON object per line: {ts, why, msg}. Order of writing is the order of replay."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.held_path = self.path.with_name(HELD_NAME)
        self._replaying = self.path.with_name(self.path.name + ".replaying")
        self._lock = threading.Lock()

    def put(self, msg: dict, why: str) -> bool:
        """Appends one row; never raises. False if the write failed."""
        try:
            line = json.dumps({"ts": time.time(), "why": str(why), "msg": msg},
                              ensure_ascii=False, default=str)
            target = self.held_path if why in HELD_WHY else self.path
            with self._lock:
                target.parent.mkdir(parents=True, exist_ok=True)
                with open(target, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
                    f.flush()
                    os.fsync(f.fileno())
            return True
        except Exception as e:
            print(f"[Capture Outbox] could not write the dead letter: {type(e).__name__}")
            return False

    @staticmethod
    def _read(path: Path) -> List[dict]:
        out: List[dict] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                    except Exception:
                        continue            # a half-written last line after a crash
                    if isinstance(entry, dict) and isinstance(entry.get("msg"), dict):
                        out.append(entry)
        except OSError:
            pass
        return out

    def take_all(self) -> List[dict]:
        """The rows to replay, oldest first. The file is renamed to `.replaying` (atomic) and stays
        there until finish_replay(), so a crash in the middle loses nothing; the next call picks
        up what a crashed run left."""
        with self._lock:
            try:
                if self.path.exists():
                    if self._replaying.exists():
                        with open(self._replaying, "ab") as dst, open(self.path, "rb") as src:
                            dst.write(src.read())
                        self.path.unlink()
                    else:
                        os.replace(self.path, self._replaying)
            except OSError as e:
                print(f"[Capture Outbox] could not take the dead letter: {type(e).__name__}")
            return [e["msg"] for e in self._read(self._replaying)]

    def finish_replay(self) -> None:
        with self._lock:
            try:
                self._replaying.unlink()
            except OSError:
                pass

    def count(self) -> int:
        """Rows waiting, including held ones."""
        with self._lock:
            return sum(len(self._read(p)) for p in (self.path, self._replaying, self.held_path) if p.exists())

    def pending(self) -> bool:
        """Rows that a replay would try (held ones do not count)."""
        with self._lock:
            return any(p.exists() and p.stat().st_size > 0 for p in (self.path, self._replaying))


class CaptureOutbox:
    """process(msg) writes one capture and returns its result; it raises when the write fails.
    health() returns the HealthMonitor (or None)."""

    def __init__(self, process: Callable[[dict], Any], dead_letter: DeadLetter,
                 health: Optional[Callable[[], Any]] = None, delays=RETRY_DELAYS_SEC,
                 sleep: Optional[Callable[[float], None]] = None, clock=time.monotonic):
        self._process = process
        self.dead = dead_letter
        self._health = health or (lambda: None)
        self._delays = tuple(delays)
        self._stop = threading.Event()
        self._sleep = sleep or (lambda s: self._stop.wait(s))
        self._clock = clock
        self._current: Optional[dict] = None
        self._backlog_failed_at: Optional[float] = None

    def handle(self, msg: dict, on_result=None):
        """One queued capture. Returns the result, or None if the row went to the dead letter."""
        self._current = msg
        try:
            if self.dead.pending():
                self._drain(on_result)
                if self.dead.pending():
                    self._dead(msg, "behind")          # behind the older rows, to keep their order
                    return None
            return self._attempt(msg)
        finally:
            self._current = None

    def replay(self, on_result=None) -> int:
        """Start-up and every REPLAY_EVERY_SEC: writes the dead letter in order. Returns rows written."""
        self._backlog_failed_at = None
        return self._drain(on_result)

    def _drain(self, on_result=None) -> int:
        if self._backlog_failed_at is not None and self._clock() - self._backlog_failed_at < BACKLOG_RETRY_GAP_SEC:
            return 0
        rows = self.dead.take_all()
        done = 0
        parked = True
        for i, row in enumerate(rows):
            if self._stop.is_set():
                parked = self._park(rows[i:])
                break
            try:
                result = self._process(row)
            except Exception as e:
                if is_retryable(e):
                    self._backlog_failed_at = self._clock()
                    parked = self._park(rows[i:])           # this row and every later one, in order
                    break
                self._dead(row, "malformed" if isinstance(e, ValueError) else "error", e)
                continue
            done += 1
            self._tell(on_result, row, result)
        else:
            self._backlog_failed_at = None
        if parked:
            self.dead.finish_replay()       # otherwise `.replaying` stays and the next replay repeats it
        if done:
            print(f"[Capture Outbox] {done} undelivered row(s) written")
        return done

    def _attempt(self, msg: dict):
        delays = iter(self._delays)
        while True:
            try:
                return self._process(msg)
            except Exception as e:
                if not is_retryable(e):
                    self._dead(msg, "malformed" if isinstance(e, ValueError) else "error", e)
                    return None
                wait = next(delays, None)
                if wait is None or self._stop.is_set():
                    self._dead(msg, "quit" if self._stop.is_set() else "database", e)
                    return None
                print(f"[Capture Outbox] write failed ({type(e).__name__}); trying again in {wait:g}s")
                self._sleep(wait)

    def _park(self, rows: List[dict]) -> bool:
        """Back into the dead letter after a failed replay; not counted again by the health record."""
        ok = all([self.dead.put(row, "database") for row in rows])
        if not ok:
            h = self._health()
            if h is not None:
                try:
                    h.disk_failed()
                except Exception:
                    pass
        return ok

    def _dead(self, msg: dict, why: str, exc: Optional[BaseException] = None) -> None:
        why_text = why if exc is None else f"{why}: {type(exc).__name__}"
        ok = self.dead.put(msg, why if why in HELD_WHY else why_text)
        h = self._health()
        if h is not None:
            try:
                h.row("dead_letter")
                if not ok:
                    h.disk_failed()
            except Exception:
                pass
        if not ok:
            print("[Capture Outbox] a row could not be saved anywhere")

    @staticmethod
    def _tell(on_result, msg, result) -> None:
        if on_result and result:
            try:
                on_result(msg, result)
            except Exception:
                pass

    def stop_and_flush(self, queued: List[dict]) -> int:
        """At quit: stops retry waits and puts the row in flight and the rows still queued into
        the dead letter (the next start replays them; a row that was just written is a harmless
        repeat). Returns the number written."""
        self._stop.set()
        rows = ([self._current] if self._current is not None else []) + list(queued)
        for row in rows:
            self._dead(row, "quit")
        return len(rows)
