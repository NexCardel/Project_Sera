"""
The six day profiles of the stress report (section 5J), written as scripted browsers. Each builder returns a
Profile: the browser to run, the submissions that must become tracker rows, how long the day is, and the
moments the app itself dies. Parameters scale a profile (minutes of invoice entry, how slow a page is, how
long a toast lives) so the same day can be made harder for a later work package without a new profile.
Fictional data only.
"""
import random
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .fake_browser import OUTSIDE, PORTAL_GST, PORTAL_ITR, Browser, Page, Tab, Toast, Window
from .harness import Harness
from .pages import (GST_RET, ITR, Client, Submission, gst_filing, gst_header, itr_filing, make_client,
                    make_clients)

SAVED = Toast(["Saved successfully"], 2.0, 8)
EXCEL, BANK = "https://sheets.example.test/book", "https://bank.example.test/pay"


@dataclass
class Profile:
    name: str
    browser: Browser
    expected: List[Submission]
    duration: float
    keep_state: bool = False
    idle_step: float = 1.0
    restarts: List[float] = field(default_factory=list)       # virtual seconds at which the Sera app dies


def play(profile: Profile, sim, *, real: bool = False) -> Harness:
    h = sim(profile.browser, keep_state=profile.keep_state, idle_step=profile.idle_step, real=real)
    for at in sorted(profile.restarts):
        h.run(at)
        h.restart_app()
    h.run(profile.duration)
    h.end_day()
    return h


def _away(browser: Browser, window: Window, t: float, sec: float) -> float:
    """The user is in another program (Excel, Tally) for `sec`."""
    browser.focus(t, None)
    browser.focus(t + sec, window)
    return t + sec


def _start(browser: Browser, n_tabs: int = 1, t: float = 0.0) -> Tuple[Window, Tab]:
    w = browser.window(tabs=n_tabs, opened=0.0)
    browser.focus(t, w)
    return w, w.tabs[0]


# ---- 1. B2B marathon -----------------------------------------------------------------------------------------

def b2b_marathon(minutes: float = 30.0, *, read_ms: float = 40.0, b2b_read_ms: Optional[float] = None,
                 arn_toast_sec: Optional[float] = None, recipients: int = 200, seed: int = 1) -> Profile:
    """One GST client, GSTR-1 invoice entry for `minutes`, a save every 30-90 s, Excel every few seconds, then
    summary, preview and the filing. b2b_read_ms is how long the heavy invoice page takes to read."""
    rng = random.Random(seed)
    b = Browser()
    w, tab = _start(b)
    client = make_client(1)
    heavy = b2b_read_ms if b2b_read_ms is not None else read_ms
    header = gst_header(client)
    others = [make_client(5000 + k) for k in range(recipients)]
    table = [f" {c.name} {c.gstin}" for c in others]

    def work(tab: Tab, t: float) -> float:
        end, n = t + minutes * 60.0, 0
        while t < end:
            n += 1
            cycle = rng.uniform(30.0, 90.0)
            tab.navigate(t, Page(GST_RET + "gstr1/b2b", ["Details of outward supplies", header, f"Invoice {n}"] + table,
                                 PORTAL_GST, read_ms=heavy))
            tab.typing(t + 1.0, t + cycle - 8.0)
            k = t + 6.0
            while k < t + cycle - 14.0:
                k = _away(b, w, k, rng.uniform(2.0, 5.0)) + rng.uniform(4.0, 9.0)
            tab.toast(t + cycle - 4.0, SAVED)
            t += cycle
        tab.navigate(t, Page(GST_RET + "gstr1/summary", ["Summary", header, "Total invoices", str(n)], PORTAL_GST,
                             read_ms=read_ms))
        t += 8.0
        tab.navigate(t, Page(GST_RET + "gstr1/preview", ["Preview", header, "Generate"], PORTAL_GST, read_ms=read_ms))
        return t + 8.0

    t, sub = gst_filing(tab, 2.0, client, 1, form="GSTR1", work=work, read_ms=read_ms, arn_toast_sec=arn_toast_sec)
    return Profile("profile 1: B2B marathon", b, [sub], t + 30.0)


