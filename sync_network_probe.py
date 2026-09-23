"""
sync_network_probe.py — Sera Sync v3, P0-9b: is this PC on a "Public" Windows
network profile?

Windows Firewall blocks the LAN discovery broadcast (F10) on networks marked
"Public" (coffee shops, phone hotspots, unrecognised networks). Nothing here
changes firewall behaviour — it only detects the situation so the Sera Sync
panel can tell the user how to fix it themselves (Settings > Network > set to
Private). No PySide6 import (see docs/sera-sync-v3-blueprint.md §0 rule 7):
UI wiring happens in sync_peer.py / ui/dialogs/sera_sync_dialog.py.

Primary path: the NetworkListManager COM object (comtypes, already a
dependency). Fallback: `Get-NetConnectionProfile` via PowerShell, for the
rare case COM is unavailable. Both are imported lazily.
"""

import json
import os
import subprocess
import sys
import threading
from typing import Any, Callable, List, Optional

CLSID_NETWORK_LIST_MANAGER = "{DCB00C01-570F-4A9B-8D69-199FDBA5723B}"
NLM_ENUM_NETWORK_CONNECTED = 1

# NLM_NETWORK_CATEGORY: https://learn.microsoft.com/windows/win32/api/netlistmgr/ne-netlistmgr-nlm_network_category
_CATEGORY_NAMES = {0: "public", 1: "private", 2: "domain"}

# Get-NetConnectionProfile's NetworkCategory comes out as the string name on
# some PowerShell/CIM builds ("Public") and as the bare enum number on others
# (confirmed on Windows PowerShell 5.1: NetworkCategory is 0/1/2, matching the
# NLM_NETWORK_CATEGORY values above) - accept both.
_POWERSHELL_CATEGORY_NAMES = {
    "Public": "public",
    "Private": "private",
    "DomainAuthenticated": "domain",
    "0": "public",
    "1": "private",
    "2": "domain",
}

_POWERSHELL_EXE = os.path.join(
    os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe"
)

DEFAULT_POLL_INTERVAL_SECONDS = 600  # 10 minutes, per spec


def _query_categories_comtypes() -> List[str]:
    """
    Reads the category of every connected network via NetworkListManager.

    Must run on a thread that keeps its COM apartment alive for the whole
    process lifetime (see NetworkCategoryMonitor._loop): CoInitialize is
    idempotent per thread, but CoUninitialize-ing while COM objects created
    on that thread are still reachable (e.g. about to be garbage collected)
    crashes the interpreter (comtypes releases the interface pointer after
    the apartment is gone). Same convention as core/vsdc/vsdc_uia_text.py.
    """
    import comtypes
    import comtypes.client

    try:
        comtypes.CoInitialize()
    except Exception:
        pass
    manager = comtypes.client.CreateObject(CLSID_NETWORK_LIST_MANAGER, dynamic=True)
    networks = manager.GetNetworks(NLM_ENUM_NETWORK_CONNECTED)
    categories = []
    try:
        for network in networks:
            try:
                category = int(network.GetCategory())
                categories.append(_CATEGORY_NAMES.get(category, "unknown"))
            except Exception:
                pass
            finally:
                try:
                    del network
                except Exception:
                    pass
    finally:
        try:
            del networks
            del manager
        except Exception:
            pass
    return categories


def _query_categories_powershell() -> List[str]:
    """Fallback for when COM is unavailable: `Get-NetConnectionProfile`."""
    powershell = _POWERSHELL_EXE if os.path.exists(_POWERSHELL_EXE) else "powershell"
    run_kwargs = {}
    if sys.platform == "win32":
        run_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    proc = subprocess.run(
        [powershell, "-NoProfile", "-Command", "Get-NetConnectionProfile | ConvertTo-Json"],
        capture_output=True,
        text=True,
        timeout=15,
        **run_kwargs,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Get-NetConnectionProfile exited {proc.returncode}: {proc.stderr.strip()}")
    stdout = proc.stdout.strip()
    if not stdout:
        return []
    data = json.loads(stdout)
    if isinstance(data, dict):
        data = [data]
    categories = []
    for entry in data:
        # PowerShell's ConvertTo-Json renders NetworkCategory as either the
        # enum name ("Public") or the bare number (0), depending on the
        # PowerShell/CIM build - normalise both to the same lookup key.
        name = str(entry.get("NetworkCategory", "")).strip()
        categories.append(_POWERSHELL_CATEGORY_NAMES.get(name, "unknown"))
    return categories


def probe_network_category() -> dict:
    """
    Returns {"is_public": bool, "categories": [str, ...], "method": str, "error": str | None}.

    "is_public" is True only when a connected network was positively identified
    as Public. If both the COM and PowerShell probes fail, "is_public" is False
    (fail quiet, not fail warning) and "error" carries both failure messages.
    """
    try:
        categories = _query_categories_comtypes()
        method = "comtypes"
        error = None
    except Exception as comtypes_error:
        try:
            categories = _query_categories_powershell()
            method = "powershell"
            error = None
        except Exception as powershell_error:
            error = f"comtypes: {comtypes_error}; powershell: {powershell_error}"
            print(f"[Sera Sync] network category probe failed: {error}")
            return {
                "is_public": False,
                "categories": [],
                "method": "unknown",
                "error": error,
            }
    return {
        "is_public": "public" in categories,
        "categories": categories,
        "method": method,
        "error": error,
    }


class NetworkCategoryMonitor:
    """
    Background poller: calls `probe` at start() and every `interval_seconds`
    afterwards, caching the latest result and (optionally) notifying a
    callback. Mirrors the threading pattern already used by
    SyncPeerService._start_peer_reaper (sync_peer.py).
    """

    def __init__(
        self,
        on_change: Optional[Callable[[dict], None]] = None,
        interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS,
        probe: Callable[[], dict] = probe_network_category,
    ):
        self.on_change = on_change
        self.interval_seconds = interval_seconds
        self.probe = probe
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last_result: dict = {"is_public": False, "categories": [], "method": "unknown", "error": None}

    def start(self):
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="NetworkCategoryMonitor", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=0.05)

    def poll_once(self) -> dict:
        try:
            result = self.probe()
        except Exception as e:
            result = {"is_public": False, "categories": [], "method": "unknown", "error": str(e)}
        self.last_result = result
        if self.on_change:
            try:
                self.on_change(result)
            except Exception:
                pass
        return result

    def _loop(self):
        try:
            import comtypes
            comtypes.CoInitialize()
        except Exception:
            pass
        while not self._stop_event.is_set():
            self.poll_once()
            self._stop_event.wait(self.interval_seconds)
