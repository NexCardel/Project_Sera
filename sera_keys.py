"""
sera_keys.py
------------
Office data key (DEK) handling for Sera Sync v3 (blueprint §4.1, WP P1-1).

- The DEK is 32 random bytes; every Sera database is keyed with it directly
  (``PRAGMA key = "x'<dek_hex>'"``).
- On each PC it is stored in ``keys/office_key.dpapi`` (Windows DPAPI,
  CurrentUser scope) so start-up needs no password.
- ``keys/office_key.recovery`` holds the DEK wrapped with the master password
  (Argon2id -> AES-256-GCM), for when DPAPI can't decrypt.
- ``keys/office.json`` is plaintext and not secret (office id/name, key id, ...).

Never log or print keys, passwords or blobs from this module. No PySide6 here.
Heavy imports (argon2, cryptography, ctypes) are done lazily.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

KEYS_DIRNAME = "keys"
OFFICE_FILE = "office.json"
DEK_DPAPI_FILE = "office_key.dpapi"
DEK_RECOVERY_FILE = "office_key.recovery"
ADMIN_KEY_DPAPI_FILE = "admin_key.dpapi"
ADMIN_KEY_RECOVERY_FILE = "admin_key.recovery"

RECOVERY_KIT_FORMAT = "sera-recovery-kit-v1"

DEFAULT_MASTER_PASSWORD = "admin123"
MIN_MASTER_PASSWORD_LEN = 8

OFFICE_FORMAT = 1
WRAP_FORMAT = 1
DEK_LEN = 32

ENTROPY_OFFICE_KEY = b"SeraOfficeKey/v1"
ENTROPY_DEVICE_KEY = b"SeraDeviceKey/v1"   # P2-1
ENTROPY_ADMIN_KEY = b"SeraAdminKey/v1"     # P2-2

KEY_ID_INFO = b"sera-key-id-v1"
AAD_RECOVERY_PREFIX = b"sera-recovery-v1|"
AAD_ADMIN_PREFIX = b"sera-admin-v1|"

# Argon2id parameters written into new blobs.
ARGON2_T = 3
ARGON2_M_KIB = 65536
ARGON2_P = 4
# Bounds accepted when reading a blob. A tampered blob must not make us
# allocate gigabytes or spin for minutes before the GCM tag check fails.
_ARGON2_T_RANGE = (1, 16)
_ARGON2_M_KIB_RANGE = (8 * 1024, 1024 * 1024)
_ARGON2_P_RANGE = (1, 16)
_SALT_LEN = 16
_NONCE_LEN = 12

_CRYPTPROTECT_UI_FORBIDDEN = 0x1
_REPLACE_RETRY_SEC = 2.0


class SeraKeysError(Exception):
    """Base class for errors raised by this module."""


class WrongPassword(SeraKeysError):
    """The password (or the blob it protects) doesn't open the wrapped secret."""


class KeyUnavailable(SeraKeysError):
    """The DEK can't be loaded on this PC (file missing or DPAPI can't decrypt)."""


class KeyFileInvalid(SeraKeysError, ValueError):
    """A key file is malformed, has an unknown format, or names a different key."""


@dataclass
class OfficeInfo:
    office_id: str
    office_name: str
    key_id: str
    admin_pubkey: str | None = None   # P2-2
    device_id: str | None = None      # P2-1
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    format: int = OFFICE_FORMAT

    @staticmethod
    def new_office_id() -> str:
        return str(uuid.uuid4())


# ---------------------------------------------------------------- DPAPI (ctypes)