# ---- 2. Due-date rush ----------------------------------------------------------------------------------------

def due_date_rush(clients: int = 20, *, ack_visible: float = 10.0, seed: int = 2) -> Profile:
    """GSTR-3B for client after client in one tab, the portal slow and now and then erroring."""
    rng = random.Random(seed)
    b = Browser()
    _w, tab = _start(b)
    t, expected = 3.0, []
    for i, c in enumerate(make_clients(clients)):
        slow = rng.choice([200.0, 600.0, 1500.0])

        def work(tab: Tab, t: float, i=i, c=c, slow=slow) -> float:
            if i % 4 == 1:
                tab.navigate(t, Page(GST_RET + "gstr3b", ["Service Unavailable", "Please try again later"], PORTAL_GST,
                                     read_ms=slow))
                t += 6.0
            for step in ("payment", "offset"):
                tab.navigate(t, Page(GST_RET + step, ["Payment of tax", gst_header(c), step], PORTAL_GST, read_ms=slow))
                tab.typing(t + 1.0, t + 9.0)
                t += rng.uniform(10.0, 25.0)
            return t

        t, sub = gst_filing(tab, t, c, i + 1, form="GSTR3B", month=i, ack_visible=ack_visible, read_ms=slow,
                            success_read_ms=slow, work=work)
        expected.append(sub)
        t += rng.uniform(1.0, 6.0)
    return Profile("profile 2: due-date rush", b, expected, t + 30.0)


# ---- 3. Parallel filing --------------------------------------------------------------------------------------

def parallel_filing(clients: int = 3, *, background_success: bool = False, ack_visible: float = 10.0) -> Profile:
    """Several windows, a different client in each, the user alternating while the portals process.
    background_success: each success appears in a window the user has left and is replaced (by the portal's
    own logout) before they come back; today only the front window is watched."""
    b = Browser()
    windows = [b.window(tabs=1, opened=0.0) for _ in range(clients)]
    expected, spans, t_end = [], [], 0.0
    for k, (w, c) in enumerate(zip(windows, make_clients(clients))):
        t, sub = gst_filing(w.tabs[0], 2.0 + 37.0 * k, c, k + 1, form="GSTR3B", month=k, ack_visible=ack_visible,
                            dwell=7.0)
        expected.append(sub)
        spans.append((sub.visible_from - 1.0, sub.visible_until + 0.5))
        t_end = max(t_end, t)
    times = sorted({9.0 * k for k in range(int(t_end / 9.0) + 2)} | {a for a, _z in spans} | {z for _a, z in spans})
    front = None
    for k, t in enumerate(times):
        pick = windows[k % clients]
        for j, (a, z) in enumerate(spans):
            if a <= t < z:
                pick = windows[(j + 1) % clients] if background_success else windows[j]
        if pick is not front:
            b.focus(t, pick)
            front = pick
    return Profile("profile 3: parallel filing" + (" (background success)" if background_success else ""), b,
                   expected, t_end + 40.0)


# ---- 4. ITR season -------------------------------------------------------------------------------------------

