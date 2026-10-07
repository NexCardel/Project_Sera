"""core/ltt/monthly.py - the monthly LTT workbook: the latest period filed, per client, per form.

Reads the tracker's filing history and keeps ONE row per (client, return form): the latest period
that has been captured (latest by the period itself, so a late capture of an old March return never
replaces September). Every form gets its own sheet; nothing is pre-filled, so a sheet is exactly as
long as its data. Statuses are the 4-level ladder: Not Submitted, Draft, Submitted (Not Verified),
Submitted & Verified.

One workbook per calendar month, LTT/LTT_2026-10.xlsx next to the vault, with the data in two CSVs
beside it. The app rewrites the CSVs after captures while that month is running (once the month is
over nobody writes them again, so the month stays as it was), and the workbook's Power Queries pull
them in, refreshing on open and every minute: live while open in Excel. A new sheet is only needed
when a new return form appears; the workbook is then rebuilt (needs Excel installed). It holds PANs in plain text, hence the opt-in: nothing is written
automatically until Tools > Open LTT sheet has created the LTT folder.

Pure functions over plain dicts (latest_rows, write_csvs); export_month is the only DB glue.
openpyxl is imported lazily so the app's start-up never pays for it.
"""
from __future__ import annotations

import calendar
import codecs
import os
import re
import threading
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from .feed import (LEVELS, NOT_APPLICABLE, _rank, clients_from, entry_form, ladder, local_time,
                   parse_period)
from .rules import FY_START_MONTH, canon

FOLDER = "LTT"
COLUMNS = ("PAN", "GSTIN", "Name", "Latest period", "Status", "ARN", "Captured on")
WIDTHS = {"PAN": 14, "GSTIN": 19, "Name": 38, "Latest period": 26, "Status": 26, "ARN": 24, "Captured on": 18}
OVERVIEW_WIDTHS = (24, 12, 28, 20, 12, 28, 24, 18)

HEAD_FILL = "FF1F6F4A"            # evergreen, the Sera colour
BAND_FILL = "FFF4F8F5"
STATUS_FILL = {LEVELS[0]: "FFF4A99B", LEVELS[1]: "FFFFDB70", LEVELS[2]: "FFC9E06F",
               LEVELS[3]: "FF86D4A0", NOT_APPLICABLE: "FFDADADA"}
STATUS_TEXT = {LEVELS[0]: "FF7A1F12", LEVELS[1]: "FF6B4A00", LEVELS[2]: "FF4A5A0A",
               LEVELS[3]: "FF0F4F2A", NOT_APPLICABLE: "FF555555"}


def month_start(d: date) -> date:
    return d.replace(day=1)


