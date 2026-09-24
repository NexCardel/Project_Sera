"""Tests for sync_admin.py (Sera Sync v3, WP P2-2): office admin key and signed records.

Everything lives under pytest's tmp_path; the real data folder is never touched (§0 rule 2).
Membership records are stored with a plain sqlite3 connection: sync_admin only needs a
DB-API connection, and the table layout is the same inside the encrypted master.db.
"""

import ast
import base64
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

import sera_keys
import sync_admin
from sync_admin import InvalidRecord, MembershipError, NotAdmin

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")

MASTER_PW = "OfficeMaster#2026"
_CERT_KEYS = {}   # cert PEM -> its private key, so a test can re-issue a cert for the same key


# ------------------------------------------------------------------ helpers

def _new_cert_pem() -> tuple[str, str]:
    """A throwaway self-signed cert (PEM str) and its device_id, like sync_identity makes."""
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    spki = key.public_key().public_bytes(serialization.Encoding.DER,
                                         serialization.PublicFormat.SubjectPublicKeyInfo)
    device_id = hashlib.sha256(spki).hexdigest()[:32]
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, device_id)])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=30))
            .sign(key, hashes.SHA256()))
    pem = cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
    _CERT_KEYS[pem] = key
    return pem, device_id


def _conn(tmp_path, name="master.db"):
    conn = sqlite3.connect(str(tmp_path / name))
    sync_admin.ensure_members_table(conn)
    return conn


def _rows(conn) -> list:
    return conn.execute("SELECT device_id, record_json, rev FROM _sync_members ORDER BY device_id").fetchall()


def _key_files(app: Path) -> dict:
    kdir = sera_keys.keys_dir(app)
    if not kdir.exists():
        return {}
    return {p.name: p.read_bytes() for p in sorted(kdir.iterdir()) if p.is_file()}


def _make_office(app: Path, password: str = MASTER_PW) -> sera_keys.OfficeInfo:
    """An office-mode PC as P1-4 left it: DEK stored, office.json with admin_pubkey = null."""
    dek = sera_keys.new_dek()
    info = sera_keys.OfficeInfo(office_id=sera_keys.OfficeInfo.new_office_id(),
                                office_name="Test Office", key_id=sera_keys.key_id(dek))
    sera_keys.store_dek(app, dek, password, info.office_id)
    sera_keys.save_office(app, info)
    return info


@pytest.fixture
def admin_office(tmp_path):
    """Admin PC with an admin key, and its membership initialised (member 'A' + office_admin)."""
    app = tmp_path / "admin_pc"
    app.mkdir()
    _make_office(app)
    pubkey = sync_admin.create_admin_key(app, MASTER_PW)
    conn = _conn(app)
    cert_pem, device_id = _new_cert_pem()
    sync_admin.init_office_membership(app, conn, cert_pem, "Front desk")
    yield {"app": app, "conn": conn, "pubkey": pubkey, "device_id": device_id}
    conn.close()


def _signed(record: dict, private_key) -> dict:
    return sync_admin.sign_record(record, private_key)


def _raw_signed(record: dict, private_key) -> dict:
    """Sign without sync_admin's checks (sign_record refuses malformed records), to test verify_record."""
    import sync_peer
    body = {k: v for k, v in record.items() if k != "sig"}
    return dict(body, sig=base64.b64encode(private_key.sign(sync_peer.canonical_json(body))).decode())


def _member_record(device_id: str, cert_pem: str, rev: int = 1, letter: str = "B", **over) -> dict:
    rec = {
        "type": "member",
        "device_id": device_id,
        "name": "Back office",
        "cert_pem": cert_pem,
        "role": "member",
        "token_letter": letter,
        "added_at": "2026-09-24T10:00:00+00:00",
        "revoked_at": None,
        "rev": rev,
    }
    rec.update(over)
    return rec


@pytest.fixture
def signing_key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.generate()
    return key, sync_admin.public_key_b64(key)


# ------------------------------------------------------------------ Accept: forged / modified records

def test_valid_signed_record_verifies(signing_key):
    key, pub = signing_key
    cert_pem, device_id = _new_cert_pem()
    rec = _signed(_member_record(device_id, cert_pem), key)
    assert sync_admin.verify_record(rec, pub) == rec
    # The signature covers canonical_json(record without "sig") (P0-7's canonical_json).
    import sync_peer
    body = {k: v for k, v in rec.items() if k != "sig"}
    key.public_key().verify(base64.b64decode(rec["sig"]), sync_peer.canonical_json(body))


