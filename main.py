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
    peer_logs_received_signal = Signal(str)
    tracker_dump_received_signal = Signal(str, int)
    sync_sent_signal = Signal(int, int)
    capture_processed_signal = Signal(dict, dict)
    update_found_signal = Signal(dict)
    update_ready_signal = Signal(str, dict)
    maintenance_done_signal = Signal()
    join_approval_signal = Signal(str, str, str, object)
    # SyncEngine's "synced" event (P3-5/P3-8): sorted list of tables an applied batch touched.
    engine_synced_signal = Signal(list)

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
        self._telemetry_lock = threading.Lock()
        self._telemetry_timer = None
        self._last_telemetry_time = 0.0
        self._telemetry_delay = 60.0
        
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
        
        self.sync_bridge = SyncSignalBridge()
        self.sync_bridge.sync_received_signal.connect(self._lock_and_force_restart)
        self.sync_bridge.live_sync_received_signal.connect(self._handle_live_sync_received_main_thread)
        self.sync_bridge.peer_logs_received_signal.connect(self._handle_peer_logs_received_main_thread)
        self.sync_bridge.tracker_dump_received_signal.connect(self._handle_tracker_dump_received_main_thread)
        self.sync_bridge.sync_sent_signal.connect(self._handle_sync_sent_main_thread)
        self.sync_bridge.capture_processed_signal.connect(self._on_capture_processed_ui)
        self.sync_bridge.update_found_signal.connect(self._handle_update_found)
        self.sync_bridge.update_ready_signal.connect(self._handle_update_ready)
        self.sync_bridge.maintenance_done_signal.connect(self._on_startup_maintenance_done)
        self.sync_bridge.join_approval_signal.connect(self._handle_join_approval_modal_main_thread)
        self.sync_bridge.engine_synced_signal.connect(self._handle_engine_synced_main_thread)
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

        self._run_pending_office_key_migration()
        self._run_pending_rejoin()
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

            # Ensure FST, SDC, SCA, and tracker settings are initialized
            if self.db.get_setting("sdc_enabled") is None:
                self.db.set_setting("sdc_enabled", "1")
            if self.db.get_setting("fst_enabled") is None:
                self.db.set_setting("fst_enabled", "1")
            if self.db.get_setting("sca_enabled") is None:
                self.db.set_setting("sca_enabled", "1")
            if self.db.get_setting("tracker_enabled") is None:
                self.db.set_setting("tracker_enabled", "1")
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
        inv_frames_enabled = False
        if self.db and hasattr(self.db, "get_setting"):
            try:
                inv_frames_enabled = (self.db.get_setting("inv_frames", "0") == "1")
            except Exception:
                pass
        self.sync_service = SyncPeerService(
            db_path=self.db_path,
            salt_path=self.salt_path,
            username=self.actor_alias,
            db=self.db,
            hex_key=hex_key,
            key_id=self.key_id,
            inv_frames=inv_frames_enabled,
            on_sync_received=self._on_sync_received,
            on_live_sync_received=self._on_live_sync_received,
            on_peer_logs_received=self._on_peer_logs_received,
            on_tracker_dump_received=self._on_tracker_dump_received,
            on_error=lambda msg: print(f"[Sera Sync] {msg}"),
            on_join_approval_requested=self._on_join_approval_requested,
        )
        self.sync_service.start()
        self.db.set_sync_revision_hook(self._broadcast_live_update_to_peers)

        # Sera Sync v3 LAN discovery (P2-5/P2-7), office mode only. Shares the beacon socket
        # with the legacy (v2) listener above via on_v3_beacon (P2-5's design) instead of
        # binding port 49156 a second time. Feeds Members / "Add workstation" in the Sera
        # Sync panel (P2-7); Phase 3's session protocol (P3-5) will use it too.
        self.discovery_service = None
        if self.key_mode == "office":
            try:
                import sync_discovery
                import sync_identity
                own_identity = sync_identity.load_device_identity(self.app_dir)
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

                self.db.set_seal_listener(_sync_seal_listener)
                self.sync_engine.start()
                self.app.aboutToQuit.connect(self.sync_engine.stop)
                self.app.aboutToQuit.connect(self._sync_engine_server.stop)
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

        if startup_alerts:
            level = "error" if any(a[0] == "error" for a in startup_alerts) else "warning"
            duration = 0 if any(a[2] == 0 for a in startup_alerts) else max(a[2] for a in startup_alerts)
            self.shell.show_alert("\n".join(a[1] for a in startup_alerts), level=level, duration=duration)

        # Start the WebSocket bridge to the extension (ui/ws_bridge.py). Only the Sera extension's
        # own background page may connect - see that module for how the origin is checked.
        from ui import ws_bridge as _ws_bridge_module
        self.bridge = WSBridge(self.app)
        self.bridge.filing_result_received.connect(self._handle_extension_result)
        self.bridge.uncertain_result_received.connect(self._handle_extension_result)
        self.bridge.session_started_received.connect(self._handle_session_started)
        self.bridge.scc_password_verified_received.connect(self._handle_scc_password_verified)
        self.bridge.sdc_timeline_received.connect(self._handle_sdc_timeline)
        self.bridge.sudr_capture_received.connect(self._handle_sudr_capture)
        self.bridge.extension_settings_updated_received.connect(self._handle_extension_settings_updated)
        self.bridge.settings_provider = self._get_extension_settings_payload
        self.bridge.sca_password_requested.connect(self._handle_sca_password_request)
        self.bridge.sca_fill_result_received.connect(self.clipboard_watcher.handle_fill_result)
        self.app.aboutToQuit.connect(self.bridge.stop)
        self.app.aboutToQuit.connect(self._on_app_about_to_quit)
        self.bridge.start(chrome_manifest_path=self._permanent_extension_dir / "manifest.json")
        # automation.py's module-level functions (autofill, SCA arm, settings push) reach this
        # bridge through here - they have no object of their own to hold a reference on.
        _ws_bridge_module.set_active_bridge(self.bridge)

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
                        win.load_data()
                else:
                    win._on_search_changed()     # redraw only - refresh() would clear the search box
            except Exception as exc:
                print(f"[Startup] Refresh after maintenance failed ({name}): {exc}")

    def _handle_extension_settings_updated(self, msg: dict):
        """Persists extension settings toggled from browser popup into SQLite database and controls VSDC worker."""
        try:
            to_set = {}
            if "vsdc_enabled" in msg:
                vsdc_on = bool(msg["vsdc_enabled"])
                to_set["vsdc_enabled"] = "1" if vsdc_on else "0"
                if hasattr(self, "vsdc_worker") and self.vsdc_worker:
                    if vsdc_on:
                        self.vsdc_worker.resume()
                    else:
                        self.vsdc_worker.pause()
                    print(f"[main] VSDC Worker {'resumed' if vsdc_on else 'paused'} via extension popup toggle")
            if "sdc_enabled" in msg:
                to_set["sdc_enabled"] = "1" if msg["sdc_enabled"] else "0"
            if "fst_enabled" in msg:
                to_set["fst_enabled"] = "1" if msg["fst_enabled"] else "0"
            if "tracker_enabled" in msg:
                to_set["tracker_enabled"] = "1" if msg["tracker_enabled"] else "0"
            if "sca_enabled" in msg:
                to_set["sca_enabled"] = "1" if msg["sca_enabled"] else "0"
            if to_set:
                self.db.set_settings_bulk(to_set)
                print(f"[main] Persisted updated extension settings from popup: {to_set}")
        except Exception as e:
            print(f"[main] Error handling extension_settings_updated: {e}")

    def _get_extension_settings_payload(self) -> dict:
        """Packages current services, settings, registered PANs and SCC configuration."""
        try:
            sdc = self.db.get_setting("sdc_enabled", "1") in ("1", "true", "True")
            fst = self.db.get_setting("fst_enabled", "1") in ("1", "true", "True")
            vsdc = self.db.get_setting("vsdc_enabled", "1") in ("1", "true", "True")
            sca_en = self.db.get_setting("sca_enabled", "1") in ("1", "true", "True")
            sca_mode = self.db.get_setting("sca_action_mode", "autofill")
            try:
                sca_max = int(self.db.get_setting("sca_max_uses", "1"))
            except (ValueError, TypeError):
                sca_max = 1
            reg_pans = self.db.get_all_registered_pans()
            scc_cfg = self.db.get_scc_settings()
            svcs = self.db.get_services()
            return {
                "status": "ok",
                "sdc_enabled": sdc,
                "fst_enabled": fst or sdc,
                "vsdc_enabled": vsdc,
                "tracker_enabled": sdc or fst or vsdc,
                "sca_enabled": sca_en,
                "sca_mode": sca_mode,
                "sca_max_uses": sca_max,
                "allowed_services": svcs,
                "registered_pans": reg_pans,
                "scc_settings": scc_cfg,
            }
        except Exception as e:
            print(f"[main] Failed to get extension settings payload: {e}")
            return {"status": "error", "message": str(e)}

    def _sync_extension_settings(self):
        """Pushes current services, settings, registered PANs and SCC configuration to extension."""
        try:
            from automation import update_extension_settings
            payload = self._get_extension_settings_payload()
            if payload.get("status") == "ok":
                update_extension_settings(
                    fst_enabled=payload.get("fst_enabled", True),
                    sdc_enabled=payload.get("sdc_enabled", True),
                    vsdc_enabled=payload.get("vsdc_enabled", True),
                    tracker_enabled=payload.get("tracker_enabled", True),
                    sca_enabled=payload.get("sca_enabled", True),
                    sca_mode=payload.get("sca_mode", "autofill"),
                    allowed_services=payload.get("allowed_services", []),
                    sca_max_uses=payload.get("sca_max_uses", 1),
                    registered_pans=payload.get("registered_pans", []),
                    scc_settings=payload.get("scc_settings", {}),
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
        # SGT rows are shown on the HUD pill as SGT sees them (tagged "SGT (Shadow)"); a second
        # toast / tray balloon for every SGT row would only double the noise.
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
            self.tracker_dump_win.load_data()

    def _handle_extension_result(self, msg: dict):
        # Queue all extension captures; the database/report pipeline is too
        # expensive to execute in the Qt signal handler during burst traffic.
        if msg.get("type") != "audit_event":
            self._capture_queue.put(msg)
            return
        self._process_extension_result(msg)

    def _process_extension_result(self, msg: dict):
        print(f"[main._handle_extension_result] Processing incoming message: {msg}")
        if msg.get("type") == "audit_event":
            try:
                cid = msg.get("client_id")
                action_name = msg.get("action", "SCA autofill triggered")
                short_act = "SCA Auto" if "autofill" in action_name.lower() else ("SCA Widget" if "widget" in action_name.lower() else "SCA")
                self.db.log_action(
                    actor=self.actor,
                    action=action_name,
                    client_id=cid,
                    detail=msg.get("detail", "")
                )
                if cid:
                    self.db.record_client_activity(int(cid), short_act, msg.get("detail", ""))
                    if hasattr(self, "search_win"):
                        self.search_win._on_search_changed()
            except Exception as e:
                print(f"[main] audit_event error: {e}")
            return

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
                # Enrich payload with Gemini AI extraction on raw text before Tracker Dump insertion
                try:
                    from core.vsdc.vsdc_gemini_parser import enrich_payload_with_gemini
                    enrich_payload_with_gemini(dataset_msg)
                except Exception as gem_e:
                    print(f"[main] Gemini enrichment notice: {gem_e}")

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

            # If payload carried an SCC verified password, ensure client/password is synced/auto-created
            scc_pwd = msg.get("scc_verified_password") or (msg.get("raw_payload", {}).get("scc_verified_password") if isinstance(msg.get("raw_payload"), dict) else None)
            if scc_pwd:
                scc_msg = dict(msg)
                scc_msg["password"] = scc_pwd
                self._handle_scc_password_verified(scc_msg)

            return results[0] if len(results) == 1 else {"datasets": results, "count": len(results)}
        except Exception as e:
            print(f"[Tracker Dump Error] {e}")
            return None

    def _handle_session_started(self, msg: dict):
        """Displays a toast notification when a new client session starts and processes SCC verification if attached."""
        portal = msg.get("portal", "Income Tax")
        pan = str(msg.get("pan") or "").strip()
        name = str(msg.get("client_name") or "").strip()
        
        if hasattr(self, "tray_icon") and self.tray_icon and self.tray_icon.isVisible():
            self.tray_icon.showMessage(
                "Sera SDC Tracking Active", 
                f"Live tracking started for {name} ({pan}) on {portal.upper()}.", 
                QSystemTrayIcon.Information, 
                3000
            )

        # If session_start included an active SCC verified password from link mutation
        if msg.get("scc_verified_password"):
            self._handle_scc_password_verified(msg)

    def _handle_scc_password_verified(self, msg: dict):
        """Persists the verified password from SCC link mutation to master.db."""
        password = str(msg.get("password") or msg.get("scc_verified_password") or "").strip()
        if not password:
            return

        client_id = msg.get("client_id")
        service_id = msg.get("service_id")
        userid = str(msg.get("userid") or msg.get("pan") or "").strip().upper()
        combo_label = str(msg.get("combo_label") or "SCC").strip()
        portal = str(msg.get("portal") or "Income Tax").strip()
        portal_clean = portal.lower()
        # Strictly enforce: SCC is exclusively an ITR-only one-time utility. Ignore any non-ITR portal attempts.
        if "gst" in portal_clean or not any(k in portal_clean for k in ("income", "itr", "tax")):
            print(f"[main.SCC] Ignored non-ITR SCC verification attempt for portal: {portal}")
            return

        try:
            actor = getattr(self, "actor", "Staff")

            try:
                client_id = int(client_id) if client_id is not None else None
            except (ValueError, TypeError):
                client_id = None

            try:
                service_id = int(service_id) if service_id is not None else None
            except (ValueError, TypeError):
                service_id = None

            # Resolve client if client_id is None
            if not client_id and userid:
                client = self.db.get_client_by_pan(userid)
                client_id = client.get("id") if client else None

            # Resolve service & password column
            pwd_col_id = None
            if service_id:
                svc = self.db.get_service(service_id)
                if svc:
                    pwd_col_id = svc.get("password_column_id")

            if not pwd_col_id:
                svc = self.db.get_service_for_portal(portal)
                if svc:
                    service_id = svc.get("id")
                    pwd_col_id = svc.get("password_column_id")

            # Fallback: search MCL columns for ITR password column
            if not pwd_col_id:
                for c in self.db.get_mcl_columns():
                    lbl = (c.get("label") or "").strip().lower()
                    if ("itr" in lbl or "income" in lbl) and "pass" in lbl:
                        pwd_col_id = c["id"]
                        break

            client_name_val = str(msg.get("client_name") or "").strip()

            if not client_id:
                # Unregistered client: auto-create in master.db
                mcl = self.db.get_mcl_columns()
                # 1. Resolve PAN column: check is_internal_pk first, or exact 'pan' in tokens
                pan_col_id = next((c["id"] for c in mcl if c.get("is_internal_pk")), None)
                if not pan_col_id:
                    for c in mcl:
                        lbl_tokens = (c.get("label") or "").lower().split()
                        if "pan" in lbl_tokens:
                            pan_col_id = c["id"]
                            break
                if not pan_col_id:
                    for c in mcl:
                        lbl = (c.get("label") or "").lower()
                        if "pan" in lbl and "pass" not in lbl and "company" not in lbl:
                            pan_col_id = c["id"]
                            break

                # 2. Resolve Name column
                name_col_id = None
                for c in mcl:
                    lbl = (c.get("label") or "").lower()
                    if c["id"] != pan_col_id and any(k in lbl for k in ("company", "name", "client", "proprietor")):
                        name_col_id = c["id"]
                        break

                values = {}
                # Ensure all internal PK columns are populated
                for c in mcl:
                    if c.get("is_internal_pk") and userid:
                        values[c["id"]] = userid

                if pan_col_id and userid:
                    values[pan_col_id] = userid
                if name_col_id:
                    values[name_col_id] = client_name_val or f"Client ({userid})"
                if pwd_col_id:
                    values[pwd_col_id] = password

                svc = self.db.get_service_for_portal(portal)
                svc_ids = [svc["id"]] if svc else []

                client_id = self.db.add_client(
                    values=values,
                    notes="Password verified via SCC",
                    service_ids=svc_ids,
                    actor=actor
                )
                print(f"[main.SCC] Auto-created client record #{client_id} with verified password ({combo_label})")
            else:
                # Existing client: update single password field if column is known
                if pwd_col_id:
                    self.db.update_client_single_field(
                        client_id=client_id,
                        column_id=pwd_col_id,
                        value=password,
                        actor=actor,
                        log_action=True
                    )
                self.db.tag_client_scc_verified(client_id=client_id, combo_label=combo_label, actor=actor)
                print(f"[main.SCC] Updated client #{client_id} password via {combo_label} and marked 'Password verified via SCC'")

            # Resolve client name for toast
            client = self.db.get_client(client_id)
            if not client_name_val and client:
                for c in self.db.get_mcl_columns():
                    lbl = (c.get("label") or "").lower()
                    if "name" in lbl or "client" in lbl or "proprietor" in lbl:
                        val = str(client.get("values", {}).get(c["id"]) or "").strip()
                        if val:
                            client_name_val = val
                            break
            if not client_name_val:
                client_name_val = "Client"

            if hasattr(self, "tray_icon") and self.tray_icon and self.tray_icon.isVisible():
                self.tray_icon.showMessage(
                    "Password Verified via SCC",
                    f"Permanent password saved for {client_name_val} ({userid}).",
                    QSystemTrayIcon.Information,
                    5000
                )

            # Refresh client detail window if currently open for this client
            if hasattr(self, "client_detail_win") and self.client_detail_win and self.client_detail_win.isVisible():
                cur_c = getattr(self.client_detail_win, "client", None)
                if cur_c and cur_c.get("id") == client_id:
                    refreshed = self.db.get_client(client_id)
                    self.client_detail_win.set_client(refreshed)

            # Notify shell / main grid to refresh if visible
            if hasattr(self, "shell") and self.shell and hasattr(self.shell, "refresh_clients"):
                self.shell.refresh_clients()
            if hasattr(self, "search_win") and self.search_win:
                self.search_win._on_search_changed()
            # Push updated registered PANs to extension so newly verified client won't trigger unregistered pop-in
            self._sync_extension_settings()
        except Exception as e:
            print(f"[main._handle_scc_password_verified error] {e}")

    def _handle_sdc_timeline(self, msg: dict):
        """Persists SDC session timeline updates from browser into SQLite database."""
        try:
            res = self.db.upsert_sdc_session_timeline(msg)
            print(f"[main._handle_sdc_timeline] Timeline synced for session {msg.get('session_id')}: {res}")
        except Exception as e:
            print(f"[main._handle_sdc_timeline Error] {e}")

    def _handle_sudr_capture(self, msg: dict):
        """
        Handles the SUDR canonical envelope (see sdcClaude.md §6). Portal-agnostic
        by design: reads only msg['event']['type'] and msg['identity'] — never
        branches on msg['source']['protocol']. This one function is meant to work
        unchanged for ITR, GST, TRACES, or any future portal, as long as that
        portal's protocol.js calls SDC.emit() with the standard envelope shape.

        Stores into the existing tracker_dump table (no schema migration needed):
        raw_payload_json holds the full envelope for now; canonical columns
        (event_type, capture_id) can be promoted later per the migration plan.
        """
        try:
            event = msg.get("event", {}) or {}
            identity = msg.get("identity", {}) or {}
            source = msg.get("source", {}) or {}

            event_type = event.get("type", "UNKNOWN")
            status = event.get("status", "pending")
            portal = source.get("protocol", "unknown")

            # insert_tracker_dump() already does identity resolution internally
            # (pan + raw_payload_json -> candidate matching against master.db) —
            # no need to duplicate that logic here. gstin/tan currently ride
            # along inside raw_payload_json until _extract_identity_candidates_from_payload
            # is extended to also key off them directly (see sdcClaude.md §6.6 step 3).
            self.db.insert_tracker_dump(
                portal=portal,
                arn_number=None,
                capture_method=f"SUDR_{source.get('crosshair_id', '')}",
                status=status,
                raw_payload_json=json.dumps(msg),
                captured_by="sudr",
                pan=identity.get("pan"),
                session_id=msg.get("session_id"),
            )
            print(f"[main._handle_sudr_capture] {portal} :: {event_type} ({status}) capture_id={msg.get('capture_id')}")
        except Exception as e:
            print(f"[main._handle_sudr_capture Error] {e}")

    def _run_pending_office_key_migration(self) -> None:
        """P1-4: finish an interrupted office-key conversion, then run a requested one.

        Runs before any database is opened. On success, _resolve_encryption_key finds
        keys/office.json and continues in office mode; on failure the PC stays legacy.
        """
        import sync_migrate
        app_dir = Path(self.app_dir)
        title = "Aman Associates — Convert to Office Key"

        try:
            outcome = sync_migrate.resume_interrupted_migration(app_dir)
        except sync_migrate.MigrationError as e:
            self._show_office_key_migration_message(
                "critical", title,
                f"An earlier office-key conversion was interrupted and can't be finished automatically:\n\n{e}\n\n"
                "Sera will close so nothing is damaged. Contact the office administrator."
            )
            sys.exit(1)
        if outcome == "completed":
            self._show_office_key_migration_message(
                "info", title, "An interrupted office-key conversion has been completed.")
        elif outcome == "rolled_back":
            self._show_office_key_migration_message(
                "warning", title,
                "An interrupted office-key conversion was undone. This PC still uses its old password key.")

        if sync_migrate.read_migrate_request(app_dir) is None:
            return
        # Consume the request first, so a failure or crash can never loop on every start.
        sync_migrate.clear_migrate_request(app_dir)
        import sera_keys
        if (sera_keys.keys_dir(app_dir) / sera_keys.OFFICE_FILE).exists():
            self._show_office_key_migration_message("info", title, "This PC already uses an office key.")
            return

        details = self._ask_office_key_migration_details(app_dir)
        if not details:
            return

        from PySide6.QtWidgets import QApplication
        busy = QApplication.instance() is not None
        if busy:
            QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            result = sync_migrate.migrate_to_office_key(
                app_dir, details["legacy_password"], details["office_name"],
                new_password=details.get("new_password"),
            )
        except sync_migrate.RowCountMismatch as e:
            self._show_office_key_migration_message(
                "critical", title,
                "The conversion was stopped: the converted database did not contain the same rows "
                f"as the original.\n\n{e}\n\nNothing was changed. This PC keeps its old password key. "
                "Please report this to the owner before trying again."
            )
            return
        except sync_migrate.MigrationRollbackFailed as e:
            self._show_office_key_migration_message(
                "critical", title, f"The conversion failed and could not be undone automatically:\n\n{e}")
            sys.exit(1)
        except Exception as e:
            self._show_office_key_migration_message(
                "warning", title,
                f"The conversion failed and nothing was changed:\n\n{e}\n\n"
                "Sera will continue with the old password key."
            )
            return
        finally:
            if busy:
                QApplication.restoreOverrideCursor()

        self._show_office_key_migration_message(
            "info", title,
            f"This PC now uses the office key for \"{details['office_name']}\".\n\n"
            f"The old files were kept in:\n{result.backup_dir}\n\n"
            "Keep the office master password safe: it is needed to unlock Sera on this PC "
            "if Windows can't, and to add other PCs to the office."
        )

        self._offer_export_recovery_kit(app_dir, details)

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

    def _offer_export_recovery_kit(self, app_dir, details: dict) -> None:
        """Prompt to export recovery kit right after migration (P1-6 / blueprint §6 step 3)."""
        from PySide6.QtWidgets import QMessageBox
        from ui.dialogs.change_master_password_dialog import export_recovery_kit_flow

        reply = QMessageBox.question(
            None,
            "Aman Associates — Export Recovery Kit",
            "Would you like to export a recovery kit to USB now? (Recommended)\n\n"
            "The recovery kit contains encrypted key files to restore access if Windows credentials change.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply == QMessageBox.Yes:
            pwd = details.get("new_password") or details.get("legacy_password")
            export_recovery_kit_flow(None, app_dir, password=pwd, office_name=details.get("office_name"))

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

    def _ask_office_key_migration_details(self, app_dir) -> dict | None:
        import sync_migrate
        from ui.dialogs.office_key_migration_dialog import OfficeKeyMigrationDialog
        dlg = OfficeKeyMigrationDialog(lambda pwd: sync_migrate.check_legacy_password(app_dir, pwd))
        if dlg.exec() != QDialog.Accepted:
            return None
        return dlg.details

    def _show_office_key_migration_message(self, level: str, title: str, text: str) -> None:
        from PySide6.QtWidgets import QMessageBox
        if level == "critical":
            QMessageBox.critical(None, title, text)
        elif level == "warning":
            QMessageBox.warning(None, title, text)
        else:
            QMessageBox.information(None, title, text)

    def _resolve_encryption_key(self) -> tuple[str, str | None, str]:
        """
        Resolves the database encryption key according to blueprint §5 P1-2:
        Order:
          1. apply_pending_swap (P0-4) - executed before this call.
          2. If keys/office.json exists -> office mode:
             dek = load_dek(). On KeyUnavailable, show the recovery dialog (P1-6).
             hex_key = dek_hex(dek).
             Check key_id against office.json.
             Never read or write sera.key in this mode.
          3. Else -> legacy mode: today's path with P0-5.
        Expose self.key_mode ('office'/'legacy') and return (key_mode, key_id, hex_key).
        """
        app_dir = getattr(self, "app_dir", None)
        if not app_dir:
            db_path = getattr(self, "db_path", str(APP_DIR / "master.db"))
            app_dir = Path(db_path).parent

        import sera_keys
        office_path = sera_keys.keys_dir(app_dir) / sera_keys.OFFICE_FILE

        if office_path.exists():
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

        # Legacy Mode
        self.key_mode = "legacy"
        self.key_id = None

        master_password = None
        db_path = getattr(self, "db_path", str(app_dir / "master.db"))
        salt_path = getattr(self, "salt_path", str(app_dir / security.SALT_FILE))

        if not os.path.exists(db_path):
            from ui.dialogs.first_run_dialog import FirstRunDialog
            first_run_dlg = FirstRunDialog(app_dir, getattr(self, "actor_alias", "Admin"))
            if first_run_dlg.exec() != QDialog.Accepted:
                sys.exit(0)
            if getattr(first_run_dlg, "office_mode", False):
                # P2-7: "New Office" / "Join Office" (pairing) wrote keys/office.json and
                # (for New Office) master.db directly -- re-resolve from scratch so this now
                # takes the office-mode branch above instead of deriving a legacy password key
                # from sera.salt, which can't open an office-key-encrypted database. A Join
                # whose pairing succeeded but whose snapshot download didn't still leaves
                # office.json without master.db; the office-mode branch's pending-join check
                # in __init__ (has_pending_join) picks that up on this same call.
                return self._resolve_encryption_key()
            master_password = first_run_dlg.master_password or self._get_master_password()
        else:
            if not os.path.exists(salt_path):
                print(f"[SeraApp Error] Database exists at {db_path} but salt is missing at {salt_path}")
            master_password = self._get_master_password()

        if not master_password:
            if os.path.exists(db_path):
                self._show_startup_auth_error()
            sys.exit(0)

        salt = security.load_salt(salt_path)
        hex_key = security.derive_key_hex(master_password, salt)
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

    def _verify_master_password(self, password: str) -> bool:
        if not password:
            self._last_auth_error = "EMPTY_PASSWORD"
            return False
        db_path = getattr(self, "db_path", str(APP_DIR / "master.db"))
        if not os.path.exists(db_path):
            self._last_auth_error = "MISSING_DB"
            return False
        salt_path = getattr(self, "salt_path", str(Path(db_path).parent / security.SALT_FILE))
        if not os.path.exists(salt_path):
            self._last_auth_error = "MISSING_SALT"
            return False
        try:
            salt = security.load_salt(salt_path)
            hex_key = security.derive_key_hex(password, salt)
            import sqlcipher3.dbapi2 as sqlite3
            conn = None
            try:
                conn = sqlite3.connect(db_path)
                conn.execute(f"PRAGMA key = \"x'{hex_key}'\";")
                conn.execute("SELECT count(*) FROM sqlite_master;").fetchone()
                self._last_auth_error = None
                return True
            except sqlite3.OperationalError as op_err:
                err_str = str(op_err).lower()
                if "locked" in err_str or "busy" in err_str:
                    self._last_auth_error = "DATABASE_LOCKED"
                else:
                    self._last_auth_error = "WRONG_PASSWORD"
                return False
            except Exception:
                self._last_auth_error = "WRONG_PASSWORD"
                return False
            finally:
                if conn:
                    conn.close()
        except Exception as e:
            err_str = str(e).lower()
            if "locked" in err_str or "busy" in err_str:
                self._last_auth_error = "DATABASE_LOCKED"
            else:
                self._last_auth_error = "AUTH_ERROR"
            return False

    def _get_master_password(self) -> str:
        db_path = getattr(self, "db_path", str(APP_DIR / "master.db"))
        app_dir = Path(db_path).parent
        key_file = app_dir / "sera.key"
        self._last_auth_error = None

        # Check existence first: never create a DB as a side effect of the check.
        # If master.db does not exist, first-run DB creation moves to P0-6.
        if not os.path.exists(db_path):
            self._last_auth_error = "MISSING_DB"
            return ""

        salt_path = getattr(self, "salt_path", str(app_dir / security.SALT_FILE))
        if not os.path.exists(salt_path):
            self._last_auth_error = "MISSING_SALT"
            return ""

        prompt_saved_fail_text = (
            "This PC's saved password doesn't open the office database. "
            "Enter the office master password."
        )

        # 1. If sera.key exists, test it
        if key_file.exists():
            saved_pwd = ""
            try:
                saved_pwd = key_file.read_text(encoding="utf-8").strip()
            except Exception:
                pass

            if saved_pwd and self._verify_master_password(saved_pwd):
                return saved_pwd

            if self._last_auth_error == "DATABASE_LOCKED":
                return ""

            # If it fails, show the prompt, up to 3 tries, with the specified text.
            # Save to sera.key only after a password opens the DB.
            for _ in range(3):
                pwd = self._prompt_master_password(prompt_saved_fail_text)
                if not pwd:
                    self._last_auth_error = "CANCELLED"
                    return ""
                if self._verify_master_password(pwd):
                    try:
                        key_file.write_text(pwd, encoding="utf-8")
                    except Exception:
                        pass
                    return pwd
                if self._last_auth_error == "DATABASE_LOCKED":
                    return ""
            self._last_auth_error = "WRONG_PASSWORD"
            return ""

        # 2. When sera.key does not exist, default password check (only when master.db already exists)
        default_pwd = "admin123"
        if self._verify_master_password(default_pwd):
            try:
                key_file.write_text(default_pwd, encoding="utf-8")
            except Exception:
                pass
            return default_pwd

        if self._last_auth_error == "DATABASE_LOCKED":
            return ""

        # Fallback: Prompt if custom password was set without a saved sera.key
        prompt_text = "Enter Master Password:"
        for _ in range(3):
            pwd = self._prompt_master_password(prompt_text)
            if not pwd:
                self._last_auth_error = "CANCELLED"
                return ""
            if self._verify_master_password(pwd):
                try:
                    key_file.write_text(pwd, encoding="utf-8")
                except Exception:
                    pass
                return pwd
            if self._last_auth_error == "DATABASE_LOCKED":
                return ""
        self._last_auth_error = "WRONG_PASSWORD"
        return ""

    def _prompt_master_password(self, prompt_text: str = "Enter Master Password:") -> str:
        from PySide6.QtWidgets import QInputDialog, QLineEdit
        password, ok = QInputDialog.getText(
            None, "Aman Associates — Login",
            prompt_text, QLineEdit.Password
        )

        return password if ok and password else ""

    def _show_startup_auth_error(self):
        from PySide6.QtWidgets import QMessageBox
        err = getattr(self, "_last_auth_error", None)
        if err == "MISSING_SALT":
            title = "Aman Associates — Missing Salt"
            msg = (
                f"The office database salt ({security.SALT_FILE}) is missing.\n\n"
                f"Location: {getattr(self, 'salt_path', security.SALT_FILE)}\n\n"
                "The database cannot be opened without its original salt file. "
                "Please restore sera.salt from a backup or previous installation."
            )
        elif err == "DATABASE_LOCKED":
            title = "Aman Associates — Database Locked"
            msg = (
                "The office database is currently locked by another process.\n\n"
                "Please check if another instance of Sera is already running "
                "or if another application has master.db open."
            )
        elif err == "CANCELLED":
            return
        elif err == "WRONG_PASSWORD":
            title = "Aman Associates — Authentication Failed"
            msg = (
                "Could not open the office database.\n\n"
                "Wrong password, or the database file is damaged. "
                "The maximum number of attempts (3) was exceeded."
            )
        else:
            title = "Aman Associates — Database Error"
            msg = (
                "Could not unlock the office database. "
                "Wrong password, or the database file is damaged."
            )

        QMessageBox.critical(None, title, msg)

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

        self.shell.setWindowTitle("Project Sera — Aman Associates")
        self.shell.on_minimized_to_tray = self._on_window_put_away
        self.shell.on_minimized = lambda: QTimer.singleShot(5_000, lambda: self._trim_if_idle("window minimised"))
        self.shell.on_quit_requested = self._quit_application
        self._setup_system_tray()
        # Apply run_in_background setting so closeEvent behaves correctly from startup
        self._apply_run_in_background()
        self._apply_window_mode()
        memory_mark("  window: tray + shown")

    def _apply_vsdc_engine_settings(self):
        """Apply the Settings -> Tracker switches (VSDC, VSDC-X, VSDC 24/7) to the running worker.

        Called once at startup and again every time the user saves settings, so a switch takes
        effect at once, without a restart. The worker thread only starts once at least one
        engine is on; turning everything off leaves it idle (the router ignores every tick).
        """
        try:
            # The HUD pill switch is independent of the engines: it only decides whether the
            # pill is shown, so it applies even when no engine is on.
            from core.vsdc.vsdc_engines import read_engine_flags, read_hud_enabled, read_sgt_mode, read_sgt_record_pages
            hud = getattr(self, "vsdc_hud", None)
            if hud is not None:
                hud.set_enabled(read_hud_enabled(self.db.get_setting))
            worker = getattr(self, "vsdc_worker", None)
            if worker is None:
                return
            vsdc, vsdc_x, vsdc247 = read_engine_flags(self.db.get_setting)
            sgt = read_sgt_mode(self.db.get_setting)
            worker.router.apply_engine_settings(vsdc, vsdc_x, vsdc247, sgt=sgt,
                                                sgt_record=read_sgt_record_pages(self.db.get_setting))
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

    def _trim_if_idle(self, reason: str) -> None:
        """Hand back touched-once memory, but only while nobody is looking at the window."""
        if self._window_in_use():
            return
        from core.memlog import trim_working_set
        trim_working_set(reason)
        if getattr(self, "_backup_scheduler", None):
            self._backup_scheduler.check_now()

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
        from core.memlog import timed
        with timed("opening the search grid"):
            self.shell.dismiss_detail_on_outside = False
            self.search_win.refresh()
            self.shell.set_current_page(0)
            self.shell.slide_panel.slide_out()

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
        from core.memlog import timed
        with timed("opening the tracker"):
            self._show_tracker_dump_now()

    def _show_tracker_dump_now(self):
        self.shell.dismiss_detail_on_outside = False
        if hasattr(self, "tracker_dump_win") and self.tracker_dump_win:
            self.tracker_dump_win.load_data()
            self.shell.set_current_page(self.tracker_dump_win)
            self.shell.sidebar.set_active_navigation(self.shell.sidebar.btn_tracker_dump)
        self.shell.slide_panel.slide_out()

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

    def _on_peer_logs_received(self, sender_host: str):
        """Called from SyncPeerService background thread when SSAL logs are received."""
        self.sync_bridge.peer_logs_received_signal.emit(sender_host)

    def _on_tracker_dump_received(self, sender_host: str, count: int):
        """Called from SyncPeerService background thread when tracker dumps are received."""
        self.sync_bridge.tracker_dump_received_signal.emit(sender_host, count)

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

    def _handle_tracker_dump_received_main_thread(self, sender_host: str, count: int):
        """Main thread GUI handler when peer tracker dumps are received."""
        try:
            if hasattr(self, "tracker_dump_win") and self.tracker_dump_win:
                self.tracker_dump_win.load_data()
            if hasattr(self, "shell") and self.shell:
                self.shell.show_alert(f"📥 Received {count} filing capture(s) from {sender_host}", level="info", duration=3500)
        except Exception:
            pass

    def _handle_live_sync_received_main_thread(self, sender_username: str, sender_host: str):
        """Main thread GUI handler for live auto-sync without app restart."""
        try:
            if hasattr(self, "dashboard_win") and self.dashboard_win:
                self.dashboard_win.refresh()
            if hasattr(self, "search_win") and self.search_win:
                self.search_win.refresh()
            if hasattr(self, "admin_win") and self.admin_win:
                self.admin_win.refresh()
            if hasattr(self, "tracker_dump_win") and self.tracker_dump_win:
                self.tracker_dump_win.load_data()
            if hasattr(self, "detail_win") and self.detail_win and self.detail_win.isVisible():
                if getattr(self.detail_win, "client_id", None):
                    self.detail_win.load_client(self.detail_win.client_id)
            if hasattr(self, "sidebar") and self.sidebar:
                self.sidebar.notify_sync_received(sender_username, sender_host)
            if hasattr(self, "shell") and self.shell:
                self.shell.show_alert(f"🔄 Database & Tracker auto-synced live from {sender_username} ({sender_host})", level="success", duration=4500)

            # Re-sync allied reports & workbooks in background thread
            threading.Thread(
                target=lambda: (self.db.sync_fst_reports(), self.db.sync_dom_parser(), self.db.re_resolve_all_tracker_dumps()),
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
        if kind == "synced":
            # In mode shadow, remote changes went to the replica, not the live DBs -- nothing
            # for the UI to reload, and refreshing anyway would falsely tell the user a live
            # sync happened (P3-8 review, item 5).
            db = getattr(self, "db", None)
            if db is not None and db.get_sync_mode() != "live":
                return
            self._queue_synced_tables(info.get("tables") or ())

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
            if "dashboard_win" in targets and getattr(self, "dashboard_win", None):
                self.dashboard_win.refresh()
            if "search_win" in targets and getattr(self, "search_win", None):
                self.search_win.refresh()
            if "admin_win" in targets and getattr(self, "admin_win", None):
                self.admin_win.refresh()
            if "tracker_dump_win" in targets and getattr(self, "tracker_dump_win", None):
                self.tracker_dump_win.load_data()
            if "detail_win" in targets and getattr(self, "detail_win", None) and self.detail_win.isVisible():
                if getattr(self.detail_win, "client_id", None):
                    self.detail_win.load_client(self.detail_win.client_id)
            if hasattr(self, "sidebar") and self.sidebar:
                self.sidebar.notify_sync_received("", "")
        except Exception as e:
            print(f"[Sync Engine] Error refreshing UI for tables {tables}: {e}")

    def _handle_peer_logs_received_main_thread(self, sender_host: str):
        """Main thread GUI handler when Host PC receives peer audit logs."""
        try:
            if hasattr(self, "shell") and self.shell:
                self.shell.show_alert(f"📋 SSAL Audit Logs received from {sender_host}", level="info", duration=3000)
        except Exception:
            pass

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

    def _broadcast_live_update_to_peers(self):
        """Called by Database write hook to broadcast mutations to LAN peers live."""
        if hasattr(self, "sync_service") and self.sync_service:
            import time
                
            def do_telemetry_broadcast(is_timer=False):
                if is_timer:
                    with self._telemetry_lock:
                        self._telemetry_timer = None
                        self._last_telemetry_time = time.monotonic()
                try:
                    peers = self.sync_service.get_peers()
                    if not peers:
                        return
                    
                    try:
                        recent_dumps = self.db.get_tracker_dumps(limit=50)
                        if recent_dumps:
                            self.sync_service.broadcast_tracker_dumps(recent_dumps, peers=peers)
                    except Exception as ex:
                        print(f"[SYNC] Broadcast tracker dumps failed: {ex}")

                    try:
                        recent_logs = self.db.get_audit_logs(limit=100)
                        if recent_logs:
                            self.sync_service.broadcast_audit_logs(recent_logs, peers=peers)
                    except Exception as ex:
                        print(f"[SSAL] Broadcast audit logs failed: {ex}")
                except Exception as e:
                    print(f"[Live Auto-Sync] Broadcast exception: {e}")

            with self._telemetry_lock:
                now = time.monotonic()
                time_since_last = now - self._last_telemetry_time
                if time_since_last >= self._telemetry_delay:
                    # Fire immediately if it's been long enough
                    if self._telemetry_timer is None:
                        self._last_telemetry_time = now
                        threading.Thread(target=do_telemetry_broadcast, kwargs={"is_timer": False}, daemon=True).start()
                else:
                    # Within the window, schedule at the end of the current window if not already scheduled
                    if self._telemetry_timer is None:
                        wait_time = self._telemetry_delay - time_since_last
                        self._telemetry_timer = threading.Timer(wait_time, do_telemetry_broadcast, kwargs={"is_timer": True})
                        self._telemetry_timer.daemon = True
                        self._telemetry_timer.start()

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
    SeraApp().run()


