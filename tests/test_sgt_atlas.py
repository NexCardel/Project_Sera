"""
Tests for tools/sgt_atlas.py: show, coverage and diff over an atlas already built by
core/sgt_i/atlas.py (see test_sgt_i_atlas.py for the atlas itself). Every portal, client name, PAN
and value here is fictional.
"""

import json

import tools.sgt_atlas as tool
from core.sgt_i import atlas as at
from core.sgt_i import page_map as pm

CLIENTS = [("RAVI MEHTA", "ABCDE1234F"), ("ANITA DESAI", "PQRSX5678K"), ("KIRAN RAO", "LMNOP9012Z")]


def dashboard(name, pan, fy="2025-26", extra_label=None):
    nodes = [
        pm.Node(0, "Welcome, %s" % name, zone=pm.HEADER),
        pm.Node(1, "Returns Dashboard", heading=1, section=0),
        pm.Node(2, "Financial Year:", section=0), pm.Node(3, fy, section=0),
        pm.Node(4, "PAN:", section=0), pm.Node(5, pan, section=0),
    ]
    pairs = [pm.Pair("Financial Year", fy, "right", 2, 3, pm.MAIN, 0),
             pm.Pair("PAN", pan, "right", 4, 5, pm.MAIN, 0)]
    if extra_label:
        i = len(nodes)
        nodes += [pm.Node(i, extra_label + ":", section=0), pm.Node(i + 1, "12-03-1980", section=0)]
        pairs.append(pm.Pair(extra_label, "12-03-1980", "right", i, i + 1, pm.MAIN, 0))
    return pm.PageMap(tuple(nodes), (pm.Section(0, "Returns Dashboard", 1, node=1),), tuple(pairs))


def built_atlas(tmp_path, extra_label=None):
    a = at.PortalAtlas("portal.example.test", directory=tmp_path, salt="00" * 32)
    for n, p in CLIENTS:
        a.merge(dashboard(n, p, extra_label=extra_label), client=p)
    a.save()
    return a


# ── show / list ──────────────────────────────────────────────────────────────────
def test_list_portals_finds_saved_atlas_files(tmp_path):
    assert tool.list_portals(tmp_path) == []
    built_atlas(tmp_path)
    assert tool.list_portals(tmp_path) == ["portal.example.test"]


