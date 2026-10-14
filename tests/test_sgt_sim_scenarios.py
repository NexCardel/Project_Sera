"""Scenario tests of the never-miss blueprint (sections 8 and 11) in the simulation: one test per scenario id the
harness can express. Fictional data only. A test marked xfail(strict) names the work package that closes its
gap; when that package lands the test turns green and strict makes the marker come off."""
import pytest

from tests.sgt_sim.fake_browser import OUTSIDE, PORTAL_GST, PORTAL_ITR, Browser, Page, Tab, Toast
from tests.sgt_sim.harness import assert_profile, problems, sim  # noqa: F401 - sim is a fixture
from tests.sgt_sim.pages import (GST_FILE, GST_RET, ITR, ITR_LOGIN, ITR_LOGOUT, ITR_PROFILE, ITR_SUCCESS, LOGGED_OUT,
                                 arn_toast, gst_filing, gst_header, itr_ack, itr_filing, make_client, make_clients)
from tests.sgt_sim.profiles import SAVED, _away, _start, parallel_filing

LATER = "closed by a later work package"


def go(sim, b, expected, until, *, known=(), allow_missed=False, **kw):
    h = sim(b, **kw)
    h.run(until)
    h.end_day()
    assert_profile(h, expected, known=known, allow_missed=allow_missed)
    return h


# ---- the review corrections ----------------------------------------------------------------------------------

def _profile_lines(client):
    return [f"{client.name} Individual", "Profile", "Name", client.name, "PAN", client.pan,
            "Date of Birth", "12-Mar-1980"]


def test_r1_logout_then_next_client_in_the_same_tab_never_mixes(sim):
    """A read of client A's profile page is still running when A logs out and client B logs in and lands on the
    very same address: the read returns B's page under A's address. It must be dropped, never written to A."""
    b = Browser()
    w, tab = _start(b)
    a, c = make_clients(2, first=40)
    t, sub_a = itr_filing(tab, 2.0, a, 1, logout=False)
    tab.navigate(t, Page(ITR_PROFILE, _profile_lines(a), PORTAL_ITR, read_ms=4000.0))
    tab.navigate(t + 0.8, Page(ITR_LOGOUT, LOGGED_OUT, PORTAL_ITR))
    tab.navigate(t + 1.2, Page(ITR_LOGIN, ["Login", "Enter your User ID", "Continue"], PORTAL_ITR))
    tab.navigate(t + 1.8, Page(ITR_PROFILE, _profile_lines(c), PORTAL_ITR, read_ms=4000.0))
    t, sub_b = itr_filing(tab, t + 9.0, c, 2, login=False)
    h = go(sim, b, [sub_a, sub_b], t + 20.0)
    for _t, r in h.rows:                             # nothing of client B under A's identity, or the other way
        assert not (r["pan"] == a.pan and r["arn"] == sub_b.arn)
        assert not (r["pan"] == c.pan and r["arn"] == sub_a.arn)
    assert h.counts["dropped"] >= 1


def test_r1_a_read_across_a_logout_is_dropped_not_written(sim):
    """The same, with the page after the logout at another address: the read is torn and asked again."""
    b = Browser()
    w, tab = _start(b)
    a, c = make_clients(2, first=60)
    t, sub_a = itr_filing(tab, 2.0, a, 1, logout=False)
    tab.navigate(t, Page(ITR_PROFILE, _profile_lines(a), PORTAL_ITR, read_ms=4000.0))
    tab.navigate(t + 1.0, Page(ITR_LOGOUT, LOGGED_OUT, PORTAL_ITR))
    t, sub_c = itr_filing(tab, t + 4.0, c, 2)
    h = go(sim, b, [sub_a, sub_c], t + 20.0)
    assert h.counts["loop_torn"] + h.counts["dropped"] >= 1


def _heavy_success(client, serial, read_ms, ack_visible):
    b = Browser()
    _w, tab = _start(b)
    t, sub = gst_filing(tab, 2.0, client, serial, read_ms=40.0, success_read_ms=read_ms, ack_visible=ack_visible)
    return b, t, sub


