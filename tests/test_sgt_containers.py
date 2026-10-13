"""
SDIS Part S.2 in SGT (W4-7): container instances, completion levels from the containers file,
Others values in the SRPF container. Fixture documents are written here; every value is fictional.
"""
import copy
import json
from datetime import date

import pytest

from core.sdis import config
from core.sgt import sgt_containers as sc
from core.sgt.sgt_shadow import SgtShadow
from core.sgt.sgt_specs import BUILTIN_FIELDS_PATH, SpecStore
from core.sgt_i import miner

PORTAL = "Fictional Portal"
URL_A = "https://work.fictional.example/returns/start"
URL_B = "https://work.fictional.example/returns/review"
D18 = {"Draft": "Draft", "In progress": "Submitted (Not Verified)", "Complete": "Submitted & Verified"}


def container(**exc):
    return {"name": "Fictional return", "portal": PORTAL, "form_field": None, "period_field": "ret_period",
            "fields": ["ret_period", "ref_no", "amount_due", "ack_no"], "exceptions": exc}


def doc_count(**exc):
    return {"version": 1,
            "levels": [{"name": "Draft", "when": {"captured": 1}},
                       {"name": "In progress", "when": {"captured": "51%"}},
                       {"name": "Complete", "when": {"captured": "all"}}],
            "level_map": dict(D18), "profile": {}, "containers": [container(**exc)],
            "others": {PORTAL: ["turnover_band"]}}


def doc_fields():
    return {"version": 1,
            "levels": [{"name": "Started", "when": {"fields": ["ret_period"]}},
                       {"name": "Filed", "when": {"fields": ["ack_no"]}}],
            "level_map": {"Started": "Draft", "Filed": "Submitted & Verified"},
            "profile": {}, "containers": [container()], "others": {}}


def doc_no_levels():
    return {"version": 1, "levels": [], "level_map": {}, "profile": {}, "containers": [container()], "others": {}}


def feed(cs, doc, page=URL_A, **values):
    return cs.feed(doc, PORTAL, values, page)


# ── Levels from the document ─────────────────────────────────────────────────────
def test_fixture_documents_pass_the_load_checks():
    for d in (doc_count(), doc_fields(), doc_no_levels(), doc_count(optional=["amount_due"]),
              doc_count(proves={"ack_no": "Complete"})):
        config.check(d)


def test_count_based_levels_climb_one_field_at_a_time():
    doc, cs = doc_count(), sc.ContainerSession()
    steps = []
    for f, v in (("ret_period", "PR2026"), ("ref_no", "QX40417"), ("amount_due", "1200"), ("ack_no", "ZK13358")):
        inst = feed(cs, doc, **{f: v})[0]
        steps.append((inst.level, inst.status, f"{inst.k} of {inst.n}"))
    # 51% of 4 rounds up to 3
    assert steps == [("Draft", "Draft", "1 of 4"), ("Draft", "Draft", "2 of 4"),
                     ("In progress", "Submitted (Not Verified)", "3 of 4"),
                     ("Complete", "Submitted & Verified", "4 of 4")]
    assert len(cs.instances) == 1 and cs.instances[0].form == "Fictional return"


def test_fields_based_levels():
    doc, cs = doc_fields(), sc.ContainerSession()
    inst = feed(cs, doc, ret_period="PR2026", ref_no="QX40417")[0]
    assert (inst.level, inst.status) == ("Started", "Draft")
    inst = feed(cs, doc, ack_no="ZK13358")[0]
    assert (inst.level, inst.status) == ("Filed", "Submitted & Verified")


def test_optional_fields_are_not_counted():
    doc, cs = doc_count(optional=["amount_due"]), sc.ContainerSession()
    inst = feed(cs, doc, ret_period="PR2026", ref_no="QX40417", ack_no="ZK13358")[0]
    assert (inst.k, inst.n, inst.level) == (3, 3, "Complete")
    feed(cs, doc, amount_due="1200")
    assert (inst.k, inst.n) == (3, 3) and inst.values["amount_due"] == "1200"


def test_a_proves_field_lifts_and_a_later_count_never_lowers():
    doc, cs = doc_count(proves={"ack_no": "Complete"}), sc.ContainerSession()
    inst = feed(cs, doc, ret_period="PR2026", ack_no="ZK13358")[0]
    assert (inst.level, inst.status, inst.proves) == ("Complete", "Submitted & Verified", ["ack_no"])
    assert sc.evidence_of(inst)["k_of_n"] == "2 of 4"
    inst.values.pop("ack_no")                   # a value that disappears never lowers the level
    feed(cs, doc, ref_no="QX40417")
    assert (inst.level, inst.status) == ("Complete", "Submitted & Verified")


