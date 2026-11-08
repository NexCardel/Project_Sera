import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from ui.os_assist import mecp_card as mc

BROWSER = 0x1234
SERVICE = {"id": 3, "name": "Income Tax", "login_page_link": "https://itr.example.test/login"}
FAKE_PAN = "FICTP1234Q"
FAKE_PASSWORD = "Fict-ional#1"
ROWS = [{"id": 1, "label": "Combo 1", "value": "Fict-combo-a"},
        {"id": 2, "label": "Combo 2", "value": "Fict-combo-b"},
        {"id": 3, "label": "Saved password", "value": "Fict-saved-c"}]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class FakeWin:
    def is_window(self, hwnd):
        return hwnd == BROWSER

    def is_iconic(self, hwnd):
        return False

    def window_rect(self, hwnd):
        return (100, 50, 1300, 850)

    def add_ex_style(self, hwnd, bits):
        pass

    def set_owner(self, hwnd, owner):
        pass


class FakeLocator:
    def probe(self, hwnd, service):
        return (True, (108, 130, 1292, 842))


class Card(mc.MecpCard):
    def _native_hwnd(self):
        return 0xBEEF


class Rec:
    def __init__(self):
        self.copies, self.sent, self.ledger, self.edits = [], [], [], []


def make(user_id=FAKE_PAN, password=FAKE_PASSWORD, scc_rows=None, attempt_id="", present=True, **kw):
    r = Rec()
    card = Card(SERVICE, user_id, password, client_id=42, client_name=kw.pop("client_name", ""),
                clear_seconds=45, scc_rows=scc_rows, attempt_id=attempt_id,
                copier=lambda t, s, c: r.copies.append((t, s, c)), scc_send=r.sent.append,
                ledger=r.ledger.append, sck_handler=r.edits.append,
                locator=FakeLocator(), win=FakeWin(), **kw)
    card.attach(BROWSER, SERVICE)
    if present:
        card.present()
    return card, r


def make_scc(attempt_id="att-1", **kw):
    return make(password="", scc_rows=ROWS, attempt_id=attempt_id, client_name=f"PAN: {FAKE_PAN}", **kw)


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def raw(label):
    return label.text().replace("​", "")


# ── plain ────────────────────────────────────────────────────────────────────────
def test_plain_look(app):
    card, _ = make()
    assert not card.scc and not card.badge.isVisible()
    assert card.title.text() == "Client Credentials"
    assert card.user_row.label.text() == "User ID (PAN)" and raw(card.user_row.value) == FAKE_PAN
    assert card.pass_row.label.text() == "Password" and raw(card.pass_row.value) == FAKE_PASSWORD
    assert card.user_row.copy.text() == "📋 Copy" and card.pass_row.copy.text() == "📋 Copy"
    assert card.timer is not None and card.timer.duration_ms == 90000
    assert card.card.width() == mc.css_px(355)          # content-box: 320 + padding + border
    card.dismiss()


def test_plain_without_user_id_has_only_password_row(app):
    card, _ = make(user_id="  ")
    assert card.user_row is None and card.pass_row is not None
    card.dismiss()


def test_copy_user_plain_then_password_secret_and_back(app):
    card, r = make()
    card.user_row.copy.click()
    assert r.copies == [(FAKE_PAN, False, None)]
    assert card.user_row.copy.text() == "✓ Copied" and card.toast.text() == "User ID copied!"
    card.pass_row.copy.click()
    assert r.copies[-1] == (FAKE_PASSWORD, True, 45)
    assert card.toast.text() == "Password copied!"
    assert r.ledger == []                                  # plain copies never feed the SCC ledger
    card.dismiss()


def test_copied_label_reverts(app, monkeypatch):
    monkeypatch.setattr(mc, "COPIED_MS", 30)
    card, _ = make(user_id="")
    card._copied["pass"] = True                            # keep the card up
    card._finishing = True
    card.pass_row.copy.click()
    wait(80)
    assert card.pass_row.copy.text() == "📋 Copy"
    card.dismiss()


def test_closes_after_both_copied(app, monkeypatch):
    monkeypatch.setattr(mc, "BOTH_COPIED_DISMISS_MS", 20)
    card, _ = make()
    reasons = []
    card.dismissed.connect(reasons.append)
    card.pass_row.copy.click()
    wait(60)
    assert not card.is_dismissed                           # the user ID is still to copy
    card.user_row.copy.click()
    assert card.timer.paused is False and not card.timer.running
    wait(60)
    assert reasons == ["copied"] and not card.has_secrets()
    assert card.pass_row.value.text() == "" and card.user_row.value.text() == ""


def test_password_only_card_closes_on_password_copy(app, monkeypatch):
    monkeypatch.setattr(mc, "BOTH_COPIED_DISMISS_MS", 20)
    card, _ = make(user_id="")
    card.pass_row.copy.click()
    wait(60)
    assert card.is_dismissed


def test_timeout_and_close_send_nothing(app):
    card, r = make(timeout_ms=40)
    reasons = []
    card.dismissed.connect(reasons.append)
    wait(120)
    assert reasons == ["timeout"] and r.sent == []
    card2, r2 = make()
    card2.close_btn.click()
    assert card2.is_dismissed and r2.sent == []


def test_copy_after_dismiss_does_nothing(app):
    card, r = make()
    card.dismiss()
    card._copy_pass()
    assert r.copies == []


def test_edit_button_opens_sck(app):
    card, r = make()
    card.edit_btn.click()
    assert r.edits == [{"client_id": 42, "service_id": 3, "portal": "income tax"}]
    card.dismiss()


