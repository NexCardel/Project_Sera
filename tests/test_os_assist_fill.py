"""OS Assist fill (the one writer), with fake elements and a fake keyboard. Fictional test data only."""

from types import SimpleNamespace

import pytest

from core.os_assist import fill, locate, worker

HWND = 4242
SERVICE = {"login_page_link": "https://services.gst.gov.in/login"}


class Box:
    """A fake Edit that is its own ValuePattern. mode: ok | ignored | readonly | novalue."""

    def __init__(self, mode="ok", password=False, expose=True, name="", ctype=fill.CT_EDIT):
        self.mode, self.password, self.expose = mode, password, expose
        self.value = ""
        self.setvalue_calls = 0
        self.focused = False
        self.CurrentName, self.CurrentAutomationId, self.CurrentControlType = name, "", ctype

    def GetCurrentPattern(self, _id):
        return None if self.mode == "novalue" else self

    def QueryInterface(self, _iface):
        return self

    @property
    def CurrentIsReadOnly(self):
        return self.mode == "readonly"

    @property
    def CurrentValue(self):
        if not self.expose:
            return None
        return "•" * len(self.value) if self.password else self.value

    def SetValue(self, text):
        self.setvalue_calls += 1
        if self.mode == "ok":
            self.value = text

    def SetFocus(self):
        self.focused = True


class Uia:
    def __init__(self):
        self.focus = None
        self.c = SimpleNamespace(UIA_ValuePatternId=10002, IUIAutomationValuePattern=object)
        self.uia = SimpleNamespace(GetFocusedElement=lambda: self.focus,
                                   CompareElements=lambda a, b: a is b)


class Keys:
    def __init__(self, box=None, change_window=False):
        self.box, self.front, self.change_window = box, HWND, change_window
        self.typed = 0
        self.brought = 0

    def foreground(self):
        return self.front

    def bring_to_front(self, hwnd):
        self.brought += 1
        self.front = hwnd

    def sleep(self, _s):
        pass

    def type_text(self, text, hwnd):
        self.typed += 1
        if self.change_window:
            self.front = 1
            return False
        self.box.value = text
        return True


@pytest.fixture
def env(monkeypatch):
    uia = Uia()
    worker.bind(uia)
    host = {"ok": True, "reads": 0}

    def matches(hwnd, service):
        host["reads"] += 1
        return host["ok"]
    monkeypatch.setattr(locate, "host_matches_service", matches)
    return SimpleNamespace(uia=uia, host=host)


def keys_for(box, **kw):
    return Keys(box, **kw)


def test_setvalue_ok(env):
    box = Box("ok")
    kb = keys_for(box)
    res = fill.fill(HWND, box, "AB12", False, SERVICE, keyboard=kb)
    assert (res.outcome, res.method) == ("filled", "setvalue")
    assert kb.typed == 0


def test_setvalue_ignored_falls_back_to_keys(env):
    box = Box("ignored")
    env.uia.focus = box
    kb = keys_for(box)
    res = fill.fill(HWND, box, "AB12", False, SERVICE, keyboard=kb)
    assert (res.outcome, res.method) == ("filled", "keys")
    assert box.setvalue_calls == 1 and box.focused and kb.typed == 1
    assert env.host["reads"] == 2          # checked before each write


def test_readonly_goes_to_keys(env):
    box = Box("readonly")
    env.uia.focus = box
    res = fill.fill(HWND, box, "AB12", False, SERVICE, keyboard=keys_for(box))
    assert (res.outcome, res.method) == ("filled", "keys")
    assert box.setvalue_calls == 0


def test_no_value_pattern_goes_to_keys(env):
    box = Box("novalue")
    env.uia.focus = box
    box.mode = "novalue"
    res = fill.fill(HWND, box, "AB12", False, SERVICE, keyboard=keys_for(box))
    assert res.outcome == "failed" and res.method == "keys"      # keys typed, but nothing to verify with


def test_host_changed_before_write_is_refused(env):
    env.host["ok"] = False
    box = Box("ok")
    kb = keys_for(box)
    res = fill.fill(HWND, box, "AB12", False, SERVICE, keyboard=kb)
    assert res.outcome == "refused"
    assert box.setvalue_calls == 0 and kb.typed == 0 and box.value == ""


