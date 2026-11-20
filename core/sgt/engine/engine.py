"""
core/sgt/engine/engine.py - the SGT engine
==========================================
SgtEngine is SGT's capture engine (until 2026-11 the module core/sgt/sgt_shadow.py, class SgtShadow).

Runs on every in-scope page, builds each session's client profile and datasets, and writes
every dataset to the tracker dump as soon as it can be keyed, and again whenever it changes,
so nothing waits for the session to end. Its captures show on the HUD pill - only real
captures, never page reads. Everything is also written to a local log.

Rows: capture method "SGT_live", the tracker's canonical keys (core/dataset_key.py): a dataset
another engine captured earlier is the same row, and its status never moves down. HUD tag
"SGT (Live)". A submission whose client never became known is written unattributed AND raises
the phone alert (VSDC247's client-unknown path).

The engine is one class made of parts, one module each:

  engine          SgtEngine: the constructor, what is attached, the entry points
  boundaries      session bookkeeping per window and tab: set aside, back, login/logout, idle
  reading         a page read into the session that asked for it; blind pages and OCR
  identity        who the client is: profile values, PAN/GSTIN sightings, contradictions
  draft           the dataset being worked on, built from pieces on several pages
  merge           a dataset seen on a page folded into the session's rows
  demotion        asking before a filing's status is lowered
  rows            the outbox, dataset rules, tracker keys and the row payload
  sdis_rows       SDIS containers and Others values
  ending          parking, finishing a session, the client-unknown alert
  persistence     the crash snapshot and its recovery
  journal         the HUD pill, the local log, the daily digest, the portal-change watch
  evidence_hooks  the sgt_ev evidence block on every row (core/sgt/evidence.py)
  session         the data: Session, Slot, Draft and their JSON form
  policy          the fixed numbers and rules

Per browser window (one client per session, two windows = two sessions):

  read      UIA first; OCR of the screenshot when UIA comes back blind (a canvas-painted
            page shows UIA nothing, or only Flutter's "Enable accessibility" button), on
            every portal - OCR only ever adds (SOURCE_POLICY). With the capture loop
            (core/sgt/capture/loop) a read is asked for by observe_window's session and
            absorbed into that session when it comes back (absorb).
            A page is only re-read when it changed - the URL, the screenshot's 32x32
            hash, or 15 s passing - so a still page costs one hash, not one read.
  profile   latches by default: once found it is no longer looked for. A spec with
            "merge": "promote_longer" (the name) keeps looking, and a longer value that
            extends the held one replaces it ("RAVI MEHTA" -> "RAVI KUMAR MEHTA").
  datasets  never latch: a slot per dataset (form + period + submit status), keyed by its
            ARN, else by (form, period). The status is a level of the one submit ladder
            every portal shares (core/sgt/specs/toolbox.SUBMIT_LEVELS), climbed by what was captured:
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
  session   ends on a login/logout keyword in the link, 4 hours idle, a closed window or app
            quit - nothing else. Another tab (another portal, another client of the same
            portal) only sets the session aside; it comes back when the window returns to it.
            The session's payload is logged then, with a note of which of its ARNs the
            other engines also dispatched.
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
            Every page read can be recorded for replay (core/sgt/corpus.py), and spec hits are
            counted to notice a portal that changed (core/sgt/spec_health.py).

Log: ~/AmanAssociates_Sera/sgt_shadow/sgt_shadow_YYYY-MM-DD.jsonl, one JSON object per
line. It holds client datapoints (PAN, name, ARNs) - it stays on this PC, like the database.
The log holds resolved values only; page TEXT is kept separately, for replay, only while page
recording is on (core/sgt/corpus.py - local, 30 days).
"""

import collections
import time
from collections import OrderedDict
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from core.sgt.specs.resolver import PageResult
from core.sgt.specs.specs import SpecStore
from .policy import EV_IDLE, MODE_LIVE, OWNERS_KEPT, RESULT_MAX_AGE_SEC, SOURCE_POLICY
from .session import Session, Slot
from .boundaries import Boundaries
from .reading import Reading
from .draft import DraftBuilder
from .identity import Identity
from .merge import Merging
from .demotion import Demotion
from .rows import Rows
from .sdis_rows import SdisRows
from .ending import Ending
from .persistence import CrashSafety
from .journal import Journal
from .evidence_hooks import EvidenceHooks


