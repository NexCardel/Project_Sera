"""S-10: Tracker Dump is built from kit widgets only (dark grid, tiles, chips, row actions by delegate)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import inspect
import re
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication, QTableWidget

import ui.windows.tracker_dump_window as tdw
from test_ui_lock_tracker import FakeDB
from ui.components.kit import (Chip, ChipBar, DataGrid, GridFooter, GridFrame, PageHeader, Segmented, Tile,
                               ROLE_ACTIONS, ROLE_PILL, ROLE_SUB)
from ui.utils.responsive import WidthMode
from ui.utils.tokens import TOKENS as T

APP = QApplication.instance() or QApplication([])
W = tdw.TrackerDumpWindow
CLASS_SOURCE = inspect.getsource(W)


@pytest.fixture
def win():
    patches = [patch.object(tdw, "_load_column_choices", return_value=(set(), set())),
               patch.object(tdw, "_save_column_choices")]
    for p in patches:
        p.start()
    w = W(FakeDB(), defer_first_load=True)
    w.resize(1366, 700)
    w.show()
    w.load_data()
    for _ in range(4):
        APP.processEvents()
    yield w
    w.hide()
    w.deleteLater()
    APP.processEvents()
    for p in patches:
        p.stop()


def test_window_class_has_no_stylesheet_literals_or_hex():
    assert "setStyleSheet" not in CLASS_SOURCE
    assert not re.search(r"#[0-9A-Fa-f]{6}\b", CLASS_SOURCE)


def test_page_is_made_of_kit_widgets(win):
    assert isinstance(win.page_header, PageHeader)
    assert isinstance(win.chip_bar, ChipBar) and all(isinstance(c, Chip) for c, *_ in win._filter_chips)
    assert isinstance(win.view_switch, Segmented)
    assert isinstance(win.table, DataGrid) and win.table.tone == "dark"
    assert isinstance(win.grid_footer, GridFooter) and isinstance(win.grid_frame, GridFrame)
    assert win.lbl_page_info is win.grid_footer.count_label
    assert all(isinstance(t, Tile) for t in win._summary_tiles.values())


def test_the_grid_holds_no_widgets_in_cells(win):
    assert isinstance(win.table, QTableWidget)
    assert all(win.table.cellWidget(r, c) is None for r in range(win.table.rowCount()) for c in range(win.table.columnCount()))


def test_status_is_a_pill_and_the_client_cell_has_a_second_line(win):
    pills = [win.table.item(r, W.COL_STATUS).data(ROLE_PILL) for r in range(win.table.rowCount())]
    assert all(p and p[0] and p[1] for p in pills)
    assert dict(pills)["Submitted & E-verified"] == "emerald"
    assert dict(pills)["Not submitted"] != "emerald"
    sub = win.table.item(0, W.COL_CLIENT).data(ROLE_SUB)
    assert sub and "ABCPD1001E" in sub


def test_unregistered_client_is_amber_and_has_no_autofill(win):
    row = next(r for r in range(win.table.rowCount()) if win.table.item(r, W.COL_CLIENT).text() == "Delta Clinic")
    assert win.table.item(row, W.COL_CLIENT).foreground().color().name().upper() == T["warn"].upper()
    keys = [a.key for a in win.table.item(row, W.COL_ACTIONS).data(ROLE_ACTIONS)]
    assert keys == ["view", "create", "more"]


def test_every_row_action_has_a_tooltip_and_autofill_is_the_red_primary(win):
    for r in range(win.table.rowCount()):
        actions = win.table.item(r, W.COL_ACTIONS).data(ROLE_ACTIONS)
        assert actions and all(a.tooltip for a in actions)
    first = win.table.item(0, W.COL_ACTIONS).data(ROLE_ACTIONS)[0]
    assert first.key == "autofill" and first.primary and first.colour == T["danger"]


def test_rows_are_the_kit_tall_rows_in_both_modes(win):
    assert win.table.verticalHeader().defaultSectionSize() == 52
    win.resize(1000, 700)
    for _ in range(4):
        APP.processEvents()
    assert win._width_mode is WidthMode.COMPACT
    assert win.table.verticalHeader().defaultSectionSize() == 52


@pytest.mark.parametrize("width", (1024, 1366, 1920))
def test_no_horizontal_scroll_at_any_width(win, width):
    win.resize(width, 700)
    for _ in range(6):
        APP.processEvents()
    win._adjust_table_columns()
    APP.processEvents()
    assert win.table.horizontalScrollBar().maximum() == 0
    assert win.table.width() <= win.width()


def test_compact_hides_the_subtitle_and_wide_brings_it_back(win):
    win.resize(1000, 700)
    for _ in range(4):
        APP.processEvents()
    assert win._lbl_subtitle.isHidden() and win._chip_row.isVisible()
    win.resize(1600, 900)
    for _ in range(4):
        APP.processEvents()
    assert not win._lbl_subtitle.isHidden() and not win._chip_row.isVisible()
