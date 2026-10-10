"""S-5 (IMPLEMENTATION 4.x): Services page, MCL manager, column edit, service manager and service edit on the kit."""
import re
from pathlib import Path

import ui_lock_support as S

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QMessageBox

from ui.components.kit import DarkListTable, DialogFrame, Select
from ui.dialogs import mcl_manager_dialog as mcl
from ui.dialogs import service_manager_dialog as svc
from ui.dialogs import unified_settings_dialog as usd


@pytest.fixture
def db(tmp_path):
    return S.make_db(tmp_path)


@pytest.fixture
def page(db, monkeypatch):
    from ui.utils import autostart
    monkeypatch.setattr(autostart, "is_autostart_enabled", lambda: False)
    dlg = usd.UnifiedSettingsDialog(db, actor="Admin", page="services")
    yield dlg
    S.dispose(dlg)


def _service(db, name, mode="extension", mode2="", link="https://example.test/login"):
    return db.create_service(name=name, login_page_link=link, userid_column_id=None, password_column_id=None,
                             username_selector="", password_selector="", automation_mode=mode,
                             automation_mode_2=mode2)


def _names(table):
    return [table.item(r, 0).text() for r in range(table.rowCount())]


def test_services_page_is_a_table_with_the_old_buttons(db, page):
    _service(db, "Alpha Portal", "smti", "manual")
    page._svc_reload()
    t = page._svc_list
    assert isinstance(t, DarkListTable)
    assert [t.horizontalHeaderItem(c).text() for c in range(4)] == ["Service", "Automation mode", "Mode 2", "Login URL"]
    assert _names(t) == [s["name"] for s in db.get_services()]
    row = _names(t).index("Alpha Portal")
    assert t.item(row, 1).data(usd.ROLE_TAG)[0] == "SMTI Assist"
    assert t.item(row, 2).data(usd.ROLE_TAG)[0] == "MECP Copy"
    assert t.item(row, 3).text() == "https://example.test/login"
    assert page._svc_btn_add.variant == "primary" and page._svc_btn_del.variant == "danger"


def test_edit_and_delete_wait_for_a_selected_row(db, page):
    _service(db, "Alpha Portal")
    page._svc_reload()
    page._svc_list.setCurrentCell(-1, -1)
    page._svc_on_sel_changed(-1)
    assert not page._svc_btn_edit.isEnabled() and not page._svc_btn_del.isEnabled()
    assert page._svc_btn_del.toolTip() == "Select a service first"
    page._svc_list.setCurrentCell(0, 0)
    assert page._svc_btn_edit.isEnabled() and page._svc_btn_del.isEnabled()


def test_delete_asks_first_and_removes_only_on_yes(db, page, monkeypatch):
    sid = _service(db, "Alpha Portal")
    page._svc_reload()
    page._svc_list.setCurrentCell(_names(page._svc_list).index("Alpha Portal"), 0)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    page._svc_on_delete()
    assert db.get_service(sid) is not None
    emitted = []
    page.services_changed.connect(lambda: emitted.append(1))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    page._svc_on_delete()
    assert db.get_service(sid) is None and emitted
    assert "Alpha Portal" not in _names(page._svc_list)


def test_add_service_writes_the_dialog_result(db, page, monkeypatch):
    data = {"name": "Zeta Portal", "login_page_link": "https://zeta.test/login", "userid_column_id": None,
            "password_column_id": None, "username_selector": "", "password_selector": "",
            "automation_mode": "manual", "automation_mode_2": "", "extension_flow": "single", "browser": "",
            "success_selector": "", "arn_selector": ""}
    monkeypatch.setattr(svc.ServiceEditDialog, "exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr(svc.ServiceEditDialog, "result_data", lambda self: data)
    page._svc_on_add()
    assert "Zeta Portal" in _names(page._svc_list)


def test_service_edit_dialog_is_a_kit_frame_with_every_control(db):
    dlg = svc.ServiceEditDialog(db)
    assert dlg.findChild(DialogFrame) is not None
    for name in ("name_input", "url_input", "uid_combo", "pwd_combo", "mode_combo", "mode_2_combo",
                 "ext_flow_combo", "browser_combo"):
        assert hasattr(dlg, name)
    assert all(isinstance(getattr(dlg, n), Select) for n in
               ("uid_combo", "pwd_combo", "mode_combo", "mode_2_combo", "ext_flow_combo", "browser_combo"))
    assert dlg.mode_combo.toolTip() == svc.MODE_HELP and dlg.mode_2_combo.toolTip() == svc.MODE_HELP_2
    assert dlg.browser_combo.toolTip() == svc.BROWSER_HELP
    dlg.close()


def test_service_edit_dialog_requires_a_name(db, monkeypatch):
    dlg = svc.ServiceEditDialog(db)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[1]))
    dlg._on_accept()
    assert warned == ["Missing Name"] and dlg.result() != QDialog.Accepted
    dlg.close()


