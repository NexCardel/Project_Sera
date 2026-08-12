from PySide6.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPropertyAnimation,
    Signal,
    Qt,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
try:
    import qtawesome as qta
except Exception:
    qta = None
from PySide6.QtCore import QSize
from pathlib import Path

BACK_ICON = str(Path(__file__).resolve().parents[2] / "Version SKY" / "Sera_SVG" / "arrow_back_24dp_E3E3E3_FILL0_wght400_GRAD0_opsz24.svg")


class SlidePanel(QFrame):
    opened = Signal()
    closed = Signal()

    def __init__(self, parent=None, width=650):
        super().__init__(parent)
        self.target_width = width
        self.setFixedWidth(0)
        self.setObjectName("SlidePanel")
        
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)
        
        self.header = QWidget()
        h_layout = QHBoxLayout(self.header)
        h_layout.setContentsMargins(24, 24, 24, 0)

        self.btn_back = QPushButton()
        self.btn_back.setIcon(qta.icon("mdi.arrow-left", color="#000000"))
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
        
        self.anim_min = QPropertyAnimation(self, b"minimumWidth")
        self.anim_max = QPropertyAnimation(self, b"maximumWidth")
        self.anim_group = QParallelAnimationGroup()
        self.anim_group.addAnimation(self.anim_min)
        self.anim_group.addAnimation(self.anim_max)
        
        for anim in [self.anim_min, self.anim_max]:
            anim.setDuration(220)
            anim.setEasingCurve(QEasingCurve.OutCubic)

    def set_widget(self, widget: QWidget, title: str = "", persistent: bool = False):
        if self.container_layout.count() > 0 and self.container_layout.itemAt(0).widget() == widget:
            pass # Already set
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
        self.opened.emit()
        if self.width() == self.target_width:
            return
        self.anim_min.setStartValue(self.width())
        self.anim_min.setEndValue(self.target_width)
        self.anim_max.setStartValue(self.width())
        self.anim_max.setEndValue(self.target_width)
        self.anim_group.start()

    def slide_out(self):
        self.closed.emit()
        if self.width() == 0:
            return
        self.anim_min.setStartValue(self.width())
        self.anim_min.setEndValue(0)
        self.anim_max.setStartValue(self.width())
        self.anim_max.setEndValue(0)
        self.anim_group.start()
