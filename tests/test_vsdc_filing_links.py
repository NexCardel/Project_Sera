"""
tests/test_vsdc_filing_links.py — Tests for GST Filing Link Resolution & Full Dataset Capture
"""

import pytest
from core.vsdc.vsdc_crosshairs import match_url_crosshair, ALL_CROSSHAIRS
from core.vsdc.vsdc_regex import (
    resolve_gst_form_type_from_url,
    repair_gst_arn,
    extract_gst_filing_date,
    extract_gst_form_table,
    extract_filing_type,
)
from core.vsdc.vsdc_name_parser import is_valid_name, is_better_taxpayer_name
from core.vsdc.vsdc_assembler import VisualSessionAssembler


def test_gst_filing_link_crosshair_matching():
    """Verify that both /file and /filing routes match gst_filing_file_success."""
    urls = [
        ("https://return.gst.gov.in/returns/auth/gstr3b/filing", "gst_filing_file_success"),
        ("https://return.gst.gov.in/returns/auth/gstr1/file", "gst_filing_file_success"),
        ("https://return.gst.gov.in/returns/auth/iff/file", "gst_filing_file_success"),
        ("https://return.gst.gov.in/returns/auth/cmp08/filing", "gst_filing_file_success"),
        ("https://return.gst.gov.in/returns/auth/gstr4/filing", "gst_filing_file_success"),
        ("https://return.gst.gov.in/returns/auth/gstr9/filing", "gst_filing_file_success"),
        ("https://return.gst.gov.in/returns/auth/dashboard", "gst_returns_dashboard"),
        ("https://return.gst.gov.in/returns/dashboard", "gst_returns_dashboard"),
        ("https://services.gst.gov.in/services/auth/fowelcome", "gst_welcome_calendar"),
    ]
    for url, expected_id in urls:
        crosshair = match_url_crosshair(url)
        assert crosshair is not None, f"URL {url} failed to match any crosshair!"
        assert crosshair.id == expected_id, f"URL {url} matched {crosshair.id} instead of {expected_id}"


def test_resolve_gst_form_type_from_url():
    """Verify deterministic form type extraction from URLs, ignoring screen noise."""
    disclaimer_text = "You are required to file GSTR-1 and GSTR-3B for the quarter."

    assert resolve_gst_form_type_from_url("https://return.gst.gov.in/returns/auth/gstr3b/filing", disclaimer_text) == "GSTR-3B"
    assert resolve_gst_form_type_from_url("https://return.gst.gov.in/returns/auth/gstr1/file", disclaimer_text) == "GSTR-1"
    assert resolve_gst_form_type_from_url("https://return.gst.gov.in/returns/auth/iff/file", disclaimer_text) == "GSTR-1/IFF"
    assert resolve_gst_form_type_from_url("https://return.gst.gov.in/returns/auth/cmp08/filing", disclaimer_text) == "CMP-08"
    assert resolve_gst_form_type_from_url("https://return.gst.gov.in/returns/auth/gstr4/filing", disclaimer_text) == "GSTR-4"
    assert resolve_gst_form_type_from_url("https://return.gst.gov.in/returns/auth/gstr9/filing", disclaimer_text) == "GSTR-9"
    assert resolve_gst_form_type_from_url("https://return.gst.gov.in/returns/auth/gstr9c/filing", disclaimer_text) == "GSTR-9C"


def test_repair_gst_arn_labeled():
    """Verify extraction of labeled ARN strings from success receipts."""
    sample_text = """
    GSTR-3B of GSTIN 19BNNPA3652HIZX for the period August 2026-27 has been successfully filed.
    Acknowledgement Reference Number (ARN) is AA190826000001Z.
    Date of filing: 15/09/2026
    Status: Filed
    """
    arn = repair_gst_arn(sample_text)
    assert arn == "AA190826000001Z"

    date_str = extract_gst_filing_date(sample_text)
    assert date_str == "15/09/2026"


def test_name_noise_rejection_ser():
    """Verify 'SER' and portal noise words are rejected and cannot overwrite authoritative names."""
    assert not is_valid_name("SER")
    assert not is_valid_name("TAX")
    assert not is_valid_name("GOODS")
    assert is_valid_name("REGI NEWS")

    # Anti-downgrade test
    assert not is_better_taxpayer_name("SER", "REGI NEWS")

    # Assembler identity test
    assembler = VisualSessionAssembler()
    assembler.update_identity(name="REGI NEWS", gstin="19BNNPA3652HIZX", portal="GST Portal")
    assert assembler.client_name == "REGI NEWS"

    # Attempt to overwrite with noise word "SER"
    assembler.update_identity(name="SER", is_authoritative=True)
    assert assembler.client_name == "REGI NEWS"


def test_full_dataset_capture_simulation():
    """Simulate a complete multi-step GST filing on gstr3b/filing and verify canonical payload."""
    assembler = VisualSessionAssembler()

    # Step 1: Welcome screen
    assembler.update_identity(
        name="REGI NEWS",
        gstin="19BNNPA3652HIZX",
        filing_preference="Quarterly",
        portal="GST Portal",
    )

    # Step 2: Returns Dashboard selection
    assembler.update_selection(
        filing_type="GSTR-3B",
        period_label="Quarter 2 (Jul - Sep)",
        fy="2026-27",
    )

    # Step 3: Submission on https://return.gst.gov.in/returns/auth/gstr3b/filing
    submission_text = """
    GSTIN - 19BNNPA3652HIZX Legal Name - REGI NEWS Return Period - August FY - 2026-27
    Your return GSTR-3B has been filed successfully.
    Acknowledgement Reference Number (ARN) : AA190826000001Z
    Date of filing: 15/09/2026
    Status: Filed
    """
    arn = repair_gst_arn(submission_text)
    filing_type = resolve_gst_form_type_from_url("https://return.gst.gov.in/returns/auth/gstr3b/filing", submission_text)

    assembler.record_submission(
        ack_number=arn,
        status="Filed",
        filing_type=filing_type,
        period_label="August 2026-27",
        raw_text=submission_text,
        crosshair_id="gst_filing_file_success",
    )

    # Flush and verify canonical payload
    payload = assembler.seal_and_flush()
    assert payload is not None
    assert payload["gstin"] == "19BNNPA3652HIZX"
    assert payload["pan"] == "BNNPA3652H"
    assert payload["client_name"] == "REGI NEWS"
    assert payload["arn"] == "AA190826000001Z"
    assert payload["filing_type"] == "GSTR-3B"
    assert payload["status"] == "Filed"
    assert payload["portal"] == "GST Portal"
