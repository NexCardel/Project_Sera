"""
core/vsdc/vsdc_health.py - is capture working on this PC right now?
===================================================================
One HealthMonitor per router. It is fed small facts (a window in front and whether the scope gate
passed, a page read and whether it gave usable text, a watch-thread stall) and keeps ONE state:
ok, blind, stalled, ... When capture stops working for a reason the staff cannot see, the state
changes and three things happen: a short HUD event, a phone alert (vsdc_alerts) and a line in
logs/vsdc.log. The HUD stays momentary (never a persistent indicator): it shows on entering a
state, repeats at most every HUD_REPEAT_SEC while the state lasts, and says "back to normal" once.

The record (CaptureHealth) holds counts, times and short fixed sentences only: never page text,
values, names, PAN/GSTIN/ARN or links. It never raises into the capture loop.
"""

import json
import threading
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any, Callable, Dict, Optional

from . import vsdc_diag
from .vsdc_alerts import device_name, redact_identifiers

STATES = ("ok", "no-portal", "slow", "busy-long", "blind", "uia-paused", "stalled", "scope-lost",
          "specs-silent", "unlisted-portal", "disk", "alerts-unconfigured", "old-build")
# Highest priority first.
_PRIORITY = ("stalled", "uia-paused", "blind", "scope-lost", "busy-long", "slow", "specs-silent",
             "unlisted-portal", "disk", "alerts-unconfigured", "old-build", "no-portal", "ok")
# Only these raise a HUD event and a phone alert; the rest are shown in the record only.
FAILURE_STATES = frozenset({"stalled", "uia-paused", "blind", "scope-lost", "busy-long",
                            "specs-silent", "unlisted-portal", "disk"})
OWN_HUD = frozenset({"unlisted-portal"})     # the router already shows its own prompt for these

BLIND_ALERT_SEC = 20.0          # in-scope page, no usable text for this long (D8)
SLOW_ALERT_SEC = 60.0           # a read stays in flight past PAGE_TIMEOUT_SEC for this long
PAGE_TIMEOUT_SEC = 3.0          # vsdc_uia_text.DEFAULT_TIMEOUT_SEC
SPECS_SILENT_SEC = 300.0        # a logged-in session with zero profile and zero dataset hits for this long
SCOPE_LOST_SEC = 30.0           # a portal-titled window with no readable address for this long
NO_PORTAL_SEC = 600.0           # no in-scope window for this long: informational "no-portal"
DISK_HOLD_SEC = 600.0
UNLISTED_HOLD_SEC = 600.0       # the tripwire fires once per host; the state is shown this long
HEALTH_WRITE_SEC = 60.0
HUD_REPEAT_SEC = 300.0          # a persisting failure is shown again at most this often (constraint 7)
HUD_MIN_GAP_SEC = 60.0          # the same state re-entered (flapping) is shown at most this often
PHONE_REPEAT_SEC = 1800.0
ALERTS_NOTICE_SEC = 60.0        # "phone alerts not set up" is said once, after this long into the run
_WINDOW_FRESH_SEC = 5.0         # a window() call this recent means the window is still in front
_MAX_WINDOWS = 32

HUD_TITLE = "Capture paused on this PC"
HUD_BACK = "Capture back to normal"
SENTENCES = {
    "stalled": "Capture is stuck and not reading anything. Restart Sera if this stays.",
    "uia-paused": "Page reading is paused after repeated hangs. It retries by itself.",
    "blind": "Sera cannot read this page. Entries here may be missed.",
    "scope-lost": "Sera cannot confirm this window is the portal. Nothing is captured from it.",
    "busy-long": "The browser is taking very long to answer. Page reads are delayed.",
    "specs-silent": "Pages are read but nothing is recognised. The portal layout may have changed.",
    "unlisted-portal": "A portal address Sera does not know was seen. Capture is paused there.",
    "disk": "Sera could not write to disk. Captured rows may be at risk.",
    "alerts-unconfigured": "Phone alerts are not set up on this PC.",
}


@dataclass
class CaptureHealth:
    device: str
    version: str
    at: str                     # ISO time of the record
    state: str
    since: str                  # ISO time the state began
    detail: str                 # short, no client data, no links
    reads: int = 0
    blind: int = 0
    busy: int = 0
    late: int = 0
    abandoned: int = 0
    stuck: int = 0
    ocr_reads: int = 0
    ocr_only_rows: int = 0
    unattributed_rows: int = 0
    rows_sent: int = 0
    rows_dead_letter: int = 0
    blind_sec_today: int = 0
    stalled_sec_today: int = 0
    lanes: int = 0
    mem_ws_mb: int = 0
    pending_update: str = ""    # staged version or ""
    alerts_configured: bool = False
    scope_extras: int = 0
    spec_override: str = ""     # sha1[:8] of the override file or ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))


