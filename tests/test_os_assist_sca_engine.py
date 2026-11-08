"""OS Assist SCA engine (blueprint §4.6) with fakes: no browser, no vault, no real UIA.
All test data is fictional."""

import os
import threading
from concurrent.futures import Future
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

import automation
import sca_protocol
from core.os_assist import locate, sca_engine, worker
from core.os_assist.fill import FillResult

PAN = "ABCDE1234F"
GST_URL = "https://services.gst.gov.in/services/login"
IT_URL = "https://eportal.incometax.gov.in/iec/foservices/#/login"
GST_HOST = "services.gst.gov.in"
IT_HOST = "eportal.incometax.gov.in"
FAKE_PASSWORD = "Fict-Pass-01"


@pytest.fixture(scope="module", autouse=True)
def app():
    yield QApplication.instance() or QApplication([])


def svc(sid, url, has_password=True, blocked="", needs_confirm=False, name="GST", selector=""):
    return {"service_id": sid, "name": name, "url": url, "host": sca_protocol.service_host(url),
            "password_selector": selector, "has_password": has_password,
            "blocked": "" if has_password else (blocked or "no_password"), "needs_confirm": needs_confirm}


def make_arm(*services, mode="autofill", max_uses=1, ttl_ms=300000, now=1_000_000.0, **extra):
    arm = {"schema": 2, "arm_id": "arm_test1", "client_id": 7, "client_id_token": "7", "matched_uid": PAN,
           "candidate_uids": [PAN, "27ABCDE1234F1Z5"], "services": list(services) or [svc(1, GST_URL)],
           "business_name": "Fictional Traders", "owner_name": "A. Person", "sca_mode": mode,
           "max_uses": max_uses, "trigger": "clipboard", "uid_page": "", "created_at": now,
           "expires_at": now + ttl_ms, "state": "ARMED"}
    arm.update(extra)
    return arm


def done(value):
    f = Future()
    f.set_result(value)
    return f


class Watcher:
    def __init__(self, grant=True, reason="no such arm"):
        self.enabled = True
        self.grant = grant
        self.reason = reason
        self.requests, self.results = [], []

    def handle_password_request(self, msg):
        self.requests.append(dict(msg))
        if not self.grant:
            return {"type": sca_protocol.MSG_SCA_PASSWORD_DENIED, "request_id": msg["request_id"],
                    "arm_id": msg["arm_id"], "reason": self.reason}
        return {"type": sca_protocol.MSG_SCA_PASSWORD_GRANT, "request_id": msg["request_id"],
                "arm_id": msg["arm_id"], "service_id": msg["service_id"], "password": FAKE_PASSWORD,
                "password_selector": ""}

    def handle_fill_result(self, msg):
        self.results.append(dict(msg))


class Card(QObject):
    inject_requested = Signal(object, object)
    dismissed = Signal(str)

    def __init__(self, arm, service, hwnd, note):
        super().__init__()
        self.arm_id, self.service, self.hwnd, self.note = arm["arm_id"], service, hwnd, note
        self.is_dismissed = False

    def dismiss(self, reason="close"):
        self.is_dismissed = True
        self.dismissed.emit(reason)


