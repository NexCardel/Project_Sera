"""
W0-2 behaviour lock, R8: Tracker Dump. Pins what the existing tracker tests do not: search, the five filters and
their chips, the summary tiles, Reset, the view switch, paging, F5/refresh, double-click, the row buttons, the
right-click and kebab menus, the Tools menu and the CSV export. (Column widths, hiding and the Submitted /
E-verified filter are already pinned by test_tracker_dump_columns.py and test_tracker_dump_status.py; the autofill
button by test_tracker_dump_autofill_button.py.) Offscreen, a stand-in database, fictional clients.
"""
import csv
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import PySide6.QtWidgets as QtWidgets
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QShortcut
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QMenu, QMessageBox, QPushButton

import ui.windows.tracker_dump_window as tdw

APP = QApplication.instance() or QApplication([])
W = tdw.TrackerDumpWindow
RealQMenu = QMenu
YES, NO = QMessageBox.Yes, QMessageBox.No


def _ts(**ago):
    return (datetime.now(timezone.utc) - timedelta(**ago)).isoformat()


def _filing(portal, method, arn, period, status):
    return {"portal": portal, "capture_method": method, "arn": arn, "period_label": period, "status": status}


def _container(key, name, pan, client_id, portal, status, arn, period, method, updated, unassigned=False):
    return {
        "identity_key": key, "display_name": name, "pan": pan, "client_id": client_id,
        "is_unassigned": unassigned, "portal": portal, "latest_status": status, "latest_arn": arn,
        "period_summary": period, "capture_method": method, "last_updated": updated, "total_captures": 2,
        "filing_history": [_filing(portal, method, arn, period, status)],
    }


def _containers():
    return [
        _container("PAN:ABCPD1001E", "Alpha Traders", "ABCPD1001E", 1, "Income Tax (ITR-4)", "Submitted & Verified",
                   "AA290121000069S", "AY 2025-26", "VSDC-X_itr_submitted", _ts(seconds=1)),
        _container("PAN:ABCPD1002E", "Bravo Textiles", "ABCPD1002E", 2, "GST Portal (GSTR-3B)", "Submitted & Verified",
                   "N/A", "June (FY 2026-27)", "SGT_live", _ts(days=3)),
        _container("PAN:ABCPD1003E", "Charlie Foods", "ABCPD1003E", 3, "TRACES (26Q)", "Submitted (Not Verified)",
                   "123456789150726", "Q1 FY 2026-27", "VSDC-X_traces", _ts(days=20)),
        _container("PAN:ABCPD1004E", "Delta Clinic", "ABCPD1004E", None, "GST Portal (GSTR-1)", "Draft",
                   "N/A", "June (FY 2026-27)", "SGT_live", _ts(seconds=2), unassigned=True),
        _container("PAN:ABCPD1005E", "Echo Mills", "ABCPD1005E", 5, "Income Tax (ITR-3)", "Not Submitted",
                   "N/A", "AY 2024-25", "VSDC-X_itr_submitted", _ts(days=60)),
        _container("PAN:ABCPD1006E", "Foxtrot Steel", "ABCPD1006E", 6, "Income Tax (ITR-3)", "Other EVC",
                   "N/A", "AY 2025-26", "VSDC-X_itr_submitted", _ts(days=10)),
    ]


def _raw_from(c, rid):
    return {
        "id": rid, "client_id": c["client_id"], "is_unassigned": c["is_unassigned"], "client_name": c["display_name"],
        "pan": c["pan"], "portal": c["portal"], "status": c["latest_status"], "arn_number": c["latest_arn"],
        "period_label": c["period_summary"], "capture_method": c["capture_method"], "created_at": c["last_updated"],
    }