def test_r2_scope_stays_fresh_while_a_5s_read_runs(sim):
    """While a 5 s page read is in flight the address is still looked at (the scope worker is never queued behind it)."""
    from core.vsdc.vsdc_reader import URL_FRESH_SEC
    c = make_client(7)
    b, t, sub = _heavy_success(c, 1, 5000.0, 14.0)
    h = sim(b)
    hwnd = next(iter(b.windows))
    h.run(sub.visible_from + 0.6)                    # the 5 s read of the success page has just begun
    ages, seen = [], 0
    while h.rel() < sub.visible_from + 4.5:
        h.run(h.rel() + 0.25)
        ws = h.window_state(hwnd)
        if ws is not None and ws.pending_seq:
            seen += 1
            ages.append(h.vc.now() - ws.url_at)
    assert seen >= 8, "the content read was not in flight"
    assert max(ages) < URL_FRESH_SEC + 0.5, f"the address went stale during the read ({max(ages):.1f}s)"
    h.run(t + 20.0)
    h.end_day()
    assert_profile(h, [sub])


def test_r2_a_page_change_during_a_5s_read_is_noticed_within_two_seconds(sim):
    b = Browser()
    _w, tab = _start(b)
    c = make_client(8)
    t, sub = gst_filing(tab, 2.0, c, 1, success_read_ms=5000.0, ack_visible=3.0, logout=False)
    nxt = t + 0.5
    tab.navigate(nxt, Page(GST_RET + "dashboard", ["Dashboard", gst_header(c)], PORTAL_GST))
    h = sim(b)
    hwnd = next(iter(b.windows))
    h.run(nxt + 1.5)
    ws = h.window_state(hwnd)
    assert ws.pending_seq and ws.url.endswith("dashboard"), "the new address was not learned during the read"


def test_r2_toast_during_a_5s_read(sim):
    """An ARN toast that lives 1.5 s on a page whose read takes 5 s: no UIA read can see it; the OCR flash must."""
    c = make_client(9)
    b = Browser()
    _w, tab = _start(b)
    t, sub = gst_filing(tab, 2.0, c, 1, success_read_ms=5000.0, ack_visible=12.0, arn_toast_sec=1.5)
    go(sim, b, [sub], t + 20.0)


# ---- A: the reader is slow, busy or loaded ----------------------------------------------------------------------

def test_a2_typing_does_not_saturate_the_reader(sim):
    b = Browser()
    _w, tab = _start(b)
    tab.navigate(0.0, Page(ITR_LOGIN, ["Login", "Enter your User ID", "Continue"], PORTAL_ITR, read_ms=700.0))
    tab.typing(1.0, 119.0)
    h = go(sim, b, [], 120.0)
    assert h.backend.reads * 0.7 < 0.5 * 120.0, f"{h.backend.reads} reads of 0.7 s in 120 s of typing"


def test_a3_fast_switching_never_mixes_pages(sim):
    b = Browser()
    w = b.window(tabs=2)
    b.focus(0.0, w)
    c1, c2 = make_clients(2, first=70)
    _t, s1 = gst_filing(w.tabs[0], 2.0, c1, 1, read_ms=600.0, ack_visible=40.0)
    _t, s2 = gst_filing(w.tabs[1], 2.0, c2, 2, read_ms=600.0, ack_visible=40.0)
    for k in range(1, 60):
        w.select(k * 0.7, w.tabs[k % 2])
    go(sim, b, [s1, s2], 80.0, allow_missed=True)


def test_a5_pages_read_slower_and_slower_are_all_captured(sim):
    b = Browser()
    _w, tab = _start(b)
    t, expected = 2.0, []
    for i, (c, ms) in enumerate(zip(make_clients(5, first=80), (100.0, 300.0, 900.0, 1800.0, 3200.0))):
        t, sub = gst_filing(tab, t, c, i + 1, read_ms=ms, success_read_ms=ms, month=i)
        expected.append(sub)
    go(sim, b, expected, t + 20.0)


