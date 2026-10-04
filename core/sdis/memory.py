"""
core/sdis/memory.py - SDIS page memory, the stage after link_map.py
====================================================================
The pipeline:

  key_probe.py  snapshots of a page link
  link_map.py   one client's snapshots of one link merged into ONE map (alignment: shape + face)
  memory.py     every client's map of that link, one after another, into the link's MEMORY

Per page link SDIS keeps a MEMORY: a flat list of nodes in page order - no tree. The first
client's map becomes the memory. Every later client's map is:

  1. COUNTED  - its total nodes vs the memory's, and how many of them match;
  2. MATCHED  - against the memory by alignment (align.py: shape + face anchors in page order, the
                rest by shape between anchors), then moved blocks (same key + face, or a unique
                shape + face);
  3. PENDING  - what matches nothing in memory is matched the same way against the PENDING pool
                (kept in page order, flat), else it becomes a new pending node, remembered by its
                ANCHOR - the memory node just above it;
  4. PROMOTED - a pending node seen by N different clients joins the memory, after its anchor.

One client = one vote per page (R8): its snapshots are already one map (link_map.py), and its
sessions are grouped by PAN / GSTIN (link_map.group_clients), so two captures of one client are
one map too.

Each client's map is sorted by its counts: SAME SCREEN (nearly everything matched, almost nothing
new), CHANGED (some new), RADICAL (most of it matched nothing - another screen at the link).
Each node gets a verdict from the counts only:
    composite                   its text is its children's texts joined (a tile title + its count)
    changes within one client   more than one value for the same client (noise - notices, dates)
    repeat                      pending, shaped like a memory node (one more row)
    only one client so far      waiting for more clients
    same for all clients        template
    differs between clients     data

    python tools/pre_dev/class_diff/memory.py            # N = 2; both client orders

Console: counts only. output/memory_<page>.csv holds the nodes and their texts (client data,
git-ignored, keep on this PC).
"""

import argparse
from collections import Counter
from typing import Any, Dict, List, Optional

from core.sdis import link_map
from core.sdis.align import align, pair_moved, shape
from core.sdis.keys import page_slug
from core.sdis.labels import composites
from core.sdis.paths import data_dir, write_csv

OUT_DIR = data_dir()

SAME_MATCH, SAME_NEW = 0.95, 3        # same screen: >= 95% of the map matched, <= 3 new
RADICAL_MATCH = 0.50                  # radical: under half of the map matched memory


def _always(e: Dict[str, Any]) -> bool:
    return True


def _match(flat: List[Dict[str, Any]], other: List[Dict[str, Any]]) -> Dict[int, int]:
    """{index in flat: index in other}: alignment, then moved blocks."""
    return pair_moved(flat, other, align(flat, other, _always), _always) if other and flat else {}


class PageMemory:
    def __init__(self, page: str, n_promote: int) -> None:
        self.page, self.n = page, n_promote
        self.nodes: List[Dict[str, Any]] = []      # every node, memory or pending
        self.order: List[int] = []                 # memory, in page order (indexes into nodes)
        self.pending: List[int] = []               # pending, in the order they were first seen
        self.log: List[Dict[str, Any]] = []

    def _node(self, e: Dict[str, Any], anchor: Optional[int], status: str) -> int:
        self.nodes.append({"shape": shape(e), "key": e["key"], "text": e["text"], "type": e["type"],
                           "status": status, "anchor": anchor, "clients": {}, "composite": False})
        return len(self.nodes) - 1

    def _see(self, nid: int, e: Dict[str, Any], client: str, composite: bool) -> None:
        nd = self.nodes[nid]
        c = nd["clients"].setdefault(client, {"texts": []})
        for t in (e.get("values") or ([e["text"]] if e["text"] else [])):
            if t not in c["texts"]:
                c["texts"].append(t)
        nd["key"] = e["key"]
        nd["composite"] = nd["composite"] or composite

    def _view(self, ids: List[int]) -> List[Dict[str, Any]]:
        return [{"key": self.nodes[n]["key"], "text": self.nodes[n]["text"]} for n in ids]

    def add(self, flat: List[Dict[str, Any]], client: str) -> None:
        """One client's merged map of this link (link_map.LinkMap.to_flat())."""
        comp =composites(flat)
        if not self.order:                                         # the first client's map IS the memory
            for i, e in enumerate(flat):
                nid = self._node(e, None, "memory")
                self.order.append(nid)
                self._see(nid, e, client, i in comp)
            self.log.append({"client": client, "nodes": len(flat), "memory": 0, "matched": len(flat),
                             "pending_hit": 0, "new": 0, "kind": "FIRST"})
            return
        n_mem = len(self.order)
        pairs = _match(flat, self._view(self.order))               # 1-2: count + match vs memory
        anchor_of: Dict[int, Optional[int]] = {}
        anchor: Optional[int] = None
        for i in range(len(flat)):
            if i in pairs:
                anchor = self.order[pairs[i]]
                self._see(anchor, flat[i], client, i in comp)
            else:
                anchor_of[i] = anchor
        # 3: the leftovers against the pending pool, the same way (page order, then moved blocks).
        left = sorted(anchor_of)
        ppairs = _match([flat[i] for i in left], self._view(self.pending))
        hit = new = 0
        for x, i in enumerate(left):
            if x in ppairs:
                nid = self.pending[ppairs[x]]
                hit += 1
            else:
                nid = self._node(flat[i], anchor_of[i], "pending")
                self.pending.append(nid)
                new += 1
            self._see(nid, flat[i], client, i in comp)
        matched = len(pairs) / len(flat) if flat else 1.0
        if matched >= SAME_MATCH and new <= SAME_NEW:
            kind = "SAME SCREEN"
        elif matched < RADICAL_MATCH:
            kind = "RADICAL"
        else:
            kind = "CHANGED"
        self.log.append({"client": client, "nodes": len(flat), "memory": n_mem, "matched": len(pairs),
                         "pending_hit": hit, "new": new, "kind": kind})
        self._promote()

    def _promote(self) -> None:
        """4: pending nodes seen by N clients join the memory right after their anchor (after any
        node already promoted to the same anchor, so their own order is kept)."""
        for p in list(self.pending):
            if len(self.nodes[p]["clients"]) < self.n:
                continue
            self.pending.remove(p)
            self.nodes[p]["status"] = "memory"
            a = self.nodes[p]["anchor"]
            pos = self.order.index(a) + 1 if a in self.order else 0
            while pos < len(self.order) and self.nodes[self.order[pos]].get("promoted_after") == a:
                pos += 1
            self.nodes[p]["promoted_after"] = a
            self.order.insert(pos, p)

    def confirmed(self, nid: int) -> bool:
        """Seen by N different clients. The first client's nodes sit in memory (they give the page
        order) but are no more confirmed than a pending node - so the client order changes nothing."""
        return len(self.nodes[nid]["clients"]) >= self.n

    def verdict(self, nid: int) -> str:
        nd = self.nodes[nid]
        cl = nd["clients"]
        if nd["composite"]:
            return "composite"
        if any(len(c["texts"]) > 1 for c in cl.values()):
            return "changes within one client"
        if not self.confirmed(nid):
            shapes = {self.nodes[m]["shape"] for m in range(len(self.nodes)) if self.confirmed(m)}
            return "repeat" if nd["shape"] in shapes else "only one client so far"
        last = {c["texts"][-1] if c["texts"] else "" for c in cl.values()}
        return "same for all clients" if len(last) == 1 else "differs between clients"

    def summary(self) -> Counter:
        return Counter(("confirmed" if self.confirmed(i) else "waiting", self.verdict(i))
                       for i, nd in enumerate(self.nodes) if nd["text"])


