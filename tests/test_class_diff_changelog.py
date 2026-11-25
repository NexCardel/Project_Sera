"""
SDIS pre-dev: page reads as a changelog (tools/pre_dev/class_diff/changelog.py, and key_probe.py writing it).

Fictional fixtures only: the pages are built in this file.
"""

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "pre_dev" / "class_diff"))

import changelog as cl  # noqa: E402


def _node(parent, name="", typ="Text", rect=(0, 0, 10, 10), **extra):
    return {"parent": parent, "depth": 0 if parent < 0 else 1, "type_name": typ, "name": name,
            "rect": list(rect), "cls": "", **extra}


def _page():
    return [[_node(-1, typ="Group"),
             _node(0, "Name:"), _node(0, "Asha Traders", rect=(50, 0, 80, 10)),
             _node(0, "Status:"), _node(0, "Active", rect=(50, 20, 40, 10)),
             _node(0, "Submit", typ="Button")],
            [_node(-1, typ="Group")]]


def _rec(docs, ts, session="s1", link="portal.test/profile", **meta):
    return dict({"v": 1, "ts": ts, "started": "2026-01-01T10:00:00.000", "session": session, "portal": "",
                 "url": "https://" + link, "link": link, "title": "Profile", "browser": "msedge"}, **meta, docs=docs)


def _reads():
    """A short session: unchanged, a value changes, a banner appears ABOVE the values, a node goes,
    a field appears on a node, the page scrolls, a second client, a doc more."""
    p0 = _page()
    p1 = copy.deepcopy(p0)
    p2 = copy.deepcopy(p1); p2[0][4]["name"] = "Suspended"
    p3 = copy.deepcopy(p2)
    p3[0][1:1] = [_node(0, "Session expires in 5 min", typ="Group")]
    for n in p3[0][2:]:
        n["parent"] = 0
    p4 = copy.deepcopy(p3); del p4[0][-1]
    p5 = copy.deepcopy(p4); p5[0][3]["value"] = "typed"
    p6 = copy.deepcopy(p5)
    for n in p6[0]:
        n["rect"][1] -= 100
    other = copy.deepcopy(p0); other[0][2]["name"] = "Bharat Steels"
    p7 = copy.deepcopy(p6) + [[_node(-1, "popup")]]
    return [_rec(p0, "2026-01-01T10:00:01.000"), _rec(p1, "2026-01-01T10:00:02.000"),
            _rec(p2, "2026-01-01T10:00:03.000"), _rec(p3, "2026-01-01T10:00:04.000"),
            _rec(other, "2026-01-01T10:00:04.500", session="s2"),
            _rec(p4, "2026-01-01T10:00:05.000"), _rec(p5, "2026-01-01T10:00:06.000"),
            _rec(p6, "2026-01-01T10:00:07.000", title="Profile - scrolled"),
            _rec(p7, "2026-01-01T10:00:08.000")]


def _log(reads):
    w = cl.ChangelogWriter()
    return "".join(w.line(r) for r in reads)


def test_every_read_replays_exactly():
    reads = _reads()
    back = list(cl.read_changelog(_log(reads).splitlines()))
    assert [cl.dumps(r) for r in back] == [cl.dumps(r) for r in reads]


def test_line_kinds():
    lines = [json.loads(x) for x in _log(_reads()).splitlines()]
    assert "base" in lines[0] and lines[0]["i"] == 0
    assert lines[1]["ch"] == [0, 0] and lines[1]["meta"] == {"ts": "2026-01-01T10:00:02.000"}
    assert lines[2]["ch"][0] == [[4], ["~", [{"name": "Suspended"}]], [1]]
    assert "base" in lines[4] and lines[4]["t"].startswith("s2 ")           # a second client: its own thread
    assert lines[7]["meta"]["title"] == "Profile - scrolled"
    assert "base" in lines[8] and lines[8]["i"] == 7                         # doc count changed: base again


def test_block_above_values_does_not_renumber_them():
    ops = json.loads(_log(_reads()).splitlines()[3])["ch"][0]
    assert ops == [[1], ["+", [_node(0, "Session expires in 5 min", typ="Group")]], [5]]


