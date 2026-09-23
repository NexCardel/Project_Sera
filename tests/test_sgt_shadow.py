"""
SGT shadow mode: the per-window session it builds, when it reads, what it logs - and that it
never changes what the live pipeline does. All identifiers are fictional.
"""
import json
import os
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from core.sgt.sgt_shadow import IDLE_END_SEC, REREAD_AFTER_SEC, SgtShadow, is_blind
from core.sgt.sgt_specs import BUILTIN_FIELDS_PATH, SpecStore
from core.vsdc.vsdc_engines import SGT_DEFAULT, read_sgt_mode
from core.vsdc.vsdc_router import SGT_OFF, SGT_SHADOW

ITR = "Income Tax"
GST = "GST Portal"
PROFILE_URL = "https://eportal.incometax.gov.in/iec/foservices/#/dashboard/myProfile/profileDetail"
FILED_URL = "https://eportal.incometax.gov.in/iec/foservices/#/dashboard/itrStatus"
LOGOUT_URL = "https://eportal.incometax.gov.in/iec/foservices/#/logout"
LOGIN_URL = "https://eportal.incometax.gov.in/iec/foservices/#/login"

PROFILE_PAGE = ["ASHOK KUMAR SEN Individual", "Profile", "Name", "ASHOK KUMAR SEN", "PAN", "ABCPD1234E",
                "Date of Birth", "12-Mar-1980"]


def filed_page(status="Pending for e-verification", ack="123456789150925"):
    return ["ASHOK KUMAR SEN Individual", "A.Y. 2025-26", "Filing Type", "Original", "done", status,
            "ITR :", "ITR-4", "Acknowledgement No :", ack]


def frame(shade):
    return Image.new("RGB", (64, 64), (shade,) * 3)


class Rig:
    def __init__(self, tmp_path, dispatched=()):
        self.clock = [1_000_000.0]
        self.page = []
        self.reads = 0
        self.echoes = []
        self.log_dir = tmp_path / "shadow"

        def read(hwnd):
            self.reads += 1
            return {"lines": list(self.page)}

        self.sgt = SgtShadow(store=SpecStore([BUILTIN_FIELDS_PATH], log=lambda m: None), read_uia=read,
                             dispatched_ids=lambda: list(dispatched), log_dir=self.log_dir,
                             clock=lambda: self.clock[0], today=lambda: date(2026, 7, 15),
                             echo=self.echoes.append)

    def see(self, url, page, shade=100, hwnd=1, advance=0.35, portal=ITR, ocr=None):
        self.clock[0] += advance
        self.page = page
        return self.sgt.observe(hwnd, portal, url, frame=frame(shade), ocr=ocr)

    def events(self, kind=None):
        out = []
        for f in sorted(self.log_dir.glob("*.jsonl")):
            out += [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()]
        return [e for e in out if kind is None or e["event"] == kind]


class TestReading:
    def test_a_still_page_is_read_once(self, tmp_path):
        r = Rig(tmp_path)
        for _ in range(5):
            r.see(PROFILE_URL, PROFILE_PAGE, shade=100)
        assert r.reads == 1

    def test_a_changed_screen_or_link_is_read_again(self, tmp_path):
        r = Rig(tmp_path)
        r.see(PROFILE_URL, PROFILE_PAGE, shade=100)
        r.see(PROFILE_URL, PROFILE_PAGE, shade=120)
        r.see(FILED_URL, filed_page(), shade=120)
        assert r.reads == 3

    def test_a_still_page_is_reread_after_a_while(self, tmp_path):
        r = Rig(tmp_path)
        r.see(PROFILE_URL, PROFILE_PAGE)
        r.see(PROFILE_URL, PROFILE_PAGE, advance=REREAD_AFTER_SEC + 1)
        assert r.reads == 2

    def test_blind_page_falls_back_to_ocr(self, tmp_path):
        r = Rig(tmp_path)
        ocr = MagicMock()
        ocr.scan_image.return_value = {"lines": PROFILE_PAGE}
        r.see(PROFILE_URL, ["Enable accessibility"], ocr=ocr)
        ocr.scan_image.assert_called_once()
        assert r.events("profile")[0]["source"] == "ocr"
        assert {e["field"] for e in r.events("profile")} == {"name", "pan", "dob"}

    def test_blind_page_without_ocr_captures_nothing(self, tmp_path):
        r = Rig(tmp_path)
        assert r.see(PROFILE_URL, []) is None
        assert r.events() == []

    def test_blindness(self):
        assert is_blind([]) and is_blind(["Enable accessibility"]) and is_blind(["a", "b"])
        assert not is_blind(["a", "b", "c"])

    def test_logout_pages_are_never_read(self, tmp_path):
        r = Rig(tmp_path)
        r.see(LOGOUT_URL, PROFILE_PAGE)
        assert r.reads == 0

    def test_the_pan_on_the_password_page_starts_the_new_clients_session(self, tmp_path):
        r = Rig(tmp_path)
        r.see(PROFILE_URL, PROFILE_PAGE, shade=90)                      # previous client
        r.see(LOGIN_URL, ["Enter your User ID", "x", "y"], shade=100)   # closes it
        r.see(LOGIN_URL + "/password", ["PAN", "XYZAB9876C", "Secure Access Message", "Password"], shade=110)
        r.see(DASHBOARD, ["Welcome Back, MEERA", "XYZAB9876C", "z"], shade=120)
        r.sgt.end_all("quit")
        pans = [e["payload"]["client_profile"].get("pan") for e in r.events("session_end")]
        assert pans == ["ABCPD1234E", "XYZAB9876C"]
        first = [e for e in r.events("profile") if e["value"] == "XYZAB9876C"][0]
        assert first["page"].endswith("#/login/password")


