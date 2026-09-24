"""
sync_admin.py
-------------
Office admin key and admin-signed records for Sera Sync v3 (blueprint §4.2, WP P2-2).

- One Ed25519 key pair per office. The public key is ``admin_pubkey`` in
  ``keys/office.json``. The private key is stored as ``keys/admin_key.dpapi``
  (DPAPI, admin PC only) and ``keys/admin_key.recovery`` (wrapped with the
  master password, AAD ``sera_keys.admin_aad(office_id)``), which is copied to
  every member so any PC can become admin with the master password (D3).
- A signed record is a dict whose ``sig`` is a base64 Ed25519 signature over
  ``canonical_json(record without "sig")`` (``sync_peer.canonical_json``, P0-7).
- Record types: ``member``, ``office_admin`` (both stored in master.db table
  ``_sync_members``) and ``staff_change`` (signed/verified here, used by P3-4).
- Rule: a record with a higher ``rev`` and a valid signature replaces a lower one.

Never log or print the admin private key, passwords or blobs. No PySide6 here.
Heavy imports (cryptography) are done lazily.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone

import sera_keys

MEMBERS_TABLE = "_sync_members"
# The single office_admin record lives in _sync_members under this key. Real
# device ids are 32 lowercase hex chars, so it can't collide with one.
OFFICE_ADMIN_ROW = "office_admin"

RECORD_MEMBER = "member"
RECORD_OFFICE_ADMIN = "office_admin"
RECORD_STAFF_CHANGE = "staff_change"

ROLE_MEMBER = "member"
ROLE_ADMIN = "admin"

ADMIN_KEY_LEN = 32
_SIG_LEN = 64
_MAX_REV = 2 ** 62
_MAX_NAME_LEN = 200

_HEX32 = re.compile(r"[0-9a-f]{32}\Z")
_TOKEN_LETTER = re.compile(r"[A-Z]{1,3}\Z")

_FIELDS = {
    RECORD_MEMBER: frozenset({"type", "device_id", "name", "cert_pem", "role", "token_letter",
                              "added_at", "revoked_at", "rev", "sig"}),
    RECORD_OFFICE_ADMIN: frozenset({"type", "device_id", "since", "rev", "sig"}),
}


class SyncAdminError(Exception):
    """Base class for errors raised by this module."""


class InvalidRecord(SyncAdminError, ValueError):
    """A record is malformed, or its signature doesn't verify with the office admin key."""


class AdminKeyUnavailable(SyncAdminError):
    """This PC can't load the office admin private key (not stored here, or can't be decrypted)."""


class NotAdmin(SyncAdminError):
    """This PC is not the one named in the current office_admin record, so it must not sign."""


class MembershipError(SyncAdminError):
    """A membership change isn't allowed (not a member, already revoked, ...)."""


def _canonical_json(obj) -> bytes:
    from sync_peer import canonical_json
    return canonical_json(obj)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- keys

def generate_admin_key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    return Ed25519PrivateKey.generate()


def _raw_private(private_key) -> bytes:
    from cryptography.hazmat.primitives import serialization
    return private_key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                     serialization.NoEncryption())


def _private_from_raw(raw: bytes):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    return Ed25519PrivateKey.from_private_bytes(raw)


def public_key_b64(key) -> str:
    """Base64 of the raw 32-byte Ed25519 public key (the ``admin_pubkey`` format). Accepts a private or public key."""
    from cryptography.hazmat.primitives import serialization
    public = key.public_key() if hasattr(key, "public_key") else key
    raw = public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def _load_public_key(admin_pubkey):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    if not isinstance(admin_pubkey, str) or not admin_pubkey:
        raise InvalidRecord("office admin public key is missing")
    try:
        raw = base64.b64decode(admin_pubkey, validate=True)
    except (binascii.Error, ValueError):
        raise InvalidRecord("office admin public key is not valid base64") from None
    if len(raw) != ADMIN_KEY_LEN:
        raise InvalidRecord("office admin public key has the wrong length")
    return Ed25519PublicKey.from_public_bytes(raw)


