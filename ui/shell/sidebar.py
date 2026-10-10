try:
    import qtawesome as qta
except Exception:
    qta = None
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QSize, QTimer
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QScrollArea
)

from ui.components.kit import IconButton, LabelledAction, NavItem, NavLabel, Switch, sidebar_qss
from ui.utils.tokens import KIT, TOKENS

SIDEBAR_WIDTH = TOKENS["w_side"]
_LOGO_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "logo"
_SYNC_IDLE_TEXT = "Auto-sync active"


def _logo_pixmap(side: int, ratio: float):
    """The Sera mark from sera_mark.svg, drawn crisp at `ratio`; None when it cannot be drawn."""
    svg = _LOGO_DIR / "sera_mark.svg"
    try:
        from PySide6.QtSvg import QSvgRenderer
        renderer = QSvgRenderer(str(svg))
        if not renderer.isValid():
            return None
        pix = QPixmap(int(side * ratio), int(side * ratio))
        pix.setDevicePixelRatio(ratio)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        renderer.render(painter)
        painter.end()
        return pix
    except Exception:
        return None


def _safe_qta_icon(icon_name, color=None):
    if qta is not None:
        try:
            if color:
                return qta.icon(icon_name, color=color)
            return qta.icon(icon_name)
        except Exception:
            pass
    return QIcon()


