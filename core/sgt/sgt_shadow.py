"""
core/sgt/sgt_shadow.py — SGT in shadow mode
============================================
Runs beside the live pipeline on every in-scope page, builds the session SGT WOULD have
dispatched, and writes it to a local log. Its returns also go into the tracker dump as their
OWN rows (capture method "SGT_shadow", keys in an "SGT:" namespace), beside the other engines'
rows so the two can be compared - an SGT row can never replace, drop or purge another engine's
row, nor be removed by one. A row is written as soon as SGT has a return it can key, and again
when it changes, so nothing waits for the session to end. Its captures also show on the HUD
pill, tagged "SGT (Shadow)" - only real captures, never page reads.

Per browser window (one client per session, two windows = two sessions):

  read      UIA first; OCR of the screenshot when UIA comes back blind (a canvas-painted
            page shows UIA nothing, or only Flutter's "Enable accessibility" button).
            A page is only re-read when it changed - the URL, the screenshot's 32x32
            hash, or 15 s passing - so a still page costs one hash, not one read.
  profile   latches by default: once found it is no longer looked for. A spec with
            "merge": "promote_longer" (the name) keeps looking, and a longer value that
            extends the held one replaces it ("RAVI MEHTA" -> "RAVI KUMAR MEHTA").
  datasets  never latch: a slot per return, keyed by its ack, else by (form, period);
            status only ever promotes. A page listing several returns keeps only the
            latest period's.
  current   the return being worked on, built from pieces on ANY page (plus the link and
            window title): a value shown once on a page is about that page's return. Each
            piece belongs to the page it came from - going back there and changing or
            clearing the field changes or clears it. A new period starts a new return. It
            becomes a dataset when a submission completes it, when another period is
            opened, or at session end - and only if complete; otherwise it is logged as
            incomplete and never dispatched. List pages never feed it.
  session   ends on a login/logout keyword in the link, 20 minutes idle, or app quit.
            The would-be payload is logged then, with a note of which of its acks the
            live pipeline also dispatched - the comparison shadow mode exists for.

Log: ~/AmanAssociates_Sera/sgt_shadow/sgt_shadow_YYYY-MM-DD.jsonl, one JSON object per
line. It holds client datapoints (PAN, name, acks) - it stays on this PC, like the database.
Page text is never written, only resolved values.
"""

import json
import os
import re
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from core.vsdc.vsdc_alerts import SERA_DATA_DIR_NAME, device_name
from core.vsdc.vsdc_assembler import get_status_rank

from .sgt_resolver import Dataset, PageResult, resolve_page
from .sgt_specs import SpecStore, compose_values
from .sgt_toolbox import MERGES

SHADOW_DIR_ENV = "SGT_SHADOW_DIR"
HUD_TAG = "SGT (Shadow)"        # the pill colours this tag (ui/components/vsdc_hud_pill.py)
CAPTURE_METHOD = "SGT_shadow"   # tracker rows; the tracker colours and can hide rows starting "SGT"
IDLE_END_SEC = 20 * 60
REREAD_AFTER_SEC = 15.0
MIN_UIA_LINES = 3               # fewer than this and the page is treated as blind
MISSES_TO_CLEAR = 2             # reads in a row a piece must be missing from its page to be cleared
MISSING_CLEAR_SEC = 5.0         # ...and for at least this long
TIMELINE_CAP = 200

# Flutter Web's accessibility placeholder: the whole page as UIA sees it until clicked.
_BLIND_PLACEHOLDER = re.compile(r"^\s*enable\s+accessibility\s*$", re.IGNORECASE)
# The keyword must be a whole segment of the link ("#/login", "/services/logout"), so a page
# such as ".../myProfile/aadhaarOtpLogin" does not end the session.
_LOGOUT = re.compile(r"(?:^|[/#=])(?:log-?out|log-?off|sign-?out)(?=$|[/?#.&])", re.IGNORECASE)
_LOGIN = re.compile(r"(?:^|[/#=])(?:log-?in|sign-?in)(?=$|[/?#.&])", re.IGNORECASE)


