"""tests/test_drs.py - Comprehensive tests for Deadline Reminder System (DRS)."""
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock

from core.drs import config, engine, export


class DummyDB:
    def __init__(self):
        self.app_dir = Path(__file__).parent / "tmp_drs"
        self._settings = {}
        self._services = [
            {"id": 1, "name": "GST", "login_page_link": "https://services.gst.gov.in"},
            {"id": 2, "name": "Income Tax", "login_page_link": "https://eportal.incometax.gov.in"},
        ]
        self._mcl = [
            {"id": 1, "label": "Client Name", "field_type": "text", "is_identity": 1},
            {"id": 2, "label": "PAN", "field_type": "alphanumeric", "is_identity": 1},
            {"id": 3, "label": "GSTIN", "field_type": "alphanumeric", "is_identity": 0},
            {"id": 4, "label": "Password", "field_type": "password", "is_identity": 0},
        ]
        self._clients = [
            {
                "id": 101,
                "values": {1: "Alpha Traders", 2: "ABCDE1234F", 3: "27ABCDE1234F1Z5"},
                "service_ids": [1],  # GST only
            },
            {
                "id": 102,
                "values": {1: "Beta Services", 2: "BCDEF2345G"},
                "service_ids": [2],  # Income Tax only
            },
            {
                "id": 103,
                "values": {1: "Gamma Industries", 2: "CDEFG3456H", 3: "27CDEFG3456H1Z2"},
                "service_ids": [1, 2],  # Both
            },
        ]
        self._containers = [
            {
                "identity_key": "ABCDE1234F",
                "client_id": 101,
                "pan": "ABCDE1234F",
                "gstin": "27ABCDE1234F1Z5",
                "company_name": "Alpha Traders",
                "history": [
                    {
                        "portal": "GST (GSTR-3B)",
                        "period_label": "September (FY 2026-27)",
                        "status": "Filed",
                        "arn": "AA2709260000001",
                        "created_at": "2026-10-20T10:00:00Z",
                        "captured_by": "PC-01",
                    }
                ],
            }
        ]

    def get_setting(self, key, default=None):
        return self._settings.get(key, default)

    def set_setting(self, key, val):
        self._settings[key] = val

    def get_services(self):
        return self._services

    def get_service_for_portal(self, portal_name):
        clean = str(portal_name).lower()
        if "gst" in clean:
            return self._services[0]
        if "income" in clean or "itr" in clean:
            return self._services[1]
        return None

    def get_mcl_columns(self):
        return self._mcl

    def search_clients(self, query="", include_archived=False):
        return self._clients

    def get_srpf_containers(self, limit=1_000_000, slim=True):
        return self._containers


def test_drs_default_columns():
    db = DummyDB()
    cols = config.default_drs_columns(db)
    col_labels = [c["label"] for c in cols]
    
    # Password column should NOT be included
    assert "Password" not in col_labels

    # PAN, GSTIN, Name should be present and enabled
    assert "PAN" in col_labels
    assert "Client Name" in col_labels
    assert "Latest period" in col_labels
    assert "Status" in col_labels
    assert "ARN" in col_labels

    # Check types
    types = {c["label"]: c["type"] for c in cols}
    assert types["Client Name"] == "mcl"
    assert types["Latest period"] == "fixed"
    assert types["Status"] == "fixed"
    assert types["ARN"] == "tracker"


def test_drs_config_load_save():
    db = DummyDB()
    cfg = config.load_drs_config(db)
    assert len(cfg["columns"]) > 0

    # Modify an attribute and save
    cfg["columns"][0]["enabled"] = False
    config.save_drs_config(db, cfg)

    reloaded = config.load_drs_config(db)
    assert reloaded["columns"][0]["enabled"] is False


def test_form_detection_and_service_linking():
    db = DummyDB()
    detected = engine.detect_forms_and_services(db)
    assert len(detected) >= 1

    form_names = [f["form"] for f in detected]
    assert "GSTR-3B" in form_names

    gstr3b = next(f for f in detected if f["form"] == "GSTR-3B")
    assert gstr3b["portal"] == "GST"
    assert gstr3b["service"]["name"] == "GST"
    assert gstr3b["is_gst"] is True


def test_build_drs_dataset():
    db = DummyDB()
    active_cols = config.get_active_columns(db)
    dataset = engine.build_drs_dataset(db, active_cols)

    assert "GSTR-3B" in dataset
    rows = dataset["GSTR-3B"]

    # Only clients with GST service (Alpha Traders id:101, Gamma Industries id:103)
    # Beta Services (id:102, Income Tax only) must NOT appear on GSTR-3B!
    client_names = [r.get("Client Name") for r in rows]
    assert "Alpha Traders" in client_names
    assert "Gamma Industries" in client_names
    assert "Beta Services" not in client_names

    # Alpha Traders has a capture:
    alpha_row = next(r for r in rows if r.get("Client Name") == "Alpha Traders")
    assert alpha_row["Latest period"] == "September (FY 2026-27)"
    assert alpha_row["Status"] == "Submitted & Verified"
    assert alpha_row["ARN"] == "AA2709260000001"

    # Gamma Industries has NO capture yet:
    gamma_row = next(r for r in rows if r.get("Client Name") == "Gamma Industries")
    assert gamma_row["Latest period"] == "no captures yet"
    assert gamma_row["Status"] == "no captures yet"
    assert gamma_row["ARN"] == ""


def test_export_and_workbook_creation(tmp_path):
    db = DummyDB()
    db.app_dir = tmp_path

    # Export DRS
    xlsx = export.export_drs(db, app_dir=tmp_path, today=date(2026, 10, 22), force_rebuild=True)
    assert xlsx.exists()
    assert xlsx.name == "DRS_2026-10.xlsx"

    # Check CSVs exist
    data_csv, over_csv = export.drs_csv_paths(xlsx)
    assert data_csv.exists()
    assert over_csv.exists()

    # Read data CSV
    csv_text = data_csv.read_text(encoding="utf-8")
    assert "Alpha Traders" in csv_text
    assert "Gamma Industries" in csv_text
    assert "no captures yet" in csv_text


def test_scheduled_export_debounce():
    db = DummyDB()
    t1 = export.schedule_drs_export(db, delay=0.1)
    assert t1 is not None
    # Subsequent immediate call replaces the timer (debounce)
    t2 = export.schedule_drs_export(db, delay=0.1)
    assert t2 is not None
    assert t1 is not t2
