"""
core/sdis/transfer.py - captures travel to the admin PC (Part P, D14)
=====================================================================
A staff PC pushes its finished recorder files (sdis_YYYY-MM-DD.jsonl, never today's open file;
core/sdis/excavator.py decides when) to the admin PC over Sera Sync's mutual-TLS transport (port 49159):

    staff -> admin   {t: 'sdis_push', files: [{name, size, sha256}, ...]}
    staff -> admin   each file as chunk frames (Session.send_file), in manifest order
    admin -> staff   {t: 'sdis_ack', files: [name, ...]}

The admin PC stores each file under data_dir()/corpus/<peer device id>/<name> and acks only the
files whose SHA-256 matches the manifest; the staff PC deletes exactly the acked files. A PC that
is not the admin PC acks nothing. On the admin PC itself mine() reads the recorder folder directly
(no push). Logs say counts only, never names of clients or contents.
"""

import hashlib
import logging
import os
import re
import time
from datetime import date
from pathlib import Path
from typing import List, Optional

from sync_transport import TransportError

_log = logging.getLogger(__name__)

FRAME_PUSH = "sdis_push"
FRAME_ACK = "sdis_ack"
MAX_FILES = 200
MAX_BATCH_BYTES = 200 * 1024 * 1024     # one session must finish inside the transport's 600 s deadline
SETTLE_S = 60.0                          # a file written to in the last minute is not finished yet
ACK_WAIT_S = 120.0

_NAME = re.compile(r"^sdis_(\d{4}-\d{2}-\d{2})\.jsonl$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_DEVICE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def corpus_dir(device_id: str) -> Path:
    from core.sdis.paths import data_dir
    return data_dir() / "corpus" / device_id


def finished_files(folder: Path, today: Optional[date] = None, now: Optional[float] = None,
                   cap: bool = True) -> List[Path]:
    """The recorder files of past days, oldest first, at most MAX_FILES / MAX_BATCH_BYTES (always
    at least one) unless cap is False. Today's file and anything written to in the last SETTLE_S
    are left alone."""
    today_s = (today or date.today()).isoformat()
    now = time.time() if now is None else now
    out, total = [], 0
    try:
        paths = sorted(Path(folder).glob("sdis_*.jsonl"))
    except OSError:
        return []
    for p in paths:
        m = _NAME.match(p.name)
        if not m or m.group(1) >= today_s:
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        if now - st.st_mtime < SETTLE_S:
            continue
        if cap and out and (len(out) >= MAX_FILES or total + st.st_size > MAX_BATCH_BYTES):
            break
        out.append(p)
        total += st.st_size
    return out


# ---------------------------------------------------------------- staff side (driven by core/sdis/excavator.py)

def push(session, files, progress=None) -> List[str]:
    """Sends the manifest and the files, reads the admin's ack and returns the acked names (only
    names that were in the manifest). [] when the admin refuses or the session breaks.
    progress(i, n) is called after each file is sent."""
    entries, paths, seen = [], [], set()
    for p in files:
        p = Path(p)
        if p.name in seen:
            continue
        seen.add(p.name)
        entries.append({"name": p.name, "size": p.stat().st_size, "sha256": _sha256(p)})
        paths.append(p)
    session.send({"t": FRAME_PUSH, "files": entries})
    try:
        for i, p in enumerate(paths, 1):
            session.send_file(p)
            if progress is not None:
                try:
                    progress(i, len(paths))
                except Exception:
                    pass
    except TransportError:
        pass                    # a refusing receiver may close early; its ack can still be waiting
    except OSError:
        return []               # a file vanished after the manifest: the stream is broken
    try:
        ack = session.recv(wait=ACK_WAIT_S)
    except TransportError:
        return []
    if ack.get("t") != FRAME_ACK or not isinstance(ack.get("files"), list):
        return []
    return [n for n in ack["files"] if isinstance(n, str) and n in seen]


# ---------------------------------------------------------------- admin side

def _valid_manifest(entries) -> bool:
    if not isinstance(entries, list) or len(entries) > MAX_FILES:
        return False
    names = set()
    for e in entries:
        if not isinstance(e, dict):
            return False
        name, size, sha = e.get("name"), e.get("size"), e.get("sha256")
        if not isinstance(name, str) or not _NAME.match(name) or name in names:
            return False
        if type(size) is not int or size < 0 or not isinstance(sha, str) or not _HEX64.match(sha):
            return False
        names.add(name)
    return True


def handle_push(session, app_dir) -> List[str]:
    """Admin side of one 'sdis_push' session (routed by sync_office.dispatch_session). Returns the
    acked names. A PC that is not the admin PC, or a bad manifest, gets an empty ack."""
    import sync_admin
    frame = session.recv()
    entries = frame.get("files")
    device = session.peer_device_id
    if (not sync_admin.is_admin_pc(app_dir) or not _valid_manifest(entries)
            or not isinstance(device, str) or not _DEVICE.match(device)):
        session.send({"t": FRAME_ACK, "files": []})
        _log.info("SDIS push from %s refused", str(device)[:8])
        return []
    dest = corpus_dir(device)
    dest.mkdir(parents=True, exist_ok=True)
    acked = []
    for e in entries:
        part = dest / (e["name"] + ".part")
        try:
            part.unlink()
        except FileNotFoundError:
            pass
        digest = session.recv_file(part, e["size"])
        if digest == e["sha256"]:
            os.replace(part, dest / e["name"])
            acked.append(e["name"])
        else:
            part.unlink()
    session.send({"t": FRAME_ACK, "files": acked})
    _log.info("SDIS push from %s: %d files, %d acked", device[:8], len(entries), len(acked))
    return acked