class Rig:
    """An engine whose worker jobs run at once: `page` is what the probe sees in front."""

    def __init__(self, watcher=None, locked=()):
        self.watcher = watcher or Watcher()
        self.t = 1_000_000.0
        self.page = None                     # {"hwnd", "host", "matched", "page_link"} or None
        self.box_after = 0                   # fill finds the password box after this many looks
        self.fill_outcome = "filled"
        self.fills, self.cards, self.chips, self.jobs = [], [], [], []
        self.locked = set(locked)
        self.engine = sca_engine.ScaEngine(
            self.watcher, submit=self.submit, probe=self.probe, filler=self.filler,
            host_of=lambda hwnd: (self.page or {}).get("host", ""),
            card_factory=self.card, chip_factory=lambda a, s, h: self.chips.append((a["business_name"], s["name"], h)),
            locked=lambda: self.locked, now=lambda: self.t)

    def submit(self, fn, *args):
        self.jobs.append(fn)
        return done(fn(*args))

    def probe(self, services, approved, uids):
        return dict(self.page) if self.page else None

    def filler(self, hwnd, service, password, selector):
        if self.box_after > 0:
            self.box_after -= 1
            return None
        self.fills.append((hwnd, service["login_page_link"], password == FAKE_PASSWORD))
        return FillResult(self.fill_outcome, "setvalue", "" if self.fill_outcome == "filled" else "mismatch")

    def card(self, arm, service, hwnd, note):
        c = Card(arm, service, hwnd, note)
        self.cards.append(c)
        return c

    def show(self, hwnd=101, host=GST_HOST, matched=PAN, page_link=""):
        self.page = {"hwnd": hwnd, "host": host, "matched": matched, "page_link": page_link}
        self.engine.tick()

    def arm(self, *services, allowed=(), **kw):
        assert self.engine.arm(make_arm(*services, now=self.t, **kw), list(allowed))


def test_match_and_autofill():
    r = Rig()
    r.arm()
    r.show()
    assert len(r.watcher.requests) == 1
    req = r.watcher.requests[0]
    assert set(req) == {"type", "request_id", "arm_id", "service_id", "page_host", "matched_uid", "confirmed"}
    assert (req["arm_id"], req["service_id"], req["page_host"], req["matched_uid"], req["confirmed"]) == \
        ("arm_test1", 1, GST_HOST, PAN, False)
    assert r.fills == [(101, GST_URL, True)]
    assert r.watcher.results == [{"type": "SCA_FILL_RESULT", "arm_id": "arm_test1", "service_id": 1,
                                  "result": "filled", "reason": ""}]
    assert r.chips == [("Fictional Traders", "GST", 101)]
    assert not r.engine.armed and not r.engine._timer.isActive()        # max_uses 1: consumed
    assert not r.engine._pending                                         # the password went with it


def test_needs_confirm_shows_card_and_inject_fills():
    r = Rig()
    r.arm(svc(3, IT_URL, needs_confirm=True, name="Income Tax"))
    r.show(host=IT_HOST)
    assert r.watcher.requests == [] and len(r.cards) == 1
    assert r.cards[0].note == sca_engine.CONFIRM_NOTE
    r.show(host=IT_HOST, matched="")
    r.show(host=IT_HOST)                                                  # typed again: one card per arm/window/service
    assert len(r.cards) == 1
    r.cards[0].inject_requested.emit("arm_test1", 3)
    assert [q["confirmed"] for q in r.watcher.requests] == [True]
    assert r.watcher.requests[0]["matched_uid"] == PAN
    assert r.fills == [(101, IT_URL, True)] and r.watcher.results[-1]["result"] == "filled"


def test_widget_mode_shows_card_without_note():
    r = Rig()
    r.arm(mode="widget")
    r.show()
    assert len(r.cards) == 1 and r.cards[0].note == "" and r.watcher.requests == []


def test_inject_from_another_clients_card_is_ignored():
    r = Rig()
    r.arm(mode="widget")
    r.show()
    assert r.engine.on_widget_fill("arm_other", 1, 101, client_id=99) == "not-armed"
    assert r.engine.on_widget_fill("arm_older", 1, 101, client_id=7) == "host-check"   # same client: live arm
    assert len(r.watcher.requests) == 1


def test_inject_on_another_site_is_ignored():
    r = Rig()
    r.arm(mode="widget")
    r.show()
    r.page["host"] = "example.com"
    r.cards[0].inject_requested.emit("arm_test1", 1)
    assert r.watcher.requests == [] and r.fills == []


def test_blocked_service_notice_once():
    r = Rig()
    r.arm(svc(3, IT_URL, has_password=False, blocked="scc_unverified", name="Income Tax"))
    for matched in (PAN, "", PAN, "", PAN):
        r.show(host=IT_HOST, matched=matched)
    assert r.watcher.requests == []
    assert r.watcher.results == [{"type": "SCA_FILL_RESULT", "arm_id": "arm_test1", "service_id": 3,
                                  "result": "failed", "reason": "scc_unverified"}]


