"""
Session boundaries: the ONLY things that end an SGT session are a login page, a logout page, 2 hours
idle, a closed window and shutdown. Another tab - another portal, another client of the same portal,
a mail tab - sets the session aside and brings it back; it never ends it. Identifiers are fictional.
"""
import json
import random

from core.sgt.sgt_shadow import IDLE_END_SEC, IDLE_REASON, SgtShadow
from core.vsdc import vsdc_router
from test_sgt_shadow import (FILED_URL, GST, ITR, LOGIN_URL, LOGOUT_URL, PROFILE_PAGE, PROFILE_URL, Rig,
                             filed_page, frame)

EMAIL = "Email"
MAIL_URL = "https://mail.google.com/mail/u/0/#inbox"
MAIL_PAGE = ["Inbox", "Compose", "Primary", "Social"]
GST_WELCOME = "https://services.gst.gov.in/services/auth/fowelcome"
GST_LOGIN = "https://services.gst.gov.in/services/login"
GST_LOGOUT = "https://services.gst.gov.in/services/logout"
GST_A = ["Welcome ASHOK KUMAR SEN to GST Common Portal", " ASHOK KUMAR SEN 19ABCPD1234E1ZB", "View Profile"]
GST_B = ["Welcome MEERA DAS to GST Common Portal", " MEERA DAS 27AAACZ9876K1ZE", "View Profile"]


def ends(r):
    return r.events("session_end")


def reasons(r):
    return [e["reason"] for e in ends(r)]


_SHADE = [0]


def _shade():
    """A different screen every time, so the page is read again (a still page is read once)."""
    _SHADE[0] = (_SHADE[0] + 7) % 150
    return 50 + _SHADE[0]


def gst_client(r, page=GST_A, hwnd=1, advance=0.35):
    r.see(GST_WELCOME, page, portal=GST, shade=_shade(), hwnd=hwnd, advance=advance)


def itr_client(r, hwnd=1, advance=0.35):
    r.see(PROFILE_URL, PROFILE_PAGE, shade=90, hwnd=hwnd, advance=advance)


class TestATabSwitchIsNotABoundary:
    def test_a_mail_tab_in_between_keeps_the_session(self, tmp_path):
        r = Rig(tmp_path)
        itr_client(r)
        sid = r.sgt._sessions[1].session_id
        r.see(MAIL_URL, MAIL_PAGE, portal=EMAIL, shade=120)
        r.see(MAIL_URL, MAIL_PAGE + ["x"], portal=EMAIL, shade=121)
        r.see(FILED_URL, filed_page(), shade=100)
        assert ends(r) == []
        s = r.sgt._sessions[1]
        assert s.session_id == sid and s.portal == ITR
        assert s.profile["pan"]["value"] == "ABCPD1234E"

    def test_itr_and_gst_tabs_each_keep_their_own_session(self, tmp_path):
        r = Rig(tmp_path)
        itr_client(r)
        itr_id = r.sgt._sessions[1].session_id
        gst_client(r)
        gst_id = r.sgt._sessions[1].session_id
        assert itr_id != gst_id
        for _ in range(3):                                  # tab, tab, tab ...
            r.see(FILED_URL, filed_page(), shade=100)
            assert r.sgt._sessions[1].session_id == itr_id
            r.see(GST_WELCOME, GST_A, portal=GST, shade=96)
            assert r.sgt._sessions[1].session_id == gst_id
        assert ends(r) == []
        assert r.sgt._sessions[1].profile["gstin"]["value"] == "19ABCPD1234E1ZB"

    def test_data_read_before_and_after_the_switch_lands_in_one_dataset(self, tmp_path):
        r = Rig(tmp_path)
        itr_client(r)
        r.see(FILED_URL, filed_page(), shade=100)
        r.see(MAIL_URL, MAIL_PAGE, portal=EMAIL, shade=120)
        gst_client(r)
        r.see(FILED_URL, filed_page(status="Submitted & e-Verified"), shade=101)
        r.sgt.end_all("quit")
        itr_end = [e for e in ends(r) if e["payload"]["portal"] == ITR][0]
        assert itr_end["payload"]["client_profile"]["pan"] == "ABCPD1234E"
        assert len(itr_end["payload"]["datasets"]) == 1

    def test_the_hud_never_says_logged_on_a_tab_switch(self, tmp_path):
        notes = []
        r = Rig(tmp_path)
        r.sgt._notify = lambda *a, **k: notes.append(a)
        itr_client(r)
        gst_client(r)
        r.see(PROFILE_URL, PROFILE_PAGE, shade=90)
        assert not any("logged" in " ".join(map(str, a)).lower() for a in notes)


