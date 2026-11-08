"""OS Assist locate + worker, with fake UIA elements (no browser)."""

from types import SimpleNamespace

import pytest

from core.os_assist import locate, worker


def rect(w=200, h=30):
    return SimpleNamespace(left=10, top=20, right=10 + w, bottom=20 + h)


class El:
    def __init__(self, id="", name="", password=False, offscreen=False, enabled=True, aria="", w=200, h=30,
                 cls=""):
        self.CurrentAutomationId = id
        self.CurrentName = name
        self.CurrentIsPassword = password
        self.CurrentIsOffscreen = offscreen
        self.CurrentIsEnabled = enabled
        self.CurrentAriaProperties = aria
        self.CurrentBoundingRectangle = rect(w, h)
        self.CurrentClassName = cls
        self.children = []


class FakeUia:
    def __init__(self, docs, url="https://services.gst.gov.in/login"):
        self.docs = docs
        self._url = url

    def url(self, hwnd):
        return self._url

    def documents(self, hwnd):
        return self.docs

    def edits(self, doc):
        return doc.children


@pytest.fixture
def page():
    def make(*edits, url="https://services.gst.gov.in/login"):
        doc = El()
        doc.children = list(edits)
        worker.bind(FakeUia([doc], url))
        return doc
    yield make
    worker.bind(None)


def ids(pair):
    return tuple(e.CurrentAutomationId if e is not None else None for e in pair)


def test_username_before_password(page):
    page(El("search"), El("user"), El("pw", password=True), El("after"))
    assert ids(locate.find_fields(1)) == ("user", "pw")


def test_decoys_skipped(page):
    page(
        El("hiddenPassword", password=True),
        El("ghostuser", aria="hidden=true"),
        El("zero", w=0),
        El("off", offscreen=True),
        El("dis", enabled=False),
        El("user"),
        El("pw", password=True),
    )
    assert ids(locate.find_fields(1)) == ("user", "pw")


def test_hidden_password_by_name(page):
    page(El("u"), El("x", name="hiddenPassword", password=True), El("pw", password=True))
    assert ids(locate.find_fields(1)) == ("u", "pw")


def test_pan_page_without_password_box(page):
    page(El("pan"), El("other"))
    assert ids(locate.find_fields(1)) == ("pan", None)


def test_id_selector_wins(page):
    page(El("a"), El("b"), El("pw", password=True), El("c"), El("pw2", password=True))
    assert ids(locate.find_fields(1, "#b", "#pw2")) == ("b", "pw2")
    assert ids(locate.find_fields(1, "#c", None)) == ("c", "pw")


def test_non_id_selector_ignored(page):
    page(El("a"), El("b"), El("pw", password=True))
    assert ids(locate.find_fields(1, "input[name=b]", ".pass")) == ("b", "pw")
    assert ids(locate.find_fields(1, "#b input", "div > #pw")) == ("b", "pw")


def test_id_selector_for_missing_box_falls_back(page):
    page(El("u"), El("pw", password=True))
    assert ids(locate.find_fields(1, "#nope", "#nada")) == ("u", "pw")


def test_documents_skip_offscreen_and_native_host():
    good = El()
    worker.bind(FakeUia([El(offscreen=True), El(cls="NativeViewHost"), good]))
    try:
        assert locate.page_documents(1) == [good]
        assert locate.page_rect(1) == (10, 20, 210, 50)
    finally:
        worker.bind(None)


def test_page_rect_none_without_document():
    worker.bind(FakeUia([]))
    try:
        assert locate.page_rect(1) is None
    finally:
        worker.bind(None)


def test_host_matches_service(page):
    page(url="services.gst.gov.in/services/login")
    assert locate.host_matches_service(1, {"login_page_link": "https://services.gst.gov.in/services/login"})
    assert not locate.host_matches_service(1, {"login_page_link": "https://eportal.incometax.gov.in/"})
    assert not locate.host_matches_service(1, {"login_page_link": ""})
    page(url="https://services.gst.gov.in.evil.com/")
    assert not locate.host_matches_service(1, {"login_page_link": "https://services.gst.gov.in/"})


def test_window_for_host_foreground_first(monkeypatch):
    urls = {1: "https://services.gst.gov.in/a", 2: "https://services.gst.gov.in/b", 3: "https://x.example.com/"}
    worker.bind(SimpleNamespace(url=lambda h: urls[h]))
    monkeypatch.setattr(locate, "browser_windows", lambda: [1, 2, 3])
    monkeypatch.setattr(locate, "_is_browser", lambda h: h in urls)
    monkeypatch.setattr(locate.user32, "GetForegroundWindow", lambda: 2, raising=False)
    try:
        assert locate.window_for_host("https://services.gst.gov.in/") == 2
        assert locate.window_for_host("https://eportal.incometax.gov.in/") == 0
    finally:
        worker.bind(None)


def test_worker_runs_on_own_thread_and_stops():
    import threading
    fut = worker.submit(lambda a, b: (a + b, threading.current_thread().name), 1, 2)
    total, name = fut.result(timeout=5)
    assert total == 3 and name == "os-assist-uia"
    bad = worker.submit(lambda: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        bad.result(timeout=5)
    worker.stop()
    assert worker.submit(lambda: 7).result(timeout=5) == 7
    worker.stop()
