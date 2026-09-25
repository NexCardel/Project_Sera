"""sync_backup.py
--------------
Scheduled local backups and snapshot management for Sera Sync v3 (WP P4-3a).

Requirements (§5 P4-3a):
- sqlcipher_export of both DBs daily at first idle after 13:00 into backups/daily-<date>/, keeping 14.
- Also back up right before any restore (P4-3b), migration (P1-4) or go-live swap (P3-9).
- Retire master.db.pre-sync-* rotation.

No PySide6 imports here (§0 rule 7).
"""

from __future__ import annotations

import contextlib
import ctypes
import datetime
import glob
import hashlib
import json
import logging
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Callable

import sqlcipher3.dbapi2 as sqlite3

_log = logging.getLogger("sera.sync.backup")

BACKUPS_DIRNAME = "backups"
MASTER_DB_NAME = "master.db"
RAW_DB_NAME = "rawPayload.db"
SALT_NAME = "sera.salt"
LEGACY_KEY_NAME = "sera.key"
MANIFEST_NAME = "backup_manifest.json"
DEFAULT_DAILY_KEEP = 14
DEFAULT_HOUR_CUTOFF = 13


class BackupError(Exception):
    """Base exception for backup operations."""


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def export_database(src_path: Path, dest_path: Path, hex_key: str) -> int:
    """Creates a consistent snapshot of src_path at dest_path using sqlcipher_export in BEGIN IMMEDIATE."""
    if dest_path.exists():
        raise BackupError(f"Destination database already exists: {dest_path}")

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(src_path))
    try:
        conn.execute(f"PRAGMA key = \"x'{hex_key}'\";")
        conn.execute("BEGIN IMMEDIATE;")
        try:
            uv_res = conn.execute("PRAGMA user_version;").fetchone()
            user_version = int(uv_res[0]) if uv_res and uv_res[0] is not None else 0

            escaped_dest = str(dest_path).replace("'", "''")
            conn.execute(f"ATTACH DATABASE '{escaped_dest}' AS snap KEY \"x'{hex_key}'\";")
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


def _resolve_hex_key(app_dir: Path, hex_key: str | None = None, db=None) -> str:
    if hex_key:
        return hex_key
    if db is not None and getattr(db, "hex_key", None):
        return str(db.hex_key)

    import sera_keys
    dek = sera_keys.load_dek(app_dir)
    if dek is not None:
        return sera_keys.dek_hex(dek)

    # Legacy fallback if available
    salt_file = app_dir / SALT_NAME
    if salt_file.exists():
        import security
        salt_bytes = security.load_salt(str(salt_file))
        # Check default master password if known
        default_pw = "admin123"
        return security.derive_key_hex(default_pw, salt_bytes)

    raise BackupError("Could not resolve encryption key for database backup")


def create_backup(
    app_dir: Path | str,
    dest_dir: Path | str | None = None,
    *,
    prefix: str = "manual-",
    reason: str = "manual",
    hex_key: str | None = None,
    db=None,
) -> Path:
    """Creates a local backup of both databases into dest_dir or backups/<prefix><ts>/."""
    app_path = Path(app_dir).resolve()
    key_hex = _resolve_hex_key(app_path, hex_key=hex_key, db=db)

    now = datetime.datetime.now()
    if dest_dir is None:
        ts = now.strftime("%Y%m%d_%H%M%S")
        target_dir = app_path / BACKUPS_DIRNAME / f"{prefix}{ts}"
    else:
        target_dir = Path(dest_dir).resolve()

    target_dir.mkdir(parents=True, exist_ok=True)

    master_src = app_path / MASTER_DB_NAME
    raw_src = app_path / RAW_DB_NAME

    if not master_src.exists():
        raise BackupError(f"Master database not found at {master_src}")

    exported_files: list[dict] = []

    # 1. Export master.db
    master_dst = target_dir / MASTER_DB_NAME
    uv_master = export_database(master_src, master_dst, key_hex)
    exported_files.append({
        "name": MASTER_DB_NAME,
        "size": master_dst.stat().st_size,
        "sha256": _sha256_file(master_dst),
        "user_version": uv_master,
    })

    # 2. Export rawPayload.db if present and non-empty
    if raw_src.exists() and raw_src.stat().st_size > 0:
        raw_dst = target_dir / RAW_DB_NAME
        uv_raw = export_database(raw_src, raw_dst, key_hex)
        exported_files.append({
            "name": RAW_DB_NAME,
            "size": raw_dst.stat().st_size,
            "sha256": _sha256_file(raw_dst),
            "user_version": uv_raw,
        })

    # 3. Preserve legacy salt and key if present
    for extra in (SALT_NAME, LEGACY_KEY_NAME):
        extra_src = app_path / extra
        if extra_src.exists():
            extra_dst = target_dir / extra
            shutil.copy2(extra_src, extra_dst)
            exported_files.append({
                "name": extra,
                "size": extra_dst.stat().st_size,
                "sha256": _sha256_file(extra_dst),
            })

    # 4. Write backup manifest
    manifest = {
        "backup_name": target_dir.name,
        "created_at": now.isoformat(timespec="seconds"),
        "reason": reason,
        "files": exported_files,
    }
    manifest_path = target_dir / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    _log.info("Created backup at %s (reason: %s, %d files)", target_dir, reason, len(exported_files))
    return target_dir


