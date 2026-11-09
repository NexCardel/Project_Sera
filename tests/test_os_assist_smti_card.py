import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
from concurrent.futures import Future

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from ui.os_assist import dock_card as dc
from ui.os_assist import smti_card as sc

BROWSER = 0x1234
SERVICE = {"id": 7, "name": "GST", "login_page_link": "https://portal.example.test/login",
           "username_selector": "#user", "password_selector": "#pass"}
TRACES = {"id": 9, "name": "TRACES", "login_page_link": "https://www.tdscpc.gov.in/app/login.xhtml"}
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


class FakeFiller:
    """Answers each job with the next queued outcome (a plain value or a pending Future)."""

    def __init__(self):
        self.fills, self.focus, self.counts = [], [], []
        self.fill_answers, self.focus_answers, self.count_answers = [], [], []

    def fill(self, hwnd, service, kind, text, user_sel, pass_sel):
        self.fills.append((hwnd, kind, text, user_sel, pass_sel))
        return self.fill_answers.pop(0)

    def edit_count(self, hwnd):
        self.counts.append(hwnd)
        return self.count_answers.pop(0)

    def focus_step(self, hwnd, service, text, is_password):
        self.focus.append((text, is_password))
        return self.focus_answers.pop(0) if self.focus_answers else None


class Card(sc.SmtiCard):
    def _native_hwnd(self):
        return 0xBEEF


def make(service=SERVICE, flutter=None, clear_seconds=45, present=True):
    filler, copies, edits, clock = FakeFiller(), [], [], Clock()
    card = Card(service, FAKE_USER, FAKE_PASSWORD, client_id=42, client_name="Fictional Traders",
                clear_seconds=clear_seconds, flutter=flutter, filler=filler,
                copier=lambda t, s, c: copies.append((t, s, c)), sck_handler=edits.append,
                locator=FakeLocator(), win=FakeWin(), clock=clock)
    card.attach(BROWSER, service)
    if present:
        card.present()
    return card, filler, copies, edits, clock


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def state(w):
    return w.property("state")


def test_look_badge_title_and_two_primary_buttons(app):
    card, *_ = make()
    assert card.badge.text() == "⚡ SERA ASSIST"
    assert card.title.text() == "Fictional Traders"
    assert card.user_btn.text() == "👤  Username" and state(card.user_btn) == "primary"
    assert card.pass_btn.text() == "🔑  Password" and state(card.pass_btn) == "primary"
    assert not card.flutter and card.guide.isHidden() and not card.actions.isHidden()
    assert card.timer.duration_ms == 30000
    card.dismiss()


def test_title_falls_back_to_client_profile(app):
    card = Card(SERVICE, FAKE_USER, FAKE_PASSWORD, filler=FakeFiller(), locator=FakeLocator(), win=FakeWin())
    assert card.title.text() == "Client Profile"
    card.dismiss()


def test_username_filled_shows_injected_for_2s(app):
    card, filler, copies, *_ = make()
    filler.fill_answers.append("filled")
    card.user_btn.click()
    assert filler.fills == [(BROWSER, "user", FAKE_USER, "#user", "#pass")]
    assert card.user_btn.text() == "✓  Username Injected" and state(card.user_btn) == "done"
    assert card._user_revert.interval() == 2000 and card._user_revert.isActive()
    assert copies == []
    card._user_revert.timeout.emit()
    assert card.user_btn.text() == "👤  Username" and state(card.user_btn) == "primary"
    assert not card.is_dismissed
    card.dismiss()


@pytest.mark.parametrize("outcome", ["nobox", "failed", "refused"])
def test_username_not_filled_is_copied_not_secret(app, outcome):
    card, filler, copies, *_ = make()
    filler.fill_answers.append(outcome)
    card.user_btn.click()
    assert copies == [(FAKE_USER, False, None)]
    assert card.user_btn.text() == "📋  Copied Username (Ctrl+V)" and state(card.user_btn) == "done"
    assert card._user_revert.interval() == 2500
    card.dismiss()


def test_click_resets_the_timer(app):
    card, filler, _copies, _edits, clock = make()
    clock.t += 20
    assert card.timer.remaining_ms() == pytest.approx(10000)
    filler.fill_answers.append("filled")
    card.user_btn.click()
    assert card.timer.remaining_ms() == pytest.approx(30000)
    card.dismiss()


def test_password_filled_dismisses_and_clears_secrets(app):
    card, filler, copies, *_ = make()
    reasons = []
    card.dismissed.connect(reasons.append)
    filler.fill_answers.append("filled")
    card.pass_btn.click()
    assert filler.fills[0][1:3] == ("pass", FAKE_PASSWORD)
    assert reasons == ["filled"] and card.is_dismissed
    assert copies == [] and card.secret("pass") is None and card.secret("user") is None


