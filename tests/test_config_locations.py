"""
Where an INSTALLED app looks for its local config files.

The program sits in Program Files, which staff cannot write to without admin rights, so the
phone-alert config and an SGT spec override are also looked for in the PC's Sera data folder.
A file next to the program still wins (an admin deliberately put it there).
"""
import sys

import pytest

from core.sgt import sgt_specs
from core.vsdc import vsdc_alerts


@pytest.fixture
def installed(monkeypatch, tmp_path):
    program = tmp_path / "Program Files" / "Amas Sera"
    home = tmp_path / "home"
    program.mkdir(parents=True)
    (home / "AmanAssociates_Sera").mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(program / "Amas_Sera.exe"))
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    monkeypatch.delenv(vsdc_alerts.ALERT_CONFIG_ENV, raising=False)
    monkeypatch.delenv(sgt_specs.FIELDS_PATH_ENV, raising=False)
    return program, home / "AmanAssociates_Sera"


@pytest.mark.parametrize("name, locate", [
    ("vsdc_alert.json", lambda: vsdc_alerts._config_path()),
    ("sgt_fields.json", lambda: sgt_specs.override_path()),
])
def test_data_folder_when_nothing_is_beside_the_program(installed, name, locate):
    program, data = installed
    assert locate() == data / name


@pytest.mark.parametrize("name, locate", [
    ("vsdc_alert.json", lambda: vsdc_alerts._config_path()),
    ("sgt_fields.json", lambda: sgt_specs.override_path()),
])
def test_a_file_beside_the_program_wins(installed, name, locate):
    program, data = installed
    (program / name).write_text("{}", encoding="utf-8")
    (data / name).write_text("{}", encoding="utf-8")
    assert locate() == program / name
