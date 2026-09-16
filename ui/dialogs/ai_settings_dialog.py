"""
ui/dialogs/ai_settings_dialog.py
---------------------------------
Comprehensive Gemini AI Configuration Dialog for Project Sera.
Provides:
- Master On/Off switch for Gemini AI Vision Enrichment
- Multiple Google AI Studio API Key support with auto-failover
- API key input field with show/hide toggle and clipboard paste
- Key health validation, active/primary selection, and deletion
- Model selection & timeout configuration
- Real-time token usage meter and free quota stats
"""

import re
from typing import List, Dict, Any, Optional

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QClipboard, QIcon
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QComboBox, QMessageBox, QFrame, QCheckBox, QSpinBox,
    QApplication, QScrollArea, QWidget
)

try:
    import qtawesome as qta
except Exception:
    qta = None

from core.vsdc.vsdc_gemini_parser import (
    get_gemini_config,
    save_gemini_config,
    test_gemini_api_key,
    fetch_available_gemini_models,
    MODELS_CASCADE,
)
from core.vsdc.vsdc_token_tracker import (
    get_token_usage_summary,
    reset_token_metrics,
)


def _safe_icon(name: str, color: str = "#FFFFFF") -> QIcon:
    if qta:
        try:
            return qta.icon(name, color=color)
        except Exception:
            pass
    return QIcon()


def _mask_key(key: str) -> str:
    """Masks an API key for safe UI display (e.g. AIzaSy...4xK9)."""
    k = str(key).strip()
    if len(k) <= 10:
        return k[:3] + "..." if len(k) > 3 else "***"
    return f"{k[:6]}...{k[-4:]}"


