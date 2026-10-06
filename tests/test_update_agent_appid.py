import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_agent_looks_up_the_uninstall_key_inno_writes():
    """The agent finds the installed version under ...\\Uninstall\\<AppId>_is1. Inno turns "{{" into
    "{" and leaves everything else as written, so AppId={{GUID}} gives the key "{GUID}}_is1".
    When the agent looked for "{GUID}_is1" instead, no installed PC ever updated."""
    iss = (ROOT / "build_tools" / "installer_setup.iss").read_text(encoding="utf-8")
    app_id = re.search(r"^AppId=(.+)$", iss, re.M).group(1).strip()
    key = app_id.replace("{{", "{") + "_is1"

    agent = (ROOT / "build_tools" / "updater" / "sera_update_agent.ps1").read_text(encoding="utf-8")
    ids = re.search(r"^\$AppIds = @\((.+)\)$", agent, re.M).group(1)
    assert f'"{key}"' in ids, f"agent does not look up {key}"
