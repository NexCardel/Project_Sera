"""Behaviour lock R7 (Manage Clients): written against the pre-overhaul UI, must stay green."""
import ui_lock_support as S

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QDialog, QInputDialog, QLineEdit, QMessageBox,
                               QTableWidgetSelectionRange)

import security
from ui.windows import admin_window as aw
from ui.windows.admin_window import AdminPinDialog, AdminWindow, NewClientDialog

YES, NO = QMessageBox.Yes, QMessageBox.No


@pytest.fixture
def world(tmp_path):
    db = S.make_db(tmp_path)
    ids = S.seed_clients(db)
    win = AdminWindow(db, actor="Admin")
    win.resize(1200, 700)
    win.show()
    win.refresh()
    S.pump()
    yield db, ids, win
    S.dispose(win)


@pytest.fixture(autouse=True)
def ask(monkeypatch):
    """Answer every QMessageBox.question with the next queued answer; record the titles asked."""
    box = {"answers": [], "asked": [], "warned": []}

    def fake(parent, title, text, *a, **k):
        box["asked"].append(title)
        return box["answers"].pop(0) if box["answers"] else NO

    def warn(parent, title, text, *a, **k):
        box["warned"].append((title, text))
        return QMessageBox.Ok
    monkeypatch.setattr(QMessageBox, "question", staticmethod(fake))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(warn))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(warn))
    return box


@pytest.fixture
def signals(world):
    _, _, win = world
    got = {"alerts": [], "toasts": []}
    win.action_alert_requested.connect(lambda kind, name: got["alerts"].append((kind, name)))
    win.toast_requested.connect(lambda text, ms: got["toasts"].append(text))
    return got


def _listed(win):
    t = win.table
    return [int(t.item(r, 0).text()) for r in range(t.rowCount())]


def _select(win, *client_ids):
    t = win.table
    t.clearSelection()
    rows = [_listed(win).index(c) for c in client_ids]
    t.blockSignals(True)
    for r in rows:
        t.setRangeSelected(QTableWidgetSelectionRange(r, 0, r, t.columnCount() - 1), True)
    t.blockSignals(False)
    win._on_row_selected()
    S.pump()


def _chip(win, text):
    return next(c for c in win._filter_chips if c.text() == text)


def _field(win, label):
    for col, widget in win._input_widgets.values():
        if col["label"] == label:
            return col, widget
    raise KeyError(label)


def _audit_actions(db, exclude_ids=()):
    return [(l["action"], l["client_id"]) for l in db.get_audit_logs() if l["id"] not in exclude_ids]


# ---- R7.1 search, filter chips, archived toggle ---------------------------------------------

def test_the_list_starts_with_the_active_clients_in_id_order_and_a_count(world):
    db, ids, win = world
    assert _listed(win) == [ids["A"], ids["B"], ids["C"]]
    assert win.count_label.text() == "3 clients"
    assert win.table.isColumnHidden(0) and not win.table.isColumnHidden(1)


def test_every_row_shows_identity_subline_and_service_tags(world):
    _, ids, win = world
    a = win.table.item(_listed(win).index(ids["A"]), 1)
    assert a.text() == "Alpha Traders Pvt Ltd — Asha Verma"
    assert "ABCDE1001F" in a.data(aw._SUBLINE_ROLE) and "29ABCDE1001F1Z5" in a.data(aw._SUBLINE_ROLE)
    assert [t[0] for t in a.data(aw._TAGS_ROLE)] == ["GST", "Income Tax"]
    b = win.table.item(_listed(win).index(ids["B"]), 1)
    assert [t[0] for t in b.data(aw._TAGS_ROLE)] == ["No services"]


def test_search_filters_after_the_debounce_across_id_name_pan_and_gstin(world):
    _, ids, win = world
    for text, want in (("Bravo", [ids["B"]]), ("ABCDE1003F", [ids["C"]]), ("29ABCDE1001F1Z5", [ids["A"]]),
                       ("Chitra", [ids["C"]]), ("zzzz-nothing", [])):
        win.search_input.setText(text)
        assert win._search_timer.isActive()
        QTest.qWait(250)
        assert _listed(win) == want, text
    win.search_input.setText("")
    QTest.qWait(250)
    assert _listed(win) == [ids["A"], ids["B"], ids["C"]]


