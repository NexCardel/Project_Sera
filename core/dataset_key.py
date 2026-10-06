"""
core/dataset_key.py - the one canonical key for a tracker dataset
=================================================================
PORTAL:IDENTIFIER:FORM:PERIOD[:PREFERENCE], e.g. GST:19BHPPM3529R1ZB:GSTR1:AY_2026_27_MAY, or
ITR:ABCPD1234E:ITR4:AY_2026_27:REVISED (the filing preference is added unless it is Original or absent). The tracker
keeps one row per key (database.insert_tracker_dump), so every engine that wants its rows to
merge with the others' must build the key with this function - the database (for VSDC and
the extension) and SGT in live mode both do. Pure: depends on nothing but `re`.
"""

import re

# SDIS carrier rows (blueprint S.4): a session's Others / Profile builder values with no dataset
# row to ride on. They are not datasets: every tracker view, counter and status resolver skips them.
SDIS_INFO_METHOD = "SGT_sdis_info"
NOT_CARRIER_SQL = "(capture_method IS NULL OR capture_method != 'SGT_sdis_info')"


def compute_dataset_key(portal: str, identifier: str, form_type: str, period_label: str,
                        preference: str = "") -> str:
    """Generates a canonical, deterministic dataset key for instant O(1) deduplication & promotion:
       Format: PORTAL:IDENTIFIER:FORM:PERIOD
       e.g. GST:19BHPPM3529R1ZB:GSTR1:MAY_2026
    """
    p_str = str(portal or "").strip()
    if re.search(r"gst", p_str, re.I):
        p_canon = "GST"
    elif re.search(r"itr|income", p_str, re.I):
        p_canon = "ITR"
    else:
        p_canon = re.sub(r"[^A-Z0-9]", "", p_str.upper()) or "PORTAL"

    id_str = re.sub(r"[^A-Z0-9]", "", str(identifier or "").strip().upper()) or "UNKNOWN"

    f_str = str(form_type or "").strip()
    if not f_str and "(" in p_str and ")" in p_str:
        f_str = p_str.split("(")[-1].split(")")[0].strip()
    if re.search(r"\bGSTR[-_ ]*1A\b", f_str, re.I):
        f_canon = "GSTR1A"
    elif re.search(r"\bGSTR[-_ ]*1(?:\s*/\s*IFF)?\b", f_str, re.I) or f_str.upper() == "IFF":
        f_canon = "GSTR1"
    elif re.search(r"\bGSTR[-_ ]*2A\b", f_str, re.I):
        f_canon = "GSTR2A"
    elif re.search(r"\bGSTR[-_ ]*2B\b", f_str, re.I):
        f_canon = "GSTR2B"
    elif re.search(r"\bGSTR[-_ ]*3B\b", f_str, re.I):
        f_canon = "GSTR3B"
    elif re.search(r"\bCMP[-_ ]*08\b", f_str, re.I):
        f_canon = "CMP08"
    elif re.search(r"\bGSTR[-_ ]*4\b", f_str, re.I):
        f_canon = "GSTR4"
    elif re.search(r"\bGSTR[-_ ]*9C\b", f_str, re.I):
        f_canon = "GSTR9C"
    elif re.search(r"\bGSTR[-_ ]*9\b", f_str, re.I):
        f_canon = "GSTR9"
    elif re.search(r"\bGSTR[-_ ]*7\b", f_str, re.I):
        f_canon = "GSTR7"
    elif re.search(r"\bGSTR[-_ ]*8\b", f_str, re.I):
        f_canon = "GSTR8"
    else:
        m_itr = re.search(r"\bITR[-_ ]*([1-7])\b", f_str, re.I)
        if m_itr:
            f_canon = f"ITR{m_itr.group(1)}"
        else:
            f_canon = re.sub(r"[^A-Z0-9]", "", f_str.upper()) or "FORM"

    # Canonical period: clean trailing text, status lines, due dates, newlines
    per_str = str(period_label or "").strip()
    per_clean = re.split(r"[\r\n]|(?:\b(?:Due date|Option|Filed|Pending|NA)\b)", per_str, flags=re.I)[0].strip()

    # Assessment Year canonical normalization: (e.g. "AY 2026-27", "2026-27", "AY: 2026-27") -> AY_2026_27
    m_ay = re.search(r"\b(?:AY|A\.Y\.)?\s*(20\d{2})[-_](\d{2})\b", per_clean, re.I)
    if m_ay:
        # GST labels commonly contain both the FY and tax period, such as
        # "May (FY 2026-27)". Preserve both so months cannot overwrite one
        # another under the same financial year.
        m_tax_mon = re.search(r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b", per_clean, re.I)
        m_tax_qtr = re.search(r"\b(Apr[- ]*Jun|Jul[- ]*Sep|Oct[- ]*Dec|Jan[- ]*Mar|Q[1-4])\b", per_clean, re.I)
        tax_period = m_tax_mon.group(1)[:3].upper() if m_tax_mon else (re.sub(r"[^A-Z0-9]+", "_", m_tax_qtr.group(1).upper()) if m_tax_qtr else "")
        per_canon = f"AY_{m_ay.group(1)}_{m_ay.group(2)}{('_' + tax_period) if tax_period else ''}"
    else:
        # Month or quarter extraction with year
        m_mon = re.search(r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b", per_clean, re.I)
        m_qtr = re.search(r"\b(Apr[- ]*Jun|Jul[- ]*Sep|Oct[- ]*Dec|Jan[- ]*Mar|Q[1-4])\b", per_clean, re.I)
        m_yr = re.search(r"\b(20\d{2})\b", per_str)

        if m_qtr and m_yr:
            q_clean = re.sub(r"[^A-Z0-9]+", "_", m_qtr.group(1).upper())
            per_canon = f"{q_clean}_{m_yr.group(1)}"
        elif m_mon and m_yr:
            mon_3 = m_mon.group(1)[:3].upper()
            per_canon = f"{mon_3}_{m_yr.group(1)}"
        elif m_qtr:
            # A quarter with no year in the label: the whole range is the period. Taking its first month
            # would make "Apr-Jun" the same dataset as April.
            per_canon = re.sub(r"[^A-Z0-9]+", "_", m_qtr.group(1).upper())
        elif m_mon:
            mon_3 = m_mon.group(1)[:3].upper()
            per_canon = mon_3
        else:
            per_canon = re.sub(r"[^A-Z0-9]+", "_", per_clean.upper()).strip("_") or "CURRENT"

    key = f"{p_canon}:{id_str}:{f_canon}:{per_canon}"
    # The filing preference (Original / Revised / Belated / Updated) tells two filings of the same form and
    # period apart. Original (or none) adds nothing, so every key made before this stays valid.
    pref = re.sub(r"[^A-Z0-9]", "", str(preference or "").upper())
    if pref and pref != "ORIGINAL":
        key += f":{pref}"
    return key
