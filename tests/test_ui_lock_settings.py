"""Behaviour lock R3 (Settings): every setting written for fixed inputs, visibility saves, data tools."""
import json
import pathlib

import ui_lock_support as S

import pytest
from PySide6.QtWidgets import QCheckBox

from ui.dialogs import unified_settings_dialog as usd

GOLDEN = json.loads((pathlib.Path(__file__).parent / "data" / "ui_lock_settings_golden.json").read_text("utf-8"))

SETTINGS_PAGES = (usd._P_GENERAL, usd._P_ACTIONS, usd._P_SCC, usd._P_TRACKER,
                  usd._P_MAIN_VIS, usd._P_QC, usd._P_ADMIN_VIS)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A dialog with every settings page built; the two outside effects of Save are captured."""
    db = S.make_db(tmp_path)
    ids = S.seed_clients(db)
    outside = {"autostart": [], "extension": []}

    from ui.utils import autostart
    monkeypatch.setattr(autostart, "is_autostart_enabled", lambda: False)
    monkeypatch.setattr(autostart, "set_autostart_enabled", lambda on: outside["autostart"].append(on))
    import automation
    monkeypatch.setattr(automation, "update_extension_settings", lambda **kw: outside["extension"].append(kw))

    written = []
    real_bulk = db.set_settings_bulk
    db.set_settings_bulk = lambda d: (written.append(dict(d)), real_bulk(d))[1]

    dlg = usd.UnifiedSettingsDialog(db, actor="Admin")
    for page in SETTINGS_PAGES:
        dlg._switch_page(page)
    dlg._switch_page(usd._P_GENERAL)
    yield db, ids, dlg, written, outside
    S.dispose(dlg)


def _combo_pick(combo, data):
    idx = combo.findData(data)
    assert idx >= 0, data
    combo.setCurrentIndex(idx)


def _apply_fixed_inputs(d):
    _combo_pick(d.window_mode_combo, "rectangular")
    _combo_pick(d.mask_mode_combo, "first_n")
    d.reveal_count_spin.setValue(3)
    d.clipboard_spin.setValue(45)
    d.quick_copy_check.setChecked(True)
    d.run_in_bg_check.setChecked(False)
    d.autostart_check.setChecked(True)
    d.sca_check.setChecked(False)
    _combo_pick(d.sca_mode_combo, "widget")
    d.sca_max_uses_spin.setValue(5)
    d.scc_check.setChecked(False)
    _combo_pick(d.scc_detect_combo, "on")
    d.scc_opt1_label_edit.setText("  Combo A  ")
    d.scc_opt1_str_edit.setText("@#")
    d.scc_opt2_label_edit.setText("")
    d.scc_opt2_str_edit.setText("-x-")
    d.scc_opt3_label_edit.setText("Third")
    d.scc_opt3_str_edit.setText("p3")
    d.scc_opt4_label_edit.setText("Fourth")
    d.scc_opt4_str_edit.setText("p4")
    d.show_hide_check.setChecked(False)
    d.vsdc_check.setChecked(True)
    d.vsdc_x_check.setChecked(True)
    d.vsdc247_check.setChecked(True)
    d.vsdc_hud_check.setChecked(False)
    _combo_pick(d.sgt_mode_combo, "shadow")
    d.sgt_record_check.setChecked(False)
    d.sdis_record_check.setChecked(False)
    _combo_pick(d.sgt_i_combo, "on")


def test_saving_untouched_defaults_writes_exactly_the_golden_settings(env):
    db, ids, dlg, written, outside = env
    assert not dlg._btn_save.isEnabled()
    dlg._on_save_settings()
    assert written == [GOLDEN["defaults_saved_without_touching_anything"]]
    assert db.get_all_settings() == GOLDEN["defaults_saved_without_touching_anything"]


def test_fixed_sequence_of_inputs_writes_exactly_the_golden_settings(env):
    db, ids, dlg, written, outside = env
    _apply_fixed_inputs(dlg)
    assert dlg._btn_save.isEnabled()
    dlg._btn_save.click()
    assert written == [GOLDEN["fixed_sequence_of_inputs"]]
    assert db.get_all_settings() == GOLDEN["fixed_sequence_of_inputs"]
    assert not dlg._btn_save.isEnabled()                               # saved = no longer dirty


def test_save_also_tells_the_extension_and_the_startup_entry(env):
    db, ids, dlg, written, outside = env
    _apply_fixed_inputs(dlg)
    dlg._on_save_settings()
    assert outside["autostart"] == [True]
    assert len(outside["extension"]) == 1
    ext = outside["extension"][0]
    assert ext["sca_enabled"] is False and ext["sca_mode"] == "widget"
    assert ext["sca_max_uses"] == 5 and ext["clipboard_clear_seconds"] == 45
    assert [s["name"] for s in ext["allowed_services"]] == ["GST", "Income Tax", "Email"]


def test_save_toasts_signals_and_writes_one_audit_entry(env):
    db, ids, dlg, written, outside = env
    toasts, saved = [], []
    dlg.toast_requested.connect(lambda m, ms: toasts.append(m))
    dlg.settings_saved.connect(lambda: saved.append(1))
    dlg.clipboard_spin.setValue(60)
    dlg._on_save_settings()
    assert toasts == ["Settings saved successfully!"] and saved == [1]
    logs = [l for l in db.get_audit_logs() if l["action"] == "update_settings"]
    assert len(logs) == 1 and logs[0]["actor"] == "Admin"
    assert logs[0]["detail"] == "Saved via Unified Settings Hub"


def test_a_saved_value_is_what_the_next_dialog_shows(env):
    db, ids, dlg, written, outside = env
    _apply_fixed_inputs(dlg)
    dlg._on_save_settings()
    again = usd.UnifiedSettingsDialog(db, actor="Admin")
    try:
        for page in SETTINGS_PAGES:
            again._switch_page(page)
        assert again.window_mode_combo.currentData() == "rectangular"
        assert again.mask_mode_combo.currentData() == "first_n"
        assert (again.reveal_count_spin.value(), again.clipboard_spin.value()) == (3, 45)
        assert again.sca_mode_combo.currentData() == "widget" and again.sca_max_uses_spin.value() == 5
        assert again.scc_opt1_label_edit.text() == "Combo A" and again.scc_opt2_label_edit.text() == "Combo 2"
        assert again.sgt_mode_combo.currentData() == "shadow" and again.sgt_i_combo.currentData() == "on"
        assert again._capture_state() == again._initial_state
    finally:
        S.dispose(again)


# ---- visibility pages -----------------------------------------------------------------------

def test_visibility_pages_save_per_column(env):
    db, ids, dlg, written, outside = env
    cols = S.cols_by_label(db)
    assert set(dlg.vis_cbs) == set(dlg.qc_cbs) == set(dlg.admin_vis_cbs) == set(cols.values())
    main_on = {cols["NAME OF COMPANY"], cols["PAN"]}
    qc_on = {cols["PAN"], cols["GSTIN"], cols["GST_Password"]}
    admin_on = {cols["NAME OF COMPANY"], cols["NAME OF PROPRIETOR"], cols["PH. NO."]}
    for cid, cb in dlg.vis_cbs.items():
        cb.setChecked(cid in main_on)
    for cid, cb in dlg.qc_cbs.items():
        cb.setChecked(cid in qc_on)
    for cid, cb in dlg.admin_vis_cbs.items():
        cb.setChecked(cid in admin_on)
    assert dlg._btn_save.isEnabled()
    dlg._on_save_settings()
    got = {c["id"]: c for c in db.get_mcl_columns()}
    assert {i for i, c in got.items() if c["show_in_search"]} == main_on
    assert {i for i, c in got.items() if c["allow_quick_copy"]} == qc_on
    assert {i for i, c in got.items() if c["admin_show_in_search"]} == admin_on


def test_visibility_pages_open_on_the_saved_state(env):
    db, ids, dlg, written, outside = env
    cols = S.cols_by_label(db)
    db.bulk_update_mcl_visibility([cols["PAN"]])
    db.bulk_update_mcl_quick_copy([cols["GSTIN"]])
    db.bulk_update_mcl_admin_visibility([cols["EMAIL"]])
    again = usd.UnifiedSettingsDialog(db, actor="Admin")
    try:
        for page in (usd._P_MAIN_VIS, usd._P_QC, usd._P_ADMIN_VIS):
            again._switch_page(page)
        assert [i for i, cb in again.vis_cbs.items() if cb.isChecked()] == [cols["PAN"]]
        assert [i for i, cb in again.qc_cbs.items() if cb.isChecked()] == [cols["GSTIN"]]
        assert [i for i, cb in again.admin_vis_cbs.items() if cb.isChecked()] == [cols["EMAIL"]]
        assert all(isinstance(cb, QCheckBox) for cb in again.vis_cbs.values())
    finally:
        S.dispose(again)


def test_main_screen_visibility_reaches_the_search_grid_after_save(env):
    db, ids, dlg, written, outside = env
    cols = S.cols_by_label(db)
    for cid, cb in dlg.vis_cbs.items():
        cb.setChecked(cid in (cols["NAME OF COMPANY"], cols["PAN"]))
    dlg._on_save_settings()
    from ui.windows.search_window import SearchWindow
    win = SearchWindow(db)
    try:
        heads = [win.results_table.horizontalHeaderItem(i).text() for i in range(win.results_table.columnCount())]
        assert heads == ["NAME OF COMPANY", "PAN", "Services"]
    finally:
        S.dispose(win)


# ---- navigation -----------------------------------------------------------------------------

def test_every_settings_entry_is_there_in_order_and_opens_its_page(env):
    db, ids, dlg, written, outside = env
    labels = [item.text() for item, _ in dlg._nav_items]
    assert labels == ["General", "Action Buttons", "SCC Vault Presets", "Tracker", "Master Column List", "Services",
                      "Main Screen", "Quick-Copy Fields", "Admin Screen", "Export CSV", "Backup & Restore",
                      "Purge Duplicates"]
    for item, idx in dlg._nav_items:
        item.clicked.emit()
        assert dlg._stack.currentWidget() is dlg._page_widgets[idx]
        assert dlg._btn_save.isVisibleTo(dlg) == (idx in usd._SETTINGS_PAGES)   # Save only on settings pages


def test_search_box_filters_the_entries(env):
    db, ids, dlg, written, outside = env
    dlg._search.setText("copy")
    shown = [item.text() for item, _ in dlg._nav_items if not item.isHidden()]
    assert shown == ["Quick-Copy Fields"]
    dlg._search.setText("")
    assert all(not item.isHidden() for item, _ in dlg._nav_items)


@pytest.mark.parametrize("name, idx", [("general", usd._P_GENERAL), ("scc", usd._P_SCC), ("tracker", usd._P_TRACKER),
                                       ("mcl", usd._P_MCL), ("services", usd._P_SERVICES)])
def test_opening_on_a_named_page(tmp_path, name, idx):
    db = S.make_db(tmp_path)
    dlg = usd.UnifiedSettingsDialog(db, actor="Admin", page=name)
    try:
        assert dlg._stack.currentWidget() is dlg._page_widgets[idx]
    finally:
        S.dispose(dlg)


def test_unsaved_dialog_closes_without_writing(env, monkeypatch):
    db, ids, dlg, written, outside = env
    dlg.clipboard_spin.setValue(99)
    monkeypatch.setattr(usd.QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(usd.QMessageBox, "clickedButton",
                        lambda self: next(b for b in self.buttons() if b.text() == "Discard changes"))
    dlg.reject()
    assert written == [] and db.get_setting("clipboard_clear_seconds") is None


# ---- data tools -----------------------------------------------------------------------------

def test_purge_asks_first_and_deletes_only_the_newer_duplicate(env, monkeypatch):
    db, ids, dlg, written, outside = env
    c = S.cols_by_label(db)
    # the same firm typed differently: the key checks are exact, the purge compares letters and digits only
    dup = db.add_client({c["NAME OF COMPANY"]: "ALPHA TRADERS PVT. LTD.", c["NAME OF PROPRIETOR"]: "asha  verma",
                         c["PAN"]: "ABCDE1009F"}, "", [])
    asked, info = [], []
    answer = {"v": usd.QMessageBox.No}
    monkeypatch.setattr(usd.QMessageBox, "question", lambda *a, **k: (asked.append(a[1]), answer["v"])[1])
    monkeypatch.setattr(usd.QMessageBox, "information", lambda *a, **k: info.append(a[2]))
    dlg._on_purge_duplicates()
    assert asked == ["Purge Duplicate Clients"] and db.get_client(dup) is not None   # "No" changes nothing
    answer["v"] = usd.QMessageBox.Yes
    dlg._on_purge_duplicates()
    assert db.get_client(dup) is None and db.get_client(ids["A"]) is not None        # oldest kept
    assert "Deleted 1 duplicate client(s) across 1 group(s)" in info[0]
    logs = [l for l in db.get_audit_logs() if l["action"] == "purge_duplicates"]
    assert [l["detail"] for l in logs] == ["Purged 1 duplicate(s) across 1 group(s)"]


def test_purge_with_nothing_to_purge_only_toasts(env, monkeypatch):
    db, ids, dlg, written, outside = env
    toasts = []
    dlg.toast_requested.connect(lambda m, ms: toasts.append(m))
    monkeypatch.setattr(usd.QMessageBox, "question", lambda *a, **k: usd.QMessageBox.Yes)
    dlg._on_purge_duplicates()
    assert toasts == ["No duplicate clients were found."]
    assert [l for l in db.get_audit_logs() if l["action"] == "purge_duplicates"] == []


def test_backup_copies_the_database_and_logs_it(env, monkeypatch, tmp_path):
    db, ids, dlg, written, outside = env
    dest = tmp_path / "bk"
    dest.mkdir()
    choice = {"v": ""}
    monkeypatch.setattr(usd.QFileDialog, "getExistingDirectory", lambda *a, **k: choice["v"])
    dlg._on_backup()                                                   # cancelled folder picker: nothing happens
    assert list(dest.iterdir()) == []
    assert [l for l in db.get_audit_logs() if l["action"] == "backup"] == []
    choice["v"] = str(dest)
    dlg._on_backup()
    assert len(list(dest.iterdir())) == 1
    logs = [l for l in db.get_audit_logs() if l["action"] == "backup"]
    assert len(logs) == 1 and logs[0]["detail"].startswith("Backed up to ")


def test_restore_is_never_run_when_the_source_question_is_cancelled(env, monkeypatch):
    db, ids, dlg, written, outside = env
    called = []
    db.restore_from = lambda p: called.append(p)
    monkeypatch.setattr(usd.QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(usd.QMessageBox, "clickedButton", lambda self: None)
    dlg._on_restore_backup()
    assert called == []


def test_restore_needs_the_overwrite_confirmation(env, monkeypatch):
    db, ids, dlg, written, outside = env
    called, warned = [], []
    db.restore_from = lambda p: called.append(p) or "restored"
    monkeypatch.setattr(usd.QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(usd.QMessageBox, "clickedButton", lambda self: self.buttons()[1])
    monkeypatch.setattr(usd.QFileDialog, "getOpenFileName", lambda *a, **k: ("C:/fake/backup.db", ""))
    monkeypatch.setattr(usd.QMessageBox, "warning", lambda *a, **k: (warned.append(a[1]), usd.QMessageBox.No)[1])
    dlg._on_restore_backup()
    assert warned == ["Confirm Restore"] and called == []


def test_export_csv_writes_every_client_including_archived_and_logs_it(env, monkeypatch, tmp_path):
    db, ids, dlg, written, outside = env
    out = tmp_path / "clients_export.csv"
    monkeypatch.setattr(usd.QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    toasts = []
    dlg.toast_requested.connect(lambda m, ms: toasts.append(m))
    dlg._on_export_csv()
    import csv
    rows = list(csv.reader(out.read_text("utf-8-sig").splitlines()))
    assert "NAME OF COMPANY" in rows[0]
    company = rows[0].index("NAME OF COMPANY")
    # The page says "active client records" but archived clients are exported too (flagged in "Is Archived").
    assert sorted(r[company] for r in rows[1:]) == ["Alpha Traders Pvt Ltd", "Bravo Textiles LLP", "Charlie Foods", "Delta Clinic"]
    archived = rows[0].index("Is Archived")
    assert {r[company]: r[archived] for r in rows[1:]}["Delta Clinic"] == "Yes"
    assert toasts and "Exported successfully" in toasts[0]
    assert [l["detail"] for l in db.get_audit_logs() if l["action"] == "csv_export"] == [f"Exported to {out}"]


def test_cancelled_export_writes_nothing(env, monkeypatch, tmp_path):
    db, ids, dlg, written, outside = env
    monkeypatch.setattr(usd.QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
    dlg._on_export_csv()
    assert [l for l in db.get_audit_logs() if l["action"] == "csv_export"] == []


def test_template_download_has_the_column_labels(env, monkeypatch, tmp_path):
    db, ids, dlg, written, outside = env
    out = tmp_path / "template.csv"
    monkeypatch.setattr(usd.QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    dlg._on_download_template()
    first = out.read_text("utf-8-sig").splitlines()[0]
    for label in ("NAME OF COMPANY", "PAN", "GSTIN"):
        assert label in first