def test_host_changed_between_setvalue_and_keys_is_refused(env):
    box = Box("ignored")
    env.uia.focus = box
    calls = {"n": 0}

    def matches(hwnd, service):
        calls["n"] += 1
        return calls["n"] == 1
    locate.host_matches_service = matches            # monkeypatch fixture restores the original
    kb = keys_for(box)
    res = fill.fill(HWND, box, "AB12", False, SERVICE, keyboard=kb)
    assert res.outcome == "refused" and kb.typed == 0


def test_window_changes_mid_type_fails(env):
    box = Box("ignored")
    env.uia.focus = box
    kb = keys_for(box, change_window=True)
    res = fill.fill(HWND, box, "AB12", False, SERVICE, keyboard=kb)
    assert res.outcome == "failed" and "window changed" in res.reason


def test_box_without_focus_fails_without_typing(env):
    box = Box("ignored")
    env.uia.focus = Box()
    kb = keys_for(box)
    res = fill.fill(HWND, box, "AB12", False, SERVICE, keyboard=kb)
    assert res.outcome == "failed" and kb.typed == 0


def test_browser_brought_to_front_for_keys(env):
    box = Box("ignored")
    env.uia.focus = box
    kb = keys_for(box)
    kb.front = 7
    res = fill.fill(HWND, box, "AB12", False, SERVICE, keyboard=kb)
    assert res.outcome == "filled" and kb.brought == 1


def test_password_verified_by_length_only(env):
    box = Box("ok", password=True)
    res = fill.fill(HWND, box, "s3cret-fake", True, SERVICE, keyboard=keys_for(box))
    assert (res.outcome, res.method) == ("filled", "setvalue")
    assert box.value == "s3cret-fake"
    assert "s3cret" not in repr(res)


def test_password_wrong_length_fails(env):
    class Short(Box):
        def SetValue(self, text):
            self.value = text[:-1]
    box = Short("ok", password=True)
    res = fill.fill(HWND, box, "s3cret-fake", True, SERVICE, method="setvalue", keyboard=keys_for(box))
    assert res.outcome == "failed" and "length" in res.reason and "s3cret" not in res.reason


def test_password_value_not_exposed_trusts_setvalue(env):
    box = Box("ok", password=True, expose=False)
    res = fill.fill(HWND, box, "s3cret-fake", True, SERVICE, keyboard=keys_for(box))
    assert res.outcome == "filled" and "not exposed" in res.reason


def test_method_setvalue_never_types(env):
    box = Box("ignored")
    env.uia.focus = box
    kb = keys_for(box)
    res = fill.fill(HWND, box, "AB12", False, SERVICE, method="setvalue", keyboard=kb)
    assert res.outcome == "failed"
    assert kb.typed == 0 and kb.brought == 0 and not box.focused


def test_type_into_focused(env):
    box = Box("ignored")
    env.uia.focus = box
    kb = keys_for(box)
    res = fill.type_into_focused(HWND, "AB12", False, SERVICE, keyboard=kb)
    assert (res.outcome, res.method) == ("filled", "keys") and box.value == "AB12"


def test_type_into_focused_refuses_on_host_change_and_non_edit(env):
    box = Box("ignored")
    env.uia.focus = box
    env.host["ok"] = False
    kb = keys_for(box)
    assert fill.type_into_focused(HWND, "AB12", False, SERVICE, keyboard=kb).outcome == "refused"
    env.host["ok"] = True
    env.uia.focus = Box(ctype=50000)
    assert fill.type_into_focused(HWND, "AB12", False, SERVICE, keyboard=kb).outcome == "failed"
    assert kb.typed == 0


def test_focus_watcher_reports_each_edit_once_and_skips_address_bar(env):
    kb = keys_for(None)
    w = fill.FocusWatcher(HWND, keyboard=kb)
    bar, a, b = Box(name="Address and search bar"), Box(), Box()
    env.uia.focus = bar
    assert w.poll() is None
    env.uia.focus = a
    assert w.poll() is a
    assert w.poll() is None                      # same box again
    env.uia.focus = b
    assert w.poll() is b
    env.uia.focus = a
    assert w.poll() is None                      # already reported
    kb.front = 9
    env.uia.focus = Box()
    assert w.poll() is None                      # browser not in front
