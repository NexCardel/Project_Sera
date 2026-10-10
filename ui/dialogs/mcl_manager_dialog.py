"""
mcl_manager_dialog.py
-----------------------------
Admin Mode -> "Manage Master Column List (MCL)".
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QMessageBox,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ui.components.kit import (
    ROLE_PILL, ROLE_TAG, Card, DarkListTable, DialogFrame, FieldLabel, IconButton, Input, LabelledAction,
    Select, SettingRow, Switch,
)
from ui.utils.tokens import TOKENS


FIELD_TYPE_CHOICES = [
    ("Text", "text"),
    ("Number", "number"),
    ("Alphanumeric", "alphanumeric"),
    ("Password / Secret", "password"),
    ("Date", "date"),
    ("Dropdown", "dropdown"),
    ("ID (Primary key / auto-serial)", "id"),
]


def mcl_role(col: dict):
    """(text, pill tone) of the Role cell, or None: the tags the old list showed after the type."""
    roles = []
    if col.get("field_type") == "id":       roles.append("ID/Serial")
    if col.get("is_internal_pk"):           roles.append("Internal PK")
    if col.get("is_identity"):              roles.append("Identity")
    if col.get("field_type") == "password": roles.append("Secret")
    if not roles:
        return None
    tone = "red" if "Secret" in roles else "amber" if "Internal PK" in roles else "emerald"
    return " · ".join(roles), tone


def _field(label: FieldLabel, widget: QWidget) -> QWidget:
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(TOKENS["s_1"])
    lay.addWidget(label)
    lay.addWidget(widget)
    return box


class ColumnEditDialog(QDialog):
    def __init__(self, parent=None, label="", field_type="text", dropdown_options=None, is_identity=False, is_internal_pk=False):
        super().__init__(parent)
        self.setObjectName("ToolDialog")
        self.setWindowTitle("Configure Column — Master Column Layout")
        self.setModal(True)
        self.setMinimumWidth(460)
        self.resize(520, 540)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = DialogFrame("Edit column" if label else "Add new column",
                            "Define the column's label, data type and identity flags.",
                            self, icon="mdi.view-column-outline", scroll=True)
        outer.addWidget(frame)
        frame.closeRequested.connect(self.reject)

        self.label_input = Input(placeholder="e.g. GSTIN, PAN No., Client Name, Password")
        self.label_input.setText(label)
        self.label_input.setAccessibleName("Column label")
        frame.add_widget(_field(FieldLabel("Column label", required=True, for_widget=self.label_input),
                                self.label_input))

        self.type_combo = Select()
        self.type_combo.setAccessibleName("Field type")
        for type_label, value in FIELD_TYPE_CHOICES:
            self.type_combo.addItem(type_label, value)
        idx = self.type_combo.findData(field_type)
        self.type_combo.setCurrentIndex(max(idx, 0))
        frame.add_widget(_field(FieldLabel("Field type", for_widget=self.type_combo), self.type_combo))

        self.options_input = Input(placeholder="e.g. Active, Inactive, Pending")
        self.options_input.setText(", ".join(dropdown_options or []))
        self.options_input.setAccessibleName("Dropdown options")
        self.options_label = FieldLabel("Dropdown options", for_widget=self.options_input)
        self.options_field = _field(self.options_label, self.options_input)
        frame.add_widget(self.options_field)

        self.identity_cb = Switch(checked=bool(is_identity), tooltip="Display identity column")
        self.internal_pk_cb = Switch(checked=bool(is_internal_pk), tooltip="Internal primary key anchor")
        flags = Card()
        flags.add_row(SettingRow("Display identity column",
                                 "Shows in search titles and header badges.", self.identity_cb, width="auto"))
        flags.add_row(SettingRow("Internal primary key anchor",
                                 "Mandatory and unique legal identity, e.g. PAN / TAN.",
                                 self.internal_pk_cb, width="auto"))
        frame.add_widget(flags)

        self.type_combo.currentIndexChanged.connect(self._update_options_visibility)
        self._update_options_visibility()

        cancel_btn = frame.add_action("Cancel")
        cancel_btn.clicked.connect(self.reject)
        save_btn = frame.add_action("Save column", variant="primary", icon="mdi.check")
        save_btn.clicked.connect(self._on_accept)
        save_btn.setDefault(True)

    def _update_options_visibility(self):
        self.options_field.setVisible(self.type_combo.currentData() == "dropdown")

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
            "is_identity": int(self.identity_cb.isChecked()),
            "is_internal_pk": int(self.internal_pk_cb.isChecked())
        }


class MCLManagerDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.setObjectName("ToolDialog")
        self.db = db
        self.setWindowTitle("Manage Master Column List (MCL) — Schema Editor")
        self.setModal(True)
        self.resize(640, 560)
        self.setMinimumSize(520, 440)
        self._build_ui()
        self._reload_columns()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = DialogFrame("Master Column List (MCL)",
                            "Custom columns, field types and display order across the application",
                            self, icon="mdi.view-column-outline")
        outer.addWidget(frame)
        frame.closeRequested.connect(self.accept)

        add_btn = LabelledAction("Add column", variant="primary", icon="mdi.plus",
                                 tooltip="Add a column to the Master Column List")
        self.edit_btn = LabelledAction("Edit", icon="mdi.pencil-outline", tooltip="Edit the selected column")
        self.up_btn = IconButton("mdi.arrow-up", "Move column up", variant="bordered")
        self.down_btn = IconButton("mdi.arrow-down", "Move column down", variant="bordered")
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(TOKENS["s_2"])
        row.addWidget(add_btn)
        row.addWidget(self.edit_btn)
        row.addStretch(1)
        row.addWidget(self.up_btn)
        row.addWidget(self.down_btn)
        frame.add_widget(bar)

        self.col_list = DarkListTable(0, 3)
        self.col_list.setHorizontalHeaderLabels(["Column", "Type", "Role"])
        self.col_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.col_list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.col_list.set_column(0, kind="strong", minimum=140)
        self.col_list.set_column(1, width=130)
        self.col_list.set_column(2, minimum=130, max_share=0.4, priority=1)
        table_card = Card()
        table_card.add_widget(self.col_list, 1)
        frame.add_widget(table_card, 1)

        self.del_btn = frame.add_action("Delete…", variant="danger", icon="mdi.delete-outline",
                                        tooltip="Asks first: all client values in this column are permanently removed")
        frame.add_footer_spacer()
        close_btn = frame.add_action("Close", icon="mdi.close")
        close_btn.clicked.connect(self.accept)

        add_btn.clicked.connect(self._on_add)
        self.edit_btn.clicked.connect(self._on_edit)
        self.del_btn.clicked.connect(self._on_delete)
        self.up_btn.clicked.connect(lambda: self._on_move(-1))
        self.down_btn.clicked.connect(lambda: self._on_move(1))
        self.col_list.currentCellChanged.connect(lambda row, *_: self._on_sel_changed(row))
        self.col_list.doubleClicked.connect(lambda *_: self._on_edit())

    def _reload_columns(self):
        table = self.col_list
        table.setRowCount(0)
        columns = self.db.get_mcl_columns()
        table.setRowCount(len(columns))
        for r, c in enumerate(columns):
            ftype = c.get("field_type", "text")
            name = QTableWidgetItem(c["label"])
            name.setData(Qt.UserRole, c["id"])
            table.setItem(r, 0, name)
            tag = QTableWidgetItem(ftype)
            tag.setData(ROLE_TAG, (ftype, "red" if ftype == "password" else "neutral"))
            table.setItem(r, 1, tag)
            role = mcl_role(c)
            cell = QTableWidgetItem(role[0] if role else "—")
            if role:
                cell.setData(ROLE_PILL, role)
            table.setItem(r, 2, cell)
        self._on_sel_changed(table.currentRow())

    def _on_sel_changed(self, row: int):
        table = self.col_list
        pick = None if (row >= 0 and table.item(row, 0) is not None) else "Select a column first"
        self.edit_btn.set_disabled_reason(pick)
        self.del_btn.set_disabled_reason(pick)
        self.up_btn.set_disabled_reason(pick or ("Already the first column" if row == 0 else None))
        self.down_btn.set_disabled_reason(
            pick or ("Already the last column" if row == table.rowCount() - 1 else None))

    def _selected_id(self):
        row = self.col_list.currentRow()
        item = self.col_list.item(row, 0) if row >= 0 else None
        return item.data(Qt.UserRole) if item else None

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
        dlg = ColumnEditDialog(self, current["label"], current.get("field_type", "text"), current.get("dropdown_options"), current.get("is_identity", 0), current.get("is_internal_pk", 0))
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
            self.col_list.setCurrentCell(new_idx, 0)
