"""Sera Sync v3 — apply engine (blueprint §5, WP P3-4).

``apply_batch(db_path, hex_key, db_name, changes, ...)`` applies remote changes (the format
``sync_capture.seal`` writes, see its module docstring) to one DB file in ONE transaction:

  1. ``_sync_flags.applying = 1`` (the capture triggers see it, so nothing applied here is
     captured again). Reset to 0 before COMMIT; on any error the transaction rolls back, which
     restores it too, and the flag is checked again afterwards.
  2. Local writes still waiting in ``_sync_pending`` are sealed first, inside the same
     transaction, so an incoming change is always compared with this PC's latest edits.
  3. Changes are sorted by ``hlc`` and applied one by one (each in a SAVEPOINT, so a change that
     has to be parked leaves nothing behind):
       - already in ``_sync_changes`` -> skipped (idempotent);
       - ``admin_lww`` without a valid admin signature -> dropped and logged (not recorded);
       - HLC more than an hour ahead -> parked (``clock_ahead``), local clock not moved;
       - rows are found by gid (following ``_sync_alias``) or by natural/composite key with the
         parent gids translated to local ids; a missing parent -> parked (``missing_parent``),
         and later changes of the same row are parked behind it;
       - per-column LWW on ``(hlc, origin)`` against ``_sync_clock``; the clock rows store the
         same ``vhash`` the sealer computes (value in its gid form), so applied values are never
         sent back out as local edits;
       - tombstones: see "Tombstones" below; deletes remove the row (FK cascades are not
         captured) and write a tombstone;
       - natural-key merge (``services.name``, ``staff_users.name``): the lexicographically
         smaller gid wins on every PC, ``_sync_alias(loser -> winner)``, columns merged by LWW;
       - clients merge on the internal PK column (owner decision 2026-09-24, see below);
       - every change is appended unchanged to ``_sync_changes`` for forwarding, and
         ``_sync_vector[origin]`` advances only while contiguous;
       - ``observe(hlc)`` on the local clock.
  4. Parked changes are retried after each batch. After 7 days they are listed by
     ``parked_changes(..., older_than_seconds=PARK_SURFACE_SECONDS)`` for the panel (P3-8).
  5. COMMIT. ``ApplyResult.tables`` is the set of touched tables for the UI refresh (P3-8).

Tombstones (owner decision 2026-09-25):
  - Rows keyed by gid: delete always wins. An upsert for a tombstoned gid is dropped; if its HLC
    is newer than the tombstone, the discarded values are written to ``_sync_conflicts``
    ("edit after delete discarded"). A delete that discards newer local edits records them too.
  - Natural / composite keys (client_values, client_services, cell_formatting, app_settings,
    sdc_session_timelines): the later of delete and edit wins, compared on ``(hlc, origin)``.
    An upsert newer than the tombstone brings the row back (the columns it doesn't carry get
    their "absent" value, stamped with the tombstone's clock). A delete older than the row's
    newest clock only resets the columns older than the delete to their absent value. Both
    orders give the same result on every PC.

Merges (one row for what two PCs created separately):
  - Natural key (``services.name``, ``staff_users.name``): an incoming row whose name another row
    already has, or a rename onto such a name.
  - Clients on the internal PK (owner decision 2026-09-24, built in P3-4 at the owner's request
    2026-09-25): two ACTIVE clients with the same internal-PK value (``mcl_columns.is_internal_pk``,
    compared ``UPPER(TRIM())``). Checked once at the end of each batch, never halfway through
    one; archived clients never take part.
  The smaller gid wins. The loser's children (client_values, client_services, cell_formatting,
  audit_log; tracker_dump and sdc_session_timelines in rawPayload.db through
  ``run_raw_repoints``) move to the winner and values merge by LWW (the name too); the loser row
  goes and ``_sync_alias(loser -> winner)`` redirects its later changes (no conflict rows). The
  winning client keeps its ``client_id_token``; an audit_log row (``CLIENT_MERGED``, gid from the
  loser) records the loser's token.
  Whether two rows are "the same" depends on what a PC holds at that moment, which differs
  between PCs. So the PC that decides also adds the decision to its own stream as a change
  ``{tbl, row_key: [loser], op: "merge", data: {"into": winner}}``, and every PC that receives
  it makes the same merge, whatever it holds: a copy not here yet is covered by the alias; a
  deleted copy deletes the merged row, as a delete arriving after the merge would. Admin-scoped
  merges (staff) are only sent when this PC holds the admin key.

No PySide6 import (blueprint §0 rule 7).
"""

from __future__ import annotations

import base64
import binascii
import datetime
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

import sync_capture
import sync_schema
from sync_capture import ACTIVE_MODES, PRESENCE_COL, ClockAhead, HLC, _q, key_text, vhash
from sync_schema import ADMIN_LWW, APPEND, LOCAL, MASTER_DB, RAW_DB

_log = logging.getLogger("sera.sync.apply")

PARK_SURFACE_SECONDS = 7 * 24 * 3600
REPOINT_META_KEY = "raw_repoints"
MERGE_ACTOR = "Sera Sync"
MERGE_ACTION = "CLIENT_MERGED"
# Columns a merged row keeps from the winner, whatever the clocks say (D8: the winning client
# keeps its token). Changes that reach the winner through an alias don't carry them either.
KEEP_ON_MERGE = {"clients": ("client_id_token",)}
_MAX_ALIAS_DEPTH = 64
# Tables whose rows can be merged: natural-key merge (services, staff_users) and the client
# merge on the internal PK. A remote "merge" for any other table is ignored.
MERGEABLE_TABLES = frozenset({"clients"} | {
    s.name for s in sync_schema.REGISTRY.values() if s.natural_merge_on})
_NO_STAMP = ("", "")

APPLIED, SKIPPED, DROPPED, REJECTED, IGNORED, PARKED = (
    "applied", "skipped", "dropped", "rejected", "ignored", "parked")


class ApplyError(Exception):
    """A change in the batch is malformed. Nothing was applied."""


@dataclass
class ApplyResult:
    applied: int = 0          # changes applied (including parked ones that now went through)
    skipped: int = 0          # already had them
    dropped: int = 0          # lost to a tombstone or an older delete, or parent deleted
    rejected: int = 0         # admin scope without a valid signature
    ignored: int = 0          # table unknown to this version / not replicated here
    parked: int = 0           # newly parked in this batch
    unparked: int = 0         # parked earlier, applied now
    conflicts: int = 0        # _sync_conflicts rows written
    sealed: int = 0           # local pending changes sealed before applying
    raw_repoints: int = 0     # rawPayload.db re-pointing jobs queued (master.db only)
    emitted: int = 0          # changes this PC added to its own stream (merge decisions)
    merges: list = field(default_factory=list)       # (tbl, loser_gid, winner_gid)
    clock_ahead: list = field(default_factory=list)  # (origin, ahead_ms)
    tables: set = field(default_factory=set)         # touched tables (UI refresh, P3-8)


# ---------------------------------------------------------------- change format

