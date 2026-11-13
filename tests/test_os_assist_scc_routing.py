"""O3-4: SCC card routing between the extension and the OS MECP card (automation.send/update/close_scc_card)."""
import unittest
from unittest import mock

import automation
from core.os_assist import launcher as la
from core.scc import Attempt, SccCard

ITR = {"id": 3, "name": "Income Tax", "login_page_link": "https://eportal.incometax.gov.in/iec/foservices/#/login"}
ROWS = [{"id": 1, "label": "Combo 1", "value": "fictional-1"}]


def _engine(name):
    return mock.patch.object(automation, "assist_engine", return_value=name)


class TestRouting(unittest.TestCase):
    def test_os_send_goes_to_launcher_with_hwnd(self):
        with _engine("os"), mock.patch.object(la, "launch_scc", return_value=True) as ls, \
                mock.patch("ui.ws_bridge.get_active_bridge") as gb:
            self.assertTrue(automation.send_scc_card(ITR, "ABCPD1234E", ROWS, "PAN: X", 7, "att-1", hwnd=555))
        gb.assert_not_called()
        ls.assert_called_once_with(ITR, "ABCPD1234E", ROWS, "PAN: X", 7, "att-1",
                                   hwnd=555, open_tab=False, on_error=None)

    def test_os_update_and_close_go_to_the_os_card(self):
        with _engine("os"), mock.patch("ui.os_assist.mecp_card.update_card", return_value=True) as uc, \
                mock.patch("ui.os_assist.mecp_card.close_card", return_value=True) as cc, \
                mock.patch("ui.ws_bridge.get_active_bridge") as gb:
            self.assertTrue(automation.update_scc_card("att-1", ["Combo 1"], "Combo 2", "m", False, ask=["Combo 2"]))
            self.assertTrue(automation.close_scc_card("att-1"))
        uc.assert_called_once_with("att-1", ["Combo 1"], "Combo 2", "m", False, ["Combo 2"])
        cc.assert_called_once_with("att-1")
        gb.assert_not_called()

    def test_extension_mode_unchanged(self):
        bridge = mock.Mock()
        bridge.broadcast.return_value = 1
        with _engine("extension"), mock.patch.object(la, "launch_scc") as ls, \
                mock.patch("ui.ws_bridge.get_active_bridge", return_value=bridge):
            self.assertTrue(automation.send_scc_card(ITR, "ABCPD1234E", ROWS, "n", 7, "att-1", hwnd=555))
            self.assertTrue(automation.close_scc_card("att-1"))
            self.assertTrue(automation.update_scc_card("att-1", [], None, "m", False))
        ls.assert_not_called()
        self.assertEqual([c.args[0]["type"] for c in bridge.broadcast.call_args_list],
                         ["autofill", "scc_card_close", "scc_card_update"])


class TestLaunchScc(unittest.TestCase):
    def _launcher(self, finder=lambda url: 0):
        opened = []
        lc = la.Launcher(opener=lambda u, b: opened.append(u), finder=finder, sleep=lambda s: None,
                         spawn=lambda fn: fn())
        shown = []
        lc._on_qt = lambda fn: fn()
        lc._show_scc = lambda *a: shown.append(a)
        return lc, opened, shown

    def test_hwnd_shows_directly(self):
        lc, opened, shown = self._launcher()
        self.assertTrue(lc.launch_scc(ITR, "P", ROWS, "n", 7, "att-1", hwnd=42))
        self.assertEqual(opened, [])
        self.assertEqual(shown[0][0], 42)

    def test_no_hwnd_no_open_tab_is_refused(self):
        lc, opened, shown = self._launcher()
        self.assertFalse(lc.launch_scc(ITR, "P", ROWS, "n", 7, "att-1"))
        self.assertEqual((opened, shown), ([], []))

    def test_open_tab_opens_page_and_waits_for_window(self):
        lc, opened, shown = self._launcher(finder=lambda url: 77)
        self.assertTrue(lc.launch_scc(ITR, "P", ROWS, "n", 7, "att-1", open_tab=True))
        self.assertEqual(opened, [ITR["login_page_link"]])
        self.assertEqual(shown[0][0], 77)

    def test_open_tab_reuses_a_window_already_on_the_login_page(self):
        lc, opened, shown = self._launcher()
        lc._reuser = lambda service: 88
        self.assertTrue(lc.launch_scc(ITR, "P", ROWS, "n", 7, "att-1", open_tab=True))
        self.assertEqual(opened, [])
        self.assertEqual(shown[0][0], 88)

    def test_open_tab_timeout_reports_error(self):
        errs = []
        t = iter(range(0, 1000, 10))
        lc = la.Launcher(opener=lambda u, b: None, finder=lambda u: 0, sleep=lambda s: None,
                         clock=lambda: next(t), spawn=lambda fn: fn())
        self.assertTrue(lc.launch_scc(ITR, "P", ROWS, "n", 7, "att-1", open_tab=True, on_error=errs.append))
        self.assertEqual(errs, [la.timeout_message(ITR)])

    def test_empty_rows_refused(self):
        lc, _, _ = self._launcher()
        self.assertFalse(lc.launch_scc(ITR, "P", [], "n", 7, "att-1", hwnd=42))


class TestSccCardHwnd(unittest.TestCase):
    def test_attempt_hwnd(self):
        card = SccCard.__new__(SccCard)
        import threading
        card._lock = threading.Lock()
        card._attempts = {"a": Attempt.__new__(Attempt)}
        card._attempts["a"].hwnd = 99
        self.assertEqual(card.attempt_hwnd("a"), 99)
        self.assertEqual(card.attempt_hwnd("zzz"), 0)


if __name__ == "__main__":
    unittest.main()
