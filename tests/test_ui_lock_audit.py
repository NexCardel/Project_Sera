"""Behaviour lock R4 (Audit log window): action/staff/date filters, search, drill-down, CSV export."""
import csv
import pathlib
import re

import ui_lock_support as S

import pytest
from PySide6.QtCore import QDate
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from ui.dialogs import audit_log_dialog as ald
from ui.dialogs.audit_log_dialog import AuditLogDialog

GOLDEN_CSV = pathlib.Path(__file__).parent / "data" / "ui_lock_audit_export_golden.csv"

ACTIONS = ["view", "manual_assist", "autofill", "manual_copy", "create", "update", "archive", "unarchive", "delete",
           "csv_import", "backup", "restore", "csv_export", "filing_submitted", "sync_pushed", "sync_received",
           "audit_log_viewed"]

BADGE_WORD = {"view": "View", "manual_assist": "Manual Assist", "autofill": "Autofill", "manual_copy": "Copy",
              "create": "Create", "update": "Update", "archive": "Archive", "unarchive": "Restore",
              "delete": "Delete", "csv_import": "Csv Import", "backup": "Backup", "restore": "Restore",
              "csv_export": "Csv Export", "filing_submitted": "Filing", "sync_pushed": "Sync",
              "sync_received": "Sync", "audit_log_viewed": "Audit View"}


def seed_audit(db, ids):
    """Fixed audit rows (explicit ids and UTC timestamps) so filters and the CSV are reproducible."""
    rows = []
    for i, action in enumerate(ACTIONS, start=1):
        rows.append((i, f"2026-03-02T10:{i:02d}:00", "Asha" if i % 2 else "Bhanu", action,
                     ids["A"] if i % 2 else ids["C"], 1 if action in ("autofill", "manual_assist") else None,
                     f"detail for {action}"))
    rows += [
        (18, "2026-03-03T09:00:00", "System", "update_settings", None, None, "Saved via Unified Settings Hub"),
        (19, "2026-03-05T23:59:59", "Asha", "view", ids["B"], None, None),            # last second of the To day
        (20, "2026-03-06T00:00:00", "Asha", "view", ids["B"], None, "after the range"),
        (21, "2026-02-28T23:59:59", "Bhanu", "view", ids["B"], None, "before the range"),
    ]
    with db._connect() as conn:
        conn.execute("DELETE FROM audit_log")
        conn.executemany("INSERT INTO audit_log (id, ts, actor, action, client_id, service_id, detail) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)", rows)


@pytest.fixture
def world(tmp_path):
    db = S.make_db(tmp_path)
    ids = S.seed_clients(db)
    seed_audit(db, ids)
    wins = []

    def make(all_time=True):
        w = AuditLogDialog(db, actor="Admin")
        wins.append(w)
        if all_time:
            w.cmb_date_preset.setCurrentText("All Time")
        return w

    yield db, ids, make
    S.dispose(*wins)


def _range(win, first, last):
    win.dt_from.setDate(QDate.fromString(first, "yyyy-MM-dd"))
    win.dt_to.setDate(QDate.fromString(last, "yyyy-MM-dd"))


def _col(win, c):
    return [win.table.item(r, c).text() for r in range(win.table.rowCount())]


def _ids(win):
    return [int(t) for t in _col(win, 0)]


def _wait_filter():
    QTest.qWait(300)


def test_window_layout_and_defaults(world):
    db, ids, make = world
    win = make(all_time=False)
    assert [win.table.horizontalHeaderItem(i).text() for i in range(7)] == [
        "ID", "Date & Time (Local)", "Actor", "Action", "Client (Name / Token)", "Service", "Detail"]
    assert win.cmb_date_preset.currentText() == "Last 30 Days"
    assert [win.cmb_date_preset.itemText(i) for i in range(win.cmb_date_preset.count())] == [
        "All Time", "Today", "Yesterday", "Last 7 Days", "Last 30 Days", "Custom Date Range"]
    assert win.workstation_list.rowCount() == 1 and win.selected_host == "local"
    assert win.table.rowCount() == 0     # seeded rows are older than 30 days; its own "viewed" entry is logged after the first load
    assert "audit_log_viewed" in [l["action"] for l in db.get_audit_logs(actor="Admin")]


def test_opening_the_window_is_itself_audited(world):
    db, ids, make = world
    make()
    mine = [l for l in db.get_audit_logs(actor="Admin") if l["action"] == "audit_log_viewed"]
    assert len(mine) == 1 and mine[0]["detail"] == "Opened SSAL Audit Log Window"


def test_every_action_type_is_listed_in_order_and_filters_to_exactly_its_rows(world):
    db, ids, make = world
    win = make()
    items = [win.action_combo.itemText(i) for i in range(win.action_combo.count())]
    assert items == ["All Actions"] + ACTIONS
    assert len(ACTIONS) == 17
    for i, action in enumerate(ACTIONS, start=1):
        win.action_combo.setCurrentText(action)
        got = [win.table.item(r, 3).toolTip() for r in range(win.table.rowCount())]
        if action == "view":
            assert sorted(_ids(win)) == [1, 19, 20, 21]
        elif action == "audit_log_viewed":
            assert i in _ids(win) and len(_ids(win)) == 2          # the seeded one and this window's own
        else:
            assert _ids(win) == [i], action
        assert set(got) == {f"Action: {action}"}
        word = BADGE_WORD[action]
        assert all(win.table.item(r, 3).text().split(" ", 1)[-1] == word for r in range(win.table.rowCount()))
    win.action_combo.setCurrentText("All Actions")
    assert win.table.rowCount() == 22                                # 21 seeded + this window's own entry


