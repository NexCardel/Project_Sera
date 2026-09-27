"""
core/sgt_i/ledger.py - the evidence ledger: why each value is believed, and a second opinion
=============================================================================================
Blueprint 14.4 step 7. SGT-I keeps a notebook of *why* it believes each value the Core captured,
and says so on the row - in its own field, beside the Core's value, never in place of it (14.2).

* **Beliefs with sightings.** Every value the Core captured on a page (a dataset card, or a piece
  of the dataset being worked on) is a belief; each page it was read on is a sighting: page kind
  (step 6), the wording around it (the assertion checker), source (UIA / OCR / PDF), the Core's own
  confidence, route (step 5), and which client's page it was. A sighting's weight is
  source x page kind x assertion x confidence, all from sgt_i_config.json's "ledger" section
  (hand-set, then tuned by counting on the corpus - statistics, not a model); a belief's score
  combines its sightings as 1 - prod(1 - w), so reading the same thing again only ever adds.
* **Retraction (truth maintenance).** A sighting remembers the client it was read under. When a
  session's client turns out to be someone else - or a card names another client than the
  session's - those sightings are withdrawn, every belief left with no sighting goes, and so does
  every belief that depended on a withdrawn one (a status depends on its dataset's identifier),
  until nothing unsupported is left.
* **Explanations:** one line per dataset - "Submitted & Verified - ack ...270926 on a confirmation
  page + 'successfully e-verified', read twice, UIA."
* **Second opinions** - five dataset constraints, every one from config: the ack is only a
  reference to an earlier dataset; the ITR form does not fit the PAN's 4th letter; a quarterly
  form with a non-quarterly period; the identifier's own date outside its period; a form that
  belongs to another portal. They are said on the row; the Core's value stands until the rule
  graduates (14.2).

Privacy (14.5): the ledger lives in memory, per session, and is never written anywhere. Beliefs
are keyed by a salted hash; what reaches the row is labels the Core itself already put there
(form, period, status), a few trailing characters of an identifier where config says so (an
ITR ack's trailing DDMMYY), counts, scores and reasons - never a PAN, a name or a whole value.
"""

import json
import re
import secrets
import threading
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from . import assertions
from . import page_kinds
from . import page_map as pm
from .stats import salted_hash

__all__ = ["CONFIG_PATH", "load_config", "Sighting", "Belief", "Ledger", "period_window",
           "period_kind", "second_opinions", "explain", "LedgerComponent"]

CONFIG_PATH = Path(__file__).with_name("sgt_i_config.json")

# Used only if sgt_i_config.json is missing or corrupt: weights only, no constraint fires.
_FALLBACK_CONFIG: Dict[str, Any] = {
    "source": {"_none": 0.7}, "page_kind": {"_none": 0.7}, "assertion": {"_none": 0.8},
    "assertion_window": {"before": 3, "after": 2}, "client_fields": ["pan", "gstin", "tan"],
    "identifier_fields": ["arn"],
}

# Which wording near a value decides its sighting: a reference to the past outweighs anything
# else on the same few lines (the revised-dataset wizard's original ack, 14.1).
_ASSERTION_ORDER = ("reference_to_past", "negated", "happened", "future_conditional")
_READ_WORDS = {1: "once", 2: "twice"}
MAX_SESSIONS = 64
MAX_DATASETS_ON_ROW = 6
ROW_BUDGET_BYTES = 3800              # under host.ENRICH_MAX_BYTES, with room to spare
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")

_lock = threading.Lock()
_cache: Optional[Dict[str, Any]] = None
_cache_path: Optional[Path] = None


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """The "ledger" section of sgt_i_config.json, cached by path. Never raises."""
    global _cache, _cache_path
    p = path or CONFIG_PATH
    with _lock:
        if _cache is not None and _cache_path == p:
            return _cache
        cfg = _FALLBACK_CONFIG
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
            section = loaded.get("ledger") if isinstance(loaded, dict) else None
            if isinstance(section, dict):
                cfg = section
        except (OSError, ValueError, AttributeError):
            pass
        _cache = cfg
        _cache_path = p
        return cfg