def test_filter_chips_list_the_same_clients_as_the_old_presets(world):
    _, ids, win = world
    _chip(win, "Has services").click()
    assert _listed(win) == [ids["A"], ids["C"]]
    _chip(win, "No services").click()
    assert _listed(win) == [ids["B"]]
    _chip(win, "Archived").click()
    assert _listed(win) == [ids["D"]]
    assert win.count_label.text() == "1 archived client"
    _chip(win, "All").click()
    assert _listed(win) == [ids["A"], ids["B"], ids["C"]]


def test_chips_drive_the_hidden_combo_and_archived_box_and_stay_in_step(world):
    _, ids, win = world
    assert win.filter_combo.isHidden() and win.show_archived_cb.isHidden()
    assert [c.isChecked() for c in win._filter_chips] == [True, False, False, False]
    _chip(win, "Has services").click()
    assert win.filter_combo.currentData() == "has_services" and not win.show_archived_cb.isChecked()
    assert [c.isChecked() for c in win._filter_chips] == [False, True, False, False]
    _chip(win, "Archived").click()
    assert win.show_archived_cb.isChecked()
    assert [c.isChecked() for c in win._filter_chips] == [False, False, False, True]


def test_the_more_menu_holds_the_other_presets_and_one_entry_per_service(world):
    db, ids, win = world
    texts = [a.text() for a in win._more_filters_menu.actions() if not a.isSeparator()]
    assert texts[:4] == ["Most viewed / active", "Active today", "Has login credentials", "Missing passwords"]
    assert texts[4:] == [f"Service: {s['name']}" for s in db.get_services()]
    login = next(a for a in win._more_filters_menu.actions() if a.text() == "Has login credentials")
    login.trigger()
    assert _listed(win) == [ids["A"], ids["C"]]
    assert win.more_filters_btn.isChecked() and win.more_filters_btn.text() == "Has login credentials"
    gst = next(a for a in win._more_filters_menu.actions() if a.text() == "Service: GST")
    gst.trigger()
    assert _listed(win) == [ids["A"]]
    assert win.more_filters_btn.text() == "GST"
    _chip(win, "All").click()
    assert win.more_filters_btn.text() == "More" and not win.more_filters_btn.isChecked()


def test_missing_passwords_preset_lists_clients_without_any_password(world):
    _, ids, win = world
    next(a for a in win._more_filters_menu.actions() if a.text() == "Missing passwords").trigger()
    assert _listed(win) == [ids["B"]]


def test_sort_combo_orders_the_list(world):
    _, ids, win = world
    keys = [win.sort_combo.itemData(i) for i in range(win.sort_combo.count())]
    assert keys == [("id", "asc"), ("id", "desc"), ("identity", "asc"), ("identity", "desc"),
                    ("activity", "desc"), ("created_at", "desc"), ("updated_at", "desc")]
    win.sort_combo.setCurrentIndex(1)
    assert _listed(win) == [ids["C"], ids["B"], ids["A"]]
    win.sort_combo.setCurrentIndex(2)
    assert _listed(win) == [ids["A"], ids["B"], ids["C"]]
    win.sort_combo.setCurrentIndex(3)
    assert _listed(win) == [ids["C"], ids["B"], ids["A"]]


def test_archived_mode_swaps_the_active_and_archived_actions(world):
    _, ids, win = world
    assert (win.save_btn.isVisible(), win.new_btn.isVisible()) == (True, True)
    _chip(win, "Archived").click()
    assert (win.save_btn.isHidden(), win.new_btn.isHidden()) == (True, True)
    assert win.bulk_archive_btn.isHidden()
    assert win._archived_mode_btns[0] is win.act_restore and win._archived_mode_btns[1] is win.act_purge
    _chip(win, "All").click()
    assert (win.save_btn.isVisible(), win.new_btn.isVisible()) == (True, True)


# ---- R7.2 the form, Ctrl+S, Ctrl+N, save ------------------------------------------------------

