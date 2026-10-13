"""SDIS Parts U + S.3: the sdis_mcl field library and the containers file (checks, levels, sync rows).

Hand-made, fictional data only; every file under tmp_path (SDIS_FIELDS_PATH / SDIS_CONTAINERS_PATH
point there).
"""

import copy
import json

import pytest

from core.sdis import config, register
from core.sdis.classes import suggest
from core.sgt import sgt_specs

BASE = [sgt_specs.BUILTIN_FIELDS_PATH]
PORTALS = ["GST Portal", "Income Tax"]
FIELDS = ["pan", "legal_name", "name", "tax_period", "tax_paid", "arn", "filing_date", "aggregate_turnover"]
KNOWN = {"fields": FIELDS, "portals": PORTALS}

# The blueprint's S.3 example, word for word.
EXAMPLE = {
    "version": 1,
    "levels": [{"name": "Draft", "when": {"captured": 1}},
               {"name": "In progress", "when": {"captured": "51%"}},
               {"name": "Complete", "when": {"captured": "all"}}],
    "level_map": {"Draft": "Draft", "In progress": "Submitted (Not Verified)",
                  "Complete": "Submitted & Verified"},
    "profile": {"GST Portal": ["pan", "legal_name"], "Income Tax": ["pan", "name"]},
    "containers": [
        {"name": "GSTR-3B submission", "portal": "GST Portal",
         "form_field": None, "period_field": "tax_period",
         "fields": ["tax_period", "tax_paid", "arn", "filing_date"],
         "exceptions": {"optional": ["filing_date"],
                        "proves": {"arn": "Complete"},
                        "levels": None, "level_map": None},
         "examples": [{"captured": ["tax_period"], "level": "Draft"},
                      {"captured": ["tax_period", "filing_date"], "level": "Draft"},
                      {"captured": ["tax_period", "tax_paid"], "level": "In progress"},
                      {"captured": ["tax_period", "arn"], "level": "Complete"}]}
    ],
    "others": {"GST Portal": ["aggregate_turnover"]},
    "class_exceptions": {"aggregate_turnover": "info"},
    "portal_exceptions": {"extra_domains": {"Some Portal": ["work.example.gov.in"]},
                          "never_register": ["login.microsoftonline.com"]},
}


def _doc():
    return copy.deepcopy(EXAMPLE)


def _c(doc):
    return doc["containers"][0]


@pytest.fixture(autouse=True)
def _fresh_config():
    yield
    config._current, config._errors = None, []         # refresh() reloads the config in force


@pytest.fixture
def paths(tmp_path, monkeypatch):
    f = tmp_path / "sdis_fields.json"
    c = tmp_path / "sdis_containers.json"
    monkeypatch.setenv(sgt_specs.SDIS_FIELDS_PATH_ENV, str(f))
    monkeypatch.setenv(config.CONFIG_ENV, str(c))
    return f, c


def _db(tmp_path):
    import security
    from database import SeraDatabase

    salt_path = str(tmp_path / "test.salt")
    security.generate_and_save_salt(salt_path)
    hex_key = security.derive_key_hex("testpass123", security.load_salt(salt_path))
    return SeraDatabase(str(tmp_path / "test_master.db"), hex_key, defer_startup_maintenance=True)


def _library(db):
    for f in FIELDS:
        db.add_sdis_mcl(f, f.replace("_", " ").title(), "text", "dataset")


# ── the document and its levels ──────────────────────────────────────────────

def test_blueprint_example_passes_and_its_examples_give_their_levels():
    doc = _doc()
    config.check(doc, **KNOWN)
    c = _c(doc)
    assert config.k_of_n(c, []) == (0, 3)                    # filing_date is optional: n = 3
    assert config.level_for(c, [], doc) is None
    assert config.level_for(c, ["tax_period"], doc) == "Draft"
    assert config.level_for(c, ["tax_period", "filing_date"], doc) == "Draft"
    assert config.level_for(c, ["tax_period", "tax_paid"], doc) == "In progress"   # ceil(51% of 3) = 2
    assert config.level_for(c, ["arn"], doc) == "Complete"                           # proves
    assert config.level_for(c, ["tax_period", "tax_paid", "arn"], doc) == "Complete"
    assert config.level_map_of(c, doc)["Complete"] == "Submitted & Verified"


