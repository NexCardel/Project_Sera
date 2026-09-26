"""Tests for Sera Sync v3 P3-8: main.py's SyncEngine "synced" event wiring.

Accept (blueprint SS5 P3-8, first bullet): apply_batch's touched-table set becomes a Qt
signal throttled to one per second, refreshing only the windows relevant to the touched
tables (split from the legacy refresh-everything ``_handle_live_sync_received_main_thread``)
with no toast, sidebar indicator only.

These build a bare ``SeraApp`` (``__new__``, no ``__init__`` -- QApplication/DB start-up is
out of scope here) and drive the plain-Python methods directly, the same pattern
tests/test_sync_hotfix.py uses for start-up logic. No PySide6 event loop is needed: the
coalescing logic is plain threading, and the "main thread handler" is called directly
rather than through the Qt signal (P2-7's UI tests already cover cross-thread signal
delivery for this codebase's other bridges).

tmp_path is not needed here (no disk I/O). Invented test data only (SS0 rule 12).
"""

import threading
from unittest.mock import MagicMock

import pytest


def _stub_app():
    import main as main_module
    app = main_module.SeraApp.__new__(main_module.SeraApp)
    app._synced_tables_lock = threading.Lock()
    app._synced_tables_pending = set()
    app._synced_tables_last_emit = 0.0
    app._synced_tables_flush_timer = None
    app.sync_bridge = MagicMock()
    return app


# ---------------------------------------------------------------- _queue_synced_tables

def test_first_call_emits_immediately(monkeypatch):
    app = _stub_app()
    app._queue_synced_tables({"clients"})
    app.sync_bridge.engine_synced_signal.emit.assert_called_once_with(["clients"])
    assert app._synced_tables_pending == set()


def test_empty_tables_does_nothing(monkeypatch):
    app = _stub_app()
    app._queue_synced_tables(())
    app.sync_bridge.engine_synced_signal.emit.assert_not_called()


def test_second_call_within_a_second_is_coalesced_not_emitted_immediately(monkeypatch):
    import time
    app = _stub_app()
    app._synced_tables_last_emit = time.monotonic()  # as if just emitted
    app._queue_synced_tables({"clients"})
    app.sync_bridge.engine_synced_signal.emit.assert_not_called()
    assert app._synced_tables_pending == {"clients"}
    assert app._synced_tables_flush_timer is not None
    app._synced_tables_flush_timer.cancel()


def test_further_calls_before_flush_merge_into_the_pending_set(monkeypatch):
    import time
    app = _stub_app()
    app._synced_tables_last_emit = time.monotonic()
    app._queue_synced_tables({"clients"})
    first_timer = app._synced_tables_flush_timer
    app._queue_synced_tables({"staff_users"})
    assert app._synced_tables_pending == {"clients", "staff_users"}
    # a timer is already pending, so a second one is not started
    assert app._synced_tables_flush_timer is first_timer
    first_timer.cancel()


def test_flush_emits_the_merged_sorted_set_and_resets_state(monkeypatch):
    import time
    app = _stub_app()
    app._synced_tables_last_emit = time.monotonic()
    app._queue_synced_tables({"clients"})
    app._queue_synced_tables({"staff_users"})
    app._synced_tables_flush_timer.cancel()

    app._flush_synced_tables_locked()

    app.sync_bridge.engine_synced_signal.emit.assert_called_once_with(["clients", "staff_users"])
    assert app._synced_tables_pending == set()
    assert app._synced_tables_flush_timer is None


def test_flush_with_nothing_pending_does_not_emit(monkeypatch):
    app = _stub_app()
    app._flush_synced_tables_locked()
    app.sync_bridge.engine_synced_signal.emit.assert_not_called()


def test_on_sync_engine_event_only_queues_the_synced_kind():
    app = _stub_app()
    app.on_sync_engine_event("synced", {"tables": ["clients"], "sent": 1, "received": 1})
    app.sync_bridge.engine_synced_signal.emit.assert_called_once_with(["clients"])

    app.sync_bridge.reset_mock()
    app.on_sync_engine_event("sync_failed", {"reason": "no_address"})
    app.on_sync_engine_event("needs_update", {"who": "peer"})
    app.sync_bridge.engine_synced_signal.emit.assert_not_called()


def test_on_sync_engine_event_clock_ahead_logs_activity():
    app = _stub_app()
    app.sync_service = MagicMock()
    app.on_sync_engine_event("clock_ahead", {
        "device_id": "dev-1",
        "name": "Alice",
        "ahead_ms": 120_000,
    })
    app.sync_service.log_activity.assert_called_once_with(
        "GUARD", "PC Alice's clock is 2 minutes ahead"
    )


