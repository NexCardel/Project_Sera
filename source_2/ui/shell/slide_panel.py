from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QRect,
    Signal,
    Qt,
    QSize,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QIcon
try:
    import qtawesome as qta
except Exception:
    qta = None
from pathlib import Path

BACK_ICON = str(Path(__file__).resolve().parents[2] / "assets" / "icons" / "arrow_back_24dp_E3E3E3_FILL0_wght400_GRAD0_opsz24.svg")


class SlidePanel(QFrame):
    opened = Signal()
    closed = Signal()

    def __init__(self, parent=None, width=650):
        super().__init__(parent)
        self.target_width = width
        self._is_open = False
        self.setObjectName("SlidePanel")
        
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)
        
        self.header = QWidget()
        h_layout = QHBoxLayout(self.header)
        h_layout.setContentsMargins(24, 24, 24, 0)

        self.btn_back = QPushButton()
        if qta:
            self.btn_back.setIcon(qta.icon("mdi.arrow-left", color="#FFFFFF"))
        else:
            self.btn_back.setIcon(QIcon(BACK_ICON))
        self.btn_back.setIconSize(QSize(22, 22))
        self.btn_back.setToolTip("Back")
        self.btn_back.setCursor(Qt.PointingHandCursor)
        self.btn_back.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid #D8CDB4;
                border-radius: 6px;
                padding: 4px 8px;
            }
            QPushButton:hover {
                background-color: #E6DCB8;
            }
        """)
        self.btn_back.clicked.connect(self.slide_out)
        h_layout.addWidget(self.btn_back)

        self.title_label = QLabel("Panel")
        self.title_label.setProperty("class", "SlidePanelTitle")
        
        self.btn_close = QPushButton("✕")
        self.btn_close.setFixedSize(30, 30)
        self.btn_close.setProperty("class", "CloseButton")
        self.btn_close.clicked.connect(self.slide_out)
        
        h_layout.addWidget(self.title_label)
        h_layout.addStretch()
        h_layout.addWidget(self.btn_close)
        
        self.layout.addWidget(self.header)
        self.btn_back.hide()
        
        self.container = QWidget()
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(0, 0, 0, 0)
        self.layout.addWidget(self.container, stretch=1)
        
        self.anim = QPropertyAnimation(self, b"geometry", self)
        self.anim.setDuration(220)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.finished.connect(self._on_anim_finished)

        self.hide()

    @property
    def is_open(self) -> bool:
        return self._is_open

    def update_position(self):
        """Update geometry when parent resizes."""
        if not self.parent():
            return
        parent_w = self.parent().width()
        parent_h = self.parent().height()
        panel_w = min(self.target_width, parent_w)
        if self._is_open:
            self.setGeometry(parent_w - panel_w, 0, panel_w, parent_h)
            self.raise_()
        else:
            self.setGeometry(parent_w, 0, panel_w, parent_h)

    def set_widget(self, widget: QWidget, title: str = "", persistent: bool = False):
        if self.container_layout.count() > 0 and self.container_layout.itemAt(0).widget() == widget:
            pass  # Already set
        else:
            while self.container_layout.count():
                item = self.container_layout.takeAt(0)
                if item.widget():
                    w = item.widget()
                    w.setParent(None)
                    if not getattr(w, '_is_persistent_panel', False):
                        w.deleteLater()
            self.container_layout.addWidget(widget)
            
        if persistent:
            widget._is_persistent_panel = True
        
        if title:
            self.title_label.setText(title)
            self.header.show()
            self.btn_back.show()
        else:
            # If no title is provided, assume the widget has its own header
            self.header.hide()
            self.btn_back.hide()
            
    def slide_in(self):
        self._is_open = True
        self.opened.emit()
        self.show()
        self.raise_()
        if not self.parent():
            return
        parent_w = self.parent().width()
        parent_h = self.parent().height()
        panel_w = min(self.target_width, parent_w)

        self.anim.stop()
        start_geom = self.geometry()
        if not self.isVisible() or start_geom.width() <= 0 or not start_geom.isValid() or start_geom.x() >= parent_w:
            start_geom = QRect(parent_w, 0, panel_w, parent_h)
        else:
            start_geom = QRect(start_geom.x(), 0, panel_w, parent_h)
        
        end_geom = QRect(parent_w - panel_w, 0, panel_w, parent_h)
        self.anim.setStartValue(start_geom)
        self.anim.setEndValue(end_geom)
        self.anim.start()

    def slide_out(self):
        if not self._is_open and not self.isVisible():
            return
        self._is_open = False
        self.closed.emit()
        if not self.parent():
            self.hide()
            return
        parent_w = self.parent().width()
        parent_h = self.parent().height()
        panel_w = min(self.target_width, parent_w)

        self.anim.stop()
        start_geom = self.geometry()
        end_geom = QRect(parent_w, 0, panel_w, parent_h)
        self.anim.setStartValue(start_geom)
        self.anim.setEndValue(end_geom)
        self.anim.start()

    def _on_anim_finished(self):
        if not self._is_open:
            self.hide()
        else:
            self.raise_()
