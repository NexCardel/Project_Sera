import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt, QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from ui.os_assist import dock_card as dc

BROWSER = 0x1234
SERVICE = {"login_page_link": "https://portal.example.test/login"}
FAKE_PASSWORD = "Fict-ional#1"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeWin:
    def __init__(self):
        self.alive = True
        self.iconic = False
        self.rect = (100, 50, 1300, 850)
        self.ex_styles = []
        self.owners = []

    def is_window(self, hwnd):
        return self.alive and hwnd == BROWSER

    def is_iconic(self, hwnd):
        return self.iconic

    def window_rect(self, hwnd):
        return self.rect

    def add_ex_style(self, hwnd, bits):
        self.ex_styles.append((hwnd, bits))

    def set_owner(self, hwnd, owner):
        self.owners.append((hwnd, owner))


class FakeLocator:
    """Synchronous answers in place of the worker's Future."""

    def __init__(self):
        self.host_ok = True
        self.page = (108, 130, 1292, 842)
        self.calls = 0

    def probe(self, hwnd, service):
        self.calls += 1
        return (self.host_ok, self.page)


class Card(dc.DockCard):
    def _native_hwnd(self):
        return 0xBEEF


def make(timeout_ms=5000):
    clock, win, loc = Clock(), FakeWin(), FakeLocator()
    card = Card(timeout_ms=timeout_ms, locator=loc, win=win, clock=clock)
    card.attach(BROWSER, SERVICE)
    return card, clock, win, loc


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def test_window_never_takes_focus_and_is_owned_by_the_browser(app):
    card, _clock, win, _loc = make()
    flags = card.windowFlags()
    assert flags & Qt.FramelessWindowHint
    assert flags & Qt.WindowDoesNotAcceptFocus
    assert (flags & Qt.Tool) == Qt.Tool
    assert not (flags & Qt.WindowStaysOnTopHint)
    assert card.testAttribute(Qt.WA_ShowWithoutActivating)
    assert card.testAttribute(Qt.WA_TranslucentBackground)
    assert (0xBEEF, BROWSER) in win.owners
    assert any(h == 0xBEEF and bits & dc.WS_EX_NOACTIVATE for h, bits in win.ex_styles)
    assert card.close_btn.focusPolicy() == Qt.NoFocus and card.edit_btn.focusPolicy() == Qt.NoFocus
    card.dismiss()


def test_header_has_badge_edit_and_close(app):
    card, *_ = make()
    seen = []
    card.edit_requested.connect(lambda: seen.append("edit"))
    assert card.badge.text() == "⚡ SERA ASSIST"
    card.edit_btn.click()
    assert seen == ["edit"] and not card.is_dismissed
    reasons = []
    card.dismissed.connect(reasons.append)
    card.close_btn.click()
    assert reasons == ["close"]


def test_docks_top_right_of_the_page_and_follows_the_window(app, monkeypatch):
    monkeypatch.setattr(dc, "running_scale", lambda: 1.0)
    monkeypatch.setattr(dc, "to_logical", lambda x, y: dc.QPoint(x, y))
    card, clock, win, loc = make()
    card.present()
    assert card.isVisible()
    assert card.x() + card.width() == 1292 - 24
    assert card.y() == 130 + 18
    win.rect = (300, 150, 1500, 950)                  # dragged; no new probe yet
    clock.t += 0.1
    card._follow()
    assert loc.calls == 1
    assert card.x() + card.width() == 1492 - 24 and card.y() == 230 + 18
    card.dismiss()


def test_probe_is_asked_at_most_every_500_ms(app):
    card, clock, _win, loc = make()
    card.present()
    for _ in range(4):
        clock.t += 0.1
        card._follow()
    assert loc.calls == 1
    clock.t += 0.2
    card._follow()
    assert loc.calls == 2
    card.dismiss()


def test_hides_on_host_mismatch_and_comes_back(app):
    card, clock, _win, loc = make()
    card.present()
    assert card.isVisible()
    loc.host_ok = False
    clock.t += 0.5
    card._follow()                                    # asks
    clock.t += 0.1
    card._follow()                                    # collects -> hide
    assert not card.isVisible() and card.hidden_reason == "host"
    assert card.timer.running                         # the timer keeps running
    loc.host_ok = True
    clock.t += 0.5
    card._follow()
    clock.t += 0.1
    card._follow()
    assert card.isVisible() and card.hidden_reason == ""
    card.dismiss()


def test_worker_future_is_collected_only_when_done(app):
    from concurrent.futures import Future

    class FutureLocator(FakeLocator):
        def probe(self, hwnd, service):
            self.calls += 1
            self.fut = Future()
            return self.fut

    clock, win, loc = Clock(), FakeWin(), FutureLocator()
    card = Card(timeout_ms=5000, locator=loc, win=win, clock=clock)
    card.attach(BROWSER, SERVICE)
    card.present()
    clock.t += 1.0
    card._follow()
    assert loc.calls == 1 and card.isVisible()        # still pending: no second ask
    loc.fut.set_result((False, None))
    card._follow()
    assert not card.isVisible()
    clock.t += 1.0
    card._follow()
    loc.fut.set_exception(RuntimeError("uia gone"))
    card._follow()
    assert not card.isVisible()                       # an error counts as no match
    card.dismiss()


def test_hidden_while_minimised_dismissed_when_closed(app):
    card, clock, win, _loc = make()
    card.hold_secret("password", FAKE_PASSWORD)
    card.present()
    win.iconic = True
    card._follow()
    assert not card.isVisible() and card.hidden_reason == "minimised"
    win.iconic = False
    card._follow()
    assert card.isVisible()
    reasons = []
    card.dismissed.connect(reasons.append)
    win.alive = False
    card._follow()
    assert reasons == ["browser_closed"] and not card.has_secrets()