class TestPortal:
    def test_an_empty_session_takes_the_portal_it_is_on(self, tmp_path):
        r = Rig(tmp_path)
        r.see("https://services.gst.gov.in/services/login", ["x", "y", "z"], portal=GST, shade=90)
        r.see(PROFILE_URL, PROFILE_PAGE, shade=100)
        r.sgt.end_all("quit")
        ends = r.events("session_end")
        assert len(ends) == 1 and ends[0]["payload"]["portal"] == ITR and ends[0]["portal"] == ITR

    def test_moving_to_the_other_portal_with_a_client_ends_the_session(self, tmp_path):
        r = Rig(tmp_path)
        r.see(PROFILE_URL, PROFILE_PAGE, shade=90)
        r.see("https://services.gst.gov.in/services/auth/fowelcome",
              ["Welcome ASHOK KUMAR SEN to GST Common Portal", "19ABCPD1234E1ZB", "x"], portal=GST, shade=100)
        r.sgt.end_all("quit")
        ends = r.events("session_end")
        assert [(e["reason"], e["payload"]["portal"]) for e in ends] == [
            ("moved to GST Portal", ITR), ("quit", GST)]


class TestProfile:
    def test_profile_latches_and_is_logged_once(self, tmp_path):
        r = Rig(tmp_path)
        r.see(PROFILE_URL, PROFILE_PAGE, shade=100)
        r.see(PROFILE_URL, PROFILE_PAGE, shade=110)
        r.see(PROFILE_URL, ["Name", "SOMEONE ELSE", "PAN", "XYZAB9876C", "x"], shade=120)
        pans = [e["value"] for e in r.events("profile") if e["field"] == "pan"]
        assert pans == ["ABCPD1234E"]


class TestNamePromotion:
    """The name is "promote_longer": a fuller name seen later replaces a shorter one it extends."""

    def names(self, r):
        return [(e.get("change"), e["value"]) for e in r.events("profile") if e["field"] == "name"]

    def test_a_longer_name_that_extends_the_held_one_replaces_it(self, tmp_path):
        r = Rig(tmp_path)
        r.see(FILED_URL, ["ASHOK SEN Individual", "x", "y"], shade=100)
        r.see(PROFILE_URL, ["Name", "ASHOK KUMAR SEN", "PAN", "ABCPD1234E"], shade=110)
        assert self.names(r) == [(None, "ASHOK SEN"), ("promoted", "ASHOK KUMAR SEN")]
        r.sgt.end_all("quit")
        assert r.events("session_end")[0]["payload"]["client_profile"]["name"] == "ASHOK KUMAR SEN"

    def test_a_shorter_name_later_never_demotes(self, tmp_path):
        r = Rig(tmp_path)
        r.see(PROFILE_URL, ["Name", "ASHOK KUMAR SEN", "PAN", "ABCPD1234E"], shade=100)
        r.see(FILED_URL, ["ASHOK SEN Individual", "x", "y"], shade=110)
        r.sgt.end_all("quit")
        assert r.events("session_end")[0]["payload"]["client_profile"]["name"] == "ASHOK KUMAR SEN"

    def test_a_longer_but_different_name_is_logged_not_taken(self, tmp_path):
        r = Rig(tmp_path)
        r.see(FILED_URL, ["ASHOK SEN Individual", "x", "y"], shade=100)
        r.see(PROFILE_URL, ["Name", "MEERA RANI CHATTERJEE", "PAN", "ABCPD1234E"], shade=110)
        assert self.names(r)[-1] == ("not promoted", "MEERA RANI CHATTERJEE")
        r.sgt.end_all("quit")
        assert r.events("session_end")[0]["payload"]["client_profile"]["name"] == "ASHOK SEN"

    def test_the_pan_still_latches(self, tmp_path):
        r = Rig(tmp_path)
        r.see(PROFILE_URL, PROFILE_PAGE, shade=100)
        before = r.reads
        r.see(PROFILE_URL, ["Name", "ASHOK KUMAR SEN", "PAN", "XYZAB9876C"], shade=110)
        assert r.reads == before + 1
        assert [e["value"] for e in r.events("profile") if e["field"] == "pan"] == ["ABCPD1234E"]


WIZ_STATUS = "https://eportal.incometax.gov.in/iec/foservices/#/foreturns-ay26/fo-itr-shared/fo-select-status"
WIZ_FORM = "https://eportal.incometax.gov.in/iec/foservices/#/foreturns-ay26/fo-itr-shared/fo-select-itr-form"
WIZ_PI = "https://eportal.incometax.gov.in/iec/foservices/#/foreturns-ay26/fo-itr4-ay2026/personal_information"
SUBMITTED = "https://eportal.incometax.gov.in/iec/foservices/#/foreturns-ay26/fo-itr4-ay2026/fo-submit-success"
DASHBOARD = "https://eportal.incometax.gov.in/iec/foservices/#/dashboard"
GST_RET_DASH = "https://return.gst.gov.in/returns/auth/dashboard"
GST_R1 = "https://return.gst.gov.in/returns/auth/gstr1"


