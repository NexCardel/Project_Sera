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

APP_DIR = Path(__file__).resolve().parent.parent


def build():
    print("====================================================")
    print("   Building Amas Sera Standalone Executable Bundle  ")
    print("====================================================")
    
    spec_file = APP_DIR / "build_tools" / "Amas_Sera.spec"

    if not spec_file.exists():
        print(f"Error: Spec file {spec_file} not found!")
        sys.exit(1)
        
    dist_dir = APP_DIR / "dist" / "CompanyInfo1"
    if dist_dir.exists():
        try:
            shutil.rmtree(dist_dir, ignore_errors=True)
        except Exception:
            pass

    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", str(spec_file)]
    print(f"Running command: {' '.join(cmd)}")
    
    res = subprocess.run(cmd, cwd=str(APP_DIR))
    if res.returncode != 0:
        print("\nERROR: PyInstaller build failed! Please check error output above.")
        sys.exit(res.returncode)
        
    exe_file = dist_dir / "CompanyInfo1.exe"
    
    if exe_file.exists():
        print("\n====================================================")
        print(" SUCCESS! Amas Sera Executable Bundle Created!")
        print("====================================================")
        print(f"Executable Location: {exe_file}")
        print(f"Bundle Directory:   {dist_dir}\n")
        print("Next Steps for Creating the Windows Setup Installer:")
        print("1. Install Inno Setup (free) from: https://jrsoftware.org/isinfo.php")
        print("2. Open 'installer_setup.iss' in Inno Setup Compiler.")
        print("3. Click 'Build -> Compile' (or press Ctrl+F9).")
        print("4. Your 1-click installer 'Amas_Sera_Setup_v2.0.exe' will be created in 'installer_output/'.")
    else:
        print(f"\nERROR: Executable was not found at {exe_file}")

if __name__ == "__main__":
    build()
