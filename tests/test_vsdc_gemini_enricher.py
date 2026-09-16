"""
tests/test_vsdc_gemini_enricher.py — Tests for Gemini AI Raw Text Extraction & Payload Enrichment
"""

import json
from unittest.mock import patch, MagicMock
import pytest

from core.vsdc.vsdc_gemini_parser import enrich_payload_with_gemini
from core.vsdc.vsdc_beeper import PANBeeper, SensitiveDataLeakageError


def test_enrich_payload_empty_raw_text():
    """Verify that when raw_text is missing or empty, gemini_extracted defaults to empty dict."""
    payload = {
        "pan": "ABCDE1234F",
        "client_name": "TEST CLIENT",
        "raw_payload": {}
    }
    res = enrich_payload_with_gemini(payload)
    assert "gemini_extracted" in res
    assert res["gemini_extracted"] == {}
    assert res["raw_payload"]["gemini_extracted"] == {}


def test_enrich_payload_redacts_credentials_and_extracts():
    """Verify PAN and GSTIN are redacted before sending to Gemini, and extracted dict is attached."""
    raw_ocr = """
    GSTIN - 19BNNPA3652H1ZX
    Legal Name of Taxpayer - FATIMA BIBI
    Trade Name - FATIMA ENTERPRISE
    Financial Year - 2026-27
    Tax Period - August
    Status - Filed
    """
    payload = {
        "pan": "BNNPA3652H",
        "gstin": "19BNNPA3652H1ZX",
        "raw_text": raw_ocr,
        "raw_payload": {
            "raw_text": raw_ocr,
        }
    }

    mock_gemini_output = {
        "legal_name": "FATIMA BIBI",
        "trade_name": "FATIMA ENTERPRISE",
        "form_type": "GSTR-1",
        "fy": "2026-27",
        "tax_period": "August",
        "status": "Filed",
        "due_date": "11/09/2026"
    }

    with patch("core.vsdc.vsdc_gemini_parser.parse_compliance_with_gemini", return_value=mock_gemini_output) as mock_parse:
        res = enrich_payload_with_gemini(payload)

        # Ensure parse_compliance_with_gemini was called
        mock_parse.assert_called_once()
        called_prompt = mock_parse.call_args[0][0]

        # Verify sensitive credentials were fully redacted from prompt
        assert "19BNNPA3652H1ZX" not in called_prompt
        assert "BNNPA3652H" not in called_prompt
        assert "[GSTIN_TOKEN]" in called_prompt

        # Verify gemini_extracted attached to payload
        assert "gemini_extracted" in res
        assert res["gemini_extracted"]["legal_name"] == "FATIMA BIBI"
        assert res["gemini_extracted"]["trade_name"] == "FATIMA ENTERPRISE"
        assert res["gemini_extracted"]["status"] == "Filed"
        assert res["raw_payload"]["gemini_extracted"] == res["gemini_extracted"]


def test_enrich_payload_offline_or_error_fallback():
    """Verify that network error or exception gracefully leaves gemini_extracted as empty dict."""
    raw_ocr = "Financial Year - 2026-27\nTax Period - September"
    payload = {
        "pan": "ABCDE1234F",
        "raw_text": raw_ocr,
    }

    with patch("core.vsdc.vsdc_gemini_parser.parse_compliance_with_gemini", side_effect=Exception("Network unreachable")):
        res = enrich_payload_with_gemini(payload)
        assert res["gemini_extracted"] == {}


def test_tracker_dump_preserves_gemini_extracted(tmp_path):
    """Verify that database insert_tracker_dump preserves gemini_extracted inside raw_payload_json."""
    import security
    from database import SeraDatabase
    db_path = str(tmp_path / "test_master.db")
    raw_db_path = str(tmp_path / "rawPayload.db")
    salt_path = str(tmp_path / "test.salt")
    security.generate_and_save_salt(salt_path)
    salt = security.load_salt(salt_path)
    hex_key = security.derive_key_hex("testpass123", salt)
    db = SeraDatabase(db_path, hex_key, raw_db_path=raw_db_path)

    raw_ocr = "GSTIN - 19BNNPA3652H1ZX\nStatus - Filed"
    payload = {
        "pan": "BNNPA3652H",
        "gstin": "19BNNPA3652H1ZX",
        "filing_type": "GSTR-1",
        "period_label": "August (FY 2026-27)",
        "status": "Filed",
        "arn": "AA1908260001234",
        "raw_text": raw_ocr,
        "gemini_extracted": {
            "legal_name": "FATIMA BIBI",
            "form_type": "GSTR-1",
            "status": "Filed"
        }
    }

    res = db.insert_tracker_dump(
        portal="GST Portal (GSTR-1)",
        period_label="August (FY 2026-27)",
        arn_number="AA1908260001234",
        status="Filed",
        raw_payload_json=json.dumps(payload),
        pan="BNNPA3652H",
        filing_type="GSTR-1"
    )

    assert res is not None and isinstance(res, dict)
    dump_id = res["id"]
    with db._connect_raw() as r_conn:
        row = r_conn.execute("SELECT raw_payload_json FROM tracker_dump WHERE id = ?", (dump_id,)).fetchone()
        assert row is not None
        saved_payload = json.loads(row[0])
        assert "gemini_extracted" in saved_payload
        assert saved_payload["gemini_extracted"]["legal_name"] == "FATIMA BIBI"
        assert saved_payload["gemini_extracted"]["status"] == "Filed"


def test_enrich_payload_from_scraped_data():
    """Verify that when raw_text is missing, scraped_data is synthesized into prompt."""
    payload = {
        "pan": "BNNPA3652H",
        "gstin": "19BNNPA3652H1ZX",
        "scraped_data": {
            "summary_labels": {
                "Legal Name": "FATIMA BIBI",
                "Status": "Filed"
            },
            "form_fields": {
                "Financial Year": "2026-27"
            }
        }
    }

    mock_gemini_output = {
        "legal_name": "FATIMA BIBI",
        "fy": "2026-27",
        "status": "Filed"
    }

    with patch("core.vsdc.vsdc_gemini_parser.parse_compliance_with_gemini", return_value=mock_gemini_output) as mock_parse:
        res = enrich_payload_with_gemini(payload)
        mock_parse.assert_called_once()
        assert res["gemini_extracted"]["legal_name"] == "FATIMA BIBI"
        assert res["gemini_extracted"]["fy"] == "2026-27"


def test_extract_json_object_handles_lists_and_fences():
    """Verify _extract_json_object properly unpacks array and markdown wraps."""
    from core.vsdc.vsdc_gemini_parser import _extract_json_object

    # Test JSON array
    res = _extract_json_object('[{"legal_name": "TEST ENTERPRISE", "fy": "2024-25"}]')
    assert res == {"legal_name": "TEST ENTERPRISE", "fy": "2024-25"}

    # Test Markdown fenced block
    res = _extract_json_object('```json\n{"legal_name": "TEST 2", "status": "Filed"}\n```')
    assert res == {"legal_name": "TEST 2", "status": "Filed"}

