"""
core/sca/sca_log.py - one line per SCA step, in ~/AmanAssociates_Sera/logs/sca.log
==================================================================================
For finding where an SCA attempt stopped: page seen -> node found -> UID matched -> arm sent -> browser
acknowledged -> what the browser did with it. Rolled to sca.log.1 past LOG_MAX_BYTES.

NEVER written here (SCA v2: the host never logs contents): a UID, a password, a client's name, page
text. Steps name a service by its id, a client by its id, an arm by its arm_id, a UID only by its length.
Failures to write are silent: logging must never break SCA.
"""

import os
import threading
from datetime import datetime
from pathlib import Path

LOG_ENV = "SERA_SCA_LOG"
LOG_MAX_BYTES = 1_000_000

_lock = threading.Lock()


def log_path() -> Path:
    env = os.environ.get(LOG_ENV)
    return Path(env) if env else Path.home() / "AmanAssociates_Sera" / "logs" / "sca.log"


def note(text: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S.%f}"[:-3] + f" {text}"
    try:
        with _lock:
            path = log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
                path.replace(path.with_suffix(".log.1"))
            with open(path, "a", encoding="utf-8") as f:
                f.write(line.rstrip("\n") + "\n")
    except Exception:
        pass
