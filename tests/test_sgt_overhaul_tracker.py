"""Tests for tools/sgt_overhaul.py - the SGT overhaul tracker and dispatcher (no real CLI)."""

import datetime as dt
import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import sgt_overhaul as so  # noqa: E402

DOCS = Path(__file__).resolve().parent.parent / "docs"


@pytest.fixture
def docs(tmp_path, monkeypatch):
    for name in ("sgt-overhaul-plan.json", "sgt-overhaul-runner.md"):
        shutil.copy(DOCS / name, tmp_path / name)
    plan = json.loads((tmp_path / "sgt-overhaul-plan.json").read_text(encoding="utf-8"))
    plan["deadline"] = "2099-01-01T00:00:00+05:30"
    (tmp_path / "sgt-overhaul-plan.json").write_text(json.dumps(plan), encoding="utf-8")
    monkeypatch.setattr(so, "DOCS", tmp_path)
    monkeypatch.setattr(so, "REPO", tmp_path)
    monkeypatch.setattr(so, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(so, "LOCK", tmp_path / ".lock")
    return tmp_path


def test_plan_is_consistent():
    plan = json.loads((DOCS / "sgt-overhaul-plan.json").read_text(encoding="utf-8"))
    ids = [w["wp"] for w in plan["wps"]]
    assert len(ids) == len(set(ids))
    seen = set()
    for w in plan["wps"]:
        assert set(w["deps"]) <= seen, f"{w['wp']} depends on a later WP"
        assert w["model"] in plan["models"] and w["kind"] in ("auto", "desktop", "review")
        seen.add(w["wp"])


def test_next_respects_dependencies(docs):
    t = so.Tracker(docs)
    assert t.next_ready() == "W0-1"
    t.set("W0-1", Status="In progress")
    assert t.next_ready() is None
    t.finish("W0-1", "abc1234", "")
    assert t.next_ready() == "W0-2"


def test_finish_needs_a_commit(docs):
    t = so.Tracker(docs)
    with pytest.raises(so.Refused):
        t.finish("W0-1", "", "")
    with pytest.raises(so.Refused):
        t.finish("W0-2", "abc1234", "")          # W0-1 not Done yet


def test_prompt_has_the_wp_filled_in(docs):
    p = so.build_prompt(so.Tracker(docs), "W0-1")
    assert "W0-1" in p and "{FOCUS}" not in p and "sgt_corpus" in p


@pytest.mark.parametrize("text,expect", [
    ("Claude AI usage limit reached|4102444800", "epoch"),
    ("5-hour limit reached ∙ resets 3pm", 15),
    ("You've hit your usage limit. It resets at 11:30 pm", 23),
    ("All good, WP finished", None),
])
def test_limit_reset(text, expect):
    ref = dt.datetime(2026, 9, 27, 14, 0).astimezone()
    got = so.limit_reset(text, ref)
    if expect is None:
        assert got is None
    elif expect == "epoch":
        assert got.year == 2100
    else:
        assert got.hour == expect and got > ref


def _fake_cli(tmp_path, body):
    script = tmp_path / "fake_claude.py"
    script.write_text(body, encoding="utf-8")
    return json.dumps([sys.executable, str(script)])


def test_run_once_records_done(docs, monkeypatch):
    # The fake worker marks its WP Done the way a real one would, then prints the CLI's JSON.
    body = (f"import sys, json; sys.path.insert(0, {str(Path(so.__file__).parent)!r}); import sgt_overhaul as so\n"
            f"from pathlib import Path; sys.stdin.read()\n"
            f"so.Tracker(Path({str(docs)!r})).finish('W0-1', 'abc1234', 'fake')\n"
            "print(json.dumps({'is_error': False, 'result': 'done', 'usage': {'output_tokens': 7}, 'num_turns': 3}))\n")
    monkeypatch.setenv("SGT_CLAUDE", _fake_cli(docs, body))
    assert so.run_loop(once=True, log=lambda *_: None) == 0
    t = so.Tracker(docs)
    assert t.status["W0-1"]["Status"] == "Done"
    runs = so._read_csv(t.p_runs, so.RUN_FIELDS)
    assert runs[-1]["Outcome"] == "Done" and runs[-1]["Output tokens"] == "7"
    assert (docs / "sgt-overhaul-report.md").exists()


def test_usage_limit_is_retried_without_spending_an_attempt(docs, monkeypatch):
    body = ("import sys, json; sys.stdin.read()\n"
            "print(json.dumps({'is_error': True, 'result': 'Claude AI usage limit reached|4102444800'}))\n")
    monkeypatch.setenv("SGT_CLAUDE", _fake_cli(docs, body))
    so.run_loop(once=True, log=lambda *_: None)
    s = so.Tracker(docs).status["W0-1"]
    assert s["Status"] == "Retry" and s["Attempts"] == "0"


def test_no_finish_twice_blocks(docs, monkeypatch):
    body = "import sys, json; sys.stdin.read(); print(json.dumps({'is_error': False, 'result': 'forgot'}))\n"
    monkeypatch.setenv("SGT_CLAUDE", _fake_cli(docs, body))
    so.run_loop(once=True, log=lambda *_: None)
    assert so.Tracker(docs).status["W0-1"]["Status"] == "Retry"
    so.run_loop(once=True, log=lambda *_: None)
    assert so.Tracker(docs).status["W0-1"]["Status"] == "Blocked"
