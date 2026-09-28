"""Sera Sync v3 — change-log compaction and catch-up of a returning PC (blueprint §5, WP P4-4).

Compaction (``compact``), per stream (``origin``) of each DB file:
  - ``floor = min(max_seq acked by each active member)``: the acks are the final vectors each
    member reported at the end of its last session (``_sync_peer_vectors``). Members not seen for
    ``STALE_MEMBER_DAYS`` (60) are left out; a member never seen counts from its ``added_at``.
    The floor is never above what this PC holds itself (``_sync_vector``), and never goes down.
  - ``_sync_changes`` rows with ``origin_seq <= floor`` are deleted. The floors are kept in
    ``_sync_meta.compaction_floors`` (JSON ``{stream: seq}``); ``SyncEngine.compaction_floors()``
    reads them, and a peer whose vector for a stream is below its floor gets ``need_snapshot``.
  - Tombstones are kept for ``TOMBSTONE_KEEP_DAYS`` (180), then deleted.
  - Nothing is compacted when no other active member was seen in the last 60 days: either this
    PC is the one that was away (its peers' acks are stale, and compacting would delete its own
    unsent changes before they are sent), or it is alone in the office. So a PC's own changes
    are never compacted below what an active member has acked.

Catch-up of a PC below a peer's floor (``need_snapshot``):
  1. In the same session the returning PC has already sent its own unsent changes (both sides
     always send). ``SyncEngine`` checks the peer's final vectors cover this PC's own streams
     before going on; otherwise it waits for the next session.
  2. ``download_catch_up`` asks that peer for a snapshot (``{"t": "snapshot"}``, served on every
     PC, P2-6 / owner decision 2026-09-26) over the existing mutual-TLS transport, verifies it
     and stages it in ``incoming/catchup/`` (no file is replaced on a running app, §4.4).
  3. At the next start-up, **before any database is opened**, ``apply_pending_catch_up``
     installs it as this PC's: its device id and stream, the old DB's sequence state, peer
     vectors, conflicts list, local tables and ``_local_*`` tables, plus any own change the
     snapshot doesn't have yet (made after the download), then swaps the files in with a journal
     (old DBs to ``backups/pre-catchup-<ts>/``, §0 rule 3).

No PySide6 import (blueprint §0 rule 7). Never logs change contents.
"""

from __future__ import annotations

import datetime
import hmac
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Callable, Optional

_log = logging.getLogger("sera.sync.compaction")

FLOORS_META_KEY = "compaction_floors"
PEER_SEEN_META_KEY = "peer_seen"           # master.db: {device_id: last completed session, UTC ISO}
STALE_MEMBER_DAYS = 60
TOMBSTONE_KEEP_DAYS = 180
COMPACTION_INTERVAL_SECONDS = 6 * 3600

CATCHUP_DIRNAME = "catchup"                # incoming/catchup/: downloaded snapshot + pending.json
CATCHUP_PENDING_FILE = "pending.json"
CATCHUP_JOURNAL_FILE = "installing.json"
PRE_CATCHUP_PREFIX = "pre-catchup-"        # backups/pre-catchup-<ts>/: the DBs a catch-up replaced
CATCHUP_TIMEOUT_SECONDS = 120.0

MASTER_DB_NAME = "master.db"
RAW_DB_NAME = "rawPayload.db"
_DB_FILES = (("master", MASTER_DB_NAME), ("raw", RAW_DB_NAME))
_SIDECARS = ("-wal", "-shm", "-journal")


class CatchUpError(RuntimeError):
    """A catch-up snapshot could not be downloaded, checked or installed. The live databases are
    unchanged."""


# ---------------------------------------------------------------- floors

