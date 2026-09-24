"""Sera Sync v3 — capture triggers, sealer and hybrid logical clock (blueprint §5, WP P3-3).

  - ``ensure_capture_triggers(conn, db_name)`` creates the ``_sync_ai_/_sync_au_/_sync_ad_/_sync_ak_<t>``
    triggers for every replicated table (registry: ``sync_schema``). They write ``_sync_pending``.
  - ``HLC``: ``f"{ms:013d}.{counter:04d}.{device_id}"`` strings, compared as strings.
  - ``seal(db_path, hex_key, db_name, ...)`` turns pending rows into ``_sync_changes`` records
    (one HLC per row, per-origin sequence numbers), updates ``_sync_clock`` / ``_sync_tombstones``
    and empties ``_sync_pending``, in one transaction on its own private connection.
  - ``sign_change`` / ``verify_change`` sign admin-scoped changes (``admin_lww``) with the office
    admin key (Ed25519). P3-4 verifies with ``verify_change``.

Conventions P3-4 / P3-5 rely on:
  - ``row_key`` is always a compact JSON array: ``["<gid>"]`` for gid tables, ``["<key>"]`` for a
    natural key, ``["<client gid>", "<column gid>"]`` for composite keys (FK parts as gids;
    ``cell_formatting.column_key`` keeps the literal ``"services"``).
  - ``data`` (upserts) is a JSON object of the changed columns. It never holds local ids, ``gid``
    or the row-key columns; FK columns hold gids (NULL when the referenced row is unknown).
  - ``_sync_clock`` has one row per replicated column plus a presence row with ``col = '*'``.
    A row with no ``'*'`` clock row has never been sealed (its first seal sends all columns).
  - A deleted row loses its clock rows and gets a tombstone; sealing it again later (the same
    natural/composite key re-created locally) removes the local tombstone and sends all columns.
  - ``origin`` is the stream id (``<device_id>:m`` / ``<device_id>:r``); ``_sync_vector[origin]``
    of the own stream follows ``next_seq - 1``. ``_sync_meta.next_seq_stream`` records which
    stream ``next_seq`` belongs to; a new stream (device_id changed) starts at 1.
  - The highest seq issued per stream is also kept outside the database, in
    ``keys/sync_seq.json`` (owner decision 2026-09-25, option A). ``next_seq`` is never below
    that mark + 1, so replacing the DB file (restore_from, a staged swap) can't reuse numbers
    other PCs already hold.

Capture only happens while ``_sync_meta.mode`` is ``shadow`` or ``live`` (the triggers check it),
so a PC in mode ``off`` doesn't fill ``_sync_pending`` forever.

No PySide6 import (blueprint §0 rule 7).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

import sync_schema
from sync_schema import ADMIN_LWW, MASTER_DB, RAW_DB

_log = logging.getLogger("sera.sync.capture")

ACTIVE_MODES = ("shadow", "live")
PRESENCE_COL = "*"
MAX_CLOCK_AHEAD_MS = 3_600_000
_MAX_COUNTER = 9999
SEAL_TIMER_SECONDS = 5.0
SEAL_BATCH_ROWS = 500          # pending rows per seal transaction (bounds the write-lock time)
# Domain tag in front of every signed change, so a change signature can never be taken for a
# P2-2 membership/staff_change record signature made with the same admin key, or vice versa.
CHANGE_SIG_DOMAIN = b"sera-sync-v3-change\x00"
_GID_RE = re.compile(r"[0-9a-f]{32}\Z")
SEQ_STATE_FILE = "sync_seq.json"


# ---------------------------------------------------------------- HLC

class ClockAhead(Exception):
    """A remote HLC is more than an hour ahead of this PC's clock. The change must be parked
    (reason ``clock_ahead``) and the local clock must not move."""

    def __init__(self, remote_hlc: str, ahead_ms: int):
        super().__init__(f"remote clock is ahead by {ahead_ms // 60000} minutes")
        self.remote_hlc = remote_hlc
        self.ahead_ms = ahead_ms


@dataclass(frozen=True, order=True)
class HLC:
    ms: int
    counter: int
    device_id: str

    def __str__(self) -> str:
        return f"{self.ms:013d}.{self.counter:04d}.{self.device_id}"

    @classmethod
    def parse(cls, text: str) -> "HLC":
        try:
            ms, counter, device_id = text.split(".", 2)
            if len(ms) != 13 or len(counter) != 4 or not ms.isdigit() or not counter.isdigit():
                raise ValueError
            return cls(int(ms), int(counter), device_id)
        except (ValueError, AttributeError):
            raise ValueError(f"not an HLC: {text!r}") from None


def _now_ms() -> int:
    return int(time.time() * 1000)


def _normalise(ms: int, counter: int) -> tuple[int, int]:
    # The counter has 4 digits; past 9999 the logical time moves to the next millisecond so
    # string order stays correct.
    if counter > _MAX_COUNTER:
        return ms + 1, 0
    return ms, counter


def tick(last: Optional[str], device_id: str, now_ms: Optional[int] = None) -> str:
    """HLC for a local event. ``last`` is the previous local HLC ('' or None when there is none)."""
    pt = _now_ms() if now_ms is None else now_ms
    if last:
        l = HLC.parse(last)
        if pt > l.ms:
            ms, c = pt, 0
        else:
            ms, c = _normalise(l.ms, l.counter + 1)
    else:
        ms, c = pt, 0
    return str(HLC(ms, c, device_id))


def observe(last: Optional[str], remote: str, device_id: str, now_ms: Optional[int] = None) -> str:
    """HLC after receiving ``remote`` (standard HLC receive rule).

    Raises ``ClockAhead`` if ``remote`` is more than an hour ahead of the physical clock; the
    caller parks the change and keeps ``last`` unchanged.
    """
    pt = _now_ms() if now_ms is None else now_ms
    r = HLC.parse(remote)
    if r.ms > pt + MAX_CLOCK_AHEAD_MS:
        raise ClockAhead(remote, r.ms - pt)
    l = HLC.parse(last) if last else HLC(0, 0, device_id)
    ms = max(l.ms, r.ms, pt)
    if ms == l.ms and ms == r.ms:
        c = max(l.counter, r.counter) + 1
    elif ms == l.ms:
        c = l.counter + 1
    elif ms == r.ms:
        c = r.counter + 1
    else:
        c = 0
    ms, c = _normalise(ms, c)
    return str(HLC(ms, c, device_id))


# Highest HLC handed out in this process per device, so master.db and rawPayload.db (each with
# its own last_hlc) never go backwards relative to each other within one run.
_process_hlc: dict[str, str] = {}
_process_hlc_lock = threading.Lock()


def _tick_shared(db_last: str, device_id: str, now_ms: Optional[int]) -> str:
    with _process_hlc_lock:
        last = max(db_last or "", _process_hlc.get(device_id, ""))
        new = tick(last, device_id, now_ms)
        _process_hlc[device_id] = new
        return new


# ---------------------------------------------------------------- triggers

def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _lit(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


_CAPTURE_WHEN = (
    "(SELECT value FROM _sync_flags WHERE name='applying') IS NOT 1 "
    "AND (SELECT value FROM _sync_meta WHERE key='mode') IN ('shadow','live')"
)


def _key_sql(spec, ref: str) -> str:
    """SQL expression (inside a trigger) giving the row key of ``ref`` (OLD/NEW) as a JSON array,
    FK parts translated to gids. Only used for tables in the same DB file (all composite keys are
    in master.db)."""
    parts = []
    for col in spec.row_key:
        target = spec.fk.get(col)
        if target is None:
            parts.append(f"{ref}.{_q(col)}")
        elif spec.name == "cell_formatting" and col == "column_key":
            parts.append(
                f"CASE WHEN CAST(CAST({ref}.column_key AS INTEGER) AS TEXT) = {ref}.column_key "
                f"THEN (SELECT gid FROM {_q(target)} WHERE id = CAST({ref}.column_key AS INTEGER)) "
                f"ELSE {ref}.column_key END"
            )
        else:
            parts.append(f"(SELECT gid FROM {_q(target)} WHERE id = {ref}.{_q(col)})")
    return "json_array(" + ", ".join(parts) + ")"


def trigger_names(table: str) -> tuple[str, str, str, str]:
    return (f"_sync_ai_{table}", f"_sync_au_{table}", f"_sync_ad_{table}", f"_sync_ak_{table}")


def ensure_capture_triggers(conn, db_name: str) -> list[str]:
    """Creates the capture triggers for every replicated table of ``db_name`` that exists.

    AFTER INSERT / AFTER UPDATE record ``(tbl, rowid, 'upsert')``. AFTER DELETE records
    ``'delete'`` with the OLD row key. ``_sync_ak_<t>`` records a delete of the OLD key when an
    UPDATE changes the row key itself (e.g. client_values moved to another client), so the old
    key doesn't stay alive on other PCs. Returns the tables processed.
    """
    live = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    done = []
    for spec in sync_schema.replicated_tables_for(db_name):
        if spec.name not in live:
            continue
        t = spec.name
        ai, au, ad, ak = trigger_names(t)
        tl = _lit(t)
        conn.execute(
            f"CREATE TRIGGER IF NOT EXISTS {ai} AFTER INSERT ON {_q(t)} WHEN {_CAPTURE_WHEN} "
            f"BEGIN INSERT INTO _sync_pending(tbl, rid, op, key_json) VALUES ({tl}, NEW.rowid, 'upsert', NULL); END;"
        )
        conn.execute(
            f"CREATE TRIGGER IF NOT EXISTS {au} AFTER UPDATE ON {_q(t)} WHEN {_CAPTURE_WHEN} "
            f"BEGIN INSERT INTO _sync_pending(tbl, rid, op, key_json) VALUES ({tl}, NEW.rowid, 'upsert', NULL); END;"
        )
        conn.execute(
            f"CREATE TRIGGER IF NOT EXISTS {ad} AFTER DELETE ON {_q(t)} WHEN {_CAPTURE_WHEN} "
            f"BEGIN INSERT INTO _sync_pending(tbl, rid, op, key_json) "
            f"VALUES ({tl}, OLD.rowid, 'delete', {_key_sql(spec, 'OLD')}); END;"
        )
        if spec.row_key == ("gid",):
            changed = "OLD.gid IS NOT NULL AND OLD.gid IS NOT NEW.gid"
        else:
            changed = "(" + " OR ".join(f"OLD.{_q(c)} IS NOT NEW.{_q(c)}" for c in spec.row_key) + ")"
        conn.execute(
            f"CREATE TRIGGER IF NOT EXISTS {ak} AFTER UPDATE ON {_q(t)} WHEN {_CAPTURE_WHEN} AND {changed} "
            f"BEGIN INSERT INTO _sync_pending(tbl, rid, op, key_json) "
            f"VALUES ({tl}, OLD.rowid, 'delete', {_key_sql(spec, 'OLD')}); END;"
        )
        done.append(t)
    return done


# ---------------------------------------------------------------- values and signatures

def canonical_json(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def key_text(parts: Iterable) -> str:
    return json.dumps(list(parts), separators=(",", ":"), ensure_ascii=False)


def _json_value(v):
    if isinstance(v, (bytes, bytearray, memoryview)):
        return {"$b64": base64.b64encode(bytes(v)).decode("ascii")}
    return v


def vhash(value) -> str:
    """sha256 over a type-tagged, normalised form of a column value."""
    if value is None:
        norm = "n:"
    elif isinstance(value, bool):
        norm = f"i:{int(value)}"
    elif isinstance(value, int):
        norm = f"i:{value}"
    elif isinstance(value, float):
        norm = f"f:{value!r}"
    elif isinstance(value, (bytes, bytearray, memoryview)):
        norm = "b:" + bytes(value).hex()
    else:
        norm = "s:" + str(value)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def change_body(change: dict) -> dict:
    """The signed part of a change: everything except ``sig`` and the local ``seq``."""
    data = change.get("data")
    if isinstance(data, str):
        data = json.loads(data)
    return {
        "origin": change["origin"], "origin_seq": int(change["origin_seq"]), "hlc": change["hlc"],
        "tbl": change["tbl"], "row_key": change["row_key"], "op": change["op"], "data": data,
    }


def sign_change(change: dict, private_key) -> str:
    """Base64 Ed25519 signature over ``CHANGE_SIG_DOMAIN + canonical_json(change_body(change))``."""
    return base64.b64encode(private_key.sign(_signed_bytes(change))).decode("ascii")


def _signed_bytes(change: dict) -> bytes:
    return CHANGE_SIG_DOMAIN + canonical_json(change_body(change))


def verify_change(change: dict, admin_pubkey: str) -> bool:
    """True if ``change['sig']`` is a valid office-admin signature (P3-4)."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    sig = change.get("sig")
    if not isinstance(sig, str) or not isinstance(admin_pubkey, str):
        return False
    try:
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(admin_pubkey, validate=True))
        pub.verify(base64.b64decode(sig, validate=True), _signed_bytes(change))
        return True
    except (InvalidSignature, ValueError, binascii.Error, KeyError, TypeError):
        return False


