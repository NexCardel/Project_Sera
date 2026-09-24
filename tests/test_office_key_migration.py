"""Tests for WP P1-4: migrate a legacy PC to an office key (sync_migrate.py).

Every test builds a scratch legacy office in pytest's tmp_path (§0 rule 2).
Test data is invented (§0 rule 12).
"""

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest
import sqlcipher3.dbapi2 as sqlite3

import security
import sera_keys
import sync_migrate
from database import SeraDatabase

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")

LEGACY_PW = "OldOffice#2026"
NEW_PW = "NewOffice#2026"
PROBE_ROWS_MASTER = 57
PROBE_ROWS_RAW = 23
USER_VERSION = 7


def _open(path, hex_key):
    conn = sqlite3.connect(str(path))
    conn.execute(f"PRAGMA key = \"x'{hex_key}'\";")
    return conn


def _opens(path, hex_key) -> bool:
    conn = _open(path, hex_key)
    try:
        conn.execute("SELECT count(*) FROM sqlite_master;").fetchone()
        return True
    except sqlite3.DatabaseError:
        return False
    finally:
        conn.close()


def _sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _counts(path, hex_key) -> dict:
    conn = _open(path, hex_key)
    try:
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {n: conn.execute('SELECT count(*) FROM "%s"' % n.replace('"', '""')).fetchone()[0] for n in names}
    finally:
        conn.close()


@pytest.fixture
def legacy_office(tmp_path):
    """A legacy office: sera.salt + sera.key + master.db + rawPayload.db, with data in both DBs."""
    app = tmp_path / "office"
    app.mkdir()
    security.generate_and_save_salt(str(app / security.SALT_FILE))
    salt = security.load_salt(str(app / security.SALT_FILE))
    hex_key = security.derive_key_hex(LEGACY_PW, salt)
    (app / "sera.key").write_text(LEGACY_PW, encoding="utf-8")

    db = SeraDatabase(str(app / "master.db"), hex_key, defer_startup_maintenance=True)
    assert Path(db.raw_db_path).exists()
    del db

    for name, rows in (("master.db", PROBE_ROWS_MASTER), ("rawPayload.db", PROBE_ROWS_RAW)):
        conn = _open(app / name, hex_key)
        try:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("CREATE TABLE migr_probe (id INTEGER PRIMARY KEY AUTOINCREMENT, label TEXT, n INTEGER)")
            conn.executemany("INSERT INTO migr_probe (label, n) VALUES (?, ?)",
                             [("row-%d" % i, i * 3) for i in range(rows)])
            if name == "master.db":
                conn.execute("INSERT INTO app_settings (key, value) VALUES ('migr_probe_key', 'v1')")
                conn.execute(f"PRAGMA user_version = {USER_VERSION};")
            conn.commit()
        finally:
            conn.close()

    return {"app": app, "hex_key": hex_key}


def _legacy_file_hashes(app: Path) -> dict:
    return {n: _sha(app / n) for n in ("master.db", "rawPayload.db", security.SALT_FILE, "sera.key")}


def _assert_no_new_keys(app: Path):
    kdir = sera_keys.keys_dir(app)
    for name in (sera_keys.OFFICE_FILE, sera_keys.DEK_DPAPI_FILE, sera_keys.DEK_RECOVERY_FILE):
        assert not (kdir / name).exists(), name
    assert not (app / "incoming" / "migrate").exists()


# ------------------------------------------------------------------ Accept

