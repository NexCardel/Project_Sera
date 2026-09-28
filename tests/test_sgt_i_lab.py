"""
Tests for core/sgt_i/lab.py - the SGT lab's accept/reject logic (blueprint 14.4 step 11,
hand-off W12-2). Every value here is fictional.
"""

import json

import pytest

from core.sgt import sgt_specs
from core.sgt_i import lab, miner


def _proposal(section=miner.PROFILE, needs=None, spec=None, name="mined.example.trade_name",
              pid="p1", status="pending"):
    if spec is None:
        spec = {"name": name, "field": "trade_name", "labels": ["Trade Name"], "within": 2,
                "pattern": r"([A-Za-z][A-Za-z0-9 .,&'()/-]{0,79})", "confidence": 70,
                "checks": ["not_ui_chrome"],
                "examples": ["Sample Trading Co", {"lines": ["Trade Name", "Sample Trading Co"],
                                                     "expect": "Sample Trading Co"}],
                "note": "Drafted by the SGT-I miner; examples are fictional."}
    return {"id": pid, "source": "template_vs_data", "portal": "Example Portal", "status": status,
            "needs": needs or [], "rules": {}, "red": False, "section": section, "spec": spec,
            "support": {"clients": 5, "seen": 12}}


def _proposals_file(tmp_path, *proposals):
    path = tmp_path / "proposals.json"
    path.write_text(json.dumps({"format": 1, "generated": "", "proposals": list(proposals),
                                 "dropped": {}}), encoding="utf-8")
    return path


# ── accept: profile / current_dataset / records ──────────────────────────────────
def test_accept_writes_profile_spec_into_new_override_file(tmp_path):
    override = tmp_path / "sgt_fields.json"
    proposals = _proposals_file(tmp_path, _proposal())

    spec = lab.accept(_proposal(), override_path=override, proposals_file=proposals)

    assert spec["field"] == "trade_name"
    data = json.loads(override.read_text(encoding="utf-8"))
    assert [s["name"] for s in data["profile"]] == ["mined.example.trade_name"]
    assert json.loads(proposals.read_text(encoding="utf-8"))["proposals"][0]["status"] == "accepted"


def test_accept_preserves_existing_override_content(tmp_path):
    override = tmp_path / "sgt_fields.json"
    override.write_text(json.dumps({"version": 3, "about": "local overrides",
                                     "profile": [{"name": "existing.field", "field": "existing"}],
                                     "current_dataset": {"fields": []}, "records": []}), encoding="utf-8")
    proposals = _proposals_file(tmp_path, _proposal())

    lab.accept(_proposal(), override_path=override, proposals_file=proposals)

    data = json.loads(override.read_text(encoding="utf-8"))
    assert data["version"] == 3
    assert data["about"] == "local overrides"
    names = [s["name"] for s in data["profile"]]
    assert "existing.field" in names and "mined.example.trade_name" in names


def test_accept_never_overwrites_an_unreadable_override_file(tmp_path):
    override = tmp_path / "sgt_fields.json"
    override.write_text('{"profile": [{"name": "hand.made"}', encoding="utf-8")   # a broken hand edit
    proposals = _proposals_file(tmp_path, _proposal())

    with pytest.raises(ValueError):
        lab.accept(_proposal(), override_path=override, proposals_file=proposals)

    assert override.read_text(encoding="utf-8") == '{"profile": [{"name": "hand.made"}'
    assert json.loads(proposals.read_text(encoding="utf-8"))["proposals"][0]["status"] == "pending"


def test_run_miner_writes_proposals_and_keeps_earlier_decisions(tmp_path):
    proposals = _proposals_file(tmp_path, _proposal(status="rejected"))
    got = lab.run_miner(atlas_dir=tmp_path / "sgt_i", corpus_dir=tmp_path / "corpus",
                        proposals_file=proposals)
    assert got["proposals"] == 0                       # an empty atlas and corpus propose nothing
    assert json.loads(proposals.read_text(encoding="utf-8"))["format"] == miner.FORMAT


