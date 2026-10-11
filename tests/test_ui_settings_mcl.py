"""S-4 (IMPLEMENTATION 4.3): Master Column List table, Shows-on write-through, the three visibility pages."""
import ui_lock_support as S

import pytest
from PySide6.QtCore import Qt

from ui.components.kit import DarkListTable, Switch
from ui.dialogs import unified_settings_dialog as usd

VIS_PAGES = ((usd._P_MAIN_VIS, "vis_cbs", 3, "show_in_search"),
             (usd._P_QC, "qc_cbs", 4, "allow_quick_copy"),
             (usd._P_ADMIN_VIS, "admin_vis_cbs", 5, "admin_show_in_search"))


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = S.make_db(tmp_path)
    S.seed_clients(db)
    from ui.utils import autostart
    monkeypatch.setattr(autostart, "is_autostart_enabled", lambda: False)
    calls = []
    for name in ("bulk_update_mcl_visibility", "bulk_update_mcl_quick_copy", "bulk_update_mcl_admin_visibility"):
        real = getattr(db, name)
        setattr(db, name, lambda ids, real=real, name=name: (calls.append((name, list(ids))), real(ids))[1])
    dlg = usd.UnifiedSettingsDialog(db, actor="Admin", page="mcl")
    yield db, dlg, calls
    dlg._on_discard()                                   # a dirty dialog would ask on close
    S.dispose(dlg)


def _row(dlg, cid):
    t = dlg._mcl_list
    return next(r for r in range(t.rowCount()) if t.item(r, 0).data(Qt.UserRole) == cid)


def _tick(dlg, cid, col):
    item = dlg._mcl_list.item(_row(dlg, cid), col)
    item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)
    return item.checkState() == Qt.Checked


def test_table_lists_every_column_in_the_old_order_with_type_role_and_flags(env):
    db, dlg, calls = env
    t = dlg._mcl_list
    assert isinstance(t, DarkListTable)
    cols = db.get_mcl_columns()
    assert [t.item(r, 0).text() for r in range(t.rowCount())] == [c["label"] for c in cols]
    for r, c in enumerate(cols):
        assert t.item(r, 1).data(usd.ROLE_TAG)[0] == c.get("field_type", "text")
        for col, (field, _tip) in usd._MCL_FLAG_COLS.items():
            assert (t.item(r, col).checkState() == Qt.Checked) == usd._flag(c, field)
    by = S.cols_by_label(db)
    assert "Secret" in t.item(_row(dlg, by["GST_Password"]), 2).data(usd.ROLE_PILL)[0]
    assert t.item(_row(dlg, by["GST_Password"]), 2).data(usd.ROLE_PILL)[1] == "red"


@pytest.mark.parametrize("build_first", [True, False])
def test_a_tick_in_columns_shows_on_each_visibility_page_built_before_or_after(env, build_first):
    db, dlg, calls = env
    cid = S.cols_by_label(db)["PAN"]
    if build_first:
        for page, *_ in VIS_PAGES:
            dlg._switch_page(page)
        dlg._switch_page(usd._P_MCL)
    wanted = {attr: _tick(dlg, cid, col) for page, attr, col, field in VIS_PAGES}
    for page, attr, col, field in VIS_PAGES:
        dlg._switch_page(page)
        box = getattr(dlg, attr)[cid]
        assert isinstance(box, Switch) and box.isChecked() == wanted[attr]
    assert dlg._dirty_count() == 3


def test_a_switch_on_a_visibility_page_ticks_the_columns_table(env):
    db, dlg, calls = env
    cid = S.cols_by_label(db)["GSTIN"]
    dlg._switch_page(usd._P_QC)
    box = dlg.qc_cbs[cid]
    box.setChecked(not box.isChecked())
    assert (dlg._mcl_list.item(_row(dlg, cid), 4).checkState() == Qt.Checked) == box.isChecked()
    on = sum(1 for c in dlg.mcl_columns if usd._flag(c, "allow_quick_copy"))
    assert dlg._vis_headers["allow_quick_copy"].count() == f"{on} of {len(dlg.mcl_columns)} shown"


