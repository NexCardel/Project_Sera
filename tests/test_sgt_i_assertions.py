"""
Tests for core/sgt_i/assertions.py (blueprint 14.4 step 6, first half). Wordings are the real
phrasing from core/sgt/sgt_fields.json's own examples/patterns and from 14.1's bug table; every
identifier-shaped value in them is fictional.
"""

from core.sgt_i import assertions as A


# ── the four classes, from 14.4 step 6's own table ─────────────────────────────────────────────
def test_happened_from_step6_table():
    a = A.classify("Your return has been successfully e-verified")
    assert a.cls == "happened"


def test_negated_failed_from_step6_table():
    assert A.classify("e-Verification failed").cls == "negated"


def test_negated_not_yet_from_step6_table():
    assert A.classify("not yet filed").cls == "negated"


def test_future_conditional_from_step6_table():
    assert A.classify("You will receive an acknowledgement soon").cls == "future_conditional"


def test_reference_to_past_from_step6_table():
    a = A.classify("Acknowledgement Number of Original Return: 123456789150925")
    assert a.cls == "reference_to_past"


# ── 14.1's bug table ────────────────────────────────────────────────────────────────────────────
def test_stepper_wording_alone_still_reads_happened():
    # 14.1: "'Return Successfully Verified' drawn on step 1 of a stepper" - telling this apart from
    # what has actually happened is page kinds' job (step 6's other half), not this checker's; the
    # wording itself is a plain happened claim.
    assert A.classify("Return Successfully Verified").cls == "happened"


def test_original_return_ack_reads_as_reference_not_new_submission():
    # 14.1: "A revised-return wizard's original ack read as a new submission."
    a = A.classify("Original return details, Acknowledgement Number: 123456789150925")
    assert a.cls == "reference_to_past"


# ── real wordings from core/sgt/sgt_fields.json's own examples and patterns ───────────────────
def test_itr_status_examples_from_sgt_fields():
    assert A.classify("Pending for e-Verification").cls == "future_conditional"
    assert A.classify("ITR Filed").cls == "happened"
    assert A.classify("Successfully e-Verified").cls == "happened"
    assert A.classify("Return Verified").cls == "happened"
    assert A.classify("Under Processing").cls == "future_conditional"


def test_itr_submit_examples_from_sgt_fields():
    assert A.classify("You have successfully submitted your return!").cls == "happened"
    assert A.classify("Return filed successfully").cls == "happened"
    assert A.classify("Still need to e-Verify").cls == "future_conditional"
    assert A.classify("Please e-Verify within 30 days").cls == "future_conditional"
    # sgt_fields.json's own counter_example for the submit pattern
    assert A.classify("Once you submit, you will receive an acknowledgement").cls == "future_conditional"


def test_gst_filing_examples_from_sgt_fields():
    assert A.classify("GSTR-3B has been filed successfully").cls == "happened"
    assert A.classify("Filing Successful").cls == "happened"
    assert A.classify("ARN generated").cls == "happened"


# ── priority: negated / reference / future all read before a bare "happened" word ─────────────
def test_negated_beats_happened_word_in_same_sentence():
    a = A.classify("e-Verification could not be completed")
    assert a.cls == "negated"
    assert "not" in a.trigger


def test_reference_to_past_beats_happened_word_in_same_sentence():
    assert A.classify("Acknowledgement Number of Original Return - already verified").cls == "reference_to_past"


# ── NegEx scope: a terminator stops a negator's forward reach ─────────────────────────────────
def test_negation_scope_stops_at_terminator():
    cfg = {
        "terminators": ["\\bbut\\b"],
        "negated": {"self": [], "pre": ["\\bnot\\b"], "post": ["\\bdeclined\\b"]},
        "reference_to_past": {"self": []},
        "future_conditional": {"self": []},
        "happened": {"self": ["\\bverified\\b"]},
    }
    # "verified" sits inside "not"'s forward scope -> negated.
    assert A.classify("not applicable but later verified", cfg).cls == "happened"
    # without the terminator, the same words are negated.
    assert A.classify("not later verified at all", cfg).cls == "negated"
    # post trigger: "verified" sits before "declined" in the same clause -> negated.
    assert A.classify("verified then declined", cfg).cls == "negated"


def test_no_trigger_is_neutral():
    a = A.classify("Search Box Input Field")
    assert a.cls is None
    assert a.trigger is None


def test_classify_many_shares_one_config():
    results = A.classify_many(("e-Verification failed", "Return filed successfully"))
    assert [r.cls for r in results] == ["negated", "happened"]


def test_config_loaded_has_all_four_classes():
    cfg = A.load_config()
    for cls in A.ASSERTION_CLASSES:
        assert cfg.get(cls), "missing '%s' section in sgt_i_config.json" % cls


def test_missing_config_file_falls_back_without_raising(tmp_path):
    missing = tmp_path / "no_such_config.json"
    a = A.classify("e-Verification failed", A.load_config(missing))
    assert a.cls == "negated"
