"""Widget-construction smoke tests for the P2-7 UI: FirstRunDialog's office-mode pages
(New Office / Join Office pairing) and SeraSyncDialog's Office Members panel.

§5 P2-7's own Accept line is light ("manual click-through by the owner. Widget construction
smoke tests."): these tests build the widgets and drive the non-networked parts (validation,
visibility, table population) without opening real sockets -- the networked path is covered
end to end in tests/test_sync_office.py.
"""

import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
pytestmark = windows_only


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _pump_until(predicate, timeout=2.0):
    """Processes the Qt event loop until predicate() is true or timeout elapses.

    Used to prove a signal emitted from a background thread is actually delivered: a plain
    ``QTimer.singleShot(0, fn)`` called from a non-GUI thread creates the timer on that
    thread (which has no event loop) and never fires -- this would hang/timeout instead.
    """
    from PySide6.QtWidgets import QApplication
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    return predicate()


# ------------------------------------------------------------------ FirstRunDialog (office mode)

def test_first_run_dialog_has_office_mode_pages(tmp_path):
    _app()
    from ui.dialogs.first_run_dialog import FirstRunDialog

    dlg = FirstRunDialog(app_dir=tmp_path, actor_alias="TestAdmin")
    assert dlg.stack.count() == 6
    assert dlg.office_mode is False

    # Choice page routes "Create a New Office" / "Join an Existing Office" to the office-mode
    # pages (index 4/5), not the legacy no-office-key pages (index 1/2).
    dlg.stack.setCurrentIndex(4)
    assert dlg.stack.currentIndex() == 4
    dlg.stack.setCurrentIndex(5)
    assert dlg.stack.currentIndex() == 5


def test_first_run_office_new_page_validates_before_creating(tmp_path):
    _app()
    from ui.dialogs.first_run_dialog import FirstRunDialog

    dlg = FirstRunDialog(app_dir=tmp_path, actor_alias="TestAdmin")
    dlg.stack.setCurrentIndex(4)

    dlg.office_name_input.setText("")
    dlg.office_new_pwd_input.setText("OfficeMaster#2026")
    dlg.office_new_pwd_confirm.setText("OfficeMaster#2026")
    dlg._handle_create_office_v3()
    assert dlg.office_new_error_label.text()
    assert not (tmp_path / "master.db").exists()
    assert dlg.office_mode is False


def test_first_run_office_new_page_creates_office(tmp_path):
    _app()
    from ui.dialogs.first_run_dialog import FirstRunDialog
    import sera_keys

    dlg = FirstRunDialog(app_dir=tmp_path, actor_alias="TestAdmin")
    dlg.stack.setCurrentIndex(4)
    dlg.office_name_input.setText("Aman Associates")
    dlg.office_new_pwd_input.setText("OfficeMaster#2026")
    dlg.office_new_pwd_confirm.setText("OfficeMaster#2026")

    dlg._handle_create_office_v3()

    assert dlg.office_mode is True
    assert dlg.result() == 1  # QDialog.Accepted
    assert (tmp_path / "master.db").exists()
    assert (sera_keys.keys_dir(tmp_path) / sera_keys.OFFICE_FILE).exists()
    # No legacy sera.key/sera.salt: office mode never uses them (§5 P1-2).
    assert not (tmp_path / "sera.key").exists()


def test_first_run_office_join_page_requires_ip_and_code(tmp_path):
    _app()
    from ui.dialogs.first_run_dialog import FirstRunDialog

    dlg = FirstRunDialog(app_dir=tmp_path, actor_alias="TestAdmin")
    dlg.stack.setCurrentIndex(5)

    dlg.office_manual_ip_input.setText("")
    dlg.office_code_input.setText("")
    dlg._handle_office_join()
    assert "Select an admin PC" in dlg.office_join_status_label.text()

    dlg.office_manual_ip_input.setText("192.168.1.50")
    dlg.office_code_input.setText("")
    dlg._handle_office_join()
    assert "Enter the 6-digit code" in dlg.office_join_status_label.text()


