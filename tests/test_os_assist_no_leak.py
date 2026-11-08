"""OS Assist O4-1: no password leaves memory through stdout, stderr or logging, and an idle OS Assist
(no card, no arm) runs no UIA job. All test data is fictional."""

import logging
import os
from concurrent.futures import Future
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

import sca_protocol
from core.os_assist import fill, launcher, locate, sca_engine, worker
from ui.os_assist import mecp_card as mc
from ui.os_assist import smti_card as sc

SMTI_PASSWORD = "Fict-ional#1"
SCA_PASSWORD = "Fict-Pass-01"
MECP_PASSWORD = "Fict-Mecp#77"
ALL_SECRETS = (SMTI_PASSWORD, SCA_PASSWORD, MECP_PASSWORD, "Fict-combo-a", "Fict-combo-b")
BROWSER = 0x1234
HWND = 4242
GST_URL = "https://services.gst.gov.in/services/login"
PAN = "ABCDE1234F"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def sink(capfd, caplog):
    """Everything printed or logged during a test, at the lowest logging level."""
    caplog.set_level(1)
    logging.getLogger().setLevel(1)

    def text():
        out, err = capfd.readouterr()
        records = "\n".join(f"{r.name}|{r.getMessage()}|{r.exc_text or ''}|{r.args!r}" for r in caplog.records)
        return out + err + records
    return text


def assert_clean(text):
    for secret in ALL_SECRETS:
        assert secret not in text


class Box:
    def __init__(self, mode="ok", password=True):
        self.mode, self.password, self.value = mode, password, ""
        self.CurrentName, self.CurrentAutomationId, self.CurrentControlType = "", "", fill.CT_EDIT

    def GetCurrentPattern(self, _id):
        return self

    def QueryInterface(self, _iface):
        return self

    CurrentIsReadOnly = False

    @property
    def CurrentValue(self):
        return "•" * len(self.value) if self.password else self.value

    def SetValue(self, text):
        if self.mode == "ok":
            self.value = text
        elif self.mode == "short":
            self.value = text[:-1]

    def SetFocus(self):
        pass


class Keys:
    def __init__(self, box):
        self.box = box

    def foreground(self):
        return HWND

    def bring_to_front(self, hwnd):
        pass

    def sleep(self, _s):
        pass

    def type_text(self, text, hwnd):
        self.box.value = text
        return True


@pytest.fixture
def uia(monkeypatch):
    u = SimpleNamespace(focus=None, c=SimpleNamespace(UIA_ValuePatternId=10002, IUIAutomationValuePattern=object))
    u.uia = SimpleNamespace(GetFocusedElement=lambda: u.focus, CompareElements=lambda a, b: a is b)
    worker.bind(u)
    host = {"ok": True}
    monkeypatch.setattr(locate, "host_matches_service", lambda hwnd, service: host["ok"])
    u.host = host
    yield u
    worker.bind(None)


def test_fill_paths_never_print_the_password(uia, sink):
    service = {"login_page_link": GST_URL}
    for mode in ("ok", "ignored", "short"):
        box = Box(mode)
        uia.focus = box
        fill.fill(HWND, box, SMTI_PASSWORD, True, service, keyboard=Keys(box))
    uia.host["ok"] = False
    box = Box("ok")
    fill.fill(HWND, box, SMTI_PASSWORD, True, service, keyboard=Keys(box))
    fill.type_into_focused(HWND, SMTI_PASSWORD, True, service, keyboard=Keys(box))
    assert_clean(sink())


# ── SMTI card ─────────────────────────────────────────────────────────────────────
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


class SmtiFiller:
    def __init__(self, outcome):
        self.outcome = outcome

    def fill(self, hwnd, service, kind, text, user_sel, pass_sel):
        return self.outcome

    def edit_count(self, hwnd):
        return 2

    def focus_step(self, hwnd, service, text, is_password):
        return None


class SmtiCard(sc.SmtiCard):
    def _native_hwnd(self):
        return 0xBEEF


@pytest.mark.parametrize("outcome", ["filled", "nobox", "mismatch", "refused"])
def test_smti_card_never_prints_the_password(app, sink, outcome):
    service = {"id": 7, "name": "GST", "login_page_link": "https://portal.example.test/login",
               "username_selector": "#user", "password_selector": "#pass"}
    copies = []
    card = SmtiCard(service, "FICTUSER01", SMTI_PASSWORD, client_id=42, client_name="Fictional Traders",
                    clear_seconds=45, filler=SmtiFiller(outcome), copier=lambda t, s, c: copies.append(t),
                    sck_handler=lambda *_a: None, locator=FakeLocator(), win=FakeWin())
    card.attach(BROWSER, service)
    card.present()
    card.user_btn.click()
    card.pass_btn.click()
    card.dismiss("close")
    assert_clean(sink())