class TestReturnInProgress:
    """Datasets built from pieces on many pages - nothing in the rules names a page."""

    def end(self, r):
        r.sgt.end_all("quit")
        ends = r.events("session_end")
        return ends[-1]["payload"]["datasets"] if ends else []

    def test_pieces_from_several_pages_make_one_return(self, tmp_path):
        r = Rig(tmp_path)
        r.see(PROFILE_URL, PROFILE_PAGE, shade=90)
        r.see(WIZ_STATUS, ["Assessment Year", "2026-27", "Filing Type", "x",
                           "139(1)-On or before due date", "Selected: 139(1)-On or before due date"], shade=100)
        r.see(WIZ_FORM, ["ITR-1 (SAHAJ)", "ITR-2", "ITR-4 (SUGAM)", "Select ITR Form", "ITR-4"], shade=110)
        r.see(WIZ_PI, ["Personal Information", "PAN", "ABCPD1234E"], shade=120)
        ds = self.end(r)
        assert ds == [{"form": "ITR-4", "period": "AY 2026-27", "filing_type": "Original",
                       "status": "Draft", "status_evidence": "form + period captured (in the page link)",
                       "record": "current_dataset"}]

    def test_a_submission_takes_form_and_period_from_the_return_in_progress(self, tmp_path):
        r = Rig(tmp_path)
        r.see(WIZ_PI, ["Personal Information", "x", "y"], shade=100)
        r.see(SUBMITTED.replace("fo-itr4-ay2026/fo-submit-success", "x/done"),
              ["You have successfully submitted your return!", "Acknowledgement Number :", "123456789150726",
               "You still need to e-Verify within 30 days"], shade=110)
        ds = self.end(r)
        assert len(ds) == 1
        assert (ds[0]["form"], ds[0]["period"], ds[0]["arn"], ds[0]["status"]) == \
            ("ITR-4", "AY 2026-27", "123456789150726", "Submitted (Not Verified)")
        assert any(e.get("change") == "completed by a submission" for e in r.events("current"))

    def test_going_back_and_changing_a_field_changes_the_piece(self, tmp_path):
        r = Rig(tmp_path)
        r.see(WIZ_FORM, ["Select ITR Form", "ITR-1", "a"], shade=100)
        r.see(WIZ_STATUS, ["Assessment Year", "2026-27", "b"], shade=110)
        r.see(WIZ_FORM, ["Select ITR Form", "ITR-4", "a"], shade=120)          # went back, picked again
        changes = [(e["field"], e["change"], e["value"]) for e in r.events("current")]
        assert ("form", "changed", "ITR-4") in changes

    def test_going_back_and_emptying_a_field_clears_the_piece(self, tmp_path):
        r = Rig(tmp_path)
        r.see(WIZ_FORM, ["Select ITR Form", "ITR-4", "a"], shade=100)
        r.see(WIZ_FORM, ["Select ITR Form", "Select", "a"], shade=110)
        r.see(WIZ_FORM, ["Select ITR Form", "Select", "a"], shade=120, advance=6)
        assert [(e["field"], e["change"]) for e in r.events("current")][-1] == ("form", "cleared")

    def test_the_blank_moment_after_continue_clears_nothing(self, tmp_path):
        r = Rig(tmp_path)
        url = "https://eportal.incometax.gov.in/iec/foservices/#/dashboard/fileIncomeTaxReturn"
        r.see(url, ["2025-26 selected in assessment year", "2025-26",
                    "Selected: 2025-26 selected in assessment year = 2025-26", "x"], shade=100)
        for shade in (110, 120, 130):                                            # blank, same link, ~1 s
            r.see(url, ["Loading", "a", "b"], shade=shade)
        r.see(WIZ_STATUS, ["x", "y", "z"], shade=140)                             # the next page arrives
        assert not any(e["change"] == "cleared" for e in r.events("current"))

    def test_the_form_choice_page_dropdown(self, tmp_path):
        """Real probe: the portal writes the value 'ITR - 2', with spaces around the dash."""
        r = Rig(tmp_path)
        r.see(WIZ_FORM, ["I know which ITR Form I need to file", "ITR - 2",
                         "Selected: I know which ITR Form I need to file = ITR - 2", "x"], shade=100)
        assert [(e["field"], e["value"], e["spec"]) for e in r.events("current") if e["field"] == "form"] == \
            [("form", "ITR-2", "cur_itr_form_selected")]

    def test_one_half_drawn_read_does_not_clear_a_piece(self, tmp_path):
        r = Rig(tmp_path)
        r.see(WIZ_FORM, ["Select ITR Form", "ITR-4", "a"], shade=100)
        r.see(WIZ_FORM, ["Loading", "a", "b"], shade=110)                       # mid-render
        r.see(WIZ_FORM, ["Select ITR Form", "ITR-4", "a"], shade=120)
        r.see(WIZ_FORM, ["Loading", "a", "b"], shade=130)                       # again, but not in a row
        assert not any(e["change"] == "cleared" for e in r.events("current"))

    # The personal-information page lists EVERY filing section as plain text under "Filed u/s"
    # (taken from a real dump). Reading "the line after the label" once gave "Belated".
    FILED_US = ["Filing Section", "Filing Section", "Filed u/s", "Filed u/s",
                "139(1) Return filed on or before due date", "139(1)", "Return filed on or before due date",
                "139(4) Belated- Return filed after due date", "139(4)", "Belated- Return filed after due date",
                "139(5) Revised- Return revised after filing original return", "139(5)",
                "139(8A) Updated Return", "139(8A)", "Updated Return"]

    def test_an_option_list_is_never_read_as_the_filing_type(self, tmp_path):
        r = Rig(tmp_path)
        r.see(WIZ_PI, self.FILED_US, shade=100)
        assert "filing_type" not in [e["field"] for e in r.events("current")]

    def test_the_ticked_option_is_the_filing_type(self, tmp_path):
        r = Rig(tmp_path)
        r.see(WIZ_PI, self.FILED_US + ["Selected: 139(1) Return filed on or before due date"], shade=100)
        ft = [e["value"] for e in r.events("current") if e["field"] == "filing_type"]
        assert ft == ["Original"]

    def test_the_file_itr_page_dropdowns_as_the_portal_names_them(self, tmp_path):
        """Real names from a probe: the AY dropdown's name carries its value, in lower case."""
        r = Rig(tmp_path)
        page = ["English selected under language", "English", "Selected: English selected under language = English",
                "2025-26 selected in assessment year", "2025-26", "Selected: 2025-26 selected in assessment year = 2025-26",
                "Select Filing Type", "Select", "Selected: Select Filing Type = Select",
                "Select ITR Type", "ITR-3", "Selected: Select ITR Type = ITR-3"]
        url = "https://eportal.incometax.gov.in/iec/foservices/#/dashboard/fileIncomeTaxReturn"
        r.see(url, page, shade=100)
        r.see(url, page, shade=110)
        r.see(url, page, shade=120)
        cur = [(e["field"], e["change"], e["value"], e["spec"]) for e in r.events("current")]
        # Read once, stable across re-reads (no set-then-cleared), and "Select" is not a choice.
        assert sorted(cur) == [("form", "set", "ITR-3", "cur_itr_form_selected"),
                               ("period", "set", "AY 2025-26", "cur_itr_period_selected")]

    def test_the_file_itr_page_with_every_choice_made(self, tmp_path):
        """From a real probe (19:00): AY, a condonation filing type, ITR-3, and an unrelated Yes/No radio."""
        r = Rig(tmp_path)
        ft = ("u/s 139(9A) - After condonation of delay / Court Order or Sanction Order of Business "
              "reorganisation of the Competent authority issued prior to 01.04.2022 selected in filing type")
        val = ("u/s 139(9A) - After condonation of delay u/s 119(2)(b) / Court Order or Sanction Order of Business "
               "re-organisation of the Competent authority issued prior to 01.04.2022")
        page = ["2025-26 selected in assessment year", "2025-26", "Selected: 2025-26 selected in assessment year = 2025-26",
                ft, val, f"Selected: {ft} = {val}", "No", "Selected: No",
                "ITR-3 selected in ITR type", "ITR-3", "Selected: ITR-3 selected in ITR type = ITR-3"]
        r.see("https://eportal.incometax.gov.in/iec/foservices/#/dashboard/fileIncomeTaxReturn", page, shade=100)
        got = {e["field"]: e["value"] for e in r.events("current")}
        assert got == {"period": "AY 2025-26", "filing_type": "Condonation of Delay", "form": "ITR-3"}

    def test_a_dropdown_choice_counts(self, tmp_path):
        r = Rig(tmp_path)
        r.see(WIZ_STATUS, ["Assessment Year", "Selected: Assessment Year = 2025-26", "Select Filing Type",
                           "Selected: Select Filing Type = 139(4)-After due date", "x"], shade=100)
        got = {e["field"]: e["value"] for e in r.events("current")}
        assert got["filing_type"] == "Belated"
        assert got["period"] == "AY 2025-26"            # the link says ay26; what was picked wins

    def test_a_piece_seen_again_elsewhere_is_owned_by_the_newer_page(self, tmp_path):
        r = Rig(tmp_path)
        r.see(WIZ_FORM, ["Select ITR Form", "ITR-4", "a"], shade=100)
        r.see(DASHBOARD, ["Your return ITR-4 is saved", "a", "b"], shade=110)
        r.see(WIZ_FORM, ["Select ITR Form", "Select", "a"], shade=120)          # old page now empty
        assert not any(e["change"] == "cleared" for e in r.events("current"))

    def test_a_different_period_closes_the_return_and_starts_another(self, tmp_path):
        r = Rig(tmp_path)
        r.see(WIZ_PI, ["x", "y", "z"], shade=100)                                 # ITR-4, AY 2026-27
        r.see(WIZ_PI.replace("ay26", "ay25").replace("ay2026", "ay2025"), ["x", "y", "z"], shade=110)
        ds = self.end(r)
        assert sorted(d["period"] for d in ds) == ["AY 2025-26", "AY 2026-27"]

    def test_an_incomplete_return_is_never_dispatched(self, tmp_path):
        r = Rig(tmp_path)
        r.see(PROFILE_URL, PROFILE_PAGE, shade=90)
        r.see(WIZ_STATUS, ["Assessment Year", "2026-27", "b"], shade=100)       # period only
        assert self.end(r) == []
        dropped = [e for e in r.events("current") if e["change"].startswith("incomplete")]
        assert dropped and "form" in dropped[0]["missing"]

    def test_form_and_period_captured_make_a_draft_even_without_the_link(self, tmp_path):
        """The user's rule (2026-09-22): the datapoints captured decide the level - form + period = Draft."""
        r = Rig(tmp_path)
        r.see(PROFILE_URL, PROFILE_PAGE, shade=90)
        r.see(DASHBOARD, ["For Assessment Year 2026-27", "Your ITR-4", "c"], shade=100)
        ds = self.end(r)
        assert [(d["form"], d["period"], d["status"], d["status_evidence"]) for d in ds] ==             [("ITR-4", "AY 2026-27", "Draft", "form + period captured")]

    def test_list_pages_never_feed_the_return_in_progress(self, tmp_path):
        r = Rig(tmp_path)
        r.see(FILED_URL, filed_page(), shade=100)
        assert r.events("current") == []

    def test_gst_return_built_from_the_dashboard_then_the_return_page(self, tmp_path):
        r = Rig(tmp_path)
        r.see(GST_RET_DASH, ["File Returns", "Financial Year", "2026-27", "Period", "August", "SEARCH"],
              shade=100, portal=GST)
        r.see(GST_R1, ["GSTR-1 - Details of outward supplies", "Status -", "Filed", "x"], shade=110, portal=GST)
        ds = self.end(r)
        assert ds == [{"fy": "2026-27", "tax_period": "August", "form": "GSTR-1", "status": "Submitted & Verified",
                       "status_evidence": "Filed", "period": "August (FY 2026-27)", "record": "current_dataset"}]

    def test_title_is_read_too(self, tmp_path):
        r = Rig(tmp_path)
        r.clock[0] += 1
        r.page = ["Assessment Year", "2026-27", "x"]
        r.sgt.observe(1, ITR, WIZ_STATUS, frame=frame(100), title="ITR4 Part A General - Google Chrome")
        assert ("form", "ITR-4") in [(e["field"], e["value"]) for e in r.events("current")]