def test_gone_moved_and_new_field():
    lines = [json.loads(x) for x in _log(_reads()).splitlines()]
    assert lines[5]["ch"][0] == [[6], ["-", 1]]
    assert lines[6]["ch"][0] == [[3], ["-", 1], ["+", [_reads()[6]["docs"][0][3]]], [2]]
    moved = lines[7]["ch"][0]
    assert moved[0][0] == "~" and all(set(p) == {"rect"} for p in moved[0][1]) and len(moved[0][1]) == 6


def test_restart_writes_the_same_file():
    reads = _reads()
    whole = _log(reads)
    w1 = cl.ChangelogWriter()
    part = "".join(w1.line(r) for r in reads[:4])
    w2 = cl.ChangelogWriter()
    w2.load(part.splitlines())
    part += "".join(w2.line(r) for r in reads[4:])
    assert part == whole


def test_cut_off_last_line_is_skipped():
    text = _log(_reads())
    cut = text[:-40]
    back = list(cl.read_changelog(cut.splitlines()))
    assert [cl.dumps(r) for r in back] == [cl.dumps(r) for r in _reads()[:-1]]


def test_changelog_is_smaller_than_full_reads():
    reads = _reads()[:4] * 1
    many = [dict(reads[1], ts=f"2026-01-01T10:01:{i:02d}.000") for i in range(30)]
    full = sum(len(cl.dumps(r)) for r in many)
    assert len(_log(many)) < full / 5


def test_check_and_build_commands(tmp_path, capsys):
    reads = _reads()
    rp, lp = tmp_path / "reads.jsonl", tmp_path / "changelog.jsonl"
    rp.write_text("".join(cl.dumps(r) + "\n" for r in reads) + '{"v": 1, "ts"', encoding="utf-8")
    assert cl.main(["build", str(rp), str(lp)]) == 0
    assert "SAME" in capsys.readouterr().out
    assert cl.main(["show", str(lp), "--nodes"]) == 0
    out = capsys.readouterr().out
    assert "'Active' -> 'Suspended'" in out and "+ Session expires in 5 min" in out and "FULL PAGE" in out
    broken = lp.read_text(encoding="utf-8").replace("Suspended", "Cancelled")
    lp.write_text(broken, encoding="utf-8")
    assert cl.main(["check", str(rp), str(lp)]) == 1
    assert "NOT THE SAME" in capsys.readouterr().out


def test_key_probe_capture_writes_both_files(tmp_path, monkeypatch):
    import key_probe
    pages = [[_page()[0]], [_page()[0]]]
    pages[1][0][4] = dict(pages[1][0][4], name="Suspended")
    calls = {"n": 0}

    def as_uia(docs):                               # the real reader gives rects as tuples
        return [[dict(n, rect=tuple(n["rect"])) for n in doc] for doc in docs]

    def fake_read(hwnd, raw_view, timeout_sec=0):
        if not raw_view:
            return as_uia(pages[0])
        calls["n"] += 1
        return as_uia(pages[0] if calls["n"] < 3 else pages[1])

    monkeypatch.setattr(key_probe, "read_keys", fake_read)
    monkeypatch.setattr(key_probe, "read_url", lambda hwnd: "https://portal.test/profile")
    monkeypatch.setattr(key_probe, "_window_title", lambda hwnd: "Profile")
    monkeypatch.setattr(key_probe, "browser_name_of_hwnd", lambda hwnd: "msedge")
    monkeypatch.setattr(key_probe, "_say", lambda msg: None)
    rp, lp = tmp_path / "reads.jsonl", tmp_path / "changelog.jsonl"
    key_probe.capture(1, 0.25, 0.05, "20260101_100000", log_to=(str(rp), str(lp)))
    reads = cl.full_reads(rp.read_text(encoding="utf-8").splitlines())
    assert len(reads) >= 3                                    # every poll, not only the changed ones
    back = list(cl.read_changelog(lp.read_text(encoding="utf-8").splitlines()))
    assert [cl.dumps(r) for r in back] == [cl.dumps(r) for r in reads]
    assert reads[-1]["docs"][0][4]["name"] == "Suspended" and reads[0]["link"] == "portal.test/profile"