def read_floors(conn) -> dict:
    """``{stream: floor}`` stored in this DB file's ``_sync_meta`` (empty when never compacted)."""
    try:
        row = conn.execute("SELECT value FROM _sync_meta WHERE key = ?", (FLOORS_META_KEY,)).fetchone()
    except Exception:
        return {}
    if not row or not row[0]:
        return {}
    try:
        data = json.loads(row[0])
    except ValueError:
        _log.warning("compaction floors in _sync_meta are unreadable; treated as none")
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items()
            if isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) and v > 0}


def read_peer_seen(conn) -> dict:
    """``{device_id: ISO time}`` of the last completed session with each peer (master.db)."""
    try:
        row = conn.execute("SELECT value FROM _sync_meta WHERE key = ?", (PEER_SEEN_META_KEY,)).fetchone()
        data = json.loads(row[0]) if row and row[0] else {}
    except (ValueError, Exception):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(data, dict) else {}


def note_peer_seen(conn, device_id: str, when: Optional[str] = None) -> None:
    """Records a completed session with ``device_id`` (``SyncEngine`` calls it after each one).
    Peer vectors alone don't show it: they have rows only for streams that have changes."""
    when = when or _now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute("BEGIN IMMEDIATE")
    try:
        seen = read_peer_seen(conn)
        seen[device_id] = when
        conn.execute("INSERT INTO _sync_meta(key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (PEER_SEEN_META_KEY, json.dumps(seen, sort_keys=True)))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _parse_time(text) -> Optional[datetime.datetime]:
    if not isinstance(text, str) or not text:
        return None
    try:
        t = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone.utc)
    return t


def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def eligible_members(master_conn, raw_conn, admin_pubkey: str, own_device_id: str,
                     now: Optional[datetime.datetime] = None) -> tuple[list, int]:
    """(device ids of the active members seen within ``STALE_MEMBER_DAYS``, number of other active
    members). "Seen" = its last completed session (``note_peer_seen``) or the newest ``seen_at``
    of its peer vectors in either DB file, else its member record's ``added_at``. An unreadable
    time counts as seen (never compact on a guess)."""
    import sync_admin
    now = now or _now_utc()
    cutoff = now - datetime.timedelta(days=STALE_MEMBER_DAYS)
    members = [m for m in sync_admin.list_members(master_conn, admin_pubkey, include_revoked=False)
               if m.get("device_id") != own_device_id]
    peer_seen = read_peer_seen(master_conn)
    eligible = []
    for m in members:
        dev = m["device_id"]
        seen_texts = [peer_seen[dev]] if dev in peer_seen else []
        for conn in (master_conn, raw_conn):
            if conn is None:
                continue
            seen_texts += [r[0] for r in conn.execute(
                "SELECT seen_at FROM _sync_peer_vectors WHERE device_id = ?", (dev,))]
        if seen_texts:
            parsed = [_parse_time(t) for t in seen_texts]
            seen = None if any(p is None for p in parsed) else max(parsed)
            unreadable = seen is None
        else:
            seen = _parse_time(m.get("added_at"))
            unreadable = seen is None
        if unreadable or seen >= cutoff:
            eligible.append(dev)
    return eligible, len(members)


def compute_floors(conn, eligible: list) -> dict:
    """``{stream: floor}`` for every stream in this DB file's ``_sync_vector``: the lowest ack of
    the ``eligible`` members (0 for a member that never acked the stream), capped at what this PC
    holds. The caller makes sure ``eligible`` is not empty."""
    vector = {o: int(s) for o, s in conn.execute("SELECT origin, max_seq FROM _sync_vector")}
    acks = {dev: {} for dev in eligible}
    if eligible:
        marks = ", ".join("?" for _ in eligible)
        for dev, origin, seq in conn.execute(
                f"SELECT device_id, origin, max_seq FROM _sync_peer_vectors WHERE device_id IN ({marks})",
                list(eligible)):
            acks[dev][origin] = int(seq or 0)
    floors = {}
    for origin, have in vector.items():
        floors[origin] = min([have] + [a.get(origin, 0) for a in acks.values()])
    return floors