def itr_season(clients: int = 8, *, seed: int = 3) -> Profile:
    """Many ITRs: long schedule editing, then e-verify by OTP, by a net-banking redirect, or later on the
    client's own phone (the filing number alone)."""
    rng = random.Random(seed)
    b = Browser()
    _w, tab = _start(b)
    t, expected = 3.0, []
    for i, c in enumerate(make_clients(clients, first=100)):
        def work(tab: Tab, t: float, i=i) -> float:
            for s in range(4):
                tab.navigate(t, Page(ITR + f"foreturns-ay26/fo-itr4-ay2026/schedule{s}",
                                     ["Schedule", "Income details", f"Part {s}"], PORTAL_ITR, read_ms=120.0))
                tab.typing(t + 1.0, t + 40.0)
                tab.toast(t + 45.0, SAVED)
                t += rng.uniform(45.0, 70.0)
            return t

        how = i % 3
        t, sub = itr_filing(tab, t, c, i + 1, work=work, logout=(how != 1), verify_visible=6.0 if how == 0 else 0.0)
        if how == 1:
            tab.navigate(t, Page(BANK, ["Net banking", "Confirm payment"], OUTSIDE))
            t += 15.0
            tab.navigate(t, Page(ITR + "dashboard/itrStatus", ["e-Verification", "Return e-verified"], PORTAL_ITR))
            t += 6.0
            tab.navigate(t, Page(ITR + "logout", ["Logged out", "You have been logged out successfully"], PORTAL_ITR))
            t += 2.0
        expected.append(sub)
        t += rng.uniform(1.0, 8.0)
    return Profile("profile 4: ITR season", b, expected, t + 30.0)


# ---- 5. All-day PC -------------------------------------------------------------------------------------------

def all_day_pc(hours: float = 12.0, filings: int = 6, tabs: int = 30, *, seed: int = 4) -> Profile:
    """One window of `tabs` tabs for the whole day; a filing in one of two portal tabs now and then, the
    user flicking through the other tabs in between."""
    rng = random.Random(seed)
    b = Browser()
    w = b.window(tabs=tabs, opened=0.0)
    b.focus(0.0, w)
    for i, tab in enumerate(w.tabs):
        tab.navigate(0.0, Page(f"https://tab{i}.example.test/", [f"Document {i}", "text"], OUTSIDE))
    itr_tab, gst_tab = w.tabs[0], w.tabs[1]
    day, expected = hours * 3600.0, []
    gap = day / (filings + 1)
    t = 0.0
    for k, c in enumerate(make_clients(filings, first=300)):
        stop = (k + 1) * gap
        while t < stop - 120.0:
            t += rng.uniform(120.0, 600.0)
            w.select(min(t, stop - 60.0), w.tabs[rng.randrange(2, tabs)])
            t = min(t, stop - 60.0)
        tab = itr_tab if k % 2 == 0 else gst_tab
        w.select(t + 1.0, tab)
        t += 2.0
        t, sub = (itr_filing if k % 2 == 0 else gst_filing)(tab, t, c, k + 1)
        expected.append(sub)
        w.select(t + 1.0, w.tabs[2])
        t += 2.0
    return Profile("profile 5: all-day PC", b, expected, day, idle_step=5.0)


# ---- 6. Interrupted day --------------------------------------------------------------------------------------

def interrupted_day() -> Profile:
    """A browser crash in the middle of a filing (refiled after the relaunch), the app dying in the middle of
    another, and a power cut just after a third."""
    b = Browser()
    w1, tab = _start(b)
    c = make_clients(6, first=500)
    expected, restarts = [], []

    t, s = itr_filing(tab, 3.0, c[0], 1)
    expected.append(s)

    def crash(tab: Tab, t: float) -> float:
        b.crash(t + 6.0)
        return t + 12.0

    t, _lost = gst_filing(tab, t + 5.0, c[1], 2, work=crash)             # its success is never on screen
    w2 = b.window(tabs=1, opened=t + 30.0)
    b.focus(t + 30.0, w2)
    tab = w2.tabs[0]
    t, s = gst_filing(tab, t + 31.0, c[1], 3)
    expected.append(s)

    def die(tab: Tab, t: float) -> float:
        restarts.append(t + 6.0)
        return t + 12.0

    t, s = gst_filing(tab, t + 5.0, c[2], 4, month=1, work=die)
    expected.append(s)

    t, s = itr_filing(tab, t + 5.0, c[3], 5)
    expected.append(s)
    restarts.append(s.visible_until + 2.0)                                 # the power goes just after the ack

    t, s = itr_filing(tab, t + 5.0, c[4], 6)
    expected.append(s)
    return Profile("profile 6: interrupted day", b, expected, t + 30.0, keep_state=True, restarts=restarts)
