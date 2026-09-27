"""
Tests for core/sgt_i/gps.py (blueprint 14.4 step 5). Every portal, page and value here is
fictional; portal/page names read like a GST-style filing flow only to make the routes readable.
"""

from core.sgt_i import atlas as at
from core.sgt_i import gps
from core.sgt_i import page_map as pm

PORTAL = "portal.example.test"

# Two routes sharing only their entry page (14.4 step 5's "graph, not chain": one dashboard, many
# destinies). Once each has its own next page the routes never touch again, so build_routes never
# has to guess which confirmation a shared MIDDLE page belongs to - that guess is what "settled by
# the lookback, else no guess" is for, tested on its own further down with identify_page.
DASHBOARD = ("Filing Dashboard", "/dashboard")
ROUTE_A = [DASHBOARD, ("GSTR-1 Details", "/gstr1/form"), ("GSTR-1 Payment", "/gstr1/pay"),
           ("GSTR-1 Filed", "/gstr1/confirm")]
ROUTE_B = [DASHBOARD, ("GSTR-3B Details", "/gstr3b/form"), ("GSTR-3B Payment", "/gstr3b/pay"),
           ("GSTR-3B Filed", "/gstr3b/confirm")]


def _page(heading: str) -> pm.PageMap:
    """The smallest page map that gives the atlas something to fingerprint: one heading."""
    return pm.PageMap((pm.Node(0, heading, heading=1, section=0),),
                      (pm.Section(0, heading, 1, node=0),), ())


def _new_atlas(tmp_path, **config):
    return at.PortalAtlas(PORTAL, directory=tmp_path, config=config or None, salt="00" * 32)


def _build(atlas, clients=4):
    """Walks `clients` different fictional clients down each route, so every edge clears the
    default min_support (3) and every page's heading is template-promoted (readable names)."""
    ids = {}
    for i in range(clients):
        for route, tag in ((ROUTE_A, "a"), (ROUTE_B, "b")):
            client, session = "client-%s%d" % (tag, i), "session-%s%d" % (tag, i)
            for heading, url in route:
                ids[heading] = atlas.merge(_page(heading), url=url, client=client, session=session)
    return ids


# ── build_routes ─────────────────────────────────────────────────────────────────────────────
def test_build_routes_finds_both_routes_from_the_shared_dashboard(tmp_path):
    atlas = _new_atlas(tmp_path)
    ids = _build(atlas)
    routes = gps.build_routes(atlas)
    assert len(routes) == 2
    by_name = {r.name: r for r in routes}
    assert set(by_name) == {"GSTR-1 Filed", "GSTR-3B Filed"}
    a, b = by_name["GSTR-1 Filed"], by_name["GSTR-3B Filed"]
    assert a.pages == (ids["Filing Dashboard"], ids["GSTR-1 Details"],
                       ids["GSTR-1 Payment"], ids["GSTR-1 Filed"])
    assert b.pages == (ids["Filing Dashboard"], ids["GSTR-3B Details"],
                       ids["GSTR-3B Payment"], ids["GSTR-3B Filed"])
    assert a.support == 4 and b.support == 4
    assert a.pages[0] == b.pages[0]        # the shared dashboard


def test_build_routes_needs_min_support(tmp_path):
    atlas = _new_atlas(tmp_path)
    _build(atlas, clients=2)          # below the default min_support of 3
    assert gps.build_routes(atlas) == []


def test_build_routes_on_an_empty_atlas(tmp_path):
    assert gps.build_routes(_new_atlas(tmp_path)) == []


# ── position, route, progress ───────────────────────────────────────────────────────────────
def test_a_session_walking_route_a_gets_route_and_progress(tmp_path):
    atlas = _new_atlas(tmp_path)
    ids = _build(atlas)
    tracker = gps.Gps()
    tracker.visit("live-1", "/dashboard", atlas)
    tracker.visit("live-1", "/gstr1/form", atlas)
    pos = tracker.visit("live-1", "/gstr1/pay", atlas)
    assert pos.page == ids["GSTR-1 Payment"]
    assert pos.route == "GSTR-1 Filed"
    assert pos.step == 3 and pos.steps_total == 4
    assert not pos.at_confirmation
    final = tracker.visit("live-1", "/gstr1/confirm", atlas)
    assert final.at_confirmation and final.step == 4 and final.steps_total == 4


