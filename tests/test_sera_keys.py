"""Tests for sera_keys.py (Sera Sync v3, WP P1-1).

All files live under pytest's tmp_path; the real data folder is never touched.
Keys and passwords here are invented test values.
"""

import base64
import json
import os
import re
import sys

import pytest

import sera_keys
from sera_keys import (
    KeyUnavailable,
    OfficeInfo,
    WrongPassword,
)

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")

OFFICE_ID = "3f2a9c1e-0000-4000-8000-000000000001"
OTHER_OFFICE_ID = "3f2a9c1e-0000-4000-8000-000000000002"
PASSWORD = "correct horse battery"


def _aad(office_id):
    return b"sera-recovery-v1|" + office_id.encode()


# ---------------------------------------------------------------- DPAPI

@windows_only
def test_dpapi_round_trip():
    secret = os.urandom(32)
    blob = sera_keys.dpapi_protect(secret, sera_keys.ENTROPY_OFFICE_KEY)
    assert isinstance(blob, bytes)
    assert secret not in blob
    assert sera_keys.dpapi_unprotect(blob, sera_keys.ENTROPY_OFFICE_KEY) == secret


@windows_only
def test_dpapi_wrong_entropy_fails():
    blob = sera_keys.dpapi_protect(os.urandom(32), sera_keys.ENTROPY_OFFICE_KEY)
    with pytest.raises(OSError):
        sera_keys.dpapi_unprotect(blob, sera_keys.ENTROPY_DEVICE_KEY)


@windows_only
def test_dpapi_tampered_blob_fails():
    blob = bytearray(sera_keys.dpapi_protect(os.urandom(32), sera_keys.ENTROPY_OFFICE_KEY))
    blob[-5] ^= 0x01
    with pytest.raises(OSError):
        sera_keys.dpapi_unprotect(bytes(blob), sera_keys.ENTROPY_OFFICE_KEY)


# ---------------------------------------------------------------- password wrap

def test_password_wrap_round_trip():
    secret = os.urandom(32)
    blob = sera_keys.wrap_with_password(secret, PASSWORD, _aad(OFFICE_ID))
    assert blob["format"] == 1
    assert blob["kdf"] == "argon2id"
    assert (blob["t"], blob["m_kib"], blob["p"]) == (3, 65536, 4)
    assert len(base64.b64decode(blob["salt"])) == 16
    assert len(base64.b64decode(blob["nonce"])) == 12
    # The blob is JSON-serialisable and does not contain the secret or password.
    text = json.dumps(blob)
    assert PASSWORD not in text
    assert secret.hex() not in text
    assert sera_keys.unwrap_with_password(blob, PASSWORD, _aad(OFFICE_ID)) == secret


def test_password_wrap_uses_fresh_salt_and_nonce():
    secret = os.urandom(32)
    a = sera_keys.wrap_with_password(secret, PASSWORD, _aad(OFFICE_ID))
    b = sera_keys.wrap_with_password(secret, PASSWORD, _aad(OFFICE_ID))
    assert a["salt"] != b["salt"]
    assert a["nonce"] != b["nonce"]
    assert a["ct"] != b["ct"]


def test_wrong_password_raises_wrong_password():
    blob = sera_keys.wrap_with_password(os.urandom(32), PASSWORD, _aad(OFFICE_ID))
    with pytest.raises(WrongPassword):
        sera_keys.unwrap_with_password(blob, PASSWORD + "x", _aad(OFFICE_ID))


def test_tampered_ciphertext_fails():
    blob = sera_keys.wrap_with_password(os.urandom(32), PASSWORD, _aad(OFFICE_ID))
    ct = bytearray(base64.b64decode(blob["ct"]))
    ct[0] ^= 0x01
    tampered = dict(blob, ct=base64.b64encode(bytes(ct)).decode())
    with pytest.raises(WrongPassword):
        sera_keys.unwrap_with_password(tampered, PASSWORD, _aad(OFFICE_ID))


def test_tampered_kdf_params_fail():
    blob = sera_keys.wrap_with_password(os.urandom(32), PASSWORD, _aad(OFFICE_ID))
    with pytest.raises(WrongPassword):
        sera_keys.unwrap_with_password(dict(blob, t=2), PASSWORD, _aad(OFFICE_ID))


