"""
Tests for core/sgt_i/miner.py - blueprint 14.4 step 11 (the miner). Every value here is fictional.
The key promise: every drafted spec passes the Core loader's own build + self-tests.
"""

import copy
import json
from collections import Counter
from pathlib import Path

import pytest

from core.sgt import sgt_specs
from core.sgt.sgt_toolbox import CHECKS, SUBMIT_LEVELS, c_gstin_checksum, ddmmyy_tail_date
from core.sgt_i import miner
from core.sgt_i import page_map as pm
from core.sgt_i.atlas import Atlas
from core.sgt_i.stats import ContainerStats

BASE = [sgt_specs.BUILTIN_FIELDS_PATH]          # the shipped file only - no PC override in tests
GOLDEN = Path(__file__).parent / "sgt_golden" / "gst_return_filed.json"
PORTAL = "Example Portal"

# Fictional clients: (PAN, registration date, trade code, notice references)
CLIENTS = [("ABCPE1234F", "12/03/2019", "TRD4471", ("NTC20250001", "NTC20250002")),
           ("PQRPX5678K", "05/11/2020", "TRD9023", ("NTC20250013", "NTC20250014")),
           ("LMNPO9012Z", "23/07/2018", "TRD1180", ("NTC20250025", "NTC20250026"))]


def cfg(**kw):
    c = dict(miner.load_config())
    c.update(kw)
    return c


def registry():
    return sgt_specs.load_registry(BASE)


def business_page(pan, reg_date, code, notices, help_label=None):
    """A fictional business-details page: two single pairs, a PAN, and a repeating notice list."""
    nodes = [pm.Node(0, "Business Details", heading=1, section=0),
             pm.Node(1, "Registration Date:", section=0), pm.Node(2, reg_date, section=0),
             pm.Node(3, "Trade Code:", section=0), pm.Node(4, code, section=0),
             pm.Node(5, "PAN:", section=0), pm.Node(6, pan, section=0),
             pm.Node(7, "Edit", role="button", section=0),
             pm.Node(8, "Notices", heading=2, section=1)]
    pairs = [pm.Pair("Registration Date", reg_date, "right", 1, 2, pm.MAIN, 0),
             pm.Pair("Trade Code", code, "right", 3, 4, pm.MAIN, 0),
             pm.Pair("PAN", pan, "right", 5, 6, pm.MAIN, 0)]
    for r, ref in enumerate(notices, start=1):
        i = len(nodes)
        nodes += [pm.Node(i, "Notice Reference:", section=1), pm.Node(i + 1, ref, section=1),
                  pm.Node(i + 2, "Issued On:", section=1), pm.Node(i + 3, "0%d/0%d/2025" % (r, r + 2), section=1)]
        pairs += [pm.Pair("Notice Reference", ref, "right", i, i + 1, pm.MAIN, 1, r),
                  pm.Pair("Issued On", "0%d/0%d/2025" % (r, r + 2), "right", i + 2, i + 3, pm.MAIN, 1, r)]
    if help_label:
        i = len(nodes)
        nodes += [pm.Node(i, help_label + ":", zone=pm.HELP), pm.Node(i + 1, "4471", zone=pm.HELP)]
        pairs.append(pm.Pair(help_label, "4471", "right", i, i + 1, pm.HELP, 0))
    sections = (pm.Section(0, "Business Details", 1, node=0), pm.Section(1, "Notices", 2, node=8))
    return pm.PageMap(tuple(nodes), sections, tuple(pairs))


def built_atlas(tmp_path, clients=CLIENTS, **kw):
    atlas = Atlas(directory=tmp_path, stats=ContainerStats(directory=tmp_path))
    for pan, reg, code, notices in clients:
        for visit in range(2):          # twice each: a profile value stays put for its client
            atlas.merge(PORTAL, business_page(pan, reg, code, notices, **kw), client=pan)
    atlas.save()
    return atlas


# ── fictional examples ───────────────────────────────────────────────────────────
def test_fictional_generators_give_valid_shapes_checksums_and_dates():
    for n in range(5):
        pan = miner.fictional_pan(n)
        assert len(pan) == 10 and pan[:5].isalpha() and pan[5:9].isdigit() and pan[9].isalpha() and pan[3] == "P"
        gstin = miner.fictional_gstin(n)
        assert c_gstin_checksum(gstin, None) and gstin[2:12] == pan
        ack = miner.fictional_ack(n)
        d = ddmmyy_tail_date(ack)
        assert len(ack) == 15 and d is not None and d < sgt_specs.SELF_TEST_TODAY
    assert miner.fictional_date("99/99/9999") == "25/06/2026"
    assert miner.fictional_date("99-AAA-9999") == "25-Jun-2026"
    assert miner.fictional_date("AAAA") is None


