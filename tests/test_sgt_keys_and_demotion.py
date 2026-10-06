"""
2026-10-06: the filing preference is part of the dataset key (a revised return is not the original), a quarter with
no year in its label keys as the whole range, and lowering a status that has an ARN is put to the user.
Identifiers are fictional.
"""
import json
import os

import pytest

import security
from core.dataset_key import compute_dataset_key as key
from core.sgt.sgt_shadow import SgtShadow, _Session
from database import SeraDatabase
from test_sgt_shadow import GST, PROFILE_PAGE, PROFILE_URL, SUBMITTED, WIZ_PI, WIZ_STATUS, Rig
import test_sgt_gst_fixes as G

PAN = "ABCPD1234E"
SUCCESS = SUBMITTED.replace("fo-itr4-ay2026/fo-submit-success", "x/done")


class TestKey:
    def test_original_or_no_preference_keeps_every_existing_key(self):
        base = key("Income Tax", PAN, "ITR-4", "AY 2026-27")
        assert base == "ITR:ABCPD1234E:ITR4:AY_2026_27"
        assert key("Income Tax", PAN, "ITR-4", "AY 2026-27", "Original") == base
        assert key("Income Tax", PAN, "ITR-4", "AY 2026-27", "original ") == base
        assert key("Income Tax", PAN, "ITR-4", "AY 2026-27", "") == base

    @pytest.mark.parametrize("pref", ["Revised", "Belated", "Updated"])
    def test_any_other_preference_is_a_different_dataset(self, pref):
        k = key("Income Tax", PAN, "ITR-4", "AY 2026-27", pref)
        assert k == f"ITR:ABCPD1234E:ITR4:AY_2026_27:{pref.upper()}"
        assert k != key("Income Tax", PAN, "ITR-4", "AY 2026-27")

    def test_a_quarter_with_no_year_is_the_whole_range(self):
        assert key("GST Portal", "29ABACD1191FAZK", "GSTR-3B", "Apr-Jun") == "GST:29ABACD1191FAZK:GSTR3B:APR_JUN"
        assert key("GST Portal", "29ABACD1191FAZK", "GSTR-3B", "Jul - Sep") == "GST:29ABACD1191FAZK:GSTR3B:JUL_SEP"
        assert key("GST Portal", "29ABACD1191FAZK", "GSTR-3B", "Apr-Jun") != \
            key("GST Portal", "29ABACD1191FAZK", "GSTR-3B", "April")

    def test_labels_with_a_year_are_unchanged(self):
        assert key("GST Portal", "29ABACD1191FAZK", "GSTR-3B", "June (FY 2026-27)").endswith("AY_2026_27_JUN")
        assert key("GST Portal", "29ABACD1191FAZK", "GSTR-1", "Jun - 2026").endswith(":JUN_2026")
        assert key("GST Portal", "29ABACD1191FAZK", "GSTR-1", "Apr-Jun 2026").endswith(":APR_JUN_2026")
        assert key("GST Portal", "29ABACD1191FAZK", "GSTR-1", "May").endswith(":MAY")

    def test_sgt_live_rows_carry_the_preference(self):
        s = _Session(portal="Income Tax")
        s.confirmed = True
        s.profile = {"pan": {"value": PAN}}
        values = {"form": "ITR-4", "period": "AY 2026-27", "filing_type": "Revised"}
        assert SgtShadow.live_key(s, values) == "ITR:ABCPD1234E:ITR4:AY_2026_27:REVISED"
        assert SgtShadow.live_key(s, dict(values, filing_type="Original")) == "ITR:ABCPD1234E:ITR4:AY_2026_27"
        assert SgtShadow.live_key(s, {"form": "ITR-4", "period": "AY 2026-27"}) == "ITR:ABCPD1234E:ITR4:AY_2026_27"


@pytest.fixture()
def db(tmp_path):
    salt = str(tmp_path / "t.salt")
    security.generate_and_save_salt(salt)
    hex_key = security.derive_key_hex("testpass123", security.load_salt(salt))
    return SeraDatabase(str(tmp_path / "m.db"), hex_key, raw_db_path=str(tmp_path / "r.db"))


def rows(db):
    with db._connect_raw() as c:
        return [tuple(r) for r in c.execute("SELECT dataset_key, status, arn_number FROM tracker_dump ORDER BY id")]


def put(db, status, arn, pref="Original", flag=False, form="ITR-4", period="AY 2026-27", portal="Income Tax"):
    p = {"portal": portal, "pan": PAN, "filing_type": form, "filing_preference": pref, "period_label": period,
         "arn": arn, "status": status, "capture_method": "SGT_live"}
    if flag:
        p["status_correction"] = True
    return db.insert_tracker_dump(portal=f"{portal} ({form})", period_label=period, arn_number=arn,
                                  capture_method="SGT_live", status=status, raw_payload_json=json.dumps(p), pan=PAN,
                                  filing_type=form)


class TestTracker:
    def test_original_and_revised_returns_are_two_rows(self, db):
        put(db, "Submitted & Verified", "123456789150726")
        put(db, "Submitted & Verified", "123456789200726", pref="Revised")
        assert sorted(r[0] for r in rows(db)) == ["ITR:ABCPD1234E:ITR4:AY_2026_27",
                                                  "ITR:ABCPD1234E:ITR4:AY_2026_27:REVISED"]

    def test_the_same_return_seen_again_still_replaces_its_own_row(self, db):
        put(db, "Submitted (Not Verified)", "123456789150726", pref="Revised")
        put(db, "Submitted & Verified", "123456789150726", pref="Revised")
        assert [(r[1]) for r in rows(db)] == ["Submitted & Verified"]

    def test_the_tracker_refuses_to_lower_a_status_unless_told_it_is_a_correction(self, db):
        put(db, "Submitted & Verified", "123456789150726")
        put(db, "Submitted (Not Verified)", "123456789150726")
        assert [r[1] for r in rows(db)] == ["Submitted & Verified"]
        put(db, "Submitted (Not Verified)", "123456789150726", flag=True)
        assert [r[1] for r in rows(db)] == ["Submitted (Not Verified)"]


