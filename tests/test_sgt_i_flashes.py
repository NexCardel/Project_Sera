"""
SGT-I step 9 tests (W10-1): what flashes and what appeared. The UIA event listener with a fake
backend (the real one is a live-desktop check), the host turning flashes into Observations of
their own, and page diffing. Everything here is fictional.
"""
import dataclasses
import json
import time

from core.sgt.sgt_shadow import SgtShadow
from core.sgt.sgt_specs import BUILTIN_FIELDS_PATH, SpecStore
from core.sgt_i import SgtIntelligence, default_components, make_observation
from core.sgt_i import assertions
from core.sgt_i import page_map as pm
from core.sgt_i.gps import GpsComponent
from core.sgt_i.ledger import LedgerComponent
from core.sgt_i.page_diff import FlashComponent, diff, map_from_lines, page_keys
from core.sgt_i.uia_events import Flash, FlashWatcher, element_lines

STORE = SpecStore([BUILTIN_FIELDS_PATH], log=lambda m: None)
TOAST = "Dataset submitted successfully"


def obs(lines=("Dashboard",), session="s1", source="uia", event=""):
    o = make_observation(session_id=session, portal="gst", url="https://example.test/p", title="t",
                         source=source, lines=list(lines), result={"profile": {}, "datasets": []},
                         profile={}, draft={}, ts=1.0, today="2026-09-28")
    return dataclasses.replace(o, event=event)


