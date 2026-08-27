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

if "--native-host" in sys.argv or any(arg.startswith("chrome-extension://") for arg in sys.argv):
    import native_host.host as nh
    nh.main()
    sys.exit(0)

from PySide6.QtWidgets import QApplication, QMessageBox, QDialog, QSizePolicy, QSystemTrayIcon, QMenu
from ui.shell.app_shell import AppShell
from PySide6.QtGui import QFont, QShortcut, QKeySequence, QIcon, QAction
from PySide6.QtCore import Qt, QObject, Signal, QTimer

try:
    import qtawesome as qta
except Exception:
    qta = None

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
    sync_sent_signal = Signal(int, int)
    capture_processed_signal = Signal(dict, dict)

import security
from database import SeraDatabase
from ui.windows.search_window import SearchWindow
from ui.windows.client_detail_window import ClientDetailWindow
from ui.windows.admin_window import AdminWindow, AdminPinDialog, NewClientDialog
from ui.windows.tracker_dump_window import TrackerDumpWindow
from ui.utils.theme import get_theme_stylesheet
from ui.extension_listener import ExtensionListener

APP_DIR = Path.home() / "AmanAssociates_Sera"

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
                    shutil.copy2(item, dest_file)

        target_ff = APP_DIR / "sera_extension_firefox"
        if source_ff.exists():
            target_ff.mkdir(parents=True, exist_ok=True)
            for item in source_ff.glob("**/*"):
                if item.is_file():
                    rel_path = item.relative_to(source_ff)
                    dest_file = target_ff / rel_path
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, dest_file)

        return target_ext
    except Exception as e:
        print(f"Could not sync permanent extension folder: {e}")
        return APP_DIR / "sera_extension"

def register_native_messaging_host():
    if sys.platform != "win32":
        return
    import winreg
    import json
    import shutil
    try:
        ensure_permanent_extension()
        
        # Use permanent APP_DIR/native_host directory so registry path never points to temporary _MEIPASS
        perm_native_dir = APP_DIR / "native_host"
        perm_native_dir.mkdir(parents=True, exist_ok=True)

        if getattr(sys, 'frozen', False):
            source_native = Path(sys._MEIPASS) / "native_host"
        else:
            source_native = Path(__file__).resolve().parent / "native_host"

        if source_native.exists():
            for item in source_native.glob("*"):
                if item.is_file():
                    try:
                        shutil.copy2(item, perm_native_dir / item.name)
                    except Exception:
                        pass

        json_path = perm_native_dir / "com.amanassociates.sera.json"
        firefox_json_path = perm_native_dir / "com.amanassociates.sera.firefox.json"
        host_bat_path = perm_native_dir / "host.bat"

        # Explicitly configure host.bat with the exact active Python/Executable path
        if not getattr(sys, 'frozen', False):
            python_exe = sys.executable
            bat_content = f'@echo off\n"{python_exe}" -u "%~dp0host.py" %*\n'
            try:
                host_bat_path.write_text(bat_content, encoding="utf-8")
            except Exception:
                pass

        if json_path.exists():
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                data["path"] = str(host_bat_path.resolve())
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
            except Exception as e:
                print(f"Could not update native host JSON path: {e}")

        if firefox_json_path.exists():
            try:
                with open(firefox_json_path, "r", encoding="utf-8") as f:
                    ff_data = json.load(f)
                ff_data["path"] = str(host_bat_path.resolve())
                with open(firefox_json_path, "w", encoding="utf-8") as f:
                    json.dump(ff_data, f, indent=2)
            except Exception as e:
                print(f"Could not update Firefox native host JSON path: {e}")

        targets = []
        if json_path.exists():
            targets.extend([
                (r"Software\Google\Chrome\NativeMessagingHosts\com.amanassociates.sera", str(json_path.resolve())),
                (r"Software\Microsoft\Edge\NativeMessagingHosts\com.amanassociates.sera", str(json_path.resolve())),
            ])
        if firefox_json_path.exists():
            targets.append(
                (r"Software\Mozilla\NativeMessagingHosts\com.amanassociates.sera", str(firefox_json_path.resolve()))
            )
        
        for subkey, target_json in targets:
            try:
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, subkey) as key:
                    winreg.SetValueEx(key, "", 0, winreg.REG_SZ, target_json)
            except Exception:
                pass
    except Exception as e:
        print(f"Could not register native messaging host: {e}")


