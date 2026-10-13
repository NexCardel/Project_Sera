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
from PySide6.QtGui import QIcon, QPixmap
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
        
        # The slide animates a snapshot of the panel (self._proxy), not the panel: repainting a full
        # client profile on every frame cost ~27 ms (7 frames in 220 ms, worst gap 70 ms), a
        # snapshot costs next to nothing. The real panel is parked off-screen meanwhile and put in
        # place when the slide ends.
        self._proxy = QLabel(parent)
        self._proxy.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._proxy.hide()
        self.anim = QPropertyAnimation(self._proxy, b"geometry", self)
        self.anim.setDuration(220)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.finished.connect(self._on_anim_finished)

        self.hide()

    @property
    def is_open(self) -> bool:
        return self._is_open

    def _calc_panel_width(self, parent_w: int) -> int:
        if parent_w <= 0:
            return self.target_width
        # On compact/laptop displays (e.g. 1280-1366px or 125%/150% scaling),
        # allocate ~42-44% of window width (min 380px) so the main content is not squeezed out.
        responsive_w = int(parent_w * 0.44)
        clamped_w = max(380, min(self.target_width, responsive_w))
        return min(clamped_w, parent_w)

    def update_position(self):
        """Update geometry when parent resizes."""
        if not self.parent():
            return
        parent_w = self.parent().width()
        parent_h = self.parent().height()
        panel_w = self._calc_panel_width(parent_w)
        if self.anim.state() == QPropertyAnimation.Running:
            return                                  # the slide's end puts the panel in place
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
            
    def _end_geometry(self) -> QRect:
        parent_w, parent_h = self.parent().width(), self.parent().height()
        panel_w = self._calc_panel_width(parent_w)
        return QRect(parent_w - panel_w, 0, panel_w, parent_h)

    def _off_geometry(self) -> QRect:
        parent_w, parent_h = self.parent().width(), self.parent().height()
        return QRect(parent_w, 0, self._calc_panel_width(parent_w), parent_h)

    def _snapshot(self, geom: QRect) -> QPixmap:
        """The panel as it looks at `geom` (laid out at that size, then drawn once)."""
        self.setGeometry(geom)
        self.show()
        return self.grab()

    def _start_proxy_slide(self, pixmap: QPixmap, start: QRect, end: QRect):
        if pixmap.isNull():
            return False
        self._proxy.setPixmap(pixmap)
        self._proxy.setGeometry(start)
        self._proxy.show()
        self._proxy.raise_()
        self.setGeometry(self._off_geometry())      # parked; the proxy stands in for it
        self.anim.stop()
        self.anim.setStartValue(start)
        self.anim.setEndValue(end)
        self.anim.start()
        return True

    def _finish_proxy(self):
        self._proxy.hide()
        self._proxy.setPixmap(QPixmap())

    def slide_in(self):
        self._is_open = True
        self.opened.emit()
        if not self.parent():
            self.show()
            self.raise_()
            return
        end_geom = self._end_geometry()
        sliding = self.anim.state() == QPropertyAnimation.Running
        if not sliding and self.isVisible() and self.geometry() == end_geom:
            self.raise_()                           # already open (another client picked): nothing to slide
            return

        start_geom = self._proxy.geometry() if sliding else self._off_geometry()
        self.anim.stop()
        if not self._start_proxy_slide(self._snapshot(end_geom), start_geom, end_geom):
            self.setGeometry(end_geom)              # no snapshot possible: show it in place
            self.raise_()

    def slide_out(self):
        if not self._is_open and not self.isVisible():
            return
        self._is_open = False
        self.closed.emit()
        if not self.parent():
            self.hide()
            return
        sliding = self.anim.state() == QPropertyAnimation.Running
        start_geom = self._proxy.geometry() if sliding else self.geometry()
        self.anim.stop()
        off = self._off_geometry()
        if sliding:
            # reverse from where the proxy is: it already carries the picture
            self.anim.setStartValue(start_geom)
            self.anim.setEndValue(off)
            self.anim.start()
            return
        if not self._start_proxy_slide(self.grab(), start_geom, off):
            self.hide()

    def _on_anim_finished(self):
        self._finish_proxy()
        if self._is_open:
            self.setGeometry(self._end_geometry())
            self.show()
            self.raise_()
        else:
            self.hide()