def test_wrong_aad_other_office_fails():
    blob = sera_keys.wrap_with_password(os.urandom(32), PASSWORD, _aad(OFFICE_ID))
    with pytest.raises(WrongPassword):
        sera_keys.unwrap_with_password(blob, PASSWORD, _aad(OTHER_OFFICE_ID))


def test_malformed_blob_rejected_without_running_kdf():
    blob = sera_keys.wrap_with_password(os.urandom(32), PASSWORD, _aad(OFFICE_ID))
    for bad in (
        dict(blob, format=2),
        dict(blob, kdf="pbkdf2"),
        dict(blob, m_kib=64 * 1024 * 1024),   # absurd memory cost: refuse, don't allocate
        dict(blob, salt="!!not base64!!"),
        dict(blob, nonce=base64.b64encode(b"short").decode()),
        {k: v for k, v in blob.items() if k != "ct"},
    ):
        with pytest.raises(sera_keys.KeyFileInvalid):
            sera_keys.unwrap_with_password(bad, PASSWORD, _aad(OFFICE_ID))


# ---------------------------------------------------------------- key id / dek

def test_key_id_stable_and_32_hex():
    dek = bytes(range(32))
    kid = sera_keys.key_id(dek)
    assert re.fullmatch(r"[0-9a-f]{32}", kid)
    assert sera_keys.key_id(dek) == kid
    import hashlib
    import hmac
    assert kid == hmac.new(dek, b"sera-key-id-v1", hashlib.sha256).hexdigest()[:32]
    assert sera_keys.key_id(os.urandom(32)) != kid


def test_new_dek_and_dek_hex():
    dek = sera_keys.new_dek()
    assert isinstance(dek, bytes) and len(dek) == 32
    assert sera_keys.new_dek() != dek
    assert sera_keys.dek_hex(dek) == dek.hex()
    assert re.fullmatch(r"[0-9a-f]{64}", sera_keys.dek_hex(dek))


# ---------------------------------------------------------------- atomic_write

def test_atomic_write_leaves_no_tmp_on_success(tmp_path):
    target = tmp_path / "keys" / "office.json"
    target.parent.mkdir()
    sera_keys.atomic_write(target, b"first")
    sera_keys.atomic_write(target, b"second")
    assert target.read_bytes() == b"second"
    assert [p.name for p in target.parent.iterdir()] == ["office.json"]


def test_atomic_write_failure_keeps_old_file_and_no_tmp(tmp_path, monkeypatch):
    target = tmp_path / "office.json"
    target.write_bytes(b"old")

    def boom(src, dst):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(sera_keys.os, "replace", boom)
    monkeypatch.setattr(sera_keys, "_REPLACE_RETRY_SEC", 0.0)
    with pytest.raises(OSError):
        sera_keys.atomic_write(target, b"new")
    assert target.read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir()] == ["office.json"]


# ---------------------------------------------------------------- office.json

def test_office_json_round_trip(tmp_path):
    assert sera_keys.load_office(tmp_path) is None
    info = OfficeInfo(office_id=OFFICE_ID, office_name="Test Office", key_id="ab" * 16)
    sera_keys.save_office(tmp_path, info)
    raw = json.loads((tmp_path / "keys" / "office.json").read_text(encoding="utf-8"))
    assert raw["format"] == 1
    assert raw["office_id"] == OFFICE_ID
    assert raw["office_name"] == "Test Office"
    assert raw["key_id"] == "ab" * 16
    assert raw["admin_pubkey"] is None and raw["device_id"] is None
    assert raw["created_at"]
    loaded = sera_keys.load_office(tmp_path)
    assert loaded == info


def test_load_office_rejects_unknown_format(tmp_path):
    keys = tmp_path / "keys"
    keys.mkdir()
    (keys / "office.json").write_text(json.dumps({"format": 99, "office_id": OFFICE_ID}), encoding="utf-8")
    with pytest.raises(sera_keys.KeyFileInvalid):
        sera_keys.load_office(tmp_path)


