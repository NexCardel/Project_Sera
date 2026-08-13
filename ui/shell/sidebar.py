try:
    import qtawesome as qta
except Exception:
    qta = None
from PySide6.QtCore import Qt, Signal, QSize, Property, QPropertyAnimation, QEasingCurve, QRectF
from PySide6.QtGui import QPainter, QColor, QBrush, QPen
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QAbstractButton,
    QScrollArea
)

class ToggleSwitch(QAbstractButton):
# (unchanged up to paintEvent end)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(32, 16)
        self._position = 0
        
        self.animation = QPropertyAnimation(self, b"position")
        self.animation.setEasingCurve(QEasingCurve.InOutQuad)
        self.animation.setDuration(150)
        
        self.toggled.connect(self._start_animation)

    def _start_animation(self, checked):
        self.animation.setEndValue(1.0 if checked else 0.0)
        self.animation.start()

    @Property(float)
    def position(self):
        return self._position

    @position.setter
    def position(self, pos):
        self._position = pos
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Track background
        bg_color = QColor("#888888") if self.isChecked() else QColor("#999999")
        if self.isChecked(): bg_color = QColor("#FF4D4D") # Red accent when checked
        painter.setBrush(QBrush(bg_color))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(0, 0, self.width(), self.height(), self.height()/2, self.height()/2)
        
        # Handle (Thumb)
        handle_color = QColor("white")
        painter.setBrush(QBrush(handle_color))
        
        handle_radius = self.height() / 2 - 2
        start_x = 2
        end_x = self.width() - handle_radius * 2 - 2
        
        current_x = start_x + (end_x - start_x) * self._position
        
        painter.drawEllipse(QRectF(current_x, 2, handle_radius*2, handle_radius*2))
        painter.end()


class AccordionHeader(QWidget):
    toggled = Signal(bool)
    
    def __init__(self, text, icon_name, expanded=True, parent=None):
        super().__init__(parent)
        self.setObjectName("SidebarAccordionHeader")
        self.setStyleSheet("background-color: #2E9B5F;")
        self.is_expanded = expanded
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(32)
        
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(8, 5, 8, 5)
        self.layout.setSpacing(9)
        
        self.icon_lbl = QLabel()
        self.icon_lbl.setStyleSheet("background-color: transparent;")
        if icon_name:
            self.icon_lbl.setPixmap(qta.icon(icon_name, color='#F8F5F2').pixmap(17, 17))
        
        self.text_lbl = QLabel(text)
        self.text_lbl.setStyleSheet("background-color: transparent; font-weight: 600; color: #F8F5F2; border: none; font-size: 13px;")
        
        self.chevron_lbl = QLabel()
        self.chevron_lbl.setStyleSheet("background-color: transparent;")
        self._update_chevron()
        
        if icon_name:
            self.layout.addWidget(self.icon_lbl)
        self.layout.addWidget(self.text_lbl)
        self.layout.addStretch()
        self.layout.addWidget(self.chevron_lbl)
        
    def _update_chevron(self):
        icon = "mdi.chevron-up" if self.is_expanded else "mdi.chevron-down"
        self.chevron_lbl.setPixmap(qta.icon(icon, color='#F8F5F2').pixmap(16, 16))
        
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_expanded = not self.is_expanded
            self._update_chevron()
            self.toggled.emit(self.is_expanded)
        super().mousePressEvent(event)


def _safe_qta_icon(icon_name, color=None):
    if qta is not None:
        try:
            if color:
                return qta.icon(icon_name, color=color)
            return qta.icon(icon_name)
        except Exception:
            pass
    from PySide6.QtGui import QIcon
    return QIcon()