def test_on_sync_engine_event_clock_ahead_deduplicates_repeated_events():
    """Sending many clock_ahead events for the same PC in one session (e.g. 300 edits from
    a clock-ahead PC) logs at most once, preventing activity log flooding."""
    app = _stub_app()
    app.sync_service = MagicMock()
    for _ in range(50):
        app.on_sync_engine_event("clock_ahead", {
            "device_id": "dev-1",
            "name": "Alice",
            "ahead_ms": 120_000,
        })
    app.sync_service.log_activity.assert_called_once_with(
        "GUARD", "PC Alice's clock is 2 minutes ahead"
    )


def test_on_sync_engine_event_synced_with_no_tables_is_a_noop():
    app = _stub_app()
    app.on_sync_engine_event("synced", {"tables": [], "sent": 0, "received": 0})
    app.sync_bridge.engine_synced_signal.emit.assert_not_called()


def test_on_sync_engine_event_synced_is_ignored_in_shadow_mode():
    """In mode shadow, remote changes went to the replica, not the live DBs -- refreshing
    anyway would tell the user a live sync happened when it didn't (P3-8 review, item 5)."""
    app = _stub_app()
    app.db = MagicMock()
    app.db.get_sync_mode.return_value = "shadow"
    app.on_sync_engine_event("synced", {"tables": ["clients"]})
    app.sync_bridge.engine_synced_signal.emit.assert_not_called()


def test_on_sync_engine_event_synced_runs_in_live_mode():
    app = _stub_app()
    app.db = MagicMock()
    app.db.get_sync_mode.return_value = "live"
    app.on_sync_engine_event("synced", {"tables": ["clients"]})
    app.sync_bridge.engine_synced_signal.emit.assert_called_once_with(["clients"])


def test_on_sync_engine_event_synced_runs_when_db_is_unset():
    """No ``self.db`` yet (e.g. very early start-up) must not crash -- treated as "don't
    gate", not "suppress"."""
    app = _stub_app()
    app.on_sync_engine_event("synced", {"tables": ["clients"]})
    app.sync_bridge.engine_synced_signal.emit.assert_called_once_with(["clients"])


# ---------------------------------------------------------------- shutdown cleanup

def test_cancel_synced_tables_timer_stops_a_pending_flush():
    """A pending trailing-edge flush must not fire after shutdown starts (P3-8 review,
    minor)."""
    import time
    app = _stub_app()
    app._synced_tables_last_emit = time.monotonic()  # as if just emitted
    app._queue_synced_tables({"clients"})
    timer = app._synced_tables_flush_timer
    assert timer is not None and timer.is_alive()

    app._cancel_synced_tables_timer()

    assert app._synced_tables_flush_timer is None
    timer.join(timeout=1.0)
    assert not timer.is_alive()


def test_cancel_synced_tables_timer_is_a_noop_with_nothing_pending():
    app = _stub_app()
    app._cancel_synced_tables_timer()  # must not raise
    assert app._synced_tables_flush_timer is None


# ---------------------------------------------------------------- _handle_engine_synced_main_thread

def _stub_app_with_windows():
    app = _stub_app()
    app.dashboard_win = MagicMock()
    app.search_win = MagicMock()
    app.admin_win = MagicMock()
    app.tracker_dump_win = MagicMock()
    app.detail_win = MagicMock()
    app.detail_win.isVisible.return_value = False
    app.sidebar = MagicMock()
    app.shell = MagicMock()
    return app


def test_only_windows_mapped_to_the_touched_table_are_refreshed():
    app = _stub_app_with_windows()
    app._handle_engine_synced_main_thread(["staff_users"])

    app.admin_win.refresh.assert_called_once()
    app.dashboard_win.refresh.assert_not_called()
    app.search_win.refresh.assert_not_called()
    app.tracker_dump_win.load_data.assert_not_called()


def test_clients_table_refreshes_dashboard_and_search():
    app = _stub_app_with_windows()
    app._handle_engine_synced_main_thread(["clients"])

    app.dashboard_win.refresh.assert_called_once()
    app.search_win.refresh.assert_called_once()
    app.admin_win.refresh.assert_not_called()


def test_tracker_dump_table_reloads_the_tracker_window():
    app = _stub_app_with_windows()
    app._handle_engine_synced_main_thread(["tracker_dump"])

    app.tracker_dump_win.load_data.assert_called_once()
    app.dashboard_win.refresh.assert_not_called()


def test_detail_win_only_reloaded_when_visible_and_showing_a_client():
    app = _stub_app_with_windows()
    app.detail_win.isVisible.return_value = True
    app.detail_win.client_id = 42
    app._handle_engine_synced_main_thread(["clients"])
    app.detail_win.load_client.assert_called_once_with(42)


def test_detail_win_not_reloaded_when_hidden():
    app = _stub_app_with_windows()
    app.detail_win.isVisible.return_value = False
    app.detail_win.client_id = 42
    app._handle_engine_synced_main_thread(["clients"])
    app.detail_win.load_client.assert_not_called()