def test_forged_record_rejected(tmp_path, signing_key):
    """Signed by some other Ed25519 key: rejected, and not stored."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    _, office_pub = signing_key
    attacker = Ed25519PrivateKey.generate()
    cert_pem, device_id = _new_cert_pem()
    forged = _signed(_member_record(device_id, cert_pem), attacker)

    with pytest.raises(InvalidRecord):
        sync_admin.verify_record(forged, office_pub)
    conn = _conn(tmp_path)
    with pytest.raises(InvalidRecord):
        sync_admin.store_record(conn, forged, office_pub)
    assert _rows(conn) == []


@pytest.mark.parametrize("field,value", [
    ("role", "admin"),
    ("revoked_at", None),            # un-revoke a revoked record
    ("rev", 99),
    ("name", "Evil PC"),
    ("token_letter", "A"),
    ("type", "office_admin"),
])
def test_modified_record_rejected(tmp_path, signing_key, field, value):
    key, pub = signing_key
    cert_pem, device_id = _new_cert_pem()
    rec = _signed(_member_record(device_id, cert_pem, revoked_at="2026-09-24T12:00:00+00:00"), key)
    tampered = dict(rec)
    tampered[field] = value
    assert tampered != rec
    with pytest.raises(InvalidRecord):
        sync_admin.verify_record(tampered, pub)
    conn = _conn(tmp_path)
    with pytest.raises(InvalidRecord):
        sync_admin.store_record(conn, tampered, pub)
    assert _rows(conn) == []


def test_record_with_added_field_rejected(signing_key):
    key, pub = signing_key
    cert_pem, device_id = _new_cert_pem()
    rec = _signed(_member_record(device_id, cert_pem), key)
    rec["extra"] = "smuggled"
    with pytest.raises(InvalidRecord):
        sync_admin.verify_record(rec, pub)


@pytest.mark.parametrize("sig", [None, "", "not base64!", base64.b64encode(b"\0" * 64).decode(),
                                 base64.b64encode(b"\0" * 10).decode()])
def test_missing_or_garbage_signature_rejected(signing_key, sig):
    key, pub = signing_key
    cert_pem, device_id = _new_cert_pem()
    rec = _signed(_member_record(device_id, cert_pem), key)
    if sig is None:
        del rec["sig"]
    else:
        rec["sig"] = sig
    with pytest.raises(InvalidRecord):
        sync_admin.verify_record(rec, pub)


def test_member_record_cert_must_match_device_id(signing_key):
    """Even a correctly signed record is refused if its cert isn't the named device's."""
    key, pub = signing_key
    cert_pem, _ = _new_cert_pem()
    _, other_device_id = _new_cert_pem()
    rec = _raw_signed(_member_record(other_device_id, cert_pem), key)
    with pytest.raises(InvalidRecord):
        sync_admin.verify_record(rec, pub)
    with pytest.raises(InvalidRecord):
        sync_admin.sign_record(rec, key)


@pytest.mark.parametrize("over", [
    {"role": "owner"},
    {"rev": 0},
    {"rev": True},
    {"rev": "2"},
    {"token_letter": "b"},
    {"token_letter": ""},
    {"device_id": "XYZ"},
    {"added_at": ""},
    {"revoked_at": 5},
])
def test_malformed_member_record_rejected_even_if_signed(signing_key, over):
    key, pub = signing_key
    cert_pem, device_id = _new_cert_pem()
    rec = _raw_signed(dict(_member_record(device_id, cert_pem), **over), key)
    with pytest.raises(InvalidRecord):
        sync_admin.verify_record(rec, pub)
    with pytest.raises(InvalidRecord):
        sync_admin.sign_record(rec, key)


def test_bad_admin_pubkey_rejected(signing_key):
    key, _ = signing_key
    cert_pem, device_id = _new_cert_pem()
    rec = _signed(_member_record(device_id, cert_pem), key)
    for pub in (None, "", "!!", base64.b64encode(b"short").decode()):
        with pytest.raises(InvalidRecord):
            sync_admin.verify_record(rec, pub)


def test_staff_change_record_signs_and_verifies(signing_key):
    """staff_change is only signed/verified here (payload is P3-4's), and not storable in _sync_members."""
    key, pub = signing_key
    rec = _signed({"type": "staff_change", "rev": 1, "staff": {"slot": 1, "name": "Staff One"}}, key)
    assert sync_admin.verify_record(rec, pub) == rec
    rec2 = dict(rec, staff={"slot": 1, "name": "Someone Else"})
    with pytest.raises(InvalidRecord):
        sync_admin.verify_record(rec2, pub)


def test_staff_change_not_stored_in_members_table(tmp_path, signing_key):
    key, pub = signing_key
    rec = _signed({"type": "staff_change", "rev": 1}, key)
    conn = _conn(tmp_path)
    with pytest.raises(InvalidRecord):
        sync_admin.store_record(conn, rec, pub)


# ------------------------------------------------------------------ Accept: rev rule

def test_lower_rev_ignored(tmp_path, signing_key):
    key, pub = signing_key
    cert_pem, device_id = _new_cert_pem()
    conn = _conn(tmp_path)
    rev2 = _signed(_member_record(device_id, cert_pem, rev=2, revoked_at="2026-09-24T12:00:00+00:00"), key)
    rev1 = _signed(_member_record(device_id, cert_pem, rev=1), key)

    assert sync_admin.store_record(conn, rev2, pub) is True
    assert sync_admin.store_record(conn, rev1, pub) is False
    assert sync_admin.get_member(conn, device_id, pub) == rev2


def test_higher_rev_replaces(tmp_path, signing_key):
    key, pub = signing_key
    cert_pem, device_id = _new_cert_pem()
    conn = _conn(tmp_path)
    rev1 = _signed(_member_record(device_id, cert_pem, rev=1), key)
    rev2 = _signed(_member_record(device_id, cert_pem, rev=2, revoked_at="2026-09-24T12:00:00+00:00"), key)
    assert sync_admin.store_record(conn, rev1, pub) is True
    assert sync_admin.store_record(conn, rev2, pub) is True
    assert sync_admin.get_member(conn, device_id, pub) == rev2
    assert conn.execute("SELECT rev FROM _sync_members WHERE device_id=?", (device_id,)).fetchone()[0] == 2


def test_same_record_twice_is_a_no_op(tmp_path, signing_key):
    key, pub = signing_key
    cert_pem, device_id = _new_cert_pem()
    conn = _conn(tmp_path)
    rec = _signed(_member_record(device_id, cert_pem), key)
    assert sync_admin.store_record(conn, rec, pub) is True
    assert sync_admin.store_record(conn, rec, pub) is False


def test_equal_rev_conflict_converges_in_either_order(tmp_path, signing_key):
    """Two different records with the same rev: every PC keeps the same one, whatever the arrival order."""
    key, pub = signing_key
    cert_pem, device_id = _new_cert_pem()
    a = _signed(_member_record(device_id, cert_pem, rev=3, name="Name A"), key)
    b = _signed(_member_record(device_id, cert_pem, rev=3, name="Name B"), key)
    c1, c2 = _conn(tmp_path, "one.db"), _conn(tmp_path, "two.db")
    sync_admin.store_record(c1, a, pub)
    sync_admin.store_record(c1, b, pub)
    sync_admin.store_record(c2, b, pub)
    sync_admin.store_record(c2, a, pub)
    assert sync_admin.get_member(c1, device_id, pub) == sync_admin.get_member(c2, device_id, pub)


def test_office_admin_record_rev_rule(tmp_path, signing_key):
    key, pub = signing_key
    conn = _conn(tmp_path)
    r1 = _signed({"type": "office_admin", "device_id": "a" * 32, "since": "2026-09-24T10:00:00+00:00", "rev": 1}, key)
    r2 = _signed({"type": "office_admin", "device_id": "b" * 32, "since": "2026-09-24T11:00:00+00:00", "rev": 2}, key)
    assert sync_admin.store_record(conn, r2, pub) is True
    assert sync_admin.store_record(conn, r1, pub) is False
    assert sync_admin.get_office_admin(conn, pub) == r2
    # The office_admin row never shows up as a member.
    assert sync_admin.list_members(conn, pub) == []


def test_tampered_stored_row_is_not_returned(tmp_path, signing_key):
    """Readers re-verify: a row edited directly in the DB is ignored, not trusted."""
    key, pub = signing_key
    cert_pem, device_id = _new_cert_pem()
    conn = _conn(tmp_path)
    sync_admin.store_record(conn, _signed(_member_record(device_id, cert_pem), key), pub)
    row = conn.execute("SELECT record_json FROM _sync_members").fetchone()[0]
    conn.execute("UPDATE _sync_members SET record_json=?", (row.replace('"member"', '"admin"', 1),))
    conn.commit()
    assert sync_admin.get_member(conn, device_id, admin_pubkey=pub) is None
    assert sync_admin.list_members(conn, admin_pubkey=pub) == []


# ------------------------------------------------------------------ revoke is final; letter / cert never change

REVOKED_AT = "2026-09-24T12:00:00+00:00"


@pytest.mark.parametrize("order", ["revoke_first", "active_first"])
def test_revoke_wins_equal_rev_race_in_either_order(tmp_path, signing_key, order):
    """Admin revokes X while X's user takes over admin: both sign X's record at the same rev.

    Every PC must end with X removed, whatever order the two records arrive in.
    """
    import sync_peer
    key, pub = signing_key
    cert_pem, device_id = _new_cert_pem()
    revoked = _signed(_member_record(device_id, cert_pem, rev=2, revoked_at=REVOKED_AT), key)
    promoted = _signed(_member_record(device_id, cert_pem, rev=2, role="admin"), key)
    # The bug the review found: on its own, the active record sorts higher.
    assert sync_peer.canonical_json(promoted) > sync_peer.canonical_json(revoked)
    conn = _conn(tmp_path)
    first, second = (revoked, promoted) if order == "revoke_first" else (promoted, revoked)
    sync_admin.store_record(conn, first, pub)
    sync_admin.store_record(conn, second, pub)
    assert sync_admin.get_member(conn, device_id, pub) == revoked


def test_active_record_never_replaces_revoked_at_any_rev(tmp_path, signing_key):
    key, pub = signing_key
    cert_pem, device_id = _new_cert_pem()
    conn = _conn(tmp_path)
    revoked = _signed(_member_record(device_id, cert_pem, rev=2, revoked_at=REVOKED_AT), key)
    assert sync_admin.store_record(conn, revoked, pub) is True
    for rev in (3, 50):
        active = _signed(_member_record(device_id, cert_pem, rev=rev), key)
        assert sync_admin.store_record(conn, active, pub) is False
    assert sync_admin.get_member(conn, device_id, pub) == revoked


def test_revoke_replaces_active_even_at_lower_rev(tmp_path, signing_key):
    key, pub = signing_key
    cert_pem, device_id = _new_cert_pem()
    conn = _conn(tmp_path)
    sync_admin.store_record(conn, _signed(_member_record(device_id, cert_pem, rev=5), key), pub)
    revoked = _signed(_member_record(device_id, cert_pem, rev=3, revoked_at=REVOKED_AT), key)
    assert sync_admin.store_record(conn, revoked, pub) is True
    assert sync_admin.get_member(conn, device_id, pub) == revoked


def _reissued_cert(cert_pem: str) -> str:
    """A second self-signed cert for the same key as ``cert_pem`` (same device_id, different PEM)."""
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    key = _CERT_KEYS[cert_pem]
    old = x509.load_pem_x509_certificate(cert_pem.encode())
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(old.subject).issuer_name(old.subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=2)).not_valid_after(now + timedelta(days=60))
            .sign(key, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.PEM).decode("ascii")


