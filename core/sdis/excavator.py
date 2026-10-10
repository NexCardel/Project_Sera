"""
core/sdis/excavator.py - SDIS excavator (corpus fix, phase 1)
=============================================================
Digs through the SDIS capture folder and makes sure the corpus reaches the admin PC. It runs on its
own timer: it does not wait for a Sera Sync "synced" event (a nudge from one only makes it look
sooner), and when nothing is unsent it does nothing at all (no connection is opened).

Staff PC, every pass:
    finished recorder files on disk (past days; today's open file is left alone)
      -> none            : idle, silent
      -> some, admin PC unknown / unreachable / refusing : PARKED - the files stay where they are,
                           one live line in the sync panel says why, retry 1, 2, 5, then every 15 min
      -> some, admin PC reachable : push them, delete exactly the files the admin PC acked

Admin PC, every pass: nothing to send (its own folder is mined in place); it checks what the staff
PCs delivered under corpus/<device id>/ - complete, readable, no half-received .part files - and
says so in the sync panel when that changes.

Only counts and sizes are ever logged, never names of clients or contents. The wire protocol and the
admin side live in core/sdis/transfer.py.
"""

import logging
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sync_transport import TransportError

from core.sdis import transfer

_log = logging.getLogger(__name__)

TICK_S = 60.0                       # how often a pass looks at the folder when all is well
BACKOFF_S = (60.0, 120.0, 300.0, 900.0)
NUDGE_GAP_S = 30.0                  # a nudge never starts a pass sooner than this after the last one
MAX_BATCHES = 20                    # batches (<= 200 files / 200 MB each) per pass
FIRST_LINE_CAP = 16 * 1024 * 1024

CATEGORY = "SDIS"


