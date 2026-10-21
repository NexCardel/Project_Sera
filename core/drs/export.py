"""core/drs/export.py - DRS Excel workbook and CSV live feed generator.

Generates monthly DRS workbooks (app_dir/DRS/DRS_YYYY-MM.xlsx) and companion CSVs.
Matches the exact visual styling, palette, fonts, and Power Query architecture of LTT.
Refreshes ONLY when new entries appear in tracker dump (debounced event trigger).
"""
from __future__ import annotations

import calendar
import codecs
import io
import os
import re
import subprocess
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from core.ltt.feed import LEVELS, NOT_APPLICABLE
from core.ltt.monthly import _rgb, _replace_file
from .config import get_active_columns
from .engine import NO_CAPTURES, build_drs_dataset

FOLDER = "DRS"
FORMAT = "DRS live v1"

HEAD_FILL = "FF1F6F4A"            # evergreen, the Sera colour
BAND_FILL = "FFF4F8F5"

STATUS_FILL = {
    LEVELS[0]: "FFF4A99B",
    LEVELS[1]: "FFFFDB70",
    LEVELS[2]: "FFC9E06F",
    LEVELS[3]: "FF86D4A0",
    NO_CAPTURES: "FFDADADA",
    NOT_APPLICABLE: "FFDADADA",
}

STATUS_TEXT = {
    LEVELS[0]: "FF7A1F12",
    LEVELS[1]: "FF6B4A00",
    LEVELS[2]: "FF4A5A0A",
    LEVELS[3]: "FF0F4F2A",
    NO_CAPTURES: "FF555555",
    NOT_APPLICABLE: "FF555555",
}

DEFAULT_WIDTHS = {
    "PAN": 14,
    "GSTIN": 19,
    "Name": 36,
    "Client Name": 36,
    "Company Name": 36,
    "Trade Name": 36,
    "Latest period": 26,
    "Status": 24,
    "ARN": 24,
    "Captured on": 18,
    "Captured by": 18,
    "Portal": 18,
    "Mobile": 16,
    "Phone": 16,
    "Email": 28,
}


def month_start(d: date) -> date:
    return d.replace(day=1)


def drs_workbook_path(app_dir, month: date) -> Path:
    return Path(app_dir) / FOLDER / f"DRS_{month:%Y-%m}.xlsx"


def drs_csv_paths(xlsx: Path) -> tuple[Path, Path]:
    x = Path(xlsx)
    return x.with_suffix(".csv"), x.with_name(x.stem + "_overview.csv")


def _csv_bytes(fields, rows) -> bytes:
    """UTF-8 with BOM for Excel compatibility."""
    import csv
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return codecs.BOM_UTF8 + buf.getvalue().encode("utf-8")


def _sheet_name(form: str, used: set) -> str:
    base = re.sub(r"[\[\]:*?/\\]", "-", form).strip()[:31] or "Form"
    name, i = base, 2
    while name.lower() in used or name.lower() == "overview":
        suffix = f" ({i})"
        name, i = base[:31 - len(suffix)] + suffix, i + 1
    used.add(name.lower())
    return name


def sheet_names(forms: list[str]) -> dict[str, str]:
    used: set = set()
    return {f: _sheet_name(f, used) for f in forms}


