"""
core/sdis/arn_autofix.py - the unattended pass. Temp database and the injector's fictional pages only.
"""
import os
import sys
from datetime import datetime, timedelta

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(__file__))

from test_mr_fixer import GSTINS, NAMES, _interleaved, add_row, make_fx, table  # noqa: E402
from core.sdis import arn_autofix as auto  # noqa: E402
from core.sdis import arn_fixer as fx_  # noqa: E402


@pytest.fixture
def fx(tmp_path):
    return make_fx(tmp_path)


def once(fx, roots=None, **kw):
    lines = kw.pop("lines", [])
    return auto.run_once(fx.w.db, fx.w.app, log=lines.append, roots=roots or fx.roots, **kw)


def test_it_fixes_the_sure_rows_and_a_second_pass_finds_nothing(fx):
    c = once(fx)
    assert (c["rows"], c["sure"], c["fixed"], c["left"]) == (3, 3, 3, 0)
    assert fx_.find_targets(fx.w.db, fx.arn_re) == []
    assert once(fx)["rows"] == 0
    assert len(list((fx.w.app / "mr_fixer").glob("auto_report_*.csv"))) == 1


def test_its_undo_file_puts_everything_back(fx):
    before = table(fx.w.db)
    once(fx)
    report = next((fx.w.app / "mr_fixer").glob("auto_report_*.csv"))
    fx_.undo_report(fx.w.app, lambda: fx.w.db, report, log=lambda m: None)
    assert table(fx.w.db) == before


def test_a_row_younger_than_the_minimum_age_is_left(fx):
    young = add_row(fx, "AA191026999940T", "host-one", (datetime.now() - timedelta(minutes=10)).isoformat(timespec="seconds"))
    c = once(fx)
    assert c["rows"] == 3 and c["fixed"] == 3
    assert young in {t.id for t in fx_.find_targets(fx.w.db, fx.arn_re)}


def test_rows_that_are_not_sure_are_never_touched_and_no_report_is_written(tmp_path):
    f, rid = _interleaved(tmp_path)
    before = table(f.w.db)
    c = auto.run_once(f.w.db, f.w.app, log=lambda m: None, roots=[tmp_path / "c"])
    assert (c["rows"], c["sure"], c["fixed"], c["left"]) == (1, 0, 0, 1)
    assert table(f.w.db) == before
    assert not (f.w.app / "mr_fixer").exists() or not list((f.w.app / "mr_fixer").glob("*"))


def test_the_disabled_flag_switches_it_off(fx):
    (fx.w.app / "mr_fixer").mkdir(parents=True)
    (fx.w.app / "mr_fixer" / auto.DISABLE_FLAG).write_text("", encoding="utf-8")
    before = table(fx.w.db)
    assert once(fx)["fixed"] == 0 and table(fx.w.db) == before


def test_no_names_or_gstins_in_the_log(fx):
    lines = []
    once(fx, lines=lines)
    text = "\n".join(lines)
    assert text and not any(s in text for s in NAMES + GSTINS)


def test_background_runs_only_on_the_admin_pc(fx, monkeypatch):
    import sync_admin
    monkeypatch.setattr(sync_admin, "is_admin_pc", lambda *a, **k: False)
    assert auto.start_background(fx.w.db, fx.w.app, delay_s=0, log=lambda m: None) is None
    assert len(fx_.find_targets(fx.w.db, fx.arn_re)) == 3


def test_background_reports_what_it_fixed(fx, monkeypatch):
    import sync_admin
    monkeypatch.setattr(sync_admin, "is_admin_pc", lambda *a, **k: True)
    monkeypatch.setattr(fx_, "default_roots", lambda app_dir: fx.roots)
    events, done = [], []
    t = auto.start_background(fx.w.db, fx.w.app, on_event=lambda *a: events.append(a), on_done=done.append,
                              delay_s=0, log=lambda m: None)
    t.join(60)
    assert done == [3] and events and events[0][0] == "SDIS" and "3 of 3" in events[0][2]
    assert not any(s in str(events) for s in NAMES + GSTINS)


def test_a_failure_in_the_thread_does_not_escape(fx, monkeypatch):
    import sync_admin
    monkeypatch.setattr(sync_admin, "is_admin_pc", lambda *a, **k: True)
    monkeypatch.setattr(auto, "run_once", lambda *a, **k: 1 / 0)
    lines = []
    t = auto.start_background(fx.w.db, fx.w.app, delay_s=0, log=lines.append)
    t.join(10)
    assert any("failed" in l for l in lines)
