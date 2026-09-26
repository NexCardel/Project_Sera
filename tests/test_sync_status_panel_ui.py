"""Widget tests for the P3-8 Sync Status panel in the Sera Sync dialog: pending outgoing /
parked summary, the conflicts table with Keep / Use discarded value, and Members "Online"
preferring the SyncEngine's ``peer_status()`` once it's wired in (P3-7).

Follows the P2-7 pattern in tests/test_sync_office_ui.py: widget construction and the
non-networked parts, driven directly (no real sockets, no ``dlg.exec()``).

tmp_path only (SS0 rule 2). Invented test data only (SS0 rule 12).
"""

import json
import sys
import time
from unittest.mock import MagicMock

import pytest

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
pytestmark = windows_only


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _pump_until(predicate, timeout=2.0):
    """Processes the Qt event loop until predicate() is true or timeout elapses (see
    tests/test_sync_office_ui.py for why: a background-thread signal emit needs the GUI
    thread's event loop actually running to be delivered)."""
    from PySide6.QtWidgets import QApplication
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    return predicate()


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


def _office_db(tmp_path):
    import sera_keys
    import sync_office
    from database import SeraDatabase
    sync_office.create_new_office(tmp_path, "Aman Associates", "OfficeMaster#2026", "Admin PC")
    dek = sera_keys.load_dek(tmp_path)
    hex_key = sera_keys.dek_hex(dek)
    return SeraDatabase(str(tmp_path / "master.db"), hex_key, defer_startup_maintenance=True)


def _insert_conflict(db, tbl, row_key, col, kept, discarded, reason="lww"):
    with db._connect() as conn:
        conn.execute(
            "INSERT INTO _sync_conflicts(at, tbl, row_key, col, kept, discarded, reason) "
            "VALUES ('2026-09-25T00:00:00', ?, ?, ?, ?, ?, ?)",
            (tbl, row_key, col, json.dumps(kept), json.dumps(discarded), reason))


def test_sync_status_panel_hidden_in_legacy_mode(tmp_path):
    _app()
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog

    svc = _office_sync_service(tmp_path, key_id=None)
    dlg = SeraSyncDialog(svc, db=None, actor="Tester")
    assert dlg.sync_status_group.isVisible() is False


def test_sync_status_panel_visible_in_office_mode(tmp_path):
    _app()
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog

    db = _office_db(tmp_path)
    svc = _office_sync_service(tmp_path, key_id="somekeyid")
    dlg = SeraSyncDialog(svc, db=db, actor="Tester")
    try:
        # setVisible(True) on a widget under an unshown dialog doesn't make isVisible() true
        # (see test_sync_office_ui.py's members-panel test note) -- actually show it, like a
        # real dialog would be.
        dlg.show()
        _app().processEvents()
        assert dlg.sync_status_group.isVisible() is True
    finally:
        dlg.close()


def test_summary_label_shows_parked_count(tmp_path):
    _app()
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog

    db = _office_db(tmp_path)
    with db._connect() as conn:
        conn.execute("INSERT INTO _sync_parked(change_json, reason, first_at, tries) "
                     "VALUES ('{}', 'parent_missing', '2026-09-25T00:00:00', 0)")

    svc = _office_sync_service(tmp_path)
    dlg = SeraSyncDialog(svc, db=db, actor="Tester")
    dlg.sync_status_group.isVisible = lambda: True
    dlg._refresh_sync_status()

    assert "Parked: 1" in dlg.sync_status_summary_label.text()


def test_conflicts_table_populates_from_both_db_files(tmp_path):
    _app()
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog

    db = _office_db(tmp_path)
    _insert_conflict(db, "clients", json.dumps(["gid-1"]), "notes", "old", "new")

    svc = _office_sync_service(tmp_path)
    dlg = SeraSyncDialog(svc, db=db, actor="Tester")
    dlg.sync_status_group.isVisible = lambda: True
    dlg._refresh_sync_status()

    assert dlg.conflicts_table.rowCount() == 1
    assert dlg.conflicts_table.item(0, 0).text() == "clients"
    assert dlg.conflicts_table.item(0, 1).text() == "notes"
    assert dlg.conflicts_table.item(0, 2).text() == "old"
    assert dlg.conflicts_table.item(0, 3).text() == "new"