def _dpapi_call(func_name: str, data: bytes, entropy: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class _BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _in(buf_data: bytes):
        # max(...,1): a zero-length ctypes buffer can't be created; cbData stays exact.
        buf = ctypes.create_string_buffer(buf_data, max(len(buf_data), 1))
        return _BLOB(len(buf_data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    func = getattr(crypt32, func_name)
    func.argtypes = [
        ctypes.POINTER(_BLOB), ctypes.c_void_p, ctypes.POINTER(_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_BLOB),
    ]
    func.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    blob_in, _keep_in = _in(data)
    blob_entropy, _keep_entropy = _in(entropy)
    blob_out = _BLOB()
    ok = func(ctypes.byref(blob_in), None, ctypes.byref(blob_entropy), None, None,
              _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        if blob_out.pbData:
            if func_name == "CryptUnprotectData":
                ctypes.memset(blob_out.pbData, 0, blob_out.cbData)
            kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def dpapi_protect(data: bytes, entropy: bytes) -> bytes:
    """Encrypt ``data`` for the current Windows user. Raises OSError on failure."""
    if os.name != "nt":
        raise OSError("DPAPI is only available on Windows")
    return _dpapi_call("CryptProtectData", bytes(data), bytes(entropy))


def dpapi_unprotect(blob: bytes, entropy: bytes) -> bytes:
    """Decrypt a DPAPI blob. Raises OSError if it can't (other user, wrong entropy, damaged)."""
    if os.name != "nt":
        raise OSError("DPAPI is only available on Windows")
    return _dpapi_call("CryptUnprotectData", bytes(blob), bytes(entropy))


# ---------------------------------------------------------------- DEK helpers

def new_dek() -> bytes:
    return os.urandom(DEK_LEN)


def key_id(dek: bytes) -> str:
    return hmac.new(dek, KEY_ID_INFO, hashlib.sha256).hexdigest()[:32]


def dek_hex(dek: bytes) -> str:
    return dek.hex()


def _check_dek(dek: bytes) -> None:
    if not isinstance(dek, (bytes, bytearray)) or len(dek) != DEK_LEN:
        raise ValueError("DEK must be %d bytes" % DEK_LEN)


def recovery_aad(office_id: str) -> bytes:
    return AAD_RECOVERY_PREFIX + office_id.encode()


def admin_aad(office_id: str) -> bytes:
    return AAD_ADMIN_PREFIX + office_id.encode()


# ---------------------------------------------------------------- password wrap

def _derive_kek(password: str, salt: bytes, t: int, m_kib: int, p: int) -> bytes:
    from argon2.low_level import Type, hash_secret_raw
    return hash_secret_raw(password.encode("utf-8"), salt, time_cost=t, memory_cost=m_kib,
                           parallelism=p, hash_len=32, type=Type.ID)


def wrap_with_password(secret: bytes, password: str, aad: bytes) -> dict:
    """Wrap ``secret`` with a key derived from ``password`` (Argon2id + AES-256-GCM)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not isinstance(password, str) or not password:
        raise ValueError("password must be a non-empty string")
    salt = os.urandom(_SALT_LEN)
    nonce = os.urandom(_NONCE_LEN)
    kek = _derive_kek(password, salt, ARGON2_T, ARGON2_M_KIB, ARGON2_P)
    ct = AESGCM(kek).encrypt(nonce, bytes(secret), aad)
    return {
        "format": WRAP_FORMAT,
        "kdf": "argon2id",
        "t": ARGON2_T,
        "m_kib": ARGON2_M_KIB,
        "p": ARGON2_P,
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ct": base64.b64encode(ct).decode("ascii"),
    }


def _int_in(blob: dict, name: str, bounds: tuple[int, int]) -> int:
    value = blob.get(name)
    if type(value) is not int or not (bounds[0] <= value <= bounds[1]):
        raise KeyFileInvalid("recovery data: bad %s" % name)
    return value


def _b64_field(blob: dict, name: str, length: int | None = None) -> bytes:
    value = blob.get(name)
    if not isinstance(value, str):
        raise KeyFileInvalid("recovery data: missing %s" % name)
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise KeyFileInvalid("recovery data: bad %s" % name) from None
    if length is not None and len(raw) != length:
        raise KeyFileInvalid("recovery data: bad %s length" % name)
    return raw


def unwrap_with_password(blob: dict, password: str, aad: bytes) -> bytes:
    """Reverse of ``wrap_with_password``.

    Raises ``WrongPassword`` when the tag doesn't verify (wrong password, other
    office's AAD, or tampered ciphertext/parameters — GCM can't tell these apart),
    and ``KeyFileInvalid`` when the blob itself is malformed.
    """
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not isinstance(blob, dict):
        raise KeyFileInvalid("recovery data: not an object")
    if blob.get("format") != WRAP_FORMAT or blob.get("kdf") != "argon2id":
        raise KeyFileInvalid("recovery data: unknown format")
    t = _int_in(blob, "t", _ARGON2_T_RANGE)
    m_kib = _int_in(blob, "m_kib", _ARGON2_M_KIB_RANGE)
    p = _int_in(blob, "p", _ARGON2_P_RANGE)
    if m_kib < 8 * p:
        raise KeyFileInvalid("recovery data: bad m_kib")
    salt = _b64_field(blob, "salt", _SALT_LEN)
    nonce = _b64_field(blob, "nonce", _NONCE_LEN)
    ct = _b64_field(blob, "ct")
    if len(ct) < 16:
        raise KeyFileInvalid("recovery data: ciphertext too short")
    if not isinstance(password, str) or not password:
        raise WrongPassword("wrong password")
    kek = _derive_kek(password, salt, t, m_kib, p)
    try:
        return AESGCM(kek).decrypt(nonce, ct, aad)
    except InvalidTag:
        raise WrongPassword("wrong password, or the recovery data is damaged") from None


def _unwrap_with_lenient_password(blob: dict, password: str, aad: bytes) -> bytes:
    """Unwrap blob trying exact password first, then stripped password as fallback."""
    try:
        return unwrap_with_password(blob, password, aad)
    except WrongPassword:
        stripped = password.strip() if isinstance(password, str) else ""
        if stripped and stripped != password:
            return unwrap_with_password(blob, stripped, aad)
        raise


# ---------------------------------------------------------------- files

def keys_dir(app_dir) -> Path:
    return Path(app_dir) / KEYS_DIRNAME


def atomic_write(path, data: bytes) -> None:
    """Write ``data`` to ``path`` via a temp file in the same folder + fsync + os.replace.

    On failure the old file (if any) is unchanged and the temp file is removed.
    """
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        deadline = time.monotonic() + _REPLACE_RETRY_SEC
        while True:
            try:
                os.replace(tmp, str(path))
                break
            except PermissionError:
                # Windows: AV scanners / indexers briefly lock freshly written files.
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _replace_key_file(path: Path, data: bytes) -> None:
    """atomic_write, but copy an existing, different file to ``*.bak-<ts>`` first (§0 rule 3)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            if path.read_bytes() == data:
                return
        except OSError:
            pass
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = path.with_name("%s.bak-%s" % (path.name, ts))
        n = 1
        while backup.exists():
            backup = path.with_name("%s.bak-%s_%d" % (path.name, ts, n))
            n += 1
        shutil.copy2(str(path), str(backup))
    atomic_write(path, data)


# ---------------------------------------------------------------- office.json

def load_office(app_dir) -> OfficeInfo | None:
    """Read ``keys/office.json``. ``None`` if it doesn't exist (legacy mode)."""
    path = keys_dir(app_dir) / OFFICE_FILE
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        raise KeyFileInvalid("%s is not valid JSON" % path) from None
    if not isinstance(data, dict) or data.get("format") != OFFICE_FORMAT:
        raise KeyFileInvalid("%s has an unknown format" % path)
    for name in ("office_id", "office_name", "key_id"):
        if not isinstance(data.get(name), str) or not data[name]:
            raise KeyFileInvalid("%s: missing %s" % (path, name))
    return OfficeInfo(
        office_id=data["office_id"],
        office_name=data["office_name"],
        key_id=data["key_id"],
        admin_pubkey=data.get("admin_pubkey"),
        device_id=data.get("device_id"),
        created_at=data.get("created_at") or "",
        format=OFFICE_FORMAT,
    )


def save_office(app_dir, info: OfficeInfo) -> None:
    d = asdict(info)
    data = {
        "format": OFFICE_FORMAT,
        "office_id": d["office_id"],
        "office_name": d["office_name"],
        "key_id": d["key_id"],
        "admin_pubkey": d["admin_pubkey"],
        "device_id": d["device_id"],
        "created_at": d["created_at"],
    }
    payload = (json.dumps(data, indent=2) + "\n").encode("utf-8")
    _replace_key_file(keys_dir(app_dir) / OFFICE_FILE, payload)


# ---------------------------------------------------------------- DEK storage

def load_dek(app_dir) -> bytes:
    """Load the DEK from ``keys/office_key.dpapi``. Raises ``KeyUnavailable``."""
    path = keys_dir(app_dir) / DEK_DPAPI_FILE
    try:
        blob = path.read_bytes()
    except FileNotFoundError:
        raise KeyUnavailable("%s not found" % path) from None
    except OSError as e:
        raise KeyUnavailable("%s can't be read (%s)" % (path, e.__class__.__name__)) from None
    try:
        dek = dpapi_unprotect(blob, ENTROPY_OFFICE_KEY)
    except OSError:
        raise KeyUnavailable("this Windows account can't decrypt %s" % path) from None
    if len(dek) != DEK_LEN:
        raise KeyUnavailable("%s does not hold a valid key" % path)
    return dek


def store_dek(app_dir, dek: bytes, password: str, office_id: str) -> None:
    """Write ``keys/office_key.recovery`` (password) and ``keys/office_key.dpapi`` (DPAPI).

    Existing different files are kept as ``*.bak-<ts>`` (§0 rule 3).
    """
    _check_dek(dek)
    if not office_id:
        raise ValueError("office_id is required")
    kdir = keys_dir(app_dir)
    kdir.mkdir(parents=True, exist_ok=True)
    # Compute both before writing either, so a DPAPI failure writes nothing.
    recovery = wrap_with_password(bytes(dek), password, recovery_aad(office_id))
    dpapi_blob = dpapi_protect(bytes(dek), ENTROPY_OFFICE_KEY)
    _replace_key_file(kdir / DEK_RECOVERY_FILE, (json.dumps(recovery, indent=2) + "\n").encode("utf-8"))
    _replace_key_file(kdir / DEK_DPAPI_FILE, dpapi_blob)


def recover_dek(app_dir, password: str) -> bytes:
    """Unwrap the DEK from ``keys/office_key.recovery`` and re-store ``office_key.dpapi``.

    Raises ``KeyUnavailable`` (no office / no recovery file), ``WrongPassword``,
    or ``KeyFileInvalid`` (malformed file, or the key doesn't match office.json).
    """
    office = load_office(app_dir)
    if office is None:
        raise KeyUnavailable("no office key on this PC (keys/%s missing)" % OFFICE_FILE)
    path = keys_dir(app_dir) / DEK_RECOVERY_FILE
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise KeyUnavailable("%s not found" % path) from None
    try:
        blob = json.loads(raw)
    except ValueError:
        raise KeyFileInvalid("%s is not valid JSON" % path) from None
    dek = _unwrap_with_lenient_password(blob, password, recovery_aad(office.office_id))
    if len(dek) != DEK_LEN or not hmac.compare_digest(key_id(dek), office.key_id):
        raise KeyFileInvalid("%s does not match the key id in %s" % (path, OFFICE_FILE))
    try:
        _replace_key_file(keys_dir(app_dir) / DEK_DPAPI_FILE, dpapi_protect(dek, ENTROPY_OFFICE_KEY))
    except OSError:
        pass
    return dek


# ---------------------------------------------------------------- password validation & change

def validate_master_password(password: str | None) -> str | None:
    """Error text, or None if ``password`` is acceptable as the office master password."""
    if not isinstance(password, str) or len(password.strip()) < MIN_MASTER_PASSWORD_LEN:
        return "The master password must be at least %d characters long." % MIN_MASTER_PASSWORD_LEN
    if password.strip().lower() == DEFAULT_MASTER_PASSWORD:
        return "The default password 'admin123' can't be the office master password."
    return None


def change_master_password(app_dir, old_password: str, new_password: str) -> None:
    """Verify old_password via unwrap_with_password, re-wrap DEK (and admin key if present).

    The database is not touched (§5 P1-6).
    """
    new_password = (new_password or "").strip()
    office = load_office(app_dir)
    if office is None:
        raise KeyUnavailable("no office key on this PC (keys/%s missing)" % OFFICE_FILE)

    err = validate_master_password(new_password)
    if err:
        raise ValueError(err)

    kdir = keys_dir(app_dir)
    recovery_path = kdir / DEK_RECOVERY_FILE
    if not recovery_path.exists():
        raise KeyUnavailable("%s not found" % recovery_path)
    try:
        blob = json.loads(recovery_path.read_text(encoding="utf-8"))
    except ValueError:
        raise KeyFileInvalid("%s is not valid JSON" % recovery_path) from None

    # 1. Verify old password and unwrap DEK
    dek = _unwrap_with_lenient_password(blob, old_password, recovery_aad(office.office_id))
    if len(dek) != DEK_LEN or not hmac.compare_digest(key_id(dek), office.key_id):
        raise KeyFileInvalid("%s does not match the key id in %s" % (recovery_path, OFFICE_FILE))

    # 2. If admin_key.recovery exists, re-wrap it with new password
    admin_path = kdir / ADMIN_KEY_RECOVERY_FILE
    new_admin_blob = None
    if admin_path.exists():
        try:
            admin_blob = json.loads(admin_path.read_text(encoding="utf-8"))
        except ValueError:
            raise KeyFileInvalid("%s is not valid JSON" % admin_path) from None
        admin_secret = _unwrap_with_lenient_password(admin_blob, old_password, admin_aad(office.office_id))
        new_admin_blob = wrap_with_password(admin_secret, new_password, admin_aad(office.office_id))

    # 3. Re-wrap DEK with new password
    new_recovery_blob = wrap_with_password(dek, new_password, recovery_aad(office.office_id))

    # 4. Save new recovery blobs (old files copied to *.bak-<ts> first per rule 3 / owner decision)
    _replace_key_file(recovery_path, (json.dumps(new_recovery_blob, indent=2) + "\n").encode("utf-8"))
    if new_admin_blob is not None:
        _replace_key_file(admin_path, (json.dumps(new_admin_blob, indent=2) + "\n").encode("utf-8"))


# ---------------------------------------------------------------- recovery kit export / restore

def export_recovery_kit(app_dir, password: str, dest_path: str | Path) -> Path:
    """Exports office.json + office_key.recovery (+ admin_key.recovery after P2-2)

    into one .serakit JSON file chosen by the user. Master password is required to create it.
    """
    office = load_office(app_dir)
    if office is None:
        raise KeyUnavailable("no office key on this PC (keys/%s missing)" % OFFICE_FILE)

    kdir = keys_dir(app_dir)
    recovery_path = kdir / DEK_RECOVERY_FILE
    if not recovery_path.exists():
        raise KeyUnavailable("%s not found" % recovery_path)
    try:
        blob = json.loads(recovery_path.read_text(encoding="utf-8"))
    except ValueError:
        raise KeyFileInvalid("%s is not valid JSON" % recovery_path) from None

    # Verify password against recovery blob
    dek = _unwrap_with_lenient_password(blob, password, recovery_aad(office.office_id))
    if len(dek) != DEK_LEN or not hmac.compare_digest(key_id(dek), office.key_id):
        raise KeyFileInvalid("%s does not match the key id in %s" % (recovery_path, OFFICE_FILE))

    admin_blob = None
    admin_path = kdir / ADMIN_KEY_RECOVERY_FILE
    if admin_path.exists():
        try:
            admin_blob = json.loads(admin_path.read_text(encoding="utf-8"))
        except ValueError:
            raise KeyFileInvalid("%s is not valid JSON" % admin_path) from None

    office_data = {
        "format": OFFICE_FORMAT,
        "office_id": office.office_id,
        "office_name": office.office_name,
        "key_id": office.key_id,
        "admin_pubkey": office.admin_pubkey,
        "device_id": office.device_id,
        "created_at": office.created_at,
    }

    kit_data = {
        "format": RECOVERY_KIT_FORMAT,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "office": office_data,
        "office_key_recovery": blob,
        "admin_key_recovery": admin_blob,
    }

    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(kit_data, indent=2) + "\n").encode("utf-8")
    atomic_write(dest, payload)
    return dest


def inspect_recovery_kit(kit_path: str | Path) -> dict:
    """Read and validate a .serakit recovery file, returning metadata dict."""
    path = Path(kit_path)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise KeyUnavailable("%s not found" % path) from None
    except OSError as e:
        raise KeyFileInvalid("%s can't be read: %s" % (path, e)) from None
    try:
        data = json.loads(raw)
    except ValueError:
        raise KeyFileInvalid("%s is not valid JSON" % path) from None

    if not isinstance(data, dict) or data.get("format") != RECOVERY_KIT_FORMAT:
        raise KeyFileInvalid("%s is not a valid Sera recovery kit" % path)

    office = data.get("office")
    if not isinstance(office, dict) or not office.get("office_id") or not office.get("key_id"):
        raise KeyFileInvalid("%s: missing or invalid office information" % path)

    if not isinstance(data.get("office_key_recovery"), dict):
        raise KeyFileInvalid("%s: missing office_key_recovery" % path)

    return {
        "office_id": office["office_id"],
        "office_name": office.get("office_name", ""),
        "key_id": office["key_id"],
        "created_at": data.get("created_at", ""),
        "has_admin_key": isinstance(data.get("admin_key_recovery"), dict),
    }


def restore_recovery_kit(app_dir, kit_path: str | Path, password: str) -> bytes:
    """Unpack a .serakit recovery file, verify password, restore key files, and return DEK."""
    path = Path(kit_path)
    inspect_recovery_kit(path)
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)

    office_dict = data["office"]
    office_id = office_dict["office_id"]
    recovery_blob = data["office_key_recovery"]

    # Reject foreign office kits if an office already exists on this PC
    existing_office = load_office(app_dir)
    if existing_office is not None:
        if not hmac.compare_digest(existing_office.office_id, office_dict["office_id"]) or \
           not hmac.compare_digest(existing_office.key_id, office_dict["key_id"]):
            raise KeyFileInvalid(
                "recovery kit belongs to a different office (%s, key_id=%s) than this PC (%s, key_id=%s)"
                % (
                    office_dict.get("office_name") or office_dict["office_id"],
                    office_dict["key_id"][:8],
                    existing_office.office_name or existing_office.office_id,
                    existing_office.key_id[:8],
                )
            )

    # Verify password against office_key_recovery
    dek = _unwrap_with_lenient_password(recovery_blob, password, recovery_aad(office_id))
    if len(dek) != DEK_LEN or not hmac.compare_digest(key_id(dek), office_dict["key_id"]):
        raise KeyFileInvalid("recovery kit DEK does not match office key_id")

    admin_blob = data.get("admin_key_recovery")
    if isinstance(admin_blob, dict):
        # Verify it unwraps too
        try:
            _unwrap_with_lenient_password(admin_blob, password, admin_aad(office_id))
        except WrongPassword:
            raise
        except Exception as e:
            raise KeyFileInvalid("admin recovery key in kit failed verification: %s" % e) from e

    kdir = keys_dir(app_dir)
    kdir.mkdir(parents=True, exist_ok=True)

    # 1. Save office.json (backs up existing if different)
    info = OfficeInfo(
        office_id=office_dict["office_id"],
        office_name=office_dict.get("office_name", ""),
        key_id=office_dict["key_id"],
        admin_pubkey=office_dict.get("admin_pubkey"),
        device_id=office_dict.get("device_id"),
        created_at=office_dict.get("created_at") or "",
        format=office_dict.get("format", OFFICE_FORMAT),
    )
    save_office(app_dir, info)

    # 2. Save office_key.recovery (backs up existing if different)
    _replace_key_file(kdir / DEK_RECOVERY_FILE, (json.dumps(recovery_blob, indent=2) + "\n").encode("utf-8"))

    # 3. Save admin_key.recovery if present
    if isinstance(admin_blob, dict):
        _replace_key_file(kdir / ADMIN_KEY_RECOVERY_FILE, (json.dumps(admin_blob, indent=2) + "\n").encode("utf-8"))

    # 4. Restore DPAPI if possible
    try:
        _replace_key_file(kdir / DEK_DPAPI_FILE, dpapi_protect(dek, ENTROPY_OFFICE_KEY))
    except OSError:
        pass

    return dek
