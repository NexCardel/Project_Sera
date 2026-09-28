"""
Tests for SGT step 10: what no Core spec claimed (core/sgt_i/residues.py).
All values are fictional.
"""
import json
from pathlib import Path
from types import SimpleNamespace

from core.sgt_i import default_components
from core.sgt_i.residues import (
    RESIDUES_FILE, ResidueCounts, ResiduesComponent, claimed_values, page_residue,
    split_inline_labels,
)

PAGE = ["Filing confirmation", "Reference No: ABCPD1234E", "Date of filing: 12-08-2026",
        "Acknowledgement Number", "123456789150726", "Status", "Filed"]


def _result(ack=None):
    return {"profile": {}, "current": {"ack": {"value": ack}} if ack else {}, "datasets": []}


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_split_inline_labels():
    assert split_inline_labels(["PAN: ABCPD1234E", "Heading:", "https://x.test/a"]) == \
        ["PAN:", "ABCPD1234E", "Heading:", "https://x.test/a"]


def test_page_residue_counts_seen_and_claimed_by_shape():
    r = page_residue(PAGE, _result("123456789150726"))
    assert r["kind"] == "confirmation"
    assert r["seen"] == {"code AAAAA9999A": 1, "date 99-99-9999": 1, "number 999999999999999": 1}
    assert r["claimed"] == {"number 999999999999999": 1}


def test_page_residue_keeps_no_raw_value():
    r = page_residue(PAGE, _result())
    text = json.dumps(r)
    assert "ABCPD1234E" not in text and "123456789150726" not in text


def test_claimed_value_matches_inside_a_longer_capture():
    r = page_residue(PAGE, _result("Ack 123456789150726 dated 12-08-2026"))
    assert r["claimed"].get("number 999999999999999") == 1


def test_page_without_typed_values_is_none():
    assert page_residue(["hello world", "Status", "Filed"], None) is None
    assert page_residue([], None) is None


def test_claimed_values_reads_datasets():
    got = claimed_values({"datasets": [{"values": {"period": "Aug 2026"}}]})
    assert got == {"aug2026"}


def test_counts_accumulate_report_and_persist(tmp_path: Path):
    clock = _Clock()
    counts = ResidueCounts(directory=tmp_path, clock=clock)
    r = page_residue(PAGE, _result("123456789150726"))
    counts.add("Portal A", r)
    counts.add("Portal A", r)
    report = counts.report()
    assert report[0].startswith("Portal A confirmation pages:")
    assert "seen 2x, claimed 0x" in report[0]
    assert not any("999999999999999" in row for row in report)   # fully claimed: no blind spot
    clock.t += 120
    counts.add("Portal A", r)                                      # debounced save fires
    on_disk = json.loads((tmp_path / RESIDUES_FILE).read_text(encoding="utf-8"))
    assert on_disk["Portal A"]["confirmation"]["code AAAAA9999A"] == {"seen": 3, "claimed": 0}
    assert ResidueCounts(directory=tmp_path).data == on_disk


def test_component_ignores_flash_events_and_counts_pages(tmp_path: Path):
    comp = ResiduesComponent(ResidueCounts(directory=tmp_path, clock=_Clock()))
    obs = SimpleNamespace(source="uia_event", lines=PAGE, result=_result(), portal="Portal A")
    comp.observe(obs, None)
    assert comp.counts.data == {}
    comp.observe(SimpleNamespace(source="tick", lines=PAGE, result=_result(), portal="Portal A"), None)
    assert "Portal A" in comp.counts.data


def test_residues_run_in_sgt_i_only():
    assert any(isinstance(c, ResiduesComponent) for c in default_components())
    import core.sgt.sgt_health as health
    assert not hasattr(health, "compute_residues")