def test_dead_reckoning_when_the_next_page_cannot_be_told(tmp_path):
    atlas = _new_atlas(tmp_path)
    ids = _build(atlas)
    tracker = gps.Gps()
    tracker.visit("live-2", "/dashboard", atlas)
    tracker.visit("live-2", "/gstr1/form", atlas)
    pos = tracker.visit("live-2", "/blind-or-unknown", atlas)   # no atlas page matches this address
    assert pos.dead_reckoned
    assert pos.page == ids["GSTR-1 Payment"]         # the only page GSTR-1 Details ever led to


def test_confirmation_next_only_on_the_page_before_the_confirmation(tmp_path):
    atlas = _new_atlas(tmp_path)
    _build(atlas)
    tracker = gps.Gps()
    flags = [tracker.visit("live-5", url, atlas).confirmation_next
             for url in ("/dashboard", "/gstr1/form", "/gstr1/pay", "/gstr1/pay", "/gstr1/confirm")]
    assert flags == [False, False, True, True, False]


def test_no_route_means_no_confirmation_next(tmp_path):
    atlas = _new_atlas(tmp_path)
    _build(atlas)
    tracker = gps.Gps(config={"min_route_len": 99})   # no route qualifies: only transitions remain
    assert gps.build_routes(atlas, tracker.config) == []
    pos = tracker.visit("live-6", "/gstr1/pay", atlas)
    assert pos.route is None and not pos.confirmation_next    # no route = no known confirmation


# ── odd jumps ────────────────────────────────────────────────────────────────────────────────
def test_landing_on_the_other_routes_confirmation_is_an_odd_jump(tmp_path):
    atlas = _new_atlas(tmp_path)
    ids = _build(atlas)
    tracker = gps.Gps()
    tracker.visit("live-3", "/dashboard", atlas)
    tracker.visit("live-3", "/gstr1/form", atlas)         # now tracking route A
    pos = tracker.visit("live-3", "/gstr3b/confirm", atlas)   # a tab switch, or a bookmark
    assert pos.odd_jump
    assert pos.page == ids["GSTR-3B Filed"]
    assert pos.route == "GSTR-3B Filed"                   # re-acquired: this page is unambiguous
    assert pos.at_confirmation


# ── context (form/period) as a second opinion ───────────────────────────────────────────────
def test_context_is_offered_only_when_the_page_itself_leaves_it_out(tmp_path):
    atlas = _new_atlas(tmp_path)
    _build(atlas)
    tracker = gps.Gps()
    tracker.visit("live-4", "/dashboard", atlas, draft={})
    tracker.visit("live-4", "/gstr1/form", atlas,
                  draft={"form": "GSTR-1", "period": "August (FY 2026-27)"})
    pos = tracker.visit("live-4", "/gstr1/pay", atlas, draft={})     # this page's own draft is bare
    assert pos.context == {"form": "GSTR-1", "period": "August (FY 2026-27)"}
    confirmed = tracker.visit("live-4", "/gstr1/confirm", atlas,
                              draft={"form": "GSTR-1", "period": "August (FY 2026-27)"})
    assert confirmed.context is None      # the page's own value already has both - nothing to add


def test_odd_jump_drops_the_carried_context(tmp_path):
    atlas = _new_atlas(tmp_path)
    _build(atlas)
    tracker = gps.Gps()
    tracker.visit("live-5", "/dashboard", atlas, draft={})
    tracker.visit("live-5", "/gstr1/form", atlas,
                  draft={"form": "GSTR-1", "period": "August (FY 2026-27)"})
    pos = tracker.visit("live-5", "/gstr3b/confirm", atlas, draft={})
    assert pos.odd_jump and pos.context is None


