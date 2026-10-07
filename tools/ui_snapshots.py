"""Render every Sera window/dialog offscreen to PNG, using a throwaway DB with fake data.

Usage:  venv\\Scripts\\python.exe tools\\ui_snapshots.py [out_dir]
        venv\\Scripts\\python.exe tools\\ui_snapshots.py out_dir --size 1024x720 --size 1920x1000 [--scale 0.8]

--size WxH (repeatable) renders only the Tracker Dump, inside the app shell, at each on-screen size.
--scale F sets QT_SCALE_FACTOR before the app is created, like a PC with that scale; the app then
lays out in size / F logical px (1024x720 at 0.80 = a 1280-px-wide layout drawn at 1024 px).
Never touches master.db: everything runs against a temp database.
"""
import argparse
import os
import sys
import shutil
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

_ap = argparse.ArgumentParser()
_ap.add_argument("out_dir", nargs="?", default=None)
_ap.add_argument("--size", action="append", default=[])
_ap.add_argument("--scale", type=float, default=None)
ARGS = _ap.parse_args(sys.argv[1:])
if ARGS.scale is not None:
    os.environ["QT_SCALE_FACTOR"] = f"{ARGS.scale:.2f}"
if ARGS.size:
    # A visible window is clamped to the real screen (a 1536-logical-px screen cannot show 1920 px),
    # and a hidden one never re-lays out. The offscreen platform takes the requested size as it is.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

OUT = Path(ARGS.out_dir) if ARGS.out_dir else ROOT / "Mockups" / "current"
OUT.mkdir(parents=True, exist_ok=True)

app = QApplication.instance() or QApplication(sys.argv)

from database import SeraDatabase
from ui.utils.theme import get_theme_stylesheet

tmp = tempfile.mkdtemp()
db = SeraDatabase(os.path.join(tmp, "snap.db"), "0123456789abcdef" * 4)
app.setStyleSheet(get_theme_stylesheet(db.get_setting("theme", "light")))


def seed():
    cols = db.get_mcl_columns()
    svcs = db.get_services() if hasattr(db, "get_services") else []
    sids = [s["id"] for s in svcs][:2]
    names = ["Sharma Traders Pvt Ltd", "Gupta & Sons", "Mehta Textiles LLP", "Rao Consultants",
             "Iyer Foods", "Khan Logistics", "Patel Jewellers", "Nair Clinic", "Singh Motors",
             "Bose Publications", "Das Hardware", "Reddy Infra"]
    for i, n in enumerate(names):
        vals = {}
        for c in cols:
            lab = c["label"].upper()
            if "NAME" in lab:
                vals[c["id"]] = n
            elif lab == "PAN":
                vals[c["id"]] = f"ABCDE{1000 + i}F"
            elif "PASSWORD" in lab:
                vals[c["id"]] = "demo-pass"
            elif c["field_type"] == "dropdown" and c["dropdown_options"]:
                vals[c["id"]] = c["dropdown_options"][i % len(c["dropdown_options"])]
        try:
            db.add_client(vals, "Demo client", sids)
        except Exception as e:
            print("seed skip", n, e)


seed()
failed = []


def snap(name, widget, size=None):
    try:
        if size:
            widget.resize(*size)
        widget.show()
        for _ in range(6):
            app.processEvents()
        widget.grab().save(str(OUT / f"{name}.png"))
        print("ok  ", name)
    except Exception:
        failed.append(name)
        print("FAIL", name)
        traceback.print_exc()
    finally:
        try:
            widget.close()
        except Exception:
            pass


def build(name, factory, size=None):
    try:
        w = factory()
    except Exception:
        failed.append(name)
        print("FAIL (construct)", name)
        traceback.print_exc()
        return
    snap(name, w, size)