class _Win:
    """What the monitor knows about one browser window."""
    __slots__ = ("last_call", "scope_seen", "usable_at", "attempts", "title_only_since")

    def __init__(self) -> None:
        self.last_call = 0.0          # last window() call of any kind
        self.scope_seen = 0.0         # last window() call with scope_ok
        self.usable_at = 0.0          # last usable read, or the start of this stretch in scope
        self.attempts = 0             # reads tried since usable_at
        self.title_only_since = 0.0   # portal-titled but no readable address since (0 = no)


class HealthMonitor:
    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 hud: Optional[Callable[[str, str], None]] = None,
                 phone: Optional[Callable[[str, str, str], Any]] = None,
                 alerts_enabled: Optional[Callable[[], bool]] = None,
                 write: Optional[Callable[[CaptureHealth], None]] = None,
                 log: Callable[[str], None] = vsdc_diag.note,
                 wall: Callable[[], datetime] = datetime.now,
                 device: Optional[str] = None,
                 version: Optional[str] = None) -> None:
        """hud(title, subtitle) and phone(device, state, detail) are optional callables; write(record)
        is set by main.py (WP12), None until then."""
        self._clock, self._hud, self._phone, self._alerts_enabled = clock, hud, phone, alerts_enabled
        self._write, self._log, self._wall = write, log, wall
        self._device = device
        self._version = version
        self._lock = threading.RLock()
        t = clock()
        self._started = t
        self.state = "ok"
        self.since = self._iso()
        self._last_adv = t
        self._wins: Dict[int, _Win] = {}
        self._cur: Optional[int] = None          # the window() of this tick: read results belong to it
        self._last_in_scope = 0.0                # last window() with scope_ok (0 = never)
        self._stall_s = 0.0                      # the watch thread's last report; 0 = moving
        self._inflight_since = 0.0
        self._busy_since = 0.0
        self._last_ms = 0.0
        self._last_read_at = 0.0
        self._paused = False
        self._unlisted_at = 0.0
        self._disk_at = 0.0
        self._pending_update = ""
        self._sessions: Dict[str, list] = {}     # session id -> [first logged-in read, hit, last read]
        self._hud_at: Dict[str, float] = {}      # state -> when its HUD was last shown
        self._hud_shown: Optional[str] = None    # the failure whose HUD is on screen / was shown last
        self._phone_at: Dict[str, float] = {}
        self._alerts_notice_done = False
        self._day = self._wall().date()
        self._counts = {"reads": 0, "blind": 0, "busy": 0, "ocr_reads": 0, "ocr_only_rows": 0,
                        "unattributed_rows": 0, "rows_sent": 0, "rows_dead_letter": 0}
        self.blind_sec_today = 0.0
        self.stalled_sec_today = 0.0
        self._last_write = t
        self._changed = False

    # ── facts in ───────────────────────────────────────────────────────────────
    def window(self, hwnd: int, scope_ok: bool, portal: str = "", titled_portal: bool = False,
               url_readable: bool = True) -> None:
        """A browser window is in front. scope_ok: the scope gate passed. titled_portal and
        url_readable are for scope-lost: a portal-looking title whose address cannot be read."""
        try:
            with self._lock:
                now = self._clock()
                self._cur = hwnd
                w = self._wins.get(hwnd)
                if w is None:
                    if len(self._wins) >= _MAX_WINDOWS:
                        self._prune(now, force=True)
                    w = self._wins[hwnd] = _Win()
                if scope_ok:
                    if now - w.scope_seen > _WINDOW_FRESH_SEC:      # came back to the window: a new stretch
                        w.usable_at, w.attempts = now, 0
                    w.scope_seen = now
                    self._last_in_scope = now
                w.last_call = now
                if titled_portal and not url_readable and not scope_ok:
                    w.title_only_since = w.title_only_since or now
                else:
                    w.title_only_since = 0.0
        except Exception:
            pass

    def requested(self, ctx: Any = None) -> None:
        """A page read was handed to a worker (WP3+). Until read_result, it counts as in flight."""
        try:
            with self._lock:
                self._inflight_since = self._inflight_since or self._clock()
        except Exception:
            pass

    def read_result(self, usable: bool, ms: float = 0.0, source: str = "uia") -> None:
        """A read finished. source "busy": skipped because the previous read is still running."""
        try:
            with self._lock:
                now = self._clock()
                if source == "busy":
                    self._counts["busy"] += 1
                    self._busy_since = self._busy_since or now
                    return
                self._busy_since = self._inflight_since = 0.0
                self._last_ms, self._last_read_at = float(ms or 0.0), now
                self._counts["reads"] += 1
                if source == "ocr":
                    self._counts["ocr_reads"] += 1
                w = self._wins.get(self._cur) if self._cur is not None else None
                if usable:
                    if w is not None:
                        w.usable_at, w.attempts = now, 0
                else:
                    self._counts["blind"] += 1
                    if w is not None:
                        w.attempts += 1
        except Exception:
            pass

    def hits(self, session_id: str, hit: bool, logged_in: bool = True) -> None:
        """A page of this session was read: did it give any profile or dataset datapoint (ever)?"""
        try:
            with self._lock:
                now = self._clock()
                rec = self._sessions.get(session_id)
                if rec is None:
                    if len(self._sessions) >= _MAX_WINDOWS:
                        self._prune(now, force=True)
                    rec = self._sessions[session_id] = [0.0, False, now]
                rec[2] = now
                if hit:
                    rec[1] = True
                elif logged_in and not rec[0]:
                    rec[0] = now
        except Exception:
            pass

    def row(self, kind: str) -> None:
        """A tracker row: "sent" | "dead_letter" | "unattributed" | "ocr_only"."""
        key = {"sent": "rows_sent", "dead_letter": "rows_dead_letter",
               "unattributed": "unattributed_rows", "ocr_only": "ocr_only_rows"}.get(kind)
        if key:
            with self._lock:
                self._counts[key] += 1

    def stall(self, seconds: float) -> None:
        """From the watch thread: no capture tick has finished for this long (0 = moving again).
        The capture thread is the one that is stuck, so the state is evaluated here."""
        try:
            with self._lock:
                self._stall_s = float(seconds or 0.0)
            self.tick()
        except Exception:
            pass

    def uia_paused(self, paused: bool) -> None:
        self._paused = bool(paused)

    def unlisted_portal(self) -> None:
        """The scope tripwire fired."""
        self._unlisted_at = self._clock()

    def disk_failed(self) -> None:
        """A snapshot, log or dead-letter write failed."""
        self._disk_at = self._clock()

    def set_pending_update(self, version: str) -> None:
        self._pending_update = str(version or "")

    # ── the state machine ──────────────────────────────────────────────────────
    def _holds(self, name: str, now: float) -> bool:
        if name == "stalled":
            return self._stall_s > 0       # the watch thread reports only past its own STALL_S
        if name == "uia-paused":
            return self._paused
        if name == "blind":
            return any(now - w.scope_seen <= _WINDOW_FRESH_SEC and w.attempts > 0
                       and now - w.usable_at >= BLIND_ALERT_SEC for w in self._wins.values())
        if name == "scope-lost":
            return any(w.title_only_since and now - w.last_call <= _WINDOW_FRESH_SEC
                       and now - w.title_only_since >= SCOPE_LOST_SEC for w in self._wins.values())
        if name == "busy-long":
            return bool((self._busy_since and now - self._busy_since >= SLOW_ALERT_SEC)
                        or (self._inflight_since
                            and now - self._inflight_since >= PAGE_TIMEOUT_SEC + SLOW_ALERT_SEC))
        if name == "slow":
            return self._last_ms > PAGE_TIMEOUT_SEC * 1000 and now - self._last_read_at <= 30.0
        if name == "specs-silent":
            return any(not r[1] and r[0] and now - r[0] >= SPECS_SILENT_SEC and now - r[2] <= 30.0
                       for r in self._sessions.values())
        if name == "unlisted-portal":
            return bool(self._unlisted_at) and now - self._unlisted_at < UNLISTED_HOLD_SEC
        if name == "disk":
            return bool(self._disk_at) and now - self._disk_at < DISK_HOLD_SEC
        if name == "alerts-unconfigured":
            return self._alerts_enabled is not None and not self._alerts_enabled()
        if name == "old-build":
            return bool(self._pending_update)
        if name == "no-portal":
            return now - (self._last_in_scope or self._started) >= NO_PORTAL_SEC
        return True

    def tick(self) -> None:
        """Evaluates the state; raises the HUD / phone / log. Never raises."""
        try:
            with self._lock:
                self._tick()
        except Exception:
            pass

    def _tick(self) -> None:
        now = self._clock()
        dt = max(0.0, now - self._last_adv)
        self._last_adv = now
        day = self._wall().date()
        if day != self._day:
            self._day = day
            self.blind_sec_today = self.stalled_sec_today = 0.0
        if self.state == "blind":
            self.blind_sec_today += dt
        elif self.state == "stalled":
            self.stalled_sec_today += dt
        self._prune(now)
        new = next(s for s in _PRIORITY if self._holds(s, now))
        if new != self.state:
            old, self.state, self.since = self.state, new, self._iso()
            self._changed = True
            self._log(f"health state={new} (was {old}) {self._detail(new)}".rstrip())
            if new in FAILURE_STATES:
                self._raise(new, now, entering=True)
            elif old in FAILURE_STATES:
                self._back_to_normal()
        elif new in FAILURE_STATES:
            if now - self._hud_at.get(new, 0.0) >= HUD_REPEAT_SEC:
                self._raise(new, now, entering=False)
        if (not self._alerts_notice_done and now - self._started >= ALERTS_NOTICE_SEC
                and self._holds("alerts-unconfigured", now)):
            self._alerts_notice_done = True
            self._show(HUD_TITLE, SENTENCES["alerts-unconfigured"])

    def _raise(self, state: str, now: float, entering: bool) -> None:
        gap = HUD_MIN_GAP_SEC if entering else HUD_REPEAT_SEC
        if state not in OWN_HUD and now - self._hud_at.get(state, -1e9) >= gap:
            self._hud_at[state] = now
            self._hud_shown = state
            self._show(HUD_TITLE, SENTENCES[state])
        if self._phone is not None and now - self._phone_at.get(state, -1e9) >= PHONE_REPEAT_SEC:
            self._phone_at[state] = now
            try:
                self._phone(self._device or device_name(), state, self._detail(state))
            except Exception:
                pass

    def _back_to_normal(self) -> None:
        if self._hud_shown is not None:
            self._hud_shown = None
            self._show(HUD_BACK, "")

    def _show(self, title: str, subtitle: str) -> None:
        if self._hud is not None:
            try:
                self._hud(title, subtitle)
            except Exception:
                pass

    @staticmethod
    def _detail(state: str) -> str:
        return redact_identifiers(SENTENCES.get(state, ""))[:120]

    def _prune(self, now: float, force: bool = False) -> None:
        stale = 600.0 if not force else 60.0
        self._wins = {h: w for h, w in self._wins.items()
                      if now - w.last_call < stale or h == self._cur}
        self._sessions = {k: r for k, r in self._sessions.items() if now - r[2] < stale}

    # ── the record ─────────────────────────────────────────────────────────────
    def _iso(self) -> str:
        return self._wall().isoformat(timespec="seconds")

    def due(self) -> bool:
        return self._changed or self._clock() - self._last_write >= HEALTH_WRITE_SEC

    def record(self, extra: Optional[Dict[str, Any]] = None) -> CaptureHealth:
        with self._lock:
            c = self._counts
            version = self._version
            if version is None:
                try:
                    import version as _v
                    version = str(_v.APP_VERSION)
                except Exception:
                    version = ""
            rec = CaptureHealth(
                device=self._device or device_name(), version=version, at=self._iso(),
                state=self.state, since=self.since, detail=self._detail(self.state),
                reads=c["reads"], blind=c["blind"], busy=c["busy"],
                ocr_reads=c["ocr_reads"], ocr_only_rows=c["ocr_only_rows"],
                unattributed_rows=c["unattributed_rows"], rows_sent=c["rows_sent"],
                rows_dead_letter=c["rows_dead_letter"],
                blind_sec_today=int(self.blind_sec_today), stalled_sec_today=int(self.stalled_sec_today),
                pending_update=self._pending_update,
                alerts_configured=bool(self._alerts_enabled and self._alerts_enabled()))
            for k, v in (extra or {}).items():
                if hasattr(rec, k):
                    setattr(rec, k, v)
            return rec

    def write(self, extra: Optional[Dict[str, Any]] = None) -> CaptureHealth:
        """Builds the record, hands it to the `write` callable (WP12: the settings key) and logs one
        `health ...` line. Returns the record."""
        rec = self.record(extra)
        try:
            with self._lock:
                self._last_write = self._clock()
                self._changed = False
            if self._write is not None:
                self._write(rec)
            self._log("health " + vsdc_diag._fmt({
                "state": rec.state, "since": rec.since[11:], "reads": rec.reads, "blind": rec.blind,
                "busy": rec.busy, "ocr": rec.ocr_reads, "blind_s": rec.blind_sec_today,
                "stalled_s": rec.stalled_sec_today, "rows": rec.rows_sent, "dead": rec.rows_dead_letter}))
        except Exception:
            pass
        return rec