# ── MECP card ─────────────────────────────────────────────────────────────────────
class MecpCard(mc.MecpCard):
    def _native_hwnd(self):
        return 0xBEEF


def test_mecp_card_never_prints_the_password(app, sink):
    service = {"id": 3, "name": "Income Tax", "login_page_link": "https://itr.example.test/login"}
    rows = [{"id": 1, "label": "Combo 1", "value": "Fict-combo-a"},
            {"id": 2, "label": "Combo 2", "value": "Fict-combo-b"}]
    copies = []
    for pw, scc_rows, attempt in ((MECP_PASSWORD, None, ""), ("", rows, "att-1")):
        card = MecpCard(service, "FICTP1234Q", pw, client_id=42, client_name="Fictional Traders",
                        clear_seconds=45, scc_rows=scc_rows, attempt_id=attempt,
                        copier=lambda t, s, c: copies.append(t), scc_send=lambda *_a: None,
                        ledger=lambda *_a: None, sck_handler=lambda *_a: None,
                        locator=FakeLocator(), win=FakeWin())
        card.attach(BROWSER, service)
        card.present()
        card.user_row.copy.click()
        if pw:
            card.pass_row.copy.click()
        card.dismiss("close")
    assert_clean(sink())


# ── SCA engine ────────────────────────────────────────────────────────────────────
class Watcher:
    enabled = True

    def __init__(self):
        self.results = []

    def handle_password_request(self, msg):
        return {"type": sca_protocol.MSG_SCA_PASSWORD_GRANT, "request_id": msg["request_id"],
                "arm_id": msg["arm_id"], "service_id": msg["service_id"], "password": SCA_PASSWORD,
                "password_selector": ""}

    def handle_fill_result(self, msg):
        self.results.append(msg)


@pytest.mark.parametrize("mode,host_ok", [("ok", True), ("short", True), ("ok", False)])
def test_sca_engine_never_prints_the_password(app, uia, sink, mode, host_ok):
    now = 1_000_000.0
    url = GST_URL
    service = {"service_id": 1, "name": "GST", "url": url, "host": sca_protocol.service_host(url),
               "password_selector": "", "has_password": True, "blocked": "", "needs_confirm": False}
    arm = {"schema": 2, "arm_id": "arm_x", "client_id": 7, "client_id_token": "7", "matched_uid": PAN,
           "candidate_uids": [PAN], "services": [service], "business_name": "Fictional Traders",
           "owner_name": "A. Person", "sca_mode": "autofill", "max_uses": 1, "trigger": "clipboard",
           "uid_page": "", "created_at": now, "expires_at": now + 300000, "state": "ARMED"}
    uia.host["ok"] = host_ok

    def filler(hwnd, svc, password, selector):
        box = Box(mode)
        uia.focus = box
        return fill.fill(hwnd, box, password, True, {"login_page_link": url}, keyboard=Keys(box))

    watcher = Watcher()
    eng = sca_engine.ScaEngine(
        watcher, submit=lambda fn, *a: _done(fn(*a)), filler=filler,
        probe=lambda *_a: {"hwnd": HWND, "host": "services.gst.gov.in", "matched": PAN, "page_link": ""},
        locked=lambda: set(), now=lambda: now, card_factory=lambda *a: None, chip_factory=lambda *a: None)
    assert eng.arm(arm, [])
    eng.tick()
    eng.disarm("DISABLED")
    assert_clean(sink())
    assert_clean(repr(watcher.results))


def _done(value):
    f = Future()
    f.set_result(value)
    return f


# ── idle ──────────────────────────────────────────────────────────────────────────
def test_idle_runs_no_uia_job_for_five_seconds(app, monkeypatch):
    jobs = []
    created = []
    monkeypatch.setattr(worker, "Uia", lambda: created.append(1))
    fresh = worker.Worker()
    monkeypatch.setattr(worker, "_default", fresh)
    eng = sca_engine.ScaEngine(Watcher(), submit=lambda fn, *a: jobs.append(fn) or _done(None),
                               probe=lambda *_a: jobs.append("probe"), filler=lambda *_a: jobs.append("fill"),
                               locked=lambda: set(), card_factory=lambda *a: None, chip_factory=lambda *a: None)
    lch = launcher.Launcher(opener=lambda *a: jobs.append("open"), finder=lambda url: 0)
    loop = QEventLoop()
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    eng.tick()
    assert jobs == [] and created == []
    assert not eng.armed and not eng._timer.isActive()
    assert fresh._thread is None
    assert lch.card("smti") is None and lch.card("mecp") is None
