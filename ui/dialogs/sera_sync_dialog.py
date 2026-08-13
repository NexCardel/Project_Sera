from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QAbstractItemView,
    QMessageBox,
    QHeaderView,
)
try:
    import qtawesome as qta
except Exception:
    qta = None


def _safe_icon(name, **kwargs):
    if qta:
        try:
            return qta.icon(name, **kwargs)
        except Exception:
            pass
    return None


class SeraSyncDialog(QDialog):
    """
    Admin-only dialog showing other Sera instances on the local network.
    Allows pushing the local database to a selected peer.
    """
    sync_pushed = Signal(str)  # emitted with peer hostname after successful push

    def __init__(self, sync_service, parent=None):
        super().__init__(parent)
        self.sync_service = sync_service
        self.setWindowTitle("Sera Sync — LAN Database Sync")
        self.resize(640, 420)
        self.setMinimumSize(560, 360)
        self._build_ui()
        self._refresh_peers()

        # Auto-refresh peer table every 3 seconds
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_peers)
        self._refresh_timer.start(3000)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        # Header
        header_row = QHBoxLayout()
        header_row.setSpacing(10)

        icon_lbl = QLabel()
        icon = _safe_icon("mdi.sync", color="#4CF9B7")
        if icon:
            icon_lbl.setPixmap(icon.pixmap(24, 24))
        header_row.addWidget(icon_lbl)

        title = QLabel("Sera Sync")
        title.setProperty("class", "DialogTitle")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        header_row.addWidget(title)
        header_row.addStretch()

        # Online indicator
        self.status_label = QLabel()
        self.status_label.setStyleSheet("color: #4CF9B7; font-size: 12px; font-weight: 600;")
        header_row.addWidget(self.status_label)

        layout.addLayout(header_row)

        desc = QLabel(
            "Devices running Sera on your local network are listed below. "
            "Select a device and click 'Sync Database' to push your current database to that device. "
            "The receiving device will auto-restart with your database."
        )
        desc.setWordWrap(True)
        desc.setProperty("class", "GuidanceText")
        layout.addWidget(desc)

        # Peer Table
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Username", "Hostname", "IP Address", "Status"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table)

        # Action Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.btn_sync = QPushButton("  Sync Database To Selected")
        icon = _safe_icon("mdi.database-export", color="#FFFFFF")
        if icon:
            self.btn_sync.setIcon(icon)
        self.btn_sync.setProperty("class", "primary")
        self.btn_sync.setStyleSheet(
            "QPushButton { background-color: #2E9B5F; color: white; font-weight: 600; "
            "padding: 8px 16px; border-radius: 6px; } "
            "QPushButton:hover { background-color: #34B76D; }"
        )
        self.btn_sync.clicked.connect(self._on_sync_clicked)
        btn_row.addWidget(self.btn_sync)

        self.btn_refresh = QPushButton("  Refresh")
        icon = _safe_icon("mdi.refresh", color="#FFFFFF")
        if icon:
            self.btn_refresh.setIcon(icon)
        self.btn_refresh.clicked.connect(self._refresh_peers)
        btn_row.addWidget(self.btn_refresh)

        btn_row.addStretch()

        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_close)

        layout.addLayout(btn_row)

    def _refresh_peers(self):
        peers = self.sync_service.get_peers()
        self.status_label.setText(f"🟢 {len(peers)} device{'s' if len(peers) != 1 else ''} online")

        # Preserve selection
        selected_key = None
        sel_row = self.table.currentRow()
        if sel_row >= 0:
            host_item = self.table.item(sel_row, 1)
            ip_item = self.table.item(sel_row, 2)
            if host_item and ip_item:
                selected_key = f"{host_item.text()}:{ip_item.text()}"

        self.table.setRowCount(len(peers))
        new_sel_row = -1

        for r_idx, peer in enumerate(peers):
            key = f"{peer['host']}:{peer['ip']}"
            if key == selected_key:
                new_sel_row = r_idx

            username_item = QTableWidgetItem(peer.get("username", "Unknown"))
            username_item.setData(Qt.UserRole, peer)
            self.table.setItem(r_idx, 0, username_item)

            host_item = QTableWidgetItem(peer.get("host", ""))
            self.table.setItem(r_idx, 1, host_item)

            ip_item = QTableWidgetItem(peer.get("ip", ""))
            self.table.setItem(r_idx, 2, ip_item)

            status_item = QTableWidgetItem("🟢 Online")
            status_item.setForeground(Qt.green)
            self.table.setItem(r_idx, 3, status_item)

        if new_sel_row >= 0:
            self.table.selectRow(new_sel_row)

    def _on_sync_clicked(self):
        selected = self.table.currentRow()
        if selected < 0:
            QMessageBox.information(
                self, "No Device Selected",
                "Please select a device from the list to sync your database to."
            )
            return

        username_item = self.table.item(selected, 0)
        peer_data = username_item.data(Qt.UserRole)
        peer_host = peer_data.get("host", "Unknown")
        peer_ip = peer_data.get("ip")
        peer_port = peer_data.get("sync_port", 49157)
        peer_username = peer_data.get("username", "Unknown")

        confirm = QMessageBox.warning(
            self, "Confirm Database Sync",
            f"You are about to push your entire database to:\n\n"
            f"  Username: {peer_username}\n"
            f"  Hostname: {peer_host}\n"
            f"  IP: {peer_ip}\n\n"
            f"This will OVERWRITE their database with yours.\n"
            f"Their app will auto-restart with your database.\n\n"
            f"Are you sure?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )

        if confirm != QMessageBox.Yes:
            return

        # Update status to "Syncing..."
        status_item = self.table.item(selected, 3)
        if status_item:
            status_item.setText("🔄 Syncing...")

        self.btn_sync.setEnabled(False)
        self.btn_sync.setText("  Syncing...")

        try:
            result = self.sync_service.push_to(peer_ip, peer_port)

            if "successfully" in result.lower():
                QMessageBox.information(self, "Sync Complete", f"{result}\n\nDatabase sent to {peer_username} ({peer_host}).")
                self.sync_pushed.emit(peer_host)
            else:
                QMessageBox.warning(self, "Sync Issue", result)
        except Exception as e:
            QMessageBox.critical(self, "Sync Error", f"Failed to sync database:\n{e!s}")
        finally:
            self.btn_sync.setEnabled(True)
            self.btn_sync.setText("  Sync Database To Selected")
            self._refresh_peers()

    def closeEvent(self, event):
        self._refresh_timer.stop()
        super().closeEvent(event)
