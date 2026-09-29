"""SCC-U step 3 (W5-3): the desktop feeds the extension's MECP card and keeps the copy ledger.
Passwords here are fictional. The ledger holds labels only; nothing is logged."""
import unittest
from unittest.mock import MagicMock, patch

import automation
import clipboard_watch
from core.scc import Attempt, SccCard
from core.scc.card import SAVED_ROW_LABEL

ITR = {"id": 3, "name": "Income Tax", "login_page_link": "https://eportal.incometax.gov.in/iec/foservices/#/login",
       "password_column_id": "pw"}
COMBOS = [
    {"id": 1, "label": "Combo 1", "value": "Fict#One1"},
    {"id": 2, "label": "Combo 2", "value": ""},
    {"id": 3, "label": "Combo 3", "value": "Fict#Three3"},
    {"id": 4, "label": "Combo 4", "value": "Fict#One1"},
]


class _Db:
    def __init__(self, combos=COMBOS, saved="Fict#Saved9", services=(ITR,)):
        self._combos, self._saved, self._services = combos, saved, list(services)

    def get_services(self):
        return self._services

    def generate_scc_passwords(self, pan):
        return list(self._combos)

    def get_client(self, client_id):
        return {"id": client_id, "values": {"pw": self._saved}}


def _attempt(client_id=7, saved_row=True):
    return Attempt(pan="ABCPD1234E", hwnd=1, session_id="s", client_id=client_id, saved_row=saved_row, opened_at=0.0)


class Harness:
    def __init__(self, db=None, **kw):
        self.opened, self.closed, self.suppressed, self.released, self.worked, self.closed_cb = [], [], [], [], [], []
        self.echoed = []
        self.card = SccCard(
            db or _Db(),
            open_card=lambda *a: self.opened.append(a) or True,
            close_card=self.closed.append,
            suppress=lambda cid, s: self.suppressed.append((cid, s)),
            release=self.released.append,
            on_worked=lambda att, label: self.worked.append((att.attempt_id, label)),
            on_closed=self.closed_cb.append,
            echo=self.echoed.append, **kw)


class TestRows(unittest.TestCase):
    def test_rows_are_the_generated_combos_without_empties_or_repeats_plus_the_saved_row(self):
        h = Harness()
        att = _attempt()
        self.assertTrue(h.card.open(att))
        service, pan, rows, title, client_id, attempt_id = h.opened[0]
        self.assertEqual(pan, "ABCPD1234E")
        self.assertEqual([r["label"] for r in rows], ["Combo 1", "Combo 3", SAVED_ROW_LABEL])
        self.assertEqual([r["value"] for r in rows], ["Fict#One1", "Fict#Three3", "Fict#Saved9"])
        self.assertEqual(title, "PAN: ABCPD1234E")
        self.assertEqual((client_id, attempt_id), (7, att.attempt_id))

    def test_no_saved_row_when_the_attempt_says_none_or_the_pan_is_unregistered(self):
        for att in (_attempt(saved_row=False), _attempt(client_id=None)):
            h = Harness()
            h.card.open(att)
            self.assertNotIn(SAVED_ROW_LABEL, [r["label"] for r in h.opened[0][2]])
        self.assertTrue(h.opened[0][3].endswith("(Unregistered)"))

    def test_a_saved_password_equal_to_a_combo_is_not_shown_twice(self):
        h = Harness(_Db(saved="Fict#Three3"))
        h.card.open(_attempt())
        self.assertEqual([r["label"] for r in h.opened[0][2]], ["Combo 1", "Combo 3"])

    def test_no_rows_or_no_income_tax_service_means_no_card(self):
        for db in (_Db(combos=[], saved=""), _Db(services=[{"id": 9, "name": "GST", "login_page_link": "https://gst.gov.in"}])):
            h = Harness(db)
            self.assertFalse(h.card.open(_attempt()))
            self.assertEqual(h.opened, [])

    def test_no_browser_reached_is_reported_not_raised(self):
        h = Harness()
        h.card._open_card = lambda *a: False
        self.assertFalse(h.card.open(_attempt()))

    def test_only_generated_combos_are_used_no_hardcoded_defaults(self):
        h = Harness(_Db(combos=[{"id": 1, "label": "Only", "value": "Fict#Only"}], saved=""))
        h.card.open(_attempt())
        self.assertEqual([r["value"] for r in h.opened[0][2]], ["Fict#Only"])


