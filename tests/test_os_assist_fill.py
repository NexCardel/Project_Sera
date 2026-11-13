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
    fill._keys_only.clear()
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
    assert env.host["reads"] == 3          # checked before SetValue, SetFocus and the keys


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


def test_host_changed_between_focus_and_keys_is_refused(env):
    box = Box("ignored")
    env.uia.focus = box
    calls = {"n": 0}

    def matches(hwnd, service):
        calls["n"] += 1
        return calls["n"] <= 2
    locate.host_matches_service = matches            # monkeypatch fixture restores the original
    kb = keys_for(box)
    res = fill.fill(HWND, box, "AB12", False, SERVICE, keyboard=kb)
    assert res.outcome == "refused" and kb.typed == 0


def test_password_box_never_compared_as_text(env):
    class Masked(Box):
        CurrentIsPassword = True
    box = Masked("ok", password=True)
    res = fill.fill(HWND, box, "s3cret-fake", False, SERVICE, method="setvalue", keyboard=keys_for(box))
    assert (res.outcome, res.method) == ("filled", "setvalue")     # judged by bullet count


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


# ----------------------------------------------------------------------------- late check in the background
class LaterKeys(Keys):
    """A keyboard whose later() runs nothing until the test says so (no real timer or worker)."""

    def __init__(self, box=None, **kw):
        super().__init__(box, **kw)
        self.slept, self.queued = [], []

    def sleep(self, s):
        self.slept.append(s)

    def later(self, seconds, fn):
        from concurrent.futures import Future
        fut = Future()
        self.queued.append((seconds, fn, fut))
        return fut

    def run_later(self):
        for _s, fn, fut in self.queued:
            fut.set_result(fn())


def test_background_returns_after_the_first_check(env):
    box = Box("ok")
    kb = LaterKeys(box)
    res = fill.fill(HWND, box, "AB12", False, SERVICE, keyboard=kb, late_check="background")
    assert (res.outcome, res.method) == ("filled", "setvalue")
    assert kb.slept == [fill.SETTLE_S]                            # the value was there at once: no fixed wait
    assert [round(s, 2) for s, _f, _u in kb.queued] == [0.96]     # the re-check, 1.0 s after the write
    assert not res.late.done()
    kb.run_later()
    assert res.late.result() == ""


def test_background_late_check_reports_a_value_the_page_dropped(env):
    box = Box("ok")
    kb = LaterKeys(box)
    res = fill.fill(HWND, box, "AB12", False, SERVICE, keyboard=kb, late_check="background")
    assert res.outcome == "filled"
    box.value = ""                                                # the page reset the box
    kb.run_later()
    assert res.late.result().startswith("mismatch") and "(at 1.0s)" in res.late.result()


def test_background_late_check_keeps_only_the_password_length(env):
    box = Box("ok", password=True)
    kb = LaterKeys(box)
    res = fill.fill(HWND, box, "s3cret-fake", True, SERVICE, keyboard=kb, late_check="background")
    assert res.outcome == "filled"
    _s, fn, _f = kb.queued[0]
    held = [c.cell_contents for c in (fn.__closure__ or ()) if isinstance(c.cell_contents, str)]
    assert "s3cret-fake" not in held
    kb.run_later()
    assert res.late.result() == ""


def test_background_first_check_failure_still_falls_back_to_keys(env):
    box = Box("ignored")
    env.uia.focus = box
    kb = LaterKeys(box)
    res = fill.fill(HWND, box, "AB12", False, SERVICE, keyboard=kb, late_check="background")
    assert (res.outcome, res.method) == ("filled", "keys")
    assert res.late is not None and kb.typed == 1


def test_wait_mode_is_unchanged(env):
    box = Box("ok")
    kb = LaterKeys(box)
    res = fill.fill(HWND, box, "AB12", False, SERVICE, keyboard=kb)
    assert res.outcome == "filled" and res.late is None
    assert [round(s, 2) for s in kb.slept] == [0.04, 0.96] and kb.queued == []   # re-check 1.0 s after the write


# ----------------------------------------------------------------------------- no fixed waits
def test_first_check_waits_only_until_the_value_is_there(env):
    class Slow(Box):
        """The page shows the value on the third read."""
        reads = 0

        @property
        def CurrentValue(self):
            Slow.reads += 1
            return self.value if Slow.reads >= 3 else ""
    box = Slow("ok")
    kb = LaterKeys(box)
    res = fill.fill(HWND, box, "AB12", False, SERVICE, keyboard=kb, late_check="background")
    assert (res.outcome, res.method) == ("filled", "setvalue")
    assert kb.slept == [fill.FIRST_POLL_S, fill.FIRST_POLL_S, fill.SETTLE_S]


def test_a_value_the_page_resets_on_its_next_tick_is_not_called_filled(env):
    class Resets(Box):
        """Takes the value, then drops it before the settle check."""
        reads = 0

        @property
        def CurrentValue(self):
            Resets.reads += 1
            return self.value if Resets.reads == 1 else ""
    box = Resets("ok")
    kb = LaterKeys(box)
    res = fill.fill(HWND, box, "AB12", False, SERVICE, method="setvalue", keyboard=kb, late_check="background")
    assert res.outcome == "failed" and "(at 0.15s)" in res.reason
    assert round(sum(kb.slept), 2) <= 0.2


