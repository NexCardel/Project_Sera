"""
core/sgt/sgt_resolver.py — lines of text + field specs -> datapoints
=====================================================================
The resolver only ever sees LINES OF TEXT. It does not know or care whether they came from
UI Automation or from OCR, so a canvas-painted portal read by OCR goes through exactly the
same rules as an ordinary one read by UIA.

One value, found once, is never enough on its own:
  * a value must match its spec's shape, survive its transforms and pass its checks;
  * a label only counts when the text after it IS the value (or is empty and the value
    follows): "Name of the Bank" is not the label "Name";
  * a profile datapoint that shows two different values on one page is ambiguous and is
    dropped for that page (reported as a conflict) rather than guessed;
  * a dataset is only produced when its record's required fields are all present.

Pure functions, no I/O, no page text in the output - only the resolved values.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .sgt_specs import MAX_LINE_LEN, FieldSpec, RecordSpec, Registry
from .sgt_toolbox import CHECKS, TRANSFORMS, period_sort_key

# Icon-font glyphs (Unicode private use area) that UIA reports as text on the portals.
_PRIVATE_USE = re.compile("[-]")
_SPACES = re.compile(r"\s+")


def prepare_lines(lines: Iterable[Any]) -> List[str]:
    """Strips icon glyphs, collapses whitespace, bounds line length, drops empty lines."""
    out = []
    for ln in lines or ():
        s = _SPACES.sub(" ", _PRIVATE_USE.sub(" ", str(ln))).strip()
        if s:
            out.append(s[:MAX_LINE_LEN])
    return out


@dataclass
class Hit:
    field: str
    value: str
    confidence: int
    spec: str
    line: int
    rank: int = 0           # position of the matching map entry (for pick "map_order")
    source: str = "page"    # page / link / title

    def as_dict(self) -> Dict[str, Any]:
        return {"value": self.value, "confidence": self.confidence, "spec": self.spec}


@dataclass
class Dataset:
    record: str
    fields: Dict[str, Hit]
    line: int

    @property
    def confidence(self) -> int:
        return min((h.confidence for h in self.fields.values()), default=0)

    def values(self) -> Dict[str, str]:
        return {k: h.value for k, h in self.fields.items()}

    def as_dict(self) -> Dict[str, Any]:
        return {"record": self.record, "confidence": self.confidence, "values": self.values()}


@dataclass
class PageResult:
    profile: Dict[str, Hit] = field(default_factory=dict)
    datasets: List[Dataset] = field(default_factory=list)
    # Pieces of the return being worked on (form, period, filing type...) seen on this page.
    current: Dict[str, Hit] = field(default_factory=dict)
    # True when the page listed returns (a record with a "start" matched): a list page shows
    # many returns, so it never feeds the return being worked on.
    is_list: bool = False
    conflicts: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {"profile": {k: h.as_dict() for k, h in self.profile.items()},
                "datasets": [d.as_dict() for d in self.datasets],
                "current": {k: h.as_dict() for k, h in self.current.items()},
                "is_list": self.is_list,
                "conflicts": list(self.conflicts)}

    @property
    def empty(self) -> bool:
        return not self.profile and not self.datasets and not self.current


# ── One value ────────────────────────────────────────────────────────────────────
def _finish(spec: FieldSpec, raw: str, today: date) -> Optional[Tuple[str, int]]:
    value: Optional[str] = raw
    for t in spec.transforms:
        value = TRANSFORMS[t](value)
        if not value:
            return None
    rank = 0
    if spec.value_map:
        for i, (rx, out) in enumerate(spec.value_map):
            if rx.search(value):
                value, rank = out, i
                break
        else:
            return None                 # outside the known vocabulary
        if not value:
            return None                 # mapped to null: known, but not a datapoint
    for c in spec.checks:
        if not CHECKS[c](value, today):
            return None
    return value, rank


def extract_value(spec: FieldSpec, text: str, today: date) -> Optional[Tuple[str, int]]:
    """The first value in `text` that has the spec's shape and passes its tools."""
    for m in spec.pattern.finditer(text or ""):
        raw = next((g for g in m.groups() if g), None) if m.re.groups else m.group(0)
        if not raw:
            continue
        got = _finish(spec, raw, today)
        if got:
            return got
    return None


# ── One spec over a range of lines ───────────────────────────────────────────────
def _hits(spec: FieldSpec, lines: Sequence[str], lo: int, hi: int, today: date) -> List[Hit]:
    hits: List[Hit] = []

    def add(line: int, got: Tuple[str, int]) -> None:
        hits.append(Hit(spec.field, got[0], spec.confidence, spec.name, line, got[1], spec.source))

    if spec.take == "anywhere":
        for i in range(lo, hi):
            for m in spec.pattern.finditer(lines[i]):
                raw = next((g for g in m.groups() if g), None) if m.re.groups else m.group(0)
                got = _finish(spec, raw, today) if raw else None
                if got:
                    add(i, got)
        return hits

    if spec.take == "nearest_above":
        for i in range(lo - 1, -1, -1):
            got = extract_value(spec, lines[i], today)
            if got:
                add(i, got)
                break
        return hits

    # after_label
    assert spec.label_re is not None
    for i in range(lo, hi):
        m = spec.label_re.search(lines[i])
        if not m:
            continue
        rest = m.group("rest").strip()
        if rest and spec.label_rest != "ignore":
            if spec.label_rest == "value" or m.group("sep"):
                got = extract_value(spec, rest, today)
                if got:
                    add(i, got)
                    continue
            continue                    # "PAN Status" / "Name of the Bank" is a different label
        for j in range(i + 1, min(hi, i + 1 + spec.within)):
            if spec.label_re.search(lines[j]) and not spec.label_re.search(lines[j]).group("rest").strip():
                continue                # the same label repeated (UIA reports some twice)
            if spec.stop_re is not None and spec.stop_re.match(lines[j]):
                break                   # another field's label: this field is empty on the page
            got = extract_value(spec, lines[j], today)
            if got:
                add(j, got)
                break
    return hits


