"""
tests/test_tracker_dump_pagination.py
-------------------------------------
Unit tests ensuring Tracker Dump preserves current page, selected row/column,
and scroll position across dialog openings and data refreshes.
"""

import pytest
from PySide6.QtWidgets import QApplication
from ui.windows.tracker_dump_window import TrackerDumpWindow


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_tracker_dump_preserves_page_on_reload(qapp):
    """When on page 2 or 3, load_data(reset_page=False) must stay on that page."""
    class DummyDB:
        def get_all_clients(self): return []
        def get_client_services(self, cid): return []
        def get_client_services_for_client(self, cid): return []
        def get_all_client_services(self): return []
        def data_generation(self): return 1
        def raw_generation(self): return 1
        def get_srpf_containers(self, **kw):
            # Generate 75 items (3 pages of 25)
            return [
                {
                    "identity_key": f"PAN{i:05d}",
                    "client_name": f"Client {i}",
                    "pan": f"ABCDE{i:04d}F",
                    "portal": "Income Tax",
                    "latest_period": "AY 2026-27",
                    "period_summary": "1 Capture",
                    "latest_arn": f"ARN{i}",
                    "capture_method": "VSDC_live",
                    "filing_history": [],
                    "last_updated": f"2026-03-20T10:{i%60:02d}:00Z",
                    "total_captures": 1,
                    "status": "Processed",
                }
                for i in range(75)
            ]
        def get_tracker_dumps(self, **kw): return []

    win = TrackerDumpWindow(db=DummyDB(), defer_first_load=True)
    win._page_size = 25
    win.load_data(reset_page=True)
    assert win._current_page == 1

    # User navigates to page 3
    win._current_page = 3
    win._apply_filters()
    assert win._current_page == 3
    assert len(win._current_page_items) == 25

    # Select row 5, col 1
    win.table.setCurrentCell(5, 1)
    assert win.table.currentRow() == 5

    # Simulate closing a dialog / reload data without reset
    win.load_data(reset_page=False)

    # Must still be on page 3, with row 5 selected
    assert win._current_page == 3
    assert win.table.currentRow() == 5


def test_restore_table_position_restores_target_key(qapp):
    """_restore_table_position restores exact target item by identity_key."""
    class DummyDB:
        def get_all_clients(self): return []
        def get_client_services(self, cid): return []
        def get_client_services_for_client(self, cid): return []
        def get_all_client_services(self): return []
        def get_srpf_containers(self, **kw): return []
        def get_tracker_dumps(self, **kw): return []

    win = TrackerDumpWindow(db=DummyDB(), defer_first_load=True)
    win._page_size = 10
    win._current_page = 2
    win._filtered_cache = [
        {"identity_key": f"KEY_{i}", "client_name": f"Client {i}"}
        for i in range(30)
    ]
    win._current_page_items = win._filtered_cache[10:20]
    win.table.setRowCount(10)
    win.table.setColumnCount(7)

    # Restore position pointing to target_key "KEY_14" (which is index 4 in current_page_items)
    win._restore_table_position(page=2, row=0, col=2, v_scroll=0, h_scroll=0, target_key="KEY_14")

    assert win._current_page == 2
    assert win.table.currentRow() == 4
    assert win.table.currentColumn() == 2
