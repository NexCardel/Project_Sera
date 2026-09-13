"""
core/vsdc/vsdc_name_parser.py — Visual Name Extraction & Normalization
======================================================================
Integrates:
- Spatial label proximity (reading text next to 'Name', 'Taxpayer Name', etc.)
- Material Icon ligature stripping and portal role-tag removal
- python-nameparser (HumanName) for structural First/Middle/Last dissection
"""

import re
from typing import Dict, List, Optional
from nameparser import HumanName

# Noise words to discard from name candidates (combining portal UI labels and common words)
NOISE_WORDS = {
    # Taxpayer roles and portal UI controls
    "INDIVIDUAL", "TAXPAYER", "HUF", "COMPANY", "REPRESENTATIVE",
    "DIRECTOR", "PARTNER", "PROPRIETOR", "WELCOME", "LOGOUT", "DASHBOARD",
    "SELECT", "PROFILE", "DETAILS", "STATUS", "RETURN", "RETURNS", "INCOME", "TAX", "ITR",
    "FILING", "FIIING", "EFIIING", "E-FIIING", "FIILING", "FILNG", "E-FILNG",
    "CALL US", "ENGLISH", "HELP", "FEEDBACK", "NOTIFICATIONS",
    "HOME", "VIEW", "DOWNLOAD", "SUBMIT", "SUBMITTED", "PAN", "GSTIN",
    "ASSESSEE", "ACK", "ACKNOWLEDGEMENT", "NUMBER", "DATE", "TIME",
    "PENDING", "ACTIONS", "ACTION", "GRIEVANCES", "AUTHORISED", "PARTNERS", "SERVICES",
    "AIS", "CALL", "US", "DEPARTMENT", "GOVERNMENT", "INDIA", "ANYWHERE",
    "ANYTIME", "E-FILING", "EFILING", "E-FILE", "EFILE", "SKIP", "MAIN",
    "CONTENT", "ACCESSIBILITY", "SEARCH", "USER", "PAYMENT", "CHALLAN",
    "ORIGINAL", "REVISED", "PROCESSED", "DEMAND", "REFUND", "FAILED",
    "V", "SECURE", "ACCESS", "MESSAGE", "PASSWORD", "FORGOT", "CONTINUE", "REGISTER",
    # Extension and App UI Badges
    "SERA", "ASSIST", "SERA ASSIST", "SCC", "EXTENSION",
    # Portal form labels and indicators
    "INDICATES", "MANDATORY", "FIELDS", "FIELD", "ASTERISK",
    # Additional portal stopwords from extract_names_gui.py
    "AND", "OR", "THE", "OF", "TO", "IN", "ON", "FOR", "BY", "AT", "IS", "IT",
    "A", "AN", "AS", "BE", "NO", "ID", "WITH", "FROM", "THIS", "THAT",
    "SESSION", "EXPORT", "EXCEL", "FORM", "RECEIPT", "JSON", "FILTER", "TYPE",
    "SUCCESSFULLY", "VERIFIED", "VERIFICATION", "SELF", "SECTION", "ORDER",
    "DATED", "ACCOUNT", "BANK", "LINKED", "ENSURE", "SAME", "NOTE", "PLEASE",
    "NEXT", "BACK", "CANCEL", "OK", "YES", "TILL", "TILL DATE",
    # Portal instructions and statutory disclaimers
    "NOT", "WHO", "WHICH", "EITHER", "NEITHER", "NOR", "HAS", "HAVE", "HAD",
    "BEEN", "BEING", "ONLY", "OTHER", "SUCH", "INTO", "ANY", "ALL",
    "CAN", "COULD", "SHOULD", "WOULD", "MAY", "MIGHT", "MUST", "THAN",
    "MORE", "LESS", "UNDER", "OVER", "ABOVE", "BELOW", "PER", "EACH",
    "PROFESSION", "BUSINESS", "SALARY", "HOUSE", "PROPERTY", "CAPITAL", "GAINS",
    # Form field labels and personal data attributes
    "FIRST", "MIDDLE", "LAST", "SURNAME", "FATHER", "MOTHER", "SPOUSE",
    "GENDER", "MALE", "FEMALE", "DOB", "BIRTH", "AADHAAR", "ADDRESS",
    "MOBILE", "EMAIL", "PREVIOUS"
}


