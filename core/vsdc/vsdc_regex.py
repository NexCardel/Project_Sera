"""
core/vsdc/vsdc_regex.py — Statutory Formats, Regex Validation & Optical Self-Repair
==================================================================================
Deterministic extraction and optical character confusion self-repair for:
- 15-digit Income Tax Acknowledgement Numbers
- 15-character GST Application Reference Numbers (ARN)
- 10-character Permanent Account Numbers (PAN)
- 15-character Goods and Services Tax Identification Numbers (GSTIN)
- Assessment Years (AY 20XX-YY) & GST Return Periods
- E-Verification & Submission Statuses
"""

import re
from typing import Optional, Dict, Tuple, List
from .vsdc_name_parser import is_valid_name, sanitize_visual_name


# Optical confusion replacement tables
DIGIT_FIX_MAP = {
    'l': '1', 'I': '1', '|': '1', '!': '1',
    'O': '0', 'o': '0', 'D': '0', 'Q': '0',
    'B': '8',
    'S': '5', 's': '5',
    'Z': '2', 'z': '2',
    'g': '9', 'q': '9',
}

LETTER_FIX_MAP = {
    '0': 'O',
    '1': 'I',
    '5': 'S',
    '8': 'B',
    '2': 'Z',
}


def repair_numeric_ack(raw_str: str) -> Optional[str]:
    """
    Validates and repairs a 15-digit ITR Acknowledgement Number from optical OCR text.
    Corrects common OCR digit confusions (l/I -> 1, O/o -> 0, B -> 8, S -> 5)
    and handles OCR space fragmentation (e.g. 5982 9000 0150 925).
    """
    if not raw_str:
        return None

    # 1. Check for exact 15-digit match directly in the raw text
    direct = re.search(r"\b\d{15}\b", raw_str)
    if direct:
        return direct.group(0)

    # 2. Check explicitly labeled Ack numbers that may have optical spaces or hyphens
    labeled_m = re.search(
        r"(?:Acknowledgement|Receipt|Ack|ARN)\s*(?:Number|No\.?)?\s*[:#\-]?\s*([0-9\s\-]{15,25})",
        raw_str,
        re.IGNORECASE,
    )
    if labeled_m:
        raw_cand = labeled_m.group(1)
        clean_cand = re.sub(r"[\s\-]+", "", raw_cand)
        if len(clean_cand) >= 15:
            prefix15 = clean_cand[:15]
            repaired = "".join(DIGIT_FIX_MAP.get(c, c) for c in prefix15)
            if re.match(r"^\d{15}$", repaired):
                return repaired

    # 3. Check tokens of length 15 for optical repair
    for token in re.findall(r"\b[A-Za-z0-9|!]{15}\b", raw_str):
        repaired = "".join(DIGIT_FIX_MAP.get(c, c) for c in token)
        if re.match(r"^\d{15}$", repaired):
            return repaired

    # 4. Fallback: search anywhere in non-space chunks
    for token in raw_str.split():
        clean_tok = re.sub(r"[^\w|!]", "", token)
        if len(clean_tok) == 15:
            repaired = "".join(DIGIT_FIX_MAP.get(c, c) for c in clean_tok)
            if re.match(r"^\d{15}$", repaired):
                return repaired

    # 5. Check consecutive numeric fragments that sum to 15 digits (e.g. '5982 9000 0150 925')
    for m in re.finditer(r"\b(\d{3,6}(?:\s+\d{3,6}){2,4})\b", raw_str):
        merged = re.sub(r"\s+", "", m.group(1))
        if len(merged) == 15 and merged.isdigit():
            return merged

    return None


def repair_gst_arn(raw_str: str) -> Optional[str]:
    """
    Validates and repairs a 14-16 character GST ARN or Transaction / Ack ID (e.g. AA070826000001Z, AA27032419827364).
    Format: 2 letters (State Code) + 11-14 digits + optional checksum letter/digit.
    """
    if not raw_str:
        return None

    # 1. Direct regex match on raw text: 2 letters + 11 to 14 digits + optional 1 alnum
    direct = re.search(r"\b([A-Z]{2}\d{11,14}[A-Z0-9]?)\b", raw_str.upper())
    if direct:
        return direct.group(1)

    # 2. Labeled ARN / Transaction ID pattern (e.g. ARN : AA190826000001Z, Transaction ID: AA27032419827364)
    labeled = re.search(
        r"\b(?:ARN|Transaction\s*(?:ID|No|Number)?|Reference\s*(?:ID|No|Number)?|Ack\s*(?:No|Number)?)\s*(?:is|[-:–—=])?\s*([A-Z0-9\s]{14,20})\b",
        raw_str,
        re.IGNORECASE,
    )
    if labeled:
        clean_tok = re.sub(r"\s+", "", labeled.group(1)).upper()
        if 14 <= len(clean_tok) <= 16 and re.match(r"^[A-Z]{2}\d{11,14}[A-Z0-9]?$", clean_tok):
            return clean_tok

    # 3. Optical repair on 14-16 char tokens
    for token in re.findall(r"\b[A-Za-z0-9]{14,16}\b", raw_str):
        token_upper = token.upper()
        state_code = "".join(LETTER_FIX_MAP.get(c, c) for c in token_upper[:2])
        digits = "".join(DIGIT_FIX_MAP.get(c, c) for c in token_upper[2:-1])
        suffix = token_upper[-1]
        candidate = state_code + digits + suffix
        if re.match(r"^[A-Z]{2}\d{11,14}[A-Z0-9]?$", candidate):
            return candidate

    return None


