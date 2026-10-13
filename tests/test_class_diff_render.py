"""
SDIS pre-dev: the UIA render (tools/pre_dev/class_diff/render.py, plan docs/uia_render/plan.md section 6).

Fictional fixtures only: the node lists are built in this file. Each test checks one rule of the plan.
"""

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "pre_dev" / "class_diff"))

import render  # noqa: E402
from tools.vsdc_uia_probe import CONTROL_TYPE_NAMES  # noqa: E402


def _data(html):
    m = re.search(r"^const D = (.*);$", html, re.M)
    assert m, "the page carries its data on one const D line"
    return json.loads(m.group(1))


def _page(docs, meta=None, **kw):
    html, _ = render.build(docs, meta or {"kind": "read", "page": "fictional.example/p", "title": "T",
                                          "browser": "chrome", "source": "fixture.json", "label": ""}, **kw)
    return html, _data(html)


def _texts(section):
    return [e for e in section["els"] if e["kind"] == "text"]


def _boxes(section):
    return [e for e in section["els"] if e["kind"] == "box"]


# a ListItem holds a Hyperlink that holds a Text: all three say "Dashboard", only the Text draws it
NESTED = [[
    {"parent": -1, "type_name": "ListItem", "rect": [10, 10, 200, 30], "name": "Dashboard"},
    {"parent": 0, "type_name": "Hyperlink", "rect": [12, 12, 150, 26], "name": "Dashboard"},
    {"parent": 1, "type_name": "Text", "rect": [14, 14, 100, 20], "name": "Dashboard"},
]]


def test_kinds_detected_from_content(tmp_path):
    cap = {"capture": True, "page": "p", "title": "t", "browser": "edge",
           "snapshots": [{"t": 1, "docs": [[{"parent": -1, "rect": [0, 0, 5, 5], "name": "first"}]]},
                         {"t": 2, "docs": [[{"parent": -1, "rect": [0, 0, 5, 5], "name": "last"}]]}]}
    read = {"page": "p", "docs": [[{"parent": -1, "rect": [0, 0, 5, 5], "name": "single"}]]}
    comp = {"comp_page": True, "page": "p", "nodes": [{"parent": -1, "node": {"rect": [0, 0, 5, 5], "name": "c"}}]}
    files = {"cap.json": cap, "read.json": read, "comp.json": comp}
    for name, obj in files.items():
        (tmp_path / name).write_text(json.dumps(obj), encoding="utf-8")
    rec = tmp_path / "rec.jsonl"
    rec.write_text("\n".join(json.dumps({"v": 1, "ts": f"t{i}", "url": "https://fictional.example/x",
                                         "docs": [[{"parent": -1, "rect": [0, 0, 5, 5], "name": f"r{i}"}]]})
                             for i in range(3)) + "\n", encoding="utf-8")

    docs, meta = render.load(tmp_path / "cap.json")
    assert meta["kind"] == "capture" and docs[0][0]["name"] == "last"  # default: last snapshot
    docs, meta = render.load(tmp_path / "cap.json", snapshot=0)
    assert docs[0][0]["name"] == "first"
    docs, meta = render.load(tmp_path / "read.json")
    assert meta["kind"] == "read" and docs[0][0]["name"] == "single"
    docs, meta = render.load(tmp_path / "comp.json")
    assert meta["kind"] == "comp" and docs[0][0]["name"] == "c"
    docs, meta = render.load(tmp_path / "rec.jsonl", record=1)
    assert meta["kind"] == "record" and meta["ts"] == "t1" and docs[0][0]["name"] == "r1"
    docs, meta = render.load(tmp_path / "rec.jsonl")
    assert meta["index"] == 2  # default: last line


