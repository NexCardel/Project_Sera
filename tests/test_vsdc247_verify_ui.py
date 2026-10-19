"""
tests/test_vsdc247_verify_ui.py
--------------------------------
Unit tests for WP6: Tracker Dump window highlighting and filtering of
VSDC247_verify flagged rows.
"""

import pytest
from PySide6.QtWidgets import QApplication
from ui.windows.tracker_dump_window import TrackerDumpWindow, _passes_source_filter


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_source_filter_preserves_vsdc247_verify_raw():
    """VSDC247_verify row must NOT be hidden by 'Hide SGT' and must NOT show under 'SGT Only'."""
    raw_row = {
        "id": 1,
        "capture_method": "VSDC247_verify",
        "dataset_key": "V247:GST:AA0000000000000",
        "arn_number": "AA0000000000000",
    }
    # Under All Sources: shown
    assert _passes_source_filter(raw_row, "All Sources", is_grouped=False) is True
    # Under Hide SGT: shown! (because it is OCR verifier, not SGT)
    assert _passes_source_filter(raw_row, "Hide SGT", is_grouped=False) is True
    # Under SGT Only: hidden
    assert _passes_source_filter(raw_row, "SGT Only", is_grouped=False) is False


def test_source_filter_preserves_vsdc247_verify_grouped():
    """Grouped container with VSDC247_verify must be preserved under 'Hide SGT'."""
    grouped_row = {
        "client_name": "Test Client",
        "capture_method": "VSDC247_verify",
        "filing_history": [
            {"capture_method": "VSDC247_verify", "arn": "AA0000000000000"}
        ]
    }
    assert _passes_source_filter(grouped_row, "All Sources", is_grouped=True) is True
    assert _passes_source_filter(grouped_row, "Hide SGT", is_grouped=True) is True
    assert _passes_source_filter(grouped_row, "SGT Only", is_grouped=True) is False


def test_source_filter_sgt_row():
    """Normal SGT row passes or fails according to SGT filter."""
    sgt_row = {
        "id": 2,
        "capture_method": "SGT_live",
        "dataset_key": "GST:AA1111111111111",
        "arn_number": "AA1111111111111",
    }
    assert _passes_source_filter(sgt_row, "All Sources", is_grouped=False) is True
    assert _passes_source_filter(sgt_row, "Hide SGT", is_grouped=False) is False
    assert _passes_source_filter(sgt_row, "SGT Only", is_grouped=False) is True


def test_tracker_dump_raw_highlight(qapp, monkeypatch):
    """TrackerDumpWindow highlights VSDC247_verify raw rows with purple tint and tooltip."""
    class DummyDB:
        def get_all_clients(self): return []
        def get_client_services(self, cid): return []
        def get_client_services_for_client(self, cid): return []
        def get_all_client_services(self): return []
        def get_srpf_containers(self, **kw): return []
        def get_tracker_dumps(self, **kw): return []

    win = TrackerDumpWindow(db=DummyDB(), defer_first_load=True)
    records = [
        {
            "id": 101,
            "client_id": None,
            "is_unassigned": True,
            "client_name": "Pending_AA2703260123456",
            "pan": "",
            "service_name": "GST Portal (GSTR-3B)",
            "portal": "GST Portal",
            "period_label": "March 2026",
            "status": "Submitted",
            "arn_number": "AA2703260123456",
            "capture_method": "VSDC247_verify",
            "created_at": "2026-03-20T10:00:00Z",
            "dataset_key": "V247:GST:AA2703260123456",
        },
        {
            "id": 102,
            "client_id": 1,
            "is_unassigned": False,
            "client_name": "Normal Client",
            "pan": "ABCDE1234F",
            "service_name": "GST Portal (GSTR-1)",
            "portal": "GST Portal",
            "period_label": "March 2026",
            "status": "Filed",
            "arn_number": "AA2703260999999",
            "capture_method": "SGT_live",
            "created_at": "2026-03-20T10:05:00Z",
            "dataset_key": "GST:AA2703260999999",
        }
    ]

    win._populate_raw_table(records)

    # Row 0: VSDC247_verify row
    client_item = win.table.item(0, win.COL_CLIENT)
    filing_item = win.table.item(0, win.COL_FILING)
    assert client_item is not None
    assert filing_item is not None

    # Check background tint is set to purple tint (#241633)
    bg_color = client_item.background().color().name().lower()
    assert bg_color == "#241633"
    assert "Seen only by OCR — SGT missed it" in client_item.toolTip()

    # Row 1: SGT row
    sgt_client_item = win.table.item(1, win.COL_CLIENT)
    assert sgt_client_item is not None
    assert "Seen only by OCR" not in sgt_client_item.toolTip()
    # Should not have the verify purple background
    assert sgt_client_item.background().color().name().lower() != "#241633"


def test_tracker_dump_grouped_highlight(qapp):
    """TrackerDumpWindow highlights VSDC247_verify in grouped container view."""
    class DummyDB:
        def get_all_clients(self): return []
        def get_client_services(self, cid): return []
        def get_client_services_for_client(self, cid): return []
        def get_all_client_services(self): return []
        def get_srpf_containers(self, **kw): return []
        def get_tracker_dumps(self, **kw): return []

    win = TrackerDumpWindow(db=DummyDB(), defer_first_load=True)
    records = [
        {
            "identity_key": "GST:AA2703260123456",
            "client_name": "Pending_AA2703260123456",
            "pan": "",
            "portal": "GST Portal",
            "latest_period": "March 2026",
            "period_summary": "1 Capture",
            "latest_arn": "AA2703260123456",
            "capture_method": "VSDC247_verify",
            "filing_history": [
                {"capture_method": "VSDC247_verify", "arn": "AA2703260123456"}
            ],
            "last_updated": "2026-03-20T10:00:00Z",
            "total_captures": 1,
            "status": "Submitted",
        }
    ]

    win._populate_grouped_table(records)

    client_item = win.table.item(0, win.COL_CLIENT)
    assert client_item is not None
    assert client_item.background().color().name().lower() == "#241633"
    assert "Seen only by OCR — SGT missed it" in client_item.toolTip()