def test_token_letter_or_cert_change_refused(tmp_path, signing_key):
    key, pub = signing_key
    cert_pem, device_id = _new_cert_pem()
    conn = _conn(tmp_path)
    original = _signed(_member_record(device_id, cert_pem, rev=1, letter="B"), key)
    sync_admin.store_record(conn, original, pub)
    other_letter = _signed(_member_record(device_id, cert_pem, rev=2, letter="C"), key)
    assert sync_admin.store_record(conn, other_letter, pub) is False
    other_cert = _reissued_cert(cert_pem)
    assert sync_admin.device_id_from_cert_pem(other_cert) == device_id and other_cert != cert_pem
    assert sync_admin.store_record(conn, _signed(_member_record(device_id, other_cert, rev=2), key), pub) is False
    assert sync_admin.get_member(conn, device_id, pub) == original


def test_readers_require_admin_pubkey():
    """No unchecked reads: get_member / list_members / get_office_admin always re-verify."""
    import inspect
    for fn in (sync_admin.get_member, sync_admin.list_members, sync_admin.get_office_admin):
        param = inspect.signature(fn).parameters["admin_pubkey"]
        assert param.default is inspect.Parameter.empty, fn.__name__


def test_next_token_letter_counts_unverifiable_rows(tmp_path, signing_key):
    """A damaged row's letter is still skipped, never handed out again."""
    key, pub = signing_key
    cert_pem, device_id = _new_cert_pem()
    conn = _conn(tmp_path)
    sync_admin.store_record(conn, _signed(_member_record(device_id, cert_pem, letter="C"), key), pub)
    row = conn.execute("SELECT record_json FROM _sync_members").fetchone()[0]
    conn.execute("UPDATE _sync_members SET record_json=?", (row.replace("Back office", "Damaged"),))
    conn.commit()
    assert sync_admin.list_members(conn, pub) == []
    assert sync_admin.next_token_letter(conn) == "D"