def month_end(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def workbook_path(app_dir, month: date) -> Path:
    return Path(app_dir) / FOLDER / f"LTT_{month:%Y-%m}.xlsx"


def period_end(label: str, itr: bool = False) -> date | None:
    """The first day of the last month a tracker period label covers, for ordering periods:
    "June (FY 2026-27)" -> Jun 2026; "Jul-Sep (FY 2026-27)" -> Sep 2026; "AY 2026-27" -> Mar 2026.
    None when the label carries no financial year to place it."""
    months, fy = parse_period(label, itr)
    if fy is None:
        return None
    last = max(months, key=lambda m: (m - FY_START_MONTH) % 12) if months else (FY_START_MONTH - 2) % 12 + 1
    return date(fy + (1 if last < FY_START_MONTH else 0), last, 1)


GST_FORMS = ("GSTR", "CMP", "ITC", "IFF")      # filed per GSTIN, not per PAN
ANNUAL_FORMS = ("ITR", "GSTR9", "GSTR4")        # a label naming only the year is a whole period for these


def is_gst_form(form: str) -> bool:
    return canon(form).startswith(GST_FORMS)


def sheet_columns(form: str) -> tuple:
    """The columns a form's sheet shows: GST returns are filed per registration, so they add the GSTIN."""
    return COLUMNS if is_gst_form(form) else tuple(c for c in COLUMNS if c != "GSTIN")


def period_key(label: str, form: str) -> date | None:
    """Where a period sits in time, for picking the latest. A label naming no month ("Status-Due
    (FY 2026-27)", a misread) only counts as the whole year for annual forms; for monthly and
    quarterly forms it is unplaced, so it never outranks a real month."""
    key = canon(form)
    itr = key.startswith("ITR")
    if not key.startswith(ANNUAL_FORMS) and not parse_period(label, itr)[0]:
        return None
    return period_end(label, itr)


def _gstin_of(h: dict, fallback: str) -> str:
    g = str(h.get("gstin") or "").strip().upper()
    return g or fallback


def _form_names(containers_clients: dict) -> dict:
    """canon(form) -> display spelling. Prefers the hyphenated, upper-case spelling (GSTR-3B)."""
    names: dict = {}
    for c in containers_clients.values():
        for h in c["history"]:
            raw = entry_form(h).strip()
            key = canon(raw)
            if not key:
                continue
            cur = names.get(key)
            if cur is None or ("-" in raw and "-" not in cur):
                names[key] = raw.upper() if raw.upper() == raw or raw.islower() else raw
    return names


def latest_rows(containers: list, month: date | None = None) -> dict:
    """{form: [row, ...]} - one row per client for each form, the latest capture first.
    `month` limits the view to captures made up to the end of that month (the month's workbook
    is the state of the tracker as it stood then); None means everything."""
    cutoff = month_end(month).isoformat() if month else None
    clients = clients_from(containers)
    names = _form_names(clients)
    out: dict = defaultdict(list)
    for pan, c in clients.items():
        # GST returns are filed per registration: a PAN with two GSTINs gets a row for each. A
        # filing captured before GSTINs were recorded belongs to the client's only GSTIN, if one.
        known: dict = defaultdict(set)    # canon form -> GSTINs seen for it
        for h in c["history"]:
            g = _gstin_of(h, "")
            if g:
                known[canon(entry_form(h))].add(g)
        groups: dict = {}                 # (canon form, gstin, period key) -> entries of that period
        for h in c["history"]:
            label = str(h.get("period_label") or "").strip()
            form = canon(entry_form(h))
            if not form or not label:
                continue
            at = str(h.get("created_at") or "")
            if cutoff and local_time(at)[:10] > cutoff and at:
                continue
            gstin = ""
            if is_gst_form(form):
                only = known[form]
                gstin = _gstin_of(h, next(iter(only)) if len(only) == 1 else "")
            end = period_key(label, form)
            groups.setdefault((form, gstin, end or label.lower()), []).append((h, end, at, label))
        best_per_form: dict = {}          # (form, gstin) -> (sort key, group key)
        for (form, gstin, gk), entries in groups.items():
            end = entries[0][1]
            newest = max(e[2] for e in entries)
            key = (end or date.min, newest)
            if (form, gstin) not in best_per_form or key > best_per_form[(form, gstin)][0]:
                best_per_form[(form, gstin)] = (key, gk)
        for (form, gstin), (_, gk) in best_per_form.items():
            pick = None
            for h, _, at, label in groups[(form, gstin, gk)]:
                lvl = ladder(h.get("status"), h.get("arn"))
                rank = _rank(lvl) if lvl != NOT_APPLICABLE else -1
                if pick is None or (rank, at) > (pick[0], pick[1]):
                    pick = (rank, at, lvl, h, label)
            _, at, lvl, h, label = pick
            arn = str(h.get("arn") or "").strip()
            out[names.get(form, form)].append({
                "PAN": pan, "GSTIN": gstin, "Name": c["name"], "Latest period": label, "Status": lvl,
                "ARN": "" if arn.upper() == "N/A" else arn, "Captured on": local_time(at)})
    for rows in out.values():
        rows.sort(key=lambda r: (r["Name"].lower(), r["PAN"], r["GSTIN"]))
        rows.sort(key=lambda r: r["Captured on"], reverse=True)       # newest capture on top (stable: ties stay A-Z)
    return dict(sorted(out.items(), key=lambda kv: (kv[0].startswith("ITR"), kv[0])))


def _sheet_name(form: str, used: set) -> str:
    base = re.sub(r"[\[\]:*?/\\]", "-", form).strip()[:31] or "Form"
    name, i = base, 2
    while name.lower() in used or name.lower() == "overview":
        suffix = f" ({i})"
        name, i = base[:31 - len(suffix)] + suffix, i + 1
    used.add(name.lower())
    return name


def _latest_label(rows: list, form: str = "") -> str:
    """The newest period on a sheet, for the Overview."""
    best = max(rows, key=lambda r: (period_key(r["Latest period"], form) or date.min, r["Captured on"]))
    return best["Latest period"]


# ---------------------------------------------------------------------------------------------
# Files. The app writes two CSVs per month (the data, and one summary row per form); the workbook
# is only a viewer: a Power Query per sheet pulls them in, refreshing when it opens and every
# minute, so it stays live while open in Excel.
# ---------------------------------------------------------------------------------------------

DATA_FIELDS = ("Form", *COLUMNS)
OVERVIEW_FIELDS = ("Form", "Clients", "Latest period", *LEVELS, "Updated")
FORMAT = "LTT live v5"             # stamped in the workbook; any other stamp means an older layout: rebuild
MAX_ROWS = 20000                   # how far down the sheet's colouring reaches


def csv_paths(xlsx) -> tuple[Path, Path]:
    x = Path(xlsx)
    return x.with_suffix(".csv"), x.with_name(x.stem + "_overview.csv")


def _csv_bytes(fields, rows) -> bytes:
    """UTF-8 with BOM so Excel and Power Query read it the same way."""
    import csv
    import io
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=fields)          # csv's default line ending is what Excel expects
    w.writeheader()
    w.writerows(rows)
    return codecs.BOM_UTF8 + buf.getvalue().encode("utf-8")


