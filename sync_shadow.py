"""
sync_shadow.py
---------------
Shadow mode for Sera Sync v3 (blueprint §5, WP P3-7).

Mode ``shadow``: capture and sealing are on (P3-3), sessions exchange changes (P3-5), but
remote changes go to a **replica** (``shadow/replica_master.db`` / ``shadow/replica_raw.db``),
never to the live DBs. Local changes are mirrored into the replica too, so the replica always
holds "what live mode would look like" without ever writing a remote change to the live DBs.

Two hooks feed the replica:
  - ``make_shadow_apply(db, app_dir)`` returns the ``shadow_apply(which, items)`` callable
    ``sync_engine.SyncEngine`` calls for remote changes in mode shadow.
  - ``mirror_own_changes_to_replica(db, app_dir)`` applies this device's own newly sealed
    changes to the replica too. Call it from ``db.set_seal_listener`` (after ``notify_local_change``,
    or before -- order doesn't matter, they touch different files).

Both apply with ``emit=False`` (sync_apply.apply_batch, P3-4): the replica shares the live DB's
stream id, so it must never add a merge decision to its own stream (that would collide with the
live DB's sequence numbers when shadow mode eventually goes live, P3-9).

``enable_shadow_mode(db, app_dir)`` takes the one-time snapshot: a **baseline** (frozen, used by
``capture_check``) and a **replica** (the mutable copy remote/local changes are mirrored into),
both copies of the live DBs at that moment, then sets ``_sync_meta.mode = 'shadow'``. It refuses
if shadow mode is already on, if old shadow files exist, or if the live DBs already count another
PC's changes as received (P3-7b guard) -- see its docstring; a second snapshot from the live DBs
would silently and permanently lose remote data the old replica holds but the live DBs never did.

Turning shadow mode on (WP P3-7b, owner decision 2026-09-26: start point "1a"):
  - Admin PC: ``start_shadow_mode(db, app_dir)`` (checks, then ``enable_shadow_mode``).
  - Every other PC downloads the admin PC's **replica** (not its live DB: in mode shadow the
    admin's live ``_sync_vector`` counts remote changes the live data doesn't hold) and uses it
    as its live DB, replica and baseline, so all PCs start from one state:
      1. ``request_shadow_start(db, app_dir, session)`` -- ``{"t": "shadow_snapshot"}`` over
         mutual TLS (routed by ``sync_office.dispatch_session`` to
         ``handle_shadow_snapshot_session`` on the admin PC), verified download into
         ``incoming/shadow_start/new/``, plus a salvage dry run against this PC's live DBs: what
         only this PC has (the dialog's count). Nothing live is touched.
      2. ``stage_shadow_start(app_dir, plan)`` (or ``cancel_shadow_start``), then restart.
      3. ``apply_pending_shadow_start(app_dir)`` at start-up, before any DB is opened: the old
         live DBs move to ``shadow/pre-start-<ts>/`` (§0 rule 3), the download becomes the live
         DB (this PC's ``_local_*`` tables and newer member records carried over, mode shadow)
         and the replica and baseline (mode off).
      4. The P2-8 salvage then offers to import what only this PC had
         (``plan_shadow_salvage`` / ``apply_shadow_salvage`` / ``skip_shadow_salvage``). The
         import is captured like any edit, so it replicates.
  - ``reset_shadow_mode(db, app_dir)`` (admin-only in the UI): renames the replica, baseline
    and ``started.json`` to ``*.bak-<ts>`` and sets mode off, which also resets the week.
  - ``shadow_status(db, app_dir)``: mode, start time and day of the shadow week for the panel.

Checks (blueprint §5 P3-7), logged to ``logs/sync_shadow.log``:
  - ``capture_check(db, app_dir)``: replays the baseline snapshot plus this device's own-origin
    changes (``_own_captured_changes``) into a scratch copy, and compares its digest to the live
    DB's. A write that bypassed the capture triggers (e.g. dropped by a schema rebuild) shows up
    in the live DB but never made it into ``_sync_changes``, so the replay is missing it and the
    digests differ. Deliberately excludes forwarded copies of remote changes recorded in
    ``_sync_changes`` for session forwarding (``_record_receipt_in_live``): those never reach the
    live DB's replicated tables in mode shadow, so replaying them would make every PC that has
    ever received anything fail the check (P3-7 review, 2026-09-25).
  - ``convergence_check(own_device_id, own_digest, peer_digests)``: compares this PC's replica
    digest to already-collected peer digests. There is no wire frame yet to ask a peer for its
    digest (not part of the P3-5 session protocol); collecting ``peer_digests`` is left to the
    caller -- the test harness today, and P3-8's panel/checks timer once such a frame exists.
    Documented deviation (§8.3): a real cross-PC convergence check needs that frame, out of
    scope here.

``digest_from_conns`` / ``digest_of_files`` are a deliberately separate implementation from
``tests/sync_harness.py``'s ``digest()`` (a P3-0 file; changing it needs the owner's OK per §0
rule 4, so it was left as is). Both hash the same way (blueprint §5 P3-7: SHA-256 over each
replicated table sorted by row key, gids for FK values, local ids excluded, every
``app_settings`` key included, D7), driven by the same ``sync_schema`` registry, so the two
should never disagree; if they ever do, trust this one for production checks.

No PySide6 (§0 rule 7). New libraries imported lazily (§0 rule 6). Never logs change contents
or client data -- only counts, table names and digests (§0 rule 12).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

_log = logging.getLogger("sera.sync.shadow")

SHADOW_DIRNAME = "shadow"
REPLICA_MASTER_NAME = "replica_master.db"
REPLICA_RAW_NAME = "replica_raw.db"
BASELINE_MASTER_NAME = "baseline_master.db"
BASELINE_RAW_NAME = "baseline_raw.db"
SHADOW_LOG_NAME = "sync_shadow.log"
STARTED_FILE = "started.json"               # shadow/started.json: when and how this PC started
SALVAGE_PENDING_FILE = "salvage_pending.json"
PRE_START_PREFIX = "pre-start-"             # shadow/pre-start-<ts>/: live DBs before a P3-7b swap
START_DIRNAME = "shadow_start"              # incoming/shadow_start/: download + pending start
PENDING_START_FILE = "pending.json"
INSTALL_JOURNAL_FILE = "installing.json"   # incoming/shadow_start/: moves of an install in progress
FRAME_SHADOW_SNAPSHOT = "shadow_snapshot"   # request for the admin PC's replica (P3-7b)
SHADOW_WEEK_DAYS = 7
_SIDECARS = ("-wal", "-shm", "-journal")

_CHANGE_COLS = ("origin", "origin_seq", "hlc", "tbl", "row_key", "op", "data", "sig")

# Serialises writes to the replica files (remote apply, own-change mirroring, reset) with the
# P3-7b replica export, so an exported master/raw pair is one consistent state.
_REPLICA_LOCK = threading.RLock()


class ShadowStartRefused(RuntimeError):
    """Turning shadow mode on (or resetting it) isn't allowed in this PC's current state."""


class ShadowStartError(RuntimeError):
    """Turning shadow mode on failed (admin PC unreachable or refused, bad download, install)."""


# ---------------------------------------------------------------- paths / logging

def replica_paths(app_dir) -> tuple[Path, Path]:
    d = Path(app_dir) / SHADOW_DIRNAME
    return d / REPLICA_MASTER_NAME, d / REPLICA_RAW_NAME


def baseline_paths(app_dir) -> tuple[Path, Path]:
    d = Path(app_dir) / SHADOW_DIRNAME
    return d / BASELINE_MASTER_NAME, d / BASELINE_RAW_NAME


def _log_line(app_dir, message: str) -> None:
    try:
        logs = Path(app_dir) / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with open(logs / SHADOW_LOG_NAME, "a", encoding="utf-8") as f:
            f.write(f"{ts} {message}\n")
    except Exception as exc:
        _log.warning("could not write logs/%s: %s", SHADOW_LOG_NAME, exc)


def _log_check(app_dir, kind: str, ok: bool, detail: str) -> None:
    status = "OK" if ok else "MISMATCH"
    _log_line(app_dir, f"{kind}_check {status}" + (f": {detail}" if detail else ""))


# ---------------------------------------------------------------- enable shadow mode

def _backup_existing(path: Path) -> None:
    """§0 rule 3: never delete a database; an existing file at *path* is renamed aside."""
    if path.exists():
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup = path.with_name(f"{path.name}.bak-{ts}")
        n = 1
        while backup.exists():
            backup = path.with_name(f"{path.name}.bak-{ts}_{n}")
            n += 1
        path.rename(backup)


def _old_shadow_files(app_dir) -> list[Path]:
    return [p for p in (*replica_paths(app_dir), *baseline_paths(app_dir)) if p.exists()]


def _refuse_if_old_shadow_files(app_dir) -> None:
    old = _old_shadow_files(app_dir)
    if old:
        raise ShadowStartRefused(
            f"this PC already has shadow-mode files from an earlier start ({old[0].name}); "
            "an admin must use \"Reset shadow mode\" first")


def _remote_origins(db) -> list[str]:
    """Origins in the live DBs' ``_sync_vector`` (``max_seq > 0``) that aren't this PC's own
    streams, i.e. changes from other PCs these live DBs count as received."""
    import sync_capture
    own = _device_id(db)
    found = set()
    for path in (db.db_path, db.raw_db_path):
        if not path or not Path(path).exists():
            continue
        conn = sync_capture._open(path, db.hex_key, 5.0)
        try:
            rows = conn.execute("SELECT origin FROM _sync_vector WHERE max_seq > 0").fetchall()
        finally:
            conn.close()
        found.update(o for (o,) in rows if not own or o.rsplit(":", 1)[0] != own)
    return sorted(found)


def enable_shadow_mode(db, app_dir=None) -> None:
    """Snapshots the live DBs into ``shadow/`` as the frozen baseline and the starting replica,
    then turns ``_sync_meta.mode`` to ``'shadow'`` in both live DBs (blueprint §5 P3-7).

    The snapshot is taken *before* the mode switch, while nothing has been captured yet, so the
    baseline's own ``_sync_changes`` is empty -- everything captured afterwards is, by
    construction, "since the baseline" (used by ``capture_check``).

    All PCs must run this from the same office snapshot (blueprint §5 P3-7); that ordering is
    an operational (owner) concern, not something this function can check on its own.

    Refuses if shadow mode is already on. A second run would build the new replica from the
    *live* DBs, which never hold remote-origin data (mode shadow routes it only to the old
    replica) -- but the live DB's ``_sync_vector`` already marks those remote changes received
    (``_record_receipt_in_live``, ``make_shadow_apply``), copied into the new replica along with
    everything else. No peer would ever resend them, so that data would be gone for good and the
    new replica would silently and permanently disagree with every other PC (P3-7 review,
    2026-09-25).

    P3-7b guard (§7): refusing only while the mode is ``shadow`` wasn't enough -- mode ``off``
    and then on again did the same thing. It also refuses (``ShadowStartRefused``) when old
    shadow files exist (``reset_shadow_mode`` renames them first) and when the live
    ``_sync_vector`` has any non-own origin: a replica built from these live DBs would count
    those changes as received without holding them. Checked before anything is touched.
    """
    import sync_snapshot
    if db.get_sync_mode() == "shadow":
        raise ShadowStartRefused(
            "shadow mode is already on; enabling it again would silently and permanently lose "
            "any remote changes only the old replica holds (see enable_shadow_mode's docstring)")
    app_dir = Path(app_dir or os.path.dirname(os.path.abspath(db.db_path)))
    _refuse_if_old_shadow_files(app_dir)
    remote = _remote_origins(db)
    if remote:
        raise ShadowStartRefused(
            f"this PC's live database already counts changes from {len(remote)} stream(s) of other "
            "PCs as received, so a replica built from it would never get them")
    shadow_dir = app_dir / SHADOW_DIRNAME
    shadow_dir.mkdir(parents=True, exist_ok=True)

    replica_master, replica_raw = replica_paths(app_dir)
    baseline_master, baseline_raw = baseline_paths(app_dir)

    tmp_dir = shadow_dir / "_enable_tmp"
    shutil.rmtree(tmp_dir, ignore_errors=True)
    try:
        sync_snapshot.make_office_snapshot(app_dir, tmp_dir)
        src_master = tmp_dir / "master.db"
        src_raw = tmp_dir / "rawPayload.db"
        if not src_master.exists():
            raise RuntimeError("shadow snapshot export produced no master.db")
        shutil.copy2(src_master, baseline_master)
        shutil.move(str(src_master), str(replica_master))
        if src_raw.exists():
            shutil.copy2(src_raw, baseline_raw)
            shutil.move(str(src_raw), str(replica_raw))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    db.set_sync_mode("shadow")
    _log_line(app_dir, "shadow mode enabled; baseline + replica snapshot taken")


# ---------------------------------------------------------------- own changes -> replica

def _device_id(db) -> str:
    import sync_capture
    conn = sync_capture._open(db.db_path, db.hex_key, 5.0)
    try:
        return sync_capture._meta(conn, "device_id") or ""
    finally:
        conn.close()


def _origin_for(device_id: str, which: str) -> str:
    return f"{device_id}:{'m' if which == 'master' else 'r'}"


def _replica_vector(replica_path: Path, hex_key: str, origin: str, timeout: float = 5.0) -> int:
    import sync_capture
    if not replica_path.exists():
        return 0
    conn = sync_capture._open(str(replica_path), hex_key, timeout)
    try:
        row = conn.execute("SELECT max_seq FROM _sync_vector WHERE origin = ?", (origin,)).fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def _changes_since(db_path: str, hex_key: str, origin: str, since_seq: int,
                    timeout: float = 5.0) -> list[dict]:
    import sync_capture
    conn = sync_capture._open(db_path, hex_key, timeout)
    try:
        rows = conn.execute(
            "SELECT origin, origin_seq, hlc, tbl, row_key, op, data, sig FROM _sync_changes "
            "WHERE origin = ? AND origin_seq > ? ORDER BY origin_seq",
            (origin, since_seq)).fetchall()
        return [dict(zip(_CHANGE_COLS, r)) for r in rows]
    finally:
        conn.close()


def _own_captured_changes(db_path: str, hex_key: str, origin: str, timeout: float = 5.0) -> list[dict]:
    """This device's own-origin changes from *db_path*'s ``_sync_changes``, oldest first -- used
    by ``capture_check`` to replay onto the baseline.

    Deliberately excludes forwarded copies of remote changes: in mode shadow (the only mode
    ``capture_check`` runs in) a remote change never touches the live DB's replicated tables --
    it goes to the replica only (``make_shadow_apply``) -- even though it *is* recorded in the
    live DB's own ``_sync_changes`` for forwarding (``_record_receipt_in_live``). Replaying those
    too would make the reconstruction hold data the live DB never had, and the check would fail
    on every PC that has ever received anything (P3-7 review, 2026-09-25)."""
    return _changes_since(db_path, hex_key, origin, 0, timeout)


def mirror_own_changes_to_replica(db, app_dir=None) -> None:
    """Applies this device's own newly sealed changes to the shadow replica too (blueprint §5
    P3-7: "Local changes are applied to the replica too"). Call from ``db.set_seal_listener``
    after a seal that produced changes. A no-op outside mode shadow, or before ``enable_shadow_mode``
    has made a replica. Never raises -- a mirroring failure must not break the caller's commit
    (matches ``database.py._seal_after_commit``'s own rule); it is logged instead."""
    if db.get_sync_mode() != "shadow":
        return
    app_dir = Path(app_dir or os.path.dirname(os.path.abspath(db.db_path)))
    replica_master, replica_raw = replica_paths(app_dir)
    if not replica_master.exists():
        return

    import sync_apply
    import sync_capture
    device_id = _device_id(db)
    if not device_id:
        return
    admin_pubkey = db._office_admin_pubkey()

    plan = [("master", db.db_path, replica_master, None)]
    if Path(db.raw_db_path).exists() and replica_raw.exists():
        plan.append(("raw", db.raw_db_path, replica_raw, replica_master))

    for which, live_path, replica_path, replica_master_path in plan:
        origin = _origin_for(device_id, which)
        try:
            with _REPLICA_LOCK:
                if not replica_path.exists():      # reset_shadow_mode ran meanwhile
                    return
                since = _replica_vector(replica_path, db.hex_key, origin)
                changes = _changes_since(live_path, db.hex_key, origin, since)
                if not changes:
                    continue
                kw = dict(admin_pubkey=admin_pubkey, get_signer=db._admin_signer,
                          seq_state=sync_capture.seq_state_path(str(replica_path)), emit=False)
                if replica_master_path is not None:
                    kw["master_path"] = str(replica_master_path)
                result = sync_apply.apply_batch(str(replica_path), db.hex_key, which, changes, **kw)
            _log_line(app_dir, f"mirrored {len(changes)} own {which} change(s) to the replica "
                                f"({result.applied} applied, {result.parked} parked)")
        except Exception as exc:
            _log.warning("could not mirror own %s changes to the shadow replica: %s", which, exc)
            _log_line(app_dir, f"replica mirror FAILED for {which}: {exc}")


# ---------------------------------------------------------------- remote changes -> replica

def _record_receipt_in_live(db_path: str, hex_key: str, items: list, timeout: float = 5.0) -> None:
    """Records that ``items`` were received, in the *live* DB's own bookkeeping (``_sync_changes``
    -- so a live-mode PC further down the chain still gets them forwarded, and ``_sync_vector``
    -- so ``SyncEngine.own_vectors()`` advances past them), without ever touching the live DB's
    replicated table data: shadow mode's remote changes go only to the replica (``make_shadow_apply``).

    Without this, a PC in shadow mode never advances past a remote change (its HELLO vector
    stays put even though it applied the change to its replica), so the sender resends it every
    round forever."""
    import json as _json
    import sync_capture
    conn = sync_capture._open(db_path, hex_key, timeout)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            origins = set()
            for it in items:
                origin, seq = it["origin"], it["origin_seq"]
                origins.add(origin)
                if conn.execute("SELECT 1 FROM _sync_changes WHERE origin = ? AND origin_seq = ?",
                                 (origin, seq)).fetchone():
                    continue
                data = it.get("data")
                if isinstance(data, dict):
                    data = _json.dumps(data, ensure_ascii=False)
                conn.execute(
                    "INSERT INTO _sync_changes(origin, origin_seq, hlc, tbl, row_key, op, data, sig) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (origin, seq, it["hlc"], it["tbl"], it["row_key"], it["op"], data, it.get("sig")))
            # Advance the vector to the highest *contiguous* seq per origin (sync_apply._Applier's
            # own rule, P3-4), so a gap still stalls forwarding the same way it would live.
            for origin in origins:
                cur = conn.execute("SELECT max_seq FROM _sync_vector WHERE origin = ?", (origin,)).fetchone()
                have = cur[0] if cur else 0
                have_seqs = {r[0] for r in conn.execute(
                    "SELECT origin_seq FROM _sync_changes WHERE origin = ? AND origin_seq > ?",
                    (origin, have)).fetchall()}
                nxt = have
                while (nxt + 1) in have_seqs:
                    nxt += 1
                if nxt > have:
                    conn.execute(
                        "INSERT INTO _sync_vector(origin, max_seq) VALUES (?, ?) "
                        "ON CONFLICT(origin) DO UPDATE SET max_seq = excluded.max_seq",
                        (origin, nxt))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def make_shadow_apply(db, app_dir=None):
    """Returns a ``shadow_apply(which, items)`` callable for ``sync_engine.SyncEngine`` (P3-5):
    applies remote changes to the shadow replica instead of the live DB, with ``emit=False``
    (sync_apply.apply_batch, P3-4) since the replica shares the live DB's stream id. Also
    records receipt in the live DB's own bookkeeping (``_record_receipt_in_live``) so the
    session protocol's forwarding and vector advancement keep working."""
    import sync_apply
    import sync_capture
    app_dir = Path(app_dir or os.path.dirname(os.path.abspath(db.db_path)))
    replica_master, replica_raw = replica_paths(app_dir)

    def shadow_apply(which: str, items: list):
        if which not in ("master", "raw"):
            raise ValueError(f"unknown database {which!r}")
        replica_path = replica_master if which == "master" else replica_raw
        live_path = db.db_path if which == "master" else db.raw_db_path
        admin_pubkey = db._office_admin_pubkey()
        kw = dict(admin_pubkey=admin_pubkey, get_signer=db._admin_signer,
                  seq_state=sync_capture.seq_state_path(str(replica_path)), emit=False)
        if which == "raw":
            kw["master_path"] = str(replica_master)
        with _REPLICA_LOCK:
            if not replica_path.exists():
                raise RuntimeError("shadow mode has no replica yet (enable_shadow_mode was not run)")
            result = sync_apply.apply_batch(str(replica_path), db.hex_key, which, items, **kw)
            _record_receipt_in_live(live_path, db.hex_key, items)
        return result

    return shadow_apply


# ---------------------------------------------------------------- digest (blueprint §5 P3-7)

def _encode_value(val: Any) -> str:
    """NULL -> ``<NULL>``; bytes -> lowercase hex; float -> ``repr``; everything else -> ``str()``,
    with ``\\ | , = :`` backslash-escaped so they can't collide with the row-serialisation
    delimiters."""
    if val is None:
        return "<NULL>"
    if isinstance(val, (bytes, bytearray, memoryview)):
        return bytes(val).hex()
    if isinstance(val, float):
        return repr(val)
    text = str(val)
    return (text.replace("\\", "\\\\").replace("|", "\\|")
                .replace(",", "\\,").replace("=", "\\=").replace(":", "\\:"))


def _translate_fk(col_name: str, val: Any, fk_gid_maps: dict) -> Optional[str]:
    """Translates an FK value to ``gid:<gid>`` where a mapping is available. A local id whose
    row no longer exists is still a local id (P3-7: local ids are excluded); the sealer sends
    such a reference as NULL (P3-3/P3-4), so ``<NULL>`` is what it is on every PC."""
    if col_name not in fk_gid_maps:
        return None
    mapping = fk_gid_maps[col_name]
    if isinstance(val, int):
        return f"gid:{mapping[val]}" if val in mapping else "<NULL>"
    if isinstance(val, str) and val.isdigit():
        int_val = int(val)
        return f"gid:{mapping[int_val]}" if int_val in mapping else "<NULL>"
    return None


def digest_from_conns(master_conn, raw_conn=None) -> str:
    """SHA-256 over every replicated table (``sync_schema.replicated_tables_for``), sorted by
    row key: row-key + sorted ``col=value`` pairs, FK values translated to gids, local ids
    excluded, every ``app_settings`` key included (D7). Raises ``RuntimeError`` on any read
    failure so a broken DB is never mistaken for an empty-but-converged one."""
    import sync_schema
    hasher = hashlib.sha256()

    master_specs = {s.name: s for s in sync_schema.replicated_tables_for(sync_schema.MASTER_DB)}
    raw_specs = {s.name: s for s in sync_schema.replicated_tables_for(sync_schema.RAW_DB)}

    conn_map = [(sync_schema.MASTER_DB, master_conn, master_specs)]
    if raw_conn is not None:
        conn_map.append((sync_schema.RAW_DB, raw_conn, raw_specs))

    for db_label, conn, specs in conn_map:
        try:
            live_tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        except Exception as exc:
            raise RuntimeError(f"digest: cannot read sqlite_master in {db_label}: {exc}") from exc

        for tname in sorted(specs):
            if tname not in live_tables:
                continue
            spec = specs[tname]
            try:
                col_info = conn.execute(f"PRAGMA table_info({tname})").fetchall()
            except Exception as exc:
                raise RuntimeError(f"digest: cannot read PRAGMA table_info({tname}) in {db_label}: {exc}") from exc
            if not col_info:
                continue

            col_names = [c[1] for c in col_info]
            col_set = set(col_names)
            col_idx = {c[1]: i for i, c in enumerate(col_info)}
            has_gid = "gid" in col_set
            cols_to_hash = sorted(c for c in col_names if not (c == "id" and has_gid))

            fk_gid_maps: dict[str, dict] = {}
            for fk_col, ref_table in spec.fk.items():
                ref_conn = master_conn if db_label == sync_schema.RAW_DB else conn
                if ref_conn is None:
                    continue
                try:
                    ref_live = {r[0] for r in ref_conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                except Exception as exc:
                    raise RuntimeError(f"digest: cannot read sqlite_master in ref DB for {ref_table}: {exc}") from exc
                if ref_table not in ref_live:
                    continue
                try:
                    ri = ref_conn.execute(f"PRAGMA table_info({ref_table})").fetchall()
                    rcs = {c[1] for c in ri}
                    if "gid" in rcs and "id" in rcs:
                        fk_gid_maps[fk_col] = {
                            row[0]: str(row[1])
                            for row in ref_conn.execute(
                                f"SELECT id, gid FROM {ref_table} WHERE gid IS NOT NULL").fetchall()}
                except Exception as exc:
                    raise RuntimeError(f"digest: cannot read {ref_table} for FK column {fk_col}: {exc}") from exc

            try:
                rows = conn.execute(f"SELECT * FROM {tname}").fetchall()
            except Exception as exc:
                raise RuntimeError(f"digest: cannot read rows from {db_label}.{tname}: {exc}") from exc

            if spec.row_key == ("gid",) and "gid" not in col_set:
                row_key_cols = ("id",) if "id" in col_set else (col_names[0],)
            else:
                row_key_cols = spec.row_key

            row_strings: list[str] = []
            for r in rows:
                key_parts = []
                for k in row_key_cols:
                    if k not in col_idx:
                        raise RuntimeError(f"digest: row-key column {k!r} not found in {db_label}.{tname}")
                    val = r[col_idx[k]]
                    if val is None:
                        raise RuntimeError(f"digest: row-key column {k!r} is NULL in {db_label}.{tname}")
                    fk_gid = _translate_fk(k, val, fk_gid_maps)
                    key_parts.append(fk_gid if fk_gid is not None else _encode_value(val))
                row_key_str = ":".join(key_parts)

                pairs = []
                for col in cols_to_hash:
                    if col not in col_idx:
                        continue
                    val = r[col_idx[col]]
                    fk_gid = _translate_fk(col, val, fk_gid_maps)
                    val_str = fk_gid if fk_gid is not None else _encode_value(val)
                    pairs.append(f"{col}={val_str}")

                row_strings.append(f"{row_key_str}|" + ",".join(pairs))

            row_strings.sort()
            hasher.update(f"--- {db_label}.{tname} ---\n".encode("utf-8"))
            for rs in row_strings:
                hasher.update(f"{rs}\n".encode("utf-8"))

    return hasher.hexdigest()


def digest_of_files(master_path, hex_key: str, raw_path=None, timeout: float = 5.0) -> str:
    """``digest_from_conns`` over plain DB files (a snapshot, replica or scratch copy)."""
    import sync_capture
    m_conn = sync_capture._open(str(master_path), hex_key, timeout)
    try:
        r_conn = None
        if raw_path is not None and Path(raw_path).exists():
            r_conn = sync_capture._open(str(raw_path), hex_key, timeout)
        try:
            return digest_from_conns(m_conn, r_conn)
        finally:
            if r_conn is not None:
                r_conn.close()
    finally:
        m_conn.close()


def digest_of_live(db) -> str:
    """The live DBs' digest, via ``SeraDatabase``'s own connections."""
    with db._connect() as m_conn:
        with db._connect_raw() as r_conn:
            return digest_from_conns(m_conn, r_conn)


def digest_of_replica(app_dir, hex_key: str, timeout: float = 5.0) -> str:
    replica_master, replica_raw = replica_paths(app_dir)
    if not replica_master.exists():
        raise RuntimeError("no shadow replica yet (enable_shadow_mode was not run)")
    return digest_of_files(replica_master, hex_key, replica_raw, timeout)


def replica_snapshot(app_dir, hex_key: str, timeout: float = 5.0) -> tuple[dict, str]:
    """``({origin: max_seq}, digest)`` of this PC's shadow replica, read consistently: both
    files are opened in one read transaction each, so the vectors and the row data the digest
    hashes describe the same instant (blueprint §5 P3-7a: "each side reads its replica's
    _sync_vector and computes digest_of_replica in one read transaction on the replica files").
    The vectors are merged across both replica files, same as ``sync_engine.own_vectors()``.
    Raises ``RuntimeError`` if there is no replica yet."""
    import sync_capture
    replica_master, replica_raw = replica_paths(app_dir)
    if not replica_master.exists():
        raise RuntimeError("no shadow replica yet (enable_shadow_mode was not run)")
    m_conn = sync_capture._open(str(replica_master), hex_key, timeout)
    try:
        m_conn.execute("BEGIN")
        r_conn = sync_capture._open(str(replica_raw), hex_key, timeout) if replica_raw.exists() else None
        try:
            if r_conn is not None:
                r_conn.execute("BEGIN")
            vectors = {o: int(s) for o, s in m_conn.execute("SELECT origin, max_seq FROM _sync_vector")}
            if r_conn is not None:
                vectors.update({o: int(s) for o, s in r_conn.execute("SELECT origin, max_seq FROM _sync_vector")})
            digest = digest_from_conns(m_conn, r_conn)
            return vectors, digest
        finally:
            m_conn.execute("ROLLBACK")
            if r_conn is not None:
                r_conn.execute("ROLLBACK")
                r_conn.close()
    finally:
        m_conn.close()


# ---------------------------------------------------------------- checks (blueprint §5 P3-7)

@dataclass
class CaptureCheckResult:
    ok: bool
    live_digest: str = ""
    replay_digest: str = ""
    detail: str = ""


def capture_check(db, app_dir=None, timeout: float = 5.0) -> CaptureCheckResult:
    """Verifies this PC's capture triggers missed nothing: replays the frozen baseline plus this
    device's own captured changes into a scratch copy, and compares its digest to the live DB's
    (blueprint §5 P3-7: "baseline snapshot + this PC's own changes replayed"). Only this
    device's own-origin changes are replayed -- see ``_own_captured_changes`` for why: in mode
    shadow (the only mode this runs in) the live DB never holds remote-origin data. Logged to
    ``logs/sync_shadow.log``."""
    import sync_apply
    app_dir = Path(app_dir or os.path.dirname(os.path.abspath(db.db_path)))
    baseline_master, baseline_raw = baseline_paths(app_dir)
    if not baseline_master.exists():
        result = CaptureCheckResult(False, detail="no shadow baseline yet (shadow mode not enabled)")
        _log_check(app_dir, "capture", result.ok, result.detail)
        return result

    device_id = _device_id(db)
    tmp = tempfile.mkdtemp(prefix="sera_capture_check_")
    try:
        scratch_master = Path(tmp) / "scratch_master.db"
        scratch_raw = Path(tmp) / "scratch_raw.db"
        shutil.copy2(baseline_master, scratch_master)
        has_raw = baseline_raw.exists()
        if has_raw:
            shutil.copy2(baseline_raw, scratch_raw)

        admin_pubkey = db._office_admin_pubkey()
        try:
            master_changes = _own_captured_changes(
                db.db_path, db.hex_key, _origin_for(device_id, "master"), timeout)
            if master_changes:
                sync_apply.apply_batch(str(scratch_master), db.hex_key, "master", master_changes,
                                        admin_pubkey=admin_pubkey, emit=False, timeout=timeout)
            if has_raw and Path(db.raw_db_path).exists():
                raw_changes = _own_captured_changes(
                    db.raw_db_path, db.hex_key, _origin_for(device_id, "raw"), timeout)
                if raw_changes:
                    sync_apply.apply_batch(str(scratch_raw), db.hex_key, "raw", raw_changes,
                                            master_path=str(scratch_master), admin_pubkey=admin_pubkey,
                                            emit=False, timeout=timeout)
            replay_digest = digest_of_files(scratch_master, db.hex_key,
                                             scratch_raw if has_raw else None, timeout)
            live_digest = digest_of_live(db)
        except Exception as exc:
            result = CaptureCheckResult(False, detail=f"capture check could not run: {exc}")
            _log_check(app_dir, "capture", result.ok, result.detail)
            return result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    ok = (replay_digest == live_digest)
    result = CaptureCheckResult(
        ok, live_digest, replay_digest,
        "" if ok else "replay of this PC's captured changes does not match its live database "
                       "(a write bypassed the capture triggers)")
    _log_check(app_dir, "capture", ok, result.detail)
    return result


@dataclass
class ConvergenceCheckResult:
    ok: bool
    digests: dict = field(default_factory=dict)
    detail: str = ""


def convergence_check(own_device_id: str, own_digest: str, peer_digests: dict) -> ConvergenceCheckResult:
    """Compares this PC's replica digest to already-collected peer digests (blueprint §5 P3-7).
    Collecting ``peer_digests`` is the caller's job -- see the module docstring."""
    digests = {own_device_id: own_digest, **peer_digests}
    unique = set(digests.values())
    ok = len(unique) <= 1
    detail = "" if ok else f"{len(unique)} distinct replica digests among {len(digests)} PC(s)"
    return ConvergenceCheckResult(ok, digests, detail)


def log_peer_digest_check(app_dir, peer_name: str, status: str, detail: str = "") -> None:
    """Logs a P3-7a peer digest-exchange result to ``logs/sync_shadow.log``. ``status`` is
    ``"OK"``, ``"MISMATCH"`` or ``"skipped"``. Never logs row contents (§0 rule 12) -- the
    caller passes only counts/seconds/reasons in ``detail``."""
    _log_line(app_dir, f"peer_digest_check {status} (peer {peer_name})" + (f": {detail}" if detail else ""))


def run_shadow_checks(db, app_dir=None, own_device_id: Optional[str] = None,
                       peer_digests: Optional[dict] = None) -> dict:
    """Runs the P3-7 checks and logs them. Called on demand (panel) and from a periodic timer
    once P3-8 wires one in. ``peer_digests`` (``{device_id: digest}``), when given, also runs
    the convergence check. Logs parked count and oldest age with every check (P3-8a)."""
    app_dir = Path(app_dir or os.path.dirname(os.path.abspath(db.db_path)))
    out: dict = {"capture": capture_check(db, app_dir)}
    if peer_digests is not None:
        replica_master, _ = replica_paths(app_dir)
        if replica_master.exists():
            own_device_id = own_device_id or _device_id(db)
            own_digest = digest_of_replica(app_dir, db.hex_key)
            convergence = convergence_check(own_device_id, own_digest, peer_digests)
            out["convergence"] = convergence
            _log_check(app_dir, "convergence", convergence.ok, convergence.detail)

    # P3-8a: Log parked count and oldest age with every shadow check
    try:
        import sync_capture
        import sync_panel
        conns = []
        replica_master, replica_raw = replica_paths(app_dir)
        if db.get_sync_mode() == "shadow" and replica_master.exists():
            conns.append(sync_capture._open(str(replica_master), db.hex_key, 5.0))
            if replica_raw.exists():
                conns.append(sync_capture._open(str(replica_raw), db.hex_key, 5.0))
        else:
            conns.append(sync_capture._open(db.db_path, db.hex_key, 5.0))
            if Path(db.raw_db_path).exists():
                conns.append(sync_capture._open(db.raw_db_path, db.hex_key, 5.0))
        try:
            parked_cnt, oldest_age = sync_panel.parked_summary(conns)
            if parked_cnt > 0 and oldest_age is not None:
                _log_line(app_dir, f"parked_changes count={parked_cnt} oldest_age={oldest_age}s")
            else:
                _log_line(app_dir, f"parked_changes count={parked_cnt} oldest_age=none")
        finally:
            for c in conns:
                c.close()
    except Exception as exc:
        _log.warning("could not log parked changes for shadow check: %s", exc)

    return out


# ---------------------------------------------------------------- seal timing (P3-8a)

class SealTimingLogger:
    """Collects seal timing durations on the committing thread and logs at most one summary
    line per minute (count, mean and max ms) to logs/sync_shadow.log (blueprint §5, WP P3-8a).
    Thread-safe. Never logs row contents (§0 rule 12)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._count = 0
        self._total_ms = 0.0
        self._max_ms = 0.0
        self._last_logged_at: Optional[float] = None

    def record(self, duration_ms: float, app_dir=None, now_fn: Optional[Callable[[], float]] = None) -> None:
        now = now_fn() if now_fn is not None else time.time()
        with self._lock:
            if self._last_logged_at is None:
                self._last_logged_at = now
            self._count += 1
            self._total_ms += duration_ms
            if duration_ms > self._max_ms:
                self._max_ms = duration_ms
            if now - self._last_logged_at >= 60.0:
                self._flush_locked(app_dir, now)

    def flush(self, app_dir=None, now_fn: Optional[Callable[[], float]] = None) -> None:
        now = now_fn() if now_fn is not None else time.time()
        with self._lock:
            if self._count > 0:
                self._flush_locked(app_dir, now)

    def _flush_locked(self, app_dir, now: float) -> None:
        if self._count > 0:
            mean_ms = self._total_ms / self._count
            _log_line(app_dir, f"seal_timing count={self._count} mean={mean_ms:.1f}ms max={self._max_ms:.1f}ms")
            self._count = 0
            self._total_ms = 0.0
            self._max_ms = 0.0
        self._last_logged_at = now


_SEAL_TIMING_LOGGER = SealTimingLogger()


def record_seal_timing(duration_ms: float, app_dir=None) -> None:
    _SEAL_TIMING_LOGGER.record(duration_ms, app_dir=app_dir)


def flush_seal_timing(app_dir=None) -> None:
    _SEAL_TIMING_LOGGER.flush(app_dir=app_dir)




# ================================================================ turning shadow mode on (P3-7b)
#
# Owner decisions 2026-09-26: start point "1a" (the admin PC's state is the start point; other
# PCs replace live + replica + baseline with the admin PC's replica after a restart), the edits
# only a non-admin PC had are offered to the P2-8 salvage import, the non-own-origin guard applies
# only where a replica is built from the live DBs (the admin path), and office snapshots are
# exported with mode "off" (sync_snapshot._clean_exported_db) so a PC added during the shadow
# week starts in "off" and turns shadow on like the others.

def _utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _write_json(path: Path, obj: dict) -> None:
    import sera_keys
    path.parent.mkdir(parents=True, exist_ok=True)
    sera_keys.atomic_write(path, json.dumps(obj, indent=1, sort_keys=True).encode("utf-8"))


def _read_json(path: Path) -> Optional[dict]:
    try:
        obj = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _office_hex(app_dir: Path) -> str:
    """This PC's office DEK as hex, checked against office.json (office mode only)."""
    import hmac
    import sera_keys
    office = sera_keys.load_office(app_dir)
    if office is None:
        raise ShadowStartRefused("shadow mode needs office mode (this PC has no office key)")
    dek = sera_keys.load_dek(app_dir)
    if not hmac.compare_digest(sera_keys.key_id(dek), office.key_id):
        raise ShadowStartError("the stored office key doesn't match office.json")
    return dek.hex()


def _is_admin(db) -> bool:
    with db._connect() as conn:
        return bool(db.is_admin_pc(conn))


def _start_dir(app_dir) -> Path:
    return Path(app_dir) / "incoming" / START_DIRNAME


def _shadow_dir(app_dir) -> Path:
    return Path(app_dir) / SHADOW_DIRNAME


def _write_started(app_dir, method: str, source_device: str) -> None:
    _write_json(_shadow_dir(app_dir) / STARTED_FILE,
                {"started_at": _utc_now_iso(), "method": method, "source_device": source_device})


def _check_mode_off(db) -> None:
    mode = db.get_sync_mode()
    if mode == "shadow":
        raise ShadowStartRefused("shadow mode is already on on this PC")
    if mode == "live":
        raise ShadowStartRefused("this PC is already live; shadow mode is only for before go-live")
    if mode != "off":
        raise ShadowStartRefused(f"unexpected sync mode {mode!r}")


def start_shadow_mode(db, app_dir=None) -> None:
    """The admin PC turns shadow mode on from its own live DBs (P3-7b option 1a; the other PCs
    then download its replica with ``request_shadow_start``). Refuses (``ShadowStartRefused``)
    unless this is the office admin PC in mode ``off`` with no old shadow files and no other PC's
    changes in its live ``_sync_vector`` (``enable_shadow_mode``'s guard)."""
    import sera_keys
    app_dir = Path(app_dir or os.path.dirname(os.path.abspath(db.db_path)))
    if sera_keys.load_office(app_dir) is None:
        raise ShadowStartRefused("shadow mode needs office mode (this PC has no office key)")
    if not _is_admin(db):
        raise ShadowStartRefused(
            "only the admin PC starts shadow mode from its own data; on this PC use "
            "\"Start shadow mode\" after the admin PC has, to download its state")
    _check_mode_off(db)
    enable_shadow_mode(db, app_dir)
    _write_started(app_dir, "admin", _device_id(db))
    _log_line(app_dir, "shadow mode started on the admin PC (start point for the other PCs)")


# ---------------------------------------------------------------- admin PC: serve the replica

def _export_replica(app_dir: Path, hex_key: str, dest_dir: Path) -> list:
    """A consistent, cleaned copy of both replica files in *dest_dir* (the P2-6 export: local
    tables dropped, mode off). Holds ``_REPLICA_LOCK`` so no remote apply or own-change mirror
    lands between the master and raw exports."""
    import sync_snapshot
    replica_master, replica_raw = replica_paths(app_dir)
    out = []
    with _REPLICA_LOCK:
        if not replica_master.exists():
            raise ShadowStartError("no shadow replica on this PC")
        dest_master = dest_dir / sync_snapshot.MASTER_DB_NAME
        sync_snapshot._export_database(replica_master, dest_master, hex_key)
        out.append(dest_master)
        if replica_raw.exists() and replica_raw.stat().st_size > 0:
            dest_raw = dest_dir / sync_snapshot.RAW_DB_NAME
            sync_snapshot._export_database(replica_raw, dest_raw, hex_key)
            out.append(dest_raw)
    for f in out:
        sync_snapshot._clean_exported_db(f, hex_key)
    return out


def _file_vectors(path: Path, hex_key: str) -> dict:
    import sync_capture
    conn = sync_capture._open(str(path), hex_key, 5.0)
    try:
        return {o: int(s) for o, s in conn.execute("SELECT origin, max_seq FROM _sync_vector")}
    finally:
        conn.close()


def handle_shadow_snapshot_session(session, db, app_dir=None) -> None:
    """Admin PC side of ``{"t": "shadow_snapshot"}`` (routed by ``sync_office.dispatch_session``):
    streams a manifest and this PC's shadow **replica** files. Only the office admin PC in mode
    shadow serves it; anything else gets ``{"t": "error"}`` with a reason."""
    import sera_keys
    import sync_snapshot
    app_dir = Path(app_dir or os.path.dirname(os.path.abspath(db.db_path)))
    peer = (session.peer_device_id or "")[:8]
    try:
        frame = session.recv()
    except Exception as exc:
        _log.warning("shadow snapshot session from %s ended before its request: %s", peer, exc)
        return

    def _refuse(reason: str) -> None:
        _log_line(app_dir, f"refused shadow start download from {peer}: {reason}")
        try:
            session.send({"t": sync_snapshot.FRAME_ERROR, "error": reason})
        except Exception:
            pass

    if not isinstance(frame, dict) or frame.get("t") != FRAME_SHADOW_SNAPSHOT:
        _refuse("expected a shadow_snapshot request")
        return
    try:
        office = sera_keys.load_office(app_dir)
        admin = office is not None and _is_admin(db)
    except Exception:
        office, admin = None, False
    if office is None:
        _refuse("this PC is not in office mode")
        return
    if not admin:
        _refuse("this PC is not the admin PC; download from the admin PC")
        return
    if db.get_sync_mode() != "shadow" or not replica_paths(app_dir)[0].exists():
        _refuse("the admin PC is not in shadow mode yet; start shadow mode there first")
        return

    try:
        with tempfile.TemporaryDirectory(prefix="sera_shadow_snap_") as td:
            files = _export_replica(app_dir, db.hex_key, Path(td))
            vectors = {}
            for f in files:
                vectors.update(_file_vectors(f, db.hex_key))
            manifest = {
                "t": sync_snapshot.FRAME_MANIFEST,
                "office_id": office.office_id,
                "key_id": office.key_id,
                "source_device": _device_id(db),
                "vectors": vectors,
                "files": [{"name": f.name, "size": f.stat().st_size, "sha256": _sha256(f)} for f in files],
            }
            session.send(manifest)
            for f in files:
                session.send_file(f)
    except Exception as exc:
        _log.exception("shadow start download for %s failed", peer)
        _refuse(f"export failed ({type(exc).__name__})")
        return
    _log_line(app_dir, f"served the shadow replica to {peer} ({len(files)} file(s))")


# ---------------------------------------------------------------- other PCs: download + plan

@dataclass
class ShadowStartPlan:
    """A verified download of the admin PC's replica, waiting for the user's decision."""
    new_dir: str
    source_device: str
    vectors: dict
    report: Any                      # sync_rejoin.SalvageReport (dry run): only on this PC
    files: list = field(default_factory=list)


def has_pending_shadow_start(app_dir) -> bool:
    return (_start_dir(app_dir) / PENDING_START_FILE).exists()


def cancel_shadow_start(app_dir) -> None:
    """Drops a download that wasn't staged, or a staged one before the restart. Only the
    downloaded copies in ``incoming/shadow_start/`` go; the live DBs are never touched."""
    shutil.rmtree(_start_dir(app_dir), ignore_errors=True)


def _salvage_dry_run(old_dir: Path, new_dir: Path, hex_key: str, token_letter) -> Any:
    """``sync_rejoin.plan_salvage`` of *old_dir* (this PC's data) against a scratch copy of
    *new_dir* (so the downloaded files are never opened, not even read-only)."""
    import sync_rejoin
    with tempfile.TemporaryDirectory(prefix="sera_shadow_plan_") as td:
        for name in ("master.db", "rawPayload.db"):
            if (new_dir / name).exists():
                shutil.copy2(new_dir / name, Path(td) / name)
        return sync_rejoin.plan_salvage(old_dir, hex_key, td, hex_key, token_letter=token_letter)


def request_shadow_start(db, app_dir, session, *, timeout: float = 120.0, on_progress=None) -> ShadowStartPlan:
    """A non-admin PC's step 1 (P3-7b option 1a): downloads the admin PC's shadow replica over
    *session* (mutual TLS, connected to the admin PC) into ``incoming/shadow_start/new/``,
    verifies it, and dry-runs the salvage of this PC's live DBs against it -- the plan's
    ``report`` is what only this PC has (clients, audit entries, tracker filings), for the
    dialog's count. Writes nothing live. Raises ``ShadowStartRefused`` (this PC's state) or
    ``ShadowStartError`` (the admin PC refused, the transfer or a check failed); on any failure
    the partial download is removed."""
    import hmac
    import sera_keys
    import sync_snapshot
    import sync_transport
    app_dir = Path(app_dir)
    office = sera_keys.load_office(app_dir)
    if office is None:
        raise ShadowStartRefused("shadow mode needs office mode (this PC has no office key)")
    if _is_admin(db):
        raise ShadowStartRefused(
            "this is the admin PC: it starts shadow mode from its own data, it doesn't download")
    _check_mode_off(db)
    _refuse_if_old_shadow_files(app_dir)
    if has_pending_shadow_start(app_dir):
        raise ShadowStartRefused("a shadow-mode start is already downloaded; restart Sera to finish it")
    hex_key = _office_hex(app_dir)
    # Review fix (#6): don't rely on the caller having dialled the right PC.
    admin = admin_device_id(db, app_dir)
    if not admin or not hmac.compare_digest(getattr(session, "peer_device_id", None) or "", admin):
        raise ShadowStartError("the connected PC is not the office admin PC")

    start_dir = _start_dir(app_dir)
    shutil.rmtree(start_dir, ignore_errors=True)
    new_dir = start_dir / "new"
    new_dir.mkdir(parents=True)
    try:
        session.send({"t": FRAME_SHADOW_SNAPSHOT})
        manifest = session.recv(wait=timeout)
        if not isinstance(manifest, dict):
            raise ShadowStartError("the admin PC sent an invalid reply")
        if manifest.get("t") == sync_snapshot.FRAME_ERROR:
            raise ShadowStartError(f"the admin PC refused: {str(manifest.get('error'))[:200]}")
        if manifest.get("t") != sync_snapshot.FRAME_MANIFEST:
            raise ShadowStartError("the admin PC sent an invalid reply")
        if manifest.get("office_id") != office.office_id or manifest.get("key_id") != office.key_id:
            raise ShadowStartError("the download belongs to a different office or office key")
        source = manifest.get("source_device")
        if not isinstance(source, str) or not hmac.compare_digest(source, session.peer_device_id or ""):
            raise ShadowStartError("the download doesn't come from the PC this session is connected to")
        files_meta = manifest.get("files")
        if not isinstance(files_meta, list) or not files_meta:
            raise ShadowStartError("the download has no database files")
        names = []
        for f in files_meta:
            name = f.get("name") if isinstance(f, dict) else None
            size = f.get("size") if isinstance(f, dict) else None
            sha = f.get("sha256") if isinstance(f, dict) else None
            if (not isinstance(name, str) or name not in sync_snapshot.ALLOWED_DB_NAMES
                    or name in names or type(size) is not int or size < 0
                    or not isinstance(sha, str) or len(sha) != 64):
                raise ShadowStartError("the download's file list is invalid")
            progress = (lambda n, _f=name, _s=size: on_progress(_f, n, _s)) if on_progress else None
            try:
                got = session.recv_file(new_dir / name, size, on_progress=progress)
            except sync_transport.ProtocolError as exc:
                raise ShadowStartError(f"bad file transfer for {name}: {exc}") from None
            if not hmac.compare_digest(got, sha):
                raise ShadowStartError(f"{name} doesn't match its checksum")
            names.append(name)
        if set(names) != set(sync_snapshot.ALLOWED_DB_NAMES):
            # Review fix (#5): without rawPayload.db this PC's own would be moved aside with
            # nothing in its place.
            raise ShadowStartError("the download needs both master.db and rawPayload.db")
        for name in names:
            try:
                sync_snapshot._verify_downloaded_db(new_dir / name, hex_key)
            except sync_snapshot.SnapshotError as exc:
                raise ShadowStartError(str(exc)) from None

        # This PC's own data, exported consistently, for the dry-run count only (the start-up
        # swap salvages from the live files it moves aside, which also hold later edits).
        old_dir = start_dir / "old"
        old_dir.mkdir()
        sync_snapshot._export_database(Path(db.db_path), old_dir / "master.db", hex_key)
        if db.raw_db_path and Path(db.raw_db_path).exists():
            sync_snapshot._export_database(Path(db.raw_db_path), old_dir / "rawPayload.db", hex_key)
        report = _salvage_dry_run(old_dir, new_dir, hex_key, db.get_token_letter())
        shutil.rmtree(old_dir, ignore_errors=True)
    except BaseException as exc:
        shutil.rmtree(start_dir, ignore_errors=True)
        if isinstance(exc, (ShadowStartRefused, ShadowStartError)) or not isinstance(exc, Exception):
            raise
        raise ShadowStartError(f"could not download the admin PC's state: {exc}") from exc

    vectors = manifest.get("vectors") if isinstance(manifest.get("vectors"), dict) else {}
    _log_line(app_dir, f"downloaded the admin PC's shadow replica from {source[:8]} "
                       f"({len(names)} file(s)); only on this PC: {len(report.to_insert)} client(s), "
                       f"{len(report.conflicts)} with different values, {report.audit_new} audit, "
                       f"{report.tracker_new} tracker, {report.timelines_new} timeline(s)")
    return ShadowStartPlan(new_dir=str(new_dir), source_device=source, vectors=vectors,
                           report=report, files=names)


def stage_shadow_start(app_dir, plan: ShadowStartPlan) -> None:
    """Step 2: the user confirmed. Records the download (with each file's checksum as it is now)
    in ``incoming/shadow_start/pending.json``; ``apply_pending_shadow_start`` installs it at the
    next start-up, before any database is opened."""
    new_dir = Path(plan.new_dir)
    files = [{"name": n, "sha256": _sha256(new_dir / n)} for n in plan.files]
    _write_json(_start_dir(app_dir) / PENDING_START_FILE,
                {"created_at": _utc_now_iso(), "source_device": plan.source_device, "files": files})
    _log_line(app_dir, "shadow mode start staged; installs at the next start-up")


# ---------------------------------------------------------------- start-up swap

def _own_device_id(app_dir: Path) -> str:
    import sync_identity
    ident = sync_identity.load_device_identity(app_dir)
    if ident is None or not ident.device_id:
        raise ShadowStartError("this PC's device identity can't be read")
    return ident.device_id


def _prepare_common(path: Path, hex_key: str, device_id: str, db_name: str) -> None:
    """The downloaded replica file as this PC's: its device id and stream, mode off, and no
    peer vectors (those were the admin PC's view of its peers)."""
    import sync_capture
    import sync_tables
    conn = sync_capture._open(str(path), hex_key, 10.0)
    try:
        conn.execute("BEGIN IMMEDIATE")
        sync_tables.set_sync_device_id(conn, db_name, device_id)
        conn.execute("UPDATE _sync_meta SET value = 'off' WHERE key = 'mode'")
        conn.execute("DELETE FROM _sync_peer_vectors")
        conn.execute("COMMIT")
    finally:
        conn.close()


def _carry_over_local_state(new_master: Path, old_master: Path, hex_key: str) -> None:
    """Copies this PC's ``_local_*`` tables (address book, ...) from the old live master.db and
    any member record the old DB has at a higher rev (a revoke it learned, say) into the new one."""
    import sync_admin
    import sync_capture
    conn = sync_capture._open(str(new_master), hex_key, 10.0)
    try:
        sync_admin.ensure_members_table(conn)
        escaped = str(old_master).replace("'", "''")
        conn.execute(f"ATTACH DATABASE '{escaped}' AS old KEY \"x'{hex_key}'\";")
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                locals_ = conn.execute(
                    "SELECT name, sql FROM old.sqlite_master WHERE type = 'table' "
                    "AND name LIKE '\\_local\\_%' ESCAPE '\\'").fetchall()
                for name, sql in locals_:
                    if not conn.execute("SELECT 1 FROM main.sqlite_master WHERE type='table' AND name=?",
                                        (name,)).fetchone():
                        conn.execute(sql)
                    new_cols = {r[1] for r in conn.execute(f'PRAGMA main.table_info("{name}")')}
                    cols = [r[1] for r in conn.execute(f'PRAGMA old.table_info("{name}")') if r[1] in new_cols]
                    if cols:
                        col_sql = ", ".join(f'"{c}"' for c in cols)
                        conn.execute(f'DELETE FROM main."{name}"')
                        conn.execute(f'INSERT INTO main."{name}" ({col_sql}) SELECT {col_sql} FROM old."{name}"')
                if conn.execute("SELECT 1 FROM old.sqlite_master WHERE type='table' "
                                "AND name='_sync_members'").fetchone():
                    conn.execute(
                        "INSERT OR REPLACE INTO main._sync_members (device_id, record_json, rev) "
                        "SELECT o.device_id, o.record_json, o.rev FROM old._sync_members o "
                        "LEFT JOIN main._sync_members m ON m.device_id = o.device_id "
                        "WHERE m.device_id IS NULL OR o.rev > m.rev")
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.execute("DETACH DATABASE old")
    finally:
        conn.close()


def _set_mode(path: Path, hex_key: str, mode: str) -> None:
    import sync_capture
    conn = sync_capture._open(str(path), hex_key, 10.0)
    try:
        conn.execute("INSERT INTO _sync_meta(key, value) VALUES ('mode', ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (mode,))
    finally:
        conn.close()


def _fail_pending(app_dir: Path, reason: str) -> None:
    """Keeps the failed pending file as ``incoming/shadow_start.pending.json.failed`` for
    diagnosis and drops the downloaded copies; the next start-up doesn't try again. Only called
    when the live DBs are as they were before the start (never moved, or moved back)."""
    start_dir = _start_dir(app_dir)
    pending = start_dir / PENDING_START_FILE
    try:
        failed = Path(app_dir) / "incoming" / f"{START_DIRNAME}.{PENDING_START_FILE}.failed"
        if pending.exists():
            os.replace(pending, failed)
    except OSError:
        pass
    shutil.rmtree(start_dir, ignore_errors=True)
    _log_line(app_dir, f"shadow mode start FAILED, live databases unchanged: {reason}")


def _carry_own_changes(common: Path, old_live: Path, hex_key: str, device_id: str, db_name: str,
                       admin_pubkey, master_path: Optional[Path]) -> int:
    """Review fix (blocking #1): this PC's own changes that the downloaded replica doesn't have
    yet (sealed here, never received by the admin PC -- e.g. saved just before a Reset) are
    applied to the prepared file from the old live DB's ``_sync_changes``. Without this the
    sealer (which never numbers below ``keys/sync_seq.json``) would continue after them and
    leave a permanent gap in this PC's stream. Returns how many were carried over."""
    import sync_apply
    import sync_capture
    if not old_live.exists():
        return 0
    origin = _origin_for(device_id, "master" if db_name == "master" else "raw")
    have = _replica_vector(common, hex_key, origin)
    changes = _changes_since(str(old_live), hex_key, origin, have)
    if not changes:
        return 0
    seqs = [int(c["origin_seq"]) for c in changes]
    if seqs != list(range(have + 1, have + 1 + len(seqs))):
        raise ShadowStartError(
            f"this PC's own {db_name} changes after #{have} are incomplete in its database, so they "
            "can't be carried over")
    kw = dict(admin_pubkey=admin_pubkey, emit=False)
    if master_path is not None:
        kw["master_path"] = str(master_path)
    sync_apply.apply_batch(str(common), hex_key, "master" if db_name == "master" else "raw", changes, **kw)
    if _replica_vector(common, hex_key, origin) != seqs[-1]:
        raise ShadowStartError(f"this PC's own {db_name} changes could not all be carried over")
    return len(changes)


def _undo_moves(moves: list) -> None:
    """Puts back every move that was done, newest first (so an installed file leaves before the
    original it replaced returns)."""
    for src, dst in reversed(moves):
        src, dst = Path(src), Path(dst)
        if dst.exists() and not src.exists():
            try:
                os.replace(dst, src)
            except OSError:
                _log.error("could not move %s back to %s", dst, src)


def _finish_start(app: Path, pre_dir: Path, source: str) -> dict:
    """Last step of the swap; safe to run again after a crash."""
    _write_started(app, "admin_replica", source)
    _write_json(_shadow_dir(app) / SALVAGE_PENDING_FILE,
                {"old_dir": str(pre_dir), "created_at": _utc_now_iso()})
    start_dir = _start_dir(app)
    for name in (PENDING_START_FILE, INSTALL_JOURNAL_FILE):
        try:
            (start_dir / name).unlink()
        except OSError:
            pass
    shutil.rmtree(start_dir, ignore_errors=True)
    _log_line(app, f"shadow mode started from the admin PC's replica ({source[:8]}); "
                   f"this PC's previous databases are in {pre_dir.name}")
    return {"pre_start_dir": str(pre_dir), "source_device": source}


def _recover_install(app: Path, journal: dict) -> dict:
    """Review fix (#3): the previous start-up died while moving files. If every move finished,
    complete the install; otherwise put everything back and report that honestly."""
    moves = journal.get("moves") or []
    last = moves[-1] if moves else None
    if last and Path(last[1]).exists() and not Path(last[0]).exists():
        _log_line(app, "shadow mode start was interrupted after its files moved; finishing it")
        return _finish_start(app, Path(journal["pre_dir"]), str(journal.get("source_device") or ""))
    _undo_moves(moves)
    try:
        (_start_dir(app) / INSTALL_JOURNAL_FILE).unlink()
    except OSError:
        pass
    _fail_pending(app, "the previous start-up was interrupted while moving files; they were put back")
    raise ShadowStartError("the shadow-mode start was interrupted and has been put back; "
                           "this PC's database was not changed")


def apply_pending_shadow_start(app_dir, hex_key: Optional[str] = None, *,
                               _undo_on_error: bool = True) -> Optional[dict]:
    """Step 3, at start-up **before any database is opened** (``main.py``). Returns None when
    nothing is staged. Otherwise installs the staged download:

      - checks the files against the checksums recorded when staged, and that no shadow files
        exist yet (the guard, again);
      - builds, in ``incoming/shadow_start/prep/``: the baseline (the download as this PC's: its
        device id, mode off), the replica (the same plus this PC's own changes the download
        didn't have yet, ``_carry_own_changes``) and the new live DBs (as the replica, plus this
        PC's ``_local_*`` tables and newer member records, mode shadow);
      - writes a journal of the moves, moves the old live DBs and their sidecars to
        ``shadow/pre-start-<ts>/`` (§0 rule 3; the salvage source), then the prepared files into
        place. A failure while moving puts everything back; a crash while moving is finished
        or put back at the next start from the journal (``_recover_install``).

    Returns ``{"pre_start_dir", "source_device"}``. Raises ``ShadowStartError`` on failure (the
    live DBs are then as they were, and the start isn't retried); a key that can't be loaded
    (``sera_keys`` errors) propagates without touching anything, so it's retried next time.
    ``_undo_on_error`` is for tests that simulate a crash mid-move.
    """
    import sera_keys
    import sync_snapshot
    app = Path(app_dir)
    start_dir = _start_dir(app)
    pending_path = start_dir / PENDING_START_FILE
    if not pending_path.exists():
        return None
    journal = _read_json(start_dir / INSTALL_JOURNAL_FILE)
    if journal is not None:
        return _recover_install(app, journal)
    pending = _read_json(pending_path)
    if pending is None or not isinstance(pending.get("files"), list):
        _fail_pending(app, "pending.json is unreadable")
        raise ShadowStartError("the staged shadow-mode start is unreadable")
    hex_key = hex_key or _office_hex(app)

    new_dir = start_dir / "new"
    prep = start_dir / "prep"
    carried = 0
    try:
        _refuse_if_old_shadow_files(app)
        names = []
        for f in pending["files"]:
            name = f.get("name") if isinstance(f, dict) else None
            if name not in sync_snapshot.ALLOWED_DB_NAMES or name in names:
                raise ShadowStartError("the staged file list is invalid")
            path = new_dir / name
            if not path.is_file() or _sha256(path) != f.get("sha256"):
                raise ShadowStartError(f"the downloaded {name} changed or is missing since it was confirmed")
            sync_snapshot._verify_downloaded_db(path, hex_key)
            names.append(name)
        if set(names) != set(sync_snapshot.ALLOWED_DB_NAMES):
            raise ShadowStartError("the staged download needs both master.db and rawPayload.db")
        device_id = _own_device_id(app)
        office = sera_keys.load_office(app)
        admin_pubkey = office.admin_pubkey if office else None

        shutil.rmtree(prep, ignore_errors=True)
        prep.mkdir(parents=True)
        plan = []   # (prepared file, final path)
        replica_master, replica_raw = replica_paths(app)
        baseline_master, baseline_raw = baseline_paths(app)
        prepared_master = None
        for name, db_name, replica, baseline in (
                (sync_snapshot.MASTER_DB_NAME, "master", replica_master, baseline_master),
                (sync_snapshot.RAW_DB_NAME, "raw", replica_raw, baseline_raw)):
            common = prep / f"common_{name}"
            shutil.copy2(new_dir / name, common)
            _prepare_common(common, hex_key, device_id, db_name)
            p = prep / baseline.name
            shutil.copy2(common, p)                      # baseline: the download only
            plan.append((p, baseline))
            carried += _carry_own_changes(common, app / name, hex_key, device_id, db_name,
                                          admin_pubkey, prepared_master)
            p = prep / replica.name
            shutil.copy2(common, p)
            plan.append((p, replica))
            live = prep / f"live_{name}"
            os.replace(common, live)
            if db_name == "master":
                prepared_master = live
                if (app / name).exists():
                    _carry_over_local_state(live, app / name, hex_key)
            _set_mode(live, hex_key, "shadow")
            plan.append((live, app / name))
    except BaseException as exc:
        shutil.rmtree(prep, ignore_errors=True)
        if not isinstance(exc, Exception):
            raise
        _fail_pending(app, str(exc) if isinstance(exc, (ShadowStartError, ShadowStartRefused))
                      else type(exc).__name__)
        if isinstance(exc, ShadowStartError):
            raise
        raise ShadowStartError(f"the staged shadow-mode start could not be prepared: {exc}") from exc

    ts = time.strftime("%Y%m%d_%H%M%S")
    pre_dir = _shadow_dir(app) / f"{PRE_START_PREFIX}{ts}"
    n = 1
    while pre_dir.exists():
        pre_dir = _shadow_dir(app) / f"{PRE_START_PREFIX}{ts}_{n}"
        n += 1
    moves = []
    for name in (sync_snapshot.MASTER_DB_NAME, sync_snapshot.RAW_DB_NAME):
        for suffix in (*_SIDECARS, ""):
            src = app / f"{name}{suffix}"
            if src.exists():
                moves.append((str(src), str(pre_dir / src.name)))
    moves += [(str(s), str(d)) for s, d in plan]
    source = str(pending.get("source_device") or "")
    pre_dir.mkdir(parents=True)
    _shadow_dir(app).mkdir(parents=True, exist_ok=True)
    _write_json(start_dir / INSTALL_JOURNAL_FILE,
                {"pre_dir": str(pre_dir), "source_device": source, "moves": moves})
    try:
        for src, dst in moves:
            os.replace(src, dst)
    except BaseException as exc:
        if not _undo_on_error:
            raise
        _undo_moves(moves)
        try:
            (start_dir / INSTALL_JOURNAL_FILE).unlink()
        except OSError:
            pass
        _fail_pending(app, f"files could not be moved ({type(exc).__name__}); put back")
        if not isinstance(exc, Exception):
            raise
        raise ShadowStartError(f"the shadow-mode start could not be installed and was put back: {exc}") from exc

    if carried:
        _log_line(app, f"carried {carried} of this PC's own change(s) the admin PC didn't have yet")
    return _finish_start(app, pre_dir, source)


# ---------------------------------------------------------------- salvage after the swap

def pending_shadow_salvage(app_dir) -> Optional[dict]:
    """``{"old_dir", ...}`` while the salvage offer after a shadow start is still open."""
    state = _read_json(_shadow_dir(app_dir) / SALVAGE_PENDING_FILE)
    return state if state and state.get("old_dir") else None


def _salvage_state(app: Path) -> dict:
    state = pending_shadow_salvage(app)
    if state is None:
        raise ShadowStartError("no shadow-mode salvage is waiting on this PC")
    return state


def _token_letter(app: Path, hex_key: str) -> Optional[str]:
    import sync_admin
    import sync_capture
    conn = sync_capture._open(str(app / "master.db"), hex_key, 5.0)
    try:
        return sync_admin.get_token_letter(app, conn=conn, device_id=_own_device_id(app))
    except Exception:
        return None
    finally:
        conn.close()


def plan_shadow_salvage(app_dir, *, take_legacy=()):
    """Dry run of importing what only the pre-start databases had (P2-8 salvage). Writes nothing."""
    import sync_rejoin
    app = Path(app_dir)
    state = _salvage_state(app)
    hex_key = _office_hex(app)
    return sync_rejoin.plan_salvage(state["old_dir"], hex_key, app, hex_key, take_legacy=take_legacy,
                                    token_letter=_token_letter(app, hex_key))


def _finish_salvage(app: Path, how: str) -> None:
    path = _shadow_dir(app) / SALVAGE_PENDING_FILE
    try:
        os.replace(path, path.with_name(f"{SALVAGE_PENDING_FILE}.{how}-{time.strftime('%Y%m%d_%H%M%S')}"))
    except OSError:
        pass


def apply_shadow_salvage(app_dir, *, take_legacy=()):
    """Imports what only the pre-start databases had into the live DBs (P2-8 salvage). The live
    DBs are in mode shadow, so the capture triggers record the import; it's sealed, mirrored
    into the replica and sent to the other PCs like any edit. Writes ``logs/salvage-<ts>.txt``
    and closes the offer."""
    import sync_rejoin
    app = Path(app_dir)
    state = _salvage_state(app)
    hex_key = _office_hex(app)
    report = sync_rejoin.apply_salvage(state["old_dir"], hex_key, app, hex_key, take_legacy=take_legacy,
                                       token_letter=_token_letter(app, hex_key))
    report.notes.append("Imported after turning shadow mode on (P3-7b); the old databases stay in "
                        "the folder above.")
    report.report_path = str(sync_rejoin.write_salvage_report(app, report))
    _finish_salvage(app, "done")
    _log_line(app, f"shadow start salvage imported: {len(report.to_insert)} client(s), "
                   f"{len(report.taken)} with this PC's values, {report.audit_new} audit, "
                   f"{report.tracker_new} tracker, {report.timelines_new} timeline(s)")
    return report


def skip_shadow_salvage(app_dir) -> Optional[Path]:
    """The user chose not to import. Writes a short report and closes the offer."""
    import sync_rejoin
    app = Path(app_dir)
    state = pending_shadow_salvage(app)
    if state is None:
        return None
    report = sync_rejoin.SalvageReport(dry_run=True, legacy_dir=state["old_dir"])
    report.notes.append("Shadow mode start (P3-7b): the user chose not to import this PC's own data. "
                        "It is still in the folder above.")
    path = sync_rejoin.write_salvage_report(app, report)
    _finish_salvage(app, "skipped")
    _log_line(app, "shadow start salvage skipped by the user")
    return path


# ---------------------------------------------------------------- reset + status

def reset_shadow_mode(db, app_dir=None) -> list:
    """Admin-only in the UI ("Reset shadow mode"): sets mode off and renames the replica, the
    baseline (with any sidecars) and ``started.json`` to ``*.bak-<ts>`` (§0 rule 3). That also
    resets the shadow week. Returns the names renamed.

    Afterwards a non-admin PC can start again from the admin PC's replica (its own changes the
    admin hadn't received are carried over by the swap). Refused on the admin PC once its live
    DBs count other PCs' changes as received: it could never start again (``enable_shadow_mode``'s
    guard) and nobody could download from it."""
    app_dir = Path(app_dir or os.path.dirname(os.path.abspath(db.db_path)))
    if has_pending_shadow_start(app_dir):
        raise ShadowStartRefused("a shadow-mode start is waiting for a restart; restart Sera first")
    if db.get_sync_mode() == "live":
        raise ShadowStartRefused("this PC is already live; there is no shadow mode to reset")
    # Review fix (#2): the admin PC is every other PC's start point. Once its live DBs count
    # other PCs' changes as received, its own guard would refuse a new start and nobody could
    # download from it: one reset would end the shadow week for the whole office.
    if _is_admin(db) and _remote_origins(db):
        raise ShadowStartRefused(
            "this is the admin PC and it has already received changes from other PCs; resetting "
            "it would leave no start point for the office. Reset the other PCs instead")
    db.set_sync_mode("off")
    renamed = []
    with _REPLICA_LOCK:
        for p in (*replica_paths(app_dir), *baseline_paths(app_dir)):
            for q in (Path(f"{p}{s}") for s in _SIDECARS):
                if q.exists():
                    _backup_existing(q)
            if p.exists():
                _backup_existing(p)
                renamed.append(p.name)
        started = _shadow_dir(app_dir) / STARTED_FILE
        if started.exists():
            _backup_existing(started)
            renamed.append(started.name)
    _log_line(app_dir, f"shadow mode reset: mode off, {len(renamed)} file(s) renamed to *.bak-<ts>")
    return renamed


def shadow_status(db, app_dir=None) -> dict:
    """For the panel: ``mode``, ``replica`` (bool), ``started_at``, ``method``, ``day`` (1-based
    day of the shadow week, None when not started), ``pending_start`` and ``salvage_pending``."""
    import calendar
    app_dir = Path(app_dir or os.path.dirname(os.path.abspath(db.db_path)))
    started = _read_json(_shadow_dir(app_dir) / STARTED_FILE) or {}
    started_at = started.get("started_at") if isinstance(started.get("started_at"), str) else None
    day = None
    if started_at:
        try:
            t0 = calendar.timegm(time.strptime(started_at, "%Y-%m-%dT%H:%M:%SZ"))
            day = max(1, int((time.time() - t0) // 86400) + 1)
        except ValueError:
            day = None
    return {
        "mode": db.get_sync_mode(),
        "replica": replica_paths(app_dir)[0].exists(),
        "started_at": started_at,
        "method": started.get("method"),
        "day": day,
        "pending_start": has_pending_shadow_start(app_dir),
        "salvage_pending": pending_shadow_salvage(app_dir) is not None,
    }


def admin_device_id(db, app_dir=None) -> Optional[str]:
    """The office admin PC's device id from the verified office_admin record, or None."""
    import sera_keys
    import sync_admin
    app_dir = Path(app_dir or os.path.dirname(os.path.abspath(db.db_path)))
    office = sera_keys.load_office(app_dir)
    if office is None or not office.admin_pubkey:
        return None
    with db._connect() as conn:
        rec = sync_admin.get_office_admin(conn, office.admin_pubkey)
    return rec.get("device_id") if rec else None


def open_admin_session(engine, db, app_dir=None):
    """A mutual-TLS session to the office admin PC over the engine's transport, trying its
    addresses in connect order (P2-5). Raises ``ShadowStartError`` if it can't be reached."""
    admin = admin_device_id(db, app_dir)
    if not admin:
        raise ShadowStartError("the office admin PC isn't known on this PC")
    addresses = engine.connect_order(admin)[:3]
    if not addresses:
        raise ShadowStartError("no address is known for the admin PC; open Sera there and wait a minute")
    last = None
    for ip, port in addresses:
        try:
            return engine.transport.connect(ip, port, admin)
        except Exception as exc:
            last = exc
    raise ShadowStartError(f"the admin PC can't be reached ({type(last).__name__}); is Sera open there?")
