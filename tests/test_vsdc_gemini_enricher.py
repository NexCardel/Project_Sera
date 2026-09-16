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


def test_enrich_payload_priority_override_of_noisy_ocr():
    """Verify that Gemini extracted legal name overrides noisy or truncated OCR client_name."""
    raw_ocr = "Legal Name - INDRAJIT CHATTERJEE\nTrade Name - IC ENTERPRISES\nStatus - Filed"
    payload = {
        "pan": "ABCDE1234F",
        "client_name": "INDRAJIT CHATTE...",  # Truncated OCR badge
        "trade_name": "IC ENTERP...",
        "raw_text": raw_ocr,
        "raw_payload": {
            "client_name": "INDRAJIT CHATTE...",
            "raw_text": raw_ocr,
        }
    }

    mock_gemini_output = {
        "legal_name": "INDRAJIT CHATTERJEE",
        "trade_name": "IC ENTERPRISES",
        "fy": "2026-27",
        "tax_period": "Q1",
        "status": "Filed"
    }

    with patch("core.vsdc.vsdc_gemini_parser.parse_compliance_with_gemini", return_value=mock_gemini_output):
        res = enrich_payload_with_gemini(payload)
        # Priority override applied
        assert res["client_name"] == "INDRAJIT CHATTERJEE"
        assert res["trade_name"] == "IC ENTERPRISES"
        assert res["raw_payload"]["client_name"] == "INDRAJIT CHATTERJEE"
        assert res["raw_payload"]["trade_name"] == "IC ENTERPRISES"
        assert res["fy"] == "2026-27"


def test_profile_parser_prioritizes_gemini_extracted():
    """Verify extract_profile_from_payload prioritizes gemini_extracted legal_name and trade_name."""
    from ui.utils.profile_parser import extract_profile_from_payload

    payload = {
        "client_name": "NOISY OCR HEADER",
        "name": "SOME RANDOM OCR TEXT",
        "gemini_extracted": {
            "legal_name": "ANANYA SEN",
            "trade_name": "SEN LOGISTICS",
            "status": "Filed"
        },
        "pan": "BCDEF2345G"
    }

    profile = extract_profile_from_payload(payload)
    assert profile["proprietor_name"] == "ANANYA SEN"
    assert profile["company_name"] == "SEN LOGISTICS"
    assert "gemini_extracted" in profile
    assert profile["gemini_extracted"]["legal_name"] == "ANANYA SEN"


def test_get_tracker_dumps_gemini_priority_resolution(tmp_path):
    """Verify get_tracker_dumps resolves client name using Gemini extraction with top priority."""
    import security
    from database import SeraDatabase
    db_path = str(tmp_path / "test_master.db")
    raw_db_path = str(tmp_path / "rawPayload.db")
    salt_path = str(tmp_path / "test.salt")
    security.generate_and_save_salt(salt_path)
    salt = security.load_salt(salt_path)
    hex_key = security.derive_key_hex("testpass123", salt)
    db = SeraDatabase(db_path, hex_key, raw_db_path=raw_db_path)

    payload = {
        "pan": "BCDEF2345G",
        "client_name": "TRUNCATED NOISY OCR",
        "gemini_extracted": {
            "legal_name": "ANANYA SEN",
            "trade_name": "SEN LOGISTICS"
        }
    }

    db.insert_tracker_dump(
        portal="Income Tax Portal",
        period_label="AY 2026-27",
        arn_number="123456789012345",
        status="Filed",
        raw_payload_json=json.dumps(payload),
        pan="BCDEF2345G",
        filing_type="ITR-1"
    )

    dumps = db.get_tracker_dumps()
    assert len(dumps) >= 1
    found = dumps[0]
    assert "ANANYA SEN" in found["client_name"]
    assert found["has_gemini"] is True


