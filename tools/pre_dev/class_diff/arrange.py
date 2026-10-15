"""
tools/pre_dev/class_diff/arrange.py - PRE-DEV: the arrangement of a page (experiment 3)
=======================================================================================
Design: docs/sdis_experiments/cards-solution.md (sections 3 and 8). Build plan: docs/sdis_experiments/arrange-build-plan.md.
Pre-dev only; nothing in core/ changes (tables.py, keys.py and align.py are only imported).

What it does, in order (design section 3):
  1. leaves():  nodes that carry a text or a look (button, box, image) with a box, present at the end of
                the capture; a Table is one leaf; a node that came and stayed is flagged `attached`, a
                node with 2+ values is flagged `ticking`.
  2. tokens():  (kind, step to the next leaf). The step is one of nine directions from the boxes.
  3. find_groups(): tandem repeats of the token string with the last step free (period >= 2), phase by
                the smallest total member area, extension by edits, ranking, nesting, label witness.
  4. slots() / roles(): every member aligned to the group's first member; roles label / distinct /
                mixed / look / ticking / single per slot.
  5. kinds():   cards when a name slot is known (corpus R at or above a cut-off), list when the member
                count varies across the occurrences, fields when it is the same, unknown otherwise.

Cut-offs are measured, never picked: OVL and TOLX must lie in an empty band of the --steps histograms;
EDITS_PER_TOKEN must lie in the empty band of the --likeness histogram; the name cut-off for R is not
set (None) until at least three captures per link exist, so cards are not decided by default. A step
that depends on an unmeasured cut-off refuses to run, as THETA does in cards.py.

Usage:
    python tools/pre_dev/class_diff/arrange.py [FILE...]              # shape report (counts only)
    python tools/pre_dev/class_diff/arrange.py --steps [FILE...]      # overlap / dx histograms, OVL and TOLX bands
    python tools/pre_dev/class_diff/arrange.py --likeness [FILE...]   # edits per token, E band
    python tools/pre_dev/class_diff/arrange.py --recurrence [FILE...] # corpus R per slot per link

Counts, shapes, slot positions and pixel numbers only. Captures hold client data: no page value is printed.
"""

import argparse
import glob
import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "output"
ROOT = HERE.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
os.environ.setdefault("SDIS_DATA_DIR", str(OUT_DIR))

from core.sdis import tables as _tables   # noqa: E402  (shared with production: only read)

Box = Tuple[int, int, int, int]           # x, y, width, height
Token = Tuple[str, str]                   # (kind, step)

# Element types that are a leaf on their own (a look), besides any element with a text.
LOOK = frozenset({"Button", "CheckBox", "ComboBox", "Edit", "Image", "RadioButton"})

# ── measured cut-offs (see the --steps and --likeness bands; section 5 of the design) ──
OVL = 4                 # px: consecutive leaves overlap vertically by more than this = same line. Band 0..8 (middle 4).
TOLX = 5                # px: horizontal differences up to this are the same column. Band 1..10 (middle 5).
EDITS_PER_TOKEN = 0.14   # middle of the measured gap (0.037, 0.250): true members of the ITR pages and the GST page
                        # against the nearest window beside a group (one 4-leaf group at 0.250). --likeness re-measures it.
MIN_PERIOD = 2          # a one-leaf period is a direction, not an arrangement (bug 9)
NAME_CUT: Optional[float] = None   # corpus R cut-off for a name slot: NOT set until >= 3 captures per link


# ── 1. leaves ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Leaf:
    node: int            # index in the flat list
    kind: str            # element type; "Table" for a whole table
    box: Box
    text: str
    ticking: bool = False
    attached: bool = False


def _box(e: Dict[str, Any]) -> Optional[Box]:
    r = (e.get("node") or {}).get("rect")
    if not r or len(r) < 4 or r[2] <= 0 or r[3] <= 0:
        return None
    return int(r[0]), int(r[1]), int(r[2]), int(r[3])


def _is_table(e: Dict[str, Any]) -> bool:
    return _tables._is_table(e.get("node") or {})


