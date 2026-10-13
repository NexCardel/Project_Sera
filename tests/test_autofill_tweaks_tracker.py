"""The Autofill tweaks project reuses the SGT overhaul dispatcher (tools/sgt_overhaul.py) through
use_project(); these tests keep its plan valid and prove the switch touches only its own files."""
import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import sgt_overhaul as so  # noqa: E402

FOLDER = Path(__file__).resolve().parent.parent / "docs" / "autofill_tweaks"
PREFIX = "autofill-tweaks"


@pytest.fixture
def project(tmp_path, monkeypatch):
    for name in ("DOCS", "LOG_DIR", "LOCK", "PREFIX", "TITLE", "CLI", "WAIT_FILE"):
        monkeypatch.setattr(so, name, getattr(so, name))   # restored after the test
    for name in (f"{PREFIX}-plan.json", f"{PREFIX}-runner.md"):
        shutil.copy(FOLDER / name, tmp_path / name)
    so.use_project(tmp_path, PREFIX, "Autofill tweaks", "tools/autofill_tweaks.py")
    monkeypatch.setattr(so, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(so, "LOCK", tmp_path / ".lock")
    return tmp_path


def test_plan_is_consistent():
    plan = json.loads((FOLDER / f"{PREFIX}-plan.json").read_text(encoding="utf-8"))
    ids = [w["wp"] for w in plan["wps"]]
    assert len(ids) == len(set(ids))
    seen = set()
    for w in plan["wps"]:                       # listed in an order the dispatcher can run
        assert set(w["deps"]) <= seen, w["wp"]
        assert w["model"] in plan["tiers"]
        seen.add(w["wp"])
    assert "Bash(node:*)" in plan["cli"]["allowed_tools"]
    assert "Bash(git push:*)" in plan["cli"]["disallowed_tools"]


def test_use_project_writes_only_its_own_files(project):
    t = so.Tracker()
    assert t.next_ready() == "W0-1"
    assert (project / f"{PREFIX}-status.csv").exists()
    assert not list(project.glob("sgt-overhaul-*.csv"))
    t.report()
    assert (project / f"{PREFIX}-report.md").read_text(encoding="utf-8").startswith("# Autofill tweaks")


def test_prompt_uses_this_projects_runner(project):
    t = so.Tracker()
    text = so.build_prompt(t, "W2-2")
    assert "W2-2" in text and "tools/autofill_tweaks.py" in text and "{FOCUS}" not in text


def test_sgt_defaults_are_unchanged():
    assert so.PREFIX == "sgt-overhaul" and so.DOCS == so.REPO / "docs"