def _replace_file(path: Path, payload: bytes, tries: int = 12, pause: float = 0.5) -> bool:
    """Writes `payload` to `path` through a temp file. Excel's Power Query holds the CSV open while
    it refreshes, and Windows will not replace an open file, so a refused swap is retried for a few
    seconds. Returns False (leaving the old file untouched) if it stays locked; the next capture tries again."""
    import time
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(payload)
    for i in range(tries):
        try:
            os.replace(tmp, path)
            return True
        except PermissionError:
            if i < tries - 1:
                time.sleep(pause)
    tmp.unlink(missing_ok=True)
    return False


def write_csvs(xlsx, data: dict, updated: datetime | None = None) -> bool:
    """Writes the month's two CSVs. Nothing is touched when the data has not changed (so the
    Overview's "Updated" stamp means the last time the data changed, and Excel is left alone).
    Returns False when Excel kept a file locked; the workbook then simply shows the previous data."""
    updated = updated or datetime.now()
    data_csv, over_csv = csv_paths(xlsx)
    data_csv.parent.mkdir(parents=True, exist_ok=True)
    body = _csv_bytes(DATA_FIELDS, ({"Form": f, **r} for f, rows in data.items() for r in rows))
    try:
        if over_csv.exists() and data_csv.read_bytes() == body:
            return True
    except OSError:
        pass
    over = _csv_bytes(OVERVIEW_FIELDS, (
        {"Form": f, "Clients": len(rows), "Latest period": _latest_label(rows, f),
         **{lvl: sum(1 for r in rows if r["Status"] == lvl) for lvl in LEVELS},
         "Updated": f"{updated:%d %b %Y %H:%M}"} for f, rows in data.items()))
    # The summary first, the data last: the unchanged-data check above then only skips a write
    # when both files made it (a locked summary is retried, never left stale behind new data).
    return _replace_file(over_csv, over) and _replace_file(data_csv, body)


def sheet_names(forms) -> dict:
    used: set = set()
    return {f: _sheet_name(f, used) for f in forms}


def expected_sheets(forms) -> list:
    return ["Overview", *sheet_names(forms).values()]


def workbook_matches(xlsx, forms) -> bool:
    """True when the workbook exists, is the current layout, and has exactly the sheets these forms need."""
    try:
        from openpyxl import load_workbook
        wb = load_workbook(xlsx, read_only=True)
        try:
            return wb.properties.title == FORMAT and list(wb.sheetnames) == expected_sheets(forms)
        finally:
            wb.close()
    except Exception:
        return False


