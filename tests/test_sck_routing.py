"""
SCK engine routing and the desktop glue (main.py), with fakes:
* engine `os`: the extension never gets sck_failed_login; the OS engine is used;
* engine `extension`: the broadcast carries the new attempt fields;
* a `worked` seen in window B never saves window A's candidate, an untested Save Directly is saved
  untagged, `locked` pauses SCA and drops the candidate.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication

import automation
from core.rotation.attempts import SckAttempts

SERVICE = {"id": 7, "name": "GST", "login_page_link": "https://p.example.test/login",
           "dashboard_page_link": "https://p.example.test/dash"}


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class FakeBridge:
    def __init__(self):
        self.sent = []

    def broadcast(self, msg):
        self.sent.append(msg)
        return 1


def test_extension_mode_broadcasts_the_attempt_fields():
    bridge = FakeBridge()
    with patch.object(automation, "assist_engine", return_value="extension"), \
            patch("ui.ws_bridge.get_active_bridge", return_value=bridge):
        assert automation.show_sck_widget(SERVICE, None, "", "GST", attempt_id="sck1",
                                          kind="new_client", user_id="u", identity="ABCDE1234F")
    msg = bridge.sent[0]
    assert msg["type"] == "sck_failed_login"
    assert (msg["kind"], msg["attempt_id"], msg["user_id"], msg["identity"]) == \
        ("new_client", "sck1", "u", "ABCDE1234F")


def test_os_mode_never_broadcasts_to_the_extension(app):
    from core.os_assist import sck_engine
    bridge = FakeBridge()
    attempts = SckAttempts()
    att = attempts.open(7, hwnd=99, client_id=5)
    shown = []
    eng = sck_engine.SckEngine(attempts, card_factory=lambda a, s, e: None)
    eng.show = lambda a, s, edit=False: shown.append((a, s, edit))
    with patch.object(automation, "assist_engine", return_value="os"), \
            patch("ui.ws_bridge.get_active_bridge", return_value=bridge), \
            patch.object(sck_engine, "_engine", eng):
        assert automation.show_sck_widget(SERVICE, 5, "Fict", "GST", hwnd=99, attempt_id=att.attempt_id)
        assert automation.sck_outcome(7, "worked", att.attempt_id)
        assert automation.sck_save_result(7, True, "ok", att.attempt_id)
        assert automation.hide_sck_widget(7, att.attempt_id)
        for _ in range(5):
            QApplication.processEvents()
    assert bridge.sent == []
    assert shown and shown[0][0] is att


def test_os_mode_without_an_engine_or_window_shows_nothing():
    with patch.object(automation, "assist_engine", return_value="os"), \
            patch("core.os_assist.sck_engine._engine", None):
        assert automation.show_sck_widget(SERVICE, 5, "Fict", "GST", hwnd=99, attempt_id="x") is False


# ── main.py glue, called unbound on a stand-in ──────────────────────────────────────────────
def _glue():
    """A stand-in `self` with the real SCK methods of SeraApp bound to it."""
    from main import SeraApp
    reg = SckAttempts()
    me = SimpleNamespace()
    me._sck_attempts_registry = lambda: reg
    me.db = MagicMock()
    me.actor = "Staff"
    me._sck_watcher = MagicMock()
    me.tray_icon = None
    me.saved = []

    def save(att, password, tested):
        me.saved.append((att.attempt_id, password, tested))
    me._save_sck_password = save
    me._sck_commit_new_client = MagicMock()
    for name in ("_handle_sck_outcome", "_sck_worked", "_sck_locked", "_sck_commit",
                 "_sck_commit_requested", "_sck_card_closed"):
        setattr(me, name, getattr(SeraApp, name).__get__(me))
    return me, reg


def test_worked_in_window_b_does_not_save_window_a(app):
    me, reg = _glue()
    a = reg.open(7, hwnd=11, client_id=1)
    reg.set_candidate(a, "Pw#A", tested=True)
    with patch.object(automation, "sck_outcome", return_value=True):
        me._handle_sck_outcome(7, "worked", 22, "", {})
        assert me.saved == []
        me._handle_sck_outcome(7, "worked", 11, "", {})
    assert me.saved == [(a.attempt_id, "Pw#A", True)]
    assert a.candidate == ""


def test_save_directly_is_committed_untested(app):
    me, reg = _glue()
    a = reg.open(7, hwnd=11, client_id=1)
    reg.set_candidate(a, "Pw#A", tested=False)
    me._sck_commit_requested(a)
    assert me.saved == [(a.attempt_id, "Pw#A", False)]


def test_a_rejected_password_is_forgotten(app):
    me, reg = _glue()
    a = reg.open(7, hwnd=11, client_id=1)
    reg.set_candidate(a, "Pw#A", tested=True)
    with patch.object(automation, "sck_outcome", return_value=True):
        me._handle_sck_outcome(7, "wrong_password", 11, "", {})
        me._handle_sck_outcome(7, "worked", 11, "", {})
    assert me.saved == []


def test_locked_drops_the_candidate_and_pauses_sca(app):
    me, reg = _glue()
    a = reg.open(7, hwnd=11, client_id=1, client_name="Fict", portal="GST")
    reg.set_candidate(a, "Pw#A", tested=True)
    with patch.object(automation, "sck_outcome", return_value=True):
        me._handle_sck_outcome(7, "locked", 11, "", {})
    assert reg.is_locked(1, 7) and a.candidate == ""
    assert "locked" in me.db.log_action.call_args.kwargs["detail"]
    assert "Pw#A" not in str(me.db.log_action.call_args)


def test_a_worked_for_an_unknown_id_goes_to_the_new_client_path(app):
    me, reg = _glue()
    a = reg.open(7, hwnd=11, client_id=None, kind="new_client", identity="ABCDE1234F")
    reg.set_candidate(a, "Pw#A", tested=True)
    with patch.object(automation, "sck_outcome", return_value=True):
        me._handle_sck_outcome(7, "worked", 11, "ABCDE1234F", {"company_name": "Fict"})
    me._sck_commit_new_client.assert_called_once_with(a, "Pw#A", True, {"company_name": "Fict"})
    assert me.saved == []


def test_dismissing_a_new_client_offer_declines_it(app):
    me, reg = _glue()
    a = reg.open(7, hwnd=11, client_id=None, kind="new_client", identity="ABCDE1234F")
    me._sck_card_closed(a, "close")
    me._sck_watcher.decline_offer.assert_called_once_with(7, "ABCDE1234F")
    me._sck_watcher.mark_resolved.assert_called_once_with(7, hwnd=11)
