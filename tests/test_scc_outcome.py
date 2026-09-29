"""SCC-U step 4 (W5-4): the outcome reader (core/scc/outcome.py). It reads the later pages of the
password page's window and SGT session, classifies each with scc_rules.json, and moves the attempt on:
worked / wrong_password / locked / neutral / no_conclusion. The lines here are fictional; the wording
staff see comes from scc_rules.json only. No password is ever read (the reader only sees line text)."""
import unittest

from core.scc import OutcomeReader, SccCard, db_client_names
from core.scc.attempts import Attempt
from core.scc.card import SAVED_ROW_LABEL
from core.scc.outcome import names_disagree
from core.scc.scc_rules import get_rules
from core.sgt_i.observation import make_observation

ITR = {"id": 3, "name": "Income Tax",
       "login_page_link": "https://eportal.incometax.gov.in/iec/foservices/#/login", "password_column_id": "pw"}

WORKED = "ASHOK KUMAR SEN Individual"
WRONG = "Invalid Password. You have 4 attempts left."
LOCKED = "Your account has been locked due to multiple invalid attempts."
OTP = "Enter 6 digit OTP"


def obs(lines=(), url="https://eportal.incometax.gov.in/iec/foservices/#/login/password", session="s",
        portal="Income Tax", pan_value=""):
    profile = {"pan": {"value": pan_value, "spec": "itr_pan"}} if pan_value else {}
    return make_observation(session_id=session, portal=portal, url=url, title="t", source="uia",
                            lines=list(lines), result={"profile": profile, "datasets": []},
                            profile={}, draft={}, ts=1.0, today="2026-09-27")


def attempt(hwnd=1, session="s", client_id=7, copies=("Combo 1",)):
    a = Attempt(pan="ABCPD1234E", hwnd=hwnd, session_id=session, client_id=client_id,
                saved_row=True, opened_at=0.0)
    a.ledger.extend(f"copied: {c}" for c in copies)
    return a


class FakeOpener:
    def __init__(self, *attempts):
        self._open = {a.hwnd: a for a in attempts}
        self.ended = []

    def attempt_for(self, hwnd):
        return self._open.get(hwnd)

    def open_attempts(self):
        return list(self._open.values())

    def end(self, hwnd, reason):
        att = self._open.pop(hwnd, None)
        self.ended.append((att, reason))


class FakeCard:
    def __init__(self):
        self.failed, self.stopped = [], []

    def mark_failed(self, att, label, message=""):
        self.failed.append((att.attempt_id, label, message))
        return None

    def show_stop(self, att, message):
        self.stopped.append((att.attempt_id, message))


def reader(opener, card=None, client_names=None, is_window=lambda h: True):
    outcomes = []
    r = OutcomeReader(opener, card, client_names=client_names, is_window=is_window,
                      on_outcome=lambda att, kind, detail: outcomes.append((kind, detail)))
    return r, outcomes


