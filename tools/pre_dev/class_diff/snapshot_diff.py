"""
tools/pre_dev/class_diff/snapshot_diff.py - PRE-DEV TEST: one client, one page, before vs after
================================================================================================
Tests whether node keys (keys.py) stay put while the page is used and change only when the
screen really changes. Take a key_probe.py read, do something on the page (type a value, open a
popup, submit), take another read, then:

    python tools/pre_dev/class_diff/snapshot_diff.py                      # the two newest reads
    python tools/pre_dev/class_diff/snapshot_diff.py --latest A.json --previous B.json

Every node is matched by its key, and every difference is one of:

    text changed   same key, different text           -> a value changed (typed, picked)
    class flip     keys differ only in class names    -> the same element changed state
                   (loose keys match - keys.py)          (focused, dirty...): not a new element
    added          key only in the latest read        -> something appeared
    removed        key only in the previous read      -> something went away

Added / removed nodes are grouped into BLOCKS: a block is an added node whose parent was not added,
plus everything under it (a popup, a message line, a new card).

An added block is NEW when it holds a key this client's map of this page link (link_map.py: all
earlier reads of the same client and link) never had, and RETURNED when every key was seen
before - the page behind a popup that just closed. Reads taken without --client share one
unnamed map per link.

Verdict (what SGT would do with this poll):
    NEW STATE      a NEW block holding text appeared         -> take a new snapshot
    RETURNED       only RETURNED blocks came back            -> an earlier state again: no snapshot
    PART CLOSED    blocks only went away                     -> a part closed: no snapshot
    SAME SCREEN    only text changes / class flips           -> update this screen's values

Class tokens seen flipping on one element that keys.py did not yet treat as state are LEARNED
(output/learned_state.json): from the next run on, keys leave them out.

Writes output/snapdiff_<latest read's timestamp>.csv with every changed node (it holds real page
values - client data - and stays in the git-ignored output folder on this PC).

COMP PAGE (experiment 1, 2026-10-07): every snapshot of one capture of one page link merged into
ONE complete page (AnchorPage). key_probe.py hands each kept snapshot to it while capturing:

    anchor page   the FIRST snapshot's nodes, in page order, fixed as the anchor page
    each later    compared with the snapshot before it (alignment: shape + text, align.py):
    snapshot        paired      the same node - its text is updated, its place stays
                    disappeared it leaves the anchor page (the live page)
                    returned    a node that had left comes back: attached again at the END
                    new         never seen: attached at the END of the anchor page
    comp page     when the capture stops: every node that was ever on the anchor page, once, in
                  the order it FIRST appeared (anchor nodes, then each batch of new ones), its
                  latest text, every value seen, and whether it was still there at the end.

compare.py --comp compares one client's comp page with another client's of the same link.
Not handled yet: variable list rows / cards (a separate mechanism will come).

    python tools/pre_dev/class_diff/snapshot_diff.py --comp               # every capture in output/
    python tools/pre_dev/class_diff/snapshot_diff.py --comp capture_X.json

Writes output/comp_<session>_<n>__<page>.json (for compare.py), .csv (one row per node) and .txt
(the snapshot log: what was attached, left, came back).
"""

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "output"
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from compare import value_type, write_csv                     # noqa: E402
from keys import LEARNED_FILE, flatten, is_state_token, learn, page_slug   # noqa: E402
import link_map                                                  # noqa: E402
from align import align, pair_moved, shape                     # noqa: E402

TEXT_CHANGED, CLASS_FLIP, ADDED, REMOVED = "text changed", "class flip", "added", "removed"
NEW_STATE, RETURNED, PART_CLOSED, SAME_SCREEN = "NEW STATE", "RETURNED", "PART CLOSED", "SAME SCREEN"
BLOCK_TEXTS = 3


def _raw_tokens(e: Dict[str, Any]) -> Set[str]:
    return set((e["node"].get("cls") or "").split())


def _blocks(flat: List[Dict[str, Any]], keys: Set[str]) -> List[Dict[str, Any]]:
    """Group nodes whose key is in `keys` into blocks: a root (its parent's key is not in `keys`)
    and everything under it."""
    blocks: List[Dict[str, Any]] = []
    by_root: Dict[int, Dict[str, Any]] = {}
    root_of: Dict[int, int] = {}
    for i, e in enumerate(flat):
        if e["key"] not in keys:
            continue
        p = e["parent"]
        if p >= 0 and p in root_of:
            r = root_of[p]
        else:
            r = i
            by_root[r] = {"root": e["key"], "nodes": 0, "texts": [], "keys": []}
            blocks.append(by_root[r])
        root_of[i] = r
        b = by_root[r]
        b["nodes"] += 1
        b["keys"].append(e["key"])
        if e["text"]:
            b["texts"].append(e["text"])
    return blocks


