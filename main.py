"""
main.py
-------
App entry point for Project Sera.
"""

import sys
import os
import socket
import json
import queue
import threading
from pathlib import Path
from datetime import datetime

# Qt probes a couple of legacy Windows bitmap fonts during platform startup;
# suppress that harmless diagnostic while keeping other Qt warnings visible.
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.fonts=false")
# numpy's OpenBLAS reserves buffers for one thread per CPU core the moment numpy is imported
# (~225 MB of private commit on this PC, measured 2026-09-21). The app only uses numpy for
# small pixel checks, so one thread loses nothing. Must be set before anything imports numpy.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

from PySide6.QtWidgets import QApplication, QMessageBox, QDialog, QSizePolicy, QSystemTrayIcon, QMenu
from ui.shell.app_shell import AppShell
from PySide6.QtGui import QFont, QShortcut, QKeySequence, QIcon, QAction
from PySide6.QtCore import Qt, QObject, Signal, QTimer

try:
    import qtawesome as qta
except Exception:
    qta = None
# Only the Material Design icon font is used; loading qtawesome's other 11 costs ~15 MB.
from ui.utils.icon_fonts import restrict_icon_fonts
restrict_icon_fonts()
from core.memlog import mark as memory_mark

def _safe_qta_icon(icon_name, color=None):
    if qta is not None:
        try:
            if color:
                return qta.icon(icon_name, color=color)
            return qta.icon(icon_name)
        except Exception:
            pass
    return QIcon()

class SyncSignalBridge(QObject):
    sync_received_signal = Signal()
    live_sync_received_signal = Signal(str, str)
    sync_sent_signal = Signal(int, int)
    capture_processed_signal = Signal(dict, dict)
    update_found_signal = Signal(dict)
    update_ready_signal = Signal(str, dict)
    maintenance_done_signal = Signal()
    join_approval_signal = Signal(str, str, str, object)
    # SyncEngine's "synced" event (P3-5/P3-8): sorted list of tables an applied batch touched.
    engine_synced_signal = Signal(list)
    # A message from the sync engine's thread for the toast (text, level); stays until dismissed.
    engine_alert_signal = Signal(str, str)
    # An SCC save (core/scc/save.py) from SCC-U's thread: client id, client name, PAN, outcome.
    scc_saved_signal = Signal(int, str, str, str)

import security
from database import SeraDatabase
from ui.windows.search_window import SearchWindow
from ui.windows.client_detail_window import ClientDetailWindow
from ui.windows.admin_window import AdminWindow, AdminPinDialog, NewClientDialog
from ui.windows.tracker_dump_window import TrackerDumpWindow
from ui.utils.theme import get_theme_stylesheet
from ui.ws_bridge import WSBridge
from ui.components.vsdc_hud_pill import VSDCHudPill

APP_DIR = Path.home() / "AmanAssociates_Sera"


def _setup_sync_log(app_dir: Path) -> None:
    """Sera Sync v3 events (pairing, snapshot, transport, engine, discovery) to
    ``logs/sync.log`` (1 MB x 3). The installed app has no console, so a pairing that fails on
    the admin PC only shows "connection closed" on the joiner without this. The sync modules
    log ids, addresses and outcomes -- never keys, codes or row contents."""
    import logging
    from logging.handlers import RotatingFileHandler
    try:
        (app_dir / "logs").mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(app_dir / "logs" / "sync.log", maxBytes=1_000_000,
                                      backupCount=3, encoding="utf-8")
    except OSError as exc:
        print(f"[Sera Sync] could not open logs/sync.log: {exc}")
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    for name in ("sera", "sync_discovery"):
        lg = logging.getLogger(name)
        lg.setLevel(logging.INFO)
        lg.addHandler(handler)

def _copy_if_changed(src: Path, dst: Path) -> None:
    """copy2 keeps the modified time, so a same-size, same-time copy is already up to date -
    runs on every start, and re-copying unchanged files was ~0.1 s of it."""
    import shutil
    try:
        s, d = src.stat(), dst.stat()
        if s.st_size == d.st_size and int(s.st_mtime) == int(d.st_mtime):
            return
    except OSError:
        pass
    shutil.copy2(src, dst)


def ensure_permanent_extension() -> Path:
    import shutil
    try:
        if getattr(sys, 'frozen', False):
            source_ext = Path(sys._MEIPASS) / "sera_extension"
            source_ff = Path(sys._MEIPASS) / "sera_extension_firefox"
        else:
            source_ext = Path(__file__).resolve().parent / "sera_extension"
            source_ff = Path(__file__).resolve().parent / "sera_extension_firefox"

        target_ext = APP_DIR / "sera_extension"
        if source_ext.exists():
            target_ext.mkdir(parents=True, exist_ok=True)
            for item in source_ext.glob("**/*"):
                if item.is_file():
                    rel_path = item.relative_to(source_ext)
                    dest_file = target_ext / rel_path
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    _copy_if_changed(item, dest_file)

        target_ff = APP_DIR / "sera_extension_firefox"
        if source_ff.exists():
            target_ff.mkdir(parents=True, exist_ok=True)
            for item in source_ff.glob("**/*"):
                if item.is_file():
                    rel_path = item.relative_to(source_ff)
                    dest_file = target_ff / rel_path
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    _copy_if_changed(item, dest_file)

        return target_ext
    except Exception as e:
        print(f"Could not sync permanent extension folder: {e}")
        return APP_DIR / "sera_extension"

