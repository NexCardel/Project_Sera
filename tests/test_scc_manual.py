"""SCC-U W5-8: Client Detail's MECP on an unverified Income Tax client opens the SCC card from the
desktop. A manual attempt when SGT has none for that PAN (only "This one worked" saves), SGT's own
attempt when it has one, the plain MECP for everyone else. Passwords here are fictional."""
import unittest
from unittest.mock import MagicMock, patch

import automation
from core.scc import AttemptOpener, ClientInfo, OutcomeReader, SccCard, manual
from core.scc.attempts import ATTEMPT_TTL_SEC
from core.sgt_i.observation import make_observation

PAN = "ABCPD1234E"
PW_URL = "https://eportal.incometax.gov.in/iec/foservices/#/login/password"
ITR = {"id": 3, "name": "Income Tax", "login_page_link": "https://eportal.incometax.gov.in/iec/foservices/#/login",
       "password_column_id": "pw"}


class _Db:
    def get_services(self):
        return [ITR]

    def generate_scc_passwords(self, pan):
        return [{"id": 1, "label": "Combo 1", "value": "Fict#One1"}, {"id": 2, "label": "Combo 2", "value": "Fict#Two2"}]

    def get_client(self, client_id):
        return {"id": client_id, "values": {"pw": "Fict#Saved9"}}


def page(pan=PAN, session="s1"):
    profile = {"pan": {"value": pan, "confidence": 95, "spec": "itr_pan"}}
    return make_observation(session_id=session, portal="Income Tax", url=PW_URL, title="t", source="uia",
                            lines=["PAN", pan], result={"profile": profile, "datasets": []},
                            profile={}, draft={}, ts=1.0, today="2026-09-29")


class Env:
    def __init__(self, clients=None):
        self.ended, self.worked, self.sent = [], [], []
        self.clock = [100.0]
        clients = clients or {}
        self.opener = AttemptOpener(lambda pan: clients.get(pan), on_end=lambda a, why: self.ended.append((a.pan, why)),
                                    monotonic=lambda: self.clock[0])
        self.card = SccCard(_Db(), open_card=lambda *a, **kw: self.sent.append((a, kw)) or True,
                            close_card=lambda aid: None, on_worked=lambda att, label: self.worked.append((att.pan, label)),
                            echo=lambda s: None)


class TestRegisterManual(unittest.TestCase):
    def test_no_sgt_attempt_registers_a_manual_one_under_a_made_up_window_id(self):
        e = Env()
        att, is_new = e.opener.register_manual("abcpd1234e", 7)
        self.assertTrue(is_new)
        self.assertTrue(att.manual)
        self.assertLess(att.hwnd, 0)
        self.assertEqual((att.pan, att.client_id, att.session_id, att.saved_row), (PAN, 7, "", True))
        self.assertIs(e.opener.attempt_for(att.hwnd), att)

    def test_an_attempt_sgt_already_has_for_that_pan_is_reused(self):
        e = Env(clients={PAN: ClientInfo(7, False)})
        self.assertEqual(e.opener.observe(page(), 5, None), "opened")
        att, is_new = e.opener.register_manual(PAN, 7)
        self.assertFalse(is_new)
        self.assertEqual((att.hwnd, att.manual), (5, False))
        self.assertEqual(len(e.opener.open_attempts()), 1)

    def test_a_second_click_replaces_the_first_manual_attempt(self):
        e = Env()
        first, _ = e.opener.register_manual(PAN, 7)
        second, is_new = e.opener.register_manual(PAN, 7)
        self.assertTrue(is_new)
        self.assertEqual([a.attempt_id for a in e.opener.open_attempts()], [second.attempt_id])
        self.assertEqual(e.ended, [(PAN, "no_conclusion")])
        self.assertNotEqual(first.attempt_id, second.attempt_id)

    def test_a_manual_attempt_ends_after_ten_minutes_when_sgt_never_sees_it(self):
        e = Env()
        e.opener.register_manual(PAN, None)
        e.clock[0] += ATTEMPT_TTL_SEC + 1
        e.opener.expire()
        self.assertEqual(e.opener.open_attempts(), [])
        self.assertEqual(e.ended, [(PAN, "no_conclusion")])

    def test_sgt_adopts_the_manual_attempt_when_it_reads_that_pans_password_page(self):
        e = Env(clients={PAN: ClientInfo(7, False)})
        att, _ = e.opener.register_manual(PAN, 7)
        self.assertEqual(e.opener.observe(page(session="s9"), 5, None), "adopted")
        self.assertEqual((att.hwnd, att.session_id, att.manual), (5, "s9", False))
        self.assertIs(e.opener.attempt_for(5), att)
        self.assertEqual(len(e.opener.open_attempts()), 1)
        self.assertEqual(e.opener.observe(page(session="s9"), 5, None), "kept")

    def test_another_pan_on_the_password_page_leaves_the_manual_attempt_alone(self):
        e = Env()
        att, _ = e.opener.register_manual(PAN, 7)
        self.assertEqual(e.opener.observe(page(pan="XYZPK9876Q"), 5, None), "opened")
        self.assertTrue(att.manual)
        self.assertEqual(len(e.opener.open_attempts()), 2)

    def test_the_outcome_reader_does_not_sweep_a_manual_attempt_as_a_closed_window(self):
        e = Env()
        att, _ = e.opener.register_manual(PAN, 7)
        reader = OutcomeReader(e.opener, e.card, is_window=lambda h: False)
        reader.observe(page(pan="XYZPK9876Q"), 5, None)
        self.assertIs(e.opener.attempt_for(att.hwnd), att)


