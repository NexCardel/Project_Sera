from PySide6.QtWidgets import QHBoxLayout, QLayout, QSizePolicy, QStackedWidget, QWidget, QGraphicsBlurEffect, QGraphicsOpacityEffect
from PySide6.QtCore import QPropertyAnimation, QEasingCurve, QEvent


from ui.shell.sidebar import Sidebar
from ui.services.alert_service import ActionAlertFormatter


class AppShell(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Aman Associates — SERA Workspace")
        self.dismiss_detail_on_outside = False

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

    def eventFilter(self, watched, event):
        """Close Client Detail when the blurred area/sidebar is clicked."""
        if (
            self.dismiss_detail_on_outside
            and event.type() == QEvent.MouseButtonPress
            and self.slide_panel.width() > 0
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
        self.layout.addWidget(self.sidebar)

        # 2. Content Area (Stacked Widget)
        self.content_area = QStackedWidget()
        self.content_area.setMinimumSize(0, 0)
        self.content_area.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.layout.addWidget(self.content_area, stretch=1)

        # 3. Slide Panel
        from ui.shell.slide_panel import SlidePanel
        self.slide_panel = SlidePanel()
        self.layout.addWidget(self.slide_panel)

        # 4. Blur Effect for Content Area when Slide Panel opens
        self.blur_effect = QGraphicsBlurEffect(self.content_area)
        self.blur_effect.setBlurRadius(0.0)
        self.blur_effect.setEnabled(False)
        self.content_area.setGraphicsEffect(self.blur_effect)
        
        self.blur_anim = QPropertyAnimation(self.blur_effect, b"blurRadius", self)
        self.blur_anim.setDuration(220)
        self.blur_anim.setEasingCurve(QEasingCurve.OutCubic)
        self.blur_anim.finished.connect(self._on_blur_anim_finished)

        self.slide_panel.opened.connect(self._apply_blur)
        self.slide_panel.closed.connect(self._remove_blur)

        # 5. Sera Alert Notification System
        from ui.components.toast import SeraAlert
        self.alert = SeraAlert(self)
        self.toast = self.alert  # Backward compatibility alias

    def _apply_blur(self):
        """Smoothly blurs the content area when the client detail slide panel opens."""
        self.blur_effect.setEnabled(True)
        self.blur_anim.stop()
        curr = self.blur_effect.blurRadius()
        if curr >= 4.0:
            self.blur_effect.setBlurRadius(4.0)
            return
        self.blur_anim.setStartValue(curr)
        self.blur_anim.setEndValue(4.0)  # Subtle, crisp background softening
        self.blur_anim.start()


    def _remove_blur(self):
        """Smoothly un-blurs the content area when the slide panel closes."""
        self.blur_anim.stop()
        curr = self.blur_effect.blurRadius()
        if curr <= 0.0:
            self.blur_effect.setBlurRadius(0.0)
            self.blur_effect.setEnabled(False)
            return
        self.blur_anim.setStartValue(curr)
        self.blur_anim.setEndValue(0.0)
        self.blur_anim.start()

    def _on_blur_anim_finished(self):
        """Disable blur effect after un-blurring to preserve crisp font rendering."""
        if self.blur_effect.blurRadius() <= 0.5:
            self.blur_effect.setBlurRadius(0.0)
            self.blur_effect.setEnabled(False)

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

        # Prepare smooth fade-in effect for the incoming page
        effect = QGraphicsOpacityEffect(new_widget)
        new_widget.setGraphicsEffect(effect)
        
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(180)
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

