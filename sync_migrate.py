"""
sync_migrate.py
---------------
WP P1-4: convert this PC from the legacy password+salt key to an office key (DEK).

Runs at start-up, after ``apply_pending_swap`` and before any database is opened.
All or nothing (blueprint §5 P1-4):

  2. checkpoint + back up master.db, rawPayload.db, sera.salt, sera.key
     into ``backups/pre-office-key-<ts>/``
  3. ``sqlcipher_export`` each DB into ``incoming/migrate/<name>`` keyed with a new DEK
  4. verify each new file (opens, cipher_integrity_check, quick_check, per-table counts)
  5. ``store_dek`` + ``save_office`` (office.json is written last: it is the office-mode switch)
  6. ``os.replace`` the new DBs over the old ones; move sera.key / sera.salt into the backup

A marker file (``incoming/migrate_state.json``) is written before step 5 so a crash
(power loss) is finished or undone on the next start by ``resume_interrupted_migration``:
office.json present -> roll forward (the DEK is stored, the new files are verified);
office.json absent -> roll back (legacy files were never touched).

Never logs passwords or keys. No PySide6 here (§0 rule 7).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import sera_keys

MASTER_DB = "master.db"
RAW_DB = "rawPayload.db"
LEGACY_KEY_FILE = "sera.key"
REQUEST_FILE = "migrate_request.json"
MARKER_FILE = "migrate_state.json"
MIGRATE_DIRNAME = "migrate"
BACKUP_PREFIX = "pre-office-key-"
PREEXISTING_KEYS_DIR = "keys-preexisting"
REPLACED_SIDECARS_DIR = "replaced-sidecars"
SIDECARS = ("-wal", "-shm", "-journal")
KEY_FILES = (sera_keys.OFFICE_FILE, sera_keys.DEK_DPAPI_FILE, sera_keys.DEK_RECOVERY_FILE)
DEFAULT_PASSWORD = "admin123"
MIN_PASSWORD_LEN = 8
MAX_OFFICE_NAME_LEN = 100
_RETRY_SEC = 2.0


class MigrationError(Exception):
    """The migration failed; unless it's MigrationRollbackFailed, the PC is back in legacy mode."""


class RowCountMismatch(MigrationError):
    """A migrated file doesn't hold the same rows as the original. Stop and ask (§5 P1-4)."""


class MigrationRollbackFailed(MigrationError):
    """The files could not be put back automatically. The backup folder has the originals."""


@dataclass
class MigrationResult:
    office_id: str
    key_id: str
    backup_dir: str


# ---------------------------------------------------------------- paths / request file

def _incoming(app: Path) -> Path:
    return app / "incoming"


def request_path(app_dir) -> Path:
    return _incoming(Path(app_dir)) / REQUEST_FILE


def marker_path(app_dir) -> Path:
    return _incoming(Path(app_dir)) / MARKER_FILE


def _migrate_dir(app: Path) -> Path:
    return _incoming(app) / MIGRATE_DIRNAME


def write_migrate_request(app_dir, requested_by: str | None = None) -> Path:
    path = request_path(app_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"requested_at": datetime.now().isoformat(timespec="seconds"), "requested_by": requested_by}
    sera_keys.atomic_write(path, (json.dumps(data) + "\n").encode("utf-8"))
    return path