class FakeDB:
    def __init__(self, containers=None, raw=None):
        self.containers = _containers() if containers is None else containers
        self.raw = [_raw_from(c, i + 1) for i, c in enumerate(self.containers)] if raw is None else raw
        self.loads = 0
        self.deleted = []

    def get_srpf_containers(self, limit=200, search_query=None, slim=False, identity_key=None):
        self.loads += 1
        return list(self.containers)

    def get_tracker_dumps(self, client_id=None, limit=200, search_query=None, slim=False, dump_id=None):
        self.loads += 1
        return list(self.raw)

    def get_client_services(self, client_id):
        return []

    def delete_srpf_container(self, key):
        self.deleted.append(("container", key))
        self.containers = [c for c in self.containers if c["identity_key"] != key]

    def delete_tracker_dump(self, dump_id):
        self.deleted.append(("dump", dump_id))
        self.raw = [r for r in self.raw if r["id"] != dump_id]

    def clear_tracker_dumps(self):
        self.deleted.append(("clear", None))

    def re_resolve_all_tracker_dumps(self):
        return 3


@pytest.fixture(autouse=True)
def no_real_dialogs(monkeypatch):
    def _boom(kind):
        def _raise(parent, title, text, *a, **k):
            raise AssertionError(f"unexpected {kind}: {title}: {text}")
        return staticmethod(_raise)
    for kind in ("critical", "warning", "question", "information"):
        monkeypatch.setattr(QMessageBox, kind, _boom(kind))


@pytest.fixture
def make_window():
    made = []
    patches = [patch.object(tdw, "_load_column_choices", return_value=(set(), set())),
               patch.object(tdw, "_save_column_choices")]
    for p in patches:
        p.start()

    def _make(db=None, raw=False):
        db = db or FakeDB()
        win = W(db, defer_first_load=True)
        win.resize(1700, 900)
        win.show()
        if raw:
            win.cmb_view_mode.setCurrentIndex(1)
        win.load_data()
        APP.processEvents()
        made.append(win)
        return win, db
    yield _make
    for win in made:
        win.hide()
        win.deleteLater()
    APP.processEvents()
    for p in patches:
        p.stop()


def _names(win):
    return [win.table.item(r, W.COL_CLIENT).text() for r in range(win.table.rowCount())]


def _pill(win, row):
    label = win.table.cellWidget(row, W.COL_STATUS).findChild(QLabel, "status_label")
    return label.property("full_text") or label.text()


def _search(win, text):
    win.txt_search.setText(text)
    QTest.qWait(400)
    APP.processEvents()


GROUPED_ORDER = ["Alpha Traders", "Delta Clinic", "Bravo Textiles", "Foxtrot Steel", "Charlie Foods", "Echo Mills"]


def test_grouped_view_lists_every_container_newest_first_with_counts(make_window):
    win, _ = make_window()
    assert _names(win) == GROUPED_ORDER
    assert win.lbl_page_info.text() == "Showing 1 to 6 of 6 entries"
    assert win.lbl_current_page.text() == "Page 1 / 1"
    assert not win.btn_prev_page.isEnabled() and not win.btn_next_page.isEnabled()
    assert [win.table.horizontalHeaderItem(c).text() for c in range(win.table.columnCount())] == [
        "Client", "Filing type", "Filings & History", "Submission Status", "", "Updated", "Captured by"]
    assert [_pill(win, r) for r in range(6)] == [
        "Submitted & E-verified", "Not submitted", "Submitted", "Other EVC", "Submitted (e-verification pending)",
        "Not submitted"]


def test_search_waits_for_the_debounce_then_matches_client_pan_arn_portal_and_period(make_window):
    win, _ = make_window()
    win.txt_search.setText("alpha")
    assert win._search_timer.isActive() and len(_names(win)) == 6
    QTest.qWait(400)
    assert _names(win) == ["Alpha Traders"]
    _search(win, "ABCPD1002E")
    assert _names(win) == ["Bravo Textiles"]
    _search(win, "123456789150726")
    assert _names(win) == ["Charlie Foods"]
    _search(win, "GSTR")
    assert _names(win) == ["Delta Clinic", "Bravo Textiles"]
    _search(win, "ay 2024-25")
    assert _names(win) == ["Echo Mills"]
    _search(win, "no such thing")
    assert _names(win) == [] and win.lbl_page_info.text() == "Showing 0 to 0 of 0 entries"
    _search(win, "")
    assert len(_names(win)) == 6