def extract_pan(text: str) -> Optional[str]:
    """
    Extracts and validates a 10-character Indian PAN.
    Pattern: 5 uppercase letters, 4 digits, 1 uppercase letter.
    Enforces 4th character entity validity: P, C, F, H, A, T, B, L, J, G.
    """
    if not text:
        return None

    text_up = text.upper()

    # 1. Search for standard exact PAN (10 characters: 5 letters, 4 digits, 1 letter)
    matches = re.finditer(r"\b([A-Z]{5}[0-9]{4}[A-Z])\b", text_up)
    for m in matches:
        pan = m.group(1)
        if pan[3] in "PCFHTABRLJG":
            return pan

    # 2. Labeled PAN candidate (explicitly preceded by 'PAN')
    m_labeled = re.search(r"\bPAN\s*[:\-]?\s*([A-Z0-9]{10})\b", text_up)
    if m_labeled:
        cand = m_labeled.group(1)
        prefix = "".join(LETTER_FIX_MAP.get(c, c) for c in cand[:5])
        digits = "".join(DIGIT_FIX_MAP.get(c, c) for c in cand[5:9])
        suffix = "".join(LETTER_FIX_MAP.get(c, c) for c in cand[9:10])
        candidate_pan = prefix + digits + suffix
        if re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", candidate_pan):
            if candidate_pan[3] in "PCFHTABRLJG":
                return candidate_pan

    # 3. Unlabeled candidate: Must NOT be purely alphabetic (skips words like 'PROFESSION', 'COMMISSION')
    # and must already contain at least one genuine digit in the middle numeric slice.
    for m in re.finditer(r"\b([A-Z0-9]{10})\b", text_up):
        cand = m.group(1)
        if cand.isalpha() or not any(c.isdigit() for c in cand[5:9]):
            continue
        prefix = "".join(LETTER_FIX_MAP.get(c, c) for c in cand[:5])
        digits = "".join(DIGIT_FIX_MAP.get(c, c) for c in cand[5:9])
        suffix = "".join(LETTER_FIX_MAP.get(c, c) for c in cand[9:10])
        candidate_pan = prefix + digits + suffix
        if re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", candidate_pan):
            if candidate_pan[3] in "PCFHTABRLJG":
                return candidate_pan

    return None


def extract_gstin(text: str) -> Optional[str]:
    """
    Extracts a 15-character Goods and Services Tax Identification Number (GSTIN).
    Format: 2 digits (State) + 10-char PAN + 1 entity code + 'Z' + 1 checksum.
    """
    if not text:
        return None

    m = re.search(r"\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b", text.upper())
    return m.group(1) if m else None


def extract_assessment_year(text: str) -> Optional[str]:
    """
    Extracts Assessment Year (e.g. 'AY 2025-26', 'A.Y. 2025-26', 'Assessment Year 2025-26').
    - Enforces mathematical year consistency (second year = first year + 1).
    - Ignores portal disclaimer boilerplate (e.g. 'starting Assessment Year 2013-14').
    - Prioritizes explicitly labeled matches and the most recent assessment year.
    """
    if not text:
        return None

    candidates = []

    # 1. Matches AY / A.Y. / Assessment Year: 2025-26, 2025 - 26, 2025-2026, or plain 2025-26
    ay_pattern = re.compile(
        r"(?:(A\.?Y\.?|Assessment\s*Year)\s*[:\-]?)?\s*\b20(\d{2})\s*[-–/]\s*(?:20)?(\d{2})\b",
        re.IGNORECASE,
    )

    for m in ay_pattern.finditer(text):
        label = m.group(1)
        y1 = int(m.group(2))
        y2 = int(m.group(3))
        # Mathematical consistency check: e.g. 25-26
        if y2 != (y1 + 1) % 100 and y2 != y1 + 1:
            continue

        # Check preceding text for portal disclaimer banners (e.g. 'starting Assessment Year 2013-14')
        start_idx = m.start()
        raw_prefix = text[max(0, start_idx - 60):start_idx]
        clause_prefix = re.split(r"[\n\r.!?*•|]", raw_prefix)[-1].lower()
        if any(w in clause_prefix for w in ("starting", "since", "from", "available")):
            continue

        is_labeled = bool(label)
        canon_label = f"AY 20{y1:02d}-{((y1 + 1) % 100):02d}"
        candidates.append((is_labeled, y1, canon_label))

    if not candidates:
        return None

    # Sort by: 1) is_labeled (True first), 2) latest year (y1 descending)
    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
    return candidates[0][2]


