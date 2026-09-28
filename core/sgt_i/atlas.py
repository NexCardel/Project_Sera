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
from typing import Any, Dict, List, Optional, Set, Tuple
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
    "url_match_threshold": 0.3,      # ...when the address path is one the page is known by
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
}

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


def url_hint(url: str) -> str:
    """The address path only - no query, no fragment - with any segment holding a digit masked,
    so an id in the path never enters the atlas."""
    try:
        path = urlparse(url or "").path
    except ValueError:
        return ""
    segs = [mask_shape(s) if any(c.isdigit() for c in s) else s for s in path.split("/")]
    return "/".join(segs).rstrip("/") or ("/" if path else "")


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
        self._dirty = False
        self._last_save = 0.0
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
            if not tokens or len(self.pages) >= cfg["max_pages"]:
                return None
            pid = self._new_page(today)
        pub, priv = self.pages[pid], self.private["pages"].setdefault(pid, _new_private_page())
        pub["visits"] += 1
        pub["last_seen"] = today
        for t in tokens:
            priv["tokens"][t] = priv["tokens"].get(t, 0) + 1
        if hint:
            priv["urls"][hint] = priv["urls"].get(hint, 0) + 1
            pub["url_hints"] = sorted(priv["urls"], key=lambda u: -priv["urls"][u])[:cfg["max_url_hints"]]
        client_hash = self._h("atlas-client", client) if client else None
        if client_hash and client_hash not in priv["clients"] and len(priv["clients"]) < MAX_CLIENTS:
            priv["clients"].append(client_hash)
            pub["clients"] += 1
        self._count_words(priv, elements, client_hash)
        self._merge_elements(pub, priv, elements, today)
        self._merge_slots(pub, priv, page, today, client)
        self._transition(session, pid, today)
        self._touch()
        return pid

    def _match(self, tokens: Set[str], hint: str) -> Optional[str]:
        cfg = self.config
        best, best_score = None, -1.0
        for pid, pub in self.pages.items():
            priv = self.private["pages"].get(pid) or {}
            known_url = bool(hint) and hint in priv.get("urls", {})
            need = cfg["url_match_threshold"] if known_url else cfg["match_threshold"]
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

    def _merge_elements(self, pub, priv, elements: List[Element], today: str) -> None:
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
            el["seen"] += 1
            el["last_seen"] = today
            if n > 1:
                el["repeats"] = max(el.get("repeats", 1), n)

    def _merge_slots(self, pub, priv, page: pm.PageMap, today: str, client: Optional[str]) -> None:
        cfg = self.config
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
            entry = visit.setdefault(id(slot), (slot, set()))
            entry[1].add(p.row)
        for slot, rows in visit.values():
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


class Atlas:
    """Every portal's atlas, opened on first use."""

    def __init__(self, directory: Optional[Path] = None, config: Optional[Dict[str, Any]] = None,
                 clock=None, stats=None) -> None:
        self._dir = directory
        self._config = config
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
    map is built from the Core's lines (no nodes in an Observation yet), as the ledger does."""

    name = "atlas"

    def __init__(self, atlas: Optional[Atlas] = None, client_fields: Optional[Tuple[str, ...]] = None) -> None:
        self.atlas = atlas if atlas is not None else Atlas()
        if client_fields is None:
            from .ledger import load_config
            client_fields = tuple(load_config().get("client_fields") or ())
        self._client_fields = client_fields

    def observe(self, obs: Any, ctx: Any) -> None:
        if getattr(obs, "event", "") or obs.source == "uia_event":
            return            # a message that flashed (step 9) is part of a page, not a page
        from .page_diff import map_from_lines
        profile = dict(obs.profile)
        client = next((f"{f}:{profile[f]}" for f in self._client_fields if profile.get(f)), None)
        self.atlas.merge(obs.portal, map_from_lines([ln for ln in obs.lines if ln][:MAX_MERGE_LINES]),
                         url=obs.url, client=client, session=obs.session_id)