# --- tracker dump at chosen window sizes (--size) ---------------------------------------------
if ARGS.size:
    import json
    from ui.shell.app_shell import AppShell
    from ui.windows.tracker_dump_window import TrackerDumpWindow

    # Fake rows so all seven columns have real-looking text (names, PANs and ARNs are placeholders).
    fake_rows = [
        ("Income Tax (ITR-4)", "AY 2025-26", "DEMO-ARN-0001", "VSDC-X_itr_submitted", "Submitted", "ABCDE1001F", "ITR-4", "DEMO-PC-01"),
        ("Income Tax (ITR-1)", "AY 2025-26", "DEMO-ARN-0002", "SGT_live", "Submitted", "ABCDE1002F", "ITR-1", "DEMO-PC-02"),
        ("GST Portal", "GSTR-3B Jul 2026", "", "DOM_Tracker", "Pending e-Verification", "ABCDE1003F", "GSTR-3B", "DEMO-PC-01"),
        ("TRACES / TDS", "Q2 2026-27 (26Q)", "", "SGT_shadow", "Not submitted", "ABCDE1004F", "26Q", "DEMO-PC-03"),
        ("Income Tax (ITR-4)", "AY 2026-27", "DEMO-ARN-0005", "VSDC_itr_submitted", "Submitted", "ABCDE1005F", "ITR-4", "DEMO-PC-02"),
        ("GST Portal", "GSTR-1 Aug 2026", "DEMO-ARN-0006", "SGT_live", "Submitted", "ABCDE1006F", "GSTR-1", "DEMO-PC-01"),
    ]
    for portal, period, arn, method, status, pan, ftype, device in fake_rows * 2:
        try:
            db.insert_tracker_dump(portal=portal, period_label=period, arn_number=arn or None,
                                   capture_method=method, status=status, pan=pan, filing_type=ftype,
                                   raw_payload_json=json.dumps({"pan": pan, "device_name": device}))
        except Exception as e:
            print("tracker seed skip", pan, e)

    # The pages a small screen has to hold: Tracker Dump, Search and Manage Clients (admin view).
    from ui.windows.search_window import SearchWindow
    from ui.windows.admin_window import AdminWindow
    shell = AppShell()
    pages = [("search", SearchWindow(db)), ("admin", AdminWindow(db, actor="Admin")), ("tracker_dump", TrackerDumpWindow(db))]
    for _name, page in pages:
        shell.add_page(page)
    pages[1][1].refresh()

    # --size is the window as it appears on screen. With --scale F the app lays out in w/F logical px,
    # the way a 1024 px screen at 80 % gives Sera 1280 px to work with.
    factor = ARGS.scale or 1.0
    suffix = f"_scale{ARGS.scale:.2f}" if ARGS.scale is not None else ""
    for size in ARGS.size:
        w, h = (int(v) for v in size.lower().split("x"))
        try:
            shell.resize(round(w / factor), round(h / factor))
            shell.show()
            for name, page in pages:
                shell.set_current_page(page)
                for _ in range(40):
                    app.processEvents()
                if name == "tracker_dump":
                    page._adjust_table_columns()
                    hidden = [c for c in range(7) if page.table.isColumnHidden(c)]
                    print("    tracker hidden cols:", hidden)
                for _ in range(6):
                    app.processEvents()
                out = f"{name}_{w}x{h}{suffix}"
                shell.grab().save(str(OUT / f"{out}.png"))
                print("ok  ", out)
            shell.hide()
        except Exception:
            failed.append(f"size {size}")
            traceback.print_exc()

    # Dialogs: their preferred and minimum sizes, so a dialog bigger than the screen shows up here.
    from ui.dialogs.unified_settings_dialog import UnifiedSettingsDialog
    from ui.dialogs.display_scale_dialog import DisplayScaleDialog
    for label, factory in (("settings_general", lambda: UnifiedSettingsDialog(db, actor="Admin", page="general")),
                           ("display_scale", lambda: DisplayScaleDialog())):
        try:
            dlg = factory()
            hint, mins = dlg.sizeHint(), dlg.minimumSizeHint()
            print(f"dialog {label}: sizeHint {hint.width()}x{hint.height()}, minimum {mins.width()}x{mins.height()}")
            dlg.close()
        except Exception:
            failed.append(f"dialog {label}")
            traceback.print_exc()
    print("FAILED:", failed)
    shutil.rmtree(tmp, ignore_errors=True)
    os._exit(0)