@windows_only
def test_migrate_to_office_key(legacy_office):
    app, legacy_hex = legacy_office["app"], legacy_office["hex_key"]
    old_master_counts = _counts(app / "master.db", legacy_hex)
    old_raw_counts = _counts(app / "rawPayload.db", legacy_hex)

    result = sync_migrate.migrate_to_office_key(app, LEGACY_PW, "Test Office", new_password=NEW_PW)

    office = sera_keys.load_office(app)
    assert office is not None
    assert office.office_name == "Test Office"
    assert office.office_id == result.office_id
    assert office.key_id == result.key_id
    dek = sera_keys.load_dek(app)
    assert sera_keys.key_id(dek) == office.key_id
    new_hex = sera_keys.dek_hex(dek)

    for name, old_counts in (("master.db", old_master_counts), ("rawPayload.db", old_raw_counts)):
        assert _opens(app / name, new_hex), name
        assert not _opens(app / name, legacy_hex), name
        assert _counts(app / name, new_hex) == old_counts

    conn = _open(app / "master.db", new_hex)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == USER_VERSION
        assert conn.execute("SELECT value FROM app_settings WHERE key='migr_probe_key'").fetchone()[0] == "v1"
        assert conn.execute("SELECT sum(n) FROM migr_probe").fetchone()[0] == sum(i * 3 for i in range(PROBE_ROWS_MASTER))
    finally:
        conn.close()

    # sera.key / sera.salt moved (not deleted) into the backup folder with the old DBs.
    backup = Path(result.backup_dir)
    assert backup.parent == app / "backups"
    assert backup.name.startswith("pre-office-key-")
    assert not (app / "sera.key").exists()
    assert not (app / security.SALT_FILE).exists()
    assert (backup / "sera.key").read_text(encoding="utf-8") == LEGACY_PW
    old_salt = security.load_salt(str(backup / security.SALT_FILE))
    assert security.derive_key_hex(LEGACY_PW, old_salt) == legacy_hex
    assert _counts(backup / "master.db", legacy_hex) == old_master_counts
    assert _counts(backup / "rawPayload.db", legacy_hex) == old_raw_counts

    assert not (app / "incoming" / "migrate").exists()
    assert not sync_migrate.marker_path(app).exists()

    # Recovery blob is wrapped with the new master password.
    with pytest.raises(sera_keys.WrongPassword):
        sera_keys.recover_dek(app, LEGACY_PW)
    assert sera_keys.recover_dek(app, NEW_PW) == dek

    # The app's own DB layer opens it in office mode.
    db = SeraDatabase(str(app / "master.db"), new_hex, defer_startup_maintenance=True)
    assert db.get_setting("migr_probe_key") == "v1"


@windows_only
def test_migrate_rollback_on_verify_failure(legacy_office, monkeypatch):
    app = legacy_office["app"]
    before = _legacy_file_hashes(app)

    def _fail(*_a, **_k):
        raise sync_migrate.MigrationError("injected verification failure")
    monkeypatch.setattr(sync_migrate, "_verify_migrated_db", _fail)

    with pytest.raises(sync_migrate.MigrationError, match="injected"):
        sync_migrate.migrate_to_office_key(app, LEGACY_PW, "Test Office", new_password=NEW_PW)

    assert _legacy_file_hashes(app) == before
    _assert_no_new_keys(app)
    assert not sync_migrate.marker_path(app).exists()


# ------------------------------------------------------------------ extra

@windows_only
def test_migrate_keep_current_password(legacy_office):
    app = legacy_office["app"]
    sync_migrate.migrate_to_office_key(app, LEGACY_PW, "Test Office", new_password=None)
    dek = sera_keys.load_dek(app)
    assert sera_keys.recover_dek(app, LEGACY_PW) == dek


def test_migrate_refuses_to_keep_default_password(legacy_office, tmp_path):
    app = legacy_office["app"]
    before = _legacy_file_hashes(app)
    with pytest.raises(ValueError):
        sync_migrate.migrate_to_office_key(app, LEGACY_PW, "Test Office", new_password="admin123")
    with pytest.raises(ValueError):
        sync_migrate.migrate_to_office_key(app, LEGACY_PW, "Test Office", new_password="short")
    with pytest.raises(ValueError):
        sync_migrate.migrate_to_office_key(app, LEGACY_PW, "   ", new_password=NEW_PW)
    assert _legacy_file_hashes(app) == before
    assert not (app / "backups").exists()


def test_migrate_wrong_legacy_password_changes_nothing(legacy_office):
    app = legacy_office["app"]
    before = _legacy_file_hashes(app)
    with pytest.raises(sera_keys.WrongPassword):
        sync_migrate.migrate_to_office_key(app, "not-the-password", "Test Office", new_password=NEW_PW)
    assert _legacy_file_hashes(app) == before
    _assert_no_new_keys(app)
    assert not (app / "backups").exists()


