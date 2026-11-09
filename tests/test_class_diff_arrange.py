"""
SDIS pre-dev: the arrangement (tools/pre_dev/class_diff/arrange.py; design docs/sdis_experiments/cards-solution.md
section 8, WP1-WP3). Fictional fixtures only, built in this file, plus the three-browser align fixtures in
tests/class_diff_align/. The cut-offs under test are arrange.py's measured constants: OVL, TOLX, EDITS_PER_TOKEN.
"""

import copy
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "pre_dev" / "class_diff"))

import arrange as A  # noqa: E402
import cards  # noqa: E402
from core.sdis.keys import flatten  # noqa: E402

CT_TABLE = 50036
FIX = ROOT / "tests" / "class_diff_align"


class Page:
    """Builds a read's node list (the same shape test_class_diff_cards.py uses): add() returns the index."""

    def __init__(self):
        self.nodes = []

    def add(self, parent, typ, name="", cls="", rect=None, ctype=None):
        n = {"parent": parent, "type_name": typ, "name": name, "cls": cls, "rect": rect or [0, 0, 10, 10]}
        if ctype is not None:
            n["ctype"] = ctype
        self.nodes.append(n)
        return len(self.nodes) - 1

    def read(self):
        return {"page": "fictional.example/arrange", "title": "T", "docs": [self.nodes]}

    def flat(self):
        return flatten(self.read())


def _named_tiles(names, status_tag="s"):
    """Tiles as _tile_page makes them, but with the given codes (the IFF / GSTR-1 trap uses real names)."""
    p = Page()
    box = p.add(-1, "Group", rect=[0, 0, 600, 200])
    for k, name in enumerate(names):
        tile = p.add(box, "Group", rect=[k * 120, 0, 110, 100])
        p.add(tile, "Text", name=name, rect=[k * 120, 10, 100, 14])
        p.add(tile, "Text", name="Details of outward supplies", rect=[k * 120, 30, 100, 14])
        p.add(tile, "Text", name=f"{status_tag}-{name}", rect=[k * 120, 50, 100, 14])
        p.add(tile, "Button", name="View", rect=[k * 120, 70, 50, 20])
    return p


def _cn(typ, text="", rect=(0, 0, 10, 10), parent=-1, present=True, values=None, origin="anchor", **node):
    """A comp-page node (the shape --comp writes): the box and flags live in the node."""
    return {"node": dict(rect=list(rect), **node), "type": typ, "text": text, "parent": parent,
            "present": present, "values": values or [], "origin": origin}


# ── fixtures ──────────────────────────────────────────────────────────────────

def _itr_card(p, parent, x, y, optional=False, wrapped=False):
    """One card shaped like the ITR page: 27 leaves (28 with the optional link). Title, status column
    (label above value), a 3-step timeline, 5 field rows, a link, and an action column (link + 3 buttons)."""
    card = p.add(parent, "Group", rect=[x, y, 1000, 360])
    p.add(card, "Text", name=f"title {y}", rect=[x, y, 300, 38 if wrapped else 18])
    p.add(card, "Text", name="Status", rect=[x + 700, y + 30, 80, 16])
    p.add(card, "Text", name=f"ok {y}", rect=[x + 700, y + 50, 80, 16])
    for i in range(3):
        yy = y + 90 + 40 * i
        p.add(card, "Text", name="icon", rect=[x, yy, 16, 16])
        p.add(card, "Text", name=f"step {i}", rect=[x + 30, yy, 200, 18])
        p.add(card, "Text", name=f"date {y} {i}", rect=[x + 30, yy + 20, 200, 14])
    for i in range(5):
        yy = y + 220 + 18 * i
        p.add(card, "Text", name=f"label {i}", rect=[x, yy, 150, 14])
        p.add(card, "Text", name=f"value {y} {i}", rect=[x + 160, yy, 150, 14])
    if optional:
        p.add(card, "Hyperlink", name="extra link", rect=[x, y + 320, 150, 16])
    p.add(card, "Hyperlink", name="Open", rect=[x, y + 330, 200, 16])
    p.add(card, "Hyperlink", name="More", rect=[x + 600, y + 330, 120, 16])
    for i in range(3):
        p.add(card, "Button", name=f"btn {i}", rect=[x + 740 + 40 * i, y + 330, 30, 20])
    return card