def shadow_dir() -> Path:
    env = os.environ.get(SHADOW_DIR_ENV)
    return Path(env) if env else Path.home() / SERA_DATA_DIR_NAME / "sgt_shadow"


def is_blind(lines: List[str]) -> bool:
    real = [ln for ln in lines if ln and ln.strip() and not _BLIND_PLACEHOLDER.match(ln)]
    return len(real) < MIN_UIA_LINES


def _frame_hash(img: Any) -> Optional[int]:
    if img is None:
        return None
    try:
        return hash(img.convert("RGB").resize((32, 32)).tobytes())
    except Exception:
        return None


@dataclass
class _Slot:
    values: Dict[str, str]
    record: str
    confidence: int
    first_page: str
    sent_key: Optional[str] = None      # the tracker dataset_key this slot was last written under

    @property
    def ack(self) -> Optional[str]:
        return self.values.get("ack")

    @property
    def form_period(self) -> Optional[Tuple[str, str]]:
        f, p = self.values.get("form"), self.values.get("period")
        return (f, p) if f and p else None


@dataclass
class _Draft:
    """
    The return being worked on, built from pieces seen on different pages. Each piece keeps
    the page it came from: that page owns it, so going back to it and changing or clearing the
    field there changes or clears the piece.
    """
    pieces: Dict[str, Dict[str, str]] = field(default_factory=dict)   # field -> {value, page, source, spec}
    slot: Optional["_Slot"] = None      # the row this return was written as, once complete

    def values(self, rules: Any = None) -> Dict[str, str]:
        vals = {k: p["value"] for k, p in self.pieces.items()}
        return compose(vals, rules) if rules is not None else vals

    @property
    def link_evidence(self) -> bool:
        return any(p["source"] == "link" for p in self.pieces.values())


def compose(values: Dict[str, str], rules: Any) -> Dict[str, str]:
    """Fills composed fields ("{tax_period} (FY {fy})") once their required parts are present."""
    out = dict(values)
    for fld, val in compose_values(values, getattr(rules, "compose", ())).items():
        if not out.get(fld):
            out[fld] = val
    return out


@dataclass
class _Session:
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    started: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    portal: str = ""
    profile: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    slots: List[_Slot] = field(default_factory=list)
    timeline: List[str] = field(default_factory=list)
    last_url: str = ""
    last_hash: Optional[int] = None
    last_read: float = 0.0
    reads: int = 0
    ocr_reads: int = 0
    blind_pages: int = 0
    read_ms: float = 0.0
    conflicts: int = 0
    draft: _Draft = field(default_factory=_Draft)

    @property
    def has_content(self) -> bool:
        return bool(self.profile or self.slots or self.draft.pieces)


