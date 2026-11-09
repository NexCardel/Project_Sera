"""O2-R: what every OS Assist card shares — no focus anywhere, every dismiss path drops the secrets,
nothing queued types after ✕, and the per-card geometry of the extension's CSS."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from concurrent.futures import Future

import pytest
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QEnterEvent
from PySide6.QtWidgets import QApplication, QWidget

from ui.os_assist import dock_card as dc
from ui.os_assist.mecp_card import MecpCard
from ui.os_assist.sca_card import ScaCard
from ui.os_assist.sca_chip import ScaChip
from ui.os_assist.smti_card import SmtiCard

BROWSER = 0x1234
SERVICE = {"id": 7, "name": "GST", "login_page_link": "https://portal.example.test/login"}
TRACES = {"id": 9, "name": "TRACES", "login_page_link": "https://www.tdscpc.gov.in/app/login.xhtml"}
FAKE_USER, FAKE_PASSWORD = "FICTUSER01", "Fict-ional#1"
ROWS = [{"id": 1, "label": "Combo 1", "value": "Fict-ional#1"}, {"id": 2, "label": "Combo 2", "value": "Fict-ional#2"}]


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


class PendingFiller:
    """Every job stays queued (a Future nobody runs)."""

    def __init__(self):
        self.jobs = []

    def _job(self):
        f = Future()
        self.jobs.append(f)
        return f

    def fill(self, *a):
        return self._job()

    def edit_count(self, *a):
        return self._job()

    def focus_step(self, *a):
        return self._job()


def _make(cls, *args, **kw):
    class C(cls):
        def _native_hwnd(self):
            return 0xBEEF
    card = C(*args, win=FakeWin(), locator=FakeLocator(), **kw)
    card.attach(BROWSER, SERVICE)
    return card


def all_cards(copies):
    copier = lambda t, s, c: copies.append(t)
    return [
        _make(SmtiCard, SERVICE, FAKE_USER, FAKE_PASSWORD, client_name="Fict", flutter=False, copier=copier,
              filler=PendingFiller()),
        _make(SmtiCard, TRACES, FAKE_USER, FAKE_PASSWORD, client_name="Fict", copier=copier, filler=PendingFiller()),
        _make(MecpCard, SERVICE, FAKE_USER, FAKE_PASSWORD, client_name="Fict", copier=copier),
        _make(MecpCard, SERVICE, FAKE_USER, client_name="Fict", scc_rows=ROWS, attempt_id="rev-1", copier=copier,
              scc_send=lambda m: None, ledger=lambda t: None),
        _make(ScaCard, "arm-1", 7, "Fict", "Owner", "GST", note="Not verified."),
        _make(ScaChip, "Fict", "GST"),
    ]


def test_no_card_can_take_focus(app):
    for card in all_cards([]):
        assert card.windowFlags() & Qt.WindowDoesNotAcceptFocus
        assert card.testAttribute(Qt.WA_ShowWithoutActivating)
        for w in [card, *card.findChildren(QWidget)]:
            assert w.focusPolicy() == Qt.NoFocus, (type(card).__name__, w.objectName(), type(w).__name__)


@pytest.mark.parametrize("reason", ["close", "timeout", "browser_closed", "filled", "copied", "injected",
                                    "replaced", "closed"])
def test_every_dismiss_path_drops_the_secrets(app, reason):
    copies = []
    for card in all_cards(copies):
        card.present()
        card.dismiss(reason)
        assert card.is_dismissed and not card.has_secrets()
        card.hold_secret("late", "x")                    # a dismissed card takes no new secret
        assert not card.has_secrets()
        if isinstance(card, MecpCard):
            rows = [card.user_row, card.pass_row, *card.rows.values()]
            assert all(r.value.text() == "" for r in rows if r is not None)
    assert copies == []


def test_browser_closing_drops_the_secrets(app):
    card = _make(SmtiCard, SERVICE, FAKE_USER, FAKE_PASSWORD, flutter=False, filler=PendingFiller())
    card.present()
    card.browser_hwnd = 0x9999                           # FakeWin: no such window any more
    card._follow()
    assert card.is_dismissed and not card.has_secrets()


def test_a_queued_press_is_cancelled_by_close_and_never_copies(app):
    copies, filler = [], PendingFiller()
    card = _make(SmtiCard, SERVICE, FAKE_USER, FAKE_PASSWORD, flutter=False, filler=filler,
                 copier=lambda t, s, c: copies.append(t))
    card.present()
    card.pass_btn.click()
    card.dismiss("close")
    assert filler.jobs and all(j.cancelled() for j in filler.jobs)
    assert copies == [] and not card.has_secrets()


def test_a_queued_flutter_step_is_cancelled_by_close(app):
    filler = PendingFiller()
    card = _make(SmtiCard, TRACES, FAKE_USER, FAKE_PASSWORD, filler=filler)
    card.present()
    card._poll_focus()
    card.dismiss("close")
    assert filler.jobs and all(j.cancelled() for j in filler.jobs)


def test_geometry_follows_each_extension_card(app):
    smti = _make(SmtiCard, SERVICE, FAKE_USER, FAKE_PASSWORD, flutter=False, filler=PendingFiller())
    mecp = _make(MecpCard, SERVICE, FAKE_USER, FAKE_PASSWORD)
    sca = _make(ScaCard, "arm-1", 7, "Fictional " * 12, "Owner", "GST")
    chip = _make(ScaChip, "Fict", "GST")
    assert (smti.DOCK_TOP, smti.DOCK_RIGHT, smti.close_btn.text()) == (18, 24, "✕")
    assert (mecp.DOCK_TOP, mecp.DOCK_RIGHT, mecp.close_btn.text(), mecp.SLIDE_IN_MS) == (18, 18, "×", 0)
    assert (sca.DOCK_TOP, chip.DOCK_TOP) == (20, 20)
    sca.present()
    assert sca.card.width() == dc.css_px(320)
    assert sca.title.text().endswith("…") and "\n" not in sca.title.text()
    assert sca.timer.height() == dc.css_px(3)
    sca.enterEvent(QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))                               # no hover pause on the SCA card (as the extension)
    assert not sca.timer.paused
    chip.present()
    assert chip.card.width() <= dc.css_px(340) and chip.card.height() < dc.css_px(40)   # one line
    mecp.present()
    assert mecp.isVisible() and mecp.card.pos().x() == 0                               # no slide-in
    mecp.dismiss("close")
    assert not mecp.isVisible()


def test_long_step_text_widens_the_card_like_css(app):
    """CSS shrink-to-fit: the in-page card grows to 420 px before wrapping its text."""
    card = _make(SmtiCard, TRACES, FAKE_USER, FAKE_PASSWORD, filler=PendingFiller())
    card.present()
    assert card.card.width() == dc.css_px(dc.CARD_MAX_PX)
    short = _make(SmtiCard, SERVICE, FAKE_USER, FAKE_PASSWORD, client_name="Fict", flutter=False,
                  filler=PendingFiller())
    short.present()
    assert short.card.width() == dc.css_px(dc.CARD_MIN_PX)