@windows_only
def test_migrate_row_count_mismatch_stops_and_rolls_back(legacy_office, monkeypatch):
    app = legacy_office["app"]
    before = _legacy_file_hashes(app)
    real_counts = sync_migrate._table_counts

    def _lying_counts(path, hex_key):
        counts, uv = real_counts(path, hex_key)
        if Path(path).parent.name == "migrate" and "migr_probe" in counts:
            counts["migr_probe"] -= 1
        return counts, uv
    monkeypatch.setattr(sync_migrate, "_table_counts", _lying_counts)

    with pytest.raises(sync_migrate.RowCountMismatch) as exc:
        sync_migrate.migrate_to_office_key(app, LEGACY_PW, "Test Office", new_password=NEW_PW)
    assert "migr_probe" in str(exc.value)
    assert _legacy_file_hashes(app) == before
    _assert_no_new_keys(app)


@windows_only
def test_migrate_rollback_on_swap_failure_restores_backup(legacy_office, monkeypatch):
    """A failure in step 6 (after master.db was already swapped) restores from the backup folder."""
    app = legacy_office["app"]
    before = _legacy_file_hashes(app)
    real_replace = os.replace

    def _replace(src, dst):
        if Path(dst) == app / "rawPayload.db":
            raise PermissionError("injected: rawPayload.db is locked")
        return real_replace(src, dst)
    monkeypatch.setattr(sync_migrate.os, "replace", _replace)

    with pytest.raises(sync_migrate.MigrationError):
        sync_migrate.migrate_to_office_key(app, LEGACY_PW, "Test Office", new_password=NEW_PW)

    monkeypatch.setattr(sync_migrate.os, "replace", real_replace)
    assert _legacy_file_hashes(app) == before
    assert _opens(app / "master.db", legacy_office["hex_key"])
    _assert_no_new_keys(app)
    assert not sync_migrate.marker_path(app).exists()


@windows_only
def test_migrate_preexisting_stray_key_file_is_restored_on_rollback(legacy_office, monkeypatch):
    app = legacy_office["app"]
    kdir = sera_keys.keys_dir(app)
    kdir.mkdir()
    stray = kdir / sera_keys.DEK_DPAPI_FILE
    stray.write_bytes(b"stray bytes from an older attempt")

    monkeypatch.setattr(sync_migrate, "_verify_migrated_db",
                        lambda *a, **k: (_ for _ in ()).throw(sync_migrate.MigrationError("injected")))
    with pytest.raises(sync_migrate.MigrationError):
        sync_migrate.migrate_to_office_key(app, LEGACY_PW, "Test Office", new_password=NEW_PW)

    assert stray.read_bytes() == b"stray bytes from an older attempt"
    assert not (kdir / sera_keys.OFFICE_FILE).exists()
    assert not (kdir / sera_keys.DEK_RECOVERY_FILE).exists()


@windows_only
def test_interrupted_swap_is_rolled_forward_at_startup(legacy_office, monkeypatch):
    """Power loss after office.json was written but before step 6 finished: next start finishes it."""
    app = legacy_office["app"]
    old_counts = _counts(app / "master.db", legacy_office["hex_key"])

    class _Crash(BaseException):
        pass

    def _crash(*_a, **_k):
        raise _Crash()
    monkeypatch.setattr(sync_migrate, "_finish_swap", _crash)
    monkeypatch.setattr(sync_migrate, "_rollback_swap", _crash)   # a real crash runs no rollback
    with pytest.raises(_Crash):
        sync_migrate.migrate_to_office_key(app, LEGACY_PW, "Test Office", new_password=NEW_PW)
    monkeypatch.undo()

    assert sync_migrate.marker_path(app).exists()
    assert sera_keys.load_office(app) is not None
    assert _opens(app / "master.db", legacy_office["hex_key"])   # not swapped yet

    outcome = sync_migrate.resume_interrupted_migration(app)
    assert outcome == "completed"
    new_hex = sera_keys.dek_hex(sera_keys.load_dek(app))
    assert _counts(app / "master.db", new_hex) == old_counts
    assert _opens(app / "rawPayload.db", new_hex)
    assert not (app / "sera.key").exists()
    assert not (app / security.SALT_FILE).exists()
    assert not sync_migrate.marker_path(app).exists()
    assert not (app / "incoming" / "migrate").exists()


