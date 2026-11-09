"""
SDIS pre-dev: cards and rows (tools/pre_dev/class_diff/cards.py, plan docs/cards_rows/plan.md section 7).

Fictional fixtures only: the node lists are built in this file. The thresholds are passed in by the tests
(theta=0.5, rho=0.4): they are test values, NOT the measured THETA / RHO. RHO is not set yet (plan 9.7).
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "pre_dev" / "class_diff"))

import cards  # noqa: E402

TEST_THETA = 0.5
TEST_RHO = 0.4

CT_TABLE, CT_DATAITEM, CT_HEADERITEM = 50036, 50029, 50035


class Page:
    """Builds a read's node list: add() returns the new node's index."""

    def __init__(self):
        self.nodes = []

    def add(self, parent, typ, name="", cls="", rect=None, ctype=None):
        n = {"parent": parent, "type_name": typ, "name": name, "cls": cls,
             "rect": rect or [0, 0, 10, 10]}
        if ctype is not None:
            n["ctype"] = ctype
        self.nodes.append(n)
        return len(self.nodes) - 1

    def read(self):
        return {"page": "fictional.example/returns", "title": "T", "docs": [self.nodes]}


def _flat(page):
    rec = page.read()
    fl, docs = cards.load_flat(rec)
    return fl, docs


def _tile_page(names, status_tag, wrap=False, tile_top=0):
    """A container of tiles: each tile holds a code, a description (the same on every tile, like
    IFF and GSTR-1 on the real dashboard), a status that is unique per capture, and a View button."""
    p = Page()
    root = p.add(-1, "Group", cls="dash")
    box = p.add(root, "Group", cls="tiles", rect=[0, tile_top, 600, 200])
    for k, name in enumerate(names):
        tile = p.add(box, "Group", cls="tile", rect=[k * 120, tile_top, 110, 100])
        holder = tile
        if wrap and k % 2:
            holder = p.add(tile, "Group", cls="wrap", rect=[k * 120, tile_top, 110, 100])
        p.add(holder, "Text", name=name, rect=[k * 120, tile_top + 10, 100, 14])
        p.add(holder, "Text", name="Details of outward supplies of goods or services",
              rect=[k * 120, tile_top + 30, 100, 14])
        p.add(holder, "Text", name=f"Status {status_tag}{k}", rect=[k * 120, tile_top + 50, 100, 14])
        p.add(holder, "Button", name="View", rect=[k * 120, tile_top + 70, 50, 20])
    return p


def _rows_page(n, prefix="row"):
    """A plain list of n identical-shape rows (div rows, not a Table)."""
    p = Page()
    root = p.add(-1, "Group", cls="list")
    for k in range(n):
        row = p.add(root, "Group", cls="row", rect=[0, k * 30, 300, 28])
        p.add(row, "Text", name=f"{prefix} {k} name", rect=[0, k * 30, 100, 14])
        p.add(row, "Text", name=f"{prefix} {k} value", rect=[120, k * 30, 100, 14])
        p.add(row, "Button", name="Edit", rect=[240, k * 30, 40, 20])
    return p


# ── §3 find groups ────────────────────────────────────────────────────────────

def test_two_tiles_form_one_group():
    fl, docs = _flat(_tile_page(["GSTR-1", "GSTR-2A"], "x"))
    groups = cards.find_groups(fl, docs, TEST_THETA)
    assert len(groups) == 1
    g = groups[0]
    assert g["kind"] == "unknown" and g["source"] == "siblings"
    assert len(g["members"]) == 2


def test_iff_and_gstr1_trap_is_not_a_name_match():
    # the description is shared by every tile: it must not make the tiles one card
    fl, docs = _flat(_tile_page(["IFF", "GSTR-2A"], "a"))
    g = cards.find_groups(fl, docs, TEST_THETA)[0]
    shared = [r for r in cards.roles(g).values() if r["role"] == "label"]
    assert any("Details of outward supplies" in r["texts"][0] for r in shared)


def test_wrapper_chains_of_different_depth_are_one_group():
    fl, docs = _flat(_tile_page(["GSTR-1", "GSTR-1A", "GSTR-2B"], "x", wrap=True))
    groups = cards.find_groups(fl, docs, TEST_THETA)
    assert len(groups) == 1 and len(groups[0]["members"]) == 3


def test_state_class_on_one_member_keeps_the_signature():
    p = Page()
    root = p.add(-1, "Group")
    a = p.add(root, "Group", cls="tile")
    p.add(a, "Text", name="GSTR-1")
    p.add(a, "Button", name="View", cls="btn active")
    b = p.add(root, "Group", cls="tile")
    p.add(b, "Text", name="GSTR-3B")
    p.add(b, "Button", name="View", cls="btn")
    fl, docs = _flat(p)
    assert cards._sig(fl, cards._children(fl), 1) == cards._sig(fl, cards._children(fl), 4)
    assert len(cards.find_groups(fl, docs, TEST_THETA)) == 1


def test_optional_field_is_its_own_slot():
    p = Page()
    root = p.add(-1, "Group")
    a = p.add(root, "Group", cls="tile")
    p.add(a, "Text", name="GSTR-1")
    p.add(a, "Text", name="Filed")
    p.add(a, "Button", name="View")
    b = p.add(root, "Group", cls="tile")
    p.add(b, "Text", name="GSTR-3B")
    p.add(b, "Text", name="Filed")
    p.add(b, "Text", name="13/09/2026")
    p.add(b, "Button", name="View")
    fl, docs = _flat(p)
    g = cards.find_groups(fl, docs, TEST_THETA)[0]
    present = {s: r["present"] for s, r in cards.roles(g).items()}
    assert sorted(present.values()) == [1, 2, 2, 2]       # name, status, button in both; the date in one


def test_header_nav_footer_siblings_are_no_group():
    p = Page()
    root = p.add(-1, "Group")
    head = p.add(root, "Group", cls="header")
    p.add(head, "Text", name="Portal")
    p.add(head, "Button", name="Menu")
    nav = p.add(root, "List", cls="nav")
    for name in ("Home", "Returns", "Help"):
        li = p.add(nav, "ListItem")
        p.add(li, "Hyperlink", name=name)
    foot = p.add(root, "Group", cls="footer")
    p.add(foot, "Hyperlink", name="Contact")
    p.add(foot, "Text", name="copyright line")
    fl, docs = _flat(p)
    assert cards.find_groups(fl, docs, TEST_THETA) == []


def test_nested_group_is_linked_to_its_card():
    p = Page()
    root = p.add(-1, "Group")
    for name in ("GSTR-1", "GSTR-3B"):
        tile = p.add(root, "Group", cls="tile")
        p.add(tile, "Text", name=name)
        inner = p.add(tile, "Group", cls="rows")
        for k in range(2):
            row = p.add(inner, "Group", cls="row")
            p.add(row, "Text", name=f"{name} line {k}")
            p.add(row, "Button", name="Edit")
    fl, docs = _flat(p)
    groups = cards.find_groups(fl, docs, TEST_THETA)
    outer = [g for g in groups if len(g["members"]) == 2 and g["parent_group"] is None]
    nested = [g for g in groups if g["parent_group"] is not None]
    assert outer and nested
    assert nested[0]["parent_group"] == groups.index(outer[0])


# ── §3.1 tables ───────────────────────────────────────────────────────────────

def _table_page():
    p = Page()
    root = p.add(-1, "Group")
    t = p.add(root, "Table", rect=[0, 0, 200, 80], ctype=CT_TABLE)
    head = p.add(t, "DataItem", rect=[0, 0, 200, 20], ctype=CT_DATAITEM)
    p.add(head, "HeaderItem", name="Name", rect=[0, 0, 100, 20], ctype=CT_HEADERITEM)
    p.add(head, "HeaderItem", name="Amount", rect=[100, 0, 100, 20], ctype=CT_HEADERITEM)
    for k, (who, amt) in enumerate([("Ravi", "1,200"), ("Mira", "850")]):
        row = p.add(t, "DataItem", rect=[0, 20 + k * 20, 200, 20], ctype=CT_DATAITEM)
        p.add(row, "DataItem", name=who, rect=[0, 20 + k * 20, 100, 20], ctype=CT_DATAITEM)
        p.add(row, "DataItem", name=amt, rect=[100, 20 + k * 20, 100, 20], ctype=CT_DATAITEM)
    return p


def test_table_is_a_table_group_with_column_slots():
    fl, docs = _flat(_table_page())
    groups = cards.find_groups(fl, docs, TEST_THETA)
    tb = [g for g in groups if g["kind"] == "table"]
    assert len(tb) == 1
    assert len(tb[0]["members"]) == 2
    slots = set(cards.roles(tb[0]))
    assert slots == {"Name", "Amount"}


# ── §4-5 slots, kind across captures ──────────────────────────────────────────

def test_cards_recur_across_captures_and_keep_their_name_slot():
    first = _tile_page(["IFF", "GSTR-2A"], "a")
    later = _tile_page(["GSTR-1", "GSTR-1A", "GSTR-2B", "GSTR-3B", "GSTR-2A"], "b")
    fl_a, docs_a = _flat(first)
    fl_b, docs_b = _flat(later)
    ga = cards.find_groups(fl_a, docs_a, TEST_THETA)
    gb = cards.find_groups(fl_b, docs_b, TEST_THETA)
    # r is per page: 1 of 2 names recurs on the short page (0.5), 1 of 5 on the long one (0.2).
    # The test value is 0.2 so both pages pass; the measured RHO decides this for real (plan section 8).
    cards.classify([("link", fl_a, ga), ("link", fl_b, gb)], rho=0.2)
    assert ga[0]["kind"] == "cards" and gb[0]["kind"] == "cards"
    assert ga[0]["name_slot"] == gb[0]["name_slot"]
    assert ga[0]["name_slot"].startswith("Text")


def test_one_capture_is_unknown_and_compared_as_template():
    fl, docs = _flat(_rows_page(3))
    gs = cards.find_groups(fl, docs, TEST_THETA)
    cards.classify([("link", fl, gs)], rho=TEST_RHO)
    assert gs[0]["kind"] == "unknown"
    fl2, docs2 = _flat(_rows_page(7, prefix="other"))
    gs2 = cards.find_groups(fl2, docs2, TEST_THETA)
    cmp = cards.compare_groups(gs[0], gs2[0])
    assert cmp["mode"] == "template" and cmp["pairs"] == []
    assert cmp["rows"] == (3, 7)


def test_list_rows_are_not_paired_even_with_equal_texts():
    fl, docs = _flat(_rows_page(3))
    gs = cards.find_groups(fl, docs, TEST_THETA)
    cards.classify([("link", fl, gs)], rho=TEST_RHO)
    cmp = cards.compare_groups(gs[0], gs[0])
    assert cmp["mode"] == "template" and cmp["pairs"] == []


# ── §6 compare ────────────────────────────────────────────────────────────────

def _cards_pair(first_names, later_names, later_status_tag="b"):
    fl_a, docs_a = _flat(_tile_page(first_names, "a"))
    fl_b, docs_b = _flat(_tile_page(later_names, later_status_tag))
    ga = cards.find_groups(fl_a, docs_a, TEST_THETA)[0]
    gb = cards.find_groups(fl_b, docs_b, TEST_THETA)[0]
    cards.classify([("link", fl_a, [ga]), ("link", fl_b, [gb])], rho=TEST_RHO)
    return ga, gb


def _names(group, pair):
    side = group["members"][pair] if pair is not None else None
    if side is None:
        return None
    return {it["text"] for it in side["items"] if it["slot"] == group["name_slot"]}


def test_iff_tile_is_not_paired_with_gstr1_tile():
    ga, gb = _cards_pair(["IFF", "GSTR-2A"], ["GSTR-1", "GSTR-2A"])
    out = cards.compare_groups(ga, gb)
    assert out["mode"] == "cards"
    for pr in out["pairs"]:
        if pr["a"] is not None and pr["b"] is not None:
            assert _names(ga, pr["a"]) == _names(gb, pr["b"])          # only equal names pair
    paired = [pr for pr in out["pairs"] if pr["pairing"] == "name" and pr["b"] is not None]
    assert len(paired) == 1                                             # GSTR-2A only


def test_two_tiles_against_five_pair_the_shared_ones_and_report_the_rest():
    ga, gb = _cards_pair(["GSTR-1", "GSTR-3B"], ["GSTR-1", "GSTR-1A", "GSTR-2B", "GSTR-3B", "GSTR-2A"])
    out = cards.compare_groups(ga, gb)
    assert sum(1 for pr in out["pairs"] if pr["a"] is not None and pr["b"] is not None) == 2
    assert sum(1 for pr in out["pairs"] if pr["b"] is None) == 0      # every latest tile is in the earlier page
    assert sum(1 for pr in out["pairs"] if pr["a"] is None) == 3      # three tiles only on the later page


def test_same_named_card_with_different_status_is_paired():
    ga, gb = _cards_pair(["GSTR-1", "GSTR-3B"], ["GSTR-1", "GSTR-2A"])
    out = cards.compare_groups(ga, gb)
    pairs = [pr for pr in out["pairs"] if pr["a"] is not None and pr["b"] is not None]
    assert len(pairs) == 1 and pairs[0]["pairing"] == "name"
    ta = {it["slot"]: it["text"] for it in ga["members"][pairs[0]["a"]]["items"]}
    tb = {it["slot"]: it["text"] for it in gb["members"][pairs[0]["b"]]["items"]}
    assert [s for s in ta if s in tb and ta[s] != tb[s]]              # the status text is the variable part


# ── determinism ───────────────────────────────────────────────────────────────

def test_same_input_same_output_including_shuffled_key_order():
    rec = _tile_page(["GSTR-1", "GSTR-2A", "GSTR-3B"], "x").read()
    reordered = json.loads(json.dumps(rec, sort_keys=True))
    first = cards.find_groups(*cards.load_flat(rec), TEST_THETA)
    again = cards.find_groups(*cards.load_flat(rec), TEST_THETA)
    other = cards.find_groups(*cards.load_flat(reordered), TEST_THETA)
    assert json.dumps(first, sort_keys=True) == json.dumps(again, sort_keys=True)
    assert json.dumps(first, sort_keys=True) == json.dumps(other, sort_keys=True)


# ── refusal until measured ────────────────────────────────────────────────────

def test_theta_is_refused_when_not_measured(monkeypatch):
    fl, docs = _flat(_tile_page(["GSTR-1", "GSTR-2A"], "x"))
    monkeypatch.setattr(cards, "THETA", None)
    try:
        cards.find_groups(fl, docs)
    except ValueError as e:
        assert "THETA" in str(e)
    else:
        raise AssertionError("find_groups must not run with an unmeasured THETA")


def test_measured_theta_is_the_middle_of_the_widest_gap():
    # 0.3333 and 0.5000 are the nearest measured values either side of the widest empty gap (plan 9.1)
    assert abs(cards.THETA - (0.3333 + 0.5000) / 2) < 0.001


# ── rho: recurrence report and CLI globs ──────────────────────────────────────

def test_recurrence_report_gives_r_per_slot(tmp_path, capsys):
    first = _tile_page(["IFF", "GSTR-2A"], "a").read()
    later = _tile_page(["GSTR-1", "GSTR-1A", "GSTR-2B", "GSTR-3B", "GSTR-2A"], "b").read()
    (tmp_path / "a.json").write_text(json.dumps(first), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps(later), encoding="utf-8")
    paths = cards._expand([str(tmp_path / "*.json")])
    assert len(paths) == 2                                          # the glob expands here, not in the shell
    cards.recurrence_report(paths, TEST_THETA)
    out = capsys.readouterr().out
    assert "captures=2" in out
    assert "r=0.50" in out and "r=0.20" in out                      # the short page and the long one


# ── §6.4 outside the groups, §6.2-6.3 card rows, §7 render overlay ───────────

def test_outside_flat_drops_members_and_keeps_order_without_groups():
    fl, docs = _flat(_tile_page(["GSTR-1", "GSTR-2A"], "x"))
    groups = cards.find_groups(fl, docs, TEST_THETA)
    kept = cards.outside_flat(fl, groups)
    member_texts = {it["text"] for g in groups for m in g["members"] for it in m["items"]}
    assert not any(e["text"] in member_texts for e in kept)               # group text is not an anchor outside
    assert all(e["parent"] < i for i, e in enumerate(kept))               # parents still point backwards
    no_group = cards.outside_flat(fl, [])
    assert [(e["key"], e["text"]) for e in no_group] == [(e["key"], e["text"]) for e in fl]


def test_outside_rows_do_not_pair_group_text_with_outside_text():
    # the same wording sits outside the tiles (a heading) and inside them: outside it must not pair
    p = _tile_page(["GSTR-1", "GSTR-2A"], "x")
    p.add(-1, "Text", name="Details of outward supplies of goods or services", rect=[0, 500, 300, 14])
    fl, docs = _flat(p)
    groups = cards.find_groups(fl, docs, TEST_THETA)
    kept = cards.outside_flat(fl, groups)
    texts = [e["text"] for e in kept if e["text"]]
    assert texts.count("Details of outward supplies of goods or services") == 1   # the heading only


def test_card_rows_pair_by_name_and_mark_the_rest(monkeypatch):
    import compare
    monkeypatch.setattr(cards, "RHO", TEST_RHO)
    fl_a, _ = _flat(_tile_page(["IFF", "GSTR-2A"], "a"))
    fl_b, _ = _flat(_tile_page(["GSTR-1", "GSTR-2A"], "b"))
    lrec = {"comp_page": True, "page": "fictional.example/returns", "nodes": fl_a}
    prec = {"comp_page": True, "page": "fictional.example/returns", "nodes": fl_b}
    rows, counts = compare.cards_rows(lrec, prec, "fictional.example/returns")
    assert set(compare.CARDS_CSV_FIELDS) <= set(rows[0])
    iff = [r for r in rows if r["card_name"] == "IFF"]
    assert iff and all(r["status"] == "only latest" for r in iff)
    paired = [r for r in rows if r["pairing"] == "name" and r["card_name"] == "GSTR-2A"]
    assert paired and {r["status"] for r in paired} <= {"fixed", "variable", "only latest", "only previous"}
    assert counts["group paired"] == 1


def test_card_rows_without_rho_compare_templates(monkeypatch):
    import compare
    monkeypatch.setattr(cards, "RHO", None)
    fl_a, _ = _flat(_rows_page(3))
    fl_b, _ = _flat(_rows_page(7, prefix="other"))
    lrec = {"comp_page": True, "page": "fictional.example/list", "nodes": fl_a}
    prec = {"comp_page": True, "page": "fictional.example/list", "nodes": fl_b}
    rows, counts = compare.cards_rows(lrec, prec, "fictional.example/list")
    assert counts["rho not measured: templates"] == 1
    summary = [r for r in rows if r["pairing"] == "rows"]
    assert summary and summary[0]["example_value"] == "latest 3 rows"
    assert summary[0]["previous_example"] == "previous 7 rows"
    assert not [r for r in rows if r["pairing"] == "name"]


def test_render_groups_box_each_member_and_mark_distinct_texts():
    import render
    p = _tile_page(["GSTR-1", "GSTR-2A"], "x")
    html, summary = render.build([p.nodes], {"kind": "read", "page": "fictional.example/p", "title": "T",
                                             "browser": "chrome", "source": "fixture.json", "label": ""},
                                 groups=True)
    data = json.loads(re.search(r"^const D = (.*);$", html, re.M).group(1))
    els = data["docs"][0]["els"]
    members = [e for e in els if e["t"] == "GroupMember"]
    assert len(members) == 2                                             # both tiles boxed
    assert any(e.get("gd") for e in els if e["kind"] == "text")          # the distinct texts are marked
    assert summary["sections"][0]["groups"] == 1
    plain, _ = render.build([p.nodes], {"kind": "read", "page": "fictional.example/p", "title": "T",
                                        "browser": "chrome", "source": "fixture.json", "label": ""})
    plain_data = json.loads(re.search(r"^const D = (.*);$", plain, re.M).group(1))
    assert not [e for e in plain_data["docs"][0]["els"] if e["t"] == "GroupMember"]   # off by default


# ── 2026-10-11 review fixes ──────────────────────────────────────────────────

def _top_tiles(names, disabled=()):
    """Tiles as DIRECT children of the document (parent -1), like the real GST dashboard. A disabled
    tile carries one extra class on its link and button."""
    p = Page()
    p.add(-1, "Group", cls="header")
    for k, name in enumerate(names):
        extra = " disableClick" if name in disabled else ""
        tile = p.add(-1, "Group", rect=[k * 120, 0, 110, 100])
        link = p.add(tile, "Hyperlink", name=f"About {name}", cls=extra.strip(), rect=[k * 120, 0, 100, 30])
        code = p.add(link, "Group", rect=[k * 120, 10, 100, 14])
        p.add(code, "Text", name=name, rect=[k * 120, 10, 100, 14])
        p.add(tile, "Button", name="View", cls="btn" + extra, rect=[k * 120, 70, 50, 20])
    return p


def test_top_level_siblings_form_a_group():
    fl, docs = _flat(_top_tiles(["GSTR-1", "GSTR-2A", "GSTR-3B"]))
    groups = cards.find_groups(fl, docs, TEST_THETA)
    tiles = [g for g in groups if g["parent"] == -1 and len(g["members"]) == 3]
    assert len(tiles) == 1


def test_a_class_on_an_outer_element_keeps_the_card_in_its_group_and_slots():
    fl, docs = _flat(_top_tiles(["GSTR-1", "GSTR-1A", "GSTR-2B"], disabled={"GSTR-1A"}))
    g = [g for g in cards.find_groups(fl, docs, TEST_THETA) if g["parent"] == -1][0]
    assert len(g["members"]) == 3
    assert not [it for m in g["members"] for it in m["items"] if it["slot"].startswith("own:")]


def test_disabled_card_pairs_by_name_on_real_shaped_pages():
    fl_a, docs_a = _flat(_top_tiles(["GSTR-1", "GSTR-1A", "GSTR-2B"], disabled={"GSTR-1A"}))
    fl_b, docs_b = _flat(_top_tiles(["GSTR-1A", "GSTR-2B", "GSTR-3B"], disabled={"GSTR-1A"}))
    ga = [g for g in cards.find_groups(fl_a, docs_a, TEST_THETA) if g["parent"] == -1]
    gb = [g for g in cards.find_groups(fl_b, docs_b, TEST_THETA) if g["parent"] == -1]
    cards.classify([("link", fl_a, ga, "A"), ("link", fl_b, gb, "B")], rho=TEST_RHO)
    out = cards.compare_groups(ga[0], gb[0])
    both = [pr for pr in out["pairs"] if pr["a"] is not None and pr["b"] is not None]
    assert len(both) == 2                                              # GSTR-1A and GSTR-2B


def test_no_generic_group_inside_a_table():
    p = Page()
    t = p.add(-1, "Table", ctype=CT_TABLE, rect=[0, 0, 300, 90])
    for r in range(3):
        row = p.add(t, "DataItem", ctype=CT_DATAITEM, rect=[0, r * 30, 300, 30])
        p.add(row, "DataItem", name=f"a{r}", ctype=CT_DATAITEM, rect=[0, r * 30, 150, 30])
        p.add(row, "DataItem", name=f"b{r}", ctype=CT_DATAITEM, rect=[150, r * 30, 150, 30])
    fl, docs = _flat(p)
    assert not [g for g in cards.find_groups(fl, docs, TEST_THETA) if g["source"] == "siblings"]


def test_same_client_captures_do_not_make_names():
    # two captures of ONE client: their values recur because they are the same client, not names
    fl_a, docs_a = _flat(_rows_page(3))
    fl_b, docs_b = _flat(_rows_page(3))
    ga = cards.find_groups(fl_a, docs_a, TEST_THETA)
    gb = cards.find_groups(fl_b, docs_b, TEST_THETA)
    cards.classify([("link", fl_a, ga, "same"), ("link", fl_b, gb, "same")], rho=TEST_RHO)
    assert ga[0]["kind"] == "unknown" and gb[0]["kind"] == "unknown"


def test_groups_are_matched_by_signature_not_by_a_loose_identity():
    # two top-level groups whose members are plain Groups: each must find its own partner
    fl_a, docs_a = _flat(_top_tiles(["GSTR-1", "GSTR-2A"]))
    fl_b, docs_b = _flat(_top_tiles(["GSTR-3B", "GSTR-2A"]))
    ga = [g for g in cards.find_groups(fl_a, docs_a, TEST_THETA) if g["parent"] == -1]
    gb = [g for g in cards.find_groups(fl_b, docs_b, TEST_THETA) if g["parent"] == -1]
    assert cards.match_group(ga[0], fl_a, gb, fl_b) == 0
