"""Render every Sera window/dialog offscreen to PNG, using a throwaway DB with fake data.

Usage:  venv\\Scripts\\python.exe tools\\ui_snapshots.py [out_dir]
Never touches master.db: everything runs against a temp database.
"""
import os
import sys
import shutil
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "Mockups" / "current"
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