class TestOnlyTheListedBoundariesEnd:
    def test_logout_ends_only_its_own_portals_session(self, tmp_path):
        r = Rig(tmp_path)
        itr_client(r)
        gst_client(r)
        r.see(GST_LOGOUT, ["Logged out"], portal=GST, shade=99)
        assert reasons(r) == ["logout"] and ends(r)[0]["payload"]["portal"] == GST
        r.see(FILED_URL, filed_page(), shade=100)           # back on the ITR tab: its session is alive
        assert r.sgt._sessions[1].portal == ITR
        assert r.sgt._sessions[1].profile["pan"]["value"] == "ABCPD1234E"
        assert reasons(r) == ["logout"]

    def test_another_portals_login_page_does_not_end_this_portals_session(self, tmp_path):
        r = Rig(tmp_path)
        gst_client(r)
        r.see(LOGIN_URL, ["Login to e-Filing", "Enter User ID", "Continue"], shade=100)   # ITR login tab
        assert ends(r) == []
        r.see(GST_WELCOME, GST_A, portal=GST, shade=96)     # the GST tab again: same client, same session
        assert ends(r) == []
        assert r.sgt._sessions[1].profile["gstin"]["value"] == "19ABCPD1234E1ZB"

    def test_the_same_portals_login_page_ends_the_session(self, tmp_path):
        r = Rig(tmp_path)
        itr_client(r)
        r.see(LOGIN_URL, ["Login to e-Filing", "Enter User ID"], shade=100)
        assert reasons(r) == ["login page - next client"]

    def test_a_logout_seen_after_a_mail_tab_still_ends_it(self, tmp_path):
        r = Rig(tmp_path)
        itr_client(r)
        r.see(MAIL_URL, MAIL_PAGE, portal=EMAIL, shade=120)
        r.see(LOGOUT_URL, ["Logged out"], shade=100)
        assert reasons(r) == ["logout"]

    def test_idle_limit_is_two_hours(self, tmp_path):
        assert IDLE_END_SEC == 2 * 3600 and IDLE_REASON == "idle 2 hr"
        assert vsdc_router._SESSION_IDLE_TIMEOUT_SEC == IDLE_END_SEC
        r = Rig(tmp_path)
        itr_client(r)
        r.see(PROFILE_URL, PROFILE_PAGE, advance=IDLE_END_SEC - 30)
        assert ends(r) == []                                # 2 hours less 30 s: still the same session
        r.see(PROFILE_URL, PROFILE_PAGE, advance=IDLE_END_SEC + 1)
        assert reasons(r) == [IDLE_REASON]

    def test_a_session_set_aside_also_idles_out_after_two_hours(self, tmp_path):
        r = Rig(tmp_path)
        itr_client(r)
        gst_client(r)                                       # ITR is set aside
        r.see(GST_WELCOME, GST_A, portal=GST, shade=96, advance=IDLE_END_SEC - 60)
        assert ends(r) == []
        r.see(GST_WELCOME, GST_A, portal=GST, shade=97, advance=120)
        assert [(e["reason"], e["payload"]["portal"]) for e in ends(r)] == [(IDLE_REASON, ITR)]
        assert r.sgt._sessions[1].portal == GST

    def test_closing_the_window_ends_every_session_of_it(self, tmp_path):
        r = Rig(tmp_path)
        itr_client(r)
        gst_client(r)
        r.see(MAIL_URL, MAIL_PAGE, portal=EMAIL, shade=120)
        r.sgt.end_session(1, "window closed")
        assert sorted(reasons(r)) == ["window closed"] * 2
        assert 1 not in r.sgt._sessions and 1 not in r.sgt._shelf

    def test_closing_one_window_leaves_the_other_window_alone(self, tmp_path):
        r = Rig(tmp_path)
        itr_client(r, hwnd=1)
        gst_client(r, hwnd=1)
        itr_client(r, hwnd=2)
        r.sgt.end_session(1, "window closed")
        assert 2 in r.sgt._sessions and len(ends(r)) == 2

    def test_shutdown_writes_the_rows_of_sessions_set_aside(self, tmp_path):
        r = Rig(tmp_path)
        itr_client(r)
        r.see(FILED_URL, filed_page(), shade=100)           # a complete ITR dataset, its ack
        gst_client(r)                                       # the ITR session is set aside
        r.sgt.end_all("Application Shutdown")
        assert sorted(reasons(r)) == ["Application Shutdown"] * 2
        rows = r.sgt.drain()
        assert [x["arn"] for x in rows] == ["123456789150925"] and rows[0]["pan"] == "ABCPD1234E"
        assert r.sgt._shelf == {} and r.sgt._sessions == {}

    def test_a_crash_keeps_sessions_set_aside_in_the_snapshot(self, tmp_path):
        state = tmp_path / "state.json"
        r = Rig(tmp_path)
        r.sgt._state_path = state
        itr_client(r)
        gst_client(r)
        r.sgt._save_state(force=True)
        saved = json.loads(state.read_text(encoding="utf-8"))["sessions"]
        assert sorted(x["portal"] for x in saved) == [GST, ITR]


