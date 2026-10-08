"""
Tracker Dump adapts its columns to the table width (fit_columns in ui/windows/tracker_dump_window.py).
The pure checks run without a widget. The widget checks cover compact mode, the header menu and the
saved per-PC choices. Identifiers are fictional.
"""
import json
import os
import tempfile
from unittest.mock import patch

import pytest

import security
from database import SeraDatabase
from PySide6.QtWidgets import QApplication

import ui.windows.tracker_dump_window as tdw
from ui.utils.responsive import WidthMode

APP = QApplication.instance() or QApplication([])
C = tdw.TrackerDumpWindow
DEVICE, UPDATED, FILING = C.COL_DEVICE, C.COL_UPDATED, C.COL_FILING
STATUS, PERIOD, ACTIONS, CLIENT = C.COL_STATUS, C.COL_PERIOD, C.COL_ACTIONS, C.COL_CLIENT
TODAY_GROUPED = {FILING: 120, PERIOD: 250, STATUS: 280, ACTIONS: 120, UPDATED: 150, DEVICE: 130}
TODAY_RAW = {**TODAY_GROUPED, PERIOD: 170}


def fit(available, grouped=True, user_widths=None, user_hidden=(), user_shown=()):
    return tdw.fit_columns(available, grouped, user_widths or {}, set(user_hidden), set(user_shown))


# ---- pure layout maths -------------------------------------------------------------------------

def test_with_room_to_spare_the_widths_are_today_s():
    widths, hidden = fit(1600)
    assert hidden == set()
    assert {c: widths[c] for c in TODAY_GROUPED} == TODAY_GROUPED
    assert widths[CLIENT] == 1600 - sum(TODAY_GROUPED.values())


def test_at_1300_the_widths_are_still_exactly_today_s():
    widths, hidden = fit(1300)
    assert hidden == set()
    assert {c: widths[c] for c in TODAY_GROUPED} == TODAY_GROUPED
    assert widths[CLIENT] == 250


def test_raw_view_uses_the_narrower_period_column():
    widths, _ = fit(1600, grouped=False)
    assert widths[PERIOD] == 170


@pytest.mark.parametrize("available", [1600, 1100, 900])
def test_client_keeps_240_px(available):
    widths, _ = fit(available)
    assert widths[CLIENT] >= tdw.CLIENT_MIN_PX


def test_1200_shrinks_columns_and_hides_nothing():
    widths, hidden = fit(1200)
    assert hidden == set()
    assert widths[CLIENT] == 240
    assert all(widths[c] <= TODAY_GROUPED[c] for c in TODAY_GROUPED)
    assert widths[PERIOD] < TODAY_GROUPED[PERIOD]


def test_1100_hides_only_device():
    widths, hidden = fit(1100)
    assert hidden == {DEVICE}
    assert widths[CLIENT] == 240


def test_1000_hides_device_and_updated_but_keeps_filing():
    widths, hidden = fit(1000)
    assert hidden == {DEVICE, UPDATED}
    assert FILING in widths and widths[CLIENT] == 240


def test_900_hides_device_updated_and_filing():
    widths, hidden = fit(900)
    assert hidden == {DEVICE, UPDATED, FILING}
    assert widths[CLIENT] >= 240                  # the leftover room goes to Client


def test_700_hides_device_updated_and_filing_but_never_status_period_or_actions():
    widths, hidden = fit(700)
    assert hidden == {DEVICE, UPDATED, FILING}
    assert {STATUS, PERIOD, ACTIONS} <= set(widths)


def test_the_hide_order_is_device_then_updated_then_filing():
    # Thresholds for the table width: Device goes below 1140, Updated below 1040, Filing below 910.
    assert fit(1140)[1] == set()
    assert fit(1139)[1] == {DEVICE}
    assert fit(1039)[1] == {DEVICE, UPDATED}
    assert fit(909)[1] == {DEVICE, UPDATED, FILING}


@pytest.mark.parametrize("available", range(700, 1800, 50))
def test_visible_columns_fill_the_table_exactly(available):
    widths, _ = fit(available)
    assert sum(widths.values()) == available


def test_user_hidden_columns_stay_hidden_when_there_is_room():
    widths, hidden = fit(1600, user_hidden={UPDATED})
    assert hidden == {UPDATED} and UPDATED not in widths


def test_status_period_and_actions_cannot_be_hidden():
    _, hidden = fit(1600, user_hidden={STATUS, PERIOD, ACTIONS})
    assert hidden == set()


def test_a_user_shown_column_is_not_auto_hidden():
    _, hidden = fit(900, user_shown={DEVICE})
    assert DEVICE not in hidden
    assert hidden == {UPDATED, FILING}


def test_user_widths_replace_the_preferred_width_when_there_is_room():
    widths, _ = fit(1600, user_widths={PERIOD: 300})
    assert widths[PERIOD] == 300


def test_auto_hidden_values_are_not_counted_as_user_hidden():
    _, hidden = fit(900)
    assert hidden - {DEVICE, UPDATED, FILING} == set()


# ---- widget behaviour ----------------------------------------------------------------------------

