"""Behaviour lock R1 (All Clients / search): written against the pre-overhaul UI, must stay green."""
import ui_lock_support as S

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTableWidgetSelectionRange

from ui.windows.search_window import SearchWindow

COMPANY = "NAME OF COMPANY"


@pytest.fixture
def world(tmp_path):
    db = S.make_db(tmp_path)
    ids = S.seed_clients(db)
    win = SearchWindow(db)
    win.resize(1100, 600)
    win.show()
    S.pump()
    yield db, ids, win
    S.dispose(win)


def _header(win):
    t = win.results_table
    return [t.horizontalHeaderItem(i).text() for i in range(t.columnCount())]


def _col(win, label):
    return _header(win).index(label)


def _shown_ids(win):
    t = win.results_table
    return [t.item(r, 0).data(Qt.UserRole) for r in range(t.rowCount())]


def _pick_filter(win, data):
    cb = win.service_filter
    cb.setCurrentIndex(cb.findData(data))
    win._search_timer.stop()
    win._on_search_changed()


def _select_cell(win, row, col):
    t = win.results_table
    t.clearSelection()
    t.setCurrentCell(row, col)
    t.setRangeSelected(QTableWidgetSelectionRange(row, col, row, col), True)


def _row_of(win, client_id):
    return _shown_ids(win).index(client_id)


def _item(win, client_id, label):
    return win.results_table.item(_row_of(win, client_id), _col(win, label))


# ---- R1.1 typing filters live; Enter; Down / Up ---------------------------------------------

def test_typing_filters_live_after_the_debounce(world):
    db, ids, win = world
    assert set(_shown_ids(win)) == {ids["A"], ids["B"], ids["C"]}        # archived D hidden
    QTest.keyClicks(win.search_box, "Bravo")
    assert win._search_timer.isActive()                                  # debounced, not instant
    QTest.qWait(300)
    assert _shown_ids(win) == [ids["B"]]


def test_search_matches_any_column_value(world):
    db, ids, win = world
    QTest.keyClicks(win.search_box, "ABCDE1003F")                        # a PAN, not a name
    QTest.qWait(300)
    assert _shown_ids(win) == [ids["C"]]


def test_down_from_search_box_moves_into_the_grid_and_selects_the_first_row(world):
    db, ids, win = world
    win.search_box.setFocus()
    assert win.results_table.currentRow() == -1
    QTest.keyClick(win.search_box, Qt.Key_Down)
    S.pump()
    assert win.results_table.hasFocus()
    assert win.results_table.currentRow() == 0


def test_down_and_up_move_through_results_inside_the_grid(world):
    db, ids, win = world
    QTest.keyClick(win.search_box, Qt.Key_Down)
    S.pump()
    QTest.keyClick(win.results_table, Qt.Key_Down)
    assert win.results_table.currentRow() == 1
    QTest.keyClick(win.results_table, Qt.Key_Up)
    assert win.results_table.currentRow() == 0


def test_enter_opens_the_current_result(world):
    db, ids, win = world
    opened = []
    win.client_selected.connect(opened.append)
    QTest.keyClicks(win.search_box, "Charlie")
    QTest.qWait(300)
    QTest.keyClick(win.search_box, Qt.Key_Down)                          # pick the result first
    S.pump()
    QTest.keyClick(win.results_table, Qt.Key_Return)
    S.pump()
    assert opened == [ids["C"]]


def test_enter_in_the_search_box_opens_only_a_current_result_today(world):
    """Today Enter in the box opens the *current* grid item; with none current (nothing picked with
    Down yet) it opens nothing, although the hint line says 'Enter to open the top result'. Locked as
    it is: decision recorded in the W0-2 tracker. The overhaul keeps this unless the owner says otherwise."""
    db, ids, win = world
    opened = []
    win.client_selected.connect(opened.append)
    QTest.keyClicks(win.search_box, "Alpha")
    QTest.keyClick(win.search_box, Qt.Key_Return)
    S.pump()
    assert _shown_ids(win) == [ids["A"]]            # Enter flushes the debounce: the filter is applied
    assert win.results_table.currentRow() == -1
    assert opened == []
    # once a result is current, Enter in the box opens it
    win.results_table.setCurrentCell(0, 0)
    win.search_box.setFocus()
    QTest.keyClick(win.search_box, Qt.Key_Return)
    assert opened == [ids["A"]]


