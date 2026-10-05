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
Each node gets a verdict from the counts only (PageMemory.verdict; the first that applies wins):
    retired                     a confirmed node that stopped appearing (Part H)
    composite                   its text is its children's texts joined (a tile title + its count)
    changes within one client   more than one value for the same client (noise - notices, dates)
    changes with time           differed between days, agreed within each day (noise - dates, notices)
    ambiguous                   not confirmed, and a client paired it only as an unsure look-alike (Part E)
    repeat                      pending, shaped like a memory node (one more row)
    only one client so far      waiting for more clients
    variable_alignment          same for all clients, but its shape holds data elsewhere (Part F)
    same for all clients        template
    probably furniture          differs between clients, sentence/label shaped, no label beside it
    differs between clients     data
Only clients whose pairing was sure vote (Part E): an unsure look-alike's texts are never counted.

    python tools/pre_dev/class_diff/memory.py            # N = 2; both client orders

Console: counts only. output/memory_<page>.csv holds the nodes and their texts (client data,
git-ignored, keep on this PC).
"""

import argparse
import re
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.sdis import link_map
from core.sdis.align import align, pair_moved, shape
from core.sdis.keys import CHOICE_CTYPES, page_slug
from core.sdis.labels import FIXABLE_TYPES, NEVER_LABEL_CTYPES, _label, cell_labels, composites, value_type
from core.sdis.noise import changes_within_client, changes_with_time, probably_furniture
from core.sdis.paths import data_dir, write_csv

OUT_DIR = data_dir()
VARIABLE_ALIGNMENT = "variable_alignment"

SAME_MATCH, SAME_NEW = 0.95, 3        # same screen: >= 95% of the map matched, <= 3 new
RADICAL_MATCH = 0.50                  # radical: under half of the map matched memory
RETIRE_P = 0.01                       # retire: (1 - p) ** misses < 0.01 (1%)
_STATUS_OF = {"differs between clients": "variable", VARIABLE_ALIGNMENT: VARIABLE_ALIGNMENT, "ambiguous": "ambiguous",
              "changes within one client": "furniture", "changes with time": "furniture",
              "probably furniture": "furniture", "repeat": "waiting", "only one client so far": "waiting",
              "retired": "retired", "composite": "composite"}
_SCREEN_RE = re.compile(r" \[screen (\d+)\]$")
_BROWSER_RE = re.compile(r" \[([a-zA-Z0-9_-]+)\]$")


def _always(e: Dict[str, Any]) -> bool:
    return True


def voters(nd: Dict[str, Any]) -> Dict[str, Any]:
    """The node with only the clients whose pairing was sure: an unsure look-alike has no vote."""
    cl = nd.get("clients", {})
    return dict(nd, clients={c: v for c, v in cl.items() if v.get("sure", True)})


def _last_texts(nd: Dict[str, Any]) -> set:
    return {c["texts"][-1] if c.get("texts") else "" for c in nd.get("clients", {}).values()}


def _match(flat: List[Dict[str, Any]], other: List[Dict[str, Any]],
           ambiguous: Optional[set] = None) -> Dict[int, int]:
    """{index in flat: index in other}: alignment, then moved blocks."""
    return pair_moved(flat, other, align(flat, other, _always, ambiguous=ambiguous), _always) if other and flat else {}


class PageMemory:
    def __init__(self, page: str, n_promote: int, screen: Optional[int] = None,
                 link: Optional[str] = None, browser: str = "",
                 rejected: Optional[Iterable[Tuple[str, str, str]]] = None, retire: bool = True) -> None:
        self.page, self.n, self.retire = page, n_promote, retire
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
        self.clients: List[str] = []               # clients in the order they were added
        self.read_info: Dict[str, List[Dict[str, str]]] = {}   # client -> its reads (stamp, period)
        self.nodes: List[Dict[str, Any]] = []      # every node, memory or pending
        self.order: List[int] = []                 # memory, in page order (indexes into nodes)
        self.pending: List[int] = []               # pending, in the order they were first seen
        self.log: List[Dict[str, Any]] = []
        self.views: Dict[str, Tuple[List[Dict[str, Any]], List[int]]] = {}   # client -> (its last flat, node id per index)
        self._view_pos: Dict[str, Dict[int, int]] = {}
        self._label_cache: Dict[str, Tuple[set, Dict[int, str]]] = {}
        self._differs_shapes_cache: Optional[set] = None
        self._confirmed_shapes_cache: Optional[set] = None

    def _node(self, e: Dict[str, Any], anchor: Optional[int], status: str,
              first_ci: Optional[int] = None, last_ci: Optional[int] = None) -> int:
        node = e.get("node") or {}
        ctype = node.get("ctype") if isinstance(node, dict) else None
        if ctype is None:
            ctype = e.get("ctype")
        if first_ci is None:
            first_ci = len(self.clients) - 1 if self.clients else 0
        if last_ci is None:
            last_ci = first_ci
        self.nodes.append({"shape": shape(e), "key": e["key"], "text": e["text"], "type": e["type"],
                           "ctype": ctype, "node": node,
                           "status": status, "anchor": anchor, "clients": {}, "composite": False,
                           "first_ci": first_ci, "last_ci": last_ci})
        return len(self.nodes) - 1

    def _see(self, nid: int, e: Dict[str, Any], client: str, composite: bool, sure: bool = True,
             ci: Optional[int] = None) -> None:
        nd = self.nodes[nid]
        if ci is None:
            ci = self.clients.index(client) if client in self.clients else (len(self.clients) - 1 if self.clients else 0)
        nd["last_ci"] = max(nd.get("last_ci", ci), ci)         # a client added again must not look like a miss
        if "first_ci" not in nd:
            nd["first_ci"] = ci
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
        # node carries the rect, so look-alikes score their screen column against memory too (Part E)
        return [{"key": self.nodes[n]["key"], "text": self.nodes[n]["text"], "node": self.nodes[n].get("node") or {}}
                for n in ids]

    def add(self, flat: List[Dict[str, Any]], client: str, reads: Optional[List[Dict[str, str]]] = None) -> None:
        """One client's merged map of this link (link_map.LinkMap.to_flat()); reads = its read_info."""
        if reads is not None:
            self.read_info[client] = list(reads)
        is_first = len(self.clients) == 0
        if client not in self.clients:
            self.clients.append(client)
        ci = self.clients.index(client)

        self._differs_shapes_cache = None
        self._confirmed_shapes_cache = None
        self._label_cache = {}
        comp = composites(flat)
        nid_of: List[int] = [-1] * len(flat)
        self.views[client] = (flat, nid_of)
        self._view_pos.pop(client, None)
        if is_first:                                               # the first client's map IS the memory
            for i, e in enumerate(flat):
                nid = self._node(e, None, "memory", first_ci=ci, last_ci=ci)
                nid_of[i] = nid
                self.order.append(nid)
                self._see(nid, e, client, i in comp, sure=True, ci=ci)
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
                nid_of[i] = anchor
                self._see(anchor, flat[i], client, i in comp, sure=(i not in amb), ci=ci)
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
                nid = self._node(flat[i], anchor_of[i], "pending", first_ci=ci, last_ci=ci)
                self.pending.append(nid)
                new += 1
                sure = True
            nid_of[i] = nid
            self._see(nid, flat[i], client, i in comp, sure=sure, ci=ci)
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
        if self.retire:
            self._retire()

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

    def _retire(self) -> None:
        """Retire confirmed memory nodes that stopped appearing, by statistics (Part H). 'In a row'
        follows the order clients were added (time), so retirement is left out of the order check."""
        n_clients = len(self.clients)
        retired_any = False
        for nid in list(self.order):
            if not self.confirmed(nid):
                continue
            nd = self.nodes[nid]
            first_ci = nd.get("first_ci", 0)
            last_ci = nd.get("last_ci", first_ci)
            chances = n_clients - first_ci
            seen = len(nd["clients"])
            misses = n_clients - 1 - last_ci
            if misses < 2:
                continue
            p = (seen + 1) / (chances + 2)
            if (1.0 - p) ** misses < RETIRE_P:
                self.order.remove(nid)
                nd["status"] = "retired"
                nd["verdict"] = "retired"
                retired_any = True
        if retired_any:
            self._differs_shapes_cache = None
            self._confirmed_shapes_cache = None
            self._label_cache = {}

    def confirmed(self, nid: int) -> bool:
        """Seen by N different clients with sure True. The first client's nodes sit in memory (they give the page
        order) but are no more confirmed than a pending node - so the client order changes nothing."""
        return sum(1 for c in self.nodes[nid]["clients"].values() if c.get("sure", True)) >= self.n

    def _differs_shapes(self) -> set:
        """The shapes of nodes whose verdict is 'differs between clients' (data)."""
        if self._differs_shapes_cache is None:
            self._differs_shapes_cache = {nd["shape"] for i, nd in enumerate(self.nodes)
                                          if self.base_verdict(i) == "differs between clients"
                                          and not probably_furniture(self, i)}
        return self._differs_shapes_cache

    def base_verdict(self, nid: int) -> str:
        """verdict() without its two refinements (variable_alignment, probably furniture), which
        look at other nodes' base verdicts - so nothing here depends on another node's verdict."""
        nd = self.nodes[nid]
        if nd.get("status") == "retired" or nd.get("verdict") == "retired":
            return "retired"
        if nd["composite"]:
            return "composite"
        votes = voters(nd)
        if changes_within_client(votes):
            return "changes within one client"
        if changes_with_time(votes):
            return "changes with time"
        if not self.confirmed(nid):
            if any(not c.get("sure", True) for c in nd["clients"].values()):
                return "ambiguous"
            if self._confirmed_shapes_cache is None:      # once per change of the nodes, not once per node
                self._confirmed_shapes_cache = {m["shape"] for i, m in enumerate(self.nodes)
                                                if m.get("status") != "retired" and self.confirmed(i)}
            return "repeat" if nd["shape"] in self._confirmed_shapes_cache else "only one client so far"
        return "same for all clients" if len(_last_texts(votes)) == 1 else "differs between clients"

    def verdict(self, nid: int, differs_shapes: Optional[set] = None,
                rejected: Optional[Iterable[Tuple[str, str, str]]] = None) -> str:
        """The node's verdict, from counts only. The first rule that applies wins:
          1. retired                     (Part H)
          2. composite
          3. noise: changes within one client, then changes with time (Part C rules 1-2)
          4. ambiguous: not confirmed, and some client paired it only as an unsure look-alike (Part E)
          5. waiting: repeat, else only one client so far
          6. variable_alignment: one shared text, not label/control/composite, whose shape is data
             elsewhere on the page (Part F) - unless rejected
          7. probably furniture: differs, every client's text sentence/label shaped, no label (Part C rule 3)
          8. same for all clients / differs between clients
        Only sure clients vote. The client order changes none of 2-8 (retirement follows time)."""
        base = self.base_verdict(nid)
        nd = self.nodes[nid]
        if base == "same for all clients":
            text = next(iter(_last_texts(voters(nd))))
            if (value_type(text) != "label" and not text.rstrip().endswith(":")
                    and nd.get("ctype") not in CHOICE_CTYPES
                    and nd["shape"] in (self._differs_shapes() if differs_shapes is None else differs_shapes)):
                rej = self.rejected if rejected is None else set(rejected)
                if (self.link, nd["shape"], text) not in rej and (self.page, nd["shape"], text) not in rej:
                    return VARIABLE_ALIGNMENT
        elif base == "differs between clients" and probably_furniture(self, nid):
            return "probably furniture"
        return base

    def status(self, nid: int) -> str:
        """The node's status for the Distill dialog, from its verdict: fixed / semi-variable /
        variable / variable_alignment / ambiguous / furniture / waiting / retired / composite.
        'Same for all clients' is fixed for a fixable text type, semi-variable for any data type
        (a choice is data: the clients may just have chosen alike)."""
        v = self.verdict(nid)
        if v == "same for all clients":
            return "fixed" if self._type_of(nid) in FIXABLE_TYPES else "semi-variable"
        return _STATUS_OF.get(v, "waiting")

    def _type_of(self, nid: int, text: Optional[str] = None) -> str:
        nd = self.nodes[nid]
        if nd.get("ctype") in CHOICE_CTYPES:
            return "control"
        if text is None:
            texts = _last_texts(voters(nd)) or {nd["text"]}
            text = next(iter(texts))
        return value_type(text)

    def _view_of(self, nid: int) -> Optional[Tuple[str, int]]:
        """(client, index in that client's flat) from the latest client whose view holds the node."""
        for client in reversed(self.clients):
            view = self.views.get(client)
            if view is None:
                continue
            pos = self._view_pos.get(client)
            if pos is None:
                pos = self._view_pos[client] = {n: i for i, n in enumerate(view[1])}
            if nid in pos:
                return client, pos[nid]
        return None

    def _label_info(self, client: str) -> Tuple[set, Dict[int, str]]:
        """For one client's view: the indexes that may be labels, and its table cells' labels. A
        label is a text the clients share (compare.py's 'shared': fixed, variable_alignment, a
        semi-variable that is words with digits), plus an unpaired text that repeats such a text of
        the same shape ("Period", "ARN" on one more list row); never a composite, link, image or
        button, and only with a real letter. Judged from the base verdict: variable_alignment
        refines 'same for all clients' without changing who may be a label, and probably furniture
        asks for labels itself."""
        if client not in self._label_cache:
            flat, nid_of = self.views[client]
            shared: set = set()
            waiting: List[int] = []
            for j, nid in enumerate(nid_of):
                base = self.base_verdict(nid)
                if base == "same for all clients":
                    if self._type_of(nid, flat[j]["text"]) in FIXABLE_TYPES | {"alphanumeric"}:
                        shared.add(j)
                elif base in ("repeat", "only one client so far"):
                    waiting.append(j)
            template = {(shape(flat[j]), flat[j]["text"]) for j in shared}
            shared |= {j for j in waiting if (shape(flat[j]), flat[j]["text"]) in template}
            cands = {j for j in shared if self.nodes[nid_of[j]].get("ctype") not in NEVER_LABEL_CTYPES
                     and any(c.isalpha() for c in flat[j]["text"])}
            self._label_cache[client] = (cands, cell_labels(flat))
        return self._label_cache[client]

    def label(self, nid: int) -> str:
        """The node's label, from the latest client view that holds it: inside a real table its
        column name (and a matrix's row name), else the screen-box rules over the texts the clients
        share (labels._label). '' when the node is in no client's view or nothing labels it."""
        view = self._view_of(nid)
        if view is None:
            return ""
        client, i = view
        cands, cells = self._label_info(client)
        return cells.get(i) or _label(self.views[client][0], i, cands)

    def summary(self) -> Counter:
        differs = self._differs_shapes()
        return Counter((nd.get("status") if nd.get("status") == "retired" else ("confirmed" if self.confirmed(i) else "waiting"),
                        self.verdict(i, differs_shapes=differs))
                       for i, nd in enumerate(self.nodes) if nd["text"])