# ---------------------------------------------------------------- sealer

def _open(path: str, hex_key: str, timeout: float):
    """Private connection for the sealer. It never calls the sealer (no recursion)."""
    import sqlcipher3.dbapi2 as sqlite3
    conn = sqlite3.connect(path, timeout=timeout, isolation_level=None)
    conn.execute(f"PRAGMA key = \"x'{hex_key}'\";")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


def _meta(conn, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM _sync_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _set_meta(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO _sync_meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def read_mode(conn) -> str:
    try:
        return _meta(conn, "mode") or "off"
    except Exception:
        return "off"


class _GidLookup:
    """id → gid and gid → id for FK targets, in this DB or (cross-DB) in master.db."""

    def __init__(self, conn, db_name: str, master_opener: Optional[Callable]):
        self.conn = conn
        self.db_name = db_name
        self._master_opener = master_opener
        self._master = None
        self._to_gid: dict = {}
        self._to_id: dict = {}

    def _conn_for(self, table: str):
        spec = sync_schema.REGISTRY.get(table)
        if spec is None or spec.db == self.db_name:
            return self.conn
        if self._master is None:
            if self._master_opener is None:
                raise RuntimeError(f"no {spec.db} connection to translate {table} ids")
            self._master = self._master_opener()
        return self._master

    def gid(self, table: str, local_id):
        if local_id is None:
            return None
        k = (table, local_id)
        if k not in self._to_gid:
            row = self._conn_for(table).execute(
                f"SELECT gid FROM {_q(table)} WHERE id = ?", (local_id,)).fetchone()
            self._to_gid[k] = row[0] if row else None
        return self._to_gid[k]

    def local_id(self, table: str, gid):
        if gid is None:
            return None
        k = (table, gid)
        if k not in self._to_id:
            row = self._conn_for(table).execute(
                f"SELECT id FROM {_q(table)} WHERE gid = ?", (gid,)).fetchone()
            self._to_id[k] = row[0] if row else None
        return self._to_id[k]

    def reset(self):
        """Forget cached translations (between seal transactions)."""
        self._to_gid.clear()
        self._to_id.clear()

    def close(self):
        if self._master is not None:
            try:
                self._master.close()
            except Exception:
                pass
            self._master = None


def _columns(conn, table: str, cache: dict) -> list[tuple[str, bool]]:
    """(name, is_integer_primary_key) for every column."""
    if table not in cache:
        info = conn.execute(f"PRAGMA table_info({_q(table)})").fetchall()
        pk_cols = [r for r in info if r[5]]
        cache[table] = [
            (r[1], len(pk_cols) == 1 and r[5] == 1 and (r[2] or "").upper() == "INTEGER")
            for r in info
        ]
    return cache[table]


def _data_columns(spec, cols) -> list[str]:
    skip = set(spec.row_key) | {"gid"}
    return [name for name, is_rowid_alias in cols if not is_rowid_alias and name not in skip]


def _translate_key_part(spec, col, value, gids: _GidLookup):
    target = spec.fk.get(col)
    if target is None:
        return value
    if spec.name == "cell_formatting" and col == "column_key":
        text = "" if value is None else str(value)
        if text.isdigit() and str(int(text)) == text:
            return gids.gid(target, int(text))
        return text
    return gids.gid(target, value)


def _local_key_part(spec, col, value, gids: _GidLookup):
    target = spec.fk.get(col)
    if target is None:
        return value
    if spec.name == "cell_formatting" and col == "column_key":
        if isinstance(value, str) and _GID_RE.match(value):
            local = gids.local_id(target, value)
            return str(local) if local is not None else None
        return value
    return gids.local_id(target, value)


def _row_key_of(spec, row: dict, gids: _GidLookup) -> Optional[list]:
    parts = [_translate_key_part(spec, c, row.get(c), gids) for c in spec.row_key]
    return None if any(p is None for p in parts) else parts


def _find_rowid(conn, spec, key: list, gids: _GidLookup) -> Optional[int]:
    local = [_local_key_part(spec, c, v, gids) for c, v in zip(spec.row_key, key)]
    if any(v is None for v in local):
        return None
    where = " AND ".join(f"{_q(c)} = ?" for c in spec.row_key)
    row = conn.execute(f"SELECT rowid FROM {_q(spec.name)} WHERE {where}", local).fetchone()
    return row[0] if row else None


@dataclass
class SealResult:
    changes: int = 0
    pending: int = 0
    skipped_admin: int = 0
    tables: frozenset = frozenset()
    last_seq: int = 0        # highest origin_seq issued (0 = none)


# ---------------------------------------------------------------- seq high-water mark

class SeqStateError(Exception):
    """keys/sync_seq.json exists but can't be read. Sealing stops (pending rows are kept)
    rather than risk reusing sequence numbers."""


_seq_lock = threading.Lock()


def seq_state_path(db_path: str) -> str:
    """``<folder of master.db>/keys/sync_seq.json``."""
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "keys", SEQ_STATE_FILE)


def _read_seq_state(path: str) -> dict:
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        return {}
    except OSError as e:
        raise SeqStateError(f"{path} can't be read ({e.__class__.__name__})") from None
    try:
        state = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise SeqStateError(f"{path} is not valid JSON") from None
    if not isinstance(state, dict) or not all(
            isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) and v >= 0
            for k, v in state.items()):
        raise SeqStateError(f"{path} has an unexpected format")
    return state


