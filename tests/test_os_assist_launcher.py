"""core/os_assist/launcher.py and the engine routing in automation.trigger_manual_assist / trigger_mecp."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
from unittest import mock

import pytest
from PySide6.QtCore import QEventLoop, QTimer, QThread
from PySide6.QtWidgets import QApplication

import automation
from core.os_assist import launcher as la
from ui.os_assist import mecp_card as mc
from ui.os_assist import smti_card as sc

BROWSER = 0x1234
SERVICE = {"id": 7, "name": "GST", "login_page_link": "https://portal.example.test/login",
           "username_selector": "#user", "password_selector": "#pass", "_client_name": "Fictional Traders"}
FAKE_USER = "FICTUSER01"
FAKE_PASSWORD = "Fict-ional#1"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeWin:
    def is_window(self, hwnd):
        return hwnd == BROWSER

    def is_iconic(self, hwnd):
        return False

    def window_rect(self, hwnd):
        return (100, 50, 1300, 850)

    def add_ex_style(self, hwnd, bits):
        pass

    def set_owner(self, hwnd, owner):
        pass


class FakeLocator:
    def probe(self, hwnd, service):
        return (True, (108, 130, 1292, 842))


class Smti(sc.SmtiCard):
    def _native_hwnd(self):
        return 0xBEEF


class Mecp(mc.MecpCard):
    def _native_hwnd(self):
        return 0xBEEF


class FakeFiller:
    def edit_count(self, hwnd):
        return 2


def card_factory(made):
    def make(kind, service, user_id, password, client_id, client_name, clear_s):
        kw = dict(client_id=client_id, client_name=client_name, clear_seconds=clear_s,
                  locator=FakeLocator(), win=FakeWin(), copier=lambda *a: None, sck_handler=lambda d: None)
        card = (Smti(service, user_id, password, filler=FakeFiller(), **kw) if kind == "smti"
                else Mecp(service, user_id, password, **kw))
        made.append((kind, card, client_name, clear_s))
        return card
    return make


def make_launcher(found=BROWSER, made=None):
    clock, opened, looks = Clock(), [], []

    def finder(url):
        looks.append(url)
        return found(len(looks)) if callable(found) else found

    made = [] if made is None else made
    lc = la.Launcher(opener=lambda url, b: opened.append((url, b)), finder=finder,
                     card_factory=card_factory(made), sleep=clock.sleep, clock=clock,
                     spawn=lambda fn: fn())
    return lc, opened, looks, made, clock


def test_opens_the_login_page_and_docks_the_card_to_the_found_window(app):
    lc, opened, looks, made, _ = make_launcher()
    lc.launch("smti", SERVICE, FAKE_USER, FAKE_PASSWORD, 42, "Fictional Traders")
    assert opened == [(SERVICE["login_page_link"], None)]
    assert looks == [SERVICE["login_page_link"]]
    kind, card, name, _ = made[0]
    assert kind == "smti" and lc.card("smti") is card and name == "Fictional Traders"
    assert card.browser_hwnd == BROWSER
    assert card.secret("pass") == FAKE_PASSWORD and not card.is_dismissed
    card.dismiss("close")


def test_a_window_already_on_the_login_page_is_reused_without_a_new_tab(app):
    lc, opened, looks, made, clock = make_launcher(found=0)
    asked = []
    lc._reuser = lambda service: asked.append(service) or BROWSER
    lc.launch("smti", SERVICE, FAKE_USER, FAKE_PASSWORD, 42, "Fictional Traders")
    assert asked == [SERVICE] and opened == [] and looks == []
    assert clock.t == 1000.0                          # no wait for a tab that was never opened
    card = made[0][1]
    assert card.browser_hwnd == BROWSER and card.secret("pass") == FAKE_PASSWORD
    card.dismiss("close")


def test_no_login_page_window_opens_a_new_tab(app):
    lc, opened, looks, made, _ = make_launcher()
    lc._reuser = lambda service: 0
    lc.launch("mecp", SERVICE, FAKE_USER, FAKE_PASSWORD, 42)
    assert opened == [(SERVICE["login_page_link"], None)] and len(looks) == 1 and made
    lc.dismiss("mecp")


def test_a_failing_reuse_look_opens_a_new_tab(app):
    lc, opened, _, made, _ = make_launcher()
    lc._reuser = mock.Mock(side_effect=OSError("uia gone"))
    lc.launch("smti", SERVICE, FAKE_USER, FAKE_PASSWORD, 42)
    assert len(opened) == 1 and made
    lc.dismiss("smti")


def test_default_reuser_asks_locate_and_brings_the_window_to_the_front(app):
    seen, fronted = [], []

    class Done:
        def __init__(self, value):
            self.value = value

        def result(self, timeout=None):
            return self.value

    def submit(fn, *args):
        seen.append((fn, args))
        return Done(BROWSER)

    with mock.patch.object(la.worker, "submit", submit), \
            mock.patch.object(la, "_bring_to_front", fronted.append):
        assert la._default_reuser({**SERVICE, "browser": "Edge"}) == BROWSER
        assert seen == [(la.locate.login_window, (SERVICE["login_page_link"], "#user", "#pass", "edge"))]
        assert fronted == [BROWSER]
        with mock.patch.object(la.worker, "submit", lambda fn, *a: Done(0)):
            assert la._default_reuser(SERVICE) == 0
        assert fronted == [BROWSER]


def test_a_launcher_with_its_own_finder_never_looks_at_real_windows(app):
    with mock.patch.object(la, "_default_reuser") as real:
        lc, opened, *_ = make_launcher()
        lc.launch("mecp", SERVICE, FAKE_USER, FAKE_PASSWORD, 42)
    real.assert_not_called()
    assert len(opened) == 1
    lc.dismiss("mecp")


def test_service_browser_is_passed_to_the_opener(app):
    lc, opened, *_ = make_launcher()
    lc.launch("mecp", {**SERVICE, "browser": "Edge"}, FAKE_USER, FAKE_PASSWORD, 42)
    assert opened[0][1] == "edge"
    lc.dismiss("mecp")


def test_waits_for_the_window_then_shows(app):
    lc, _, looks, made, _ = make_launcher(found=lambda n: BROWSER if n == 5 else 0)
    lc.launch("mecp", SERVICE, FAKE_USER, FAKE_PASSWORD, 42)
    assert len(looks) == 5 and made and made[0][0] == "mecp"
    lc.dismiss("mecp")


def test_timeout_calls_on_error_with_a_clear_message_and_shows_nothing(app):
    errors = []
    lc, _, looks, made, clock = make_launcher(found=0)
    lc.launch("smti", SERVICE, FAKE_USER, FAKE_PASSWORD, 42, on_error=errors.append)
    assert made == [] and lc.card("smti") is None
    assert errors == [la.timeout_message(SERVICE)]
    assert "GST" in errors[0] and "20 seconds" in errors[0]
    assert FAKE_PASSWORD not in errors[0] and FAKE_USER not in errors[0]
    assert clock.t - 1000.0 >= la.WAIT_S and len(looks) <= la.WAIT_S / la.POLL_S + 2


def test_no_login_link_is_an_error_without_opening(app):
    errors = []
    lc, opened, *_ = make_launcher()
    lc.launch("smti", {"name": "X"}, FAKE_USER, FAKE_PASSWORD, 42, on_error=errors.append)
    assert opened == [] and errors and "X" in errors[0]


def test_unknown_kind_is_refused(app):
    lc, *_ = make_launcher()
    with pytest.raises(ValueError):
        lc.launch("sca", SERVICE, FAKE_USER, FAKE_PASSWORD, 42)


def test_one_card_per_kind_a_new_launch_dismisses_the_old_and_clears_its_secrets(app):
    lc, _, _, made, _ = make_launcher()
    lc.launch("smti", SERVICE, FAKE_USER, FAKE_PASSWORD, 42)
    old = made[0][1]
    reasons = []
    old.dismissed.connect(reasons.append)
    lc.launch("smti", SERVICE, "FICTUSER02", "Other-Fict#2", 43)
    new = made[1][1]
    assert old.is_dismissed and reasons == ["replaced"] and not old.has_secrets()
    assert lc.card("smti") is new and new.secret("user") == "FICTUSER02"
    new.dismiss("close")


def test_kinds_are_independent(app):
    lc, _, _, made, _ = make_launcher()
    lc.launch("smti", SERVICE, FAKE_USER, FAKE_PASSWORD, 42)
    lc.launch("mecp", SERVICE, FAKE_USER, FAKE_PASSWORD, 42)
    smti, mecp = made[0][1], made[1][1]
    assert not smti.is_dismissed and lc.card("smti") is smti and lc.card("mecp") is mecp
    lc.dismiss("smti")
    lc.dismiss("mecp")
    assert smti.is_dismissed and mecp.is_dismissed
    assert not smti.has_secrets() and not mecp.has_secrets()


def test_a_dismissed_card_is_forgotten(app):
    lc, _, _, made, _ = make_launcher()
    lc.launch("mecp", SERVICE, FAKE_USER, FAKE_PASSWORD, 42)
    made[0][1].dismiss("timeout")
    assert lc.card("mecp") is None and not made[0][1].has_secrets()


def test_a_superseded_wait_shows_nothing_and_reports_nothing(app):
    """A launch still waiting when a newer one of its kind starts gives way silently."""
    pending, errors, made = [], [], []
    lc = la.Launcher(opener=lambda u, b: None, finder=lambda url: 0, card_factory=card_factory(made),
                     sleep=lambda s: None, clock=Clock(), spawn=pending.append)
    lc.launch("smti", SERVICE, FAKE_USER, FAKE_PASSWORD, 42, on_error=errors.append)
    first = pending.pop()
    lc._finder = lambda url: BROWSER
    lc.launch("smti", SERVICE, "FICTUSER02", "Other-Fict#2", 43, on_error=errors.append)
    pending.pop()()
    first()                                       # the old wait wakes up late
    assert errors == [] and len(made) == 1 and made[0][1].secret("user") == "FICTUSER02"
    made[0][1].dismiss("close")


def test_attach_failure_dismisses_the_card_and_reports(app):
    made, errors = [], []

    def factory(*a):
        card = card_factory(made)(*a)
        card.attach = mock.Mock(side_effect=OSError("gone"))
        return card

    lc = la.Launcher(opener=lambda u, b: None, finder=lambda url: BROWSER, card_factory=factory,
                     sleep=lambda s: None, clock=Clock(), spawn=lambda fn: fn())
    lc.launch("mecp", SERVICE, FAKE_USER, FAKE_PASSWORD, 42, on_error=errors.append)
    assert errors and made[0][1].is_dismissed and not made[0][1].has_secrets()
    assert lc.card("mecp") is None


def test_card_is_built_on_the_qt_thread_from_a_worker_thread(app):
    made, threads = [], []

    def factory(*a):
        threads.append(QThread.currentThread() is app.thread())
        return card_factory(made)(*a)

    lc = la.Launcher(opener=lambda u, b: None, finder=lambda url: BROWSER, card_factory=factory,
                     sleep=lambda s: None)
    lc.launch("smti", SERVICE, FAKE_USER, FAKE_PASSWORD, 42)
    loop = QEventLoop()
    poll = QTimer()
    poll.timeout.connect(lambda: made and loop.quit())
    poll.start(10)
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    poll.stop()
    assert threads == [True] and lc.card("smti") is made[0][1]
    lc.dismiss("smti")


def test_default_factory_uses_todays_lifetimes(app):
    with mock.patch.object(sc, "SmtiCard") as smti, mock.patch.object(mc, "MecpCard") as mecp:
        la._default_card_factory("smti", SERVICE, FAKE_USER, FAKE_PASSWORD, 42, "F", 45)
        la._default_card_factory("mecp", SERVICE, FAKE_USER, FAKE_PASSWORD, 42, "F", 45)
    assert smti.call_args.kwargs["timeout_ms"] == 30000 == sc.TIMEOUT_MS
    assert mecp.call_args.kwargs["timeout_ms"] == 90000 == mc.TIMEOUT_MS
    assert smti.call_args.kwargs["clear_seconds"] == 45


def test_clear_seconds_comes_from_the_provider_clamped(app):
    try:
        la.set_clear_seconds_provider(lambda: "45")
        assert la.clear_seconds() == 45
        la.set_clear_seconds_provider(lambda: 9999)
        assert la.clear_seconds() == 300
        la.set_clear_seconds_provider(lambda: "bad")
        assert la.clear_seconds() == 30
    finally:
        la.set_clear_seconds_provider(None)
    assert la.clear_seconds() == 30


# -- automation routing --------------------------------------------------------------------------

@pytest.mark.parametrize("trigger,kind,mode", [("trigger_manual_assist", "smti", "manual_assist"),
                                               ("trigger_mecp", "mecp", "mecp")])
def test_os_engine_goes_to_the_launcher(trigger, kind, mode):
    on_error = lambda m: None
    with mock.patch.object(automation, "assist_engine", return_value="os"), \
            mock.patch.object(la, "launch") as launch, \
            mock.patch.object(automation, "_send_to_extension") as ext:
        getattr(automation, trigger)(SERVICE, FAKE_USER, FAKE_PASSWORD, 42, on_error=on_error)
    ext.assert_not_called()
    launch.assert_called_once_with(kind, SERVICE, FAKE_USER, FAKE_PASSWORD, 42, "Fictional Traders", on_error)


@pytest.mark.parametrize("trigger,mode", [("trigger_manual_assist", "manual_assist"), ("trigger_mecp", "mecp")])
def test_extension_engine_is_unchanged(trigger, mode):
    on_error = lambda m: None
    with mock.patch.object(automation, "assist_engine", return_value="extension"), \
            mock.patch.object(la, "launch") as launch, \
            mock.patch.object(automation, "_send_to_extension") as ext:
        getattr(automation, trigger)(SERVICE, FAKE_USER, FAKE_PASSWORD, 42, on_error=on_error)
    launch.assert_not_called()
    ext.assert_called_once_with(SERVICE, FAKE_USER, FAKE_PASSWORD, 42, on_error, mode=mode)