@windows_only
def test_interrupted_before_office_json_is_cleaned_up_at_startup(legacy_office, monkeypatch):
    """Crash in step 5 before office.json exists: legacy files untouched, leftovers cleaned."""
    app = legacy_office["app"]
    before = _legacy_file_hashes(app)

    class _Crash(BaseException):
        pass

    def _crash(*_a, **_k):
        raise _Crash()
    monkeypatch.setattr(sync_migrate.sera_keys, "save_office", _crash)
    monkeypatch.setattr(sync_migrate, "_rollback_prepare", _crash)
    with pytest.raises(_Crash):
        sync_migrate.migrate_to_office_key(app, LEGACY_PW, "Test Office", new_password=NEW_PW)
    monkeypatch.undo()

    assert sync_migrate.marker_path(app).exists()
    assert sync_migrate.resume_interrupted_migration(app) == "rolled_back"
    assert _legacy_file_hashes(app) == before
    _assert_no_new_keys(app)
    assert not sync_migrate.marker_path(app).exists()


def test_resume_without_marker_is_noop(tmp_path):
    assert sync_migrate.resume_interrupted_migration(tmp_path) is None


def test_migrate_request_file_round_trip(tmp_path):
    assert sync_migrate.read_migrate_request(tmp_path) is None
    path = sync_migrate.write_migrate_request(tmp_path, requested_by="TestAdmin")
    assert path == tmp_path / "incoming" / "migrate_request.json"
    req = sync_migrate.read_migrate_request(tmp_path)
    assert req["requested_by"] == "TestAdmin"
    sync_migrate.clear_migrate_request(tmp_path)
    assert sync_migrate.read_migrate_request(tmp_path) is None


def test_refuses_when_already_office_mode(legacy_office):
    app = legacy_office["app"]
    kdir = sera_keys.keys_dir(app)
    kdir.mkdir()
    (kdir / sera_keys.OFFICE_FILE).write_text("{}", encoding="utf-8")
    with pytest.raises(sync_migrate.MigrationError):
        sync_migrate.migrate_to_office_key(app, LEGACY_PW, "Test Office", new_password=NEW_PW)


def test_check_legacy_password(legacy_office):
    app = legacy_office["app"]
    assert sync_migrate.check_legacy_password(app, LEGACY_PW)
    assert not sync_migrate.check_legacy_password(app, "nope-nope")


def test_sync_migrate_does_not_import_pyside6():
    import re
    src = Path(sync_migrate.__file__).read_text(encoding="utf-8")
    assert not re.search(r"^\s*(from|import)\s+PySide6", src, re.M)


# ------------------------------------------------------------------ main.py wiring

@windows_only
def test_startup_runs_requested_migration_then_office_mode(legacy_office, monkeypatch):
    from main import SeraApp
    app = legacy_office["app"]
    monkeypatch.setattr("main.APP_DIR", app)
    sync_migrate.write_migrate_request(app, requested_by="TestAdmin")

    class Stub:
        _run_pending_office_key_migration = SeraApp._run_pending_office_key_migration
        _resolve_encryption_key = SeraApp._resolve_encryption_key

        def __init__(self):
            self.app_dir = app
            self.db_path = str(app / "master.db")
            self.salt_path = str(app / security.SALT_FILE)
            self.notices = []

        def _ask_office_key_migration_details(self, app_dir):
            return {"legacy_password": LEGACY_PW, "office_name": "Test Office", "new_password": NEW_PW}

        def _show_office_key_migration_message(self, level, title, text):
            self.notices.append((level, title))

    stub = Stub()
    stub._run_pending_office_key_migration()
    assert sync_migrate.read_migrate_request(app) is None
    assert stub.notices and stub.notices[-1][0] == "info"

    mode, kid, hex_key = stub._resolve_encryption_key()
    assert mode == "office"
    assert kid == sera_keys.load_office(app).key_id
    assert _opens(app / "master.db", hex_key)