def test_wrong_host_ignored():
    r = Rig()
    r.arm()
    assert r.engine.on_candidate(PAN, 101, "services.gst.gov.in.evil.com") == "not-a-portal-of-this-client"
    assert r.engine.on_candidate(PAN, 101, "gov.in") == "not-a-portal-of-this-client"
    assert r.watcher.requests == [] and r.watcher.results == []


def test_other_client_id_ignored():
    r = Rig()
    r.arm()
    assert r.engine.on_candidate("ZZZZZ9999Z", 101, GST_HOST) == "not-this-client"
    assert r.watcher.requests == []


def test_non_approved_domain_ignored():
    r = Rig()
    r.arm(allowed=["incometax.gov.in"])
    r.show()
    assert r.watcher.requests == [] and r.watcher.results == []
    assert sca_engine.is_approved_domain("eportal.incometax.gov.in", ["incometax.gov.in"])
    assert sca_engine.is_approved_domain("anything.example", [])          # list not synced yet


def test_one_fill_per_arm_window_service():
    r = Rig()
    r.arm(max_uses=3)
    r.show(hwnd=101)
    r.show(hwnd=101, matched="")
    r.show(hwnd=101)                                                       # typed again in the same window
    assert len(r.fills) == 1
    assert r.engine.on_candidate(PAN, 101, GST_HOST) == "already-handled"
    r.show(hwnd=202)                                                       # another window: its own fill
    assert [f[0] for f in r.fills] == [101, 202]
    assert r.engine.armed                                                  # 2 of 3 uses


def test_same_value_is_not_acted_on_every_tick():
    r = Rig(Watcher(grant=False, reason="no uses left for this copy"))
    r.arm()
    for _ in range(4):
        r.show()
    assert len(r.watcher.requests) == 1
    assert r.watcher.results == [{"type": "SCA_FILL_RESULT", "arm_id": "arm_test1", "service_id": 1,
                                  "result": "failed", "reason": "no uses left for this copy"}]
    r.show(matched="")
    r.show()                                                               # typed again: denied again, no loop
    assert len(r.watcher.requests) == 2


def test_failed_fill_is_reported_and_can_be_retried():
    r = Rig()
    r.fill_outcome = "failed"
    r.arm()
    r.show()
    assert r.watcher.results[-1]["result"] == "failed" and r.chips == []
    r.fill_outcome = "filled"
    r.show(matched="")
    r.show()
    assert r.watcher.results[-1]["result"] == "filled"


def test_income_tax_two_page_wait():
    r = Rig()
    r.arm(svc(3, IT_URL, name="Income Tax"))
    r.box_after = 3                                                        # PAN page, Continue, password page
    r.show(host=IT_HOST)
    assert len(r.watcher.requests) == 1 and r.fills == []
    for _ in range(2):
        r.t += 600
        r.show(host=IT_HOST, matched="")                                   # the PAN box is gone
    assert r.fills == [] and r.watcher.results == []
    r.t += 600
    r.show(host=IT_HOST, matched="")
    assert r.fills == [(101, IT_URL, True)]
    assert len(r.watcher.requests) == 1 and r.watcher.results[-1]["result"] == "filled"


def test_password_box_never_appears():
    r = Rig()
    r.arm(svc(3, IT_URL, name="Income Tax"), ttl_ms=10 * 60 * 1000)
    r.box_after = 10 ** 6
    r.show(host=IT_HOST)
    r.t += sca_engine.FIELD_WAIT_MS + 1
    r.engine.tick()
    assert r.watcher.results == [{"type": "SCA_FILL_RESULT", "arm_id": "arm_test1", "service_id": 3,
                                  "result": "failed", "reason": "no visible password field"}]
    assert not r.engine._pending


def test_assist_lock():
    r = Rig(locked={101})
    r.arm(max_uses=2)
    r.show(hwnd=101)
    assert r.engine.on_candidate(PAN, 101, GST_HOST) == "assist-open-in-tab"
    assert r.watcher.requests == []
    r.show(hwnd=202)                                                       # other windows work normally
    assert len(r.watcher.requests) == 1
    r.locked.clear()
    r.show(hwnd=101)                                                       # the id the card filled stays quiet
    assert len(r.watcher.requests) == 1
    r.show(hwnd=101, matched="")
    r.show(hwnd=101)                                                       # staff typed it: acts
    assert len(r.watcher.requests) == 2


