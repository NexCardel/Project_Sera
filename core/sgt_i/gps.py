"""
core/sgt_i/gps.py - the GPS: where the user is, which route, how far along
===========================================================================
Blueprint 14.4 step 5. The atlas (atlas.py) says how a portal's pages connect; the GPS reads
those transitions and says, for one session, which page this is, which route it is probably on,
and how far along that route it is - never more than the atlas's own counts support.

* **Routes are not written by hand.** `build_routes()` walks the atlas's transitions from every
  page that only ever *starts* a session (never arrived at from another known page) to every page
  that only ever *ends* one (never leads anywhere else) - a "frequent path from a starting kind to
  a confirmation kind" (14.4 step 5), without needing step 6's page-kind classifier (build order:
  step 5 comes before step 6). A route's support is its weakest edge; only edges seen at least
  `min_support` times count, so a one-off detour never becomes a route.
* **Position** matches the page read against a page's own `url_hints` (atlas.py); a hint shared by
  several pages is settled by the last few pages of this session (the lookback), never guessed.
  When the page can't be told at all, `dead_reckoning()` falls back to the previous page's usual
  next step - never a value, only a position.
* **Progress** is `step n of m` along whichever route the current page belongs to; a page that
  fits no known route, or fits several with no way to tell which, carries no route or progress -
  only its bare position.
* **Odd jumps** - landing straight on another route's confirmation page while one route is being
  followed - close the route rather than mislabel it; the guard also covers a tab switch in the
  same window, which looks exactly the same to the atlas.
* **Context (form/period) is a second opinion only.** The GPS remembers the session's own last
  captured form/period (from the Core's own draft, never a value it discovered itself) and offers
  them again when a later page on the same route leaves them out - exactly 14.5's rule that a
  value read on the page itself always wins; this is only ever handed back when nothing said
  otherwise.

Guards (14.4 step 5): a graph, not a chain (back/forward, reloads, bookmarks are normal); context
flows only along an established route, inside one session; the dashboard or a logout ends the
route quietly. The GPS itself never writes to the atlas and stores no client data - it only reads
what atlas.py already persisted and keeps a little session state in memory.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .atlas import PortalAtlas, url_hint

__all__ = ["DEFAULT_CONFIG", "Route", "Position", "build_routes", "identify_page",
           "dead_reckoning", "Gps", "GpsComponent"]

DEFAULT_CONFIG: Dict[str, Any] = {
    "min_support": 3,             # an edge must be walked this many times to belong to a route
    "min_route_len": 2,           # a route of one page says nothing
    "max_route_len": 10,          # hops from start to confirmation; bounds the path search
    "max_routes_per_portal": 40,
    "lookback": 5,                # past pages kept per session, to settle a shared page
    "max_dfs_steps": 5000,        # a hard cap on the route search, whatever the graph's shape
}

LABEL_MAX_LEN = 60
MAX_TRACKED_SESSIONS = 64         # GpsComponent's per-session "last page" memory


@dataclass(frozen=True)
class Route:
    """A frequent path from a starting page to a confirmation page, over the atlas's own
    transitions. `support` is the path's weakest edge (how many sessions actually walked it)."""
    id: str
    pages: Tuple[str, ...]        # atlas page ids, start first, confirmation last
    name: str                     # what the confirmation page's own text says (best effort)
    support: int


@dataclass(frozen=True)
class Position:
    """One session's GPS fix for the page just read. `page` is None when nothing in the atlas
    matches - not a guess. `route` / `step` / `steps_total` are None when the page fits no route,
    or fits more than one with no way to tell which (14.4 step 5: "if still unclear, no guess")."""
    page: Optional[str] = None
    label: str = ""
    route: Optional[str] = None
    step: Optional[int] = None
    steps_total: Optional[int] = None
    at_confirmation: bool = False
    confirmation_next: bool = False   # the page usually reached next is a route's confirmation
    odd_jump: bool = False
    dead_reckoned: bool = False
    context: Optional[Dict[str, str]] = None


# ── routes: built from the atlas's transitions ─────────────────────────────────────────────────
def _graph(atlas: PortalAtlas, min_support: int) -> Tuple[Dict[str, List[Tuple[str, int]]],
                                                          Dict[str, int], Dict[str, int]]:
    pages = atlas.pages
    edges: Dict[str, List[Tuple[str, int]]] = {}
    in_totals: Dict[str, int] = {}
    out_totals: Dict[str, int] = {}
    for tr in atlas.data.get("transitions", []):
        frm, to, count = tr["from"], tr["to"], tr["count"]
        if frm not in pages or to not in pages:
            continue
        out_totals[frm] = out_totals.get(frm, 0) + count
        in_totals[to] = in_totals.get(to, 0) + count
        if count >= min_support:
            edges.setdefault(frm, []).append((to, count))
    return edges, in_totals, out_totals


