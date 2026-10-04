"""
core/sdis/tables.py - precise table extraction and re-rendering for SDIS
=========================================================================
Takes the raw-view nodes key_probe.py records (a --once read, a capture snapshot, or a test
fixture) and gives back every table on the page as a GRID: rows x columns, every cell's text, its
row/column span, the header rows, the caption, and nested tables. Then renders it back, as a text
grid and as an HTML page, so the extraction can be checked against the portal by eye.

What the browsers hand over (read live 2026-10-04 in Edge, Chrome and Firefox,
tests/class_diff_tables/): a <table> (or role=table/grid) is a Table element; each row a DataItem;
each cell a DataItem under it. There is NO row/column number and NO header flag in the raw view,
and a cell spanning rows appears only in its first row. So the grid is rebuilt from the screen
boxes - the cells' left/right edges give the column lines, the rows' top/bottom edges the row
lines, and a cell's span is how many lines its box crosses - not from the order of the cells.
A cell whose box is missing or empty (a hidden row) takes the next free slot of its row; a
production node's own grid position (core/sgt_i/uia_nodes "grid") is used when present.

Cell text is rebuilt from the cell's own content in page order - texts, link texts, a text box's
or dropdown's value, a button's name - never from the cell's name: Firefox puts a nested table's
whole text and a row's cells into names. A nested table is skipped (it is its own table, linked
to the cell), and so are a dropdown's option lists.

Header rows: the browsers do not mark <th>, so the leading rows that hold only labels (letters, no
digits unless the text is a period like "Apr 2026"; an empty cell only as a matrix's top-left
corner) are the header; a second header row is accepted only when the first one spans (a two-level
header: "Tax" over IGST / CGST / SGST). Header column: under an empty corner, a first column of
labels is the row headers of a matrix. A role=columnheader cell, when
a browser reports it, makes its row a header outright. Each column's name joins its header cells
top-down ("Tax / IGST"); records() gives each body row as {column name: text}.

Fix the kind, not the case: nothing here knows a portal, a class name or a wording.

Usage:
    python tools/pre_dev/class_diff/tables.py                       # the newest read in output/
    python tools/pre_dev/class_diff/tables.py FILE.json [--snapshot N] [--html] [--counts]
--html writes output/tables_<file>.html (open it beside the portal to compare); --counts prints
only each table's shape (no values), safe to paste anywhere. Real reads hold client data: the
text and HTML output stay in the git-ignored output folder on this PC.
"""

import argparse
import html as _html
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.sdis.paths import data_dir

OUT_DIR = data_dir()

CT_BUTTON, CT_COMBOBOX, CT_EDIT, CT_HYPERLINK, CT_IMAGE = 50000, 50003, 50004, 50005, 50006
CT_TEXT, CT_DATAGRID, CT_DATAITEM, CT_HEADERITEM, CT_TABLE = 50020, 50028, 50029, 50035, 50036
CT_CHECKBOX, CT_RADIO, CT_SPINNER = 50002, 50013, 50016
TABLE_ROLES = frozenset({"table", "grid", "treegrid"})
ROW_ROLES = frozenset({"row"})
CELL_ROLES = frozenset({"cell", "gridcell", "columnheader", "rowheader"})
HEADER_ROLES = frozenset({"columnheader"})
CAPTION_ROLES = frozenset({"caption"})
TOL = 3                  # pixels: two edges this close are the same grid line
TITLE_GAP = 80           # pixels: a heading this far above a nameless table is its title


# ── the node tree ─────────────────────────────────────────────────────────────

def _rect(n: Dict[str, Any]) -> Optional[Tuple[int, int, int, int]]:
    """(left, top, right, bottom) or None for a missing / empty box."""
    r = n.get("rect")
    if not r or len(r) < 4 or r[2] <= 0 or r[3] <= 0:
        return None
    return int(r[0]), int(r[1]), int(r[0]) + int(r[2]), int(r[1]) + int(r[3])