def _weight(cfg: Dict[str, Any], table: str, key: Optional[str]) -> float:
    t = cfg.get(table) or {}
    return float(t.get(key or "_none", t.get("_none", 1.0)))


# ── The ledger itself ──────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Sighting:
    """One page a value was read on. Reasons only - never the value, never the page text."""
    id: int
    page: int                        # the session's page number
    kind: Optional[str]              # page kind (step 6), None = abstained
    assertion: Optional[str]         # what the wording around the value claims
    trigger: Optional[str]           # the config phrase that decided it
    source: str                      # "uia" / "ocr" / "pdf"
    route: Optional[str]             # the GPS's route, when it had one
    client: Optional[str]            # salted hash of the client the page was read under
    confidence: int                  # the Core's own confidence, 0-100
    weight: float


@dataclass
class Belief:
    """A value the Core captured for one dataset, and everything that makes SGT-I believe it.
    `value` stays in memory for the constraints; only its hash keys the belief."""
    key: Tuple[str, str, str]        # (dataset label, field, salted hash of the value)
    dataset: str
    field: str
    value: str
    sightings: Set[int] = field(default_factory=set)
    depends: Set[Tuple[str, str, str]] = field(default_factory=set)


class Ledger:
    """One session's beliefs. add() records a sighting; depend() ties one belief to another;
    retract() withdraws sightings and everything left unsupported (truth maintenance)."""

    def __init__(self, salt: Optional[str] = None) -> None:
        self._salt = salt or secrets.token_hex(16)     # per run, never written anywhere
        self.sightings: Dict[int, Sighting] = {}
        self.beliefs: Dict[Tuple[str, str, str], Belief] = {}
        self.retracted = 0
        self._next_id = 0

    def key(self, dataset: str, fld: str, value: str) -> Tuple[str, str, str]:
        return (dataset, fld, salted_hash(value, self._salt))

    def hash_client(self, fld: str, value: str) -> str:
        """"field:hash" - two identities only ever disagree when they are the same kind (a PAN
        against a PAN), so a GSTIN-only profile never contradicts a card's PAN."""
        return "%s:%s" % (fld, salted_hash(value, self._salt, domain="client"))

    def new_sighting(self, **kw: Any) -> Sighting:
        self._next_id += 1
        s = Sighting(id=self._next_id, **kw)
        self.sightings[s.id] = s
        return s

    def add(self, dataset: str, fld: str, value: str, sighting: Sighting) -> Belief:
        k = self.key(dataset, fld, value)
        b = self.beliefs.get(k)
        if b is None:
            b = self.beliefs[k] = Belief(key=k, dataset=dataset, field=fld, value=value)
        b.sightings.add(sighting.id)
        return b

    def depend(self, belief: Belief, on: Belief) -> None:
        if belief.key != on.key:
            belief.depends.add(on.key)

    def score(self, belief: Belief) -> float:
        miss = 1.0
        for sid in belief.sightings:
            miss *= 1.0 - max(0.0, min(1.0, self.sightings[sid].weight))
        return round(1.0 - miss, 3)

    def pages(self, belief: Belief) -> int:
        return len({self.sightings[sid].page for sid in belief.sightings})

    def retract(self, withdraw: Callable[[Sighting], bool]) -> List[Belief]:
        """Withdraw every sighting `withdraw` says came from the wrong place, then every belief
        that lost all its sightings or depends on a belief that is gone - repeated until stable."""
        gone_ids = {sid for sid, s in self.sightings.items() if withdraw(s)}
        if not gone_ids:
            return []
        for sid in gone_ids:
            del self.sightings[sid]
        for b in self.beliefs.values():
            b.sightings -= gone_ids
        dropped: List[Belief] = []
        changed = True
        while changed:
            changed = False
            for k, b in list(self.beliefs.items()):
                if not b.sightings or any(d not in self.beliefs for d in b.depends):
                    dropped.append(self.beliefs.pop(k))
                    changed = True
        self.retracted += len(dropped)
        return dropped

    def datasets(self) -> List[str]:
        seen: List[str] = []
        for b in self.beliefs.values():
            if b.dataset not in seen:
                seen.append(b.dataset)
        return seen

    def best(self, dataset: str) -> Dict[str, Belief]:
        """The best-supported belief of each field of one dataset."""
        out: Dict[str, Belief] = {}
        for b in self.beliefs.values():
            if b.dataset != dataset:
                continue
            cur = out.get(b.field)
            if cur is None or (self.score(b), self.pages(b)) > (self.score(cur), self.pages(cur)):
                out[b.field] = b
        return out


