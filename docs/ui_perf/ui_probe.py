"""UI timing probe: throwaway DB, offscreen Qt. Usage: ui_probe.py <repo> <tmpdir> [clients] [dumps]"""
import os, sys, time, cProfile, pstats, io, random

repo, tmp = sys.argv[1], sys.argv[2]
N_CLIENTS = int(sys.argv[3]) if len(sys.argv) > 3 else 500
N_DUMPS = int(sys.argv[4]) if len(sys.argv) > 4 else 400
PROFILE = os.environ.get("PROBE_PROFILE", "")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
sys.path.insert(0, repo)
os.chdir(repo)

t0 = time.perf_counter()
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont
from database import SeraDatabase
t_imp = time.perf_counter() - t0

app = QApplication([])
app.setFont(QFont("Segoe UI", 10))
KEY = "0123456789abcdef" * 4
db = SeraDatabase(os.path.join(tmp, "p.db"), KEY, raw_db_path=os.path.join(tmp, "raw.db"))

results = []


def pump(n=8):
    for _ in range(n):
        app.processEvents()


def timed(label, fn, repeat=1, paint=None):
    best = None
    first = None
    for i in range(repeat):
        a = time.perf_counter()
        out = fn()
        pump()
        if paint is not None:
            paint.grab()
        d = (time.perf_counter() - a) * 1000
        if first is None:
            first = d
        best = d if best is None else min(best, d)
    results.append((label, first, best))
    print(f"{label:<52} first {first:8.1f} ms   best {best:8.1f} ms", flush=True)
    return out


def profiled(label, fn):
    if PROFILE and PROFILE in label:
        pr = cProfile.Profile()
        pr.enable()
        fn()
        pump()
        pr.disable()
        s = io.StringIO()
        pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(45)
        print(f"\n===== PROFILE {label} =====\n{s.getvalue()}", flush=True)


# ---- seed ----
cols = {c["label"]: c["id"] for c in db.get_mcl_columns()}
svcs = {s["name"]: s["id"] for s in db.get_services()}
svc_ids = list(svcs.values())
random.seed(7)
a = time.perf_counter()
ids = [c["id"] for c in db.search_clients("")]
SEEDED = bool(ids)
for i in range(0 if SEEDED else N_CLIENTS):
    vals = {}
    for lbl, cid in cols.items():
        L = lbl.upper()
        if "COMPANY" in L:
            vals[cid] = f"Client {i:04d} Traders Pvt Ltd"
        elif "PROPRIETOR" in L:
            vals[cid] = f"Owner {i:04d}"
        elif L == "PAN":
            vals[cid] = f"ABCDE{i:04d}F"
        elif L == "GSTIN":
            vals[cid] = f"29ABCDE{i:04d}F1Z5"
        elif "PH" in L:
            vals[cid] = f"90000{i:05d}"
        elif "USER" in L:
            vals[cid] = f"user{i:04d}"
        elif "PASSWORD" in L:
            vals[cid] = f"pw#{i:04d}xY"
    ids.append(db.add_client(vals, "", random.sample(svc_ids, k=min(len(svc_ids), random.randint(0, 3)))))
print(f"seeded {N_CLIENTS} clients in {time.perf_counter()-a:.1f}s; columns={len(cols)} services={len(svcs)}", flush=True)
a = time.perf_counter()
gst = svcs.get("GST") or svc_ids[0]
for i in range(0 if SEEDED else N_DUMPS):
    c = i % N_CLIENTS
    try:
        db.insert_tracker_dump(client_id=ids[c], service_id=gst, portal="gst.gov.in",
                               period_label=f"{['Apr','May','Jun','Jul'][i%4]} 2026", arn_number=f"AA29{i:011d}",
                               capture_method="SGT", status="submitted", pan=f"ABCDE{c:04d}F",
                               filing_type=["GSTR1", "GSTR3B"][i % 2], raw_payload_json="{}")
    except Exception as e:
        print("dump seed failed:", repr(e)[:200]); break
print(f"seeded {N_DUMPS} dumps in {time.perf_counter()-a:.1f}s", flush=True)

