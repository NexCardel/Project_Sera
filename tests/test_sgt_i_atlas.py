"""
Tests for core/sgt_i/atlas.py (blueprint 14.4 step 4, 14.5 privacy rules 2, 3, 7). Every portal,
client name, PAN and value here is fictional.
"""

import json
import re
from pathlib import Path

from core.sgt_i import atlas as at
from core.sgt_i import page_map as pm
from core.sgt_i.stats import ContainerStats

CLIENTS = [("RAVI MEHTA", "ABCDE1234F"), ("ANITA DESAI", "PQRSX5678K"), ("KIRAN RAO", "LMNOP9012Z"),
           ("SUNIL VERMA", "FGHIJ3456L"), ("MEERA IYER", "UVWXY7890M")]
DAY = 86400.0


def dashboard(name, pan, rows=3, dialog=False, fy="2025-26", extra_label=None):
    """A fictional returns dashboard: header greeting, a section, two pairs, a button, a table."""
    nodes = [
        pm.Node(0, "Welcome, %s" % name, zone=pm.HEADER),
        pm.Node(1, "Returns Dashboard", heading=1, section=0),
        pm.Node(2, "Financial Year:", section=0), pm.Node(3, fy, section=0),
        pm.Node(4, "PAN:", section=0), pm.Node(5, pan, section=0),
        pm.Node(6, "Search", role="button", section=0),
        pm.Node(7, "Period", role="columnheader", row=0, col=0, table=9, section=0),
        pm.Node(8, "Status", role="columnheader", row=0, col=1, table=9, section=0),
    ]
    pairs = [pm.Pair("Financial Year", fy, "right", 2, 3, pm.MAIN, 0),
             pm.Pair("PAN", pan, "right", 4, 5, pm.MAIN, 0)]
    for r in range(1, rows + 1):
        i = len(nodes)
        nodes += [pm.Node(i, "M%02d-2025" % r, role="cell", row=r, col=0, table=9, section=0),
                  pm.Node(i + 1, "Filed", role="cell", row=r, col=1, table=9, section=0)]
        pairs += [pm.Pair("Period", "M%02d-2025" % r, "column", 7, i, pm.MAIN, 0, r),
                  pm.Pair("Status", "Filed", "column", 8, i + 1, pm.MAIN, 0, r)]
    if extra_label:
        i = len(nodes)
        nodes += [pm.Node(i, extra_label + ":", section=0), pm.Node(i + 1, "12-03-1980", section=0)]
        pairs.append(pm.Pair(extra_label, "12-03-1980", "right", i, i + 1, pm.MAIN, 0))
    if dialog:
        nodes.append(pm.Node(len(nodes), "Filing Successful", zone=pm.DIALOG, heading=2, section=1))
    sections = (pm.Section(0, "Returns Dashboard", 1, node=1),
                pm.Section(1, "Filing Successful", 2, node=len(nodes) - 1))
    return pm.PageMap(tuple(nodes), sections, tuple(pairs))


def profile(name):
    nodes = [pm.Node(0, "My Profile", heading=1, section=0),
             pm.Node(1, "Legal Name:", section=0), pm.Node(2, name, section=0),
             pm.Node(3, "Mobile:", section=0), pm.Node(4, "9876543210", section=0),
             pm.Node(5, "Edit", role="button", section=0)]
    pairs = (pm.Pair("Legal Name", name, "right", 1, 2, pm.MAIN, 0),
             pm.Pair("Mobile", "9876543210", "right", 3, 4, pm.MAIN, 0))
    return pm.PageMap(tuple(nodes), (pm.Section(0, "My Profile", 1, node=0),), pairs)


class Clock:
    def __init__(self):
        self.t = 1_790_000_000.0

    def __call__(self):
        return self.t


def new_atlas(tmp_path, clock=None, **config):
    return at.PortalAtlas("portal.example.test", directory=tmp_path, config=config or None,
                          clock=clock or Clock(), salt="00" * 32)


def only_page(a):
    assert len(a.pages) == 1
    return a.to_json()["pages"][0]


# ── page identity ────────────────────────────────────────────────────────────────
def test_same_page_across_clients_and_list_lengths_repeating_blocks_collapse(tmp_path):
    a = new_atlas(tmp_path)
    ids = {a.merge(dashboard(n, p, rows=r), client=p) for (n, p), r in zip(CLIENTS, (3, 14, 5))}
    assert len(ids) == 1
    page = only_page(a)
    assert page["visits"] == 3 and page["clients"] == 3
    period = next(s for s in page["slots"].values() if s["container"].endswith("Period"))
    assert period["repeating"] and period["max_rows"] == 14
    assert len(page["slots"]) == 4                     # FY, PAN, Period, Status - rows collapsed


def test_different_structure_is_a_new_page(tmp_path):
    a = new_atlas(tmp_path)
    assert a.merge(dashboard(*CLIENTS[0])) != a.merge(profile(CLIENTS[0][0]))
    assert len(a.pages) == 2


