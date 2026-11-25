"""
core/capture/remote.py - Sera's side of the capture worker: RemoteReader and its supervisor (UCW)
=================================================================================================
docs/ucw/ucw-plan.md, WP2 (with WP3, WP5-WP8). RemoteReader is the translation layer: it offers exactly
the PageReader methods the capture loop uses (request, results, inflight, abandon_slow_content, page_ms,
paused, close_lane, stats, close) and hands back the same ReadContext / ReadResult objects, so SGT's
engine and the capture loop do not change. The reads themselves run in the capture worker process
(core/capture/worker_main), which hosts the real PageReader.

  * Nothing here waits on the worker on the capture thread: request() puts a frame on a queue that a
    writer thread sends; a reader thread turns the worker's frames into a deque that results() drains.
    inflight() and page_ms() answer from RemoteReader's own record of what it sent and got back.
  * Supervisor (its own thread): a worker that has not beaten for SILENCE_SEC, or has exited, is killed
    and a new one started; every request it still had comes back as error "worker-restart" (the loop
    asks again; never blind). RESTART_BURST restarts within RESTART_WINDOW_SEC: the next start waits
    RESTART_BACKOFF_SEC, and meanwhile paused() is True (OCR takes over) and restarting() is True
    (health state "uia-restarting"). Never off for the run.
  * Times: a result's started_at / finished_at are moved onto Sera's clock (worker clock + the gap
    between the worker's send time and Sera's receive time).

RemoteFlashes (WP6) is SGT-I's FlashWatcher interface over the same worker; read_raw (WP5) is the SDIS
recorder's raw-view read, queued on the window's lane behind any SGT page read.

Privacy: the pipe carries page text in memory only; logs are counts and times.
"""

import itertools
import os
import queue
import statistics
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from core.capture import protocol
from core.capture.reader import (REASON_SCOPE, RESULT_MAX_AGE_SEC, ReadContext, ReadResult)

SILENCE_SEC = 3.0             # no beat for this long: the worker is killed and replaced
START_GRACE_SEC = 20.0        # a new worker has this long to say "ready"
RESTART_BURST = 3             # this many restarts ...
RESTART_WINDOW_SEC = 60.0     # ... within this window ...
RESTART_BACKOFF_SEC = 30.0    # ... and the next start waits this long
SUPERVISE_SEC = 0.1
ECHO_EVERY_SEC = 10.0         # the pipe's own cost, measured by an echo round trip (WP8)
RAW_WAIT_SEC = 5.0            # the SDIS recorder waits this long for its raw read at most
EVENT_MIN_GAP_SEC = 0.3       # WP7: at most one event-driven URL read per window this often
READ_MS_KEPT = 200            # WP8: read times kept for p50 / p95 between heartbeats

REPO = Path(__file__).resolve().parents[2]


def worker_command() -> List[str]:
    """The app's own exe with --capture-worker (frozen), else this Python running main.py."""
    if getattr(sys, "frozen", False):
        return [sys.executable, protocol.FLAG]
    return [sys.executable, str(REPO / "main.py"), protocol.FLAG]


# ---- channels: how Sera talks to one worker --------------------------------------------------