def _mb(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MB"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


class Excavator:
    def __init__(self, engine, folder: Optional[Path] = None, on_event=None, tick_s: float = TICK_S,
                 backoff: Tuple[float, ...] = BACKOFF_S, received_root: Optional[Path] = None):
        self.engine = engine
        self.on_event = on_event                      # on_event(category, title, detail): the sync panel's live log
        self._folder = folder
        self._received_root = received_root
        self.tick_s = tick_s
        self.backoff = backoff
        self.status: Dict[str, object] = {"state": "starting", "reason": "", "unsent_files": 0,
                                          "unsent_bytes": 0, "received_files": 0, "received_bytes": 0}
        self.stats = {"passes": 0, "sent": 0, "acked": 0, "deleted": 0, "parked": 0, "failed": 0}
        self._fails = 0
        self._last_pass = 0.0
        self._last_key = None
        self._checked: Dict[tuple, bool] = {}         # (path, size, mtime_ns) -> readable
        self._run_lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------ plumbing

    @property
    def folder(self) -> Path:
        if self._folder is None:
            from core.sdis.recorder import default_dir
            self._folder = default_dir()
        return self._folder

    @property
    def received_root(self) -> Path:
        if self._received_root is None:
            from core.sdis.paths import data_dir
            self._received_root = data_dir() / "corpus"
        return self._received_root

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="sdis-excavator", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def nudge(self, *_args) -> bool:
        """Look sooner (a sync round just succeeded, so peers are reachable). Never starts a pass
        within NUDGE_GAP_S of the last one."""
        if time.monotonic() - self._last_pass < NUDGE_GAP_S:
            return False
        self._fails = 0
        self._wake.set()
        return True

    def _loop(self) -> None:
        delay = 5.0
        while not self._stop.is_set():
            self._wake.wait(delay)
            self._wake.clear()
            if self._stop.is_set():
                break
            self.dig()
            if self._fails:
                delay = self.backoff[min(self._fails - 1, len(self.backoff) - 1)]
            else:
                delay = self.tick_s

    def _say(self, key, title: str, detail: str = "") -> None:
        """One live line for the sync panel; the same key twice in a row is said once."""
        if key == self._last_key:
            return
        self._last_key = key
        if self.on_event is None:
            return
        try:
            self.on_event(CATEGORY, title, detail)
        except Exception:
            pass

    def _set(self, state: str, reason: str = "", **extra) -> None:
        self.status.update(state=state, reason=reason, **extra)

    # ------------------------------------------------------------ one pass

    def dig(self) -> Dict[str, object]:
        """One pass. Never raises. Returns the status."""
        with self._run_lock:
            self._last_pass = time.monotonic()
            self.stats["passes"] += 1
            try:
                import sync_admin
                if sync_admin.is_admin_pc(self.engine.app_dir):
                    self._fails = 0
                    self._inspect_received()
                else:
                    self._send_unsent()
            except Exception:
                self.stats["failed"] += 1
                self._fails += 1
                _log.exception("SDIS excavator pass failed")
                self._set("error", "excavator pass failed")
                self._say(("error",), "Corpus check failed", "see the log; will retry")
            return dict(self.status)

    # ------------------------------------------------------------ staff PC

    def _admin_target(self) -> Tuple[Optional[str], str]:
        """(admin device id, '') or (None, why not)."""
        import sync_admin
        engine = self.engine
        with engine._conn("master") as conn:
            record = sync_admin.get_office_admin(conn, engine.admin_pubkey)
        dev = (record or {}).get("device_id")
        if not dev:
            return None, "this PC does not know the admin PC yet"
        if dev == engine.device_id:
            return None, "this PC is recorded as the admin PC"
        return dev, ""

    def _park(self, reason: str, files: int, size: int) -> None:
        self._fails += 1
        self.stats["parked"] += 1
        self._set("parked", reason, unsent_files=files, unsent_bytes=size)
        self._say(("parked", reason), "Corpus parked",
                  f"{_plural(files, 'file')} ({_mb(size)}) kept on this PC: {reason}")

    def _send_unsent(self) -> None:
        folder = self.folder
        all_files = transfer.finished_files(folder, cap=False)
        size = sum(_size(p) for p in all_files)
        if not all_files:
            self._fails = 0
            self._set("idle", "", unsent_files=0, unsent_bytes=0)
            if self._last_key and self._last_key[0] in ("parked", "error"):
                self._last_key = None
            return
        admin, why = self._admin_target()
        if admin is None:
            self._park(why, len(all_files), size)
            return
        left = list(all_files)
        for _ in range(MAX_BATCHES):
            batch = transfer.finished_files(folder)
            if not batch:
                break
            try:
                session = self.engine._open_session_to(admin)
            except TransportError as exc:
                _log.info("SDIS excavator: admin PC not reachable (%s)", type(exc).__name__)
                self._park("the admin PC is not reachable (it may be off)",
                           len(left), sum(_size(p) for p in left))
                return
            self._say(("sending", len(batch)), "Corpus sending",
                      f"{_plural(len(batch), 'file')} ({_mb(sum(_size(p) for p in batch))}) to the admin PC")
            with session:
                done = set(transfer.push(session, batch, progress=self._progress))
            self.stats["sent"] += len(batch)
            self.stats["acked"] += len(done)
            for p in batch:
                if p.name in done:
                    try:
                        p.unlink()
                        self.stats["deleted"] += 1
                    except OSError:
                        pass
            batch_deleted = sum(1 for p in batch if p.name in done and not p.exists())
            if done:
                self._say(("sent", self.stats["acked"]), "Corpus sent to admin PC",
                          f"{len(done)} of {len(batch)} files acknowledged")
            if batch_deleted:
                self._say(("deleted", self.stats["deleted"]), "Corpus deleted on this PC",
                          f"{_plural(batch_deleted, 'file')} removed after the admin PC's ack")
            left = transfer.finished_files(folder, cap=False)
            if not done:
                self._park("the admin PC did not accept the files (refused or the link dropped)",
                           len(left), sum(_size(p) for p in left))
                return
        left = transfer.finished_files(folder, cap=False)
        if left:                                       # more than MAX_BATCHES batches: continue next pass
            self._set("sending", "", unsent_files=len(left), unsent_bytes=sum(_size(p) for p in left))
            self._wake.set()
            self._fails = 0
            return
        self._fails = 0
        self._set("idle", "", unsent_files=0, unsent_bytes=0)

    def _progress(self, i: int, n: int) -> None:
        self._say(("sending", i, n), "Corpus sending", f"file {i} of {n} sent, waiting for the admin PC's ack"
                  if i == n else f"file {i} of {n}")

    # ------------------------------------------------------------ admin PC

    def _readable(self, p: Path) -> bool:
        try:
            st = p.stat()
        except OSError:
            return False
        key = (str(p), st.st_size, st.st_mtime_ns)
        hit = self._checked.get(key)
        if hit is not None:
            return hit
        ok = True
        if st.st_size:
            try:
                import json
                with open(p, "rb") as f:
                    first = f.readline(FIRST_LINE_CAP)
                    f.seek(-1, 2)
                    last = f.read(1)
                if last != b"\n":
                    ok = False
                elif first.endswith(b"\n"):
                    json.loads(first)
            except (OSError, ValueError):
                ok = False
        self._checked[key] = ok
        return ok

    def _inspect_received(self) -> None:
        root = self.received_root
        files: List[Path] = []
        parts = 0
        devices = 0
        try:
            folders = [d for d in sorted(root.iterdir()) if d.is_dir()] if root.is_dir() else []
        except OSError:
            folders = []
        for d in folders:
            try:
                names = sorted(d.iterdir())
            except OSError:
                continue
            mine = [p for p in names if p.suffix == ".jsonl"]
            parts += sum(1 for p in names if p.name.endswith(".part"))
            if mine:
                devices += 1
            files.extend(mine)
        size = sum(_size(p) for p in files)
        bad = [p for p in files if not self._readable(p)]
        self._set("admin", "", received_files=len(files), received_bytes=size,
                  unsent_files=0, unsent_bytes=0)
        if not files and not parts:
            self._say(("admin", 0), "Corpus: admin PC", "no staff capture has arrived yet")
            return
        detail = f"{_plural(len(files), 'file')} ({_mb(size)}) from {_plural(devices, 'PC')}"
        if parts:
            detail += f"; {_plural(parts, 'half-received file')} waiting to be redone"
        if bad:
            detail += f"; {_plural(len(bad), 'file')} unreadable"
            self._set("admin", "unreadable files", received_files=len(files), received_bytes=size)
        self._say(("admin", len(files), size, parts, len(bad)),
                  "Corpus held on admin PC" if not bad else "Corpus held on admin PC - check", detail)


def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0
