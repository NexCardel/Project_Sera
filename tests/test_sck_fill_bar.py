"""
SCK: the desktop Test & Fill bar.

The bar no longer types with Win32 ``SendInput``. It hands the candidate to a ``filler``
(``automation.sck_fill`` in the app) which asks the browser extension to set the portal's
password field in its DOM. These tests drive the bar's state machine directly - no browser,
no extension, no event loop:

* Fill & Test clears the UI copy, stores the candidate in the attempt session and waits for
  the extension's ``sck_fill_result``.
* ``fill_ok`` turns the status green; ``fill_failed`` re-enables the button and discards the
  candidate.
* ``wrong_password`` re-enables for a retry; ``worked`` saves the credential and announces it.
* A login Sera knows no client for cannot be filled at all (there is nowhere to save it).
"""

from unittest.mock import patch

import pytest

from PySide6.QtWidgets import QApplication

from ui.components import test_fill_bar as tfb

APP = QApplication.instance() or QApplication([])

SERVICE = {"id": 7, "name": "GST", "login_page_link": "https://www.gst.gov.in/login",
           "password_selector": "#pwd"}


class _Db:
    pass


class _Save:
    def __init__(self, outcome="saved", client_id=42):
        self.outcome = outcome
        self.client_id = client_id
        self.client_name = "Acme Ltd"
        self.pan = "ABCDE1234F"
        self.portal = "GST"


def _make_bar(filler=None, client_id=42, on_fill_started=None):
    sent = []

    def _filler(password):
        sent.append(password)
        return True

    bar = tfb.TestFillBar(
        db=_Db(),
        client_id=client_id,
        pan="ABCDE1234F",
        portal="GST",
        hwnd=0,
        client_name="Acme Ltd",
        actor_fn=lambda: "Tester",
        service=dict(SERVICE),
        filler=filler or _filler,
        on_fill_started=on_fill_started,
    )
    return bar, sent


@pytest.fixture
def bar():
    made = []

    def _build(filler=None, client_id=42, on_fill_started=None):
        b, sent = _make_bar(filler=filler, client_id=client_id, on_fill_started=on_fill_started)
        made.append(b)
        return b, sent

    yield _build
    for b in made:
        try:
            b.close()
        except RuntimeError:
            pass


def test_fill_clears_the_ui_copy_and_waits(bar):
    b, sent = bar()

    b._pwd_field.setText("Secret#1")
    b._on_fill_clicked()

    assert sent == ["Secret#1"]
    assert b._pwd_field.text() == ""
    assert b._testing is True
    assert b._fill_btn.isEnabled() is False
    assert tfb._MSG_FILLING in b._status_lbl.text()
    # The candidate lives in the attempt session only.
    assert b._session.get(b._attempt.attempt_id)._candidate == "Secret#1"


def test_a_new_fill_starts_a_new_attempt(bar):
    started = []
    b, _sent = bar(on_fill_started=lambda: started.append(1))

    b._pwd_field.setText("Secret#1")
    b._on_fill_clicked()

    assert started == [1]


def test_fill_ok_turns_the_status_green(bar):
    b, _sent = bar()
    b._pwd_field.setText("Secret#1")
    b._on_fill_clicked()

    b.deliver_outcome("fill_ok")
    b._poll_outcome()

    assert tfb._MSG_FILLED in b._status_lbl.text()
    assert b._fill_acked is True


def test_fill_failed_re_enables_and_discards(bar):
    b, _sent = bar()
    b._pwd_field.setText("Secret#1")
    b._on_fill_clicked()

    b.deliver_outcome({"kind": "fill_failed", "message": "No password box."})
    b._poll_outcome()

    assert b._testing is False
    assert b._fill_btn.isEnabled() is True
    assert "No password box." in b._status_lbl.text()
    assert b._session.get(b._attempt.attempt_id)._candidate == ""


def test_unreachable_extension_reports_immediately(bar):
    b, _sent = bar(filler=lambda password: False)
    b._pwd_field.setText("Secret#1")

    b._on_fill_clicked()

    assert b._testing is False
    assert b._fill_btn.isEnabled() is True
    assert tfb._MSG_NO_EXTENSION in b._status_lbl.text()


def test_fill_ack_timeout_reports_the_extension(bar):
    b, _sent = bar()
    b._pwd_field.setText("Secret#1")
    b._on_fill_clicked()

    b._on_fill_ack_timeout()

    assert b._testing is False
    assert tfb._MSG_NO_EXTENSION in b._status_lbl.text()


def test_wrong_password_re_enables_for_a_retry(bar):
    b, _sent = bar()
    b._pwd_field.setText("Secret#1")
    b._on_fill_clicked()

    b.deliver_outcome("wrong_password")
    b._poll_outcome()

    assert b._testing is False
    assert b._fill_btn.isEnabled() is True
    assert tfb._MSG_WRONG in b._status_lbl.text()
    assert b._session.get(b._attempt.attempt_id)._candidate == ""