def read_migrate_request(app_dir) -> dict | None:
    path = request_path(app_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    return data if isinstance(data, dict) else {}


def clear_migrate_request(app_dir) -> None:
    try:
        request_path(app_dir).unlink()
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------- validation

def validate_new_password(password: str) -> str | None:
    """Error text, or None if ``password`` is acceptable as the office master password."""
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LEN:
        return "The master password must be at least %d characters long." % MIN_PASSWORD_LEN
    if password.lower() == DEFAULT_PASSWORD:
        return "The default password 'admin123' can't be the office master password."
    return None


def _validate_inputs(office_name: str, legacy_password: str, new_password: str | None) -> tuple[str, str]:
    name = (office_name or "").strip()
    if not name:
        raise ValueError("Enter an office name.")
    if len(name) > MAX_OFFICE_NAME_LEN:
        raise ValueError("The office name is too long (max %d characters)." % MAX_OFFICE_NAME_LEN)
    if new_password is None:
        if not legacy_password or legacy_password.lower() == DEFAULT_PASSWORD:
            raise ValueError("The current password is the default 'admin123'. Choose a new master password.")
        return name, legacy_password
    err = validate_new_password(new_password)
    if err:
        raise ValueError(err)
    return name, new_password


# ---------------------------------------------------------------- sqlcipher helpers

def _connect(path: Path, hex_key: str):
    import sqlcipher3.dbapi2 as sqlite3
    conn = sqlite3.connect(str(path))
    conn.execute(f"PRAGMA key = \"x'{hex_key}'\";")
    return conn


def _legacy_hex_key(app: Path, password: str) -> str:
    """Derive the legacy key and prove it opens master.db. Raises WrongPassword / MigrationError."""
    import sqlcipher3.dbapi2 as sqlite3
    import security
    master = app / MASTER_DB
    salt = app / security.SALT_FILE
    if not master.is_file():
        raise MigrationError("master.db not found in %s" % app)
    if not salt.is_file():
        raise MigrationError("%s not found in %s" % (security.SALT_FILE, app))
    if not password:
        raise sera_keys.WrongPassword("wrong master password")
    hex_key = security.derive_key_hex(password, security.load_salt(str(salt)))
    conn = _connect(master, hex_key)
    try:
        conn.execute("SELECT count(*) FROM sqlite_master;").fetchone()
    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower() or "busy" in str(e).lower():
            raise MigrationError("master.db is in use by another program") from None
        raise sera_keys.WrongPassword("wrong master password") from None
    except sqlite3.DatabaseError:
        raise sera_keys.WrongPassword("wrong master password") from None
    finally:
        conn.close()
    return hex_key


def check_legacy_password(app_dir, password: str) -> bool:
    """False only for a wrong password. Raises MigrationError if it can't be checked (locked, file missing)."""
    try:
        _legacy_hex_key(Path(app_dir), password)
        return True
    except sera_keys.WrongPassword:
        return False


def _checkpoint(path: Path, hex_key: str) -> None:
    conn = _connect(path, hex_key)
    try:
        row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE);").fetchone()
    finally:
        conn.close()
    if row and row[0]:
        raise MigrationError("%s is in use; its write-ahead log could not be checkpointed" % path.name)


def _table_counts(path, hex_key: str) -> tuple[dict, int]:
    conn = _connect(Path(path), hex_key)
    try:
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        counts = {n: conn.execute('SELECT count(*) FROM "%s"' % n.replace('"', '""')).fetchone()[0] for n in names}
        user_version = conn.execute("PRAGMA user_version;").fetchone()[0]
        return counts, int(user_version or 0)
    finally:
        conn.close()


def _export(src: Path, src_hex: str, dest: Path, dest_hex: str) -> None:
    if dest.exists():
        raise MigrationError("%s already exists" % dest)
    conn = _connect(src, src_hex)
    try:
        user_version = int(conn.execute("PRAGMA user_version;").fetchone()[0] or 0)
        escaped = str(dest).replace("'", "''")
        conn.execute(f"ATTACH DATABASE '{escaped}' AS mig KEY \"x'{dest_hex}'\";")
        try:
            conn.execute("SELECT sqlcipher_export('mig');")
            conn.execute(f"PRAGMA mig.user_version = {user_version};")
        finally:
            conn.execute("DETACH DATABASE mig;")
    finally:
        conn.close()


def _verify_migrated_db(old_path: Path, old_hex: str, new_path: Path, new_hex: str) -> None:
    import sqlcipher3.dbapi2 as sqlite3
    conn = _connect(new_path, new_hex)
    try:
        try:
            conn.execute("SELECT count(*) FROM sqlite_master;").fetchone()
        except sqlite3.DatabaseError:
            raise MigrationError("%s does not open with the new office key" % new_path.name) from None
        integrity = conn.execute("PRAGMA cipher_integrity_check;").fetchall()
        if integrity:
            raise MigrationError("%s failed cipher_integrity_check (%d problems)" % (new_path.name, len(integrity)))
        quick = conn.execute("PRAGMA quick_check;").fetchall()
        if [tuple(r) for r in quick] != [("ok",)]:
            raise MigrationError("%s failed quick_check" % new_path.name)
    finally:
        conn.close()

    old_counts, old_uv = _table_counts(old_path, old_hex)
    new_counts, new_uv = _table_counts(new_path, new_hex)
    if old_counts != new_counts:
        diffs = []
        for name in sorted(set(old_counts) | set(new_counts)):
            if old_counts.get(name) != new_counts.get(name):
                diffs.append("%s: %s -> %s" % (name, old_counts.get(name, "missing"), new_counts.get(name, "missing")))
        raise RowCountMismatch("%s: row counts differ after export (%s)" % (old_path.name, "; ".join(diffs)))
    if old_uv != new_uv:
        raise MigrationError("%s: user_version %d -> %d after export" % (old_path.name, old_uv, new_uv))


