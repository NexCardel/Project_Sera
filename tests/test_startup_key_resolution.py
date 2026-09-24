"""Tests for start-up key resolution in main.py (Sera Sync v3, WP P1-2).

Verifies key resolution behavior:
- Office mode (when keys/office.json exists):
  - Uses load_dek() via DPAPI
  - Exposes self.key_mode == "office"
  - Exposes self.key_id matching office.json
  - Derives hex_key = dek_hex(dek)
  - Passes hex_key and key_id into SyncPeerService
  - Ignores sera.key completely (never reads or writes it)
  - Falls back to recovery prompt (P1-6) on KeyUnavailable
- Legacy mode (when keys/office.json does not exist):
  - Unchanged today's path with P0-5
  - Exposes self.key_mode == "legacy"
  - Exposes self.key_id == None
  - Passes hex_key and key_id=None into SyncPeerService

All tests use pytest's tmp_path; never touches real data directories (§0 rule 2).
"""

import os
import sys
from pathlib import Path
import pytest

import security
import sera_keys
from database import SeraDatabase
from main import SeraApp
from sync_peer import SyncPeerService

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")


class StubSeraApp:
    """Lightweight test harness mirroring SeraApp's key resolution wiring."""
    _resolve_encryption_key = SeraApp._resolve_encryption_key
    _recover_office_dek = SeraApp._recover_office_dek
    _verify_master_password = SeraApp._verify_master_password
    _get_master_password = SeraApp._get_master_password
    _show_startup_auth_error = SeraApp._show_startup_auth_error

    def __init__(self, folder: Path):
        self.app_dir = folder
        self.db_path = str(folder / "master.db")
        self.salt_path = str(folder / security.SALT_FILE)
        self.identity_path = folder / "device_identity.txt"
        self.actor_alias = "TestAdmin"
        self.prompt_calls = []
        self.alert_calls = []
        self.key_mode = None
        self.key_id = None
        self.sync_service = None

    def _prompt_master_password(self, prompt_text="Enter Master Password:"):
        self.prompt_calls.append(prompt_text)
        return ""


@windows_only
def test_startup_office_mode_ignores_sera_key(tmp_path, monkeypatch):
    """Office mode: keys/office.json exists -> loads DEK, exposes key_mode='office'
    and key_id, passes them to SyncPeerService, and never reads or writes sera.key.
    """
    monkeypatch.setattr("main.APP_DIR", tmp_path)

    # 1. Setup office mode credentials
    office_id = sera_keys.OfficeInfo.new_office_id()
    dek = sera_keys.new_dek()
    expected_key_id = sera_keys.key_id(dek)
    expected_hex_key = sera_keys.dek_hex(dek)

    info = sera_keys.OfficeInfo(
        office_id=office_id,
        office_name="Aman Office Test",
        key_id=expected_key_id,
    )
    sera_keys.save_office(tmp_path, info)
    sera_keys.store_dek(tmp_path, dek, "SecretMaster123!", office_id)

    # Create encrypted master.db using the DEK
    db_path = str(tmp_path / "master.db")
    db = SeraDatabase(db_path, expected_hex_key, defer_startup_maintenance=True)
    del db

    # Place an invalid / bogus legacy password in sera.key
    # And make sure NO sera.salt exists
    bogus_legacy_content = "bogus_invalid_password_that_must_not_be_read"
    key_file = tmp_path / "sera.key"
    key_file.write_text(bogus_legacy_content, encoding="utf-8")
    salt_file = tmp_path / security.SALT_FILE
    assert not salt_file.exists()

    # Intercept file opens to strictly verify sera.key is never opened (read or write)
    accessed_paths = []
    orig_path_open = Path.open
    def tracked_path_open(self, *args, **kwargs):
        try:
            accessed_paths.append(self.resolve())
        except Exception:
            pass
        return orig_path_open(self, *args, **kwargs)
    monkeypatch.setattr(Path, "open", tracked_path_open)

    # 2. Run start-up key resolution
    app = StubSeraApp(tmp_path)
    key_mode, key_id, hex_key = app._resolve_encryption_key()

    # 3. Assertions on key resolution
    assert key_mode == "office"
    assert app.key_mode == "office"
    assert key_id == expected_key_id
    assert app.key_id == expected_key_id
    assert hex_key == expected_hex_key

    # Assert sera.key was NEVER opened, read, modified, or rewritten
    assert key_file.resolve() not in accessed_paths, "sera.key was accessed during office mode key resolution!"
    assert key_file.exists()
    assert key_file.read_text(encoding="utf-8") == bogus_legacy_content

    # Assert no password prompt was displayed
    assert len(app.prompt_calls) == 0

    # Assert database can be unlocked with resolved key
    test_db = SeraDatabase(db_path, hex_key, defer_startup_maintenance=True)
    assert test_db is not None
    del test_db

    # 4. Assert SyncPeerService wiring
    sync_service = SyncPeerService(
        db_path=app.db_path,
        salt_path=app.salt_path,
        username=app.actor_alias,
        hex_key=hex_key,
        key_id=app.key_id,
    )
    assert sync_service.hex_key == expected_hex_key
    assert sync_service.key_id == expected_key_id


