"""
tools/pre_dev/class_diff/render.py - PRE-DEV TOOL: a raw UIA read as an HTML page that looks like the page the browser showed
=================================================================================================
Plan: docs/uia_render/plan.md. Engine only: no Qt, no UIA, no network, no portal knowledge. It uses
what the read holds: control type, screen box, name, value, class names, ids, aria.

Same input, same bytes: nothing depends on the time, random numbers, dict order or the machine.
Python decides what is drawn and where. The page's script only measures text to fit its box.

usage: python tools/pre_dev/class_diff/render.py <file> [--snapshot N] [--doc N|all] [--record N|list]
                                                  [--comp-view present|all] [--out PATH] [--open]
                                                  [--groups] [--arrange] [--outline]
"""

import argparse
import json
import re
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[3]
PRE_DEV_OUT = Path(__file__).resolve().parent / "output"

FONT_RATIO = 0.78      # font px = box height x this (plan 4.3)
FONT_MIN = 8
FIT_MIN = 7            # the page's script shrinks a text no lower than this
LINE_H_FALLBACK = 18
TAB_LINE_RATIO = 1.8   # a box taller than this many lines may need a wrap decision (plan 4.3)

# containers that get a look of their own (plan 4.4); Hyperlink is text only; ListItem only when active
LOOK_BOX = {"Button", "Table", "DataItem", "HeaderItem", "Header", "Edit", "ComboBox",
            "CheckBox", "RadioButton", "Image"}
PUA_LO, PUA_HI = 0xE000, 0xF8FF

_control_names = None


def _type_of(ctype):
    """UIA control type name. type_name in the read wins; CONTROL_TYPE_NAMES is imported only when needed,
    because tools/vsdc_uia_probe loads comtypes at import time."""
    global _control_names
    if ctype is None:
        return "Unknown"
    if _control_names is None:
        if str(REPO) not in sys.path:
            sys.path.insert(0, str(REPO))
        from tools.vsdc_uia_probe import CONTROL_TYPE_NAMES
        _control_names = CONTROL_TYPE_NAMES
    try:
        return _control_names.get(int(ctype), str(ctype))
    except (TypeError, ValueError):
        return str(ctype)


# ---------------------------------------------------------------- loading (plan 2)

def _record_lines(path):
    """Yield (index, line) for the non-empty lines of a record file. Streams: one line in memory at a time."""
    n = -1
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                n += 1
                yield n, line


def _load_record(path, record):
    chosen = None
    last = None
    for n, line in _record_lines(path):
        if record is None:
            last = (n, line)
        elif n == record:
            chosen = (n, line)
            break
    if record is None:
        chosen = last
    if chosen is None:
        raise ValueError(f"no record {record} in {path.name}")
    idx, line = chosen
    rec = json.loads(line)
    meta = _meta(rec, path, "record", idx, f"record {idx}")
    meta["ts"] = str(rec.get("ts") or "")
    return rec.get("docs") or [], meta


def _comp_docs(rec):
    """One merged doc. Each comp entry: its raw node, plus where it sits (parent indexes this list) and
    how it came and went (origin, present, first/last snapshot, values)."""
    merged = []
    for e in rec.get("nodes") or []:
        node = dict(e.get("node") or {})
        node["parent"] = e.get("parent", -1)
        for key in ("key", "origin", "first", "last", "values", "present"):
            if key in e:
                node[key] = e[key]
        merged.append(node)
    return [merged]


def _meta(rec, path, kind, index, label):
    return {"kind": kind, "source": path.name, "index": index, "label": label,
            "page": str(rec.get("page") or rec.get("url") or ""),
            "title": str(rec.get("title") or ""), "browser": str(rec.get("browser") or ""),
            "anchor": str(rec.get("anchor") or "")}


def load(path, snapshot=None, record=None):
    """(docs, meta). docs: a list of docs, each a list of raw nodes (the read's own fields).
    Kinds (plan 2), found from the content: a record line (jsonl: a dict with v and docs), a capture
    (capture true, snapshots), a comp page (comp_page true), or a single read (docs at the top level)."""
    path = Path(path)
    with open(path, encoding="utf-8") as f:
        first = f.readline()
    try:
        head = json.loads(first) if first.strip() else None
    except ValueError:
        head = None
    if isinstance(head, dict) and "v" in head and "docs" in head:
        return _load_record(path, record)

    rec = json.loads(path.read_text(encoding="utf-8"))
    if rec.get("comp_page"):
        return _comp_docs(rec), _meta(rec, path, "comp", None, "comp page")
    if rec.get("capture") or "snapshots" in rec:
        snaps = rec["snapshots"]
        idx = len(snaps) - 1 if snapshot is None else snapshot
        return snaps[idx]["docs"], _meta(rec, path, "capture", idx, f"snapshot {idx + 1} of {len(snaps)}")
    if "docs" in rec:
        return rec["docs"], _meta(rec, path, "read", None, "single read")
    raise ValueError(f"{path.name} is not a UIA read this renderer knows")


def list_records(path):
    """(index, ts, host, node count) per record line. Streams the file; the output holds no page text."""
    for n, line in _record_lines(path):
        rec = json.loads(line)
        nodes = sum(len(d) for d in rec.get("docs") or [])
        url = str(rec.get("url") or rec.get("link") or "")
        host = urlsplit(url if "://" in url else "//" + url).netloc  # recorder urls may have no scheme
        yield n, str(rec.get("ts") or ""), host, nodes


