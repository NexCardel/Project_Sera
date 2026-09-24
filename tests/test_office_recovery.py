"""
Tests for office recovery, recovery kit export/restore, and master password change
(Sera Sync v3, WP P1-6).

All tests use pytest's tmp_path fixture; the real data folder is never touched.
No client private data is used.
"""

import ast
import json
import os
import pathlib
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

import sera_keys
from sera_keys import (
    KeyFileInvalid,
    KeyUnavailable,
    OfficeInfo,
    WrongPassword,
)

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")

OFFICE_ID = "3f2a9c1e-0000-4000-8000-000000000001"
OFFICE_NAME = "Test Accounting Office"
INITIAL_PW = "initial-secure-password"
NEW_PW = "new-secure-password-2026"


def _make_office(app_dir, dek=None, password=INITIAL_PW, with_admin=False):
    if dek is None:
        dek = sera_keys.new_dek()
    info = OfficeInfo(
        office_id=OFFICE_ID,
        office_name=OFFICE_NAME,
        key_id=sera_keys.key_id(dek),
    )
    sera_keys.save_office(app_dir, info)
    sera_keys.store_dek(app_dir, dek, password, OFFICE_ID)
    if with_admin:
        kdir = sera_keys.keys_dir(app_dir)
        admin_secret = os.urandom(32)
        admin_blob = sera_keys.wrap_with_password(
            admin_secret, password, sera_keys.admin_aad(OFFICE_ID)
        )
        (kdir / sera_keys.ADMIN_KEY_RECOVERY_FILE).write_text(
            json.dumps(admin_blob, indent=2) + "\n", encoding="utf-8"
        )
    return dek, info


# ---------------------------------------------------------------- password validation

def test_validate_master_password():
    assert sera_keys.validate_master_password(None) is not None
    assert sera_keys.validate_master_password("") is not None
    assert sera_keys.validate_master_password("short") is not None
    assert sera_keys.validate_master_password("1234567") is not None
    assert sera_keys.validate_master_password("admin123") is not None
    assert sera_keys.validate_master_password("ADMIN123") is not None
    assert sera_keys.validate_master_password("  admin123  ") is not None
    assert sera_keys.validate_master_password(INITIAL_PW) is None
    assert sera_keys.validate_master_password("8characters") is None


# ---------------------------------------------------------------- change master password

def test_change_master_password_success(tmp_path):
    dek, info = _make_office(tmp_path, password=INITIAL_PW)

    sera_keys.change_master_password(tmp_path, INITIAL_PW, NEW_PW)

    # New password can recover the DEK
    assert sera_keys.recover_dek(tmp_path, NEW_PW) == dek

    # Old password no longer works
    with pytest.raises(WrongPassword):
        sera_keys.recover_dek(tmp_path, INITIAL_PW)

    # office.json key_id is unchanged
    assert sera_keys.load_office(tmp_path).key_id == info.key_id


def test_change_master_password_with_admin_key(tmp_path):
    dek, info = _make_office(tmp_path, password=INITIAL_PW, with_admin=True)

    sera_keys.change_master_password(tmp_path, INITIAL_PW, NEW_PW)

    # DEK recovers with new password
    assert sera_keys.recover_dek(tmp_path, NEW_PW) == dek

    # Admin key recovery file exists and unwraps with new password
    kdir = sera_keys.keys_dir(tmp_path)
    admin_path = kdir / sera_keys.ADMIN_KEY_RECOVERY_FILE
    assert admin_path.exists()
    admin_blob = json.loads(admin_path.read_text(encoding="utf-8"))
    unwrapped_admin = sera_keys.unwrap_with_password(
        admin_blob, NEW_PW, sera_keys.admin_aad(OFFICE_ID)
    )
    assert len(unwrapped_admin) == 32

    # Old password cannot unwrap admin key
    with pytest.raises(WrongPassword):
        sera_keys.unwrap_with_password(
            admin_blob, INITIAL_PW, sera_keys.admin_aad(OFFICE_ID)
        )