# ------------------------------------------------------------------ token letters (D8)

def test_token_letter_sequence():
    letters = [sync_admin.token_letter_for_index(i) for i in range(30)]
    assert letters[:3] == ["A", "B", "C"]
    assert letters[25] == "Z"
    assert letters[26:30] == ["AA", "AB", "AC", "AD"]
    assert sync_admin.token_letter_for_index(26 + 26 * 26 - 1) == "ZZ"
    assert sync_admin.token_letter_for_index(26 + 26 * 26) == "AAA"


# ------------------------------------------------------------------ admin key files

@windows_only
def test_create_admin_key_for_office_without_one(tmp_path):
    """The admin PC after P1-4 has admin_pubkey = null; create_admin_key fills it in."""
    app = tmp_path / "pc"
    app.mkdir()
    office = _make_office(app)
    assert office.admin_pubkey is None

    pub = sync_admin.create_admin_key(app, MASTER_PW)

    assert sera_keys.load_office(app).admin_pubkey == pub
    kdir = sera_keys.keys_dir(app)
    assert (kdir / sera_keys.ADMIN_KEY_DPAPI_FILE).exists()
    assert (kdir / sera_keys.ADMIN_KEY_RECOVERY_FILE).exists()
    key = sync_admin.load_admin_key(app)
    assert sync_admin.public_key_b64(key) == pub
    # The recovery blob opens with the master password and the admin AAD (P1-1).
    blob = json.loads((kdir / sera_keys.ADMIN_KEY_RECOVERY_FILE).read_text(encoding="utf-8"))
    raw = sera_keys.unwrap_with_password(blob, MASTER_PW, sera_keys.admin_aad(office.office_id))
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    assert sync_admin.public_key_b64(Ed25519PrivateKey.from_private_bytes(raw)) == pub
    # Nothing secret in office.json.
    text = (kdir / sera_keys.OFFICE_FILE).read_text(encoding="utf-8")
    assert base64.b64encode(raw).decode() not in text and raw.hex() not in text