class SgtShadow:
    """One per router. observe() is called once per in-scope tick; it never raises."""

    def __init__(
        self,
        store: Optional[SpecStore] = None,
        read_uia: Optional[Callable[[int], Dict[str, Any]]] = None,
        dispatched_ids: Optional[Callable[[], Iterable[str]]] = None,
        log_dir: Optional[Path] = None,
        clock: Callable[[], float] = time.time,
        today: Callable[[], date] = date.today,
        echo: Callable[[str], None] = print,
        notify: Optional[Callable[[str, str, str, Dict[str, Any]], None]] = None,
    ) -> None:
        self.store = store or SpecStore()
        # HUD pill: (event_type, title, subtitle, context). Only real captures reach it - a
        # client identified, a return found or promoted, a session with something in it
        # ending - never page reads or half-built returns, so the pill stays quiet otherwise.
        self._notify = notify
        # Tracker rows waiting to be handed to the router (one entry per return; a return that
        # changes again before it is sent is simply sent once, in its latest state).
        self._outbox: "OrderedDict[int, Tuple[_Session, _Slot]]" = OrderedDict()
        if read_uia is None:
            from core.vsdc import vsdc_uia_text

            def read_uia(hwnd: int) -> Dict[str, Any]:
                # Ticked radio buttons / checkboxes too: a filing type is often a radio choice.
                return vsdc_uia_text.read_page_text(hwnd, include_selection=True)
        self._read_uia = read_uia
        self._dispatched_ids = dispatched_ids or (lambda: ())
        self._log_dir = log_dir
        self._clock = clock
        self._today = today
        self._echo = echo
        self._sessions: Dict[int, _Session] = {}

    # ── Entry points ─────────────────────────────────────────────────────────────
    def observe(self, hwnd: int, portal: Optional[str], url: str, frame: Any = None,
                ocr: Any = None, title: str = "") -> Optional[PageResult]:
        try:
            return self._observe(hwnd, portal or "", url or "", frame, ocr, title or "")
        except Exception as e:                  # shadow mode must never disturb capture
            self._echo(f"[SGT] (shadow) error, page skipped: {e}")
            return None

    def end_session(self, hwnd: int, reason: str) -> None:
        s = self._sessions.pop(hwnd, None)
        if s is not None:
            self._finish(s, reason)

    def end_all(self, reason: str) -> None:
        for hwnd in list(self._sessions):
            self.end_session(hwnd, reason)

    # ── One tick ─────────────────────────────────────────────────────────────────
    def _observe(self, hwnd: int, portal: str, url: str, frame: Any, ocr: Any, title: str = "") -> Optional[PageResult]:
        now = self._clock()
        self._end_idle(now)
        s = self._sessions.get(hwnd)
        if s is not None and url and url != s.last_url:
            if _LOGOUT.search(url):
                if len(s.timeline) < TIMELINE_CAP:
                    s.timeline.append(url)      # how the session ended belongs on its timeline
                self.end_session(hwnd, "logout")
                s = None
            elif _LOGIN.search(url) and s.has_content:
                self.end_session(hwnd, "login page - next client")
                s = None
        if s is not None and portal and s.portal and portal != s.portal:
            # The other portal is another login. An empty session (say, opened on the GST
            # login page before going to the ITR portal) just takes the new portal.
            if s.has_content:
                self.end_session(hwnd, f"moved to {portal}")
                s = None
            else:
                s.portal = portal
        if s is None:
            s = self._sessions[hwnd] = _Session(portal=portal)
        if not s.portal:
            s.portal = portal
        s.last_seen = now

        url_changed = url != s.last_url
        if url_changed:
            s.last_url = url
            if url and (not s.timeline or s.timeline[-1] != url) and len(s.timeline) < TIMELINE_CAP:
                s.timeline.append(url)
        if _LOGOUT.search(url):
            return None                         # nothing about a client lives on a logout page
        # Login pages ARE read: the ITR password page shows the PAN being logged into. The
        # login keyword above already closed the previous client's session, so what is read
        # here belongs to the new one.

        h = _frame_hash(frame)
        changed = url_changed or h is None or h != s.last_hash or (now - s.last_read) >= REREAD_AFTER_SEC
        s.last_hash = h
        if not changed:
            return None
        s.last_read = now

        t0 = time.perf_counter()
        lines = list((self._read_uia(hwnd) or {}).get("lines") or [])
        source = "uia"
        if is_blind(lines):
            s.blind_pages += 1
            lines = []
            if ocr is not None and frame is not None:
                lines = list((ocr.scan_image(frame, region_type="full") or {}).get("lines") or [])
                source = "ocr"
                s.ocr_reads += 1
        s.read_ms += (time.perf_counter() - t0) * 1000
        s.reads += 1
        if not lines:
            return None

        registry = self.store.get()
        # Latched datapoints are not looked for again; "promote_longer" ones (the name) are.
        skip = set(s.profile) & registry.latching_fields()
        res = resolve_page(registry, lines, portal, url, self._today(), skip_profile=skip, title=title)
        s.conflicts += len(res.conflicts)
        if not res.is_list:
            # Before the datasets, so a confirmation page's ack can be joined to the form and
            # period the earlier pages supplied.
            self._update_draft(s, res, url, source, registry.current_rules)
        self._absorb(s, res, url, source, registry)
        return res

    # ── The return being worked on ───────────────────────────────────────────────
    def _update_draft(self, s: _Session, res: PageResult, url: str, source: str, rules: Any) -> None:
        d = s.draft
        seen = res.current
        # This page owns the pieces it supplied: gone from it now = cleared (the CA went back
        # and emptied the field). Only after MISSES_TO_CLEAR reads in a row, so one read of a
        # half-drawn page cannot wipe a good value.
        now = self._clock()
        for fld, piece in list(d.pieces.items()):
            if piece["page"] != url:
                continue
            if fld in seen:
                piece["misses"], piece["missing_since"] = 0, None
                continue
            piece["misses"] = piece.get("misses", 0) + 1
            if piece.get("missing_since") is None:
                piece["missing_since"] = now
            # Both: several reads AND some seconds. Clicking Continue leaves the old link in the
            # address bar for a moment while the page goes blank - that gap must not clear what
            # was just picked (seen in a real test: the AY was cleared 2 s after it was set).
            if piece["misses"] >= MISSES_TO_CLEAR and now - piece["missing_since"] >= MISSING_CLEAR_SEC:
                del d.pieces[fld]
                self._event(s, "current", change="cleared", field=fld, value=piece["value"], page=url, source=source)
                self._echo(f"[SGT] (shadow) return in progress: {fld} cleared (gone from its page)")
        if not seen:
            return
        # A different period is a different return: close the one being built, start afresh.
        before = d.values(rules).get("period")
        trial = dict(d.values())
        trial.update({f: h.value for f, h in seen.items()})
        after = compose(trial, rules).get("period")
        if before and after and before != after:
            self._close_draft(s, rules, "a different period was opened", url)
            d = s.draft
        for fld, hit in seen.items():
            old = d.pieces.get(fld)
            if old and old["value"] == hit.value:
                old["page"], old["source"], old["misses"], old["missing_since"] = url, hit.source, 0, None
                continue
            d.pieces[fld] = {"value": hit.value, "page": url, "source": hit.source, "spec": hit.spec,
                             "misses": 0, "missing_since": None}
            self._event(s, "current", change="changed" if old else "set", field=fld, value=hit.value,
                        previous=old["value"] if old else None, spec=hit.spec, via=hit.source, page=url, source=source)
            self._echo(f"[SGT] (shadow) return in progress: {fld} = {hit.value}"
                       f"{' (was ' + old['value'] + ')' if old else ''}  [{hit.spec}, {hit.source}]")
        # Complete already? Then it is a return now - written at once rather than when it
        # closes, so quitting the app (or a crash) in the middle of a filing loses nothing.
        # It keeps building; later pieces update the same row.
        vals, missing = self._draft_as_dataset(d, rules)
        if not missing:
            prev = d.slot
            if (prev is not None and prev in s.slots and not prev.ack
                    and prev.form_period != (vals.get("form"), vals.get("period"))):
                # The CA went back and changed the form (or year) of this same return: move its
                # row rather than leave a stale one behind (the old key is superseded on send).
                old = dict(prev.values)
                prev.values.update(vals)
                self._queue(s, prev)
                self._event(s, "dataset", change="moved", record="current_return", previous=old,
                            values=prev.values, page=url, source=source)
                self._echo(f"[SGT] (shadow) return in progress moved: {self._label(old)} -> {self._label(prev.values)}")
            else:
                d.slot = self._merge_values(s, vals, "current_return", 85, url, source)

    @staticmethod
    def _draft_as_dataset(d: _Draft, rules: Any) -> Tuple[Dict[str, str], List[str]]:
        """The return being built as dataset values, plus what it still lacks (empty = complete)."""
        vals = d.values(rules)
        missing = [f for f in getattr(rules, "complete_when", ("form", "period")) if not vals.get(f)]
        if not missing and not vals.get("status"):
            status = getattr(rules, "in_progress_status", None)
            if status and (d.link_evidence or not getattr(rules, "in_progress_needs_link", True)):
                vals["status"] = status
            else:
                missing.append("status (nothing shows it, and no link evidence that it was being filed)")
        return vals, missing

    def _close_draft(self, s: _Session, rules: Any, reason: str, url: str) -> None:
        """Turns the return being built into a dataset if it is complete; logs it if not."""
        d = s.draft
        s.draft = _Draft()
        if not d.pieces:
            return
        vals, missing = self._draft_as_dataset(d, rules)
        if missing:
            self._event(s, "current", change="incomplete - not dispatched", values=vals, missing=missing, reason=reason)
            self._echo(f"[SGT] (shadow) return in progress dropped ({reason}): missing {', '.join(missing)}")
            return
        self._merge_values(s, vals, "current_return", 85, url, "pages")

    def _fill_from_draft(self, s: _Session, values: Dict[str, str], rules: Any) -> bool:
        """
        A dataset that lacks its form or period (a confirmation page shows the ack, not the
        return) takes them from the return being built - unless they disagree, which means
        it is some other return. True if the draft was used.
        """
        draft = s.draft.values(rules)
        if not draft or (values.get("form") and values.get("period")):
            return False
        for k in ("form", "period"):
            if values.get(k) and draft.get(k) and values[k] != draft[k]:
                return False
        used = False
        for k in ("form", "period", "filing_type"):
            if not values.get(k) and draft.get(k):
                values[k] = draft[k]
                used = True
        return used

    # ── Folding a page into the session ──────────────────────────────────────────
    # ── Tracker rows ─────────────────────────────────────────────────────────────
    def _queue(self, s: _Session, slot: _Slot) -> None:
        self._outbox[id(slot)] = (s, slot)
        self._outbox.move_to_end(id(slot))

    def _queue_all(self, s: _Session) -> None:
        """The client became known (or their name completed): every row of the session is
        rewritten under the client's key, replacing the rows written before it was known."""
        for slot in s.slots:
            if slot.sent_key is not None:
                self._queue(s, slot)

    def pending(self) -> int:
        return len(self._outbox)

    def pop_dispatch(self) -> Optional[Dict[str, Any]]:
        """The next tracker row to save, in the pipeline's payload shape, or None."""
        while self._outbox:
            _, (s, slot) = self._outbox.popitem(last=False)
            try:
                return self._tracker_payload(s, slot)
            except Exception as e:                  # never let one bad row block the rest
                self._echo(f"[SGT] could not build a tracker row: {e}")
        return None

    def drain(self) -> List[Dict[str, Any]]:
        out = []
        while True:
            p = self.pop_dispatch()
            if p is None:
                return out
            out.append(p)

    @staticmethod
    def dataset_key(s: _Session, values: Dict[str, str]) -> str:
        """
        SGT's own key namespace ("SGT:..."), so an SGT row only ever replaces an SGT row - never
        VSDC's row for the same return. Stable across sessions for a known client, so seeing
        the same return again updates its row instead of adding one.
        """
        def norm(x: Any) -> str:
            return re.sub(r"[^A-Z0-9]", "", str(x or "").upper())
        portal = "GST" if "gst" in (s.portal or "").lower() else "ITR"
        prof = {k: v["value"] for k, v in s.profile.items()}
        ident = (prof.get("gstin") or prof.get("pan")) if portal == "GST" else (prof.get("pan") or prof.get("gstin"))
        ident = norm(ident) or f"S{norm(s.session_id)}"
        if values.get("form") and values.get("period"):
            return f"SGT:{portal}:{ident}:{norm(values['form'])}:{norm(values['period'])}"
        return f"SGT:{portal}:{ident}:ACK:{norm(values.get('ack'))}"

    def _tracker_payload(self, s: _Session, slot: _Slot) -> Dict[str, Any]:
        v = dict(slot.values)
        prof = {k: p["value"] for k, p in s.profile.items()}
        key = self.dataset_key(s, v)
        supersedes = slot.sent_key if slot.sent_key and slot.sent_key != key else None
        slot.sent_key = key
        gstin = prof.get("gstin") or ""
        pan = prof.get("pan") or (gstin[2:12] if len(gstin) == 15 else "")
        status = v.get("status") or ("Submitted" if v.get("ack") else "In Progress")
        filing_date = (f"{v['filing_date']} 00:00:00" if v.get("filing_date")
                       else datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        default_form = "GST Return" if "gst" in (s.portal or "").lower() else "ITR"
        return {
            "source": "sgt",
            "session_id": f"SGT-{s.session_id}",
            "portal": s.portal,
            "pan": pan,
            "gstin": gstin,
            "client_name": prof.get("name", ""),
            "dob": prof.get("dob", ""),
            "mobile": prof.get("phone", ""),
            "email": prof.get("email", ""),
            "filing_type": v.get("form") or default_form,
            "filing_preference": v.get("filing_type", ""),
            "period_label": v.get("period", ""),
            "arn": v.get("ack") or "N/A",
            "status": status,
            "filing_date": filing_date,
            "raw_text": "",                         # SGT never stores page text
            "capture_method": CAPTURE_METHOD,
            "identity_resolved": bool(pan),
            "page_url": slot.first_page,
            "dataset_key": key,
            "supersedes_dataset_key": supersedes,
            "raw_payload": {
                "source": {"engine": "SGT", "mode": "shadow", "record": slot.record, "confidence": slot.confidence},
                "sgt_dataset": v,
                "client_profile": prof,
                "timeline": list(s.timeline),
                "session_id": f"SGT-{s.session_id}",
                "dataset_key": key,
            },
        }

    # ── HUD pill ─────────────────────────────────────────────────────────────────
    def _hud(self, s: _Session, event_type: str, title: str, subtitle: str,
             values: Optional[Dict[str, str]] = None) -> None:
        if self._notify is None:
            return
        v = values or {}
        ctx = {"portal": s.portal, "form": v.get("form"), "filing_pref": v.get("filing_type"),
               "period": v.get("period")}
        try:
            self._notify(event_type, title, f"{subtitle} • {HUD_TAG}".strip(" •"), ctx)
        except Exception as e:                  # the pill must never break capture
            self._echo(f"[SGT] (shadow) HUD event failed: {e}")

    @staticmethod
    def _who(s: _Session) -> str:
        p = s.profile
        return (p.get("name") or p.get("pan") or p.get("gstin") or {}).get("value", "")

    def _offer_profile(self, s: _Session, fld: str, value: str, spec: str, confidence: int,
                       url: str, source: str, registry: Any) -> None:
        """A profile value seen on a page: taken if new, else handled by the field's merge policy."""
        held = s.profile.get(fld)
        if held is not None:
            policy = registry.merge_policy(fld) if registry is not None else "latch"
            if held["value"] == value or policy == "latch":
                return
            if not MERGES[policy](held["value"], value):
                self._event(s, "profile", change="not promoted", field=fld, value=value,
                            kept=held["value"], spec=spec, page=url, source=source)
                return
            self._event(s, "profile", change="promoted", field=fld, value=value, previous=held["value"],
                        spec=spec, confidence=confidence, page=url, source=source)
            self._echo(f"[SGT] (shadow) profile {fld} promoted: {held['value']} -> {value}  [{spec}]")
            s.profile[fld] = {"value": value, "spec": spec, "confidence": confidence}
            if fld == "name":
                self._hud(s, "update", "Client name completed", value)
                self._queue_all(s)
            return
        s.profile[fld] = {"value": value, "spec": spec, "confidence": confidence}
        self._event(s, "profile", field=fld, value=value, spec=spec, confidence=confidence, page=url, source=source)
        self._echo(f"[SGT] (shadow) profile {fld} = {value}  [{spec}]")
        if fld in ("pan", "gstin", "name"):
            self._queue_all(s)                  # rows written before the client was known get their identity
        if fld in ("pan", "gstin"):             # the moment the client is known
            name = (s.profile.get("name") or {}).get("value", "")
            self._hud(s, "identity", "Client identified", f"{name} • {fld.upper()}: {value}".strip(" •"))

    def _absorb(self, s: _Session, res: PageResult, url: str, source: str, registry: Any = None) -> None:
        for fld, hit in res.profile.items():
            self._offer_profile(s, fld, hit.value, hit.spec, hit.confidence, url, source, registry)
        # Datapoints built from parts (first + middle + last name): offered like any other
        # value, so the joined name goes through the same promotion rule as a read one.
        prules = getattr(registry, "profile_rules", None)
        if prules is not None and prules.compose:
            held = {k: v["value"] for k, v in s.profile.items()}
            for fld, value in compose_values(held, prules.compose).items():
                self._offer_profile(s, fld, value, f"compose:{fld}", 92, url, source, registry)
        rules = registry.current_rules if registry is not None else None
        for ds in res.datasets:
            self._merge(s, ds, url, source, rules)

    def _find_slot(self, s: _Session, values: Dict[str, str]) -> Optional[_Slot]:
        ack = values.get("ack")
        if ack:
            for slot in s.slots:
                if slot.ack == ack:
                    return slot
        fp = (values.get("form"), values.get("period"))
        if all(fp):
            for slot in s.slots:
                if slot.form_period == fp and (not ack or not slot.ack):
                    return slot
        return None

    def _merge(self, s: _Session, ds: Dataset, url: str, source: str, rules: Any = None) -> None:
        values = ds.values()
        if rules is not None and self._fill_from_draft(s, values, rules):
            self._echo(f"[SGT] (shadow) {ds.record}: form/period taken from the return in progress")
            if values.get("ack"):
                # Submitted: the return being built is this one, and it is finished.
                s.draft = _Draft()
                self._event(s, "current", change="completed by a submission", ack=values["ack"], page=url)
        self._merge_values(s, values, ds.record, ds.confidence, url, source)

    def _merge_values(self, s: _Session, values: Dict[str, str], record: str, confidence: int,
                      url: str, source: str) -> Optional[_Slot]:
        if not values.get("ack") and not (values.get("form") and values.get("period")):
            return None                         # nothing to key it on - not a return yet
        slot = self._find_slot(s, values)
        if slot is None:
            slot = _Slot(dict(values), record, confidence, url)
            s.slots.append(slot)
            self._queue(s, slot)
            self._event(s, "dataset", change="new", record=record, values=values,
                        confidence=confidence, page=url, source=source)
            self._echo(f"[SGT] (shadow) dataset {self._label(values)}  [{record}]")
            submitted = bool(values.get("ack")) and get_status_rank(values.get("status")) >= 2
            detail = f"Ack: {values['ack']}" if values.get("ack") else (values.get("status") or "")
            self._hud(s, "submit" if submitted else "capture",
                      "Submission captured" if submitted else "Return captured",
                      f"{self._who(s)} • {detail}".strip(" •"), slot.values)
            return slot
        ds_record = record
        changes = {}
        for k, v in values.items():
            old = slot.values.get(k)
            if k == "status":
                if old is None or get_status_rank(v) > get_status_rank(old):
                    slot.values[k] = v
                    changes[k] = [old, v]
            elif old is None:
                slot.values[k] = v
                changes[k] = [None, v]
            elif old != v:
                changes[k] = [old, v, "disagrees - kept the first"]
        if any(len(c) == 2 for c in changes.values()):     # a real change, not just a disagreement
            self._queue(s, slot)
        if changes:
            self._event(s, "dataset", change="updated", record=ds_record, key=self._label(slot.values),
                        changes=changes, page=url, source=source)
            self._echo(f"[SGT] (shadow) dataset {self._label(slot.values)} updated: "
                       + ", ".join(f"{k} {c[0]!r}->{c[1]!r}" for k, c in changes.items()))
            if "status" in changes and changes["status"][0]:        # a status that moved forward
                self._hud(s, "update", "Return status updated",
                          f"{changes['status'][0]} → {changes['status'][1]}", slot.values)
        return slot

    @staticmethod
    def _label(values: Dict[str, str]) -> str:
        return " ".join(str(values[k]) for k in ("form", "period", "status", "ack") if values.get(k))

    # ── Ending ───────────────────────────────────────────────────────────────────
    def _end_idle(self, now: float) -> None:
        for hwnd, s in list(self._sessions.items()):
            if now - s.last_seen > IDLE_END_SEC:
                self.end_session(hwnd, "idle 20 min")

    def would_be_payload(self, s: _Session) -> Dict[str, Any]:
        dispatched: Set[str] = {str(x) for x in (self._dispatched_ids() or ())}
        datasets = []
        for slot in s.slots:
            d = dict(slot.values)
            d["record"] = slot.record
            if slot.ack:
                d["live_pipeline_also_captured"] = slot.ack in dispatched
            datasets.append(d)
        profile = {k: v["value"] for k, v in s.profile.items()}
        return {
            "portal": s.portal,
            "client_profile": profile,
            "client_known": bool(profile.get("pan") or profile.get("gstin")),
            "datasets": datasets,
            "timeline": list(s.timeline),
            "device_name": device_name(),
        }

    def _finish(self, s: _Session, reason: str) -> None:
        if not s.has_content:
            return
        # The return still being built becomes a dataset only if it is complete.
        self._close_draft(s, self.store.get().current_rules, f"session end ({reason})", s.last_url)
        payload = self.would_be_payload(s)
        stats = {"reads": s.reads, "ocr_reads": s.ocr_reads, "blind_pages": s.blind_pages,
                 "read_ms_total": round(s.read_ms, 1), "conflicts": s.conflicts,
                 "minutes": round((s.last_seen - s.started) / 60, 1)}
        self._event(s, "session_end", reason=reason, payload=payload, stats=stats)
        who = payload["client_profile"].get("name") or payload["client_profile"].get("pan") \
            or payload["client_profile"].get("gstin") or "client unknown"
        note = ""
        if payload["datasets"] and not payload["client_known"]:
            # Live mode hands these to VSDC247's unattributed path (tracker row + phone alert).
            note = " - no PAN/GSTIN: live mode would send these through VSDC247's client-unknown path"
        self._echo(f"[SGT] (shadow) session ended ({reason}): {who}, "
                   f"{len(payload['datasets'])} dataset(s), {len(payload['client_profile'])} profile field(s){note}")
        # Not on app quit or when SGT is switched off: nobody is looking at the pill then.
        if "shutdown" not in reason.lower() and "switched off" not in reason.lower():
            n = len(payload["datasets"])
            if payload["datasets"] and not payload["client_known"]:
                self._hud(s, "prompt", "SGT session ended - client unknown", f"{n} return(s) with no PAN/GSTIN")
            else:
                self._hud(s, "logout", "SGT session logged", f"{who} • {n} return(s)")

    def _event(self, s: _Session, event: str, **data: Any) -> None:
        rec = {"ts": datetime.now().isoformat(timespec="seconds"), "event": event,
               "session": s.session_id, "portal": s.portal, **data}
        try:
            d = self._log_dir or shadow_dir()
            d.mkdir(parents=True, exist_ok=True)
            with open(d / f"sgt_shadow_{date.today().isoformat()}.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:
            self._echo(f"[SGT] (shadow) could not write the log: {e}")
