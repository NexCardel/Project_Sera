"""
tools/build_ltt_tracker.py
--------------------------
Builds the LTT tracker workbook, in the same style as docs/sera-sync-v3-agents.xlsx:

  ltt_feed.csv      the data. Written by the app (Tracker Dump window > Tools > Update LTT sheet).
  ltt_tracker.xlsx  a read-only viewer. Excel's Power Query pulls the CSV in, refreshing when the
                    file opens and every minute.

Columns: PAN, Name, Return form, Current period, Submit status. Both files sit in the app's data
folder (next to the vault) and are git-ignored: they hold client PANs in plain text.

  venv\\Scripts\\python tools\\build_ltt_tracker.py                  (build, attach Power Query)
  venv\\Scripts\\python tools\\build_ltt_tracker.py --no-excel       (skip Power Query; tests use this)
  venv\\Scripts\\python tools\\build_ltt_tracker.py --dir D:\\vault   (another data folder)

Close the workbook in Excel before rebuilding it.
"""
import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from core.ltt.feed import FIELDS, LEVELS, NOT_APPLICABLE, write_csv  # noqa: E402
from core.ltt.rules import FEED_FILE, WORKBOOK_FILE  # noqa: E402

MAX_ROWS = 3000                  # formula rows pre-filled on the display sheet
WIDTHS = [14, 34, 14, 26, 26, 20]
FILL_HEAD = "FF2F4F6F"
STATUS_FILL = {LEVELS[0]: "FFF8CBAD", LEVELS[1]: "FFFFF2CC", LEVELS[2]: "FFFFE699",
               LEVELS[3]: "FFE2EFDA", NOT_APPLICABLE: "FFEDEDED"}

HOW_TO = [
    ("How to use this workbook", True),
    ("", False),
    ("This workbook only displays status. The Sera app writes the data to a text file next to the vault", True),
    ("(ltt_feed.csv) and this workbook reads it. You can keep this file open while captures come in.", False),
    ("", False),
    ("To see the latest: Data → Refresh All (Ctrl+Alt+F5). It also refreshes when opened and every minute.", False),
    ("Don't type into the sheets: the next refresh overwrites it. To change what is tracked, use", False),
    ("Sera → Tracker Dump → Tools → LTT form rules. To change statuses, file/capture in the portals as usual.", False),
    ("When Excel asks to save on close, either answer is fine — the data lives in the CSV, not here.", False),
    ("", False),
    ("The columns", True),
    ("• PAN, Name — the client; Name is the trade name when there is one.", False),
    ("• Return form — a form set up in LTT form rules (GSTR-3B, ITR, ...).", False),
    ("• Current period — the month being filed right now, with the financial year, e.g. September (FY 2026-27).", False),
    ("• Submit status — Not Submitted → Draft → Submitted (Not Verified) → Submitted & Verified.", False),
    ("   'Not Submitted' also means Sera has no capture for that month yet.", False),
    ("• Capture date & time — when Sera captured the filing that decided the status (blank if none).", False),
    ("", False),
    ("Rebuild (only if this file is damaged; close it first): venv\\Scripts\\python tools\\build_ltt_tracker.py", False),
    ("If refresh shows an error about a missing file, the project folder moved: run the rebuild command.", False),
]


def build_viewer(out: Path):
    from openpyxl import Workbook
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    how = wb.active
    how.title = "How to use"
    how.column_dimensions["A"].width = 110
    for i, (text, bold) in enumerate(HOW_TO, 1):
        c = how.cell(i, 1, text)
        c.font = Font(name="Arial", size=14 if i == 1 else 10, bold=bold)
        c.alignment = Alignment(wrap_text=True, vertical="top")

    ws = wb.create_sheet("LTT Tracker")
    data = wb.create_sheet("FeedData")
    data.sheet_state = "hidden"

    thin = Side(style="thin", color="FFBFBFBF")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    ws["A1"] = "LTT tracker — current period per client and return form"
    ws["A1"].font = Font(name="Arial", size=14, bold=True)
    ws["A2"] = ("Grey columns come from ltt_feed.csv, which the Sera app updates. Data → Refresh All "
                "(Ctrl+Alt+F5) to see the latest; it also refreshes on open and every minute. "
                "Don't type into this sheet: a refresh overwrites it.")
    ws["A2"].font = Font(name="Arial", size=10, color="FF555555")

    for col, (label, width) in enumerate(zip(FIELDS, WIDTHS), 1):
        h = ws.cell(4, col, label)
        h.font = Font(name="Arial", size=10, bold=True, color="FFFFFFFF")
        h.fill = PatternFill("solid", fgColor=FILL_HEAD)
        h.alignment = Alignment(wrap_text=True, vertical="center")
        h.border = border
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.row_dimensions[4].height = 26.4

    grey = PatternFill("solid", fgColor="FFEDEDED")
    last = 4 + MAX_ROWS
    for r in range(5, last + 1):
        for col in range(1, len(FIELDS) + 1):
            letter = get_column_letter(col)
            c = ws.cell(r, col, f'=IFERROR(IF(INDEX(FeedData!${letter}:${letter},ROW()-3)="","",'
                                f'INDEX(FeedData!${letter}:${letter},ROW()-3)),"")')
            c.font = Font(name="Arial", size=10, bold=(col == 1))
            c.alignment = Alignment(wrap_text=True, vertical="top")
            c.border = border
            c.fill = grey
    status_rng = f"E5:E{last}"
    for status, color in STATUS_FILL.items():
        ws.conditional_formatting.add(
            status_rng, FormulaRule(formula=[f'$E5="{status}"'],
                                    fill=PatternFill("solid", bgColor=color, fgColor=color)))
    ws.freeze_panes = "A5"
    ws.auto_filter.ref = f"A4:F{last}"
    ws.sheet_view.zoomScale = 100
    wb.active = 1
    wb.save(out)