def _best_paths(edges: Dict[str, List[Tuple[str, int]]], start: str, targets: set,
                max_len: int, budget: List[int]) -> Dict[str, Tuple[int, Tuple[str, ...]]]:
    """For one start page, the highest-bottleneck simple path to each confirmation page reachable
    within `max_len` hops - the widest-path idea, kept to a plain bounded DFS since a portal's
    atlas is small. A route ends at the first confirmation it reaches, never runs through one."""
    best: Dict[str, Tuple[int, Tuple[str, ...]]] = {}

    def dfs(node: str, path: Tuple[str, ...], bottleneck: int, visited: frozenset) -> None:
        if budget[0] <= 0 or len(path) >= max_len:
            return
        budget[0] -= 1
        for to, count in edges.get(node, ()):
            if to in visited:
                continue
            nb = min(bottleneck, count)
            npath = path + (to,)
            if to in targets:
                cur = best.get(to)
                if cur is None or nb > cur[0]:
                    best[to] = (nb, npath)
                continue
            dfs(to, npath, nb, visited | {to})

    dfs(start, (start,), 1 << 30, frozenset({start}))
    return best


def _page_label(atlas: PortalAtlas, pid: str) -> str:
    """Best-effort human label for a page: its most-seen heading, else a url hint, else its id -
    whatever the atlas already has (may still be a masked shape if the text is not yet template-
    promoted for enough clients; that is the atlas's call, not the GPS's, see atlas.py)."""
    pub = atlas.pages.get(pid) or {}
    headings = [e for e in pub.get("elements", {}).values() if e.get("region") == "section"]
    if headings:
        text = max(headings, key=lambda e: e.get("seen", 0)).get("text") or pid
    else:
        hints = pub.get("url_hints") or []
        text = hints[0] if hints else pid
    return text[:LABEL_MAX_LEN]


def build_routes(atlas: PortalAtlas, config: Optional[Dict[str, Any]] = None) -> List[Route]:
    """Every frequent path from a page that only ever starts a session to one that only ever
    ends it. Pure over the atlas's current state; the caller decides how often to rebuild
    (`Gps` only rebuilds when the atlas's version has moved)."""
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    edges, in_totals, out_totals = _graph(atlas, cfg["min_support"])
    pages = atlas.pages
    starts = {pid for pid in pages if out_totals.get(pid, 0) > 0 and in_totals.get(pid, 0) == 0}
    confirmations = {pid for pid in pages if in_totals.get(pid, 0) > 0 and out_totals.get(pid, 0) == 0}
    if not starts or not confirmations:
        return []
    budget = [cfg["max_dfs_steps"]]
    routes: List[Route] = []
    for start in sorted(starts):
        for target, (support, path) in _best_paths(edges, start, confirmations,
                                                    cfg["max_route_len"], budget).items():
            if len(path) < cfg["min_route_len"]:
                continue
            routes.append(Route(id="%s>%s" % (start, target), pages=path,
                                name=_page_label(atlas, target), support=support))
    routes.sort(key=lambda r: -r.support)
    return routes[:cfg["max_routes_per_portal"]]


# ── position: matching one read against the atlas ──────────────────────────────────────────────
def identify_page(atlas: PortalAtlas, url: str, lookback: Sequence[str]) -> Optional[str]:
    """Which atlas page this read is, by its address's masked path (atlas.py's `url_hint`) alone -
    the GPS never sees the page's own structure (14.2: the shared read is not adopted yet, so
    Observation carries lines, not nodes). A hint shared by more than one page is settled by the
    strongest known transition from the last page seen this session; if that does not narrow it
    to one, no guess."""
    hint = url_hint(url)
    if not hint:
        return None
    candidates = [pid for pid, pub in atlas.pages.items() if hint in (pub.get("url_hints") or ())]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1 and lookback:
        prev = lookback[-1]
        best, best_n = None, -1
        for tr in atlas.data.get("transitions", []):
            if tr["from"] == prev and tr["to"] in candidates and tr["count"] > best_n:
                best, best_n = tr["to"], tr["count"]
        return best
    return None


def dead_reckoning(atlas: PortalAtlas, prev: str) -> Optional[str]:
    """When the page itself could not be told apart (a blind read, or one whose address matches
    nothing yet), the likely position is the previous page's usual next step (14.4 step 5)."""
    best, best_n = None, -1
    for tr in atlas.data.get("transitions", []):
        if tr["from"] == prev and tr["count"] > best_n:
            best, best_n = tr["to"], tr["count"]
    return best


class _SessionState:
    __slots__ = ("lookback", "route_id", "context")

    def __init__(self) -> None:
        self.lookback: List[str] = []
        self.route_id: Optional[str] = None
        self.context: Dict[str, str] = {}     # this session's own last-seen form/period