def build_shell(path, month: date, forms) -> Path:
    """The frame of the workbook: titles, widths, live counts and all the colouring. The tables
    arrive with the Power Queries (attach_queries), so every sheet is exactly as long as its data
    and everything below it stays white."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    wb.properties.title = FORMAT
    ov = wb.active
    ov.title = "Overview"
    font = lambda **kw: Font(**{"name": "Calibri", "size": 10, **kw})       # noqa: E731
    names = sheet_names(forms)

    def frame(ws, widths):
        ws.sheet_view.showGridLines = False
        widths = tuple(widths)
        for i, w in enumerate(widths):
            ws.column_dimensions[chr(65 + i)].width = w
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True

    for form, name in names.items():
        ws = wb.create_sheet(name)
        frame(ws, (WIDTHS[c] for c in sheet_columns(form)))
        ws["A1"] = form
        ws["A1"].font = font(size=16, bold=True, color=HEAD_FILL)
        ws.row_dimensions[3].height = 22
        ws.freeze_panes = "A4"
        ws.print_title_rows = "3:3"

    frame(ov, OVERVIEW_WIDTHS)
    for form_sheet in names.values():
        wb[form_sheet]['A2'].font = font(color='FF555555')
    ov.column_dimensions["H"].hidden = True                 # the "Updated" stamp, read by the A2 formula
    ov["A1"] = f"LTT — {calendar.month_name[month.month]} {month.year}"
    ov["A1"].font = font(size=18, bold=True, color=HEAD_FILL)
    ov["A2"].font = font(color="FF555555")
    ov["A3"] = ("Each client shows only the latest period filed for each form, one tab per form. The sheet "
                "refreshes by itself while it is open; this file is the record once the month ends.")
    ov["A3"].font = font(color="FF555555")
    ov.row_dimensions[4].height = 30
    ov.freeze_panes = "A5"

    tmp = path.with_suffix(".tmp.xlsx")
    wb.save(tmp)
    os.replace(tmp, path)
    return path


def _rgb(argb: str) -> int:
    """'FF1F6F4A' -> the BGR integer Excel's COM colours use."""
    return int(argb[2:4], 16) + int(argb[4:6], 16) * 256 + int(argb[6:8], 16) * 65536


def _style_table(ws, lo, r, first_row: int, centre: tuple, status_col: str | None, mono: tuple = ()) -> None:
    """Body fonts and the conditional colouring, applied through Excel once the table exists: Excel
    moves cells when it creates a table, so formatting laid down beforehand ends up misaligned."""
    last_col = chr(64 + r(lambda: lo.ListColumns.Count))
    for i in range(1, r(lambda: lo.ListColumns.Count) + 1):
        body = r(lambda: lo.ListColumns(i).DataBodyRange)
        f = r(lambda: body.Font)
        r(lambda: setattr(f, "Name", "Consolas" if i in mono else "Calibri"))
        r(lambda: setattr(f, "Size", 10))
        r(lambda: setattr(body, "HorizontalAlignment", -4108 if i in centre else -4131))
        r(lambda: setattr(body, "IndentLevel", 0 if i in centre else 1))
        r(lambda: setattr(body, "VerticalAlignment", -4108))
    hdr = r(lambda: lo.HeaderRowRange)
    r(lambda: setattr(hdr, "HorizontalAlignment", -4131))
    r(lambda: setattr(hdr, "IndentLevel", 1))
    r(lambda: ws.Activate())
    r(lambda: ws.Range("A1").Select())            # COM reads relative formulas from the active cell
    rng = r(lambda: ws.Range(f"A1:{last_col}{MAX_ROWS}"))
    r(lambda: rng.FormatConditions.Delete())
    body_row = f"ROW()>={first_row}"
    if status_col:
        srng = r(lambda: ws.Range(f"{status_col}1:{status_col}{MAX_ROWS}"))
        for lvl, fill in STATUS_FILL.items():
            fc = r(lambda: srng.FormatConditions.Add(2, 0, f'=AND({body_row},${status_col}1="{lvl}")'))
            r(lambda: setattr(r(lambda: fc.Interior), "Color", _rgb(fill)))
            r(lambda: setattr(r(lambda: fc.Font), "Color", _rgb(STATUS_TEXT[lvl])))
            r(lambda: setattr(r(lambda: fc.Font), "Bold", True))
    row_has_data = f"COUNTA($A1:${last_col}1)>0"
    fc = r(lambda: rng.FormatConditions.Add(2, 0, f"=AND({body_row},{row_has_data},MOD(ROW(),2)=0)"))
    r(lambda: setattr(r(lambda: fc.Interior), "Color", _rgb(BAND_FILL)))
    fc = r(lambda: rng.FormatConditions.Add(2, 0, f"=AND({body_row},{row_has_data})"))
    edge = r(lambda: fc.Borders(-4107))
    r(lambda: setattr(edge, "LineStyle", 1))
    r(lambda: setattr(edge, "Color", _rgb("FFD9D9D9")))