def test_no_levels_passes_and_level_for_gives_none():
    doc = _doc()
    doc["levels"], doc["level_map"] = [], {}
    c = _c(doc)
    c["exceptions"]["proves"] = {}
    for ex in c["examples"]:
        ex["level"] = None
    config.check(doc, **KNOWN)
    assert config.level_for(c, ["tax_period", "tax_paid", "arn"], doc) is None
    assert config.k_of_n(c, ["tax_period", "tax_paid", "arn"]) == (3, 3)
    assert config.empty()["levels"] == [] and config.empty()["containers"] == []


def test_builtin_file_has_no_levels_and_no_containers():
    data = json.loads(config.BUILTIN_PATH.read_text(encoding="utf-8"))
    assert not data.get("levels") and not data.get("containers")
    config.check(data)


def test_a_containers_own_levels_replace_the_files():
    doc = _doc()
    c = _c(doc)
    c["exceptions"]["levels"] = [{"name": "Seen", "when": {"fields": ["tax_period"]}},
                                 {"name": "Paid", "when": {"captured": 2, "fields": ["tax_paid"]}}]
    c["exceptions"]["level_map"] = {"Paid": "Submitted (Not Verified)"}
    c["exceptions"]["proves"] = {"arn": "Paid"}
    c["examples"] = [{"captured": ["tax_period"], "level": "Seen"},
                     {"captured": ["tax_paid"], "level": None},
                     {"captured": ["tax_period", "tax_paid"], "level": "Paid"},
                     {"captured": ["arn"], "level": "Paid"}]
    config.check(doc, **KNOWN)
    assert config.level_for(c, ["tax_period", "tax_paid", "arn"], doc) == "Paid"
    assert config.level_map_of(c, doc) == {"Paid": "Submitted (Not Verified)"}


def test_share_rounds_up_and_all_means_every_counted_field():
    c = {"name": "x", "portal": "GST Portal", "fields": ["a", "b", "c", "d", "e"], "exceptions": {}}
    doc = {"levels": [{"name": "L1", "when": {"captured": "40%"}}, {"name": "L2", "when": {"captured": "all"}}]}
    assert config.level_for(c, ["a"], doc) is None                 # ceil(2.0) = 2
    assert config.level_for(c, ["a", "b"], doc) == "L1"
    assert config.level_for(c, ["a", "b", "c", "d", "e"], doc) == "L2"


# ── every load check refuses its own broken document ─────────────────────────

def _set(path, value):
    def f(doc):
        tgt = doc
        for k in path[:-1]:
            tgt = tgt[k]
        tgt[path[-1]] = value
    return f


BROKEN = {
    "field not in sdis_mcl": _set(["containers", 0, "fields", 1], "no_such_field"),
    "profile portal not registered": _set(["profile", "Nowhere"], ["pan"]),
    "profile not per portal": _set(["profile"], ["pan"]),
    "others portal not registered": _set(["others", "Nowhere"], ["aggregate_turnover"]),
    "container portal not registered": _set(["containers", 0, "portal"], "Nowhere"),
    "period_field not a field": _set(["containers", 0, "period_field"], "pan"),
    "form_field not a field": _set(["containers", 0, "form_field"], "pan"),
    "optional not a field": _set(["containers", 0, "exceptions", "optional"], ["pan"]),
    "optional names the period": _set(["containers", 0, "exceptions", "optional"], ["tax_period"]),
    "proves not a field": _set(["containers", 0, "exceptions", "proves"], {"pan": "Complete"}),
    "proves level undefined": _set(["containers", 0, "exceptions", "proves"], {"arn": "Done"}),
    "level names twice": _set(["levels", 1, "name"], "Draft"),
    "when unknown key": _set(["levels", 0, "when"], {"seen": 1}),
    "share below 1%": _set(["levels", 1, "when"], {"captured": "0%"}),
    "share above 100%": _set(["levels", 1, "when"], {"captured": "101%"}),
    "when field not in container": _set(["levels", 0, "when"], {"captured": 1, "fields": ["pan"]}),
    "level_map key undefined": _set(["level_map", "Done"], "Draft"),
    "level_map value not a ladder status": _set(["level_map", "Draft"], "Filed"),
    "no counted field": lambda d: (_set(["containers", 0, "period_field"], None)(d), _set(
        ["containers", 0, "exceptions", "optional"], ["tax_period", "tax_paid", "arn", "filing_date"])(d)),
    "field in two containers": _set(["others", "GST Portal"], ["aggregate_turnover", "pan"]),
    "example gives another level": _set(["containers", 0, "examples", 0, "level"], "Complete"),
    "example field not in container": _set(["containers", 0, "examples", 0, "captured"], ["pan"]),
    "class exception unknown class": _set(["class_exceptions", "aggregate_turnover"], "furniture"),
    "never_register bare suffix": _set(["portal_exceptions", "never_register"], ["gov.in"]),
    "extra_domains a URL": _set(["portal_exceptions", "extra_domains"], {"Some Portal": ["https://x.gov.in/a"]}),
    "container unknown key": _set(["containers", 0, "colour"], "red"),
    "container named twice": lambda d: d["containers"].append(copy.deepcopy(d["containers"][0])),
}


