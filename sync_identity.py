"""
sync_identity.py
-----------------
Per-device identity for Sera Sync v3 (blueprint §4, WP P2-1).

Each PC generates its own EC P-256 key and a self-signed X.509 certificate
the first time it needs one. The certificate's subject CN is the device id
(derived from the public key). Other PCs in the office come to trust it via
the signed membership records (P2-2) and mutual TLS (P2-3); this module only
creates and loads the identity, it doesn't distribute or verify it.

Files, all under ``keys/`` (see ``sera_keys.keys_dir``):
  - ``device_cert.pem``       self-signed certificate (PEM, not secret)
  - ``device_key.pem``        EC private key (PEM), encrypted with a random
                               passphrase -- never with the master password
  - ``device_key.pass.dpapi`` that passphrase, DPAPI-protected (CurrentUser)

Never log or print the private key, its passphrase, or DPAPI blobs. No
PySide6 here. Heavy imports (cryptography) are done lazily.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sera_keys

DEVICE_CERT_FILE = "device_cert.pem"
DEVICE_KEY_FILE = "device_key.pem"
DEVICE_KEY_PASS_DPAPI_FILE = "device_key.pass.dpapi"
DEVICE_IDENTITY_LOCK_FILE = "device_identity.lock"

DEVICE_KEY_PASSPHRASE_LEN = 32
CERT_VALIDITY_DAYS = 20 * 365
# How long this caller personally waits for the lock before giving up.
_LOCK_ACQUIRE_TIMEOUT_SECONDS = 10.0
# How old (by file mtime) a lock must be before it's treated as abandoned by
# a dead process and stolen. Bigger than the acquire timeout on purpose: a
# lock a live process still holds past our own timeout is not yet "stale"
# just because we gave up waiting on it (see _IdentityLock).
_LOCK_STALE_SECONDS = 30.0


class DeviceIdentityError(Exception):
    """Base class for errors raised by this module."""


class DeviceIdentityUnavailable(DeviceIdentityError):
    """The identity can't be loaded on this PC (missing/damaged file, or DPAPI can't decrypt)."""


@dataclass
class DeviceIdentity:
    device_id: str
    cert_pem: bytes
    key_pem: bytes       # encrypted PEM (BestAvailableEncryption)
    passphrase: bytes    # decrypts key_pem


def _device_id_from_public_key(public_key) -> str:
    from cryptography.hazmat.primitives import serialization
    spki = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(spki).hexdigest()[:32]


def _generate() -> DeviceIdentity:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    private_key = ec.generate_private_key(ec.SECP256R1())
    device_id = _device_id_from_public_key(private_key.public_key())

    now = datetime.now(timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, device_id)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=CERT_VALIDITY_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_cert_sign=True,
                content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False,
                crl_sign=False, encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .sign(private_key, hashes.SHA256())
    )

    passphrase = os.urandom(DEVICE_KEY_PASSPHRASE_LEN)
    key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(passphrase),
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    return DeviceIdentity(device_id=device_id, cert_pem=cert_pem, key_pem=key_pem, passphrase=passphrase)


def _paths(app_dir):
    kdir = sera_keys.keys_dir(app_dir)
    return (
        kdir,
        kdir / DEVICE_CERT_FILE,
        kdir / DEVICE_KEY_FILE,
        kdir / DEVICE_KEY_PASS_DPAPI_FILE,
        kdir / DEVICE_IDENTITY_LOCK_FILE,
    )


def _backup_stray(path: Path) -> None:
    """Move a leftover file aside as ``*.bak-<ts>`` instead of deleting it (§0 rule 3).

    Raises ``OSError`` if the rename fails, rather than silently leaving the
    file for a caller to overwrite without a backup.
    """
    if not path.exists():
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_name("%s.bak-%s" % (path.name, ts))
    n = 1
    while backup.exists():
        backup = path.with_name("%s.bak-%s_%d" % (path.name, ts, n))
        n += 1
    path.replace(backup)


def load_device_identity(app_dir) -> DeviceIdentity | None:
    """Load the identity already on this PC, or ``None`` if none was generated yet.

    Side-effect free (does not touch any file): a concurrent
    ``ensure_device_identity`` elsewhere may be mid-write, between the
    key/passphrase and the certificate. Only ``ensure_device_identity``,
    while holding the identity lock, may clear out an incomplete identity.

    Raises ``DeviceIdentityUnavailable`` if the files are present but can't
    be read or decrypted (damaged file, tampered/mismatched cert or key, or
    this Windows account can't decrypt the passphrase).
    """
    kdir, cert_path, key_path, pass_path, _ = _paths(app_dir)
    if not cert_path.exists():
        # The cert is written last (see _write_new). Its absence means either
        # nothing was ever generated, or another writer hasn't gotten to it
        # yet (possibly mid-write right now) -- either way, "no identity
        # yet", not an error.
        return None
    try:
        cert_pem = cert_path.read_bytes()
        key_pem = key_path.read_bytes()
        pass_blob = pass_path.read_bytes()
    except OSError as e:
        raise DeviceIdentityUnavailable("device identity file missing or unreadable (%s)" % e) from None
    try:
        passphrase = sera_keys.dpapi_unprotect(pass_blob, sera_keys.ENTROPY_DEVICE_KEY)
    except OSError:
        raise DeviceIdentityUnavailable("this Windows account can't decrypt %s" % pass_path) from None

    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    from cryptography.x509.oid import NameOID
    try:
        private_key = load_pem_private_key(key_pem, password=passphrase)
        cert = x509.load_pem_x509_certificate(cert_pem)
        device_id = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value

        def _spki(public_key) -> bytes:
            return public_key.public_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )

        cert_spki = _spki(cert.public_key())
        if device_id != hashlib.sha256(cert_spki).hexdigest()[:32]:
            raise ValueError("device_id does not match the certificate's public key")
        if _spki(private_key.public_key()) != cert_spki:
            raise ValueError("private key does not match the certificate's public key")
    except Exception as e:
        raise DeviceIdentityUnavailable("device identity is damaged (%s)" % e.__class__.__name__) from None

    return DeviceIdentity(device_id=device_id, cert_pem=cert_pem, key_pem=key_pem, passphrase=passphrase)


def _write_new(app_dir, identity: DeviceIdentity) -> None:
    kdir, cert_path, key_path, pass_path, _ = _paths(app_dir)
    kdir.mkdir(parents=True, exist_ok=True)
    # Compute the DPAPI blob before writing anything, so a DPAPI failure
    # leaves no files behind (mirrors sera_keys.store_dek). The cert is
    # written last: its presence is what load_device_identity treats as
    # "an identity was fully generated" (see the crash-recovery note there).
    pass_blob = sera_keys.dpapi_protect(identity.passphrase, sera_keys.ENTROPY_DEVICE_KEY)
    sera_keys.atomic_write(key_path, identity.key_pem)
    sera_keys.atomic_write(pass_path, pass_blob)
    sera_keys.atomic_write(cert_path, identity.cert_pem)


class _IdentityLock:
    """Best-effort cross-process lock so two first-time callers don't interleave writes.

    A stale lock (left behind by a process that died while holding it) is
    stolen after ``_LOCK_STALE_SECONDS``. Raises ``TimeoutError`` rather than
    proceeding without the lock if a live holder keeps it that long, since
    proceeding unlocked is exactly the race this exists to prevent.
    """

    def __init__(self, path: Path):
        self.path = path
        self._owned = False

    def __enter__(self):
        deadline = time.monotonic() + _LOCK_ACQUIRE_TIMEOUT_SECONDS
        while True:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                self._owned = True
                return self
            except FileExistsError:
                try:
                    age = time.time() - self.path.stat().st_mtime
                except OSError:
                    age = 0.0
                if age > _LOCK_STALE_SECONDS:
                    try:
                        self.path.unlink()
                    except OSError:
                        pass
                    continue
                if time.monotonic() >= deadline:
                    raise TimeoutError("could not acquire %s (still held after %.0fs)"
                                        % (self.path, _LOCK_ACQUIRE_TIMEOUT_SECONDS)) from None
                time.sleep(0.05)

    def __exit__(self, *exc_info):
        if self._owned:
            try:
                self.path.unlink()
            except OSError:
                pass


def _clear_incomplete_identity(app_dir) -> None:
    """Move aside a leftover key/passphrase file with no matching certificate.

    Only called from ``ensure_device_identity`` while holding the identity
    lock, immediately before generating -- never from ``load_device_identity``,
    which must stay side-effect free (see its docstring).
    """
    _, cert_path, key_path, pass_path, _ = _paths(app_dir)
    if cert_path.exists():
        return
    _backup_stray(key_path)
    _backup_stray(pass_path)


def ensure_device_identity(app_dir) -> DeviceIdentity:
    """Return this PC's device identity, generating it on first office-mode start.

    Idempotent: a later call loads the existing identity instead of
    regenerating it (``device_id`` stays stable).
    """
    existing = load_device_identity(app_dir)
    if existing is not None:
        return existing
    kdir, _, _, _, lock_path = _paths(app_dir)
    kdir.mkdir(parents=True, exist_ok=True)
    with _IdentityLock(lock_path):
        # Re-check under the lock: another thread/process may have generated
        # the identity, or be mid-way through generating one, while we were
        # waiting. Only now -- holding the lock, about to generate -- is it
        # safe to clear out a leftover partial identity (§0 rule 3: backed
        # up, not deleted).
        existing = load_device_identity(app_dir)
        if existing is not None:
            return existing
        _clear_incomplete_identity(app_dir)
        identity = _generate()
        _write_new(app_dir, identity)
        return identity


def load_cert_chain_args(app_dir):
    """``(certfile, keyfile, password)`` for ``ssl.SSLContext.load_cert_chain``.

    Raises ``DeviceIdentityUnavailable`` if no identity exists yet, or it
    can't be decrypted on this PC. Call ``ensure_device_identity`` first.
    """
    _, cert_path, key_path, _, _ = _paths(app_dir)
    identity = load_device_identity(app_dir)
    if identity is None:
        raise DeviceIdentityUnavailable("no device identity on this PC yet")
    return str(cert_path), str(key_path), identity.passphrase