def sanitize_visual_name(raw_name: str) -> str:
    """
    Cleans raw OCR text of names:
    - Strips Material Design Icon font ligatures (expand_more, account_circle, etc.)
    - Strips portal role tags (Individual, HUF, Company, Proprietor)
    - Strips Unicode dropdown arrows and punctuation symbols
    - Keeps letters, spaces, hyphens, and apostrophes
    """
    if not raw_name:
        return ""

    # 1. Strip Material Icon ligatures first
    clean = re.sub(
        r"(?:EXPAND[_\s]*MORE|EXPANDMORE|EXPAND[_\s]*LESS|EXPANDLESS|"
        r"KEYBOARD[_\s]*ARROW[_\s]*(?:DOWN|UP|RIGHT|LEFT)|ARROW[_\s]*(?:DOWN|UP|DROP)|"
        r"MORE[_\s]*VERT|MORE[_\s]*HORIZ|ACCOUNT[_\s]*CIRCLE|PERSON(?=\s|$)|USER(?=\s|$))",
        " ", raw_name, flags=re.IGNORECASE
    )

    # 2. Strip portal branding & e-filing noise
    clean = re.sub(r"\b[Ee][-_ ]?[Ff][IiLl1|]{2,}[Nn][Gg]\b", " ", clean)
    clean = re.sub(r"\b(?:Income\s*Tax\s*Department|Government\s*Of\s*India)\b", " ", clean, flags=re.IGNORECASE)

    # 3. Strip portal role indicators
    clean = re.sub(
        r"\b(?:Individual|Taxpayer|HUFs?|Company|Representative|Director|Partners?|Proprietor)\b",
        "", clean, flags=re.IGNORECASE
    )

    # 4. Strip Unicode arrows & UI symbols
    clean = re.sub(r"[\u02C0-\u02FF\u25A0-\u25FF\u2300-\u23FF\uFE00-\uFE0F⌵▼▽˅^<>|•\-_:]+", " ", clean)

    # 5. Strip trailing ellipsis (including multiple dots like '......', '...', and Unicode '…')
    clean = re.sub(r"[\u2026\.]+$", "", clean)
    clean = re.sub(r"\s*[\u2026\.]+\s*", " ", clean)

    # 6. Keep only valid name characters
    clean = re.sub(r"[^A-Za-z\s.'-]", " ", clean)

    # 7. Normalize whitespace and strip trailing non-letter symbols
    clean = re.sub(r"\s+", " ", clean).strip().upper()
    clean = re.sub(r"[\s.'-]+$", "", clean).strip()
    return clean


def is_valid_name(name: str) -> bool:
    """
    Validates whether a candidate string is a plausible person or firm name.
    """
    if not name or len(name) < 3 or len(name) > 70:
        return False
    if name in NOISE_WORDS:
        return False
    words = name.split()
    if not words:
        return False
    # If all words or half or more of the words are portal/UI noise words, reject
    noise_count = sum(1 for w in words if w in NOISE_WORDS)
    if noise_count > 0 and (noise_count == len(words) or noise_count >= len(words) / 2):
        return False
    # Must contain at least one vowel
    if not re.search(r"[AEIOUY]", name):
        return False
    # Must match name character set
    return bool(re.match(r"^[A-Z\s.'-]{3,70}$", name))