def write_admin_key_files(app_dir, private_key, password: str, office_id: str) -> str:
    """Write ``admin_key.recovery`` (password) and ``admin_key.dpapi`` (DPAPI). Returns the public key (b64).

    Does not touch office.json; the caller records ``admin_pubkey`` there afterwards.
    Both blobs are computed before either file is written, so a DPAPI failure writes
    nothing. Existing different files are kept as ``*.bak-<ts>`` (§0 rule 3).
    """
    if not office_id:
        raise ValueError("office_id is required")
    raw = _raw_private(private_key)
    recovery = sera_keys.wrap_with_password(raw, password, sera_keys.admin_aad(office_id))
    dpapi_blob = sera_keys.dpapi_protect(raw, sera_keys.ENTROPY_ADMIN_KEY)
    kdir = sera_keys.keys_dir(app_dir)
    kdir.mkdir(parents=True, exist_ok=True)
    sera_keys._replace_key_file(kdir / sera_keys.ADMIN_KEY_RECOVERY_FILE,
                                (json.dumps(recovery, indent=2) + "\n").encode("utf-8"))
    sera_keys._replace_key_file(kdir / sera_keys.ADMIN_KEY_DPAPI_FILE, dpapi_blob)
    return public_key_b64(private_key)


def _read_json_key_file(path, missing_exc):
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise missing_exc("%s not found" % path) from None
    try:
        return json.loads(raw)
    except ValueError:
        raise sera_keys.KeyFileInvalid("%s is not valid JSON" % path) from None


def _verified_master_password(app_dir, office, password: str) -> str:
    """The form of ``password`` (as typed, else stripped) that opens office_key.recovery.

    Raises ``sera_keys.WrongPassword`` if neither does.
    """
    path = sera_keys.keys_dir(app_dir) / sera_keys.DEK_RECOVERY_FILE
    blob = _read_json_key_file(path, sera_keys.KeyUnavailable)
    aad = sera_keys.recovery_aad(office.office_id)
    candidates = [password]
    if isinstance(password, str) and password.strip() and password.strip() != password:
        candidates.append(password.strip())
    for i, candidate in enumerate(candidates):
        try:
            dek = sera_keys.unwrap_with_password(blob, candidate, aad)
        except sera_keys.WrongPassword:
            if i == len(candidates) - 1:
                raise
            continue
        if not hmac.compare_digest(sera_keys.key_id(dek), office.key_id):
            raise sera_keys.KeyFileInvalid("%s does not match the key id in %s" % (path, sera_keys.OFFICE_FILE))
        return candidate
    raise sera_keys.WrongPassword("wrong password")  # not reached


def create_admin_key(app_dir, password: str) -> str:
    """Create the office admin key for an office that has none yet; returns ``admin_pubkey``.

    For the admin PC after P1-4 (office.json with ``admin_pubkey`` null) and for a new
    office. The master password is checked against ``office_key.recovery`` first, so
    ``admin_key.recovery`` is always wrapped with the same password as the DEK.
    office.json is written last. Raises ``sera_keys.WrongPassword`` with nothing changed.
    """
    office = sera_keys.load_office(app_dir)
    if office is None:
        raise sera_keys.KeyUnavailable("no office key on this PC (keys/%s missing)" % sera_keys.OFFICE_FILE)
    if office.admin_pubkey:
        raise SyncAdminError("this office already has an admin key")
    password = _verified_master_password(app_dir, office, password)
    pub = write_admin_key_files(app_dir, generate_admin_key(), password, office.office_id)
    office.admin_pubkey = pub
    sera_keys.save_office(app_dir, office)
    return pub


def load_admin_key(app_dir):
    """The office admin private key from ``admin_key.dpapi``. Raises ``AdminKeyUnavailable``.

    The key must match ``admin_pubkey`` in office.json.
    """
    try:
        office = sera_keys.load_office(app_dir)
    except sera_keys.KeyFileInvalid as e:
        raise AdminKeyUnavailable(str(e)) from None
    if office is None or not office.admin_pubkey:
        raise AdminKeyUnavailable("this office has no admin key")
    path = sera_keys.keys_dir(app_dir) / sera_keys.ADMIN_KEY_DPAPI_FILE
    try:
        blob = path.read_bytes()
    except FileNotFoundError:
        raise AdminKeyUnavailable("this PC does not hold the office admin key") from None
    except OSError as e:
        raise AdminKeyUnavailable("%s can't be read (%s)" % (path, e.__class__.__name__)) from None
    try:
        raw = sera_keys.dpapi_unprotect(blob, sera_keys.ENTROPY_ADMIN_KEY)
    except OSError:
        raise AdminKeyUnavailable("this Windows account can't decrypt %s" % path) from None
    if len(raw) != ADMIN_KEY_LEN:
        raise AdminKeyUnavailable("%s does not hold a valid key" % path)
    key = _private_from_raw(raw)
    if not hmac.compare_digest(public_key_b64(key), office.admin_pubkey):
        raise AdminKeyUnavailable("%s does not match admin_pubkey in %s" % (path, sera_keys.OFFICE_FILE))
    return key