def _source_lines(spec: FieldSpec, lines: Sequence[str], link: str, title: str) -> List[str]:
    if spec.source == "link":
        return prepare_lines([link])
    if spec.source == "title":
        return prepare_lines([title])
    return prepare_lines(lines)


def resolve_single(spec: FieldSpec, lines: Sequence[str], today: date, link: str = "", title: str = "",
                   conflicts: Optional[List[str]] = None) -> Optional[Hit]:
    """
    ONE value from one spec over a whole page (or its link, or its window title) - a profile
    datapoint, or a piece of the return being worked on. A page showing several different
    values is a list, not THE value: with "multiple": "conflict" nothing is taken; with
    "latest_period" the latest period among them is (a page naming several years is about the
    latest one - e.g. a filing page that also cites "available starting Assessment Year 2013-14").
    """
    src = _source_lines(spec, lines, link, title)
    hits = _hits(spec, src, 0, len(src), today)
    if not hits:
        return None
    distinct = {h.value for h in hits}
    if len(distinct) == 1:
        return hits[0]
    if spec.multiple == "latest_period":
        dated = [(period_sort_key(h.value), h) for h in hits]
        dated = [(k, h) for k, h in dated if k is not None]
        if dated:
            return max(dated, key=lambda kh: kh[0])[1]
    if conflicts is not None:
        conflicts.append(f"{spec.name}: {len(distinct)} different values on one page")
    return None


def resolve_profile_field(spec: FieldSpec, lines: Sequence[str], today: date,
                          conflicts: Optional[List[str]] = None) -> Optional[Hit]:
    """A profile datapoint from one spec over a page's text (see resolve_single)."""
    return resolve_single(spec, lines, today, conflicts=conflicts)


def latest_period_only(datasets: List[Dataset]) -> List[Dataset]:
    """
    When one page shows several returns, only those with the latest period are kept (several
    forms for that same latest period all stay). Returns without a readable period are dropped
    when others have one; if none has one, all are kept.
    """
    if len(datasets) < 2:
        return datasets
    keyed = [(period_sort_key(d.values().get("period")), d) for d in datasets]
    known = [k for k, _ in keyed if k is not None]
    if not known:
        return datasets
    top = max(known)
    return [d for k, d in keyed if k == top]


def _pick(spec: FieldSpec, hits: List[Hit]) -> Optional[Hit]:
    if not hits:
        return None
    if spec.pick == "map_order":
        return min(hits, key=lambda h: (h.rank, h.line))
    return hits[0]


def _blocks(spec: RecordSpec, lines: Sequence[str]) -> List[Tuple[int, int]]:
    if spec.start is None:
        return [(0, len(lines))] if lines else []
    starts = [i for i, ln in enumerate(lines) if spec.start.search(ln)]
    out = []
    for n, s in enumerate(starts):
        nxt = starts[n + 1] if n + 1 < len(starts) else len(lines)
        out.append((s, min(nxt, s + spec.max_lines)))
    return out


def resolve_records(spec: RecordSpec, lines: Sequence[str], today: date) -> List[Dataset]:
    lines = prepare_lines(lines)
    out: List[Dataset] = []
    for lo, hi in _blocks(spec, lines):
        found: Dict[str, Hit] = {}
        for f in spec.fields:
            if f.field in found:
                continue                # an earlier spec for the same field already answered
            hit = _pick(f, _hits(f, lines, lo, hi, today))
            if hit:
                found[f.field] = hit
        if found and all(r in found for r in spec.require):
            out.append(Dataset(spec.name, found, lo))
    return out


# ── A whole page ─────────────────────────────────────────────────────────────────
def _best(into: Dict[str, Hit], hit: Hit) -> None:
    held = into.get(hit.field)
    if held is None or hit.confidence > held.confidence:
        into[hit.field] = hit


def resolve_page(registry: Registry, lines: Sequence[str], portal: str, url: str, today: date,
                 skip_profile: Optional[Set[str]] = None, title: str = "") -> PageResult:
    """
    Everything the specs can find on one page. `skip_profile` names profile datapoints the
    session has already latched - the profile builder stops looking for them, so they cost
    nothing on later pages. Dataset fields are never skipped: status must keep updating.
    """
    lines = prepare_lines(lines)
    skip = skip_profile or set()
    result = PageResult()
    for spec in registry.profile:
        if spec.field in skip or not spec.applies(portal, url):
            continue
        hit = resolve_single(spec, lines, today, link=url, title=title, conflicts=result.conflicts)
        if hit:
            _best(result.profile, hit)
    for rec in registry.records:
        if rec.applies(portal, url):
            found = resolve_records(rec, lines, today)
            result.datasets.extend(found)
            if found and rec.start is not None:
                result.is_list = True
    result.datasets = latest_period_only(result.datasets)
    for spec in registry.current:
        if spec.applies(portal, url):
            hit = resolve_single(spec, lines, today, link=url, title=title, conflicts=result.conflicts)
            if hit:
                _best(result.current, hit)
    return result
