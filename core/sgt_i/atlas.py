"""
core/sgt_i/atlas.py - the atlas: each portal's structure, built visit by visit
===============================================================================
Blueprint 14.4 step 4 (the fingerprint idea). Every page map (page_map.py) is matched against the
pages already known for its portal; known structure is only counted, new parts are added.

* **Two files per portal.** `sgt_i/atlas/<portal>.json` is the atlas proper: structure, counts,
  masked shapes, dates - no client value, no hash (14.5 rule 7: this is what may sync).
  `sgt_i/atlas_private/<portal>.json` holds the salted hashes the atlas counts with (page tokens,
  word/client counts) and never leaves this PC (14.5 rule 3). Both written atomically (tmp +
  replace, like sessions_state.json). Losing the private file only means pages are re-learnt.
* **Same page?** By structure: the Jaccard overlap between this read's (role, label) tokens and a
  known page's core tokens (those seen on at least `core_share` of its visits). A matching address
  path is only a hint - it lowers the bar, never decides alone (single-page apps reuse addresses).
* **Lists collapse.** Tokens are a set, so 3 cards and 14 cards are one page; a label that repeats
  on one read is one element with `repeats`, a table column one slot with `repeating`/`max_rows`.
* **Template promotion.** A word of page text enters the atlas in the clear only once it has been
  seen at the same place (page, role, zone, word position) for >= `template_min_clients`
  different clients AND for at least `template_share` of the page's clients; until then only its
  masked shape is stored. So "Welcome, RAVI MEHTA" becomes `Welcome, «text»` (the slot is named by
  its generic type - the atlas cannot know it is a name). A pair's value never enters at all:
  only its container, generic type and masked shape (slots).
* **Optional regions.** Section headings and a dialog's title are regions with how often they
  appeared; `optional` when seen on fewer visits than the page.
* **Ageing.** Everything carries first_seen / last_seen. A part not seen for `fade_days` of its
  page's visits is `fading`; after `retire_days` it moves to the page's `retired` list (a redesign
  then reads "Date of Birth" retired, "DOB" first seen). A page unvisited for `retire_days`
  retires to `retired_pages`.
* **Transitions** between consecutive pages of one session are counted (step 5 reads them).

Written rule: the Core never needs the atlas - nothing in core/sgt imports it; an empty or corrupt
atlas is simply an empty one. Rules and counting only (no AI).
"""

import json
import re
import secrets
import time
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from . import page_map as pm
from .pairs import classify_type, mask_shape
from .stats import load_or_create_salt, salted_hash, sgt_i_dir

__all__ = ["DEFAULT_CONFIG", "atlas_dir", "url_hint", "PortalAtlas", "Atlas", "AtlasComponent"]

MAX_MERGE_LINES = 400                # the ledger's bound: a huge table is not worth mapping per read

ATLAS_DIR = "atlas"                  # public: structure, counts, shapes (may sync)
PRIVATE_DIR = "atlas_private"        # salted hashes (never leaves this PC)
FORMAT = 1
SEP = " › "

DEFAULT_CONFIG: Dict[str, Any] = {
    "template_min_clients": 3,       # 14.4 step 4: "identical for several different clients"
    "template_share": 0.6,           # ...and for most of the page's clients (a shared surname is not)
    "match_threshold": 0.5,          # Jaccard needed to call a read a known page
    "url_match_threshold": 0.1,      # ...when the address (with its #/route) is one the page is known by (measured, 2026-09-28)
    "url_mismatch_threshold": 0.8,   # ...when both have addresses and they differ
    "min_new_page_tokens": 10,       # fewer, at an address already known = still loading
    "core_share": 0.5,               # a token is part of a page's fingerprint on >= half its visits
    "fade_days": 30,
    "retire_days": 90,
    "max_pages": 500,
    "max_elements": 400,             # per page
    "max_slots": 300,                # per page
    "max_words": 4096,               # per page, private word counters
    "max_url_hints": 5,
    "max_shapes": 5,
    "max_retired": 50,
    "save_every_sec": 30.0,
    # Portal furniture (menus, headers, footers the markup did not label) - see furniture_test().
    "furniture_min_known_pages": 10, # below this the portal is too new to tell furniture apart
    "furniture_min_pages": 5,        # a text on at least this many different pages...
    "furniture_read_share": 0.4,     # ...and in at least this share of the portal's page reads
    "furniture_min_clients": 2,      # ...seen for 2+ clients (one client's name on every page is not)
    "furniture_min_run": 3,          # only a run of such texts is furniture; a lone label is not
    "max_texts": 6000,               # per portal, private text counters
}
FURNITURE_MAX_LEN = 120              # longer text is a sentence, not a menu item or header strip