def test_change_master_password_wrong_old_password(tmp_path):
    dek, _ = _make_office(tmp_path, password=INITIAL_PW)

    with pytest.raises(WrongPassword):
        sera_keys.change_master_password(tmp_path, "wrong-password", NEW_PW)

    # Original password still recovers the DEK
    assert sera_keys.recover_dek(tmp_path, INITIAL_PW) == dek


def test_change_master_password_invalid_new_password(tmp_path):
    _make_office(tmp_path, password=INITIAL_PW)

    with pytest.raises(ValueError, match="at least 8 characters"):
        sera_keys.change_master_password(tmp_path, INITIAL_PW, "short")

    with pytest.raises(ValueError, match="default password"):
        sera_keys.change_master_password(tmp_path, INITIAL_PW, "admin123")


def test_change_master_password_preserves_old_recovery_as_backup(tmp_path):
    _make_office(tmp_path, password=INITIAL_PW)

    sera_keys.change_master_password(tmp_path, INITIAL_PW, NEW_PW)

    kdir = sera_keys.keys_dir(tmp_path)
    backups = [p for p in kdir.iterdir() if p.name.startswith("office_key.recovery.bak-")]
    assert len(backups) == 1

    # Owner decision (2026-09-24): old recovery backup is kept and can be opened by old password
    old_blob = json.loads(backups[0].read_text(encoding="utf-8"))
    assert (
        sera_keys.unwrap_with_password(
            old_blob, INITIAL_PW, sera_keys.recovery_aad(OFFICE_ID)
        )
        is not None
    )


def test_change_master_password_missing_office_raises_key_unavailable(tmp_path):
    with pytest.raises(KeyUnavailable):
        sera_keys.change_master_password(tmp_path, INITIAL_PW, NEW_PW)


# ---------------------------------------------------------------- recovery kit export

def test_export_recovery_kit_success(tmp_path):
    dek, info = _make_office(tmp_path, password=INITIAL_PW)
    kit_dest = tmp_path / "exports" / "office.serakit"

    exported_path = sera_keys.export_recovery_kit(tmp_path, INITIAL_PW, kit_dest)
    assert exported_path == kit_dest
    assert kit_dest.exists()

    data = json.loads(kit_dest.read_text(encoding="utf-8"))
    assert data["format"] == sera_keys.RECOVERY_KIT_FORMAT
    assert data["office"]["office_id"] == info.office_id
    assert data["office"]["key_id"] == info.key_id
    assert "office_key_recovery" in data
    assert data["admin_key_recovery"] is None


def test_export_recovery_kit_with_admin_key(tmp_path):
    dek, info = _make_office(tmp_path, password=INITIAL_PW, with_admin=True)
    kit_dest = tmp_path / "exports" / "office_with_admin.serakit"

    sera_keys.export_recovery_kit(tmp_path, INITIAL_PW, kit_dest)
    data = json.loads(kit_dest.read_text(encoding="utf-8"))
    assert data["admin_key_recovery"] is not None
    assert data["admin_key_recovery"]["kdf"] == "argon2id"


def test_export_recovery_kit_wrong_password_fails(tmp_path):
    _make_office(tmp_path, password=INITIAL_PW)
    kit_dest = tmp_path / "exports" / "should_not_exist.serakit"

    with pytest.raises(WrongPassword):
        sera_keys.export_recovery_kit(tmp_path, "incorrect-pass", kit_dest)
    assert not kit_dest.exists()


def test_export_recovery_kit_missing_office_raises_key_unavailable(tmp_path):
    kit_dest = tmp_path / "office.serakit"
    with pytest.raises(KeyUnavailable):
        sera_keys.export_recovery_kit(tmp_path, INITIAL_PW, kit_dest)


# ---------------------------------------------------------------- inspect & restore kit