def test_space_on_a_tick_cell_stages_the_flag(env):
    from PySide6.QtTest import QTest
    db, dlg, calls = env
    cid = S.cols_by_label(db)["PAN"]
    dlg.show()
    t = dlg._mcl_list
    item = t.item(_row(dlg, cid), 4)
    before = item.checkState() == Qt.Checked
    t.setCurrentCell(item.row(), 4)
    t.setFocus()
    QTest.keyClick(t, Qt.Key_Space)
    assert (item.checkState() == Qt.Checked) != before
    assert usd._flag(next(c for c in dlg.mcl_columns if c["id"] == cid), "allow_quick_copy") != before
    assert dlg._dirty_count() == 1


def test_toggling_twice_leaves_the_dialog_clean(env):
    db, dlg, calls = env
    cid = S.cols_by_label(db)["EMAIL"]
    _tick(dlg, cid, 3)
    assert dlg._dirty_count() == 1 and dlg._unsaved.count() == 1
    _tick(dlg, cid, 3)
    assert dlg._dirty_count() == 0 and dlg._unsaved.count() == 0


def test_save_writes_only_the_flag_sets_that_changed_and_the_right_rows(env):
    db, dlg, calls = env
    by = S.cols_by_label(db)
    on = _tick(dlg, by["PAN"], 5)
    dlg._on_save_settings()
    assert [name for name, _ in calls] == ["bulk_update_mcl_admin_visibility"]
    got = {c["id"]: c for c in db.get_mcl_columns()}
    assert bool(got[by["PAN"]]["admin_show_in_search"]) == on
    assert {i for i, c in got.items() if c["admin_show_in_search"]} == set(calls[0][1])
    assert dlg._dirty_count() == 0


def test_save_without_a_staged_tick_writes_nothing_until_a_page_is_opened(env):
    db, dlg, calls = env
    dlg._on_save_settings()
    assert calls == []
    dlg._switch_page(usd._P_MAIN_VIS)                   # as before: an opened page writes its set
    dlg._on_save_settings()
    assert [name for name, _ in calls] == ["bulk_update_mcl_visibility"]


def test_discard_restores_the_table_and_the_pages(env):
    db, dlg, calls = env
    cid = S.cols_by_label(db)["PH. NO."]
    dlg._switch_page(usd._P_MAIN_VIS)
    before = dlg.vis_cbs[cid].isChecked()
    dlg._switch_page(usd._P_MCL)
    _tick(dlg, cid, 3)
    _tick(dlg, cid, 4)
    dlg._on_discard()
    assert dlg._dirty_count() == 0 and dlg.vis_cbs[cid].isChecked() == before
    flags = {c["id"]: c for c in db.get_mcl_columns()}[cid]
    for col, (field, _tip) in usd._MCL_FLAG_COLS.items():
        assert (dlg._mcl_list.item(_row(dlg, cid), col).checkState() == Qt.Checked) == usd._flag(flags, field)
    dlg._switch_page(usd._P_QC)
    assert dlg.qc_cbs[cid].isChecked() == usd._flag(flags, "allow_quick_copy")


def test_move_keeps_staged_ticks_and_reorders_like_before(env):
    db, dlg, calls = env
    cid = S.cols_by_label(db)["GSTIN"]
    on = _tick(dlg, cid, 3)
    r = _row(dlg, cid)
    dlg._mcl_list.setCurrentCell(r, 0)
    assert dlg._mcl_sel_id() == cid and dlg._mcl_btn_up.isEnabled()
    dlg._mcl_on_move(-1)
    assert [c["id"] for c in db.get_mcl_columns()].index(cid) == r - 1
    assert _row(dlg, cid) == r - 1 and dlg._mcl_list.currentRow() == r - 1
    assert (dlg._mcl_list.item(r - 1, 3).checkState() == Qt.Checked) == on
    assert dlg._dirty_count() == 1