def test_password_not_filled_is_securely_copied_then_dismissed(app):
    card, filler, copies, *_ = make(clear_seconds=45)
    reasons = []
    card.dismissed.connect(reasons.append)
    filler.fill_answers.append("nobox")
    card.pass_btn.click()
    assert copies == [(FAKE_PASSWORD, True, 45)]
    assert card.pass_btn.text() == "📋  Copied Password (Ctrl+V)" and state(card.pass_btn) == "done"
    assert not card.is_dismissed
    wait(1300)
    assert reasons == ["copied"] and not card.has_secrets()


def test_result_after_dismiss_is_ignored(app):
    card, filler, copies, *_ = make()
    fut = Future()
    filler.fill_answers.append(fut)
    card.pass_btn.click()
    fut.set_running_or_notify_cancel()       # the worker has started it: dismiss can't cancel it
    card.dismiss("close")
    fut.set_result("nobox")
    app.processEvents()
    assert copies == [] and card.pass_btn.text() == "🔑  Password"


def test_second_click_while_a_job_runs_is_ignored(app):
    card, filler, *_ = make()
    fut = Future()
    filler.fill_answers.append(fut)
    card.user_btn.click()
    card.user_btn.click()
    assert len(filler.fills) == 1
    fut.set_result("filled")
    app.processEvents()
    assert card.user_btn.text() == "✓  Username Injected"
    card.dismiss()


def test_worker_thread_result_reaches_the_qt_thread(app):
    card, filler, *_ = make()
    fut = Future()
    filler.fill_answers.append(fut)
    card.user_btn.click()
    seen = []
    orig = card._pressed
    card._pressed = lambda k, o: (seen.append(threading.current_thread() is threading.main_thread()), orig(k, o))
    t = threading.Thread(target=lambda: fut.set_result("filled"))
    t.start()
    t.join()
    for _ in range(20):
        app.processEvents()
        if seen:
            break
        wait(10)
    assert seen == [True] and card.user_btn.text() == "✓  Username Injected"
    card.dismiss()


def test_edit_button_opens_sck_for_this_client_and_service(app):
    card, _f, _c, edits, _clock = make()
    card.edit_btn.click()
    assert edits == [{"client_id": 42, "service_id": 7, "portal": "gst"}]
    card.dismiss()


def test_shared_sck_helper_uses_the_registered_app_handler(app):
    got = []
    dc.set_sck_edit_handler(got.append)
    try:
        card = dc.DockCard(locator=FakeLocator(), win=FakeWin())
        dc.wire_sck_edit(card, 5, 11, "mca")
        card.edit_btn.click()
    finally:
        dc.set_sck_edit_handler(None)
    assert got == [{"client_id": 5, "service_id": 11, "portal": "mca"}]


# -- Flutter steps --------------------------------------------------------------------------------
def test_traces_host_is_flutter():
    assert sc.is_flutter_service(TRACES)
    assert sc.is_flutter_service({"login_page_link": "traces.example.test"})
    assert not sc.is_flutter_service(SERVICE)


def test_flutter_mode_hides_buttons_and_shows_steps(app):
    card, *_ = make(TRACES)
    assert card.flutter and card.actions.isHidden() and not card.guide.isHidden()
    assert card.step1.title.text() == "👉 Step 1: Click the Username box" and card.step1.state == "active"
    assert card.step2.title.text() == "Step 2: Click the Password box" and card.step2.state == ""
    assert card._focus_poll.isActive() and card._focus_poll.interval() == 200
    card.dismiss()
    assert not card._focus_poll.isActive()


def test_focus_poll_waits_for_present(app):
    card, *_ = make(TRACES, present=False)
    assert card.flutter and not card._focus_poll.isActive()
    card.present()
    assert card._focus_poll.isActive()
    card.dismiss()


def test_flutter_step_flow_types_user_then_password(app):
    card, filler, copies, *_ = make(TRACES)
    reasons = []
    card.dismissed.connect(reasons.append)
    card._poll_focus()                                   # no new box focused yet
    assert filler.focus == [(FAKE_USER, False)] and card._step == 0
    filler.focus_answers.append("filled")
    card._poll_focus()
    assert card.step1.state == "done" and card.step1.num.text() == "✓"
    assert card.step1.title.text() == "✓  Username Typed"
    assert card.step2.state == "active" and card.step2.title.text() == "👉 Step 2: Click the Password box"
    assert card.step2.desc.text() == "Now tap or click inside the Password box on the page!"
    filler.focus_answers.append("filled")
    card._poll_focus()
    assert filler.focus[-1] == (FAKE_PASSWORD, True)
    assert card.step2.state == "done" and card.step2.title.text() == "✓  Password Typed"
    assert not card._focus_poll.isActive() and copies == []
    wait(500)
    assert reasons == ["filled"] and not card.has_secrets()


