"""
autostart.py
------------
Starts Project Sera when the user logs on to Windows.

Primary method: a per-user Task Scheduler task ("\\Amas Sera\\Startup-<user>") with an
"At log on" trigger. Unlike the Run registry key, a task
  * starts at logon itself - Windows holds Run-key programs back ~10 s after the desktop appears,
  * has a working directory (Run-key programs start in System32),
  * has a process priority class, an "ignore a second start" rule and restart-on-failure.
Fallback: the Run registry key, when this user may not register a task (policy, no rights).
Both launch the app with --startup so main.py can apply its boot-time behaviour
(priority boost, single-instance guard, patient retries).

Everything returns what happened - nothing is swallowed - so Settings can tell the user.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from typing import NamedTuple, Optional
from xml.sax.saxutils import escape

REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "ProjectSera"
STARTUP_ARG = "--startup"
TASK_FOLDER = "Amas Sera"
TASK_PREFIX = "Startup-"
TASK_PRIORITY = 4          # 0-10; 4 = normal process priority (the Task Scheduler default, 7, is below normal)
_CREATE_NO_WINDOW = 0x08000000


class AutostartResult(NamedTuple):
    ok: bool
    method: str     # "task" | "run-key" | "none"
    message: str    # empty when nothing needs saying


# ── identity ──────────────────────────────────────────────────────────────────

def _sam_name(name_format: int = 2) -> str:
    """DOMAIN\\user of the process's own account (NameSamCompatible)."""
    import ctypes
    size = ctypes.c_ulong(0)
    ctypes.windll.secur32.GetUserNameExW(name_format, None, ctypes.byref(size))
    buf = ctypes.create_unicode_buffer(max(size.value, 256))
    size = ctypes.c_ulong(len(buf))
    if ctypes.windll.secur32.GetUserNameExW(name_format, buf, ctypes.byref(size)):
        return buf.value
    return os.environ.get("USERDOMAIN", "") + "\\" + os.environ.get("USERNAME", "")


def process_user() -> str:
    return _sam_name()


def session_user() -> Optional[str]:
    """DOMAIN\\user logged on at this session's desktop (differs from process_user() when the app
    was started "as administrator" with another account's credentials)."""
    import ctypes
    from ctypes import wintypes
    try:
        wts = ctypes.windll.wtsapi32
        out = []
        for info_class in (7, 5):  # WTSDomainName, WTSUserName
            buf = ctypes.c_void_p()
            n = wintypes.DWORD()
            if not wts.WTSQuerySessionInformationW(None, 0xFFFFFFFF, info_class, ctypes.byref(buf), ctypes.byref(n)):
                return None
            out.append(ctypes.wstring_at(buf.value))
            wts.WTSFreeMemory(buf)
        domain, user = out
        return f"{domain}\\{user}" if user else None
    except Exception:
        return None


def user_mismatch() -> Optional[str]:
    """A sentence if this process runs as a different account than the one at the desktop, else None."""
    if sys.platform != "win32":
        return None
    mine, desk = process_user(), session_user()
    if desk and mine and mine.lower() != desk.lower():
        return (f"Project Sera is running as {mine}, but {desk} is the user logged on to this PC. "
                f"A startup entry saved now would belong to {mine}, and Windows would never run it "
                f"when {desk} logs on. Close Sera and start it normally (not 'Run as administrator' "
                f"with another account), then turn this setting on again.")
    return None


# ── what to launch ────────────────────────────────────────────────────────────

def get_main_script_path() -> str:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    app_dir = os.path.abspath(os.path.join(current_dir, "..", ".."))
    return os.path.join(app_dir, "main.py")


def launch_command() -> tuple[str, str, str]:
    """(exe, arguments, working directory) that start the app at logon."""
    if getattr(sys, "frozen", False):
        exe = sys.executable
        return exe, STARTUP_ARG, os.path.dirname(exe)
    python_exe = sys.executable
    if python_exe.lower().endswith("python.exe"):  # pythonw: no console window at logon
        pythonw = os.path.join(os.path.dirname(python_exe), "pythonw.exe")
        if os.path.exists(pythonw):
            python_exe = pythonw
    main_py = get_main_script_path()
    return python_exe, f'"{main_py}" {STARTUP_ARG}', os.path.dirname(main_py)


def _run_key_command() -> str:
    exe, args, _ = launch_command()
    return f'"{exe}" {args}'


# ── scheduled task ────────────────────────────────────────────────────────────

def task_name() -> str:
    user = re.sub(r"[^A-Za-z0-9_.-]", "_", process_user())
    return f"\\{TASK_FOLDER}\\{TASK_PREFIX}{user}"


