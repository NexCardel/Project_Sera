"""
csv_import_dialog.py
-----------------------------
Handles importing clients directly from a CSV file.
"""

import csv

from PySide6.QtCore import Qt
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
    QTextEdit,
    QVBoxLayout,
)

from database import SeraDatabase


class ImportResultDialog(QDialog):
    def __init__(self, results, parent=None):
        super().__init__(parent)
        self.setObjectName("ToolDialog")
        self.setWindowTitle("Import Complete")
        self.resize(600, 400)
        self.setMinimumSize(500, 300)

        layout = QVBoxLayout(self)

        summary_text = (
            f"<b>Created:</b> {results['imported']} new client(s)<br>"
            f"<b>Updated:</b> {results['updated']} existing client(s)"
        )
        if results.get("skipped_columns"):
            summary_text += f"<br><br><font color='#777'><b>Skipped columns:</b> {', '.join(results['skipped_columns'])}</font>"

        summary_lbl = QLabel(summary_text)
        summary_lbl.setWordWrap(True)
        layout.addWidget(summary_lbl)

        if results.get("warnings"):
            layout.addWidget(QLabel("<b>Warnings:</b>"))
            warn_edit = QTextEdit()
            warn_edit.setReadOnly(True)
            warn_edit.setPlainText("\n\n".join(results["warnings"]))
            layout.addWidget(warn_edit, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("OK")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

class CSVImportDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.setObjectName("ToolDialog")
        self.db = db
        self.setWindowTitle("Import Clients from CSV")
        self.setModal(True)
        self.resize(700, 500)
        self.csv_data = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        info = QLabel(
            "Rows whose identity column value matches an existing, non-archived "
            "client will UPDATE that client (only the columns present in this "
            "file are touched -- nothing else is erased). Rows with no match "
            "create a new client. Add a \"Services\" column or \"Service: <Name>\" "
            "columns (with Yes/No values) to auto-attach services."
        )
        info.setWordWrap(True)
        info.setProperty("class", "InfoText")
        layout.addWidget(info)
        
        file_row = QHBoxLayout()
        self.file_label = QLabel("No file selected.")
        browse_btn = QPushButton("Browse CSV...")
        browse_btn.clicked.connect(self._on_browse)
        file_row.addWidget(self.file_label, stretch=1)
        file_row.addWidget(browse_btn)
        layout.addLayout(file_row)

        self.table = QTableWidget()
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(QLabel("Column Mapping Preview:"))
        layout.addWidget(self.table)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.import_btn = QPushButton("Import Data")
        self.import_btn.setProperty("class", "primary")
        self.import_btn.setEnabled(False)
        self.import_btn.clicked.connect(self._on_import)
        close_btn = QPushButton("Cancel")
        close_btn.clicked.connect(self.reject)
        
        btn_row.addWidget(self.import_btn)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _on_browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select CSV File", "", "CSV Files (*.csv)")
        if not path: return
        self.file_label.setText(path)
        self._load_preview(path)

    def _read_csv_rows(self, path):
        """Excel on Windows saves 'CSV' as cp1252/ANSI by default, not UTF-8 --
        that's exactly what throws 'invalid continuation byte' errors, usually
        from a pasted accented character, curly quote, or currency symbol.
        Try encodings in order of likelihood; latin-1 always succeeds (it maps
        every byte value 0-255), so this only fails on a genuine I/O problem."""
        encodings_to_try = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]
        for enc in encodings_to_try:
            try:
                with open(path, newline='', encoding=enc) as f:
                    reader = csv.DictReader(f)
                    return list(reader), enc
            except UnicodeDecodeError:
                continue
        # Unreachable in practice since latin-1 never raises UnicodeDecodeError,
        # but keeps the method's contract honest if that ever changes.
        raise UnicodeDecodeError("unknown", b"", 0, 1, "could not decode file with any supported encoding")

    def _load_preview(self, path):
        try:
            self.csv_data, used_encoding = self._read_csv_rows(path)
        except Exception as e:
            QMessageBox.critical(self, "Error Reading CSV", str(e))
            return

        if used_encoding not in ("utf-8-sig", "utf-8"):
            self.file_label.setText(f"{path}  (read as {used_encoding} -- not UTF-8, double-check accented text below)")
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
            c["label"].lower().strip() for c in self.db.get_mcl_columns() if c["is_identity"]
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
                    label_text += "  [Identity -- used to match existing clients]"
                status = QTableWidgetItem(label_text)
                status.setForeground(Qt.darkGreen)
            elif clean_header.startswith("service:"):
                svc_name = header.split(":", 1)[1].strip()
                status = QTableWidgetItem(f"Service Attachment Column: {svc_name} (Yes/No)")
                status.setForeground(Qt.darkGreen)
            elif clean_header in system_headers:
                status = QTableWidgetItem("System Metadata (Ignored on Import)")
                status.setForeground(Qt.darkGray)
            else:
                status = QTableWidgetItem("Will be skipped (No Match)")
                status.setForeground(Qt.red)
            self.table.setItem(i, 1, status)
        
        self.import_btn.setEnabled(True)

    def _on_import(self):
        if not self.csv_data: return
        results = self.db.bulk_import_clients(self.csv_data)
        dlg = ImportResultDialog(results, self)
        dlg.exec()
        self.accept()