@pytest.mark.parametrize("case", sorted(BROKEN))
def test_each_load_check_refuses_its_broken_document(case, tmp_path):
    doc = _doc()
    BROKEN[case](doc)
    with pytest.raises(config.ConfigError):
        config.check(doc, **KNOWN)
    # loading a file: membership checks need the database, the rest refuse at load
    if case.startswith(("field not in", "profile portal not", "others portal not", "container portal not")):
        return
    good, bad = tmp_path / "good.json", tmp_path / "bad.json"
    good.write_text(json.dumps(EXAMPLE), encoding="utf-8")
    bad.write_text(json.dumps(doc), encoding="utf-8")
    previous, errors = config.load([config.BUILTIN_PATH, good])
    assert not errors and previous["containers"] == EXAMPLE["containers"]
    cfg, errors = config.load([config.BUILTIN_PATH, bad], previous=previous)
    assert errors and cfg is previous


def test_put_refuses_a_failing_document_and_the_previous_version_stays(tmp_path, paths):
    db = _db(tmp_path)
    _library(db)
    assert db.get_sdis_containers() is None
    assert db.put_sdis_containers(_doc(), "tester", portals=PORTALS) == 1
    assert json.loads(paths[1].read_text(encoding="utf-8")) == EXAMPLE
    bad = _doc()
    BROKEN["example gives another level"](bad)
    with pytest.raises(config.ConfigError):
        db.put_sdis_containers(bad, "tester", portals=PORTALS)
    with pytest.raises(config.ConfigError):
        db.put_sdis_containers(_doc(), "tester", portals=["Income Tax"])     # GST Portal not registered
    row = db.get_sdis_containers()
    assert row["version"] == 1 and row["doc"] == EXAMPLE
    assert json.loads(paths[1].read_text(encoding="utf-8")) == EXAMPLE
    doc = config.remove_field(_doc(), "GSTR-3B submission", "filing_date", **KNOWN)
    assert db.put_sdis_containers(doc, "tester", portals=PORTALS) == 2
    assert db.get_sdis_containers()["doc"] == doc


def test_export_and_import(tmp_path, paths):
    db = _db(tmp_path)
    _library(db)
    db.put_sdis_containers(_doc(), portals=PORTALS)
    out = config.export(db, tmp_path / "exported.json")
    data = json.loads(out.read_text(encoding="utf-8"))
    data["containers"][0]["examples"][0]["level"] = "Complete"
    out.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.import_file(db, out, portals=PORTALS)
    assert db.get_sdis_containers()["version"] == 1
    data["containers"][0]["examples"][0]["level"] = "Draft"
    data["containers"][0]["name"] = "Monthly summary"
    out.write_text(json.dumps(data), encoding="utf-8")
    assert config.import_file(db, out, portals=PORTALS) == 2
    assert db.get_sdis_containers()["doc"]["containers"][0]["name"] == "Monthly summary"


