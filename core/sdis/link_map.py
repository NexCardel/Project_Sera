"""
core/sdis/link_map.py - one map per client per page link
=========================================================
Every node ever read at one page link, for one client, merged into ONE map. Each read is paired
with the map by ALIGNMENT - shape (the key without its counters) and face (the text) - never by
key alone, so a re-render that renumbers elements does not store them twice. A link that shows
several screens (a form, then a popup, then a success message) is still one map: each screen only
adds the elements that paired with nothing.

For each entry the map keeps: the latest node (type, classes, id), its latest key and text, every
distinct value seen (up to MAX_VALUES), and the read it first appeared in / was last seen in.

EVENTS: each read after the first that brought keys the map had never had is an event - the
moments a new part of the page appeared (a popup, a submission message). A block that only came
BACK (the page behind a closed popup) brings no new keys, so it is no event.

The map is kept in page order: a new key is placed right after the sibling it followed in the
read that brought it, so to_flat() gives the same list shape as keys.flatten() and compare.py
works on a map exactly as on a single read.

Sources: every capture (key_probe.py, default mode: output/capture_*.json) is ONE session = one
client: its kept snapshots are merged in order into one map per page link it visited. A single
read (key_probe.py --once: output/key_probe_*.json) is its own session ("read <time>"), unless it
carries an old --client name.

    python tools/pre_dev/class_diff/link_map.py          # list every map: reads, nodes, events

Writes output/map_<client>_<latest read time>.csv per map (keys, texts, values seen - client data,
kept in the git-ignored output folder on this PC).
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.sdis.align import align, pair_moved
from core.sdis.keys import flatten
from core.sdis.paths import data_dir, write_csv

OUT_DIR = data_dir()

MAX_VALUES = 5
BLOCK_TEXTS = 3


def read_stamp(path: Path) -> str:
    """The read's time from its file name (key_probe_<time>[__<page>].json)."""
    return path.stem[len("key_probe_"):].split("__")[0]


def client_of(rec: Dict[str, Any], path: Path) -> str:
    return (rec.get("client") or "").strip() or f"read {read_stamp(path)}"


def page_of(rec: Dict[str, Any]) -> str:
    return rec.get("page") or ("title: " + (rec.get("title") or ""))


def _always(e: Dict[str, Any]) -> bool:
    return True


_SCREEN_RE = re.compile(r" \[screen (\d+)\]$")


