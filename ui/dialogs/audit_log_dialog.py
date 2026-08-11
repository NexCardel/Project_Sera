"""
audit_log_window.py
--------------------
Dialog window for viewing and filtering audit log entries in Admin mode.
"""

from PySide6.QtCore import Signal
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
)


class AuditLogDialog(QDialog):
    toast_requested = Signal(str, int)

    def __init__(self, db, actor: str = "Admin", parent=None):
        super().__init__(parent)
        self.setObjectName("ToolDialog")
        self.db = db
        self.actor = actor
        self.setWindowTitle("Project Sera — Audit Log")
        self.resize(850, 500)
        self._build_ui()
        self._load_logs()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel("Audit Log")
        title.setProperty("class", "DialogTitle")
        layout.addWidget(title)

        # Filters
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Actor:"))
        self.actor_input = QLineEdit()
        self.actor_input.setPlaceholderText("Filter by actor name...")
        self.actor_input.textChanged.connect(self._load_logs)
        filter_row.addWidget(self.actor_input)

        filter_row.addWidget(QLabel("Action:"))
        self.action_combo = QComboBox()
        self.action_combo.addItems([
            "All Actions", "view", "autofill", "manual_copy",
            "create", "update", "archive", "unarchive", "delete",
            "csv_import", "backup", "restore", "csv_export"
        ])
        self.action_combo.currentIndexChanged.connect(self._load_logs)
        filter_row.addWidget(self.action_combo)

        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self._load_logs)
        filter_row.addWidget(btn_refresh)

        btn_export = QPushButton("Export Log CSV...")
        btn_export.clicked.connect(self._on_export_log_csv)
        filter_row.addWidget(btn_export)

        layout.addLayout(filter_row)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "ID", "Timestamp (UTC)", "Actor", "Action", "Client ID", "Service ID", "Detail"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.table)

    def _load_logs(self):
        actor_filter = self.actor_input.text().strip()
        action_filter = self.action_combo.currentText()
        logs = self.db.get_audit_logs(actor=actor_filter, action=action_filter, limit=500)

        self.table.setRowCount(len(logs))
        for row, log in enumerate(logs):
            self.table.setItem(row, 0, QTableWidgetItem(str(log["id"])))
            self.table.setItem(row, 1, QTableWidgetItem(log["ts"]))
            self.table.setItem(row, 2, QTableWidgetItem(log["actor"]))
            self.table.setItem(row, 3, QTableWidgetItem(log["action"]))
            self.table.setItem(row, 4, QTableWidgetItem(str(log["client_id"]) if log["client_id"] else "—"))
            self.table.setItem(row, 5, QTableWidgetItem(str(log["service_id"]) if log["service_id"] else "—"))
            self.table.setItem(row, 6, QTableWidgetItem(log["detail"] or "—"))

    def _on_export_log_csv(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Audit Log to CSV", "audit_log.csv", "CSV Files (*.csv)"
        )
        if path:
            try:
                self.db.export_audit_log_csv(path)
                self.db.log_action(self.actor, "csv_export", detail=f"Exported audit log to {path}")
                self.toast_requested.emit(f"Audit log exported successfully to:\n{path}", 3000)
            except Exception as e:
                QMessageBox.critical(self, "Export Error", f"Could not export audit log CSV:\n{e!s}")