@pytest.mark.parametrize("combo,text,expected", [
    ("cmb_status", "Submitted & E-verified", ["Alpha Traders"]),
    ("cmb_status", "Submitted", ["Bravo Textiles"]),
    ("cmb_status", "Pending e-Verification", ["Charlie Foods"]),
    ("cmb_status", "Other EVC", ["Foxtrot Steel"]),
    ("cmb_status", "Not submitted", ["Delta Clinic", "Echo Mills"]),
    ("cmb_portal", "Income Tax (ITR)", ["Alpha Traders", "Foxtrot Steel", "Echo Mills"]),
    ("cmb_portal", "GST Portal", ["Delta Clinic", "Bravo Textiles"]),
    ("cmb_portal", "TRACES / TDS", ["Charlie Foods"]),
    ("cmb_client", "Registered Clients", ["Alpha Traders", "Bravo Textiles", "Foxtrot Steel", "Charlie Foods", "Echo Mills"]),
    ("cmb_client", "Unregistered / Action Required", ["Delta Clinic"]),
    ("cmb_date", "Today", ["Alpha Traders", "Delta Clinic"]),
    ("cmb_date", "Past 7 Days", ["Alpha Traders", "Delta Clinic", "Bravo Textiles"]),
    ("cmb_date", "Past 30 Days", ["Alpha Traders", "Delta Clinic", "Bravo Textiles", "Foxtrot Steel", "Charlie Foods"]),
    ("cmb_source", "Hide SGT", ["Alpha Traders", "Foxtrot Steel", "Charlie Foods", "Echo Mills"]),
    ("cmb_source", "SGT Only", ["Delta Clinic", "Bravo Textiles"]),
])
def test_each_filter_value_keeps_exactly_its_rows(make_window, combo, text, expected):
    win, _ = make_window()
    getattr(win, combo).setCurrentText(text)
    assert _names(win) == expected


def test_filter_dropdown_values_are_these(make_window):
    win, _ = make_window()
    texts = lambda c: [c.itemText(i) for i in range(c.count())]
    assert texts(win.cmb_status) == ["All Statuses", "Submitted & E-verified", "Submitted", "Pending e-Verification",
                                     "Other EVC", "Not submitted"]
    assert texts(win.cmb_portal) == ["All Portals", "Income Tax (ITR)", "GST Portal", "TRACES / TDS"]
    assert texts(win.cmb_client) == ["All Clients", "Registered Clients", "Unregistered / Action Required"]
    assert texts(win.cmb_date) == ["All Time", "Today", "Past 7 Days", "Past 30 Days"]
    assert texts(win.cmb_source) == ["All Sources", "Hide SGT", "SGT Only"]
    assert all(c.isHidden() for c in (win.cmb_status, win.cmb_portal, win.cmb_client, win.cmb_date, win.cmb_source))
    assert win.cmb_view_mode.isHidden()


def test_filters_combine_and_search_stacks_on_them(make_window):
    win, _ = make_window()
    win.cmb_portal.setCurrentText("Income Tax (ITR)")
    win.cmb_date.setCurrentText("Past 30 Days")
    assert _names(win) == ["Alpha Traders", "Foxtrot Steel"]
    _search(win, "foxtrot")
    assert _names(win) == ["Foxtrot Steel"]


def test_filter_chips_drive_the_hidden_combos_and_show_the_value(make_window):
    win, _ = make_window()
    chips = {label: (chip, combo, short) for chip, combo, label, short in win._filter_chips}
    assert list(chips) == ["Status", "Portal", "Client", "Time", "Source"]
    assert all(c.text() == f"{label}  ▾" and not c.isChecked() for label, (c, _, _) in chips.items())
    chip, combo, _ = chips["Status"]
    entries = [a.text() for a in chip.menu().actions() if not a.isSeparator()]
    assert entries == [combo.itemText(i) for i in range(combo.count())]
    next(a for a in chip.menu().actions() if a.text() == "Submitted & E-verified").trigger()
    assert combo.currentText() == "Submitted & E-verified" and chip.isChecked()
    assert chip.text() == "Status: E-verified  ▾" and _names(win) == ["Alpha Traders"]
    next(a for a in chips["Client"][0].menu().actions() if a.text() == "Unregistered / Action Required").trigger()
    assert chips["Client"][0].text() == "Client: Unregistered  ▾" and _names(win) == []
    next(a for a in chip.menu().actions() if a.text() == "All Statuses").trigger()
    assert not chip.isChecked() and _names(win) == ["Delta Clinic"]