def read_seq_mark(path: Optional[str], stream: str) -> int:
    """Highest seq recorded outside the DB for ``stream`` (0 if none or no path)."""
    if not path:
        return 0
    with _seq_lock:
        return _read_seq_state(path).get(stream, 0)


def record_seq_mark(path: Optional[str], stream: str, seq: int) -> None:
    """Raises the mark for ``stream`` to ``seq`` (never lowers it). Atomic file replace."""
    if not path or seq <= 0:
        return
    with _seq_lock:
        state = _read_seq_state(path)
        if state.get(stream, 0) >= seq:
            return
        state[stream] = seq
        os.makedirs(os.path.dirname(path), exist_ok=True)
        import sera_keys
        sera_keys.atomic_write(path, json.dumps(state, sort_keys=True, indent=1).encode("utf-8"))


def seal(db_path: str, hex_key: str, db_name: str, *, master_path: Optional[str] = None,
         get_signer: Optional[Callable[[], object]] = None, now_ms: Optional[int] = None,
         timeout: float = 5.0, batch_rows: int = SEAL_BATCH_ROWS,
         seq_state: Optional[str] = None) -> SealResult:
    """Seals ``_sync_pending`` of one DB file into ``_sync_changes`` (one transaction).

    ``master_path`` is needed for rawPayload.db (cross-DB FK translation, read-only).
    ``get_signer()`` returns the office admin private key or None; called only when an
    admin-scoped change is pending. Without it those changes are dropped and logged (the UI
    should have prevented the edit). Does nothing unless ``_sync_meta.mode`` is shadow/live.
    Works through the pending rows in transactions of at most ``batch_rows`` rows, so a big
    batch never holds the write lock for long.
    ``seq_state`` is the ``keys/sync_seq.json`` path (``seq_state_path``); numbering never goes
    below the mark recorded there, and the mark is raised after each committed batch.
    """
    conn = _open(db_path, hex_key, timeout)
    gids = None
    try:
        if read_mode(conn) not in ACTIVE_MODES:
            return SealResult()
        if conn.execute("SELECT 1 FROM _sync_pending LIMIT 1").fetchone() is None:
            return SealResult()
        master_opener = None
        if db_name != MASTER_DB and master_path:
            def master_opener():
                m = _open(master_path, hex_key, timeout)
                m.execute("PRAGMA query_only = 1;")
                return m
        gids = _GidLookup(conn, db_name, master_opener)
        total = SealResult()
        signer_cache: dict = {}
        while True:
            conn.execute("BEGIN IMMEDIATE")
            try:
                stream = _meta(conn, "stream_id")
                floor = read_seq_mark(seq_state, stream) if stream else 0
                r = _seal_in_transaction(conn, db_name, gids, get_signer, now_ms, batch_rows,
                                         signer_cache, floor)
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            # After the commit, never before: a mark written for a batch that then rolled back
            # would leave a gap in the stream, which stalls every other PC's vector for it.
            if r.last_seq:
                record_seq_mark(seq_state, stream, r.last_seq)
            total = SealResult(total.changes + r.changes, total.pending + r.pending,
                               total.skipped_admin + r.skipped_admin, total.tables | r.tables,
                               max(total.last_seq, r.last_seq))
            if r.pending < batch_rows:
                return total
            gids.reset()
    finally:
        if gids is not None:
            gids.close()
        conn.close()