def test_worked_saves_the_credential_and_emits(bar):
    b, _sent = bar()
    events = []
    b.rotation_succeeded.connect(lambda cid, pan, portal: events.append((cid, pan, portal)))

    b._pwd_field.setText("Secret#1")
    b._on_fill_clicked()

    with patch.object(tfb, "save_verified_for_rotation", return_value=_Save()) as save:
        b.deliver_outcome("worked")
        b._poll_outcome()

    save.assert_called_once()
    kwargs = save.call_args.kwargs
    assert kwargs["password"] == "Secret#1"
    assert kwargs["client_id"] == 42
    assert kwargs["service_id"] == 7
    assert kwargs["method"] == "SCK-Bar"
    assert isinstance(b.last_save, _Save)
    assert events == [(42, "ABCDE1234F", "GST")]
    assert tfb._MSG_WORKED in b._status_lbl.text()


def test_worked_ignored_when_nothing_was_filled(bar):
    """The bar is open after a failure but staff logged in by hand: never save an empty value."""
    b, _sent = bar()

    with patch.object(tfb, "save_verified_for_rotation") as save:
        b.deliver_outcome("worked")
        b._poll_outcome()

    save.assert_not_called()
    assert b.last_save is None


def test_save_failure_is_reported(bar):
    b, _sent = bar()
    b._pwd_field.setText("Secret#1")
    b._on_fill_clicked()

    with patch.object(tfb, "save_verified_for_rotation", return_value=None):
        b.deliver_outcome("worked")
        b._poll_outcome()

    assert b.last_save is None
    assert tfb._MSG_SAVE_FAILED in b._status_lbl.text()
    assert b._fill_btn.isEnabled() is True


def test_locked_closes_after_warning(bar):
    b, _sent = bar()
    b._pwd_field.setText("Secret#1")
    b._on_fill_clicked()
    events = []
    b.rotation_failed.connect(lambda cid, pan, portal, reason: events.append(reason))

    b.deliver_outcome("locked")
    b._poll_outcome()

    assert tfb._MSG_LOCKED in b._status_lbl.text()
    assert events == ["locked"]


def test_no_client_cannot_fill(bar):
    b, sent = bar(client_id=None)

    assert b._fill_btn.isEnabled() is False
    assert b._pwd_field.isEnabled() is False
    assert tfb._MSG_NO_CLIENT in b._status_lbl.text()

    b._on_fill_clicked()
    assert sent == []


def test_service_row_is_what_the_extension_needs(bar):
    b, _sent = bar()
    assert b._service["login_page_link"] == "https://www.gst.gov.in/login"
    assert b._service["password_selector"] == "#pwd"


def test_a_swept_attempt_is_reopened_when_filling(bar):
    """Another bar's open() sweeps attempts older than 3 minutes; filling must not raise."""
    b, sent = bar()
    b._session.close(b._attempt.attempt_id)
    b._pwd_field.setText("Secret#1")

    b._on_fill_clicked()

    assert sent == ["Secret#1"]
    assert b._testing is True
    assert b._session.get(b._attempt.attempt_id)._candidate == "Secret#1"


def test_without_a_browser_window_the_bar_uses_the_screen_corner(bar):
    b, _sent = bar()
    screen = QApplication.primaryScreen()
    if screen is None:
        pytest.skip("no screen")
    geom = screen.availableGeometry()
    assert (b.x(), b.y()) == (geom.right() - tfb._BAR_W - 16, geom.top() + 16)


# ── automation.sck_fill: one browser only ─────────────────────────────────────────────────

class _RecordingBridge:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def send_first(self, payload, browser=None):
        self.calls.append((dict(payload), browser))
        return bool(self.answers.get(browser, False))


def _sck_fill_with(monkeypatch, service, password="Secret#1", answers=None):
    import automation
    from ui import ws_bridge

    bridge = _RecordingBridge(answers or {})
    monkeypatch.setattr(ws_bridge, "get_active_bridge", lambda: bridge)
    monkeypatch.setattr(automation, "_default_browser_kind", lambda: "chrome")
    ok = automation.sck_fill(service, password, 42, "GST")
    return ok, bridge


def test_sck_fill_goes_only_to_the_service_browser(monkeypatch):
    ok, bridge = _sck_fill_with(monkeypatch, dict(SERVICE, browser="firefox"),
                                answers={"firefox": True})

    assert ok is True
    assert len(bridge.calls) == 1
    payload, browser = bridge.calls[0]
    assert browser == "firefox"
    assert payload["type"] == "sck_fill"
    assert payload["password"] == "Secret#1"
    assert payload["service_id"] == 7
    assert payload["client_id"] == 42
    assert payload["url"] == "https://www.gst.gov.in/login"
    assert payload["password_selector"] == "#pwd"


def test_sck_fill_falls_back_to_any_connected_browser(monkeypatch):
    ok, bridge = _sck_fill_with(monkeypatch, dict(SERVICE), answers={None: True})

    assert ok is True
    assert [browser for _payload, browser in bridge.calls] == ["chrome", "edge", None]


def test_sck_fill_with_no_password_sends_nothing(monkeypatch):
    ok, bridge = _sck_fill_with(monkeypatch, dict(SERVICE), password="", answers={None: True})

    assert ok is False
    assert bridge.calls == []


def test_sck_fill_with_no_bridge_reports_failure(monkeypatch):
    import automation
    from ui import ws_bridge

    monkeypatch.setattr(ws_bridge, "get_active_bridge", lambda: None)
    assert automation.sck_fill(dict(SERVICE), "Secret#1", 42, "GST") is False
