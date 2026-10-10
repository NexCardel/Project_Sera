"""Behaviour lock R5 (Sera Sync dialog), green on the unchanged code.

Adds only what the existing sync UI tests miss: the device table columns and data, selection,
Refresh, add/remove PC by IP, the right-click menu, the activity stream, the visibility rules
and the Close button. Shadow mode / go-live / conflicts / members are locked by
test_sync_shadow_start_ui.py, test_sync_golive_ui.py, test_sync_status_panel_ui.py and
test_sync_office_ui.py and are not repeated here.

Fictional peers only; throwaway DB under tmp_path.
"""

import json
import os
import sys
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(__file__))
import ui_lock_support as S  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")

PEERS = [
    {"username": "Asha", "host": "DESK-ASHA", "ip": "192.168.7.21", "app_version": "v3.1.0",
     "db_mtime": "2026-03-02 10:15", "client_count": 42, "tracker_count": 7, "key_id": "officekey"},
    {"username": "Bhanu", "host": "DESK-BHANU", "ip": "192.168.7.22", "app_version": "v3.0.9",
     "db_mtime": "2026-03-01 18:00", "client_count": 40, "tracker_count": 3, "key_id": "otherkey"},
]


def _service(tmp_path, key_id=None, peers=None):
    svc = MagicMock()
    svc.get_sync_state.return_value = {"status": "NORMAL"}
    svc.get_peers.return_value = list(peers if peers is not None else PEERS)
    svc.get_activity_history.return_value = []
    svc.get_network_category.return_value = {"is_public": False}
    svc.key_id = key_id
    svc.app_dir = tmp_path
    svc.db_path = str(tmp_path / "master.db")
    return svc


@pytest.fixture
def make_dialog(tmp_path):
    S.app
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog
    made = []

    def _make(key_id="officekey", peers=None, db=None, history=None):
        svc = _service(tmp_path, key_id=key_id, peers=peers)
        if history is not None:
            svc.get_activity_history.return_value = history
        dlg = SeraSyncDialog(svc, db=db, actor="Tester")
        dlg._refresh_timer.stop()
        made.append(dlg)
        return dlg, svc

    yield _make
    for dlg in made:
        dlg.close()
        dlg.deleteLater()


@pytest.fixture
def real_db(tmp_path):
    yield S.make_db(tmp_path)


@pytest.fixture
def boxes(monkeypatch):
    from ui.dialogs import sera_sync_dialog as m
    from PySide6.QtWidgets import QMessageBox
    shown = []
    answer = {"question": QMessageBox.Yes}

    def _rec(kind):
        def f(parent, title, text, *a, **k):
            shown.append((kind, title, text))
            return QMessageBox.Ok
        return f

    def q(parent, title, text, *a, **k):
        shown.append(("question", title, text))
        return answer["question"]

    monkeypatch.setattr(m.QMessageBox, "information", staticmethod(_rec("information")))
    monkeypatch.setattr(m.QMessageBox, "warning", staticmethod(_rec("warning")))
    monkeypatch.setattr(m.QMessageBox, "question", staticmethod(q))
    return shown, answer


def _row(dlg, r):
    return [dlg.table.item(r, c).text() for c in range(dlg.table.columnCount())]


# --- R5.1 device table -------------------------------------------------------------------

def test_device_table_columns_in_order(make_dialog):
    dlg, _ = make_dialog()
    heads = [dlg.table.horizontalHeaderItem(c).text() for c in range(dlg.table.columnCount())]
    assert heads == ["Username", "Hostname", "IP Address", "App Version", "DB Modified",
                     "Clients / Dumps", "Mode / Status"]


def test_device_table_shows_every_column_of_every_peer(make_dialog):
    dlg, _ = make_dialog()
    dlg._refresh_peers()
    assert dlg.table.rowCount() == 2
    assert _row(dlg, 0) == ["Asha", "DESK-ASHA", "192.168.7.21", "v3.1.0", "2026-03-02 10:15",
                            "42 CLI | 7 Dumps", "🟢 Normal"]
    assert _row(dlg, 1)[:6] == ["Bhanu", "DESK-BHANU", "192.168.7.22", "v3.0.9",
                                "2026-03-01 18:00", "40 CLI | 3 Dumps"]


