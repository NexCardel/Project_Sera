"""
core/screentime.py - when Sera was open on this PC
===================================================
One line in <Sera data>/screentime.txt each time Sera starts and each time it closes:

    2026-10-12 09:01:22 | FRONT-DESK-1 | STARTED
    2026-10-12 13:04:51 | FRONT-DESK-1 | CLOSED
    2026-10-12 17:30:00 | FRONT-DESK-1 | CLOSED (not a clean close - last seen)

The PC name is the first line of device_identity.txt (the hostname when there is none). While Sera
runs, a small file (screentime_alive.txt) is touched every minute. If Sera is killed, loses power or
crashes, the next start finds that file and closes the old session at the last time it was seen, so
a session is never left open and the day's total is not inflated.

Only times and the PC name are written; nothing about clients or pages.

Office view: each PC also publishes its last sessions under its own app_settings key,
`screentime:<device id>` (a short JSON list). app_settings is office-wide through Sera Sync, so every
PC's Settings > Screentime tab lists every PC's sessions without a new table or a schema change. A PC
writes only its own key, so there is nothing to merge. It is published at start, at close and every
PUBLISH_EVERY_S while running (that is the "last seen" the other PCs use to tell running from stale).
"""

import json
import socket
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

FILE_NAME = "screentime.txt"
ALIVE_NAME = "screentime_alive.txt"
HEARTBEAT_S = 60.0
PUBLISH_EVERY_S = 600.0                      # how often a running PC refreshes its "last seen" in the office
RUNNING_FRESH_S = 15 * 60.0                  # an open session seen within this long ago counts as running
KEY_PREFIX = "screentime:"
PUBLISH_SESSIONS = 80
MAX_BYTES = 2 * 1024 * 1024                  # the oldest half is dropped past this
TS = "%Y-%m-%d %H:%M:%S"
STARTED, CLOSED = "STARTED", "CLOSED"
UNCLEAN = " (not a clean close - last seen)"

_lock = threading.Lock()
_current: Dict[str, Optional[str]] = {"app_dir": None, "started": None}
_office: Dict[str, object] = {"db": None, "key": None, "last": 0.0}


def attach(db, device_key: Optional[str]) -> None:
    """Call once the database is open (and the sync device id is known, else None = the PC name)."""
    _office.update(db=db, key=device_key, last=0.0)


def _publish(now: datetime) -> None:
    db, app_dir = _office["db"], _current["app_dir"]
    if db is None or not app_dir:
        return
    try:
        pc = pc_name(app_dir)
        key = KEY_PREFIX + str(_office["key"] or pc)
        mine = [r for r in read_sessions(app_dir, _current["started"], limit=PUBLISH_SESSIONS) if r["pc"] == pc]
        payload = {"pc": pc, "seen": now.strftime(TS),
                   "s": [[r["started"], r["closed"], 1 if r["clean"] else 0] for r in mine]}
        db.set_setting(key, json.dumps(payload, separators=(",", ":")))
        _office["last"] = time.monotonic()
    except Exception:
        pass


def pc_name(app_dir) -> str:
    try:
        text = (Path(app_dir) / "device_identity.txt").read_text(encoding="utf-8").strip()
        if text:
            return text.splitlines()[0].strip().replace("|", "/")
    except (OSError, UnicodeError):
        pass
    return (socket.gethostname() or "this PC").replace("|", "/")


def _file(app_dir) -> Path:
    return Path(app_dir) / FILE_NAME


def _alive(app_dir) -> Path:
    return Path(app_dir) / ALIVE_NAME