def test_idle_when_no_arm():
    r = Rig()
    r.show()
    assert r.jobs == [] and not r.engine._timer.isActive()
    r.arm()
    assert r.engine._timer.isActive()
    r.t += sca_protocol.ARM_TTL_MS + 1
    r.show()
    assert not r.engine.armed and not r.engine._timer.isActive() and r.jobs == []


def test_disabled_sca_drops_the_arm():
    r = Rig()
    r.arm()
    r.watcher.enabled = False
    r.show()
    assert not r.engine.armed and r.jobs == []


def test_arm_with_password_or_expired_is_refused():
    r = Rig()
    bad = make_arm(dict(svc(1, GST_URL), password=FAKE_PASSWORD), now=r.t)
    assert not r.engine.arm(bad, [])
    assert not r.engine.arm(make_arm(now=r.t, ttl_ms=-1), [])
    assert not r.engine.armed


def test_new_arm_acts_on_an_id_already_typed():
    r = Rig()
    r.page = {"hwnd": 101, "host": GST_HOST, "matched": PAN, "page_link": ""}
    r.arm()
    r.engine.tick()
    assert len(r.watcher.requests) == 1


def test_uid_page_arm_acts_on_the_page_in_front():
    r = Rig()
    r.arm(trigger="source", uid_page="accounts.example.gov.in/signin")
    r.show(host=GST_HOST, matched="", page_link="www.accounts.example.gov.in/signin")
    assert len(r.watcher.requests) == 1 and r.watcher.requests[0]["matched_uid"] == PAN


# ----------------------------------------------------------------------------- the real probe job
class _VP:
    def __init__(self, value):
        self.CurrentValue = value

    def QueryInterface(self, _iface):
        return self


class El:
    def __init__(self, id="", value="", password=False):
        self.CurrentAutomationId = id
        self.CurrentName = ""
        self.CurrentIsPassword = password
        self.CurrentIsOffscreen = False
        self.CurrentIsEnabled = True
        self.CurrentAriaProperties = ""
        self.CurrentBoundingRectangle = SimpleNamespace(left=0, top=0, right=200, bottom=30)
        self.CurrentClassName = ""
        self.value = value
        self.reads = 0
        self.children = []

    def GetCurrentPattern(self, _pid):
        if self.CurrentIsPassword:
            raise AssertionError("a password box was read")
        self.reads += 1
        return _VP(self.value)


class FakeUia:
    def __init__(self, edits, url, focused=None):
        self.doc = El()
        self.doc.children = edits
        self._url = url
        self.c = SimpleNamespace(UIA_ValuePatternId=10002, IUIAutomationValuePattern=object)
        self.uia = SimpleNamespace(GetFocusedElement=lambda: focused, CompareElements=lambda a, b: a is b)

    def url(self, hwnd):
        return self._url

    def documents(self, hwnd):
        return [self.doc]

    def edits(self, doc):
        return doc.children


@pytest.fixture
def uia(monkeypatch):
    monkeypatch.setattr(locate, "_is_browser", lambda hwnd: hwnd == 101)

    def make(edits, url=GST_URL, focused=None):
        worker.bind(FakeUia(edits, url, focused))
    yield make
    worker.bind(None)


def test_probe_finds_the_id_and_never_reads_a_password_box(uia):
    pw = El("pw", password=True)
    uia([El("search", "gst"), El("user", " abcde1234f "), pw])
    out = sca_engine.probe_job([svc(1, GST_URL)], [], [PAN], foreground=lambda: 101)
    assert out["hwnd"] == 101 and out["host"] == GST_HOST and out["matched"] == PAN


def test_probe_reads_the_focused_box_first(uia):
    other, user = El("a", "SOMETHING"), El("user", PAN)
    uia([other, user], focused=user)
    assert sca_engine.probe_job([svc(1, GST_URL)], [], [PAN], foreground=lambda: 101)["matched"] == PAN
    assert other.reads == 0