def test_double_click_or_activation_of_a_cell_opens_that_client(world):
    db, ids, win = world
    opened = []
    win.client_selected.connect(opened.append)
    win.results_table.itemActivated.emit(_item(win, ids["B"], COMPANY))
    assert opened == [ids["B"]]


# ---- R1.2 copy / undo / redo ----------------------------------------------------------------

def test_copy_shortcut_keys_and_clipboard_text(world):
    db, ids, win = world
    assert win.copy_shortcut.key() == QKeySequence(QKeySequence.Copy)
    assert win.undo_shortcut.key() == QKeySequence(QKeySequence.Undo)
    assert QKeySequence("Ctrl+Z") in QKeySequence.keyBindings(QKeySequence.Undo)
    assert QKeySequence("Ctrl+Y") in QKeySequence.keyBindings(QKeySequence.Redo)

    toasts = []
    win.toast_requested.connect(lambda m, k: toasts.append((m, k)))
    _select_cell(win, _row_of(win, ids["A"]), _col(win, COMPANY))
    win.results_table.setFocus()
    win.copy_shortcut.activated.emit()
    cb = QApplication.clipboard()
    assert cb.text() == "Alpha Traders Pvt Ltd"
    assert cb.mimeData().hasFormat("application/x-sera-uid")             # single cell keeps the marker
    assert toasts and toasts[-1][1] == "success"


def test_copy_of_a_block_is_tab_and_newline_separated_without_the_marker(world):
    db, ids, win = world
    t = win.results_table
    r = _row_of(win, ids["A"])
    t.clearSelection()
    t.setRangeSelected(QTableWidgetSelectionRange(r, _col(win, COMPANY), r + 1, _col(win, COMPANY) + 1), True)
    win.results_table.setFocus()
    win._copy_selection_to_clipboard()
    lines = QApplication.clipboard().text().split("\n")
    assert len(lines) == 2 and all("\t" in ln for ln in lines)
    assert lines[0].split("\t")[0] == t.item(r, _col(win, COMPANY)).text()
    assert not QApplication.clipboard().mimeData().hasFormat("application/x-sera-uid")


def test_ctrl_c_key_press_in_the_grid_copies(world):
    db, ids, win = world
    QApplication.clipboard().setText("before")
    _select_cell(win, _row_of(win, ids["B"]), _col(win, "PAN"))
    win.results_table.setFocus()
    QTest.keyClick(win.results_table, Qt.Key_C, Qt.ControlModifier)
    S.pump()
    assert QApplication.clipboard().text() == "ABCDE1002F"


def _format_cell(win, ids, who, label, color="#ffee00"):
    _select_cell(win, _row_of(win, ids[who]), _col(win, label))
    win._execute_formatting_operation("fill", color)


def test_format_undo_redo_via_buttons_and_stacks(world):
    db, ids, win = world
    assert not win.btn_undo.isEnabled() and not win.btn_redo.isEnabled()
    _format_cell(win, ids, "A", COMPANY)
    key = str(_col_id(db, COMPANY))
    assert db.get_cell_formatting_for_clients([ids["A"]])[(ids["A"], key)]["bg_color"] == "#ffee00"
    assert win.btn_undo.isEnabled() and not win.btn_redo.isEnabled()

    win.results_table.setFocus()
    win.btn_undo.click()
    assert db.get_cell_formatting_for_clients([ids["A"]]) == {}
    assert not win.btn_undo.isEnabled() and win.btn_redo.isEnabled()

    win.btn_redo.click()
    assert db.get_cell_formatting_for_clients([ids["A"]])[(ids["A"], key)]["bg_color"] == "#ffee00"
    assert win.btn_undo.isEnabled() and not win.btn_redo.isEnabled()


