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
    "ASSESSMENT", "YEAR", "FINANCIAL", "PERIOD", "QUARTER", "MONTH", "MONTHLY", "QUARTERLY", "MODE",
    "FILING", "FIIING", "EFIIING", "E-FIIING", "FIILING", "FILNG", "E-FILNG",
    "CALL US", "ENGLISH", "HELP", "FEEDBACK", "NOTIFICATIONS", "BROWSER", "SUPPORT", "BROWSER SUPPORT", "SITE MAP", "MAP",
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
    "MOBILE", "EMAIL", "PREVIOUS",
    # GST portal specific navigation & statutory headers
    "GOODS", "SERVICE", "SERVICES", "SER", "GST", "GSTIN", "UIN",
    "TAX", "TAXES", "TAXATION",
    "UNION", "TERRITORIES", "TERRITORY", "STATES", "STATE",
    "FACILITIES", "FACILITY", "COMMON", "PORTAL", "LAW", "DOWNLOADS",
    "INVOICE", "INVOICES", "E-INVOICE", "ADVISORY",
    "SUPPLIES", "SUPPLY", "OUTWARD", "INWARD", "RATED",
    "CREDIT", "DEBIT", "NOTES", "NOTE",
    "REGISTERED", "UNREGISTERED", "LIABILITY", "ADVANCES",
    "RECEIVED", "NIL", "RECORDS", "RECORD", "APPLY", "APPLICATION",
    "CENTRAL", "BOARD", "INDIRECT", "CUSTOMS", "CBIC",
    # Browser & UI artifacts
    "ASK", "GEMINI", "CHROME", "EDGE", "BRAVE", "FIREFOX", "BROWSER",
    "NEW", "TAB", "USE", "GCK", "DS",
    # Captcha & Form input placeholders
    "ENTER", "CHARACTERS", "SHOWN", "BELOW", "CAPTCHA", "REFRESH", "CODE",
    # Portal navigational footer / header boilerplate noise
    "WEBSITE", "POLICIES", "POLICY", "ACCESSIBILITY", "STATEMENT", "STATEMENTS",
    "EXTRACTING", "CLIENT", "HYPERLINK", "DISCLAIMER", "COPYRIGHT", "TERMS",
    "CONDITIONS", "PRIVACY", "HELPDESK", "CONTACT", "SITEMAP", "GUIDELINES",
    "VERSION", "PORTAL", "NATIONAL",
    # Portal dialog, form guidance, and mode indicators
    "INFORMATION", "DIRECTED", "APPLICABLE", "LATER", "RECOMMENDED",
    "OFFLINE", "ONLINE", "PREPARED", "UTILITY", "UPLOAD"
}

# Categorically rejected boilerplate phrases from portal headers and footers
REJECTED_PHRASES = [
    "WEBSITE POLICIES",
    "ACCESSIBILITY STATEMENT",
    "EXTRACTING CLIENT NAME",
    "EXTRACTING CLIENT",
    "HYPERLINK POLICY",
    "TERMS OF USE",
    "TERMS AND CONDITIONS",
    "TERMS & CONDITIONS",
    "DISCLAIMER",
    "COPYRIGHT",
    "PRIVACY POLICY",
    "HELP DESK",
    "CONTACT US",
    "SITE MAP",
    "BROWSER SUPPORT",
    "BROWSER",
    "IBROWSER",
    "SUPPORT",
    "MAP BROWSER",
    "MAP IBROWSER",
    "SKIP TO MAIN",
    "SKIP MAIN CONTENT",
    "NATIONAL PORTAL",
    "GOVERNMENT OF INDIA",
    "INCOME TAX DEPARTMENT",
]


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
    clean = re.sub(r"\s*(?:\u2026|\.{2,})\s*", " ", clean)

    # 6. Keep only valid name characters
    clean = re.sub(r"[^A-Za-z\s.'-]", " ", clean)

    # 7. Normalize whitespace and strip trailing non-letter symbols
    clean = re.sub(r"\s+", " ", clean).strip().upper()
    clean = re.sub(r"[\s.'-]+$", "", clean).strip()
    return clean


