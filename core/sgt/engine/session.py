"""
core/sgt/engine/session.py - what one SGT session holds
=======================================================
A Session (one client in one browser window or tab), its dataset Slots, the Draft being built
across pages, and their JSON form for the crash snapshot and parked sessions. Data only: every
decision about them is made by the engine's parts.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from core.sgt.containers import ContainerSession
from core.sgt.evidence import SessionEv
from core.sgt.specs.specs import compose_values
from .policy import ABSORB_AFTER_END, CURRENT_RECORD, CLIENT_KEYS


@dataclass
class Slot:
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
    # form / period / arn / status -> the stamp of the read that set it (sgt_evidence)
    ev: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    @property
    def arn(self) -> Optional[str]:
        return self.values.get("arn")

    @property
    def form_period(self) -> Optional[Tuple[str, str]]:
        f, p = self.values.get("form"), self.values.get("period")
        return (f, p) if f and p else None


@dataclass
class Draft:
    """
    The dataset being worked on, built from pieces seen on different pages. Each piece keeps
    the page it came from: that page owns it, so going back to it and changing or clearing the
    field there changes or clears the piece.
    """
    pieces: Dict[str, Dict[str, str]] = field(default_factory=dict)   # field -> {value, page, source, spec}
    slot: Optional["Slot"] = None      # the row this dataset was written as, once complete
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
class Session:
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    started: float = 0.0                # on SgtEngine's clock (set when the session is first seen)
    last_seen: float = 0.0
    portal: str = ""
    profile: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    slots: List[Slot] = field(default_factory=list)
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
    draft: Draft = field(default_factory=Draft)
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
    # SDIS dataset containers and Others values (core/sgt/containers.py, blueprint S.2/S.4)
    sdis: ContainerSession = field(default_factory=ContainerSession)
    # How it was born, what happened to it and what named its client: goes on every row (sgt_evidence)
    ev: SessionEv = field(default_factory=SessionEv)

    @property
    def has_content(self) -> bool:
        # A lone login-box user name (no client, no dataset) is not a session worth ending or setting aside.
        return bool(any(k != "username" for k in self.profile) or self.slots or self.draft.pieces
                    or self.sdis.has_content)


def absorbs_after(reason: str) -> bool:
    """A late result may still land in a session that ended this way (ABSORB_AFTER_END)."""
    r = (reason or "").lower()
    return any(r == x or r.startswith(x + " ") for x in ABSORB_AFTER_END)


RESTORED = "_restored"         # marks a parked snapshot read back from disk: its hwnd means nothing now


def client_value(snap: Dict[str, Any], key: Optional[str] = None) -> str:
    """The client's PAN or GSTIN in a session snapshot (`key`: only that one), or ''."""
    prof = snap.get("profile") or {}
    for k in ((key,) if key else CLIENT_KEYS):
        v = prof.get(k)
        v = v.get("value") if isinstance(v, dict) else v
        if v:
            return str(v)
    return ""


def browser_of(hwnd: int) -> str:
    """'chrome' | 'msedge' | 'firefox' | ... for a window, '' when it cannot be read (a test's fake window)."""
    try:
        from core.sdis.recorder import browser_of
        return browser_of(hwnd)
    except Exception:
        return ""


def session_to_json(s: Session) -> Dict[str, Any]:
    return {
        "session_id": s.session_id, "started": s.started, "last_seen": s.last_seen, "portal": s.portal,
        "profile": s.profile, "timeline": s.timeline, "last_url": s.last_url,
        "confirmed": s.confirmed, "confirm_note": s.confirm_note, "strict": s.strict, "browser": s.browser,
        "strict_block": s.strict_block, "tabs": sorted(s.tabs),
        "slots": [{"values": sl.values, "record": sl.record, "confidence": sl.confidence,
                   "first_page": sl.first_page, "sent_key": sl.sent_key, "held": sl.held,
                   "claimed": sl.claimed, "waived": sl.waived, "evidence_source": sl.evidence_source,
                   "first_seen_as": sl.first_seen_as, "ev": sl.ev} for sl in s.slots],
        "ev": s.ev.to_json(),
        "draft": {f: {k: v for k, v in piece.items() if isinstance(v, (str, int, float, bool, type(None)))}
                  for f, piece in s.draft.pieces.items()},
        "draft_slot": s.slots.index(s.draft.slot) if s.draft.slot in s.slots else None,
        "draft_claims": list(s.draft.claims),
        "sdis": s.sdis.to_json(),
    }


def session_from_json(raw: Dict[str, Any]) -> Session:
    s = Session(session_id=str(raw["session_id"]), started=float(raw.get("started") or 0.0),
                 last_seen=float(raw.get("last_seen") or 0.0), portal=str(raw.get("portal") or ""))
    s.browser = str(raw.get("browser") or "")
    s.profile = dict(raw.get("profile") or {})
    s.timeline = list(raw.get("timeline") or [])
    s.last_url = str(raw.get("last_url") or "")
    s.confirmed, s.confirm_note, s.strict = bool(raw.get("confirmed")), str(raw.get("confirm_note") or ""), bool(raw.get("strict"))
    s.strict_block = dict(raw.get("strict_block") or {})
    s.tabs = set(raw.get("tabs") or [])
    for sl in raw.get("slots") or []:
        s.slots.append(Slot(dict(sl["values"]), sl.get("record") or CURRENT_RECORD, int(sl.get("confidence") or 0),
                             sl.get("first_page") or "", sl.get("sent_key"), sl.get("held"), sl.get("claimed"),
                             waived=bool(sl.get("waived")), evidence_source=str(sl.get("evidence_source") or "uia"),
                             first_seen_as=str(sl.get("first_seen_as") or ""),
                             ev={k: dict(v) for k, v in (sl.get("ev") or {}).items() if isinstance(v, dict)}))
    s.ev = SessionEv.from_json(raw.get("ev"))
    s.draft = Draft(pieces={f: dict(pc) for f, pc in (raw.get("draft") or {}).items()},
                     claims=list(raw.get("draft_claims") or []))
    idx = raw.get("draft_slot")
    if isinstance(idx, int) and 0 <= idx < len(s.slots):
        s.draft.slot = s.slots[idx]
    s.sdis = ContainerSession.from_json(raw.get("sdis"))
    return s
