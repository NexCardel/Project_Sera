"""
Dataset gating (docs/sgt-dataset-gating-plan.md): identity first (form + period), then evidence bound to it;
a value follows the page that supplied it; no link dependence; view-only returns are never datasets.
Identifiers are fictional.
"""
from test_sgt_shadow import (FILED_URL, GST, PROFILE_PAGE, PROFILE_URL, SUBMITTED, WIZ_PI, WIZ_STATUS, Rig,
                             filed_page)
import test_sgt_gst_fixes as G

SUCCESS = SUBMITTED.replace("fo-itr4-ay2026/fo-submit-success", "x/done")
VERIFY = "https://eportal.incometax.gov.in/iec/foservices/#/foreturns-ay26/x/verify"
ACK_A, ACK_B = "123456789150726", "123456789140726"


def ack_page(ack):
    return ["Return submission", "Acknowledgement Number :", ack, "OK"]


def itr_return(r):
    """A client and an ITR-4 AY 2026-27 being built (form from the link, year from the status page)."""
    r.see(PROFILE_URL, PROFILE_PAGE, shade=90)
    r.see(WIZ_STATUS, ["Assessment Year", "2026-27", "Filing Type", "x"], shade=100)
    r.see(WIZ_PI, ["Personal Information", "x", "y"], shade=110)


def slots(r):
    return r.sgt._sessions[1].slots


def values(r):
    return [dict(s.values) for s in slots(r)]


class TestAckBelongsToThePageThatShowedIt:
    def test_a_different_ack_on_the_same_page_view_corrects_the_first(self, tmp_path):
        r = Rig(tmp_path)
        itr_return(r)
        r.see(SUCCESS, ack_page(ACK_A), shade=120)
        r.see(SUCCESS, ack_page(ACK_B), shade=130, advance=20)
        assert [v["arn"] for v in values(r)] == [ACK_B]
        assert values(r)[0]["form"] == "ITR-4"

    def test_the_corrected_ack_is_sent_under_the_same_row(self, tmp_path):
        r = Rig(tmp_path)
        itr_return(r)
        r.see(SUCCESS, ack_page(ACK_A), shade=120)
        r.sgt.drain()
        r.see(SUCCESS, ack_page(ACK_B), shade=130, advance=20)
        sent = r.sgt.drain()
        assert len(sent) == 1 and sent[0]["arn"] == ACK_B and not sent[0].get("supersedes_dataset_key")

    def test_an_ack_seen_after_leaving_the_page_is_another_return_and_never_overwrites(self, tmp_path):
        r = Rig(tmp_path)
        itr_return(r)
        r.see(SUCCESS, ack_page(ACK_A), shade=120)
        r.see(WIZ_STATUS, ["Assessment Year", "2025-26", "Filing Type", "x"], shade=121)     # navigated away
        r.see(SUCCESS, ack_page(ACK_B), shade=130, advance=20)
        assert sorted(v["arn"] for v in values(r)) == sorted([ACK_A, ACK_B])


class TestIdentityFirst:
    def test_an_ack_with_no_form_and_period_waits_and_is_not_written(self, tmp_path):
        r = Rig(tmp_path)
        r.see(PROFILE_URL, PROFILE_PAGE, shade=90)
        r.see(SUCCESS, ack_page(ACK_A), shade=100)
        assert len(slots(r)) == 1 and slots(r)[0].held
        assert r.sgt.drain() == []

    def test_it_is_released_once_a_card_names_its_form_and_period(self, tmp_path):
        r = Rig(tmp_path)
        r.see(PROFILE_URL, PROFILE_PAGE, shade=90)
        r.see(SUCCESS, ack_page("123456789150925"), shade=100)
        assert r.sgt.drain() == []
        r.see(FILED_URL, filed_page(ack="123456789150925"), shade=110, advance=20)
        sent = r.sgt.drain()
        assert len(sent) == 1 and sent[0]["arn"] == "123456789150925" and sent[0]["filing_type"] == "ITR-4"

    def test_it_is_written_flagged_when_the_session_ends_without_an_identity(self, tmp_path):
        r = Rig(tmp_path)
        r.see(PROFILE_URL, PROFILE_PAGE, shade=90)
        r.see(SUCCESS, ack_page(ACK_A), shade=100)
        r.sgt.end_all("quit")
        sent = r.sgt.drain()
        assert len(sent) == 1 and sent[0]["arn"] == ACK_A
        assert [e for e in r.events("dataset") if "without form and period" in str(e.get("change"))]

    def test_a_bare_verification_message_is_ignored(self, tmp_path):
        r = Rig(tmp_path)
        itr_return(r)
        r.see(VERIFY, ["Return successfully e-verified", "x", "y"], shade=120)
        assert [v.get("status") for v in values(r)] == ["Draft"]
        assert [e for e in r.events("dataset") if e.get("change") == "message not tied - ignored"]

    def test_a_message_never_wipes_the_return_being_built(self, tmp_path):
        r = Rig(tmp_path)
        itr_return(r)
        r.see(VERIFY, ["Return successfully e-verified", "x", "y"], shade=120)
        assert r.sgt._sessions[1].draft.pieces

    def test_a_verification_message_ties_to_the_only_return_waiting_for_it(self, tmp_path):
        r = Rig(tmp_path)
        itr_return(r)
        r.see(SUCCESS, ack_page(ACK_A), shade=120)
        assert values(r)[0]["status"] == "Submitted (Not Verified)"
        r.see(VERIFY, ["Return successfully e-verified", "x", "y"], shade=130, advance=20)
        assert [v["status"] for v in values(r)] == ["Submitted & Verified"] and len(values(r)) == 1

    def test_with_two_returns_waiting_a_bare_message_is_not_guessed(self, tmp_path):
        r = Rig(tmp_path)
        itr_return(r)
        r.see(SUCCESS, ack_page(ACK_A), shade=120)
        r.see(WIZ_STATUS, ["Assessment Year", "2025-26", "Filing Type", "x"], shade=121)
        r.see(SUCCESS, ack_page(ACK_B), shade=125, advance=20)
        before = [v["status"] for v in values(r)]
        r.see(VERIFY, ["Return successfully e-verified", "x", "y"], shade=130, advance=20)
        assert [v["status"] for v in values(r)] == before


