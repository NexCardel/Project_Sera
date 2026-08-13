"""
main.py
-------
App entry point for Project Sera.
"""

import sys
import os
import socket
from pathlib import Path

# Qt probes a couple of legacy Windows bitmap fonts during platform startup;
# suppress that harmless diagnostic while keeping other Qt warnings visible.
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.fonts=false")

if "--native-host" in sys.argv or any(arg.startswith("chrome-extension://") for arg in sys.argv):
    import native_host.host as nh
    nh.main()
    sys.exit(0)

from PySide6.QtWidgets import QApplication, QMessageBox, QDialog, QSizePolicy
from ui.shell.app_shell import AppShell
from PySide6.QtGui import QFont, QShortcut, QKeySequence
from PySide6.QtCore import Qt, QObject, Signal

class SyncSignalBridge(QObject):
    sync_received_signal = Signal()
    live_sync_received_signal = Signal(str, str)
    peer_logs_received_signal = Signal(str)
    sync_sent_signal = Signal(int, int)

import security
from database import SeraDatabase
from ui.windows.search_window import SearchWindow
from ui.windows.client_detail_window import ClientDetailWindow
from ui.windows.admin_window import AdminWindow, AdminPinDialog, NewClientDialog
from ui.windows.dashboard_window import DashboardWindow
from ui.utils.theme import get_theme_stylesheet
from ui.extension_listener import ExtensionListener
from ui.dialogs.filing_confirmation_dialog import FilingConfirmationDialog
from datetime import datetime

APP_DIR = Path.home() / "AmanAssociates_Sera"

