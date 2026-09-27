"""
Tests for SGT step 10: tracking unclaimed containers/typed values (residues).
"""
from datetime import date
from pathlib import Path

import pytest

from core.sgt.sgt_health import (
    SpecStats, compute_residues, _mask_shape, _find_shaped_values,
    _extract_claimed_shapes, _detect_page_kind,
)
from core.sgt.sgt_resolver import PageResult, Hit, Dataset


class TestResidueHelpers:
    """Test the helper functions for residue detection."""

    def test_mask_shape(self):
        """Test that shape masking works correctly."""
        assert _mask_shape("ABCPD1234E") == "AAAAA9999A"
        assert _mask_shape("123456789150726") == "999999999999999"
        assert _mask_shape("2026-09-28") == "9999-99-99"
        assert _mask_shape("Aadhaar: 1234 5678 9012") == "AAAAAAA: 9999 9999 9999"

    def test_find_shaped_values_pan(self):
        """Test finding PAN-shaped values."""
        lines = ["PAN: ABCPD1234E", "Another line"]
        shaped = _find_shaped_values(lines)
        assert "PAN:AAAAA9999A" in shaped
        assert shaped["PAN:AAAAA9999A"] == 1

    def test_find_shaped_values_arn(self):
        """Test finding ARN-shaped values."""
        lines = ["Acknowledgement No: 123456789150726"]
        shaped = _find_shaped_values(lines)
        assert "ARN:999999999999999" in shaped
        assert shaped["ARN:999999999999999"] == 1

    def test_find_shaped_values_multiple(self):
        """Test finding multiple shaped values."""
        lines = ["PAN: ABCPD1234E", "ARN: 123456789150726", "Another PAN: XYZAB5678F"]
        shaped = _find_shaped_values(lines)
        assert shaped.get("PAN:AAAAA9999A", 0) >= 1
        assert shaped.get("ARN:999999999999999", 0) == 1

    def test_detect_page_kind_gst_confirmation(self):
        """Test page kind detection for GST confirmation."""
        result = _detect_page_kind(
            url="https://gst.gov.in/ack",
            title="GST Acknowledgement",
            lines=["Acknowledgement Number", "123456789150726"]
        )
        assert result == "gst_confirmation"

    def test_detect_page_kind_itr_return(self):
        """Test page kind detection for ITR return."""
        result = _detect_page_kind(
            url="https://itr.gov.in/form",
            title="Income Tax Return Form",
            lines=["Form", "ITR-1"]
        )
        assert result == "itr_return"

    def test_detect_page_kind_list_page(self):
        """Test page kind detection for list pages."""
        result = _detect_page_kind(
            url="https://example.com/list",
            title="Records List",
            lines=["Date", "Amount", "Status"]
        )
        assert result == "list_page"

    def test_extract_claimed_shapes_from_result(self):
        """Test extracting claimed shapes from PageResult."""
        result = PageResult(
            profile={
                "pan": Hit(field="pan", value="ABCPD1234E", confidence=95, spec="pan_spec", line=1)
            }
        )
        claimed = _extract_claimed_shapes(result)
        assert "PAN:AAAAA9999A" in claimed
        assert claimed["PAN:AAAAA9999A"] == 1

    def test_extract_claimed_shapes_from_dict(self):
        """Test extracting claimed shapes from dict (as_dict output)."""
        result_dict = {
            "profile": {
                "pan": {"value": "ABCPD1234E", "confidence": 95, "spec": "pan_spec"}
            },
            "current": {},
            "datasets": []
        }
        claimed = _extract_claimed_shapes(result_dict)
        assert "PAN:AAAAA9999A" in claimed

    def test_extract_claimed_shapes_from_datasets(self):
        """Test extracting shapes from claimed datasets."""
        result_dict = {
            "profile": {},
            "current": {},
            "datasets": [
                {"values": {"arn": "123456789150726"}, "record": "itr_ack"}
            ]
        }
        claimed = _extract_claimed_shapes(result_dict)
        assert "ARN:999999999999999" in claimed


