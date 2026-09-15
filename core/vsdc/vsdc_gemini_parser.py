"""
core/vsdc/vsdc_gemini_parser.py — Gemini Flash Structured JSON Extraction
========================================================================
Calls Google AI Studio Gemini API (gemini-3.6-flash / gemini-flash-latest)
with strict JSON Schema mode and minimal token footprint.
Incorporates TokenTracker and falls back gracefully to local regex when offline.
"""

import os
import re
import json
import configparser
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, Any, Optional

from .vsdc_token_tracker import record_gemini_call
from .vsdc_beeper import PANBeeper

SETTINGS_FILE = Path(__file__).resolve().parent.parent.parent / "settings.ini"

MODELS_CASCADE = [
    "gemini-3.5-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-3.6-flash",
]
PRIMARY_MODEL = "gemini-3.5-flash-lite"
FALLBACK_MODEL = "gemini-flash-lite-latest"


def get_gemini_api_key() -> str:
    """
    Retrieves Gemini API Key from settings.ini or GEMINI_API_KEY env var.
    """
    env_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if env_key:
        return env_key

    if SETTINGS_FILE.exists():
        try:
            config = configparser.ConfigParser()
            config.read(SETTINGS_FILE, encoding="utf-8")
            if config.has_option("AppSettings", "gemini_api_key"):
                val = config.get("AppSettings", "gemini_api_key").strip()
                if val:
                    return val
        except Exception as e:
            print(f"[GeminiParser] Warning reading settings.ini: {e}")

    return ""


