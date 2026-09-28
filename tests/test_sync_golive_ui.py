"""Widget tests for P3-9's "Go live" in the Sera Sync panel.

Same pattern as tests/test_sync_shadow_start_ui.py: widgets driven directly, no ``dlg.exec()``,
message boxes, the PIN dialog and the restart patched. tmp_path only (§0 rule 2); invented data
(§0 rule 12).
"""

import sys
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _svc(tmp_path):
    svc = MagicMock()
    svc.get_sync_state.return_value = {"status": "NORMAL"}
    svc.get_peers.return_value = []
    svc.get_activity_history.return_value = []
    svc.get_network_category.return_value = {"is_public": False}
    svc.key_id = "somekeyid"
    svc.app_dir = tmp_path
    svc.db_path = str(tmp_path / "master.db")
    return svc


def _office_db(tmp_path):
    import sera_keys
    import sync_office
    from database import SeraDatabase
    sync_office.create_new_office(tmp_path, "Aman Associates", "OfficeMaster#2026", "Admin PC")
    hex_key = sera_keys.dek_hex(sera_keys.load_dek(tmp_path))
    return SeraDatabase(str(tmp_path / "master.db"), hex_key, defer_startup_maintenance=True)


def _dialog(tmp_path, monkeypatch, *, answer=True, pin=True):
    import version
    from PySide6.QtWidgets import QMessageBox
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog
    db = _office_db(tmp_path)
    engine = MagicMock()
    engine.running = True
    engine.peer_status.return_value = {}
    dlg = SeraSyncDialog(_svc(tmp_path), db=db, actor="Tester", sync_engine=engine)
    dlg.sync_status_group.isVisible = lambda: True
    asked, restarts = [], []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: (asked.append(a[2]), QMessageBox.Yes if answer else QMessageBox.No)[1])
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: asked.append(a[2]))
    monkeypatch.setattr(SeraSyncDialog, "_confirm_admin_pin", lambda self: pin)
    monkeypatch.setattr(version, "restart_app", lambda *a, **k: restarts.append(True))
    return dlg, db, engine, asked, restarts


def _start_shadow(dlg):
    dlg._on_start_shadow_mode()


def test_go_live_enabled_only_in_shadow_mode(tmp_path, monkeypatch):
    _app()
    dlg, db, *_ = _dialog(tmp_path, monkeypatch)
    dlg._refresh_sync_status()
    assert not dlg.btn_go_live.isEnabled()
    _start_shadow(dlg)
    assert dlg.btn_go_live.isEnabled()


def test_go_live_stops_sync_stages_and_restarts(tmp_path, monkeypatch):
    _app()
    import sync_shadow
    dlg, db, engine, asked, restarts = _dialog(tmp_path, monkeypatch)
    _start_shadow(dlg)
    dlg._on_go_live()
    engine.stop.assert_called_once()
    assert restarts == [True]
    assert sync_shadow.has_pending_go_live(tmp_path)
    assert db.get_sync_mode() == "off"            # nothing captured until the restart
    text = asked[-1]
    assert "day 1 of 7" in text and "NOT" in text     # the week isn't complete
    assert "parked" in text and "2 minutes" in text
    dlg._refresh_sync_status()
    assert dlg.shadow_mode_label.text() == "Sync mode: goes live when Sera restarts"
    assert not dlg.btn_go_live.isEnabled()
    assert not dlg.btn_reset_shadow.isEnabled()


def test_go_live_needs_the_pin(tmp_path, monkeypatch):
    _app()
    import sync_shadow
    dlg, db, engine, _, restarts = _dialog(tmp_path, monkeypatch)
    _start_shadow(dlg)                                # PIN given for the start
    monkeypatch.setattr(type(dlg), "_confirm_admin_pin", lambda self: False)
    dlg._on_go_live()
    engine.stop.assert_not_called()
    assert restarts == []
    assert not sync_shadow.has_pending_go_live(tmp_path)
    assert db.get_sync_mode() == "shadow"


def test_go_live_cancelled_changes_nothing(tmp_path, monkeypatch):
    _app()
    import sync_shadow
    dlg, db, engine, _, restarts = _dialog(tmp_path, monkeypatch)
    _start_shadow(dlg)
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    dlg._on_go_live()
    engine.stop.assert_not_called()
    assert restarts == []
    assert not sync_shadow.has_pending_go_live(tmp_path)


def test_failed_go_live_restarts_sync_and_says_so(tmp_path, monkeypatch):
    """open-problems S2: a failed staging must not leave sync stopped."""
    _app()
    import sync_backup
    import sync_shadow
    dlg, db, engine, asked, restarts = _dialog(tmp_path, monkeypatch)
    _start_shadow(dlg)
    monkeypatch.setattr(sync_backup, "export_database",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full (simulated)")))
    started = []
    monkeypatch.setattr(db, "start_seal_timer", lambda *a, **k: started.append(True))
    dlg._on_go_live()
    engine.stop.assert_called_once()
    engine.start.assert_called_once()
    assert started == [True]
    assert restarts == []
    assert db.get_sync_mode() == "shadow"
    assert not sync_shadow.has_pending_go_live(tmp_path)
    assert "did not go live" in asked[-1] and "disk full" in asked[-1]
    from PySide6.QtWidgets import QApplication
    assert QApplication.overrideCursor() is None      # the wait cursor is gone again


def test_live_pc_shows_live_and_no_shadow_buttons(tmp_path, monkeypatch):
    _app()
    import sync_shadow
    dlg, db, *_ = _dialog(tmp_path, monkeypatch)
    _start_shadow(dlg)
    dlg._on_go_live()
    db.set_seal_listener(None)
    assert sync_shadow.apply_pending_go_live(tmp_path) is not None
    from database import SeraDatabase
    new_db = SeraDatabase(str(tmp_path / "master.db"), db.hex_key, defer_startup_maintenance=True)
    dlg.db = new_db
    dlg._refresh_sync_status()
    assert new_db.get_sync_mode() == "live"
    assert dlg.shadow_mode_label.text().startswith("Sync mode: live (since ")
    assert not dlg.btn_go_live.isEnabled()
    assert not dlg.btn_start_shadow.isEnabled()
    assert not dlg.btn_reset_shadow.isEnabled()
