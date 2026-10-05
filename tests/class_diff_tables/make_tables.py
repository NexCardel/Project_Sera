"""Fictional table page for tools/pre_dev/class_diff/tables.py. All data is made up.

TABLES below is the ground truth: each table is a list of rows, each row a list of cells
(text, colspan, rowspan, header). page() renders it as HTML; expected() turns it into the grid
the extractor must give back (tests/test_sdis_tables.py).

Regenerating the fixtures (only when this page or the probe's node format changes):
    ../APP/venv/Scripts/python.exe tests/class_diff_tables/capture_fixtures.py
which writes tables.html, serves it on 127.0.0.1, reads it in Edge, Chrome and Firefox with
throwaway profiles and saves tables_<browser>.json here.
"""
import html
import json
from pathlib import Path

HERE = Path(__file__).parent
TITLE = "SDIS tables test"


def c(text="", colspan=1, rowspan=1, header=False, html_text=None):
    """One cell. html_text = what goes in the HTML when it is not just the text (links, inputs)."""
    return {"text": text, "colspan": colspan, "rowspan": rowspan, "header": header, "html": html_text}


def h(text="", colspan=1, rowspan=1):
    return c(text, colspan, rowspan, header=True)


# Each entry: (id, kind, caption, header rows (in <thead>), body rows, footer rows)
TABLES = [
    ("t_returns", "table", "Filed returns",
     [[h("Period"), h("Form"), h("Status"), h("ARN")]],
     [[c("Jun 2026"), c("GSTR-1"), c("Filed"), c("AA2706261111111")],
      [c("May 2026"), c("GSTR-3B"), c("Not filed"), c("")],
      [c("Apr 2026"), c("GSTR-1"), c("Filed"), c("AA2704261111113")]],
     [[c("Total filed", colspan=3), c("2")]]),

    ("t_ledger", "table", "Ledger balance",
     [[h("Ledger", rowspan=2), h("Tax", colspan=3)],
      [h("IGST"), h("CGST"), h("SGST")]],
     [[c("Cash ledger"), c("1,010.00"), c("2,020.00"), c("3,030.00")],
      [c("Credit ledger"), c("0.00"), c("45.50"), c("45.50")]],
     []),

    ("t_quarters", "table", "Quarterly view",
     [[h("Quarter"), h("Month"), h("Turnover")]],
     [[c("Q1", rowspan=3), c("April"), c("10,000")],
      [c("May"), c("12,500")],
      [c("June"), c("9,800")],
      [c("Q2", rowspan=2), c("July"), c("11,000")],
      [c("August"), c("")]],
     []),

    ("t_actions", "table", "",          # no caption: the title comes from the heading above it
     [[h("Document"), h("Amount"), h("Remark"), h("Action")]],
     [[c("Invoice 17", html_text='<a href="#inv17">Invoice 17</a>'),
       c("5,000", html_text='<input type="text" value="5,000" aria-label="Amount">'),
       c("Paid", html_text='<select aria-label="Remark"><option>Pending</option><option selected>Paid</option></select>'),
       c("Download", html_text='<button type="button">Download</button>')],
      [c("Credit note 3 (revised)", html_text='<a href="#cn3">Credit note 3</a> (revised)'),
       c("750", html_text='<input type="text" value="750" aria-label="Amount">'),
       c("Pending", html_text='<select aria-label="Remark"><option selected>Pending</option><option>Paid</option></select>'),
       c("Download", html_text='<button type="button">Download</button>')]],
     []),

    ("t_outer", "table", "Branches",
     [[h("Branch"), h("Contacts")]],
     [[c("Pune"), c("[t_inner]")],          # the cell holds the nested table t_inner
      [c("Goa"), c("None")]],
     []),

    ("t_aria", "aria", "Payments (grid)",
     [[h("Date"), h("Mode"), h("Amount")]],
     [[c("02/07/2026"), c("NEFT"), c("4,200")],
      [c("15/07/2026"), c("UPI"), c("")]],
     []),

    ("t_matrix", "table", "Monthly summary",   # a matrix: empty corner, period column labels, row labels
     [[h(""), h("Apr 2026"), h("May 2026"), h("Q1 total")]],
     [[c("Returns filed"), c("3"), c("4"), c("7")],
      [c("Pending"), c("1"), c("0"), c("1")]],
     []),

    ("t_multiline", "table", "Notes",
     [],                                      # no header row at all
     [[c("Address"), c("Flat 4B, Tower 2 Sector 9, Pune", html_text="Flat 4B, Tower 2<br>Sector 9, Pune")],
      [c("Email"), c("")],
      [c("Phone"), c("90000 11111")]],
     []),
]

