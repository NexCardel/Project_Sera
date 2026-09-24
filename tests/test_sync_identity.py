"""Tests for sync_identity.py (Sera Sync v3, WP P2-1).

All files live under pytest's tmp_path; the real data folder is never touched.
"""

import os
import ssl
import sys
import time

import pytest

import sync_identity
from sync_identity import DeviceIdentityUnavailable

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")


@windows_only
def test_ensure_device_identity_generates_files(tmp_path):
    identity = sync_identity.ensure_device_identity(tmp_path)
    kdir = tmp_path / "keys"
    assert (kdir / sync_identity.DEVICE_CERT_FILE).exists()
    assert (kdir / sync_identity.DEVICE_KEY_FILE).exists()
    assert (kdir / sync_identity.DEVICE_KEY_PASS_DPAPI_FILE).exists()
    assert len(identity.device_id) == 32
    int(identity.device_id, 16)  # hex


@windows_only
def test_ensure_device_identity_is_idempotent(tmp_path):
    first = sync_identity.ensure_device_identity(tmp_path)
    kdir = tmp_path / "keys"
    cert_bytes = (kdir / sync_identity.DEVICE_CERT_FILE).read_bytes()
    key_bytes = (kdir / sync_identity.DEVICE_KEY_FILE).read_bytes()

    second = sync_identity.ensure_device_identity(tmp_path)

    assert second.device_id == first.device_id
    assert (kdir / sync_identity.DEVICE_CERT_FILE).read_bytes() == cert_bytes
    assert (kdir / sync_identity.DEVICE_KEY_FILE).read_bytes() == key_bytes


@windows_only
def test_device_id_stable_across_loads(tmp_path):
    generated = sync_identity.ensure_device_identity(tmp_path)
    loaded = sync_identity.load_device_identity(tmp_path)
    assert loaded is not None
    assert loaded.device_id == generated.device_id
    assert loaded.cert_pem == generated.cert_pem


def test_load_device_identity_missing_returns_none(tmp_path):
    assert sync_identity.load_device_identity(tmp_path) is None


@windows_only
def test_key_pem_requires_passphrase(tmp_path):
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    identity = sync_identity.ensure_device_identity(tmp_path)
    with pytest.raises(TypeError):
        load_pem_private_key(identity.key_pem, password=None)
    with pytest.raises(ValueError):
        load_pem_private_key(identity.key_pem, password=b"wrong passphrase entirely")
    # The right passphrase works.
    load_pem_private_key(identity.key_pem, password=identity.passphrase)


@windows_only
def test_certificate_fields(tmp_path):
    from cryptography import x509
    from cryptography.x509.oid import NameOID

    identity = sync_identity.ensure_device_identity(tmp_path)
    cert = x509.load_pem_x509_certificate(identity.cert_pem)

    cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
    assert cn == identity.device_id

    basic_constraints = cert.extensions.get_extension_for_class(x509.BasicConstraints)
    assert basic_constraints.critical is True
    assert basic_constraints.value.ca is True
    assert basic_constraints.value.path_length is None

    key_usage = cert.extensions.get_extension_for_class(x509.KeyUsage)
    assert key_usage.critical is True
    assert key_usage.value.digital_signature is True
    assert key_usage.value.key_cert_sign is True

    validity_days = (cert.not_valid_after_utc - cert.not_valid_before_utc).days
    assert validity_days >= sync_identity.CERT_VALIDITY_DAYS - 1


@windows_only
def test_load_cert_chain_args_works_with_ssl(tmp_path):
    sync_identity.ensure_device_identity(tmp_path)
    certfile, keyfile, password = sync_identity.load_cert_chain_args(tmp_path)

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile, keyfile, password)  # raises if the passphrase is wrong


def test_load_cert_chain_args_without_identity_raises(tmp_path):
    with pytest.raises(DeviceIdentityUnavailable):
        sync_identity.load_cert_chain_args(tmp_path)


@windows_only
def test_load_device_identity_undecryptable_raises(tmp_path):
    sync_identity.ensure_device_identity(tmp_path)
    pass_path = tmp_path / "keys" / sync_identity.DEVICE_KEY_PASS_DPAPI_FILE
    blob = bytearray(pass_path.read_bytes())
    blob[-5] ^= 0x01
    pass_path.write_bytes(bytes(blob))
    with pytest.raises(DeviceIdentityUnavailable):
        sync_identity.load_device_identity(tmp_path)