class Gps:
    """One portal's GPS across many sessions. `visit()` is the only thing a caller needs: give it
    the session id, the page's address and the atlas it should be read against (and, if going,
    the Core's own draft so far, for the context second opinion), get back a `Position`. Routes
    are rebuilt only when the atlas's version has moved since the last visit."""

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self._routes_cache: Dict[str, Tuple[int, List[Route]]] = {}      # portal -> (version, routes)
        self._sessions: Dict[str, _SessionState] = {}

    def end_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def _routes_for(self, atlas: PortalAtlas) -> List[Route]:
        version = atlas.data.get("version", 0)
        cached = self._routes_cache.get(atlas.portal)
        if cached is not None and cached[0] == version:
            return cached[1]
        routes = build_routes(atlas, self.config)
        self._routes_cache[atlas.portal] = (version, routes)
        return routes

    def visit(self, session_id: str, url: str, atlas: PortalAtlas,
             draft: Optional[Dict[str, str]] = None) -> Position:
        draft = draft or {}
        st = self._sessions.setdefault(session_id, _SessionState())
        routes = self._routes_for(atlas)

        page = identify_page(atlas, url, st.lookback)
        dead_reckoned = False
        if page is None and st.lookback:
            page = dead_reckoning(atlas, st.lookback[-1])
            dead_reckoned = page is not None
        for k in ("form", "period"):
            if draft.get(k):
                st.context[k] = draft[k]
        if page is None:
            return Position()

        confirmations = {r.pages[-1] for r in routes}
        starts = {r.pages[0] for r in routes}
        odd = False
        cur = next((r for r in routes if r.id == st.route_id), None) if st.route_id else None
        if cur is not None and page not in cur.pages:
            if page in confirmations and page != cur.pages[-1]:
                odd = True             # landed on ANOTHER route's confirmation mid-way through this one
            cur = None
            st.route_id = None
            st.context.clear()

        if cur is None and page not in starts:
            candidates = [r for r in routes if page in r.pages]
            if len(candidates) == 1:
                cur = candidates[0]
            elif len(candidates) > 1 and st.lookback:
                prev = st.lookback[-1]
                narrowed = [r for r in candidates
                           if prev in r.pages and r.pages.index(prev) < r.pages.index(page)]
                if len(narrowed) == 1:
                    cur = narrowed[0]
            if cur is not None:
                st.route_id = cur.id

        if not dead_reckoned and (not st.lookback or st.lookback[-1] != page):
            st.lookback.append(page)       # a re-read of the same page is not a new step back
            del st.lookback[:-self.config["lookback"]]

        step = steps_total = None
        at_conf = False
        if cur is not None:
            step = cur.pages.index(page) + 1
            steps_total = len(cur.pages)
            at_conf = page == cur.pages[-1]
            next_page = cur.pages[step] if step < steps_total else None
        else:
            next_page = dead_reckoning(atlas, page) if page not in confirmations else None
        conf_next = next_page is not None and next_page in confirmations

        context = None
        if cur is not None and not odd:
            missing = {k: v for k, v in st.context.items() if not draft.get(k)}
            context = missing or None

        return Position(page=page, label=_page_label(atlas, page), route=cur.name if cur else None,
                        step=step, steps_total=steps_total, at_confirmation=at_conf,
                        confirmation_next=conf_next, odd_jump=odd, dead_reckoned=dead_reckoned, context=context)


class GpsComponent:
    """The step-5 SGT-I component: reads `obs.portal`'s atlas (read-only - it never merges a
    page, that is step 4's job) and enriches the session's row with where the GPS thinks it is.
    Registering this costs nothing while the atlas is empty (cold start, 14.6): no page matches,
    nothing is enriched, exactly as if it were not there."""

    name = "gps"

    def __init__(self, atlas: Any = None, config: Optional[Dict[str, Any]] = None) -> None:
        if atlas is None:
            from .atlas import Atlas
            atlas = Atlas()
        self._atlas = atlas
        self._gps = Gps(config)
        self._last_page: Dict[str, Optional[str]] = {}   # session -> page of its previous visit

    def observe(self, obs: Any, ctx: Any) -> None:
        portal = self._atlas.portal(obs.portal)
        pos = self._gps.visit(obs.session_id, obs.url, portal, draft=dict(obs.draft))
        arrived = self._last_page.get(obs.session_id) != pos.page
        self._last_page.pop(obs.session_id, None)
        self._last_page[obs.session_id] = pos.page
        while len(self._last_page) > MAX_TRACKED_SESSIONS:
            del self._last_page[next(iter(self._last_page))]
        if pos.page is None:
            return
        # Read harder near the finish line (14.4 step 5 item 1, contract rule 3): on ARRIVING at a
        # page whose usual next page is the confirmation, the Core reads on every tick for a bounded
        # window, so a success message shown for under a second is not missed. Asked once per
        # arrival - staying on the page does not keep the window open.
        if pos.confirmation_next and arrived:
            ctx.read_harder()
        data: Dict[str, Any] = {}
        if pos.route:
            data["route"] = pos.route
        if pos.step and pos.steps_total:
            data["step"] = "%d of %d" % (pos.step, pos.steps_total)
        if pos.at_confirmation:
            data["position"] = "reached %s" % pos.label
        else:
            data["position"] = "reached %s, not submitted" % pos.label
        if pos.context:
            data["context"] = dict(pos.context)
        if pos.odd_jump:
            data["note"] = "unexpected jump between routes - route context reset"
        ctx.enrich(data)
