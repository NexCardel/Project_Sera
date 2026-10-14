"""
core/sgt/sgt_shadow.py — the SGT engine (live or shadow)
========================================================
(The module keeps its first name; since 2026-09-26 SGT live is the main capture engine.)

Runs on every in-scope page, builds each session's client profile and datasets, and writes
every dataset to the tracker dump as soon as it can be keyed, and again whenever it changes,
so nothing waits for the session to end. Its captures show on the HUD pill - only real
captures, never page reads. Everything is also written to a local log.

  live      capture method "SGT_live", the tracker's canonical keys (core/dataset_key.py): a
            dataset another engine captured earlier is the same row, and its status never
            moves down. HUD tag "SGT (Live)". A submission whose client never became known is
            written unattributed AND raises the phone alert (VSDC247's client-unknown path).
  shadow    capture method "SGT_shadow", keys in an "SGT:" namespace: rows kept apart from the
            other engines' so the two can be compared - a shadow row can never replace, drop or
            purge another engine's row, nor be removed by one. HUD tag "SGT (Shadow)".

Per browser window (one client per session, two windows = two sessions):

  read      UIA first; OCR of the screenshot when UIA comes back blind (a canvas-painted
            page shows UIA nothing, or only Flutter's "Enable accessibility" button), on
            every portal - OCR only ever adds (SOURCE_POLICY). With the capture loop
            (core/vsdc/vsdc_capture) a read is asked for by observe_window's session and
            absorbed into that session when it comes back (absorb).
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
  session   ends on a login/logout keyword in the link, 2 hours idle, a closed window or app
            quit - nothing else. Another tab (another portal, another client of the same
            portal) only sets the session aside; it comes back when the window returns to it.
            The session's payload is logged then, with a note of which of its ARNs the
            other engines also dispatched - the comparison shadow mode exists for.
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

import collections
import copy
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
from .sgt_containers import (KEYED_MINIMUM, Carrier, ContainerSession, Instance, evidence_of, info_signature,
                             load_doc, sdis_info, split_hits)
from core.dataset_key import SDIS_INFO_METHOD, compute_dataset_key

SHADOW_DIR_ENV = "SGT_SHADOW_DIR"
MODE_SHADOW = "shadow"
MODE_LIVE = "live"
# Per mode: the HUD pill colours the tag (ui/components/vsdc_hud_pill.py); the tracker colours
# and can hide rows whose capture method starts "SGT".
HUD_TAGS = {MODE_SHADOW: "SGT (Shadow)", MODE_LIVE: "SGT (Live)"}
CAPTURE_METHODS = {MODE_SHADOW: "SGT_shadow", MODE_LIVE: "SGT_live"}
HUD_TAG = HUD_TAGS[MODE_SHADOW]
CAPTURE_METHOD = CAPTURE_METHODS[MODE_SHADOW]
SESSION_QUIET_SEC = 600.0       # a session with content touched this recently holds back a mandatory update
IDLE_END_SEC = 4 * 3600        # owner D13: one client survives a whole workday of pauses
IDLE_REASON = "idle 4 hr"
CLIENT_LINK_SPEC = "client_db:user_id"       # the "spec" of a PAN / GSTIN taken from the saved client that owns the portal user ID
IDENTITY_PROBLEM = "form and period are not known yet"      # why an ack-only row waits (see _queue)
RESUME_WINDOW_SEC = 12 * 3600   # a parked client (also on disk, sessions_state.json) can be resumed this long
# What a read may change, by where its text came from (SGT never-miss blueprint 5.2 with R6). OCR
# is allowed on every portal but only ever ADDS: it never sets the client or any profile field,
# fills a piece of the dataset in progress only where none exists (a canvas read loses label
# markers such as "Period •", which once cleared values UIA had just set: GST, 2026-10-06), and
# creates a row or raises a status only together with that dataset's own ARN/ack.
SOURCE_POLICY = {
    "uia": {"profile": "all", "pieces": "set_change_clear", "datasets": "merge_follow", "identity_check": True},
    "ocr": {"profile": "none", "pieces": "fill_empty_only", "datasets": "merge_add_only", "identity_check": False},
}
BLIND_RETRIES = 2               # a page that read fine before is read again this often before OCR
BLIND_RETRY = "blind-retry"     # absorb()'s answer: the capture loop asks for the page again
RESULT_MAX_AGE_SEC = 60.0       # as core/vsdc/vsdc_reader: an older result is dropped, never absorbed
OWNERS_KEPT = 500               # requests remembered (request_made) at most
CLOSED_RING = 20                # ended sessions a late result may still land in...
CLOSED_KEEP_SEC = 300.0         # ...for this long
# Only these ends let a late result in (R1): the next page in that tab is still the same login.
# Never a logout, a login page or another client's PAN/GSTIN - the read may show the next client.
ABSORB_AFTER_END = ("idle", "window closed")
REREAD_AFTER_SEC = 15.0
MIN_UIA_LINES = 3               # fewer than this and the page is treated as blind
MISSES_TO_CLEAR = 2             # reads in a row a piece must be missing from its page to be cleared
MISSING_CLEAR_SEC = 5.0         # ...and for at least this long
TIMELINE_CAP = 200
CURRENT_RECORD = "current_dataset"  # the record name a dataset built across pages is written under
SDIS_CARRIER_FORM = "SDIS info"     # the form in a carrier row's key (blueprint S.4); never a dataset
# How the pill names the client datapoints it has just captured.
PROFILE_LABELS = {"name": "name", "first_name": "first name", "middle_name": "middle name",
                  "last_name": "last name", "pan": "PAN", "gstin": "GSTIN", "dob": "date of birth",
                  "email": "email", "phone": "phone", "tan": "TAN", "address": "address",
                  "aggregate_turnover": "aggregate turnover"}
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
    alerted: bool = False               # live: the client-unknown phone alert went out for it
    # field -> the page that last set it. A value belongs to the page that supplied it: when that same
    # page later shows a different one, the row follows it (a corrected misread, a status that changed).
    owner: Dict[str, str] = field(default_factory=dict)
    waived: bool = False                # written without form + period: the session ended before they came
    lowered: bool = False               # its status was just lowered on purpose: the tracker must accept it
    confirmed: bool = False             # ... and the user said yes to it (a complete row is lowered only then)
    demotion_asked: Set[str] = field(default_factory=set)   # (status, page visit) pairs already put to the user
    # "uia" | "ocr" | "recovered": what the row rests on now. An OCR-only row becomes "uia" once a UIA
    # read shows the same ARN. first_seen_as: what first saw it.
    evidence_source: str = "uia"
    first_seen_as: str = ""

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
    # The last period this dataset had. A piece can be cleared for a moment while a page redraws;
    # the "a different period was opened" check must still compare against what it was.
    last_period: str = ""

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
    started: float = 0.0                # on SgtShadow's clock (set when the session is first seen)
    last_seen: float = 0.0
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
    ocr_skips: int = 0                  # always 0 since OCR runs on every portal (kept for old logs)
    good_lines: Dict[str, int] = field(default_factory=dict)    # page -> lines of its last good UIA read
    visit: int = 0                      # +1 whenever the page link changes: one stay on one page
    untied: Set[str] = field(default_factory=set)   # page visits whose bare submit message was already reported
    draft: _Draft = field(default_factory=_Draft)
    # (field, value) pairs already logged as "not promoted": a truncated header name is seen on
    # every page, and one log line about it is enough.
    not_promoted: Set[Tuple[str, str]] = field(default_factory=set)
    linked: Set[str] = field(default_factory=set)   # portal user IDs already looked up in the saved clients
    confirmed: bool = False             # the client's identity may go on rows
    confirm_note: str = ""
    browser: str = ""                   # the window's browser ('chrome', 'msedge', 'firefox'); on every row and log line
    # Born from a contradiction (another client's PAN/GSTIN with no session of its own here). The
    # values that caused it are not taken from the read that caused it. When the tab is known, not
    # from that page's re-reads either (strict_block) - a stray PAN/GSTIN (a supplier in a table)
    # is the same evidence on every re-read; see the clash branch of _observe for why not without
    # a tab id. Any OTHER page that shows them names it at once (one sighting, user rule 2026-09-22).
    strict: bool = False
    strict_block: Dict[str, Any] = field(default_factory=dict)    # {"url": ..., "values": {key: value}}
    # The browser tabs this session is the one for (vsdc_uia_text.selected_tab_id). A tab opened
    # from it joins it (same login); switching to another tab brings that tab's own session back.
    tabs: Set[str] = field(default_factory=set)
    unread_tabs: Set[str] = field(default_factory=set)   # joined, not read yet (see _observe's clash)
    # SDIS dataset containers and Others values (core/sgt/sgt_containers.py, blueprint S.2/S.4)
    sdis: ContainerSession = field(default_factory=ContainerSession)

    @property
    def has_content(self) -> bool:
        # A lone login-box user name (no client, no dataset) is not a session worth ending or setting aside.
        return bool(any(k != "username" for k in self.profile) or self.slots or self.draft.pieces
                    or self.sdis.has_content)


class SgtShadow:
    """One per router. observe() is called once per in-scope tick; it never raises."""

    def __init__(
        self,
        store: Optional[SpecStore] = None,
        read_uia: Optional[Callable[[int], Dict[str, Any]]] = None,
        read_nodes: Optional[Callable[[int], Dict[str, Any]]] = None,
        dispatched_ids: Optional[Callable[[], Iterable[str]]] = None,
        log_dir: Optional[Path] = None,
        clock: Optional[Callable[[], float]] = None,
        today: Callable[[], date] = date.today,
        echo: Callable[[str], None] = print,
        notify: Optional[Callable[[str, str, str, Dict[str, Any]], None]] = None,
        recorder: Any = None,
        stats: Any = None,
        state_path: Optional[Path] = None,
        mode: str = MODE_SHADOW,
        alert_unattributed: Optional[Callable[[str, str], Any]] = None,
        intelligence: Any = None,
        scc: Any = None,
        sdis: Any = None,
        ask_demotion: Optional[Callable[[Dict[str, Any]], Any]] = None,
        resolve_client: Optional[Callable[[str, str], Optional[Dict[str, Any]]]] = None,
        on_read: Optional[Callable[[bool, float, str], None]] = None,
        on_hits: Optional[Callable[[str, bool, bool], None]] = None,
        on_ocr_row: Optional[Callable[[], None]] = None,
    ) -> None:
        self.mode = MODE_SHADOW
        # Capture health (core/vsdc/vsdc_health): on_read(usable, ms, source) after each page read
        # (source "uia", "ocr" or "busy"); on_hits(session id, any datapoint yet, logged-in page);
        # on_ocr_row() when a dataset row is first created from an OCR read.
        self._on_read = on_read
        self._on_hits = on_hits
        self._on_ocr_row = on_ocr_row
        # (portal, portal user ID) -> {"gstin", "pan"} of the saved client who owns it, or None
        # (core/sgt/sgt_client_link.ClientLink). Names the client where the page itself does not.
        self._resolve_client = resolve_client
        # A page now shows a LOWER status than a filing that has an ARN: never applied on its own. The
        # user is asked (this callback shows the dialog); confirm_demotion / decline_demotion answer.
        self._ask_demotion = ask_demotion
        self._demotions: Dict[str, Tuple[_Session, _Slot, str, str]] = {}
        self._demotion_answers: "collections.deque[Tuple[str, bool]]" = collections.deque()   # from the UI thread
        # SDIS's own recorder (core.sdis.recorder.SdisRecorder), or None. It is only offered the
        # pages SGT's change gate let through, after SGT's own read; see _observe.
        self._sdis = sdis
        # SGT-I (core.sgt_i.SgtIntelligence), or None. It only ever gets copies; see _hand_to_sgt_i.
        self._sgt_i = intelligence
        # SCC-U (core.scc.SccHost), or None. Copies only, like SGT-I; see _hand_to_scc.
        self._scc = scc
        self.set_mode(mode)
        # live: (form, page) of a submission written with no client - the phone alert
        self._alert_unattributed = alert_unattributed
        self.store = store or SpecStore()
        self._recorder = recorder           # sgt_corpus.PageRecorder, or None
        self._stats = stats                 # sgt_health.SpecStats, or None
        self._state_path = state_path       # crash snapshot; None = not kept (tests, replay)
        self._state_saved = 0.0
        self._state_dirty = False           # a change the 2 s throttle held back from the snapshot
        self._log_checked: Set[str] = set() # log files already checked for a torn last line
        self._checked_day: Optional[date] = None
        self._digest: Optional[Callable[[date], Optional[Dict[str, Any]]]] = None
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
        # SGT-I's own extra read (blueprint 14.2's "shared read" is not adopted yet): only ever
        # called for the corpus, only while SGT-I is on - see _nodes_for_recording.
        if read_nodes is None:
            def read_nodes(hwnd: int) -> Dict[str, Any]:
                from core.sgt_i.uia_nodes import read_page_nodes
                return read_page_nodes(hwnd)
        self._read_nodes = read_nodes
        self._dispatched_ids = dispatched_ids or (lambda: ())
        self._log_dir = log_dir
        # Idle and parking maths run on a monotonic clock (a wall-clock jump must not end or keep a
        # session); wall time is only for log/row stamps and for a parked entry's age on disk.
        self._monotonic = clock is None
        self._clock = clock or time.monotonic
        self._today = today
        self._echo = echo
        self._sessions: Dict[int, _Session] = {}
        # Sessions of a window that are set aside, not ended: the same window shows another portal
        # (a GST tab, an Income Tax tab, a mail tab) or another client of the same portal. Only a
        # login or logout page, the idle limit, shutdown and a closed window end a session.
        self._shelf: Dict[int, List[_Session]] = {}
        self._parked: Dict[int, List[Tuple[float, Dict[str, Any]]]] = {}
        self._foreign_pans: Set[str] = set()     # other PANs readable on the page being absorbed
        self._new_profile: List[str] = []        # datapoints captured while absorbing this page
        self._page_pans: Set[str] = set()        # every PAN readable on it
        # Tabs whose session ended (insertion-ordered, capped): seen again, they start a session of
        # their own instead of joining the one in view (_retire_tabs).
        self._retired_tabs: Dict[str, None] = {}
        # Counts since the last capture-worker heartbeat (core/vsdc/vsdc_diag, logs/vsdc.log):
        # counts and the portal only, never a page's text.
        self._diag: Dict[str, Any] = self._fresh_diag()
        self._sdis_doc: Optional[Dict[str, Any]] = None   # the containers document instances were built under
        # The capture loop's reads (blueprint 6.1): ReadContext.seq -> [session id, asked_at, cut]. A
        # result lands in the session that asked, never in the one in view when it comes back. `cut`
        # names the boundary (logout, login page, another client) that session crossed since: then
        # the result is dropped, because the page it read may already be the next client's (R1).
        self._owners: "OrderedDict[int, List[Any]]" = OrderedDict()
        # Ended sessions, for a result that comes back after its session ended: id -> (ended, session, reason)
        self._closed: "OrderedDict[str, Tuple[float, _Session, str]]" = OrderedDict()
        self._policy: Dict[str, str] = SOURCE_POLICY["uia"]    # the source policy of the read being absorbed
        self._url_changed = False                # the last observe_window moved its session to another page
        if self._state_path is not None:
            self._recover()

    def _stamp(self, now: float) -> float:
        """A reading of SgtShadow's clock as a wall-clock time, for the page corpus (replay sorts by it)."""
        return now + (time.time() - time.monotonic()) if self._monotonic else now

    # ── Mode ─────────────────────────────────────────────────────────────────────
    def set_mode(self, mode: str) -> None:
        """"live" or "shadow". Switch only between sessions (the router ends them first):
        the mode decides how a row is keyed."""
        self.mode = MODE_LIVE if str(mode).strip().lower() == MODE_LIVE else MODE_SHADOW

    def set_intelligence(self, host: Any) -> None:
        """Attaches SGT-I's host (or None). It never changes what the Core captures."""
        self._sgt_i = host

    def set_client_lookup(self, lookup: Optional[Callable[[str, str], Optional[Dict[str, Any]]]]) -> None:
        """Attaches the saved-client lookup by portal user ID (or None)."""
        self._resolve_client = lookup

    def set_scc(self, host: Any) -> None:
        """Attaches SCC-U's host (or None). It never changes what the Core captures."""
        self._scc = host

    @property
    def live(self) -> bool:
        return self.mode == MODE_LIVE

    @property
    def _tag(self) -> str:
        return f"({self.mode})"

    # ── Entry points ─────────────────────────────────────────────────────────────
    def observe(self, hwnd: int, portal: Optional[str], url: str, frame: Any = None,
                ocr: Any = None, title: str = "", tab: Optional[str] = None,
                read: Optional[Dict[str, Any]] = None) -> Optional[PageResult]:
        """observe_window and, when the page changed, a read of it absorbed at once (replay, tests,
        and anything without a capture loop). tab: the id of the browser tab in front (None = not
        known: the window is one session, as before tabs were told apart). read: a page read
        already made ({"lines", "docs", "ms"}) - absorbed as it is, with no read and no change gate."""
        try:
            return self._observe(hwnd, portal or "", url or "", frame, ocr, title or "", tab or None, read)
        except Exception as e:                  # shadow mode must never disturb capture
            self._echo(f"[SGT] {self._tag} error, page skipped: {e}")
            return None

    def observe_window(self, hwnd: int, tab: Optional[str], portal: Optional[str], url: str,
                       title: str = "") -> Optional[_Session]:
        """Session bookkeeping for the window in view (tab switch, portal switch, login/logout link,
        idle, parked resume, claiming the tab) - no read. The session in view, or None on a logout
        page (nothing about a client is read there)."""
        try:
            return self._observe_window(hwnd, portal or "", url or "", tab or None, self._clock())
        except Exception as e:
            self._echo(f"[SGT] {self._tag} error, window skipped: {e}")
            return None

    def request_made(self, ctx: Any, s: Optional[_Session] = None) -> None:
        """The capture loop asked for a page read (a vsdc_reader.ReadContext) while `s` (default: the
        session in view in ctx.hwnd) was the window's session: its result belongs to that session."""
        try:
            s = s if s is not None else self._sessions.get(ctx.hwnd)
            if s is None:
                return
            asked = float(ctx.asked_at or 0.0)
            self._owners[ctx.seq] = [s.session_id, asked, ""]
            while self._owners:
                seq, (_, at, _) = next(iter(self._owners.items()))
                if len(self._owners) <= OWNERS_KEPT and asked - at <= RESULT_MAX_AGE_SEC:
                    break
                self._owners.pop(seq)
        except Exception as e:
            self._echo(f"[SGT] {self._tag} request not noted: {type(e).__name__}")

    def absorb(self, result: Any, frame: Any = None, ocr: Any = None, now: Optional[float] = None,
               blind_retries: int = 0) -> Any:
        """Resolves and merges one finished read (a vsdc_reader.ReadResult) into the session that asked
        for it (request_made) - in view, set aside, or ended by idle or a closed window - under the
        SOURCE_POLICY of its source. Dropped: an error, a torn read (R1), a result older than
        RESULT_MAX_AGE_SEC (now: the reader's clock), an unknown owner, an owner that crossed a
        boundary since. frame/ocr: the screenshot taken when the read was asked, for a blind page.
        blind_retries: how often the loop already re-read this page. Returns the PageResult, None,
        or BLIND_RETRY (a page that read fine before came back blind: ask again before OCR)."""
        try:
            return self._absorb_result(result, frame, ocr, now, blind_retries)
        except Exception as e:
            self._echo(f"[SGT] {self._tag} error, read skipped: {e}")
            return None

    def wants_read(self, hwnd: int) -> bool:
        """SGT-I / SCC-U ask for another read of the session in view in this window (advisory, as
        inside _observe; the capture loop's change gate asks this)."""
        try:
            s = self._sessions.get(hwnd)
            if s is None:
                return False
            sgt_i, scc = self._sgt_i, self._scc
            return bool((sgt_i is not None and sgt_i.wants_read(s.session_id))
                        or (scc is not None and scc.wants_read(s.session_id)))
        except Exception:
            return False

    def end_session(self, hwnd: int, reason: str, keep_parked: bool = False) -> None:
        """Ends EVERY session of this window - the one in view and the ones set aside. For a
        closed window, shutdown and switching SGT off; a login / logout page or the idle limit
        end only the session they belong to (``_end_one``). Sessions parked from this window are
        forgotten too, unless keep_parked (shutdown: they resume after a restart)."""
        if not keep_parked:
            self._parked.pop(hwnd, None)
        s = self._sessions.pop(hwnd, None)
        aside = self._shelf.pop(hwnd, [])
        for x in ([s] if s is not None else []) + aside:
            self._finish(x, reason)
        if s is not None or aside:
            self._save_state(force=True)
        if self._stats is not None:
            self._stats.save()

    def window_gone(self, hwnd: int) -> None:
        """The window is really closed (IsWindow false): every session of it ends, none is parked."""
        self.end_session(hwnd, "window closed")

    def window_idle(self, hwnd: int) -> None:
        """The window still exists but its sessions sat unseen too long (the user worked elsewhere): a
        session with a client is parked - the same client, named again by any window, resumes its
        draft - and the others end. Never raises."""
        try:
            now = self._clock()
            sessions = ([self._sessions.pop(hwnd)] if hwnd in self._sessions else []) + self._shelf.pop(hwnd, [])
            for s in sessions:
                self._park(hwnd, s, now)
            if sessions and self._stats is not None:
                self._stats.save()
        except Exception as e:
            self._echo(f"[SGT] {self._tag} error, idle window skipped: {e}")

    def end_all(self, reason: str) -> None:
        for hwnd in list(self._sessions) + [h for h in self._shelf if h not in self._sessions]:
            self.end_session(hwnd, reason, keep_parked=True)
        self._save_state(force=True)            # parked clients stay on disk for the next start

    def _end_one(self, s: _Session, reason: str) -> None:
        """Ends one session; the window's other sessions carry on."""
        self._finish(s, reason)
        self._save_state(force=True)
        if self._stats is not None:
            self._stats.save()

    def _all_sessions(self) -> List[_Session]:
        return list(self._sessions.values()) + [x for lst in self._shelf.values() for x in lst]

    def sessions_open(self) -> bool:
        """True while some session with content was touched in the last SESSION_QUIET_SEC: an update
        restart now could cut a client's work in two."""
        try:
            now = self._clock()
            return any(s.has_content and now - s.last_seen < SESSION_QUIET_SEC for s in self._all_sessions())
        except Exception:
            return True

    def _shelve(self, hwnd: int, s: _Session) -> None:
        """Sets the window's session in view aside. An empty one has nothing to keep."""
        if s.has_content:
            self._shelf.setdefault(hwnd, []).append(s)

    def _bring_back(self, hwnd: int, back: _Session, url: str, h: Any, now: float) -> _Session:
        """`back` (a session this window had set aside) is the one in view again."""
        self._sessions[hwnd] = back
        back.last_url, back.last_hash, back.last_read, back.last_seen = url, h, now, now
        if url and (not back.timeline or back.timeline[-1] != url) and len(back.timeline) < TIMELINE_CAP:
            back.timeline.append(url)
        self._echo(f"[SGT] {self._tag} back on this window's session for the client on this page")
        return back

    def _unshelve(self, hwnd: int, portal: str, key: Optional[str] = None,
                  value: Optional[str] = None, tab: Optional[str] = None) -> Optional[_Session]:
        """The most recently seen set-aside session of this window for `portal` (and, with
        `key`/`value`, for that client PAN / GSTIN), taken off the shelf - or None. With `tab`,
        only a session that is that tab's own, or one that never learnt its tabs; a client match
        (`key`) needs no tab - the same client's page belongs to its session in any tab."""
        aside = self._shelf.get(hwnd) or []
        fit = [x for x in aside
               if (not portal or not x.portal or x.portal == portal)
               and (key is None or (x.profile.get(key) or {}).get("value") == value)
               and (key is not None or not tab or not x.tabs or tab in x.tabs)]
        if not fit:
            return None
        pick = max(fit, key=lambda x: x.last_seen)
        aside.remove(pick)
        if not aside:
            self._shelf.pop(hwnd, None)
        return pick

    def _claim_tab(self, hwnd: int, s: _Session, tab: Optional[str]) -> None:
        """`tab` is s's from now on - and no other session's of this window."""
        if not tab:
            return
        s.tabs.add(tab)
        self._retired_tabs.pop(tab, None)
        for x in self._shelf.get(hwnd) or []:
            if x is not s:
                x.tabs.discard(tab)

    def _retire_tabs(self, s: _Session) -> None:
        """s has ended: a tab of its, seen again, is not joined to whatever session is in view -
        its page is from a login that is over (or from the one that replaced it), so it starts a
        session of its own that names its client only from what it shows."""
        for t in s.tabs:
            self._retired_tabs[t] = None
        while len(self._retired_tabs) > 500:
            self._retired_tabs.pop(next(iter(self._retired_tabs)))

    # ── One tick ─────────────────────────────────────────────────────────────────
    def _observe(self, hwnd: int, portal: str, url: str, frame: Any, ocr: Any, title: str = "",
                 tab: Optional[str] = None, read: Optional[Dict[str, Any]] = None) -> Optional[PageResult]:
        now = self._clock()
        s = self._observe_window(hwnd, portal, url, tab, now)
        if s is None:
            return None
        h = _frame_hash(frame)
        changed = (read is not None or self._url_changed or h is None or h != s.last_hash
                   or (now - s.last_read) >= REREAD_AFTER_SEC)
        sgt_i = self._sgt_i    # one read: Settings may detach it from another thread mid-tick
        if not changed and sgt_i is not None:
            changed = sgt_i.wants_read(s.session_id)   # SGT-I may only ask for MORE reads
        scc = self._scc        # one read, as sgt_i
        if not changed and scc is not None:
            changed = scc.wants_read(s.session_id)     # SCC-U too: at most 30 s after a copy
        prev_hash, prev_read = s.last_hash, s.last_read
        s.last_hash = h
        if not changed:
            return None
        s.last_read = now

        t0 = time.perf_counter()
        res = read if read is not None else (self._read_uia(hwnd) or {})
        if res.get("busy"):
            # The previous UIA read is still running (a slow page, not a hung one): this page was
            # not read at all, so it must not count as read - the next tick asks again.
            s.last_hash, s.last_read = prev_hash, prev_read
            self._diag["busy"] += 1
            self._report(self._on_read, False, 0.0, "busy")
            return None
        out = self._absorb_lines(hwnd, s, portal, url, title, tab, list(res.get("lines") or []), "uia",
                                 docs=(read.get("docs") if read is not None else None), own_read=read is None,
                                 read_ms=float(res.get("ms") or 0.0) if read is not None else 0.0, t0=t0,
                                 frame=frame, ocr=ocr, now=now, h=h, blind_retries=BLIND_RETRIES, in_view=True)
        return out if isinstance(out, PageResult) else None

    def _absorb_result(self, r: Any, frame: Any, ocr: Any, now: Optional[float],
                       blind_retries: int) -> Any:
        t0 = time.perf_counter()
        when = self._clock()
        self._prune_closed(when)
        ctx = r.ctx
        owner = self._owners.pop(ctx.seq, None)
        why = ""
        if getattr(r, "error", ""):
            why = "error"
        elif r.torn:
            why = f"torn ({r.torn})"            # R1: both kinds; the loop asks again
        elif now is not None and now - float(ctx.asked_at or r.started_at or 0.0) > RESULT_MAX_AGE_SEC:
            why = "too old"
        elif owner is None:
            why = "owner unknown"
        elif owner[2]:
            why = f"its session crossed a boundary ({owner[2]})"
        hwnd = ctx.hwnd
        s, in_view, ended = None, False, ""
        if not why:
            s = self._sessions.get(hwnd)
            in_view = s is not None and s.session_id == owner[0]
            if not in_view:
                s = next((x for x in self._shelf.get(hwnd) or [] if x.session_id == owner[0]), None)
            if s is None and owner[0] in self._closed:
                _, s, ended = self._closed[owner[0]]
                if not _absorbs_after(ended):
                    s, why = None, f"its session ended ({ended})"
            if s is None and not why:
                why = "its session is gone"
            elif s is not None and not why and r.tab_before and s.tabs and r.tab_before not in s.tabs:
                why = "the page read is another tab's"       # asked for one tab, the other was in front
        from core.vsdc import vsdc_scope
        url = vsdc_scope.sanitize_page_url(r.url) if r.url else ""
        if not why and _LOGOUT.search(url):
            why = "logout page"
        if why:
            self._diag["dropped"] += 1
            if s is not None or owner is not None and owner[2]:
                self._echo(f"[SGT] {self._tag} a read was not absorbed: {why}")
            return None
        portal = vsdc_scope.portal_for_url(url) or ctx.portal or s.portal
        if in_view:
            s.last_read = when                  # SGT's clock; the result's own times are the reader's
        out = self._absorb_lines(hwnd, s, portal, url, ctx.title or "", r.tab_after, list(r.lines or []),
                                 r.source or "uia", docs=r.docs, own_read=False, read_ms=r.ms, t0=t0,
                                 frame=frame, ocr=ocr, now=when, h=None, blind_retries=blind_retries,
                                 in_view=in_view)
        if ended and isinstance(out, PageResult):
            # Late, after an idle end or a closed window: the read was asked while this session was the
            # window's, and its tab and page did not change during it. Its rows go out as usual.
            self._close_draft(s, self.store.get().current_rules, f"late result after session end ({ended})", url)
            self._event(s, "late_absorbed_after_end", ended=ended, page=url, source=r.source or "uia")
        return out

    def _cut(self, session_id: str, why: str) -> None:
        """`session_id` crossed a boundary: a read it asked for and has not had back is never absorbed."""
        for o in self._owners.values():
            if o[0] == session_id and not o[2]:
                o[2] = why

    def _prune_closed(self, now: float) -> None:
        while self._closed:
            sid, (ended, _, _) = next(iter(self._closed.items()))
            if len(self._closed) <= CLOSED_RING and now - ended <= CLOSED_KEEP_SEC:
                break
            self._closed.pop(sid)

    def _observe_window(self, hwnd: int, portal: str, url: str, tab: Optional[str], now: float) -> Optional[_Session]:
        if self._state_dirty:
            self._save_state()              # the last change before a still page must not wait for the next one
        self._apply_demotion_answers()
        self._end_idle(now)
        self._daily_health_check()
        self._prune_closed(now)
        s = self._sessions.get(hwnd)
        if tab and s is not None and s.tabs and tab not in s.tabs:
            # The window shows another of its tabs. Each tab has its own session (2026-10-13): a
            # page that names no client must never feed the client of the tab the user came from.
            owner = self._unshelve(hwnd, "", tab=tab) if any(
                tab in x.tabs for x in self._shelf.get(hwnd) or []) else None
            if owner is not None:
                self._shelve(hwnd, s)
                s = self._sessions[hwnd] = owner
                self._echo(f"[SGT] {self._tag} another tab: back on its own session")
            elif tab in self._retired_tabs:
                self._shelve(hwnd, s)
                self._sessions.pop(hwnd, None)
                s = None
                self._echo(f"[SGT] {self._tag} a tab whose session ended: a new session for it")
            else:
                # A tab never seen before: opened from the session in view, under the same login -
                # it joins that session (claimed below). Until its first page is read: one that
                # names another client makes it that client's tab (a tab open before Sera started).
                s.unread_tabs.add(tab)
        if s is not None and portal and s.portal and portal != s.portal:
            # The window is on another portal (another tab). That is NOT a boundary: this
            # session is set aside, and the one the window had on that portal comes back. An
            # empty session (say, opened on the GST login page before going to the ITR portal)
            # just takes the portal it is on.
            back = self._unshelve(hwnd, portal, tab=tab)
            if s.has_content:
                self._shelve(hwnd, s)
                s = back
            elif back is not None:
                s = back
            else:
                s.portal = portal
            if s is None:
                self._sessions.pop(hwnd, None)
            else:
                self._sessions[hwnd] = s
        if s is not None and url and url != s.last_url:
            if _LOGOUT.search(url):
                if len(s.timeline) < TIMELINE_CAP:
                    s.timeline.append(url)      # how the session ended belongs on its timeline
                self._sessions.pop(hwnd, None)
                self._end_one(s, "logout")
                s = None
            elif _LOGIN.search(url) and s.has_content:
                self._sessions.pop(hwnd, None)
                self._end_one(s, "login page - next client")
                s = None
        if s is None and not (url and (_LOGIN.search(url) or _LOGOUT.search(url))):
            # A session of this window that was set aside for this portal comes back.
            s = self._unshelve(hwnd, portal, tab=tab)
            if s is not None:
                self._sessions[hwnd] = s
        if s is None:
            resumed = False
            picked = None
            cands = self._parked.get(hwnd) or []
            if cands:
                # A snapshot read back from disk is never matched by window number: the number may now
                # belong to another window. Only the client, named by a page, brings it back.
                fit = [c for c in cands
                       if not c[1].get(_RESTORED)
                       and (not portal or not c[1].get("portal") or portal == c[1].get("portal"))
                       and (not tab or not c[1].get("tabs") or tab in c[1]["tabs"])]
                rest = [c for c in cands if c not in fit]
                if rest:
                    self._parked[hwnd] = rest
                else:
                    self._parked.pop(hwnd, None)
                if fit:
                    picked = max(fit, key=lambda c: float(c[1].get("last_seen") or c[0]))
            if picked is not None:
                parked_at, snap = picked
                age = now - parked_at
                gap = now - float(snap.get("last_seen") or parked_at)
                url_ok = not (_LOGIN.search(url) or _LOGOUT.search(url)) if url else True
                if 0 <= age <= RESUME_WINDOW_SEC and gap <= RESUME_WINDOW_SEC and url_ok:
                    resumed = True
                    s = _session_from_json(snap)
                    old_id = snap.get("session_id", s.session_id)
                    s.session_id = uuid.uuid4().hex[:12]
                    s.started = now
                    s.last_seen = now
                    s.slots = []
                    s.draft.slot = None
                    if portal:
                        s.portal = portal
                    if s.sdis is not None:
                        if s.sdis.instances:
                            s.sdis.instances = [inst for inst in s.sdis.instances if not inst.sent_key]
                            s.sdis.current = {k: v for k, v in s.sdis.current.items() if v in s.sdis.instances}
                        if s.sdis.carrier and s.sdis.carrier.sent_key:
                            s.sdis.carrier = None
                    self._sessions[hwnd] = s
                    idle_sec = max(0.0, round(gap, 1))
                    self._event(s, "session_resumed", from_session=old_id, idle_sec=idle_sec)
                    self._echo(f"[SGT] {self._tag} session resumed (from session {old_id}, idled {int(idle_sec)}s)")
            if not resumed:
                s = self._sessions[hwnd] = _Session(portal=portal)
        if not s.portal:
            s.portal = portal
        if not s.browser:
            s.browser = _browser_of(hwnd)
        s.last_seen = now
        if not s.started:
            s.started = now
        self._claim_tab(hwnd, s, tab)

        url_changed = url != s.last_url
        self._url_changed = url_changed         # for observe()'s change gate
        if url_changed:
            s.last_url = url
            s.visit += 1
            if url and (not s.timeline or s.timeline[-1] != url) and len(s.timeline) < TIMELINE_CAP:
                s.timeline.append(url)
        if _LOGOUT.search(url):
            return None                         # nothing about a client lives on a logout page
        # Login pages ARE read: the ITR password page shows the PAN being logged into. The
        # login keyword above already closed the previous client's session, so what is read
        # here belongs to the new one.
        return s

    def _absorb_lines(self, hwnd: int, s: _Session, portal: str, url: str, title: str, tab: Optional[str],
                      lines: List[str], source: str, docs: Optional[List[Any]], own_read: bool, read_ms: float,
                      t0: float, frame: Any, ocr: Any, now: float, h: Any, blind_retries: int,
                      in_view: bool) -> Any:
        """One page read of `s`, from its lines on. in_view: `s` is the window's session in view (not
        one set aside or ended since the read was asked: then it never switches the window's session)."""
        sgt_i = self._sgt_i    # one read: Settings may detach it from another thread mid-tick
        scc = self._scc        # likewise; also the host the page is handed to below
        self._diag["reads"] += 1
        self._diag["portal"] = portal or "?"
        if source == "uia" and is_blind(lines):
            if blind_retries < BLIND_RETRIES and s.good_lines.get(url, 0) > MIN_UIA_LINES:
                # This page read fine before: most likely a half-drawn page (Chromium after idle).
                self._report(self._on_read, False, read_ms, source)
                return BLIND_RETRY
            s.blind_pages += 1
            self._diag["blind"] += 1
            lines = []
            if ocr is not None and frame is not None:      # every portal, add-only (5.2, R6)
                lines = list((ocr.scan_image(frame, region_type="full") or {}).get("lines") or [])
                source = "ocr"
                s.ocr_reads += 1
                self._diag["ocr"] += 1
        elif source == "uia":
            s.good_lines.pop(url, None)
            s.good_lines[url] = len(lines)
            while len(s.good_lines) > 50:
                s.good_lines.pop(next(iter(s.good_lines)))
        elif source == "ocr":
            s.ocr_reads += 1
            self._diag["ocr"] += 1
        read_ms += (time.perf_counter() - t0) * 1000
        s.read_ms += read_ms
        s.reads += 1
        self._diag["last_lines"] = len(lines)
        self._diag["last_source"] = source
        self._report(self._on_read, bool(lines) and not is_blind(lines), read_ms, source)
        if not lines:
            return None
        self._policy = SOURCE_POLICY.get(source, SOURCE_POLICY["ocr"])
        try:
            return self._absorb_page(hwnd, s, portal, url, title, tab, lines, source, docs, own_read,
                                     now, h, in_view, sgt_i, scc)
        finally:
            self._policy = SOURCE_POLICY["uia"]

    def _absorb_page(self, hwnd: int, s: _Session, portal: str, url: str, title: str, tab: Optional[str],
                     lines: List[str], source: str, docs: Optional[List[Any]], own_read: bool, now: float,
                     h: Any, in_view: bool, sgt_i: Any, scc: Any) -> Optional[PageResult]:
        policy = self._policy
        today = self._today()
        # The capture loop's read carries the nodes of the same read: no second read for them.
        nodes = (list(docs or []) or None) if source == "uia" else None
        if self._recorder is not None:
            record_nodes = self._recorder.enabled and sgt_i is not None and sgt_i.active
            if own_read:
                nodes = self._nodes_for_recording(hwnd) if record_nodes else None
            self._recorder.record(session=s.session_id, portal=portal, url=url, title=title,
                                  source=source, lines=lines, ts=self._stamp(now), today=today,
                                  nodes=nodes if record_nodes else None)
        sdis = self._sdis
        try:
            if sdis is not None and sdis.enabled and in_view:
                sdis.offer(hwnd, s.session_id, portal, url, title)
        except Exception:
            pass                                # SDIS never disturbs SGT
        if self._stats is not None:
            self._stats.record_read(portal, source, today)

        registry = self.store.get()
        # Latched datapoints are not looked for again; "promote_longer" ones (the name) are.
        # The client's PAN / GSTIN are ALWAYS looked for: a second sighting confirms the
        # client, a different value means this window is now on another client.
        skip = (set(s.profile) & registry.latching_fields()) - set(_CLIENT_KEYS)
        res = resolve_page(registry, lines, portal, url, today, skip_profile=skip, title=title)
        block = s.strict_block if s.strict else None
        if block and url == block.get("url"):
            # The page that started this session, read again: its clashing PAN/GSTIN still does
            # not name it (see _Session.strict).
            for k, v in (block.get("values") or {}).items():
                if getattr(res.profile.get(k), "value", None) == v:
                    res.profile.pop(k, None)
        if self._stats is not None:
            self._stats.record_hits(portal, [h.spec for h in res.profile.values()]
                                    + [h.spec for h in res.current.values()]
                                    + [d.record for d in res.datasets], today)
        self._report(self._on_hits, s.session_id,
                     bool(s.profile or res.profile or res.current or res.datasets),
                     not _LOGIN.search(url or ""))
        if policy["profile"] == "none":
            res.profile.clear()                 # OCR never names, splits or switches a client (5.2)
        clash = None if res.is_list or not policy["identity_check"] else self._identity_conflict(s, res)
        if clash and not in_view:
            # A late read of a session set aside or ended names another client: nothing of it is taken.
            self._event(s, "page", change="late read names another client - not absorbed", field=clash, page=url)
            self._echo(f"[SGT] {self._tag} a late read shows another {clash.upper()} - not absorbed")
            return None
        if clash:
            # Another client's PAN/GSTIN: this window moved to someone else without passing a
            # login page (another tab). That is not a boundary either: the session is set aside
            # untouched, and the window's own session for that client comes back if it has one.
            # Otherwise a new one starts (strict): the clashing value is NOT taken from this page,
            # not even when it is read again - a stray GSTIN (a supplier in a table) must not name
            # it. Another page that shows it names it at once.
            seen = getattr(res.profile.get(clash), "value", None)
            first_read = bool(tab) and tab in s.unread_tabs
            s.unread_tabs.discard(tab)
            if not first_read:
                # The same tab now shows someone else: a read this session asked for and has not had
                # back may show them too (R1). A new tab's first page is only a tab switch.
                self._cut(s.session_id, "another client")
            self._shelve(hwnd, s)
            back = self._unshelve(hwnd, portal, key=clash, value=seen) if seen else None
            if back is not None:
                s = self._bring_back(hwnd, back, url, h, now)
            elif first_read:
                # The first page of a tab that had only joined the session in view: it is simply
                # that client's tab, named from this page like any session's first page.
                s = self._sessions[hwnd] = _Session(portal=portal, last_url=url, last_hash=h, last_read=now,
                                                    timeline=[url] if url else [])
            else:
                # The re-reads of this page are blocked too, but only when the tab is known: then a
                # switch to another client's tab never comes this way (each tab has its session), so
                # this is a clash INSIDE one tab - most likely a stray row, the same evidence again
                # on every re-read. With no tab id, two clients' tabs are told apart only by this
                # path, and GST gives every client's welcome page the same link - blocking it would
                # leave the second client unnamed (test_two_gst_clients_in_two_tabs_never_end_or_mix).
                blocked = {k: getattr(res.profile.get(k), "value", None) for k in _CLIENT_KEYS} if tab else {}
                s = self._sessions[hwnd] = _Session(
                    portal=portal, strict=True, last_url=url, last_hash=h, last_read=now,
                    timeline=[url] if url else [],
                    strict_block={"url": url, "values": {k: v for k, v in blocked.items() if v}} if tab else {})
                for k in _CLIENT_KEYS:
                    res.profile.pop(k, None)
            self._claim_tab(hwnd, s, tab)
        elif not res.is_list and in_view:
            # A session that has no client yet (one born from a stray PAN/GSTIN, say) meets the
            # page of a client this window already has a session for: that session comes back.
            for k in _CLIENT_KEYS:
                v = getattr(res.profile.get(k), "value", None)
                if v and not (s.profile.get(k) or {}).get("value"):
                    back = self._unshelve(hwnd, portal, key=k, value=v)
                    if back is not None:
                        self._shelve(hwnd, s)       # nothing to keep when it is still empty
                        s = self._bring_back(hwnd, back, url, h, now)
                        self._claim_tab(hwnd, s, tab)
                        break
        s.last_seen = now                   # a session made above was not stamped yet
        if not s.started:
            s.started = now
        s.unread_tabs.discard(tab)
        s.conflicts += len(res.conflicts)
        self._page_pans = {p for ln in lines for p in _PAN_TOKEN.findall(ln or "")}
        self._foreign_pans = self._other_pans_on_page(s, res, lines)
        # SDIS field specs ("sdis.<field>") feed containers, never SGT's built-in dataset.
        res.current, sdis_hits = split_hits(res.current)
        if not res.is_list:
            # Before the datasets, so a confirmation page's ARN can be joined to the form and
            # period the earlier pages supplied.
            self._update_draft(s, res, url, source, registry.current_rules)
        self._absorb(s, res, url, source, registry)
        self._feed_containers(s, res, sdis_hits, url, source)
        if scc is not None:    # the one read above: Settings may detach it mid-tick
            self._hand_to_scc(scc, s, portal, url, title, source, lines, res, registry, now, today, hwnd)
        self._save_state()
        if self._sgt_i is not None:
            self._hand_to_sgt_i(s, portal, url, title, source, lines, res, registry, now, today, hwnd,
                                nodes if source == "uia" else None)
        return res

    def _nodes_for_recording(self, hwnd: int) -> Optional[List[List[Dict[str, Any]]]]:
        """The page's node dump for the corpus (sgt_corpus format v2) - an extra read, never
        allowed to disturb capture: any failure here is silent, same as a recorder failure."""
        try:
            return list((self._read_nodes(hwnd) or {}).get("docs") or []) or None
        except Exception:
            return None

    def _hand_to_sgt_i(self, s: _Session, portal: str, url: str, title: str, source: str,
                       lines: List[str], res: PageResult, registry: Any, now: float, today: date,
                       hwnd: int = 0, nodes: Optional[List[Any]] = None) -> None:
        """SGT-I (core/sgt_i) gets a frozen copy of the page the Core has just finished with.
        It runs on its own thread; nothing it does can come back here (blueprint 14.2). `nodes`
        is the node tree the recorder already read for this page, if any - never an extra read."""
        try:
            if not self._sgt_i.active:
                return
            from core.sgt_i import make_observation
            obs = make_observation(
                session_id=s.session_id, portal=portal, url=url, title=title, source=source, lines=lines,
                result=res, profile={k: p.get("value", "") for k, p in s.profile.items()},
                draft=s.draft.values(registry.current_rules), ts=now, today=today.isoformat(),
                nodes=nodes)
            seen = getattr(self._sgt_i, "window_seen", None)
            if seen is not None:
                seen(hwnd, obs)     # step 9: what flashed in this (scope-gated) window since the last read
            self._sgt_i.submit(obs)
        except Exception as e:
            self._echo(f"[SGT-I] page not handed over: {e}")

    def _hand_to_scc(self, scc: Any, s: _Session, portal: str, url: str, title: str, source: str,
                     lines: List[str], res: PageResult, registry: Any, now: float, today: date,
                     hwnd: int) -> None:
        """SCC-U (core/scc) gets a frozen copy of the page the Core has just absorbed, with the
        window and SGT session it belongs to (obs.session_id). Nothing comes back (G.1 rule 1)."""
        try:
            if not scc.active:
                return
            from core.sgt_i.observation import make_observation
            obs = make_observation(
                session_id=s.session_id, portal=portal, url=url, title=title, source=source, lines=lines,
                result=res, profile={k: p.get("value", "") for k, p in s.profile.items()},
                draft=s.draft.values(registry.current_rules), ts=now, today=today.isoformat())
            scc.submit(obs, hwnd)
        except Exception as e:
            self._echo(f"[SCC-U] page not handed over: {type(e).__name__}")

    # ── The dataset being worked on ──────────────────────────────────────────────
    def _update_draft(self, s: _Session, res: PageResult, url: str, source: str, rules: Any) -> None:
        d = s.draft
        seen = res.current
        fill_only = self._policy["pieces"] == "fill_empty_only"
        if fill_only:
            # OCR: only a field nothing has set yet, and never the status (wording alone never
            # promotes, R6). Nothing it reads or misses changes or clears a piece.
            seen = {f: h for f, h in seen.items() if f not in d.pieces and f != "status"}
        if self._foreign_pans:
            # Another client's PAN is readable on this page: its TEXT may be about them, so only
            # what the link and window title say (the filing wizard's form and year) is used.
            seen = {f: h for f, h in seen.items() if h.source != "page"}
        scoped = (s.portal or "") in getattr(rules, "scoped_portals", ())
        if scoped:
            # A GST return page is one self-contained return. A page that does not name its own
            # form (a comparison tool, a menu) says nothing about the dataset being built: its
            # year, period or status could belong to any form.
            named = (d.pieces.get("form") or {}).get("page") == url
            if "form" not in seen and not named:
                seen = {}
        if self._page_pans and not self._client_pan(s) and any(h.source == "page" for h in seen.values()):
            # The client is not known yet and this page's text prints PANs: remember them, so the
            # dataset is only ever written for a client whose PAN was among them.
            d.claims = sorted(set(d.claims) | self._page_pans)
        # This page owns the pieces it supplied: gone from it now = cleared (the CA went back
        # and emptied the field). Only after MISSES_TO_CLEAR reads in a row, so one read of a
        # half-drawn page cannot wipe a good value.
        now = self._clock()
        for fld, piece in list(d.pieces.items()):
            if piece["page"] != url or fill_only:
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
                self._echo(f"[SGT] {self._tag} dataset in progress: {fld} cleared (gone from its page)")
        if not seen:
            return
        # A different period is a different dataset: close the one being built, start afresh.
        if scoped and "form" in seen:
            old_form = (d.pieces.get("form") or {}).get("value")
            if old_form and old_form != seen["form"].value:
                # Another return: nothing read for the old one (year, period, status) is carried over.
                old_period = d.values(rules).get("period") or d.last_period
                self._close_draft(s, rules, f"a different form was opened ({old_form} -> {seen['form'].value})", url)
                d = s.draft
                d.last_period = ""
                if old_period:
                    self._echo(f"[SGT] {self._tag} dataset in progress closed: {old_form} {old_period}")
        before = d.values(rules).get("period") or d.last_period
        trial = dict(d.values())
        trial.update({f: h.value for f, h in seen.items()})
        after = compose(trial, rules).get("period")
        if before and after and before != after:
            if fill_only:
                return                          # OCR never opens another dataset
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
                        previous=old["value"] if old else None, spec=hit.spec, via=hit.source, page=url, source=source,
                        evidence=hit.evidence if hit.evidence != hit.value else None)
            self._echo(f"[SGT] {self._tag} dataset in progress: {fld} = {hit.value}"
                       f"{' (was ' + old['value'] + ')' if old else ''}  [{hit.spec}, {hit.source}]")
        period_now = d.values(rules).get("period")
        if period_now:
            d.last_period = period_now
        if fill_only:
            return                              # a row from OCR needs its own ARN (R6): the next UIA read or the end writes it
        # Complete already? Then it is a dataset now - written at once rather than when it
        # closes, so quitting the app (or a crash) in the middle of a filing loses nothing.
        # It keeps building; later pieces update the same row.
        vals, missing = self._draft_as_dataset(d, rules)
        if not missing:
            prev = d.slot
            if (prev is not None and prev in s.slots and not prev.arn and not scoped
                    and submit_level(prev.values.get("status")) <= submit_level("Draft")
                    and prev.form_period != (vals.get("form"), vals.get("period"))):
                # The CA went back and changed the form (or year) of this same dataset, still only a
                # draft in a wizard: move its row rather than leave a stale one behind (the old key is
                # superseded on send). A return that was submitted, or a portal where each period is
                # its own return (GST), is never moved: a new period is a new dataset.
                old = dict(prev.values)
                prev.values.update(vals)
                self._queue(s, prev)
                self._event(s, "dataset", change="moved", record=CURRENT_RECORD, previous=old,
                            values=prev.values, page=url, source=source)
                self._echo(f"[SGT] {self._tag} dataset in progress moved: {self._label(old)} -> {self._label(prev.values)}")
            else:
                st = d.pieces.get("status")
                d.slot = self._merge_values(s, vals, CURRENT_RECORD, 85, url, source, claimed=d.claims or None,
                                            follow=True, status_explicit=bool(st and st.get("page") == url))

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
            self._echo(f"[SGT] {self._tag} dataset in progress dropped ({reason}): missing {', '.join(missing)}")
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

    # ── SDIS containers (blueprint S.2 / S.4) ────────────────────────────────────
    def _feed_containers(self, s: _Session, res: PageResult, hits: Dict[str, Any], url: str, source: str) -> None:
        """The page's SDIS field values into the session's container instances and Others values.
        Built like the dataset in progress: list pages never feed it, and while another client's
        PAN is readable only the link and title count. A changed containers document re-evaluates
        every open instance first; nothing moves down."""
        if not hits and not any(x.sdis.instances for x in self._all_sessions()):
            return
        doc = load_doc()
        if doc is not self._sdis_doc:
            if self._sdis_doc is not None:
                for other in self._all_sessions():
                    for inst in other.sdis.recompute(doc):
                        self._event(other, "container", change="re-evaluated (containers file changed)",
                                    container=inst.container, level=inst.level, status=inst.status)
                        if inst.keyed:
                            self._queue(other, inst)
            self._sdis_doc = doc
        if self._foreign_pans:
            hits = {f: h for f, h in hits.items() if h.source != "page"}
        if not hits or res.is_list:
            return
        values = {f: h.value for f, h in hits.items()}
        for inst in s.sdis.feed(doc, s.portal, values, url):
            before = inst.sent_key
            self._event(s, "container", container=inst.container, form=inst.form, period=inst.period,
                        fields=sorted(inst.captured), level=inst.level, status=inst.status,
                        k_of_n=f"{inst.k} of {inst.n}", page=url, source=source)
            self._echo(f"[SGT] {self._tag} container {inst.container} {inst.period}: {inst.k} of {inst.n}"
                       f"{' - ' + inst.level if inst.level else ''} ({inst.status})")
            if inst.keyed:
                self._queue(s, inst)
                if before is None:
                    self._hud(s, "capture", "Dataset captured",
                              f"{self._who(s)} • {inst.container} • {inst.level or f'{inst.k} of {inst.n}'}".strip(" •"),
                              {"form": inst.form, "period": inst.period})
        changed = s.sdis.feed_others(doc, s.portal, values, datetime.now().isoformat(timespec="seconds"))
        if changed:
            self._event(s, "others", fields=changed, page=url, source=source)
            self._echo(f"[SGT] {self._tag} Others: {', '.join(changed)} captured")

    def _sdis_profile(self, s: _Session) -> Dict[str, str]:
        """The session's profile values a row may carry: PAN, GSTIN and name only once confirmed."""
        prof = {k: p["value"] for k, p in s.profile.items() if k not in _CLIENT_KEYS + ("name",)}
        if s.confirmed:
            prof.update({k: s.profile[k]["value"] for k in _CLIENT_KEYS + ("name",) if k in s.profile})
        return prof

    def _sdis_info(self, s: _Session) -> Optional[Dict[str, Any]]:
        if not (s.sdis.others or s.profile):
            return None
        return sdis_info(s.sdis, load_doc(), s.portal, self._sdis_profile(s),
                         datetime.now().isoformat(timespec="seconds"))

    def _attach_sdis(self, s: _Session, payload: Dict[str, Any]) -> None:
        """S.4: the session's Others and Profile builder values ride on every row it writes."""
        info = self._sdis_info(s)
        if info:
            payload["raw_payload"]["sdis"] = info
            s.sdis.carried = info_signature(info)

    def _carry_sdis(self, s: _Session) -> None:
        """Session end: values no dispatched row carried yet go out - on the session's last row, or,
        with no dataset row at all, on its one carrier row (capture method SGT_sdis_info)."""
        info = self._sdis_info(s)
        if not info or info_signature(info) == s.sdis.carried:
            return
        if any(e[0] is s for e in self._outbox.values()):
            return                              # a row on its way picks the values up when built
        sent = [x for x in list(s.slots) + list(s.sdis.instances) if x.sent_key]
        if sent:
            self._queue(s, sent[-1])
            return
        if s.sdis.carrier is None:
            s.sdis.carrier = Carrier()
        self._queue(s, s.sdis.carrier)

    def _sdis_payload(self, s: _Session, obj: Any) -> Dict[str, Any]:
        """A container instance's tracker row, or the session's carrier row."""
        prof = self._sdis_profile(s)
        ids = self._client_ids(s)
        ident = ids.get("gstin") or ids.get("pan") or f"SGT{s.session_id}"
        if isinstance(obj, Instance):
            key = compute_dataset_key(s.portal, ident, obj.form, obj.period)
            if not self.live:
                key = f"SGT:{key}"
            form, period, status, method = obj.form, obj.period, obj.status or KEYED_MINIMUM, CAPTURE_METHODS[self.mode]
            dataset = {"form": form, "period": period, "status": status, "container": obj.container,
                       "fields": dict(obj.values), "evidence": evidence_of(obj),
                       "status_evidence": f"{obj.k} of {obj.n} fields captured"
                                          + (f" ({obj.level})" if obj.level else "")}
            record = obj.container
        else:
            key = compute_dataset_key(s.portal, ident, SDIS_CARRIER_FORM, "")
            form, period, status, method = "", "", SUBMIT_LEVELS[0], SDIS_INFO_METHOD
            dataset, record = {}, "sdis_info"
        supersedes = obj.sent_key if obj.sent_key and obj.sent_key != key else None
        obj.sent_key = key
        gstin = prof.get("gstin") or ""
        pan = prof.get("pan") or (gstin[2:12] if len(gstin) == 15 else "")
        payload = {
            "source": "sgt",
            "session_id": f"SGT-{s.session_id}",
            "portal": s.portal,
            "pan": pan,
            "gstin": gstin,
            "client_name": prof.get("name", ""),
            "dob": prof.get("dob", ""),
            "mobile": prof.get("phone", ""),
            "email": prof.get("email", ""),
            "filing_type": form,
            "filing_preference": "",
            "period_label": period,
            "arn": "N/A",
            "status": status,
            "filing_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "raw_text": "",                         # SGT never stores page text
            "capture_method": method,
            "capture_browser": s.browser,
            "identity_resolved": bool(pan),
            "page_url": next(iter(obj.evidence.values()), s.last_url) if isinstance(obj, Instance) else s.last_url,
            "dataset_key": key,
            "supersedes_dataset_key": supersedes,
            "raw_payload": {
                "source": {"engine": "SGT", "mode": self.mode, "record": record, "confidence": 0,
                           "identity": s.confirm_note or "not confirmed yet"},
                "sgt_dataset": dataset,
                "client_profile": prof,
                "timeline": list(s.timeline),
                "session_id": f"SGT-{s.session_id}",
                "dataset_key": key,
            },
        }
        self._attach_sdis(s, payload)
        return payload

    # ── Folding a page into the session ──────────────────────────────────────────
    # ── Tracker rows ─────────────────────────────────────────────────────────────
    def _queue(self, s: _Session, slot: _Slot) -> None:
        """Hands a dataset to the tracker - unless it breaks a dataset rule: then it is held,
        with the reason, and written the next time it is queued and passes."""
        if isinstance(slot, (Instance, Carrier)):
            # SDIS rows: the built-in dataset_rules are about SGT's own GST/ITR datasets.
            self._outbox[id(slot)] = (s, slot)
            self._outbox.move_to_end(id(slot))
            return
        problems = self.store.get().dataset_rules.problems(s.portal, slot.values, self._today())
        if not slot.waived and not (slot.values.get("form") and slot.values.get("period")):
            # Identity first: an ack or a message with no form and period of its own waits for them.
            problems.insert(0, IDENTITY_PROBLEM)
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
                self._echo(f"[SGT] {self._tag} dataset {self._label(slot.values)} HELD: {'; '.join(problems)}")
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
        for inst in s.sdis.instances:
            if inst.keyed and (every or inst.sent_key is not None):
                self._queue(s, inst)
        carrier = s.sdis.carrier
        if carrier is not None and (every or carrier.sent_key is not None):
            self._queue(s, carrier)

    @staticmethod
    def _report(callback: Optional[Callable[..., None]], *args: Any) -> None:
        """Tells the health monitor something; it must never disturb capture."""
        if callback is not None:
            try:
                callback(*args)
            except Exception:
                pass

    @staticmethod
    def _fresh_diag() -> Dict[str, Any]:
        # "skipped" stays 0 (blind pages of every portal go to OCR now); kept for the heartbeat's format.
        return {"reads": 0, "blind": 0, "skipped": 0, "ocr": 0, "busy": 0, "dropped": 0,
                "last_lines": None, "last_source": None, "portal": None}

    def take_diag(self) -> Dict[str, Any]:
        """Page reads since the last call - total, blind (UIA gave nothing usable), read by OCR,
        dropped (absorb: torn, too old, owner gone), skipped because UIA was still busy -
        the last read's line count and source, the open sessions. Resets the counts."""
        out, self._diag = self._diag, self._fresh_diag()
        out["sessions"] = len(self._sessions)
        return out

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

    def row_key(self, s: _Session, values: Dict[str, str]) -> str:
        """The tracker key for a dataset in the current mode (see live_key / dataset_key)."""
        return self.live_key(s, values) if self.live else self.dataset_key(s, values)

    @staticmethod
    def live_key(s: _Session, values: Dict[str, str]) -> str:
        """
        Live: the tracker's canonical key (core/dataset_key.py), identified the way the database
        identifies any row (GSTIN, else PAN), so a dataset another engine saved is the same row.
        Until the client is known the identifier is "SGT<session>" - it cannot collide with a
        real client's row, and is superseded once the client is known. A dataset known only by
        its ARN is keyed by it ("ARN_<n>" in the period place).
        """
        prof = SgtShadow._client_ids(s)
        ident = prof.get("gstin") or prof.get("pan") or f"SGT{s.session_id}"
        form, period = values.get("form") or "", values.get("period") or ""
        if not (form and period):
            form, period = "", f"ARN {values.get('arn') or ''}"
        return compute_dataset_key(s.portal, ident, form, period, values.get("filing_type") or "")

    @staticmethod
    def dataset_key(s: _Session, values: Dict[str, str]) -> str:
        """
        Shadow: SGT's own key namespace ("SGT:..."), so an SGT row only ever replaces an SGT
        row - never VSDC's row for the same dataset. Stable across sessions for a known client,
        so seeing the same dataset again updates its row instead of adding one.
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
        if isinstance(slot, (Instance, Carrier)):
            return self._sdis_payload(s, slot)
        v = dict(slot.values)
        # Name, PAN and GSTIN say WHO: none of them go on a row until the client is confirmed.
        prof = {k: p["value"] for k, p in s.profile.items() if k not in _CLIENT_KEYS + ("name",)}
        prof.update(self._client_ids(s))
        if s.confirmed and "name" in s.profile:
            prof["name"] = s.profile["name"]["value"]
        key = self.row_key(s, v)
        supersedes = slot.sent_key if slot.sent_key and slot.sent_key != key else None
        slot.sent_key = key
        lowered, slot.lowered = slot.lowered, False        # the tracker is told once; later rows are ordinary
        confirmed, slot.confirmed = slot.confirmed, False
        gstin = prof.get("gstin") or ""
        pan = prof.get("pan") or (gstin[2:12] if len(gstin) == 15 else "")
        status = v.get("status") or SUBMIT_LEVELS[0]
        filing_date = (f"{v['filing_date']} 00:00:00" if v.get("filing_date")
                       else datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        default_form = "GST Return" if "gst" in (s.portal or "").lower() else "ITR"
        payload = {
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
            "capture_method": CAPTURE_METHODS[self.mode],
            "capture_browser": s.browser,
            "identity_resolved": bool(pan),
            "page_url": slot.first_page,
            "dataset_key": key,
            "supersedes_dataset_key": supersedes,
            "status_correction": lowered,
            "status_confirmed": bool(lowered and confirmed),     # a complete (ARN + verified) row yields only to this
            "raw_payload": {
                "source": {"engine": "SGT", "mode": self.mode, "record": slot.record, "confidence": slot.confidence,
                           "identity": s.confirm_note or "not confirmed yet",
                           "evidence_source": slot.evidence_source, "first_seen_as": slot.first_seen_as},
                "sgt_dataset": v,
                "client_profile": prof,
                "timeline": list(s.timeline),
                "session_id": f"SGT-{s.session_id}",
                "dataset_key": key,
            },
        }
        sgt_i = self._sgt_i    # one read (see observe): a failure here would drop this row
        if sgt_i is not None:
            # SGT-I's enrichment sits beside the Core's values, in its own key only (14.2 rule 4).
            extra = sgt_i.enrichment(s.session_id)
            if extra:
                payload["raw_payload"]["sgt_i"] = extra
        self._attach_sdis(s, payload)
        return payload

    # ── HUD pill ─────────────────────────────────────────────────────────────────
    def _hud(self, s: _Session, event_type: str, title: str, subtitle: str,
             values: Optional[Dict[str, str]] = None) -> None:
        if self._notify is None:
            return
        v = values or {}
        ctx = {"portal": s.portal, "form": v.get("form"), "filing_pref": v.get("filing_type"),
               "period": v.get("period")}
        try:
            self._notify(event_type, title, f"{subtitle} • {HUD_TAGS[self.mode]}".strip(" •"), ctx)
        except Exception as e:                  # the pill must never break capture
            self._echo(f"[SGT] {self._tag} HUD event failed: {e}")

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
            self._echo(f"[SGT] {self._tag} profile {fld} promoted: {held['value']} -> {value}  [{spec}]")
            s.profile[fld] = {"value": value, "spec": spec, "confidence": confidence}
            if fld == "name":
                self._hud(s, "update", "Client name completed", value)
                self._queue_all(s)
            return
        s.profile[fld] = {"value": value, "spec": spec, "confidence": confidence, "pages": [url]}
        self._event(s, "profile", field=fld, value=value, spec=spec, confidence=confidence, page=url, source=source)
        self._echo(f"[SGT] {self._tag} profile {fld} = {value}  [{spec}]")
        if fld in _CLIENT_KEYS:
            self._sighted(s, fld, url)
        if fld in ("pan", "gstin", "name"):
            self._queue_all(s)                  # rows written before the client was known get their identity
        if fld != "username":                   # typed char by char on a login page: never a pill
            self._new_profile.append(fld)       # the pill announces the page's datapoints together

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
        if note.endswith("seen once") and (s.profile.get(fld) or {}).get("spec") == CLIENT_LINK_SPEC:
            note = "portal user ID matched a saved client"
        first = not s.confirmed
        if not first and (note == s.confirm_note or s.confirm_note.startswith(note + "; earlier session")):
            return
        s.confirmed, s.confirm_note = True, note
        self._event(s, "identity", change="client identified" if first else "identity strengthened", note=note)
        if not first:
            return
        self._echo(f"[SGT] {self._tag} client identified ({note})")
        if (s.profile.get(fld) or {}).get("spec") != CLIENT_LINK_SPEC:     # named by a page, not a lookup
            self._resume_by_client(s, fld, s.profile[fld]["value"], self._clock())
        self._queue_all(s)
        for slot in s.slots:
            if slot.held and slot.sent_key is None:
                self._queue(s, slot)            # a card that was waiting for the client

    def _resume_by_client(self, s: _Session, key: str, value: str, now: float) -> bool:
        """`s` was just named by its first page (`key` = PAN or GSTIN `value`, read from the page). A
        parked session of the same client and portal - from any window, idle less than
        RESUME_WINDOW_SEC ago - hands over its dataset in progress, its timeline and its identity note.
        Identity is never borrowed: `s` already has its client from its own page, and nothing else of
        the parked profile or its rows is taken. The parked entry is used up."""
        best: Optional[Tuple[int, Tuple[float, Dict[str, Any]]]] = None
        for h, lst in self._parked.items():
            for c in lst:
                snap = c[1]
                if _client_value(snap, key) != value:
                    continue
                if s.portal and snap.get("portal") and s.portal != snap.get("portal"):
                    continue
                if now - float(snap.get("last_seen") or c[0]) > RESUME_WINDOW_SEC:
                    continue
                if best is None or float(snap.get("last_seen") or c[0]) > float(best[1][1].get("last_seen") or best[1][0]):
                    best = (h, c)
        if best is None:
            return False
        h, c = best
        self._parked[h].remove(c)
        if not self._parked[h]:
            self._parked.pop(h, None)
        snap = c[1]
        old = _session_from_json(snap)
        # The dataset in progress carries on only if this page does not already show another one
        # (a piece of the same name with another value): then it is a different filing and starts fresh.
        if all(s.draft.pieces[f]["value"] == p["value"] for f, p in old.draft.pieces.items() if f in s.draft.pieces):
            for f, piece in old.draft.pieces.items():
                s.draft.pieces.setdefault(f, piece)
            s.draft.claims = sorted(set(s.draft.claims) | set(old.draft.claims))
        s.timeline = (old.timeline + [u for u in s.timeline if u not in old.timeline])[-TIMELINE_CAP:]
        if old.confirm_note:
            s.confirm_note = f"{s.confirm_note}; earlier session: {old.confirm_note}"
        idle_sec = max(0.0, round(now - float(snap.get("last_seen") or c[0]), 1))
        self._event(s, "session_resumed", from_session=snap.get("session_id"), idle_sec=idle_sec, by="client")
        self._echo(f"[SGT] {self._tag} same client as a parked session: its dataset in progress carries on "
                   f"(idled {int(idle_sec)}s)")
        self._save_state(force=True)
        return True

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

    def _announce_profile(self, s: _Session, before_identity: bool) -> None:
        """One pill event for the client datapoints this page gave us - the pill used to appear
        only for the PAN/GSTIN, so capturing a name, date of birth, e-mail or phone looked like
        nothing had happened (reported 2026-09-23). Never one pop per field."""
        fields = [f for f in dict.fromkeys(self._new_profile) if f in s.profile]
        self._new_profile = []
        if not fields:
            return
        known = self._client_pan(s) or (s.profile.get("gstin") or {}).get("value")
        named = [PROFILE_LABELS.get(f, f.replace("_", " ")) for f in fields]
        shown = ", ".join(named[:3]) + (f" +{len(named) - 3} more" if len(named) > 3 else "")
        who = self._who(s)
        if before_identity and known:               # this page is the one that identified them
            ident = next((f for f in fields if f in _CLIENT_KEYS), None)
            detail = f"{ident.upper()}: {s.profile[ident]['value']}" if ident else shown
            lead = "" if (ident and who == s.profile[ident]["value"]) else who   # no name yet: just the id
            self._hud(s, "identity", "Client identified", f"{lead} • {detail}".strip(" •"))
            rest = [n for f, n in zip(fields, named) if f not in _CLIENT_KEYS]
            if not rest:
                return
            shown = ", ".join(rest[:3]) + (f" +{len(rest) - 3} more" if len(rest) > 3 else "")
        self._hud(s, "capture", "Client details captured", f"{who} • {shown}".strip(" •"))

    def _absorb(self, s: _Session, res: PageResult, url: str, source: str, registry: Any = None) -> None:
        had_client = bool(self._client_pan(s) or (s.profile.get("gstin") or {}).get("value"))
        for fld, hit in res.profile.items():
            self._offer_profile(s, fld, hit.value, hit.spec, hit.confidence, url, source, registry)
        # Datapoints built from parts (first + middle + last name): offered like any other
        # value, so the joined name goes through the same promotion rule as a read one.
        prules = getattr(registry, "profile_rules", None)
        if self._policy["profile"] == "all":
            if prules is not None and prules.compose:
                held = {k: v["value"] for k, v in s.profile.items()}
                for fld, value in compose_values(held, prules.compose).items():
                    self._offer_profile(s, fld, value, f"compose:{fld}", 92, url, source, registry)
            self._link_client(s, url, source, registry)
        self._announce_profile(s, before_identity=not had_client)
        rules = registry.current_rules if registry is not None else None
        for ds in res.datasets:
            self._merge(s, ds, url, source, rules, registry)

    def _link_client(self, s: _Session, url: str, source: str, registry: Any) -> None:
        """A page that shows the portal's user ID (an SDIS profile datapoint, "username") but not the
        client's GSTIN: the saved client who owns that user ID names the session. Once per user ID per
        session. Only fills a gap - a GSTIN / PAN the pages gave is never overridden - and a later header
        GSTIN that disagrees still ends the session like any other contradiction."""
        user_id = (s.profile.get("username") or {}).get("value")
        if not user_id or self._resolve_client is None or any(k in s.profile for k in _CLIENT_KEYS):
            return
        if url and (_LOGIN.search(url) or _LOGOUT.search(url)):
            return                              # still being typed (or retyped): link once the portal moves on
        if user_id in s.linked:
            return
        s.linked.add(user_id)
        try:
            found = self._resolve_client(s.portal, user_id)
        except Exception:
            found = None
        if not found:
            self._event(s, "identity", change="user id not matched to a saved client")
            return
        for fld in ("gstin", "pan"):            # a GST session is keyed by the GSTIN; the PAN only when that is all there is
            if found.get(fld):
                self._offer_profile(s, fld, found[fld], CLIENT_LINK_SPEC, 85, url, source, registry)
                break
        if found.get("name"):                   # the company name the client is registered under in the master DB
            self._offer_profile(s, "name", found["name"], CLIENT_LINK_SPEC, 85, url, source, registry)
        if found.get("client_id") is not None:
            self._event(s, "identity", change="user id matched a saved client", user_id=user_id,
                        client_id=found["client_id"], company=found.get("name") or "")

    def _find_slot(self, s: _Session, values: Dict[str, str], stamp: str = "", follow: bool = False) -> Optional[_Slot]:
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
        if follow and arn and stamp:
            # The same confirmation page, still in view, now shows another ack: a corrected read of the
            # first one - unless the page names a different return.
            for slot in s.slots:
                if slot.arn and slot.owner.get("arn") == stamp and (not all(fp) or slot.form_period in (None, fp)):
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

    @staticmethod
    def _is_whole_page(registry: Any, record: str) -> bool:
        """A whole-page record (a confirmation, a verification message) as against a card of a list."""
        for r in getattr(registry, "records", ()) or ():
            if r.name == record:
                return r.start is None
        return False

    def _tie_message(self, s: _Session, values: Dict[str, str], url: str, source: str) -> bool:
        """
        A submit message with no ack on its page counts only for a return that already has one: the one
        its form + period name, or the only one in this session still waiting for verification. It never
        creates a dataset and never uses the return being built. True when it was tied (values then carry
        that return's ack).
        """
        fp = (values.get("form"), values.get("period"))
        acked = [sl for sl in s.slots if sl.arn]
        pool = [sl for sl in acked if sl.form_period == fp] if all(fp) else acked
        if len(pool) != 1:
            pool = [sl for sl in pool if submit_level(sl.values.get("status")) == 2]
        if len(pool) == 1:
            values["arn"] = pool[0].arn
            return True
        stamp = f"{url}#{s.visit}"
        if stamp not in s.untied:
            s.untied.add(stamp)
            why = "no return with an ack in this session" if not acked else "more than one return could be meant"
            self._event(s, "dataset", change="message not tied - ignored", status=values.get("status"),
                        reason=why, page=url, source=source)
            self._echo(f"[SGT] {self._tag} a submit message on this page was ignored: {why}")
        return False

    def _merge(self, s: _Session, ds: Dataset, url: str, source: str, rules: Any = None, registry: Any = None) -> None:
        values = ds.values()
        other = self._other_client(s, values) or ("page" if self._foreign_pans else None)
        if other:
            self._event(s, "dataset", change="not attributed - another client's", record=ds.record,
                        field=other, page=url, source=source,
                        **({"reason": "another PAN is readable on the page"} if other == "page" else {}))
            self._echo(f"[SGT] {self._tag} {ds.record}: names a different {other.upper()} than this session's client - skipped")
            return
        card_pan = values.get("pan") or (values["gstin"][2:12] if len(values.get("gstin") or "") == 15 else None)
        claimed = [card_pan] if card_pan else None
        for k in _CLIENT_KEYS:
            values.pop(k, None)
        if values.get("status"):
            values["status_evidence"] = ds.evidence("status")
        whole = self._is_whole_page(registry, ds.record)
        message_only = whole and bool(values.get("status")) and not values.get("arn")
        add_only = self._policy["datasets"] == "merge_add_only"
        if message_only and add_only:
            return                              # OCR wording with no ARN of its own never promotes (R6)
        if message_only:
            if not self._tie_message(s, values, url, source):
                return
        elif rules is not None and self._fill_from_draft(s, values, rules):
            self._echo(f"[SGT] {self._tag} {ds.record}: form/period taken from the dataset in progress")
            if values.get("arn"):
                # Submitted: the dataset being built is this one, and it is finished.
                s.draft = _Draft()
                self._event(s, "current", change="completed by a submission", arn=values["arn"], page=url)
        self._merge_values(s, values, ds.record, ds.confidence, url, source, claimed=claimed,
                           follow=whole and not add_only, status_explicit=bool(values.get("status")),
                           recovered=not whole)

    def _request_demotion(self, s: _Session, slot: _Slot, old: str, new: str, evidence: str,
                          stamp: str, url: str) -> None:
        mark = f"{new}|{stamp}"
        if mark in slot.demotion_asked:
            return                              # already put to the user for this stay on this page
        slot.demotion_asked.add(mark)
        token = uuid.uuid4().hex[:12]
        self._demotions[token] = (s, slot, new, evidence)
        info = {"token": token, "client": self._who(s), "form": slot.values.get("form") or "",
                "period": slot.values.get("period") or "", "arn": slot.arn, "from": old, "to": new,
                "evidence": evidence, "portal": s.portal}
        self._event(s, "dataset", change="status demotion needs confirmation", key=self._label(slot.values),
                    **{"from": old, "to": new, "evidence": evidence}, page=url)
        self._echo(f"[SGT] {self._tag} a page shows {new!r} for {self._label(slot.values)}, which was {old!r} "
                   f"with an ARN - asking before lowering it")
        if self._ask_demotion is not None:
            try:
                self._ask_demotion(info)
            except Exception as e:              # a dialog problem must never stop capture; the status is kept
                self._echo(f"[SGT] {self._tag} could not ask about the demotion: {e}")

    def answer_demotion(self, token: str, lower: bool) -> None:
        """The user's answer, from the UI thread: it is applied on SGT's own thread at its next tick."""
        self._demotion_answers.append((token, bool(lower)))

    def _apply_demotion_answers(self) -> None:
        while self._demotion_answers:
            token, lower = self._demotion_answers.popleft()
            (self.confirm_demotion if lower else self.decline_demotion)(token)

    def confirm_demotion(self, token: str) -> bool:
        """The user agreed to lower the status: apply it and send the row (the tracker accepts it)."""
        got = self._demotions.pop(token, None)
        if got is None:
            return False
        s, slot, new, evidence = got
        old = slot.values.get("status")
        slot.values["status"] = new
        if evidence:
            slot.values["status_evidence"] = evidence
        slot.lowered = True
        slot.confirmed = True
        self._event(s, "dataset", change="status lowered - confirmed by the user", key=self._label(slot.values),
                    **{"from": old, "to": new})
        self._queue(s, slot)
        return True

    def decline_demotion(self, token: str) -> bool:
        got = self._demotions.pop(token, None)
        if got is not None:
            s, slot, new, _ = got
            self._event(s, "dataset", change="status kept - lowering declined", key=self._label(slot.values),
                        to=new)
        return got is not None

    # Fields a page may correct once it shows another value (form and period are the dataset's identity:
    # a different one is a different dataset, not a correction).
    _FOLLOW = ("arn", "filing_type", "filing_preference", "fy", "tax_period", "filing_date")

    def _merge_values(self, s: _Session, values: Dict[str, str], record: str, confidence: int,
                      url: str, source: str, claimed: Optional[List[str]] = None, follow: bool = False,
                      status_explicit: bool = False, recovered: bool = False) -> Optional[_Slot]:
        """
        follow: this is a page read of one dataset (a whole-page record, the return being built), not a card
        of a list: a value the SAME page, still in view, now shows differently replaces the one it set.
        status_explicit: the page itself shows a status right now (not a default the dataset was lifted to).
        recovered: the values are a card of a list page, so a row born from them is "recovered" (6.5).
        """
        if not values.get("arn") and not (values.get("form") and values.get("period")):
            return None                         # nothing to key it on - not a dataset yet
        add_only = self._policy["datasets"] == "merge_add_only"
        # OCR (R6): a row is created, or its status raised, only by a read that also gives the
        # dataset's own ARN/ack (the resolver's label, shape and date checks already passed).
        with_id = bool(values.get("arn")) or not add_only
        values = dict(values)
        self._apply_identifier(s, values)
        if not values.get("status"):
            values["status"] = SUBMIT_LEVELS[0]     # the ladder's default: nothing shows any work
        stamp = f"{url}#{s.visit}"
        slot = self._find_slot(s, values, stamp, follow)
        if slot is not None and claimed:
            slot.claimed = sorted(set(slot.claimed or []) | set(claimed))
        if slot is None and not with_id:
            return None
        if slot is None:
            slot = _Slot(dict(values), record, confidence, url, claimed=claimed)
            slot.owner = {} if add_only else {k: stamp for k in values}
            slot.evidence_source = slot.first_seen_as = "ocr" if add_only else "recovered" if recovered else "uia"
            s.slots.append(slot)
            if add_only:
                if self._stats is not None:
                    self._stats.record_ocr_only_row(self._diag.get("portal") or "?", self._today())
                self._report(self._on_ocr_row)
            self._queue(s, slot)
            self._event(s, "dataset", change="new", record=record, values=values,
                        confidence=confidence, page=url, source=source)
            self._echo(f"[SGT] {self._tag} dataset {self._label(values)}  [{record}]")
            if slot.held:
                if any(IDENTITY_PROBLEM in p for p in slot.held):
                    self._hud(s, "prompt", "Submission seen - return not identified",
                              "open the return's page so SGT can place it")
                return slot                     # not written - the pill stays quiet about it
            submitted = submit_level(values.get("status")) >= 2
            detail = f"ARN: {values['arn']} • {values['status']}" if values.get("arn") else values["status"]
            self._hud(s, "submit" if submitted else "capture",
                      "Submission captured" if submitted else "Dataset captured",
                      f"{self._who(s)} • {detail}".strip(" •"), slot.values)
            return slot
        ds_record = record
        changes = {}
        if not add_only and slot.evidence_source == "ocr" and slot.arn and values.get("arn") == slot.arn:
            slot.evidence_source = "uia"        # a UIA read shows the same ARN: the row no longer rests on OCR
            changes["evidence_source"] = ["ocr", "uia"]
        elif (not add_only and not recovered and slot.evidence_source == "recovered"
              and values.get("arn") and slot.arn in (None, values["arn"])):
            slot.evidence_source = "uia"        # a confirmation page shows the ack; first_seen_as keeps "recovered"
            changes["evidence_source"] = ["recovered", "uia"]
        for k, v in values.items():
            old = slot.values.get(k)
            if k == "status_evidence":
                continue                        # travels with the status, below
            mine = follow and slot.owner.get(k) == stamp
            if k == "status":
                up = (old is None or submit_level(v) > submit_level(old)) and with_id
                corrected = (not up) and mine and status_explicit and v != old and not add_only
                if corrected and slot.arn:
                    # A filing with an ARN: lowering its status is risky - ask, never assume.
                    self._request_demotion(s, slot, old, v, values.get("status_evidence") or "", stamp, url)
                    corrected = False
                    continue
                if up or corrected:
                    slot.values[k] = v
                    slot.owner[k] = stamp
                    changes[k] = [old, v]
                    if corrected:
                        slot.lowered = True
                    if values.get("status_evidence"):
                        slot.values["status_evidence"] = values["status_evidence"]
            elif old is None:
                slot.values[k] = v
                if not add_only:
                    slot.owner[k] = stamp
                changes[k] = [None, v]
            elif old != v:
                if mine and k in self._FOLLOW:
                    slot.values[k] = v
                    slot.owner[k] = stamp
                    changes[k] = [old, v]
                else:
                    changes[k] = [old, v, "disagrees - kept the first"]
        if any(len(c) == 2 for c in changes.values()):     # a real change, not just a disagreement
            self._queue(s, slot)
        if changes:
            self._event(s, "dataset", change="updated", record=ds_record, key=self._label(slot.values),
                        changes=changes, page=url, source=source)
            self._echo(f"[SGT] {self._tag} dataset {self._label(slot.values)} updated: "
                       + ", ".join(f"{k} {c[0]!r}->{c[1]!r}" for k, c in changes.items()))
            if "status" in changes and changes["status"][0]:        # a status that moved
                self._hud(s, "update", "Dataset status updated",
                          f"{changes['status'][0]} \u2192 {changes['status'][1]}", slot.values)
        return slot

    @staticmethod
    def _label(values: Dict[str, str]) -> str:
        return " ".join(str(values[k]) for k in ("form", "period", "status", "arn") if values.get(k))

    # ── Ending ───────────────────────────────────────────────────────────────────
    def _end_idle(self, now: float) -> None:
        for h in list(self._parked):
            kept = [c for c in self._parked[h] if now - c[0] <= RESUME_WINDOW_SEC]
            if kept:
                self._parked[h] = kept
            else:
                self._parked.pop(h, None)
        idle = [(hwnd, s, False) for hwnd, s in self._sessions.items() if now - s.last_seen > IDLE_END_SEC]
        idle += [(hwnd, s, True) for hwnd, lst in self._shelf.items() for s in lst
                 if now - s.last_seen > IDLE_END_SEC]
        for hwnd, s, aside in idle:
            if aside:
                lst = self._shelf.get(hwnd) or []
                if s in lst:
                    lst.remove(s)
                if not lst:
                    self._shelf.pop(hwnd, None)
            else:
                self._sessions.pop(hwnd, None)
            self._park(hwnd, s, now)

    def _park(self, hwnd: int, s: _Session, now: float) -> None:
        """`s` (already off the window's lists) sat idle: it ends, and when it had a client (a PAN or
        GSTIN) and content it is also kept in `_parked` - and on disk - to be resumed within
        RESUME_WINDOW_SEC (same window and tab, or the same client named by any window)."""
        snap = copy.deepcopy(_session_to_json(s))
        has_content = s.has_content
        gap = now - s.last_seen
        self._end_one(s, IDLE_REASON)
        if has_content and _client_value(snap) and gap <= RESUME_WINDOW_SEC:
            self._parked.setdefault(hwnd, []).append((now, snap))
            self._event(s, "session_parked", idle_sec=round(gap, 1))
            self._save_state(force=True)

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
        self._retire_tabs(s)
        # Kept a while for a read it asked for that has not come back (absorb); after a logout, a
        # login page or the like, such a read is dropped instead (R1).
        self._closed[s.session_id] = (self._clock(), s, reason)
        self._closed.move_to_end(s.session_id)
        if not _absorbs_after(reason):
            self._cut(s.session_id, reason)
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
        # A row still waiting for its form and period is written now, flagged: a filing is never lost.
        for slot in s.slots:
            if slot.held and not slot.waived and any(IDENTITY_PROBLEM in p for p in slot.held):
                slot.waived = True
                self._queue(s, slot)
                self._event(s, "dataset", change="written without form and period - session ended",
                            values=dict(slot.values))
                self._echo(f"[SGT] {self._tag} {self._label(slot.values)} written without form and period")
        # The dataset still being built is dispatched only if it is complete.
        self._close_draft(s, self.store.get().current_rules, f"session end ({reason})", s.last_url)
        self._carry_sdis(s)
        self._alert_unknown_client(s)
        payload = self.would_be_payload(s)
        stats = {"reads": s.reads, "ocr_reads": s.ocr_reads, "blind_pages": s.blind_pages,
                 "read_ms_total": round(s.read_ms, 1), "conflicts": s.conflicts,
                 "minutes": round((s.last_seen - s.started) / 60, 1)}
        self._event(s, "session_end", reason=reason, payload=payload, stats=stats)
        who = payload["client_profile"].get("name") or payload["client_profile"].get("pan") \
            or payload["client_profile"].get("gstin") or "client unknown"
        note = ""
        if payload["datasets"] and not payload["client_known"]:
            note = (" - no PAN/GSTIN: written unattributed" if self.live
                    else " - no PAN/GSTIN: live mode would write these unattributed and alert")
        self._echo(f"[SGT] {self._tag} session ended ({reason}): {who}, "
                   f"{len(payload['datasets'])} dataset(s), {len(payload['client_profile'])} profile field(s){note}")
        # Not on app quit or when SGT is switched off: nobody is looking at the pill then.
        quiet = ("shutdown", "switched off", "recovered")
        if not any(q in reason.lower() for q in quiet):
            n = len(payload["datasets"])
            if payload["datasets"] and not payload["client_known"]:
                self._hud(s, "prompt", "SGT session ended - client unknown", f"{n} dataset(s) with no PAN/GSTIN")
            else:
                self._hud(s, "logout", "SGT session logged", f"{who} • {n} dataset(s)")

    def _alert_unknown_client(self, s: _Session) -> None:
        """Live: a submission written with no client needs a human to attach it - the phone
        alert VSDC247 raises for its own client-unknown rows. Once per dataset; never for one
        held back by a dataset rule (it was not written)."""
        # Its rows carry the client only once confirmed (_client_ids), so a PAN merely seen does
        # not count: those rows went out unassigned too.
        if not self.live or self._alert_unattributed is None or self._client_ids(s):
            return
        for slot in s.slots:
            if slot.held or slot.alerted or submit_level(slot.values.get("status")) < 2:
                continue
            slot.alerted = True
            self._event(s, "alert", change="submission with no client - phone alert", values=dict(slot.values))
            try:
                self._alert_unattributed(slot.values.get("form") or "", slot.first_page or s.last_url)
            except Exception as e:              # an alert must never break capture
                self._echo(f"[SGT] {self._tag} phone alert failed: {e}")

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
                    "sessions": [_session_to_json(s) for s in live], "parked": parked}
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
                s = _session_from_json(raw)
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
                if age > RESUME_WINDOW_SEC or not _client_value(snap):
                    continue
                snap["last_seen"] = now - age - max(0.0, float(e.get("gap") or 0.0))
                snap[_RESTORED] = True
                self._parked.setdefault(int(e["hwnd"]), []).append((now - age, snap))
                n += 1
            except Exception as ex:
                self._echo(f"[SGT] {self._tag} could not restore a parked session: {type(ex).__name__}")
        if n:
            self._echo(f"[SGT] {self._tag} {n} parked session(s) restored from the last run")
        return n

    # ── Portal-change watch ──────────────────────────────────────────────────────
    def set_digest(self, digest: Optional[Callable[[date], Optional[Dict[str, Any]]]]) -> None:
        """digest(today) -> {"title", "subtitle", "counts"} when a daily capture digest was made, else
        None (not the admin PC, or already made today). main.py sets it; the text of the digest is
        kept by the digest itself, only counts reach the log."""
        self._digest = digest

    def _daily_digest(self, today: date) -> None:
        if self._digest is None:
            return
        try:
            made = self._digest(today)
        except Exception as ex:
            self._echo(f"[SGT] {self._tag} the daily digest failed: {type(ex).__name__}")
            return
        if not made:
            return
        self._log({"event": "digest", **(made.get("counts") or {})})
        if self._notify is not None:
            try:
                self._notify("prompt", made.get("title") or "Capture digest ready", made.get("subtitle") or "", {})
            except Exception:
                pass

    def _daily_health_check(self) -> None:
        today = self._today()
        if self._checked_day == today:
            return
        if self._stats is None and self._digest is None:
            return
        self._checked_day = today
        self._daily_digest(today)
        if self._stats is None:
            return
        for alert in self._stats.alerts(today):
            self._echo(f"[SGT] portal-change watch: {alert['detail']}")
            self._log({"event": "health", **alert})
            if self._notify is not None:
                try:
                    self._notify("prompt", "SGT: a portal may have changed", alert["detail"], {"portal": alert["portal"]})
                except Exception:
                    pass

    def _append_log(self, rec: Dict[str, Any]) -> None:
        try:
            d = self._log_dir or shadow_dir()
            d.mkdir(parents=True, exist_ok=True)
            path = d / f"sgt_shadow_{date.today().isoformat()}.jsonl"
            if str(path) not in self._log_checked:
                self._log_checked.add(str(path))
                self._seal_torn_line(path)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:
            self._echo(f"[SGT] {self._tag} could not write the log: {e}")

    @staticmethod
    def _seal_torn_line(path: Path) -> None:
        """An app killed mid-write leaves the log's last line without its newline; the next record would
        be glued onto it and both lines would be unreadable. Ends the torn line first."""
        try:
            if path.exists() and path.stat().st_size:
                with open(path, "rb+") as f:
                    f.seek(-1, os.SEEK_END)
                    if f.read(1) != b"\n":
                        f.write(b"\n")
        except OSError:
            pass

    def _log(self, rec: Dict[str, Any]) -> None:
        self._append_log({"ts": datetime.now().isoformat(timespec="seconds"), **rec})

    def _event(self, s: _Session, event: str, **data: Any) -> None:
        self._append_log({"ts": datetime.now().isoformat(timespec="seconds"), "event": event,
                          "session": s.session_id, "portal": s.portal, "capture_browser": s.browser, **data})


def _absorbs_after(reason: str) -> bool:
    """A late result may still land in a session that ended this way (ABSORB_AFTER_END)."""
    r = (reason or "").lower()
    return any(r == x or r.startswith(x + " ") for x in ABSORB_AFTER_END)


_RESTORED = "_restored"         # marks a parked snapshot read back from disk: its hwnd means nothing now


def _client_value(snap: Dict[str, Any], key: Optional[str] = None) -> str:
    """The client's PAN or GSTIN in a session snapshot (`key`: only that one), or ''."""
    prof = snap.get("profile") or {}
    for k in ((key,) if key else _CLIENT_KEYS):
        v = prof.get(k)
        v = v.get("value") if isinstance(v, dict) else v
        if v:
            return str(v)
    return ""


def _browser_of(hwnd: int) -> str:
    """'chrome' | 'msedge' | 'firefox' | ... for a window, '' when it cannot be read (a test's fake window)."""
    try:
        from core.sdis.recorder import browser_of
        return browser_of(hwnd)
    except Exception:
        return ""


def _session_to_json(s: _Session) -> Dict[str, Any]:
    return {
        "session_id": s.session_id, "started": s.started, "last_seen": s.last_seen, "portal": s.portal,
        "profile": s.profile, "timeline": s.timeline, "last_url": s.last_url,
        "confirmed": s.confirmed, "confirm_note": s.confirm_note, "strict": s.strict, "browser": s.browser,
        "strict_block": s.strict_block, "tabs": sorted(s.tabs),
        "slots": [{"values": sl.values, "record": sl.record, "confidence": sl.confidence,
                   "first_page": sl.first_page, "sent_key": sl.sent_key, "held": sl.held,
                   "claimed": sl.claimed, "waived": sl.waived, "evidence_source": sl.evidence_source,
                   "first_seen_as": sl.first_seen_as} for sl in s.slots],
        "draft": {f: {k: v for k, v in piece.items() if isinstance(v, (str, int, float, bool, type(None)))}
                  for f, piece in s.draft.pieces.items()},
        "draft_slot": s.slots.index(s.draft.slot) if s.draft.slot in s.slots else None,
        "draft_claims": list(s.draft.claims),
        "sdis": s.sdis.to_json(),
    }


def _session_from_json(raw: Dict[str, Any]) -> _Session:
    s = _Session(session_id=str(raw["session_id"]), started=float(raw.get("started") or 0.0),
                 last_seen=float(raw.get("last_seen") or 0.0), portal=str(raw.get("portal") or ""))
    s.browser = str(raw.get("browser") or "")
    s.profile = dict(raw.get("profile") or {})
    s.timeline = list(raw.get("timeline") or [])
    s.last_url = str(raw.get("last_url") or "")
    s.confirmed, s.confirm_note, s.strict = bool(raw.get("confirmed")), str(raw.get("confirm_note") or ""), bool(raw.get("strict"))
    s.strict_block = dict(raw.get("strict_block") or {})
    s.tabs = set(raw.get("tabs") or [])
    for sl in raw.get("slots") or []:
        s.slots.append(_Slot(dict(sl["values"]), sl.get("record") or CURRENT_RECORD, int(sl.get("confidence") or 0),
                             sl.get("first_page") or "", sl.get("sent_key"), sl.get("held"), sl.get("claimed"),
                             waived=bool(sl.get("waived")), evidence_source=str(sl.get("evidence_source") or "uia"),
                             first_seen_as=str(sl.get("first_seen_as") or "")))
    s.draft = _Draft(pieces={f: dict(pc) for f, pc in (raw.get("draft") or {}).items()},
                     claims=list(raw.get("draft_claims") or []))
    idx = raw.get("draft_slot")
    if isinstance(idx, int) and 0 <= idx < len(s.slots):
        s.draft.slot = s.slots[idx]
    s.sdis = ContainerSession.from_json(raw.get("sdis"))
    return s