class SgtEngine(Boundaries, Reading, DraftBuilder, Identity, Merging, Demotion, Rows, SdisRows, Ending,
                CrashSafety, Journal, EvidenceHooks):
    """One per router. observe() is called once per in-scope tick; it never raises. Each part of the
    engine lives in its own module (see the package docstring); this class joins them and holds the
    state they share."""

    mode = MODE_LIVE        # written on every row ("raw_payload.source.mode")

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
        # Capture health (core/vsdc/vsdc_health): on_read(usable, ms, source) after each page read
        # (source "uia", "ocr" or "busy"); on_hits(session id, any datapoint yet, logged-in page);
        # on_ocr_row() when a dataset row is first created from an OCR read.
        self._on_read = on_read
        self._on_hits = on_hits
        self._on_ocr_row = on_ocr_row
        # (portal, portal user ID) -> {"gstin", "pan"} of the saved client who owns it, or None
        # (core/sgt/client_link.ClientLink). Names the client where the page itself does not.
        self._resolve_client = resolve_client
        # A page now shows a LOWER status than a filing that has an ARN: never applied on its own. The
        # user is asked (this callback shows the dialog); confirm_demotion / decline_demotion answer.
        self._ask_demotion = ask_demotion
        self._demotions: Dict[str, Tuple[Session, Slot, str, str]] = {}
        self._demotion_answers: "collections.deque[Tuple[str, bool]]" = collections.deque()   # from the UI thread
        # SDIS's own recorder (core.sdis.recorder.SdisRecorder), or None. It is only offered the
        # pages SGT's change gate let through, after SGT's own read; see _observe.
        self._sdis = sdis
        # SGT-I (core.sgt_i.SgtIntelligence), or None. It only ever gets copies; see _hand_to_sgt_i.
        self._sgt_i = intelligence
        # SCC-U (core.scc.SccHost), or None. Copies only, like SGT-I; see _hand_to_scc.
        self._scc = scc
        # VSDC247-V verifier for payload verification enrichment
        self._verifier = None
        # (form, page) of a submission written with no client - the phone alert
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
        self._digest_thread: Any = None         # the day's digest runs off the capture thread
        self._digest_made: Optional[Dict[str, Any]] = None     # its result, reported on a later tick
        # HUD pill: (event_type, title, subtitle, context). Only real captures reach it - a
        # client identified, a dataset found or promoted, a session with something in it
        # ending - never page reads or half-built datasets, so the pill stays quiet otherwise.
        self._notify = notify
        # Tracker rows waiting to be handed to the router (one entry per dataset; a dataset that
        # changes again before it is sent is simply sent once, in its latest state).
        self._outbox: "OrderedDict[int, Tuple[Session, Slot]]" = OrderedDict()
        if read_uia is None:
            from core.capture import uia_text

            def read_uia(hwnd: int) -> Dict[str, Any]:
                # Ticked radio buttons / checkboxes too: a filing type is often a radio choice.
                return uia_text.read_page_text(hwnd, include_selection=True)
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
        self._sessions: Dict[int, Session] = {}
        # Sessions of a window that are set aside, not ended: the same window shows another portal
        # (a GST tab, an Income Tax tab, a mail tab) or another client of the same portal. Only a
        # login or logout page, the idle limit, shutdown and a closed window end a session.
        self._shelf: Dict[int, List[Session]] = {}
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
        self._closed: "OrderedDict[str, Tuple[float, Session, str]]" = OrderedDict()
        self._policy: Dict[str, str] = SOURCE_POLICY["uia"]    # the source policy of the read being absorbed
        self._url_changed = False                # the last observe_window moved its session to another page
        # The read being absorbed, for evidence stamps (sgt_evidence): its tab and source, whether its
        # session was no longer in view, and how long the read took to come back.
        self._ev_ctx: Dict[str, Any] = dict(EV_IDLE)
        self._ev_lag = 0.0
        if self._state_path is not None:
            self._recover()

    def _stamp(self, now: float) -> float:
        """A reading of SgtEngine's clock as a wall-clock time, for the page corpus (replay sorts by it)."""
        return now + (time.time() - time.monotonic()) if self._monotonic else now

    # ── What is attached ─────────────────────────────────────────────────────────
    def set_intelligence(self, host: Any) -> None:
        """Attaches SGT-I's host (or None). It never changes what the Core captures."""
        self._sgt_i = host

    def set_client_lookup(self, lookup: Optional[Callable[[str, str], Optional[Dict[str, Any]]]]) -> None:
        """Attaches the saved-client lookup by portal user ID (or None)."""
        self._resolve_client = lookup

    def set_scc(self, host: Any) -> None:
        """Attaches SCC-U's host (or None). It never changes what the Core captures."""
        self._scc = host

    def set_verifier(self, verifier: Any) -> None:
        """Attaches VSDC247-V's verifier for payload verification enrichment."""
        self._verifier = verifier

    @property
    def _tag(self) -> str:
        """Every console line says "(live)", as it did when SGT also had a shadow mode."""
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
        except Exception as e:                  # SGT must never disturb capture
            self._echo(f"[SGT] {self._tag} error, page skipped: {e}")
            return None

    def observe_window(self, hwnd: int, tab: Optional[str], portal: Optional[str], url: str,
                       title: str = "") -> Optional[Session]:
        """Session bookkeeping for the window in view (tab switch, portal switch, login/logout link,
        idle, parked resume, claiming the tab) - no read. The session in view, or None on a logout
        page (nothing about a client is read there)."""
        try:
            return self._observe_window(hwnd, portal or "", url or "", tab or None, self._clock())
        except Exception as e:
            self._echo(f"[SGT] {self._tag} error, window skipped: {e}")
            return None

    def request_made(self, ctx: Any, s: Optional[Session] = None) -> None:
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
