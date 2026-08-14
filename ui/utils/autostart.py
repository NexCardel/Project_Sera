"""
autostart.py
------------
Utilities to enable or disable automatic launching of Project Sera on Windows PC startup via Registry.
"""

import sys
import os

REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "ProjectSera"

def get_main_script_path() -> str:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    app_dir = os.path.abspath(os.path.join(current_dir, "..", ".."))
    return os.path.join(app_dir, "main.py")

def set_autostart_enabled(enabled: bool) -> bool:
    """Enable or disable Windows startup Registry entry for Project Sera."""
    if sys.platform != "win32":
        return False

    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_ALL_ACCESS)
        if enabled:
            python_exe = sys.executable
            # Prefer pythonw.exe to suppress command prompt window on startup
            if python_exe.endswith("python.exe"):
                pythonw = os.path.join(os.path.dirname(python_exe), "pythonw.exe")
                if os.path.exists(pythonw):
                    python_exe = pythonw
            
            main_py = get_main_script_path()
            cmd = f'"{python_exe}" "{main_py}"'
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
        return True
    except Exception as e:
        print(f"[Autostart Error] {e}")
        return False

def is_autostart_enabled() -> bool:
    """Check if Windows startup Registry entry is currently present."""
    if sys.platform != "win32":
        return False

    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_READ)
        value, _ = winreg.QueryValueEx(key, APP_NAME)
        winreg.CloseKey(key)
        return bool(value)
    except Exception:
        return False
