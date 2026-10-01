"""
tools/pre_dev/class_diff/compare.py - PRE-DEV TEST: same page link, two clients, compared by key
=================================================================================================
For EVERY page link the latest capture visited, takes that session's MAP of the link (link_map.py: every node one capture - or one
single read - saw at that link, form, popup, success message, merged by key) and the map of the
most recent OTHER session of the same link, and sorts every text element into:

    fixed          same key, same text for both clients      -> template (labels, headings, menus)
    variable       same key, different text                  -> data (the values we want)
    only latest    the key is only in the latest client's map -> e.g. one more list row / card
    only previous  the key is only in the other client's map

Portal-neutral: an element is matched by its KEY (keys.py) - its address in the page built from
element types, stable ids and class names, counted per parent (card 1, card 2...), with generated
(digit) and state (ng-valid, focused...) class names left out. Nothing here knows what any class
name means, and nothing is portal-specific.

Label: for a variable element, the first fixed text inside the smallest box around it that has
one ("Acknowledgement No :" inside the same .valueBox as the number).

    python tools/pre_dev/class_diff/compare.py                         # latest client vs previous client
    python tools/pre_dev/class_diff/compare.py --latest A.json --previous B.json   # two single reads

Writes output/compare_<latest read's timestamp>.csv (opens in Excel). example_value is from the
latest read; an "only previous" row has none there, so it shows the previous read's value. It holds real values from
the page - client data - and stays in the git-ignored output folder on this PC.

page: the page link the row belongs to (a capture can visit several links; each is compared with
the previous session that visited the same link). key_matched: "yes" when the element's key was
found in both sessions (fixed / variable), "no" when only one side has it.

value_type: SGT-I's generic type of the value (pairs.classify_type) plus "period", "label", "sentence",
"alphanumeric";
previous_type the same for the previous read. check flags a type that contradicts the status:
variable with different types (likely misaligned), fixed but data-shaped (may be a coincidence),
variable but furniture-shaped (wording changed?).

Known limits of this test (on purpose, it is a test): only TWO reads are compared, so a value two
clients happen to share ("Filed By: self") shows as fixed - the real version needs 3+ clients;
lists are matched card n to card n.
"""

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "output"
ROOT = HERE.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.sgt_i.pairs import classify_type                     # noqa: E402

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from keys import BUTTON_CTYPES, CHOICE_CTYPES, flatten, page_slug   # noqa: E402
import link_map                                                  # noqa: E402

# Only these value types can be FIXED (template). Any other type (date, period, code, alphanumeric,
# amount, email, phone, percentage, yes/no, control) is data-shaped: when both sessions show the
# same value it is SEMI-VARIABLE - a value the clients compared so far happened to share - until a
# session with a different value makes it variable. "control" = a choice the user makes (dropdown,
# radio, checkbox - keys.CHOICE_CTYPES). Buttons are actions, not data: left out of the comparison.
FIXABLE_TYPES = frozenset({"text", "number", "label", "sentence"})
SENTENCE_WORDS = 8

FIXED, SEMI_VARIABLE, VARIABLE, ONLY_LATEST, ONLY_PREVIOUS = (
    "fixed", "semi-variable", "variable", "only latest", "only previous")
STATUSES = (FIXED, SEMI_VARIABLE, VARIABLE, ONLY_LATEST, ONLY_PREVIOUS)


def page_key(rec: Dict[str, Any]) -> str:
    return rec.get("page") or ("title: " + (rec.get("title") or ""))


# Period: a span of time rather than one day. Month words must be real month names, so a hyphenated
# word ("Non-filer", "e-Verify") never matches.
_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
          r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?")
_YEAR_PREFIX = r"(?:(?:a\.?\s?y\.?|f\.?\s?y\.?|assessment\s+year|financial\s+year|tax\s+year|year)\s*:?\s*)?"
_PERIOD_RES = tuple(re.compile(p, re.IGNORECASE) for p in (
    rf"^{_YEAR_PREFIX}(?:19|20)\d\d\s?[-–/]\s?(?:(?:19|20)\d\d|\d\d)$",   # A.Y. 2026-27, FY 2025-2026
    r"^(?:a\.?\s?y\.?|f\.?\s?y\.?)\s*:?\s*\d\d\s?[-–/]\s?\d\d$",          # AY 26-27, F.Y. 25-26 (prefix needed)
    rf"^{_MONTH}\s?[-–/',]?\s?(?:(?:19|20)\d\d|\d\d)$",                   # April 2026, Apr-2026, Apr'26
    rf"^{_MONTH}\s?[-–]\s?(?:19|20)\d\d$",                                 # January - 2021
    r"^(?:0?[1-9]|1[0-2])\s?[-/]\s?(?:19|20)\d\d$",                        # 04/2026, 4-2026
    rf"^q[1-4]\b.*$",                                                      # Q1 2026, Q1 (Apr-Jun)
    rf"^{_MONTH}\s?[-–]\s?{_MONTH}(?:\s?,?\s?(?:(?:19|20)\d\d|\d\d))?$",   # Apr-Jun 2026, April - June
))