def test_flutter_step_not_typed_is_copied(app):
    card, filler, copies, *_ = make(TRACES)
    filler.focus_answers.append("failed")
    card._poll_focus()
    assert copies == [(FAKE_USER, False, None)] and card.step1.title.text() == "📋  Copied Username (Ctrl+V)"
    filler.focus_answers.append("refused")
    card._poll_focus()
    assert copies[-1] == (FAKE_PASSWORD, True, 45)
    assert card.step2.title.text() == "📋  Copied Password (Ctrl+V)"
    wait(1300)
    assert card.is_dismissed


def test_one_focus_job_at_a_time_and_none_while_hidden(app):
    card, filler, *_ = make(TRACES)
    fut = Future()
    filler.focus_answers.append(fut)
    card._poll_focus()
    card._poll_focus()
    assert len(filler.focus) == 1
    fut.set_result(None)
    card.hidden_reason = "host"
    card._poll_focus()
    assert len(filler.focus) == 1
    card.dismiss()


def test_page_without_boxes_switches_to_flutter(app):
    card, filler, *_ = make()
    filler.count_answers.append(0)
    card._probe_flutter()
    assert card.flutter and card.actions.isHidden() and card._focus_poll.isActive()
    card.dismiss()


def test_page_with_boxes_stays_normal_and_no_document_retries(app):
    card, filler, *_ = make()
    filler.count_answers.append(2)
    card._probe_flutter()
    assert not card.flutter
    card2, filler2, *_ = make()
    filler2.count_answers.append(-1)
    card2._probe_flutter()
    assert not card2.flutter and card2._probe_tries == 1
    card.dismiss()
    card2.dismiss()


def test_no_flutter_switch_after_a_button_was_used(app):
    card, filler, *_ = make()
    filler.fill_answers.append("filled")
    card.user_btn.click()
    card._probe_flutter()
    assert filler.counts == [] and not card.flutter
    card.dismiss()


def test_forced_normal_mode_on_traces(app):
    card, *_ = make(TRACES, flutter=False)
    assert not card.flutter and not card.actions.isHidden()
    card.dismiss()


def test_no_secret_left_on_the_card_after_dismiss(app):
    card, *_ = make()
    card.dismiss("close")
    assert not card.has_secrets()
    for v in vars(card).values():
        assert v != FAKE_PASSWORD and v != FAKE_USER


# ----------------------------------------------------------------------------- fill's late check
def test_username_shows_injected_at_once_and_stays_when_the_late_check_passes(app):
    card, filler, copies, *_ = make()
    late = Future()
    filler.fill_answers.append(("filled", late))
    card.user_btn.click()
    assert card.user_btn.text() == "✓  Username Injected"     # before the late check is in
    card.user_btn.click()
    assert len(filler.fills) == 1                               # busy until it is
    late.set_result("")
    app.processEvents()
    assert card.user_btn.text() == "✓  Username Injected" and copies == []
    filler.fill_answers.append("filled")
    card.user_btn.click()
    assert len(filler.fills) == 2                               # free again
    card.dismiss()


def test_username_late_failure_falls_back_to_copy(app):
    card, filler, copies, *_ = make()
    late = Future()
    filler.fill_answers.append(("filled", late))
    card.user_btn.click()
    late.set_result("mismatch (0 chars) (at 1.0s)")
    app.processEvents()
    assert card.user_btn.text() == "📋  Copied Username (Ctrl+V)"
    assert copies == [(FAKE_USER, False, None)]
    card.dismiss()


def test_password_shows_injected_then_goes_when_the_late_check_passes(app):
    card, filler, copies, *_ = make()
    reasons = []
    card.dismissed.connect(reasons.append)
    late = Future()
    filler.fill_answers.append(("filled", late))
    card.pass_btn.click()
    assert card.pass_btn.text() == "✓  Password Injected" and not card.is_dismissed
    late.set_result("")
    app.processEvents()
    assert reasons == ["filled"] and not card.has_secrets() and copies == []


def test_password_late_failure_is_securely_copied(app):
    card, filler, copies, *_ = make(clear_seconds=45)
    reasons = []
    card.dismissed.connect(reasons.append)
    late = Future()
    filler.fill_answers.append(("filled", late))
    card.pass_btn.click()
    late.set_result("empty (at 1.0s)")
    app.processEvents()
    assert copies == [(FAKE_PASSWORD, True, 45)]
    assert card.pass_btn.text() == "📋  Copied Password (Ctrl+V)"
    wait(1300)
    assert reasons == ["copied"] and not card.has_secrets()


def test_dismiss_cancels_a_pending_late_check(app):
    card, filler, copies, *_ = make()
    late = Future()
    filler.fill_answers.append(("filled", late))
    card.pass_btn.click()
    card.dismiss("close")
    assert late.cancelled() and copies == []