def test_a_containers_own_levels_and_level_map_replace_the_files():
    own = [{"name": "Seen", "when": {"captured": 1}}, {"name": "Done", "when": {"captured": 2}}]
    doc, cs = doc_count(levels=own, level_map={"Seen": "Draft", "Done": "Submitted (Not Verified)"}), sc.ContainerSession()
    config.check(doc)
    inst = feed(cs, doc, ret_period="PR2026")[0]
    assert (inst.level, inst.status) == ("Seen", "Draft")
    feed(cs, doc, ref_no="QX40417")
    assert (inst.level, inst.status) == ("Done", "Submitted (Not Verified)")


def test_no_levels_gives_k_of_n_and_draft():
    doc, cs = doc_no_levels(), sc.ContainerSession()
    inst = feed(cs, doc, ret_period="PR2026", ref_no="QX40417", amount_due="1200", ack_no="ZK13358")[0]
    assert inst.level is None and inst.status == "Draft" and (inst.k, inst.n) == (4, 4)


# ── Instances ────────────────────────────────────────────────────────────────────
def test_a_field_captured_on_two_pages_fills_one_slot():
    doc, cs = doc_count(), sc.ContainerSession()
    feed(cs, doc, URL_A, ret_period="PR2026", ref_no="QX40417")
    assert feed(cs, doc, URL_B, ref_no="QX40417") == []      # same value from another page: nothing new
    inst = cs.instances[0]
    assert (inst.k, inst.n) == (2, 4) and list(inst.values) == ["ret_period", "ref_no"]


def test_a_new_period_opens_a_new_instance_and_early_values_wait():
    doc, cs = doc_count(), sc.ContainerSession()
    assert feed(cs, doc, ref_no="QX40417") == []             # no period yet: waits in the session
    first = feed(cs, doc, ret_period="PR2026")[0]
    assert first.values == {"ref_no": "QX40417", "ret_period": "PR2026"}
    second = feed(cs, doc, ret_period="PR2027", amount_due="900")[0]
    assert second is not first and second.period == "PR2027" and "ref_no" not in second.values
    assert feed(cs, doc, ret_period="PR2026", ack_no="ZK13358")[0] is first   # back to the first one
    assert len(cs.instances) == 2


def test_a_changed_document_never_lowers_a_level():
    doc, cs = doc_count(), sc.ContainerSession()
    inst = feed(cs, doc, ret_period="PR2026", ref_no="QX40417", amount_due="1200")[0]
    assert inst.level == "In progress"
    harder = copy.deepcopy(doc)
    harder["containers"][0]["fields"].append("extra_no")      # n grows: 3 of 5 is only a Draft now
    config.check(harder)
    assert cs.recompute(harder) == []
    assert (inst.level, inst.status, inst.n) == ("In progress", "Submitted (Not Verified)", 5)


def test_the_session_round_trips_through_json():
    doc, cs = doc_count(), sc.ContainerSession()
    feed(cs, doc, ref_no="QX40417")
    feed(cs, doc, ret_period="PR2027")
    cs.feed_others(doc, PORTAL, {"turnover_band": "B2"}, "2026-10-05T10:00:00")
    back = sc.ContainerSession.from_json(json.loads(json.dumps(cs.to_json())))
    assert back.to_json() == cs.to_json()


# ── Inside SGT ───────────────────────────────────────────────────────────────────
def _spec(field, label, shape):
    spec = miner.draft_field(label, "code", {shape: 2}, field, miner.CURRENT, PORTAL)
    spec["name"] = sc.SPEC_PREFIX + field
    return spec