def test_timer_pause_resume_reset(app):
    clock = Clock()
    bar = dc.TimerBar(10_000, clock=clock)
    bar.start()
    clock.t += 3.0
    assert bar.remaining_ms() == pytest.approx(7000)
    bar.pause()
    assert bar.paused
    clock.t += 5.0
    assert bar.remaining_ms() == pytest.approx(7000)  # paused: no time lost
    bar.resume()
    clock.t += 1.0
    assert bar.remaining_ms() == pytest.approx(6000)
    bar.reset()
    assert bar.remaining_ms() == pytest.approx(10_000)
    bar.stop()


def test_card_hover_pauses_and_click_resets(app):
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QEnterEvent

    card, clock, *_ = make(timeout_ms=10_000)
    card.present()
    clock.t += 4.0
    card.enterEvent(QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
    assert card.timer.paused
    clock.t += 3.0
    card.leaveEvent(QEvent(QEvent.Leave))
    assert not card.timer.paused and card.timer.remaining_ms() == pytest.approx(6000)
    card.timer.mousePressEvent(_press())
    assert card.timer.remaining_ms() == pytest.approx(10_000)
    card.dismiss()


def _press():
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent
    return QMouseEvent(QEvent.MouseButtonPress, QPointF(1, 1), QPointF(1, 1),
                       Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)


def test_resume_with_little_left_dismisses(app):
    clock = Clock()
    bar = dc.TimerBar(1000, clock=clock)
    fired = []
    bar.expired.connect(lambda: fired.append(1))
    bar.start()
    clock.t += 0.6
    bar.pause()
    bar.resume()
    assert fired == [1]


def test_expiry_dismisses_and_clears_secrets(app):
    card = Card(timeout_ms=150, locator=FakeLocator(), win=FakeWin())
    card.attach(BROWSER, SERVICE)
    card.hold_secret("password", FAKE_PASSWORD)
    reasons = []
    card.dismissed.connect(reasons.append)
    card.present()
    wait(400)
    assert reasons == ["timeout"]
    assert card.secret("password") is None and not card.has_secrets()


def test_dismiss_clears_secrets_before_the_signal_and_only_once(app):
    card, *_ = make()
    card.hold_secret("userid", "FICTIONAL01")
    card.hold_secret("password", FAKE_PASSWORD)
    seen = []
    card.dismissed.connect(lambda r: seen.append((r, card.has_secrets())))
    card.present()
    card.dismiss("filled")
    card.dismiss("close")
    assert seen == [("filled", False)]
    card.hold_secret("password", FAKE_PASSWORD)       # a dismissed card holds nothing
    assert not card.has_secrets()
    assert not card._tick.isActive()


def test_slide_out_hides_the_window(app):
    card, *_ = make()
    card.present()
    assert card.isVisible()
    card.dismiss()
    wait(dc.SLIDE_MS + 150)
    assert not card.isVisible()


def test_card_without_timeout_has_no_timer(app):
    card = Card(timeout_ms=None, locator=FakeLocator(), win=FakeWin())
    assert card.timer is None
    card.attach(BROWSER, SERVICE)
    card.present()
    card.dismiss()


# -- lightweight: timer-bar repaints, Windows animation setting --------------------------------------
def test_timer_bar_repaints_only_when_it_loses_a_pixel(app, monkeypatch):
    clock = Clock()
    bar = dc.TimerBar(10000, clock=clock)
    bar.resize(100, 3)
    monkeypatch.setattr(bar, "devicePixelRatioF", lambda: 1.0)
    updates = []
    monkeypatch.setattr(bar, "update", lambda *a: updates.append(1))
    bar.start()
    assert bar._paint.interval() == 100               # 10 s over 100 px: one pixel each 100 ms
    updates.clear()
    bar._drawn_px = bar._bar_px()
    clock.t += 0.05                                    # half a pixel: nothing to redraw
    bar._repaint_if_moved()
    assert updates == []
    clock.t += 0.06
    bar._repaint_if_moved()
    assert updates == [1]
    bar.stop()
    assert not bar._paint.isActive()


def test_timer_bar_check_interval_is_bounded(app, monkeypatch):
    long_bar, short_bar = dc.TimerBar(90000), dc.TimerBar(500)
    for bar in (long_bar, short_bar):
        bar.resize(300, 3)
        monkeypatch.setattr(bar, "devicePixelRatioF", lambda: 1.0)
    assert long_bar._pixel_ms() == dc.BAR_MAX_MS
    assert short_bar._pixel_ms() == dc.BAR_MIN_MS


def test_timer_bar_pause_stops_the_repaint_checks(app):
    clock = Clock()
    bar = dc.TimerBar(5000, clock=clock)
    bar.resize(100, 3)
    bar.start()
    assert bar._paint.isActive()
    bar.pause()
    assert not bar._paint.isActive()
    bar.resume()
    assert bar._paint.isActive()
    bar.stop()


def test_without_windows_animations_the_card_appears_and_goes_at_once(app, monkeypatch):
    monkeypatch.setattr(dc, "ANIMATIONS", False)
    card, *_ = make()
    card.present()
    assert card.isVisible()
    assert card.card.pos() == dc.QPoint(0, 0) and card.windowOpacity() == 1.0
    assert card._slide.state() != dc.QAbstractAnimation.Running
    card.dismiss()
    assert not card.isVisible()


def test_warm_up_is_safe_to_call(app):
    dc.warm_up()
    dc.warm_up()
