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
    changes with time           differed between days, agreed within each day (noise - dates, notices)
    repeat                      pending, shaped like a memory node (one more row)
    only one client so far      waiting for more clients
    same for all clients        template
    probably furniture          differs between clients, sentence/label shaped, no label beside it
    differs between clients     data

    python tools/pre_dev/class_diff/memory.py            # N = 2; both client orders

Console: counts only. output/memory_<page>.csv holds the nodes and their texts (client data,
git-ignored, keep on this PC).
"""

import argparse
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from core.sdis import link_map
from core.sdis.align import align, pair_moved, shape
from core.sdis.keys import CHOICE_CTYPES, page_slug
from core.sdis.labels import composites, value_type
from core.sdis.noise import changes_within_client, changes_with_time, probably_furniture
from core.sdis.paths import data_dir, write_csv

OUT_DIR = data_dir()
VARIABLE_ALIGNMENT = "variable_alignment"

SAME_MATCH, SAME_NEW = 0.95, 3        # same screen: >= 95% of the map matched, <= 3 new
RADICAL_MATCH = 0.50                  # radical: under half of the map matched memory
_SCREEN_RE = re.compile(r" \[screen (\d+)\]$")
_BROWSER_RE = re.compile(r" \[([a-zA-Z0-9_-]+)\]$")


def _always(e: Dict[str, Any]) -> bool:
    return True


def _match(flat: List[Dict[str, Any]], other: List[Dict[str, Any]],
           ambiguous: Optional[set] = None) -> Dict[int, int]:
    """{index in flat: index in other}: alignment, then moved blocks."""
    return pair_moved(flat, other, align(flat, other, _always, ambiguous=ambiguous), _always) if other and flat else {}


class PageMemory:
    def __init__(self, page: str, n_promote: int, screen: Optional[int] = None,
                 link: Optional[str] = None, browser: str = "",
                 rejected: Optional[Iterable[Tuple[str, str, str]]] = None) -> None:
        self.page, self.n = page, n_promote
        m = _SCREEN_RE.search(page)
        if screen is not None:
            self.screen = screen
        elif m:
            self.screen = int(m.group(1))
        else:
            self.screen = 1
        clean = _SCREEN_RE.sub("", page)
        mb = _BROWSER_RE.search(clean)
        if browser:
            self.browser = browser
        elif mb:
            self.browser = mb.group(1)
        else:
            self.browser = ""
        clean = _BROWSER_RE.sub("", clean)
        self.link = link if link is not None else clean
        self.rejected: set = set(rejected) if rejected else set()
        self.nodes: List[Dict[str, Any]] = []      # every node, memory or pending
        self.order: List[int] = []                 # memory, in page order (indexes into nodes)
        self.pending: List[int] = []               # pending, in the order they were first seen
        self.log: List[Dict[str, Any]] = []
        self._differs_shapes_cache: Optional[set] = None

    def _node(self, e: Dict[str, Any], anchor: Optional[int], status: str) -> int:
        node = e.get("node") or {}
        ctype = node.get("ctype") if isinstance(node, dict) else None
        if ctype is None:
            ctype = e.get("ctype")
        self.nodes.append({"shape": shape(e), "key": e["key"], "text": e["text"], "type": e["type"],
                           "ctype": ctype, "node": node,
                           "status": status, "anchor": anchor, "clients": {}, "composite": False})
        return len(self.nodes) - 1

    def _see(self, nid: int, e: Dict[str, Any], client: str, composite: bool, sure: bool = True) -> None:
        nd = self.nodes[nid]
        if "ctype" not in nd or nd["ctype"] is None:
            node = e.get("node") or {}
            ctype = node.get("ctype") if isinstance(node, dict) else None
            nd["ctype"] = ctype if ctype is not None else e.get("ctype")
        if "node" not in nd or not nd["node"]:
            if e.get("node"):
                nd["node"] = e["node"]
        if client not in nd["clients"]:
            c = nd["clients"].setdefault(client, {"texts": [], "history": [], "sure": sure})
        else:
            c = nd["clients"][client]
            if sure:
                c["sure"] = True
        c.setdefault("history", [])
        for t in (e.get("values") or ([e["text"]] if e["text"] else [])):
            if t not in c["texts"]:
                c["texts"].append(t)
        for h in e.get("history") or []:
            item = list(h) if isinstance(h, (list, tuple)) else h
            if item not in c["history"]:
                c["history"].append(item)
        nd["key"] = e["key"]
        nd["composite"] = nd["composite"] or composite

    def _view(self, ids: List[int]) -> List[Dict[str, Any]]:
        return [{"key": self.nodes[n]["key"], "text": self.nodes[n]["text"]} for n in ids]

    def add(self, flat: List[Dict[str, Any]], client: str) -> None:
        """One client's merged map of this link (link_map.LinkMap.to_flat())."""
        self._differs_shapes_cache = None
        comp = composites(flat)
        if not self.order:                                         # the first client's map IS the memory
            for i, e in enumerate(flat):
                nid = self._node(e, None, "memory")
                self.order.append(nid)
                self._see(nid, e, client, i in comp, sure=True)
            self.log.append({"client": client, "nodes": len(flat), "memory": 0, "matched": len(flat),
                             "pending_hit": 0, "new": 0, "kind": "FIRST"})
            return
        n_mem = len(self.order)
        amb: set = set()
        pairs = _match(flat, self._view(self.order), amb)               # 1-2: count + match vs memory
        anchor_of: Dict[int, Optional[int]] = {}
        anchor: Optional[int] = None
        for i in range(len(flat)):
            if i in pairs:
                anchor = self.order[pairs[i]]
                self._see(anchor, flat[i], client, i in comp, sure=(i not in amb))
            else:
                anchor_of[i] = anchor
        # 3: the leftovers against the pending pool, the same way (page order, then moved blocks).
        left = sorted(anchor_of)
        p_amb: set = set()
        ppairs = _match([flat[i] for i in left], self._view(self.pending), p_amb)
        hit = new = 0
        for x, i in enumerate(left):
            if x in ppairs:
                nid = self.pending[ppairs[x]]
                hit += 1
                sure = (x not in p_amb)
            else:
                nid = self._node(flat[i], anchor_of[i], "pending")
                self.pending.append(nid)
                new += 1
                sure = True
            self._see(nid, flat[i], client, i in comp, sure=sure)
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
        """Seen by N different clients with sure True. The first client's nodes sit in memory (they give the page
        order) but are no more confirmed than a pending node - so the client order changes nothing."""
        return sum(1 for c in self.nodes[nid]["clients"].values() if c.get("sure", True)) >= self.n

    def _differs_shapes(self) -> set:
        """The shapes of confirmed nodes whose verdict is 'differs between clients'."""
        if self._differs_shapes_cache is not None:
            return self._differs_shapes_cache
        shapes: set = set()
        self._differs_shapes_cache = set()
        for i, nd in enumerate(self.nodes):
            if not self.confirmed(i) or nd.get("composite"):
                continue
            if changes_within_client(nd) or changes_with_time(nd):
                continue
            cl = nd["clients"]
            last = {c["texts"][-1] if c["texts"] else "" for c in cl.values()}
            if len(last) > 1 and not probably_furniture(self, i):
                shapes.add(nd["shape"])
        self._differs_shapes_cache = shapes
        return shapes

    def verdict(self, nid: int, differs_shapes: Optional[set] = None,
                rejected: Optional[Iterable[Tuple[str, str, str]]] = None) -> str:
        nd = self.nodes[nid]
        cl = nd["clients"]
        if nd["composite"]:
            return "composite"
        if changes_within_client(nd):
            return "changes within one client"
        if changes_with_time(nd):
            return "changes with time"
        if not self.confirmed(nid):
            if any(not c.get("sure", True) for c in cl.values()):
                return "ambiguous"
            shapes = {self.nodes[m]["shape"] for m in range(len(self.nodes)) if self.confirmed(m)}
            return "repeat" if nd["shape"] in shapes else "only one client so far"
        last = {c["texts"][-1] if c["texts"] else "" for c in cl.values()}
        if len(last) == 1:
            text = next(iter(last)) if last else (nd.get("text") or "")
            if (value_type(text) != "label"
                    and not text.rstrip().endswith(":")
                    and not nd["composite"]):
                ctype = nd.get("ctype")
                if ctype not in CHOICE_CTYPES:
                    if differs_shapes is None:
                        differs_shapes = self._differs_shapes()
                    if nd["shape"] in differs_shapes:
                        rej = self.rejected if rejected is None else set(rejected)
                        sig = (self.link, nd["shape"], text)
                        sig_page = (self.page, nd["shape"], text)
                        if sig not in rej and sig_page not in rej:
                            return "variable_alignment"
            return "same for all clients"
        if probably_furniture(self, nid):
            return "probably furniture"
        return "differs between clients"

    def summary(self) -> Counter:
        differs = self._differs_shapes()
        return Counter(("confirmed" if self.confirmed(i) else "waiting",
                        self.verdict(i, differs_shapes=differs))
                       for i, nd in enumerate(self.nodes) if nd["text"])


