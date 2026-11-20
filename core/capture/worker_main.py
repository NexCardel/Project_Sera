"""
core/capture/worker_main.py - the capture worker process (UCW, docs/ucw/ucw-plan.md)
====================================================================================
Started by Sera as the app's own exe with --capture-worker (main.py dispatches it before Qt or the
database), or `python main.py --capture-worker` from source. It does every UI Automation read of the
browser for SGT-C, the SDIS recorder and SGT-I's flash events: it hosts the reader (core/capture/reader:
one lane per window, content + scope threads, MTA, the wedge rules), SGT-I's FlashWatcher (WP6) and the
page-change listener (WP7), and talks to Sera in frames (core/capture/protocol) over stdin/stdout.

  * Requests are answered with the ReadContext Sera sent, unchanged; the worker reads with its own
    copy whose asked_at is on its own clock, so its age checks never mix two processes' clocks.
  * A beat every BEAT_SEC (with the reader's numbers since the last one) tells Sera it is alive. Sera
    kills and restarts a worker that stops beating; this process never pauses UIA for minutes: when the
    reader would pause (MAX_WEDGED_WORKERS reads stuck in the browser), it exits and is started fresh.
  * stdin closing (Sera gone) ends the process.

Nothing is written to disk but a crash's stack (logs/capture_worker_fault.log, which Sera copies into
hang.log). Notes for vsdc.log go to Sera as counts and times only, like every capture log.
"""

import dataclasses
import faulthandler
import os
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.capture import protocol

BEAT_SEC = 0.5
SWEEP_SEC = 0.25          # results() also sweeps the wedge rules: at least this often
FLASH_PUMP_SEC = 0.1
ORIGINALS_KEEP_SEC = 120.0
EXIT_PAUSED = 3           # the reader would have paused UIA: exit, Sera starts a fresh worker


def fault_log_path() -> Path:
    return Path.home() / "AmanAssociates_Sera" / "logs" / "capture_worker_fault.log"


class Worker:
    def __init__(self, inp: Any, out: Any, backend: Any = None, flash_factory: Optional[Callable[[], Any]] = None,
                 events_factory: Optional[Callable[..., Any]] = None, clock: Callable[[], float] = time.monotonic,
                 exit_fn: Callable[[int], None] = os._exit):
        from core.capture.reader import PageReader
        self._in, self._out = inp, out
        self._clock = clock
        self._exit = exit_fn
        self._send_lock = threading.Lock()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._originals: Dict[int, Any] = {}        # seq -> the ReadContext Sera sent
        self._orig_lock = threading.Lock()
        self.reader = PageReader(backend=backend, clock=clock, diag=self._note,
                                 on_dropped=self._dropped, on_result=self._wake.set)
        self._flash_factory = flash_factory
        self._flashes: Any = None
        self._flash_windows: Dict[int, str] = {}
        self._events_factory = events_factory
        self._events: Any = None

    # ---- sending (any thread) -----------------------------------------------------------------

    def send(self, msg: Any) -> None:
        try:
            with self._send_lock:
                protocol.write(self._out, msg)
        except Exception:
            self._stopping.set()                     # Sera's end is gone
            self._wake.set()

    def _note(self, text: str) -> None:
        self.send(("note", "capture worker: " + text))

    def _dropped(self, ctx: Any) -> None:
        with self._orig_lock:
            self._originals.pop(ctx.seq, None)
        self.send(("gone", ctx.seq))

    # ---- the request side (this process's main thread) ----------------------------------------

    def handle(self, msg: Any) -> bool:
        """One message from Sera. False = stop."""
        kind = msg[0]
        if kind == "request":
            ctx = msg[1]
            local = dataclasses.replace(ctx, asked_at=self._clock())
            with self._orig_lock:
                self._originals[ctx.seq] = (ctx, local.asked_at)
            status = self.reader.request(local)
            if status not in ("started", "queued"):
                # refused at once: Sera's loop hears it as an error result and asks again later
                from core.capture.reader import ReadResult
                t = self._clock()
                with self._orig_lock:
                    self._originals.pop(ctx.seq, None)
                self.send(("result", t, ReadResult(ctx, started_at=t, finished_at=t, error=status)))
        elif kind == "close_lane":
            self.reader.close_lane(msg[1])
        elif kind == "abandon":
            self.reader.abandon_slow_content(msg[1], msg[2])
        elif kind == "raw":
            job_id, hwnd = msg[1], msg[2]
            self.reader.request_raw(hwnd, lambda value, j=job_id: self.send(("raw", j, value)))
        elif kind == "flash_watch":
            self._flash_watch(msg[1], msg[2])
        elif kind == "flash_stop":
            self._flash_windows.clear()
            if self._flashes is not None:
                self._flashes.stop()
        elif kind == "watch_events":
            self._watch_events(list(msg[1]))
        elif kind == "echo":
            self.send(("echo", msg[1]))
        elif kind == "stop":
            return False
        return True

    def _flash_watch(self, hwnd: int, key: str) -> None:
        if self._flashes is None:
            if self._flash_factory is not None:
                self._flashes = self._flash_factory()
            else:
                from core.sgt_i.uia_events import FlashWatcher
                self._flashes = FlashWatcher(echo=self._note)
        self._flash_windows[hwnd] = key
        while len(self._flash_windows) > 8:
            self._flash_windows.pop(next(iter(self._flash_windows)))
        self._flashes.watch(hwnd, key)

    def _watch_events(self, hwnds: List[int]) -> None:
        if self._events is None:
            if not hwnds:
                return
            factory = self._events_factory
            if factory is None:
                from core.capture.page_events import PageEvents
                factory = PageEvents
            self._events = factory(lambda hwnd, kind: self.send(("event", hwnd, kind)), diag=self._note)
        self._events.watch(hwnds)

    # ---- the result side (its own thread) -----------------------------------------------------

    def _results_loop(self) -> None:
        while not self._stopping.is_set():
            self._wake.wait(SWEEP_SEC)
            self._wake.clear()
            try:
                results = self.reader.results()
            except Exception:
                results = []
            now = self._clock()
            for r in results:
                with self._orig_lock:
                    orig = self._originals.pop(r.ctx.seq, None)
                if orig is not None:
                    r.ctx = orig[0]
                self.send(("result", now, r))
            if self.reader.paused():
                self._note("UIA reads stuck in the browser - this worker exits and Sera starts a fresh one")
                self._stopping.set()
                self._exit(EXIT_PAUSED)
                return
            self._forget_old(now)

    def _forget_old(self, now: float) -> None:
        with self._orig_lock:
            old = [s for s, (_c, at) in self._originals.items() if now - at > ORIGINALS_KEEP_SEC]
            for s in old:
                self._originals.pop(s, None)

    def _beat_loop(self) -> None:
        last_beat = 0.0
        while not self._stopping.is_set():
            now = self._clock()
            if now - last_beat >= BEAT_SEC:
                last_beat = now
                try:
                    stats = self.reader.stats(reset=True)
                except Exception:
                    stats = {}
                self.send(("beat", now, stats))
            if self._flashes is not None:
                self._pump_flashes(now)
            self._stopping.wait(FLASH_PUMP_SEC)

    def _pump_flashes(self, now: float) -> None:
        for hwnd in list(self._flash_windows):
            try:
                caught = self._flashes.take(hwnd)
            except Exception:
                caught = []
            for f in caught:
                self.send(("flash", hwnd, f.kind, tuple(f.lines), max(0.0, now - f.ts)))

    # ---- running -------------------------------------------------------------------------------

    def serve(self, ready_ms: float = 0.0) -> int:
        self.send(("ready", os.getpid(), ready_ms))
        threads = [threading.Thread(target=self._results_loop, name="ucw-results", daemon=True),
                   threading.Thread(target=self._beat_loop, name="ucw-beat", daemon=True)]
        for t in threads:
            t.start()
        code = 0
        try:
            while not self._stopping.is_set():
                msg = protocol.read(self._in)
                if msg is None:                      # Sera closed the pipe: it is gone or stopping us
                    break
                if not self.handle(msg):
                    break
        except Exception as e:
            self.send(("crash", f"capture worker request loop: {type(e).__name__}\n{traceback.format_exc()}"))
            code = 1
        self._stopping.set()
        self._wake.set()
        try:
            self.reader.close()
        except Exception:
            pass
        try:
            with self._send_lock:
                self._out.close()
        except Exception:
            pass
        return code


