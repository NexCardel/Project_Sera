"""
SDIS pre-dev: the page map (tools/pre_dev/class_diff/comp_map.py) and the per-snapshot record it needs
from snapshot_diff.AnchorPage (seen runs, text steps). Fictional fixtures only.

The story of the fixture: a page loads, a popup opens, closes and opens again, a status text changes,
and a late item appears.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "pre_dev" / "class_diff"))

import comp_map  # noqa: E402
import snapshot_diff as sd  # noqa: E402


def _read(status="Status: Open", popup=False, late=False, button="Services"):
    nodes = [{"parent": -1, "type_name": "Group", "name": "", "cls": "", "rect": [0, 0, 800, 600]},
             {"parent": 0, "type_name": "Text", "name": "Returns dashboard", "cls": "", "rect": [10, 10, 300, 20]},
             {"parent": 0, "type_name": "Text", "name": status, "cls": "st", "rect": [10, 40, 300, 20]},
             {"parent": 0, "type_name": "Button", "name": button, "cls": "", "rect": [10, 70, 90, 24]}]
    if popup:
        nodes.append({"parent": 0, "type_name": "Group", "name": "", "cls": "modal", "rect": [200, 200, 300, 120]})
        nodes.append({"parent": len(nodes) - 1, "type_name": "Text", "name": "Are you sure?", "cls": "",
                      "rect": [210, 210, 200, 20]})
    if late:
        nodes.append({"parent": 0, "type_name": "Text", "name": "Due date 11 Dec", "cls": "due",
                      "rect": [10, 500, 300, 20]})
    return {"page": "fictional.example/map", "title": "T", "docs": [nodes]}


STORY = [_read(), _read(), _read(popup=True), _read(), _read(popup=True, status="Status: Done"),
         _read(popup=True, status="Status: Done", late=True)]


def _comp(reads=STORY):
    ap = sd.AnchorPage("fictional.example/map", "s1", "chrome")
    for t, r in enumerate(reads):
        ap.add(r, f"+{t}.0s", quiet_before=t > 0)
    return ap, sd.comp_record(ap)


def _by_text(rec, text):
    hits = [n for n in rec["nodes"] if n["text"] == text or text in [t for _, t in n.get("steps") or []]]
    assert len(hits) == 1, (text, len(hits))
    return hits[0]


def _data(html):
    m = re.search(r"^const D = (.*);$", html, re.M)
    assert m, "the page carries its data on one const D line"
    return json.loads(m.group(1))


# ---------------------------------------------------------------- snapshot_diff: the per-snapshot record

def test_seen_runs_split_when_a_node_leaves_and_comes_back():
    _, rec = _comp()
    assert _by_text(rec, "Returns dashboard")["seen"] == [[0, 5]]
    assert _by_text(rec, "Are you sure?")["seen"] == [[2, 2], [4, 5]]
    assert _by_text(rec, "Due date 11 Dec")["seen"] == [[5, 5]]


def test_steps_keep_each_text_change_with_its_snapshot():
    _, rec = _comp()
    st = _by_text(rec, "Status: Done")["steps"]
    assert st == [[0, "Status: Open"], [4, "Status: Done"]]
    assert _by_text(rec, "Returns dashboard")["steps"] == [[0, "Returns dashboard"]]


def test_the_page_at_any_snapshot_is_rebuilt_from_the_record():
    """What the map shows at moment si (every node whose seen runs hold si, with its text at si) is exactly
    the texts of snapshot si."""
    _, rec = _comp()
    for si, read in enumerate(STORY):
        want = sorted(n["name"] for n in read["docs"][0] if n["name"])
        got = []
        for n in rec["nodes"]:
            if any(a <= si <= b for a, b in n["seen"]):
                t = [x for s, x in n["steps"] if s <= si]
                if t and t[-1]:
                    got.append(t[-1])
        assert sorted(got) == want, si


def test_steps_are_capped_and_the_newest_text_wins():
    reads = [_read(status=f"Status {k}") for k in range(sd.MAX_STEPS + 5)]
    _, rec = _comp(reads)
    steps = [n for n in rec["nodes"] if n["text"].startswith("Status")][0]["steps"]
    assert len(steps) == sd.MAX_STEPS
    assert steps[-1] == [len(reads) - 1, f"Status {len(reads) - 1}"]


# ---------------------------------------------------------------- comp_map: the story and the timeline

def test_story_flags_late_gone_back_changed():
    _, rec = _comp()
    _, data = None, _data(comp_map.build(rec, "fixture.json")[0])
    items = {v["text"]: v for v in data["items"].values()}
    assert items["Due date 11 Dec"]["late"] and not items["Due date 11 Dec"]["gone"]
    assert items["Are you sure?"]["back"] and items["Are you sure?"]["late"]
    assert items["Status: Done"]["changes"] == 1
    assert not any(items["Returns dashboard"][f] for f in ("late", "gone", "back", "changes"))


def test_timeline_says_what_happened_in_plain_words():
    _, rec = _comp()
    data = _data(comp_map.build(rec, "fixture.json")[0])
    steps = data["steps"]
    assert [s["kind"] for s in steps] == ["open", "quiet", "change", "change", "change", "change"]
    assert steps[0]["say"].startswith("The page opened with ") and steps[0]["settled"]
    assert steps[2]["say"] == "1 new item appeared." and steps[2]["ex_new"] == ["Are you sure?"]
    assert steps[3]["say"] == "1 item went away." and steps[3]["ex_gone"] == ["Are you sure?"]
    assert steps[4]["say"] == "1 item came back; 1 value changed."
    assert steps[4]["ex_changed"] == ["Status: Done"]
    assert steps[5]["ex_new"] == ["Due date 11 Dec"]
    assert data["summary"]["steps_with_change"] == 4


def test_a_button_whose_snapshot_text_differs_keeps_its_drawn_label():
    """keys.flatten may give a Button no text while the render draws its name: the map must not blank it."""
    _, rec = _comp()
    for n in rec["nodes"]:
        if n["node"].get("type_name") == "Button":
            n["text"], n["steps"] = "", [[0, ""]]
    data = _data(comp_map.build(rec, "fixture.json")[0])
    btn = [v for v in data["items"].values() if v["t"] == "Button"][0]
    assert btn["text"] == "Services" and btn["steps"] == [[0, "Services"]] and btn["changes"] == 0


def test_same_input_same_bytes_and_no_external_url():
    _, rec = _comp()
    a, _ = comp_map.build(rec, "fixture.json")
    b, _ = comp_map.build(json.loads(json.dumps(rec)), "fixture.json")
    assert a == b
    assert not re.search(r"(?:src|href)=[\"']?https?://", a)


# ---------------------------------------------------------------- loading old and new files

def test_capture_file_is_rebuilt_and_old_comp_uses_its_capture(tmp_path):
    cap = {"capture": True, "page": "fictional.example/map", "session": "s1", "browser": "chrome", "interval": 1.0,
           "snapshots": [{"t": float(t), "docs": r["docs"]} for t, r in enumerate(STORY)]}
    cp = tmp_path / "capture_s1_1__map.json"
    cp.write_text(json.dumps(cap), encoding="utf-8")
    rec, note = comp_map.load(cp)
    assert rec["comp_page"] and all("seen" in n for n in rec["nodes"]) and note

    old = json.loads(json.dumps(rec))
    for n in old["nodes"]:
        n.pop("seen"), n.pop("steps")
    op = tmp_path / "comp_s1_1__map.json"
    op.write_text(json.dumps(old), encoding="utf-8")
    rec2, note2 = comp_map.load(op)
    assert "capture" in note2 and _by_text(rec2, "Are you sure?")["seen"] == [[2, 2], [4, 5]]


def test_old_comp_without_its_capture_is_approximate(tmp_path):
    _, rec = _comp()
    for n in rec["nodes"]:
        n.pop("seen"), n.pop("steps")
    op = tmp_path / "comp_s9_1__map.json"
    op.write_text(json.dumps(rec), encoding="utf-8")
    rec2, note = comp_map.load(op)
    assert "approximate" in note
    popup = _by_text(rec2, "Are you sure?")
    assert popup["approx"] and popup["seen"] == [[2, 5]]
    assert not _by_text(rec2, "Returns dashboard").get("approx")


def test_main_writes_map_page_beside_the_input(tmp_path, capsys):
    _, rec = _comp()
    p = tmp_path / "comp_s1_1__map.json"
    p.write_text(json.dumps(rec), encoding="utf-8")
    assert comp_map.main([str(p)]) == 0
    out = capsys.readouterr().out
    assert (tmp_path / "map_comp_s1_1__map.html").exists()
    assert "Are you sure?" not in out, "the console prints counts only"


def test_views_window_offers_the_page_map():
    import views_ui
    by = {v[0]: v for v in views_ui.VIEWS}
    assert by[views_ui.PAGE_MAP][1:3] == ("comp_map.py", [])
    assert views_ui.PAGE_MAP in views_ui.DEFAULT_TICKS
    assert "map_" in views_ui.CLEAR_PREFIXES
