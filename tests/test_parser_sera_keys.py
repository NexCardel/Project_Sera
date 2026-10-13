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