class TestTwoClientsInOneWindow:
    def test_two_gst_clients_in_two_tabs_never_end_or_mix(self, tmp_path):
        r = Rig(tmp_path)
        gst_client(r, GST_A)
        gst_client(r, GST_A)                                # seen twice: confirmed
        a = r.sgt._sessions[1]
        gst_client(r, GST_B)                                # the window is now on another client
        gst_client(r, GST_B)                                # seen twice: confirmed
        b = r.sgt._sessions[1]
        assert b is not a and ends(r) == []
        assert a.profile["gstin"]["value"] == "19ABCPD1234E1ZB"
        gst_client(r, GST_A)                                # back to the first tab
        assert r.sgt._sessions[1] is a and ends(r) == []
        gst_client(r, GST_B)
        assert r.sgt._sessions[1] is b and ends(r) == []
        assert (b.profile.get("gstin") or {}).get("value") == "27AAACZ9876K1ZE"
        assert a.profile["gstin"]["value"] == "19ABCPD1234E1ZB"

    def test_a_lone_gstin_never_changes_the_session(self, tmp_path):
        # The client comes from the portal's top header line only ("NAME GSTIN"): a GSTIN alone on a line
        # (a table cell, a label's value) is read by nothing, so it cannot start another session.
        r = Rig(tmp_path)
        gst_client(r, GST_A)
        gst_client(r, GST_A)
        a = r.sgt._sessions[1]
        r.see(GST_WELCOME, ["x", "27AAACZ9876K1ZE", "y"], portal=GST, shade=_shade())
        assert r.sgt._sessions[1] is a and ends(r) == [] and r.sgt._shelf == {}

    def test_a_table_row_that_looks_like_the_header_sets_the_session_aside_not_ends_it(self, tmp_path):
        # Documented risk of the header rule: a table row printing "NAME GSTIN" on one line reads as a
        # client. It must never END the real client's session, and the real header brings it back.
        r = Rig(tmp_path)
        gst_client(r, GST_A)
        gst_client(r, GST_A)
        a = r.sgt._sessions[1]
        r.see(GST_WELCOME, ["x", " SOME TRADERS 27AAACZ9876K1ZE", "y"], portal=GST, shade=_shade())
        assert ends(r) == [] and a in r.sgt._shelf[1]
        gst_client(r, GST_A)
        assert r.sgt._sessions[1] is a and ends(r) == []


