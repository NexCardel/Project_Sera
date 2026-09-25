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
if shadow mode is already on -- see its docstring; a second snapshot from the live DBs would
silently and permanently lose remote data the old replica holds but the live DBs never did.

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
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

_log = logging.getLogger("sera.sync.shadow")

SHADOW_DIRNAME = "shadow"
REPLICA_MASTER_NAME = "replica_master.db"
REPLICA_RAW_NAME = "replica_raw.db"
BASELINE_MASTER_NAME = "baseline_master.db"
BASELINE_RAW_NAME = "baseline_raw.db"
SHADOW_LOG_NAME = "sync_shadow.log"

_CHANGE_COLS = ("origin", "origin_seq", "hlc", "tbl", "row_key", "op", "data", "sig")


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
    2026-09-25). There is currently no supported way to reset shadow mode once it is on.
    """
    import sync_snapshot
    if db.get_sync_mode() == "shadow":
        raise RuntimeError(
            "shadow mode is already on; enabling it again would silently and permanently lose "
            "any remote changes only the old replica holds (see enable_shadow_mode's docstring)")
    app_dir = Path(app_dir or os.path.dirname(os.path.abspath(db.db_path)))
    shadow_dir = app_dir / SHADOW_DIRNAME
    shadow_dir.mkdir(parents=True, exist_ok=True)

    replica_master, replica_raw = replica_paths(app_dir)
    baseline_master, baseline_raw = baseline_paths(app_dir)
    for p in (replica_master, replica_raw, baseline_master, baseline_raw):
        _backup_existing(p)

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
        if not replica_path.exists():
            raise RuntimeError("shadow mode has no replica yet (enable_shadow_mode was not run)")
        admin_pubkey = db._office_admin_pubkey()
        kw = dict(admin_pubkey=admin_pubkey, get_signer=db._admin_signer,
                  seq_state=sync_capture.seq_state_path(str(replica_path)), emit=False)
        if which == "raw":
            kw["master_path"] = str(replica_master)
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


def run_shadow_checks(db, app_dir=None, own_device_id: Optional[str] = None,
                       peer_digests: Optional[dict] = None) -> dict:
    """Runs the P3-7 checks and logs them. Called on demand (panel) and from a periodic timer
    once P3-8 wires one in. ``peer_digests`` (``{device_id: digest}``), when given, also runs
    the convergence check."""
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
    return out