def _compact_file(conn, eligible: list, now: datetime.datetime) -> dict:
    """One DB file, in one transaction. Returns ``{"deleted", "tombstones", "floors"}``."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        old = read_floors(conn)
        new = dict(old)
        deleted = 0
        for origin, floor in compute_floors(conn, eligible).items():
            if floor <= old.get(origin, 0):
                continue
            deleted += conn.execute("DELETE FROM _sync_changes WHERE origin = ? AND origin_seq <= ?",
                                    (origin, floor)).rowcount
            new[origin] = floor
        cutoff_ms = int((now - datetime.timedelta(days=TOMBSTONE_KEEP_DAYS)).timestamp() * 1000)
        # HLC strings start with the 13-digit millisecond time, so they compare as text.
        tombstones = conn.execute("DELETE FROM _sync_tombstones WHERE hlc < ?",
                                  (f"{max(cutoff_ms, 0):013d}",)).rowcount
        if new != old:
            conn.execute("INSERT INTO _sync_meta(key, value) VALUES (?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                         (FLOORS_META_KEY, json.dumps(new, sort_keys=True)))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {"deleted": deleted, "tombstones": tombstones, "floors": new}


def compact(master_path: str, raw_path: Optional[str], hex_key: str, *, admin_pubkey: str,
            own_device_id: str, now: Optional[datetime.datetime] = None, timeout: float = 5.0) -> dict:
    """Compacts both DB files (see the module docstring). Returns
    ``{"skipped": reason | None, "master": {...}, "raw": {...}}``."""
    import sync_capture
    now = now or _now_utc()
    master = sync_capture._open(master_path, hex_key, timeout)
    raw = None
    try:
        if raw_path and os.path.exists(raw_path):
            raw = sync_capture._open(raw_path, hex_key, timeout)
        eligible, others = eligible_members(master, raw, admin_pubkey, own_device_id, now)
        if not eligible:
            reason = ("no other active member was seen in the last %d days" % STALE_MEMBER_DAYS
                      if others else "no other active member")
            return {"skipped": reason}
        out = {"skipped": None, "master": _compact_file(master, eligible, now)}
        if raw is not None:
            out["raw"] = _compact_file(raw, eligible, now)
        total = sum(out[w]["deleted"] for w in ("master", "raw") if w in out)
        tombs = sum(out[w]["tombstones"] for w in ("master", "raw") if w in out)
        if total or tombs:
            _log.info("compaction: %d change(s) and %d old tombstone(s) removed", total, tombs)
        return out
    finally:
        if raw is not None:
            raw.close()
        master.close()


# ---------------------------------------------------------------- catch-up: download + stage

def _stage_dir(app_dir) -> Path:
    return Path(app_dir) / "incoming" / CATCHUP_DIRNAME


def has_pending_catch_up(app_dir) -> bool:
    d = _stage_dir(app_dir)
    return (d / CATCHUP_PENDING_FILE).exists() or (d / CATCHUP_JOURNAL_FILE).exists()


def _file_vector(path: Path, hex_key: str, origin: str) -> int:
    import sync_capture
    conn = sync_capture._open(str(path), hex_key, 5.0)
    try:
        row = conn.execute("SELECT max_seq FROM _sync_vector WHERE origin = ?", (origin,)).fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def download_catch_up(app_dir, hex_key: str, open_session: Callable[[], object], source_device: str,
                      need_own: dict, *, timeout: float = CATCHUP_TIMEOUT_SECONDS) -> Path:
    """Downloads a snapshot from ``source_device`` (``open_session()`` returns a mutual-TLS
    ``Session`` to it) and stages it for the next start-up. ``need_own`` is ``{own stream: seq}``:
    the snapshot must hold this PC's own changes at least that far, or it is refused (they would
    otherwise be lost by the install). Returns the path of ``incoming/catchup/pending.json``.
    Raises ``CatchUpError``; nothing is staged then."""
    import sera_keys
    import sync_shadow
    import sync_snapshot
    import sync_transport
    app = Path(app_dir)
    office = sera_keys.load_office(app)
    if office is None:
        raise CatchUpError("this PC has no office key")
    if has_pending_catch_up(app):
        raise CatchUpError("a catch-up is already waiting for a restart")
    stage = _stage_dir(app)
    new_dir = stage / "new"
    shutil.rmtree(stage, ignore_errors=True)
    new_dir.mkdir(parents=True)
    try:
        session = open_session()
        try:
            session.send({"t": sync_snapshot.FRAME_SNAPSHOT})
            manifest = session.recv(wait=timeout)
            if not isinstance(manifest, dict) or manifest.get("t") != sync_snapshot.FRAME_MANIFEST:
                raise CatchUpError("the other PC did not send a snapshot")
            if manifest.get("office_id") != office.office_id or not hmac.compare_digest(
                    str(manifest.get("key_id") or ""), office.key_id):
                raise CatchUpError("the snapshot is from another office or another key")
            files = manifest.get("files")
            if not isinstance(files, list) or len(files) > len(sync_snapshot.ALLOWED_DB_NAMES):
                raise CatchUpError("the snapshot manifest is invalid")
            staged = []
            for f in files:
                name = f.get("name") if isinstance(f, dict) else None
                size, sha = (f.get("size"), f.get("sha256")) if isinstance(f, dict) else (None, None)
                if name not in sync_snapshot.ALLOWED_DB_NAMES or name in [s["name"] for s in staged]:
                    raise CatchUpError("the snapshot manifest names an unexpected file")
                if type(size) is not int or size < 0 or not isinstance(sha, str) or len(sha) != 64:
                    raise CatchUpError("the snapshot manifest is invalid")
                try:
                    got = session.recv_file(new_dir / name, size)
                except sync_transport.ProtocolError as exc:
                    raise CatchUpError(f"bad transfer of {name}: {exc}") from None
                if got != sha:
                    raise CatchUpError(f"{name} arrived damaged")
                staged.append({"name": name, "sha256": sha})
        finally:
            session.close()
        if {s["name"] for s in staged} != set(sync_snapshot.ALLOWED_DB_NAMES):
            raise CatchUpError("the snapshot needs both master.db and rawPayload.db")
        for s in staged:
            sync_snapshot._verify_downloaded_db(new_dir / s["name"], hex_key)
        for stream, seq in need_own.items():
            import sync_engine
            which = sync_engine.stream_db(stream)
            name = MASTER_DB_NAME if which == "master" else RAW_DB_NAME
            if _file_vector(new_dir / name, hex_key, stream) < seq:
                raise CatchUpError("the snapshot doesn't hold all of this PC's own changes yet")
        # Checksums of the files as they are now: opening them for the checks above may have
        # changed their header (journal mode), which the install compares against.
        for s in staged:
            checkpoint(new_dir / s["name"], hex_key)
            s["sha256"] = sync_shadow._sha256(new_dir / s["name"])
        pending = stage / CATCHUP_PENDING_FILE
        sync_shadow._write_json(pending, {"staged_at": _now_utc().replace(microsecond=0).isoformat(),
                                          "source_device": source_device, "files": staged,
                                          "pid": os.getpid()})
    except BaseException as exc:
        shutil.rmtree(stage, ignore_errors=True)
        if isinstance(exc, CatchUpError) or not isinstance(exc, Exception):
            raise
        raise CatchUpError(f"the catch-up snapshot could not be downloaded: {exc}") from exc
    _log.warning("catch-up snapshot from %s staged; it is installed at the next start of Sera",
                 source_device[:8])
    return pending


# ---------------------------------------------------------------- journaled swap (shared)

def _live_moves(app: Path, pre_dir: Path) -> list:
    moves = []
    for _which, name in _DB_FILES:
        for suffix in (*_SIDECARS, ""):
            src = app / f"{name}{suffix}"
            if src.exists():
                moves.append((str(src), str(pre_dir / src.name)))
    return moves


def unique_dir(parent: Path, prefix: str) -> Path:
    ts = time.strftime("%Y%m%d_%H%M%S")
    d = parent / f"{prefix}{ts}"
    n = 1
    while d.exists():
        d = parent / f"{prefix}{ts}_{n}"
        n += 1
    return d


def journaled_swap(app: Path, journal_path: Path, pre_dir: Path, prepared: dict,
                   undo_on_error: bool = True, extra: Optional[dict] = None) -> None:
    """Moves the live DBs (and sidecars) to ``pre_dir`` and the ``prepared`` files
    (``{file name: path}``) into place, with a journal so a crash is finished or put back at the
    next start (``recover_swap``). A failure puts everything back and re-raises."""
    import sync_shadow
    moves = _live_moves(app, pre_dir)
    moves += [(str(path), str(app / name)) for name, path in prepared.items()]
    pre_dir.mkdir(parents=True, exist_ok=True)
    sync_shadow._write_json(journal_path, dict(extra or {}, pre_dir=str(pre_dir), moves=moves))
    try:
        for src, dst in moves:
            sync_shadow._replace_when_unlocked(src, dst)
    except BaseException:
        if not undo_on_error:
            raise
        sync_shadow._undo_moves(moves)
        try:
            journal_path.unlink()
        except OSError:
            pass
        raise


def recover_swap(journal: dict) -> bool:
    """After a crash during ``journaled_swap``: True if every move had finished (the caller
    completes the install), else puts the moves back and returns False."""
    import sync_shadow
    moves = journal.get("moves") or []
    last = moves[-1] if moves else None
    if last and Path(last[1]).exists() and not Path(last[0]).exists():
        return True
    sync_shadow._undo_moves(moves)
    return False


def seal_live(app: Path, hex_key: str) -> None:
    """Seals what the previous run captured but didn't seal yet (it stopped mid-way), so those
    changes are numbered in the old DB and can be carried over."""
    import sync_admin
    import sync_capture
    master, raw = app / MASTER_DB_NAME, app / RAW_DB_NAME

    def signer():
        return sync_admin.load_admin_key(app)

    seq_state = sync_capture.seq_state_path(str(master))
    if master.exists():
        sync_capture.seal(str(master), hex_key, "master", get_signer=signer, seq_state=seq_state)
    if raw.exists():
        sync_capture.seal(str(raw), hex_key, "raw", master_path=str(master), get_signer=signer,
                          seq_state=seq_state)


def checkpoint(path: Path, hex_key: str) -> None:
    """Folds a prepared file's WAL back into it, so the file moved into place is complete."""
    import sync_capture
    conn = sync_capture._open(str(path), hex_key, 10.0)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        conn.execute("PRAGMA journal_mode = DELETE;")
    finally:
        conn.close()