def attach_queries(xlsx, forms, month_file=None) -> None:
    """Uses Excel itself (COM) to add one Power Query per sheet over the month's CSVs, load each as
    a table, refresh, and set refresh-on-open / every-minute. Needs Excel installed. `month_file` is
    the workbook's final path when `xlsx` is a copy being built (the CSVs are named after it)."""
    import time
    import comtypes
    import comtypes.client
    from _ctypes import COMError

    try:
        comtypes.CoInitialize()                    # harmless on a thread that already has COM
    except Exception:
        pass

    def r(fn, tries=60):
        for i in range(tries):                     # Excel rejects calls while still loading
            try:
                return fn()
            except COMError as e:
                if e.hresult not in (-2147418111, -2147417846) or i == tries - 1:
                    raise
                time.sleep(0.5)

    xlsx = Path(xlsx)
    data_csv, over_csv = csv_paths(month_file or xlsx)
    esc = lambda t: str(t).replace('"', '""')       # noqa: E731
    head = ('let Source = Csv.Document(File.Contents("%s"),[Delimiter=",", Encoding=65001, '
            'QuoteStyle=QuoteStyle.Csv]), Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars=true]), '
            'AsText = Table.TransformColumnTypes(Promoted, List.Transform(Table.ColumnNames(Promoted), '
            'each {_, type text}))')
    ints = ", ".join('{"%s", Int64.Type}' % c for c in ("Clients", *LEVELS))
    over_m = head % esc(over_csv.resolve()) + f', Out = Table.TransformColumnTypes(AsText, {{{ints}}}) in Out'
    jobs = [("Overview", "A4", "Overview", over_m, None)]
    for i, (form, sheet) in enumerate(sheet_names(forms).items(), 1):
        drop = ", ".join(f'"{c}"' for c in DATA_FIELDS if c == "Form" or c not in sheet_columns(form))
        m = (head % esc(data_csv.resolve())
             + f', Rows = Table.SelectRows(AsText, each [Form] = "{esc(form)}"), '
               f'Out = Table.RemoveColumns(Rows, {{{drop}}}) in Out')
        jobs.append((sheet, "A3", f"Form{i}", m, sheet_columns(form)))

    xl = comtypes.client.CreateObject("Excel.Application")
    wb = None
    try:
        r(lambda: setattr(xl, "Visible", False))
        r(lambda: setattr(xl, "DisplayAlerts", False))
        wb = r(lambda: xl.Workbooks.Open(str(xlsx.resolve())))
        for sheet, anchor, query, m, cols in jobs:
            r(lambda: wb.Queries.Add(query, m))
            ws = r(lambda: wb.Worksheets(sheet))
            lo = r(lambda: ws.ListObjects.Add(
                0, 'OLEDB;Provider=Microsoft.Mashup.OleDb.1;Data Source=$Workbook$;'
                   f'Location={query};Extended Properties=""', None, 1, r(lambda: ws.Range(anchor))))
            r(lambda: setattr(lo, "Name", "LTT_" + query))
            qt = r(lambda: lo.QueryTable)
            r(lambda: setattr(qt, "CommandType", 2))
            r(lambda: setattr(qt, "CommandText", f"SELECT * FROM [{query}]"))
            r(lambda: setattr(qt, "BackgroundQuery", False))
            r(lambda: setattr(qt, "RefreshStyle", 0))          # overwrite: never shift the counts/colouring below
            r(lambda: setattr(qt, "AdjustColumnWidth", False))
            r(lambda: setattr(qt, "PreserveColumnInfo", True))
            r(lambda: setattr(qt, "PreserveFormatting", True))
            r(lambda: qt.Refresh(False))
            r(lambda: setattr(lo, "TableStyle", ""))
            hdr = r(lambda: lo.HeaderRowRange)
            r(lambda: setattr(r(lambda: hdr.Interior), "Color", _rgb(HEAD_FILL)))
            r(lambda: setattr(r(lambda: hdr.Font), "Color", _rgb("FFFFFFFF")))
            r(lambda: setattr(r(lambda: hdr.Font), "Bold", True))
            r(lambda: setattr(hdr, "VerticalAlignment", -4108))
            if cols is None:
                _style_table(ws, lo, r, 5, centre=(2, 4, 5, 6, 7), status_col=None)
            else:
                _style_table(ws, lo, r, 4, centre=(), status_col=chr(65 + cols.index("Status")),
                             mono=tuple(i + 1 for i, c in enumerate(cols) if c in ("PAN", "GSTIN")))
            tbl = "LTT_" + query
            if sheet == "Overview":
                f = (f'=IF(INDEX({tbl}[Updated],1)="","Waiting for the first capture…",'
                     f'"Updated "&INDEX({tbl}[Updated],1))')
            else:
                parts = [f'ROWS({tbl}[PAN])&" clients"']
                for lvl in (*LEVELS, NOT_APPLICABLE):
                    n = f'COUNTIF({tbl}[Status],"{lvl}")'
                    parts.append(f'IF({n}>0,"  ·  "&{n}&" {lvl}","")')
                f = "=" + "&".join(parts)
            r(lambda: setattr(r(lambda: ws.Range("A2")), "Formula", f))
            conn = r(lambda: qt.WorkbookConnection.OLEDBConnection)
            r(lambda: setattr(conn, "BackgroundQuery", True))
            r(lambda: setattr(conn, "RefreshOnFileOpen", True))
            r(lambda: setattr(conn, "RefreshPeriod", 1))
        r(lambda: xl.CalculateFull())
        r(lambda: wb.Worksheets("Overview").Activate())
        r(lambda: wb.Save())
        r(lambda: wb.Close(False))
        wb = None
    finally:
        # a failed build must not leave a hidden Excel holding the half-built file
        for step in ((lambda: wb.Close(False)) if wb is not None else None, lambda: xl.Quit()):
            if step is not None:
                try:
                    r(step, tries=10)
                except Exception:
                    pass