class SeraApp:
    # Table -> which windows to refresh when a live sync batch touches it (P3-8). Extend when
    # a window starts showing sync-relevant data from a table not listed here.
    _SYNC_TABLE_REFRESH = {
        "clients": ("dashboard_win", "search_win", "detail_win"),
        "client_values": ("dashboard_win", "search_win", "detail_win"),
        "mcl_columns": ("dashboard_win", "search_win"),
        "services": ("dashboard_win", "search_win", "detail_win"),
        "client_services": ("dashboard_win", "search_win", "detail_win"),
        "cell_formatting": ("dashboard_win",),
        "client_activity_stats": ("dashboard_win", "detail_win"),
        "client_recent_activity": ("dashboard_win", "detail_win"),
        "client_raw_containers": ("detail_win",),
        "client_container_notes": ("detail_win",),
        "sdc_session_timelines": ("detail_win",),
        "staff_users": ("admin_win",),
        "app_settings": ("admin_win",),
        "tracker_dump": ("tracker_dump_win",),
    }

    def __init__(self):
        # Keeps the extension folders on disk in a stable location (outside the temporary
        # PyInstaller extraction dir) so the browser has somewhere fixed to load them from.
        # Native messaging's registry registration used to happen here too; the bridge to the
        # extension is a WebSocket now (ui/ws_bridge.py) and needs no registration at all.
        self._permanent_extension_dir = ensure_permanent_extension()

        # Fix Windows Taskbar preview icon grouping & display
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AmanAssociates.ProjectSera.Vault.2.3.3")
            except Exception:
                pass

        # Enable High-DPI and smooth fractional scaling (125%, 150%, etc.)
        if hasattr(Qt, "HighDpiScaleFactorRoundingPolicy"):
            QApplication.setHighDpiScaleFactorRoundingPolicy(
                Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
            )

        memory_mark("start-up: code loaded")
        self.app = QApplication(sys.argv)
        # Avoid Windows legacy bitmap-font fallback warnings (8514oem/Fixedsys)
        # and keep all dialogs consistent with the app stylesheet.
        self.app.setFont(QFont("Segoe UI", 10))
        self.app.setQuitOnLastWindowClosed(False)
        APP_DIR.mkdir(parents=True, exist_ok=True)

        # Bind the extension bridge port as early as possible, before the DB unlock and services,
        # so an extension already waiting for the app connects within ~1 s of launch. Messages
        # that arrive before the handlers are connected are held (see WSBridge.mark_ready).
        from ui import ws_bridge as _ws_bridge_module
        self.bridge = WSBridge(self.app)
        self.app.aboutToQuit.connect(self.bridge.stop)
        self.bridge.start(chrome_manifest_path=self._permanent_extension_dir / "manifest.json")
        _ws_bridge_module.set_active_bridge(self.bridge)

        self.sync_bridge = SyncSignalBridge()
        self.sync_bridge.sync_received_signal.connect(self._lock_and_force_restart)
        self.sync_bridge.live_sync_received_signal.connect(self._handle_live_sync_received_main_thread)
        self.sync_bridge.sync_sent_signal.connect(self._handle_sync_sent_main_thread)
        self.sync_bridge.capture_processed_signal.connect(self._on_capture_processed_ui)
        self.sync_bridge.update_found_signal.connect(self._handle_update_found)
        self.sync_bridge.update_ready_signal.connect(self._handle_update_ready)
        self.sync_bridge.maintenance_done_signal.connect(self._on_startup_maintenance_done)
        self.sync_bridge.join_approval_signal.connect(self._handle_join_approval_modal_main_thread)
        self.sync_bridge.engine_synced_signal.connect(self._handle_engine_synced_main_thread)
        self.sync_bridge.scc_saved_signal.connect(self._after_scc_save)
        self.sync_bridge.engine_alert_signal.connect(
            lambda text, level: getattr(self, "shell", None) and self.shell.show_alert(text, level=level, duration=0))
        self._synced_tables_lock = threading.Lock()
        self._synced_tables_pending = set()
        self._synced_tables_last_emit = 0.0
        self._synced_tables_flush_timer = None
        self.app.aboutToQuit.connect(self._cancel_synced_tables_timer)
        self._pending_update_installer = None
        self._pending_update_info = None
        self._update_applied = False
        self._capture_ui_refresh_pending = False
        self._capture_queue = queue.Queue()
        self._capture_worker = threading.Thread(target=self._capture_worker_loop, daemon=True)
        self._capture_worker.start()
        
        base_dir = Path(__file__).resolve().parent
        icon_path = base_dir / "assets" / "logo" / "icon_here.ico"
        if not icon_path.exists():
            icon_path = base_dir / "assets" / "logo" / "icon_here.png"
        if not icon_path.exists():
            icon_path = base_dir / "assets" / "logo" / "sera_icon.ico"
        if not icon_path.exists():
            icon_path = base_dir / "assets" / "logo" / "sera_icon.png"
        if not icon_path.exists():
            icon_path = APP_DIR / "assets" / "logo" / "icon_here.ico"

        if icon_path.exists():
            self.app_icon = QIcon(str(icon_path))
            self.app.setWindowIcon(self.app_icon)
        else:
            self.app_icon = None

        # Apply any pending staged sync database swap before opening database
        self._pending_swap_error = None
        from sync_peer import apply_pending_swap
        try:
            apply_pending_swap(APP_DIR)
        except Exception as e:
            self._pending_swap_error = str(e)
            print(f"[SeraApp] Failed to apply pending sync swap: {e}")

        self.db_path = str(APP_DIR / "master.db")
        self.salt_path = str(APP_DIR / security.SALT_FILE)
        self.identity_path = APP_DIR / "device_identity.txt"
        self.app_dir = APP_DIR

        self._run_pending_rejoin()
        self._run_pending_shadow_start()
        self._go_live_alert = None
        self._run_pending_go_live()
        self._restore_alert = None
        self._run_pending_restore()
        self._catch_up_alert = None
        self._run_pending_catch_up()
        self.key_mode, self.key_id, hex_key = self._resolve_encryption_key()

        # In office mode, check whether master.db exists; prevent silent empty DB initialization (P1-6)
        if self.key_mode == "office" and not os.path.exists(self.db_path):
            import sync_snapshot
            if sync_snapshot.has_pending_join(self.app_dir):
                from PySide6.QtWidgets import QMessageBox
                reply = QMessageBox.question(
                    None,
                    "Aman Associates — Resume Office Join",
                    "An office join was in progress but interrupted before the database snapshot could be downloaded.\n\n"
                    "Would you like to connect to the admin PC now and complete the join?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes,
                )
                if reply == QMessageBox.Yes:
                    try:
                        sync_snapshot.resume_join_snapshot(self.app_dir)
                    except Exception as exc:
                        QMessageBox.critical(
                            None,
                            "Office Join Failed",
                            f"Could not complete office join: {exc}\n\n"
                            "Please check that the admin PC is online and running Sera, then restart the application to retry.",
                        )
                        sys.exit(1)
                else:
                    sys.exit(0)
            if not os.path.exists(self.db_path):
                self._handle_missing_office_db(self.app_dir, self.db_path, hex_key)

        from ui.dialogs.loading_dialog import StartupLoadingDialog
        loading_dlg = StartupLoadingDialog()
        
        # Exception hook to close loading modal on error
        orig_excepthook = sys.excepthook
        def _safe_excepthook(exctype, value, tb):
            try:
                loading_dlg.close()
            except Exception:
                pass
            orig_excepthook(exctype, value, tb)
        sys.excepthook = _safe_excepthook

        loading_dlg.show()
        self.app.processEvents()
        memory_mark("  vault: loading dialog shown")

        try:
            if self.key_mode == "office":
                loading_dlg.set_status("Unlocking vault with office data key...")
            else:
                loading_dlg.set_status("Decrypting vault & deriving PBKDF2 key...")
            
            loading_dlg.set_status("Connecting to SQLCipher database & resolving service selectors...")
            self.db = SeraDatabase(self.db_path, hex_key, defer_startup_maintenance=True, key_mode=self.key_mode)
            memory_mark("  vault: database opened")
            # Sera Sync v3 (P3-3): seals captured changes every 5 s, incl. writes by the parsers.
            # A no-op while the sync mode is 'off'.
            self.db.start_seal_timer()
            self.app.aboutToQuit.connect(self.db.stop_seal_timer)

            # Sera Sync v3 (P4-3a): scheduled daily backup at first idle after 13:00 (keep 14)
            # and retire legacy pre-sync backups
            from sync_backup import DailyBackupScheduler, retire_pre_sync_backups
            retire_pre_sync_backups(self.app_dir)
            self._backup_scheduler = DailyBackupScheduler(
                self.app_dir,
                db=self.db,
                is_idle_callback=lambda: not self._window_in_use(),
            )
            self._backup_scheduler.start()
            self.app.aboutToQuit.connect(self._backup_scheduler.stop)

            # Ensure SCA, SCC, and VSDC settings are initialized
            if self.db.get_setting("sca_enabled") is None:
                self.db.set_setting("sca_enabled", "1")
            if self.db.get_setting("scc_enabled") is None:
                self.db.set_setting("scc_enabled", "1")
            if self.db.get_setting("vsdc_enabled") is None:
                self.db.set_setting("vsdc_enabled", "1")
            self.scc_banner = None

            # Initialize Sera Clipboard Assist (SCA) immediately so cold-boot copies are armed
            from clipboard_watch import ClipboardWatchService
            self.clipboard_watcher = ClipboardWatchService(self.db, self.app)
            self.clipboard_watcher.sca_armed.connect(self._on_sca_armed)
            # Why a fill did not happen (e.g. an Income Tax password SCC has not verified yet)
            self.clipboard_watcher.sca_notice.connect(
                lambda text, level: getattr(self, "shell", None) and self.shell.show_alert(text, level=level, duration=6000))
            sca_active = (self.db.get_setting("sca_enabled", "1") == "1")
            self.clipboard_watcher.set_enabled(sca_active)
            memory_mark("start-up: vault unlocked")
        except Exception as e:
            loading_dlg.close()
            QMessageBox.critical(None, "Database Error", str(e))
            sys.exit(1)

        loading_dlg.set_status("Verifying staff identity & pre-loading workspace...")
        loading_dlg.hide()
        self.actor, self.actor_alias = self._ensure_user_identity()
        loading_dlg.show()

        # Start Sera Sync LAN peer service
        loading_dlg.set_status("Starting Sera Sync LAN discovery...")
        from sync_peer import SyncPeerService
        self.sync_service = SyncPeerService(
            db_path=self.db_path,
            salt_path=self.salt_path,
            username=self.actor_alias,
            db=self.db,
            hex_key=hex_key,
            key_id=self.key_id,
            on_sync_received=self._on_sync_received,
            on_live_sync_received=self._on_live_sync_received,
            on_error=lambda msg: print(f"[Sera Sync] {msg}"),
            on_join_approval_requested=self._on_join_approval_requested,
        )
        self.sync_service.start()

        # Sera Sync v3 LAN discovery (P2-5/P2-7), office mode only. Shares the beacon socket
        # with the legacy (v2) listener above via on_v3_beacon (P2-5's design) instead of
        # binding port 49156 a second time. Feeds Members / "Add workstation" in the Sera
        # Sync panel (P2-7); Phase 3's session protocol (P3-5) will use it too.
        self.discovery_service = None
        if self.key_mode == "office":
            try:
                import sync_discovery
                import sync_identity
                import socket
                import sync_office
                # Creates the identity (and, on the admin PC, the first member records) if a
                # "Convert to office key" PC doesn't have them yet; loads them otherwise.
                with self.db._connect() as _conn:
                    own_identity = sync_office.ensure_office_identity(
                        self.app_dir, _conn, (self.actor_alias or "").strip() or socket.gethostname())
                if own_identity is not None:
                    def _open_v3_discovery_db(_db_path=self.db_path, _hex_key=hex_key):
                        # DiscoveryService closes what open_db() returns itself; SeraDatabase's
                        # own _connect() is a context manager, not a plain connection, so a
                        # separate one is opened here (same PRAGMAs main.py already uses to
                        # open master.db elsewhere).
                        import sqlcipher3.dbapi2 as _sqlite3
                        _conn = _sqlite3.connect(_db_path)
                        _conn.execute("PRAGMA key = \"x'%s'\";" % _hex_key)
                        return _conn
                    self.discovery_service = sync_discovery.DiscoveryService(
                        open_db=_open_v3_discovery_db,
                        office_tag=sync_discovery.compute_office_tag(bytes.fromhex(hex_key)),
                        device_id=own_identity.device_id,
                        device_name=self.actor_alias,
                        listen=False,
                    )
                    self.sync_service.on_v3_beacon = self.discovery_service.handle_datagram
                    self.discovery_service.start()
            except Exception as exc:
                print(f"[Sera Sync] v3 discovery service failed to start: {exc}")

        # Sera Sync v3 session engine (P3-5) + shadow mode (P3-7), office mode only. The engine
        # runs in every sync mode: idle in "off" (still exchanges HELLO/membership so P2-2
        # records spread before Phase 3 goes live); in "shadow" remote changes go to
        # sync_shadow's replica, never the live DBs (local changes are mirrored there too, via
        # the seal listener below). One permanent server on port 49159 serves both this and
        # snapshot requests ("Add workstation", P2-6) -- sync_office.dispatch_session routes by
        # the first frame so neither handler needs to change (P3-5 note: "Port clash").
        self.sync_engine = None
        self.sync_transport = None
        self._sync_engine_server = None
        if self.key_mode == "office" and self.discovery_service is not None and own_identity is not None:
            try:
                import sera_keys
                import sync_engine
                import sync_office
                import sync_shadow
                from sync_transport import MemberSet, SyncTransport

                office = sera_keys.load_office(self.app_dir)
                own_cert_pem = own_identity.cert_pem.decode("ascii")
                with self.db._connect() as conn:
                    members = MemberSet.from_db(conn, office.admin_pubkey, own_cert_pem)
                cert_chain = sync_identity.load_cert_chain_args(self.app_dir)
                self.sync_transport = SyncTransport(cert_chain, members)

                self.sync_engine = sync_engine.SyncEngine(
                    self.db, self.sync_transport,
                    app_dir=self.app_dir,
                    admin_pubkey=office.admin_pubkey,
                    beacon_sighting=self.discovery_service.get_beacon_sighting,
                    on_event=self.on_sync_engine_event,
                    shadow_apply=sync_shadow.make_shadow_apply(self.db, self.app_dir),
                    own_cert_pem=own_cert_pem,
                )
                self._sync_engine_server = self.sync_transport.serve(
                    lambda session: sync_office.dispatch_session(session, self.app_dir, self.sync_engine),
                    host="0.0.0.0",
                )
                self.discovery_service.on_poke = self.sync_engine.handle_poke

                def _sync_seal_listener(results, _db=self.db, _app_dir=self.app_dir, _engine=self.sync_engine):
                    # Own edits go to the shadow replica too (P3-7); a no-op outside mode shadow.
                    sync_shadow.mirror_own_changes_to_replica(_db, _app_dir)
                    _engine.notify_local_change()

                def _seal_timing_cb(dur_ms, _db=self.db, _dir=self.app_dir):
                    if _db.get_sync_mode() == "shadow":
                        sync_shadow.record_seal_timing(dur_ms, _dir)

                self.db.set_seal_listener(_sync_seal_listener)
                if self.db.get_sync_mode() == "shadow":
                    # Own changes sealed before the listener existed (e.g. a P3-7b salvage
                    # import at start-up) reach the replica now, not only at the next edit.
                    threading.Thread(
                        target=sync_shadow.mirror_own_changes_to_replica, args=(self.db, self.app_dir),
                        name="shadow-mirror-catch-up", daemon=True).start()
                self.db.set_seal_timing_callback(_seal_timing_cb)
                self.sync_engine.start()
                self.app.aboutToQuit.connect(self.sync_engine.stop)
                self.app.aboutToQuit.connect(self._sync_engine_server.stop)
                self.app.aboutToQuit.connect(lambda _dir=self.app_dir: sync_shadow.flush_seal_timing(_dir))
            except Exception as exc:
                print(f"[Sera Sync] v3 sync engine failed to start: {exc}")
                self.sync_engine = None
                self.sync_transport = None

        # Periodic shadow-mode checks (P3-8): sync_shadow.run_shadow_checks's own docstring
        # says it's "called on demand (panel) and from a periodic timer once P3-8 wires one
        # in" -- this is that timer. The panel's "Run now" button (sera_sync_dialog.py) calls
        # the same function directly instead of going through this, since the dialog only
        # holds the SyncEngine, not this SeraApp. Peer-digest collection for the convergence
        # check isn't done (no wire protocol exists for it yet) -- only the capture check runs.
        self._shadow_check_timer = None
        if self.sync_engine is not None:
            def _run_shadow_check_bg(_db=self.db, _app_dir=self.app_dir):
                if _db.get_sync_mode() != "shadow":
                    return
                import sync_shadow
                try:
                    sync_shadow.run_shadow_checks(_db, _app_dir)
                except Exception as exc:
                    print(f"[Shadow Mode] periodic check failed: {exc}")

            def _run_shadow_check_async():
                threading.Thread(target=_run_shadow_check_bg, name="shadow-check", daemon=True).start()

            self._shadow_check_timer = QTimer(self.app)
            self._shadow_check_timer.timeout.connect(_run_shadow_check_async)
            self._shadow_check_timer.start(30 * 60 * 1000)
            _run_shadow_check_async()

        # Asynchronous background auto-updater (non-blocking, silent)
        loading_dlg.set_status("Initializing Background Auto-Updater...")
        import version
        self.update_manager = version.BackgroundUpdateManager(
            check_interval_seconds=7200,
            on_update_found=lambda info: self.sync_bridge.update_found_signal.emit(info),
            on_update_ready=lambda path, info: self.sync_bridge.update_ready_signal.emit(str(path), info),
            on_error=lambda err: print(f"[AutoUpdater] {err}")
        )
        self.update_manager.start(initial_delay_seconds=3)
        memory_mark("start-up: sync + updater started")

        loading_dlg.set_status("Initializing User Interface...")
        self._build_ui()
        loading_dlg.close()
        memory_mark("start-up: main window built")

        # Start-up alerts share one toast widget, so collect them and show them together:
        # otherwise a later alert would replace the persistent tracker-reset one.
        startup_alerts = []  # (level, message, duration_ms; 0 = stays until dismissed)
        if self.db.raw_db_was_reset:
            if self.db.raw_db_was_reset == "reset_without_backup":
                msg = "Tracker database could not be opened with this office's key and was reset (no backup created)."
            else:
                msg = ("Tracker database could not be opened with this office's key and was reset. "
                       f"Backup: {os.path.basename(self.db.raw_db_was_reset)}")
            startup_alerts.append(("warning", msg, 0))

        # Show alert if a staged sync database swap failed during startup
        failed_swap_marker = APP_DIR / "incoming" / "pending_swap.json.failed"
        if self._pending_swap_error:
            startup_alerts.append((
                "error",
                f"Sync Database Swap Failed: {self._pending_swap_error}. Rolled back to previous database.",
                10000,
            ))
        elif failed_swap_marker.exists():
            startup_alerts.append((
                "warning",
                "A pending sync database swap could not be applied. Rolled back to previous database.",
                8000,
            ))
            try:
                failed_swap_marker.unlink(missing_ok=True)
            except OSError:
                pass

        for attr in ("_go_live_alert", "_restore_alert", "_catch_up_alert"):
            if getattr(self, attr, None):
                startup_alerts.append(getattr(self, attr))

        if startup_alerts:
            level = "error" if any(a[0] == "error" for a in startup_alerts) else "warning"
            duration = 0 if any(a[2] == 0 for a in startup_alerts) else max(a[2] for a in startup_alerts)
            self.shell.show_alert("\n".join(a[1] for a in startup_alerts), level=level, duration=duration)

        # Start the WebSocket bridge to the extension (ui/ws_bridge.py). Only the Sera extension's
        # own background page may connect - see that module for how the origin is checked.
        # (The bridge itself was created and bound early in __init__; only the handlers connect here.)
        self.bridge.filing_result_received.connect(self._handle_extension_result)
        self.bridge.scc_row_worked_received.connect(self._handle_scc_row_worked)
        self.bridge.scc_card_closed_received.connect(self._handle_scc_card_closed)
        self.bridge.scc_row_none_received.connect(self._handle_scc_row_none)
        self.bridge.extension_settings_updated_received.connect(self._handle_extension_settings_updated)
        self.bridge.settings_provider = self._get_extension_settings_payload
        self.bridge.sca_password_requested.connect(self._handle_sca_password_request)
        self.bridge.sca_fill_result_received.connect(self.clipboard_watcher.handle_fill_result)
        self.app.aboutToQuit.connect(self._on_app_about_to_quit)
        # Handlers are wired: release any messages the extension sent while start-up was running.
        self.bridge.mark_ready()

        # The capture engines start on the first turn of the event loop, i.e. right after the
        # window has painted: loading them (OCR, numpy, UI Automation) is ~0.5 s the window used
        # to wait for (measured 2026-09-22). Every use of vsdc_worker / vsdc_hud is guarded.
        self.app.processEvents()                      # paint the window now
        memory_mark("start-up: window painted")
        QTimer.singleShot(0, self._start_capture_engines)

        # Memory over the working day (~/AmanAssociates_Sera/logs/memory.log): once a minute after
        # start-up, then every 10 minutes - steady growth here is a leak, a jump is a feature
        # loading something heavy.
        # A minute in, start-up is over: hand back what it touched once and will not use again
        # (~40% of the working set, measured). Later samples trim only above 120 MB.
        # Trims happen only while the window is NOT in use (minimised, hidden to the tray): a trim
        # empties the working set and the next click has to page it all back in, which was felt
        # as a slow search grid (2026-09-22). If the window is open a minute in, the start-up
        # trim waits for the next minimise / hide.
        from core.memlog import sample_and_maybe_trim
        QTimer.singleShot(60_000, lambda: (memory_mark("running: 1 min after start-up"),
                                           self._trim_if_idle("start-up finished")))
        self._memory_timer = QTimer(self.app)
        self._memory_timer.timeout.connect(
            lambda: sample_and_maybe_trim("running: periodic sample", may_trim=lambda: not self._window_in_use()))
        self._memory_timer.start(10 * 60_000)
        # A frozen window ("Not responding") writes every thread's stack to logs/hang.log.
        from core import hang_watchdog
        hang_watchdog.start(self.app)

        # Heavy historical repair/report work is intentionally deferred until
        # after the main window and extension listener are available.
        threading.Thread(
            target=self._run_deferred_startup_maintenance,
            name="sera-startup-maintenance",
            daemon=True,
        ).start()

    def _start_capture_engines(self):
        """VSDC / VSDC-X / VSDC 24/7 / SGT worker and the HUD pill - started just after the window
        first paints (see __init__)."""
        try:
            if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
                if sys._MEIPASS not in sys.path:
                    sys.path.insert(0, sys._MEIPASS)

            from core.vsdc import VSDCWorker
            self.vsdc_hud = VSDCHudPill()
            self.vsdc_worker = VSDCWorker(parent=self.app)
            self.vsdc_worker.filing_captured.connect(self._handle_extension_result)
            self.vsdc_worker.activity_event.connect(self._on_vsdc_activity_event)
            self.app.aboutToQuit.connect(self.vsdc_worker.stop)
            # After stop() (slots run in connection order): the captures stop() just handed over
            # - SGT rows completed at quit - are saved before the process exits.
            self.app.aboutToQuit.connect(self._flush_capture_queue_on_quit)
            # Settings -> Tracker decides which of VSDC / VSDC-X / VSDC 24/7 run; the worker
            # itself only starts when at least one of them is on.
            self.vsdc_worker.router.set_scc_handlers(self._make_scc_handlers)
            from core.scc import manual as scc_manual
            scc_manual.set_opener(self._open_scc_manual)
            self._apply_vsdc_engine_settings()
            memory_mark("start-up: capture engines ready")
        except Exception as vsdc_exc:
            import traceback
            err_msg = traceback.format_exc()
            print(f"⚠️ [main] VSDC Worker initialization warning: {vsdc_exc}\n{err_msg}")
            try:
                log_dir = os.path.join(os.path.expanduser("~"), "AmanAssociates_Sera")
                os.makedirs(log_dir, exist_ok=True)
                with open(os.path.join(log_dir, "vsdc_startup_error.log"), "a", encoding="utf-8") as f:
                    f.write(f"[{datetime.now()}] {err_msg}\n")
            except Exception:
                pass

    def _run_deferred_startup_maintenance(self):
        try:
            self.db.run_startup_maintenance()
            print("[Startup] Deferred maintenance completed")
            memory_mark("start-up: deferred maintenance done")
            self.sync_bridge.maintenance_done_signal.emit()
        except Exception as exc:
            # Startup maintenance is best-effort; the already-open app remains
            # usable and the error is visible in the diagnostic console.
            print(f"[Startup] Deferred maintenance failed: {exc}")
        self._sync_extension_settings()

    def _on_startup_maintenance_done(self):
        """The background maintenance re-links tracker rows to clients and tidies names, after the
        window is already showing - redraw the views that show them (main thread)."""
        for name in ("tracker_dump_win", "search_win"):
            win = getattr(self, name, None)
            try:
                if win is None:
                    continue
                if name == "tracker_dump_win":
                    if not getattr(win, "_first_load_pending", False):   # not filled yet: fills when opened
                        self._refresh_when_seen(name, win.load_data)
                else:
                    # redraw only - refresh() would clear the search box
                    self._refresh_when_seen(name, win._on_search_changed)
            except Exception as exc:
                print(f"[Startup] Refresh after maintenance failed ({name}): {exc}")

    def _handle_extension_settings_updated(self, msg: dict):
        """Persists extension settings toggled from browser popup into SQLite database."""
        try:
            to_set = {}
            if "sca_enabled" in msg:
                to_set["sca_enabled"] = "1" if msg["sca_enabled"] else "0"
            if to_set:
                self.db.set_settings_bulk(to_set)
                print(f"[main] Persisted updated extension settings from popup: {to_set}")
        except Exception as e:
            print(f"[main] Error handling extension_settings_updated: {e}")

    def _get_extension_settings_payload(self) -> dict:
        """Packages current services and settings for the extension."""
        try:
            sca_en = self.db.get_setting("sca_enabled", "1") in ("1", "true", "True")
            sca_mode = self.db.get_setting("sca_action_mode", "autofill")
            try:
                sca_max = int(self.db.get_setting("sca_max_uses", "1"))
            except (ValueError, TypeError):
                sca_max = 1
            svcs = self.db.get_services()
            try:
                clip_secs = max(5, min(int(self.db.get_setting("clipboard_clear_seconds", "30")), 300))
            except (ValueError, TypeError):
                clip_secs = 30
            from automation import allowed_portal_domains
            return {
                "status": "ok",
                "allowed_domains": allowed_portal_domains(svcs),
                "clipboard_clear_seconds": clip_secs,
                "sca_enabled": sca_en,
                "sca_mode": sca_mode,
                "sca_max_uses": sca_max,
                "allowed_services": svcs,
            }
        except Exception as e:
            print(f"[main] Failed to get extension settings payload: {e}")
            return {"status": "error", "message": str(e)}

    def _sync_extension_settings(self):
        """Pushes current services and settings to the extension."""
        try:
            from automation import update_extension_settings
            payload = self._get_extension_settings_payload()
            if payload.get("status") == "ok":
                update_extension_settings(
                    sca_enabled=payload.get("sca_enabled", True),
                    sca_mode=payload.get("sca_mode", "autofill"),
                    allowed_services=payload.get("allowed_services", []),
                    sca_max_uses=payload.get("sca_max_uses", 1),
                    clipboard_clear_seconds=payload.get("clipboard_clear_seconds"),
                )
        except Exception as e:
            print(f"[main] Failed to sync extension settings: {e}")

    def _handle_sca_password_request(self, msg: dict):
        """SCA v2: the extension asks for one portal password after the client's id was entered
        on that portal. clipboard_watch decides; the answer goes back only on the socket that
        asked (WSBridge.reply), never to every connected browser."""
        reply = self.clipboard_watcher.handle_password_request(msg)
        self.bridge.reply(msg, reply)

    def _on_sca_armed(self, client_id: int, client_token: str, services: list):
        try:
            self.db.record_client_activity(client_id, "SCA", f"Armed {len(services)} portal(s)")
            if hasattr(self, "search_win"):
                self.search_win._on_search_changed()
        except Exception:
            pass

    def _capture_worker_loop(self):
        """Serially process incoming captures without blocking the Qt thread."""
        while True:
            msg = self._capture_queue.get()
            try:
                result = self._process_extension_result(msg)
                if result:
                    self.sync_bridge.capture_processed_signal.emit(msg, result)
            except Exception as e:
                print(f"[Capture Worker Error] {e}")
            finally:
                self._capture_queue.task_done()

    def _on_vsdc_activity_event(self, event_type: str, title: str, subtitle: str = "", context=None):
        """Displays real-time bottom-left HUD overlay (no system tray, disappears within 3s)."""
        if hasattr(self, "vsdc_hud") and self.vsdc_hud:
            self.vsdc_hud.show_event(event_type, title, subtitle, duration_ms=2500, context=context)

    def _on_capture_processed_ui(self, msg: dict, res: dict):
        """Apply only UI updates on the Qt main thread after background work."""
        if not self._capture_ui_refresh_pending:
            self._capture_ui_refresh_pending = True
            QTimer.singleShot(50, self._refresh_tracker_dump_ui)
        portal = msg.get("portal", "Portal")
        arn = msg.get("arn", "N/A")
        capture_method = msg.get("capture_method", "DOM_Tracker")
        # SGT rows are shown on the HUD pill as SGT sees them (tagged "SGT (Live)" / "SGT (Shadow)");
        # SGT re-sends a row every time its dataset changes, so a toast / tray balloon per row
        # would only double the noise.
        if str(capture_method).startswith("SGT"):
            return
        is_vsdc = "VSDC" in capture_method
        if is_vsdc:
            method_label = "Sera VSDC (Visual Harvester)"
        elif capture_method in ("SAD_API_Interceptor", "SAD_API_Detector"):
            # Historical label only - that capture mechanism was retired and
            # fully removed from the extension; no longer produces new rows.
            method_label = "Legacy Capture (Retired)"
        else:
            method_label = "Sera SDC (DOM Crosshair)"
        client_display = ""
        if res.get("client_id"):
            try:
                c_full = self.db.get_client(res["client_id"])
                c_name = ""
                if c_full:
                    id_col = self.db.get_identity_column()
                    c_name = c_full.get("values", {}).get(id_col["id"] if id_col else 1, "")
                fallback_name = f"CLI-{int(res['client_id']):05d}"
                client_display = f"Client: {c_name or fallback_name} | "
            except Exception:
                client_display = f"Client #{res.get('client_id')} | "
        elif res.get("unassigned_identity"):
            client_display = f"Unregistered ({res['unassigned_identity']}) | "

        # Real-time bottom-left HUD indicator for VSDC (disappears within 3s, no system tray)
        if is_vsdc and hasattr(self, "vsdc_hud") and self.vsdc_hud:
            self.vsdc_hud.show_event(
                "capture",
                f"Captured {portal} Filing",
                f"{client_display}ARN: {arn}",
                duration_ms=2600,
            )

        toast_msg = f"Captured {portal} Filing ({method_label}) — {client_display}ARN: {arn}"
        if hasattr(self, "shell") and self.shell and self.shell.isVisible() and not self.shell.isMinimized():
            self.shell.show_toast(toast_msg, duration=3000)
        elif not is_vsdc and hasattr(self, "tray_icon") and self.tray_icon and self.tray_icon.isVisible():
            self.tray_icon.showMessage(f"Filing Captured — {portal}", f"{client_display}ARN: {arn} ({method_label})", QSystemTrayIcon.Information, 4500)

    def _refresh_tracker_dump_ui(self):
        self._capture_ui_refresh_pending = False
        if hasattr(self, "tracker_dump_win") and self.tracker_dump_win:
            self._refresh_when_seen("tracker_dump_win", self.tracker_dump_win.load_data)

    def _handle_extension_result(self, msg: dict):
        # Queue all captures (SGT/VSDC, via vsdc_worker.filing_captured); the database/report
        # pipeline is too expensive to execute in the Qt signal handler during burst traffic.
        self._capture_queue.put(msg)

    def _process_extension_result(self, msg: dict):
        print(f"[main._handle_extension_result] Processing incoming message: {msg}")
        # Every stored capture says which PC produced it (the name from this PC's
        # device_identity.txt), inside its own payload.
        try:
            from core.vsdc.vsdc_alerts import stamp_device_name
            stamp_device_name(msg)
        except Exception:
            pass

        # Enrich identity and return details from DOM scraped_data if available
        scraped = msg.get("scraped_data") or (msg.get("raw_payload", {}).get("scraped_data") if isinstance(msg.get("raw_payload"), dict) else None)
        if scraped and isinstance(scraped, dict):
            summary = scraped.get("summary_labels") or {}
            form_fields = scraped.get("form_fields") or {}
            
            # 1. Parse Legal Name / Assessee Name from Form Fields (FirstName + MiddleName + SurName)
            f_name = ""
            m_name = ""
            l_name = ""
            for k, v in form_fields.items():
                k_lower = k.lower()
                val = str(v).strip()
                if not val or len(val) > 60:
                    continue
                if "firstname" in k_lower or "first_name" in k_lower:
                    f_name = val
                elif "middlename" in k_lower or "middle_name" in k_lower:
                    m_name = val
                elif "surname" in k_lower or "lastname" in k_lower or "last_name" in k_lower or "orgname" in k_lower:
                    l_name = val

            assembled_form_name = " ".join(part for part in [f_name, m_name, l_name] if part).strip() if (f_name or l_name) else ""

            raw_p = msg.get("raw_payload") if isinstance(msg.get("raw_payload"), dict) else {}
            legal_name = (
                assembled_form_name 
                or summary.get("Legal Name") 
                or summary.get("Trade Name")
                or msg.get("client_name") 
                or msg.get("taxpayer_name") 
                or msg.get("name")
                or raw_p.get("client_name")
                or raw_p.get("taxpayer_name")
            )
            if not legal_name and scraped.get("header_badges"):
                legal_name = str(scraped["header_badges"][0]).split("(")[0].strip()
            
            if not legal_name:
                for t in scraped.get("title_attributes") or []:
                    cand = (t.get("title") or "").strip()
                    if cand and len(cand) > 3 and not re.match(r"^(?:[A-Z]?\d{1,3}\.|\d+[\.\s-])", cand) and not any(j in cand.lower() for j in ["http", "login", "logout", "portal", "profile", "first name", "last name"]):
                        legal_name = cand
                        break
            
            if legal_name and str(legal_name).strip() and not any(part in str(legal_name).lower() for part in ("first name", "last name", "general information")):
                clean_name = str(legal_name).strip()
                msg["client_name"] = clean_name
                msg["name"] = clean_name
                msg["taxpayer_name"] = clean_name
                if isinstance(msg.get("raw_payload"), dict):
                    msg["raw_payload"]["client_name"] = clean_name
                    msg["raw_payload"]["taxpayer_name"] = clean_name

            # 2. Parse GSTIN and derive PAN locally
            gstin = summary.get("GSTIN")
            if not gstin:
                for nb in scraped.get("ng_binds") or []:
                    if "gstin" in str(nb.get("expr", "")).lower() and nb.get("value"):
                        gstin = str(nb["value"]).strip().upper()
                        break
            if gstin and re.match(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$", gstin):
                derived_pan = gstin[2:12]
                if not msg.get("pan"):
                    msg["pan"] = derived_pan

            # 3. Parse Period Label
            if not msg.get("period_label"):
                fy = summary.get("FY")
                tax_p = summary.get("Tax Period") or summary.get("Return Period")
                if tax_p and fy:
                    msg["period_label"] = f"{tax_p} (FY {fy})"
                elif fy:
                    msg["period_label"] = f"FY {fy}"

            # 4. Parse Status
            status_val = summary.get("Status")
            if status_val and not msg.get("status"):
                msg["status"] = status_val

        raw_client_id = msg.get('client_id')
        pan = str(msg.get('pan') or "").strip()
        client_name = str(msg.get('client_name') or msg.get('name') or "").strip()

        # If PAN is empty but client_name was visually captured, resolve identity via master.db
        if not pan and client_name and len(client_name) >= 3:
            try:
                with self.db._connect() as m_conn:
                    row = m_conn.execute(
                        """SELECT c.id, UPPER(TRIM(cv_pan.value))
                           FROM clients c
                           JOIN client_values cv_name ON cv_name.client_id = c.id
                           JOIN mcl_columns mc_name ON mc_name.id = cv_name.column_id
                           LEFT JOIN client_values cv_pan ON cv_pan.client_id = c.id
                           LEFT JOIN mcl_columns mc_pan ON mc_pan.id = cv_pan.column_id
                                AND (LOWER(mc_pan.label) LIKE '%pan%' OR LOWER(mc_pan.label) LIKE '%gstin%')
                           WHERE c.is_archived = 0
                             AND (mc_name.is_identity = 1 OR LOWER(mc_name.label) LIKE '%name%')
                             AND UPPER(TRIM(cv_name.value)) = ?
                           LIMIT 1""",
                        (client_name.upper(),)
                    ).fetchone()
                    if row:
                        raw_client_id = row[0]
                        if row[1]:
                            pan = row[1]
                            msg["pan"] = pan
                            if hasattr(self, "vsdc_worker") and self.vsdc_worker:
                                # Scoped to whichever window is actually foreground right
                                # now (per-window session isolation), not a single global
                                # assembler - see VSDCRouter.seed_identity_for_foreground.
                                self.vsdc_worker.router.seed_identity_for_foreground(pan=pan, name=client_name)
                            print(f"[main] Resolved client #{raw_client_id} and PAN {pan} from visual name '{client_name}'")
            except Exception as e:
                print(f"[main] Warning: Failed to resolve PAN from name '{client_name}': {e}")
        
        # Cross-client guard: Only trust raw_client_id if it matches the parsed PAN in master.db
        if raw_client_id and pan:
            try:
                with self.db._connect() as m_conn:
                    cur = m_conn.execute(
                        """SELECT cv.client_id FROM client_values cv
                           JOIN clients c ON c.id = cv.client_id
                           JOIN mcl_columns mc ON mc.id = cv.column_id
                           WHERE c.id = ? AND c.is_archived = 0 AND UPPER(TRIM(cv.value)) = ?
                           LIMIT 1""",
                        (raw_client_id, pan.upper())
                    )
                    if not cur.fetchone():
                        # Discard mismatched client_id from stale extension storage
                        raw_client_id = None
            except Exception:
                pass

        arn = msg.get('arn', 'N/A')
        portal = msg.get('portal', 'Portal')
        filing_type = str(msg.get('filing_type') or "").strip()
        # Used as the fallback period for each dataset's key below; it was referenced without
        # ever being defined, so a dataset arriving without its own period crashed this handler.
        period = str(msg.get("period_label") or "").strip()
        portal_display = f"{portal} ({filing_type})" if filing_type else portal
        capture_method = msg.get("capture_method", "DOM_Tracker")
        capture_status = msg.get("status") or (scraped.get("summary_labels", {}).get("Status") if scraped else None) or "Submitted"
        
        # An assembler payload may contain several independent GST/ITR
        # datasets. Materialize one tracker-dump row per dataset while keeping
        # An assembler payload may contain several independent GST/ITR
        # datasets. Materialize tracker-dump rows based on tracker_dump_captures
        # (confirmed submissions + last-viewed dataset) while keeping the full
        # assembler_captures and shared session timeline inside each row's raw payload for LTT.
        raw_payload_dict = msg.get("raw_payload") if isinstance(msg.get("raw_payload"), dict) else {}
        if "tracker_dump_captures" in raw_payload_dict and isinstance(raw_payload_dict["tracker_dump_captures"], list):
            target_captures = raw_payload_dict["tracker_dump_captures"]
        elif isinstance(raw_payload_dict.get("assembler_captures"), list) and raw_payload_dict["assembler_captures"]:
            target_captures = raw_payload_dict["assembler_captures"]
        else:
            target_captures = []

        dataset_messages = []
        if target_captures:
            for dataset in target_captures:
                if not isinstance(dataset, dict):
                    continue
                dataset_msg = dict(msg)
                dataset_msg.update({
                    key: value for key, value in dataset.items()
                    if value not in (None, "")
                })
                dataset_raw = dict(msg.get("raw_payload") or {})
                dataset_raw["dataset_capture"] = dataset
                computed_key = dataset.get("dataset_key") or self.db.compute_dataset_key(
                    dataset.get("portal") or portal,
                    dataset.get("gstin") or dataset.get("pan") or pan,
                    dataset.get("filing_type") or filing_type,
                    dataset.get("period_label") or period
                )
                dataset_raw["dataset_key"] = computed_key
                dataset_msg["dataset_key"] = computed_key
                dataset_msg["raw_payload"] = dataset_raw
                dataset_messages.append(dataset_msg)
        else:
            dataset_messages = [msg]

        # Directly record into Tracker Dump database with authoritative identity resolution
        try:
            results = []
            for dataset_msg in dataset_messages:
                dataset_raw = dataset_msg.get("raw_payload") if isinstance(dataset_msg.get("raw_payload"), dict) else {}
                dataset_pan = str(dataset_msg.get("pan") or pan or "").strip()
                dataset_arn = dataset_msg.get("arn", "N/A")
                dataset_filing_type = str(dataset_msg.get("filing_type") or filing_type).strip()
                dataset_portal = dataset_msg.get("portal") or portal
                dataset_portal_display = f"{dataset_portal} ({dataset_filing_type})" if dataset_filing_type else dataset_portal
                dataset_status = dataset_msg.get("status") or capture_status
                # SGT rewrites a row under a new key once the client becomes known; the row it
                # wrote before (keyed by its session) is removed. SGT keys only - see the DB method.
                superseded = dataset_msg.get("supersedes_dataset_key")
                if superseded:
                    self.db.delete_sgt_rows_by_dataset_key(superseded)
                result = self.db.insert_tracker_dump(
                    client_id=raw_client_id,
                    service_id=None,
                    portal=dataset_portal_display,
                    period_label=dataset_msg.get("period_label", ""),
                    arn_number=dataset_arn,
                    capture_method=dataset_msg.get("capture_method", capture_method),
                    status=dataset_status,
                    raw_payload_json=json.dumps(dataset_msg),
                    captured_by=self.actor,
                    pan=dataset_pan,
                    session_id=dataset_msg.get("session_id") or dataset_raw.get("session_id"),
                    filing_type=dataset_filing_type,
                    dataset_key=dataset_msg.get("dataset_key") or dataset_raw.get("dataset_key")
                )
                results.append(result)
            print(f"[main._handle_extension_result] Successfully inserted {len(results)} tracker_dump dataset row(s): {results}")

            return results[0] if len(results) == 1 else {"datasets": results, "count": len(results)}
        except Exception as e:
            print(f"[Tracker Dump Error] {e}")
            return None

    def _make_scc_handlers(self):
        """SCC-U's handlers (once, when its host is created)."""
        return self._ensure_scc()

    def _open_scc_manual(self, pan: str, client_id, on_error=None) -> bool:
        """Client Detail's MECP on an unverified Income Tax client (core/scc/manual.py): the card, with
        desktop-generated rows, in the portal's login page. An attempt SGT already has for this PAN is
        reused; otherwise a manual one is registered (SGT adopts it if it reads the password page, else
        only "This one worked" saves). Works with Detect login automatically Off."""
        opener, _outcome = self._ensure_scc()
        watcher = getattr(self, "clipboard_watcher", None)
        if watcher is not None:
            watcher.scc_ledger = self._scc_card.note_clipboard
        att, is_new = opener.register_manual(pan, client_id)
        if is_new:
            self._scc_counter.on_attempt_opened()
        sent = self._scc_card.open(att, open_tab=att.manual, on_error=on_error)
        if not sent and is_new:
            opener.end(att.hwnd, "closed")
        return sent

    def _ensure_scc(self):
        """Builds SCC-U's objects once: the attempt opener, the MECP SCC card it feeds (core/scc/card.py)
        and the outcome reader (core/scc/outcome.py). The card's copy ledger reads the clipboard through
        clipboard_watch; each copy opens the host's read window. Returns [opener, outcome reader]."""
        existing = getattr(self, "_scc_parts", None)
        if existing is not None:
            return existing
        import automation
        import clipboard_watch
        from core.scc import AttemptOpener, OutcomeReader, SccCard, SccSaver, WhichOne, db_client_names, db_lookup, SccCounter
        counter = SccCounter()
        self._scc_counter = counter

        def on_attempt_opened(att):
            counter.on_attempt_opened()
            self._scc_card.open(att)

        opener = AttemptOpener(db_lookup(self.db), on_open=on_attempt_opened,
                               on_end=lambda att, reason: self._scc_card.end(att, reason))
        self._scc_opener = opener

        def read_harder(att, _label):
            host = getattr(getattr(self.vsdc_worker, "router", None), "_scc_host", None)
            if host is not None and att.session_id:
                host.open_read_window(att.session_id)

        def on_worked_with_close_and_count(att, row_label):
            saver = SccSaver(self.db, lambda aid, label: self._scc_card.row_text(aid, label),
                           lambda: getattr(self, "actor", "Staff"), after=self._scc_saved)
            result = saver.on_worked(att, row_label)
            if result is not None:
                counter.on_password_saved()
                # Close the card and mark the attempt as closed in the opener
                automation.close_scc_card(att.attempt_id)
                opener.close(att.hwnd)
            return result

        self._scc_card = SccCard(
            self.db,
            open_card=lambda service, pan, rows, title, client_id, attempt_id, **opts:
                automation.send_scc_card(service, pan, rows, title, client_id, attempt_id, **opts),
            close_card=automation.close_scc_card,
            suppress=clipboard_watch.suppress_client, release=clipboard_watch.release_client,
            on_closed=lambda att: opener.close(att.hwnd),
            update_card=automation.update_scc_card, on_copy=read_harder,
            on_none=lambda att: opener.end(att.hwnd, "typed own"),
            on_asking=lambda: counter.on_card_asked(),
            on_worked=on_worked_with_close_and_count)
        # Step 5: which row gets the login's credit (SccCard.credit -> the guarded save).
        def on_outcome_with_counter(att, kind, detail):
            counter.on_outcome(kind)

        which = WhichOne(self._scc_card, lambda att: self._scc_outcome.copied_since_refusal(att),
                        then=on_outcome_with_counter)
        self._scc_outcome = OutcomeReader(opener, self._scc_card, client_names=db_client_names(self.db),
                                          on_outcome=which.on_outcome)
        watcher = getattr(self, "clipboard_watcher", None)
        if watcher is not None:
            watcher.scc_ledger = self._scc_card.note_clipboard
        self._scc_parts = [opener, self._scc_outcome]
        return self._scc_parts

    def _handle_scc_row_worked(self, msg: dict):
        card = getattr(self, "_scc_card", None)
        if card is not None:
            card.row_worked(msg.get("attempt_id"), msg.get("row_label"))    # credit -> the guarded save

    def _handle_scc_card_closed(self, msg: dict):
        card = getattr(self, "_scc_card", None)
        if card is not None:
            card.card_closed(msg.get("attempt_id"))

    def _handle_scc_row_none(self, msg: dict):
        card = getattr(self, "_scc_card", None)
        if card is not None:
            card.none_typed(msg.get("attempt_id"))

    def _scc_saved(self, result):
        """SccSaver's `after`: SCC-U's thread or the Qt thread - the refresh always runs on the Qt thread."""
        self.sync_bridge.scc_saved_signal.emit(result.client_id, result.client_name, result.pan, result.outcome)

    def _after_scc_save(self, client_id: int, client_name: str, pan: str, outcome: str):
        """UI refresh after an SCC save: toast, Client Detail, grid, search, extension settings."""
        try:
            if hasattr(self, "tray_icon") and self.tray_icon and self.tray_icon.isVisible():
                what = {"created": "Client added with the verified password", "replaced": "Saved password replaced",
                        "verified": "Marked verified"}.get(outcome, "Permanent password saved")
                self.tray_icon.showMessage("Password Verified via SCC", f"{what} for {client_name} ({pan}).",
                                           QSystemTrayIcon.Information, 5000)

            if hasattr(self, "client_detail_win") and self.client_detail_win and self.client_detail_win.isVisible():
                cur_c = getattr(self.client_detail_win, "client", None)
                if cur_c and cur_c.get("id") == client_id:
                    self.client_detail_win.set_client(self.db.get_client(client_id))

            if hasattr(self, "shell") and self.shell and hasattr(self.shell, "refresh_clients"):
                self.shell.refresh_clients()
            if hasattr(self, "search_win") and self.search_win:
                self.search_win._on_search_changed()
            self._sync_extension_settings()
        except Exception as e:
            print(f"[main._after_scc_save error] {type(e).__name__}")

    def _run_pending_rejoin(self) -> None:
        """P2-8: finish or run a requested "Rejoin office", then offer the salvage import.

        Runs before any database is opened. Cheap when there is nothing to do.
        """
        import sync_rejoin
        app_dir = Path(self.app_dir)
        requested = sync_rejoin.read_rejoin_request(app_dir) is not None
        if not requested and not sync_rejoin.state_path(app_dir).exists():
            return
        # Consume the request first, so a failure or crash can never loop on every start.
        sync_rejoin.clear_rejoin_request(app_dir)
        from ui.dialogs.rejoin_office_dialog import run_pending_rejoin
        result = run_pending_rejoin(app_dir, requested=requested)
        if result == "exit":
            sys.exit(1)
        if result == "quit":
            sys.exit(0)

    def _run_pending_shadow_start(self) -> None:
        """P3-7b: install a staged "Start shadow mode" (the admin PC's replica becomes this PC's
        databases), then offer to import what only this PC had.

        Runs before any database is opened. Cheap when there is nothing to do.
        """
        import sync_shadow
        app_dir = Path(self.app_dir)
        if not sync_shadow.has_pending_shadow_start(app_dir) and sync_shadow.pending_shadow_salvage(app_dir) is None:
            return
        from ui.dialogs.shadow_start_dialog import run_pending_shadow_start
        run_pending_shadow_start(app_dir)

    def _run_pending_go_live(self) -> None:
        """P3-9: install a staged go-live (the shadow replicas become the live databases, mode
        live). Runs before any database is opened. The outcome is shown as a start-up alert,
        so a failed go-live isn't mistaken for a successful one."""
        import sync_shadow
        app_dir = Path(self.app_dir)
        if not sync_shadow.has_pending_go_live(app_dir):
            return
        try:
            sync_shadow.apply_pending_go_live(app_dir)
        except sync_shadow.GoLiveError as e:
            print(f"[SeraApp] Go-live failed: {e}")
            self._go_live_alert = ("error", f"Sera Sync did not go live on this PC: {e}. It is still in "
                                            "shadow mode; see logs/sync_shadow.log.", 0)
            return
        except Exception as e:
            # The office key couldn't be loaded: nothing was touched, retried at the next start.
            print(f"[SeraApp] Go-live not applied yet: {e}")
            self._go_live_alert = ("warning", f"Sera Sync could not go live yet ({type(e).__name__}); "
                                              "it will try again at the next start.", 0)
            return
        self._go_live_alert = ("warning", "Sera Sync is now live on this PC: other PCs' changes appear "
                                          "in your data directly. The previous database is kept in the "
                                          "shadow folder.", 15000)

    def _run_pending_restore(self) -> None:
        """P4-3b: a backup restore staged on the admin PC is done now, before any database is
        opened; it then replicates to every PC. The outcome is a start-up alert."""
        import sync_restore
        app_dir = Path(self.app_dir)
        if not sync_restore.has_pending_restore(app_dir):
            return
        try:
            info = sync_restore.apply_pending_restore(app_dir)
        except sync_restore.RestoreError as e:
            print(f"[SeraApp] Restore failed: {e}")
            self._restore_alert = ("error", f"The backup was not restored: {e}. Your data is unchanged.", 0)
            return
        except Exception as e:
            # The office key couldn't be loaded: nothing was touched, retried at the next start.
            print(f"[SeraApp] Restore not done yet: {e}")
            self._restore_alert = ("warning", f"The backup could not be restored yet ({type(e).__name__}); "
                                              "Sera will try again at the next start.", 0)
            return
        if info:
            self._restore_alert = (
                "warning",
                f"Backup {info.get('backup_name')} restored: {info.get('clients_back', 0)} client(s) back, "
                f"{info.get('clients_removed', 0)} removed, {info.get('fields_revert', 0)} field(s) reverted. "
                "The other PCs follow when they sync.", 15000)

    def _run_pending_catch_up(self) -> None:
        """P4-4: a snapshot downloaded because this PC was below another PC's compaction floor
        is installed now, before any database is opened."""
        import sync_compaction
        app_dir = Path(self.app_dir)
        if not sync_compaction.has_pending_catch_up(app_dir):
            return
        try:
            sync_compaction.apply_pending_catch_up(app_dir)
        except sync_compaction.CatchUpError as e:
            print(f"[SeraApp] Catch-up failed: {e}")
            self._catch_up_alert = ("warning", f"Sera Sync could not install the office data it downloaded: {e}. "
                                               "It will download it again.", 0)
            return
        except Exception as e:
            print(f"[SeraApp] Catch-up not installed yet: {e}")
            self._catch_up_alert = ("warning", f"Sera Sync could not install the downloaded office data yet "
                                               f"({type(e).__name__}); it will try again at the next start.", 0)
            return
        self._catch_up_alert = ("warning", "This PC is up to date with the office again (it had been away "
                                           "for a long time). Its own changes were kept.", 15000)

    def _handle_missing_office_db(self, app_dir: Path, db_path: str, hex_key: str) -> None:
        """Office mode requires an existing database; prevent silent initialization of empty DB (P1-6)."""
        from PySide6.QtWidgets import QMessageBox, QFileDialog
        import shutil
        import sqlcipher3.dbapi2 as sqlite3

        reply = QMessageBox.critical(
            None,
            "Aman Associates — Office Database Missing",
            f"The office database was not found at:\n{db_path}\n\n"
            "To prevent data loss, Sera will not initialize a blank database in an existing office.\n\n"
            "Would you like to select a database backup file to restore?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply == QMessageBox.Yes:
            backup_dir = str(Path(app_dir) / "backups")
            backup_file, _ = QFileDialog.getOpenFileName(
                None, "Select Master Database Backup",
                backup_dir if os.path.exists(backup_dir) else str(app_dir),
                "Database Files (*.db *.bak*);;All Files (*.*)",
            )
            if backup_file and os.path.exists(backup_file):
                import tempfile
                try:
                    # Test against a temporary copy so user's backup is never modified or checkpointed by SQLite
                    with tempfile.TemporaryDirectory() as td:
                        temp_db = Path(td) / "probe.db"
                        shutil.copy2(backup_file, temp_db)
                        for ext in ("-wal", "-shm"):
                            s = Path(str(backup_file) + ext)
                            if s.exists():
                                shutil.copy2(s, str(temp_db) + ext)

                        conn = sqlite3.connect(str(temp_db))
                        conn.execute(f"PRAGMA key = \"x'{hex_key}'\";")
                        res = conn.execute("SELECT count(*) FROM sqlite_master;").fetchone()
                        qc = conn.execute("PRAGMA quick_check;").fetchone()
                        conn.close()

                        if not (res and res[0] >= 0 and qc and qc[0] == "ok"):
                            raise ValueError(f"Integrity check failed: {qc[0] if qc else 'unknown error'}")

                    # Restore database and any accompanying sidecars
                    shutil.copy2(backup_file, db_path)
                    for ext in ("-wal", "-shm"):
                        src_sidecar = Path(str(backup_file) + ext)
                        dst_sidecar = Path(str(db_path) + ext)
                        if src_sidecar.exists():
                            shutil.copy2(src_sidecar, dst_sidecar)
                        elif dst_sidecar.exists():
                            # Retain timestamped backup of leftover sidecar per §0 rule 3
                            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
                            dst_sidecar.rename(dst_sidecar.with_name(f"{dst_sidecar.name}.bak-{ts}"))

                    QMessageBox.information(
                        None, "Database Restored",
                        f"Database successfully restored from:\n{backup_file}\n\nStarting Sera...",
                    )
                    return
                except Exception as e:
                    QMessageBox.critical(
                        None, "Restore Failed",
                        f"The selected backup could not be opened with this office key:\n\n{e}",
                    )
            else:
                QMessageBox.information(
                    None, "Restore Cancelled",
                    "No database backup was selected. Sera cannot start without the office database.",
                )
        else:
            QMessageBox.information(
                None, "Startup Cancelled",
                "Database restore was cancelled. Sera cannot start without the office database.",
            )
        sys.exit(0)

    def _resolve_encryption_key(self) -> tuple[str, str | None, str]:
        """
        Resolves the database encryption key according to blueprint §5 P1-2/P4-2:
        Order:
          1. apply_pending_swap (P0-4) - executed before this call.
          2. If keys/office.json exists -> office mode:
             dek = load_dek(). On KeyUnavailable, show the recovery dialog (P1-6).
             hex_key = dek_hex(dek).
             Check key_id against office.json.
             Never read or write sera.key in this mode.
          3. Else, if master.db doesn't exist either -> first run: FirstRunDialog
             ("New Office" / "Join Office", P2-7; both office-key only since P4-2 removed the
             "Legacy: new/join" pages). Re-resolve once it completes.
          4. Else (master.db exists but there is no office key): P4-2 removed the legacy
             password+salt start-up path and the "Convert to office key" migration tool --
             every real PC already has an office key since Sera Sync v3 went live (P3-9). The
             only surviving reader of sera.key/sera.salt is the P2-8 rejoin/salvage importer,
             so offer that directly instead of opening the database with the old password.
        Expose self.key_mode ('office') and return (key_mode, key_id, hex_key). key_mode is
        always 'office'; legacy mode no longer exists.
        """
        app_dir = getattr(self, "app_dir", None)
        if not app_dir:
            db_path = getattr(self, "db_path", str(APP_DIR / "master.db"))
            app_dir = Path(db_path).parent

        import sera_keys
        office_path = sera_keys.keys_dir(app_dir) / sera_keys.OFFICE_FILE

        if not office_path.exists():
            db_path = getattr(self, "db_path", str(app_dir / "master.db"))

            if not os.path.exists(db_path):
                from ui.dialogs.first_run_dialog import FirstRunDialog
                first_run_dlg = FirstRunDialog(app_dir, getattr(self, "actor_alias", "Admin"))
                if first_run_dlg.exec() != QDialog.Accepted:
                    sys.exit(0)
                # "New Office" / "Join Office" wrote keys/office.json (and, for New Office,
                # master.db directly) -- re-resolve so this call takes the office-mode branch
                # below. A Join whose pairing succeeded but whose snapshot download didn't
                # still leaves office.json without master.db; the office-mode branch's
                # pending-join check in __init__ (has_pending_join) picks that up on this
                # same call.
                return self._resolve_encryption_key()

            # A database exists but this PC has no office key: a pre-office-key install that
            # was never converted or rejoined. Offer the P2-8 rejoin/salvage wizard (the one
            # surviving reader of this PC's sera.key/sera.salt) instead of opening it directly.
            from ui.dialogs.rejoin_office_dialog import run_pending_rejoin
            result = run_pending_rejoin(app_dir, requested=True)
            if result == "exit":
                sys.exit(1)
            if result == "quit":
                sys.exit(0)
            if not (sera_keys.keys_dir(app_dir) / sera_keys.OFFICE_FILE).exists():
                # The user declined to rejoin; there is no other way to open this database.
                sys.exit(0)
            return self._resolve_encryption_key()

        # Office Mode
        self.key_mode = "office"
        try:
            office_info = sera_keys.load_office(app_dir)
        except sera_keys.KeyFileInvalid as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(
                None, "Aman Associates — Office Configuration Error",
                f"The office configuration file is invalid or corrupted:\n\n{e}\n\n"
                "Please restore keys/office.json or contact your office administrator."
            )
            sys.exit(0)

        if office_info is None:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(
                None, "Aman Associates — Office Configuration Error",
                "The office configuration file could not be loaded.\n\n"
                "Please contact your office administrator."
            )
            sys.exit(0)

        try:
            dek = sera_keys.load_dek(app_dir)
        except sera_keys.KeyUnavailable:
            dek = self._recover_office_dek(app_dir)
            if not dek:
                sys.exit(0)

        import hmac
        computed_key_id = sera_keys.key_id(dek)
        if not hmac.compare_digest(computed_key_id, office_info.key_id):
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(
                None, "Aman Associates — Office Key Mismatch",
                "The loaded office data key does not match the office configuration in office.json.\n\n"
                "Startup aborted to prevent database damage."
            )
            sys.exit(0)

        self.key_id = computed_key_id
        hex_key = sera_keys.dek_hex(dek)
        return self.key_mode, self.key_id, hex_key

    def _recover_office_dek(self, app_dir: Path | str | None = None) -> bytes | None:
        """Prompts for office master password or recovery kit restore when DPAPI fails (P1-6)."""
        import sera_keys
        if not app_dir:
            app_dir = getattr(self, "app_dir", None) or Path(getattr(self, "db_path", str(APP_DIR / "master.db"))).parent
        app_dir = Path(app_dir)

        from ui.dialogs.office_recovery_dialog import OfficeRecoveryDialog
        dlg = OfficeRecoveryDialog(app_dir, parent=None)
        if dlg.exec() == QDialog.Accepted:
            return dlg.recovered_dek
        return None

    def _ensure_user_identity(self) -> tuple[str, str]:
        """Get or create a simple username for this workstation (non-intrusive).
        Stores it in device_identity.txt for future launches."""
        saved_name = ""
        try:
            saved_name = self.identity_path.read_text(encoding="utf-8").strip()
        except OSError:
            pass

        if saved_name:
            return saved_name, saved_name

        clean_name = socket.gethostname()
        try:
            self.identity_path.write_text(clean_name, encoding="utf-8")
        except OSError:
            pass
        return clean_name, clean_name

    def _apply_theme(self):
        theme_name = self.db.get_setting("theme", "light")
        qss = get_theme_stylesheet(theme_name)
        self.app.setStyleSheet(qss)

    def _apply_window_mode(self):
        # Reset size constraints and size policy completely
        self.shell.setMinimumSize(0, 0)
        self.shell.setMaximumSize(16777215, 16777215)
        self.shell.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        mode = self.db.get_setting("window_mode", "fullscreen")
        if mode == "square":
            self.shell.setWindowState(Qt.WindowNoState)
            self.shell.showNormal()
            self.app.processEvents()
            screen = self.app.primaryScreen()
            if screen:
                geom = screen.availableGeometry()
                max_side = min(geom.width(), geom.height()) - 60
                side = max(500, min(max_side, 850))
                x = geom.x() + (geom.width() - side) // 2
                y = geom.y() + (geom.height() - side) // 2
                self.shell.setGeometry(x, y, side, side)
            else:
                self.shell.resize(800, 800)
                self.shell.move(100, 100)
            self.shell.show()
        elif mode == "rectangular":
            self.shell.setWindowState(Qt.WindowNoState)
            self.shell.showNormal()
            self.app.processEvents()
            screen = self.app.primaryScreen()
            if screen:
                geom = screen.availableGeometry()
                w, h = min(950, geom.width() - 40), min(680, geom.height() - 40)
                x = geom.x() + (geom.width() - w) // 2
                y = geom.y() + (geom.height() - h) // 2
                self.shell.setGeometry(x, y, w, h)
            else:
                self.shell.resize(950, 680)
                self.shell.move(100, 100)
            self.shell.show()
        else:  # "fullscreen" (Default)
            self.shell.setWindowState(Qt.WindowNoState)
            self.shell.showMaximized()

    def _build_ui(self):
        self._apply_theme()
        self.shell = AppShell()
        memory_mark("  window: theme + shell")
        self.shell.sidebar.set_user_name(self.actor_alias)
        
        self.search_win = SearchWindow(self.db)
        memory_mark("  window: search")
        self.detail_win = ClientDetailWindow(self.db, actor=self.actor)
        memory_mark("  window: client detail")
        self.admin_win = AdminWindow(self.db, actor=self.actor)
        memory_mark("  window: admin")
        self.tracker_dump_win = TrackerDumpWindow(self.db, defer_first_load=True)   # hidden page: fill when shown
        memory_mark("  window: tracker dump")
        self.shell.add_page(self.search_win)          # Index 0
        self.shell.add_page(self.admin_win)           # Index 1
        self.shell.add_page(self.tracker_dump_win)    # Index 2
        
        # Inject detail_win into the slide panel
        self.shell.slide_panel.set_widget(self.detail_win, persistent=True)
        
        # Signals
        self.search_win.client_selected.connect(self._show_client_detail)
        self.search_win.add_client_requested.connect(self._open_new_client_form)
        self.search_win.edit_client_requested.connect(self._open_client_editor)
        self.search_win.delete_client_requested.connect(self._delete_client_from_search)
        self.search_win.manage_services_requested.connect(self._manage_client_services_from_search)
        self.search_win.archive_client_requested.connect(self._archive_client_from_search)
        self.search_win.toast_requested.connect(self.shell.show_alert)
        self.search_win.action_alert_requested.connect(self.shell.show_action_alert)
        self.search_win.toggle_sidebar_requested.connect(self.shell.toggle_sidebar)
        
        self.detail_win.back_requested.connect(self._show_search_from_detail)
        self.detail_win.toast_requested.connect(self.shell.show_toast)
        self.detail_win.action_alert_requested.connect(self.shell.show_action_alert)
        self.tracker_dump_win.service_action_requested.connect(self.detail_win.run_service_action)
        self.admin_win.back_requested.connect(self._show_search_from_admin)
        self.admin_win.request_slide_panel.connect(self._open_in_slide_panel)
        self.admin_win.toast_requested.connect(self.shell.show_toast)
        self.admin_win.action_alert_requested.connect(self.shell.show_action_alert)
        self.admin_win.settings_saved.connect(self._apply_run_in_background)
        self.admin_win.settings_saved.connect(self._apply_vsdc_engine_settings)

        # Sidebar Connections
        sidebar = self.shell.sidebar
        sidebar.go_to_search.connect(self._show_search_from_admin)
        sidebar.action_import_csv.connect(self.admin_win._on_import_csv)
        sidebar.action_manage_clients.connect(self._show_manage_clients)
        sidebar.action_tracker_dump.connect(self._show_tracker_dump)
        sidebar.action_audit_log.connect(self.admin_win._on_view_audit_log)
        sidebar.action_manage_staff.connect(self.admin_win._on_open_sera_sync)
        sidebar.action_open_sera_sync.connect(self.admin_win._on_open_sera_sync)
        sidebar.action_trigger_sync.connect(self.search_win._on_manual_refresh)

        sidebar.action_settings.connect(self.admin_win._on_open_settings)
        sidebar.action_enter_admin.connect(self._request_admin_mode)
        sidebar.action_exit_admin.connect(self._exit_admin_mode)
        
        # Global Search Hotkeys (Ctrl+F, Ctrl+K)
        self.sc_ctrl_f = QShortcut(QKeySequence("Ctrl+F"), self.shell)
        self.sc_ctrl_f.activated.connect(self._global_search_shortcut)
        
        self.sc_ctrl_k = QShortcut(QKeySequence("Ctrl+K"), self.shell)
        self.sc_ctrl_k.activated.connect(self._global_search_shortcut)

        # Toggle Sidebar Hotkey (Ctrl+B)
        self.sc_ctrl_b = QShortcut(QKeySequence("Ctrl+B"), self.shell)
        self.sc_ctrl_b.activated.connect(self.shell.toggle_sidebar)

        # Inject sync service into admin window for Sera Sync dialog
        self.admin_win.set_sync_service(self.sync_service)
        self.admin_win.set_discovery_service(getattr(self, "discovery_service", None))
        self.admin_win.set_sync_engine(getattr(self, "sync_engine", None))

        from version import APP_VERSION
        self.shell.setWindowTitle(f"Project Sera — Aman Associates — v{APP_VERSION}")
        self.shell.on_minimized_to_tray = self._on_window_put_away
        self.shell.on_restored = self._on_window_restored
        self.shell.on_minimized = lambda: QTimer.singleShot(5_000, lambda: self._trim_if_idle("window minimised"))
        self.shell.on_quit_requested = self._quit_application
        self._setup_system_tray()
        # Apply run_in_background setting so closeEvent behaves correctly from startup
        self._apply_run_in_background()
        self._apply_window_mode()
        memory_mark("  window: tray + shown")

    def _apply_vsdc_engine_settings(self):
        """Apply the Settings -> Tracker switches (VSDC, VSDC-X, VSDC 24/7, SGT) to the running worker.

        Called once at startup and again every time the user saves settings, so a switch takes
        effect at once, without a restart. The worker thread only starts once at least one
        engine is on; turning everything off leaves it idle (the router ignores every tick).
        """
        try:
            # The HUD pill switch is independent of the engines: it only decides whether the
            # pill is shown, so it applies even when no engine is on.
            from core.vsdc.vsdc_engines import (apply_sgt_live_rollout, read_engine_flags, read_hud_enabled,
                                                read_scc_detect_mode, read_sgt_i_mode, read_sgt_mode,
                                                read_sgt_record_pages)
            # Once per office: SGT live becomes the capture engine, the other three go off.
            apply_sgt_live_rollout(self.db.get_setting, self.db.set_settings_bulk)
            hud = getattr(self, "vsdc_hud", None)
            if hud is not None:
                hud.set_enabled(read_hud_enabled(self.db.get_setting))
            worker = getattr(self, "vsdc_worker", None)
            if worker is None:
                return
            vsdc, vsdc_x, vsdc247 = read_engine_flags(self.db.get_setting)
            sgt = read_sgt_mode(self.db.get_setting)
            worker.router.apply_engine_settings(vsdc, vsdc_x, vsdc247, sgt=sgt,
                                                sgt_record=read_sgt_record_pages(self.db.get_setting),
                                                sgt_i=read_sgt_i_mode(self.db.get_setting) == "on",
                                                scc_detect=read_scc_detect_mode(self.db.get_setting) == "on")
            if (vsdc or vsdc_x or vsdc247 or sgt != "off") and not worker.isRunning():
                worker.start()
                print("⚡ [main] VSDC Worker started "
                      f"(VSDC={'on' if vsdc else 'off'}, VSDC-X={'on' if vsdc_x else 'off'}, "
                      f"VSDC 24/7={'on' if vsdc247 else 'off'}, SGT={sgt}).")
        except Exception as e:
            print(f"⚠️ [main] Could not apply the VSDC engine settings: {e}")

    def _apply_run_in_background(self):
        """Read the run_in_background db setting and push it onto the shell.

        Called once at startup and again every time the user saves settings.
        When False the tray icon is hidden (not needed) and closing the window
        exits the app immediately. When True the tray icon is shown and closing
        minimises to tray.
        """
        try:
            run_in_bg = (self.db.get_setting("run_in_background", "1") == "1")
            self.shell._run_in_background = run_in_bg
            if hasattr(self, "tray_icon") and self.tray_icon:
                if run_in_bg:
                    self.tray_icon.show()
                else:
                    self.tray_icon.hide()

            # Also refresh SCA (Sera Clipboard Assist) status
            if hasattr(self, "clipboard_watcher") and self.clipboard_watcher:
                sca_active = (self.db.get_setting("sca_enabled", "1") == "1")
                self.clipboard_watcher.set_enabled(sca_active)
                self.clipboard_watcher.refresh_index()
        except Exception:
            pass

    def _setup_system_tray(self):
        """Initializes the Windows system tray icon and background context menu."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        self.tray_icon = QSystemTrayIcon(self.shell)
        if hasattr(self, "app_icon") and self.app_icon and not self.app_icon.isNull():
            self.tray_icon.setIcon(self.app_icon)
        elif not self.shell.windowIcon().isNull():
            self.tray_icon.setIcon(self.shell.windowIcon())

        self.tray_icon.setToolTip("Project Sera — Aman Associates (Running in background)")

        tray_menu = QMenu()
        tray_menu.setStyleSheet("""
            QMenu {
                background-color: #1E252B;
                color: #F8F5F2;
                border: 1px solid #2E9B5F;
                border-radius: 6px;
                padding: 6px;
                font-family: 'Segoe UI', Arial, sans-serif;
                font-size: 13px;
            }
            QMenu::item {
                padding: 7px 22px 7px 28px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #2E9B5F;
                color: #FFFFFF;
            }
            QMenu::separator {
                height: 1px;
                background: #333D45;
                margin: 4px 8px;
            }
        """)

        action_open = tray_menu.addAction(_safe_qta_icon("mdi.monitor", "#4CF9B7"), "Open Project Sera")
        action_open.triggered.connect(self._restore_from_tray)
        f = action_open.font()
        f.setBold(True)
        action_open.setFont(f)

        action_search = tray_menu.addAction(_safe_qta_icon("mdi.magnify", "#4CF9B7"), "Search Clients")
        action_search.triggered.connect(lambda: (self._show_search_from_admin(), self._restore_from_tray()))

        action_tracker = tray_menu.addAction(_safe_qta_icon("mdi.clipboard-text-search-outline", "#4CF9B7"), "Tracker Dump Workspace")
        action_tracker.triggered.connect(lambda: (self._show_tracker_dump(), self._restore_from_tray()))

        action_sync = tray_menu.addAction(_safe_qta_icon("mdi.sync", "#4CF9B7"), "Sera Sync")
        action_sync.triggered.connect(lambda: (self.admin_win._on_open_sera_sync(), self._restore_from_tray()))

        action_sca_diag = tray_menu.addAction(_safe_qta_icon("mdi.clipboard-pulse-outline", "#4CF9B7"), "SCA Diagnostics")
        action_sca_diag.triggered.connect(lambda: (self._show_sca_diagnostics(), self._restore_from_tray()))

        tray_menu.addSeparator()

        self.action_install_update = tray_menu.addAction(_safe_qta_icon("mdi.update", "#4CF9B7"), "Install Update & Restart")
        self.action_install_update.triggered.connect(self._apply_pending_update_now)
        self.action_install_update.setVisible(False)

        action_quit = tray_menu.addAction(_safe_qta_icon("mdi.power", "#FF4D4D"), "Exit Project Sera")
        action_quit.triggered.connect(self._quit_application)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason):
        """Single or double click on tray icon restores and brings window to foreground."""
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            if self.shell.isVisible() and not self.shell.isMinimized():
                self.shell.raise_()
                self.shell.activateWindow()
            else:
                self._restore_from_tray()

    def _restore_from_tray(self):
        """Unhides/restores the main application window."""
        if hasattr(self, "shell") and self.shell:
            if self.shell.isMinimized():
                self.shell.showNormal()
            self.shell.show()
            self.shell.raise_()
            self.shell.activateWindow()

    def _window_in_use(self) -> bool:
        shell = getattr(self, "shell", None)
        return bool(shell is not None and shell.isVisible() and not shell.isMinimized())

    def _refresh_when_seen(self, key: str, fn) -> None:
        """Background-triggered screen refreshes (a capture, a sync, maintenance) run now if the
        window is in use; while it is minimised or in the tray only the latest one per screen is
        kept and run once the window is back. Every one of them rebuilds a table on the UI thread,
        and the app froze while minimised (2026-09-26) - nobody can see those tables then."""
        if self._window_in_use():
            fn()
            return
        if not hasattr(self, "_deferred_refreshes"):
            self._deferred_refreshes = {}
        self._deferred_refreshes[key] = fn

    def _refresh_synced_screens(self, targets) -> None:
        """Refreshes the named screens after synced data arrived, via _refresh_when_seen."""
        refreshers = {
            "dashboard_win": "refresh", "search_win": "refresh", "admin_win": "refresh",
            "tracker_dump_win": "load_data",
        }
        for name, method in refreshers.items():
            win = getattr(self, name, None)
            if name in targets and win:
                self._refresh_when_seen(name, getattr(win, method))
        detail = getattr(self, "detail_win", None)
        if "detail_win" in targets and detail is not None:
            def _reload_detail(d=detail):
                if d.isVisible() and getattr(d, "client_id", None):
                    d.load_client(d.client_id)
            self._refresh_when_seen("detail_win", _reload_detail)

    def _on_window_restored(self) -> None:
        # After the window has painted, so coming back from the tray is instant.
        QTimer.singleShot(0, self._run_deferred_refreshes)

    def _run_deferred_refreshes(self) -> None:
        if not self._window_in_use():
            return
        pending, self._deferred_refreshes = getattr(self, "_deferred_refreshes", {}), {}
        # Keys are window attribute names; a screen whose minute tick was skipped catches up
        # here, unless a full refresh of it is already pending.
        for win_name in ("search_win", "admin_win"):
            win = getattr(self, win_name, None)
            if win is not None and getattr(win, "_activity_stale", False):
                win._activity_stale = False
                tick = getattr(win, "_on_activity_tick", None) or getattr(win, "_refresh_activity_tags", None)
                pending.setdefault(win_name, tick)
        for key, fn in pending.items():
            try:
                fn()
            except Exception as e:
                print(f"[UI] Deferred refresh '{key}' failed: {e}")

    def _trim_if_idle(self, reason: str) -> None:
        """Hand back touched-once memory, but only while nobody is looking at the window."""
        if self._window_in_use():
            return
        from core.memlog import trim_working_set
        trim_working_set(reason)
        if getattr(self, "_backup_scheduler", None):
            # Off the Qt thread: a due backup copies both databases, which froze the window.
            self._backup_scheduler.check_soon()

    def _on_window_put_away(self):
        self._show_tray_minimized_hint()
        QTimer.singleShot(5_000, lambda: self._trim_if_idle("hidden to the tray"))

    def _show_tray_minimized_hint(self):
        """Displays a one-time balloon toast notifying user that the app is active in background."""
        if hasattr(self, "tray_icon") and self.tray_icon and self.tray_icon.isVisible():
            if not getattr(self, "_tray_hint_shown", False):
                self.tray_icon.showMessage(
                    "Project Sera is running in the background",
                    "The app is still active and listening for sync/filings. Right-click this tray icon to open or exit.",
                    QSystemTrayIcon.Information,
                    3500
                )
                self._tray_hint_shown = True

    def _handle_update_found(self, info: dict):
        latest = info.get("latest_version", "new")
        current = info.get("current_version", "")
        print(f"[AutoUpdater] New update found: v{latest} (current: v{current}). Downloading payload in background...")

    def _handle_update_ready(self, installer_path_str: str, info: dict):
        self._pending_update_installer = installer_path_str
        self._pending_update_info = info
        latest_ver = info.get("latest_version", "new")
        print(f"[AutoUpdater] Update v{latest_ver} successfully downloaded to {installer_path_str}. Ready for silent install.")
        if hasattr(self, "action_install_update") and self.action_install_update:
            self.action_install_update.setText(f"Install v{latest_ver} & Restart")
            self.action_install_update.setVisible(True)
        if hasattr(self, "tray_icon") and self.tray_icon and self.tray_icon.isVisible():
            self.tray_icon.showMessage(
                "Sera Update Ready",
                f"Version {latest_ver} has been downloaded in the background.\nIt will be installed automatically upon exiting the app.",
                QSystemTrayIcon.Information,
                5000,
            )

    def _apply_pending_update_now(self):
        if getattr(self, "_pending_update_installer", None):
            installer = Path(self._pending_update_installer)
            if installer.exists():
                self._update_applied = True
                print(f"[AutoUpdater] Applying downloaded update immediately: {installer}")
                import version
                version.apply_and_restart(installer, silent=True)

    def _flush_capture_queue_on_quit(self, timeout_sec: float = 5.0):
        """Waits (bounded) for queued captures to be written; the capture thread is a daemon."""
        import time as _time
        deadline = _time.monotonic() + timeout_sec
        q = getattr(self, "_capture_queue", None)
        while q is not None and q.unfinished_tasks and _time.monotonic() < deadline:
            _time.sleep(0.05)

    def _on_app_about_to_quit(self):
        if self._update_applied:
            return
        if getattr(self, "_pending_update_installer", None):
            installer = Path(self._pending_update_installer)
            if installer.exists():
                self._update_applied = True
                print(f"[AutoUpdater] Silent update triggered upon app aboutToQuit: {installer}")
                import version
                version.apply_and_restart(installer, silent=True)

    def _quit_application(self):
        """Full clean application shutdown triggered from the system tray menu or close button."""
        if hasattr(self, "shell") and self.shell:
            self.shell.hide()
            self.shell._force_close = True
            self.shell.close()
        if hasattr(self, "tray_icon") and self.tray_icon:
            self.tray_icon.hide()
        if hasattr(self, "bridge") and self.bridge:
            try:
                self.bridge.stop()
            except Exception:
                pass
        if hasattr(self, "sync_service") and self.sync_service:
            try:
                self.sync_service.stop()
            except Exception:
                pass
        if getattr(self, "discovery_service", None):
            try:
                self.discovery_service.stop()
            except Exception:
                pass
        if not self._update_applied and getattr(self, "_pending_update_installer", None):
            installer = Path(self._pending_update_installer)
            if installer.exists():
                self._update_applied = True
                print(f"[AutoUpdater] Silent update triggered upon app exit: {installer}")
                import version
                version.apply_and_restart(installer, silent=True)
                return
        self.app.quit()

    def _global_search_shortcut(self):
        self.shell.dismiss_detail_on_outside = False
        self.shell.set_current_page(0)
        self.shell.slide_panel.slide_out()
        self.search_win.focus_and_select_search()

    def _show_search_from_detail(self):
        # Close the slide panel to reveal the search view underneath
        self.shell.dismiss_detail_on_outside = False
        self.shell.slide_panel.slide_out()

    def _show_search_from_admin(self):
        self.shell.dismiss_detail_on_outside = False
        self.shell.set_current_page(0)
        self.shell.slide_panel.slide_out()
        QTimer.singleShot(0, self._refresh_search_after_switch)

    def _refresh_search_after_switch(self):
        from core.memlog import timed
        with timed("loading the search grid"):
            self.search_win.refresh()

    def _show_client_detail(self, client_id: int):
        self.shell.dismiss_detail_on_outside = True
        self.detail_win.load_client(client_id)
        # Pop the slide panel open
        self.shell.slide_panel.set_widget(self.detail_win, persistent=True)
        self.shell.slide_panel.slide_in()

    def _refresh_all_screens(self):
        try:
            if hasattr(self, "search_win") and self.search_win:
                self.search_win.refresh()
            if hasattr(self, "admin_win") and self.admin_win:
                self.admin_win.refresh()
            if hasattr(self, "clipboard_watcher") and self.clipboard_watcher:
                self.clipboard_watcher.refresh_index()
        except Exception as e:
            print(f"[Auto-Refresh] Error refreshing screens: {e}")

    def _open_new_client_form(self):
        dialog = NewClientDialog(self.db, parent=self.shell)
        dialog.client_created.connect(self._refresh_all_screens)
        dialog.exec()

    def _open_client_editor(self, client_id: int):
        if self.admin_win.open_client_editor(client_id):
            self.shell.set_current_page(1)
            self.shell.slide_panel.slide_out()

    def _delete_client_from_search(self, client_id: int):
        self.admin_win.delete_client(client_id)
        self._refresh_all_screens()

    def _manage_client_services_from_search(self, client_id: int):
        self.admin_win.manage_client_services(client_id)
        self._refresh_all_screens()

    def _archive_client_from_search(self, client_id: int):
        client = self.db.get_client(client_id)
        if not client:
            return
        identity = self.admin_win._get_identity_label(client)
        confirm = QMessageBox.question(
            self.shell, "Archive client",
            f"Archive {identity}? It will be hidden from Search until restored.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return
        self.db.bulk_archive_clients([client_id])
        self.db.log_action(self.actor, "archive", client_id=client_id, detail=f"Archived CLI-{client_id:05d} from Search")
        self.shell.show_action_alert("archive", identity)
        self._refresh_all_screens()

    def _request_admin_mode(self):
        dlg = AdminPinDialog(self.db)
        if dlg.exec() == AdminPinDialog.Accepted:
            self.shell.sidebar.set_admin_mode(True)
            self.shell.sidebar.set_active_navigation(self.shell.sidebar.btn_manage_clients)
            self.search_win.set_admin_mode(True)
            self.admin_win.refresh()
            self.shell.set_current_page(1)
            self.shell.slide_panel.slide_out()
        else:
            self.shell.sidebar.set_admin_mode(False)

    def _exit_admin_mode(self):
        self.shell.dismiss_detail_on_outside = False
        self.shell.sidebar.set_admin_mode(False)
        self.search_win.set_admin_mode(False)
        self.shell.set_current_page(0)
        self.shell.slide_panel.slide_out()

    def _show_manage_clients(self):
        from core.memlog import timed
        with timed("opening manage clients"):
            self._show_manage_clients_now()

    def _show_manage_clients_now(self):
        self.shell.dismiss_detail_on_outside = False
        self.admin_win.refresh()
        self.shell.set_current_page(1)
        self.shell.slide_panel.slide_out()

    def _show_tracker_dump(self):
        self._show_tracker_dump_now()

    def _show_tracker_dump_now(self):
        self.shell.dismiss_detail_on_outside = False
        if hasattr(self, "tracker_dump_win") and self.tracker_dump_win:
            self.shell.set_current_page(self.tracker_dump_win)
            self.shell.sidebar.set_active_navigation(self.shell.sidebar.btn_tracker_dump)
            QTimer.singleShot(0, self._load_tracker_after_switch)
        self.shell.slide_panel.slide_out()

    def _load_tracker_after_switch(self):
        from core.memlog import timed
        with timed("loading the tracker"):
            self.tracker_dump_win.load_data_if_stale()

    def _open_in_slide_panel(self, widget, title: str):
        from PySide6.QtWidgets import QDialog
        self.shell.dismiss_detail_on_outside = True
        if isinstance(widget, QDialog):
            widget.setWindowFlags(Qt.Widget)
            widget.finished.connect(self.shell.slide_panel.slide_out)
        self.shell.slide_panel.set_widget(widget, title)
        self.shell.slide_panel.slide_in()

    def _on_sync_received(self):
        """Called from SyncPeerService background TCP thread when an incoming
        database push has been accepted and written to disk. Emits Qt signal for main thread handling."""
        self.sync_bridge.sync_received_signal.emit()

    def _on_live_sync_received(self, sender_username: str = "", sender_host: str = ""):
        """Called from SyncPeerService background thread. Under v3 (P0-4), live in-place replacement
        is retired; incoming databases are staged and require application restart to swap."""
        self._on_sync_received()

    def _on_join_approval_requested(self, host: str, username: str, code: str) -> bool:
        """Called from SyncPeerService background thread when a join request arrives (P0-6)."""
        event = threading.Event()
        holder = [False]
        self.sync_bridge.join_approval_signal.emit(host, username, code, (event, holder))
        # Modal auto-times out in 120s; wait up to 122s
        if not event.wait(timeout=122.0):
            return False
        return holder[0]

    def _handle_join_approval_modal_main_thread(self, host: str, username: str, code: str, payload: tuple):
        """Main thread GUI handler to show on-screen approval modal for incoming join request (P0-6)."""
        event, holder = payload
        try:
            from ui.dialogs.first_run_dialog import JoinApprovalDialog
            parent = getattr(self, "shell", None)
            dlg = JoinApprovalDialog(host, username, code, timeout_seconds=120, parent=parent)
            res = dlg.exec()
            holder[0] = (res == QDialog.Accepted)
        except Exception as e:
            print(f"[SeraApp] Error showing join approval modal: {e}")
            holder[0] = False
        finally:
            event.set()

    def _handle_live_sync_received_main_thread(self, sender_username: str, sender_host: str):
        """Main thread GUI handler for live auto-sync without app restart."""
        try:
            self._refresh_synced_screens(("dashboard_win", "search_win", "admin_win",
                                          "tracker_dump_win", "detail_win"))
            if hasattr(self, "sidebar") and self.sidebar:
                self.sidebar.notify_sync_received(sender_username, sender_host)
            if hasattr(self, "shell") and self.shell:
                self.shell.show_alert(f"🔄 Database & Tracker auto-synced live from {sender_username} ({sender_host})", level="success", duration=4500)

            # Re-sync allied reports & workbooks in background thread
            threading.Thread(
                target=lambda: (self.db.re_resolve_all_tracker_dumps(),),
                name="sync-live-allied-reports",
                daemon=True
            ).start()
        except Exception as e:
            print(f"[Live Auto-Sync] Error refreshing UI: {e}")

    # ------------------------------------------------------------------
    # Sera Sync v3 engine events (P3-8). ``on_sync_engine_event`` is the ``on_event``
    # callback SyncEngine's public API expects (P3-5 notes); P3-7 passes it in when the
    # engine is wired into the app. It runs on the engine's background thread.
    # ------------------------------------------------------------------

    def on_sync_engine_event(self, kind: str, info: dict):
        if kind == "catch_up_staged":
            # P4-4: this PC was below another PC's compaction floor; a snapshot is staged.
            self.sync_bridge.engine_alert_signal.emit(
                "This PC was away from the office for a long time. Sera Sync has downloaded a fresh "
                "copy of the office data; restart Sera to finish (your own changes are kept).", "warning")
            return
        if kind == "synced":
            # In mode shadow, remote changes went to the replica, not the live DBs -- nothing
            # for the UI to reload, and refreshing anyway would falsely tell the user a live
            # sync happened (P3-8 review, item 5).
            db = getattr(self, "db", None)
            if db is not None and db.get_sync_mode() != "live":
                return
            self._queue_synced_tables(info.get("tables") or ())
        elif kind == "clock_ahead":
            name = info.get("name") or info.get("device_id") or "Peer"
            ahead_ms = info.get("ahead_ms", 0)
            minutes = max(1, round(ahead_ms / 60000))
            dev_id = info.get("device_id") or name

            if not hasattr(self, "_clock_ahead_last_logged") or self._clock_ahead_last_logged is None:
                self._clock_ahead_last_logged = {}
            import time
            now = time.monotonic()
            last = self._clock_ahead_last_logged.get(dev_id)
            if last is None or (now - last[0] >= 3600.0 or last[1] != minutes):
                self._clock_ahead_last_logged[dev_id] = (now, minutes)
                msg = f"PC {name}'s clock is {minutes} minutes ahead"
                if hasattr(self, "sync_service") and self.sync_service:
                    self.sync_service.log_activity("GUARD", msg)

    def _queue_synced_tables(self, tables) -> None:
        """Coalesces touched-table sets from applied sync batches and emits the Qt signal at
        most once per second, so a burst of small sessions doesn't hammer the UI (P3-8)."""
        if not tables:
            return
        import time
        with self._synced_tables_lock:
            self._synced_tables_pending.update(tables)
            elapsed = time.monotonic() - self._synced_tables_last_emit
            if elapsed >= 1.0:
                self._flush_synced_tables_locked()
            elif self._synced_tables_flush_timer is None:
                self._synced_tables_flush_timer = threading.Timer(
                    1.0 - elapsed, self._flush_synced_tables)
                self._synced_tables_flush_timer.daemon = True
                self._synced_tables_flush_timer.start()

    def _flush_synced_tables(self) -> None:
        with self._synced_tables_lock:
            self._flush_synced_tables_locked()

    def _cancel_synced_tables_timer(self) -> None:
        """Stops the trailing-edge flush from firing after the app has started shutting down
        (P3-8 review, minor)."""
        with self._synced_tables_lock:
            if self._synced_tables_flush_timer is not None:
                self._synced_tables_flush_timer.cancel()
                self._synced_tables_flush_timer = None

    def _flush_synced_tables_locked(self) -> None:
        if not self._synced_tables_pending:
            self._synced_tables_flush_timer = None
            return
        import time
        tables = sorted(self._synced_tables_pending)
        self._synced_tables_pending = set()
        self._synced_tables_last_emit = time.monotonic()
        self._synced_tables_flush_timer = None
        self.sync_bridge.engine_synced_signal.emit(tables)

    def _handle_engine_synced_main_thread(self, tables):
        """Main thread GUI handler for SyncEngine's ``synced`` event (P3-8): refreshes only
        the windows relevant to the touched tables, split from the legacy refresh-everything
        path above. No toast -- the sidebar pill alone shows a live sync happened."""
        try:
            targets = set()
            for table in tables:
                targets.update(self._SYNC_TABLE_REFRESH.get(table, ()))
            self._refresh_synced_screens(targets)
            if hasattr(self, "sidebar") and self.sidebar:
                self.sidebar.notify_sync_received("", "")
        except Exception as e:
            print(f"[Sync Engine] Error refreshing UI for tables {tables}: {e}")

    def _handle_sync_sent_main_thread(self, count: int, total: int):
        """Main thread GUI handler when local changes are broadcasted to peers."""
        try:
            if hasattr(self, "sidebar") and self.sidebar:
                self.sidebar.notify_sync_sent(count, total)
            if hasattr(self, "shell") and self.shell and count > 0:
                target_str = f"{count}/{total} workstations" if total > 1 else f"{count} workstation"
                self.shell.show_alert(f"⬆️ Live update synced to {target_str}", level="success", duration=3500)
        except Exception:
            pass

    def _show_sca_diagnostics(self):
        from ui.dialogs.sca_diagnostics_dialog import ScaDiagnosticsDialog
        if not hasattr(self, "sca_diag") or self.sca_diag is None:
            self.sca_diag = ScaDiagnosticsDialog(listener=self.bridge, parent=self.shell,
                                                watcher=getattr(self, "clipboard_watcher", None))
        self.sca_diag.show()
        self.sca_diag.raise_()
        self.sca_diag.activateWindow()

    def _lock_and_force_restart(self):
        """Disables main UI completely and pops a non-dismissable modal dialog requiring application restart."""
        from PySide6.QtWidgets import QVBoxLayout, QLabel, QPushButton
        
        if hasattr(self, "shell") and self.shell:
            self.shell.setEnabled(False)

        if hasattr(self, "sync_service") and self.sync_service:
            try:
                self.sync_service.stop()
            except Exception:
                pass
        if getattr(self, "discovery_service", None):
            try:
                self.discovery_service.stop()
            except Exception:
                pass

        dlg = QDialog(None)
        dlg.setWindowTitle("Sera Sync — Database Received")
        dlg.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.CustomizeWindowHint | Qt.WindowTitleHint)
        dlg.setFixedSize(460, 210)
        dlg.setStyleSheet("QDialog { background-color: #1A232A; color: #FFFFFF; }")

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        lbl_title = QLabel("Database Synchronized Successfully!")
        lbl_title.setStyleSheet("font-size: 16px; font-weight: 700; color: #4CF9B7;")
        layout.addWidget(lbl_title)

        lbl_desc = QLabel(
            "An updated database has been pushed to this workstation from another team member.\n\n"
            "The application MUST restart now to initialize the new database."
        )
        lbl_desc.setWordWrap(True)
        lbl_desc.setStyleSheet("font-size: 13px; color: #E0E0E0;")
        layout.addWidget(lbl_desc)

        btn_restart = QPushButton("Restart Application Now")
        btn_restart.setStyleSheet(
            "QPushButton { background-color: #2E9B5F; color: white; font-weight: 700; "
            "font-size: 14px; padding: 10px 20px; border-radius: 6px; } "
            "QPushButton:hover { background-color: #34B76D; }"
        )
        btn_restart.clicked.connect(dlg.accept)
        layout.addWidget(btn_restart)

        dlg.exec()

        import version
        version.restart_app()

    def run(self):
        sys.exit(self.app.exec())

if __name__ == "__main__":
    _setup_sync_log(APP_DIR)
    SeraApp().run()


