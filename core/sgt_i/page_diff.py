"""
core/sgt_i/page_diff.py - what just appeared: page-map diffing and short-lived messages
=======================================================================================
Blueprint 14.4 step 9, the half that needs no event: comparing each page map with the previous
one of the same session says what just APPEARED - a new message, a new dialog (a new dialog *is*
the event). And the half that does: what uia_events.FlashWatcher caught between two reads comes
in as an Observation with source "uia_event", and is counted here - including whether the
Core's polling would have seen it at all (the flash text was on neither the read before nor the
read after it).

diff() is a pure function over page maps. FlashComponent keeps, per session, only salted hashes
of the previous page's nodes (the salt lives in memory for this run only), and puts counts on the
row - never a text (14.5). The latest appeared text itself is kept in memory for the other
components (last_appeared), like the GPS's last_route.
"""

import hashlib
import os
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Dict, Hashable, List, Optional, Sequence, Tuple

from . import assertions
from . import page_map as pm

MAX_SESSIONS = 16
_DIALOG_MARK = ("\0dialog",)        # a key no node can have: "this page had a dialog open"


@dataclass(frozen=True)
class PageDiff:
    appeared: Tuple[pm.Node, ...]      # nodes on this page that the previous page did not have
    gone: int                          # how many of the previous page's nodes are not here now
    new_dialog: bool                   # a dialog is open now that was not before
    first: bool = False                # no previous page to compare with: nothing "appeared"


def node_key(n: pm.Node) -> Hashable:
    """What makes two nodes "the same" across reads: text, role and zone. Geometry is left out -
    a page that scrolls or reflows has not changed."""
    return (n.text, n.role, n.zone)


def page_keys(page: pm.PageMap, key: Callable[[pm.Node], Hashable] = node_key) -> Counter:
    """A page as a multiset of node keys (a text shown twice counts twice)."""
    keys = Counter(key(n) for n in page.nodes if n.text)
    if any(n.zone == pm.DIALOG for n in page.nodes):
        keys[_DIALOG_MARK] = 1
    return keys


def diff(prev: Optional[Counter], page: pm.PageMap, key: Callable[[pm.Node], Hashable] = node_key) -> PageDiff:
    """What appeared on `page` since the page whose page_keys() are `prev` (same key function)."""
    if prev is None:
        return PageDiff((), 0, False, first=True)
    left = Counter(prev)
    appeared: List[pm.Node] = []
    for n in page.nodes:
        if not n.text:
            continue
        k = key(n)
        if left[k] > 0:
            left[k] -= 1
        else:
            appeared.append(n)
    left.pop(_DIALOG_MARK, None)
    new_dialog = any(n.zone == pm.DIALOG for n in appeared) and not prev.get(_DIALOG_MARK)
    return PageDiff(tuple(appeared), sum(v for v in left.values() if v > 0), new_dialog)


def map_from_lines(lines: Sequence[str]) -> pm.PageMap:
    """A page map from the Core's lines (the Observation carries lines, not nodes, until the
    shared read is adopted - 14.2): each line a node stacked in reading order, as ledger.py does.
    Lines carry no geometry or roles, so a dialog only shows once nodes reach the Observation."""
    boxes = [{"text": t, "x": 20, "y": 24 * i, "width": 8 * max(1, len(t)), "height": 18}
             for i, t in enumerate(lines)]
    return pm.build_page_map(pm.nodes_from_ocr(boxes), header_band_px=0)


class _Session:
    __slots__ = ("keys", "texts", "waiting", "pages", "appeared", "dialogs", "flashes", "claims",
                 "missed", "last")

    def __init__(self) -> None:
        self.keys: Optional[Counter] = None           # previous page, salted node-key hashes
        self.texts: frozenset = frozenset()           # previous page, salted text hashes
        self.waiting: List[frozenset] = []            # flashes not on the read before them
        self.pages = 0
        self.appeared = 0                             # content nodes that appeared, latest page
        self.dialogs = 0
        self.flashes: Counter = Counter()             # event kind -> count
        self.claims: Counter = Counter()              # assertion class of the flash text -> count
        self.missed = 0                               # flashes on neither neighbouring read
        self.last: Tuple[str, ...] = ()               # latest appeared text, memory only


class FlashComponent:
    """The step-9 SGT-I component: diffs each page with the session's previous one and counts
    what flashed in between. Enrichment is counts only; it never asks the Core for anything."""

    name = "flashes"

    def __init__(self, max_lines: int = 400) -> None:
        self._salt = os.urandom(16)
        self._max_lines = max_lines
        self._sessions: Dict[str, _Session] = {}

    def _h(self, *parts: str) -> str:
        return hashlib.blake2b("\x1f".join(parts).encode("utf-8"), key=self._salt, digest_size=12).hexdigest()

    def _key(self, n: pm.Node) -> Hashable:
        return self._h(n.text, n.role, n.zone)

    def _session(self, sid: str) -> _Session:
        st = self._sessions.pop(sid, None) or _Session()
        self._sessions[sid] = st
        while len(self._sessions) > MAX_SESSIONS:
            del self._sessions[next(iter(self._sessions))]
        return st

    def last_appeared(self, session_id: str) -> Tuple[str, ...]:
        """The text that most recently appeared in this session (a flash, or new on a page)."""
        st = self._sessions.get(session_id)
        return st.last if st is not None else ()

    def observe(self, obs: Any, ctx: Any) -> None:
        st = self._session(obs.session_id)
        lines = [ln for ln in obs.lines if ln][: self._max_lines]
        texts = frozenset(self._h(t) for t in lines)
        if obs.source == "uia_event":
            st.flashes[obs.event or "event"] += 1
            for t in lines:
                cls = assertions.classify(t).cls
                if cls:
                    st.claims[cls] += 1
                    break
            if not texts & st.texts:
                st.waiting.append(texts)          # the read before did not have it - did the next?
            st.last = tuple(lines)
        else:
            page = map_from_lines(lines)
            d = diff(st.keys, page, self._key)
            st.keys, st.texts = page_keys(page, self._key), texts
            st.pages += 1
            st.appeared = sum(1 for n in d.appeared if n.is_content)
            st.dialogs += int(d.new_dialog)
            if d.appeared:
                st.last = tuple(n.text for n in d.appeared)
            st.missed += sum(1 for w in st.waiting if not w & texts)
            st.waiting = []
        self._enrich(st, ctx)

    def _enrich(self, st: _Session, ctx: Any) -> None:
        if not st.flashes and not st.dialogs:
            return            # nothing flashed and no dialog opened: the row stays as it was
        data: Dict[str, Any] = {"pages_diffed": st.pages, "appeared": st.appeared}
        if st.dialogs:
            data["dialogs"] = st.dialogs
        if st.flashes:
            data["flashes"] = dict(sorted(st.flashes.items()))
            data["missed_by_polling"] = st.missed
        if st.claims:
            data["flash_claims"] = dict(sorted(st.claims.items()))
        ctx.enrich(data)
