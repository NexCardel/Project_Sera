"""
SGT replay and stress tests (2026-09-22). Every field-test bug so far was fixed once and could
quietly come back with the next spec edit; these replay fictional sessions (tests/sgt_golden/)
through SGT and pin what it writes - and hammer the resolver with the noise real pages have
(OCR confusions, repeated blocks, stray lines, lost lines), which must never crash it and must
never make it INVENT a value. Everything here is fictional.
"""
import json
import random
from pathlib import Path

import pytest

from core.sgt.sgt_corpus import PageRecorder, group_sessions, load_pages
from core.sgt.sgt_replay import diff, replay, replay_session
from core.sgt.sgt_specs import BUILTIN_FIELDS_PATH, SpecStore

GOLDEN = Path(__file__).resolve().parent / "sgt_golden"
SCENARIOS = sorted(GOLDEN.glob("*.json"))
STORE = SpecStore([BUILTIN_FIELDS_PATH], log=lambda m: None)
KEY_FIELDS = ("pan", "gstin", "filing_type", "period_label", "arn")


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def rows_of(result):
    return sorted(result["rows"].values(), key=lambda r: (r["period_label"], r["arn"]))


# ── The golden sessions replay exactly ───────────────────────────────────────────
@pytest.mark.parametrize("path", SCENARIOS, ids=[p.stem for p in SCENARIOS])
def test_golden_session(path):
    doc = load(path)
    res = replay_session(doc["pages"], STORE)
    assert rows_of(res) == doc["expect"]["rows"], doc["about"]
    assert len(res["held"]) == doc["expect"]["held"], doc["about"]


def test_there_are_positive_negative_and_held_scenarios():
    kinds = {p.stem.split("_")[0] for p in SCENARIOS}
    assert {"negative", "held"} <= kinds and len(SCENARIOS) >= 6


# ── Noise must never crash SGT or make it invent a value ────────────────────────
CONFUSE = {"O": "0", "0": "O", "I": "1", "1": "I", "S": "5", "5": "S", "B": "8", "8": "B", "l": "1"}
JUNK = ["Loading...", "Home", "Help", "Skip to main content", "×", "", "  ", "Menu", "12", "Page 1 of 1"]


def mutate(lines, rng):
    out = list(lines)
    for _ in range(rng.randint(1, 3)):
        op = rng.choice(["confuse", "duplicate", "junk", "drop", "blank"])
        if op == "confuse" and out:
            i = rng.randrange(len(out))
            chars = list(out[i])
            spots = [j for j, c in enumerate(chars) if c in CONFUSE]
            if spots:
                j = rng.choice(spots)
                chars[j] = CONFUSE[chars[j]]
                out[i] = "".join(chars)
        elif op == "duplicate" and out:
            a = rng.randrange(len(out))
            b = min(len(out), a + rng.randint(1, 4))
            out[b:b] = out[a:b]
        elif op == "junk":
            out.insert(rng.randint(0, len(out)), rng.choice(JUNK))
        elif op == "drop" and len(out) > 3:
            del out[rng.randrange(len(out))]
        elif op == "blank" and out:
            out[rng.randrange(len(out))] = ""
    return out


PLACEHOLDERS = {"", "N/A", "ITR", "GST Return"}         # what a row says when a value is unknown


def _alnum(text):
    return "".join(ch for ch in str(text).upper() if ch.isalnum())


def invented(value, clean_text):
    """A value is INVENTED when nothing like it is printed anywhere on the clean pages. A noisy
    read may miss a value, or take another real one from the page (an older card when the newest
    card's year was garbled) - that is found, not invented. 'AY 2024-25' is looked up as its
    years, 'August (FY 2026-27)' as its month and years."""
    if value in PLACEHOLDERS:
        return False
    parts = [p for p in str(value).replace("(", " ").replace(")", " ").split() if p not in ("AY", "FY")]
    return not all(_alnum(p) in clean_text for p in parts)


@pytest.mark.parametrize("path", [p for p in SCENARIOS if not p.stem.startswith("negative")],
                         ids=[p.stem for p in SCENARIOS if not p.stem.startswith("negative")])
def test_noise_never_invents_a_value(path):
    doc = load(path)
    clean_text = _alnum(" ".join(ln for p in doc["pages"] for ln in p["lines"]))
    rng = random.Random(f"sgt-{path.stem}")            # fixed: a failure always reproduces
    for n in range(60):
        pages = [dict(p, lines=mutate(p["lines"], rng)) for p in doc["pages"]]
        res = replay_session(pages, STORE)
        for row in res["rows"].values():
            for f in KEY_FIELDS:
                assert not invented(row.get(f, ""), clean_text), (
                    f"variant {n}: {f}={row.get(f)!r} was invented", [p["lines"] for p in pages])