def test_the_address_is_only_a_hint_that_lowers_the_bar(tmp_path):
    nodes = [pm.Node(0, "Returns Dashboard", heading=1, section=0),
             pm.Node(1, "Financial Year:", section=0), pm.Node(2, "2025-26", section=0),
             pm.Node(3, "Search", role="button", section=0),
             pm.Node(4, "Quarter:", section=0), pm.Node(5, "Q1", section=0),
             pm.Node(6, "Reset", role="button", section=0)]
    pairs = (pm.Pair("Financial Year", "2025-26", "right", 1, 2, pm.MAIN, 0),
             pm.Pair("Quarter", "Q1", "right", 4, 5, pm.MAIN, 0))
    variant = pm.PageMap(tuple(nodes), (pm.Section(0, "Returns Dashboard", 1, node=0),), pairs)
    url = "https://portal.example.test/returns/dashboard"

    a = new_atlas(tmp_path)
    first = a.merge(dashboard(*CLIENTS[0]), url=url)
    assert a.merge(variant, url=url) == first            # overlap 3/8: known address -> same page
    b = new_atlas(tmp_path / "b")
    other = b.merge(dashboard(*CLIENTS[0]), url=url)
    assert b.merge(variant, url="https://portal.example.test/other") != other
    assert len(b.pages) == 2


def test_url_hint_keeps_only_a_masked_path():
    assert at.url_hint("https://x.test/returns/ABCDE1234F/view?pan=ABCDE1234F#top") == "/returns/AAAAA9999A/view"
    assert at.url_hint("") == ""


# ── template promotion ───────────────────────────────────────────────────────────
def test_text_becomes_structure_only_after_three_different_clients(tmp_path):
    a = new_atlas(tmp_path)
    for _ in range(3):
        a.merge(dashboard(*CLIENTS[0]), client=CLIENTS[0][1])       # one client, many visits
    a.merge(dashboard(*CLIENTS[1]), client=CLIENTS[1][1])
    fp = only_page(a)["fingerprint"]
    assert "heading:AAAAAAA AAAAAAAAA" in fp and "heading:Returns Dashboard" not in fp
    a.merge(dashboard(*CLIENTS[2]), client=CLIENTS[2][1])
    page = only_page(a)
    assert "heading:Returns Dashboard" in page["fingerprint"]
    assert any(s["container"] == "Returns Dashboard › Financial Year" for s in page["slots"].values())


def test_the_client_threshold_is_config(tmp_path):
    a = new_atlas(tmp_path, template_min_clients=2)
    for n, p in CLIENTS[:2]:
        a.merge(dashboard(n, p), client=p)
    assert "button:Search" in only_page(a)["fingerprint"]


def test_mixed_text_keeps_the_portal_words_and_masks_the_rest(tmp_path):
    a = new_atlas(tmp_path)
    a.merge(dashboard(*CLIENTS[0]), client=CLIENTS[0][1])
    texts = [e["text"] for e in only_page(a)["elements"].values()]
    assert not any("Welcome" in t for t in texts)        # one client: nothing but shapes
    for n, p in CLIENTS[1:3]:
        a.merge(dashboard(n, p), client=p)
    texts = [e["text"] for e in only_page(a)["elements"].values()]
    assert "Welcome, «text»" in texts


# ── regions, ageing, transitions ─────────────────────────────────────────────────
def test_optional_regions_carry_how_often_they_appear(tmp_path):
    a = new_atlas(tmp_path)
    for i, (n, p) in enumerate(CLIENTS[:3]):
        a.merge(dashboard(n, p, dialog=(i == 1)), client=p)
    regions = only_page(a)["regions"]
    dialog = next(r for r in regions if r["role"] == "dialog")
    section = next(r for r in regions if r["label"] == "Returns Dashboard")
    assert dialog["optional"] and dialog["seen"] == 1
    assert not section["optional"] and section["seen"] == 3


def test_parts_fade_then_retire_and_pages_retire(tmp_path):
    clock = Clock()
    a = new_atlas(tmp_path, clock=clock)
    a.merge(dashboard(*CLIENTS[0], extra_label="Date of Birth"))
    clock.t += 40 * DAY
    a.merge(dashboard(*CLIENTS[0]))
    a.age()
    dob = [s for s in only_page(a)["slots"].values() if s["container"].endswith("AAAA AA AAAAA")]
    assert dob and dob[0]["fading"]
    clock.t += 60 * DAY
    a.merge(dashboard(*CLIENTS[0]))
    a.age()
    page = only_page(a)
    assert not any(s["container"].endswith("AAAA AA AAAAA") for s in page["slots"].values())
    assert any(r["was"] == "slot" for r in page["retired"])
    clock.t += 200 * DAY
    a.age()
    data = a.to_json()
    assert data["pages"] == [] and len(data["retired_pages"]) == 1


