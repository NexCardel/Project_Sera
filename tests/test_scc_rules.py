"""
SCC-U rules and attempts (autofill-tweaks blueprint G.2 steps 1-2 and the step 4 wording, W5-2):
scc_rules.json loads with its self-tests and refuses a rule whose examples fail; an attempt opens on
SGT's itr_pan on the password page, a verified client opens nothing, one card per PAN per window.
Everything here is fictional.
"""
import copy
import json

import pytest

from core.scc.attempts import ATTEMPT_TTL_SEC, AttemptOpener, ClientInfo, db_lookup, password_page_pan
from core.scc.scc_rules import OUTCOMES, RULES_PATH, build_rule, load_rules, self_test_rule
from core.sgt.sgt_specs import SpecError
from core.sgt_i.observation import make_observation

PW_URL = "https://eportal.example.test/iec/foservices/#/login/password"
PAN = "ABCPD1234E"


def write_rules(tmp_path, rules, **extra):
    p = tmp_path / "scc_rules.json"
    p.write_text(json.dumps({"rules": rules, **extra}), encoding="utf-8")
    return p


def builtin_rules():
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))["rules"]


def rule_dict(**kw):
    base = {"name": "r", "outcome": "locked", "patterns": ["^locked$"], "examples": ["Locked"],
            "counter_examples": ["Open"]}
    base.update(kw)
    return base


# ── The built-in rules ────────────────────────────────────────────────────────────
def test_builtin_rules_load_clean_and_cover_every_outcome():
    rs = load_rules()
    assert rs.errors == ()
    assert {r.outcome for r in rs.rules} == set(OUTCOMES)


def test_unconfirmed_wording_is_marked_and_the_header_is_not():
    rs = load_rules()
    assert "itr_logged_in_header" not in rs.unconfirmed
    for name in ("itr_wrong_password", "itr_locked", "itr_otp_or_secure_access", "itr_forgot_or_reset_page"):
        assert name in rs.unconfirmed


@pytest.mark.parametrize("lines,url,kind", [
    (["Dashboard", "ASHOK KUMAR SEN Individual"], "", "worked"),
    (["Invalid Password. You have 4 attempts left."], PW_URL, "wrong_password"),
    (["Your account has been locked due to multiple invalid attempts."], PW_URL, "locked"),
    (["Enter 6 digit OTP"], "", "neutral"),
    (["Secure Access Message"], "", "neutral"),
    (["Steps"], "https://x.test/#/e-verify/start", "neutral"),
    (["Anything"], "https://x.test/#/forgot-password", "no_conclusion"),
    (["Reset Password"], "", "no_conclusion"),
])
def test_classify(lines, url, kind):
    got = load_rules().classify(lines, url, "Income Tax")
    assert got is not None and got.kind == kind


def test_worked_carries_the_name_and_marks_itself_confirmed():
    got = load_rules().classify(["ASHOK KUMAR SEN Individual"], "", "Income Tax")
    assert got.name == "ASHOK KUMAR SEN" and got.unconfirmed is False


@pytest.mark.parametrize("lines,url", [
    (["ASHOK KUMAR SEN Chartered Accountant"], ""),             # a CA/ERI role is not the taxpayer
    (["Forgot Password?", "Login with OTP", "Password"], PW_URL),   # the password page's own furniture
    (["e-Verify", "e-File"], ""),                                # header menu of a logged-in page
    (["Invalid Password. Your account will be locked after 3 more attempts."], PW_URL),  # warning, not lock
    ([], ""),
])
def test_not_understood_or_not_that_outcome(lines, url):
    got = load_rules().classify(lines, url, "Income Tax")
    assert got is None or got.kind not in ("worked", "locked", "no_conclusion")


def test_a_lock_warning_is_wrong_password_not_locked():
    got = load_rules().classify(["Invalid Password. Your account will be locked after 3 more attempts."], PW_URL, "Income Tax")
    assert got.kind == "wrong_password"


def test_locked_beats_wrong_password_and_worked_beats_neutral():
    rs = load_rules()
    both = ["Invalid Password", "Your account is locked."]
    assert rs.classify(both, PW_URL, "Income Tax").kind == "locked"
    assert rs.classify(["ASHOK KUMAR SEN Individual", "Enter the OTP"], "", "Income Tax").kind == "worked"


