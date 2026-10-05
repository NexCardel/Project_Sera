"""SDIS Part Q: a picked datapoint -> an SGT spec in the synced sdis_fields table -> sdis_fields.json.

Hand-made, fictional values only; every file under tmp_path (SDIS_FIELDS_PATH points there).
"""

import json

import pytest

from core.sdis import register
from core.sgt import sgt_specs

BASE = [sgt_specs.BUILTIN_FIELDS_PATH]


def _datapoint(label="Registration Number:", value_type="code"):
    dp = {"key": (label.lower(), value_type), "label": label, "value_type": value_type, "nodes": [(0, 7)]}
    mem = {"nodes": {7: {"clients": {
        "client 1": {"texts": ["QX40417"], "sure": True},
        "client 2": {"texts": ["ZK13358", "ZK13358"], "sure": True},
        "client 3": {"texts": ["MM55555"], "sure": False},          # unsure pairing: no vote
    }}}}
    return dp, [mem]


@pytest.fixture
def fields_path(tmp_path, monkeypatch):
    p = tmp_path / "sdis_fields.json"
    monkeypatch.setenv(sgt_specs.SDIS_FIELDS_PATH_ENV, str(p))
    return p


def _db(tmp_path):
    import security
    from database import SeraDatabase

    salt_path = str(tmp_path / "test.salt")
    security.generate_and_save_salt(salt_path)
    hex_key = security.derive_key_hex("testpass123", security.load_salt(salt_path))
    return SeraDatabase(str(tmp_path / "test_master.db"), hex_key, defer_startup_maintenance=True)


def test_shapes_count_each_clients_value_once_sure_only():
    dp, mems = _datapoint()
    assert register.value_shapes(dp, mems) == {"AA99999": 2}


def test_draft_spec_loads_through_load_registry(tmp_path):
    dp, mems = _datapoint()
    row = register.draft_spec(dp, mems, "Fictional Portal", base_paths=BASE)
    assert row["section"] == "profile" and row["field"] == "registration_number"
    assert row["name"] == "sdis.registration_number" and row["label"] == "Registration Number"
    assert row["spec"]["labels"] == ["Registration Number"] and row["spec"]["portals"] == ["Fictional Portal"]
    out = register.write_fields_file([dict(row, status="active")], tmp_path / "sdis_fields.json")
    reg = sgt_specs.load_registry(BASE + [out])
    spec = reg.by_name()["sdis.registration_number"]
    assert spec.field == "registration_number" and spec.slot == "profile"
    assert not [e for e in reg.errors if "sdis." in e]


def test_field_name_never_lands_on_an_existing_field():
    taken = register._taken_fields(BASE)
    assert "pan" in taken
    assert register.field_name("PAN", taken) == "pan_2"


def test_dataset_and_others_fields_are_current_dataset_specs_untyped_are_not_registered(tmp_path):
    # W4-7: dataset / Others fields never latch as profile; SGT hands them to the containers
    dp, mems = _datapoint()
    for kind in ("dataset", "others"):
        row = register.draft_spec(dp, mems, "Fictional Portal", container=kind, base_paths=BASE)
        assert row["section"] == "current_dataset"
        out = register.write_fields_file([dict(row, status="active")], tmp_path / f"{kind}.json")
        spec = sgt_specs.load_registry(BASE + [out]).by_name()[row["name"]]
        assert spec.slot == "current"
    dp, mems = _datapoint(value_type="label")
    with pytest.raises(register.NotRegistrable):
        register.draft_spec(dp, mems, "Fictional Portal", base_paths=BASE)


def test_rename_keeps_the_field_and_retire_removes_it(tmp_path, fields_path):
    db = _db(tmp_path)
    dp, mems = _datapoint()
    row = register.draft_spec(dp, mems, "Fictional Portal", base_paths=BASE)
    gid = db.add_sdis_field(row["name"], row["portal"], row["section"], row["spec"], row["label"], "tester")
    before = json.loads(fields_path.read_text(encoding="utf-8"))["profile"][0]
    assert db.rename_sdis_field(gid, "Reg. no. (my name)")
    after = json.loads(fields_path.read_text(encoding="utf-8"))["profile"][0]
    assert after["note"] == "Reg. no. (my name)"
    assert {k: v for k, v in after.items() if k != "note"} == {k: v for k, v in before.items() if k != "note"}
    # a later registration of the same spec never overwrites the user's label
    db.add_sdis_field(row["name"], row["portal"], row["section"], row["spec"], row["label"])
    assert db.list_sdis_fields()[0]["label"] == "Reg. no. (my name)"
    reg = sgt_specs.load_registry(BASE + [fields_path])
    assert reg.by_name()["sdis.registration_number"].field == "registration_number"
    assert db.retire_sdis_field(gid)
    assert not fields_path.exists()
    assert db.list_sdis_fields()[0]["status"] == "retired" and db.list_sdis_fields(active_only=True) == []


def test_empty_table_writes_no_file(tmp_path, fields_path):
    db = _db(tmp_path)
    assert register.refresh(db) is None
    assert not fields_path.exists()


def test_decisions_one_row_per_signature(tmp_path):
    db = _db(tmp_path)
    g1 = db.set_sdis_decision("sig-1", "rejected", "")
    g2 = db.set_sdis_decision("sig-1", "moved:dataset", "")
    assert g1 == g2
    assert [(d["signature"], d["decision"]) for d in db.list_sdis_decisions()] == [("sig-1", "moved:dataset")]


def test_spec_store_loads_the_third_file_and_a_missing_one_is_fine(fields_path):
    assert sgt_specs.default_paths()[-1] == fields_path
    assert not fields_path.exists()
    plain = sgt_specs.load_registry(sgt_specs.default_paths()[:2])
    with_missing = sgt_specs.SpecStore(log=lambda *_: None).get()
    assert set(with_missing.by_name()) == set(plain.by_name())
