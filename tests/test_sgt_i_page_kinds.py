"""
Tests for core/sgt_i/page_kinds.py (blueprint 14.4 step 6, second half). Synthetic node fixtures,
built the same way test_sgt_i_page_map.py builds them; every name, PAN, ack and amount here is
fictional.
"""

from core.sgt_i import page_kinds as pk
from core.sgt_i import page_map as pm

TEXT, EDIT, RADIO, GROUP, TABITEM, TABLE, CELL, HEADER_ITEM, COMBO, LIST, LISTITEM, LINK = (
    50020, 50004, 50013, 50026, 50019, 50036, 50029, 50035, 50003, 50008, 50007, 50005)


class Doc:
    """Builds one uia_nodes-style document: pre-order dicts with parent indices."""

    def __init__(self):
        self.nodes = []

    def add(self, name, rect=None, ctype=TEXT, parent=-1, **extra):
        depth = self.nodes[parent]["depth"] + 1 if parent >= 0 else 0
        node = {"parent": parent, "depth": depth, "ctype": ctype, "name": name, "rect": rect}
        node.update(extra)
        self.nodes.append(node)
        return len(self.nodes) - 1

    def map(self, **kw):
        return pm.build_page_map(pm.nodes_from_uia([self.nodes]), **kw)


def _main(doc):
    return doc.add("", (0, 150, 1200, 900), GROUP, landmark=80002)


# ── wizard_step: a stepper on the page itself, unambiguous ─────────────────────────────────────
def test_stepper_current_step_is_wizard_step_even_with_happened_text_on_another_step():
    # 14.1's own bug case: an un-reached stepper step already carries "Successfully Verified"
    # text. Page kinds must not read that as a confirmation.
    d = Doc()
    main = _main(d)
    steps = ("Personal Info", "Income", "Tax Paid", "Return Successfully Verified")
    for i, name in enumerate(steps):
        d.add(str(i + 1), (40 + i * 250, 200, 20, 20), parent=main)
        d.add(name, (70 + i * 250, 200, 170, 20), TABITEM, parent=main, selected=(i == 0))
    d.add("Name", (20, 300, 80, 20), parent=main)
    d.add("Asha Verma", (200, 300, 200, 20), parent=main)
    page = d.map()
    result = pk.classify(page)
    assert result.kind == "wizard_step"
    assert "stepper" in result.evidence


# ── confirmation: an identifier + "happened" wording, in a trusted zone ────────────────────────
def test_dialog_with_ack_and_happened_wording_is_confirmation():
    d = Doc()
    main = _main(d)
    d.add("Name", (20, 640, 80, 20), parent=main)
    d.add("Example Person", (200, 640, 200, 20), parent=main)
    dlg = d.add("", (300, 200, 500, 200), role="dialog")
    d.add("Your return has been submitted successfully", (320, 210, 400, 30), parent=dlg, heading=2)
    d.add("Acknowledgement No.", (320, 260, 180, 20), parent=dlg)
    d.add("123456789012345", (520, 260, 180, 20), parent=dlg)
    page = d.map()
    result = pk.classify(page)
    assert result.kind == "confirmation"
    assert "identifier" in result.evidence and "happened" in result.evidence


def test_original_return_ack_is_not_a_confirmation():
    # 14.1's bug: "a revised-return wizard's original ack read as a new submission." The
    # assertion checker already reads this sentence as reference_to_past, not happened, so the
    # page-kind classifier has no identifier+happened pair to call a confirmation on.
    d = Doc()
    main = _main(d)
    dlg = d.add("", (300, 200, 500, 200), role="dialog")
    d.add("Details of Original Return", (320, 210, 400, 30), parent=dlg, heading=2)
    d.add("Acknowledgement Number", (320, 260, 220, 20), parent=dlg)
    d.add("123456789012345", (560, 260, 180, 20), parent=dlg)
    page = d.map()
    result = pk.classify(page)
    assert result.kind is None


# ── error: negated wording, or error vocabulary, beats everything but the stepper ──────────────
def test_negated_wording_beats_an_identifier_present_on_the_same_page():
    d = Doc()
    main = _main(d)
    dlg = d.add("", (300, 200, 500, 200), role="dialog")
    d.add("Payment failed for transaction TXN123456", (320, 210, 400, 30), parent=dlg, heading=2)
    d.add("Transaction ID", (320, 260, 180, 20), parent=dlg)
    d.add("TXN1234567890", (520, 260, 180, 20), parent=dlg)
    page = d.map()
    result = pk.classify(page)
    assert result.kind == "error"
    assert result.evidence[0] == "negated"