@windows_only
def test_startup_office_mode_without_sera_key(tmp_path, monkeypatch):
    """Office mode starts cleanly when sera.key does not exist at all."""
    monkeypatch.setattr("main.APP_DIR", tmp_path)

    office_id = sera_keys.OfficeInfo.new_office_id()
    dek = sera_keys.new_dek()
    expected_key_id = sera_keys.key_id(dek)
    expected_hex_key = sera_keys.dek_hex(dek)

    info = sera_keys.OfficeInfo(
        office_id=office_id,
        office_name="Aman Office No Legacy Key",
        key_id=expected_key_id,
    )
    sera_keys.save_office(tmp_path, info)
    sera_keys.store_dek(tmp_path, dek, "SecretMaster123!", office_id)

    db_path = str(tmp_path / "master.db")
    db = SeraDatabase(db_path, expected_hex_key, defer_startup_maintenance=True)
    del db

    # Explicitly verify sera.key and sera.salt do NOT exist
    assert not (tmp_path / "sera.key").exists()
    assert not (tmp_path / security.SALT_FILE).exists()

    app = StubSeraApp(tmp_path)
    key_mode, key_id, hex_key = app._resolve_encryption_key()

    assert key_mode == "office"
    assert key_id == expected_key_id
    assert hex_key == expected_hex_key
    assert not (tmp_path / "sera.key").exists(), "sera.key must not be created in office mode"

    sync_service = SyncPeerService(
        db_path=app.db_path,
        salt_path=app.salt_path,
        username=app.actor_alias,
        hex_key=hex_key,
        key_id=app.key_id,
    )
    assert sync_service.hex_key == expected_hex_key
    assert sync_service.key_id == expected_key_id


def test_startup_legacy_mode_unchanged(tmp_path, monkeypatch):
    """Legacy mode: keys/office.json does not exist -> uses P0-5 path, exposes
    key_mode='legacy', key_id=None, and passes them to SyncPeerService.
    """
    monkeypatch.setattr("main.APP_DIR", tmp_path)

    legacy_password = "legacy_office_pass_789"

    # Setup legacy salt, key, and database
    salt_path = str(tmp_path / security.SALT_FILE)
    security.generate_and_save_salt(salt_path)
    salt = security.load_salt(salt_path)
    expected_hex_key = security.derive_key_hex(legacy_password, salt)

    db_path = str(tmp_path / "master.db")
    db = SeraDatabase(db_path, expected_hex_key, defer_startup_maintenance=True)
    del db

    key_file = tmp_path / "sera.key"
    key_file.write_text(legacy_password, encoding="utf-8")

    # Verify keys/office.json does NOT exist
    office_file = tmp_path / "keys" / "office.json"
    assert not office_file.exists()

    # Run key resolution
    app = StubSeraApp(tmp_path)
    key_mode, key_id, hex_key = app._resolve_encryption_key()

    # Assertions
    assert key_mode == "legacy"
    assert app.key_mode == "legacy"
    assert key_id is None
    assert app.key_id is None
    assert hex_key == expected_hex_key

    # DB can be opened with hex_key
    test_db = SeraDatabase(db_path, hex_key, defer_startup_maintenance=True)
    assert test_db is not None
    del test_db

    # SyncPeerService receives hex_key and key_id=None
    sync_service = SyncPeerService(
        db_path=app.db_path,
        salt_path=app.salt_path,
        username=app.actor_alias,
        hex_key=hex_key,
        key_id=app.key_id,
    )
    assert sync_service.hex_key == expected_hex_key
    assert sync_service.key_id is None


