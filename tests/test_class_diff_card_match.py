"""
SDIS pre-dev: card match inside a record set (tools/pre_dev/class_diff/card_match.py, via shape_map.py).

Fictional fixtures only: the node lists are built in this file.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "pre_dev" / "class_diff"))

import card_match  # noqa: E402
import shape_map  # noqa: E402


def _page(extra_button=None, drop_date=None, n_cards=4):
    """A column of cards. Each card: a head block (period value), a field block (label 'Status:' + a status
    word, label 'Filed on:' + a date) and a button. extra_button: card index that gets one more button.
    drop_date: card index whose date is missing."""
    doc = []

    def add(parent, typ, rect, cls="", name=""):
        doc.append({"parent": parent, "type_name": typ, "rect": rect, "cls": cls, "name": name})
        return len(doc) - 1

    root = add(-1, "Group", [0, 0, 800, 300 * n_cards + 20], "page")
    for c in range(n_cards):
        y = 10 + 300 * c
        card = add(root, "Group", [10, y, 700, 280], "card")
        head = add(card, "Group", [20, y + 10, 300, 40], "head")
        add(head, "Text", [30, y + 15, 120, 20], name=f"A.Y. 20{20 + c}-{21 + c}")
        fields = add(card, "Group", [20, y + 60, 300, 120], "fields")
        add(fields, "Text", [30, y + 65, 100, 20], name="Status:")
        add(fields, "Text", [140, y + 65, 100, 20], name=("Processed", "Filed", "Verified", "Pending")[c % 4])
        add(fields, "Text", [30, y + 95, 100, 20], name="Filed on:")
        if c != drop_date:
            add(fields, "Text", [140, y + 95, 100, 20], name=f"0{c + 1}/07/2026")
        acts = add(card, "Group", [20, y + 200, 300, 40], "acts")
        add(acts, "Button", [30, y + 205, 80, 30], name="Download")
        if c == extra_button:
            add(acts, "Button", [120, y + 205, 80, 30], name="View more")
    return doc


def _match(doc):
    inf = shape_map.infer(shape_map.page_containers(doc))
    sets = [s for s in inf["sets"] if s["tail"].endswith("card")]
    assert len(sets) == 1
    return sets[0]["match"]


def test_identical_cards_match_fully_in_every_layer():
    m = _match(_page())
    assert m["layer_names"] == ["composition", "adjacency"]
    assert m["overall"] == 100.0 and set(m["layers"].values()) == {100.0}
    assert all(c["nodes"] == 6 and c["overall"] == 100.0 for c in m["cards"])
    assert m["cards"][0]["kinds"] == {"button": 1, "date": 1, "label": 2, "period": 1, "text": 1}


def test_an_extra_button_lowers_only_that_cards_pairs():
    m = _match(_page(extra_button=3))
    p = m["pairs"]
    assert p["0-1"]["overall"] == 100.0
    # 7 vs 6 nodes, same kinds otherwise: composition 6/7
    assert p["0-3"]["layers"]["composition"] == round(100 * 6 / 7, 1)
    # 6 aligned pairs; the shared button is last in card 0 but has the extra button after it in card 3,
    # so it loses its right neighbour (2/3); the extra one is unmatched: (5 + 2/3) / 7
    assert p["0-3"]["layers"]["adjacency"] == round(100 * (5 + 2 / 3) / 7, 1)
    assert m["cards"][3]["overall"] < m["cards"][0]["overall"]
    st = m["structure"]
    assert st["ref"] == 0, "the medoid: first of the best-matching cards"
    assert len(st["extras"]) == 1
    ex = st["extras"][0]
    assert {k: ex[k] for k in ("kind", "path", "cards", "count")} == {"kind": "button", "path": "Group.acts / Button",
                                                                     "cards": [3], "count": 1}
    assert ex["node_cards"] == [3] and len(ex["nodes"]) == 1


def test_a_missing_node_breaks_its_neighbours_adjacency():
    m = _match(_page(drop_date=1))
    p = m["pairs"]["0-1"]
    assert p["layers"]["composition"] == round(100 * 5 / 6, 1)
    # 5 aligned pairs; the label before the missing date and the button after it lose one neighbour each
    assert p["layers"]["adjacency"] == round(100 * (3 + 2 * (2 / 3)) / 6, 1)


def test_structure_finds_labels_values_and_blocks():
    st = _match(_page(drop_date=2))["structure"]
    kinds = [(s["kind"], s["role"], s["found"]) for s in st["slots"]]
    assert kinds == [("period", "value", 4), ("label", "label", 4), ("text", "value", 4),
                     ("label", "label", 4), ("date", "value", 3), ("button", "label", 4)]
    assert [s["label"] for s in st["slots"]] == [None, None, 1, None, 3, None], "a value's label: nearest label before it, same block"
    assert st["blocks"] == [[0], [1, 2, 3, 4], [5]]
    assert st["always"] == 5 and st["labels"] == 3 and st["values"] == 3


def test_slots_list_one_node_per_card_for_the_picker():
    doc = _page(drop_date=2, extra_button=1)
    st = _match(doc)["structure"]
    pc = shape_map.page_containers(doc)
    for sl in st["slots"]:
        assert len(sl["nodes"]) == 4
        assert sum(k is not None for k in sl["nodes"]) == sl["found"]
    date = st["slots"][4]
    assert date["kind"] == "date" and date["nodes"][2] is None, "card 2 has no date"
    names = [doc[k]["name"] for k in date["nodes"] if k is not None]
    assert names == ["01/07/2026", "02/07/2026", "04/07/2026"], "the slot's nodes are the dates, in card order"
    ex = st["extras"][0]
    assert ex["node_cards"] == [1] and doc[ex["nodes"][0]]["name"] == "View more"
    # card_sets: what render.py --cards draws (node indexes and paths only)
    cs = [c for c in shape_map.card_sets(shape_map.infer(pc)) if c["tail"].endswith("card")]
    assert len(cs) == 1 and len(cs[0]["cards"]) == 4 and cs[0]["ref"] == st["ref"]
    assert [s["nodes"] for s in cs[0]["slots"]] == [s["nodes"] for s in st["slots"]]
    assert cs[0]["slots"][4]["label"] == 3 and cs[0]["extras"][0]["node_cards"] == [1]
    assert "Status" not in json.dumps(cs), "no page text in the card sets"


def test_match_groups_chain_through_a_shared_card():
    scores = {"0-2": 91.0, "2-5": 85.0, "0-5": 62.0, "1-3": 80.0}          # every other pair: 40
    pairs = {f"{i}-{j}": {"overall": scores.get(f"{i}-{j}", 40.0)} for i in range(6) for j in range(i + 1, 6)}
    groups = card_match.match_groups(6, pairs, 80)
    assert [g["cards"] for g in groups] == [[0, 2, 5], [1, 3], [4]]
    # 0 and 5 do not match directly (62) but both match 2: one group, and pair_min shows it is a chain
    assert groups[0]["link_min"] == 85.0 and groups[0]["pair_min"] == 62.0
    assert groups[1]["link_min"] == 80.0 == groups[1]["pair_min"], "a pair at the minimum is a link"
    assert groups[2]["link_min"] is None and groups[2]["pair_min"] is None, "a card alone"
    assert [g["cards"] for g in card_match.match_groups(6, pairs, 95)] == [[0], [1], [2], [3], [4], [5]]


def test_a_sets_cards_carry_their_match_group():
    doc = _page(extra_button=3)                      # card 3 against the others: 83.3 %
    pc = shape_map.page_containers(doc)
    m = shape_map.infer(pc)["sets"][0]["match"]
    assert m["group_min"] == card_match.GROUP_MIN == 80.0
    assert [g["cards"] for g in m["groups"]] == [[0, 1, 2, 3]] and {c["group"] for c in m["cards"]} == {0}
    inf = shape_map.infer(pc, group_min=90)
    m = inf["sets"][0]["match"]
    assert [g["cards"] for g in m["groups"]] == [[0, 1, 2], [3]]
    assert [c["group"] for c in m["cards"]] == [0, 0, 0, 1]
    rep = "\n".join(shape_map.report(inf))
    assert "group 1: cards 0, 1, 2" in rep and "group 2: cards 3  (alone)" in rep
    html = shape_map.build_html(pc, inf, {})
    assert "Match groups" in html


def test_kind_of_text_inside_link_and_button():
    doc = [{"parent": -1, "type_name": "Group", "rect": [0, 0, 400, 400]}]

    def add(parent, typ, name=""):
        doc.append({"parent": parent, "type_name": typ, "rect": [10, 10 + 20 * len(doc), 100, 18], "name": name})
        return len(doc) - 1
    link = add(0, "Hyperlink", "Open")
    add(link, "Text", "Open")
    btn = add(0, "Button", "Go")
    add(btn, "Text", "Go")
    add(0, "Text", "42")
    pc = shape_map.page_containers(doc)
    kids = {}
    for n in pc["nodes"]:
        if n["p"] >= 0:
            kids.setdefault(n["p"], []).append(n["i"])
    leaves = card_match.card_leaves(0, pc["nodes"], pc["flat"], kids)
    assert [x["kind"] for x in leaves] == ["link", "button", "number"]


def test_html_carries_the_match_but_no_text_and_same_bytes():
    pc = shape_map.page_containers(_page(extra_button=1))
    html = shape_map.build_html(pc, shape_map.infer(pc), {"page": "fictional.example/p", "source": "fixture"})
    pc2 = shape_map.page_containers(_page(extra_button=1))
    assert html == shape_map.build_html(pc2, shape_map.infer(pc2), {"page": "fictional.example/p", "source": "fixture"})
    data = json.loads(re.search(r"^const D = (.*);$", html, re.M).group(1))
    assert data["sets"][0]["match"]["structure"]["slots"]
    blob = json.dumps(data)
    for word in ("Status:", "Processed", "Verified", "07/2026", "Download", "View more", "A.Y."):
        assert word not in blob, "kinds, paths and percentages only, never a text"
    assert "Card match" in html and "matchPanel" in html


def test_no_match_leaves_the_shape_map_as_before():
    pc = shape_map.page_containers(_page())
    assert all(s["match"] is None for s in shape_map.infer(pc, match=False)["sets"])
    assert not any("card match" in line for line in shape_map.report(shape_map.infer(pc, match=False)))


def test_views_window_card_match_view():
    import views_ui
    by = {v[0]: v for v in views_ui.VIEWS}
    assert by[views_ui.SHAPE_MAP][1:3] == ("shape_map.py", ["--no-match"])
    assert by[views_ui.CARD_MATCH][1:3] == ("shape_map.py", [])
    both = [by[views_ui.SHAPE_MAP], by[views_ui.CARD_MATCH]]
    assert [v[0] for v in views_ui.picked(both)] == [views_ui.CARD_MATCH], "same page: run it once"
    assert [v[0] for v in views_ui.picked(both[:1])] == [views_ui.SHAPE_MAP]
    assert views_ui.CARD_MATCH in views_ui.DEFAULT_TICKS


def test_views_window_match_inside_box():
    import views_ui
    assert views_ui.MATCH_MIN == shape_map.MATCH_MIN, "the window shows the tool's default"
    by = {v[0]: v for v in views_ui.VIEWS}
    f = Path("comp_x.json")
    assert views_ui.match_min_args("50") == [] and views_ui.match_min_args("") == []
    assert views_ui.match_min_args("abc") == [] and views_ui.match_min_args("140") == []
    assert views_ui.command(by[views_ui.CARD_MATCH], f, "65")[-2:] == ["--match-min", "65"]
    assert views_ui.command(by[views_ui.SHAPE_MAP], f, "65")[-3:] == ["--no-match", "--match-min", "65"]
    assert "--match-min" not in views_ui.command(by[views_ui.CARD_MATCH], f, "50")
    assert "--match-min" not in views_ui.command(by[views_ui.CARD_MATCH], f)
    assert "--match-min" not in views_ui.command(by["outline (container shapes)"], f, "65"), "shape_map.py only"


def test_clear_all_captures_takes_captures_and_views_only(tmp_path, monkeypatch):
    import views_ui
    for name in ("capture_1.json", "comp_1.json", "comp_1.csv", "key_probe_2.txt", "render_comp_1.html",
                 "shape_map_comp_1.html", "tables_capture_1.html", "notes.md"):
        (tmp_path / name).write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "capture_9.json").write_text("x")
    names = [p.name for p in views_ui.clearable(tmp_path)]
    assert "notes.md" not in names and "capture_9.json" not in names, "other names and sub-folders stay"
    assert len(names) == 7
    assert views_ui.clearable(tmp_path / "missing") == []


def test_clear_all_asks_first_and_refreshes(tmp_path, monkeypatch):
    import tkinter as tk
    from tkinter import messagebox
    import views_ui
    (tmp_path / "capture_1.json").write_text("{}")
    (tmp_path / "shape_map_capture_1.html").write_text("x")
    monkeypatch.setattr(views_ui, "OUT", tmp_path)
    moved = []

    def fake_bin(paths):
        moved.extend(p.name for p in paths)
        for p in paths:
            p.unlink()
        return True
    monkeypatch.setattr(views_ui, "to_recycle_bin", fake_bin)
    try:
        root = tk.Tk()
    except tk.TclError:
        import pytest
        pytest.skip("no display")
    root.withdraw()
    try:
        app = views_ui.App(root)
        assert app.files == [tmp_path / "capture_1.json"]
        monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: False)
        app.clear_all()
        assert moved == [] and (tmp_path / "capture_1.json").exists(), "No = nothing moved"
        monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: True)
        app.clear_all()
        assert sorted(moved) == ["capture_1.json", "shape_map_capture_1.html"]
        assert app.files == [] and app.list.size() == 0
    finally:
        root.destroy()