MAX_CLIENTS = 1024                   # client hashes kept per page
WORD_CLIENT_CAP = 64                 # client hashes kept per word counter
MAX_SESSIONS = 64                    # sessions remembered for transitions (memory only)
SHAPE_MAX_LEN = 40

_SKIP_ZONES = frozenset({pm.NAVIGATION, pm.FOOTER, pm.HELP})
_NO_FINGERPRINT = frozenset({pm.DIALOG, pm.HEADER})     # optional / the same on every page
_STRUCTURAL_ROLES = frozenset({"columnheader", "rowheader", "button", "tabitem", "link",
                               "menuitem", "checkbox"})

Element = Tuple[str, str, str, bool, str]     # role, zone, text, structural, region


def atlas_dir() -> Path:
    return sgt_i_dir() / ATLAS_DIR


def _file_name(portal: str) -> str:
    return (re.sub(r"[^a-z0-9._-]+", "_", (portal or "").strip().lower()) or "_") + ".json"


def _mask_path(path: str) -> str:
    segs = [mask_shape(s) if any(c.isdigit() for c in s) else s for s in path.split("/")]
    return "/".join(segs).rstrip("/")


def url_hint(url: str) -> str:
    """The address path - no query - with any segment holding a digit masked, so an id in the path
    never enters the atlas. A single-page app's route after '#/' (or '#!/') IS the page's address
    ("eportal.../foservices/#/dashboard/itrStatus"), so it is kept, masked the same way; any other
    fragment ("#top") is not. Without the route every screen of such a portal shared one hint and
    the lower url_match_threshold merged different screens (field test 2026-09-28: 12 in one)."""
    try:
        parsed = urlparse(url or "")
    except ValueError:
        return ""
    path = parsed.path
    out = _mask_path(path) or ("/" if path else "")
    frag = (parsed.fragment or "").split("?")[0]
    if frag.startswith("!"):
        frag = frag[1:]
    if frag.startswith("/"):
        route = _mask_path(frag)
        if route:
            out = (out if out != "/" else "") + "#" + route
    return out


def _elements(page: pm.PageMap) -> List[Element]:
    """Every piece of page text the atlas may learn from: headings, pair labels, controls and
    free text. A pair's value is never one of them."""
    values = {p.value_node for p in page.pairs}
    labels = {p.label_node: p.label for p in page.pairs}
    out: List[Element] = []
    dialog_titled = False
    for n in page.nodes:
        if n.index in values or n.zone in _SKIP_ZONES:
            continue
        if n.index in labels:
            role, text = "label", labels[n.index]
        elif n.heading:
            role, text = "heading", n.text
        elif n.role in _STRUCTURAL_ROLES:
            role, text = n.role, n.text
        elif n.is_content:
            role, text = "text", n.text
        else:
            continue
        text = " ".join(text.split())
        if not text:
            continue
        region = ""
        if n.zone == pm.DIALOG and not dialog_titled:
            region, dialog_titled = "dialog", True
        elif role == "heading":
            region = "section"
        out.append((role, n.zone, text, role != "text", region))
    return out


def _empty(portal: str) -> Dict[str, Any]:
    return {"portal": portal, "format": FORMAT, "version": 0, "updated": "",
            "pages": {}, "transitions": [], "retired_pages": []}


def _new_private_page() -> Dict[str, Any]:
    return {"tokens": {}, "clients": [], "words": {}, "keys": {}, "slot_keys": {}, "urls": {}}


def _days(a: str, b: str) -> int:
    try:
        return (date.fromisoformat(b) - date.fromisoformat(a)).days
    except (TypeError, ValueError):
        return 0


