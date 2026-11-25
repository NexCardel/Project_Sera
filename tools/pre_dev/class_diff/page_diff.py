"""
tools/pre_dev/class_diff/page_diff.py - PRE-DEV: the same page for two clients, the differences as a .txt
=========================================================================================================
Takes ONE page link as two clients saw it and writes every difference to a plain-text report that
ends with a summary. Nothing new is stored and no new matching is invented: the two sides are paired
by compare.py (compare.compare - align.py pairing, labels, type checks), exactly as compare.py --comp
pairs them; this file only turns those rows into a report a person reads top to bottom.

A side is any of (the same inputs compare.py and snapshot_diff.py read):
    comp_<start>_<n>__<page>.json      a comp page (key_probe.py writes one per page link it visited)
    capture_<start>_<n>__<page>.json   a capture: its comp page is rebuilt from its kept snapshots
    any read with "docs"               a key_probe_*.json single read (or a test fixture)
    <start>                            a capture's session stamp (20261124_005606): its comp_ file of
                                       --page in output/ (when the session visited one link, --page
                                       may be left out)
With --page alone (no sides): that link's latest comp page vs the latest one of ANOTHER client (other
session, no shared PAN / GSTIN), the pick compare.py --comp makes.

What counts as a difference (client A = the first side, B = the second):
    value differs   the same element on both pages, a different text           (compare: variable)
    only on A       a text on A's page that B's page has no partner for          (compare: only latest)
    only on B       the other way round                                        (compare: only previous)
    moved           the same text on both, but its key differs (a list row or notice above it shifted
                    it); listed so a shift is visible, never counted as a data difference
Texts both clients show alike (labels, headings, menus; compare's fixed / semi-variable /
variable_alignment) are only counted. Page facts (link, title, browser, comp page pages, node counts)
that differ are listed first.

SUMMARY: counts by kind, then the most significant ones - labelled values that differ (the data a
label names: "Legal Name : -> ALPHA FOODS / BETA FOODS"), the largest blocks only one client has
(texts in a row in the same box: a notice, extra list rows), and the rows compare.py's check flags
(types differ - likely misaligned, an ambiguous pairing, ...).

    python tools/pre_dev/class_diff/page_diff.py A B [--page LINK] [--out REPORT.txt]
    python tools/pre_dev/class_diff/page_diff.py --page LINK
    python tools/pre_dev/class_diff/page_diff.py A B --view sgt --align off     # as compare.py

Writes output/page_diff_<A>_vs_<B>__<page>.txt by default. The report holds real values from both
pages - client data - and stays in the git-ignored output folder on this PC; client ids in its header
are masked.
"""

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import compare as cmp                                            # noqa: E402  (puts ROOT on sys.path)
import snapshot_diff as sd                                       # noqa: E402
from core.sdis import keys                                       # noqa: E402
from core.sdis.identity import masked                            # noqa: E402
from core.sdis.keys import page_slug                             # noqa: E402

OUT_DIR = cmp.OUT_DIR
VALUE, ONLY_A, ONLY_B, MOVED = "value differs", "only on A", "only on B", "moved"
KINDS = (VALUE, ONLY_A, ONLY_B, MOVED)
SAME_STATUSES = (cmp.FIXED, cmp.SEMI_VARIABLE, cmp.VARIABLE_ALIGNMENT)
TOP = 10              # most significant items listed per heading in the summary
CLIP = 70             # characters of a text shown in a column
META_FIELDS = ("page", "title", "browser", "anchor")


# ── the two sides ────────────────────────────────────────────────────────────
class Side:
    """One client's page: the record compare.compare reads, and what the report says about it."""

    def __init__(self, rec: Dict[str, Any], name: str) -> None:
        self.rec, self.name = rec, name
        self.link = cmp.page_key(rec)
        self.flat = cmp.flat_of(rec)

    def to_flat(self) -> List[Dict[str, Any]]:          # what identity.client_ids reads
        return self.flat

    @property
    def tag(self) -> str:
        """A short name for the output file: the session stamp, else the file's stem."""
        s = self.rec.get("session") or str(self.rec.get("read_at") or "").replace("-", "").replace(":", "").replace("T", "_")
        return s or Path(self.name).stem[:40]


def _comps_of(stamp: str) -> List[Path]:
    return sorted(OUT_DIR.glob(f"comp_{stamp}_*__*.json"))