# ---------------------------------------------------------------- file helpers

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _retry(op):
    deadline = time.monotonic() + _RETRY_SEC
    while True:
        try:
            return op()
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


def _unique_path(path: Path) -> Path:
    candidate, n = path, 1
    while candidate.exists():
        candidate = path.with_name("%s_%d" % (path.name, n))
        n += 1
    return candidate


def _copy_verified(src: Path, dest: Path) -> None:
    shutil.copy2(str(src), str(dest))
    if _sha256(src) != _sha256(dest):
        raise MigrationError("backup copy of %s does not match the original" % src.name)


def _restore_file(backup: Path, live: Path) -> None:
    """Copy ``backup`` over ``live`` via a temp file + os.replace. The backup stays."""
    tmp = live.with_name(live.name + ".restore-tmp")
    shutil.copy2(str(backup), str(tmp))
    try:
        _retry(lambda: os.replace(str(tmp), str(live)))
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _remove_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(str(path))


def _write_marker(app: Path, marker: dict) -> None:
    path = marker_path(app)
    path.parent.mkdir(parents=True, exist_ok=True)
    sera_keys.atomic_write(path, (json.dumps(marker, indent=2) + "\n").encode("utf-8"))


def _read_marker(app: Path) -> dict | None:
    path = marker_path(app)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise MigrationError("%s is damaged; check the backups folder before continuing" % path) from None
    if not isinstance(data, dict) or not isinstance(data.get("dbs"), list) or not data.get("backup_dir"):
        raise MigrationError("%s is damaged; check the backups folder before continuing" % path)
    for name in data["dbs"]:
        if name not in (MASTER_DB, RAW_DB):
            raise MigrationError("%s names an unexpected file" % path)
    return data


def _marker_backup_dir(app: Path, marker: dict) -> Path:
    backup = Path(marker["backup_dir"])
    if backup.parent.resolve() != (app / "backups").resolve() or not backup.name.startswith(BACKUP_PREFIX):
        raise MigrationError("%s points outside the backups folder" % marker_path(app))
    return backup


# ---------------------------------------------------------------- rollback / roll-forward

def _undo_keys(app: Path, backup_dir: Path, moved_aside: list) -> None:
    """Remove the key files this run created and put any pre-existing ones back.

    ``moved_aside`` lists the files that existed before the run. If the move aside
    hasn't happened yet, the file in keys/ is still the original and is left alone.
    """
    kdir = sera_keys.keys_dir(app)
    aside_dir = backup_dir / PREEXISTING_KEYS_DIR
    for name in KEY_FILES:
        live = kdir / name
        aside = aside_dir / name
        if name in moved_aside and not aside.exists():
            continue  # never moved: the file in keys/ is still the original
        if live.exists():
            live.unlink()
        if name in moved_aside and aside.exists():
            os.replace(str(aside), str(live))


def _rollback_prepare(app: Path, backup_dir: Path, moved_aside: list) -> None:
    """Failure in steps 1-5: legacy files were never touched.

    Keys go first: a crash in between must never leave office.json without the new files,
    or the next start would roll forward onto the legacy databases.
    """
    _undo_keys(app, backup_dir, moved_aside)
    _remove_tree(_migrate_dir(app))
    try:
        marker_path(app).unlink()
    except FileNotFoundError:
        pass


def _check_live_dbs_open(app: Path, marker: dict, new_hex: str | None) -> None:
    import sqlcipher3.dbapi2 as sqlite3
    if new_hex is None:
        try:
            dek = sera_keys.load_dek(app)
        except sera_keys.KeyUnavailable as e:
            raise MigrationError("can't check the converted files: %s" % e) from None
        if sera_keys.key_id(dek) != marker.get("key_id"):
            raise MigrationError("the stored office key doesn't match the conversion; not finishing")
        new_hex = sera_keys.dek_hex(dek)
    for name in marker["dbs"]:
        path = app / name
        # Connecting would create a missing file, and an empty file "opens" with any key.
        if not path.is_file() or path.stat().st_size == 0:
            raise MigrationError("%s is missing or empty; not finishing the conversion" % name)
        conn = _connect(path, new_hex)
        try:
            conn.execute("SELECT count(*) FROM sqlite_master;").fetchone()
        except sqlite3.DatabaseError:
            raise MigrationError("%s doesn't open with the office key; not finishing the conversion" % name) from None
        finally:
            conn.close()