def client_maps(client_link_maps: Optional[Dict[Tuple[str, str], link_map.LinkMap]] = None,
                sources: Optional[List[Any]] = None) -> Dict[str, Dict[str, link_map.LinkMap]]:
    """{page link: {client: that client's ONE map of it}} - sessions grouped into clients by the
    PAN / GSTIN SGT-C finds on any page they visited.
    Groups by base link and assigns each client's screen map to a PageMemory screen by weighted matching."""
    from core.sdis.identity import client_ids, group_clients, resolve_owners
    from core.sdis.screens import link_weights

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
            taken = {i for i, cm in enumerate(screen_client_maps) if lm.client in cm}
            _pm, idx = place_map(screen_mems, lm, base_link, browser, weights, taken)
            if idx == len(screen_client_maps):
                screen_client_maps.append({})
            screen_client_maps[idx][lm.client] = lm

        for pm, cm in zip(screen_mems, screen_client_maps):
            out[pm.page] = cm

    return out


def place_map(screen_mems: List[PageMemory], lm: link_map.LinkMap, base_link: str, browser: str,
              weights: Any, taken: Iterable[int] = (), rejected: Optional[Iterable[Tuple[str, str, str]]] = None
              ) -> Tuple[PageMemory, int]:
    """Part G: add one client's map to the screen memory of (base_link, browser) it is the same screen as
    (best weighted cover; screens in `taken` already hold another map of this client), else to a new
    screen appended to `screen_mems`. Returns (that memory, its index in screen_mems)."""
    from core.sdis.screens import covers, same_screen

    flat = lm.to_flat()
    taken = set(taken)
    prefix = f"{base_link} [{browser}]" if browser else base_link
    best_idx, best_max_cover = -1, -1.0
    for idx, pm in enumerate(screen_mems):
        if idx in taken:
            continue
        cur = pm._view(pm.order)
        pairs = pair_moved(flat, cur, align(flat, cur, _always), _always) if cur else {}
        c_new, c_old = covers(flat, cur, pairs, weights)
        mc = max(c_new, c_old)
        if mc > best_max_cover:
            best_max_cover, best_idx = mc, idx
    if best_idx >= 0 and same_screen(best_max_cover, 0.0):
        pm = screen_mems[best_idx]
    else:
        best_idx = len(screen_mems)
        n_screen = best_idx + 1
        pm = PageMemory(prefix if n_screen == 1 else f"{prefix} [screen {n_screen}]", n_promote=2, screen=n_screen,
                        link=base_link, browser=browser, rejected=rejected)
        screen_mems.append(pm)
    pm.add(flat, lm.client, lm.read_info)
    return pm, best_idx


