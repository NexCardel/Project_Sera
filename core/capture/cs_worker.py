"""
core/capture/cs_worker.py - the C# capture worker (native/capture_worker, SeraCaptureWorker.exe)
================================================================================================
The same capture worker as core/capture/worker_main.py - one lane per browser window, content + scope
threads, the wedge rules, SDIS raw reads, SGT-I's flash events - written in C#. The UIA work is the
expensive part of a read (a heavy page is thousands of cached property reads); in C# each is a direct COM
call instead of a comtypes call, and the worker is a 60 KB exe with no Python inside.

It is the default (setting capture_worker = "csharp"; "process" picks the Python worker). There is no
fallback to the Python worker: a C# worker that cannot start or dies is restarted by RemoteReader's
supervisor (with its backoff), and a missing exe is noted in vsdc.log.

CsWorkerProcess is a drop-in for remote.WorkerProcess: RemoteReader and its supervisor are unchanged.
Its frames are JSON instead of pickle (the C# side cannot read pickle); this class turns them back into
exactly the tuples, ReadContext and ReadResult objects the Python worker sends, so the capture loop,
SGT and the SDIS recorder cannot tell the two apart:
  * a request's ReadContext stays here (keyed by seq); the worker sends only the fields it reads with,
    and its result carries the seq, which picks the original ReadContext back up;
  * node "rect" / "grid" come back as tuples, as uia_nodes makes them.

Privacy: the pipe carries page text in memory only; nothing new is logged or written.
"""

import json
import os
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.capture import protocol
from core.capture.reader import ReadContext, ReadResult
from core.capture.remote import REPO, WorkerProcess

EXE_NAME = "SeraCaptureWorker.exe"
EXE_ENV = "SERA_CS_WORKER_EXE"
CTX_KEEP_SEC = 120.0          # a request whose result never came is forgotten after this long

_HEADER = struct.Struct("<I")


def exe_path() -> Optional[Path]:
    """The built worker: SERA_CS_WORKER_EXE, else next to the frozen app (or in its _internal folder), else
    native/capture_worker/bin in the repository. None when there is none."""
    override = os.environ.get(EXE_ENV, "").strip()
    candidates: List[Path] = [Path(override)] if override else []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / EXE_NAME)
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / EXE_NAME)
    candidates.append(REPO / "native" / "capture_worker" / "bin" / EXE_NAME)
    for p in candidates:
        if p.is_file():
            return p
    return None


def worker_command(exe: Optional[Path] = None) -> List[str]:
    from core.capture import scope
    exe = exe or exe_path()
    if exe is None:
        raise FileNotFoundError(EXE_NAME)
    # The admin's extra portal domains (vsdc_scope.json): the same file the Python worker would read.
    return [str(exe), "--scope-config", str(scope._config_path())]


def _node_hook(d: Dict[str, Any]) -> Dict[str, Any]:
    """json.loads object_hook: a node's rect and grid as tuples, as core/sgt_i/uia_nodes builds them."""
    rect = d.get("rect")
    if isinstance(rect, list):
        d["rect"] = tuple(rect)
    grid = d.get("grid")
    if isinstance(grid, list):
        d["grid"] = tuple(grid)
    return d


def encode(msg: Tuple) -> bytes:
    """A Sera -> worker message as a JSON frame (only the fields the worker reads)."""
    kind = msg[0]
    if kind == "request":
        ctx: ReadContext = msg[1]
        out: List[Any] = ["request", {"seq": ctx.seq, "hwnd": ctx.hwnd, "url": ctx.url or "",
                                      "reason": ctx.reason, "background": bool(ctx.background)}]
    elif kind == "watch_events":
        out = ["watch_events", list(msg[1])]
    elif kind == "echo":
        out = ["echo", list(msg[1]) if isinstance(msg[1], tuple) else msg[1]]
    else:
        out = list(msg)
    body = json.dumps(out, separators=(",", ":")).encode("utf-8")
    return _HEADER.pack(len(body)) + body


def read_frame(stream: Any) -> Any:
    """The next JSON frame as Python objects, or None when the pipe closed cleanly between frames."""
    head = protocol._exactly(stream, _HEADER.size)
    if head is None:
        return None
    (size,) = _HEADER.unpack(head)
    if size > protocol.MAX_FRAME:
        raise protocol.ProtocolError(f"frame of {size} bytes")
    body = protocol._exactly(stream, size)
    if body is None:
        raise protocol.ProtocolError("pipe closed mid-frame")
    return json.loads(body, object_hook=_node_hook)


class CsWorkerProcess(WorkerProcess):
    """remote.WorkerProcess for SeraCaptureWorker.exe: same methods, same messages out of on_message."""

    def __init__(self, command: Optional[List[str]] = None, on_message: Optional[Callable[[Any], None]] = None,
                 clock: Callable[[], float] = time.monotonic):
        # Set before the base class starts its threads.
        self._ctx_lock = threading.Lock()
        self._ctxs: Dict[int, Tuple[ReadContext, float]] = {}
        self._ctx_clock = clock
        super().__init__(command=command or worker_command(), on_message=on_message)

    def _write_msg(self, stream: Any, msg: Any) -> None:
        if msg[0] == "request":
            now = self._ctx_clock()
            with self._ctx_lock:
                self._ctxs[msg[1].seq] = (msg[1], now)
                old = [s for s, (_c, at) in self._ctxs.items() if now - at > CTX_KEEP_SEC]
                for s in old:
                    self._ctxs.pop(s, None)
        stream.write(encode(msg))
        stream.flush()

    def _read_msg(self, stream: Any) -> Any:
        while True:
            raw = read_frame(stream)
            if raw is None:
                return None
            msg = self._decode(raw)
            if msg is not None:
                return msg

    def _decode(self, m: List[Any]) -> Optional[Tuple]:
        kind = m[0]
        if kind == "result":
            return ("result", m[1], self._result(m[2]))
        if kind == "beat":
            return ("beat", m[1], m[2] or {})
        if kind == "raw":
            return ("raw", m[1], m[2] or {})
        if kind == "flash":
            return ("flash", m[1], m[2], tuple(m[3] or ()), m[4])
        if kind == "echo":
            payload = m[1] if len(m) > 1 else None
            return ("echo", tuple(payload) if isinstance(payload, list) else payload)
        if kind in ("ready", "gone", "event", "note", "crash"):
            # "gone": the context is kept - an abandoned read's late result still comes back for it
            return tuple(m)
        return None

    def _result(self, d: Dict[str, Any]) -> Optional[ReadResult]:
        seq = int(d.get("seq", 0))
        with self._ctx_lock:
            held = self._ctxs.pop(seq, None)
        ctx = held[0] if held is not None else ReadContext(seq=seq, hwnd=0)
        return ReadResult(
            ctx, source=d.get("source") or "uia", lines=list(d.get("lines") or []), docs=list(d.get("docs") or []),
            doc_urls=list(d.get("doc_urls") or []), tab_before=d.get("tab_before"), tab_after=d.get("tab_after"),
            url_before=d.get("url_before") or "", url_after=d.get("url_after") or "",
            started_at=float(d.get("started_at") or 0.0), finished_at=float(d.get("finished_at") or 0.0),
            error=d.get("error") or "", scope_rule=d.get("scope_rule") or "")


def spawn(on_message: Callable[[Any], None]) -> CsWorkerProcess:
    return CsWorkerProcess(on_message=on_message)
