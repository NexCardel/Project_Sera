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
    args = ap.parse_args(argv)

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
