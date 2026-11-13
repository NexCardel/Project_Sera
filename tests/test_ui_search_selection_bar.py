"""S-7: the All Clients selection bar (IMPLEMENTATION 4.1) and the screen's COMPACT behaviour."""
import ui_lock_support as S

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox, QTableWidgetSelectionRange

from ui.components.kit import LabelledAction
from ui.windows.admin_window import AdminWindow
from ui.windows.search_window import SearchWindow
from ui.components.kit import MessageBox

COMPANY = "NAME OF COMPANY"
NO = QMessageBox.StandardButton.No


@pytest.fixture
def world(tmp_path):
    db = S.make_db(tmp_path)
    ids = S.seed_clients(db)
    win = SearchWindow(db)
    win.resize(1366, 700)
    win.show()
    S.pump()
    win.set_admin_mode(True)
    yield db, ids, win
    S.dispose(win)


def _col(win, label):
    t = win.results_table
    return [t.horizontalHeaderItem(i).text() for i in range(t.columnCount())].index(label)


def _row_of(win, client_id):
    t = win.results_table
    return [t.item(r, 0).data(Qt.UserRole) for r in range(t.rowCount())].index(client_id)


def _select(win, top, left, bottom, right):
    t = win.results_table
    t.clearSelection()
    t.setCurrentCell(top, left)
    t.setRangeSelected(QTableWidgetSelectionRange(top, left, bottom, right), True)
    S.pump()


def _client(win):
    return [win.btn_open_client, win.btn_edit_client, win.btn_manage_services,
            win.btn_archive_client, win.btn_delete_client]


def _cell(win):
    return [win.btn_fill_color, win.btn_text_color, win.btn_clear_fmt]


# ---- state matrix ---------------------------------------------------------------------------

def test_nothing_selected_shows_only_the_hint_and_nothing_is_enabled(world):
    db, ids, win = world
    win.results_table.clearSelection()
    S.pump()
    bar = win.selection_bar
    assert not bar.has_selection()
    assert bar.hint_label.isVisibleTo(win) and not bar.active_container.isVisibleTo(win)
    for b in _client(win) + _cell(win):
        assert not b.isEnabled() and b.toolTip()                         # disabled with a reason
    assert win.btn_delete_client.disabled_reason                         # never clickable with no selection


def test_one_row_enables_the_client_and_cell_groups_and_names_the_client(world):
    db, ids, win = world
    r = _row_of(win, ids["B"])
    _select(win, r, 0, r, 2)                                             # several cells of one row
    bar = win.selection_bar
    assert bar.has_selection() and bar.who() == "Bravo Textiles LLP"
    for b in _client(win) + _cell(win):
        assert b.isEnabled()


def test_several_rows_keep_only_the_cell_group(world):
    db, ids, win = world
    _select(win, 0, _col(win, COMPANY), 1, _col(win, COMPANY))
    assert win.selection_bar.who() == "2 clients selected"
    for b in _client(win):
        assert not b.isEnabled() and "one client" in b.toolTip()
    for b in _cell(win):
        assert b.isEnabled()
    calls = []
    win.edit_client_requested.connect(calls.append)
    win.archive_client_requested.connect(calls.append)
    win.btn_edit_client.click()
    win.btn_archive_client.click()
    assert calls == []                                                   # never acts on "the first" row


def test_staff_view_hides_the_admin_actions_in_the_bar(world):
    db, ids, win = world
    win.set_admin_mode(False)
    _select(win, 0, 0, 0, 0)
    assert win.btn_open_client.isVisibleTo(win) and win.btn_archive_client.isVisibleTo(win)
    for b in (win.btn_edit_client, win.btn_manage_services, win.btn_delete_client):
        assert not b.isVisibleTo(win)


def test_open_and_actions_act_on_the_selected_row(world):
    db, ids, win = world
    seen = []
    win.client_selected.connect(lambda i: seen.append(("open", i)))
    win.edit_client_requested.connect(lambda i: seen.append(("edit", i)))
    win.manage_services_requested.connect(lambda i: seen.append(("services", i)))
    r = _row_of(win, ids["C"])
    _select(win, r, 1, r, 1)
    for b in (win.btn_open_client, win.btn_edit_client, win.btn_manage_services):
        b.click()
    assert seen == [("open", ids["C"]), ("edit", ids["C"]), ("services", ids["C"])]


def test_undo_redo_follow_the_stacks_with_reasons(world):
    db, ids, win = world
    assert not win.btn_undo.isEnabled() and "Ctrl+Z" in win.btn_undo.toolTip()
    assert not win.btn_redo.isEnabled() and "Ctrl+Y" in win.btn_redo.toolTip()
    r = _row_of(win, ids["A"])
    _select(win, r, _col(win, COMPANY), r, _col(win, COMPANY))
    win._execute_formatting_operation("fill", "#ffee00")
    assert win.btn_undo.isEnabled() and "Ctrl+Z" in win.btn_undo.toolTip()