def test_other_portal_and_long_lines_are_ignored():
    rs = load_rules()
    assert rs.classify(["ASHOK KUMAR SEN Individual"], "", "GST") is None
    assert rs.classify(["Invalid Password " + "x" * 500], PW_URL, "Income Tax") is None


# ── The loader refuses ────────────────────────────────────────────────────────────
def test_a_rule_whose_example_fails_is_refused(tmp_path):
    rules = builtin_rules() + [rule_dict(name="bad", examples=["Something else"])]
    rs = load_rules(write_rules(tmp_path, rules))
    assert "bad" not in {r.name for r in rs.rules}
    assert any(e.startswith("bad:") and "should match" in e for e in rs.errors)


def test_a_rule_whose_counter_example_matches_is_refused(tmp_path):
    rules = builtin_rules() + [rule_dict(name="bad", counter_examples=["locked"])]
    rs = load_rules(write_rules(tmp_path, rules))
    assert "bad" not in {r.name for r in rs.rules}
    assert any("should not match" in e for e in rs.errors)


def test_a_rule_another_outcome_claims_first_is_refused(tmp_path):
    greedy = rule_dict(name="greedy", outcome="neutral", patterns=["^locked$"], examples=["locked"], counter_examples=["x"])
    rs = load_rules(write_rules(tmp_path, builtin_rules() + [rule_dict(name="mine", outcome="worked",
                                                                       patterns=["^locked$"], examples=["locked"]), greedy]))
    assert any("another rule claims it first" in e for e in rs.errors)


def test_examples_and_counter_examples_are_required():
    with pytest.raises(SpecError):
        build_rule(rule_dict(examples=[]))
    with pytest.raises(SpecError):
        build_rule(rule_dict(counter_examples=[]))


@pytest.mark.parametrize("bad", [
    {"outcome": "maybe"}, {"patterns": []}, {"patterns": ["(a+)+$"]}, {"patterns": ["("]},
    {"typo": 1}, {"group": 3}, {"case": "loud"},
])
def test_malformed_rules_are_refused(bad):
    with pytest.raises(SpecError):
        build_rule(rule_dict(**bad))


def test_previous_version_of_a_refused_rule_keeps_running(tmp_path):
    good = load_rules()
    edited = copy.deepcopy(builtin_rules())
    for r in edited:
        if r["name"] == "itr_locked":
            r["examples"] = ["No such wording at all"]
    rs = load_rules(write_rules(tmp_path, edited), previous=good)
    assert any(e.startswith("itr_locked:") for e in rs.errors)
    assert rs.classify(["Your account is locked."], PW_URL, "Income Tax").kind == "locked"


def test_an_unreadable_file_gives_the_previous_rules_back(tmp_path):
    good = load_rules()
    p = tmp_path / "scc_rules.json"
    p.write_text("{not json", encoding="utf-8")
    rs = load_rules(p, previous=good)
    assert rs.rules == good.rules and rs.errors
    assert load_rules(p).rules == ()


def test_a_missing_outcome_is_reported(tmp_path):
    rs = load_rules(write_rules(tmp_path, [r for r in builtin_rules() if r["outcome"] != "locked"]))
    assert any("no rule left for 'locked'" in e for e in rs.errors)


def test_every_builtin_rule_passes_its_own_self_test():
    for raw in builtin_rules():
        self_test_rule(build_rule(raw))


# ── Attempts: steps 1-2 ───────────────────────────────────────────────────────────
def page(pan=PAN, url=PW_URL, session="s1", portal="Income Tax", spec="itr_pan"):
    profile = {"pan": {"value": pan, "confidence": 95, "spec": spec}} if pan else {}
    return make_observation(session_id=session, portal=portal, url=url, title="t", source="uia",
                            lines=["PAN", pan or ""], result={"profile": profile, "datasets": []},
                            profile={}, draft={}, ts=1.0, today="2026-09-29")


class Env:
    def __init__(self, clients=None, ttl=ATTEMPT_TTL_SEC):
        self.clients = clients if clients is not None else {}
        self.lookups, self.opened, self.ended = [], [], []
        self.clock = [100.0]
        self.opener = AttemptOpener(self.lookup, on_open=self.opened.append,
                                    on_end=lambda a, why: self.ended.append((a.pan, why)),
                                    ttl_sec=ttl, monotonic=lambda: self.clock[0])

    def lookup(self, pan):
        self.lookups.append(pan)
        return self.clients.get(pan)

    def see(self, obs, hwnd=7):
        return self.opener.observe(obs, hwnd, None)


