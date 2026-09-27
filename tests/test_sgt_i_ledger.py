"""
SGT-I evidence ledger tests (blueprint 14.4 step 7, W8-1): beliefs with sightings and config
weights, truth-maintenance retraction, one-line explanations, and the five second opinions -
all written through the enrichment channel, never into a Core value. Everything here is fictional.
"""
import json
from datetime import date

from core.sgt_i import SgtIntelligence, default_components, make_observation
from core.sgt_i.ledger import (Ledger, LedgerComponent, load_config, period_kind, period_window,
                               second_opinions)

CFG = load_config()
PAN_P = "ABCPD1234E"       # 4th letter P: an individual
PAN_P2 = "XYZPQ9876K"
PAN_C = "AAACZ9876K"       # 4th letter C: a company


class _Ctx:
    def __init__(self):
        self.data = None

    def enrich(self, data):
        json.dumps(data)
        self.data = data

    def ask_more_reads(self, n=1):
        pass

    def read_harder(self, s=30):
        pass


def _hit(v, c=90):
    return {"value": v, "confidence": c, "spec": "x"}


def _obs(lines, current=None, datasets=None, profile=None, draft=None, portal="Income Tax",
         source="uia", session="s1"):
    return make_observation(session_id=session, portal=portal, url="https://example.test/p", title="t",
                            source=source, lines=list(lines),
                            result={"profile": {}, "datasets": list(datasets or []),
                                    "current": {k: _hit(v) for k, v in (current or {}).items()},
                                    "is_list": bool(datasets), "conflicts": []},
                            profile=profile if profile is not None else {"pan": PAN_P},
                            draft=draft or {}, ts=1.0, today="2026-09-27")


def _card(**values):
    return {"record": "itr_dataset_cards", "confidence": 95, "values": values}


SUBMITTED = ["You have successfully submitted your return!", "Acknowledgement Number :",
             "123456789270926", "Return e-Verified successfully", "ITR-4 for A.Y. 2026-27"]


def _run(comp, *observations):
    ctx = _Ctx()
    for o in observations:
        comp.observe(o, ctx)
    return ctx.data


def _only(data):
    assert data and len(data["datasets"]) == 1, data
    return data["datasets"][0]


def _rules(row):
    return [o["rule"] for o in row.get("second_opinions", [])]


# ── Explanations and scores ──────────────────────────────────────────────────────────────────
def test_explanation_names_the_status_ack_tail_page_kind_wording_reads_and_source():
    o = _obs(SUBMITTED, current={"arn": "123456789270926", "status": "Submitted & Verified",
                                 "form": "ITR-4", "period": "AY 2026-27"})
    row = _only(_run(LedgerComponent(), o))
    assert row["dataset"] == "ITR-4 AY 2026-27"
    assert row["explanation"].startswith("Submitted & Verified - ack …270926 on a confirmation page + '")
    assert row["explanation"].endswith(", read once, UIA.")
    assert "second_opinions" not in row


def test_reading_again_raises_the_score_and_counts_the_reads():
    comp = LedgerComponent()
    cur = {"arn": "123456789270926", "status": "Submitted & Verified", "form": "ITR-4", "period": "AY 2026-27"}
    first = _only(_run(comp, _obs(SUBMITTED, current=cur)))["confidence"]["arn"]
    row = _only(_run(comp, _obs(SUBMITTED, current=cur, source="ocr")))
    assert row["confidence"]["arn"] > first
    assert "read twice, OCR/UIA." in row["explanation"]


def test_weights_come_from_config():
    cur = {"arn": "123456789270926", "form": "ITR-4", "period": "AY 2026-27"}
    low = dict(CFG, source={"uia": 0.1})
    lo = _only(_run(LedgerComponent(config=low), _obs(SUBMITTED, current=cur)))["confidence"]["arn"]
    hi = _only(_run(LedgerComponent(), _obs(SUBMITTED, current=cur)))["confidence"]["arn"]
    assert lo < hi


