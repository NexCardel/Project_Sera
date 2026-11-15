"""
OS-mode SCK engine (core/os_assist/sck_engine.py) with fakes: no browser, no UIA, no worker thread.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time
from concurrent.futures import Future
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from core.os_assist import sck_engine
from core.rotation.attempts import SckAttempts
from ui.os_assist import sck_card as card_mod

BROWSER = 0x1234
SERVICE = {"id": 7, "name": "GST", "login_page_link": "https://portal.example.test/login",
           "password_selector": "#pw"}


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def spin(ms=30):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.005)


class FakeWin:
    def is_window(self, h): return h == BROWSER
    def is_iconic(self, h): return False
    def window_rect(self, h): return (100, 50, 1300, 850)
    def add_ex_style(self, h, b): pass
    def set_owner(self, h, o): pass


class FakeLocator:
    def probe(self, hwnd, service): return (True, (108, 130, 1292, 842))


def done(value):
    f = Future()
    f.set_result(value)
    return f


class Rig:
    def __init__(self, fills=None):
        self.fills = list(fills or [])          # results the filler returns, in order (None = no box yet)
        self.fill_calls = []
        self.committed = []
        self.closed = []
        self.started = []
        self.fronted = []
        self.attempts = SckAttempts()
        self.engine = sck_engine.SckEngine(
            self.attempts, commit=self.committed.append,
            closed=lambda a, r: self.closed.append((a.attempt_id, r)),
            fill_started=self.started.append,
            submit=lambda fn, *a: done(fn(*a)),
            filler=self.filler, card_factory=self.factory, front=self.fronted.append)

    def filler(self, hwnd, service, password, selector):
        self.fill_calls.append((hwnd, service.get("login_page_link"), password, selector))
        return self.fills.pop(0) if self.fills else None

    def factory(self, attempt, service, edit):
        class C(card_mod.SckCard):
            def _native_hwnd(self): return 0xBEEF
        return C(attempt.attempt_id, service, client_id=attempt.client_id, client_name=attempt.client_name,
                 portal=attempt.portal, kind=attempt.kind, identity=attempt.identity, edit=edit,
                 win=FakeWin(), locator=FakeLocator())

    def show(self, **kw):
        att = self.attempts.open(7, hwnd=BROWSER, client_id=5, client_name="Fict", portal="GST", **kw)
        card = self.engine.show(att, dict(SERVICE))
        return att, card


def ok(late=None):
    return SimpleNamespace(outcome="filled", reason="", late=late)


def test_show_docks_a_card_for_the_attempt(app):
    r = Rig()
    att, card = r.show()
    assert card is r.engine.card(att.attempt_id) and card.browser_hwnd == BROWSER
    card.dismiss()


def test_no_window_no_card(app):
    r = Rig()
    att = r.attempts.open(7, hwnd=0, client_id=5)
    assert r.engine.show(att, dict(SERVICE)) is None


def test_fill_and_test_fills_stores_a_tested_candidate_and_fronts_the_browser(app):
    r = Rig(fills=[ok()])
    att, card = r.show()
    card.pwd.setText("New#Pw1")
    card.fill_btn.click()
    spin()
    assert r.fill_calls == [(BROWSER, SERVICE["login_page_link"], "New#Pw1", "#pw")]
    assert "Password filled" in card.status.text()
    assert r.started == [7] and r.fronted == [BROWSER]
    assert att.tested and r.attempts.take_candidate(att) == "New#Pw1"
    card.dismiss()


def test_late_failure_is_shown_and_the_candidate_dropped(app):
    r = Rig(fills=[ok(late=done("the page cleared the box"))])
    att, card = r.show()
    card.pwd.setText("New#Pw1")
    card.fill_btn.click()
    spin()
    assert "cleared the box" in card.status.text()
    assert not r.attempts.has_candidate(att)
    assert r.fronted == []
    card.dismiss()


def test_waits_for_a_box_then_fills(app, monkeypatch):
    monkeypatch.setattr(sck_engine, "RETRY_MS", 10)
    r = Rig(fills=[None, None, ok()])
    att, card = r.show()
    card.pwd.setText("New#Pw1")
    card.fill_btn.click()
    spin(200)
    assert len(r.fill_calls) == 3 and "Password filled" in card.status.text()
    card.dismiss()


def test_gives_up_after_the_deadline(app, monkeypatch):
    monkeypatch.setattr(sck_engine, "RETRY_MS", 10)
    clock = {"t": 0.0}
    r = Rig()
    r.engine._now = lambda: clock["t"]
    att, card = r.show()
    card.pwd.setText("New#Pw1")
    card.fill_btn.click()
    clock["t"] = sck_engine.FILL_WAIT_MS + 1
    spin(100)
    assert "could not find the password box" in card.status.text()
    assert not r.attempts.has_candidate(att)
    card.dismiss()


def test_a_refused_fill_is_reported(app):
    r = Rig(fills=[SimpleNamespace(outcome="refused", reason="wrong site", late=None)])
    att, card = r.show()
    card.pwd.setText("New#Pw1")
    card.fill_btn.click()
    spin()
    assert "wrong site" in card.status.text()
    assert r.fronted == []
    card.dismiss()


def test_save_directly_commits_an_untested_candidate(app):
    r = Rig()
    att, card = r.show()
    card.pwd.setText("New#Pw1")
    card.save_btn.click()
    assert r.committed == [att] and att.tested is False and att.candidate == "New#Pw1"
    r.engine.save_result(att.attempt_id, True, "Saved password replaced")
    assert card.save_btn.text() == "Saved"
    card.dismiss()


def test_outcomes_reach_the_card(app):
    r = Rig()
    att, card = r.show()
    r.engine.outcome(att.attempt_id, "wrong_password")
    assert "rejected" in card.status.text()
    r.engine.outcome(att.attempt_id, "locked")
    assert "locked" in card.status.text()
    card.dismiss()


def test_dismissing_closes_the_attempt_and_tells_the_app(app):
    r = Rig()
    att, card = r.show()
    card.pwd.setText("x")
    card.dismiss("close")
    assert r.closed == [(att.attempt_id, "close")]
    assert r.attempts.get(7, BROWSER) is None and att.candidate == ""
    assert r.engine.card(att.attempt_id) is None


def test_call_runs_on_the_qt_thread(app):
    r = Rig()
    hits = []
    r.engine.call(lambda: hits.append(1))
    spin()
    assert hits == [1]


def test_fill_job_records_why_it_found_nothing(monkeypatch):
    from core.os_assist import sck_engine, locate as loc
    svc = {"login_page_link": "https://eportal.incometax.gov.in/iec/foservices/#/login"}
    monkeypatch.setattr(loc, "host_matches_service", lambda h, s: False)
    assert sck_engine.fill_job(5, svc, "x", "") is None
    assert "not on the portal" in sck_engine._last_miss[5]
    monkeypatch.setattr(loc, "host_matches_service", lambda h, s: True)
    monkeypatch.setattr(loc, "find_fields_cached", lambda *a, **k: ("userbox", None))
    assert sck_engine.fill_job(5, svc, "x", "") is None
    assert "non-password box" in sck_engine._last_miss[5]
    monkeypatch.setattr(loc, "find_fields_cached", lambda *a, **k: (None, None))
    assert sck_engine.fill_job(5, svc, "x", "") is None
    assert sck_engine._last_miss.pop(5) == "no input box on the page"
