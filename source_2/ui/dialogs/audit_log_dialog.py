"""
audit_log_dialog.py
--------------------
Redesigned wide SSAL (Sera-Sync Audit Log) window for Project Sera.
Displays local audit log and peer workstation logs received by the Host PC.
Includes date presets, custom date range, full-text live search, action filter,
local timezone timestamp conversion, service name resolution, token-safe CSV export,
and rich row drill-down details.
"""

import os
import csv
import datetime
from PySide6.QtCore import Qt, Signal, QDate, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QSplitter,
    QListWidget,
    QListWidgetItem,
    QDateEdit,
    QFrame,
    QTextEdit,
    QCheckBox,
    QWidget,
    QApplication,
)

try:
    import qtawesome as qta
except ImportError:
    qta = None

from database import PeerAuditLogManager


def _safe_qta_icon(icon_name: str, color: str = "#FFFFFF") -> QIcon:
    if qta is not None:
        try:
            return qta.icon(icon_name, color=color)
        except Exception:
            pass
    return QIcon()


def _format_to_local_time(iso_str: str) -> tuple[str, str]:
    """Converts a UTC ISO timestamp to formatted local time (IST) and returns (local_display, raw_utc)."""
    if not iso_str:
        return "—", ""
    clean = str(iso_str).strip()
    if not clean:
        return "—", ""
    try:
        raw_clean = clean
        if clean.endswith("Z"):
            clean = clean[:-1] + "+00:00"
        dt = datetime.datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        local_dt = dt.astimezone()
        local_str = local_dt.strftime("%d-%b-%Y %I:%M:%S %p")
        return local_str, raw_clean
    except Exception:
        return clean[:19].replace("T", " "), clean


def _get_action_badge_style(action: str) -> tuple[str, str, str]:
    """Returns (display_label, fg_color, bg_color) for action pill badges."""
    act = (action or "").lower().strip()
    if act in ("create", "add"):
        return "➕ Create", "#4CF9B7", "#133829"
    elif act in ("unarchive", "restore"):
        return "♻️ Restore", "#4CF9B7", "#133829"
    elif act == "view":
        return "👁️ View", "#58A6FF", "#162B4D"
    elif act == "audit_log_viewed":
        return "🛡️ Audit View", "#58A6FF", "#162B4D"
    elif act in ("autofill", "sca_autofill", "autofill_extension", "autofill_playwright"):
        return "🪄 Autofill", "#BC8CFF", "#2E1D4D"
    elif act in ("manual_assist", "sca_widget"):
        return "⚡ Manual Assist", "#39FF14", "#183B18"
    elif act == "manual_copy":
        return "📋 Copy", "#79C0FF", "#1C304A"
    elif act in ("update", "update_notes", "update_settings"):
        return "✏️ Update", "#E3B341", "#3B3014"
    elif act in ("archive", "delete"):
        return "🗑️ Delete" if act == "delete" else "📦 Archive", "#FF7B72", "#3D1A1D"
    elif act in ("csv_export", "csv_import", "backup"):
        return "💾 " + act.replace("_", " ").title(), "#8B949E", "#21262D"
    elif act in ("sync_pushed", "sync_received"):
        return "🔄 Sync", "#7EE787", "#193B22"
    elif "filing" in act:
        return "📑 Filing", "#56D364", "#163820"
    else:
        return act.replace("_", " ").title()[:15], "#C9D1D9", "#21262D"


