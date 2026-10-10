"""
GST fixes of 2026-10-06 (docs/sgt-gst-fixes-plan.md). The sequences are the ones in the SGT log:
a finished June GSTR-3B row must never be rewritten to another period by a dashboard dropdown, a
status read for one return must never land on another, OCR is never used on the GST portal, and a
quarter is recorded by its end month.
"""
from unittest.mock import MagicMock

from core.sgt.sgt_shadow import MISSING_CLEAR_SEC

from test_sgt_shadow import GST, ITR, PROFILE_PAGE, PROFILE_URL, WIZ_PI, WIZ_STATUS, Rig, frame

GSTIN = "29ABACD1191FAZK"
WELCOME = "https://services.gst.gov.in/services/auth/fowelcome"
SVC_DASH = "https://services.gst.gov.in/services/auth/dashboard"
RET_DASH = "https://return.gst.gov.in/returns/auth/dashboard"
R3B = "https://return.gst.gov.in/returns/auth/gstr3b"
R1 = "https://return.gst.gov.in/returns/auth/gstr1"
COMPARISON = "https://return.gst.gov.in/returns/auth/comparison"

WELCOME_PAGE = ["Welcome TEST CLIENT to GST Common Portal", GSTIN, "View Profile", "x"]
SVC_DASH_PAGE = ["Dashboard", GSTIN, "Services", "x"]


def r3b_page(period="June", status="Filed", fy="2026-27"):
    return ["Returns", "GSTR-3B - Monthly Return", f"Status - {status}", f"FY - {fy}", f"Return Period - {period}", "x"]


def r1_page(period="September(Q)", status="Not Filed", fy="2026-27"):
    return ["Returns", fy, "Tax Period -", period, "Status -", status, "x"]


def client(r):
    """A confirmed client: the GSTIN seen on two pages."""
    r.see(WELCOME, WELCOME_PAGE, shade=90, portal=GST)
    r.see(SVC_DASH, SVC_DASH_PAGE, shade=95, portal=GST)


def sent_keys(sent):
    """The tracker keys of what was handed out, upper-cased and without dashes."""
    return [x["dataset_key"].replace("-", "").upper() for x in sent]


