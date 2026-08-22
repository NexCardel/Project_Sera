"""
client_detail_window.py
------------------------
Window 2: shows client info, masked passwords, and autofill buttons.
"""

import webbrowser

from PySide6.QtCore import Qt, QTimer, QSize, Signal
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import automation
try:
    import qtawesome as qta
except Exception:
    qta = None
from automation import _AutofillBridge
from ui.utils.masking import mask_password
from pathlib import Path

BACK_ICON = str(Path(__file__).resolve().parents[2] / "assets" / "icons" / "arrow_back_24dp_E3E3E3_FILL0_wght400_GRAD0_opsz24.svg")


_ICON_CACHE = {}

def _cached_icon(name: str, color: str = None) -> QIcon:
    key = (name, color)
    if key not in _ICON_CACHE:
        if qta:
            try:
                _ICON_CACHE[key] = qta.icon(name, color=color) if color else qta.icon(name)
            except Exception:
                _ICON_CACHE[key] = QIcon()
        else:
            _ICON_CACHE[key] = QIcon()
    return _ICON_CACHE[key]


def _get_service_icon(name: str) -> str:
    n = (name or "").lower()
    if "gst" in n:
        return "mdi.percent-outline"
    elif "income" in n or "tax" in n or "itr" in n:
        return "mdi.file-document-outline"
    elif "email" in n or "mail" in n:
        return "mdi.email-outline"
    elif "tds" in n:
        return "mdi.percent"
    elif "mca" in n or "company" in n:
        return "mdi.bank-outline"
    elif "pf" in n or "provident" in n:
        return "mdi.account-group-outline"
    elif "esi" in n:
        return "mdi.asterisk"
    elif "pt" in n or "professional" in n:
        return "mdi.map-marker-outline"
    else:
        return "mdi.web"


def _get_portal_accent_color(name: str) -> str:
    n = (name or "").lower()
    if "gst" in n:
        return "#D85A30"
    elif "income" in n or "tax" in n or "itr" in n:
        return "#378ADD"
    elif "email" in n or "mail" in n:
        return "#7F77DD"
    elif "tds" in n or "traces" in n:
        return "#E6A23C"
    elif "pf" in n or "provident" in n:
        return "#2E9B5F"
    return "#5DCAA5"


class ClickableLabel(QLabel):

    clicked = Signal()
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