def test_keep_button_removes_the_selected_conflict(tmp_path):
    _app()
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog

    db = _office_db(tmp_path)
    _insert_conflict(db, "clients", json.dumps(["gid-1"]), "notes", "old", "new")

    svc = _office_sync_service(tmp_path)
    dlg = SeraSyncDialog(svc, db=db, actor="Tester")
    dlg.sync_status_group.isVisible = lambda: True
    dlg._refresh_sync_status()
    assert dlg.conflicts_table.rowCount() == 1

    dlg.conflicts_table.selectRow(0)
    dlg._on_conflict_keep()

    assert dlg.conflicts_table.rowCount() == 0
    with db._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM _sync_conflicts").fetchone()[0] == 0


def test_use_discarded_value_button_writes_the_field(tmp_path):
    _app()
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog

    db = _office_db(tmp_path)
    from tests.test_sync_capture import PAN_A, _add_client, _gid
    client_id = _add_client(db, PAN_A)
    with db._connect() as conn:
        conn.execute("UPDATE clients SET notes = 'original' WHERE id = ?", (client_id,))
    gid = _gid(db, "clients", client_id)
    _insert_conflict(db, "clients", json.dumps([gid]), "notes", "original", "discarded text")

    svc = _office_sync_service(tmp_path)
    dlg = SeraSyncDialog(svc, db=db, actor="Tester")
    dlg.sync_status_group.isVisible = lambda: True
    dlg._refresh_sync_status()

    dlg.conflicts_table.selectRow(0)
    dlg._on_conflict_use_discarded()

    assert dlg.conflicts_table.rowCount() == 0
    with db._connect() as conn:
        notes = conn.execute("SELECT notes FROM clients WHERE id = ?", (client_id,)).fetchone()[0]
    assert notes == "discarded text"


def test_use_discarded_value_missing_row_shows_status_and_does_not_crash(tmp_path):
    _app()
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog

    db = _office_db(tmp_path)
    _insert_conflict(db, "clients", json.dumps(["no-such-gid"]), "notes", "old", "new")

    svc = _office_sync_service(tmp_path)
    dlg = SeraSyncDialog(svc, db=db, actor="Tester")
    dlg.sync_status_group.isVisible = lambda: True
    dlg.shell_alert_or_status = MagicMock()
    dlg._refresh_sync_status()

    dlg.conflicts_table.selectRow(0)
    dlg._on_conflict_use_discarded()

    dlg.shell_alert_or_status.assert_called_once()
    # A failed attempt must not destroy the conflict record (P3-8 review, item 1).
    with db._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM _sync_conflicts").fetchone()[0] == 1


def test_use_discarded_value_failure_message_names_the_deleted_row_reason(tmp_path):
    """The two reasons sync_apply.py actually writes (P3-4 log note 10) get a specific
    explanation; anything else falls back to the generic message (P3-8 review, item 1)."""
    _app()
    import sync_panel
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog

    db = _office_db(tmp_path)
    _insert_conflict(db, "clients", json.dumps(["no-such-gid"]), "notes", "old", "new",
                      reason="edit discarded by delete")

    svc = _office_sync_service(tmp_path)
    dlg = SeraSyncDialog(svc, db=db, actor="Tester")
    dlg.sync_status_group.isVisible = lambda: True
    dlg.shell_alert_or_status = MagicMock()
    dlg._refresh_sync_status()

    dlg.conflicts_table.selectRow(0)
    dlg._on_conflict_use_discarded()

    message = dlg.shell_alert_or_status.call_args[0][0]
    assert "deleted on another PC" in message


def test_conflicts_table_shows_deleted_placeholder_for_kept_none(tmp_path):
    """Every conflict sync_apply.py writes has kept=None (the delete that won left nothing) --
    the literal text "None" would be confusing (P3-8 review, minor)."""
    _app()
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog

    db = _office_db(tmp_path)
    _insert_conflict(db, "clients", json.dumps(["gid-1"]), "notes", None, "some text")

    svc = _office_sync_service(tmp_path)
    dlg = SeraSyncDialog(svc, db=db, actor="Tester")
    dlg.sync_status_group.isVisible = lambda: True
    dlg._refresh_sync_status()

    assert dlg.conflicts_table.item(0, 2).text() == "(deleted)"