def extract_filing_type(text: str, allow_multiple: bool = False) -> Optional[str]:
    """
    Identifies statutory filing form name from optical text.
    Suppresses latching when an open dropdown menu is detected (multiple distinct forms present),
    unless allow_multiple=True (e.g. on historical filed returns views).
    """
    if not text:
        return None

    # 1. Income Tax ITR forms (ITR-1, ITR 1, ITR-4, ITR-4S, Form ITR-1)
    itr_matches = re.findall(r"\b(?:Form\s*)?(ITR\s*[-_]?\s*[1-7UV]|ITR\s*[-_]?\s*4S)\b", text, re.IGNORECASE)
    if itr_matches:
        canonical_forms = []
        for m in itr_matches:
            val = re.sub(r"[-_\s]+", "-", m.upper())
            if not val.startswith("ITR-"):
                val = "ITR-" + val[3:].lstrip("-")
            if val not in canonical_forms:
                canonical_forms.append(val)
        # Dropdown Open Guard: If multiple distinct forms appear, the dropdown list is currently open!
        if len(canonical_forms) > 1 and not allow_multiple:
            return None
        return canonical_forms[0]

    # 2. Statutory Forms (10-IEA, 10BA, 29B, etc.)
    form_m = re.search(r"\b(?:Form\s*)?(10[-_ ]?[A-Z]+|29B|3CA|3CB|15CA|15CB)\b", text, re.IGNORECASE)
    if form_m:
        return "Form " + form_m.group(1).upper().replace(" ", "-")

    # 3. GST Forms
    # Optical repair: GSTR-I, GSTR-l, GSTR-i -> GSTR-1, CMP-O8 -> CMP-08
    text_fixed = re.sub(r"\bGSTR[-_\s]*[Ili]\b", "GSTR-1", text, flags=re.IGNORECASE)
    text_fixed = re.sub(r"\bCMP[-_\s]*[Oo]8\b", "CMP-08", text_fixed, flags=re.IGNORECASE)
    gst_matches = re.findall(r"\b(GSTR[-_\s]?[1-9A-Z]+|CMP[-_\s]?08|IFF)\b", text_fixed, re.IGNORECASE)
    if gst_matches:
        canonical_gst = []
        for m in gst_matches:
            val = re.sub(r"[-_\s]+", "-", m.upper())
            if val not in canonical_gst:
                canonical_gst.append(val)
        if len(canonical_gst) > 1 and not allow_multiple:
            return None
        return canonical_gst[0]

    return None


def resolve_gst_form_type_from_url(url: Optional[str], text: Optional[str] = None) -> Optional[str]:
    """
    Resolves the authoritative GST form type directly from the browser URL path.
    Prevents on-screen informational disclaimers (e.g. 'required to file GSTR-1 and GSTR-3B')
    from polluting or misclassifying the active filing form.

    Examples:
    - returns/auth/gstr3b/filing -> GSTR-3B
    - returns/auth/gstr1/file   -> GSTR-1 (or GSTR-1/IFF if IFF indicated in URL/text)
    - returns/auth/iff/file     -> GSTR-1/IFF
    - returns/auth/cmp08/filing -> CMP-08
    - returns/auth/gstr4/filing -> GSTR-4
    - returns/auth/gstr9/filing -> GSTR-9
    - returns/auth/gstr9c/filing -> GSTR-9C
    - ?rtn_typ=GSTR1 -> GSTR-1
    """
    if not url:
        return extract_filing_type(text) if text else None

    import urllib.parse
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    
    # Check query params first (e.g., ?rtn_typ=GSTR1, ?f=gstr3b)
    url_param_form = ""
    if "rtn_typ" in qs:
        url_param_form = qs["rtn_typ"][0].lower()
    elif "f" in qs:
        url_param_form = qs["f"][0].lower()
    elif "returnType" in qs:
        url_param_form = qs["returnType"][0].lower()

    # Create a concatenated search string from path, query params, and hash fragment
    u_lower = (parsed.path + " " + url_param_form + " " + parsed.fragment).lower()

    if "gstr3b" in u_lower or "gstr-3b" in u_lower:
        return "GSTR-3B"
    if "cmp08" in u_lower or "cmp-08" in u_lower:
        return "CMP-08"
    if "iff" in u_lower:
        return "GSTR-1/IFF"
    if "gstr9c" in u_lower or "gstr-9c" in u_lower:
        return "GSTR-9C"
    if "gstr9" in u_lower or "gstr-9" in u_lower:
        return "GSTR-9"
    if "gstr10" in u_lower or "gstr-10" in u_lower:
        return "GSTR-10"
    if "gstr11" in u_lower or "gstr-11" in u_lower:
        return "GSTR-11"
    if "gstr4" in u_lower or "gstr-4" in u_lower:
        return "GSTR-4"
    if "gstr5a" in u_lower or "gstr-5a" in u_lower:
        return "GSTR-5A"
    if "gstr5" in u_lower or "gstr-5" in u_lower:
        return "GSTR-5"
    if "gstr6" in u_lower or "gstr-6" in u_lower:
        return "GSTR-6"
    if "gstr7" in u_lower or "gstr-7" in u_lower:
        return "GSTR-7"
    if "gstr8" in u_lower or "gstr-8" in u_lower:
        return "GSTR-8"
    if "gstr1" in u_lower or "gstr-1" in u_lower:
        return "GSTR-1"
    if "itc04" in u_lower or "itc-04" in u_lower:
        return "ITC-04"
    if "drc03" in u_lower or "drc-03" in u_lower or "drc03a" in u_lower:
        return "DRC-03"

    return extract_filing_type(text) if text else None


