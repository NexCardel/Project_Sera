"""
SDIS pre-dev: the shape map (tools/pre_dev/class_diff/shape_map.py).

Fictional fixtures only: the node lists are built in this file.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "pre_dev" / "class_diff"))

import shape_map  # noqa: E402


def _page(extra=0):
    """A page of two generic rows: a header row (2 texts) and a body row with 3 cards. Each card has a
    block (label + value) and a stepper of 2 steps (title + date). extra: that many more texts in the
    first card's block."""
    doc = []

    def add(parent, typ, rect, cls="", name=""):
        doc.append({"parent": parent, "type_name": typ, "rect": rect, "cls": cls, "name": name})
        return len(doc) - 1

    root = add(-1, "Group", [0, 0, 800, 1000], "page")
    head = add(root, "Group", [0, 0, 800, 90], "row")
    add(head, "Text", [10, 10, 200, 20], name="Welcome")
    add(head, "Text", [10, 40, 200, 20], name="Logout")
    body = add(root, "Group", [0, 100, 800, 900], "row")
    for c, y in enumerate((110, 400, 690)):
        card = add(body, "Group", [10, y, 700, 280], "card")
        block = add(card, "Group", [20, y + 10, 300, 60], "block")
        add(block, "Text", [30, y + 15, 100, 20], name="Period")
        add(block, "Text", [30, y + 40, 100, 20], name=f"Month {c}")
        stepper = add(card, "Group", [20, y + 90, 300, 160], "stepper")
        for s in range(2):
            step = add(stepper, "Group", [30, y + 100 + 70 * s, 280, 60], "step")
            add(step, "Text", [40, y + 110 + 70 * s, 200, 20], name=f"Step {s}")
            add(step, "Text", [40, y + 135 + 70 * s, 200, 20], name="1 Apr")
        if c == 0:
            for x in range(extra):
                add(block, "Text", [140, y + 15 + 5 * x, 50, 4], name=f"note {x}")
    return doc


def test_cards_are_the_record_set_not_the_generic_rows():
    inf = shape_map.infer(shape_map.page_containers(_page()))
    assert len(inf["sets"]) == 1, "the two rows line up but do not match inside (2 texts vs 18): not a record set"
    s = inf["sets"][0]
    assert s["count"] == 3 and s["texts"] == [6, 6, 6] and "card" in s["tail"]
    assert s["layout"]["kind"] == "column" and s["layout"]["gap_min"] == s["layout"]["gap_max"] == 10
    roles = {p["tail"].split(".")[-1]: (p["role"], p["level"]) for p in s["inner"]}
    assert roles["block"] == ("part", 0)
    assert roles["stepper"] == ("part", 0)
    assert roles["step"] == ("list of 2", 1), "the steps nest under the stepper, two per card"


def test_lift_to_the_card_around_the_part_found():
    # one card's block holds 10 more texts: that card (16 texts) no longer matches the other two (6, 6), so
    # the cards are a set of 2 that leaves one out. The steppers match in all 3 and win; the lift walks
    # back up to the cards
    inf = shape_map.infer(shape_map.page_containers(_page(extra=10)))
    assert len(inf["sets"]) == 1 and inf["sets"][0]["tail"].endswith("stepper")
    assert inf["sets"][0]["inner"][0]["role"] == "list of 2"
    lift = inf["sets"][0]["lift"]
    assert lift and lift["levels"] == 1 and lift["texts"] == [16, 6, 6] and lift["strict_shapes"] == 1


def test_loose_merge_when_one_class_differs_per_card():
    doc = _page()
    for n, name in zip((n for n in doc if n["cls"] == "card"), ("gold", "silver", "bronze")):
        n["cls"] = "card " + name
    inf = shape_map.infer(shape_map.page_containers(doc))
    assert len(inf["sets"]) == 1, "the boxes line up and match inside: one record set, whatever the classes"
    s = inf["sets"][0]
    assert s["count"] == 3 and s["strict_shapes"] == 3 and s["match"]["overall"] == 100.0
    cards = [m for m in inf["merges"] if m["count"] == 3 and m["strict_shapes"] == 3]
    assert cards and cards[0]["layout"]["kind"] == "column"


