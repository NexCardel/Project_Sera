"""
core/sgt_i/expectations.py - what Sera already knows: due filings and known-value anchoring
=============================================================================================
Blueprint 14.4 step 8, second half (core/sgt_i/sera_data.py is the accessor; this is the
component that consumes it). Two things, both second opinion only (14.2) - the Core's own
capture is never touched:

* **Expectations from the tracker.** `SeraData.due_services()` gives the client's own due
  universe; `SeraData.tracker_rows()` gives what is already filed. A dataset for a period that
  is already filed (a status in `filed_statuses`) is flagged "revision" (its own wording says so,
  `revision_wording`) or "duplicate" (it does not); a dataset that matches a due service with
  nothing filed for it yet is the prime candidate.
* **Self-healing (known-value anchoring).** `SeraData.known_values()` gives every field Sera
  already holds for this client, under the label the office gave it. When one of those values
  turns up on the page under a *different* label - "Date of Birth" renamed "DOB" is the
  blueprint's own example - the container is recorded as a candidate synonym for the miner
  (step 11) to turn into a new spec, once you approve it. A container whose own label already
  reads as the field (word overlap, or a pattern in `known_field_labels`) is not "new" and is
  never reported.

Privacy (14.5): a value is compared to a known value in memory only, exactly as
`sera_data.masked_confirms` compares a masked one; only the container's LABEL text is kept, never
the value itself, and never the value that was already there before the match.

The Observation carries lines, not nodes yet (the shared read is not adopted, 14.2) - like
ledger.py's page kind, one page map is built from the lines for this page alone, in reading order,
with no geometry beyond that.
"""

import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import page_map as pm
from .sera_data import SeraData

CONFIG_PATH = Path(__file__).with_name("sgt_i_config.json")

_FALLBACK_CONFIG: Dict[str, Any] = {
    "client_fields": ["pan", "gstin"], "filed_statuses": ["submitted", "filed", "verified"],
    "revision_wording": ["\\brevis", "\\bamend"], "service_form_aliases": {},
    "known_field_labels": {}, "max_synonyms": 6, "max_expectations": 6,
}

MAX_SESSIONS = 64
ENRICH_BUDGET_BYTES = 3800     # under host.ENRICH_MAX_BYTES, with room to spare

_lock = threading.Lock()
_cache: Optional[Dict[str, Any]] = None
_cache_path: Optional[Path] = None


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """The "expectations" section of sgt_i_config.json, cached by path. Never raises."""
    global _cache, _cache_path
    p = path or CONFIG_PATH
    with _lock:
        if _cache is not None and _cache_path == p:
            return _cache
        cfg = _FALLBACK_CONFIG
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
            section = loaded.get("expectations") if isinstance(loaded, dict) else None
            if isinstance(section, dict):
                cfg = section
        except (OSError, ValueError, AttributeError):
            pass
        _cache = cfg
        _cache_path = p
        return cfg


def _norm(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def _words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())}


# ── Expectations: due, revision, duplicate ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class Expectation:
    dataset: str
    verdict: str        # "prime_candidate" / "revision" / "duplicate"


def _form_matches_service(form: str, service: str, aliases: Dict[str, List[str]]) -> bool:
    patterns = aliases.get(service.lower()) or aliases.get(service) or ()
    if patterns and any(re.search(p, form, re.IGNORECASE) for p in patterns):
        return True
    return bool(form) and _norm(form) == _norm(service)


def _portal_match(a: str, b: str) -> bool:
    a, b = (a or "").lower().strip(), (b or "").lower().strip()
    return bool(a) and bool(b) and (a in b or b in a)


def classify_dataset(portal: str, form: str, period: str, status: str,
                     due_services: Sequence[str], filed_rows: Sequence[Any],
                     cfg: Dict[str, Any]) -> Optional[Expectation]:
    """One dataset's expectation, or None when there is nothing to say. `filed_rows` are the
    client's own tracker rows already restricted to this portal; matching is by period label,
    case-insensitive, exactly as the Core wrote it - Sera's tracker and SGT capture the same
    wording for the same portal."""
    filed_here = [r for r in filed_rows
                  if (r.period_label or "").strip().casefold() == (period or "").strip().casefold()]
    filed = [r for r in filed_here if (r.status or "").strip().lower() in
             {s.lower() for s in cfg.get("filed_statuses") or ()}]
    if filed:
        revised = any(re.search(p, status, re.IGNORECASE) or re.search(p, form, re.IGNORECASE)
                      for p in cfg.get("revision_wording") or ())
        return Expectation(dataset="", verdict="revision" if revised else "duplicate")
    aliases = cfg.get("service_form_aliases") or {}
    if form and any(_form_matches_service(form, s, aliases) for s in due_services):
        return Expectation(dataset="", verdict="prime_candidate")
    return None


# ── Self-healing: known-value anchoring ──────────────────────────────────────────────────────────

def label_is_known(label: str, field: str, cfg: Dict[str, Any]) -> bool:
    """True when `label` already reads as `field` - a shared significant word, or one of the
    field's own extra synonyms in config - so it is NOT a new wording worth reporting."""
    if _words(label) & _words(field):
        return True
    extra = (cfg.get("known_field_labels") or {}).get(field.lower(), ())
    return any(re.search(p, label, re.IGNORECASE) for p in extra)


def _client_known_pairs(client, known: Dict[str, str], tracker_rows: Sequence[Any]
                        ) -> List[Tuple[str, str]]:
    """(field, value) for everything Sera holds about this client - PAN/GSTIN, every labelled
    field, and every ack it has already seen filed."""
    out: List[Tuple[str, str]] = []
    if client.pan:
        out.append(("pan", client.pan))
    if client.gstin:
        out.append(("gstin", client.gstin))
    out.extend((label, value) for label, value in known.items() if value)
    seen_acks = {r.arn_number for r in tracker_rows if r.arn_number}
    out.extend(("ack", a) for a in seen_acks)
    return out


