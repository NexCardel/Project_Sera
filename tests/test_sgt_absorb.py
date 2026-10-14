"""
SGT absorbs by provenance (SGT never-miss blueprint 5.2, 6.1-6.3 and WP5, with R1 and R6): a read
the capture loop asked for lands in the session that asked for it, never in the one in view when it
comes back; a torn read is dropped and asked again; the closed ring never takes a read across a
logout, a login page or another client; OCR only ever adds, and raises a status only with an ARN.
Every page, PAN, name and ack here is made up.
"""
from unittest.mock import MagicMock

from core.sgt.sgt_shadow import BLIND_RETRIES, BLIND_RETRY, IDLE_END_SEC, MISSING_CLEAR_SEC, RESULT_MAX_AGE_SEC
from core.vsdc.vsdc_reader import ReadContext, ReadResult

from test_sgt_gst_fixes import GSTIN, R3B, client as gst_client, r3b_page
from test_sgt_shadow import (FILED_URL, GST, ITR, LOGIN_URL, LOGOUT_URL, PROFILE_PAGE, PROFILE_URL, Rig,
                             filed_page, frame)

PAN_A = "ABCPD1234E"                          # PROFILE_PAGE's client
PAN_B = "BCDPE5678F"
PROFILE_B = ["MEERA RAO Individual", "Profile", "Name", "MEERA RAO", "PAN", PAN_B, "Date of Birth", "02-Feb-1985"]
ACK_A = "123456789150925"
ACK_B = "987654321100925"
DASHBOARD = "https://eportal.incometax.gov.in/iec/foservices/#/dashboard"
EVERIFY_URL = "https://eportal.incometax.gov.in/iec/foservices/#/dashboard/eVerifyReturn/done"


class AbsorbRig(Rig):
    """The capture loop's side, by hand: observe_window per tick, request_made when a read is
    asked, absorb when its result comes back - in whatever order the test needs."""

    def __init__(self, tmp_path, **kw):
        super().__init__(tmp_path, **kw)
        self.seq = 0

    def window(self, url, hwnd=1, tab="t1", portal=ITR, advance=0.35):
        self.clock[0] += advance
        return self.sgt.observe_window(hwnd, tab, portal, url)

    def ask(self, url, hwnd=1, tab="t1", portal=ITR):
        s = self.window(url, hwnd, tab, portal)
        self.seq += 1
        ctx = ReadContext(seq=self.seq, hwnd=hwnd, portal=portal, url=url, tab=tab, asked_at=self.clock[0])
        self.sgt.request_made(ctx, s)
        return ctx

    def back(self, ctx, lines, source="uia", tab_after=None, url_after=None, error="", **kw):
        """The result of ctx: as asked, unless the tab or URL changed during the read."""
        res = ReadResult(ctx=ctx, source=source, lines=list(lines), tab_before=ctx.tab,
                         tab_after=tab_after or ctx.tab, url_before=ctx.url, url_after=url_after or ctx.url,
                         started_at=ctx.asked_at, finished_at=ctx.asked_at + 0.2, error=error)
        return self.sgt.absorb(res, **kw)

    def read(self, url, lines, **kw):
        """A read asked and answered at once (the ordinary case)."""
        tab, portal = kw.pop("tab", "t1"), kw.pop("portal", ITR)
        self.clock[0] += kw.pop("advance", 0.0)
        return self.back(self.ask(url, tab=tab, portal=portal), lines, **kw)

    def session(self, hwnd=1):
        return self.sgt._sessions.get(hwnd)

    def rows(self):
        return self.sgt.drain()


def arns(rows):
    return [x["arn"] for x in rows]


# ── Which session a read lands in (6.1) ────────────────────────────────────────────────────────