def build_task_xml(user: str, exe: str, args: str, workdir: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Starts Project Sera when {escape(user)} logs on. Managed from Sera Settings.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{escape(user)}</UserId>
      <Delay>PT0S</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>{TASK_PRIORITY}</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(exe)}</Command>
      <Arguments>{escape(args)}</Arguments>
      <WorkingDirectory>{escape(workdir)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _schtasks(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "schtasks.exe"), *args],
        capture_output=True, text=True, timeout=30, creationflags=_CREATE_NO_WINDOW)


def _tidy(proc: subprocess.CompletedProcess) -> str:
    text = (proc.stderr or proc.stdout or "").strip()
    return re.sub(r"\s+", " ", text) or f"schtasks exited with {proc.returncode}"


def task_exists() -> bool:
    try:
        return _schtasks("/Query", "/TN", task_name()).returncode == 0
    except Exception:
        return False


def task_command() -> Optional[str]:
    """The executable the registered task starts, or None when there is no task."""
    try:
        proc = _schtasks("/Query", "/TN", task_name(), "/XML")
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    m = re.search(r"<Command>(.*?)</Command>", proc.stdout or "", re.S)
    if not m:
        return None
    from xml.sax.saxutils import unescape
    return unescape(m.group(1).strip())


def _create_task() -> tuple[bool, str]:
    exe, args, workdir = launch_command()
    xml = build_task_xml(process_user(), exe, args, workdir)
    fd, path = tempfile.mkstemp(suffix=".xml", prefix="sera_startup_")
    try:
        with os.fdopen(fd, "w", encoding="utf-16") as fh:
            fh.write(xml)
        proc = _schtasks("/Create", "/TN", task_name(), "/XML", path, "/F")
    except Exception as exc:
        return False, str(exc)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return (True, "") if proc.returncode == 0 else (False, _tidy(proc))


def _delete_task() -> tuple[bool, str]:
    """True when no task remains (including 'there never was one')."""
    try:
        if not task_exists():
            return True, ""
        proc = _schtasks("/Delete", "/TN", task_name(), "/F")
    except Exception as exc:
        return False, str(exc)
    return (True, "") if proc.returncode == 0 else (False, _tidy(proc))


# ── Run registry key ──────────────────────────────────────────────────────────

def run_key_value() -> Optional[str]:
    if sys.platform != "win32":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
        return str(value) or None
    except OSError:
        return None


def _set_run_key(enabled: bool) -> tuple[bool, str]:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _run_key_command())
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        return True, ""
    except Exception as exc:
        return False, str(exc)


# ── public API ────────────────────────────────────────────────────────────────

def apply_autostart(enabled: bool) -> AutostartResult:
    """Turn start-at-logon on or off for the current user and say what happened."""
    if sys.platform != "win32":
        return AutostartResult(False, "none", "Start at logon is only available on Windows.")

    if not enabled:
        task_ok, task_err = _delete_task()
        key_ok, key_err = _set_run_key(False)
        if task_ok and key_ok:
            return AutostartResult(True, "none", "")
        return AutostartResult(False, "none", "Could not fully turn off start at logon: "
                               + "; ".join(e for e in (task_err, key_err) if e))

    mismatch = user_mismatch()
    if mismatch:
        return AutostartResult(False, "none", mismatch)

    task_ok, task_err = _create_task()
    if task_ok:
        _set_run_key(False)   # the task replaces the legacy entry; two launchers would double-start
        return AutostartResult(True, "task", "")

    key_ok, key_err = _set_run_key(True)
    if key_ok:
        return AutostartResult(
            True, "run-key",
            f"Windows would not let this account create a logon task ({task_err}). "
            f"Sera will start through the older Run entry instead, which Windows delays by ~10 seconds.")
    return AutostartResult(False, "none",
                           f"Start at logon could not be saved. Task: {task_err}. Run entry: {key_err}.")


def set_autostart_enabled(enabled: bool) -> bool:
    """Boolean form of apply_autostart(); prints the reason when it fails."""
    result = apply_autostart(enabled)
    if not result.ok:
        print(f"[Autostart Error] {result.message}")
    return result.ok


def is_autostart_enabled() -> bool:
    """A logon task or a Run entry for this user is present."""
    if sys.platform != "win32":
        return False
    return task_exists() or run_key_value() is not None


def repair_autostart() -> Optional[AutostartResult]:
    """Run once per launch of an installed build, off the UI thread.

    * Moves a pre-task Run entry to the logon task.
    * Re-points the task when the app was reinstalled somewhere else (a stale path never starts).
    Returns None when nothing needed doing."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return None
    if user_mismatch():
        return None
    command = task_command()
    if command is None:
        if run_key_value() is None:
            return None     # autostart is off
        return apply_autostart(True)
    exe, _, _ = launch_command()
    if os.path.normcase(os.path.abspath(command)) != os.path.normcase(os.path.abspath(exe)):
        return apply_autostart(True)
    if run_key_value() is not None:
        _set_run_key(False)
    return None