def _open_in_excel(xlsx: Path) -> bool:
    """True while Excel (or anything) holds the file open. Looks at the file itself: the ~$ owner
    file Excel leaves behind can outlive a crash and would block the rebuild for ever."""
    try:
        with open(xlsx, "r+b"):
            return False
    except FileNotFoundError:
        return False
    except OSError:
        return True


def ensure_workbook(xlsx, month: date, forms, excel: bool = True) -> Path:
    """Builds the live workbook when it is missing or has no sheet for a form that has appeared.
    Raises RuntimeError when the file is open in Excel (it is then rebuilt after it is closed)."""
    xlsx = Path(xlsx)
    forms = list(forms)
    if workbook_matches(xlsx, forms):
        return xlsx
    if _open_in_excel(xlsx):
        raise RuntimeError(f"{xlsx.name} is open in Excel and needs updating - close it and open the LTT sheet again.")
    # Built aside and swapped in only once complete: a shell whose queries never attached (Excel
    # missing, busy or killed) would otherwise look current and never be rebuilt.
    work = xlsx.with_name(xlsx.stem + ".building.xlsx")
    try:
        build_shell(work, month, forms)
        if excel:
            attach_queries(work, forms, month_file=xlsx)
        os.replace(work, xlsx)
    finally:
        try:
            work.unlink(missing_ok=True)
        except OSError:                # still held by an Excel that did not let go; the next build overwrites it
            pass
    return xlsx


_export_lock = threading.Lock()


def export_month(db, app_dir=None, today: date | None = None, create: bool = False,
                 excel: bool = True) -> Path | None:
    """Brings this month's files up to date from the tracker: the CSVs always, the workbook only
    when it is missing or lacks a sheet. `create` (the menu action) makes the LTT folder and raises
    on problems; without it nothing is written until the office has switched the sheet on."""
    today = today or date.today()
    app_dir = Path(app_dir or db.app_dir)
    if not create and not (app_dir / FOLDER).is_dir():
        return None
    with _export_lock:                 # the capture timer and the menu never build the same files at once
        return _export_month(db, app_dir, today, create, excel)


def _export_month(db, app_dir: Path, today: date, create: bool, excel: bool) -> Path | None:
    month = month_start(today)
    xlsx = workbook_path(app_dir, month)
    data = latest_rows(db.get_srpf_containers(limit=1_000_000, slim=True), month)
    if not write_csvs(xlsx, data):
        print("[ltt] the month's CSV is busy in Excel; the sheet keeps its last data until the next capture")
    try:
        return ensure_workbook(xlsx, month, data.keys(), excel)
    except Exception:
        if create:
            raise
        print("[ltt] new sheet not added yet (the workbook is open in Excel, or Excel is unavailable)")
        return xlsx


_timer = None
_timer_lock = threading.Lock()


def schedule_export(db, delay: float = 10.0) -> None:
    """Rewrites this month's workbook `delay` seconds after the last capture (bursts collapse into
    one write). Does nothing until the LTT folder exists. Never raises - a background refresh must
    not disturb the app, and a workbook open in Excel is simply tried again after the next capture."""
    global _timer
    try:
        if not (Path(db.app_dir) / FOLDER).is_dir():
            return

        def run():
            try:
                export_month(db)
            except Exception as e:
                print(f"[ltt] monthly sheet refresh skipped: {e}")

        with _timer_lock:
            if _timer is not None:
                _timer.cancel()
            _timer = threading.Timer(delay, run)
            _timer.daemon = True
            _timer.start()
    except Exception:
        pass
