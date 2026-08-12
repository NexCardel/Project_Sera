"""
settings_dialog.py
-------------------
Admin dialog for configuring password masking modes, reveal counts, clipboard clearing, and column permissions.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
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


class SettingsDialog(QDialog):
    toast_requested = Signal(str, int)
    
    def __init__(self, db, actor: str = "Admin", parent=None):
        super().__init__(parent)
        self.setObjectName("ToolDialog")
        self.db = db
        self.actor = actor
        self.setWindowTitle("Aman Associates — Application Settings")

        self.resize(500, 450)
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
        layout.setSpacing(14)

        title = QLabel("Application Settings")
        title.setProperty("class", "DialogTitle")
        layout.addWidget(title)

        tabs = QTabWidget()
        
        # --- TAB 1: General ---
        tab_general = QWidget()
        form_general = QFormLayout(tab_general)
        
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Light Mode (Default)", "light")
        self.theme_combo.addItem("Dark Mode (Sleek)", "dark")
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
        form_general.addRow("Auto Clipboard Clear Timeout:", self.clipboard_spin)

        self.quick_copy_check = QCheckBox("Enable Quick-Copy (Master Toggle)")
        form_general.addRow("", self.quick_copy_check)

        self.show_hide_enabled_check = QCheckBox("Enable 'Show/Hide' Password Buttons")
        self.show_hide_enabled_check.setToolTip("Toggle the Show/Hide button next to password credentials in Client Detail view. Uncheck to hide password reveal buttons.")
        form_general.addRow("", self.show_hide_enabled_check)

        self.manual_copy_enabled_check = QCheckBox("Enable Service Manual Credential Copy Buttons")
        self.manual_copy_enabled_check.setToolTip("Toggle the service manual credential copy buttons (e.g. GST — Manual Copy) in Client Detail view. Uncheck to hide manual copy controls.")
        form_general.addRow("", self.manual_copy_enabled_check)

        # Feature Toggles
        separator = QLabel("")
        separator.setProperty("class", "Separator")
        form_general.addRow(separator)

        self.drs_enabled_check = QCheckBox("Enable DRS (Deadline Reminder System)")
        self.drs_enabled_check.setEnabled(True)
        self.drs_enabled_check.setToolTip("Toggle the DRS (Deadline Reminder System) filing status panel.")
        form_general.addRow("", self.drs_enabled_check)

        self.tracker_enabled_check = QCheckBox("Enable Filing Success Tracker (Extension)")
        self.tracker_enabled_check.setEnabled(True)
        self.tracker_enabled_check.setToolTip("Toggle the background Filing Success Tracker IPC listener.")
        form_general.addRow("", self.tracker_enabled_check)
        
        # Primary Key (ID Field) Wiring Status
        id_col = self.db.get_id_column()
        if id_col:
            id_status_text = f"'{id_col['label']}' — Wired to Client ID Tokens (Auto Serial # & Backend Token: CLI-XXXXX)"
            lbl_id = QLabel(id_status_text)
            lbl_id.setStyleSheet("color: #2E9B5F; font-weight: 700;")
        else:
            lbl_id = QLabel("Not assigned (Can be assigned in Manage Master Column List)")
            lbl_id.setStyleSheet("color: #888888; font-style: italic;")
            
        form_general.addRow("ID / Primary Key Column:", lbl_id)

        tabs.addTab(tab_general, "General")

        # --- TAB 2: Main Screen Visibility ---
        tab_main_screen = QWidget()
        layout_ms = QVBoxLayout(tab_main_screen)
        layout_ms.addWidget(QLabel("Select which columns should appear on the search grid:"))
        
        scroll_ms = QScrollArea()
        scroll_ms.setWidgetResizable(True)
        content_ms = QWidget()
        form_ms = QVBoxLayout(content_ms)
        
        for col in self.mcl_columns:
            cb = QCheckBox(f"{col['label']} ({col['field_type']})")
            cb.setChecked(col["show_in_search"])
            self.visibility_cbs[col["id"]] = cb
            form_ms.addWidget(cb)
            
        form_ms.addStretch()
        scroll_ms.setWidget(content_ms)
        layout_ms.addWidget(scroll_ms)
        tabs.addTab(tab_main_screen, "Main Screen")
        
        # --- TAB 3: Quick-Copy Permissions ---
        tab_qc = QWidget()
        layout_qc = QVBoxLayout(tab_qc)
        layout_qc.addWidget(QLabel("Select which fields employees can directly click to copy:"))
        
        scroll_qc = QScrollArea()
        scroll_qc.setWidgetResizable(True)
        content_qc = QWidget()
        form_qc = QVBoxLayout(content_qc)
        
        for col in self.mcl_columns:
            cb = QCheckBox(f"{col['label']} ({col['field_type']})")
            cb.setChecked(col["allow_quick_copy"])
            self.quick_copy_cbs[col["id"]] = cb
            form_qc.addWidget(cb)
            
        form_qc.addStretch()
        scroll_qc.setWidget(content_qc)
        layout_qc.addWidget(scroll_qc)
        tabs.addTab(tab_qc, "Quick-Copy")

        # --- TAB 4: Admin Screen Visibility ---
        tab_admin_screen = QWidget()
        layout_as = QVBoxLayout(tab_admin_screen)
        layout_as.addWidget(QLabel("Select which columns should appear on the search grid in Admin Mode:"))
        
        scroll_as = QScrollArea()
        scroll_as.setWidgetResizable(True)
        content_as = QWidget()
        form_as = QVBoxLayout(content_as)
        
        for col in self.mcl_columns:
            cb = QCheckBox(f"{col['label']} ({col['field_type']})")
            cb.setChecked(col.get("admin_show_in_search", True))
            self.admin_visibility_cbs[col["id"]] = cb
            form_as.addWidget(cb)
            
        form_as.addStretch()
        scroll_as.setWidget(content_as)
        layout_as.addWidget(scroll_as)
        tabs.addTab(tab_admin_screen, "Admin Screen")

        layout.addWidget(tabs)

        # --- Buttons ---
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        btn_save = QPushButton("Save Settings")
        btn_save.setProperty("class", "primary")
        btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(btn_save)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

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

        show_hide_btn = self.db.get_setting("show_hide_btn_enabled", "1")
        self.show_hide_enabled_check.setChecked(show_hide_btn == "1")

        manual_copy_btn = self.db.get_setting("manual_copy_btn_enabled", "1")
        self.manual_copy_enabled_check.setChecked(manual_copy_btn == "1")

        drs_enabled = self.db.get_setting("drs_enabled", "0")
        self.drs_enabled_check.setChecked(drs_enabled == "1")

        tracker_enabled = self.db.get_setting("tracker_enabled", "0")
        self.tracker_enabled_check.setChecked(tracker_enabled == "1")

    def _on_save(self):
        theme = self.theme_combo.currentData()
        win_mode = self.window_mode_combo.currentData()
        mode = self.mask_mode_combo.currentData()
        reveal_count = self.reveal_count_spin.value()
        clipboard_sec = self.clipboard_spin.value()
        quick_copy_val = "1" if self.quick_copy_check.isChecked() else "0"
        show_hide_val = "1" if self.show_hide_enabled_check.isChecked() else "0"
        manual_copy_val = "1" if self.manual_copy_enabled_check.isChecked() else "0"
        drs_val = "1" if self.drs_enabled_check.isChecked() else "0"
        tracker_val = "1" if self.tracker_enabled_check.isChecked() else "0"

        try:
            # Save General Settings
            self.db.set_setting("theme", theme)
            self.db.set_setting("window_mode", win_mode)
            self.db.set_setting("mask_mode", mode)
            self.db.set_setting("mask_reveal_count", str(reveal_count))
            self.db.set_setting("clipboard_clear_seconds", str(clipboard_sec))
            self.db.set_setting("quick_copy_enabled", quick_copy_val)
            self.db.set_setting("show_hide_btn_enabled", show_hide_val)
            self.db.set_setting("manual_copy_btn_enabled", manual_copy_val)
            self.db.set_setting("drs_enabled", drs_val)
            self.db.set_setting("tracker_enabled", tracker_val)
            
            # Broadcast tracker toggle to browser extension immediately
            import automation
            automation.update_extension_settings(tracker_val == "1")
            
            # Save Column Permissions
            visible_ids = [cid for cid, cb in self.visibility_cbs.items() if cb.isChecked()]
            qc_allowed_ids = [cid for cid, cb in self.quick_copy_cbs.items() if cb.isChecked()]
            admin_visible_ids = [cid for cid, cb in self.admin_visibility_cbs.items() if cb.isChecked()]
            
            self.db.bulk_update_mcl_visibility(visible_ids)
            self.db.bulk_update_mcl_quick_copy(qc_allowed_ids)
            self.db.bulk_update_mcl_admin_visibility(admin_visible_ids)

            self.db.log_action(
                self.actor, "update_settings",
                detail=f"Theme: {theme}, WindowMode: {win_mode}, Masking: {mode}, Quick-Copy Master: {quick_copy_val}, DRS: {drs_val}, Tracker: {tracker_val}, Visibility IDs: {len(visible_ids)}, QuickCopy IDs: {len(qc_allowed_ids)}, AdminVisibility IDs: {len(admin_visible_ids)}"
            )


            self.toast_requested.emit("Application settings updated successfully!", 3000)
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Error Saving Settings", str(e))
