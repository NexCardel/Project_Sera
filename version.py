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
import threading
import time
from pathlib import Path
from typing import Optional, Dict, Tuple, Callable

APP_VERSION = "2.9.7"
GITHUB_REPO = "NexCardel/Project_Sera"
VERSION_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/version.json"
RELEASES_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


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


def check_for_updates(timeout_seconds: int = 5) -> Optional[Dict]:
    """
    Query GitHub raw content for version.json, with automatic fallback
    to GitHub Releases API to guarantee reliable update notifications.
    Returns dictionary with update info if a newer version is available, else None.
    """
    # 1. First attempt: Query version.json with cache buster
    cache_buster = int(time.time())
    req = urllib.request.Request(
        f"{VERSION_URL}?_cb={cache_buster}",
        headers={
            "User-Agent": f"ProjectSera-Updater/{APP_VERSION}",
            "Cache-Control": "no-cache"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                latest_v = data.get("version", "0.0.0")
                min_req_v = data.get("min_required_version", "0.0.0")
                
                update_needed = is_update_available(latest_v, APP_VERSION)
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
    except Exception as e:
        print(f"[Updater] version.json check error: {e}")

    # 2. Secondary fallback: Query GitHub Releases API directly
    # This prevents missed updates if version.json wasn't bumped on the main branch.
    try:
        api_req = urllib.request.Request(
            RELEASES_API_URL,
            headers={
                "User-Agent": f"ProjectSera-Updater/{APP_VERSION}",
                "Accept": "application/vnd.github.v3+json"
            }
        )
        with urllib.request.urlopen(api_req, timeout=timeout_seconds) as api_resp:
            if api_resp.status == 200:
                rel_data = json.loads(api_resp.read().decode("utf-8"))
                tag_v = rel_data.get("tag_name", "").strip().lstrip("vV")
                if is_update_available(tag_v, APP_VERSION):
                    download_url = None
                    for asset in rel_data.get("assets", []):
                        if asset.get("name", "").endswith(".exe"):
                            download_url = asset.get("browser_download_url")
                            break
                    if not download_url:
                        download_url = rel_data.get("html_url", f"https://github.com/{GITHUB_REPO}/releases/latest")
                    
                    return {
                        "latest_version": tag_v,
                        "current_version": APP_VERSION,
                        "mandatory": False,
                        "download_url": download_url,
                        "release_notes": rel_data.get("body") or "A new release is available on GitHub."
                    }
    except Exception as e:
        print(f"[Updater] GitHub Releases API fallback error: {e}")

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


def apply_and_restart(installer_path: Path, silent: bool = True, target_exe: Optional[str] = None):
    """
    Launch installer/update script and exit current process.
    If silent is True, passes /VERYSILENT /SUPPRESSMSGBOXES /NORESTART to Inno Setup,
    waits for installer to finish, and automatically relaunches the updated executable.
    """
    temp_dir = installer_path.parent
    bat_script = temp_dir / "run_installer.bat"

    if target_exe is None:
        target_exe = sys.executable if getattr(sys, "frozen", False) else ""

    if silent:
        relaunch_cmd = f'start "" "{target_exe}"' if target_exe else ""
        script_content = f"""@echo off
timeout /t 2 /nobreak > nul
start /wait "" "{installer_path.resolve()}" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
{relaunch_cmd}
"""
    else:
        script_content = f"""@echo off
timeout /t 2 /nobreak > nul
start "" "{installer_path.resolve()}"
"""
    with open(bat_script, "w", encoding="utf-8") as f:
        f.write(script_content)

    subprocess.Popen(["cmd.exe", "/c", str(bat_script.resolve())], creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)

    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            app.quit()
    except Exception:
        pass
    sys.exit(0)


class BackgroundUpdateManager:
    """
    Automated background update service for Project Sera.
    Checks GitHub for releases asynchronously, downloads update payloads
    without user interaction or manual permission, and prepares the silent update.
    """
    def __init__(
        self,
        check_interval_seconds: int = 7200,
        on_update_found: Optional[Callable[[Dict], None]] = None,
        on_download_progress: Optional[Callable[[int, int], None]] = None,
        on_update_ready: Optional[Callable[[Path, Dict], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ):
        self.check_interval = check_interval_seconds
        self.on_update_found = on_update_found
        self.on_download_progress = on_download_progress
        self.on_update_ready = on_update_ready
        self.on_error = on_error

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._is_downloading = False
        self.downloaded_payload: Optional[Path] = None
        self.pending_update_info: Optional[Dict] = None

    def start(self, initial_delay_seconds: int = 3):
        """Starts the background updater daemon thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            args=(initial_delay_seconds,),
            name="sera-background-updater",
            daemon=True
        )
        self._thread.start()

    def stop(self):
        """Stops the background updater loop."""
        self._running = False

    def trigger_check(self):
        """Triggers an immediate asynchronous check and download in a background thread."""
        threading.Thread(target=self._check_and_download, daemon=True).start()

    def _run_loop(self, initial_delay: int):
        if initial_delay > 0:
            time.sleep(initial_delay)
        while self._running:
            self._check_and_download()
            for _ in range(self.check_interval):
                if not self._running:
                    break
                time.sleep(1)

    def _check_and_download(self):
        if self._is_downloading or self.downloaded_payload is not None:
            return

        update_info = check_for_updates(timeout_seconds=4)
        if not update_info:
            return

        self.pending_update_info = update_info
        if self.on_update_found:
            try:
                self.on_update_found(update_info)
            except Exception:
                pass

        download_url = update_info.get("download_url")
        if not download_url:
            return

        self._is_downloading = True
        try:
            dest_path = download_update_payload(
                download_url,
                progress_callback=self.on_download_progress
            )
            self.downloaded_payload = dest_path
            if self.on_update_ready:
                try:
                    self.on_update_ready(dest_path, update_info)
                except Exception:
                    pass
        except Exception as e:
            if self.on_error:
                try:
                    self.on_error(str(e))
                except Exception:
                    pass
        finally:
            self._is_downloading = False


def restart_app():
    """
    Cleanly restarts the application on Windows, macOS, and Linux.
    Avoids os.execl thread corruption issues with PySide6/Qt on Windows.
    """
    from PySide6.QtWidgets import QApplication

    if getattr(sys, "frozen", False):
        cmd = [sys.executable] + sys.argv[1:]
    else:
        cmd = [sys.executable] + sys.argv

    subprocess.Popen(cmd)
    app = QApplication.instance()
    if app:
        app.quit()
    sys.exit(0)

