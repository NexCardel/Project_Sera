"""Acceptance test for WP P1-3: parsers load the key through sera_keys.

All files live under pytest's tmp_path; the real data folder is never touched.
Keys and passwords here are invented test values.
"""

import importlib
import sys

import pytest

import sera_keys

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")

OFFICE_ID = "3f2a9c1e-0000-4000-8000-00000000abcd"
PASSWORD = "correct horse battery"


def _make_office_key(app_dir):
    dek = sera_keys.new_dek()
    kid = sera_keys.key_id(dek)
    sera_keys.store_dek(app_dir, dek, PASSWORD, OFFICE_ID)
    sera_keys.save_office(
        app_dir,
        sera_keys.OfficeInfo(office_id=OFFICE_ID, office_name="Test Office", key_id=kid),
    )
    return dek


@windows_only
def test_dom_parser_get_db_hex_key_uses_office_dek(tmp_path, monkeypatch):
    data_dir = tmp_path / "AmanAssociates_Sera"
    dek = _make_office_key(data_dir)
    monkeypatch.setattr("os.path.expanduser", lambda p: str(tmp_path) if p == "~" else p)

    import DOM_Parser_1.dom_parser as dom_parser
    importlib.reload(dom_parser)

    assert dom_parser.get_db_hex_key() == sera_keys.dek_hex(dek)


@windows_only
def test_sdc_parser_get_db_hex_key_uses_office_dek(tmp_path, monkeypatch):
    data_dir = tmp_path / "AmanAssociates_Sera"
    dek = _make_office_key(data_dir)
    monkeypatch.setattr("os.path.expanduser", lambda p: str(tmp_path) if p == "~" else p)

    import SDC_Parser.sdc_parser as sdc_parser
    importlib.reload(sdc_parser)

    assert sdc_parser.get_db_hex_key() == sera_keys.dek_hex(dek)
