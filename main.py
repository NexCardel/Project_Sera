"""
main.py
-------
App entry point for Project Sera.
"""

import sys
import os
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
from PySide6.QtCore import Qt

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
    try:
        ensure_permanent_extension()
        if getattr(sys, 'frozen', False):
            base_dir = Path(sys._MEIPASS)
        else:
            base_dir = Path(__file__).resolve().parent
            
        json_path = base_dir / "native_host" / "com.amanassociates.sera.json"
        if not json_path.exists():
            return
            
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
        self.app = QApplication(sys.argv)
        # Avoid Windows legacy bitmap-font fallback warnings (8514oem/Fixedsys)
        # and keep all dialogs consistent with the app stylesheet.
        self.app.setFont(QFont("Segoe UI", 10))
        APP_DIR.mkdir(parents=True, exist_ok=True)
        
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
        self.actor, self.actor_alias = self._ensure_user_actor()

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

    def _ensure_user_actor(self) -> tuple[str, str]:

        from PySide6.QtWidgets import QInputDialog, QLineEdit

        saved_alias = ""
        try:
            saved_alias = self.identity_path.read_text(encoding="utf-8").strip()
        except OSError:
            pass

        if saved_alias:
            return self.db.assign_or_get_alias(saved_alias)

        # On new PC / first run: prompt employee for Display Alias ONLY without exposing secret canonical usernames
        alias_input, ok = QInputDialog.getText(
            None, "Workstation Setup",
            "Enter your Workstation / Display Alias (e.g. FrontDesk-1, TaxStation-A):",
            QLineEdit.Normal, "Station-1"
        )
        clean_alias = alias_input.strip() if ok and alias_input.strip() else "Station-1"
        username, assigned_alias = self.db.assign_or_get_alias(clean_alias)
        
        try:
            self.identity_path.write_text(assigned_alias, encoding="utf-8")
        except OSError:
            pass

        return username, assigned_alias

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
        sidebar.action_manage_staff.connect(self.admin_win._on_manage_staff_users)
        sidebar.action_open_alias_matrix.connect(self.admin_win._on_manage_staff_users)

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

    def _open_new_client_form(self):
        dialog = NewClientDialog(self.db, parent=self.shell)
        dialog.client_created.connect(self.search_win.refresh)
        dialog.exec()

    def _open_client_editor(self, client_id: int):
        if self.admin_win.open_client_editor(client_id):
            self.shell.set_current_page(1)
            self.shell.slide_panel.slide_out()

    def _delete_client_from_search(self, client_id: int):
        self.admin_win.delete_client(client_id)
        self.search_win.refresh()

    def _manage_client_services_from_search(self, client_id: int):
        self.admin_win.manage_client_services(client_id)
        self.search_win.refresh()

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
        self.db.log_action(self.actor, "archive", client_id=client_id, detail="Archived from Search")
        self.shell.show_action_alert("archive", identity)
        self.search_win.refresh()

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

    def run(self):
        sys.exit(self.app.exec())

if __name__ == "__main__":
    SeraApp().run()
