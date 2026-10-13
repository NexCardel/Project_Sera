"""SCC-U step 5 (W5-5): which password worked (core/scc/which.py), with the real card and outcome
reader. Distinct rows copied since the last wrong-password message: one -> credited; several -> the
card asks; none -> the card asks with "None - I typed my own" (saves nothing). A x row is never
credited in the same attempt. Passwords and page lines are fictional; the ledger holds labels only."""
import unittest
from unittest.mock import MagicMock, patch

import automation
from core.scc import OutcomeReader, SccCard, WhichOne, pick_rows
from core.scc.attempts import Attempt
from core.scc.card import SAVED_ROW_LABEL
from core.scc.scc_rules import get_rules
from core.sgt_i.observation import make_observation

ITR = {"id": 3, "name": "Income Tax", "password_column_id": "pw"}
COMBOS = [{"id": 1, "label": "Combo 1", "value": "Fict#One1"},
          {"id": 3, "label": "Combo 3", "value": "Fict#Three3"}]
TEXT = {"Combo 1": "Fict#One1", "Combo 3": "Fict#Three3", SAVED_ROW_LABEL: "Fict#Saved9"}

WORKED = "ASHOK KUMAR SEN Individual"
WRONG = "Invalid Password. You have 4 attempts left."
WRONG_2 = "Invalid Password. You have 3 attempts left."


class _Db:
    def get_services(self):
        return [ITR]

    def generate_scc_passwords(self, pan):
        return list(COMBOS)

    def get_client(self, client_id):
        return {"id": client_id, "values": {"pw": "Fict#Saved9"}}


class _Opener:
    def __init__(self, att):
        self._open = {att.hwnd: att}
        self.ended = []

    def attempt_for(self, hwnd):
        return self._open.get(hwnd)

    def open_attempts(self):
        return list(self._open.values())

    def end(self, hwnd, reason):
        att = self._open.pop(hwnd, None)
        self.ended.append(reason)
        if att is not None:
            self.card.end(att, reason)


def obs(lines):
    return make_observation(session_id="s", portal="Income Tax",
                            url="https://eportal.incometax.gov.in/iec/foservices/#/login/password", title="t",
                            source="uia", lines=list(lines), result={"profile": {}, "datasets": []},
                            profile={}, draft={}, ts=1.0, today="2026-09-29")


class World:
    def __init__(self):
        self.att = Attempt(pan="ABCPD1234E", hwnd=1, session_id="s", client_id=7, saved_row=True, opened_at=0.0)
        self.worked, self.updates, self.closed, self.after = [], [], [], []
        self.t = 0.0
        self.opener = _Opener(self.att)
        self.card = SccCard(_Db(), open_card=lambda *a: True, close_card=self.closed.append,
                            on_worked=lambda att, label: self.worked.append(label),
                            update_card=lambda *a, **kw: self.updates.append((a, kw)),
                            on_none=lambda att: self.opener.end(att.hwnd, "typed own"),
                            echo=lambda s: None, clock=lambda: self.t)
        self.opener.card = self.card
        self.card.open(self.att)
        self.which = WhichOne(self.card, lambda att: self.reader.copied_since_refusal(att),
                              then=lambda att, kind, detail: self.after.append(kind))
        self.reader = OutcomeReader(self.opener, self.card, on_outcome=self.which.on_outcome,
                                    is_window=lambda h: True)

    def copy(self, label):
        self.t += 5.0
        self.card.note_clipboard(TEXT[label])

    def read(self, *lines):
        return self.reader.observe(obs(lines), 1)

    def last_ask(self):
        asks = [kw["ask"] for _, kw in self.updates if "ask" in kw]
        return asks[-1] if asks else None


