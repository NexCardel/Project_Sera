"""
One SGT session per browser tab (2026-10-13). Before, a window was one session: after a switch to
another client's tab, a page that names no client (a returns dashboard) fed the client of the tab
the user came from. Now the router hands SGT the id of the tab in front
(vsdc_uia_text.selected_tab_id) and each tab keeps its own session; a tab opened from a session
joins it (same login); a tab whose session ended starts afresh. Also: a session born from a clash
never takes its name from the page that caused the clash, re-reads included. Identifiers are
fictional.
"""
from types import SimpleNamespace

from core.sgt.sgt_shadow import _session_from_json, _session_to_json
from test_sgt_boundaries import GST_A, GST_B, GST_LOGIN, GST_LOGOUT, GST_WELCOME, _shade, ends
from test_sgt_shadow import GST, Rig, frame

T1, T2, T3 = "42.1.4.0.0.783", "42.1.4.0.0.891", "42.1.4.0.0.914"
DASH = "https://return.gst.gov.in/returns/auth/dashboard"
DASH_PAGE = ["Returns Dashboard", "Financial Year", "2026-27", "Return Filing Period", "Quarter", "SEARCH"]
STRAY = ["x", " SOME TRADERS 27AAACZ9876K1ZE", "y"]     # a table row shaped like the header


class TabRig(Rig):
    def at(self, tab, url, page, hwnd=1):
        self.clock[0] += 0.35
        self.page = page
        return self.sgt.observe(hwnd, GST, url, frame=frame(_shade()), tab=tab)


def gstin(s):
    return (s.profile.get("gstin") or {}).get("value")


class TestEachTabHasItsOwnSession:
    def test_a_page_with_no_client_on_it_belongs_to_its_own_tab(self, tmp_path):
        r = TabRig(tmp_path)
        r.at(T1, GST_WELCOME, GST_A)
        a = r.sgt._sessions[1]
        r.at(T2, GST_WELCOME, GST_B)
        b = r.sgt._sessions[1]
        assert b is not a and gstin(b) == "27AAACZ9876K1ZE"
        r.at(T1, DASH, DASH_PAGE)                   # back to A's tab: no GSTIN on this page
        assert r.sgt._sessions[1] is a
        r.at(T2, DASH, DASH_PAGE)                   # and to B's
        assert r.sgt._sessions[1] is b
        assert a.tabs == {T1} and b.tabs == {T2} and ends(r) == []
        assert gstin(a) == "19ABCPD1234E1ZB" and gstin(b) == "27AAACZ9876K1ZE"

    def test_without_tab_ids_the_window_is_one_session_as_before(self, tmp_path):
        r = TabRig(tmp_path)
        r.at(None, GST_WELCOME, GST_A)
        r.at(None, GST_WELCOME, GST_B)
        b = r.sgt._sessions[1]
        r.at(None, DASH, DASH_PAGE)
        assert r.sgt._sessions[1] is b              # nothing to tell the tabs apart with

    def test_a_new_tab_joins_the_session_it_was_opened_from(self, tmp_path):
        r = TabRig(tmp_path)
        r.at(T1, GST_WELCOME, GST_A)
        a = r.sgt._sessions[1]
        r.at(T3, DASH, DASH_PAGE)                   # ctrl+click: same login, same client
        assert r.sgt._sessions[1] is a and a.tabs == {T1, T3}
        r.at(T1, GST_WELCOME, GST_A)
        assert r.sgt._sessions[1] is a and ends(r) == []

    def test_a_logout_in_one_tab_leaves_the_other_tabs_session(self, tmp_path):
        r = TabRig(tmp_path)
        r.at(T1, GST_WELCOME, GST_A)
        a = r.sgt._sessions[1]
        r.at(T2, GST_WELCOME, GST_B)
        r.at(T2, GST_LOGOUT, ["You have logged out"])
        assert len(ends(r)) == 1
        r.at(T1, DASH, DASH_PAGE)
        assert r.sgt._sessions[1] is a and len(ends(r)) == 1

    def test_a_tab_whose_session_ended_starts_afresh_not_in_the_next_clients_session(self, tmp_path):
        r = TabRig(tmp_path)
        r.at(T1, GST_WELCOME, GST_A)
        a = r.sgt._sessions[1]
        r.at(T2, DASH, DASH_PAGE)                   # opened from A's session: joins it
        assert a.tabs == {T1, T2}
        r.at(T2, GST_LOGOUT, ["You have logged out"])
        r.at(T2, GST_LOGIN, ["Login", "Username", "Username", "meera_27", "Password", "Password", "LOGIN"])
        r.at(T2, GST_WELCOME, GST_B)
        b = r.sgt._sessions[1]
        assert gstin(b) == "27AAACZ9876K1ZE" and b.tabs == {T2}
        r.at(T1, DASH, DASH_PAGE)                   # A's old tab: its login is over
        c = r.sgt._sessions[1]
        assert c is not b and not gstin(c) and T1 in c.tabs and b.tabs == {T2}
        r.at(T1, GST_WELCOME, GST_B)                # reloaded: B's page now - B's session comes back
        assert r.sgt._sessions[1] is b and T1 in b.tabs

    def test_a_tab_moves_to_the_session_that_claims_it(self, tmp_path):
        r = TabRig(tmp_path)
        r.at(T1, GST_WELCOME, GST_A)
        a = r.sgt._sessions[1]
        r.at(T2, GST_WELCOME, GST_B)
        b = r.sgt._sessions[1]
        r.at(T2, GST_WELCOME, GST_A)                # B's tab now shows A's header: A's session
        assert r.sgt._sessions[1] is a and a.tabs == {T1, T2} and T2 not in b.tabs

    def test_tabs_survive_the_crash_snapshot(self, tmp_path):
        r = TabRig(tmp_path)
        r.at(T1, GST_WELCOME, GST_A)
        r.at(T3, DASH, DASH_PAGE)
        s = _session_from_json(_session_to_json(r.sgt._sessions[1]))
        assert s.tabs == {T1, T3}


