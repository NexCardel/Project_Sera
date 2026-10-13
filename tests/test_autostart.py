"""Start-at-logon: the task XML, the task/Run-key choice, and the boot helpers.
Nothing here touches the real Task Scheduler or registry."""

import sys
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest

from core import startup_boost
from ui.utils import autostart

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def _xml():
    return ET.fromstring(
        autostart.build_task_xml(r"PC\Aman", r"C:\Program Files\Amas Sera\Amas_Sera.exe",
                                 "--startup", r"C:\Program Files\Amas Sera").split("\n", 1)[1])


def test_task_xml_is_a_logon_task_with_the_agreed_settings():
    root = _xml()
    assert root.find("t:Triggers/t:LogonTrigger/t:UserId", NS).text == r"PC\Aman"
    assert root.find("t:Triggers/t:LogonTrigger/t:Delay", NS).text == "PT0S"
    s = root.find("t:Settings", NS)
    assert s.find("t:Priority", NS).text == "4"
    assert s.find("t:MultipleInstancesPolicy", NS).text == "IgnoreNew"
    assert s.find("t:RestartOnFailure/t:Count", NS).text == "3"
    assert s.find("t:ExecutionTimeLimit", NS).text == "PT0S"
    assert root.find("t:Principals/t:Principal/t:RunLevel", NS).text == "LeastPrivilege"
    ex = root.find("t:Actions/t:Exec", NS)
    assert ex.find("t:Arguments", NS).text == "--startup"
    assert ex.find("t:WorkingDirectory", NS).text == r"C:\Program Files\Amas Sera"


def test_task_xml_escapes_user_and_paths():
    text = autostart.build_task_xml("PC\\A&B <x>", "C:\\a&b\\x.exe", '"m.py" --startup', "C:\\a&b")
    ET.fromstring(text.split("\n", 1)[1])  # still well formed