@pytest.fixture
def rig(tmp_path, monkeypatch):
    specs = {"current_dataset": {"fields": [
        _spec("ret_period", "Return period", "AA9999"),
        _spec("ref_no", "Reference number", "AA99999"),
        _spec("amount_due", "Amount code", "A999"),
        _spec("ack_no", "Acknowledgement code", "AA99999"),
        _spec("turnover_band", "Turnover band", "A9")]}}
    spec_path = tmp_path / "sdis_fields.json"
    spec_path.write_text(json.dumps(specs), encoding="utf-8")
    doc_path = tmp_path / "sdis_containers.json"
    monkeypatch.setenv(config.CONFIG_ENV, str(doc_path))
    monkeypatch.setattr(config, "_current", None)
    store = SpecStore([BUILTIN_FIELDS_PATH, spec_path], log=lambda m: None)
    assert not [e for e in store.get().errors if "sdis." in e]
    page = []
    clock = [1_000_000.0]
    sgt = SgtShadow(store=store, read_uia=lambda h: {"lines": list(page)}, log_dir=tmp_path / "log",
                    clock=lambda: clock[0], today=lambda: date(2026, 7, 15), echo=lambda m: None, mode="live")

    def see(lines, url=URL_A):
        clock[0] += 1
        page[:] = ["Fictional portal", "Returns", "Menu"] + lines
        sgt.observe(1, PORTAL, url)
        return sgt.drain()

    def write(doc):
        doc_path.write_text(json.dumps(doc), encoding="utf-8")
        config.reload([config.BUILTIN_PATH, doc_path])
    return sgt, see, write


def test_sgt_writes_the_instance_and_its_status_climbs(rig):
    sgt, see, write = rig
    write(doc_count())
    assert see(["Reference number", "QX40417"]) == []                  # waits for the period
    rows = see(["Return period", "PR2026"])
    assert [r["status"] for r in rows] == ["Draft"]
    row = rows[0]
    assert row["filing_type"] == "Fictional return" and row["period_label"] == "PR2026"
    assert row["dataset_key"].endswith(":FICTIONALRETURN:PR2026") and row["capture_method"] == "SGT_live"
    assert row["raw_payload"]["sgt_dataset"]["evidence"]["k_of_n"] == "2 of 4"
    rows = see(["Amount code", "A120"], URL_B)
    assert rows[0]["status"] == "Submitted (Not Verified)" and rows[0]["dataset_key"] == row["dataset_key"]
    rows = see(["Acknowledgement code", "ZK13358"], URL_B)
    assert rows[0]["status"] == "Submitted & Verified"


def test_sdis_hits_never_reach_sgts_own_dataset(rig):
    sgt, see, write = rig
    write(doc_count())
    see(["Return period", "PR2026"])
    s = sgt._sessions[1]
    assert not s.draft.pieces and not s.slots


def test_a_changed_document_requeues_without_lowering(rig):
    sgt, see, write = rig
    write(doc_no_levels())
    assert [r["status"] for r in see(["Return period", "PR2026", "Reference number", "QX40417"])] == ["Draft"]
    write(doc_count(proves={"ref_no": "Complete"}))
    rows = see(["Menu"], URL_B)
    assert [r["status"] for r in rows] == ["Submitted & Verified"]
    write(doc_no_levels())
    assert see(["Menu", "Help"], URL_A) == []                           # nothing moves down


def test_no_containers_means_no_rows(rig):
    sgt, see, write = rig
    assert see(["Return period", "PR2026", "Reference number", "QX40417"]) == []
    sgt.end_all("shutdown")
    assert sgt.drain() == []


def test_others_only_session_writes_one_carrier_row(rig):
    sgt, see, write = rig
    write(doc_count())
    assert see(["Turnover band", "B2"]) == []
    sgt.end_all("shutdown")
    rows = sgt.drain()
    assert len(rows) == 1
    row = rows[0]
    assert row["capture_method"] == "SGT_sdis_info" and row["filing_type"] == "" and row["period_label"] == ""
    assert row["raw_payload"]["sdis"]["others"] == {"turnover_band": "B2"}
    assert row["raw_payload"]["sdis"]["portal"] == PORTAL


def test_dataset_rows_carry_the_others_values_instead_of_a_carrier(rig):
    sgt, see, write = rig
    write(doc_count())
    see(["Turnover band", "B2"])
    rows = see(["Return period", "PR2026"])
    assert rows[0]["raw_payload"]["sdis"]["others"] == {"turnover_band": "B2"}
    sgt.end_all("shutdown")
    assert sgt.drain() == []


# ── The SRPF container (S.4) ─────────────────────────────────────────────────────
def _db(tmp_path):
    import security
    from database import SeraDatabase

    salt_path = str(tmp_path / "test.salt")
    security.generate_and_save_salt(salt_path)
    hex_key = security.derive_key_hex("testpass123", security.load_salt(salt_path))
    return SeraDatabase(str(tmp_path / "test_master.db"), hex_key, defer_startup_maintenance=True)


