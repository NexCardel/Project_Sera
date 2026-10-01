"""
Tests for portal furniture (2026-09-28): the atlas learns the text a portal shows on many different
pages, and page maps leave RUNS of it out (page_map.mark_furniture, atlas furniture_test,
page_view.page_for). Field test that asked for it: the ITR portal's menu ("e-file unavailable",
"My Profile", ...) made most pages read as error or profile pages. Every value here is fictional.
"""
from types import SimpleNamespace

from core.sgt_i import atlas as at
from core.sgt_i import page_kinds as pk
from core.sgt_i import page_map as pm
from core.sgt_i.page_view import page_for

CLIENTS = [("RAVI MEHTA", "ABCDE1234F"), ("ANITA DESAI", "PQRSX5678K"), ("KIRAN RAO", "LMNOP9012Z")]
MENU = ["Dashboard", "e-File unavailable", "My Profile unavailable", "Help desk", "Logout"]


class Clock:
    def __init__(self):
        self.t = 1_790_000_000.0

    def __call__(self):
        return self.t


def page_lines(k, name):
    """Page k of a fictional portal: the client's greeting, the menu every page shares, a lone
    'Status:' label many pages share, and content of its own."""
    own = []
    for j in range(5):
        own += ["Page %d field %s:" % (k, "abcde"[j]), "value %d%d" % (k, j)]
    return ["Welcome, %s" % name] + MENU + ["Page %d title" % k, "Status:", "Open"] + own


def obs(lines, pan, url="https://portal.example.test/x", nodes=()):
    return SimpleNamespace(portal="portal.example.test", url=url, lines=tuple(lines), nodes=nodes,
                           source="uia", event="", session_id="s-" + pan, profile={"pan": pan})


def learnt(tmp_path, pages=12, clients=CLIENTS):
    atlas = at.Atlas(directory=tmp_path, clock=Clock())
    comp = at.AtlasComponent(atlas, client_fields=("pan",))
    for name, pan in clients:
        for k in range(pages):
            comp.observe(obs(page_lines(k, name), pan, url="https://portal.example.test/%s" % ("page-" + "abcdefghijklmnop"[k])), None)
    return atlas, atlas.portal("portal.example.test")


# ── page_map.mark_furniture: only runs ───────────────────────────────────────────────────────
def test_only_a_run_of_frequent_text_is_furniture():
    texts = ["Home", "Menu A", "", "Menu B", "Title", "Status:", "Open", "Footer 1", "Footer 2", "Footer 3"]
    frequent = {"Home", "Menu A", "Menu B", "Status:", "Footer 1", "Footer 2", "Footer 3"}
    nodes = [pm.Node(i, t) for i, t in enumerate(texts)]
    nodes[7] = pm.Node(7, "Footer 1", zone=pm.DIALOG)          # a dialog is never touched
    out = pm.mark_furniture(nodes, frequent.__contains__, min_run=3)
    zones = [n.zone for n in out]
    assert [zones[i] for i in (0, 1, 3)] == [pm.NAVIGATION] * 3  # the empty container does not break the run
    assert zones[5] == pm.MAIN                                  # lone shared label: data, stays
    assert zones[7] == pm.DIALOG and zones[8] == zones[9] == pm.MAIN   # run broken by the dialog: only 2


def test_no_furniture_test_means_todays_map():
    lines = MENU + ["Status:", "Open"]
    boxes = [{"text": t, "x": 20, "y": 24 * i, "width": 80, "height": 18} for i, t in enumerate(lines)]
    plain = pm.build_page_map(pm.nodes_from_ocr(boxes), header_band_px=0)
    same = pm.build_page_map(pm.nodes_from_ocr(boxes), header_band_px=0, furniture=None)
    assert plain == same


# ── the atlas learns it ─────────────────────────────────────────────────────────────────────
def test_atlas_learns_the_menu_but_not_a_name_a_lone_label_or_a_rare_line(tmp_path):
    _, portal = learnt(tmp_path)
    frequent = portal.furniture_test()
    assert frequent is not None
    assert all(frequent(t) for t in MENU)
    assert frequent("Status:")                                  # frequent, but alone on each page...
    assert not frequent("Welcome, RAVI MEHTA")                  # one client's name on every page
    assert not frequent("Page 3 title")                         # one page only
    page = page_for(obs(page_lines(3, "RAVI MEHTA"), "ABCDE1234F"), SimpleNamespace(portal=lambda p: portal))
    zone = {n.text: n.zone for n in page.nodes}
    assert all(zone[t] == pm.NAVIGATION for t in MENU)
    assert zone["Status:"] == pm.MAIN                           # ...so it stays a label
    assert any(p.label.startswith("Status") for p in page.pairs)