class TestFinishedReturnsAreNeverRewritten:
    def test_dashboard_dropdown_after_a_filed_return_adds_nothing(self, tmp_path):
        """2026-10-06, as the SGT log shows it: the dashboard supplied June and 'Filed', the GSTR-3B
        link completed the return, the dashboard redrew (period gone), then its dropdown went to October."""
        r = Rig(tmp_path)
        client(r)
        dash = ["File Returns", "Financial Year", "2026-27", "Period", "{p}", "Status -", "Filed", "SEARCH"]
        r.see(RET_DASH, [x.replace("{p}", "June") for x in dash], shade=100, portal=GST)
        r.see(R3B, ["Returns", "x", "y"], shade=105, portal=GST, advance=2)         # link only, page text not read
        for i in range(3):                                                           # the dashboard redraws
            r.see(RET_DASH, ["File Returns", "Financial Year", "SEARCH"], shade=110 + i, portal=GST,
                  advance=MISSING_CLEAR_SEC + 2)
        r.see(RET_DASH, [x.replace("{p}", "October") for x in dash], shade=140, portal=GST, advance=2)
        sent = r.sgt.drain()
        assert not [e for e in r.events("dataset") if e.get("change") == "moved"]
        assert all(not x.get("supersedes_dataset_key") for x in sent)
        assert "OCTOBER" not in " ".join(sent_keys(sent)) and "JUNE" not in " ".join(sent_keys(sent))

    def test_a_filed_return_read_on_its_page_survives_the_dashboard_dropdown(self, tmp_path):
        r = Rig(tmp_path)
        client(r)
        r.see(R3B, r3b_page("June", "Filed"), shade=100, portal=GST)
        for i in range(3):
            r.see(RET_DASH, ["File Returns", "Financial Year", "2026-27", "Period", "October", "Status -", "Filed",
                             "SEARCH"], shade=110 + i, portal=GST, advance=MISSING_CLEAR_SEC + 2)
        sent = r.sgt.drain()
        ks = [k for k in sent_keys(sent) if "GSTR3B" in k]
        assert len(set(ks)) == 1 and "JUNE" in ks[0], ks
        assert all(not x.get("supersedes_dataset_key") for x in sent)
        assert not [e for e in r.events("dataset") if e.get("change") == "moved"]

    def test_a_new_period_on_its_own_return_page_is_a_new_row(self, tmp_path):
        r = Rig(tmp_path)
        client(r)
        r.see(R3B, r3b_page("June", "Filed"), shade=100, portal=GST)
        r.see(R3B, r3b_page("September", "Not Filed"), shade=110, portal=GST, advance=30)
        sent = r.sgt.drain()
        ks = sent_keys(sent)
        assert any("JUNE" in k for k in ks) and any("SEPTEMBER" in k for k in ks), ks
        assert all(not x.get("supersedes_dataset_key") for x in sent)
        assert not [e for e in r.events("dataset") if e.get("change") == "moved"]
        june = [x for x in sent if "JUNE" in x["dataset_key"].upper()][-1]
        assert june["status"] == "Submitted & Verified"

    def test_a_form_change_carries_nothing_over(self, tmp_path):
        """Filed GSTR-3B for June, then the GSTR-1 link opens: its year/period/status are its own."""
        r = Rig(tmp_path)
        client(r)
        r.see(R3B, r3b_page("June", "Filed"), shade=100, portal=GST)
        r.sgt.drain()
        r.see(R1, ["Returns", "x", "y"], shade=110, portal=GST, advance=30)         # link only: page not drawn yet
        assert r.sgt.drain() == []                                               # no GSTR-1 June / Filed row
        r.see(R1, r1_page("September(Q)", "Not Filed"), shade=120, portal=GST, advance=10)
        sent = r.sgt.drain()
        assert sent and all("GSTR1" in k and "SEPTEMBER" in k for k in sent_keys(sent)), sent_keys(sent)

    def test_a_page_that_names_no_form_says_nothing(self, tmp_path):
        r = Rig(tmp_path)
        client(r)
        r.see(R3B, r3b_page("June", "Filed"), shade=100, portal=GST)
        r.sgt.drain()
        r.see(COMPARISON, ["FY - 2025-26", "Tax Period -", "March(Q)", "Status - Filed", "x"],
              shade=110, portal=GST, advance=30)
        assert r.sgt.drain() == []
        r.see(R3B, r3b_page("June", "Filed"), shade=120, portal=GST, advance=30)
        assert not [e for e in r.events("dataset") if e.get("change") == "moved"]

    def test_a_blip_clearing_the_period_does_not_defeat_the_period_check(self, tmp_path):
        r = Rig(tmp_path)
        client(r)
        r.see(R3B, r3b_page("June", "Filed"), shade=100, portal=GST)
        r.sgt.drain()
        gap = MISSING_CLEAR_SEC + 2
        no_period = ["Returns", "GSTR-3B - Monthly Return", "Status - Filed", "FY - 2026-27", "x"]
        for i in range(3):                                   # the period piece goes missing for a while
            r.see(R3B, no_period, shade=101 + i, portal=GST, advance=gap)
        assert any(e.get("change") == "cleared" and e["field"] == "tax_period" for e in r.events("current"))
        r.see(R3B, r3b_page("September", "Not Filed"), shade=140, portal=GST, advance=gap)
        sent = r.sgt.drain()
        assert all(not x.get("supersedes_dataset_key") for x in sent)
        assert not [e for e in r.events("dataset") if e.get("change") == "moved"]
        assert any("SEPTEMBER" in k for k in sent_keys(sent))