def test_password_page_pan_needs_the_itr_pan_spec_on_the_password_page():
    assert password_page_pan(page()) == (True, PAN)
    assert password_page_pan(page(spec="itr_dashboard_pan")) == (True, "")
    assert password_page_pan(page(url="https://x.test/#/dashboard")) == (False, "")
    assert password_page_pan(page(portal="GST")) == (False, "")
    assert password_page_pan(page(pan="abc")) == (True, "")
    assert password_page_pan(page(pan=None)) == (True, "")


def test_unregistered_pan_opens_an_attempt_without_a_saved_row():
    e = Env()
    assert e.see(page()) == "opened"
    att = e.opener.attempt_for(7)
    assert (att.pan, att.hwnd, att.session_id, att.client_id, att.saved_row) == (PAN, 7, "s1", None, False)
    assert e.opened == [att]


def test_registered_unverified_client_opens_with_the_saved_password_row_d3():
    e = Env({PAN: ClientInfo(41, False)})
    assert e.see(page()) == "opened"
    att = e.opener.attempt_for(7)
    assert att.client_id == 41 and att.saved_row is True


def test_verified_client_opens_nothing_and_is_looked_up_once():
    e = Env({PAN: ClientInfo(41, True)})
    assert e.see(page()) == "verified"
    assert e.see(page()) == "verified"
    assert e.opener.attempt_for(7) is None and e.opened == [] and e.lookups == [PAN]


def test_one_card_per_pan_per_window():
    e = Env()
    assert e.see(page()) == "opened"
    assert e.see(page()) == "kept"
    assert e.see(page(url="https://x.test/#/login/otp")) == "kept"      # OTP step: same attempt
    assert len(e.opened) == 1 and e.lookups == [PAN]
    assert e.see(page(), hwnd=8) == "opened"                              # another window: its own card


def test_closed_card_stays_closed_until_the_password_page_is_reached_again():
    e = Env()
    e.see(page())
    e.opener.close(7)
    assert e.ended == [(PAN, "closed")]
    assert e.see(page()) == "closed_before"          # still on the password page: stays closed
    assert e.see(page(pan=None)) is None             # page read without the PAN: still closed
    assert e.see(page(url="https://x.test/#/dashboard", pan=None)) is None   # left the password page
    assert e.see(page()) == "opened"                 # reached again: a fresh card
    assert len(e.opened) == 2


def test_session_end_or_another_pan_ends_the_attempt_with_no_conclusion():
    e = Env()
    e.see(page())
    assert e.see(page(session="s2")) == "opened"     # SGT ended the session (a login link): new one
    assert e.ended == [(PAN, "no_conclusion")]
    other = "XYZAB9876C"
    assert e.see(page(pan=other, session="s2")) == "opened"
    assert e.ended[-1] == (PAN, "no_conclusion") and e.opener.attempt_for(7).pan == other


def test_session_change_off_the_password_page_ends_it_too():
    e = Env()
    e.see(page())
    assert e.see(page(pan=None, url="https://x.test/#/login", session="s2")) == "ended"
    assert e.opener.attempt_for(7) is None and e.ended == [(PAN, "no_conclusion")]


def test_attempt_times_out_after_ten_minutes():
    e = Env()
    e.see(page())
    e.clock[0] += ATTEMPT_TTL_SEC + 1
    e.opener.expire()
    assert e.opener.attempt_for(7) is None and e.ended == [(PAN, "no_conclusion")]


def test_other_portals_are_ignored():
    e = Env()
    assert e.see(page(portal="GST")) is None and e.lookups == []


def test_db_lookup_is_read_only_and_names_no_password():
    calls = []

    class Db:
        def get_client_by_pan(self, pan):
            calls.append(("get_client_by_pan", pan))
            return {"id": 9, "values": {"IT Password": "not-a-real-secret"}} if pan == PAN else None

        def is_client_scc_verified(self, pan="", client_id=None):
            calls.append(("is_client_scc_verified", client_id))
            return False

    look = db_lookup(Db())
    info = look(PAN)
    assert info == ClientInfo(9, False) and look("XYZAB9876C") is None
    assert [c[0] for c in calls] == ["get_client_by_pan", "is_client_scc_verified", "get_client_by_pan"]
    assert "secret" not in repr(info)