def is_better_taxpayer_name(new_name: Optional[str], existing_name: Optional[str]) -> bool:
    """
    Evaluates whether an incoming name candidate is strictly more complete and authoritative
    than the currently registered name.

    Handles:
    1. Initial population: Any valid name beats None/empty.
    2. Word count expansion: "WASIL AMAN MANDAL" (3 words) beats "WASIL MANDAL" (2 words).
    3. Same word count with truncation expansion:
       - "RABINDRANATH SAGORE" beats "RABINDRANATH S" (single-letter / prefix expansion).
       - "WASIL AMAN MANDAL" beats "WASIL AMAN MAND" (character length expansion).
    4. Anti-downgrade protection: A truncated header re-scan ("RABINDRANATH S")
       never overwrites an already-expanded full profile name ("RABINDRANATH SAGORE").
    """
    if not new_name:
        return False
    n_clean = sanitize_visual_name(new_name)
    if not is_valid_name(n_clean):
        return False
    if not existing_name:
        return True

    e_clean = sanitize_visual_name(existing_name)
    if not e_clean:
        return True
    if n_clean == e_clean:
        return False

    n_words = n_clean.split()
    e_words = e_clean.split()

    # 1. Word count expansion (more words is generally more complete)
    if len(n_words) > len(e_words):
        return True
    if len(n_words) < len(e_words):
        # Fewer words is a downgrade, unless existing was purely single-letter fragments
        if all(len(w) <= 1 for w in e_words) and all(len(w) >= 2 for w in n_words):
            return True
        return False

    # 2. Equal word count: inspect character length and word expansions
    n_chars = len(n_clean.replace(" ", ""))
    e_chars = len(e_clean.replace(" ", ""))

    # Check if incoming word expands a single-letter initial or truncated prefix
    has_expansion = False
    for nw, ew in zip(n_words, e_words):
        if nw.startswith(ew) and len(nw) > len(ew):
            has_expansion = True
            break
        elif len(ew) <= 2 and len(nw) >= 3:
            has_expansion = True
            break

    if has_expansion and n_chars > e_chars:
        return True

    # Noticeably longer character length with matching leading name
    if n_chars > e_chars and n_words[0] == e_words[0]:
        return True

    return False


def parse_human_name(raw_name: str) -> Dict[str, str]:
    """
    Parses a sanitized name string into structured components using HumanName.
    Returns: { full_name, first_name, middle_name, last_name, title }
    """
    cleaned = sanitize_visual_name(raw_name)
    if not is_valid_name(cleaned):
        return {
            "full_name": "",
            "first_name": "",
            "middle_name": "",
            "last_name": "",
            "title": ""
        }

    parsed = HumanName(cleaned)
    return {
        "full_name": cleaned,
        "first_name": parsed.first.upper() if parsed.first else "",
        "middle_name": parsed.middle.upper() if parsed.middle else "",
        "last_name": parsed.last.upper() if parsed.last else "",
        "title": parsed.title.upper() if parsed.title else ""
    }


def caps_run_name_candidates(text: str) -> List[str]:
    """
    Finds runs of 2+ consecutive ALL-CAPS words and treats them as candidate
    person/firm names unless every word in the run is a portal stopword.
    Adapted from NLP-free entity isolation (Option A).
    """
    candidates = []
    for match in re.finditer(r"\b[A-Z]{2,}(?:\s+[A-Z]{2,}){1,3}\b", text):
        run = match.group()
        words = run.split()
        if any(w not in NOISE_WORDS for w in words):
            clean_words = list(words)
            while clean_words and (clean_words[0] in NOISE_WORDS or len(clean_words[0]) <= 1):
                clean_words.pop(0)
            while clean_words and (clean_words[-1] in NOISE_WORDS or len(clean_words[-1]) <= 1):
                clean_words.pop(-1)
            candidate = " ".join(clean_words)
            if len(clean_words) >= 2 and is_valid_name(candidate) and candidate not in NOISE_WORDS:
                candidates.append(candidate)
    return candidates


_SPACY_NLP = None
_SPACY_INITIALIZED = False


def get_spacy_nlp():
    """
    Safely retrieves or lazily loads the local spaCy NLP engine (en_core_web_sm).
    Falls back gracefully to None if not installed.
    """
    global _SPACY_NLP, _SPACY_INITIALIZED
    if not _SPACY_INITIALIZED:
        _SPACY_INITIALIZED = True
        try:
            import spacy
            _SPACY_NLP = spacy.load("en_core_web_sm")
        except Exception:
            _SPACY_NLP = None
    return _SPACY_NLP