def test_peer_with_other_office_key_says_rejoin_needed(make_dialog):
    dlg, _ = make_dialog()
    dlg._refresh_peers()
    assert dlg.table.item(1, 6).text() == "different office key — rejoin needed"


def test_peer_missing_fields_use_the_documented_fallbacks(make_dialog):
    dlg, _ = make_dialog(key_id=None, peers=[{"host": "H", "ip": "10.0.0.9"}])
    dlg._refresh_peers()
    assert _row(dlg, 0)[:6] == ["Unknown", "H", "10.0.0.9", "v2.4.0", "N/A", "0 CLI | 0 Dumps"]


def test_peer_dict_is_kept_on_the_username_cell(make_dialog):
    from PySide6.QtCore import Qt
    dlg, _ = make_dialog()
    dlg._refresh_peers()
    assert dlg.table.item(0, 0).data(Qt.UserRole)["ip"] == "192.168.7.21"


def test_status_label_counts_devices(make_dialog):
    dlg, svc = make_dialog()
    dlg._refresh_peers()
    assert dlg.status_label.text() == "🟢 2 devices online"
    svc.get_peers.return_value = PEERS[:1]
    dlg._refresh_peers()
    assert dlg.status_label.text() == "🟢 1 device online"
    svc.get_peers.return_value = []
    dlg._refresh_peers()
    assert dlg.status_label.text() == "🟢 0 devices online"
    assert dlg.table.rowCount() == 0


def test_table_is_single_row_selection_and_read_only(make_dialog):
    from PySide6.QtWidgets import QAbstractItemView
    dlg, _ = make_dialog()
    assert dlg.table.selectionBehavior() == QAbstractItemView.SelectRows
    assert dlg.table.selectionMode() == QAbstractItemView.SingleSelection
    assert dlg.table.editTriggers() == QAbstractItemView.NoEditTriggers


def test_selection_follows_the_same_device_across_a_refresh(make_dialog):
    dlg, svc = make_dialog()
    dlg._refresh_peers()
    dlg.table.selectRow(1)
    svc.get_peers.return_value = [PEERS[1], PEERS[0]]
    dlg._refresh_peers()
    assert dlg.table.currentRow() == 0
    assert dlg.table.item(0, 1).text() == "DESK-BHANU"


def test_refresh_button_rereads_the_peers(make_dialog):
    dlg, svc = make_dialog()
    assert dlg.btn_refresh.text().strip() == "Refresh"
    dlg.btn_refresh.click()
    assert dlg.table.rowCount() == 2
    svc.get_peers.return_value = PEERS[:1]
    dlg.btn_refresh.click()
    assert dlg.table.rowCount() == 1


def test_refresh_timer_runs_every_three_seconds(tmp_path):
    S.app
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog
    dlg = SeraSyncDialog(_service(tmp_path), db=None, actor="Tester")
    try:
        assert dlg._refresh_timer.isActive()
        assert dlg._refresh_timer.interval() == 3000
    finally:
        dlg.close()
    assert not dlg._refresh_timer.isActive()


def test_public_network_banner_follows_the_service(make_dialog):
    dlg, svc = make_dialog()
    dlg.show()
    S.app.processEvents()
    dlg._refresh_peers()
    assert dlg.network_warning_banner.isVisible() is False
    svc.get_network_category.return_value = {"is_public": True}
    dlg._refresh_peers()
    assert dlg.network_warning_banner.isVisible() is True


# --- R5.1 right-click menu ---------------------------------------------------------------