def test_sidebar_pill_updates_but_no_toast_shown():
    app = _stub_app_with_windows()
    app._handle_engine_synced_main_thread(["clients"])

    app.sidebar.notify_sync_received.assert_called_once()
    app.shell.show_alert.assert_not_called()


def test_unmapped_table_refreshes_nothing_but_still_updates_the_pill():
    app = _stub_app_with_windows()
    app._handle_engine_synced_main_thread(["audit_log"])

    app.dashboard_win.refresh.assert_not_called()
    app.search_win.refresh.assert_not_called()
    app.admin_win.refresh.assert_not_called()
    app.tracker_dump_win.load_data.assert_not_called()
    app.sidebar.notify_sync_received.assert_called_once()


def test_a_window_raising_does_not_propagate():
    app = _stub_app_with_windows()
    app.dashboard_win.refresh.side_effect = RuntimeError("boom")
    # Must not raise -- the handler's own try/except swallows it and logs instead.
    app._handle_engine_synced_main_thread(["clients"])


# ---------------------------------------------------------------- P3-8a: clock-ahead 1-hour expiry

def test_peer_status_clears_clock_ahead_after_one_hour():
    """peer_status() expires a clock-ahead entry whose seen_at is more than one hour old,
    so the banner doesn't persist forever when a peer's clock has since been corrected (P3-8a
    non-blocking review item)."""
    import threading
    import time
    import sync_engine

    engine = sync_engine.SyncEngine.__new__(sync_engine.SyncEngine)
    engine._lock = threading.Lock()
    engine._peers = {
        "dev-1": {
            "last_ok": time.time(),
            "last_attempt": None,
            "last_error": None,
            "needs_update": None,
            # seen_at set 70 minutes ago -- beyond the 1-hour threshold
            "clock_ahead": {"ahead_ms": 180_000, "minutes": 3, "seen_at": time.time() - 4200.0},
        }
    }

    status = engine.peer_status()

    assert status["dev-1"]["clock_ahead"] is None
    # The stored state must be cleared too (not just the copy returned to the caller).
    assert engine._peers["dev-1"]["clock_ahead"] is None


def test_peer_status_keeps_clock_ahead_within_one_hour():
    """A clock-ahead entry seen less than 1 hour ago is still surfaced by peer_status()."""
    import threading
    import time
    import sync_engine

    engine = sync_engine.SyncEngine.__new__(sync_engine.SyncEngine)
    engine._lock = threading.Lock()
    engine._peers = {
        "dev-1": {
            "last_ok": time.time(),
            "last_attempt": None,
            "last_error": None,
            "needs_update": None,
            "clock_ahead": {"ahead_ms": 180_000, "minutes": 3, "seen_at": time.time() - 30.0},
        }
    }

    status = engine.peer_status()

    assert status["dev-1"]["clock_ahead"] is not None
    assert status["dev-1"]["clock_ahead"]["minutes"] == 3


# ---------------------------------------------------------------- P3-8a: shadow-only seal timing / flush-on-quit

def test_seal_timing_cb_only_records_in_shadow_mode():
    """_seal_timing_cb must call sync_shadow.record_seal_timing only when mode == 'shadow',
    not in 'live' or 'off' mode (P3-8a non-blocking review item)."""
    from unittest.mock import MagicMock, patch

    db = MagicMock()
    app_dir = "some/dir"

    # Build the closure exactly as main.py does it.
    import sync_shadow
    def _seal_timing_cb(dur_ms, _db=db, _dir=app_dir):
        if _db.get_sync_mode() == "shadow":
            sync_shadow.record_seal_timing(dur_ms, _dir)

    with patch.object(sync_shadow, "record_seal_timing") as mock_record:
        db.get_sync_mode.return_value = "live"
        _seal_timing_cb(12.5)
        mock_record.assert_not_called()

        db.get_sync_mode.return_value = "off"
        _seal_timing_cb(12.5)
        mock_record.assert_not_called()

        db.get_sync_mode.return_value = "shadow"
        _seal_timing_cb(12.5)
        mock_record.assert_called_once_with(12.5, app_dir)


def test_flush_seal_timing_called_on_quit():
    """The lambda wired to app.aboutToQuit must call sync_shadow.flush_seal_timing with app_dir
    (P3-8a non-blocking review item)."""
    from unittest.mock import patch
    import sync_shadow

    app_dir = "some/dir"
    # The lambda wired in main.py:
    quit_cb = lambda _dir=app_dir: sync_shadow.flush_seal_timing(_dir)

    with patch.object(sync_shadow, "flush_seal_timing") as mock_flush:
        quit_cb()
        mock_flush.assert_called_once_with(app_dir)