class TestLatestPeriodOnly:
    def test_a_list_page_keeps_only_the_latest_return(self, tmp_path):
        r = Rig(tmp_path)
        page = filed_page() + ["A.Y. 2026-27", "ITR Filed", "ITR :", "ITR-1", "Acknowledgement No :", "123456789150726"]
        r.see(FILED_URL, page, shade=100)
        news = [e for e in r.events("dataset") if e["change"] == "new"]
        assert [e["values"]["period"] for e in news] == ["AY 2026-27"]


class TestNameParts:
    PI = ["ASHOK SEN Individual", "First Name", "First Name", "ASHOK", "Middle Name", "Middle Name", "KUMAR",
          "Last Name", "Last Name", "SEN", "PAN", "PAN", "ABCPD1234E"]

    def name(self, r):
        r.sgt.end_all("quit")
        return r.events("session_end")[0]["payload"]["client_profile"]["name"]

    def test_first_middle_last_are_joined_and_promote_the_header_name(self, tmp_path):
        r = Rig(tmp_path)
        r.see(WIZ_PI, self.PI, shade=100)
        assert self.name(r) == "ASHOK KUMAR SEN"
        promo = [e for e in r.events("profile") if e.get("change") == "promoted"]
        assert promo and promo[0]["spec"] == "compose:name" and promo[0]["previous"] == "ASHOK SEN"

    def test_an_empty_middle_name_is_left_out(self, tmp_path):
        r = Rig(tmp_path)
        page = ["First Name", "First Name", "ASHOK", "Middle Name", "Middle Name",
                "Last Name", "Last Name", "SEN", "PAN", "PAN", "ABCPD1234E"]
        r.see(WIZ_PI, page, shade=100)
        assert self.name(r) == "ASHOK SEN"
        assert "middle_name" not in {e["field"] for e in r.events("profile")}

    def test_an_empty_last_name_never_takes_the_next_label(self, tmp_path):
        r = Rig(tmp_path)
        page = ["First Name", "First Name", "ASHOK", "Last Name", "Last Name", "PAN", "PAN", "ABCPD1234E"]
        r.see(WIZ_PI, page, shade=100)
        fields = {e["field"]: e["value"] for e in r.events("profile")}
        assert "last_name" not in fields and "name" not in fields and fields["first_name"] == "ASHOK"

    def test_a_joined_name_that_is_someone_else_does_not_replace_the_client(self, tmp_path):
        r = Rig(tmp_path)
        r.see(PROFILE_URL, PROFILE_PAGE, shade=90)                               # ASHOK KUMAR SEN
        r.see(WIZ_PI, ["First Name", "First Name", "MEERA", "Last Name", "Last Name", "DAS", "x"], shade=100)
        assert self.name(r) == "ASHOK KUMAR SEN"


