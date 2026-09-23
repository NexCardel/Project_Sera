import re
from pathlib import Path


def test_installer_firewall_rules():
    repo_dir = Path(__file__).resolve().parent.parent
    iss_path = repo_dir / "build_tools" / "installer_setup.iss"
    assert iss_path.exists(), f"{iss_path} should exist"

    content = iss_path.read_text(encoding="utf-8")

    # Verify MyAppExeName is defined as Amas_Sera.exe
    match_exe = re.search(r'#define\s+MyAppExeName\s+"([^"]+)"', content)
    assert match_exe is not None, "MyAppExeName must be defined"
    exe_name = match_exe.group(1)
    assert exe_name == "Amas_Sera.exe", f"Expected Amas_Sera.exe, got {exe_name}"

    # Verify [Run] section contains firewall delete rule (to prevent duplicates on upgrade) before add rule
    assert "[Run]" in content
    run_part = content.split("[Run]", 1)[1].split("[UninstallRun]")[0]
    assert 'Filename: "{sys}\\netsh.exe"' in run_part
    assert 'advfirewall firewall delete rule name=""Amas Sera Sync""' in run_part
    assert 'advfirewall firewall add rule name=""Amas Sera Sync""' in run_part
    assert run_part.index('delete rule') < run_part.index('add rule'), "Delete rule must precede add rule in [Run]"
    assert 'dir=in action=allow' in run_part
    assert (
        'program=""{app}\\{#MyAppExeName}""' in run_part
        or f'program=""{{app}}\\{exe_name}""' in run_part
    )
    assert 'enable=yes' in run_part
    assert 'profile=private,domain' in run_part
    assert 'Flags: runhidden' in run_part

    # Verify [UninstallRun] section exists and contains firewall delete rule and RunOnceId
    assert "[UninstallRun]" in content
    uninstall_part = content.split("[UninstallRun]", 1)[1]
    assert 'Filename: "{sys}\\netsh.exe"' in uninstall_part
    assert 'advfirewall firewall delete rule name=""Amas Sera Sync""' in uninstall_part
    assert 'Flags: runhidden' in uninstall_part
    assert 'RunOnceId: "DelAmasSeraSyncRule"' in uninstall_part
