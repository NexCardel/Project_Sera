"""
sync_snapshot.py
----------------
Snapshot service and joiner install for Sera Sync v3 (blueprint §5, WP P2-6).

Server (mTLS, request ``{"t": "snapshot"}``):
  1. ``sqlcipher_export`` both DBs (master.db and rawPayload.db if present) into a temp dir.
  2. In the copies, delete ``_local_*`` tables (e.g. ``_local_addresses``) and the rows of
     ``local``-mode tables (``client_activity_stats``, ``client_recent_activity``).
     All ``app_settings`` rows are kept (§2 D7).
  3. Build ``manifest = {"t": "manifest", "office_id": ..., "key_id": ..., "schema_version": ...,
     "vectors": {}, "files": [{"name": ..., "size": ..., "sha256": ...}] }`` and stream it.
  4. Stream the exported files using chunk frames.

Joiner:
  1. Download into ``incoming/join/``.
  2. Verify: sha256; open with the DEK; ``cipher_integrity_check`` empty; ``quick_check`` ok;
     ``key_id`` and ``office_id`` match ``office.json``.
  3. Install with ``os.replace`` (no DB is open during joining). Continue start-up.
  4. The joiner never seeds default rows (F14): the DB comes only from the snapshot.

Never log passwords or key material. No PySide6 here (§0 rule 7).
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Callable, Iterator

import sqlcipher3.dbapi2 as sqlite3

import sera_keys
import sync_identity
import sync_transport
from sync_transport import MemberSet, Session, SyncTransport, TransportError

SYNC_PORT = 49159
FRAME_SNAPSHOT = "snapshot"
FRAME_MANIFEST = "manifest"
FRAME_ERROR = "error"

SNAPSHOT_TIMEOUT_SECONDS = 60.0
CONNECT_TIMEOUT_SECONDS = 10.0

JOIN_DIRNAME = "join"
PAIRING_STATE_FILE = "pairing.json"

MASTER_DB_NAME = "master.db"
RAW_DB_NAME = "rawPayload.db"
ALLOWED_DB_NAMES = frozenset({MASTER_DB_NAME, RAW_DB_NAME})

# Local-mode tables whose rows must be deleted from the snapshot (P3-1)
LOCAL_MODE_TABLES = frozenset({"client_activity_stats", "client_recent_activity"})

_log = logging.getLogger("sera.sync.snapshot")


class SnapshotError(Exception):
    """Base class for snapshot export, transfer, and installation errors."""


class SnapshotVerificationError(SnapshotError):
    """The snapshot is corrupt, tampered with, or does not match this office."""


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- server export

def _clean_exported_db(db_path: Path, dek_hex: str) -> None:
    """Drop _local_* tables and delete rows from local-mode tables."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(f"PRAGMA key = \"x'{dek_hex}'\";")
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall()]
        for table in tables:
            if table.startswith("_local_") or table == "_local":
                conn.execute(f'DROP TABLE IF EXISTS "{table}";')
            elif table in LOCAL_MODE_TABLES:
                conn.execute(f'DELETE FROM "{table}";')
        conn.commit()
        # Purge deleted rows and tables from freelists so no IP or local history remains
        conn.execute("VACUUM;")
    finally:
        conn.close()


def _export_database(src_path: Path, dest_path: Path, dek_hex: str) -> int:
    """Creates a consistent snapshot of src_path at dest_path using sqlcipher_export in BEGIN IMMEDIATE."""
    if dest_path.exists():
        raise SnapshotError(f"Destination {dest_path} already exists")

    conn = sqlite3.connect(str(src_path))
    try:
        conn.execute(f"PRAGMA key = \"x'{dek_hex}'\";")
        # BEGIN IMMEDIATE blocks writers for under a second to ensure a consistent snapshot point
        conn.execute("BEGIN IMMEDIATE;")
        try:
            uv_res = conn.execute("PRAGMA user_version;").fetchone()
            user_version = int(uv_res[0]) if uv_res and uv_res[0] is not None else 0

            escaped_dest = str(dest_path).replace("'", "''")
            conn.execute(f"ATTACH DATABASE '{escaped_dest}' AS snap KEY \"x'{dek_hex}'\";")
            try:
                conn.execute("SELECT sqlcipher_export('snap');")
                conn.execute(f"PRAGMA snap.user_version = {user_version};")
            finally:
                try:
                    conn.execute("DETACH DATABASE snap;")
                except Exception:
                    pass
            conn.execute("COMMIT;")
            return user_version
        except Exception:
            conn.execute("ROLLBACK;")
            raise
    finally:
        conn.close()


