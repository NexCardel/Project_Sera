"""
Corners: SCA's cards and notice popup take the top-right, SCK's card the bottom-right (as the
extension's in-page SCK card). Real case (2026-11-15): the OS SCK card inherited DockCard's
top-right dock and SCA's "no client has this UID" popup sat bottom-right, so they swapped corners.
"""

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from ui.os_assist import dock_card as dc
from ui.os_assist.mecp_card import MecpCard
from ui.os_assist.sca_card import ScaCard
from ui.os_assist.sck_card import SckCard
from ui.os_assist.smti_card import SmtiCard

WINDOW = (100, 50, 1300, 850)          # left, top, right, bottom (physical)
PAGE = (108, 130, 1292, 842)           # the page area inside it


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])



class TopCard(dc.DockCard):
    pass


class BottomCard(dc.DockCard):
    DOCK_CORNER = "bottom-right"


def _docked(cls):
    card = cls(timeout_ms=60000)
    card.resize(320, 200)
    card._page_inset = tuple(p - w for p, w in zip(PAGE, WINDOW))
    card._dock_to(WINDOW)
    return card


def test_only_the_sck_card_docks_bottom_right():
    assert SckCard.DOCK_CORNER == "bottom-right"
    for cls in (ScaCard, SmtiCard, MecpCard):
        assert cls.DOCK_CORNER == "top-right", cls.__name__


def test_top_right_dock_is_unchanged(app):
    card = _docked(TopCard)
    tr = dc.to_logical(PAGE[2], PAGE[1])
    assert card.pos() == QPoint(tr.x() - dc.css_px(card.DOCK_RIGHT) - card.width(),
                                tr.y() + dc.css_px(card.DOCK_TOP))
    card.deleteLater()


def test_bottom_right_dock_sits_above_the_page_bottom(app):
    card = _docked(BottomCard)
    br = dc.to_logical(PAGE[2], PAGE[3])
    assert card.pos() == QPoint(br.x() - dc.css_px(card.DOCK_RIGHT) - card.width(),
                                br.y() - dc.css_px(card.DOCK_BOTTOM) - card.height())
    assert card.pos().y() + card.height() <= br.y()
    card.deleteLater()


def test_bottom_right_dock_follows_a_taller_card(app):
    card = _docked(BottomCard)
    bottom_edge = card.pos().y() + card.height()
    card.resize(320, 260)                   # e.g. a status line appears
    card._dock_to(WINDOW)
    assert card.pos().y() + card.height() == bottom_edge
    card.deleteLater()


def test_sca_notice_popup_sits_top_right_below_the_browser_bar(app):
    from PySide6.QtGui import QGuiApplication
    from ui.components import sca_notice_popup as mod
    pop = mod.ScaNoticePopup()
    pop.show_notice("SCA: test notice", "warning")
    try:
        g = (QGuiApplication.screenAt(pop.geometry().center()) or QGuiApplication.primaryScreen()).availableGeometry()
        assert pop.x() == g.right() - pop.width() - mod.MARGIN
        assert pop.y() == g.top() + mod.TOP_CLEAR
    finally:
        pop.hide()


# -- never off-screen (real case 2026-11-15: Chrome's stale UIA bounds docked the SCK and MECP
#    cards off the left edge of a maximised GST login window) -----------------------------------

def _inside_window(card, wr):
    tl, br = dc.to_logical(wr[0], wr[1]), dc.to_logical(wr[2], wr[3])
    return (card.x() >= tl.x() and card.y() >= tl.y()
            and card.x() + card.width() <= br.x() and card.y() + card.height() <= br.y())


@pytest.mark.parametrize("cls", [TopCard, BottomCard])
def test_a_bogus_page_inset_still_docks_inside_the_window(app, cls):
    card = cls(timeout_ms=60000)
    card.resize(320, 200)
    card._page_inset = (9, 74, -1740, -934)        # what the off-screen cards were docked by
    card._dock_to(WINDOW)
    assert _inside_window(card, WINDOW)
    card.deleteLater()


def test_a_page_rect_outside_the_window_is_not_trusted(app):
    card = BottomCard(timeout_ms=60000)
    card.resize(320, 200)
    card._probe_job = (True, (-31991, -31891, -30070, -30979))     # minimised-era bounds
    card._collect_probe(WINDOW)
    assert card._page_inset is None
    card._dock_to(WINDOW)
    assert _inside_window(card, WINDOW)
    card.deleteLater()


def test_a_page_rect_inside_the_window_is_kept(app):
    card = BottomCard(timeout_ms=60000)
    card._probe_job = (True, PAGE)
    card._collect_probe(WINDOW)
    assert card._page_inset == tuple(p - w for p, w in zip(PAGE, WINDOW))
    card.deleteLater()


def test_a_maximised_window_overhang_still_counts_as_inside():
    assert dc.page_fits_window((0, 109, 1920, 1021), (-9, -9, 1929, 1029))
    assert not dc.page_fits_window((0, 65, 189, 95 - 40), (-9, -9, 1929, 1029))   # empty rect


def test_to_logical_takes_the_screens_right_edge(app):
    from PySide6.QtGui import QGuiApplication
    s = QGuiApplication.primaryScreen()
    g, dpr = s.geometry(), s.devicePixelRatio() or 1.0
    edge = round(g.x() + g.width() * dpr)
    assert dc.to_logical(edge, g.y()).x() == g.x() + g.width()


def test_a_window_hanging_off_the_screen_keeps_the_card_on_it(app, monkeypatch):
    from PySide6.QtCore import QRect
    monkeypatch.setattr(dc.DockCard, "_work_area", staticmethod(lambda center: QRect(0, 0, 1000, 700)))
    card = BottomCard(timeout_ms=60000)
    card.resize(320, 200)
    card._page_inset = tuple(p - w for p, w in zip(PAGE, WINDOW))
    card._dock_to(WINDOW)                     # the page's bottom-right is past the 1000x700 screen
    assert card.x() + card.width() <= 1000 and card.y() + card.height() <= 700
    card.deleteLater()