def _children(doc: List[Dict[str, Any]]) -> List[List[int]]:
    kids: List[List[int]] = [[] for _ in doc]
    for i, n in enumerate(doc):
        p = n.get("parent", -1)
        if 0 <= p < len(doc):
            kids[p].append(i)
    return kids


def _is_table(n: Dict[str, Any]) -> bool:
    return n.get("ctype") in (CT_TABLE, CT_DATAGRID) or (n.get("role") or "") in TABLE_ROLES


def _is_cell(n: Dict[str, Any]) -> bool:
    return n.get("ctype") in (CT_DATAITEM, CT_HEADERITEM) or (n.get("role") or "") in CELL_ROLES


def _is_row(n: Dict[str, Any], kids: List[int], doc) -> bool:
    """A row holds cells. A DataItem / role=row with at least one cell child."""
    if (n.get("role") or "") in ROW_ROLES:
        return True
    return n.get("ctype") == CT_DATAITEM and any(_is_cell(doc[k]) for k in kids)


# ── cell text ─────────────────────────────────────────────────────────────────

def _cell_text(doc, kids, i: int, nested: List[int]) -> str:
    """The cell's own content in page order; nested tables and option lists skipped."""
    parts: List[str] = []

    def walk(j: int) -> None:
        n = doc[j]
        ct = n.get("ctype")
        if _is_table(n):
            nested.append(j)
            return
        if ct in (CT_EDIT, CT_COMBOBOX, CT_SPINNER):
            if n.get("value"):
                parts.append(n["value"])
            return                                   # its children (a dropdown's options) repeat it
        if ct in (CT_BUTTON, CT_CHECKBOX, CT_RADIO) and not kids[j]:
            if n.get("name"):
                parts.append(n["name"])
            return
        if not kids[j]:
            if ct in (CT_TEXT, CT_HYPERLINK, CT_IMAGE) or not ct:
                if (n.get("name") or "").strip():
                    parts.append(n["name"].strip())
            return
        for k in kids[j]:
            walk(k)

    for k in kids[i]:
        walk(k)
    if not parts and not nested and not kids[i]:
        name = (doc[i].get("name") or "").strip()    # a cell holding bare text with no child
        if name:
            parts.append(name)
    return " ".join(" ".join(parts).split())


# ── one table ─────────────────────────────────────────────────────────────────

def _lines(edges: List[int]) -> List[int]:
    """Sorted grid lines: edges within TOL of each other are one line (their mean)."""
    out: List[List[int]] = []
    for e in sorted(edges):
        if out and e - out[-1][-1] <= TOL:
            out[-1].append(e)
        else:
            out.append([e])
    return [round(sum(g) / len(g)) for g in out]


def _index(lines: List[int], v: int) -> int:
    return min(range(len(lines)), key=lambda k: abs(lines[k] - v))


def _rows_of(doc, kids, t: int) -> Tuple[List[int], List[int]]:
    """(rows, caption nodes) of table t: rows found through any wrapper (rowgroup / thead /
    tbody groups); a caption or any other text before the first row is the caption."""
    rows: List[int] = []
    caption: List[int] = []

    def walk(j: int) -> None:
        for k in kids[j]:
            n = doc[k]
            if _is_table(n):
                continue
            if _is_row(n, kids[k], doc):
                rows.append(k)
            elif _is_cell(n):
                continue                             # a stray cell outside a row: not placed
            elif (n.get("role") or "") in CAPTION_ROLES or (not rows and n.get("ctype") == CT_TEXT):
                caption.append(k)
            else:
                walk(k)                              # rowgroup, thead, tbody...

    walk(t)
    return rows, caption


def _is_period(text: str) -> bool:
    """labels.py's period rule (A.Y. 2026-27, Apr 2026, Q1 ...), so both tools agree."""
    from core.sdis.labels import is_period
    return is_period(text)