# ── Second opinions ──────────────────────────────────────────────────────────────────────────
def test_an_ack_beside_original_return_wording_is_only_a_reference():
    lines = ["Original return details", "Acknowledgement Number :", "123456789150925", "Filed successfully"]
    o = _obs(lines, datasets=[_card(arn="123456789150925", form="ITR-4", period="AY 2025-26")])
    row = _only(_run(LedgerComponent(), o))
    assert _rules(row) == ["reference_ack"]
    assert "reference to an earlier dataset" in row["second_opinions"][0]["says"]


def test_itr_form_against_the_pans_fourth_letter():
    o = _obs(SUBMITTED, current={"arn": "123456789270926", "form": "ITR-6", "period": "AY 2026-27"})
    row = _only(_run(LedgerComponent(), o))
    assert row["second_opinions"] == [{"rule": "form_vs_pan",
                                       "says": "ITR-6 with an individual's PAN (P) cannot be right"}]
    ok = _obs(SUBMITTED, current={"arn": "123456789270926", "form": "ITR-6", "period": "AY 2026-27"},
              profile={"pan": PAN_C})
    assert "second_opinions" not in _only(_run(LedgerComponent(), ok))


def test_a_quarterly_form_needs_a_quarterly_period():
    gst = {"gstin": "19ABCPD1234E1ZB"}
    bad = _obs(["CMP-08"], current={"form": "CMP-08", "period": "June (FY 2026-27)"}, portal="GST Portal", profile=gst)
    row = _only(_run(LedgerComponent(), bad))
    assert _rules(row) == ["form_vs_period"]
    assert row["second_opinions"][0]["says"] == "CMP-08 is filed quarterly but the period reads monthly"
    good = _obs(["CMP-08"], current={"form": "CMP-08", "period": "Q1 (FY 2026-27)"}, portal="GST Portal", profile=gst)
    assert "second_opinions" not in _only(_run(LedgerComponent(), good))


def test_the_identifiers_own_date_must_fall_inside_its_period():
    lines = ["Acknowledgement No :", "123456789150926"]
    bad = _obs(lines, datasets=[_card(arn="123456789150926", form="ITR-4", period="AY 2025-26",
                                      filing_type="Original")])
    assert _rules(_only(_run(LedgerComponent(), bad))) == ["identifier_date"]
    good = _obs(lines, datasets=[_card(arn="123456789150925", form="ITR-4", period="AY 2025-26")])
    assert "second_opinions" not in _only(_run(LedgerComponent(), good))
    # An updated dataset is filed years later by design: exempt in config.
    upd = _obs(lines, datasets=[_card(arn="123456789150926", form="ITR-4", period="AY 2025-26",
                                      filing_type="Updated")])
    assert "second_opinions" not in _only(_run(LedgerComponent(), upd))


def test_a_form_that_belongs_to_another_portal():
    o = _obs(["GSTR-3B"], current={"form": "GSTR-3B", "period": "June (FY 2026-27)"})
    row = _only(_run(LedgerComponent(), o))
    assert row["second_opinions"] == [{"rule": "form_portal", "says": "GSTR-3B is a gst form, not Income Tax"}]


def test_constraints_are_config_driven():
    empty = {k: v for k, v in CFG.items() if k not in ("form_pan_letters", "portal_forms")}
    o = _obs(["x"], current={"form": "ITR-6", "period": "AY 2026-27"})
    assert "second_opinions" not in _only(_run(LedgerComponent(config=empty), o))


def test_period_windows_and_kinds():
    assert period_window("AY 2025-26") == ("ay", date(2025, 4, 1), date(2026, 3, 31))
    assert period_window("June (FY 2026-27)") == ("month", date(2026, 6, 1), date(2026, 6, 30))
    assert period_window("January (FY 2026-27)") == ("month", date(2027, 1, 1), date(2027, 1, 31))
    assert period_window("Q3 (FY 2026-27)") == ("quarter", date(2026, 10, 1), date(2026, 12, 31))
    assert period_window("Q4 (FY 2026-27)") == ("quarter", date(2027, 1, 1), date(2027, 3, 31))
    assert period_window("whenever") is None
    assert [period_kind(p, CFG) for p in ("AY 2025-26", "June (FY 2026-27)", "Q2 (FY 2026-27)")] \
        == ["annual", "monthly", "quarterly"]