class TestSuppressAndEnd(unittest.TestCase):
    def test_the_client_is_suppressed_while_the_attempt_is_open_and_released_at_its_end(self):
        h = Harness()
        att = _attempt()
        h.card.open(att)
        self.assertEqual([c for c, _ in h.suppressed], [7])
        h.card.end(att, "ttl")
        self.assertEqual(h.released, [7])
        self.assertEqual(h.closed, [att.attempt_id])

    def test_an_unregistered_pan_suppresses_nothing(self):
        h = Harness()
        att = _attempt(client_id=None)
        h.card.open(att)
        h.card.end(att, "left")
        self.assertEqual((h.suppressed, h.released), ([], []))

    def test_end_after_the_card_was_closed_by_staff_does_not_send_a_close(self):
        h = Harness()
        att = _attempt()
        h.card.open(att)
        h.card.end(att, "closed")
        self.assertEqual(h.closed, [])
        self.assertEqual(h.released, [7])

    def test_end_of_an_attempt_that_never_had_a_card_does_nothing(self):
        h = Harness(_Db(combos=[], saved=""))
        att = _attempt()
        h.card.open(att)
        h.card.end(att, "ttl")
        self.assertEqual((h.released, h.closed), ([], []))

    def test_rows_are_forgotten_at_the_end(self):
        h = Harness()
        att = _attempt()
        h.card.open(att)
        h.card.end(att, "ttl")
        self.assertIsNone(h.card.note_clipboard("Fict#One1"))
        self.assertEqual(h.card._rows, {})


class TestLedger(unittest.TestCase):
    def setUp(self):
        self.t = [100.0]
        self.h = Harness(clock=lambda: self.t[0])
        self.att = _attempt()
        self.h.card.open(self.att)

    def test_a_copied_row_is_recorded_by_label(self):
        self.assertEqual(self.h.card.note_clipboard("Fict#Three3"), "Combo 3")
        self.assertEqual(self.h.card.ledger(self.att.attempt_id), ["copied: Combo 3"])

    def test_the_saved_row_is_recorded_too(self):
        self.h.card.note_clipboard("Fict#Saved9")
        self.assertEqual(self.att.ledger, [f"copied: {SAVED_ROW_LABEL}"])

    def test_other_text_and_the_pan_are_not_recorded(self):
        for text in ("", "hello", "ABCPD1234E", " Fict#One1"):
            self.assertIsNone(self.h.card.note_clipboard(text))
        self.assertEqual(self.att.ledger, [])

    def test_the_repeated_change_event_of_one_copy_counts_once_but_a_later_copy_counts_again(self):
        self.h.card.note_clipboard("Fict#One1")
        self.t[0] += 0.5
        self.h.card.note_clipboard("Fict#One1")
        self.t[0] += 5
        self.h.card.note_clipboard("Fict#One1")
        self.assertEqual(self.att.ledger, ["copied: Combo 1", "copied: Combo 1"])

    def test_the_ledger_and_the_output_never_hold_the_text(self):
        for text in ("Fict#One1", "Fict#Three3", "Fict#Saved9"):
            self.h.card.note_clipboard(text)
        self.h.card.row_worked(self.att.attempt_id, "Combo 1")
        blob = " ".join(self.att.ledger + self.h.echoed)
        for text in ("Fict#One1", "Fict#Three3", "Fict#Saved9"):
            self.assertNotIn(text, blob)


