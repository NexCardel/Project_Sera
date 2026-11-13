from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QTableWidgetItem
from PySide6.QtCore import QTimer
import datetime

from ui.components.kit import Banner, Card, DarkListTable, DialogFrame


class ScaDiagnosticsDialog(QDialog):
    def __init__(self, listener, parent=None, watcher=None):
        super().__init__(parent)
        self.setWindowTitle("SCA Diagnostics View")
        self.resize(700, 500)

        self.listener = listener
        self.watcher = watcher          # clipboard_watch.ClipboardWatchService - owns the arm
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
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = DialogFrame("SCA diagnostics", "This PC's view of the SCA arm and its last events",
                            self, icon="mdi.shield-check-outline", full=True)
        outer.addWidget(frame)
        frame.closeRequested.connect(self.reject)

        self.status_label = Banner("Connection: Native Host Listening...", "i")
        frame.add_widget(self.status_label)

        arm = Card("Arm")
        self.arm_state_label = QLabel("Current Arm State: IDLE")
        self.arm_state_label.setObjectName("KitFieldVal")
        arm.add_widget(self.arm_state_label)
        self.client_label = QLabel("Client ID: N/A")
        self.client_label.setObjectName("KitFieldVal")
        arm.add_widget(self.client_label)
        self.expiry_label = QLabel("Expiry: N/A")
        self.expiry_label.setObjectName("KitFieldVal")
        arm.add_widget(self.expiry_label)
        frame.add_widget(arm)

        self.log_table = DarkListTable(0, 4)
        self.log_table.setHorizontalHeaderLabels(["Time", "Type", "Status", "Detail"])
        self.log_table.set_column(0, kind="mono", width=90)
        self.log_table.set_column(1, kind="strong", width=130)
        self.log_table.set_column(2, kind="text", width=110)
        self.log_table.set_column(3, kind="text", stretch=3)
        frame.add_widget(self.log_table, 1)

        refresh_btn = frame.add_action("Refresh State", icon="mdi.refresh")
        refresh_btn.clicked.connect(self._request_state)
        clear_btn = frame.add_action("Clear Logs", variant="ghost", icon="mdi.broom")
        clear_btn.clicked.connect(lambda: self.log_table.setRowCount(0))
        frame.add_footer_spacer()
        close_btn = frame.add_action("Close", variant="secondary")
        close_btn.clicked.connect(self.reject)

    def _request_state(self):
        """The desktop's own view of the arm (it decides every password request)."""
        arm = getattr(self.watcher, "_arm", None) if self.watcher else None
        if arm:
            left = int(arm.expires_at - datetime.datetime.now().timestamp())
            state = "ARMED" if left > 0 else "EXPIRED"
            self.arm_state_label.setText(f"Current Arm State: {state} - {arm.uses_remaining} use(s) left, "
                                         f"{len(arm.fills)} fill(s)")
            self.client_label.setText(f"Client ID: {arm.client_token}")
            self.expiry_label.setText(f"Expiry: {datetime.datetime.fromtimestamp(arm.expires_at):%H:%M:%S}")
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
        state = msg.get('state') or (msg.get('arm') or {}).get('state') or 'UNKNOWN'
        self._add_log("SCA_STATE", state, f"Browser: arm {msg.get('arm_id') or '-'} is {state}")

    def on_sca_error(self, msg):
        detail = msg.get('detail', 'Unknown error')
        self._add_log("SCA_ERROR", "FAILED", detail)

    def on_sca_fill_result(self, msg):
        res = msg.get('result', 'unknown')
        detail = msg.get('reason') or msg.get('detail', '')
        self._add_log("SCA_FILL_RESULT", res.upper(), detail)

    def on_audit_event(self, msg):
        if msg.get('type') == 'audit_event' and 'SCA' in msg.get('action', ''):
            self._add_log("AUDIT", "INFO", msg.get('detail', ''))