def client_maps(client_link_maps: Optional[Dict[Tuple[str, str], link_map.LinkMap]] = None,
                sources: Optional[List[Any]] = None) -> Dict[str, Dict[str, link_map.LinkMap]]:
    """{page link: {client: that client's ONE map of it}} - sessions grouped into clients by the
    PAN / GSTIN SGT-C finds on any page they visited.
    Groups by base link and assigns each client's screen map to a PageMemory screen by weighted matching."""
    from core.sdis.identity import client_ids, group_clients, resolve_owners
    from core.sdis.screens import covers, link_weights, same_screen

    if sources is None:
        sources = link_map.all_sources()
    weights = link_weights(sources)

    if client_link_maps is None:
        session_maps = link_map.build_maps(sources=sources)
        ids: Dict[str, set] = {}
        for (session, _page), lm in session_maps.items():
            ids.setdefault(session, set()).update(client_ids(lm))
        base_clients = group_clients(ids)

        links_by_client: Dict[str, set] = {}
        for (session, page), lm in session_maps.items():
            client = base_clients.get(session, session)
            raw_link = getattr(lm, "link", page)
            links_by_client.setdefault(client, set()).add(raw_link)
        from core.sdis.links import resolve
        link_of = resolve(links_by_client)

        session_maps_resolved = link_map.build_maps(sources=sources, link_of=link_of)
        owners = resolve_owners(session_maps_resolved, ids)
        sources_filtered = [s for s in sources if not owners.get(s[1], "").startswith("undecided")]

        sources_resolved = [(s[0], s[1], link_of.get(s[2], s[2]), s[3]) for s in sources_filtered]
        weights = link_weights(sources_resolved)
        all_client_maps = link_map.build_maps(client_of_session=owners, sources=sources_filtered, link_of=link_of)
    else:
        all_client_maps = {k: v for k, v in client_link_maps.items() if not k[0].startswith("undecided")}


    by_link_browser: Dict[Tuple[str, str], List[link_map.LinkMap]] = {}
    for key, lm in all_client_maps.items():
        page_key = key[1] if isinstance(key, tuple) else getattr(lm, "page", "")
        base_link = getattr(lm, "link", page_key)
        browser = getattr(lm, "browser", "")
        by_link_browser.setdefault((base_link, browser), []).append(lm)

    out: Dict[str, Dict[str, link_map.LinkMap]] = {}

    for (base_link, browser), lms in by_link_browser.items():
        lms_sorted = sorted(lms, key=lambda m: m.reads[0] if m.reads else "")
        screen_mems: List[PageMemory] = []
        screen_client_maps: List[Dict[str, link_map.LinkMap]] = []

        for lm in lms_sorted:
            client = lm.client
            flat = lm.to_flat()

            if not screen_mems:
                page_name = f"{base_link} [{browser}]" if browser else base_link
                pm = PageMemory(page_name, n_promote=2, screen=1, link=base_link, browser=browser)
                pm.add(flat, client)
                screen_mems.append(pm)
                screen_client_maps.append({client: lm})
                continue

            best_pm = None
            best_idx = -1
            best_max_cover = -1.0

            for idx, pm in enumerate(screen_mems):
                if client in screen_client_maps[idx]:
                    continue
                cur = pm._view(pm.order)
                pairs = pair_moved(flat, cur, align(flat, cur, _always), _always) if cur else {}
                c_new, c_old = covers(flat, cur, pairs, weights)
                mc = max(c_new, c_old)
                if mc > best_max_cover:
                    best_max_cover = mc
                    best_pm = pm
                    best_idx = idx

            if best_pm is not None and same_screen(best_max_cover, 0.0):
                screen_client_maps[best_idx][client] = lm
                best_pm.add(flat, client)
            else:
                n_screen = len(screen_mems) + 1
                prefix = f"{base_link} [{browser}]" if browser else base_link
                page_name = f"{prefix} [screen {n_screen}]"
                pm = PageMemory(page_name, n_promote=2, screen=n_screen, link=base_link, browser=browser)
                pm.add(flat, client)
                screen_mems.append(pm)
                screen_client_maps.append({client: lm})

        for pm, cm in zip(screen_mems, screen_client_maps):
            out[pm.page] = cm

    return out


def build(page: str, maps: Dict[str, link_map.LinkMap], order: List[str], n: int,
          screen: Optional[int] = None, link: Optional[str] = None,
          browser: Optional[str] = None,
          rejected: Optional[Iterable[Tuple[str, str, str]]] = None) -> PageMemory:
    first_lm = next(iter(maps.values())) if maps else None
    if screen is None and first_lm is not None:
        screen = getattr(first_lm, "screen", None)
    if link is None and first_lm is not None:
        link = getattr(first_lm, "link", None)
    if browser is None and first_lm is not None:
        browser = getattr(first_lm, "browser", "")
    mem = PageMemory(page, n, screen=screen, link=link, browser=browser or "", rejected=rejected)
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
        first_lm = next(iter(cm.values())) if cm else None
        b = getattr(first_lm, "browser", "")
        b_str = f" [{b}]" if (b and f"[{b}]" not in page) else ""
        print(f"Page: {page}{b_str}")
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
                differs = m._differs_shapes()
                rows = [{"status": "confirmed" if m.confirmed(i) else "waiting",
                         "verdict": m.verdict(i, differs_shapes=differs), "clients": len(nd["clients"]),
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