class TestComputeResidues:
    """Test the residue computation function."""

    def test_no_residues_when_all_claimed(self):
        """Test that no residues are returned when all found values are claimed."""
        lines = ["PAN: ABCPD1234E"]
        result = PageResult(
            profile={"pan": Hit(field="pan", value="ABCPD1234E", confidence=95, spec="pan_spec", line=1)}
        )
        residue = compute_residues(lines, result, "https://example.com", "Test")
        assert residue is None

    def test_residues_for_unclaimed_values(self):
        """Test that unclaimed values are returned as residues."""
        lines = ["PAN: ABCPD1234E", "Another PAN: XYZAB5678F"]
        result = PageResult(
            profile={"pan": Hit(field="pan", value="ABCPD1234E", confidence=95, spec="pan_spec", line=1)}
        )
        residue = compute_residues(lines, result, "https://itr.gov.in/form", "ITR Form")
        assert residue is not None
        page_kind, residues = residue
        assert page_kind == "itr_return"
        # One PAN was found but not claimed
        assert "PAN:AAAAA9999A" in residues

    def test_residues_with_multiple_shapes(self):
        """Test residues with multiple different shapes."""
        lines = [
            "PAN: ABCPD1234E",
            "PAN: XYZAB5678F",
            "ARN: 123456789150726"
        ]
        result = PageResult(
            profile={"pan": Hit(field="pan", value="ABCPD1234E", confidence=95, spec="pan_spec", line=1)}
        )
        residue = compute_residues(lines, result, "https://itr.gov.in/ack", "ITR Ack")
        assert residue is not None
        page_kind, residues = residue
        assert page_kind == "itr_ack"
        # Another PAN and ARN not claimed
        assert len(residues) >= 1

    def test_no_residues_when_nothing_found(self):
        """Test that None is returned when no shaped values found."""
        lines = ["Some text without any identifiers"]
        result = PageResult()
        residue = compute_residues(lines, result, "https://example.com", "Test")
        assert residue is None


class TestSpecStatsResidues:
    """Test SpecStats tracking of residues."""

    def test_record_residues(self, tmp_path: Path):
        """Test recording residues."""
        stats = SpecStats(tmp_path)
        stats.record_residues("GST Portal", "gst_confirmation", {"ARN:999999999999999": 2, "PAN:AAAAA9999A": 1})

        # Check that residues are stored
        assert "GST Portal" in stats.data["residues"]
        assert "gst_confirmation" in stats.data["residues"]["GST Portal"]
        assert stats.data["residues"]["GST Portal"]["gst_confirmation"]["ARN:999999999999999"] == 2

    def test_record_residues_accumulates(self, tmp_path: Path):
        """Test that residues accumulate across calls."""
        stats = SpecStats(tmp_path)
        stats.record_residues("GST Portal", "gst_confirmation", {"ARN:999999999999999": 1})
        stats.record_residues("GST Portal", "gst_confirmation", {"ARN:999999999999999": 1})

        assert stats.data["residues"]["GST Portal"]["gst_confirmation"]["ARN:999999999999999"] == 2

    def test_record_residues_empty(self, tmp_path: Path):
        """Test that recording empty residues does nothing."""
        stats = SpecStats(tmp_path)
        stats.record_residues("GST Portal", "gst_confirmation", {})

        assert "GST Portal" not in stats.data["residues"]

    def test_summary_includes_residues(self, tmp_path: Path):
        """Test that summary includes residues."""
        stats = SpecStats(tmp_path)
        today = date(2026, 9, 28)
        stats.record_read("GST Portal", "uia", today)
        stats.record_residues("GST Portal", "gst_confirmation", {"ARN:999999999999999": 2})

        summary = stats.summary(today, days=7)
        assert "GST Portal" in summary
        assert "residues" in summary["GST Portal"]
        assert summary["GST Portal"]["residues"]["gst_confirmation"]["ARN:999999999999999"] == 2

    def test_residues_persist_and_load(self, tmp_path: Path):
        """Test that residues are saved and loaded correctly."""
        stats1 = SpecStats(tmp_path)
        stats1.record_residues("Portal1", "page_kind", {"SHAPE1": 3})
        stats1.save()

        stats2 = SpecStats(tmp_path)
        assert stats2.data["residues"]["Portal1"]["page_kind"]["SHAPE1"] == 3