class AISettingsDialog(QDialog):
    """
    Dedicated modal dialog for managing Google Gemini AI Studio settings,
    multi-key failover pools, vision extraction toggles, and token usage meters.
    """
    settings_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("AISettingsDialog")
        self.setWindowTitle("Gemini AI Vision & Extraction Engine — Project Sera")
        self.resize(680, 720)
        self.setMinimumSize(600, 620)
        self.setModal(True)

        self._keys_pool: List[Dict[str, Any]] = []
        self._key_visible = False

        self._build_ui()
        self._load_config()

    def _build_ui(self):
        self.setStyleSheet("""
            QDialog#AISettingsDialog {
                background-color: #0E1117;
                color: #F0F6FC;
                font-family: 'Segoe UI', -apple-system, sans-serif;
            }
            QFrame#HeaderCard {
                background-color: #161B22;
                border: 1px solid #30363D;
                border-radius: 8px;
            }
            QFrame#SectionCard {
                background-color: #161B22;
                border: 1px solid #30363D;
                border-radius: 8px;
            }
            QLabel#TitleLbl {
                font-size: 16px;
                font-weight: 700;
                color: #58A6FF;
            }
            QLabel#SubLbl {
                font-size: 12px;
                color: #8B949E;
            }
            QLabel#SectionHeader {
                font-size: 13px;
                font-weight: 700;
                color: #F0F6FC;
            }
            QLineEdit {
                background-color: #0D1117;
                color: #F0F6FC;
                border: 1px solid #30363D;
                border-radius: 6px;
                padding: 7px 10px;
                font-size: 13px;
            }
            QLineEdit:focus {
                border: 1px solid #58A6FF;
            }
            QComboBox {
                background-color: #0D1117;
                color: #F0F6FC;
                border: 1px solid #30363D;
                border-radius: 6px;
                padding: 6px 30px 6px 10px;
                font-size: 13px;
                min-height: 22px;
            }
            QComboBox:hover {
                border-color: #58A6FF;
            }
            QComboBox:focus {
                border: 1px solid #58A6FF;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 26px;
                border-left: 1px solid #30363D;
                border-top-right-radius: 6px;
                border-bottom-right-radius: 6px;
                background-color: #161B22;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #8B949E;
                width: 0;
                height: 0;
                margin-top: 1px;
            }
            QComboBox::down-arrow:hover {
                border-top-color: #58A6FF;
            }
            QComboBox QAbstractItemView {
                background-color: #161B22;
                color: #F0F6FC;
                selection-background-color: #1F6FEB;
                selection-color: #FFFFFF;
                border: 1px solid #30363D;
                border-radius: 6px;
                padding: 4px;
                outline: none;
            }
            QComboBox QAbstractItemView::item {
                min-height: 26px;
                padding: 4px 8px;
            }
            QComboBox QAbstractItemView::item:hover {
                background-color: #21262D;
            }
            QSpinBox {
                background-color: #0D1117;
                color: #F0F6FC;
                border: 1px solid #30363D;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 13px;
            }
            QSpinBox:focus {
                border: 1px solid #58A6FF;
            }
            QPushButton.TableActionBtn {
                background-color: #21262D;
                color: #F0F6FC;
                border: 1px solid #30363D;
                border-radius: 4px;
                padding: 3px 8px;
                font-size: 11px;
                font-weight: 600;
                min-height: 22px;
            }
            QPushButton.TableActionBtn:hover {
                background-color: #30363D;
                border-color: #58A6FF;
            }
            QPushButton.ActionBtn {
                background-color: #21262D;
                color: #F0F6FC;
                border: 1px solid #30363D;
                border-radius: 6px;
                padding: 7px 14px;
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton.ActionBtn:hover {
                background-color: #30363D;
                border-color: #8B949E;
            }
            QPushButton.PrimaryBtn {
                background-color: #1F6FEB;
                color: #FFFFFF;
                border: 1px solid #388BFD;
                border-radius: 6px;
                padding: 7px 16px;
                font-weight: 700;
                font-size: 13px;
            }
            QPushButton.PrimaryBtn:hover {
                background-color: #388BFD;
            }
            QPushButton.SuccessBtn {
                background-color: #238636;
                color: #FFFFFF;
                border: 1px solid #2EA043;
                border-radius: 6px;
                padding: 7px 16px;
                font-weight: 700;
                font-size: 13px;
            }
            QPushButton.SuccessBtn:hover {
                background-color: #2EA043;
            }
            QPushButton.DangerBtn {
                background-color: #DA3633;
                color: #FFFFFF;
                border: 1px solid #F85149;
                border-radius: 6px;
                padding: 7px 14px;
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton.DangerBtn:hover {
                background-color: #F85149;
            }
            QTableWidget {
                background-color: #0D1117;
                color: #F0F6FC;
                gridline-color: #21262D;
                border: 1px solid #30363D;
                border-radius: 6px;
            }
            QTableWidget::item {
                padding: 4px;
            }
            QHeaderView::section {
                background-color: #161B22;
                color: #8B949E;
                font-weight: 600;
                font-size: 11px;
                border: none;
                border-bottom: 1px solid #30363D;
                padding: 6px;
            }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(18, 18, 18, 18)
        main_layout.setSpacing(14)

        # 1. Header Card
        header_card = QFrame()
        header_card.setObjectName("HeaderCard")
        h_layout = QHBoxLayout(header_card)
        h_layout.setContentsMargins(14, 12, 14, 12)
        h_layout.setSpacing(12)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(_safe_icon("mdi.robot", "#58A6FF").pixmap(32, 32))
        h_layout.addWidget(icon_lbl)

        title_vbox = QVBoxLayout()
        title_vbox.setSpacing(2)
        lbl_title = QLabel("Gemini AI Vision & Extraction Engine")
        lbl_title.setObjectName("TitleLbl")
        lbl_sub = QLabel("Configure Google AI Studio API Keys, model fallback cascading, and real-time vision enrichment.")
        lbl_sub.setObjectName("SubLbl")
        title_vbox.addWidget(lbl_title)
        title_vbox.addWidget(lbl_sub)
        h_layout.addLayout(title_vbox)
        h_layout.addStretch()

        main_layout.addWidget(header_card)

        # Scroll Area for clean dialog height management
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background: transparent;")

        scroll_content = QWidget()
        content_layout = QVBoxLayout(scroll_content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)

        # 2. Master On/Off Switch Card
        master_card = QFrame()
        master_card.setObjectName("SectionCard")
        m_layout = QHBoxLayout(master_card)
        m_layout.setContentsMargins(14, 12, 14, 12)
        m_layout.setSpacing(10)

        self.chk_enable_gemini = QCheckBox("Enable Gemini AI Vision Enrichment")
        self.chk_enable_gemini.setStyleSheet("""
            QCheckBox {
                font-size: 14px;
                font-weight: 700;
                color: #58A6FF;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
            }
        """)
        self.chk_enable_gemini.setChecked(True)
        self.chk_enable_gemini.stateChanged.connect(self._on_master_toggle)
        m_layout.addWidget(self.chk_enable_gemini)

        m_layout.addStretch()

        self.lbl_master_status = QLabel("🟢 Vision Enrichment Active")
        self.lbl_master_status.setStyleSheet("color: #4CF9B7; font-weight: 700; font-size: 12px;")
        m_layout.addWidget(self.lbl_master_status)

        content_layout.addWidget(master_card)

        # 3. Multi-Key Pool Section Card
        keys_card = QFrame()
        keys_card.setObjectName("SectionCard")
        k_layout = QVBoxLayout(keys_card)
        k_layout.setContentsMargins(14, 14, 14, 14)
        k_layout.setSpacing(10)

        lbl_keys_title = QLabel("Google AI Studio API Keys (Multi-Key Pool & Failover)")
        lbl_keys_title.setObjectName("SectionHeader")
        k_layout.addWidget(lbl_keys_title)

        lbl_keys_sub = QLabel(
            "Add multiple Gemini API keys. The engine rotates keys and automatically fails over if a key encounters daily quota limits (HTTP 429)."
        )
        lbl_keys_sub.setObjectName("SubLbl")
        lbl_keys_sub.setWordWrap(True)
        k_layout.addWidget(lbl_keys_sub)

        # Key Input Bar
        input_bar = QHBoxLayout()
        input_bar.setSpacing(6)

        self.txt_new_key = QLineEdit()
        self.txt_new_key.setPlaceholderText("Enter Google AI Studio API Key (AIzaSy...)")
        self.txt_new_key.setEchoMode(QLineEdit.Password)
        input_bar.addWidget(self.txt_new_key, stretch=5)

        self.btn_toggle_vis = QPushButton()
        self.btn_toggle_vis.setProperty("class", "ActionBtn")
        self.btn_toggle_vis.setIcon(_safe_icon("mdi.eye", "#8B949E"))
        self.btn_toggle_vis.setToolTip("Show / Hide API Key Characters")
        self.btn_toggle_vis.clicked.connect(self._toggle_key_visibility)
        input_bar.addWidget(self.btn_toggle_vis)

        btn_paste = QPushButton("Paste")
        btn_paste.setProperty("class", "ActionBtn")
        btn_paste.setIcon(_safe_icon("mdi.clipboard-text-outline", "#FFFFFF"))
        btn_paste.clicked.connect(self._paste_key_from_clipboard)
        input_bar.addWidget(btn_paste)

        btn_add = QPushButton("+ Add Key")
        btn_add.setProperty("class", "PrimaryBtn")
        btn_add.setIcon(_safe_icon("mdi.plus-circle", "#FFFFFF"))
        btn_add.clicked.connect(self._add_key_to_pool)
        input_bar.addWidget(btn_add)

        k_layout.addLayout(input_bar)

        # Table of Configured Keys
        self.tbl_keys = QTableWidget()
        self.tbl_keys.setColumnCount(4)
        self.tbl_keys.setHorizontalHeaderLabels(["API Key (Masked)", "Priority / Status", "Health Check", "Actions"])
        self.tbl_keys.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tbl_keys.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tbl_keys.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tbl_keys.horizontalHeader().setSectionResizeMode(3, QHeaderView.Fixed)
        self.tbl_keys.setColumnWidth(3, 175)
        self.tbl_keys.verticalHeader().setVisible(False)
        self.tbl_keys.verticalHeader().setDefaultSectionSize(40)
        self.tbl_keys.setFixedHeight(150)
        k_layout.addWidget(self.tbl_keys)

        content_layout.addWidget(keys_card)

        # 4. Model Selection & Advanced Settings Card
        settings_card = QFrame()
        settings_card.setObjectName("SectionCard")
        s_layout = QVBoxLayout(settings_card)
        s_layout.setContentsMargins(14, 14, 14, 14)
        s_layout.setSpacing(10)

        lbl_model_title = QLabel("Model Selection & Cascade Fallback")
        lbl_model_title.setObjectName("SectionHeader")
        s_layout.addWidget(lbl_model_title)

        model_form = QHBoxLayout()
        model_form.setSpacing(12)

        lbl_m_choice = QLabel("Primary Model:")
        lbl_m_choice.setStyleSheet("color: #C9D1D9; font-weight: 600; font-size: 12px;")
        model_form.addWidget(lbl_m_choice)

        self.cmb_model = QComboBox()
        self.cmb_model.setEditable(True)
        self.cmb_model.addItems(MODELS_CASCADE)
        model_form.addWidget(self.cmb_model, stretch=3)

        lbl_timeout = QLabel("Timeout (sec):")
        lbl_timeout.setStyleSheet("color: #C9D1D9; font-weight: 600; font-size: 12px;")
        model_form.addWidget(lbl_timeout)

        self.spin_timeout = QSpinBox()
        self.spin_timeout.setRange(3, 30)
        self.spin_timeout.setValue(10)
        model_form.addWidget(self.spin_timeout, stretch=1)

        s_layout.addLayout(model_form)

        self.chk_auto_failover = QCheckBox("Automatically failover across multi-key pool on rate limits (HTTP 429)")
        self.chk_auto_failover.setChecked(True)
        self.chk_auto_failover.setStyleSheet("color: #8B949E; font-size: 12px;")
        s_layout.addWidget(self.chk_auto_failover)

        content_layout.addWidget(settings_card)

        # 5. Token Tracker & Free Quota Meter Card
        meter_card = QFrame()
        meter_card.setObjectName("SectionCard")
        mt_layout = QVBoxLayout(meter_card)
        mt_layout.setContentsMargins(14, 12, 14, 12)
        mt_layout.setSpacing(8)

        m_top = QHBoxLayout()
        lbl_mt_title = QLabel("Google AI Studio Daily Free Quota & Token Meter")
        lbl_mt_title.setObjectName("SectionHeader")
        m_top.addWidget(lbl_mt_title)
        m_top.addStretch()

        btn_reset_meter = QPushButton("Reset Meter")
        btn_reset_meter.setProperty("class", "ActionBtn")
        btn_reset_meter.setIcon(_safe_icon("mdi.refresh", "#8B949E"))
        btn_reset_meter.clicked.connect(self._reset_meter)
        m_top.addWidget(btn_reset_meter)

        mt_layout.addLayout(m_top)

        self.lbl_meter_details = QLabel("Total Calls: 0 | Prompt Tokens: 0 | Response Tokens: 0 | Free Left: 1,500")
        self.lbl_meter_details.setStyleSheet("""
            background-color: #0D1D30;
            color: #58A6FF;
            border: 1px solid #1F6FEB;
            border-radius: 6px;
            padding: 8px 12px;
            font-weight: 700;
            font-size: 12px;
        """)
        mt_layout.addWidget(self.lbl_meter_details)

        content_layout.addWidget(meter_card)

        scroll.setWidget(scroll_content)
        main_layout.addWidget(scroll)

        # 6. Bottom Action Bar
        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(10)

        btn_test_all = QPushButton("Test Active Connection")
        btn_test_all.setProperty("class", "ActionBtn")
        btn_test_all.setIcon(_safe_icon("mdi.lightning-bolt", "#F1E05A"))
        btn_test_all.clicked.connect(self._test_primary_key)
        bottom_bar.addWidget(btn_test_all)

        bottom_bar.addStretch()

        btn_cancel = QPushButton("Cancel")
        btn_cancel.setProperty("class", "ActionBtn")
        btn_cancel.clicked.connect(self.reject)
        bottom_bar.addWidget(btn_cancel)

        btn_save = QPushButton("Save & Apply")
        btn_save.setProperty("class", "SuccessBtn")
        btn_save.setIcon(_safe_icon("mdi.content-save", "#FFFFFF"))
        btn_save.clicked.connect(self._save_and_close)
        bottom_bar.addWidget(btn_save)

        main_layout.addLayout(bottom_bar)

    def _load_config(self):
        """Loads configuration from settings.ini and updates UI."""
        cfg = get_gemini_config()
        self.chk_enable_gemini.setChecked(cfg.get("enabled", True))
        self._on_master_toggle()

        model = cfg.get("primary_model", "gemini-flash-lite-latest")
        idx = self.cmb_model.findText(model)
        if idx >= 0:
            self.cmb_model.setCurrentIndex(idx)
        else:
            self.cmb_model.addItem(model)
            self.cmb_model.setCurrentIndex(self.cmb_model.count() - 1)

        self.spin_timeout.setValue(int(cfg.get("timeout", 10.0)))
        self.chk_auto_failover.setChecked(cfg.get("auto_failover", True))

        # Populate keys pool
        self._keys_pool = []
        for k in cfg.get("api_keys", []):
            self._keys_pool.append({
                "key": k,
                "status": "Untested",
                "message": ""
            })

        self._refresh_keys_table()
        self._refresh_token_meter()

    def _on_master_toggle(self):
        """Updates status label and disables/enables child sections."""
        enabled = self.chk_enable_gemini.isChecked()
        if enabled:
            self.lbl_master_status.setText("🟢 Vision Enrichment Active")
            self.lbl_master_status.setStyleSheet("color: #4CF9B7; font-weight: 700; font-size: 12px;")
        else:
            self.lbl_master_status.setText("⚪ Vision Enrichment Disabled (Offline Regex Only)")
            self.lbl_master_status.setStyleSheet("color: #8B949E; font-weight: 700; font-size: 12px;")

    def _toggle_key_visibility(self):
        """Toggles between password mask and plain text for the key input field."""
        self._key_visible = not self._key_visible
        if self._key_visible:
            self.txt_new_key.setEchoMode(QLineEdit.Normal)
            self.btn_toggle_vis.setIcon(_safe_icon("mdi.eye-off", "#58A6FF"))
        else:
            self.txt_new_key.setEchoMode(QLineEdit.Password)
            self.btn_toggle_vis.setIcon(_safe_icon("mdi.eye", "#8B949E"))

    def _paste_key_from_clipboard(self):
        """Pastes text from system clipboard into key input field."""
        cb = QApplication.clipboard()
        text = cb.text().strip()
        if text:
            self.txt_new_key.setText(text)

    def _add_key_to_pool(self):
        """Validates and adds a new API key to the active key pool."""
        raw_key = self.txt_new_key.text().strip()
        if not raw_key:
            QMessageBox.warning(self, "Empty Key", "Please enter a valid Google AI Studio API key.")
            return

        for entry in self._keys_pool:
            if entry["key"] == raw_key:
                QMessageBox.information(self, "Duplicate Key", "This API key is already in the pool.")
                self.txt_new_key.clear()
                return

        self._keys_pool.append({
            "key": raw_key,
            "status": "Untested",
            "message": ""
        })
        self.txt_new_key.clear()
        self._refresh_keys_table()

    def _refresh_keys_table(self):
        """Renders the keys pool in the table widget."""
        self.tbl_keys.setRowCount(len(self._keys_pool))
        for row_idx, item in enumerate(self._keys_pool):
            key_val = item["key"]
            is_primary = (row_idx == 0)

            # Col 0: Masked Key
            lbl_key = QLabel(_mask_key(key_val))
            lbl_key.setStyleSheet("font-family: monospace; font-weight: 600; color: #F0F6FC; padding-left: 6px;")
            self.tbl_keys.setCellWidget(row_idx, 0, lbl_key)

            # Col 1: Priority / Role
            role_text = "★ Primary" if is_primary else f"Standby #{row_idx + 1}"
            role_color = "#58A6FF" if is_primary else "#8B949E"
            lbl_role = QLabel(role_text)
            lbl_role.setStyleSheet(f"font-weight: 700; color: {role_color}; font-size: 11px;")
            self.tbl_keys.setCellWidget(row_idx, 1, lbl_role)

            # Col 2: Health Status
            st = item.get("status", "Untested")
            if st == "Valid":
                st_html = "<span style='color:#4CF9B7; font-weight:700;'>✓ Active</span>"
            elif st == "Invalid":
                st_html = "<span style='color:#FF6B6B; font-weight:700;'>✗ Error</span>"
            elif st == "Rate Limited":
                st_html = "<span style='color:#F1E05A; font-weight:700;'>⚠ Rate Limited</span>"
            else:
                st_html = "<span style='color:#8B949E;'>⚪ Untested</span>"
            lbl_status = QLabel(st_html)
            self.tbl_keys.setCellWidget(row_idx, 2, lbl_status)

            # Col 3: Row Actions Widget
            act_w = QWidget()
            act_lay = QHBoxLayout(act_w)
            act_lay.setContentsMargins(2, 2, 2, 2)
            act_lay.setSpacing(4)

            btn_test = QPushButton("Test")
            btn_test.setProperty("class", "TableActionBtn")
            btn_test.setIcon(_safe_icon("mdi.check-circle-outline", "#4CF9B7"))
            btn_test.clicked.connect(lambda _, idx=row_idx: self._test_single_key(idx))
            act_lay.addWidget(btn_test)

            if not is_primary:
                btn_make_prim = QPushButton("Primary")
                btn_make_prim.setProperty("class", "TableActionBtn")
                btn_make_prim.setIcon(_safe_icon("mdi.star", "#F1E05A"))
                btn_make_prim.clicked.connect(lambda _, idx=row_idx: self._set_primary_key(idx))
                act_lay.addWidget(btn_make_prim)

            btn_del = QPushButton()
            btn_del.setProperty("class", "TableActionBtn")
            btn_del.setIcon(_safe_icon("mdi.trash-can-outline", "#FF6B6B"))
            btn_del.setToolTip("Remove API Key from Pool")
            btn_del.clicked.connect(lambda _, idx=row_idx: self._remove_key(idx))
            act_lay.addWidget(btn_del)

            self.tbl_keys.setCellWidget(row_idx, 3, act_w)

    def _test_single_key(self, idx: int):
        """Runs a live test ping for a specific key in the pool."""
        if idx < 0 or idx >= len(self._keys_pool):
            return
        entry = self._keys_pool[idx]
        key = entry["key"]
        model = self.cmb_model.currentText()

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            ok, msg = test_gemini_api_key(key, model=model)
            entry["status"] = "Valid" if ok else "Invalid"
            entry["message"] = msg
            QApplication.restoreOverrideCursor()
            if ok:
                # Dynamically enrich model combo with models supported by this project
                try:
                    discovered = fetch_available_gemini_models(key)
                    curr = self.cmb_model.currentText()
                    for d in discovered:
                        if self.cmb_model.findText(d) < 0:
                            self.cmb_model.addItem(d)
                    curr_idx = self.cmb_model.findText(curr)
                    if curr_idx >= 0:
                        self.cmb_model.setCurrentIndex(curr_idx)
                except Exception:
                    pass
                QMessageBox.information(self, "API Key Valid", f"Success for key {_mask_key(key)}:\n\n{msg}")
            else:
                QMessageBox.warning(self, "API Key Verification Failed", f"Failed for key {_mask_key(key)}:\n\n{msg}")
        except Exception as e:
            QApplication.restoreOverrideCursor()
            entry["status"] = "Invalid"
            entry["message"] = str(e)
            QMessageBox.critical(self, "Connection Error", f"Exception testing key: {e}")

        self._refresh_keys_table()

    def _set_primary_key(self, idx: int):
        """Moves the selected key to index 0 (Primary)."""
        if idx <= 0 or idx >= len(self._keys_pool):
            return
        item = self._keys_pool.pop(idx)
        self._keys_pool.insert(0, item)
        self._refresh_keys_table()

    def _remove_key(self, idx: int):
        """Removes a key from the pool."""
        if idx < 0 or idx >= len(self._keys_pool):
            return
        key_masked = _mask_key(self._keys_pool[idx]["key"])
        if QMessageBox.question(
            self, "Confirm Remove Key",
            f"Remove API key {key_masked} from the pool?",
            QMessageBox.Yes | QMessageBox.No
        ) == QMessageBox.Yes:
            self._keys_pool.pop(idx)
            self._refresh_keys_table()

    def _test_primary_key(self):
        """Tests the primary key or first available key."""
        if not self._keys_pool:
            QMessageBox.warning(self, "No Keys Configured", "Please add at least one Gemini API key first.")
            return
        self._test_single_key(0)

    def _refresh_token_meter(self):
        """Updates token meter stats display."""
        try:
            summary = get_token_usage_summary()
            calls = summary.get("total_calls", 0)
            p_tks = summary.get("prompt_tokens", 0)
            c_tks = summary.get("candidate_tokens", 0)
            free_left = max(0, 1500 - calls)
            self.lbl_meter_details.setText(
                f"⚡ Total Calls: {calls:,}  |  Prompt Tokens: {p_tks:,}  |  Response Tokens: {c_tks:,}  |  Daily Free Left: {free_left:,} / 1,500"
            )
        except Exception:
            pass

    def _reset_meter(self):
        """Resets the token tracker metrics."""
        if QMessageBox.question(
            self, "Reset Usage Meter",
            "Reset local Gemini API call counters and token meter for today?",
            QMessageBox.Yes | QMessageBox.No
        ) == QMessageBox.Yes:
            reset_token_metrics()
            self._refresh_token_meter()
            QMessageBox.information(self, "Reset Complete", "Token tracker counters have been reset.")

    def _save_and_close(self):
        """Saves configuration to settings.ini and closes dialog."""
        enabled = self.chk_enable_gemini.isChecked()
        keys = [item["key"] for item in self._keys_pool if item.get("key")]
        model = self.cmb_model.currentText()
        timeout = float(self.spin_timeout.value())

        success = save_gemini_config(
            enabled=enabled,
            api_keys=keys,
            primary_model=model,
            timeout=timeout
        )

        if success:
            self.settings_changed.emit()
            self.accept()
        else:
            QMessageBox.critical(self, "Save Error", "Could not write configuration to settings.ini.")


# Aliases for flexible imports
GeminiSettingsDialog = AISettingsDialog
AIDialog = AISettingsDialog
