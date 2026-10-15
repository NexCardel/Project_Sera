"""
A tracker row never borrows its client from another PC, and an SGT row never borrows one at all
(2026-10-13). A GST filing done on one office PC sat on the client open on ANOTHER PC for a few
minutes: SGT sent the row before it had read the client, and re_resolve_all_tracker_dumps (run after
every insert and every Sera Sync) gave it the identity of whatever row came before it on that portal
within 15 minutes - from any PC, the tracker being office-wide. The row came right only when SGT
re-sent it under its client; a session that never read the client left it wrong for good.
Identifiers are fictional.
"""
import json
import os

import pytest

import security
from database import SeraDatabase
from sera_db.common import is_sgt_capture, payload_device_name

GST = "GST Portal (GSTR-1)"
PAN_A = "ABCPD1234E"


@pytest.fixture
def db(tmp_path):
    salt_path = os.path.join(tmp_path, "t.salt")
    security.generate_and_save_salt(salt_path)
    key = security.derive_key_hex("testpass123", security.load_salt(salt_path))
    return SeraDatabase(str(tmp_path / "master.db"), key, raw_db_path=str(tmp_path / "rawPayload.db"))


def payload(device, **kw):
    return json.dumps({"device_name": device, "raw_payload": {"device_name": device}, **kw})


def put(db, method, device, arn, **kw):
    out = db.insert_tracker_dump(portal=GST, arn_number=arn, capture_method=method, status="Submitted",
                                 raw_payload_json=payload(device, **kw))
    return out["id"]


def identity(db, row_id):
    with db._connect_raw() as c:
        return c.execute("SELECT client_id, unassigned_identity FROM tracker_dump WHERE id = ?",
                         (row_id,)).fetchone()


def test_an_sgt_row_without_its_client_borrows_none_after_re_resolve(db):
    put(db, "SGT_live", "FRONT-DESK", "N/A", pan=PAN_A, dataset_key=f"GST:{PAN_A}:GSTR3B:SEP_2026")
    orphan = put(db, "SGT_live", "BACK-OFFICE", "AA190926000001Z",
                 dataset_key="GST:SGTABCDEF123456:GSTR1:SEP_2026")
    db.re_resolve_all_tracker_dumps()
    cid, unassigned = identity(db, orphan)
    assert cid is None and unassigned != PAN_A


def test_an_sgt_row_borrows_none_even_from_its_own_pc(db):
    put(db, "SGT_live", "FRONT-DESK", "N/A", pan=PAN_A, dataset_key=f"GST:{PAN_A}:GSTR3B:SEP_2026")
    orphan = put(db, "SGT_live", "FRONT-DESK", "AA190926000002Z",
                 dataset_key="GST:SGTABCDEF654321:GSTR1:SEP_2026")
    db.re_resolve_all_tracker_dumps()
    assert identity(db, orphan)[1] != PAN_A


def test_another_engines_row_never_borrows_from_another_pc(db):
    put(db, "VSDC", "FRONT-DESK", "N/A", pan=PAN_A)
    orphan = put(db, "VSDC", "BACK-OFFICE", "AA190926000003Z")
    assert identity(db, orphan)[1] != PAN_A          # not at insert...
    db.re_resolve_all_tracker_dumps()
    assert identity(db, orphan)[1] != PAN_A          # ...nor when re-resolved


def test_another_engines_row_still_takes_its_own_pcs_previous_identity(db):
    # The older engines' wizard pages carry no PAN; within ONE PC the behaviour is unchanged.
    put(db, "VSDC", "FRONT-DESK", "N/A", pan=PAN_A)
    row = put(db, "VSDC", "FRONT-DESK", "AA190926000004Z")
    assert identity(db, row)[1] == PAN_A
    db.re_resolve_all_tracker_dumps()
    assert identity(db, row)[1] == PAN_A


def test_a_row_that_does_not_say_its_pc_borrows_nothing(db):
    put(db, "VSDC", "FRONT-DESK", "N/A", pan=PAN_A)
    out = db.insert_tracker_dump(portal=GST, arn_number="AA190926000005Z", capture_method="VSDC",
                                 status="Submitted", raw_payload_json=json.dumps({}))
    db.re_resolve_all_tracker_dumps()
    assert identity(db, out["id"])[1] != PAN_A


def test_the_helpers():
    assert is_sgt_capture("SGT_live") and is_sgt_capture("SGT_sdis_info") and not is_sgt_capture("VSDC")
    assert not is_sgt_capture(None)
    assert payload_device_name(payload("PC-1")) == "PC-1"
    assert payload_device_name(json.dumps({"raw_payload": {"device_name": "PC-2"}})) == "PC-2"
    assert payload_device_name("not json") == "" and payload_device_name(None) == ""
