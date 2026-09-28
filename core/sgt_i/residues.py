"""
core/sgt_i/residues.py - what no spec claimed (step 10)
=======================================================
Blueprint 14.4 step 10: SGT-I tells you where it is blind. Every page leaves a residue: typed
values that no Core spec claimed. The report then reads, for example, "confirmation pages: code
AAAAA9999A seen 4x, claimed 0x" - silent misses made visible, each one a lead for the miner.

Runs on SGT-I's own thread like every component (14.2 rule 5), so the Core does no extra work and
SGT-I Off = no residues at all. Generic throughout (rule: global, config-free of portal wording):
the page kind is step 6's (page_kinds.classify), the type and shape are step 2's (pairs.py), over
the same line-stacked page map ledger.py and page_diff.py build.

Privacy (14.5 rule 1): a value is held only long enough to compare it with the Core's captured
values and mask it. The file `sgt_i/residues.json` keeps portal -> page kind -> "type shape" ->
{"seen", "claimed"} counts, nothing else. Counts are per page visit, not per read (see
ResiduesComponent._visit_increment); the in-memory visit state holds masked keys only.
"""

import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from . import page_kinds
from .page_view import map_from_lines, page_for, split_inline_labels  # noqa: F401 (re-exported)
from .pairs import classify_type, mask_shape
from .stats import sgt_i_dir

RESIDUES_FILE = "residues.json"
SAVE_EVERY_SEC = 60.0
MAX_LINES = 400                  # same bound as ledger's page kind: a huge table is not worth mapping
MAX_SHAPE_LEN = 40               # a longer "value" is prose, not a typed value
MAX_KEYS_PER_KIND = 256          # bounds the file; a new key past it is dropped, counts go on
UNTYPED = frozenset({"text", "choice", "yes/no"})   # not typed values: nothing a spec would miss
UNKNOWN_KIND = "unknown"
_COUNTED_ZONES = frozenset({"main", "dialog", "header"})   # never navigation / footer / furniture
MAX_OPEN_VISITS = 16             # sessions whose current page visit is remembered (in memory only)


def _norm(value: Any) -> str:
    return "".join(str(value or "").split()).casefold()


def claimed_values(result: Any) -> Set[str]:
    """Every value the Core captured on the page (PageResult.as_dict()), normalised."""
    out: Set[str] = set()
    result = result or {}
    for section in ("profile", "current"):
        for hit in (result.get(section) or {}).values():
            if hasattr(hit, "get") and hit.get("value"):
                out.add(_norm(hit.get("value")))
    for ds in result.get("datasets") or ():
        for v in ((ds.get("values") if hasattr(ds, "get") else None) or {}).values():
            if v:
                out.add(_norm(v))
    out.discard("")
    return out


def _is_claimed(value: str, claimed: Set[str]) -> bool:
    """Claimed when the Core holds the same value - or one inside the other, since a spec's
    transforms may trim a prefix or a date tail. Short values only match exactly."""
    v = _norm(value)
    if v in claimed:
        return True
    return len(v) >= 6 and any(len(c) >= 6 and (c in v or v in c) for c in claimed)


def page_residue(lines: Sequence[str], result: Any) -> Optional[Dict[str, Any]]:
    """One page -> {"kind": page kind, "seen": {"type shape": n}, "claimed": {...}}, or None when
    the page holds no typed value. Pure; no value leaves this function."""
    lines = [ln for ln in lines if ln][:MAX_LINES]
    if not lines:
        return None
    return residue_of_map(map_from_lines(split_inline_labels(lines)), result)


def residue_of_map(page: Any, result: Any) -> Optional[Dict[str, Any]]:
    """page_residue over a page map already built (page_view: nodes or lines, minus furniture) -
    values in the menu, header strip or footer are not residues."""
    claimed = claimed_values(result)
    seen: Dict[str, int] = {}
    got: Dict[str, int] = {}
    for p in page.pairs:
        if p.zone not in _COUNTED_ZONES:
            continue
        typ = classify_type(p.value, p.method)
        shape = mask_shape(p.value.strip())
        if typ in UNTYPED or not shape or len(shape) > MAX_SHAPE_LEN:
            continue
        key = f"{typ} {shape}"
        seen[key] = seen.get(key, 0) + 1
        if _is_claimed(p.value, claimed):
            got[key] = got.get(key, 0) + 1
    if not seen:
        return None
    return {"kind": page_kinds.classify(page).kind or UNKNOWN_KIND, "seen": seen, "claimed": got}