# ── Retraction (truth maintenance) ───────────────────────────────────────────────────────────
def test_retraction_withdraws_dependents_until_stable():
    led = Ledger(salt="00")
    s1 = led.new_sighting(page=1, kind=None, assertion=None, trigger=None, source="uia", route=None,
                          client="pan:a", confidence=90, weight=0.9)
    s2 = led.new_sighting(page=2, kind=None, assertion=None, trigger=None, source="uia", route=None,
                          client="pan:b", confidence=90, weight=0.9)
    arn = led.add("d", "arn", "123456789270926", s1)
    status = led.add("d", "status", "Submitted & Verified", s2)
    form = led.add("d", "form", "ITR-4", s2)
    led.depend(status, arn)
    gone = led.retract(lambda s: s.client == "pan:a")
    assert {b.field for b in gone} == {"arn", "status"}     # status fell with the ack it rested on
    assert list(led.beliefs) == [form.key] and led.retracted == 2


def test_another_clients_evidence_is_withdrawn_when_the_session_turns_out_to_be_someone_else():
    comp = LedgerComponent()
    cur = {"arn": "123456789270926", "status": "Submitted & Verified", "form": "ITR-4", "period": "AY 2026-27"}
    assert _only(_run(comp, _obs(SUBMITTED, current=cur, profile={"pan": PAN_P})))
    data = _run(comp, _obs(["Dashboard"], profile={"pan": PAN_P2}))
    assert data == {"datasets": [], "retracted": 4}


def test_a_card_naming_another_client_is_never_evidence():
    lines = ["Acknowledgement No :", "123456789150925"]
    o = _obs(lines, datasets=[_card(arn="123456789150925", form="ITR-4", period="AY 2025-26", pan=PAN_P2),
                              _card(arn="987654321150925", form="ITR-1", period="AY 2025-26", pan=PAN_P)])
    data = _run(LedgerComponent(), o)
    assert [r["dataset"] for r in data["datasets"]] == ["ITR-1 AY 2025-26"]
    assert data["retracted"] == 4


def test_a_gstin_profile_never_contradicts_a_cards_pan():
    o = _obs(["x"], datasets=[_card(arn="123456789150925", form="ITR-4", period="AY 2025-26", pan=PAN_P)],
             profile={"gstin": "19XYZPQ9876K1ZB"})
    assert "retracted" not in _run(LedgerComponent(), o)


# ── The channel, privacy, and the Core ───────────────────────────────────────────────────────
def test_the_row_never_carries_a_pan_or_a_whole_ack():
    o = _obs(SUBMITTED, current={"arn": "123456789270926", "form": "ITR-6", "period": "AY 2026-27",
                                 "pan": PAN_P})
    data = _run(LedgerComponent(), o)
    text = json.dumps(data)
    assert PAN_P not in text and "123456789270926" not in text
    assert "pan" not in _only(data)["confidence"]


def test_many_datasets_stay_under_the_enrichment_limit():
    cards = [_card(arn="1234567%02d150925" % i, form="ITR-%d" % (i % 7 + 1), period="AY 20%02d-%02d" % (i, i + 1))
             for i in range(10, 40)]
    data = _run(LedgerComponent(), _obs(["x"], datasets=cards))
    assert 0 < len(data["datasets"]) <= 6
    assert len(json.dumps(data, ensure_ascii=False).encode("utf-8")) <= 4096


def test_registered_in_the_app_after_the_gps_and_runs_through_the_host():
    names = [c.name for c in default_components()]
    assert names.index("ledger") > names.index("gps")
    host = SgtIntelligence([LedgerComponent()], enabled=True, echo=lambda m: None)
    o = _obs(SUBMITTED, current={"arn": "123456789270926", "form": "ITR-4", "period": "AY 2026-27"})
    host._page(o, host._components)
    assert host.tripped is None
    assert host.enrichment("s1")["ledger"]["datasets"][0]["dataset"] == "ITR-4 AY 2026-27"
