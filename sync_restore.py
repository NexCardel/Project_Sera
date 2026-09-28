"""Sera Sync v3 — restoring a backup becomes the state on every PC (blueprint §5, WP P4-3b, D9).

Who: the admin PC only (it holds the office admin key; any PC can become admin with the master
password, D3), in sync mode ``live``. Every change of the restore set is signed with the admin key.

1. ``plan_restore(db, backup_dir)`` checks the backup (both DB files, opens with the office key,
   made by Sera Sync v3: every client has a gid, same sync schema) and returns the dry-run counts
   for the confirmation: clients that come back, clients created since the backup that will be
   removed, client fields that revert. The UI asks the user to type ``RESTORE``.
2. ``stage_restore(db, backup_dir, actor=...)`` backs up the current state (P4-3a,
   ``backups/pre-restore-<ts>/``), copies the backup into ``incoming/restore/new/`` and brings its
   schema up to date there (``SeraDatabase`` on the copy, sync mode off so nothing is captured).
   Nothing live changes; Sera then restarts.
3. ``apply_pending_restore(app_dir)`` at the next start-up, **before any database is opened**
   (``main.py``), builds the new live DBs in ``incoming/restore/prep/`` from the staged copy:
     - the current ``_sync_*`` and ``_local_*`` tables are kept (membership, change log, vectors,
       sequence state, address book), the backup's are not;
     - a backup row whose gid is tombstoned or merged away (``_sync_alias``) today gets a new gid,
       because every PC drops an upsert for a tombstoned gid ("delete wins"): it comes back as a
       new row;
     - **re-assert**: every replicated row is sealed with all its columns as fresh changes (new
       HLCs), so it beats everything older on every PC;
     - every row key that exists now but not in the backup gets a delete (tombstone), so other
       PCs remove what was created after the backup (children of a removed parent are covered by
       the parent's tombstone);
     - ``audit_log`` is append-only (§4.4): no PC can remove audit rows, so today's rows are kept
       and only the backup's rows that are missing today are re-sent;
     - local-mode tables (activity counters, ``client_raw_containers``) are the backup's;
     - one ``audit_log`` row ``action="office_restore"`` with the backup name and the counts.
   Then the files are swapped in with a journal (the replaced DBs go to
   ``backups/replaced-by-restore-<ts>/``, §0 rule 3) and the sequence high-water marks are raised.

Known limit (in the confirmation text): a PC that was offline during the restore keeps new
clients it created that the admin PC never saw; its edits to restored rows lose, because they
are older than the restore.

No PySide6 import (blueprint §0 rule 7).
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import shutil
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import sync_compaction
import sync_schema
from sync_schema import APPEND, MASTER_DB, RAW_DB

_log = logging.getLogger("sera.sync.restore")

RESTORE_DIRNAME = "restore"                # incoming/restore/
PENDING_FILE = "pending.json"
JOURNAL_FILE = "installing.json"
REPLACED_PREFIX = "replaced-by-restore-"   # backups/replaced-by-restore-<ts>/
AUDIT_ACTION = "office_restore"
CONFIRM_WORD = "RESTORE"
MASTER_DB_NAME = sync_compaction.MASTER_DB_NAME
RAW_DB_NAME = sync_compaction.RAW_DB_NAME

# Columns that say nothing about the client's data (not counted as "fields that revert").
_CLIENT_COLS_NOT_COUNTED = {"id", "gid", "updated_at", "created_at"}


class RestoreRefused(RuntimeError):
    """The restore can't start (nothing was touched). The message is for the user."""


class RestoreError(RuntimeError):
    """The restore failed; the live databases are unchanged."""