class TestStatusFollowsItsPage:
    def test_gst_status_follows_the_page_down_when_it_shows_another_status(self, tmp_path):
        r = Rig(tmp_path)
        G.client(r)
        r.see(G.R3B, G.r3b_page("June", "Filed"), shade=100, portal=GST)
        r.see(G.R3B, G.r3b_page("June", "Not Filed"), shade=110, portal=GST, advance=20)
        assert [v["status"] for v in values(r)] == ["Draft"]

    def test_a_half_drawn_page_does_not_lower_it(self, tmp_path):
        r = Rig(tmp_path)
        G.client(r)
        r.see(G.R3B, G.r3b_page("June", "Filed"), shade=100, portal=GST)
        for i in range(3):                                   # the status line is not on the page for a while
            r.see(G.R3B, ["Returns", "GSTR-3B - Monthly Return", "FY - 2026-27", "Return Period - June", "x"],
                  shade=110 + i, portal=GST, advance=8)
        assert [v["status"] for v in values(r)] == ["Submitted & Verified"]

    def test_another_page_cannot_lower_what_this_page_set(self, tmp_path):
        r = Rig(tmp_path)
        r.see(PROFILE_URL, PROFILE_PAGE, shade=90)
        r.see(FILED_URL, filed_page(status="e-Verified", ack="123456789150925"), shade=100)
        assert values(r)[0]["status"] == "Submitted & Verified"
        r.see(SUCCESS, ack_page("123456789150925"), shade=110, advance=20)       # says only: submitted
        assert values(r)[0]["status"] == "Submitted & Verified"


class TestNoLinkDependence:
    def test_a_gst_return_page_is_read_wherever_it_lives(self, tmp_path):
        r = Rig(tmp_path)
        G.client(r)
        r.see("https://return.gst.gov.in/returns/auth/some-new-layout", G.r3b_page("June", "Filed"), shade=100, portal=GST)
        assert [(v["form"], v["period"], v["status"]) for v in values(r)] == [
            ("GSTR-3B", "June (FY 2026-27)", "Submitted & Verified")]

    def test_the_dashboard_names_no_form_so_it_feeds_nothing(self, tmp_path):
        r = Rig(tmp_path)
        G.client(r)
        r.see(G.RET_DASH, ["File Returns", "Financial Year", "2026-27", "Period", "June", "Status -", "Filed", "SEARCH"],
              shade=100, portal=GST)
        assert values(r) == [] and r.events("current") == []


class TestViewOnlyReturnsAreNeverDatasets:
    def test_gstr2_preview_is_not_a_form(self, tmp_path):
        r = Rig(tmp_path)
        G.client(r)
        r.see("https://return.gst.gov.in/returns/auth/gstr2/preview",
              ["Returns", "FY - 2026-27", "Tax Period - June", "Status - Not Filed", "x"], shade=100, portal=GST)
        assert values(r) == [] and r.sgt.drain() == []

    def test_gstr2a_and_2b_headings_are_not_forms(self, tmp_path):
        r = Rig(tmp_path)
        G.client(r)
        r.see("https://return.gst.gov.in/returns/auth/gstr2b",
              ["GSTR-2B - Auto-drafted ITC statement", "FY - 2026-27", "Return Period - June", "x"],
              shade=100, portal=GST)
        r.see("https://return.gst.gov.in/returns/auth/other",
              ["GSTR-2A - Auto drafted details", "FY - 2026-27", "Return Period - June", "x"], shade=110, portal=GST)
        assert values(r) == []