def extract_spacy_person_names(text: str) -> List[str]:
    """
    Runs local spaCy Named Entity Recognition (NER) on text, isolating
    PERSON and taxpayer ORG entities against portal stopwords.
    """
    nlp = get_spacy_nlp()
    if not nlp or not text or not text.strip():
        return []

    names = []
    seen = set()
    try:
        doc = nlp(text)
        for ent in doc.ents:
            if ent.label_ in ("PERSON", "ORG"):
                cand = ent.text.strip()
                clean = sanitize_visual_name(cand)
                words = clean.split()
                while words and (words[0] in NOISE_WORDS or len(words[0]) <= 1):
                    words.pop(0)
                while words and (words[-1] in NOISE_WORDS or len(words[-1]) <= 1):
                    words.pop(-1)
                candidate = " ".join(words)
                if len(words) >= 2 and is_valid_name(candidate) and candidate not in NOISE_WORDS:
                    k = candidate.upper()
                    if k not in seen:
                        seen.add(k)
                        names.append(candidate)
    except Exception:
        pass
    return names


def extract_composite_form_name(lines: List[str]) -> Optional[str]:
    """
    Extracts and synthesizes full taxpayer names from segmented form fields:
    - First Name, Middle Name, Last Name / Surname (colon, next-line, or single line)
    - Tabular field headers followed by value rows
    """
    f_name = ""
    m_name = ""
    l_name = ""

    # Check for horizontal table headers:
    # Line i:   "First Name    Middle Name    Last Name"
    # Line i+1: "WASIL         AMAN           MANDAL"
    for idx, line in enumerate(lines):
        line_clean = line.strip()
        if re.search(r"\bFirst\s*Name\b", line_clean, re.IGNORECASE) and re.search(r"\b(?:Last\s*Name|Surname)\b", line_clean, re.IGNORECASE):
            if idx + 1 < len(lines):
                next_line = lines[idx + 1].strip()
                # 1. Check if next_line itself is the full synthesized name (e.g. "WASIL AMAN MANDAL")
                clean_cand = sanitize_visual_name(next_line)
                cand_words = clean_cand.split()
                if len(cand_words) >= 2 and is_valid_name(clean_cand) and not any(w in NOISE_WORDS for w in cand_words):
                    return clean_cand
                # 2. Check tab or multi-space separation
                val_parts = [p.strip() for p in re.split(r"\s{2,}|\t+", next_line) if p.strip()]
                if len(val_parts) >= 2:
                    clean_parts = [sanitize_visual_name(p) for p in val_parts]
                    if all(is_valid_name(p) or (len(p) >= 2 and p.isalpha()) for p in clean_parts):
                        assembled = " ".join(clean_parts)
                        if is_valid_name(assembled) and not any(w in NOISE_WORDS for w in assembled.split()):
                            return assembled

        # Check consecutive 3 header lines followed by 3 value lines:
        # Line i:   "First Name"
        # Line i+1: "Middle Name"
        # Line i+2: "Last Name"
        # Line i+3: "WASIL"
        # Line i+4: "AMAN"
        # Line i+5: "MANDAL"
        if idx + 5 < len(lines):
            l0, l1, l2 = lines[idx].strip(), lines[idx + 1].strip(), lines[idx + 2].strip()
            if re.search(r"^First\s*Name\b", l0, re.IGNORECASE) and re.search(r"^Middle\s*Name\b", l1, re.IGNORECASE) and re.search(r"^(?:Last\s*Name|Surname)\b", l2, re.IGNORECASE):
                v0, v1, v2 = sanitize_visual_name(lines[idx + 3]), sanitize_visual_name(lines[idx + 4]), sanitize_visual_name(lines[idx + 5])
                parts = [p for p in [v0, v1, v2] if p and is_valid_name(p) and p not in NOISE_WORDS]
                if len(parts) >= 2:
                    assembled = " ".join(parts)
                    if is_valid_name(assembled):
                        return assembled

    for idx, line in enumerate(lines):
        line_clean = line.strip()

        # 1. Single line containing both First and Last Name (e.g. "First Name: MD SAYID Last Name: MOLLA")
        m_single = re.search(
            r"\bFirst\s*Name\s*[:\-]?\s*([A-Za-z\s.'-]{2,40}?)\s*(?:Middle\s*Name\s*[:\-]?\s*([A-Za-z\s.'-]{0,30}?)\s*)?\b(?:Last\s*Name|Sur\s*name)\s*[:\-]?\s*([A-Za-z\s.'-]{2,40})",
            line_clean,
            re.IGNORECASE,
        )
        if m_single:
            f = sanitize_visual_name(m_single.group(1))
            m = sanitize_visual_name(m_single.group(2) or "")
            l = sanitize_visual_name(m_single.group(3))
            parts = [p for p in [f, m, l] if p]
            assembled = " ".join(parts)
            if is_valid_name(assembled):
                return assembled

        # 2. Check First Name field
        m_first = re.search(r"\bFirst\s*Name\s*[:\-]\s*([A-Za-z\s.'-]{2,40})", line_clean, re.IGNORECASE)
        if m_first:
            cand = sanitize_visual_name(m_first.group(1))
            if cand and (is_valid_name(cand) or (len(cand) >= 2 and cand.replace(' ', '').isalpha())):
                f_name = cand
        elif re.match(r"^First\s*Name\s*[:\-]?$", line_clean, re.IGNORECASE) and idx + 1 < len(lines):
            cand = sanitize_visual_name(lines[idx + 1])
            if cand and not re.search(r"\b(?:Name|PAN|DOB|Date|Status|Filing)\b", cand, re.IGNORECASE):
                if is_valid_name(cand) or (len(cand) >= 2 and cand.replace(' ', '').isalpha()):
                    f_name = cand
        elif re.match(r"^First\s*Name\s+([A-Za-z\s.'-]{2,40})$", line_clean, re.IGNORECASE):
            m_no_colon = re.match(r"^First\s*Name\s+([A-Za-z\s.'-]{2,40})$", line_clean, re.IGNORECASE)
            cand = sanitize_visual_name(m_no_colon.group(1))
            if cand and (is_valid_name(cand) or (len(cand) >= 2 and cand.replace(' ', '').isalpha())):
                f_name = cand

        # 3. Check Middle Name field
        m_mid = re.search(r"\bMiddle\s*Name\s*[:\-]\s*([A-Za-z\s.'-]{2,40})", line_clean, re.IGNORECASE)
        if m_mid:
            cand = sanitize_visual_name(m_mid.group(1))
            if cand and (is_valid_name(cand) or (len(cand) >= 2 and cand.replace(' ', '').isalpha())):
                m_name = cand
        elif re.match(r"^Middle\s*Name\s*[:\-]?$", line_clean, re.IGNORECASE) and idx + 1 < len(lines):
            cand = sanitize_visual_name(lines[idx + 1])
            if cand and not re.search(r"\b(?:Name|PAN|DOB|Date|Status|Filing)\b", cand, re.IGNORECASE):
                if is_valid_name(cand) or (len(cand) >= 2 and cand.replace(' ', '').isalpha()):
                    m_name = cand
        elif re.match(r"^Middle\s*Name\s+([A-Za-z\s.'-]{2,40})$", line_clean, re.IGNORECASE):
            m_no_colon = re.match(r"^Middle\s*Name\s+([A-Za-z\s.'-]{2,40})$", line_clean, re.IGNORECASE)
            cand = sanitize_visual_name(m_no_colon.group(1))
            if cand and (is_valid_name(cand) or (len(cand) >= 2 and cand.replace(' ', '').isalpha())):
                m_name = cand

        # 4. Check Last Name / Surname field
        m_last = re.search(r"\b(?:Last\s*Name|Sur\s*name)\s*[:\-]\s*([A-Za-z\s.'-]{2,40})", line_clean, re.IGNORECASE)
        if m_last:
            cand = sanitize_visual_name(m_last.group(1))
            if cand and (is_valid_name(cand) or (len(cand) >= 2 and cand.replace(' ', '').isalpha())):
                l_name = cand
        elif re.match(r"^(?:Last\s*Name|Sur\s*name)\s*[:\-]?$", line_clean, re.IGNORECASE) and idx + 1 < len(lines):
            cand = sanitize_visual_name(lines[idx + 1])
            if cand and not re.search(r"\b(?:Name|PAN|DOB|Date|Status|Filing)\b", cand, re.IGNORECASE):
                if is_valid_name(cand) or (len(cand) >= 2 and cand.replace(' ', '').isalpha()):
                    l_name = cand
        elif re.match(r"^(?:Last\s*Name|Sur\s*name)\s+([A-Za-z\s.'-]{2,40})$", line_clean, re.IGNORECASE):
            m_no_colon = re.match(r"^(?:Last\s*Name|Sur\s*name)\s+([A-Za-z\s.'-]{2,40})$", line_clean, re.IGNORECASE)
            cand = sanitize_visual_name(m_no_colon.group(1))
            if cand and (is_valid_name(cand) or (len(cand) >= 2 and cand.replace(' ', '').isalpha())):
                l_name = cand

    if f_name and l_name:
        parts = [p for p in [f_name, m_name, l_name] if p]
        assembled = " ".join(parts)
        if is_valid_name(assembled):
            return assembled
    elif f_name and not l_name and m_name:
        assembled = f"{f_name} {m_name}"
        if is_valid_name(assembled):
            return assembled

    return None