def test_startup_migration_cancelled_stays_legacy(legacy_office, monkeypatch):
    from main import SeraApp
    app = legacy_office["app"]
    before = _legacy_file_hashes(app)
    sync_migrate.write_migrate_request(app)

    class Stub:
        _run_pending_office_key_migration = SeraApp._run_pending_office_key_migration

        def __init__(self):
            self.app_dir = app
            self.notices = []

        def _ask_office_key_migration_details(self, app_dir):
            return None

        def _show_office_key_migration_message(self, level, title, text):
            self.notices.append((level, title))

    Stub()._run_pending_office_key_migration()
    assert sync_migrate.read_migrate_request(app) is None   # consumed, no restart loop
    assert _legacy_file_hashes(app) == before
    _assert_no_new_keys(app)


# ------------------------------------------------------------------ UI smoke tests

def _sync_dialog(key_id, db_path):
    from unittest.mock import MagicMock
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog
    svc = MagicMock()
    svc.get_sync_state.return_value = {"status": "NORMAL"}
    svc.get_peers.return_value = []
    svc.get_activity_history.return_value = []
    svc.get_network_category.return_value = {"is_public": False}
    svc.inv_frames = False
    svc.key_id = key_id
    svc.db_path = str(db_path)
    return SeraSyncDialog(sync_service=svc, db=None, actor="TestAdmin"), svc


def test_convert_button_only_in_legacy_mode(tmp_path):
    legacy, _ = _sync_dialog(None, tmp_path / "master.db")
    office, _ = _sync_dialog("a" * 32, tmp_path / "master.db")
    assert not legacy.btn_office_key.isHidden()
    assert office.btn_office_key.isHidden()


def test_convert_button_writes_request_and_restarts(tmp_path):
    from unittest.mock import patch
    from PySide6.QtWidgets import QMessageBox
    dlg, svc = _sync_dialog(None, tmp_path / "master.db")
    with patch("PySide6.QtWidgets.QMessageBox.question", return_value=QMessageBox.Yes), \
         patch("version.restart_app") as restart:
        dlg._on_convert_to_office_key()
    assert sync_migrate.read_migrate_request(tmp_path)["requested_by"] == "TestAdmin"
    svc.stop.assert_called_once()
    restart.assert_called_once()


def test_convert_button_cancel_writes_nothing(tmp_path):
    from unittest.mock import patch
    from PySide6.QtWidgets import QMessageBox
    dlg, _ = _sync_dialog(None, tmp_path / "master.db")
    with patch("PySide6.QtWidgets.QMessageBox.question", return_value=QMessageBox.No), \
         patch("version.restart_app") as restart:
        dlg._on_convert_to_office_key()
    assert sync_migrate.read_migrate_request(tmp_path) is None
    restart.assert_not_called()


def test_migration_dialog_validation():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from ui.dialogs.office_key_migration_dialog import OfficeKeyMigrationDialog, MAX_ATTEMPTS

    dlg = OfficeKeyMigrationDialog(lambda pw: pw == LEGACY_PW)
    dlg.office_name.setText("  Test Office  ")
    dlg.current_pw.setText("wrong-one")
    dlg._on_accept()
    assert dlg.details is None and "Wrong password" in dlg.error.text()

    dlg.current_pw.setText(LEGACY_PW)
    dlg.keep_pw.setChecked(False)
    dlg.new_pw.setText("admin123")
    dlg.new_pw_confirm.setText("admin123")
    dlg._on_accept()
    assert dlg.details is None

    dlg.new_pw.setText(NEW_PW)
    dlg.new_pw_confirm.setText(NEW_PW + "x")
    dlg._on_accept()
    assert dlg.details is None and "match" in dlg.error.text()

    dlg.new_pw_confirm.setText(NEW_PW)
    dlg._on_accept()
    assert dlg.details == {"legacy_password": LEGACY_PW, "office_name": "Test Office", "new_password": NEW_PW}

    keep = OfficeKeyMigrationDialog(lambda pw: True)
    keep.office_name.setText("Office")
    keep.current_pw.setText("admin123")
    keep._on_accept()
    assert keep.details is None and not keep.keep_pw.isChecked()

    locked = OfficeKeyMigrationDialog(lambda pw: False)
    locked.office_name.setText("Office")
    for _ in range(MAX_ATTEMPTS):
        locked.current_pw.setText("x")
        locked._on_accept()
    assert locked.result() == 0 and locked.details is None