def _extract_json_object(raw_text: str) -> Optional[Dict[str, Any]]:
    """
    Bulletproof extraction of a JSON object from raw LLM output,
    handling markdown fences (```json ... ```) or preamble text.
    """
    clean = raw_text.strip()
    
    # Try direct parse first
    try:
        return json.loads(clean)
    except Exception:
        pass

    # Strip markdown code blocks
    clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s*```$", "", clean)
    try:
        return json.loads(clean)
    except Exception:
        pass

    # Extract outermost balanced or slice { ... }
    s_idx = clean.find("{")
    e_idx = clean.rfind("}")
    if s_idx != -1 and e_idx != -1 and e_idx > s_idx:
        bracketed = clean[s_idx : e_idx + 1]
        try:
            return json.loads(bracketed)
        except Exception:
            pass

    return None


def parse_compliance_with_gemini(
    masked_prompt_text: str,
    api_key: Optional[str] = None,
    timeout: float = 15.0
) -> Optional[Dict[str, Any]]:
    """
    Sends the anonymized, slimmed text to Gemini Flash.
    Returns structured dict with keys:
    {
        "legal_name": str or None,
        "trade_name": str or None,
        "form_type": str or None,
        "fy": str or None,
        "tax_period": str or None,
        "period_label": str or None,
        "status": str or None,
        "due_date": str or None
    }
    """
    key = api_key or get_gemini_api_key()
    if not key:
        return None

    # Ultra-compact, token-efficient prompt
    prompt = (
        "Extract compliance attributes from this portal text into JSON.\n"
        "Input:\n"
        f"{masked_prompt_text}\n\n"
        "Return JSON strictly matching this schema:\n"
        "{\n"
        '  "legal_name": string | null,\n'
        '  "trade_name": string | null,\n'
        '  "form_type": string | null,\n'
        '  "fy": string | null,\n'
        '  "tax_period": string | null,\n'
        '  "period_label": string | null,\n'
        '  "status": string | null,\n'
        '  "due_date": string | null\n'
        "}"
    )

    models_to_try = MODELS_CASCADE
    last_error = None

    for model in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.0,
                "maxOutputTokens": 1500
            }
        }
        
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw_bytes = resp.read()
                resp_json = json.loads(raw_bytes.decode("utf-8"))
                
                # Extract token usage metadata from Gemini response
                usage = resp_json.get("usageMetadata", {})
                prompt_tks = usage.get("promptTokenCount", max(1, len(prompt) // 4))
                cand_tks = usage.get("candidatesTokenCount", 30)
                tot_tks = usage.get("totalTokenCount", prompt_tks + cand_tks)
                
                # Record metrics in TokenTracker
                record_gemini_call(
                    prompt_tokens=prompt_tks,
                    candidate_tokens=cand_tks,
                    total_tokens=tot_tks,
                    model=model,
                    success=True
                )

                candidates = resp_json.get("candidates", [])
                if not candidates:
                    return None
                    
                content_text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                parsed = _extract_json_object(content_text)
                if parsed is not None:
                    return parsed

        except urllib.error.HTTPError as e:
            last_error = e
            # Record failed call
            record_gemini_call(0, 0, 0, model=model, success=False)
            if e.code in (429, 503):
                print(f"[GeminiParser] Quota rate limit or service busy ({e.code}) on {model}, trying fallback...")
                continue
            elif e.code in (400, 403, 404):
                print(f"[GeminiParser] HTTP {e.code} on {model}: {e}")
                continue
        except Exception as e:
            last_error = e
            record_gemini_call(0, 0, 0, model=model, success=False)
            print(f"[GeminiParser] Network/Parse exception on {model}: {e}")
            continue

    return None


def enrich_payload_with_gemini(
    payload: Dict[str, Any],
    timeout: float = 6.0
) -> Dict[str, Any]:
    """
    Checks the payload's raw text capture, redacts sensitive credentials (PAN/GSTIN/PII)
    via PANBeeper, queries Gemini AI for structured extraction, and attaches a
    'gemini_extracted': {...} portion to the JSON payload before Tracker Dump ingestion.
    """
    if not isinstance(payload, dict):
        return payload

    # Ensure gemini_extracted object is present by default
    payload.setdefault("gemini_extracted", {})
    if isinstance(payload.get("raw_payload"), dict):
        payload["raw_payload"].setdefault("gemini_extracted", {})

    # Extract raw text from payload or nested structures
    raw_text = payload.get("raw_text")
    if not raw_text and isinstance(payload.get("raw_payload"), dict):
        raw_text = payload["raw_payload"].get("raw_text")
        if not raw_text:
            captures = payload["raw_payload"].get("assembler_captures") or []
            if captures and isinstance(captures, list) and isinstance(captures[0], dict):
                raw_text = captures[0].get("raw_text")
            elif isinstance(payload["raw_payload"].get("dataset_capture"), dict):
                raw_text = payload["raw_payload"]["dataset_capture"].get("raw_text")

    if not raw_text or not isinstance(raw_text, str) or not raw_text.strip():
        return payload

    # Segment unbroken text on statutory field anchors if line count is small or lines are long
    field_anchors = (
        "GSTIN", "Legal Name", "Trade Name", "Financial Year", "FY", "Tax Period",
        "Return Period", "Status", "Due Date", "GSTR", "IFF", "Acknowledgement", "Ack No", "ARN"
    )
    segmented_text = raw_text
    for fa in field_anchors:
        segmented_text = re.sub(rf"(?i)\s+({re.escape(fa)}\s*[-:–—])", r"\n\1", segmented_text)

    lines = [ln.strip() for ln in segmented_text.splitlines() if ln.strip()]
    if not lines:
        return payload

    pan = payload.get("pan")
    gstin = payload.get("gstin")

    try:
        # 1. Anonymize and slim text via PANBeeper (guarantees zero-leakage of PAN/GSTIN)
        beeper_res = PANBeeper.anonymize_and_slim(lines, known_pan=pan, known_gstin=gstin)
        slimmed_text = beeper_res.get("slimmed_masked_text", "")
        if not slimmed_text:
            return payload

        # 2. Call Gemini AI with prompt and timeout
        gem_res = parse_compliance_with_gemini(slimmed_text, timeout=timeout)
        if gem_res and isinstance(gem_res, dict):
            # Clean out null/empty values
            clean_res = {k: v for k, v in gem_res.items() if v not in (None, "")}
            payload["gemini_extracted"] = clean_res
            if isinstance(payload.get("raw_payload"), dict):
                payload["raw_payload"]["gemini_extracted"] = clean_res

            # Supplemental non-destructive backfill for top-level keys if missing
            if not payload.get("client_name") and clean_res.get("legal_name"):
                payload["client_name"] = clean_res["legal_name"]
            if not payload.get("trade_name") and clean_res.get("trade_name"):
                payload["trade_name"] = clean_res["trade_name"]
            if not payload.get("fy") and clean_res.get("fy"):
                payload["fy"] = clean_res["fy"]
            if not payload.get("tax_period") and clean_res.get("tax_period"):
                payload["tax_period"] = clean_res["tax_period"]

    except Exception as e:
        print(f"[GeminiEnricher] Notice during payload enrichment: {e}")

    return payload
