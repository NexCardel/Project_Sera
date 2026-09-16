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
    "gemini-flash-lite-latest",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-3.6-flash",
    "gemini-3.7-flash",
    "gemini-flash-latest",
    "gemini-pro-latest",
]
PRIMARY_MODEL = "gemini-flash-lite-latest"
FALLBACK_MODEL = "gemini-3.1-flash-lite"


def get_gemini_config() -> Dict[str, Any]:
    """
    Reads the Gemini AI configuration from settings.ini and environment.
    Returns:
    {
        "enabled": bool,
        "api_keys": List[str],
        "primary_model": str,
        "timeout": float,
        "auto_failover": bool
    }
    """
    cfg = {
        "enabled": True,
        "api_keys": [],
        "primary_model": "gemini-flash-lite-latest",
        "timeout": 10.0,
        "auto_failover": True,
    }
    env_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if env_key:
        cfg["api_keys"].append(env_key)

    if SETTINGS_FILE.exists():
        try:
            config = configparser.ConfigParser()
            config.read(SETTINGS_FILE, encoding="utf-8")
            if config.has_section("AppSettings"):
                # Enabled toggle
                if config.has_option("AppSettings", "gemini_enabled"):
                    raw_en = config.get("AppSettings", "gemini_enabled").strip().lower()
                    cfg["enabled"] = raw_en in ("1", "true", "yes", "on")
                # Multiple keys
                if config.has_option("AppSettings", "gemini_api_keys"):
                    raw_keys = config.get("AppSettings", "gemini_api_keys").strip()
                    if raw_keys:
                        for k in re.split(r"[,;\n\r]+", raw_keys):
                            k_clean = k.strip()
                            if k_clean and k_clean not in cfg["api_keys"]:
                                cfg["api_keys"].append(k_clean)
                # Single fallback key
                if not cfg["api_keys"] and config.has_option("AppSettings", "gemini_api_key"):
                    raw_k = config.get("AppSettings", "gemini_api_key").strip()
                    if raw_k and raw_k not in cfg["api_keys"]:
                        cfg["api_keys"].append(raw_k)
                # Primary model
                if config.has_option("AppSettings", "gemini_primary_model"):
                    pm = config.get("AppSettings", "gemini_primary_model").strip()
                    if pm:
                        cfg["primary_model"] = pm
                # Timeout
                if config.has_option("AppSettings", "gemini_timeout"):
                    try:
                        cfg["timeout"] = float(config.get("AppSettings", "gemini_timeout").strip())
                    except ValueError:
                        pass
        except Exception as e:
            print(f"[GeminiParser] Warning reading settings.ini: {e}")

    return cfg


def save_gemini_config(
    enabled: bool,
    api_keys: List[str],
    primary_model: str = "gemini-flash-lite-latest",
    timeout: float = 10.0
) -> bool:
    """
    Saves the Gemini AI configuration into settings.ini.
    """
    try:
        config = configparser.ConfigParser()
        if SETTINGS_FILE.exists():
            config.read(SETTINGS_FILE, encoding="utf-8")
        if not config.has_section("AppSettings"):
            config.add_section("AppSettings")

        config.set("AppSettings", "gemini_enabled", "1" if enabled else "0")
        clean_keys = [k.strip() for k in api_keys if k and k.strip()]
        config.set("AppSettings", "gemini_api_keys", ",".join(clean_keys))
        # Keep single primary key for backward compatibility
        config.set("AppSettings", "gemini_api_key", clean_keys[0] if clean_keys else "")
        config.set("AppSettings", "gemini_primary_model", primary_model)
        config.set("AppSettings", "gemini_timeout", str(timeout))

        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            config.write(f)
        return True
    except Exception as e:
        print(f"[GeminiParser] Error saving settings.ini: {e}")
        return False


def get_gemini_api_keys() -> List[str]:
    """
    Retrieves the list of configured Gemini API keys.
    """
    cfg = get_gemini_config()
    return cfg.get("api_keys", [])


def get_gemini_api_key() -> str:
    """
    Retrieves primary Gemini API Key from settings.ini or GEMINI_API_KEY env var.
    """
    keys = get_gemini_api_keys()
    return keys[0] if keys else ""


def is_gemini_enabled() -> bool:
    """
    Checks if Gemini AI Vision enrichment is toggled on and has an active key.
    """
    cfg = get_gemini_config()
    return bool(cfg.get("enabled", True) and cfg.get("api_keys"))