def test_gemini_config_read_and_save(tmp_path, monkeypatch):
    """Verify reading and saving multi-key configurations to settings.ini."""
    from core.vsdc.vsdc_gemini_parser import get_gemini_config, save_gemini_config, SETTINGS_FILE
    fake_ini = tmp_path / "test_settings.ini"
    monkeypatch.setattr("core.vsdc.vsdc_gemini_parser.SETTINGS_FILE", fake_ini)

    # Save new config
    keys = ["AIzaSyKey1111111111111111111111111", "AIzaSyKey2222222222222222222222222"]
    ok = save_gemini_config(
        enabled=True,
        api_keys=keys,
        primary_model="gemini-flash-lite-latest",
        timeout=15.0
    )
    assert ok is True

    # Read config back
    cfg = get_gemini_config()
    assert cfg["enabled"] is True
    assert cfg["api_keys"] == keys
    assert cfg["primary_model"] == "gemini-flash-lite-latest"
    assert cfg["timeout"] == 15.0


def test_gemini_master_toggle_disables_enrichment(monkeypatch):
    """Verify that when gemini_enabled is False, enrich_payload_with_gemini skips network calls."""
    from core.vsdc.vsdc_gemini_parser import enrich_payload_with_gemini

    monkeypatch.setattr(
        "core.vsdc.vsdc_gemini_parser.get_gemini_config",
        lambda: {"enabled": False, "api_keys": ["test_key"], "primary_model": "gemini-flash-lite-latest"}
    )

    payload = {
        "pan": "ABCDE1234F",
        "raw_text": "GSTIN - 19BNNPA3652H1ZX\nLegal Name - FATIMA BIBI\nStatus - Filed"
    }

    with patch("core.vsdc.vsdc_gemini_parser.parse_compliance_with_gemini") as mock_parse:
        res = enrich_payload_with_gemini(payload)
        mock_parse.assert_not_called()
        assert "gemini_extracted" not in res or res["gemini_extracted"] == {}


def test_ai_settings_dialog_initialization(monkeypatch, tmp_path):
    """Verify AISettingsDialog initializes properly with multi-key pool and controls."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    fake_ini = tmp_path / "test_settings.ini"
    monkeypatch.setattr("core.vsdc.vsdc_gemini_parser.SETTINGS_FILE", fake_ini)

    from core.vsdc.vsdc_gemini_parser import save_gemini_config
    save_gemini_config(
        enabled=True,
        api_keys=["AIzaSySampleKey1234567890", "AIzaSySecondKey9876543210"],
        primary_model="gemini-flash-lite-latest"
    )

    from ui.dialogs.ai_settings_dialog import AISettingsDialog
    dlg = AISettingsDialog()
    assert dlg.chk_enable_gemini.isChecked() is True
    assert dlg.tbl_keys.rowCount() == 2
    assert dlg.cmb_model.currentText() == "gemini-flash-lite-latest"
    dlg.close()


def test_test_gemini_api_key_empty():
    """Verify testing empty key immediately returns False."""
    from core.vsdc.vsdc_gemini_parser import test_gemini_api_key
    ok, msg = test_gemini_api_key("")
    assert ok is False
    assert "empty" in msg.lower()


def test_test_gemini_api_key_success():
    """Verify test_gemini_api_key handles 200 response."""
    from core.vsdc.vsdc_gemini_parser import test_gemini_api_key
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        ok, msg = test_gemini_api_key("AIzaSyValidKey12345")
        assert ok is True
        assert "valid & active" in msg


def test_test_gemini_api_key_404_cascade_fallback():
    """Verify test_gemini_api_key cascades to alternative model when primary returns 404."""
    import urllib.error
    from core.vsdc.vsdc_gemini_parser import test_gemini_api_key

    err_404 = urllib.error.HTTPError(
        url="http://test", code=404, msg="Not Found", hdrs={}, fp=None
    )
    mock_200 = MagicMock()
    mock_200.status = 200
    mock_200.__enter__.return_value = mock_200

    # First call (gemini-unknown) fails with 404, second call (gemini-1.5-flash) succeeds with 200
    with patch("urllib.request.urlopen", side_effect=[err_404, mock_200]):
        ok, msg = test_gemini_api_key("AIzaSyValidKey12345", model="gemini-unknown-model")
        assert ok is True
        assert "verified via" in msg