def test_the_form_has_one_input_per_mcl_column_and_one_chip_per_service(world):
    db, _, win = world
    assert set(win._input_widgets) == {c["id"] for c in db.get_mcl_columns()}
    assert list(win._service_cbs) == [s["id"] for s in db.get_services()]
    assert [c.text() for c in win._service_cbs.values()] == [s["name"] for s in db.get_services()]


def test_cards_are_identity_logins_contact_other_services_notes(world):
    _, _, win = world
    titles = []
    for card in win._cards:
        # S-9: the card is a kit Card now; its title is natural case, shown upper-case by the font.
        titles.append(card.title().upper())
    assert titles[0] == "IDENTITY" and "PORTAL LOGINS" in titles
    assert titles[-2:] == ["ATTACHED SERVICES", "NOTES"]


def test_the_id_column_widget_is_kept_hidden(world):
    db, _, win = world
    idcols = [c for c in db.get_mcl_columns() if c.get("field_type") == "id"]
    for c in idcols:
        assert win._input_widgets[c["id"]][1].isHidden()


def test_selecting_a_client_fills_the_form_header_and_services(world):
    db, ids, win = world
    _select(win, ids["A"])
    assert win.selected_client_id == ids["A"]
    assert _field(win, "NAME OF COMPANY")[1].text() == "Alpha Traders Pvt Ltd"
    assert _field(win, "PAN")[1].text() == "ABCDE1001F"
    assert win.editor_title.text() == "Alpha Traders Pvt Ltd — Asha Verma"
    assert win.editor_avatar.text() == "AT"
    sub = win.editor_sub.text()
    assert sub.startswith(f"Client #{ids['A']}") and "Alpha Traders Pvt Ltd" in sub and "Updated " in sub
    assert win.f_notes.toPlainText() == "Alpha notes"
    sv = S.services_by_name(db)
    assert {sid for sid, cb in win._service_cbs.items() if cb.isChecked()} == {sv["GST"], sv["Income Tax"]}
    assert win.more_btn.isEnabled() and win.dirty_label.isHidden()


def test_the_new_client_form_is_blank_with_the_new_header(world):
    _, ids, win = world
    _select(win, ids["A"])
    win._on_new()
    assert win.selected_client_id is None and win.table.selectedIndexes() == []
    assert win.editor_title.text() == "New client" and win.editor_avatar.text() == "+"
    assert not win.more_btn.isEnabled()
    assert _field(win, "PAN")[1].text() == ""
    assert not any(cb.isChecked() for cb in win._service_cbs.values())


def test_ctrl_s_saves_and_ctrl_n_starts_a_new_client(world):
    _, ids, win = world
    assert win.new_btn.toolTip().endswith("(Ctrl+N)") and win.save_btn.toolTip().endswith("(Ctrl+S)")
    shortcuts = {s.key().toString(): s for s in win.findChildren(QShortcut)}
    assert shortcuts[QKeySequence(QKeySequence.Save).toString()].context() == Qt.WidgetWithChildrenShortcut
    assert shortcuts[QKeySequence(QKeySequence.New).toString()].context() == Qt.WidgetWithChildrenShortcut
    _select(win, ids["A"])
    shortcuts[QKeySequence(QKeySequence.New).toString()].activated.emit()
    assert win.selected_client_id is None
    _select(win, ids["A"])
    _field(win, "PH. NO.")[1].setText("9000000099")
    assert not win.dirty_label.isHidden()
    shortcuts[QKeySequence(QKeySequence.Save).toString()].activated.emit()
    assert win.db.get_client(ids["A"])["values"][_field(win, "PH. NO.")[0]["id"]] == "9000000099"


def test_ctrl_s_and_ctrl_n_key_presses_reach_the_shortcuts(world):
    _, ids, win = world
    _select(win, ids["A"])
    _field(win, "PH. NO.")[1].setFocus()
    S.pump()
    QTest.keyClick(_field(win, "PH. NO.")[1], Qt.Key_N, Qt.ControlModifier)
    assert win.selected_client_id is None


