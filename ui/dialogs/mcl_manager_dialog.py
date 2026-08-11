"""
mcl_manager_dialog.py
-----------------------------
Admin Mode -> "Manage Master Column List".
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

FIELD_TYPE_CHOICES = [
    ("Text", "text"),
    ("Number", "number"),
    ("Alphanumeric", "alphanumeric"),
    ("Password / Secret", "password"),
    ("Dropdown", "dropdown"),
    ("Date", "date"),
]

class ColumnEditDialog(QDialog):
    def __init__(self, parent=None, label="", field_type="text", dropdown_options=None, is_identity=False):
        super().__init__(parent)
        self.setObjectName("ToolDialog")
        self.setWindowTitle("MCL Column")
        self.setModal(True)
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.label_input = QLineEdit(label)
        self.label_input.setPlaceholderText("e.g. GSTIN, PAN No., Password")
        form.addRow("Column Label:", self.label_input)

        self.type_combo = QComboBox()
        for type_label, value in FIELD_TYPE_CHOICES:
            self.type_combo.addItem(type_label, value)
        idx = self.type_combo.findData(field_type)
        self.type_combo.setCurrentIndex(max(idx, 0))
        form.addRow("Field Type:", self.type_combo)

        self.options_label = QLabel("Dropdown Options:")
        self.options_input = QLineEdit(", ".join(dropdown_options or []))
        self.options_input.setPlaceholderText("e.g. Active, Inactive")
        form.addRow(self.options_label, self.options_input)

        self.identity_cb = QCheckBox("Identity column (shows in search & titles)")
        self.identity_cb.setChecked(bool(is_identity))
        form.addRow("", self.identity_cb)

        layout.addLayout(form)
        self.type_combo.currentIndexChanged.connect(self._update_options_visibility)
        self._update_options_visibility()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update_options_visibility(self):
        is_dropdown = self.type_combo.currentData() == "dropdown"
        self.options_label.setVisible(is_dropdown)
        self.options_input.setVisible(is_dropdown)

    def _on_accept(self):
        if not self.label_input.text().strip():
            QMessageBox.warning(self, "Missing Label", "Column label is required.")
            return
        if self.type_combo.currentData() == "dropdown" and not self.options_input.text().strip():
            QMessageBox.warning(self, "Missing Options", "Enter at least one option, comma-separated.")
            return
        self.accept()

    def result_data(self) -> dict:
        options = None
        if self.type_combo.currentData() == "dropdown":
            options = [o.strip() for o in self.options_input.text().split(",") if o.strip()]
        return {
            "label": self.label_input.text().strip(),
            "field_type": self.type_combo.currentData(),
            "dropdown_options": options,
            "is_identity": int(self.identity_cb.isChecked())
        }

class MCLManagerDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.setObjectName("ToolDialog")
        self.db = db
        self.setWindowTitle("Manage Master Column List (MCL)")
        self.setModal(True)
        self.resize(450, 500)
        self._build_ui()
        self._reload_columns()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Master Column List (Schema):"))
        self.col_list = QListWidget()
        layout.addWidget(self.col_list)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add Column")
        edit_btn = QPushButton("Edit")
        del_btn = QPushButton("Delete")
        up_btn = QPushButton("\u2191")
        down_btn = QPushButton("\u2193")
        up_btn.setFixedWidth(32)
        down_btn.setFixedWidth(32)

        add_btn.clicked.connect(self._on_add)
        edit_btn.clicked.connect(self._on_edit)
        del_btn.clicked.connect(self._on_delete)
        up_btn.clicked.connect(lambda: self._on_move(-1))
        down_btn.clicked.connect(lambda: self._on_move(1))

        for btn in (add_btn, edit_btn, del_btn, up_btn, down_btn):
            btn_row.addWidget(btn)
        layout.addLayout(btn_row)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignRight)

    def _reload_columns(self):
        self.col_list.clear()
        for c in self.db.get_mcl_columns():
            id_tag = " [Identity]" if c["is_identity"] else ""
            item = QListWidgetItem(f"{c['label']} ({c['field_type']}){id_tag}")
            item.setData(Qt.UserRole, c["id"])
            self.col_list.addItem(item)

    def _selected_id(self):
        items = self.col_list.selectedItems()
        return items[0].data(Qt.UserRole) if items else None

    def _on_add(self):
        dlg = ColumnEditDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self.db.create_mcl_column(**dlg.result_data())
            self._reload_columns()

    def _on_edit(self):
        col_id = self._selected_id()
        if not col_id: return
        current = next((c for c in self.db.get_mcl_columns() if c["id"] == col_id), None)
        if not current: return
        dlg = ColumnEditDialog(self, current["label"], current["field_type"], current["dropdown_options"], current["is_identity"])
        if dlg.exec() == QDialog.Accepted:
            self.db.update_mcl_column(col_id, **dlg.result_data())
            self._reload_columns()

    def _on_delete(self):
        col_id = self._selected_id()
        if not col_id: return
        if QMessageBox.question(self, "Confirm", "Delete this column? All client values for this column will be lost.") == QMessageBox.Yes:
            self.db.delete_mcl_column(col_id)
            self._reload_columns()

    def _on_move(self, direction: int):
        col_id = self._selected_id()
        if not col_id: return
        ids = [c["id"] for c in self.db.get_mcl_columns()]
        idx = ids.index(col_id)
        new_idx = idx + direction
        if 0 <= new_idx < len(ids):
            ids[idx], ids[new_idx] = ids[new_idx], ids[idx]
            self.db.reorder_mcl_columns(ids)
            self._reload_columns()
            self.col_list.setCurrentRow(new_idx)
