"""
core/capture/protocol.py - frames between Sera and the capture worker (UCW, docs/ucw/ucw-plan.md)
================================================================================================
A frame is a 4-byte little-endian length and a pickled message. Both ends are Sera's own code (the
worker is the app's own exe with --capture-worker), so pickle is safe here; the pipe is the child's
stdin/stdout and never leaves the PC.

A message is a tuple whose first item is its kind:

  Sera -> worker                                   worker -> Sera
  ("request", ReadContext)                         ("ready", pid, ms_to_ready)
  ("close_lane", hwnd)                             ("beat", worker_now, stats_since_last_beat)
  ("abandon", hwnd, min_running_sec)               ("result", worker_now, ReadResult)
  ("raw", job_id, hwnd)          (SDIS, WP5)       ("gone", seq)            (a queued request replaced)
  ("flash_watch", hwnd, key)     (SGT-I, WP6)      ("raw", job_id, result_dict)
  ("flash_stop",)                                  ("flash", hwnd, kind, lines, age_sec)
  ("watch_events", hwnds)        (WP7)             ("event", hwnd, kind)
  ("echo", payload)              (the probe)       ("echo", payload)
  ("stop",)                                        ("note", text)           (a vsdc.log line, counts only)
                                                   ("crash", text)          (stack for hang.log)

Every result a worker sends carries the ReadContext Sera asked with, unchanged.
"""

import pickle
import struct
from typing import Any, BinaryIO, Optional

HEADER = struct.Struct("<I")
MAX_FRAME = 64 * 1024 * 1024          # far above the largest page seen (204 KB); a bigger one is a broken pipe

FLAG = "--capture-worker"


class ProtocolError(Exception):
    """The pipe carried something that is not a frame (or was cut mid-frame)."""


def encode(msg: Any) -> bytes:
    body = pickle.dumps(msg, protocol=pickle.HIGHEST_PROTOCOL)
    return HEADER.pack(len(body)) + body


def write(stream: BinaryIO, msg: Any) -> int:
    """One frame, flushed. Returns the bytes written."""
    data = encode(msg)
    stream.write(data)
    stream.flush()
    return len(data)


def _exactly(stream: BinaryIO, n: int) -> Optional[bytes]:
    chunks, got = [], 0
    while got < n:
        part = stream.read(n - got)
        if not part:
            if got:
                raise ProtocolError("pipe closed mid-frame")
            return None
        chunks.append(part)
        got += len(part)
    return b"".join(chunks)


def read(stream: BinaryIO) -> Any:
    """The next message, or None when the pipe closed cleanly between frames."""
    head = _exactly(stream, HEADER.size)
    if head is None:
        return None
    (size,) = HEADER.unpack(head)
    if size > MAX_FRAME:
        raise ProtocolError(f"frame of {size} bytes")
    body = _exactly(stream, size)
    if body is None:
        raise ProtocolError("pipe closed mid-frame")
    return pickle.loads(body)