# ---------------------------------------------------------------- normalising (plan 2, 4.1)

def _rect(r):
    if not isinstance(r, (list, tuple)) or len(r) != 4:
        return None
    try:
        return [int(round(float(v))) for v in r]
    except (TypeError, ValueError):
        return None


def _pos(rect):
    return rect is not None and rect[2] > 0 and rect[3] > 0


def _norm(raw_doc):
    nodes = []
    size = len(raw_doc)
    for i, r in enumerate(raw_doc):
        p = r.get("parent")
        if not isinstance(p, int) or isinstance(p, bool) or not (0 <= p < size) or p == i:
            p = -1
        nodes.append({
            "i": i, "p": p, "depth": r.get("depth"), "ctype": r.get("ctype"),
            "type": str(r.get("type_name") or _type_of(r.get("ctype"))),
            "name": str(r.get("name") or ""), "value": str(r.get("value") or ""),
            "rect": _rect(r.get("rect")), "id": str(r.get("id") or ""), "cls": str(r.get("cls") or ""),
            "aria": str(r.get("aria") or ""), "required": bool(r.get("required")),
            "disabled": bool(r.get("disabled")), "key": r.get("key"), "origin": r.get("origin"),
            "first": r.get("first"), "last": r.get("last"), "values": r.get("values"),
            "present": r.get("present"),
        })
    return nodes


# ---------------------------------------------------------------- deciding (plan 4.2, 4.4, 4.6)

def _is_icon(s):
    return bool(s) and all(PUA_LO <= ord(c) <= PUA_HI for c in s)


def _active(n):
    return "active" in n["cls"].split() or bool(re.search(r"\bselected=true\b", n["aria"]))


def _in_link(n, nodes):
    """Text inside a Hyperlink, up to the nearest Button (plan 4.4)."""
    if n["type"] == "Button":
        return False
    cur, hops = n, 0
    while cur is not None and hops < 64:
        if cur["type"] == "Hyperlink":
            return True
        if cur["type"] == "Button":
            return False
        cur = nodes[cur["p"]] if cur["p"] >= 0 else None
        hops += 1
    return False


def _why(n):
    if n["rect"] is None:
        return "no rect"
    if not _pos(n["rect"]):
        return "zero size"
    if not n["in_view"]:
        return "left (not in present view)"
    if n["text_drawn"] or n["box_drawn"]:
        if n["text_drawn"] and n["box_drawn"]:
            return "drawn: box + text"
        return "drawn: box" if n["box_drawn"] else "drawn: text"
    if n["text"] and n["below"]:
        return "text drawn by a descendant"
    return "container without a look"


def _decide(nodes, comp_view):
    """Which nodes are in the view, which draw their text (innermost named node wins), which draw a look."""
    kids = {}
    for n in nodes:
        if n["p"] >= 0:
            kids.setdefault(n["p"], []).append(n["i"])
    for n in nodes:
        n["in_view"] = comp_view == "all" or n["present"] is not False
        n["text"] = "" if n["type"] == "Image" else (n["name"].strip() or n["value"].strip())
        n["draws"] = n["in_view"] and _pos(n["rect"]) and bool(n["text"])

    memo = {}

    def below(i):
        # True when some descendant draws its own text
        if i not in memo:
            memo[i] = False
            memo[i] = any(nodes[c]["draws"] or below(c) for c in kids.get(i, ()))
        return memo[i]

    for n in nodes:
        n["below"] = below(n["i"])
        n["text_drawn"] = n["draws"] and not n["below"]
        n["box_drawn"] = n["in_view"] and _pos(n["rect"]) and (
            n["type"] in LOOK_BOX or (n["type"] == "ListItem" and _active(n)))
        n["link"] = n["text_drawn"] and _in_link(n, nodes)
    for n in nodes:
        n["why"] = _why(n)
        n["drawn"] = n["text_drawn"] or n["box_drawn"]


