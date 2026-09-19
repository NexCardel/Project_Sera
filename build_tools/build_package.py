"""
build_package.py
----------------
Build script to compile Project Sera (Amas_Sera) into a standalone Windows executable
and generate the Windows Packaged Installer using Inno Setup.
"""

import sys
import subprocess
import shutil
from pathlib import Path

from build_extension import build as build_extension

APP_DIR = Path(__file__).resolve().parent.parent

# Imported by the app at runtime. PyInstaller only WARNS when a hidden import is
# missing and still produces a bundle, so without this check a build from an
# incomplete environment ships an installer that launches fine but captures nothing.
REQUIRED_RUNTIME_MODULES = (
    "PySide6.QtWidgets", "qtawesome", "sqlcipher3", "cryptography", "argon2",
    "openpyxl", "pandas", "numpy", "lxml", "playwright",
    "PIL",                                   # VSDC screen capture
    "comtypes", "comtypes.client",           # VSDC-X UI Automation
    "winrt.windows.media.ocr",               # legacy VSDC OCR
    "winrt.windows.graphics.imaging",
    "winrt.windows.storage.streams",
    "winrt.windows.foundation",
)


def preflight():
    """Refuses to build anything that would install but not work."""
    import importlib
    import json
    import re

    problems = []

    missing = []
    for name in REQUIRED_RUNTIME_MODULES:
        try:
            importlib.import_module(name)
        except Exception as exc:
            missing.append(f"{name} ({type(exc).__name__})")
    if missing:
        problems.append(
            "These runtime packages are not importable in the interpreter running this build "
            f"({sys.executable}):\n      " + "\n      ".join(missing)
            + "\n    Build from the project venv:  venv\\Scripts\\python build_tools\\build_package.py"
        )

    # VSDC-X needs comtypes' generated UIAutomationClient wrapper. Generating it here,
    # in the build interpreter, means the bundle ships it ready-made instead of relying
    # on runtime code generation inside a read-only Program Files install.
    if not missing:
        try:
            import comtypes.client
            comtypes.client.GetModule("UIAutomationCore.dll")
            import comtypes.gen.UIAutomationClient  # noqa: F401
        except Exception as exc:
            problems.append(f"Could not generate comtypes' UIAutomationClient wrapper: {exc}")

    # A missing key would be silently REPLACED by build_extension.get_key(), minting a
    # new extension ID and breaking the native-host link on every existing install.
    key_path = APP_DIR / "build_tools" / "sera_extension.pem"
    if not key_path.exists():
        problems.append(
            f"Extension signing key not found at {key_path}. Refusing to build: a new key "
            "means a new extension ID, which breaks every installed copy's native host link."
        )

    # All shipped version numbers must agree, or the updater and Chrome's external
    # extension updater see different versions of the same release.
    versions = {}
    m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', (APP_DIR / "version.py").read_text(encoding="utf-8"))
    versions["version.py APP_VERSION"] = m.group(1) if m else None
    m = re.search(r'#define MyAppVersion "([^"]+)"',
                  (APP_DIR / "build_tools" / "installer_setup.iss").read_text(encoding="utf-8"))
    versions["installer_setup.iss MyAppVersion"] = m.group(1) if m else None
    for ext in ("sera_extension", "sera_extension_firefox"):
        manifest = json.loads((APP_DIR / ext / "manifest.json").read_text(encoding="utf-8"))
        versions[f"{ext}/manifest.json"] = manifest.get("version")
    if len(set(versions.values())) != 1:
        problems.append("Version numbers disagree:\n      "
                        + "\n      ".join(f"{k}: {v}" for k, v in versions.items()))

    if problems:
        print("\nPREFLIGHT FAILED - nothing was built:\n")
        for p in problems:
            print(f"  - {p}\n")
        sys.exit(1)
    print(f"Preflight OK - building version {next(iter(versions.values()))} with {sys.executable}")


def build():
    print("====================================================")
    print("   Building Amas Sera Standalone Executable Bundle  ")
    print("====================================================")
    
    preflight()

    spec_file = APP_DIR / "build_tools" / "Amas_Sera.spec"

    if not spec_file.exists():
        print(f"Error: Spec file {spec_file} not found!")
        sys.exit(1)
        
    dist_dir = APP_DIR / "package_dist" / "Amas_Sera"
    if dist_dir.exists():
        try:
            shutil.rmtree(dist_dir, ignore_errors=True)
        except Exception:
            pass

    build_extension()

    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm",
        "--distpath", str(APP_DIR / "package_dist"),
        "--workpath", str(APP_DIR / "package_build"),
        str(spec_file),
    ]
    print(f"Running command: {' '.join(cmd)}")
    
    res = subprocess.run(cmd, cwd=str(APP_DIR))
    if res.returncode != 0:
        print("\nERROR: PyInstaller build failed! Please check error output above.")
        sys.exit(res.returncode)
        
    exe_file = dist_dir / "Amas_Sera.exe"
    
    if exe_file.exists():
        print("\n====================================================")
        print(" SUCCESS! Amas Sera Executable Bundle Created!")
        print("====================================================")
        print(f"Executable Location: {exe_file}")
        print(f"Bundle Directory:   {dist_dir}\n")
        print("Run Inno Setup Compiler against build_tools/installer_setup.iss to create the installer.")
    else:
        print(f"\nERROR: Executable was not found at {exe_file}")

if __name__ == "__main__":
    build()
