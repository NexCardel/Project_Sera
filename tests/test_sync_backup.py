"""Tests for sync_backup.py (Sera Sync v3, WP P4-3a): Scheduled local backups.

Requirements (§5 P4-3a):
- sqlcipher_export of both DBs daily at first idle after 13:00 into backups/daily-<date>/, keeping 14.
- Also back up right before any restore (P4-3b), migration (P1-4) or go-live swap (P3-9).
- Retire master.db.pre-sync-* rotation.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest
import sqlcipher3.dbapi2 as sqlite3

import sera_keys
import sync_backup
from sync_backup import (
    DailyBackupScheduler,
    backup_before_golive,
    backup_before_migration,
    backup_before_restore,
    backup_daily,
    check_and_run_daily_backup,
    create_backup,
    export_database,
    prune_daily_backups,
    retire_pre_sync_backups,
    should_run_daily_backup,
)

HEX_KEY = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _init_test_databases(app_dir: Path, hex_key: str = HEX_KEY) -> tuple[Path, Path]:
    master_path = app_dir / "master.db"
    raw_path = app_dir / "rawPayload.db"

    conn = sqlite3.connect(str(master_path))
    conn.execute(f"PRAGMA key = \"x'{hex_key}'\";")
    conn.execute("PRAGMA user_version = 10;")
    conn.execute("CREATE TABLE clients (id INTEGER PRIMARY KEY, name TEXT, pan TEXT);")
    conn.execute("INSERT INTO clients VALUES (1, 'Alice Corp', 'AAAAA1111A');")
    conn.execute("INSERT INTO clients VALUES (2, 'Bob Ltd', 'BBBBB2222B');")
    conn.commit()
    conn.close()

    conn_raw = sqlite3.connect(str(raw_path))
    conn_raw.execute(f"PRAGMA key = \"x'{hex_key}'\";")
    conn_raw.execute("PRAGMA user_version = 20;")
    conn_raw.execute("CREATE TABLE tracker_dump (id INTEGER PRIMARY KEY, status TEXT);")
    conn_raw.execute("INSERT INTO tracker_dump VALUES (1, 'submitted');")
    conn_raw.commit()
    conn_raw.close()

    return master_path, raw_path


def test_export_database_creates_consistent_standalone_db(tmp_path: Path):
    master_path, _ = _init_test_databases(tmp_path)
    dest_path = tmp_path / "exported_master.db"

    uv = export_database(master_path, dest_path, HEX_KEY)
    assert uv == 10
    assert dest_path.exists()

    conn = sqlite3.connect(str(dest_path))
    conn.execute(f"PRAGMA key = \"x'{HEX_KEY}'\";")
    res_uv = conn.execute("PRAGMA user_version;").fetchone()[0]
    assert res_uv == 10
    integrity = conn.execute("PRAGMA cipher_integrity_check;").fetchall()
    assert integrity == []
    quick = conn.execute("PRAGMA quick_check;").fetchall()
    assert [tuple(r) for r in quick] == [("ok",)]
    count = conn.execute("SELECT count(*) FROM clients;").fetchone()[0]
    assert count == 2
    conn.close()


def test_create_backup_exports_both_dbs(tmp_path: Path):
    _init_test_databases(tmp_path)

    backup_folder = create_backup(
        tmp_path,
        hex_key=HEX_KEY,
        prefix="test-backup-",
        reason="test_both_dbs",
    )
    assert backup_folder.exists()
    assert backup_folder.is_dir()
    assert (backup_folder / "master.db").exists()
    assert (backup_folder / "rawPayload.db").exists()

    # Manifest verification
    manifest_path = backup_folder / "backup_manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["reason"] == "test_both_dbs"
    file_names = {f["name"] for f in manifest["files"]}
    assert "master.db" in file_names
    assert "rawPayload.db" in file_names

    # Check that backup DBs open and have proper content
    for name in ("master.db", "rawPayload.db"):
        conn = sqlite3.connect(str(backup_folder / name))
        conn.execute(f"PRAGMA key = \"x'{HEX_KEY}'\";")
        assert conn.execute("PRAGMA cipher_integrity_check;").fetchall() == []
        conn.close()


def test_create_backup_handles_missing_raw_payload(tmp_path: Path):
    master_path = tmp_path / "master.db"
    conn = sqlite3.connect(str(master_path))
    conn.execute(f"PRAGMA key = \"x'{HEX_KEY}'\";")
    conn.execute("CREATE TABLE clients (id INTEGER PRIMARY KEY);")
    conn.commit()
    conn.close()

    backup_folder = create_backup(tmp_path, hex_key=HEX_KEY, prefix="master-only-")
    assert (backup_folder / "master.db").exists()
    assert not (backup_folder / "rawPayload.db").exists()

    manifest = json.loads((backup_folder / "backup_manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["files"]) == 1
    assert manifest["files"][0]["name"] == "master.db"


def test_prune_daily_backups_keeps_14(tmp_path: Path):
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)

    # Create 20 daily backups: daily-2026-09-01 .. daily-2026-09-20
    for day in range(1, 21):
        folder = backups_dir / f"daily-2026-09-{day:02d}"
        folder.mkdir()
        (folder / "master.db").write_text(f"dummy data {day}")

    # Also create non-daily backup folders that must NOT be pruned
    pre_restore = backups_dir / "pre-restore-20260901_100000"
    pre_restore.mkdir()
    pre_office = backups_dir / "pre-office-key-20260901_100000"
    pre_office.mkdir()

    pruned = prune_daily_backups(backups_dir, keep=14)
    assert len(pruned) == 6

    # The 6 oldest (01 to 06) should be pruned
    for day in range(1, 7):
        assert not (backups_dir / f"daily-2026-09-{day:02d}").exists()

    # The 14 newest (07 to 20) must still exist
    for day in range(7, 21):
        assert (backups_dir / f"daily-2026-09-{day:02d}").exists()

    # Non-daily backups must remain intact
    assert pre_restore.exists()
    assert pre_office.exists()


def test_should_run_daily_backup_schedule(tmp_path: Path):
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)

    # 1. Before 13:00 (e.g. 12:45) -> False
    dt_before = datetime.datetime(2026, 9, 25, 12, 45, 0)
    assert not should_run_daily_backup(tmp_path, hour_cutoff=13, now=dt_before)

    # 2. At or after 13:00 (e.g. 13:05) -> True
    dt_after = datetime.datetime(2026, 9, 25, 13, 5, 0)
    assert should_run_daily_backup(tmp_path, hour_cutoff=13, now=dt_after)

    # 3. If daily backup for today already exists -> False
    today_folder = backups_dir / "daily-2026-09-25"
    today_folder.mkdir()
    assert not should_run_daily_backup(tmp_path, hour_cutoff=13, now=dt_after)


def test_check_and_run_daily_backup_flow(tmp_path: Path):
    _init_test_databases(tmp_path)
    now = datetime.datetime(2026, 9, 25, 14, 0, 0)

    # Not idle -> should not run
    result_busy = check_and_run_daily_backup(
        tmp_path,
        hex_key=HEX_KEY,
        now=now,
        is_idle_callback=lambda: False,
    )
    assert result_busy is None

    # Idle -> runs backup
    result_idle = check_and_run_daily_backup(
        tmp_path,
        hex_key=HEX_KEY,
        now=now,
        is_idle_callback=lambda: True,
    )
    assert result_idle is not None
    assert result_idle.exists()
    assert result_idle.name == "daily-2026-09-25"

    # Already run today -> should not run again
    result_second = check_and_run_daily_backup(
        tmp_path,
        hex_key=HEX_KEY,
        now=now,
        is_idle_callback=lambda: True,
    )
    assert result_second is None


def test_backup_before_restore_migration_golive(tmp_path: Path):
    _init_test_databases(tmp_path)

    p_restore = backup_before_restore(tmp_path, hex_key=HEX_KEY)
    assert p_restore.exists()
    assert p_restore.name.startswith("pre-restore-")
    assert (p_restore / "master.db").exists()

    p_mig = backup_before_migration(tmp_path, hex_key=HEX_KEY)
    assert p_mig.exists()
    assert p_mig.name.startswith("pre-office-key-")
    assert (p_mig / "master.db").exists()

    p_golive = backup_before_golive(tmp_path, hex_key=HEX_KEY)
    assert p_golive.exists()
    assert p_golive.name.startswith("pre-golive-")
    assert (p_golive / "master.db").exists()


def test_retire_pre_sync_backups(tmp_path: Path):
    # Create legacy pre-sync backup files in app_dir
    f1 = tmp_path / "master.db.pre-sync-2026-08-29_100000.db"
    f2 = tmp_path / "sera.salt.pre-sync-2026-08-29_100000"
    f3 = tmp_path / "master.db-wal.pre-sync-2026-08-29_100000"
    f1.write_text("db copy")
    f2.write_text("salt copy")
    f3.write_text("wal copy")

    moved = retire_pre_sync_backups(tmp_path)
    assert len(moved) == 3

    # Ensure none remain in app_dir root
    assert not f1.exists()
    assert not f2.exists()
    assert not f3.exists()

    # Ensure they are safely preserved in backups/legacy-pre-sync/
    retired_dir = tmp_path / "backups" / "legacy-pre-sync"
    assert (retired_dir / f1.name).exists()
    assert (retired_dir / f2.name).exists()
    assert (retired_dir / f3.name).exists()


def test_scheduler_lifecycle(tmp_path: Path):
    _init_test_databases(tmp_path)
    scheduler = DailyBackupScheduler(
        tmp_path,
        hex_key=HEX_KEY,
        check_interval_seconds=0.1,
        is_idle_callback=lambda: True,
    )
    scheduler.start()
    assert scheduler.is_running
    scheduler.stop()
    assert not scheduler.is_running
