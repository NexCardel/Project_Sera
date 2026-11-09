"""
2026-10-11: a container's list row shows the latest period captured, the form that has been submitted in it
(otherwise the form captured last) and that filing's status. Identifiers are fictional.
"""
import json

import pytest

import security
from database import SeraDatabase
from sera_db.srpf import headline_entry

GSTIN = "29ABACD1191FAZK"


def h(form, period, status, created, arn="N/A"):
    return {"portal": f"GST Portal ({form})", "period_label": period, "status": status, "created_at": created, "arn": arn}


SEP, AUG = "September (FY 2026-27)", "August (FY 2026-27)"


class TestHeadline:
    def test_the_latest_period_wins_over_the_latest_capture(self):
        hist = [h("GSTR-1", SEP, "Submitted & Verified", "2026-10-01"), h("GSTR-1", AUG, "Draft", "2026-10-05")]
        assert headline_entry(hist)["period_label"] == SEP

    def test_in_that_period_the_submitted_form_wins(self):
        hist = [h("GSTR-1", SEP, "Submitted & Verified", "2026-10-01"), h("GSTR-3B", SEP, "Draft", "2026-10-05")]
        assert headline_entry(hist)["portal"] == "GST Portal (GSTR-1)"

    def test_several_submitted_forms_take_the_last_captured_of_them(self):
        hist = [h("GSTR-3B", SEP, "Submitted & Verified", "2026-10-03"), h("GSTR-1", SEP, "Submitted & Verified", "2026-10-01"),
                h("GSTR-9", SEP, "Draft", "2026-10-09")]
        assert headline_entry(hist)["portal"] == "GST Portal (GSTR-3B)"

    def test_none_submitted_takes_the_form_captured_last(self):
        hist = [h("GSTR-1", SEP, "Draft", "2026-10-01"), h("GSTR-3B", SEP, "Not Submitted", "2026-10-05")]
        assert headline_entry(hist)["portal"] == "GST Portal (GSTR-3B)"

    def test_periods_of_other_labels_and_years(self):
        hist = [h("ITR-4", "AY 2025-26", "Submitted & Verified", "2026-09-01"), h("ITR-4", "AY 2026-27", "Draft", "2026-08-01")]
        assert headline_entry(hist)["period_label"] == "AY 2026-27"
        hist = [h("GSTR-1", "Jun - 2026", "Draft", "2026-07-01"), h("GSTR-1", "May - 2026", "Draft", "2026-08-01")]
        assert headline_entry(hist)["period_label"] == "Jun - 2026"

    def test_unreadable_periods_fall_back_to_the_last_capture(self):
        hist = [h("GSTR-1", "something", "Draft", "2026-07-01"), h("GSTR-3B", "other", "Draft", "2026-08-01")]
        assert headline_entry(hist)["portal"] == "GST Portal (GSTR-3B)"

    def test_empty(self):
        assert headline_entry([]) is None


@pytest.fixture()
def db(tmp_path):
    salt = str(tmp_path / "t.salt")
    security.generate_and_save_salt(salt)
    hex_key = security.derive_key_hex("testpass123", security.load_salt(salt))
    return SeraDatabase(str(tmp_path / "m.db"), hex_key, raw_db_path=str(tmp_path / "r.db"))


def capture(db, form, period, status, arn):
    p = {"portal": "GST Portal", "gstin": GSTIN, "filing_type": form, "period_label": period, "arn": arn,
         "status": status, "capture_method": "SGT_live"}
    db.insert_tracker_dump(portal=f"GST Portal ({form})", period_label=period, arn_number=arn,
                           capture_method="SGT_live", status=status, raw_payload_json=json.dumps(p), pan=GSTIN,
                           filing_type=form)


@pytest.mark.parametrize("slim", [True, False])
def test_the_container_row_carries_the_headline(db, slim):
    capture(db, "GSTR-1", SEP, "Submitted & Verified", "AA290926000001Z")
    capture(db, "GSTR-3B", SEP, "Draft", "N/A")
    capture(db, "GSTR-1", AUG, "Draft", "N/A")                  # captured last, but an earlier period
    rows = db.get_srpf_containers(limit=-1, slim=slim)
    assert len(rows) == 1
    r = rows[0]
    assert r["latest_period"] == SEP
    assert r["portal"] == "GST Portal (GSTR-1)"
    assert r["status"] == "Submitted & Verified" and r["latest_arn"] == "AA290926000001Z"
