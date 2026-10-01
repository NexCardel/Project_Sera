"""
tools/sgt_i_id_census.py - are the portals' HTML ids stable enough to key nodes by?
====================================================================================
SGT-I keys a node by where it sits (zone, section headings, label), never by its box. A portal
that gives its fields a stable HTML id (id="gstin") would offer a sharper key - Chromium exposes
it as the UIA AutomationId, and core/sgt_i/uia_nodes.py records it on every node ("aid") from
corpus v3 on. This tool measures, on the pages SGT recorded on this PC, whether such ids exist
and hold still across sessions. It only reads the corpus; nothing is written.

    python tools/sgt_i_id_census.py                  # every v3 page in the corpus
    python tools/sgt_i_id_census.py --days 7 --min-sessions 3 --examples 15

What it reports, per portal:

* coverage  - share of text nodes, and of input fields (edit / combo box / spinner / radio /
              check box), that carry an id at all;
* fields    - each field label seen in >= --min-sessions sessions of the same page, sorted into
              stable id   the same id every session it was seen,
              shifting id different ids in different sessions (generated: useless as a key),
              partial id  one id, but missing in some sessions,
              no id       never an id,
              repeating   the label shows more than once on one read (table rows, lists) - an id
                          there names a row, so it is set aside rather than judged;
* ids       - each id seen on a page with >= --min-sessions sessions: present in >= 80% of that
              page's sessions, always the same control type, never twice on one read. Those
              three together make a usable key.

A "page" is the address path with its #/route (atlas.url_hint), so screens sharing an address are
one page here. A "session" is an SGT session, usually one client's login - not guaranteed.

Blind spot (checked live in Edge, 2026-09-30): the id is exposed on inputs, selects, check boxes,
buttons, links and headings, but an id on a plain <span>/<div> sits on a container that only
UIA's raw view has - the control view SGT records keeps the text and drops the id. So the field
numbers are sound; the text-node coverage undercounts ids on display-only values.

The corpus holds client data. Printed ids are shown as-is only when they hold no digit; any id
with a digit is shown as its masked shape (A/9). Labels have digits masked. Output stays on this
PC unless you share it.
"""

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.sgt.sgt_corpus import corpus_dir, load_pages          # noqa: E402
from core.sgt_i.atlas import url_hint                           # noqa: E402
from core.sgt_i.pairs import mask_shape                         # noqa: E402

ID_VERSION = 3                                   # first corpus version whose nodes carry "aid"
FIELD_CTYPES = frozenset({50002, 50003, 50004, 50013, 50016})   # checkbox, combo, edit, radio, spinner
STEADY_SHARE = 0.8                               # an id on >= this share of its page's sessions
LABEL_MAX = 48


def _visible_id(aid: str) -> str:
    return mask_shape(aid) if any(c.isdigit() for c in aid) else aid


def _visible_label(label: str) -> str:
    label = " ".join(label.split())
    label = "".join("9" if c.isdigit() else c for c in label)
    return label if len(label) <= LABEL_MAX else label[:LABEL_MAX - 1] + "…"


def _portal_nodes(docs: Iterable[List[Dict[str, Any]]]) -> Iterable[Dict[str, Any]]:
    """Every node of the read except Sera's own injected panel and everything inside it."""
    for doc in docs:
        own: Set[int] = set()
        for i, n in enumerate(doc):
            if n.get("own") or n.get("parent", -1) in own:
                own.add(i)
                continue
            yield n