def _page_map_from_lines(lines: Sequence[str], max_lines: int) -> Optional[pm.PageMap]:
    if not lines or len(lines) > max_lines:
        return None
    boxes = [{"text": t, "x": 20, "y": 24 * i, "width": 8 * max(1, len(t)), "height": 18}
             for i, t in enumerate(lines)]
    return pm.build_page_map(pm.nodes_from_ocr(boxes), header_band_px=0)


def find_synonyms(page: pm.PageMap, known_pairs: Sequence[Tuple[str, str]],
                  cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every page-map pair whose value matches something Sera already holds for this client,
    under a label that does not already read as that field (14.4 step 8's self-healing). The
    value itself never leaves this function - only the field name and the container it was
    found under."""
    out: List[Dict[str, Any]] = []
    for p in page.pairs:
        if not p.value:
            continue
        for field, value in known_pairs:
            if p.value.strip().upper() != value.strip().upper():
                continue
            if label_is_known(p.label, field, cfg):
                continue
            container = page.section_path(p.section) + (p.label,)
            out.append({"field": field, "container": list(container)})
            break
    return out


# ── The component ──────────────────────────────────────────────────────────────────────────────

_MAX_LINES = 400


class _Session:
    __slots__ = ("seen_synonyms",)

    def __init__(self) -> None:
        self.seen_synonyms: set = set()


class ExpectationsComponent:
    """The step-8 SGT-I component. Reads Sera's own client list and tracker (`SeraData`, read-only)
    beside each page the Core resolved, and enriches the row with due/revision/duplicate
    expectations and candidate label synonyms for the miner (step 11). Never writes to the Core,
    never stores a raw value (14.5)."""

    name = "expectations"

    def __init__(self, sera: SeraData, config: Optional[Dict[str, Any]] = None) -> None:
        self._sera = sera
        self._cfg = config
        self._sessions: Dict[str, _Session] = {}

    def _session(self, sid: str) -> _Session:
        st = self._sessions.pop(sid, None) or _Session()
        self._sessions[sid] = st
        while len(self._sessions) > MAX_SESSIONS:
            del self._sessions[next(iter(self._sessions))]
        return st

    def _identify(self, profile: Dict[str, str], cfg: Dict[str, Any]):
        for f in cfg.get("client_fields") or ():
            v = profile.get(f)
            if not v:
                continue
            client = self._sera.find_by_pan(v) if f == "pan" else \
                self._sera.find_by_gstin(v) if f == "gstin" else None
            if client is not None:
                return client
        return None

    def _pieces(self, obs: Any) -> List[Dict[str, str]]:
        """Every dataset card, plus the dataset in progress, as a flat {field: value} dict -
        the same shape ledger.py's own component reduces obs.result to."""
        result = dict(obs.result or {})
        pieces: List[Dict[str, str]] = []
        for d in result.get("datasets") or ():
            vals = {k: str(v) for k, v in dict(d.get("values") or {}).items() if v}
            if vals:
                pieces.append(vals)
        current = {k: dict(h) for k, h in dict(result.get("current") or {}).items()}
        if current and not result.get("is_list"):
            vals = {k: str(h.get("value")) for k, h in current.items() if h.get("value")}
            draft = {k: str(v) for k, v in dict(obs.draft or {}).items()}
            merged = {**draft, **vals}
            if merged:
                pieces.append(merged)
        return pieces

    def observe(self, obs: Any, ctx: Any) -> None:
        cfg = self._cfg if self._cfg is not None else load_config()
        st = self._session(obs.session_id)
        profile = {k: str(v) for k, v in dict(obs.profile).items()}
        client = self._identify(profile, cfg)

        expectations: List[Dict[str, str]] = []
        synonyms: List[Dict[str, Any]] = []

        if client is not None:
            due = [d.service for d in self._sera.due_services(client.client_id)]
            filed_rows = [r for r in self._sera.tracker_rows(client.client_id)
                          if _portal_match(r.portal, obs.portal)]
            for vals in self._pieces(obs):
                form, period, status = vals.get("form", ""), vals.get("period", ""), vals.get("status", "")
                if not (form or period):
                    continue
                exp = classify_dataset(obs.portal, form, period, status, due, filed_rows, cfg)
                if exp is not None:
                    label = " ".join(x for x in (form, period) if x) or "dataset"
                    expectations.append({"dataset": label, "expectation": exp.verdict})

            known_pairs = _client_known_pairs(client, self._sera.known_values(client.client_id),
                                               filed_rows)
            page = _page_map_from_lines(obs.lines, int(cfg.get("max_lines_for_page_kind", _MAX_LINES)))
            if page is not None and known_pairs:
                for syn in find_synonyms(page, known_pairs, cfg):
                    key = (syn["field"], tuple(syn["container"]))
                    if key not in st.seen_synonyms:
                        st.seen_synonyms.add(key)
                        synonyms.append(syn)

        max_exp = int(cfg.get("max_expectations", 6))
        max_syn = int(cfg.get("max_synonyms", 6))
        data: Dict[str, Any] = {}
        if expectations:
            data["expectations"] = expectations[:max_exp]
        if synonyms:
            data["synonyms"] = synonyms[:max_syn]
        if not data:
            return
        while json.dumps(data, ensure_ascii=False).encode("utf-8").__len__() > ENRICH_BUDGET_BYTES:
            if data.get("synonyms"):
                data["synonyms"].pop()
            elif data.get("expectations"):
                data["expectations"].pop()
            else:
                break
        ctx.enrich(data)