class TestDatasets:
    def test_status_promotes_and_never_demotes(self, tmp_path):
        r = Rig(tmp_path)
        r.see(FILED_URL, filed_page("Pending for e-verification"), shade=100)
        r.see(FILED_URL, filed_page("Processed with refund"), shade=110)
        r.see(FILED_URL, filed_page("ITR Filed"), shade=120)
        r.sgt.end_all("test")
        ds = r.events("session_end")[0]["payload"]["datasets"]
        assert len(ds) == 1 and ds[0]["status"] == "Submitted & Verified"
        assert ds[0]["status_evidence"] == "Processed with refund"             # the portal's own wording
        updates = r.events("dataset")
        assert [e["change"] for e in updates] == ["new", "updated"]

    def test_a_disagreeing_value_keeps_the_first_and_says_so(self, tmp_path):
        r = Rig(tmp_path)
        r.see(FILED_URL, filed_page(), shade=100)
        page = filed_page()
        page[3] = "Revised"
        r.see(FILED_URL, page, shade=110)
        upd = r.events("dataset")[-1]
        assert upd["changes"]["filing_type"][2].startswith("disagrees")
        r.sgt.end_all("test")
        assert r.events("session_end")[0]["payload"]["datasets"][0]["filing_type"] == "Original"

    def test_ack_only_and_form_period_views_of_one_return_share_a_slot(self, tmp_path):
        r = Rig(tmp_path)
        r.see("https://eportal.incometax.gov.in/iec/foservices/#/x/submitted",
              ["You have successfully submitted your return!", "Acknowledgement Number :", "123456789150726"], shade=90)
        r.see(FILED_URL, filed_page(ack="123456789150726"), shade=100)
        r.sgt.end_all("test")
        ds = r.events("session_end")[0]["payload"]["datasets"]
        assert len(ds) == 1 and ds[0]["form"] == "ITR-4" and ds[0]["arn"] == "123456789150726"


class TestSessions:
    def test_logout_ends_the_session_with_the_would_be_payload(self, tmp_path):
        r = Rig(tmp_path, dispatched=["123456789150925"])
        r.see(PROFILE_URL, PROFILE_PAGE, shade=100)
        r.see(FILED_URL, filed_page(), shade=110)
        r.see(LOGOUT_URL, [], shade=120)
        end = r.events("session_end")
        assert len(end) == 1 and end[0]["reason"] == "logout"
        p = end[0]["payload"]
        assert p["client_profile"]["pan"] == "ABCPD1234E" and p["client_known"] is True
        assert p["datasets"][0]["live_pipeline_also_captured"] is True
        assert p["timeline"] == [PROFILE_URL, FILED_URL, LOGOUT_URL]
        assert "device_name" in p
        assert end[0]["stats"]["reads"] == 2

    def test_a_login_page_starts_the_next_client(self, tmp_path):
        r = Rig(tmp_path)
        r.see(PROFILE_URL, PROFILE_PAGE, shade=100)
        r.see(LOGIN_URL, [], shade=110)
        r.see(PROFILE_URL, ["Name", "MEERA DAS", "PAN", "XYZAB9876C", "x"], shade=120)
        r.sgt.end_all("test")
        ends = r.events("session_end")
        assert [e["payload"]["client_profile"]["pan"] for e in ends] == ["ABCPD1234E", "XYZAB9876C"]

    def test_login_in_the_middle_of_a_word_is_not_a_boundary(self, tmp_path):
        r = Rig(tmp_path)
        r.see(PROFILE_URL, PROFILE_PAGE, shade=100)
        r.see(PROFILE_URL.replace("profileDetail", "aadhaarOtpLogin"), ["x", "y", "z"], shade=110)
        assert r.events("session_end") == []

    def test_an_empty_login_session_is_not_logged(self, tmp_path):
        r = Rig(tmp_path)
        r.see(LOGIN_URL, [], shade=100)
        r.see(LOGIN_URL + "/password", [], shade=110)
        r.sgt.end_all("quit")
        assert r.events() == []

    def test_idle_ends_the_session(self, tmp_path):
        r = Rig(tmp_path)
        r.see(PROFILE_URL, PROFILE_PAGE)
        r.see(PROFILE_URL, PROFILE_PAGE, advance=IDLE_END_SEC + 1)
        assert r.events("session_end")[0]["reason"] == "idle 20 min"

    def test_two_windows_are_two_sessions(self, tmp_path):
        r = Rig(tmp_path)
        r.see(PROFILE_URL, PROFILE_PAGE, hwnd=1)
        r.see(PROFILE_URL, ["Name", "MEERA DAS", "PAN", "XYZAB9876C", "x"], hwnd=2)
        r.sgt.end_all("quit")
        pans = sorted(e["payload"]["client_profile"]["pan"] for e in r.events("session_end"))
        assert pans == ["ABCPD1234E", "XYZAB9876C"]

    def test_datasets_without_a_client_say_where_live_mode_would_send_them(self, tmp_path):
        r = Rig(tmp_path)
        r.see(FILED_URL, filed_page()[1:], shade=100)            # no header name, no PAN
        r.sgt.end_all("quit")
        p = r.events("session_end")[0]["payload"]
        assert p["client_known"] is False and p["datasets"]
        assert any("VSDC247's client-unknown path" in m for m in r.echoes)