def test_summary_tiles_count_over_everything_loaded(make_window):
    win, _ = make_window()
    tiles = win._summary_tiles
    assert list(tiles) == ["total", "unregistered", "pending", "not_submitted", "today"]
    assert {k: v[1].text() for k, v in tiles.items()} == {
        "total": "6", "unregistered": "1", "pending": "1", "not_submitted": "2", "today": "2"}
    assert tiles["total"][2].text() == "Client containers"
    assert tiles["total"][0].isChecked() and not tiles["pending"][0].isChecked()


def test_a_tile_applies_its_filter_and_a_second_click_clears_it(make_window):
    win, _ = make_window()
    tiles = win._summary_tiles
    tiles["pending"][0].click()
    assert win.cmb_status.currentText() == "Pending e-Verification" and _names(win) == ["Charlie Foods"]
    assert tiles["pending"][0].isChecked() and not tiles["total"][0].isChecked()
    tiles["pending"][0].click()
    assert win.cmb_status.currentIndex() == 0 and len(_names(win)) == 6 and tiles["total"][0].isChecked()
    tiles["unregistered"][0].click()
    assert win.cmb_client.currentText() == "Unregistered / Action Required" and _names(win) == ["Delta Clinic"]
    tiles["not_submitted"][0].click()
    assert win.cmb_status.currentText() == "Not submitted"
    tiles["today"][0].click()
    assert win.cmb_date.currentText() == "Today"


def test_the_total_tile_clears_search_and_every_filter(make_window):
    win, _ = make_window()
    win.cmb_portal.setCurrentText("GST Portal")
    win.cmb_source.setCurrentText("SGT Only")
    _search(win, "bravo")
    win._summary_tiles["total"][0].click()
    assert win.txt_search.text() == "" and len(_names(win)) == 6
    assert all(c.currentIndex() == 0 for c in (win.cmb_status, win.cmb_portal, win.cmb_client, win.cmb_date, win.cmb_source))


def test_reset_shows_only_when_a_filter_is_on_and_clears_everything(make_window):
    win, _ = make_window()
    assert win.btn_reset_filters.isHidden()
    win.cmb_status.setCurrentText("Not submitted")
    assert not win.btn_reset_filters.isHidden()
    _search(win, "echo")
    win.btn_reset_filters.click()
    assert win.txt_search.text() == "" and win.cmb_status.currentIndex() == 0
    assert len(_names(win)) == 6 and win.btn_reset_filters.isHidden()


def test_view_buttons_drive_the_hidden_combo_and_swap_the_table(make_window):
    win, db = make_window()
    seg = {text: b for b, text, _ in win._seg_buttons}
    assert list(seg) == ["Containers", "Raw captures"] and seg["Containers"].isChecked()
    seg["Raw captures"].click()
    assert win.cmb_view_mode.currentIndex() == 1 and seg["Raw captures"].isChecked()
    assert win.table.horizontalHeaderItem(W.COL_PERIOD).text() == "Period"
    assert win._summary_tiles["total"][2].text() == "Raw captures"
    assert win.table.rowCount() == 6 and win.table.item(0, W.COL_CLIENT).data(tdw._SUBLINE_ROLE).startswith("Record #")
    seg["Containers"].click()
    assert win.cmb_view_mode.currentIndex() == 0 and win.table.horizontalHeaderItem(W.COL_PERIOD).text() == "Filings & History"
    win.cmb_view_mode.setCurrentIndex(1)
    assert seg["Raw captures"].isChecked()