@dataclass
class RestorePlan:
    backup_dir: str
    backup_name: str
    backup_date: str
    clients_back: int
    clients_removed: int
    fields_revert: int

    def summary(self) -> str:
        return (f"{self.clients_back} client(s) will come back, {self.clients_removed} client(s) "
                f"created since {self.backup_date} will be removed, {self.fields_revert} field(s) "
                "will revert. All PCs will follow.")

    def confirmation_text(self) -> str:
        return (f"Restore backup {self.backup_name} ({self.backup_date}) as the office's data.\n\n"
                f"{self.summary()}\n\n"
                "Known limit: a PC that is offline now and has created new clients this PC hasn't "
                "received keeps those clients when it reconnects. Its edits to restored clients are "
                "lost, because they are older than the restore.\n\n"
                "Sera restarts to do the restore; the current data is backed up first.")


# ---------------------------------------------------------------- helpers

def _stage_dir(app_dir) -> Path:
    return Path(app_dir) / "incoming" / RESTORE_DIRNAME


def has_pending_restore(app_dir) -> bool:
    d = _stage_dir(app_dir)
    return (d / PENDING_FILE).exists() or (d / JOURNAL_FILE).exists()


def _open(path, hex_key: str, read_only: bool = False):
    import sync_capture
    conn = sync_capture._open(str(path), hex_key, 10.0)
    if read_only:
        conn.execute("PRAGMA query_only = 1;")
    return conn