def extract_gst_filing_date(text: str) -> Optional[str]:
    """
    Extracts the statutory filing date from the GST success banner/receipt:
    e.g., 'Date of filing: 15/09/2026' or 'Date: 15-09-2026'.
    """
    if not text:
        return None
    m = re.search(
        r"\b(?:Date\s*of\s*filing|Filing\s*Date|Filed\s*on|Date)\s*[:\-–—]?\s*([0-9]{1,2}[\/\-][0-9]{1,2}[\/\-][0-9]{4})",
        text,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()
    return None


def is_page_loading(text: str) -> bool:
    """
    Checks if optical text indicates an active asynchronous loading/transition state.
    """
    if not text:
        return False
    t = text.lower()
    return any(indicator in t for indicator in (
        "loading ...",
        "loading...",
        "please wait",
        "fetching details",
        "fetching...",
        "0 filings till date loading",
        "0-0 of 0 items",
    ))


def extract_view_filed_returns_card(text: str) -> Optional[Dict[str, Any]]:
    """
    Extracts the latest filed return card record from historical view screens
    (e.g. itr_view_filed_returns). Extracts the latest Assessment Year, Ack Number,
    Form Type, and Status.
    Returns details for strictly the single latest return on screen, or None if still loading / empty.
    """
    if not text or is_page_loading(text):
        return None

    ay = extract_assessment_year(text)
    ack = repair_numeric_ack(text)
    if not ay or not ack:
        return None

    form = extract_filing_type(text, allow_multiple=True) or "ITR-1"
    status = classify_verification_status(text)
    if status in ("Not Submitted", "Filing Submitted"):
        status = "Submitted (e-Verified)"

    return {
        "ack": ack,
        "ay": ay,
        "form": form,
        "status": status,
    }


def classify_verification_status(text: str) -> str:
    """
    Classifies the filing submission and e-verification status.
    Distinguishes between:
    - Submitted (e-Verified): Successfully completed verification or processed
    - Submitted (Not e-Verified): Return filed, awaiting e-verification (e.g. pending for e-verification, verify later, 30 days)
    - Filing Submitted: Standard submission confirmation
    """
    if not text:
        return "Submitted"

    lower = text.lower()
    # Normalize optical variations of e-verification / e-verify (e.g. 'e- verification', 'e - verify')
    normalized = re.sub(r"e\s*[-_ ]\s*verif", "e-verif", lower)

    # 1. Check if already successfully e-verified or processed
    if any(k in normalized for k in (
        "successfully e-verified",
        "successfully verified",
        "return has been verified",
        "return is verified",
        "e-verified on",
        "verified on",
        "processed with no demand/refund",
        "processed with refund",
        "processed with demand",
        "under processing",
        "processed",
        "verified with aadhaar otp",
        "otp validated",
        "e-verification successful",
        "e-verification completed",
    )):
        return "Submitted (e-Verified)"

    # 2. Check if pending verification / unverified
    if any(k in normalized for k in (
        "pending for e-verification",
        "pending for verification",
        "pending e-verification",
        "pending verification",
        "verification pending",
        "verify later",
        "e-verify later",
        "verify within 30 days",
        "not e-verified",
        "pending for e-verify",
        "under e-verification",
        "need to e-verify",
    )):
        return "Submitted (Not e-Verified)"

    # 3. Fallback: general e-verified vs unverified
    if "e-verified" in normalized and "not e-verified" not in normalized:
        return "Submitted (e-Verified)"

    # 4. Check explicit submission success
    if any(k in normalized for k in (
        "submitted successfully",
        "successfully submitted",
        "return submitted",
        "filed successfully",
        "itr filed",
        "filing confirmed",
        "return has been submitted",
        "you have successfully submitted your return",
        "arn generated",
        "acknowledgement number",
    )):
        return "Filing Submitted"

    return "Not Submitted"


def extract_gst_filing_preference(text: str) -> Optional[str]:
    """
    Extracts GST Return Filing Preference from the welcome/dashboard page.
    Matches e.g. 'Return filing preference (Jul-Sep 2026) : Quarterly (Change)',
    'Filing preference: Monthly', or QRMP indicator.
    Returns 'Quarterly' or 'Monthly'.
    """
    if not text:
        return None
    # 1. Direct label pattern
    m = re.search(r"(?:return\s+)?filing\s+preference[^\n:]*[:\-–—\s]*\s*(Quarterly|Monthly)", text, re.IGNORECASE)
    if m:
        return m.group(1).title()
    # 2. Proximity window within 80 characters of 'filing preference'
    m2 = re.search(r"filing\s*preference.{0,80}?\b(Quarterly|Monthly)\b", text, re.IGNORECASE | re.DOTALL)
    if m2:
        return m2.group(1).title()
    # 3. QRMP indicator
    if re.search(r"\bQRMP\b", text, re.IGNORECASE):
        return "Quarterly"
    # 4. Quarterly Return descriptor or GSTR-3BQ indicator
    if re.search(r"\b(?:Quarterly\s+Return|GSTR[-_ ]*3BQ)\b", text, re.IGNORECASE):
        return "Quarterly"
    if re.search(r"\bMonthly\s+Return\b", text, re.IGNORECASE):
        return "Monthly"
    return None


def extract_gst_fy(text: str) -> Optional[str]:
    """
    Extracts GST Financial Year (e.g. '2026-27', '2025-26', '2026-2027').
    Handles 'FY - 2026-27', 'Financial Year : 2026-27', 'FY\\n2026-27',
    spaces within hyphen ('2026 - 27'), en-dash ('2026–27'), etc.
    """
    if not text:
        return None

    # 1. Explicit FY / Financial Year label (allowing line breaks and spaces)
    fy_m = re.search(
        r"(?:FY|Financial\s*Year)\s*[-:–—]?\s*[\r\n]*\s*(20\d{2}\s*[-–—/]\s*(?:\d{2}|20\d{2}))",
        text,
        re.IGNORECASE,
    )
    if fy_m:
        return re.sub(r"\s+", "", fy_m.group(1)).replace("–", "-").replace("—", "-").replace("/", "-")

    # 2. Standalone FY pattern (e.g. 2025-26, 2026-27)
    standalone_m = re.search(r"\b(20[2-9]\d\s*[-–—]\s*(?:\d{2}|20\d{2}))\b", text)
    if standalone_m:
        return re.sub(r"\s+", "", standalone_m.group(1)).replace("–", "-").replace("—", "-")

    return None


VALID_GST_PERIOD_PATTERN = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|"
    r"Q[1-4]|Quarter\s*[1-4]|"
    r"Apr[- ]*Jun|Jul[- ]*Sep|Oct[- ]*Dec|Jan[- ]*Mar)\b",
    re.IGNORECASE,
)