class TestTheRealPortalLayout:
    """The GST portal hands UI Automation its text in separate nodes: a field's label and its value
    are two lines, and the success banner is cut into pieces with the ARN on a line of its own
    (live GST test, 2026-10-06: the ARN was never picked up). Fictional values, real shape."""

    URL = "https://return.gst.gov.in/returns/auth/file"
    PAGE = ["Dashboard", "Returns", "File", "English",
            "GSTR1", "of GSTIN -", "19ABCPD1234E1ZB", "for the Return Period - '", "September", "-", "2026-27",
            "has been successfully filed. The Acknowledgment Reference Number is", "AA190926191296I", ". The",
            "GSTR1", "can be viewed on your Dashboard Login=>Taxpayer Dashboard=>Returns. This message is sent "
            "to your registered Email ID and Mobile Number.",
            "Indicates Mandatory Fields",
            " ASHOK KUMAR SEN 19ABCPD1234E1ZB", "GSTIN -", "19ABCPD1234E1ZB", "Legal Name -", "ASHOK KUMAR SEN",
            "Trade Name -", "A K SEN STORES",
            "Return Type -", "GSTR1", "FY -", "2026-27", "Return Period -", "September(Q)", "Status -", "Filed",
            "Returns Filing for GST", "GSTR1"]

    def test_the_arn_on_its_own_line_is_picked_up(self, tmp_path):
        r = Rig(tmp_path)
        r.see(self.URL, self.PAGE, portal=GST, shade=_shade())
        s = r.sgt._sessions[1]
        assert s.profile["gstin"]["value"] == "19ABCPD1234E1ZB"
        assert s.profile["name"]["value"] == "ASHOK KUMAR SEN"
        r.sgt.end_all("Application Shutdown")
        rows = r.sgt.drain()
        assert [x["arn"] for x in rows] == ["AA190926191296I"]
        assert rows[0]["status"] == "Submitted & Verified"

    def test_the_arn_on_the_same_line_still_works(self, tmp_path):
        r = Rig(tmp_path)
        page = [("The Acknowledgment Reference Number is AA190926191296I ." if "Reference Number is" in ln
                 else ln) for ln in self.PAGE if ln not in ("AA190926191296I", ". The")]
        page = [ln.replace("has been successfully filed. ", "") for ln in page]
        page.insert(5, "has been successfully filed.")
        r.see(self.URL, page, portal=GST, shade=_shade())
        r.sgt.end_all("Application Shutdown")
        assert [x["arn"] for x in r.sgt.drain()] == ["AA190926191296I"]

    def test_the_banner_without_an_arn_invents_none(self, tmp_path):
        r = Rig(tmp_path)
        page = [ln for ln in self.PAGE if ln not in ("AA190926191296I",)]
        r.see(self.URL, page, portal=GST, shade=_shade())
        r.sgt.end_all("Application Shutdown")
        assert all(x.get("arn") in (None, "", "N/A") for x in r.sgt.drain())      # no ARN is invented

    def test_a_supplier_in_the_same_layout_is_not_the_client(self, tmp_path):
        r = Rig(tmp_path)
        gst_client(r, GST_A)
        gst_client(r, GST_A)
        a = r.sgt._sessions[1]
        for page in (["GSTR-1 - Details", "GSTIN of Supplier", "27AAACZ9876K1ZE", "Invoice No"],
                     ["GSTR-1 - Details", "GSTIN", "27AAACZ9876K1ZE", "Trade Name", "SOME TRADERS"],
                     ["GSTR-1 - Details", "Legal Name", "SOME SUPPLIER TRADERS", "GSTIN", "27AAACZ9876K1ZE"]):
            r.see("https://return.gst.gov.in/returns/auth/gstr1", page, portal=GST, shade=_shade())
            assert r.sgt._sessions[1] is a
        assert ends(r) == [] and r.sgt._shelf == {}


