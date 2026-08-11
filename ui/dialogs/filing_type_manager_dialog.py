"""
filing_type_manager_dialog.py
-----------------------------
Dialog for managing Filing Types (DRS compliance obligations attached to Services).
"""

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


class FilingTypeManagerDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.setObjectName("ToolDialog")
        self.db = db
        self.setWindowTitle("Manage Filing Types (DRS)")
        self.setModal(True)
        self.resize(800, 500)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel("Filter by Service:"))
        self.svc_combo = QComboBox()
        self.svc_combo.addItem("All Services", userData=None)
        for s in self.db.get_services():
            self.svc_combo.addItem(s["name"], userData=s["id"])
        self.svc_combo.currentIndexChanged.connect(self.refresh)
        top_bar.addWidget(self.svc_combo)
        top_bar.addStretch()

        layout.addLayout(top_bar)

        self.table = QTableWidget()
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.table)

        btn_bar = QHBoxLayout()
        btn_bar.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_bar.addWidget(close_btn)
        layout.addLayout(btn_bar)

        self.refresh()

    def refresh(self):
        svc_id = self.svc_combo.currentData()
        filing_types = self.db.get_filing_types(service_id=svc_id)

        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["Service", "Code", "Name", "Frequency", "Due Date Rule", "Variants"])
        self.table.setRowCount(len(filing_types))

        for i, ft in enumerate(filing_types):
            self.table.setItem(i, 0, QTableWidgetItem(ft["service_name"]))
            self.table.setItem(i, 1, QTableWidgetItem(ft["code"]))
            self.table.setItem(i, 2, QTableWidgetItem(ft["name"]))
            self.table.setItem(i, 3, QTableWidgetItem(ft["frequency"].title()))

            due = f"Day {ft['due_day']}" if ft['due_day'] else (ft['due_day_absolute'] or "-")
            self.table.setItem(i, 4, QTableWidgetItem(due))

            v_tags = [v.get("tag", "") for v in ft.get("variants", []) if v.get("tag")]
            v_str = ", ".join(v_tags) if v_tags else "None"
            self.table.setItem(i, 5, QTableWidgetItem(v_str))