def _normalise_change(ch) -> dict:
    if not isinstance(ch, dict):
        raise ApplyError("change is not an object")
    try:
        origin, seq, hlc, tbl = ch["origin"], ch["origin_seq"], ch["hlc"], ch["tbl"]
        row_key, op = ch["row_key"], ch["op"]
    except KeyError as e:
        raise ApplyError(f"change without {e.args[0]!r}") from None
    if not isinstance(origin, str) or not origin:
        raise ApplyError("bad origin")
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
        raise ApplyError("bad origin_seq")
    try:
        HLC.parse(hlc)
    except ValueError:
        raise ApplyError("bad hlc") from None
    if not isinstance(tbl, str) or not tbl:
        raise ApplyError("bad tbl")
    if not isinstance(row_key, str):
        raise ApplyError("bad row_key")
    try:
        key = json.loads(row_key)
    except ValueError:
        raise ApplyError("row_key is not JSON") from None
    if not isinstance(key, list) or not key or any(p is None for p in key):
        raise ApplyError("row_key is not a JSON array")
    if op not in ("upsert", "delete", "merge"):
        raise ApplyError("bad op")
    data = ch.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            raise ApplyError("data is not JSON") from None
    if op == "upsert" and not isinstance(data, dict):
        raise ApplyError("upsert without a data object")
    if op == "merge" and not (isinstance(data, dict) and isinstance(data.get("into"), str)
                              and sync_capture._GID_RE.match(data["into"]) and len(key) == 1):
        raise ApplyError("merge without a valid 'into' gid")
    if op == "delete":
        data = None
    sig = ch.get("sig")
    if sig is not None and not isinstance(sig, str):
        raise ApplyError("bad sig")
    return {"origin": origin, "origin_seq": seq, "hlc": hlc, "tbl": tbl, "row_key": row_key,
            "op": op, "data": data, "sig": sig, "key": key}


def _change_json(ch: dict) -> str:
    return json.dumps({k: ch[k] for k in ("origin", "origin_seq", "hlc", "tbl", "row_key", "op",
                                          "data", "sig")}, sort_keys=True, ensure_ascii=False)


def _decode(value):
    if isinstance(value, dict) and set(value) == {"$b64"}:
        try:
            return base64.b64decode(value["$b64"], validate=True)
        except (binascii.Error, TypeError):
            raise ApplyError("bad $b64 value") from None
    return value


def _json_safe(value):
    if value is None:
        return None
    return json.dumps(sync_capture._json_value(value), ensure_ascii=False)


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def _stamp(hlc, origin) -> tuple:
    return (hlc or "", origin or "")


# ---------------------------------------------------------------- the applier