# --- main shell with each page + slide panel ---------------------------------------------------
try:
    from ui.shell.app_shell import AppShell
    from ui.windows.search_window import SearchWindow
    from ui.windows.client_detail_window import ClientDetailWindow
    from ui.windows.admin_window import AdminWindow
    from ui.windows.tracker_dump_window import TrackerDumpWindow

    shell = AppShell()
    search = SearchWindow(db)
    detail = ClientDetailWindow(db, actor="Admin")
    admin = AdminWindow(db, actor="Admin")
    tracker = TrackerDumpWindow(db)
    for p in (search, admin, tracker):
        shell.add_page(p)
    shell.slide_panel.set_widget(detail, persistent=True)
    admin.refresh()
    shell.resize(1500, 900)
    shell.show()
    app.processEvents()

    for idx, nm in enumerate(["01_all_clients_search", "02_manage_clients_admin", "03_tracker_dump"]):
        shell.content_area.setCurrentIndex(idx)
        for _ in range(40):
            app.processEvents()
        shell.grab().save(str(OUT / f"{nm}.png"))
        print("ok  ", nm)

    # client detail slide panel over the search page
    try:
        shell.content_area.setCurrentIndex(0)
        rows = db.search_clients("")
        cid = rows[0]["id"] if rows else 1
        detail.load_client(cid)
        shell.slide_panel.slide_in()
        import time
        t0 = time.time()
        while time.time() - t0 < 1.2:   # let the slide animation actually finish
            app.processEvents()
            time.sleep(0.01)
        shell.grab().save(str(OUT / "04_client_detail_panel.png"))
        print("ok   04_client_detail_panel")
    except Exception:
        failed.append("client_detail_panel")
        traceback.print_exc()
    shell.close()
except Exception:
    failed.append("shell")
    traceback.print_exc()

# --- settings pages -----------------------------------------------------------------------------
from ui.dialogs.unified_settings_dialog import UnifiedSettingsDialog, _PAGE_MAP
for key in _PAGE_MAP:
    def mk(key=key):
        return UnifiedSettingsDialog(db, actor="Admin", page=key)
    build(f"settings_{key}", mk)

# --- other dialogs ------------------------------------------------------------------------------
from ui.dialogs.audit_log_dialog import AuditLogDialog
from ui.dialogs.csv_import_dialog import CSVImportDialog
from ui.dialogs.mcl_manager_dialog import MCLManagerDialog, ColumnEditDialog
from ui.dialogs.service_manager_dialog import ServiceManagerDialog, ServiceEditDialog
from ui.dialogs.change_master_password_dialog import ChangeMasterPasswordDialog
from ui.dialogs.first_run_dialog import FirstRunDialog
from ui.dialogs.loading_dialog import StartupLoadingDialog
from ui.dialogs.office_recovery_dialog import OfficeRecoveryDialog
from ui.dialogs.rejoin_office_dialog import RejoinOfficeDialog
from ui.dialogs.sera_sync_dialog import SeraSyncDialog
from ui.dialogs.settings_dialog import SettingsDialog
from ui.dialogs.sgt_lab_dialog import SgtLabDialog
from ui.dialogs.update_dialog import ForceUpdateDialog

build("dlg_audit_log", lambda: AuditLogDialog(db, actor="Admin"))
build("dlg_csv_import", lambda: CSVImportDialog(db))
build("dlg_mcl_manager", lambda: MCLManagerDialog(db))
build("dlg_mcl_column_edit", lambda: ColumnEditDialog())
build("dlg_service_manager", lambda: ServiceManagerDialog(db))
build("dlg_service_edit", lambda: ServiceEditDialog(db))
build("dlg_change_master_password", lambda: ChangeMasterPasswordDialog(tmp))
build("dlg_first_run", lambda: FirstRunDialog(tmp))
build("dlg_startup_loading", lambda: StartupLoadingDialog())
build("dlg_office_recovery", lambda: OfficeRecoveryDialog(tmp))
build("dlg_rejoin_office", lambda: RejoinOfficeDialog())
build("dlg_sera_sync", lambda: SeraSyncDialog(None, db=db, actor="Admin"))
build("dlg_settings_legacy", lambda: SettingsDialog(db))
build("dlg_sgt_lab", lambda: SgtLabDialog())
build("dlg_force_update", lambda: ForceUpdateDialog({"current_version": "2.12.0", "latest_version": "2.13.0", "release_notes": "Demo release notes.", "download_url": ""}, auto_start=False))

print("FAILED:", failed)
shutil.rmtree(tmp, ignore_errors=True)
os._exit(0)