def extract_name_from_ocr_lines(lines: List[str]) -> Optional[str]:
    """
    Scans a list of text lines for taxpayer name patterns:
    - Composite form fields (First Name, Middle Name, Last Name / Surname)
    - Proximity to 'Name:', 'Taxpayer Name:', 'Legal Name:', 'Assessee Name:'
    - Welcome banner: 'Welcome, <Name>'
    - Header profile badge '<Name> (<PAN>)'
    - Header profile dropdown button with role indicator
    """
    # 0. Check composite form fields first (solves missing last name across separate fields)
    composite = extract_composite_form_name(lines)
    if composite:
        return composite

    # 1. Check Profile / Personal Details labeled rows (e.g. "Full Name as per PAN", "Name as per PAN", "Legal Name", "Full Name")
    for idx, line in enumerate(lines):
        line_clean = line.strip()
        # Direct next-line value for Full Name / Name as per PAN / Taxpayer Name / Assessee Name
        if re.search(r"^(?:Full\s*Name(?:\s*as\s*per\s*PAN)?|Name\s*as\s*per\s*PAN|Legal\s*Name|Taxpayer\s*Name|Assessee\s*Name)\s*[:\-]?$", line_clean, re.IGNORECASE):
            if idx + 1 < len(lines):
                cand = sanitize_visual_name(lines[idx + 1])
                words = cand.split()
                if len(words) >= 2 and is_valid_name(cand) and not any(w in NOISE_WORDS for w in words):
                    return cand
        # Inline with colon or dash: "Full Name as per PAN : WASIL AMAN MANDAL"
        m_inline = re.search(r"\b(?:Full\s*Name(?:\s*as\s*per\s*PAN)?|Name\s*as\s*per\s*PAN|Legal\s*Name|Taxpayer\s*Name|Assessee\s*Name)\s*[:\-]\s*([A-Za-z\s.'-]{3,60})", line_clean, re.IGNORECASE)
        if m_inline:
            cand = sanitize_visual_name(m_inline.group(1))
            words = cand.split()
            if len(words) >= 2 and is_valid_name(cand) and not any(w in NOISE_WORDS for w in words):
                return cand

    # 2. Check for labeled fields (excluding individual First/Middle/Last/Surname fragments)
    for line in lines:
        m_prefix = re.search(
            r"\b(First|Middle|Last|Sur(?:name)?|Full|Legal|Taxpayer|Assessee)?\s*Name\s*[:\-]\s*([A-Za-z\s.'-]{3,60})",
            line,
            re.IGNORECASE,
        )
        if m_prefix:
            prefix = (m_prefix.group(1) or "").lower()
            if prefix in ("first", "middle", "last", "sur", "surname"):
                continue  # Skip solitary composite fragment to avoid dropping last name
            clean = sanitize_visual_name(m_prefix.group(2))
            if is_valid_name(clean):
                return clean

        m_taxpayer = re.search(r"(?:Taxpayer|Assessee|Legal)\s+Name\s*[:\-]?\s*([A-Za-z\s.'-]{3,60})", line, re.IGNORECASE)
        if m_taxpayer:
            clean = sanitize_visual_name(m_taxpayer.group(1))
            if is_valid_name(clean):
                return clean

    # 2. Check for welcome banner
    for line in lines:
        m_welcome = re.search(r"(?:Welcome(?:\s+Back)?|Hello)[,\s]+([A-Za-z\s.'-]{3,60})", line, re.IGNORECASE)
        if m_welcome:
            clean = sanitize_visual_name(m_welcome.group(1))
            if is_valid_name(clean):
                return clean

    # 3. Check for '<Name> (<PAN>)' or '<Name> [PAN]' (Standard Portal Header Profile Pill)
    for line in lines:
        m_pan = re.search(r"([A-Za-z\s.'-]{3,60})\s*[\(\[]\s*[A-Z]{5}[0-9]{4}[A-Z]\s*[\)\]]", line)
        if m_pan:
            clean = sanitize_visual_name(m_pan.group(1))
            if is_valid_name(clean):
                return clean

    # 4. Check for Taxpayer Profile Dropdown Button in Portal Header
    # Matches patterns like 'AMEJUDDIN SEKH v Individual' or adjacent lines
    for idx, line in enumerate(lines):
        m_profile = re.search(
            r"\b([A-Za-z][A-Za-z\s.'-]{2,50})\s*(?:[v▼▽⌵^|]|expand_more)?\s*(?:Individual|Taxpayer|HUF|Company|Proprietor|Director|Partner)\b",
            line,
            re.IGNORECASE,
        )
        if m_profile:
            raw_cand = m_profile.group(1).strip()
            clean = sanitize_visual_name(raw_cand)
            words = clean.split()
            while words and (words[0] in NOISE_WORDS or len(words[0]) <= 1):
                words.pop(0)
            while words and (words[-1] in NOISE_WORDS or len(words[-1]) <= 1):
                words.pop(-1)
            candidate = " ".join(words)
            if is_valid_name(candidate) and candidate not in NOISE_WORDS and len(words) >= 2:
                return candidate

        # Check adjacent line: line[idx] is Name and line[idx+1] is 'Individual' / role
        if idx + 1 < len(lines):
            next_line = lines[idx + 1].strip()
            if re.match(r"^(?:[v▼▽⌵^|]|expand_more)?\s*(?:Individual|Taxpayer|HUF|Company|Proprietor|Director|Partner)\b", next_line, re.IGNORECASE):
                clean = sanitize_visual_name(line)
                words = clean.split()
                while words and (words[0] in NOISE_WORDS or len(words[0]) <= 1):
                    words.pop(0)
                while words and (words[-1] in NOISE_WORDS or len(words[-1]) <= 1):
                    words.pop(-1)
                candidate = " ".join(words)
                if is_valid_name(candidate) and candidate not in NOISE_WORDS and len(words) >= 2:
                    return candidate

    # 5. ALL-CAPS Run Candidate Detection (NLP-Free Entity Isolation)
    full_block = " \n ".join(lines)
    for candidate in caps_run_name_candidates(full_block):
        if is_valid_name(candidate) and candidate not in NOISE_WORDS:
            return candidate

    # 6. spaCy Local NLP Named Entity Recognition (PERSON label detection)
    for candidate in extract_spacy_person_names(full_block):
        if is_valid_name(candidate) and candidate not in NOISE_WORDS:
            return candidate

    # 7. Standalone name line from portal header or profile button
    for line in lines:
        cleaned = sanitize_visual_name(line)
        if not is_valid_name(cleaned):
            continue
        words = cleaned.split()
        # Plausible human/firm name: 2 to 5 words
        if len(words) < 2 or len(words) > 5:
            continue
        # Ensure none of the constituent words are navigation/portal noise words
        if any(w in NOISE_WORDS for w in words):
            continue
        return cleaned

    return None