def _header_like(text: str) -> bool:
    """A label: letters, and no digits unless the whole text is a period ("Apr 2026" heads a column)."""
    return (bool(text) and any(c.isalpha() for c in text)
            and (not any(c.isdigit() for c in text) or _is_period(text)))


def _label_row(rc: List[Dict[str, Any]]) -> bool:
    """Every filled cell a label; an empty cell only in the first column (a matrix's corner)."""
    filled = [c for c in rc if c["text"]]
    return (bool(filled) and all(_header_like(c["text"]) and not c["nested"] for c in filled)
            and all(c["text"] or c["col"] == 0 for c in rc))


def extract_table(doc, kids, t: int) -> Dict[str, Any]:
    n = doc[t]
    rows, caption_nodes = _rows_of(doc, kids, t)
    cells: List[Dict[str, Any]] = []
    for r, row in enumerate(rows):
        for k in kids[row]:
            if not _is_cell(doc[k]):
                continue
            nested: List[int] = []
            text = _cell_text(doc, kids, k, nested)
            cells.append({"node": k, "row": r, "text": text, "nested": nested,
                          "box": _rect(doc[k]), "grid": doc[k].get("grid"),
                          "role": doc[k].get("role") or "", "ctype": doc[k].get("ctype")})

    # Grid lines from the boxes: columns from every cell's left/right edge, rows from every row's
    # top/bottom edge.
    boxed = [c for c in cells if c["box"]]
    xs = _lines([c["box"][0] for c in boxed] + [c["box"][2] for c in boxed])
    row_boxes = [_rect(doc[r]) for r in rows]
    ys = _lines([b[1] for b in row_boxes if b] + [b[3] for b in row_boxes if b])
    placed_by = "boxes" if boxed and len(xs) > 1 else "order"

    taken: Dict[Tuple[int, int], int] = {}
    conflicts = 0
    for ci, c in enumerate(cells):
        rs = cs = 1
        if placed_by == "boxes" and c["box"]:
            l, tp, rt, bt = c["box"]
            col, col_end = _index(xs, l), _index(xs, rt)
            cs = max(1, col_end - col)
            if len(ys) > 1:
                rs = max(1, _index(ys, bt) - _index(ys, tp))
        elif c["grid"]:
            col = int(c["grid"][1])
        else:
            col = 0
            while (c["row"], col) in taken:
                col += 1
        r = c["row"]
        if any((r + dr, col + dc) in taken for dr in range(rs) for dc in range(cs)):
            conflicts += 1                           # boxes overlap: next free slot, 1x1
            col, rs, cs = 0, 1, 1
            while (r, col) in taken:
                col += 1
        c.update(col=col, rowspan=rs, colspan=cs)
        for dr in range(rs):
            for dc in range(cs):
                taken[(r + dr, col + dc)] = ci

    n_rows = max([r for r, _ in taken] + [len(rows) - 1]) + 1 if rows else 0
    n_cols = max([cc for _, cc in taken], default=-1) + 1

    # Header rows: role=columnheader rows, else the leading label-only rows (two-level only when
    # the first header row spans).
    def row_cells(r):
        return [c for c in cells if c["row"] == r]
    header_rows = 0
    for r in range(len(rows)):
        rc = row_cells(r)
        if rc and all(c["role"] in HEADER_ROLES or c["ctype"] == CT_HEADERITEM for c in rc):
            header_rows = r + 1
            continue
        if r == 0 and _label_row(rc) and len(rows) > 1:
            header_rows = 1
            continue
        if (r == header_rows and r > 0 and _label_row(rc)
                and any(c["rowspan"] > 1 or c["colspan"] > 1 for c in row_cells(r - 1))):
            header_rows = r + 1
            continue
        break
    # A header COLUMN: a matrix - the header's corner is empty and every body row starts with a
    # label ("Returns filed" | 3 | 4).
    corner = next((c for c in cells if (c["row"], c["col"]) == (0, 0)), None)
    body_first = [c for c in cells if c["col"] == 0 and c["row"] >= header_rows]
    header_cols = int(bool(header_rows and corner is not None and not corner["text"] and body_first
                           and all(_header_like(c["text"]) for c in body_first)))

    caption = (n.get("name") or "").strip()          # <caption> / aria-label, as the browser names it
    if not caption:
        caption = _cell_text_of_nodes(doc, kids, caption_nodes)
    if not caption:
        caption = _title_above(doc, kids, t)
    return {"node": t, "id": n.get("id") or "", "caption": caption, "box": _rect(n),
            "n_rows": n_rows, "n_cols": n_cols, "header_rows": header_rows, "header_cols": header_cols,
            "cells": cells,
            "placed_by": placed_by, "conflicts": conflicts, "nested_in": None}