def _fake_menu(monkeypatch, on_exec):
    # QMenu.exec cannot be patched on the Qt class (a real, blocking menu opens); a subclass is honoured.
    from PySide6.QtWidgets import QMenu
    from ui.dialogs import sera_sync_dialog as m

    class FakeMenu(QMenu):
        def exec(self, *a, **k):
            return on_exec(self)

    monkeypatch.setattr(m, "QMenu", FakeMenu)


def _menu_capture(monkeypatch):
    seen = []

    def on_exec(menu):
        seen.append([(x.text(), x.isEnabled()) for x in menu.actions()])
        return None

    _fake_menu(monkeypatch, on_exec)
    return seen


def _pos_of(dlg, row):
    dlg.table.resizeRowsToContents()
    return dlg.table.visualItemRect(dlg.table.item(row, 0)).center()


def test_context_menu_on_a_broadcast_device_is_an_info_line(make_dialog, monkeypatch):
    dlg, _ = make_dialog()
    dlg.show()
    dlg._refresh_peers()
    seen = _menu_capture(monkeypatch)
    dlg._on_table_context_menu(_pos_of(dlg, 0))
    assert seen == [[("Discovered via LAN broadcast (192.168.7.21)", False)]]


def test_context_menu_on_empty_area_opens_nothing(make_dialog, monkeypatch):
    from PySide6.QtCore import QPoint
    dlg, _ = make_dialog()
    dlg.show()
    dlg._refresh_peers()
    seen = _menu_capture(monkeypatch)
    dlg._on_table_context_menu(QPoint(5, dlg.table.viewport().height() - 2))
    assert seen == []


def test_context_menu_on_a_manual_peer_offers_removal(make_dialog, real_db, monkeypatch, boxes):
    real_db.set_setting("sync_manual_peers", json.dumps(["192.168.7.21:5555"]))
    dlg, svc = make_dialog(db=real_db)
    dlg.show()
    dlg._refresh_peers()
    seen = []

    def on_exec(menu):
        seen.append([x.text() for x in menu.actions()])
        return menu.actions()[0]

    _fake_menu(monkeypatch, on_exec)
    dlg._on_table_context_menu(_pos_of(dlg, 0))
    assert seen == [["Remove '192.168.7.21:5555' from Manual Peers"]]
    shown, _ = boxes
    assert shown[0][:2] == ("question", "Confirm Removal")
    assert json.loads(real_db.get_setting("sync_manual_peers", "[]")) == []
    svc.remove_manual_peer.assert_called_once_with("192.168.7.21:5555")
    assert shown[-1][1] == "Workstation Removed"


# --- R5.1 add / remove PC by IP ----------------------------------------------------------

def _answer_input(monkeypatch, text=None, item=None, ok=True):
    from ui.dialogs import sera_sync_dialog as m
    monkeypatch.setattr(m.QInputDialog, "getText", staticmethod(lambda *a, **k: (text, ok)))
    monkeypatch.setattr(m.QInputDialog, "getItem", staticmethod(lambda *a, **k: (item, ok)))


@pytest.fixture
def no_beacon_thread(monkeypatch):
    from ui.dialogs import sera_sync_dialog as m

    class _T:
        def __init__(self, target=None, daemon=None, **k):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(m.threading, "Thread", _T)


def test_add_pc_by_ip_stores_it_and_tells_the_service(make_dialog, real_db, monkeypatch, boxes,
                                                       no_beacon_thread):
    dlg, svc = make_dialog(db=real_db)
    _answer_input(monkeypatch, text="  192.168.7.50  ")
    dlg.btn_add_ip.click()
    assert json.loads(real_db.get_setting("sync_manual_peers", "[]")) == ["192.168.7.50"]
    svc.add_manual_peer.assert_called_once_with("192.168.7.50")
    svc.send_manual_beacons.assert_called_once()
    assert svc.log_activity.call_args.args[0] == "NETWORK"
    assert boxes[0][-1][:2] == ("information", "Workstation Added")


