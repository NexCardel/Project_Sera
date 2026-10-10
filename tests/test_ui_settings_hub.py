"""S-1: Settings hub shell - unsaved bar, dirty count, Discard, close prompt, compact mode, nav groups."""
import ui_lock_support as S

import pytest

from ui.dialogs import unified_settings_dialog as usd

SETTINGS_PAGES = (usd._P_GENERAL, usd._P_ACTIONS, usd._P_SCC, usd._P_TRACKER,
                  usd._P_MAIN_VIS, usd._P_QC, usd._P_ADMIN_VIS)


@pytest.fixture
def dlg(tmp_path, monkeypatch):
    db = S.make_db(tmp_path)
    S.seed_clients(db)
    from ui.utils import autostart
    monkeypatch.setattr(autostart, "is_autostart_enabled", lambda: False)
    d = usd.UnifiedSettingsDialog(db, actor="Admin")
    for page in SETTINGS_PAGES:
        d._switch_page(page)
    d._switch_page(usd._P_GENERAL)
    yield d
    S.dispose(d)


def _answer(monkeypatch, choice):
    seen = []
    monkeypatch.setattr(usd.QMessageBox, "exec", lambda self: seen.append(self.text()) or 0)
    monkeypatch.setattr(usd.QMessageBox, "clickedButton",
                        lambda self: next(b for b in self.buttons() if b.text() == choice))
    return seen


def test_clean_dialog_has_no_unsaved_changes(dlg):
    assert dlg._dirty_count() == 0
    assert not dlg._btn_save.isEnabled()
    assert dlg._unsaved.count() == 0 and dlg._unsaved.label.isHidden()


def test_each_edit_is_counted_and_undoing_it_clears_the_count(dlg):
    dlg.clipboard_spin.setValue(dlg.clipboard_spin.value() + 5)
    assert dlg._dirty_count() == 1 and dlg._btn_save.isEnabled()
    dlg.quick_copy_check.setChecked(not dlg.quick_copy_check.isChecked())
    assert dlg._dirty_count() == 2
    dlg.clipboard_spin.setValue(dlg.clipboard_spin.value() - 5)
    dlg.quick_copy_check.setChecked(not dlg.quick_copy_check.isChecked())
    assert dlg._dirty_count() == 0 and not dlg._btn_save.isEnabled()


def test_the_unsaved_bar_follows_the_count(dlg):
    dlg.sca_max_uses_spin.setValue(7)
    dlg.autostart_check.setChecked(True)
    bar = dlg._unsaved
    assert bar.count() == 2 and bar.label.text() == "2 unsaved changes" and not bar.save_btn.isHidden()
    bar.discard_btn.click()
    assert bar.count() == 0 and dlg._dirty_count() == 0


def test_discard_restores_every_page_including_the_visibility_boxes(dlg):
    before = dlg._capture_state()
    dlg.clipboard_spin.setValue(250)
    dlg.mask_mode_combo.setCurrentIndex((dlg.mask_mode_combo.currentIndex() + 1) % dlg.mask_mode_combo.count())
    dlg.scc_opt1_label_edit.setText("changed")
    cid, box = next(iter(dlg.vis_cbs.items()))
    box.setChecked(not box.isChecked())
    assert dlg._dirty_count() == 4
    dlg._on_discard()
    assert dlg._capture_state() == before and dlg._dirty_count() == 0


def test_discard_writes_nothing(dlg):
    written = []
    dlg.db.set_settings_bulk = lambda d: written.append(d)
    dlg.clipboard_spin.setValue(250)
    dlg._on_discard()
    assert written == []


def test_closing_clean_does_not_ask(dlg, monkeypatch):
    seen = _answer(monkeypatch, "Keep editing")
    dlg.reject()
    assert seen == []


def test_close_prompt_keep_editing_stays_open_with_the_edit(dlg, monkeypatch):
    dlg.clipboard_spin.setValue(250)
    seen = _answer(monkeypatch, "Keep editing")
    closed = []
    monkeypatch.setattr(usd.QDialog, "reject", lambda self: closed.append(True))
    dlg.reject()
    assert len(seen) == 1 and closed == [] and dlg.clipboard_spin.value() == 250


def test_close_prompt_discard_closes_and_writes_nothing(dlg, monkeypatch):
    dlg.clipboard_spin.setValue(250)
    written = []
    dlg.db.set_settings_bulk = lambda d: written.append(d)
    _answer(monkeypatch, "Discard changes")
    closed = []
    monkeypatch.setattr(usd.QDialog, "reject", lambda self: closed.append(True))
    dlg.reject()
    assert closed == [True] and written == []


def test_close_prompt_save_writes_then_closes(dlg, monkeypatch):
    dlg.clipboard_spin.setValue(250)
    _answer(monkeypatch, "Save changes")
    closed = []
    monkeypatch.setattr(usd.QDialog, "reject", lambda self: closed.append(True))
    monkeypatch.setattr(usd.QMessageBox, "information", staticmethod(lambda *a, **k: 0))
    dlg.reject()
    assert closed == [True] and dlg.db.get_setting("clipboard_clear_seconds") == "250"


def test_compact_follows_the_pane_width_with_a_gap(dlg):
    dlg.resize(650 + usd.TOKENS["w_setnav"] + 1, 500)
    dlg._sync_compact()
    assert dlg._compact is True
    dlg.resize(720 + usd.TOKENS["w_setnav"] + 1, 500)          # inside the gap: no flip back
    dlg._sync_compact()
    assert dlg._compact is True
    dlg.resize(800 + usd.TOKENS["w_setnav"] + 1, 500)
    dlg._sync_compact()
    assert dlg._compact is False


def test_pages_built_while_compact_start_compact(tmp_path, monkeypatch):
    db = S.make_db(tmp_path)
    d = usd.UnifiedSettingsDialog(db, actor="Admin")
    try:
        d._compact = True
        d._switch_page(usd._P_ACTIONS)
        assert d._compact is True
    finally:
        S.dispose(d)


def test_search_hides_empty_group_labels(dlg):
    dlg._search.setText("copy")
    visible = [lbl.text() for lbl, items in dlg._nav_groups if not lbl.isHidden()]
    assert [v.lower() for v in visible] == ["visibility"]
    dlg._search.setText("")
    assert all(not lbl.isHidden() for lbl, _ in dlg._nav_groups)


def test_masking_preview_follows_the_two_controls(dlg):
    dlg.mask_mode_combo.setCurrentIndex(dlg.mask_mode_combo.findData("first_n"))
    dlg.reveal_count_spin.setValue(3)
    assert "Abw" in dlg._mask_row.sub_label.text() and "Example" in dlg._mask_row.sub_label.text()
    dlg.mask_mode_combo.setCurrentIndex(dlg.mask_mode_combo.findData("full_dots"))
    assert "Abw" not in dlg._mask_row.sub_label.text()