def _finish_swap(app: Path, marker: dict, new_hex: str | None = None) -> None:
    """Step 6. Idempotent, so it also completes an interrupted run.

    ``new_hex`` None (resume): the DEK is loaded from keys/ to check the result.
    """
    import security
    backup_dir = _marker_backup_dir(app, marker)
    mdir = _migrate_dir(app)
    for name in marker["dbs"]:
        src = mdir / name
        if not src.exists():
            continue  # already replaced
        live = app / name
        for ext in SIDECARS:
            sidecar = app / (name + ext)
            if sidecar.exists():
                dest_dir = backup_dir / REPLACED_SIDECARS_DIR
                dest_dir.mkdir(exist_ok=True)
                _retry(lambda: os.replace(str(sidecar), str(_unique_path(dest_dir / sidecar.name))))
        _retry(lambda: os.replace(str(src), str(live)))
    # A missing new file is taken as "already replaced"; prove it before the legacy key goes away.
    _check_live_dbs_open(app, marker, new_hex)
    for name in (LEGACY_KEY_FILE, security.SALT_FILE):
        live = app / name
        if live.exists():
            # The verified copy is already in the backup folder; this moves the original onto it.
            _retry(lambda: os.replace(str(live), str(backup_dir / name)))
    try:
        mdir.rmdir()
    except OSError:
        pass
    marker_path(app).unlink()


def _rollback_swap(app: Path, marker: dict, legacy_hex: str | None) -> None:
    """Failure in step 6: restore the originals from the backup folder."""
    import security
    backup_dir = _marker_backup_dir(app, marker)
    names = list(marker["dbs"]) + [security.SALT_FILE] + ([LEGACY_KEY_FILE] if marker.get("had_sera_key") else [])
    try:
        # Saved before restoring anything, so a crash from here on is finished as a rollback
        # at the next start, never rolled forward over half-restored files.
        marker["phase"] = "rolling_back"
        _write_marker(app, marker)
        for name in names:
            backup, live = backup_dir / name, app / name
            if not live.exists() or _sha256(live) != _sha256(backup):
                for ext in SIDECARS:
                    sidecar = app / (name + ext)
                    if sidecar.exists():
                        dest_dir = backup_dir / REPLACED_SIDECARS_DIR
                        dest_dir.mkdir(exist_ok=True)
                        os.replace(str(sidecar), str(_unique_path(dest_dir / ("new-" + sidecar.name))))
                _restore_file(backup, live)
        for name in names:
            if _sha256(app / name) != _sha256(backup_dir / name):
                raise MigrationError("%s does not match its backup after restore" % name)
        if legacy_hex is not None:
            conn = _connect(app / MASTER_DB, legacy_hex)
            try:
                conn.execute("SELECT count(*) FROM sqlite_master;").fetchone()
            finally:
                conn.close()
    except BaseException as e:
        marker["phase"] = "rollback_failed"
        try:
            _write_marker(app, marker)
        except Exception:
            pass
        raise MigrationRollbackFailed(
            "The original files could not be put back automatically (%s). "
            "The office key was kept so nothing is lost. The originals are in %s." % (e, backup_dir)
        ) from e
    # Originals are back and verified: the new key protects nothing live any more.
    _rollback_prepare(app, backup_dir, marker.get("moved_aside", []))


def resume_interrupted_migration(app_dir) -> str | None:
    """Finish or undo a migration that was interrupted (crash / power loss).

    Returns None (nothing to do), "completed" or "rolled_back". Raises MigrationError
    if the state needs a person to look at it.
    """
    app = Path(app_dir)
    marker = _read_marker(app)
    if marker is None:
        return None
    backup_dir = _marker_backup_dir(app, marker)
    if marker.get("phase") == "rollback_failed":
        raise MigrationRollbackFailed(
            "An earlier office-key conversion could not be undone. The originals are in %s." % backup_dir)
    if marker.get("phase") == "rolling_back":
        # Idempotent: files already restored match their backup and are skipped.
        _rollback_swap(app, marker, None)
        return "rolled_back"
    office = sera_keys.load_office(app)
    if office is None:
        _rollback_prepare(app, backup_dir, marker.get("moved_aside", []))
        return "rolled_back"
    if office.key_id != marker.get("key_id"):
        raise MigrationError("keys/office.json doesn't match the interrupted conversion; not touching anything")
    if not (sera_keys.keys_dir(app) / sera_keys.DEK_RECOVERY_FILE).exists():
        raise MigrationError("the office key recovery file is missing; not touching anything")
    _finish_swap(app, marker)
    return "completed"