def test_add_pc_by_ip_accepts_ip_with_port(make_dialog, real_db, monkeypatch, boxes, no_beacon_thread):
    dlg, svc = make_dialog(db=real_db)
    _answer_input(monkeypatch, text="192.168.7.51:5555")
    dlg._on_add_pc_by_ip()
    assert json.loads(real_db.get_setting("sync_manual_peers", "[]")) == ["192.168.7.51:5555"]


@pytest.mark.parametrize("text,title", [
    ("not-an-ip", "Invalid IP Address"),
    ("192.168.7", "Invalid IP Address"),
    ("192.168.7.300", "Invalid IP Address"),
    ("192.168.7.5:0", "Invalid Port"),
    ("192.168.7.5:70000", "Invalid Port"),
    ("192.168.7.5:abc", "Invalid Port"),
])
def test_add_pc_by_ip_rejects_bad_addresses(make_dialog, real_db, monkeypatch, boxes, text, title):
    dlg, svc = make_dialog(db=real_db)
    _answer_input(monkeypatch, text=text)
    dlg._on_add_pc_by_ip()
    assert boxes[0][0][:2] == ("warning", title)
    assert real_db.get_setting("sync_manual_peers", "[]") in ("[]", None, "")
    svc.add_manual_peer.assert_not_called()


def test_add_pc_by_ip_twice_says_already_added(make_dialog, real_db, monkeypatch, boxes,
                                               no_beacon_thread):
    real_db.set_setting("sync_manual_peers", json.dumps(["192.168.7.50"]))
    dlg, svc = make_dialog(db=real_db)
    _answer_input(monkeypatch, text="192.168.7.50")
    dlg._on_add_pc_by_ip()
    assert boxes[0][0][:2] == ("information", "Already Added")
    svc.add_manual_peer.assert_not_called()


def test_add_pc_by_ip_cancel_and_blank_change_nothing(make_dialog, real_db, monkeypatch, boxes):
    dlg, svc = make_dialog(db=real_db)
    _answer_input(monkeypatch, text="192.168.7.50", ok=False)
    dlg._on_add_pc_by_ip()
    _answer_input(monkeypatch, text="")
    dlg._on_add_pc_by_ip()
    _answer_input(monkeypatch, text="   ")
    dlg._on_add_pc_by_ip()
    assert boxes[0] == []
    svc.add_manual_peer.assert_not_called()


def test_remove_pc_by_ip_with_nothing_configured_says_so(make_dialog, real_db, boxes):
    dlg, _ = make_dialog(db=real_db)
    dlg.btn_remove_ip.click()
    assert boxes[0][0][:2] == ("information", "Remove PC by IP")
    assert "No manual workstation addresses" in boxes[0][0][2]


def test_remove_pc_by_ip_confirms_then_removes_only_that_entry(make_dialog, real_db, monkeypatch, boxes):
    real_db.set_setting("sync_manual_peers", json.dumps(["192.168.7.50", "192.168.7.51:5555"]))
    dlg, svc = make_dialog(db=real_db)
    _answer_input(monkeypatch, item="192.168.7.51:5555")
    dlg.btn_remove_ip.click()
    assert [b[:2] for b in boxes[0]] == [("question", "Confirm Removal"),
                                          ("information", "Workstation Removed")]
    assert json.loads(real_db.get_setting("sync_manual_peers", "[]")) == ["192.168.7.50"]
    svc.remove_manual_peer.assert_called_once_with("192.168.7.51:5555")


def test_remove_pc_by_ip_declined_keeps_the_entry(make_dialog, real_db, monkeypatch, boxes):
    from PySide6.QtWidgets import QMessageBox
    real_db.set_setting("sync_manual_peers", json.dumps(["192.168.7.50"]))
    dlg, svc = make_dialog(db=real_db)
    boxes[1]["question"] = QMessageBox.No
    _answer_input(monkeypatch, item="192.168.7.50")
    dlg._on_remove_pc_by_ip()
    assert json.loads(real_db.get_setting("sync_manual_peers", "[]")) == ["192.168.7.50"]
    svc.remove_manual_peer.assert_not_called()