def diff(latest: Dict[str, Any], previous: Dict[str, Any], known: Optional[Set[str]] = None) -> Dict[str, Any]:
    """`known`: every key this client's earlier reads of this link showed (None = nothing known,
    every added block is new)."""
    fl, fp = flatten(latest), flatten(previous)
    lk = {e["key"]: e for e in fl}
    pk = {e["key"]: e for e in fp}
    rows: List[Dict[str, Any]] = []
    same = 0
    for k, e in lk.items():
        o = pk.get(k)
        if o is None:
            continue
        if e["text"] != o["text"]:
            rows.append({"change": TEXT_CHANGED, "key": k, "element": e["type"], "previous_text": o["text"],
                         "latest_text": e["text"], "value_type": value_type(e["text"]) if e["text"] else ""})
        elif e["text"]:
            same += 1

    added = set(lk) - set(pk)
    removed = set(pk) - set(lk)
    # Class flips: an added and a removed key with the same loose key are one element.
    l_loose = {lk[k]["loose"]: k for k in added}
    p_loose = {pk[k]["loose"]: k for k in removed}
    flipped_tokens: Counter = Counter()
    for loose in set(l_loose) & set(p_loose):
        a, r = l_loose[loose], p_loose[loose]
        e, o = lk[a], pk[r]
        added.discard(a)
        removed.discard(r)
        for t in _raw_tokens(e) ^ _raw_tokens(o):
            flipped_tokens[t] += 1
        rows.append({"change": CLASS_FLIP, "key": a, "element": e["type"], "previous_text": o["text"],
                     "latest_text": e["text"], "value_type": "",
                     "classes": f"was: {o['node'].get('cls', '')} | now: {e['node'].get('cls', '')}"})
        if e["text"] != o["text"]:
            rows.append({"change": TEXT_CHANGED, "key": a, "element": e["type"], "previous_text": o["text"],
                         "latest_text": e["text"], "value_type": value_type(e["text"]) if e["text"] else ""})

    added_blocks = _blocks(fl, added)
    removed_blocks = _blocks(fp, removed)
    for b in added_blocks:
        b["new"] = known is None or any(k not in known for k in b["keys"])
    new_keys = {k for b in added_blocks if b["new"] for k in b["keys"]}
    for flat, keys, change in ((fl, added, ADDED), (fp, removed, REMOVED)):
        for e in flat:
            if e["key"] in keys and e["text"]:
                label = change if change == REMOVED else f"{ADDED} ({'new' if e['key'] in new_keys else 'returned'})"
                rows.append({"change": label, "key": e["key"], "element": e["type"],
                             "previous_text": e["text"] if change == REMOVED else "",
                             "latest_text": e["text"] if change == ADDED else "",
                             "value_type": value_type(e["text"])})

    if any(b["texts"] for b in added_blocks if b["new"]):
        verdict = NEW_STATE
    elif any(b["texts"] for b in added_blocks):
        verdict = RETURNED
    elif any(b["texts"] for b in removed_blocks):
        verdict = PART_CLOSED
    else:
        verdict = SAME_SCREEN
    unknown = {t: k for t, k in flipped_tokens.items() if not is_state_token(t) and not any(c.isdigit() for c in t)}
    return {"rows": rows, "same": same, "verdict": verdict, "added_blocks": added_blocks,
            "removed_blocks": removed_blocks, "flipped_tokens": flipped_tokens, "unknown_state_tokens": unknown,
            "nodes": (len(fp), len(fl))}


ANCHOR, ATTACHED = "anchor", "attached"


def _everything(e: Dict[str, Any]) -> bool:
    return True