def test_needs_update_banner_from_peer_status(tmp_path):
    """peer_status()[dev]['needs_update'] is set by the engine itself the instant a session
    sees a schema mismatch (sync_engine.py's _needs_update) -- the panel just has to read it
    and word the two 'who' cases per the P3-5 note (P3-8 review, item 4)."""
    _app()
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog

    db = _office_db(tmp_path)
    svc = _office_sync_service(tmp_path)
    engine = MagicMock()
    dlg = SeraSyncDialog(svc, db=db, actor="Tester", sync_engine=engine)
    dlg.members_group.isVisible = lambda: True

    own_device_id, _ = dlg._own_identity()
    engine.peer_status.return_value = {
        own_device_id: {"needs_update": {"who": "this_pc", "mine": 1, "yours": 2}, "online": True, "last_ok": None},
    }
    dlg._refresh_members()

    assert "This PC needs updating" in dlg.members_network_warning.text()


def test_last_successful_sync_prefers_peer_status_over_address_book(tmp_path):
    """_local_addresses.local_ok_at is only updated by the side that dialled (P3-5 deviation
    9); peer_status()['last_ok'] is fresh regardless of who dialled whom, so it wins when
    present (P3-8 review, item 6)."""
    _app()
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog

    db = _office_db(tmp_path)
    svc = _office_sync_service(tmp_path)
    engine = MagicMock()
    dlg = SeraSyncDialog(svc, db=db, actor="Tester", sync_engine=engine)
    dlg.members_group.isVisible = lambda: True

    own_device_id, _ = dlg._own_identity()
    engine.peer_status.return_value = {own_device_id: {"last_ok": 1_700_000_000.0, "online": True}}
    dlg._refresh_members()

    assert dlg.members_table.item(0, 3).text() != "never"
    assert "2023-11" in dlg.members_table.item(0, 3).text()  # 1_700_000_000 -> 2023-11-14


def test_stalled_streams_shown_in_summary(tmp_path):
    _app()
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog

    db = _office_db(tmp_path)
    svc = _office_sync_service(tmp_path)
    engine = MagicMock()
    engine.stalled_streams.return_value = [{"stream": "dev-b:m", "waiting_for": 5, "have_up_to": 9}]
    dlg = SeraSyncDialog(svc, db=db, actor="Tester", sync_engine=engine)
    dlg.sync_status_group.isVisible = lambda: True
    dlg._refresh_sync_status()

    assert "Stalled: 1" in dlg.sync_status_summary_label.text()


def test_stalled_streams_absent_when_none(tmp_path):
    _app()
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog

    db = _office_db(tmp_path)
    svc = _office_sync_service(tmp_path)
    engine = MagicMock()
    engine.stalled_streams.return_value = []
    dlg = SeraSyncDialog(svc, db=db, actor="Tester", sync_engine=engine)
    dlg.sync_status_group.isVisible = lambda: True
    dlg._refresh_sync_status()

    assert "Stalled" not in dlg.sync_status_summary_label.text()


# ---------------------------------------------------------------- shadow check "Run now"

def test_run_shadow_check_calls_run_shadow_checks_and_refreshes(tmp_path, monkeypatch):
    """Same as the 30-minute timer in main.py: "Run now" only does anything in mode shadow
    (P3-8b fix a)."""
    _app()
    import sync_shadow
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog

    db = _office_db(tmp_path)
    sync_shadow.enable_shadow_mode(db, tmp_path)
    svc = _office_sync_service(tmp_path)
    dlg = SeraSyncDialog(svc, db=db, actor="Tester")
    dlg.sync_status_group.isVisible = lambda: True

    called = {}
    def _fake_run_shadow_checks(_db, _app_dir):
        called["db"] = _db
        called["app_dir"] = _app_dir
        return {}
    monkeypatch.setattr(sync_shadow, "run_shadow_checks", _fake_run_shadow_checks)

    refreshed = []
    dlg._refresh_sync_status = lambda: refreshed.append(True)

    dlg._on_run_shadow_check()
    assert dlg.btn_run_shadow_check.isEnabled() is False
    assert _pump_until(lambda: called.get("db") is not None)
    assert _pump_until(lambda: len(refreshed) >= 1)
    assert dlg.btn_run_shadow_check.isEnabled() is True
    assert called["db"] is db