class SeraApp:
    def __init__(self):
        register_native_messaging_host()

        # Fix Windows Taskbar preview icon grouping & display
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AmanAssociates.ProjectSera.Vault.2.3.3")
            except Exception:
                pass

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
        self.sync_bridge.sync_sent_signal.connect(self._handle_sync_sent_main_thread)
        self.sync_bridge.capture_processed_signal.connect(self._on_capture_processed_ui)
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
        
        self.db_path = str(APP_DIR / "master.db")
        self.salt_path = str(APP_DIR / security.SALT_FILE)
        self.identity_path = APP_DIR / "device_identity.txt"
        
        # Salt initialization
        if not os.path.exists(self.salt_path):
            security.generate_and_save_salt(self.salt_path)
            
        # Auto-unlock vault via stored keyfile or default credentials (no login prompt on launch)
        master_password = self._get_master_password()
        if not master_password:
            sys.exit(0)
            
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

        try:
            loading_dlg.set_status("Decrypting vault & deriving PBKDF2 key...")
            salt = security.load_salt(self.salt_path)
            hex_key = security.derive_key_hex(master_password, salt)
            
            loading_dlg.set_status("Connecting to SQLCipher database & resolving service selectors...")
            self.db = SeraDatabase(self.db_path, hex_key, defer_startup_maintenance=True)

            # Ensure FST, SAD, SCA, and tracker settings are initialized
            if self.db.get_setting("fst_enabled") is None:
                self.db.set_setting("fst_enabled", "1")
            if self.db.get_setting("sad_enabled") is None:
                self.db.set_setting("sad_enabled", "1")
            if self.db.get_setting("sad_browser_notif_enabled") is None:
                self.db.set_setting("sad_browser_notif_enabled", "1")
            if self.db.get_setting("sca_enabled") is None:
                self.db.set_setting("sca_enabled", "1")
            if self.db.get_setting("tracker_enabled") is None:
                self.db.set_setting("tracker_enabled", "1")

            # Initialize Sera Clipboard Assist (SCA) immediately so cold-boot copies are armed
            from clipboard_watch import ClipboardWatchService
            self.clipboard_watcher = ClipboardWatchService(self.db, self.app)
            self.clipboard_watcher.sca_armed.connect(self._on_sca_armed)
            sca_active = (self.db.get_setting("sca_enabled", "1") == "1")
            self.clipboard_watcher.set_enabled(sca_active)
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
            inv_frames=inv_frames_enabled,
            on_sync_received=self._on_sync_received,
            on_live_sync_received=self._on_live_sync_received,
            on_peer_logs_received=self._on_peer_logs_received,
            on_error=lambda msg: print(f"[Sera Sync] {msg}"),
        )
        self.sync_service.start()
        self.db.set_sync_revision_hook(self._broadcast_live_update_to_peers)

        # Check for mandatory updates on GitHub (fast timeout on cold boot)
        loading_dlg.set_status("Checking GitHub for mandatory version updates...")
        import version
        update_info = version.check_for_updates(timeout_seconds=2)
        if update_info:
            loading_dlg.close()
            from ui.dialogs.update_dialog import ForceUpdateDialog
            update_dlg = ForceUpdateDialog(update_info)
            res = update_dlg.exec()

        loading_dlg.set_status("Initializing User Interface...")
        self._build_ui()
        loading_dlg.close()

        # Start extension listener on port 49152
        self.ext_listener = ExtensionListener(self.app)
        self.ext_listener.filing_result_received.connect(self._handle_extension_result)
        self.ext_listener.uncertain_result_received.connect(self._handle_extension_result)
        self.app.aboutToQuit.connect(self.ext_listener.stop)
        self.ext_listener.start()

        # Heavy historical repair/report work is intentionally deferred until
        # after the main window and extension listener are available.
        threading.Thread(
            target=self._run_deferred_startup_maintenance,
            name="sera-startup-maintenance",
            daemon=True,
        ).start()

    def _run_deferred_startup_maintenance(self):
        try:
            self.db.run_startup_maintenance()
            print("[Startup] Deferred maintenance completed")
        except Exception as exc:
            # Startup maintenance is best-effort; the already-open app remains
            # usable and the error is visible in the diagnostic console.
            print(f"[Startup] Deferred maintenance failed: {exc}")

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

    def _on_capture_processed_ui(self, msg: dict, res: dict):
        """Apply only UI updates on the Qt main thread after background work."""
        if not self._capture_ui_refresh_pending:
            self._capture_ui_refresh_pending = True
            QTimer.singleShot(300, self._refresh_tracker_dump_ui)
        portal = msg.get("portal", "Portal")
        arn = msg.get("arn", "N/A")
        capture_method = msg.get("capture_method", "DOM_Tracker")
        method_label = "Sera SAD (API Detector)" if capture_method in ("SAD_API_Interceptor", "SAD_API_Detector") else "Sera DOM (DOM Detector)"
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
        toast_msg = f"Captured {portal} Filing ({method_label}) — {client_display}ARN: {arn}"
        if hasattr(self, "shell") and self.shell and self.shell.isVisible() and not self.shell.isMinimized():
            self.shell.show_toast(toast_msg, duration=5000)
        elif hasattr(self, "tray_icon") and self.tray_icon and self.tray_icon.isVisible():
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

        raw_client_id = msg.get('client_id')
        pan = str(msg.get('pan') or "").strip()
        arn = msg.get('arn', 'N/A')
        portal = msg.get('portal', 'Portal')
        filing_type = str(msg.get('filing_type') or "").strip()
        portal_display = f"{portal} ({filing_type})" if filing_type else portal
        capture_method = msg.get("capture_method", "DOM_Tracker")
        
        # Directly record into Tracker Dump database with authoritative identity resolution
        try:
            res = self.db.insert_tracker_dump(
                client_id=raw_client_id,
                service_id=None,
                portal=portal_display,
                period_label=msg.get("period_label", ""),
                arn_number=arn,
                capture_method=capture_method,
                status=msg.get("status", "submitted"),
                raw_payload_json=json.dumps(msg),
                captured_by=self.actor,
                pan=pan
            )
            print(f"[main._handle_extension_result] Successfully inserted tracker_dump row: {res}")
            return res
        except Exception as e:
            print(f"[Tracker Dump Error] {e}")
            return None

    def _get_master_password(self) -> str:
        key_file = APP_DIR / "sera.key"
        if key_file.exists():
            try:
                pwd = key_file.read_text(encoding="utf-8").strip()
                if pwd:
                    return pwd
            except Exception:
                pass
        
        # Default password check
        default_pwd = "admin123"
        try:
            salt = security.load_salt(self.salt_path)
            hex_key = security.derive_key_hex(default_pwd, salt)
            # Verify if default password unlocks database
            test_db = SeraDatabase(self.db_path, hex_key)
            try:
                key_file.write_text(default_pwd, encoding="utf-8")
            except Exception:
                pass
            return default_pwd
        except Exception:
            pass

        # Fallback: Prompt once if custom password was set, then remember it
        pwd = self._prompt_master_password()
        if pwd:
            try:
                key_file.write_text(pwd, encoding="utf-8")
            except Exception:
                pass
        return pwd

    def _prompt_master_password(self) -> str:
        from PySide6.QtWidgets import QInputDialog, QLineEdit
        password, ok = QInputDialog.getText(
            None, "Aman Associates — Login",
            "Enter Master Password:", QLineEdit.Password
        )

        return password if ok and password else ""

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
        self.shell.sidebar.set_user_name(self.actor_alias)
        
        self.search_win = SearchWindow(self.db)
        self.detail_win = ClientDetailWindow(self.db, actor=self.actor)
        self.admin_win = AdminWindow(self.db, actor=self.actor)
        self.tracker_dump_win = TrackerDumpWindow(self.db)
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

        self.shell.setWindowTitle("Project Sera — Aman Associates")
        self.shell.on_minimized_to_tray = self._show_tray_minimized_hint
        self.shell.on_quit_requested = self._quit_application
        self._setup_system_tray()
        # Apply run_in_background setting so closeEvent behaves correctly from startup
        self._apply_run_in_background()
        self._apply_window_mode()

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

    def _quit_application(self):
        """Full clean application shutdown triggered from the system tray menu or close button."""
        if hasattr(self, "shell") and self.shell:
            self.shell.hide()
            self.shell._force_close = True
            self.shell.close()
        if hasattr(self, "tray_icon") and self.tray_icon:
            self.tray_icon.hide()
        if hasattr(self, "ext_listener") and self.ext_listener:
            try:
                self.ext_listener.stop()
            except Exception:
                pass
        if hasattr(self, "sync_service") and self.sync_service:
            try:
                self.sync_service.stop()
            except Exception:
                pass
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
        self.shell.dismiss_detail_on_outside = False
        self.admin_win.refresh()
        self.shell.set_current_page(1)
        self.shell.slide_panel.slide_out()

    def _show_tracker_dump(self):
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

    def _on_live_sync_received(self, sender_username: str, sender_host: str):
        """Called from SyncPeerService background thread on live data auto-sync."""
        self.sync_bridge.live_sync_received_signal.emit(sender_username, sender_host)

    def _on_peer_logs_received(self, sender_host: str):
        """Called from SyncPeerService background thread when SSAL logs are received."""
        self.sync_bridge.peer_logs_received_signal.emit(sender_host)

    def _handle_live_sync_received_main_thread(self, sender_username: str, sender_host: str):
        """Main thread GUI handler for live auto-sync without app restart."""
        try:
            if hasattr(self, "dashboard_win") and self.dashboard_win:
                self.dashboard_win.refresh()
            if hasattr(self, "search_win") and self.search_win:
                self.search_win.refresh()
            if hasattr(self, "admin_win") and self.admin_win:
                self.admin_win.refresh()
            if hasattr(self, "sidebar") and self.sidebar:
                self.sidebar.notify_sync_received(sender_username, sender_host)
            if hasattr(self, "shell") and self.shell:
                self.shell.show_alert(f"🔄 Database auto-synced live from {sender_username} ({sender_host})", level="success", duration=4500)
        except Exception as e:
            print(f"[Live Auto-Sync] Error refreshing UI: {e}")

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
        """Called by Database write hook to broadcast mutations to LAN peers live and in parallel."""
        if hasattr(self, "sync_service") and self.sync_service:
            import threading
            def push_peer(peer_info, count_ref):
                try:
                    ip = peer_info.get("ip")
                    port = int(peer_info.get("sync_port", 49157))
                    if ip:
                        local_metrics = self.db.get_sync_metrics() if hasattr(self.db, "get_sync_metrics") else {}
                        local_clients = local_metrics.get("client_count", 0)
                        local_rev = local_metrics.get("sync_revision", 0)

                        peer_clients = int(peer_info.get("client_count", 0))
                        peer_rev = int(peer_info.get("sync_revision", 0))

                        # PRE-FLIGHT GUARD: If local database has fewer clients or lower revision than peer,
                        # NEVER push local database to peer. Instead, request peer to send their higher database to us!
                        if local_clients < peer_clients or (local_clients == peer_clients and local_rev < peer_rev):
                            print(f"[Pre-flight Guard] Skipping push to {peer_info.get('host')}: Local DB (clients={local_clients}, rev={local_rev}) < Peer DB (clients={peer_clients}, rev={peer_rev}). Requesting pull...")
                            self.sync_service.request_pull_from(ip, port)
                            return

                        res = self.sync_service.push_to(ip, port, live_update=True)
                        if "successfully" in str(res).lower() or "ok" in str(res).lower():
                            count_ref[0] += 1
                except Exception as e:
                    print(f"[Live Auto-Sync] Push to {peer_info.get('host')} failed: {e}")

            def bg_push_all():
                try:
                    peers = self.sync_service.get_peers()
                    if not peers:
                        return
                    count_ref = [0]
                    threads = []
                    for peer in peers:
                        t = threading.Thread(target=push_peer, args=(peer, count_ref), daemon=True)
                        t.start()
                        threads.append(t)
                    for t in threads:
                        t.join(timeout=3.5)
                    self.sync_bridge.sync_sent_signal.emit(count_ref[0], len(peers))

                    # SSAL: If configured, push recent audit logs to Host PC
                    host_ip = self.db.get_setting("host_ip")
                    if host_ip and self.sync_service:
                        try:
                            recent_logs = self.db.get_audit_logs(limit=100)
                            self.sync_service.push_audit_logs_to_host(host_ip, recent_logs)
                        except Exception as ex:
                            print(f"[SSAL] Auto push logs to host failed: {ex}")
                except Exception as e:
                    print(f"[Live Auto-Sync] Broadcast exception: {e}")

            threading.Thread(target=bg_push_all, daemon=True).start()

    def _show_sca_diagnostics(self):
        from ui.dialogs.sca_diagnostics_dialog import ScaDiagnosticsDialog
        if not hasattr(self, "sca_diag") or self.sca_diag is None:
            self.sca_diag = ScaDiagnosticsDialog(listener=self.ext_listener, parent=self.shell)
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
    if "--native-host" in sys.argv:
        from native_host.host import main as run_native_host
        run_native_host()
        sys.exit(0)
    SeraApp().run()