def is_period(text: str) -> bool:
    v = " ".join(text.split())
    return any(r.match(v) for r in _PERIOD_RES)


def value_type(text: str) -> str:
    """SGT-I's generic type (core/sgt_i/pairs.classify_type: text, number, amount, date, code,
    email, phone, percentage, yes/no), plus four for this comparison: "period" (a year, month or
    quarter span - A.Y. 2026-27, AY 26-27, April 2026, Q1), "label" (text ending in ':'),
    "sentence" (SENTENCE_WORDS+ words) - those two almost always furniture - and "alphanumeric"
    (digits mixed with letters or punctuation that no other type claimed: 139(1), Flat 4B, Tower 2;
    "text" is then only words). The more
    specific type wins: a single letters+digits token stays "code" (PAN, ARN), a date stays
    "date", a period "period"; a label or sentence holding a digit stays label / sentence."""
    t = classify_type(text)
    if t != "text":
        return t
    if is_period(text):
        return "period"
    if text.rstrip().endswith(":"):
        return "label"
    if len(text.split()) >= SENTENCE_WORDS:
        return "sentence"
    if any(c.isdigit() for c in text):
        return "alphanumeric"      # digits mixed with letters or punctuation: 139(1), Flat 4B, 26-27
    return "text"


def element_type(e: Dict[str, Any]) -> str:
    """The value type of a flat entry: "control" for a choice element, else value_type(text)."""
    return "control" if e["node"].get("ctype") in CHOICE_CTYPES else value_type(e["text"])


def _compared(e: Dict[str, Any]) -> bool:
    """Elements that take part in the comparison: a text, and not a button."""
    return bool(e["text"]) and e["node"].get("ctype") not in BUTTON_CTYPES


def check(status: str, latest_type: str, previous_type: str, label: str = "") -> str:
    """Where a value's type contradicts its status. A variable long text WITH a label beside it is
    data (an address), not changed wording - only a label-less one is flagged."""
    if status == VARIABLE and latest_type != previous_type:
        return "types differ - likely misaligned (list shifted?)"
    if status == VARIABLE and latest_type in ("label", "sentence") and not label:
        return "furniture-shaped - wording may have changed"
    return "ok"


def _container(flat: List[Dict[str, Any]], i: int) -> str:
    """The nearest ancestor that has a class - the box the element sits in."""
    p = flat[i]["parent"]
    while p >= 0:
        if flat[p]["cls"]:
            return flat[p]["cls"]
        p = flat[p]["parent"]
    return ""


LABEL_LOOKBACK = 6     # fallback: how many elements back a label may sit when no box holds one


def _label(flat: List[Dict[str, Any]], i: int, fixed: set) -> str:
    """The label of element i: inside the smallest box around it that holds a fixed text, the
    fixed text NEAREST BEFORE it (else the first after it). When no box around it holds one - the
    browser can flatten a card, leaving "GSTIN OF TAXPAYER" and the value's box as plain siblings -
    the nearest fixed text at most LABEL_LOOKBACK elements before it in page order."""
    a = flat[i]["parent"]
    while a >= 0:
        before, after = "", ""
        j = a + 1
        while j < len(flat) and flat[j]["depth"] > flat[a]["depth"]:
            if j != i and j in fixed and flat[j]["text"]:
                if j < i:
                    before = flat[j]["text"]
                else:
                    after = after or flat[j]["text"]
                    break
            j += 1
        if before or after:
            return before or after
        a = flat[a]["parent"]
    for j in range(i - 1, max(-1, i - 1 - LABEL_LOOKBACK), -1):
        if j in fixed and flat[j]["text"]:
            return flat[j]["text"]
    return ""