def load_side(arg: str, page: Optional[str] = None) -> Side:
    """A side from a file (comp_ / capture_ / any read with docs) or a session stamp (+ --page)."""
    p = Path(arg)
    if not p.exists() and not p.suffix:
        comps = []
        for c in _comps_of(arg):
            rec = json.loads(c.read_text(encoding="utf-8"))
            if rec.get("comp_page") and (page is None or cmp.page_key(rec) == page):
                comps.append((c, rec))
        if not comps:
            links = sorted({cmp.page_key(json.loads(c.read_text(encoding="utf-8"))) for c in _comps_of(arg)})
            raise SystemExit(f"{arg}: no comp page" + (f" of {page}" if page else "") + f" in {OUT_DIR}"
                             + (f" - this session visited: {', '.join(links)}" if links else ""))
        if len(comps) > 1:
            raise SystemExit(f"{arg}: visited {len(comps)} page links - name one with --page: "
                             + ", ".join(cmp.page_key(r) for _, r in comps))
        return Side(comps[0][1], comps[0][0].name)
    if not p.exists():
        raise SystemExit(f"{arg}: no such file")
    rec = json.loads(p.read_text(encoding="utf-8"))
    if rec.get("capture") and "snapshots" in rec:
        ap, _ = sd.comp_from_capture(p)
        rec = sd.comp_record(ap)
    elif not rec.get("comp_page") and not isinstance(rec.get("docs"), list):
        raise SystemExit(f"{p.name}: not a comp page, a capture or a read (no nodes / snapshots / docs)")
    return Side(rec, p.name)


def ids_of(side: Side) -> set:
    """The client's PAN / GSTIN on this page (empty when the page shows none or it cannot be read)."""
    try:
        from core.sdis.identity import client_ids
        return client_ids(side)
    except Exception:                                    # noqa: BLE001 - a header nicety, never a failure
        return set()


def latest_pair(page: str) -> Tuple[Side, Side]:
    """--page alone: the latest comp page of the link vs the latest of another client (compare.py --comp)."""
    comps = []
    for p in sorted(OUT_DIR.glob("comp_*.json")):
        rec = json.loads(p.read_text(encoding="utf-8"))
        if rec.get("comp_page") and cmp.page_key(rec) == page:
            s = Side(rec, p.name)
            comps.append((s, ids_of(s)))
    if not comps:
        raise SystemExit(f"No comp page of {page} in {OUT_DIR} - run key_probe.py on it first.")
    comps.sort(key=lambda c: c[0].rec.get("session") or c[0].name)
    a, aids = comps[-1]
    others = [c for c in comps[:-1] if c[0].rec.get("session") != a.rec.get("session") and not (c[1] & aids and aids)]
    if not others:
        raise SystemExit(f"Only one client has a comp page of {page} ({masked(aids)}) - capture it for another client.")
    return a, others[-1][0]


# ── the differences ──────────────────────────────────────────────────────────
def differences(a: Side, b: Side) -> Dict[str, Any]:
    """compare.compare's rows sorted into the report's kinds: {"items": {kind: [row]}, "same": n,
    "rows": every row, "meta": [(field, A's, B's)]}. A row is compare's, A = latest, B = previous."""
    rows = cmp.compare(a.rec, b.rec)
    items: Dict[str, List[Dict[str, Any]]] = {k: [] for k in KINDS}
    same = 0
    for r in rows:
        st = r["status"]
        if st == cmp.VARIABLE:
            items[VALUE].append(r)
        elif st == cmp.ONLY_LATEST:
            items[ONLY_A].append(r)
        elif st == cmp.ONLY_PREVIOUS:
            items[ONLY_B].append(r)
        elif st in SAME_STATUSES:
            same += 1
            if r["key_matched"] == "aligned":
                items[MOVED].append(r)
    return {"items": items, "same": same, "rows": rows, "meta": meta_differences(a, b)}


def meta_differences(a: Side, b: Side) -> List[Tuple[str, str, str]]:
    """Page facts that differ: link, title, browser, anchor, comp page pages, node and text counts."""
    def facts(s: Side) -> Dict[str, str]:
        f = {k: str(s.rec.get(k) or "") for k in META_FIELDS}
        f["page"] = s.link
        if s.rec.get("comp_page"):
            f["comp page pages"] = str(len(s.rec.get("pages") or []) or 1)
        f["nodes"] = str(len(s.flat))
        f["texts"] = str(sum(1 for e in s.flat if cmp._compared(e)))
        return f
    fa, fb = facts(a), facts(b)
    return [(k, fa.get(k, ""), fb.get(k, "")) for k in dict.fromkeys(list(fa) + list(fb))
            if fa.get(k, "") != fb.get(k, "")]