@windows_only
def test_create_admin_key_wrong_password_changes_nothing(tmp_path):
    app = tmp_path / "pc"
    app.mkdir()
    _make_office(app)
    before = _key_files(app)
    with pytest.raises(sera_keys.WrongPassword):
        sync_admin.create_admin_key(app, "not the password")
    assert _key_files(app) == before


@windows_only
def test_create_admin_key_refuses_when_office_already_has_one(tmp_path):
    app = tmp_path / "pc"
    app.mkdir()
    _make_office(app)
    sync_admin.create_admin_key(app, MASTER_PW)
    before = _key_files(app)
    with pytest.raises(sync_admin.SyncAdminError):
        sync_admin.create_admin_key(app, MASTER_PW)
    assert _key_files(app) == before


@windows_only
def test_load_admin_key_rejects_key_not_matching_office(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    app = tmp_path / "pc"
    app.mkdir()
    _make_office(app)
    sync_admin.create_admin_key(app, MASTER_PW)
    other = Ed25519PrivateKey.generate()
    from cryptography.hazmat.primitives import serialization
    raw = other.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                              serialization.NoEncryption())
    (sera_keys.keys_dir(app) / sera_keys.ADMIN_KEY_DPAPI_FILE).write_bytes(
        sera_keys.dpapi_protect(raw, sera_keys.ENTROPY_ADMIN_KEY))
    with pytest.raises(sync_admin.AdminKeyUnavailable):
        sync_admin.load_admin_key(app)


def test_load_admin_key_missing(tmp_path):
    with pytest.raises(sync_admin.AdminKeyUnavailable):
        sync_admin.load_admin_key(tmp_path)