def _seal_in_transaction(conn, db_name, gids: _GidLookup, get_signer, now_ms,
                         batch_rows: int = SEAL_BATCH_ROWS, signer_cache: Optional[dict] = None,
                         seq_floor: int = 0) -> SealResult:
    device_id = _meta(conn, "device_id")
    stream = _meta(conn, "stream_id")
    if not device_id or not stream:
        _log.warning("seal skipped: this database has no device_id/stream_id yet")
        return SealResult()
    pending = conn.execute("SELECT id, tbl, rid, op, key_json FROM _sync_pending ORDER BY id LIMIT ?",
                           (batch_rows,)).fetchall()
    max_pending_id = pending[-1][0] if pending else 0

    # Net effect per row: upserts by rowid (read the row as it is now), deletes by key (only a
    # delete if no row with that key exists any more — update_client() deletes and re-inserts
    # client_values/client_services on every save, which is an update, not a delete).
    upserts: dict = {}   # (tbl, rid) -> first pending id
    deletes: dict = {}   # (tbl, key_text) -> (first pending id, key list)
    for pid, tbl, rid, op, key_json in pending:
        spec = sync_schema.REGISTRY.get(tbl)
        if spec is None or spec.db != db_name or spec.mode == sync_schema.LOCAL:
            continue
        if op == "upsert":
            upserts.setdefault((tbl, rid), pid)
        elif op == "delete":
            try:
                key = json.loads(key_json) if key_json else None
            except ValueError:
                key = None
            if not isinstance(key, list) or any(p is None for p in key):
                # Parent gid NULL (parent deleted in the same cascade): its tombstone implies it.
                continue
            deletes.setdefault((tbl, key_text(key)), (pid, key))

    col_cache: dict = {}
    items = []  # (order, kind, spec, payload)
    live_keys = set()
    for (tbl, rid), pid in upserts.items():
        spec = sync_schema.REGISTRY[tbl]
        cols = _columns(conn, tbl, col_cache)
        names = [c for c, _ in cols]
        row = conn.execute(
            f"SELECT {', '.join(_q(c) for c in names)} FROM {_q(tbl)} WHERE rowid = ?", (rid,)).fetchone()
        if row is None:
            continue
        rowd = dict(zip(names, row))
        key = _row_key_of(spec, rowd, gids)
        if key is None:
            _log.warning("seal: %s row without a resolvable key skipped", tbl)
            continue
        kt = key_text(key)
        if (tbl, kt) in live_keys:
            continue
        live_keys.add((tbl, kt))
        items.append((pid, "upsert", spec, (kt, rowd, cols)))

    for (tbl, kt), (pid, key) in deletes.items():
        if (tbl, kt) in live_keys:
            continue
        spec = sync_schema.REGISTRY[tbl]
        rid = _find_rowid(conn, spec, key, gids)
        if rid is not None:
            # The key exists again: seal the current row instead of deleting it.
            cols = _columns(conn, tbl, col_cache)
            names = [c for c, _ in cols]
            row = conn.execute(
                f"SELECT {', '.join(_q(c) for c in names)} FROM {_q(tbl)} WHERE rowid = ?", (rid,)).fetchone()
            live_keys.add((tbl, kt))
            items.append((pid, "upsert", spec, (kt, dict(zip(names, row)), cols)))
        else:
            items.append((pid, "delete", spec, (kt,)))

    items.sort(key=lambda it: it[0])

    next_seq = max(_next_seq(conn, stream), seq_floor + 1)
    last_hlc = _meta(conn, "last_hlc") or ""
    signer_cache = {} if signer_cache is None else signer_cache
    written = 0
    skipped_admin = 0
    tables = set()

    for _order, kind, spec, payload in items:
        kt = payload[0]
        if kind == "upsert":
            _kt, rowd, cols = payload
            data, hashes = _changed_columns(conn, spec, kt, rowd, cols, gids)
            if data is None:
                continue
        else:
            if conn.execute("SELECT 1 FROM _sync_clock WHERE tbl = ? AND row_key = ? LIMIT 1",
                            (spec.name, kt)).fetchone() is None and \
               conn.execute("SELECT 1 FROM _sync_tombstones WHERE tbl = ? AND row_key = ?",
                            (spec.name, kt)).fetchone() is not None:
                continue  # already tombstoned, nothing new to say
            data, hashes = None, None

        sig = None
        if spec.mode == ADMIN_LWW:
            if "key" not in signer_cache:
                try:
                    signer_cache["key"] = get_signer() if get_signer else None
                except Exception as e:
                    _log.warning("seal: admin key unavailable (%s)", e.__class__.__name__)
                    signer_cache["key"] = None
            signer = signer_cache["key"]
            if signer is None:
                skipped_admin += 1
                _log.warning("seal: %s change not sealed: this PC does not hold the office admin key",
                             spec.name)
                continue

        hlc = _tick_shared(last_hlc, device_id, now_ms)
        last_hlc = hlc
        change = {
            "origin": stream, "origin_seq": next_seq, "hlc": hlc, "tbl": spec.name,
            "row_key": kt, "op": kind, "data": data,
        }
        if spec.mode == ADMIN_LWW:
            sig = sign_change(change, signer)
        conn.execute(
            "INSERT INTO _sync_changes(origin, origin_seq, hlc, tbl, row_key, op, data, sig) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (stream, next_seq, hlc, spec.name, kt, kind,
             None if data is None else json.dumps(data, sort_keys=True, ensure_ascii=False), sig))
        next_seq += 1
        written += 1
        tables.add(spec.name)

        if kind == "upsert":
            conn.execute("DELETE FROM _sync_tombstones WHERE tbl = ? AND row_key = ?", (spec.name, kt))
            for col, h in hashes.items():
                conn.execute(
                    "INSERT INTO _sync_clock(tbl, row_key, col, hlc, origin, vhash) VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(tbl, row_key, col) DO UPDATE SET hlc = excluded.hlc, "
                    "origin = excluded.origin, vhash = excluded.vhash",
                    (spec.name, kt, col, hlc, stream, h))
        else:
            conn.execute("DELETE FROM _sync_clock WHERE tbl = ? AND row_key = ?", (spec.name, kt))
            conn.execute(
                "INSERT INTO _sync_tombstones(tbl, row_key, hlc, origin) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(tbl, row_key) DO UPDATE SET hlc = excluded.hlc, origin = excluded.origin",
                (spec.name, kt, hlc, stream))

    conn.execute("DELETE FROM _sync_pending WHERE id <= ?", (max_pending_id,))
    _set_meta(conn, "next_seq", str(next_seq))
    _set_meta(conn, "next_seq_stream", stream)
    if written:
        _set_meta(conn, "last_hlc", last_hlc)
        conn.execute(
            "INSERT INTO _sync_vector(origin, max_seq) VALUES (?, ?) "
            "ON CONFLICT(origin) DO UPDATE SET max_seq = excluded.max_seq",
            (stream, next_seq - 1))
    return SealResult(changes=written, pending=len(pending), skipped_admin=skipped_admin,
                      tables=frozenset(tables), last_seq=next_seq - 1 if written else 0)