def build(page: str, maps: Dict[str, link_map.LinkMap], order: List[str], n: int,
          screen: Optional[int] = None, link: Optional[str] = None,
          browser: Optional[str] = None,
          rejected: Optional[Iterable[Tuple[str, str, str]]] = None, retire: bool = True) -> PageMemory:
    first_lm = next(iter(maps.values())) if maps else None
    if screen is None and first_lm is not None:
        screen = getattr(first_lm, "screen", None)
    if link is None and first_lm is not None:
        link = getattr(first_lm, "link", None)
    if browser is None and first_lm is not None:
        browser = getattr(first_lm, "browser", "")
    mem = PageMemory(page, n, screen=screen, link=link, browser=browser or "", rejected=rejected, retire=retire)
    for client in order:
        if client in maps:
            mem.add(maps[client].to_flat(), client, getattr(maps[client], "read_info", None))
    return mem


def _main_state(state_path: Optional[str]) -> int:
    from core.sdis import mine
    r = mine.mine(OUT_DIR, state_path, progress=lambda done, total, page: print(f"  client map {done}/{total}"))
    print(f"new snapshots {r['processed']} in {r['files']} files, client maps added {r['clients']}, "
          f"pages {r['pages']}, rebuilt {len(r['rebuilt'])}{' (all)' if r['rebuild_all'] else ''}, "
          f"{'CANCELLED, ' if r['cancelled'] else ''}{r['skipped']} unreadable files, {r['seconds']} s")
    for k, n in sorted(r["verdicts"].items()):
        print(f"      {k:36s} {n:5d} text nodes")
    print(f"datapoints {r['datapoints']}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="SDIS page memory over every client's map in output/.")
    ap.add_argument("--n", type=int, default=2, help="different clients a pending node needs to join memory")
    ap.add_argument("--state", nargs="?", const="", default=None, metavar="PATH",
                    help="use the incremental miner (Part O): only new captures are taken, the state is saved at PATH"
                         " (default: memory.json.gz in the data folder); prints counts only")
    args = ap.parse_args(argv)
    if args.state is not None:
        return _main_state(args.state or None)
    pages = {p: cm for p, cm in client_maps().items() if len(cm) >= 2}
    if not pages:
        raise SystemExit("No page link has two clients yet - capture it for another client first.")
    print(f"N = {args.n}\n")
    stable = True
    forward_mems = []
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
            if label == "forward":
                forward_mems.append(m)
            sums.append(build(page, cm, o, args.n, retire=False).summary())   # retirement follows time
            print(f"  {label}:")
            for r in m.log:
                print(f"    {r['client'][-26:]:26s} nodes {r['nodes']:4d}  memory {r['memory']:4d}  matched {r['matched']:4d}"
                      f" ({100 * r['matched'] / max(1, r['nodes']):5.1f}%)  pending hit {r['pending_hit']:3d}"
                      f"  new {r['new']:3d}  {r['kind']}")
            print(f"    memory {len(m.order)} nodes, pending {len(m.pending)}")
            if label == "forward":
                for (st, v), k in sorted(m.summary().items()):
                    print(f"      {st:8s} {v:26s} {k:5d} text nodes")
                differs = m._differs_shapes()
                rows = [{"status": nd.get("status") if nd.get("status") == "retired" else ("confirmed" if m.confirmed(i) else "waiting"),
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
    from core.sdis.relevance import datapoints
    dps = datapoints(forward_mems)
    top10 = [dp.relevance_pct for dp in dps[:10]]
    print(f"datapoints {len(dps)}, top 10 relevance %: {top10}")
    from core.sdis.classes import annotate
    annotate(dps, forward_mems)
    counts = Counter(dp.suggested_class or "none" for dp in dps)
    print("suggested class: " + ", ".join(f"{c} {counts[c]}" for c in ("profile", "dataset", "info", "none")))
    print("(output/memory_*.csv hold real page values - keep them on this PC)")
    return 0