class AnchorPage:
    """One capture's snapshots of one page link merged into one complete page (module doc,
    COMP PAGE). Entries are numbered in the order they first appeared - the comp page's order."""

    def __init__(self, page: str, session: str = "", browser: str = "", anchor: str = "settled") -> None:
        self.page, self.session, self.browser = page, session, browser
        # "settled": the anchor page stays open while the page is still loading - every snapshot joins
        # it, in page order - and is fixed once the page was QUIET (at least one poll with no change,
        # or a snapshot that adds nothing) while it already showed text. A loading shell has no text.
        # "first": the first snapshot alone is the anchor page.
        self.anchor_mode = anchor
        self.settled = False
        self.settled_at = ""
        self.anchor_order: List[int] = []
        self.entries: List[Dict[str, Any]] = []
        self.live: List[int] = []                 # the anchor page now: anchor order, then attached
        self.prev: List[Dict[str, Any]] = []      # the previous snapshot (flat) ...
        self.prev_ids: List[int] = []             # ... and the entry of each of its nodes
        self.log: List[Dict[str, Any]] = []       # one row per snapshot

    def _returned(self, flat: List[Dict[str, Any]], ids: List[Optional[int]]) -> None:
        """Nodes the previous snapshot did not have, paired with entries that LEFT the anchor
        page: same key + text, else a unique shape + text, else the same key (a value that
        changed while it was hidden)."""
        live = set(self.live)
        gone = [k for k in range(len(self.entries)) if k not in live]
        rest = [i for i, x in enumerate(ids) if x is None]
        if not gone or not rest:
            return
        taken: set = set()
        tests = (lambda e: (e["key"], e["text"]), lambda e: (shape(e), e["text"]), lambda e: e["key"])
        for n, test in enumerate(tests):
            pool: Dict[Any, List[int]] = {}
            for k in gone:
                if k not in taken:
                    pool.setdefault(test(self.entries[k]), []).append(k)
            want: Dict[Any, List[int]] = {}
            for i in rest:
                if ids[i] is None:
                    want.setdefault(test(flat[i]), []).append(i)
            for f, idx in want.items():
                cand = pool.get(f) or []
                if n == 1 and (len(idx) != 1 or len(cand) != 1):   # by face only when unique
                    continue
                for i, k in zip(idx, cand):
                    ids[i] = k
                    taken.add(k)

    @staticmethod
    def _rescue(flat: List[Dict[str, Any]], prev: List[Dict[str, Any]], pairs: Dict[int, int]) -> int:
        """Re-rendered nodes alignment missed (a class or a wrapper changed, so the shape did):
        pair what is left on both sides by
          1. the same loose key (element types only) + the same text, when unique on both sides;
          2. the same element type + the same non-empty text, when unique on both sides;
          3. bottom-up: a node whose paired children all came from ONE unpaired previous node of
             the same element type is that node.
        Returns how many pairs it added."""
        added = 0
        for test in (lambda e: (e["loose"], e["text"]), lambda e: (e["type"], e["text"]) if e["text"] else None):
            used = set(pairs.values())
            a: Dict[Any, List[int]] = {}
            b: Dict[Any, List[int]] = {}
            for i, e in enumerate(flat):
                if i not in pairs and test(e) is not None:
                    a.setdefault(test(e), []).append(i)
            for j, e in enumerate(prev):
                if j not in used and test(e) is not None:
                    b.setdefault(test(e), []).append(j)
            for f, ia in a.items():
                jb = b.get(f)
                if len(ia) == 1 and jb and len(jb) == 1:
                    pairs[ia[0]] = jb[0]
                    added += 1
        kids: Dict[int, List[int]] = {}
        for i, e in enumerate(flat):
            if e["parent"] >= 0:
                kids.setdefault(e["parent"], []).append(i)
        used = set(pairs.values())
        for i in range(len(flat) - 1, -1, -1):             # children come after their parent
            if i in pairs or i not in kids:
                continue
            ps = {prev[pairs[c]]["parent"] for c in kids[i] if c in pairs}
            if len(ps) == 1 and all(c in pairs for c in kids[i]):
                j = ps.pop()
                if j >= 0 and j not in used and prev[j]["type"] == flat[i]["type"]:
                    pairs[i] = j
                    used.add(j)
                    added += 1
        return added

    def add(self, rec: Dict[str, Any], stamp: str, quiet_before: bool = False) -> Dict[str, Any]:
        """Merge one snapshot. quiet_before: at least one poll since the previous snapshot saw no
        change (the page sat still). Returns this snapshot's log row: counts plus the texts attached,
        left and returned."""
        if not self.settled and quiet_before and any(self.entries[k].get("text") for k in self.live):
            self.settled, self.settled_at = True, self.log[-1]["snapshot"] if self.log else stamp
        flat = flatten(rec)
        ids: List[Optional[int]] = [None] * len(flat)
        rescued = 0
        if self.prev:
            pairs = pair_moved(flat, self.prev, align(flat, self.prev, _everything), _everything)
            rescued = self._rescue(flat, self.prev, pairs)
            for i, j in pairs.items():
                ids[i] = self.prev_ids[j]
        paired = {x for x in ids if x is not None}
        self._returned(flat, ids)
        returned = [i for i, x in enumerate(ids) if x is not None and x not in paired]
        open_ = not self.settled
        first = not self.entries
        new = []
        for i, e in enumerate(flat):
            if ids[i] is None:
                ids[i] = len(self.entries)
                self.entries.append({"first": stamp, "origin": ANCHOR if open_ else ATTACHED,
                                     "batch": len(self.log), "values": [], "left": 0, "came_back": 0,
                                     "parent0": ids[e["parent"]] if e["parent"] >= 0 else -1})
                new.append(i)
        now = set(ids)
        left = [k for k in self.live if k not in now]
        for k in left:
            self.entries[k]["left"] += 1
        for i in returned:
            self.entries[ids[i]]["came_back"] += 1
        if open_:
            # Still loading: the anchor page is this snapshot, in its page order; anchor nodes that
            # left meanwhile (a spinner) go after it.
            self.live = list(ids)
            self.anchor_order = list(ids) + [k for k in self.anchor_order if k not in now]
            for i, e in enumerate(flat):
                self.entries[ids[i]]["parent0"] = ids[e["parent"]] if e["parent"] >= 0 else -1
            if self.anchor_mode == "first" or (not first and not new):
                self.settled, self.settled_at = True, stamp
        else:
            # Returned and new nodes go to the END of the anchor page, in this snapshot's page order.
            self.live = [k for k in self.live if k in now] + [ids[i] for i in sorted(returned + new)]
        changed = 0
        for i, e in enumerate(flat):
            ent = self.entries[ids[i]]
            if ent.get("text", e["text"]) != e["text"]:
                changed += 1
            ent.update(key=e["key"], node=e["node"], cls=e["cls"], type=e["type"], text=e["text"],
                       loose=e["loose"], last=stamp)
            if e["text"] and e["text"] not in ent["values"] and len(ent["values"]) < link_map.MAX_VALUES:
                ent["values"].append(e["text"])
        self.prev, self.prev_ids = flat, ids

        def texts(idx):
            return [flat[i]["text"] for i in idx if flat[i]["text"]]
        row = {"snapshot": stamp, "phase": "anchor" if open_ else "attach", "nodes": len(flat),
               "paired": len(paired), "rescued": rescued, "attached": len(new), "left": len(left),
               "returned": len(returned), "text_changed": changed,
               "attached_texts": [] if first else texts(new), "returned_texts": texts(returned),
               "left_texts": [self.entries[k]["text"] for k in left if self.entries[k]["text"]]}
        self.log.append(row)
        return row

    def comp_flat(self) -> List[Dict[str, Any]]:
        """The comp page in keys.flatten()'s shape (key, parent index, depth, cls, type, text, node)
        plus values, first, last, origin, present (still on the page at the end), left, came_back.
        An attached node keeps its real parent, which may sit earlier (in the anchor part)."""
        live = set(self.live)
        order = self.anchor_order + [k for k in range(len(self.entries)) if self.entries[k]["origin"] == ATTACHED]
        pos = {k: n for n, k in enumerate(order)}
        depth: Dict[int, int] = {}

        def d(k: int, guard: int = 0) -> int:
            if k not in depth:
                p = self.entries[k]["parent0"]
                depth[k] = 0 if p < 0 or guard > 200 else d(p, guard + 1) + 1
            return depth[k]
        out = []
        for n, k in enumerate(order):
            ent = self.entries[k]
            p = ent["parent0"]
            out.append({"id": n, "key": ent["key"], "loose": ent["loose"], "parent": pos[p] if p >= 0 else -1,
                        "depth": d(k), "cls": ent["cls"],
                        "type": ent["type"], "text": ent["text"], "node": ent["node"], "values": ent["values"],
                        "first": ent["first"], "last": ent["last"], "origin": ent["origin"],
                        "batch": ent["batch"], "present": k in live, "left": ent["left"],
                        "came_back": ent["came_back"]})
        return out