# ── SCC mode ─────────────────────────────────────────────────────────────────────
def test_scc_look(app):
    card, _ = make_scc()
    assert card.scc and card.timer is None
    assert card.badge.text() == "⚡ SERA ASSIST (SCC)" and not card.badge.isHidden()
    assert card.title.text() == f"PAN: {FAKE_PAN}"
    assert raw(card.user_row.value) == FAKE_PAN and card.pass_row is None
    assert list(card.rows) == ["Combo 1", "Combo 2", "Saved password"]
    assert raw(card.rows["Combo 2"].value) == "Fict-combo-b"
    assert all(r.worked.text() == "This one worked" for r in card.rows.values())
    assert card.note.isHidden() and card.none_btn.isHidden()
    assert card.card.width() == mc.css_px(395)
    card.dismiss("closed")


def test_scc_copy_is_secret_and_feeds_ledger(app):
    card, r = make_scc()
    card.rows["Combo 2"].copy.click()
    assert r.copies == [("Fict-combo-b", True, 45)] and r.ledger == ["Fict-combo-b"]
    assert card.toast.text() == "Combo 2 copied!"
    card.user_row.copy.click()                             # the PAN: plain copy, no ledger
    assert r.copies[-1] == (FAKE_PAN, False, None) and r.ledger == ["Fict-combo-b"]
    assert not card.is_dismissed                           # SCC never closes on copies
    card.dismiss("closed")


def test_scc_worked_and_none_messages(app):
    card, r = make_scc()
    card.rows["Combo 1"].worked.click()
    card.rows["Combo 1"].worked.click()                    # disabled after the first
    assert r.sent == [{"type": "scc_row_worked", "attempt_id": "att-1", "row_label": "Combo 1"}]
    assert card.rows["Combo 1"].worked.text() == "✓ Sent"
    card.apply_update([], None, "Which password worked?", False, ask=[])
    assert not card.none_btn.isHidden()
    card.none_btn.click()
    assert r.sent[-1] == {"type": "scc_row_none", "attempt_id": "att-1"}
    assert card.none_btn.text() == "✓ Sent" and not card.none_btn.isEnabled()
    card.dismiss("closed")


def test_scc_close_tells_desktop_only_on_x(app):
    card, r = make_scc()
    card.close_btn.click()
    assert r.sent == [{"type": "scc_card_closed", "attempt_id": "att-1"}]
    assert not card.has_secrets() and all(row.value.text() == "" for row in card.rows.values())
    card2, r2 = make_scc(attempt_id="att-2")
    card2.dismiss("closed")
    assert r2.sent == []


def test_scc_update_marks_failed_next_note(app):
    card, _ = make_scc()
    card.apply_update(["Combo 1"], "Combo 2", "The portal refused Combo 1.", False)
    c1, c2, c3 = (card.rows[k] for k in ["Combo 1", "Combo 2", "Saved password"])
    assert c1.label.text() == "✗ Combo 1" and c1.state == "failed" and not c1.worked.isEnabled()
    assert c2.state == "next" and c2.worked.isEnabled() and c3.state == ""
    assert card.note.text() == "The portal refused Combo 1." and not card.note.isHidden()
    assert card.note.property("stop") is False and card.none_btn.isHidden()
    card.apply_update(["Combo 1"], None, "", False)
    assert c2.state == "" and card.note.isHidden()
    card.dismiss("closed")


def test_scc_update_stop_and_ask(app):
    card, _ = make_scc()
    card.apply_update(["Combo 1"], None, "", False, ask=["Combo 2"])
    assert card.rows["Combo 2"].state == "ask" and not card.none_btn.isHidden()
    card.apply_update(["Combo 1"], None, "Locked out. Stop trying.", True)
    assert card.note.property("stop") is True and card.none_btn.isHidden()
    assert all(not r.copy.isEnabled() and not r.worked.isEnabled() for r in card.rows.values())
    assert all(r.state in ("", "failed") for r in card.rows.values())
    card.dismiss("closed")


def test_module_update_and_close_from_another_thread(app):
    card, r = make_scc(attempt_id="att-thread")
    assert mc.card_for("att-thread") is card
    done = []
    t = threading.Thread(target=lambda: done.append(
        mc.update_card("att-thread", ["Combo 1"], "Combo 2", "msg", False)))
    t.start()
    t.join()
    wait(30)
    assert done == [True] and card.rows["Combo 1"].state == "failed"
    t = threading.Thread(target=lambda: done.append(mc.close_card("att-thread")))
    t.start()
    t.join()
    wait(30)
    assert done[-1] is True and card.is_dismissed and r.sent == []
    assert mc.card_for("att-thread") is None
    assert mc.update_card("att-thread", [], None, "", False) is False
    assert mc.close_card("nope") is False


def test_new_card_for_same_attempt_replaces_old(app):
    old, _ = make_scc(attempt_id="att-dup")
    new, _ = make_scc(attempt_id="att-dup")
    assert old.is_dismissed and mc.card_for("att-dup") is new
    new.dismiss("closed")


def test_module_hooks_used_when_no_callables_given(app):
    sent, ledger = [], []
    mc.set_scc_hooks(send=sent.append, ledger=ledger.append)
    try:
        card = Card(SERVICE, FAKE_PAN, "", scc_rows=ROWS, attempt_id="att-hook",
                    copier=lambda *a: None, locator=FakeLocator(), win=FakeWin())
        card.attach(BROWSER, SERVICE)
        card.present()
        card.rows["Saved password"].copy.click()
        card.close_btn.click()
        assert ledger == ["Fict-saved-c"]
        assert sent == [{"type": "scc_card_closed", "attempt_id": "att-hook"}]
    finally:
        mc.set_scc_hooks()


def test_no_logging_or_printing_in_module():
    src = open(mc.__file__, encoding="utf-8").read()
    assert "print(" not in src and "logging" not in src and "logger" not in src