# ── Periods ────────────────────────────────────────────────────────────────────────────────────

def period_kind(period: str, cfg: Dict[str, Any]) -> Optional[str]:
    """quarterly / monthly / annual from the period's own wording (config), or None."""
    low = (period or "").lower()
    for kind in ("quarterly", "monthly", "annual"):
        if any(re.search(p, low) for p in (cfg.get("period_kinds") or {}).get(kind, ())):
            return kind
    return None


def _year_pair(text: str, tag: str) -> Optional[int]:
    m = re.search(tag + r"\.?\s*y\.?\s*(20\d{2})\s*-\s*\d{2,4}", text)
    return int(m.group(1)) if m else None


def period_window(period: str) -> Optional[Tuple[str, date, date]]:
    """(basis, first day, last day) of a period the Core labelled: "AY 2025-26",
    "FY 2025-26", "June (FY 2026-27)", "Q1 (FY 2026-27)". None when it cannot be told."""
    low = (period or "").lower()
    fy = _year_pair(low, "f")
    ay = _year_pair(low, "a")
    month = next((i + 1 for i, m in enumerate(_MONTHS) if re.search(r"\b" + m + r"[a-z]*\b", low)), None)
    quarter = re.search(r"\bq([1-4])\b", low)
    if fy is not None and quarter:
        start_month = 4 + 3 * (int(quarter.group(1)) - 1)
        y = fy + (start_month - 1) // 12
        start = date(y, (start_month - 1) % 12 + 1, 1)
        end_month = start.month + 2
        end = date(start.year, end_month + 1, 1) - timedelta(days=1) if end_month < 12 \
            else date(start.year, 12, 31)
        return "quarter", start, end
    if fy is not None and month:
        y = fy if month >= 4 else fy + 1
        start = date(y, month, 1)
        end = (date(y + 1, 1, 1) if month == 12 else date(y, month + 1, 1)) - timedelta(days=1)
        return "month", start, end
    if ay is not None:
        return "ay", date(ay, 4, 1), date(ay + 1, 3, 31)
    if fy is not None:
        return "fy", date(fy, 4, 1), date(fy + 1, 3, 31)
    return None


def _identifier_date(value: str, form: str, rule: Dict[str, Any]) -> Optional[date]:
    if rule.get("forms") and not re.search(rule["forms"], form or "", re.I):
        return None
    m = re.search(rule.get("pattern") or "$^", value or "")
    if not m:
        return None
    parts = dict(zip(rule.get("order") or "DMY", (int(g) for g in m.groups())))
    try:
        return date(2000 + parts["Y"] if parts["Y"] < 100 else parts["Y"], parts["M"], parts["D"])
    except (KeyError, ValueError):
        return None


# ── Second opinions and explanations ───────────────────────────────────────────────────────────

def _norm_form(form: str) -> str:
    return re.sub(r"\s+", "", (form or "").upper())


