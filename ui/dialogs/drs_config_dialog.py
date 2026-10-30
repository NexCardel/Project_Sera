"""ui/dialogs/drs_config_dialog.py - DRS Configuration and Column Arrangement Dialog.

Allows users to configure column arrangement, ordering, and visibility per return form.
Provides toggles for 'Extract from tracker dump only' (LTT mode) and Client Roster Scope.
Matches the visual style of LTT (dark theme, Sera evergreen accent #2E9B5F, #0A0A0A background)
and strictly uses Google Material Design icons (mdi.*).
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

try:
    import qtawesome as qta
except Exception:
    qta = None

from core.drs.config import (
    SCOPE_ALL_MCL,
    SCOPE_SERVICE_ONLY,
    default_drs_columns_for_form,
    default_drs_config,
    load_drs_config,
    save_drs_config,
)
from core.drs.engine import detect_forms_and_services
from core.drs.export import export_drs, open_drs_workbook


def _safe_icon(name: str, color: str | None = None) -> QIcon:
    if qta:
        try:
            if color:
                return qta.icon(name, color=color)
            return qta.icon(name)
        except Exception:
            pass
    return QIcon()


class DRSConfigDialog(QDialog):
    """Deadline Reminder System Configuration & Arrangement Dialog."""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.app_dir = db.app_dir
        self.setWindowTitle("Deadline Reminder System (DRS) — Configuration")
        self.setMinimumSize(860, 480)
        from PySide6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else None
        if avail:
            target_w = min(960, max(860, avail.width() - 40))
            target_h = min(580, max(480, avail.height() - 70))
            self.resize(target_w, target_h)
        else:
            self.resize(920, 560)

        self.setStyleSheet("""
            QDialog {
                background-color: #0A0A0A;
            }
            QLabel {
                color: #C9D1D9;
                font-size: 12.5px;
            }
            QLabel#title {
                color: #F0F6FC;
                font-size: 18px;
                font-weight: 700;
            }
            QLabel#hint {
                color: #8B949E;
                font-size: 12px;
                line-height: 1.4;
            }
            QLabel#section_hdr {
                color: #E6EDF3;
                font-size: 12.5px;
                font-weight: 600;
                letter-spacing: 0.3px;
            }
            QComboBox {
                background-color: #0F0F0F;
                border: 1px solid #262626;
                border-radius: 6px;
                color: #E6EDF3;
                font-size: 12.5px;
                padding: 5px 10px;
                min-height: 26px;
            }
            QComboBox:hover {
                border-color: #2E9B5F;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox QAbstractItemView {
                background-color: #0F0F0F;
                border: 1px solid #262626;
                color: #E6EDF3;
                selection-background-color: #1B2A22;
                selection-color: #4CF9B7;
                outline: none;
            }
            QListWidget {
                background-color: #0F0F0F;
                border: 1px solid #262626;
                border-radius: 8px;
                color: #E6EDF3;
                font-size: 13px;
                padding: 6px;
                outline: 0;
            }
            QListWidget::item {
                padding: 8px 10px;
                border-radius: 6px;
                margin-bottom: 2px;
            }
            QListWidget::item:hover {
                background-color: #161B22;
            }
            QListWidget::item:selected {
                background-color: #1B2A22;
                color: #4CF9B7;
                border: 1px solid #2E9B5F;
            }
            QCheckBox {
                color: #E6EDF3;
                font-size: 12.5px;
                spacing: 6px;
            }
            QPushButton {
                background-color: #1F1F1F;
                color: #E6EDF3;
                border: 1px solid #3A3A3A;
                border-radius: 8px;
                padding: 7px 14px;
                font-size: 12.5px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #2A2A2A;
                border-color: #4A4A4A;
            }
            QPushButton#primary {
                background-color: #2E9B5F;
                color: #FFFFFF;
                font-weight: 700;
                border: none;
            }
            QPushButton#primary:hover {
                background-color: #36B06D;
            }
            QFrame#card {
                background-color: #0F0F0F;
                border: 1px solid #262626;
                border-radius: 8px;
                padding: 12px;
            }
        """)

        self._detected_forms: list[dict[str, Any]] = []
        self._config: dict[str, Any] = {}
        self._current_form: str = ""

        self._build_ui()
        self._load_data()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        # Header with Material Icon
        hdr_layout = QHBoxLayout()
        hdr_layout.setSpacing(12)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(_safe_icon("mdi.calendar-clock", color="#2E9B5F").pixmap(30, 30))
        hdr_layout.addWidget(icon_lbl)

        title_vbox = QVBoxLayout()
        title_vbox.setSpacing(3)
        title_lbl = QLabel("Deadline Reminder System (DRS)")
        title_lbl.setObjectName("title")
        hint_lbl = QLabel(
            "Configure column arrangement, ordering, and live data feeds per return form. "
            "Master DB client rosters are populated automatically with support for LTT-style tracker extractions."
        )
        hint_lbl.setObjectName("hint")
        hint_lbl.setWordWrap(True)
        title_vbox.addWidget(title_lbl)
        title_vbox.addWidget(hint_lbl)
        hdr_layout.addLayout(title_vbox, stretch=1)
        root.addLayout(hdr_layout)

        # Divider
        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet("border: none; border-top: 1px solid #262626; margin: 2px 0;")
        root.addWidget(divider)

        # Top Control Row: Client Population Scope + Return Form Selector
        ctrl_bar = QHBoxLayout()
        ctrl_bar.setSpacing(16)

        # Form Selector
        form_sel_layout = QHBoxLayout()
        form_sel_layout.setSpacing(8)
        lbl_form = QLabel("Form Tab:")
        lbl_form.setObjectName("section_hdr")
        self.form_combo = QComboBox()
        self.form_combo.setMinimumWidth(160)
        self.form_combo.currentIndexChanged.connect(self._on_form_changed)
        form_sel_layout.addWidget(lbl_form)
        form_sel_layout.addWidget(self.form_combo)
        ctrl_bar.addLayout(form_sel_layout)

        # Tracker-only Checkbox for this form
        self.chk_tracker_only = QCheckBox("Extract from tracker dump only (LTT mode)")
        self.chk_tracker_only.setToolTip(
            "When checked, this return form tab will act like LTT by only displaying clients "
            "with recorded captures in Tracker Dump. Clients without captures are omitted."
        )
        self.chk_tracker_only.toggled.connect(self._on_tracker_only_toggled)
        ctrl_bar.addWidget(self.chk_tracker_only)

        ctrl_bar.addStretch()

        # Scope Selector (Whole MCL Roster vs Enrolled only)
        scope_layout = QHBoxLayout()
        scope_layout.setSpacing(8)
        lbl_scope = QLabel("Client Roster Scope:")
        lbl_scope.setObjectName("section_hdr")
        self.combo_scope = QComboBox()
        self.combo_scope.addItem("Whole MCL Roster (All Clients)", SCOPE_ALL_MCL)
        self.combo_scope.addItem("Service-Enrolled Only", SCOPE_SERVICE_ONLY)
        self.combo_scope.currentIndexChanged.connect(self._on_scope_changed)
        scope_layout.addWidget(lbl_scope)
        scope_layout.addWidget(self.combo_scope)
        ctrl_bar.addLayout(scope_layout)

        root.addLayout(ctrl_bar)

        # Main Body: Left Column (Columns Arrangement) + Right Column (Detected Forms & Service Preview)
        body = QHBoxLayout()
        body.setSpacing(18)
        root.addLayout(body, stretch=1)

        # --- LEFT: Column Manager ---
        left_box = QVBoxLayout()
        left_box.setSpacing(8)

        col_header_row = QHBoxLayout()
        self.lbl_cols_header = QLabel("COLUMNS FOR SELECTED FORM")
        self.lbl_cols_header.setObjectName("section_hdr")
        col_header_row.addWidget(self.lbl_cols_header)
        col_header_row.addStretch()
        left_box.addLayout(col_header_row)

        self.col_list = QListWidget()
        self.col_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.col_list.itemChanged.connect(self._on_item_checked)
        left_box.addWidget(self.col_list, stretch=1)

        # Column reorder button controls (like MCL dialog)
        btn_order_row = QHBoxLayout()
        btn_order_row.setSpacing(8)

        self.btn_up = QPushButton()
        self.btn_up.setIcon(_safe_icon("mdi.arrow-up", color="#F0F6FC"))
        self.btn_up.setToolTip("Move Selected Column Up")
        self.btn_up.setFixedWidth(40)
        self.btn_up.clicked.connect(lambda: self._move_column(-1))

        self.btn_down = QPushButton()
        self.btn_down.setIcon(_safe_icon("mdi.arrow-down", color="#F0F6FC"))
        self.btn_down.setToolTip("Move Selected Column Down")
        self.btn_down.setFixedWidth(40)
        self.btn_down.clicked.connect(lambda: self._move_column(1))

        self.btn_reset_form = QPushButton("Reset Form Columns")
        self.btn_reset_form.setIcon(_safe_icon("mdi.restore", color="#8B949E"))
        self.btn_reset_form.setToolTip("Reset the currently selected form's columns to defaults")
        self.btn_reset_form.clicked.connect(self._reset_current_form_defaults)

        self.btn_reset_all = QPushButton("Reset All Forms")
        self.btn_reset_all.setIcon(_safe_icon("mdi.restore", color="#8B949E"))
        self.btn_reset_all.setToolTip("Reset all return forms and columns to system defaults")
        self.btn_reset_all.clicked.connect(self._reset_all_defaults)

        btn_order_row.addWidget(self.btn_up)
        btn_order_row.addWidget(self.btn_down)
        btn_order_row.addWidget(self.btn_reset_form)
        btn_order_row.addWidget(self.btn_reset_all)
        btn_order_row.addStretch()
        left_box.addLayout(btn_order_row)

        body.addLayout(left_box, stretch=3)

        # --- RIGHT: Detected Forms & Service Information ---
        right_box = QVBoxLayout()
        right_box.setSpacing(8)

        lbl_forms = QLabel("DETECTED FILING FORMS & FEED STATUS")
        lbl_forms.setObjectName("section_hdr")
        right_box.addWidget(lbl_forms)

        self.forms_card = QFrame()
        self.forms_card.setObjectName("card")
        forms_card_layout = QVBoxLayout(self.forms_card)
        forms_card_layout.setContentsMargins(8, 8, 8, 8)
        forms_card_layout.setSpacing(6)

        forms_scroll = QScrollArea()
        forms_scroll.setWidgetResizable(True)
        forms_scroll.setFrameShape(QFrame.NoFrame)
        forms_scroll.setStyleSheet("background: transparent; border: none;")
        forms_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.lbl_forms_summary = QLabel("Scanning filing history…")
        self.lbl_forms_summary.setWordWrap(True)
        self.lbl_forms_summary.setStyleSheet("color: #C9D1D9; font-size: 12px; line-height: 1.5; background: transparent;")
        forms_scroll.setWidget(self.lbl_forms_summary)
        forms_card_layout.addWidget(forms_scroll)

        right_box.addWidget(self.forms_card, stretch=1)

        # Mechanism Explanation Card
        info_card = QFrame()
        info_card.setObjectName("card")
        info_card_layout = QVBoxLayout(info_card)
        info_card_layout.setContentsMargins(10, 8, 10, 8)
        info_card_layout.setSpacing(4)

        lbl_info_title = QLabel("DRS Multi-Form Engine")
        lbl_info_title.setStyleSheet("color: #4CF9B7; font-weight: 600; font-size: 12.5px;")
        lbl_info_desc = QLabel(
            "• Each detected return form generates a dedicated tab in Excel.\n"
            "• Columns can be rearranged and toggled independently per form.\n"
            "• Whole MCL Roster mode includes all clients; uncaptured default to 'no captures yet'.\n"
            "• 'Extract from tracker dump only' acts like LTT, listing only clients with captures.\n"
            "• Workbook updates automatically on new capture arrivals without interval polling."
        )
        lbl_info_desc.setStyleSheet("color: #8B949E; font-size: 11.5px; line-height: 1.4;")
        info_card_layout.addWidget(lbl_info_title)
        info_card_layout.addWidget(lbl_info_desc)
        right_box.addWidget(info_card)

        body.addLayout(right_box, stretch=2)

        # Bottom Actions Bar
        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(10)

        self.btn_open_excel = QPushButton("Open DRS Workbook")
        self.btn_open_excel.setIcon(_safe_icon("mdi.microsoft-excel", color="#2E9B5F"))
        self.btn_open_excel.clicked.connect(self._on_open_workbook)

        self.btn_save = QPushButton("Save & Refresh Now")
        self.btn_save.setObjectName("primary")
        self.btn_save.setIcon(_safe_icon("mdi.content-save-check-outline", color="#FFFFFF"))
        self.btn_save.clicked.connect(self._on_save_and_refresh)

        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.accept)

        bottom_bar.addWidget(self.btn_open_excel)
        bottom_bar.addStretch()
        bottom_bar.addWidget(self.btn_save)
        bottom_bar.addWidget(self.btn_close)
        root.addLayout(bottom_bar)

    def _load_data(self):
        """Discovers filing forms, loads per-form configs, and updates UI."""
        self._detected_forms = detect_forms_and_services(self.db)
        form_names = [f["form"] for f in self._detected_forms] or ["GSTR-3B", "GSTR-1", "ITR"]

        self._config = load_drs_config(self.db, detected_forms=form_names)

        # Populate Scope combobox
        current_scope = self._config.get("client_roster_scope", SCOPE_ALL_MCL)
        scope_idx = self.combo_scope.findData(current_scope)
        if scope_idx >= 0:
            self.combo_scope.blockSignals(True)
            self.combo_scope.setCurrentIndex(scope_idx)
            self.combo_scope.blockSignals(False)

        # Populate Form combobox
        self.form_combo.blockSignals(True)
        self.form_combo.clear()
        for fname in form_names:
            self.form_combo.addItem(_safe_icon("mdi.file-document-outline", color="#4CF9B7"), fname)
        self.form_combo.blockSignals(False)

        if form_names:
            self._current_form = form_names[0]
            self.form_combo.setCurrentIndex(0)
            self._render_current_form_columns()

        self._render_forms_summary()

    def _render_current_form_columns(self):
        """Renders column manager for the currently selected form."""
        if not self._current_form:
            return

        self.lbl_cols_header.setText(f"COLUMNS FOR {self._current_form.upper()} (ORDER & VISIBILITY)")

        form_cfg = self._config.get("forms", {}).get(self._current_form, {})
        cols = form_cfg.get("columns", [])
        is_tracker_only = bool(form_cfg.get("tracker_only", False))

        self.chk_tracker_only.blockSignals(True)
        self.chk_tracker_only.setChecked(is_tracker_only)
        self.chk_tracker_only.blockSignals(False)

        self.col_list.blockSignals(True)
        self.col_list.clear()

        for col in cols:
            label = col.get("label", "Untitled")
            ctype = col.get("type", "")
            enabled = bool(col.get("enabled", True))

            if ctype == "mcl":
                is_sec = col.get("is_secret") or col.get("field_type") == "password"
                badge = "[MCL: Secret]" if is_sec else "[MCL]"
            elif ctype == "fixed":
                badge = "[Fixed: DRS Core]"
            elif ctype == "tracker":
                badge = "[Tracker Dump]"
            else:
                badge = "[Field]"

            item = QListWidgetItem(f"{label}   {badge}")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            item.setCheckState(Qt.Checked if enabled else Qt.Unchecked)
            item.setData(Qt.UserRole, col.get("key"))
            self.col_list.addItem(item)

        self.col_list.blockSignals(False)

    def _render_forms_summary(self):
        """Summarizes all detected forms, portals, services, and LTT/Tracker-only status."""
        if not self._detected_forms:
            self.lbl_forms_summary.setText("No return forms detected yet in Tracker Dump.")
            return

        lines = ["<b>Active Form Tabs & Feeds:</b><br>"]
        all_clients = self.db.search_clients("", include_archived=False) if hasattr(self.db, "search_clients") else []
        scope = self._config.get("client_roster_scope", SCOPE_ALL_MCL)

        for fdef in self._detected_forms:
            fname = fdef["form"]
            portal = fdef["portal"]
            svc = fdef["service"]
            svc_name = svc["name"] if svc else "Unlinked"
            svc_id = svc["id"] if svc else None

            f_cfg = self._config.get("forms", {}).get(fname, {})
            is_ltt = bool(f_cfg.get("tracker_only", False))

            if is_ltt:
                mode_badge = "<font color='#FFDB70'><b>[Tracker Only / LTT Mode]</b></font>"
                cnt_str = "Captures only"
            elif scope == SCOPE_SERVICE_ONLY and svc_id is not None:
                mode_badge = "<font color='#8B949E'>[Service Enrolled]</font>"
                cnt = sum(1 for c in all_clients if svc_id in c.get("service_ids", []))
                cnt_str = f"{cnt} enrolled client{'s' if cnt != 1 else ''}"
            else:
                mode_badge = "<font color='#4CF9B7'>[Whole MCL Roster]</font>"
                cnt = len(all_clients)
                cnt_str = f"{cnt} total client{'s' if cnt != 1 else ''}"

            lines.append(
                f"• <b>{fname}</b> — Portal: <font color='#4CF9B7'>{portal}</font><br>"
                f"&nbsp;&nbsp;&nbsp;Service: <i>{svc_name}</i> &middot; {cnt_str} &middot; {mode_badge}<br>"
            )

        self.lbl_forms_summary.setText("".join(lines))

    def _on_form_changed(self, index: int):
        self._current_form = self.form_combo.currentText()
        self._render_current_form_columns()

    def _on_tracker_only_toggled(self, checked: bool):
        if not self._current_form:
            return
        self._config.setdefault("forms", {}).setdefault(self._current_form, {})["tracker_only"] = checked
        self._render_forms_summary()

    def _on_scope_changed(self, index: int):
        scope = self.combo_scope.currentData()
        self._config["client_roster_scope"] = scope
        self._render_forms_summary()

    def _on_item_checked(self, item: QListWidgetItem):
        if not self._current_form:
            return
        key = item.data(Qt.UserRole)
        is_checked = (item.checkState() == Qt.Checked)
        cols = self._config.get("forms", {}).get(self._current_form, {}).get("columns", [])
        for col in cols:
            if col.get("key") == key:
                col["enabled"] = is_checked
                break

    def _move_column(self, direction: int):
        if not self._current_form:
            return
        cur_row = self.col_list.currentRow()
        if cur_row < 0:
            return
        cols = self._config.get("forms", {}).get(self._current_form, {}).get("columns", [])
        new_row = cur_row + direction
        if 0 <= new_row < len(cols):
            col = cols.pop(cur_row)
            cols.insert(new_row, col)
            self._render_current_form_columns()
            self.col_list.setCurrentRow(new_row)

    def _reset_current_form_defaults(self):
        if not self._current_form:
            return
        if QMessageBox.question(
            self,
            "Reset Form Columns",
            f"Reset columns for '{self._current_form}' to default arrangement and visibility?",
        ) == QMessageBox.Yes:
            f_cfg = self._config.setdefault("forms", {}).setdefault(self._current_form, {})
            f_cfg["columns"] = default_drs_columns_for_form(self.db, self._current_form)
            f_cfg["tracker_only"] = False
            self._render_current_form_columns()
            self._render_forms_summary()

    def _reset_all_defaults(self):
        if QMessageBox.question(
            self,
            "Reset All Forms",
            "Reset all return forms, columns, and scopes to system defaults?",
        ) == QMessageBox.Yes:
            form_names = [f["form"] for f in self._detected_forms]
            self._config = default_drs_config(self.db, form_names)
            self.combo_scope.blockSignals(True)
            self.combo_scope.setCurrentIndex(0)
            self.combo_scope.blockSignals(False)
            self._render_current_form_columns()
            self._render_forms_summary()

    def _save_current(self):
        save_drs_config(self.db, self._config)

    def _on_save_and_refresh(self):
        self._save_current()
        try:
            xlsx = export_drs(self.db, force_rebuild=False)
            QMessageBox.information(
                self,
                "DRS Refreshed",
                f"DRS configuration saved and live workbook refreshed successfully!\n\nLocation: {xlsx}",
            )
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", f"Could not refresh DRS workbook:\n{e}")

    def _on_open_workbook(self):
        self._save_current()
        try:
            xlsx = export_drs(self.db, force_rebuild=False)
            if not open_drs_workbook(self.app_dir):
                QMessageBox.warning(
                    self,
                    "Workbook Not Found",
                    f"Could not open DRS workbook. Check if file exists at:\n{xlsx}",
                )
        except Exception as e:
            QMessageBox.critical(self, "Open Failed", f"Could not open DRS workbook:\n{e}")
