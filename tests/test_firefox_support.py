"""W1-6 (blueprint Part R.1): Firefox's address bar, window title and background tabs.

Fake UIA objects only - no browser, no portal.
"""
from types import SimpleNamespace

from core.sgt_i import uia_nodes
from core.vsdc import vsdc_uia_text as uia_text
from core.vsdc.vsdc_router import BROWSER_SUFFIX_RE, VSDCRouter

CT_COMBOBOX, CT_EDIT = 50003, 50004
VALUE_PATTERN = 10002


class FakeArray:
    def __init__(self, items):
        self._items = list(items)
        self.Length = len(self._items)

    def GetElement(self, i):
        return self._items[i]


class FakeValue:
    def __init__(self, value):
        self.CurrentValue = value

    def QueryInterface(self, _iface):
        return self


class FakeElement:
    def __init__(self, ctype, name="", auto_id="", value="", offscreen=False, children=()):
        self.CurrentControlType = ctype
        self.CurrentName = name
        self.CurrentAutomationId = auto_id
        self._value = value
        self._offscreen = offscreen
        self._children = list(children)

    @property
    def CurrentIsOffscreen(self):
        if isinstance(self._offscreen, Exception):
            raise self._offscreen
        return self._offscreen

    def GetCurrentPattern(self, _pid):
        return FakeValue(self._value)

    def _descendants(self):
        for c in self._children:
            yield c
            yield from c._descendants()

    def FindAll(self, _scope, condition):
        return FakeArray(e for e in self._descendants() if condition(e))

    def FindAllBuildCache(self, scope, condition, _cache):
        return self.FindAll(scope, condition)


class FakeUia:
    """CreatePropertyCondition / CreateOrCondition as plain predicates on the fake elements."""

    def ElementFromHandle(self, _hwnd):
        return self.root

    def CreatePropertyCondition(self, _prop, ctype):
        return lambda e: e.CurrentControlType == ctype

    def CreateOrCondition(self, a, b):
        return lambda e: a(e) or b(e)


CLIENT = SimpleNamespace(UIA_ControlTypePropertyId=30003, TreeScope_Descendants=4,
                         UIA_ValuePatternId=VALUE_PATTERN, IUIAutomationValuePattern=object)


def _url_of(window):
    uia = FakeUia()
    uia.root = window
    router = SimpleNamespace(_uia=uia, _uia_client=CLIENT, _cached_address_elements={})
    return VSDCRouter.extract_browser_url(router, 1)


# ---- address bar --------------------------------------------------------------------------

def test_firefox_combobox_address_bar_is_read():
    bar = FakeElement(CT_COMBOBOX, "Search with Google or enter address", "urlbar-input",
                      "https://example.test/page")
    assert _url_of(FakeElement(0, children=[bar])) == "https://example.test/page"


def test_chrome_edit_address_bar_still_read():
    bar = FakeElement(CT_EDIT, "Address and search bar", "", "example.test/page")
    assert _url_of(FakeElement(0, children=[bar])) == "example.test/page"


def test_page_dropdown_without_address_keyword_is_not_taken():
    dropdown = FakeElement(CT_COMBOBOX, "Financial year", "fy-select", "2025-26/x.y")
    assert _url_of(FakeElement(0, children=[dropdown])) is None


def test_address_bar_comes_before_page_dropdown():
    bar = FakeElement(CT_COMBOBOX, "Search with Google or enter address", "urlbar-input",
                      "https://example.test/a")
    dropdown = FakeElement(CT_COMBOBOX, "Period", "period", "x.y/z")
    assert _url_of(FakeElement(0, children=[bar, dropdown])) == "https://example.test/a"


# ---- window title -------------------------------------------------------------------------

def test_title_suffixes_clean_the_same_way():
    for title in ("dashboard — mozilla firefox", "dashboard – mozilla firefox",
                  "dashboard - google chrome", "dashboard - Microsoft Edge"):
        assert BROWSER_SUFFIX_RE.sub("", title).strip() == "dashboard"


def test_title_hyphen_inside_page_name_kept():
    assert BROWSER_SUFFIX_RE.sub("", "itr-4 part a - google chrome").strip() == "itr-4 part a"


# ---- background tabs ----------------------------------------------------------------------

def _window(*docs):
    return FakeElement(0, children=[FakeElement(50033, children=list(docs))])


def _doc(name, offscreen):
    return FakeElement(uia_text.UIA_DOCUMENT_CONTROL_TYPE_ID, name, offscreen=offscreen)


def _find(window):
    return [d.CurrentName for d in uia_text._find_document_elements(FakeUia(), CLIENT, window)]


def test_text_reader_skips_offscreen_documents():
    assert _find(_window(_doc("front", False), _doc("background", True))) == ["front"]


def test_text_reader_all_offscreen_reads_nothing():
    assert _find(_window(_doc("a", True), _doc("b", True))) == []


def test_text_reader_keeps_document_whose_offscreen_raises():
    assert _find(_window(_doc("unknown", RuntimeError("stale")), _doc("b", True))) == ["unknown"]


def _nodes_of(window, monkeypatch):
    uia = FakeUia()
    uia.root = window
    monkeypatch.setattr(uia_text, "user32", SimpleNamespace(IsWindow=lambda _h: True))
    monkeypatch.setattr(uia_text, "_get_uia_on_worker", lambda: (uia, CLIENT))
    monkeypatch.setattr(uia_text, "_run_with_timeout", lambda fn, _t: fn())
    monkeypatch.setattr(uia_nodes, "build_cache_request", lambda *_a: None)
    monkeypatch.setattr(uia_nodes, "_walk", lambda el, p, d, out: out.append(el.CurrentName))
    return uia_nodes.read_page_nodes(1)["docs"]


def test_node_reader_skips_offscreen_documents(monkeypatch):
    window = _window(_doc("front", False), _doc("background", True))
    assert _nodes_of(window, monkeypatch) == [["front"]]


def test_node_reader_all_offscreen_reads_nothing(monkeypatch):
    assert _nodes_of(_window(_doc("a", True), _doc("b", True)), monkeypatch) == []


def test_node_reader_keeps_document_whose_offscreen_raises(monkeypatch):
    window = _window(_doc("unknown", RuntimeError("stale")), _doc("b", True))
    assert _nodes_of(window, monkeypatch) == [["unknown"]]