def leaves(fl: List[Dict[str, Any]]) -> List[Leaf]:
    """Leaves in document order. Takes a comp page's node list (with `present`) or a flatten() list
    (no `present`: every node is present). A Table is one leaf; nothing inside a Table is a leaf."""
    inside = [False] * len(fl)          # strictly inside a Table
    for i, e in enumerate(fl):
        p = e.get("parent", -1)
        if p >= 0:
            inside[i] = inside[p] or _is_table(fl[p])
    out: List[Leaf] = []
    for i, e in enumerate(fl):
        if inside[i] or e.get("present", True) is False:
            continue
        box = _box(e)
        if box is None:
            continue
        ticking = len(e.get("values") or []) >= 2
        attached = e.get("origin") == "attached"
        if _is_table(e):
            out.append(Leaf(i, "Table", box, "", ticking, attached))
            continue
        text = e.get("text") or ""
        if text or e.get("type") in LOOK:
            out.append(Leaf(i, str(e.get("type")), box, text, ticking, attached))
    return out


# ── 2. steps and tokens ───────────────────────────────────────────────────────

def step(a: Box, b: Box) -> str:
    """Direction from box a to box b. Same line when the boxes overlap vertically by more than OVL:
    R (b starts at or past a's right edge), L (b ends at or before a's left edge), else O. Otherwise
    D / U (b is below / above), with R or L added when b's left edge differs from a's by more than TOLX."""
    xa, ya, wa, ha = a
    xb, yb, wb, hb = b
    vover = min(ya + ha, yb + hb) - max(ya, yb)
    if vover > OVL:
        if xb >= xa + wa - TOLX:
            return "R"
        if xb + wb <= xa + TOLX:
            return "L"
        return "O"
    d = "D" if yb > ya else "U"
    if xb - xa > TOLX:
        return d + "R"
    if xa - xb > TOLX:
        return d + "L"
    return d


def tokens(lv: Sequence[Leaf]) -> List[Token]:
    """One token per leaf: its kind and the step to the next leaf ('END' for the last)."""
    dirs = [step(a.box, b.box) for a, b in zip(lv, lv[1:])] + ["END"]
    return [(a.kind, d) for a, d in zip(lv, dirs)]


def step_samples(lv: Sequence[Leaf]) -> Tuple[List[int], List[int]]:
    """Measurement only: vertical overlap (0 when none) of every consecutive pair, and |dx| of the pairs
    that do not overlap vertically at all (they are on another line)."""
    overlaps, dxs = [], []
    for a, b in zip(lv, lv[1:]):
        vover = min(a.box[1] + a.box[3], b.box[1] + b.box[3]) - max(a.box[1], b.box[1])
        overlaps.append(max(0, vover))
        if vover <= 0:
            dxs.append(abs(b.box[0] - a.box[0]))
    return overlaps, dxs


def empty_bands(values: Sequence[int], min_width: int = 2) -> List[Tuple[int, int]]:
    """Gaps between consecutive distinct values that are at least min_width wide: (lo, hi) with no value
    strictly between them. Largest first (ties: the lower band first). A cut-off is the middle of one."""
    vs = sorted(set(values))
    bands = [(lo, hi) for lo, hi in zip(vs, vs[1:]) if hi - lo >= min_width]
    return sorted(bands, key=lambda b: (-(b[1] - b[0]), b[0]))


def in_empty_band(value: int, bands: Sequence[Tuple[int, int]]) -> bool:
    return any(lo < value < hi for lo, hi in bands)


def band_of(value: int, bands: Sequence[Tuple[int, int]]) -> Optional[Tuple[int, int]]:
    """The widest empty band that holds value (None when no band does)."""
    return next(((lo, hi) for lo, hi in bands if lo < value < hi), None)


# ── 3. runs ───────────────────────────────────────────────────────────────────

def relaxed(seq: Sequence[Token]) -> List[Token]:
    """A member's tokens with the last step free (kind only): the last step leads out of the member."""
    if not seq:
        return []
    return list(seq[:-1]) + [(seq[-1][0], "*")]


def _same_relaxed(S: Sequence[Token], a: int, b: int, L: int) -> bool:
    return list(S[a:a + L - 1]) == list(S[b:b + L - 1]) and S[a + L - 1][0] == S[b + L - 1][0]