def attach_power_query(xlsx: Path, csv_path: Path):
    """Uses Excel itself (COM) to add the CSV query, refresh it and save."""
    import time
    import comtypes.client
    from _ctypes import COMError

    def r(fn, tries=60):
        for i in range(tries):                 # Excel rejects calls while still loading
            try:
                return fn()
            except COMError as e:
                if e.hresult not in (-2147418111, -2147417846) or i == tries - 1:
                    raise
                time.sleep(0.5)

    p = str(csv_path.resolve()).replace('"', '""')
    m = ('let Source = Csv.Document(File.Contents("' + p + '"),[Delimiter=",", Encoding=65001, '
         'QuoteStyle=QuoteStyle.Csv]), Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars=true]), '
         'AsText = Table.TransformColumnTypes(Promoted, List.Transform(Table.ColumnNames(Promoted), '
         'each {_, type text})) in AsText')
    query = "LttFeedCsv"
    xl = comtypes.client.CreateObject("Excel.Application")
    try:
        r(lambda: setattr(xl, "Visible", False))
        r(lambda: setattr(xl, "DisplayAlerts", False))
        wb = r(lambda: xl.Workbooks.Open(str(xlsx.resolve())))
        r(lambda: wb.Queries.Add(query, m))
        ws = r(lambda: wb.Worksheets("FeedData"))
        r(lambda: ws.Cells.Clear())
        anchor = r(lambda: ws.Range("$A$1"))
        lo = r(lambda: ws.ListObjects.Add(
            0, 'OLEDB;Provider=Microsoft.Mashup.OleDb.1;Data Source=$Workbook$;'
               f'Location={query};Extended Properties=""', None, 1, anchor))
        r(lambda: setattr(lo, "Name", query))
        qt = r(lambda: lo.QueryTable)
        r(lambda: setattr(qt, "CommandType", 2))
        r(lambda: setattr(qt, "CommandText", f"SELECT * FROM [{query}]"))
        r(lambda: setattr(qt, "BackgroundQuery", False))
        r(lambda: qt.Refresh(False))
        conn = r(lambda: qt.WorkbookConnection.OLEDBConnection)
        r(lambda: setattr(conn, "BackgroundQuery", True))
        r(lambda: setattr(conn, "RefreshOnFileOpen", True))
        r(lambda: setattr(conn, "RefreshPeriod", 1))
        r(lambda: xl.CalculateFull())
        r(lambda: wb.Worksheets("LTT Tracker").Activate())
        r(lambda: wb.Save())
        r(lambda: wb.Close(False))
    finally:
        r(lambda: xl.Quit())


def is_current(xlsx: Path) -> bool:
    """True when the workbook exists and has exactly today's columns (older builds lack newer ones)."""
    try:
        from openpyxl import load_workbook
        wb = load_workbook(xlsx, read_only=True)
        try:
            head = [c.value for c in next(wb["LTT Tracker"].iter_rows(min_row=4, max_row=4))]
            return head == FIELDS
        finally:
            wb.close()
    except Exception:
        return False


def build(folder: Path, power_query: bool = True) -> Path:
    csv_path = folder / FEED_FILE
    if not csv_path.exists():
        write_csv(csv_path, [])
    out = folder / WORKBOOK_FILE
    if out.with_name("~$" + out.name).exists():
        raise RuntimeError(f"{out.name} is open in Excel. Close it and run again.")
    build_viewer(out)
    if power_query:
        attach_power_query(out, csv_path)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=REPO, help="data folder holding ltt_feed.csv (default: the app folder)")
    ap.add_argument("--no-excel", action="store_true", help="skip the Power Query step")
    a = ap.parse_args(argv)
    try:
        out = build(a.dir, power_query=not a.no_excel)
    except RuntimeError as e:
        raise SystemExit(str(e))
    print(f"Built {out}" + ("" if a.no_excel else " — Power Query attached (refresh on open + every minute)."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
