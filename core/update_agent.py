"""
App side of the silent updater (build_tools/updater/sera_update_agent.ps1).

The agent runs as SYSTEM from the "Amas Sera\\Updater" scheduled task, downloads and verifies a
new installer, and writes %ProgramData%\\AmasSera\\updater\\pending.json. It installs only while
Amas_Sera.exe is not running. This module lets the app take part:

- is_available(): installed build with the agent present. Then the old in-app updater (which
  needs a UAC prompt) stays off.
- pending_version(): the staged version, if newer than this build.
- should_close_now(): the app closes itself for the update when nobody is using the PC (no input
  for 3 minutes), or after the update has waited 8 hours, as soon as input pauses for 30 s.
  At most twice a day per version, so a failing install cannot keep closing the app.
- start_relauncher(): hidden helper in the user's session that starts the agent task, waits for
  the install, then reopens the app (the old version too, if the install failed).
- install_in_progress(): checked first thing at start-up. The app must not hold its own files
  open while the installer replaces them, so it exits and leaves a helper to reopen it.
"""

import ctypes
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

STATE_DIR = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "AmasSera" / "updater"
TASK_NAME = r"\Amas Sera\Updater"
IDLE_SECONDS = 180
LONG_WAIT_IDLE_SECONDS = 30
LONG_WAIT_HOURS = 8
MAX_CLOSES_PER_DAY = 2
INSTALL_FLAG_MAX_AGE = 20 * 60


def is_available() -> bool:
    if not getattr(sys, "frozen", False):
        return False
    return (Path(sys.executable).parent / "updater" / "sera_update_agent.ps1").exists()


def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def pending_update() -> Optional[dict]:
    """pending.json if it names a version newer than this build, else None."""
    import version
    data = _read_json(STATE_DIR / "pending.json")
    if not data or not version.is_update_available(str(data.get("version", "")), version.APP_VERSION):
        return None
    return data


def pending_version() -> Optional[str]:
    data = pending_update()
    return str(data["version"]) if data else None


def _staged_hours(data: dict) -> float:
    try:
        staged = datetime.fromisoformat(str(data.get("staged_at", "")))
    except ValueError:
        return 0.0
    if staged.tzinfo is None:
        staged = staged.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return max(0.0, (datetime.now(timezone.utc) - staged).total_seconds() / 3600)


def seconds_since_input() -> float:
    """Seconds since the last keyboard/mouse input in this session (Windows only)."""
    if sys.platform != "win32":
        return 0.0

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    ticks = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
    return ((ticks - info.dwTime) & 0xFFFFFFFF) / 1000.0


def _closes_path(app_dir: Path) -> Path:
    return app_dir / "update_closes.json"


def _closes_today(app_dir: Path, ver: str) -> int:
    data = _read_json(_closes_path(app_dir)) or {}
    if data.get("version") == ver and data.get("date") == date.today().isoformat():
        return int(data.get("count", 0))
    return 0


def record_close(app_dir: Path, ver: str) -> None:
    count = _closes_today(app_dir, ver) + 1
    try:
        _closes_path(app_dir).write_text(
            json.dumps({"version": ver, "date": date.today().isoformat(), "count": count}), encoding="utf-8")
    except OSError:
        pass


def should_close_now(app_dir: Path, captures_busy: bool) -> Optional[str]:
    """The pending version if the app should close itself for it now, else None."""
    data = pending_update()
    if not data or captures_busy:
        return None
    ver = str(data["version"])
    if _closes_today(app_dir, ver) >= MAX_CLOSES_PER_DAY:
        return None
    idle_needed = LONG_WAIT_IDLE_SECONDS if _staged_hours(data) >= LONG_WAIT_HOURS else IDLE_SECONDS
    return ver if seconds_since_input() >= idle_needed else None


# Runs in the user's session: start the agent, wait for the install to finish, reopen the app.
# Waits while pending.json or installing.flag exists; gives up after 20 minutes, or after
# 3 minutes if the install never started. Reopens only if the app is not already running here.
_RELAUNCHER = r"""
$ErrorActionPreference = 'SilentlyContinue'
$state = '__STATE__'
$exe = '__EXE__'
if ('__TRIGGER__' -eq '1') { & schtasks.exe /Run /TN '__TASK__' | Out-Null }
$start = Get-Date
$seenInstall = $false
while (((Get-Date) - $start).TotalMinutes -lt 20) {
    Start-Sleep -Seconds 5
    $installing = Test-Path (Join-Path $state 'installing.flag')
    if ($installing) { $seenInstall = $true }
    $pending = Test-Path (Join-Path $state 'pending.json')
    if (-not $installing -and -not $pending) { break }
    if (-not $seenInstall -and ((Get-Date) - $start).TotalMinutes -ge 3) { break }
}
$mine = (Get-Process -Id $PID).SessionId
if (-not (Get-Process -Name 'Amas_Sera' | Where-Object { $_.SessionId -eq $mine })) {
    Start-Process -FilePath $exe
}
"""


def start_relauncher(exe: Optional[str] = None, trigger_agent: bool = True) -> None:
    import base64
    exe = exe or sys.executable
    script = (_RELAUNCHER.replace("__TRIGGER__", "1" if trigger_agent else "0").replace("__STATE__", str(STATE_DIR).replace("'", "''"))
              .replace("__EXE__", str(exe).replace("'", "''"))
              .replace("__TASK__", TASK_NAME))
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP | 0x01000000  # BREAKAWAY_FROM_JOB
    args = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-EncodedCommand", encoded]
    try:
        subprocess.Popen(args, creationflags=flags, close_fds=True)
    except OSError:
        # Not allowed to leave the job: start it inside it (still outlives this process).
        subprocess.Popen(args, creationflags=flags & ~0x01000000, close_fds=True)


def install_in_progress() -> bool:
    """An update is replacing the app's files right now (installing.flag, younger than 20 min)."""
    try:
        return time.time() - (STATE_DIR / "installing.flag").stat().st_mtime < INSTALL_FLAG_MAX_AGE
    except OSError:
        return False