def test_move_buttons_follow_the_selection(env):
    db, dlg, calls = env
    t = dlg._mcl_list
    assert not dlg._mcl_btn_edit.isEnabled() and not dlg._mcl_btn_del.isEnabled()
    t.setCurrentCell(0, 0)
    assert not dlg._mcl_btn_up.isEnabled() and dlg._mcl_btn_dn.isEnabled() and dlg._mcl_btn_del.isEnabled()
    t.setCurrentCell(t.rowCount() - 1, 0)
    assert dlg._mcl_btn_up.isEnabled() and not dlg._mcl_btn_dn.isEnabled()


def test_delete_asks_first_and_drops_the_column_everywhere(env, monkeypatch):
    db, dlg, calls = env
    col = db.create_mcl_column("Invented Extra", "text")
    dlg._mcl_reload()
    dlg._switch_page(usd._P_MAIN_VIS)
    assert col in dlg.vis_cbs
    dlg._switch_page(usd._P_MCL)
    dlg._mcl_list.setCurrentCell(_row(dlg, col), 0)
    asked = []
    monkeypatch.setattr(usd.MessageBox, "question",
                        staticmethod(lambda *a, **k: asked.append(a[1]) or usd.QMessageBox.No))
    dlg._mcl_btn_del.click()
    assert asked == ["Delete Column"] and col in [c["id"] for c in db.get_mcl_columns()]
    monkeypatch.setattr(usd.MessageBox, "question", staticmethod(lambda *a, **k: usd.QMessageBox.Yes))
    dlg._mcl_btn_del.click()
    assert col not in [c["id"] for c in db.get_mcl_columns()]
    assert col not in [dlg._mcl_list.item(r, 0).data(Qt.UserRole) for r in range(dlg._mcl_list.rowCount())]
    dlg._switch_page(usd._P_MAIN_VIS)
    assert col not in dlg.vis_cbs and dlg._dirty_count() == 0


def test_alt_arrows_move_the_selected_column(env):
    db, dlg, calls = env
    t = dlg._mcl_list
    t.setCurrentCell(1, 0)
    cid = dlg._mcl_sel_id()
    from PySide6.QtGui import QShortcut
    shortcuts = {sc.key().toString(): sc for sc in t.findChildren(QShortcut)}
    shortcuts["Alt+Down"].activated.emit()
    assert [c["id"] for c in db.get_mcl_columns()].index(cid) == 2
    shortcuts["Alt+Up"].activated.emit()
    assert [c["id"] for c in db.get_mcl_columns()].index(cid) == 1


def test_edit_cell_action_opens_the_editor_on_that_row(env, monkeypatch):
    db, dlg, calls = env
    opened = []
    monkeypatch.setattr(dlg, "_mcl_on_edit", lambda: opened.append(dlg._mcl_sel_id()))
    dlg._mcl_list.actionTriggered.emit(2, usd._MCL_COL_EDIT, "edit")
    assert opened == [dlg._mcl_list.item(2, 0).data(Qt.UserRole)]


def test_visibility_page_find_box_and_count(env):
    db, dlg, calls = env
    dlg._switch_page(usd._P_MAIN_VIS)
    header = dlg._vis_headers["show_in_search"]
    cols = db.get_mcl_columns()
    on = sum(1 for c in cols if usd._flag(c, "show_in_search"))
    assert header.count() == f"{on} of {len(cols)} shown"
    page = dlg._page_widgets[usd._P_MAIN_VIS]
    find = next(w for w in page.findChildren(usd.Input) if w.placeholderText() == "Find a column")
    find.setText("pan")
    shown = [r.title_label.text() for r in page.findChildren(usd.SettingRow) if not r.isHidden()]
    assert shown and all("pan" in s.lower() for s in shown)


def test_no_literal_styles_or_hex_in_the_new_pages():
    import inspect
    import re
    for fn in (usd.UnifiedSettingsDialog._build_page_mcl, usd.UnifiedSettingsDialog._build_page_visibility,
               usd.UnifiedSettingsDialog._mcl_reload, usd.UnifiedSettingsDialog._paint_flags):
        src = inspect.getsource(fn)
        assert "setStyleSheet(" not in src and not re.search(r"#[0-9A-Fa-f]{3,8}\b", src)