def test_a_late_result_lands_in_the_session_that_asked(tmp_path):
    r = AbsorbRig(tmp_path)
    r.read(PROFILE_URL, PROFILE_PAGE)
    ctx = r.ask(FILED_URL)                         # a slow read of the filed-returns page...
    r.window(DASHBOARD, advance=3)                 # ...the user has moved on before it comes back
    r.back(ctx, filed_page(ack=ACK_A))
    rows = r.rows()
    row = [x for x in rows if x["arn"] == ACK_A][-1]
    assert row["pan"] == PAN_A and row["page_url"] == FILED_URL     # owned by the page it read


def test_a_result_for_a_shelved_session_does_not_touch_the_one_in_view(tmp_path):
    r = AbsorbRig(tmp_path)
    r.read(PROFILE_URL, PROFILE_PAGE, tab="t1")
    a = r.session()
    ctx = r.ask(FILED_URL, tab="t1")               # client A's page read, in flight
    r.read(PROFILE_URL, PROFILE_B, tab="t2")       # meanwhile: another tab, another client
    b = r.session()
    assert b is not a and b.profile["pan"]["value"] == PAN_B
    r.back(ctx, filed_page(ack=ACK_A))             # A's read comes back: into A, on the shelf
    assert [sl.arn for sl in a.slots] == [ACK_A] and b.slots == []
    assert r.session() is b
    assert [x["pan"] for x in r.rows() if x["arn"] == ACK_A] == [PAN_A]


def test_a_result_after_logout_is_dropped(tmp_path):
    """R1: the page a read started on may be the next client's by the time it returns."""
    r = AbsorbRig(tmp_path)
    r.read(PROFILE_URL, PROFILE_PAGE)
    r.rows()
    ctx = r.ask(FILED_URL)
    r.window(LOGOUT_URL)                           # session A ends: logout
    assert r.back(ctx, filed_page(ack=ACK_A)) is None
    assert ACK_A not in arns(r.rows())
    assert not [e for e in r.events("dataset") if e.get("change") == "new"]
    assert not r.events("late_absorbed_after_end")


def test_logout_then_next_client_in_the_same_tab_never_mixes(tmp_path):
    r = AbsorbRig(tmp_path)
    r.read(PROFILE_URL, PROFILE_PAGE)
    a = r.session()
    ctx = r.ask(FILED_URL)                         # asked on client A's last page
    r.window(LOGOUT_URL)
    r.window(LOGIN_URL)
    r.window(DASHBOARD)                            # client B logged in, same tab
    # The read comes back showing B's page: neither A nor B takes it.
    assert r.back(ctx, PROFILE_B + filed_page(ack=ACK_B)) is None
    b = r.session()
    assert b is not a and not b.profile and b.slots == [] and a.slots == []
    # B's own reads name B and file B's ack under B.
    r.read(PROFILE_URL, PROFILE_B)
    r.read(FILED_URL, ["MEERA RAO Individual"] + filed_page(ack=ACK_B)[1:])
    rows = r.rows()
    assert {x["pan"] for x in rows if x["arn"] == ACK_B} == {PAN_B}
    assert not [x for x in rows if x["pan"] == PAN_A]
    r.sgt.end_all("t")
    ends = {e["session"]: e for e in r.events("session_end")}
    assert ends[a.session_id]["payload"]["datasets"] == []


def test_a_late_result_after_an_idle_end_is_still_absorbed(tmp_path):
    """The closed ring: idle (and a closed window) are not boundaries between clients."""
    r = AbsorbRig(tmp_path)
    r.read(PROFILE_URL, PROFILE_PAGE)
    a = r.session()
    ctx = r.ask(FILED_URL)
    r.clock[0] += IDLE_END_SEC + 1
    r.sgt.observe_window(2, None, ITR, PROFILE_URL)    # another window's tick ends A by idle
    assert r.session() is None
    r.back(ctx, filed_page(ack=ACK_A))
    assert [sl.arn for sl in a.slots] == [ACK_A]
    assert [x["pan"] for x in r.rows() if x["arn"] == ACK_A] == [PAN_A]
    assert r.events("late_absorbed_after_end")