@contextlib.contextmanager
def temp_snapshot_export(app_dir: Path | str, dek: bytes) -> Iterator[tuple[dict, list[Path]]]:
    """Export both master.db and rawPayload.db into a temporary directory, cleaned and hashed.

    Yields ``(manifest, file_paths)``. The temporary directory is deleted on exit.
    """
    app_dir = Path(app_dir)
    office = sera_keys.load_office(app_dir)
    if office is None:
        raise SnapshotError("Not in office mode: office.json missing")

    dek_hex = dek.hex()
    master_path = app_dir / MASTER_DB_NAME
    raw_path = app_dir / RAW_DB_NAME

    if not master_path.exists():
        raise SnapshotError(f"Master database not found at {master_path}")

    with tempfile.TemporaryDirectory(prefix="sera_snap_") as td:
        temp_dir = Path(td)
        temp_master = temp_dir / MASTER_DB_NAME
        temp_raw = temp_dir / RAW_DB_NAME

        # Export master.db
        schema_version = _export_database(master_path, temp_master, dek_hex)
        _clean_exported_db(temp_master, dek_hex)

        files_to_send = [temp_master]

        # Export rawPayload.db if present
        if raw_path.exists() and raw_path.stat().st_size > 0:
            _export_database(raw_path, temp_raw, dek_hex)
            _clean_exported_db(temp_raw, dek_hex)
            files_to_send.append(temp_raw)

        files_meta = []
        for fpath in files_to_send:
            size = fpath.stat().st_size
            sha = _sha256_file(fpath)
            files_meta.append({
                "name": fpath.name,
                "size": size,
                "sha256": sha,
            })

        manifest = {
            "t": FRAME_MANIFEST,
            "office_id": office.office_id,
            "key_id": office.key_id,
            "schema_version": schema_version,
            "vectors": {},  # Phase 3; empty before
            "files": files_meta,
        }

        yield manifest, files_to_send


def make_office_snapshot(app_dir: Path | str, dest_dir: Path | str) -> tuple[dict, list[Path]]:
    """Export and clean office databases into dest_dir. Returns ``(manifest, exported_files)``."""
    app_dir = Path(app_dir)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dek = sera_keys.load_dek(app_dir)
    with temp_snapshot_export(app_dir, dek) as (manifest, files):
        copied_files = []
        for f in files:
            target = dest_dir / f.name
            shutil.copy2(f, target)
            copied_files.append(target)
        return manifest, copied_files


# ---------------------------------------------------------------- server handler