def test_editing_a_field_shows_unsaved_and_saving_clears_it(world, signals):
    db, ids, win = world
    _select(win, ids["B"])
    col, w = _field(win, "PH. NO.")
    w.setText("9000000077")
    assert not win.dirty_label.isHidden()
    win._on_save()
    assert win.dirty_label.isHidden()
    assert db.get_client(ids["B"])["values"][col["id"]] == "9000000077"
    assert signals["alerts"] == [("update", "Bravo Textiles LLP — Bhanu Rao")]
    assert win.selected_client_id == ids["B"]


def test_saving_an_existing_client_writes_values_notes_services_and_one_update_audit(world, signals):
    db, ids, win = world
    before = {l["id"] for l in db.get_audit_logs()}
    _select(win, ids["B"])
    sv = S.services_by_name(db)
    win._service_cbs[sv["Email"]].setChecked(True)
    win.f_notes.setPlainText("  fresh note  ")
    win._on_save()
    c = db.get_client(ids["B"])
    assert c["notes"] == "fresh note" and c["service_ids"] == [sv["Email"]]
    assert [a for a in _audit_actions(db, before) if a[1] == ids["B"]] == [("update", ids["B"])]
    last = next(l for l in db.get_audit_logs() if l["id"] not in before and l["action"] == "update")
    assert last["actor"] == "Admin" and last["detail"].startswith("Updated client profile CLI-")


def test_saving_a_new_client_creates_it_with_a_create_audit_and_selects_it(world, signals):
    db, ids, win = world
    before = {l["id"] for l in db.get_audit_logs()}
    win._on_new()
    _field(win, "NAME OF COMPANY")[1].setText("Echo Stores")
    _field(win, "PAN")[1].setText("ABCDE1005F")
    win.f_notes.setPlainText("n")
    sv = S.services_by_name(db)
    win._service_cbs[sv["GST"]].setChecked(True)
    win._on_save()
    new = [i for i in _listed(win) if i not in ids.values()]
    assert len(new) == 1
    c = db.get_client(new[0])
    assert c["notes"] == "n" and c["service_ids"] == [sv["GST"]]
    assert signals["alerts"] == [("create", "Echo Stores")]
    assert [a for a in _audit_actions(db, before) if a[0] == "create"] == [("create", new[0])]
    assert win.count_label.text() == "4 clients"


def test_a_duplicate_identity_asks_before_saving_and_no_saves_nothing(world, ask):
    db, ids, win = world
    win._on_new()
    _field(win, "NAME OF COMPANY")[1].setText("Echo Mills Pvt Ltd")
    _field(win, "NAME OF PROPRIETOR")[1].setText("Asha Verma")
    _field(win, "PAN")[1].setText("ABCDE1008F")
    ask["answers"] = [NO]
    win._on_save()
    assert ask["asked"] == ["Possible duplicate"]
    assert len(db.search_clients("")) == 3
    ask["answers"] = [YES]
    win._on_save()
    assert len(db.search_clients("")) == 4


def test_a_taken_primary_key_or_a_missing_one_shows_the_validation_error(world, ask):
    db, ids, win = world
    win._on_new()
    _field(win, "NAME OF COMPANY")[1].setText("No Pan Co")
    win._on_save()
    assert [w[0] for w in ask["warned"]] == ["Validation Error"]
    _field(win, "PAN")[1].setText("ABCDE1001F")
    ask["answers"] = [YES]
    win._on_save()
    assert [w[0] for w in ask["warned"]] == ["Validation Error", "Validation Error"]
    assert len(db.search_clients("")) == 3


def test_save_with_several_selected_only_toasts(world, signals):
    db, ids, win = world
    _select(win, ids["A"], ids["B"])
    win._on_save()
    assert signals["toasts"] == ["The edit form only applies to one client at a time. Use the bulk buttons for bulk actions."]


def test_passwords_are_masked_with_an_eye_and_a_copy_action(world):
    _, ids, win = world
    _select(win, ids["A"])
    col, w = _field(win, "GST_Password")
    assert w.echoMode() == QLineEdit.Password
    tips = [a.toolTip() for a in w.actions()]
    assert "Show / hide" in tips and "Copy (clipboard clears after 30 s)" in tips
    eye = next(a for a in w.actions() if a.toolTip() == "Show / hide")
    eye.trigger()
    assert w.echoMode() == QLineEdit.Normal and w.text() == S.GST_PW_A
    win._on_row_selected()
    win._mask_secrets()
    assert w.echoMode() == QLineEdit.Password
    _, user = _field(win, "USER ID")
    assert [a.toolTip() for a in user.actions() if a.toolTip().startswith("Copy")] == ["Copy (clipboard clears after 30 s)"]


