"""
SDIS pre-dev: comp page PAGES (tools/pre_dev/class_diff/snapshot_diff.py) - a significant change on the
same page link starts page 2 of the same comp page, and render.py draws each page as its own section.
Fictional fixtures only.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "pre_dev" / "class_diff"))

import snapshot_diff as sd  # noqa: E402
from core.uia_render import render  # noqa: E402


def _read(texts, popup=False):
    nodes = [{"parent": -1, "type_name": "Group", "name": "", "cls": "", "rect": [0, 0, 800, 600]},
             {"parent": 0, "type_name": "Text", "name": "Fictional portal", "cls": "hdr", "rect": [0, 0, 800, 30]}]
    for n, t in enumerate(texts):
        nodes.append({"parent": 0, "type_name": "Text", "name": t, "cls": "row", "rect": [10, 40 + 30 * n, 300, 20]})
    if popup:
        nodes.append({"parent": 0, "type_name": "Text", "name": "Are you sure?", "cls": "modal",
                      "rect": [200, 200, 200, 20]})
    return {"page": "fictional.example/app", "title": "T", "docs": [nodes]}


STEP1 = [f"Step one field {n}" for n in range(12)]
STEP2 = [f"Step two choice {n}" for n in range(12)]


def _comp(reads):
    ap = sd.AnchorPage("fictional.example/app", "s1", "chrome")
    for t, r in enumerate(reads):
        ap.add(r, f"+{t}.0s", quiet_before=t > 0)
    return ap, sd.comp_record(ap)


def _node(rec, text):
    hits = [n for n in rec["nodes"] if n["text"] == text]
    assert len(hits) == 1, (text, len(hits))
    return hits[0]


def test_a_popup_or_a_value_change_stays_one_page():
    changed = STEP1[:3] + ["Typed value"] + STEP1[4:]
    ap, rec = _comp([_read(STEP1), _read(STEP1), _read(STEP1, popup=True), _read(changed)])
    assert [p["page"] for p in rec["pages"]] == [1]
    assert all(r["page"] == 1 and not r["page_change"] for r in rec["log"])


def test_a_few_new_texts_stay_one_page():
    """9 new texts (under MIN_PAGE_TEXTS), or 10 that are under a fifth of the screen: no new page."""
    big = STEP1 + [f"Row {n}" for n in range(40)]
    _, rec = _comp([_read(STEP1), _read(STEP1), _read(STEP1 + STEP2[:9])])
    assert [p["page"] for p in rec["pages"]] == [1]
    _, rec = _comp([_read(big), _read(big), _read(big + STEP2[:10])])
    assert [p["page"] for p in rec["pages"]] == [1]


def test_a_big_block_added_under_a_page_that_stays_starts_page_two():
    """The GST dashboard case: the form stays, a block of results appears under it."""
    _, rec = _comp([_read(STEP1), _read(STEP1), _read(STEP1 + STEP2), _read(STEP1 + STEP2)])
    assert [r["page"] for r in rec["log"]] == [1, 1, 2, 2]
    a, b = _node(rec, STEP1[0]), _node(rec, STEP2[0])
    assert a["pages"] == [1, 2] and a["present_pages"] == [1, 2]
    assert b["pages"] == [2]


def test_a_second_block_under_the_same_form_is_page_three_not_back_to_page_one():
    """The form is on page 1 too, but only the NEW texts decide where the snapshot goes (GST, +18 s)."""
    step3 = [f"Step three tile {n}" for n in range(12)]
    _, rec = _comp([_read(STEP1), _read(STEP1), _read(STEP1 + STEP2), _read(STEP1 + step3)])
    assert [r["page"] for r in rec["log"]] == [1, 1, 2, 3]
    assert rec["log"][3]["page_change"] == "new"


def test_shape_map_reads_the_whole_comp_page(tmp_path):
    ap, rec = _comp([_read(STEP1), _read(STEP1), _read(STEP2), _read(STEP2)])
    out = sd.write_comp(ap, tmp_path / "comp_x")
    docs, meta = render.load(out["json"], split=False)
    assert len(docs) == 1 and len(docs[0]) == len(rec["nodes"]) and "doc_labels" not in meta


def test_a_new_screen_starts_page_two_and_the_old_page_keeps_its_texts():
    ap, rec = _comp([_read(STEP1), _read(STEP1), _read(STEP2), _read(STEP2)])
    assert [p["page"] for p in rec["pages"]] == [1, 2]
    assert [r["page"] for r in rec["log"]] == [1, 1, 2, 2]
    assert rec["log"][2]["page_change"] == "new"
    a, b = _node(rec, STEP1[0]), _node(rec, STEP2[0])
    assert a["pages"] == [1] and a["present_pages"] == [1]
    assert b["pages"] == [2] and b["present_pages"] == [2] and b["origin"] == sd.ANCHOR
    hdr = _node(rec, "Fictional portal")
    assert hdr["pages"] == [1, 2] and hdr["present_pages"] == [1, 2]


def test_going_back_returns_to_page_one():
    ap, rec = _comp([_read(STEP1), _read(STEP1), _read(STEP2), _read(STEP2), _read(STEP1)])
    assert [r["page"] for r in rec["log"]] == [1, 1, 2, 2, 1]
    assert rec["log"][4]["page_change"] == "back"
    assert len(rec["pages"]) == 2 and rec["pages"][0]["visits"] == [[0, 1], [4, 4]]
    assert _node(rec, STEP1[0])["came_back"] == 1


def test_render_draws_one_section_per_page(tmp_path):
    ap, rec = _comp([_read(STEP1), _read(STEP1), _read(STEP2), _read(STEP2)])
    out = sd.write_comp(ap, tmp_path / "comp_x")
    docs, meta = render.load(out["json"])
    assert len(docs) == 2 and meta["doc_labels"][1].startswith("page 2")
    texts = [{n.get("name") for n in d} for d in docs]
    assert STEP1[0] in texts[0] and STEP1[0] not in texts[1]
    assert STEP2[0] in texts[1] and "Fictional portal" in texts[1]
    for d in docs:
        assert all(-1 <= n["parent"] < len(d) for n in d)
    html, summary = render.build(docs, meta)
    labels = [s["label"] for s in summary["sections"]]
    assert len(labels) == 2 and labels[0].startswith("page 1") and labels[1].startswith("page 2")
    assert "Pages      : 2" in out["txt"].read_text(encoding="utf-8")


def test_page_map_keeps_the_whole_comp_page_lined_up():
    """comp_map.py indexes rec["nodes"] by the drawn node's index: it must get every node, not page 1 only."""
    import json
    import re
    import comp_map
    ap, rec = _comp([_read(STEP1), _read(STEP1), _read(STEP2), _read(STEP2)])
    html, summary = comp_map.build(rec, "fixture.json")
    data = json.loads(re.search(r"^const D = (.*);$", html, re.M).group(1))
    texts = {v["text"] for v in data["items"].values()}
    assert STEP1[0] in texts and STEP2[0] in texts
    for v in data["items"].values():
        assert rec["nodes"][v["tech"]["index"]]["text"] == v["text"]