def test_remove_pc_by_ip_offers_the_selected_devices_entry_first(make_dialog, real_db, monkeypatch, boxes):
    real_db.set_setting("sync_manual_peers", json.dumps(["192.168.7.50", "192.168.7.22:5555"]))
    dlg, _ = make_dialog(db=real_db)
    dlg._refresh_peers()
    dlg.table.selectRow(1)
    from ui.dialogs import sera_sync_dialog as m
    got = {}

    def get_item(parent, title, label, items, current, editable):
        got.update(items=items, current=current)
        return "", False

    monkeypatch.setattr(m.QInputDialog, "getItem", staticmethod(get_item))
    dlg._on_remove_pc_by_ip()
    assert got == {"items": ["192.168.7.50", "192.168.7.22:5555"], "current": 1}


# --- R5.2 activity stream ----------------------------------------------------------------

def test_activity_history_is_loaded_when_the_dialog_opens(make_dialog):
    hist = [{"timestamp": "10:01:02", "category": "PULL", "title": "Pulled", "detail": "3 rows"},
            {"timestamp": "10:02:03", "category": "info", "title": "Idle", "detail": ""}]
    dlg, _ = make_dialog(history=hist)
    assert dlg.log_list.count() == 2
    assert dlg.log_list.item(0).text() == "[10:01:02] 📥    PULL    | Pulled - 3 rows"
    assert dlg.log_list.item(1).text() == "[10:02:03] ℹ️    INFO    | Idle"


def test_live_activity_arrives_through_the_service_callback(make_dialog):
    dlg, svc = make_dialog()
    assert dlg.log_list.count() == 0
    svc.on_activity("11:00:00", "SYNC IN", "Accepted 4 rows")
    assert dlg.log_list.count() == 1
    assert "Accepted 4 rows" in dlg.log_list.item(0).text()
    dlg.activity_signal.emit("11:00:01", "BEACON", "Found DESK-ASHA")
    assert dlg.log_list.count() == 2
    assert dlg.log_list.item(1).text().startswith("[11:00:01] 🟢")


@pytest.mark.parametrize("cat,badge", [
    ("INV_FRAMES", "🛡️"), ("REVISION", "📊"), ("BEACON", "🟢"), ("GUARD", "⚠️"),
    ("PULL", "📥"), ("PUSH", "📤"), ("SDIS", "🗂️"), ("SSAL", "📋"),
    ("SYNC IN", "✅"), ("ACCEPTED", "✅"), ("NETWORK", "ℹ️"),
])
def test_each_activity_category_has_its_badge(make_dialog, cat, badge):
    dlg, _ = make_dialog()
    dlg._on_activity_received("t", cat, "m")
    assert f" {badge} " in dlg.log_list.item(0).text()


def test_activity_list_is_capped_at_400_oldest_dropped(make_dialog):
    dlg, _ = make_dialog()
    for i in range(405):
        dlg._add_log_item("t", "INFO", f"n{i}", auto_scroll=False)
    assert dlg.log_list.count() == 400
    assert dlg.log_list.item(0).text().endswith("| n5")


def test_clear_button_empties_the_activity_stream(make_dialog):
    from PySide6.QtWidgets import QPushButton
    dlg, _ = make_dialog()
    dlg._on_activity_received("t", "INFO", "one")
    dlg._on_activity_received("t", "INFO", "two")
    clear = [b for b in dlg.findChildren(QPushButton) if b.text().strip() == "Clear"]
    assert len(clear) == 1
    clear[0].click()
    assert dlg.log_list.count() == 0


# --- R5.2 reachability / visibility ------------------------------------------------------

BUTTONS = ["btn_refresh", "btn_add_ip", "btn_remove_ip", "btn_rejoin", "btn_export_kit",
           "btn_add_workstation", "btn_remove_member", "btn_hand_over_admin", "btn_become_admin",
           "btn_start_shadow", "btn_reset_shadow", "btn_go_live", "btn_run_shadow_check",
           "btn_conflict_keep", "btn_conflict_use_discarded", "btn_close"]


