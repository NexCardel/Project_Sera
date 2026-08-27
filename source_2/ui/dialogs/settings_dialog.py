"""
settings_dialog.py
-----------------------------
Application-wide settings dialog.
"""

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

try:
    import qtawesome as qta
except Exception:
    qta = None


def _safe_icon(name, color=None):
    if qta:
        try:
            if color:
                return qta.icon(name, color=color)
            return qta.icon(name)
        except Exception:
            pass
    from PySide6.QtGui import QIcon
    return QIcon()


class SettingsDialog(QDialog):
    toast_requested = Signal(str, int)
    settings_saved = Signal()  # Emitted after every successful save

    def __init__(self, db, actor: str = "Admin", parent=None):
        super().__init__(parent)
        self.setObjectName("ToolDialog")
        self.db = db
        self.actor = actor
        self.setWindowTitle("Application Settings — Project Sera")

        self.resize(660, 580)
        self.setMinimumSize(580, 480)
        self.setModal(True)
        
        self.mcl_columns = self.db.get_mcl_columns()
        self.visibility_cbs = {}
        self.quick_copy_cbs = {}
        self.admin_visibility_cbs = {}
        
        self._build_ui()
        self._load_settings()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # Header Frame
        header = QHBoxLayout()
        header.setSpacing(10)
        icon_lbl = QLabel()
        icon_lbl.setPixmap(_safe_icon("mdi.cog-outline", color="#2E9B5F").pixmap(26, 26))
        header.addWidget(icon_lbl)

        title_vbox = QVBoxLayout()
        title_vbox.setSpacing(2)
        title_lbl = QLabel("Application Settings")
        title_lbl.setStyleSheet("font-size: 17px; font-weight: 700; color: #F8FAFC;")
        sub_lbl = QLabel("Configure display preferences, credential masking, automation daemons, and column visibility.")
        sub_lbl.setStyleSheet("font-size: 12px; color: #8E8D88;")
        title_vbox.addWidget(title_lbl)
        title_vbox.addWidget(sub_lbl)
        header.addLayout(title_vbox)
        header.addStretch()
        layout.addLayout(header)

        # Divider
        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet("border: none; border-top: 1px solid #262626; margin: 2px 0;")
        layout.addWidget(divider)

        tabs = QTabWidget()
        tabs.setStyleSheet("""
            QTabWidget::pane {
                background-color: #141414;
                border: 1px solid #262626;
                border-radius: 8px;
                padding: 10px;
            }
            QTabBar::tab {
                background-color: #171717;
                color: #8E8D88;
                border: 1px solid #262626;
                border-bottom: none;
                padding: 8px 16px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                margin-right: 2px;
                font-weight: 600;
                font-size: 12px;
            }
            QTabBar::tab:selected {
                background-color: #141414;
                color: #4CF9B7;
                border-color: #2E9B5F #2E9B5F #141414 #2E9B5F;
            }
            QTabBar::tab:hover:!selected {
                background-color: #1F2933;
                color: #F8FAFC;
            }
        """)
        
        # --- TAB 1: General ---
        tab_general = QWidget()
        scroll_gen = QScrollArea()
        scroll_gen.setWidgetResizable(True)
        scroll_gen.setFrameShape(QFrame.NoFrame)
        gen_content = QWidget()
        form_general = QFormLayout(gen_content)
        form_general.setSpacing(12)
        form_general.setContentsMargins(8, 8, 8, 8)
        
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Light Mode (Default)", "light")
        form_general.addRow("Application Theme:", self.theme_combo)

        self.window_mode_combo = QComboBox()
        self.window_mode_combo.addItem("Fullscreen / Maximized (Default)", "fullscreen")
        self.window_mode_combo.addItem("Square Mode (1:1 Aspect)", "square")
        self.window_mode_combo.addItem("Rectangular Mode (Classic 950x680)", "rectangular")
        form_general.addRow("Window Display Mode:", self.window_mode_combo)

        self.mask_mode_combo = QComboBox()
        self.mask_mode_combo.addItem("Last N Characters Visible (e.g. *****1234)", "last_n")
        self.mask_mode_combo.addItem("First N Characters Visible (e.g. 1234*****)", "first_n")
        self.mask_mode_combo.addItem("Full Dots Masking (●●●●●●●●)", "full_dots")
        form_general.addRow("Password Masking Mode:", self.mask_mode_combo)

        self.reveal_count_spin = QSpinBox()
        self.reveal_count_spin.setRange(1, 10)
        self.reveal_count_spin.setValue(4)
        form_general.addRow("Visible Characters Count:", self.reveal_count_spin)

        self.clipboard_spin = QSpinBox()
        self.clipboard_spin.setRange(5, 300)
        self.clipboard_spin.setSuffix(" seconds")
        self.clipboard_spin.setValue(30)
        form_general.addRow("Auto Clipboard Clear:", self.clipboard_spin)

        self.quick_copy_check = QCheckBox("Enable Quick-Copy (Master Toggle)")
        form_general.addRow("", self.quick_copy_check)

        # Feature Toggles
        self.run_in_bg_check = QCheckBox("Keep app running in background when closed")
        self.run_in_bg_check.setToolTip(
            "When enabled, closing the window minimises Sera to the system tray instead of exiting. "
            "Disable to fully quit the application when the window is closed."
        )
        form_general.addRow("", self.run_in_bg_check)

        self.autostart_check = QCheckBox("Launch Project Sera automatically on Windows PC startup")
        self.autostart_check.setToolTip("Automatically launch Project Sera in background when Windows starts.")
        form_general.addRow("", self.autostart_check)

        self.fst_enabled_check = QCheckBox("Enable Sera DOM (DOM Detector — File Submission Tracker)")
        self.fst_enabled_check.setToolTip("Toggle DOM detector for capturing on-screen filing confirmations from web pages.")
        form_general.addRow("", self.fst_enabled_check)

        self.sad_enabled_check = QCheckBox("Enable Sera SAD (API Detector — File Submission Tracker)")
        self.sad_enabled_check.setToolTip("Toggle passive network API detector (fetch/XHR) for real-time JSON capture from government backends.")
        form_general.addRow("", self.sad_enabled_check)

        self.sad_notif_enabled_check = QCheckBox("Enable SAD In-Browser Toast Notifications")
        self.sad_notif_enabled_check.setToolTip("Toggle on-screen popup toast notification cards on web pages during filing capture.")
        form_general.addRow("", self.sad_notif_enabled_check)

        self.sca_enabled_check = QCheckBox("Enable SCA (Sera Clipboard Assist)")
        self.sca_enabled_check.setToolTip("When copying client User ID from Excel, arms matching credentials for portal interaction.")
        form_general.addRow("", self.sca_enabled_check)

        self.sca_mode_combo = QComboBox()
        self.sca_mode_combo.addItem("Ambient Autofill (Silently fills password on paste)", "autofill")
        self.sca_mode_combo.addItem("SCA Widget (Floating 1-click password prompt on paste)", "widget")
        self.sca_mode_combo.setToolTip("Choose whether SCA silently injects password or presents a 1-click interactive SCA Widget.")
        form_general.addRow("SCA Action Mode:", self.sca_mode_combo)

        self.sca_max_uses_spin = QSpinBox()
        self.sca_max_uses_spin.setRange(1, 20)
        self.sca_max_uses_spin.setSuffix(" uses")
        self.sca_max_uses_spin.setToolTip("Maximum successful SCA fills allowed after one UID is copied.")
        form_general.addRow("SCA Uses per Copied UID:", self.sca_max_uses_spin)
        
        # Primary Key Wiring Status
        id_col = self.db.get_id_column()
        if id_col:
            id_status_text = f"'{id_col['label']}' — Wired to Client Auto-Serial Numbers"
            lbl_id = QLabel(id_status_text)
            lbl_id.setStyleSheet("color: #4CF9B7; font-weight: 700;")
        else:
            lbl_id = QLabel("Not assigned (Can be assigned in Manage Master Column List)")
            lbl_id.setStyleSheet("color: #8E8D88; font-style: italic;")
            
        form_general.addRow("ID / Primary Key Column:", lbl_id)

        scroll_gen.setWidget(gen_content)
        vbox_gen = QVBoxLayout(tab_general)
        vbox_gen.setContentsMargins(0, 0, 0, 0)
        vbox_gen.addWidget(scroll_gen)
        tabs.addTab(tab_general, "General")

        # --- TAB 2: Action Buttons ---
        tab_buttons = QWidget()
        layout_btn = QVBoxLayout(tab_buttons)
        layout_btn.setSpacing(12)
        layout_btn.setContentsMargins(14, 14, 14, 14)

        lbl_btn_info = QLabel("Configure visibility of service action buttons in Client Detail view:")
        lbl_btn_info.setStyleSheet("font-weight: 600; color: #F8FAFC; font-size: 13px;")
        layout_btn.addWidget(lbl_btn_info)

        self.btn_ext_check = QCheckBox("Enable 'Ext' Button (Extension Autofill)")
        self.btn_ext_check.setToolTip("Toggle the Ext (Extension Autofill) button in Client Detail view.")
        layout_btn.addWidget(self.btn_ext_check)

        self.btn_assist_check = QCheckBox("Enable 'Assist' Button (SMTI Manual Assist)")
        self.btn_assist_check.setToolTip("Toggle the Assist (SMTI Manual Assist) button in Client Detail view.")
        layout_btn.addWidget(self.btn_assist_check)

        self.btn_copy_check = QCheckBox("Enable 'Copy' Button (MECP Manual Copy)")
        self.btn_copy_check.setToolTip("Toggle the Copy (MECP Manual Copy) button in Client Detail view.")
        layout_btn.addWidget(self.btn_copy_check)

        self.show_hide_enabled_check = QCheckBox("Enable 'Show/Hide' Password Eye Buttons")
        self.show_hide_enabled_check.setToolTip("Toggle the Show/Hide eye button next to passwords in Client Detail view.")
        layout_btn.addWidget(self.show_hide_enabled_check)

        layout_btn.addStretch()
        tabs.addTab(tab_buttons, "Action Buttons")

        # --- TAB 3: Main Screen Visibility ---
        tab_main_screen = QWidget()
        layout_ms = QVBoxLayout(tab_main_screen)
        layout_ms.addWidget(QLabel("Select which columns should appear on the search grid:"))
        
        scroll_ms = QScrollArea()
        scroll_ms.setWidgetResizable(True)
        scroll_ms.setFrameShape(QFrame.NoFrame)
        content_ms = QWidget()
        form_ms = QVBoxLayout(content_ms)
        
        for col in self.mcl_columns:
            cb = QCheckBox(f"{col['label']} ({col.get('field_type', 'text')})")
            cb.setChecked(col["show_in_search"])
            self.visibility_cbs[col["id"]] = cb
            form_ms.addWidget(cb)
            
        form_ms.addStretch()
        scroll_ms.setWidget(content_ms)
        layout_ms.addWidget(scroll_ms)
        tabs.addTab(tab_main_screen, "Main Screen")
        
        # --- TAB 4: Quick-Copy Permissions ---
        tab_qc = QWidget()
        layout_qc = QVBoxLayout(tab_qc)
        layout_qc.addWidget(QLabel("Select which fields employees can directly click to copy:"))
        
        scroll_qc = QScrollArea()
        scroll_qc.setWidgetResizable(True)
        scroll_qc.setFrameShape(QFrame.NoFrame)
        content_qc = QWidget()
        form_qc = QVBoxLayout(content_qc)
        
        for col in self.mcl_columns:
            cb = QCheckBox(f"{col['label']} ({col.get('field_type', 'text')})")
            cb.setChecked(col["allow_quick_copy"])
            self.quick_copy_cbs[col["id"]] = cb
            form_qc.addWidget(cb)
            
        form_qc.addStretch()
        scroll_qc.setWidget(content_qc)
        layout_qc.addWidget(scroll_qc)
        tabs.addTab(tab_qc, "Quick-Copy")

        # --- TAB 5: Admin Screen Visibility ---
        tab_admin_screen = QWidget()
        layout_as = QVBoxLayout(tab_admin_screen)
        layout_as.addWidget(QLabel("Select which columns should appear on the search grid in Admin Mode:"))
        
        scroll_as = QScrollArea()
        scroll_as.setWidgetResizable(True)
        scroll_as.setFrameShape(QFrame.NoFrame)
        content_as = QWidget()
        form_as = QVBoxLayout(content_as)
        
        for col in self.mcl_columns:
            cb = QCheckBox(f"{col['label']} ({col.get('field_type', 'text')})")
            cb.setChecked(col.get("admin_show_in_search", True))
            self.admin_visibility_cbs[col["id"]] = cb
            form_as.addWidget(cb)
            
        form_as.addStretch()
        scroll_as.setWidget(content_as)
        layout_as.addWidget(scroll_as)
        tabs.addTab(tab_admin_screen, "Admin Screen")

        layout.addWidget(tabs, stretch=1)

        # --- Buttons ---
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        btn_cancel = QPushButton("Cancel")
        btn_cancel.setIcon(_safe_icon("mdi.close", color="#8E8D88"))
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        btn_save = QPushButton("Save Settings")
        btn_save.setProperty("class", "primary")
        btn_save.setIcon(_safe_icon("mdi.check", color="#FFFFFF"))
        btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(btn_save)

        layout.addLayout(btn_row)

    def _load_settings(self):
        current_theme = self.db.get_setting("theme", "light")
        idx_t = self.theme_combo.findData(current_theme)
        if idx_t >= 0:
            self.theme_combo.setCurrentIndex(idx_t)

        current_win_mode = self.db.get_setting("window_mode", "fullscreen")
        idx_wm = self.window_mode_combo.findData(current_win_mode)
        if idx_wm >= 0:
            self.window_mode_combo.setCurrentIndex(idx_wm)

        current_mode = self.db.get_setting("mask_mode", "last_n")
        idx = self.mask_mode_combo.findData(current_mode)
        if idx >= 0:
            self.mask_mode_combo.setCurrentIndex(idx)

        reveal_count = int(self.db.get_setting("mask_reveal_count", "4"))
        self.reveal_count_spin.setValue(reveal_count)

        clipboard_sec = int(self.db.get_setting("clipboard_clear_seconds", "30"))
        self.clipboard_spin.setValue(clipboard_sec)

        quick_copy = self.db.get_setting("quick_copy_enabled", "0")
        self.quick_copy_check.setChecked(quick_copy == "1")

        ext_btn = self.db.get_setting("extension_autofill_enabled", "1")
        self.btn_ext_check.setChecked(ext_btn == "1")

        assist_btn = self.db.get_setting("manual_assist_enabled", "1")
        self.btn_assist_check.setChecked(assist_btn == "1")

        copy_btn = self.db.get_setting("manual_copy_btn_enabled", "1")
        self.btn_copy_check.setChecked(copy_btn == "1")

        show_hide_btn = self.db.get_setting("show_hide_btn_enabled", "1")
        self.show_hide_enabled_check.setChecked(show_hide_btn == "1")

        fst_enabled = self.db.get_setting("fst_enabled", "1")
        self.fst_enabled_check.setChecked(fst_enabled == "1")

        sad_enabled = self.db.get_setting("sad_enabled", "1")
        self.sad_enabled_check.setChecked(sad_enabled == "1")

        sad_notif_enabled = self.db.get_setting("sad_browser_notif_enabled", "1")
        self.sad_notif_enabled_check.setChecked(sad_notif_enabled == "1")

        sca_enabled = self.db.get_setting("sca_enabled", "1")
        self.sca_enabled_check.setChecked(sca_enabled == "1")

        sca_mode = self.db.get_setting("sca_action_mode", "autofill")
        if sca_mode == "assist":
            sca_mode = "widget"
        idx_sm = self.sca_mode_combo.findData(sca_mode)
        if idx_sm >= 0:
            self.sca_mode_combo.setCurrentIndex(idx_sm)
        try:
            self.sca_max_uses_spin.setValue(max(1, min(int(self.db.get_setting("sca_max_uses", "1")), 20)))
        except (TypeError, ValueError):
            self.sca_max_uses_spin.setValue(1)

        run_in_bg = self.db.get_setting("run_in_background", "1")
        self.run_in_bg_check.setChecked(run_in_bg == "1")

        from ui.utils import autostart
        self.autostart_check.setChecked(autostart.is_autostart_enabled())

    def _on_save(self):
        theme = self.theme_combo.currentData()
        win_mode = self.window_mode_combo.currentData()
        mode = self.mask_mode_combo.currentData()
        reveal_count = self.reveal_count_spin.value()
        clipboard_sec = self.clipboard_spin.value()
        quick_copy_val = "1" if self.quick_copy_check.isChecked() else "0"
        ext_btn_val = "1" if self.btn_ext_check.isChecked() else "0"
        assist_btn_val = "1" if self.btn_assist_check.isChecked() else "0"
        copy_btn_val = "1" if self.btn_copy_check.isChecked() else "0"
        show_hide_val = "1" if self.show_hide_enabled_check.isChecked() else "0"
        fst_val = "1" if self.fst_enabled_check.isChecked() else "0"
        sad_val = "1" if self.sad_enabled_check.isChecked() else "0"
        sad_notif_val = "1" if self.sad_notif_enabled_check.isChecked() else "0"
        sca_val = "1" if self.sca_enabled_check.isChecked() else "0"
        sca_mode_val = self.sca_mode_combo.currentData() or "autofill"
        sca_max_uses_val = self.sca_max_uses_spin.value()
        tracker_val = "1" if (fst_val == "1" or sad_val == "1") else "0"
        run_in_bg_val = "1" if self.run_in_bg_check.isChecked() else "0"

        from ui.utils import autostart
        autostart.set_autostart_enabled(self.autostart_check.isChecked())

        try:
            # Save General Settings
            self.db.set_setting("theme", theme)
            self.db.set_setting("window_mode", win_mode)
            self.db.set_setting("mask_mode", mode)
            self.db.set_setting("mask_reveal_count", str(reveal_count))
            self.db.set_setting("clipboard_clear_seconds", str(clipboard_sec))
            self.db.set_setting("quick_copy_enabled", quick_copy_val)
            self.db.set_setting("extension_autofill_enabled", ext_btn_val)
            self.db.set_setting("manual_assist_enabled", assist_btn_val)
            self.db.set_setting("manual_copy_btn_enabled", copy_btn_val)
            self.db.set_setting("show_hide_btn_enabled", show_hide_val)
            self.db.set_setting("fst_enabled", fst_val)
            self.db.set_setting("sad_enabled", sad_val)
            self.db.set_setting("sad_browser_notif_enabled", sad_notif_val)
            self.db.set_setting("sca_enabled", sca_val)
            self.db.set_setting("sca_action_mode", sca_mode_val)
            self.db.set_setting("sca_max_uses", str(sca_max_uses_val))
            self.db.set_setting("tracker_enabled", tracker_val)
            self.db.set_setting("run_in_background", run_in_bg_val)

            # Update extension daemon settings
            from automation import update_extension_settings
            update_extension_settings(
                fst_enabled=(fst_val == "1"),
                sad_enabled=(sad_val == "1"),
                tracker_enabled=(tracker_val == "1"),
                sad_browser_notif_enabled=(sad_notif_val == "1"),
                sca_enabled=(sca_val == "1"),
                sca_mode=sca_mode_val,
                sca_max_uses=sca_max_uses_val
            )
            
            # Save Column Permissions
            visible_ids = [cid for cid, cb in self.visibility_cbs.items() if cb.isChecked()]
            qc_allowed_ids = [cid for cid, cb in self.quick_copy_cbs.items() if cb.isChecked()]
            admin_visible_ids = [cid for cid, cb in self.admin_visibility_cbs.items() if cb.isChecked()]
            
            self.db.bulk_update_mcl_visibility(visible_ids)
            self.db.bulk_update_mcl_quick_copy(qc_allowed_ids)
            self.db.bulk_update_mcl_admin_visibility(admin_visible_ids)

            self.db.log_action(
                self.actor, "update_settings",
                detail=f"Theme: {theme}, WindowMode: {win_mode}, Masking: {mode}, Quick-Copy Master: {quick_copy_val}, FST: {fst_val}, SAD: {sad_val}, Visibility IDs: {len(visible_ids)}, QuickCopy IDs: {len(qc_allowed_ids)}, AdminVisibility IDs: {len(admin_visible_ids)}"
            )

            self.toast_requested.emit("Application settings updated successfully!", 3000)
            self.settings_saved.emit()
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Error Saving Settings", str(e))