class TestNeverDisturbs:
    def test_a_failing_read_is_swallowed(self, tmp_path):
        r = Rig(tmp_path)

        def boom(hwnd):
            raise RuntimeError("UIA gone")
        r.sgt._read_uia = boom
        assert r.see(PROFILE_URL, PROFILE_PAGE) is None
        assert any("error, page skipped" in m for m in r.echoes)

    def test_log_holds_values_not_page_text(self, tmp_path):
        r = Rig(tmp_path)
        r.see(PROFILE_URL, PROFILE_PAGE + ["A private note that is on screen"])
        r.sgt.end_all("quit")
        text = "".join(f.read_text(encoding="utf-8") for f in r.log_dir.glob("*.jsonl"))
        assert "private note" not in text and "ABCPD1234E" in text


# ── HUD pill ─────────────────────────────────────────────────────────────────────
class TestHud:
    def rig(self, tmp_path):
        r = Rig(tmp_path)
        r.hud = []
        r.sgt._notify = lambda *a: r.hud.append(a)
        return r

    def test_page_reads_and_half_built_datasets_never_reach_the_pill(self, tmp_path):
        r = self.rig(tmp_path)
        r.see(DASHBOARD, ["For Assessment Year 2026-27", "x", "y"], shade=100)       # a period, no form
        assert r.hud == []
        r.see(DASHBOARD, ["Your ITR-4 draft", "x", "y"], shade=110)                  # now form + period
        assert [(h[0], h[1]) for h in r.hud] == [("capture", "Dataset captured")]

    def test_a_return_in_progress_is_announced_once_it_is_complete(self, tmp_path):
        r = self.rig(tmp_path)
        r.see(WIZ_STATUS, ["Assessment Year", "2026-27", "x"], shade=100)
        assert r.hud == []                                                            # period only
        r.see(WIZ_FORM, ["Select ITR Form", "ITR-4", "y"], shade=110)                  # now complete
        assert [(h[0], h[1]) for h in r.hud] == [("capture", "Dataset captured")]

    def test_client_identified_once(self, tmp_path):
        r = self.rig(tmp_path)
        r.see(LOGIN_URL + "/password", ["PAN", "ABCPD1234E", "Password"], shade=100)
        r.see(PROFILE_URL, PROFILE_PAGE, shade=110)
        ids = [h for h in r.hud if h[0] == "identity"]
        assert len(ids) == 1 and "PAN: ABCPD1234E" in ids[0][2] and ids[0][2].endswith("SGT (Shadow)")

    def test_a_return_found_carries_sgts_own_context(self, tmp_path):
        r = self.rig(tmp_path)
        r.see(PROFILE_URL, PROFILE_PAGE, shade=90)
        r.see(FILED_URL, filed_page(), shade=100)
        cap = [h for h in r.hud if h[0] in ("capture", "submit") and "Dataset" not in h[1]
               and "Client details" not in h[1]]
        assert len(cap) == 1
        event_type, title, subtitle, ctx = cap[0]
        assert "ARN: 123456789150925" in subtitle and "ASHOK KUMAR SEN" in subtitle
        assert (ctx["form"], ctx["filing_pref"], ctx["period"], ctx["portal"]) == ("ITR-4", "Original", "AY 2025-26", ITR)

    def test_a_status_moving_forward_is_announced(self, tmp_path):
        r = self.rig(tmp_path)
        r.see(FILED_URL, filed_page("Pending for e-verification"), shade=100)
        r.see(FILED_URL, filed_page("Processed"), shade=110)
        ups = [h for h in r.hud if h[0] == "update"]
        assert ups and "Submitted (Not Verified) → Submitted & Verified" in ups[0][2]

    def test_session_end_on_logout_but_not_on_app_quit(self, tmp_path):
        r = self.rig(tmp_path)
        r.see(PROFILE_URL, PROFILE_PAGE, shade=90)
        r.see(LOGOUT_URL, [], shade=100)
        assert r.hud[-1][0] == "logout"
        r.hud.clear()
        r.see(PROFILE_URL, PROFILE_PAGE, shade=110)
        r.sgt.end_all("Application Shutdown")
        assert [h for h in r.hud if h[0] == "logout"] == []

    def test_a_failing_pill_never_breaks_capture(self, tmp_path):
        r = Rig(tmp_path)

        def boom(*a):
            raise RuntimeError("Qt gone")
        r.sgt._notify = boom
        r.see(PROFILE_URL, PROFILE_PAGE, shade=90)
        assert {"name", "pan", "dob"} <= {e["field"] for e in r.events("profile")}   # capture carried on
        assert any("HUD event failed" in m for m in r.echoes)

    def test_router_keeps_sgts_context_over_vsdcs(self):
        from tests.test_vsdc247_integration import Harness
        h = Harness()
        r = h.router
        r.assembler.current_filing_type = "GSTR-1"             # VSDC thinks something else
        r._hud_buffering = True
        r.notify_sgt("capture", "Return captured", "x • SGT (Shadow)",
                     {"portal": ITR, "form": "ITR-4", "filing_pref": "Original", "period": "AY 2026-27"})
        seen = []
        r.on_activity = lambda *a: seen.append(dict(r.activity_context))
        r._hud_buffering = False
        r._flush_hud_events()
        assert seen and seen[0]["form"] == "ITR-4" and seen[0]["period"] == "AY 2026-27" and "_own" not in seen[0]

    def test_pill_colours_the_sgt_tag(self):
        from ui.components.vsdc_hud_pill import VSDCHudPill
        html_out = VSDCHudPill._format_subtitle_html("ASHOK SEN • Ack: 123456789150726 • SGT (Shadow)")
        assert "#FFA657" in html_out and "SGT (Shadow)" in html_out and "123456789150726" in html_out


