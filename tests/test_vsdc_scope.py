"""
tests/test_vsdc_scope.py - VSDC may only ever look at the two tax portals.

Before this gate existed, match_url_crosshair() had a second loop that ignored the host
pattern, so any page whose URL contained "home", "profile" or "dashboard" resolved to an
ITR crosshair - a bank's myProfile page, Facebook's /home, a local PDF. Once matched, VSDC
screenshotted, OCR'd and read the page through UI Automation, and could seed a client
identity from it.
"""

import os
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image

from core.vsdc.vsdc_assembler import VisualSessionAssembler
from core.vsdc.vsdc_router import VSDCRouter
from core.vsdc.vsdc_scope import extract_host, is_in_scope_url, portal_for_url


class TestScopeHostParsing(unittest.TestCase):
    def setUp(self):
        self._env = patch.dict(os.environ)
        self._env.start()
        os.environ.pop("VSDC_ALLOW_LOCAL_TEST", None)

    def tearDown(self):
        self._env.stop()

    def test_the_two_portals_are_in_scope_however_the_browser_shows_them(self):
        for url in (
            "https://eportal.incometax.gov.in/iec/foservices/#/dashboard",
            "eportal.incometax.gov.in/iec/foservices/#/dashboard/itrStatus",   # Chrome hides the scheme
            "https://www.incometax.gov.in/iec/foportal/",
            "https://services.gst.gov.in/services/auth/fowelcome",
            "return.gst.gov.in/returns/auth/gstr3b",
            "https://eportal.incometax.gov.in:443/x",
            "https://EPORTAL.IncomeTax.GOV.IN./x",
        ):
            self.assertTrue(is_in_scope_url(url), url)
        self.assertEqual(portal_for_url("eportal.incometax.gov.in/x"), "Income Tax")
        self.assertEqual(portal_for_url("return.gst.gov.in/x"), "GST Portal")

    def test_a_url_that_merely_mentions_a_portal_is_out_of_scope(self):
        for url in (
            "https://www.google.com/search?q=incometax+home",
            "https://evil.example/?u=https://eportal.incometax.gov.in/",
            "https://evil.example/#eportal.incometax.gov.in",
            "https://www.youtube.com/watch?v=abc&t=incometax-dashboard",
        ):
            self.assertFalse(is_in_scope_url(url), url)

    def test_lookalike_domains_and_userinfo_tricks_are_refused(self):
        for url in (
            "https://incometax.gov.in.evil.example/login",      # portal name as a subdomain of someone else
            "https://evilincometax.gov.in/",                    # no dot boundary
            "https://eportal.incometax.gov.in@evil.example/",   # user-info trick: the host is evil.example
        ):
            self.assertFalse(is_in_scope_url(url), url)
        self.assertEqual(extract_host("https://eportal.incometax.gov.in@evil.example/"), "evil.example")

    def test_other_sites_and_non_urls_are_out_of_scope(self):
        for url in ("https://netbanking.hdfcbank.com/myProfile/profileDetail", "https://www.facebook.com/home",
                    "incometax home", "chrome://settings", "about:blank", "", None):
            self.assertFalse(is_in_scope_url(url), url)

    def test_local_test_pages_need_an_explicit_opt_in(self):
        local = ("file:///C:/x/p.html", "http://localhost:3000/dashboard", "C:\\x\\p.html")
        for url in local:
            self.assertFalse(is_in_scope_url(url), url)
        os.environ["VSDC_ALLOW_LOCAL_TEST"] = "1"
        for url in local:
            self.assertTrue(is_in_scope_url(url), url)
        # ...and the opt-in never widens the scope to real third-party sites.
        self.assertFalse(is_in_scope_url("https://www.facebook.com/home"))
        self.assertFalse(is_in_scope_url("https://www.google.com/search?q=incometax"))