def _line_h(nodes):
    """Plan 4.3: the median box height of the drawn Text nodes of this doc."""
    hs = sorted(n["rect"][3] for n in nodes if n["text_drawn"] and n["type"] == "Text")
    return hs[len(hs) // 2] if hs else LINE_H_FALLBACK


def _el(n, kind, lh, ox, oy):
    x, y, w, h = n["rect"]
    e = {"k": n["i"], "t": n["type"], "kind": kind, "x": x - ox, "y": y - oy, "w": w, "h": h,
         "muted": n["present"] is False, "dis": n["disabled"], "att": n["origin"] == "attached"}
    if kind == "text":
        icon = _is_icon(n["text"])
        e["txt"] = " ".join("U+%04X" % ord(c) for c in n["text"]) if icon else n["text"]
        e["icon"] = icon
        e["link"] = n["link"]
        if icon:
            e["fs"] = 8
        else:
            base = h * FONT_RATIO if n["type"] == "Text" else min(h, lh) * FONT_RATIO
            e["fs"] = max(FONT_MIN, round(base))
    else:
        e["req"] = n["required"]
        e["combo"] = n["type"] == "ComboBox"
        e["tab"] = n["type"] == "ListItem"
    return e


def _elements(nodes, lh, ox, oy):
    """Paint order (plan 4.5): boxes first, big to small, ties by index; then texts in index order."""
    els = []
    boxes = sorted((n for n in nodes if n["box_drawn"]),
                   key=lambda n: (-n["rect"][2] * n["rect"][3], n["i"]))
    els += [_el(n, "box", lh, ox, oy) for n in boxes]
    els += [_el(n, "text", lh, ox, oy) for n in nodes if n["text_drawn"]]
    return els


def _overlaps(els):
    """Plan 7: two drawn texts whose intersection is more than half the smaller one."""
    texts = [e for e in els if e["kind"] == "text"]
    count = 0
    for a_i, a in enumerate(texts):
        for b in texts[a_i + 1:]:
            ix = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
            iy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
            if ix > 0 and iy > 0 and ix * iy > 0.5 * min(a["w"] * a["h"], b["w"] * b["h"]):
                count += 1
    return count


def _group_els(raw, nodes, ox, oy):
    """Plan 7 (--groups): every group member boxed, and the texts of its distinct slots (or name slot)
    marked. Groups come from cards.py on this one doc, so a flat index is the raw index. Returns
    (box elements, {node index: marks}, number of groups)."""
    import cards
    flat, _ = cards.load_flat({"docs": [raw]})
    groups = cards.find_groups(flat, [raw])
    boxes, marks = [], {}
    for gi, g in enumerate(groups):
        for m in g["members"]:
            n = nodes[m["root"]]
            if n["rect"] and _pos(n["rect"]):
                x, y, w, h = n["rect"]
                boxes.append({"k": n["i"], "t": "GroupMember", "kind": "box", "x": x - ox, "y": y - oy,
                              "w": w, "h": h, "muted": False, "dis": False, "att": False, "req": False,
                              "combo": False, "tab": False, "gm": g["kind"]})
        name = g.get("name_slot")
        distinct = {slot for slot, info in cards.roles(g).items() if info["role"] == "distinct"}
        for m in g["members"]:
            for it in m["items"]:
                if it["slot"] == name:
                    marks[it["node"]] = "gname"
                elif it["slot"] in distinct:
                    marks.setdefault(it["node"], "gd")
    return boxes, marks, len(groups)


def _arrange_els(raw, nodes, ox, oy):
    """WP5 (--arrange): every member of every arrangement group boxed (the union of its leaves' boxes; nested
    members too), and the texts of the name slot and of distinct slots marked, as --groups does. A group's
    kind is its arrangement kind. Label-to-value arrows are not drawn yet (design section 4, WP5)."""
    import arrange as arr
    import cards
    flat, _ = cards.load_flat({"docs": [raw]})
    a = arr.arrange(flat)
    boxes, marks = [], {}
    for g in a.groups:
        for (lo, hi) in g.members:
            leaves = [a.leaves[i] for i in range(lo, hi)]
            x0 = min(l.box[0] for l in leaves)
            y0 = min(l.box[1] for l in leaves)
            x1 = max(l.box[0] + l.box[2] for l in leaves)
            y1 = max(l.box[1] + l.box[3] for l in leaves)
            k = leaves[0].node
            boxes.append({"k": k, "t": "ArrangeMember", "kind": "box", "x": x0 - ox, "y": y0 - oy,
                          "w": x1 - x0, "h": y1 - y0, "muted": False, "dis": False, "att": False, "req": False,
                          "combo": False, "tab": False, "gm": g.kind})
        name = arr.name_slot(g, {}, arr.NAME_CUT)
        for slot, idx in g.slots.items():
            for i in idx:
                if i is None:
                    continue
                if slot == name:
                    marks[a.leaves[i].node] = "gname"
                elif g.roles.get(slot) == "distinct":
                    marks.setdefault(a.leaves[i].node, "gd")
    return boxes, marks, len(a.groups)


_COUNTER = re.compile(r"\[\d+\]")


def _outline_els(raw, nodes, ox, oy):
    """--outline: every container (a node with children and a box, in the view) outlined. Each carries
    its depth, how many drawn texts it holds, and two shape ids: its key without the [n] counters
    (types, ids, classes) and its loose key without counters (types only). The page highlights every
    container of the same shape. Returns (outline elements, shape strings, loose strings), painted big
    to small so the innermost container is on top."""
    import cards
    flat, _ = cards.load_flat({"docs": [raw]})
    kids = {}
    for n in nodes:
        if n["p"] >= 0:
            kids.setdefault(n["p"], []).append(n["i"])
    texts = {}

    def held(i):
        # drawn texts at or below node i
        if i not in texts:
            texts[i] = int(nodes[i]["text_drawn"]) + sum(held(c) for c in kids.get(i, ()))
        return texts[i]

    conts = [n for n in nodes if n["i"] in kids and n["in_view"] and _pos(n["rect"])]
    shapes, loose = {}, {}
    els = []
    for n in sorted(conts, key=lambda n: (-n["rect"][2] * n["rect"][3], n["i"])):
        f = flat[n["i"]] if n["i"] < len(flat) else {"key": "", "loose": "", "depth": 0}
        sh = _COUNTER.sub("", f["key"])
        lo = _COUNTER.sub("", f["loose"])
        x, y, w, h = n["rect"]
        els.append({"k": n["i"], "t": n["type"], "kind": "ol", "x": x - ox, "y": y - oy, "w": w, "h": h,
                    "dep": f["depth"], "nt": held(n["i"]),
                    "sh": shapes.setdefault(sh, len(shapes)), "lo": loose.setdefault(lo, len(loose))})
    return els, list(shapes), list(loose)


def _section(k, raw, comp_view, groups=False, arrange=False, outline=False):
    nodes = _norm(raw)
    _decide(nodes, comp_view)
    view = [n for n in nodes if n["in_view"] and _pos(n["rect"])]
    ox = min((n["rect"][0] for n in view), default=0)
    oy = min((n["rect"][1] for n in view), default=0)
    W = max((n["rect"][0] + n["rect"][2] for n in view), default=0) - ox
    H = max((n["rect"][1] + n["rect"][3] for n in view), default=0) - oy
    lh = _line_h(nodes)
    els = _elements(nodes, lh, ox, oy)
    n_groups = 0
    if groups:
        boxes, marks, n_groups = _group_els(raw, nodes, ox, oy)
        for e in els:
            if e["kind"] == "text" and e["k"] in marks:
                e[marks[e["k"]]] = True
        els = boxes + els
    n_arrange = 0
    if arrange:
        boxes, marks, n_arrange = _arrange_els(raw, nodes, ox, oy)
        for e in els:
            if e["kind"] == "text" and e["k"] in marks:
                e[marks[e["k"]]] = True
        els = boxes + els
    ol_shapes = ol_loose = None
    if outline:
        ols, ol_shapes, ol_loose = _outline_els(raw, nodes, ox, oy)
        els = els + ols

    rows = []
    skipped = {}
    for n in nodes:
        row = {"i": n["i"], "depth": n["depth"], "t": n["type"], "id": n["id"], "cls": n["cls"],
               "name": n["name"], "value": n["value"], "aria": n["aria"], "key": n["key"],
               "origin": n["origin"], "first": n["first"], "last": n["last"], "values": n["values"],
               "present": n["present"], "box": n["rect"], "drawn": n["drawn"], "why": n["why"],
               "b": None}
        if n["in_view"] and _pos(n["rect"]):
            row["b"] = [n["rect"][0] - ox, n["rect"][1] - oy, n["rect"][2], n["rect"][3]]
        rows.append(row)
        if not n["drawn"]:
            skipped[n["why"]] = skipped.get(n["why"], 0) + 1
    texts = [e for e in els if e["kind"] == "text"]
    stats = {"nodes": len(nodes), "in_view": sum(1 for n in nodes if n["in_view"]),
             "texts": len(texts), "boxes": sum(1 for e in els if e["kind"] == "box"),
             "icons": sum(1 for e in texts if e["icon"]), "overlaps": _overlaps(els),
             "line_h": lh, "skipped": dict(sorted(skipped.items()))}
    if groups:
        stats["groups"] = n_groups
    if arrange:
        stats["arrange_groups"] = n_arrange
    sec = {"k": k, "label": f"doc {k}: {len(nodes)} nodes", "n": len(nodes), "W": W, "H": H,
           "lineH": lh, "els": els, "nodes": rows, "stats": stats}
    if outline:
        ols = [e for e in els if e["kind"] == "ol"]
        per = {}
        for e in ols:
            per[e["sh"]] = per.get(e["sh"], 0) + 1
        stats["outlines"] = len(ols)
        stats["outlines_2plus_texts"] = sum(1 for e in ols if e["nt"] >= 2)
        stats["shapes"] = len(per)
        stats["shapes_repeated"] = sum(1 for c in per.values() if c >= 2)
        sec["shapes"], sec["loose"] = ol_shapes, ol_loose
    return sec


def _pick_docs(docs, doc):
    """Plan 4.6: 'auto' = the doc with the most non-zero boxes; 'all' = every doc that has one; N = that doc."""
    if not docs:
        return []
    if doc == "all":
        pick = [k for k, d in enumerate(docs) if any(_pos(_rect(r.get("rect"))) for r in d)]
        return pick or [0]
    if doc == "auto":
        counts = [sum(1 for r in d if _pos(_rect(r.get("rect")))) for d in docs]
        return [max(range(len(docs)), key=lambda k: (counts[k], -k))]
    return [int(doc)]


def build(docs, meta, doc="auto", comp_view="present", groups=False, arrange=False, outline=False):
    """(html, summary). The summary has counts only: the caller may print it. groups=True draws the
    repeating groups of cards.py on top (plan docs/cards_rows/plan.md section 7); outline=True outlines
    every container and highlights same-shape containers on hover."""
    sections = [_section(k, docs[k], comp_view, groups, arrange, outline) for k in _pick_docs(docs, doc)]
    data = {"title": meta.get("title", ""), "page": meta.get("page", ""), "browser": meta.get("browser", ""),
            "source": meta.get("source", ""), "index": meta.get("label", ""), "comp_view": comp_view,
            "anchor": meta.get("anchor", ""), "outline": bool(outline), "docs": sections}
    blob = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).replace("</", "<\\/")
    summary = {"sections": [{"label": s["label"], **s["stats"]} for s in sections]}
    return TEMPLATE.replace("/*DATA*/null", blob), summary