def comp_record(ap: AnchorPage) -> Dict[str, Any]:
    return {"comp_page": True, "session": ap.session, "page": ap.page, "browser": ap.browser,
            "anchor": ap.anchor_mode, "settled_at": ap.settled_at,
            "snapshots": len(ap.log), "nodes": ap.comp_flat(), "log": ap.log}


COMP_FIELDS = ["order", "origin", "batch", "first", "last", "present", "left", "came_back", "element", "value_type",
               "text", "values_seen", "key"]


def write_comp(ap: AnchorPage, base: Path) -> Dict[str, Path]:
    """base = output/comp_<session>_<n>__<page> (no extension). Writes .json, .csv, .txt."""
    rec = comp_record(ap)
    base.parent.mkdir(parents=True, exist_ok=True)
    js = base.with_name(base.name + ".json")
    js.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    rows = [{"order": e["id"], "origin": e["origin"], "batch": e["batch"], "first": e["first"], "last": e["last"],
             "present": "yes" if e["present"] else "no", "left": e["left"], "came_back": e["came_back"],
             "element": e["type"], "value_type": value_type(e["text"]) if e["text"] else "",
             "text": e["text"], "values_seen": " || ".join(e["values"]), "key": e["key"]}
            for e in rec["nodes"] if e["text"]]
    csv_path = write_csv(base.with_name(base.name + ".csv"), COMP_FIELDS, rows)
    nodes = rec["nodes"]
    lines = [f"Page link  : {ap.page}", f"Session    : {ap.session}   browser: {ap.browser or '?'}",
             f"Snapshots  : {len(ap.log)}   anchor page: {ap.anchor_mode}, fixed at {ap.settled_at or '(never - still loading)'}",
             f"Comp page  : {len(nodes)} nodes, {sum(1 for e in nodes if e['text'])} with text   "
             f"anchor {sum(1 for e in nodes if e['origin'] == ANCHOR)}, "
             f"attached {sum(1 for e in nodes if e['origin'] == ATTACHED)}, "
             f"gone by the end {sum(1 for e in nodes if not e['present'])}", "=" * 100,
             f"{'snapshot':>9s} {'phase':6s} {'nodes':>6s} {'paired':>6s} {'rescue':>6s} {'new':>5s} {'left':>5s} {'back':>5s} {'text~':>5s}"]
    for r in ap.log:
        lines.append(f"{r['snapshot']:>9s} {r['phase']:6s} {r['nodes']:6d} {r['paired']:6d} {r['rescued']:6d} "
                     f"{r['attached']:5d} {r['left']:5d} {r['returned']:5d} {r['text_changed']:5d}")
        for tag, key in (("+ attached", "attached_texts"), ("- left    ", "left_texts"), ("< back    ", "returned_texts")):
            if r[key]:
                lines.append(f"{'':10s}{tag}  " + " | ".join(t[:30] for t in r[key][:6])
                             + (f"  (+{len(r[key]) - 6} more)" if len(r[key]) > 6 else ""))
    txt = base.with_name(base.name + ".txt")
    txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": js, "csv": csv_path, "txt": txt}