def census(pages: Iterable[Dict[str, Any]], min_sessions: int = 3) -> Dict[str, Any]:
    """Pure counting over corpus records. Returns {"skipped_old": n, "portals": {portal: stats}}."""
    skipped_old = 0
    raw: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "reads": 0, "sessions": set(), "pages": defaultdict(set),
        "text": 0, "text_id": 0, "fields": 0, "fields_id": 0,
        # (page, aid) -> sessions, control types, reads, reads where it showed twice
        "ids": defaultdict(lambda: {"sessions": set(), "ctypes": Counter(), "reads": 0, "dup": 0}),
        # (page, label) -> sessions, sessions with an id, ids, reads where the label repeated
        "labels": defaultdict(lambda: {"sessions": set(), "with_id": set(), "ids": Counter(), "repeat": 0}),
    })
    for rec in pages:
        docs = rec.get("nodes")
        if not docs:
            continue
        if (rec.get("v") or 1) < ID_VERSION:
            skipped_old += 1
            continue
        p = raw[str(rec.get("portal") or "?")]
        page = url_hint(rec.get("url") or "") or "(no address)"
        session = str(rec.get("session") or "?")
        p["reads"] += 1
        p["sessions"].add(session)
        p["pages"][page].add(session)
        aids = Counter()
        label_nodes: Dict[str, List[str]] = defaultdict(list)
        for n in _portal_nodes(docs):
            name = (n.get("name") or "").strip()
            aid = n.get("aid") or ""
            ctype = n.get("ctype") or 0
            if name or n.get("value"):
                p["text"] += 1
                p["text_id"] += bool(aid)
            if ctype in FIELD_CTYPES:
                p["fields"] += 1
                p["fields_id"] += bool(aid)
                if name:
                    label_nodes[name].append(aid)
            if aid:
                aids[aid] += 1
                e = p["ids"][(page, aid)]
                e["sessions"].add(session)
                e["ctypes"][ctype] += 1
        for aid, count in aids.items():
            e = p["ids"][(page, aid)]
            e["reads"] += 1
            e["dup"] += count > 1
        for label, found in label_nodes.items():
            e = p["labels"][(page, label)]
            e["sessions"].add(session)
            if len(found) > 1:
                e["repeat"] += 1
                continue
            if found[0]:
                e["with_id"].add(session)
                e["ids"][found[0]] += 1

    portals: Dict[str, Any] = {}
    for portal, p in sorted(raw.items()):
        page_sessions = {pg: len(s) for pg, s in p["pages"].items()}
        fields = {"stable": [], "shifting": [], "partial": [], "none": [], "repeating": []}
        too_few_labels = 0
        for (page, label), e in p["labels"].items():
            seen = len(e["sessions"])
            if seen < min_sessions:
                too_few_labels += 1
                continue
            item = (page, label, seen, dict(e["ids"]))
            if e["repeat"]:
                fields["repeating"].append(item)
            elif not e["ids"]:
                fields["none"].append(item)
            elif len(e["ids"]) > 1:
                fields["shifting"].append(item)
            elif len(e["with_id"]) < seen:
                fields["partial"].append(item)
            else:
                fields["stable"].append(item)
        ids = {"judged": 0, "steady": 0, "one_type": 0, "unique": 0, "usable": 0, "one_session": 0}
        usable: List[Tuple[str, str, int, int]] = []
        for (page, aid), e in p["ids"].items():
            total = page_sessions.get(page, 0)
            if total < min_sessions:
                continue
            ids["judged"] += 1
            steady = len(e["sessions"]) >= STEADY_SHARE * total
            one_type = len(e["ctypes"]) == 1
            unique = e["dup"] == 0
            ids["steady"] += steady
            ids["one_type"] += one_type
            ids["unique"] += unique
            ids["one_session"] += len(e["sessions"]) == 1
            if steady and one_type and unique:
                ids["usable"] += 1
                usable.append((page, aid, len(e["sessions"]), total))
        portals[portal] = {
            "reads": p["reads"], "sessions": len(p["sessions"]), "pages": len(page_sessions),
            "pages_judged": sum(1 for n in page_sessions.values() if n >= min_sessions),
            "text": p["text"], "text_id": p["text_id"], "fields": p["fields"], "fields_id": p["fields_id"],
            "field_labels": fields, "labels_too_few": too_few_labels, "ids": ids, "usable_ids": usable,
        }
    return {"skipped_old": skipped_old, "portals": portals}


def _pct(a: int, b: int) -> str:
    return f"{100.0 * a / b:5.1f}%" if b else "    - "


