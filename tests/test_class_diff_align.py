"""
SDIS pre-dev: alignment (tools/pre_dev/class_diff/align.py) and compare.py labels.

Fixtures (tests/class_diff_align/): two FICTIONAL clients read from a real Edge window with
key_probe.py --once. Client B has an extra notice line near the top and 5 list rows against A's 3,
so keys shift. Every label is the same on both pages and every client value differs, so:
  * a template text paired with a DIFFERENT text is a pairing error;
  * each of B's values must get its known label.
make_clients.py beside the fixtures regenerates the pages if they ever need re-reading.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).resolve().parent / "class_diff_align"
sys.path.insert(0, str(ROOT / "tools" / "pre_dev" / "class_diff"))

import align as align_mod  # noqa: E402
import compare  # noqa: E402
import keys  # noqa: E402

LABELS = {"Dashboard", "Returns", "Payments", "Profile", "Taxpayer details", "GSTIN :", "Legal Name :",
          "Trade Name :", "Registration Date :", "Filed returns", "Period", "Form", "Status", "ARN",
          "Ledger balance", "IGST", "CGST", "SGST", "Cash ledger", "Credit ledger",
          "Designed by the test team", "Help desk"}

EXPECTED = {"29BBBBB2222B2Z2": "GSTIN :", "BETA FOODS": "Legal Name :", "BETA KITCHEN": "Trade Name :",
            "15/08/2021": "Registration Date :"}
for _p, _a in [("Jun 2026", "-"), ("Mar 2026", "BB2903262222221"), ("Dec 2025", "BB2912252222222"),
               ("Sep 2025", "BB2909252222223"), ("Jun 2025", "BB2906252222224")]:
    EXPECTED[_p] = "Period"
    EXPECTED[_a] = "ARN"
for _ledger, _vals in (("Cash ledger", ("404", "505", "606")), ("Credit ledger", ("4444", "5555", "6666"))):
    for _v, _col in zip(_vals, ("IGST", "CGST", "SGST")):
        EXPECTED[_v] = f"{_ledger} / {_col}"


def _fixture_name(c, browser):
    return f"client_{c}.json" if browser == "edge" else f"client_{c}_{browser}.json"


# "edge" = the original client_A/B.json; the others are W1-7's reads (tools/browser_parity.py).
ALIGN_BROWSERS = [b for b in ("edge", "msedge", "chrome", "firefox") if (FIX / _fixture_name("A", b)).exists()
                  and (FIX / _fixture_name("B", b)).exists()]


def _load(c, browser="edge"):
    return json.loads((FIX / _fixture_name(c, browser)).read_text(encoding="utf-8"))


@pytest.fixture(params=ALIGN_BROWSERS)
def setup(monkeypatch, request):
    def _run(view, aligned):
        monkeypatch.setattr(keys, "VIEW", view)
        monkeypatch.setattr(compare, "ALIGN", aligned)
        fl, fp = keys.flatten(_load("B", request.param)), keys.flatten(_load("A", request.param))
        return fl, fp, compare._partners(fl, fp), compare.compare_flat(fl, fp)
    return _run


def _pairing_errors(fl, fp, pairs):
    return sum(1 for i, j in pairs.items()
               if fl[i]["text"] != fp[j]["text"] and (fl[i]["text"] in LABELS or fp[j]["text"] in LABELS))


def _labelled_right(rows):
    got = {}
    for r in rows:
        if r["example_value"] in EXPECTED and r["status"] != compare.ONLY_PREVIOUS:
            got.setdefault(r["example_value"], set()).add(r["label"].strip())
    return {v for v, want in EXPECTED.items() if want in got.get(v, set())}


@pytest.mark.parametrize("view", ["raw", "sgt"])
def test_alignment_pairs_every_template_text_correctly(setup, view):
    fl, fp, pairs, rows = setup(view, True)
    assert _pairing_errors(fl, fp, pairs) == 0


@pytest.mark.parametrize("view", ["raw", "sgt"])
def test_every_value_gets_its_label(setup, view):
    *_, rows = setup(view, True)
    assert _labelled_right(rows) == set(EXPECTED)


def test_fixture_still_shifts_keys_without_alignment(setup):
    """Guard: the pages must keep exercising the shift, else the tests above prove nothing."""
    fl, fp, pairs, rows = setup("sgt", False)
    assert _pairing_errors(fl, fp, pairs) > 10


def test_extra_list_rows_are_only_latest_not_mispaired(setup):
    *_, rows = setup("raw", True)
    only = {r["example_value"] for r in rows if r["status"] == compare.ONLY_LATEST}
    assert {"Sep 2025", "Jun 2025", "BB2909252222223", "BB2906252222224"} <= only


# ── align.py on hand-made pages ────────────────────────────────────────────────

def _flat(items):
    """[(key, text)] -> keys.flatten-shaped entries (only what align needs)."""
    return [{"key": k, "text": t} for k, t in items]


def test_inserted_line_does_not_shift_later_pairs():
    a = _flat([("D / Text[1]", "Name :"), ("D / Text[2]", "ALPHA"), ("D / Text[3]", "City :"), ("D / Text[4]", "PUNE")])
    b = _flat([("D / Text[1]", "Notice!"), ("D / Text[2]", "Name :"), ("D / Text[3]", "BETA"),
               ("D / Text[4]", "City :"), ("D / Text[5]", "GOA")])
    pairs = align_mod.align(b, a, lambda e: True)
    assert pairs == {1: 0, 2: 1, 3: 2, 4: 3}            # the notice (0) pairs nothing


def test_moved_block_is_left_unpaired_never_mispaired():
    a = _flat([("D / Text[1]", "Name :"), ("D / Text[2]", "ALPHA"), ("D / Text[3]", "City :"), ("D / Text[4]", "PUNE")])
    b = _flat([("D / Text[1]", "City :"), ("D / Text[2]", "GOA"), ("D / Text[3]", "Name :"), ("D / Text[4]", "BETA")])
    pairs = align_mod.align(b, a, lambda e: True)
    for i, j in pairs.items():                          # whatever pairs, label pairs with the same label
        if b[i]["text"].endswith(":") or a[j]["text"].endswith(":"):
            assert b[i]["text"] == a[j]["text"]