def handle_snapshot_session(session: Session, app_dir: Path | str) -> None:
    """Server-side handler for mutual-TLS session requesting a snapshot."""
    app_dir = Path(app_dir)
    try:
        frame = session.recv()
    except TransportError as exc:
        _log.warning("Snapshot session from %s ended before request: %s", session.peer_device_id, exc)
        return

    if frame.get("t") != FRAME_SNAPSHOT:
        _log.warning("Expected '%s' frame from %s, got %r", FRAME_SNAPSHOT, session.peer_device_id, frame.get("t"))
        try:
            session.send({"t": FRAME_ERROR, "error": "expected snapshot request"})
        except Exception:
            pass
        return

    try:
        dek = sera_keys.load_dek(app_dir)
    except Exception as exc:
        _log.error("Could not load DEK for snapshot request: %s", exc)
        try:
            session.send({"t": FRAME_ERROR, "error": "unable to read office encryption key"})
        except Exception:
            pass
        return

    try:
        with temp_snapshot_export(app_dir, dek) as (manifest, files):
            _log.info("Streaming snapshot manifest to %s: %d file(s)", session.peer_device_id, len(files))
            session.send(manifest)
            for fpath in files:
                _log.debug("Streaming %s (%d bytes) to %s", fpath.name, fpath.stat().st_size, session.peer_device_id)
                session.send_file(fpath)
            _log.info("Snapshot transfer to %s completed successfully", session.peer_device_id)
    except Exception as exc:
        _log.exception("Error exporting/streaming snapshot to %s: %s", session.peer_device_id, exc)
        try:
            session.send({"t": FRAME_ERROR, "error": "snapshot export failed"})
        except Exception:
            pass


# ---------------------------------------------------------------- joiner client

def has_pending_join(app_dir: Path | str) -> bool:
    """Returns True if pairing finished but the snapshot download was interrupted."""
    app_dir = Path(app_dir)
    join_state = app_dir / "incoming" / JOIN_DIRNAME / PAIRING_STATE_FILE
    master_db = app_dir / MASTER_DB_NAME
    return join_state.exists() and not master_db.exists()


