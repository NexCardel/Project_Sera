"""
SDIS pre-dev: page_diff.py (tools/pre_dev/class_diff/) - the same page for two clients, every difference
then a summary, as a .txt report. Fixtures: the two FICTIONAL clients of tests/class_diff_align/ (B has an
extra notice and two more list rows; every label is shared, every value differs) and small made-up
comp pages built with snapshot_diff.AnchorPage.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).resolve().parent / "class_diff_align"
sys.path.insert(0, str(ROOT / "tools" / "pre_dev" / "class_diff"))

import compare  # noqa: E402
import page_diff as pd  # noqa: E402
import snapshot_diff as sd  # noqa: E402
from core.sdis import keys  # noqa: E402


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.setattr(keys, "VIEW", "raw")
    monkeypatch.setattr(compare, "ALIGN", True)


def _side(c):
    return pd.load_side(str(FIX / f"client_{c}.json"))


@pytest.fixture
def ab():
    a, b = _side("A"), _side("B")
    return a, b, pd.differences(a, b)


def test_values_that_differ_carry_both_sides_and_their_label(ab):
    _, _, d = ab
    vals = {r["label"]: (r["example_value"], r["previous_example"]) for r in d["items"][pd.VALUE]}
    assert vals["Legal Name :"] == ("ALPHA TRADERS", "BETA FOODS")
    assert vals["GSTIN :"] == ("27AAAAA1111A1Z1", "29BBBBB2222B2Z2")
    assert vals["Cash ledger / IGST"] == ("101", "404")


def test_one_side_texts_and_their_blocks(ab):
    _, _, d = ab
    only_b = [r["example_value"] for r in d["items"][pd.ONLY_B]]
    assert any(t.startswith("Your return for the quarter") for t in only_b)       # B's extra notice
    assert "BB2906252222224" in only_b                                             # B's extra list row
    assert d["items"][pd.ONLY_A] == []
    sizes = sorted(len(b) for b in pd.blocks(d["items"][pd.ONLY_B]))
    assert sum(sizes) == len(only_b) and sizes[-1] > 1                             # rows grouped, not one per text


def test_shared_labels_are_not_differences(ab):
    _, _, d = ab
    diff_texts = {r["example_value"] for k in (pd.VALUE, pd.ONLY_A) for r in d["items"][k]}
    assert not diff_texts & {"Taxpayer details", "Legal Name :", "Ledger balance"}
    assert d["same"] > 0


def test_report_lists_every_difference_and_ends_with_the_summary(ab):
    a, b, d = ab
    txt = pd.report(a, b, d)
    body, summary = txt.split("\nSUMMARY\n")
    for r in d["items"][pd.VALUE]:
        assert r["example_value"] in body and r["previous_example"] in body
    for r in d["items"][pd.ONLY_B]:
        assert r["example_value"][:60] in body
    assert f"values that differ              {len(d['items'][pd.VALUE])}" in summary
    assert f"texts only on client B          {len(d['items'][pd.ONLY_B])}" in summary
    assert "Most significant:" in summary
    assert "Legal Name : ALPHA TRADERS  |  BETA FOODS" in summary                 # label kept, no doubled colon
    assert "only on B: " in summary                                               # the biggest one-side block
    assert "WARNING   : the two sides are different page links" in txt            # the fixtures' links differ


def test_page_facts_that_differ(ab):
    _, _, d = ab
    facts = {k: (x, y) for k, x, y in d["meta"]}
    assert facts["title"] == ("SDIS align A", "SDIS align B")
    assert "browser" not in facts                                                 # same on both: not listed


def test_the_same_page_twice_has_no_differences():
    a = _side("A")
    d = pd.differences(a, _side("A"))
    assert all(not v for v in d["items"].values()) and d["meta"] == []
    assert "none - the two pages show the same texts" in pd.report(a, a, d)


# ── sides from comp pages, captures and session stamps ──────────────────────
def _read(rows):
    nodes = [{"parent": -1, "type_name": "Group", "name": "", "cls": "", "rect": [0, 0, 800, 600]},
             {"parent": 0, "type_name": "Text", "name": "Fictional portal", "cls": "hdr", "rect": [0, 0, 800, 30]}]
    for n, (label, value) in enumerate(rows):
        nodes.append({"parent": 0, "type_name": "Group", "name": "", "cls": "box", "rect": [0, 40 + 40 * n, 600, 30]})
        g = len(nodes) - 1
        nodes.append({"parent": g, "type_name": "Text", "name": label, "cls": "lbl", "rect": [10, 40 + 40 * n, 200, 20]})
        nodes.append({"parent": g, "type_name": "Text", "name": value, "cls": "val", "rect": [220, 40 + 40 * n, 300, 20]})
    return {"docs": [nodes]}


def _write_comp(dirpath, session, rows):
    ap = sd.AnchorPage("fictional.example/app", session, "chrome")
    ap.add(_read(rows), "+0.0s")
    p = dirpath / f"comp_{session}_1__fictional.example_app.json"
    p.write_text(json.dumps(sd.comp_record(ap)), encoding="utf-8")
    return p


A_ROWS = [("Name :", "Asha Fictional"), ("Status :", "Active"), ("City :", "Pune")]
B_ROWS = [("Name :", "Bala Madeup"), ("Status :", "Active"), ("City :", "Agra")]


def test_session_stamp_and_comp_file_give_the_same_side(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "OUT_DIR", tmp_path)
    p = _write_comp(tmp_path, "20260101_000001", A_ROWS)
    by_stamp = pd.load_side("20260101_000001")
    by_file = pd.load_side(str(p))
    assert by_stamp.link == by_file.link == "fictional.example/app"
    assert [e["text"] for e in by_stamp.flat] == [e["text"] for e in by_file.flat]
    with pytest.raises(SystemExit, match="no comp page of other.example"):
        pd.load_side("20260101_000001", page="other.example")


def test_capture_file_is_rebuilt_into_its_comp_page(tmp_path):
    cap = {"capture": True, "session": "20260101_000002", "page": "fictional.example/app", "browser": "chrome",
           "interval": 1.0, "snapshots": [{"t": 0.0, "docs": _read(B_ROWS)["docs"]}]}
    p = tmp_path / "capture_20260101_000002_1__fictional.example_app.json"
    p.write_text(json.dumps(cap), encoding="utf-8")
    side = pd.load_side(str(p))
    assert side.rec.get("comp_page") and "Bala Madeup" in [e["text"] for e in side.flat]


def test_latest_pair_and_the_written_report(tmp_path, monkeypatch):
    monkeypatch.setattr(pd, "OUT_DIR", tmp_path)
    monkeypatch.setattr(pd, "ids_of", lambda side: set())
    _write_comp(tmp_path, "20260101_000001", A_ROWS)
    _write_comp(tmp_path, "20260102_000001", B_ROWS)
    a, b = pd.latest_pair("fictional.example/app")
    assert (a.rec["session"], b.rec["session"]) == ("20260102_000001", "20260101_000001")
    dest = pd.run(a, b)
    assert dest.parent == tmp_path and dest.name.startswith("page_diff_20260102_000001_vs_20260101_000001__")
    txt = dest.read_text(encoding="utf-8")
    vals = {r["label"]: (r["example_value"], r["previous_example"])
            for r in pd.differences(a, b)["items"][pd.VALUE]}
    assert vals == {"Name :": ("Bala Madeup", "Asha Fictional"), "City :": ("Agra", "Pune")}
    assert "Bala Madeup" in txt and "Asha Fictional" in txt and txt.rstrip().splitlines()[-1].startswith("    - ")


# ── the views window's differential analysis ─────────────────────────────────
def test_views_window_default_client_b_is_the_same_page_from_another_session():
    import views_ui
    P = Path
    files = [P("comp_20260103_000000_1__portal.test_profile.json"),       # newest first, as inputs() lists
             P("capture_20260102_000000_2__portal.test_profile.json"),
             P("comp_20260102_000000_2__portal.test_profile.json"),
             P("comp_20260102_000000_1__portal.test_other.json"),
             P("capture_20260103_000000_1__portal.test_profile.json"),
             P("key_probe_20260101_000000.json")]
    assert views_ui.page_of(files[0]) == ("20260103_000000", "portal.test_profile")
    assert views_ui.page_of(files[-1]) == (None, None)
    # another session (not A's own capture), the newest one, its comp page before its capture
    assert views_ui.partner(files[0], files) == files[2]
    assert views_ui.partner(files[4], files) == files[2]
    assert views_ui.partner(files[3], files) is None                    # nobody else has that page
    assert views_ui.partner(files[-1], files) is None                   # a single read: pick B by hand


def test_views_window_runs_page_diff_and_finds_its_report():
    import views_ui
    cmd = views_ui.diff_command(Path("output/comp_a.json"), Path("output/comp_b.json"))
    assert cmd[1].endswith("page_diff.py") and cmd[2:] == [str(Path("output/comp_a.json")),
                                                           str(Path("output/comp_b.json"))]
    m = views_ui.REPORT.match("Report   : C:\\x\\output\\page_diff_a_vs_b__p.txt")
    assert m and m.group(1).endswith("page_diff_a_vs_b__p.txt")
    assert "page_diff_a_vs_b__p.txt".startswith(views_ui.CLEAR_PREFIXES)


def test_command_line_needs_two_sides_or_a_page():
    with pytest.raises(SystemExit):
        pd.main(["only_one_side.json"])