def _next_seq(conn, stream: str) -> int:
    """``next_seq`` belongs to the stream recorded in ``next_seq_stream``. A new stream (the PC's
    device_id changed, e.g. after a rejoin) starts after its highest existing seq, normally 1."""
    if _meta(conn, "next_seq_stream") == stream:
        return max(1, int(_meta(conn, "next_seq") or "1"))
    row = conn.execute("SELECT MAX(origin_seq) FROM _sync_changes WHERE origin = ?", (stream,)).fetchone()
    return (row[0] or 0) + 1


def _changed_columns(conn, spec, kt, rowd, cols, gids):
    """(data, hashes) for the columns whose value differs from ``_sync_clock``; (None, None) if
    nothing changed. All columns on the first seal of a row."""
    clock = dict(conn.execute("SELECT col, vhash FROM _sync_clock WHERE tbl = ? AND row_key = ?",
                              (spec.name, kt)).fetchall())
    first = PRESENCE_COL not in clock
    data = {}
    hashes = {}
    for col in _data_columns(spec, cols):
        value = rowd.get(col)
        if col in spec.fk:
            value = gids.gid(spec.fk[col], value)
        h = vhash(value)
        if first or clock.get(col) != h:
            data[col] = _json_value(value)
            hashes[col] = h
    if first:
        hashes[PRESENCE_COL] = vhash(None)
    elif not data:
        return None, None
    return data, hashes


# ---------------------------------------------------------------- timer

class SealTimer:
    """Calls ``fn()`` every ``interval`` seconds on a daemon thread (picks up writes made by other
    processes, e.g. DOM_Parser/SDC_Parser). Exceptions are logged, never raised."""

    def __init__(self, fn: Callable[[], object], interval: float = SEAL_TIMER_SECONDS):
        self._fn = fn
        self._interval = interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="sera-sync-sealer", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self._fn()
            except Exception as e:
                _log.warning("timer seal failed: %s", e)

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout)