# ── drafted specs pass the Core loader's self-tests ──────────────────────────────
@pytest.mark.parametrize("typ,shapes,rules,section", [
    ("date", {"99/99/9999": 40}, {}, miner.PROFILE),
    ("date", {"99-AAA-9999": 12}, {}, miner.CURRENT),
    ("code", {"AAA9999": 30}, {}, miner.PROFILE),
    ("code", {"AAAAA9999A": 30}, {}, miner.PROFILE),
    ("code", {"99AAAAA9999A9A9": 20, "99AAAAA9999A9AA": 15}, {"checksum": "mod36"}, miner.PROFILE),
    ("number", {"999999999999999": 25}, {"date_tail": True}, miner.CURRENT),
    ("number", {"999999": 10, "9999999": 8}, {}, miner.CURRENT),
    ("amount", {"9,99,999": 9}, {}, miner.CURRENT),
    ("percentage", {"99%": 9}, {}, miner.CURRENT),
    ("email", {"AAAA@AAAA.AAA": 9}, {}, miner.PROFILE),
    ("phone", {"9999999999": 9}, {}, miner.PROFILE),
    ("yes/no", {"AAA": 9}, {}, miner.CURRENT),
    ("text", {"AAAA AAAA": 9}, {}, miner.PROFILE),
])
def test_drafted_field_specs_pass_the_core_loader_self_tests(typ, shapes, rules, section):
    spec = miner.draft_field("Mined Label Here", typ, shapes, "mined_label_here", section, PORTAL, rules)
    spec["name"] = "mined.test.%s" % typ.replace("/", "_")
    assert miner.check_spec(section, spec, BASE) is None, spec
    assert spec["examples"] and spec["note"].startswith("Drafted by the SGT-I miner")


def test_proven_rules_become_toolbox_checks():
    gst = miner.draft_field("Supplier Id", "code", {"99AAAAA9999A9A9": 20, "99AAAAA9999A9AA": 15},
                            "supplier_id", miner.PROFILE, rules={"checksum": "mod36"})
    assert "gstin_checksum" in gst["checks"]
    assert all(c_gstin_checksum(v, None) for v in gst["examples"] if isinstance(v, str))
    assert any(not c_gstin_checksum(v[:15], None) for v in gst["counter_examples"])
    ack = miner.draft_field("Reference", "number", {"9" * 15: 9}, "reference", miner.CURRENT,
                            rules={"date_tail": True})
    assert ack["checks"] == ["all_digits", "ddmmyy_tail_real"]
    date = miner.draft_field("Filed On", "date", {"99/99/9999": 9}, "filed_on", miner.CURRENT)
    assert date["transforms"] == ["parse_date"] and date["examples"][-1]["expect"] == "2026-06-25"


def test_mixed_shapes_and_choices_are_dropped():
    with pytest.raises(miner.Drop):
        miner.draft_field("X", "code", {"AAA-99": 10, "99.AAAA": 10}, "x", miner.PROFILE)
    with pytest.raises(miner.Drop):
        miner.draft_field("X", "choice", {"AAAA": 10}, "x", miner.PROFILE)


# ── sources 1 and 3: the atlas ───────────────────────────────────────────────────
def test_atlas_slots_become_profile_fields_and_a_list_record(tmp_path):
    atlas = built_atlas(tmp_path)
    dropped = Counter()
    props = miner.from_atlas(atlas, [PORTAL], registry(), cfg(), dropped=dropped)
    by_container = {p["container"].split(" › ")[-1]: p for p in props}
    assert set(by_container) == {"Registration Date", "Trade Code", "Notices"}
    reg = by_container["Registration Date"]
    assert reg["source"] == "template_vs_data" and reg["section"] == "profile" and reg["kind"] == "profile"
    assert reg["spec"]["portals"] == [PORTAL] and reg["spec"]["checks"] == ["is_real_date"]
    rec = by_container["Notices"]
    assert rec["source"] == "list_record" and rec["section"] == "records"
    assert [f["field"] for f in rec["spec"]["fields"]] == ["notice_reference", "issued_on"]
    assert rec["spec"]["start"].startswith(r"^\s*Notice\s+Reference")
    for p in props:
        assert miner.check_spec(p["section"], p["spec"], BASE) is None, p["spec"]
    assert dropped["already claimed"] >= 1           # the PAN: a Core spec already reads it


def test_support_gate_needs_enough_different_clients(tmp_path):
    atlas = built_atlas(tmp_path, clients=CLIENTS[:2])
    dropped = Counter()
    assert miner.from_atlas(atlas, [PORTAL], registry(), cfg(), dropped=dropped) == []
    assert dropped["support"] or dropped["label not template yet"]


