"""Widget tests for P3-7b's "Start shadow mode" / "Reset shadow mode" in the Sera Sync panel.

Same pattern as tests/test_sync_status_panel_ui.py: widgets driven directly, no ``dlg.exec()``,
message boxes and the PIN dialog patched. tmp_path only (§0 rule 2); invented data (§0 rule 12).
"""

import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _pump_until(predicate, timeout=5.0):
    from PySide6.QtWidgets import QApplication
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    return predicate()


def _svc(tmp_path):
    svc = MagicMock()
    svc.get_sync_state.return_value = {"status": "NORMAL"}
    svc.get_peers.return_value = []
    svc.get_activity_history.return_value = []
    svc.get_network_category.return_value = {"is_public": False}
    svc.inv_frames = False
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


def _dialog(tmp_path, monkeypatch, *, answer=True, pin=True, engine=None):
    from PySide6.QtWidgets import QMessageBox
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog
    db = _office_db(tmp_path)
    dlg = SeraSyncDialog(_svc(tmp_path), db=db, actor="Tester", sync_engine=engine)
    dlg.sync_status_group.isVisible = lambda: True
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: (asked.append(a[2]), QMessageBox.Yes if answer else QMessageBox.No)[1])
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: asked.append(a[2]))
    monkeypatch.setattr(SeraSyncDialog, "_confirm_admin_pin", lambda self: pin)
    return dlg, db, asked


def test_start_button_enabled_only_in_mode_off(tmp_path, monkeypatch):
    _app()
    dlg, db, _ = _dialog(tmp_path, monkeypatch)
    dlg._refresh_sync_status()
    assert dlg.btn_start_shadow.isEnabled()
    assert not dlg.btn_reset_shadow.isEnabled()
    assert dlg.shadow_mode_label.text() == "Shadow mode: off"


def test_start_on_the_admin_pc_turns_shadow_on_after_pin(tmp_path, monkeypatch):
    _app()
    import sync_shadow
    dlg, db, asked = _dialog(tmp_path, monkeypatch)
    dlg._on_start_shadow_mode()
    assert db.get_sync_mode() == "shadow"
    assert sync_shadow.replica_paths(tmp_path)[0].exists()
    assert "7-day" in asked[0] and "admin PC" in asked[0]
    assert "day 1 of 7" in dlg.shadow_mode_label.text()
    assert not dlg.btn_start_shadow.isEnabled()
    assert dlg.btn_reset_shadow.isEnabled()


def test_start_needs_the_pin(tmp_path, monkeypatch):
    _app()
    dlg, db, _ = _dialog(tmp_path, monkeypatch, pin=False)
    dlg._on_start_shadow_mode()
    assert db.get_sync_mode() == "off"


def test_start_cancelled_at_confirmation_changes_nothing(tmp_path, monkeypatch):
    _app()
    dlg, db, _ = _dialog(tmp_path, monkeypatch, answer=False)
    dlg._on_start_shadow_mode()
    assert db.get_sync_mode() == "off"


def test_reset_sets_mode_off_and_reenables_start_only_where_allowed(tmp_path, monkeypatch):
    _app()
    import sync_shadow
    dlg, db, _ = _dialog(tmp_path, monkeypatch)
    dlg._on_start_shadow_mode()
    dlg._on_reset_shadow_mode()
    assert db.get_sync_mode() == "off"
    assert not sync_shadow.replica_paths(tmp_path)[0].exists()
    assert list((tmp_path / "shadow").glob("replica_master.db.bak-*"))
    assert dlg.shadow_mode_label.text() == "Shadow mode: off"


def test_non_admin_download_shows_count_and_stages_then_restarts(tmp_path, monkeypatch):
    """Non-admin path with the network part patched: the count dialog shows what only this PC
    has, Yes stages the start and restarts."""
    _app()
    import sync_shadow
    import version
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog
    monkeypatch.setattr(SeraSyncDialog, "_own_identity", lambda self: ("dev-b", "pem"))
    monkeypatch.setattr(sync_shadow, "admin_device_id", lambda db, app_dir=None: "dev-a")
    session = MagicMock()
    monkeypatch.setattr(sync_shadow, "open_admin_session", lambda engine, db, app_dir=None: session)
    report = SimpleNamespace(pk_label="PAN", to_insert=[1, 2], conflicts=[1], audit_new=3,
                             tracker_new=4, timelines_new=0, no_pk=[], ambiguous=[])
    plan = SimpleNamespace(report=report)
    monkeypatch.setattr(sync_shadow, "request_shadow_start", lambda db, app_dir, s: plan)
    staged, restarted = [], []
    monkeypatch.setattr(sync_shadow, "stage_shadow_start", lambda app_dir, p: staged.append(p))
    monkeypatch.setattr(version, "restart_app", lambda: restarted.append(True))

    dlg, db, asked = _dialog(tmp_path, monkeypatch, engine=MagicMock())
    dlg._on_start_shadow_mode()
    assert _pump_until(lambda: restarted)
    assert staged == [plan]
    session.close.assert_called_once()
    count_text = asked[1]
    assert "2 client(s)" in count_text and "1 client(s) with different values" in count_text
    assert "3 audit" in count_text and "4 tracker" in count_text


def test_non_admin_download_declined_cancels(tmp_path, monkeypatch):
    _app()
    import sync_shadow
    from PySide6.QtWidgets import QMessageBox
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog
    monkeypatch.setattr(SeraSyncDialog, "_own_identity", lambda self: ("dev-b", "pem"))
    monkeypatch.setattr(sync_shadow, "admin_device_id", lambda db, app_dir=None: "dev-a")
    monkeypatch.setattr(sync_shadow, "open_admin_session", lambda *a, **k: MagicMock())
    report = SimpleNamespace(pk_label="PAN", to_insert=[], conflicts=[], audit_new=0,
                             tracker_new=0, timelines_new=0, no_pk=[], ambiguous=[])
    monkeypatch.setattr(sync_shadow, "request_shadow_start", lambda *a, **k: SimpleNamespace(report=report))
    cancelled = []
    monkeypatch.setattr(sync_shadow, "cancel_shadow_start", lambda app_dir: cancelled.append(True))

    dlg, db, _ = _dialog(tmp_path, monkeypatch, engine=MagicMock())
    answers = iter([QMessageBox.Yes, QMessageBox.No])
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: next(answers))
    dlg._on_start_shadow_mode()
    assert _pump_until(lambda: cancelled)
    assert db.get_sync_mode() == "off"


def test_non_admin_download_error_is_shown(tmp_path, monkeypatch):
    _app()
    import sync_shadow
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog
    monkeypatch.setattr(SeraSyncDialog, "_own_identity", lambda self: ("dev-b", "pem"))
    monkeypatch.setattr(sync_shadow, "admin_device_id", lambda db, app_dir=None: "dev-a")

    def _boom(*a, **k):
        raise sync_shadow.ShadowStartError("the admin PC is not in shadow mode yet")
    monkeypatch.setattr(sync_shadow, "open_admin_session", _boom)
    dlg, db, asked = _dialog(tmp_path, monkeypatch, engine=MagicMock())
    dlg._on_start_shadow_mode()
    assert _pump_until(lambda: any("not in shadow mode" in str(x) for x in asked))
    assert dlg.btn_start_shadow.isEnabled()
