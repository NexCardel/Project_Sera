"""
audit_log_dialog.py
--------------------
Redesigned wide SSAL (Sera-Sync Audit Log) window for Project Sera v2.4.0.
Displays local audit log and peer workstation logs received by the Host PC.
Includes date range filters, multi-select action filter, client name resolution,
token-safe CSV export, and row drill-down details.
"""

import os
from PySide6.QtCore import Qt, Signal, QDate
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
)
from database import PeerAuditLogManager


class AuditLogDialog(QDialog):
    toast_requested = Signal(str, int)

    def __init__(self, db, actor: str = "Admin", parent=None):
        super().__init__(parent)
        self.setObjectName("ToolDialog")
        self.db = db
        self.actor = actor
        self.setWindowTitle("Project Sera — Sera-Sync Audit Log (SSAL)")
        # Spacious wide dialog window (1120x680)
        self.resize(1120, 680)
        self.setMinimumSize(980, 540)

        live_dir = os.path.dirname(self.db.db_path) if hasattr(self.db, "db_path") else "."
        self.peer_mgr = PeerAuditLogManager(live_dir)
        self.selected_host = "local"  # "local" or hostname

        self._build_ui()
        self._load_workstations()
        self._load_logs()

        # Self-referential audit log entry
        try:
            self.db.log_action(self.actor, "audit_log_viewed", detail="Opened SSAL Audit Log Window")
        except Exception:
            pass

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(18, 18, 18, 18)
        main_layout.setSpacing(12)

        # Header Title Row
        title_row = QHBoxLayout()
        title_row.setSpacing(10)

        title = QLabel("Sera-Sync Audit Log (SSAL)")
        title.setProperty("class", "DialogTitle")
        title.setStyleSheet("font-size: 18px; font-weight: 700; color: #241F1B;")
        title_row.addWidget(title)

        title_row.addStretch()

        self.host_badge = QLabel("🟢 Host Aggregator Active")
        self.host_badge.setStyleSheet("color: #2E9B5F; font-weight: 700; font-size: 12px; padding: 4px 10px; background-color: #E2F5EA; border-radius: 6px;")
        title_row.addWidget(self.host_badge)

        btn_close_top = QPushButton("Close")
        btn_close_top.setFixedWidth(70)
        btn_close_top.clicked.connect(self.accept)
        title_row.addWidget(btn_close_top)

        main_layout.addLayout(title_row)

        # Main Splitter: Left Sidebar (Workstations) + Right Area (Audit Logs Table)
        splitter = QSplitter(Qt.Horizontal)

        # ---------------- Left Sidebar (Workstations / Users) ----------------
        sidebar_frame = QFrame()
        sidebar_frame.setFixedWidth(180)
        sidebar_frame.setStyleSheet("""
            QFrame {
                background-color: #1A232A;
                border-radius: 8px;
                border: 1px solid #2C3842;
            }
        """)
        sidebar_layout = QVBoxLayout(sidebar_frame)
        sidebar_layout.setContentsMargins(8, 10, 8, 10)
        sidebar_layout.setSpacing(8)

        sidebar_title = QLabel("WORKSTATIONS (SSAL)")
        sidebar_title.setStyleSheet("color: #8D99AE; font-size: 10px; font-weight: 700; letter-spacing: 0.5px;")
        sidebar_layout.addWidget(sidebar_title)

        self.workstation_list = QListWidget()
        self.workstation_list.setStyleSheet("""
            QListWidget {
                background-color: transparent;
                border: none;
                color: #FFFFFF;
                font-size: 12px;
            }
            QListWidget::item {
                padding: 7px 8px;
                border-radius: 6px;
                margin-bottom: 2px;
            }
            QListWidget::item:selected {
                background-color: #2E9B5F;
                color: #FFFFFF;
                font-weight: bold;
            }
            QListWidget::item:hover:!selected {
                background-color: #25333D;
            }
        """)
        self.workstation_list.currentItemChanged.connect(self._on_workstation_changed)
        sidebar_layout.addWidget(self.workstation_list)

        splitter.addWidget(sidebar_frame)

        # ---------------- Right Main Log Area ----------------
        right_frame = QFrame()
        right_layout = QVBoxLayout(right_frame)
        right_layout.setContentsMargins(6, 0, 0, 0)
        right_layout.setSpacing(10)

        # Filter Controls Bar
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)

        lbl_actor = QLabel("Actor:")
        lbl_actor.setStyleSheet("font-weight: 600; color: #241F1B;")
        filter_row.addWidget(lbl_actor)

        self.actor_input = QLineEdit()
        self.actor_input.setPlaceholderText("Filter staff...")
        self.actor_input.setFixedWidth(110)
        self.actor_input.textChanged.connect(self._load_logs)
        filter_row.addWidget(self.actor_input)

        lbl_action = QLabel("Action:")
        lbl_action.setStyleSheet("font-weight: 600; color: #241F1B;")
        filter_row.addWidget(lbl_action)

        self.action_combo = QComboBox()
        self.action_combo.setFixedWidth(130)
        self.action_combo.addItems([
            "All Actions", "view", "autofill", "manual_copy",
            "create", "update", "archive", "unarchive", "delete",
            "csv_import", "backup", "restore", "csv_export",
            "filing_submitted", "sync_pushed", "sync_received", "audit_log_viewed"
        ])
        self.action_combo.currentIndexChanged.connect(self._load_logs)
        filter_row.addWidget(self.action_combo)

        # Date Range Enable Checkbox & Date Pickers
        self.chk_date_range = QCheckBox("Date Range:")
        self.chk_date_range.setStyleSheet("font-weight: 600; color: #241F1B;")
        self.chk_date_range.toggled.connect(self._toggle_date_range)
        filter_row.addWidget(self.chk_date_range)

        self.dt_from = QDateEdit(QDate.currentDate().addDays(-30))
        self.dt_from.setCalendarPopup(True)
        self.dt_from.setDisplayFormat("yyyy-MM-dd")
        self.dt_from.setEnabled(False)
        self.dt_from.setFixedWidth(105)
        self.dt_from.dateChanged.connect(self._load_logs)
        filter_row.addWidget(self.dt_from)

        lbl_to = QLabel("to")
        lbl_to.setStyleSheet("color: #241F1B;")
        filter_row.addWidget(lbl_to)

        self.dt_to = QDateEdit(QDate.currentDate())
        self.dt_to.setCalendarPopup(True)
        self.dt_to.setDisplayFormat("yyyy-MM-dd")
        self.dt_to.setEnabled(False)
        self.dt_to.setFixedWidth(105)
        self.dt_to.dateChanged.connect(self._load_logs)
        filter_row.addWidget(self.dt_to)

        filter_row.addStretch()

        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self._load_logs)
        filter_row.addWidget(btn_refresh)

        btn_export = QPushButton("Export Log CSV...")
        btn_export.setStyleSheet("QPushButton { background-color: #2E9B5F; color: white; font-weight: 600; padding: 5px 12px; border-radius: 5px; }")
        btn_export.clicked.connect(self._on_export_log_csv)
        filter_row.addWidget(btn_export)

        right_layout.addLayout(filter_row)

        # Wide Table (7 Columns)
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "ID", "Timestamp (UTC)", "Actor", "Action", "Client (Name / Token)", "Service ID", "Detail"
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        self.table.setColumnWidth(1, 140)
        self.table.setColumnWidth(2, 110)
        self.table.setColumnWidth(3, 110)
        self.table.setColumnWidth(4, 160)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        right_layout.addWidget(self.table)

        # Inspection Box at Bottom
        self.inspector_box = QTextEdit()
        self.inspector_box.setReadOnly(True)
        self.inspector_box.setMaximumHeight(75)
        self.inspector_box.setPlaceholderText("Select any audit log row above to view full drill-down details...")
        self.inspector_box.setStyleSheet("""
            QTextEdit {
                background-color: #1A232A;
                color: #4CF9B7;
                border: 1px solid #2C3842;
                border-radius: 6px;
                font-family: Consolas, monospace;
                font-size: 11px;
                padding: 6px;
            }
        """)
        right_layout.addWidget(self.inspector_box)

        splitter.addWidget(right_frame)
        splitter.setSizes([180, 920])

        main_layout.addWidget(splitter)

    def _toggle_date_range(self, enabled: bool):
        self.dt_from.setEnabled(enabled)
        self.dt_to.setEnabled(enabled)
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

        from_date = None
        to_date = None
        if self.chk_date_range.isChecked():
            from_date = self.dt_from.date().toString("yyyy-MM-dd") + "T00:00:00"
            to_date = self.dt_to.date().addDays(1).toString("yyyy-MM-dd") + "T00:00:00"

        if self.selected_host == "local":
            logs = self.db.get_audit_logs(
                actor=actor_filter, action=action_filter,
                from_date=from_date, to_date=to_date, resolve_names=True, limit=500
            )
        else:
            logs = self.peer_mgr.get_peer_logs(
                hostname=self.selected_host, actor=actor_filter, action=action_filter,
                from_date=from_date, to_date=to_date, limit=500
            )

        self.table.setRowCount(len(logs))
        for row, log in enumerate(logs):
            self.table.setItem(row, 0, QTableWidgetItem(str(log["id"])))
            self.table.setItem(row, 1, QTableWidgetItem(log["ts"]))
            self.table.setItem(row, 2, QTableWidgetItem(log["actor"]))
            self.table.setItem(row, 3, QTableWidgetItem(log["action"]))
            client_display = log.get("client_name") or (f"CLI-{log['client_id']:05d}" if log.get("client_id") else "—")
            item_client = QTableWidgetItem(client_display)
            item_client.setData(Qt.UserRole, log)
            self.table.setItem(row, 4, item_client)
            self.table.setItem(row, 5, QTableWidgetItem(str(log["service_id"]) if log["service_id"] else "—"))
            self.table.setItem(row, 6, QTableWidgetItem(log["detail"] or "—"))

    def _on_row_selected(self):
        sel_items = self.table.selectedItems()
        if not sel_items:
            self.inspector_box.clear()
            return
        row = self.table.currentRow()
        item_client = self.table.item(row, 4)
        if not item_client:
            return
        log = item_client.data(Qt.UserRole)
        if not log:
            return

        detail_text = (
            f"Log ID: {log.get('id')} | Timestamp (UTC): {log.get('ts')}\n"
            f"Workstation: {self.selected_host} | Actor: {log.get('actor')} | Action: {log.get('action')}\n"
            f"Client: {log.get('client_name', '—')} (ID: {log.get('client_id', '—')}) | Service ID: {log.get('service_id', '—')}\n"
            f"Detail Payload: {log.get('detail', 'None')}"
        )
        self.inspector_box.setPlainText(detail_text)

    def _on_export_log_csv(self):
        path, _ = QFileDialog.getSaveFileName(
            self, f"Export SSAL Audit Log ({self.selected_host}) to CSV", f"audit_log_{self.selected_host}.csv", "CSV Files (*.csv)"
        )
        if path:
            try:
                import csv
                actor_filter = self.actor_input.text().strip()
                action_filter = self.action_combo.currentText()
                from_date = None
                to_date = None
                if self.chk_date_range.isChecked():
                    from_date = self.dt_from.date().toString("yyyy-MM-dd") + "T00:00:00"
                    to_date = self.dt_to.date().addDays(1).toString("yyyy-MM-dd") + "T00:00:00"

                if self.selected_host == "local":
                    logs = self.db.get_audit_logs(
                        actor=actor_filter, action=action_filter,
                        from_date=from_date, to_date=to_date, resolve_names=True, limit=5000
                    )
                else:
                    logs = self.peer_mgr.get_peer_logs(
                        hostname=self.selected_host, actor=actor_filter, action=action_filter,
                        from_date=from_date, to_date=to_date, limit=5000
                    )

                with open(path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(["ID", "Timestamp (UTC)", "Workstation", "Actor", "Action", "Client Token", "Client Name", "Service ID", "Detail"])
                    for l in logs:
                        writer.writerow([
                            l.get("id"),
                            l.get("ts"),
                            self.selected_host,
                            l.get("actor"),
                            l.get("action"),
                            l.get("client_token", ""),
                            l.get("client_name", ""),
                            l.get("service_id", ""),
                            l.get("detail", "")
                        ])

                try:
                    self.db.log_action(self.actor, "csv_export", detail=f"Exported SSAL audit log ({self.selected_host}) to {path}")
                except Exception:
                    pass

                self.toast_requested.emit(f"SSAL Audit log ({self.selected_host}) exported successfully to:\n{path}", 3000)
            except Exception as e:
                QMessageBox.critical(self, "Export Error", f"Could not export audit log CSV:\n{e!s}")