def _cell_text_of_nodes(doc, kids, nodes: List[int]) -> str:
    """The text of a few nodes (a caption) the way a cell's text is built."""
    out = [_cell_text(doc, kids, j, []) for j in nodes]
    return " ".join(" ".join(t for t in out if t).split())


def _title_above(doc, kids, t: int) -> str:
    """A nameless table's title: the nearest earlier sibling that is text and sits just above it."""
    box = _rect(doc[t])
    p = doc[t].get("parent", -1)
    sibs = kids[p] if p >= 0 else [i for i, n in enumerate(doc) if n.get("parent", -1) == -1]
    before = [s for s in sibs if s < t]
    for s in reversed(before):
        sn = doc[s]
        if _is_table(sn):
            break
        text = (sn.get("name") or "").strip() or _cell_text(doc, kids, s, [])
        if not text:
            continue
        sb = _rect(sn)
        if box and sb and 0 <= box[1] - sb[3] <= TITLE_GAP:
            return text
        break
    return ""


# ── the page ──────────────────────────────────────────────────────────────────

def extract(docs: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Every table in every document of a read, outer before inner, nested ones linked."""
    tables: List[Dict[str, Any]] = []
    for di, doc in enumerate(docs):
        if not doc:
            continue
        kids = _children(doc)
        found: Dict[int, Dict[str, Any]] = {}
        for i, n in enumerate(doc):
            if _is_table(n):
                found[i] = dict(extract_table(doc, kids, i), doc=di)
        index = {node: len(tables) + k for k, node in enumerate(found)}   # page order = outer first
        for node, tb in found.items():
            for c in tb["cells"]:
                c["nested_tables"] = [index[j] for j in c["nested"] if j in index]
                for j in c["nested"]:
                    if j in found:
                        found[j]["nested_in"] = (index[node], c["row"], c["col"])
        tables.extend(found.values())
    return tables


def grid(tb: Dict[str, Any]) -> List[List[str]]:
    """rows x cols of texts; a spanned slot repeats its cell's text."""
    g = [["" for _ in range(tb["n_cols"])] for _ in range(tb["n_rows"])]
    for c in tb["cells"]:
        for dr in range(c["rowspan"]):
            for dc in range(c["colspan"]):
                if c["row"] + dr < tb["n_rows"] and c["col"] + dc < tb["n_cols"]:
                    g[c["row"] + dr][c["col"] + dc] = c["text"]
    return g


def spans(tb: Dict[str, Any]) -> Dict[Tuple[int, int], Tuple[int, int]]:
    return {(c["row"], c["col"]): (c["rowspan"], c["colspan"]) for c in tb["cells"]
            if c["rowspan"] > 1 or c["colspan"] > 1}


def column_names(tb: Dict[str, Any]) -> List[str]:
    """Each column's header texts top-down, joined: "Tax / IGST". Empty without header rows."""
    g = grid(tb)
    names = []
    for col in range(tb["n_cols"]):
        seen: List[str] = []
        for r in range(tb["header_rows"]):
            t = g[r][col]
            if t and t not in seen:
                seen.append(t)
        names.append(" / ".join(seen))
    return names


def records(tb: Dict[str, Any]) -> List[Dict[str, str]]:
    """Body rows as {column name: text}; a column with no header is "column <n>"."""
    names = [nm or f"column {i + 1}" for i, nm in enumerate(column_names(tb))]
    return [dict(zip(names, row)) for row in grid(tb)[tb["header_rows"]:]]


# ── re-rendering ──────────────────────────────────────────────────────────────

def render_text(tb: Dict[str, Any], max_width: int = 28) -> str:
    """A boxed text grid drawn like the page: a spanned cell is one box across its columns, and
    the line under a row-spanning cell stays open until the cell ends. '=' closes the header."""
    origin = {(c["row"], c["col"]): c for c in tb["cells"]}
    covered = {(c["row"] + dr, c["col"] + dc): c for c in tb["cells"]
               for dr in range(c["rowspan"]) for dc in range(c["colspan"])}

    def disp(c):
        s = c["text"] if len(c["text"]) <= max_width else c["text"][:max_width - 1] + "…"
        n = len(c.get("nested_tables", []))
        return (s + " " if s else "") + f"[{n} nested table{'s' if n > 1 else ''}]" if n else s
    width = [3] * tb["n_cols"]
    for c in sorted(tb["cells"], key=lambda c: c["colspan"]):
        span = range(c["col"], min(c["col"] + c["colspan"], tb["n_cols"]))
        short = len(disp(c)) - (sum(width[k] for k in span) + 3 * (len(span) - 1))
        if short > 0 and len(span):
            width[span[-1]] += short                  # widen the last column the cell covers

    def rule(r, ch):
        """The line under row r: open where a row-spanning cell continues below."""
        out = "+"
        for col in range(tb["n_cols"]):
            cv = covered.get((r, col))
            cont = cv is not None and cv["row"] + cv["rowspan"] - 1 > r
            out += (" " if cont else ch) * (width[col] + 2) + "+"
        return out
    out = [f"Table {tb['id'] or tb['node']}: {tb['caption'] or '(no title)'}  "
           f"[{tb['n_rows']} x {tb['n_cols']}, header rows {tb['header_rows']}, header cols "
           f"{tb['header_cols']}, by {tb['placed_by']}]",
           rule(-1, "-")]
    for r in range(tb["n_rows"]):
        line, col = "|", 0
        while col < tb["n_cols"]:
            c = origin.get((r, col)) or covered.get((r, col))
            cs = c["colspan"] if c and c["col"] == col else 1
            w = sum(width[col:col + cs]) + 3 * (cs - 1)
            text = disp(c) if c and (c["row"], c["col"]) == (r, col) else ""
            line += " " + text.ljust(w)[:w] + " |"
            col += cs
        out.append(line)
        out.append(rule(r, "=" if r + 1 == tb["header_rows"] else "-"))
    return "\n".join(out)


def _table_html(tables: List[Dict[str, Any]], i: int) -> str:
    tb = tables[i]
    origin = {(c["row"], c["col"]): c for c in tb["cells"]}
    out = [f'<table data-sdis-table="{i}">']
    if tb["caption"]:
        out.append(f"<caption>{_html.escape(tb['caption'])}</caption>")
    for r in range(tb["n_rows"]):
        if r == 0 and tb["header_rows"]:
            out.append("<thead>")
        if r == tb["header_rows"]:
            out.append("<tbody>")
        out.append("<tr>")
        for col in range(tb["n_cols"]):
            c = origin.get((r, col))
            if not c:
                continue
            tag = "th" if r < tb["header_rows"] or col < tb.get("header_cols", 0) else "td"
            attrs = (f' colspan="{c["colspan"]}"' if c["colspan"] > 1 else "") + \
                    (f' rowspan="{c["rowspan"]}"' if c["rowspan"] > 1 else "")
            inner = _html.escape(c["text"]) + "".join(_table_html(tables, j) for j in c.get("nested_tables", []))
            out.append(f"<{tag}{attrs}>{inner}</{tag}>")
        out.append("</tr>")
        if r + 1 == tb["header_rows"]:
            out.append("</thead>")
    if tb["n_rows"] > tb["header_rows"]:
        out.append("</tbody>")
    out.append("</table>")
    return "".join(out)


def render_html(tables: List[Dict[str, Any]], title: str = "") -> str:
    """A standalone page: every outer table (nested ones inside their cells) with its shape."""
    css = ("body{font:13px Segoe UI,Arial;margin:16px;background:#fff;color:#111}"
           "table{border-collapse:collapse;margin:4px 0 22px}td,th{border:1px solid #999;padding:3px 8px;"
           "vertical-align:top}th{background:#eef5ef}caption{text-align:left;font-weight:600;padding:2px 0}"
           ".meta{color:#666;font-size:12px}")
    body = []
    for i, tb in enumerate(tables):
        if tb["nested_in"] is not None:
            continue
        body.append(f'<div class="meta">table {i}: {tb["n_rows"]} x {tb["n_cols"]}, header rows '
                    f'{tb["header_rows"]}, placed by {tb["placed_by"]}'
                    + (f', {tb["conflicts"]} overlapping box(es)' if tb["conflicts"] else "") + "</div>")
        body.append(_table_html(tables, i))
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{_html.escape(title or 'Tables')}</title>"
            f"<style>{css}</style></head><body><h3>{_html.escape(title)}</h3>{''.join(body)}</body></html>")