class WorkerProcess:
    """One worker process: frames over its stdin/stdout, a writer thread and a reader thread.
    on_message(msg) runs on the reader thread; on_message(None) once when the pipe closes."""

    def __init__(self, command: Optional[List[str]] = None, on_message: Optional[Callable[[Any], None]] = None):
        self._on_message = on_message
        self._outbox: "queue.SimpleQueue" = queue.SimpleQueue()
        self._ready = threading.Event()
        self._echo: Dict[int, Tuple[threading.Event, list]] = {}
        self._echo_ids = itertools.count(1)
        self.ready_info: Tuple[int, float] = (0, 0.0)
        self.proc = subprocess.Popen(
            command or worker_command(), cwd=str(REPO), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, bufsize=0, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.pid = self.proc.pid
        threading.Thread(target=self._write_loop, name="ucw-send", daemon=True).start()
        threading.Thread(target=self._read_loop, name="ucw-recv", daemon=True).start()

    def send(self, msg: Any) -> None:
        self._outbox.put(msg)

    def alive(self) -> bool:
        return self.proc.poll() is None

    def exit_code(self) -> Optional[int]:
        return self.proc.poll()

    def kill(self) -> None:
        try:
            self.proc.kill()
        except Exception:
            pass
        self._outbox.put(None)

    def wait_ready(self, timeout: float) -> bool:
        return self._ready.wait(timeout)

    def echo_ms(self, payload: Any, timeout: float = 10.0) -> float:
        """Round trip of one frame carrying payload, in ms (blocking: for the probe and the
        supervisor's pipe check, never the capture thread). -1 when no answer came."""
        n = next(self._echo_ids)
        ev, box = threading.Event(), []
        self._echo[n] = (ev, box)
        t0 = time.perf_counter()
        self.send(("echo", (n, payload)))
        ok = ev.wait(timeout)
        self._echo.pop(n, None)
        return (time.perf_counter() - t0) * 1000.0 if ok else -1.0

    # The frame codec; core/capture/cs_worker.CsWorkerProcess overrides both for the C# worker's JSON frames.
    def _write_msg(self, stream: Any, msg: Any) -> None:
        protocol.write(stream, msg)

    def _read_msg(self, stream: Any) -> Any:
        return protocol.read(stream)

    def _write_loop(self) -> None:
        stream = self.proc.stdin
        while True:
            msg = self._outbox.get()
            if msg is None:
                break
            try:
                self._write_msg(stream, msg)
            except Exception:
                break
        try:
            stream.close()
        except Exception:
            pass

    def _read_loop(self) -> None:
        stream = self.proc.stdout
        while True:
            try:
                msg = self._read_msg(stream)
            except Exception:
                msg = None
            if msg is None:
                break
            if msg[0] == "ready":
                self.ready_info = (msg[1], msg[2])
                self._ready.set()
            elif msg[0] == "echo" and isinstance(msg[1], tuple) and msg[1][0] in self._echo:
                ev, box = self._echo[msg[1][0]]
                box.append(msg[1][1])
                ev.set()
                continue
            if self._on_message is not None:
                self._on_message(msg)
        if self._on_message is not None:
            self._on_message(None)


class ThreadChild:
    """A worker in this process, on a thread, over real OS pipes: the same frames and pickling as
    WorkerProcess, with a backend the caller controls (tests: the capture loop's suite runs on it)."""

    def __init__(self, backend: Any, on_message: Optional[Callable[[Any], None]] = None,
                 clock: Callable[[], float] = time.monotonic, flash_factory: Any = None, events_factory: Any = None):
        from core.capture.worker_main import Worker
        self._on_message = on_message
        r1, w1 = os.pipe()           # Sera -> worker
        r2, w2 = os.pipe()           # worker -> Sera
        self._to = os.fdopen(w1, "wb", buffering=0)
        self._from = os.fdopen(r2, "rb", buffering=0)
        self._dead = threading.Event()
        self.exit_codes: List[int] = []
        self._worker = Worker(os.fdopen(r1, "rb", buffering=0), os.fdopen(w2, "wb", buffering=0), backend=backend,
                              flash_factory=flash_factory, events_factory=events_factory, clock=clock,
                              exit_fn=self._exit)
        self._send_lock = threading.Lock()
        self._ready = threading.Event()
        self.pid = 0
        threading.Thread(target=self._serve, name="ucw-thread-child", daemon=True).start()
        threading.Thread(target=self._read_loop, name="ucw-recv", daemon=True).start()

    @property
    def reader(self) -> Any:
        return self._worker.reader

    def _exit(self, code: int) -> None:
        self.exit_codes.append(code)
        self.kill()

    def _serve(self) -> None:
        try:
            self._worker.serve()
        finally:
            self._dead.set()

    def send(self, msg: Any) -> None:
        if self._dead.is_set():
            return
        try:
            with self._send_lock:
                protocol.write(self._to, msg)
        except Exception:
            self._dead.set()

    def alive(self) -> bool:
        return not self._dead.is_set()

    def exit_code(self) -> Optional[int]:
        return None if self.alive() else (self.exit_codes[-1] if self.exit_codes else 0)

    def kill(self) -> None:
        self._dead.set()
        for s in (self._to,):
            try:
                s.close()
            except Exception:
                pass

    def wait_ready(self, timeout: float) -> bool:
        return self._ready.wait(timeout)

    def _read_loop(self) -> None:
        while True:
            try:
                msg = protocol.read(self._from)
            except Exception:
                msg = None
            if msg is None:
                break
            if msg[0] == "ready":
                self._ready.set()
            if self._dead.is_set():
                continue           # a killed child's late frames are not delivered, like a dead process's
            if self._on_message is not None:
                self._on_message(msg)
        self._dead.set()
        if self._on_message is not None:
            self._on_message(None)


# ---- RemoteReader ----------------------------------------------------------------------------

class _Sent:
    __slots__ = ("ctx", "at", "abandoned")

    def __init__(self, ctx: ReadContext, at: float):
        self.ctx, self.at, self.abandoned = ctx, at, False


_INTERVAL_SUMS = ("reads", "scope_reads", "late", "queued_replaced", "held", "paused_requests", "no_lane", "too_old")


class RemoteReader:
    """PageReader's interface, served by a capture worker process. spawn(on_message) makes a channel
    (WorkerProcess by default); clock is the capture loop's clock; real_clock times the supervisor."""

    def __init__(self, spawn: Optional[Callable[[Callable[[Any], None]], Any]] = None,
                 clock: Callable[[], float] = time.monotonic, real_clock: Callable[[], float] = time.monotonic,
                 diag: Optional[Callable[[str], None]] = None, seq: Optional[Callable[[], int]] = None,
                 crash_log: Optional[Callable[[str], None]] = None, supervise: bool = True):
        self._spawn = spawn or (lambda on_message: WorkerProcess(on_message=on_message))
        self._clock = clock
        self._real = real_clock
        self._diag = diag or _diag_note
        self._crash_log = crash_log or _hang_log
        self._seq = seq or itertools.count(10 ** 9).__next__
        self._lock = threading.RLock()
        self._chan: Any = None
        self._gen = 0                               # which worker a frame came from
        self._chan_started = 0.0                    # real clock
        self._ready = False
        self._last_beat = 0.0                       # real clock
        self._next_start = 0.0                      # real clock: 0 = start now
        self._restart_times: Deque[float] = deque()
        self._backoff = False
        self._closed = False
        self._sent: Dict[int, _Sent] = {}
        self._done: Deque[ReadResult] = deque()
        self._page_ms: Dict[int, Dict[str, float]] = {}
        self._last_ctx: Dict[int, ReadContext] = {}
        self._event_at: Dict[int, float] = {}
        self._watched: Tuple[int, ...] = ()
        self._raw: Dict[int, Tuple[threading.Event, list]] = {}
        self._raw_ids = itertools.count(1)
        self.flashes = RemoteFlashes(self)
        self._restarts = 0
        self._abandoned_before = 0                  # abandoned reads of earlier workers this run
        self._worker_abandoned = 0
        self._snap: Dict[str, Any] = {"stuck": 0, "lanes": 0}
        self._interval: Dict[str, Any] = {}
        self._read_ms: Deque[float] = deque(maxlen=READ_MS_KEPT)
        self._pipe_ms = 0.0
        self._echo_at = 0.0
        self._reset_interval()
        self._sup_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._start_worker()
        if supervise:
            self._sup_thread = threading.Thread(target=self._supervise_loop, name="ucw-supervisor", daemon=True)
            self._sup_thread.start()

    # ---- the capture thread's side (never blocks) ---------------------------------------------

    def request(self, ctx: ReadContext) -> str:
        with self._lock:
            if self._closed:
                return "no-lane"
            chan = self._chan
            if chan is None or not self._ready:
                self._interval["paused_requests"] += 1
                return "paused"
            self._sent[ctx.seq] = _Sent(ctx, self._clock())
            self._last_ctx[ctx.hwnd] = ctx
            new_window = ctx.hwnd not in self._watched
        chan.send(("request", ctx))
        if new_window:
            self._send_watch()
        return "queued"

    def results(self) -> List[ReadResult]:
        with self._lock:
            out = list(self._done)
            self._done.clear()
            now = self._clock()
        kept = []
        for r in out:
            age = now - (r.ctx.asked_at or r.started_at)
            if age > RESULT_MAX_AGE_SEC:
                with self._lock:
                    self._interval["too_old"] += 1
                self._diag_safe(f"reader dropped a result {age:.0f} s old ({r.error or 'ok'})")
                continue
            kept.append(r)
        return kept

    def inflight(self, hwnd: int) -> Optional[ReadContext]:
        with self._lock:
            mine = [s for s in self._sent.values()
                    if s.ctx.hwnd == hwnd and s.ctx.reason != REASON_SCOPE and not s.abandoned]
            return min(mine, key=lambda s: s.ctx.seq).ctx if mine else None

    def abandon_slow_content(self, hwnd: int, min_running_sec: float = 3.0) -> bool:
        with self._lock:
            if self._closed or self._chan is None or self._backoff:
                return False
            now = self._clock()
            mine = [s for s in self._sent.values()
                    if s.ctx.hwnd == hwnd and s.ctx.reason != REASON_SCOPE and not s.abandoned]
            if not mine:
                return False
            oldest = min(mine, key=lambda s: s.ctx.seq)
            if now - oldest.at < min_running_sec:
                return False
            oldest.abandoned = True
            chan = self._chan
        chan.send(("abandon", hwnd, min_running_sec))
        return True

    def page_ms(self, hwnd: int, url: str) -> float:
        with self._lock:
            return self._page_ms.get(hwnd, {}).get(url, 0.0)

    def paused(self) -> bool:
        """True while no worker can read (between a restart burst and the next start)."""
        with self._lock:
            return self._backoff

    def restarting(self) -> bool:
        """Health state "uia-restarting" (WP8)."""
        return self.paused()

    def close_lane(self, hwnd: int) -> None:
        with self._lock:
            self._page_ms.pop(hwnd, None)
            self._last_ctx.pop(hwnd, None)
            chan = self._chan
            changed = hwnd in self._watched
        if chan is not None:
            chan.send(("close_lane", hwnd))
        if changed:
            self._send_watch(drop=hwnd)

    def stats(self, reset: bool = False) -> Dict[str, Any]:
        with self._lock:
            out = dict(self._interval)
            out["max_ms"] = round(out["max_ms"])
            out["last_ms"] = round(out["last_ms"])
            out.update(abandoned=self._abandoned_before + self._worker_abandoned, stuck=self._snap.get("stuck", 0),
                       paused="yes" if self._backoff else None, lanes=self._snap.get("lanes", 0),
                       restarts=self._restarts, pipe_ms=round(self._pipe_ms, 2))
            if self._read_ms:
                ms = sorted(self._read_ms)
                out["read_p50"] = round(statistics.median(ms))
                out["read_p95"] = round(ms[min(len(ms) - 1, int(len(ms) * 0.95))])
            if reset:
                self._reset_interval(keep_last=self._interval["last_ms"])
                self._read_ms.clear()
            return out

    def close(self) -> None:
        with self._lock:
            self._closed = True
            chan, self._chan = self._chan, None
            raws = list(self._raw.values())
            self._raw.clear()
        self._stop.set()
        for ev, box in raws:
            box.append({"error": "closed"})
            ev.set()
        if chan is not None:
            chan.send(("stop",))
            threading.Timer(1.0, chan.kill).start()

    # ---- SDIS (WP5): the recorder's raw read, on its own thread -------------------------------

    def read_raw(self, hwnd: int, timeout: float = RAW_WAIT_SEC) -> Dict[str, Any]:
        """{"docs": [...], "ms": the read's own time} or {"docs": [], "error": ...}. Blocks the
        caller (the SDIS recorder's thread), never the capture thread."""
        with self._lock:
            chan = self._chan if self._ready and not self._closed else None
            if chan is None:
                return {"docs": [], "error": "no-worker"}
            job = next(self._raw_ids)
            ev, box = threading.Event(), []
            self._raw[job] = (ev, box)
        chan.send(("raw", job, hwnd))
        ev.wait(timeout)
        with self._lock:
            self._raw.pop(job, None)
        value = box[0] if box else {"error": "timeout"}
        value.setdefault("docs", [])
        return value

    # ---- frames from the worker (reader thread) -----------------------------------------------

    def _on_message(self, gen: int, msg: Any) -> None:
        with self._lock:
            if gen != self._gen:
                return                              # a replaced worker's late frame
            if msg is None:
                self._chan_closed = True
                return
            kind = msg[0]
            if kind == "ready":
                self._ready = True
                self._last_beat = self._real()
            elif kind == "beat":
                self._last_beat = self._real()
                self._take_stats(msg[2] or {})
            elif kind == "result":
                self._take_result(msg[1], msg[2])
            elif kind == "gone":
                self._sent.pop(msg[1], None)
            elif kind == "raw":
                slot = self._raw.get(msg[1])
                if slot is not None:
                    slot[1].append(dict(msg[2] or {}))
                    slot[0].set()
            elif kind == "flash":
                self.flashes._caught(msg[1], msg[2], msg[3], self._clock() - float(msg[4] or 0.0))
            elif kind == "event":
                self._on_event(msg[1], msg[2])
        if msg is not None and msg[0] == "note":
            self._diag_safe(str(msg[1]))
        elif msg is not None and msg[0] == "crash":
            self._crash_safe(str(msg[1]))

    def _take_result(self, worker_now: float, r: ReadResult) -> None:
        delta = self._clock() - float(worker_now)
        r.started_at += delta
        r.finished_at += delta
        self._sent.pop(r.ctx.seq, None)
        if not r.url_only and not r.error and r.ctx.reason != "sdis-raw":
            pages = self._page_ms.setdefault(r.ctx.hwnd, {})
            if len(pages) >= 50 and r.ctx.url not in pages:
                pages.pop(next(iter(pages)))
            pages[r.ctx.url] = r.ms
            self._read_ms.append(r.ms)
        self._done.append(r)

    def _take_stats(self, st: Dict[str, Any]) -> None:
        for k in _INTERVAL_SUMS:
            self._interval[k] += int(st.get(k, 0) or 0)
        self._interval["max_ms"] = max(self._interval["max_ms"], float(st.get("max_ms", 0) or 0))
        if st.get("reads"):
            self._interval["last_ms"] = float(st.get("last_ms", 0) or 0)
        self._worker_abandoned = int(st.get("abandoned", 0) or 0)
        self._snap = {"stuck": int(st.get("stuck", 0) or 0), "lanes": int(st.get("lanes", 0) or 0)}

    def _reset_interval(self, keep_last: float = 0.0) -> None:
        self._interval = {k: 0 for k in _INTERVAL_SUMS}
        self._interval.update(max_ms=0.0, last_ms=keep_last)

    # ---- WP7: a page changed - ask for its URL now, not at the next poll ------------------------

    def _on_event(self, hwnd: int, kind: str) -> None:
        """Inside the lock. A URL-only read with the window's last context, at most once per
        EVENT_MIN_GAP_SEC per window and never while one is outstanding; the loop learns the new
        URL from its result like any other and starts its burst of page reads."""
        last = self._last_ctx.get(hwnd)
        if last is None or self._chan is None or not self._ready or self._closed:
            return
        now = self._clock()
        if now - self._event_at.get(hwnd, -1e9) < EVENT_MIN_GAP_SEC:
            return
        if any(s.ctx.hwnd == hwnd and s.ctx.reason == REASON_SCOPE for s in self._sent.values()):
            return
        self._event_at[hwnd] = now
        ctx = ReadContext(seq=self._seq(), hwnd=hwnd, title=last.title, portal=last.portal, url=last.url,
                          url_source=last.url_source, tab=last.tab, asked_at=now, reason=REASON_SCOPE,
                          background=last.background)
        self._sent[ctx.seq] = _Sent(ctx, now)
        self._interval.setdefault("event_reads", 0)
        self._interval["event_reads"] += 1
        self._chan.send(("request", ctx))

    def _send_watch(self, drop: Optional[int] = None) -> None:
        """The windows the worker listens to for page changes: the last few asked about."""
        with self._lock:
            chan = self._chan
            hwnds = tuple(h for h in self._last_ctx if h != drop)[-4:]
            if chan is None or hwnds == self._watched:
                return
            self._watched = hwnds
        chan.send(("watch_events", hwnds))

    # ---- the supervisor (its own thread) -------------------------------------------------------

    def _start_worker(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._gen += 1
            gen = self._gen
            self._ready = False
            self._chan_closed = False
            self._chan_started = self._real()
        try:
            chan = self._spawn(lambda msg, g=gen: self._on_message(g, msg))
        except Exception as e:
            self._diag_safe(f"capture worker could not start: {type(e).__name__}")
            chan = None
        with self._lock:
            if gen != self._gen or self._closed:
                if chan is not None:
                    chan.kill()
                return
            self._chan = chan
            self._backoff = False
            self._watched = ()
            flash_windows = dict(self.flashes._keys)
        if chan is None:
            self._schedule_restart("could not start")
            return
        # a fresh worker knows nothing: SGT-I's flash windows and the page-event windows again
        for hwnd, key in flash_windows.items():
            chan.send(("flash_watch", hwnd, key))
        self._send_watch()

    def _supervise_loop(self) -> None:
        while not self._stop.wait(SUPERVISE_SEC):
            try:
                self.supervise_once()
            except Exception:
                pass

    def supervise_once(self) -> None:
        """One supervisor check (the supervisor thread calls it every SUPERVISE_SEC; tests call it)."""
        now = self._real()
        with self._lock:
            if self._closed:
                return
            chan = self._chan
            due = self._next_start and now >= self._next_start
        if chan is None:
            if due or not self._next_start:
                with self._lock:
                    self._next_start = 0.0
                self._start_worker()
            return
        why = ""
        with self._lock:
            ready, last_beat, started, closed = self._ready, self._last_beat, self._chan_started, self._chan_closed
        if not chan.alive() or closed:
            code = chan.exit_code() if hasattr(chan, "exit_code") else None
            why = f"exited ({code})" if code is not None else "exited"
        elif not ready and now - started > START_GRACE_SEC:
            why = f"never ready in {START_GRACE_SEC:.0f} s"
        elif ready and now - last_beat > SILENCE_SEC:
            why = f"silent for {now - last_beat:.1f} s"
        if why:
            self._restart(why)
            return
        if ready and now - self._echo_at >= ECHO_EVERY_SEC and hasattr(chan, "echo_ms"):
            self._echo_at = now
            threading.Thread(target=self._measure_pipe, args=(chan,), name="ucw-echo", daemon=True).start()

    def _measure_pipe(self, chan: Any) -> None:
        ms = chan.echo_ms(None, timeout=5.0)
        if ms >= 0:
            with self._lock:
                self._pipe_ms = ms / 2.0

    def _restart(self, why: str) -> None:
        with self._lock:
            chan, self._chan = self._chan, None
            self._gen += 1                           # its late frames are ignored from now
            self._ready = False
            failed = list(self._sent.values())
            self._sent.clear()
            raws = list(self._raw.values())
            self._raw.clear()
            t = self._clock()
            for s in failed:
                self._done.append(ReadResult(s.ctx, started_at=t, finished_at=t, error="worker-restart"))
            self._restarts += 1
            self._abandoned_before += self._worker_abandoned
            self._worker_abandoned = 0
            self._snap = {"stuck": 0, "lanes": 0}
        for ev, box in raws:
            box.append({"error": "worker-restart"})
            ev.set()
        if chan is not None:
            try:
                chan.kill()
            except Exception:
                pass
        self._copy_fault_log()
        self._diag_safe(f"capture worker {why} - restarted ({len(failed)} read(s) asked again, "
                        f"{self._restarts} restart(s) this run)")
        self._schedule_restart(why)

    def _schedule_restart(self, why: str) -> None:
        now = self._real()
        with self._lock:
            self._restart_times.append(now)
            while self._restart_times and now - self._restart_times[0] > RESTART_WINDOW_SEC:
                self._restart_times.popleft()
            if len(self._restart_times) >= RESTART_BURST:
                self._restart_times.clear()
                self._backoff = True
                self._next_start = now + RESTART_BACKOFF_SEC
                backoff = True
            else:
                self._next_start = 0.0
                backoff = False
        if backoff:
            self._diag_safe(f"capture worker restarted {RESTART_BURST} times in {RESTART_WINDOW_SEC:.0f} s - "
                            f"next start in {RESTART_BACKOFF_SEC:.0f} s (health uia-restarting, OCR still works)")

    def _copy_fault_log(self) -> None:
        """A worker that died hard (a crash inside COM) left its stack in capture_worker_fault.log:
        it goes to hang.log, where every stall's stacks are (WP8)."""
        try:
            from core.capture.worker_main import fault_log_path
            path = fault_log_path()
            if path.exists() and path.stat().st_size > 0:
                text = path.read_text(encoding="utf-8", errors="replace")[-20000:]
                path.write_text("", encoding="utf-8")
                self._crash_safe("capture worker died - its stack:\n" + text)
        except Exception:
            pass

    def _diag_safe(self, text: str) -> None:
        try:
            self._diag(text)
        except Exception:
            pass

    def _crash_safe(self, text: str) -> None:
        try:
            self._crash_log(text)
        except Exception:
            pass


class RemoteFlashes:
    """SGT-I's FlashWatcher interface (watch, take, pending, stop, disabled, caught) over the capture
    worker, which hosts the real FlashWatcher (WP6). Never blocks, never raises."""

    def __init__(self, reader: RemoteReader):
        self._reader = reader
        self._lock = threading.Lock()
        self._keys: Dict[int, str] = {}
        self._pending: Dict[int, Deque[Any]] = {}
        self.disabled: Optional[str] = None
        self.caught = 0

    def watch(self, hwnd: int, key: str = "") -> None:
        try:
            if not hwnd:
                return
            with self._lock:
                if self._keys.get(hwnd) == key:
                    return
                self._keys[hwnd] = key
                while len(self._keys) > 4:
                    old = next(iter(self._keys))
                    self._keys.pop(old)
                    self._pending.pop(old, None)
            chan = self._reader._chan
            if chan is not None:
                chan.send(("flash_watch", hwnd, key))
        except Exception:
            pass

    def take(self, hwnd: int) -> List[Any]:
        from core.sgt_i.uia_events import FLASH_TTL_SEC
        with self._lock:
            got = self._pending.pop(hwnd, None)
        if not got:
            return []
        cutoff = self._reader._clock() - FLASH_TTL_SEC
        return [f for f in got if f.ts >= cutoff]

    def pending(self, hwnd: int) -> bool:
        with self._lock:
            return bool(self._pending.get(hwnd))

    def stop(self) -> None:
        with self._lock:
            self._keys.clear()
            self._pending.clear()
        chan = self._reader._chan
        if chan is not None:
            chan.send(("flash_stop",))

    def _caught(self, hwnd: int, kind: str, lines: Tuple[str, ...], ts: float) -> None:
        from core.sgt_i.uia_events import MAX_PENDING, Flash
        with self._lock:
            if hwnd not in self._keys:
                return
            q = self._pending.setdefault(hwnd, deque(maxlen=MAX_PENDING))
            q.append(Flash(kind, tuple(lines), ts))
            self.caught += 1


def _diag_note(text: str) -> None:
    from core.vsdc import vsdc_diag
    vsdc_diag.note(text)


def _hang_log(text: str) -> None:
    from datetime import datetime
    from core import hang_watchdog
    hang_watchdog._append(f"\n==== {datetime.now():%Y-%m-%d %H:%M:%S}  {text.splitlines()[0]} ====\n"
                          + "\n".join(text.splitlines()[1:]) + "\n")