def test_error_vocab_without_any_assertion_trigger():
    d = Doc()
    main = _main(d)
    d.add("Something went wrong", (20, 200, 400, 30), parent=main, heading=1)
    d.add("Please try again later.", (20, 240, 400, 20), parent=main)
    page = d.map()
    result = pk.classify(page)
    assert result.kind == "error"
    assert result.evidence[0] == "vocab:error"


def test_disabled_menu_items_placeholders_and_help_text_are_not_errors():
    # Field test 2026-09-28: 373 of 495 recorded ITR pages read as "error". The menu on every
    # page names disabled items "<item> unavailable"; forms carry an empty "Error :" slot and an
    # "error icon" image; help text mentions a return being "rejected". None is an error.
    d = Doc()
    main = _main(d)
    for i, item in enumerate(("e-file unavailable", "Dashboard unavailable", "My Profile unavailable")):
        d.add(item, (20 + i * 200, 160, 180, 20), LINK, parent=main)
    d.add("Error :", (20, 200, 80, 20), parent=main)
    d.add("error icon", (20, 230, 80, 20), parent=main)
    d.add("After filing your return, please check its processing status in View Filed Returns "
          "to see whether it was accepted or rejected.", (20, 260, 900, 20), parent=main)
    assert pk.classify(d.map()).kind != "error"


def test_real_error_messages_still_read_as_errors():
    for text in ("Invalid Password, Please retry.", "Service temporarily unavailable",
                 "Error : PAN does not exist"):
        d = Doc()
        main = _main(d)
        d.add(text, (20, 200, 600, 20), parent=main)
        assert pk.classify(d.map()).kind == "error", text


def test_dashboard_menu_labels_with_an_ack_are_not_a_confirmation():
    # Same field test: "View Filed Returns" / "Recent Forms Filed" on a dashboard beside a listed
    # ack number read as confirmation on 112 pages; an avatar's alt text read as profile on 300.
    d = Doc()
    main = _main(d)
    d.add("User profile picture", (1100, 160, 40, 40), parent=main)
    d.add("View Filed Returns", (20, 200, 200, 20), LINK, parent=main)
    d.add("Recent Forms Filed", (20, 240, 200, 20), parent=main, heading=2)
    d.add("Acknowledgement No.", (20, 280, 180, 20), parent=main)
    d.add("123456789012345", (220, 280, 180, 20), parent=main)
    assert pk.classify(d.map()).kind not in ("confirmation", "profile")


def test_statement_on_the_main_page_with_an_ack_is_a_confirmation():
    d = Doc()
    main = _main(d)
    d.add("Your return has been filed successfully", (20, 200, 500, 30), parent=main, heading=1)
    d.add("Acknowledgement No.", (20, 260, 180, 20), parent=main)
    d.add("123456789012345", (220, 260, 180, 20), parent=main)
    assert pk.classify(d.map()).kind == "confirmation"


def test_filed_returns_list_with_a_status_per_ack_is_a_list_not_a_confirmation():
    d = Doc()
    main = _main(d)
    for i, ack in enumerate(("123456789012345", "223456789012345")):
        y = 200 + i * 80
        d.add("Successfully e-verified", (20, y, 300, 20), parent=main, heading=3)
        d.add("Acknowledgement No.", (20, y + 30, 180, 20), parent=main)
        d.add(ack, (220, y + 30, 180, 20), parent=main)
    assert pk.classify(d.map()).kind == "list"


def test_long_negated_sentence_in_a_dialog_is_still_an_error():
    d = Doc()
    _main(d)
    dlg = d.add("", (300, 200, 500, 200), role="dialog")
    d.add("The registration process is not yet complete and hence the applicant cannot proceed "
          "with filing until the pending steps are done.", (320, 210, 460, 60), parent=dlg)
    assert pk.classify(d.map()).kind == "error"


# ── payment: an amount plus payment vocabulary, both required ──────────────────────────────────
def test_amount_with_payment_vocab_is_payment():
    d = Doc()
    main = _main(d)
    d.add("Payment Due", (20, 200, 300, 30), parent=main, heading=1)
    d.add("Amount Due", (20, 240, 100, 20), parent=main)
    d.add("₹15,000", (140, 240, 100, 20), parent=main)
    page = d.map()
    result = pk.classify(page)
    assert result.kind == "payment"


def test_amount_alone_without_payment_vocab_does_not_fire():
    d = Doc()
    main = _main(d)
    d.add("Salary", (20, 200, 100, 20), parent=main)
    d.add("₹50,000", (140, 200, 100, 20), parent=main)
    page = d.map()
    result = pk.classify(page)
    assert result.kind != "payment"


# ── login: mostly inputs, plus login wording ────────────────────────────────────────────────────
def test_input_dominant_page_with_login_wording_is_login():
    d = Doc()
    main = _main(d)
    d.add("Login", (20, 160, 200, 30), parent=main, heading=1)
    d.add("Username", (20, 200, 200, 24), EDIT, parent=main)
    d.add("Password", (20, 240, 200, 24), EDIT, parent=main)
    page = d.map()
    result = pk.classify(page)
    assert result.kind == "login"
    assert "input-dominant" in result.evidence