class TestManualCard(unittest.TestCase):
    def test_the_card_gets_the_generated_rows_and_the_open_tab_option(self):
        e = Env()
        att, _ = e.opener.register_manual(PAN, 7)
        self.assertTrue(e.card.open(att, open_tab=True, on_error=None))
        (service, pan, rows, title, client_id, attempt_id), kw = e.sent[0]
        self.assertEqual([r["label"] for r in rows], ["Combo 1", "Combo 2", "Saved password"])
        self.assertEqual((pan, client_id, attempt_id, kw["open_tab"]), (PAN, 7, att.attempt_id, True))

    def test_this_one_worked_is_the_save_path_of_a_manual_attempt(self):
        e = Env()
        att, _ = e.opener.register_manual(PAN, 7)
        e.card.open(att, open_tab=True)
        self.assertIs(e.card.row_worked(att.attempt_id, "Combo 2"), att)
        self.assertEqual(e.worked, [(PAN, "Combo 2")])

    def test_a_card_that_could_not_be_built_says_so(self):
        e = Env()
        e.card._db.generate_scc_passwords = lambda pan: []
        att, _ = e.opener.register_manual(PAN, None)
        self.assertFalse(e.card.open(att, open_tab=True))


class TestSendSccCard(unittest.TestCase):
    ROWS = [{"id": 1, "label": "Combo 1", "value": "Fict#One1"}]

    def test_open_tab_goes_to_one_browser_with_retry_and_carries_the_flag(self):
        with patch.object(automation, "_deliver_to_extension") as deliver:
            self.assertTrue(automation.send_scc_card(ITR, PAN, self.ROWS, "PAN: X", 7, "att-1", open_tab=True))
        payload = deliver.call_args[0][0]
        self.assertTrue(payload["open_tab"] and payload["scc_mode"])
        self.assertEqual((payload["userid"], payload["password"], payload["attempt_id"]), (PAN, "", "att-1"))

    def test_without_open_tab_it_is_the_broadcast_with_no_flag(self):
        bridge = MagicMock()
        bridge.broadcast.return_value = 1
        with patch("ui.ws_bridge.get_active_bridge", return_value=bridge), \
                patch.object(automation, "_deliver_to_extension") as deliver:
            self.assertTrue(automation.send_scc_card(ITR, PAN, self.ROWS, "PAN: X", 7, "att-1"))
        deliver.assert_not_called()
        self.assertNotIn("open_tab", bridge.broadcast.call_args[0][0])


class TestManualHook(unittest.TestCase):
    def tearDown(self):
        manual.set_opener(None)

    def test_no_opener_or_a_failing_one_means_false_so_the_window_falls_back_to_plain_mecp(self):
        manual.set_opener(None)
        self.assertFalse(manual.open_card(PAN, 7))
        manual.set_opener(MagicMock(side_effect=RuntimeError("x")))
        self.assertFalse(manual.open_card(PAN, 7))

    def test_the_opener_result_is_passed_back(self):
        fn = MagicMock(return_value=True)
        manual.set_opener(fn)
        err = object()
        self.assertTrue(manual.open_card(PAN, 7, err))
        fn.assert_called_once_with(PAN, 7, err)


if __name__ == "__main__":
    unittest.main()