@pytest.mark.parametrize("path", SCENARIOS, ids=[p.stem for p in SCENARIOS])
def test_shuffled_pages_never_crash(path):
    doc = load(path)
    rng = random.Random(f"shuffle-{path.stem}")
    for _ in range(30):
        pages = []
        for p in doc["pages"]:
            lines = list(p["lines"])
            rng.shuffle(lines)
            pages.append(dict(p, lines=lines))
        replay_session(pages, STORE)                      # must not raise


OTHER_CLIENT_PAN = "XYZAB9876C"
THIS_CLIENT_PAN = "ABCPD1234E"


def test_negative_sessions_stay_empty_under_noise():
    """The guarantee: while the other client's PAN is still READABLE on the page, their card is
    never attributed to this client (it may be written unattributed - nothing is dropped). (Noise that garbles that PAN itself leaves nothing to tell
    the card apart - those variants are skipped, and counted so the test stays meaningful.)"""
    for path in [p for p in SCENARIOS if p.stem.startswith("negative")]:
        doc = load(path)
        rng = random.Random(f"neg-{path.stem}")
        checked = 0
        for _ in range(60):
            pages = [dict(p, lines=mutate(p["lines"], rng)) for p in doc["pages"]]
            had_it = any(OTHER_CLIENT_PAN in " ".join(p["lines"]) for p in doc["pages"])
            still_readable = any(OTHER_CLIENT_PAN in " ".join(p["lines"]) for p in pages)
            if had_it and not still_readable:
                continue
            checked += 1
            for row in replay_session(pages, STORE)["rows"].values():
                # Nothing is dropped any more - a card whose owner is unclear is written
                # UNATTRIBUTED - but it must never be put under this session's client.
                assert row["pan"] != THIS_CLIENT_PAN and row["client_name"] == "", (
                    path.stem, row, [p["lines"] for p in pages])
        assert checked >= 40, path.stem


# ── Recording and the replay tool ────────────────────────────────────────────────
def test_the_recorder_writes_each_page_once_and_the_replay_reads_it_back(tmp_path):
    from datetime import date
    doc = load(GOLDEN / "itr_wizard_to_submission.json")
    rec = PageRecorder(tmp_path)
    for p in doc["pages"] + doc["pages"]:                  # the same pages twice: recorded once
        rec.record(session="s1", portal=p["portal"], url=p["url"], title=p["title"], source=p["source"],
                   lines=p["lines"], ts=p["ts"], today=date.fromisoformat(p["today"]))
    pages = load_pages(tmp_path)
    assert len(pages) == len(doc["pages"])
    assert list(group_sessions(pages)) == ["s1"]
    assert rows_of(replay(pages, STORE)["s1"]) == doc["expect"]["rows"]


def test_the_recorder_prunes_old_days(tmp_path):
    (tmp_path / "pages_2020-01-01.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "pages_2099-01-01.jsonl").write_text("{}\n", encoding="utf-8")
    assert PageRecorder(tmp_path).prune() == 1
    assert [p.name for p in tmp_path.iterdir()] == ["pages_2099-01-01.jsonl"]


def test_a_disabled_recorder_writes_nothing(tmp_path):
    from datetime import date
    PageRecorder(tmp_path, enabled=False).record(session="s", portal="Income Tax", url="u", title="",
                                                 source="uia", lines=["a"], ts=1.0, today=date.today())
    assert list(tmp_path.iterdir()) == []


def test_diff_names_what_a_spec_change_changes():
    before = {"s": {"rows": {"K1": {"arn": "1", "status": "Draft"}, "K2": {"arn": "2"}}, "held": []}}
    after = {"s": {"rows": {"K1": {"arn": "1", "status": "Submitted (Not Verified)"}, "K3": {"arn": "3"}},
                   "held": [{}]}}
    out = "\n".join(diff(before, after))
    assert "no longer written: K2" in out and "newly written:     K3" in out
    assert "status: 'Draft' -> 'Submitted (Not Verified)'" in out and "held datasets: 0 -> 1" in out


def test_the_replay_tool_runs(tmp_path, capsys):
    from datetime import date
    import tools.sgt_replay as tool
    doc = load(GOLDEN / "gst_return_filed.json")
    rec = PageRecorder(tmp_path)
    for p in doc["pages"]:
        rec.record(session="g", portal=p["portal"], url=p["url"], title="", source="uia",
                   lines=p["lines"], ts=p["ts"], today=date.fromisoformat(p["today"]))
    assert tool.main(["baseline", "--corpus", str(tmp_path)]) == 0
    assert tool.main(["diff", "--corpus", str(tmp_path)]) == 0
    assert "No difference from the baseline." in capsys.readouterr().out
