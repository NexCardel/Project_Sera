"""
SDIS pre-dev: table extraction and re-rendering (tools/pre_dev/class_diff/tables.py).

Round trip on a FICTIONAL page (tests/class_diff_tables/make_tables.py): the page's tables are
defined in Python, rendered to HTML, read in Edge, Chrome and Firefox exactly as key_probe reads
(tables_<browser>.json, made by capture_fixtures.py), extracted, and must come back as the same
grids - texts, spans, header rows, captions, the nested table. Then the extracted tables are
rendered back to HTML, parsed again, and must still be the same grids (the re-rendering is
faithful). Hand-made node lists cover what the browsers did not show: hidden rows, a production
read's grid positions, row groups, overlapping boxes.
"""

import json
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).resolve().parent / "class_diff_tables"
sys.path.insert(0, str(ROOT / "tools" / "pre_dev" / "class_diff"))
sys.path.insert(0, str(FIX))

import make_tables  # noqa: E402
import tables  # noqa: E402

EXPECTED = make_tables.expected()
BROWSERS = [b for b in ("edge", "chrome", "firefox") if (FIX / f"tables_{b}.json").exists()]


def _read(browser):
    return tables.extract(tables.load_docs(FIX / f"tables_{browser}.json"))


def _by_id(tbs):
    return {tb["id"]: tb for tb in tbs}


def test_fixtures_present():
    assert "edge" in BROWSERS                              # at least one real browser read


# ── extraction, per browser ───────────────────────────────────────────────────

@pytest.mark.parametrize("browser", BROWSERS)
def test_every_table_found_once(browser):
    assert sorted(tb["id"] for tb in _read(browser)) == sorted(EXPECTED)


@pytest.mark.parametrize("browser", BROWSERS)
@pytest.mark.parametrize("tid", sorted(EXPECTED))
def test_grid_spans_header_caption(browser, tid):
    tb, exp = _by_id(_read(browser))[tid], EXPECTED[tid]
    assert tables.grid(tb) == exp["grid"]
    assert tables.spans(tb) == exp["spans"]
    assert tb["header_rows"] == exp["header_rows"]
    assert tb["header_cols"] == exp["header_cols"]
    assert tb["caption"] == exp["caption"]
    assert tb["placed_by"] == "boxes" and tb["conflicts"] == 0


@pytest.mark.parametrize("browser", BROWSERS)
def test_nested_table_is_linked_to_its_cell_and_not_in_its_text(browser):
    tbs = _read(browser)
    ids = [tb["id"] for tb in tbs]
    outer, inner = ids.index("t_outer"), ids.index("t_inner")
    assert tbs[inner]["nested_in"] == (outer, 1, 1)
    cell = next(c for c in tbs[outer]["cells"] if (c["row"], c["col"]) == (1, 1))
    assert cell["nested_tables"] == [inner] and cell["text"] == ""   # Firefox names it with the inner text


@pytest.mark.parametrize("browser", BROWSERS)
def test_records_use_two_level_column_names(browser):
    tb = _by_id(_read(browser))["t_ledger"]
    assert tables.column_names(tb) == ["Ledger", "Tax / IGST", "Tax / CGST", "Tax / SGST"]
    assert tables.records(tb)[0] == {"Ledger": "Cash ledger", "Tax / IGST": "1,010.00",
                                     "Tax / CGST": "2,020.00", "Tax / SGST": "3,030.00"}
    notes = _by_id(_read(browser))["t_multiline"]          # no header: numbered columns
    assert tables.records(notes)[0] == {"column 1": "Address", "column 2": "Flat 4B, Tower 2 Sector 9, Pune"}


# ── re-rendering ──────────────────────────────────────────────────────────────