def test_record_file_is_streamed_only_the_asked_line_is_parsed(tmp_path, monkeypatch):
    rec = tmp_path / "big.jsonl"
    with open(rec, "w", encoding="utf-8", newline="\n") as f:
        for i in range(10000):
            f.write(json.dumps({"v": 1, "ts": f"t{i}", "docs": [[{"parent": -1, "rect": [0, 0, 5, 5], "name": str(i)}]]}) + "\n")
    calls = []
    real = json.loads

    def spy(s, *a, **k):
        calls.append(1)
        return real(s, *a, **k)

    monkeypatch.setattr(render.json, "loads", spy)
    docs, meta = render.load(rec, record=9999)
    assert meta["ts"] == "t9999" and docs[0][0]["name"] == "9999"
    assert len(calls) <= 2  # the first line (kind check) and the asked line; the other 9998 are never parsed


def test_record_list_prints_one_line_per_record(tmp_path, capsys):
    rec = tmp_path / "list.jsonl"
    rec.write_text("".join(json.dumps({"v": 1, "ts": f"t{i}", "url": "https://fictional.example/a",
                                       "docs": [[{"parent": -1, "rect": [0, 0, 5, 5]}] * (i + 1)]}) + "\n"
                           for i in range(3)), encoding="utf-8")
    assert render.main([str(rec), "--record", "list"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 3
    assert lines[2].split("\t")[2:] == ["fictional.example", "3 nodes"]  # host and count only, no path, no title


def test_same_input_same_bytes_whatever_the_key_order():
    meta = {"kind": "read", "page": "fictional.example/p", "title": "T", "browser": "edge", "source": "f", "label": ""}
    docs = [[
        {"parent": -1, "type_name": "Group", "rect": [0, 0, 300, 200], "name": "", "depth": 0},
        {"parent": 0, "type_name": "Button", "rect": [10, 10, 80, 30], "name": "Save", "disabled": False},
        {"parent": 1, "type_name": "Text", "rect": [12, 14, 60, 20], "name": "Save"},
        {"parent": 0, "type_name": "Edit", "rect": [10, 60, 120, 24], "value": "12", "required": True},
    ]]

    def reversed_keys(o):
        if isinstance(o, dict):
            return {k: reversed_keys(o[k]) for k in reversed(list(o))}
        if isinstance(o, list):
            return [reversed_keys(x) for x in o]
        return o

    a = render.render_html(docs, meta)
    assert a == render.render_html(docs, meta)
    assert a == render.render_html(reversed_keys(docs), reversed_keys(meta))


def test_innermost_named_node_draws_once():
    _, d = _page(NESTED)
    sec = d["docs"][0]
    texts = _texts(sec)
    assert [e["k"] for e in texts] == [2]
    assert texts[0]["txt"] == "Dashboard" and texts[0]["link"] is True  # the Text inside the Hyperlink
    why = {n["i"]: n["why"] for n in sec["nodes"]}
    assert why[0] == "text drawn by a descendant" and why[1] == "text drawn by a descendant"


def test_origin_shift_and_zero_or_null_rects_listed_with_reason():
    docs = [[
        {"parent": -1, "type_name": "Text", "rect": [-20, -30, 50, 20], "name": "a"},
        {"parent": -1, "type_name": "Text", "rect": [0, 0, 0, 0], "name": "zero"},
        {"parent": -1, "type_name": "Text", "rect": None, "name": "null"},
        {"parent": -1, "type_name": "Text", "rect": [10, -5, 40, 20], "name": "b"},
    ]]
    _, d = _page(docs)
    sec = d["docs"][0]
    el = {e["k"]: e for e in sec["els"]}
    assert (el[0]["x"], el[0]["y"]) == (0, 0)  # shifted by the minimum x and y of the drawn boxes
    assert (sec["W"], sec["H"]) == (70, 45)
    assert 1 not in el and 2 not in el
    why = {n["i"]: n["why"] for n in sec["nodes"]}
    assert why[1] == "zero size" and why[2] == "no rect"
    assert sec["nodes"][1]["box"] == [0, 0, 0, 0] and sec["nodes"][2]["box"] is None


def test_missing_type_name_comes_from_control_names_unknown_is_a_number():
    docs = [[
        {"parent": -1, "ctype": 50004, "rect": [0, 0, 100, 20], "value": "123"},  # Edit, from the value
        {"parent": -1, "ctype": 987654, "rect": [0, 30, 100, 20], "name": "x"},   # unknown control type
    ]]
    _, d = _page(docs)
    sec = d["docs"][0]
    assert sec["nodes"][0]["t"] == CONTROL_TYPE_NAMES[50004] == "Edit"
    assert [e["txt"] for e in _texts(sec)] == ["123", "x"]
    assert [e["t"] for e in _boxes(sec)] == ["Edit"]  # the unknown container draws no look
    assert sec["nodes"][1]["t"] == "987654"


def test_pua_icon_is_a_code_box_and_nothing_else():
    docs = [[{"parent": -1, "type_name": "Button", "rect": [0, 0, 40, 40], "name": ""}]]
    _, d = _page(docs)
    icon = _texts(d["docs"][0])[0]
    assert icon["icon"] is True and icon["txt"] == "U+E055" and icon["fs"] == 8


def test_line_height_is_the_median_text_height_not_the_box_height():
    docs = [[
        {"parent": -1, "type_name": "Text", "rect": [0, 0, 200, 30], "name": "t"},
        {"parent": -1, "type_name": "Button", "rect": [0, 40, 100, 49], "name": "Go"},
    ]]
    _, d = _page(docs)
    sec = d["docs"][0]
    assert sec["lineH"] == 30
    btn = [e for e in _texts(sec) if e["k"] == 1][0]
    assert btn["fs"] == round(30 * 0.78) == 23  # not 49 x 0.78 = 38


def test_looks_by_type_link_required_combo_tab_disabled():
    docs = [[
        {"parent": -1, "type_name": "Edit", "rect": [0, 0, 100, 24], "value": "v", "required": True},
        {"parent": -1, "type_name": "ComboBox", "rect": [0, 30, 100, 24], "value": "c"},
        {"parent": -1, "type_name": "ListItem", "cls": "tab active", "rect": [0, 60, 100, 24], "name": "On"},
        {"parent": -1, "type_name": "ListItem", "cls": "tab", "rect": [0, 90, 100, 24], "name": "Off"},
        {"parent": -1, "type_name": "Text", "rect": [0, 120, 100, 20], "name": "dim", "disabled": True},
    ]]
    _, d = _page(docs)
    sec = d["docs"][0]
    boxes = {e["k"]: e for e in _boxes(sec)}
    assert boxes[0]["req"] is True and boxes[1]["combo"] is True
    assert boxes[2]["tab"] is True and 3 not in boxes  # only the active item gets a tab outline
    assert [e["txt"] for e in _texts(sec) if e["k"] == 3] == ["Off"]  # an inactive ListItem draws its text, no tab
    assert [e for e in _texts(sec) if e["k"] == 4][0]["dis"] is True


def test_comp_views_present_all_and_attached_marker():
    comp = [
        {"parent": -1, "node": {"type_name": "Group", "rect": [0, 0, 300, 200]}, "present": True, "origin": "anchor"},
        {"parent": 0, "node": {"type_name": "Text", "rect": [10, 10, 80, 20], "name": "Keep"}, "present": True, "origin": "anchor"},
        {"parent": 0, "node": {"type_name": "Text", "rect": [10, 40, 80, 20], "name": "Gone"}, "present": False, "origin": "anchor"},
        {"parent": 0, "node": {"type_name": "Text", "rect": [10, 70, 80, 20], "name": "New"}, "present": True, "origin": "attached"},
    ]
    docs, meta = [[dict(e["node"], parent=e["parent"], present=e["present"], origin=e["origin"]) for e in comp]], {
        "kind": "comp", "page": "p", "title": "", "browser": "", "source": "c.json", "label": "comp page"}
    _, present = _page(docs, meta, comp_view="present")
    _, everything = _page(docs, meta, comp_view="all")

    p = present["docs"][0]
    assert sorted(e["txt"] for e in _texts(p)) == ["Keep", "New"]
    assert [n["why"] for n in p["nodes"] if n["i"] == 2] == ["left (not in present view)"]
    assert [e for e in _texts(p) if e["txt"] == "New"][0]["att"] is True

    a = everything["docs"][0]
    gone = [e for e in _texts(a) if e["txt"] == "Gone"]
    assert len(gone) == 1 and gone[0]["muted"] is True


def test_doc_default_is_the_one_with_most_boxes_all_renders_each_with_content():
    docs = [
        [{"parent": -1, "type_name": "Text", "name": "no box"}],
        [{"parent": -1, "type_name": "Text", "rect": [0, 0, 10, 10], "name": "a"},
         {"parent": -1, "type_name": "Text", "rect": [0, 20, 10, 10], "name": "b"},
         {"parent": -1, "type_name": "Text", "rect": [0, 40, 10, 10], "name": "c"}],
        [{"parent": -1, "type_name": "Text", "rect": [0, 0, 10, 10], "name": "d"},
         {"parent": -1, "type_name": "Text", "rect": [0, 20, 10, 10], "name": "e"}],
    ]
    _, auto = _page(docs)
    assert [s["k"] for s in auto["docs"]] == [1]
    _, every = _page(docs, doc="all")
    assert [s["k"] for s in every["docs"]] == [1, 2]
    assert every["docs"][0]["label"] == "doc 1: 3 nodes"
    _, one = _page(docs, doc=2)
    assert [s["k"] for s in one["docs"]] == [2]


def test_overlapping_texts_are_counted():
    apart = [[{"parent": -1, "type_name": "Text", "rect": [0, 0, 100, 20], "name": "a"},
              {"parent": -1, "type_name": "Text", "rect": [0, 30, 100, 20], "name": "b"}]]
    overlap = [[{"parent": -1, "type_name": "Text", "rect": [0, 0, 100, 20], "name": "a"},
                {"parent": -1, "type_name": "Text", "rect": [10, 0, 100, 20], "name": "b"}]]
    assert render.build(apart, {"kind": "read"})[1]["sections"][0]["overlaps"] == 0
    assert render.build(overlap, {"kind": "read"})[1]["sections"][0]["overlaps"] == 1


def test_no_external_url_in_the_page():
    html, _ = _page(NESTED)
    for needle in ("http://", "https://", "//cdn", "src="):
        assert needle not in html
    assert "https://" not in render.TEMPLATE and "http://" not in render.TEMPLATE


def test_output_paths_never_recorder_or_tracked_folders(tmp_path):
    recorder = tmp_path / "AmanAssociates_Sera" / "sdis_capture" / "render.html"
    with pytest.raises(ValueError):
        render.check_out(recorder)
    with pytest.raises(ValueError):
        render.check_out(ROOT / "docs" / "render.html")
    render.check_out(render.PRE_DEV_OUT / "render.html")  # allowed: the git-ignored pre-dev output
    render.check_out(tmp_path / "anywhere.html")  # allowed: outside the repo, the user's own folder


def test_default_output_name_and_folder(tmp_path):
    cap = tmp_path / "capture_20261011_094427_1__x.json"
    assert render.default_out(cap, {"kind": "capture"}) == tmp_path / "render_capture_20261011_094427_1__x.html"
    rec = tmp_path / "sdis_2026-10-11.jsonl"
    assert render.default_out(rec, {"kind": "record", "index": 384}) == \
        render.PRE_DEV_OUT / "render_sdis_2026-10-11_line384.html"
    recorder_file = tmp_path / "sdis_capture" / "comp_x.json"
    assert render.default_out(recorder_file, {"kind": "comp"}).parent == render.PRE_DEV_OUT
