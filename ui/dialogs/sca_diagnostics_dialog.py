from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, 
                               QLabel, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView)
from PySide6.QtCore import Qt, QTimer
import datetime

class ScaDiagnosticsDialog(QDialog):
    def __init__(self, listener, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SCA Diagnostics View")
        self.resize(700, 500)
        
        self.listener = listener
        self._setup_ui()
        
        if self.listener:
            self.listener.sca_state_received.connect(self.on_sca_state)
            self.listener.sca_error_received.connect(self.on_sca_error)
            self.listener.sca_fill_result_received.connect(self.on_sca_fill_result)
            self.listener.filing_result_received.connect(self.on_audit_event)

        # Trigger a state request initially
        self._request_state()
        
        # Periodic refresh just in case
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._request_state)
        self.refresh_timer.start(5000)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        
        # State Section
        self.status_label = QLabel("Connection: Native Host Listening...")
        self.status_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(self.status_label)
        
        self.arm_state_label = QLabel("Current Arm State: IDLE")
        layout.addWidget(self.arm_state_label)
        
        self.client_label = QLabel("Client ID: N/A")
        layout.addWidget(self.client_label)
        
        self.expiry_label = QLabel("Expiry: N/A")
        layout.addWidget(self.expiry_label)
        
        # Table for logs
        self.log_table = QTableWidget(0, 4)
        self.log_table.setHorizontalHeaderLabels(["Time", "Type", "Status", "Detail"])
        self.log_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        layout.addWidget(self.log_table)
        
        btn_layout = QHBoxLayout()
        refresh_btn = QPushButton("Refresh State")
        refresh_btn.clicked.connect(self._request_state)
        clear_btn = QPushButton("Clear Logs")
        clear_btn.clicked.connect(lambda: self.log_table.setRowCount(0))
        btn_layout.addWidget(refresh_btn)
        btn_layout.addWidget(clear_btn)
        layout.addLayout(btn_layout)

    def _request_state(self):
        # We can ping the extension or ask automation for current arm
        import automation
        arm = automation._current_arm
        if arm:
            self.arm_state_label.setText(f"Current Arm State: {arm.get('state', 'ARMED (Local)')}")
            self.client_label.setText(f"Client ID: {arm.get('client_id_token') or arm.get('client_id')}")
            exp = arm.get('expires_at', 0)
            if exp:
                dt = datetime.datetime.fromtimestamp(exp/1000)
                self.expiry_label.setText(f"Expiry: {dt.strftime('%H:%M:%S')}")
        else:
            self.arm_state_label.setText("Current Arm State: IDLE (No local arm)")
            self.client_label.setText("Client ID: N/A")
            self.expiry_label.setText("Expiry: N/A")

    def _add_log(self, mtype, status, detail):
        row = self.log_table.rowCount()
        self.log_table.insertRow(row)
        
        now = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_table.setItem(row, 0, QTableWidgetItem(now))
        self.log_table.setItem(row, 1, QTableWidgetItem(mtype))
        self.log_table.setItem(row, 2, QTableWidgetItem(status))
        self.log_table.setItem(row, 3, QTableWidgetItem(detail))
        self.log_table.scrollToBottom()

    def on_sca_state(self, msg):
        arm = msg.get('arm', {})
        state = arm.get('state', 'UNKNOWN')
        self.arm_state_label.setText(f"Current Arm State: {state}")
        self.client_label.setText(f"Client ID: {arm.get('client_id_token') or arm.get('client_id')}")
        self._add_log("SCA_STATE", state, f"State transitioned to {state}")

    def on_sca_error(self, msg):
        detail = msg.get('detail', 'Unknown error')
        self._add_log("SCA_ERROR", "FAILED", detail)
        
    def on_sca_fill_result(self, msg):
        res = msg.get('result', 'success')
        detail = msg.get('detail', '')
        self._add_log("SCA_FILL_RESULT", res.upper(), detail)
        
    def on_audit_event(self, msg):
        if msg.get('type') == 'audit_event' and 'SCA' in msg.get('action', ''):
            self._add_log("AUDIT", "INFO", msg.get('detail', ''))