def test_placement_gate_never_mines_help_or_placeholders(tmp_path):
    atlas = built_atlas(tmp_path, help_label="Code Hint")
    props = miner.from_atlas(atlas, [PORTAL], registry(), cfg(placeholder_words=[r"\btrade\b"]))
    containers = [p["container"] for p in props]
    assert not any("Code Hint" in c or "Trade Code" in c for c in containers)


# ── source 2: known-value anchoring ──────────────────────────────────────────────
def _row(pan, synonyms=(), opinions=(), portal="Income Tax"):
    return {"portal": portal, "pan": pan,
            "raw_payload": {"sgt_i": {"expectations": {"synonyms": list(synonyms)},
                                      "ledger": {"datasets": [{"dataset": "d", "second_opinions": list(opinions)}]}}}}


def test_a_new_wording_for_a_known_field_is_proposed_from_three_clients():
    syn = {"field": "ack", "container": ["Filing Details", "Acknowledgement Ref"]}
    rows = [_row(pan, [syn]) for pan, *_ in CLIENTS]
    props = miner.from_synonyms(rows, registry(), cfg())
    assert len(props) == 1
    p = props[0]
    assert p["source"] == "known_value" and p["type"] == "arn" and p["section"] == "records"
    assert p["spec"]["fields"][0]["labels"] == ["Acknowledgement Ref"]
    assert miner.check_spec(p["section"], p["spec"], BASE) is None, p["spec"]
    dropped = Counter()
    assert miner.from_synonyms(rows[:2], registry(), cfg(), dropped) == [] and dropped["support"] == 1


def test_a_profile_synonym_borrows_the_core_pattern():
    syn = {"field": "pan", "container": ["Assessee", "Taxpayer Account Id"]}
    props = miner.from_synonyms([_row(pan, [syn]) for pan, *_ in CLIENTS], registry(), cfg())
    assert props and props[0]["section"] == "profile" and props[0]["spec"]["field"] == "pan"
    assert miner.check_spec("profile", props[0]["spec"], BASE) is None


# ── source 4: status wording from outcomes ───────────────────────────────────────
def _gst_sessions():
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))["pages"]
    pages = []
    for n in range(3):
        gstin = miner.fictional_gstin(n + 1)
        for pg in golden:
            p = copy.deepcopy(pg)
            p["session"] = "s%d" % n
            p["ts"] = pg["ts"] + n * 1000
            p["lines"] = [ln.replace("19ABCPD1234E1ZB", gstin).replace("AA070826000001Z", "AA07082600000%dZ" % (n + 2))
                          for ln in p["lines"]]
            if p["url"].endswith("/success"):
                p["lines"].insert(1, "Your filing is now complete")
            pages.append(p)
    return pages


def test_status_wording_before_a_settled_identifier_needs_the_users_level():
    from core.sgt.sgt_replay import replay
    pages = _gst_sessions()
    replayed = replay(pages, sgt_specs.SpecStore(BASE, log=lambda *a, **k: None))
    props = miner.from_status_phrases(pages, replayed, registry(), cfg())
    p = next(p for p in props if p["container"] == "Your filing is now complete")
    assert p["needs"] == ["level"] and p["support"]["clients"] == 3
    assert p["support"]["levels_seen"] == {"Submitted & Verified": 3}
    with pytest.raises(ValueError):
        miner.spec_for_accept(p)
    for level in SUBMIT_LEVELS[1:]:
        spec = miner.spec_for_accept(p, level)
        assert spec["map"][0][1] == level
        assert miner.check_spec(p["section"], spec, BASE) is None, spec
    assert p["spec"]["map"][0][1] is None               # the miner never decides a level


# ── source 5: graduation ─────────────────────────────────────────────────────────
def test_graduation_needs_a_verdict_right_for_enough_clients_and_never_wrong():
    op = {"rule": "form_vs_pan", "says": "an ITR form on a company PAN"}
    rows = [_row(pan, opinions=[op]) for pan, *_ in CLIENTS]
    assert miner.from_second_opinions(rows, None, cfg()) == []
    props = miner.from_second_opinions(rows, lambda row, o: True, cfg())
    assert props[0]["source"] == "graduation" and props[0]["spec"] is None and props[0]["needs"] == ["developer"]
    verdicts = iter([True, True, True, False])
    assert miner.from_second_opinions(rows + [_row("ZZZPZ0000Z", opinions=[op])],
                                      lambda row, o: next(verdicts), cfg()) == []


