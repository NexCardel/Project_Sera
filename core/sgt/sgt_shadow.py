"""
core/sgt/sgt_shadow.py — SGT in shadow mode
============================================
Runs beside the live pipeline on every in-scope page, builds the session SGT WOULD have
dispatched, and writes it to a local log. Its datasets also go into the tracker dump as their
OWN rows (capture method "SGT_shadow", keys in an "SGT:" namespace), beside the other engines'
rows so the two can be compared - an SGT row can never replace, drop or purge another engine's
row, nor be removed by one. A row is written as soon as SGT has a dataset it can key, and again
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
  datasets  never latch: a slot per dataset (form + period + submit status), keyed by its
            ARN, else by (form, period). The status is a level of the one submit ladder
            every portal shares (sgt_toolbox.SUBMIT_LEVELS), climbed by what was captured:
            form + period (being worked on) -> Draft; + ARN -> Submitted (Not Verified);
            + a submit message -> whatever its wording says. It only ever promotes, and the
            evidence for the level is kept beside it. A page listing several datasets
            keeps only the latest period's; a card naming another client is not attributed.
  current   the dataset being worked on, built from pieces on ANY page (plus the link and
            window title): a value shown once on a page is about that page's dataset. Each
            piece belongs to the page it came from - going back there and changing or
            clearing the field changes or clears it. A new period starts a new dataset. It
            becomes a dataset when a submission completes it, when another period is
            opened, or at session end - and only if complete; otherwise it is logged as
            incomplete and never dispatched. List pages never feed it.
  session   ends on a login/logout keyword in the link, 20 minutes idle, or app quit.
            The would-be payload is logged then, with a note of which of its ARNs the
            live pipeline also dispatched - the comparison shadow mode exists for.
  identity  a dataset filed under the WRONG client is worse than a missed one - and a missed
            one is not acceptable either (2026-09-22). The first sighting of the client's
            PAN/GSTIN attributes the session's rows (the log notes "seen once" / "seen on two
            pages"; the portals often show it only once). The protection is contradiction,
            not repetition: a different PAN/GSTIN appearing ends the session there and then -
            nothing is overwritten, and the clashing value is not taken from that page.
            A dataset remembers the PANs printed where it was read (its card's own PAN, or
            every PAN on a page whose text built it); it is attributed only to a client whose
            PAN is among them, and waits while the client is unknown - at session end a
            dataset still waiting is written UNATTRIBUTED, never dropped. While another
            client's PAN is readable on a page, that page's cards are not attributed and its
            text does not feed the dataset in progress (only its link and title do).
  checks    before a dataset is written, the spec file's dataset_rules check it as a whole
            (an ITR period must be an assessment year, an ack's own date must fall inside
            it...). A dataset that fails is HELD with the reason, and written once it passes.
  safety    open sessions are snapshotted to sessions_state.json as they change; after a
            crash, the next start-up finishes them and re-writes their datasets.
            Every page read can be recorded for replay (sgt_corpus.py), and spec hits are
            counted to notice a portal that changed (sgt_health.py).

Log: ~/AmanAssociates_Sera/sgt_shadow/sgt_shadow_YYYY-MM-DD.jsonl, one JSON object per
line. It holds client datapoints (PAN, name, ARNs) - it stays on this PC, like the database.
The log holds resolved values only; page TEXT is kept separately, for replay, only while page
recording is on (sgt_corpus.py - local, 30 days).
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
from .sgt_resolver import Dataset, PageResult, resolve_page
from .sgt_specs import SpecStore, compose_values
from .sgt_toolbox import MERGES, SUBMIT_LEVELS, submit_level

SHADOW_DIR_ENV = "SGT_SHADOW_DIR"
HUD_TAG = "SGT (Shadow)"        # the pill colours this tag (ui/components/vsdc_hud_pill.py)
CAPTURE_METHOD = "SGT_shadow"   # tracker rows; the tracker colours and can hide rows starting "SGT"
IDLE_END_SEC = 20 * 60
REREAD_AFTER_SEC = 15.0
MIN_UIA_LINES = 3               # fewer than this and the page is treated as blind
MISSES_TO_CLEAR = 2             # reads in a row a piece must be missing from its page to be cleared
MISSING_CLEAR_SEC = 5.0         # ...and for at least this long
TIMELINE_CAP = 200
CURRENT_RECORD = "current_dataset"  # the record name a dataset built across pages is written under
# Fields a dataset may name that say WHOSE it is: checked against the session's client, never
# stored as dataset fields.
_CLIENT_KEYS = ("pan", "gstin")

# Any PAN printed on a page (upper-case, whole word).
_PAN_TOKEN = re.compile(r"(?<![A-Z0-9])[A-Z]{5}[0-9]{4}[A-Z](?![A-Z0-9])")

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
    held: Optional[List[str]] = None    # dataset_rules it breaks: not written until they pass
    # The PANs printed where this dataset was read (its card's own PAN; or every PAN on a page
    # whose text built it). Once the client is known, it must be one of them.
    claimed: Optional[List[str]] = None

    @property
    def arn(self) -> Optional[str]:
        return self.values.get("arn")

    @property
    def form_period(self) -> Optional[Tuple[str, str]]:
        f, p = self.values.get("form"), self.values.get("period")
        return (f, p) if f and p else None


@dataclass
class _Draft:
    """
    The dataset being worked on, built from pieces seen on different pages. Each piece keeps
    the page it came from: that page owns it, so going back to it and changing or clearing the
    field there changes or clears the piece.
    """
    pieces: Dict[str, Dict[str, str]] = field(default_factory=dict)   # field -> {value, page, source, spec}
    slot: Optional["_Slot"] = None      # the row this dataset was written as, once complete
    claims: List[str] = field(default_factory=list)   # PANs printed on the pages whose TEXT built it

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
    # (field, value) pairs already logged as "not promoted": a truncated header name is seen on
    # every page, and one log line about it is enough.
    not_promoted: Set[Tuple[str, str]] = field(default_factory=set)
    confirmed: bool = False             # the client's identity may go on rows
    confirm_note: str = ""
    strict: bool = False                # born from a contradiction: a single sighting never confirms

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
        recorder: Any = None,
        stats: Any = None,
        state_path: Optional[Path] = None,
    ) -> None:
        self.store = store or SpecStore()
        self._recorder = recorder           # sgt_corpus.PageRecorder, or None
        self._stats = stats                 # sgt_health.SpecStats, or None
        self._state_path = state_path       # crash snapshot; None = not kept (tests, replay)
        self._state_saved = 0.0
        self._checked_day: Optional[date] = None
        # HUD pill: (event_type, title, subtitle, context). Only real captures reach it - a
        # client identified, a dataset found or promoted, a session with something in it
        # ending - never page reads or half-built datasets, so the pill stays quiet otherwise.
        self._notify = notify
        # Tracker rows waiting to be handed to the router (one entry per dataset; a dataset that
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
        self._foreign_pans: Set[str] = set()     # other PANs readable on the page being absorbed
        self._page_pans: Set[str] = set()        # every PAN readable on it
        if self._state_path is not None:
            self._recover()

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
            self._save_state(force=True)
        if self._stats is not None:
            self._stats.save()

    def end_all(self, reason: str) -> None:
        for hwnd in list(self._sessions):
            self.end_session(hwnd, reason)

    # ── One tick ─────────────────────────────────────────────────────────────────
    def _observe(self, hwnd: int, portal: str, url: str, frame: Any, ocr: Any, title: str = "") -> Optional[PageResult]:
        now = self._clock()
        self._end_idle(now)
        self._daily_health_check()
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
        today = self._today()
        if self._recorder is not None:
            self._recorder.record(session=s.session_id, portal=portal, url=url, title=title,
                                  source=source, lines=lines, ts=now, today=today)
        if self._stats is not None:
            self._stats.record_read(portal, source, today)

        registry = self.store.get()
        # Latched datapoints are not looked for again; "promote_longer" ones (the name) are.
        # The client's PAN / GSTIN are ALWAYS looked for: a second sighting confirms the
        # client, a different value means this window is now on another client.
        skip = (set(s.profile) & registry.latching_fields()) - set(_CLIENT_KEYS)
        res = resolve_page(registry, lines, portal, url, today, skip_profile=skip, title=title)
        if self._stats is not None:
            self._stats.record_hits(portal, [h.spec for h in res.profile.values()]
                                    + [h.spec for h in res.current.values()]
                                    + [d.record for d in res.datasets], today)
        clash = None if res.is_list else self._identity_conflict(s, res)
        if clash:
            # Another client's PAN/GSTIN: this window moved to someone else without passing a
            # login page. End the session here - nothing already captured is touched - and
            # start one that must see its client twice. The clashing value is NOT taken from
            # this page: a stray GSTIN (a supplier in a table) must not name the new session.
            self.end_session(hwnd, f"a different {clash.upper()} appeared - treated as another client")
            s = self._sessions[hwnd] = _Session(portal=portal, strict=True, last_url=url, last_hash=h,
                                                last_read=now, timeline=[url] if url else [])
            for k in _CLIENT_KEYS:
                res.profile.pop(k, None)
        s.conflicts += len(res.conflicts)
        self._page_pans = {p for ln in lines for p in _PAN_TOKEN.findall(ln or "")}
        self._foreign_pans = self._other_pans_on_page(s, res, lines)
        if not res.is_list:
            # Before the datasets, so a confirmation page's ARN can be joined to the form and
            # period the earlier pages supplied.
            self._update_draft(s, res, url, source, registry.current_rules)
        self._absorb(s, res, url, source, registry)
        self._save_state()
        return res

    # ── The dataset being worked on ──────────────────────────────────────────────
    def _update_draft(self, s: _Session, res: PageResult, url: str, source: str, rules: Any) -> None:
        d = s.draft
        seen = res.current
        if self._foreign_pans:
            # Another client's PAN is readable on this page: its TEXT may be about them, so only
            # what the link and window title say (the filing wizard's form and year) is used.
            seen = {f: h for f, h in seen.items() if h.source != "page"}
        if self._page_pans and not self._client_pan(s) and any(h.source == "page" for h in seen.values()):
            # The client is not known yet and this page's text prints PANs: remember them, so the
            # dataset is only ever written for a client whose PAN was among them.
            d.claims = sorted(set(d.claims) | self._page_pans)
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
                self._echo(f"[SGT] (shadow) dataset in progress: {fld} cleared (gone from its page)")
        if not seen:
            return
        # A different period is a different dataset: close the one being built, start afresh.
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
                             "evidence": hit.evidence, "misses": 0, "missing_since": None}
            self._event(s, "current", change="changed" if old else "set", field=fld, value=hit.value,
                        previous=old["value"] if old else None, spec=hit.spec, via=hit.source, page=url, source=source)
            self._echo(f"[SGT] (shadow) dataset in progress: {fld} = {hit.value}"
                       f"{' (was ' + old['value'] + ')' if old else ''}  [{hit.spec}, {hit.source}]")
        # Complete already? Then it is a dataset now - written at once rather than when it
        # closes, so quitting the app (or a crash) in the middle of a filing loses nothing.
        # It keeps building; later pieces update the same row.
        vals, missing = self._draft_as_dataset(d, rules)
        if not missing:
            prev = d.slot
            if (prev is not None and prev in s.slots and not prev.arn
                    and prev.form_period != (vals.get("form"), vals.get("period"))):
                # The CA went back and changed the form (or year) of this same dataset: move its
                # row rather than leave a stale one behind (the old key is superseded on send).
                old = dict(prev.values)
                prev.values.update(vals)
                self._queue(s, prev)
                self._event(s, "dataset", change="moved", record=CURRENT_RECORD, previous=old,
                            values=prev.values, page=url, source=source)
                self._echo(f"[SGT] (shadow) dataset in progress moved: {self._label(old)} -> {self._label(prev.values)}")
            else:
                d.slot = self._merge_values(s, vals, CURRENT_RECORD, 85, url, source, claimed=d.claims or None)

    @staticmethod
    def _draft_as_dataset(d: _Draft, rules: Any) -> Tuple[Dict[str, str], List[str]]:
        """
        The dataset being built as dataset values, plus what it still lacks (empty = complete).
        Its captured form + period make it at least a Draft, whatever a page status said
        ("Not Filed" on the form's own page is still a return being prepared).
        """
        vals = d.values(rules)
        if vals.get("status"):
            vals["status_evidence"] = d.pieces.get("status", {}).get("evidence") or vals["status"]
        missing = [f for f in getattr(rules, "complete_when", ("form", "period")) if not vals.get(f)]
        if missing:
            return vals, missing
        working = getattr(rules, "in_progress_status", None)
        needs_link = getattr(rules, "in_progress_needs_link", True)
        if working and (d.link_evidence or not needs_link) and submit_level(vals.get("status")) < submit_level(working):
            vals["status"] = working
            vals["status_evidence"] = ("form + period captured (in the page link)" if d.link_evidence
                                       else "form + period captured")
        if not vals.get("status"):
            missing.append("status (nothing shows it, and no link evidence that it was being filed)")
        return vals, missing

    def _close_draft(self, s: _Session, rules: Any, reason: str, url: str) -> None:
        """Turns the dataset being built into a dispatched one if it is complete; logs it if not."""
        d = s.draft
        s.draft = _Draft()
        if not d.pieces:
            return
        vals, missing = self._draft_as_dataset(d, rules)
        if missing:
            self._event(s, "current", change="incomplete - not dispatched", values=vals, missing=missing, reason=reason)
            self._echo(f"[SGT] (shadow) dataset in progress dropped ({reason}): missing {', '.join(missing)}")
            return
        self._merge_values(s, vals, CURRENT_RECORD, 85, url, "pages", claimed=d.claims or None)

    def _fill_from_draft(self, s: _Session, values: Dict[str, str], rules: Any) -> bool:
        """
        A dataset that lacks its form or period (a confirmation page shows the ARN, not the
        form) takes them from the dataset being built - unless they disagree, which means
        it is some other dataset. True if the draft was used.
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
        """Hands a dataset to the tracker - unless it breaks a dataset rule: then it is held,
        with the reason, and written the next time it is queued and passes."""
        problems = self.store.get().dataset_rules.problems(s.portal, slot.values, self._today())
        if slot.claimed:
            client = self._client_pan(s)
            named = ", ".join(slot.claimed)
            if not client:
                problems.append(f"it was read where PAN {named} is printed; waiting until this session's client is known")
            elif client not in slot.claimed:
                problems.append(f"it was read where only PAN {named} is printed - not this session's client")
        if problems:
            self._outbox.pop(id(slot), None)
            if slot.held != problems:
                slot.held = problems
                self._event(s, "dataset", change="held - not written", values=dict(slot.values), problems=problems)
                self._echo(f"[SGT] (shadow) dataset {self._label(slot.values)} HELD: {'; '.join(problems)}")
            return
        if slot.held:
            slot.held = None
            self._event(s, "dataset", change="released - now passes its checks", values=dict(slot.values))
        self._outbox[id(slot)] = (s, slot)
        self._outbox.move_to_end(id(slot))

    def _queue_all(self, s: _Session, every: bool = False) -> None:
        """The client became known (or their name completed): every row of the session is
        rewritten under the client's key, replacing the rows written before it was known.
        every=True also queues rows never sent (crash recovery)."""
        for slot in s.slots:
            if every or slot.sent_key is not None:
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
        VSDC's row for the same dataset. Stable across sessions for a known client, so seeing
        the same dataset again updates its row instead of adding one.
        """
        def norm(x: Any) -> str:
            return re.sub(r"[^A-Z0-9]", "", str(x or "").upper())
        portal = "GST" if "gst" in (s.portal or "").lower() else "ITR"
        prof = SgtShadow._client_ids(s)
        ident = (prof.get("gstin") or prof.get("pan")) if portal == "GST" else (prof.get("pan") or prof.get("gstin"))
        ident = norm(ident) or f"S{norm(s.session_id)}"
        if values.get("form") and values.get("period"):
            return f"SGT:{portal}:{ident}:{norm(values['form'])}:{norm(values['period'])}"
        return f"SGT:{portal}:{ident}:ARN:{norm(values.get('arn'))}"

    @staticmethod
    def _client_pan(s: _Session) -> Optional[str]:
        """The client's PAN as seen so far (from the PAN, or inside the GSTIN) - confirmed or not."""
        pan = (s.profile.get("pan") or {}).get("value")
        gstin = (s.profile.get("gstin") or {}).get("value")
        return pan or (gstin[2:12] if gstin and len(gstin) == 15 else None)

    @staticmethod
    def _client_ids(s: _Session) -> Dict[str, str]:
        """The client's PAN / GSTIN - only once confirmed; until then the rows stay unattributed."""
        if not s.confirmed:
            return {}
        return {k: s.profile[k]["value"] for k in _CLIENT_KEYS if k in s.profile}

    def _tracker_payload(self, s: _Session, slot: _Slot) -> Dict[str, Any]:
        v = dict(slot.values)
        # Name, PAN and GSTIN say WHO: none of them go on a row until the client is confirmed.
        prof = {k: p["value"] for k, p in s.profile.items() if k not in _CLIENT_KEYS + ("name",)}
        prof.update(self._client_ids(s))
        if s.confirmed and "name" in s.profile:
            prof["name"] = s.profile["name"]["value"]
        key = self.dataset_key(s, v)
        supersedes = slot.sent_key if slot.sent_key and slot.sent_key != key else None
        slot.sent_key = key
        gstin = prof.get("gstin") or ""
        pan = prof.get("pan") or (gstin[2:12] if len(gstin) == 15 else "")
        status = v.get("status") or SUBMIT_LEVELS[0]
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
            "arn": v.get("arn") or "N/A",
            "status": status,
            "filing_date": filing_date,
            "raw_text": "",                         # SGT never stores page text
            "capture_method": CAPTURE_METHOD,
            "identity_resolved": bool(pan),
            "page_url": slot.first_page,
            "dataset_key": key,
            "supersedes_dataset_key": supersedes,
            "raw_payload": {
                "source": {"engine": "SGT", "mode": "shadow", "record": slot.record, "confidence": slot.confidence,
                           "identity": s.confirm_note or "not confirmed yet"},
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
            if held["value"] == value and fld in _CLIENT_KEYS:
                self._sighted(s, fld, url)
                return
            policy = registry.merge_policy(fld) if registry is not None else "latch"
            if held["value"] == value or policy == "latch":
                return
            if not MERGES[policy](held["value"], value):
                if (fld, value) not in s.not_promoted:
                    s.not_promoted.add((fld, value))
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
        s.profile[fld] = {"value": value, "spec": spec, "confidence": confidence, "pages": [url]}
        self._event(s, "profile", field=fld, value=value, spec=spec, confidence=confidence, page=url, source=source)
        self._echo(f"[SGT] (shadow) profile {fld} = {value}  [{spec}]")
        if fld in _CLIENT_KEYS:
            self._sighted(s, fld, url)
        if fld in ("pan", "gstin", "name"):
            self._queue_all(s)                  # rows written before the client was known get their identity
        if fld in ("pan", "gstin"):             # the moment the client is known
            name = (s.profile.get("name") or {}).get("value", "")
            self._hud(s, "identity", "Client identified", f"{name} • {fld.upper()}: {value}".strip(" •"))

    def _sighted(self, s: _Session, fld: str, url: str) -> None:
        """A page showing the client's PAN/GSTIN. The first one attributes the session's rows
        (portals often show it only once); later ones only strengthen the note in the log."""
        pages = s.profile[fld].setdefault("pages", [])
        if url not in pages and len(pages) < 10:
            pages.append(url)
        pan = (s.profile.get("pan") or {}).get("value")
        gstin = (s.profile.get("gstin") or {}).get("value")
        if any(len((s.profile.get(k) or {}).get("pages", [])) >= 2 for k in _CLIENT_KEYS):
            note = f"{fld.upper()} seen on two pages"
        elif pan and gstin and len(gstin) == 15 and gstin[2:12] == pan:
            note = "PAN and GSTIN agree"
        else:
            note = f"{fld.upper()} seen once"
        first = not s.confirmed
        if not first and note == s.confirm_note:
            return
        s.confirmed, s.confirm_note = True, note
        self._event(s, "identity", change="client identified" if first else "identity strengthened", note=note)
        if not first:
            return
        self._echo(f"[SGT] (shadow) client identified ({note})")
        self._queue_all(s)
        for slot in s.slots:
            if slot.held and slot.sent_key is None:
                self._queue(s, slot)            # a card that was waiting for the client

    @staticmethod
    def _other_pans_on_page(s: _Session, res: PageResult, lines: List[str]) -> Set[str]:
        """PANs on this page that are not the client's. A card whose own "PAN :" line was
        misread would otherwise be taken as the client's (found by the noise tests,
        2026-09-22), so while another client's PAN is readable anywhere on the page, its
        dataset cards are not attributed. Only consulted for record cards: the filing wizard's
        own pages legitimately list other PANs (landlord, donee)."""
        mine = set()
        for k in _CLIENT_KEYS:
            v = (s.profile.get(k) or {}).get("value") or getattr(res.profile.get(k), "value", None)
            if v:
                mine.add(v if k == "pan" else v[2:12])
        if not mine:
            return set()
        return {p for ln in lines for p in _PAN_TOKEN.findall(ln or "")} - mine

    @staticmethod
    def _identity_conflict(s: _Session, res: PageResult) -> Optional[str]:
        """The PAN/GSTIN on this page that contradicts the session's client, or None."""
        held = {k: (s.profile.get(k) or {}).get("value") for k in _CLIENT_KEYS}
        seen = {k: getattr(res.profile.get(k), "value", None) for k in _CLIENT_KEYS}
        for k in _CLIENT_KEYS:
            if held[k] and seen[k] and held[k] != seen[k]:
                return k
        pan = held["pan"] or seen["pan"]
        gstin = seen["gstin"] if seen["gstin"] and not held["gstin"] else None
        if pan and gstin and len(gstin) == 15 and gstin[2:12] != pan:
            return "gstin"
        gstin = held["gstin"]
        if gstin and seen["pan"] and not held["pan"] and len(gstin) == 15 and gstin[2:12] != seen["pan"]:
            return "pan"
        return None

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
        arn = values.get("arn")
        if arn:
            for slot in s.slots:
                if slot.arn == arn:
                    return slot
        fp = (values.get("form"), values.get("period"))
        if all(fp):
            for slot in s.slots:
                if slot.form_period == fp and (not arn or not slot.arn):
                    return slot
        return None

    def _other_client(self, s: _Session, values: Dict[str, str]) -> Optional[str]:
        """The field naming a DIFFERENT client than the session's (a card of someone else's), or None."""
        for k in _CLIENT_KEYS:
            held = (s.profile.get(k) or {}).get("value")
            if values.get(k) and held and values[k] != held:
                return k
        return None

    def _apply_identifier(self, s: _Session, values: Dict[str, str]) -> None:
        """
        A captured ARN is a submission: at least "Submitted (Not Verified)" - or more on a
        portal that issues its ARN only later (submit_rules). The submit message, when one was
        read, may already say more; the higher level wins.
        """
        proven = self.store.get().submit_rules.proven_by_identifier(s.portal)
        if values.get("arn") and submit_level(values.get("status")) < submit_level(proven):
            values["status"] = proven
            values["status_evidence"] = ("ARN captured" if submit_level(proven) == 2
                                         else f"ARN captured ({s.portal} issues it only at this level)")

    def _merge(self, s: _Session, ds: Dataset, url: str, source: str, rules: Any = None) -> None:
        values = ds.values()
        other = self._other_client(s, values) or ("page" if self._foreign_pans else None)
        if other:
            self._event(s, "dataset", change="not attributed - another client's", record=ds.record,
                        field=other, page=url, source=source,
                        **({"reason": "another PAN is readable on the page"} if other == "page" else {}))
            self._echo(f"[SGT] (shadow) {ds.record}: names a different {other.upper()} than this session's client - skipped")
            return
        card_pan = values.get("pan") or (values["gstin"][2:12] if len(values.get("gstin") or "") == 15 else None)
        claimed = [card_pan] if card_pan else None
        for k in _CLIENT_KEYS:
            values.pop(k, None)
        if values.get("status"):
            values["status_evidence"] = ds.evidence("status")
        if rules is not None and self._fill_from_draft(s, values, rules):
            self._echo(f"[SGT] (shadow) {ds.record}: form/period taken from the dataset in progress")
            if values.get("arn"):
                # Submitted: the dataset being built is this one, and it is finished.
                s.draft = _Draft()
                self._event(s, "current", change="completed by a submission", arn=values["arn"], page=url)
        self._merge_values(s, values, ds.record, ds.confidence, url, source, claimed=claimed)

    def _merge_values(self, s: _Session, values: Dict[str, str], record: str, confidence: int,
                      url: str, source: str, claimed: Optional[List[str]] = None) -> Optional[_Slot]:
        if not values.get("arn") and not (values.get("form") and values.get("period")):
            return None                         # nothing to key it on - not a dataset yet
        values = dict(values)
        self._apply_identifier(s, values)
        if not values.get("status"):
            values["status"] = SUBMIT_LEVELS[0]     # the ladder's default: nothing shows any work
        slot = self._find_slot(s, values)
        if slot is not None and claimed:
            slot.claimed = sorted(set(slot.claimed or []) | set(claimed))
        if slot is None:
            slot = _Slot(dict(values), record, confidence, url, claimed=claimed)
            s.slots.append(slot)
            self._queue(s, slot)
            self._event(s, "dataset", change="new", record=record, values=values,
                        confidence=confidence, page=url, source=source)
            self._echo(f"[SGT] (shadow) dataset {self._label(values)}  [{record}]")
            if slot.held:
                return slot                     # not written - the pill stays quiet about it
            submitted = submit_level(values.get("status")) >= 2
            detail = f"ARN: {values['arn']} • {values['status']}" if values.get("arn") else values["status"]
            self._hud(s, "submit" if submitted else "capture",
                      "Submission captured" if submitted else "Dataset captured",
                      f"{self._who(s)} • {detail}".strip(" •"), slot.values)
            return slot
        ds_record = record
        changes = {}
        for k, v in values.items():
            old = slot.values.get(k)
            if k == "status_evidence":
                continue                        # travels with the status, below
            if k == "status":
                if old is None or submit_level(v) > submit_level(old):
                    slot.values[k] = v
                    changes[k] = [old, v]
                    if values.get("status_evidence"):
                        slot.values["status_evidence"] = values["status_evidence"]
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
                self._hud(s, "update", "Dataset status updated",
                          f"{changes['status'][0]} → {changes['status'][1]}", slot.values)
        return slot

    @staticmethod
    def _label(values: Dict[str, str]) -> str:
        return " ".join(str(values[k]) for k in ("form", "period", "status", "arn") if values.get(k))

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
            if slot.arn:
                d["live_pipeline_also_captured"] = slot.arn in dispatched
            if slot.held:
                d["held"] = list(slot.held)
            datasets.append(d)
        profile = {k: v["value"] for k, v in s.profile.items()}
        return {
            "portal": s.portal,
            "client_profile": profile,
            "client_known": bool(self._client_ids(s)),
            "identity": s.confirm_note or "not confirmed",
            "datasets": datasets,
            "timeline": list(s.timeline),
            "device_name": device_name(),
        }

    def _finish(self, s: _Session, reason: str) -> None:
        if not s.has_content:
            return
        if not self._client_pan(s):
            # The client was never identified: datasets that were waiting for it are written
            # UNATTRIBUTED (like VSDC247's client-unknown rows) rather than lost. The dataset
            # rules still apply.
            for slot in s.slots:
                if slot.held and slot.claimed:
                    slot.claimed = None
                    self._queue(s, slot)
        # The dataset still being built is dispatched only if it is complete.
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
        quiet = ("shutdown", "switched off", "recovered")
        if not any(q in reason.lower() for q in quiet):
            n = len(payload["datasets"])
            if payload["datasets"] and not payload["client_known"]:
                self._hud(s, "prompt", "SGT session ended - client unknown", f"{n} dataset(s) with no PAN/GSTIN")
            else:
                self._hud(s, "logout", "SGT session logged", f"{who} • {n} dataset(s)")

    # ── Crash safety ─────────────────────────────────────────────────────────────
    STATE_SAVE_EVERY_SEC = 2.0

    def _save_state(self, force: bool = False) -> None:
        """Snapshots every open session (atomic replace), at most every couple of seconds."""
        if self._state_path is None:
            return
        now = self._clock()
        if not force and now - self._state_saved < self.STATE_SAVE_EVERY_SEC:
            return
        self._state_saved = now
        try:
            live = [s for s in self._sessions.values() if s.has_content]
            if not live:
                if self._state_path.exists():
                    self._state_path.unlink()
                return
            snap = {"saved": datetime.now().isoformat(timespec="seconds"),
                    "sessions": [_session_to_json(s) for s in live]}
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._state_path)
        except Exception as e:
            self._echo(f"[SGT] (shadow) could not snapshot sessions: {e}")

    def _recover(self) -> None:
        """Sessions the app never ended (it crashed or was killed): finish them now and re-write
        their datasets - a dataset key is stable, so a row already saved is simply updated."""
        try:
            if not self._state_path.exists():
                return
            snap = json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception as e:
            self._echo(f"[SGT] (shadow) crash snapshot unreadable, ignored: {e}")
            return
        n = 0
        for raw in snap.get("sessions") or []:
            try:
                s = _session_from_json(raw)
            except Exception as e:
                self._echo(f"[SGT] (shadow) could not recover a session: {e}")
                continue
            self._finish(s, "recovered - the app stopped without ending it")
            self._queue_all(s, every=True)
            n += 1
        try:
            self._state_path.unlink()
        except OSError:
            pass
        if n:
            self._echo(f"[SGT] (shadow) recovered {n} session(s) the app did not end; their datasets are re-written")

    # ── Portal-change watch ──────────────────────────────────────────────────────
    def _daily_health_check(self) -> None:
        if self._stats is None:
            return
        today = self._today()
        if self._checked_day == today:
            return
        self._checked_day = today
        for alert in self._stats.alerts(today):
            self._echo(f"[SGT] portal-change watch: {alert['detail']}")
            self._log({"event": "health", **alert})
            if self._notify is not None:
                try:
                    self._notify("prompt", "SGT: a portal may have changed", alert["detail"], {"portal": alert["portal"]})
                except Exception:
                    pass

    def _log(self, rec: Dict[str, Any]) -> None:
        rec = {"ts": datetime.now().isoformat(timespec="seconds"), **rec}
        try:
            d = self._log_dir or shadow_dir()
            d.mkdir(parents=True, exist_ok=True)
            with open(d / f"sgt_shadow_{date.today().isoformat()}.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:
            self._echo(f"[SGT] (shadow) could not write the log: {e}")

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


def _session_to_json(s: _Session) -> Dict[str, Any]:
    return {
        "session_id": s.session_id, "started": s.started, "last_seen": s.last_seen, "portal": s.portal,
        "profile": s.profile, "timeline": s.timeline, "last_url": s.last_url,
        "confirmed": s.confirmed, "confirm_note": s.confirm_note, "strict": s.strict,
        "slots": [{"values": sl.values, "record": sl.record, "confidence": sl.confidence,
                   "first_page": sl.first_page, "sent_key": sl.sent_key, "held": sl.held,
                   "claimed": sl.claimed} for sl in s.slots],
        "draft": {f: {k: v for k, v in piece.items() if isinstance(v, (str, int, float, bool, type(None)))}
                  for f, piece in s.draft.pieces.items()},
        "draft_slot": s.slots.index(s.draft.slot) if s.draft.slot in s.slots else None,
        "draft_claims": list(s.draft.claims),
    }


def _session_from_json(raw: Dict[str, Any]) -> _Session:
    s = _Session(session_id=str(raw["session_id"]), started=float(raw.get("started") or time.time()),
                 last_seen=float(raw.get("last_seen") or time.time()), portal=str(raw.get("portal") or ""))
    s.profile = dict(raw.get("profile") or {})
    s.timeline = list(raw.get("timeline") or [])
    s.last_url = str(raw.get("last_url") or "")
    s.confirmed, s.confirm_note, s.strict = bool(raw.get("confirmed")), str(raw.get("confirm_note") or ""), bool(raw.get("strict"))
    for sl in raw.get("slots") or []:
        s.slots.append(_Slot(dict(sl["values"]), sl.get("record") or CURRENT_RECORD, int(sl.get("confidence") or 0),
                             sl.get("first_page") or "", sl.get("sent_key"), sl.get("held"), sl.get("claimed")))
    s.draft = _Draft(pieces={f: dict(pc) for f, pc in (raw.get("draft") or {}).items()},
                     claims=list(raw.get("draft_claims") or []))
    idx = raw.get("draft_slot")
    if isinstance(idx, int) and 0 <= idx < len(s.slots):
        s.draft.slot = s.slots[idx]
    return s