def test_copying_a_password_copies_the_real_value_logs_it_and_wipes_after_30s(world, signals, monkeypatch):
    db, ids, win = world
    timers = []
    monkeypatch.setattr(aw.QTimer, "singleShot", staticmethod(lambda ms, fn: timers.append((ms, fn))))
    _select(win, ids["A"])
    col, w = _field(win, "GST_Password")
    copy = next(a for a in w.actions() if a.toolTip().startswith("Copy"))
    copy.trigger()
    assert QApplication.clipboard().text() == S.GST_PW_A
    assert signals["toasts"] == ["Copied GST_Password — clears in 30 s"]
    assert timers[-1][0] == 30000
    logged = [l for l in db.get_audit_logs() if l["action"] == "manual_copy"]
    assert logged[0]["client_id"] == ids["A"] and logged[0]["detail"] == "Copied field from Manage Clients: GST_Password"
    QApplication.clipboard().setText("something else")
    timers[-1][1]()
    assert QApplication.clipboard().text() == "something else"        # only wiped while it still holds the password
    QApplication.clipboard().setText(S.GST_PW_A)
    timers[-1][1]()
    assert QApplication.clipboard().text() == ""


def test_copying_an_empty_field_only_toasts(world, signals):
    _, ids, win = world
    _select(win, ids["B"])
    _, w = _field(win, "GST_Password")
    next(a for a in w.actions() if a.toolTip().startswith("Copy")).trigger()
    assert signals["toasts"] == ["GST_Password is empty."]


def test_new_client_dialog_builds_from_the_master_columns_and_creates(tmp_path, ask):
    db = S.make_db(tmp_path)
    S.seed_clients(db)
    dlg = NewClientDialog(db)
    dlg.show()
    dlg.actor = "Admin"
    assert set(dlg._input_widgets) == {c["id"] for c in db.get_mcl_columns()}
    assert list(dlg._service_cbs) == [s["id"] for s in db.get_services()]
    created = []
    dlg.client_created.connect(lambda: created.append(1))
    _, name = next(v for v in dlg._input_widgets.values() if v[0]["label"] == "NAME OF COMPANY")
    _, pan = next(v for v in dlg._input_widgets.values() if v[0]["label"] == "PAN")
    name.setText("Foxtrot Gas")
    pan.setText("ABCDE1006F")
    dlg._service_cbs[S.services_by_name(db)["Email"]].setChecked(True)
    dlg.f_notes.setPlainText("hello")
    dlg._on_create()
    assert created == [1] and dlg.result() == QDialog.Accepted
    new = [c for c in db.search_clients("Foxtrot")]
    assert len(new) == 1 and db.get_client(new[0]["id"])["notes"] == "hello"
    assert [l for l in db.get_audit_logs() if l["action"] == "create"][0]["actor"] == "Admin"
    S.dispose(dlg)


# ---- R7.3 archive, restore, purge, bulk service ---------------------------------------------

def test_archive_asks_first_then_archives_only_the_selected_client(world, ask, signals):
    db, ids, win = world
    _select(win, ids["B"])
    ask["answers"] = [NO]
    win._on_archive()
    assert ask["asked"] == ["Archive client"] and _listed(win) == [ids["A"], ids["B"], ids["C"]]
    ask["answers"] = [YES]
    win._on_archive()
    assert _listed(win) == [ids["A"], ids["C"]]
    assert db.get_client(ids["B"])["is_archived"] and not db.get_client(ids["A"])["is_archived"]
    assert signals["alerts"] == [("archive", "")]


def test_the_more_menu_actions_and_the_bulk_bar_run_the_same_handlers(world, ask):
    _, ids, win = world
    ask["answers"] = [YES]
    _select(win, ids["A"], ids["C"])
    assert not win.bulk_bar.isHidden() and win.selection_label.text() == "2 selected"
    assert win.editor_title.text() == "2 clients selected" and not win.save_btn.isEnabled()
    assert not win.more_btn.isEnabled() and not win.form_widget.isEnabled()
    win.bulk_archive_btn.click()
    assert ask["asked"] == ["Archive clients"] and _listed(win) == [ids["B"]]