def test_every_sync_control_still_exists(make_dialog):
    from PySide6.QtWidgets import QPushButton
    dlg, _ = make_dialog()
    for name in BUTTONS:
        assert isinstance(getattr(dlg, name), QPushButton), name


def test_office_mode_shows_recovery_kit_and_members_not_rejoin(make_dialog, real_db):
    dlg, _ = make_dialog(key_id="officekey", db=real_db)
    dlg.show()
    S.app.processEvents()
    assert dlg.btn_export_kit.isVisible() is True
    assert dlg.btn_rejoin.isVisible() is False
    assert dlg.members_group.isVisible() is True
    assert dlg.sync_status_group.isVisible() is True
    assert dlg.devices_group.isVisible() is True


def test_legacy_mode_shows_rejoin_not_kit_members_or_status(make_dialog):
    dlg, _ = make_dialog(key_id=None)
    dlg.show()
    S.app.processEvents()
    assert dlg.btn_rejoin.isVisible() is True
    assert dlg.btn_export_kit.isVisible() is False
    assert dlg.members_group.isVisible() is False
    assert dlg.sync_status_group.isVisible() is False
    assert dlg.devices_group.isVisible() is True


def test_recovery_kit_button_opens_the_export_flow(make_dialog, real_db, monkeypatch, tmp_path):
    from ui.dialogs import change_master_password_dialog as cm
    calls = []
    monkeypatch.setattr(cm, "export_recovery_kit_flow", lambda parent, app_dir: calls.append(app_dir))
    dlg, _ = make_dialog(key_id="officekey", db=real_db)
    dlg.btn_export_kit.click()
    assert calls == [tmp_path]


def test_rejoin_asks_first_then_schedules_and_restarts(make_dialog, monkeypatch, boxes, tmp_path):
    import sync_rejoin
    import version
    written, restarted = [], []
    monkeypatch.setattr(sync_rejoin, "write_rejoin_request",
                        lambda folder, requested_by: written.append((folder, requested_by)))
    monkeypatch.setattr(version, "restart_app", lambda: restarted.append(True))
    dlg, svc = make_dialog(key_id=None)
    dlg.btn_rejoin.click()
    assert boxes[0][0][:2] == ("question", "Rejoin Office")
    assert written == [(tmp_path, "Tester")]
    svc.stop.assert_called_once()
    assert restarted == [True]


def test_rejoin_declined_changes_nothing(make_dialog, monkeypatch, boxes):
    from PySide6.QtWidgets import QMessageBox
    import sync_rejoin
    import version
    written, restarted = [], []
    monkeypatch.setattr(sync_rejoin, "write_rejoin_request", lambda *a, **k: written.append(1))
    monkeypatch.setattr(version, "restart_app", lambda: restarted.append(True))
    dlg, svc = make_dialog(key_id=None)
    boxes[1]["question"] = QMessageBox.No
    dlg._on_rejoin_office()
    assert written == [] and restarted == []
    svc.stop.assert_not_called()


def test_close_button_closes_the_dialog_and_stops_the_timer(tmp_path):
    S.app
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog
    dlg = SeraSyncDialog(_service(tmp_path), db=None, actor="Tester")
    dlg.show()
    assert dlg.btn_close.text() == "Close"
    dlg.btn_close.click()
    assert dlg.isVisible() is False
    assert dlg.result() == 1
    assert dlg._closed is True
    dlg.deleteLater()


def test_legacy_push_pull_buttons_are_gone(make_dialog):
    """P4-1 removed 'Sync selected' / 'Sync all'; the handlers must stay absent so nothing
    can talk the old whole-database protocol."""
    dlg, _ = make_dialog()
    assert not hasattr(dlg, "btn_sync")
    assert not hasattr(dlg, "btn_sync_all")
    assert not hasattr(dlg, "_on_sync_clicked")
    assert not hasattr(dlg, "_on_sync_all_clicked")
