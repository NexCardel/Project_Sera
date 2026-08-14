"""
tracker_dump_window.py
----------------------
Dedicated workspace window for inspecting, filtering, and managing captured
filing submissions, ARNs, and raw technical payloads received from the browser
extension and Sera_API_detection (SAD).
"""

import json
import csv
from pathlib import Path
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QComboBox, QMessageBox, QDialog, QTextEdit, QFrame,
    QFileDialog
)

try:
    import qtawesome as qta
except Exception:
    qta = None


def _safe_qta_icon(icon_name, color="#FFFFFF"):
    if qta is not None:
        try:
            return qta.icon(icon_name, color=color)
        except Exception:
            pass
    from PySide6.QtGui import QIcon
    return QIcon()


class PayloadInspectorDialog(QDialog):
    """Modal dialog displaying formatted raw JSON payload captured by SAD or extension."""
    def __init__(self, item_data: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Tracker Dump Payload - ARN: {item_data.get('arn_number', 'N/A')}")
        self.resize(650, 500)
        self.setStyleSheet("""
            QDialog {
                background-color: #121212;
                color: #F8F5F2;
            }
            QLabel {
                color: #F8F5F2;
                font-size: 13px;
            }
            QTextEdit {
                background-color: #0A0A0A;
                color: #39FF14;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 12px;
                border: 1px solid #2E9B5F;
                border-radius: 6px;
                padding: 10px;
            }
            QPushButton {
                background-color: #2E9B5F;
                color: #FFFFFF;
                font-weight: 600;
                border: none;
                border-radius: 4px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #247C4C;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header Info
        header_lbl = QLabel(
            f"<b>Client:</b> {item_data.get('client_name', 'N/A')} &nbsp;|&nbsp; "
            f"<b>PAN:</b> {item_data.get('pan', 'N/A')} &nbsp;|&nbsp; "
            f"<b>Portal:</b> {item_data.get('portal', 'N/A')}<br>"
            f"<b>Period:</b> {item_data.get('period_label', 'N/A')} &nbsp;|&nbsp; "
            f"<b>Capture Method:</b> <span style='color:#2E9B5F;'>{item_data.get('capture_method', 'N/A')}</span> &nbsp;|&nbsp; "
            f"<b>Timestamp:</b> {item_data.get('created_at', 'N/A')}"
        )
        header_lbl.setTextFormat(Qt.RichText)
        layout.addWidget(header_lbl)

        # JSON Viewer
        self.txt_json = QTextEdit()
        self.txt_json.setReadOnly(True)

        raw_str = item_data.get('raw_payload_json', '{}')
        try:
            parsed = json.loads(raw_str)
            formatted = json.dumps(parsed, indent=4)
        except Exception:
            formatted = raw_str

        self.txt_json.setText(formatted)
        layout.addWidget(self.txt_json)

        # Actions
        btn_box = QHBoxLayout()
        btn_box.addStretch()

        btn_copy = QPushButton("Copy JSON")
        btn_copy.clicked.connect(lambda: self.txt_json.selectAll() or self.txt_json.copy())

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)

        btn_box.addWidget(btn_copy)
        btn_box.addWidget(btn_close)
        layout.addLayout(btn_box)


class TrackerDumpWindow(QWidget):
    """Full-featured workspace for inspecting client tracker dumps."""
    
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._dumps_cache = []
        self._setup_ui()

    def _setup_ui(self):
        self.setStyleSheet("""
            QWidget {
                background-color: #292929;
                color: #F8F5F2;
                font-family: 'Segoe UI', sans-serif;
            }
            QFrame#HeaderCard {
                background-color: #0A0A0A;
                border-bottom: 2px solid #2E9B5F;
                border-radius: 0px;
            }
            QLabel#TitleLbl {
                font-size: 18px;
                font-weight: 700;
                color: #F8F5F2;
            }
            QLabel#SubtitleLbl {
                font-size: 12px;
                color: #A0A0A0;
            }
            QLineEdit, QComboBox {
                background-color: #171717;
                border: 1px solid #444444;
                border-radius: 5px;
                color: #F8F5F2;
                padding: 6px 10px;
                font-size: 13px;
            }
            QLineEdit:focus, QComboBox:focus {
                border: 1px solid #2E9B5F;
            }
            QPushButton.ActionBtn {
                background-color: #2E9B5F;
                color: #FFFFFF;
                font-weight: 600;
                border: none;
                border-radius: 5px;
                padding: 7px 14px;
                font-size: 12px;
            }
            QPushButton.ActionBtn:hover {
                background-color: #247C4C;
            }
            QPushButton.DangerBtn {
                background-color: #D9534F;
                color: #FFFFFF;
                font-weight: 600;
                border: none;
                border-radius: 5px;
                padding: 7px 14px;
                font-size: 12px;
            }
            QPushButton.DangerBtn:hover {
                background-color: #C9302C;
            }
            QTableWidget {
                background-color: #171717;
                gridline-color: #333333;
                border: 1px solid #333333;
                border-radius: 6px;
                color: #F8F5F2;
                selection-background-color: #2E9B5F;
                selection-color: #FFFFFF;
            }
            QHeaderView::section {
                background-color: #0A0A0A;
                color: #2E9B5F;
                font-weight: 700;
                font-size: 12px;
                padding: 8px;
                border: none;
                border-bottom: 2px solid #2E9B5F;
            }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(12)

        # Top Header Card
        header_card = QFrame()
        header_card.setObjectName("HeaderCard")
        header_layout = QHBoxLayout(header_card)
        header_layout.setContentsMargins(14, 12, 14, 12)

        title_vbox = QVBoxLayout()
        lbl_title = QLabel("Tracker Dump Workspace")
        lbl_title.setObjectName("TitleLbl")
        lbl_sub = QLabel("Client-connected filing logs captured via Extension & Sera API Detection (SAD)")
        lbl_sub.setObjectName("SubtitleLbl")
        title_vbox.addWidget(lbl_title)
        title_vbox.addWidget(lbl_sub)
        header_layout.addLayout(title_vbox)

        header_layout.addStretch()

        self.lbl_counter = QLabel("Records: 0")
        self.lbl_counter.setStyleSheet("font-weight: 700; color: #4CF9B7; font-size: 13px; background-color: #1A382B; padding: 6px 12px; border-radius: 4px;")
        header_layout.addWidget(self.lbl_counter)

        btn_refresh = QPushButton("Refresh")
        btn_refresh.setProperty("class", "ActionBtn")
        btn_refresh.setIcon(_safe_qta_icon("mdi.refresh", "#FFFFFF"))
        btn_refresh.clicked.connect(self.load_data)
        header_layout.addWidget(btn_refresh)

        btn_export = QPushButton("Export CSV")
        btn_export.setProperty("class", "ActionBtn")
        btn_export.setIcon(_safe_qta_icon("mdi.file-export", "#FFFFFF"))
        btn_export.clicked.connect(self._export_csv)
        header_layout.addWidget(btn_export)

        btn_purge = QPushButton("Clear All")
        btn_purge.setProperty("class", "DangerBtn")
        btn_purge.setIcon(_safe_qta_icon("mdi.delete-sweep", "#FFFFFF"))
        btn_purge.clicked.connect(self._clear_all_dumps)
        header_layout.addWidget(btn_purge)

        main_layout.addWidget(header_card)

        # Search & Filter Controls
        filter_layout = QHBoxLayout()
        filter_layout.setSpacing(10)

        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("Search Client Name, PAN, GSTIN, ARN, Period, Portal...")
        self.txt_search.textChanged.connect(self._apply_filters)
        filter_layout.addWidget(self.txt_search, stretch=3)

        self.cmb_method = QComboBox()
        self.cmb_method.addItems(["All Capture Methods", "SAD_API_Interceptor", "DOM_Tracker", "Manual_Fallback"])
        self.cmb_method.currentIndexChanged.connect(self._apply_filters)
        filter_layout.addWidget(self.cmb_method, stretch=1)

        self.cmb_status = QComboBox()
        self.cmb_status.addItems(["All Statuses", "submitted", "pending", "uncertain"])
        self.cmb_status.currentIndexChanged.connect(self._apply_filters)
        filter_layout.addWidget(self.cmb_status, stretch=1)

        main_layout.addLayout(filter_layout)

        # Data Table
        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels([
            "ID", "Client Name & PAN", "Service / Portal", "Period",
            "ARN / Ack Number", "Capture Method", "Timestamp", "Actions"
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeToContents)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("""
            QTableWidget::item { padding: 6px; }
            QTableWidget::item:alternate { background-color: #1E1E1E; }
        """)

        main_layout.addWidget(self.table)

        # Initial Load
        self.load_data()

    def load_data(self):
        """Fetch tracker dumps from database."""
        try:
            self._dumps_cache = self.db.get_tracker_dumps(limit=300)
            self._apply_filters()
        except Exception as e:
            QMessageBox.critical(self, "Error Loading Dumps", f"Could not load tracker dumps: {e}")

    def _apply_filters(self):
        """Filter cached dump records and populate table."""
        search_txt = self.txt_search.text().strip().lower()
        method_filter = self.cmb_method.currentText()
        status_filter = self.cmb_status.currentText()

        filtered = []
        for d in self._dumps_cache:
            # Method Filter
            if method_filter != "All Capture Methods" and d.get("capture_method") != method_filter:
                continue

            # Status Filter
            if status_filter != "All Statuses" and d.get("status") != status_filter:
                continue

            # Search Text
            if search_txt:
                match_fields = [
                    d.get("client_name", ""), d.get("pan", ""),
                    d.get("portal", ""), d.get("service_name", ""),
                    d.get("period_label", ""), d.get("arn_number", ""),
                    d.get("captured_by", "")
                ]
                if not any(search_txt in str(f).lower() for f in match_fields):
                    continue

            filtered.append(d)

        self._populate_table(filtered)

    def _populate_table(self, records: list[dict]):
        self.table.setRowCount(0)
        self.lbl_counter.setText(f"Records: {len(records)}")

        for row_idx, r in enumerate(records):
            self.table.insertRow(row_idx)

            # ID
            id_item = QTableWidgetItem(str(r["id"]))
            id_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row_idx, 0, id_item)

            # Client Name & PAN
            client_str = f"{r['client_name']} ({r['pan']})" if r.get('pan') else r['client_name']
            c_item = QTableWidgetItem(client_str)
            c_item.setFont(QFont("Segoe UI", 9, QFont.Bold))
            self.table.setItem(row_idx, 1, c_item)

            # Service / Portal
            portal_str = r.get("service_name") or r.get("portal") or "Portal"
            self.table.setItem(row_idx, 2, QTableWidgetItem(portal_str))

            # Period
            self.table.setItem(row_idx, 3, QTableWidgetItem(r.get("period_label", "N/A")))

            # ARN Number
            arn_item = QTableWidgetItem(r.get("arn_number", "N/A"))
            arn_item.setFont(QFont("Consolas", 9, QFont.Bold))
            arn_item.setForeground(QColor("#4CF9B7"))
            self.table.setItem(row_idx, 4, arn_item)

            # Capture Method Badge
            method = r.get("capture_method", "DOM_Tracker")
            method_item = QTableWidgetItem(method)
            method_item.setTextAlignment(Qt.AlignCenter)
            if method == "SAD_API_Interceptor":
                method_item.setForeground(QColor("#00FF66")) # Neon Emerald
            elif method == "DOM_Tracker":
                method_item.setForeground(QColor("#33B5E5")) # Sky Blue
            else:
                method_item.setForeground(QColor("#FFBB33")) # Amber
            self.table.setItem(row_idx, 5, method_item)

            # Timestamp
            ts_str = r.get("created_at", "")[:19].replace("T", " ")
            self.table.setItem(row_idx, 6, QTableWidgetItem(ts_str))

            # Actions Column
            action_widget = QWidget()
            action_layout = QHBoxLayout(action_widget)
            action_layout.setContentsMargins(2, 2, 2, 2)
            action_layout.setSpacing(4)

            btn_view = QPushButton("View Payload")
            btn_view.setStyleSheet("""
                QPushButton {
                    background-color: #1A382B;
                    color: #4CF9B7;
                    border: 1px solid #2E9B5F;
                    border-radius: 4px;
                    padding: 3px 8px;
                    font-size: 11px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    background-color: #2E9B5F;
                    color: #FFFFFF;
                }
            """)
            btn_view.clicked.connect(lambda _, item=r: self._show_payload_dialog(item))
            action_layout.addWidget(btn_view)

            btn_del = QPushButton("Delete")
            btn_del.setStyleSheet("""
                QPushButton {
                    background-color: #331A1A;
                    color: #FF6B6B;
                    border: 1px solid #882222;
                    border-radius: 4px;
                    padding: 3px 8px;
                    font-size: 11px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    background-color: #D9534F;
                    color: #FFFFFF;
                }
            """)
            btn_del.clicked.connect(lambda _, dump_id=r["id"]: self._delete_dump(dump_id))
            action_layout.addWidget(btn_del)

            self.table.setCellWidget(row_idx, 7, action_widget)

    def _show_payload_dialog(self, dump_item: dict):
        dlg = PayloadInspectorDialog(dump_item, self)
        dlg.exec()

    def _delete_dump(self, dump_id: int):
        if QMessageBox.question(
            self, "Confirm Delete",
            f"Are you sure you want to delete Tracker Dump record #{dump_id}?",
            QMessageBox.Yes | QMessageBox.No
        ) == QMessageBox.Yes:
            self.db.delete_tracker_dump(dump_id)
            self.load_data()

    def _clear_all_dumps(self):
        if QMessageBox.warning(
            self, "Clear All Dump Logs",
            "Are you sure you want to clear ALL tracker dump logs?\nThis operation cannot be undone.",
            QMessageBox.Yes | QMessageBox.No
        ) == QMessageBox.Yes:
            self.db.clear_tracker_dumps()
            self.load_data()

    def _export_csv(self):
        if not self._dumps_cache:
            QMessageBox.information(self, "Export CSV", "No records to export.")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Tracker Dump CSV", "Sera_Tracker_Dump_Export.csv", "CSV Files (*.csv)"
        )
        if not file_path:
            return

        try:
            with open(file_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "ID", "Client ID", "Client Name", "PAN", "Service ID", "Service Name",
                    "Portal", "Period Label", "ARN Number", "Capture Method", "Status",
                    "Captured By", "Created At", "Raw Payload JSON"
                ])
                for r in self._dumps_cache:
                    writer.writerow([
                        r.get("id"), r.get("client_id"), r.get("client_name"), r.get("pan"),
                        r.get("service_id"), r.get("service_name"), r.get("portal"),
                        r.get("period_label"), r.get("arn_number"), r.get("capture_method"),
                        r.get("status"), r.get("captured_by"), r.get("created_at"),
                        r.get("raw_payload_json")
                    ])
            QMessageBox.information(self, "Export Complete", f"Exported {len(self._dumps_cache)} records to:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", f"Could not write CSV: {e}")