def test_inspect_recovery_kit_success(tmp_path):
    _, info = _make_office(tmp_path, password=INITIAL_PW, with_admin=True)
    kit_dest = tmp_path / "office.serakit"
    sera_keys.export_recovery_kit(tmp_path, INITIAL_PW, kit_dest)

    meta = sera_keys.inspect_recovery_kit(kit_dest)
    assert meta["office_id"] == info.office_id
    assert meta["office_name"] == info.office_name
    assert meta["key_id"] == info.key_id
    assert meta["has_admin_key"] is True


def test_inspect_recovery_kit_invalid_format(tmp_path):
    bad_kit = tmp_path / "bad.serakit"
    bad_kit.write_text(json.dumps({"format": "unknown-v99", "office": {}}), encoding="utf-8")
    with pytest.raises(KeyFileInvalid):
        sera_keys.inspect_recovery_kit(bad_kit)


def test_restore_recovery_kit_to_fresh_dir(tmp_path):
    dek, info = _make_office(tmp_path, password=INITIAL_PW, with_admin=True)
    kit_dest = tmp_path / "office.serakit"
    sera_keys.export_recovery_kit(tmp_path, INITIAL_PW, kit_dest)

    target_app_dir = tmp_path / "new_workstation"
    recovered_dek = sera_keys.restore_recovery_kit(target_app_dir, kit_dest, INITIAL_PW)
    assert recovered_dek == dek

    # Keys are restored
    assert (target_app_dir / "keys" / sera_keys.OFFICE_FILE).exists()
    assert (target_app_dir / "keys" / sera_keys.DEK_RECOVERY_FILE).exists()
    assert (target_app_dir / "keys" / sera_keys.ADMIN_KEY_RECOVERY_FILE).exists()

    restored_office = sera_keys.load_office(target_app_dir)
    assert restored_office.office_id == info.office_id
    assert restored_office.key_id == info.key_id

    # recover_dek works on the restored directory
    assert sera_keys.recover_dek(target_app_dir, INITIAL_PW) == dek


def test_restore_recovery_kit_wrong_password(tmp_path):
    _make_office(tmp_path, password=INITIAL_PW)
    kit_dest = tmp_path / "office.serakit"
    sera_keys.export_recovery_kit(tmp_path, INITIAL_PW, kit_dest)

    target_app_dir = tmp_path / "new_workstation"
    with pytest.raises(WrongPassword):
        sera_keys.restore_recovery_kit(target_app_dir, kit_dest, "wrong-password")


def test_restore_recovery_kit_rejects_foreign_office(tmp_path):
    dek_a, info_a = _make_office(tmp_path / "office_a", password=INITIAL_PW)
    kit_a = tmp_path / "office_a.serakit"
    sera_keys.export_recovery_kit(tmp_path / "office_a", INITIAL_PW, kit_a)

    office_b_dir = tmp_path / "office_b"
    dek_b, info_b = _make_office(office_b_dir, password="office-b-password")

    # Attempt to restore Kit A into Office B directory -> must raise KeyFileInvalid
    with pytest.raises(KeyFileInvalid) as exc_info:
        sera_keys.restore_recovery_kit(office_b_dir, kit_a, INITIAL_PW)
    assert "belongs to a different office" in str(exc_info.value)

    # Office B files must be untouched
    assert sera_keys.load_office(office_b_dir).office_id == info_b.office_id
    assert sera_keys.load_office(office_b_dir).key_id == info_b.key_id
    kdir = sera_keys.keys_dir(office_b_dir)
    assert not any(p.name.startswith("office.json.bak-") for p in kdir.iterdir())