class TestRouterNeverTouchesOutOfScopePages(unittest.TestCase):
    """The behaviour that matters: no screenshot, no OCR, no identity - nothing."""

    OUT_OF_SCOPE = (
        ("Client profile - Zoho Books - Google Chrome", "https://books.zoho.in/app/incometax#/contacts/profile"),
        ("Personal information - HDFC Bank - Google Chrome", "https://netbanking.hdfcbank.com/personal-information"),
        ("Home / Facebook - Google Chrome", "https://www.facebook.com/home"),
        ("incometax home - Google Search - Google Chrome", "https://www.google.com/search?q=incometax+home"),
        ("Inbox - Gmail - Google Chrome", "https://mail.google.com/mail/u/0/#inbox/incometax-profile"),
        ("profile-summary.pdf - Google Chrome", "file:///C:/Users/Nex/Documents/profile-summary.pdf"),
        ("localhost - Google Chrome", "http://localhost:3000/dashboard"),
    )

    def setUp(self):
        self._env = patch.dict(os.environ)
        self._env.start()
        os.environ.pop("VSDC_ALLOW_LOCAL_TEST", None)

    def tearDown(self):
        self._env.stop()

    def _router(self, title, url):
        ocr = MagicMock()
        ocr.capture_window_image.return_value = Image.new("RGB", (1280, 800), color="white")
        # Text a bank/profile page would happily yield if it were ever read.
        ocr.scan_image.return_value = {
            "text": "RAMESH SHARMA ABCPE1234F Date of Birth 06-Aug-1971",
            "lines": ["RAMESH SHARMA", "ABCPE1234F", "Date of Birth", "06-Aug-1971"],
        }
        events = []
        router = VSDCRouter(ocr_engine=ocr, assembler=VisualSessionAssembler(),
                            on_activity=lambda *a: events.append(a))
        router.get_foreground_info = MagicMock(return_value=(4242, title, "chrome.exe"))
        router.extract_browser_url = MagicMock(return_value=url)
        return router, ocr, events

    def test_no_screenshot_ocr_identity_or_hud_event_on_any_out_of_scope_page(self):
        for title, url in self.OUT_OF_SCOPE:
            router, ocr, events = self._router(title, url)
            for _ in range(3):
                self.assertIsNone(router.evaluate_tick(), url)
            ocr.capture_window_image.assert_not_called()
            ocr.scan_image.assert_not_called()
            self.assertIsNone(router.assembler.client_pan, url)
            self.assertIsNone(router.assembler.client_name, url)
            self.assertEqual(events, [], url)

    def test_the_real_portal_is_still_read(self):
        router, ocr, events = self._router(
            "Income Tax Portal, Government of India - Google Chrome",
            "https://eportal.incometax.gov.in/iec/foservices/#/dashboard/myProfile/profileDetail",
        )
        router.evaluate_tick()
        self.assertTrue(ocr.capture_window_image.called)
        self.assertEqual(router.assembler.client_pan, "ABCPE1234F")

    def test_navigating_from_a_portal_to_another_site_stops_capture_at_once(self):
        router, ocr, _ = self._router(
            "Income Tax Portal, Government of India - Google Chrome",
            "https://eportal.incometax.gov.in/iec/foservices/#/dashboard/myProfile/profileDetail",
        )
        router.evaluate_tick()
        self.assertTrue(ocr.capture_window_image.called)

        ocr.capture_window_image.reset_mock()
        router.extract_browser_url = MagicMock(return_value="https://www.facebook.com/home")
        router.get_foreground_info = MagicMock(return_value=(4242, "Facebook - Google Chrome", "chrome.exe"))
        router.evaluate_tick()
        ocr.capture_window_image.assert_not_called()

    def test_the_grace_period_does_not_follow_the_user_to_another_site(self):
        # Verified on the portal, then the address bar becomes unreadable while the tab's
        # title is no longer portal-like: the grace period must not apply.
        router, ocr, _ = self._router(
            "Income Tax Portal, Government of India - Google Chrome",
            "https://eportal.incometax.gov.in/iec/foservices/#/dashboard",
        )
        router.evaluate_tick()
        ocr.capture_window_image.reset_mock()
        router.extract_browser_url = MagicMock(return_value=None)
        router.get_foreground_info = MagicMock(return_value=(4242, "How to save tax - YouTube - Google Chrome", "chrome.exe"))
        router.evaluate_tick()
        ocr.capture_window_image.assert_not_called()

    def test_the_bare_words_mca_and_traces_no_longer_make_a_window_look_like_a_portal(self):
        # Substring keywords for two other government sites; both matched inside unrelated
        # words ("stack traces", "Amcat"), and neither has a crosshair here.
        from core.vsdc.vsdc_router import PORTAL_KEYWORDS
        self.assertNotIn("mca", PORTAL_KEYWORDS)
        self.assertNotIn("traces", PORTAL_KEYWORDS)


if __name__ == "__main__":
    unittest.main()


