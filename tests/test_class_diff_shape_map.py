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
    assert len(inf["sets"]) == 1, "the two rows repeat with unlike contents (2 texts vs 18): not a record set"
    s = inf["sets"][0]
    assert s["count"] == 3 and s["texts"] == [6, 6, 6] and "card" in s["tail"]
    assert s["layout"]["kind"] == "column" and s["layout"]["gap_min"] == s["layout"]["gap_max"] == 10
    roles = {p["tail"].split(".")[-1]: (p["role"], p["level"]) for p in s["inner"]}
    assert roles["block"] == ("part", 0)
    assert roles["stepper"] == ("part", 0)
    assert roles["step"] == ("list of 2", 1), "the steps nest under the stepper, two per card"


def test_lift_to_the_card_around_the_part_found():
    # one card's block holds 10 more texts: the cards (16, 6, 6) and the blocks (12, 2, 2) are no longer
    # alike, so the steppers are the record set found, and the lift walks back up to the cards
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
    assert not any("card" in s["tail"] for s in inf["sets"]), "three strict shapes: no record set of cards"
    cards = [m for m in inf["merges"] if m["count"] == 3 and m["strict_shapes"] == 3]
    assert cards and cards[0]["layout"]["kind"] == "column"


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
