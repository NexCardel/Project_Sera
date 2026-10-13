from PySide6.QtWidgets import QHBoxLayout, QLayout, QSizePolicy, QStackedWidget, QWidget, QGraphicsBlurEffect, QGraphicsOpacityEffect
from PySide6.QtCore import QPropertyAnimation, QEasingCurve, QEvent, Qt


from ui.shell.sidebar import Sidebar
from ui.services.alert_service import ActionAlertFormatter


SIDEBAR_AUTO_COLLAPSE_BELOW = 1000   # logical px: narrower than this, the sidebar folds away by itself
SIDEBAR_AUTO_EXPAND_ABOVE = 1060     # wider than this again (the gap stops it flapping at the edge)


class AppShell(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Aman Associates — SERA Workspace")
        self.dismiss_detail_on_outside = False
        # Once the user has toggled the sidebar (button or Ctrl+B) this session, the window width no longer moves it.
        self._sidebar_user_toggled = False

        from pathlib import Path
        from PySide6.QtGui import QIcon
        icon_path = Path(__file__).resolve().parent.parent.parent / "assets" / "logo" / "icon_here.ico"
        if not icon_path.exists():
            icon_path = Path(__file__).resolve().parent.parent.parent / "assets" / "logo" / "icon_here.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        from PySide6.QtWidgets import QApplication
        if QApplication.instance() is not None:
            QApplication.instance().installEventFilter(self)
        self._build_ui()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            if self.isMinimized():
                callback = getattr(self, "on_minimized", None)
            elif event.oldState() & Qt.WindowMinimized:
                callback = getattr(self, "on_restored", None)
            else:
                callback = None
            if callable(callback):
                callback()

    def showEvent(self, event):
        """Back from the tray: refreshes held back while hidden run now (on_restored)."""
        super().showEvent(event)
        callback = getattr(self, "on_restored", None)
        if callable(callback) and not self.isMinimized():
            callback()

    def closeEvent(self, event):
        """Intercept close button (X).

        If 'run_in_background' is enabled the window is hidden to the system
        tray (existing behaviour).  If it is disabled the app quits cleanly.
        """
        if getattr(self, "_force_close", False):
            event.accept()
            return

        run_in_bg = getattr(self, "_run_in_background", True)
        if run_in_bg:
            # Minimise to tray
            event.ignore()
            self.hide()
            if hasattr(self, "on_minimized_to_tray") and callable(self.on_minimized_to_tray):
                self.on_minimized_to_tray()
        else:
            # Hide window immediately so the user experiences an instant shutdown
            self.hide()
            event.accept()
            if hasattr(self, "on_quit_requested") and callable(self.on_quit_requested):
                self.on_quit_requested()
            else:
                from PySide6.QtWidgets import QApplication
                app_inst = QApplication.instance()
                if app_inst:
                    app_inst.quit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "slide_panel") and self.slide_panel:
            self.slide_panel.update_position()
        self._auto_sidebar_for_width()

    def _auto_sidebar_for_width(self):
        """Folds the sidebar away on a narrow window, and opens it again on a wide one. Ignored once the user has toggled it."""
        if getattr(self, "_sidebar_user_toggled", False) or not hasattr(self, "sidebar_collapsed"):
            return
        if not self.sidebar_collapsed and self.width() < SIDEBAR_AUTO_COLLAPSE_BELOW:
            self.collapse_sidebar()
        elif self.sidebar_collapsed and self.width() > SIDEBAR_AUTO_EXPAND_ABOVE:
            self.expand_sidebar()

    def eventFilter(self, watched, event):
        """Close Client Detail when the area outside the slide panel is clicked."""
        if (
            self.dismiss_detail_on_outside
            and event.type() == QEvent.MouseButtonPress
            and hasattr(self, "slide_panel")
            and (getattr(self.slide_panel, "is_open", False) or self.slide_panel.isVisible())
        ):
            from PySide6.QtWidgets import QApplication
            if QApplication.activeModalWidget() is not None:
                return super().eventFilter(watched, event)

            pos = self.mapFromGlobal(event.globalPosition().toPoint())
            if not self.slide_panel.geometry().contains(pos):
                self.dismiss_detail_on_outside = False
                self.slide_panel.slide_out()
                return True
        return super().eventFilter(watched, event)

    def _build_ui(self):
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)
        self.layout.setSizeConstraint(QLayout.SetNoConstraint)

        # 1. Sidebar
        self.sidebar = Sidebar()
        self.sidebar_target_width = 172
        self.sidebar_collapsed = False
        self.sidebar_anim = QPropertyAnimation(self.sidebar, b"maximumWidth", self)
        self.sidebar_anim.setDuration(220)
        self.sidebar_anim.setEasingCurve(QEasingCurve.InOutQuad)
        self.sidebar.toggle_requested.connect(self.toggle_sidebar)
        self.layout.addWidget(self.sidebar)

        # 2. Content Area (Stacked Widget)
        self.content_area = QStackedWidget()
        self.content_area.setMinimumSize(0, 0)
        self.content_area.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.layout.addWidget(self.content_area, stretch=1)

        # 3. Slide Panel (Overlay on top of AppShell)
        from ui.shell.slide_panel import SlidePanel
        self.slide_panel = SlidePanel(parent=self)

        # 4. Sera Alert Notification System
        from ui.components.toast import SeraAlert
        self.alert = SeraAlert(self)
        self.toast = self.alert  # Backward compatibility alias

    def toggle_sidebar(self):
        """Smoothly toggle sidebar between collapsed (0px) and expanded (172px)."""
        self._sidebar_user_toggled = True
        if self.sidebar_collapsed:
            self.expand_sidebar()
        else:
            self.collapse_sidebar()

    def collapse_sidebar(self):
        """Collapse sidebar to 0px."""
        self.sidebar_anim.stop()
        self.sidebar.setMinimumWidth(0)
        self.sidebar_anim.setStartValue(self.sidebar.width())
        self.sidebar_anim.setEndValue(0)
        self.sidebar_collapsed = True
        self.sidebar_anim.start()

    def expand_sidebar(self):
        """Expand sidebar to target width (172px)."""
        self.sidebar_anim.stop()
        self.sidebar_anim.setStartValue(self.sidebar.width())
        self.sidebar_anim.setEndValue(self.sidebar_target_width)
        self.sidebar_collapsed = False
        def _on_expand_finished():
            if not self.sidebar_collapsed:
                self.sidebar.setMinimumWidth(self.sidebar_target_width)
        self.sidebar_anim.finished.connect(_on_expand_finished)
        self.sidebar_anim.start()

    def show_alert(self, message: str, level: str = "success", duration: int = 3000):
        """Displays a non-blocking bottom-left Sera Alert notification."""
        self.alert.show_alert(message, level=level, duration_ms=duration)

    def show_action_alert(self, action: str, client_name: str = None, duration: int = 3000):
        """Maps an audit action to a safe formatted alert message and level."""
        res = ActionAlertFormatter.format(action, client_name)
        if res:
            message, level = res
            self.show_alert(message, level=level, duration=duration)

    def show_toast(self, message: str, duration: int = 3000):
        """Displays a non-blocking toast notification (backward compatibility)."""
        self.alert.show_alert(message, level="info", duration_ms=duration)

    def add_page(self, widget: QWidget):
        """Adds a widget to the main content area."""
        self.content_area.addWidget(widget)

    def set_current_page(self, page):
        """Switches the content area to the specified page index or QWidget with a smooth cross-fade transition."""
        if isinstance(page, QWidget):
            index = self.content_area.indexOf(page)
        else:
            try:
                index = int(page)
            except (ValueError, TypeError):
                return

        if index < 0 or self.content_area.currentIndex() == index:
            return

        new_widget = self.content_area.widget(index)
        if new_widget is None:
            self.content_area.setCurrentIndex(index)
            return

        if getattr(new_widget, "skip_page_fade", False):
            self.content_area.setCurrentIndex(index)
            return

        # Prepare smooth fade-in effect for the incoming page
        effect = QGraphicsOpacityEffect(new_widget)
        new_widget.setGraphicsEffect(effect)
        
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(120)
        anim.setStartValue(0.15)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        
        def _on_fade_finished():
            new_widget.setGraphicsEffect(None)
            
        anim.finished.connect(_on_fade_finished)
        
        self.content_area.setCurrentIndex(index)
        anim.start()
        # Retain reference so animation object is not garbage-collected prematurely
        self._page_anim = anim