def test_paging_steps_through_pages_and_resets_on_a_filter(make_window):
    base = _containers()[0]
    raw = [dict(_raw_from(base, i + 1), client_name=f"Client {i + 1:02d}", created_at=_ts(minutes=i + 1)) for i in range(60)]
    win, _ = make_window(FakeDB(raw=raw), raw=True)
    assert win.table.rowCount() == 25 and win.cmb_page_size.currentText() == "25"
    assert [win.cmb_page_size.itemText(i) for i in range(win.cmb_page_size.count())] == ["25", "50", "100", "200", "All"]
    assert win.lbl_page_info.text() == "Showing 1 to 25 of 60 entries" and win.lbl_current_page.text() == "Page 1 / 3"
    assert not win.btn_prev_page.isEnabled() and win.btn_next_page.isEnabled()
    win.btn_next_page.click()
    assert win.lbl_page_info.text() == "Showing 26 to 50 of 60 entries" and win.lbl_current_page.text() == "Page 2 / 3"
    assert _names(win)[0] == "Client 26"
    win.btn_next_page.click()
    assert win.lbl_page_info.text() == "Showing 51 to 60 of 60 entries" and win.table.rowCount() == 10
    assert not win.btn_next_page.isEnabled()
    win.btn_next_page.click()
    assert win.lbl_current_page.text() == "Page 3 / 3"
    win.btn_prev_page.click()
    assert win.lbl_current_page.text() == "Page 2 / 3"
    win.cmb_page_size.setCurrentText("50")
    assert win.lbl_current_page.text() == "Page 1 / 2" and win.table.rowCount() == 50
    win.btn_next_page.click()
    win.cmb_source.setCurrentText("Hide SGT")
    assert win.lbl_current_page.text().startswith("Page 1 /")
    win.cmb_page_size.setCurrentText("All")
    assert win.table.rowCount() == 60 and win.lbl_page_info.text() == "Showing 1 to 60 of 60 entries"
    assert win.lbl_current_page.text() == "Page 1 / 1" and not win.btn_prev_page.isEnabled() and not win.btn_next_page.isEnabled()


def test_f5_and_the_refresh_button_reload_from_the_database(make_window):
    win, db = make_window()
    before = db.loads
    f5 = [s for s in win.findChildren(QShortcut) if s.key().toString() == "F5"]
    assert len(f5) == 1 and f5[0].context() == Qt.WidgetWithChildrenShortcut
    f5[0].activated.emit()
    assert db.loads == before + 1
    refresh = next(b for b in win.findChildren(QPushButton) if b.toolTip() == "Refresh (F5)")
    refresh.click()
    assert db.loads == before + 2


def test_double_click_inspects_the_row_container_or_raw_payload(make_window, monkeypatch):
    win, _ = make_window()
    seen = []
    monkeypatch.setattr(win, "_show_container_dialog", lambda item: seen.append(("container", item["display_name"])))
    monkeypatch.setattr(win, "_show_payload_dialog", lambda item: seen.append(("payload", item["id"])))
    win.table.cellDoubleClicked.emit(2, W.COL_CLIENT)
    win.cmb_view_mode.setCurrentIndex(1)
    win.table.cellDoubleClicked.emit(0, W.COL_CLIENT)
    win.table.cellDoubleClicked.emit(99, 0)
    assert seen == [("container", "Bravo Textiles"), ("payload", 1)]


def test_row_buttons_exist_and_tooltips_follow_the_view(make_window):
    win, _ = make_window()

    def buttons(row):
        cell = win.table.cellWidget(row, W.COL_ACTIONS)
        return {n: cell.findChild(QPushButton, n) for n in ("act_autofill", "act_view", "act_more", "act_create")}
    b0, b1 = buttons(0), buttons(1)
    assert b0["act_view"].toolTip() == "Inspect (2 captures)"
    assert b0["act_create"].isHidden() and not b1["act_create"].isHidden()
    assert b1["act_autofill"].isHidden() and not b0["act_autofill"].isHidden()
    win.cmb_view_mode.setCurrentIndex(1)
    assert buttons(0)["act_view"].toolTip() == "View Payload"