def test_restore_recovery_kit_same_office_backs_up_existing_files(tmp_path):
    office_dir = tmp_path / "office"
    dek, info = _make_office(office_dir, password=INITIAL_PW)
    kit_path = tmp_path / "office.serakit"
    sera_keys.export_recovery_kit(office_dir, INITIAL_PW, kit_path)

    # Overwrite recovery file with modified data so that replacement creates a backup
    kdir = sera_keys.keys_dir(office_dir)
    recovery_file = kdir / sera_keys.DEK_RECOVERY_FILE
    dummy_blob = {"format": "dummy", "ct": "dummy"}
    recovery_file.write_text(json.dumps(dummy_blob), encoding="utf-8")

    # Restore the kit into the same office
    restored_dek = sera_keys.restore_recovery_kit(office_dir, kit_path, INITIAL_PW)
    assert restored_dek == dek

    # Existing modified file was backed up (§0 rule 3)
    recovery_backups = [p for p in kdir.iterdir() if p.name.startswith("office_key.recovery.bak-")]
    assert len(recovery_backups) >= 1
    assert sera_keys.load_office(office_dir).office_id == info.office_id


# ---------------------------------------------------------------- DPAPI failure handling

def test_recover_dek_handles_dpapi_failure_gracefully(tmp_path):
    dek, _ = _make_office(tmp_path, password=INITIAL_PW)

    # Simulate DPAPI failing (e.g. non-Windows, or Windows user profile restriction)
    with patch("sera_keys.dpapi_protect", side_effect=OSError("DPAPI write failed")):
        recovered = sera_keys.recover_dek(tmp_path, INITIAL_PW)
        # DEK is still safely returned to caller
        assert recovered == dek


# ---------------------------------------------------------------- module imports rule

