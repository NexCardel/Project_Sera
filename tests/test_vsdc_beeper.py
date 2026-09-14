"""
tests/test_vsdc_beeper.py — Verification of Zero-Leakage PAN Beeper & Token Tracker
=================================================================================
Proves that no PAN, GSTIN, mobile number, or email can pass through the PAN Beeper
to an external LLM, and verifies token minimization and quota tracking.
"""

import pytest
from core.vsdc.vsdc_beeper import PANBeeper, SensitiveDataLeakageError
from core.vsdc.vsdc_token_tracker import record_gemini_call, get_token_usage_summary
from core.vsdc.vsdc_gemini_parser import parse_compliance_with_gemini, get_gemini_api_key


def test_masking_real_gstin_and_pan():
    raw_lines = [
        "Dashboard > Returns Dashboard",
        "GSTIN: 19AKVPA6032B1ZC",
        "Legal Name of Taxpayer: A. P. Enterprise",
        "Trade Name: A. P. Enterprise",
        "Financial Year: 2026-27",
        "Quarter: Jul-Sep",
        "Tax Period: September",
        "Details of the taxpayer: PAN AKVPA6032B",
        "Contact: 9830112233 | email: accounts@apenterprise.com",
        "GSTR-1/IFF | Details of outward supplies | Status: Filed | Due Date: 11/10/2026",
        "Help desk: 1800-103-4786",
        "Copyright 2026 Goods and Services Tax Network. All rights reserved."
    ]

    result = PANBeeper.anonymize_and_slim(raw_lines, known_pan="AKVPA6032B", known_gstin="19AKVPA6032B1ZC")
    slimmed = result["slimmed_masked_text"]

    # 1. Real GSTIN and PAN must NOT be anywhere in the output
    assert "19AKVPA6032B1ZC" not in slimmed
    assert "AKVPA6032B" not in slimmed
    assert "9830112233" not in slimmed
    assert "accounts@apenterprise.com" not in slimmed

    # 2. Replaced tokens must be present
    assert "[GSTIN_TOKEN]" in slimmed
    assert "[PAN_TOKEN]" in slimmed
    assert "[PHONE_REDACTED]" in slimmed
    assert "[EMAIL_REDACTED]" in slimmed

    # 3. Preserved compliance attributes
    assert "A. P. Enterprise" in slimmed
    assert "GSTR-1/IFF" in slimmed
    assert "Filed" in slimmed

    # 4. Token efficiency: boilerplate discarded
    assert "copyright" not in slimmed.lower()
    assert "help desk" not in slimmed.lower()
    assert result["estimated_tokens"] < 120


def test_preflight_leak_guard_blocks_unmasked_gstin():
    leaky_text = "Taxpayer report with GSTIN 19AKVPA6032B1ZC and status Filed"
    with pytest.raises(SensitiveDataLeakageError) as exc_info:
        PANBeeper.assert_zero_sensitive_data(leaky_text)
    assert "Unmasked GSTIN detected" in str(exc_info.value)


def test_preflight_leak_guard_blocks_unmasked_pan():
    leaky_text = "Taxpayer details PAN ABCDE1234F verified"
    with pytest.raises(SensitiveDataLeakageError) as exc_info:
        PANBeeper.assert_zero_sensitive_data(leaky_text)
    assert "Unmasked PAN detected" in str(exc_info.value)


def test_preflight_leak_guard_blocks_phone_and_email():
    with pytest.raises(SensitiveDataLeakageError):
        PANBeeper.assert_zero_sensitive_data("Call client at 9876543210 for verification")

    with pytest.raises(SensitiveDataLeakageError):
        PANBeeper.assert_zero_sensitive_data("Send notice to client@firm.com immediately")


def test_preflight_leak_guard_passes_clean_payload():
    safe_text = (
        "GSTIN: [GSTIN_TOKEN]\n"
        "Legal Name: GCK DS SERVICE\n"
        "Form: GSTR-1/IFF\n"
        "Period: September(Q) (FY 2026-27)\n"
        "Status: Filed\n"
        "Due Date: 13/10/2026"
    )
    # Must return True without raising exception
    assert PANBeeper.assert_zero_sensitive_data(safe_text) is True


def test_token_tracker_increments_and_computes_remaining():
    summary_before = get_token_usage_summary()
    calls_before = summary_before["calls_today"]

    record_gemini_call(
        prompt_tokens=85,
        candidate_tokens=40,
        total_tokens=125,
        model="gemini-3.6-flash",
        success=True
    )

    summary_after = get_token_usage_summary()
    assert summary_after["calls_today"] == calls_before + 1
    assert summary_after["calls_remaining"] == 1500 - (calls_before + 1)
    assert "Gemini" in summary_after["badge_text"]
    assert "100% Free Tier" in summary_after["tooltip"]


def test_live_gemini_flash_parsing():
    api_key = get_gemini_api_key()
    assert api_key, "API key should be loaded from settings.ini"

    test_lines = [
        "GSTIN: [GSTIN_TOKEN]",
        "Legal Name of Taxpayer: GCK DS SERVICE",
        "Trade Name: GCK DS SERVICE",
        "Financial Year: 2026-27",
        "Tax Period: September",
        "Quarter: Jul-Sep",
        "GSTR-1/IFF",
        "Details of outward supplies of goods or services",
        "Status: Filed",
        "Due Date: 13/10/2026"
    ]

    masked = PANBeeper.anonymize_and_slim(test_lines)
    result = parse_compliance_with_gemini(masked["slimmed_masked_text"], api_key=api_key)

    assert result is not None, "Gemini should return parsed JSON"
    assert "GCK DS SERVICE" in (result.get("legal_name") or "")
    assert "GSTR" in (result.get("form_type") or "")
    assert "2026-27" in (result.get("fy") or "")
    assert (result.get("status") or "").lower() == "filed"