def test_input_dominant_page_without_any_vocab_abstains():
    d = Doc()
    main = _main(d)
    d.add("City", (20, 200, 200, 24), EDIT, parent=main)
    d.add("Pincode", (20, 240, 200, 24), EDIT, parent=main)
    page = d.map()
    result = pk.classify(page)
    assert result.kind is None


def test_atlas_hint_is_only_ever_a_tie_break_for_an_unvocabbed_entry_page():
    d = Doc()
    main = _main(d)
    d.add("City", (20, 200, 200, 24), EDIT, parent=main)
    d.add("Pincode", (20, 240, 200, 24), EDIT, parent=main)
    page = d.map()
    assert pk.classify(page, atlas_hint={"in_degree": 1}).kind is None
    result = pk.classify(page, atlas_hint={"in_degree": 0})
    assert result.kind == "login"
    assert result.evidence[-1] == "atlas:in_degree=0"


# ── list: a table with more than one row ────────────────────────────────────────────────────────
def test_table_with_two_rows_is_list():
    d = Doc()
    main = _main(d)
    t = d.add("", (20, 200, 800, 100), TABLE, parent=main)
    d.add("Assessment Year", (20, 200, 200, 20), HEADER_ITEM, parent=t, role="columnheader",
          grid=(0, 0))
    d.add("Status", (300, 200, 200, 20), HEADER_ITEM, parent=t, role="columnheader", grid=(0, 1))
    for r, (ay, st) in enumerate((("2025-26", "Processed"), ("2024-25", "Filed")), start=1):
        c0 = d.add("", (20, 200 + r * 25, 200, 20), CELL, parent=t, grid=(r, 0))
        d.add(ay, (22, 200 + r * 25, 100, 20), parent=c0)
        d.add(st, (300, 200 + r * 25, 200, 20), CELL, parent=t, grid=(r, 1))
    page = d.map()
    result = pk.classify(page)
    assert result.kind == "list"
    assert result.evidence[0] == "table"


# ── list / dashboard: repeated sibling blocks, not a table ─────────────────────────────────────
def test_repeated_link_cards_are_dashboard():
    d = Doc()
    main = _main(d)
    for i in range(3):
        card = d.add("", (20 + i * 200, 200, 180, 100), GROUP, parent=main)
        d.add("Open service %d" % i, (30 + i * 200, 210, 160, 20), LINK, parent=card)
    page = d.map()
    result = pk.classify(page)
    assert result.kind == "dashboard"
    assert result.evidence[0] == "repeated-blocks"


def test_repeated_data_rows_without_a_table_are_list():
    d = Doc()
    main = _main(d)
    for i in range(3):
        row = d.add("", (20, 200 + i * 40, 800, 30), GROUP, parent=main)
        d.add("Item %d" % i, (20, 200 + i * 40, 200, 20), EDIT, parent=row, value="X%d" % i)
    page = d.map()
    result = pk.classify(page)
    assert result.kind == "list"
    assert result.evidence[0] == "repeated-blocks"


# ── profile: an identity-shaped value under profile wording ────────────────────────────────────
def test_profile_vocab_with_identity_value_is_profile():
    d = Doc()
    main = _main(d)
    d.add("My Profile", (20, 160, 200, 30), parent=main, heading=1)
    d.add("Email", (20, 200, 100, 20), parent=main)
    d.add("person@example.com", (140, 200, 200, 20), parent=main)
    page = d.map()
    result = pk.classify(page)
    assert result.kind == "profile"


def test_identity_value_without_profile_vocab_abstains():
    d = Doc()
    main = _main(d)
    d.add("Email", (20, 200, 100, 20), parent=main)
    d.add("person@example.com", (140, 200, 200, 20), parent=main)
    page = d.map()
    result = pk.classify(page)
    assert result.kind is None


# ── config ───────────────────────────────────────────────────────────────────────────────────
def test_vocab_loaded_has_the_four_kinds():
    vocab = pk.load_vocab()
    for key in ("login", "payment", "profile", "error"):
        assert vocab.get(key), "missing 'page_kinds.%s' in sgt_i_config.json" % key


def test_missing_config_file_falls_back_without_raising(tmp_path):
    missing = tmp_path / "no_such_config.json"
    vocab = pk.load_vocab(missing)
    assert vocab == pk._FALLBACK_VOCAB


def test_page_kinds_lists_all_eight():
    assert set(pk.PAGE_KINDS) == {
        "login", "dashboard", "profile", "list", "wizard_step", "confirmation", "error", "payment"}
