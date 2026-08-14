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


def build():
    print("====================================================")
    print("   Building Amas Sera Standalone Executable Bundle  ")
    print("====================================================")
    
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