def _primitive(S: Sequence[Token], s: int, L: int) -> bool:
    for d in range(1, L):
        if L % d == 0 and all(list(S[s:s + d]) == list(S[s + j * d:s + (j + 1) * d]) for j in range(1, L // d)):
            return False
    return True


def tandem_runs(S: Sequence[Token]) -> List[Tuple[int, int, int]]:
    """Maximal tandem repeats (start, period L, members k) of S, last step free, L >= MIN_PERIOD,
    k >= 2, period primitive. Sorted by start, then period (deterministic)."""
    n = len(S)
    runs: List[Tuple[int, int, int]] = []
    for L in range(MIN_PERIOD, n // 2 + 1):
        s = 0
        while s + 2 * L <= n:
            if _same_relaxed(S, s, s + L, L):
                k = 2
                while s + (k + 1) * L <= n and _same_relaxed(S, s, s + k * L, L):
                    k += 1
                if _primitive(S, s, L):
                    runs.append((s, L, k))
                s += k * L
            else:
                s += 1
    return sorted(runs)


def edits(period: Sequence[Token], window: Sequence[Token]) -> int:
    """Edit distance in tokens between a period and a window (relaxed: the last step is free)."""
    a, b = relaxed(period), relaxed(window)
    sm = SequenceMatcher(None, a, b, autojunk=False)
    return sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal")


def extend(S: Sequence[Token], s: int, L: int, k: int) -> List[Tuple[int, int]]:
    """Grow an exact run (s, L, k) left and right by windows whose edits to the period are within
    E x L (E = EDITS_PER_TOKEN). Each step takes the window with the fewest edits. Member ranges."""
    period = list(S[s:s + L])
    allow = int(EDITS_PER_TOKEN * L)
    members = [(s + m * L, s + (m + 1) * L) for m in range(k)]
    changed = True
    while changed:
        changed = False
        a = members[0][0]
        best = None
        for ln in range(max(1, L - allow), L + allow + 1):
            if a - ln < 0:
                continue
            e = edits(period, S[a - ln:a])
            if e <= allow and (best is None or e < best[0]):
                best = (e, ln)
        if best:
            members.insert(0, (a - best[1], a))
            changed = True
        b = members[-1][1]
        best = None
        for ln in range(max(1, L - allow), L + allow + 1):
            if b + ln > len(S):
                continue
            e = edits(period, S[b:b + ln])
            if e <= allow and (best is None or e < best[0]):
                best = (e, ln)
        if best:
            members.append((b, b + best[1]))
            changed = True
    return members


def union_area(lv: Sequence[Leaf], a: int, b: int) -> int:
    """Area of the bounding box of leaves a..b-1."""
    boxes = [lv[i].box for i in range(a, b)]
    x0 = min(x for x, _, _, _ in boxes)
    y0 = min(y for _, y, _, _ in boxes)
    x1 = max(x + w for x, _, w, _ in boxes)
    y1 = max(y + h for _, y, _, h in boxes)
    return (x1 - x0) * (y1 - y0)


def _members_from(S: Sequence[Token], start: int, L: int, k: int) -> int:
    """How many consecutive members from `start` are relaxed-equal to the first (at most k). A rotation
    of a run is only a seed where its members really repeat: the last step is free, so a rotation can
    start on a position the run never compared."""
    n = 1
    while n < k and start + (n + 1) * L <= len(S) and _same_relaxed(S, start, start + n * L, L):
        n += 1
    return n


def phase(lv: Sequence[Leaf], S: Sequence[Token], s: int, L: int, k: int) -> Tuple[int, int]:
    """The rotation of the period among those that really repeat: the one with the smallest MEAN member-box
    area (ties: the earliest). Returns (rotation, members that repeat from it).

    The design says "total area"; the mean is used because a total falls when a rotation drops a member
    (on the ITR page the true alignment has 5 members, a wrong one 4, and the total prefers the wrong one).
    Mean area per member is compared as an exact fraction, so ties are exact and deterministic."""
    best = None
    for rot in range(L):
        kk = _members_from(S, s + rot, L, k)
        if kk < 2:
            continue
        tot = sum(union_area(lv, s + rot + m * L, s + rot + (m + 1) * L) for m in range(kk))
        key = (Fraction(tot, kk), rot)
        if best is None or key < best[0]:
            best = (key, rot, kk)
    return (best[1], best[2]) if best else (0, 0)


def label_merge(lv: Sequence[Leaf], members: List[Tuple[int, int]]) -> Tuple[List[Tuple[int, int]], int]:
    """Label witness: the smallest divisor m of the member count at which some position holds the same
    non-empty text in every merged member (m consecutive members). Merged members, m (1 = unchanged)."""
    k = len(members)
    for m in range(1, k // 2 + 1):
        if k % m:
            continue
        merged = [(members[j * m][0], members[(j + 1) * m - 1][1]) for j in range(k // m)]
        P = min(b - a for a, b in merged)
        for p in range(P):
            texts = [lv[a + p].text for a, _ in merged]
            if texts[0] and len(set(texts)) == 1:
                return merged, m
    return members, 1


@dataclass
class Group:
    members: List[Tuple[int, int]]                 # leaf index ranges [a, b), document order
    period: int                                    # the seed's period, in tokens
    area: int                                      # total area of the member boxes
    seed: int = 0                                  # token index where the (rotated) seed period starts
    raw: List[Tuple[int, int]] = field(default_factory=list)   # members as extended, before label merge
    merged: int = 1                                # members merged per member by the label witness
    parent: Optional[Tuple[int, int]] = None       # (group index, member index) this group sits in
    slots: Dict[Any, List[Optional[int]]] = field(default_factory=dict)  # slot -> leaf index per member
    roles: Dict[Any, str] = field(default_factory=dict)
    kind: str = "unknown"


def find_groups(lv: Sequence[Leaf], S: Sequence[Token]) -> List[Group]:
    """The accepted groups of a page, in acceptance order (most leaves first)."""
    cands: List[Group] = []
    for s, L, k in tandem_runs(S):
        rot, kk = phase(lv, S, s, L, k)
        if kk < 2:
            continue
        members = extend(S, s + rot, L, kk)
        area = sum(union_area(lv, a, b) for a, b in members)
        cands.append(Group(members=members, period=L, area=area, seed=s + rot, raw=list(members)))
    cands.sort(key=lambda g: (-sum(b - a for a, b in g.members), g.area, g.members[0][0]))
    accepted: List[Group] = []
    for g in cands:
        lo, hi = g.members[0][0], g.members[-1][1]
        parent: Optional[Tuple[int, int]] = None
        ok = True
        for gi, a in enumerate(accepted):
            alo, ahi = a.members[0][0], a.members[-1][1]
            if hi <= alo or lo >= ahi:
                continue
            inside = [j for j, m in enumerate(a.members) if m[0] <= lo and hi <= m[1]]
            if inside:
                parent = (gi, inside[0])
                continue
            ok = False
            break
        if not ok:
            continue
        g.members, g.merged = label_merge(lv, g.members)
        g.parent = parent
        accepted.append(g)
    return accepted


# ── 4. slots and roles ────────────────────────────────────────────────────────

def _slot_order(k: Any) -> Tuple[int, Any]:
    return (0, k) if isinstance(k, int) else (1, k)


def _align(template: Sequence[Token], member: Sequence[Token]) -> List[Any]:
    """The slot of each member position: a template position where the member matches it, else an own
    slot ('own:<previous template position>.<n>') for a leaf the template does not have (an optional one)."""
    slot: List[Any] = [None] * len(member)
    own_count: Counter = Counter()
    sm = SequenceMatcher(None, list(template), list(member), autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for d in range(j2 - j1):
                slot[j1 + d] = i1 + d
            continue
        n = min(i2 - i1, j2 - j1)
        for d in range(n):
            slot[j1 + d] = i1 + d
        for j in range(j1 + n, j2):
            prev = i1 + n - 1 if n else i1 - 1
            own_count[prev] += 1
            slot[j] = f"own:{prev}.{own_count[prev]}"
    return slot


def fill_slots(lv: Sequence[Leaf], S: Sequence[Token], g: Group) -> None:
    """Slots of a group: every member aligned to the first member (its tokens are the template)."""
    template = relaxed(S[g.members[0][0]:g.members[0][1]])
    per_member: List[Dict[Any, int]] = []
    for a, b in g.members:
        keys = _align(template, relaxed(S[a:b]))
        per_member.append({key: a + j for j, key in enumerate(keys)})
    all_keys = sorted({key for d in per_member for key in d}, key=_slot_order)
    g.slots = {key: [d.get(key) for d in per_member] for key in all_keys}


def role_of(lv: Sequence[Leaf], idx: Sequence[Optional[int]]) -> str:
    here = [lv[i] for i in idx if i is not None]
    if any(l.ticking for l in here):
        return "ticking"
    texts = [l.text for l in here]
    if not any(texts):
        return "look"
    if len(texts) == 1:
        return "single"
    if all(texts[0] and t == texts[0] for t in texts):
        return "label"
    if len(set(texts)) == len(texts):
        return "distinct"
    return "mixed"


def fill_roles(lv: Sequence[Leaf], g: Group) -> None:
    g.roles = {key: role_of(lv, idx) for key, idx in g.slots.items()}


# ── 5. kinds ──────────────────────────────────────────────────────────────────

def recurrence(captures: Sequence[Tuple[str, int, Dict[Any, List[str]]]], slot: Any) -> Optional[float]:
    """Corpus R of one slot over the captures of one page link. Each capture: (client, member count,
    {slot: texts}). R = 1 - (distinct texts - members of the largest capture) / (occurrences - members of
    the largest capture). None when fewer than two clients have captured the link (same-client captures
    do not make names), or when there is nothing to recur (one capture)."""
    if len({c for c, _, _ in captures}) < 2:
        return None
    largest = max(n for _, n, _ in captures)
    texts = [t for _, _, d in captures for t in d.get(slot, [])]
    occ, distinct = len(texts), len(set(texts))
    if occ - largest <= 0:
        return None
    return max(0.0, min(1.0, 1 - (distinct - largest) / (occ - largest)))


def name_slot(g: Group, R: Dict[Any, Optional[float]], cut: Optional[float] = NAME_CUT) -> Optional[Any]:
    """The earliest slot that is distinct on this page, never ticking, and whose corpus R is at or above
    the cut-off. None while the cut-off is not set (then no name is decided)."""
    if cut is None:
        return None
    for key in sorted(g.roles, key=_slot_order):
        r = R.get(key)
        if g.roles[key] == "distinct" and r is not None and r >= cut:
            return key
    return None


def _kind_from_counts(counts: Sequence[int]) -> str:
    if len(counts) < 2:
        return "unknown"
    return "list" if len(set(counts)) > 1 else "fields"


def occurrence_counts(groups: Sequence[Group], gi: int) -> List[int]:
    """Member counts of this group's occurrences: one per member of its parent group (0 when a parent
    member holds no such group). Top-level groups have no occurrences on one page."""
    g = groups[gi]
    if g.parent is None:
        return []
    pi, _ = g.parent
    parent = groups[pi]
    counts = []
    for j in range(len(parent.members)):
        same = [h for h in groups if h.parent == (pi, j) and h.period == g.period]
        counts.append(len(same[0].members) if same else 0)
    return counts


def assign_kinds(groups: Sequence[Group], R_by_group: Optional[Dict[int, Dict[Any, Optional[float]]]] = None,
                 cut: Optional[float] = NAME_CUT) -> None:
    """Design 3.5: cards when a name slot is known; else list when the count varies; else fields."""
    for gi, g in enumerate(groups):
        R = (R_by_group or {}).get(gi, {})
        g.kind = "cards" if name_slot(g, R, cut) is not None else _kind_from_counts(occurrence_counts(groups, gi))


# ── the whole page ────────────────────────────────────────────────────────────

@dataclass
class Arrangement:
    leaves: List[Leaf]
    tokens: List[Token]
    groups: List[Group]


def arrange(fl: List[Dict[str, Any]]) -> Arrangement:
    """The whole arrangement of one page (a comp node list or a read's flatten list)."""
    lv = leaves(fl)
    S = tokens(lv)
    groups = find_groups(lv, S)
    for g in groups:
        fill_slots(lv, S, g)
        fill_roles(lv, g)
    assign_kinds(groups)
    return Arrangement(lv, S, groups)


# ── CLI: counts only ──────────────────────────────────────────────────────────

def _load(path: Path) -> Optional[List[Dict[str, Any]]]:
    """The flat node list of a comp page or a read; None (with a note) for any other JSON, such as a
    capture log of snapshots, which has no node list to arrange."""
    import cards   # the reader for both comp pages and reads (same as cards.py)
    rec = json.loads(path.read_text(encoding="utf-8"))
    if not (rec.get("comp_page") and rec.get("nodes")) and not rec.get("docs"):
        print(f"{path.name[:60]}: skipped (not a comp page or a read)")
        return None
    fl, _ = cards.load_flat(rec)
    return fl


def _loaded(paths: List[Path]) -> List[Tuple[Path, List[Dict[str, Any]]]]:
    out = []
    for p in paths:
        fl = _load(p)
        if fl is not None:
            out.append((p, fl))
    return out


def _default_files() -> List[Path]:
    return [Path(p) for p in sorted(glob.glob(str(OUT_DIR / "comp_*.json")))]


def _expand(args: List[str]) -> List[Path]:
    out: List[Path] = []
    for a in args:
        out += [Path(p) for p in sorted(glob.glob(a))] if any(c in a for c in "*?[") else [Path(a)]
    return out


def steps_report(paths: List[Path]) -> int:
    """Counts only: the overlap and dx histograms, the empty bands, and whether OVL and TOLX lie in one."""
    all_ov: List[int] = []
    all_dx: List[int] = []
    for p, fl in _loaded(paths):
        lv = leaves(fl)
        ov, dx = step_samples(lv)
        all_ov += ov
        all_dx += dx
        print(f"{p.name[:60]}: leaves={len(lv)} consecutive pairs={len(ov)} other-line pairs={len(dx)}")
    ov_bands = empty_bands(all_ov)
    dx_bands = empty_bands(all_dx)
    print("overlap (px) -> pairs:", dict(sorted(Counter(all_ov).items())))
    print("dx of other-line pairs (px) -> pairs:", dict(sorted(Counter(all_dx).items())))
    # OVL separates "no overlap" (0) from "overlap": the band is the one that starts at 0 (design 5)
    separating = (0, min((v for v in set(all_ov) if v > 0), default=0))
    print(f"OVL={OVL} lies in the separating band {separating} between no overlap and overlap: "
          f"{separating[0] < OVL < separating[1]}")
    print(f"TOLX={TOLX} lies in the empty dx band {band_of(TOLX, dx_bands)}")
    ok = separating[0] < OVL < separating[1] and in_empty_band(TOLX, dx_bands)
    if not ok:
        print("REFUSE: a constant is not in an empty band on these captures; the steps do not run with it.")
    return 0 if ok else 2


def likeness_report(paths: List[Path]) -> int:
    """Counts only: edits per token of each group's members (as extension extended them, before label merge)
    against the seed period, and of the nearest window beside the first and the last member, lengths
    L-3..L+3 (the candidates extension tests). E must lie in the gap between the two."""
    true_e: List[float] = []
    cand_e: List[float] = []
    for p, fl in _loaded(paths):
        arr = arrange(fl)
        S = arr.tokens
        for g in arr.groups:
            L = g.period
            period = S[g.seed:g.seed + L]
            true_e += [edits(period, S[a:b]) / L for a, b in g.raw]
            first, last = g.raw[0][0], g.raw[-1][1]
            for side in ("before", "after"):
                best = None
                for ln in range(max(1, L - 3), L + 4):
                    if side == "before" and first - ln >= 0:
                        w = S[first - ln:first]
                    elif side == "after" and last + ln <= len(S):
                        w = S[last:last + ln]
                    else:
                        continue
                    v = edits(period, w) / L
                    best = v if best is None else min(best, v)
                if best is not None:
                    cand_e.append(best)
        print(f"{p.name[:60]}: groups={len(arr.groups)} members={sum(len(g.raw) for g in arr.groups)}")
    hi_true = max(true_e) if true_e else 0.0
    lo_cand = min(cand_e) if cand_e else 1.0
    print("true members, edits per token (3 d.p.) -> count:", dict(sorted(Counter(round(v, 3) for v in true_e).items())))
    print("nearest candidate beside each group, edits per token (3 d.p.) -> count:",
          dict(sorted(Counter(round(v, 3) for v in cand_e).items())))
    ok = hi_true < EDITS_PER_TOKEN < lo_cand
    print(f"gap: ({hi_true:.3f}, {lo_cand:.3f}); EDITS_PER_TOKEN={EDITS_PER_TOKEN} inside: {ok}")
    if not ok:
        print("REFUSE: EDITS_PER_TOKEN is not in the measured gap; extension does not run with it.")
    return 0 if ok else 2


def families(arr: Arrangement) -> Dict[Tuple[int, int], Group]:
    """One group per (period, ordinal): the ordinal counts the groups of that period in page order. A
    group is matched across captures by this key, which is the position on the page, not its texts."""
    out: Dict[Tuple[int, int], Group] = {}
    seen: Counter = Counter()
    for g in sorted(arr.groups, key=lambda h: (h.period, h.members[0][0])):
        out[(g.period, seen[g.period])] = g
        seen[g.period] += 1
    return out


def recurrence_report(paths: List[Path]) -> int:
    """Counts only: corpus R per slot per family (a group matched across the captures of one page link by
    period and position). Names stay undecided while NAME_CUT is not set; R is printed for the measurement."""
    import cards
    by_link: Dict[str, List[Tuple[str, Arrangement]]] = defaultdict(list)
    for p in paths:
        rec = json.loads(p.read_text(encoding="utf-8"))
        if not (rec.get("comp_page") and rec.get("nodes")) and not rec.get("docs"):
            print(f"{p.name[:60]}: skipped (not a comp page or a read)")
            continue
        fl, _ = cards.load_flat(rec)
        client = cards.client_of(rec, fl, p.stem.split("__")[0])
        by_link[rec.get("page") or p.name].append((client, arrange(fl)))
    all_r: List[float] = []
    for link, caps in by_link.items():
        clients = {c for c, _ in caps}
        print(f"link {link[:70]!r}: captures={len(caps)} clients={len(clients)}")
        if len(clients) < 2:
            print("  one client only: no recurrence (same-client captures do not make names)")
            continue
        fams = [families(arr) for _, arr in caps]
        for key in sorted({k for f in fams for k in f}):
            family = []
            for (client, arr), f in zip(caps, fams):
                if key in f:
                    g = f[key]
                    d = {k: [arr.leaves[i].text for i in v if i is not None] for k, v in g.slots.items()}
                    family.append((client, len(g.members), d))
            if len(family) < 2:
                continue
            slots = sorted({k for _, _, d in family for k in d}, key=_slot_order)
            print(f"  family period={key[0]} ordinal={key[1]} captures={len(family)} "
                  f"members={[n for _, n, _ in family]}")
            for slot in slots:
                r = recurrence(family, slot)
                if r is not None:
                    all_r.append(r)
                print(f"    slot {str(slot)[:20]!r}: R={'-' if r is None else f'{r:.2f}'}")
    print(f"R values: {sorted(round(v, 2) for v in all_r)}; NAME_CUT={NAME_CUT} (not set until >=3 captures per link)")
    return 0


def shape_report(paths: List[Path]) -> int:
    """Counts only: per page, the leaves and each group's members, lengths, nesting, boxes and roles."""
    for p, fl in _loaded(paths):
        arr = arrange(fl)
        print(f"{p.name[:60]}: leaves={len(arr.leaves)} distinct tokens={len(set(arr.tokens))} groups={len(arr.groups)}")
        for gi, g in enumerate(arr.groups):
            lengths = [b - a for a, b in g.members]
            print(f"  group {gi}: members={len(g.members)} lengths={min(lengths)}-{max(lengths)} period={g.period} "
                  f"merged x{g.merged} nested={'-' if g.parent is None else f'in {g.parent[0]}/{g.parent[1]}'} "
                  f"area={g.area} kind={g.kind} roles={dict(sorted(Counter(g.roles.values()).items()))}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Arrangement of a page (design: docs/sdis_experiments/cards-solution.md).")
    ap.add_argument("files", nargs="*", help="comp pages or reads (.json, globs ok); default: the comp and capture files in output/")
    ap.add_argument("--steps", action="store_true", help="overlap and dx histograms; checks OVL and TOLX against the empty bands")
    ap.add_argument("--likeness", action="store_true", help="edits per token of members and nearest windows; checks EDITS_PER_TOKEN")
    ap.add_argument("--recurrence", action="store_true", help="corpus R per slot per page link (names need NAME_CUT)")
    args = ap.parse_args(argv)
    paths = _expand(args.files) if args.files else _default_files()
    if not paths:
        print("no files")
        return 2
    if args.steps:
        return steps_report(paths)
    if args.likeness:
        return likeness_report(paths)
    if args.recurrence:
        return recurrence_report(paths)
    return shape_report(paths)


if __name__ == "__main__":
    raise SystemExit(main())