def test_view_and_create_buttons_open_the_row_item(make_window, monkeypatch):
    win, _ = make_window()
    seen = []
    monkeypatch.setattr(win, "_show_container_dialog", lambda i: seen.append(("container", i["display_name"])))
    monkeypatch.setattr(win, "_show_payload_dialog", lambda i: seen.append(("payload", i["id"])))
    monkeypatch.setattr(win, "_create_client_from_capture", lambda i: seen.append(("create", i["display_name"] if "display_name" in i else i["client_name"])))
    win.table.cellWidget(2, W.COL_ACTIONS).findChild(QPushButton, "act_view").click()
    win.table.cellWidget(1, W.COL_ACTIONS).findChild(QPushButton, "act_create").click()
    win.cmb_view_mode.setCurrentIndex(1)
    win.table.cellWidget(0, W.COL_ACTIONS).findChild(QPushButton, "act_view").click()
    assert seen == [("container", "Bravo Textiles"), ("create", "Delta Clinic"), ("payload", 1)]


def _fake_menu(monkeypatch, pick=None):
    shown = []

    class FakeMenu(RealQMenu):
        def exec_(self, *a, **k):
            texts = [a_.text() for a_ in self.actions() if not a_.isSeparator()]
            shown.append(texts)
            if pick:
                next(a_ for a_ in self.actions() if a_.text() == pick).trigger()

        exec = exec_
    monkeypatch.setattr(tdw, "QMenu", FakeMenu)
    monkeypatch.setattr(QtWidgets, "QMenu", FakeMenu)
    return shown


def _right_click(win, row):
    y = win.table.rowViewportPosition(row) + 8
    win._on_table_context_menu(QPoint(20, y))


def test_right_click_menu_lists_the_actions_for_the_row_and_view(make_window, monkeypatch):
    win, _ = make_window()
    shown = _fake_menu(monkeypatch)
    _right_click(win, 0)
    _right_click(win, 1)
    win._on_table_context_menu(QPoint(20, 5000))
    win.cmb_view_mode.setCurrentIndex(1)
    _right_click(win, 0)
    _right_click(win, 1)
    assert shown == [
        ["Inspect Client Container", "Delete Container"],
        ["Inspect Client Container", "+ Create Client", "Delete Container"],
        ["View Raw Payload", "Delete Record"],
        ["View Raw Payload", "+ Create Client", "Delete Record"],
    ]


def test_kebab_menu_lists_create_and_delete(make_window, monkeypatch):
    win, _ = make_window()
    shown = _fake_menu(monkeypatch)
    win.table.cellWidget(0, W.COL_ACTIONS).findChild(QPushButton, "act_more").click()
    win.table.cellWidget(1, W.COL_ACTIONS).findChild(QPushButton, "act_more").click()
    win.cmb_view_mode.setCurrentIndex(1)
    win.table.cellWidget(0, W.COL_ACTIONS).findChild(QPushButton, "act_more").click()
    assert shown == [["Delete Container"], ["+ Create Client", "Delete Container"], ["Delete Record"]]


def test_delete_container_asks_first_and_removes_only_that_container(make_window, monkeypatch):
    win, db = make_window()
    asked = []
    answer = {"v": NO}
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda p, t, text, *a, **k: (asked.append((t, text)), answer["v"])[1]))
    _fake_menu(monkeypatch, pick="Delete Container")
    _right_click(win, 2)
    assert asked == [("Confirm Delete Container", "Are you sure you want to delete this client container "
                      "(PAN:ABCPD1002E) and all its captured filings?")]
    assert db.deleted == [] and len(_names(win)) == 6
    answer["v"] = YES
    _right_click(win, 2)
    assert db.deleted == [("container", "PAN:ABCPD1002E")]
    assert "Bravo Textiles" not in _names(win) and len(_names(win)) == 5