def fetch_available_gemini_models(api_key: str, timeout: float = 5.0) -> List[str]:
    """
    Queries Google Generative Language API ListModels endpoint
    to dynamically discover available models supporting generateContent.
    """
    clean_key = (api_key or "").strip()
    if not clean_key:
        return MODELS_CASCADE

    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={clean_key}"
    try:
        req = urllib.request.Request(url, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            discovered = []
            for m in data.get("models", []):
                if "generateContent" in m.get("supportedGenerationMethods", []):
                    m_name = m.get("name", "").replace("models/", "")
                    if m_name and m_name not in discovered:
                        discovered.append(m_name)
            if discovered:
                # Prioritize flash models, then other fast models
                flash_first = [m for m in discovered if "flash" in m.lower()]
                other = [m for m in discovered if m not in flash_first]
                return flash_first + other
    except Exception:
        pass
    return MODELS_CASCADE


def test_gemini_api_key(
    api_key: str,
    model: str = "gemini-flash-lite-latest",
    timeout: float = 6.0
) -> tuple[bool, str]:
    """
    Sends a lightweight test call to Google AI Studio to verify key validity.
    Automatically cascades to other standard models if the requested model returns HTTP 404.
    Returns: (is_valid: bool, message: str)
    """
    clean_key = (api_key or "").strip()
    if not clean_key:
        return False, "API Key cannot be empty."

    test_models = [model] + [m for m in MODELS_CASCADE if m != model]
    last_err = ""

    for cand_model in test_models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{cand_model}:generateContent?key={clean_key}"
        payload = {
            "contents": [{"parts": [{"text": "Say OK"}]}],
            "generationConfig": {
                "maxOutputTokens": 5,
                "temperature": 0.0
            }
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    if cand_model == model:
                        return True, f"Key is valid & active on '{model}' (Quota OK)."
                    else:
                        return True, f"Key is valid & active! (Model '{model}' was unavailable; verified via '{cand_model}')."
                return False, f"HTTP Status {resp.status}"
        except urllib.error.HTTPError as e:
            if e.code == 400:
                hint = " (Note: Google AI Studio keys typically begin with 'AIzaSy...')" if not clean_key.startswith("AIzaSy") else ""
                return False, f"Invalid API Key or Bad Request (HTTP 400){hint}."
            elif e.code == 403:
                return False, "Permission Denied / Expired Key (HTTP 403). Ensure Gemini API is enabled for this project."
            elif e.code == 429:
                return False, "Daily Quota or Rate Limit Reached (HTTP 429)."
            elif e.code == 404:
                last_err = f"Model '{cand_model}' Not Found (HTTP 404)."
                continue
            return False, f"HTTP Error {e.code}: {e.reason}"
        except Exception as e:
            return False, f"Connection Failed: {e}"

    return False, f"{last_err} Please check your Google AI Studio project at https://aistudio.google.com/."


def _extract_json_object(raw_text: str) -> Optional[Dict[str, Any]]:
    """
    Bulletproof extraction of a JSON object from raw LLM output,
    handling markdown fences (```json ... ```), list structures, or preamble text.
    """
    clean = raw_text.strip()
    
    # Try direct parse first
    try:
        parsed = json.loads(clean)
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], dict):
            return parsed[0]
    except Exception:
        pass

    # Strip markdown code blocks
    clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s*```$", "", clean)
    try:
        parsed = json.loads(clean)
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], dict):
            return parsed[0]
    except Exception:
        pass

    # Extract outermost balanced or slice { ... }
    s_idx = clean.find("{")
    e_idx = clean.rfind("}")
    if s_idx != -1 and e_idx != -1 and e_idx > s_idx:
        bracketed = clean[s_idx : e_idx + 1]
        try:
            parsed = json.loads(bracketed)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

    # Extract outermost [ ... ]
    s_arr = clean.find("[")
    e_arr = clean.rfind("]")
    if s_arr != -1 and e_arr != -1 and e_arr > s_arr:
        bracketed_arr = clean[s_arr : e_arr + 1]
        try:
            parsed = json.loads(bracketed_arr)
            if isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], dict):
                return parsed[0]
        except Exception:
            pass

    return None


def parse_compliance_with_gemini(
    masked_prompt_text: str,
    api_key: Optional[str] = None,
    timeout: float = 8.0
) -> Optional[Dict[str, Any]]:
    """
    Sends the anonymized, slimmed text to Gemini Flash with multi-key failover and model cascade.
    Returns structured dict with compliance attributes.
    """
    cfg = get_gemini_config()
    if not cfg.get("enabled", True) and not api_key:
        return None

    keys_pool = [api_key] if api_key else cfg.get("api_keys", [])
    if not keys_pool:
        return None

    primary_model = cfg.get("primary_model", "gemini-flash-lite-latest")
    models_to_try = [primary_model] + [m for m in MODELS_CASCADE if m != primary_model]
    req_timeout = cfg.get("timeout", timeout)

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

    for key in keys_pool:
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
                with urllib.request.urlopen(req, timeout=req_timeout) as resp:
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
                        continue
                        
                    content_text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                    parsed = _extract_json_object(content_text)
                    if parsed is not None:
                        # Normalize keys to lowercase for standard consumption
                        norm_parsed = {}
                        for k, v in parsed.items():
                            norm_parsed[k.lower()] = v
                        return norm_parsed

            except urllib.error.HTTPError as e:
                record_gemini_call(0, 0, 0, model=model, success=False)
                if e.code in (429, 403):
                    masked_k = f"...{key[-6:]}" if len(key) >= 6 else "***"
                    print(f"[GeminiParser] HTTP {e.code} on key {masked_k}, failing over to next key...")
                    break
                elif e.code in (400, 404, 503):
                    print(f"[GeminiParser] HTTP {e.code} on {model}: {e}")
                    continue
            except Exception as e:
                record_gemini_call(0, 0, 0, model=model, success=False)
                print(f"[GeminiParser] Network/Parse exception on {model}: {e}")
                continue

    return None


def enrich_payload_with_gemini(
    payload: Dict[str, Any],
    timeout: float = 12.0
) -> Dict[str, Any]:
    """
    Checks the payload's raw text capture, redacts sensitive credentials (PAN/GSTIN/PII)
    via PANBeeper, queries Gemini AI for structured extraction, and attaches a
    'gemini_extracted': {...} portion to the JSON payload before Tracker Dump ingestion.
    """
    if not isinstance(payload, dict):
        return payload

    # If Gemini Vision Enrichment is switched off, return payload directly
    if not is_gemini_enabled():
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

    # If raw_text is still missing, synthesize text from scraped_data (DOM SDC)
    if not raw_text or not isinstance(raw_text, str) or not raw_text.strip():
        scraped = payload.get("scraped_data")
        if not scraped and isinstance(payload.get("raw_payload"), dict):
            scraped = payload["raw_payload"].get("scraped_data") or payload["raw_payload"].get("summary_data")
        if not scraped:
            scraped = payload.get("summary_data")

        if isinstance(scraped, dict):
            synth_lines = []
            for k, v in (scraped.get("summary_labels") or {}).items():
                if v: synth_lines.append(f"{k}: {v}")
            for k, v in (scraped.get("form_fields") or {}).items():
                if v: synth_lines.append(f"{k}: {v}")
            for k, v in scraped.items():
                if k not in ("summary_labels", "form_fields", "tables", "raw_text") and isinstance(v, (str, int, float)) and v:
                    synth_lines.append(f"{k}: {v}")
            if synth_lines:
                raw_text = "\n".join(synth_lines)

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

            # Priority override: Gemini extracted details take precedence over raw optical/OCR extractions
            legal_nm = clean_res.get("legal_name")
            if legal_nm and isinstance(legal_nm, str) and len(legal_nm.strip()) >= 3:
                payload["client_name"] = legal_nm.strip()
                if isinstance(payload.get("raw_payload"), dict):
                    payload["raw_payload"]["client_name"] = legal_nm.strip()

            trade_nm = clean_res.get("trade_name")
            if trade_nm and isinstance(trade_nm, str) and len(trade_nm.strip()) >= 3:
                payload["trade_name"] = trade_nm.strip()
                if isinstance(payload.get("raw_payload"), dict):
                    payload["raw_payload"]["trade_name"] = trade_nm.strip()

            fy_val = clean_res.get("fy")
            if fy_val and isinstance(fy_val, str) and len(fy_val.strip()) >= 4:
                payload["fy"] = fy_val.strip()
                if isinstance(payload.get("raw_payload"), dict):
                    payload["raw_payload"]["fy"] = fy_val.strip()

            tp_val = clean_res.get("tax_period")
            if tp_val and isinstance(tp_val, str) and tp_val.strip():
                payload["tax_period"] = tp_val.strip()
                if isinstance(payload.get("raw_payload"), dict):
                    payload["raw_payload"]["tax_period"] = tp_val.strip()

            pl_val = clean_res.get("period_label")
            if pl_val and isinstance(pl_val, str) and pl_val.strip():
                payload["period_label"] = pl_val.strip()
                if isinstance(payload.get("raw_payload"), dict):
                    payload["raw_payload"]["period_label"] = pl_val.strip()

            st_val = clean_res.get("status")
            if st_val and isinstance(st_val, str) and st_val.strip():
                payload["gemini_status"] = st_val.strip()
                if not payload.get("status") or str(payload.get("status")).strip().lower() in ("n/a", "unknown", "pending", ""):
                    payload["status"] = st_val.strip()
                    if isinstance(payload.get("raw_payload"), dict):
                        payload["raw_payload"]["status"] = st_val.strip()

    except Exception as e:
        print(f"[GeminiEnricher] Notice during payload enrichment: {e}")

    return payload