@windows_only
def test_change_master_password_rewraps_admin_key(tmp_path):
    """P1-6 already re-wraps admin_key.recovery; check it works with the real file P2-2 writes."""
    app = tmp_path / "pc"
    app.mkdir()
    office = _make_office(app)
    pub = sync_admin.create_admin_key(app, MASTER_PW)
    sera_keys.change_master_password(app, MASTER_PW, "BrandNew#Pass1")
    blob = json.loads((sera_keys.keys_dir(app) / sera_keys.ADMIN_KEY_RECOVERY_FILE).read_text(encoding="utf-8"))
    raw = sera_keys.unwrap_with_password(blob, "BrandNew#Pass1", sera_keys.admin_aad(office.office_id))
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    assert sync_admin.public_key_b64(Ed25519PrivateKey.from_private_bytes(raw)) == pub


# ------------------------------------------------------------------ membership (admin PC)

@windows_only
def test_init_office_membership(admin_office):
    conn, pub, dev = admin_office["conn"], admin_office["pubkey"], admin_office["device_id"]
    me = sync_admin.get_member(conn, dev, admin_pubkey=pub)
    assert me["role"] == "admin" and me["token_letter"] == "A" and me["rev"] == 1
    assert me["revoked_at"] is None and me["name"] == "Front desk"
    oa = sync_admin.get_office_admin(conn, admin_pubkey=pub)
    assert oa["device_id"] == dev and oa["rev"] == 1


@windows_only
def test_init_office_membership_twice_refused(admin_office):
    cert_pem, _ = _new_cert_pem()
    with pytest.raises(MembershipError):
        sync_admin.init_office_membership(admin_office["app"], admin_office["conn"], cert_pem, "Again")


@windows_only
def test_add_member_assigns_next_letter_and_revoked_letters_are_never_reused(admin_office):
    app, conn, pub, admin_dev = (admin_office[k] for k in ("app", "conn", "pubkey", "device_id"))
    b_cert, b_dev = _new_cert_pem()
    c_cert, c_dev = _new_cert_pem()
    d_cert, d_dev = _new_cert_pem()

    b = sync_admin.add_member(app, conn, admin_dev, b_cert, "PC B")
    assert b["token_letter"] == "B" and b["role"] == "member" and b["rev"] == 1
    c = sync_admin.add_member(app, conn, admin_dev, c_cert, "PC C")
    assert c["token_letter"] == "C"

    revoked = sync_admin.revoke_member(app, conn, admin_dev, c_dev)
    assert revoked["revoked_at"] and revoked["rev"] == 2 and revoked["token_letter"] == "C"

    d = sync_admin.add_member(app, conn, admin_dev, d_cert, "PC D")
    assert d["token_letter"] == "D"
    for rec in (b, revoked, d):
        assert sync_admin.verify_record(rec, pub) == rec

    active = {m["device_id"] for m in sync_admin.list_members(conn, admin_pubkey=pub, include_revoked=False)}
    assert active == {admin_dev, b_dev, d_dev}


@windows_only
def test_add_existing_active_member_returns_existing_record(admin_office):
    app, conn, admin_dev = admin_office["app"], admin_office["conn"], admin_office["device_id"]
    cert, _ = _new_cert_pem()
    first = sync_admin.add_member(app, conn, admin_dev, cert, "PC B")
    again = sync_admin.add_member(app, conn, admin_dev, cert, "PC B")
    assert again == first


@windows_only
def test_add_revoked_member_refused(admin_office):
    app, conn, admin_dev = admin_office["app"], admin_office["conn"], admin_office["device_id"]
    cert, dev = _new_cert_pem()
    sync_admin.add_member(app, conn, admin_dev, cert, "PC B")
    sync_admin.revoke_member(app, conn, admin_dev, dev)
    with pytest.raises(MembershipError):
        sync_admin.add_member(app, conn, admin_dev, cert, "PC B")


@windows_only
def test_admin_cannot_revoke_itself(admin_office):
    app, conn, admin_dev = admin_office["app"], admin_office["conn"], admin_office["device_id"]
    with pytest.raises(MembershipError):
        sync_admin.revoke_member(app, conn, admin_dev, admin_dev)


@windows_only
def test_non_admin_pc_cannot_sign(admin_office):
    """A PC holding the admin key file but not named in office_admin must not sign."""
    app, conn = admin_office["app"], admin_office["conn"]
    cert, _ = _new_cert_pem()
    with pytest.raises(NotAdmin):
        sync_admin.add_member(app, conn, "f" * 32, cert, "PC B")