SAFE_CONNECTORS = {"AND", "OF", "THE", "&"}


def is_valid_name(name: str) -> bool:
    """
    Validates whether a candidate string is a plausible person or firm name.
    Ensures:
    - 3 to 70 characters
    - Must contain at least one vowel
    - No constituent word may be in NOISE_WORDS (unless it's a safe connector in a 3+ word firm name or single-letter initial)
    - Rejects portal header/footer boilerplate phrases
    """
    if not name or len(name) < 3 or len(name) > 70:
        return False
    upper_name = name.upper()
    if upper_name in NOISE_WORDS:
        return False
    # Categorically reject portal header/footer boilerplate phrases
    for phrase in REJECTED_PHRASES:
        if phrase in upper_name:
            return False
    words = upper_name.split()
    if not words:
        return False

    for i, w in enumerate(words):
        clean_w = re.sub(r"[^A-Z]", "", w)
        if not clean_w:
            continue
        if len(clean_w) == 1:
            # Single letter initial (e.g. 'A.', 'K.') is allowed
            continue
        if clean_w in SAFE_CONNECTORS:
            # Allowed as interior word in >= 3 word name (e.g. 'SEN AND SONS')
            if 0 < i < len(words) - 1 and len(words) >= 3:
                continue
            return False
        if clean_w in NOISE_WORDS:
            return False

    # Any solitary presence of key boilerplate words is disqualifying
    if any(w in ("WEBSITE", "POLICIES", "ACCESSIBILITY", "STATEMENT", "EXTRACTING", "HYPERLINK", "DISCLAIMER", "BROWSER", "IBROWSER", "SUPPORT", "MAP") for w in words):
        return False
    if re.search(r"\b(?:BROWSER(?:\s*SUPPORT)?|IBROWSER|SUPPORT|SITE\s*MAP|MAP\s+I?BROWSER)\b", upper_name):
        return False
    # Must contain at least one vowel
    if not re.search(r"[AEIOUY]", upper_name):
        return False
    # Must match name character set
    return bool(re.match(r"^[A-Z\s.'-]{3,70}$", upper_name))


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

    # 0. Noise purge: If existing name contains any noise word or rejected phrase and new candidate is clean, new candidate wins!
    e_has_noise = any(w in NOISE_WORDS for w in e_words) or any(p in e_clean for p in REJECTED_PHRASES)
    n_has_noise = any(w in NOISE_WORDS for w in n_words) or any(p in n_clean for p in REJECTED_PHRASES)
    if e_has_noise and not n_has_noise:
        return True
    if n_has_noise and not e_has_noise:
        return False

    # 0b. Trailing PAN fragment purge:
    # If existing name has a trailing word that is a 5-letter PAN prefix (e.g. 'PINKI ROY AHJPR')
    # and incoming name is the clean sub-phrase (e.g. 'PINKI ROY'), incoming candidate wins!
    if len(e_words) >= 3 and len(n_words) == len(e_words) - 1:
        if e_words[:-1] == n_words and len(e_words[-1]) == 5:
            return True

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
    # Pre-clean known PAN patterns so they don't break or contaminate all-caps runs
    cleaned_text = re.sub(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", " ", text)
    for match in re.finditer(r"\b[A-Z]{2,}(?:\s+[A-Z]{2,}){1,4}\b", cleaned_text):
        # Skip a run immediately followed by an ellipsis - VSDC-X reads the
        # whole page regardless of on-screen crop, so a header pill visually
        # truncated to fit a fixed-width badge (e.g. "INDRAJIT CHATTE...")
        # would otherwise be treated as if it were the complete name, ahead
        # of a fuller, untruncated version that's very often present
        # elsewhere on the same page (a profile card, a form field).
        trailing = cleaned_text[match.end():match.end() + 3]
        if trailing.startswith("...") or trailing.startswith("…"):
            continue
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


PROXIMITY_LABEL_PATTERNS = [
    ("legal_name", re.compile(
        r"\bLegal\s+Name(?:\s+of(?:\s+the)?\s+(?:Taxpayer|Business))?(?:\s*\(?\s*as\s*per\s*PAN\s*\)?)?",
        re.IGNORECASE,
    )),
    ("trade_name", re.compile(
        r"\bTrade\s+Name(?:\s+of(?:\s+the)?\s+Business)?",
        re.IGNORECASE,
    )),
    ("pan_name", re.compile(
        r"\b(?:Full\s+)?Name\s*(?:\(?\s*as\s*per\s*(?:PAN|Aadhaar)\s*\)?)",
        re.IGNORECASE,
    )),
    ("taxpayer_name", re.compile(
        r"\b(?:Taxpayer(?:'s)?|Assessee(?:'s)?|(?:Name\s+of\s+)?Proprietor(?:\s+Name)?|Full)\s+Name\b",
        re.IGNORECASE,
    )),
]


def extract_proximity_labeled_names(lines: List[str]) -> Dict[str, str]:
    """
    Scans OCR lines for statutory name labels using proximity scanning:
    - Inline with separator: 'Legal Name of Taxpayer : RAHUL MONDAL'
    - Inline direct ALL-CAPS: 'Legal Name of Taxpayer RAHUL MONDAL'
    - Adjacent lines: Line N: 'Legal Name of Taxpayer', Line N+1: 'RAHUL MONDAL'

    Returns a dict mapping label key to extracted name:
    {
        'legal_name': '...',
        'trade_name': '...',
        'pan_name': '...',
        'taxpayer_name': '...'
    }
    """
    results: Dict[str, str] = {}
    if not lines:
        return results

    def _extract_name_candidate(raw: str) -> Optional[str]:
        if not raw:
            return None
        cleaned = sanitize_visual_name(raw)
        # Strip PAN suffix if present
        cleaned = re.sub(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", "", cleaned).strip()
        words = cleaned.split()
        while words and (words[0] in NOISE_WORDS or (len(words[0]) <= 1 and not words[0].endswith('.'))):
            words.pop(0)
        while words and (words[-1] in NOISE_WORDS or (len(words[-1]) <= 1 and not words[-1].endswith('.'))):
            words.pop(-1)
        if not words:
            return None
        # Indian taxpayer names are typically 2 to 4 words long
        if 2 <= len(words) <= 4:
            cand = " ".join(words)
            if is_valid_name(cand):
                return cand
        # Also check all-caps sub-run of 2-3 words
        m_run = re.search(r"\b([A-Z]{2,}(?:\s+[A-Z]{2,}){1,2})\b", cleaned)
        if m_run:
            cand = m_run.group(1).strip()
            if is_valid_name(cand):
                return cand
        return None

    for idx, line in enumerate(lines):
        line_str = line.strip()
        if not line_str:
            continue

        for key, pattern in PROXIMITY_LABEL_PATTERNS:
            if key in results:
                continue

            m_match = pattern.search(line_str)
            if not m_match:
                continue

            # Case 1: Inline separator (colon, dash, tab, double-space)
            m_sep = re.search(
                pattern.pattern + r"\s*[:\-\t]\s*([A-Za-z0-9\s.'&-]{2,60})",
                line_str,
                re.IGNORECASE,
            )
            if m_sep:
                cand = _extract_name_candidate(m_sep.group(1))
                if cand:
                    results[key] = cand
                    continue

            # Case 2: Inline direct without colon (label followed by 2-4 ALL CAPS words)
            m_direct = re.search(
                pattern.pattern + r"\s+([A-Z]{2,}(?:\s+[A-Z]{2,}){1,3})\b",
                line_str,
            )
            if m_direct:
                cand = _extract_name_candidate(m_direct.group(1))
                if cand:
                    results[key] = cand
                    continue

            # Case 3: Label on Line N, Name on Line N+1 (or N+2 if N+1 is solitary separator)
            if re.match(r"^\s*" + pattern.pattern + r"\s*[:\-]?\s*$", line_str, re.IGNORECASE):
                next_idx = idx + 1
                if next_idx < len(lines) and lines[next_idx].strip() in (":", "-", ":-"):
                    next_idx += 1
                if next_idx < len(lines):
                    cand = _extract_name_candidate(lines[next_idx])
                    if cand:
                        results[key] = cand
                        continue

    return results


HEADER_ROLES = (
    r"(?:Individual|Taxpayer|HUFs?|Company|Representative|Director|Partners?|Proprietor)"
)
HEADER_ARROWS = r"(?:[v▼▽⌵˅^|]|expand_more|keyboard_arrow_down)"


def extract_header_profile_caps_name(lines: List[str]) -> Optional[str]:
    """
    Extracts taxpayer name from the top navbar / header profile pill.
    Based on the insights:
    1. Names are written in ALL CAPS.
    2. Names are usually 2 to 3 words long (up to 4 for compound names).
    3. Positioned immediately preceding a dropdown indicator (v, ▼, expand_more)
       and/or role badge (Individual, Taxpayer, HUF, etc.).
    4. Preceded by navbar utility items (Call Us, English, A- A A+, user icon)
       which must be cleanly excluded.
    """
    if not lines:
        return None

    def _extract_trailing_name_words(text_before: str) -> Optional[str]:
        if not text_before:
            return None
        # A header pill visually truncated with an ellipsis (e.g. a name too
        # long for a fixed-width nav badge, rendered "INDRAJIT CHATTE...")
        # must NOT be trusted as a complete name - VSDC-X reads the entire
        # page's accessible text regardless of on-screen crop, so a fuller,
        # untruncated version of the same name is very often present
        # elsewhere on the page (a profile card, a form field) and should win
        # instead. Returning None here lets extract_name_from_ocr_lines fall
        # through to its later, more thorough checks rather than confidently
        # reporting a truncated fragment as the taxpayer's name.
        if re.search(r"\.\.\.|…", text_before):
            return None
        # Strip any bracketed or unbracketed PAN
        cleaned = re.sub(r"[(\[{<]?\s*[A-Z]{5}[0-9]{4}[A-Z]\s*[)\]}>]?", " ", text_before)
        # Strip trailing arrows, icons, colons, vertical bars, bullet points
        cleaned = re.sub(
            r"[\s:|\-•/]*(?:" + HEADER_ARROWS + r"|account_circle|person|user)*[\s:|\-•/]*$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        # Clean trailing non-letter/digit symbols
        cleaned = cleaned.rstrip(" ([{<:-/|#•")
        if not cleaned:
            return None

        # Split into tokens
        tokens = cleaned.split()
        if not tokens:
            return None

        # Walk backwards from right to left, collecting 2 to 4 consecutive valid name words
        collected = []
        for token in reversed(tokens):
            t_upper = token.upper()
            t_clean = re.sub(r"[^A-Z]", "", t_upper)
            if not t_clean:
                break
            if t_clean in NOISE_WORDS:
                if t_clean in SAFE_CONNECTORS and len(collected) >= 1:
                    collected.append(t_upper)
                    continue
                break
            if len(t_clean) < 2 and not token.endswith('.'):
                break
            collected.append(t_upper)
            if len(collected) == 4:
                break

        # A name cannot lead with a connector (e.g. 'AND SONS')
        while collected and re.sub(r"[^A-Z]", "", collected[-1].upper()) in SAFE_CONNECTORS:
            collected.pop()

        if len(collected) in (2, 3, 4):
            candidate = " ".join(reversed(collected))
            candidate_sanitized = sanitize_visual_name(candidate)
            if is_valid_name(candidate_sanitized):
                return candidate_sanitized

        return None

    for idx, line in enumerate(lines):
        line_str = line.strip()
        if not line_str:
            continue

        # Case 1: Same line with role badge: '... RAHUL MONDAL v Individual' or '... RAHUL MONDAL Individual'
        # Iterate matches from right to left
        role_matches = list(re.finditer(r"\b" + HEADER_ROLES + r"\b", line_str, re.IGNORECASE))
        for m_role in reversed(role_matches):
            text_before = line_str[:m_role.start()].strip()
            cand = _extract_trailing_name_words(text_before)
            if cand:
                return cand

        # Case 2: Adjacent lines: Line idx has '... RAHUL MONDAL v', Line idx+1 has 'Individual'
        if idx + 1 < len(lines):
            next_line = lines[idx + 1].strip()
            if re.match(r"^(?:" + HEADER_ARROWS + r"\s*)?" + HEADER_ROLES + r"\b", next_line, re.IGNORECASE):
                cand = _extract_trailing_name_words(line_str)
                if cand:
                    return cand

        # Case 3: Line ending with dropdown arrow: '... RAHUL MONDAL v' or '... RAHUL MONDAL ▼'
        m_arrow = re.search(r"(?:\s+" + HEADER_ARROWS + r"|\s*▼|\s*▽|\s*⌵|\s*˅|\s*expand_more)\s*$", line_str, re.IGNORECASE)
        if m_arrow:
            text_before = line_str[:m_arrow.start()].strip()
            cand = _extract_trailing_name_words(text_before)
            if cand:
                return cand

        # Case 4: Line with co-located PAN: '... RAHUL MONDAL (AHJPR0846B)' or '... RAHUL MONDAL AHJPR0846B'
        m_pan = re.search(r"\b([A-Z]{5}[0-9]{4}[A-Z])\b", line_str)
        if m_pan:
            text_before = line_str[:m_pan.start()].strip()
            cand = _extract_trailing_name_words(text_before)
            if cand:
                return cand

    return None


def extract_profile_name_field(lines: List[str]) -> Optional[str]:
    """
    Reads the taxpayer's full name from a bare "Name" label followed by its value
    (e.g. the My Profile page's "Name" / "RAHUL MONDAL" pair). Only meant for exact
    UI Automation lines: that value is the authoritative full name, whereas the
    header profile pill can be shortened or truncated, and every other tier in
    extract_name_from_ocr_lines would let a partial pill name win over it.
    """
    for idx, line in enumerate(lines[:-1]):
        if not re.fullmatch(r"(?:Full\s+)?Name\s*:?", line.strip(), re.IGNORECASE):
            continue
        cand = sanitize_visual_name(lines[idx + 1])
        words = cand.split()
        if len(words) >= 2 and is_valid_name(cand) and not any(w in NOISE_WORDS for w in words):
            return cand
    return None


_UI_CONTROL_LINE_RE = re.compile(
    r"\b(?:input\s+field|search\s+box|text\s*box|edit\s+box|combo\s*box|check\s*box|radio\s+button|"
    r"drop\s*-?\s*down|scroll\s*bar|placeholder)\b"
    r"|^(?:e[\s-]?verify|discard|search|showing\b.*|please\s+select\b.*|current\s+step\b.*|"
    r"unvisited\s+step\b.*|filed\s+on|filing\s+type|applicable\s+act|original|revised|belated|updated)\s*:?$",
    re.IGNORECASE)


# A PAN (5 letters, 4 digits, 1 letter) or a GSTIN.
_IDENTIFIER_TOKEN_RE = re.compile(
    r"\b(?:[A-Z]{5}[0-9]{4}[A-Z]|[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][A-Z0-9]Z[A-Z0-9])\b")


def extract_name_from_ocr_lines(lines: List[str]) -> Optional[str]:
    """
    Scans a list of text lines for taxpayer name patterns:
    - Composite form fields (First Name, Middle Name, Last Name / Surname)
    - Proximity labeled statutory names (Legal Name, Name as per PAN, Taxpayer Name, Trade Name)
    - Header profile pill / dropdown button (2-3 ALL CAPS words before arrow/role)
    - Welcome banner: 'Welcome, <Name>'
    - Header profile badge '<Name> (<PAN>)'
    - Header profile dropdown button with role indicator
    """
    if not lines:
        return None

    # UI Automation reports controls by what they ARE ("Search Box Input Field"), and the
    # end-trimming below strips the noise words off such a line and leaves "BOX INPUT" - a
    # wrong client name that would then be stored on a capture. A line that describes a
    # control is never a name.
    lines = [ln for ln in lines if not _UI_CONTROL_LINE_RE.search(ln or "")]
    if not lines:
        return None

    # 0. Check composite form fields first (solves missing last name across separate fields)
    composite = extract_composite_form_name(lines)
    if composite:
        return composite

    # 1. Check Proximity Labeled Statutory Names (Legal Name, Name as per PAN, Taxpayer Name, Trade Name)
    labeled = extract_proximity_labeled_names(lines)
    if labeled:
        for key in ("legal_name", "pan_name", "taxpayer_name", "trade_name"):
            if key in labeled and labeled[key]:
                return labeled[key]

    # 2. Check Header Profile Pill / Dropdown Button (2-3 ALL CAPS words before arrow / role badge)
    header_name = extract_header_profile_caps_name(lines)
    if header_name:
        return header_name

    # 3. Check for welcome banner
    for line in lines:
        m_welcome = re.search(r"(?:Welcome(?:\s+Back)?|Hello)[,\s]+([A-Za-z\s.'-]{3,60})", line, re.IGNORECASE)
        if m_welcome:
            clean = sanitize_visual_name(m_welcome.group(1))
            if is_valid_name(clean):
                return clean

    # 3. Check for co-located PAN on same line or adjacent lines (Standard Portal Header Profile Pill)
    # Matches '<Name> (<PAN>)', '<Name> [PAN]', '<Name> - PAN', '<Name> PAN', '<Name> / PAN', etc.
    for idx, line in enumerate(lines):
        # (a) Co-located on same line: find 10-char PAN
        m_pan = re.search(r"\b([A-Z]{5}[0-9]{4}[A-Z])\b", line)
        if m_pan:
            prefix = line[:m_pan.start()].rstrip(" ([{<:-/|#")
            clean_pfx = sanitize_visual_name(prefix)
            words = clean_pfx.split()
            while words and (words[0] in NOISE_WORDS or len(words[0]) <= 1):
                words.pop(0)
            while words and (words[-1] in NOISE_WORDS or len(words[-1]) <= 1):
                words.pop(-1)
            candidate = " ".join(words)
            if len(words) >= 2 and is_valid_name(candidate) and candidate not in NOISE_WORDS:
                return candidate

        # (b) PAN on adjacent line: line[idx] is Name, line[idx+1] is PAN
        if idx + 1 < len(lines):
            next_line = lines[idx + 1].strip()
            if re.search(r"^[(\[]?\s*[A-Z]{5}[0-9]{4}[A-Z]\s*[)\]]?$", next_line):
                clean = sanitize_visual_name(line)
                words = clean.split()
                while words and (words[0] in NOISE_WORDS or len(words[0]) <= 1):
                    words.pop(0)
                while words and (words[-1] in NOISE_WORDS or len(words[-1]) <= 1):
                    words.pop(-1)
                candidate = " ".join(words)
                if len(words) >= 2 and is_valid_name(candidate) and candidate not in NOISE_WORDS:
                    return candidate

    # 4. Check for Taxpayer Profile Dropdown Button in Portal Header
    # Matches patterns like 'AMEJUDDIN SEKH v Individual', 'AMEJUDDIN SEKH Individual', or adjacent lines
    for idx, line in enumerate(lines):
        m_profile = re.search(
            r"\b([A-Za-z][A-Za-z\s.'-]{2,50})\s*(?:[v▼▽⌵^|]|expand_more|keyboard_arrow_down)?\s*(?:Individual|Taxpayer|HUF|Company|Proprietor|Director|Partner)\b",
            line,
            re.IGNORECASE,
        )
        if m_profile:
            raw_cand = m_profile.group(1).strip()
            # Strip any co-located PAN (must have 4 digits)
            raw_cand = re.sub(r"\b[A-Z]{5}[0-9]{4}[A-Z]?\b\s*$", "", raw_cand).strip()
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
            if re.match(r"^(?:[v▼▽⌵^|]|expand_more|keyboard_arrow_down)?\s*(?:Individual|Taxpayer|HUF|Company|Proprietor|Director|Partner)\b", next_line, re.IGNORECASE):
                # Strip any co-located PAN (must have 4 digits)
                line_no_pan = re.sub(r"\b[A-Z]{5}[0-9]{4}[A-Z]?\b", "", line).strip()
                clean = sanitize_visual_name(line_no_pan)
                words = clean.split()
                while words and (words[0] in NOISE_WORDS or len(words[0]) <= 1):
                    words.pop(0)
                while words and (words[-1] in NOISE_WORDS or len(words[-1]) <= 1):
                    words.pop(-1)
                candidate = " ".join(words)
                if is_valid_name(candidate) and candidate not in NOISE_WORDS and len(words) >= 2:
                    return candidate

    # The stages below guess a name from loose text. An identifier is never a name, but once its
    # digits are stripped a PAN's letters look like one ("ABCPD5678E" -> "ABCPD E"), so PANs and
    # GSTINs are taken out of what they see. (Stages 3-4 above USE a PAN, as the anchor next to a
    # real name, and have already run.)
    lines = [ln for ln in lines if not _IDENTIFIER_TOKEN_RE.search(ln or "")]
    if not lines:
        return None

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


def extract_gst_welcome_name(text: str) -> Optional[str]:
    """
    Extracts the taxpayer legal/trade name from the GST welcome page.
    1. Primary: 'Welcome <NAME> to GST Common Portal'
    2. Secondary: Name preceding GSTIN on the right-side profile card.
    """
    if not text:
        return None
    m = re.search(r"Welcome\s+([A-Za-z0-9\s.,'&-]{3,60})\s+to\s+GST\s+Common\s+Portal", text, re.IGNORECASE)
    if m:
        raw_cand = m.group(1).strip()
        cleaned = sanitize_visual_name(raw_cand)
        words = cleaned.split()
        while words and (words[0] in NOISE_WORDS or len(words[0]) <= 1):
            words.pop(0)
        while words and (words[-1] in NOISE_WORDS or len(words[-1]) <= 1):
            words.pop(-1)
        candidate = " ".join(words)
        if candidate and len(candidate.split()) >= 1 and candidate not in NOISE_WORDS:
            return candidate

    # Secondary: Name line right before 15-char GSTIN
    m_gstin_block = re.search(r"\b([A-Z0-9\s.,'&-]{3,50})\n\s*(?:[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z])", text)
    if m_gstin_block:
        cleaned = sanitize_visual_name(m_gstin_block.group(1).strip())
        words = cleaned.split()
        while words and (words[0] in NOISE_WORDS or len(words[0]) <= 1):
            words.pop(0)
        while words and (words[-1] in NOISE_WORDS or len(words[-1]) <= 1):
            words.pop(-1)
        candidate = " ".join(words)
        if candidate and len(candidate.split()) >= 1 and candidate not in NOISE_WORDS and not any(w in NOISE_WORDS for w in candidate.split()):
            return candidate

    # Tertiary: Navbar user profile dropdown badge (e.g. 'JABED ALI v 19BNN...HIZX' or 'JABED ALI ˅')
    m_badge = re.search(
        r"(?:Ask\s*Gemini\s*[a-z0-9]?\s*)?\b([A-Za-z\s.'-]{3,40})\s*(?:[v▼▽⌵^|]|expand_more)\s*(?:[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z])?",
        text,
        re.IGNORECASE,
    )
    if m_badge:
        cand = sanitize_visual_name(m_badge.group(1).strip())
        words = cand.split()
        while words and (words[0] in NOISE_WORDS or len(words[0]) <= 1):
            words.pop(0)
        while words and (words[-1] in NOISE_WORDS or len(words[-1]) <= 1):
            words.pop(-1)
        candidate = " ".join(words)
        if candidate and is_valid_name(candidate) and not any(w in NOISE_WORDS for w in candidate.split()):
            return candidate

    return None