# ── identify_page: a page shared by several routes, settled by the lookback ────────────────
def test_identify_page_needs_the_lookback_to_settle_a_shared_address(tmp_path):
    """A single-page app can show two structurally different pages at the same address. The atlas
    tells them apart by structure (never by address alone); the GPS only has the address to go on
    live, so a shared one is settled by the last page seen this session - or not guessed at all."""
    atlas = _new_atlas(tmp_path)
    for i in range(3):
        client, session = "client-%d" % i, "session-%d" % i
        p1 = atlas.merge(_page("Step One"), url="/wizard", client=client, session=session)
        p2 = atlas.merge(_page("Step Two"), url="/wizard", client=client, session=session)
    assert gps.identify_page(atlas, "/wizard", []) is None        # no lookback - no guess
    assert gps.identify_page(atlas, "/wizard", [p1]) == p2        # p1's usual next step


# ── the SGT-I component: row enrichment via the W1-2 channel ───────────────────────────────
class _FakeCtx:
    def __init__(self):
        self.data = None
        self.harder = 0

    def enrich(self, data):
        self.data = data

    def read_harder(self, seconds=None):
        self.harder += 1


class _FakeObs:
    def __init__(self, session_id, portal, url, draft=None):
        self.session_id, self.portal, self.url = session_id, portal, url
        self.draft = draft or {}


def test_component_enriches_reached_page_not_submitted(tmp_path):
    atlas = _new_atlas(tmp_path)
    _build(atlas)
    atlas.save()
    live_atlas = at.Atlas(directory=tmp_path)          # a fresh load, as the live host would have
    comp = gps.GpsComponent(atlas=live_atlas)
    ctx = _FakeCtx()
    comp.observe(_FakeObs("s1", PORTAL, "/dashboard"), ctx)
    comp.observe(_FakeObs("s1", PORTAL, "/gstr1/form"), ctx)
    ctx = _FakeCtx()
    comp.observe(_FakeObs("s1", PORTAL, "/gstr1/pay"), ctx)
    assert ctx.data["route"] == "GSTR-1 Filed"
    assert ctx.data["step"] == "3 of 4"
    assert ctx.data["position"] == "reached GSTR-1 Payment, not submitted"
    ctx = _FakeCtx()
    comp.observe(_FakeObs("s1", PORTAL, "/gstr1/confirm"), ctx)
    assert ctx.data["position"] == "reached GSTR-1 Filed"
    assert "not submitted" not in ctx.data["position"]


def test_component_enriches_nothing_on_an_unmatched_page(tmp_path):
    live_atlas = at.Atlas(directory=tmp_path)          # nothing merged yet - cold start
    comp = gps.GpsComponent(atlas=live_atlas)
    ctx = _FakeCtx()
    comp.observe(_FakeObs("s1", PORTAL, "/anything"), ctx)
    assert ctx.data is None and ctx.harder == 0


def test_component_reads_harder_once_per_arrival_before_the_confirmation(tmp_path):
    atlas = _new_atlas(tmp_path)
    _build(atlas)
    atlas.save()
    comp = gps.GpsComponent(atlas=at.Atlas(directory=tmp_path))
    ctx = _FakeCtx()
    for url in ("/dashboard", "/gstr1/form"):
        comp.observe(_FakeObs("s1", PORTAL, url), ctx)
    assert ctx.harder == 0                            # far from the finish line: reads as usual
    for _ in range(4):                                # arrive, then re-read the same page 3 times
        comp.observe(_FakeObs("s1", PORTAL, "/gstr1/pay"), ctx)
    assert ctx.harder == 1                            # staying does not keep the window open
    comp.observe(_FakeObs("s2", PORTAL, "/gstr1/pay"), ctx)
    assert ctx.harder == 2                            # another session arriving asks for its own
    comp.observe(_FakeObs("s1", PORTAL, "/gstr1/form"), ctx)
    comp.observe(_FakeObs("s1", PORTAL, "/gstr1/pay"), ctx)
    assert ctx.harder == 3                            # back, then forward again = a new arrival
    comp.observe(_FakeObs("s1", PORTAL, "/gstr1/confirm"), ctx)
    assert ctx.harder == 3