def prune_daily_backups(backups_dir: Path | str, keep: int = DEFAULT_DAILY_KEEP) -> list[Path]:
    """Retains the newest `keep` daily-* backup directories and purges older ones (FIFO).

    Returns the list of removed directories. Non-daily backups are never touched.
    """
    backups_path = Path(backups_dir).resolve()
    if not backups_path.exists():
        return []

    daily_dirs = [d for d in backups_path.iterdir() if d.is_dir() and d.name.startswith("daily-")]
    # Alphabetical sorting works for ISO dates (daily-YYYY-MM-DD or daily-YYYYMMDD)
    daily_dirs.sort(key=lambda d: d.name)

    pruned: list[Path] = []
    if len(daily_dirs) > keep:
        to_remove = daily_dirs[: len(daily_dirs) - keep]
        for d in to_remove:
            try:
                shutil.rmtree(d)
                pruned.append(d)
                _log.info("Pruned old daily backup: %s", d.name)
            except OSError as exc:
                _log.warning("Could not prune backup %s: %exc", d, exc)

    return pruned


def backup_daily(
    app_dir: Path | str,
    *,
    date_str: str | None = None,
    hex_key: str | None = None,
    db=None,
    keep: int = DEFAULT_DAILY_KEEP,
) -> Path:
    """Creates a daily backup in backups/daily-<date>/ and prunes older ones keeping 14."""
    app_path = Path(app_dir).resolve()
    if date_str is None:
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")

    backups_dir = app_path / BACKUPS_DIRNAME
    dest_dir = backups_dir / f"daily-{date_str}"

    backup_folder = create_backup(
        app_path,
        dest_dir=dest_dir,
        reason="daily",
        hex_key=hex_key,
        db=db,
    )
    prune_daily_backups(backups_dir, keep=keep)
    return backup_folder


def backup_before_restore(app_dir: Path | str, *, hex_key: str | None = None, db=None) -> Path:
    """Safety backup created right before any restore operation (P4-3b)."""
    return create_backup(app_dir, prefix="pre-restore-", reason="pre_restore", hex_key=hex_key, db=db)


def backup_before_migration(app_dir: Path | str, *, hex_key: str | None = None, db=None) -> Path:
    """Safety backup created right before an office-key migration (P1-4)."""
    return create_backup(app_dir, prefix="pre-office-key-", reason="pre_migration", hex_key=hex_key, db=db)


def backup_before_golive(app_dir: Path | str, *, hex_key: str | None = None, db=None) -> Path:
    """Safety backup created right before go-live replica swap (P3-9)."""
    return create_backup(app_dir, prefix="pre-golive-", reason="pre_golive", hex_key=hex_key, db=db)


def retire_pre_sync_backups(app_dir: Path | str, dest_folder_name: str = "legacy-pre-sync") -> list[Path]:
    """Retires legacy master.db.pre-sync-* files by moving them into backups/legacy-pre-sync/.

    Satisfies §0 rule 3 (never delete a DB or salt) while removing pre-sync clutter from app_dir.
    """
    app_path = Path(app_dir).resolve()
    retired_dir = app_path / BACKUPS_DIRNAME / dest_folder_name
    patterns = [
        "master.db.pre-sync-*",
        "sera.salt.pre-sync-*",
        "master.db-wal.pre-sync-*",
        "master.db-shm.pre-sync-*",
        "rawPayload.db.pre-sync-*",
    ]

    moved: list[Path] = []
    for pat in patterns:
        for file_path_str in glob.glob(str(app_path / pat)):
            file_path = Path(file_path_str)
            if not file_path.is_file():
                continue
            retired_dir.mkdir(parents=True, exist_ok=True)
            target = retired_dir / file_path.name
            if target.exists():
                target = retired_dir / f"{file_path.name}_{int(time.time())}"
            try:
                os.replace(file_path, target)
                moved.append(target)
                _log.info("Retired pre-sync backup file %s -> %s", file_path.name, target)
            except OSError as exc:
                _log.warning("Could not retire %s: %s", file_path, exc)

    return moved


