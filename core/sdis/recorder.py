"""
core/sdis/recorder.py - SDIS's own recorder (Part K)
=====================================================
SGT hands every page its change gate let through (and only after SGT's own read) to offer().
offer() puts ONE job in a size-1 slot - a newer page replaces one not taken yet - and returns at
once; it never blocks and never raises. A daemon thread takes the job and reads the page's RAW-view
node tree (parents, screen boxes) with its OWN UIA object, never vsdc_uia_text's shared worker, so
SGT's reads can neither wait for it nor see it as a hung read.

A read over the budget (D13: 1.5 s) or with no nodes is dropped, never retried. Else one JSON line

    {v: 1, ts, started, session, portal, url, link, title, browser, docs}

(started = the time of the session's first record in this run, so a session name sorts by time.)

is appended to sdis_YYYY-MM-DD.jsonl in the capture folder (<Sera data>/sdis_capture, NOT the
admin corpus). Past the size cap (D13: 500 MB) the oldest files are deleted, never the one being
written. The records hold client data: they stay on this PC until they reach the admin PC (rule 7).
Logs say counts and times only.
"""

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

RECORD_VERSION = 1
FILE_PREFIX = "sdis_"
FILE_SUFFIX = ".jsonl"
BUDGET_S = 1.5
CAP_BYTES = 500 * 1024 * 1024


def default_dir() -> Path:
    from core.vsdc.vsdc_alerts import SERA_DATA_DIR_NAME
    return Path.home() / SERA_DATA_DIR_NAME / "sdis_capture"


def browser_of(hwnd: int) -> str:
    """The window's process exe name without '.exe', lower case ('chrome', 'msedge', 'firefox');
    '' when it cannot be read. Same calls as vsdc_router.get_foreground_info."""
    import ctypes
    from ctypes import wintypes
    try:
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        kernel32 = ctypes.windll.kernel32
        hproc = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not hproc:
            return ""
        try:
            buff = ctypes.create_unicode_buffer(512)
            size = wintypes.DWORD(512)
            if not kernel32.QueryFullProcessImageNameW(hproc, 0, buff, ctypes.byref(size)):
                return ""
            name = buff.value.split("\\")[-1].lower()
        finally:
            kernel32.CloseHandle(hproc)
    except Exception:
        return ""
    return name[:-4] if name.endswith(".exe") else name


class SdisRecorder:
    def __init__(self, directory: Optional[Path] = None, enabled: bool = True, budget_s: float = BUDGET_S,
                 cap_bytes: int = CAP_BYTES, read_nodes: Optional[Callable[[int], Dict[str, Any]]] = None,
                 browser_of: Optional[Callable[[int], str]] = None) -> None:
        self.enabled = bool(enabled)
        self.budget_s = float(budget_s)
        self.cap_bytes = int(cap_bytes)
        self._directory = Path(directory) if directory is not None else None
        self._read_nodes = read_nodes           # None = this module's own UIA read (_own_read)
        self._browser_of = browser_of or globals()["browser_of"]
        self._uia: Optional[Tuple[Any, Any]] = None
        self._slot: Optional[Tuple[Any, ...]] = None
        # session -> its first record's time: session names sort by time (identity._by_time)
        self._started: Dict[str, str] = {}
        self._cond = threading.Condition()
        self._thread: Optional[threading.Thread] = None
        # counts only: written, dropped as slow, dropped as empty, replaced in the slot, failed
        self.stats = {"written": 0, "slow": 0, "empty": 0, "replaced": 0, "failed": 0}

    @property
    def directory(self) -> Path:
        if self._directory is None:
            self._directory = default_dir()
        return self._directory

    # ── SGT's side: never blocks, never raises ───────────────────────────────────
    def offer(self, hwnd: int, session: str, portal: str, url: str, title: str) -> None:
        try:
            if not self.enabled:
                return
            job = (hwnd, session, portal, url, title)
            with self._cond:
                if self._slot is not None:
                    self.stats["replaced"] += 1
                self._slot = job
                if self._thread is None:
                    self._start()
                self._cond.notify()
        except Exception:
            pass

    def _start(self) -> None:
        if self._read_nodes is None:
            # comtypes' FIRST import CoInitializes the importing thread as single-threaded; the
            # recorder's thread joins the multithreaded apartment itself (as core/sgt_i/uia_events).
            import comtypes  # noqa: F401
        self._thread = threading.Thread(target=self._run, name="sdis-recorder", daemon=True)
        self._thread.start()

    # ── The worker ───────────────────────────────────────────────────────────────
    def _run(self) -> None:
        while True:
            with self._cond:
                while self._slot is None:
                    self._cond.wait()
                job, self._slot = self._slot, None
            try:
                self._take(*job)
            except Exception:
                self.stats["failed"] += 1

    def _own_read(self, hwnd: int) -> Dict[str, Any]:
        if self._uia is None:
            import comtypes
            import comtypes.client
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
            client = comtypes.client.GetModule("UIAutomationCore.dll")
            try:        # CUIAutomation8 is the class that also answers IUIAutomation2..6 (Windows 8+)
                uia = comtypes.client.CreateObject(client.CUIAutomation8, interface=client.IUIAutomation)
            except Exception:
                uia = comtypes.client.CreateObject(client.CUIAutomation, interface=client.IUIAutomation)
            self._uia = (uia, client)
        from core.sgt_i.uia_nodes import read_page_nodes_here
        return read_page_nodes_here(self._uia[0], self._uia[1], hwnd, raw_view=True)

    def _take(self, hwnd: int, session: str, portal: str, url: str, title: str) -> Optional[Path]:
        t0 = time.perf_counter()
        try:
            read = self._read_nodes(hwnd) if self._read_nodes is not None else self._own_read(hwnd)
        except Exception:
            self.stats["failed"] += 1
            return None
        took = time.perf_counter() - t0
        if took > self.budget_s:
            self.stats["slow"] += 1                  # over budget: dropped, not retried (Part K)
            return None
        docs = (read or {}).get("docs") or []
        if not any(docs):
            self.stats["empty"] += 1
            return None
        from core.sdis.keys import page_link
        now = datetime.now()
        ts = now.isoformat(timespec="milliseconds")
        if session not in self._started:
            if len(self._started) >= 1000:
                self._started.clear()
            self._started[session] = ts
        rec = {"v": RECORD_VERSION, "ts": ts, "started": self._started[session], "session": session or "",
               "portal": portal or "", "url": url or "", "link": page_link(url) if url else "",
               "title": title or "", "browser": self._browser_of(hwnd) or "", "docs": docs}
        folder = self.directory
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{FILE_PREFIX}{now:%Y-%m-%d}{FILE_SUFFIX}"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.stats["written"] += 1
        self._cap(path)
        return path

    def _cap(self, keep: Path) -> None:
        """Deletes the oldest record files while the folder is over cap_bytes (never `keep`)."""
        files = []
        for p in self.directory.glob(f"{FILE_PREFIX}*{FILE_SUFFIX}"):
            try:
                files.append((p.name, p, p.stat().st_size))
            except OSError:
                pass
        total = sum(size for _n, _p, size in files)
        for _name, p, size in sorted(files):        # file names sort by day
            if total <= self.cap_bytes:
                break
            if p == keep:
                continue
            try:
                p.unlink()
                total -= size
            except OSError:
                pass