class ResidueCounts:
    """The counts on disk, under sgt_i_dir(). Debounced tmp+replace save, like stats.py."""

    def __init__(self, directory: Optional[Path] = None, clock=None) -> None:
        self._dir = directory
        self._clock = clock or time.time
        self._dirty = False
        self._last_save = 0.0
        self.data: Dict[str, Any] = {}
        self._load()

    @property
    def directory(self) -> Path:
        return self._dir or sgt_i_dir()

    def add(self, portal: str, residue: Dict[str, Any]) -> None:
        cell = self.data.setdefault(portal or "?", {}).setdefault(residue["kind"], {})
        for key, n in residue["seen"].items():
            c = cell.get(key)
            if c is None:
                if len(cell) >= MAX_KEYS_PER_KIND:
                    continue
                c = cell[key] = {"seen": 0, "claimed": 0}
            c["seen"] += n
            c["claimed"] += residue["claimed"].get(key, 0)
        self._dirty = True
        if self._clock() - self._last_save >= SAVE_EVERY_SEC:
            self.save()

    def report(self, portal: Optional[str] = None) -> List[str]:
        """The blind spots, worst first: "<portal> <kind> pages: <type shape> seen Nx, claimed Mx"."""
        rows = []
        for p, kinds in self.data.items():
            if portal and p != portal:
                continue
            for kind, cell in kinds.items():
                for key, c in cell.items():
                    missed = c["seen"] - c["claimed"]
                    if missed > 0:
                        rows.append((-missed, f"{p} {kind} pages: {key} seen {c['seen']}x, claimed {c['claimed']}x"))
        return [text for _, text in sorted(rows)]

    def save(self) -> None:
        if not self._dirty:
            return
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / RESIDUES_FILE
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data), encoding="utf-8")
            tmp.replace(path)
            self._dirty = False
            self._last_save = self._clock()
        except OSError:
            pass

    def _load(self) -> None:
        try:
            loaded = json.loads((self.directory / RESIDUES_FILE).read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.data = loaded
        except (OSError, ValueError):
            pass


class ResiduesComponent:
    """The step-10 SGT-I component. It counts; it never enriches a row or asks for a read."""

    name = "residues"

    def __init__(self, counts: Optional[ResidueCounts] = None, atlas: Any = None) -> None:
        self._counts = counts
        self._atlas = atlas                               # learnt furniture (shared with the atlas step)
        self._visits: Dict[str, Dict[str, Any]] = {}     # session id -> the page visit in progress

    @property
    def counts(self) -> ResidueCounts:
        if self._counts is None:
            self._counts = ResidueCounts()
        return self._counts

    def observe(self, obs: Any, ctx: Any) -> None:
        if obs.source == "uia_event":
            return                       # a flash is part of a page, not a page
        page = page_for(obs, self._atlas, split_labels=True, max_lines=MAX_LINES)
        residue = residue_of_map(page, obs.result) if page is not None else None
        if residue is None:
            return
        residue = self._visit_increment(obs, residue)
        if residue["seen"]:
            self.counts.add(obs.portal, residue)

    def _visit_increment(self, obs: Any, residue: Dict[str, Any]) -> Dict[str, Any]:
        """Count once per page VISIT, not per read. The Core hands over a new read whenever any
        line changes (a menu opens, another year's card, a clock), so one visit can be dozens of
        reads. A visit = consecutive reads with the same session and URL; within it each
        "kind + type shape" counts at its highest number seen on any read, so 40 reads of a list
        of 5 acks add 5, not 200. Leaving the URL and coming back is a new visit."""
        sid = getattr(obs, "session_id", "")
        where = (obs.portal, getattr(obs, "url", ""))
        visit = self._visits.get(sid)
        if visit is None or visit["where"] != where:
            visit = self._visits[sid] = {"where": where, "seen": {}, "claimed": {}}
            while len(self._visits) > MAX_OPEN_VISITS:
                self._visits.pop(next(iter(self._visits)))
        kind = residue["kind"]
        seen: Dict[str, int] = {}
        claimed: Dict[str, int] = {}
        for key, n in residue["seen"].items():
            mark = (kind, key)
            add_seen = n - visit["seen"].get(mark, 0)
            add_claimed = residue["claimed"].get(key, 0) - visit["claimed"].get(mark, 0)
            if add_seen > 0:
                seen[key] = add_seen
                visit["seen"][mark] = n
            if add_claimed > 0:
                claimed[key] = add_claimed
                visit["claimed"][mark] = residue["claimed"][key]
                seen.setdefault(key, 0)
        return {"kind": kind, "seen": seen, "claimed": claimed}