PERIOD_NOISE_KEYWORDS = (
    "status", "due", "date", "dashboard", "network", "search",
    "taxpayer", "returns", "designed", "developed", "updated", "site",
    "goods", "services", "help", "facilities", "invoice"
)


def is_valid_gst_tax_period(val: Optional[str]) -> bool:
    """
    Validates whether a candidate string is an authentic GST tax period (Month or Quarter).
    Rejects OCR noise words, navigation headers, or portal boilerplate (e.g. 'Status-Due').
    """
    if not val:
        return False
    clean = str(val).strip()
    if len(clean) < 2 or len(clean) > 35:
        return False
    clean_low = clean.lower()
    if any(noise in clean_low for noise in PERIOD_NOISE_KEYWORDS):
        return False
    return bool(VALID_GST_PERIOD_PATTERN.search(clean))


def is_valid_gst_status(val: Optional[str]) -> bool:
    """
    Validates whether a candidate string is an authoritative GST filing status.
    Rejects footer disclaimers, timestamps, or arbitrary text (e.g. 'Due Date - Site Last Updated...').
    """
    if not val:
        return False
    clean = str(val).strip().lower()
    if any(k in clean for k in ("site last updated", "due date", "designed", "developed", "best viewed", "resolution", "network")):
        return False
    allowed = (
        "not filed", "filed", "submitted", "initiated", "in progress", "draft",
        "pending", "ready to file", "under process"
    )
    return any(a in clean for a in allowed)


def extract_gst_tax_period(text: str) -> Optional[str]:
    """
    Extracts the GST Tax Period (Month/Quarter).
    Examples:
      - 'Tax Period - June(Q)' -> 'June(Q)'
      - 'Tax Period: June (Q)' -> 'June(Q)'
      - 'Tax Period -\\nJune(Q)' -> 'June(Q)'
      - 'Return Period - Apr-Jun' -> 'Apr-Jun'
      - 'Tax Period - Q1' -> 'Q1'
      - 'Tax Period - July' -> 'July'
    """
    if not text:
        return None

    month_names_pat = (
        r"(?:January|February|March|April|May|June|July|August|September|October|November|December|"
        r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
    )

    # 1. Explicit period token directly following Tax / Return / Filing Period label
    # Note: Ranges (e.g. Apr-Jun) MUST precede single month names to prevent partial matches.
    explicit_pat = (
        r"(?:Tax|Return|Filing)\s*Period\s*[-:–—]?\s*[\r\n]*\s*"
        rf"([A-Za-z]{{3,9}}\s*[-–—/]\s*[A-Za-z]{{3,9}}|"
        rf"{month_names_pat}\s*(?:\([A-Za-z0-9]+\))?|"
        r"Q[1-4](?:\s*\([A-Za-z0-9]+\))?|"
        r"(?:0[1-9]|1[0-2])[\/\-](?:20)?[0-9]{2})"
    )
    m = re.search(explicit_pat, text, re.IGNORECASE)
    if m:
        val = m.group(1).strip()
        val = re.sub(r"\s*([-–—/])\s*", r"\1", val)
        val = re.sub(r"([A-Za-z]+)\s+\(([A-Za-z0-9]+)\)", r"\1(\2)", val)
        if is_valid_gst_tax_period(val):
            return val

    # 2. Line-by-line inspection around any line containing 'Period'
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for i, line in enumerate(lines):
        if re.search(r"\b(?:Tax|Return|Filing)\s*Period\b", line, re.IGNORECASE):
            window = " ".join(lines[i:min(i + 3, len(lines))])
            after_label = window[re.search(r"Period", window, re.IGNORECASE).end():]
            token_m = re.search(
                rf"\b([A-Za-z]{{3,9}}\s*[-–—/]\s*[A-Za-z]{{3,9}}|{month_names_pat}\s*(?:\([A-Za-z0-9]+\))?|Q[1-4](?:\s*\([A-Za-z0-9]+\))?)\b",
                after_label,
                re.IGNORECASE,
            )
            if token_m:
                val = token_m.group(1).strip()
                val = re.sub(r"\s*([-–—/])\s*", r"\1", val)
                val = re.sub(r"([A-Za-z]+)\s+\(([A-Za-z0-9]+)\)", r"\1(\2)", val)
                if is_valid_gst_tax_period(val):
                    return val

    # 3. Delimited capture after Period label up to next field keyword
    delim_m = re.search(
        r"(?:Tax|Return|Filing)\s*Period\s*[-:–—]?\s*[\r\n]*\s*([A-Za-z0-9()\-_/\s]+?)(?=\s*(?:Status|Due\s*Date|FY|Financial|Trade|Legal|GSTIN|Indicates|\*|\n|$))",
        text,
        re.IGNORECASE,
    )
    if delim_m:
        cand = delim_m.group(1).strip()
        if cand and not re.match(r"^(?:status|due\s*date|fy|financial|na|-+)$", cand, re.IGNORECASE):
            cand = re.sub(r"([A-Za-z]+)\s+\(([A-Za-z0-9]+)\)", r"\1(\2)", cand)
            if is_valid_gst_tax_period(cand):
                return cand

    # 4. Fallback standalone search for Month with (Q) or quarter range anywhere in text
    standalone_m = re.search(
        rf"(?:\b({month_names_pat}\s*\([A-Za-z0-9]+\))|\b((?:Apr[- ]*Jun|Jul[- ]*Sep|Oct[- ]*Dec|Jan[- ]*Mar))\b)",
        text,
        re.IGNORECASE,
    )
    if standalone_m:
        val = (standalone_m.group(1) or standalone_m.group(2) or "").strip()
        val = re.sub(r"([A-Za-z]+)\s+\(([A-Za-z0-9]+)\)", r"\1(\2)", val)
        val = re.sub(r"\s*([-–—/])\s*", r"\1", val)
        if is_valid_gst_tax_period(val):
            return val

    return None