def test_a6_forty_tabs_tab_id_cache(sim):
    b = Browser()
    w = b.window(tabs=40)
    b.focus(0.0, w)
    for i, tab in enumerate(w.tabs):
        tab.navigate(0.0, Page(f"https://tab{i}.example.test/", [f"Document {i}", "text"], OUTSIDE))
    c = make_client(90)
    t, sub = gst_filing(w.tabs[17], 5.0, c, 1)
    w.select(4.0, w.tabs[17])
    h = go(sim, b, [sub], t + 20.0)
    be = h.backend
    assert be.calls["tab_id"] <= be.calls["documents"] + 2 * be.reads + 4, "the selected tab is asked for outside the reads"
    assert be.calls["tab_id"] < h.ticks / 1.5, "the selected tab is asked for on nearly every tick"


def test_a7_slow_browser_late_read_absorbed(sim):
    c = make_client(91)
    b, t, sub = _heavy_success(c, 1, 6000.0, 9.0)
    go(sim, b, [sub], t + 20.0)


def test_a10_spinner_never_passes_the_gate(sim):
    b = Browser()
    _w, tab = _start(b)
    tab.navigate(0.0, Page(ITR_LOGIN, ["Login", "Please wait"], PORTAL_ITR, read_ms=200.0))
    for k in range(300):
        tab.toast(2.0 + k * 0.4, Toast([], 0.2, 2))              # two cells blinking, as a spinner does
    h = go(sim, b, [], 125.0)
    assert h.backend.reads <= 125 // 5 + 8, f"{h.backend.reads} reads of a page that only spins"


# ---- B: the page changes under a read ----------------------------------------------------------------------------

def test_b1_tab_switch_mid_read(sim):
    b = Browser()
    w = b.window(tabs=2)
    b.focus(0.0, w)
    c1, c2 = make_clients(2, first=100)
    _t, s1 = gst_filing(w.tabs[0], 2.0, c1, 1, success_read_ms=3000.0, ack_visible=30.0)
    _t, s2 = gst_filing(w.tabs[1], 2.0, c2, 2, success_read_ms=3000.0, ack_visible=30.0)
    w.select(s1.visible_from + 1.0, w.tabs[1])         # away while tab 0's 3 s read is running: that read is torn
    w.select(s1.visible_from + 20.0, w.tabs[0])
    go(sim, b, [s1, s2], s1.visible_until + 20.0)


def test_b2_url_change_during_read_is_torn(sim):
    b = Browser()
    _w, tab = _start(b)
    c = make_client(110)
    tab.navigate(2.0, Page(ITR_PROFILE, _profile_lines(c), PORTAL_ITR, read_ms=3000.0))
    tab.navigate(3.0, Page(ITR + "dashboard/other", ["Dashboard"], PORTAL_ITR))
    t, sub = itr_filing(tab, 12.0, c, 1, login=False)
    h = go(sim, b, [sub], t + 20.0)
    assert h.counts["loop_torn"] >= 1


def test_b3_foreground_change_mid_tick(sim):
    b = Browser()
    w1 = b.window(tabs=1)
    w2 = b.window(tabs=1)
    c1, c2 = make_clients(2, first=120)
    _t, s1 = gst_filing(w1.tabs[0], 2.0, c1, 1, read_ms=1200.0, ack_visible=40.0)
    _t, s2 = gst_filing(w2.tabs[0], 2.0, c2, 2, read_ms=1200.0, ack_visible=40.0)
    for k in range(0, 60):
        b.focus(k * 1.1, (w1, w2)[k % 2])
    go(sim, b, [s1, s2], 80.0, allow_missed=True)