def _insert(db, method, others, at, period="", form=""):
    payload = {"pan": "ABCPD1234E", "capture_method": method,
               "raw_payload": {"sdis": {"portal": PORTAL, "portal_profile": {"trade_ref": "TR77"},
                                        "others": others, "at": at}}}
    key = db.compute_dataset_key(PORTAL, "ABCPD1234E", form or "SDIS info", period)
    return db.insert_tracker_dump(portal=PORTAL, period_label=period, arn_number="N/A", capture_method=method,
                                  status="Draft" if period else "Not Submitted", raw_payload_json=json.dumps(payload),
                                  pan="ABCPD1234E", filing_type=form, dataset_key=key)


def test_others_reach_raw_aggregates_with_history_and_survive_a_rebuild(tmp_path):
    db = _db(tmp_path)
    _insert(db, "SGT_sdis_info", {"turnover_band": "B2"}, "2026-10-01T10:00:00")
    _insert(db, "SGT_live", {"turnover_band": "B3"}, "2026-10-03T10:00:00", period="PR2026", form="Fictional return")
    _insert(db, "SGT_live", {"turnover_band": "B3"}, "2026-10-04T10:00:00", period="PR2027", form="Fictional return")
    before = db.get_client_raw_container(identity_key="ABCPD1234E")
    band = before["raw_aggregates"][PORTAL]["turnover_band"]
    assert band["value"] == "B3" and band["updated_at"] == "2026-10-03T10:00:00"
    assert [h["value"] for h in band["history"]] == ["B2", "B3"]
    assert before["portal_profiles"] == {PORTAL: {"trade_ref": "TR77"}}
    db.re_resolve_all_tracker_dumps()
    after = db.get_client_raw_container(identity_key="ABCPD1234E")
    assert after["raw_aggregates"] == before["raw_aggregates"]
    assert after["portal_profiles"] == before["portal_profiles"]


def test_a_carrier_row_is_not_counted_or_listed_as_a_dataset(tmp_path):
    db = _db(tmp_path)
    _insert(db, "SGT_sdis_info", {"turnover_band": "B2"}, "2026-10-01T10:00:00")
    assert db.get_tracker_dumps() == []
    cont = db.get_client_raw_container(identity_key="ABCPD1234E")
    assert cont["filing_history"] == [] and cont["total_captures"] == 0
    assert cont["raw_aggregates"][PORTAL]["turnover_band"]["value"] == "B2"
    _insert(db, "SGT_live", {}, "2026-10-03T10:00:00", period="PR2026", form="Fictional return")
    listed = db.get_tracker_dumps()
    assert [r["capture_method"] for r in listed] == ["SGT_live"]
    cont = db.get_client_raw_container(identity_key="ABCPD1234E")
    assert len(cont["filing_history"]) == 1 and cont["total_captures"] == 1


def test_the_container_view_shows_portal_values_with_library_labels(tmp_path):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel
    from ui.windows.tracker_dump_window import PayloadInspectorDialog
    QApplication.instance() or QApplication([])
    db = _db(tmp_path)
    db.add_sdis_mcl("turnover_band", "Turnover band", "code", "info", PORTAL)
    _insert(db, "SGT_sdis_info", {"turnover_band": "B2"}, "2026-10-01T10:00:00")
    item = [c for c in db.get_srpf_containers() if c["identity_key"] == "ABCPD1234E"][0]
    dlg = PayloadInspectorDialog(item, db=db, is_container=True)
    texts = {w.text() for w in dlg.findChildren(QLabel)}
    assert "Turnover band" in texts and "B2" in texts
    dlg.deleteLater()


def test_value_rows_use_the_field_library_labels():
    from sera_db.srpf import fold_sdis, sdis_value_rows
    agg, prof = {}, {}
    fold_sdis(agg, prof, {"portal": PORTAL, "portal_profile": {"trade_ref": "TR77"},
                          "others": {"turnover_band": "B2"}, "at": "1"})
    fold_sdis(agg, prof, {"portal": PORTAL, "portal_profile": {}, "others": {"turnover_band": "B3"}, "at": "2"})
    assert sdis_value_rows(agg, prof, {"turnover_band": "Turnover band"}) == [
        (PORTAL, [("trade_ref", "TR77"), ("Turnover band", "B3  (2 values seen)")])]