class _Applier:
    def __init__(self, conn, db_name: str, master, admin_pubkey: Optional[str],
                 now_ms: Optional[int], result: ApplyResult, get_signer=None, seq_state=None,
                 emit: bool = True):
        self.conn = conn
        self.db_name = db_name
        self.master = master            # read-only master.db connection (rawPayload.db only)
        self.admin_pubkey = admin_pubkey
        self.now_ms = now_ms
        self.r = result
        self.device_id = sync_capture._meta(conn, "device_id") or ""
        self.last_hlc = sync_capture._meta(conn, "last_hlc") or ""
        self._info: dict = {}
        self._live = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self._parked_keys: dict = {}     # (tbl, row_key) -> oldest parked hlc of that row
        self._queued: set = set()
        self._candidates: set = set()
        self.get_signer = get_signer
        self.seq_state = seq_state
        self.emitted_seq = 0
        self.emitted_stream = None
        self.emit = emit

    # ------------------------------------------------ schema helpers

    def _conn_for(self, table: str):
        spec = sync_schema.REGISTRY.get(table)
        if spec is None or spec.db == self.db_name:
            return self.conn
        if self.master is None:
            raise RuntimeError(f"no {spec.db} connection to look up {table}")
        return self.master

    def _table_info(self, table: str):
        if table not in self._info:
            self._info[table] = self.conn.execute(f"PRAGMA table_info({_q(table)})").fetchall()
        return self._info[table]

    def _column_names(self, table: str) -> list:
        return [r[1] for r in self._table_info(table)]

    def _rowid_alias(self, table: str) -> Optional[str]:
        info = self._table_info(table)
        pk = [r for r in info if r[5]]
        if len(pk) == 1 and (pk[0][2] or "").upper() == "INTEGER":
            return pk[0][1]
        return None

    def _data_columns(self, spec) -> list:
        skip = set(spec.row_key) | {"gid"}
        alias = self._rowid_alias(spec.name)
        return [c for c in self._column_names(spec.name) if c != alias and c not in skip]

    def _required(self, table: str) -> list:
        alias = self._rowid_alias(table)
        return [r[1] for r in self._table_info(table) if r[3] and r[4] is None and r[1] != alias]

    def _absent(self, table: str, col: str):
        """The value a column has when nobody set it: its DEFAULT, else NULL, else '' / 0."""
        for r in self._table_info(table):
            if r[1] != col:
                continue
            dflt = r[4]
            if dflt is not None:
                text = str(dflt).strip()
                if len(text) >= 2 and text[0] == text[-1] == "'":
                    return text[1:-1].replace("''", "'")
                if text.upper() == "NULL":
                    return None
                try:
                    return int(text)
                except ValueError:
                    try:
                        return float(text)
                    except ValueError:
                        pass
            if not r[3]:
                return None
            typ = (r[2] or "").upper()
            return 0 if ("INT" in typ or "REAL" in typ or "NUM" in typ) else ""
        return None

    # ------------------------------------------------ lookups

    @staticmethod
    def _follow_alias(conn, gid):
        for _ in range(_MAX_ALIAS_DEPTH):
            row = conn.execute("SELECT gid FROM _sync_alias WHERE alias_gid = ?", (gid,)).fetchone()
            if not row or not row[0] or row[0] == gid:
                return gid
            gid = row[0]
        return gid

    def _resolve_gid(self, table: str, gid):
        """('ok', local id, gid) | ('gone', None, gid) | ('missing', None, gid). Follows aliases."""
        conn = self._conn_for(table)
        g = self._follow_alias(conn, gid)
        row = conn.execute(f"SELECT id FROM {_q(table)} WHERE gid = ?", (g,)).fetchone()
        if row:
            return "ok", row[0], g
        if conn.execute("SELECT 1 FROM _sync_tombstones WHERE tbl = ? AND row_key = ?",
                        (table, key_text([g]))).fetchone():
            return "gone", None, g
        return "missing", None, g

    def _gid_of(self, table: str, local_id):
        if local_id is None:
            return None
        row = self._conn_for(table).execute(
            f"SELECT gid FROM {_q(table)} WHERE id = ?", (local_id,)).fetchone()
        return row[0] if row else None

    def _fk_gidform(self, spec, col, local):
        """A column value in the form the sealer hashes it (FK ids as gids)."""
        target = spec.fk.get(col)
        if target is None or local is None:
            return local
        if spec.name == "cell_formatting" and col == "column_key":
            text = str(local)
            return self._gid_of(target, int(text)) if text.isdigit() else text
        return self._gid_of(target, local)

    def _tomb(self, table: str, kt: str):
        row = self.conn.execute("SELECT hlc, origin FROM _sync_tombstones WHERE tbl = ? AND row_key = ?",
                                (table, kt)).fetchone()
        return _stamp(*row) if row else None

    def _set_tomb(self, table: str, kt: str, stamp) -> None:
        cur = self._tomb(table, kt)
        if cur is None or stamp > cur:
            self.conn.execute(
                "INSERT INTO _sync_tombstones(tbl, row_key, hlc, origin) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(tbl, row_key) DO UPDATE SET hlc = excluded.hlc, origin = excluded.origin",
                (table, kt, stamp[0], stamp[1]))

    def _clocks(self, table: str, kt: str) -> dict:
        return {c: _stamp(h, o) for c, h, o in self.conn.execute(
            "SELECT col, hlc, origin FROM _sync_clock WHERE tbl = ? AND row_key = ?", (table, kt))}

    def _set_clock(self, table: str, kt: str, col: str, stamp, vh: str) -> None:
        self.conn.execute(
            "INSERT INTO _sync_clock(tbl, row_key, col, hlc, origin, vhash) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(tbl, row_key, col) DO UPDATE SET hlc = excluded.hlc, "
            "origin = excluded.origin, vhash = excluded.vhash",
            (table, kt, col, stamp[0], stamp[1], vh))

    def _find_rowid(self, spec, key_local: Optional[dict]):
        if key_local is None:
            return None
        where = " AND ".join(f"{_q(c)} = ?" for c in spec.row_key)
        row = self.conn.execute(f"SELECT rowid FROM {_q(spec.name)} WHERE {where}",
                                [key_local[c] for c in spec.row_key]).fetchone()
        return row[0] if row else None

    def _row(self, table: str, rowid) -> dict:
        names = self._column_names(table)
        row = self.conn.execute(f"SELECT {', '.join(_q(c) for c in names)} FROM {_q(table)} "
                                f"WHERE rowid = ?", (rowid,)).fetchone()
        return dict(zip(names, row)) if row else {}

    def _conflict(self, table, kt, col, kept, discarded, reason) -> None:
        self.conn.execute(
            "INSERT INTO _sync_conflicts(at, tbl, row_key, col, kept, discarded, reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_now_iso(), table, kt, col, _json_safe(kept), _json_safe(discarded), reason))
        self.r.conflicts += 1

    # ------------------------------------------------ key and data translation

    def _resolve_key(self, spec, key: list):
        """(status, local key values, canonical gid-form key, redirected, gone_stamp)."""
        status, local, canon, redirected, gone = "ok", {}, [], False, None
        for col, part in zip(spec.row_key, key):
            if col == "gid":
                g = self._follow_alias(self.conn, part)
                redirected |= g != part
                local[col] = g
                canon.append(g)
                continue
            target = spec.fk.get(col)
            if target is None:
                local[col] = part
                canon.append(part)
                continue
            if spec.name == "cell_formatting" and col == "column_key" and not (
                    isinstance(part, str) and sync_capture._GID_RE.match(part)):
                local[col] = part          # the literal "services"
                canon.append(part)
                continue
            st, lid, g = self._resolve_gid(target, part)
            redirected |= g != part
            canon.append(g)
            if st == "ok":
                local[col] = str(lid) if spec.name == "cell_formatting" and col == "column_key" else lid
            elif st == "gone":
                status = "gone"
                gone = self._conn_for_tomb(target, g)
            elif status == "ok":
                status = "missing"
        return status, local, canon, redirected, gone

    def _conn_for_tomb(self, table, gid):
        row = self._conn_for(table).execute(
            "SELECT hlc, origin FROM _sync_tombstones WHERE tbl = ? AND row_key = ?",
            (table, key_text([gid]))).fetchone()
        return _stamp(*row) if row else None

    def _translate_data(self, spec, data: dict, redirected: bool):
        """{col: (local value, gid-form value)}, or None when an FK referent is still missing."""
        live = set(self._column_names(spec.name))
        skip = set(spec.row_key) | {"gid", self._rowid_alias(spec.name)}
        if redirected:
            skip |= set(KEEP_ON_MERGE.get(spec.name, ()))
        cols = {}
        for col, value in data.items():
            if col in skip:
                continue
            if col not in live:
                _log.warning("apply: %s has no column %s here; value ignored", spec.name, col)
                continue
            value = _decode(value)
            target = spec.fk.get(col)
            if target is None or value is None:
                cols[col] = (value, value)
                continue
            st, lid, g = self._resolve_gid(target, value)
            if st == "ok":
                cols[col] = (lid, g)
            elif st == "gone":
                cols[col] = (None, None)       # referent deleted: store NULL (P3-1 note)
            else:
                return None
        return cols

    # ------------------------------------------------ writing rows

    def _upsert_row(self, spec, kt, key_local: dict, cols: dict, stamp, col_stamps=None,
                    missing_clock_loses=False) -> str:
        """Per-column LWW of ``cols`` ({col: (local, gid form)}) onto the row. INSERTs it when
        absent. Returns 'applied', 'noop' or 'incomplete' (a NOT NULL column has no value)."""
        t = spec.name
        clocks = self._clocks(t, kt)
        col_stamps = col_stamps or {}

        def newer(c):
            s = col_stamps.get(c, stamp)
            cur = clocks.get(c)
            if cur is None:
                return not missing_clock_loses or s > _NO_STAMP
            return s > cur

        winning = {c: v for c, v in cols.items() if newer(c)}
        rowid = self._find_rowid(spec, key_local)
        inserted = False
        if rowid is None:
            values = dict(key_local)
            for c, v in winning.items():
                values[c] = v[0]
            if any(values.get(c) is None for c in self._required(t)):
                return "incomplete"
            names = list(values)
            self.conn.execute(f"INSERT INTO {_q(t)} ({', '.join(_q(c) for c in names)}) "
                              f"VALUES ({', '.join('?' for _ in names)})", [values[c] for c in names])
            inserted = True
        else:
            nm = spec.natural_merge_on
            if nm and nm in winning and winning[nm][0] is not None:
                other = self.conn.execute(f"SELECT gid FROM {_q(t)} WHERE {_q(nm)} = ? AND rowid != ?",
                                          (winning[nm][0], rowid)).fetchone()
                if other:
                    # Renamed onto a name another row has: same row (natural-key merge).
                    mine = self.conn.execute(f"SELECT gid FROM {_q(t)} WHERE rowid = ?", (rowid,)).fetchone()[0]
                    self._decide_merge(spec, mine, other[0])
                    winner = self._follow_alias(self.conn, mine)
                    return self._upsert_row(spec, key_text([winner]), {"gid": winner}, cols, stamp,
                                            col_stamps, missing_clock_loses)
            if winning:
                sets = ", ".join(f"{_q(c)} = ?" for c in winning)
                self.conn.execute(f"UPDATE {_q(t)} SET {sets} WHERE rowid = ?",
                                  [v[0] for v in winning.values()] + [rowid])
        for c, v in winning.items():
            self._set_clock(t, kt, c, col_stamps.get(c, stamp), vhash(v[1]))
        presence = clocks.get(PRESENCE_COL)
        if presence is None or stamp > presence:
            self._set_clock(t, kt, PRESENCE_COL, stamp, vhash(None))
        return "applied" if (winning or inserted) else "noop"

    def _absent_fill(self, spec, cols: dict, col_stamps: dict, stamp) -> None:
        for c in self._data_columns(spec):
            if c not in cols:
                v = self._absent(spec.name, c)
                cols[c] = (v, None if c in spec.fk else v)
                col_stamps[c] = stamp

    def _delete(self, spec, kt: str, key_local: Optional[dict], stamp) -> str:
        t = spec.name
        rowid = self._find_rowid(spec, key_local)
        gid_keyed = spec.row_key == ("gid",)
        if rowid is not None:
            clocks = self._clocks(t, kt)
            newest = max(clocks.values(), default=None)
            if not gid_keyed and newest is not None and newest > stamp:
                # The row was written after this delete: only what's older than the delete goes.
                row = self._row(t, rowid)
                sets = {}
                for c in self._data_columns(spec):
                    if c not in clocks or clocks[c] < stamp:
                        v = self._absent(t, c)
                        sets[c] = v
                        self._set_clock(t, kt, c, stamp, vhash(None if c in spec.fk else v))
                sets = {c: v for c, v in sets.items() if row.get(c) != v}
                if sets:
                    self.conn.execute(f"UPDATE {_q(t)} SET {', '.join(f'{_q(c)} = ?' for c in sets)} "
                                      f"WHERE rowid = ?", list(sets.values()) + [rowid])
                return APPLIED
            if gid_keyed and newest is not None and newest > stamp:
                row = self._row(t, rowid)
                for c, s in clocks.items():
                    if c != PRESENCE_COL and s > stamp:
                        self._conflict(t, kt, c, None, row.get(c), "edit discarded by delete")
            local_id = None
            if gid_keyed:
                local_id = self.conn.execute(f"SELECT id FROM {_q(t)} WHERE rowid = ?", (rowid,)).fetchone()[0]
            self.conn.execute(f"DELETE FROM {_q(t)} WHERE rowid = ?", (rowid,))
            if gid_keyed:
                self._forget_children(spec, key_local["gid"])
                # Other rows that pointed at it (audit_log, tracker_dump, ...) now point at nothing,
                # the same NULL a PC that never had the row stores for them.
                self._rekey_children(t, key_local["gid"], None, local_id, None)
        self.conn.execute("DELETE FROM _sync_clock WHERE tbl = ? AND row_key = ?", (t, kt))
        self._set_tomb(t, kt, stamp)
        return APPLIED

    def _forget_children(self, spec, gid: str) -> None:
        """Clock rows of composite-key children removed by ON DELETE CASCADE."""
        for child in sync_schema.replicated_tables_for(self.db_name):
            if any(target == spec.name and col in child.row_key for col, target in child.fk.items()):
                self.conn.execute("DELETE FROM _sync_clock WHERE tbl = ? AND row_key LIKE ?",
                                  (child.name, f'%"{gid}"%'))

    # ------------------------------------------------ merges

    def _set_alias(self, table: str, alias: str, gid: str) -> None:
        self.conn.execute("INSERT INTO _sync_alias(tbl, alias_gid, gid) VALUES (?, ?, ?) "
                          "ON CONFLICT(alias_gid) DO UPDATE SET tbl = excluded.tbl, gid = excluded.gid",
                          (table, alias, gid))
        self.conn.execute("UPDATE _sync_alias SET gid = ? WHERE gid = ?", (gid, alias))

    def _queue_repoint(self, table: str, old_id, new_id, new_gid) -> None:
        if self.db_name != MASTER_DB:
            return
        job = (table, old_id, new_id, new_gid)
        if job in self._queued:
            return
        self._queued.add(job)
        jobs = json.loads(sync_capture._meta(self.conn, REPOINT_META_KEY) or "[]")
        jobs.append([table, old_id, new_id, new_gid])
        sync_capture._set_meta(self.conn, REPOINT_META_KEY, json.dumps(jobs))
        self.r.raw_repoints += 1

    def _children(self, table: str):
        """(child spec, fk column) for every registered table whose fk points at ``table``."""
        for child in sync_schema.REGISTRY.values():
            for col, target in child.fk.items():
                if target == table:
                    yield child, col

    def _key_gidform(self, spec, row: dict) -> list:
        return [self._fk_gidform(spec, c, row.get(c)) if c in spec.fk else row.get(c)
                for c in spec.row_key]

    def _rekey_children(self, table: str, old_gid: str, new_gid: str, old_id, new_id) -> None:
        """Children of ``table`` follow its row from ``old_gid``/``old_id`` to ``new_gid``/``new_id``."""
        for child, col in self._children(table):
            if child.db != self.db_name:
                self._queue_repoint(table, old_id, new_id, new_gid)
                continue
            if child.name not in self._live:
                continue
            if child.mode == LOCAL:
                if old_id != new_id and new_id is not None:
                    # Per-PC tables (activity counters): keep the winner's row if it has one;
                    # whatever is left goes with the loser row (ON DELETE CASCADE).
                    self.conn.execute(f"UPDATE OR IGNORE {_q(child.name)} SET {_q(col)} = ? "
                                      f"WHERE {_q(col)} = ?", (new_id, old_id))
                continue
            if col in child.row_key:
                if new_gid is not None:      # (deleted parent: ON DELETE CASCADE removed them)
                    self._rekey_composite(child, col, old_gid, new_gid, old_id, new_id)
            else:
                rows = self.conn.execute(f"SELECT rowid FROM {_q(child.name)} WHERE {_q(col)} = ?",
                                         (old_id,)).fetchall()
                if old_id != new_id:
                    self.conn.execute(f"UPDATE {_q(child.name)} SET {_q(col)} = ? WHERE {_q(col)} = ?",
                                      (new_id, old_id))
                for (rid,) in rows:
                    kt = key_text(self._key_gidform(child, self._row(child.name, rid)))
                    self.conn.execute("UPDATE _sync_clock SET vhash = ? WHERE tbl = ? AND row_key = ? AND col = ?",
                                      (vhash(new_gid), child.name, kt, col))

    def _rekey_composite(self, child, col, old_gid, new_gid, old_id, new_id) -> None:
        pos = child.row_key.index(col)
        t = child.name
        local_value = (lambda i: str(i)) if (t == "cell_formatting" and col == "column_key") else (lambda i: i)
        for (rid,) in self.conn.execute(f"SELECT rowid FROM {_q(t)} WHERE {_q(col)} = ?",
                                        (local_value(old_id),)).fetchall():
            row = self._row(t, rid)
            old_key = self._key_gidform(child, row)
            old_key[pos] = old_gid
            new_key = list(old_key)
            new_key[pos] = new_gid
            old_kt, new_kt = key_text(old_key), key_text(new_key)
            clocks = {c: (_stamp(h, o), vh) for c, h, o, vh in self.conn.execute(
                "SELECT col, hlc, origin, vhash FROM _sync_clock WHERE tbl = ? AND row_key = ?", (t, old_kt))}
            self.conn.execute("DELETE FROM _sync_clock WHERE tbl = ? AND row_key = ?", (t, old_kt))
            new_local = {c: row[c] for c in child.row_key}
            new_local[col] = local_value(new_id)
            if old_id == new_id:
                # Same local row, new gid: only the clock key changes (merge with any clock
                # already kept under the new key).
                existing = {c: _stamp(h, o) for c, h, o in self.conn.execute(
                    "SELECT col, hlc, origin FROM _sync_clock WHERE tbl = ? AND row_key = ?", (t, new_kt))}
                for c, (s, vh) in clocks.items():
                    if c not in existing or s > existing[c]:
                        self._set_clock(t, new_kt, c, s, vh)
                tomb = self._tomb(t, new_kt)
                if tomb is not None:
                    self.conn.execute("DELETE FROM _sync_tombstones WHERE tbl = ? AND row_key = ?", (t, new_kt))
                    self._delete(child, new_kt, new_local, tomb)
                continue
            # Different row: the loser's child row is replayed onto the winner's key with its own
            # clocks, by the same rules a remote change would follow.
            cols, stamps = {}, {}
            for c in self._data_columns(child):
                cols[c] = (row[c], self._fk_gidform(child, c, row[c]))
                stamps[c] = clocks[c][0] if c in clocks else _NO_STAMP
            row_stamp = max((s for s, _ in clocks.values()), default=_NO_STAMP)
            self.conn.execute(f"DELETE FROM {_q(t)} WHERE rowid = ?", (rid,))
            self._replay_upsert(child, new_kt, new_local, cols, row_stamp, stamps)
        # Tombstones kept under the old key.
        for kt, h, o in self.conn.execute(
                "SELECT row_key, hlc, origin FROM _sync_tombstones WHERE tbl = ? AND row_key LIKE ?",
                (t, f'%"{old_gid}"%')).fetchall():
            key = json.loads(kt)
            if len(key) <= pos or key[pos] != old_gid:
                continue
            key[pos] = new_gid
            self.conn.execute("DELETE FROM _sync_tombstones WHERE tbl = ? AND row_key = ?", (t, kt))
            st, local, canon, _r, _g = self._resolve_key(child, key)
            self._delete(child, key_text(canon), local if st == "ok" else None, _stamp(h, o))

    def _replay_upsert(self, spec, kt, key_local, cols, row_stamp, stamps) -> None:
        tomb = self._tomb(spec.name, kt)
        if tomb is not None:
            if spec.row_key == ("gid",) or row_stamp <= tomb:
                return
            self.conn.execute("DELETE FROM _sync_tombstones WHERE tbl = ? AND row_key = ?", (spec.name, kt))
            self._absent_fill(spec, cols, stamps, tomb)
        if self._upsert_row(spec, kt, key_local, cols, row_stamp, stamps, missing_clock_loses=True) == "incomplete":
            _log.warning("apply: merged %s row could not be written (incomplete)", spec.name)
        self.r.tables.add(spec.name)

    def _rewrite_gid(self, spec, old: str, new: str) -> None:
        """This PC's row ``old`` is the same row as ``new`` (smaller), which isn't here yet: the
        row takes the gid ``new``."""
        t = spec.name
        rid = self.conn.execute(f"SELECT id FROM {_q(t)} WHERE gid = ?", (old,)).fetchone()[0]
        self.conn.execute(f"UPDATE {_q(t)} SET gid = ? WHERE gid = ?", (new, old))
        self.conn.execute("UPDATE _sync_clock SET row_key = ? WHERE tbl = ? AND row_key = ?",
                          (key_text([new]), t, key_text([old])))
        # Columns the winner keeps (its token) take the winner's value once it arrives.
        for c in KEEP_ON_MERGE.get(t, ()):
            self.conn.execute("DELETE FROM _sync_clock WHERE tbl = ? AND row_key = ? AND col = ?",
                              (t, key_text([new]), c))
        self._rekey_children(t, old, new, rid, rid)
        self._set_alias(t, old, new)

    def _merge_rows(self, spec, winner: str, loser: str) -> None:
        """Both rows exist here: the loser's children and newer values go to the winner."""
        t = spec.name
        w_id = self.conn.execute(f"SELECT id FROM {_q(t)} WHERE gid = ?", (winner,)).fetchone()[0]
        l_rowid, l_id = self.conn.execute(f"SELECT rowid, id FROM {_q(t)} WHERE gid = ?", (loser,)).fetchone()
        l_row = self._row(t, l_rowid)
        l_kt = key_text([loser])
        l_clocks = self._clocks(t, l_kt)
        # The name (natural key) merges by LWW like any column: a later rename of either copy
        # wins on every PC, whether it arrives before or after the merge.
        keep = set(KEEP_ON_MERGE.get(t, ()))
        cols, stamps = {}, {}
        for c in self._data_columns(spec):
            if c in keep:
                continue
            cols[c] = (l_row[c], self._fk_gidform(spec, c, l_row[c]))
            stamps[c] = l_clocks.get(c, _NO_STAMP)
        row_stamp = max(l_clocks.values(), default=_NO_STAMP)
        self._rekey_children(t, loser, winner, l_id, w_id)
        self.conn.execute(f"DELETE FROM {_q(t)} WHERE id = ?", (l_id,))
        self.conn.execute("DELETE FROM _sync_clock WHERE tbl = ? AND row_key = ?", (t, l_kt))
        self._set_alias(t, loser, winner)
        if cols and self._upsert_row(spec, key_text([winner]), {"gid": winner}, cols, row_stamp, stamps,
                                     missing_clock_loses=True) == "incomplete":
            _log.warning("apply: merged %s row could not be written (incomplete)", t)

    def _merge(self, spec, a: str, b: str) -> Optional[tuple]:
        """Makes rows ``a`` and ``b`` one row with the smaller gid, whatever this PC holds of
        them. Returns (loser, winner), or None if they already were one row.

        The same call gives the same result on every PC, in any order relative to the rows'
        other changes: a row that isn't here yet is covered by the alias (its changes are
        redirected when they arrive); a deleted side deletes the merged row, exactly as a delete
        arriving after the merge would have."""
        t = spec.name
        a, b = self._follow_alias(self.conn, a), self._follow_alias(self.conn, b)
        if a == b:
            return None
        winner, loser = min(a, b), max(a, b)
        w_row = self.conn.execute(f"SELECT 1 FROM {_q(t)} WHERE gid = ?", (winner,)).fetchone()
        l_row = self.conn.execute(f"SELECT 1 FROM {_q(t)} WHERE gid = ?", (loser,)).fetchone()
        w_tomb = self._tomb(t, key_text([winner]))
        l_tomb = self._tomb(t, key_text([loser]))
        if w_row and l_row:
            self._merge_rows(spec, winner, loser)
        elif l_row:
            if w_tomb is not None:
                self._set_alias(t, loser, winner)
                self._delete(spec, key_text([loser]), {"gid": loser}, w_tomb)
                self.conn.execute("DELETE FROM _sync_tombstones WHERE tbl = ? AND row_key = ?",
                                  (t, key_text([loser])))
            else:
                self._rewrite_gid(spec, loser, winner)
        else:
            self._set_alias(t, loser, winner)
            if l_tomb is not None:
                self.conn.execute("DELETE FROM _sync_tombstones WHERE tbl = ? AND row_key = ?",
                                  (t, key_text([loser])))
                if w_row:
                    self._delete(spec, key_text([winner]), {"gid": winner}, l_tomb)
                else:
                    self._set_tomb(t, key_text([winner]), l_tomb)
        self.r.merges.append((t, loser, winner))
        self.r.tables.add(t)
        return loser, winner

    # ------------------------------------------------ changes made by this PC during apply

    def _emit(self, tbl: str, key: list, op: str, data) -> Optional[dict]:
        """Records a change of this PC's own stream (a merge decision, or the audit row that goes
        with it) so every other PC applies the same thing. Numbered like the sealer does."""
        if not self.emit:
            return None
        stream = sync_capture._meta(self.conn, "stream_id")
        if not self.device_id or not stream:
            _log.warning("apply: %s %s not sent: this database has no stream id", tbl, op)
            return None
        spec = sync_schema.REGISTRY.get(tbl)
        signer = None
        if spec is not None and spec.mode == ADMIN_LWW:
            try:
                signer = self.get_signer() if self.get_signer else None
            except Exception:
                signer = None
            if signer is None:
                _log.warning("apply: %s %s applied here but not sent: no office admin key", tbl, op)
                return None
        floor = sync_capture.read_seq_mark(self.seq_state, stream)
        seq = max(sync_capture._next_seq(self.conn, stream), floor + 1, self.emitted_seq + 1)
        hlc = sync_capture._tick_shared(max(self.last_hlc, sync_capture._meta(self.conn, "last_hlc") or ""),
                                        self.device_id, self.now_ms)
        self.last_hlc = hlc
        ch = {"origin": stream, "origin_seq": seq, "hlc": hlc, "tbl": tbl, "row_key": key_text(key),
              "op": op, "data": data, "sig": None}
        if signer is not None:
            ch["sig"] = sync_capture.sign_change(ch, signer)
        self._record(ch)
        sync_capture._set_meta(self.conn, "next_seq", str(seq + 1))
        sync_capture._set_meta(self.conn, "next_seq_stream", stream)
        sync_capture._set_meta(self.conn, "last_hlc", hlc)
        self.conn.execute("INSERT INTO _sync_vector(origin, max_seq) VALUES (?, ?) "
                          "ON CONFLICT(origin) DO UPDATE SET max_seq = excluded.max_seq", (stream, seq))
        self.emitted_seq = seq
        self.emitted_stream = stream
        self.r.emitted += 1
        return ch

    def _decide_merge(self, spec, a: str, b: str) -> None:
        """This PC found that ``a`` and ``b`` are the same row: merge here and tell everyone."""
        pair = self._merge(spec, a, b)
        if pair is None:
            return
        loser, winner = pair
        self._emit(spec.name, [loser], "merge", {"into": winner})

    def _detect_client_merges(self) -> None:
        """Active clients that share an internal-PK value become one (owner decision 2026-09-24).
        Run once per batch, after everything in it was applied, so a state the batch only passes
        through (e.g. a client archived later in the same batch) doesn't cause a merge."""
        if self.db_name != MASTER_DB or not self._candidates:
            return
        spec = sync_schema.REGISTRY["clients"]
        pk_cols = [r[0] for r in self.conn.execute("SELECT id FROM mcl_columns WHERE is_internal_pk = 1")]
        for gid in sorted(self._candidates):
            gid = self._follow_alias(self.conn, gid)
            row = self.conn.execute("SELECT id, is_archived FROM clients WHERE gid = ?", (gid,)).fetchone()
            if not row or row[1]:
                continue
            group = {gid}
            for col_id in pk_cols:
                v = self.conn.execute("SELECT UPPER(TRIM(value)) FROM client_values WHERE client_id = ? "
                                      "AND column_id = ?", (row[0], col_id)).fetchone()
                if not v or not v[0]:
                    continue
                group |= {g for (g,) in self.conn.execute(
                    "SELECT c.gid FROM clients c JOIN client_values cv ON cv.client_id = c.id "
                    "WHERE c.is_archived = 0 AND cv.column_id = ? AND UPPER(TRIM(cv.value)) = ? AND c.id != ?",
                    (col_id, v[0], row[0]))}
            if len(group) < 2:
                continue
            winner = min(group)
            for loser in sorted(group - {winner}):
                token, created = self.conn.execute(
                    "SELECT client_id_token, created_at FROM clients WHERE gid = ?", (loser,)).fetchone()
                self._decide_merge(spec, loser, winner)
                self._emit_merge_audit(loser, winner, token, created)
                _log.info("apply: client %s merged into another with the same internal PK", token)
        self._candidates.clear()

    def _emit_merge_audit(self, loser: str, winner: str, token, created) -> None:
        """audit_log row naming the merged-away token (D8: staff can still find it). Its gid
        depends on the loser only, so two PCs finding the same duplicate write one row."""
        gid = uuid.uuid5(sync_schema.SERA_NS, f"merge:clients:{loser}").hex
        if self.conn.execute("SELECT 1 FROM audit_log WHERE gid = ?", (gid,)).fetchone():
            return
        w_id = self.conn.execute("SELECT id FROM clients WHERE gid = ?", (winner,)).fetchone()
        row = {"ts": created, "actor": MERGE_ACTOR, "action": MERGE_ACTION,
               "client_id": w_id[0] if w_id else None, "service_id": None,
               "detail": f"Duplicate client {token} merged (same internal PK)"}
        self.conn.execute("INSERT INTO audit_log (ts, actor, action, client_id, service_id, detail, gid) "
                          "VALUES (?, ?, ?, ?, ?, ?, ?)",
                          (row["ts"], row["actor"], row["action"], row["client_id"], row["service_id"],
                           row["detail"], gid))
        data = dict(row, client_id=winner if w_id else None)
        ch = self._emit("audit_log", [gid], "upsert", data)
        kt = key_text([gid])
        stamp = (ch["hlc"], ch["origin"]) if ch else _NO_STAMP
        for c, v in data.items():
            self._set_clock("audit_log", kt, c, stamp, vhash(v))
        self._set_clock("audit_log", kt, PRESENCE_COL, stamp, vhash(None))
        self.r.tables.add("audit_log")

    # ------------------------------------------------ one change

    def apply_one(self, ch: dict):
        """Returns APPLIED/DROPPED/REJECTED/IGNORED or (PARKED, reason)."""
        spec = sync_schema.REGISTRY.get(ch["tbl"])
        if (spec is None or spec.db != self.db_name or spec.mode == LOCAL
                or spec.name not in self._live or len(ch["key"]) != len(spec.row_key)):
            _log.warning("apply: change for %s not applied here (unknown or not replicated)", ch["tbl"])
            return IGNORED
        if spec.mode == ADMIN_LWW and not (
                self.admin_pubkey and sync_capture.verify_change(ch, self.admin_pubkey)):
            _log.warning("apply: %s change from %s rejected: admin signature missing or invalid",
                         spec.name, ch["origin"])
            return REJECTED
        try:
            observed = sync_capture.observe(self.last_hlc, ch["hlc"], self.device_id, self.now_ms)
        except ClockAhead as e:
            self.r.clock_ahead.append((ch["origin"], e.ahead_ms))
            _log.warning("apply: change from %s parked: its clock is ahead by %d minutes",
                         ch["origin"], e.ahead_ms // 60000)
            return PARKED, "clock_ahead"
        outcome = self._apply(spec, ch)
        if outcome not in (IGNORED,) and not isinstance(outcome, tuple):
            self.last_hlc = max(self.last_hlc, observed)
        return outcome

    def _apply(self, spec, ch):
        t = spec.name
        stamp = (ch["hlc"], ch["origin"])
        status, key_local, canon, redirected, gone = self._resolve_key(spec, ch["key"])
        kt = key_text(canon)
        gid_keyed = spec.row_key == ("gid",)

        if ch["op"] == "merge":
            if t not in MERGEABLE_TABLES:
                _log.warning("apply: merge for %s from %s ignored (not a mergeable table)", t, ch["origin"])
                return IGNORED
            self._merge(spec, ch["key"][0], ch["data"]["into"])
            return APPLIED

        if ch["op"] == "delete":
            if spec.mode == APPEND:
                # Append tables are insert-only (§4.4): no PC can remove another's rows.
                _log.warning("apply: delete for %s from %s ignored (append-only)", t, ch["origin"])
                return IGNORED
            if status == "gone":
                return DROPPED             # the parent's tombstone already covers it
            self._delete(spec, kt, key_local if status == "ok" else None, stamp)
            self.r.tables.add(t)
            return APPLIED

        data = ch["data"] or {}
        if status == "gone":
            if gone is not None and stamp > gone:
                for c, v in data.items():
                    self._conflict(t, kt, c, None, _decode(v), "edit after delete discarded")
            return DROPPED
        if status == "missing":
            return PARKED, "missing_parent"

        tomb = self._tomb(t, kt)
        resurrect = None
        if tomb is not None:
            if gid_keyed:
                if stamp > tomb:
                    for c, v in data.items():
                        self._conflict(t, kt, c, None, _decode(v), "edit after delete discarded")
                return DROPPED
            if stamp <= tomb:
                return DROPPED
            resurrect = tomb

        cols = self._translate_data(spec, data, redirected)
        if cols is None:
            return PARKED, "missing_parent"

        if gid_keyed and spec.natural_merge_on and self._find_rowid(spec, key_local) is None:
            nm = spec.natural_merge_on
            if nm in cols and cols[nm][0] is not None:
                other = self.conn.execute(f"SELECT gid FROM {_q(t)} WHERE {_q(nm)} = ?",
                                          (cols[nm][0],)).fetchone()
                if other and other[0] != key_local["gid"]:
                    self._decide_merge(spec, key_local["gid"], other[0])
                    g = self._follow_alias(self.conn, key_local["gid"])
                    key_local, kt = {"gid": g}, key_text([g])

        if spec.mode == APPEND and self._find_rowid(spec, key_local) is not None:
            return APPLIED                 # append rows never change once written

        col_stamps = {}
        if resurrect is not None:
            self.conn.execute("DELETE FROM _sync_tombstones WHERE tbl = ? AND row_key = ?", (t, kt))
            self._absent_fill(spec, cols, col_stamps, resurrect)
        existed = self._find_rowid(spec, key_local) is not None
        res = self._upsert_row(spec, kt, key_local, cols, stamp, col_stamps)
        if res == "incomplete":
            return PARKED, "incomplete_row"
        self.r.tables.add(t)

        # Clients to check for a duplicate internal PK at the end of the batch.
        if t == "client_values":
            pk = self.conn.execute("SELECT is_internal_pk FROM mcl_columns WHERE id = ?",
                                   (key_local["column_id"],)).fetchone()
            if pk and pk[0]:
                self._candidates.add(canon[0])
        elif t == "clients" and (not existed or "is_archived" in cols):
            self._candidates.add(key_local["gid"])
        return APPLIED

    # ------------------------------------------------ batch

    def _have(self, ch) -> bool:
        return self.conn.execute("SELECT 1 FROM _sync_changes WHERE origin = ? AND origin_seq = ?",
                                 (ch["origin"], ch["origin_seq"])).fetchone() is not None

    def _record(self, ch) -> None:
        self.conn.execute(
            "INSERT INTO _sync_changes(origin, origin_seq, hlc, tbl, row_key, op, data, sig) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ch["origin"], ch["origin_seq"], ch["hlc"], ch["tbl"], ch["row_key"], ch["op"],
             None if ch["data"] is None else json.dumps(ch["data"], sort_keys=True, ensure_ascii=False),
             ch["sig"]))

    def _handle(self, ch):
        self.conn.execute("SAVEPOINT sync_apply_change")
        try:
            outcome = self.apply_one(ch)
        except BaseException:
            self.conn.execute("ROLLBACK TO sync_apply_change")
            self.conn.execute("RELEASE sync_apply_change")
            raise
        if isinstance(outcome, tuple):
            self.conn.execute("ROLLBACK TO sync_apply_change")
        self.conn.execute("RELEASE sync_apply_change")
        return outcome

    def _park(self, ch, reason: str) -> None:
        self.conn.execute("INSERT INTO _sync_parked(change_json, reason, first_at, tries) VALUES (?, ?, ?, 0)",
                          (_change_json(ch), reason, _now_iso()))
        k = self._park_key(ch)
        if k not in self._parked_keys or ch["hlc"] < self._parked_keys[k]:
            self._parked_keys[k] = ch["hlc"]
        self.r.parked += 1

    def _count(self, outcome) -> None:
        if outcome == APPLIED:
            self.r.applied += 1
        elif outcome == DROPPED:
            self.r.dropped += 1
        elif outcome == IGNORED:
            self.r.ignored += 1

    def run(self, changes: list) -> None:
        self._load_parked_keys()
        origins = set()
        for ch in sorted(changes, key=lambda c: (c["hlc"], c["origin"], c["origin_seq"])):
            if self._have(ch):
                self.r.skipped += 1
                continue
            spec = sync_schema.REGISTRY.get(ch["tbl"])
            if spec is not None and spec.mode == ADMIN_LWW and not (
                    self.admin_pubkey and sync_capture.verify_change(ch, self.admin_pubkey)):
                # Not recorded either: only a genuine change may take this (origin, origin_seq).
                _log.warning("apply: %s change from %s rejected: admin signature missing or invalid",
                             ch["tbl"], ch["origin"])
                self.r.rejected += 1
                continue
            self._record(ch)
            origins.add(ch["origin"])
            oldest = self._parked_keys.get(self._park_key(ch))
            if oldest is not None and ch["hlc"] > oldest:
                # A later change of a row whose earlier change is parked waits behind it.
                self._park(ch, "behind_parked")
                continue
            outcome = self._handle(ch)
            if isinstance(outcome, tuple):
                self._park(ch, outcome[1])
            else:
                self._count(outcome)
        self.retry_parked()
        self._detect_client_merges()
        self._advance_vectors(origins)
        if self.last_hlc and self.last_hlc != (sync_capture._meta(self.conn, "last_hlc") or ""):
            sync_capture._set_meta(self.conn, "last_hlc", self.last_hlc)

    def _park_key(self, ch) -> tuple:
        """(tbl, row key) with gid parts taken through ``_sync_alias``, so a change sent under a
        merged-away gid waits behind a parked change of the row it now belongs to."""
        tbl, row_key = ch.get("tbl"), ch.get("row_key")
        spec = sync_schema.REGISTRY.get(tbl)
        try:
            key = json.loads(row_key) if isinstance(row_key, str) else None
        except ValueError:
            key = None
        if spec is None or not isinstance(key, list) or len(key) != len(spec.row_key):
            return (tbl, row_key)
        out = []
        for col, part in zip(spec.row_key, key):
            if col == "gid":
                out.append(self._follow_alias(self.conn, part))
            elif col in spec.fk and isinstance(part, str) and sync_capture._GID_RE.match(part):
                out.append(self._follow_alias(self._conn_for(spec.fk[col]), part))
            else:
                out.append(part)
        return (tbl, key_text(out))

    def _load_parked_keys(self) -> None:
        self._parked_keys = {}
        for (j,) in self.conn.execute("SELECT change_json FROM _sync_parked"):
            try:
                c = json.loads(j)
            except ValueError:
                continue
            k = self._park_key(c)
            if k not in self._parked_keys or (c.get("hlc") or "") < self._parked_keys[k]:
                self._parked_keys[k] = c.get("hlc") or ""

    def retry_parked(self) -> None:
        start = [r[0] for r in self.conn.execute("SELECT id FROM _sync_parked")]
        if not start:
            return
        progress = True
        while progress:
            progress = False
            blocked = set()
            parked = []
            for pid, cj in self.conn.execute("SELECT id, change_json FROM _sync_parked").fetchall():
                try:
                    parked.append((pid, _normalise_change(json.loads(cj))))
                except (ApplyError, ValueError):
                    _log.warning("apply: unreadable parked change %d left in place", pid)
            # In HLC order, like a batch: an earlier change of a row goes before a later one.
            parked.sort(key=lambda p: (p[1]["hlc"], p[1]["origin"], p[1]["origin_seq"]))
            for pid, ch in parked:
                k = self._park_key(ch)
                if k in blocked:
                    continue
                outcome = self._handle(ch)
                if isinstance(outcome, tuple):
                    blocked.add(k)
                    self.conn.execute("UPDATE _sync_parked SET reason = ? WHERE id = ?", (outcome[1], pid))
                    continue
                self.conn.execute("DELETE FROM _sync_parked WHERE id = ?", (pid,))
                self._count(outcome)
                self.r.unparked += 1
                progress = True
        self.conn.execute(f"UPDATE _sync_parked SET tries = tries + 1 WHERE id IN "
                          f"({', '.join('?' for _ in start)})", start)
        self._load_parked_keys()

    def _advance_vectors(self, origins) -> None:
        for origin in origins:
            row = self.conn.execute("SELECT max_seq FROM _sync_vector WHERE origin = ?", (origin,)).fetchone()
            cur = row[0] if row else 0
            new = cur
            for (s,) in self.conn.execute("SELECT origin_seq FROM _sync_changes WHERE origin = ? AND origin_seq > ? "
                                          "ORDER BY origin_seq", (origin, cur)):
                if s != new + 1:
                    break
                new = s
            if new != cur or row is None:
                self.conn.execute("INSERT INTO _sync_vector(origin, max_seq) VALUES (?, ?) "
                                  "ON CONFLICT(origin) DO UPDATE SET max_seq = excluded.max_seq", (origin, new))


# ---------------------------------------------------------------- public API

def _open(path: str, hex_key: str, timeout: float):
    conn = sync_capture._open(path, hex_key, timeout)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _open_master_ro(master_path: str, hex_key: str, timeout: float):
    m = sync_capture._open(master_path, hex_key, timeout)
    m.execute("PRAGMA query_only = 1;")
    return m


def _seal_pending_in_transaction(conn, db_name, master_path, hex_key, timeout, get_signer,
                                 now_ms, seq_state):
    """Seals local pending rows inside the apply transaction. Returns (changes, stream, last_seq)."""
    if sync_capture.read_mode(conn) not in ACTIVE_MODES:
        return 0, None, 0
    if conn.execute("SELECT 1 FROM _sync_pending LIMIT 1").fetchone() is None:
        return 0, None, 0
    opener = None
    if db_name != MASTER_DB and master_path:
        def opener():
            return _open_master_ro(master_path, hex_key, timeout)
    gids = sync_capture._GidLookup(conn, db_name, opener)
    stream = sync_capture._meta(conn, "stream_id")
    total, last = 0, 0
    signer_cache: dict = {}
    try:
        while conn.execute("SELECT 1 FROM _sync_pending LIMIT 1").fetchone() is not None:
            floor = max(sync_capture.read_seq_mark(seq_state, stream) if stream else 0, last)
            r = sync_capture._seal_in_transaction(conn, db_name, gids, get_signer, now_ms,
                                                  sync_capture.SEAL_BATCH_ROWS, signer_cache, floor)
            total += r.changes
            last = max(last, r.last_seq)
            if r.pending == 0:
                break
    finally:
        gids.close()
    return total, stream, last


def apply_batch(db_path: str, hex_key: str, db_name: str, changes, *,
                master_path: Optional[str] = None, admin_pubkey: Optional[str] = None,
                get_signer: Optional[Callable[[], object]] = None, seq_state: Optional[str] = None,
                now_ms: Optional[int] = None, timeout: float = 5.0, emit: bool = True) -> ApplyResult:
    """Applies ``changes`` (dicts as in ``_sync_changes``; ``data`` as a dict or JSON text) to one
    DB file in one transaction, then retries parked changes. See the module docstring.

    ``master_path`` is required for rawPayload.db (client/service gids live in master.db).
    ``admin_pubkey`` verifies admin-scoped changes; without it they are rejected.
    ``get_signer`` / ``seq_state`` are passed to the sealer for local pending rows.
    ``emit``: a merge this PC decides (duplicate internal PK, natural-key collision) is added to
    this DB's own stream, so every PC makes the same merge. False applies it here only; for a
    copy of a DB that shares its stream id (P3-7 shadow replicas) so numbers never collide.
    Raises ``ApplyError`` for a malformed change (nothing applied) and re-raises any database
    error after rolling the whole batch back.
    """
    if db_name not in (MASTER_DB, RAW_DB):
        raise ValueError(f"unknown database {db_name!r}")
    parsed = [_normalise_change(c) for c in changes]
    result = ApplyResult()
    conn = _open(db_path, hex_key, timeout)
    master = None
    try:
        if not parsed and conn.execute("SELECT 1 FROM _sync_parked LIMIT 1").fetchone() is None:
            return result
        if db_name != MASTER_DB:
            if not master_path:
                raise ValueError("master_path is needed to apply rawPayload.db changes")
            master = _open_master_ro(master_path, hex_key, timeout)
        conn.execute("BEGIN IMMEDIATE")
        stream, last_seq = None, 0
        try:
            sealed, stream, last_seq = _seal_pending_in_transaction(
                conn, db_name, master_path, hex_key, timeout, get_signer, now_ms, seq_state)
            result.sealed = sealed
            conn.execute("UPDATE _sync_flags SET value = 1 WHERE name = 'applying'")
            try:
                applier = _Applier(conn, db_name, master, admin_pubkey, now_ms, result,
                                   get_signer=get_signer, seq_state=seq_state, emit=emit)
                applier.run(parsed)
            finally:
                conn.execute("UPDATE _sync_flags SET value = 0 WHERE name = 'applying'")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        if last_seq and stream:
            sync_capture.record_seq_mark(seq_state, stream, last_seq)
        if applier.emitted_seq:
            sync_capture.record_seq_mark(seq_state, applier.emitted_stream, applier.emitted_seq)
        sync_capture.note_observed(applier.device_id, applier.last_hlc)
        return result
    finally:
        try:
            # The rollback restores 0 anyway; this only guards against a flag left at 1 by a
            # database that was edited by hand.
            row = conn.execute("SELECT value FROM _sync_flags WHERE name = 'applying'").fetchone()
            if row and row[0] == 1 and not conn.in_transaction:
                conn.execute("UPDATE _sync_flags SET value = 0 WHERE name = 'applying'")
        except Exception:
            pass
        if master is not None:
            master.close()
        conn.close()


def retry_parked(db_path: str, hex_key: str, db_name: str, **kw) -> ApplyResult:
    """Retries parked changes only (e.g. rawPayload.db after master.db received new clients)."""
    return apply_batch(db_path, hex_key, db_name, [], **kw)


def run_raw_repoints(master_path: str, raw_path: str, hex_key: str, timeout: float = 5.0) -> int:
    """Points rawPayload.db rows at the client/service a merge in master.db kept.

    master.db and rawPayload.db are separate files, so this runs after the master transaction
    committed. Jobs stay in master.db's ``_sync_meta`` until rawPayload.db has committed, and
    re-running a job is harmless (AUTOINCREMENT ids are never reused). Returns the jobs done.
    """
    m = sync_capture._open(master_path, hex_key, timeout)
    try:
        jobs = json.loads(sync_capture._meta(m, REPOINT_META_KEY) or "[]")
    finally:
        m.close()
    if not jobs:
        return 0
    r = _open(raw_path, hex_key, timeout)
    try:
        live = {x[0] for x in r.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        r.execute("BEGIN IMMEDIATE")
        try:
            r.execute("UPDATE _sync_flags SET value = 1 WHERE name = 'applying'")
            for table, old_id, new_id, new_gid in jobs:
                for spec in sync_schema.tables_for(RAW_DB):
                    if spec.name not in live:
                        continue
                    for col, target in spec.fk.items():
                        if target != table:
                            continue
                        key_cols = list(spec.row_key)
                        rows = r.execute(
                            f"SELECT {', '.join(_q(c) for c in key_cols)} FROM {_q(spec.name)} "
                            f"WHERE {_q(col)} = ?", (old_id,)).fetchall() if key_cols else []
                        if old_id != new_id:
                            r.execute(f"UPDATE {_q(spec.name)} SET {_q(col)} = ? WHERE {_q(col)} = ?",
                                      (new_id, old_id))
                        if spec.mode == LOCAL:
                            continue
                        for key in rows:
                            r.execute("UPDATE _sync_clock SET vhash = ? WHERE tbl = ? AND row_key = ? AND col = ?",
                                      (vhash(new_gid), spec.name, key_text(list(key)), col))
            r.execute("UPDATE _sync_flags SET value = 0 WHERE name = 'applying'")
            r.execute("COMMIT")
        except BaseException:
            r.execute("ROLLBACK")
            raise
    finally:
        r.close()
    m = sync_capture._open(master_path, hex_key, timeout)
    try:
        m.execute("BEGIN IMMEDIATE")
        now = json.loads(sync_capture._meta(m, REPOINT_META_KEY) or "[]")
        rest = now[len(jobs):] if now[:len(jobs)] == jobs else [j for j in now if j not in jobs]
        sync_capture._set_meta(m, REPOINT_META_KEY, json.dumps(rest))
        m.execute("COMMIT")
    finally:
        m.close()
    return len(jobs)


def parked_changes(db_path: str, hex_key: str, older_than_seconds: Optional[float] = None,
                   now: Optional[datetime.datetime] = None, timeout: float = 5.0) -> list:
    """Parked changes (no values: table, row key, origin, reason, first_at, tries). With
    ``older_than_seconds`` only those parked at least that long (the panel uses 7 days)."""
    conn = sync_capture._open(db_path, hex_key, timeout)
    try:
        rows = conn.execute("SELECT id, change_json, reason, first_at, tries FROM _sync_parked ORDER BY id").fetchall()
    finally:
        conn.close()
    now = now or datetime.datetime.now(datetime.timezone.utc)
    out = []
    for pid, cj, reason, first_at, tries in rows:
        try:
            ch = json.loads(cj)
            first = datetime.datetime.fromisoformat(first_at)
        except (ValueError, TypeError):
            continue
        if older_than_seconds is not None and (now - first).total_seconds() < older_than_seconds:
            continue
        out.append({"id": pid, "tbl": ch.get("tbl"), "row_key": ch.get("row_key"),
                    "origin": ch.get("origin"), "origin_seq": ch.get("origin_seq"),
                    "reason": reason, "first_at": first_at, "tries": tries})
    return out
