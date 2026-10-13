"""
core/sdis/miner_client.py - "Find datapoints" from the app's side (SDIS Part O)
================================================================================
MinerClient.start(captures, state) starts the mining child (mine_process.py through main.py's --sdis-mine
flag: the app's own exe when frozen, `python main.py` from source), with no window and no CPU cap. A reader
thread turns its JSON lines into on_progress(done, total, page) and one final on_done(result). Both are
called ON THE READER THREAD: a Qt caller passes them on through a signal.

cancel() ends the child at once; on_done then gets {"result": "cancelled"}. Nothing is lost: mine() saves
the state after every client map (atomically), and the next run resumes from it.
"""

import json
import logging
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[2]


class MinerClient:
    def __init__(self, on_progress: Optional[Callable[[int, int, str], None]] = None,
                 on_done: Optional[Callable[[Dict[str, Any]], None]] = None) -> None:
        self.on_progress = on_progress
        self.on_done = on_done
        self.proc: Optional[subprocess.Popen] = None
        self._reader: Optional[threading.Thread] = None
        self._cancelled = False

    def command(self, captures: Any, state: Any, rebuild: bool = False) -> List[str]:
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--sdis-mine"]
        else:
            cmd = [sys.executable, str(REPO / "main.py"), "--sdis-mine"]
        cmd += ["--captures", str(captures), "--state", str(state)]
        return cmd + (["--rebuild"] if rebuild else [])

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, captures: Any, state: Any = None, rebuild: bool = False) -> None:
        if self.running():
            raise RuntimeError("mining is already running")
        if state is None:
            from core.sdis.store import default_path
            state = default_path()                           # resolved here: the child would load Qt for it
        self._cancelled = False
        self.proc = subprocess.Popen(
            self.command(captures, state, rebuild), cwd=str(REPO),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self._reader = threading.Thread(target=self._read, args=(self.proc,), name="sdis-miner", daemon=True)
        self._reader.start()

    def cancel(self) -> None:
        proc = self.proc
        if proc is not None and proc.poll() is None:
            self._cancelled = True
            proc.kill()

    def wait(self, timeout: Optional[float] = None) -> bool:
        """True when the reader thread has finished (on_done was called)."""
        if self._reader is not None:
            self._reader.join(timeout)
            return not self._reader.is_alive()
        return True

    def _read(self, proc: subprocess.Popen) -> None:
        result: Optional[Dict[str, Any]] = None
        for raw in proc.stdout:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue                                     # not ours (a stray print)
            if not isinstance(msg, dict):
                continue
            if "result" in msg:
                result = msg
            elif "done" in msg and self.on_progress is not None:
                self.on_progress(int(msg["done"]), int(msg.get("total", 0)), str(msg.get("page", "")))
        proc.stdout.close()
        code = proc.wait()
        if self._cancelled and result is None:
            result = {"result": "cancelled"}
        elif result is None:
            result = {"result": "error", "message": f"the mining process ended without a result (exit {code})"}
        log.info("SDIS mining: %s", result["result"])
        if self.on_done is not None:
            self.on_done(result)
