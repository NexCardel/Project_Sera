import json
import threading
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTableWidgetItem,
    QAbstractItemView,
    QMessageBox,
    QInputDialog,
    QMenu,
    QLineEdit,
    QStackedWidget,
    QWidget,
    QScrollArea,
    QFrame,
)

from ui.components.kit import (
    ROLE_PILL,
    Banner,
    Card,
    DarkListTable,
    DialogFrame,
    EmptyState,
    LabelledAction,
    LogView,
    Segmented,
    StatusStrip,
    ask_confirm,
    show_message,
)
from ui.utils.tokens import KIT, TOKENS

_ACTIVITY_MIN_WIDTH = 290
_ACTIVITY_MAX_WIDTH = 380
_ACTIVITY_MAX_LINES = 400  # oldest lines drop off so a long session does not slow the list

# (header, DataGrid.set_column spec). Narrow windows hide DB Modified, then Version, then
# Clients / Dumps; the row tooltip keeps every fact.
_DEVICE_COLUMNS = (
    ("Username", dict(minimum=64, stretch=1)),
    ("Hostname", dict(kind="strong", minimum=96, stretch=2)),
    ("IP Address", dict(kind="mono", width=124, minimum=124, stretch=0)),
    ("App Version", dict(kind="num", width=76, minimum=64, stretch=0, priority=2)),
    ("DB Modified", dict(kind="num", minimum=128, stretch=1, priority=3)),
    ("Clients / Dumps", dict(kind="num", minimum=132, stretch=1, priority=1)),
    ("Mode / Status", dict(minimum=110, stretch=2)),
)
_MEMBER_COLUMNS = (
    ("Name", dict(kind="strong", minimum=96, stretch=2)),
    ("Online", dict(width=150, stretch=0)),
    ("Role", dict(width=80, stretch=0)),
    ("Last Successful Sync (this PC)", dict(kind="num", minimum=120, stretch=1)),
    ("This PC", dict(width=84, stretch=0)),
)
_CONFLICT_COLUMNS = (
    ("Table", dict(minimum=70, stretch=1)),
    ("Column", dict(minimum=80, stretch=1)),
    ("Kept", dict(kind="mono", minimum=90, stretch=2)),
    ("Discarded", dict(kind="mono", minimum=90, stretch=2)),
    ("When", dict(kind="num", minimum=120, stretch=1)),
)

# Activity category word -> LogView tag colour; the first match wins (the old emoji badges,
# same order).
_ACTIVITY_TONES = (
    ("INV_FRAMES", "amber"),
    ("REVISION", "emerald"),
    ("BEACON", "blue"),
    ("GUARD", "amber"),
    ("PULL", "blue"),
    ("PUSH", "purple"),
    ("SDIS", "purple"),
    ("SSAL", "blue"),
    ("SYNC IN", "emerald"),
    ("ACCEPTED", "emerald"),
)