def _tables(conn, schema: str = "main") -> set:
    return {r[0] for r in conn.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type='table'")}


def _backup_date(backup: Path) -> str:
    try:
        manifest = json.loads((backup / "backup_manifest.json").read_text(encoding="utf-8"))
        created = datetime.datetime.fromisoformat(manifest["created_at"])
        return created.strftime("%Y-%m-%d %H:%M")
    except Exception:
        ts = (backup / MASTER_DB_NAME).stat().st_mtime
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _schema_version(conn) -> Optional[str]:
    try:
        row = conn.execute("SELECT value FROM _sync_meta WHERE key = 'schema_version'").fetchone()
    except Exception:
        return None
    return row[0] if row else None


def _check_backup(backup: Path, hex_key: str, live_master: Path) -> None:
    if not backup.is_dir():
        raise RestoreRefused("choose the backup's folder (it holds master.db and rawPayload.db)")
    for name in (MASTER_DB_NAME, RAW_DB_NAME):
        if not (backup / name).is_file():
            raise RestoreRefused(f"the backup has no {name}; only a complete backup (both databases) "
                                 "can be restored for the whole office")
        try:
            conn = _open(backup / name, hex_key, read_only=True)
            try:
                conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
            finally:
                conn.close()
        except Exception:
            raise RestoreRefused(f"the backup's {name} doesn't open with this office's key") from None
    conn = _open(backup / MASTER_DB_NAME, hex_key, read_only=True)
    try:
        tables = _tables(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(clients)")} if "clients" in tables else set()
        if "_sync_meta" not in tables or "gid" not in cols or conn.execute(
                "SELECT 1 FROM clients WHERE gid IS NULL OR gid = '' LIMIT 1").fetchone():
            raise RestoreRefused("this backup was made before the office used Sera Sync v3, so it "
                                 "can't be restored for the whole office")
        backup_version = _schema_version(conn)
    finally:
        conn.close()
    live = _open(live_master, hex_key, read_only=True)
    try:
        live_version = _schema_version(live)
    finally:
        live.close()
    if backup_version != live_version:
        raise RestoreRefused(f"the backup uses sync schema {backup_version}, this PC {live_version}")


def _client_state(conn) -> tuple[dict, dict]:
    """({client gid: {col: value}}, {client gid: {column gid: value}})."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(clients)")
            if r[1] not in _CLIENT_COLS_NOT_COUNTED]
    rows = {}
    for r in conn.execute(f"SELECT gid, {', '.join(cols)} FROM clients" if cols else "SELECT gid FROM clients"):
        rows[r[0]] = dict(zip(cols, r[1:]))
    values: dict = {}
    for cgid, mgid, value in conn.execute(
            "SELECT c.gid, m.gid, cv.value FROM client_values cv JOIN clients c ON c.id = cv.client_id "
            "JOIN mcl_columns m ON m.id = cv.column_id"):
        values.setdefault(cgid, {})[mgid] = value
    return rows, values


def count_changes(current_master, backup_master, hex_key: str) -> tuple[int, int, int]:
    """(clients that come back, clients that go, client fields that revert), comparing by gid."""
    cur = _open(current_master, hex_key, read_only=True)
    new = _open(backup_master, hex_key, read_only=True)
    try:
        cur_rows, cur_vals = _client_state(cur)
        new_rows, new_vals = _client_state(new)
    finally:
        cur.close()
        new.close()
    back = len(set(new_rows) - set(cur_rows))
    gone = len(set(cur_rows) - set(new_rows))

    def norm(v):
        return "" if v is None else str(v)

    fields = 0
    for gid in set(cur_rows) & set(new_rows):
        a, b = cur_rows[gid], new_rows[gid]
        fields += sum(1 for c in set(a) | set(b) if norm(a.get(c)) != norm(b.get(c)))
        av, bv = cur_vals.get(gid, {}), new_vals.get(gid, {})
        fields += sum(1 for c in set(av) | set(bv) if norm(av.get(c)) != norm(bv.get(c)))
    return back, gone, fields


# ---------------------------------------------------------------- plan + stage

def plan_restore(db, backup_dir, app_dir=None) -> RestorePlan:
    """Checks that this PC may restore ``backup_dir`` office-wide and returns the dry-run counts.
    Raises ``RestoreRefused`` with a message for the user."""
    import sera_keys
    import sync_admin
    import sync_shadow
    app = Path(app_dir or os.path.dirname(os.path.abspath(db.db_path)))
    if sera_keys.load_office(app) is None:
        raise RestoreRefused("restoring for the whole office needs an office key on this PC")
    if not sync_admin.has_admin_key(app) or not db.is_admin_pc():
        raise RestoreRefused("only the admin PC can restore a backup for the whole office "
                             "(Sera Sync panel: Become admin, with the master password)")
    if db.get_sync_mode() != "live":
        raise RestoreRefused(f"restoring for the whole office needs Sera Sync live "
                             f"(this PC is in mode {db.get_sync_mode()!r})")
    if has_pending_restore(app):
        raise RestoreRefused("a restore is already waiting for Sera to restart")
    if sync_shadow.has_pending_go_live(app):
        raise RestoreRefused("going live is waiting for a restart; restart Sera first")
    backup = Path(backup_dir)
    _check_backup(backup, db.hex_key, Path(db.db_path))
    back, gone, fields = count_changes(db.db_path, backup / MASTER_DB_NAME, db.hex_key)
    return RestorePlan(str(backup), backup.name, _backup_date(backup), back, gone, fields)


def _prepare_staged_copy(new_dir: Path, hex_key: str) -> None:
    """Mode off (nothing captured) and the current schema (migrations run by ``SeraDatabase``)."""
    for name in (MASTER_DB_NAME, RAW_DB_NAME):
        conn = _open(new_dir / name, hex_key)
        try:
            conn.execute("UPDATE _sync_meta SET value = 'off' WHERE key = 'mode'")
            conn.execute("UPDATE _sync_flags SET value = 0 WHERE name = 'applying'")
        finally:
            conn.close()
    from database import SeraDatabase
    SeraDatabase(str(new_dir / MASTER_DB_NAME), hex_key, raw_db_path=str(new_dir / RAW_DB_NAME),
                 defer_startup_maintenance=True, key_mode="office")
    for name in (MASTER_DB_NAME, RAW_DB_NAME):
        sync_compaction.checkpoint(new_dir / name, hex_key)


def stage_restore(db, backup_dir, *, actor: str, app_dir=None) -> Path:
    """Stages the restore of ``backup_dir`` for the next start-up (module docstring, step 2).
    Returns the path of ``incoming/restore/pending.json``. Raises ``RestoreRefused`` (nothing
    touched) or ``RestoreError`` (nothing staged)."""
    import sync_backup
    import sync_shadow
    app = Path(app_dir or os.path.dirname(os.path.abspath(db.db_path)))
    plan = plan_restore(db, backup_dir, app)
    hex_key = db.hex_key
    try:
        pre = sync_backup.backup_before_restore(app, hex_key=hex_key, db=db)
    except Exception as exc:
        raise RestoreError(f"the backup before restoring failed: {exc}") from exc
    stage = _stage_dir(app)
    new_dir = stage / "new"
    try:
        shutil.rmtree(stage, ignore_errors=True)
        new_dir.mkdir(parents=True)
        backup = Path(plan.backup_dir)
        for name in (MASTER_DB_NAME, RAW_DB_NAME):
            sync_backup.export_database(backup / name, new_dir / name, hex_key)
        _prepare_staged_copy(new_dir, hex_key)
        files = [{"name": n, "sha256": sync_shadow._sha256(new_dir / n)} for n in (MASTER_DB_NAME, RAW_DB_NAME)]
        pending = stage / PENDING_FILE
        sync_shadow._write_json(pending, {
            "staged_at": sync_shadow._utc_now_iso(), "actor": actor or "System",
            "plan": asdict(plan), "pre_restore_backup": str(pre), "files": files, "pid": os.getpid()})
    except BaseException as exc:
        shutil.rmtree(stage, ignore_errors=True)
        if not isinstance(exc, Exception):
            raise
        raise RestoreError(f"the restore could not be prepared: {exc}") from exc
    # A catch-up staged earlier would install an older snapshot over the restore.
    sync_compaction.cancel_catch_up(app)
    _log.warning("restore of backup %s staged; it happens at the next start of Sera", plan.backup_name)
    return pending


# ---------------------------------------------------------------- install: rebuild one DB file

def _replace_sync_tables(conn) -> None:
    """main's ``_sync_*`` / ``_local_*`` tables (and their indexes) become copies of old's."""
    pattern = "(name LIKE '\\_sync\\_%' ESCAPE '\\' OR name LIKE '\\_local\\_%' ESCAPE '\\')"
    for (name,) in conn.execute(f"SELECT name FROM main.sqlite_master WHERE type='table' AND {pattern}").fetchall():
        conn.execute(f'DROP TABLE main."{name}"')
    old = conn.execute(f"SELECT name, sql FROM old.sqlite_master WHERE type='table' AND {pattern}").fetchall()
    for name, sql in old:
        conn.execute(sql)
        conn.execute(f'INSERT INTO main."{name}" SELECT * FROM old."{name}"')
    names = [n for n, _ in old]
    if names:
        marks = ", ".join("?" for _ in names)
        for (sql,) in conn.execute(f"SELECT sql FROM old.sqlite_master WHERE type='index' AND sql IS NOT NULL "
                                   f"AND tbl_name IN ({marks})", names).fetchall():
            conn.execute(sql)


def _regenerate_gids(conn, spec) -> int:
    """Rows whose gid is tombstoned or merged away today get a new gid (they come back as new rows)."""
    t = spec.name
    rows = conn.execute(
        f'SELECT gid FROM main."{t}" WHERE gid IN (SELECT json_extract(row_key, \'$[0]\') FROM main._sync_tombstones '
        f"WHERE tbl = ?) OR gid IN (SELECT alias_gid FROM main._sync_alias WHERE tbl = ?)", (t, t)).fetchall()
    for (gid,) in rows:
        conn.execute(f'UPDATE main."{t}" SET gid = ? WHERE gid = ?', (uuid.uuid4().hex, gid))
    return len(rows)


def _key_set(conn, spec, gids) -> dict:
    """{key text: key list} of every row of ``spec`` in ``conn`` (main schema), keys in gid form."""
    import sync_capture
    cols = [r[1] for r in conn.execute(f'PRAGMA main.table_info("{spec.name}")')]
    out = {}
    for row in conn.execute(f'SELECT {", ".join(chr(34) + c + chr(34) for c in cols)} FROM main."{spec.name}"'):
        key = sync_capture._row_key_of(spec, dict(zip(cols, row)), gids)
        if key is not None:
            out[sync_capture.key_text(key)] = key
    return out


def _fk_map_sql(target: str, old_schema: str, new_schema: str) -> str:
    return (f'SELECT o.id AS old_id, n.id AS new_id FROM {old_schema}."{target}" o '
            f'JOIN {new_schema}."{target}" n ON n.gid = o.gid')


def _merge_append_table(conn, spec, schemas: dict) -> list:
    """Append-only table (audit_log): today's rows are kept (no PC can delete them); returns the
    rowids of the backup's rows that are missing today, which are re-sent."""
    t = spec.name
    backup_only = [r[0] for r in conn.execute(
        f'SELECT gid FROM main."{t}" WHERE gid NOT IN (SELECT gid FROM old."{t}" WHERE gid IS NOT NULL)')]
    conn.execute(f'DELETE FROM main."{t}" WHERE gid IN (SELECT gid FROM old."{t}")')
    new_cols = {r[1] for r in conn.execute(f'PRAGMA main.table_info("{t}")')}
    cols = [r[1] for r in conn.execute(f'PRAGMA old.table_info("{t}")') if r[1] in new_cols]
    sel, joins = [], []
    for c in cols:
        target = spec.fk.get(c)
        if target is None:
            sel.append(f'o."{c}"')
            continue
        alias = f"m_{c}"
        o_schema, n_schema = schemas[target]
        joins.append(f'LEFT JOIN ({_fk_map_sql(target, o_schema, n_schema)}) {alias} ON {alias}.old_id = o."{c}"')
        sel.append(f"{alias}.new_id")
    base = f'FROM old."{t}" o {" ".join(joins)}'
    col_sql = ", ".join(f'"{c}"' for c in cols)
    if "id" in cols:
        # Keep today's local ids where they are free; a clash gets a new one (ids are local, D5).
        free = f' WHERE o.id NOT IN (SELECT id FROM main."{t}")'
        conn.execute(f'INSERT INTO main."{t}" ({col_sql}) SELECT {", ".join(sel)} {base}{free}')
        rest = [c for c in cols if c != "id"]
        rest_sel = [s for c, s in zip(cols, sel) if c != "id"]
        conn.execute(f'INSERT INTO main."{t}" ({", ".join(chr(34) + c + chr(34) for c in rest)}) '
                     f'SELECT {", ".join(rest_sel)} {base} WHERE o.id IN (SELECT id FROM main."{t}") '
                     f'AND o.gid NOT IN (SELECT gid FROM main."{t}")')
    else:
        conn.execute(f'INSERT INTO main."{t}" ({col_sql}) SELECT {", ".join(sel)} {base}')
    marks = ", ".join("?" for _ in backup_only)
    return [r[0] for r in conn.execute(f'SELECT rowid FROM main."{t}" WHERE gid IN ({marks})', backup_only)] \
        if backup_only else []


def _rebuild(new_path: Path, old_path: Path, hex_key: str, which: str, *, signer, app: Path,
             new_master: Optional[Path] = None, old_master: Optional[Path] = None,
             audit: Optional[dict] = None, now_ms: Optional[int] = None) -> int:
    """Turns the staged backup copy at ``new_path`` into the new live DB (module docstring,
    step 3) and seals the restore change set into it. Returns the last own seq issued."""
    import sync_capture
    db_name = MASTER_DB if which == "master" else RAW_DB
    conn = _open(new_path, hex_key)
    old_conn = _open(old_path, hex_key, read_only=True)
    attached = []
    gids_old = gids_new = None
    try:
        def attach(alias, p):
            escaped = str(p).replace("'", "''")
            conn.execute(f"ATTACH DATABASE '{escaped}' AS {alias} KEY \"x'{hex_key}'\";")
            attached.append(alias)
        attach("old", old_path)
        if which == "raw":
            attach("om", old_master)
            attach("nm", new_master)
            schemas = {t.name: ("om", "nm") for t in sync_schema.tables_for(MASTER_DB)}
            old_opener = lambda: _open(old_master, hex_key, read_only=True)      # noqa: E731
            new_opener = lambda: _open(new_master, hex_key, read_only=True)      # noqa: E731
        else:
            schemas = {t.name: ("old", "main") for t in sync_schema.tables_for(MASTER_DB)}
            old_opener = new_opener = None
        gids_old = sync_capture._GidLookup(old_conn, db_name, old_opener)
        gids_new = sync_capture._GidLookup(conn, db_name, new_opener)

        conn.execute("BEGIN IMMEDIATE")
        try:
            _replace_sync_tables(conn)
            conn.execute("UPDATE main._sync_flags SET value = 1 WHERE name = 'applying'")
            conn.execute("DELETE FROM main._sync_pending")
            # rawPayload.db re-pointing jobs left by a merge name today's local ids, not the
            # backup's (whose two files are consistent with each other already).
            conn.execute("DELETE FROM main._sync_meta WHERE key = 'raw_repoints'")
            if sync_capture._meta(conn, "mode") != "live":
                raise RestoreError("this PC isn't in sync mode live")
            new_tables, old_tables = _tables(conn, "main"), _tables(conn, "old")
            specs = [s for s in sync_schema.replicated_tables_for(db_name) if s.name in new_tables]

            for spec in specs:
                if spec.row_key == ("gid",):
                    _regenerate_gids(conn, spec)

            upserts: list = []          # (tbl, rowid)
            for spec in specs:
                if spec.mode == APPEND:
                    rowids = _merge_append_table(conn, spec, schemas) if spec.name in old_tables else \
                        [r[0] for r in conn.execute(f'SELECT rowid FROM main."{spec.name}"')]
                    upserts += [(spec.name, r) for r in rowids]
                    continue
                conn.execute("DELETE FROM main._sync_clock WHERE tbl = ?", (spec.name,))
                upserts += [(spec.name, r[0]) for r in conn.execute(f'SELECT rowid FROM main."{spec.name}"')]

            # Row keys that exist today but not in the backup: deletes. A child whose parent row
            # goes is covered by the parent's tombstone (as the capture triggers do it).
            gone_keys: dict = {}
            gone_gids: dict = {}
            for spec in specs:
                if spec.mode == APPEND or spec.name not in old_tables:
                    continue
                old_keys = _key_set(old_conn, spec, gids_old)
                new_keys = _key_set(conn, spec, gids_new)
                gone_keys[spec.name] = {kt: k for kt, k in old_keys.items() if kt not in new_keys}
                if spec.row_key == ("gid",):
                    gone_gids[spec.name] = {k[0] for k in gone_keys[spec.name].values()}
            deletes = []
            for spec in specs:
                for kt, key in gone_keys.get(spec.name, {}).items():
                    parents = [spec.fk.get(c) for c in spec.row_key]
                    if any(tgt and part in gone_gids.get(tgt, ()) for tgt, part in zip(parents, key)):
                        continue
                    deletes.append((spec.name, kt))

            if audit is not None and which == "master":
                cur = conn.execute(
                    "INSERT INTO main.audit_log (ts, actor, action, client_id, service_id, detail, gid) "
                    "VALUES (?, ?, ?, NULL, NULL, ?, ?)",
                    (datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat(), audit["actor"], AUDIT_ACTION, audit["detail"],
                     uuid.uuid4().hex))
                upserts.append(("audit_log", cur.lastrowid))

            conn.executemany("INSERT INTO main._sync_pending(tbl, rid, op, key_json) VALUES (?, ?, 'upsert', NULL)",
                             upserts)
            conn.executemany("INSERT INTO main._sync_pending(tbl, rid, op, key_json) VALUES (?, 0, 'delete', ?)",
                             deletes)
            conn.execute("UPDATE main._sync_flags SET value = 0 WHERE name = 'applying'")

            stream = sync_capture._meta(conn, "stream_id")
            seq_state = sync_capture.seq_state_path(str(app / MASTER_DB_NAME))
            floor = sync_capture.read_seq_mark(seq_state, stream) if stream else 0
            seal_gids = sync_capture._GidLookup(conn, db_name, new_opener)
            signer_cache: dict = {}
            last = 0
            try:
                while conn.execute("SELECT 1 FROM main._sync_pending LIMIT 1").fetchone() is not None:
                    r = sync_capture._seal_in_transaction(
                        conn, db_name, seal_gids, lambda: signer, now_ms, sync_capture.SEAL_BATCH_ROWS,
                        signer_cache, max(floor, last), sign_all=True)
                    last = max(last, r.last_seq)
                    seal_gids.reset()
                    if r.pending == 0:
                        break
            finally:
                seal_gids.close()
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        return last
    finally:
        for g in (gids_old, gids_new):
            if g is not None:
                g.close()
        for alias in attached:
            try:
                conn.execute(f"DETACH DATABASE {alias}")
            except Exception:
                pass
        conn.close()
        old_conn.close()


# ---------------------------------------------------------------- install

def _fail(app: Path, reason: str) -> None:
    stage = _stage_dir(app)
    try:
        pending = stage / PENDING_FILE
        if pending.exists():
            os.replace(pending, app / "incoming" / f"{RESTORE_DIRNAME}.{PENDING_FILE}.failed")
    except OSError:
        pass
    shutil.rmtree(stage, ignore_errors=True)
    _log.error("office restore not done, databases unchanged: %s", reason)


def _raise_marks(app: Path, hex_key: str) -> None:
    """The new live DBs' own streams went up by the restore set: raise keys/sync_seq.json."""
    import sync_capture
    seq_state = sync_capture.seq_state_path(str(app / MASTER_DB_NAME))
    for name in (MASTER_DB_NAME, RAW_DB_NAME):
        conn = _open(app / name, hex_key, read_only=True)
        try:
            stream = sync_capture._meta(conn, "stream_id")
            nxt = sync_capture._meta(conn, "next_seq")
            if stream and sync_capture._meta(conn, "next_seq_stream") == stream and nxt:
                sync_capture.record_seq_mark(seq_state, stream, int(nxt) - 1)
        finally:
            conn.close()


def _finish(app: Path, hex_key: Optional[str], pre_dir: Path, info: dict) -> dict:
    import sync_shadow
    try:
        _raise_marks(app, hex_key or sync_shadow._office_hex(app))
    except Exception as exc:
        # The DB's own next_seq is right; the mark is only a floor against reuse.
        _log.warning("could not raise the sequence marks after the restore: %s", exc)
    shutil.rmtree(_stage_dir(app), ignore_errors=True)
    _log.warning("office restore installed; the replaced databases are in %s", pre_dir)
    return dict(info, replaced_dir=str(pre_dir))


def apply_pending_restore(app_dir, hex_key: Optional[str] = None, *, now_ms: Optional[int] = None,
                          _undo_on_error: bool = True) -> Optional[dict]:
    """At start-up, **before any database is opened** (``main.py``). None when nothing is staged;
    else does the restore (module docstring, step 3) and returns ``{"backup_name", "backup_date",
    "clients_back", "clients_removed", "fields_revert", "replaced_dir"}``. Raises
    ``RestoreError`` when it can't (live DBs unchanged; not retried). A key that can't be loaded
    propagates without touching anything, so it is retried at the next start."""
    import sync_admin
    import sync_shadow
    import sync_snapshot
    app = Path(app_dir)
    stage = _stage_dir(app)
    journal = sync_shadow._read_json(stage / JOURNAL_FILE)
    if journal is not None:
        if sync_compaction.recover_swap(journal):
            return _finish(app, hex_key, Path(journal["pre_dir"]), journal.get("info") or {})
        try:
            (stage / JOURNAL_FILE).unlink()
        except OSError:
            pass
        _fail(app, "the previous start-up was interrupted while moving files; put back")
        raise RestoreError("the restore was interrupted and has been put back; the data is unchanged")
    pending_path = stage / PENDING_FILE
    if not pending_path.exists():
        return None
    pending = sync_shadow._read_json(pending_path)
    if pending is None or not isinstance(pending.get("files"), list) or not isinstance(pending.get("plan"), dict):
        _fail(app, "pending.json is unreadable")
        raise RestoreError("the staged restore is unreadable")
    hex_key = hex_key or sync_shadow._office_hex(app)
    plan = pending["plan"]
    new_dir, prep = stage / "new", stage / "prep"
    try:
        names = []
        for f in pending["files"]:
            name = f.get("name") if isinstance(f, dict) else None
            if name not in sync_snapshot.ALLOWED_DB_NAMES or name in names:
                raise RestoreError("the staged file list is invalid")
            path = new_dir / name
            if not path.is_file() or sync_shadow._sha256(path) != f.get("sha256"):
                raise RestoreError(f"the staged {name} changed or is missing")
            sync_snapshot._verify_downloaded_db(path, hex_key)
            names.append(name)
        if set(names) != set(sync_snapshot.ALLOWED_DB_NAMES):
            raise RestoreError("the staged restore needs both master.db and rawPayload.db")
        try:
            signer = sync_admin.load_admin_key(app)
        except Exception:
            signer = None
        if signer is None:
            raise RestoreError("the office admin key isn't available on this PC")
        if not sync_shadow._wait_for_process_exit(pending.get("pid"), sync_shadow.GOLIVE_OLD_PROCESS_WAIT_SECONDS):
            _log.warning("restore: the previous Sera process was still running; trying anyway")
        sync_compaction.seal_live(app, hex_key)

        shutil.rmtree(prep, ignore_errors=True)
        prep.mkdir(parents=True)
        new_master, new_raw = prep / MASTER_DB_NAME, prep / RAW_DB_NAME
        shutil.copy2(new_dir / MASTER_DB_NAME, new_master)
        shutil.copy2(new_dir / RAW_DB_NAME, new_raw)
        old_master, old_raw = app / MASTER_DB_NAME, app / RAW_DB_NAME
        back, gone, fields = count_changes(old_master, new_master, hex_key)
        info = {"backup_name": plan.get("backup_name"), "backup_date": plan.get("backup_date"),
                "clients_back": back, "clients_removed": gone, "fields_revert": fields}
        detail = (f"Office restored from backup {info['backup_name']} ({info['backup_date']}): "
                  f"{back} client(s) back, {gone} removed, {fields} field(s) reverted")
        _rebuild(new_master, old_master, hex_key, "master", signer=signer, app=app,
                 audit={"actor": str(pending.get("actor") or "System"), "detail": detail}, now_ms=now_ms)
        _rebuild(new_raw, old_raw, hex_key, "raw", signer=signer, app=app,
                 new_master=new_master, old_master=old_master, now_ms=now_ms)
        for p in (new_master, new_raw):
            sync_compaction.checkpoint(p, hex_key)
    except BaseException as exc:
        shutil.rmtree(prep, ignore_errors=True)
        if not isinstance(exc, Exception):
            raise
        _fail(app, str(exc) if isinstance(exc, RestoreError) else type(exc).__name__)
        if isinstance(exc, RestoreError):
            raise
        raise RestoreError(f"the restore could not be prepared: {exc}") from exc

    pre_dir = sync_compaction.unique_dir(app / "backups", REPLACED_PREFIX)
    try:
        sync_compaction.journaled_swap(app, stage / JOURNAL_FILE, pre_dir,
                                       {MASTER_DB_NAME: new_master, RAW_DB_NAME: new_raw},
                                       undo_on_error=_undo_on_error, extra={"info": info})
    except BaseException as exc:
        if not _undo_on_error:
            raise
        _fail(app, f"files could not be moved ({type(exc).__name__}); put back")
        if not isinstance(exc, Exception):
            raise
        raise RestoreError(f"the restore could not be installed and was put back: {exc}") from exc
    return _finish(app, hex_key, pre_dir, info)