# ── editing helpers ──────────────────────────────────────────────────────────

def test_editing_helpers_return_new_documents_that_pass():
    doc = _doc()
    d = config.add_container(doc, "Refund application", "GST Portal", ["refund_amount"],
                             fields=FIELDS + ["refund_amount"], portals=PORTALS)
    assert len(doc["containers"]) == 1 and len(d["containers"]) == 2
    d = config.rename_container(d, "Refund application", "Refund", fields=FIELDS + ["refund_amount"])
    d = config.mark_key(d, "Refund", "refund_amount", "form")
    d = config.delete_container(d, "Refund")
    assert d["containers"] == doc["containers"]
    d = config.add_field(d, ("others", "Income Tax"), "aggregate_turnover", **KNOWN)
    with pytest.raises(config.ConfigError):            # the arn example claims Complete
        config.set_exception(d, "GSTR-3B submission", "proves", "arn", None, **KNOWN)
    _c(d)["examples"] = []
    d = config.set_exception(d, "GSTR-3B submission", "proves", "arn", None, **KNOWN)
    assert config.level_for(_c(d), ["arn"], d) == "Draft"
    d2 = config.set_exception(d, "GSTR-3B submission", "optional", "tax_paid", True, **KNOWN)
    assert config.k_of_n(_c(d2), []) == (0, 2)
    d = config.set_levels(d, None, container=None)
    assert d["levels"] == [] and d["level_map"] == {}
    with pytest.raises(config.ConfigError):            # its examples still claim levels
        config.set_levels(_doc(), None)
    with pytest.raises(config.ConfigError):            # pan is in GST Portal's Profile builder
        config.add_field(_doc(), "GSTR-3B submission", "pan", **KNOWN)
    with pytest.raises(config.ConfigError):            # a period field is never optional
        config.set_exception(_doc(), "GSTR-3B submission", "optional", "tax_period", True)
    with pytest.raises(config.ConfigError):            # a container needs a counted field
        config.add_container(_doc(), "Empty", "GST Portal", [])


def test_remove_field_clears_its_marks_and_examples():
    with pytest.raises(config.ConfigError):     # n becomes 2: the tax_paid example now gives Complete
        config.remove_field(_doc(), "GSTR-3B submission", "arn", **KNOWN)
    d = config.remove_field(_doc(), "GSTR-3B submission", "filing_date", **KNOWN)
    c = _c(d)
    assert "filing_date" not in c["fields"] and c["exceptions"]["optional"] == []
    assert len(c["examples"]) == 3
    d = config.remove_field(d, "GSTR-3B submission", "tax_period", **KNOWN)
    assert _c(d)["period_field"] is None and _c(d)["examples"] == []


# ── the library, registration and the two files ──────────────────────────────

def _datapoint(label="Registration Number:"):
    dp = {"key": (label.lower(), "code"), "label": label, "value_type": "code", "nodes": [(0, 7)]}
    mem = {"nodes": {7: {"clients": {
        "client 1": {"texts": ["QX40417"], "sure": True},
        "client 2": {"texts": ["ZK13358"], "sure": True},
    }}}}
    return dp, [mem]


