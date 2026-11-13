from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QRect,
    Signal,
    Qt,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QPixmap

from ui.components.kit import IconButton
from ui.utils.responsive import WidthMode, mode_for
from ui.utils.tokens import KIT, TOKENS


def _panel_qss(compact):
    edge = "none" if compact else f"1px solid {TOKENS['line2']}"
    return (
        f"QFrame#SlidePanel {{ background: {TOKENS['panel']}; border: none; border-left: {edge}; }}"
        f"QFrame#SlidePanelHead {{ background: transparent; border: none;"
        f" border-bottom: 1px solid {TOKENS['line']}; }}"
        f"QWidget#SlidePanelBody {{ background: transparent; }}"
        f"QLabel#SlidePanelTitle {{ color: {TOKENS['text']}; background: transparent; border: none;"
        f" font-size: {TOKENS['f_xl']}px; font-weight: 700; }}"
    )


class SlidePanel(QFrame):
    opened = Signal()
    closed = Signal()

    def __init__(self, parent=None, width=KIT["w_panel"]):
        super().__init__(parent)
        self.target_width = width
        self._is_open = False
        self._compact = None            # None: follow the parent's width until a mode is set
        self._auto_mode = WidthMode.WIDE
        self.setObjectName("SlidePanel")
        self.setStyleSheet(_panel_qss(False))

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)

        self.header = QFrame()
        self.header.setObjectName("SlidePanelHead")
        h_layout = QHBoxLayout(self.header)
        h_layout.setContentsMargins(KIT["pad_dlg_h"], KIT["pad_panel_top"], KIT["pad_dlg_h"], KIT["pad_panel_bot"])
        h_layout.setSpacing(TOKENS["s_3"])

        self.btn_back = IconButton("mdi.arrow-left", "Back", variant="bordered")
        self.btn_back.clicked.connect(self.slide_out)
        h_layout.addWidget(self.btn_back)

        self.title_label = QLabel("Panel")
        self.title_label.setObjectName("SlidePanelTitle")

        self.btn_close = IconButton("mdi.close", "Close", variant="bordered")
        self.btn_close.clicked.connect(self.slide_out)

        h_layout.addWidget(self.title_label)
        h_layout.addStretch()
        h_layout.addWidget(self.btn_close)

        self.layout.addWidget(self.header)
        self.btn_back.hide()

        self.container = QWidget()
        self.container.setObjectName("SlidePanelBody")
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

    def is_compact(self) -> bool:
        if self._compact is not None:
            return self._compact
        return self._auto_mode is WidthMode.COMPACT

    def set_compact(self, on: bool):
        self._compact = bool(on)
        self._restyle()
        self.update_position()

    def apply_width_mode(self, mode):
        self.set_compact(str(getattr(mode, "value", mode)).lower() == "compact")

    def _restyle(self):
        self.setStyleSheet(_panel_qss(self.is_compact()))

    def _calc_panel_width(self, parent_w: int) -> int:
        if parent_w <= 0:
            return self.target_width
        if self._compact is None:
            mode = mode_for(parent_w, self._auto_mode)
            if mode is not self._auto_mode:
                self._auto_mode = mode
                self._restyle()
        if self.is_compact():
            return parent_w
        return min(self.target_width, parent_w)

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