def _uia_ready_ms() -> float:
    """How long making a UIA object takes in this process (on a throwaway MTA thread): the WP0 figure
    'start to UIA ready'. The lanes make their own on their own threads."""
    box: Dict[str, float] = {}

    def make() -> None:
        t0 = time.perf_counter()
        try:
            import comtypes
            import comtypes.client
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
            client = comtypes.client.GetModule("UIAutomationCore.dll")
            comtypes.client.CreateObject(client.CUIAutomation, interface=client.IUIAutomation)
        except Exception:
            pass
        box["ms"] = (time.perf_counter() - t0) * 1000.0

    t = threading.Thread(target=make, name="ucw-uia-probe", daemon=True)
    t.start()
    t.join(15.0)
    return box.get("ms", -1.0)


def _load(spec: str) -> Any:
    """'package.module:attr' -> the attribute (tests pass a fake backend this way)."""
    import importlib
    mod, _, attr = spec.partition(":")
    return getattr(importlib.import_module(mod), attr)


def _arg(argv: List[str], name: str) -> Optional[str]:
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def _pipes() -> tuple:
    """The frame pipes, and nothing else on fd 1: a stray print() anywhere in the worker would break
    a frame, so stdout's descriptor is kept for frames and fd 1 / sys.stdout go to the null device."""
    inp = os.fdopen(os.dup(0), "rb", buffering=0)
    out = os.fdopen(os.dup(1), "wb", buffering=0)
    null = os.open(os.devnull, os.O_RDWR)
    os.dup2(null, 1)
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    return inp, out


def run(argv: Optional[List[str]] = None) -> int:
    t0 = time.perf_counter()
    argv = list(sys.argv if argv is None else argv)
    inp, out = _pipes()
    try:
        path = fault_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fault = open(path, "a", encoding="utf-8")
        faulthandler.enable(file=fault, all_threads=True)
    except Exception:
        pass
    fake = _arg(argv, "--fake-backend")
    backend = _load(fake)() if fake else None
    flash = _arg(argv, "--fake-flashes")
    events = _arg(argv, "--fake-events")
    if backend is None:
        # comtypes' FIRST import CoInitializes the importing thread as single-threaded: let that be this
        # main thread (it only reads the pipe), never a lane's MTA thread.
        import comtypes  # noqa: F401
        _uia_ready_ms()
    worker = Worker(inp, out, backend=backend, flash_factory=_load(flash) if flash else None,
                    events_factory=_load(events) if events else None)
    return worker.serve(ready_ms=(time.perf_counter() - t0) * 1000.0)