# ── the views window's changelog view ────────────────────────────────────────
def _capture_files(folder, stamp="20260101_100000", with_reads=True):
    reads = _reads()
    if with_reads:
        (folder / f"reads_{stamp}.jsonl").write_text("".join(cl.dumps(r) + "\n" for r in reads), encoding="utf-8")
    (folder / f"changelog_{stamp}.jsonl").write_text(_log(reads), encoding="utf-8")
    cap = folder / f"capture_{stamp}_1__portal.test_profile.json"
    cap.write_text(json.dumps({"capture": True, "session": stamp, "page": "portal.test/profile", "snapshots": []}),
                   encoding="utf-8")
    return cap


def test_view_of_a_capture_shows_its_page(tmp_path, capsys):
    cap = _capture_files(tmp_path)
    out = tmp_path / "page.html"
    assert cl.main([str(cap), "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "SAME: every read replays exactly" in printed and f"-> {out}" in printed
    page = out.read_text(encoding="utf-8")
    assert "SAME: all 9 reads replay exactly" in page
    assert "Active &rarr; Suspended" in page and "+ Session expires in 5 min" in page
    assert "full page 7 nodes" in page and "also changed: title" in page
    assert "session s1" in page and "session s2" in page          # both clients of that page link


def test_view_of_a_comp_page_finds_the_same_changelog(tmp_path):
    cap = _capture_files(tmp_path)
    comp = tmp_path / cap.name.replace("capture_", "comp_")
    comp.write_text(json.dumps({"page": "portal.test/other"}), encoding="utf-8")
    src = cl.sources_for(comp)
    assert src["page"] == "portal.test/other" and src["names"][0] == "changelog_20260101_100000.jsonl"
    assert "no reads of this page" in cl.view_html(src, comp.name)


def test_view_without_reads_or_changelog(tmp_path, capsys):
    cap = _capture_files(tmp_path, with_reads=False)
    page = cl.view_html(cl.sources_for(cap), cap.name)
    assert "replay could not be checked" in page
    (tmp_path / "changelog_20260101_100000.jsonl").unlink()
    assert cl.main([str(cap), "--out", str(tmp_path / "x.html")]) == 1
    assert "no changelog_20260101_100000.jsonl beside this capture" in capsys.readouterr().out
    assert not (tmp_path / "x.html").exists()


def test_view_builds_a_changelog_from_full_reads(tmp_path):
    day = tmp_path / "sdis_2026-01-01.jsonl"
    day.write_text("".join(cl.dumps(r) + "\n" for r in _reads()) + '{"v": 1, "ts', encoding="utf-8")
    src = cl.sources_for(day)
    assert src["built"] and src["page"] is None
    page = cl.view_html(src, day.name)
    assert "SAME: all 9 reads" in page and "built here from the full reads" in page


def test_view_escapes_page_text(tmp_path):
    reads = _reads()
    reads[2]["docs"][0][4]["name"] = "<script>x</script>"
    day = tmp_path / "reads_x.jsonl"
    day.write_text("".join(cl.dumps(r) + "\n" for r in reads), encoding="utf-8")
    page = cl.view_html(cl.sources_for(day), day.name)
    assert "<script>x" not in page and "&lt;script&gt;x" in page


def test_views_window_no_longer_has_the_changelog_view_but_clears_its_files():
    """The changelog view left the views window on 2026-11-25 (changelog.py FILE still writes it); key_probe
    still writes reads_ / changelog_ files, and Clear all captures still takes them and old view pages."""
    import views_ui
    assert not any(v[1] == "changelog.py" for v in views_ui.VIEWS)
    assert not any("changelog" in t for t in views_ui.DEFAULT_TICKS)
    for name in ("reads_20260101_100000.jsonl", "changelog_20260101_100000.jsonl",
                 "changelog_view_capture_20260101_100000_1__p.html"):
        assert name.startswith(views_ui.CLEAR_PREFIXES)