# ------------------------------------------------------------------ claim_admin / hand-over

def _second_pc(admin_office, tmp_path):
    """PC B: a member that received office.json, the recovery blobs and the members (as P2-4 will do)."""
    app_a, conn_a, admin_dev = admin_office["app"], admin_office["conn"], admin_office["device_id"]
    b_cert, b_dev = _new_cert_pem()
    sync_admin.add_member(app_a, conn_a, admin_dev, b_cert, "PC B")

    app_b = tmp_path / "pc_b"
    kdir_b = sera_keys.keys_dir(app_b)
    kdir_b.mkdir(parents=True)
    kdir_a = sera_keys.keys_dir(app_a)
    for name in (sera_keys.OFFICE_FILE, sera_keys.DEK_RECOVERY_FILE, sera_keys.ADMIN_KEY_RECOVERY_FILE):
        (kdir_b / name).write_bytes((kdir_a / name).read_bytes())
    conn_b = _conn(app_b)
    pub = admin_office["pubkey"]
    for rec in sync_admin.list_members(conn_a, admin_pubkey=pub) + [sync_admin.get_office_admin(conn_a, pub)]:
        sync_admin.store_record(conn_b, rec, pub)
    return app_b, conn_b, b_dev


@windows_only
def test_claim_admin_wrong_password_changes_nothing(admin_office, tmp_path):
    app_b, conn_b, b_dev = _second_pc(admin_office, tmp_path)
    files_before = _key_files(app_b)
    rows_before = _rows(conn_b)

    with pytest.raises(sera_keys.WrongPassword):
        sync_admin.claim_admin(app_b, conn_b, "wrong password", b_dev)

    assert _key_files(app_b) == files_before
    assert not (sera_keys.keys_dir(app_b) / sera_keys.ADMIN_KEY_DPAPI_FILE).exists()
    assert _rows(conn_b) == rows_before
    conn_b.close()


@windows_only
def test_claim_admin_hands_over_and_old_admin_drops_its_key(admin_office, tmp_path):
    app_a, conn_a, a_dev, pub = (admin_office[k] for k in ("app", "conn", "device_id", "pubkey"))
    app_b, conn_b, b_dev = _second_pc(admin_office, tmp_path)

    new_oa = sync_admin.claim_admin(app_b, conn_b, MASTER_PW, b_dev)

    assert new_oa["device_id"] == b_dev and new_oa["rev"] == 2
    assert sync_admin.get_office_admin(conn_b, admin_pubkey=pub) == new_oa
    assert sync_admin.public_key_b64(sync_admin.load_admin_key(app_b)) == pub
    assert sync_admin.get_member(conn_b, b_dev, pub)["role"] == "admin"
    assert sync_admin.get_member(conn_b, a_dev, pub)["role"] == "member"

    # B can now sign; A's copy of the records arrives at the old admin PC.
    for rec in sync_admin.list_members(conn_b, admin_pubkey=pub) + [new_oa]:
        sync_admin.store_record(conn_a, rec, pub)
    assert (sera_keys.keys_dir(app_a) / sera_keys.ADMIN_KEY_DPAPI_FILE).exists()
    assert sync_admin.reconcile_admin_key(app_a, conn_a, a_dev) is True
    assert not (sera_keys.keys_dir(app_a) / sera_keys.ADMIN_KEY_DPAPI_FILE).exists()
    assert (sera_keys.keys_dir(app_a) / sera_keys.ADMIN_KEY_RECOVERY_FILE).exists()
    # The new admin keeps its key.
    assert sync_admin.reconcile_admin_key(app_b, conn_b, b_dev) is False
    assert (sera_keys.keys_dir(app_b) / sera_keys.ADMIN_KEY_DPAPI_FILE).exists()

    # And the old admin can no longer sign.
    cert, _ = _new_cert_pem()
    with pytest.raises(NotAdmin):
        sync_admin.add_member(app_a, conn_a, a_dev, cert, "PC X")
    conn_b.close()


@windows_only
def test_claim_admin_by_non_member_refused(admin_office, tmp_path):
    app_b, conn_b, _ = _second_pc(admin_office, tmp_path)
    before = (_key_files(app_b), _rows(conn_b))
    with pytest.raises(MembershipError):
        sync_admin.claim_admin(app_b, conn_b, MASTER_PW, "e" * 32)
    assert (_key_files(app_b), _rows(conn_b)) == before
    conn_b.close()