@windows_only
def test_crash_before_cert_written_is_recovered_on_next_call(tmp_path):
    """A crash between the key/passphrase writes and the cert write must not
    strand the PC without an identity forever (review finding #1)."""
    first = sync_identity.ensure_device_identity(tmp_path)
    kdir = tmp_path / "keys"
    cert_path = kdir / sync_identity.DEVICE_CERT_FILE
    key_bytes = (kdir / sync_identity.DEVICE_KEY_FILE).read_bytes()
    cert_path.unlink()  # simulate a crash before the cert (written last) landed

    # load_device_identity is read-only: it reports "no identity" but must
    # not touch any file (see finding below: a concurrent loader must never
    # race a concurrent generator's in-progress write).
    assert sync_identity.load_device_identity(tmp_path) is None
    assert (kdir / sync_identity.DEVICE_KEY_FILE).exists()
    assert not list(kdir.glob(sync_identity.DEVICE_KEY_FILE + ".bak-*"))

    # ensure_device_identity, holding the lock, is the only thing allowed to
    # clear the stray files -- and it must back them up, not delete them.
    second = sync_identity.ensure_device_identity(tmp_path)
    assert second.device_id != first.device_id  # a fresh identity was generated
    assert cert_path.exists()
    backups = list(kdir.glob(sync_identity.DEVICE_KEY_FILE + ".bak-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == key_bytes


@windows_only
def test_concurrent_load_during_generation_does_not_disturb_writer(tmp_path):
    """A loader that runs while another caller is mid-generation (key and
    passphrase written, certificate not yet) must not move those files out
    from under the writer -- reproduces the race from the second review."""
    kdir = tmp_path / "keys"
    kdir.mkdir(parents=True)
    key_path = kdir / sync_identity.DEVICE_KEY_FILE
    pass_path = kdir / sync_identity.DEVICE_KEY_PASS_DPAPI_FILE
    key_path.write_bytes(b"pretend-encrypted-key-pem-mid-write")
    pass_path.write_bytes(b"pretend-dpapi-blob-mid-write")

    # A concurrent loader (e.g. load_cert_chain_args from another thread)
    # must see "no identity yet" without disturbing the in-progress write.
    assert sync_identity.load_device_identity(tmp_path) is None
    assert key_path.read_bytes() == b"pretend-encrypted-key-pem-mid-write"
    assert pass_path.read_bytes() == b"pretend-dpapi-blob-mid-write"
    assert not list(kdir.glob("*.bak-*"))


@windows_only
def test_load_device_identity_rejects_cert_not_matching_private_key(tmp_path):
    """The loader must not trust the cert's CN blindly (review finding #2)."""
    sync_identity.ensure_device_identity(tmp_path)
    other_dir = tmp_path / "other"
    other = sync_identity.ensure_device_identity(other_dir)

    kdir = tmp_path / "keys"
    (kdir / sync_identity.DEVICE_CERT_FILE).write_bytes(other.cert_pem)

    with pytest.raises(DeviceIdentityUnavailable):
        sync_identity.load_device_identity(tmp_path)


@windows_only
def test_load_device_identity_rejects_forged_device_id_in_cn(tmp_path):
    """A cert whose CN doesn't match sha256(SPKI) must be rejected, even if
    it still verifies against the stored private key's own public key
    (review finding #2)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    from cryptography.x509.oid import NameOID

    identity = sync_identity.ensure_device_identity(tmp_path)
    kdir = tmp_path / "keys"
    private_key = load_pem_private_key(identity.key_pem, password=identity.passphrase)

    forged_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "0" * 32)])
    forged_cert = (
        x509.CertificateBuilder()
        .subject_name(forged_name)
        .issuer_name(forged_name)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(x509.load_pem_x509_certificate(identity.cert_pem).not_valid_before_utc)
        .not_valid_after(x509.load_pem_x509_certificate(identity.cert_pem).not_valid_after_utc)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(private_key, hashes.SHA256())
    )
    (kdir / sync_identity.DEVICE_CERT_FILE).write_bytes(forged_cert.public_bytes(serialization.Encoding.PEM))

    with pytest.raises(DeviceIdentityUnavailable):
        sync_identity.load_device_identity(tmp_path)


@windows_only
def test_ensure_device_identity_stale_lock_is_stolen(tmp_path):
    """A lock file left behind by a dead process must not block generation
    forever (review finding #3)."""
    kdir = tmp_path / "keys"
    kdir.mkdir(parents=True)
    lock_path = kdir / sync_identity.DEVICE_IDENTITY_LOCK_FILE
    lock_path.touch()
    stale_time = time.time() - sync_identity._LOCK_STALE_SECONDS - 5
    os.utime(lock_path, (stale_time, stale_time))

    identity = sync_identity.ensure_device_identity(tmp_path)
    assert identity is not None
    assert not lock_path.exists()


def test_identity_lock_timeout_raises_and_does_not_steal_live_lock(monkeypatch, tmp_path):
    """A lock held by a live process must not be timed out past silently, and
    on timeout the caller must not delete a lock it never owned (review
    finding: 'not blocking' item on the lock)."""
    monkeypatch.setattr(sync_identity, "_LOCK_ACQUIRE_TIMEOUT_SECONDS", 0.2)
    # _LOCK_STALE_SECONDS stays at its normal (much larger) default, so the
    # fresh lock below looks live, not abandoned, for the whole wait.
    lock_path = tmp_path / "identity.lock"
    lock_path.touch()
    with pytest.raises(TimeoutError):
        with sync_identity._IdentityLock(lock_path):
            pass
    assert lock_path.exists()  # not deleted -- this caller never owned it


def test_no_pyside6_import():
    import ast
    import pathlib

    module_path = pathlib.Path(__file__).resolve().parent.parent / "sync_identity.py"
    src = module_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("PySide6")
        elif isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("PySide6")