class Sidebar(QFrame):
    # Navigation Signals
    go_to_search = Signal()
    
    # Action Signals
    action_import_csv = Signal()
    action_download_template = Signal()
    action_purge_duplicates = Signal()
    action_drs = Signal()
    action_manage_clients = Signal()
    action_audit_log = Signal()
    action_manage_mcl = Signal()
    action_manage_services = Signal()
    action_manage_staff = Signal()
    action_manage_filing_types = Signal()
    action_import_fps = Signal()
    action_export_csv = Signal()
    action_backup = Signal()
    
    action_settings = Signal()
    action_restore = Signal()
    action_open_sera_sync = Signal()
    
    # Toggle Signal
    action_enter_admin = Signal()
    action_exit_admin = Signal()


    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        # Keep the sidebar compact while giving its longest labels room to breathe.
        self.setFixedWidth(172)
        self._admin_mode = False
        self._build_ui()

    def _build_ui(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(9, 13, 9, 8)
        self.main_layout.setSpacing(5)

        # Title / Brand
        brand = QWidget()
        brand.setStyleSheet("background-color: #2E9B5F;")
        brand_layout = QHBoxLayout(brand)
        brand_layout.setContentsMargins(4, 3, 4, 8)
        brand_layout.setSpacing(8)
        
        logo = QLabel()
        logo.setObjectName("SidebarLogo")
        logo.setAlignment(Qt.AlignCenter)
        logo.setFixedSize(28, 28)
        
        from pathlib import Path
        from PySide6.QtGui import QPixmap
        logo_path = Path(__file__).resolve().parent.parent.parent / "assets" / "logo" / "icon_here.png"
        if not logo_path.exists():
            logo_path = Path(__file__).resolve().parent.parent.parent / "assets" / "logo" / "files" / "sera_icon_whitegold.png"
        if not logo_path.exists():
            logo_path = Path(__file__).resolve().parent.parent.parent / "assets" / "logo" / "sera_icon.png"
            
        if logo_path.exists():
            pix = QPixmap(str(logo_path)).scaled(28, 28, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            logo.setPixmap(pix)
        else:
            logo.setText("PS")
            logo.setStyleSheet("background-color: #164A68; border-radius: 5px; color: #F8F5F2; font-size: 10px; font-weight: 700;")

        title = QLabel("Aman Associates")
        title.setObjectName("SidebarTitle")
        title.setStyleSheet("background-color: transparent; color: #F8F5F2; font-size: 14px; font-weight: 700;")

        brand_layout.addWidget(logo)
        brand_layout.addWidget(title)
        brand_layout.addStretch()
        self.main_layout.addWidget(brand)
        
        # Admin Toggle (Top Area)
        self.admin_layout = QHBoxLayout()
        self.lbl_admin = QLabel("Admin Mode")
        self.lbl_admin.setObjectName("SidebarSection")
        self.lbl_admin.setStyleSheet("color: white;")
        
        self.toggle_admin = ToggleSwitch()
        self.toggle_admin.toggled.connect(self._on_admin_toggled)
        
        self.admin_layout.addWidget(self.lbl_admin)
        self.admin_layout.addStretch()
        self.admin_layout.addWidget(self.toggle_admin)
        self.main_layout.addLayout(self.admin_layout)

        divider = QFrame()
        divider.setObjectName("SidebarDivider")
        divider.setFrameShape(QFrame.HLine)
        self.main_layout.addWidget(divider)
        self.main_layout.addSpacing(1)

        # Scroll Area for Buttons
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setStyleSheet("QScrollArea { background: transparent; } QWidget#ScrollContent { background: transparent; }")
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        
        self.scroll_content = QWidget()
        self.scroll_content.setObjectName("ScrollContent")
        self.scroll_content.setStyleSheet("background-color: #2E9B5F;")
        self.layout = QVBoxLayout(self.scroll_content)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(3)
        
        self.scroll_area.setWidget(self.scroll_content)
        self.main_layout.addWidget(self.scroll_area)

        self.admin_widgets = []
        self._nav_buttons = []
        
        # --- Dashboard ---
        self.btn_drs = self._create_nav_button("Dashboard", self.action_drs, "mdi.home-outline")
        self.btn_drs.setEnabled(False)
        self.btn_drs.setToolTip("DRS (Dashboard & Deadline Reminder System) is currently offline for system maintenance.")

        # --- Clients Group ---
        self.lbl_clients, self.l_clients = self._create_accordion_group("Clients", "mdi.account-multiple-outline", expanded=True)
        
        self.btn_all_clients = self._create_sub_nav_button("All Clients", self.go_to_search, parent_layout=self.l_clients)
        self.btn_search = self._create_sub_nav_button("Search", self.go_to_search, parent_layout=self.l_clients)
        self._set_active_button(self.btn_search)
        self.btn_manage_clients = self._create_admin_sub_nav_button(
            "Manage Clients", self.action_manage_clients, parent_layout=self.l_clients
        )
        self.btn_audit = self._create_admin_sub_nav_button("Audit Log", self.action_audit_log, parent_layout=self.l_clients)
        self.btn_manage_mcl = self._create_admin_sub_nav_button("Manage MCL", self.action_manage_mcl, parent_layout=self.l_clients)
        self.btn_purge = self._create_sub_nav_button("Purge Duplicates", self.action_purge_duplicates, parent_layout=self.l_clients)

        # --- Settings Group ---
        self.lbl_settings, self.l_settings = self._create_accordion_group("Settings", "mdi.cog-outline", expanded=True)
        
        self.btn_settings = self._create_admin_sub_nav_button("General", self.action_settings, parent_layout=self.l_settings)
        self.btn_manage_ft = self._create_admin_sub_nav_button("Filing Types", self.action_manage_filing_types, parent_layout=self.l_settings)
        self.btn_manage_ft.setEnabled(False)
        self.btn_manage_ft.setToolTip("Filing Types management is currently offline because DRS is offline.")

        self.btn_import_fps = self._create_admin_sub_nav_button("Periods", self.action_import_fps, parent_layout=self.l_settings)
        self.btn_import_fps.setEnabled(False)
        self.btn_import_fps.setToolTip("Filing Periods management is currently offline because DRS is offline.")

        self.btn_dl_template = self._create_sub_nav_button("CSV Templates", self.action_download_template, parent_layout=self.l_settings)

        # --- Services Group ---
        self.lbl_services, self.l_services = self._create_accordion_group("Services", "mdi.briefcase-outline", expanded=False)
        self.btn_manage_services = self._create_admin_sub_nav_button("Manage Services", self.action_manage_services, parent_layout=self.l_services)
        self.btn_manage_staff = self._create_admin_sub_nav_button("Manage Staff Users", self.action_manage_staff, parent_layout=self.l_services)
        # --- Data Management Group ---
        self.lbl_data, self.l_data = self._create_accordion_group("Data Management", "mdi.database-outline", expanded=False)
        
        self.btn_new_clients = self._create_sub_nav_button("Import CSV", self.action_import_csv, parent_layout=self.l_data)
        self.btn_export = self._create_admin_sub_nav_button("Export CSV", self.action_export_csv, parent_layout=self.l_data)
        self.btn_backup = self._create_admin_sub_nav_button("Backup DB", self.action_backup, parent_layout=self.l_data)
        self.btn_restore = self._create_admin_sub_nav_button("Restore DB", self.action_restore, parent_layout=self.l_data)

        # Spacer keeps the navigation at the top while the account area stays pinned.
        self.layout.addSpacerItem(QSpacerItem(20, 40, QSizePolicy.Minimum, QSizePolicy.Expanding))

        footer_divider = QFrame()
        footer_divider.setObjectName("SidebarDivider")
        footer_divider.setFrameShape(QFrame.HLine)
        self.main_layout.addWidget(footer_divider)
        self.profile_row = QWidget()
        self.profile_row.setStyleSheet("background-color: #2E9B5F;")
        profile_layout = QHBoxLayout(self.profile_row)
        profile_layout.setContentsMargins(2, 2, 2, 2)
        profile_layout.setSpacing(7)

        profile = QLabel()
        profile.setObjectName("SidebarProfile")
        profile.setAlignment(Qt.AlignCenter)
        profile.setFixedSize(27, 27)
        profile.setPixmap(_safe_qta_icon("fa5s.user", color="#168A70").pixmap(14, 14))
        self.profile_name = QLabel()
        self.profile_name.setObjectName("SidebarProfileName")
        self.profile_name.setStyleSheet("background-color: transparent; color: #F8F5F2; font-size: 12px; font-weight: 600;")
        profile_layout.addWidget(profile)
        profile_layout.addWidget(self.profile_name, stretch=1)
        self.profile_row.installEventFilter(self)
        self.main_layout.addWidget(self.profile_row)

        self.sync_status_badge = QLabel("🟢 Auto-Sync Active")
        self.sync_status_badge.setStyleSheet("color: #4CF9B7; font-size: 11px; font-weight: 600; padding: 4px 8px; background-color: #1A382B; border-radius: 4px; margin-top: 4px;")
        self.main_layout.addWidget(self.sync_status_badge)

        self._update_visibility()

    def notify_sync_sent(self, count: int, total: int):
        """Visual badge indicator when local changes are pushed to LAN peers."""
        import datetime
        now_t = datetime.datetime.now().strftime("%H:%M:%S")
        target_str = f"{count}/{total} PCs" if total > 1 else f"{count} PC"
        self.sync_status_badge.setText(f"⬆️ Synced to {target_str} ({now_t})")
        self.sync_status_badge.setStyleSheet(
            "color: #FFFFFF; font-size: 11px; font-weight: 700; padding: 4px 8px; background-color: #2E9B5F; border-radius: 4px; margin-top: 4px;"
        )

    def notify_sync_received(self, sender_username: str, sender_host: str):
        """Visual badge indicator when incoming live auto-sync is received."""
        import datetime
        now_t = datetime.datetime.now().strftime("%H:%M:%S")
        self.sync_status_badge.setText(f"⬇️ Synced Live {now_t} ({sender_host})")
        self.sync_status_badge.setStyleSheet(
            "color: #FFFFFF; font-size: 11px; font-weight: 700; padding: 4px 8px; background-color: #1F7846; border-radius: 4px; margin-top: 4px;"
        )

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
        self.toggle_admin.position = 1.0 if active else 0.0
        self.toggle_admin.blockSignals(False)


    def set_user_name(self, user_name: str):
        """Show the signed-in staff label without allowing it to overflow the sidebar."""
        available_width = max(60, self.width() - 58)
        self.profile_name.setText(
            self.profile_name.fontMetrics().elidedText(user_name or "Staff", Qt.ElideRight, available_width)
        )

    def _update_visibility(self):
        for w in self.admin_widgets:
            w.setVisible(self._admin_mode)
        
        self.toggle_admin.blockSignals(True)
        self.toggle_admin.setChecked(self._admin_mode)
        self.toggle_admin.position = 1.0 if self._admin_mode else 0.0
        self.toggle_admin.blockSignals(False)

    def _create_accordion_group(self, text, icon_name, expanded=True):
        header = AccordionHeader(text, icon_name, expanded)
        if text == "Clients":
            header.setProperty("active", True)
            header.setStyleSheet("background-color: #23794A; border-radius: 4px;")
        self.layout.addWidget(header)
        
        container = QWidget()
        container.setStyleSheet("background-color: #2E9B5F;")
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(4)
        
        container.setVisible(expanded)
        header.toggled.connect(container.setVisible)
        
        self.layout.addWidget(container)
        return header, container_layout

    def _create_nav_button(self, text, signal, icon_name=None, parent_layout=None) -> QPushButton:
        btn = QPushButton(f" {text}")
        if icon_name:
            btn.setIcon(_safe_qta_icon(icon_name, color='#F8F5F2'))
            btn.setIconSize(QSize(17, 17))
        btn.setObjectName("SidebarButton")
        btn.setProperty("sidebar_sub", False)
        self._style_nav_button(btn, active=False)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(signal.emit)
        btn.clicked.connect(lambda _checked=False, button=btn: self._set_active_button(button))
        
        target_layout = parent_layout if parent_layout else self.layout
        target_layout.addWidget(btn)
        self._nav_buttons.append(btn)
        return btn

    def _create_sub_nav_button(self, text, signal, parent_layout=None) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("SidebarSubButton")
        btn.setProperty("sidebar_sub", True)
        self._style_nav_button(btn, active=False)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(signal.emit)
        btn.clicked.connect(lambda _checked=False, button=btn: self._set_active_button(button))
        
        target_layout = parent_layout if parent_layout else self.layout
        target_layout.addWidget(btn)
        self._nav_buttons.append(btn)
        return btn

    def _set_active_button(self, selected: QPushButton):
        """Keep a single, clear navigation selection visible at a time."""
        for button in self._nav_buttons:
            is_active = button is selected
            button.setProperty("active", is_active)
            self._style_nav_button(button, active=is_active)

    def set_active_navigation(self, button: QPushButton):
        """Synchronize the visual selection when navigation is changed in code."""
        self._set_active_button(button)

    @staticmethod
    def _style_nav_button(button: QPushButton, active: bool):
        is_sub = bool(button.property("sidebar_sub"))
        padding = "3px 8px 3px 34px"
        size = "12px"
        if not is_sub:
            padding = "5px 8px"
            size = "13px"

        if active:
            button.setStyleSheet(
                f"QPushButton {{ background-color: #FF4D4D; color: #FFFFFF; border: none; "
                f"border-radius: 4px; padding: {padding}; font-size: {size}; font-weight: 700; text-align: left; }}"
                "QPushButton:hover { background-color: #E63939; }"
            )
        else:
            button.setStyleSheet(
                f"QPushButton {{ background-color: #2E9B5F; color: #F8F5F2; border: none; "
                f"border-radius: 4px; padding: {padding}; font-size: {size}; font-weight: 500; text-align: left; }}"
                "QPushButton:hover { background-color: #23794A; }"
            )

    def _create_admin_nav_button(self, text, signal, icon_name=None, parent_layout=None) -> QPushButton:
        btn = self._create_nav_button(text, signal, icon_name, parent_layout)
        self.admin_widgets.append(btn)
        return btn

    def _create_admin_sub_nav_button(self, text, signal, parent_layout=None) -> QPushButton:
        btn = self._create_sub_nav_button(text, signal, parent_layout)
        self.admin_widgets.append(btn)
        return btn