def test_nothing_is_furniture_while_the_portal_is_new(tmp_path):
    _, portal = learnt(tmp_path, pages=6)                       # < furniture_min_known_pages
    assert portal.furniture_test() is None
    _, portal = learnt(tmp_path / "one", clients=CLIENTS[:1])   # one client: never enough
    assert portal.furniture_test() is None


def test_furniture_list_shows_template_text_only_and_survives_a_restart(tmp_path):
    atlas, portal = learnt(tmp_path)
    shown = portal.furniture()
    texts = {e["text"] for e in shown}
    assert "Help desk" in texts and not any("RAVI" in t or "ANITA" in t for t in texts)
    # A menu item the line map ever paired as a VALUE is shown only as its shape (14.5).
    assert "Logout" not in texts and any(e["shape"] == "AAAAAA" and not e["text"] for e in shown)
    assert all(e["pages"] >= 5 and e["clients"] >= 2 for e in shown)
    atlas.save()
    again = at.Atlas(directory=tmp_path, clock=Clock()).portal("portal.example.test")
    assert again.furniture_test() is not None and again.furniture_test()("Logout")
    public = (tmp_path / "atlas" / "portal.example.test.json").read_text(encoding="utf-8")
    assert "ABCDE1234F" not in public and "RAVI" not in public and '"furniture"' in public


def test_lab_lists_the_furniture_per_portal(tmp_path):
    from core.sgt_i import lab
    atlas, _ = learnt(tmp_path)
    atlas.save()
    lines = lab.furniture_summary(base=tmp_path)
    assert len(lines) == 1 and lines[0].startswith("portal.example.test:") and "Help desk" in lines[0]
    assert "RAVI" not in lines[0] and "not yet template" in lines[0]
    assert lab.furniture_summary(base=tmp_path / "none") == []


def test_furniture_fades_when_the_portal_stops_showing_it(tmp_path):
    atlas, portal = learnt(tmp_path)
    assert portal.furniture_test()("Logout")
    portal._clock.t += 40 * 86400                               # > fade_days without a sighting
    portal._furniture = None
    assert portal.furniture_test() is None


# ── what it fixes ───────────────────────────────────────────────────────────────────────────
def test_the_menu_no_longer_makes_every_page_a_profile_or_error_page(tmp_path):
    atlas, portal = learnt(tmp_path)
    lines = page_lines(4, "RAVI MEHTA") + ["PAN:", "ABCDE1234F"]
    before = pk.classify(page_for(obs(lines, "ABCDE1234F")))                 # no atlas: today
    after = pk.classify(page_for(obs(lines, "ABCDE1234F"), atlas))
    assert before.kind == "profile"                             # "My Profile" menu item + a PAN
    assert after.kind != "profile"


def test_seras_own_injected_panel_is_never_portal_content():
    # Field test 2026-09-28: Sera's extension panel ("Username Injected", "Password" buttons)
    # showed up in the ITR login page's atlas fingerprint. uia_nodes marks it "own".
    docs = [[{"parent": -1, "depth": 0, "ctype": 50026, "name": "", "own": True},
             {"parent": 0, "depth": 1, "ctype": 50000, "name": "Username Injected"},
             {"parent": -1, "depth": 0, "ctype": 50026, "name": "", "landmark": 80002},
             {"parent": 2, "depth": 1, "ctype": 50000, "name": "Continue"}]]
    page = page_for(obs(["Username Injected", "Continue"], "ABCDE1234F", nodes=docs))
    zone = {n.text: n.zone for n in page.nodes if n.text}
    assert zone == {"Username Injected": pm.NAVIGATION, "Continue": pm.MAIN}


def test_node_tree_is_used_when_the_core_had_one(tmp_path):
    NAV, TEXT = 80003, 50020
    docs = [[{"parent": -1, "depth": 0, "ctype": 50026, "name": "", "landmark": NAV},
             {"parent": 0, "depth": 1, "ctype": TEXT, "name": "My Profile"},
             {"parent": -1, "depth": 0, "ctype": 50026, "name": "", "landmark": 80002},
             {"parent": 2, "depth": 1, "ctype": TEXT, "name": "Return filed"}]]
    page = page_for(obs(["My Profile", "Return filed"], "ABCDE1234F", nodes=docs))
    zone = {n.text: n.zone for n in page.nodes if n.text}
    assert zone == {"My Profile": pm.NAVIGATION, "Return filed": pm.MAIN}
    broken = page_for(obs(["My Profile", "Return filed"], "ABCDE1234F", nodes=[[{"bad": 1}]]))
    assert [n.text for n in broken.nodes] == ["My Profile", "Return filed"]   # lines instead