class AuditLogDialog(QDialog):
    toast_requested = Signal(str, int)

    def __init__(self, db, actor: str = "Admin", parent=None):
        super().__init__(parent)
        self.setObjectName("ToolDialog")
        self.db = db
        self.actor = actor
        self.setWindowTitle("Project Sera — Sera-Sync Audit Log (SSAL)")
        self.resize(1180, 720)
        self.setMinimumSize(1020, 580)

        live_dir = os.path.dirname(self.db.db_path) if hasattr(self.db, "db_path") else "."
        self.peer_mgr = PeerAuditLogManager(live_dir)
        self.selected_host = "local"  # "local" or hostname
        self._raw_logs_cache = []

        # Live search debounce timer
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(180)
        self._search_timer.timeout.connect(self._apply_client_side_filter)

        self._build_ui()
        self._load_workstations()
        self._load_logs()

        # Self-referential audit log entry
        try:
            self.db.log_action(self.actor, "audit_log_viewed", detail="Opened SSAL Audit Log Window")
        except Exception:
            pass

    def _build_ui(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #0D1117;
                color: #F0F6FC;
                font-family: 'Segoe UI', sans-serif;
            }
            QLabel {
                color: #F0F6FC;
            }
            QLineEdit, QComboBox, QDateEdit {
                background-color: #161B22;
                border: 1px solid #30363D;
                border-radius: 6px;
                color: #F0F6FC;
                padding: 5px 8px;
                font-size: 12px;
            }
            QLineEdit:focus, QComboBox:focus, QDateEdit:focus {
                border: 1px solid #2E9B5F;
                background-color: #1A222D;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox QAbstractItemView {
                background-color: #161B22;
                color: #F0F6FC;
                selection-background-color: #1F6FEB;
                selection-color: #FFFFFF;
                border: 1px solid #30363D;
            }
            QTableWidget {
                background-color: #0D1117;
                alternate-background-color: #161B22;
                gridline-color: #21262D;
                border: 1px solid #30363D;
                border-radius: 6px;
                color: #F0F6FC;
                selection-background-color: #1F6FEB;
                selection-color: #FFFFFF;
            }
            QTableWidget::item {
                color: #F0F6FC;
                padding: 6px 8px;
                border-bottom: 1px solid #1B2129;
            }
            QTableWidget::item:selected {
                background-color: #1F6FEB;
                color: #FFFFFF;
            }
            QHeaderView::section {
                background-color: #161B22;
                color: #4CF9B7;
                font-weight: 700;
                font-size: 12px;
                padding: 7px;
                border: none;
                border-bottom: 2px solid #2E9B5F;
                border-right: 1px solid #21262D;
            }
            QPushButton {
                background-color: #21262D;
                color: #F0F6FC;
                border: 1px solid #30363D;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #30363D;
                border-color: #8B949E;
            }
            QPushButton.PrimaryBtn {
                background-color: #238636;
                color: #FFFFFF;
                border: 1px solid #2EA043;
            }
            QPushButton.PrimaryBtn:hover {
                background-color: #2EA043;
            }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # Header Title Row
        title_row = QHBoxLayout()
        title_row.setSpacing(12)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(_safe_qta_icon("mdi.shield-search-outline", "#4CF9B7").pixmap(24, 24))
        title_row.addWidget(icon_lbl)

        title = QLabel("Sera-Sync Audit Log (SSAL)")
        title.setStyleSheet("font-size: 18px; font-weight: 700; color: #F0F6FC;")
        title_row.addWidget(title)

        title_row.addStretch()

        self.host_badge = QLabel("🟢 Host Aggregator Active")
        self.host_badge.setStyleSheet("color: #39FF14; font-weight: 700; font-size: 12px; padding: 4px 10px; background-color: #11281E; border: 1px solid #2E9B5F; border-radius: 6px;")
        title_row.addWidget(self.host_badge)

        btn_close_top = QPushButton("Close")
        btn_close_top.setIcon(_safe_qta_icon("mdi.close", "#F0F6FC"))
        btn_close_top.setFixedWidth(80)
        btn_close_top.clicked.connect(self.accept)
        title_row.addWidget(btn_close_top)

        main_layout.addLayout(title_row)

        # Main Splitter: Left Sidebar (Workstations) + Right Area (Audit Logs Table)
        splitter = QSplitter(Qt.Horizontal)

        # ---------------- Left Sidebar (Workstations / Users) ----------------
        sidebar_frame = QFrame()
        sidebar_frame.setFixedWidth(190)
        sidebar_frame.setStyleSheet("""
            QFrame {
                background-color: #161B22;
                border-radius: 8px;
                border: 1px solid #30363D;
            }
        """)
        sidebar_layout = QVBoxLayout(sidebar_frame)
        sidebar_layout.setContentsMargins(8, 12, 8, 12)
        sidebar_layout.setSpacing(8)

        sidebar_title = QLabel("WORKSTATIONS")
        sidebar_title.setStyleSheet("color: #8B949E; font-size: 11px; font-weight: 700; letter-spacing: 0.5px;")
        sidebar_layout.addWidget(sidebar_title)

        self.workstation_list = QListWidget()
        self.workstation_list.setStyleSheet("""
            QListWidget {
                background-color: transparent;
                border: none;
                color: #F0F6FC;
                font-size: 12px;
            }
            QListWidget::item {
                padding: 8px 10px;
                border-radius: 6px;
                margin-bottom: 3px;
            }
            QListWidget::item:selected {
                background-color: #238636;
                color: #FFFFFF;
                font-weight: bold;
            }
            QListWidget::item:hover:!selected {
                background-color: #21262D;
            }
        """)
        self.workstation_list.currentItemChanged.connect(self._on_workstation_changed)
        sidebar_layout.addWidget(self.workstation_list)

        splitter.addWidget(sidebar_frame)

        # ---------------- Right Main Log Area ----------------
        right_frame = QFrame()
        right_layout = QVBoxLayout(right_frame)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.setSpacing(10)

        # Filter Controls Bar 1: Full-Text Search + Quick Filters
        filter_bar_1 = QHBoxLayout()
        filter_bar_1.setSpacing(8)

        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("🔍 Search Client, PAN, Actor, Action, Service, Details...")
        self.txt_search.textChanged.connect(self._on_search_changed)
        filter_bar_1.addWidget(self.txt_search, stretch=3)

        lbl_action = QLabel("Action:")
        lbl_action.setStyleSheet("font-weight: 600; color: #C9D1D9;")
        filter_bar_1.addWidget(lbl_action)

        self.action_combo = QComboBox()
        self.action_combo.setFixedWidth(145)
        self.action_combo.addItems([
            "All Actions", "view", "manual_assist", "autofill", "manual_copy",
            "create", "update", "archive", "unarchive", "delete",
            "csv_import", "backup", "restore", "csv_export",
            "filing_submitted", "sync_pushed", "sync_received", "audit_log_viewed"
        ])
        self.action_combo.currentIndexChanged.connect(self._load_logs)
        filter_bar_1.addWidget(self.action_combo)

        lbl_actor = QLabel("Staff/Actor:")
        lbl_actor.setStyleSheet("font-weight: 600; color: #C9D1D9;")
        filter_bar_1.addWidget(lbl_actor)

        self.actor_input = QLineEdit()
        self.actor_input.setPlaceholderText("All staff...")
        self.actor_input.setFixedWidth(110)
        self.actor_input.textChanged.connect(self._on_search_changed)
        filter_bar_1.addWidget(self.actor_input)

        btn_refresh = QPushButton("Refresh")
        btn_refresh.setIcon(_safe_qta_icon("mdi.refresh", "#F0F6FC"))
        btn_refresh.clicked.connect(self._load_logs)
        filter_bar_1.addWidget(btn_refresh)

        btn_export = QPushButton("Export CSV...")
        btn_export.setIcon(_safe_qta_icon("mdi.file-delimited-outline", "#FFFFFF"))
        btn_export.setProperty("class", "PrimaryBtn")
        btn_export.clicked.connect(self._on_export_log_csv)
        filter_bar_1.addWidget(btn_export)

        right_layout.addLayout(filter_bar_1)

        # Filter Controls Bar 2: Interactive Date Presets & Responsive Date/Time Pickers
        filter_bar_2 = QHBoxLayout()
        filter_bar_2.setSpacing(8)

        lbl_preset = QLabel("Date Range:")
        lbl_preset.setStyleSheet("font-weight: 600; color: #4CF9B7;")
        filter_bar_2.addWidget(lbl_preset)

        self.cmb_date_preset = QComboBox()
        self.cmb_date_preset.addItems([
            "All Time", "Today", "Yesterday", "Last 7 Days", "Last 30 Days", "Custom Date Range"
        ])
        self.cmb_date_preset.setCurrentText("Last 30 Days")
        self.cmb_date_preset.currentIndexChanged.connect(self._on_date_preset_changed)
        filter_bar_2.addWidget(self.cmb_date_preset)

        lbl_from = QLabel("From:")
        lbl_from.setStyleSheet("color: #8B949E; font-size: 12px;")
        filter_bar_2.addWidget(lbl_from)

        self.dt_from = QDateEdit(QDate.currentDate().addDays(-30))
        self.dt_from.setCalendarPopup(True)
        self.dt_from.setDisplayFormat("yyyy-MM-dd")
        self.dt_from.setFixedWidth(115)
        self.dt_from.dateChanged.connect(self._on_custom_date_changed)
        filter_bar_2.addWidget(self.dt_from)

        lbl_to = QLabel("To:")
        lbl_to.setStyleSheet("color: #8B949E; font-size: 12px;")
        filter_bar_2.addWidget(lbl_to)

        self.dt_to = QDateEdit(QDate.currentDate())
        self.dt_to.setCalendarPopup(True)
        self.dt_to.setDisplayFormat("yyyy-MM-dd")
        self.dt_to.setFixedWidth(115)
        self.dt_to.dateChanged.connect(self._on_custom_date_changed)
        filter_bar_2.addWidget(self.dt_to)

        self.lbl_record_count = QLabel("0 log entries")
        self.lbl_record_count.setStyleSheet("color: #8B949E; font-size: 12px; margin-left: 10px;")
        filter_bar_2.addWidget(self.lbl_record_count)

        filter_bar_2.addStretch()
        right_layout.addLayout(filter_bar_2)

        # Wide Table (7 Columns)
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "ID", "Date & Time (Local)", "Actor", "Action", "Client (Name / Token)", "Service", "Detail"
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        self.table.setColumnWidth(1, 175)
        self.table.setColumnWidth(2, 100)
        self.table.setColumnWidth(3, 140)
        self.table.setColumnWidth(4, 200)
        self.table.setColumnWidth(5, 120)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        right_layout.addWidget(self.table)

        # Enhanced Inspection Card at Bottom
        inspector_frame = QFrame()
        inspector_frame.setStyleSheet("""
            QFrame {
                background-color: #161B22;
                border: 1px solid #30363D;
                border-radius: 6px;
            }
        """)
        inspector_layout = QVBoxLayout(inspector_frame)
        inspector_layout.setContentsMargins(10, 8, 10, 8)
        inspector_layout.setSpacing(6)

        inspector_header = QHBoxLayout()
        insp_title = QLabel("AUDIT ENTRY DRILL-DOWN")
        insp_title.setStyleSheet("color: #4CF9B7; font-size: 11px; font-weight: 700; letter-spacing: 0.5px;")
        inspector_header.addWidget(insp_title)

        inspector_header.addStretch()

        self.btn_copy_insp = QPushButton("Copy Details")
        self.btn_copy_insp.setIcon(_safe_qta_icon("mdi.content-copy", "#F0F6FC"))
        self.btn_copy_insp.setFixedHeight(24)
        self.btn_copy_insp.setStyleSheet("font-size: 11px; padding: 2px 8px;")
        self.btn_copy_insp.clicked.connect(self._on_copy_inspector)
        inspector_header.addWidget(self.btn_copy_insp)

        self.btn_filter_client = QPushButton("Filter Client")
        self.btn_filter_client.setIcon(_safe_qta_icon("mdi.account-filter", "#F0F6FC"))
        self.btn_filter_client.setFixedHeight(24)
        self.btn_filter_client.setStyleSheet("font-size: 11px; padding: 2px 8px;")
        self.btn_filter_client.clicked.connect(self._on_filter_by_selected_client)
        inspector_header.addWidget(self.btn_filter_client)

        inspector_layout.addLayout(inspector_header)

        self.inspector_box = QTextEdit()
        self.inspector_box.setReadOnly(True)
        self.inspector_box.setMaximumHeight(80)
        self.inspector_box.setPlaceholderText("Select any audit log row above to view full drill-down details and timestamps...")
        self.inspector_box.setStyleSheet("""
            QTextEdit {
                background-color: #0D1117;
                color: #E6EDF3;
                border: 1px solid #21262D;
                border-radius: 4px;
                font-family: 'Segoe UI', Consolas, monospace;
                font-size: 12px;
                padding: 6px;
            }
        """)
        inspector_layout.addWidget(self.inspector_box)
        right_layout.addWidget(inspector_frame)

        splitter.addWidget(right_frame)
        splitter.setSizes([190, 950])

        main_layout.addWidget(splitter)

    def _on_search_changed(self):
        self._search_timer.start()

    def _on_date_preset_changed(self):
        preset = self.cmb_date_preset.currentText()
        today = QDate.currentDate()
        self.dt_from.blockSignals(True)
        self.dt_to.blockSignals(True)

        if preset == "Today":
            self.dt_from.setDate(today)
            self.dt_to.setDate(today)
        elif preset == "Yesterday":
            self.dt_from.setDate(today.addDays(-1))
            self.dt_to.setDate(today.addDays(-1))
        elif preset == "Last 7 Days":
            self.dt_from.setDate(today.addDays(-7))
            self.dt_to.setDate(today)
        elif preset == "Last 30 Days":
            self.dt_from.setDate(today.addDays(-30))
            self.dt_to.setDate(today)
        elif preset == "All Time":
            self.dt_from.setDate(QDate(2020, 1, 1))
            self.dt_to.setDate(today)

        self.dt_from.blockSignals(False)
        self.dt_to.blockSignals(False)
        self._load_logs()

    def _on_custom_date_changed(self):
        if self.cmb_date_preset.currentText() != "Custom Date Range":
            self.cmb_date_preset.blockSignals(True)
            self.cmb_date_preset.setCurrentText("Custom Date Range")
            self.cmb_date_preset.blockSignals(False)
        self._load_logs()

    def _load_workstations(self):
        self.workstation_list.blockSignals(True)
        self.workstation_list.clear()

        # Local Workstation
        item_local = QListWidgetItem("🖥️ Local Workstation")
        item_local.setData(Qt.UserRole, "local")
        self.workstation_list.addItem(item_local)

        # Peer Workstations
        peer_ws = self.peer_mgr.get_peer_workstations()
        for ws in peer_ws:
            item = QListWidgetItem(f"💻 {ws['hostname']}")
            item.setData(Qt.UserRole, ws['hostname'])
            self.workstation_list.addItem(item)

        self.workstation_list.setCurrentRow(0)
        self.workstation_list.blockSignals(False)

    def _on_workstation_changed(self, current: QListWidgetItem, previous: QListWidgetItem):
        if not current:
            return
        self.selected_host = current.data(Qt.UserRole)
        self._load_logs()

    def _load_logs(self):
        actor_filter = self.actor_input.text().strip()
        action_filter = self.action_combo.currentText()
        preset = self.cmb_date_preset.currentText()

        from_date = None
        to_date = None
        if preset != "All Time":
            from_date = self.dt_from.date().toString("yyyy-MM-dd") + "T00:00:00"
            to_date = self.dt_to.date().addDays(1).toString("yyyy-MM-dd") + "T00:00:00"

        if self.selected_host == "local":
            logs = self.db.get_audit_logs(
                actor=actor_filter, action=action_filter,
                from_date=from_date, to_date=to_date, resolve_names=True, limit=1000
            )
        else:
            logs = self.peer_mgr.get_peer_logs(
                hostname=self.selected_host, actor=actor_filter, action=action_filter,
                from_date=from_date, to_date=to_date, limit=1000
            )

        self._raw_logs_cache = logs
        self._apply_client_side_filter()

    def _apply_client_side_filter(self):
        query = self.txt_search.text().strip().lower()
        actor_filter = self.actor_input.text().strip().lower()

        filtered = []
        for l in self._raw_logs_cache:
            if actor_filter and actor_filter not in str(l.get("actor", "")).lower():
                continue
            if query:
                match_fields = [
                    str(l.get("id", "")),
                    str(l.get("client_name", "")),
                    str(l.get("client_token", "")),
                    str(l.get("actor", "")),
                    str(l.get("action", "")),
                    str(l.get("service_name", "")),
                    str(l.get("detail", ""))
                ]
                if not any(query in f.lower() for f in match_fields):
                    continue
            filtered.append(l)

        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(len(filtered))
            self.lbl_record_count.setText(f"{len(filtered)} log entries")

            for row, log in enumerate(filtered):
                # 0. ID
                id_item = QTableWidgetItem(str(log["id"]))
                id_item.setTextAlignment(Qt.AlignCenter)
                id_item.setForeground(QColor("#8B949E"))
                self.table.setItem(row, 0, id_item)

                # 1. Date & Time (Local IST Conversion)
                local_ts, raw_utc = _format_to_local_time(log["ts"])
                ts_item = QTableWidgetItem(local_ts)
                ts_item.setToolTip(f"Local Time: {local_ts}\nUTC: {raw_utc}")
                ts_item.setForeground(QColor("#E6EDF3"))
                self.table.setItem(row, 1, ts_item)

                # 2. Actor
                actor_item = QTableWidgetItem(str(log["actor"] or "System"))
                actor_item.setFont(QFont("Segoe UI", 9, QFont.Bold))
                actor_item.setForeground(QColor("#4CF9B7") if log["actor"] != "System" else QColor("#8B949E"))
                self.table.setItem(row, 2, actor_item)

                # 3. Action Badge
                badge_label, fg_col, bg_col = _get_action_badge_style(log["action"])
                action_item = QTableWidgetItem(badge_label)
                action_item.setFont(QFont("Segoe UI", 9, QFont.Bold))
                action_item.setForeground(QColor(fg_col))
                action_item.setToolTip(f"Action: {log['action']}")
                self.table.setItem(row, 3, action_item)

                # 4. Client (Name / Token)
                client_display = log.get("client_name") or (f"CLI-{log['client_id']:05d}" if log.get("client_id") else "—")
                item_client = QTableWidgetItem(client_display)
                item_client.setFont(QFont("Segoe UI", 9, QFont.Bold))
                item_client.setForeground(QColor("#FFFFFF") if client_display != "—" else QColor("#8B949E"))
                item_client.setData(Qt.UserRole, log)
                item_client.setToolTip(f"Client: {client_display} (ID: {log.get('client_id', 'N/A')})")
                self.table.setItem(row, 4, item_client)

                # 5. Service Name
                srv_str = log.get("service_name") or (str(log["service_id"]) if log.get("service_id") else "—")
                srv_item = QTableWidgetItem(srv_str)
                srv_item.setTextAlignment(Qt.AlignCenter)
                srv_item.setForeground(QColor("#58A6FF") if srv_str != "—" else QColor("#8B949E"))
                srv_item.setToolTip(f"Service: {srv_str}")
                self.table.setItem(row, 5, srv_item)

                # 6. Detail
                det_str = log.get("detail") or ("Viewed client profile" if log.get("action") == "view" else "—")
                det_item = QTableWidgetItem(det_str)
                det_item.setForeground(QColor("#C9D1D9") if det_str != "—" else QColor("#6E7681"))
                det_item.setToolTip(det_str)
                self.table.setItem(row, 6, det_item)
        finally:
            self.table.setUpdatesEnabled(True)

    def _on_row_selected(self):
        row = self.table.currentRow()
        if row < 0:
            self.inspector_box.clear()
            return
        item_client = self.table.item(row, 4)
        if not item_client:
            return
        log = item_client.data(Qt.UserRole)
        if not log:
            return

        local_ts, raw_utc = _format_to_local_time(log.get("ts", ""))
        srv_name = log.get("service_name") or (f"ID: {log['service_id']}" if log.get("service_id") else "None")
        det_val = log.get("detail") or ("Viewed client profile" if log.get("action") == "view" else "None")

        detail_text = (
            f"📌 Log ID: {log.get('id')}  |  🕒 Local Time: {local_ts}  |  🌍 UTC: {raw_utc}\n"
            f"💻 Workstation: {self.selected_host}  |  👤 Actor: {log.get('actor', 'System')}  |  ⚡ Action: {log.get('action')}\n"
            f"🏢 Client: {log.get('client_name', '—')} (Token: {log.get('client_token', 'N/A')} | ID: {log.get('client_id', 'N/A')})  |  🌐 Service: {srv_name}\n"
            f"📝 Detail Payload: {det_val}"
        )
        self.inspector_box.setPlainText(detail_text)

    def _on_copy_inspector(self):
        text = self.inspector_box.toPlainText().strip()
        if text:
            clipboard = QGuiApplication.clipboard()
            clipboard.setText(text)
            self.toast_requested.emit("Audit details copied to clipboard!", 2500)

    def _on_filter_by_selected_client(self):
        row = self.table.currentRow()
        if row < 0: return
        item_client = self.table.item(row, 4)
        if item_client:
            log = item_client.data(Qt.UserRole)
            if log and log.get("client_name") and log["client_name"] != "—":
                self.txt_search.setText(log["client_name"])
            elif log and log.get("client_token"):
                self.txt_search.setText(log["client_token"])

    def _on_export_log_csv(self):
        default_filename = f"audit_log_{self.selected_host}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, f"Export SSAL Audit Log ({self.selected_host}) to CSV", default_filename, "CSV Files (*.csv)"
        )
        if path:
            try:
                actor_filter = self.actor_input.text().strip()
                action_filter = self.action_combo.currentText()
                preset = self.cmb_date_preset.currentText()

                from_date = None
                to_date = None
                if preset != "All Time":
                    from_date = self.dt_from.date().toString("yyyy-MM-dd") + "T00:00:00"
                    to_date = self.dt_to.date().addDays(1).toString("yyyy-MM-dd") + "T00:00:00"

                if self.selected_host == "local":
                    logs = self.db.get_audit_logs(
                        actor=actor_filter, action=action_filter,
                        from_date=from_date, to_date=to_date, resolve_names=True, limit=10000
                    )
                else:
                    logs = self.peer_mgr.get_peer_logs(
                        hostname=self.selected_host, actor=actor_filter, action=action_filter,
                        from_date=from_date, to_date=to_date, limit=10000
                    )

                with open(path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        "ID", "Local Time (IST)", "Timestamp (UTC)", "Workstation", "Actor", "Action",
                        "Client Token", "Client Name", "Service", "Detail"
                    ])
                    for l in logs:
                        local_ts, raw_utc = _format_to_local_time(l.get("ts", ""))
                        writer.writerow([
                            l.get("id"),
                            local_ts,
                            raw_utc,
                            self.selected_host,
                            l.get("actor"),
                            l.get("action"),
                            l.get("client_token", ""),
                            l.get("client_name", ""),
                            l.get("service_name") or l.get("service_id", ""),
                            l.get("detail", "")
                        ])

                try:
                    self.db.log_action(self.actor, "csv_export", detail=f"Exported SSAL audit log ({self.selected_host}) to {path}")
                except Exception:
                    pass

                self.toast_requested.emit(f"SSAL Audit log ({self.selected_host}) exported successfully to:\n{path}", 3000)
            except Exception as e:
                QMessageBox.critical(self, "Export Error", f"Could not export audit log CSV:\n{e!s}")
