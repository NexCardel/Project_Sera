"""
OS Assist responsiveness pass (2026-11-09): window events in place of fixed sleeps and polls.
core/os_assist/win_events + the dock card, launcher, SMTI Flutter steps, SCA engine and the
found-box cache. Fakes stand in for the hub; one smoke test hooks for real on Windows.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import threading
import time
from concurrent.futures import Future

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from core.os_assist import launcher as la
from core.os_assist import locate, sca_engine, win_events as we
from ui.os_assist import dock_card as dc
from ui.os_assist import smti_card as sc

BROWSER = 0x1234
SERVICE = {"id": 7, "name": "GST", "login_page_link": "https://portal.example.test/login",
           "username_selector": "#user", "password_selector": "#pass"}
TRACES = {"id": 9, "name": "TRACES", "login_page_link": "https://www.tdscpc.gov.in/app/login.xhtml"}
FAKE_USER = "FICTUSER01"
FAKE_PASSWORD = "Fict-ional#1"
PID = 4242


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


class FakeHub:
    """Records watches; fire() calls the callbacks as the hub thread would."""

    def __init__(self):
        self.subs = {}
        self.unwatched = []
        self._n = 0

    def watch(self, ranges, pid, cb):
        self._n += 1
        self.subs[self._n] = (list(ranges), pid, cb)
        return self._n

    def unwatch(self, token):
        self.unwatched.append(token)
        self.subs.pop(token, None)

    def fire(self, event, hwnd=BROWSER, id_object=0, id_child=0, pid=None):
        for ranges, sub_pid, cb in list(self.subs.values()):
            if pid is not None and sub_pid not in (0, pid):
                continue
            if any(lo <= event <= hi for lo, hi in ranges):
                cb(event, hwnd, id_object, id_child)

    def fire_from_thread(self, *a, **kw):
        t = threading.Thread(target=self.fire, args=a, kwargs=kw)
        t.start()
        t.join()


@pytest.fixture
def pid_known(monkeypatch):
    monkeypatch.setattr(we, "window_pid", lambda hwnd: PID if hwnd == BROWSER else 0)
    monkeypatch.setattr(we, "root_of", lambda hwnd: BROWSER if hwnd in (BROWSER, 0x5678) else hwnd)


# ----------------------------------------------------------------------------- WindowWatch
def test_window_watch_routes_only_this_windows_own_events(pid_known):
    hub, seen = FakeHub(), []
    w = we.WindowWatch(BROWSER, on_move=lambda: seen.append("move"), on_title=lambda: seen.append("title"),
                       on_focus=lambda: seen.append("focus"), hub=hub)
    assert w.active and list(hub.subs.values())[0][1] == PID
    hub.fire(we.EVENT_OBJECT_LOCATIONCHANGE)
    hub.fire(we.EVENT_OBJECT_LOCATIONCHANGE, id_object=-9)         # a caret / page node: not the window
    hub.fire(we.EVENT_OBJECT_LOCATIONCHANGE, hwnd=0x9999)          # another window of the browser
    hub.fire(we.EVENT_OBJECT_NAMECHANGE)
    hub.fire(we.EVENT_OBJECT_NAMECHANGE, id_object=-4, id_child=12)  # a page node's name
    hub.fire(we.EVENT_SYSTEM_MINIMIZESTART)
    hub.fire(we.EVENT_OBJECT_DESTROY)
    hub.fire(we.EVENT_OBJECT_FOCUS, hwnd=0x5678, id_object=-4, id_child=-33)   # a box inside it
    hub.fire(we.EVENT_OBJECT_FOCUS, hwnd=0x9999)                    # focus in another window
    assert seen == ["move", "title", "move", "move", "focus"]
    w.close()
    assert not w.active and hub.unwatched == [1]


def test_window_watch_without_a_pid_hooks_nothing(monkeypatch):
    monkeypatch.setattr(we, "window_pid", lambda hwnd: 0)
    hub = FakeHub()
    w = we.WindowWatch(BROWSER, on_move=lambda: None, hub=hub)
    assert not w.active and hub.subs == {}


def test_focus_hook_only_when_asked(pid_known):
    hub = FakeHub()
    we.WindowWatch(BROWSER, on_move=lambda: None, hub=hub)
    ranges = list(hub.subs.values())[0][0]
    assert (we.EVENT_OBJECT_FOCUS, we.EVENT_OBJECT_FOCUS) not in ranges


# ----------------------------------------------------------------------------- dock card
class FakeWin:
    def __init__(self):
        self.rect = (100, 50, 1300, 850)
        self.iconic = False
        self.alive = True

    def is_window(self, hwnd):
        return self.alive and hwnd == BROWSER

    def is_iconic(self, hwnd):
        return self.iconic

    def window_rect(self, hwnd):
        return self.rect

    def add_ex_style(self, hwnd, bits):
        pass

    def set_owner(self, hwnd, owner):
        pass


class FutureLocator:
    """Answers each probe with a Future the test resolves (as the worker would, off the Qt thread)."""

    def __init__(self):
        self.jobs = []
        self.host_ok = True

    def probe(self, hwnd, service):
        f = Future()
        self.jobs.append(f)
        return f

    def answer(self, host_ok=True, page=(108, 130, 1292, 842)):
        f = self.jobs[-1]
        threading.Thread(target=lambda: f.set_result((host_ok, page))).start()


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Card(dc.DockCard):
    def _native_hwnd(self):
        return 0xBEEF


def make_card(hub, locator=None):
    win, loc, clock = FakeWin(), locator or FutureLocator(), Clock()
    card = Card(timeout_ms=60000, locator=loc, win=win, clock=clock, events=hub)
    card.attach(BROWSER, SERVICE)
    return card, win, loc, clock


def test_card_follows_a_move_event_at_once(app, pid_known):
    hub = FakeHub()
    card, win, loc, _ = make_card(hub)
    card.present()
    loc.answer()
    wait(30)
    assert card.events_active and card._tick.interval() == dc.TICK_EVENTS_MS
    before = card.pos()
    win.rect = (400, 300, 1600, 1100)
    hub.fire_from_thread(we.EVENT_OBJECT_LOCATIONCHANGE)
    wait(20)                                          # far less than a tick
    assert card.pos().x() - before.x() == dc.to_logical(1600, 0).x() - dc.to_logical(1300, 0).x()
    card.dismiss()


def test_drag_events_coalesce_into_one_follow(app, pid_known, monkeypatch):
    hub = FakeHub()
    card, win, loc, _ = make_card(hub)
    card.present()
    calls = []
    monkeypatch.setattr(card, "_follow", lambda: calls.append(1))
    for _ in range(50):
        hub.fire(we.EVENT_OBJECT_LOCATIONCHANGE)
    wait(20)
    assert len(calls) == 1
    card.dismiss()


def test_title_change_probes_at_once_and_hides_on_another_host(app, pid_known):
    hub = FakeHub()
    card, win, loc, clock = make_card(hub)
    card.present()
    loc.answer()
    wait(30)
    assert card.isVisible() and len(loc.jobs) == 1
    clock.t += 0.05                                   # well inside PROBE_EVERY_S
    hub.fire_from_thread(we.EVENT_OBJECT_NAMECHANGE)  # tab switch
    wait(20)
    assert len(loc.jobs) == 2                         # asked straight away
    loc.answer(host_ok=False)
    wait(30)                                          # applied when in, no tick needed
    assert card.hidden_reason == "host" and not card.isVisible()
    clock.t += 0.05
    hub.fire_from_thread(we.EVENT_OBJECT_NAMECHANGE)  # back to the portal tab
    wait(20)
    loc.answer(host_ok=True)
    wait(30)
    assert card.hidden_reason == "" and card.isVisible()
    card.dismiss()


def test_minimise_event_hides_and_restore_shows(app, pid_known):
    hub = FakeHub()
    card, win, loc, _ = make_card(hub)
    card.present()
    loc.answer()
    wait(30)
    win.iconic = True
    hub.fire_from_thread(we.EVENT_SYSTEM_MINIMIZESTART)
    wait(20)
    assert card.hidden_reason == "minimised"
    win.iconic = False
    hub.fire_from_thread(we.EVENT_SYSTEM_MINIMIZEEND)
    wait(20)
    assert card.hidden_reason == ""
    card.dismiss()


def test_close_event_dismisses_and_dismiss_unhooks(app, pid_known):
    hub = FakeHub()
    card, win, loc, _ = make_card(hub)
    reasons = []
    card.dismissed.connect(reasons.append)
    card.present()
    win.alive = False
    hub.fire_from_thread(we.EVENT_OBJECT_DESTROY)
    wait(20)
    assert reasons == ["browser_closed"] and hub.subs == {} and hub.unwatched


def test_no_hooks_keeps_the_old_tick(app, monkeypatch):
    monkeypatch.setattr(we, "window_pid", lambda hwnd: 0)
    card, *_ = make_card(FakeHub())
    card.present()
    assert not card.events_active and card._tick.interval() == dc.TICK_MS
    card.dismiss()


# ----------------------------------------------------------------------------- SMTI card
class FakeFiller:
    def __init__(self):
        self.fills, self.focus, self.warms = [], [], []
        self.focus_answers = []

    def fill(self, hwnd, service, kind, text, user_sel, pass_sel):
        self.fills.append(kind)
        return "filled"

    def warm(self, hwnd, user_sel, pass_sel):
        self.warms.append((hwnd, user_sel, pass_sel))
        return True

    def edit_count(self, hwnd):
        return 2

    def focus_step(self, hwnd, service, text, is_password):
        self.focus.append(is_password)
        return self.focus_answers.pop(0) if self.focus_answers else None


class SyncLocator:
    def probe(self, hwnd, service):
        return (True, (108, 130, 1292, 842))


class Smti(sc.SmtiCard):
    def _native_hwnd(self):
        return 0xBEEF


def make_smti(hub, service=SERVICE, flutter=None):
    filler = FakeFiller()
    card = Smti(service, FAKE_USER, FAKE_PASSWORD, client_id=42, client_name="Fictional Traders",
                flutter=flutter, filler=filler, copier=lambda *a: None, sck_handler=lambda m: None,
                locator=SyncLocator(), win=FakeWin(), clock=Clock(), events=hub)
    card.attach(BROWSER, service)
    return card, filler


def test_smti_warms_the_boxes_when_it_shows(app, pid_known):
    card, filler = make_smti(FakeHub())
    card.present()
    assert filler.warms == [(BROWSER, "#user", "#pass")]
    card.dismiss()


def test_flutter_focus_event_types_without_waiting_for_the_poll(app, pid_known):
    hub = FakeHub()
    card, filler = make_smti(hub, service=TRACES)
    card.present()
    assert card.flutter and filler.warms == []
    assert card._focus_poll.interval() == sc.FOCUS_POLL_EVENTS_MS
    filler.focus_answers = ["filled"]
    hub.fire_from_thread(we.EVENT_OBJECT_FOCUS, hwnd=0x5678, id_object=-4, id_child=-7)
    wait(30)                                          # far less than the 400 ms safety poll
    assert filler.focus[:1] == [False] and card._step == 1
    card.dismiss()


def test_focus_event_during_a_focus_job_looks_again_after_it(app, pid_known):
    hub = FakeHub()
    card, filler = make_smti(hub, service=TRACES)
    card.present()
    pending = Future()
    filler.focus_step = lambda *a: (filler.focus.append(a[3]), pending)[1]
    hub.fire_from_thread(we.EVENT_OBJECT_FOCUS, hwnd=0x5678)
    wait(10)
    assert card._focus_job is pending
    hub.fire_from_thread(we.EVENT_OBJECT_FOCUS, hwnd=0x5678)
    wait(10)
    assert card._focus_again
    n = len(filler.focus)
    filler.focus_step = lambda *a: (filler.focus.append(a[3]), "filled")[1]
    pending.set_result(None)
    wait(30)
    assert len(filler.focus) > n and card._step == 1
    card.dismiss()


# ----------------------------------------------------------------------------- launcher
def test_launch_looks_as_soon_as_a_browser_event_comes(app, monkeypatch):
    monkeypatch.setattr(locate, "browser_windows", lambda: [BROWSER])
    monkeypatch.setattr(we, "window_pid", lambda hwnd: PID)
    hub = FakeHub()
    looks, shown = [], []
    t0 = time.monotonic()

    def opener(url, browser):
        threading.Timer(0.05, lambda: hub.fire(we.EVENT_OBJECT_NAMECHANGE, pid=PID)).start()

    def finder(url):
        looks.append(time.monotonic() - t0)
        return BROWSER

    lc = la.Launcher(opener=opener, finder=finder, events=hub, spawn=lambda fn: fn(),
                     card_factory=lambda *a: type("C", (), {
                         "attach": lambda s, h, sv: None, "present": lambda s: shown.append(1),
                         "dismissed": type("S", (), {"connect": lambda s, f: None})(),
                         "is_dismissed": False, "dismiss": lambda s, r="": None})())
    lc.launch("smti", SERVICE, FAKE_USER, FAKE_PASSWORD)
    assert looks and looks[0] < 0.5                   # not FIRST_LOOK_S (1.0 s)
    assert shown == [1]
    assert hub.subs == {}                             # unhooked after the wait


def test_launch_without_events_still_looks_after_the_longest_wait(app, monkeypatch):
    monkeypatch.setattr(locate, "browser_windows", lambda: [])
    hub, looks = FakeHub(), []
    t0 = time.monotonic()
    lc = la.Launcher(opener=lambda u, b: None, finder=lambda url: looks.append(time.monotonic() - t0) or BROWSER,
                     events=hub, spawn=lambda fn: fn(),
                     card_factory=lambda *a: type("C", (), {
                         "attach": lambda s, h, sv: None, "present": lambda s: None,
                         "dismissed": type("S", (), {"connect": lambda s, f: None})(),
                         "is_dismissed": False, "dismiss": lambda s, r="": None})())
    lc.launch("smti", SERVICE, FAKE_USER, FAKE_PASSWORD)
    assert looks and la.FIRST_LOOK_S - 0.05 <= looks[0] < la.FIRST_LOOK_S + 0.5


def test_wake_ignores_page_node_title_changes():
    w = la._Wake(None)
    w._event(we.EVENT_OBJECT_NAMECHANGE, BROWSER, -4, 12)
    assert not w.event.is_set()
    w._event(we.EVENT_OBJECT_NAMECHANGE, BROWSER, 0, 0)
    assert w.event.is_set()


# ----------------------------------------------------------------------------- SCA engine
PAN = "ABCDE1234F"
GST_URL = "https://services.gst.gov.in/services/login"


def make_arm(now):
    svc = {"service_id": 1, "name": "GST", "url": GST_URL, "host": "services.gst.gov.in",
           "password_selector": "", "has_password": True, "blocked": "", "needs_confirm": False}
    return {"schema": 2, "arm_id": "arm_ev1", "client_id": 7, "client_id_token": "7", "matched_uid": PAN,
            "candidate_uids": [PAN], "services": [svc], "business_name": "Fictional Traders",
            "owner_name": "A. Person", "sca_mode": "autofill", "max_uses": 1, "trigger": "clipboard",
            "uid_page": "", "created_at": now, "expires_at": now + 300000, "state": "ARMED"}


class Watcher:
    enabled = True


def make_engine(hub, monkeypatch):
    monkeypatch.setattr(locate, "_is_browser", lambda h: True)
    monkeypatch.setattr(we, "window_pid", lambda hwnd: PID)
    probes, futures = [], []

    def submit(fn, *a):
        f = Future()
        futures.append(f)
        probes.append(fn)
        return f

    eng = sca_engine.ScaEngine(Watcher(), submit=submit, probe=lambda *a: None, locked=lambda: set(),
                               card_factory=lambda *a: None, chip_factory=lambda *a: None,
                               now=lambda: 1_000_000.0, events=hub)
    return eng, probes, futures


def test_sca_hooks_only_while_armed(app, monkeypatch):
    hub = FakeHub()
    eng, probes, _ = make_engine(hub, monkeypatch)
    assert hub.subs == {}
    assert eng.arm(make_arm(1_000_000.0))
    pids = sorted(pid for _r, pid, _cb in hub.subs.values())
    assert pids == [0, PID]                           # foreground (all) + the browser in front
    eng.disarm()
    assert hub.subs == {}


def test_sca_value_change_probes_at_once_and_again_after_a_running_probe(app, monkeypatch):
    hub = FakeHub()
    eng, probes, futures = make_engine(hub, monkeypatch)
    eng.arm(make_arm(1_000_000.0))
    hub.fire_from_thread(we.EVENT_OBJECT_VALUECHANGE, hwnd=BROWSER, id_object=-4, id_child=-21, pid=PID)
    wait(20)
    assert len(probes) == 1                           # not waiting for the 0.6 s tick
    for _ in range(5):                                # more typing while it runs
        hub.fire_from_thread(we.EVENT_OBJECT_VALUECHANGE, pid=PID)
    wait(20)
    assert len(probes) == 1 and eng._again
    futures[0].set_result(None)
    wait(30)
    assert len(probes) == 2                           # one more look at the final value
    eng.disarm()


def test_sca_foreground_change_rehooks_the_new_browser(app, monkeypatch):
    hub = FakeHub()
    eng, probes, futures = make_engine(hub, monkeypatch)
    eng.arm(make_arm(1_000_000.0))
    monkeypatch.setattr(we, "window_pid", lambda hwnd: PID + 1)
    hub.fire_from_thread(we.EVENT_SYSTEM_FOREGROUND, pid=0)
    wait(20)
    assert sorted(pid for _r, pid, _cb in hub.subs.values()) == [0, PID + 1]
    eng.disarm()


# ----------------------------------------------------------------------------- found-box cache
class El:
    def __init__(self, name):
        self.name = name
        self.alive = True


def test_find_fields_cached_reuses_live_boxes_and_refinds_dead_ones(monkeypatch):
    finds = []
    user, pw = El("u"), El("p")
    monkeypatch.setattr(locate, "find_fields", lambda h, us, ps: finds.append(h) or (user, pw))
    monkeypatch.setattr(locate, "describe", lambda el: {"alive": el.alive})
    monkeypatch.setattr(locate, "visible", lambda d: d["alive"])
    locate.forget_fields()
    assert locate.warm_fields(BROWSER, "#user", "#pass")
    assert locate.find_fields_cached(BROWSER, "#user", "#pass", want="pass") == (user, pw)
    assert finds == [BROWSER]                         # the press did not walk the page again
    pw.alive = False                                  # the page changed under it
    locate.find_fields_cached(BROWSER, "#user", "#pass", want="pass")
    assert finds == [BROWSER, BROWSER]
    locate.forget_fields()


def test_find_fields_cached_refinds_a_box_that_was_missing(monkeypatch):
    finds = []
    answers = [(El("u"), None), (El("u"), El("p"))]   # PAN page first, then the password page
    monkeypatch.setattr(locate, "find_fields", lambda h, us, ps: finds.append(h) or answers.pop(0))
    monkeypatch.setattr(locate, "describe", lambda el: {"alive": True})
    monkeypatch.setattr(locate, "visible", lambda d: True)
    locate.forget_fields()
    locate.warm_fields(BROWSER)
    _u, p = locate.find_fields_cached(BROWSER, want="pass")
    assert p is not None and len(finds) == 2
    locate.forget_fields()


# ----------------------------------------------------------------------------- real hub
@pytest.mark.skipif(sys.platform != "win32", reason="WinEvent hooks are Windows only")
def test_real_hub_hooks_and_unhooks():
    hub = we.Hub()
    try:
        token = hub.watch([(we.EVENT_SYSTEM_FOREGROUND, we.EVENT_SYSTEM_FOREGROUND)], 0, lambda *a: None)
        assert token is not None
        assert hub._tid
        hub.unwatch(token)
    finally:
        hub.stop()
