from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QAbstractItemView,
    QInputDialog,
    QMessageBox,
    QGroupBox,
    QHeaderView,
    QFrame
)
import qtawesome as qta

class StaffAliasMatrixDialog(QDialog):
    """
    Admin-only dialog displaying the canonical Username ↔ Alias mapping matrix
    for all 6 staff slots in the system.
    """
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Staff User ↔ Alias Matrix (Admin Only)")
        self.resize(580, 420)
        self.setMinimumSize(500, 360)
        self._build_ui()
        self._load_matrix()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        # Header
        header_row = QHBoxLayout()
        header_row.setSpacing(10)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(qta.icon("fa5s.user-shield", color="#4CF9B7").pixmap(24, 24))
        header_row.addWidget(icon_lbl)

        title = QLabel("Staff User ↔ Alias Matrix")
        title.setProperty("class", "DialogTitle")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        header_row.addWidget(title)
        header_row.addStretch()
        layout.addLayout(header_row)

        desc = QLabel(
            "Below is the mapping between canonical internal usernames (hidden from employees) "
            "and their workstation Aliases displayed on employee devices."
        )
        desc.setWordWrap(True)
        desc.setProperty("class", "GuidanceText")
        layout.addWidget(desc)

        # Matrix Table
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["ID", "Canonical Username (Secret)", "Assigned Alias (Display)"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.SelectedClicked)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().setSectionsClickable(True)
        self.table.cellChanged.connect(self._on_cell_changed)
        layout.addWidget(self.table)


        # Action Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.btn_edit = QPushButton("Edit Alias")
        self.btn_edit.setIcon(qta.icon("mdi.pencil", color="#FFFFFF"))
        self.btn_edit.clicked.connect(self._edit_selected_alias)
        btn_row.addWidget(self.btn_edit)

        self.btn_clear = QPushButton("Clear Selected Alias")
        self.btn_clear.setIcon(qta.icon("mdi.eraser", color="#FFFFFF"))
        self.btn_clear.clicked.connect(self._clear_selected_alias)
        btn_row.addWidget(self.btn_clear)

        self.btn_reset_all = QPushButton("Reset All Aliases")
        self.btn_reset_all.setIcon(qta.icon("mdi.refresh", color="#FFFFFF"))
        self.btn_reset_all.clicked.connect(self._reset_all_aliases)
        btn_row.addWidget(self.btn_reset_all)

        btn_row.addStretch()

        self.btn_close = QPushButton("Close")
        self.btn_close.setProperty("class", "primary")
        self.btn_close.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_close)

        layout.addLayout(btn_row)

    def _load_matrix(self):
        self.table.setSortingEnabled(False)
        self.table.blockSignals(True)
        matrix = self.db.get_staff_matrix()
        self.table.setRowCount(len(matrix))

        for r_idx, row in enumerate(matrix):
            # ID
            id_item = QTableWidgetItem(str(row["id"]))
            id_item.setFlags(id_item.flags() & ~Qt.ItemIsEditable)
            id_item.setData(Qt.UserRole, row["id"])
            self.table.setItem(r_idx, 0, id_item)

            # Canonical Username (Read-only ID)
            name_item = QTableWidgetItem(row["name"])
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(r_idx, 1, name_item)

            # Editable Alias
            alias_item = QTableWidgetItem(row["alias"])
            self.table.setItem(r_idx, 2, alias_item)

        self.table.blockSignals(False)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().setSectionsClickable(True)


    def _on_cell_changed(self, row: int, col: int):
        if col != 2:
            return
        id_item = self.table.item(row, 0)
        alias_item = self.table.item(row, 2)
        if id_item and alias_item:
            user_id = id_item.data(Qt.UserRole)
            new_alias = alias_item.text().strip()
            self.db.update_staff_alias(user_id, new_alias)

    def _edit_selected_alias(self):
        selected = self.table.currentRow()
        if selected < 0:
            QMessageBox.information(self, "Selection Required", "Please select a staff row to edit its Alias.")
            return

        id_item = self.table.item(selected, 0)
        name_item = self.table.item(selected, 1)
        alias_item = self.table.item(selected, 2)
        
        user_id = id_item.data(Qt.UserRole)
        username = name_item.text()
        curr_alias = alias_item.text()

        new_alias, ok = QInputDialog.getText(
            self, "Edit Staff Alias",
            f"Enter display Alias for {username}:",
            text=curr_alias
        )
        if ok:
            self.db.update_staff_alias(user_id, new_alias.strip())
            self._load_matrix()

    def _clear_selected_alias(self):
        selected = self.table.currentRow()
        if selected < 0:
            return
        id_item = self.table.item(selected, 0)
        user_id = id_item.data(Qt.UserRole)
        self.db.update_staff_alias(user_id, "")
        self._load_matrix()

    def _reset_all_aliases(self):
        confirm = QMessageBox.question(
            self, "Reset Matrix",
            "Clear all workstation aliases across all 6 staff slots?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if confirm == QMessageBox.Yes:
            self.db.reset_staff_matrix()
            self._load_matrix()
