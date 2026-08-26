"""
profile_parser.py — Sera Raw Payload Filter (SRPF) Profile Parser
------------------------------------------------------------------
Extracts standardized business and individual profile attributes from
unified government portal network capture containers (Income Tax, GST,
TRACES, MCA) and maps them against user-defined Master Column List (MCL) definitions.
"""

import json
import re
from typing import Any, Dict, List, Optional


def extract_profile_from_payload(raw_payload: Any) -> Dict[str, str]:
    """
    Scans a raw payload dict or JSON string and extracts standardized profile fields:
    - company_name: Trade name, legal firm name, or business title
    - proprietor_name: First/Middle/Last name, taxpayer full name
    - pan: 10-character Permanent Account Number
    - gstin: 15-character Goods & Services Tax Identification Number
    - tan: 10-character Tax Deduction & Collection Account Number
    - phone: 10-digit mobile number
    - email: Email address
    - dob: Date of birth or incorporation date (YYYY-MM-DD or DD/MM/YYYY)
    - user_id: Login username / User ID
    - address: Business address or registered office string
    """
    if isinstance(raw_payload, str):
        try:
            payload = json.loads(raw_payload)
        except Exception:
            payload = {}
    elif isinstance(raw_payload, dict):
        payload = raw_payload
    else:
        payload = {}

    extracted = {
        "company_name": "",
        "proprietor_name": "",
        "pan": "",
        "gstin": "",
        "tan": "",
        "phone": "",
        "email": "",
        "dob": "",
        "user_id": "",
        "address": ""
    }

    def _clean_str(v: Any) -> str:
        if v is None:
            return ""
        s = str(v).strip()
        return "" if s.lower() in ("null", "none", "undefined", "n/a", "[]", "{}") else s

    # Recursive collector of key-value pairs
    flat_kv: List[tuple[str, str]] = []

    def _collect(item, depth=6):
        if depth <= 0 or not item:
            return
        if isinstance(item, dict):
            for k, v in item.items():
                if isinstance(v, (str, int, float)) and v is not None:
                    s_val = _clean_str(v)
                    if s_val:
                        flat_kv.append((str(k).lower(), s_val))
                elif isinstance(v, (dict, list)):
                    _collect(v, depth - 1)
        elif isinstance(item, list):
            for sub in item:
                _collect(sub, depth - 1)

    _collect(payload)

    # 1. PAN Extraction
    for k, v in flat_kv:
        v_upper = v.upper()
        if re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", v_upper):
            if any(target in k for target in ("pan", "entitynum", "entityid", "userpan", "taxpayerid", "clientpan", "submituserid", "userid")):
                extracted["pan"] = v_upper
                break
    if not extracted["pan"]:
        for _, v in flat_kv:
            v_upper = v.upper()
            if re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", v_upper):
                extracted["pan"] = v_upper
                break

    # 2. GSTIN Extraction
    for k, v in flat_kv:
        v_upper = v.upper()
        if re.match(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$", v_upper):
            extracted["gstin"] = v_upper
            if not extracted["pan"]:
                extracted["pan"] = v_upper[2:12]
            break

    # 3. TAN Extraction
    for k, v in flat_kv:
        v_upper = v.upper()
        if re.match(r"^[A-Z]{4}[0-9]{5}[A-Z]$", v_upper) and any(t in k for t in ("tan", "deductor")):
            extracted["tan"] = v_upper
            break

    # 4. Email Extraction
    for k, v in flat_kv:
        if "@" in v and re.match(r"^[\w\.-]+@[\w\.-]+\.\w+$", v.strip()):
            extracted["email"] = v.strip().lower()
            break

    # 5. Phone / Mobile Extraction
    for k, v in flat_kv:
        digits = re.sub(r"\D", "", v)
        if len(digits) == 10 and digits[0] in "6789" and any(t in k for t in ("mobile", "mob", "phone", "ph", "contact", "sms")):
            extracted["phone"] = digits
            break
    if not extracted["phone"]:
        for k, v in flat_kv:
            digits = re.sub(r"\D", "", v)
            if len(digits) == 10 and digits[0] in "6789":
                extracted["phone"] = digits
                break

    # 6. Company / Firm Name Extraction
    company_keys = ("tradename", "trade_name", "legalname", "legal_name", "companyname", "firmname", "businessname", "entityname", "taxpayername", "name")
    # Bank-account responses contain fields such as bankName and accountHolder
    # alongside taxpayer identity. They are institution/account metadata, not
    # company or proprietor names.
    non_identity_name_keys = ("bank", "account", "branch", "ifsc", "institution", "holdertype")
    for target in company_keys:
        for k, v in flat_kv:
            if any(part in k for part in non_identity_name_keys):
                continue
            matches = k == target or (target != "name" and target in k and "first" not in k and "last" not in k and "user" not in k)
            if matches:
                if len(v) >= 3 and not re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", v.upper()) and "@" not in v:
                    extracted["company_name"] = v
                    break
        if extracted["company_name"]:
            break

    # 7. Proprietor / Individual Name (Income Tax firstName + lastName or authSignatory)
    first_name = ""
    last_name = ""
    for k, v in flat_kv:
        if k in ("firstname", "first_name", "fname"):
            first_name = v
        elif k in ("lastname", "last_name", "lname", "sur_name", "surname"):
            last_name = v
        elif k in ("fullname", "full_name", "assesseename", "assessee_name", "nameasperbank"):
            if not extracted["proprietor_name"] and len(v) >= 3:
                extracted["proprietor_name"] = v
        elif k in ("authsignatory", "auth_signatory", "proprietorname", "proprietor_name", "taxpayer_name"):
            if not extracted["proprietor_name"] and len(v) >= 3:
                extracted["proprietor_name"] = v

    if first_name or last_name:
        full = f"{first_name} {last_name}".strip()
        if full and not extracted["proprietor_name"]:
            extracted["proprietor_name"] = full

    # Do not mirror one field into the other. A bank name or a single legal
    # name is not sufficient evidence for both company and proprietor fields.

    # 8. Date of Birth / Incorporation Date
    for k, v in flat_kv:
        if any(t in k for t in ("dob", "birth", "incorporation", "incorp_dt", "incorpdt", "creationdt")):
            # YYYY-MM-DD or DD/MM/YYYY or DD-MM-YYYY
            if re.match(r"^\d{4}-\d{2}-\d{2}$", v) or re.match(r"^\d{2}[/-]\d{2}[/-]\d{4}$", v):
                extracted["dob"] = v
                break

    # 9. User ID / Login ID
    for k, v in flat_kv:
        if k in ("submituserid", "userid", "user_id", "loginid", "login_id", "username") and v:
            extracted["user_id"] = v
            break

    return extracted


def map_profile_to_mcl_columns(extracted_profile: Dict[str, str], mcl_columns: List[Dict[str, Any]]) -> Dict[int, str]:
    """
    Maps extracted profile fields to appropriate MCL column IDs based on semantic label matching.
    Returns {column_id: value_string}.
    """
    mapped: Dict[int, str] = {}
    if not extracted_profile or not mcl_columns:
        return mapped

    for col in mcl_columns:
        col_id = col["id"]
        lbl = col.get("label", "").strip().lower()
        f_type = col.get("field_type", "text")

        # Skip Auto-serial / ID field type (handled by DB)
        if f_type == "id" or lbl in ("no", "no.", "sl no", "sl. no.", "id", "#"):
            continue

        # 1. PAN Column
        if col.get("is_internal_pk") or re.search(r"\bpan\b", lbl) or lbl == "pan":
            if "pass" not in lbl and extracted_profile.get("pan"):
                mapped[col_id] = extracted_profile["pan"]
                continue

        # 2. GSTIN Column
        if "gstin" in lbl or "gst no" in lbl or "gst_no" in lbl or re.search(r"\bgst\b", lbl):
            if "pass" not in lbl and extracted_profile.get("gstin"):
                mapped[col_id] = extracted_profile["gstin"]
                continue

        # 3. TAN Column
        if (re.search(r"\btan\b", lbl) or lbl == "tan") and "pass" not in lbl:
            if extracted_profile.get("tan"):
                mapped[col_id] = extracted_profile["tan"]
                continue

        # 4. Company / Business Name Column
        if ("company" in lbl or "firm" in lbl or "trade" in lbl or "business" in lbl or "legal" in lbl) and "pass" not in lbl:
            if extracted_profile.get("company_name"):
                mapped[col_id] = extracted_profile["company_name"]
                continue

        # 5. Proprietor / Taxpayer Name Column
        if ("proprietor" in lbl or "prop" in lbl or "director" in lbl or "client name" in lbl) and "pass" not in lbl:
            if extracted_profile.get("proprietor_name"):
                mapped[col_id] = extracted_profile["proprietor_name"]
                continue

        # 6. Phone / Mobile Column
        if ("ph" in lbl or "phone" in lbl or "mobile" in lbl or "contact" in lbl) and "pass" not in lbl:
            if extracted_profile.get("phone"):
                mapped[col_id] = extracted_profile["phone"]
                continue

        # 7. Email Column
        if ("email" in lbl or "mail" in lbl) and "pass" not in lbl:
            if extracted_profile.get("email"):
                mapped[col_id] = extracted_profile["email"]
                continue

        # 8. Date of Birth / Incorporation Column
        if ("dob" in lbl or "birth" in lbl or "incorp" in lbl or "date" in lbl) and "pass" not in lbl:
            if extracted_profile.get("dob"):
                mapped[col_id] = extracted_profile["dob"]
                continue

        # 9. User ID Column
        if ("user id" in lbl or "userid" in lbl or "login" in lbl) and "pass" not in lbl:
            if extracted_profile.get("user_id"):
                mapped[col_id] = extracted_profile["user_id"]
                continue

        # Fallback: General identity column (if company name not already assigned)
        if col.get("is_identity") and "name" in lbl:
            cand = extracted_profile.get("company_name") or extracted_profile.get("proprietor_name")
            if cand and col_id not in mapped:
                mapped[col_id] = cand

    return mapped
