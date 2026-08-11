from PySide6.QtCore import QPropertyAnimation, Qt, QTimer
from PySide6.QtWidgets import QGraphicsOpacityEffect, QHBoxLayout, QLabel, QFrame
import qtawesome as qta


class SeraAlert(QFrame):
    """
    Sera Alert component: Non-modal, bottom-left floating notification widget
    with action-specific level colors and Google Material Design icons.
    """
    LEVEL_ICONS = {
        "success": ("mdi.check-circle", "#1B5E20"),
        "info": ("mdi.information", "#0D47A1"),
        "warning": ("mdi.alert", "#E65100"),
        "error": ("mdi.alert-circle", "#B71C1C"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SeraAlert")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 16, 10)
        layout.setSpacing(8)
        
        self.icon_label = QLabel(self)
        self.icon_label.setProperty("class", "SeraAlertIcon")
        layout.addWidget(self.icon_label)
        
        self.label = QLabel("", self)
        self.label.setProperty("class", "SeraAlertLabel")
        layout.addWidget(self.label)
        
        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity_effect)
        self.opacity_effect.setOpacity(0.0)
        
        self.anim = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.anim.setDuration(250)
        self.anim.finished.connect(self._on_hide_finished)
        
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide_alert)
        
        self.hide()

    def show_alert(self, message: str, level: str = "success", duration_ms: int = 3000):
        level = level.lower() if level in ("success", "info", "warning", "error") else "success"
        self.setProperty("level", level)
        
        # Google Material Design Icon matching level
        icon_name, color = self.LEVEL_ICONS.get(level, ("mdi.check-circle", "#1B5E20"))
        try:
            pixmap = qta.icon(icon_name, color=color).pixmap(20, 20)
            self.icon_label.setPixmap(pixmap)
            self.icon_label.show()
        except Exception:
            self.icon_label.hide()
            
        self.label.setText(message)
        
        # Refresh stylesheet properties
        self.style().unpolish(self)
        self.style().polish(self)
        self.adjustSize()
        
        # Bottom-Left positioning with safe 24px padding
        if self.parent():
            p_geom = self.parent().rect()
            x = 24
            y = p_geom.height() - self.height() - 24
            self.move(x, y)
            
        self.show()
        self.raise_()
        
        self.anim.setStartValue(self.opacity_effect.opacity())
        self.anim.setEndValue(1.0)
        self.anim.start()
        
        self.timer.start(duration_ms)

    def show_message(self, message: str, duration_ms: int = 3000, level: str = "info"):
        self.show_alert(message, level=level, duration_ms=duration_ms)

    def hide_alert(self):
        self.anim.setStartValue(self.opacity_effect.opacity())
        self.anim.setEndValue(0.0)
        self.anim.start()

    def hide_toast(self):
        self.hide_alert()

    def _on_hide_finished(self):
        if self.opacity_effect.opacity() == 0.0:
            self.hide()


class ToastNotification(SeraAlert):
    """Backward-compatible alias for SeraAlert."""
    pass