def build_drs_shell(path: Path, month: date, forms: list[str], active_cols: list[dict[str, Any]]) -> Path:
    """Builds the styled workbook shell with openpyxl."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    wb.properties.title = FORMAT

    # Overview sheet
    ov = wb.active
    ov.title = "Overview"

    font_regular = Font(name="Calibri", size=10)
    font_bold = Font(name="Calibri", size=10, bold=True)
    font_sub = Font(name="Calibri", size=10, color="FF555555")
    font_title = Font(name="Calibri", size=18, bold=True, color=HEAD_FILL)
    font_form_title = Font(name="Calibri", size=16, bold=True, color=HEAD_FILL)

    ov.sheet_view.showGridLines = False
    ov["A1"] = f"Deadline Reminder System (DRS) — {calendar.month_name[month.month]} {month.year}"
    ov["A1"].font = font_title
    ov["A2"] = "Updated Waiting for data…"
    ov["A2"].font = font_sub
    ov["A3"] = "Real-time client filing compliance and deadline status across all active portal services."
    ov["A3"].font = font_sub
    ov.row_dimensions[4].height = 24
    ov.freeze_panes = "A5"

    names = sheet_names(forms)
    col_labels = [c["label"] for c in active_cols]

    for form, sname in names.items():
        ws = wb.create_sheet(sname)
        ws.sheet_view.showGridLines = False
        ws["A1"] = form
        ws["A1"].font = font_form_title
        ws["A2"] = "All registered clients for service"
        ws["A2"].font = font_sub
        ws.row_dimensions[3].height = 24
        ws.freeze_panes = "A4"

        # Column widths
        for i, lbl in enumerate(col_labels):
            col_letter = chr(65 + i) if i < 26 else f"A{chr(65 + i - 26)}"
            w = DEFAULT_WIDTHS.get(lbl, 20)
            ws.column_dimensions[col_letter].width = w

    tmp = path.with_suffix(".tmp.xlsx")
    wb.save(tmp)
    os.replace(tmp, path)
    return path


def attach_drs_queries(xlsx: Path, forms: list[str], active_cols: list[dict[str, Any]]) -> None:
    """Attaches Power Query live connections via Windows COM Excel if available."""
    try:
        import comtypes.client
    except ImportError:
        return

    data_csv, over_csv = drs_csv_paths(xlsx)
    if not data_csv.exists() or not over_csv.exists():
        return

    def r(fn, tries=15, pause=0.25):
        import time
        for i in range(tries):
            try:
                return fn()
            except Exception:
                if i < tries - 1:
                    time.sleep(pause)
                else:
                    raise

    names = sheet_names(forms)
    esc = lambda p: str(p).replace('"', '""')  # noqa: E731
    head = ('let Source = Csv.Document(File.Contents("%s"), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]), '
            'Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars=true]), '
            'AsText = Table.TransformColumnTypes(Promoted, Table.TransformColumnTypes(Promoted, {})[Column1])')

    over_fields = ("Form", "Clients", "Latest period", *LEVELS, NO_CAPTURES, "Updated")
    jobs = [(
        "Overview", "A4", "DRS_Overview",
        head % esc(over_csv.resolve()) + ' in AsText',
        over_fields
    )]

    data_fields = ["Form"] + [c["label"] for c in active_cols]
    for i, (form, sname) in enumerate(names.items(), 1):
        drop = ", ".join(f'"{c}"' for c in data_fields if c == "Form")
        m = (head % esc(data_csv.resolve())
             + f', Rows = Table.SelectRows(AsText, each [Form] = "{esc(form)}"), '
               f'Out = Table.RemoveColumns(Rows, {{{drop}}}) in Out')
        jobs.append((sname, "A3", f"DRS_Form_{i}", m, [c["label"] for c in active_cols]))

    xl = None
    wb = None
    try:
        xl = comtypes.client.CreateObject("Excel.Application")
        r(lambda: setattr(xl, "Visible", False))
        r(lambda: setattr(xl, "DisplayAlerts", False))
        wb = r(lambda: xl.Workbooks.Open(str(xlsx.resolve())))

        for sheet, anchor, query, m, cols in jobs:
            r(lambda: wb.Queries.Add(query, m))
            ws = r(lambda: wb.Worksheets(sheet))
            lo = r(lambda: ws.ListObjects.Add(
                0, 'OLEDB;Provider=Microsoft.Mashup.OleDb.1;Data Source=$Workbook$;'
                   f'Location={query};Extended Properties=""', None, 1, r(lambda: ws.Range(anchor))))
            r(lambda: setattr(lo, "Name", query))
            qt = r(lambda: lo.QueryTable)
            r(lambda: setattr(qt, "CommandType", 2))
            r(lambda: setattr(qt, "CommandText", f"SELECT * FROM [{query}]"))
            r(lambda: setattr(qt, "BackgroundQuery", False))
            r(lambda: setattr(qt, "RefreshStyle", 0))
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

            conn = r(lambda: qt.WorkbookConnection.OLEDBConnection)
            r(lambda: setattr(conn, "BackgroundQuery", True))
            r(lambda: setattr(conn, "RefreshOnFileOpen", True))
            r(lambda: setattr(conn, "RefreshPeriod", 1))

        r(lambda: xl.CalculateFull())
        r(lambda: wb.Worksheets("Overview").Activate())
        r(lambda: wb.Save())
        r(lambda: wb.Close(False))
        wb = None
    except Exception as e:
        print(f"[drs] Power Query attachment fallback: {e}")
    finally:
        if wb is not None:
            try: wb.Close(False)
            except Exception: pass
        if xl is not None:
            try: xl.Quit()
            except Exception: pass


def populate_direct_openpyxl(xlsx: Path, month: date, data: dict[str, list[dict]], active_cols: list[dict[str, Any]], updated: datetime) -> None:
    """Populates rows directly into the Excel workbook via openpyxl.
    Ensures the workbook has full data immediately even if Excel is not installed or COM is bypassed.
    """
    from openpyxl import load_workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    wb = load_workbook(xlsx)
    names = sheet_names(list(data.keys()))
    col_labels = [c["label"] for c in active_cols]

    font_head = Font(name="Calibri", size=10, bold=True, color="FFFFFFFF")
    fill_head = PatternFill(start_color=HEAD_FILL, end_color=HEAD_FILL, fill_type="solid")
    font_body = Font(name="Calibri", size=10)
    font_mono = Font(name="Consolas", size=10)

    # 1. Update Overview
    if "Overview" in wb.sheetnames:
        ov = wb["Overview"]
        ov["A2"] = f"Updated {updated:%d %b %Y %H:%M}"
        # Header row at row 4
        ov_headers = ["Form", "Clients", "Latest period", *LEVELS, NO_CAPTURES, "Updated"]
        for c_idx, h in enumerate(ov_headers, 1):
            cell = ov.cell(row=4, column=c_idx, value=h)
            cell.font = font_head
            cell.fill = fill_head
            cell.alignment = Alignment(horizontal="center", vertical="center")

        r_idx = 5
        for form, rows in data.items():
            counts = {lvl: sum(1 for r in rows if r.get("Status") == lvl) for lvl in (*LEVELS, NO_CAPTURES)}
            best_period = rows[0].get("Latest period", NO_CAPTURES) if rows else NO_CAPTURES
            row_vals = [form, len(rows), best_period] + [counts.get(lvl, 0) for lvl in (*LEVELS, NO_CAPTURES)] + [f"{updated:%d %b %Y %H:%M}"]
            for c_idx, v in enumerate(row_vals, 1):
                cell = ov.cell(row=r_idx, column=c_idx, value=v)
                cell.font = font_body
                if c_idx in (2, 4, 5, 6, 7, 8):
                    cell.alignment = Alignment(horizontal="center")
            r_idx += 1

    # 2. Update Form Sheets
    for form, sname in names.items():
        if sname not in wb.sheetnames:
            continue
        ws = wb[sname]
        rows = data.get(form, [])
        ws["A2"] = f"{len(rows)} clients registered for service"

        # Headers at row 3
        for c_idx, lbl in enumerate(col_labels, 1):
            cell = ws.cell(row=3, column=c_idx, value=lbl)
            cell.font = font_head
            cell.fill = fill_head
            cell.alignment = Alignment(horizontal="left", vertical="center")

        # Rows starting at row 4
        for r_idx, r_data in enumerate(rows, 4):
            for c_idx, lbl in enumerate(col_labels, 1):
                val = r_data.get(lbl, "")
                cell = ws.cell(row=r_idx, column=c_idx, value=val)
                if any(kw in lbl.upper() for kw in ("PAN", "GSTIN", "ARN")):
                    cell.font = font_mono
                else:
                    cell.font = font_body

                # Status coloring
                if lbl == "Status" and val in STATUS_FILL:
                    cell.fill = PatternFill(start_color=STATUS_FILL[val], end_color=STATUS_FILL[val], fill_type="solid")
                    cell.font = Font(name="Calibri", size=10, bold=True, color=STATUS_TEXT.get(val, "FF000000"))

    wb.save(xlsx)


def write_drs_csvs(xlsx: Path, data: dict[str, list[dict]], active_cols: list[dict[str, Any]], updated: datetime) -> bool:
    """Writes DRS companion CSV files."""
    data_csv, over_csv = drs_csv_paths(xlsx)
    col_labels = [c["label"] for c in active_cols]

    flat_data = []
    for form, rows in data.items():
        for r in rows:
            entry = {"Form": form}
            for lbl in col_labels:
                entry[lbl] = r.get(lbl, "")
            flat_data.append(entry)

    over_rows = []
    for form, rows in data.items():
        counts = {lvl: sum(1 for r in rows if r.get("Status") == lvl) for lvl in (*LEVELS, NO_CAPTURES)}
        best_p = rows[0].get("Latest period", NO_CAPTURES) if rows else NO_CAPTURES
        over_rows.append({
            "Form": form,
            "Clients": len(rows),
            "Latest period": best_p,
            **counts,
            "Updated": f"{updated:%d %b %Y %H:%M}",
        })

    over_fields = ("Form", "Clients", "Latest period", *LEVELS, NO_CAPTURES, "Updated")
    over_bytes = _csv_bytes(over_fields, over_rows)
    data_bytes = _csv_bytes(["Form"] + col_labels, flat_data)

    return _replace_file(over_csv, over_bytes) and _replace_file(data_csv, data_bytes)


_export_lock = threading.Lock()


def export_drs(db, app_dir=None, today: date | None = None, force_rebuild: bool = False) -> Path | None:
    """Builds and refreshes the current month's DRS workbook and CSVs.
    Returns the path to the .xlsx workbook.
    """
    today = today or date.today()
    app_dir = Path(app_dir or db.app_dir)
    month = month_start(today)
    xlsx = drs_workbook_path(app_dir, month)

    with _export_lock:
        active_cols = get_active_columns(db)
        data = build_drs_dataset(db, active_cols)
        updated = datetime.now(timezone.utc).astimezone()

        # Build workbook shell if missing or forced
        needs_shell = force_rebuild or not xlsx.exists()
        if needs_shell:
            build_drs_shell(xlsx, month, list(data.keys()), active_cols)
            attach_drs_queries(xlsx, list(data.keys()), active_cols)

        # Write CSVs
        write_drs_csvs(xlsx, data, active_cols, updated)

        # Directly populate openpyxl for instant visual inspection
        try:
            populate_direct_openpyxl(xlsx, month, data, active_cols, updated)
        except Exception as e:
            print(f"[drs] Direct openpyxl population notice: {e}")

        return xlsx


_timer = None
_timer_lock = threading.Lock()


def schedule_drs_export(db, delay: float = 3.0):
    """Event-driven debounced export trigger.
    Refreshes ONLY when called upon new capture or sync arrival.
    Does NOT poll on fixed intervals.
    """
    global _timer
    try:
        def run():
            try:
                export_drs(db)
            except Exception as e:
                print(f"[drs] scheduled refresh skipped: {e}")

        with _timer_lock:
            if _timer is not None:
                _timer.cancel()
            _timer = threading.Timer(delay, run)
            _timer.daemon = True
            _timer.start()
            return _timer
    except Exception:
        return None


def open_drs_workbook(app_dir: str | Path, today: date | None = None) -> bool:
    """Opens the DRS workbook in Excel or default spreadsheet handler."""
    today = today or date.today()
    xlsx = drs_workbook_path(app_dir, month_start(today))
    if not xlsx.exists():
        return False
    try:
        os.startfile(str(xlsx))
        return True
    except Exception as e:
        print(f"[drs] Failed to open workbook: {e}")
        return False