# ---- build ----
from ui.utils.theme import get_theme_stylesheet
print(f"imports (Qt + database): {t_imp*1000:.0f} ms")
a = time.perf_counter(); qss = get_theme_stylesheet(db.get_setting("theme", "light")); t1 = time.perf_counter()
app.setStyleSheet(qss); t2 = time.perf_counter()
print(f"theme: build qss {(t1-a)*1000:.1f} ms ({len(qss)} chars), setStyleSheet {(t2-t1)*1000:.1f} ms")

a = time.perf_counter()
from ui.shell.app_shell import AppShell
from ui.windows.search_window import SearchWindow
from ui.windows.client_detail_window import ClientDetailWindow
from ui.windows.admin_window import AdminWindow
from ui.windows.tracker_dump_window import TrackerDumpWindow
print(f"import windows: {(time.perf_counter()-a)*1000:.0f} ms")

shell = timed("build AppShell", lambda: AppShell())
search = timed("build SearchWindow (fills grid)", lambda: SearchWindow(db))
detail = timed("build ClientDetailWindow", lambda: ClientDetailWindow(db, actor="Admin"))
admin = timed("build AdminWindow", lambda: AdminWindow(db, actor="Admin"))
tracker = timed("build TrackerDumpWindow (deferred)", lambda: TrackerDumpWindow(db, defer_first_load=True))
shell.add_page(search); shell.add_page(admin); shell.add_page(tracker)
shell.slide_panel.set_widget(detail, persistent=True)
shell.resize(1366, 768)
a = time.perf_counter(); shell.show(); pump(); shell.grab()
print(f"{'first show + paint of shell':<52} {(time.perf_counter()-a)*1000:8.1f} ms", flush=True)

# ---- interactions ----
print(f"search grid: {search.results_table.rowCount()} rows x {search.results_table.columnCount()} cols")
timed("search: full grid rebuild (_on_search_changed)", search._on_search_changed, 3, shell)
profiled("search rebuild", search._on_search_changed)


def type_filter(txt):
    search.search_box.setText(txt)
    search._search_timer.stop()
    search._on_search_changed()


timed("search: filter 'Client 01' (rebuild)", lambda: type_filter("Client 01"), 3, shell)
timed("search: clear filter (rebuild all)", lambda: type_filter(""), 3, shell)
timed("search: refresh() unchanged data", search.refresh, 3, shell)
timed("repaint All Clients page (grab)", lambda: None, 3, shell)

timed("detail: load_client + slide in", lambda: (detail.load_client(ids[3]), shell.slide_panel.slide_in()), 3, shell)
profiled("detail load", lambda: detail.load_client(ids[5]))
shell.slide_panel.slide_out(); pump()

timed("switch -> Manage Clients (admin.refresh + page)", lambda: (admin.refresh(), shell.set_current_page(1)), 3, shell)
profiled("admin refresh", admin.refresh)
timed("switch -> Tracker (load_data + page)", lambda: (shell.set_current_page(tracker), tracker.load_data()), 3, shell)
profiled("tracker load", tracker.load_data)
timed("tracker: load_data_if_stale (no change)", tracker.load_data_if_stale, 3, shell)
timed("switch -> All Clients (page + refresh)", lambda: (shell.set_current_page(0), search.refresh()), 3, shell)

# window resize = relayout + repaint
def resize_cycle():
    for w in (1280, 1100, 1366):
        shell.resize(w, 768); pump(2)
timed("resize window x3 (relayout + paint)", resize_cycle, 2, shell)

timed("sidebar toggle x2", lambda: (shell.toggle_sidebar(), pump(), shell.toggle_sidebar()), 2, shell)

# dialogs
from ui.dialogs.unified_settings_dialog import UnifiedSettingsDialog
dl = []
def open_settings():
    d = UnifiedSettingsDialog(db, actor="Admin", page="general", parent=shell); d.show(); pump(); d.grab(); dl.append(d)
timed("open Settings dialog", open_settings, 2)
profiled("settings open", open_settings)
for d in dl: d.close()
try:
    from ui.dialogs.audit_log_dialog import AuditLogDialog
    def open_audit():
        d = AuditLogDialog(db, actor="Admin", parent=shell); d.show(); pump(); d.grab(); dl.append(d)
    timed("open Audit log dialog", open_audit, 2)
except Exception as e:
    print("audit dialog failed", repr(e)[:200])
for d in dl: d.close()
print(f"widgets alive: {len(app.allWidgets())}")
os._exit(0)
