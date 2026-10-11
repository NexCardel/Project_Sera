"""
2026-10-11: a complete dataset (an ARN and a verified status) is locked. A re-read of the portal that shows less,
a dashboard tile, an unasked "correction" or an older view from another PC cannot lower it; only a lowering the
user confirmed in SGT's dialog gets through. Identifiers are fictional.
"""
import json

import pytest

import security
from database import SeraDatabase
from sera_db.tracker_dump import is_complete

GSTIN = "29ABACD1191FAZK"
ARN = "AA290726123456Z"


@pytest.fixture()
def db(tmp_path):
    salt = str(tmp_path / "t.salt")
    security.generate_and_save_salt(salt)
    hex_key = security.derive_key_hex("testpass123", security.load_salt(salt))
    return SeraDatabase(str(tmp_path / "m.db"), hex_key, raw_db_path=str(tmp_path / "r.db"))


def rows(db):
    with db._connect_raw() as c:
        return [tuple(r) for r in c.execute("SELECT status, arn_number FROM tracker_dump ORDER BY id")]


def put(db, status, arn=ARN, correction=False, confirmed=False):
    p = {"portal": "GST Portal", "gstin": GSTIN, "filing_type": "GSTR-3B", "period_label": "June (FY 2026-27)",
         "arn": arn, "status": status, "capture_method": "SGT_live"}
    if correction:
        p["status_correction"] = True
    if confirmed:
        p["status_confirmed"] = True
    return db.insert_tracker_dump(portal="GST Portal (GSTR-3B)", period_label="June (FY 2026-27)", arn_number=arn,
                                  capture_method="SGT_live", status=status, raw_payload_json=json.dumps(p),
                                  pan=GSTIN, filing_type="GSTR-3B")


def peer(db, status, arn=ARN, created="2999-01-01T00:00:00+00:00", confirmed=False):
    p = {"status": status}
    if confirmed:
        p.update(status_correction=True, status_confirmed=True)
    key = db.compute_dataset_key("GST Portal", GSTIN, "GSTR-3B", "June (FY 2026-27)")
    db.store_peer_tracker_dumps([{"portal": "GST Portal (GSTR-3B)", "period_label": "June (FY 2026-27)",
                                  "arn_number": arn, "capture_method": "SGT_live", "status": status,
                                  "raw_payload_json": json.dumps(p), "captured_by": "PC2", "created_at": created,
                                  "dataset_key": key}])


def test_what_is_complete():
    assert is_complete("Submitted & Verified", ARN)
    assert is_complete("Processed with refund", ARN)
    assert not is_complete("Submitted & Verified", "N/A")
    assert not is_complete("Submitted & Verified", "")
    assert not is_complete("Submitted (Not Verified)", ARN)


def test_an_unasked_correction_cannot_lower_a_complete_row(db):
    put(db, "Submitted & Verified")
    put(db, "Submitted (Not Verified)", arn="N/A", correction=True)
    assert rows(db) == [("Submitted & Verified", ARN)]


def test_a_confirmed_correction_lowers_it(db):
    put(db, "Submitted & Verified")
    put(db, "Submitted (Not Verified)", correction=True, confirmed=True)
    assert rows(db) == [("Submitted (Not Verified)", ARN)]


def test_an_incomplete_row_still_takes_a_correction(db):
    put(db, "Submitted (Not Verified)")
    put(db, "Draft", arn="N/A", correction=True)
    assert [r[0] for r in rows(db)] == ["Draft"]


def test_a_complete_row_still_climbs(db):
    put(db, "Submitted (Not Verified)")
    put(db, "Submitted & Verified")
    assert rows(db) == [("Submitted & Verified", ARN)]


def test_a_newer_lower_view_from_a_peer_is_refused(db):
    put(db, "Submitted & Verified")
    peer(db, "Submitted (Not Verified)")
    assert rows(db) == [("Submitted & Verified", ARN)]


def test_a_confirmed_lowering_from_a_peer_is_taken(db):
    put(db, "Submitted & Verified")
    peer(db, "Submitted (Not Verified)", confirmed=True)
    assert rows(db) == [("Submitted (Not Verified)", ARN)]


def test_a_peer_view_that_is_not_lower_still_replaces(db):
    put(db, "Submitted (Not Verified)")
    peer(db, "Submitted & Verified")
    assert rows(db) == [("Submitted & Verified", ARN)]