INNER = ("t_inner", "table", "",
         [[h("Name"), h("Phone")]],
         [[c("R. Kumar"), c("90000 22222")],
          [c("S. Rao"), c("90000 33333")]],
         [])


def _cell_html(cell, tag, inner_tables):
    attrs = ""
    if cell["colspan"] > 1:
        attrs += f' colspan="{cell["colspan"]}"'
    if cell["rowspan"] > 1:
        attrs += f' rowspan="{cell["rowspan"]}"'
    if cell["text"].startswith("[") and cell["text"].endswith("]"):
        body = _table_html(inner_tables[cell["text"][1:-1]], inner_tables)
    else:
        body = cell["html"] if cell["html"] is not None else html.escape(cell["text"])
    return f"<{tag}{attrs}>{body}</{tag}>"


def _table_html(t, inner_tables):
    tid, kind, caption, head, body, foot = t
    if kind == "aria":
        def arow(r, role):
            return '<div role="row" class="r">' + "".join(
                f'<div role="{role}" class="c">{html.escape(x["text"])}</div>' for x in r) + "</div>"
        return (f'<div role="table" id="{tid}" aria-label="{html.escape(caption)}" class="grid">'
                + "".join(arow(r, "columnheader") for r in head)
                + "".join(arow(r, "cell") for r in body) + "</div>")
    out = [f'<table id="{tid}" border="1">']
    if caption:
        out.append(f"<caption>{html.escape(caption)}</caption>")
    for section, rows in (("thead", head), ("tbody", body), ("tfoot", foot)):
        if rows:
            out.append(f"<{section}>" + "".join(
                "<tr>" + "".join(_cell_html(x, "th" if x["header"] else "td", inner_tables) for x in r) + "</tr>"
                for r in rows) + f"</{section}>")
    out.append("</table>")
    return "".join(out)


def page() -> str:
    inner = {INNER[0]: INNER}
    parts = []
    for t in TABLES:
        if t[0] == "t_actions":
            parts.append("<h3>Documents and actions</h3>")
        parts.append(_table_html(t, inner))
    css = ("body{font:14px Segoe UI,Arial;margin:16px} table{border-collapse:collapse;margin:0 0 18px}"
           " td,th{padding:4px 10px} .grid{display:table;margin:0 0 18px;border:1px solid #888}"
           " .r{display:table-row} .c{display:table-cell;padding:4px 10px;border:1px solid #888}")
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{TITLE}</title><style>{css}</style>"
            f"</head><body><h2>{TITLE}</h2>" + "".join(parts) + "</body></html>")


def expected(t=None):
    """The grid each table must come back as: {id: {"caption", "header_rows", "grid", "spans"}}.
    grid = rows x cols of texts, a spanned slot repeating its cell's text (a cell holding a nested
    table has its own text only, here ""); spans = {(r, c): (rs, cs)}
    for every cell that spans; header_rows = rows in <thead> (or the ARIA header rows)."""
    out = {}
    for tt in (TABLES + [INNER]) if t is None else [t]:
        tid, kind, caption, head, body, foot = tt
        rows = head + body + foot
        grid, spans, taken = [], {}, {}
        for r, row in enumerate(rows):
            col = 0
            for cell in row:
                while (r, col) in taken:
                    col += 1
                text = "" if cell["text"].startswith("[") else cell["text"]   # a nested table's cell
                for dr in range(cell["rowspan"]):
                    for dc in range(cell["colspan"]):
                        taken[(r + dr, col + dc)] = text
                if cell["rowspan"] > 1 or cell["colspan"] > 1:
                    spans[(r, col)] = (cell["rowspan"], cell["colspan"])
                col += cell["colspan"]
        n_rows = len(rows)
        n_cols = max(cc for _, cc in taken) + 1
        grid = [[taken.get((r, cc), "") for cc in range(n_cols)] for r in range(n_rows)]
        out[tid] = {"caption": caption or ("Documents and actions" if tid == "t_actions" else ""),
                    "header_rows": len(head), "grid": grid, "spans": spans,
                    # row labels down the first column under an empty corner = a header column
                    "header_cols": 1 if head and head[0][0]["text"] == "" else 0}
    return out


if __name__ == "__main__":
    (HERE / "tables.html").write_text(page(), encoding="utf-8")
    print(json.dumps({k: v["grid"] for k, v in expected().items()}, indent=1)[:400])
