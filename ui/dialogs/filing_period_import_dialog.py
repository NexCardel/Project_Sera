"""
filing_period_import_dialog.py
------------------------------
Dialog to import Filing Period Structure (FPS) JSON definitions into database.
"""

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

import drs


class FilingPeriodImportDialog(QDialog):
    toast_requested = Signal(str, int)

    def __init__(self, db, actor: str = "Admin", parent=None):
        super().__init__(parent)
        self.setObjectName("ToolDialog")
        self.db = db
        self.actor = actor
        self.setWindowTitle("Import Filing Period Structures (FPS)")
        self.setModal(True)
        self.resize(750, 520)
        self.json_text = ""
        self.parsed_data = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        info = QLabel(
            "Select an FPS JSON file (e.g. filing_period_structures_v2.json) to import compliance return definitions. "
            "Re-importing an updated file will update existing due date schedules without duplicating filing types or wiping client statuses."
        )
        info.setWordWrap(True)
        info.setProperty("class", "InfoText")
        layout.addWidget(info)

        file_row = QHBoxLayout()
        self.file_label = QLabel("No JSON file selected.")
        browse_btn = QPushButton("Browse JSON...")
        browse_btn.clicked.connect(self._on_browse)
        file_row.addWidget(self.file_label, stretch=1)
        file_row.addWidget(browse_btn)
        layout.addLayout(file_row)

        self.table = QTableWidget()
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(QLabel("Filing Types Preview:"))
        layout.addWidget(self.table)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.import_btn = QPushButton("Import Filing Periods")
        self.import_btn.setProperty("class", "primary")
        self.import_btn.setEnabled(False)
        self.import_btn.clicked.connect(self._on_import)
        close_btn = QPushButton("Cancel")
        close_btn.clicked.connect(self.reject)

        btn_row.addWidget(self.import_btn)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _on_browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select FPS JSON File", "", "JSON Files (*.json)")
        if not path:
            return
        self.file_label.setText(path)
        self._load_preview(path)

    def _load_preview(self, path):
        try:
            with open(path, mode="r", encoding="utf-8") as f:
                self.json_text = f.read()
            data = json.loads(self.json_text)
        except Exception as e:
            QMessageBox.critical(self, "Error Reading JSON", f"Invalid JSON file: {e!s}")
            return

        filing_types = data.get("filing_types") or data.get("filing_period_structures") or (data if isinstance(data, list) else [])

        if not filing_types:
            QMessageBox.warning(self, "Empty FPS File", "No filing types found in this JSON file.")
            return

        raw_services = self.db.get_services()
        db_services = {s["name"].lower().strip(): s["name"] for s in raw_services}

        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Target Service", "Code", "Filing Name", "Frequency / Due", "Service Status"])
        self.table.setRowCount(len(filing_types))

        valid_count = 0
        for i, ft in enumerate(filing_types):
            code = ft.get("code") or ft.get("sub_service_code") or ""
            name = ft.get("name") or ft.get("sub_service_name") or code
            freq = ft.get("frequency", "monthly")
            due = f"Day {ft.get('due_day')}" if ft.get('due_day') else (ft.get('due_day_absolute') or "Default")

            candidates = [
                ft.get("service_code"),
                ft.get("service"),
                ft.get("service_name")
            ]
            candidates = [c.strip().lower() for c in candidates if c and isinstance(c, str) and c.strip()]

            matched_name = None

            # 1. Exact match
            for c in candidates:
                if c in db_services:
                    matched_name = db_services[c]
                    break

            # 2. Substring match fallback
            if matched_name is None:
                for c in candidates:
                    for db_s_lower, db_s_real in db_services.items():
                        if c in db_s_lower or db_s_lower in c:
                            matched_name = db_s_real
                            break
                    if matched_name is not None:
                        break

            disp_svc = matched_name or ft.get("service_name") or ft.get("service") or ft.get("service_code") or "Unknown"

            self.table.setItem(i, 0, QTableWidgetItem(disp_svc))
            self.table.setItem(i, 1, QTableWidgetItem(code))
            self.table.setItem(i, 2, QTableWidgetItem(name))
            self.table.setItem(i, 3, QTableWidgetItem(f"{freq.title()} ({due})"))

            if matched_name is not None:
                status_item = QTableWidgetItem(f"Matched ({matched_name})")
                status_item.setForeground(Qt.darkGreen)
                valid_count += 1
            else:
                status_item = QTableWidgetItem("Service Missing!")
                status_item.setForeground(Qt.red)
            self.table.setItem(i, 4, status_item)

        if valid_count > 0:
            self.import_btn.setEnabled(True)
        else:
            self.import_btn.setEnabled(False)
            QMessageBox.warning(
                self, "Service Mismatch",
                "None of the services specified in this JSON file match your existing Services in Admin Mode.\n\n"
                "Please create matching services in Service Manager first (e.g. GST, TDS, ROC, Income Tax)."
            )

    def _on_import(self):
        if not self.json_text:
            return
        res = drs.import_fps_json(self.db, self.json_text, actor=self.actor)

        msg = f"Imported {res['imported']} new filing type(s).\nUpdated {res['updated']} existing filing type(s).\n\n"
        if res["warnings"]:
            msg += "Warnings:\n" + "\n".join(res["warnings"])

        self.toast_requested.emit(msg.strip(), 5000)
        self.accept()