class PortalAtlas:
    """One portal's atlas. `merge()` one page map at a time; `save()` (debounced by `_touch`)
    writes both files. Pass `directory=` in tests so nothing real is touched; `stats=` (a
    stats.ContainerStats) lets slots carry their container kind."""

    def __init__(self, portal: str, directory: Optional[Path] = None,
                 config: Optional[Dict[str, Any]] = None, clock=None, stats=None,
                 salt: Optional[str] = None) -> None:
        self.portal = portal
        self._dir = directory
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self._clock = clock or time.time
        self._stats = stats
        self._salt = salt
        self.data: Dict[str, Any] = _empty(portal)
        self.private: Dict[str, Any] = {"pages": {}}
        self._last_page: Dict[str, str] = {}
        self._counted: Dict[str, Set[Any]] = {}     # session -> what this page visit already counted
        self._dirty = False
        self._last_save = 0.0
        self._furniture: Optional[frozenset] = None
        self._load()

    # ── paths, hashing ──────────────────────────────────────────────────────────
    @property
    def base(self) -> Path:
        return self._dir or sgt_i_dir()

    @property
    def path(self) -> Path:
        return self.base / ATLAS_DIR / _file_name(self.portal)

    @property
    def private_path(self) -> Path:
        return self.base / PRIVATE_DIR / _file_name(self.portal)

    @property
    def salt(self) -> str:
        if self._salt is None:
            self._salt = load_or_create_salt(self.base)
        return self._salt

    def _h(self, domain: str, *parts: str) -> str:
        return salted_hash("\0".join(parts), self.salt, domain)

    def _today(self) -> str:
        return date.fromtimestamp(self._clock()).isoformat()

    @property
    def pages(self) -> Dict[str, Dict[str, Any]]:
        return self.data["pages"]

    # ── merge ───────────────────────────────────────────────────────────────────
    def merge(self, page: pm.PageMap, url: str = "", client: Optional[str] = None,
              session: Optional[str] = None) -> Optional[str]:
        """Fold one page read into the atlas and return its page id (None when the read has
        nothing to recognise a page by, or the atlas is full). `client` (a PAN, a session key -
        anything that tells clients apart) is only ever hashed; without it text is never promoted.
        `session` links consecutive reads for transition counts; it is never stored."""
        cfg = self.config
        today = self._today()
        elements = _elements(page)
        hint = url_hint(url)
        tokens = {self._h("atlas-el", r, z, t) for r, z, t, structural, _ in elements
                  if structural and z not in _NO_FINGERPRINT}
        pid = self._match(tokens, hint)
        if pid is None:
            # A thin read at an address the atlas already knows is that screen still loading (a
            # spinner, half the page): it never founds a page of its own - it would be a junk page.
            # Measured on the 2026-09-28 corpus: with this, 18 screens -> 26 pages (was 46).
            thin = len(tokens) < cfg["min_new_page_tokens"] and bool(hint) and any(
                hint in (p.get("urls") or {}) for p in self.private["pages"].values())
            if not tokens or thin or len(self.pages) >= cfg["max_pages"]:
                return None
            pid = self._new_page(today)
        pub, priv = self.pages[pid], self.private["pages"].setdefault(pid, _new_private_page())
        # A VISIT is arriving on a page; the Core hands over a new read whenever any line changes,
        # so one visit can be dozens of reads. Counts are per visit: a re-read only adds what this
        # visit had not shown yet (a dialog, a new value).
        counted = self._visit(session, pid)
        if not counted:
            pub["visits"] += 1
            if hint:
                priv["urls"][hint] = priv["urls"].get(hint, 0) + 1
        pub["last_seen"] = today
        for t in tokens:
            if ("t", t) not in counted:
                counted.add(("t", t))
                priv["tokens"][t] = priv["tokens"].get(t, 0) + 1
        if hint:
            pub["url_hints"] = sorted(priv["urls"], key=lambda u: -priv["urls"][u])[:cfg["max_url_hints"]]
        client_hash = self._h("atlas-client", client) if client else None
        if client_hash and client_hash not in priv["clients"] and len(priv["clients"]) < MAX_CLIENTS:
            priv["clients"].append(client_hash)
            pub["clients"] += 1
        self._count_texts(page, pid, client_hash, today)
        self._count_words(priv, elements, client_hash)
        self._merge_elements(pub, priv, elements, today, counted)
        self._merge_slots(pub, priv, page, today, client, counted)
        self._transition(session, pid, today)
        self._touch()
        return pid

    def _visit(self, session: Optional[str], pid: str) -> Set[Any]:
        """What the current visit to `pid` has already counted - empty on a new visit (a different
        page than this session's last read, or no session at all). Memory only."""
        if session is None:
            return set()
        if self._last_page.get(session) != pid:
            self._counted.pop(session, None)
        counted = self._counted.setdefault(session, set())
        while len(self._counted) > MAX_SESSIONS:
            self._counted.pop(next(iter(self._counted)))
        return counted

    def _match(self, tokens: Set[str], hint: str) -> Optional[str]:
        cfg = self.config
        best, best_score = None, -1.0
        for pid, pub in self.pages.items():
            priv = self.private["pages"].get(pid) or {}
            urls = priv.get("urls", {})
            known_url = bool(hint) and hint in urls
            if known_url:
                need = cfg["url_match_threshold"]
            elif hint and urls:
                # Both have an address and they differ (a route is kept, so '#/itrStatus' vs
                # '#/fileIncomeTaxReturn'): only a near-identical structure is the same page.
                need = cfg["url_mismatch_threshold"]
            else:
                need = cfg["match_threshold"]
            floor = cfg["core_share"] * max(pub["visits"], 1)
            core = {t for t, n in priv.get("tokens", {}).items() if n >= floor}
            union = tokens | core
            score = len(tokens & core) / len(union) if union and tokens else 0.0
            if score >= need and score + (0.01 if known_url else 0) > best_score:
                best, best_score = pid, score + (0.01 if known_url else 0)
        return best

    def _new_page(self, today: str) -> str:
        pid = "p-" + secrets.token_hex(2)
        while pid in self.pages:
            pid = "p-" + secrets.token_hex(3)
        self.pages[pid] = {"id": pid, "url_hints": [], "first_seen": today, "last_seen": today,
                           "visits": 0, "clients": 0, "elements": {}, "slots": {},
                           "retired": [], "next": 1}
        self.private["pages"][pid] = _new_private_page()
        return pid

    # ── furniture ──────────────────────────────────────────────────────────────
    def _count_texts(self, page: pm.PageMap, pid: str, client_hash: Optional[str], today: str) -> None:
        """On how many different pages, for how many clients, each text appears (salted hashes,
        private file). Dialog and stepper text is a message or progress, never furniture. A text
        seen for enough clients to be template text also keeps its words, for the lab to show -
        unless it was ever a pair's VALUE: a value never enters the atlas in the clear (14.5), so
        such a text is counted by hash and shown only as its masked shape."""
        cfg = self.config
        texts = self.private.setdefault("texts", {})
        keep_clients = max(cfg["furniture_min_clients"], cfg["template_min_clients"])
        done: Set[str] = set()
        values = {p.value_node for p in page.pairs}
        self.private["reads"] = int(self.private.get("reads") or 0) + 1
        for n in page.nodes:
            t = " ".join(n.text.split())
            if not t or len(t) > FURNITURE_MAX_LEN or n.zone in (pm.DIALOG, pm.STEPPER):
                continue
            h = self._h("atlas-text", t)
            if h in done:
                continue
            done.add(h)
            e = texts.get(h)
            if e is None:
                if len(texts) >= cfg["max_texts"]:
                    for dead in [k for k, v in texts.items() if len(v["p"]) <= 1]:
                        del texts[dead]                   # one-page texts: never furniture yet
                    if len(texts) >= cfg["max_texts"]:
                        continue
                e = texts[h] = {"p": [], "c": [], "n": 0, "last": today, "s": mask_shape(t)[:SHAPE_MAX_LEN]}
            e["n"] = int(e.get("n") or 0) + 1
            if pid not in e["p"]:
                e["p"].append(pid)
            if client_hash and client_hash not in e["c"] and len(e["c"]) < keep_clients:
                e["c"].append(client_hash)
            if n.index in values:
                e["v"] = True                             # a pair's value: never kept in the clear
                e.pop("t", None)
            elif len(e["c"]) >= cfg["template_min_clients"] and not e.get("v"):
                e["t"] = t                                # identical for 3+ clients: template text
            e["last"] = today
        self._furniture = None

    def _furniture_entries(self) -> List[Dict[str, Any]]:
        cfg = self.config
        if len(self.pages) < cfg["furniture_min_known_pages"]:
            return []
        # A share of READS, not of pages: how finely the atlas splits a portal into pages must not
        # decide it (plain-line reads split one screen into several pages). A menu is on nearly
        # every read; a data label only on its own screens.
        need_reads = cfg["furniture_read_share"] * int(self.private.get("reads") or 0)
        today = self._today()
        out = []
        for h, e in (self.private.get("texts") or {}).items():
            pages = sum(1 for p in e["p"] if p in self.pages)
            if pages >= cfg["furniture_min_pages"] and int(e.get("n") or 0) >= need_reads \
                    and len(e["c"]) >= cfg["furniture_min_clients"] \
                    and _days(e["last"], today) <= cfg["fade_days"]:
                out.append({"hash": h, "pages": pages, "clients": len(e["c"]),
                            "text": e.get("t") or "", "shape": e.get("s", ""), "last_seen": e["last"]})
        return out

    def furniture_test(self) -> Optional[Callable[[str], bool]]:
        """Whether a text is one this portal shows on many different pages, or None while the
        portal is too new to tell (then nothing is furniture - exactly the behaviour before).
        page_map.mark_furniture only acts on RUNS of such texts."""
        if self._furniture is None:
            self._furniture = frozenset(e["hash"] for e in self._furniture_entries())
        hashes = self._furniture
        if not hashes:
            return None
        return lambda text: self._h("atlas-text", " ".join(text.split())) in hashes

    def furniture(self) -> List[Dict[str, Any]]:
        """What this portal treats as furniture-shaped, for the lab and the atlas tool: the text
        once it is template (identical for 3+ clients, never a pair's value), else its masked shape.
        Only RUNS of these are furniture on a page (page_map.mark_furniture)."""
        return sorted(({k: v for k, v in e.items() if k != "hash"} for e in self._furniture_entries()),
                      key=lambda e: (-e["pages"], e["text"] or e["shape"]))

    # ── template promotion ─────────────────────────────────────────────────────
    def _word_key(self, role: str, zone: str, i: int, word: str) -> str:
        return self._h("atlas-word", role, zone, str(i), word)

    def _count_words(self, priv: Dict[str, Any], elements: List[Element],
                     client_hash: Optional[str]) -> None:
        if not client_hash:
            return
        words = priv["words"]
        for role, zone, text, _, _ in elements:
            for i, w in enumerate(text.split()):
                k = self._word_key(role, zone, i, w)
                lst = words.get(k)
                if lst is None:
                    if len(words) >= self.config["max_words"]:
                        for dead in [key for key, v in words.items() if len(v) <= 1]:
                            del words[dead]            # one-client words: never template yet
                        if len(words) >= self.config["max_words"]:
                            continue
                    lst = words[k] = []
                if client_hash not in lst and len(lst) < WORD_CLIENT_CAP:
                    lst.append(client_hash)

    def _promoted(self, priv: Dict[str, Any], role: str, zone: str, i: int, word: str) -> bool:
        n = len(priv["words"].get(self._word_key(role, zone, i, word), ()))
        page_clients = min(len(priv["clients"]), WORD_CLIENT_CAP)
        return n >= self.config["template_min_clients"] and n >= self.config["template_share"] * page_clients

    def _render(self, priv: Dict[str, Any], role: str, zone: str, text: str) -> Tuple[str, bool]:
        """(what the atlas may store for this text, whether any word of it is template)."""
        words = text.split()
        ok = [self._promoted(priv, role, zone, i, w) for i, w in enumerate(words)]
        if all(ok):
            return " ".join(words), True
        if not any(ok):
            return mask_shape(" ".join(words)), False
        out: List[str] = []
        run: List[str] = []
        for w, good in zip(words, ok):
            if good:
                if run:
                    out.append("«%s»" % classify_type(" ".join(run)))
                    run = []
                out.append(w)
            else:
                run.append(w)
        if run:
            out.append("«%s»" % classify_type(" ".join(run)))
        return " ".join(out), True

    # ── elements, slots, transitions ───────────────────────────────────────────
    def _item(self, pub: Dict[str, Any], keys: Dict[str, str], bucket: str, key: str,
              today: str, cap: int) -> Optional[Dict[str, Any]]:
        xid = keys.get(key)
        item = pub[bucket].get(xid) if xid else None
        if item is None:
            if len(pub[bucket]) >= cap:
                return None
            xid = "%s%d" % (bucket[0], pub["next"])
            pub["next"] += 1
            item = pub[bucket][xid] = {"first_seen": today, "last_seen": today, "seen": 0}
            keys[key] = xid
        return item

    def _merge_elements(self, pub, priv, elements: List[Element], today: str,
                        counted: Optional[Set[Any]] = None) -> None:
        counted = counted if counted is not None else set()
        counts: Dict[int, Tuple[Dict[str, Any], int]] = {}
        for role, zone, text, structural, region in elements:
            render, template = self._render(priv, role, zone, text)
            if structural:
                key = self._h("atlas-el", role, zone, text)
            elif template:
                key = "r|%s|%s|%s" % (role, zone, render)   # "Welcome, «text»" once for all clients
            else:
                continue                                      # plain unpaired text: not structure
            el = self._item(pub, priv["keys"], "elements", key, today, self.config["max_elements"])
            if el is None:
                continue
            el.update(role=role, zone=zone, text=render)
            if structural:
                el["structural"] = True
            if region:
                el["region"] = region
            prev = counts.get(id(el))
            counts[id(el)] = (el, prev[1] + 1 if prev else 1)
        for el, n in counts.values():
            if ("e", id(el)) not in counted:
                counted.add(("e", id(el)))
                el["seen"] += 1
            el["last_seen"] = today
            if n > 1:
                el["repeats"] = max(el.get("repeats", 1), n)

    def _merge_slots(self, pub, priv, page: pm.PageMap, today: str, client: Optional[str],
                     counted: Optional[Set[Any]] = None) -> None:
        cfg = self.config
        counted = counted if counted is not None else set()
        visit: Dict[int, Tuple[Dict[str, Any], Set[Any]]] = {}
        for p in page.pairs:
            if p.zone in _SKIP_ZONES:
                continue
            texts = [" ".join(t.split()) for t in page.section_path(p.section) + (p.label,)]
            roles = ["heading"] * (len(texts) - 1) + ["label"]
            raw = SEP.join(texts)
            key = self._h("atlas-slot", p.zone, raw)
            slot = self._item(pub, priv["slot_keys"], "slots", key, today, cfg["max_slots"])
            if slot is None:
                continue
            if client:
                # Support for the miner (step 11): how many different clients filled this slot.
                seen_by = priv.setdefault("slot_clients", {}).setdefault(priv["slot_keys"][key], [])
                ch = self._h("atlas-client", client)
                if ch not in seen_by and len(seen_by) < WORD_CLIENT_CAP:
                    seen_by.append(ch)
                slot["clients"] = len(seen_by)
            slot["container"] = SEP.join(self._render(priv, r, p.zone, t)[0] for r, t in zip(roles, texts))
            slot["zone"] = p.zone
            entry = visit.setdefault(id(slot), (slot, set()))
            entry[1].add(p.row)
            sighting = ("v", id(slot), p.row, p.value)    # the value lives in memory only, per visit
            if sighting in counted:
                continue                                  # a re-read of the same value: counted once
            counted.add(sighting)
            types = slot.setdefault("types", {})
            typ = classify_type(p.value, p.method)
            types[typ] = types.get(typ, 0) + 1
            slot["type"] = max(types, key=types.get)
            shape = mask_shape(" ".join(p.value.split()))
            if len(shape) > SHAPE_MAX_LEN:
                shape = shape[:SHAPE_MAX_LEN] + "…"
            shapes = slot.setdefault("shapes", {})
            if shape in shapes or len(shapes) < cfg["max_shapes"]:
                shapes[shape] = shapes.get(shape, 0) + 1
            else:
                slot["other_shapes"] = slot.get("other_shapes", 0) + 1
            if self._stats is not None:
                self._stats.observe(raw, p.value, client)
                kind = self._stats.classify(raw)
                if kind is not None and kind.kind:
                    slot["kind"] = kind.kind
        for slot, rows in visit.values():
            if ("s", id(slot)) not in counted:
                counted.add(("s", id(slot)))
                slot["seen"] += 1
            slot["last_seen"] = today
            n = len(rows - {None})
            if n:
                slot["repeating"] = True
                slot["max_rows"] = max(slot.get("max_rows", 0), n)

    def _transition(self, session: Optional[str], pid: str, today: str) -> None:
        if session is None:
            return
        prev = self._last_page.pop(session, None)
        self._last_page[session] = pid
        while len(self._last_page) > MAX_SESSIONS:
            self._last_page.pop(next(iter(self._last_page)))
        if prev is None or prev == pid or prev not in self.pages:
            return
        for tr in self.data["transitions"]:
            if tr["from"] == prev and tr["to"] == pid:
                tr["count"] += 1
                tr["last_seen"] = today
                return
        self.data["transitions"].append({"from": prev, "to": pid, "count": 1,
                                         "first_seen": today, "last_seen": today})

    # ── ageing ─────────────────────────────────────────────────────────────────
    def age(self, today: Optional[str] = None) -> None:
        """Fade and retire what has not been seen. A page's parts age against the page's own last
        visit (only a visit can show a part is gone); pages age against today."""
        cfg = self.config
        today = today or self._today()
        texts = self.private.get("texts") or {}
        for h in [h for h, e in texts.items() if _days(e["last"], today) > cfg["retire_days"]]:
            del texts[h]                                  # furniture a redesign removed
            self._furniture = None
        for pid in list(self.pages):
            pub = self.pages[pid]
            if _days(pub["last_seen"], today) > cfg["retire_days"]:
                self.data["retired_pages"].append({
                    "id": pid, "fingerprint": self._fingerprint(pub),
                    "first_seen": pub["first_seen"], "last_seen": pub["last_seen"],
                    "visits": pub["visits"]})
                del self.data["retired_pages"][:-cfg["max_retired"]]
                del self.pages[pid]
                self.private["pages"].pop(pid, None)
                self.data["transitions"] = [t for t in self.data["transitions"]
                                            if pid not in (t["from"], t["to"])]
                self._dirty = True
                continue
            pub["fading"] = _days(pub["last_seen"], today) > cfg["fade_days"]
            priv = self.private["pages"].get(pid) or _new_private_page()
            for bucket, keys, label in (("elements", "keys", "text"), ("slots", "slot_keys", "container")):
                for xid in list(pub[bucket]):
                    item = pub[bucket][xid]
                    gap = _days(item["last_seen"], pub["last_seen"])
                    if gap > cfg["retire_days"]:
                        pub["retired"].append({"was": bucket[:-1], label: item.get(label, ""),
                                               "first_seen": item["first_seen"],
                                               "last_seen": item["last_seen"]})
                        del pub["retired"][:-cfg["max_retired"]]
                        del pub[bucket][xid]
                        for k in [k for k, v in priv.get(keys, {}).items() if v == xid]:
                            del priv[keys][k]
                        self._dirty = True
                    elif gap > cfg["fade_days"]:
                        item["fading"] = True
                    else:
                        item.pop("fading", None)

    # ── views ──────────────────────────────────────────────────────────────────
    def _fingerprint(self, pub: Dict[str, Any]) -> List[str]:
        floor = self.config["core_share"] * max(pub["visits"], 1)
        return sorted({"%s:%s" % (e["role"], e["text"]) for e in pub["elements"].values()
                       if e.get("structural") and e["zone"] not in _NO_FINGERPRINT
                       and e["seen"] >= floor})

    def _regions(self, pub: Dict[str, Any]) -> List[Dict[str, Any]]:
        out = []
        for e in pub["elements"].values():
            if e.get("region"):
                out.append({"role": e["region"], "label": e["text"],
                            "optional": e["seen"] < pub["visits"], "seen": e["seen"]})
            if e.get("repeats"):
                out.append({"role": "repeating", "label": "%s:%s" % (e["role"], e["text"]),
                            "max": e["repeats"], "seen": e["seen"]})
        return out

    def to_json(self) -> Dict[str, Any]:
        """The public file: pages as a list, with fingerprint and regions as derived views."""
        pages = []
        for pub in self.pages.values():
            view = {"id": pub["id"], "fingerprint": self._fingerprint(pub)}
            view.update({k: v for k, v in pub.items() if k != "id"})
            view["regions"] = self._regions(pub)
            pages.append(view)
        out = {k: v for k, v in self.data.items() if k != "pages"}
        out["pages"] = pages
        out["furniture"] = self.furniture()
        return out

    # ── persistence ────────────────────────────────────────────────────────────
    def _touch(self) -> None:
        self._dirty = True
        if self._clock() - self._last_save >= self.config["save_every_sec"]:
            self.save()

    def save(self) -> None:
        if not self._dirty:
            return
        self.age()
        self.data["version"] += 1
        self.data["updated"] = self._today()
        try:
            _write_atomic(self.path, self.to_json())
            _write_atomic(self.private_path, self.private)
            self._dirty = False
            self._last_save = self._clock()
        except OSError:
            pass

    def _load(self) -> None:
        loaded = _read_json(self.path)
        if loaded is None:
            return
        try:
            pages = {}
            for p in loaded["pages"]:
                p = dict(p)
                p.pop("fingerprint", None)
                p.pop("regions", None)
                for key in ("url_hints", "retired"):
                    p.setdefault(key, [])
                for key in ("elements", "slots"):
                    p.setdefault(key, {})
                p.setdefault("next", len(p["elements"]) + len(p["slots"]) + 1)
                pages[p["id"]] = p
            data = _empty(self.portal)
            data.update({k: loaded[k] for k in ("version", "transitions", "retired_pages") if k in loaded})
            data["pages"] = pages
            self.data = data
        except (KeyError, TypeError, AttributeError):
            self._set_aside(self.path)
            return
        private = _read_json(self.private_path)
        if isinstance(private, dict) and isinstance(private.get("pages"), dict):
            self.private = {"pages": {pid: {**_new_private_page(), **v}
                                      for pid, v in private["pages"].items() if pid in self.pages}}
            if isinstance(private.get("texts"), dict):
                self.private["texts"] = private["texts"]
                self.private["reads"] = int(private.get("reads") or 0)

    def _set_aside(self, path: Path) -> None:
        """A corrupt atlas is kept for inspection and replaced by an empty one."""
        self.data = _empty(self.portal)
        try:
            path.replace(path.with_suffix(".corrupt"))
        except OSError:
            pass


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    except ValueError:
        try:
            path.replace(path.with_suffix(".corrupt"))
        except OSError:
            pass
        return None
    return loaded if isinstance(loaded, dict) else None