def comp_from_capture(path: Path, anchor: str = "settled") -> Tuple[AnchorPage, str]:
    """Rebuild a comp page from a saved capture_*.json (its kept snapshots, in order)."""
    rec = json.loads(path.read_text(encoding="utf-8"))
    ap = AnchorPage(rec.get("page") or "", rec.get("session") or "", rec.get("browser") or "", anchor)
    gap = 1.5 * float(rec.get("interval") or 1.0)       # more than one interval apart = a quiet poll between
    last = None
    for s in rec.get("snapshots") or []:
        ap.add({"docs": s["docs"]}, f"+{s['t']:.1f}s", quiet_before=last is not None and s["t"] - last >= gap)
        last = s["t"]
    return ap, path.stem.replace("capture_", "comp_", 1)


def main_comp(files: List[str], anchor: str = "settled") -> int:
    paths = [Path(f) for f in files] or sorted(OUT_DIR.glob("capture_*.json"))
    paths = [p for p in paths if p.exists()]
    if not paths:
        raise SystemExit(f"No capture_*.json in {OUT_DIR} - run key_probe.py first.")
    for p in paths:
        if not json.loads(p.read_text(encoding="utf-8")).get("capture"):
            continue
        ap, stem = comp_from_capture(p, anchor)
        out = write_comp(ap, OUT_DIR / stem)
        nodes = ap.comp_flat()
        print(f"{p.name}")
        print(f"  {ap.page[-70:]}")
        print(f"  {len(ap.log)} snapshots -> {len(nodes)} nodes "
              f"(anchor {sum(1 for e in nodes if e['origin'] == ANCHOR)}, "
              f"attached {sum(1 for e in nodes if e['origin'] == ATTACHED)}, "
              f"gone by the end {sum(1 for e in nodes if not e['present'])}; anchor fixed at "
              f"{ap.settled_at or 'never'})   -> {out['json'].name}")
    print("(the comp files hold real page values - keep them on this PC)")
    return 0