class TestOutcomes(unittest.TestCase):
    def test_a_wrong_password_marks_the_last_copied_row_with_the_rules_message(self):
        opener = FakeOpener(attempt(copies=("Combo 1", "Combo 3")))
        card = FakeCard()
        r, out = reader(opener, card)
        self.assertEqual(r.observe(obs([WRONG]), 1), "wrong_password")
        self.assertEqual(card.failed, [(opener.attempt_for(1).attempt_id, "Combo 3",
                                        get_rules().message("wrong_password"))])
        self.assertIn("Combo 3", r.failed(opener.attempt_for(1).attempt_id))
        self.assertEqual(out, [("wrong_password", "Combo 3")])

    def test_the_same_message_on_the_next_read_is_not_a_second_refusal(self):
        opener = FakeOpener(attempt(copies=("Combo 1",)))
        card = FakeCard()
        r, _ = reader(opener, card)
        self.assertEqual(r.observe(obs([WRONG]), 1), "wrong_password")
        self.assertEqual(r.observe(obs([WRONG]), 1), "same_message")
        self.assertEqual(len(card.failed), 1)

    def test_a_wrong_password_with_no_copy_since_the_last_refusal_is_a_typed_one(self):
        opener = FakeOpener(attempt(copies=()))
        card = FakeCard()
        r, out = reader(opener, card)
        self.assertEqual(r.observe(obs([WRONG]), 1), "wrong_password_typed")
        self.assertEqual(card.failed, [])
        self.assertEqual(out, [("wrong_password", "")])

    def test_locked_stops_the_attempt_with_no_next_row_and_reads_nothing_more(self):
        opener = FakeOpener(attempt())
        card = FakeCard()
        r, out = reader(opener, card)
        aid = opener.attempt_for(1).attempt_id
        self.assertEqual(r.observe(obs([LOCKED]), 1), "locked")
        self.assertEqual(card.stopped, [(aid, get_rules().message("locked"))])
        self.assertEqual(card.failed, [])
        self.assertIsNone(r.observe(obs([WRONG]), 1))     # done: nothing more is read
        self.assertEqual(out, [("locked", "")])

    def test_locked_wins_over_a_wrong_password_on_the_same_page(self):
        opener = FakeOpener(attempt())
        card = FakeCard()
        r, _ = reader(opener, card)
        self.assertEqual(r.observe(obs([WRONG, LOCKED]), 1), "locked")

    def test_worked_ends_the_attempt_when_the_header_name_agrees(self):
        opener = FakeOpener(attempt())
        r, out = reader(opener, client_names=lambda cid: ["Ashok Kumar Sen"])
        self.assertEqual(r.observe(obs([WORKED], url="https://example.test/#/dashboard"), 1), "worked")
        self.assertEqual(out, [("worked", "ASHOK KUMAR SEN")])

    def test_worked_with_a_header_name_that_disagrees_is_no_conclusion(self):
        opener = FakeOpener(attempt())
        r, out = reader(opener, client_names=lambda cid: ["Queenie Roy"])
        self.assertEqual(r.observe(obs([WORKED], url="https://example.test/#/dashboard"), 1), "other_name")
        self.assertEqual([a.hwnd for a, _ in opener.ended], [1])
        self.assertEqual(out, [("no_conclusion", "another name")])

    def test_a_dashboard_whose_pan_disagrees_is_no_conclusion(self):
        opener = FakeOpener(attempt())
        r, out = reader(opener, client_names=lambda cid: ["Ashok Kumar Sen"])
        got = r.observe(obs([WORKED], url="https://example.test/#/dashboard", pan_value="ZZZPZ9999Z"), 1)
        self.assertEqual(got, "another_pan")
        self.assertEqual([r_ for _, r_ in opener.ended], ["no_conclusion"])
        self.assertEqual(out, [("no_conclusion", "another PAN")])

    def test_a_neutral_page_keeps_waiting(self):
        opener = FakeOpener(attempt())
        r, out = reader(opener)
        self.assertEqual(r.observe(obs([OTP]), 1), "neutral")
        self.assertEqual(opener.ended, [])
        self.assertEqual(out, [])

    def test_a_forgot_password_page_is_no_conclusion(self):
        opener = FakeOpener(attempt())
        r, _ = reader(opener)
        self.assertEqual(r.observe(obs(["Anything"], url="https://example.test/#/forgot-password"), 1),
                         "no_conclusion")
        self.assertEqual([r_ for _, r_ in opener.ended], ["no_conclusion"])

    def test_a_closed_window_ends_the_attempt_on_the_next_read(self):
        opener = FakeOpener(attempt())
        r, out = reader(opener, is_window=lambda h: False)
        r.observe(obs([OTP]), 1)
        self.assertEqual([(a.hwnd, r_) for a, r_ in opener.ended], [(1, "no_conclusion")])
        self.assertEqual(out, [("no_conclusion", "window closed")])

    def test_a_page_from_another_session_is_ignored(self):
        opener = FakeOpener(attempt(session="s"))
        r, out = reader(opener)
        self.assertIsNone(r.observe(obs([WRONG], session="other"), 1))
        self.assertEqual(out, [])

    def test_a_non_itr_portal_is_ignored(self):
        opener = FakeOpener(attempt())
        r, _ = reader(opener)
        self.assertIsNone(r.observe(obs([WRONG], portal="GST"), 1))

    def test_an_unmatched_page_does_nothing(self):
        opener = FakeOpener(attempt())
        r, out = reader(opener)
        self.assertIsNone(r.observe(obs(["Password", "Continue"]), 1))
        self.assertEqual((opener.ended, out), ([], []))

    def test_nothing_read_ever_holds_a_password(self):
        opener = FakeOpener(attempt())
        card = FakeCard()
        r, out = reader(opener, card)
        r.observe(obs([WRONG]), 1)
        blob = " ".join(str(x) for x in card.failed + card.stopped + out)
        self.assertNotIn("Fict", blob)      # the reader is fed lines only, never a row's value