# ── the replay gate ──────────────────────────────────────────────────────────────
def test_replay_flags_red_when_an_existing_capture_changes():
    pages = json.loads(GOLDEN.read_text(encoding="utf-8"))["pages"]
    harmless = miner.draft_field("Mined Label Here", "code", {"AAA9999": 9}, "mined_label_here", miner.PROFILE)
    harmless["name"] = "mined.test.harmless"
    r = miner.replay_diff("profile", harmless, pages, BASE)
    assert r["red"] is False and r["adds"] == 0 and r["pages"] == len(pages)
    # A "name" read from a button's text would change the golden row's client name.
    changer = {"name": "mined.test.name", "field": "name", "take": "anywhere", "confidence": 100,
               "merge": "promote_longer", "pattern": "^(View Profile)$", "examples": ["View Profile"]}
    assert miner.check_spec(miner.PROFILE, changer, BASE) is None
    r = miner.replay_diff(miner.PROFILE, changer, pages, BASE)
    assert r["red"] is True and r["changed"] == 1 and r["adds"] == 2
    assert set(r) == {"pages", "adds", "added", "changed", "removed", "held", "red"}   # counts only


# ── the whole run, the file ──────────────────────────────────────────────────────
def test_mine_writes_proposals_without_any_client_value_and_keeps_decisions(tmp_path):
    atlas = built_atlas(tmp_path / "sgt_i")
    pages = _gst_sessions()
    result = miner.mine(atlas=atlas, portals=[PORTAL], pages=pages, base_paths=BASE)
    sources = {p["source"] for p in result["proposals"]}
    assert {"template_vs_data", "list_record", "status_wording"} <= sources
    assert all("replay" in p and p["red"] is False for p in result["proposals"])
    out = miner.write_proposals(result, tmp_path / "proposals.json")
    raw = out.read_bytes().decode("utf-8")
    for pan, reg, code, notices in CLIENTS:
        for v in (pan, reg, code) + notices:
            assert v not in raw
    for pg in pages:
        for ln in pg["lines"]:
            if any(ch.isdigit() for ch in ln):
                assert ln not in raw
    data = json.loads(raw)
    data["proposals"][0]["status"] = "rejected"
    out.write_text(json.dumps(data), encoding="utf-8")
    again = miner.write_proposals(miner.mine(atlas=atlas, portals=[PORTAL], pages=pages, base_paths=BASE), out)
    first = json.loads(again.read_text(encoding="utf-8"))["proposals"]
    assert {p["id"]: p["status"] for p in first}[data["proposals"][0]["id"]] == "rejected"


def test_the_atlas_now_counts_clients_per_slot(tmp_path):
    atlas = built_atlas(tmp_path)
    page = next(iter(atlas.portal(PORTAL).pages.values()))
    assert {s["clients"] for s in page["slots"].values()} == {3}


def test_the_core_never_imports_the_miner():
    core = Path(__file__).resolve().parents[1] / "core" / "sgt"
    for f in core.glob("*.py"):
        assert "sgt_i.miner" not in f.read_text(encoding="utf-8")


# ── labels that may never become a datapoint (field test 2026-09-29) ──────────────
def _slot(container, typ="text"):
    return {"container": container, "zone": "main", "clients": 6, "types": {typ: 30}, "kind": "profile"}


@pytest.mark.parametrize("container,why", [
    ("Login › Password", "sensitive label"),
    ("Change Password", "sensitive label"),
    ("Change Password › New", "sensitive label"),            # a heading above the label counts too
    ("Login › Enter OTP", "sensitive label"),
    ("Secure Access Message", "sensitive label"),
    ("Captcha", "sensitive label"),
    ("SCA", "placement: Sera's own UI"),                     # Sera Clipboard Assist
    ("Username Injected", "placement: Sera's own UI"),
    ("View Filed Returns › 1", "placement: no words"),        # a counter / page number
    ("0", "placement: no words"),
])
def test_blocked_labels_never_pass_the_slot_gate(container, why):
    label = container.split(" › ")[-1]
    assert miner._slot_gate(_slot(container), label, cfg(), registry()) == why


def test_learnt_furniture_never_passes_the_slot_gate():
    furniture = {"English", "Skip to main content", "CoBrowse Help"}.__contains__
    for label in ("English", "Skip to main content", "CoBrowse Help"):
        assert miner._slot_gate(_slot(label), label, cfg(), registry(), furniture) == "placement: furniture"
    assert miner._slot_gate(_slot("Profile › Residential Status"), "Residential Status", cfg(), registry(),
                            furniture) is None                # real data labels still pass
    assert miner._slot_gate(_slot("Pinpoint Code"), "Pinpoint Code", cfg(), registry()) is None   # not "pin"
