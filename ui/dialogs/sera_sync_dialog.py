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
    QSplitter,
    QListWidget,
    QListWidgetItem,
    QGroupBox,
    QWidget,
    QSizePolicy,
)
from PySide6.QtGui import QColor, QFont

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
    Admin-only dialog showing Sera instances on the local network along with
    a live plain-text activity log sidebar tracking all P2P discovery, push, pull,
    and Sync Guard events.
    """
    sync_pushed = Signal(str)  # emitted with peer hostname after successful push
    activity_signal = Signal(str, str, str)  # (timestamp, category, message)

    def __init__(self, sync_service, db=None, actor="System", parent=None):
        super().__init__(parent)
        self.sync_service = sync_service
        self.db = db
        self.actor = actor
        self.setWindowTitle("Sera Sync — LAN Database Sync & Live Activity")
        self.resize(1080, 620)
        self.setMinimumSize(920, 500)

        self._build_ui()
        self._refresh_peers()
        self._load_existing_activity()

        # Connect thread-safe activity signal
        self.activity_signal.connect(self._on_activity_received)
        if self.sync_service:
            self.sync_service.on_activity = lambda ts, cat, msg: self.activity_signal.emit(ts, cat, msg)

        # Auto-refresh peer table every 3 seconds
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_peers)
        self._refresh_timer.start(3000)

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 4, 14, 10)
        main_layout.setSpacing(6)

        # Top Header Container
        header_widget = QWidget()
        header_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(2, 0, 2, 0)
        header_layout.setSpacing(8)

        icon_lbl = QLabel()
        icon = _safe_icon("mdi.sync", color="#4CF9B7")
        if icon:
            icon_lbl.setPixmap(icon.pixmap(24, 24))
        header_layout.addWidget(icon_lbl)

        title = QLabel("Sera Sync Network Control")
        title.setProperty("class", "DialogTitle")
        title.setStyleSheet("font-size: 18px; font-weight: 700; margin: 0px; padding: 0px;")
        header_layout.addWidget(title)
        header_layout.addStretch()

        # Inv-Frames Sovereign Mode Toggle Button
        self.btn_inv_frames = QPushButton("🛡️ Inv-Frames: OFF")
        self.btn_inv_frames.setCursor(Qt.PointingHandCursor)
        self.btn_inv_frames.setToolTip(
            "Toggle Invincibility Frames (inv_frames) protocol:\n"
            "• ON: Node rejects all incoming database sync, but can push to other nodes.\n"
            "• If only 1 node is ON, it acts as sovereign master.\n"
            "• If >1 node is ON, sync across entire LAN is frozen to prevent corruption."
        )
        self.btn_inv_frames.clicked.connect(self._on_toggle_inv_frames)
        header_layout.addWidget(self.btn_inv_frames)

        # Online indicator
        self.status_label = QLabel()
        self.status_label.setStyleSheet("color: #4CF9B7; font-size: 13px; font-weight: 600; margin: 0px; padding: 0px;")
        header_layout.addWidget(self.status_label)

        main_layout.addWidget(header_widget)

        # LAN Protocol Status Banner
        self.protocol_banner = QLabel()
        self.protocol_banner.setWordWrap(True)
        self.protocol_banner.setStyleSheet(
            "QLabel { padding: 6px 12px; border-radius: 6px; font-size: 12px; font-weight: 600; }"
        )
        main_layout.addWidget(self.protocol_banner)
        self._update_inv_frames_ui()

        # Main Splitter (Left: Peer Table & Controls, Right: Live Activity Log Sidebar)
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        # Left Widget (Peers Table & Main Action Buttons)
        left_widget = QGroupBox("Discovered LAN Devices")
        left_widget.setStyleSheet("QGroupBox { font-weight: 700; border: 1px solid rgba(255,255,255,0.15); border-radius: 8px; padding-top: 14px; }")
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(12, 14, 12, 12)
        left_layout.setSpacing(10)

        desc = QLabel(
            "Devices running Sera on your local network are listed below. "
            "Select a workstation to push/pull database updates or monitor LAN revision scores."
        )
        desc.setWordWrap(True)
        desc.setProperty("class", "GuidanceText")
        desc.setStyleSheet("font-size: 12px; color: #B0B0B0;")
        left_layout.addWidget(desc)

        # Peer Table (8 Columns: Username, Hostname, IP, Version, DB Modified, Rev Score, Clients / Dumps, Mode / Status)
        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels([
            "Username", "Hostname", "IP Address", "App Version", "DB Modified", "Rev Score", "Clients / Dumps", "Mode / Status"
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        left_layout.addWidget(self.table)

        # Left Action Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.btn_sync = QPushButton("  Sync Selected")
        icon = _safe_icon("mdi.database-export", color="#FFFFFF")
        if icon:
            self.btn_sync.setIcon(icon)
        self.btn_sync.setStyleSheet(
            "QPushButton { background-color: #2E9B5F; color: white; font-weight: 600; "
            "padding: 8px 14px; border-radius: 6px; } "
            "QPushButton:hover { background-color: #34B76D; }"
        )
        self.btn_sync.clicked.connect(self._on_sync_clicked)
        btn_row.addWidget(self.btn_sync)

        self.btn_sync_all = QPushButton("  Sync To All Devices")
        icon = _safe_icon("mdi.database-sync", color="#FFFFFF")
        if icon:
            self.btn_sync_all.setIcon(icon)
        self.btn_sync_all.setStyleSheet(
            "QPushButton { background-color: #1A73E8; color: white; font-weight: 600; "
            "padding: 8px 14px; border-radius: 6px; } "
            "QPushButton:hover { background-color: #2884FB; }"
        )
        self.btn_sync_all.clicked.connect(self._on_sync_all_clicked)
        btn_row.addWidget(self.btn_sync_all)

        self.btn_refresh = QPushButton("  Refresh")
        icon = _safe_icon("mdi.refresh", color="#FFFFFF")
        if icon:
            self.btn_refresh.setIcon(icon)
        self.btn_refresh.clicked.connect(self._refresh_peers)
        btn_row.addWidget(self.btn_refresh)

        btn_row.addStretch()
        left_layout.addLayout(btn_row)

        splitter.addWidget(left_widget)

        # Right Widget (Activity Log Sidebar)
        right_widget = QGroupBox("⚡ Live Sync Activity Stream")
        right_widget.setStyleSheet("QGroupBox { font-weight: 700; border: 1px solid rgba(255,255,255,0.15); border-radius: 8px; padding-top: 14px; }")
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(12, 14, 12, 12)
        right_layout.setSpacing(8)

        log_top = QHBoxLayout()
        log_info = QLabel("Real-time P2P sync events, revision scores & guard alerts:")
        log_info.setStyleSheet("font-size: 11px; color: #A0A0A0;")
        log_top.addWidget(log_info)
        log_top.addStretch()

        btn_clear_log = QPushButton("Clear")
        btn_clear_log.setStyleSheet("padding: 3px 10px; font-size: 11px;")
        btn_clear_log.clicked.connect(self._clear_activity_log)
        log_top.addWidget(btn_clear_log)
        right_layout.addLayout(log_top)

        self.log_list = QListWidget()
        self.log_list.setStyleSheet(
            "QListWidget { background-color: #161B22; border: 1px solid #30363D; border-radius: 6px; padding: 4px; font-family: Consolas, monospace; font-size: 11px; }"
            "QListWidget::item { padding: 5px 6px; border-bottom: 1px solid #21262D; }"
        )
        self.log_list.setSelectionMode(QAbstractItemView.NoSelection)
        right_layout.addWidget(self.log_list)

        splitter.addWidget(right_widget)

        # Set Splitter ratio: Left 62%, Right 38%
        splitter.setSizes([650, 410])
        main_layout.addWidget(splitter)

        # Bottom Close Row
        bottom_row = QHBoxLayout()
        bottom_row.addStretch()
        self.btn_close = QPushButton("Close")
        self.btn_close.setStyleSheet("padding: 6px 18px;")
        self.btn_close.clicked.connect(self.accept)
        bottom_row.addWidget(self.btn_close)

        main_layout.addLayout(bottom_row)

    def _on_toggle_inv_frames(self):
        if not self.sync_service:
            return
        new_val = not getattr(self.sync_service, "inv_frames", False)
        self.sync_service.set_inv_frames(new_val)
        if self.db:
            try:
                self.db.set_setting("inv_frames", "1" if new_val else "0")
            except Exception:
                pass
        self._update_inv_frames_ui()
        self._refresh_peers()

    def _update_inv_frames_ui(self):
        if not self.sync_service:
            return

        is_local_inv = getattr(self.sync_service, "inv_frames", False)
        if is_local_inv:
            self.btn_inv_frames.setText("🛡️ Inv-Frames: ON")
            self.btn_inv_frames.setStyleSheet(
                "QPushButton { background-color: #F2C94C; color: #121212; font-weight: 700; "
                "padding: 5px 12px; border-radius: 5px; border: 1px solid #E5B83B; } "
                "QPushButton:hover { background-color: #FFD566; }"
            )
        else:
            self.btn_inv_frames.setText("🛡️ Inv-Frames: OFF")
            self.btn_inv_frames.setStyleSheet(
                "QPushButton { background-color: #21262D; color: #8B949E; font-weight: 600; "
                "padding: 5px 12px; border-radius: 5px; border: 1px solid #30363D; } "
                "QPushButton:hover { background-color: #30363D; color: #C9D1D9; }"
            )

        # Update dynamic LAN protocol status banner
        sync_state = self.sync_service.get_sync_state() if hasattr(self.sync_service, "get_sync_state") else {}
        status = sync_state.get("status", "NORMAL")

        if status == "INV_FRAMES_MASTER":
            self.protocol_banner.setText("🛡️ INV-FRAMES ACTIVE (Local Node is Master Authority — All incoming sync rejected, pushing to LAN)")
            self.protocol_banner.setStyleSheet(
                "QLabel { background-color: #2A2000; color: #F2C94C; border: 1px solid #F2C94C; "
                "padding: 6px 12px; border-radius: 6px; font-size: 12px; font-weight: 700; }"
            )
        elif status == "LAN_SYNC_FROZEN_MULTI_INV":
            inv_nodes_str = ", ".join(sync_state.get("active_inv_frames_nodes", []))
            self.protocol_banner.setText(f"⛔ LAN SYNC FROZEN — Multiple nodes ({inv_nodes_str}) have Inv-Frames enabled. All LAN sync is paused.")
            self.protocol_banner.setStyleSheet(
                "QLabel { background-color: #300808; color: #FF8080; border: 1px solid #FF4D4D; "
                "padding: 6px 12px; border-radius: 6px; font-size: 12px; font-weight: 700; }"
            )
        elif status == "INV_FRAMES_FOLLOWER":
            auth = sync_state.get("authority_host", "Remote Master")
            self.protocol_banner.setText(f"📥 FOLLOWING INV-FRAMES MASTER ({auth}) — Normal P2P sync locked; accepting master pushes.")
            self.protocol_banner.setStyleSheet(
                "QLabel { background-color: #001F33; color: #7DD3FC; border: 1px solid #38BDF8; "
                "padding: 6px 12px; border-radius: 6px; font-size: 12px; font-weight: 600; }"
            )
        else:
            self.protocol_banner.setText("🟢 LAN SYNC ACTIVE (Normal P2P Operational — All nodes synchronized bidirectionally)")
            self.protocol_banner.setStyleSheet(
                "QLabel { background-color: #082012; color: #4CF9B7; border: 1px solid #2E9B5F; "
                "padding: 6px 12px; border-radius: 6px; font-size: 12px; font-weight: 600; }"
            )

    def _load_existing_activity(self):
        if self.sync_service and hasattr(self.sync_service, "get_activity_history"):
            history = self.sync_service.get_activity_history()
            for entry in history:
                ts = entry.get("timestamp", "")
                cat = entry.get("category", "INFO")
                title = entry.get("title", "")
                detail = entry.get("detail", "")
                msg = f"{title} - {detail}" if detail else title
                self._add_log_item(ts, cat, msg)

    def _on_activity_received(self, timestamp: str, category: str, message: str):
        self._add_log_item(timestamp, category, message)
        self._update_inv_frames_ui()

    def _add_log_item(self, ts: str, cat: str, message: str):
        # Format organized plain-text activity log item with colored badges
        icon_badge = "ℹ️"
        color = "#C9D1D9"

        cat_upper = cat.upper()
        if "INV_FRAMES" in cat_upper:
            icon_badge = "🛡️"
            color = "#F2C94C"
        elif "REVISION" in cat_upper:
            icon_badge = "📊"
            color = "#38D9A9"
        elif "BEACON" in cat_upper:
            icon_badge = "🟢"
            color = "#7EE787"
        elif "GUARD" in cat_upper:
            icon_badge = "⚠️"
            color = "#FFA657"
        elif "PULL" in cat_upper:
            icon_badge = "📥"
            color = "#58A6FF"
        elif "PUSH" in cat_upper:
            icon_badge = "📤"
            color = "#D2A8FF"
        elif "SSAL" in cat_upper:
            icon_badge = "📋"
            color = "#79C0FF"
        elif "SYNC IN" in cat_upper or "ACCEPTED" in cat_upper:
            icon_badge = "✅"
            color = "#56D364"

        formatted_text = f"[{ts}] {icon_badge} {cat_upper:^10} | {message}"
        item = QListWidgetItem(formatted_text)
        item.setForeground(QColor(color))
        self.log_list.addItem(item)
        self.log_list.scrollToBottom()

    def _clear_activity_log(self):
        self.log_list.clear()

    def _refresh_peers(self):
        if not self.sync_service:
            return
        peers = self.sync_service.get_peers()
        self.status_label.setText(f"🟢 {len(peers)} device{'s' if len(peers) != 1 else ''} online")
        self._update_inv_frames_ui()

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

            ver_item = QTableWidgetItem(peer.get("app_version", "v2.4.0"))
            self.table.setItem(r_idx, 3, ver_item)

            mtime_item = QTableWidgetItem(peer.get("db_mtime", "N/A"))
            self.table.setItem(r_idx, 4, mtime_item)

            rev_score = peer.get("sync_revision", 0)
            rev_item = QTableWidgetItem(str(rev_score))
            rev_item.setTextAlignment(Qt.AlignCenter)
            rev_item.setForeground(QColor("#38D9A9"))
            self.table.setItem(r_idx, 5, rev_item)

            c_cnt = peer.get("client_count", 0)
            t_cnt = peer.get("tracker_count", 0)
            data_item = QTableWidgetItem(f"{c_cnt} CLI | {t_cnt} Dumps")
            data_item.setTextAlignment(Qt.AlignCenter)
            data_item.setForeground(QColor("#7DD3FC"))
            self.table.setItem(r_idx, 6, data_item)

            is_peer_inv = peer.get("inv_frames", False)
            if is_peer_inv:
                status_item = QTableWidgetItem("🛡️ Inv-Frames")
                status_item.setForeground(QColor("#F2C94C"))
            else:
                status_item = QTableWidgetItem("🟢 Normal")
                status_item.setForeground(QColor("#4CF9B7"))
            self.table.setItem(r_idx, 7, status_item)

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

        sync_state = self.sync_service.get_sync_state() if hasattr(self.sync_service, "get_sync_state") else {}
        if sync_state.get("status") == "LAN_SYNC_FROZEN_MULTI_INV":
            inv_nodes_str = ", ".join(sync_state.get("active_inv_frames_nodes", []))
            QMessageBox.warning(
                self, "LAN Sync Frozen",
                f"LAN Sync is currently frozen because multiple nodes ({inv_nodes_str}) have Inv-Frames active.\n\n"
                f"Please disable Inv-Frames on other nodes before initiating sync."
            )
            return

        username_item = self.table.item(selected, 0)
        peer_data = username_item.data(Qt.UserRole)
        peer_host = peer_data.get("host", "Unknown")
        peer_ip = peer_data.get("ip")
        peer_port = peer_data.get("sync_port", 49157)
        peer_username = peer_data.get("username", "Unknown")
        peer_inv = peer_data.get("inv_frames", False)

        if peer_inv:
            QMessageBox.warning(
                self, "Target Node is Sovereign (Inv-Frames)",
                f"Workstation {peer_username} ({peer_host}) has Inv-Frames enabled.\n\n"
                f"This node rejects all incoming database sync. To sync to this node, disable Inv-Frames on {peer_host} first."
            )
            return

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

        status_item = self.table.item(selected, 6)
        if status_item:
            status_item.setText("🔄 Syncing...")

        self.btn_sync.setEnabled(False)
        self.btn_sync.setText("  Syncing...")

        try:
            result = self.sync_service.push_to(peer_ip, peer_port, force_override=True)

            if "successfully" in result.lower():
                if self.db:
                    try:
                        self.db.log_action(
                            self.actor, "sync_pushed",
                            detail=f"Pushed database to {peer_username} ({peer_host} - {peer_ip})"
                        )
                    except Exception:
                        pass
                QMessageBox.information(self, "Sync Complete", f"{result}\n\nDatabase sent to {peer_username} ({peer_host}).")
                self.sync_pushed.emit(peer_host)
            else:
                QMessageBox.warning(self, "Sync Issue", result)
        except Exception as e:
            QMessageBox.critical(self, "Sync Error", f"Failed to sync database:\n{e!s}")
        finally:
            self.btn_sync.setEnabled(True)
            self.btn_sync.setText("  Sync Selected")
            self._refresh_peers()

    def _on_sync_all_clicked(self):
        sync_state = self.sync_service.get_sync_state() if hasattr(self.sync_service, "get_sync_state") else {}
        if sync_state.get("status") == "LAN_SYNC_FROZEN_MULTI_INV":
            inv_nodes_str = ", ".join(sync_state.get("active_inv_frames_nodes", []))
            QMessageBox.warning(
                self, "LAN Sync Frozen",
                f"LAN Sync is currently frozen because multiple nodes ({inv_nodes_str}) have Inv-Frames active.\n\n"
                f"Please disable Inv-Frames on other nodes before initiating sync."
            )
            return

        peers = self.sync_service.get_peers()
        if not peers:
            QMessageBox.information(
                self, "No Online Devices",
                "No other devices running Sera were discovered on the local network."
            )
            return

        peer_names = ", ".join([f"{p.get('username')} ({p.get('host')})" for p in peers])
        confirm = QMessageBox.warning(
            self, "Confirm Bulk Database Sync",
            f"You are about to push your database to ALL {len(peers)} online device(s):\n\n"
            f"  Target Devices: {peer_names}\n\n"
            f"This will OVERWRITE their databases with your current database.\n"
            f"Target devices will auto-restart with your database.\n\n"
            f"Are you sure you want to proceed?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )

        if confirm != QMessageBox.Yes:
            return

        self.btn_sync_all.setEnabled(False)
        self.btn_sync_all.setText("  Syncing All...")

        try:
            results = self.sync_service.push_to_all(peers)
            successes = []
            failures = []

            for peer in peers:
                host = peer.get("host", "Unknown")
                user = peer.get("username", "Unknown")
                res = results.get(host, "No response")
                if "successfully" in res.lower():
                    successes.append(f"• {user} ({host}): Success")
                    if self.db:
                        try:
                            self.db.log_action(
                                self.actor, "sync_pushed",
                                detail=f"Pushed database to {user} ({host} - {peer.get('ip')})"
                            )
                        except Exception:
                            pass
                else:
                    failures.append(f"• {user} ({host}): {res}")

            msg_parts = []
            if successes:
                msg_parts.append("Successfully synced database to:\n" + "\n".join(successes))
            if failures:
                msg_parts.append("Failed to sync to:\n" + "\n".join(failures))

            full_msg = "\n\n".join(msg_parts)
            if failures:
                QMessageBox.warning(self, "Bulk Sync Results", full_msg)
            else:
                QMessageBox.information(self, "Bulk Sync Complete", full_msg)

        except Exception as e:
            QMessageBox.critical(self, "Sync Error", f"Bulk sync failed:\n{e!s}")
        finally:
            self.btn_sync_all.setEnabled(True)
            self.btn_sync_all.setText("  Sync To All Devices")
            self._refresh_peers()

    def closeEvent(self, event):
        self._refresh_timer.stop()
        super().closeEvent(event)