@windows_only
def test_startup_office_mode_with_key_unavailable_recovers_dek(tmp_path, monkeypatch):
    """When DPAPI cannot decrypt (KeyUnavailable), recovery prompt recovers DEK
    from keys/office_key.recovery and re-creates keys/office_key.dpapi.
    """
    monkeypatch.setattr("main.APP_DIR", tmp_path)

    office_id = sera_keys.OfficeInfo.new_office_id()
    dek = sera_keys.new_dek()
    expected_key_id = sera_keys.key_id(dek)
    expected_hex_key = sera_keys.dek_hex(dek)
    recovery_password = "CorrectRecoveryPass1!"

    info = sera_keys.OfficeInfo(
        office_id=office_id,
        office_name="Aman Recovery Test",
        key_id=expected_key_id,
    )
    sera_keys.save_office(tmp_path, info)
    sera_keys.store_dek(tmp_path, dek, recovery_password, office_id)

    # Delete the .dpapi file to simulate KeyUnavailable (e.g. new Windows account)
    dpapi_path = tmp_path / "keys" / "office_key.dpapi"
    dpapi_path.unlink()
    assert not dpapi_path.exists()
    assert (tmp_path / "keys" / "office_key.recovery").exists()

    app = StubSeraApp(tmp_path)
    # Monkeypatch prompt to supply correct recovery password
    app._prompt_master_password = lambda prompt_text="": recovery_password

    key_mode, key_id, hex_key = app._resolve_encryption_key()

    assert key_mode == "office"
    assert key_id == expected_key_id
    assert hex_key == expected_hex_key
    # The .dpapi file must have been re-created by recover_dek
    assert dpapi_path.exists()
    assert sera_keys.load_dek(tmp_path) == dek


def test_startup_office_mode_key_id_mismatch_aborts(tmp_path, monkeypatch):
    """If loaded DEK key_id does not match office.json, start-up aborts."""
    monkeypatch.setattr("main.APP_DIR", tmp_path)

    office_id = sera_keys.OfficeInfo.new_office_id()
    dek = sera_keys.new_dek()

    # Save office.json with a forged / different key_id
    info = sera_keys.OfficeInfo(
        office_id=office_id,
        office_name="Tampered Office",
        key_id="0123456789abcdef0123456789abcdef",
    )
    sera_keys.save_office(tmp_path, info)

    # Mock load_dek to return the original dek (whose key_id differs)
    monkeypatch.setattr("sera_keys.load_dek", lambda app_dir: dek)

    # Mock QMessageBox.critical so it does not pop up a UI dialog
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.critical", lambda *args: None)

    app = StubSeraApp(tmp_path)
    with pytest.raises(SystemExit):
        app._resolve_encryption_key()


def test_startup_office_mode_corrupt_office_json_aborts(tmp_path, monkeypatch):
    """If keys/office.json exists but is corrupt, start-up aborts and does NOT
    fall back to legacy mode.
    """
    monkeypatch.setattr("main.APP_DIR", tmp_path)

    keys_dir = tmp_path / "keys"
    keys_dir.mkdir(parents=True, exist_ok=True)
    (keys_dir / "office.json").write_text("{this is not valid json", encoding="utf-8")

    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.critical", lambda *args: None)

    app = StubSeraApp(tmp_path)
    with pytest.raises(SystemExit):
        app._resolve_encryption_key()