def test_sera_keys_does_not_import_pyside6():
    tree = ast.parse(pathlib.Path(sera_keys.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "PySide6" not in imported


# ---------------------------------------------------------------- UI dialogs & flows

@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_office_recovery_dialog_unlock_success(tmp_path, qapp):
    from ui.dialogs.office_recovery_dialog import OfficeRecoveryDialog

    dek, _ = _make_office(tmp_path, password=INITIAL_PW)
    dlg = OfficeRecoveryDialog(tmp_path)
    dlg.pw_edit.setText(INITIAL_PW)
    dlg._on_unlock()

    assert dlg.result() == 1  # Accepted
    assert dlg.recovered_dek == dek


def test_office_recovery_dialog_wrong_password_counts_attempts(tmp_path, qapp, monkeypatch):
    from ui.dialogs.office_recovery_dialog import OfficeRecoveryDialog

    _make_office(tmp_path, password=INITIAL_PW)
    dlg = OfficeRecoveryDialog(tmp_path)

    # 4 wrong attempts
    for attempt in range(1, 5):
        dlg.pw_edit.setText(f"wrong-{attempt}")
        dlg._on_unlock()
        assert dlg.result() == 0  # not accepted
        assert f"{5 - attempt} attempts remaining" in dlg.lbl_error.text()

    # 5th wrong attempt triggers critical error and rejects
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.critical", lambda *args: None)
    dlg.pw_edit.setText("wrong-5")
    dlg._on_unlock()
    assert dlg.result() == 0  # Rejected


def test_office_recovery_dialog_restore_kit(tmp_path, qapp, monkeypatch):
    from ui.dialogs.office_recovery_dialog import OfficeRecoveryDialog

    dek, info = _make_office(tmp_path / "office_a", password=INITIAL_PW)
    kit_file = tmp_path / "office_a.serakit"
    sera_keys.export_recovery_kit(tmp_path / "office_a", INITIAL_PW, kit_file)

    target_dir = tmp_path / "office_target"
    dlg = OfficeRecoveryDialog(target_dir)

    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getOpenFileName", lambda *args, **kwargs: (str(kit_file), ""))
    monkeypatch.setattr("PySide6.QtWidgets.QInputDialog.getText", lambda *args, **kwargs: (INITIAL_PW, True))
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.information", lambda *args: None)

    dlg._on_restore_kit()
    assert dlg.result() == 1  # Accepted
    assert dlg.recovered_dek == dek


def test_change_master_password_dialog(tmp_path, qapp, monkeypatch):
    from ui.dialogs.change_master_password_dialog import ChangeMasterPasswordDialog

    dek, _ = _make_office(tmp_path, password=INITIAL_PW)
    dlg = ChangeMasterPasswordDialog(tmp_path)

    # Validation failure: mismatch
    dlg.current_pw.setText(INITIAL_PW)
    dlg.new_pw.setText(NEW_PW)
    dlg.confirm_pw.setText("mismatched-pass")
    dlg._on_save()
    assert dlg.result() == 0
    assert "do not match" in dlg.lbl_error.text()

    # Successful change
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.information", lambda *args: None)
    dlg.confirm_pw.setText(NEW_PW)
    dlg._on_save()
    assert dlg.result() == 1  # Accepted

    # Verify password was changed
    assert sera_keys.recover_dek(tmp_path, NEW_PW) == dek


def test_export_recovery_kit_flow(tmp_path, qapp, monkeypatch):
    from ui.dialogs.change_master_password_dialog import export_recovery_kit_flow

    _make_office(tmp_path, password=INITIAL_PW)
    dest_file = tmp_path / "exported.serakit"

    monkeypatch.setattr("PySide6.QtWidgets.QInputDialog.getText", lambda *args, **kwargs: (INITIAL_PW, True))
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getSaveFileName", lambda *args, **kwargs: (str(dest_file), ""))
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.information", lambda *args: None)

    ok = export_recovery_kit_flow(None, tmp_path)
    assert ok is True
    assert dest_file.exists()


def test_main_startup_missing_office_db_prompt(tmp_path, qapp, monkeypatch):
    from main import SeraApp

    dek, info = _make_office(tmp_path, password=INITIAL_PW)
    hex_key = sera_keys.dek_hex(dek)
    db_path = str(tmp_path / "master.db")
    assert not os.path.exists(db_path)

    class StubApp:
        _handle_missing_office_db = SeraApp._handle_missing_office_db

    app = StubApp()
    # When user clicks No on restore prompt, startup aborts (sys.exit)
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.critical", lambda *args, **kwargs: 65536)  # QMessageBox.No
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.information", lambda *args, **kwargs: None)

    with pytest.raises(SystemExit):
        app._handle_missing_office_db(tmp_path, db_path, hex_key)

    assert not os.path.exists(db_path), "master.db must not be created when aborted"


def test_main_startup_missing_office_db_restore(tmp_path, qapp, monkeypatch):
    import sqlcipher3.dbapi2 as sqlite3
    from main import SeraApp

    dek, info = _make_office(tmp_path, password=INITIAL_PW)
    hex_key = sera_keys.dek_hex(dek)
    db_path = str(tmp_path / "master.db")

    # Create a real encrypted backup database file
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir(parents=True)
    backup_file = backups_dir / "master.db.bak"
    conn = sqlite3.connect(str(backup_file))
    conn.execute(f"PRAGMA key = \"x'{hex_key}'\";")
    conn.execute("CREATE TABLE test_data (id INT);")
    conn.execute("INSERT INTO test_data VALUES (42);")
    conn.commit()
    conn.close()

    # Also create a dummy -wal sidecar to verify sidecar copying
    wal_file = Path(str(backup_file) + "-wal")
    wal_file.write_bytes(b"dummy-wal-content")

    class StubApp:
        _handle_missing_office_db = SeraApp._handle_missing_office_db

    app = StubApp()
    # User clicks Yes to restore, selects backup_file
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.critical", lambda *args, **kwargs: 16384)  # QMessageBox.Yes
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getOpenFileName", lambda *args, **kwargs: (str(backup_file), ""))
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.information", lambda *args: None)

    app._handle_missing_office_db(tmp_path, db_path, hex_key)

    assert os.path.exists(db_path)
    assert os.path.exists(db_path + "-wal")
    assert Path(db_path + "-wal").read_bytes() == b"dummy-wal-content"

    # Verify the restored DB opens with hex_key using sqlcipher3
    conn2 = sqlite3.connect(db_path)
    conn2.execute(f"PRAGMA key = \"x'{hex_key}'\";")
    res = conn2.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='test_data';").fetchone()
    qc = conn2.execute("PRAGMA quick_check;").fetchone()
    conn2.close()
    assert res is not None
    assert qc == ("ok",)


def test_main_startup_missing_office_db_wrong_key(tmp_path, qapp, monkeypatch):
    import sqlcipher3.dbapi2 as sqlite3
    from main import SeraApp

    dek, info = _make_office(tmp_path, password=INITIAL_PW)
    hex_key = sera_keys.dek_hex(dek)
    db_path = str(tmp_path / "master.db")

    # Create backup with a DIFFERENT key
    wrong_key = "aa" * 32
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir(parents=True)
    backup_file = backups_dir / "master.db.bak"
    conn = sqlite3.connect(str(backup_file))
    conn.execute(f"PRAGMA key = \"x'{wrong_key}'\";")
    conn.execute("CREATE TABLE test_data (id INT);")
    conn.commit()
    conn.close()

    class StubApp:
        _handle_missing_office_db = SeraApp._handle_missing_office_db

    app = StubApp()
    critical_errors = []
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.critical", lambda parent, title, text, *args, **kwargs: critical_errors.append(text) or 16384)
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getOpenFileName", lambda *args, **kwargs: (str(backup_file), ""))
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.information", lambda *args: None)

    with pytest.raises(SystemExit):
        app._handle_missing_office_db(tmp_path, db_path, hex_key)

    assert not os.path.exists(db_path)
    assert any("could not be opened with this office key" in err for err in critical_errors)


def test_main_startup_missing_office_db_unencrypted(tmp_path, qapp, monkeypatch):
    import sqlite3 as std_sqlite3
    from main import SeraApp

    dek, info = _make_office(tmp_path, password=INITIAL_PW)
    hex_key = sera_keys.dek_hex(dek)
    db_path = str(tmp_path / "master.db")

    # Create unencrypted SQLite backup (standard sqlite3 without PRAGMA key)
    backup_file = tmp_path / "unencrypted.db"
    conn = std_sqlite3.connect(str(backup_file))
    conn.execute("CREATE TABLE unenc (id INT);")
    conn.commit()
    conn.close()

    class StubApp:
        _handle_missing_office_db = SeraApp._handle_missing_office_db

    app = StubApp()
    critical_errors = []
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.critical", lambda parent, title, text, *args, **kwargs: critical_errors.append(text) or 16384)
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getOpenFileName", lambda *args, **kwargs: (str(backup_file), ""))
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.information", lambda *args: None)

    with pytest.raises(SystemExit):
        app._handle_missing_office_db(tmp_path, db_path, hex_key)

    assert not os.path.exists(db_path)
    assert any("could not be opened with this office key" in err for err in critical_errors)


def test_main_startup_missing_office_db_file_dialog_cancel(tmp_path, qapp, monkeypatch):
    from main import SeraApp

    dek, info = _make_office(tmp_path, password=INITIAL_PW)
    hex_key = sera_keys.dek_hex(dek)
    db_path = str(tmp_path / "master.db")

    class StubApp:
        _handle_missing_office_db = SeraApp._handle_missing_office_db

    app = StubApp()
    info_messages = []
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.critical", lambda *args, **kwargs: 16384)  # Yes
    monkeypatch.setattr("PySide6.QtWidgets.QFileDialog.getOpenFileName", lambda *args, **kwargs: ("", ""))  # Cancelled
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.information", lambda parent, title, text: info_messages.append((title, text)))

    with pytest.raises(SystemExit):
        app._handle_missing_office_db(tmp_path, db_path, hex_key)

    assert not os.path.exists(db_path)
    assert any("Restore Cancelled" in title for title, text in info_messages)


def test_password_whitespace_preserves_exact_legacy_password(tmp_path):
    """When an office key was wrapped with a password containing whitespace,
    unlocking uses the exact password as typed and does not lock the office out.
    """
    pw_with_spaces = "  spaced-pass-123  "
    dek, info = _make_office(tmp_path, password=pw_with_spaces)

    # 1. recover_dek works with the exact password
    assert sera_keys.recover_dek(tmp_path, pw_with_spaces) == dek

    # 2. Stripped text does not unwrap when the true password had spaces
    with pytest.raises(WrongPassword):
        sera_keys.recover_dek(tmp_path, pw_with_spaces.strip())

    # 3. export_recovery_kit works with exact password
    kit_path = tmp_path / "spaced.serakit"
    sera_keys.export_recovery_kit(tmp_path, pw_with_spaces, kit_path)
    assert kit_path.exists()

    # 4. restore_recovery_kit to a fresh dir works with exact password
    target_dir = tmp_path / "restored_spaced"
    assert sera_keys.restore_recovery_kit(target_dir, kit_path, pw_with_spaces) == dek

    # 5. change_master_password verifies exact old password and sets stripped new password
    sera_keys.change_master_password(tmp_path, pw_with_spaces, "  " + NEW_PW + "  ")
    # New password was stripped on save per §5 / review
    assert sera_keys.recover_dek(tmp_path, NEW_PW) == dek


def test_password_whitespace_lenient_fallback(tmp_path):
    """When the true password has no spaces, accidentally typing spaces still
    unwraps via lenient fallback.
    """
    clean_pw = "clean-pass-123"
    dek, info = _make_office(tmp_path, password=clean_pw)

    # Typing with leading/trailing spaces falls back to stripped and succeeds
    assert sera_keys.recover_dek(tmp_path, "  " + clean_pw + "  ") == dek


def test_unified_settings_backup_page_office_mode(tmp_path, qapp, monkeypatch):
    """UnifiedSettingsDialog Backup page shows recovery kit and change password buttons
    in office mode, and works without NameError or crash.
    """
    from ui.dialogs.unified_settings_dialog import UnifiedSettingsDialog, _P_BACKUP
    from PySide6.QtWidgets import QPushButton, QLabel
    from unittest.mock import MagicMock

    dek, info = _make_office(tmp_path, password=INITIAL_PW)
    db_path = str(tmp_path / "master.db")

    mock_db = MagicMock()
    mock_db.db_path = db_path
    mock_db.get_setting.side_effect = lambda k, default=None: default

    dlg = UnifiedSettingsDialog(parent=None, db=mock_db)
    # Switch to Backup page
    dlg._switch_page(_P_BACKUP)

    # Find the buttons in the dialog
    buttons = {btn.text(): btn for btn in dlg.findChildren(QPushButton)}
    labels = [lbl.text() for lbl in dlg.findChildren(QLabel)]

    assert any("Office Recovery Kit & Security" in txt for txt in labels)
    assert "Export Recovery Kit →" in buttons
    assert "Change Password →" in buttons


def test_unified_settings_backup_page_legacy_mode(tmp_path, qapp):
    """UnifiedSettingsDialog Backup page hides recovery kit and change password buttons
    in legacy mode.
    """
    from ui.dialogs.unified_settings_dialog import UnifiedSettingsDialog, _P_BACKUP
    from PySide6.QtWidgets import QPushButton, QLabel
    from unittest.mock import MagicMock

    db_path = str(tmp_path / "master.db")

    mock_db = MagicMock()
    mock_db.db_path = db_path
    mock_db.get_setting.side_effect = lambda k, default=None: default

    dlg = UnifiedSettingsDialog(parent=None, db=mock_db)
    dlg._switch_page(_P_BACKUP)

    buttons = {btn.text(): btn for btn in dlg.findChildren(QPushButton)}
    labels = [lbl.text() for lbl in dlg.findChildren(QLabel)]

    assert not any("Office Recovery Kit & Security" in txt for txt in labels)
    assert "Export Recovery Kit →" not in buttons
    assert "Change Password →" not in buttons