class TestNoOcrOnGst:
    def test_a_blind_gst_page_is_never_sent_to_ocr(self, tmp_path):
        r = Rig(tmp_path)
        ocr = MagicMock()
        ocr.scan_image.return_value = {"lines": ["Period •", "September", "x", "y"]}
        client(r)
        r.see(R3B, r3b_page("June", "Filed"), shade=100, portal=GST)
        r.page = ["Enable accessibility"]
        r.clock[0] += 30
        res = r.sgt.observe(1, GST, RET_DASH, frame=frame(150), ocr=ocr)
        assert res is None and ocr.scan_image.call_count == 0
        r.sgt.end_all("t")
        assert r.events("session_end")[-1]["stats"]["ocr_skipped"] >= 1

    def test_itr_still_uses_ocr(self, tmp_path):
        r = Rig(tmp_path)
        ocr = MagicMock()
        ocr.scan_image.return_value = {"lines": ["a", "b", "c", "d"]}
        r.page = ["Enable accessibility"]
        r.clock[0] += 1
        r.sgt.observe(1, ITR, "https://eportal.incometax.gov.in/iec/foservices/#/x",
                      frame=frame(100), ocr=ocr)
        assert ocr.scan_image.call_count == 1


class TestQuarters:
    def _period_events(self, r):
        return [(e["value"], e.get("evidence")) for e in r.events("current")
                if e.get("field") == "tax_period" and e.get("change") in ("set", "changed")]

    def test_gstr3b_quarter_range_is_recorded_by_its_end_month(self, tmp_path):
        r = Rig(tmp_path)
        r.see(R3B, ["Returns", "GSTR-3BQ - Quarterly Return", "Status - Filed", "FY - 2026-27",
                    "Return Period - Apr-Jun", "x"], portal=GST)
        assert self._period_events(r) == [("June", "Return Period - Apr-Jun")]

    def test_gstr1_quarter_marker_is_recorded_by_its_end_month(self, tmp_path):
        r = Rig(tmp_path)
        r.see(R1, r1_page("September(Q)", "Not Filed"), portal=GST)
        assert [v for v, _ in self._period_events(r)] == ["September"]

    def test_every_quarter_maps_to_its_last_month(self, tmp_path):
        for text, month in (("Jan-Mar", "March"), ("Apr-Jun", "June"), ("Jul-Sep", "September"), ("Oct-Dec", "December")):
            r = Rig(tmp_path / text)
            r.see(R3B, ["Returns", "GSTR-3BQ - Quarterly Return", "FY - 2026-27", f"Return Period - {text}", "x"],
                  portal=GST)
            assert [v for v, _ in self._period_events(r)] == [month], text

    def test_the_bare_year_line_above_the_gstr1_period_is_the_year(self, tmp_path):
        r = Rig(tmp_path)
        client(r)
        r.see(R1, r1_page("September(Q)", "Not Filed"), shade=100, portal=GST)
        sent = r.sgt.drain()
        assert any("GSTR1" in k and "SEPTEMBER" in k for k in sent_keys(sent))


class TestItrWizardUnchanged:
    ALT = WIZ_PI.replace("fo-itr4-ay2026", "fo-itr1-ay2026")

    def _wizard(self, r):
        r.see(PROFILE_URL, PROFILE_PAGE, shade=90)
        r.see(WIZ_STATUS, ["Assessment Year", "2026-27", "Filing Type", "x"], shade=100)
        r.see(WIZ_PI, ["Personal Information", "x", "y"], shade=110)

    def test_a_draft_return_is_still_moved_when_its_form_is_changed(self, tmp_path):
        r = Rig(tmp_path)
        self._wizard(r)
        r.see(self.ALT, ["Personal Information", "x", "y"], shade=120)
        moved = [e for e in r.events("dataset") if e.get("change") == "moved"]
        assert len(moved) == 1 and moved[0]["values"]["form"] == "ITR-1"

    def test_a_submitted_return_is_never_moved(self, tmp_path):
        r = Rig(tmp_path)
        self._wizard(r)
        r.sgt._sessions[1].slots[0].values["status"] = "Submitted (Not Verified)"
        r.see(self.ALT, ["Personal Information", "x", "y"], shade=120)
        assert not [e for e in r.events("dataset") if e.get("change") == "moved"]
        assert len(r.sgt._sessions[1].slots) == 2