def itr_return(r):
    r.see(PROFILE_URL, PROFILE_PAGE, shade=90)
    r.see(WIZ_STATUS, ["Assessment Year", "2026-27", "Filing Type", "x"], shade=100)
    r.see(WIZ_PI, ["Personal Information", "x", "y"], shade=110)


VERIFIED = ["You have successfully e-verified your return!", "Acknowledgement Number :", "123456789150726", "OK"]
PENDING = ["You have successfully submitted your return!", "Acknowledgement Number :", "123456789150726",
           "You still need to e-Verify within 30 days"]


class TestDemotionWithAnArn:
    def make(self, tmp_path):
        r = Rig(tmp_path)
        self.asked = []
        r.sgt._ask_demotion = self.asked.append
        itr_return(r)
        r.see(SUCCESS, VERIFIED, shade=120)
        r.sgt.drain()
        return r

    def status(self, r):
        return [s.values["status"] for s in r.sgt._sessions[1].slots]

    def test_a_lower_status_is_asked_about_never_applied(self, tmp_path):
        r = self.make(tmp_path)
        r.see(SUCCESS, PENDING, shade=130, advance=20)
        assert self.status(r) == ["Submitted & Verified"]
        assert len(self.asked) == 1
        info = self.asked[0]
        assert info["arn"] == "123456789150726" and info["from"] == "Submitted & Verified"
        assert info["to"] == "Submitted (Not Verified)" and info["form"] == "ITR-4" and info["token"]
        assert r.sgt.drain() == []

    def test_it_is_asked_once_per_stay_on_the_page(self, tmp_path):
        r = self.make(tmp_path)
        r.see(SUCCESS, PENDING, shade=130, advance=20)
        r.see(SUCCESS, PENDING, shade=131, advance=20)
        assert len(self.asked) == 1

    def test_yes_lowers_it_and_tells_the_tracker_it_is_deliberate(self, tmp_path):
        r = self.make(tmp_path)
        r.see(SUCCESS, PENDING, shade=130, advance=20)
        r.sgt.answer_demotion(self.asked[0]["token"], True)
        r.see(SUCCESS, PENDING, shade=131, advance=20)           # the next tick applies the answer
        assert self.status(r) == ["Submitted (Not Verified)"]
        sent = r.sgt.drain()
        assert len(sent) == 1 and sent[0]["status"] == "Submitted (Not Verified)" and sent[0]["status_correction"] is True

    def test_the_correction_flag_is_sent_once(self, tmp_path):
        r = self.make(tmp_path)
        r.see(SUCCESS, PENDING, shade=130, advance=20)
        r.sgt.answer_demotion(self.asked[0]["token"], True)
        r.see(SUCCESS, PENDING, shade=131, advance=20)
        r.sgt.drain()
        r.sgt._queue(r.sgt._sessions[1], r.sgt._sessions[1].slots[0])
        assert r.sgt.drain()[0]["status_correction"] is False

    def test_no_keeps_it_and_does_not_ask_again(self, tmp_path):
        r = self.make(tmp_path)
        r.see(SUCCESS, PENDING, shade=130, advance=20)
        r.sgt.answer_demotion(self.asked[0]["token"], False)
        r.see(SUCCESS, PENDING, shade=131, advance=20)
        assert self.status(r) == ["Submitted & Verified"] and len(self.asked) == 1 and r.sgt.drain() == []

    def test_without_a_dialog_hook_the_status_is_simply_kept(self, tmp_path):
        r = Rig(tmp_path)
        itr_return(r)
        r.see(SUCCESS, VERIFIED, shade=120)
        r.see(SUCCESS, PENDING, shade=130, advance=20)
        assert self.status(r) == ["Submitted & Verified"]


class TestDemotionWithoutAnArn:
    def test_a_gst_page_showing_not_filed_lowers_its_row_without_asking(self, tmp_path):
        r = Rig(tmp_path)
        asked = []
        r.sgt._ask_demotion = asked.append
        G.client(r)
        r.see(G.R3B, G.r3b_page("June", "Filed"), shade=100, portal=GST)
        r.sgt.drain()
        r.see(G.R3B, G.r3b_page("June", "Not Filed"), shade=110, portal=GST, advance=20)
        sent = r.sgt.drain()
        assert asked == [] and len(sent) == 1
        assert sent[0]["status"] == "Draft"
        assert sent[0]["status_correction"] is True


class TestRouterWiring:
    def test_the_router_forwards_the_question_and_the_answer(self):
        from core.vsdc.vsdc_router import VSDCRouter
        router = object.__new__(VSDCRouter)
        got = []
        router.on_demotion = got.append
        router.notify_demotion({"token": "t"})
        assert got == [{"token": "t"}]

        class Fake:
            def __init__(self):
                self.calls = []

            def answer_demotion(self, token, lower):
                self.calls.append((token, lower))
        router._sgt = Fake()
        router.answer_demotion("t", True)
        assert router._sgt.calls == [("t", True)]
