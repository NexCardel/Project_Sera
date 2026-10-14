"""
tools/sgt_never_miss_suite.py
-----------------------------
Runs the full test suite the CLAUDE.md way (the live T/F/S plugin) for an unattended worker, with the
per-test lines going to a log file instead of the worker's context. Prints the log path, the command
to watch it, and at the end only the summary and the failed tests.

  venv/Scripts/python.exe tools/sgt_never_miss_suite.py            # the whole of tests/
  venv/Scripts/python.exe tools/sgt_never_miss_suite.py tests/test_sgt_replay.py tests/test_vsdc_diag.py
"""

import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOG_DIR = REPO / "logs" / "sgt-never-miss"


def main(argv) -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = LOG_DIR / f"suite-{dt.datetime.now():%Y%m%d-%H%M%S}.log"
    # --basetemp: on this PC pytest's own temp-folder clean-up fails with a PermissionError otherwise.
    cmd = [sys.executable, "-m", "pytest", "-p", "live_tf", "-p", "no:terminal", "-p", "no:cacheprovider",
           f"--basetemp={LOG_DIR / 'pytest-tmp'}"] + (argv or ["tests"])
    env = {**os.environ, "PYTHONPATH": str(REPO / "tools") + os.pathsep + os.environ.get("PYTHONPATH", "")}
    print(f"log: {log}")
    print(f'watch: Get-Content -Path "{log}" -Wait -Tail 20')
    sys.stdout.flush()
    with open(log, "w", encoding="utf-8") as f:
        code = subprocess.run(cmd, cwd=REPO, env=env, stdout=f, stderr=subprocess.STDOUT).returncode
    text = log.read_text(encoding="utf-8", errors="replace")
    i = text.rfind("\n--- T=")
    print(text[i + 1:] if i >= 0 else text[-3000:])
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
