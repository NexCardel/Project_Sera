"""
SDIS's own recorder (Part K): offer() never blocks or raises, slow and empty reads are dropped,
a record carries every field, the size cap drops the oldest file, SgtShadow offers exactly the
pages its change gate let through, and the records feed link_map / mine like pre-dev captures.
Fakes only - no real UIA. Pages are the fictional fixtures in tests/class_diff_align/.
"""
import json
import time
from datetime import date
from pathlib import Path

from PIL import Image

from core.sdis import link_map, sources
from core.sdis.recorder import SdisRecorder

FIXTURES = Path(__file__).parent / "class_diff_align"
URL = "https://portal.test.local/returns/auth/gstr1#/summary?id=7"
DOCS = [[{"name": "Fictional page", "ctype": 50020, "parent": -1}]]


def _wait(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def _records(folder):
    return [json.loads(l) for p in sorted(folder.glob("sdis_*.jsonl"))
            for l in p.read_text(encoding="utf-8").splitlines()]


def test_offer_returns_at_once_with_a_slow_reader(tmp_path):
    rec = SdisRecorder(tmp_path, read_nodes=lambda h: (time.sleep(2), {"docs": DOCS})[1],
                       browser_of=lambda h: "chrome")
    t0 = time.perf_counter()
    rec.offer(1, "s1", "GST Portal", URL, "t")
    rec.offer(1, "s1", "GST Portal", URL, "t")
    assert time.perf_counter() - t0 < 0.005 * 2


def test_a_slow_read_is_dropped(tmp_path):
    rec = SdisRecorder(tmp_path, budget_s=0.05, read_nodes=lambda h: (time.sleep(0.1), {"docs": DOCS})[1],
                       browser_of=lambda h: "chrome")
    assert rec._take(1, "s1", "GST Portal", URL, "t") is None
    assert rec.stats["slow"] == 1 and _records(tmp_path) == []


def test_an_empty_read_is_dropped(tmp_path):
    rec = SdisRecorder(tmp_path, read_nodes=lambda h: {"docs": [[]]}, browser_of=lambda h: "chrome")
    assert rec._take(1, "s1", "GST Portal", URL, "t") is None
    assert rec.stats["empty"] == 1 and _records(tmp_path) == []


def test_a_fast_read_is_written_with_every_field(tmp_path):
    rec = SdisRecorder(tmp_path, read_nodes=lambda h: {"docs": DOCS}, browser_of=lambda h: "msedge")
    rec.offer(7, "abc123", "GST Portal", URL, "Fictional title")
    assert _wait(lambda: rec.stats["written"] == 1)
    (r,) = _records(tmp_path)
    assert r["v"] == 1 and r["session"] == "abc123" and r["portal"] == "GST Portal"
    assert r["url"] == URL and r["link"] == "portal.test.local/returns/auth/gstr1#/summary"
    assert r["title"] == "Fictional title" and r["browser"] == "msedge" and r["docs"] == DOCS
    assert r["ts"][:4].isdigit() and "T" in r["ts"] and r["started"] == r["ts"]
    assert (tmp_path / f"sdis_{r['ts'][:10]}.jsonl").exists()


def test_a_newer_offer_replaces_one_not_taken(tmp_path):
    seen = []

    def read(h):
        seen.append(h)
        time.sleep(0.2)
        return {"docs": DOCS}
    rec = SdisRecorder(tmp_path, read_nodes=read, browser_of=lambda h: "")
    rec.offer(1, "s", "", URL, "")
    assert _wait(lambda: seen == [1])
    for h in (2, 3, 4):
        rec.offer(h, "s", "", URL, "")
    assert _wait(lambda: rec.stats["written"] == 2)
    assert seen == [1, 4] and rec.stats["replaced"] == 2


def test_the_size_cap_deletes_the_oldest_file(tmp_path):
    old = tmp_path / "sdis_2026-01-01.jsonl"
    mid = tmp_path / "sdis_2026-01-02.jsonl"
    old.write_text("x" * 600, encoding="utf-8")
    mid.write_text("x" * 600, encoding="utf-8")
    rec = SdisRecorder(tmp_path, cap_bytes=1000, read_nodes=lambda h: {"docs": DOCS}, browser_of=lambda h: "")
    path = rec._take(1, "s", "", URL, "")
    assert path.exists() and mid.exists() and not old.exists()


def test_the_file_being_written_is_never_deleted(tmp_path):
    rec = SdisRecorder(tmp_path, cap_bytes=1, read_nodes=lambda h: {"docs": DOCS}, browser_of=lambda h: "")
    path = rec._take(1, "s", "", URL, "")
    assert path.exists()


def test_a_raising_reader_never_escapes(tmp_path):
    def boom(h):
        raise RuntimeError("UIA gone")
    rec = SdisRecorder(tmp_path, read_nodes=boom, browser_of=lambda h: "")
    rec.offer(1, "s", "", URL, "")
    assert _wait(lambda: rec.stats["failed"] == 1)
    rec._read_nodes = lambda h: {"docs": DOCS}           # the worker is still alive
    rec.offer(1, "s", "", URL, "")
    assert _wait(lambda: rec.stats["written"] == 1)


def test_offer_survives_anything(tmp_path):
    rec = SdisRecorder(tmp_path, read_nodes=lambda h: {"docs": DOCS})
    rec._cond = None                                     # anything at all going wrong inside
    rec.offer(1, "s", "", URL, "")


def test_switched_off_takes_nothing(tmp_path):
    rec = SdisRecorder(tmp_path, enabled=False, read_nodes=lambda h: {"docs": DOCS})
    rec.offer(1, "s", "", URL, "")
    assert rec._thread is None and rec._slot is None


# ── SgtShadow offers one page per change ─────────────────────────────────────────
class _StubSdis:
    enabled = True

    def __init__(self):
        self.offers = []

    def offer(self, hwnd, session, portal, url, title):
        self.offers.append((hwnd, session, portal, url, title))


def _shadow(tmp_path, sdis, page):
    from core.sgt.sgt_shadow import SgtShadow
    from core.sgt.sgt_specs import BUILTIN_FIELDS_PATH, SpecStore
    clock = [1_000_000.0]
    sgt = SgtShadow(store=SpecStore([BUILTIN_FIELDS_PATH], log=lambda m: None),
                    read_uia=lambda h: {"lines": list(page)}, log_dir=tmp_path / "shadow",
                    clock=lambda: clock[0], today=lambda: date(2026, 7, 15), echo=lambda m: None, sdis=sdis)

    def see(shade, url=URL):
        clock[0] += 0.35
        sgt.observe(1, "GST Portal", url, frame=Image.new("RGB", (64, 64), (shade,) * 3), title="T")
    return sgt, see


PAGE = ["Fictional Traders", "GSTIN", "27ABCDE1234F1Z5", "Return period", "September"]


def test_sgt_offers_each_changed_page_once(tmp_path):
    stub = _StubSdis()
    sgt, see = _shadow(tmp_path, stub, PAGE)
    see(100)
    see(100)
    see(100)
    assert len(stub.offers) == 1
    see(120)
    assert len(stub.offers) == 2
    hwnd, session, portal, url, title = stub.offers[-1]
    assert (hwnd, portal, url, title) == (1, "GST Portal", URL, "T")
    assert session == sgt._sessions[1].session_id


def test_sgt_offers_nothing_when_switched_off_or_raising(tmp_path):
    stub = _StubSdis()
    stub.enabled = False
    _sgt, see = _shadow(tmp_path, stub, PAGE)
    see(100)
    assert stub.offers == []

    class Boom:
        enabled = True

        def offer(self, *a):
            raise RuntimeError("x")
    _sgt, see = _shadow(tmp_path, Boom(), PAGE)
    see(100)                                             # SGT carries on


def test_sgt_reads_the_same_with_or_without_sdis(tmp_path):
    a, see_a = _shadow(tmp_path / "a", None, PAGE)
    b, see_b = _shadow(tmp_path / "b", _StubSdis(), PAGE)
    for shade in (100, 100, 120):
        see_a(shade)
        see_b(shade)
    sa, sb = a._sessions[1], b._sessions[1]
    assert (sa.reads, sorted(sa.profile)) == (sb.reads, sorted(sb.profile))


# ── The records feed link_map and mine like pre-dev captures ────────────────────
def _write_records(folder):
    folder.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, c in enumerate("AB"):
        docs = json.loads((FIXTURES / f"client_{c}.json").read_text(encoding="utf-8"))["docs"]
        ts = f"2026-10-0{i + 1}T10:00:00.000"
        lines.append(json.dumps({"v": 1, "ts": ts, "started": ts, "session": f"s{c}", "portal": "GST Portal",
                                 "url": URL, "link": "portal.test.local/x", "title": "T",
                                 "browser": "chrome", "docs": docs}))
    (folder / "sdis_2026-10-01.jsonl").write_text("\n".join(lines) + "\n{\"v\": 1, \"ts", encoding="utf-8")


def test_records_become_sources(tmp_path):
    _write_records(tmp_path)
    out = list(sources.read_records(tmp_path))
    assert len(out) == 2                                  # the half-written last line is skipped
    stamp, session, link, rec = out[0]
    assert stamp == "2026-10-01T10:00:00.000" and session == "sgt sA 2026-10-01T10:00:00.000"
    assert link == "portal.test.local/x" and set(rec) == {"docs", "browser"} and rec["browser"] == "chrome"
    assert link_map.file_sources(tmp_path / "sdis_2026-10-01.jsonl") == out
    assert [s[1] for s in link_map.all_sources(tmp_path)] == [s[1] for s in out]


def test_mine_takes_a_recorder_folder(tmp_path):
    from core.sdis.mine import mine
    _write_records(tmp_path / "cap")
    res = mine(tmp_path / "cap", state_path=tmp_path / "state.json.gz")
    assert res["processed"] == 2 and res["files"] == 1 and res["skipped"] == 0
    again = mine(tmp_path / "cap", state_path=tmp_path / "state.json.gz")
    assert again["processed"] == 0