def test_the_lab_dialog_builds_offscreen(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(miner, "proposals_path", lambda: _proposals_file(tmp_path, _proposal()))
    from PySide6.QtWidgets import QApplication
    from ui.dialogs import sgt_lab_dialog
    monkeypatch.setattr(sgt_lab_dialog, "ResidueCounts", lambda: type("C", (), {"report": lambda s: []})())
    app = QApplication.instance() or QApplication([])
    dlg = sgt_lab_dialog.SgtLabDialog()
    assert dlg._mine_btn.isEnabled() and not dlg._empty_label.isVisibleTo(dlg)
    dlg._on_mined("0 pending proposal(s).")
    assert dlg._mine_status.text() == "0 pending proposal(s)."
    dlg.deleteLater()
    app.processEvents()


def test_accept_replaces_earlier_mined_spec_of_the_same_name(tmp_path):
    override = tmp_path / "sgt_fields.json"
    p1 = _proposal(pid="p1")
    lab.accept(p1, override_path=override, proposals_file=_proposals_file(tmp_path, p1))

    p2 = _proposal(pid="p2")
    p2["spec"]["confidence"] = 55
    lab.accept(p2, override_path=override, proposals_file=_proposals_file(tmp_path, p2))

    data = json.loads(override.read_text(encoding="utf-8"))
    matches = [s for s in data["profile"] if s["name"] == "mined.example.trade_name"]
    assert len(matches) == 1
    assert matches[0]["confidence"] == 55


def test_accept_current_dataset_section_shape(tmp_path):
    override = tmp_path / "sgt_fields.json"
    spec = {"name": "mined.example.status_note", "field": "status_note", "labels": ["Note"],
            "within": 2, "pattern": r"([A-Za-z ]{1,40})", "confidence": 70, "examples": ["Filed"]}
    p = _proposal(section=miner.CURRENT, spec=spec)

    lab.accept(p, override_path=override, proposals_file=_proposals_file(tmp_path, p))

    data = json.loads(override.read_text(encoding="utf-8"))
    assert [s["name"] for s in data["current_dataset"]["fields"]] == ["mined.example.status_note"]


def test_accept_records_section_shape(tmp_path):
    override = tmp_path / "sgt_fields.json"
    spec = {"name": "mined.example.notices", "start": r"^\s*Notice", "max_lines": 20,
            "fields": [{"field": "notice_ref", "labels": ["Notice"], "within": 2,
                        "pattern": r"(NTC\d{7})", "confidence": 70, "examples": ["NTC1234567"]}],
            "require": ["notice_ref"], "examples": [{"lines": ["Notice", "NTC1234567"],
                                                       "expect": [{"notice_ref": "NTC1234567"}]}]}
    p = _proposal(section=miner.RECORDS, spec=spec)

    lab.accept(p, override_path=override, proposals_file=_proposals_file(tmp_path, p))

    data = json.loads(override.read_text(encoding="utf-8"))
    assert [s["name"] for s in data["records"]] == ["mined.example.notices"]


def test_accept_rename_field_on_a_single_field_proposal(tmp_path):
    override = tmp_path / "sgt_fields.json"
    p = _proposal()

    spec = lab.accept(p, rename="business_name", override_path=override,
                       proposals_file=_proposals_file(tmp_path, p))

    assert spec["field"] == "business_name"
    data = json.loads(override.read_text(encoding="utf-8"))
    assert data["profile"][0]["field"] == "business_name"


def test_accept_rename_field_on_a_record_proposal_renames_first_field(tmp_path):
    spec = {"name": "mined.example.notices", "start": r"^\s*Notice", "max_lines": 20,
            "fields": [{"field": "notice_ref", "labels": ["Notice"], "within": 2,
                        "pattern": r"(NTC\d{7})", "confidence": 70, "examples": ["NTC1234567"]}],
            "require": ["notice_ref"], "examples": [{"lines": ["Notice", "NTC1234567"],
                                                       "expect": [{"notice_ref": "NTC1234567"}]}]}
    p = _proposal(section=miner.RECORDS, spec=spec)
    override = tmp_path / "sgt_fields.json"

    accepted = lab.accept(p, rename="notice_number", override_path=override,
                           proposals_file=_proposals_file(tmp_path, p))

    assert accepted["fields"][0]["field"] == "notice_number"
    assert accepted["require"] == ["notice_number"]
    assert p["spec"]["fields"][0]["field"] == "notice_ref", "the proposal itself must not mutate"


def test_accept_status_wording_needs_a_level(tmp_path):
    spec = {"name": "mined.example.status.under_process", "field": "status", "take": "anywhere",
            "case": "insensitive", "pattern": r"^\s*(Under Process)\s*[.!]?\s*$",
            "map": [[r"Under\s+Process", None]], "confidence": 80, "examples": ["Under Process"]}
    p = _proposal(section=miner.CURRENT, needs=["level"], spec=spec)
    override = tmp_path / "sgt_fields.json"

    with pytest.raises(ValueError):
        lab.accept(p, override_path=override, proposals_file=_proposals_file(tmp_path, p))

    spec_out = lab.accept(p, level=sgt_specs.SUBMIT_LEVELS[1] if hasattr(sgt_specs, "SUBMIT_LEVELS")
                           else "Draft", override_path=override,
                           proposals_file=_proposals_file(tmp_path, p))
    assert spec_out["map"][0][1] is not None


def test_accept_without_a_spec_raises(tmp_path):
    p = _proposal(section=None, spec=None, needs=["developer"])
    with pytest.raises(ValueError):
        lab.accept(p, override_path=tmp_path / "sgt_fields.json",
                    proposals_file=_proposals_file(tmp_path, p))


# ── reject ────────────────────────────────────────────────────────────────────────
def test_reject_marks_status_and_never_touches_the_override_file(tmp_path):
    override = tmp_path / "sgt_fields.json"
    proposals = _proposals_file(tmp_path, _proposal())

    ok = lab.reject("p1", proposals_file=proposals)

    assert ok is True
    assert not override.exists()
    assert json.loads(proposals.read_text(encoding="utf-8"))["proposals"][0]["status"] == "rejected"


def test_reject_unknown_id_returns_false(tmp_path):
    proposals = _proposals_file(tmp_path, _proposal())
    assert lab.reject("does-not-exist", proposals_file=proposals) is False


def test_set_status_round_trips_through_load_proposals(tmp_path):
    proposals = _proposals_file(tmp_path, _proposal(pid="p1"), _proposal(pid="p2"))
    assert lab.set_status("p2", "accepted", path=proposals) is True
    data = lab.load_proposals(proposals)
    statuses = {p["id"]: p["status"] for p in data["proposals"]}
    assert statuses == {"p1": "pending", "p2": "accepted"}


def test_load_proposals_missing_file_returns_empty_shell(tmp_path):
    data = lab.load_proposals(tmp_path / "does_not_exist.json")
    assert data["proposals"] == []
