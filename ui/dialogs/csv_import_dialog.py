"""
csv_import_dialog.py
-----------------------------
Handles importing clients directly from a CSV file.
"""

import csv

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from database import SeraDatabase
from ui.components.kit import (
    ROLE_PILL, Banner, Card, DarkListTable, DialogFrame, EmptyState, LabelledAction, SectionLabel, Tile,
)
from ui.utils.tokens import TOKENS


class ImportResultDialog(QDialog):
    def __init__(self, results, parent=None):
        super().__init__(parent)
        self.setObjectName("ToolDialog")
        self.setWindowTitle("Import Summary — Project Sera")
        self.resize(560, 420)
        self.setMinimumSize(480, 320)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = DialogFrame("CSV import complete",
                            "Client records have been processed and saved into the database.",
                            self, icon="mdi.check-decagram-outline")
        outer.addWidget(frame)
        frame.closeRequested.connect(self.accept)

        tiles = QWidget()
        row = QHBoxLayout(tiles)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(TOKENS["s_3"])
        for value, label, tone in (
            (results.get("imported", 0), "New clients created", "emerald"),
            (results.get("updated", 0), "Existing clients updated", "blue"),
        ):
            tile = Tile(str(value), label, tone=tone)
            tile.setCheckable(False)
            tile.setFocusPolicy(Qt.NoFocus)
            tile.setCursor(Qt.ArrowCursor)
            row.addWidget(tile, 1)
        frame.add_widget(tiles)

        if results.get("skipped_columns"):
            frame.add_widget(Banner(f"Skipped unmatched columns: {', '.join(results['skipped_columns'])}", "w"))

        if results.get("warnings"):
            frame.add_widget(SectionLabel("Warnings & notes"))
            warn_edit = QPlainTextEdit()
            warn_edit.setReadOnly(True)
            warn_edit.setPlainText("\n\n".join(results["warnings"]))
            frame.add_widget(warn_edit, 1)

        ok_btn = frame.add_action("Done", variant="primary", icon="mdi.check")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)


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
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = DialogFrame("Import clients from CSV",
                            "Upload spreadsheet data to populate or update your client roster.",
                            self, icon="mdi.file-delimited-outline")
        outer.addWidget(frame)
        frame.closeRequested.connect(self.reject)

        frame.add_widget(Banner(
            "Rows matching an existing client's Identity column (e.g. GSTIN / PAN / Name) <b>update</b> that record. "
            "Rows with no match create a <b>new</b> client. "
            "Attach services with a <i>'Services'</i> column or columns named <i>'Service: [Name]'</i> holding Yes/No.",
            "i"))

        self.file_label = QLabel("No CSV file selected.")
        self.file_label.setWordWrap(True)
        browse_btn = LabelledAction("Browse CSV…", icon="mdi.folder-open-outline",
                                    tooltip="Choose the CSV file to import")
        browse_btn.clicked.connect(self._on_browse)
        file_row = QWidget()
        file_lay = QHBoxLayout(file_row)
        file_lay.setContentsMargins(0, 0, 0, 0)
        file_lay.setSpacing(TOKENS["s_3"])
        file_lay.addWidget(self.file_label, 1)
        file_lay.addWidget(browse_btn)
        file_card = Card()
        file_card.add_widget(file_row)
        frame.add_widget(file_card)

        frame.add_widget(SectionLabel("Column mapping preview"))

        self.table = DarkListTable(0, 2)
        self.table.setHorizontalHeaderLabels(["CSV Header", "Maps to MCL Column"])
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.set_column(0, kind="strong", minimum=160, max_share=0.4)
        self.table.set_column(1, minimum=240)
        table_card = Card()
        table_card.add_widget(self.table, 1)
        self.table_card = table_card
        self.empty_state = EmptyState("No file chosen yet",
                                      "Browse for a CSV to see how each column will map.",
                                      icon="mdi.file-delimited-outline")
        frame.add_widget(self.empty_state, 1)
        frame.add_widget(table_card, 1)
        self.table_card.hide()

        close_btn = frame.add_action("Cancel", icon="mdi.close")
        close_btn.clicked.connect(self.reject)

        self.import_btn = frame.add_action("Import data", variant="primary", icon="mdi.upload")
        self.import_btn.set_disabled_reason("Choose a CSV file first")
        self.import_btn.clicked.connect(self._on_import)

    def _on_browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select CSV File", "", "CSV Files (*.csv)")
        if not path:
            return
        self.file_label.setText(path)
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

        self.table.setRowCount(len(headers))

        for i, header in enumerate(headers):
            clean_header = header.lower().strip() if header else ""
            self.table.setItem(i, 0, QTableWidgetItem(header or "[Empty]"))

            if clean_header in mcl_labels:
                label_text = mcl_labels[clean_header]
                if clean_header in identity_labels:
                    label_text += "  [Identity Match]"
                tone = "emerald"
            elif clean_header.startswith("service:"):
                svc_name = header.split(":", 1)[1].strip()
                label_text = f"Service Attachment: {svc_name} (Yes/No)"
                tone = "blue"
            elif clean_header in system_headers:
                label_text = "System Metadata (Ignored on Import)"
                tone = "neutral"
            else:
                label_text = "Will be skipped (No Match)"
                tone = "red"
            status = QTableWidgetItem(label_text)
            status.setData(ROLE_PILL, (label_text, tone))
            self.table.setItem(i, 1, status)

        self.empty_state.hide()
        self.table_card.show()
        self.import_btn.set_disabled_reason(None)

    def _on_import(self):
        if not self.csv_data:
            return
        results = self.db.bulk_import_clients(self.csv_data)
        dlg = ImportResultDialog(results, self)
        dlg.exec()
        self.accept()