def has_admin_key(app_dir) -> bool:
    try:
        load_admin_key(app_dir)
        return True
    except AdminKeyUnavailable:
        return False


# ---------------------------------------------------------------- records

def device_id_from_cert_pem(cert_pem) -> str:
    """``sha256(SPKI DER of the cert's public key).hexdigest()[:32]`` (same as sync_identity)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    if isinstance(cert_pem, str):
        cert_pem = cert_pem.encode("ascii", errors="strict")
    try:
        cert = x509.load_pem_x509_certificate(cert_pem)
        spki = cert.public_key().public_bytes(serialization.Encoding.DER,
                                              serialization.PublicFormat.SubjectPublicKeyInfo)
    except Exception:
        raise InvalidRecord("cert_pem is not a valid certificate") from None
    return hashlib.sha256(spki).hexdigest()[:32]


def token_letter_for_index(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA, 27 -> AB, ... (D8)."""
    if index < 0:
        raise ValueError("index must be >= 0")
    n = index + 1
    out = []
    while n:
        n, r = divmod(n - 1, 26)
        out.append(chr(ord("A") + r))
    return "".join(reversed(out))


def token_letter_index(letter: str) -> int:
    n = 0
    for ch in letter:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def _check_rev(rec: dict) -> None:
    rev = rec.get("rev")
    if type(rev) is not int or not (1 <= rev <= _MAX_REV):
        raise InvalidRecord("bad rev")


def _check_str(rec: dict, name: str, max_len: int | None = None) -> None:
    value = rec.get(name)
    if not isinstance(value, str) or not value.strip():
        raise InvalidRecord("bad %s" % name)
    if max_len is not None and len(value) > max_len:
        raise InvalidRecord("%s too long" % name)


def _check_shape(rec: dict) -> str:
    """Field checks that don't need the signature. Returns the record type."""
    if not isinstance(rec, dict):
        raise InvalidRecord("record is not an object")
    rtype = rec.get("type")
    if rtype == RECORD_STAFF_CHANGE:
        return rtype
    fields = _FIELDS.get(rtype)
    if fields is None:
        raise InvalidRecord("unknown record type")
    if set(rec) != fields:
        raise InvalidRecord("%s record has missing or unexpected fields" % rtype)
    if not isinstance(rec["device_id"], str) or not _HEX32.match(rec["device_id"]):
        raise InvalidRecord("bad device_id")
    _check_rev(rec)
    if rtype == RECORD_OFFICE_ADMIN:
        _check_str(rec, "since")
        return rtype
    _check_str(rec, "name", _MAX_NAME_LEN)
    _check_str(rec, "cert_pem")
    _check_str(rec, "added_at")
    if rec["role"] not in (ROLE_MEMBER, ROLE_ADMIN):
        raise InvalidRecord("bad role")
    if not isinstance(rec["token_letter"], str) or not _TOKEN_LETTER.match(rec["token_letter"]):
        raise InvalidRecord("bad token_letter")
    if rec["revoked_at"] is not None:
        _check_str(rec, "revoked_at")
    return rtype


def _body(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if k != "sig"}


def verify_record(record: dict, admin_pubkey: str) -> dict:
    """Check the record's fields and its signature against ``admin_pubkey``. Returns a copy.

    Raises ``InvalidRecord``. A member record's ``cert_pem`` must also belong to its
    ``device_id``.
    """
    from cryptography.exceptions import InvalidSignature
    rtype = _check_shape(record)
    public_key = _load_public_key(admin_pubkey)
    sig_b64 = record.get("sig")
    if not isinstance(sig_b64, str):
        raise InvalidRecord("record is not signed")
    try:
        sig = base64.b64decode(sig_b64, validate=True)
    except (binascii.Error, ValueError):
        raise InvalidRecord("signature is not valid base64") from None
    if len(sig) != _SIG_LEN:
        raise InvalidRecord("signature has the wrong length")
    try:
        public_key.verify(sig, _canonical_json(_body(record)))
    except InvalidSignature:
        raise InvalidRecord("signature does not verify with the office admin key") from None
    if rtype == RECORD_MEMBER and device_id_from_cert_pem(record["cert_pem"]) != record["device_id"]:
        raise InvalidRecord("cert_pem does not belong to device_id")
    return dict(record)


