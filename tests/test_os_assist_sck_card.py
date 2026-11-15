import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from ui.os_assist import sck_card as card_mod
from ui.os_assist import dock_card

BROWSER = 0x1234
SERVICE = {"id": 7, "name": "GST", "login_page_link": "https://portal.example.test/login"}


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class FakeWin:
    def __init__(self):
        self.styles = []

    def is_window(self, hwnd):
        return hwnd == BROWSER

    def is_iconic(self, hwnd):
        return False

    def window_rect(self, hwnd):
        return (100, 50, 1300, 850)

    def add_ex_style(self, hwnd, bits):
        self.styles.append(bits)

    def set_owner(self, hwnd, owner):
        pass


class FakeLocator:
    def probe(self, hwnd, service):
        return (True, (108, 130, 1292, 842))


def make(attempt_id="sck1", **kw):
    win = FakeWin()

    class C(card_mod.SckCard):
        def _native_hwnd(self):
            return 0xBEEF
    c = C(attempt_id, SERVICE, win=win, locator=FakeLocator(), **kw)
    c.attach(BROWSER, SERVICE)
    return c, win


def test_texts_and_no_timer(app):
    c, _ = make(client_id=5, client_name="Fict Traders", portal="GST")
    assert c.badge.text() == "SERA CREDENTIAL KEEPER"
    assert c.title.text() == "Fict Traders • GST"
    assert c.subtitle.text() == card_mod.SUB_ROTATION
    assert c.fill_btn.text() == "Fill && Test"       # shows as "Fill & Test" and c.save_btn.text() == "Save Directly"
    assert c.timer is None
    assert c.edit_btn.isHidden()
    c.dismiss()


def test_new_client_variant(app):
    c, _ = make(kind="new_client", identity="ABCDE1234F", portal="Income Tax")
    assert c.title.text() == "ABCDE1234F"
    assert c.subtitle.text() == card_mod.SUB_NEW_CLIENT
    c.dismiss()


def test_this_card_takes_focus_the_others_do_not(app):
    c, win = make()
    assert not (c.windowFlags() & Qt.WindowDoesNotAcceptFocus)
    assert c.testAttribute(Qt.WA_ShowWithoutActivating)
    c._apply_native()
    assert dock_card.WS_EX_NOACTIVATE not in win.styles
    assert all(not (b & dock_card.WS_EX_NOACTIVATE) for b in win.styles)
    assert c.pwd.focusPolicy() == Qt.ClickFocus
    assert c.fill_btn.focusPolicy() == Qt.NoFocus
    assert c.save_btn.focusPolicy() == Qt.NoFocus
    c.dismiss()
    assert DockCard_flag_default()


def DockCard_flag_default():
    return dock_card.DockCard.ACCEPTS_FOCUS is False


def test_fill_signal_and_busy_states(app):
    c, _ = make(client_id=5, portal="GST")
    got = []
    c.fill_requested.connect(got.append)
    c.fill_btn.click()                      # empty box: no signal
    assert got == [] and "Type the new password" in c.status.text()
    c.pwd.setText("Secret#1")
    c.fill_btn.click()
    assert got == ["sck1"]
    assert c.fill_btn.text() == "Filling…" and not c.fill_btn.isEnabled()
    c.set_filled()
    assert c.fill_btn.isEnabled() and "Password filled" in c.status.text()
    c.set_outcome("wrong_password")
    assert "rejected" in c.status.text()
    c.set_outcome("worked")
    assert "Login succeeded" in c.status.text()
    c.set_saved("Saved password replaced")
    assert c.save_btn.text() == "Saved" and c.pwd.text() == ""
    assert not c.fill_btn.isEnabled()
    c.dismiss()


def test_save_signal_and_failure(app):
    c, _ = make(client_id=5, portal="GST")
    got = []
    c.save_requested.connect(got.append)
    c.pwd.setText("Secret#1")
    c.save_btn.click()
    assert got == ["sck1"]
    c.set_save_failed("The password could not be saved. Try again.")
    assert c.save_btn.isEnabled() and "could not be saved" in c.status.text()
    c.dismiss()


def test_locked_disables_and_drops_password(app):
    c, _ = make(client_id=5, portal="GST")
    c.pwd.setText("Secret#1")
    c.set_outcome("locked")
    assert c.pwd.text() == "" and not c.fill_btn.isEnabled()
    assert "locked" in c.status.text()
    c.dismiss()


def test_no_outcome_message(app):
    c, _ = make(client_id=5, portal="GST")
    c.set_no_outcome()
    assert "Save Directly as untested" in c.status.text()
    c.dismiss()


def test_dismiss_drops_secrets_and_unregisters(app):
    c, _ = make("sckX", client_id=5, portal="GST")
    assert card_mod.card_for("sckX") is c
    c.pwd.setText("Secret#1")
    c.hold_secret("pw", "Secret#1")
    reasons = []
    c.dismissed.connect(reasons.append)
    c.dismiss("close")
    assert reasons == ["close"]
    assert c.pwd.text() == "" and not c.has_secrets()
    assert card_mod.card_for("sckX") is None
    assert card_mod.close_card("sckX") is False