def test_probe_ignores_other_sites_and_non_browsers(uia):
    uia([El("user", PAN)], url="https://services.gst.gov.in.evil.com/login")
    assert sca_engine.probe_job([svc(1, GST_URL)], [], [PAN], foreground=lambda: 101) is None
    uia([El("user", PAN)])
    assert sca_engine.probe_job([svc(1, GST_URL)], ["incometax.gov.in"], [PAN], foreground=lambda: 101) is None
    assert sca_engine.probe_job([svc(1, GST_URL)], [], [PAN], foreground=lambda: 555) is None


def test_fill_job_waits_while_the_window_is_not_in_front_or_has_no_box(uia):
    uia([El("pan", PAN)], url=IT_URL)
    service = {"login_page_link": IT_URL, "name": "Income Tax"}
    assert sca_engine.fill_job(101, service, FAKE_PASSWORD, "", foreground=lambda: 555) is None
    assert sca_engine.fill_job(101, service, FAKE_PASSWORD, "", foreground=lambda: 101) is None


# ----------------------------------------------------------------------------- the extension's SCA in OS mode
class _Bridge:
    def __init__(self):
        self.sent = []
        self.done = threading.Event()

    def broadcast(self, payload):
        self.sent.append(payload)
        self.done.set()
        return 1


@pytest.mark.parametrize("engine, expected", [("os", False), ("extension", True)])
def test_extension_sca_is_off_in_os_mode(monkeypatch, engine, expected):
    from ui import ws_bridge
    bridge = _Bridge()
    monkeypatch.setattr(ws_bridge, "get_active_bridge", lambda: bridge)
    monkeypatch.setattr(automation, "assist_engine", lambda: engine)
    automation.update_extension_settings(sca_enabled=True)
    assert bridge.done.wait(5)
    assert bridge.sent[0]["sca_enabled"] is expected


class _Db:
    """Just enough of SeraDatabase for clipboard_watch to build an arm (fictional client)."""
    cols = [{"id": 1, "label": "PAN"}, {"id": 2, "label": "Password"}, {"id": 3, "label": "Client Name"}]
    service = {"id": 1, "name": "GST", "login_page_link": GST_URL, "userid_column_id": 1, "password_column_id": 2}
    client = {"id": 7, "client_id_token": "", "values": {1: PAN, 2: FAKE_PASSWORD, 3: "Fictional Traders"}}

    def search_clients(self, _q, include_archived=False):
        return [self.client]

    def get_mcl_columns(self):
        return self.cols

    def get_services(self):
        return [self.service]

    def get_client_services(self, _cid):
        return [self.service]

    def get_client(self, _cid):
        return self.client

    def get_setting(self, _key, default=None):
        return default


@pytest.mark.parametrize("engine", ["os", "extension"])
def test_arms_go_to_the_engine_only_in_os_mode(monkeypatch, engine):
    import clipboard_watch
    sent, armed = [], []
    monkeypatch.setattr(automation, "assist_engine", lambda: engine)
    monkeypatch.setattr(automation, "arm_sca", lambda request: sent.append(request))
    monkeypatch.setattr(sca_engine.ScaEngine, "arm", lambda self, arm, allowed=None: armed.append((arm, allowed)))
    watch = clipboard_watch.ClipboardWatchService(_Db())
    watch._arm_client_services(7, "7", PAN)
    if engine == "os":
        assert sent == [] and len(armed) == 1
        arm, allowed = armed[0]
        assert arm["candidate_uids"][0] == PAN and GST_HOST in allowed
        assert all("password" not in s for s in arm["services"])
    else:
        assert armed == [] and len(sent) == 1
    sca_engine._engines.clear()


def test_disarm_all():
    w = Watcher()
    eng = sca_engine.for_watcher(w)
    assert sca_engine.for_watcher(w) is eng
    assert eng.arm(make_arm(now=eng._now()), [])
    sca_engine.disarm_all()
    assert not eng.armed and not eng._timer.isActive()