class TestStress:
    """A long random run of tab switches, logins, logouts, idle gaps and window closes, checked for what
    must always hold - no exception, no lost or duplicated session, no cross-client mixing."""

    PAGES = {
        ("itr", "A"): (ITR, PROFILE_URL, PROFILE_PAGE),
        ("itr", "B"): (ITR, PROFILE_URL, ["MEERA DAS Individual", "Profile", "Name", "MEERA DAS", "PAN",
                                          "XYZAB9876C"]),
        ("gst", "A"): (GST, GST_WELCOME, GST_A),
        ("gst", "B"): (GST, GST_WELCOME, GST_B),
        ("mail", "-"): (EMAIL, MAIL_URL, MAIL_PAGE),
        ("gst", "supplier1"): (GST, "https://return.gst.gov.in/returns/auth/gstr1",
                               ["GSTR-1 - Details", "GSTIN of Supplier 27AAACZ9876K1ZE", "Invoice No"]),
        ("gst", "supplier2"): (GST, "https://return.gst.gov.in/returns/auth/gstr1",
                               ["GSTR-1 - Details", "GSTIN", "19ABCPD1234E1ZB", "Legal Name", "SOME TRADERS"]),
    }

    def run(self, tmp_path, seed, steps):
        rng = random.Random(seed)
        r = Rig(tmp_path)
        started = 0
        for i in range(steps):
            hwnd = rng.choice((1, 1, 1, 2, 3))
            roll = rng.random()
            if roll < 0.70:
                portal, url, page = self.PAGES[rng.choice(list(self.PAGES))]
                r.see(url, page, portal=portal, shade=rng.randint(60, 200), hwnd=hwnd,
                      advance=rng.choice((0.35, 1, 5, 30)))
            elif roll < 0.78:
                r.see(rng.choice((LOGOUT_URL, GST_LOGOUT)), ["Logged out"], hwnd=hwnd,
                      portal=rng.choice((ITR, GST)))
            elif roll < 0.86:
                r.see(rng.choice((LOGIN_URL, GST_LOGIN)), ["Login", "User ID", "Password"], hwnd=hwnd,
                      portal=rng.choice((ITR, GST)))
            elif roll < 0.93:
                r.clock[0] += rng.choice((600, IDLE_END_SEC - 5, IDLE_END_SEC + 5, 3 * IDLE_END_SEC))
            elif roll < 0.97:
                r.sgt.end_session(hwnd, "window closed")
            else:
                portal, url, page = self.PAGES[("itr", "A")]
                r.see(FILED_URL, filed_page(ack=f"1234567891{rng.randint(10000, 99999)}"), portal=ITR, hwnd=hwnd)
            self.invariants(r)
        r.sgt.end_all("Application Shutdown")
        assert r.sgt._sessions == {} and r.sgt._shelf == {}
        return r

    @staticmethod
    def invariants(r):
        seen = {}
        for hwnd, s in r.sgt._sessions.items():
            seen[id(s)] = seen.get(id(s), 0) + 1
        for hwnd, lst in r.sgt._shelf.items():
            assert lst, "an empty shelf entry was left behind"
            for s in lst:
                assert s.has_content, "an empty session was set aside"
                seen[id(s)] = seen.get(id(s), 0) + 1
                assert s is not r.sgt._sessions.get(hwnd), "a session is both in view and set aside"
        assert all(n == 1 for n in seen.values()), "a session is held twice"
        for s in r.sgt._all_sessions():
            pan = (s.profile.get("pan") or {}).get("value")
            gstin = (s.profile.get("gstin") or {}).get("value")
            if pan and gstin and len(gstin) == 15:
                assert gstin[2:12] == pan, "two clients mixed in one session"

    def test_random_tab_switching_never_breaks_a_session(self, tmp_path):
        for seed in range(12):
            sub = tmp_path / f"s{seed}"
            sub.mkdir()
            r = self.run(sub, seed, 400)
            allowed = {"logout", "login page - next client", IDLE_REASON, "window closed",
                       "Application Shutdown"}
            for e in ends(r):
                assert e["reason"] in allowed, e["reason"]

    def test_session_ids_are_never_ended_twice(self, tmp_path):
        r = self.run(tmp_path, 99, 600)
        ids = [e["session"] for e in ends(r)]
        assert len(ids) == len(set(ids))