class ClientDetailWindow(QWidget):
    back_requested = Signal()
    toast_requested = Signal(str, int)
    action_alert_requested = Signal(str, str)
    MANUAL_POPUP_DELAY_MS = 3000

    def __init__(self, db, actor: str = "Staff"):
        super().__init__()
        self.db = db
        self.actor = actor
        self.client = None
        self._bridge = _AutofillBridge()
        self._bridge.failed.connect(self._on_autofill_failed)
        self._service_shortcuts = []
        self._is_navigating_back = False
        self._build_ui()

        self.back_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self.back_shortcut.activated.connect(self._safe_back_request)

    def _build_ui(self):
        self.setStyleSheet("background-color: #1C1C1C;")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 14, 24, 14)
        outer.setSpacing(8)

        # Header Bar: Back Button | Identity Title (2-line) | Token Badge
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 2)
        header.setSpacing(12)

        self.back_btn = QPushButton()
        self.back_btn.setFixedSize(28, 28)
        self.back_btn.setToolTip("Back (Esc)")
        self.back_btn.setIcon(qta.icon("mdi.arrow-left", color="#B4B2A9") if qta else QIcon(BACK_ICON))
        self.back_btn.setIconSize(QSize(16, 16))
        self.back_btn.setProperty("class", "GhostIconButton")
        self.back_btn.setCursor(Qt.PointingHandCursor)
        self.back_btn.clicked.connect(self._safe_back_request)
        header.addWidget(self.back_btn)

        title_vbox = QVBoxLayout()
        title_vbox.setContentsMargins(0, 0, 0, 0)
        title_vbox.setSpacing(1)

        self.primary_name_lbl = QLabel()
        self.primary_name_lbl.setStyleSheet("font-size: 14px; font-weight: 600; color: #F5F5F0;")
        title_vbox.addWidget(self.primary_name_lbl)

        self.secondary_name_lbl = QLabel()
        self.secondary_name_lbl.setStyleSheet("font-size: 11px; color: #888780;")
        title_vbox.addWidget(self.secondary_name_lbl)

        header.addLayout(title_vbox, stretch=1)

        self.token_badge = QLabel()
        self.token_badge.setCursor(Qt.PointingHandCursor)
        self.token_badge.setStyleSheet("""
            QLabel {
                background-color: #123B31;
                color: #5DCAA5;
                font-size: 10px;
                font-weight: 600;
                padding: 3px 10px;
                border-radius: 10px;
            }
            QLabel:hover {
                background-color: #16483C;
            }
        """)
        header.addWidget(self.token_badge)
        outer.addLayout(header)

        # Top Hairline Divider
        top_divider = QFrame()
        top_divider.setFrameShape(QFrame.HLine)
        top_divider.setStyleSheet("background-color: #2A2A2A; min-height: 1px; max-height: 1px; border: none;")
        outer.addWidget(top_divider)

        # Scroll Area for Profile Content (Seamless on #1C1C1C)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.scroll_widget = QWidget()
        self.scroll_widget.setStyleSheet("background: transparent;")
        self.scroll_layout = QVBoxLayout(self.scroll_widget)
        self.scroll_layout.setContentsMargins(4, 8, 4, 12)
        self.scroll_layout.setSpacing(12)
        scroll.setWidget(self.scroll_widget)
        outer.addWidget(scroll, stretch=1)

    def _safe_back_request(self):
        if getattr(self, "_is_navigating_back", False):
            return
        if hasattr(self, "_notes_timer") and self._notes_timer.isActive():
            self._notes_timer.stop()
            self._auto_save_notes()
        self._is_navigating_back = True
        self.back_btn.setEnabled(False)
        self.back_shortcut.setEnabled(False)
        self.back_requested.emit()
        QTimer.singleShot(300, self._restore_back_inputs)

    def _copy_token_to_clipboard(self, token: str):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(token)
        self.toast_requested.emit(f"Copied Client Token: {token}", 2000)

    def _restore_back_inputs(self):
        self._is_navigating_back = False
        self.back_btn.setEnabled(True)
        self.back_shortcut.setEnabled(True)

    def _clear_layout(self, layout):
        if layout is not None:
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
                elif item.layout():
                    self._clear_layout(item.layout())

    def load_client(self, client_id: int):
        self.scroll_widget.setUpdatesEnabled(False)
        try:
            self._load_client_internal(client_id)
            try:
                self.db.record_client_activity(client_id, "Viewed", "Opened profile")
            except Exception:
                pass
        finally:
            self.scroll_widget.setUpdatesEnabled(True)

    def _load_client_internal(self, client_id: int):
        self._clear_layout(self.scroll_layout)

        for shortcut in self._service_shortcuts:
            shortcut.setParent(None)
            shortcut.deleteLater()
        self._service_shortcuts = []

        self.client = self.db.get_client(client_id)
        if not self.client:
            QMessageBox.warning(self, "Not found", "That client record no longer exists.")
            self.back_requested.emit()
            return

        self.db.log_action(self.actor, "view", client_id=client_id)

        # Header Titles and Token
        primary, secondary = self._get_identity_parts(self.client)
        self.primary_name_lbl.setText(primary)
        if secondary:
            self.secondary_name_lbl.setText(secondary)
            self.secondary_name_lbl.show()
        else:
            self.secondary_name_lbl.hide()
        
        token = self.client.get("client_id_token") or str(client_id)
        self.token_badge.setText(f"CLI-{token.zfill(5)}" if token.isdigit() else f"ID: {token}")
        self.token_badge.setToolTip(f"Client ID: {token}\nClick to copy to clipboard")
        self.token_badge.mousePressEvent = lambda event, t=token: self._copy_token_to_clipboard(t)
        
        from PySide6.QtWidgets import QGridLayout
        
        mask_mode = self.db.get_setting("mask_mode", "last_n")
        reveal_count = int(self.db.get_setting("mask_reveal_count", "4"))
        show_hide_btn_enabled = self.db.get_setting("show_hide_btn_enabled", "1") == "1"
        
        timeout_sec = 30
        try:
            timeout_sec = int(self.db.get_setting("clipboard_clear_seconds", "30"))
        except Exception:
            pass

        mcl_cols = self.db.get_mcl_columns()
        identity_cols = [c for c in mcl_cols if c["field_type"] != "password"]
        security_cols = [c for c in mcl_cols if c["field_type"] == "password"]

        # ======================================================================
        # 1. Identity Section (Flowing 2-Column Grid on Canvas)
        # ======================================================================
        if identity_cols:
            sec_lbl = QLabel("IDENTITY")
            sec_lbl.setStyleSheet("font-size: 10px; font-weight: 600; letter-spacing: 0.5px; color: #6E6D67;")
            self.scroll_layout.addWidget(sec_lbl)

            id_grid = QGridLayout()
            id_grid.setContentsMargins(0, 2, 0, 6)
            id_grid.setHorizontalSpacing(24)
            id_grid.setVerticalSpacing(10)

            id_row, id_col = 0, 0
            for col in identity_cols:
                raw_val = self.client["values"].get(col["id"], "")
                allow_qc = col.get("allow_quick_copy", True)

                field_vbox = QVBoxLayout()
                field_vbox.setContentsMargins(0, 0, 0, 0)
                field_vbox.setSpacing(2)

                lbl = QLabel(col['label'])
                lbl.setStyleSheet("font-size: 10px; color: #7A7972;")
                field_vbox.addWidget(lbl)

                val_row = QHBoxLayout()
                val_row.setContentsMargins(0, 0, 0, 0)
                val_row.setSpacing(6)

                if raw_val:
                    if allow_qc:
                        val_label = ClickableLabel(raw_val)
                        val_label.clicked.connect(
                            lambda v=raw_val, lbl_name=col['label'], is_sec=False, t=timeout_sec: self._on_label_clicked(v, lbl_name, is_sec, t)
                        )
                        val_label.setCursor(Qt.PointingHandCursor)
                        val_label.setToolTip(f"Click to copy {col['label']}")
                    else:
                        val_label = QLabel(raw_val)
                    val_label.setStyleSheet("font-size: 12px; font-weight: 500; color: #E8E8E3;")
                    val_row.addWidget(val_label, stretch=1)

                    if allow_qc:
                        btn_copy = QPushButton()
                        btn_copy.setFixedSize(22, 22)
                        btn_copy.setProperty("class", "GhostIconButton")
                        btn_copy.setToolTip(f"Copy {col['label']}")
                        btn_copy.setIcon(_cached_icon("mdi.content-copy", "#8E8D88"))
                        btn_copy.setIconSize(QSize(13, 13))
                        btn_copy.clicked.connect(
                            lambda _, v=raw_val, lbl_name=col['label'], is_sec=False, t=timeout_sec: self._copy_to_clipboard(v, lbl_name, is_sec, t)
                        )
                        val_row.addWidget(btn_copy)
                else:
                    val_label = QLabel("Not set")
                    val_label.setStyleSheet("font-size: 12px; color: #57564F;")
                    val_row.addWidget(val_label, stretch=1)

                field_vbox.addLayout(val_row)
                id_grid.addLayout(field_vbox, id_row, id_col)

                id_col += 1
                if id_col > 1:
                    id_col = 0
                    id_row += 1

            self.scroll_layout.addLayout(id_grid)

            # Section Divider
            div1 = QFrame()
            div1.setFrameShape(QFrame.HLine)
            div1.setStyleSheet("background-color: #2A2A2A; min-height: 1px; max-height: 1px; border: none;")
            self.scroll_layout.addWidget(div1)

        # ======================================================================
        # 2. Security Credentials Section (2-Column Grid on Canvas)
        # ======================================================================
        if security_cols:
            sec_lbl2 = QLabel("SECURITY CREDENTIALS")
            sec_lbl2.setStyleSheet("font-size: 10px; font-weight: 600; letter-spacing: 0.5px; color: #6E6D67;")
            self.scroll_layout.addWidget(sec_lbl2)

            sec_grid = QGridLayout()
            sec_grid.setContentsMargins(0, 2, 0, 6)
            sec_grid.setHorizontalSpacing(24)
            sec_grid.setVerticalSpacing(8)

            sec_row, sec_col = 0, 0
            for idx, col in enumerate(security_cols):
                raw_val = self.client["values"].get(col["id"], "")
                allow_qc = col.get("allow_quick_copy", True)

                field_row_widget = QWidget()
                fr_layout = QHBoxLayout(field_row_widget)
                fr_layout.setContentsMargins(0, 4, 0, 4)
                fr_layout.setSpacing(6)

                text_vbox = QVBoxLayout()
                text_vbox.setContentsMargins(0, 0, 0, 0)
                text_vbox.setSpacing(2)

                lbl = QLabel(col['label'])
                lbl.setStyleSheet("font-size: 10px; color: #7A7972;")
                text_vbox.addWidget(lbl)

                if raw_val:
                    masked_val = mask_password(raw_val, mask_mode, reveal_count)
                    if allow_qc:
                        val_label = ClickableLabel(masked_val)
                        val_label.clicked.connect(
                            lambda v=raw_val, lbl_name=col['label'], is_sec=True, t=timeout_sec: self._on_label_clicked(v, lbl_name, is_sec, t)
                        )
                        val_label.setCursor(Qt.PointingHandCursor)
                        val_label.setToolTip(f"Click to copy {col['label']}")
                    else:
                        val_label = QLabel(masked_val)
                    val_label.setStyleSheet("font-size: 12px; font-weight: 500; color: #E8E8E3;")
                else:
                    masked_val = "Not set"
                    val_label = QLabel("Not set")
                    val_label.setStyleSheet("font-size: 12px; color: #57564F;")

                text_vbox.addWidget(val_label)
                fr_layout.addLayout(text_vbox, stretch=1)

                if raw_val:
                    btn_group = QHBoxLayout()
                    btn_group.setContentsMargins(0, 0, 0, 0)
                    btn_group.setSpacing(2)

                    if show_hide_btn_enabled:
                        btn_show = QPushButton()
                        btn_show.setFixedSize(24, 24)
                        btn_show.setProperty("class", "GhostIconButton")
                        btn_show.setToolTip("Show / Hide Password")
                        btn_show.setIcon(_cached_icon("mdi.eye-outline", "#8E8D88"))
                        btn_show.setIconSize(QSize(14, 14))
                        btn_show.setCheckable(True)
                        btn_show.toggled.connect(
                            lambda checked, lbl_w=val_label, m=masked_val, r=raw_val, btn_w=btn_show: (
                                lbl_w.setText(r if checked else m),
                                btn_w.setIcon(_cached_icon("mdi.eye-off-outline" if checked else "mdi.eye-outline", "#FFFFFF" if checked else "#8E8D88"))
                            )
                        )
                        btn_group.addWidget(btn_show)

                    if allow_qc:
                        btn_copy = QPushButton()
                        btn_copy.setFixedSize(24, 24)
                        btn_copy.setProperty("class", "GhostIconButton")
                        btn_copy.setToolTip("Copy Password")
                        btn_copy.setIcon(_cached_icon("mdi.content-copy", "#8E8D88"))
                        btn_copy.setIconSize(QSize(13, 13))
                        btn_copy.clicked.connect(
                            lambda _, v=raw_val, lbl_name=col['label'], is_sec=True, t=timeout_sec: self._copy_to_clipboard(v, lbl_name, is_sec, t)
                        )
                        btn_group.addWidget(btn_copy)

                    fr_layout.addLayout(btn_group)

                sec_grid.addWidget(field_row_widget, sec_row, sec_col)
                sec_col += 1
                if sec_col > 1:
                    sec_col = 0
                    sec_row += 1

            self.scroll_layout.addLayout(sec_grid)

            # Section Divider
            div2 = QFrame()
            div2.setFrameShape(QFrame.HLine)
            div2.setStyleSheet("background-color: #2A2A2A; min-height: 1px; max-height: 1px; border: none;")
            self.scroll_layout.addWidget(div2)

        # ======================================================================
        # 3. Services Section (Flowing List on Canvas)
        # ======================================================================
        services = self.db.get_client_services(client_id)
        if services:
            ext_setting = self.db.get_setting("extension_autofill_enabled", "1") == "1"
            assist_setting = self.db.get_setting("manual_assist_enabled", "1") == "1"
            copy_setting = self.db.get_setting("manual_copy_btn_enabled", "1") == "1"

            # Header row above services
            svc_hdr = QHBoxLayout()
            svc_hdr.setContentsMargins(0, 0, 0, 0)
            lbl_svc_sec = QLabel("SERVICES")
            lbl_svc_sec.setStyleSheet("font-size: 10px; font-weight: 600; letter-spacing: 0.5px; color: #6E6D67;")
            svc_hdr.addWidget(lbl_svc_sec)
            svc_hdr.addStretch()

            shortcut_info = QLabel("ⓘ shortcuts: Alt+1..9")
            shortcut_info.setStyleSheet("color: #6E6D67; font-size: 10.5px;")
            shortcut_info.setToolTip(
                "Shortcuts:\n"
                "Alt+1..9 = Extension Autofill\n"
                "Alt+Ctrl+1..9 = Manual Assist\n"
                "Alt+Shift+1..9 = Manual Copy"
            )
            svc_hdr.addWidget(shortcut_info)
            self.scroll_layout.addLayout(svc_hdr)

            svc_list_layout = QVBoxLayout()
            svc_list_layout.setContentsMargins(0, 0, 0, 4)
            svc_list_layout.setSpacing(0)

            for i, s in enumerate(services):
                key_num = i + 1
                has_shortcut = key_num <= 9

                portal_mode = s.get("automation_mode", "extension")
                is_manual_only = (portal_mode == "manual")

                ext_on = ext_setting and not is_manual_only
                assist_on = assist_setting
                copy_on = copy_setting

                row_widget = QWidget()
                row_layout = QHBoxLayout(row_widget)
                row_layout.setContentsMargins(0, 5, 0, 5)
                row_layout.setSpacing(10)

                # Accent Color Dot / Icon
                portal_color = _get_portal_accent_color(s["name"])
                icon_name = _get_service_icon(s["name"])
                
                icon_lbl = QLabel()
                icon_lbl.setPixmap(qta.icon(icon_name, color=portal_color).pixmap(QSize(16, 16)) if qta else QIcon().pixmap(QSize(16, 16)))
                icon_lbl.setFixedSize(18, 18)
                row_layout.addWidget(icon_lbl)

                svc_name = QLabel(s["name"])
                svc_name.setStyleSheet("font-weight: 500; font-size: 12px; color: #E8E8E3;")
                row_layout.addWidget(svc_name, stretch=1)

                # 1. Ext Button (Extension Autofill)
                if ext_setting:
                    btn_ext = QPushButton()
                    btn_ext.setFixedSize(36, 28)
                    btn_ext.setCursor(Qt.PointingHandCursor if ext_on else Qt.ForbiddenCursor)
                    btn_ext.setEnabled(ext_on)
                    if ext_on:
                        btn_ext.setIcon(qta.icon("mdi.flash", color="#FFFFFF") if qta else QIcon())
                        btn_ext.setIconSize(QSize(18, 18))
                        btn_ext.setToolTip(f"Extension Autofill {s['name']}" + (f" (Alt+{key_num})" if has_shortcut else ""))
                        btn_ext.setStyleSheet("QPushButton { background-color: #FF4D4D; border: none; border-radius: 6px; } QPushButton:hover { background-color: #E63939; }")
                        btn_ext.clicked.connect(lambda _, svc=s: self._launch_extension_autofill(svc))
                    else:
                        btn_ext.setIcon(qta.icon("mdi.flash", color="#555555") if qta else QIcon())
                        btn_ext.setIconSize(QSize(18, 18))
                        btn_ext.setToolTip("Extension Autofill is OFF" if not ext_setting else "Manual-only portal")
                        btn_ext.setStyleSheet("QPushButton { background-color: #171717; border: 1px solid #262626; border-radius: 6px; }")
                    row_layout.addWidget(btn_ext)

                # 2. Assist Button (Manual Assist)
                if assist_setting:
                    btn_assist = QPushButton()
                    btn_assist.setFixedSize(36, 28)
                    btn_assist.setCursor(Qt.PointingHandCursor if assist_on else Qt.ForbiddenCursor)
                    btn_assist.setEnabled(assist_on)
                    if assist_on:
                        btn_assist.setIcon(qta.icon("mdi.clipboard-account-outline", color="#FF4D4D") if qta else QIcon())
                        btn_assist.setIconSize(QSize(18, 18))
                        btn_assist.setToolTip(f"Manual Assist {s['name']}" + (f" (Alt+Ctrl+{key_num})" if has_shortcut else ""))
                        btn_assist.setStyleSheet("QPushButton { background-color: #1A1A1A; border: 1.5px solid #FF4D4D; border-radius: 6px; } QPushButton:hover { background-color: rgba(255, 77, 77, 0.2); }")
                        btn_assist.clicked.connect(lambda _, svc=s: self._launch_manual_assist(svc))
                    else:
                        btn_assist.setIcon(qta.icon("mdi.clipboard-account-outline", color="#555555") if qta else QIcon())
                        btn_assist.setIconSize(QSize(18, 18))
                        btn_assist.setToolTip("Manual Assist is OFF")
                        btn_assist.setStyleSheet("QPushButton { background-color: #171717; border: 1px solid #262626; border-radius: 6px; }")
                    row_layout.addWidget(btn_assist)

                # 3. Copy Button (Manual Copy)
                if copy_setting:
                    btn_copy = QPushButton()
                    btn_copy.setFixedSize(36, 28)
                    btn_copy.setCursor(Qt.PointingHandCursor if copy_on else Qt.ForbiddenCursor)
                    btn_copy.setEnabled(copy_on)
                    if copy_on:
                        btn_copy.setIcon(qta.icon("mdi.content-copy", color="#FF4D4D") if qta else QIcon())
                        btn_copy.setIconSize(QSize(17, 17))
                        btn_copy.setToolTip(f"Manual Copy {s['name']}" + (f" (Alt+Shift+{key_num})" if has_shortcut else ""))
                        btn_copy.setStyleSheet("QPushButton { background-color: #1A1A1A; border: 1.5px solid #FF4D4D; border-radius: 6px; } QPushButton:hover { background-color: rgba(255, 77, 77, 0.2); }")
                        btn_copy.clicked.connect(lambda _, svc=s: self._launch_manual_copy(svc))
                    else:
                        btn_copy.setIcon(qta.icon("mdi.content-copy", color="#555555") if qta else QIcon())
                        btn_copy.setIconSize(QSize(17, 17))
                        btn_copy.setToolTip("Manual Copy is OFF")
                        btn_copy.setStyleSheet("QPushButton { background-color: #171717; border: 1px solid #262626; border-radius: 6px; }")
                    row_layout.addWidget(btn_copy)

                svc_list_layout.addWidget(row_widget)

                if i < len(services) - 1:
                    item_div = QFrame()
                    item_div.setFrameShape(QFrame.HLine)
                    item_div.setStyleSheet("background-color: #232323; min-height: 1px; max-height: 1px; border: none;")
                    svc_list_layout.addWidget(item_div)

                if has_shortcut:
                    if ext_on:
                        sc_ext = QShortcut(QKeySequence(f"Alt+{key_num}"), self)
                        sc_ext.activated.connect(lambda svc=s: self._launch_extension_autofill(svc))
                        self._service_shortcuts.append(sc_ext)

                    if assist_on:
                        sc_assist = QShortcut(QKeySequence(f"Alt+Ctrl+{key_num}"), self)
                        sc_assist.activated.connect(lambda svc=s: self._launch_manual_assist(svc))
                        self._service_shortcuts.append(sc_assist)

                    if copy_on:
                        sc_copy = QShortcut(QKeySequence(f"Alt+Shift+{key_num}"), self)
                        sc_copy.activated.connect(lambda svc=s: self._launch_manual_copy(svc))
                        self._service_shortcuts.append(sc_copy)

            self.scroll_layout.addLayout(svc_list_layout)

            # Section Divider
            div3 = QFrame()
            div3.setFrameShape(QFrame.HLine)
            div3.setStyleSheet("background-color: #2A2A2A; min-height: 1px; max-height: 1px; border: none;")
            self.scroll_layout.addWidget(div3)

        # ======================================================================
        # 4. Notes Section (Clean White Editor)
        # ======================================================================
        notes_header = QHBoxLayout()
        notes_header.setContentsMargins(0, 0, 0, 0)
        notes_label = QLabel("NOTES")
        notes_label.setStyleSheet("font-size: 10px; font-weight: 600; letter-spacing: 0.5px; color: #6E6D67;")
        notes_header.addWidget(notes_label)

        self.notes_status_lbl = QLabel("")
        self.notes_status_lbl.setStyleSheet("color: #4CF9B7; font-size: 11px; font-weight: 600;")
        notes_header.addWidget(self.notes_status_lbl)
        notes_header.addStretch()
        self.scroll_layout.addLayout(notes_header)

        self.notes_edit = QTextEdit()
        self.notes_edit.setObjectName("DetailNotes")
        self.notes_edit.setReadOnly(False)
        self.notes_edit.setPlaceholderText("Add notes for this client...")
        self.notes_edit.setMaximumHeight(64)
        self.notes_edit.setStyleSheet("""
            QTextEdit#DetailNotes {
                background-color: #FFFFFF;
                border: 1px solid #D8CDB4;
                border-radius: 7px;
                padding: 8px;
                font-size: 12.5px;
                color: #241F1B;
            }
            QTextEdit#DetailNotes:focus {
                border-color: #2E9B5F;
            }
        """)
        
        self._loading_notes = True
        self.notes_edit.setPlainText(self.client.get("notes") or "")
        self._loading_notes = False

        self._notes_timer = QTimer(self)
        self._notes_timer.setSingleShot(True)
        self._notes_timer.timeout.connect(self._auto_save_notes)
        self.notes_edit.textChanged.connect(self._on_notes_text_changed)
        self.scroll_layout.addWidget(self.notes_edit)

        self.scroll_layout.addStretch()

    def _get_identity_parts(self, client):
        identity_cols = [c for c in self.db.get_mcl_columns() if c["is_identity"]]
        vals = [client["values"].get(c["id"], "") for c in identity_cols if client["values"].get(c["id"])]
        primary = vals[0] if len(vals) > 0 else "Client Profile"
        secondary = vals[1] if len(vals) > 1 else ""
        return primary, secondary

    def _get_identity_label(self, client):
        primary, secondary = self._get_identity_parts(client)
        return f"{primary} — {secondary}" if secondary else primary

    def _get_credentials(self, service: dict):
        uid = self.client["values"].get(service["userid_column_id"], "")
        pwd = self.client["values"].get(service["password_column_id"], "")
        return uid, pwd

    def _launch_extension_autofill(self, service: dict):
        uid, pwd = self._get_credentials(service)
        if not uid or not pwd:
            QMessageBox.warning(self, "Missing credentials", f"No User ID / Password saved for {service['name']}.")
            return

        try:
            self.db.record_client_activity(self.client["id"], service["name"], "Autofilled")
        except Exception:
            pass

        self.db.log_action(
            self.actor, "autofill_extension",
            client_id=self.client["id"],
            service_id=service["id"],
            detail=f"Extension autofill triggered for {service['name']}"
        )
        self.action_alert_requested.emit("autofill", self._get_identity_label(self.client))

        fst_on = self.db.get_setting("fst_enabled", "1") == "1"
        sad_on = self.db.get_setting("sad_enabled", "1") == "1"
        service["_fst_enabled"] = fst_on
        service["_sad_enabled"] = sad_on
        service["_tracker_enabled"] = fst_on or sad_on
        service["_client_name"] = self._get_identity_label(self.client)
        automation._send_to_extension(
            service, uid, pwd, self.client["id"],
            on_error=lambda msg, s=service['name']: self._bridge.failed.emit(s, msg)
        )
        self.window().showMinimized()

    def _launch_playwright_autofill(self, service: dict):
        uid, pwd = self._get_credentials(service)
        if not uid or not pwd:
            QMessageBox.warning(self, "Missing credentials", f"No User ID / Password saved for {service['name']}.")
            return

        try:
            self.db.record_client_activity(self.client["id"], service["name"], "Playwright Autofill")
        except Exception:
            pass

        self.db.log_action(
            self.actor, "autofill_playwright",
            client_id=self.client["id"],
            service_id=service["id"],
            detail=f"Playwright autofill triggered for {service['name']}"
        )
        self.action_alert_requested.emit("autofill", self._get_identity_label(self.client))

        automation._manager.request_autofill(
            service, uid, pwd, self.client["id"],
            on_error=lambda msg, s=service['name']: self._bridge.failed.emit(s, msg)
        )
        self.window().showMinimized()

    def _launch_autofill(self, service: dict):
        uid, pwd = self._get_credentials(service)
        if not uid or not pwd:
            QMessageBox.warning(self, "Missing credentials", f"No User ID / Password saved for {service['name']}.")
            return

        try:
            self.db.record_client_activity(self.client["id"], service["name"], "Autofilled")
        except Exception:
            pass

        self.db.log_action(
            self.actor, "autofill",
            client_id=self.client["id"],
            service_id=service["id"],
            detail=f"Autofill triggered for {service['name']}"
        )
        self.action_alert_requested.emit("autofill", self._get_identity_label(self.client))

        if automation.is_manual_portal(service):
            self._launch_manual(service, uid, pwd)
        else:
            fst_on = self.db.get_setting("fst_enabled", "1") == "1"
            sad_on = self.db.get_setting("sad_enabled", "1") == "1"
            service["_fst_enabled"] = fst_on
            service["_sad_enabled"] = sad_on
            service["_tracker_enabled"] = fst_on or sad_on
            automation.autofill_login(
                service, uid, pwd, self.client["id"],
                on_error=lambda msg, s=service['name']: self._bridge.failed.emit(s, msg)
            )
        self.window().showMinimized()



    def _launch_manual_copy(self, service: dict):
        uid, pwd = self._get_credentials(service)
        if not uid or not pwd:
            QMessageBox.warning(self, "Missing credentials", f"No User ID / Password saved for {service['name']}.")
            return

        try:
            self.db.record_client_activity(self.client["id"], service["name"], "Manual Copy")
        except Exception:
            pass

        self.db.log_action(
            self.actor, "manual_copy",
            client_id=self.client["id"],
            service_id=service["id"],
            detail=f"MECP manual copy triggered for {service['name']}"
        )
        self.action_alert_requested.emit("manual_copy", self._get_identity_label(self.client))

        fst_on = self.db.get_setting("fst_enabled", "1") == "1"
        sad_on = self.db.get_setting("sad_enabled", "1") == "1"
        service["_fst_enabled"] = fst_on
        service["_sad_enabled"] = sad_on
        service["_tracker_enabled"] = fst_on or sad_on
        service["_client_name"] = self._get_identity_label(self.client)
        automation.trigger_mecp(
            service, uid, pwd, self.client["id"],
            on_error=lambda msg, s=service['name']: self._bridge.failed.emit(s, msg)
        )
        self.window().showMinimized()

    def _launch_manual_assist(self, service: dict):
        uid, pwd = self._get_credentials(service)
        if not uid or not pwd:
            QMessageBox.warning(self, "Missing credentials", f"No User ID / Password saved for {service['name']}.")
            return
        try:
            self.db.record_client_activity(self.client["id"], service["name"], "Manual Assist")
        except Exception:
            pass
        self.db.log_action(
            self.actor, "manual_assist", client_id=self.client["id"], service_id=service["id"],
            detail=f"Manual assist triggered for {service['name']}"
        )
        self.action_alert_requested.emit("manual_assist", self._get_identity_label(self.client))
        fst_on = self.db.get_setting("fst_enabled", "1") == "1"
        sad_on = self.db.get_setting("sad_enabled", "1") == "1"
        service["_fst_enabled"] = fst_on
        service["_sad_enabled"] = sad_on
        service["_tracker_enabled"] = fst_on or sad_on
        service["_client_name"] = self._get_identity_label(self.client)
        automation.trigger_manual_assist(
            service, uid, pwd, self.client["id"],
            on_error=lambda msg, s=service['name']: self._bridge.failed.emit(s, msg)
        )
        self.window().showMinimized()

    def _on_autofill_failed(self, portal: str, msg: str):
        QMessageBox.critical(
            self, "Autofill failed",
            f"Could not complete {portal} autofill:\n{msg}\n\n"
            f"Verify the CSS selectors in Admin Mode -> Manage Services."
        )

    def _on_notes_text_changed(self):
        if getattr(self, "_loading_notes", False):
            return
        if hasattr(self, "notes_status_lbl"):
            self.notes_status_lbl.setText("  • Saving...")
            self.notes_status_lbl.setStyleSheet("color: #E65100; font-size: 11px; font-style: italic;")
        if hasattr(self, "_notes_timer"):
            self._notes_timer.start(500)

    def _auto_save_notes(self):
        if not self.client or not hasattr(self, "notes_edit"):
            return
        new_notes = self.notes_edit.toPlainText().strip()
        if self.client.get("notes") == new_notes:
            if hasattr(self, "notes_status_lbl"):
                self.notes_status_lbl.setText("  • Saved ✓")
            return

        self.db.update_client_notes(self.client["id"], new_notes)
        self.client["notes"] = new_notes
        self.db.log_action(
            self.actor, "update_notes",
            client_id=self.client["id"],
            detail="Auto-saved client notes"
        )
        if hasattr(self, "notes_status_lbl"):
            self.notes_status_lbl.setText("  • Saved ✓")
            self.notes_status_lbl.setStyleSheet("color: #2E7D32; font-size: 11px; font-weight: 600;")


    def _on_label_clicked(self, val: str, label_name: str, is_secret: bool, timeout_sec: int):
        self._copy_to_clipboard(val, label_name, is_secret, timeout_sec)

    def _copy_to_clipboard(self, val: str, label_name: str, is_secret: bool, timeout_sec: int):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(val)
        
        c_name = self._get_identity_label(self.client) if self.client else label_name
        self.action_alert_requested.emit("manual_copy", c_name)
        
        if self.client:
            try:
                self.db.record_client_activity(self.client["id"], "Copied", label_name)
            except Exception:
                pass

        self.db.log_action(
            self.actor, "manual_copy",
            client_id=self.client["id"] if self.client else None,
            detail=f"Quick copied field: {label_name}"
        )
        
        if is_secret:
            QTimer.singleShot(timeout_sec * 1000, lambda: self._clear_clipboard_if_matches(val))

    def _clear_clipboard_if_matches(self, val: str):
        try:
            from PySide6.QtWidgets import QApplication
            clipboard = QApplication.clipboard()
            if clipboard.text() == val:
                clipboard.clear()
        except Exception:
            pass # Ignore clipboard clear failures
