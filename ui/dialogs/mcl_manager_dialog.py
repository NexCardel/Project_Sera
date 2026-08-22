"""
mcl_manager_dialog.py
-----------------------------
Admin Mode -> "Manage Master Column List (MCL)".
"""

from PySide6.QtCore import Qt, QSize
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
    QFrame,
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


FIELD_TYPE_CHOICES = [
    ("Text", "text"),
    ("Number", "number"),
    ("Alphanumeric", "alphanumeric"),
    ("Password / Secret", "password"),
    ("Dropdown", "dropdown"),
    ("Date", "date"),
    ("ID (Primary Key / Auto-Serial)", "id"),
]


class ColumnEditDialog(QDialog):
    def __init__(self, parent=None, label="", field_type="text", dropdown_options=None, is_identity=False):
        super().__init__(parent)
        self.setObjectName("ToolDialog")
        self.setWindowTitle("Configure Column — Master Column Layout")
        self.setModal(True)
        self.setMinimumWidth(440)
        self.resize(460, 360)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(14)

        # Header Frame
        header = QHBoxLayout()
        header.setSpacing(10)
        icon_lbl = QLabel()
        icon_lbl.setPixmap(_safe_icon("mdi.view-column-outline", color="#2E9B5F").pixmap(24, 24))
        header.addWidget(icon_lbl)

        title_vbox = QVBoxLayout()
        title_vbox.setSpacing(2)
        title_lbl = QLabel("Edit Column Schema" if label else "Add New Column")
        title_lbl.setStyleSheet("font-size: 16px; font-weight: 700; color: #F8FAFC;")
        sub_lbl = QLabel("Define attribute properties, data types, and identity flags.")
        sub_lbl.setStyleSheet("font-size: 11.5px; color: #8E8D88;")
        title_vbox.addWidget(title_lbl)
        title_vbox.addWidget(sub_lbl)
        header.addLayout(title_vbox)
        header.addStretch()
        main_layout.addLayout(header)

        # Divider
        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet("border: none; border-top: 1px solid #262626; margin: 4px 0;")
        main_layout.addWidget(divider)

        # Form Container
        form_frame = QFrame()
        form_frame.setStyleSheet("""
            QFrame {
                background-color: #141414;
                border: 1px solid #262626;
                border-radius: 8px;
                padding: 12px;
            }
        """)
        form = QFormLayout(form_frame)
        form.setSpacing(10)
        form.setContentsMargins(8, 8, 8, 8)

        self.label_input = QLineEdit(label)
        self.label_input.setPlaceholderText("e.g. GSTIN, PAN No., Client Name, Password")
        form.addRow("Column Label:", self.label_input)

        self.type_combo = QComboBox()
        for type_label, value in FIELD_TYPE_CHOICES:
            self.type_combo.addItem(type_label, value)
        idx = self.type_combo.findData(field_type)
        self.type_combo.setCurrentIndex(max(idx, 0))
        form.addRow("Field Type:", self.type_combo)

        self.options_label = QLabel("Dropdown Options:")
        self.options_input = QLineEdit(", ".join(dropdown_options or []))
        self.options_input.setPlaceholderText("e.g. Active, Inactive, Pending")
        form.addRow(self.options_label, self.options_input)

        self.identity_cb = QCheckBox("Identity column (shows in search titles & primary badges)")
        self.identity_cb.setChecked(bool(is_identity))
        form.addRow("", self.identity_cb)

        main_layout.addWidget(form_frame)
        self.type_combo.currentIndexChanged.connect(self._update_options_visibility)
        self._update_options_visibility()

        # Action Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setIcon(_safe_icon("mdi.close", color="#8E8D88"))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        save_btn = QPushButton("Save Column")
        save_btn.setProperty("class", "primary")
        save_btn.setIcon(_safe_icon("mdi.check", color="#FFFFFF"))
        save_btn.clicked.connect(self._on_accept)
        btn_row.addWidget(save_btn)

        main_layout.addLayout(btn_row)

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
        self.setWindowTitle("Manage Master Column List (MCL) — Schema Editor")
        self.setModal(True)
        self.resize(540, 560)
        self.setMinimumSize(480, 480)
        self._build_ui()
        self._reload_columns()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # Header Frame
        header = QHBoxLayout()
        header.setSpacing(10)
        icon_lbl = QLabel()
        icon_lbl.setPixmap(_safe_icon("mdi.view-column-outline", color="#2E9B5F").pixmap(26, 26))
        header.addWidget(icon_lbl)

        title_vbox = QVBoxLayout()
        title_vbox.setSpacing(2)
        title_lbl = QLabel("Master Column List (MCL)")
        title_lbl.setStyleSheet("font-size: 17px; font-weight: 700; color: #F8FAFC;")
        sub_lbl = QLabel("Configure custom columns, field types, and display order across the application.")
        sub_lbl.setStyleSheet("font-size: 12px; color: #8E8D88;")
        title_vbox.addWidget(title_lbl)
        title_vbox.addWidget(sub_lbl)
        header.addLayout(title_vbox)
        header.addStretch()
        layout.addLayout(header)

        # Divider
        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet("border: none; border-top: 1px solid #262626; margin: 4px 0;")
        layout.addWidget(divider)

        # List Widget
        self.col_list = QListWidget()
        self.col_list.setStyleSheet("""
            QListWidget {
                background-color: #141414;
                border: 1px solid #262626;
                border-radius: 8px;
                padding: 6px;
                color: #F8FAFC;
                font-size: 13px;
            }
            QListWidget::item {
                padding: 9px 12px;
                border-radius: 6px;
                margin-bottom: 2px;
            }
            QListWidget::item:hover {
                background-color: #1F2933;
            }
            QListWidget::item:selected {
                background-color: #1E3A2F;
                color: #4CF9B7;
                border: 1px solid #2E9B5F;
            }
        """)
        layout.addWidget(self.col_list, stretch=1)

        # Action Buttons Row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        add_btn = QPushButton("Add Column")
        add_btn.setProperty("class", "primary")
        add_btn.setIcon(_safe_icon("mdi.plus", color="#FFFFFF"))

        edit_btn = QPushButton("Edit")
        edit_btn.setIcon(_safe_icon("mdi.pencil-outline", color="#F8FAFC"))

        del_btn = QPushButton("Delete")
        del_btn.setProperty("class", "danger")
        del_btn.setIcon(_safe_icon("mdi.delete-outline", color="#FF5252"))

        up_btn = QPushButton()
        up_btn.setIcon(_safe_icon("mdi.arrow-up", color="#F8FAFC"))
        up_btn.setToolTip("Move Column Up")
        up_btn.setFixedWidth(36)

        down_btn = QPushButton()
        down_btn.setIcon(_safe_icon("mdi.arrow-down", color="#F8FAFC"))
        down_btn.setToolTip("Move Column Down")
        down_btn.setFixedWidth(36)

        add_btn.clicked.connect(self._on_add)
        edit_btn.clicked.connect(self._on_edit)
        del_btn.clicked.connect(self._on_delete)
        up_btn.clicked.connect(lambda: self._on_move(-1))
        down_btn.clicked.connect(lambda: self._on_move(1))

        btn_row.addWidget(add_btn)
        btn_row.addWidget(edit_btn)
        btn_row.addWidget(del_btn)
        btn_row.addWidget(up_btn)
        btn_row.addWidget(down_btn)
        btn_row.addStretch()

        close_btn = QPushButton("Close")
        close_btn.setIcon(_safe_icon("mdi.close", color="#8E8D88"))
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

    def _reload_columns(self):
        self.col_list.clear()
        for c in self.db.get_mcl_columns():
            tags = []
            if c.get("field_type") == "id":
                tags.append("ID / Auto-Serial")
            if c.get("is_identity"):
                tags.append("Identity")
            if c.get("field_type") == "password":
                tags.append("Secret")
            elif c.get("field_type") == "dropdown":
                tags.append("Dropdown")

            tag_str = f"  [{', '.join(tags)}]" if tags else ""
            item = QListWidgetItem(f"{c['label']} ({c.get('field_type', 'text')}){tag_str}")
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
        if not col_id:
            return
        current = next((c for c in self.db.get_mcl_columns() if c["id"] == col_id), None)
        if not current:
            return
        dlg = ColumnEditDialog(self, current["label"], current.get("field_type", "text"), current.get("dropdown_options"), current.get("is_identity", 0))
        if dlg.exec() == QDialog.Accepted:
            self.db.update_mcl_column(col_id, **dlg.result_data())
            self._reload_columns()

    def _on_delete(self):
        col_id = self._selected_id()
        if not col_id:
            return
        if QMessageBox.question(self, "Confirm Deletion", "Delete this column? All client values associated with this column will be permanently removed.") == QMessageBox.Yes:
            self.db.delete_mcl_column(col_id)
            self._reload_columns()

    def _on_move(self, direction: int):
        col_id = self._selected_id()
        if not col_id:
            return
        ids = [c["id"] for c in self.db.get_mcl_columns()]
        idx = ids.index(col_id)
        new_idx = idx + direction
        if 0 <= new_idx < len(ids):
            ids[idx], ids[new_idx] = ids[new_idx], ids[idx]
            self.db.reorder_mcl_columns(ids)
            self._reload_columns()
            self.col_list.setCurrentRow(new_idx)