def test_restore_unarchives_without_asking_and_alerts(world, ask, signals):
    db, ids, win = world
    _chip(win, "Archived").click()
    _select(win, ids["D"])
    win.act_restore.trigger()
    assert ask["asked"] == [] and signals["alerts"] == [("unarchive", "")]
    assert not db.get_client(ids["D"])["is_archived"]


def test_purge_needs_a_no_default_confirmation_and_deletes_only_the_selection(world, ask):
    db, ids, win = world
    _chip(win, "Archived").click()
    _select(win, ids["D"])
    ask["answers"] = [NO]
    win._on_purge()
    assert ask["asked"] == ["Delete permanently"] and db.get_client(ids["D"])
    ask["answers"] = [YES]
    win.act_purge.trigger()
    assert db.get_client(ids["D"]) is None
    assert {c["id"] for c in db.search_clients("")} == {ids["A"], ids["B"], ids["C"]}


def test_bulk_purge_button_deletes_every_selected_row(world, ask):
    db, ids, win = world
    cols = S.cols_by_label(db)
    extra = db.add_client({cols["NAME OF COMPANY"]: "Foxtrot Steel LLP", cols["PAN"]: "ABCDE1007F"}, "", [])
    db.archive_client(extra, actor="Admin")
    _chip(win, "Archived").click()
    _select(win, ids["D"], extra)
    ask["answers"] = [YES]
    win.bulk_purge_btn.click()
    assert db.get_client(ids["D"]) is None and db.get_client(extra) is None


def test_the_delete_from_search_entry_confirms_with_the_identity(world, monkeypatch):
    db, ids, win = world
    seen = []
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda p, t, text, *a, **k: (seen.append((t, text)), NO)[1]))
    win.delete_client(ids["B"])
    assert seen == [("Delete client", 'Permanently delete "Bravo Textiles LLP — Bhanu Rao"? This cannot be undone.')]
    assert db.get_client(ids["B"])
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: YES))
    win.delete_client(ids["B"])
    assert db.get_client(ids["B"]) is None


def test_open_client_editor_selects_that_client_and_new_client_form_is_blank(world):
    _, ids, win = world
    assert win.open_client_editor(ids["C"]) is True and win.selected_client_id == ids["C"]
    assert win.open_client_editor(999999) is False
    win.open_new_client_form()
    assert win.selected_client_id is None


def _service_answers(monkeypatch, name, attach_answer):
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: (name, True)))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: attach_answer))


def test_bulk_service_attaches_then_detaches_for_every_selected_client(world, monkeypatch):
    db, ids, win = world
    sv = S.services_by_name(db)
    _select(win, ids["B"], ids["C"])
    _service_answers(monkeypatch, "Email", YES)
    win._on_bulk_service()
    assert sv["Email"] in db.get_client(ids["B"])["service_ids"] and sv["Email"] in db.get_client(ids["C"])["service_ids"]
    _select(win, ids["B"], ids["C"])
    _service_answers(monkeypatch, "Email", NO)
    win._on_bulk_service()
    assert db.get_client(ids["B"])["service_ids"] == [] and db.get_client(ids["C"])["service_ids"] == []
    assert db.get_client(ids["A"])["service_ids"] == [sv["GST"], sv["Income Tax"]]


def test_bulk_service_cancelled_changes_nothing_and_needs_a_selection(world, monkeypatch, signals):
    db, ids, win = world
    win._on_bulk_service()
    assert signals["toasts"] == ["Select one or more clients first."]
    _select(win, ids["B"], ids["C"])
    _service_answers(monkeypatch, "GST", QMessageBox.Cancel)
    win._on_bulk_service()
    assert db.get_client(ids["B"])["service_ids"] == []
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("", False)))
    win._on_bulk_service()
    assert db.get_client(ids["C"])["service_ids"] == [S.services_by_name(db)["Email"]]


