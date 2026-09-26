import json
import threading
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
    QInputDialog,
    QMenu,
    QHeaderView,
    QSplitter,
    QListWidget,
    QListWidgetItem,
    QGroupBox,
    QWidget,
    QSizePolicy,
    QLineEdit,
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
    # AddWorkstationSession's on_joined/on_closed run on PairingWindow's background thread
    # (P2-4). QTimer.singleShot(0, fn) called from a non-GUI thread creates the timer on
    # that thread, which has no Qt event loop, so it never fires -- these signals are true
    # queued cross-thread delivery (Qt detects sender/receiver are on different threads).
    workstation_joined_signal = Signal(dict)
    workstation_closed_signal = Signal(str)
    # sync_shadow.run_shadow_checks runs in a background thread (P3-8); this brings the "Run
    # now" button + panel refresh back onto the GUI thread when it's done.
    shadow_check_finished_signal = Signal()
    # "Start shadow mode" on a non-admin PC (P3-7b) downloads the admin PC's replica in a
    # background thread; (plan or None, error text) comes back on the GUI thread.
    shadow_start_downloaded_signal = Signal(object, str)

    def __init__(self, sync_service, db=None, actor="System", parent=None, discovery_service=None,
                 sync_engine=None):
        super().__init__(parent)
        self.sync_service = sync_service
        self.db = db
        self.actor = actor
        self.discovery_service = discovery_service
        # The v3 sync engine (P3-5/P3-7); its .transport is already serving the permanent sync
        # port, so "Add workstation" reuses it instead of binding a second server on it.
        self.sync_engine = sync_engine
        self._add_workstation_session = None  # sync_office.AddWorkstationSession while "Add workstation" is open
        self._shadow_check_running = False  # P3-8b: "Run now" only makes sense in mode shadow
        self.setWindowTitle("Sera Sync — LAN Database Sync & Live Activity")
        self.resize(1080, 620)
        self.setMinimumSize(920, 500)

        self._build_ui()
        self._refresh_peers()
        self._refresh_sync_status()
        self._load_existing_activity()

        # Connect thread-safe activity signal
        self.activity_signal.connect(self._on_activity_received)
        if self.sync_service:
            self.sync_service.on_activity = lambda ts, cat, msg: self.activity_signal.emit(ts, cat, msg)

        # Add workstation (P2-7): PairingWindow calls on_joined/on_closed from its own worker
        # thread; these connections queue safely onto this dialog's (GUI) thread.
        self.workstation_joined_signal.connect(lambda record: self._refresh_members())
        self.workstation_closed_signal.connect(self._on_add_workstation_closed)
        self.shadow_check_finished_signal.connect(self._on_shadow_check_finished)
        self.shadow_start_downloaded_signal.connect(self._on_shadow_start_downloaded)

        # Auto-refresh peer table every 3 seconds
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_peers)
        self._refresh_timer.timeout.connect(self._refresh_sync_status)
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

        # Public-network warning (P0-9b). Hidden unless the active network is Public.
        self.network_warning_banner = QLabel()
        self.network_warning_banner.setWordWrap(True)
        self.network_warning_banner.setStyleSheet(
            "QLabel { background-color: #300808; color: #FF8080; border: 1px solid #FF4D4D; "
            "padding: 6px 12px; border-radius: 6px; font-size: 12px; font-weight: 600; }"
        )
        self.network_warning_banner.setText(
            "⚠️ This PC's network is set to Public. Sera Sync is reachable by other devices on it. "
            "For security, set it to Private: Windows Settings → Network → set this network to Private."
        )
        self.network_warning_banner.setVisible(False)
        main_layout.addWidget(self.network_warning_banner)
        self._update_network_warning()

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
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)
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

        self.btn_add_ip = QPushButton("  Add PC by IP")
        icon = _safe_icon("mdi.plus-network", color="#FFFFFF")
        if icon:
            self.btn_add_ip.setIcon(icon)
        self.btn_add_ip.clicked.connect(self._on_add_pc_by_ip)
        btn_row.addWidget(self.btn_add_ip)

        self.btn_remove_ip = QPushButton("  Remove PC by IP")
        icon = _safe_icon("mdi.minus-network", color="#FFFFFF")
        if icon:
            self.btn_remove_ip.setIcon(icon)
        self.btn_remove_ip.clicked.connect(self._on_remove_pc_by_ip)
        btn_row.addWidget(self.btn_remove_ip)

        btn_row.addStretch()

        self.btn_office_key = QPushButton("  Convert to office key (admin PC only)")
        icon = _safe_icon("mdi.key-change", color="#FFFFFF")
        if icon:
            self.btn_office_key.setIcon(icon)
        self.btn_office_key.clicked.connect(self._on_convert_to_office_key)
        # Only a legacy-mode PC (no office key id yet) can be converted.
        self.btn_office_key.setVisible(
            self.sync_service is not None and getattr(self.sync_service, "key_id", None) is None
        )
        btn_row.addWidget(self.btn_office_key)

        self.btn_rejoin = QPushButton("  Rejoin office")
        rejoin_icon = _safe_icon("mdi.database-import", color="#FFFFFF")
        if rejoin_icon:
            self.btn_rejoin.setIcon(rejoin_icon)
        self.btn_rejoin.clicked.connect(self._on_rejoin_office)
        # P2-8: every legacy-mode PC except the admin PC rejoins instead of converting.
        self.btn_rejoin.setVisible(
            self.sync_service is not None and getattr(self.sync_service, "key_id", None) is None
        )
        btn_row.addWidget(self.btn_rejoin)

        self.btn_export_kit = QPushButton("  Export recovery kit")
        kit_icon = _safe_icon("mdi.file-key-outline", color="#FFFFFF")
        if kit_icon:
            self.btn_export_kit.setIcon(kit_icon)
        self.btn_export_kit.clicked.connect(self._on_export_recovery_kit)
        self.btn_export_kit.setVisible(
            self.sync_service is not None and getattr(self.sync_service, "key_id", None) is not None
        )
        btn_row.addWidget(self.btn_export_kit)

        left_layout.addLayout(btn_row)

        # Office Members panel (P2-7): members, Add workstation, Remove, Hand over/Become admin,
        # network warnings. Office mode only (legacy-mode PCs have no admin key / member records).
        self.members_group = QGroupBox("Office Members")
        self.members_group.setStyleSheet(
            "QGroupBox { font-weight: 700; border: 1px solid rgba(255,255,255,0.15); border-radius: 8px; padding-top: 14px; }")
        members_layout = QVBoxLayout(self.members_group)
        members_layout.setContentsMargins(12, 14, 12, 12)
        members_layout.setSpacing(8)

        self.members_network_warning = QLabel("")
        self.members_network_warning.setWordWrap(True)
        self.members_network_warning.setStyleSheet(
            "QLabel { background-color: #300808; color: #FF8080; border: 1px solid #FF4D4D; "
            "padding: 6px 12px; border-radius: 6px; font-size: 11px; }"
        )
        self.members_network_warning.setVisible(False)
        members_layout.addWidget(self.members_network_warning)

        self.members_table = QTableWidget(0, 5)
        self.members_table.setHorizontalHeaderLabels(
            ["Name", "Online", "Role", "Last Successful Sync (this PC)", "This PC"])
        self.members_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.members_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.members_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.members_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.members_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.members_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.members_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.members_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.members_table.setMaximumHeight(140)
        members_layout.addWidget(self.members_table)

        members_btn_row = QHBoxLayout()
        self.btn_add_workstation = QPushButton("  Add workstation")
        icon = _safe_icon("mdi.laptop", color="#FFFFFF")
        if icon:
            self.btn_add_workstation.setIcon(icon)
        self.btn_add_workstation.clicked.connect(self._on_add_workstation)
        members_btn_row.addWidget(self.btn_add_workstation)

        self.btn_remove_member = QPushButton("  Remove")
        icon = _safe_icon("mdi.account-remove", color="#FFFFFF")
        if icon:
            self.btn_remove_member.setIcon(icon)
        self.btn_remove_member.clicked.connect(self._on_remove_member)
        members_btn_row.addWidget(self.btn_remove_member)

        self.btn_hand_over_admin = QPushButton("  Hand over admin")
        icon = _safe_icon("mdi.account-arrow-right", color="#FFFFFF")
        if icon:
            self.btn_hand_over_admin.setIcon(icon)
        self.btn_hand_over_admin.clicked.connect(self._on_hand_over_admin)
        members_btn_row.addWidget(self.btn_hand_over_admin)

        self.btn_become_admin = QPushButton("  Become admin")
        icon = _safe_icon("mdi.shield-account", color="#FFFFFF")
        if icon:
            self.btn_become_admin.setIcon(icon)
        self.btn_become_admin.clicked.connect(self._on_become_admin)
        members_btn_row.addWidget(self.btn_become_admin)
        members_btn_row.addStretch()
        members_layout.addLayout(members_btn_row)

        left_layout.addWidget(self.members_group)
        # Office mode only: key_id is set once office.json exists (legacy PCs have none).
        self.members_group.setVisible(
            self.sync_service is not None and getattr(self.sync_service, "key_id", None) is not None
        )

        # Sync Status panel (P3-8): pending outgoing count, parked changes, conflicts (with
        # resolution actions) and the shadow-check summary. Office mode only, like Members.
        self.sync_status_group = QGroupBox("Sync Status")
        self.sync_status_group.setStyleSheet(
            "QGroupBox { font-weight: 700; border: 1px solid rgba(255,255,255,0.15); border-radius: 8px; padding-top: 14px; }")
        sync_status_layout = QVBoxLayout(self.sync_status_group)
        sync_status_layout.setContentsMargins(12, 14, 12, 12)
        sync_status_layout.setSpacing(8)

        self.sync_status_summary_label = QLabel("Pending outgoing: 0   •   Parked: 0")
        self.sync_status_summary_label.setStyleSheet("font-size: 12px; color: #B0B0B0;")
        sync_status_layout.addWidget(self.sync_status_summary_label)

        self.sync_status_warning_label = QLabel("")
        self.sync_status_warning_label.setWordWrap(True)
        self.sync_status_warning_label.setVisible(False)
        sync_status_layout.addWidget(self.sync_status_warning_label)

        # Turning shadow mode on / resetting it (P3-7b). Admin mode (PIN) is asked for again.
        shadow_mode_row = QHBoxLayout()
        self.shadow_mode_label = QLabel("Shadow mode: off")
        self.shadow_mode_label.setWordWrap(True)
        self.shadow_mode_label.setStyleSheet("font-size: 12px; color: #B0B0B0;")
        shadow_mode_row.addWidget(self.shadow_mode_label, 1)
        self.btn_start_shadow = QPushButton("  Start shadow mode")
        icon = _safe_icon("mdi.play-circle-outline", color="#FFFFFF")
        if icon:
            self.btn_start_shadow.setIcon(icon)
        self.btn_start_shadow.setToolTip("Start the 7-day shadow week on this PC (admin PC first, then every other PC).")
        self.btn_start_shadow.clicked.connect(self._on_start_shadow_mode)
        shadow_mode_row.addWidget(self.btn_start_shadow)
        self.btn_reset_shadow = QPushButton("  Reset shadow mode")
        icon = _safe_icon("mdi.restore", color="#FFFFFF")
        if icon:
            self.btn_reset_shadow.setIcon(icon)
        self.btn_reset_shadow.setToolTip("Set shadow mode off on this PC and set its shadow files aside (the week starts over).")
        self.btn_reset_shadow.clicked.connect(self._on_reset_shadow_mode)
        shadow_mode_row.addWidget(self.btn_reset_shadow)
        sync_status_layout.addLayout(shadow_mode_row)

        shadow_row = QHBoxLayout()
        self.shadow_status_label = QLabel("Shadow checks: none logged yet")
        self.shadow_status_label.setWordWrap(True)
        self.shadow_status_label.setStyleSheet("font-size: 11px; color: #A0A0A0; font-family: Consolas, monospace;")
        shadow_row.addWidget(self.shadow_status_label, 1)

        self.btn_run_shadow_check = QPushButton("  Run now")
        icon = _safe_icon("mdi.shield-check", color="#FFFFFF")
        if icon:
            self.btn_run_shadow_check.setIcon(icon)
        self.btn_run_shadow_check.setToolTip("Run the shadow-mode capture check now (also runs every 30 minutes).")
        self.btn_run_shadow_check.clicked.connect(self._on_run_shadow_check)
        shadow_row.addWidget(self.btn_run_shadow_check)
        sync_status_layout.addLayout(shadow_row)

        conflicts_label = QLabel("Conflicts (field edits an older change lost to):")
        conflicts_label.setStyleSheet("font-size: 11px; color: #B0B0B0;")
        sync_status_layout.addWidget(conflicts_label)

        self.conflicts_table = QTableWidget(0, 5)
        self.conflicts_table.setHorizontalHeaderLabels(["Table", "Column", "Kept", "Discarded", "When"])
        self.conflicts_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.conflicts_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.conflicts_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.conflicts_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.conflicts_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.conflicts_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.conflicts_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.conflicts_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.conflicts_table.setMaximumHeight(120)
        sync_status_layout.addWidget(self.conflicts_table)

        conflicts_btn_row = QHBoxLayout()
        self.btn_conflict_keep = QPushButton("  Keep")
        icon = _safe_icon("mdi.check", color="#FFFFFF")
        if icon:
            self.btn_conflict_keep.setIcon(icon)
        self.btn_conflict_keep.setToolTip("Acknowledge the row already holds the value that won.")
        self.btn_conflict_keep.clicked.connect(self._on_conflict_keep)
        conflicts_btn_row.addWidget(self.btn_conflict_keep)

        self.btn_conflict_use_discarded = QPushButton("  Use discarded value")
        icon = _safe_icon("mdi.undo", color="#FFFFFF")
        if icon:
            self.btn_conflict_use_discarded.setIcon(icon)
        self.btn_conflict_use_discarded.setToolTip("Write the discarded value back, as a normal edit.")
        self.btn_conflict_use_discarded.clicked.connect(self._on_conflict_use_discarded)
        conflicts_btn_row.addWidget(self.btn_conflict_use_discarded)
        conflicts_btn_row.addStretch()
        sync_status_layout.addLayout(conflicts_btn_row)

        left_layout.addWidget(self.sync_status_group)
        # Mirrors the Members panel's own condition, not members_group.isVisible() -- at this
        # point in _build_ui() the dialog hasn't been shown yet, so isVisible() would read
        # False regardless.
        self.sync_status_group.setVisible(
            self.sync_service is not None and getattr(self.sync_service, "key_id", None) is not None
        )

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

    def _on_convert_to_office_key(self):
        if not self.sync_service:
            return
        reply = QMessageBox.question(
            self, "Convert to Office Key",
            "Convert this PC to an office key?\n\n"
            "Do this only on the admin PC: the one with the most complete database.\n\n"
            "Sera will restart and ask for the current master password and an office name. "
            "The current files are backed up first.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        from pathlib import Path
        import sync_migrate
        try:
            sync_migrate.write_migrate_request(Path(self.sync_service.db_path).parent, requested_by=self.actor)
        except OSError as e:
            QMessageBox.warning(self, "Convert to Office Key", f"Could not schedule the conversion: {e}")
            return
        try:
            self.sync_service.stop()
        except Exception:
            pass
        import version
        version.restart_app()

    def _on_rejoin_office(self):
        if not self.sync_service:
            return
        reply = QMessageBox.question(
            self, "Rejoin Office",
            "Rejoin the office on this PC?\n\n"
            "Sera will restart, pair with the admin PC (ask there for a code under \"Add workstation\") "
            "and download the office database. This PC's current files are kept in a \"legacy\" folder, "
            "and Sera then offers to import what only this PC has.\n\n"
            "Don't do this on the admin PC.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        from pathlib import Path
        import sync_rejoin
        try:
            sync_rejoin.write_rejoin_request(Path(self.sync_service.db_path).parent, requested_by=self.actor)
        except OSError as e:
            QMessageBox.warning(self, "Rejoin Office", f"Could not schedule the rejoin: {e}")
            return
        try:
            self.sync_service.stop()
        except Exception:
            pass
        import version
        version.restart_app()

    def _on_export_recovery_kit(self):
        if not self.sync_service:
            return
        from pathlib import Path
        from ui.dialogs.change_master_password_dialog import export_recovery_kit_flow
        app_dir = getattr(self.sync_service, "app_dir", None)
        if not app_dir:
            app_dir = Path(getattr(self.sync_service, "db_path", "")).parent
        export_recovery_kit_flow(self, app_dir)

    # ------------------------------------------------------------------
    # Office Members panel (P2-7): members, Add workstation, Remove, Hand
    # over/Become admin, network warnings.
    # ------------------------------------------------------------------

    def _app_dir(self):
        from pathlib import Path
        app_dir = getattr(self.sync_service, "app_dir", None)
        if not app_dir:
            app_dir = Path(getattr(self.sync_service, "db_path", "")).parent
        return Path(app_dir)

    def _own_identity(self):
        """Returns ``(device_id, cert_pem)`` for this PC, or ``(None, None)`` if unavailable."""
        import sync_identity
        identity = sync_identity.load_device_identity(self._app_dir())
        if identity is None:
            return None, None
        cert_pem = identity.cert_pem.decode("ascii") if isinstance(identity.cert_pem, bytes) else identity.cert_pem
        return identity.device_id, cert_pem

    def _office_admin_pubkey(self):
        import sera_keys
        office = sera_keys.load_office(self._app_dir())
        return office.admin_pubkey if office else None

    def _open_db_conn(self):
        """A plain DB-API connection to master.db (for code that calls ``.close()`` itself,
        e.g. ``PairingWindow``/``sync_admin`` -- ``SeraDatabase._connect()`` is a context
        manager, not a connection, so it can't be handed to those as an ``open_db`` factory)."""
        import sqlcipher3.dbapi2 as sqlite3
        conn = sqlite3.connect(self.db.db_path)
        conn.execute("PRAGMA key = \"x'%s'\";" % self.db.hex_key)
        return conn

    def _refresh_members(self):
        if not getattr(self, "members_group", None) or not self.members_group.isVisible() or self.db is None:
            return
        try:
            import datetime
            import sync_admin
            import sync_discovery
            admin_pubkey = self._office_admin_pubkey()
            if not admin_pubkey:
                return
            own_device_id, _ = self._own_identity()
            peer_status = {}
            if self.sync_engine is not None:
                try:
                    peer_status = self.sync_engine.peer_status()
                except Exception:
                    peer_status = {}
            with self.db._connect() as conn:
                members = sync_admin.list_members(conn, admin_pubkey, include_revoked=False)
                office_admin = sync_admin.get_office_admin(conn, admin_pubkey)
                sync_discovery.ensure_address_book_table(conn)
                warnings = sync_discovery.get_unreachable_member_warnings(conn, members)
                last_ok = {}
                for m in members:
                    dev_id = m.get("device_id")
                    # _local_addresses.local_ok_at is only updated by the side that dialled
                    # (P3-5 deviation 9), so a peer that always dials us shows stale/"never"
                    # there while peer_status()'s last_ok is current -- prefer it when we have
                    # it (P3-8 review, item 6).
                    engine_last_ok = peer_status.get(dev_id, {}).get("last_ok")
                    if engine_last_ok:
                        # Same format as the address book's local_ok_at (UTC, "...Z") -- P3-8b
                        # fix (c): the two sources used to mix a naive-local-time stamp here
                        # with a UTC one below.
                        last_ok[dev_id] = datetime.datetime.fromtimestamp(
                            engine_last_ok, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                        continue
                    addrs = sync_discovery.get_known_addresses(conn, dev_id)
                    stamps = [a["local_ok_at"] for a in addrs if a.get("local_ok_at")]
                    last_ok[dev_id] = max(stamps) if stamps else None
                # "PC <name> needs updating" / "This PC needs updating" (P3-5 note): the engine
                # already tracks this per peer in peer_status()[dev]["needs_update"] (set the
                # instant a session sees a schema mismatch, cleared on the next clean sync) --
                # folded into the same banner as the unreachable-member warnings.
                for m in members:
                    dev_id = m.get("device_id")
                    nu = peer_status.get(dev_id, {}).get("needs_update")
                    if nu:
                        name = m.get("name") or dev_id
                        if nu.get("who") == "this_pc":
                            warnings[f"{dev_id}:needs_update"] = (
                                f"This PC needs updating to sync with {name} (schema {nu.get('mine')} vs {nu.get('yours')})."
                            )
                        else:
                            warnings[f"{dev_id}:needs_update"] = (
                                f"PC {name} needs updating (schema {nu.get('yours')} vs this PC's {nu.get('mine')})."
                            )
                    ca = peer_status.get(dev_id, {}).get("clock_ahead")
                    if ca:
                        name = m.get("name") or dev_id
                        minutes = ca.get("minutes", max(1, round(ca.get("ahead_ms", 0) / 60000)))
                        warnings[f"{dev_id}:clock_ahead"] = f"PC {name}'s clock is {minutes} minutes ahead"
        except Exception as exc:
            print(f"[Sera Sync] could not refresh members: {exc}")
            return

        admin_device_id = office_admin["device_id"] if office_admin else None
        self._members_cache = members
        self.members_table.setUpdatesEnabled(False)
        self.members_table.setRowCount(len(members))
        for row, m in enumerate(members):
            dev_id = m.get("device_id")
            name_item = QTableWidgetItem(m.get("name", ""))
            name_item.setData(Qt.UserRole, dev_id)
            self.members_table.setItem(row, 0, name_item)
            self.members_table.setItem(row, 1, QTableWidgetItem(self._online_status(dev_id, own_device_id)))
            role = "Admin" if dev_id == admin_device_id else "Member"
            self.members_table.setItem(row, 2, QTableWidgetItem(role))
            self.members_table.setItem(row, 3, QTableWidgetItem(last_ok.get(dev_id) or "never"))
            self.members_table.setItem(row, 4, QTableWidgetItem("This PC" if dev_id == own_device_id else ""))
        self.members_table.setUpdatesEnabled(True)

        if warnings:
            self.members_network_warning.setText("\n".join(warnings.values()))
            self.members_network_warning.setVisible(True)
        else:
            self.members_network_warning.setVisible(False)

        is_admin = own_device_id is not None and own_device_id == admin_device_id
        self.btn_add_workstation.setEnabled(is_admin and self._add_workstation_session is None)
        self.btn_remove_member.setEnabled(is_admin)
        self.btn_hand_over_admin.setEnabled(is_admin)
        self.btn_become_admin.setEnabled(not is_admin)

    def _online_status(self, device_id: str, own_device_id: str | None) -> str:
        """Presence for the Members panel. Once the engine is running (P3-7), "Online" means
        a session with that PC actually completed in the last minute (P3-5's ``peer_status``,
        per its P3-8 note) -- a real connectivity signal, not just a recent beacon. Falls back
        to beacon recency (P2-5) when there is no engine yet or it has no session history for
        that PC (e.g. legacy mode, or this PC hasn't dialled it since start-up)."""
        if device_id == own_device_id:
            return "Online (this PC)"
        if self.sync_engine is not None:
            try:
                status = self.sync_engine.peer_status().get(device_id)
            except Exception:
                status = None
            if status is not None:
                return "Online" if status.get("online") else "Offline"
        if self.discovery_service is None:
            return "Unknown"
        try:
            sighting = self.discovery_service.get_beacon_sighting(device_id, max_age_seconds=30.0)
        except Exception:
            return "Unknown"
        return "Online" if sighting else "Offline"

    def _selected_member(self):
        """Returns the selected row's member dict from ``_members_cache``, or ``None``."""
        rows = self.members_table.selectionModel().selectedRows() if self.members_table.selectionModel() else []
        if not rows:
            return None
        idx = rows[0].row()
        cache = getattr(self, "_members_cache", [])
        return cache[idx] if 0 <= idx < len(cache) else None

    # ------------------------------------------------------------------
    # Sync Status panel (P3-8): pending outgoing, parked, conflicts, shadow checks.
    # ------------------------------------------------------------------

    def _refresh_sync_status(self):
        if not getattr(self, "sync_status_group", None) or not self.sync_status_group.isVisible() or self.db is None:
            return
        import sync_panel
        try:
            own_device_id, _ = self._own_identity()
            members = getattr(self, "_members_cache", []) or []
            mode = self.db.get_sync_mode()
            with self.db._connect() as master_conn, self.db._connect_raw() as raw_conn:
                own_master = sync_panel.own_vector(master_conn)
                own_raw = sync_panel.own_vector(raw_conn)
                conflicts = (
                    [dict(c, db="master") for c in sync_panel.list_conflicts(master_conn)]
                    + [dict(c, db="raw") for c in sync_panel.list_conflicts(raw_conn)]
                )
                conflicts.sort(key=lambda c: c.get("at") or "", reverse=True)
                members_waiting, total_pending = 0, 0
                for m in members:
                    dev_id = m.get("device_id")
                    if dev_id == own_device_id:
                        continue
                    n = (sync_panel.pending_outgoing(own_master, sync_panel.peer_vector(master_conn, dev_id))
                         + sync_panel.pending_outgoing(own_raw, sync_panel.peer_vector(raw_conn, dev_id)))
                    if n:
                        members_waiting += 1
                        total_pending += n

            # Read parked stats from replica in shadow mode, or live DBs in live mode (P3-8a)
            if mode == "shadow":
                import sync_capture
                import sync_shadow
                replica_master, replica_raw = sync_shadow.replica_paths(self._app_dir())
                rep_conns = []
                if replica_master.exists():
                    rep_conns.append(sync_capture._open(str(replica_master), self.db.hex_key, 5.0))
                if replica_raw.exists():
                    rep_conns.append(sync_capture._open(str(replica_raw), self.db.hex_key, 5.0))
                try:
                    parked, oldest_age = sync_panel.parked_summary(rep_conns)
                finally:
                    for rc in rep_conns:
                        rc.close()
            else:
                with self.db._connect() as master_conn, self.db._connect_raw() as raw_conn:
                    parked, oldest_age = sync_panel.parked_summary([master_conn, raw_conn])
        except Exception as exc:
            print(f"[Sera Sync] could not refresh sync status: {exc}")
            return

        # P3-8b fix (a): "Run now" only makes sense in mode shadow (see _on_run_shadow_check).
        # Left untouched while a check is already running so its own re-enable at the end wins.
        if hasattr(self, "btn_run_shadow_check") and not self._shadow_check_running:
            self.btn_run_shadow_check.setEnabled(mode == "shadow")

        # Stuck streams (P3-5 note: "Stuck streams from stalled_streams()") -- polled, like
        # peer_status(), rather than tracked from the "stalled" event.
        stalled = 0
        if self.sync_engine is not None:
            try:
                stalled = len(self.sync_engine.stalled_streams())
            except Exception:
                stalled = 0

        parked_str = f"{parked} (oldest: {sync_panel.format_age(oldest_age)})" if (parked > 0 and oldest_age is not None) else f"{parked}"
        summary = f"Pending outgoing: {total_pending} change(s) across {members_waiting} member(s)   •   Parked: {parked_str}"
        if stalled:
            summary += f"   •   Stalled: {stalled} stream(s)"
        self.sync_status_summary_label.setText(summary)

        # Parked age warning (P3-8a): amber > 1 hour in shadow mode, red > 7 days in any mode
        if hasattr(self, "sync_status_warning_label"):
            if oldest_age is not None and oldest_age >= 7 * 86400:
                self.sync_status_warning_label.setText(
                    f"Warning: Parked changes exist older than 7 days ({sync_panel.format_age(oldest_age)})."
                )
                self.sync_status_warning_label.setStyleSheet(
                    "QLabel { background-color: #300808; color: #FF8080; border: 1px solid #FF4D4D; "
                    "padding: 4px 8px; border-radius: 4px; font-size: 11px; }"
                )
                self.sync_status_warning_label.setVisible(True)
            elif mode == "shadow" and oldest_age is not None and oldest_age >= 3600:
                self.sync_status_warning_label.setText(
                    f"Warning: Parked changes exist older than 1 hour in shadow mode ({sync_panel.format_age(oldest_age)})."
                )
                self.sync_status_warning_label.setStyleSheet(
                    "QLabel { background-color: #332600; color: #FFD54F; border: 1px solid #FFA000; "
                    "padding: 4px 8px; border-radius: 4px; font-size: 11px; }"
                )
                self.sync_status_warning_label.setVisible(True)
            else:
                self.sync_status_warning_label.setVisible(False)

        # P3-8b fix (b): remember the selected conflict by (db, id), not row position -- the
        # table is rebuilt from scratch every 3 seconds, so a plain row-index restore could
        # re-select a different conflict if the list changed between selecting and clicking.
        prev = self._selected_conflict()
        prev_key = (prev["db"], prev["id"]) if prev is not None else None

        self._conflicts_cache = conflicts
        self.conflicts_table.setUpdatesEnabled(False)
        self.conflicts_table.setRowCount(len(conflicts))
        restore_row = None
        for row, c in enumerate(conflicts):
            # Every conflict sync_apply.py writes today has kept=None (the delete that won
            # left nothing) -- show that plainly instead of the literal text "None".
            kept_text = "(deleted)" if c["kept"] is None else str(c["kept"])
            self.conflicts_table.setItem(row, 0, QTableWidgetItem(c["table"]))
            self.conflicts_table.setItem(row, 1, QTableWidgetItem(c["column"]))
            self.conflicts_table.setItem(row, 2, QTableWidgetItem(kept_text))
            self.conflicts_table.setItem(row, 3, QTableWidgetItem(str(c["discarded"])))
            self.conflicts_table.setItem(row, 4, QTableWidgetItem(c["at"] or ""))
            if prev_key is not None and (c["db"], c["id"]) == prev_key:
                restore_row = row
        self.conflicts_table.setUpdatesEnabled(True)
        if restore_row is not None:
            self.conflicts_table.selectRow(restore_row)

        lines = sync_panel.shadow_check_summary(self._app_dir())
        self.shadow_status_label.setText("\n".join(lines) if lines else "Shadow checks: none logged yet")
        self._refresh_shadow_mode_row()

    # ------------------------------------------------------------------
    # Turning shadow mode on / reset (P3-7b).
    # ------------------------------------------------------------------

    def _refresh_shadow_mode_row(self):
        import sync_shadow
        try:
            st = sync_shadow.shadow_status(self.db, self._app_dir())
        except Exception as exc:
            self.shadow_mode_label.setText(f"Shadow mode: status unavailable ({exc})")
            self.btn_start_shadow.setEnabled(False)
            self.btn_reset_shadow.setEnabled(False)
            return
        busy = getattr(self, "_shadow_start_busy", False)
        if st["pending_start"]:
            text = "Shadow mode: starts when Sera restarts"
        elif st["mode"] == "shadow":
            since = (st["started_at"] or "")[:10] or "unknown date"
            day = st["day"]
            if day is None:
                text = f"Shadow mode: on (since {since})"
            elif day <= sync_shadow.SHADOW_WEEK_DAYS:
                text = f"Shadow mode: on since {since}, day {day} of {sync_shadow.SHADOW_WEEK_DAYS}"
            else:
                text = f"Shadow mode: on since {since}, day {day} (the 7-day week is complete; check the go-live criteria)"
        elif st["mode"] == "live":
            text = "Sync mode: live"
        else:
            text = "Shadow mode: off"
        self.shadow_mode_label.setText(text)
        self.btn_start_shadow.setEnabled(
            not busy and st["mode"] == "off" and not st["replica"] and not st["pending_start"])
        self.btn_reset_shadow.setEnabled(
            not busy and st["mode"] != "live" and not st["pending_start"]
            and (st["replica"] or st["started_at"] is not None))

    def _confirm_admin_pin(self) -> bool:
        """Starting and resetting shadow mode are admin-mode actions: ask for the PIN again."""
        from PySide6.QtWidgets import QDialog as _QDialog
        from ui.windows.admin_window import AdminPinDialog
        return AdminPinDialog(self.db, self).exec() == _QDialog.Accepted

    _SHADOW_WEEK_TEXT = (
        "Shadow mode is a 7-day trial of the new Sera Sync. Every edit is recorded and exchanged "
        "with the other PCs, but other PCs' changes go into a separate copy (the replica), never "
        "into the data staff work with. Sera checks the copies every 30 minutes.\n\n"
        "Going live needs 7 days in a row with: no capture-check mismatch, equal replicas on all "
        "PCs within 2 minutes of quiet, and no parked change older than 1 hour.")

    def _on_start_shadow_mode(self):
        if self.db is None:
            return
        import sync_shadow
        own_device_id, _ = self._own_identity()
        try:
            admin_id = sync_shadow.admin_device_id(self.db, self._app_dir())
        except Exception:
            admin_id = None
        is_admin = own_device_id is not None and own_device_id == admin_id
        if is_admin:
            text = (self._SHADOW_WEEK_TEXT + "\n\nThis is the admin PC: start shadow mode here first. Its data "
                    "is the starting point; then use \"Start shadow mode\" on every other PC.\n\nStart shadow mode now?")
        else:
            text = (self._SHADOW_WEEK_TEXT + "\n\nThis PC downloads the admin PC's data (shadow mode must "
                    "already be on there) and uses it from the next restart. This PC's current database is "
                    "kept in the shadow folder. What only this PC has is counted next, and after the restart "
                    "Sera offers to import it.\n\nDownload the admin PC's data now?")
        if QMessageBox.question(self, "Start Shadow Mode", text, QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No) != QMessageBox.Yes:
            return
        if not self._confirm_admin_pin():
            return
        if is_admin:
            try:
                sync_shadow.start_shadow_mode(self.db, self._app_dir())
            except Exception as exc:
                QMessageBox.warning(self, "Start Shadow Mode", f"Shadow mode was not started: {exc}")
                return
            self.shell_alert_or_status("Shadow mode started on the admin PC (day 1 of 7).")
            QMessageBox.information(self, "Start Shadow Mode",
                                    "Shadow mode is on. Now use \"Start shadow mode\" on every other PC.")
            self._refresh_sync_status()
            return

        if self.sync_engine is None:
            QMessageBox.warning(self, "Start Shadow Mode", "Sera Sync isn't running on this PC. Restart Sera and try again.")
            return
        self._shadow_start_busy = True
        self.btn_start_shadow.setEnabled(False)
        self.btn_reset_shadow.setEnabled(False)
        self.shell_alert_or_status("Downloading the admin PC's data for shadow mode...")
        db, app_dir, engine = self.db, self._app_dir(), self.sync_engine

        def _run():
            plan, error = None, ""
            try:
                session = sync_shadow.open_admin_session(engine, db, app_dir)
                try:
                    plan = sync_shadow.request_shadow_start(db, app_dir, session)
                finally:
                    session.close()
            except Exception as exc:
                error = str(exc) or type(exc).__name__
            # The dialog may have been closed meanwhile: then nobody can confirm, so the
            # download is dropped (nothing was staged, nothing live changed).
            delivered = False
            if not getattr(self, "_closed", False):
                try:
                    self.shadow_start_downloaded_signal.emit(plan, error)
                    delivered = True
                except RuntimeError:
                    pass
            if not delivered and plan is not None:
                sync_shadow.cancel_shadow_start(app_dir)

        threading.Thread(target=_run, name="shadow-start-download", daemon=True).start()

    @staticmethod
    def _shadow_start_count_text(report) -> str:
        r = report
        pk = r.pk_label or "PAN"
        lines = [
            "Found only on this PC (not in the admin PC's data):",
            f"  • {len(r.to_insert)} client(s)",
            f"  • {len(r.conflicts)} client(s) with different values here",
            f"  • {r.audit_new} audit log entries",
            f"  • {r.tracker_new} tracker filing(s), {r.timelines_new} session timeline(s)",
        ]
        if r.no_pk:
            lines.append(f"  • {len(r.no_pk)} client(s) without a {pk} (can't be imported)")
        if r.ambiguous:
            lines.append(f"  • {len(r.ambiguous)} client(s) sharing a {pk} (can't be imported)")
        lines += [
            "",
            "These are not in the shadow copy. After the restart Sera offers to import them, and "
            "the import then reaches every PC. If you don't import them, they stay only in this "
            "PC's previous database (kept in the shadow folder).",
            "",
            "Restart Sera now and start shadow mode?",
        ]
        return "\n".join(lines)

    def _on_shadow_start_downloaded(self, plan, error: str):
        import sync_shadow
        self._shadow_start_busy = False
        if plan is None:
            self.shell_alert_or_status(f"Shadow mode download failed: {error}")
            QMessageBox.warning(self, "Start Shadow Mode", f"Could not get the admin PC's data: {error}")
            self._refresh_sync_status()
            return
        reply = QMessageBox.question(self, "Start Shadow Mode", self._shadow_start_count_text(plan.report),
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            sync_shadow.cancel_shadow_start(self._app_dir())
            self.shell_alert_or_status("Shadow mode start cancelled; nothing changed.")
            self._refresh_sync_status()
            return
        try:
            sync_shadow.stage_shadow_start(self._app_dir(), plan)
        except Exception as exc:
            sync_shadow.cancel_shadow_start(self._app_dir())
            QMessageBox.warning(self, "Start Shadow Mode", f"Could not prepare the start: {exc}")
            self._refresh_sync_status()
            return
        try:
            if self.sync_service:
                self.sync_service.stop()
        except Exception:
            pass
        import version
        version.restart_app()

    def _on_reset_shadow_mode(self):
        if self.db is None:
            return
        reply = QMessageBox.question(
            self, "Reset Shadow Mode",
            "Reset shadow mode on this PC?\n\n"
            "Shadow mode goes off here and this PC's shadow copies are set aside (renamed, not "
            "deleted). The 7-day week starts over when shadow mode is started again.\n\n"
            "This PC can then start again from the admin PC's data; its own changes the admin PC "
            "hasn't received yet are kept. The admin PC itself can't be reset once it has "
            "received changes from other PCs (it is everyone's start point).",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        if not self._confirm_admin_pin():
            return
        import sync_shadow
        try:
            renamed = sync_shadow.reset_shadow_mode(self.db, self._app_dir())
        except Exception as exc:
            QMessageBox.warning(self, "Reset Shadow Mode", f"Shadow mode was not reset: {exc}")
            return
        self.shell_alert_or_status(f"Shadow mode reset ({len(renamed)} file(s) set aside).")
        self._refresh_sync_status()

    def _selected_conflict(self):
        """Returns the selected row's conflict dict from ``_conflicts_cache``, or ``None``."""
        rows = self.conflicts_table.selectionModel().selectedRows() if self.conflicts_table.selectionModel() else []
        if not rows:
            return None
        idx = rows[0].row()
        cache = getattr(self, "_conflicts_cache", [])
        return cache[idx] if 0 <= idx < len(cache) else None

    def _conflict_db_connect(self, which: str):
        return self.db._connect() if which == "master" else self.db._connect_raw()

    def _on_conflict_keep(self):
        conflict = self._selected_conflict()
        if conflict is None or self.db is None:
            return
        import sync_panel
        with self._conflict_db_connect(conflict["db"]) as conn:
            sync_panel.dismiss_conflict(conn, conflict["id"])
        self._refresh_sync_status()

    def _on_conflict_use_discarded(self):
        conflict = self._selected_conflict()
        if conflict is None or self.db is None:
            return
        import sync_panel
        with self._conflict_db_connect(conflict["db"]) as conn:
            applied = sync_panel.use_discarded_value(conn, conflict["id"])
        if not applied:
            if conflict.get("reason") in sync_panel.DELETED_ROW_REASONS:
                self.shell_alert_or_status(
                    "That edit was discarded because the row was deleted on another PC -- "
                    "it can't be restored from here yet."
                )
            else:
                self.shell_alert_or_status("Could not apply the discarded value (the row may be gone).")
        self._refresh_sync_status()

    def _on_run_shadow_check(self):
        """"Run now" (P3-8, on the "on demand from panel" trigger sync_shadow.run_shadow_checks
        expects): runs the capture check in a background thread (it copies/replays a scratch
        DB, not instant) and refreshes the panel when done.

        P3-8b fix (a): only meaningful in mode ``shadow``, same as the periodic 30-minute timer
        (main.py's ``_run_shadow_check_bg`` already checks ``get_sync_mode() != "shadow"``) --
        in mode ``off`` there's no baseline to compare against, and after go-live it would log
        false capture-check mismatches. A no-op here rather than an error message: the button
        is kept disabled outside mode shadow (see ``_refresh_sync_status``), so reaching this
        with the wrong mode means a stale click queued before the panel caught up.
        """
        if self.db is None or self.db.get_sync_mode() != "shadow":
            return
        self._shadow_check_running = True
        self.btn_run_shadow_check.setEnabled(False)
        app_dir = self._app_dir()
        db = self.db

        def _run():
            import sync_shadow
            try:
                sync_shadow.run_shadow_checks(db, app_dir)
            except Exception as exc:
                print(f"[Sera Sync] shadow check failed: {exc}")
            finally:
                # P3-8b fix (d): don't emit on a dialog that has been closed meanwhile (same
                # ``_closed`` flag P3-7b's download worker uses, set by closeEvent()/done()).
                if not getattr(self, "_closed", False):
                    self.shadow_check_finished_signal.emit()

        threading.Thread(target=_run, name="shadow-check-on-demand", daemon=True).start()

    def _on_shadow_check_finished(self):
        self._shadow_check_running = False
        self.btn_run_shadow_check.setEnabled(self.db is not None and self.db.get_sync_mode() == "shadow")
        self._refresh_sync_status()

    def _on_add_workstation(self):
        if self._add_workstation_session is not None:
            QMessageBox.information(self, "Add Workstation", "A pairing window is already open.")
            return
        own_device_id, own_cert_pem = self._own_identity()
        if own_device_id is None:
            QMessageBox.warning(self, "Add Workstation", "This PC's device identity could not be read.")
            return

        import sync_office
        session = sync_office.AddWorkstationSession(
            self._app_dir(), self._open_db_conn, own_device_id, own_cert_pem,
            on_joined=lambda record: self.workstation_joined_signal.emit(record),
            on_closed=lambda reason: self.workstation_closed_signal.emit(reason),
            transport=getattr(self.sync_engine, "transport", None),
        )
        try:
            session.start()
        except Exception as exc:
            QMessageBox.warning(self, "Add Workstation", f"Could not open the pairing window: {exc}")
            return
        self._add_workstation_session = session
        self.btn_add_workstation.setEnabled(False)
        if self.discovery_service is not None:
            try:
                import sera_keys
                office = sera_keys.load_office(self._app_dir())
                self.discovery_service.set_pairing(True, office.office_name if office else "", session.window.port)
            except Exception:
                pass

        dlg = QMessageBox(self)
        dlg.setWindowTitle("Add Workstation")
        dlg.setIcon(QMessageBox.Information)
        dlg.setText(
            f"Code for the new PC: {session.window.display_code}\n\n"
            "Enter it in \"Join an Existing Office\" on the new PC. This window closes itself "
            "after the PC joins, 3 wrong tries, or 5 minutes."
        )
        dlg.setStandardButtons(QMessageBox.Cancel)
        dlg.setDefaultButton(QMessageBox.Cancel)

        def _poll():
            if not session.window.is_open:
                dlg.accept()
                return
            stalls = session.window.stalled_connections
            blocked = [ip for ip, n in stalls.items() if n >= 3]
            if blocked:
                dlg.setText(dlg.text().split("\n\n")[0] + "\n\n"
                           f"Note: {', '.join(blocked)} keeps connecting without trying a code; "
                           "it may be blocking pairing.")
        poll_timer = QTimer(dlg)
        poll_timer.timeout.connect(_poll)
        poll_timer.start(1000)
        result = dlg.exec()
        poll_timer.stop()
        if result == QMessageBox.Cancel and session.window.is_open:
            session.close("cancelled")

    def _on_add_workstation_closed(self, reason: str):
        self._add_workstation_session = None
        if self.discovery_service is not None:
            try:
                self.discovery_service.set_pairing(False)
            except Exception:
                pass
        self._refresh_members()
        if reason == "too_many_attempts":
            self.shell_alert_or_status("Add workstation: closed after 3 wrong codes.")
        elif reason == "expired":
            self.shell_alert_or_status("Add workstation: the 5-minute window expired.")

    def shell_alert_or_status(self, message: str):
        # Best-effort: this dialog has no toast of its own; the activity log is close enough.
        from datetime import datetime
        self._add_log_item(datetime.now().strftime("%H:%M:%S"), "OFFICE", message)

    def _on_remove_member(self):
        member = self._selected_member()
        if member is None:
            QMessageBox.information(self, "Remove", "Select a workstation to remove first.")
            return
        own_device_id, _ = self._own_identity()
        if member.get("device_id") == own_device_id:
            QMessageBox.warning(self, "Remove", "The admin PC can't remove itself; hand over admin first.")
            return
        reply = QMessageBox.question(
            self, "Remove Workstation",
            f"Remove \"{member.get('name')}\" from the office?\n\n"
            "It will no longer be able to sync. This can't be undone from here.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            import sync_admin
            with self.db._connect() as conn:
                sync_admin.revoke_member(self._app_dir(), conn, own_device_id, member.get("device_id"))
        except Exception as exc:
            QMessageBox.warning(self, "Remove", f"Could not remove the workstation: {exc}")
            return
        self._refresh_members()

    def _on_hand_over_admin(self):
        member = self._selected_member()
        if member is None:
            QMessageBox.information(self, "Hand Over Admin", "Select the workstation to make admin.")
            return
        own_device_id, _ = self._own_identity()
        if member.get("device_id") == own_device_id:
            QMessageBox.information(self, "Hand Over Admin", "This PC is already the office admin.")
            return
        reply = QMessageBox.question(
            self, "Hand Over Admin",
            f"Make \"{member.get('name')}\" the office admin PC?\n\n"
            "This PC stops being able to add or remove workstations immediately. Until "
            f"\"{member.get('name')}\" also runs \"Become admin\" there with the office master "
            "password, no PC can add or remove workstations -- there is no automatic "
            "notification yet (that needs Phase 3's sync). Do that on the other PC right "
            "after confirming here.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            import sync_admin
            with self.db._connect() as conn:
                sync_admin.hand_over_admin(self._app_dir(), conn, own_device_id, member.get("device_id"))
                sync_admin.reconcile_admin_key(self._app_dir(), conn, own_device_id)
        except Exception as exc:
            QMessageBox.warning(self, "Hand Over Admin", f"Could not hand over admin: {exc}")
            return
        self._refresh_members()

    def _on_become_admin(self):
        pwd, ok = QInputDialog.getText(
            self, "Become Admin", "Office master password:", QLineEdit.Password)
        if not ok or not pwd:
            return
        own_device_id, _ = self._own_identity()
        if own_device_id is None:
            QMessageBox.warning(self, "Become Admin", "This PC's device identity could not be read.")
            return
        try:
            import sync_admin
            with self.db._connect() as conn:
                sync_admin.claim_admin(self._app_dir(), conn, pwd, own_device_id)
        except Exception as exc:
            QMessageBox.warning(self, "Become Admin", f"Could not become admin: {exc}")
            return
        self._refresh_members()

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
        if not hasattr(self, "_last_inv_state") or self._last_inv_state != is_local_inv:
            self._last_inv_state = is_local_inv
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
        
        # Check if text/status actually changed to avoid re-parsing CSS
        auth = sync_state.get("authority_host", "Remote Master")
        inv_nodes_str = ", ".join(sync_state.get("active_inv_frames_nodes", []))
        cache_key = f"{status}_{auth}_{inv_nodes_str}"
        
        if getattr(self, "_last_banner_state", None) == cache_key:
            return
        self._last_banner_state = cache_key

        if status == "INV_FRAMES_MASTER":
            self.protocol_banner.setText("🛡️ INV-FRAMES ACTIVE (Local Node is Master Authority — All incoming sync rejected, pushing to LAN)")
            self.protocol_banner.setStyleSheet(
                "QLabel { background-color: #2A2000; color: #F2C94C; border: 1px solid #F2C94C; "
                "padding: 6px 12px; border-radius: 6px; font-size: 12px; font-weight: 700; }"
            )
        elif status == "LAN_SYNC_FROZEN_MULTI_INV":
            self.protocol_banner.setText(f"⛔ LAN SYNC FROZEN — Multiple nodes ({inv_nodes_str}) have Inv-Frames enabled. All LAN sync is paused.")
            self.protocol_banner.setStyleSheet(
                "QLabel { background-color: #300808; color: #FF8080; border: 1px solid #FF4D4D; "
                "padding: 6px 12px; border-radius: 6px; font-size: 12px; font-weight: 700; }"
            )
        elif status == "INV_FRAMES_FOLLOWER":
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

    def _update_network_warning(self):
        if not self.sync_service or not hasattr(self.sync_service, "get_network_category"):
            self.network_warning_banner.setVisible(False)
            return
        result = self.sync_service.get_network_category()
        self.network_warning_banner.setVisible(bool(result.get("is_public")))

    def _load_existing_activity(self):
        if self.sync_service and hasattr(self.sync_service, "get_activity_history"):
            history = self.sync_service.get_activity_history()
            if history:
                self.log_list.setUpdatesEnabled(False)
                for entry in history:
                    ts = entry.get("timestamp", "")
                    cat = entry.get("category", "INFO")
                    title = entry.get("title", "")
                    detail = entry.get("detail", "")
                    msg = f"{title} - {detail}" if detail else title
                    self._add_log_item(ts, cat, msg, auto_scroll=False)
                self.log_list.scrollToBottom()
                self.log_list.setUpdatesEnabled(True)

    def _on_activity_received(self, timestamp: str, category: str, message: str):
        self._add_log_item(timestamp, category, message, auto_scroll=True)
        self._update_inv_frames_ui()

    def _add_log_item(self, ts: str, cat: str, message: str, auto_scroll: bool = True):
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
        
        # Auto-prune if too large to prevent UI lag over long periods
        if self.log_list.count() > 400:
            deleted_item = self.log_list.takeItem(0)
            del deleted_item
            
        if auto_scroll:
            self.log_list.scrollToBottom()

    def _clear_activity_log(self):
        self.log_list.clear()

    def _refresh_peers(self):
        if not self.sync_service:
            return
        peers = self.sync_service.get_peers()
        self.status_label.setText(f"🟢 {len(peers)} device{'s' if len(peers) != 1 else ''} online")
        self._update_inv_frames_ui()
        self._update_network_warning()
        self._refresh_members()

        # Preserve selection
        selected_key = None
        sel_row = self.table.currentRow()
        if sel_row >= 0:
            host_item = self.table.item(sel_row, 1)
            ip_item = self.table.item(sel_row, 2)
            if host_item and ip_item:
                selected_key = f"{host_item.text()}:{ip_item.text()}"

        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(peers))
        new_sel_row = -1

        for r_idx, peer in enumerate(peers):
            key = f"{peer['host']}:{peer['ip']}"
            if key == selected_key:
                new_sel_row = r_idx

            # 0: Username
            item0 = self.table.item(r_idx, 0) or QTableWidgetItem()
            item0.setText(peer.get("username", "Unknown"))
            item0.setData(Qt.UserRole, peer)
            if not self.table.item(r_idx, 0): self.table.setItem(r_idx, 0, item0)

            # 1: Host
            item1 = self.table.item(r_idx, 1) or QTableWidgetItem()
            item1.setText(peer.get("host", ""))
            if not self.table.item(r_idx, 1): self.table.setItem(r_idx, 1, item1)

            # 2: IP
            item2 = self.table.item(r_idx, 2) or QTableWidgetItem()
            item2.setText(peer.get("ip", ""))
            if not self.table.item(r_idx, 2): self.table.setItem(r_idx, 2, item2)

            # 3: Version
            item3 = self.table.item(r_idx, 3) or QTableWidgetItem()
            item3.setText(peer.get("app_version", "v2.4.0"))
            if not self.table.item(r_idx, 3): self.table.setItem(r_idx, 3, item3)

            # 4: DB MTime
            item4 = self.table.item(r_idx, 4) or QTableWidgetItem()
            item4.setText(peer.get("db_mtime", "N/A"))
            if not self.table.item(r_idx, 4): self.table.setItem(r_idx, 4, item4)

            # 5: Revision
            item5 = self.table.item(r_idx, 5) or QTableWidgetItem()
            item5.setText(str(peer.get("sync_revision", 0)))
            item5.setTextAlignment(Qt.AlignCenter)
            item5.setForeground(QColor("#38D9A9"))
            if not self.table.item(r_idx, 5): self.table.setItem(r_idx, 5, item5)

            # 6: Data Counts
            c_cnt = peer.get("client_count", 0)
            t_cnt = peer.get("tracker_count", 0)
            item6 = self.table.item(r_idx, 6) or QTableWidgetItem()
            item6.setText(f"{c_cnt} CLI | {t_cnt} Dumps")
            item6.setTextAlignment(Qt.AlignCenter)
            item6.setForeground(QColor("#7DD3FC"))
            if not self.table.item(r_idx, 6): self.table.setItem(r_idx, 6, item6)

            # 7: Status
            item7 = self.table.item(r_idx, 7) or QTableWidgetItem()
            local_key_id = getattr(self.sync_service, "key_id", None)
            peer_key_id = peer.get("key_id")
            if local_key_id != peer_key_id:
                item7.setText("different office key — rejoin needed")
                item7.setForeground(QColor("#FFA657"))
            elif peer.get("inv_frames", False):
                item7.setText("🛡️ Inv-Frames")
                item7.setForeground(QColor("#F2C94C"))
            else:
                item7.setText("🟢 Normal")
                item7.setForeground(QColor("#4CF9B7"))
            if not self.table.item(r_idx, 7): self.table.setItem(r_idx, 7, item7)

        if new_sel_row >= 0:
            self.table.selectRow(new_sel_row)
        self.table.setUpdatesEnabled(True)

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

        local_key_id = getattr(self.sync_service, "key_id", None)
        peer_key_id = peer_data.get("key_id")
        if local_key_id != peer_key_id:
            QMessageBox.warning(
                self, "Different Office Key",
                f"Workstation {peer_username} ({peer_host}) has a different office key — rejoin needed.\n\n"
                f"Database exchange between this workstation and {peer_host} is blocked."
            )
            return

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

        local_key_id = getattr(self.sync_service, "key_id", None)
        valid_peers = [p for p in peers if p.get("key_id") == local_key_id]
        if not valid_peers:
            QMessageBox.warning(
                self, "Sync Blocked",
                "No online workstations share this office's key.\n\n"
                "Workstations with different office keys or in legacy mode require a rejoin."
            )
            return

        peer_names = ", ".join([f"{p.get('username')} ({p.get('host')})" for p in valid_peers])
        confirm = QMessageBox.warning(
            self, "Confirm Bulk Database Sync",
            f"You are about to push your database to ALL {len(valid_peers)} compatible online device(s):\n\n"
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
            results = self.sync_service.push_to_all(valid_peers)
            successes = []
            failures = []

            for peer in valid_peers:
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

    def _on_add_pc_by_ip(self):
        """
        P0-10: 'Add PC by IP' in the Sera Sync dialog stores addresses in the setting
        'sync_manual_peers' (JSON list). It is office-wide like every setting (D7).
        """
        import ipaddress

        text, ok = QInputDialog.getText(
            self,
            "Add PC by IP",
            "Enter workstation IP address (e.g. 192.168.1.50):",
        )
        if not ok or not text:
            return
        addr = text.strip()
        if not addr:
            return

        # Validate IP and optional port (1-65535)
        if ":" in addr:
            parts = addr.rsplit(":", 1)
            ip_part = parts[0].strip()
            port_part = parts[1].strip()
            if not port_part.isdigit() or not (1 <= int(port_part) <= 65535):
                QMessageBox.warning(
                    self,
                    "Invalid Port",
                    f"Port '{port_part}' is invalid.\nPort must be a number between 1 and 65535.",
                )
                return
        else:
            ip_part = addr

        try:
            ipaddress.IPv4Address(ip_part)
        except ValueError:
            QMessageBox.warning(
                self,
                "Invalid IP Address",
                f"'{ip_part}' is not a valid IPv4 address.\nPlease enter a valid IPv4 address.",
            )
            return

        # Load existing manual peers list
        existing = self._get_manual_peers_list()

        if addr in existing:
            QMessageBox.information(
                self,
                "Already Added",
                f"Workstation address '{addr}' is already in the manual peer list.",
            )
            return

        existing.append(addr)
        if self.db and hasattr(self.db, "set_setting"):
            try:
                self.db.set_setting("sync_manual_peers", json.dumps(existing))
            except Exception as e:
                print(f"[SeraSyncDialog] Failed to save sync_manual_peers: {e}")

        if self.sync_service:
            if hasattr(self.sync_service, "add_manual_peer"):
                self.sync_service.add_manual_peer(addr)
            if hasattr(self.sync_service, "send_manual_beacons"):
                threading.Thread(target=self.sync_service.send_manual_beacons, daemon=True).start()
            if hasattr(self.sync_service, "log_activity"):
                self.sync_service.log_activity("NETWORK", f"Added manual PC: {addr}", "Probing via unicast beacon")

        QMessageBox.information(
            self,
            "Workstation Added",
            f"Address '{addr}' added to office manual peer list.\nSera Sync will probe this workstation periodically.",
        )

    def _get_manual_peers_list(self) -> list[str]:
        """Loads and returns the list of configured manual peer addresses."""
        existing = []
        if self.db and hasattr(self.db, "get_setting"):
            try:
                raw = self.db.get_setting("sync_manual_peers", "[]")
                if raw:
                    loaded = json.loads(raw)
                    if isinstance(loaded, list):
                        existing = [str(x).strip() for x in loaded if str(x).strip()]
            except Exception:
                existing = []
        elif self.sync_service and hasattr(self.sync_service, "get_manual_peers"):
            existing = self.sync_service.get_manual_peers()
        return existing

    def _remove_manual_peer_address(self, addr: str):
        """Removes a manual peer address from DB, sync_service, and UI."""
        addr = str(addr).strip()
        if not addr:
            return

        # 1. Update DB setting (exact entry only; see SyncPeerService.remove_manual_peer)
        if self.db and hasattr(self.db, "get_setting") and hasattr(self.db, "set_setting"):
            try:
                raw = self.db.get_setting("sync_manual_peers", "[]")
                if raw:
                    loaded = json.loads(raw)
                    if isinstance(loaded, list):
                        new_peers = [str(x).strip() for x in loaded if str(x).strip() != addr]
                        self.db.set_setting("sync_manual_peers", json.dumps(new_peers))
            except Exception as e:
                print(f"[SeraSyncDialog] Failed to remove manual peer from settings: {e}")

        # 2. Update service
        if self.sync_service:
            if hasattr(self.sync_service, "remove_manual_peer"):
                self.sync_service.remove_manual_peer(addr)
            if hasattr(self.sync_service, "log_activity"):
                self.sync_service.log_activity("NETWORK", f"Removed manual PC: {addr}", "Removed from office manual peer list")

        # 3. Refresh table
        self._refresh_peers()

    def _on_remove_pc_by_ip(self):
        """
        Removes a workstation address from the office-wide setting 'sync_manual_peers'.
        """
        existing = self._get_manual_peers_list()
        if not existing:
            QMessageBox.information(
                self,
                "Remove PC by IP",
                "No manual workstation addresses are currently configured.",
            )
            return

        # Determine default selection based on current table selection
        default_idx = 0
        sel_row = self.table.currentRow()
        if sel_row >= 0:
            ip_item = self.table.item(sel_row, 2)
            if ip_item:
                selected_ip = ip_item.text().strip()
                for idx, entry in enumerate(existing):
                    entry_ip = entry.split(":")[0].strip() if ":" in entry else entry
                    if entry == selected_ip or entry_ip == selected_ip:
                        default_idx = idx
                        break

        item, ok = QInputDialog.getItem(
            self,
            "Remove PC by IP",
            "Select workstation address to remove:",
            existing,
            default_idx,
            False,
        )
        if not ok or not item:
            return

        addr = item.strip()
        reply = QMessageBox.question(
            self,
            "Confirm Removal",
            f"Remove workstation address '{addr}' from office manual peer list?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self._remove_manual_peer_address(addr)

        QMessageBox.information(
            self,
            "Workstation Removed",
            f"Address '{addr}' removed from the manual peer list.\n"
            "Sera Sync will stop contacting this address from this PC. The PC may still appear "
            "if it is found by broadcast or still lists this PC. Other PCs keep the address "
            "until they next receive this database.",
        )

    def _on_table_context_menu(self, pos):
        """Provides right-click context menu options on the peer table."""
        item = self.table.itemAt(pos)
        if not item:
            return
        row = item.row()
        ip_item = self.table.item(row, 2)
        if not ip_item:
            return
        peer_ip = ip_item.text().strip()
        host_item = self.table.item(row, 1)
        peer_host = host_item.text().strip() if host_item else peer_ip

        menu = QMenu(self)

        # Check if this peer is in manual peers
        manual_peers = self._get_manual_peers_list()
        matching_addr = None
        for m in manual_peers:
            m_ip = m.split(":")[0].strip() if ":" in m else m
            if m == peer_ip or m_ip == peer_ip:
                matching_addr = m
                break

        if matching_addr:
            icon = _safe_icon("mdi.minus-network", color="#FF5252")
            act_remove = menu.addAction(f"Remove '{matching_addr}' from Manual Peers")
            if icon:
                act_remove.setIcon(icon)

            chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
            if chosen == act_remove:
                reply = QMessageBox.question(
                    self,
                    "Confirm Removal",
                    f"Remove workstation '{peer_host}' ({matching_addr}) from office manual peer list?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if reply == QMessageBox.Yes:
                    self._remove_manual_peer_address(matching_addr)
                    QMessageBox.information(
                        self,
                        "Workstation Removed",
                        f"Address '{matching_addr}' removed from the manual peer list.\n"
                        "Sera Sync will stop contacting this address from this PC. The PC may still appear "
                        "if it is found by broadcast or still lists this PC. Other PCs keep the address "
                        "until they next receive this database.",
                    )
        else:
            act_info = menu.addAction(f"Discovered via LAN broadcast ({peer_ip})")
            act_info.setEnabled(False)
            menu.exec(self.table.viewport().mapToGlobal(pos))

    def closeEvent(self, event):
        self._closed = True
        self._refresh_timer.stop()
        super().closeEvent(event)

    def done(self, result):
        # exec() dialogs closed with Esc/Close go through done(), not always closeEvent().
        self._closed = True
        super().done(result)