def is_system_idle(idle_threshold_seconds: float = 60.0) -> bool:
    """Checks whether the system has been input-idle for at least idle_threshold_seconds."""
    if sys.platform != "win32":
        return True

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    try:
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
            idle_seconds = millis / 1000.0
            return idle_seconds >= idle_threshold_seconds
    except Exception:
        pass
    return True


def should_run_daily_backup(
    app_dir: Path | str,
    *,
    hour_cutoff: int = DEFAULT_HOUR_CUTOFF,
    now: datetime.datetime | None = None,
) -> bool:
    """Returns True if local time is >= hour_cutoff (13:00) and no daily backup exists for today."""
    if now is None:
        now = datetime.datetime.now()

    if now.hour < hour_cutoff:
        return False

    app_path = Path(app_dir).resolve()
    backups_dir = app_path / BACKUPS_DIRNAME
    today_str = now.strftime("%Y-%m-%d")
    today_backup = backups_dir / f"daily-{today_str}"

    return not today_backup.exists()


def check_and_run_daily_backup(
    app_dir: Path | str,
    *,
    hour_cutoff: int = DEFAULT_HOUR_CUTOFF,
    idle_threshold_seconds: float = 60.0,
    is_idle_callback: Callable[[], bool] | None = None,
    now: datetime.datetime | None = None,
    hex_key: str | None = None,
    db=None,
    keep: int = DEFAULT_DAILY_KEEP,
) -> Path | None:
    """Checks timing and idle state, then performs daily backup if appropriate.

    Returns the backup folder Path if executed, or None if skipped.
    """
    if now is None:
        now = datetime.datetime.now()

    if not should_run_daily_backup(app_dir, hour_cutoff=hour_cutoff, now=now):
        return None

    # Determine idle state
    idle = False
    if is_idle_callback is not None:
        try:
            idle = bool(is_idle_callback())
        except Exception as exc:
            _log.warning("is_idle_callback failed: %s", exc)
            idle = False
    else:
        idle = is_system_idle(idle_threshold_seconds)

    if not idle:
        return None

    date_str = now.strftime("%Y-%m-%d")
    return backup_daily(app_dir, date_str=date_str, hex_key=hex_key, db=db, keep=keep)


class DailyBackupScheduler:
    """Background scheduler that periodically checks and runs daily backup at first idle after 13:00."""

    def __init__(
        self,
        app_dir: Path | str,
        *,
        db=None,
        hex_key: str | None = None,
        hour_cutoff: int = DEFAULT_HOUR_CUTOFF,
        check_interval_seconds: float = 60.0,
        idle_threshold_seconds: float = 60.0,
        is_idle_callback: Callable[[], bool] | None = None,
        keep: int = DEFAULT_DAILY_KEEP,
    ):
        self.app_dir = Path(app_dir).resolve()
        self.db = db
        self.hex_key = hex_key
        self.hour_cutoff = hour_cutoff
        self.check_interval_seconds = check_interval_seconds
        self.idle_threshold_seconds = idle_threshold_seconds
        self.is_idle_callback = is_idle_callback
        self.keep = keep

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="SeraDailyBackupScheduler", daemon=True)
        self._thread.start()
        _log.info("DailyBackupScheduler started (cutoff %02d:00, check interval %.1f s)", self.hour_cutoff, self.check_interval_seconds)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        _log.info("DailyBackupScheduler stopped")

    def check_now(self) -> Path | None:
        """Immediate check and run if conditions are met."""
        try:
            return check_and_run_daily_backup(
                self.app_dir,
                hour_cutoff=self.hour_cutoff,
                idle_threshold_seconds=self.idle_threshold_seconds,
                is_idle_callback=self.is_idle_callback,
                hex_key=self.hex_key,
                db=self.db,
                keep=self.keep,
            )
        except Exception as exc:
            _log.exception("Error in check_and_run_daily_backup: %s", exc)
            return None

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self.check_now()
            self._stop_event.wait(self.check_interval_seconds)