FIELDS = (("Name -", "Asha Traders"), ("Year -", "2026-27"), ("Status -", "Filed"), ("Due -", "13/10/2026"))


def _generic_page(tall=None, fields_down=False):
    """Classless containers only, as on a page whose layout classes say nothing: a title (a sentence and 2
    buttons), 4 label/value fields (in a row, or one under the other) and a 2x3 grid of tiles (a link
    holding a name and a count). Each of them is a Group in a Group in the page: one strict shape.
    tall: index of the field whose box is 20 px taller (its value wraps)."""
    doc = []

    def add(parent, typ, rect, name=""):
        doc.append({"parent": parent, "type_name": typ, "rect": rect, "cls": "", "name": name})
        return len(doc) - 1

    root = add(-1, "Group", [0, 0, 1000, 900])
    band = add(root, "Group", [0, 0, 1000, 60])
    add(band, "Text", [920, 20, 60, 20], name="Top")
    title = add(band, "Group", [10, 10, 900, 40])
    add(title, "Text", [20, 20, 300, 20], name="Details of the fictional return for the period")
    add(title, "Button", [700, 20, 80, 20], name="Refresh")
    add(title, "Button", [800, 20, 80, 20], name="Help")
    info = add(root, "Group", [0, 70, 1000, 250])
    y = 80
    for f, (lab, val) in enumerate(FIELDS):
        x = 10 if fields_down else 10 + 240 * f
        h = 41 if f == tall else 21
        fld = add(info, "Group", [x, y, 230, h])
        add(fld, "Text", [x + 5, y + 2, 60, 16], name=lab)
        add(fld, "Text", [x + 70, y + 2, 140, h - 5], name=val)
        if fields_down:
            y += h + 10
    grid = add(root, "Group", [0, 330, 1000, 300])
    for n in range(6):
        x, y = 10 + 300 * (n % 3), 340 + 130 * (n // 3)
        t = add(grid, "Group", [x, y, 290, 120])
        link = add(t, "Hyperlink", [x + 5, y + 5, 280, 110], name=f"Table {n}")
        add(link, "Text", [x + 10, y + 10, 150, 20], name=f"Table {n} - invoices")
        add(link, "Text", [x + 10, y + 60, 40, 20], name=str(n * 3))
    return doc


def _generic_sets(doc):
    pc = shape_map.page_containers(doc)
    inf = shape_map.infer(pc)
    return pc, inf, sorted((s["count"], s["layout"]["kind"]) for s in inf["sets"])


def test_boxes_of_one_tag_shape_in_three_page_areas_are_not_one_set():
    pc, inf, got = _generic_sets(_generic_page())
    same = {e["sh"] for e in pc["ols"] if e["nt"] in (2, 3) and e["dep"] == 2}
    assert len(same) == 1, "the title, the fields and the tiles are one strict shape"
    assert got == [(4, "row"), (6, "grid 2x3")], "the tiles and the fields, each on its own; the title in neither"
    tiles = next(s for s in inf["sets"] if s["count"] == 6)
    assert tiles["match"]["overall"] == 100.0 and tiles["match"]["structure"]["always"] == 2


def test_a_taller_box_stays_in_its_column_but_not_in_its_row():
    _, inf, got = _generic_sets(_generic_page(tall=0, fields_down=True))
    assert (4, "column") in got, "one left edge and width: the height is free down a column"
    col = next(s for s in inf["sets"] if s["count"] == 4)
    assert col["layout"]["h_spread"] == 20
    _, _, got = _generic_sets(_generic_page(tall=0))
    assert (3, "row") in got and (4, "row") not in got, "a row needs one height"


def test_two_bands_of_one_width_are_not_a_set():
    doc = _generic_page()
    foot = len(doc)
    doc.append({"parent": 0, "type_name": "Group", "rect": [0, 700, 1000, 60], "cls": "", "name": ""})
    for i, name in enumerate(("Contact", "Terms")):
        doc.append({"parent": foot, "type_name": "Hyperlink", "rect": [20 + 200 * i, 720, 100, 20], "cls": "",
                    "name": name})
    pc, inf, got = _generic_sets(doc)
    assert got == [(4, "row"), (6, "grid 2x3")]
    assert all(foot not in s["members"] for s in inf["sets"]), "same left edge and width, unlike inside"


def test_html_has_no_page_text_and_same_bytes():
    pc = shape_map.page_containers(_page())
    inf = shape_map.infer(pc)
    html = shape_map.build_html(pc, inf, {"page": "fictional.example/p", "source": "fixture"})
    assert html == shape_map.build_html(pc, shape_map.infer(shape_map.page_containers(_page())),
                                        {"page": "fictional.example/p", "source": "fixture"})
    data = json.loads(re.search(r"^const D = (.*);$", html, re.M).group(1))
    blob = json.dumps(data)
    for word in ("Welcome", "Logout", "Period", "Month 0", "Step 1"):
        assert word not in blob, "the map draws text boxes, never their text"
    assert not re.search(r"https?://(?!www\.w3\.org/2000/svg)", html), "no external URL (the SVG namespace is a name)"


def test_match_min_decides_and_the_page_says_how_the_sets_were_found():
    doc = _generic_page()
    pc = shape_map.page_containers(doc)
    inf = shape_map.infer(pc)
    bx = inf["boxes"]
    assert bx["match_min"] == shape_map.MATCH_MIN and bx["linked"] <= bx["scored"] <= bx["lined_up"]
    assert all(s["link_min"] >= shape_map.MATCH_MIN for s in inf["sets"])
    assert any(line.startswith("boxes: ") for line in shape_map.report(inf))
    assert any("found by boxes" in line for line in shape_map.report(inf))
    html = shape_map.build_html(pc, inf, {"page": "fictional.example/p", "source": "fixture"})
    data = json.loads(re.search(r"^const D = (.*);$", html, re.M).group(1))
    assert data["how"] == bx and "Record sets from boxes" in html
    assert all(str(k) in data["boxes"] for s in data["sets"] for k in s["members"]), "every member has its box"
    # a field whose value is a number is unlike the others above 100 % only: at 100 the fields that hold a
    # plain text stay one set of 4, since plain texts are one kind
    strict = shape_map.infer(pc, match_min=100)
    assert sorted(s["count"] for s in strict["sets"]) == [4, 6]
    # at 0 everything that lines up is one set: the title band and the bands under it
    loose = shape_map.infer(pc, match_min=0)
    assert loose["boxes"]["best_rejected"] is None and loose["boxes"]["linked"] == loose["boxes"]["scored"]
    assert max(s["count"] for s in loose["sets"]) >= 3 and min(s["link_min"] for s in loose["sets"]) < 50


def test_match_min_flag(tmp_path, monkeypatch, capsys):
    import render
    src = tmp_path / "comp_fixture.json"
    monkeypatch.setattr(render, "load", lambda *a, **k: ([_generic_page()], {"page": "fictional.example/p"}))
    monkeypatch.setattr(render, "PRE_DEV_OUT", tmp_path)
    monkeypatch.setattr(render, "REPO", tmp_path)
    monkeypatch.setattr(render, "check_out", lambda p: None)
    assert shape_map.main([str(src), "--match-min", "90"]) == 0
    assert "(>= 90%)" in capsys.readouterr().out
    assert shape_map.main([str(src)]) == 0
    assert f"(>= {shape_map.MATCH_MIN:g}%)" in capsys.readouterr().out
