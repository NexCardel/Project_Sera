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


def test_selector_list_uses_its_id_parts(page):
    page(El("a"), El("b"), El("pw", password=True), El("passwordInput", password=True))
    sel = "#passwordInput, input[id='passwordInput'], input[type='password']"
    assert ids(locate.find_fields(1, "input [id=\"b\"]", sel)) == ("b", "passwordInput")
    assert ids(locate.find_fields(1, "#nope, input#a", "#nada, [id=pw]")) == ("a", "pw")


def test_uia_off_the_worker_thread_is_refused():
    worker.bind(None)
    with pytest.raises(RuntimeError):
        locate.find_fields(1)


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


LOGIN = "https://services.gst.gov.in/services/login"


def _windows(monkeypatch, urls, password_on=(), front=0, exes=None):
    """Fake browser windows: hwnd -> address bar; `password_on` = windows showing a password box."""
    worker.bind(SimpleNamespace(url=lambda h: urls[h]))
    monkeypatch.setattr(locate, "browser_windows", lambda: list(urls))
    monkeypatch.setattr(locate, "_is_browser", lambda h: h in urls)
    monkeypatch.setattr(locate, "_proc_name", lambda h: (exes or {}).get(h, "chrome.exe"))
    monkeypatch.setattr(locate.user32, "GetForegroundWindow", lambda: front, raising=False)
    monkeypatch.setattr(locate, "find_fields_cached",
                        lambda h, us=None, ps=None, want="": (None, "box" if h in password_on else None))


def test_login_window_is_the_one_on_the_login_link(monkeypatch):
    _windows(monkeypatch, {1: "https://x.example.com/", 2: "services.gst.gov.in/services/login/?a=1"})
    try:
        assert locate.login_window(LOGIN) == 2
    finally:
        worker.bind(None)


def test_login_window_is_the_one_showing_a_password_box(monkeypatch):
    _windows(monkeypatch, {1: "https://services.gst.gov.in/services/auth/dashboard",
                           2: "https://services.gst.gov.in/services/relogin"}, password_on={2})
    try:
        assert locate.login_window(LOGIN) == 2
    finally:
        worker.bind(None)


def test_a_logged_in_page_of_the_portal_is_never_a_login_window(monkeypatch):
    _windows(monkeypatch, {1: "https://services.gst.gov.in/services/auth/dashboard"})
    try:
        assert locate.login_window(LOGIN) == 0
        assert locate.window_for_host(LOGIN) == 1          # the portal is open, just not on its login page
    finally:
        worker.bind(None)


def test_login_window_tells_single_page_portal_pages_apart_by_fragment(monkeypatch):
    link = "https://eportal.incometax.gov.in/iec/foservices/#/login"
    _windows(monkeypatch, {1: "eportal.incometax.gov.in/iec/foservices/#/dashboard",
                           2: "eportal.incometax.gov.in/iec/foservices/#/login"})
    try:
        assert locate.login_window(link) == 2
    finally:
        worker.bind(None)


def test_login_window_front_window_wins_and_other_portals_are_skipped(monkeypatch):
    _windows(monkeypatch, {1: LOGIN, 2: LOGIN, 3: "https://eportal.incometax.gov.in/login"},
             password_on={3}, front=2)
    try:
        assert locate.login_window(LOGIN) == 2
        assert locate.login_window("") == 0
    finally:
        worker.bind(None)


def test_login_window_keeps_to_the_service_browser(monkeypatch):
    _windows(monkeypatch, {1: LOGIN, 2: LOGIN}, exes={1: "chrome.exe", 2: "msedge.exe"})
    try:
        assert locate.login_window(LOGIN, browser="edge") == 2
        assert locate.login_window(LOGIN, browser="firefox") == 0
        assert locate.login_window(LOGIN, browser="system_default") == 1
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


def test_worker_urgent_job_jumps_the_queue():
    import threading
    from core.os_assist import worker
    w = worker.Worker()
    gate, order = threading.Event(), []
    first = w.submit(gate.wait, 5)
    slow = w.submit(lambda: order.append("normal"))
    fast = w.submit_urgent(lambda: order.append("urgent"))
    gate.set()
    for f in (first, slow, fast):
        f.result(timeout=5)
    w.stop()
    assert order == ["urgent", "normal"]


# -- page_rect reuses the Document it found last (the whole-tree search is 100-200 ms on Chrome) -----
class CountingUia(FakeUia):
    def __init__(self, docs):
        super().__init__(docs)
        self.searches = 0

    def documents(self, hwnd):
        self.searches += 1
        return self.docs


def test_page_rect_reuses_the_document_found_last():
    uia = CountingUia([El()])
    worker.bind(uia)
    try:
        assert locate.page_rect(7) == (10, 20, 210, 50)
        assert locate.page_rect(7) == (10, 20, 210, 50)
        assert uia.searches == 1
    finally:
        worker.bind(None)


def test_page_rect_searches_again_when_the_document_goes_offscreen():
    first, second = El(), El(w=400)
    uia = CountingUia([first])
    worker.bind(uia)
    try:
        assert locate.page_rect(8) == (10, 20, 210, 50)
        first.CurrentIsOffscreen = True                # a tab switch
        uia.docs = [first, second]
        assert locate.page_rect(8) == (10, 20, 410, 50)
        assert uia.searches == 2
    finally:
        worker.bind(None)


def test_page_rect_searches_again_when_the_document_is_gone():
    class Dead:
        @property
        def CurrentIsOffscreen(self):
            raise OSError("element not available")

    uia = CountingUia([El()])
    worker.bind(uia)
    try:
        locate.page_rect(9)
        locate._DOC_CACHE[9] = (uia, Dead())          # a navigation dropped the element
        assert locate.page_rect(9) == (10, 20, 210, 50)
        assert uia.searches == 2
    finally:
        worker.bind(None)


def test_page_rect_cache_never_crosses_uia_objects():
    worker.bind(CountingUia([El()]))
    try:
        locate.page_rect(10)
    finally:
        worker.bind(None)
    worker.bind(CountingUia([]))
    try:
        assert locate.page_rect(10) is None
    finally:
        worker.bind(None)