# ---------------------------------------------------------------- main entry point

def migrate_to_office_key(app_dir, legacy_password: str, office_name: str,
                          new_password: str | None = None) -> MigrationResult:
    """Convert this PC to an office key. ``new_password=None`` keeps the current password.

    Raises ValueError (bad input, nothing touched), sera_keys.WrongPassword,
    RowCountMismatch, MigrationRollbackFailed or MigrationError. On any of these
    except MigrationRollbackFailed the PC is left in legacy mode with its files unchanged.
    """
    import security
    app = Path(app_dir)
    if (sera_keys.keys_dir(app) / sera_keys.OFFICE_FILE).exists():
        raise MigrationError("This PC already uses an office key.")
    if marker_path(app).exists():
        raise MigrationError("An earlier conversion was interrupted; restart Sera to finish it first.")
    office_name, recovery_password = _validate_inputs(office_name, legacy_password, new_password)

    # Step 1: the current password must open master.db (and rawPayload.db, if present).
    legacy_hex = _legacy_hex_key(app, legacy_password)
    dbs = [MASTER_DB] + ([RAW_DB] if (app / RAW_DB).is_file() else [])
    if RAW_DB in dbs:
        try:
            _table_counts(app / RAW_DB, legacy_hex)
        except Exception:
            raise MigrationError("rawPayload.db doesn't open with the current master password") from None

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    mdir = _migrate_dir(app)
    # A leftover from an earlier crash (no marker) is renamed aside, before anything that
    # can roll back and remove incoming/migrate/.
    if mdir.exists():
        os.replace(str(mdir), str(_unique_path(_incoming(app) / ("migrate.stale-" + ts))))
    backup_dir = _unique_path(app / "backups" / (BACKUP_PREFIX + ts))
    backup_dir.mkdir(parents=True)
    kdir = sera_keys.keys_dir(app)
    # Key files that exist before this run (stray leftovers) are never deleted, only moved aside.
    moved_aside = [n for n in KEY_FILES if (kdir / n).exists()]

    try:
        # Step 2: checkpoint, then back up.
        for name in dbs:
            _checkpoint(app / name, legacy_hex)
        to_back_up = dbs + [security.SALT_FILE] + ([LEGACY_KEY_FILE] if (app / LEGACY_KEY_FILE).exists() else [])
        for name in dbs:
            to_back_up += [name + ext for ext in SIDECARS if (app / (name + ext)).exists()]
        for name in to_back_up:
            _copy_verified(app / name, backup_dir / name)

        # Step 3: export with the new key.
        mdir.mkdir(parents=True)
        dek = sera_keys.new_dek()
        new_hex = sera_keys.dek_hex(dek)
        for name in dbs:
            _export(app / name, legacy_hex, mdir / name, new_hex)

        # Step 4: verify.
        for name in dbs:
            _verify_migrated_db(app / name, legacy_hex, mdir / name, new_hex)

        # Step 5: store the key. The marker comes first so a crash from here on is recoverable.
        office_id = sera_keys.OfficeInfo.new_office_id()
        kid = sera_keys.key_id(dek)
        marker = {
            "format": 1,
            "phase": "keys",
            "key_id": kid,
            "backup_dir": str(backup_dir),
            "dbs": dbs,
            "had_sera_key": LEGACY_KEY_FILE in to_back_up,
            "moved_aside": moved_aside,
        }
        _write_marker(app, marker)
        if moved_aside:
            (backup_dir / PREEXISTING_KEYS_DIR).mkdir()
            for name in moved_aside:
                os.replace(str(kdir / name), str(backup_dir / PREEXISTING_KEYS_DIR / name))
        sera_keys.store_dek(app, dek, recovery_password, office_id)
        if sera_keys.load_dek(app) != dek:
            raise MigrationError("the stored office key could not be read back")
        sera_keys.save_office(app, sera_keys.OfficeInfo(office_id=office_id, office_name=office_name, key_id=kid))
    except BaseException:
        _rollback_prepare(app, backup_dir, moved_aside)
        raise

    # Step 6: swap.
    marker["phase"] = "swap"
    try:
        _write_marker(app, marker)
        _finish_swap(app, marker, new_hex)
    except BaseException as e:
        _rollback_swap(app, marker, legacy_hex)
        if isinstance(e, MigrationError):
            raise
        raise MigrationError("could not replace the database files (%s)" % e) from e

    return MigrationResult(office_id=office_id, key_id=kid, backup_dir=str(backup_dir))
