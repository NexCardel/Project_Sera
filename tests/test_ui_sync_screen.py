"""S-12: the Sera Sync dialog on the kit (status strip, three tabs, device list, activity card).

What the fleet layout adds on top of the behaviour lock (test_ui_lock_sync.py): the office
panels still refresh while their tab is not showing, the empty state, the row tooltip with
every fact, the strip, one primary, disabled-with-reason, no literal styles.

Fictional peers only; throwaway DB under tmp_path.
"""

import os
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(__file__))
import ui_lock_support as S  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")

PEERS = [
    {"username": "Asha", "host": "DESK-ASHA", "ip": "192.168.7.21", "app_version": "v3.1.0",
     "db_mtime": "2026-03-02 10:15", "client_count": 42, "tracker_count": 7, "key_id": "k1"},
    {"username": "Bhanu", "host": "DESK-BHANU", "ip": "192.168.7.22", "app_version": "v3.0.9",
     "db_mtime": "2026-03-01 18:00", "client_count": 40, "tracker_count": 3, "key_id": "k2"},
]


def _service(tmp_path, key_id=None, peers=()):
    svc = MagicMock()
    svc.get_peers.return_value = list(peers)
    svc.get_activity_history.return_value = []
    svc.get_network_category.return_value = {"is_public": False}
    svc.key_id = key_id
    svc.app_dir = tmp_path
    svc.db_path = str(tmp_path / "master.db")
    return svc


@pytest.fixture
def made():
    S.app
    dialogs = []
    yield dialogs
    for dlg in dialogs:
        dlg.close()
        dlg.deleteLater()


def _dialog(made, svc, db=None):
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog
    dlg = SeraSyncDialog(svc, db=db, actor="Tester")
    dlg._refresh_timer.stop()
    made.append(dlg)
    return dlg


def _office(tmp_path):
    import sera_keys
    import sync_office
    from database import SeraDatabase
    office = sync_office.create_new_office(tmp_path, "Fictional Office", "OfficeMaster#2026", "Admin PC")
    hex_key = sera_keys.dek_hex(sera_keys.load_dek(tmp_path))
    return office, SeraDatabase(str(tmp_path / "master.db"), hex_key, defer_startup_maintenance=True)


def test_one_row_per_device_and_the_empty_state(made, tmp_path):
    svc = _service(tmp_path, key_id="k1", peers=PEERS)
    dlg = _dialog(made, svc)
    assert dlg.table.rowCount() == len(PEERS)
    assert dlg.table.isVisibleTo(dlg.devices_group) and not dlg.devices_empty.isVisibleTo(dlg.devices_group)
    svc.get_peers.return_value = []
    dlg.btn_refresh.click()
    assert dlg.table.rowCount() == 0
    assert not dlg.table.isVisibleTo(dlg.devices_group) and dlg.devices_empty.isVisibleTo(dlg.devices_group)
    from ui.components.kit import Banner
    assert any("same network" in b.text() for b in dlg.findChildren(Banner))


def test_empty_state_add_pc_by_ip_runs_the_same_handler(made, tmp_path, monkeypatch):
    from ui.dialogs import sera_sync_dialog as m
    calls = []
    monkeypatch.setattr(m.SeraSyncDialog, "_on_add_pc_by_ip", lambda self: calls.append(1))
    dlg = _dialog(made, _service(tmp_path, key_id="k1"))
    dlg.btn_empty_add_ip.click()
    assert calls == [1]


def test_row_tooltip_carries_every_fact_and_status_is_a_pill(made, tmp_path):
    from ui.components.kit import ROLE_PILL
    dlg = _dialog(made, _service(tmp_path, key_id="k1", peers=PEERS))
    tip = dlg.table.item(0, 0).toolTip()
    for fact in ("Asha", "DESK-ASHA", "192.168.7.21", "v3.1.0", "2026-03-02 10:15", "42 / 7", "Normal"):
        assert fact in tip
    assert dlg.table.item(0, 6).data(ROLE_PILL)[:2] == ("Normal", "emerald")
    assert dlg.table.item(1, 6).data(ROLE_PILL)[:2] == ("different office key — rejoin needed", "amber")


def test_status_strip_sync_on_and_off(made, tmp_path):
    dlg = _dialog(made, _service(tmp_path, key_id="k1", peers=PEERS))
    assert dlg.sync_pill.text() == "Sync on" and dlg.sync_pill.tone == "emerald"
    assert dlg.status_label.text() == "2 devices online"
    off = _dialog(made, None)
    assert off.sync_pill.text() == "Sync off"
    assert off.status_label.text() == "Sera Sync is off on this PC"
    assert off.devices_empty.isVisibleTo(off)


def test_legacy_mode_has_no_tab_switch(made, tmp_path):
    dlg = _dialog(made, _service(tmp_path, key_id=None))
    assert not dlg.tabs.isVisibleTo(dlg)
    assert dlg.tab_stack.currentIndex() == 0
    assert not dlg.mode_pill.isVisibleTo(dlg)


def test_office_panels_refresh_while_another_tab_shows(made, tmp_path):
    office, db = _office(tmp_path)
    dlg = _dialog(made, _service(tmp_path, key_id=office.key_id), db=db)
    dlg.show()
    S.app.processEvents()
    assert dlg.tab_stack.currentIndex() == 2  # office mode opens on Sync status
    dlg.tabs.set_current(0)
    S.app.processEvents()
    assert dlg.members_group.isVisible() is False
    dlg._refresh_members()
    dlg._refresh_sync_status()
    assert dlg.members_table.rowCount() == 1
    assert dlg.members_table.item(0, 2).text() == "Admin"
    assert dlg.mode_pill.text().startswith("Mode: ")


def test_admin_only_buttons_say_why_they_are_off(made, tmp_path):
    office, db = _office(tmp_path)
    dlg = _dialog(made, _service(tmp_path, key_id=office.key_id), db=db)
    dlg._refresh_members()
    assert dlg.btn_add_workstation.isEnabled() is True
    assert dlg.btn_become_admin.isEnabled() is False
    assert dlg.btn_become_admin.toolTip() == "This PC is already the admin"


def test_one_primary_and_tabs_switch_the_stack(made, tmp_path):
    from ui.components.kit import LabelledAction
    office, db = _office(tmp_path)
    dlg = _dialog(made, _service(tmp_path, key_id=office.key_id), db=db)
    primaries = [b for b in dlg.findChildren(LabelledAction) if b.variant == "primary"]
    assert primaries == [dlg.btn_add_workstation]
    for i in range(3):
        dlg.tabs.set_current(i)
        assert dlg.tab_stack.currentIndex() == i


def test_dialog_file_has_no_literal_styles_or_hex():
    src = (Path(__file__).resolve().parents[1] / "ui" / "dialogs" / "sera_sync_dialog.py").read_text(encoding="utf-8")
    assert "setStyleSheet(" not in src
    assert not re.search(r"#[0-9A-Fa-f]{6}\b", src)
