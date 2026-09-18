"""
tests/test_vsdc_name.py — Unit Tests for VSDC Visual Name Parsing
"""

import unittest
from core.vsdc.vsdc_name_parser import (
    sanitize_visual_name,
    is_valid_name,
    is_better_taxpayer_name,
    parse_human_name,
    extract_name_from_ocr_lines,
    extract_composite_form_name,
    extract_proximity_labeled_names,
    extract_header_profile_caps_name,
    caps_run_name_candidates,
    extract_spacy_person_names,
    get_spacy_nlp,
)


class TestVSDCNameParser(unittest.TestCase):

    def test_material_icon_ligature_stripping(self):
        # Header user badge often has 'account_circle' and 'expand_more' directly attached
        raw = "account_circle AMAN ASSOCIATES expand_more"
        cleaned = sanitize_visual_name(raw)
        self.assertEqual(cleaned, "AMAN ASSOCIATES")

        # Portal role tags stripped
        raw_role = "JOHN DOE Individual"
        self.assertEqual(sanitize_visual_name(raw_role), "JOHN DOE")

    def test_name_validation(self):
        self.assertTrue(is_valid_name("AMAN ASSOCIATES"))
        self.assertTrue(is_valid_name("INDRAJIT CHATTERJEE"))
        self.assertTrue(is_valid_name("K. VENKATESHWARLU"))

        # Rejects noise words
        self.assertFalse(is_valid_name("DASHBOARD"))
        self.assertFalse(is_valid_name("LOGOUT"))
        self.assertFalse(is_valid_name("WELCOME"))
        self.assertFalse(is_valid_name("SERA ASSIST"))
        self.assertFalse(is_valid_name("SERA"))
        self.assertFalse(is_valid_name("SCC"))
        self.assertFalse(is_valid_name("12345"))
        self.assertFalse(is_valid_name("XZP")) # no vowel

    def test_human_name_parsing(self):
        parsed = parse_human_name("MR AMIT KUMAR SHARMA")
        self.assertEqual(parsed["first_name"], "AMIT")
        self.assertEqual(parsed["middle_name"], "KUMAR")
        self.assertEqual(parsed["last_name"], "SHARMA")
        self.assertEqual(parsed["title"], "MR")

    def test_extract_name_from_ocr_lines(self):
        lines = [
            "e-Filing Home Page",
            "Taxpayer Name: AMAN ASSOCIATES",
            "PAN: AHJPR0846B",
            "Status: Active"
        ]
        name = extract_name_from_ocr_lines(lines)
        self.assertEqual(name, "AMAN ASSOCIATES")

        # Welcome banner test
        lines_welcome = [
            "Welcome Back, DHANANJAY JOSHI",
            "Last Login: 12-Sep-2026"
        ]
        name_w = extract_name_from_ocr_lines(lines_welcome)
        self.assertEqual(name_w, "DHANANJAY JOSHI")

        # Standalone header badge test from live portal navbar
        lines_header = [
            "e-FiIing Income Tax Departnmt, Government Of India",
            "Authorised Partners",
            "Services",
            "Pending Actions",
            "Grievances",
            "Help",
            "WASIL AMAN MANDAL",
            "Individual"
        ]
        name_h = extract_name_from_ocr_lines(lines_header)
        self.assertEqual(name_h, "WASIL AMAN MANDAL")

        # Live portal navbar test with profile dropdown button (e.g. AMEJUDDIN SEKH v Individual)
        lines_live = [
            "e-FiIing",
            "Income Tax Department, Govemment Of India",
            "Services @ English v Call Us v Pending Actions AMEJUDDIN SEKH v Individual",
            "e-File Authorised Partners AIS Grievances Help",
            "Dashboard > e-flle > Income Tax Return > View Filed Returns",
        ]
        name_live = extract_name_from_ocr_lines(lines_live)
        self.assertEqual(name_live, "AMEJUDDIN SEKH")

        # Noise word rejection test: E FIIING must never be extracted as a person name
        lines_noise = [
            "e-FiIing",
            "Call Us",
            "Pending Actions",
        ]
        self.assertIsNone(extract_name_from_ocr_lines(lines_noise))

        # Rejects statutory portal disclaimers and instructions
        lines_disclaimer = [
            "ITR-1 is NOT FOR AN INDIVIDUAL WHO IS EITHER a Director in a company",
            "or has invested in unlisted equity shares"
        ]
        self.assertIsNone(extract_name_from_ocr_lines(lines_disclaimer))

    def test_caps_run_name_candidates(self):
        # Raw text dump containing taxpayer name surrounded by portal headers and buttons
        raw_dump = (
            "INCOME TAX DEPARTMENT SERVICES PENDING ACTIONS AMEJUDDIN SEKH "
            "INDIVIDUAL VIEW FILED RETURNS DOWNLOAD RECEIPT AY 2025-26"
        )
        cands = caps_run_name_candidates(raw_dump)
        self.assertIn("AMEJUDDIN SEKH", cands)
        # Verify portal noise runs are not included as names
        self.assertNotIn("INCOME TAX", cands)
        self.assertNotIn("VIEW FILED RETURNS", cands)
        self.assertNotIn("DOWNLOAD RECEIPT", cands)

    def test_spacy_person_name_extraction(self):
        if not get_spacy_nlp():
            self.skipTest("spaCy / en_core_web_sm not loaded")
        # Mixed-case document / notice text
        doc_text = "Income Tax intimation for Indrajit Chatterjee issued under section 143(1)."
        names = extract_spacy_person_names(doc_text)
        self.assertIn("INDRAJIT CHATTERJEE", names)

    def test_composite_name_extraction(self):
        # Case 1: Separate fields with colons
        lines_colon = [
            "Part A General - Personal Information",
            "First Name : MD SAYID",
            "Middle Name :",
            "Last Name : MOLLA",
            "PAN : GZEPM6417A"
        ]
        self.assertEqual(extract_composite_form_name(lines_colon), "MD SAYID MOLLA")
        self.assertEqual(extract_name_from_ocr_lines(lines_colon), "MD SAYID MOLLA")

        # Case 2: Alternating label and value lines
        lines_alt = [
            "Personal Information",
            "First Name",
            "WASIL",
            "Middle Name",
            "AMAN",
            "Last Name",
            "MANDAL",
            "PAN",
            "GZEPM6367M"
        ]
        self.assertEqual(extract_composite_form_name(lines_alt), "WASIL AMAN MANDAL")
        self.assertEqual(extract_name_from_ocr_lines(lines_alt), "WASIL AMAN MANDAL")

        # Case 3: Single-line multi-column layout
        lines_single = [
            "Profile Details",
            "First Name: MD SAYID   Last Name: MOLLA",
            "Status: Active"
        ]
        self.assertEqual(extract_composite_form_name(lines_single), "MD SAYID MOLLA")
        self.assertEqual(extract_name_from_ocr_lines(lines_single), "MD SAYID MOLLA")

        # Case 4: Surname with colon
        lines_surname = [
            "First Name : MOHAMMED",
            "Surname : GAZI"
        ]
        self.assertEqual(extract_composite_form_name(lines_surname), "MOHAMMED GAZI")
        self.assertEqual(extract_name_from_ocr_lines(lines_surname), "MOHAMMED GAZI")

        # Case 5: Horizontal table headers followed by data row
        lines_table = [
            "First Name    Middle Name    Last Name",
            "WASIL         AMAN           MANDAL"
        ]
        self.assertEqual(extract_composite_form_name(lines_table), "WASIL AMAN MANDAL")
        self.assertEqual(extract_name_from_ocr_lines(lines_table), "WASIL AMAN MANDAL")

        # Case 6: Dropdown adjacent line
        lines_dropdown_adj = [
            "WASIL AMAN MANDAL",
            "Individual"
        ]
        self.assertEqual(extract_name_from_ocr_lines(lines_dropdown_adj), "WASIL AMAN MANDAL")

        # Case 7: Mandatory fields instruction line must NEVER be extracted as name
        lines_mandatory = [
            "* Indicates mandatory fields",
            "Please fill all details"
        ]
        self.assertIsNone(extract_name_from_ocr_lines(lines_mandatory))
        self.assertFalse(is_valid_name("INDICATES MANDATORY FIELDS"))

    def test_assembler_name_subset_downgrade_protection(self):
        from core.vsdc.vsdc_assembler import VisualSessionAssembler
        assembler = VisualSessionAssembler()
        assembler.update_identity(name="WASIL AMAN MANDAL")
        self.assertEqual(assembler.client_name, "WASIL AMAN MANDAL")

        # Partial fragment should NOT overwrite authoritative 3-word name
        assembler.update_identity(name="WASIL MANDAL")
        self.assertEqual(assembler.client_name, "WASIL AMAN MANDAL")

        # Truncated prefix words (e.g. WASIL AMAN MAND.) should NOT overwrite authoritative name
        assembler.update_identity(name="WASIL AMAN MAND")
        self.assertEqual(assembler.client_name, "WASIL AMAN MANDAL")

    def test_personal_info_page_table_extraction(self):
        # Real OCR lines from Part A General - Personal Information screen
        lines = [
            "e-Filing Anywhere Anytime",
            "WASIL AMAN MAND...",
            "Individual",
            "Dashboard > e-file > Income Tax Return > Return Summary > Part A General - Personal Information",
            "Part A General - Personal Information",
            "Profile",
            "First Name Middle Name Last Name",
            "WASIL AMAN MANDAL",
            "PAN Date of Birth / Formation Aadhaar Number",
            "GZEPM6367M"
        ]
        extracted = extract_name_from_ocr_lines(lines)
        self.assertEqual(extracted, "WASIL AMAN MANDAL")

    def test_profile_page_full_name_as_per_pan(self):
        # Real OCR lines from My Profile screen
        lines = [
            "e-Filing Anywhere Anytime",
            "WASIL AMAN MAND...",
            "Individual",
            "My Profile",
            "Personal Details",
            "Full Name as per PAN",
            "WASIL AMAN MANDAL",
            "PAN",
            "GZEPM6367M"
        ]
        extracted = extract_name_from_ocr_lines(lines)
        self.assertEqual(extracted, "WASIL AMAN MANDAL")

    def test_stacked_composite_headers_and_values(self):
        lines = [
            "First Name",
            "Middle Name",
            "Last Name",
            "WASIL",
            "AMAN",
            "MANDAL"
        ]
        extracted = extract_composite_form_name(lines)
        self.assertEqual(extracted, "WASIL AMAN MANDAL")

    def test_sanitize_visual_name_ellipsis_and_multiple_dots(self):
        # Multiple trailing dots (e.g. 's......') and Unicode ellipsis '…'
        self.assertEqual(sanitize_visual_name("RABINDRANATH S......"), "RABINDRANATH S")
        self.assertEqual(sanitize_visual_name("RABINDRANATH S…"), "RABINDRANATH S")
        self.assertEqual(sanitize_visual_name("WASIL AMAN MAND..."), "WASIL AMAN MAND")
        self.assertEqual(sanitize_visual_name("WASIL AMAN MANDAL"), "WASIL AMAN MANDAL")

    def test_is_better_taxpayer_name_equal_word_count_expansion(self):
        # 1. Equal word count: full name expands truncated single-letter initial / prefix
        # "RABINDRANATH SAGORE" (2 words) vs "RABINDRANATH S" (2 words)
        self.assertTrue(is_better_taxpayer_name("RABINDRANATH SAGORE", "RABINDRANATH S"))
        self.assertTrue(is_better_taxpayer_name("RABINDRANATH SAGORE", "RABINDRANATH S......"))

        # 2. Equal word count: full name expands truncated word fragment
        # "WASIL AMAN MANDAL" (3 words) vs "WASIL AMAN MAND" (3 words)
        self.assertTrue(is_better_taxpayer_name("WASIL AMAN MANDAL", "WASIL AMAN MAND"))

        # 3. Word count expansion: 3 words vs 2 words
        self.assertTrue(is_better_taxpayer_name("WASIL AMAN MANDAL", "WASIL MANDAL"))

        # 4. Anti-downgrade: truncated header name cannot overwrite full profile name
        self.assertFalse(is_better_taxpayer_name("RABINDRANATH S", "RABINDRANATH SAGORE"))
        self.assertFalse(is_better_taxpayer_name("RABINDRANATH S......", "RABINDRANATH SAGORE"))
        self.assertFalse(is_better_taxpayer_name("WASIL AMAN MAND", "WASIL AMAN MANDAL"))
        self.assertFalse(is_better_taxpayer_name("WASIL MANDAL", "WASIL AMAN MANDAL"))

        # 5. Identical names do not qualify as 'better'
        self.assertFalse(is_better_taxpayer_name("RABINDRANATH SAGORE", "RABINDRANATH SAGORE"))

    def test_boilerplate_noise_and_phrase_rejection(self):
        # Header/footer navigational text must be categorically rejected
        self.assertFalse(is_valid_name("IWEBSITE POLICIES IACCESSIBILITY STATEMENT"))
        self.assertFalse(is_valid_name("WEBSITE POLICIES"))
        self.assertFalse(is_valid_name("ACCESSIBILITY STATEMENT"))
        self.assertFalse(is_valid_name("LO X EXTRACTING CLIENT NAME"))
        self.assertFalse(is_valid_name("HYPERLINK POLICY"))
        self.assertFalse(is_valid_name("TERMS OF USE"))
        self.assertFalse(is_valid_name("DISCLAIMER"))

        # In a list of OCR lines, boilerplate must never be extracted as name
        lines = [
            "e-Filing Anywhere Anytime",
            "WEBSITE POLICIES | ACCESSIBILITY STATEMENT | HELP | CONTACT US",
            "Dashboard > File Income Tax Return",
            "Assessment Year 2026-27",
            "Select Mode of Filing"
        ]
        self.assertIsNone(extract_name_from_ocr_lines(lines))

    def test_profile_labels_various_formats(self):
        # Format 1: "Name (as per PAN)"
        lines1 = [
            "Personal Details",
            "Name (as per PAN)",
            "WASIL AMAN MANDAL",
            "PAN : GZEPM6367M"
        ]
        self.assertEqual(extract_name_from_ocr_lines(lines1), "WASIL AMAN MANDAL")

        # Format 2: "Name as per PAN : WASIL AMAN MANDAL"
        lines2 = [
            "Personal Details",
            "Name as per PAN : WASIL AMAN MANDAL",
            "PAN : GZEPM6367M"
        ]
        self.assertEqual(extract_name_from_ocr_lines(lines2), "WASIL AMAN MANDAL")

        # Format 3: "Taxpayer's Name"
        lines3 = [
            "Taxpayer's Name : WASIL AMAN MANDAL",
            "PAN : GZEPM6367M"
        ]
        self.assertEqual(extract_name_from_ocr_lines(lines3), "WASIL AMAN MANDAL")

    def test_profile_name_authoritative_overwrites_noise(self):
        # Even if a noise candidate had 4 words, a valid clean name must replace it
        self.assertTrue(is_better_taxpayer_name("WASIL AMAN MANDAL", "IWEBSITE POLICIES IACCESSIBILITY STATEMENT"))
        self.assertTrue(is_better_taxpayer_name("WASIL MANDAL", "IWEBSITE POLICIES IACCESSIBILITY STATEMENT"))
        self.assertTrue(is_better_taxpayer_name("MOHD MARUF GAZI", "LO X EXTRACTING CLIENT NAME"))

    def test_header_pill_name_extraction_formats(self):
        """
        Verifies header pill name extraction across all real-world ITR portal formats:
        - Name with bracketed PAN: RAMESH SHARMA (ABCPE1234F)
        - Name with unbracketed PAN: PINKI ROY AHJPR0846B
        - Name with dash PAN: MD SAYID MOLLA - AHJPR0846B
        - Name adjacent to PAN line: line 1 = PINKI ROY, line 2 = AHJPR0846B
        - Name with prefix noise in header: e-Filing Income Tax Department PINKI ROY (AHJPR0846B)
        - Name with role tag and dropdown: AMEJUDDIN SEKH v Individual
        """
        # Format 1: Bracketed PAN
        lines1 = ["e-Filing Income Tax Department", "Notifications Help", "RAMESH SHARMA (ABCPE1234F)", "Individual"]
        self.assertEqual(extract_name_from_ocr_lines(lines1), "RAMESH SHARMA")

        # Format 2: Unbracketed PAN
        lines2 = ["Income Tax Department Government of India", "Dashboard e-File Authorised Partners", "PINKI ROY AHJPR0846B", "Taxpayer"]
        self.assertEqual(extract_name_from_ocr_lines(lines2), "PINKI ROY")

        # Format 3: Hyphenated PAN
        lines3 = ["Notifications", "MD SAYID MOLLA - AHJPR0846B", "Individual"]
        self.assertEqual(extract_name_from_ocr_lines(lines3), "MD SAYID MOLLA")

        # Format 4: Role dropdown button
        lines4 = ["e-Filing Anywhere Anytime", "AMEJUDDIN SEKH v Individual", "AHJPR0846B"]
        self.assertEqual(extract_name_from_ocr_lines(lines4), "AMEJUDDIN SEKH")

        # Format 5: Multi-line pill (name line followed by PAN line)
        lines5 = ["Income Tax Department", "PINKI ROY", "AHJPR0846B"]
        self.assertEqual(extract_name_from_ocr_lines(lines5), "PINKI ROY")

        # Format 6: Header line with portal prefix words
        lines6 = ["e-Filing Income Tax Department PINKI ROY (AHJPR0846B)"]
        self.assertEqual(extract_name_from_ocr_lines(lines6), "PINKI ROY")

        # Format 7: Trailing PAN fragment purge in is_better_taxpayer_name
        self.assertTrue(is_better_taxpayer_name("PINKI ROY", "PINKI ROY AHJPR"))

    def test_header_profile_caps_name_live_portal_navbar(self):
        """
        Tests the user-provided live portal navbar scenarios:
        - Position: Top-right navbar with utility items (Call Us, English, A- A A+)
        - Format: 2-3 words in ALL CAPS
        - Proximity: Followed by dropdown indicator (v, ▼) and role tag (Individual)
        """
        # User screenshot scenario: Same-line navbar with utility controls and zoom buttons
        line_navbar = "Call Us v English v A- A A+ RAHUL MONDAL v Individual"
        self.assertEqual(extract_header_profile_caps_name([line_navbar]), "RAHUL MONDAL")
        self.assertEqual(extract_name_from_ocr_lines([line_navbar]), "RAHUL MONDAL")

        # Multi-line navbar: Name line with trailing arrow followed by role line
        lines_multiline = [
            "e-FiIing Income Tax Department, Government Of India",
            "Authorised Partners Services Pending Actions Grievances Help",
            "Call Us English A- A A+ RAHUL MONDAL v",
            "Individual"
        ]
        self.assertEqual(extract_header_profile_caps_name(lines_multiline), "RAHUL MONDAL")
        self.assertEqual(extract_name_from_ocr_lines(lines_multiline), "RAHUL MONDAL")

        # Trailing Unicode dropdown arrow without role badge
        line_arrow = "Services @ English v Call Us v A- A A+ RAHUL MONDAL ▼"
        self.assertEqual(extract_header_profile_caps_name([line_arrow]), "RAHUL MONDAL")

        # Compound Indian name (4 words) in navbar
        line_compound = "Call Us English A A+ MD WASIL AMAN MANDAL v Individual"
        self.assertEqual(extract_header_profile_caps_name([line_compound]), "MD WASIL AMAN MANDAL")

        # Firm name with safe connector 'AND'
        line_firm = "Call Us English A A+ SEN AND SONS v Individual"
        self.assertEqual(extract_header_profile_caps_name([line_firm]), "SEN AND SONS")

        # Pure navigation line must return None
        line_nav_only = "Call Us v English v Pending Actions v"
        self.assertIsNone(extract_header_profile_caps_name([line_nav_only]))

    def test_proximity_labeled_statutory_names(self):
        """
        Tests statutory labeled proximity scanning:
        - 'Legal Name of Taxpayer : RAHUL MONDAL'
        - 'Trade Name : MONDAL ENTERPRISES'
        - Line-separated label and value
        - Dict output from extract_proximity_labeled_names()
        """
        # Inline with colon
        lines_inline = ["Legal Name of Taxpayer : RAHUL MONDAL"]
        res_inline = extract_proximity_labeled_names(lines_inline)
        self.assertEqual(res_inline.get("legal_name"), "RAHUL MONDAL")
        self.assertEqual(extract_name_from_ocr_lines(lines_inline), "RAHUL MONDAL")

        # Adjacent lines: Line N is label, Line N+1 is value
        lines_adj = [
            "General Information",
            "Legal Name of Taxpayer",
            "RAHUL MONDAL",
            "PAN",
            "AHJPR0846B"
        ]
        res_adj = extract_proximity_labeled_names(lines_adj)
        self.assertEqual(res_adj.get("legal_name"), "RAHUL MONDAL")
        self.assertEqual(extract_name_from_ocr_lines(lines_adj), "RAHUL MONDAL")

        # Separator on its own line: Line N is label, Line N+1 is ':', Line N+2 is value
        lines_sep_line = [
            "Legal Name",
            ":",
            "RAHUL MONDAL"
        ]
        res_sep = extract_proximity_labeled_names(lines_sep_line)
        self.assertEqual(res_sep.get("legal_name"), "RAHUL MONDAL")

        # Both Legal Name and Trade Name extracted simultaneously
        lines_both = [
            "Trade Name : MONDAL ENTERPRISES",
            "Legal Name of Business : RAHUL MONDAL"
        ]
        res_both = extract_proximity_labeled_names(lines_both)
        self.assertEqual(res_both.get("trade_name"), "MONDAL ENTERPRISES")
        self.assertEqual(res_both.get("legal_name"), "RAHUL MONDAL")
        # Legal Name takes priority over Trade Name in primary extraction
        self.assertEqual(extract_name_from_ocr_lines(lines_both), "RAHUL MONDAL")

    def test_noise_disqualification_in_valid_name(self):
        """
        Ensures portal instructions, mode guidance, and UI words are 100% disqualified.
        """
        self.assertFalse(is_valid_name("ONLINE PREPARED UTILITY"))
        self.assertFalse(is_valid_name("APPLICABLE LATER"))
        self.assertFalse(is_valid_name("INFORMATION DIRECTED"))
        self.assertFalse(is_valid_name("CALL US ENGLISH"))
        self.assertFalse(is_valid_name("SERVICES PENDING ACTIONS"))
        self.assertFalse(is_valid_name("INCOME TAX DEPARTMENT"))
        self.assertFalse(is_valid_name("MAP IBROWSER SUPPORT"))
        self.assertFalse(is_valid_name("SITE MAP"))
        self.assertFalse(is_valid_name("BROWSER SUPPORT"))
        self.assertFalse(is_valid_name("MAP BROWSER SUPPORT"))

    def test_reject_portal_nav_browser_support(self):
        """
        Ensures portal navigation boilerplate such as 'MAP IBROWSER SUPPORT' or 'SITE MAP'
        is strictly rejected from being parsed as a valid client name in OCR lines.
        """
        lines = [
            "Site Map | Browser Support",
            "MAP IBROWSER SUPPORT",
            "Skip to Main Content"
        ]
        self.assertIsNone(extract_name_from_ocr_lines(lines))

    def test_truncated_header_pill_yields_to_fuller_name_elsewhere_on_page(self):
        """
        VSDC-X reads the entire page's accessible text regardless of on-screen
        crop, unlike OCR which is genuinely cropped to a region - so a header
        nav pill visually truncated to fit a fixed-width badge (e.g.
        "INDRAJIT CHATTE..." from a name too long to display) must NOT win
        over a fuller, untruncated version of the same name that's very often
        present elsewhere on the same page (a profile card, a labeled form
        field). Observed live: DOB captured correctly from the profile card
        while name incorrectly locked onto the truncated header fragment.
        """
        # Header pill + icon ligature as one combined line (typical UIA read)
        lines_combined = [
            "Dashboard > My Profile > Profile Details",
            "INDRAJIT CHATTE... expand_more",
            "Name",
            "INDRAJIT CHATTERJEE",
            "Date of Birth",
            "06-Aug-1971",
            "PAN",
            "APFPC0458J",
        ]
        self.assertEqual(extract_name_from_ocr_lines(lines_combined), "INDRAJIT CHATTERJEE")

        # Header pill and icon ligature as two separate lines
        lines_split = [
            "INDRAJIT CHATTE...",
            "expand_more",
            "Name",
            "INDRAJIT CHATTERJEE",
            "PAN",
            "APFPC0458J",
        ]
        self.assertEqual(extract_name_from_ocr_lines(lines_split), "INDRAJIT CHATTERJEE")

        # Unicode ellipsis character instead of three literal dots
        lines_unicode_ellipsis = [
            "INDRAJIT CHATTE… expand_more",
            "Name",
            "INDRAJIT CHATTERJEE",
        ]
        self.assertEqual(extract_name_from_ocr_lines(lines_unicode_ellipsis), "INDRAJIT CHATTERJEE")

        # No fuller name anywhere on the page - the truncated fragment is all
        # there is, so it must still be returned rather than nothing at all.
        lines_only_truncated = [
            "INDRAJIT CHATTE... expand_more",
            "Some unrelated dashboard text",
        ]
        self.assertEqual(extract_name_from_ocr_lines(lines_only_truncated), "INDRAJIT CHATTE")


if __name__ == "__main__":
    unittest.main()

