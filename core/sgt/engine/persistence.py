"""
core/sgt/engine/persistence.py - crash safety
=============================================
Open and parked sessions are snapshotted to sessions_state.json as they change; after a crash the
next start-up finishes them and re-writes their datasets.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Any

from .policy import RESUME_WINDOW_SEC
from .session import RESTORED, client_value, session_from_json, session_to_json


class CrashSafety:
    """The crash snapshot and its recovery (part of SgtEngine)."""

    # ── Crash safety ─────────────────────────────────────────────────────────────
    STATE_SAVE_EVERY_SEC = 2.0

    def _save_state(self, force: bool = False) -> None:
        """Snapshots every open session (atomic replace), at most every couple of seconds."""
        if self._state_path is None:
            return
        now = self._clock()
        if not force and now - self._state_saved < self.STATE_SAVE_EVERY_SEC:
            self._state_dirty = True        # written on the next tick, even if that page has not changed
            return
        self._state_saved = now
        self._state_dirty = False
        try:
            live = [s for s in self._all_sessions() if s.has_content]
            # Parked clients (6.4): their age is written as the wall-clock moment they were parked and
            # the idle gap they had then, because this process's clock means nothing after a restart.
            wall = time.time()
            parked = [{"hwnd": h, "parked_wall": wall - (now - at), "gap": max(0.0, at - float(snap.get("last_seen") or at)),
                       "snap": snap}
                      for h, lst in self._parked.items() for at, snap in lst
                      if now - at <= RESUME_WINDOW_SEC]
            prev = self._state_path.with_suffix(".prev")
            if not live and not parked:
                for f in (self._state_path, prev):
                    if f.exists():
                        f.unlink()
                return
            snap = {"saved": datetime.now().isoformat(timespec="seconds"),
                    "sessions": [session_to_json(s) for s in live], "parked": parked}
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(json.dumps(snap, ensure_ascii=False))
                f.flush()
                os.fsync(f.fileno())        # on disk before it replaces the good copy: a power cut leaves a whole file
            if self._state_path.exists():
                self._state_path.replace(prev)   # the last good snapshot is kept one generation back
            tmp.replace(self._state_path)
        except Exception as e:
            self._echo(f"[SGT] {self._tag} could not snapshot sessions: {e}")

    def _recover(self) -> None:
        """Sessions the app never ended (it crashed or was killed): finish them now and re-write
        their datasets - a dataset key is stable, so a row already saved is simply updated."""
        prev = self._state_path.with_suffix(".prev")
        snap = None
        for f in (self._state_path, prev):      # the newest whole snapshot wins; a torn one is set aside, not lost
            if not f.exists():
                continue
            try:
                snap = json.loads(f.read_text(encoding="utf-8"))
                if not isinstance(snap, dict):
                    raise ValueError("not a snapshot")
                break
            except Exception as e:
                snap = None
                self._echo(f"[SGT] {self._tag} crash snapshot {f.name} unreadable: {e}")
                try:
                    f.replace(f.with_suffix(".corrupt"))
                except OSError:
                    pass
        if snap is None:
            return
        n = 0
        for raw in snap.get("sessions") or []:
            try:
                s = session_from_json(raw)
            except Exception as e:
                self._echo(f"[SGT] {self._tag} could not recover a session: {e}")
                continue
            self._finish(s, "recovered - the app stopped without ending it")
            self._queue_all(s, every=True)
            n += 1
        restored = self._restore_parked(snap.get("parked"))
        for f in (self._state_path, prev):
            try:
                f.unlink()
            except OSError:
                pass
        if restored:
            self._save_state(force=True)
        if n:
            self._echo(f"[SGT] {self._tag} recovered {n} session(s) the app did not end; their datasets are re-written")

    def _restore_parked(self, raw: Any) -> int:
        """Parked clients from the last run that are still inside RESUME_WINDOW_SEC (not finished: they
        ended when they were parked). Their window is gone, so they are resumable only by client."""
        n = 0
        now, wall = self._clock(), time.time()
        for e in raw if isinstance(raw, list) else []:
            try:
                snap = dict(e["snap"])
                age = max(0.0, wall - float(e["parked_wall"]))
                if age > RESUME_WINDOW_SEC or not client_value(snap):
                    continue
                snap["last_seen"] = now - age - max(0.0, float(e.get("gap") or 0.0))
                snap[RESTORED] = True
                self._parked.setdefault(int(e["hwnd"]), []).append((now - age, snap))
                n += 1
            except Exception as ex:
                self._echo(f"[SGT] {self._tag} could not restore a parked session: {type(ex).__name__}")
        if n:
            self._echo(f"[SGT] {self._tag} {n} parked session(s) restored from the last run")
        return n