def test_show_prints_the_page_and_its_slots(tmp_path, capsys):
    built_atlas(tmp_path)
    assert tool.main(["show", "portal.example.test", "--dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "1 page(s)" in out
    pid = tool.load_portal("portal.example.test", tmp_path)["pages"][0]["id"]

    assert tool.main(["show", "portal.example.test", "--dir", str(tmp_path), "--page", pid]) == 0
    out = capsys.readouterr().out
    # PAN and Financial Year are fields SGT's own specs already read: shown as claimed by them.
    assert "Returns Dashboard › Financial Year" in out and " pan " in out


def test_show_reports_an_unknown_portal_without_writing_anything(tmp_path, capsys):
    assert tool.main(["show", "no.such.portal", "--dir", str(tmp_path)]) == 1
    assert "No atlas yet" in capsys.readouterr().out
    assert not (tmp_path / "atlas").exists()


# ── coverage ─────────────────────────────────────────────────────────────────────
def test_coverage_counts_slots_sgts_own_specs_already_read_as_claimed(tmp_path):
    # 2026-09-28: nothing ever wrote `claimed_by`, so every slot read "unclaimed". The tool now
    # asks SGT's field specs (the miner's own label test) - PAN and Financial Year are claimed.
    built_atlas(tmp_path)
    atlas = tool.load_portal("portal.example.test", tmp_path)
    cov = tool.coverage(atlas)
    assert cov["pages"] == 1 and cov["slots"] == 2 and cov["claimed"] == 2 and cov["unclaimed"] == 0
    for slot in atlas["pages"][0]["slots"].values():
        slot.pop("claimed_by")
    cov = tool.coverage(atlas)
    assert cov["claimed"] == 0 and cov["unclaimed"] == 2


def test_coverage_cli_prints_totals(tmp_path, capsys):
    built_atlas(tmp_path)
    assert tool.main(["coverage", "portal.example.test", "--dir", str(tmp_path)]) == 0
    assert "2 slot(s): 2 claimed, 0 unclaimed" in capsys.readouterr().out


# ── diff: the portal-change alarm ─────────────────────────────────────────────────
def test_diff_finds_no_difference_between_identical_snapshots(tmp_path):
    a = built_atlas(tmp_path)
    same = a.to_json()
    assert tool.diff_atlases(same, same) == {"pages_added": [], "pages_removed": [], "pages_changed": []}


def test_diff_names_a_relabelled_field_gone_and_appeared(tmp_path):
    # One client only, so nothing is ever promoted to clear text (test_sgt_i_atlas.py covers
    # promotion) - both labels stay masked shapes, distinct enough not to collide with PAN/FY/the
    # heading. "Date of Birth" is core by visit 3; by visit 7 (4 more, all "Registration Number")
    # its share has fallen under the floor and it drops out of the *current* fingerprint - the
    # alarm - while the slot itself stays (slots retire only by ageing, 14.4 step 4).
    a = at.PortalAtlas("portal.example.test", directory=tmp_path, salt="00" * 32)
    for _ in range(3):
        a.merge(dashboard(*CLIENTS[0], extra_label="Date of Birth"))
    a.save()
    before = json.loads((tmp_path / "atlas" / "portal.example.test.json").read_text(encoding="utf-8"))

    for _ in range(4):
        a.merge(dashboard(*CLIENTS[0], extra_label="Registration Number"))
    a.save()
    after = json.loads((tmp_path / "atlas" / "portal.example.test.json").read_text(encoding="utf-8"))

    d = tool.diff_atlases(before, after)
    assert d["pages_added"] == [] and d["pages_removed"] == []
    changed = d["pages_changed"][0]
    assert "label:AAAA AA AAAAA" in changed["fingerprint_removed"]
    assert "label:AAAAAAAAAAAA AAAAAA" in changed["fingerprint_added"]
    assert any(s.endswith("AAAAAAAAAAAA AAAAAA") for s in changed["slots_added"])
    assert changed["slots_removed"] == []


def test_diff_finds_a_page_that_appeared_and_one_that_disappeared(tmp_path):
    old = {"pages": [{"id": "p-1", "fingerprint": ["heading:Old Page"], "slots": {}}]}
    new = {"pages": [{"id": "p-2", "fingerprint": ["heading:New Page"], "slots": {}}]}
    d = tool.diff_atlases(old, new)
    assert d["pages_added"] == [{"id": "p-2", "fingerprint": ["heading:New Page"]}]
    assert d["pages_removed"] == [{"id": "p-1", "fingerprint": ["heading:Old Page"]}]
    assert d["pages_changed"] == []


def test_diff_cli_reads_two_files_and_names_the_alarm(tmp_path, capsys):
    old_file = tmp_path / "old.json"
    new_file = tmp_path / "new.json"
    old_file.write_text(json.dumps({"pages": [{"id": "p-1", "fingerprint": ["label:PAN"], "slots": {}}]}))
    new_file.write_text(json.dumps({"pages": [{"id": "p-1", "fingerprint": ["label:GSTIN"], "slots": {}}]}))
    assert tool.main(["diff", str(old_file), str(new_file)]) == 0
    out = capsys.readouterr().out
    assert "- label:PAN" in out and "+ label:GSTIN" in out


def test_diff_accepts_a_directory_with_portal_for_either_side(tmp_path, capsys):
    built_atlas(tmp_path)
    assert tool.main(["diff", str(tmp_path), str(tmp_path), "--portal", "portal.example.test"]) == 0
    assert "No difference." in capsys.readouterr().out