def render_html(docs, meta, doc="auto", comp_view="present"):
    return build(docs, meta, doc, comp_view)[0]


# ---------------------------------------------------------------- output (plan 1, 5)

def default_out(src, meta):
    """render_<input stem>.html. The stem keeps its kind prefix (capture_, comp_, sdis_): a capture and a comp
    page from one session would otherwise share a name. Recorder and corpus inputs go to the pre-dev output
    folder, never beside the data (sdis_capture is shipped to the admin PC)."""
    name = src.stem
    if meta["kind"] == "record":
        name += f"_line{meta['index']}"
    to_pre_dev = src.suffix == ".jsonl" or src.parent.name == "sdis_capture" or "corpus" in src.parts
    folder = PRE_DEV_OUT if to_pre_dev else src.parent
    return folder / f"render_{name}.html"


def check_out(out):
    """Refuse the folders that must not get a render: recorder data, and tracked folders of the repo."""
    p = Path(out).resolve()
    if "sdis_capture" in p.parts:
        raise ValueError("never write a render into sdis_capture")
    pre = PRE_DEV_OUT.resolve()
    if REPO.resolve() in p.parents and pre not in p.parents:
        raise ValueError("inside the repo a render goes only to tools/pre_dev/class_diff/output")


def write_html(out, html):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Render a raw UIA read as an HTML page (plan: docs/uia_render/plan.md).")
    ap.add_argument("src", help="a key_probe capture or single read (.json), a comp page, or a record file (.jsonl)")
    ap.add_argument("--snapshot", type=int, help="capture: snapshot index (default: last)")
    ap.add_argument("--doc", default="auto", help="doc index, 'all', or 'auto' (default)")
    ap.add_argument("--record", help="record file: line index (default: last), or 'list' (writes nothing)")
    ap.add_argument("--comp-view", default="present", choices=["present", "all"])
    ap.add_argument("--out", help="output path (default: beside the input, see default_out)")
    ap.add_argument("--open", action="store_true", help="open the page in the default browser")
    ap.add_argument("--arrange", action="store_true",
                    help="draw the arrangement's groups (arrange.py, design section 3): member boxes, name and distinct slots; writes *_arrange.html")
    ap.add_argument("--groups", action="store_true",
                    help="draw the repeating groups (cards.py): members boxed, distinct slots marked; writes *_groups.html")
    ap.add_argument("--outline", action="store_true",
                    help="outline every container (colour by depth); hover highlights every same-shape container; writes *_outline.html")
    a = ap.parse_args(argv)
    src = Path(a.src)
    if not src.exists():
        newest = sorted(PRE_DEV_OUT.glob("comp_*.json"), key=lambda p: p.stat().st_mtime)[-3:]
        print(f"No such file: {src}")
        if newest:
            print("Newest comp pages in output/:")
            for p in reversed(newest):
                print(f"  {p.relative_to(REPO)}")
        return 1
    try:
        if a.record == "list":
            for n, ts, host, count in list_records(src):
                print(f"{n}\t{ts}\t{host}\t{count} nodes")
            return 0
        record = None if a.record is None else int(a.record)
        docs, meta = load(src, snapshot=a.snapshot, record=record)
        doc = a.doc if a.doc in ("auto", "all") else int(a.doc)
        out = Path(a.out) if a.out else default_out(src, meta)
        if a.groups and not a.out:
            out = out.with_name(out.stem + "_groups" + out.suffix)
        if a.arrange and not a.out:
            out = out.with_name(out.stem + "_arrange" + out.suffix)
        if a.outline and not a.out:
            out = out.with_name(out.stem + "_outline" + out.suffix)
        check_out(out)
        html, summary = build(docs, meta, doc=doc, comp_view=a.comp_view, groups=a.groups, arrange=a.arrange,
                              outline=a.outline)
    except ValueError as e:
        raise SystemExit(str(e))
    write_html(out, html)
    for s in summary["sections"]:
        print(f"{s['label']}: {s['nodes']} nodes, {s['texts']} texts, {s['boxes']} boxes, {s['overlaps']} overlapping texts"
              + (f", {s['groups']} groups" if "groups" in s else "")
              + (f", {s['arrange_groups']} arrangement groups" if "arrange_groups" in s else "")
              + (f", {s['outlines']} containers ({s['outlines_2plus_texts']} with 2+ texts), {s['shapes']} shapes"
                 f" ({s['shapes_repeated']} repeated)" if "outlines" in s else ""))
    print(f"-> {out}")
    if a.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>UIA raw view render</title>