def test_delete_record_asks_first_and_removes_only_that_capture(make_window, monkeypatch):
    win, db = make_window(raw=True)
    asked = []
    answer = {"v": NO}
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda p, t, text, *a, **k: (asked.append((t, text)), answer["v"])[1]))
    _fake_menu(monkeypatch, pick="Delete Record")
    _right_click(win, 1)
    assert asked == [("Confirm Delete", "Are you sure you want to delete Tracker Dump record #4?")]
    assert db.deleted == []
    answer["v"] = YES
    _right_click(win, 1)
    assert db.deleted == [("dump", 4)] and "Delta Clinic" not in _names(win)


def test_kebab_and_menu_create_client_open_the_capture_dialog(make_window, monkeypatch):
    win, _ = make_window()
    seen = []
    monkeypatch.setattr(win, "_create_client_from_capture", lambda i: seen.append(i["identity_key"]))
    _fake_menu(monkeypatch, pick="+ Create Client")
    _right_click(win, 1)
    win.table.cellWidget(1, W.COL_ACTIONS).findChild(QPushButton, "act_more").click()
    assert seen == ["PAN:ABCPD1004E", "PAN:ABCPD1004E"]


def test_tools_menu_entries_in_order(make_window, monkeypatch):
    win, _ = make_window()
    shown = _fake_menu(monkeypatch)
    assert win.btn_preferences.text() == "Tools  ▾"
    win.btn_preferences.click()
    assert shown == [["Re-Resolve Identities (SRPF)", "Map Datapoints to MCL Columns…", "Open LTT sheet",
                      "Export Captures (CSV)", "Distill…", "Clear All Captures"]]


def test_re_resolve_reports_how_many_captures_changed(make_window, monkeypatch):
    win, db = make_window()
    told = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda p, t, text, *a, **k: told.append((t, text))))
    _fake_menu(monkeypatch, pick="Re-Resolve Identities (SRPF)")
    win.btn_preferences.click()
    assert told == [("SRPF Containers Rebuilt", "Successfully re-resolved captures and rebuilt unified SRPF client "
                     "containers (3 captures updated).")]


def test_clear_all_asks_with_a_warning_and_no_clears_nothing(make_window, monkeypatch):
    win, db = make_window()
    asked = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda p, t, text, *a, **k: (asked.append(t), NO)[1]))
    _fake_menu(monkeypatch, pick="Clear All Captures")
    win.btn_preferences.click()
    assert asked == ["Clear All Dump Logs"] and db.deleted == [] and len(_names(win)) == 6


def test_export_csv_currently_fails_because_tracker_dump_window_never_imports_csv(make_window, monkeypatch, tmp_path):
    # Known bug (decision recorded in the tracker): the export reports "Export Failed" instead of writing the file.
    # When the redesign fixes it, flip this to the intended behaviour: header row + one row per loaded container.
    win, _ = make_window()
    out = tmp_path / "export.csv"
    failed = []
    monkeypatch.setattr(tdw.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "")))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda p, t, text, *a, **k: failed.append((t, text))))
    win.cmb_status.setCurrentText("Other EVC")
    win._export_csv()
    assert len(failed) == 1 and failed[0][0] == "Export Failed" and failed[0][1].startswith("Could not write CSV: ")
    assert not hasattr(tdw, "csv")


def test_export_csv_with_nothing_loaded_only_says_so(make_window, monkeypatch):
    win, _ = make_window(FakeDB(containers=[], raw=[]))
    told = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda p, t, text, *a, **k: told.append((t, text))))
    monkeypatch.setattr(tdw.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: pytest.fail("asked for a path")))
    win._export_csv()
    assert told == [("Export CSV", "No records to export.")]
    assert win.lbl_page_info.text() == "Showing 0 to 0 of 0 entries"


def test_a_cancelled_save_dialog_writes_nothing(make_window, monkeypatch, tmp_path):
    win, _ = make_window()
    monkeypatch.setattr(tdw.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: pytest.fail("reported an export")))
    win._export_csv()
    assert list(tmp_path.iterdir()) == []