def wait_for(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


class Ctx:
    def __init__(self):
        self.data, self.reads, self.harder = None, 0, 0

    def enrich(self, d):
        self.data = json.loads(json.dumps(d))

    def ask_more_reads(self, n=1):
        self.reads += n

    def read_harder(self, s=30):
        self.harder += 1


class FakeBackend:
    """Stands in for the UIA registrations; tests call sink() the way a handler would."""

    def __init__(self):
        self.registered, self.unregistered, self.sinks = [], [], {}

    def register(self, hwnd, sink):
        self.registered.append(hwnd)
        self.sinks[hwnd] = sink
        return [("auto", 20024, "doc", object())]

    def unregister(self, tokens):
        if tokens:
            self.unregistered.append(tokens)


def watcher(backend=None, **kw):
    b = backend or FakeBackend()
    return FlashWatcher(backend_factory=lambda: b, echo=lambda m: None, **kw), b


# ── page diffing ──────────────────────────────────────────────────────────────────────────────

def page_of(*nodes):
    return pm.PageMap(nodes=tuple(pm.Node(i, t, **kw) for i, (t, kw) in enumerate(nodes)), sections=(), pairs=())


def test_diff_says_what_appeared_and_a_new_dialog_is_the_event():
    before = page_of(("Returns", {}), ("GSTR-3B", {}))
    after = page_of(("Returns", {}), ("GSTR-3B", {}), ("Filed successfully", {"zone": pm.DIALOG}))
    first = diff(None, before)
    assert first.first and first.appeared == ()
    d = diff(page_keys(before), after)
    assert [n.text for n in d.appeared] == ["Filed successfully"] and d.new_dialog and d.gone == 0
    # the same dialog still open on the next read is not a new event
    again = diff(page_keys(after), after)
    assert again.appeared == () and not again.new_dialog
    # a text shown twice counts twice; one that went away is counted as gone
    twice = page_of(("Returns", {}), ("Returns", {}))
    d2 = diff(page_keys(before), twice)
    assert [n.text for n in d2.appeared] == ["Returns"] and d2.gone == 1


def test_component_counts_flashes_the_polling_missed_and_never_puts_text_on_the_row():
    comp, ctx = FlashComponent(), Ctx()
    comp.observe(obs(["Dashboard", "GSTR-3B August 2026"]), ctx)
    assert ctx.data is None                      # nothing flashed: the row stays as it was
    comp.observe(obs([TOAST], source="uia_event", event="live_region"), ctx)
    assert comp.last_appeared("s1") == (TOAST,)
    comp.observe(obs(["Dashboard", "GSTR-3B August 2026"]), ctx)   # gone before the next read
    assert ctx.data["flashes"] == {"live_region": 1} and ctx.data["missed_by_polling"] == 1
    cls = assertions.classify(TOAST).cls
    assert ctx.data.get("flash_claims") == ({cls: 1} if cls else None)
    # a flash the next read still shows is not a miss
    comp.observe(obs(["Saved as draft"], source="uia_event", event="notification"), ctx)
    comp.observe(obs(["Dashboard", "Saved as draft"]), ctx)
    assert ctx.data["missed_by_polling"] == 1 and ctx.data["flashes"]["notification"] == 1
    text = json.dumps(ctx.data)
    assert TOAST not in text and "Saved as draft" not in text and "GSTR-3B" not in text


def test_page_diff_on_the_cores_lines_reports_appeared_content():
    comp, ctx = FlashComponent(), Ctx()
    comp.observe(obs(["Dashboard"]), ctx)
    comp.observe(obs(["Dashboard", "ARN AA0000000000X generated"]), ctx)
    assert comp.last_appeared("s1") == ("ARN AA0000000000X generated",)
    assert len(map_from_lines(["a", "b"]).nodes) == 2


def test_gps_and_ledger_do_not_take_a_flash_for_a_page():
    for comp in (GpsComponent(), LedgerComponent()):
        ctx = Ctx()
        comp.observe(obs([TOAST], source="uia_event", event="live_region"), ctx)
        assert ctx.data is None and ctx.harder == 0
    assert "flashes" in [c.name for c in default_components()]


# ── the listener (fake backend) ───────────────────────────────────────────────────────────────

def test_watcher_registers_once_per_page_and_again_when_the_page_changes():
    w, b = watcher()
    w.watch(7, "https://example.test/a")
    assert wait_for(lambda: b.registered == [7])
    w.watch(7, "https://example.test/a")
    time.sleep(0.05)
    assert b.registered == [7]                   # same page: nothing redone
    w.watch(7, "https://example.test/b")
    assert wait_for(lambda: b.registered == [7, 7] and len(b.unregistered) == 1)
    w.stop()
    assert wait_for(lambda: len(b.unregistered) == 2)   # stop removes this listener's own


def test_watcher_buffers_dedupes_and_hands_out_flashes_once():
    w, b = watcher()
    w.watch(7, "u")
    assert wait_for(lambda: 7 in b.sinks)
    b.sinks[7]("live_region", (TOAST,))
    b.sinks[7]("live_region", (TOAST,))           # the same toast re-announced: one flash
    b.sinks[7]("window_opened", ("Confirm",))
    b.sinks[7]("notification", ())                # nothing readable: ignored
    assert w.pending(7) and not w.pending(8)
    got = w.take(7)
    assert [(f.kind, f.lines) for f in got] == [("live_region", (TOAST,)), ("window_opened", ("Confirm",))]
    assert w.take(7) == [] and not w.pending(7)
    w.stop()


def test_watcher_keeps_few_windows_and_drops_the_oldest():
    w, b = watcher()
    for h in range(1, 6):
        w.watch(h, "u")
    assert wait_for(lambda: len(b.registered) == 5 and len(b.unregistered) == 1)
    assert 1 not in w._windows and set(w._windows) == {2, 3, 4, 5}
    w.stop()


def test_a_hung_registration_switches_listening_off_and_a_broken_one_retries():
    class Hangs(FakeBackend):
        def register(self, hwnd, sink):
            time.sleep(0.5)
            return super().register(hwnd, sink)

    now = [0.0]
    msgs = []
    w = FlashWatcher(backend_factory=Hangs, hang_sec=10, echo=msgs.append, monotonic=lambda: now[0])
    w.watch(7, "u")
    assert wait_for(lambda: w._busy_since is not None)
    now[0] = 11.0
    w.watch(7, "v")
    assert w.disabled and "hung" in w.disabled and msgs
    w.watch(8, "u")
    assert w.take(8) == [] and 8 not in w._windows

    class Empty(FakeBackend):
        def register(self, hwnd, sink):
            self.registered.append(hwnd)
            return []                          # a cold page: no Document yet

    w2, b2 = watcher(Empty())
    w2.watch(7, "u")
    assert wait_for(lambda: b2.registered == [7])
    assert wait_for(lambda: w2._windows[7].key is None)
    w2.watch(7, "u")                            # same URL, but nothing was registered: try again
    assert wait_for(lambda: b2.registered == [7, 7])
    w2.stop()

    def boom():
        raise OSError("no UI Automation")
    w3 = FlashWatcher(backend_factory=boom, echo=lambda m: None)
    w3.watch(7, "u")
    assert wait_for(lambda: w3.disabled is not None)


def test_element_lines_reads_the_senders_cache_only():
    class El:
        def __init__(self, name, kids=()):
            self.name, self.kids = name, list(kids)

        def GetCachedPropertyValue(self, pid):
            return {30005: self.name, 30003: 50020}.get(pid)

        def GetCachedChildren(self):
            kids = self.kids

            class Arr:
                Length = len(kids)

                def GetElement(self, i):
                    return kids[i]
            return Arr()

        def __getattr__(self, attr):            # any live (Current*) read would fail the test
            raise AssertionError("live read: " + attr)

    region = El("", [El("Toast"), El(TOAST)])
    assert element_lines(region) == ("Toast", TOAST)
    assert element_lines(El(""), "Saved") == ("Saved",)


# ── the host and the Core ─────────────────────────────────────────────────────────────────────

class FakeWatcher:
    def __init__(self, flashes=()):
        self.flashes, self.watched, self.stopped = list(flashes), [], 0

    def watch(self, hwnd, key=""):
        self.watched.append((hwnd, key))

    def take(self, hwnd):
        got, self.flashes = self.flashes, []
        return got

    def pending(self, hwnd):
        return bool(self.flashes)

    def stop(self):
        self.stopped += 1


def test_host_queues_flashes_as_observations_before_the_page_and_asks_for_a_read():
    seen = []

    class Rec:
        name = "rec"

        def observe(self, o, ctx):
            seen.append((o.source, o.event, o.lines))

    fw = FakeWatcher([Flash("live_region", (TOAST,), 0.0)])
    h = SgtIntelligence([Rec()], enabled=True, echo=lambda m: None, flashes=fw)
    page = obs(["Dashboard"])
    h.window_seen(7, page)
    h.submit(page)
    assert wait_for(lambda: len(seen) == 2)
    assert seen == [("uia_event", "live_region", (TOAST,)), ("uia", "", ("Dashboard",))]
    assert fw.watched == [(7, "https://example.test/p")]
    fw.flashes = [Flash("notification", ("Saved",), 1.0)]
    assert h.wants_read("s1") is True           # something flashed there: read now
    fw.flashes = []
    assert h.wants_read("s1") is False
    h.set_enabled(False)
    assert fw.stopped == 1
    h.window_seen(7, page)
    assert fw.watched == [(7, "https://example.test/p")]     # Off: the listener is never pointed


def test_off_host_never_starts_the_listener_and_the_core_hands_the_window_over(tmp_path):
    fw = FakeWatcher()
    off = SgtIntelligence([], enabled=False, echo=lambda m: None, flashes=fw)
    off.window_seen(7, obs())
    assert fw.watched == [] and off.wants_read("s1") is False

    on = SgtIntelligence([], enabled=True, echo=lambda m: None, flashes=fw)
    page = ["Dashboard", "Returns", "GSTR-3B", "August 2026", "Status", "Not filed", "Due date",
            "20/09/2026", "Help", "Log out"]
    s = SgtShadow(store=STORE, read_uia=lambda h: {"lines": page},
                  log_dir=tmp_path, echo=lambda m: None, intelligence=on)
    s.observe(42, "gst", "https://example.test/dash")
    assert fw.watched == [(42, "https://example.test/dash")]