@windows_only
def test_claim_admin_refuses_open_transaction(admin_office, tmp_path):
    app_b, conn_b, b_dev = _second_pc(admin_office, tmp_path)
    conn_b.execute("BEGIN")
    before = _key_files(app_b)
    with pytest.raises(sync_admin.SyncAdminError):
        sync_admin.claim_admin(app_b, conn_b, MASTER_PW, b_dev)
    conn_b.rollback()
    assert _key_files(app_b) == before
    conn_b.close()


@windows_only
def test_claim_admin_by_revoked_member_refused(admin_office, tmp_path):
    app_b, conn_b, b_dev = _second_pc(admin_office, tmp_path)
    app_a, conn_a, a_dev, pub = (admin_office[k] for k in ("app", "conn", "device_id", "pubkey"))
    sync_admin.store_record(conn_b, sync_admin.revoke_member(app_a, conn_a, a_dev, b_dev), pub)
    before = (_key_files(app_b), _rows(conn_b))
    with pytest.raises(MembershipError):
        sync_admin.claim_admin(app_b, conn_b, MASTER_PW, b_dev)
    assert (_key_files(app_b), _rows(conn_b)) == before
    conn_b.close()


@windows_only
def test_claim_admin_on_current_admin_restores_lost_dpapi_key(admin_office):
    app, conn, dev, pub = (admin_office[k] for k in ("app", "conn", "device_id", "pubkey"))
    kfile = sera_keys.keys_dir(app) / sera_keys.ADMIN_KEY_DPAPI_FILE
    kfile.rename(kfile.with_name("moved-away"))
    oa_before = sync_admin.get_office_admin(conn, pub)
    oa = sync_admin.claim_admin(app, conn, MASTER_PW, dev)
    assert oa == oa_before
    assert sync_admin.public_key_b64(sync_admin.load_admin_key(app)) == pub


# ------------------------------------------------------------------ P1-4 migration creates the admin key

@windows_only
def test_migration_creates_admin_key(tmp_path):
    from test_office_key_migration import LEGACY_PW, NEW_PW
    import sync_migrate
    app = _legacy_app(tmp_path)
    sync_migrate.migrate_to_office_key(app, LEGACY_PW, "Test Office", new_password=NEW_PW)
    office = sera_keys.load_office(app)
    assert office.admin_pubkey
    assert sync_admin.public_key_b64(sync_admin.load_admin_key(app)) == office.admin_pubkey
    blob = json.loads((sera_keys.keys_dir(app) / sera_keys.ADMIN_KEY_RECOVERY_FILE).read_text(encoding="utf-8"))
    sera_keys.unwrap_with_password(blob, NEW_PW, sera_keys.admin_aad(office.office_id))


@windows_only
def test_migration_rollback_removes_admin_key(tmp_path, monkeypatch):
    from test_office_key_migration import LEGACY_PW, NEW_PW
    import sync_migrate
    app = _legacy_app(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("simulated failure after the admin key was written")
    monkeypatch.setattr(sera_keys, "save_office", boom)
    with pytest.raises(RuntimeError):
        sync_migrate.migrate_to_office_key(app, LEGACY_PW, "Test Office", new_password=NEW_PW)
    kdir = sera_keys.keys_dir(app)
    for name in (sera_keys.ADMIN_KEY_DPAPI_FILE, sera_keys.ADMIN_KEY_RECOVERY_FILE, sera_keys.OFFICE_FILE):
        assert not (kdir / name).exists(), name


def _legacy_app(tmp_path) -> Path:
    import security
    from database import SeraDatabase
    from test_office_key_migration import LEGACY_PW
    app = tmp_path / "legacy"
    app.mkdir()
    security.generate_and_save_salt(str(app / security.SALT_FILE))
    hex_key = security.derive_key_hex(LEGACY_PW, security.load_salt(str(app / security.SALT_FILE)))
    (app / "sera.key").write_text(LEGACY_PW, encoding="utf-8")
    db = SeraDatabase(str(app / "master.db"), hex_key, defer_startup_maintenance=True)
    del db
    return app


# ------------------------------------------------------------------ module rules

def test_no_pyside6_import():
    src = Path(sync_admin.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            assert not any(a.name.startswith("PySide6") for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("PySide6")