# ── The switch ───────────────────────────────────────────────────────────────────
class TestSwitch:
    def test_reading_the_setting(self):
        assert SGT_DEFAULT == "off"
        assert read_sgt_mode(lambda k, d=None: d) == "off"
        assert read_sgt_mode(lambda k, d=None: "Shadow") == "shadow"
        assert read_sgt_mode(lambda k, d=None: "live") == "off"        # not built yet

    @pytest.fixture
    def harness(self):
        from tests.test_vsdc247_integration import Harness
        with patch.dict(os.environ):
            for var in ("VSDC247_MODE", "VSDC247_ONLY", "VSDC_UIA_ONLY", "SGT_MODE"):
                os.environ.pop(var, None)
            yield Harness()

    def test_off_by_default_and_applied_live(self, harness):
        r = harness.router
        assert r._sgt_mode == SGT_OFF
        r.apply_engine_settings(True, True, False, sgt="shadow")
        assert r._sgt_mode == SGT_SHADOW and not r._sgt_alone and not r._engines_off
        r.apply_engine_settings(True, True, False)
        assert r._sgt_mode == SGT_OFF

    def test_sgt_alone_keeps_the_worker_ticking_but_routes_no_crosshair(self, harness):
        r = harness.router
        r.apply_engine_settings(False, False, False, sgt="shadow")
        assert r._sgt_alone and not r._engines_off
        r.apply_engine_settings(False, False, False)
        assert r._engines_off

    def test_env_override(self, harness):
        with patch.dict(os.environ, {"SGT_MODE": "shadow"}):
            harness.router.apply_engine_settings(True, True, False, sgt="off")
            assert harness.router._sgt_mode == SGT_SHADOW

    def test_shadow_observes_in_scope_ticks_and_never_changes_the_result(self, harness, tmp_path):
        r = harness.router
        r.apply_engine_settings(True, True, False, sgt="shadow")
        seen = []
        fake = MagicMock()
        fake.observe.side_effect = lambda *a, **k: seen.append((a, k))
        fake.pending.return_value = 0                   # no tracker rows waiting
        r._sgt = fake
        harness.screen(["Dashboard"])
        out = harness.tick()
        assert out is None and len(seen) == 1
        (hwnd, portal, url), kw = seen[0]
        assert (hwnd, portal) == (4242, "Income Tax") and "eportal.incometax.gov.in" in url
        assert kw["frame"] is not None

    def test_out_of_scope_pages_are_never_observed(self, harness):
        r = harness.router
        r.apply_engine_settings(True, True, False, sgt="shadow")
        r._sgt = MagicMock()
        r._sgt.pending.return_value = 0                 # else the tick returns early and proves nothing
        r.extract_browser_url.return_value = "https://www.youtube.com/watch?v=x"
        harness.tick()
        r._sgt.observe.assert_not_called()

    def test_switching_off_ends_open_sessions(self, harness):
        r = harness.router
        r.apply_engine_settings(True, True, False, sgt="shadow")
        r._sgt = MagicMock()
        r.apply_engine_settings(True, True, False, sgt="off")
        r._sgt.end_all.assert_called_once_with("SGT switched off")


# ── The submit ladder, on every portal ──────────────────────────────────────────
EVERIFY_URL = "https://eportal.incometax.gov.in/iec/foservices/#/dashboard/eVerifyReturn/eVerifyReturn-al"


def everify_picker(pan="ABCPD1234E", ack="123456789180925"):
    return ["Current Step 1 of 3 in stepper", "Select The Return To Be Verified", "1",
            "Unvisited Step 3 of 3 in stepper", "Return Successfully Verified", "3",
            "e-Verify / Discard Return", "Please select the return you would like to verify/discard",
            "Showing (1) returns", "Assessment Year", "2025-26", "ITR", "ITR 4", "Filing Type", "Original",
            "PAN :", pan, "Acknowledgement Number :", ack, "Filed On :", "Sep 18, 2025", "e verify", "Discard"]