# ------------------------------------------------------------------ review fixes

@windows_only
def test_rollforward_refuses_when_new_files_are_missing(legacy_office, monkeypatch):
    """Review finding 1: office.json + marker left, but the new files are gone. Roll-forward must
    not take 'missing' as 'already replaced' and move the legacy key away."""
    app = legacy_office["app"]
    before = _legacy_file_hashes(app)

    class _Crash(BaseException):
        pass

    def _crash(*_a, **_k):
        raise _Crash()
    monkeypatch.setattr(sync_migrate, "_finish_swap", _crash)
    monkeypatch.setattr(sync_migrate, "_rollback_swap", _crash)
    with pytest.raises(_Crash):
        sync_migrate.migrate_to_office_key(app, LEGACY_PW, "Test Office", new_password=NEW_PW)
    monkeypatch.undo()

    import shutil
    shutil.rmtree(app / "incoming" / "migrate")
    with pytest.raises(sync_migrate.MigrationError, match="doesn't open with the office key"):
        sync_migrate.resume_interrupted_migration(app)

    assert _legacy_file_hashes(app) == before          # sera.key / sera.salt not moved
    assert sync_migrate.marker_path(app).exists()      # left for a person to look at


@windows_only
def test_rollback_removes_keys_before_new_files(legacy_office, monkeypatch):
    """Review finding 1: a crash inside the rollback, between the two removals, must leave
    no office.json, so the next start rolls back instead of forward."""
    app = legacy_office["app"]
    before = _legacy_file_hashes(app)
    real_replace = os.replace

    class _Crash(BaseException):
        pass

    def _replace(src, dst):
        if Path(dst) == app / "rawPayload.db":
            raise PermissionError("injected: rawPayload.db is locked")
        return real_replace(src, dst)

    def _crash(*_a, **_k):
        raise _Crash()
    monkeypatch.setattr(sync_migrate.os, "replace", _replace)
    monkeypatch.setattr(sync_migrate, "_remove_tree", _crash)
    with pytest.raises(_Crash):
        sync_migrate.migrate_to_office_key(app, LEGACY_PW, "Test Office", new_password=NEW_PW)
    monkeypatch.undo()

    assert sera_keys.load_office(app) is None
    assert sync_migrate.resume_interrupted_migration(app) == "rolled_back"
    assert _legacy_file_hashes(app) == before
    _assert_no_new_keys(app)


def test_step2_failure_keeps_stale_migrate_folder(legacy_office, monkeypatch):
    """Review finding 2: a leftover incoming/migrate/ from an earlier crash is renamed, never deleted."""
    app = legacy_office["app"]
    stale = app / "incoming" / "migrate"
    stale.mkdir(parents=True)
    (stale / "master.db").write_bytes(b"leftover from a crashed run")

    def _fail(*_a, **_k):
        raise sync_migrate.MigrationError("injected checkpoint failure")
    monkeypatch.setattr(sync_migrate, "_checkpoint", _fail)
    with pytest.raises(sync_migrate.MigrationError, match="injected"):
        sync_migrate.migrate_to_office_key(app, LEGACY_PW, "Test Office", new_password=NEW_PW)

    kept = list((app / "incoming").glob("migrate.stale-*"))
    assert len(kept) == 1
    assert (kept[0] / "master.db").read_bytes() == b"leftover from a crashed run"


def test_check_legacy_password_raises_when_it_cannot_check(legacy_office, monkeypatch):
    """Review finding 3: a locked DB is not a wrong password."""
    def _locked(*_a, **_k):
        raise sync_migrate.MigrationError("master.db is in use by another program")
    monkeypatch.setattr(sync_migrate, "_legacy_hex_key", _locked)
    with pytest.raises(sync_migrate.MigrationError):
        sync_migrate.check_legacy_password(legacy_office["app"], LEGACY_PW)