def test_save_office_backs_up_previous_file(tmp_path):
    sera_keys.save_office(tmp_path, OfficeInfo(office_id=OFFICE_ID, office_name="A", key_id="00" * 16))
    sera_keys.save_office(tmp_path, OfficeInfo(office_id=OFFICE_ID, office_name="B", key_id="00" * 16))
    names = sorted(p.name for p in (tmp_path / "keys").iterdir())
    assert "office.json" in names
    backups = [n for n in names if n.startswith("office.json.bak-")]
    assert len(backups) == 1
    assert json.loads((tmp_path / "keys" / backups[0]).read_text(encoding="utf-8"))["office_name"] == "A"


# ---------------------------------------------------------------- store / load / recover

def _make_office(tmp_path, dek):
    sera_keys.save_office(
        tmp_path, OfficeInfo(office_id=OFFICE_ID, office_name="Test Office", key_id=sera_keys.key_id(dek))
    )
    sera_keys.store_dek(tmp_path, dek, PASSWORD, OFFICE_ID)


@windows_only
def test_store_and_load_dek(tmp_path):
    dek = sera_keys.new_dek()
    _make_office(tmp_path, dek)
    keys = tmp_path / "keys"
    assert (keys / "office_key.dpapi").exists()
    assert (keys / "office_key.recovery").exists()
    # The DEK is never on disk in the clear.
    for p in keys.iterdir():
        data = p.read_bytes()
        assert dek not in data and dek.hex().encode() not in data
    assert sera_keys.load_dek(tmp_path) == dek


def test_load_dek_missing_raises_key_unavailable(tmp_path):
    with pytest.raises(KeyUnavailable):
        sera_keys.load_dek(tmp_path)


@windows_only
def test_load_dek_undecryptable_raises_key_unavailable(tmp_path):
    keys = tmp_path / "keys"
    keys.mkdir()
    # A blob protected with other entropy stands in for "another Windows account".
    (keys / "office_key.dpapi").write_bytes(
        sera_keys.dpapi_protect(os.urandom(32), sera_keys.ENTROPY_ADMIN_KEY)
    )
    with pytest.raises(KeyUnavailable):
        sera_keys.load_dek(tmp_path)


@windows_only
def test_recover_dek_restores_dpapi_file(tmp_path):
    dek = sera_keys.new_dek()
    _make_office(tmp_path, dek)
    dpapi_path = tmp_path / "keys" / "office_key.dpapi"
    dpapi_path.write_bytes(b"garbage from another account")
    with pytest.raises(KeyUnavailable):
        sera_keys.load_dek(tmp_path)

    with pytest.raises(WrongPassword):
        sera_keys.recover_dek(tmp_path, "not the password")
    assert dpapi_path.read_bytes() == b"garbage from another account"

    assert sera_keys.recover_dek(tmp_path, PASSWORD) == dek
    assert sera_keys.load_dek(tmp_path) == dek
    # The unreadable file was kept as a backup, not deleted (rule 3).
    backups = [p for p in (tmp_path / "keys").iterdir() if p.name.startswith("office_key.dpapi.bak-")]
    assert len(backups) == 1 and backups[0].read_bytes() == b"garbage from another account"


@windows_only
def test_recover_dek_rejects_key_id_mismatch(tmp_path):
    dek = sera_keys.new_dek()
    _make_office(tmp_path, dek)
    # office.json now names a different key (e.g. a mixed-up keys folder).
    sera_keys.save_office(
        tmp_path, OfficeInfo(office_id=OFFICE_ID, office_name="Test Office", key_id="ff" * 16)
    )
    with pytest.raises(sera_keys.KeyFileInvalid):
        sera_keys.recover_dek(tmp_path, PASSWORD)


def test_recover_dek_without_office_raises_key_unavailable(tmp_path):
    with pytest.raises(KeyUnavailable):
        sera_keys.recover_dek(tmp_path, PASSWORD)


def test_module_does_not_import_pyside6():
    import ast
    import pathlib
    tree = ast.parse(pathlib.Path(sera_keys.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "PySide6" not in imported