def test_b4_modal_mid_read_invents_nothing(sim):
    b = Browser()
    _w, tab = _start(b)
    c, other = make_clients(2, first=130)
    tab.navigate(2.0, Page(ITR_PROFILE, _profile_lines(c), PORTAL_ITR, read_ms=3000.0))
    tab.toast(3.0, Toast(["Switch user?", "PAN", other.pan, "Continue"], 2.0, 14))
    t, sub = itr_filing(tab, 12.0, c, 1, login=False)
    h = go(sim, b, [sub], t + 20.0)
    assert not [r for _t, r in h.rows if r["pan"] == other.pan]


def test_b6_two_clients_two_windows_alternating(sim):
    b = Browser()
    w1 = b.window(tabs=1)
    w2 = b.window(tabs=1)
    c1, c2 = make_clients(2, first=140)
    _t, s1 = gst_filing(w1.tabs[0], 2.0, c1, 1, ack_visible=30.0)
    _t, s2 = gst_filing(w2.tabs[0], 2.0, c2, 2, ack_visible=30.0)
    for k in range(0, 40):
        b.focus(k * 2.5, (w1, w2)[k % 2])
    go(sim, b, [s1, s2], 100.0)


# ---- C: the evidence is short-lived ----------------------------------------------------------------------------------

def test_c1_toast_one_second_on_a_light_page_is_caught(sim):
    c = make_client(150)
    b = Browser()
    _w, tab = _start(b)
    t, sub = gst_filing(tab, 2.0, c, 1, arn_toast_sec=1.0)
    go(sim, b, [sub], t + 20.0)


def test_c1_toast_one_second_on_a_heavy_page_is_caught_by_ocr(sim):
    c = make_client(151)
    b = Browser()
    _w, tab = _start(b)
    t, sub = gst_filing(tab, 2.0, c, 1, success_read_ms=3000.0, arn_toast_sec=1.0)
    go(sim, b, [sub], t + 20.0)


def test_c1_toast_one_second_on_heavy_page_is_caught_by_ocr(sim):
    c = make_client(152)
    b = Browser()
    _w, tab = _start(b)
    t, sub = gst_filing(tab, 2.0, c, 1, read_ms=2000.0, arn_toast_sec=1.0)
    h = go(sim, b, [sub], t + 25.0)
    assert h.counts["loop_ocr_absorbed"] >= 1


@pytest.mark.parametrize("seconds", [2.5, 4.0])
def test_c2_redirect_after_success(sim, seconds):
    b = Browser()
    _w, tab = _start(b)
    c = make_client(152)
    t, sub = itr_filing(tab, 2.0, c, 1, ack_visible=seconds)
    go(sim, b, [sub], t + 20.0)


def test_c3_user_closes_modal_at_once(sim):
    b = Browser()
    _w, tab = _start(b)
    c = make_client(153)
    t, sub = gst_filing(tab, 2.0, c, 1, arn_toast_sec=0.6)
    go(sim, b, [sub], t + 20.0)


@pytest.mark.xfail(strict=True, reason="WP10 watches visible browser windows that are not in front")
def test_c8_success_on_second_monitor_is_captured(sim):
    p = parallel_filing(clients=3, background_success=True)
    h = sim(p.browser)
    h.run(p.duration)
    h.end_day()
    assert_profile(h, p.expected)


# ---- D: where the page is ------------------------------------------------------------------------------------------------

def _two_tabs_same_address(second_tab_starts, stint):
    b = Browser()
    w = b.window(tabs=2)
    b.focus(0.0, w)
    c1, c2 = make_clients(2, first=160)
    _t, s1 = itr_filing(w.tabs[0], 2.0, c1, 1, ack_visible=40.0, logout=False)
    _t, s2 = itr_filing(w.tabs[1], second_tab_starts, c2, 2, ack_visible=40.0, logout=False)
    first_switch = second_tab_starts - 2.0 if second_tab_starts > 10.0 else 5.0
    for k in range(40):
        w.select(first_switch + k * stint, w.tabs[(k + 1) % 2])
    return b, [s1, s2]


