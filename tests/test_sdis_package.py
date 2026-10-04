"""
SDIS engine package (core/sdis): the pre-dev files keys, align, link_map, memory and tables are
module ALIASES of the core ones, so a setting made on the pre-dev name (keys.VIEW) reaches the
real module.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "pre_dev" / "class_diff"))

import keys  # noqa: E402
import tables  # noqa: E402

import core.sdis.keys  # noqa: E402
import core.sdis.tables  # noqa: E402


def test_pre_dev_keys_is_the_core_module():
    assert keys is core.sdis.keys


def test_setting_view_on_the_alias_reaches_the_core_module():
    old = keys.VIEW
    try:
        keys.VIEW = "sgt"
        assert core.sdis.keys.VIEW == "sgt"
    finally:
        keys.VIEW = old


def test_pre_dev_tables_is_the_core_module():
    assert tables is core.sdis.tables


def test_compare_still_exposes_the_moved_names():
    import compare
    from core.sdis import identity, labels
    assert compare.value_type is labels.value_type
    assert compare.is_period is labels.is_period
    assert compare.composites is labels.composites
    assert compare.client_ids is identity.client_ids
    assert compare.keys is core.sdis.keys


def test_pre_dev_screens_is_the_core_module():
    import screens
    import core.sdis.screens
    assert screens is core.sdis.screens


def test_pre_dev_identity_is_the_core_module():
    import identity
    import core.sdis.identity
    assert identity is core.sdis.identity


def test_link_map_reexports_identity_functions():
    from core.sdis import identity, link_map
    assert link_map.group_clients is identity.group_clients
    assert link_map._by_time is identity._by_time



def test_pre_dev_links_is_the_core_module():
    import links
    import core.sdis.links
    assert links is core.sdis.links


def test_core_sdis_imports_without_pyside6(tmp_path):
    env = dict(os.environ, SDIS_DATA_DIR=str(tmp_path))
    code = ("import sys, core.sdis.paths, core.sdis.keys, core.sdis.align, core.sdis.labels, "
            "core.sdis.identity, core.sdis.history, core.sdis.screens, core.sdis.links, core.sdis.link_map, core.sdis.memory, core.sdis.tables; "
            "sys.exit(1 if 'PySide6' in sys.modules else 0)")
    r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-500:]