def test_fill_and_text_show_the_current_cell_colour(world):
    db, ids, win = world
    r = _row_of(win, ids["A"])
    c = _col(win, COMPANY)
    _select(win, r, c, r, c)
    from ui.utils.tokens import TOKENS as T
    assert win.btn_fill_color._swatch == T["g_bg"]                       # unformatted cell: grid white
    win._execute_formatting_operation("fill", "#ffee00")
    S.pump()
    assert win.btn_fill_color._swatch.lower() == "#ffee00"
    assert win.btn_text_color._swatch                                    # always a swatch (no width jump)


# ---- focus, height, delete --------------------------------------------------------------------

def test_bar_buttons_never_take_focus_and_keys_keep_working(world):
    db, ids, win = world
    win.results_table.setFocus()
    _select(win, 0, 0, 0, 0)
    for b in _client(win) + _cell(win) + [win.btn_undo, win.btn_redo]:
        assert b.focusPolicy() == Qt.NoFocus
    QTest.mouseClick(win.btn_archive_client, Qt.LeftButton)
    S.pump()
    assert win.results_table.hasFocus()
    QTest.keyClick(win.results_table, Qt.Key_Down)
    assert win.results_table.currentRow() == 1
    opened = []
    win.client_selected.connect(opened.append)
    QTest.keyClick(win.results_table, Qt.Key_Return)
    assert len(opened) == 1


def test_bar_height_is_constant_and_the_grid_never_moves(world):
    db, ids, win = world
    win.results_table.clearSelection()
    S.pump()
    h, y = win.selection_bar.height(), win.grid_frame.y()
    _select(win, 0, 0, 0, 0)
    assert (win.selection_bar.height(), win.grid_frame.y()) == (h, y)
    _select(win, 0, 0, 2, 2)
    assert (win.selection_bar.height(), win.grid_frame.y()) == (h, y)


def test_delete_opens_the_same_confirmation_and_cancel_changes_nothing(world, monkeypatch):
    db, ids, win = world
    admin = AdminWindow(db, actor="Admin")
    win.delete_client_requested.connect(admin.delete_client)             # as main.py wires it
    asked = []
    monkeypatch.setattr(MessageBox, "question",
                        staticmethod(lambda p, t, text, *a, **k: (asked.append(t), NO)[1]))
    audit_before = len(db.get_audit_logs())
    r = _row_of(win, ids["B"])
    _select(win, r, 0, r, 0)
    win.btn_delete_client.click()
    assert asked == ["Delete client"]
    assert db.get_client(ids["B"]) and len(db.get_audit_logs()) == audit_before
    S.dispose(admin)


def test_client_actions_also_have_a_menu_path(world, monkeypatch):
    from PySide6.QtWidgets import QMenu
    db, ids, win = world
    r = _row_of(win, ids["C"])
    _select(win, r, 0, r, 0)
    shown = []

    def fake_exec(menu, *a, **k):
        acts = [a for a in menu.actions() if isinstance(a.data(), tuple) and a.data()[0] == "client"]
        shown.extend(a.data()[1] for a in acts)
        return next(a for a in acts if a.data()[1] is win.btn_edit_client)
    monkeypatch.setattr(QMenu, "exec_", fake_exec)
    seen = []
    win.edit_client_requested.connect(seen.append)
    win._show_cell_formatting_menu(win.results_table.visualItemRect(win.results_table.item(r, 0)).center())
    assert shown == _client(win) and seen == [ids["C"]]


# ---- COMPACT ----------------------------------------------------------------------------------

def test_compact_makes_bar_buttons_icon_only_except_open(world):
    db, ids, win = world
    _select(win, 0, 0, 0, 0)
    win.apply_width_mode("compact")
    S.pump()
    assert win.selection_bar.is_compact()
    assert win.btn_open_client.text() == "Open"
    for b in (win.btn_edit_client, win.btn_archive_client, win.btn_fill_color, win.btn_delete_client):
        assert isinstance(b, LabelledAction) and b.text() == "" and b.toolTip()
    win.apply_width_mode("wide")
    assert win.btn_edit_client.text() == "Edit"


def test_compact_width_moves_chips_and_hides_gstin_and_phone(world):
    db, ids, win = world
    win.resize(900, 640)
    S.pump()
    win.check_width_mode(900)
    S.pump()
    assert win.selection_bar.is_compact()
    assert win.chip_row.isVisibleTo(win)
    t = win.results_table
    hidden = {t.horizontalHeaderItem(c).text() for c in range(t.columnCount()) if t.isColumnHidden(c)}
    assert {"GSTIN", "PH. NO."} <= hidden
    assert COMPANY not in hidden
    assert t.horizontalScrollBar().maximum() == 0 or not t.horizontalScrollBar().isVisible()