def test_d1_same_url_two_clients_two_tabs(sim):
    """Tab 0's client has been read before the other tab is first visited: each tab keeps its own session."""
    b, expected = _two_tabs_same_address(30.0, 12.0)
    go(sim, b, expected, 260.0)


@pytest.mark.xfail(strict=True, reason="OPEN FINDING: a tab never read before a second tab joins the session in view "
                                       "(SgtShadow._observe_window); its later acknowledgement lands under the other "
                                       "client. Rare; the fix belongs with WP9 identity persistence")
def test_d1_harsh_both_tabs_unread_before_the_first_switch(sim):
    b, expected = _two_tabs_same_address(2.0, 5.0)
    go(sim, b, expected, 260.0)


def test_d13_typed_address_bar_does_not_change_scope(sim):
    b = Browser()
    w, tab = _start(b)
    c = make_client(170)
    t, sub = gst_filing(tab, 2.0, c, 1)
    w.type_in_bar(0.0, t + 10.0, "https://search.example.test/?q=gstr3b status")
    go(sim, b, [sub], t + 20.0)


def test_d14_only_in_scope_documents_are_read(sim):
    b = Browser()
    w = b.window(tabs=4)
    b.focus(0.0, w)
    outside = []
    for i, tab in enumerate(w.tabs[1:]):
        outside.append(tab.navigate(0.0, Page(f"https://tab{i}.example.test/", ["PAN", "ABCDE1234F"], OUTSIDE)))
    t, sub = gst_filing(w.tabs[0], 2.0, make_client(171), 1)
    after = max(t, 32.0) + 3.0
    for k, tab in enumerate(w.tabs[1:]):
        w.select(30.0 + k, tab)
    w.select(after, w.tabs[0])
    go(sim, b, [sub], after + 20.0)
    assert all(p.reads == 0 for p in outside), "a page outside the portals was read"


def test_d6_fifteen_restored_tabs(sim):
    b = Browser()
    w = b.window(tabs=15)
    b.focus(0.0, w)
    cs = make_clients(15, first=200)
    for tab, c in zip(w.tabs, cs):
        tab.navigate(0.0, Page(ITR_PROFILE, _profile_lines(c), PORTAL_ITR))
    for k, tab in enumerate(w.tabs):
        w.select(5.0 + 4.0 * k, tab)
    t, sub = itr_filing(w.tabs[6], 5.0 + 4.0 * 15 + 2.0, cs[6], 1, login=False)
    w.select(5.0 + 4.0 * 15 + 1.0, w.tabs[6])
    go(sim, b, [sub], t + 20.0, known=cs)


# ---- E: sessions and identity ---------------------------------------------------------------------------------------------

def test_e1_forty_clients_back_to_back(sim):
    b = Browser()
    _w, tab = _start(b)
    t, expected = 2.0, []
    for i, c in enumerate(make_clients(40, first=300)):
        t, sub = gst_filing(tab, t, c, i + 1, month=i, dwell=2.0, ack_visible=6.0)
        expected.append(sub)
    go(sim, b, expected, t + 20.0)


def test_e5_b2b_page_with_200_recipient_gstins_never_splits(sim):
    from tests.sgt_sim.profiles import b2b_marathon
    p = b2b_marathon(minutes=3.0, recipients=200)
    h = sim(p.browser)
    h.run(p.duration)
    h.end_day()
    assert_profile(h, p.expected)
    assert len({r["session_id"] for _t, r in h.rows}) == 1


def test_e7_login_page_ends_the_session_and_the_next_client_names_itself(sim):
    b = Browser()
    _w, tab = _start(b)
    c1, c2 = make_clients(2, first=400)
    t, s1 = itr_filing(tab, 2.0, c1, 1, logout=False)
    t, s2 = itr_filing(tab, t + 1.0, c2, 2, logout=False)
    go(sim, b, [s1, s2], t + 20.0)