class LinkMap:
    """Entries are kept under an ENTRY ID: the element's key when it first appeared, made unique
    with "#n" if a later, different element arrives under a key already taken. An entry's "key" is
    its LATEST key - counters can change between snapshots, the entry stays the same one."""

    def __init__(self, client: str, page: str, screen: Optional[int] = None, link: Optional[str] = None) -> None:
        self.client, self.page = client, page
        m = _SCREEN_RE.search(page)
        if screen is not None:
            self.screen = screen
        elif m:
            self.screen = int(m.group(1))
        else:
            self.screen = 1
        self.link = link if link is not None else _SCREEN_RE.sub("", page)
        self.entries: Dict[str, Dict[str, Any]] = {}
        self.children: Dict[Optional[str], List[str]] = {}
        self.reads: List[str] = []
        self.sessions: List[str] = []
        self.events: List[Dict[str, Any]] = []

    def _new_id(self, key: str) -> str:
        eid, n = key, 1
        while eid in self.entries:
            n += 1
            eid = f"{key}#{n}"
        return eid

    def known_keys(self) -> set:
        """Every key any entry was ever seen under."""
        return {k for ent in self.entries.values() for k in ent["keys"]}

    def add(self, rec: Dict[str, Any], stamp: str, session: str = "",
            precomputed: Optional[Tuple[List[Dict[str, Any]], Dict[int, int]]] = None) -> Dict[str, Any]:
        """Merge one read. Each element of the read is paired with an entry the map already has
        by ALIGNMENT (align.py: same shape + same text anchors in page order, the rest by shape
        between anchors) - not by key, so a re-render that renumbers a footer, or one more row
        above it, updates the entries the map has instead of storing them a second time (P16, R8).
        Only what pairs nothing is new. Returns {"new": the new entry ids, "blocks": their blocks}."""
        if precomputed is not None:
            flat, pairs = precomputed
            cur = self.to_flat() if (pairs and self.entries) else []
        else:
            flat = flatten(rec)
            cur = self.to_flat()
            pairs = pair_moved(flat, cur, align(flat, cur, _always), _always) if cur else {}
        if session and session not in self.sessions:
            self.sessions.append(session)
        ids: List[str] = []
        new_idx: set = set()
        for i, e in enumerate(flat):
            if i in pairs:
                ids.append(cur[pairs[i]]["id"])
            else:
                ids.append(self._new_id(e["key"]))
                new_idx.add(i)
                self.entries[ids[i]] = {"first": stamp, "values": [], "keys": set(), "history": []}  # reserves the id
        # This read's children of each parent, in order - to place a new entry among its siblings.
        read_children: Dict[Optional[str], List[str]] = {}
        for i, e in enumerate(flat):
            read_children.setdefault(ids[e["parent"]] if e["parent"] >= 0 else None, []).append(ids[i])
        last_child: Dict[Optional[str], str] = {}
        for i, e in enumerate(flat):
            k = ids[i]
            pk = ids[e["parent"]] if e["parent"] >= 0 else None
            ent = self.entries[k]
            if i in new_idx:                                   # a paired entry stays where it is
                ent["parent"] = pk
                sibs = self.children.setdefault(pk, [])
                prev = last_child.get(pk)
                if prev in sibs:                               # right after the sibling it followed
                    sibs.insert(sibs.index(prev) + 1, k)
                else:                                          # else before the next known sibling,
                    order = read_children.get(pk, [])          # else at the end (a popup that hid the
                    nxt = next((s for s in order[order.index(k) + 1:] if s in sibs), None)  # page)
                    sibs.insert(sibs.index(nxt) if nxt else len(sibs), k)
            last_child[pk] = k
            ent.update(key=e["key"], node=e["node"], cls=e["cls"], type=e["type"], last=stamp)
            ent["keys"].add(e["key"])
            if e["text"]:
                ent["text"] = e["text"]
                if e["text"] not in ent["values"] and len(ent["values"]) < MAX_VALUES:
                    ent["values"].append(e["text"])
                hist = ent.setdefault("history", [])
                if not hist or hist[-1][1] != e["text"]:
                    hist.append([stamp, e["text"]])
        new = {ids[i] for i in new_idx}
        blocks = _blocks(flat, new_idx)
        if self.reads and any(b["texts"] for b in blocks):
            self.events.append({"read": stamp, "new_nodes": len(new), "blocks": blocks})
        self.reads.append(stamp)
        return {"new": new, "blocks": blocks}

    def to_flat(self) -> List[Dict[str, Any]]:
        """The map in page order, in keys.flatten()'s shape (key, parent index, depth, cls, type,
        text, node) - plus id (the entry id), values, first, last, history."""
        out: List[Dict[str, Any]] = []

        def walk(pk: Optional[str], parent: int, depth: int) -> None:
            for k in self.children.get(pk, []):
                ent = self.entries[k]
                out.append({"id": k, "key": ent["key"], "parent": parent, "depth": depth, "cls": ent["cls"],
                            "type": ent["type"], "text": ent.get("text", ""), "node": ent["node"],
                            "values": ent["values"], "first": ent["first"], "last": ent["last"],
                            "history": ent.get("history", [])})
                walk(k, len(out) - 1, depth + 1)

        walk(None, -1, 0)
        return out


def _blocks(flat: List[Dict[str, Any]], idx: set) -> List[Dict[str, Any]]:
    """The nodes at indexes `idx`, grouped: a root (its parent not in `idx`) + all under it."""
    blocks: List[Dict[str, Any]] = []
    root_of: Dict[int, Dict[str, Any]] = {}
    for i, e in enumerate(flat):
        if i not in idx:
            continue
        p = e["parent"]
        b = root_of.get(p) if p >= 0 else None
        if b is None:
            b = {"root": e["key"], "nodes": 0, "texts": []}
            blocks.append(b)
        root_of[i] = b
        b["nodes"] += 1
        if e["text"]:
            b["texts"].append(e["text"])
    return blocks


def all_reads() -> List[Tuple[Path, Dict[str, Any]]]:
    return [(p, json.loads(p.read_text(encoding="utf-8"))) for p in sorted(OUT_DIR.glob("key_probe_*.json"))]


def all_sources() -> List[Tuple[str, str, str, Dict[str, Any]]]:
    """(stamp, session, page link, read) for every single read and every capture snapshot, in
    time order. A capture snapshot's stamp is its session time + seconds into the capture."""
    out: List[Tuple[str, str, str, Dict[str, Any]]] = []
    for path, rec in all_reads():
        out.append((read_stamp(path), client_of(rec, path), page_of(rec), rec))
    for path in sorted(OUT_DIR.glob("capture_*.json")):
        rec = json.loads(path.read_text(encoding="utf-8"))
        session = rec.get("session") or path.stem
        for snap in rec.get("snapshots") or []:
            out.append((f"{session}+{snap['t']:06.1f}", f"capture {session}", page_of(rec), {"docs": snap["docs"]}))
    return sorted(out, key=lambda x: x[0])