def sign_record(record: dict, private_key) -> dict:
    """Return ``record`` with ``sig`` set. The result is verified before it is returned."""
    body = _body(record)
    sig = private_key.sign(_canonical_json(body))
    signed = dict(body, sig=base64.b64encode(sig).decode("ascii"))
    return verify_record(signed, public_key_b64(private_key))


# ---------------------------------------------------------------- storage (master.db)

def ensure_members_table(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS %s ("
        "device_id TEXT PRIMARY KEY, record_json TEXT NOT NULL, rev INTEGER NOT NULL)" % MEMBERS_TABLE
    )


@contextmanager
def _transaction(conn):
    """BEGIN IMMEDIATE ... COMMIT, unless the caller already has a transaction open (then it commits)."""
    if conn.in_transaction:
        yield
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


def _row_key(record: dict) -> str:
    return OFFICE_ADMIN_ROW if record["type"] == RECORD_OFFICE_ADMIN else record["device_id"]


def _replaces(new: dict, new_text: str, old: dict | None, old_text: str) -> bool:
    """Does ``new`` replace the stored ``old`` record (same row)?"""
    if old is None:
        return True
    if new["type"] == RECORD_MEMBER and old.get("type") == RECORD_MEMBER:
        # A member's letter and certificate never change (D8: letters are never reused).
        if new["token_letter"] != old.get("token_letter") or new["cert_pem"] != old.get("cert_pem"):
            return False
        # Removal is final, at any rev: an active record never replaces a revoked one, and a
        # revoke always replaces an active one. Otherwise a revoke and a role change signed at
        # the same rev (admin hand-over race) could leave a removed PC in the office.
        new_revoked = new["revoked_at"] is not None
        old_revoked = old.get("revoked_at") is not None
        if new_revoked != old_revoked:
            return new_revoked
    old_rev = old.get("rev") if type(old.get("rev")) is int else 0
    return (new["rev"], new_text) > (old_rev, old_text)


def store_record(conn, record: dict, admin_pubkey: str) -> bool:
    """Verify and store a member / office_admin record. True if it was stored.

    - A higher ``rev`` replaces a lower one.
    - Two different records with the same ``rev`` (e.g. signed by two PCs during an
      admin hand-over): the one whose canonical JSON sorts higher is kept, so every PC
      ends up with the same record whatever order they arrive in.
    - Member records: a revoke is final (it replaces an active record at any rev, and an
      active record never replaces it), and ``token_letter`` / ``cert_pem`` never change.
    """
    rec = verify_record(record, admin_pubkey)
    if rec["type"] not in (RECORD_MEMBER, RECORD_OFFICE_ADMIN):
        raise InvalidRecord("%s records are not stored in %s" % (rec["type"], MEMBERS_TABLE))
    text = _canonical_json(rec).decode("utf-8")
    key = _row_key(rec)
    with _transaction(conn):
        ensure_members_table(conn)
        row = conn.execute("SELECT record_json FROM %s WHERE device_id = ?" % MEMBERS_TABLE, (key,)).fetchone()
        old, old_text = None, ""
        if row is not None:
            old_text = row[0]
            try:
                old = json.loads(old_text)
            except ValueError:
                old = None
            if not isinstance(old, dict):
                old = None
        if row is not None and old is None:
            # Unreadable row: only a record that verifies replaces it.
            old = {"rev": 0}
        if not _replaces(rec, text, old, old_text):
            return False
        conn.execute("INSERT OR REPLACE INTO %s (device_id, record_json, rev) VALUES (?, ?, ?)" % MEMBERS_TABLE,
                     (key, text, rec["rev"]))
        return True


def _parse_row(text: str, admin_pubkey: str, rtype: str) -> dict | None:
    try:
        rec = verify_record(json.loads(text), admin_pubkey)
    except (ValueError, InvalidRecord):
        return None
    return rec if rec.get("type") == rtype else None


def _stored_rev(conn, row_key: str) -> int:
    row = conn.execute("SELECT rev FROM %s WHERE device_id = ?" % MEMBERS_TABLE, (row_key,)).fetchone()
    return int(row[0]) if row else 0


def get_member(conn, device_id: str, admin_pubkey: str) -> dict | None:
    """The stored member record, re-verified with ``admin_pubkey``. None if absent or it doesn't verify."""
    ensure_members_table(conn)
    if device_id == OFFICE_ADMIN_ROW:
        return None
    row = conn.execute("SELECT record_json FROM %s WHERE device_id = ?" % MEMBERS_TABLE, (device_id,)).fetchone()
    return _parse_row(row[0], admin_pubkey, RECORD_MEMBER) if row else None


def list_members(conn, admin_pubkey: str, include_revoked: bool = True) -> list[dict]:
    """Member records that verify with ``admin_pubkey``, in the order they were added (token letter order).

    Rows that don't verify are left out. For the trust store (P2-3) use ``include_revoked=False``.
    """
    ensure_members_table(conn)
    rows = conn.execute("SELECT record_json FROM %s WHERE device_id != ?" % MEMBERS_TABLE,
                        (OFFICE_ADMIN_ROW,)).fetchall()
    out = []
    for (text,) in rows:
        rec = _parse_row(text, admin_pubkey, RECORD_MEMBER)
        if rec is None or (not include_revoked and rec["revoked_at"] is not None):
            continue
        out.append(rec)
    out.sort(key=lambda r: token_letter_index(r["token_letter"]))
    return out


def get_office_admin(conn, admin_pubkey: str) -> dict | None:
    """The stored office_admin record, re-verified with ``admin_pubkey``. None if absent or it doesn't verify."""
    ensure_members_table(conn)
    row = conn.execute("SELECT record_json FROM %s WHERE device_id = ?" % MEMBERS_TABLE,
                       (OFFICE_ADMIN_ROW,)).fetchone()
    return _parse_row(row[0], admin_pubkey, RECORD_OFFICE_ADMIN) if row else None


def next_token_letter(conn) -> str:
    """The letter after the highest one ever handed out. Revoked members keep theirs, so it is never reused.

    Deliberately reads every row without re-verifying: for uniqueness it is safer to
    skip past a letter in a damaged row than to hand it out again.
    """
    ensure_members_table(conn)
    highest = -1
    rows = conn.execute("SELECT record_json FROM %s WHERE device_id != ?" % MEMBERS_TABLE,
                        (OFFICE_ADMIN_ROW,)).fetchall()
    for (text,) in rows:
        try:
            letter = json.loads(text).get("token_letter")
        except (ValueError, AttributeError):
            continue
        if isinstance(letter, str) and _TOKEN_LETTER.match(letter):
            highest = max(highest, token_letter_index(letter))
    return token_letter_for_index(highest + 1)


# ---------------------------------------------------------------- admin operations

def _office_pubkey(app_dir) -> str:
    office = sera_keys.load_office(app_dir)
    if office is None or not office.admin_pubkey:
        raise AdminKeyUnavailable("this office has no admin key")
    return office.admin_pubkey


def _require_named_admin(app_dir, conn, device_id: str):
    """(private key, admin_pubkey) if this PC is the one named in office_admin and holds the key."""
    pub = _office_pubkey(app_dir)
    current = get_office_admin(conn, admin_pubkey=pub)
    if current is None or current["device_id"] != device_id:
        raise NotAdmin("this PC is not the office admin PC")
    return load_admin_key(app_dir), pub


def _check_name(name) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("device name is required")
    name = name.strip()
    if len(name) > _MAX_NAME_LEN:
        raise ValueError("device name is too long")
    return name


def init_office_membership(app_dir, conn, cert_pem: str, device_name: str) -> tuple[dict, dict]:
    """On the PC that created the office: sign its own member record (letter A, role admin)
    and the first office_admin record. Refused if the office already has membership records.
    """
    key = load_admin_key(app_dir)
    pub = public_key_b64(key)
    device_id = device_id_from_cert_pem(cert_pem)
    name = _check_name(device_name)
    now = _now_iso()
    with _transaction(conn):
        ensure_members_table(conn)
        if conn.execute("SELECT 1 FROM %s LIMIT 1" % MEMBERS_TABLE).fetchone():
            raise MembershipError("this office already has membership records")
        member = sign_record({
            "type": RECORD_MEMBER, "device_id": device_id, "name": name, "cert_pem": cert_pem,
            "role": ROLE_ADMIN, "token_letter": token_letter_for_index(0),
            "added_at": now, "revoked_at": None, "rev": 1,
        }, key)
        office_admin = sign_record({"type": RECORD_OFFICE_ADMIN, "device_id": device_id, "since": now, "rev": 1}, key)
        store_record(conn, member, pub)
        store_record(conn, office_admin, pub)
    return member, office_admin


def add_member(app_dir, conn, admin_device_id: str, cert_pem: str, name: str) -> dict:
    """Admin PC only: sign a member record for a new device with the next token letter.

    An already active member gets its existing record back. A revoked device is refused
    (its letter must not be handed out again, and re-admitting it is a new identity).
    """
    key, pub = _require_named_admin(app_dir, conn, admin_device_id)
    device_id = device_id_from_cert_pem(cert_pem)
    name = _check_name(name)
    with _transaction(conn):
        existing = get_member(conn, device_id, admin_pubkey=pub)
        if existing is not None:
            if existing["revoked_at"] is not None:
                raise MembershipError("this PC was removed from the office; it needs a new device identity to rejoin")
            return existing
        record = sign_record({
            "type": RECORD_MEMBER, "device_id": device_id, "name": name, "cert_pem": cert_pem,
            "role": ROLE_MEMBER, "token_letter": next_token_letter(conn),
            "added_at": _now_iso(), "revoked_at": None, "rev": _stored_rev(conn, device_id) + 1,
        }, key)
        store_record(conn, record, pub)
    return record


def revoke_member(app_dir, conn, admin_device_id: str, device_id: str) -> dict:
    """Admin PC only: sign a revoke (``revoked_at`` set, ``rev`` + 1). The admin PC can't revoke itself."""
    key, pub = _require_named_admin(app_dir, conn, admin_device_id)
    if device_id == admin_device_id:
        raise MembershipError("the admin PC can't remove itself; hand over admin first")
    with _transaction(conn):
        existing = get_member(conn, device_id, admin_pubkey=pub)
        if existing is None:
            raise MembershipError("that PC is not a member of this office")
        if existing["revoked_at"] is not None:
            return existing
        record = sign_record(dict(_body(existing), revoked_at=_now_iso(),
                                  rev=max(existing["rev"], _stored_rev(conn, device_id)) + 1), key)
        store_record(conn, record, pub)
    return record


def claim_admin(app_dir, conn, password: str, device_id: str) -> dict:
    """Make this PC the office admin PC with the master password (D3). Returns the office_admin record.

    Unwraps ``admin_key.recovery``, signs a new office_admin record (``rev`` + 1) naming
    this PC, updates the two member records' roles, then stores ``admin_key.dpapi``.
    A wrong password (``sera_keys.WrongPassword``) or a PC that isn't an active member
    (``MembershipError``) changes nothing. If this PC is already named, only the DPAPI
    copy of the key is restored. Must be called with no transaction open on ``conn``.
    """
    office = sera_keys.load_office(app_dir)
    if office is None:
        raise sera_keys.KeyUnavailable("no office key on this PC (keys/%s missing)" % sera_keys.OFFICE_FILE)
    if not office.admin_pubkey:
        raise AdminKeyUnavailable("this office has no admin key")
    pub = office.admin_pubkey
    kdir = sera_keys.keys_dir(app_dir)
    blob = _read_json_key_file(kdir / sera_keys.ADMIN_KEY_RECOVERY_FILE, AdminKeyUnavailable)
    raw = sera_keys._unwrap_with_lenient_password(blob, password, sera_keys.admin_aad(office.office_id))
    if len(raw) != ADMIN_KEY_LEN:
        raise sera_keys.KeyFileInvalid("%s does not hold a valid key" % sera_keys.ADMIN_KEY_RECOVERY_FILE)
    key = _private_from_raw(raw)
    if not hmac.compare_digest(public_key_b64(key), pub):
        raise sera_keys.KeyFileInvalid("%s does not match admin_pubkey in %s"
                                       % (sera_keys.ADMIN_KEY_RECOVERY_FILE, sera_keys.OFFICE_FILE))
    if conn.in_transaction:
        # admin_key.dpapi is written after COMMIT; with the caller's transaction it could
        # be written for records the caller later rolls back.
        raise SyncAdminError("claim_admin must not run inside an open transaction")
    # Before any write, so a DPAPI failure changes nothing.
    dpapi_blob = sera_keys.dpapi_protect(raw, sera_keys.ENTROPY_ADMIN_KEY)

    with _transaction(conn):
        me = get_member(conn, device_id, admin_pubkey=pub)
        if me is None or me["revoked_at"] is not None:
            raise MembershipError("this PC is not an active member of the office")
        current = get_office_admin(conn, admin_pubkey=pub)
        if current is not None and current["device_id"] == device_id:
            result = current
        else:
            rev = max(current["rev"] if current else 0, _stored_rev(conn, OFFICE_ADMIN_ROW)) + 1
            result = sign_record({"type": RECORD_OFFICE_ADMIN, "device_id": device_id,
                                  "since": _now_iso(), "rev": rev}, key)
            store_record(conn, result, pub)
            # office_admin is authoritative; the member roles follow it for display.
            if me["role"] != ROLE_ADMIN:
                store_record(conn, sign_record(dict(_body(me), role=ROLE_ADMIN, rev=me["rev"] + 1), key), pub)
            old = get_member(conn, current["device_id"], admin_pubkey=pub) if current else None
            if old is not None and old["role"] == ROLE_ADMIN:
                store_record(conn, sign_record(dict(_body(old), role=ROLE_MEMBER, rev=old["rev"] + 1), key), pub)

    sera_keys._replace_key_file(kdir / sera_keys.ADMIN_KEY_DPAPI_FILE, dpapi_blob)
    return result


def hand_over_admin(app_dir, conn, admin_device_id: str, target_device_id: str) -> dict:
    """Current admin PC signs an office_admin record naming another active member as admin.

    Unlike ``claim_admin`` (which needs the master password because the caller doesn't yet
    hold the admin key), this runs on the PC that already holds it: no password needed. The
    old admin PC keeps its local ``admin_key.dpapi`` until it notices the hand-over (P2-7
    calls ``reconcile_admin_key`` right after this); ``admin_key.recovery`` already sits on
    every member, so the new admin PC still uses "Become admin" once to fetch its own copy.
    """
    key, pub = _require_named_admin(app_dir, conn, admin_device_id)
    if target_device_id == admin_device_id:
        raise MembershipError("this PC is already the office admin")
    with _transaction(conn):
        target = get_member(conn, target_device_id, admin_pubkey=pub)
        if target is None or target["revoked_at"] is not None:
            raise MembershipError("that PC is not an active member of the office")
        current = get_office_admin(conn, admin_pubkey=pub)
        rev = max(current["rev"] if current else 0, _stored_rev(conn, OFFICE_ADMIN_ROW)) + 1
        result = sign_record({"type": RECORD_OFFICE_ADMIN, "device_id": target_device_id,
                              "since": _now_iso(), "rev": rev}, key)
        store_record(conn, result, pub)
        # office_admin is authoritative; the member roles follow it for display (P2-7's Members list).
        if target["role"] != ROLE_ADMIN:
            store_record(conn, sign_record(dict(_body(target), role=ROLE_ADMIN, rev=target["rev"] + 1), key), pub)
        me = get_member(conn, admin_device_id, admin_pubkey=pub)
        if me is not None and me["role"] == ROLE_ADMIN:
            store_record(conn, sign_record(dict(_body(me), role=ROLE_MEMBER, rev=me["rev"] + 1), key), pub)
    return result


def reconcile_admin_key(app_dir, conn, device_id: str) -> bool:
    """If the current office_admin record names another PC, delete this PC's ``admin_key.dpapi``.

    Returns True if it was deleted. Only done while ``admin_key.recovery`` is present,
    so the key can still be recovered here with the master password.
    """
    path = sera_keys.keys_dir(app_dir) / sera_keys.ADMIN_KEY_DPAPI_FILE
    if not path.exists():
        return False
    try:
        pub = _office_pubkey(app_dir)
    except (AdminKeyUnavailable, sera_keys.KeyFileInvalid):
        return False
    current = get_office_admin(conn, admin_pubkey=pub)
    if current is None or current["device_id"] == device_id:
        return False
    if not (sera_keys.keys_dir(app_dir) / sera_keys.ADMIN_KEY_RECOVERY_FILE).exists():
        return False
    path.unlink()
    return True