def second_opinions(led: Ledger, dataset: str, portal: str, profile: Dict[str, str],
                    cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
    """Where SGT-I disagrees with what the Core captured for one dataset. Each is
    {"rule": ..., "says": ...}; the Core's value stands whatever is said here."""
    cfg = cfg if cfg is not None else load_config()
    best = led.best(dataset)
    val = {f: b.value for f, b in best.items()}
    form = _norm_form(val.get("form", ""))
    out: List[Dict[str, str]] = []

    # 1. The ack is only a reference to an earlier dataset: every sighting of it sat beside
    #    reference-to-the-past wording (14.1: the revised-dataset wizard's original ack).
    for fld in cfg.get("identifier_fields") or ():
        b = best.get(fld)
        if b is None:
            continue
        seen = [led.sightings[sid] for sid in b.sightings]
        if seen and all(s.assertion == "reference_to_past" for s in seen):
            out.append({"rule": "reference_ack",
                        "says": "this %s looks like a reference to an earlier dataset ('%s')"
                                % (_field_word(fld), seen[0].trigger or "reference")})

    # 2. The ITR form against the PAN's 4th letter (the holder's category).
    pan = val.get("pan") or (profile or {}).get("pan") or ""
    if form and len(pan) >= 4 and pan[3].isalpha():
        letter = pan[3].upper()
        for pat, letters in (cfg.get("form_pan_letters") or {}).items():
            if re.search(pat, form) and letter not in letters:
                who = (cfg.get("pan_letter_names") or {}).get(letter, "a '%s'-category" % letter)
                out.append({"rule": "form_vs_pan",
                            "says": "%s with %s PAN (%s) cannot be right" % (form, who, letter)})
                break

    # 3. A quarterly (or annual) form against the period's own kind.
    period = val.get("period", "")
    have = period_kind(period, cfg) if period else None
    if form and have:
        for pat, need in (cfg.get("form_periods") or {}).items():
            if re.search(pat, form) and need != have:
                out.append({"rule": "form_vs_period",
                            "says": "%s is filed %s but the period reads %s" % (form, need, have)})
                break

    # 4. The identifier's own date inside its period (plus the config's grace for the basis).
    window = period_window(period) if period else None
    exempt = {t.lower() for t in cfg.get("exempt_filing_types") or ()}
    if window and val.get("filing_type", "").lower() not in exempt:
        basis, start, end = window
        grace = int((cfg.get("period_grace_days") or {}).get(basis, 0))
        for rule in cfg.get("identifier_dates") or ():
            ident = val.get(rule.get("field", ""))
            when = _identifier_date(ident, form, rule) if ident else None
            if when is not None and not (start <= when <= end + timedelta(days=grace)):
                out.append({"rule": "identifier_date",
                            "says": "the %s's own date falls outside %s" % (_field_word(rule["field"]), period)})
                break

    # 5. The form belongs to another portal.
    table = cfg.get("portal_forms") or {}
    here = next((k for k in table if k in (portal or "").lower()), None)
    if form and here is not None and not any(re.search(p, form) for p in table[here]):
        other = next((k for k, pats in table.items() if k != here and any(re.search(p, form) for p in pats)), None)
        if other:
            out.append({"rule": "form_portal",
                        "says": "%s is a %s form, not %s" % (form, other, portal)})
    return out


def _field_word(fld: str) -> str:
    return {"arn": "ack"}.get(fld, fld)


def explain(led: Ledger, dataset: str, cfg: Optional[Dict[str, Any]] = None) -> str:
    """One line: what is believed about this dataset and why."""
    cfg = cfg if cfg is not None else load_config()
    best = led.best(dataset)
    if not best:
        return ""
    status = best.get("status")
    ident = next((best[f] for f in cfg.get("identifier_fields") or () if f in best), None)
    lead = ident or status or max(best.values(), key=led.score)
    sightings = sorted((led.sightings[sid] for sid in lead.sightings), key=lambda s: -s.weight)
    top = sightings[0]
    head = status.value if status else dataset
    what = ""
    if ident is not None:
        tail = int((cfg.get("show_tail") or {}).get(ident.field, 0))
        what = "%s …%s " % (_field_word(ident.field), ident.value[-tail:]) if tail > 0 \
            else "%s " % _field_word(ident.field)
    where = "on a %s page" % top.kind.replace("_", " ") if top.kind else "on a page"
    wording = " + '%s'" % top.trigger if top.trigger else ""
    n = led.pages(lead)
    reads = "read %s" % _READ_WORDS.get(n, "%d times" % n)
    sources = "/".join(sorted({s.source.upper() for s in sightings if s.source})) or "?"
    return "%s - %s%s%s, %s, %s." % (head, what, where, wording, reads, sources)


# ── The component ──────────────────────────────────────────────────────────────────────────────

def _page_kind(lines: Sequence[str], cfg: Dict[str, Any]) -> page_kinds.PageKind:
    """Step 6's page kind from the Core's lines: the Observation carries lines, not nodes (the
    shared read is not adopted yet, 14.2), so each line becomes a node stacked in reading order.
    Lines carry no geometry, so no header band: every line counts as main content."""
    if not lines or len(lines) > int(cfg.get("max_lines_for_page_kind", 400)):
        return page_kinds.PageKind(None, ())
    boxes = [{"text": t, "x": 20, "y": 24 * i, "width": 8 * max(1, len(t)), "height": 18}
             for i, t in enumerate(lines)]
    return page_kinds.classify(pm.build_page_map(pm.nodes_from_ocr(boxes), header_band_px=0))


def _assertion_near(lines: Sequence[str], value: str, cfg: Dict[str, Any]) -> assertions.Assertion:
    """What the wording just around `value` claims, strongest class first (_ASSERTION_ORDER)."""
    win = cfg.get("assertion_window") or {}
    before, after = int(win.get("before", 3)), int(win.get("after", 2))
    found: Dict[str, assertions.Assertion] = {}
    for i, line in enumerate(lines):
        if value and value in line:
            for j in range(max(0, i - before), min(len(lines), i + after + 1)):
                a = assertions.classify(lines[j].replace(value, " "))
                if a.cls and a.cls not in found:
                    found[a.cls] = a
    for cls in _ASSERTION_ORDER:
        if cls in found:
            return found[cls]
    return assertions.Assertion(None, None)


def _dataset_label(values: Dict[str, str], fallback: str) -> str:
    parts = [values.get("form", ""), values.get("period", "")]
    return " ".join(p for p in parts if p) or fallback


class _Session:
    __slots__ = ("ledger", "page", "clients")

    def __init__(self) -> None:
        self.ledger = Ledger()
        self.page = 0
        self.clients: Dict[str, str] = {}            # identity field -> "field:hash" of the client

    def foreign(self, s: Sighting) -> bool:
        """Read under another client than this session's (same identity field, other hash)."""
        if not s.client:
            return False
        mine = self.clients.get(s.client.split(":", 1)[0])
        return mine is not None and mine != s.client


class LedgerComponent:
    """The step-7 SGT-I component. Reads what the Core captured on each page (a frozen copy),
    records it in the session's ledger, and enriches the row with explanations, scores and
    second opinions. It never changes a Core value - it has no way to."""

    name = "ledger"

    def __init__(self, gps: Any = None, config: Optional[Dict[str, Any]] = None) -> None:
        self._gps = gps
        self._cfg = config
        self._sessions: Dict[str, _Session] = {}

    def _session(self, sid: str) -> _Session:
        st = self._sessions.pop(sid, None) or _Session()
        self._sessions[sid] = st
        while len(self._sessions) > MAX_SESSIONS:
            del self._sessions[next(iter(self._sessions))]
        return st

    def _client(self, led: Ledger, values: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[str]:
        for f in cfg.get("client_fields") or ():
            v = values.get(f)
            if v:
                return led.hash_client(f, v)
        return None

    def observe(self, obs: Any, ctx: Any) -> None:
        if getattr(obs, "event", ""):
            return            # a flash (step 9) carries no Core capture: not a page of evidence
        cfg = self._cfg if self._cfg is not None else load_config()
        st = self._session(obs.session_id)
        led = st.ledger
        st.page += 1
        profile = {k: str(v) for k, v in dict(obs.profile).items()}

        # Truth maintenance: this session's client is now someone else - what was read under the
        # previous client is not evidence for this one.
        changed = False
        for f in cfg.get("client_fields") or ():
            if profile.get(f):
                h = led.hash_client(f, profile[f])
                changed |= st.clients.get(f) not in (None, h)
                st.clients[f] = h
        if changed:
            led.retract(st.foreign)
        client = self._client(led, profile, cfg)

        result = dict(obs.result or {})
        pieces: List[Tuple[str, Dict[str, str], Dict[str, int]]] = []
        for i, d in enumerate(result.get("datasets") or ()):
            vals = {k: str(v) for k, v in dict(d.get("values") or {}).items() if v}
            conf = {k: int(d.get("confidence") or 0) for k in vals}
            pieces.append((_dataset_label(vals, d.get("record") or "dataset %d" % (i + 1)), vals, conf))
        current = {k: dict(h) for k, h in dict(result.get("current") or {}).items()}
        if current and not result.get("is_list"):
            vals = {k: str(h.get("value")) for k, h in current.items() if h.get("value")}
            conf = {k: int(h.get("confidence") or 0) for k, h in current.items()}
            draft = {k: str(v) for k, v in dict(obs.draft or {}).items()}
            pieces.append((_dataset_label({**draft, **vals}, "dataset in progress"), vals, conf))

        if pieces:
            kind = _page_kind(obs.lines, cfg).kind
            route = self._gps.last_route(obs.session_id) if self._gps is not None else None
            for label, vals, conf in pieces:
                # A card naming another client than the session's is another client's evidence.
                own = self._client(led, vals, cfg)
                who = own or client
                beliefs: Dict[str, Belief] = {}
                for fld, value in vals.items():
                    a = _assertion_near(obs.lines, value, cfg)
                    c = max(0, min(100, conf.get(fld, 0))) or 80
                    w = (_weight(cfg, "source", obs.source) * _weight(cfg, "page_kind", kind)
                         * _weight(cfg, "assertion", a.cls) * c / 100.0)
                    s = led.new_sighting(page=st.page, kind=kind, assertion=a.cls, trigger=a.trigger,
                                         source=obs.source or "", route=route, client=who,
                                         confidence=c, weight=round(w, 4))
                    beliefs[fld] = led.add(label, fld, value, s)
                ident = next((beliefs[f] for f in cfg.get("identifier_fields") or () if f in beliefs), None)
                if ident is not None and "status" in beliefs:
                    led.depend(beliefs["status"], ident)
            led.retract(st.foreign)

        self._enrich(obs, ctx, st, profile, cfg)

    def _enrich(self, obs: Any, ctx: Any, st: _Session, profile: Dict[str, str],
                cfg: Dict[str, Any]) -> None:
        led = st.ledger
        rows: List[Dict[str, Any]] = []
        for ds in led.datasets()[-MAX_DATASETS_ON_ROW:]:
            best = led.best(ds)
            row: Dict[str, Any] = {"dataset": ds, "explanation": explain(led, ds, cfg),
                                   "confidence": {f: led.score(b) for f, b in sorted(best.items())
                                                  if f not in (cfg.get("client_fields") or ())}}
            opinions = second_opinions(led, ds, obs.portal, profile, cfg)
            if opinions:
                row["second_opinions"] = opinions
            rows.append(row)
        if not rows and not led.retracted:
            return
        data: Dict[str, Any] = {"datasets": rows}
        if led.retracted:
            data["retracted"] = led.retracted
        while rows and len(json.dumps(data, ensure_ascii=False).encode("utf-8")) > ROW_BUDGET_BYTES:
            rows.pop(0)
        ctx.enrich(data)