def test_undo_shortcut_inside_the_search_box_undoes_typing_not_formatting(world):
    db, ids, win = world
    _format_cell(win, ids, "A", COMPANY)
    win.search_box.setFocus()
    QTest.keyClicks(win.search_box, "xyz")
    win._undo_last_action()
    assert win.search_box.text() != "xyz"
    assert win.btn_undo.isEnabled()                                      # formatting op untouched


def _col_id(db, label):
    return S.cols_by_label(db)[label]


# ---- R1.3 filter presets --------------------------------------------------------------------

def test_the_nine_presets_and_service_filters_are_listed_in_order(world):
    db, ids, win = world
    cb = win.service_filter
    assert [cb.itemData(i) for i in range(cb.count())] == [
        None, "most_viewed", "active_today", "has_services", "no_services", "has_formatting",
        "has_passwords", "missing_passwords", "archived", 1, 2, 3]


def test_filter_presets_return_the_same_clients(world):
    db, ids, win = world
    with db._connect() as conn:                      # add_client logged "Created" for everyone: start clean
        conn.execute("DELETE FROM client_recent_activity")
        conn.execute("DELETE FROM client_activity_stats")
    db.record_client_activity(ids["C"], "Viewed")
    db.record_client_activity(ids["C"], "Viewed")
    db.record_client_activity(ids["A"], "Copied")
    _format_cell(win, ids, "B", "PAN")

    def shown(preset):
        _pick_filter(win, preset)
        return _shown_ids(win)

    assert set(shown(None)) == {ids["A"], ids["B"], ids["C"]}
    assert shown("most_viewed") == [ids["C"], ids["A"]]                  # views weigh 3, ordered high to low
    assert set(shown("active_today")) == {ids["A"], ids["C"]}
    assert set(shown("has_services")) == {ids["A"], ids["C"]}
    assert shown("no_services") == [ids["B"]]
    assert shown("has_formatting") == [ids["B"]]
    assert set(shown("has_passwords")) == {ids["A"], ids["C"]}
    assert shown("missing_passwords") == [ids["B"]]
    assert shown("archived") == [ids["D"]]
    assert shown(1) == [ids["A"]]                                        # GST
    assert shown(2) == [ids["A"]]                                        # Income Tax
    assert shown(3) == [ids["C"]]                                        # Email


def test_search_text_and_a_preset_combine(world):
    db, ids, win = world
    win.search_box.blockSignals(True)
    win.search_box.setText("ABCDE100")
    win.search_box.blockSignals(False)
    _pick_filter(win, "has_services")
    assert set(_shown_ids(win)) == {ids["A"], ids["C"]}
    win.search_box.blockSignals(True)
    win.search_box.setText("Charlie")
    win.search_box.blockSignals(False)
    _pick_filter(win, "has_services")
    assert _shown_ids(win) == [ids["C"]]


# ---- R1.4 highlights survive refresh, re-sort and restart; every column sorts ----------------

def test_highlight_survives_refresh_resort_and_a_new_window(world):
    db, ids, win = world
    _format_cell(win, ids, "A", COMPANY, "#00ccff")
    assert _item(win, ids["A"], COMPANY).background().color().name() == "#00ccff"

    win.refresh()
    assert _item(win, ids["A"], COMPANY).background().color().name() == "#00ccff"

    win.results_table.sortByColumn(_col(win, COMPANY), Qt.DescendingOrder)
    assert _item(win, ids["A"], COMPANY).background().color().name() == "#00ccff"
    assert _item(win, ids["B"], COMPANY).background().color().name() != "#00ccff"

    fresh = SearchWindow(db)                                              # "restart"
    fresh.show()
    S.pump()
    assert _item(fresh, ids["A"], COMPANY).background().color().name() == "#00ccff"
    S.dispose(fresh)


def test_text_colour_and_clear_formatting(world):
    db, ids, win = world
    _select_cell(win, _row_of(win, ids["C"]), _col(win, "PAN"))
    win._execute_formatting_operation("text", "#cc0000")
    assert _item(win, ids["C"], "PAN").foreground().color().name() == "#cc0000"
    _select_cell(win, _row_of(win, ids["C"]), _col(win, "PAN"))
    win._execute_formatting_operation("all", "clear")
    assert db.get_cell_formatting_for_clients([ids["C"]]) == {}