def test_service_manager_lists_services_and_guards_buttons(db, monkeypatch):
    _service(db, "Alpha Portal", "smti")
    dlg = svc.ServiceManagerDialog(db)
    t = dlg.list_widget
    assert isinstance(t, DarkListTable)
    assert _names(t) == [s["name"] for s in db.get_services()]
    assert not dlg.edit_btn.isEnabled() and not dlg.del_btn.isEnabled()
    t.setCurrentCell(0, 0)
    assert dlg.edit_btn.isEnabled() and dlg.del_btn.isEnabled()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    n = t.rowCount()
    dlg._on_delete()
    assert t.rowCount() == n - 1
    dlg.close()


def test_column_edit_shows_dropdown_options_only_for_dropdown():
    dlg = mcl.ColumnEditDialog()
    dlg.show()
    assert dlg.options_field.isHidden()
    dlg.type_combo.setCurrentIndex(dlg.type_combo.findData("dropdown"))
    assert not dlg.options_field.isHidden()
    assert dlg.options_input.isVisible() and dlg.options_label.isVisible()
    dlg.close()


def test_column_edit_validates_and_returns_the_same_dict(monkeypatch):
    dlg = mcl.ColumnEditDialog(None, "Entity", "dropdown", ["A", "B"], True, False)
    assert dlg.result_data() == {"label": "Entity", "field_type": "dropdown", "dropdown_options": ["A", "B"],
                                 "is_identity": 1, "is_internal_pk": 0}
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[1]))
    dlg.options_input.setText("")
    dlg._on_accept()
    assert warned == ["Missing Options"]
    dlg.label_input.setText(" ")
    dlg._on_accept()
    assert warned[-1] == "Missing Label"
    dlg.close()


def test_mcl_manager_reorders_and_deletes_with_a_prompt(db, monkeypatch):
    dlg = mcl.MCLManagerDialog(db)
    t = dlg.col_list
    labels = _names(t)
    assert labels == [c["label"] for c in db.get_mcl_columns()]
    t.setCurrentCell(0, 0)
    assert not dlg.up_btn.isEnabled() and dlg.down_btn.isEnabled()
    dlg._on_move(1)
    assert _names(t)[:2] == [labels[1], labels[0]] and t.currentRow() == 1
    n = t.rowCount()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    dlg._on_delete()
    assert t.rowCount() == n
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    dlg._on_delete()
    assert t.rowCount() == n - 1
    dlg.close()


def test_mcl_role_tags_match_the_settings_page():
    assert mcl.mcl_role({"field_type": "password"}) == ("Secret", "red")
    assert mcl.mcl_role({"is_internal_pk": 1, "is_identity": 1}) == ("Internal PK · Identity", "amber")
    assert mcl.mcl_role({"field_type": "text"}) is None
    assert usd._mcl_role is mcl.mcl_role


@pytest.mark.parametrize("module", ["mcl_manager_dialog", "service_manager_dialog"])
def test_dialogs_define_no_colours_or_stylesheets(module):
    src = (Path(mcl.__file__).parent / f"{module}.py").read_text(encoding="utf-8")
    assert "setStyleSheet" not in src
    assert not re.search(r"#[0-9A-Fa-f]{6}\b", src)