def test_another_clients_page_cuts_the_reads_asked_before(tmp_path):
    r = AbsorbRig(tmp_path)
    r.read(PROFILE_URL, PROFILE_PAGE, tab="t1")
    a = r.session()
    ctx = r.ask(FILED_URL, tab="t1")
    r.read(PROFILE_URL, PROFILE_B, tab="t1")       # same tab, another client's PAN: a contradiction
    assert r.session() is not a
    assert r.back(ctx, filed_page(ack=ACK_A)) is None
    assert a.slots == [] and ACK_A not in arns(r.rows())


# ── Drop rules (6.1, 6.3) ──────────────────────────────────────────────────────────────────────

def test_torn_tab_results_are_dropped(tmp_path):
    r = AbsorbRig(tmp_path)
    ctx = r.ask(PROFILE_URL)
    assert r.back(ctx, PROFILE_PAGE, tab_after="t2") is None
    assert r.events("profile") == [] and r.sgt.take_diag()["dropped"] == 1


def test_torn_url_results_are_dropped_and_re_requested(tmp_path):
    """R1: a URL torn read is dropped exactly like a tab change - by SGT and by the capture loop,
    which asks for the page again (torn-retry)."""
    r = AbsorbRig(tmp_path)
    ctx = r.ask(PROFILE_URL)
    assert r.back(ctx, PROFILE_PAGE, url_after=DASHBOARD) is None
    assert r.events("profile") == []

    from tests.test_vsdc_capture import Rig as LoopRig
    from tests.test_vsdc_reader import PORTAL_2
    loop = LoopRig()
    try:
        loop.step()
        loop.win.docs[0].hooks.append(lambda: setattr(loop.win.docs[0], "url", PORTAL_2))   # mid-read
        loop.step(2)
        assert loop.sgt.observed == []
        assert loop.page_reads()[-1].reason == "torn-retry"
        loop.step()
        assert [o.url for o in loop.sgt.observed] == [PORTAL_2]
    finally:
        loop.close()


def test_torn_results_never_clear_a_piece(tmp_path):
    r = AbsorbRig(tmp_path)
    gst_client(r)
    r.read(R3B, r3b_page("June", "Not Filed"), portal=GST)
    before = dict(r.session().draft.values())
    assert before
    for _ in range(4):                             # empty, torn reads of the same page, well apart
        r.clock[0] += MISSING_CLEAR_SEC + 1
        r.back(r.ask(R3B, portal=GST), ["Returns", "x", "y"], url_after=R3B + "/next")
    assert r.session().draft.values() == before


def test_results_older_than_60s_are_dropped(tmp_path):
    r = AbsorbRig(tmp_path)
    ctx = r.ask(PROFILE_URL)
    assert r.back(ctx, PROFILE_PAGE, now=ctx.asked_at + RESULT_MAX_AGE_SEC + 1) is None
    assert r.events("profile") == []
    ctx = r.ask(PROFILE_URL)
    r.back(ctx, PROFILE_PAGE, now=ctx.asked_at + 1)
    assert r.events("profile")


def test_a_result_nobody_asked_for_is_dropped(tmp_path):
    r = AbsorbRig(tmp_path)
    r.window(PROFILE_URL)
    stray = ReadContext(seq=999, hwnd=1, portal=ITR, url=PROFILE_URL, tab="t1", asked_at=r.clock[0])
    assert r.sgt.absorb(ReadResult(ctx=stray, lines=PROFILE_PAGE, tab_before="t1", tab_after="t1",
                                   url_before=PROFILE_URL, url_after=PROFILE_URL)) is None
    assert r.events("profile") == []


# ── OCR only adds (5.2, D3, R6) ────────────────────────────────────────────────────────────────