class SeraSyncDialog(QDialog):
    """
    Admin-only dialog showing Sera instances on the local network along with
    a live plain-text activity log sidebar tracking all P2P discovery, push, pull,
    and Sync Guard events.
    """
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
        # Office mode shows three panels on the left; start large enough for them (capped to the
        # screen -- the left side scrolls if it still doesn't fit).
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen is not None:
            self.resize(min(1280, int(screen.width() * 0.9)), min(900, int(screen.height() * 0.9)))

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
        # Fleet layout (S-12): dialog frame, network banner and status strip on top, the three
        # tabs (Devices / Office members / Sync status) on the left and the Live activity card
        # on the right. Office mode opens on Sync status (the old dialog put the office panels
        # first); legacy mode has only the devices, so the tab switch is hidden there.
        office_mode = self.sync_service is not None and getattr(self.sync_service, "key_id", None) is not None
        self.setObjectName("ToolDialog")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.frame = DialogFrame(
            "Sera Sync", "Keeps the client database the same on every Sera PC on this network",
            self, icon="mdi.sync", full=True)
        outer.addWidget(self.frame)
        self.frame.closeRequested.connect(self.reject)
        self.frame.add_close_button()

        # Public-network warning (P0-9b). Hidden unless the active network is Public.
        self.network_warning_banner = Banner(
            "This PC's network is set to Public. Sera Sync is reachable by other devices on it. "
            "For security, set it to Private: Windows Settings → Network → set this network to Private.",
            "w")
        self.network_warning_banner.setVisible(False)
        self.frame.add_widget(self.network_warning_banner)
        self._update_network_warning()

        self.frame.add_widget(self._build_status_strip(office_mode))

        body = QWidget()
        body_row = QHBoxLayout(body)
        body_row.setContentsMargins(0, 0, 0, 0)
        body_row.setSpacing(TOKENS["s_3"])
        self.frame.add_widget(body, 1)

        left = QWidget()
        left_col = QVBoxLayout(left)
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(TOKENS["s_3"])
        self.tabs = Segmented(["Devices", "Office members", "Sync status"])
        self.tabs.setVisible(office_mode)
        left_col.addWidget(self.tabs, 0, Qt.AlignLeft)
        self.tab_stack = QStackedWidget()
        # Each tab scrolls on its own when taller than the window, so the tallest tab (Sync
        # status) does not stretch the others.
        for page in (self._build_devices_tab(), self._build_members_tab(office_mode),
                     self._build_sync_status_tab(office_mode)):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setWidget(page)
            self.tab_stack.addWidget(scroll)
        self.tabs.currentChanged.connect(self.tab_stack.setCurrentIndex)
        self.tabs.set_current(2 if office_mode else 0)
        left_col.addWidget(self.tab_stack, 1)
        body_row.addWidget(left, 2)

        # Live activity (right): the stream and Clear, unchanged.
        activity = Card("Live activity")
        activity.setMinimumWidth(_ACTIVITY_MIN_WIDTH)
        activity.setMaximumWidth(_ACTIVITY_MAX_WIDTH)
        btn_clear_log = LabelledAction("Clear", variant="ghost", icon="mdi.delete-outline",
                                       tooltip="Empty the list shown here")
        btn_clear_log.clicked.connect(self._clear_activity_log)
        activity.add_action(btn_clear_log)
        log_info = QLabel("Real-time P2P sync events, revision scores and guard alerts")
        log_info.setProperty("class", "HintText")
        log_info.setWordWrap(True)
        activity.add_widget(log_info)
        self.log_list = LogView(max_lines=_ACTIVITY_MAX_LINES)
        activity.add_widget(self.log_list, 1)
        body_row.addWidget(activity, 1)

        self.btn_close = LabelledAction("Close", icon="mdi.close")
        self.btn_close.clicked.connect(self.accept)
        self.frame.add_action(self.btn_close)

    def _build_status_strip(self, office_mode):
        """Sync on / off, PCs online (the old "N devices online" label), this PC's name and
        version and, in office mode, the sync mode."""
        import socket
        import version
        strip = StatusStrip()
        on = self.sync_service is not None
        self.sync_pill = strip.add_pill("Sync on" if on else "Sync off", "emerald" if on else "neutral", dot=True)
        self.status_label = strip.add_text(
            "0 devices online" if on else "Sera Sync is off on this PC", bold=True)
        strip.add_separator()
        strip.add_text("This PC:", muted=True)
        strip.add_text(socket.gethostname(), bold=True)
        strip.add_text(f"· v{version.APP_VERSION}", muted=True)
        mode_sep = strip.add_separator()
        self.mode_pill = strip.add_pill("Mode: off")
        mode_sep.setVisible(office_mode)
        self.mode_pill.setVisible(office_mode)
        strip.add_stretch()
        return strip

    def _build_devices_tab(self):
        # Discovered LAN devices (beacon discovery; P4-1 removed the legacy whole-database
        # push/pull buttons that used to live here -- Sera Sync v3 exchanges changes by itself
        # once workstations are paired as office members).
        self.devices_group = QWidget()
        lay = QVBoxLayout(self.devices_group)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(TOKENS["s_3"])

        top = QHBoxLayout()
        top.setSpacing(TOKENS["s_3"])
        desc = QLabel("Devices running Sera on your local network. Right-click a device to remove a manual address.")
        desc.setProperty("class", "HintText")
        desc.setWordWrap(True)
        top.addWidget(desc, 1)
        self.btn_refresh = LabelledAction("Scan again", icon="mdi.refresh",
                                          tooltip="Read the list of Sera PCs on the network again (was: Refresh)")
        self.btn_refresh.clicked.connect(self._refresh_peers)
        top.addWidget(self.btn_refresh)
        lay.addLayout(top)

        # One row per device with all seven facts (Username, Hostname, IP, Version, DB Modified,
        # Clients / Dumps, Mode / Status); the row tooltip repeats every fact, so a column that
        # is elided or hidden on a narrow window loses nothing.
        card = Card("Discovered LAN devices")
        self.table = DarkListTable(0, len(_DEVICE_COLUMNS))
        self.table.setHorizontalHeaderLabels([c[0] for c in _DEVICE_COLUMNS])
        for col, (_, spec) in enumerate(_DEVICE_COLUMNS):
            self.table.set_column(col, **spec)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        # Columns fit the width (elided cells keep their text in the tooltip), never scroll sideways.
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)
        self.table.setMinimumHeight(100)
        self.table.setVisible(False)
        card.add_widget(self.table, 1)
        self.devices_empty = EmptyState(
            "No other Sera PC found yet",
            "Scan again in a moment, or add the PC by its IP address.", icon="mdi.lan-disconnect")
        self.btn_empty_add_ip = LabelledAction("Add PC by IP", variant="soft", icon="mdi.plus-network",
                                               tooltip="Add a PC this one cannot find by broadcast")
        self.btn_empty_add_ip.clicked.connect(self._on_add_pc_by_ip)
        self.devices_empty.add_action(self.btn_empty_add_ip)
        card.add_widget(self.devices_empty, 1)
        lay.addWidget(card, 1)

        lay.addWidget(Banner(
            "Not seeing a PC? It must be on the same network, running Sera, with Sera Sync switched on.", "i"))

        # Addresses and office-key actions.
        btn_row = QHBoxLayout()
        btn_row.setSpacing(TOKENS["s_2"])
        self.btn_add_ip = LabelledAction("Add PC by IP", icon="mdi.plus-network",
                                         tooltip="Add a PC this one cannot find by broadcast")
        self.btn_add_ip.clicked.connect(self._on_add_pc_by_ip)
        btn_row.addWidget(self.btn_add_ip)
        self.btn_remove_ip = LabelledAction("Remove PC by IP", icon="mdi.minus-network",
                                            tooltip="Stop contacting a manually added address (asks first)")
        self.btn_remove_ip.clicked.connect(self._on_remove_pc_by_ip)
        btn_row.addWidget(self.btn_remove_ip)
        btn_row.addStretch()

        self.btn_rejoin = LabelledAction("Rejoin office", icon="mdi.database-import",
                                         tooltip="Sera restarts and pairs with the admin PC (asks first)")
        self.btn_rejoin.clicked.connect(self._on_rejoin_office)
        # P2-8: every legacy-mode PC except the admin PC rejoins instead of converting.
        self.btn_rejoin.setVisible(
            self.sync_service is not None and getattr(self.sync_service, "key_id", None) is None
        )
        btn_row.addWidget(self.btn_rejoin)

        self.btn_export_kit = LabelledAction("Export recovery kit", icon="mdi.file-key-outline")
        self.btn_export_kit.clicked.connect(self._on_export_recovery_kit)
        self.btn_export_kit.setVisible(
            self.sync_service is not None and getattr(self.sync_service, "key_id", None) is not None
        )
        btn_row.addWidget(self.btn_export_kit)
        lay.addLayout(btn_row)
        return self.devices_group

    def _build_members_tab(self, office_mode):
        # Office Members panel (P2-7): members, Add workstation, Remove, Hand over/Become admin,
        # network warnings. Office mode only (legacy-mode PCs have no admin key / member records).
        # The group sits inside its tab page, so it can be off (legacy mode) apart from the
        # page being hidden by another tab (see _panel_on).
        page = QWidget()
        page_lay = QVBoxLayout(page)
        page_lay.setContentsMargins(0, 0, 0, 0)
        self.members_group = QWidget(page)
        lay = QVBoxLayout(self.members_group)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(TOKENS["s_3"])

        self.members_network_warning = Banner("", "r")
        self.members_network_warning.setVisible(False)
        lay.addWidget(self.members_network_warning)

        card = Card("Office members")
        self.members_table = DarkListTable(0, len(_MEMBER_COLUMNS))
        self.members_table.setHorizontalHeaderLabels([c[0] for c in _MEMBER_COLUMNS])
        for col, (_, spec) in enumerate(_MEMBER_COLUMNS):
            self.members_table.set_column(col, **spec)
        self.members_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.members_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.members_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.members_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.members_table.setMinimumHeight(110)
        card.add_widget(self.members_table, 1)
        lay.addWidget(card, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(TOKENS["s_2"])
        self.btn_add_workstation = LabelledAction("Add workstation", variant="primary", icon="mdi.laptop",
                                                  tooltip="Shows a code the new PC types in to join this office")
        self.btn_add_workstation.clicked.connect(self._on_add_workstation)
        btn_row.addWidget(self.btn_add_workstation)
        self.btn_remove_member = LabelledAction("Remove", icon="mdi.account-remove", tooltip="Asks first")
        self.btn_remove_member.clicked.connect(self._on_remove_member)
        btn_row.addWidget(self.btn_remove_member)
        self.btn_hand_over_admin = LabelledAction("Hand over admin", icon="mdi.account-arrow-right",
                                                  tooltip="Asks first; the other PC then runs Become admin")
        self.btn_hand_over_admin.clicked.connect(self._on_hand_over_admin)
        btn_row.addWidget(self.btn_hand_over_admin)
        self.btn_become_admin = LabelledAction("Become admin", icon="mdi.shield-account",
                                               tooltip="Asks for the office master password")
        self.btn_become_admin.clicked.connect(self._on_become_admin)
        btn_row.addWidget(self.btn_become_admin)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        # Office mode only: key_id is set once office.json exists (legacy PCs have none).
        self.members_group.setVisible(office_mode)
        page_lay.addWidget(self.members_group, 1)
        return page

    def _build_sync_status_tab(self, office_mode):
        # Sync Status panel (P3-8): pending outgoing count, parked changes, conflicts (with
        # resolution actions) and the shadow-check summary. Office mode only, like Members.
        page = QWidget()
        page_lay = QVBoxLayout(page)
        page_lay.setContentsMargins(0, 0, 0, 0)
        self.sync_status_group = QWidget(page)
        lay = QVBoxLayout(self.sync_status_group)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(TOKENS["s_3"])

        summary = Card("Sync status")
        self.sync_status_summary_label = QLabel("Pending outgoing: 0   •   Parked: 0")
        self.sync_status_summary_label.setWordWrap(True)
        summary.add_widget(self.sync_status_summary_label)
        self.sync_status_warning_label = Banner("", "w")
        self.sync_status_warning_label.setVisible(False)
        summary.add_widget(self.sync_status_warning_label)
        lay.addWidget(summary)

        # Turning shadow mode on / resetting it (P3-7b). Admin mode (PIN) is asked for again.
        shadow = Card("Shadow mode")
        self.shadow_mode_label = QLabel("Shadow mode: off")
        self.shadow_mode_label.setWordWrap(True)
        shadow.add_widget(self.shadow_mode_label)
        shadow_btns = QWidget()
        shadow_btns.setProperty("class", "FormHost")  # transparent inside the card (theme rule)
        shadow_row = QHBoxLayout(shadow_btns)
        shadow_row.setContentsMargins(0, 0, 0, 0)
        shadow_row.setSpacing(TOKENS["s_2"])
        self.btn_start_shadow = LabelledAction(
            "Start shadow mode", icon="mdi.play-circle-outline",
            tooltip="Start the 7-day shadow week on this PC (admin PC first, then every other PC).")
        self.btn_start_shadow.clicked.connect(self._on_start_shadow_mode)
        shadow_row.addWidget(self.btn_start_shadow)
        self.btn_reset_shadow = LabelledAction(
            "Reset shadow mode", icon="mdi.restore",
            tooltip="Set shadow mode off on this PC and set its shadow files aside (the week starts over).")
        self.btn_reset_shadow.clicked.connect(self._on_reset_shadow_mode)
        shadow_row.addWidget(self.btn_reset_shadow)
        # Going live (P3-9): per PC, admin mode (PIN), when the owner decides the week passed.
        self.btn_go_live = LabelledAction(
            "Go live", variant="soft", icon="mdi.rocket-launch-outline",
            tooltip="Make this PC's shadow copy its live data and switch Sera Sync to live (after a restart).")
        self.btn_go_live.clicked.connect(self._on_go_live)
        shadow_row.addWidget(self.btn_go_live)
        shadow_row.addStretch()
        self.btn_run_shadow_check = LabelledAction(
            "Run now", icon="mdi.shield-check",
            tooltip="Run the shadow-mode capture check now (also runs every 30 minutes).")
        self.btn_run_shadow_check.clicked.connect(self._on_run_shadow_check)
        shadow_row.addWidget(self.btn_run_shadow_check)
        shadow.add_widget(shadow_btns)
        self.shadow_status_label = QLabel("Shadow checks: none logged yet")
        self.shadow_status_label.setProperty("class", "HintText")
        self.shadow_status_label.setWordWrap(True)
        shadow.add_widget(self.shadow_status_label)
        lay.addWidget(shadow)

        conflicts = Card("Conflicts")
        conflicts_label = QLabel("Field edits an older change lost to:")
        conflicts_label.setProperty("class", "HintText")
        conflicts.add_widget(conflicts_label)
        self.conflicts_table = DarkListTable(0, len(_CONFLICT_COLUMNS))
        self.conflicts_table.setHorizontalHeaderLabels([c[0] for c in _CONFLICT_COLUMNS])
        for col, (_, spec) in enumerate(_CONFLICT_COLUMNS):
            self.conflicts_table.set_column(col, **spec)
        self.conflicts_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.conflicts_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.conflicts_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.conflicts_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.conflicts_table.setMinimumHeight(100)
        conflicts.add_widget(self.conflicts_table, 1)
        conflict_btns = QWidget()
        conflict_btns.setProperty("class", "FormHost")
        conflicts_btn_row = QHBoxLayout(conflict_btns)
        conflicts_btn_row.setContentsMargins(0, 0, 0, 0)
        conflicts_btn_row.setSpacing(TOKENS["s_2"])
        self.btn_conflict_keep = LabelledAction("Keep", icon="mdi.check",
                                                tooltip="Acknowledge the row already holds the value that won.")
        self.btn_conflict_keep.clicked.connect(self._on_conflict_keep)
        conflicts_btn_row.addWidget(self.btn_conflict_keep)
        self.btn_conflict_use_discarded = LabelledAction("Use discarded value", icon="mdi.undo",
                                                         tooltip="Write the discarded value back, as a normal edit.")
        self.btn_conflict_use_discarded.clicked.connect(self._on_conflict_use_discarded)
        conflicts_btn_row.addWidget(self.btn_conflict_use_discarded)
        conflicts_btn_row.addStretch()
        conflicts.add_widget(conflict_btns)
        lay.addWidget(conflicts, 1)

        self.sync_status_group.setVisible(office_mode)
        page_lay.addWidget(self.sync_status_group, 1)
        return page

    @staticmethod
    def _panel_on(group):
        """The Members / Sync Status refresh gate. The panels now sit on tabs, so a panel on a
        tab that is not showing reads isVisible() False although the PC is in office mode;
        visible-to-its-tab-page is False only when the panel is switched off (legacy mode)."""
        return group.isVisible() or group.isVisibleTo(group.parentWidget())

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
        if not getattr(self, "members_group", None) or not self._panel_on(self.members_group) or self.db is None:
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
            online = self._online_status(dev_id, own_device_id)
            online_item = QTableWidgetItem(online)
            online_item.setData(ROLE_PILL, (online, "emerald" if online.startswith("Online") else "neutral", True))
            self.members_table.setItem(row, 1, online_item)
            role = "Admin" if dev_id == admin_device_id else "Member"
            self.members_table.setItem(row, 2, QTableWidgetItem(role))
            self.members_table.setItem(row, 3, QTableWidgetItem(last_ok.get(dev_id) or "never"))
            this_pc_item = QTableWidgetItem("This PC" if dev_id == own_device_id else "")
            if dev_id == own_device_id:
                this_pc_item.setData(ROLE_PILL, ("This PC", "neutral"))
            self.members_table.setItem(row, 4, this_pc_item)
        self.members_table.setUpdatesEnabled(True)

        if warnings:
            self.members_network_warning.set_text("\n".join(warnings.values()))
            self.members_network_warning.setVisible(True)
        else:
            self.members_network_warning.setVisible(False)

        is_admin = own_device_id is not None and own_device_id == admin_device_id
        not_admin = None if is_admin else "Only the office's admin PC can do this"
        self.btn_add_workstation.set_disabled_reason(
            not_admin or ("A code window is already open" if self._add_workstation_session is not None else None))
        self.btn_remove_member.set_disabled_reason(not_admin)
        self.btn_hand_over_admin.set_disabled_reason(not_admin)
        self.btn_become_admin.set_disabled_reason("This PC is already the admin" if is_admin else None)

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
        if not getattr(self, "sync_status_group", None) or not self._panel_on(self.sync_status_group) or self.db is None:
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
        self.mode_pill.set_text(f"Mode: {mode}")

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
                self.sync_status_warning_label.set_text(
                    f"Warning: Parked changes exist older than 7 days ({sync_panel.format_age(oldest_age)})."
                )
                self.sync_status_warning_label.set_tone("r")
                self.sync_status_warning_label.setVisible(True)
            elif mode == "shadow" and oldest_age is not None and oldest_age >= 3600:
                self.sync_status_warning_label.set_text(
                    f"Warning: Parked changes exist older than 1 hour in shadow mode ({sync_panel.format_age(oldest_age)})."
                )
                self.sync_status_warning_label.set_tone("w")
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
        elif prev_key is not None:
            # The previously-selected conflict is gone (dismissed, resolved, or pushed past
            # the LIMIT 200) -- Qt keeps the old row index selected otherwise, which would
            # then point at whatever conflict now occupies that position (review finding 1).
            self.conflicts_table.clearSelection()

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
            self.btn_go_live.setEnabled(False)
            return
        busy = getattr(self, "_shadow_start_busy", False)
        pending_golive = st.get("pending_golive", False)
        if pending_golive:
            text = "Sync mode: goes live when Sera restarts"
        elif st["pending_start"]:
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
            since = (st.get("went_live_at") or "")[:10]
            text = f"Sync mode: live (since {since})" if since else "Sync mode: live"
        else:
            text = "Shadow mode: off"
        self.shadow_mode_label.setText(text)
        went_live = bool(st.get("went_live_at"))
        self.btn_start_shadow.setEnabled(
            not busy and st["mode"] == "off" and not st["replica"] and not st["pending_start"]
            and not pending_golive and not went_live)
        self.btn_reset_shadow.setEnabled(
            not busy and st["mode"] != "live" and not st["pending_start"] and not pending_golive
            and not went_live and (st["replica"] or st["started_at"] is not None))
        self.btn_go_live.setEnabled(
            not busy and st["mode"] == "shadow" and st["replica"] and not st["pending_start"]
            and not pending_golive and not st.get("salvage_pending"))

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

    # ------------------------------------------------------------------
    # Going live (P3-9).
    # ------------------------------------------------------------------

    def _on_go_live(self):
        if self.db is None:
            return
        import sync_shadow
        try:
            day = sync_shadow.shadow_status(self.db, self._app_dir()).get("day")
        except Exception:
            day = None
        week = sync_shadow.SHADOW_WEEK_DAYS
        if day is not None and day <= week:
            week_text = (f"This PC is on day {day} of {week} of the shadow week. The week is NOT "
                         "complete yet.\n\n")
        else:
            week_text = ""
        text = (
            week_text +
            "Go live on this PC?\n\n"
            "Do this only when the owner has decided the shadow week passed: 7 days in a row with "
            "no capture-check mismatch, equal replicas on all PCs within 2 minutes of quiet, and "
            "no parked change older than 1 hour (see the shadow checks above and "
            "logs/sync_shadow.log).\n\n"
            "Sera restarts. This PC's shadow copy, which already holds every PC's changes, becomes "
            "the data staff work with, and other PCs' changes then appear directly. The current "
            "database is kept in the shadow folder. Old-style \"Sync To\" pushes stay off.\n\n"
            "Don't save anything until Sera has restarted: a change made in those seconds stays "
            "only in the kept old database and doesn't reach the other PCs.\n\n"
            "Go live on every PC, one after the other.")
        if QMessageBox.question(self, "Go Live", text, QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No) != QMessageBox.Yes:
            return
        if not self._confirm_admin_pin():
            return
        # Nothing may write to the replica while it is exported: stop the engine and the seal
        # timer first (stage_go_live seals and mirrors what is left itself).
        engine_was_running = bool(self.sync_engine is not None and getattr(self.sync_engine, "running", False))
        if engine_was_running:
            self.sync_engine.stop()
        self.db.stop_seal_timer()
        # Staging (backup + export of both databases) runs on the UI thread on purpose: while it
        # runs nobody can save anything, and a save after the switch to mode off wouldn't reach
        # the other PCs (C3). Show that it's working instead of looking frozen.
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
        self.btn_go_live.setEnabled(False)
        self.shadow_mode_label.setText("Sync mode: preparing to go live (this can take a minute)...")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        try:
            try:
                sync_shadow.stage_go_live(self.db, self._app_dir())
            finally:
                QApplication.restoreOverrideCursor()
        except Exception as exc:
            # Staging put this PC back in shadow mode; get sync running again (open-problems S2).
            try:
                self.db.start_seal_timer()
                if engine_was_running:
                    self.sync_engine.start()
            except Exception as restart_exc:
                exc = f"{exc} (and Sera Sync could not be restarted: {restart_exc}; restart Sera)"
            QMessageBox.warning(self, "Go Live", f"This PC did not go live: {exc}")
            self._refresh_sync_status()
            return
        try:
            if self.sync_service:
                self.sync_service.stop()
        except Exception:
            pass
        import version
        version.restart_app()

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
            show_message(self, "Add Workstation", "A pairing window is already open.")
            return
        own_device_id, own_cert_pem = self._own_identity()
        if own_device_id is None:
            show_message(self, "Add Workstation", "This PC's device identity could not be read.", "warning")
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
            show_message(self, "Add Workstation", f"Could not open the pairing window: {exc}", "warning")
            return
        self._add_workstation_session = session
        self.btn_add_workstation.set_disabled_reason("A code window is already open")
        if self.discovery_service is not None:
            try:
                import sera_keys
                office = sera_keys.load_office(self._app_dir())
                self.discovery_service.set_pairing(True, office.office_name if office else "", session.window.port)
            except Exception:
                pass

        dlg = QDialog(self)
        dlg.setModal(True)
        dlg.setWindowTitle("Add Workstation")
        dlg.setMinimumWidth(KIT["w_msg"])
        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = DialogFrame("Add workstation", "Pairing code for a new PC", dlg, icon="mdi.lan-connect")
        outer.addWidget(frame)
        frame.closeRequested.connect(dlg.reject)
        body = QLabel(
            f"Code for the new PC: {session.window.display_code}\n\n"
            "Enter it in \"Join an Existing Office\" on the new PC. This window closes itself "
            "after the PC joins, 3 wrong tries, or 5 minutes."
        )
        body.setObjectName("KitDlgMessage")
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        frame.add_widget(body)
        cancel_btn = frame.add_action("Cancel")
        cancel_btn.clicked.connect(dlg.reject)

        def _poll():
            if not session.window.is_open:
                dlg.accept()
                return
            stalls = session.window.stalled_connections
            blocked = [ip for ip, n in stalls.items() if n >= 3]
            if blocked:
                body.setText(body.text().split("\n\n")[0] + "\n\n"
                             f"Note: {', '.join(blocked)} keeps connecting without trying a code; "
                             "it may be blocking pairing.")
        poll_timer = QTimer(dlg)
        poll_timer.timeout.connect(_poll)
        poll_timer.start(1000)
        result = dlg.exec()
        poll_timer.stop()
        if result == QDialog.Rejected and session.window.is_open:
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
                bar = self.log_list.verticalScrollBar()
                bar.setValue(bar.maximum())
                self.log_list.setUpdatesEnabled(True)

    def _on_activity_received(self, timestamp: str, category: str, message: str):
        self._add_log_item(timestamp, category, message, auto_scroll=True)

    @staticmethod
    def _activity_tone(category: str) -> str:
        """The tag colour of an activity category (neutral when none of the words match)."""
        cat_upper = category.upper()
        for word, tone in _ACTIVITY_TONES:
            if word in cat_upper:
                return tone
        return "neutral"

    def _add_log_item(self, ts: str, cat: str, message: str, auto_scroll: bool = True):
        # One line per event: "[ts] CATEGORY message", the category coloured by _activity_tone.
        # LogView keeps the newest _ACTIVITY_MAX_LINES lines (the oldest drop off, as before)
        # and follows new lines unless the user has scrolled up to read (auto_scroll is kept for
        # the callers; the history load jumps to the end itself).
        self.log_list.add_line(message, cat.upper(), tone=self._activity_tone(cat), ts=f"[{ts}]")

    def _clear_activity_log(self):
        self.log_list.clear_log()

    def _refresh_peers(self):
        if not self.sync_service:
            return
        peers = self.sync_service.get_peers()
        self.status_label.setText(f"{len(peers)} device{'s' if len(peers) != 1 else ''} online")
        self.table.setVisible(bool(peers))
        self.devices_empty.setVisible(not peers)
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

            # 5: Data Counts
            c_cnt = peer.get("client_count", 0)
            t_cnt = peer.get("tracker_count", 0)
            item5 = self.table.item(r_idx, 5) or QTableWidgetItem()
            item5.setText(f"{c_cnt} CLI | {t_cnt} Dumps")
            if not self.table.item(r_idx, 5): self.table.setItem(r_idx, 5, item5)

            # 6: Status (a pill: emerald Normal, amber when the office key differs)
            item6 = self.table.item(r_idx, 6) or QTableWidgetItem()
            local_key_id = getattr(self.sync_service, "key_id", None)
            peer_key_id = peer.get("key_id")
            if local_key_id != peer_key_id:
                item6.setText("different office key — rejoin needed")
                item6.setData(ROLE_PILL, (item6.text(), "amber"))
            else:
                item6.setText("Normal")
                item6.setData(ROLE_PILL, (item6.text(), "emerald", True))
            if not self.table.item(r_idx, 6): self.table.setItem(r_idx, 6, item6)

            # Every fact in the row's tooltip, so an elided or hidden column loses nothing.
            facts = (f"{item0.text()} · {item1.text()}\nIP {item2.text()} · Version {item3.text()}\n"
                     f"Database changed {item4.text()}\nClients / dumps {c_cnt} / {t_cnt}\n{item6.text()}")
            for item in (item0, item1, item2, item3, item4, item5, item6):
                item.setToolTip(facts)

        if new_sel_row >= 0:
            self.table.selectRow(new_sel_row)
        self.table.setUpdatesEnabled(True)

    # _on_sync_clicked / _on_sync_all_clicked (push_to / push_to_all to a selected LAN
    # device) were removed in P4-1 along with the legacy protocol they called; Sera Sync
    # v3 exchanges changes on its own once workstations are paired as office members.

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
                show_message(
                    self,
                    "Invalid Port",
                    f"Port '{port_part}' is invalid.\nPort must be a number between 1 and 65535.",
                    "warning",
                )
                return
        else:
            ip_part = addr

        try:
            ipaddress.IPv4Address(ip_part)
        except ValueError:
            show_message(
                self,
                "Invalid IP Address",
                f"'{ip_part}' is not a valid IPv4 address.\nPlease enter a valid IPv4 address.",
                "warning",
            )
            return

        # Load existing manual peers list
        existing = self._get_manual_peers_list()

        if addr in existing:
            show_message(
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

        show_message(
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
            show_message(
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
        if not ask_confirm(
            self,
            "Confirm Removal",
            f"Remove workstation address '{addr}' from office manual peer list?",
            yes="Remove",
            no="Cancel",
            danger=True,
        ):
            return

        self._remove_manual_peer_address(addr)

        show_message(
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
            act_remove = menu.addAction(f"Remove '{matching_addr}' from Manual Peers")
            try:
                import qtawesome as qta
                act_remove.setIcon(qta.icon("mdi.minus-network", color=TOKENS["dangertx"]))
            except Exception:
                pass

            chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
            if chosen == act_remove:
                if ask_confirm(
                    self,
                    "Confirm Removal",
                    f"Remove workstation '{peer_host}' ({matching_addr}) from office manual peer list?",
                    yes="Remove",
                    no="Cancel",
                    danger=True,
                ):
                    self._remove_manual_peer_address(matching_addr)
                    show_message(
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
