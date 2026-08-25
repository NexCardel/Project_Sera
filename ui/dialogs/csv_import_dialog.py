"""
csv_import_dialog.py
-----------------------------
Handles importing clients directly from a CSV file.
"""

import csv

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

try:
    import qtawesome as qta
except Exception:
    qta = None


def _safe_icon(name, color=None):
    if qta:
        try:
            if color:
                return qta.icon(name, color=color)
            return qta.icon(name)
        except Exception:
            pass
    from PySide6.QtGui import QIcon
    return QIcon()


from database import SeraDatabase


class ImportResultDialog(QDialog):
    def __init__(self, results, parent=None):
        super().__init__(parent)
        self.setObjectName("ToolDialog")
        self.setWindowTitle("Import Summary — Project Sera")
        self.resize(560, 420)
        self.setMinimumSize(480, 320)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(14)

        # Header
        header = QHBoxLayout()
        header.setSpacing(10)
        icon_lbl = QLabel()
        icon_lbl.setPixmap(_safe_icon("mdi.check-decagram-outline", color="#2E9B5F").pixmap(26, 26))
        header.addWidget(icon_lbl)

        title_vbox = QVBoxLayout()
        title_vbox.setSpacing(2)
        title_lbl = QLabel("CSV Import Complete")
        title_lbl.setStyleSheet("font-size: 17px; font-weight: 700; color: #F8FAFC;")
        sub_lbl = QLabel("Client records have been processed and saved into the database.")
        sub_lbl.setStyleSheet("font-size: 12px; color: #8E8D88;")
        title_vbox.addWidget(title_lbl)
        title_vbox.addWidget(sub_lbl)
        header.addLayout(title_vbox)
        header.addStretch()
        main_layout.addLayout(header)

        # Stats Cards Row
        stats_row = QHBoxLayout()
        stats_row.setSpacing(10)

        imported_card = QFrame()
        imported_card.setStyleSheet("""
            QFrame {
                background-color: #141414;
                border: 1px solid #262626;
                border-radius: 8px;
                padding: 10px;
            }
        """)
        imp_layout = QVBoxLayout(imported_card)
        imp_layout.setSpacing(4)
        imp_val = QLabel(str(results.get('imported', 0)))
        imp_val.setStyleSheet("font-size: 22px; font-weight: 700; color: #4CF9B7;")
        imp_lbl = QLabel("NEW CLIENTS CREATED")
        imp_lbl.setStyleSheet("font-size: 10px; font-weight: 700; color: #8E8D88; letter-spacing: 0.5px;")
        imp_layout.addWidget(imp_val)
        imp_layout.addWidget(imp_lbl)
        stats_row.addWidget(imported_card)

        updated_card = QFrame()
        updated_card.setStyleSheet("""
            QFrame {
                background-color: #141414;
                border: 1px solid #262626;
                border-radius: 8px;
                padding: 10px;
            }
        """)
        upd_layout = QVBoxLayout(updated_card)
        upd_layout.setSpacing(4)
        upd_val = QLabel(str(results.get('updated', 0)))
        upd_val.setStyleSheet("font-size: 22px; font-weight: 700; color: #4FC3F7;")
        upd_lbl = QLabel("EXISTING CLIENTS UPDATED")
        upd_lbl.setStyleSheet("font-size: 10px; font-weight: 700; color: #8E8D88; letter-spacing: 0.5px;")
        upd_layout.addWidget(upd_val)
        upd_layout.addWidget(upd_lbl)
        stats_row.addWidget(updated_card)

        main_layout.addLayout(stats_row)

        if results.get("skipped_columns"):
            skipped_text = f"Skipped Unmatched Columns: {', '.join(results['skipped_columns'])}"
            lbl_skip = QLabel(skipped_text)
            lbl_skip.setStyleSheet("font-size: 11.5px; color: #FFB74D; padding: 4px;")
            main_layout.addWidget(lbl_skip)

        if results.get("warnings"):
            lbl_warn = QLabel("WARNINGS & NOTES")
            lbl_warn.setProperty("class", "SectionLabel")
            main_layout.addWidget(lbl_warn)

            warn_edit = QTextEdit()
            warn_edit.setReadOnly(True)
            warn_edit.setStyleSheet("""
                QTextEdit {
                    background-color: #141414;
                    border: 1px solid #333333;
                    border-radius: 6px;
                    color: #FF8A80;
                    font-family: 'Consolas', 'Courier New', monospace;
                    font-size: 12px;
                    padding: 8px;
                }
            """)
            warn_edit.setPlainText("\n\n".join(results["warnings"]))
            main_layout.addWidget(warn_edit, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("Done")
        ok_btn.setProperty("class", "primary")
        ok_btn.setIcon(_safe_icon("mdi.check", color="#FFFFFF"))
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)
        main_layout.addLayout(btn_row)


class CSVImportDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.setObjectName("ToolDialog")
        self.db = db
        self.setWindowTitle("Import Clients from CSV — Project Sera")
        self.setModal(True)
        self.resize(740, 540)
        self.setMinimumSize(640, 440)
        self.csv_data = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # Header
        header = QHBoxLayout()
        header.setSpacing(10)
        icon_lbl = QLabel()
        icon_lbl.setPixmap(_safe_icon("mdi.file-delimited-outline", color="#2E9B5F").pixmap(26, 26))
        header.addWidget(icon_lbl)

        title_vbox = QVBoxLayout()
        title_vbox.setSpacing(2)
        title_lbl = QLabel("Import Clients from CSV")
        title_lbl.setStyleSheet("font-size: 17px; font-weight: 700; color: #F8FAFC;")
        sub_lbl = QLabel("Upload spreadsheet data to populate or update your client roster.")
        sub_lbl.setStyleSheet("font-size: 12px; color: #8E8D88;")
        title_vbox.addWidget(title_lbl)
        title_vbox.addWidget(sub_lbl)
        header.addLayout(title_vbox)
        header.addStretch()
        layout.addLayout(header)

        # Divider
        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet("border: none; border-top: 1px solid #262626; margin: 2px 0;")
        layout.addWidget(divider)

        # Instruction Card
        info_frame = QFrame()
        info_frame.setStyleSheet("""
            QFrame {
                background-color: #141414;
                border: 1px solid #262626;
                border-radius: 8px;
                padding: 8px 12px;
            }
        """)
        info_layout = QVBoxLayout(info_frame)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info = QLabel(
            "• Rows matching an existing client's Identity column (e.g. GSTIN / PAN / Name) will <b>UPDATE</b> that record.<br>"
            "• Rows with no match will automatically create a <b>NEW</b> client.<br>"
            "• Attach services by including a <i>'Services'</i> column or columns named <i>'Service: [Name]'</i> with Yes/No values."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #C5C0B8; font-size: 12px; line-height: 1.4;")
        info_layout.addWidget(info)
        layout.addWidget(info_frame)
        
        # File Selector Row
        file_frame = QFrame()
        file_frame.setStyleSheet("""
            QFrame {
                background-color: #171717;
                border: 1px dashed #333333;
                border-radius: 8px;
                padding: 6px 12px;
            }
        """)
        file_row = QHBoxLayout(file_frame)
        file_row.setContentsMargins(0, 0, 0, 0)
        self.file_label = QLabel("No CSV file selected.")
        self.file_label.setStyleSheet("color: #8E8D88; font-size: 12.5px;")
        browse_btn = QPushButton("Browse CSV...")
        browse_btn.setIcon(_safe_icon("mdi.folder-open-outline", color="#F8FAFC"))
        browse_btn.clicked.connect(self._on_browse)
        file_row.addWidget(self.file_label, stretch=1)
        file_row.addWidget(browse_btn)
        layout.addWidget(file_frame)

        # Mapping Preview Table
        lbl_preview = QLabel("COLUMN MAPPING PREVIEW")
        lbl_preview.setProperty("class", "SectionLabel")
        layout.addWidget(lbl_preview)

        self.table = QTableWidget()
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setStyleSheet("""
            QTableWidget {
                background-color: #141414;
                border: 1px solid #262626;
                border-radius: 8px;
                color: #F8FAFC;
                gridline-color: #232323;
            }
            QHeaderView::section {
                background-color: #1A1A1A;
                color: #8E8D88;
                border: none;
                border-bottom: 1px solid #262626;
                padding: 6px 8px;
                font-weight: 600;
                font-size: 11.5px;
                text-transform: uppercase;
            }
        """)
        layout.addWidget(self.table, stretch=1)

        # Action Buttons Row
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        close_btn = QPushButton("Cancel")
        close_btn.setIcon(_safe_icon("mdi.close", color="#8E8D88"))
        close_btn.clicked.connect(self.reject)

        self.import_btn = QPushButton("Import Data")
        self.import_btn.setProperty("class", "primary")
        self.import_btn.setIcon(_safe_icon("mdi.upload", color="#FFFFFF"))
        self.import_btn.setEnabled(False)
        self.import_btn.clicked.connect(self._on_import)
        
        btn_row.addWidget(close_btn)
        btn_row.addWidget(self.import_btn)
        layout.addLayout(btn_row)

    def _on_browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select CSV File", "", "CSV Files (*.csv)")
        if not path:
            return
        self.file_label.setText(path)
        self.file_label.setStyleSheet("color: #4CF9B7; font-weight: 500; font-size: 12.5px;")
        self._load_preview(path)

    def _read_csv_rows(self, path):
        encodings_to_try = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]
        for enc in encodings_to_try:
            try:
                with open(path, newline='', encoding=enc) as f:
                    reader = csv.DictReader(f)
                    return list(reader), enc
            except UnicodeDecodeError:
                continue
        raise UnicodeDecodeError("unknown", b"", 0, 1, "could not decode file with any supported encoding")

    def _load_preview(self, path):
        try:
            self.csv_data, used_encoding = self._read_csv_rows(path)
        except Exception as e:
            QMessageBox.critical(self, "Error Reading CSV", str(e))
            return

        if used_encoding not in ("utf-8-sig", "utf-8"):
            self.file_label.setText(f"{path}  (read as {used_encoding})")
        else:
            self.file_label.setText(path)

        if not self.csv_data:
            QMessageBox.warning(self, "Empty File", "The CSV file appears to be empty.")
            return

        headers = list(self.csv_data[0].keys())
        mcl_labels = {c["label"].lower().strip(): c["label"] for c in self.db.get_mcl_columns()}
        mcl_labels["notes"] = "Notes (Base Field)"
        for alias in SeraDatabase.SERVICES_HEADER_ALIASES:
            mcl_labels[alias] = "Services (comma/semicolon-separated, matched by name)"

        identity_labels = {
            c["label"].lower().strip() for c in self.db.get_mcl_columns() if c.get("is_identity")
        }
        system_headers = getattr(SeraDatabase, "SYSTEM_HEADERS", {"client id", "created at", "updated at", "is archived"})

        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["CSV Header", "Maps to MCL Column"])
        self.table.setRowCount(len(headers))

        for i, header in enumerate(headers):
            clean_header = header.lower().strip() if header else ""
            self.table.setItem(i, 0, QTableWidgetItem(header or "[Empty]"))
            
            if clean_header in mcl_labels:
                label_text = mcl_labels[clean_header]
                if clean_header in identity_labels:
                    label_text += "  [Identity Match]"
                status = QTableWidgetItem(label_text)
                status.setForeground(Qt.green)
            elif clean_header.startswith("service:"):
                svc_name = header.split(":", 1)[1].strip()
                status = QTableWidgetItem(f"Service Attachment: {svc_name} (Yes/No)")
                status.setForeground(Qt.cyan)
            elif clean_header in system_headers:
                status = QTableWidgetItem("System Metadata (Ignored on Import)")
                status.setForeground(Qt.gray)
            else:
                status = QTableWidgetItem("Will be skipped (No Match)")
                status.setForeground(Qt.red)
            self.table.setItem(i, 1, status)
        
        self.import_btn.setEnabled(True)

    def _on_import(self):
        if not self.csv_data:
            return
        results = self.db.bulk_import_clients(self.csv_data)
        dlg = ImportResultDialog(results, self)
        dlg.exec()
        self.accept()