class TestAdminAllowlistConfig(unittest.TestCase):
    """A portal that moves to a new domain must be fixable by editing a file, not rebuilding."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ)
        self._env.start()
        os.environ.pop("VSDC_ALLOW_LOCAL_TEST", None)
        os.environ["VSDC_SCOPE_CONFIG"] = os.path.join(self._tmp.name, "vsdc_scope.json")

    def tearDown(self):
        from core.vsdc import vsdc_scope
        self._env.stop()
        vsdc_scope.reload_extra_domains()
        self._tmp.cleanup()

    def _write(self, text):
        from core.vsdc import vsdc_scope
        with open(os.environ["VSDC_SCOPE_CONFIG"], "w", encoding="utf-8") as f:
            f.write(text)
        return vsdc_scope.reload_extra_domains()

    def test_no_file_means_the_built_in_list_only(self):
        from core.vsdc import vsdc_scope
        vsdc_scope.reload_extra_domains()
        self.assertIsNone(portal_for_url("https://efile.newtax.gov.in/x"))
        self.assertEqual(portal_for_url("https://eportal.incometax.gov.in/x"), "Income Tax")

    def test_a_listed_domain_and_its_subdomains_join_the_right_portal(self):
        self._write('{"income_tax": ["newtax.gov.in"], "gst": ["newgst.gov.in"]}')
        self.assertEqual(portal_for_url("https://efile.newtax.gov.in/x"), "Income Tax")
        self.assertEqual(portal_for_url("https://newgst.gov.in/"), "GST Portal")
        self.assertTrue(is_in_scope_url("https://efile.newtax.gov.in/x"))

    def test_look_alikes_of_an_added_domain_are_still_refused(self):
        self._write('{"income_tax": ["newtax.gov.in"]}')
        for url in ("https://newtax.gov.in.evil.example/", "https://evilnewtax.gov.in/",
                    "https://newtax.gov.in@evil.example/", "https://www.google.com/search?q=newtax.gov.in"):
            self.assertIsNone(portal_for_url(url), url)

    def test_entries_that_would_widen_the_scope_by_accident_are_rejected(self):
        self._write('{"income_tax": ["gov.in", "in", "nic.in", "*.gov.in", "https://x.gov.in/", "a.gov", "", 7], "gst": null}')
        for url in ("https://anything.gov.in/", "https://bank.co.in/", "https://x.gov.in/"):
            self.assertIsNone(portal_for_url(url), url)

    def test_a_broken_file_changes_nothing(self):
        for text in ("not json", "[]", '{"income_tax": "newtax.gov.in"}'):
            self._write(text)
            self.assertIsNone(portal_for_url("https://efile.newtax.gov.in/x"), text)
            self.assertEqual(portal_for_url("https://www.incometax.gov.in/x"), "Income Tax")


class TestPortalLogoCue(unittest.TestCase):
    def test_the_two_real_logo_names_are_recognised(self):
        from core.vsdc.vsdc_scope import portal_logo_cue
        self.assertEqual(portal_logo_cue(["Skip to content", "E Filing logo", "Login"]), "Income Tax")
        self.assertEqual(portal_logo_cue(["Goods and Services Tax Home", "Services"]), "GST Portal")
        self.assertEqual(portal_logo_cue(["[Image] (navbarBrandPart1 logomargin) E Filing logo"]), "Income Tax")

    def test_ordinary_text_is_not_a_cue(self):
        from core.vsdc.vsdc_scope import portal_logo_cue
        for lines in (
            ["How to e-file your income tax return - a complete guide"],
            ["Goods and Services Tax: what changed in the budget and why it matters to small traders this year"],
            ["Income tax return", "e-filing"],
            [],
        ):
            self.assertIsNone(portal_logo_cue(lines), lines)

    def test_only_the_top_of_the_page_is_looked_at(self):
        from core.vsdc.vsdc_scope import portal_logo_cue, TRIPWIRE_TOP_LINES
        self.assertIsNone(portal_logo_cue(["x"] * TRIPWIRE_TOP_LINES + ["E Filing logo"]))


class TestUnlistedPortalTripwire(unittest.TestCase):
    """If a portal moves to an unlisted domain, say so - never capture, never widen the scope."""

    def setUp(self):
        self._env = patch.dict(os.environ)
        self._env.start()
        os.environ.pop("VSDC_ALLOW_LOCAL_TEST", None)
        os.environ.pop("VSDC247_MODE", None)
        os.environ["VSDC_SCOPE_CONFIG"] = os.path.join(os.environ.get("TEMP", "."), "no_such_vsdc_scope.json")
        self.events = []
        ocr = MagicMock()
        ocr.capture_window_image.return_value = Image.new("RGB", (800, 600), (240,) * 3)
        ocr.scan_image.return_value = {"text": "", "lines": [], "words": []}
        self.ocr = ocr
        self.router = VSDCRouter(ocr_engine=ocr, assembler=VisualSessionAssembler(),
                                 on_activity=lambda *a: self.events.append(a))
        self.router.get_foreground_info = MagicMock(return_value=(7, "Efile - Google Chrome", "chrome.exe"))
        self.router.extract_browser_url = MagicMock(return_value="https://efile.newtax.gov.in/dashboard")

    def tearDown(self):
        self._env.stop()

    def _run(self, lines, ticks=3):
        self.clock = getattr(self, "clock", 1_000_000.0)
        read = MagicMock(return_value={"text": "", "lines": lines})
        with patch("core.vsdc.vsdc_router.vsdc_uia_text.read_page_text", read):
            for _ in range(ticks):
                self.clock += 6
                with patch("core.vsdc.vsdc_router.time.time", side_effect=lambda: self.clock):
                    self.assertIsNone(self.router.evaluate_tick())
        return read

    def test_an_unlisted_gov_host_with_the_portal_logo_raises_one_prompt_and_nothing_else(self):
        read = self._run(["E Filing logo", "Login"], ticks=4)
        prompts = [e for e in self.events if e[0] == "prompt"]
        self.assertEqual(len(prompts), 1)
        self.assertIn("efile.newtax.gov.in", prompts[0][2])
        self.assertEqual(read.call_count, 1, "once the host is flagged it is never read again")
        self.ocr.capture_window_image.assert_not_called()      # nothing screenshotted
        self.assertEqual(self.router.assembler.client_pan, None)

    def test_the_read_is_bounded_to_the_top_of_the_page(self):
        read = self._run(["E Filing logo"], ticks=1)
        self.assertEqual(read.call_args.kwargs.get("max_lines"), 30)

    def test_an_unlisted_gov_host_without_the_logo_is_probed_a_few_times_then_left_alone(self):
        read = self._run(["Some ministry page", "News"], ticks=8)
        self.assertEqual(self.events, [])
        self.assertEqual(read.call_count, 3)

    def test_a_uia_warm_up_that_returned_nothing_is_retried(self):
        self._run([], ticks=1)
        self.assertEqual(self.events, [])
        self._run(["E Filing logo"], ticks=1)
        self.assertEqual(len([e for e in self.events if e[0] == "prompt"]), 1)

    def test_a_non_government_site_is_never_read_at_all(self):
        for url in ("https://www.google.com/", "https://mybank.co.in/profile", "https://efile.newtax.example.com/"):
            self.router.extract_browser_url.return_value = url
            read = self._run(["E Filing logo"], ticks=2)
            self.assertEqual(read.call_count, 0, url)
        self.assertEqual(self.events, [])

    def test_a_listed_portal_never_trips_it(self):
        self.router.extract_browser_url.return_value = "https://eportal.incometax.gov.in/iec/foservices/#/dashboard"
        read = MagicMock(return_value={"text": "", "lines": []})
        with patch("core.vsdc.vsdc_router.vsdc_uia_text.read_page_text", read):
            self.router.evaluate_tick()
        self.assertFalse([c for c in read.call_args_list if c.kwargs.get("max_lines")])


class TestSanitizePageUrl(unittest.TestCase):
    """The URL stored with a capture says which page it came from - and nothing secret."""

    def test_the_route_is_kept(self):
        from core.vsdc.vsdc_scope import sanitize_page_url
        self.assertEqual(
            sanitize_page_url("https://eportal.incometax.gov.in/iec/foservices/#/foreturns-ay26/fo-itr1-ay2026/fo-e-verify-later"),
            "https://eportal.incometax.gov.in/iec/foservices/#/foreturns-ay26/fo-itr1-ay2026/fo-e-verify-later")
        self.assertEqual(sanitize_page_url("services.gst.gov.in/services/auth/fowelcome"),
                         "services.gst.gov.in/services/auth/fowelcome")

    def test_query_strings_are_dropped_including_inside_a_hash_route(self):
        from core.vsdc.vsdc_scope import sanitize_page_url
        for url, want in (
            ("https://x.incometax.gov.in/a/b?token=SECRET&ack=123", "https://x.incometax.gov.in/a/b"),
            ("https://x.incometax.gov.in/#/route/page?session=SECRET", "https://x.incometax.gov.in/#/route/page"),
        ):
            out = sanitize_page_url(url)
            self.assertEqual(out, want)
            self.assertNotIn("SECRET", out)

    def test_user_info_is_dropped(self):
        from core.vsdc.vsdc_scope import sanitize_page_url
        self.assertEqual(sanitize_page_url("https://user:pw@eportal.incometax.gov.in/x"), "https://eportal.incometax.gov.in/x")
        self.assertNotIn("pw", sanitize_page_url("eportal.incometax.gov.in@evil.example/x"))

    def test_junk_and_overlong_values_give_nothing_or_are_capped(self):
        from core.vsdc.vsdc_scope import sanitize_page_url
        self.assertEqual(sanitize_page_url(""), "")
        self.assertEqual(sanitize_page_url(None), "")
        self.assertEqual(sanitize_page_url("https://a.gov.in/\x00x"), "")
        self.assertEqual(len(sanitize_page_url("https://a.gov.in/" + "p" * 1000)), 300)