def test_ocr_never_sets_pan_gstin_or_name(tmp_path):
    r = AbsorbRig(tmp_path)
    r.read(PROFILE_URL, PROFILE_PAGE, source="ocr")
    assert r.session().profile == {} and r.events("profile") == []
    r.read(PROFILE_URL, PROFILE_PAGE)              # UIA names client A
    a = r.session()
    r.read(PROFILE_URL, PROFILE_B, source="ocr")   # an OCR read of another PAN neither renames nor splits
    assert r.session() is a and a.profile["pan"]["value"] == PAN_A and a.profile["name"]["value"] == "ASHOK KUMAR SEN"


def test_ocr_fills_only_an_empty_piece_and_never_clears(tmp_path):
    r = AbsorbRig(tmp_path)
    gst_client(r)
    r.read(R3B, ["Returns", "GSTR-3B - Monthly Return", "Return Period - June", "x"], portal=GST)
    before = dict(r.session().draft.values())
    r.read(R3B, r3b_page("September", "Filed", fy="2026-27"), portal=GST, source="ocr")
    after = r.session().draft.values()
    assert {k: after.get(k) for k in before} == before                 # nothing UIA set was changed
    assert "status" not in after                                       # wording never goes in from OCR
    assert set(after) - set(before)                                    # an empty piece was filled
    for _ in range(3):                                                 # OCR reads missing everything
        r.clock[0] += MISSING_CLEAR_SEC + 1
        r.read(R3B, ["Returns", "x", "y"], portal=GST, source="ocr")
    assert {k: r.session().draft.values().get(k) for k in before} == before


def test_ocr_period_marker_cannot_clear_a_uia_period(tmp_path):
    """2026-10-06: an OCR read of the GST return page lost the 'Period •' label and its value, and the
    period UIA had just set was cleared (then the next read 'opened' another period)."""
    r = AbsorbRig(tmp_path)
    gst_client(r)
    r.read(R3B, r3b_page("June", "Not Filed"), portal=GST)
    before = dict(r.session().draft.values())
    for _ in range(3):
        r.clock[0] += MISSING_CLEAR_SEC + 1
        r.read(R3B, ["Returns", "GSTR-3B - Monthly Return", "Period •", "x"], portal=GST, source="ocr")
    r.read(R3B, ["Returns", "GSTR-3B - Monthly Return", "Period •", "September", "x"], portal=GST, source="ocr")
    assert r.session().draft.values() == before
    assert not [e for e in r.events("current") if e.get("change") in ("cleared", "changed")]


def test_an_ocr_only_arn_makes_a_row_marked_ocr(tmp_path):
    r = AbsorbRig(tmp_path)
    r.read(PROFILE_URL, PROFILE_PAGE)
    r.read(FILED_URL, filed_page(ack=ACK_A), source="ocr")
    row = [x for x in r.rows() if x["arn"] == ACK_A][-1]
    assert row["pan"] == PAN_A and row["status"] == "Submitted (Not Verified)"
    src = row["raw_payload"]["source"]
    assert (src["evidence_source"], src["first_seen_as"]) == ("ocr", "ocr")


def test_an_ocr_only_row_is_counted_for_the_digest_and_a_uia_row_is_not(tmp_path):
    from datetime import date
    from core.sgt.sgt_health import SpecStats
    r = AbsorbRig(tmp_path)
    stats, told = SpecStats(tmp_path / "stats"), []
    r.sgt._stats, r.sgt._on_ocr_row = stats, lambda: told.append(1)
    today = date(2026, 7, 15)
    r.read(PROFILE_URL, PROFILE_PAGE)
    r.read(FILED_URL, filed_page(ack=ACK_A))                       # a UIA row: not counted
    assert stats.ocr_only_rows(today) == 0 and told == []
    r.read(FILED_URL, filed_page(ack=ACK_B), source="ocr")         # a row only OCR saw
    r.read(FILED_URL, filed_page(ack=ACK_B), source="ocr")         # the same row again: still one
    assert stats.ocr_only_rows(today, ITR) == 1 and told == [1]