def test_run_shadow_check_button_disabled_outside_shadow_mode(tmp_path):
    """P3-8b fix (a): the button itself is disabled whenever the panel refreshes and the mode
    isn't shadow (mode defaults to "off" here), mirroring the 30-minute timer's own mode check
    in main.py."""
    _app()
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog

    db = _office_db(tmp_path)
    assert db.get_sync_mode() == "off"
    svc = _office_sync_service(tmp_path)
    dlg = SeraSyncDialog(svc, db=db, actor="Tester")
    dlg.sync_status_group.isVisible = lambda: True
    dlg._refresh_sync_status()

    assert dlg.btn_run_shadow_check.isEnabled() is False


def test_run_shadow_check_is_a_noop_outside_shadow_mode(tmp_path, monkeypatch):
    """Clicking "Run now" while not in mode shadow (e.g. a stale click queued just as the mode
    changed) must not call sync_shadow.run_shadow_checks at all (P3-8b fix a)."""
    _app()
    import sync_shadow
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog

    db = _office_db(tmp_path)
    svc = _office_sync_service(tmp_path)
    dlg = SeraSyncDialog(svc, db=db, actor="Tester")
    dlg.sync_status_group.isVisible = lambda: True

    called = []
    monkeypatch.setattr(sync_shadow, "run_shadow_checks", lambda *a, **k: called.append(True))

    dlg._on_run_shadow_check()
    _app().processEvents()

    assert called == []


def test_no_selection_is_a_noop_for_both_buttons(tmp_path):
    _app()
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog

    db = _office_db(tmp_path)
    svc = _office_sync_service(tmp_path)
    dlg = SeraSyncDialog(svc, db=db, actor="Tester")
    dlg.sync_status_group.isVisible = lambda: True
    # Must not raise with nothing selected.
    dlg._on_conflict_keep()
    dlg._on_conflict_use_discarded()


# ---------------------------------------------------------------- Members "Online" via engine

def test_online_status_uses_engine_peer_status_when_present(tmp_path):
    _app()
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog

    svc = _office_sync_service(tmp_path, key_id=None)
    engine = MagicMock()
    engine.peer_status.return_value = {"dev-b": {"online": True}}
    dlg = SeraSyncDialog(svc, db=None, actor="Tester", sync_engine=engine)

    assert dlg._online_status("dev-b", own_device_id="dev-a") == "Online"
    engine.peer_status.return_value = {"dev-b": {"online": False}}
    assert dlg._online_status("dev-b", own_device_id="dev-a") == "Offline"


def test_online_status_falls_back_to_beacon_when_engine_has_no_history_for_peer(tmp_path):
    _app()
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog

    svc = _office_sync_service(tmp_path, key_id=None)
    engine = MagicMock()
    engine.peer_status.return_value = {}  # no session with dev-b yet
    discovery = MagicMock()
    discovery.get_beacon_sighting.return_value = {"seen": True}
    dlg = SeraSyncDialog(svc, db=None, actor="Tester", sync_engine=engine, discovery_service=discovery)

    assert dlg._online_status("dev-b", own_device_id="dev-a") == "Online"
    discovery.get_beacon_sighting.assert_called_once()


def test_online_status_without_engine_still_uses_beacon(tmp_path):
    _app()
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog

    svc = _office_sync_service(tmp_path, key_id=None)
    discovery = MagicMock()
    discovery.get_beacon_sighting.return_value = None
    dlg = SeraSyncDialog(svc, db=None, actor="Tester", discovery_service=discovery)

    assert dlg._online_status("dev-b", own_device_id="dev-a") == "Offline"


# ---------------------------------------------------------------- P3-8a: Parked replica stats & warnings

