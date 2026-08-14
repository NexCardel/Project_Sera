import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)


class FilingConfirmationDialog(QDialog):
    def __init__(self, db, client_id: int, portal: str, result_type: str, arn: str | None = None, parent=None):
        super().__init__(parent)
        self.db = db
        self.client_id = client_id
        self.result_type = result_type  # 'filing_result' or 'uncertain_result'
        self.arn = arn
        self.selected_period = ""
        self._filing_types_map = {}
        
        self.setWindowTitle("Filing Confirmation")
        self.resize(450, 250)
        
        # Ensure dialog blocks and stays on top
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setWindowModality(Qt.ApplicationModal)
        
        client = self.db.get_client(client_id)
        mcl = self.db.get_mcl_columns()
        ident_col = next((c for c in mcl if c["is_identity"]), None)
        client_name = client["values"].get(ident_col["id"], f"Client #{client_id}") if client and ident_col else f"Client #{client_id}"
        
        layout = QVBoxLayout(self)
        
        if result_type == 'filing_result':
            lbl = QLabel(f"<b>✅ Successful filing detected for {client_name}!</b>")
            layout.addWidget(lbl)
            if arn and arn != "N/A":
                layout.addWidget(QLabel(f"Captured ARN/Transaction ID: <b>{arn}</b>"))
            layout.addWidget(QLabel("Which return was this for?"))
        else:
            lbl = QLabel(f"<b>⚠️ Session ended for {client_name}.</b>")
            layout.addWidget(lbl)
            layout.addWidget(QLabel("Did you successfully submit a return? If yes, please select it below:"))
            
        self.combo = QComboBox()
        self.combo.currentIndexChanged.connect(self._on_type_changed)
        
        # Period selection area
        self.period_layout = QHBoxLayout()
        self.period_buttons = []
        
        layout.addWidget(self.combo)
        layout.addWidget(QLabel("Select Period:"))
        layout.addLayout(self.period_layout)
        
        # Populate combobox which will trigger _on_type_changed
        self._populate_pending_filings()
        
        btn_layout = QHBoxLayout()
        
        if result_type == 'filing_result':
            self.btn_save = QPushButton("Save Record")
            self.btn_save.clicked.connect(self.accept)
            btn_layout.addWidget(self.btn_save)
        else:
            self.btn_yes = QPushButton("Yes, Save Record")
            self.btn_yes.clicked.connect(self.accept)
            btn_no = QPushButton("No, didn't file")
            btn_no.clicked.connect(self.reject)
            btn_layout.addWidget(self.btn_yes)
            btn_layout.addWidget(btn_no)
            
        layout.addLayout(btn_layout)
        
        # Ensure we steal focus
        self.raise_()
        self.activateWindow()
        
    def _populate_pending_filings(self):
        fts = self.db.get_client_filing_types(self.client_id)
        if not fts:
            self.combo.addItem("No filing types assigned to client", None)
            return
            
        for ft in fts:
            if ft.get('is_enabled', True):
                self._filing_types_map[ft['id']] = ft
                self.combo.addItem(f"{ft['name']} ({ft['frequency']})", ft['id'])
                
    def _on_type_changed(self):
        ft_id = self.combo.currentData()
        if not ft_id or ft_id not in self._filing_types_map:
            self._update_period_buttons([])
            return
            
        ft = self._filing_types_map[ft_id]
        freq = ft.get('frequency', 'monthly').lower()
        periods = self._generate_periods(freq)
        self._update_period_buttons(periods)
        
    def _generate_periods(self, freq: str) -> list[str]:
        now = datetime.datetime.now(datetime.timezone.utc)
        periods = []
        
        if freq == 'monthly':
            for i in range(1, 5): # Last 4 months
                m = now.month - i
                y = now.year
                while m < 1:
                    m += 12
                    y -= 1
                periods.append(datetime.date(y, m, 1).strftime("%b %Y"))
        elif freq == 'quarterly':
            # Simplified generic quarters
            y = now.year
            periods = [f"Q1 {y}", f"Q2 {y}", f"Q3 {y}", f"Q4 {y}", f"Q4 {y-1}"]
            # Only keep latest 4
            periods = periods[:4]
        elif freq == 'annual':
            y = now.year
            periods = [f"FY {y-2}-{str(y-1)[-2:]}", f"FY {y-1}-{str(y)[-2:]}", f"FY {y}-{str(y+1)[-2:]}"]
        else:
            periods = ["Current Period", "Previous Period"]
            
        return periods
        
    def _update_period_buttons(self, periods: list[str]):
        # Clear existing
        for btn in self.period_buttons:
            self.period_layout.removeWidget(btn)
            btn.setParent(None)
            btn.deleteLater()
        self.period_buttons.clear()
        self.selected_period = ""
        
        for p in periods:
            btn = QPushButton(p)
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, b=btn, txt=p: self._on_period_selected(b, txt))
            self.period_layout.addWidget(btn)
            self.period_buttons.append(btn)
            
        if self.period_buttons:
            # Select first by default
            self.period_buttons[0].setChecked(True)
            self.selected_period = periods[0]
            
    def _on_period_selected(self, clicked_btn: QPushButton, period: str):
        for btn in self.period_buttons:
            if btn != clicked_btn:
                btn.setChecked(False)
            else:
                btn.setChecked(True)
        self.selected_period = period

    def get_selected_filing_type_id(self):
        return self.combo.currentData()
        
    def get_period_label(self):
        return self.selected_period
