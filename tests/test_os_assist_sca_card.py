import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from ui.os_assist import sca_card as card_mod
from ui.os_assist import sca_chip as chip_mod

BROWSER = 0x1234
SERVICE = {"id": 7, "name": "GST", "login_page_link": "https://portal.example.test/login"}


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


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


def spin(ms):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.005)


def make_card(cls, *args, **kw):
    class C(cls):
        def _native_hwnd(self):
            return 0xBEEF
    c = C(*args, win=FakeWin(), locator=FakeLocator(), **kw)
    c.attach(BROWSER, SERVICE)
    return c


def test_card_texts(app):
    c = make_card(card_mod.ScaCard, 11, 7, "Fict Traders", "A. Owner", "GST")
    assert c.badge.text() == "SCA"
    assert c.title.text() == "Fict Traders"
    assert c.subtitle.text() == "A. Owner • GST"
    assert c.inject_btn.text() == "Inject Password"
    assert c.note is None and not c.amber
    assert c.timer.duration_ms == 60000
    c.dismiss()


def test_card_without_owner_or_name(app):
    c = make_card(card_mod.ScaCard, 11, 7, "", "", "GST")
    assert c.title.text() == "Client"
    assert c.subtitle.text() == "GST"
    c.dismiss()


def test_card_amber_note(app):
    c = make_card(card_mod.ScaCard, 11, 7, "Fict Traders", "A. Owner", "GST",
                  note="Check the user ID before injecting")
    assert c.note.text() == "Check the user ID before injecting"
    assert c.amber
    assert "D29922" in c.card_qss()
    c.dismiss()


def test_inject_emits_and_dismisses(app):
    c = make_card(card_mod.ScaCard, 11, 7, "Fict Traders", "A. Owner", "GST")
    got, reasons = [], []
    c.inject_requested.connect(lambda a, s: got.append((a, s)))
    c.dismissed.connect(reasons.append)
    c.inject_btn.click()
    c.inject_btn.click()
    assert got == [(11, 7)]
    assert reasons == ["injected"]


def test_close_does_not_inject(app):
    c = make_card(card_mod.ScaCard, 11, 7, "Fict Traders", "", "GST")
    got, reasons = [], []
    c.inject_requested.connect(lambda a, s: got.append((a, s)))
    c.dismissed.connect(reasons.append)
    c.close_btn.click()
    assert got == [] and reasons == ["close"]


def test_edit_opens_sck(app):
    calls = []
    c = make_card(card_mod.ScaCard, 11, 7, "Fict Traders", "A. Owner", "GST", client_id=5,
                  sck_handler=calls.append)
    c.edit_btn.click()
    assert calls == [{"client_id": 5, "service_id": 7, "portal": "GST"}]
    c.dismiss()


def test_card_never_takes_focus(app):
    c = make_card(card_mod.ScaCard, 11, 7, "Fict Traders", "A. Owner", "GST")
    assert c.windowFlags() & Qt.WindowDoesNotAcceptFocus
    assert c.inject_btn.focusPolicy() == Qt.NoFocus
    c.dismiss()


def test_chip_text():
    assert chip_mod.chip_text("GST", "Fict Traders") == "Filled GST for Fict Traders"
    assert chip_mod.chip_text("GST", "Fict Traders", "AB12") == "Filled GST for Fict Traders (AB12)"
    assert chip_mod.chip_text("", "") == "Filled password for client"


def test_chip_lifetime(app):
    chip = make_card(chip_mod.ScaChip, "Fict Traders", "GST", "AB12", life_ms=150)
    reasons = []
    chip.dismissed.connect(reasons.append)
    assert chip.label.text() == "Filled GST for Fict Traders (AB12)"
    assert chip.windowFlags() & Qt.WindowDoesNotAcceptFocus
    chip.present()
    assert chip.isVisible()
    spin(60)
    assert reasons == []
    spin(200)
    assert reasons == ["timeout"]
    spin(chip_mod.FADE_OUT_MS + 300)
    assert not chip.isVisible()


def test_chip_default_life_is_2_4_s():
    assert chip_mod.LIFE_MS == 2400