<style>
  :root { --bar:#f4f4f6; --ink:#1d1d1f; --muted:#8a8a8e; --line:#c9c9ce; --hl:#ffb000; --att:#2a9d8f; }
  @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bar:#1c1c1e; --ink:#e5e5ea; --line:#48484a; --muted:#98989d; } }
  :root[data-theme="dark"] { --bar:#1c1c1e; --ink:#e5e5ea; --line:#48484a; --muted:#98989d; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bar); color:var(--ink); font:13px/1.4 system-ui, "Segoe UI", sans-serif; }
  header { position:sticky; top:0; z-index:10; background:var(--bar); border-bottom:1px solid var(--line);
           padding:8px 16px; display:flex; gap:16px; align-items:center; flex-wrap:wrap; }
  header b { font-weight:600; }
  header label { display:flex; gap:6px; align-items:center; cursor:pointer; }
  .meta { color:var(--muted); overflow-wrap:anywhere; }
  main { padding:8px 16px 32px; }
  h2 { font-size:13px; font-weight:600; margin:16px 0 6px; color:var(--muted); }
  .pagewrap { overflow:auto; }
  .page { position:relative; background:#fff; color:#1d1d1f; box-shadow:0 1px 4px rgba(0,0,0,.12); }
  .n { position:absolute; }
  .text { white-space:nowrap; display:flex; align-items:center; line-height:1; }
  .text .t { display:inline-block; white-space:nowrap; }
  .text.Button { justify-content:center; }
  .text.link { color:#1a4fd6; text-decoration:underline; }
  .text.icon { border:1px dashed #8a8a8e; border-radius:3px; color:#8a8a8e; font-size:8px !important; justify-content:center; }
  .box.GroupMember { border:2px dashed #d6336c; background:rgba(214,51,108,.05); }
  .text.gd { box-shadow:inset 0 0 0 1px #d6336c; }
  .text.gname { box-shadow:inset 0 0 0 2px #d6336c; background:rgba(214,51,108,.15); }
  .box.Button { border:1px solid #9aa0a8; border-radius:4px; background:#eef0f3; }
  .box.Edit, .box.ComboBox { border:1px solid #777; background:#fff; }
  .box.Table { border:1px solid #777; }
  .box.DataItem, .box.HeaderItem, .box.Header { border:1px solid #c9c9ce; }
  .box.Image { border:1px dashed #8a8a8e; background:repeating-linear-gradient(45deg,#f2f2f2 0 6px,#fafafa 6px 12px); }
  .box.CheckBox::before, .box.RadioButton::before { content:""; position:absolute; left:3px; top:50%; width:11px; height:11px;
           transform:translateY(-50%); border:1px solid #555; background:#fff; }
  .box.RadioButton::before { border-radius:50%; }
  .box.combo::after { content:"\25BE"; position:absolute; right:4px; top:50%; transform:translateY(-50%); color:#555; font-size:10px; }
  .box.tab { border:1px solid #c9c9ce; border-bottom:none; border-radius:4px 4px 0 0; }
  .req { position:absolute; right:-8px; top:0; color:#c0392b; }
  .muted { opacity:.4; }
  .muted.box { border-style:dashed; }
  .dis { opacity:.5; }
  .att::before { content:""; position:absolute; left:0; top:0; bottom:0; width:2px; background:var(--att); }
  .tag { display:none; position:absolute; right:0; top:0; background:#222; color:#fff; font:9px/1 ui-monospace,Consolas,monospace; padding:1px 2px; }
  body.idx .tag { display:block; }
  .ghost { display:none; outline:1px dotted rgba(255,0,0,.45); pointer-events:none; }
  body.boxes .ghost, .ghost.forced { display:block; }
  .n.hot { outline:2px solid var(--hl) !important; z-index:5; }
  details.nodes { margin-top:16px; }
  details.nodes summary { cursor:pointer; color:var(--muted); }
  table { border-collapse:collapse; font-size:12px; margin-top:6px; background:var(--bar); }
  th, td { border:1px solid var(--line); padding:2px 6px; text-align:left; vertical-align:top; max-width:260px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  tr[data-k] { cursor:pointer; }
  tr[data-k]:hover { background:rgba(255,176,0,.15); }
  .n.ol { display:none; pointer-events:none; border:1px solid; }
  body.ol-on .n.ol { display:block; }
  body.ol-on.ol-few .n.ol.few { display:none; }
  body.ol-on .n.ol.same { display:block; background:rgba(255,176,0,.10); outline:2px solid var(--hl); z-index:4; }
  body.ol-on .n.ol.cur { display:block; background:rgba(255,176,0,.22); outline:3px solid #e8590c; z-index:6; }
  .olc { display:none; }
  body.has-ol .olc { display:flex; }
  #olpin { color:#e8590c; }
  #tip { position:fixed; z-index:20; pointer-events:none; background:#222; color:#fff; font:12px/1.4 ui-monospace,Consolas,monospace;
         padding:6px 8px; border-radius:4px; max-width:520px; display:none; white-space:pre-wrap; }
</style>
</head>
<body>
<header>
  <b>UIA raw view render</b>
  <span class="meta" id="meta"></span>
  <label><input type="checkbox" id="boxes"> show every UIA box</label>
  <label><input type="checkbox" id="idx"> show node index</label>
  <label class="olc"><input type="checkbox" id="olon" checked> outline containers</label>
  <label class="olc"><input type="checkbox" id="olfew" checked> hide wrappers with &lt; 2 texts</label>
  <label class="olc">same shape by <select id="olby"><option value="sh">shape (types, ids, classes)</option><option value="lo">loose (types only)</option></select></label>
  <span class="meta olc">hover a container · [ ] parent / child · click pins · Esc clears <span id="olpin"></span></span>
  <span class="meta" id="sum"></span>
</header>
<main id="docs"></main>
<div id="tip"></div>
<script>
const D = /*DATA*/null;
const FIT_MIN = 7;
const body = document.body;
const tip = document.getElementById('tip');
const OLS = [];   // one per section with outlines: {d, ols, chain, level, pinned}

// --outline: the containers under the point, innermost first (hidden wrappers skipped)
function olChain(o, x, y) {
  const few = body.classList.contains('ol-few');
  const inner = o.chain[0];
  o.chain = o.ols.filter(e => (!few || e.nt >= 2) && x >= e.x && x < e.x + e.w && y >= e.y && y < e.y + e.h)
                 .sort((a, b) => a.w * a.h - b.w * b.h || b.dep - a.dep);
  if (o.chain[0] !== inner) o.level = 0;          // a new innermost container: start at it again
  o.level = Math.min(o.level, Math.max(0, o.chain.length - 1));
  olMark(o);
}
function olBy() { return document.getElementById('olby').value; }
function olMark(o) {
  for (const e of o.ols) e.div.classList.remove('same', 'cur');
  const cur = o.chain[o.level];
  if (!cur) return;
  const by = olBy();
  for (const e of o.ols) if (e[by] === cur[by]) e.div.classList.add('same');
  cur.div.classList.add('cur');
}
function olInfo(o) {
  const cur = o.chain[o.level];
  if (!cur) return '';
  const n = o.d.nodes[cur.k];
  const sh = o.ols.filter(e => e.sh === cur.sh).length, lo = o.ols.filter(e => e.lo === cur.lo).length;
  const cls = n.cls ? '  .' + n.cls.split(/\s+/).filter(Boolean).join('.') : '';
  return [
    'CONTAINER ' + n.t + (n.id ? '  #' + n.id : '') + cls,
    'index ' + n.i + '  depth ' + cur.dep + '  texts ' + cur.nt + '  box ' + cur.w + 'x' + cur.h,
    sh + ' like this by shape · ' + lo + ' by loose shape' + (olBy() === 'lo' ? '  (highlighting loose)' : '  (highlighting shape)'),
    'shape ' + o.d.shapes[cur.sh],
    'loose ' + o.d.loose[cur.lo],
    'level ' + (o.level + 1) + ' of ' + o.chain.length + '  ([ parent, ] child)' + (o.pinned ? '  PINNED' : ''),
  ].join('\n');
}
function olRefresh() { for (const o of OLS) olMark(o); }
document.addEventListener('keydown', ev => {
  for (const o of OLS) {
    if (!o.chain.length) continue;
    if (ev.key === '[') o.level = Math.min(o.level + 1, o.chain.length - 1);
    else if (ev.key === ']') o.level = Math.max(o.level - 1, 0);
    else if (ev.key === 'Escape') { o.pinned = false; o.chain = []; o.level = 0; document.getElementById('olpin').textContent = ''; }
    else continue;
    olMark(o);
    if (tip.style.display === 'block') tip.textContent = olInfo(o) || tip.textContent;
  }
});

function mk(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}
function place(e, b) {
  e.style.left = b[0] + 'px'; e.style.top = b[1] + 'px';
  e.style.width = b[2] + 'px'; e.style.height = b[3] + 'px';
}
function infoOf(n) {
  const cls = n.cls ? '  .' + n.cls.split(/\s+/).filter(Boolean).join('.') : '';
  const lines = [n.t + (n.id ? '  #' + n.id : '') + cls];
  lines.push('index ' + n.i + '  box ' + (n.box ? n.box.join(', ') : 'none'));
  if (n.name) lines.push('name ' + JSON.stringify(n.name));
  if (n.value) lines.push('value ' + JSON.stringify(n.value));
  if (n.aria) lines.push('aria ' + n.aria);
  if (n.key) lines.push('key ' + n.key);
  if (n.origin) lines.push('origin ' + n.origin + (n.first != null ? '  first ' + n.first : '') + (n.last != null ? '  last ' + n.last : ''));
  if (n.values) lines.push('values ' + JSON.stringify(n.values));
  if (n.present === false) lines.push('left the page (last box shown)');
  lines.push('drawn: ' + n.why);
  return lines.join('\n');
}
function drawEl(e, d) {
  const div = mk('div', 'n ' + e.kind + ' ' + e.t);
  div.dataset.k = e.k;
  place(div, [e.x, e.y, e.w, e.h]);
  if (e.kind === 'box') {
    if (e.tab) div.classList.add('tab');
    if (e.combo) div.classList.add('combo');
    if (e.req) div.appendChild(mk('span', 'req', '*'));
  } else {
    if (e.icon) div.classList.add('icon');
    if (e.link) div.classList.add('link');
    div.appendChild(mk('span', 't', e.txt));
    div.dataset.fs = e.fs;
    div.style.fontSize = e.fs + 'px';
  }
  if (e.gm) div.classList.add('gm');
  if (e.gd) div.classList.add('gd');
  if (e.gname) div.classList.add('gname');
  if (e.muted) div.classList.add('muted');
  if (e.dis) div.classList.add('dis');
  if (e.att) div.classList.add('att');
  div.appendChild(mk('span', 'tag', String(e.k)));
  div.dataset.info = infoOf(d.nodes[e.k]);
  return div;
}
function nodeTable(d, page) {
  const det = mk('details', 'nodes');
  det.appendChild(mk('summary', null, 'every UIA node (' + d.nodes.length + ') - click a row to find it in the render'));
  const t = mk('table');
  const head = mk('tr');
  for (const h of ['#', 'depth', 'type', 'name', 'value', 'box', 'classes', 'drawn', 'why']) head.appendChild(mk('th', null, h));
  t.appendChild(mk('thead')).appendChild(head);
  const tb = mk('tbody');
  for (const n of d.nodes) {
    const tr = mk('tr');
    tr.dataset.k = n.i;
    tr.appendChild(mk('td', null, String(n.i)));
    const dep = mk('td', null, n.depth == null ? '' : String(n.depth));
    tr.appendChild(dep);
    tr.appendChild(mk('td', null, n.t));
    tr.appendChild(mk('td', null, n.name));
    tr.appendChild(mk('td', null, n.value));
    tr.appendChild(mk('td', null, n.box ? n.box.join(', ') : 'none'));
    tr.appendChild(mk('td', null, n.cls));
    tr.appendChild(mk('td', null, n.drawn ? 'yes' : 'no'));
    tr.appendChild(mk('td', null, n.why));
    if (n.b) tr.addEventListener('click', () => hl(page, n.i));
    tb.appendChild(tr);
  }
  t.appendChild(tb);
  det.appendChild(t);
  return det;
}
function hl(page, k) {
  for (const h of page.querySelectorAll('.hot')) h.classList.remove('hot');
  for (const g of page.querySelectorAll('.forced')) g.classList.remove('forced');
  let first = null;
  for (const e of page.querySelectorAll('[data-k="' + k + '"]')) {
    e.classList.add('hot');
    if (e.classList.contains('ghost')) e.classList.add('forced');
    if (!first) first = e;
  }
  if (first) first.scrollIntoView({block: 'center', inline: 'center'});
}
function drawSection(d) {
  const sec = mk('section', 'doc');
  sec.appendChild(mk('h2', null, d.label + '  ·  ' + d.W + '×' + d.H + ' px'));
  const wrap = mk('div', 'pagewrap');
  const page = mk('div', 'page');
  page.style.width = d.W + 'px';
  page.style.height = d.H + 'px';
  for (const n of d.nodes) {
    if (!n.b) continue;
    const g = mk('div', 'n ghost ' + n.t);
    place(g, n.b);
    g.dataset.k = n.i;
    g.dataset.info = infoOf(n);
    page.appendChild(g);
  }
  const ols = [];
  for (const e of d.els) {
    if (e.kind === 'ol') {
      const div = mk('div', 'n ol' + (e.nt < 2 ? ' few' : ''));
      place(div, [e.x, e.y, e.w, e.h]);
      div.style.borderColor = 'hsl(' + ((e.dep * 53) % 360) + ',70%,45%)';
      div.dataset.k = e.k;
      e.div = div;
      ols.push(e);
      page.appendChild(div);
    } else {
      page.appendChild(drawEl(e, d));
    }
  }
  const OL = {d, ols, chain: [], level: 0, pinned: false};
  page.addEventListener('mousemove', ev => {
    let info = '';
    if (ols.length && body.classList.contains('ol-on')) {
      if (!OL.pinned) {
        const r = page.getBoundingClientRect();
        olChain(OL, ev.clientX - r.left, ev.clientY - r.top);
      }
      info = olInfo(OL);
    }
    const t = ev.target.closest('.n');
    if (t && t.dataset.info) info = info ? info + '\n\n- node under the mouse -\n' + t.dataset.info : t.dataset.info;
    if (!info) { tip.style.display = 'none'; return; }
    tip.textContent = info;
    tip.style.display = 'block';
    tip.style.left = Math.min(ev.clientX + 14, innerWidth - 540) + 'px';
    tip.style.top = Math.min(ev.clientY + 14, innerHeight - tip.offsetHeight - 4) + 'px';
  });
  page.addEventListener('mouseleave', () => {
    tip.style.display = 'none';
    if (!OL.pinned) { OL.chain = []; olMark(OL); }
  });
  page.addEventListener('click', () => {
    if (!ols.length || !OL.chain.length) return;
    OL.pinned = !OL.pinned;
    document.getElementById('olpin').textContent = OL.pinned ? '· pinned' : '';
  });
  OLS.push(OL);
  wrap.appendChild(page);
  sec.appendChild(wrap);
  sec.appendChild(nodeTable(d, page));
  return {sec, page};
}
// shrink each text until it fits its UIA box width (plus 1 px); report what was done
function fit(sec, d) {
  const st = {doc: d.k, texts: 0, shrunk: 0, shrunk30: 0, overflow: 0, tall_overflow: 0};
  for (const div of sec.querySelectorAll('.text:not(.icon)')) {
    st.texts++;
    const fs0 = parseFloat(div.dataset.fs), w = parseFloat(div.style.width), h = parseFloat(div.style.height);
    const sp = div.querySelector('.t');
    let fs = fs0;
    div.style.fontSize = fs + 'px';
    while (sp.offsetWidth > w + 1 && fs > FIT_MIN) { fs -= 0.5; div.style.fontSize = fs + 'px'; }
    if (fs < fs0) st.shrunk++;
    if (fs < 0.7 * fs0) st.shrunk30++;
    if (sp.offsetWidth > w + 1) { st.overflow++; if (h > 1.8 * d.lineH) st.tall_overflow++; }
  }
  return st;
}
document.getElementById('boxes').addEventListener('change', e => body.classList.toggle('boxes', e.target.checked));
document.getElementById('idx').addEventListener('change', e => body.classList.toggle('idx', e.target.checked));
if (D.outline) {
  body.classList.add('has-ol', 'ol-on', 'ol-few');
  document.getElementById('olon').addEventListener('change', e => body.classList.toggle('ol-on', e.target.checked));
  document.getElementById('olfew').addEventListener('change', e => body.classList.toggle('ol-few', e.target.checked));
  document.getElementById('olby').addEventListener('change', olRefresh);
}

const root = document.getElementById('docs');
const fits = [];
let texts = 0, nodes = 0;
for (const d of D.docs) {
  const {sec} = drawSection(d);
  root.appendChild(sec);
  fits.push(fit(sec, d));
  nodes += d.nodes.length;
}
if (!D.docs.length) root.appendChild(mk('p', 'meta', 'no UIA nodes in this read'));
for (const f of fits) texts += f.texts;
const shrunk = fits.reduce((a, f) => a + f.shrunk, 0);
document.getElementById('meta').textContent = [D.page, D.browser, D.source, D.index, D.comp_view ? 'comp view: ' + D.comp_view : ''].filter(Boolean).join(' · ');
document.getElementById('sum').textContent = nodes + ' UIA nodes · ' + texts + ' texts · ' + shrunk + ' shrunk to fit';
body.dataset.fit = JSON.stringify(fits);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
