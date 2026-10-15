"""S-9: Manage Clients is built from kit widgets only; scale and responsive behaviour."""
import ui_lock_support as S

import re
from pathlib import Path

import pytest

import ui.windows.admin_window as aw
from ui.components.kit import (Card, ChipBar, DataGrid, DialogFrame, GridFooter, GridFrame,
                               LabelledAction, PageHeader, SelectionBar)
from ui.windows.admin_window import AdminPinDialog, AdminWindow, NewClientDialog

SOURCE = Path(aw.__file__).read_text(encoding="utf-8")


@pytest.fixture
def world(tmp_path):
    db = S.make_db(tmp_path)
    ids = S.seed_clients(db)
    win = AdminWindow(db, actor="Admin")
    win.resize(1366, 700)
    win.show()
    win.refresh()
    S.pump()
    yield db, ids, win
    S.dispose(win)


def test_source_has_no_stylesheet_literals_or_hex():
    assert "setStyleSheet" not in SOURCE
    assert not re.search(r"#[0-9A-Fa-f]{6}\b", SOURCE)


def test_page_is_made_of_kit_widgets(world):
    db, ids, win = world
    assert isinstance(win.page_header, PageHeader)
    assert isinstance(win.chip_bar, ChipBar)
    assert isinstance(win.table, DataGrid) and win.table.list_mode and win.table.tone == "white"
    assert isinstance(win.grid_footer, GridFooter) and isinstance(win.grid_frame, GridFrame)
    assert isinstance(win.bulk_bar, SelectionBar)
    assert isinstance(win.editor_header, Card)
    for b in (win.new_btn, win.more_btn, win.save_btn, win.bulk_archive_btn,
              win.bulk_restore_btn, win.bulk_purge_btn):
        assert isinstance(b, LabelledAction)


def test_form_cards_are_kit_cards_and_never_nested(world):
    db, ids, win = world
    win._build_dynamic_form({}, [], force_rebuild=True)
    titles = [c.title() for c in win._cards]
    assert {"IDENTITY"} <= {t.upper() for t in titles} and len(titles) >= 3
    assert all(isinstance(c, Card) and not c.findChildren(Card) for c in win._cards)


def test_dialogs_are_dialog_frames(world):
    db, ids, win = world
    for dlg in (NewClientDialog(db, win), AdminPinDialog(db, win)):
        assert dlg.findChildren(DialogFrame)
        S.dispose(dlg)


def test_chip_bar_drives_the_hidden_filter_state(world):
    db, ids, win = world
    win._filter_chips[3].click()                                         # Archived
    S.pump()
    assert win.show_archived_cb.isChecked()
    win._filter_chips[0].click()                                         # All
    S.pump()
    assert not win.show_archived_cb.isChecked()


def test_bulk_bar_appears_for_several_rows_and_names_them(world):
    db, ids, win = world
    assert win.bulk_bar.isHidden()
    win.table.selectAll()
    S.pump()
    assert not win.bulk_bar.isHidden()
    assert win.bulk_bar.who() == f"{win.table.rowCount()} selected"
    win.table.clearSelection()
    S.pump()
    assert win.bulk_bar.isHidden()


@pytest.mark.parametrize("width", (900, 1024, 1366, 1920))
def test_no_horizontal_scroll_or_clipped_page_at_any_width(world, width):
    db, ids, win = world
    win.resize(width, 700)
    S.pump(12)
    win.check_width_mode(width)
    S.pump()
    assert win.table.horizontalScrollBar().maximum() == 0
    host = win.table.parentWidget()
    assert win.table.width() <= win.width() and host.width() <= win.width()


def test_compact_makes_header_and_editor_actions_icon_only(world):
    db, ids, win = world
    win.apply_width_mode("compact")
    S.pump()
    assert win.new_btn.text() == "" and win.more_btn.text() == "" and win.new_btn.toolTip()
    win.apply_width_mode("wide")
    S.pump()
    assert win.new_btn.text() and win.more_btn.text()


def test_editor_header_text_is_available_as_a_tooltip_when_squeezed(world):
    db, ids, win = world
    win.table.selectRow(0)
    S.pump()
    assert win.editor_title.toolTip() == win.editor_title.text()
    assert win.editor_sub.toolTip() == win.editor_sub.text()

def test_list_column_is_360_wide_and_300_in_compact(world):
    from ui.utils.tokens import KIT
    db, ids, win = world
    assert win._left_host.width() == KIT["w_mc_list"] == 360
    win.apply_width_mode("compact")
    S.pump()
    assert win._left_host.width() == KIT["w_mc_list_c"] == 300
    win.apply_width_mode("wide")
    S.pump()
    assert win._left_host.width() == 360


def test_cards_are_two_columns_at_1366_and_one_when_narrow(world):
    db, ids, win = world
    win.resize(1366 - 212, 700)          # the page next to the 212 px sidebar
    S.pump()
    assert win._form_scroll.viewport().width() >= 700 and win._card_cols == 2
    win.resize(1024 - 212, 700)
    S.pump()
    assert win._card_cols == 1
    win.resize(1366 - 212, 700)
    S.pump()
    assert win._card_cols == 2


def test_form_fields_are_kit_inputs_and_selects(world):
    from PySide6.QtWidgets import QComboBox, QLineEdit
    from ui.components.kit import Input, Select
    db, ids, win = world
    seen = set()
    for col, widget in win._input_widgets.values():
        ft = col.get("field_type", "text")
        if ft == "date":
            continue
        seen.add(ft)
        want = Select if ft == "dropdown" else Input
        assert isinstance(widget, want), (col["label"], type(widget))
    assert isinstance(next(iter(win._input_widgets.values()))[1], (QLineEdit, QComboBox))
    assert {"password", "text"} <= seen
