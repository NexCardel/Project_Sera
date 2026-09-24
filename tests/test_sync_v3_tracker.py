"""Tests for tools/sync_v3_tracker.py and tools/build_sync_v3_tracker.py (temp folders only)."""
import csv
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import build_sync_v3_tracker as builder  # noqa: E402
import sync_v3_tracker as tracker  # noqa: E402


@pytest.fixture
def docs(tmp_path):
    shutil.copy(REPO / "docs" / "sera-sync-v3-blueprint.md", tmp_path / "sera-sync-v3-blueprint.md")
    assert builder.main(["--docs", str(tmp_path), "--no-excel"]) == 0
    return tmp_path


def run(docs, *args):
    return tracker.main(["--docs", str(docs), *args])


def status(docs, wp):
    with open(docs / "sera-sync-v3-status.csv", encoding="utf-8", newline="") as f:
        return next(r for r in csv.DictReader(f) if r["WP"] == wp)


def test_build_creates_files_with_everything_not_started(docs):
    for name in ("sera-sync-v3-plan.json", "sera-sync-v3-status.csv", "sera-sync-v3-checks.csv",
                 "sera-sync-v3-agents.xlsx"):
        assert (docs / name).exists()
    assert status(docs, "P0-1")["Status"] == "Not started"


def test_refuses_start_before_dependencies_done(docs, capsys):
    assert run(docs, "set", "P0-2", "--status", "In progress", "--model", "Claude Haiku 4.5") == 2
    assert "P0-1" in capsys.readouterr().err
    assert status(docs, "P0-2")["Status"] == "Not started"


def test_refuses_model_not_allowed_for_tier(docs):
    assert run(docs, "set", "P0-3", "--model", "Gemini 3.5 Flash-Lite") == 2   # T2
    assert run(docs, "set", "P1-1", "--model", "Gemini 3.8 Flash") == 2        # T3
    assert run(docs, "set", "P0-1", "--model", "Some Other Model") == 2


def test_caution_model_forces_review_before_done(docs, capsys):
    assert run(docs, "set", "P0-10", "--status", "In progress", "--model", "Gemini 3.6 Flash") == 0
    assert "needs a Fable 5.1 / Opus 5.5 review" in capsys.readouterr().out
    assert run(docs, "set", "P0-10", "--status", "Done", "--commit", "abc1234") == 2
    assert run(docs, "set", "P0-10", "--reviewed-by", "Claude Sonnet 5") == 2
    assert run(docs, "set", "P0-10", "--status", "Done", "--commit", "abc1234",
               "--reviewed-by", "Claude Opus 5.5") == 0
    assert status(docs, "P0-10")["Status"] == "Done"


def test_done_needs_commit_and_unblocks_dependents(docs):
    assert run(docs, "set", "P0-1", "--status", "In progress", "--model", "Claude Haiku 4.5") == 0
    assert run(docs, "set", "P0-1", "--status", "Done") == 2
    assert run(docs, "set", "P0-1", "--status", "Done", "--commit", "1a2b3c4") == 0
    assert run(docs, "set", "P0-2", "--status", "In progress", "--model", "Claude Haiku 4.5") == 0


def test_required_review_enforced(docs):
    assert run(docs, "set", "P0-3", "--status", "In progress", "--model", "Claude Sonnet 5") == 0
    assert run(docs, "set", "P0-3", "--status", "Done", "--commit", "def5678") == 2
    assert run(docs, "set", "P0-3", "--reviewed-by", "Claude Fable 5.1") == 0
    assert run(docs, "set", "P0-3", "--status", "Done", "--commit", "def5678") == 0


def test_notes_with_commas_quotes_and_newlines_round_trip(docs):
    assert run(docs, "set", "P0-1", "--notes", 'first, "quoted"') == 0
    assert run(docs, "set", "P0-1", "--notes", "second", "--append-notes") == 0
    notes = status(docs, "P0-1")["Notes"]
    assert 'first, "quoted"' in notes and notes.count("\n") == 1 and notes.endswith("second")


def test_phase_check_recorded(docs):
    assert run(docs, "check", "3", "--result", "Pass", "--notes", "spare laptop") == 0
    assert run(docs, "check", "999", "--result", "Pass") == 2
    with open(docs / "sera-sync-v3-checks.csv", encoding="utf-8", newline="") as f:
        row = next(r for r in csv.DictReader(f) if r["Check"] == "3")
    assert row["Result"] == "Pass" and row["Notes"] == "spare laptop" and row["Date"]


def test_rebuild_keeps_progress(docs):
    assert run(docs, "set", "P0-1", "--status", "In progress", "--model", "Claude Haiku 4.5") == 0
    assert builder.main(["--docs", str(docs), "--no-excel"]) == 0
    assert status(docs, "P0-1")["Status"] == "In progress"


def test_stale_lock_is_cleared(docs):
    import os, time
    lock = docs / ".sera-sync-v3-tracker.lock"
    lock.write_text("")
    old = time.time() - 120
    os.utime(lock, (old, old))
    assert run(docs, "set", "P0-1", "--notes", "after crash") == 0
    assert not lock.exists()