def extract_gst_status(text: str) -> Optional[str]:
    """
    Extracts authoritative filing status from GST forms/tables.
    Handles 'Filed', 'Not Filed', 'Submitted', 'Initiated', 'Draft', 'Pending', etc.,
    including multiline separation ('Status -\\nFiled') and column interleaving.
    """
    if not text:
        return None

    # 1. Tier 1: Look for known status keywords directly following a Status label
    # NOTE: "Not Filed" MUST precede "Filed" to prevent substring mismatch!
    status_kw_pattern = (
        r"(?:Filing\s*Status|Return\s*Status|Status)\s*[-:–—]?\s*[\r\n]*\s*"
        r"\b(Not\s*Filed|Filed\s*(?:&|and)\s*Confirmed|Filed|Submitted|Initiated|In\s*Progress|Draft|Pending|Ready\s*to\s*File|Under\s*Process)\b"
    )
    status_m = re.search(status_kw_pattern, text, re.IGNORECASE)
    if status_m:
        raw = status_m.group(1).strip()
        low = raw.lower()
        if "not filed" in low:
            return "Not Filed"
        elif "filed" in low:
            return "Filed"
        elif "submitted" in low:
            return "Submitted"
        elif "initiated" in low or "draft" in low or "progress" in low:
            return "Initiated"
        elif "pending" in low:
            return "Pending"
        elif "ready" in low:
            return "Ready to File"
        return raw.title()

    # 2. Tier 2: Line-by-line inspection around any line containing 'Status'
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for i, line in enumerate(lines):
        if re.search(r"\bStatus\b", line, re.IGNORECASE):
            window = " ".join(lines[i:min(i + 3, len(lines))])
            after_status = window[re.search(r"\bStatus\b", window, re.IGNORECASE).start():]
            kw_match = re.search(
                r"\b(Not\s*Filed|Filed\s*(?:&|and)\s*Confirmed|Filed|Submitted|Initiated|In\s*Progress|Draft|Pending|Ready\s*to\s*File)\b",
                after_status,
                re.IGNORECASE,
            )
            if kw_match:
                k_val = kw_match.group(1).strip()
                k_low = k_val.lower()
                if "not filed" in k_low:
                    return "Not Filed"
                elif "filed" in k_low:
                    return "Filed"
                elif "submitted" in k_low:
                    return "Submitted"
                elif "initiated" in k_low or "draft" in k_low or "progress" in k_low:
                    return "Initiated"
                elif "pending" in k_low:
                    return "Pending"
                return k_val.title()

    # 3. Tier 3: Delimited capture after Status label up to the next field keyword
    delim_m = re.search(
        r"(?:Filing\s*Status|Return\s*Status|Status)\s*[-:–—]?\s*[\r\n]*\s*([A-Za-z0-9\s\-_/]+?)(?=\s*(?:Due\s*Date|FY|Financial|Tax\s*Period|Return\s*Period|Trade\s*Name|Legal\s*Name|GSTIN|Indicates|\*|\n|$))",
        text,
        re.IGNORECASE,
    )
    if delim_m:
        cand = delim_m.group(1).strip()
        if cand and not re.match(r"^(?:due\s*date|fy|financial|na|trade|legal|gstin|-+)$", cand, re.IGNORECASE):
            cand_low = cand.lower()
            if "not filed" in cand_low:
                return "Not Filed"
            elif "filed" in cand_low:
                return "Filed"
            elif "submitted" in cand_low:
                return "Submitted"
            elif "initiated" in cand_low or "draft" in cand_low or "progress" in cand_low:
                return "Initiated"
            elif "pending" in cand_low:
                return "Pending"
            elif "ready" in cand_low:
                return "Ready to File"
            # Strict protection: NEVER return arbitrary candidate text unless it matches an allowed status!
            return None

    return None


def format_gst_period_label(tax_period: Optional[str], fy: Optional[str]) -> str:
    """
    Formats the canonical GST period label combining Month/Quarter and Financial Year.
    Examples:
      - tax_period='June(Q)', fy='2026-27' -> 'June(Q) (FY 2026-27)'
      - tax_period='June', fy='2026-27'    -> 'June (FY 2026-27)'
      - tax_period='June(Q)', fy=None       -> 'June(Q)'
      - tax_period=None, fy='2026-27'       -> 'FY 2026-27'
    """
    tp = (tax_period or "").strip()
    f_year = (fy or "").strip()
    if tp and f_year:
        if f"FY {f_year}" in tp or f"({f_year})" in tp:
            return tp
        return f"{tp} (FY {f_year})"
    if tp:
        return tp
    if f_year:
        return f"FY {f_year}" if not f_year.upper().startswith("FY") else f_year
    return ""