class Sidebar(QFrame):
    # Navigation Signals
    go_to_search = Signal()

    # Action Signals
    action_import_csv = Signal()
    action_display_scale = Signal()
    action_manage_clients = Signal()
    action_tracker_dump = Signal()
    action_audit_log = Signal()

    action_settings = Signal()
    action_open_sera_sync = Signal()
    action_trigger_sync = Signal()

    # Toggle Signal
    action_enter_admin = Signal()
    action_exit_admin = Signal()
    toggle_requested = Signal()


    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(SIDEBAR_WIDTH)
        self.setStyleSheet(sidebar_qss())
        self._admin_mode = False
        self._sync_timer = QTimer(self)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.timeout.connect(self._sync_back_to_idle)
        self._build_ui()

    def _build_ui(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(KIT["pad_sb_h"], KIT["pad_sb_top"], KIT["pad_sb_h"], KIT["pad_sb_bot"])
        self.main_layout.setSpacing(0)

        # Title / Brand
        brand = QWidget()
        brand.setObjectName("SidebarBrand")
        brand_layout = QHBoxLayout(brand)
        brand_layout.setContentsMargins(4, 0, 4, 10)
        brand_layout.setSpacing(KIT["gap_brand"])

        logo = QLabel()
        logo.setObjectName("SidebarLogo")
        logo.setAlignment(Qt.AlignCenter)
        logo.setFixedSize(KIT["d_logo"], KIT["d_logo"])
        pix = _logo_pixmap(KIT["d_logo"], self.devicePixelRatioF() or 1.0)
        if pix is None:
            png = _LOGO_DIR / "sera_icon.png"
            pix = QPixmap(str(png)).scaled(KIT["d_logo"], KIT["d_logo"], Qt.KeepAspectRatio, Qt.SmoothTransformation) if png.exists() else None
        if pix is not None:
            logo.setPixmap(pix)
        else:
            logo.setText("PS")

        names = QVBoxLayout()
        names.setContentsMargins(0, 0, 0, 0)
        names.setSpacing(0)
        title = QLabel("Aman Associates")
        title.setObjectName("SidebarTitle")
        title.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        subtitle = QLabel("Project Sera")
        subtitle.setObjectName("SidebarSub")
        names.addWidget(title)
        names.addWidget(subtitle)
        self.brand_title = title

        self.btn_toggle_sidebar = IconButton("mdi.dock-left", "Toggle / hide sidebar (Ctrl+B)", sm=True)
        self.btn_toggle_sidebar.setObjectName("SidebarCollapseButton")
        self.btn_toggle_sidebar.clicked.connect(self.toggle_requested.emit)

        brand_layout.addWidget(logo)
        brand_layout.addLayout(names, stretch=1)
        brand_layout.addWidget(self.btn_toggle_sidebar)
        self.main_layout.addWidget(brand)

        # Admin Toggle (Top Area)
        admin_row = QWidget()
        admin_row.setObjectName("SidebarAdminRow")
        self.admin_layout = QHBoxLayout(admin_row)
        self.admin_layout.setContentsMargins(6, 8, 6, 10)
        self.lbl_admin = QLabel("Admin mode")
        self.lbl_admin.setObjectName("SidebarSection")

        self.toggle_admin = Switch(tooltip="Admin Mode")
        self.toggle_admin.toggled.connect(self._on_admin_toggled)

        self.admin_layout.addWidget(self.lbl_admin)
        self.admin_layout.addStretch()
        self.admin_layout.addWidget(self.toggle_admin)
        self.main_layout.addWidget(admin_row)

        divider = QFrame()
        divider.setObjectName("SidebarDivider")
        divider.setFrameShape(QFrame.HLine)
        self.main_layout.addWidget(divider)

        # Scroll Area for Buttons
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("SidebarScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self.scroll_content = QWidget()
        self.scroll_content.setObjectName("SidebarScrollContent")
        self.layout = QVBoxLayout(self.scroll_content)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(2)

        self.scroll_area.setWidget(self.scroll_content)
        self.main_layout.addWidget(self.scroll_area)

        self.admin_widgets = []
        self._nav_buttons = []

        # --- Clients ---
        self.layout.addWidget(NavLabel("Clients"))
        self.btn_all_clients = self._create_nav_button(
            "All Clients", self.go_to_search, "mdi.account-multiple-outline", hint="Ctrl K")
        self.btn_search = self.btn_all_clients  # one entry; "Search" was a second button for the same signal
        self.btn_manage_clients = self._create_admin_nav_button(
            "Manage Clients", self.action_manage_clients, "mdi.pencil-outline")
        self.btn_tracker_dump = self._create_nav_button("Tracker Dump", self.action_tracker_dump, "mdi.radar")
        self.btn_audit = self._create_admin_nav_button("Audit Log", self.action_audit_log, "mdi.shield-outline")
        self._set_active_button(self.btn_all_clients)

        # --- Tools ---
        self.layout.addWidget(NavLabel("Tools"))
        self.btn_new_clients = self._create_nav_button("Import CSV", self.action_import_csv, "mdi.database-outline")
        # Open to everyone (unlike Options): a PC's own screen scale is not an admin setting.
        self.btn_display_scale = self._create_nav_button("Display", self.action_display_scale, "mdi.monitor")

        # --- Admin ---
        self.lbl_admin_group = NavLabel("Admin")
        self.layout.addWidget(self.lbl_admin_group)
        self.admin_widgets.append(self.lbl_admin_group)
        self.btn_settings = self._create_admin_nav_button("Options", self.action_settings, "mdi.cog-outline")

        # Spacer keeps the navigation at the top while the account area stays pinned.
        self.layout.addSpacerItem(QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding))

        footer_divider = QFrame()
        footer_divider.setObjectName("SidebarDivider")
        footer_divider.setFrameShape(QFrame.HLine)
        self.main_layout.addWidget(footer_divider)
        self.main_layout.addSpacing(KIT["pad_sb_bot"])
        self.profile_row = QWidget()
        self.profile_row.setObjectName("SidebarProfileRow")
        profile_layout = QHBoxLayout(self.profile_row)
        profile_layout.setContentsMargins(4, 2, 4, 2)
        profile_layout.setSpacing(8)

        profile = QLabel()
        profile.setObjectName("SidebarProfile")
        profile.setAlignment(Qt.AlignCenter)
        profile.setFixedSize(KIT["d_avatar"], KIT["d_avatar"])
        profile.setPixmap(_safe_qta_icon("mdi.account", color=KIT["white"]).pixmap(14, 14))
        self.profile_name = QLabel()
        self.profile_name.setObjectName("SidebarProfileName")
        profile_layout.addWidget(profile)
        profile_layout.addWidget(self.profile_name, stretch=1)
        self.profile_row.installEventFilter(self)
        self.main_layout.addWidget(self.profile_row)
        self.main_layout.addSpacing(8)

        self.sync_status_badge = LabelledAction(
            _SYNC_IDLE_TEXT, variant="soft", sm=True, icon="mdi.circle-medium",
            tooltip="Sera LAN Mesh • Live Link Active\nClick to Broadcast Sync with LAN Peers")
        self.sync_status_badge.setObjectName("SyncDeckPill")
        self.sync_status_badge.clicked.connect(self._on_sync_pill_clicked)
        self.main_layout.addWidget(self.sync_status_badge)
        self.main_layout.addSpacing(8)

        # App version, so staff (and a two-PC sync test) can tell which build a PC runs --
        # the window starts full-screen by default, which hides the title bar.
        from version import APP_VERSION
        self.version_lbl = QLabel(f"v{APP_VERSION}")
        self.version_lbl.setObjectName("SidebarVersion")
        self.version_lbl.setAlignment(Qt.AlignCenter)
        self.main_layout.addWidget(self.version_lbl)

        self._update_visibility()

    def _show_sync_state(self, state: str, text: str, tooltip: str = "", hold_ms: int = 0):
        """The pill shows `text` in the emerald fill (syncing / received) or the soft idle look for hold_ms."""
        self.sync_status_badge.set_variant("soft" if state == "idle" else "primary")
        self.sync_status_badge.setText(text)
        if state != "idle":
            self.sync_status_badge.setToolTip(tooltip or text)
            self._sync_timer.start(hold_ms)

    def _sync_back_to_idle(self):
        self._show_sync_state("idle", _SYNC_IDLE_TEXT)
        self.sync_status_badge.setToolTip("Sera LAN Mesh • Live Link Active\nClick to Broadcast Sync with LAN Peers")

    def _on_sync_pill_clicked(self):
        self._show_sync_state("syncing", "Syncing LAN mesh…", hold_ms=1600)
        self.action_trigger_sync.emit()

    def notify_sync_sent(self, count: int, total: int):
        """Visual badge indicator when local changes are pushed to LAN peers."""
        import datetime
        now = datetime.datetime.now()
        target_str = f"{count}/{total} PCs" if total > 1 else f"{count} PC"
        self._show_sync_state("syncing", f"Synced {target_str} · {now:%H:%M}",
                              f"Synced {target_str} at {now:%H:%M:%S}", hold_ms=3500)

    def notify_sync_received(self, sender_username: str, sender_host: str):
        """Visual badge indicator when incoming live auto-sync is received."""
        import datetime
        now = datetime.datetime.now()
        self._show_sync_state("received", f"Live sync · {now:%H:%M}",
                              f"Live sync received at {now:%H:%M:%S}", hold_ms=3500)

    def eventFilter(self, watched, event):
        try:
            from PySide6.QtCore import QEvent
            if watched is getattr(self, "profile_row", None) and event.type() == QEvent.MouseButtonPress:
                if self._admin_mode:
                    self.action_open_sera_sync.emit()
                    return True
        except Exception:
            pass
        return False

    def _on_admin_toggled(self, checked):
        if checked:
            self.action_enter_admin.emit()
        else:
            self.action_exit_admin.emit()

    def set_admin_mode(self, active: bool):
        self._admin_mode = active
        for w in self.admin_widgets:
            w.setVisible(active)

        if hasattr(self, "profile_row"):
            if active:
                self.profile_row.setCursor(Qt.PointingHandCursor)
                self.profile_row.setToolTip("Open Sera Sync (Admin Only)")
            else:
                self.profile_row.setCursor(Qt.ArrowCursor)
                self.profile_row.setToolTip("")

        self.toggle_admin.blockSignals(True)
        self.toggle_admin.setChecked(active)
        self.toggle_admin.blockSignals(False)


    def set_user_name(self, user_name: str):
        """Show the signed-in staff label without allowing it to overflow the sidebar."""
        available_width = max(60, SIDEBAR_WIDTH - 2 * KIT["pad_sb_h"] - KIT["d_avatar"] - 20)
        self.profile_name.setText(
            self.profile_name.fontMetrics().elidedText(user_name or "Staff", Qt.ElideRight, available_width)
        )

    def _update_visibility(self):
        for w in self.admin_widgets:
            w.setVisible(self._admin_mode)

        self.toggle_admin.blockSignals(True)
        self.toggle_admin.setChecked(self._admin_mode)
        self.toggle_admin.blockSignals(False)

    def _create_nav_button(self, text, signal, icon_name=None, hint=None) -> NavItem:
        btn = NavItem(text, icon=icon_name, hint=hint)
        btn.clicked.connect(signal.emit)
        btn.clicked.connect(lambda _checked=False, button=btn: self._set_active_button(button))
        self.layout.addWidget(btn)
        self._nav_buttons.append(btn)
        return btn

    def _set_active_button(self, selected: NavItem):
        """Keep a single, clear navigation selection visible at a time."""
        for button in self._nav_buttons:
            button.set_active(button is selected)

    def set_active_navigation(self, button: NavItem):
        """Synchronize the visual selection when navigation is changed in code."""
        self._set_active_button(button)

    def _create_admin_nav_button(self, text, signal, icon_name=None, hint=None) -> NavItem:
        btn = self._create_nav_button(text, signal, icon_name, hint)
        self.admin_widgets.append(btn)
        return btn