def build_maps(reads: Optional[List[Tuple[Path, Dict[str, Any]]]] = None,
               until: Optional[str] = None,
               client_of_session: Optional[Dict[str, str]] = None,
               sources: Optional[List[Tuple[str, str, str, Dict[str, Any]]]] = None,
               link_of: Optional[Dict[str, str]] = None) -> Dict[Tuple[str, str], LinkMap]:
    """Every (session, page link) map, merged in time order (only up to `until`, a stamp, when
    given). `reads`: only these single reads instead of everything in output/.
    `client_of_session` (group_clients): one map per (CLIENT, page link) instead - every session
    of one client merged into one map, so a client is one vote per page (R8).
    Candidate maps are the owner's maps of that link; weighted matching splits into screens (Part G).
    `link_of`: optional mapping from raw link to resolved link (Part N)."""
    from core.sdis.screens import covers, link_weights, same_screen

    if sources is not None:
        pass
    elif reads is not None:
        sources = [(read_stamp(p), client_of(r, p), page_of(r), r) for p, r in reads]
    else:
        sources = all_sources()
    if until is not None:
        sources = [s for s in sources if s[0] <= until]

    sources_for_weights = [(s[0], s[1], link_of.get(s[2], s[2]), s[3]) for s in sources] if link_of else sources
    weights = link_weights(sources_for_weights)
    maps: Dict[Tuple[str, str], LinkMap] = {}
    owner_link_maps: Dict[Tuple[str, str], List[LinkMap]] = {}

    for stamp, session, page, rec in sources:
        owner = (client_of_session or {}).get(session, session)
        resolved_page = link_of.get(page, page) if link_of else page
        flat = flatten(rec)
        candidates = owner_link_maps.get((owner, resolved_page), [])

        if not candidates:
            m = LinkMap(owner, resolved_page, screen=1, link=resolved_page)
            m.add(rec, stamp, session, precomputed=(flat, {}))
            owner_link_maps[(owner, resolved_page)] = [m]
            maps[(owner, resolved_page)] = m
            continue

        best_candidate = None
        best_max_cover = -1.0
        best_pairs = None

        for m in candidates:
            cur = m.to_flat()
            pairs = pair_moved(flat, cur, align(flat, cur, _always), _always) if cur else {}
            c_new, c_old = covers(flat, cur, pairs, weights)
            mc = max(c_new, c_old)
            if mc > best_max_cover:
                best_max_cover = mc
                best_candidate = m
                best_pairs = pairs

        if best_candidate is not None and same_screen(best_max_cover, 0.0):
            best_candidate.add(rec, stamp, session, precomputed=(flat, best_pairs))
        else:
            n = len(candidates) + 1
            page_key = f"{resolved_page} [screen {n}]"
            m = LinkMap(owner, page_key, screen=n, link=resolved_page)
            m.add(rec, stamp, session, precomputed=(flat, {}))
            candidates.append(m)
            maps[(owner, page_key)] = m

    return maps


from core.sdis.identity import _by_time, group_clients  # noqa: F401 re-exported



def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")[:40] or "client"


def main(argv: Optional[List[str]] = None) -> int:
    maps = build_maps()
    if not maps:
        raise SystemExit(f"No captures or reads in {OUT_DIR} yet - run key_probe.py on a page first.")
    for (client, page), m in sorted(maps.items(), key=lambda kv: kv[1].reads[-1]):
        flat = m.to_flat()
        print(f"{client!r:24s} {page[-50:]}")
        print(f"   reads {len(m.reads)}   nodes {len(flat)}   with text {sum(1 for e in flat if e['text'])}"
              f"   events {len(m.events)}")
        for ev in m.events:
            for b in ev["blocks"]:
                if b["texts"]:
                    shown = " | ".join(t[:30] for t in b["texts"][:BLOCK_TEXTS])
                    print(f"   read {ev['read']}: new block, {b['nodes']} nodes: {shown}")
        rows = [{"key": e["key"], "element": e["type"], "classes": e["cls"], "id": e["node"].get("id", ""),
                 "text": e["text"], "values_seen": " || ".join(e["values"]), "first_seen": e["first"],
                 "last_seen": e["last"]} for e in flat if e["text"]]
        out = write_csv(OUT_DIR / f"map_{_slug(client)}_{m.reads[-1]}.csv",
                        ["key", "element", "classes", "id", "text", "values_seen", "first_seen", "last_seen"], rows)
        print(f"   CSV {out.name}")
    return 0