def _append(app_dir, when: datetime, pc: str, what: str) -> None:
    path = _file(app_dir)
    try:
        if path.exists() and path.stat().st_size > MAX_BYTES:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            path.write_text("\n".join(lines[len(lines) // 2:]) + "\n", encoding="utf-8")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{when.strftime(TS)} | {pc} | {what}\n")
    except OSError:
        pass


def _touch_alive(app_dir, pc: str, started: str, now: datetime) -> None:
    try:
        _alive(app_dir).write_text(f"{pc}\n{started}\n{now.strftime(TS)}\n", encoding="utf-8")
    except OSError:
        pass


def start(app_dir, now: Optional[datetime] = None) -> None:
    """Call once when Sera has started. Closes a session an earlier run left open, then writes STARTED."""
    now = now or datetime.now()
    with _lock:
        pc = pc_name(app_dir)
        try:
            old = _alive(app_dir).read_text(encoding="utf-8").splitlines()
        except OSError:
            old = []
        if len(old) >= 3:
            try:
                seen = datetime.strptime(old[2], TS)
            except ValueError:
                seen = None
            if seen is not None:
                _append(app_dir, seen, old[0] or pc, CLOSED + UNCLEAN)
        _append(app_dir, now, pc, STARTED)
        _current.update(app_dir=str(app_dir), started=now.strftime(TS))
        _touch_alive(app_dir, pc, now.strftime(TS), now)
        _publish(now)


def heartbeat(now: Optional[datetime] = None) -> None:
    app_dir, started = _current["app_dir"], _current["started"]
    if not app_dir or not started:
        return
    now = now or datetime.now()
    with _lock:
        _touch_alive(app_dir, pc_name(app_dir), started, now)
        if time.monotonic() - float(_office["last"]) >= PUBLISH_EVERY_S:
            _publish(now)


def stop(now: Optional[datetime] = None) -> None:
    """Call when Sera closes. Writes CLOSED once and removes the alive file."""
    app_dir = _current["app_dir"]
    if not app_dir or not _current["started"]:
        return
    with _lock:
        now = now or datetime.now()
        _append(app_dir, now, pc_name(app_dir), CLOSED)
        try:
            _alive(app_dir).unlink()
        except OSError:
            pass
        _current["started"] = None
        _publish(now)


# ---------------------------------------------------------------- reading (the Screentime tab)

def read_sessions(app_dir, running_started: Optional[str] = None, limit: int = 500) -> List[dict]:
    """Newest first: {pc, started, closed, clean, running, seconds}. A STARTED with no CLOSED after
    it is `running` when it is this run's start, otherwise it has no end time (closed is empty)."""
    try:
        text = _file(app_dir).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    open_by_pc: Dict[str, datetime] = {}
    out: List[dict] = []
    for raw in text.splitlines():
        parts = [p.strip() for p in raw.split("|", 2)]
        if len(parts) != 3:
            continue
        try:
            when = datetime.strptime(parts[0], TS)
        except ValueError:
            continue
        pc, what = parts[1], parts[2]
        if what == STARTED:
            if pc in open_by_pc:                        # a start with no close before the next one
                out.append(_row(pc, open_by_pc.pop(pc), None, False, False))
            open_by_pc[pc] = when
        elif what.startswith(CLOSED):
            started = open_by_pc.pop(pc, None)
            out.append(_row(pc, started, when, what == CLOSED, False))
    for pc, started in open_by_pc.items():
        running = running_started is not None and started.strftime(TS) == running_started
        out.append(_row(pc, started, None, False, running))
    out.sort(key=lambda r: r["sort"], reverse=True)
    return out[:limit]


def _row(pc, started, closed, clean, running) -> dict:
    seconds = int((closed - started).total_seconds()) if started and closed else None
    return {"pc": pc, "started": started.strftime(TS) if started else "",
            "closed": closed.strftime(TS) if closed else "", "clean": clean, "running": running,
            "seconds": seconds, "sort": (started or closed or datetime.min)}


def current_start() -> Optional[str]:
    return _current["started"]


def duration_text(seconds: Optional[int]) -> str:
    if seconds is None:
        return ""
    h, rem = divmod(max(seconds, 0), 3600)
    m = rem // 60
    return f"{h} h {m:02d} min" if h else f"{m} min"


def file_path(app_dir) -> Path:
    return _file(app_dir)


def app_dir_now() -> Optional[Path]:
    return Path(_current["app_dir"]) if _current["app_dir"] else None


def read_office(db, now: Optional[datetime] = None, limit: int = 500) -> List[dict]:
    """Every PC's published sessions, newest first. Same row shape as read_sessions plus `stale`:
    an open session not seen for RUNNING_FRESH_S (the PC was switched off or killed; its next start
    closes the session). `running` = open and seen recently."""
    now = now or datetime.now()
    try:
        settings = db.get_all_settings()
    except Exception:
        return []
    out: List[dict] = []
    for key, value in settings.items():
        if not str(key).startswith(KEY_PREFIX):
            continue
        try:
            data = json.loads(value)
            pc = str(data.get("pc") or key[len(KEY_PREFIX):])
            seen = datetime.strptime(data["seen"], TS)
            sessions = data["s"]
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        fresh = (now - seen).total_seconds() <= RUNNING_FRESH_S
        for s in sessions:
            try:
                started, closed, clean = s[0], s[1], bool(s[2])
                t0 = datetime.strptime(started, TS)
                t1 = datetime.strptime(closed, TS) if closed else None
            except (ValueError, IndexError, TypeError):
                continue
            is_open = not closed
            row = _row(pc, t0, t1, clean, False)
            row["running"] = is_open and fresh and s is sessions[0]
            row["stale"] = is_open and not row["running"]
            row["seen"] = data["seen"]
            if is_open and fresh and s is sessions[0]:
                row["seconds"] = int((now - t0).total_seconds())
            out.append(row)
    out.sort(key=lambda r: r["sort"], reverse=True)
    return out[:limit]