def test_unlisted_actions_still_show_in_all_actions_with_a_readable_badge(world):
    db, ids, make = world
    win = make()
    row = _ids(win).index(18)
    assert win.table.item(row, 3).text() == "\u270f\ufe0f Update"       # update_settings gets the update badge
    assert win.table.item(row, 3).toolTip() == "Action: update_settings"
    assert win.table.item(row, 2).text() == "System"
    assert win.table.item(row, 4).text() == "\u2014"                 # no client


def test_staff_filter_matches_part_of_the_name_case_insensitively(world):
    db, ids, make = world
    win = make()
    win.actor_input.setText("ash")
    _wait_filter()
    assert set(_col(win, 2)) == {"Asha"} and win.lbl_record_count.text() == f"{win.table.rowCount()} log entries"
    assert set(_ids(win)) == {1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 20}
    win.actor_input.setText("")
    _wait_filter()
    assert win.table.rowCount() == 22
    win.actor_input.setText("system")
    _wait_filter()
    assert _ids(win) == [18]


def test_custom_date_range_includes_the_whole_last_day_and_excludes_the_next(world):
    db, ids, make = world
    win = make()
    _range(win, "2026-03-01", "2026-03-05")
    assert win.cmb_date_preset.currentText() == "Custom Date Range"      # editing a date switches the preset
    assert sorted(_ids(win)) == list(range(1, 20))                      # 1..19: 21 is before, 20 is after
    _range(win, "2026-03-03", "2026-03-03")
    assert _ids(win) == [18]
    _range(win, "2026-03-06", "2026-03-06")
    assert _ids(win) == [20]


def test_date_presets_pass_the_right_window_to_the_database(world, monkeypatch):
    db, ids, make = world
    win = make()
    seen = []
    real = db.get_audit_logs
    monkeypatch.setattr(db, "get_audit_logs", lambda **kw: (seen.append(kw), real(**kw))[1])
    today = QDate.currentDate()
    cases = {
        "Today": (today, today), "Yesterday": (today.addDays(-1), today.addDays(-1)),
        "Last 7 Days": (today.addDays(-7), today), "Last 30 Days": (today.addDays(-30), today),
    }
    for preset, (a, b) in cases.items():
        seen.clear()
        win.cmb_date_preset.setCurrentText(preset)
        assert seen[-1]["from_date"] == a.toString("yyyy-MM-dd") + "T00:00:00", preset
        assert seen[-1]["to_date"] == b.addDays(1).toString("yyyy-MM-dd") + "T00:00:00", preset
        assert (win.dt_from.date(), win.dt_to.date()) == (a, b)
        assert seen[-1]["limit"] == 1000 and seen[-1]["resolve_names"] is True
    seen.clear()
    win.cmb_date_preset.setCurrentText("All Time")
    assert seen[-1]["from_date"] is None and seen[-1]["to_date"] is None
    assert win.dt_from.date() == QDate(2020, 1, 1) and win.dt_to.date() == today


def test_search_matches_client_token_actor_action_detail_and_id(world):
    db, ids, make = world
    win = make()
    for text, expected in [
        ("Alpha Traders", {1, 3, 5, 7, 9, 11, 13, 15, 17}),             # client name
        ("CLI-00003", {2, 4, 6, 8, 10, 12, 14, 16}),                    # client token
        ("detail for csv_", {10, 13}),                                  # detail text
        ("bhanu", {2, 4, 6, 8, 10, 12, 14, 16, 21}),                    # actor
        ("sync_re", {16}),                                              # action
        ("after the range", {20}),
        ("21", {21}),                                                   # row id
    ]:
        win.txt_search.setText(text)
        _wait_filter()
        assert set(_ids(win)) == expected, text
    win.txt_search.setText("")
    _wait_filter()
    assert win.table.rowCount() == 22
    assert win.lbl_record_count.text() == "22 log entries"


def test_search_and_filters_combine(world):
    db, ids, make = world
    win = make()
    win.actor_input.setText("asha")
    win.txt_search.setText("detail for")
    _wait_filter()
    assert set(_ids(win)) == {1, 3, 5, 7, 9, 11, 13, 15, 17}            # row 20 is Asha's but has other detail
    win.action_combo.setCurrentText("update")
    assert _ids(win) == []                                               # update row 6 belongs to Bhanu


