"""SDIS: tools/sdis_regress.py runs end to end over the two fictional clients and prints counts only."""

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).resolve().parent / "class_diff_align"
sys.path.insert(0, str(ROOT / "tools"))

import sdis_regress  # noqa: E402


def test_runner_on_fictional_clients(tmp_path, capsys):
    shutil.copy(FIX / "client_A.json", tmp_path / "key_probe_20260101_000000.json")
    shutil.copy(FIX / "client_B.json", tmp_path / "key_probe_20260101_000100.json")
    assert sdis_regress.main(["--captures", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "orders agree: yes" in out
    assert "double count 0" in out


def test_runner_without_captures(tmp_path, capsys):
    assert sdis_regress.main(["--captures", str(tmp_path / "missing")]) == 0
    assert "no captures" in capsys.readouterr().out