class _WindowCase:
    """A Tracker Dump with a throwaway database. The saved column choices are patched so the
    registry is never read or written."""

    def setup_method(self):
        self.tmp = tempfile.TemporaryDirectory()
        salt = os.path.join(self.tmp.name, "t.salt")
        security.generate_and_save_salt(salt)
        key = security.derive_key_hex("testpass123", security.load_salt(salt))
        self.db = SeraDatabase(os.path.join(self.tmp.name, "m.db"), key,
                               raw_db_path=os.path.join(self.tmp.name, "rawPayload.db"))
        for i, pan in enumerate(("ABCPD1001E", "ABCPD1002E", "ABCPD1003E")):
            self.db.insert_tracker_dump(portal="Income Tax (ITR-4)", period_label=f"AY 202{i}-2{i + 1}",
                                        arn_number=f"DEMO-ARN-{i}", capture_method="VSDC-X_itr_submitted",
                                        status="Submitted", pan=pan, filing_type="ITR-4",
                                        raw_payload_json=json.dumps({"pan": pan, "device_name": "DEMO-PC-01"}))
        self.patches = [
            patch.object(tdw, "_load_column_choices", return_value=(set(), set())),
            patch.object(tdw, "_save_column_choices"),
        ]
        _load, self.saved = (p.start() for p in self.patches)
        self.win = tdw.TrackerDumpWindow(self.db)
        self.win.resize(1920, 1000)
        self.win.show()
        self.settle()

    def teardown_method(self):
        self.win.hide()
        self.win.deleteLater()
        APP.processEvents()
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def settle(self):
        for _ in range(4):
            APP.processEvents()
        self.win._adjust_table_columns()


class TestWidthModes(_WindowCase):
    def test_wide_page_keeps_today_s_header_and_row_height(self):
        assert self.win._width_mode is WidthMode.WIDE
        assert not self.win._lbl_subtitle.isHidden()
        assert not self.win._chip_row.isVisible()
        assert self.win.table.verticalHeader().defaultSectionSize() == 48
        assert self.win.txt_search.width() == 320

    def test_compact_page_moves_the_chips_to_their_own_row_and_back(self):
        self.win.resize(1000, 700)
        self.settle()
        assert self.win._width_mode is WidthMode.COMPACT
        assert self.win._lbl_subtitle.isHidden()
        assert self.win._chip_row.isVisible()
        assert all(self.win._chip_layout.indexOf(chip) >= 0 for chip, *_ in self.win._filter_chips)
        assert self.win.table.verticalHeader().defaultSectionSize() == 40
        assert self.win._lbl_per_page.isHidden()

        self.win.resize(1600, 900)
        self.settle()
        assert self.win._width_mode is WidthMode.WIDE
        assert not self.win._chip_row.isVisible()
        assert all(self.win._filter_layout.indexOf(chip) >= 0 for chip, *_ in self.win._filter_chips)
        assert self.win.table.verticalHeader().defaultSectionSize() == 48

    def test_compact_uses_the_short_segment_labels(self):
        self.win.resize(1000, 700)
        self.settle()
        assert [b.text() for b, *_ in self.win._seg_buttons] == ["Containers", "Raw"]
        self.win.resize(1600, 900)
        self.settle()
        assert [b.text() for b, *_ in self.win._seg_buttons] == ["Containers", "Raw captures"]

    def test_wide_columns_match_today(self):
        # 1500 rather than 1920: a 1920-wide screen at 125 % is 1536 logical px, which clamps the window.
        self.win.resize(1500, 1000)
        self.settle()
        for col, width in TODAY_GROUPED.items():
            assert self.win.table.columnWidth(col) == width, col

    def test_qt_reporting_a_hidden_column_as_zero_wide_is_not_a_user_width(self):
        self.win.table.setColumnHidden(DEVICE, True)
        self.win._on_section_resized(DEVICE, 130, 0)
        self.win.table.setColumnHidden(DEVICE, False)
        self.win._on_section_resized(DEVICE, 0, 0)
        assert not self.win._user_col_widths


class TestColumnChoices(_WindowCase):
    def test_hiding_a_column_from_the_header_menu_saves_the_choice(self):
        self.win._set_column_visible(DEVICE, False)
        assert self.win.table.isColumnHidden(DEVICE)
        assert self.win._user_hidden_cols == {DEVICE}
        self.saved.assert_called()

    def test_showing_an_auto_hidden_column_wins_over_auto_hiding(self):
        self.win.resize(900, 700)
        self.settle()
        assert self.win.table.isColumnHidden(DEVICE)
        self.win._set_column_visible(DEVICE, True)
        assert not self.win.table.isColumnHidden(DEVICE)
        assert DEVICE in self.win._user_shown_cols

    def test_reset_returns_to_automatic(self):
        self.win._set_column_visible(UPDATED, False)
        self.win._reset_column_choices()
        assert not self.win.table.isColumnHidden(UPDATED)
        assert self.win._user_hidden_cols == set() and self.win._user_shown_cols == set()

    def test_values_of_an_auto_hidden_column_go_into_the_client_tooltip(self):
        self.win.resize(900, 700)
        self.settle()
        assert self.win.table.isColumnHidden(DEVICE)
        device = self.win.table.item(0, DEVICE).text()
        assert device and f"Device: {device}" in self.win.table.item(0, CLIENT).toolTip()

    def test_the_client_tooltip_is_plain_when_nothing_is_hidden(self):
        tip = self.win.table.item(0, CLIENT).toolTip()
        assert "Device:" not in tip and "Updated:" not in tip