def test_cells_show_name_service_and_detail_text(world):
    db, ids, make = world
    win = make()
    row = _ids(win).index(3)                                             # autofill, client A, service 1
    assert win.table.item(row, 4).text() == "Alpha Traders Pvt Ltd"
    assert win.table.item(row, 4).toolTip() == f"Client: Alpha Traders Pvt Ltd (ID: {ids['A']})"
    assert win.table.item(row, 5).text() == "1"                          # the service id, not its name
    assert win.table.item(row, 6).text() == "detail for autofill"
    assert re.fullmatch(r"\d\d-[A-Z][a-z]{2}-2026 \d\d:\d\d:\d\d [AP]M", win.table.item(row, 1).text())
    assert "UTC: 2026-03-02T10:03:00" in win.table.item(row, 1).toolTip()
    row19 = _ids(win).index(19)
    assert win.table.item(row19, 6).text() == "Viewed client profile"    # empty detail on a view row
    assert win.table.item(_ids(win).index(4), 5).text() == "\u2014"      # no service


def test_row_drill_down_and_copy_details(world):
    db, ids, make = world
    win = make()
    toasts = []
    win.toast_requested.connect(lambda m, ms: toasts.append(m))
    win.btn_copy_insp.click()
    assert toasts == [] and win.inspector_box.toPlainText() == ""        # nothing selected, nothing copied
    win.table.selectRow(_ids(win).index(3))
    text = win.inspector_box.toPlainText()
    lines = text.splitlines()
    assert lines[0].startswith("\U0001F4CC Log ID: 3  |  ") and "UTC: 2026-03-02T10:03:00" in lines[0]
    assert lines[1].endswith("Workstation: local  |  \U0001F464 Actor: Asha  |  \u26a1 Action: autofill")
    assert lines[2] == (f"\U0001F3E2 Client: Alpha Traders Pvt Ltd (Token: CLI-{ids['A']:05d} | ID: {ids['A']})"
                        f"  |  \U0001F310 Service: ID: 1")
    assert lines[3] == "\U0001F4DD Detail Payload: detail for autofill"
    win.btn_copy_insp.click()
    assert QGuiApplication.clipboard().text() == text
    assert toasts == ["Audit details copied to clipboard!"]


def test_filter_client_button_puts_the_client_in_the_search_box(world):
    db, ids, make = world
    win = make()
    win.btn_filter_client.click()                                        # no row selected: nothing happens
    assert win.txt_search.text() == ""
    win.table.selectRow(_ids(win).index(4))
    win.btn_filter_client.click()
    assert win.txt_search.text() == "Charlie Foods"
    _wait_filter()
    assert set(_ids(win)) == {2, 4, 6, 8, 10, 12, 14, 16}


def test_refresh_button_picks_up_new_entries(world):
    db, ids, make = world
    win = make()
    before = win.table.rowCount()
    db.log_action("Asha", "backup", detail="a new entry")
    assert win.table.rowCount() == before
    win.btn_refresh.click()
    assert win.table.rowCount() == before + 1
    assert "a new entry" in _col(win, 6)


def _export(win, monkeypatch, path):
    monkeypatch.setattr(ald.QFileDialog, "getSaveFileName", lambda *a, **k: (str(path), ""))
    [b for b in win.findChildren(type(win.btn_copy_insp)) if b.text() == "Export CSV..."][0].click()


def test_csv_export_matches_the_golden_file(world, monkeypatch, tmp_path):
    db, ids, make = world
    win = make()
    _range(win, "2026-03-01", "2026-03-05")
    out = tmp_path / "audit.csv"
    toasts = []
    win.toast_requested.connect(lambda m, ms: toasts.append(m))
    _export(win, monkeypatch, out)

    got = list(csv.reader(out.read_text("utf-8").splitlines()))
    want = list(csv.reader(GOLDEN_CSV.read_text("utf-8").splitlines()))
    assert got[0] == want[0] == ["ID", "Local Time (IST)", "Timestamp (UTC)", "Workstation", "Actor", "Action",
                                 "Client Token", "Client Name", "Service", "Detail"]
    for row in got[1:]:                                                  # the local time depends on this PC's zone
        assert re.fullmatch(r"\d\d-[A-Z][a-z]{2}-2026 \d\d:\d\d:\d\d [AP]M", row[1])
        row[1] = "<local>"
    assert got == want
    assert len(got) == 1 + 19
    assert toasts == [f"SSAL Audit log (local) exported successfully to:\n{out}"]
    logs = [l for l in db.get_audit_logs() if l["action"] == "csv_export" and l["id"] > 21]
    assert [l["detail"] for l in logs] == [f"Exported SSAL audit log (local) to {out}"]


def test_csv_export_follows_the_current_filters(world, monkeypatch, tmp_path):
    db, ids, make = world
    win = make()
    win.action_combo.setCurrentText("view")
    win.actor_input.setText("bhanu")
    out = tmp_path / "audit.csv"
    _export(win, monkeypatch, out)
    rows = list(csv.reader(out.read_text("utf-8").splitlines()))
    assert [r[0] for r in rows[1:]] == ["21"] and rows[1][4] == "Bhanu" and rows[1][5] == "view"


def test_csv_export_cancelled_writes_nothing(world, monkeypatch, tmp_path):
    db, ids, make = world
    win = make()
    n = len(db.get_audit_logs())
    _export(win, monkeypatch, "")
    assert len(db.get_audit_logs()) == n