class TestWhichOne(unittest.TestCase):
    def test_exactly_one_row_copied_is_credited_without_asking(self):
        w = World()
        w.copy("Combo 3")
        self.assertEqual(w.read(WORKED), "worked")
        self.assertEqual(w.worked, ["Combo 3"])
        self.assertIsNone(w.last_ask())
        self.assertIn("worked: Combo 3", w.att.ledger)
        self.assertEqual(w.after, ["worked"])            # the next listener still hears the outcome

    def test_the_same_row_copied_twice_is_still_one_distinct_row(self):
        w = World()
        w.copy("Combo 1")
        w.copy("Combo 1")
        w.read(WORKED)
        self.assertEqual(w.worked, ["Combo 1"])

    def test_several_rows_copied_the_card_asks_which_and_staff_answer_with_a_row(self):
        w = World()
        w.copy("Combo 1")
        w.copy(SAVED_ROW_LABEL)
        w.read(WORKED)
        self.assertEqual(w.worked, [])
        self.assertEqual(w.last_ask(), ["Combo 1", SAVED_ROW_LABEL])
        (aid, failed, nxt, message, stop), _ = w.updates[-1]
        self.assertEqual((nxt, stop, message), (None, False, get_rules().message("ask_which")))
        self.assertTrue(message)
        self.assertIsNotNone(w.card.row_worked(w.att.attempt_id, SAVED_ROW_LABEL))
        self.assertEqual(w.worked, [SAVED_ROW_LABEL])
        self.assertIsNone(w.card.asking(w.att.attempt_id))

    def test_nothing_copied_the_card_asks_and_none_saves_nothing_and_ends_the_attempt(self):
        w = World()
        w.read(WORKED)
        self.assertEqual(w.last_ask(), [])
        self.assertEqual(w.card.asking(w.att.attempt_id), [])
        self.assertIsNotNone(w.card.none_typed(w.att.attempt_id))
        self.assertEqual(w.worked, [])
        self.assertEqual(w.opener.ended, ["typed own"])
        self.assertEqual(w.closed, [w.att.attempt_id])   # the card is taken down
        self.assertIn("typed own", w.att.ledger)

    def test_none_is_ignored_when_the_card_is_not_asking(self):
        w = World()
        self.assertIsNone(w.card.none_typed(w.att.attempt_id))
        self.assertIsNone(w.card.none_typed("no-such-attempt"))
        self.assertEqual(w.opener.ended, [])

    def test_copies_before_the_last_wrong_password_do_not_count(self):
        w = World()
        w.copy("Combo 1")
        self.assertEqual(w.read(WRONG), "wrong_password")
        w.copy("Combo 3")
        w.read(WORKED)
        self.assertEqual(w.worked, ["Combo 3"])

    def test_a_refused_row_is_never_credited_in_the_same_attempt(self):
        w = World()
        w.copy("Combo 1")
        w.read(WRONG)
        w.copy("Combo 1")                                 # copied again after its x
        w.read(WORKED)
        self.assertEqual(w.worked, [])
        self.assertEqual(w.last_ask(), [])                # asked, with the x row left out
        self.assertIsNone(w.card.row_worked(w.att.attempt_id, "Combo 1"))
        self.assertEqual(w.worked, [])
        self.assertIsNotNone(w.card.row_worked(w.att.attempt_id, "Combo 3"))
        self.assertEqual(w.worked, ["Combo 3"])

    def test_a_refused_row_among_several_copies_is_left_out_of_the_pick(self):
        w = World()
        w.copy("Combo 3")
        w.read(WRONG)                                     # Combo 3 x
        w.copy("Combo 1")
        w.read(WRONG_2)                                   # Combo 1 x
        w.copy("Combo 3")
        w.copy(SAVED_ROW_LABEL)
        w.read(WORKED)
        self.assertEqual(w.worked, [SAVED_ROW_LABEL])     # Combo 3 is x: one row left, credited

    def test_a_row_staff_already_credited_is_not_asked_about_again(self):
        w = World()
        w.copy("Combo 1")
        w.copy("Combo 3")
        w.card.row_worked(w.att.attempt_id, "Combo 3")
        w.read(WORKED)
        self.assertEqual(w.worked, ["Combo 3"])
        self.assertIsNone(w.last_ask())

    def test_other_outcomes_are_passed_on_without_a_credit(self):
        w = World()
        w.copy("Combo 1")
        w.read(WRONG)
        self.assertEqual(w.worked, [])
        self.assertEqual(w.after, ["wrong_password"])

    def test_the_ledger_and_card_messages_never_hold_a_password(self):
        w = World()
        w.copy("Combo 1")
        w.copy("Combo 3")
        w.read(WORKED)
        dump = repr(w.att.ledger) + repr(w.updates)
        for text in TEXT.values():
            self.assertNotIn(text, dump)


class TestPickRows(unittest.TestCase):
    def test_distinct_in_first_copied_order_without_refused_rows(self):
        self.assertEqual(pick_rows(["A", "B", "A", "C"], ["B"]), ["A", "C"])
        self.assertEqual(pick_rows([], []), [])


class TestUpdateMessage(unittest.TestCase):
    def test_ask_is_sent_as_labels_and_dropped_when_stopped(self):
        bridge = MagicMock()
        bridge.broadcast.return_value = 1
        with patch("ui.ws_bridge.get_active_bridge", return_value=bridge):
            self.assertTrue(automation.update_scc_card("att-1", ["Combo 1"], None, "m", False, ask=["Combo 3"]))
            self.assertEqual(bridge.broadcast.call_args[0][0]["ask"], ["Combo 3"])
            automation.update_scc_card("att-1", [], None, "m", False, ask=[])
            self.assertEqual(bridge.broadcast.call_args[0][0]["ask"], [])
            automation.update_scc_card("att-1", [], None, "m", True, ask=["Combo 3"])
            self.assertNotIn("ask", bridge.broadcast.call_args[0][0])
            automation.update_scc_card("att-1", [], "Combo 3", "m", False)
            self.assertNotIn("ask", bridge.broadcast.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