def ensure_permanent_extension() -> Path:
    import shutil
    try:
        if getattr(sys, 'frozen', False):
            source_ext = Path(sys._MEIPASS) / "sera_extension"
        else:
            source_ext = Path(__file__).resolve().parent / "sera_extension"

        target_ext = APP_DIR / "sera_extension"
        if source_ext.exists():
            target_ext.mkdir(parents=True, exist_ok=True)
            for item in source_ext.glob("**/*"):
                if item.is_file():
                    rel_path = item.relative_to(source_ext)
                    dest_file = target_ext / rel_path
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
        host_bat_path = perm_native_dir / "host.bat"

        if json_path.exists():
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                data["path"] = str(host_bat_path.resolve())
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
            except Exception as e:
                print(f"Could not update native host JSON path: {e}")

            targets = [
                r"Software\Google\Chrome\NativeMessagingHosts\com.amanassociates.sera",
                r"Software\Microsoft\Edge\NativeMessagingHosts\com.amanassociates.sera",
            ]
            
            for subkey in targets:
                try:
                    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, subkey) as key:
                        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(json_path.resolve()))
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
        APP_DIR.mkdir(parents=True, exist_ok=True)
        
        self.sync_bridge = SyncSignalBridge()
        self.sync_bridge.sync_received_signal.connect(self._lock_and_force_restart)
        self.sync_bridge.live_sync_received_signal.connect(self._handle_live_sync_received_main_thread)
        self.sync_bridge.peer_logs_received_signal.connect(self._handle_peer_logs_received_main_thread)
        self.sync_bridge.sync_sent_signal.connect(self._handle_sync_sent_main_thread)
        
        icon_path = APP_DIR / "assets" / "logo" / "icon_here.ico"
        if not icon_path.exists():
            icon_path = APP_DIR / "assets" / "logo" / "icon_here.png"
        if not icon_path.exists():
            icon_path = APP_DIR / "assets" / "logo" / "sera_icon.png"
        if icon_path.exists():
            from PySide6.QtGui import QIcon
            self.app.setWindowIcon(QIcon(str(icon_path)))
        
        self.db_path = str(APP_DIR / "master.db")
        self.salt_path = str(APP_DIR / security.SALT_FILE)
        self.identity_path = APP_DIR / "device_identity.txt"
        
        # Salt initialization
        if not os.path.exists(self.salt_path):
            security.generate_and_save_salt(self.salt_path)
            
        # Master Password Prompt
        master_password = self._prompt_master_password()
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
            self.db = SeraDatabase(self.db_path, hex_key)
            import threading
            threading.Thread(target=self.db.auto_populate_service_selectors, daemon=True).start()
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
            on_sync_received=self._on_sync_received,
            on_live_sync_received=self._on_live_sync_received,
            on_peer_logs_received=self._on_peer_logs_received,
            on_error=lambda msg: print(f"[Sera Sync] {msg}"),
        )
        self.sync_service.start()
        self.db.set_sync_revision_hook(self._broadcast_live_update_to_peers)

        # Check for mandatory updates on GitHub
        loading_dlg.set_status("Checking GitHub for mandatory version updates...")
        import version
        update_info = version.check_for_updates()
        if update_info:
            loading_dlg.close()
            from ui.dialogs.update_dialog import ForceUpdateDialog
            update_dlg = ForceUpdateDialog(update_info)
            res = update_dlg.exec()
            # If update modal was dismissed or failed, exit process to enforce update
            sys.exit(0)

        self._build_ui()
        loading_dlg.close()

        
        # Start extension listener (only when tracker is enabled)
        if self.db.get_setting("tracker_enabled", "0") == "1":
            self.ext_listener = ExtensionListener(self.app)
            self.ext_listener.filing_result_received.connect(self._handle_extension_result)
            self.ext_listener.uncertain_result_received.connect(self._handle_extension_result)
            self.app.aboutToQuit.connect(self.ext_listener.stop)
            self.ext_listener.start()



    def _handle_extension_result(self, msg: dict):
        client_id = msg.get('client_id')
        if not client_id: return
        
        result_type = msg.get('type')
        arn = msg.get('arn')
        portal = msg.get('portal')
        
        dlg = FilingConfirmationDialog(self.db, client_id, portal, result_type, arn, self.shell)
        if dlg.exec() == QDialog.Accepted:
            ft_id = dlg.get_selected_filing_type_id()
            period = dlg.get_period_label()
            
            if ft_id and period:
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.db.set_filing_status(
                    client_id=client_id, 
                    filing_type_id=ft_id, 
                    period_label=period, 
                    status="submitted", 
                    updated_by=self.actor,
                    arn_number=arn, 
                    submitted_at=now
                )
                QMessageBox.information(self.shell, "Success", f"Saved filing record for period {period}.")
                self.detail_win.load_client(client_id)
            else:
                QMessageBox.warning(self.shell, "Incomplete", "Filing type or period was not provided. Record not saved.")

    def _prompt_master_password(self) -> str:
        from PySide6.QtWidgets import QInputDialog, QLineEdit
        password, ok = QInputDialog.getText(
            None, "Aman Associates — Login",
            "Enter Master Password:", QLineEdit.Password
        )

        return password if ok and password else ""

    def _ensure_user_identity(self) -> tuple[str, str]:
        """Get or prompt for a simple username for this workstation.
        Stores it in device_identity.txt for future launches."""
        from PySide6.QtWidgets import QInputDialog, QLineEdit

        saved_name = ""
        try:
            saved_name = self.identity_path.read_text(encoding="utf-8").strip()
        except OSError:
            pass

        if saved_name:
            return saved_name, saved_name

        # First run on this PC: prompt for a display name
        name_input, ok = QInputDialog.getText(
            None, "Workstation Setup",
            "Enter your name or workstation label (e.g. Rajesh, FrontDesk-1):",
            QLineEdit.Normal, socket.gethostname()
        )
        clean_name = name_input.strip() if ok and name_input.strip() else socket.gethostname()

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
        self.dashboard_win = DashboardWindow(self.db)
        
        self.shell.add_page(self.search_win)     # Index 0
        self.shell.add_page(self.admin_win)      # Index 1
        self.shell.add_page(self.dashboard_win)  # Index 2
        
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
        
        self.detail_win.back_requested.connect(self._show_search_from_detail)
        self.detail_win.toast_requested.connect(self.shell.show_toast)
        self.detail_win.action_alert_requested.connect(self.shell.show_action_alert)
        self.admin_win.back_requested.connect(self._show_search_from_admin)
        self.admin_win.request_slide_panel.connect(self._open_in_slide_panel)
        self.admin_win.toast_requested.connect(self.shell.show_toast)
        self.admin_win.action_alert_requested.connect(self.shell.show_action_alert)
        self.dashboard_win.back_requested.connect(self._show_search_from_detail)
        
        # Sidebar Connections
        sidebar = self.shell.sidebar
        sidebar.go_to_search.connect(self._show_search_from_admin)
        sidebar.action_import_csv.connect(self.admin_win._on_import_csv)
        sidebar.action_download_template.connect(self.admin_win._on_download_template)
        sidebar.action_purge_duplicates.connect(self.admin_win._on_purge_duplicates)
        sidebar.action_drs.connect(self._show_dashboard)
        sidebar.action_manage_clients.connect(self._show_manage_clients)
        sidebar.action_audit_log.connect(self.admin_win._on_view_audit_log)
        sidebar.action_manage_mcl.connect(self.admin_win._on_manage_mcl)
        sidebar.action_manage_services.connect(self.admin_win._on_manage_services)
        sidebar.action_manage_staff.connect(self.admin_win._on_open_sera_sync)
        sidebar.action_open_sera_sync.connect(self.admin_win._on_open_sera_sync)

        sidebar.action_manage_filing_types.connect(self.admin_win._on_manage_filing_types)
        sidebar.action_import_fps.connect(self.admin_win._on_import_fps)
        sidebar.action_export_csv.connect(self.admin_win._on_export_csv)
        sidebar.action_backup.connect(self.admin_win._on_backup)
        sidebar.action_settings.connect(self.admin_win._on_open_settings)
        sidebar.action_restore.connect(self.admin_win._on_restore_backup)
        sidebar.action_enter_admin.connect(self._request_admin_mode)
        sidebar.action_exit_admin.connect(self._exit_admin_mode)
        
        # Global Search Hotkeys (Ctrl+F, Ctrl+K)
        self.sc_ctrl_f = QShortcut(QKeySequence("Ctrl+F"), self.shell)
        self.sc_ctrl_f.activated.connect(self._global_search_shortcut)
        
        self.sc_ctrl_k = QShortcut(QKeySequence("Ctrl+K"), self.shell)
        self.sc_ctrl_k.activated.connect(self._global_search_shortcut)

        # Inject sync service into admin window for Sera Sync dialog
        self.admin_win.set_sync_service(self.sync_service)

        self.shell.setWindowTitle("Project Sera — Aman Associates")
        self._apply_window_mode()

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
        self._apply_theme()
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
            if hasattr(self, "dashboard_win") and self.dashboard_win:
                self.dashboard_win.refresh()
            if hasattr(self, "admin_win") and self.admin_win:
                self.admin_win.refresh()
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

    def _show_dashboard(self):
        self.shell.dismiss_detail_on_outside = False
        self.dashboard_win.refresh()
        self.shell.set_current_page(2)
        self.shell.slide_panel.slide_out()

    def _show_manage_clients(self):
        self.shell.dismiss_detail_on_outside = False
        self.admin_win.refresh()
        self.shell.set_current_page(1)
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