def test_manage_client_services_selects_the_client_then_opens_the_service_flow(world, monkeypatch):
    db, ids, win = world
    opened = []
    monkeypatch.setattr(win, "_on_bulk_service", lambda: opened.append(win.selected_client_id))
    win.manage_client_services(ids["B"])
    assert opened == [ids["B"]]
    opened.clear()
    win.manage_client_services(999999)
    assert opened == []


def test_admin_pin_first_run_sets_it_and_later_runs_check_it(tmp_path, monkeypatch):
    db = S.make_db(tmp_path)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda p, t, m, *a, **k: warned.append(t)))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda p, t, m, *a, **k: warned.append(t)))
    first = AdminPinDialog(db)
    assert first._is_first_run and first.pin_input.echoMode() == QLineEdit.Password
    first.pin_input.setText("123")
    first._on_accept()
    assert warned == ["Too Short"] and db.get_setting("admin_pin_hash") is None
    first.pin_input.setText("4711")
    first._on_accept()
    assert first.result() == QDialog.Accepted
    assert security.verify_admin_pin("4711", db.get_setting("admin_pin_hash"))
    again = AdminPinDialog(db)
    assert not again._is_first_run
    again.pin_input.setText("0000")
    again._on_accept()
    assert warned[-1] == "Incorrect PIN" and again.pin_input.text() == "" and again.result() != QDialog.Accepted
    again.pin_input.setText("4711")
    again._on_accept()
    assert again.result() == QDialog.Accepted
    S.dispose(first, again)


# ---- R7.4 every toolbar action is still reachable (now via the sidebar / settings pages) ------

@pytest.mark.parametrize("handler,page", [
    ("_on_backup", "backup"), ("_on_restore_backup", "backup"), ("_on_export_csv", "export"),
    ("_on_download_template", "export"), ("_on_manage_mcl", "mcl"), ("_on_manage_services", "services"),
    ("_on_open_settings", "general"), ("_on_purge_duplicates", "purge"),
])
def test_toolbar_handlers_open_the_settings_dialog_on_their_page(world, monkeypatch, handler, page):
    _, _, win = world
    opened = []

    class Dlg:
        def __init__(self, db, actor=None, page=None, parent=None):
            opened.append(page)
            self.toast_requested = type("S", (), {"connect": lambda *a: None})()
            self.settings_saved = self.toast_requested

        def exec(self):
            return 0
    import ui.dialogs.unified_settings_dialog as usd
    monkeypatch.setattr(usd, "UnifiedSettingsDialog", Dlg)
    getattr(win, handler)()
    assert opened == [page]


def test_import_csv_audit_conflicts_and_sync_handlers_exist_and_sync_needs_the_service(world, monkeypatch):
    _, _, win = world
    for name in ("_on_import_csv", "_on_view_audit_log", "_on_inspect_conflicts", "_on_open_sera_sync"):
        assert callable(getattr(win, name))
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda p, t, m, *a, **k: warned.append(t)))
    win._on_open_sera_sync()
    assert warned == ["Sera Sync"]


def test_inspect_conflicts_with_none_toasts_and_the_banner_follows_the_database(world, monkeypatch, signals):
    db, _, win = world
    win._on_inspect_conflicts()
    assert signals["toasts"] == ["No Syncthing sync conflict files detected."]
    assert win.conflict_banner_widget.isHidden()
    monkeypatch.setattr(db, "get_sync_conflicts", lambda: ["x/master (conflict).db"])
    win._check_sync_conflicts()
    assert not win.conflict_banner_widget.isHidden() and "1 conflict file)" in win.conflict_label.text()


def test_back_button_and_page_widgets_keep_their_names(world):
    _, _, win = world
    got = []
    win.back_requested.connect(lambda: got.append(1))
    btn = next(b for b in win.findChildren(aw.QPushButton) if b.toolTip() == "Back to Search (Esc)")
    btn.click()
    assert got == [1]
    assert win.objectName() == "ManageClientsPage"
    for attr in ("new_btn", "save_btn", "more_btn", "search_input", "table", "bulk_bar", "filter_combo",
                 "show_archived_cb", "sort_combo", "count_label", "dirty_label", "f_notes"):
        assert hasattr(win, attr), attr