# ---------------------------------------------------------------- catch-up: install

def _prepare_file(path: Path, old_live: Path, hex_key: str, which: str, device_id: str,
                  new_master: Path, old_master: Path) -> None:
    """The downloaded snapshot as this PC's: device id and stream; the old DB's sequence state,
    peer vectors and conflicts list; mode live; the other PC's unsealed pending rows dropped;
    local-mode tables from the old DB with client ids translated by gid."""
    import sync_capture
    import sync_shadow
    import sync_tables
    conn = sync_capture._open(str(path), hex_key, 10.0)
    attached = []
    try:
        def attach(alias, p):
            escaped = str(p).replace("'", "''")
            conn.execute(f"ATTACH DATABASE '{escaped}' AS {alias} KEY \"x'{hex_key}'\";")
            attached.append(alias)
        attach("old", old_live)
        if which == "raw":
            attach("om", old_master)
            attach("nm", new_master)
            id_map = ("SELECT oc.id AS old_id, nc.id AS new_id FROM om.clients oc "
                      "JOIN nm.clients nc ON nc.gid = oc.gid")
        else:
            id_map = ("SELECT oc.id AS old_id, nc.id AS new_id FROM old.clients oc "
                      "JOIN main.clients nc ON nc.gid = oc.gid")
        conn.execute("BEGIN IMMEDIATE")
        try:
            sync_tables.set_sync_device_id(conn, which, device_id)
            conn.execute("DELETE FROM main._sync_pending")
            conn.execute("UPDATE main._sync_flags SET value = 0 WHERE name = 'applying'")
            meta = dict(conn.execute("SELECT key, value FROM old._sync_meta").fetchall())
            new_meta = dict(conn.execute("SELECT key, value FROM main._sync_meta").fetchall())
            updates = {"mode": "live"}
            for key in ("next_seq", "next_seq_stream"):
                if meta.get(key) is not None:
                    updates[key] = meta[key]
            last_hlc = max(meta.get("last_hlc") or "", new_meta.get("last_hlc") or "")
            if last_hlc:
                updates["last_hlc"] = last_hlc
            for key, value in updates.items():
                conn.execute("INSERT INTO main._sync_meta(key, value) VALUES (?, ?) "
                             "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
            for table in ("_sync_peer_vectors", "_sync_conflicts"):
                conn.execute(f"DELETE FROM main.{table}")
                conn.execute(f"INSERT INTO main.{table} SELECT * FROM old.{table}")
            for table in sync_shadow._local_table_names(which):
                in_both = all(conn.execute(f"SELECT 1 FROM {s}.sqlite_master WHERE type='table' AND name=?",
                                           (table,)).fetchone() for s in ("main", "old"))
                if in_both:
                    sync_shadow._copy_client_mapped(conn, table, "old", id_map)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        for alias in attached:
            try:
                conn.execute(f"DETACH DATABASE {alias}")
            except Exception:
                pass
        conn.close()


def _fail_catch_up(app: Path, reason: str) -> None:
    stage = _stage_dir(app)
    try:
        pending = stage / CATCHUP_PENDING_FILE
        if pending.exists():
            os.replace(pending, app / "incoming" / f"{CATCHUP_DIRNAME}.{CATCHUP_PENDING_FILE}.failed")
    except OSError:
        pass
    shutil.rmtree(stage, ignore_errors=True)
    _log.error("catch-up not installed, databases unchanged: %s", reason)


def _finish_catch_up(app: Path, pre_dir: Path, source: str) -> dict:
    shutil.rmtree(_stage_dir(app), ignore_errors=True)
    _log.warning("catch-up installed from %s; the previous databases are in %s", source[:8], pre_dir)
    return {"pre_catchup_dir": str(pre_dir), "source_device": source}


def cancel_catch_up(app_dir) -> None:
    """Drops a staged catch-up that hasn't started installing (e.g. a restore was staged after it)."""
    stage = _stage_dir(app_dir)
    if (stage / CATCHUP_JOURNAL_FILE).exists():
        return
    shutil.rmtree(stage, ignore_errors=True)


def apply_pending_catch_up(app_dir, hex_key: Optional[str] = None, *,
                           _undo_on_error: bool = True) -> Optional[dict]:
    """At start-up, **before any database is opened** (``main.py``). None when nothing is staged;
    else installs the staged snapshot (module docstring) and returns ``{"pre_catchup_dir",
    "source_device", "carried"}``. Raises ``CatchUpError`` when it can't (live DBs unchanged, not
    retried; the next ``need_snapshot`` stages a new one). A key that can't be loaded propagates
    without touching anything, so it is retried at the next start."""
    import sera_keys
    import sync_shadow
    import sync_snapshot
    app = Path(app_dir)
    stage = _stage_dir(app)
    journal = sync_shadow._read_json(stage / CATCHUP_JOURNAL_FILE)
    if journal is not None:
        if recover_swap(journal):
            return _finish_catch_up(app, Path(journal["pre_dir"]), str(journal.get("source_device") or ""))
        try:
            (stage / CATCHUP_JOURNAL_FILE).unlink()
        except OSError:
            pass
        _fail_catch_up(app, "the previous start-up was interrupted while moving files; put back")
        raise CatchUpError("installing the catch-up was interrupted and has been put back")
    pending_path = stage / CATCHUP_PENDING_FILE
    if not pending_path.exists():
        return None
    pending = sync_shadow._read_json(pending_path)
    if pending is None or not isinstance(pending.get("files"), list):
        _fail_catch_up(app, "pending.json is unreadable")
        raise CatchUpError("the staged catch-up is unreadable")
    hex_key = hex_key or sync_shadow._office_hex(app)
    source = str(pending.get("source_device") or "")
    new_dir = stage / "new"
    prep = stage / "prep"
    carried = 0
    try:
        names = []
        for f in pending["files"]:
            name = f.get("name") if isinstance(f, dict) else None
            if name not in sync_snapshot.ALLOWED_DB_NAMES or name in names:
                raise CatchUpError("the staged file list is invalid")
            path = new_dir / name
            if not path.is_file() or sync_shadow._sha256(path) != f.get("sha256"):
                raise CatchUpError(f"the staged {name} changed or is missing")
            sync_snapshot._verify_downloaded_db(path, hex_key)
            names.append(name)
        if set(names) != set(sync_snapshot.ALLOWED_DB_NAMES):
            raise CatchUpError("the staged catch-up needs both master.db and rawPayload.db")
        if not (app / MASTER_DB_NAME).exists():
            raise CatchUpError("this PC has no database to catch up")
        if not sync_shadow._wait_for_process_exit(pending.get("pid"), sync_shadow.GOLIVE_OLD_PROCESS_WAIT_SECONDS):
            _log.warning("catch-up: the previous Sera process was still running; trying anyway")
        device_id = sync_shadow._own_device_id(app)
        office = sera_keys.load_office(app)
        admin_pubkey = office.admin_pubkey if office else None
        seal_live(app, hex_key)

        shutil.rmtree(prep, ignore_errors=True)
        prep.mkdir(parents=True)
        prepared = {}
        old_master = app / MASTER_DB_NAME
        new_master = prep / MASTER_DB_NAME
        for which, name in _DB_FILES:
            target = prep / name
            shutil.copy2(new_dir / name, target)
            _prepare_file(target, app / name, hex_key, which, device_id, new_master, old_master)
            if which == "master":
                sync_shadow._carry_over_local_state(target, old_master, hex_key)
            if (app / name).exists():
                carried += sync_shadow._carry_own_changes(
                    target, app / name, hex_key, device_id, which, admin_pubkey,
                    new_master if which == "raw" else None)
            checkpoint(target, hex_key)
            prepared[name] = target
    except BaseException as exc:
        shutil.rmtree(prep, ignore_errors=True)
        if not isinstance(exc, Exception):
            raise
        _fail_catch_up(app, str(exc) if isinstance(exc, CatchUpError) else type(exc).__name__)
        if isinstance(exc, CatchUpError):
            raise
        raise CatchUpError(f"the catch-up could not be prepared: {exc}") from exc

    pre_dir = unique_dir(app / "backups", PRE_CATCHUP_PREFIX)
    journal_path = stage / CATCHUP_JOURNAL_FILE
    try:
        journaled_swap(app, journal_path, pre_dir, prepared, undo_on_error=_undo_on_error,
                       extra={"source_device": source})
    except BaseException as exc:
        if not _undo_on_error:
            raise
        _fail_catch_up(app, f"files could not be moved ({type(exc).__name__}); put back")
        if not isinstance(exc, Exception):
            raise
        raise CatchUpError(f"the catch-up could not be installed and was put back: {exc}") from exc
    result = _finish_catch_up(app, pre_dir, source)
    result["carried"] = carried
    return result