def test_uia_confirmation_upgrades_the_ocr_mark(tmp_path):
    r = AbsorbRig(tmp_path)
    r.read(PROFILE_URL, PROFILE_PAGE)
    r.read(FILED_URL, filed_page(ack=ACK_A), source="ocr")
    r.rows()
    r.read(FILED_URL, filed_page(ack=ACK_A))
    row = [x for x in r.rows() if x["arn"] == ACK_A][-1]
    src = row["raw_payload"]["source"]
    assert (src["evidence_source"], src["first_seen_as"]) == ("uia", "ocr")


def test_ocr_never_asks_for_a_demotion(tmp_path):
    asked = []
    r = AbsorbRig(tmp_path)
    r.sgt._ask_demotion = asked.append
    r.read(PROFILE_URL, PROFILE_PAGE)
    r.read(FILED_URL, filed_page("Processed with refund", ack=ACK_A))
    r.rows()
    r.read(FILED_URL, filed_page("Pending for e-verification", ack=ACK_A), source="ocr")
    assert asked == [] and r.rows() == []
    assert not [e for e in r.events("dataset") if "demotion" in str(e.get("change"))]
    assert [sl.values["status"] for sl in r.session().slots] == ["Submitted & Verified"]


def test_ocr_status_wording_without_an_arn_never_promotes(tmp_path):
    """R6 (the 2026-10-06 '11 Verified without ARN' rows): wording or a tile alone, from OCR, never
    creates a row or raises one; the same page read by UIA does (so the page is not simply unread)."""
    message = ["e-Verify Return", "Return e-Verified successfully", "Thank you"]     # no ack on it
    r = AbsorbRig(tmp_path)
    r.read(PROFILE_URL, PROFILE_PAGE)
    r.read(EVERIFY_URL, message, source="ocr")
    r.read(FILED_URL, filed_page("Pending for e-verification", ack=ACK_A)[:-2], source="ocr")   # a card, no ack
    assert r.session().slots == [] and r.rows() == []             # OCR made no row without an ack
    r.read(FILED_URL, filed_page("Pending for e-verification", ack=ACK_A))
    r.rows()
    r.read(EVERIFY_URL, message, source="ocr", advance=1)          # the message alone, from OCR
    assert r.rows() == []
    assert [sl.values["status"] for sl in r.session().slots] == ["Submitted (Not Verified)"]
    r.read(EVERIFY_URL, message, advance=1)                        # control: from UIA it is tied and promotes
    assert [sl.values["status"] for sl in r.session().slots] == ["Submitted & Verified"]


# ── Blind pages (6.2) ──────────────────────────────────────────────────────────────────────────

def test_blind_after_a_good_read_retries_before_ocr(tmp_path):
    r = AbsorbRig(tmp_path)
    ocr = MagicMock()
    ocr.scan_image.return_value = {"lines": PROFILE_PAGE}
    r.read(PROFILE_URL, PROFILE_PAGE)
    for n in range(BLIND_RETRIES):
        assert r.read(PROFILE_URL, ["Enable accessibility"], frame=frame(1), ocr=ocr, blind_retries=n) == BLIND_RETRY
    assert ocr.scan_image.call_count == 0
    r.read(PROFILE_URL, ["Enable accessibility"], frame=frame(1), ocr=ocr, blind_retries=BLIND_RETRIES)
    assert ocr.scan_image.call_count == 1
    # A page never read fine goes to OCR at once.
    r.read(FILED_URL, [], frame=frame(2), ocr=ocr, blind_retries=0)
    assert ocr.scan_image.call_count == 2


def test_gst_blind_page_now_goes_to_ocr(tmp_path):
    r = AbsorbRig(tmp_path)
    ocr = MagicMock()
    ocr.scan_image.return_value = {"lines": ["Dashboard", GSTIN, "x"]}
    r.read(R3B, ["Enable accessibility"], portal=GST, frame=frame(3), ocr=ocr)
    assert ocr.scan_image.call_count == 1
    assert r.session().profile == {}               # and it named nobody (OCR never sets the client)
    assert r.sgt.take_diag()["ocr"] == 1
