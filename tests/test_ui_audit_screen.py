"""S-11: the Audit log screen on the kit (arrangement only; behaviour is pinned by test_ui_lock_audit)."""
import re
from pathlib import Path

import ui_lock_support as S

import pytest
from PySide6.QtCore import Qt

from ui.components.kit import DarkListTable, DataGrid, Pill
from ui.dialogs import audit_log_dialog as ald
from ui.dialogs.audit_log_dialog import AuditLogDialog
from ui.utils.responsive import WidthMode


@pytest.fixture
def win(tmp_path):
    db = S.make_db(tmp_path)
    S.seed_clients(db)
    w = AuditLogDialog(db, actor="Admin")
    w.cmb_date_preset.setCurrentText("All Time")
    yield w
    S.dispose(w)


def test_module_defines_no_colours_or_stylesheets():
    src = Path(ald.__file__).read_text(encoding="utf-8")
    assert "setStyleSheet" not in src
    assert not re.search(r"#[0-9A-Fa-f]{6}\b", src)


def test_screen_is_built_from_kit_widgets(win):
    assert isinstance(win.table, DataGrid)
    assert isinstance(win.workstation_list, DarkListTable)
    assert isinstance(win.entry_pill, Pill)


@pytest.mark.parametrize("action, tone", [
    ("create", "green"), ("restore", "green"), ("filing_submitted", "green"),
    ("view", "neutral"), ("backup", "neutral"), ("csv_export", "neutral"),
    ("autofill", "amber"), ("manual_assist", "amber"), ("manual_copy", "amber"), ("update", "amber"),
    ("delete", "red"), ("archive", "red"), ("audit_log_viewed", "red"),
    ("sync_pushed", "blue"), ("sync_received", "blue"),
])
def test_action_badge_tone_groups(action, tone):
    label, text, got = ald._get_action_badge(action)
    assert got == tone and text and text in label


def test_unknown_action_gets_a_neutral_readable_badge():
    label, text, tone = ald._get_action_badge("some_new_thing")
    assert (label, text, tone) == ("Some New Thing", "Some New Thing", "neutral")
    assert ald._get_action_badge(None)[2] == "neutral"


def test_every_listed_action_maps_to_a_known_pill_tone(win):
    for i in range(1, win.action_combo.count()):
        tone = ald._get_action_badge(win.action_combo.itemText(i))[2]
        assert tone in {"neutral", "green", "amber", "red", "blue"}


def test_chips_mirror_the_state_widgets(win):
    assert win.chip_date.text() == "All Time" and not win.chip_date.isChecked()
    win.cmb_date_preset.setCurrentText("Last 7 Days")
    assert win.chip_date.text() == "Last 7 Days" and win.chip_date.isChecked()

    assert not win.chip_action.isChecked() and not win.chip_staff.isChecked()
    win.action_combo.setCurrentText("delete")
    assert win.chip_action.isChecked()
    win.actor_input.setText("Asha")
    assert win.chip_staff.isChecked()
    win.actor_input.setText("")
    win.action_combo.setCurrentIndex(0)
    assert not win.chip_action.isChecked() and not win.chip_staff.isChecked()


def test_custom_range_row_only_shows_for_the_custom_preset(win):
    win.show()
    assert not win._custom_row.isVisible()
    win.cmb_date_preset.setCurrentText("Custom Date Range")
    assert win._custom_row.isVisible()
    win.cmb_date_preset.setCurrentText("Today")
    assert not win._custom_row.isVisible()


def test_compact_mode_drops_the_workstation_card_and_secondary_columns(win):
    win.resize(1600, 720)
    win.show()
    win.apply_width_mode(WidthMode.WIDE)
    assert win._ws_card.isVisible()
    win.apply_width_mode(WidthMode.COMPACT)
    assert not win._ws_card.isVisible()
    assert win.table.isColumnHidden(5) and win.table.isColumnHidden(6)
    assert win.btn_export.text() == ""
    win.apply_width_mode(WidthMode.WIDE)
    assert not win.table.isColumnHidden(5) and not win.table.isColumnHidden(6)


def test_workstation_row_counts_the_loaded_entries(win):
    assert win.workstation_list.item(0, 0).text() == "All (this office)"
    assert win.workstation_list.item(0, 1).text() == str(len(win._raw_logs_cache))
    assert win.workstation_list.item(0, 0).data(Qt.UserRole) == "local"