def report(result: Dict[str, Any], min_sessions: int, examples: int, out=print) -> None:
    if result["skipped_old"]:
        out(f"Skipped {result['skipped_old']} older page reads (corpus v2 and earlier: ids were not recorded).")
    if not result["portals"]:
        out("No corpus v3 page reads with nodes yet. Run Sera with SGT-I on and page recording on, "
            "open real portal pages for a few clients, then run this again.")
        return
    for portal, s in result["portals"].items():
        out("")
        out("=" * 78)
        out(f"{portal}: {s['reads']} reads, {s['sessions']} sessions, {s['pages']} pages "
            f"({s['pages_judged']} with >= {min_sessions} sessions)")
        out("=" * 78)
        out(f"Coverage   text nodes with an id   {_pct(s['text_id'], s['text'])}  ({s['text_id']}/{s['text']})")
        out(f"           input fields with an id {_pct(s['fields_id'], s['fields'])}  ({s['fields_id']}/{s['fields']})")
        f = s["field_labels"]
        judged = sum(len(v) for v in f.values())
        out("")
        out(f"Field labels seen in >= {min_sessions} sessions of one page: {judged}"
            f"  (another {s['labels_too_few']} seen in fewer - not judged)")
        for key, text in (("stable", "stable id (same id every session)"),
                          ("shifting", "shifting id (generated - not a key)"),
                          ("partial", "partial id (missing in some sessions)"),
                          ("none", "no id"),
                          ("repeating", "repeating label (rows / lists) - set aside")):
            out(f"  {text:42s} {len(f[key]):5d}  {_pct(len(f[key]), judged)}")
        i = s["ids"]
        out("")
        out(f"Ids on pages with >= {min_sessions} sessions: {i['judged']}")
        out(f"  on >= {int(STEADY_SHARE * 100)}% of the page's sessions     {i['steady']:5d}  {_pct(i['steady'], i['judged'])}")
        out(f"  seen in one session only              {i['one_session']:5d}  {_pct(i['one_session'], i['judged'])}")
        out(f"  always the same control type          {i['one_type']:5d}  {_pct(i['one_type'], i['judged'])}")
        out(f"  never twice on one read               {i['unique']:5d}  {_pct(i['unique'], i['judged'])}")
        out(f"  usable key (all three)                {i['usable']:5d}  {_pct(i['usable'], i['judged'])}")
        if examples:
            if f["stable"]:
                out("")
                out("Examples - fields with a stable id:")
                for page, label, seen, found in sorted(f["stable"], key=lambda t: -t[2])[:examples]:
                    out(f"  {page[-38:]:38s} {_visible_label(label)!r} -> {_visible_id(next(iter(found)))}  ({seen} sessions)")
            if f["shifting"]:
                out("")
                out("Examples - fields whose id changes between sessions:")
                for page, label, seen, found in sorted(f["shifting"], key=lambda t: -t[2])[:examples]:
                    shown = ", ".join(sorted({_visible_id(a) for a in found}))
                    out(f"  {page[-38:]:38s} {_visible_label(label)!r}: {len(found)} ids ({shown[:60]})")
    out("")
    out("Notes: a page is the address path + #/route; a session is an SGT session (usually one "
        "client, not guaranteed). Few sessions per page = weak evidence either way. Text-node "
        "coverage undercounts: an id on a plain <span>/<div> is not in the control view SGT records.")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Measure whether portal HTML ids are stable node keys.")
    ap.add_argument("--corpus", type=Path, default=None, help=f"corpus folder (default {corpus_dir()})")
    ap.add_argument("--days", type=int, default=None, help="only the last N days of recordings")
    ap.add_argument("--min-sessions", type=int, default=3, help="sessions needed before a page / label is judged")
    ap.add_argument("--examples", type=int, default=10, help="examples to print per list (0 = none)")
    args = ap.parse_args(argv)
    if args.min_sessions < 2:
        ap.error("--min-sessions must be at least 2 (stability needs a comparison)")
    pages = load_pages(args.corpus, args.days)
    report(census(pages, args.min_sessions), args.min_sessions, args.examples)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