def compare(latest: Dict[str, Any], previous: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Two single reads."""
    return compare_flat(flatten(latest), flatten(previous))


def compare_flat(fl: List[Dict[str, Any]], fp: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One row per text element of either side (a read's or a map's flat list), latest first."""
    prev_by_key = {e["key"]: e for e in fp if _compared(e)}
    latest_keys = {e["key"] for e in fl if _compared(e)}
    status: Dict[int, str] = {}
    for i, e in enumerate(fl):
        if not _compared(e):
            continue
        other = prev_by_key.get(e["key"])
        if other is None:
            status[i] = ONLY_LATEST
        elif other["text"] != e["text"]:
            status[i] = VARIABLE
        else:
            status[i] = FIXED if element_type(e) in FIXABLE_TYPES else SEMI_VARIABLE
    fixed = {i for i, s in status.items() if s == FIXED}

    def row(flat, i, st, example):
        e, n = flat[i], flat[i]["node"]
        other = prev_by_key.get(e["key"]) if flat is fl else None
        vt = element_type(e)
        pt = element_type(other) if other else ""
        label = _label(flat, i, fixed) if st != FIXED and flat is fl else ""
        return {"status": st, "key_matched": "yes" if st in (FIXED, SEMI_VARIABLE, VARIABLE) else "no",
                "value_type": vt, "previous_type": pt, "check": check(st, vt, pt, label),
                "label": label,
                "element": n.get("type_name") or n.get("ctype"), "classes": e["cls"],
                "container": _container(flat, i), "id": n.get("id", ""),
                "sgt_sees": "yes" if n.get("sgt") else "no", "example_value": example,
                "values_seen": " || ".join(e.get("values") or []),
                "key": e["key"]}

    rows = [row(fl, i, st, fl[i]["text"]) for i, st in status.items()]
    rows += [row(fp, i, ONLY_PREVIOUS, e["text"]) for i, e in enumerate(fp)
             if _compared(e) and e["key"] not in latest_keys]
    return rows


def _reads() -> List[Path]:
    return sorted(OUT_DIR.glob("key_probe_*.json"))


def pick(latest: Optional[str], previous: Optional[str]) -> Tuple[Path, Path]:
    reads = _reads()
    lp = Path(latest) if latest else (reads[-1] if reads else None)
    if lp is None:
        raise SystemExit(f"No reads in {OUT_DIR} yet - run key_probe.py on a page first.")
    if previous:
        return lp, Path(previous)
    key = page_key(json.loads(lp.read_text(encoding="utf-8")))
    for p in reversed(reads):
        if p.name < lp.name and page_key(json.loads(p.read_text(encoding="utf-8"))) == key:
            return lp, p
    raise SystemExit(f"No earlier read of the same page ({key}) - run key_probe.py on it for another client first.")


FIELDS = ["page", "status", "key_matched", "check", "label", "example_value", "values_seen", "value_type", "previous_type", "element", "classes",
          "container", "id", "sgt_sees", "key"]


def write_csv(out: Path, fields: List[str], rows: List[Dict[str, Any]]) -> Path:
    """Write rows (UTF-8 with BOM, so Excel reads it right). When `out` is open in Excel (locked),
    write beside it as _2, _3... Returns the path written."""
    out.parent.mkdir(parents=True, exist_ok=True)
    stem = out.stem
    for n in range(2, 100):
        try:
            with open(out, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                w.writeheader()
                w.writerows(rows)
            return out
        except PermissionError:
            out = out.with_name(f"{stem}_{n}.csv")
    raise SystemExit(f"Could not write {out} - close it in Excel and run again.")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Compare two clients' maps of the same page link (or two single reads).")
    ap.add_argument("--latest", help="a key_probe_*.json - compare two single reads instead of client maps")
    ap.add_argument("--previous", help="a key_probe_*.json (default with --latest: the newest earlier read of that page)")
    args = ap.parse_args(argv)

    # One comparison per page link: (page, latest side's name, other side's name, rows).
    results: List[Tuple[str, str, str, List[Dict[str, Any]]]] = []
    if args.latest or args.previous:
        lp, pp = pick(args.latest, args.previous)
        latest = json.loads(lp.read_text(encoding="utf-8"))
        previous = json.loads(pp.read_text(encoding="utf-8"))
        results.append((page_key(latest), lp.name, pp.name, compare(latest, previous)))
        stamp = link_map.read_stamp(lp)
    else:
        maps = link_map.build_maps()
        if not maps:
            raise SystemExit(f"No captures or reads in {OUT_DIR} yet - run key_probe.py on a page first.")
        # The latest session, and every page link it visited.
        last = max(maps.values(), key=lambda m: m.reads[-1])
        session = last.client
        stamp = session.split()[-1]
        for lm in sorted((m for m in maps.values() if m.client == session), key=lambda m: m.reads[0]):
            others = [m for m in maps.values() if m.page == lm.page and m.client != session]
            if not others:
                print(f"Page     : {lm.page}")
                print("  only this session has it - capture it for another client to compare")
                print()
                continue
            pm = max(others, key=lambda m: m.reads[-1])
            results.append((lm.page, f"{lm.client}: {len(lm.reads)} snapshot(s) merged",
                            f"{pm.client}: {len(pm.reads)} snapshot(s) merged",
                            compare_flat(lm.to_flat(), pm.to_flat())))
        if not results:
            raise SystemExit("Nothing to compare yet.")

    for page, l_name, p_name, page_rows in results:
        print(f"Page     : {page}")
        print(f"Latest   : {l_name}")
        print(f"Previous : {p_name}")
        counts = Counter(r["status"] for r in page_rows)
        matched = sum(1 for r in page_rows if r["key_matched"] == "yes")
        for st in STATUSES:
            print(f"  {st:14s} {counts.get(st, 0):5d}")
        print(f"  keys matched   {matched:5d} of {len(page_rows)}")
        flags = Counter(r["check"] for r in page_rows if r["check"] != "ok")
        for msg, k in flags.most_common():
            print(f"  check: {k:4d} x {msg}")
        # One CSV per page link, named after the link, so the same page's files sort together.
        out = write_csv(OUT_DIR / f"compare_{stamp}__{page_slug(page)}.csv", FIELDS,
                        [dict(r, page=page) for r in page_rows])
        print(f"CSV      : {out.name}")
        print()
    print("(the CSVs hold real page values - keep them on this PC)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
