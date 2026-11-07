"""UidSourceWatcher.tick with a fake reader: idle costs nothing, a page's text is reported once per change."""

from core.sca.uid_source_watch import UidSourceWatcher, page_texts

PAGE = "https://sheet.example.com/clients"


class FakeReader:
    def __init__(self):
        self.url, self.uid, self.url_reads, self.page_reads = PAGE + "?x=1", "ABCDE1234F", 0, 0

    def read_url(self, hwnd):
        self.url_reads += 1
        return self.url

    def read_docs(self, hwnd):
        self.page_reads += 1
        return [[{"name": "Client", "value": ""}, {"name": f"PAN {self.uid}", "value": ""}]]


def _watcher(reader, pages=(PAGE,), active=True, fg=1):
    got = []
    w = UidSourceWatcher(lambda: list(pages), lambda: active, got.append, reader=reader, foreground=lambda: fg)
    return w, got


def test_page_texts_keep_words_and_whole_labels_and_drop_short_ones():
    t = page_texts([[{"name": "PAN ABCDE1234F", "value": ""}, {"name": "ok", "value": "x"}]])
    assert "PAN ABCDE1234F" in t and "ABCDE1234F" in t and "PAN" in t and "ok" not in t


def test_reports_once_then_again_only_when_the_page_text_changes():
    r = FakeReader()
    w, got = _watcher(r)
    assert w.tick() and w.tick()
    assert len(got) == 1 and "ABCDE1234F" in got[0]
    r.uid = "PQRST5678K"
    w.tick()
    assert len(got) == 2 and "PQRST5678K" in got[1]


def test_leaving_the_page_forgets_it_so_coming_back_reports_again():
    r = FakeReader()
    w, got = _watcher(r)
    w.tick()
    r.url = "https://other.example.com/"
    assert w.tick() is False
    r.url = PAGE
    w.tick()
    assert len(got) == 2


def test_no_pages_or_sca_off_or_no_browser_costs_no_uia():
    for kw in ({"pages": ()}, {"active": False}, {"fg": 0}):
        r = FakeReader()
        w, got = _watcher(r, **kw)
        assert w.tick() is False
        assert r.url_reads == 0 and r.page_reads == 0 and got == []


def test_a_page_that_is_not_saved_is_never_read():
    r = FakeReader()
    r.url = "https://other.example.com/x"
    w, got = _watcher(r)
    assert w.tick() is False
    assert r.url_reads == 1 and r.page_reads == 0