def counts(tb: Dict[str, Any]) -> str:
    """The table's shape only - no values."""
    sp = spans(tb)
    return (f"{tb['n_rows']} rows x {tb['n_cols']} cols, header rows {tb['header_rows']}, "
            f"header cols {tb['header_cols']}, "
            f"{len(tb['cells'])} cells, {len(sp)} spanning, empty {sum(1 for c in tb['cells'] if not c['text'])}, "
            f"placed by {tb['placed_by']}, overlaps {tb['conflicts']}"
            + (", nested" if tb["nested_in"] is not None else ""))


# ── command line ──────────────────────────────────────────────────────────────

def load_docs(path: Path, snapshot: int = -1) -> List[List[Dict[str, Any]]]:
    """docs of a key_probe --once read / fixture, or of one snapshot of a capture (default: last)."""
    rec = json.loads(Path(path).read_text(encoding="utf-8"))
    if "snapshots" in rec:
        return rec["snapshots"][snapshot]["docs"]
    return rec["docs"]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Extract and re-render the tables of a recorded page.")
    ap.add_argument("file", nargs="?", help="a key_probe / capture JSON (default: the newest in output/)")
    ap.add_argument("--snapshot", type=int, default=-1, help="which capture snapshot (default: the last)")
    ap.add_argument("--html", action="store_true", help="also write output/tables_<file>.html")
    ap.add_argument("--counts", action="store_true", help="print only each table's shape, no values")
    args = ap.parse_args(argv)
    if args.file:
        path = Path(args.file)
    else:
        reads = sorted(OUT_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
        reads = [p for p in reads if p.name.startswith(("key_probe_", "capture_"))]
        if not reads:
            print("no reads in output/")
            return 1
        path = reads[-1]
    tables = extract(load_docs(path, args.snapshot))
    print(f"{path.name}: {len(tables)} table(s)")
    for i, tb in enumerate(tables):
        print(f"  table {i}: {counts(tb)}" if args.counts else "\n" + render_text(tb))
    if args.html:
        OUT_DIR.mkdir(exist_ok=True)
        out = OUT_DIR / f"tables_{path.stem}.html"
        out.write_text(render_html(tables, path.stem), encoding="utf-8")
        print(f"HTML: {out}")
    return 0