def extract_gst_form_table(
    text: str,
    lines: Optional[List[str]] = None,
    url: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    """
    Extracts structured metadata from the 4-column GST Return Form details table
    (e.g., on return.gst.gov.in/returns/auth/gstr1, gstr3b, cmp08, iff):
    - gstin & derived pan
    - legal_name
    - trade_name
    - fy
    - tax_period
    - period_label (canonical Month + FY)
    - status
    - due_date
    - form_type

    Supports:
    1. Single-line labeled pairs ('Legal Name - FATIMA BIBI')
    2. Delimited multi-column horizontal rows ('GSTIN - ... Legal Name - ... Trade Name - ...')
    3. Multi-line wrapped / adjacent lines (Header line 'Legal Name' followed by 'FATIMA BIBI')
    4. Full-page OCR token proximity scanning across all 4 statutory columns.
    """
    res: Dict[str, Optional[str]] = {
        "gstin": None,
        "pan": None,
        "legal_name": None,
        "trade_name": None,
        "fy": None,
        "tax_period": None,
        "period_label": None,
        "status": None,
        "due_date": None,
        "form_type": None,
    }
    if not text and not lines:
        return res

    raw_text = text or ""
    if lines is not None:
        norm_lines = [ln.strip() for ln in lines if ln and ln.strip()]
    else:
        norm_lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]

    # 1. GSTIN & PAN
    gstin_m = re.search(r"GSTIN(?:\/UIN)?\s*[-:–—]?\s*([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z])", raw_text, re.IGNORECASE)
    if gstin_m:
        res["gstin"] = gstin_m.group(1).upper()
    else:
        res["gstin"] = extract_gstin(raw_text)

    # If still not found, search lines for GSTIN anchor line
    if not res["gstin"]:
        for i, ln in enumerate(norm_lines):
            if re.match(r"^GSTIN(?:\/UIN)?\s*[-:–—]?$", ln, re.IGNORECASE):
                if i + 1 < len(norm_lines):
                    g_cand = extract_gstin(norm_lines[i + 1])
                    if g_cand:
                        res["gstin"] = g_cand
                        break

    if res["gstin"] and len(res["gstin"]) >= 12:
        res["pan"] = res["gstin"][2:12]

    # 2. FY (Financial Year)
    res["fy"] = extract_gst_fy(raw_text)
    if not res["fy"]:
        for i, ln in enumerate(norm_lines):
            if re.match(r"^(?:Financial\s*Year|FY)\s*[-:–—]?$", ln, re.IGNORECASE):
                if i + 1 < len(norm_lines):
                    f_cand = extract_gst_fy(norm_lines[i + 1])
                    if f_cand:
                        res["fy"] = f_cand
                        break

    # 3. Legal Name
    # 3a. Inline regex: "Legal Name [-:] <value>"
    legal_m = re.search(
        r"Legal\s*Name(?:\s+of\s+Business)?\s*[-:–—]\s*([A-Za-z0-9\s\.\-_&]+?)(?=\s*(?:Tax\s*Period|Return\s*Period|Trade\s*Name|Status|Due\s*Date|FY|Financial|GSTIN|Indicates|\*|\n|$))",
        raw_text,
        re.IGNORECASE,
    )
    if legal_m:
        cand = legal_m.group(1).strip()
        cand_clean = sanitize_visual_name(cand)
        if cand_clean and is_valid_name(cand_clean) and not re.match(r"^(?:status|due\s*date|fy|financial|na|trade|gstin|-+)$", cand_clean, re.IGNORECASE):
            res["legal_name"] = cand_clean

    # 3b. Line-by-line anchor inspection for Legal Name
    if not res["legal_name"]:
        for i, ln in enumerate(norm_lines):
            if re.match(r"^(?:Legal\s*Name(?:\s+of\s+Business)?|Legal\s*Name)\s*[-:–—]?$", ln, re.IGNORECASE):
                if i + 1 < len(norm_lines):
                    cand = norm_lines[i + 1].strip()
                    cand_clean = sanitize_visual_name(cand)
                    if cand_clean and is_valid_name(cand_clean) and not re.search(r"\b(?:Tax\s*Period|Return\s*Period|Trade\s*Name|Status|Due\s*Date|FY|Financial|GSTIN|Indicates|\*)\b", cand, re.IGNORECASE):
                        if len(cand_clean) >= 3 and not cand_clean.startswith("-"):
                            res["legal_name"] = cand_clean
                            break
            m_inline = re.match(r"^Legal\s*Name(?:\s+of\s+Business)?\s*[-:–—\s]+([A-Za-z0-9\s\.\-_&]{3,60})$", ln, re.IGNORECASE)
            if m_inline:
                cand = m_inline.group(1).strip()
                cand_clean = sanitize_visual_name(cand)
                if cand_clean and is_valid_name(cand_clean) and not re.search(r"\b(?:Tax\s*Period|Return\s*Period|Trade\s*Name|Status|Due\s*Date|FY|Financial|GSTIN|Indicates)\b", cand, re.IGNORECASE):
                    res["legal_name"] = cand_clean
                    break

    # 4. Trade Name
    # 4a. Inline regex: "Trade Name [-:] <value>"
    trade_m = re.search(
        r"Trade\s*Name(?:\s+of\s+Business)?\s*[-:–—]\s*([A-Za-z0-9\s\.\-_&]+?)(?=\s*(?:Legal\s*Name|Status|Due\s*Date|Tax\s*Period|Return\s*Period|FY|Financial|GSTIN|Indicates|\*|\n|$))",
        raw_text,
        re.IGNORECASE,
    )
    if trade_m:
        cand = trade_m.group(1).strip()
        cand_clean = sanitize_visual_name(cand)
        if cand_clean and is_valid_name(cand_clean) and not re.match(r"^(?:status|due\s*date|fy|financial|na|legal|gstin|-+)$", cand_clean, re.IGNORECASE):
            res["trade_name"] = cand_clean

    # 4b. Line-by-line anchor inspection for Trade Name
    if not res["trade_name"]:
        for i, ln in enumerate(norm_lines):
            if re.match(r"^(?:Trade\s*Name(?:\s+of\s+Business)?|Trade\s*Name)\s*[-:–—]?$", ln, re.IGNORECASE):
                if i + 1 < len(norm_lines):
                    cand = norm_lines[i + 1].strip()
                    cand_clean = sanitize_visual_name(cand)
                    if cand_clean and is_valid_name(cand_clean) and not re.search(r"\b(?:Legal\s*Name|Tax\s*Period|Return\s*Period|Status|Due\s*Date|FY|Financial|GSTIN|Indicates|\*)\b", cand, re.IGNORECASE):
                        if len(cand_clean) >= 3 and not cand_clean.startswith("-"):
                            res["trade_name"] = cand_clean
                            break
            m_inline = re.match(r"^Trade\s*Name(?:\s+of\s+Business)?\s*[-:–—\s]+([A-Za-z0-9\s\.\-_&]{3,60})$", ln, re.IGNORECASE)
            if m_inline:
                cand = m_inline.group(1).strip()
                cand_clean = sanitize_visual_name(cand)
                if cand_clean and is_valid_name(cand_clean) and not re.search(r"\b(?:Legal\s*Name|Tax\s*Period|Return\s*Period|Status|Due\s*Date|FY|Financial|GSTIN|Indicates)\b", cand, re.IGNORECASE):
                    res["trade_name"] = cand_clean
                    break

    # 5. Tax Period (Month/Quarter)
    res["tax_period"] = extract_gst_tax_period(raw_text)
    if not res["tax_period"]:
        for i, ln in enumerate(norm_lines):
            if re.search(r"\b(?:Tax|Return|Filing)\s*Period\b", ln, re.IGNORECASE):
                sub_block = " ".join(norm_lines[i:min(i + 3, len(norm_lines))])
                tp = extract_gst_tax_period(sub_block)
                if tp:
                    res["tax_period"] = tp
                    break

    # 6. Canonical Period Label (Month + FY)
    res["period_label"] = format_gst_period_label(res["tax_period"], res["fy"]) or None

    # 7. Status
    res["status"] = extract_gst_status(raw_text)
    if not res["status"]:
        for i, ln in enumerate(norm_lines):
            if re.search(r"\b(?:Filing\s*Status|Return\s*Status|Status)\b", ln, re.IGNORECASE):
                sub_block = " ".join(norm_lines[i:min(i + 3, len(norm_lines))])
                st = extract_gst_status(sub_block)
                if st:
                    res["status"] = st
                    break

    # 8. Due Date
    due_m = re.search(
        r"Due\s*Date\s*[-:–—]?\s*[\r\n]*\s*([0-9]{1,2}(?:[\/\-][0-9]{1,2}[\/\-][0-9]{2,4}|[\/\-\s]+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember))[\/\-\s]+[0-9]{2,4}))",
        raw_text,
        re.IGNORECASE,
    )
    if due_m:
        res["due_date"] = due_m.group(1).strip()
    elif not res["due_date"]:
        for i, ln in enumerate(norm_lines):
            if re.match(r"^Due\s*Date\s*[-:–—]?$", ln, re.IGNORECASE):
                if i + 1 < len(norm_lines):
                    cand = norm_lines[i + 1].strip()
                    d_m = re.search(r"([0-9]{1,2}[\/\-][0-9]{1,2}[\/\-][0-9]{2,4})", cand)
                    if d_m:
                        res["due_date"] = d_m.group(1).strip()
                        break

    # 9. Form Type (Prioritize URL, then Banner or Breadcrumbs or Screen text)
    if url:
        url_form = resolve_gst_form_type_from_url(url, raw_text)
        if url_form:
            res["form_type"] = url_form

    if not res["form_type"]:
        text_fixed = re.sub(r"\bGSTR[-_\s]*[Ili]\b", "GSTR-1", raw_text, flags=re.IGNORECASE)
        text_fixed = re.sub(r"\bCMP[-_\s]*[Oo]8\b", "CMP-08", text_fixed, flags=re.IGNORECASE)
        form_m = re.search(
            r"\b(GSTR[-_ ]*1(?:\s*\/\s*IFF)?|GSTR[-_ ]*3B|CMP[-_ ]*08|GSTR[-_ ]*4|GSTR[-_ ]*9C|GSTR[-_ ]*9|GSTR[-_ ]*7|GSTR[-_ ]*8|IFF)\b",
            text_fixed,
            re.IGNORECASE,
        )
        if form_m:
            raw_f = form_m.group(1).upper().replace(" ", "").replace("_", "-")
            if "GSTR-1" in raw_f or "IFF" in raw_f:
                res["form_type"] = "GSTR-1/IFF" if "IFF" in raw_f else "GSTR-1"
            elif "GSTR-3B" in raw_f:
                res["form_type"] = "GSTR-3B"
            elif "CMP-08" in raw_f:
                res["form_type"] = "CMP-08"
            elif "GSTR-4" in raw_f:
                res["form_type"] = "GSTR-4"
            elif "GSTR-9C" in raw_f:
                res["form_type"] = "GSTR-9C"
            elif "GSTR-9" in raw_f:
                res["form_type"] = "GSTR-9"
            else:
                res["form_type"] = raw_f

    return res