def test_first_check_gives_up_after_the_old_wait(env):
    box = Box("ignored")
    kb = LaterKeys(box)
    res = fill.fill(HWND, box, "AB12", False, SERVICE, method="setvalue", keyboard=kb)
    assert res.outcome == "failed" and "(at 0.15s)" in res.reason
    assert round(sum(kb.slept), 2) == 0.15


def test_focus_is_waited_for_only_until_it_arrives(env):
    box = Box("readonly")
    env.uia.focus = box
    kb = LaterKeys(box)
    res = fill.fill(HWND, box, "AB12", False, SERVICE, keyboard=kb, late_check="background")
    assert (res.outcome, res.method) == ("filled", "keys")
    assert kb.slept == [fill.FOCUS_SETTLE_S, fill.SETTLE_S]       # no 0.15 s focus wait


def test_a_box_that_ignored_setvalue_is_typed_into_directly_next_time(env):
    box = Box("ignored")
    env.uia.focus = box
    kb = keys_for(box)
    assert fill.fill(HWND, box, "AB12", False, SERVICE, keyboard=kb).method == "keys"
    assert box.setvalue_calls == 1
    res = fill.fill(HWND, box, "CD34", False, SERVICE, keyboard=kb)
    assert (res.outcome, res.method) == ("filled", "keys") and box.value == "CD34"
    assert box.setvalue_calls == 1 and kb.typed == 2              # SetValue not tried again
    other = Box("ok")                                             # another box of that window: SetValue as usual
    assert fill.fill(HWND, other, "EF56", False, SERVICE, keyboard=keys_for(other)).method == "setvalue"


def test_method_setvalue_is_tried_even_for_a_keys_only_box(env):
    box = Box("ignored")
    env.uia.focus = box
    kb = keys_for(box)
    fill.fill(HWND, box, "AB12", False, SERVICE, keyboard=kb)
    res = fill.fill(HWND, box, "CD34", False, SERVICE, method="setvalue", keyboard=kb)
    assert res.outcome == "failed" and box.setvalue_calls == 2 and kb.typed == 1


def test_fast_keys_the_page_dropped_are_typed_again_slower(env):
    class DropsFast(Keys):
        SLOW_GAP_S = 0.012
        gaps = []

        def type_text(self, text, hwnd, gap=None):
            self.gaps.append(gap)
            self.typed += 1
            self.box.value = text if gap else text[:-1]           # the fast try loses a character
            return True
    box = Box("readonly")
    env.uia.focus = box
    kb = DropsFast(box)
    res = fill.fill(HWND, box, "AB12", False, SERVICE, keyboard=kb)
    assert (res.outcome, res.method) == ("filled", "keys") and box.value == "AB12"
    assert kb.gaps == [None, 0.012]
    assert env.host["reads"] == 4          # SetValue, SetFocus, the keys, the second keys


def test_no_second_try_when_the_focus_left_the_box(env):
    class DropsFast(Keys):
        SLOW_GAP_S = 0.012

        def type_text(self, text, hwnd, gap=None):
            self.typed += 1
            env.uia.focus = Box()                                 # the page moved the focus on
            return True
    box = Box("readonly")
    env.uia.focus = box
    kb = DropsFast(box)
    res = fill.fill(HWND, box, "AB12", False, SERVICE, keyboard=kb)
    assert res.outcome == "failed" and kb.typed == 1


# ----------------------------------------------------------------------------- the fill log
def test_log_says_why_setvalue_was_not_used_and_how_long_it_took(env, caplog):
    box = Box("ignored")
    env.uia.focus = box
    with caplog.at_level("INFO", logger="os_assist.fill"):
        fill.fill(HWND, box, "s3cret-fake", False, SERVICE, keyboard=keys_for(box))
    lines = [r.getMessage() for r in caplog.records]
    assert any(l.startswith("user  setvalue-not-used  mismatch (0 chars) (at 0.15s)") for l in lines)
    assert any(l.startswith("user  typed") for l in lines)
    assert any(l.startswith("user  took  ") and l.endswith("ms (keys)") for l in lines)
    assert "s3cret" not in " | ".join(lines) and "s3cret" not in repr([r.args for r in caplog.records])


def test_log_file_gets_the_fill_lines_and_no_value(env, tmp_path):
    import logging
    import main
    lg = logging.getLogger("os_assist")
    before = list(lg.handlers)
    try:
        main._setup_os_assist_log(tmp_path)
        box = Box("ok", password=True)
        fill.fill(HWND, box, "s3cret-fake", True, SERVICE, keyboard=keys_for(box))
    finally:
        for h in [h for h in lg.handlers if h not in before]:
            lg.removeHandler(h)
            h.close()
    text = (tmp_path / "logs" / "os_assist.log").read_text(encoding="utf-8")
    assert "pass  filled  setvalue" in text and "pass  took  " in text
    assert "s3cret" not in text