def test_first_run_office_signals_deliver_across_threads(tmp_path, monkeypatch):
    """A previous review found QTimer.singleShot(0, fn) called from the scan/join worker
    threads never fired (the timer is created on the calling thread, which has no Qt event
    loop). These signals must arrive on the GUI thread instead -- prove it for real, from a
    real background thread, rather than just calling the slot directly.

    office_join_done_signal is also connected (in __init__) to _on_office_join_done, which
    pops a real QMessageBox.warning on failure; that's patched out here so the test can't
    block on an unattended modal (emitting True instead would skip _on_office_join_done's
    failure branch entirely and prove less)."""
    from unittest.mock import patch
    from PySide6.QtWidgets import QMessageBox
    _app()
    from ui.dialogs.first_run_dialog import FirstRunDialog

    dlg = FirstRunDialog(app_dir=tmp_path, actor_alias="TestAdmin")
    received = []
    dlg.office_peer_found_signal.connect(lambda peer: received.append(peer))
    dlg.office_join_done_signal.connect(lambda ok, reason: received.append((ok, reason)))
    dlg.legacy_scan_finished_signal.connect(lambda: received.append("legacy_scan_done"))

    def _emit():
        time.sleep(0.05)
        dlg.office_peer_found_signal.emit({"ip": "127.0.0.1"})
        dlg.office_join_done_signal.emit(False, "no admin PC reachable")
        dlg.legacy_scan_finished_signal.emit()

    with patch.object(QMessageBox, "warning", return_value=QMessageBox.Ok):
        t = threading.Thread(target=_emit, daemon=True)
        t.start()
        assert _pump_until(lambda: len(received) >= 3)
        t.join()
    assert received[0] == {"ip": "127.0.0.1"}
    assert received[1] == (False, "no admin PC reachable")
    assert received[2] == "legacy_scan_done"


# ------------------------------------------------------------------ SeraSyncDialog (Office Members panel)

def _office_sync_service(tmp_path, key_id="somekeyid"):
    svc = MagicMock()
    svc.get_sync_state.return_value = {"status": "NORMAL"}
    svc.get_peers.return_value = []
    svc.get_activity_history.return_value = []
    svc.get_network_category.return_value = {"is_public": False}
    svc.inv_frames = False
    svc.key_id = key_id
    svc.app_dir = tmp_path
    svc.db_path = str(tmp_path / "master.db")
    return svc


def test_members_panel_hidden_in_legacy_mode(tmp_path):
    _app()
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog

    svc = _office_sync_service(tmp_path, key_id=None)
    dlg = SeraSyncDialog(svc, db=None, actor="Tester")
    assert dlg.members_group.isVisible() is False


def test_members_panel_visible_in_office_mode_and_populates(tmp_path):
    _app()
    import sync_office
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog

    office = sync_office.create_new_office(tmp_path, "Aman Associates", "OfficeMaster#2026", "Admin PC")

    import sera_keys
    from database import SeraDatabase
    dek = sera_keys.load_dek(tmp_path)
    hex_key = sera_keys.dek_hex(dek)
    db = SeraDatabase(str(tmp_path / "master.db"), hex_key, defer_startup_maintenance=True)

    svc = _office_sync_service(tmp_path, key_id=office.key_id)
    dlg = SeraSyncDialog(svc, db=db, actor="Tester")
    # The dialog is never shown/exec'd in this test, so QWidget.isVisible() would read False
    # regardless of setVisible(True) (it also depends on the (unshown) top-level window's
    # visibility); override it directly to exercise the populate logic.
    dlg.members_group.isVisible = lambda: True
    dlg._refresh_members()

    assert dlg.members_table.rowCount() == 1
    assert dlg.members_table.item(0, 1).text() == "Online (this PC)"
    assert dlg.members_table.item(0, 2).text() == "Admin"
    assert dlg.members_table.item(0, 4).text() == "This PC"
    assert dlg.btn_add_workstation.isEnabled() is True
    assert dlg.btn_become_admin.isEnabled() is False


def test_workstation_signals_deliver_across_threads(tmp_path):
    """A previous review found on_joined/on_closed (called by PairingWindow's own worker
    thread, see sync_pairing.py) never reached the dialog: QTimer.singleShot(0, fn) from a
    non-GUI thread never fires. Prove the fix delivers from a real background thread."""
    _app()
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog

    svc = _office_sync_service(tmp_path, key_id=None)
    dlg = SeraSyncDialog(svc, db=None, actor="Tester")
    received = []
    dlg.workstation_joined_signal.connect(lambda record: received.append(("joined", record)))
    dlg.workstation_closed_signal.connect(lambda reason: received.append(("closed", reason)))

    def _emit():
        time.sleep(0.05)
        dlg.workstation_joined_signal.emit({"device_id": "b" * 32, "name": "PC B"})
        dlg.workstation_closed_signal.emit("joined")

    t = threading.Thread(target=_emit, daemon=True)
    t.start()
    assert _pump_until(lambda: len(received) >= 2)
    t.join()
    assert received[0] == ("joined", {"device_id": "b" * 32, "name": "PC B"})
    assert received[1] == ("closed", "joined")
    # _on_add_workstation_closed (also connected) ran on the GUI thread and cleared this.
    assert dlg._add_workstation_session is None