def test_every_column_sorts(world):
    db, ids, win = world
    t = win.results_table
    assert t.isSortingEnabled()
    company = _col(win, COMPANY)
    for c in range(t.columnCount()):
        for order in (Qt.AscendingOrder, Qt.DescendingOrder):
            t.sortByColumn(c, order)
            assert t.horizontalHeader().sortIndicatorSection() == c
            assert t.rowCount() == 3
    t.sortByColumn(company, Qt.AscendingOrder)
    assert [t.item(r, company).text() for r in range(3)] == ["Alpha Traders Pvt Ltd", "Bravo Textiles LLP", "Charlie Foods"]
    t.sortByColumn(company, Qt.DescendingOrder)
    assert [t.item(r, company).text() for r in range(3)] == ["Charlie Foods", "Bravo Textiles LLP", "Alpha Traders Pvt Ltd"]
    # the id column stays attached to its row after a sort
    for r in range(3):
        assert t.item(r, company).data(Qt.UserRole) == t.item(r, 0).data(Qt.UserRole)


# ---- R1.1/R1.6 header + the actions act on the intended client ------------------------------

def test_header_is_every_visible_mcl_column_plus_services(world):
    db, ids, win = world
    assert _header(win) == [c["label"] for c in db.get_mcl_columns()] + ["Services"]
    services_col = _col(win, "Services")
    assert win.results_table.item(_row_of(win, ids["A"]), services_col).text() == "GST, Income Tax"
    assert win.results_table.item(_row_of(win, ids["B"]), services_col).text() == ""


def test_row_actions_are_admin_only_and_act_on_the_current_client(world):
    db, ids, win = world
    assert win.btn_add_client.isVisibleTo(win) and win.btn_archive_client.isVisibleTo(win)
    for b in (win.btn_edit_client, win.btn_delete_client, win.btn_manage_services):
        assert not b.isVisibleTo(win)                                    # staff view
    win.set_admin_mode(True)
    for b in (win.btn_edit_client, win.btn_delete_client, win.btn_manage_services):
        assert b.isVisibleTo(win)

    calls = []
    win.edit_client_requested.connect(lambda i: calls.append(("edit", i)))
    win.delete_client_requested.connect(lambda i: calls.append(("delete", i)))
    win.manage_services_requested.connect(lambda i: calls.append(("services", i)))
    win.archive_client_requested.connect(lambda i: calls.append(("archive", i)))
    add = []
    win.add_client_requested.connect(lambda: add.append(1))

    win.btn_edit_client.click()                                           # nothing selected: no signal
    assert calls == []
    win.results_table.setCurrentCell(_row_of(win, ids["B"]), 0)
    for b in (win.btn_edit_client, win.btn_delete_client, win.btn_manage_services, win.btn_archive_client):
        b.click()
    assert calls == [("edit", ids["B"]), ("delete", ids["B"]), ("services", ids["B"]), ("archive", ids["B"])]
    win.btn_add_client.click()
    assert add == [1]


def test_toolbar_buttons_have_tooltips_and_sidebar_button_signals(world):
    db, ids, win = world
    fired = []
    win.toggle_sidebar_requested.connect(lambda: fired.append(1))
    win.btn_toggle_sidebar.click()
    assert fired == [1]
    assert "Ctrl+B" in win.btn_toggle_sidebar.toolTip()
    assert "Ctrl+Z" in win.btn_undo.toolTip() and "Ctrl+Y" in win.btn_redo.toolTip()
    for b in (win.btn_fill_color, win.btn_text_color, win.btn_clear_fmt, win.btn_undo, win.btn_redo,
              win.btn_sync, win.btn_edit_client, win.btn_delete_client, win.btn_manage_services,
              win.btn_archive_client, win.btn_toggle_sidebar):
        assert b.toolTip()


def test_manual_refresh_toasts_and_keeps_the_grid(world):
    db, ids, win = world
    toasts = []
    win.toast_requested.connect(lambda m, k: toasts.append((m, k)))
    win.btn_sync.click()
    assert toasts and toasts[-1][1] == "info"
    assert set(_shown_ids(win)) == {ids["A"], ids["B"], ids["C"]}