class TestNameCheck(unittest.TestCase):
    def test_disagree_only_when_no_word_is_shared_unknowns_agree(self):
        self.assertTrue(names_disagree("QUEENIE ROY", ["Ashok Kumar Sen"]))
        self.assertFalse(names_disagree("ASHOK KUMAR SEN", ["Sen Trading Co"]))
        self.assertFalse(names_disagree("", ["Ashok Kumar Sen"]))
        self.assertFalse(names_disagree("ASHOK KUMAR SEN", []))

    def test_db_client_names_reads_name_like_columns_and_skips_password_columns(self):
        class Db:
            def get_client(self, cid):
                return {"values": {"c1": "Sen Trading Co", "c2": "ABCPD1234E", "c3": "Fict#Pw"}}

            def get_mcl_columns(self):
                return [{"id": "c1", "label": "Company Name"}, {"id": "c2", "label": "PAN"},
                        {"id": "c3", "label": "ITR Password"}]

        names = db_client_names(Db())
        self.assertEqual(names(7), ["Sen Trading Co"])


class _Db:
    def get_services(self):
        return [ITR]

    def generate_scc_passwords(self, pan):
        return [{"id": 1, "label": "Combo 1", "value": "Fict#One1"},
                {"id": 2, "label": "Combo 2", "value": "Fict#Two2"}]

    def get_client(self, client_id):
        return {"id": client_id, "values": {"pw": "Fict#Saved9"}}


class TestCardMarks(unittest.TestCase):
    """The card side the reader drives: x on a row, highlight the next, wrap round, and stop on lock."""
    def setUp(self):
        self.updates = []
        self.card = SccCard(_Db(), open_card=lambda *a: True, close_card=lambda a: None,
                            update_card=lambda *a: self.updates.append(a))
        self.att = Attempt(pan="ABCPD1234E", hwnd=1, session_id="s", client_id=7, saved_row=True, opened_at=0.0)
        self.card.open(self.att)
        self.rows = ["Combo 1", "Combo 2", SAVED_ROW_LABEL]

    def test_a_refusal_marks_the_row_and_highlights_the_next_one(self):
        self.assertEqual(self.card.mark_failed(self.att, "Combo 1", "msg"), "Combo 2")
        self.assertEqual(self.card.failed(self.att.attempt_id), ["Combo 1"])
        aid, failed, nxt, message, stop = self.updates[-1]
        self.assertEqual((aid, failed, nxt, message, stop), (self.att.attempt_id, ["Combo 1"], "Combo 2", "msg", False))

    def test_the_next_row_skips_already_failed_rows_and_wraps_round(self):
        self.card.mark_failed(self.att, "Combo 2", "")
        self.assertEqual(self.card.mark_failed(self.att, SAVED_ROW_LABEL, ""), "Combo 1")

    def test_when_every_row_has_failed_there_is_no_next_row(self):
        for label in self.rows[:-1]:
            self.card.mark_failed(self.att, label, "")
        self.assertIsNone(self.card.mark_failed(self.att, self.rows[-1], ""))
        self.assertIsNone(self.updates[-1][2])

    def test_show_stop_marks_no_next_row(self):
        self.card.show_stop(self.att, "locked")
        aid, failed, nxt, message, stop = self.updates[-1]
        self.assertEqual((nxt, message, stop), (None, "locked", True))

    def test_a_row_not_on_the_card_is_ignored(self):
        self.assertIsNone(self.card.mark_failed(self.att, "made up", ""))
        self.assertEqual(self.updates, [])


if __name__ == "__main__":
    unittest.main()
