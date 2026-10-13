"""
tools/sera_tool_common.py - what the offline recovery tools share
==================================================================
The app-is-running gate and the way to open the app's encrypted database from a script. Used by
tools/inject_unknown_gst_arn.py and tools/mr_fixer.py.
"""

import socket
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def app_dir_default() -> Path:
    return Path.home() / "AmanAssociates_Sera"


def app_is_running() -> bool:
    """True when a port the app's extension bridge binds at start-up is taken. Conservative: a
    different program holding one of those ports also counts as running."""
    from ui.ws_bridge import WS_PORTS                       # imports Qt: only when needed
    for port in WS_PORTS:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                return True
    return False


def open_database(app_dir: Path) -> Any:
    import sera_keys
    from database import SeraDatabase
    hex_key = sera_keys.dek_hex(sera_keys.load_dek(app_dir))      # the key is never printed
    return SeraDatabase(str(app_dir / "master.db"), hex_key, defer_startup_maintenance=True, key_mode="office")


def actor_name(app_dir: Path) -> str:
    try:
        name = (app_dir / "device_identity.txt").read_text(encoding="utf-8").strip()
    except OSError:
        name = ""
    return name or socket.gethostname()
