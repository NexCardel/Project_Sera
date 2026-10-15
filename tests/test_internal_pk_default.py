"""The default internal-PK anchor is the PAN column only; a label that merely contains the letters
"pan" (NAME OF COMPANY) must never be picked (regression: 130+ fixture failures on installs without
settings.ini, where every client save failed with "Internal Primary Key 'NAME OF COMPANY' is mandatory")."""

import pytest


def _open(tmp_path):
    import security
    from database import SeraDatabase

    salt_path = str(tmp_path / "test.salt")
    try:
        salt = security.load_salt(salt_path)
    except Exception:
        security.generate_and_save_salt(salt_path)
        salt = security.load_salt(salt_path)
    hex_key = security.derive_key_hex("testpass123", salt)
    return SeraDatabase(str(tmp_path / "pk.db"), hex_key, defer_startup_maintenance=True)


def _pk_labels(db):
    with db._connect() as conn:
        return {r[0] for r in conn.execute("SELECT label FROM mcl_columns WHERE is_internal_pk = 1")}


def test_default_anchor_is_pan_not_company(tmp_path):
    db = _open(tmp_path)
    with db._connect() as conn:
        conn.execute("UPDATE mcl_columns SET is_internal_pk = 0")
    db2 = _open(tmp_path)
    labels = _pk_labels(db2)
    assert labels, "a default anchor must be chosen"
    assert not any("company" in l.lower() for l in labels), labels
    assert {l.strip().upper() for l in labels} <= {"PAN", "PAN NO", "PAN NUMBER", "PAN NO."}, labels