def client_maps() -> Dict[str, Dict[str, link_map.LinkMap]]:
    """{page link: {client: that client's ONE map of it}} - sessions grouped into clients by the
    PAN / GSTIN SGT-C finds on any page they visited."""
    from core.sdis.identity import client_ids
    ids: Dict[str, set] = {}
    for (session, _page), lm in link_map.build_maps().items():
        ids.setdefault(session, set()).update(client_ids(lm))
    owners = link_map.group_clients(ids)
    out: Dict[str, Dict[str, link_map.LinkMap]] = {}
    for (client, page), lm in link_map.build_maps(client_of_session=owners).items():
        out.setdefault(page, {})[client] = lm
    return out


def build(page: str, maps: Dict[str, link_map.LinkMap], order: List[str], n: int) -> PageMemory:
    mem = PageMemory(page, n)
    for client in order:
        if client in maps:
            mem.add(maps[client].to_flat(), client)
    return mem


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="SDIS page memory over every client's map in output/.")
    ap.add_argument("--n", type=int, default=2, help="different clients a pending node needs to join memory")
    args = ap.parse_args(argv)
    pages = {p: cm for p, cm in client_maps().items() if len(cm) >= 2}
    if not pages:
        raise SystemExit("No page link has two clients yet - capture it for another client first.")
    print(f"N = {args.n}\n")
    stable = True
    for page in sorted(pages):
        cm = pages[page]
        order = sorted(cm, key=lambda c: cm[c].reads[0])
        print(f"Page: {page}")
        sums = []
        for label, o in (("forward", order), ("reversed", order[::-1])):
            m = build(page, cm, o, args.n)
            sums.append(m.summary())
            print(f"  {label}:")
            for r in m.log:
                print(f"    {r['client'][-26:]:26s} nodes {r['nodes']:4d}  memory {r['memory']:4d}  matched {r['matched']:4d}"
                      f" ({100 * r['matched'] / max(1, r['nodes']):5.1f}%)  pending hit {r['pending_hit']:3d}"
                      f"  new {r['new']:3d}  {r['kind']}")
            print(f"    memory {len(m.order)} nodes, pending {len(m.pending)}")
            if label == "forward":
                for (st, v), k in sorted(sums[0].items()):
                    print(f"      {st:8s} {v:26s} {k:5d} text nodes")
                rows = [{"status": "confirmed" if m.confirmed(i) else "waiting", "verdict": m.verdict(i), "clients": len(nd["clients"]),
                         "type": nd["type"], "shape": nd["shape"],
                         "texts": " || ".join(t for c in nd["clients"].values() for t in c["texts"])}
                        for i, nd in enumerate(m.nodes) if nd["text"]]
                write_csv(OUT_DIR / f"memory_{page_slug(page)}.csv",
                          ["status", "verdict", "clients", "type", "shape", "texts"], rows)
        if sums[0] != sums[1]:
            stable = False
            print(f"  ORDER CHANGES THE RESULT: forward {dict(sums[0] - sums[1])}  reversed {dict(sums[1] - sums[0])}")
        print()
    print(f"Same verdicts in both client orders: {'yes' if stable else 'NO'}")
    print("(output/memory_*.csv hold real page values - keep them on this PC)")
    return 0