class TestRowWorked(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.att = _attempt()
        self.h.card.open(self.att)

    def test_a_click_on_a_row_of_the_open_attempt_reaches_on_worked(self):
        self.assertIs(self.h.card.row_worked(self.att.attempt_id, "Combo 3"), self.att)
        self.assertEqual(self.h.worked, [(self.att.attempt_id, "Combo 3")])
        self.assertEqual(self.att.ledger, ["worked: Combo 3"])

    def test_unknown_attempt_or_row_is_ignored(self):
        self.assertIsNone(self.h.card.row_worked("nope", "Combo 3"))
        self.assertIsNone(self.h.card.row_worked(self.att.attempt_id, "Combo 2"))
        self.assertIsNone(self.h.card.row_worked(self.att.attempt_id, "made up"))
        self.assertEqual(self.h.worked, [])

    def test_a_click_after_the_attempt_ended_is_ignored(self):
        self.h.card.end(self.att, "ttl")
        self.assertIsNone(self.h.card.row_worked(self.att.attempt_id, "Combo 3"))

    def test_x_on_the_card_closes_the_attempt_through_on_closed(self):
        self.assertIs(self.h.card.card_closed(self.att.attempt_id), self.att)
        self.assertEqual(self.h.closed_cb, [self.att])
        self.assertIsNone(self.h.card.card_closed("nope"))


class TestMessage(unittest.TestCase):
    def _bridge(self, reached=1):
        bridge = MagicMock()
        bridge.broadcast.return_value = reached
        return bridge

    def test_the_mecp_message_carries_the_rows_and_the_attempt_id(self):
        bridge = self._bridge()
        rows = [{"id": 1, "label": "Combo 1", "value": "Fict#One1"}]
        with patch("ui.ws_bridge.get_active_bridge", return_value=bridge):
            self.assertTrue(automation.send_scc_card(ITR, "ABCPD1234E", rows, "PAN: ABCPD1234E", 7, "att-1"))
        msg = bridge.broadcast.call_args[0][0]
        self.assertEqual((msg["type"], msg["mode"], msg["scc_mode"]), ("autofill", "mecp", True))
        self.assertEqual((msg["userid"], msg["password"], msg["attempt_id"], msg["client_id"]), ("ABCPD1234E", "", "att-1", 7))
        self.assertEqual(msg["scc_combos"], rows)
        self.assertEqual(msg["client_name"], "PAN: ABCPD1234E")
        bridge.send_first.assert_not_called()

    def test_no_browser_or_no_rows_sends_nothing_and_opens_no_browser(self):
        with patch("ui.ws_bridge.get_active_bridge", return_value=self._bridge(reached=0)), \
                patch.object(automation, "open_in_default_browser") as launch:
            self.assertFalse(automation.send_scc_card(ITR, "ABCPD1234E", [{"id": 1, "label": "a", "value": "b"}], "n", None, "a"))
            self.assertFalse(automation.send_scc_card(ITR, "ABCPD1234E", [], "n", None, "a"))
            launch.assert_not_called()
        with patch("ui.ws_bridge.get_active_bridge", return_value=None):
            self.assertFalse(automation.send_scc_card(ITR, "ABCPD1234E", [{"id": 1}], "n", None, "a"))

    def test_close_message_names_only_the_attempt(self):
        bridge = self._bridge()
        with patch("ui.ws_bridge.get_active_bridge", return_value=bridge):
            self.assertTrue(automation.close_scc_card("att-1"))
            self.assertFalse(automation.close_scc_card(""))
        bridge.broadcast.assert_called_once_with({"type": "scc_card_close", "attempt_id": "att-1"})

    def test_the_existing_client_detail_payload_is_unchanged(self):
        msg = automation._extension_payload(ITR, "U", "P", 7, "mecp", True, [{"id": 1}])
        self.assertNotIn("attempt_id", msg)
        self.assertEqual(msg["scc_combos"], [{"id": 1}])


class TestPanCopyDoesNotArmSca(unittest.TestCase):
    def test_an_open_attempt_suppresses_its_client_until_it_ends(self):
        clipboard_watch._suppressed_until.clear()
        h = Harness()
        att = _attempt()
        h.card = SccCard(_Db(), open_card=lambda *a: True, close_card=lambda a: None,
                         suppress=clipboard_watch.suppress_client, release=clipboard_watch.release_client)
        h.card.open(att)
        self.assertTrue(clipboard_watch.is_suppressed(7))
        h.card.end(att, "ttl")
        self.assertFalse(clipboard_watch.is_suppressed(7))
        clipboard_watch._suppressed_until.clear()


if __name__ == "__main__":
    unittest.main()