def test_summary_label_reads_from_replica_in_shadow_mode(tmp_path, monkeypatch):
    _app()
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog
    import sync_shadow
    import sync_capture
    import datetime

    db = _office_db(tmp_path)
    sync_shadow.enable_shadow_mode(db, tmp_path)
    replica_master, _ = sync_shadow.replica_paths(tmp_path)

    # Insert parked change into replica only, live DB has 0
    conn = sync_capture._open(str(replica_master), db.hex_key, 5.0)
    try:
        conn.execute("INSERT INTO _sync_parked(change_json, reason, first_at, tries) "
                     "VALUES ('{}', 'parent_missing', '2026-09-25T00:00:00Z', 0)")
        conn.commit()
    finally:
        conn.close()

    # Freeze now at 2 hours later
    fixed_now = datetime.datetime(2026, 9, 25, 2, 0, 0, tzinfo=datetime.timezone.utc)
    import sync_panel
    monkeypatch.setattr(sync_panel, "_now_utc", lambda: fixed_now)

    svc = _office_sync_service(tmp_path)
    dlg = SeraSyncDialog(svc, db=db, actor="Tester")
    dlg.sync_status_group.isVisible = lambda: True
    dlg._refresh_sync_status()

    assert "Parked: 1 (oldest: 2h)" in dlg.sync_status_summary_label.text()


def test_parked_warning_amber_above_1_hour_in_shadow_mode(tmp_path, monkeypatch):
    _app()
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog
    import sync_shadow
    import sync_capture
    import datetime

    db = _office_db(tmp_path)
    sync_shadow.enable_shadow_mode(db, tmp_path)
    replica_master, _ = sync_shadow.replica_paths(tmp_path)

    conn = sync_capture._open(str(replica_master), db.hex_key, 5.0)
    try:
        conn.execute("INSERT INTO _sync_parked(change_json, reason, first_at, tries) "
                     "VALUES ('{}', 'parent_missing', '2026-09-25T00:00:00Z', 0)")
        conn.commit()
    finally:
        conn.close()

    # Freeze now at 2 hours later
    fixed_now = datetime.datetime(2026, 9, 25, 2, 0, 0, tzinfo=datetime.timezone.utc)
    import sync_panel
    monkeypatch.setattr(sync_panel, "_now_utc", lambda: fixed_now)

    svc = _office_sync_service(tmp_path)
    dlg = SeraSyncDialog(svc, db=db, actor="Tester")
    dlg.sync_status_group.isVisible = lambda: True
    dlg._refresh_sync_status()

    assert not dlg.sync_status_warning_label.isHidden()
    assert "1 hour" in dlg.sync_status_warning_label.text()


def test_parked_warning_red_above_7_days(tmp_path, monkeypatch):
    _app()
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog
    import datetime

    db = _office_db(tmp_path)
    with db._connect() as conn:
        conn.execute("INSERT INTO _sync_parked(change_json, reason, first_at, tries) "
                     "VALUES ('{}', 'parent_missing', '2026-09-25T00:00:00Z', 0)")

    # Freeze now at 8 days later
    fixed_now = datetime.datetime(2026, 10, 3, 0, 0, 0, tzinfo=datetime.timezone.utc)
    import sync_panel
    monkeypatch.setattr(sync_panel, "_now_utc", lambda: fixed_now)

    svc = _office_sync_service(tmp_path)
    dlg = SeraSyncDialog(svc, db=db, actor="Tester")
    dlg.sync_status_group.isVisible = lambda: True
    dlg._refresh_sync_status()

    assert not dlg.sync_status_warning_label.isHidden()
    assert "7 days" in dlg.sync_status_warning_label.text()


# ---------------------------------------------------------------- P3-8a: Clock-ahead warning in banner

def test_members_network_warning_shows_clock_ahead(tmp_path):
    _app()
    from ui.dialogs.sera_sync_dialog import SeraSyncDialog

    db = _office_db(tmp_path)
    svc = _office_sync_service(tmp_path)
    engine = MagicMock()
    dlg = SeraSyncDialog(svc, db=db, actor="Tester", sync_engine=engine)
    dlg.members_group.isVisible = lambda: True

    own_device_id, _ = dlg._own_identity()
    engine.peer_status.return_value = {
        own_device_id: {"clock_ahead": {"ahead_ms": 180_000, "minutes": 3}, "online": True}
    }
    # Call refresh
    dlg._refresh_members()

    assert not dlg.members_network_warning.isHidden()
    assert "clock is 3 minutes ahead" in dlg.members_network_warning.text()