def test_migration_dialog_locked_db_does_not_use_an_attempt():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from ui.dialogs.office_key_migration_dialog import OfficeKeyMigrationDialog, MAX_ATTEMPTS

    def _locked(_pw):
        raise sync_migrate.MigrationError("master.db is in use by another program")
    dlg = OfficeKeyMigrationDialog(_locked)
    dlg.office_name.setText("Office")
    for _ in range(MAX_ATTEMPTS + 2):
        dlg.current_pw.setText(LEGACY_PW)
        dlg._on_accept()
    assert dlg._attempts == 0
    assert "in use" in dlg.error.text()
    assert dlg.result() == 0 and dlg.details is None


# ------------------------------------------------------------------ second review round

@windows_only
def test_rollforward_refuses_when_a_live_db_is_missing(legacy_office, monkeypatch):
    """A missing live DB must not pass the check (connecting would create an empty file)."""
    app = legacy_office["app"]

    class _Crash(BaseException):
        pass

    def _crash(*_a, **_k):
        raise _Crash()
    monkeypatch.setattr(sync_migrate, "_finish_swap", _crash)
    monkeypatch.setattr(sync_migrate, "_rollback_swap", _crash)
    with pytest.raises(_Crash):
        sync_migrate.migrate_to_office_key(app, LEGACY_PW, "Test Office", new_password=NEW_PW)
    monkeypatch.undo()

    # rawPayload.db and its new copy both gone (deleted by hand).
    (app / "incoming" / "migrate" / "rawPayload.db").unlink()
    (app / "rawPayload.db").unlink()
    with pytest.raises(sync_migrate.MigrationError, match="rawPayload.db is missing"):
        sync_migrate.resume_interrupted_migration(app)

    assert not (app / "rawPayload.db").exists()        # no empty file created
    assert (app / "sera.key").exists() and (app / security.SALT_FILE).exists()
    assert sync_migrate.marker_path(app).exists()


@windows_only
def test_crash_during_swap_rollback_is_finished_as_rollback(legacy_office, monkeypatch):
    """A crash part-way through a step-6 rollback must be finished as a rollback at the next
    start, not rolled forward (which would leave the two DBs on different keys)."""
    app = legacy_office["app"]
    before = _legacy_file_hashes(app)
    real_replace = os.replace
    real_restore = sync_migrate._restore_file
    real_write_marker = sync_migrate._write_marker

    class _Crash(BaseException):
        pass

    def _replace(src, dst):
        if Path(dst) == app / "rawPayload.db":
            raise PermissionError("injected: rawPayload.db is locked")
        return real_replace(src, dst)

    state = {"crashed": False}

    def _restore_then_crash(backup, live):
        real_restore(backup, live)          # master.db is put back ...
        state["crashed"] = True
        raise _Crash()                      # ... then the power goes

    def _write_marker(app_, marker):
        if state["crashed"]:
            raise _Crash()                  # nothing more runs after a power cut
        real_write_marker(app_, marker)

    monkeypatch.setattr(sync_migrate.os, "replace", _replace)
    monkeypatch.setattr(sync_migrate, "_restore_file", _restore_then_crash)
    monkeypatch.setattr(sync_migrate, "_write_marker", _write_marker)
    with pytest.raises(_Crash):
        sync_migrate.migrate_to_office_key(app, LEGACY_PW, "Test Office", new_password=NEW_PW)
    monkeypatch.undo()

    assert sera_keys.load_office(app) is not None       # office.json still there
    assert (app / "incoming" / "migrate" / "rawPayload.db").exists()
    assert json.loads(sync_migrate.marker_path(app).read_text(encoding="utf-8"))["phase"] == "rolling_back"

    assert sync_migrate.resume_interrupted_migration(app) == "rolled_back"
    assert _legacy_file_hashes(app) == before
    assert _opens(app / "master.db", legacy_office["hex_key"])
    assert _opens(app / "rawPayload.db", legacy_office["hex_key"])
    _assert_no_new_keys(app)
    assert not sync_migrate.marker_path(app).exists()