class TestTheClashPageNeverNamesTheSession:
    def test_a_stray_header_row_does_not_name_the_new_session_on_a_re_read(self, tmp_path):
        r = TabRig(tmp_path)
        r.at(T1, GST_WELCOME, GST_A)
        a = r.sgt._sessions[1]
        r.at(T1, DASH, STRAY)                       # clash inside A's tab: a strict session starts
        s = r.sgt._sessions[1]
        assert s is not a and s.strict and not gstin(s)
        for _ in range(3):                          # the same page read again (it redraws)
            r.at(T1, DASH, STRAY)
            assert r.sgt._sessions[1] is s and not gstin(s)
        assert s.strict_block == {"url": DASH, "values": {"gstin": "27AAACZ9876K1ZE"}}
        r.at(T1, GST_WELCOME, GST_A)                # A's header again: A's session is back
        assert r.sgt._sessions[1] is a

    def test_another_page_showing_the_client_names_it_at_once(self, tmp_path):
        r = TabRig(tmp_path)
        r.at(T1, GST_WELCOME, GST_A)
        r.at(T1, DASH, STRAY)
        s = r.sgt._sessions[1]
        r.at(T1, GST_WELCOME, GST_B)                # one sighting on another page is enough
        assert r.sgt._sessions[1] is s and gstin(s) == "27AAACZ9876K1ZE"

    def test_without_a_tab_id_a_re_read_still_names_it_as_before(self, tmp_path):
        # Two GST clients' welcome pages share one link: with no tab id, the re-read is how the
        # second client's tab gets its name.
        r = TabRig(tmp_path)
        r.at(None, GST_WELCOME, GST_A)
        r.at(None, GST_WELCOME, GST_B)
        s = r.sgt._sessions[1]
        assert s.strict and s.strict_block == {} and not gstin(s)
        r.at(None, GST_WELCOME, GST_B)
        assert gstin(s) == "27AAACZ9876K1ZE"

    def test_the_block_survives_the_crash_snapshot(self, tmp_path):
        r = TabRig(tmp_path)
        r.at(T1, GST_WELCOME, GST_A)
        r.at(T1, DASH, STRAY)
        s = _session_from_json(_session_to_json(r.sgt._sessions[1]))
        assert s.strict and s.strict_block["values"] == {"gstin": "27AAACZ9876K1ZE"}


# ── the tab id and the router ────────────────────────────────────────────────────
class _Pattern:
    def __init__(self, selected):
        self.CurrentIsSelected = selected

    def QueryInterface(self, _iface):
        return self


class _Tab:
    def __init__(self, rid, selected, broken=False):
        self.rid, self.selected, self.broken = rid, selected, broken

    def GetCurrentPattern(self, _pid):
        if self.broken:
            raise OSError("element not available")
        return _Pattern(self.selected)

    def GetRuntimeId(self):
        return self.rid


def test_the_selected_tab_is_named_by_its_runtime_id():
    from core.vsdc import vsdc_uia_text as u
    client = SimpleNamespace(UIA_SelectionItemPatternId=10010, IUIAutomationSelectionItemPattern=object)
    tabs = [_Tab((42, 7, 4, 1), False, broken=True), _Tab((42, 7, 4, 2), False), _Tab((42, 7, 4, 3), True)]
    assert u._selected_among(client, tabs) == "42.7.4.3"
    assert u._selected_among(client, tabs[:2]) is None


def _router(monkeypatch, tab, busy):
    from core.vsdc import vsdc_router, vsdc_uia_text
    seen = []
    r = object.__new__(vsdc_router.VSDCRouter)
    r._sgt = SimpleNamespace(observe=lambda *a, **k: seen.append(k.get("tab")))
    r._tick_portal, r._tick_page_url, r.ocr, r.diag_tick = GST, GST_WELCOME, None, {}
    r._capture_window = lambda h: None
    monkeypatch.setattr(vsdc_uia_text, "selected_tab_id", lambda h: tab)
    monkeypatch.setattr(vsdc_uia_text, "was_busy", lambda: busy)
    return r, seen


def test_the_router_hands_sgt_the_tab(monkeypatch):
    r, seen = _router(monkeypatch, T1, False)
    r._run_sgt(5)
    assert seen == [T1] and r.diag_tick["tab"] == "known"


def test_the_router_waits_a_tick_while_uia_is_busy(monkeypatch):
    r, seen = _router(monkeypatch, None, True)
    r._run_sgt(5)
    assert seen == []


def test_a_browser_with_no_tab_strip_is_still_read(monkeypatch):
    r, seen = _router(monkeypatch, None, False)
    r._run_sgt(5)
    assert seen == [None] and r.diag_tick["tab"] == "unknown"