def test_frozen_build_launches_its_own_exe_with_startup_arg(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Apps\Amas_Sera.exe")
    assert autostart.launch_command() == (r"C:\Apps\Amas_Sera.exe", "--startup", r"C:\Apps")


def test_source_run_launches_python_with_main_py(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    exe, args, workdir = autostart.launch_command()
    assert args.endswith("main.py\" --startup")
    assert workdir


def test_task_name_is_per_user_and_path_safe(monkeypatch):
    monkeypatch.setattr(autostart, "process_user", lambda: r"DOMAIN\Aman Wasil")
    assert autostart.task_name() == r"\Amas Sera\Startup-DOMAIN_Aman_Wasil"


class _Fake:
    """Stands in for the scheduler and the Run key."""

    def __init__(self, task_ok=True, key_ok=True):
        self.task_ok, self.key_ok = task_ok, key_ok
        self.task = None
        self.key = None

    def install(self, monkeypatch):
        monkeypatch.setattr(autostart, "user_mismatch", lambda: None)
        monkeypatch.setattr(autostart, "_create_task", self._create)
        monkeypatch.setattr(autostart, "_delete_task", self._delete)
        monkeypatch.setattr(autostart, "_set_run_key", self._set_key)
        monkeypatch.setattr(autostart, "task_exists", lambda: self.task is not None)
        monkeypatch.setattr(autostart, "run_key_value", lambda: self.key)
        monkeypatch.setattr(autostart, "task_command", lambda: self.task)
        monkeypatch.setattr(autostart, "launch_command", lambda: (r"C:\new\Amas_Sera.exe", "--startup", r"C:\new"))
        monkeypatch.setattr(autostart, "_run_key_command", lambda: "RUNKEY-CMD")
        monkeypatch.setattr(sys, "platform", "win32")
        return self

    def _create(self):
        if not self.task_ok:
            return False, "Access is denied."
        self.task = r"C:\new\Amas_Sera.exe"
        return True, ""

    def _delete(self):
        self.task = None
        return True, ""

    def _set_key(self, enabled):
        if not self.key_ok:
            return False, "registry blocked"
        self.key = "RUNKEY-CMD" if enabled else None
        return True, ""


def test_enable_uses_the_task_and_retires_the_run_entry(monkeypatch):
    fake = _Fake().install(monkeypatch)
    fake.key = "old"
    result = autostart.apply_autostart(True)
    assert (result.ok, result.method) == (True, "task")
    assert fake.task and fake.key is None


def test_enable_falls_back_to_run_key_and_says_why(monkeypatch):
    fake = _Fake(task_ok=False).install(monkeypatch)
    result = autostart.apply_autostart(True)
    assert (result.ok, result.method) == (True, "run-key")
    assert "Access is denied" in result.message
    assert fake.key == "RUNKEY-CMD"


def test_enable_reports_failure_when_nothing_can_be_written(monkeypatch):
    _Fake(task_ok=False, key_ok=False).install(monkeypatch)
    result = autostart.apply_autostart(True)
    assert not result.ok
    assert "Access is denied" in result.message and "registry blocked" in result.message


def test_enable_refuses_when_running_as_another_account(monkeypatch):
    fake = _Fake().install(monkeypatch)
    monkeypatch.setattr(autostart, "user_mismatch", lambda: "running as a different user")
    result = autostart.apply_autostart(True)
    assert not result.ok and "different user" in result.message
    assert fake.task is None and fake.key is None


def test_disable_removes_both(monkeypatch):
    fake = _Fake().install(monkeypatch)
    fake.task, fake.key = "x", "y"
    assert autostart.apply_autostart(False).ok
    assert fake.task is None and fake.key is None


def test_is_enabled_sees_task_or_run_key(monkeypatch):
    fake = _Fake().install(monkeypatch)
    assert not autostart.is_autostart_enabled()
    fake.key = "y"
    assert autostart.is_autostart_enabled()
    fake.key, fake.task = None, "x"
    assert autostart.is_autostart_enabled()


def test_repair_migrates_legacy_run_key(monkeypatch):
    fake = _Fake().install(monkeypatch)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    fake.key = "old"
    result = autostart.repair_autostart()
    assert result and result.method == "task" and fake.key is None


def test_repair_repoints_a_task_whose_exe_moved(monkeypatch):
    fake = _Fake().install(monkeypatch)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    fake.task = r"C:\old\Amas_Sera.exe"
    result = autostart.repair_autostart()
    assert result and fake.task == r"C:\new\Amas_Sera.exe"


def test_repair_leaves_a_correct_setup_alone_and_stays_off_when_off(monkeypatch):
    fake = _Fake().install(monkeypatch)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert autostart.repair_autostart() is None and fake.task is None   # off stays off
    fake.task = r"C:\new\Amas_Sera.exe"
    assert autostart.repair_autostart() is None


def test_repair_does_nothing_from_source(monkeypatch):
    _Fake().install(monkeypatch).key = "old"
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert autostart.repair_autostart() is None


# ── startup_boost ─────────────────────────────────────────────────────────────

def test_launched_at_startup_flag():
    assert startup_boost.launched_at_startup(["Amas_Sera.exe", "--startup"])
    assert not startup_boost.launched_at_startup(["Amas_Sera.exe"])


def test_retry_recovers_then_gives_up(monkeypatch):
    monkeypatch.setattr(startup_boost.time, "sleep", lambda s: None)
    calls = SimpleNamespace(n=0)

    def flaky():
        calls.n += 1
        if calls.n < 3:
            raise OSError("locked")
        return "ok"

    assert startup_boost.retry(flaky, attempts=4, delay=0) == "ok" and calls.n == 3

    def never():
        raise OSError("still locked")

    with pytest.raises(OSError):
        startup_boost.retry(never, attempts=2, delay=0)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows mutex")
def test_second_claim_in_the_same_session_is_refused(monkeypatch):
    monkeypatch.setattr(startup_boost, "_MUTEX_NAME", "Local\\AmasSeraRunningTest")
    monkeypatch.setattr(startup_boost, "_mutex_handle", None)
    assert startup_boost.claim_instance() is True
    assert startup_boost.claim_instance() is False
