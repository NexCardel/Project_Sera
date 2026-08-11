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
import qtawesome as qta
from automation import _AutofillBridge
from ui.dialogs.manual_credentials_dialog import ManualCredentialsDialog
from ui.utils.masking import mask_password
from pathlib import Path

BACK_ICON = str(Path(__file__).resolve().parents[2] / "Version SKY" / "Sera_SVG" / "arrow_back_24dp_E3E3E3_FILL0_wght400_GRAD0_opsz24.svg")


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
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(10)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(10)
        # The back control is placed at the start of the detail header.
        self.back_btn = QPushButton("✕")
        self.back_btn.setText("")
        self.back_btn.setFixedSize(30, 30)
        self.back_btn.setToolTip("Back")
        self.back_btn.setIcon(qta.icon("mdi.arrow-left", color="#000000"))
        self.back_btn.setIconSize(QSize(18, 18))
        self.back_btn.setProperty("class", "CloseButton")
        self.back_btn.clicked.connect(self._safe_back_request)
        header.addWidget(self.back_btn)
        header.removeWidget(self.back_btn)
        header.insertWidget(0, self.back_btn)
        self.identity_label = QLabel()
        self.identity_label.setProperty("class", "LargeIdentityText")
        self.identity_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.identity_label.setStyleSheet("font-size: 18px; font-weight: 700; color: #241F1B;")
        header.addWidget(self.identity_label, stretch=1)
        header.addStretch()
        outer.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.scroll_widget = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_widget)
        self.scroll_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_layout.setSpacing(6)
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

        
        # We set identity label for backwards compatibility or header
        self.identity_label.setText(f"Client Profile - {self._get_identity_label(self.client)}")
        
        # Add Edit / Delete buttons if admin
        # We don't have self.admin_mode passed in directly, but we can check if actor is Admin maybe?
        # The user said "make it only in admin mode". The Sidebar knows Admin Mode. 
        # Actually, I can just add them and hide them by default for now, or just leave them out as requested.
        # "dont create edit and delete button in normal mode make it only in admin mode"
        
        # Let's create the group boxes
        from PySide6.QtWidgets import QGridLayout
        
        mask_mode = self.db.get_setting("mask_mode", "last_n")
        reveal_count = int(self.db.get_setting("mask_reveal_count", "4"))
        
        show_hide_btn_enabled = self.db.get_setting("show_hide_btn_enabled", "1") == "1"
        
        timeout_sec = 30
        try:
            timeout_sec = int(self.db.get_setting("clipboard_clear_seconds", "30"))
        except Exception:
            pass

        # 1. Identity & Contacts
        id_box = QGroupBox("🪪 Identity & Contacts")
        id_layout = QGridLayout(id_box)
        id_box.setTitle("Identity & Contacts")
        id_box.setObjectName("ClientDetailCard")
        id_layout.setContentsMargins(6, 8, 6, 6)
        id_layout.setSpacing(6)
        
        # 2. Security Credentials
        sec_box = QGroupBox("🔒 Security Credentials")
        sec_layout = QGridLayout(sec_box)
        sec_box.setTitle("Security Credentials")
        sec_box.setObjectName("ClientDetailCard")
        sec_layout.setContentsMargins(6, 8, 6, 6)
        sec_layout.setSpacing(5)

        # Build them
        mcl_cols = self.db.get_mcl_columns()
        id_row, id_col = 0, 0
        sec_row, sec_col = 0, 0

        for col in mcl_cols:
            raw_val = self.client["values"].get(col["id"], "")
            allow_qc = col.get("allow_quick_copy", True)
            
            if col["field_type"] == "password":
                # Security Credentials (2-Column Grid)
                field_widget = QWidget()
                fl = QHBoxLayout(field_widget)
                fl.setContentsMargins(8, 6, 8, 6)
                fl.setSpacing(8)
                
                lbl = QLabel(f"{col['label']}:")
                lbl.setProperty("class", "SidebarSection")
                lbl.setStyleSheet("color: black; font-weight: bold;")
                lbl.setObjectName("DetailSectionLabel")
                fl.addWidget(lbl)
                
                masked_val = mask_password(raw_val, mask_mode, reveal_count) if raw_val else "—"
                
                if allow_qc:
                    val_label = ClickableLabel(masked_val)
                    if raw_val:
                        val_label.clicked.connect(
                            lambda v=raw_val, lbl_name=col['label'], is_sec=True, t=timeout_sec: self._on_label_clicked(v, lbl_name, is_sec, t)
                        )
                        val_label.setCursor(Qt.PointingHandCursor)
                else:
                    val_label = QLabel(masked_val)
                val_label.setObjectName("DetailSecretValue")
                fl.addWidget(val_label, stretch=1)
                
                if raw_val:
                    btn_row = QHBoxLayout()
                    btn_row.setContentsMargins(0, 0, 0, 0)
                    btn_row.setSpacing(4)
                    
                    if show_hide_btn_enabled:
                        btn_show = QPushButton()
                        btn_show.setFixedSize(28, 28)
                        btn_show.setToolTip("Show / Hide Password")
                        btn_show.setObjectName("DetailActionButton")
                        btn_show.setIcon(qta.icon("mdi.eye-outline", color="#000000"))
                        btn_show.setCheckable(True)
                        btn_show.toggled.connect(
                            lambda checked, lbl=val_label, m=masked_val, r=raw_val, btn=btn_show: (
                                lbl.setText(r if checked else m),
                                btn.setIcon(qta.icon("mdi.eye-off-outline" if checked else "mdi.eye-outline", color="#000000"))
                            )
                        )
                        btn_row.addWidget(btn_show)

                    if allow_qc:
                        btn_copy = QPushButton()
                        btn_copy.setFixedSize(28, 28)
                        btn_copy.setToolTip("Copy Password")
                        btn_copy.setObjectName("DetailActionButton")
                        btn_copy.setIcon(qta.icon("mdi.content-copy", color="#000000"))
                        btn_copy.clicked.connect(
                            lambda _, v=raw_val, lbl_name=col['label'], is_sec=True, t=timeout_sec: self._copy_to_clipboard(v, lbl_name, is_sec, t)
                        )
                        btn_row.addWidget(btn_copy)
                        
                    fl.addLayout(btn_row)

                
                field_widget.setStyleSheet("QWidget { background: #FFFFFF; border: 1px solid #D8CDB4; border-radius: 6px; } QLabel { border: none; background: transparent; }")
                field_widget.setObjectName("ClientDetailField")
                
                sec_layout.addWidget(field_widget, sec_row, sec_col)
                sec_col += 1
                if sec_col > 1:
                    sec_col = 0
                    sec_row += 1
            else:
                # Identity & Contacts
                field_widget = QWidget()
                card_layout = QHBoxLayout(field_widget)
                card_layout.setContentsMargins(8, 6, 8, 6)
                card_layout.setSpacing(6)

                fl = QVBoxLayout()
                fl.setContentsMargins(0, 0, 0, 0)
                fl.setSpacing(2)
                
                lbl = QLabel(col['label'])
                lbl.setProperty("class", "SidebarSection")
                lbl.setStyleSheet("color: #444444; font-size: 11px;")
                lbl.setObjectName("DetailFieldLabel")
                
                if allow_qc:
                    val_label = ClickableLabel(raw_val or "—")
                    if raw_val:
                        val_label.clicked.connect(
                            lambda v=raw_val, lbl_name=col['label'], is_sec=False, t=timeout_sec: self._on_label_clicked(v, lbl_name, is_sec, t)
                        )
                        val_label.setCursor(Qt.PointingHandCursor)
                else:
                    val_label = QLabel(raw_val or "—")
                val_label.setObjectName("DetailFieldValue")
                
                fl.addWidget(lbl)
                fl.addWidget(val_label)
                card_layout.addLayout(fl, stretch=1)

                if allow_qc and raw_val:
                    icon_lbl = ClickableLabel()
                    icon_lbl.setPixmap(qta.icon("mdi.content-copy", color="#000000").pixmap(QSize(16, 16)))
                    icon_lbl.setFixedSize(18, 18)
                    icon_lbl.setToolTip(f"Click to copy {col['label']}")
                    icon_lbl.setCursor(Qt.PointingHandCursor)
                    icon_lbl.clicked.connect(
                        lambda v=raw_val, lbl_name=col['label'], is_sec=False, t=timeout_sec: self._on_label_clicked(v, lbl_name, is_sec, t)
                    )
                    card_layout.addWidget(icon_lbl, alignment=Qt.AlignVCenter)
                
                field_widget.setStyleSheet("QWidget { background: #FFFFFF; border: 1px solid #D8CDB4; border-radius: 6px; } QLabel { border: none; background: transparent; }")
                field_widget.setObjectName("ClientDetailField")
                
                id_layout.addWidget(field_widget, id_row, id_col)
                id_col += 1
                if id_col > 1:
                    id_col = 0
                    id_row += 1




        self.scroll_layout.addWidget(id_box)
        self.scroll_layout.addWidget(sec_box)


        notes_header = QHBoxLayout()
        notes_label = QLabel("Notes")
        notes_label.setObjectName("DetailSectionLabel")
        notes_header.addWidget(notes_label)

        self.notes_status_lbl = QLabel("")
        self.notes_status_lbl.setStyleSheet("color: #2E7D32; font-size: 11px; font-weight: 600;")
        notes_header.addWidget(self.notes_status_lbl)
        notes_header.addStretch()
        
        self.scroll_layout.addLayout(notes_header)

        self.notes_edit = QTextEdit()
        self.notes_edit.setObjectName("DetailNotes")
        self.notes_edit.setReadOnly(False)
        self.notes_edit.setPlaceholderText("Type notes for this client here...")
        self.notes_edit.setMaximumHeight(64)
        
        self._loading_notes = True
        self.notes_edit.setPlainText(self.client.get("notes") or "")
        self._loading_notes = False

        self.notes_edit.setStyleSheet("QTextEdit#DetailNotes { background-color: #FFFFFF; border: 1px solid #D8CDB4; border-radius: 6px; padding: 8px; font-size: 13px; color: #241F1B; }")
        
        # Debounce auto-save setup (500ms delay after typing stops)
        self._notes_timer = QTimer(self)
        self._notes_timer.setSingleShot(True)
        self._notes_timer.timeout.connect(self._auto_save_notes)
        self.notes_edit.textChanged.connect(self._on_notes_text_changed)

        self.scroll_layout.addWidget(self.notes_edit)



        services = self.db.get_client_services(client_id)
        if services:
            ext_setting = self.db.get_setting("extension_autofill_enabled", "1") == "1"
            assist_setting = self.db.get_setting("manual_assist_enabled", "1") == "1"
            copy_setting = self.db.get_setting("manual_copy_btn_enabled", "1") == "1"

            # Service Management Card
            svc_card = QGroupBox("Service management")
            svc_card.setObjectName("ClientDetailCard")
            svc_card_layout = QVBoxLayout(svc_card)
            svc_card_layout.setContentsMargins(12, 10, 12, 10)
            svc_card_layout.setSpacing(6)

            # Top Header Bar inside card (Title + Shortcuts info)
            card_hdr = QHBoxLayout()
            card_hdr.setContentsMargins(0, 0, 0, 0)
            
            lbl_title = QLabel("Service management")
            lbl_title.setStyleSheet("font-weight: 700; font-size: 14px; color: #241F1B;")
            card_hdr.addWidget(lbl_title)
            card_hdr.addStretch()

            shortcut_info = QLabel("ⓘ shortcuts")
            shortcut_info.setStyleSheet("color: #777777; font-size: 11px;")
            shortcut_info.setToolTip(
                "Shortcuts:\n"
                "Alt+1..9 = Extension Autofill\n"
                "Alt+Ctrl+1..9 = Manual Assist\n"
                "Alt+Shift+1..9 = Manual Copy"
            )
            card_hdr.addWidget(shortcut_info)
            svc_card_layout.addLayout(card_hdr)

            # Column Headers (Service | Ext | Assist | Copy)
            col_hdr = QHBoxLayout()
            col_hdr.setContentsMargins(0, 4, 0, 4)
            lbl_col_svc = QLabel("Service")
            lbl_col_svc.setStyleSheet("color: #888888; font-size: 11px; font-weight: 600;")
            col_hdr.addWidget(lbl_col_svc)
            col_hdr.addStretch()

            for hdr_text, is_on in [("Ext", ext_setting), ("Assist", assist_setting), ("Copy", copy_setting)]:
                lbl_h = QLabel(hdr_text)
                color_str = "#888888" if is_on else "#BBBBBB"
                lbl_h.setStyleSheet(f"color: {color_str}; font-size: 11px; font-weight: 600; font-family: monospace;")
                lbl_h.setFixedWidth(34)
                lbl_h.setAlignment(Qt.AlignCenter)
                col_hdr.addWidget(lbl_h)

            svc_card_layout.addLayout(col_hdr)

            # Header separator
            sep = QFrame()
            sep.setFrameShape(QFrame.HLine)
            sep.setStyleSheet("color: #E0D7C3; background-color: #E0D7C3; min-height: 1px; max-height: 1px; border: none;")
            svc_card_layout.addWidget(sep)

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
                row_layout.setContentsMargins(0, 4, 0, 4)
                row_layout.setSpacing(8)

                # Icon + Service Name
                icon_name = _get_service_icon(s["name"])
                icon_lbl = QLabel()
                icon_lbl.setPixmap(qta.icon(icon_name, color="#C62828").pixmap(QSize(18, 18)))
                icon_lbl.setFixedSize(22, 22)
                row_layout.addWidget(icon_lbl)

                svc_name = QLabel(s["name"])
                svc_name.setStyleSheet("font-weight: 600; font-size: 13px; color: #241F1B;")
                row_layout.addWidget(svc_name, stretch=1)

                # 1. Ext Button (Extension Autofill)
                btn_ext = QPushButton()
                btn_ext.setFixedSize(34, 28)
                btn_ext.setCursor(Qt.PointingHandCursor if ext_on else Qt.ForbiddenCursor)
                btn_ext.setEnabled(ext_on)
                if ext_on:
                    btn_ext.setIcon(qta.icon("mdi.flash", color="#FFFFFF"))
                    btn_ext.setToolTip(f"Extension Autofill {s['name']}" + (f" (Alt+{key_num})" if has_shortcut else ""))
                    btn_ext.setStyleSheet("QPushButton { background-color: #C62828; border: none; border-radius: 6px; } QPushButton:hover { background-color: #B71C1C; }")
                    btn_ext.clicked.connect(lambda _, svc=s: self._launch_extension_autofill(svc))
                else:
                    btn_ext.setIcon(qta.icon("mdi.flash", color="#AAAAAA"))
                    btn_ext.setToolTip("Extension Autofill is OFF" if not ext_setting else "Manual-only portal")
                    btn_ext.setStyleSheet("QPushButton { background-color: #E6E0D2; border: 1px solid #D0C5B0; border-radius: 6px; }")
                row_layout.addWidget(btn_ext)

                # 2. Assist Button (Manual Dialog)
                btn_assist = QPushButton()
                btn_assist.setFixedSize(34, 28)
                btn_assist.setCursor(Qt.PointingHandCursor if assist_on else Qt.ForbiddenCursor)
                btn_assist.setEnabled(assist_on)
                if assist_on:
                    btn_assist.setIcon(qta.icon("mdi.clipboard-account-outline", color="#C62828"))
                    btn_assist.setToolTip(f"Manual Assist {s['name']}" + (f" (Alt+Ctrl+{key_num})" if has_shortcut else ""))
                    btn_assist.setStyleSheet("QPushButton { background-color: transparent; border: 1.5px solid #C62828; border-radius: 6px; } QPushButton:hover { background-color: rgba(198, 40, 40, 0.08); }")
                    btn_assist.clicked.connect(lambda _, svc=s: self._launch_manual_assist(svc))
                else:
                    btn_assist.setIcon(qta.icon("mdi.clipboard-account-outline", color="#AAAAAA"))
                    btn_assist.setToolTip("Manual Assist is OFF")
                    btn_assist.setStyleSheet("QPushButton { background-color: #E6E0D2; border: 1px solid #D0C5B0; border-radius: 6px; }")
                row_layout.addWidget(btn_assist)

                # 3. Copy Button (Manual Copy)
                btn_copy = QPushButton()
                btn_copy.setFixedSize(34, 28)
                btn_copy.setCursor(Qt.PointingHandCursor if copy_on else Qt.ForbiddenCursor)
                btn_copy.setEnabled(copy_on)
                if copy_on:
                    btn_copy.setIcon(qta.icon("mdi.content-copy", color="#C62828"))
                    btn_copy.setToolTip(f"Manual Copy {s['name']}" + (f" (Alt+Shift+{key_num})" if has_shortcut else ""))
                    btn_copy.setStyleSheet("QPushButton { background-color: transparent; border: 1.5px solid #C62828; border-radius: 6px; } QPushButton:hover { background-color: rgba(198, 40, 40, 0.08); }")
                    btn_copy.clicked.connect(lambda _, svc=s: self._launch_manual_copy(svc))
                else:
                    btn_copy.setIcon(qta.icon("mdi.content-copy", color="#AAAAAA"))
                    btn_copy.setToolTip("Manual Copy is OFF")
                    btn_copy.setStyleSheet("QPushButton { background-color: #E6E0D2; border: 1px solid #D0C5B0; border-radius: 6px; }")
                row_layout.addWidget(btn_copy)

                svc_card_layout.addWidget(row_widget)

                # Separator line except last row
                if i < len(services) - 1:
                    row_sep = QFrame()
                    row_sep.setFrameShape(QFrame.HLine)
                    row_sep.setStyleSheet("color: #F0E8D6; background-color: #F0E8D6; min-height: 1px; max-height: 1px; border: none;")
                    svc_card_layout.addWidget(row_sep)

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

            # Footer Legend
            legend_sep = QFrame()
            legend_sep.setFrameShape(QFrame.HLine)
            legend_sep.setStyleSheet("color: #E0D7C3; background-color: #E0D7C3; min-height: 1px; max-height: 1px; border: none; margin-top: 4px;")
            svc_card_layout.addWidget(legend_sep)

            footer_legend = QLabel("⚡ Ext = extension autofill    📋 Assist = manual dialog    📄 Copy = manual copy")
            footer_legend.setStyleSheet("color: #777777; font-size: 11px; padding-top: 2px;")
            svc_card_layout.addWidget(footer_legend)

            self.scroll_layout.addWidget(svc_card)






        # ---------------- DRS (Filing Status) Section ----------------
        if self.db.get_setting("drs_enabled", "0") == "1":
            drs_line = QFrame()
            drs_line.setFrameShape(QFrame.HLine)
            self.scroll_layout.addWidget(drs_line)

            drs_header = QHBoxLayout()
            drs_title = QLabel("<b>Filing Status (DRS)</b>")
            drs_header.addWidget(drs_title)
            drs_header.addStretch()


            btn_open_drs_mgr = QPushButton("📊 Open Full DRS Filing Manager")
            btn_open_drs_mgr.setEnabled(False)
            btn_open_drs_mgr.setToolTip("DRS Filing Manager is currently offline for system maintenance.")
            btn_open_drs_mgr.clicked.connect(self._open_filing_status_dialog)
            drs_header.addWidget(btn_open_drs_mgr)
            self.scroll_layout.addLayout(drs_header)

            drs_box = QGroupBox()
            drs_layout = QVBoxLayout(drs_box)

            import drs
            from ui.utils.tag_widget import TagWidget

            client_fts = self.db.get_client_filing_types(client_id)
            if client_fts:
                for ft in client_fts:
                    row_layout = QHBoxLayout()

                    # Enable / Track checkbox
                    cb_track = QCheckBox()
                    cb_track.setEnabled(False)
                    cb_track.setToolTip("DRS is currently offline for system maintenance.")
                    cb_track.setChecked(ft.get("is_enabled", True))
                    cb_track.toggled.connect(
                        lambda checked, f_id=ft['id']: self.db.set_client_filing_type_enabled(client_id, f_id, is_enabled=checked)
                    )
                    row_layout.addWidget(cb_track)

                    # Label & Code
                    title_lbl = QLabel(f"<b>{ft['name']}</b> ({ft['service_name']})")
                    row_layout.addWidget(title_lbl, stretch=2)

                    # Period selector combo (Current Period, Previous Period, 2 Periods Ago)
                    period_combo = QComboBox()
                    periods_list = [
                        drs.DRSEngine.get_period_info(ft, variant_tag=ft.get("variant_tag"), offset_periods=off)
                        for off in [0, -1, -2]
                    ]
                    for p in periods_list:
                        due_str = p.get("due_date_formatted", p["due_date"])
                        period_combo.addItem(f"{p['period_label']} (Due: {due_str})", userData=p)
                    row_layout.addWidget(period_combo, stretch=2)

                    # Status tag badge
                    initial_p = periods_list[0]
                    db_stat = self.db.get_filing_status(client_id, ft['id'], initial_p['period_label'])
                    eval_stat = drs.DRSEngine.evaluate_status(db_stat, initial_p['due_date'], initial_p['grace_days'])

                    tag = TagWidget(tag_type=eval_stat)
                    row_layout.addWidget(tag)

                    # Variant selection combo (if variants exist)
                    if ft.get("variants"):
                        var_combo = QComboBox()
                        var_combo.addItem("Default", userData=None)
                        for v in ft["variants"]:
                            var_combo.addItem(v.get("tag", "Variant"), userData=v.get("tag"))
                        
                        curr_tag = ft.get("variant_tag")
                        if curr_tag:
                            idx = var_combo.findText(curr_tag, Qt.MatchFixedString)
                            if idx >= 0:
                                var_combo.setCurrentIndex(idx)

                        var_combo.currentIndexChanged.connect(
                            lambda idx, f_id=ft['id'], combo=var_combo: self._on_variant_changed(client_id, f_id, combo.currentData())
                        )
                        row_layout.addWidget(QLabel("Schedule:"))
                        row_layout.addWidget(var_combo)

                    # Status change combo
                    stat_combo = QComboBox()
                    stat_combo.addItem("Pending", "pending")
                    stat_combo.addItem("In-Progress", "in_progress")
                    stat_combo.addItem("Submitted", "submitted")

                    def sync_stat_for_period(p_info, _ft=ft, _tag=tag, _stat_combo=stat_combo):
                        curr_db_stat = self.db.get_filing_status(client_id, _ft['id'], p_info['period_label'])
                        curr_eval_stat = drs.DRSEngine.evaluate_status(curr_db_stat, p_info['due_date'], p_info['grace_days'])
                        _tag.set_tag(curr_eval_stat)

                        c_st = (curr_db_stat.get("status") if curr_db_stat else curr_eval_stat).lower()
                        if c_st == "overdue":
                            c_st = "pending"
                        s_idx = _stat_combo.findData(c_st)
                        if s_idx >= 0:
                            _stat_combo.blockSignals(True)
                            _stat_combo.setCurrentIndex(s_idx)
                            _stat_combo.blockSignals(False)

                    sync_stat_for_period(initial_p)

                    period_combo.currentIndexChanged.connect(
                        lambda idx, combo=period_combo: sync_stat_for_period(combo.currentData())
                    )

                    stat_combo.currentIndexChanged.connect(
                        lambda idx, f_id=ft['id'], p_combo=period_combo, s_combo=stat_combo, tg=tag: self._on_status_changed(
                            client_id, f_id, p_combo.currentData()['period_label'], s_combo.currentData(), tg
                        )
                    )
                    row_layout.addWidget(stat_combo)

                    drs_layout.addLayout(row_layout)
            else:
                no_fts_lbl = QLabel(
                    "No active filing types configured for this client's attached service(s).\n"
                    "To load filing period rules (e.g. GSTR-1, GSTR-3B), go to Admin Mode → Import Filing Periods..."
                )
                no_fts_lbl.setProperty("class", "NoDataLabel")
                drs_layout.addWidget(no_fts_lbl)

            self.scroll_layout.addWidget(drs_box)
        self.scroll_layout.addStretch()

    def _open_filing_status_dialog(self):


        if not self.client:
            return
        from ui.dialogs.client_filing_status_dialog import ClientFilingStatusDialog
        dlg = ClientFilingStatusDialog(self.db, self.client["id"], actor=self.actor, parent=self)
        dlg.exec_()
        self.load_client(self.client["id"])

    def _on_variant_changed(self, client_id: int, filing_type_id: int, variant_tag: str):
        self.db.attach_client_filing_type(client_id, filing_type_id, variant_tag)
        self.load_client(client_id)

    def _on_status_changed(self, client_id: int, filing_type_id: int, period_label: str, new_status: str, tag_widget):
        self.db.set_filing_status(
            client_id=client_id,
            filing_type_id=filing_type_id,
            period_label=period_label,
            status=new_status,
            updated_by=self.actor
        )
        tag_widget.set_tag(tag_type=new_status)

    def _get_identity_label(self, client):
        identity_cols = [c["id"] for c in self.db.get_mcl_columns() if c["is_identity"]]
        vals = [client["values"].get(cid, "") for cid in identity_cols if client["values"].get(cid)]
        return " - ".join(vals) if vals else "[No Identity Data]"
        return " — ".join(vals) if vals else "[No Identity Data]"

    def _get_credentials(self, service: dict):
        uid = self.client["values"].get(service["userid_column_id"], "")
        pwd = self.client["values"].get(service["password_column_id"], "")
        return uid, pwd

    def _launch_extension_autofill(self, service: dict):
        uid, pwd = self._get_credentials(service)
        if not uid or not pwd:
            QMessageBox.warning(self, "Missing credentials", f"No User ID / Password saved for {service['name']}.")
            return

        self.db.log_action(
            self.actor, "autofill_extension",
            client_id=self.client["id"],
            service_id=service["id"],
            detail=f"Extension autofill triggered for {service['name']}"
        )
        self.action_alert_requested.emit("autofill", self._get_identity_label(self.client))

        service["_tracker_enabled"] = self.db.get_setting("tracker_enabled", "0") == "1"
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
            service["_tracker_enabled"] = self.db.get_setting("tracker_enabled", "0") == "1"
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

        self.db.log_action(
            self.actor, "manual_copy",
            client_id=self.client["id"],
            service_id=service["id"],
            detail=f"Manual copy triggered for {service['name']}"
        )
        self.action_alert_requested.emit("manual_copy", self._get_identity_label(self.client))

        self._launch_manual(service, uid, pwd)
        self.window().showMinimized()

    def _launch_manual_assist(self, service: dict):
        uid, pwd = self._get_credentials(service)
        if not uid or not pwd:
            QMessageBox.warning(self, "Missing credentials", f"No User ID / Password saved for {service['name']}.")
            return
        self.db.log_action(
            self.actor, "manual_assist", client_id=self.client["id"], service_id=service["id"],
            detail=f"Manual assist triggered for {service['name']}"
        )
        self.action_alert_requested.emit("manual_assist", self._get_identity_label(self.client))
        service["_tracker_enabled"] = self.db.get_setting("tracker_enabled", "1") == "1"
        service["_client_name"] = self._get_identity_label(self.client)
        automation.trigger_manual_assist(
            service, uid, pwd, self.client["id"],
            on_error=lambda msg, s=service['name']: self._bridge.failed.emit(s, msg)
        )
        self.window().showMinimized()

    def _launch_manual(self, service: dict, user_id: str, password: str):
        webbrowser.open(automation.get_login_url(service))

        if getattr(self, "_manual_dialog", None) is not None:
            self._manual_dialog.close()
        if getattr(self, "_manual_popup_timer", None) is not None:
            self._manual_popup_timer.stop()

        self._manual_popup_timer = QTimer(self)
        self._manual_popup_timer.setSingleShot(True)
        self._manual_popup_timer.timeout.connect(
            lambda: self._show_manual_dialog(service["name"], user_id, password)
        )
        self._manual_popup_timer.start(self.MANUAL_POPUP_DELAY_MS)

    def _show_manual_dialog(self, portal_name: str, user_id: str, password: str):
        self._manual_dialog = ManualCredentialsDialog(portal_name, user_id, password)
        self._manual_dialog.show()
        self._manual_dialog.raise_()
        self._manual_dialog.activateWindow()

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
                self.notes_status_lbl.setStyleSheet("color: #2E7D32; font-size: 11px; font-weight: 600;")
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