class _TableParser(HTMLParser):
    """Rebuilds grids from HTML with the browser's own placement rule (rowspan/colspan)."""

    def __init__(self):
        super().__init__()
        self.stack, self.done = [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self.stack.append({"rows": [], "caption": "", "in_caption": False, "thead": 0, "in_thead": False})
        elif not self.stack:
            return
        t = self.stack[-1]
        if tag == "caption":
            t["in_caption"] = True
        elif tag == "thead":
            t["in_thead"] = True
        elif tag == "tr":
            t["rows"].append([])
            t["thead"] += t["in_thead"]
        elif tag in ("td", "th"):
            t["rows"][-1].append({"text": "", "cs": int(a.get("colspan", 1)), "rs": int(a.get("rowspan", 1))})

    def handle_endtag(self, tag):
        if not self.stack:
            return
        t = self.stack[-1]
        if tag == "caption":
            t["in_caption"] = False
        elif tag == "thead":
            t["in_thead"] = False
        elif tag == "table":
            self.done.append(self.stack.pop())

    def handle_data(self, data):
        if self.stack:
            t = self.stack[-1]
            if t["in_caption"]:
                t["caption"] += data
            elif t["rows"] and t["rows"][-1]:
                t["rows"][-1][-1]["text"] += data


def _grid_of(parsed):
    taken, spans = {}, {}
    for r, row in enumerate(parsed["rows"]):
        col = 0
        for cell in row:
            while (r, col) in taken:
                col += 1
            for dr in range(cell["rs"]):
                for dc in range(cell["cs"]):
                    taken[(r + dr, col + dc)] = cell["text"]
            if cell["rs"] > 1 or cell["cs"] > 1:
                spans[(r, col)] = (cell["rs"], cell["cs"])
            col += cell["cs"]
    n_rows, n_cols = max(r for r, _ in taken) + 1, max(c for _, c in taken) + 1
    return [[taken.get((r, c), "") for c in range(n_cols)] for r in range(n_rows)], spans


@pytest.mark.parametrize("browser", BROWSERS)
def test_html_rendering_round_trips(browser):
    tbs = _read(browser)
    p = _TableParser()
    p.feed(tables.render_html(tbs, "fictional"))
    by_caption_shape = {}
    for parsed in p.done:                                   # inner tables close first
        g, sp = _grid_of(parsed)
        by_caption_shape.setdefault((parsed["caption"], len(g), len(g[0])), []).append((g, sp, parsed["thead"]))
    for tid, exp in EXPECTED.items():
        g = exp["grid"]
        hits = by_caption_shape[(exp["caption"], len(g), len(g[0]))]
        assert (g, exp["spans"], exp["header_rows"]) in hits, tid


@pytest.mark.parametrize("browser", BROWSERS)
def test_text_rendering_shows_every_value(browser):
    for tb in _read(browser):
        out = tables.render_text(tb, max_width=200)
        for c in tb["cells"]:
            assert c["text"] in out
        assert len({len(line) for line in out.splitlines()[1:]}) == 1      # a straight box


def test_counts_hold_no_values():
    tb = _by_id(_read("edge"))["t_returns"]
    line = tables.counts(tb)
    assert "5 rows x 4 cols" in line and "AA2706261111111" not in line and "Filed" not in line


# ── hand-made nodes: what the browsers did not show ──────────────────────────

def _n(parent, depth, ctype, name="", rect=None, **kw):
    return dict({"parent": parent, "depth": depth, "ctype": ctype, "name": name, "rect": rect}, **kw)


def _table(rows, wrap_rows=False, rects=True):
    """rows: [[(text, (l, t, w, h))]] -> a Chromium-shaped node list (Table / DataItem / DataItem / Text)."""
    doc = [_n(-1, 0, tables.CT_TABLE, "", [0, 0, 300, 30 * len(rows)])]
    parent, depth = 0, 1
    if wrap_rows:
        doc.append(_n(0, 1, 50026, "", [0, 0, 300, 30 * len(rows)], role="rowgroup"))
        parent, depth = 1, 2
    for r, row in enumerate(rows):
        doc.append(_n(parent, depth, tables.CT_DATAITEM, "", [0, 30 * r, 300, 30] if rects else None))
        ri = len(doc) - 1
        for text, box in row:
            doc.append(_n(ri, depth + 1, tables.CT_DATAITEM, text, list(box) if (rects and box) else None))
            ci = len(doc) - 1
            if text:
                doc.append(_n(ci, depth + 2, tables.CT_TEXT, text, list(box) if (rects and box) else None))
    return doc


def test_hidden_cell_takes_the_next_free_slot():
    doc = _table([[("Name", (0, 0, 100, 30)), ("Phone", (100, 0, 100, 30))],
                  [("A. One", (0, 30, 100, 30)), ("hidden", None)]])
    tb = tables.extract([doc])[0]
    assert tables.grid(tb) == [["Name", "Phone"], ["A. One", "hidden"]]


def test_production_grid_positions_when_no_boxes():
    doc = _table([[("x1", None), ("x2", None)], [("y1", None), ("y2", None)]], rects=False)
    cells = [n for n in doc if n["ctype"] == tables.CT_DATAITEM and n["name"]]
    for n, g in zip(cells, [(0, 1), (0, 0), (1, 0), (1, 1)]):      # the page says x1 is column 1
        n["grid"] = g
    tb = tables.extract([doc])[0]
    assert tb["placed_by"] == "order" and tables.grid(tb) == [["x2", "x1"], ["y1", "y2"]]


def test_rows_inside_a_rowgroup_are_found():
    doc = _table([[("Name", (0, 0, 100, 30)), ("Phone", (100, 0, 100, 30))],
                  [("A. One", (0, 30, 100, 30)), ("90000 1", (100, 30, 100, 30))]], wrap_rows=True)
    tb = tables.extract([doc])[0]
    assert tables.grid(tb) == [["Name", "Phone"], ["A. One", "90000 1"]] and tb["header_rows"] == 1


def test_overlapping_boxes_are_counted_not_lost():
    doc = _table([[("a", (0, 0, 100, 30)), ("b", (0, 0, 100, 30))]])
    tb = tables.extract([doc])[0]
    assert tb["conflicts"] == 1 and sorted(tables.grid(tb)[0]) == ["a", "b"]


def test_all_label_table_without_digits_keeps_one_header_row():
    doc = _table([[("Form", (0, 0, 100, 30)), ("Status", (100, 0, 100, 30))],
                  [("GSTR-one", (0, 30, 100, 30)), ("Filed", (100, 30, 100, 30))]])
    assert tables.extract([doc])[0]["header_rows"] == 1