def _verify_downloaded_db(db_path: Path, dek_hex: str) -> None:
    """Verify that db_path opens with dek_hex, and passes cipher_integrity_check and quick_check."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(f"PRAGMA key = \"x'{dek_hex}'\";")
        try:
            conn.execute("SELECT count(*) FROM sqlite_master;").fetchone()
        except sqlite3.DatabaseError as exc:
            raise SnapshotVerificationError(f"{db_path.name} could not be decrypted with DEK: {exc}") from None

        integrity = conn.execute("PRAGMA cipher_integrity_check;").fetchall()
        if integrity:
            raise SnapshotVerificationError(
                f"{db_path.name} failed cipher_integrity_check ({len(integrity)} issues: {integrity[:3]})"
            )

        quick = conn.execute("PRAGMA quick_check;").fetchall()
        if [tuple(r) for r in quick] != [("ok",)]:
            raise SnapshotVerificationError(f"{db_path.name} failed quick_check: {quick}")
    finally:
        conn.close()


def _backup_db_and_sidecars(db_path: Path, ts: str) -> None:
    """Rename existing db and any -wal/-shm/-journal sidecars to *.bak-<ts> (§0 rule 3)."""
    if db_path.exists():
        backup_target = db_path.with_name(f"{db_path.name}.bak-{ts}")
        if backup_target.exists():
            backup_target = db_path.with_name(f"{db_path.name}.bak-{ts}_{time.time_ns() % 1000000}")
        os.replace(db_path, backup_target)
        _log.info("Backed up existing database %s to %s", db_path.name, backup_target.name)
    for ext in ("-wal", "-shm", "-journal"):
        sc = Path(f"{db_path}{ext}")
        if sc.exists():
            sc_bak = db_path.with_name(f"{db_path.name}{ext}.bak-{ts}")
            try:
                os.replace(sc, sc_bak)
            except OSError:
                pass


def _install_downloaded_files(app_dir: Path, files: list[tuple[Path, str]]) -> list[str]:
    """Atomic installation into app_dir using os.replace with backups, installing master.db last."""
    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    installed_names = {filename for _, filename in files}

    # If rawPayload.db exists locally but is not part of this snapshot, set it aside
    # so an old-key rawPayload.db doesn't break office mode startup (§0 rule 3).
    if RAW_DB_NAME not in installed_names:
        local_raw = app_dir / RAW_DB_NAME
        if local_raw.exists():
            _backup_db_and_sidecars(local_raw, ts)

    # Install order: install non-master DBs first, master.db LAST.
    # has_pending_join checks for master.db existence; installing master.db last ensures
    # a crash mid-install leaves has_pending_join() True so start-up resumes properly.
    files_sorted = sorted(files, key=lambda pair: 1 if pair[1] == MASTER_DB_NAME else 0)

    installed = []
    for src_path, filename in files_sorted:
        dest_path = app_dir / filename
        _backup_db_and_sidecars(dest_path, ts)
        os.replace(src_path, dest_path)
        installed.append(filename)

    # Clean up pairing.json now that DBs are safely installed
    join_state = app_dir / "incoming" / JOIN_DIRNAME / PAIRING_STATE_FILE
    if join_state.exists():
        try:
            join_state.unlink(missing_ok=True)
        except OSError:
            pass

    return installed


def download_snapshot(
    app_dir: Path | str,
    host: str,
    admin_device_id: str,
    records: list,
    *,
    port: int = SYNC_PORT,
    timeout: float = SNAPSHOT_TIMEOUT_SECONDS,
    connect_timeout: float = CONNECT_TIMEOUT_SECONDS,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> list[str]:
    """Download the office snapshot over mutual TLS, verify integrity with DEK, and install via os.replace.

    Returns the list of installed database file names.
    Raises SnapshotVerificationError or SnapshotError on failure.
    """
    app_dir = Path(app_dir)
    office = sera_keys.load_office(app_dir)
    if office is None:
        raise SnapshotError("Office information (office.json) is missing on this workstation")

    dek = sera_keys.load_dek(app_dir)
    if not hmac.compare_digest(sera_keys.key_id(dek), office.key_id):
        raise SnapshotVerificationError("Stored office DEK does not match office.json key_id")

    identity = sync_identity.ensure_device_identity(app_dir)
    cert_chain = sync_identity.load_cert_chain_args(app_dir)

    # Filter records to member records only (join_res.records also contains office_admin record)
    member_records = [r for r in records if r.get("type") == "member" or ("cert_pem" in r)]
    members = MemberSet.from_records(member_records, own_cert_pem=identity.cert_pem)
    transport = SyncTransport(cert_chain, members)

    try:
        session = transport.connect(host, port, expected_device_id=admin_device_id, connect_timeout=connect_timeout)
    except TransportError as exc:
        raise SnapshotError(f"Can't establish mutual-TLS session to admin PC ({admin_device_id}) at {host}:{port}: {exc}") from None

    join_dir = app_dir / "incoming" / JOIN_DIRNAME
    join_dir.mkdir(parents=True, exist_ok=True)
    downloaded_files: list[tuple[Path, str]] = []

    try:
        # Request snapshot
        session.send({"t": FRAME_SNAPSHOT})

        # Wait for manifest (can take time while admin PC exports DB)
        manifest = session.recv(wait=timeout)
        if not isinstance(manifest, dict) or manifest.get("t") != FRAME_MANIFEST:
            err_msg = manifest.get("error") if isinstance(manifest, dict) else "unknown"
            raise SnapshotError(f"Admin PC returned invalid manifest or error: {err_msg}")

        # Verify office_id and key_id
        if manifest.get("office_id") != office.office_id:
            raise SnapshotVerificationError(
                f"Manifest office_id {manifest.get('office_id')!r} does not match office.json {office.office_id!r}"
            )
        if manifest.get("key_id") != office.key_id:
            raise SnapshotVerificationError(
                f"Manifest key_id {manifest.get('key_id')!r} does not match office.json {office.key_id!r}"
            )

        files_meta = manifest.get("files")
        if not isinstance(files_meta, list) or not files_meta:
            raise SnapshotVerificationError("Snapshot manifest contains no database files")

        # Download each file with per-chunk progress reporting
        for f in files_meta:
            if not isinstance(f, dict):
                raise SnapshotError("Invalid file entry in snapshot manifest")
            fname = f.get("name")
            size = f.get("size")
            expected_sha = f.get("sha256")

            if not isinstance(fname, str) or fname not in ALLOWED_DB_NAMES or Path(fname).name != fname:
                raise SnapshotError(f"Snapshot manifest contains invalid file name: {fname!r}")
            if type(size) is not int or size < 0:
                raise SnapshotError(f"Snapshot manifest contains invalid size for {fname}: {size}")
            if not isinstance(expected_sha, str) or len(expected_sha) != 64:
                raise SnapshotError(f"Snapshot manifest contains invalid SHA-256 for {fname}")

            target_path = join_dir / fname
            if target_path.exists():
                try:
                    target_path.unlink()
                except OSError:
                    pass

            if on_progress:
                on_progress(fname, 0, size)

            class _Sink:
                def __init__(self, f_out):
                    self.f_out = f_out
                    self.h = hashlib.sha256()

                def write(self, data):
                    self.f_out.write(data)
                    self.h.update(data)

            received = 0
            ok = False
            with open(target_path, "xb") as f_out:
                sink = _Sink(f_out)
                try:
                    while received < size:
                        chunk_frame = session.recv()
                        if chunk_frame.get("t") != sync_transport.FRAME_CHUNK:
                            raise SnapshotError(f"Expected chunk frame, got {chunk_frame.get('t')!r}")
                        n = chunk_frame.get("n", 0)
                        if n > size - received:
                            raise SnapshotError("More file data than announced")
                        if n == 0:
                            raise SnapshotError("Empty chunk in file transfer")
                        received += session.read_chunk(sink)
                        if on_progress:
                            on_progress(fname, received, size)
                    ok = True
                finally:
                    if not ok:
                        try:
                            target_path.unlink(missing_ok=True)
                        except OSError:
                            pass

            actual_sha = sink.h.hexdigest()
            if actual_sha != expected_sha:
                try:
                    target_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise SnapshotVerificationError(
                    f"SHA-256 mismatch for {fname}: got {actual_sha}, expected {expected_sha}"
                )

            downloaded_files.append((target_path, fname))

    finally:
        session.close()

    # Verify all downloaded databases with DEK before installing
    dek_hex = dek.hex()
    for fpath, fname in downloaded_files:
        try:
            _verify_downloaded_db(fpath, dek_hex)
        except Exception:
            # Clean up unverified files on failure
            for p, _ in downloaded_files:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
            raise

    # Install into app_dir via os.replace with automatic backup of pre-existing DBs
    installed = _install_downloaded_files(app_dir, downloaded_files)
    _log.info("Installed snapshot databases: %s", ", ".join(installed))
    return installed


def resume_join_snapshot(
    app_dir: Path | str,
    *,
    port: int = SYNC_PORT,
    timeout: float = SNAPSHOT_TIMEOUT_SECONDS,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> list[str]:
    """Resume an interrupted join by reading pairing.json, validating records, and downloading the snapshot."""
    import sync_admin
    app_dir = Path(app_dir)
    join_state_file = app_dir / "incoming" / JOIN_DIRNAME / PAIRING_STATE_FILE
    if not join_state_file.exists():
        raise SnapshotError(f"No pending join state found at {join_state_file}")

    office = sera_keys.load_office(app_dir)
    if office is None:
        raise SnapshotError("Office information (office.json) is missing on this workstation")

    try:
        state = json.loads(join_state_file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SnapshotError(f"Corrupt pairing state file {join_state_file}: {exc}") from None

    admin_device_id = state.get("admin_device_id")
    admin_address = state.get("admin_address")
    records = state.get("records")

    if not admin_device_id or not admin_address or not isinstance(records, list):
        raise SnapshotError("Pairing state file is missing required admin or member records")

    # Re-verify member records against office.admin_pubkey before using
    verified_records = []
    for r in records:
        try:
            rec = sync_admin.verify_record(r, office.admin_pubkey)
            verified_records.append(rec)
        except Exception as exc:
            raise SnapshotVerificationError(f"A member record in pairing.json failed verification: {exc}") from None

    return download_snapshot(
        app_dir,
        admin_address,
        admin_device_id,
        verified_records,
        port=port,
        timeout=timeout,
        on_progress=on_progress,
    )