def pick(latest: Optional[str], previous: Optional[str]) -> Tuple[Path, Path]:
    reads = sorted(OUT_DIR.glob("key_probe_*.json"))
    if latest and previous:
        return Path(latest), Path(previous)
    if len(reads) < 2 and not (latest or previous):
        raise SystemExit(f"Need two reads in {OUT_DIR} - run key_probe.py, change the page, run it again.")
    lp = Path(latest) if latest else reads[-1]
    pp = Path(previous) if previous else max(p for p in reads if p.name < lp.name)
    return lp, pp


FIELDS = ["change", "key", "element", "value_type", "previous_text", "latest_text", "classes"]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="One client, one page: what changed between two reads, by node key.")
    ap.add_argument("--latest", help="a key_probe_*.json (default: the newest in output/)")
    ap.add_argument("--previous", help="a key_probe_*.json (default: the read just before --latest)")
    ap.add_argument("--comp", nargs="*", metavar="CAPTURE",
                    help="build the comp page of these capture_*.json (none given: every capture in output/)")
    ap.add_argument("--anchor", choices=("settled", "first"), default="settled",
                    help="with --comp: settled (default) = the anchor page grows while the page loads and is fixed "
                         "at the first snapshot that adds nothing; first = the first snapshot alone")
    args = ap.parse_args(argv)
    if args.comp is not None:
        return main_comp(args.comp, args.anchor)

    lp, pp = pick(args.latest, args.previous)
    latest = json.loads(lp.read_text(encoding="utf-8"))
    previous = json.loads(pp.read_text(encoding="utf-8"))
    # What this client's earlier reads of this link already showed (the link map).
    same = [(p, rec) for p, rec in link_map.all_reads()
            if p.name < lp.name and link_map.page_of(rec) == link_map.page_of(latest)
            and (rec.get("client") or "") == (latest.get("client") or "")]
    known: Optional[Set[str]] = None
    if same:
        m = link_map.LinkMap(latest.get("client") or "", link_map.page_of(latest))
        for p, rec in same:
            m.add(rec, link_map.read_stamp(p))
        known = m.known_keys()
    r = diff(latest, previous, known)
    out = write_csv(OUT_DIR / (f"snapdiff_{link_map.read_stamp(lp)}__{page_slug(link_map.page_of(latest))}.csv"),
                    FIELDS, r["rows"])

    counts = Counter(x["change"] for x in r["rows"])
    print(f"Previous : {pp.name}  {previous.get('page') or previous.get('title', '')[:50]}")
    print(f"Latest   : {lp.name}  {latest.get('page') or latest.get('title', '')[:50]}")
    if (previous.get("page") or "") != (latest.get("page") or ""):
        print("           (the page link differs between the two reads)")
    print(f"Nodes    : {r['nodes'][0]} -> {r['nodes'][1]}   texts unchanged: {r['same']}")
    counts[ADDED] = sum(v for k, v in counts.items() if k.startswith(ADDED))
    for c in (TEXT_CHANGED, CLASS_FLIP, ADDED, REMOVED):
        print(f"  {c:13s} {counts.get(c, 0):5d}")
    print(f"Known    : {len(known) if known is not None else 0} keys from {len(same)} earlier read(s) "
          f"of this client + link")
    for title, blocks in (("Blocks added", r["added_blocks"]), ("Blocks removed", r["removed_blocks"])):
        if blocks:
            print(f"{title}:")
            for b in blocks[:8]:
                shown = " | ".join(t[:30] for t in b["texts"][:BLOCK_TEXTS]) or "(no text)"
                tag = ("NEW      " if b.get("new") else "returned ") if "new" in b else ""
                print(f"  {tag}{b['nodes']:3d} nodes  {b['root'][-60:]}")
                print(f"             {shown}")
            if len(blocks) > 8:
                print(f"  ... {len(blocks) - 8} more")
    new = learn(r["unknown_state_tokens"])
    if new:
        print(f"Learned state class names (seen flipping; left out of keys from the next run): {', '.join(new)}")
        print(f"           saved in {LEARNED_FILE.name} - delete that file to forget them")
    print(f"VERDICT  : {r['verdict']}")
    print(f"CSV      : {out}   (holds real page values - keep it on this PC)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