def test_one_field_on_two_pages_is_two_specs_and_rename_reaches_both(tmp_path, paths):
    db = _db(tmp_path)
    dp, mems = _datapoint()
    a = register.register_field(db, dp, mems, "GST Portal", created_by="tester", base_paths=BASE)
    b = register.register_field(db, dp, mems, "Income Tax", field=a["field"], base_paths=BASE)
    again = register.register_field(db, dp, mems, "Income Tax", field=a["field"], base_paths=BASE)
    assert a["mcl_gid"] == b["mcl_gid"] == again["mcl_gid"] and again["name"] == b["name"]
    assert (a["name"], b["name"]) == ("sdis.registration_number", "sdis.registration_number.2")
    lib = db.list_sdis_mcl()
    assert len(lib) == 1 and lib[0]["name"] == "registration_number" and lib[0]["portal"] == "all"
    specs = db.list_sdis_fields()
    assert len(specs) == 2 and {s["mcl_gid"] for s in specs} == {a["mcl_gid"]}
    reg = sgt_specs.load_registry(BASE + [paths[0]])
    assert {reg.by_name()[s["name"]].field for s in specs} == {"registration_number"}
    assert not [e for e in reg.errors if "sdis." in e]
    before = paths[0].read_text(encoding="utf-8")
    assert db.rename_sdis_mcl(a["mcl_gid"], "Reg. no. (fictional)")
    assert db.list_sdis_mcl()[0]["label"] == "Reg. no. (fictional)"
    assert {s["label"] for s in db.list_sdis_fields()} == {"Reg. no. (fictional)"}
    notes = [s["note"] for s in json.loads(paths[0].read_text(encoding="utf-8"))["profile"]]
    assert notes == ["Reg. no. (fictional)"] * 2
    strip = lambda t: [{k: v for k, v in s.items() if k != "note"} for s in json.loads(t)["profile"]]
    assert strip(paths[0].read_text(encoding="utf-8")) == strip(before)
    with pytest.raises(register.NotRegistrable):
        register.register_field(db, dp, mems, "GST Portal", field="no_such_field", base_paths=BASE)


def test_moving_a_field_between_containers_leaves_the_fields_file_byte_identical(tmp_path, paths):
    db = _db(tmp_path)
    _library(db)
    dp, mems = _datapoint()
    db.add_sdis_mcl("refund_amount", "Refund amount", "amount", "dataset")
    reg = register.register_field(db, dp, mems, "GST Portal", base_paths=BASE)
    known = {"fields": FIELDS + ["refund_amount", reg["field"]], "portals": PORTALS}
    doc = config.add_container(_doc(), "Refund application", "GST Portal", ["refund_amount"], **known)
    doc = config.add_field(doc, ("profile", "GST Portal"), reg["field"], **known)
    db.put_sdis_containers(doc, portals=PORTALS)
    before = paths[0].read_bytes()
    doc = config.move_field(doc, reg["field"], ("profile", "GST Portal"), "Refund application", **known)
    db.put_sdis_containers(doc, portals=PORTALS)
    assert paths[0].read_bytes() == before
    doc = config.move_field(doc, reg["field"], "Refund application", ("others", "GST Portal"), **known)
    assert db.put_sdis_containers(doc, portals=PORTALS) == 3
    assert reg["field"] in json.loads(paths[1].read_text(encoding="utf-8"))["others"]["GST Portal"]
    assert paths[0].read_bytes() == before


def test_empty_tables_change_no_file(tmp_path, paths):
    db = _db(tmp_path)
    assert config.refresh(db) is None and register.refresh(db) is None
    assert not paths[0].exists() and not paths[1].exists()
    paths[1].write_text("{}", encoding="utf-8")
    assert config.refresh(db) is None and paths[1].read_text(encoding="utf-8") == "{}"


def test_refresh_writes_the_row_once_and_skips_same_bytes(tmp_path, paths):
    db = _db(tmp_path)
    _library(db)
    db.put_sdis_containers(_doc(), portals=PORTALS)
    paths[1].unlink()
    assert config.refresh(db) == paths[1]
    assert config.refresh(db) is None                  # same bytes: not touched
    assert not paths[1].with_name(paths[1].name + ".tmp").exists()


def test_tables_are_replicated():
    import sync_schema
    for name in ("sdis_mcl", "sdis_config"):
        spec = sync_schema.get(name)
        assert spec.mode == sync_schema.LWW and spec.row_key == ("gid",)


# ── class suggestion honours class_exceptions ────────────────────────────────

def test_suggest_honours_class_exceptions():
    class DP:
        key = ("aggregate turnover", "amount")
        nodes = []
    out = suggest([DP()], [], class_exceptions={"aggregate_turnover": "info"},
                  field_of={("Aggregate Turnover:", "amount"): "aggregate_turnover"})
    assert out[DP.key] == ("info", "class exception in the containers file")
    assert suggest([DP()], [])[DP.key][0] is None
    out = suggest([DP()], [], moves={DP.key: "profile"}, class_exceptions={"aggregate_turnover": "info"},
                  field_of={DP.key: "aggregate_turnover"})
    assert out[DP.key][0] == "profile"              # a user's move still wins