def _write_atomic(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def load_config() -> Dict[str, Any]:
    """The "atlas" section of sgt_i_config.json (tunable thresholds, e.g. the furniture ones);
    {} when missing or unreadable - DEFAULT_CONFIG then stands."""
    try:
        loaded = json.loads(Path(__file__).with_name("sgt_i_config.json").read_text(encoding="utf-8"))
        section = loaded.get("atlas") if isinstance(loaded, dict) else None
        return {k: v for k, v in section.items() if k in DEFAULT_CONFIG} if isinstance(section, dict) else {}
    except (OSError, ValueError):
        return {}


class Atlas:
    """Every portal's atlas, opened on first use."""

    def __init__(self, directory: Optional[Path] = None, config: Optional[Dict[str, Any]] = None,
                 clock=None, stats=None) -> None:
        self._dir = directory
        self._config = config if config is not None else load_config()
        self._clock = clock
        self._stats = stats
        self._portals: Dict[str, PortalAtlas] = {}

    def portal(self, name: str) -> PortalAtlas:
        key = (name or "").strip().lower()
        if key not in self._portals:
            self._portals[key] = PortalAtlas(key, self._dir, self._config, self._clock, self._stats)
        return self._portals[key]

    def merge(self, portal: str, page: pm.PageMap, **kw) -> Optional[str]:
        return self.portal(portal).merge(page, **kw)

    def save(self) -> None:
        for a in self._portals.values():
            a.save()


class AtlasComponent:
    """The step-4 SGT-I component: every page the Core read is folded into its portal's atlas -
    each read captures part of the portal. Shares one `Atlas` with the GPS, so a page merged here
    is known to the GPS on its next visit. The client key is the session's identity (the ledger's
    `client_fields`: PAN, GSTIN...), only ever hashed; with none, text is never promoted. The page
    map is page_view's: the node tree when the Core read one (the markup's navigation and footer
    left out), else the lines. Learnt furniture is counted here but not left out here - the other
    components leave it out; page identity must not shift as the furniture is learnt."""

    name = "atlas"

    def __init__(self, atlas: Optional[Atlas] = None, client_fields: Optional[Tuple[str, ...]] = None) -> None:
        if atlas is None:
            # The container stats give each slot its kind (profile / dataset / identifier). Without
            # them every slot stays kind-less and the miner drops them all as "kind unknown" - the
            # live atlas ran that way until 2026-09-28 (found in the field test, 974 slots, 0 kinds).
            from .stats import ContainerStats
            atlas = Atlas(stats=ContainerStats())
        self.atlas = atlas
        if client_fields is None:
            from .ledger import load_config
            client_fields = tuple(load_config().get("client_fields") or ())
        self._client_fields = client_fields

    def observe(self, obs: Any, ctx: Any) -> None:
        if getattr(obs, "event", "") or obs.source == "uia_event":
            return            # a message that flashed (step 9) is part of a page, not a page
        from .page_view import page_for
        profile = dict(obs.profile)
        client = next((f"{f}:{profile[f]}" for f in self._client_fields if profile.get(f)), None)
        # The atlas maps the page as read - learnt furniture NOT left out: which page this is must
        # not change as the atlas learns what its furniture is (measured on the recorded corpus:
        # leaving it out split 13 pages into 23). The markup's own navigation / footer still is.
        page = page_for(obs, None, max_lines=MAX_MERGE_LINES)
        if page is not None:
            self.atlas.merge(obs.portal, page, url=obs.url, client=client, session=obs.session_id)