def test_e9_app_restart_mid_filing_keeps_the_draft(sim):
    b = Browser()
    _w, tab = _start(b)
    c = make_client(410)
    restart_at = []

    def die(tab, t):
        restart_at.append(t + 2.0)
        return t + 6.0

    t, sub = gst_filing(tab, 2.0, c, 1, work=die)
    h = sim(b, keep_state=True)
    h.run(restart_at[0])
    h.restart_app()
    h.run(t + 20.0)
    h.end_day()
    assert_profile(h, [sub])


# ---- F, G, H: slow, half-loaded and failing portals --------------------------------------------------------------------

def test_f1_slow_portal_half_loaded_pages(sim):
    b = Browser()
    _w, tab = _start(b)
    c = make_client(420)
    scratch = Tab()
    t, sub = gst_filing(scratch, 2.0, c, 1, read_ms=1500.0, ack_visible=14.0)
    for nt, page in zip(scratch._nav_t, scratch._nav_p):
        if nt == sub.visible_from:                                  # the success page arrives four seconds late
            tab.navigate(nt, Page(GST_FILE, ["Dashboard", "Please wait"], PORTAL_GST, read_ms=1500.0))
            nt += 4.0
        tab.navigate(nt, page)
    go(sim, b, [sub], t + 25.0)


def test_f3_already_filed_message_adds_no_row(sim):
    b = Browser()
    _w, tab = _start(b)
    c = make_client(430)
    header = gst_header(c)
    tab.navigate(2.0, Page(GST_RET + "dashboard", ["Returns", header, "GSTR3B", "Return Period - September"],
                           PORTAL_GST))
    tab.navigate(8.0, Page(GST_RET + "gstr3b", ["Returns", header, "You have already filed GSTR3B for this period"],
                           PORTAL_GST))
    h = go(sim, b, [], 40.0)
    assert not [r for _t, r in h.rows if r["status"] in ("Submitted", "Submitted & Verified", "Submitted (Not Verified)")]


def test_f4_processing_error_never_promotes(sim):
    b = Browser()
    _w, tab = _start(b)
    c = make_client(431)
    header = gst_header(c)
    tab.navigate(2.0, Page(GST_RET + "gstr3b", ["Returns", header, "GSTR3B - Return", "Return Period - September",
                                               "Status - Not Filed"], PORTAL_GST))
    tab.navigate(10.0, Page(GST_FILE, ["Returns", header, "Your request could not be processed. Please try again",
                                      "Status - Not Filed"], PORTAL_GST))
    h = go(sim, b, [], 40.0, known=[c])
    assert not [r for _t, r in h.rows if r["status"] in ("Submitted", "Submitted & Verified", "Submitted (Not Verified)")]


def test_g3_partial_first_read_after_idle_is_retried(sim):
    b = Browser()
    _w, tab = _start(b)
    c = make_client(440)
    t, sub = gst_filing(tab, 2.0, c, 1)
    tab.page_at(sub.visible_from + 0.1).partial_first_read = True
    go(sim, b, [sub], t + 20.0)


def test_g4_sleeping_tab_reload(sim):
    b = Browser()
    _w, tab = _start(b)
    c = make_client(441)
    header = gst_header(c)
    same = GST_RET + "gstr3b"
    tab.navigate(2.0, Page(same, ["Returns", header, "GSTR3B - Return", "Status - Not Filed"], PORTAL_GST))
    t, sub = gst_filing(tab, 12.0, c, 1, login=False)
    go(sim, b, [sub], t + 20.0)


def test_h2_a_wedged_read_does_not_stop_later_filings(sim):
    b = Browser()
    _w, tab = _start(b)
    c1, c2 = make_clients(2, first=450)
    stuck = Page(GST_RET + "gstr1/b2b", ["Details", gst_header(c1)], PORTAL_GST, wedge=True)
    tab.navigate(2.0, stuck)
    tab.navigate(30.0, Page(GST_RET + "gstr1/summary", ["Summary", gst_header(c1)], PORTAL_GST))
    t, sub = gst_filing(tab, 36.0, c2, 1, login=False)
    go(sim, b, [sub], t + 20.0, known=[c1])