def test_transitions_are_counted_per_session(tmp_path):
    a = new_atlas(tmp_path)
    d, p = dashboard(*CLIENTS[0]), profile(CLIENTS[0][0])
    ids = [a.merge(pg, session="s1") for pg in (d, p, d, p)]
    a.merge(d)                                                   # no session: no transition
    tr = {(t["from"], t["to"]): t["count"] for t in a.to_json()["transitions"]}
    assert tr == {(ids[0], ids[1]): 2, (ids[1], ids[0]): 1}


# ── persistence ──────────────────────────────────────────────────────────────────
def test_atomic_files_reload_and_keep_matching(tmp_path):
    a = new_atlas(tmp_path)
    pid = a.merge(dashboard(*CLIENTS[0]), client=CLIENTS[0][1])
    a.save()
    assert (tmp_path / "atlas" / "portal.example.test.json").exists()
    assert (tmp_path / "atlas_private" / "portal.example.test.json").exists()
    assert not list(tmp_path.rglob("*.tmp"))
    b = new_atlas(tmp_path)
    assert b.merge(dashboard(*CLIENTS[1]), client=CLIENTS[1][1]) == pid
    assert only_page(b)["visits"] == 2


def test_a_corrupt_atlas_is_an_empty_atlas(tmp_path):
    path = tmp_path / "atlas" / "portal.example.test.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    a = new_atlas(tmp_path)
    assert a.pages == {}
    assert path.with_suffix(".corrupt").exists()
    assert a.merge(dashboard(*CLIENTS[0])) is not None


def test_slots_carry_the_container_kind_when_stats_are_given(tmp_path):
    a = at.PortalAtlas("portal.example.test", directory=tmp_path, clock=Clock(), salt="00" * 32,
                       stats=ContainerStats(directory=tmp_path))
    for n, p in CLIENTS[:3]:
        a.merge(dashboard(n, p), client=p)
    pan = next(s for s in only_page(a)["slots"].values() if s["container"].endswith("PAN"))
    assert pan["kind"] == "identifier" and pan["shapes"] == {"AAAAA9999A": 3}


# ── privacy (14.5) ───────────────────────────────────────────────────────────────
def test_no_client_value_ever_reaches_either_atlas_file(tmp_path):
    a = new_atlas(tmp_path)
    for n, p in CLIENTS:
        a.merge(dashboard(n, p, dialog=True), url="https://portal.example.test/r/%s?pan=%s" % (p, p),
                client=p, session="s")
        a.merge(profile(n), client=p, session="s")
    a.save()
    public = (tmp_path / "atlas" / "portal.example.test.json").read_text(encoding="utf-8")
    private = (tmp_path / "atlas_private" / "portal.example.test.json").read_text(encoding="utf-8")
    json.loads(public)
    for name, pan in CLIENTS:
        for secret in [pan] + name.split():
            assert secret not in public and secret not in private
    for value in ("9876543210", "2025-26", "M01-2025"):
        assert value not in public and value not in private
    assert "Returns Dashboard" in public and "Welcome, «text»" in public   # the portal's own words
    assert not re.search(r"[0-9a-f]{24}", public)               # no salted hash leaves the private file


def test_the_component_feeds_every_page_read_and_shares_the_atlas_with_the_gps(tmp_path):
    from types import SimpleNamespace
    from core.sgt_i import default_components
    from core.sgt_i.gps import GpsComponent

    comp = at.AtlasComponent(at.Atlas(directory=tmp_path, clock=Clock()), client_fields=("pan",))

    def read(name, pan, source="uia", event=""):
        lines = ("Welcome, %s" % name, "Returns Dashboard", "Financial Year:", "2025-26", "PAN:", pan, "Search")
        return SimpleNamespace(source=source, event=event, lines=lines, portal="Portal.Example.Test",
                               url="https://portal.example.test/dashboard", session_id="s-" + pan,
                               profile={"pan": pan})

    comp.observe(read(*CLIENTS[0], source="uia_event", event="live_region"), None)
    assert comp.atlas.portal("portal.example.test").pages == {}          # a flash is not a page
    for n, p in CLIENTS[:3]:
        comp.observe(read(n, p), None)
    a = comp.atlas.portal("portal.example.test")
    assert len(a.pages) == 1 and only_page(a)["visits"] == 3 and only_page(a)["clients"] == 3
    a.save()
    for f in tmp_path.rglob("*.json"):
        text = f.read_text(encoding="utf-8")
        assert not any(s in text for n, p in CLIENTS for s in [p] + n.split())
    comps = default_components()
    gps = next(c for c in comps if isinstance(c, GpsComponent))
    assert comps[0].name == "atlas" and gps._atlas is comps[0].atlas
    # Field test 2026-09-28: the live atlas had no container stats, so no slot ever got a kind
    # and the miner dropped every one as "kind unknown".
    assert comps[0].atlas._stats is not None


def test_the_core_never_imports_the_atlas():
    root = Path(__file__).resolve().parents[1] / "core" / "sgt"
    for f in root.glob("*.py"):
        assert "atlas" not in f.read_text(encoding="utf-8"), f