def blocks(rows: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """One-side rows (in page order) as runs that sit in the same box - a notice, a card, extra list rows."""
    out: List[List[Dict[str, Any]]] = []
    for r in rows:
        if out and out[-1][-1]["container"] == r["container"]:
            out[-1].append(r)
        else:
            out.append([r])
    return out


def significant(d: Dict[str, Any]) -> Dict[str, List[Any]]:
    """The summary's most significant differences: labelled values, the biggest one-side blocks, flags."""
    vals = d["items"][VALUE]
    labelled = [r for r in vals if r["label"]] + [r for r in vals if not r["label"]]
    big = sorted([(ONLY_A, blk) for blk in blocks(d["items"][ONLY_A])] +
                 [(ONLY_B, blk) for blk in blocks(d["items"][ONLY_B])], key=lambda x: -len(x[1]))
    flagged = [r for r in d["rows"] if r["check"] != "ok" and r["status"] not in SAME_STATUSES]
    return {"values": labelled[:TOP], "blocks": big[:TOP], "flagged": flagged[:TOP]}


# ── the report ───────────────────────────────────────────────────────────────
def _c(s: Any, n: int = CLIP) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[:n - 3] + "..."


def _lab(label: str) -> str:
    """A label followed by its value: "GSTIN :" stays as it is, "Period" becomes "Period:"."""
    s = _c(label, 40) or "(no label)"
    return s if s.endswith(":") else s + ":"


def _table(header: List[str], rows: List[List[str]]) -> List[str]:
    w = [max([len(h)] + [len(r[i]) for r in rows]) for i, h in enumerate(header)]
    line = lambda cells: "  " + "  ".join(c.ljust(w[i]) for i, c in enumerate(cells)).rstrip()   # noqa: E731
    return [line(header), line(["-" * x for x in w])] + [line(r) for r in rows]


def _section(title: str, n: int) -> List[str]:
    head = f"{title}  ({n})"
    return ["", head, "-" * len(head)]


def report(a: Side, b: Side, d: Dict[str, Any], aids: Optional[set] = None, bids: Optional[set] = None,
           made: Optional[datetime] = None) -> str:
    items = d["items"]
    out = ["PAGE DIFFERENCES - the same page, two clients",
           "=============================================",
           f"Page      : {a.link}" + ("" if a.link == b.link else f"   (B: {b.link})"),
           f"Client A  : {a.name}  [{masked(aids or set())}]  {len(a.flat)} nodes",
           f"Client B  : {b.name}  [{masked(bids or set())}]  {len(b.flat)} nodes",
           f"Compared  : view {keys.VIEW}, align {'on' if cmp.ALIGN else 'off'}"
           f"   made {(made or datetime.now()).strftime('%Y-%m-%d %H:%M:%S')}"]
    if a.link != b.link:
        out.append("WARNING   : the two sides are different page links - most of the page will differ")
    if aids and bids and aids & bids:
        out.append("WARNING   : both sides share a PAN / GSTIN - this may be the same client twice")
    elif not (aids and bids):
        out.append("NOTE      : no PAN / GSTIN on one side - make sure these are two different clients")
    out.append("Client data: keep this file on this PC.")

    out += _section("PAGE FACTS THAT DIFFER", len(d["meta"]))
    out += _table(["fact", "client A", "client B"], [[k, _c(x), _c(y)] for k, x, y in d["meta"]]) \
        if d["meta"] else ["  none"]

    out += _section("VALUES THAT DIFFER - the same element, a different text", len(items[VALUE]))
    out += _table(["#", "label", "client A", "client B", "element", "check"],
                  [[str(n), _c(r["label"], 40) or "-", _c(r["example_value"]), _c(r["previous_example"]),
                    str(r["element"]), "" if r["check"] == "ok" else r["check"]]
                   for n, r in enumerate(items[VALUE], 1)]) if items[VALUE] else ["  none"]

    for kind, who in ((ONLY_A, "A"), (ONLY_B, "B")):
        out += _section(f"ONLY ON CLIENT {who} - no partner on the other page", len(items[kind]))
        if not items[kind]:
            out.append("  none")
            continue
        n = 0
        for blk in blocks(items[kind]):
            out.append(f"  [{blk[0]['container'] or 'page'}]  {len(blk)} text(s)")
            for r in blk:
                n += 1
                lab = _lab(r["label"]) + " " if r["label"] else ""
                out.append(f"    {n:4d}. {lab}{_c(r['example_value'], 100)}   ({r['element']})")

    out += _section("MOVED - the same text on both, at a shifted key (not a data difference)", len(items[MOVED]))
    out += _table(["#", "text", "element"], [[str(n), _c(r["example_value"]), str(r["element"])]
                                             for n, r in enumerate(items[MOVED], 1)]) if items[MOVED] else ["  none"]

    # ── summary ──
    sig = significant(d)
    data = len(items[VALUE]) + len(items[ONLY_A]) + len(items[ONLY_B])
    out += ["", "", "SUMMARY", "======="]
    out += _table(["kind of difference", "count"],
                  [["values that differ", str(len(items[VALUE]))],
                   ["  of them labelled", str(sum(1 for r in items[VALUE] if r["label"]))],
                   ["texts only on client A", str(len(items[ONLY_A]))],
                   ["texts only on client B", str(len(items[ONLY_B]))],
                   ["blocks only on one side", str(len(blocks(items[ONLY_A])) + len(blocks(items[ONLY_B])))],
                   ["moved (same text, shifted key)", str(len(items[MOVED]))],
                   ["page facts that differ", str(len(d["meta"]))],
                   ["flagged by compare's check", str(sum(1 for r in d["rows"]
                                                          if r["check"] != "ok" and r["status"] not in SAME_STATUSES))],
                   ["texts the same on both", str(d["same"])]])
    total = data + d["same"]
    out.append(f"  {data} of {total} texts differ ({100 * data / total:.0f} %)." if total else "  no texts on either page.")

    out += ["", "Most significant:"]
    if sig["values"]:
        out.append("  Values that differ" + (" (labelled first):" if any(r["label"] for r in sig["values"]) else ":"))
        out += [f"    - {_lab(r['label'])} {_c(r['example_value'], 45)}  |  {_c(r['previous_example'], 45)}"
                for r in sig["values"]]
    if sig["blocks"]:
        out.append("  Largest blocks only one client has:")
        out += [f"    - only on {k[-1]}: {len(blk)} text(s) in [{blk[0]['container'] or 'page'}], "
                f"starting \"{_c(blk[0]['example_value'], 50)}\"" for k, blk in sig["blocks"]]
    if sig["flagged"]:
        out.append("  Flagged (worth a look):")
        out += [f"    - {r['check']}: \"{_c(r['example_value'], 45)}\"" for r in sig["flagged"]]
    if not (sig["values"] or sig["blocks"] or sig["flagged"]):
        out.append("  none - the two pages show the same texts")
    return "\n".join(out) + "\n"


def default_out(a: Side, b: Side) -> Path:
    return OUT_DIR / f"page_diff_{page_slug(a.tag)}_vs_{page_slug(b.tag)}__{page_slug(a.link)}.txt"


def run(a: Side, b: Side, out: Optional[str] = None) -> Path:
    aids, bids = ids_of(a), ids_of(b)
    d = differences(a, b)
    dest = Path(out) if out else default_out(a, b)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(report(a, b, d, aids, bids), encoding="utf-8")
    it = d["items"]
    print(f"Page     : {a.link}")
    print(f"A        : {a.name}  [{masked(aids)}]")
    print(f"B        : {b.name}  [{masked(bids)}]")
    print(f"  {len(it[VALUE])} values differ, {len(it[ONLY_A])} only on A, {len(it[ONLY_B])} only on B, "
          f"{len(it[MOVED])} moved, {d['same']} the same")
    print(f"Report   : {dest}")
    print("(the report holds real page values - keep it on this PC)")
    return dest


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="The same page for two clients: every difference, then a summary, "
                                             "as a .txt report.")
    ap.add_argument("sides", nargs="*", metavar="SIDE",
                    help="client A then client B: a comp_ / capture_ / read .json, or a session stamp (with --page)")
    ap.add_argument("--page", help="the page link (needed for a session stamp that visited several links; "
                                   "alone: that link's latest two clients)")
    ap.add_argument("--out", help="the report to write (default output/page_diff_<A>_vs_<B>__<page>.txt)")
    ap.add_argument("--view", choices=("raw", "sgt"), default="raw", help="as compare.py (default raw)")
    ap.add_argument("--align", choices=("on", "off"), default="on", help="as compare.py (default on)")
    args = ap.parse_args(argv)
    keys.VIEW = args.view
    cmp.ALIGN = args.align == "on"
    if len(args.sides) == 2:
        a, b = load_side(args.sides[0], args.page), load_side(args.sides[1], args.page)
    elif not args.sides and args.page:
        a, b = latest_pair(args.page)
    else:
        ap.error("give two sides (client A, client B), or --page alone")
    run(a, b, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
