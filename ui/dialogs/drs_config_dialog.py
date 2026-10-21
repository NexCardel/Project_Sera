"""ui/dialogs/drs_config_dialog.py - DRS Configuration and Column Arrangement Dialog.

Allows users to reorder and toggle MCL columns, fixed DRS status/period columns,
and tracker dump fields. Matches the visual style of LTT (dark theme, Sera evergreen
accent #2E9B5F, #0A0A0A background) and the column management UX of the MCL dialog.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

try:
    import qtawesome as qta
except Exception:
    qta = None

from core.drs.config import (
    default_drs_columns,
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
        self.setMinimumSize(920, 640)
        self.resize(960, 680)

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
                font-size: 13px;
                font-weight: 600;
                letter-spacing: 0.3px;
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

        self._columns_data: list[dict[str, Any]] = []
        self._build_ui()
        self._load_data()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(14)

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
            "Configure column arrangement, visibility, and filing form feeds for the live DRS Excel workbook. "
            "Clients from Master DB are automatically grouped per registered service."
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

        # Main Body: Left Column (Columns Arrangement) + Right Column (Detected Forms & Service Preview)
        body = QHBoxLayout()
        body.setSpacing(18)
        root.addLayout(body, stretch=1)

        # --- LEFT: Column Manager ---
        left_box = QVBoxLayout()
        left_box.setSpacing(8)

        col_header_row = QHBoxLayout()
        lbl_cols = QLabel("WORKBOOK COLUMNS (ORDER & VISIBILITY)")
        lbl_cols.setObjectName("section_hdr")
        col_header_row.addWidget(lbl_cols)
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

        self.btn_reset = QPushButton("Reset to Defaults")
        self.btn_reset.setIcon(_safe_icon("mdi.restore", color="#8B949E"))
        self.btn_reset.clicked.connect(self._reset_defaults)

        btn_order_row.addWidget(self.btn_up)
        btn_order_row.addWidget(self.btn_down)
        btn_order_row.addWidget(self.btn_reset)
        btn_order_row.addStretch()
        left_box.addLayout(btn_order_row)

        body.addLayout(left_box, stretch=3)

        # --- RIGHT: Detected Forms & Service Information ---
        right_box = QVBoxLayout()
        right_box.setSpacing(8)

        lbl_forms = QLabel("DETECTED FILING FORMS & LINKED SERVICES")
        lbl_forms.setObjectName("section_hdr")
        right_box.addWidget(lbl_forms)

        self.forms_card = QFrame()
        self.forms_card.setObjectName("card")
        forms_card_layout = QVBoxLayout(self.forms_card)
        forms_card_layout.setContentsMargins(12, 12, 12, 12)
        forms_card_layout.setSpacing(10)

        self.lbl_forms_summary = QLabel("Scanning filing history…")
        self.lbl_forms_summary.setWordWrap(True)
        self.lbl_forms_summary.setStyleSheet("color: #C9D1D9; font-size: 12px; line-height: 1.5;")
        forms_card_layout.addWidget(self.lbl_forms_summary)
        forms_card_layout.addStretch()

        right_box.addWidget(self.forms_card, stretch=1)

        # Mechanism Explanation Card
        info_card = QFrame()
        info_card.setObjectName("card")
        info_card_layout = QVBoxLayout(info_card)
        info_card_layout.setContentsMargins(12, 12, 12, 12)
        info_card_layout.setSpacing(6)

        lbl_info_title = QLabel("DRS Live Mechanism")
        lbl_info_title.setStyleSheet("color: #4CF9B7; font-weight: 600; font-size: 12.5px;")
        lbl_info_desc = QLabel(
            "• Feeds automatically refresh when a new entry appears in Tracker Dump.\n"
            "• Each detected return form generates a dedicated tab in Excel.\n"
            "• All registered clients for that service are listed.\n"
            "• Uncaptured clients default to 'no captures yet'."
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
        """Loads configuration and renders form detection summary."""
        cfg = load_drs_config(self.db)
        self._columns_data = cfg.get("columns", [])
        self._render_column_list()
        self._render_forms_summary()

    def _render_column_list(self):
        self.col_list.clear()
        for idx, col in enumerate(self._columns_data):
            label = col.get("label", "Untitled")
            ctype = col.get("type", "")
            enabled = bool(col.get("enabled", True))

            if ctype == "mcl":
                badge = "[MCL]"
            elif ctype == "fixed":
                badge = "[Fixed: DRS Core]"
            elif ctype == "tracker":
                badge = "[Tracker Dump]"
            else:
                badge = "[Field]"

            item = QListWidgetItem(f"{label}   {badge}")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if enabled else Qt.Unchecked)
            item.setData(Qt.UserRole, col.get("key"))
            self.col_list.addItem(item)

    def _render_forms_summary(self):
        form_defs = detect_forms_and_services(self.db)
        if not form_defs:
            self.lbl_forms_summary.setText("No return forms detected yet in Tracker Dump.")
            return

        lines = ["<b>Active Form Tabs:</b><br>"]
        all_clients = self.db.search_clients("", include_archived=False) if hasattr(self.db, "search_clients") else []

        for fdef in form_defs:
            fname = fdef["form"]
            portal = fdef["portal"]
            svc = fdef["service"]
            svc_name = svc["name"] if svc else "Unlinked"
            svc_id = svc["id"] if svc else None
            cnt = sum(1 for c in all_clients if svc_id in c.get("service_ids", [])) if svc_id is not None else len(all_clients)
            lines.append(
                f"• <b>{fname}</b> — Portal: <font color='#4CF9B7'>{portal}</font><br>"
                f"&nbsp;&nbsp;&nbsp;Service: <i>{svc_name}</i> ({cnt} registered client{'s' if cnt != 1 else ''})<br>"
            )

        self.lbl_forms_summary.setText("".join(lines))

    def _on_item_checked(self, item: QListWidgetItem):
        key = item.data(Qt.UserRole)
        is_checked = (item.checkState() == Qt.Checked)
        for col in self._columns_data:
            if col.get("key") == key:
                col["enabled"] = is_checked
                break

    def _move_column(self, direction: int):
        cur_row = self.col_list.currentRow()
        if cur_row < 0:
            return
        new_row = cur_row + direction
        if 0 <= new_row < len(self._columns_data):
            col = self._columns_data.pop(cur_row)
            self._columns_data.insert(new_row, col)
            self._render_column_list()
            self.col_list.setCurrentRow(new_row)

    def _reset_defaults(self):
        if QMessageBox.question(
            self,
            "Reset Columns",
            "Reset all columns to their default arrangement and visibility?",
        ) == QMessageBox.Yes:
            self._columns_data = default_drs_columns(self.db)
            self._render_column_list()

    def _save_current(self):
        save_drs_config(self.db, {"columns": self._columns_data})

    def _on_save_and_refresh(self):
        self._save_current()
        try:
            xlsx = export_drs(self.db, force_rebuild=True)
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
