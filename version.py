"""
version.py
----------
Version metadata and automated GitHub update checking service for Project Sera.
"""

import sys
import os
import json
import urllib.request
import urllib.error
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Dict, Tuple, Callable

APP_VERSION = "2.3.4.1"
GITHUB_REPO = "NexCardel/Project_Sera"
VERSION_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/version.json"


def parse_version(v_str: str) -> Tuple[int, ...]:
    """Parse version string into a integer tuple for clean comparison."""
    clean_v = v_str.strip().lstrip("vV")
    parts = []
    for part in clean_v.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def is_update_available(latest_version: str, current_version: str = APP_VERSION) -> bool:
    """Return True if latest_version > current_version."""
    return parse_version(latest_version) > parse_version(current_version)


def check_for_updates(timeout_seconds: int = 4) -> Optional[Dict]:
    """
    Query GitHub raw content for version.json.
    Returns dictionary with update info if a newer version is available, else None.
    """
    req = urllib.request.Request(
        VERSION_URL,
        headers={"User-Agent": "ProjectSera-Updater/2.3.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                latest_v = data.get("version", "0.0.0")
                min_req_v = data.get("min_required_version", "0.0.0")
                
                # Check if update is available or mandatory
                update_needed = is_update_available(latest_v, APP_VERSION)
                # A mandatory flag only applies to an actual newer release. Without
                # this guard, a build can be forced to download itself forever.
                is_mandatory = update_needed and (
                    data.get("mandatory", False)
                    or is_update_available(min_req_v, APP_VERSION)
                )
                
                if update_needed or is_mandatory:
                    return {
                        "latest_version": latest_v,
                        "current_version": APP_VERSION,
                        "mandatory": is_mandatory,
                        "download_url": data.get("download_url", f"https://github.com/{GITHUB_REPO}/releases/latest"),
                        "release_notes": data.get("release_notes", "A new security and feature update is available for Project Sera.")
                    }
    except urllib.error.HTTPError as e:
        # A release branch/repository may not publish version metadata yet. This
        # is a normal no-update state and should not alarm staff at startup.
        if e.code != 404:
            print(f"[Updater] Version check skipped/failed: {e}")
    except Exception as e:
        print(f"[Updater] Version check skipped/failed: {e}")
    return None


def download_update_payload(download_url: str, progress_callback: Optional[Callable[[int, int], None]] = None) -> Path:
    """
    Download update executable/installer to temp folder.
    """
    temp_dir = Path(tempfile.gettempdir()) / "ProjectSera_Update"
    temp_dir.mkdir(parents=True, exist_ok=True)
    dest_path = temp_dir / "Amas_Sera_Update.exe"
    
    req = urllib.request.Request(
        download_url,
        headers={"User-Agent": "ProjectSera-Updater/2.3.0"}
    )
    
    with urllib.request.urlopen(req) as response, open(dest_path, "wb") as out_file:
        total_size = int(response.getheader("Content-Length", 0))
        downloaded = 0
        chunk_size = 65536
        
        while True:
            chunk = response.read(chunk_size)
            if not chunk:
                break
            out_file.write(chunk)
            downloaded += len(chunk)
            if progress_callback and total_size > 0:
                progress_callback(downloaded, total_size)
                
    return dest_path


def apply_and_restart(installer_path: Path):
    """
    Launch installer/update script and exit current process.
    """
    temp_dir = installer_path.parent
    bat_script = temp_dir / "run_installer.bat"
    
    # Batch file waits 2s for current Amas_Sera app to close, then launches installer
    script_content = f"""@echo off
timeout /t 2 /nobreak > nul
start "" "{installer_path.resolve()}"
"""
    with open(bat_script, "w", encoding="utf-8") as f:
        f.write(script_content)
        
    subprocess.Popen(["cmd.exe", "/c", str(bat_script.resolve())], creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    sys.exit(0)