class TestSubmitLadder:
    def test_the_everify_picker_is_captured_as_submitted_not_verified(self, tmp_path):
        """The page from the field report: the ack, form, year, filing type and date all captured."""
        r = Rig(tmp_path)
        r.see(PROFILE_URL, PROFILE_PAGE, shade=90)
        r.see(EVERIFY_URL, everify_picker(), shade=100)
        r.sgt.end_all("test")
        ds = r.events("session_end")[0]["payload"]["datasets"]
        assert len(ds) == 1
        d = ds[0]
        assert (d["form"], d["period"], d["arn"], d["filing_type"], d["filing_date"], d["status"]) == \
            ("ITR-4", "AY 2025-26", "123456789180925", "Original", "2025-09-18", "Submitted (Not Verified)")
        assert "pan" not in d                                     # whose it is was checked, not stored

    def test_a_card_of_another_client_is_not_attributed(self, tmp_path):
        r = Rig(tmp_path)
        r.see(PROFILE_URL, PROFILE_PAGE, shade=90)                               # client ABCPD1234E
        r.see(EVERIFY_URL, everify_picker(pan="XYZAB9876C"), shade=100)
        r.sgt.end_all("test")
        assert r.events("session_end")[0]["payload"]["datasets"] == []
        assert any(e.get("change", "").startswith("not attributed") for e in r.events("dataset"))

    def test_verifying_the_picked_return_promotes_the_same_dataset(self, tmp_path):
        r = Rig(tmp_path)
        r.see(PROFILE_URL, PROFILE_PAGE, shade=90)
        r.see(EVERIFY_URL, everify_picker(), shade=100)
        r.see(EVERIFY_URL, ["Visited Step 2 of 3 in stepper", "Select Method For Return Verification", "2",
                            "Current Step 3 of 3 in stepper", "Return Successfully Verified", "3",
                            "Return e-Verified successfully", "ITR-4 for A.Y. 2025-26"], shade=110)
        r.sgt.end_all("test")
        ds = r.events("session_end")[0]["payload"]["datasets"]
        assert len(ds) == 1 and ds[0]["arn"] == "123456789180925" and ds[0]["status"] == "Submitted & Verified"

    def test_the_otp_step_is_not_a_verification(self, tmp_path):
        r = Rig(tmp_path)
        r.see(EVERIFY_URL, everify_picker(), shade=100)
        r.see(EVERIFY_URL, ["Current Step 2 of 3 in stepper", "Select Method For Return Verification", "2",
                            "Unvisited Step 3 of 3 in stepper", "Return Successfully Verified", "3",
                            "Enter the OTP", "ITR-4 for A.Y. 2025-26"], shade=110)
        r.sgt.end_all("test")
        ds = r.events("session_end")[0]["payload"]["datasets"]
        assert [d["status"] for d in ds] == ["Submitted (Not Verified)"]

    def test_a_gst_arn_alone_proves_the_return_was_filed_and_verified(self, tmp_path):
        """GST issues the ARN only on filing with DSC/EVC - the portal's rule, from submit_rules."""
        r = Rig(tmp_path)
        r.see(GST_R1, ["GSTR-1 - Details of outward supplies", "FY -", "2026-27", "Tax Period -", "August", "x"],
              shade=100, portal=GST)
        r.see(GST_R1, ["Success", "Return submitted successfully. ARN: AA070826000001Z", "OK"], shade=110, portal=GST)
        r.sgt.end_all("test")
        ds = r.events("session_end")[0]["payload"]["datasets"]
        assert len(ds) == 1
        assert (ds[0]["form"], ds[0]["arn"], ds[0]["status"]) == ("GSTR-1", "AA070826000001Z", "Submitted & Verified")
        assert ds[0]["status_evidence"].startswith("ARN captured")

    def test_an_itr_ack_alone_proves_submission_but_not_verification(self, tmp_path):
        r = Rig(tmp_path)
        r.see(FILED_URL, ["View Filed Returns", "A.Y. 2025-26", "ITR :", "ITR-4",
                          "Acknowledgement No :", "123456789150925"], shade=100)
        r.sgt.end_all("test")
        ds = r.events("session_end")[0]["payload"]["datasets"]
        assert [d["status"] for d in ds] == ["Submitted (Not Verified)"]

    def test_a_truncated_name_is_logged_once(self, tmp_path):
        r = Rig(tmp_path)
        r.see(PROFILE_URL, PROFILE_PAGE, shade=90)                               # ASHOK KUMAR SEN
        for n in range(5):
            r.see(DASHBOARD, ["ASHOK KUMAR S... Individual", "x", f"y{n}"], shade=100 + n)
        assert len([e for e in r.events("profile") if e.get("change") == "not promoted"]) <= 1


class TestLadderClimbsWithDatapoints:
    """form + period -> Draft; + ARN -> Submitted (Not Verified); + message -> what it says."""

    def test_each_datapoint_raises_the_level_and_the_message_decides_the_last_step(self, tmp_path):
        r = Rig(tmp_path)
        r.see(WIZ_PI, ["Personal Information", "x", "y"], shade=100)                       # form + period
        levels = [e["values"]["status"] for e in r.events("dataset") if e["change"] == "new"]
        assert levels == ["Draft"]
        r.see(SUBMITTED.replace("fo-itr4-ay2026/fo-submit-success", "x/done"),
              ["Return submission", "Acknowledgement Number :", "123456789150726", "OK"], shade=110)   # + ack
        r.see(EVERIFY_URL, ["Return e-Verified successfully", "Acknowledgement Number :", "123456789150726",
                            "ITR-4 for A.Y. 2026-27"], shade=120)                                      # + message
        r.sgt.end_all("test")
        ups = [e["changes"]["status"] for e in r.events("dataset") if "status" in e.get("changes", {})]
        assert [u[1] for u in ups] == ["Submitted (Not Verified)", "Submitted & Verified"]
        ds = r.events("session_end")[0]["payload"]["datasets"]
        assert len(ds) == 1 and ds[0]["status_evidence"] == "Return e-Verified successfully"

    def test_a_pending_verification_message_keeps_it_not_verified(self, tmp_path):
        r = Rig(tmp_path)
        r.see(WIZ_PI, ["Personal Information", "x", "y"], shade=100)
        r.see(SUBMITTED.replace("fo-itr4-ay2026/fo-submit-success", "x/done"),
              ["You have successfully submitted your return!", "Acknowledgement Number :", "123456789150726",
               "You still need to e-Verify within 30 days"], shade=110)
        r.sgt.end_all("test")
        ds = r.events("session_end")[0]["payload"]["datasets"]
        assert [d["status"] for d in ds] == ["Submitted (Not Verified)"]

    def test_the_verification_page_joins_by_its_ack(self, tmp_path):
        """The e-Verify confirmation shows the ack of the return verified - an older filing."""
        r = Rig(tmp_path)
        r.see(PROFILE_URL, PROFILE_PAGE, shade=90)
        r.see(EVERIFY_URL, everify_picker(), shade=100)
        r.see(EVERIFY_URL, ["Return e-Verified successfully", "Acknowledgement Number :", "123456789180925",
                            "Transaction ID", "x"], shade=110)
        r.sgt.end_all("test")
        ds = r.events("session_end")[0]["payload"]["datasets"]
        assert len(ds) == 1 and (ds[0]["arn"], ds[0]["status"]) == ("123456789180925", "Submitted & Verified")