def _itr_page(n=5, optional_in=(), wrapped_in=()):
    p = Page()
    root = p.add(-1, "Group", rect=[0, 0, 1100, 2000])
    box = p.add(root, "Group", rect=[0, 0, 1100, 2000])
    for k in range(n):
        _itr_card(p, box, 0, k * 388, optional=k in optional_in, wrapped=k in wrapped_in)
    return p


def _tile_page(n, status_tag="s"):
    """n tiles of four leaves each: a code, a description that repeats on every tile, a status, a button."""
    p = Page()
    box = p.add(-1, "Group", rect=[0, 0, 600, 200])
    for k in range(n):
        tile = p.add(box, "Group", rect=[k * 120, 0, 110, 100])
        p.add(tile, "Text", name=f"TILE{k}", rect=[k * 120, 10, 100, 14])
        p.add(tile, "Text", name="Details of outward supplies", rect=[k * 120, 30, 100, 14])
        p.add(tile, "Text", name=f"{status_tag}{k}", rect=[k * 120, 50, 100, 14])
        p.add(tile, "Button", name="View", rect=[k * 120, 70, 50, 20])
    return p


def _rows_only_page():
    """Three cards of four rows each, as plain div rows with no card wrapper. The row label is the same on
    every card (it repeats by row index, never within one card); the value differs everywhere."""
    p = Page()
    box = p.add(-1, "Group", rect=[0, 0, 400, 600])
    for r in range(12):
        y = (r // 4) * 140 + (r % 4) * 28
        row = p.add(box, "Group", rect=[0, y, 300, 26])
        p.add(row, "Text", name=f"Label {r % 4}", rect=[0, y, 100, 14])
        p.add(row, "Text", name=f"value {r}", rect=[120, y, 100, 14])
    return p


def _firefox_dashboard():
    """Five tiles, shaped the way Firefox exposes them (prototype s8): tile 2 is a loose top-level tile
    (its parts lie at the top level, bug 7), tile 3 has an extra inner Group (bug 8). Four unrelated
    single-text boxes sit around them (bug 9)."""
    p = Page()
    p.add(-1, "Group", cls="header", rect=[0, 0, 600, 40])
    p.add(-1, "ComboBox", name="English", rect=[500, 50, 80, 20])
    p.add(-1, "Button", name="File Returns", rect=[10, 50, 100, 20])
    p.add(-1, "Text", name="ticker ticker ticker", rect=[10, 80, 400, 16])
    p.add(-1, "Text", name="* mandatory fields", rect=[10, 100, 150, 16])
    names = ["GSTR-1", "GSTR-1A", "GSTR-2B", "GSTR-3B", "GSTR-2A"]
    for k, name in enumerate(names):
        x, y = k * 120, 140
        holder = -1 if k == 2 else p.add(-1, "Group", rect=[x, y, 110, 100])
        link_holder = p.add(holder, "Group", rect=[x, y, 110, 40]) if k == 3 else holder
        link = p.add(link_holder, "Hyperlink", name=f"About {name}", rect=[x, y, 100, 30])
        code = p.add(link, "Group", rect=[x, y + 10, 100, 14])
        p.add(code, "Text", name=name, rect=[x, y + 10, 100, 14])
        p.add(holder, "Text", name="Status-", rect=[x, y + 40, 40, 14])
        p.add(holder, "Text", name=f"state {k}", rect=[x + 45, y + 40, 50, 14])
        p.add(holder, "Button", name="View", rect=[x, y + 70, 50, 20])
    return p


def _no_repeat_page():
    p = Page()
    box = p.add(-1, "Group", rect=[0, 0, 600, 600])
    kinds = ["Text", "Button", "Hyperlink", "Image", "ComboBox", "Edit"]
    for k, typ in enumerate(kinds):
        p.add(box, typ, name=f"unique {k}", rect=[k * 50, (k % 3) * 60, 40, 20])
    return p


def _table_page():
    p = Page()
    box = p.add(-1, "Group", rect=[0, 0, 500, 300])
    t = p.add(box, "Table", rect=[0, 0, 400, 200], ctype=CT_TABLE)
    for r in range(3):
        for c in range(2):
            p.add(t, "Text", name=f"cell {r} {c}", rect=[c * 150, r * 30, 140, 14])
    p.add(box, "Text", name="after the table", rect=[0, 220, 200, 14])
    return p


def _steps_page():
    """Hand-made boxes for the nine step classes, one pair each."""
    return [A.Leaf(0, "Text", (0, 0, 10, 10), "a"), A.Leaf(1, "Text", (0, 0, 10, 10), "b")]


def _arr(p):
    return A.arrange(p.flat())


# ── WP1: leaves, tokens, steps ────────────────────────────────────────────────

def test_leaf_selection_drops_zero_box_absent_blank_and_text_less_containers():
    fl = [
        _cn("Text", "keep"),
        _cn("Text", "zero box", rect=(0, 0, 0, 10)),
        _cn("Text", "gone", present=False),
        _cn("Group", ""),                           # text-less container
        _cn("Text", ""),                            # blank text
        _cn("Image", ""),                           # look-only: kept
        _cn("Button", ""),                          # look-only: kept
    ]
    lv = A.leaves(fl)
    assert [l.node for l in lv] == [0, 5, 6]
    assert [l.kind for l in lv] == ["Text", "Image", "Button"]


def test_ticking_and_attached_flags_come_from_values_and_origin():
    fl = [
        _cn("Text", "a", values=["1", "2"]),
        _cn("Text", "b", origin="attached"),
        _cn("Text", "c", values=["7"]),
    ]
    lv = A.leaves(fl)
    assert [(l.ticking, l.attached) for l in lv] == [(True, False), (False, True), (False, False)]


def test_a_table_is_one_leaf_and_its_cells_are_not_leaves():
    lv = A.leaves(_table_page().flat())
    assert [l.kind for l in lv] == ["Table", "Text"]
    assert lv[1].text == "after the table"


def test_leaves_keep_document_order():
    lv = A.leaves(_itr_page(n=2).flat())
    assert [l.node for l in lv] == sorted(l.node for l in lv)


def test_step_classes_at_their_boundaries(monkeypatch):
    a = (100, 100, 50, 20)                          # bottom edge at 120
    # same line: the vertical overlap must be MORE than OVL (4) to count
    assert A.step(a, (200, 116, 50, 20)) == "DR"   # overlap 4 = OVL: another line, below and to the right
    assert A.step(a, (200, 115, 50, 20)) == "R"    # overlap 5: same line, starts past the right edge
    assert A.step(a, (-100, 115, 50, 20)) == "L"   # same line, ends before the left edge
    assert A.step(a, (120, 115, 50, 20)) == "O"    # same line, overlapping
    # another line: an x difference up to TOLX (5) is the same column
    assert A.step(a, (105, 130, 50, 20)) == "D"    # dx 5
    assert A.step(a, (106, 130, 50, 20)) == "DR"   # dx 6
    assert A.step(a, (94, 130, 50, 20)) == "DL"    # dx -6
    assert A.step(a, (105, 60, 50, 20)) == "U"
    assert A.step(a, (50, 60, 50, 20)) == "UL"
    assert A.step(a, (150, 60, 50, 20)) == "UR"


def test_last_leaf_gets_the_end_step():
    lv = [A.Leaf(0, "Text", (0, 0, 10, 10), "a"), A.Leaf(1, "Button", (20, 0, 10, 10), "")]
    assert A.tokens(lv) == [("Text", "R"), ("Button", "END")]


def test_edge_chrome_firefox_token_strings_are_equal_on_both_clients():
    for client in ("A", "B"):
        strings = []
        for browser in ("msedge", "chrome", "firefox"):
            rec = json.loads((FIX / f"client_{client}_{browser}.json").read_text(encoding="utf-8"))
            strings.append(A.tokens(A.leaves(flatten(rec))))
        assert strings[0] == strings[1] == strings[2], f"client {client}"


def test_widest_empty_bands_and_cut_checks():
    assert A.empty_bands([0, 0, 8, 9, 30]) == [(9, 30), (0, 8)]
    assert A.in_empty_band(4, [(0, 8)])
    assert not A.in_empty_band(8, [(0, 8)])
    assert A.band_of(5, [(1, 10), (20, 30)]) == (1, 10)


# ── WP2: runs, phase, extension, nesting, label witness ──────────────────────

def test_itr_like_page_is_five_cards_of_27_leaves_and_phase_starts_at_the_title():
    arr = _arr(_itr_page(n=5))
    top = [g for g in arr.groups if g.parent is None and g.period == 27]
    assert len(top) == 1
    g = top[0]
    assert g.members == [(27 * k, 27 * k + 27) for k in range(5)]


def test_optional_link_in_one_card_is_absorbed_and_becomes_an_own_slot():
    arr = _arr(_itr_page(n=5, optional_in=(2,)))
    top = [g for g in arr.groups if g.period == 27 and g.parent is None][0]
    lengths = [b - a for a, b in top.members]
    assert lengths.count(28) == 1 and lengths.count(27) == 4
    own = [k for k in top.slots if isinstance(k, str) and k.startswith("own:")]
    assert own, "the optional link must get an own: slot"
    present = [sum(1 for i in top.slots[k] if i is not None) for k in own]
    assert present == [1]
    assert all(top.roles[k] == "single" for k in own)


def test_one_wrapped_title_keeps_all_five_cards():
    arr = _arr(_itr_page(n=5, wrapped_in=(1,)))
    top = [g for g in arr.groups if g.period == 27 and g.parent is None][0]
    assert len(top.members) == 5


def test_nested_field_rows_and_timeline_steps_are_found_inside_the_cards():
    arr = _arr(_itr_page(n=5))
    nested = [g for g in arr.groups if g.parent is not None]
    rows = [g for g in nested if g.period == 2 and len(g.members) == 5]
    steps = [g for g in nested if g.period == 3 and len(g.members) == 3]
    assert len(rows) == 5 and len(steps) == 5
    assert all(g.parent[0] == 0 for g in nested)


def test_firefox_shaped_tiles_are_all_five_and_the_loose_boxes_are_not_a_group():
    arr = _arr(_firefox_dashboard())
    tiles = [g for g in arr.groups if g.period == 5]
    assert len(tiles) == 1
    assert len(tiles[0].members) == 5
    assert all(b - a == 5 for a, b in tiles[0].members)
    member_leaves = {i for a, b in tiles[0].members for i in range(a, b)}
    loose = [l for l in arr.leaves if l.text in ("ticker ticker ticker", "* mandatory fields", "English", "File Returns")]
    assert loose and all(arr.leaves.index(l) not in member_leaves for l in loose)


def test_two_tiles_form_a_group_with_the_last_step_free():
    arr = _arr(_tile_page(2))
    assert len(arr.groups) == 1
    assert len(arr.groups[0].members) == 2


def test_rows_only_cards_merge_into_three_cards_by_the_label_witness():
    arr = _arr(_rows_only_page())
    g = [g for g in arr.groups if g.period == 2 and g.parent is None][0]
    assert len(g.members) == 3
    assert [b - a for a, b in g.members] == [8, 8, 8]
    assert g.merged == 4


def test_a_page_with_no_repeat_has_no_group():
    assert _arr(_no_repeat_page()).groups == []


def test_one_leaf_periods_are_never_groups():
    p = Page()
    for k in range(4):
        p.add(-1, "Text", name=f"lone {k}", rect=[0, k * 40, 100, 14])
    assert all(g.period >= A.MIN_PERIOD for g in _arr(p).groups)


def test_phase_counts_only_the_members_that_really_repeat_from_the_rotation():
    # four identical 3-token members, then a fifth that differs in its middle token: no rotation repeats 5 times
    S = [("Text", "R"), ("Text", "D"), ("Button", "R")] * 4 + [("Text", "R"), ("Image", "D"), ("Button", "R")]
    lv = [A.Leaf(i, k, (i * 20, 0, 10, 10), "") for i, (k, _) in enumerate(S)]
    rot, kk = A.phase(lv, S, 0, 3, 5)
    assert kk == A._members_from(S, rot, 3, 5)
    assert 2 <= kk <= 4


# ── WP3: slots, roles, kinds, corpus R ───────────────────────────────────────

def test_roles_label_distinct_look_and_ticking():
    arr = _arr(_itr_page(n=5))
    top = [g for g in arr.groups if g.period == 27 and g.parent is None][0]
    assert top.roles[0] == "distinct"          # the title
    assert top.roles[1] == "label"             # the status label, the same in every card
    assert "distinct" in top.roles.values()


def test_a_ticking_leaf_makes_its_slot_ticking():
    lv = [A.Leaf(0, "Text", (0, 0, 10, 10), "x", ticking=True), A.Leaf(1, "Text", (0, 0, 10, 10), "y")]
    assert A.role_of(lv, [0, 1]) == "ticking"


def test_name_slot_needs_a_cut_and_never_a_ticking_slot():
    g = A.Group(members=[(0, 2), (2, 4)], period=2, area=0, roles={0: "distinct", 1: "ticking"})
    assert A.name_slot(g, {0: 1.0, 1: 1.0}, cut=None) is None          # cut-off not set: no names
    assert A.name_slot(g, {0: 1.0, 1: 1.0}, cut=0.5) == 0             # a test value: the distinct slot wins
    assert A.name_slot(g, {0: 0.2, 1: 1.0}, cut=0.5) is None          # R under the cut
    g.roles = {0: "ticking", 1: "distinct"}
    assert A.name_slot(g, {0: 1.0, 1: 1.0}, cut=0.5) == 1


def test_name_tie_goes_to_the_earliest_position():
    g = A.Group(members=[(0, 3), (3, 6)], period=3, area=0, roles={0: "distinct", 2: "distinct"})
    assert A.name_slot(g, {0: 0.9, 2: 0.95}, cut=0.5) == 0


def test_corpus_r_formula_on_three_fixtures():
    assert A.recurrence([("A", 2, {"s": ["x", "y"]}), ("B", 5, {"s": ["x", "a", "b", "c", "d"]})], "s") == 0.5
    closed = [("A", 5, {"s": list("abcde")}), ("B", 5, {"s": list("abcde")})]
    assert A.recurrence(closed, "s") == 1.0
    fresh = [("A", 2, {"s": ["a", "b"]}), ("B", 2, {"s": ["c", "d"]})]
    assert A.recurrence(fresh, "s") == 0.0


def test_same_client_captures_make_no_name():
    same = [("A", 2, {"s": ["x", "y"]}), ("A", 5, {"s": ["x", "a", "b", "c", "d"]})]
    assert A.recurrence(same, "s") is None


def test_fields_when_the_count_is_the_same_and_list_when_it_varies():
    parent = A.Group(members=[(0, 30), (30, 60)], period=27, area=0)
    same = [A.Group(members=[(1, 3)] * 5, period=2, area=0, parent=(0, 0)),
            A.Group(members=[(31, 33)] * 5, period=2, area=0, parent=(0, 1))]
    A.assign_kinds([parent] + same)
    assert [g.kind for g in same] == ["fields", "fields"]

    varies = [A.Group(members=[(1, 3)] * 5, period=2, area=0, parent=(0, 0)),
              A.Group(members=[(31, 33)] * 7, period=2, area=0, parent=(0, 1))]
    A.assign_kinds([parent] + varies)
    assert [g.kind for g in varies] == ["list", "list"]


def test_cards_only_when_a_name_slot_is_known():
    top = A.Group(members=[(0, 2), (2, 4)], period=2, area=0, roles={0: "distinct"})
    A.assign_kinds([top], R_by_group={0: {0: 1.0}}, cut=0.5)
    assert top.kind == "cards"
    top.kind = "unknown"
    A.assign_kinds([top], R_by_group={0: {0: 1.0}}, cut=None)
    assert top.kind == "unknown"


# ── determinism and the measured cut-offs ────────────────────────────────────

def _summary(arr):
    return [(g.members, g.period, g.merged, g.parent, sorted(map(str, g.slots)), g.roles, g.kind) for g in arr.groups]


def _shuffled_keys(fl, seed):
    rnd = random.Random(seed)
    out = []
    for e in fl:
        items = list(e.items())
        rnd.shuffle(items)
        out.append(dict(items))
    return out


def test_same_input_same_output_including_shuffled_key_order():
    fl = _itr_page(n=5, optional_in=(2,)).flat()
    first = _summary(A.arrange(copy.deepcopy(fl)))
    second = _summary(A.arrange(copy.deepcopy(fl)))
    shuffled = _summary(A.arrange(_shuffled_keys(fl, 7)))
    assert first == second == shuffled


def test_the_measured_constants_are_the_middle_of_their_bands():
    assert A.OVL == 4 and A.TOLX == 5
    assert A.NAME_CUT is None
    assert 0.037 < A.EDITS_PER_TOKEN < 0.25


# ── WP4: --comp --arrange (compare.py) ───────────────────────────────────────

def _compare_arrange(latest, previous, monkeypatch=None, name_cut=None):
    import compare
    if name_cut is not None:
        monkeypatch.setattr(A, "NAME_CUT", name_cut)
    return compare.arrange_rows(latest.read(), previous.read(), "fictional.example/arrange", "A", "B")


def test_iff_and_gstr1_trap_is_never_paired_by_name(monkeypatch):
    latest = _named_tiles(["IFF", "GSTR-2A"], "x")
    previous = _named_tiles(["GSTR-1", "GSTR-1A", "GSTR-2B", "GSTR-3B", "GSTR-2A"], "x")
    rows, counts = _compare_arrange(latest, previous, monkeypatch, name_cut=0.5)
    assert counts.get("group paired by name") == 1
    paired = [r for r in rows if r["pairing"] == "name" and r["status"] in ("fixed", "variable")]
    assert {r["card_name"] for r in paired} == {"GSTR-2A"}           # the shared tile, and only it
    assert all(r["card_name"] != "IFF" for r in paired)
    assert any(r["card_name"] == "IFF" and r["status"] == "only latest" for r in rows)


def test_without_a_name_cut_equal_counts_are_never_paired_by_place():
    # NAME_CUT is not set: IFF must not be paired with GSTR-1 just because both pages show two tiles
    for latest, previous in ((["IFF", "GSTR-2A"], ["GSTR-1", "GSTR-2A"]),
                             (["GSTR-1", "GSTR-3B", "IFF"], ["GSTR-1", "IFF", "GSTR-3B"])):
        rows, counts = _compare_arrange(_named_tiles(latest, "x"), _named_tiles(previous, "x"))
        assert counts.get("group compared by template") == 1
        assert not counts.get("group paired by place")
        assert not any(r["pairing"] == "place" for r in rows)


def test_same_named_card_with_a_different_status_is_paired_and_the_status_is_variable(monkeypatch):
    latest = _named_tiles(["GSTR-1", "GSTR-3B"], "a")
    previous = _named_tiles(["GSTR-1", "GSTR-3B", "GSTR-2A"], "b")
    rows, _ = _compare_arrange(latest, previous, monkeypatch, name_cut=0.5)
    status = [r for r in rows if r["card_name"] == "GSTR-1" and r["slot"] == "2"]
    assert status and all(r["status"] == "variable" for r in status)


def test_a_list_with_different_row_counts_is_compared_by_template():
    # no name is known (NAME_CUT is not set): the cards differ in count, so the group is a list
    rows, counts = _compare_arrange(_itr_page(n=3), _itr_page(n=7))
    assert counts.get("group compared by template") == 1
    assert not any(r["pairing"] == "place" and r["group"] != "" for r in rows)
    assert any(r["status"] == "row counts" and r["example_value"] == "latest 3 members" for r in rows)


def test_every_leaf_of_a_group_is_reported_once_across_the_pair():
    import compare
    latest, previous = _itr_page(n=4), _itr_page(n=5)
    rows, _ = compare.arrange_rows(latest.read(), previous.read(), "p", "A", "B")
    la, pa = A.arrange(latest.flat()), A.arrange(previous.flat())
    grouped_l = {i for g in la.groups if g.parent is None for a, b in g.members for i in range(a, b)}
    grouped_p = {i for g in pa.groups if g.parent is None for a, b in g.members for i in range(a, b)}
    # the template rows count every member leaf of the group once (the rows are per slot, not per leaf)
    per_slot_l = sum(len([i for i in g.slots[s] if i is not None]) for g in la.groups if g.parent is None for s in g.slots)
    assert per_slot_l == len(grouped_l)
    assert any(r["pairing"] == "template" for r in rows)
    assert len(grouped_p) == sum(len([i for i in g.slots[s] if i is not None]) for g in pa.groups if g.parent is None for s in g.slots)


def test_group_text_never_anchors_text_outside_its_group():
    import compare
    fl = _itr_page(n=5).flat()
    lv = A.arrange(fl).leaves
    inside = {lv[i].node for i in range(len(lv))}
    kept = compare._without(fl, inside)
    assert all(e["text"] == "" or e["node"]["rect"][1] >= 0 for e in kept)
    assert not any(e["node"].get("name", "").startswith("title") for e in kept if e["text"])


# ── WP5: the overlay (render.py --arrange) ───────────────────────────────────

def test_render_arrange_boxes_are_off_by_default_and_drawn_when_asked():
    import render
    docs = [_itr_page(n=3).nodes]
    html_off, sum_off = render.build(docs, {"title": "T"})
    html_on, sum_on = render.build(docs, {"title": "T"}, arrange=True)
    assert "ArrangeMember" not in html_off
    assert "ArrangeMember" in html_on
    assert "arrange_groups" not in sum_off["sections"][0]
    assert sum_on["sections"][0]["arrange_groups"] >= 1
