"""
ui/os_assist/dock_card.py
-------------------------
The docked, no-focus base window every OS Assist card is built on (blueprint §4.4).

- Frameless, translucent, a tool window (no taskbar entry) that never takes focus
  (Qt.WindowDoesNotAcceptFocus, WA_ShowWithoutActivating and WS_EX_NOACTIVATE), so a button click
  leaves the browser focused.
- `attach(browser_hwnd, service)` makes the browser window its owner (GWLP_HWNDPARENT): above that
  browser only, minimises with it.
- While presented, a 100 ms tick follows the browser's GetWindowRect and docks the card top-right
  of the page area (top 18 px, right 24 px, as the in-page card). The page rect and the address-bar
  host come from the UIA worker (locate.page_rect / host_matches_service), asked at most every
  500 ms, never on the Qt thread. Hidden while the browser is minimised or the host stops matching
  the service (shown again when it matches; the timer keeps running); dismissed when it closes.
- Shared pieces: slide-in from the right, `TimerBar`, header with badge + ✎ + ✕, and `dismissed`,
  emitted after every secret the card holds has been dropped.

No card content here; smti_card / mecp_card / sca_card fill `self.body`.
"""
import ctypes
import sys
import time
from ctypes import wintypes
from typing import Callable, Optional, Tuple

from PySide6.QtCore import (Qt, QTimer, QPoint, QPointF, QRectF, QAbstractAnimation,
                            QPropertyAnimation, QVariantAnimation, QEasingCurve, Signal)
from PySide6.QtGui import QColor, QFont, QPainter, QGuiApplication
from PySide6.QtWidgets import QWidget, QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton

from ui.utils.ui_scale import running_scale

Rect = Tuple[int, int, int, int]                 # (left, top, right, bottom), physical pixels

TICK_MS = 100
PROBE_EVERY_S = 0.5
TOP_PX, RIGHT_PX = 18, 24                        # the in-page card's `top: 18px; right: 24px`
SLIDE_MS, FADE_MS = 380, 320
CARD_MIN_PX, CARD_MAX_PX = 320, 420
RESUME_DISMISS_MS = 500                          # resuming with this little left dismisses (as the extension)

GWL_EXSTYLE = -20
GWLP_HWNDPARENT = -8
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080


def css_px(v: float) -> int:
    """A CSS pixel of the in-page card in Qt logical pixels. Qt already applies the monitor DPI;
    the app's own UI scale (QT_SCALE_FACTOR, ui_scale) is undone so the card matches the browser."""
    return max(1, round(v / (running_scale() or 1.0)))


def slide_curve() -> QEasingCurve:
    """cubic-bezier(0.16, 1, 0.3, 1), the extension's slide-in."""
    curve = QEasingCurve(QEasingCurve.BezierSpline)
    curve.addCubicBezierSegment(QPointF(0.16, 1.0), QPointF(0.3, 1.0), QPointF(1.0, 1.0))
    return curve


def to_logical(x: int, y: int) -> QPoint:
    """A physical screen point (Win32 / UIA) in Qt logical coordinates, per monitor."""
    for screen in QGuiApplication.screens():
        g = screen.geometry()
        dpr = screen.devicePixelRatio() or 1.0
        if g.x() <= x < g.x() + g.width() * dpr and g.y() <= y < g.y() + g.height() * dpr:
            return QPoint(round(g.x() + (x - g.x()) / dpr), round(g.y() + (y - g.y()) / dpr))
    screen = QGuiApplication.primaryScreen()
    dpr = (screen.devicePixelRatio() if screen else 1.0) or 1.0
    return QPoint(round(x / dpr), round(y / dpr))


class Win32:
    """The few window calls the card makes on the Qt thread (no UIA). Tests pass a fake."""

    def __init__(self) -> None:
        self._u = None
        if sys.platform == "win32":
            u = ctypes.windll.user32
            ptr = getattr(u, "SetWindowLongPtrW", None) is not None
            self._get = u.GetWindowLongPtrW if ptr else u.GetWindowLongW
            self._set = u.SetWindowLongPtrW if ptr else u.SetWindowLongW
            self._get.restype = ctypes.c_ssize_t
            self._get.argtypes = [wintypes.HWND, ctypes.c_int]
            self._set.restype = ctypes.c_ssize_t
            self._set.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
            self._u = u

    def is_window(self, hwnd: int) -> bool:
        return bool(self._u and hwnd and self._u.IsWindow(wintypes.HWND(hwnd)))

    def is_iconic(self, hwnd: int) -> bool:
        return bool(self._u and hwnd and self._u.IsIconic(wintypes.HWND(hwnd)))

    def window_rect(self, hwnd: int) -> Optional[Rect]:
        if not (self._u and hwnd):
            return None
        r = wintypes.RECT()
        if not self._u.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r)):
            return None
        return (r.left, r.top, r.right, r.bottom)

    def add_ex_style(self, hwnd: int, bits: int) -> None:
        if self._u and hwnd:
            self._set(hwnd, GWL_EXSTYLE, int(self._get(hwnd, GWL_EXSTYLE)) | bits)

    def set_owner(self, hwnd: int, owner: int) -> None:
        if self._u and hwnd:
            self._set(hwnd, GWLP_HWNDPARENT, int(owner or 0))


def _probe(hwnd: int, service) -> Tuple[bool, Optional[Rect]]:
    from core.os_assist import locate
    return locate.host_matches_service(hwnd, service), locate.page_rect(hwnd)


class WorkerLocator:
    """Asks the UIA worker for (host matches the service, page rect). Returns a Future."""

    def probe(self, hwnd: int, service):
        from core.os_assist import worker
        return worker.submit(_probe, hwnd, service)


class TimerBar(QWidget):
    """The countdown bar: shrinks from the left (scaleX) over the card's life. Pauses on hover
    (the card calls pause/resume), resets on click, emits `expired` when it runs out."""

    expired = Signal()

    TRACK = QColor(255, 255, 255, 20)            # rgba(255, 255, 255, 0.08)
    BAR = QColor("#2E9B5F")

    def __init__(self, duration_ms: int, parent=None, clock: Callable[[], float] = time.monotonic):
        super().__init__(parent)
        self.duration_ms = int(duration_ms)
        self._clock = clock
        self._remaining = float(self.duration_ms)
        self._started_at: Optional[float] = None     # set while running
        self._paused = False
        self._done = False
        self.setFixedHeight(max(2, round(css_px(3.5))))
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)
        self._expiry = QTimer(self)
        self._expiry.setSingleShot(True)
        self._expiry.timeout.connect(self._expire)
        self._paint = QVariantAnimation(self)        # only drives repaints
        self._paint.setStartValue(0.0)
        self._paint.setEndValue(1.0)
        self._paint.valueChanged.connect(lambda _v: self.update())

    # -- state ---------------------------------------------------------------------------------
    def remaining_ms(self) -> float:
        if self._started_at is None:
            return max(0.0, self._remaining)
        return max(0.0, self._remaining - (self._clock() - self._started_at) * 1000.0)

    def fraction(self) -> float:
        return self.remaining_ms() / self.duration_ms if self.duration_ms > 0 else 0.0

    @property
    def running(self) -> bool:
        return self._started_at is not None

    @property
    def paused(self) -> bool:
        return self._paused

    # -- control -------------------------------------------------------------------------------
    def start(self) -> None:
        if self._done:
            return
        self._paused = False
        self._started_at = self._clock()
        ms = max(0, round(self._remaining))
        self._expiry.start(ms)
        self._paint.stop()
        self._paint.setDuration(max(1, ms))
        self._paint.start()

    def pause(self) -> None:
        if self._paused or not self.running or self._done:
            return
        self._remaining = self.remaining_ms()
        self._started_at = None
        self._paused = True
        self._expiry.stop()
        self._paint.stop()
        self.update()

    def resume(self) -> None:
        if not self._paused or self._done:
            return
        if self._remaining <= RESUME_DISMISS_MS:
            self._expire()
            return
        self.start()

    def reset(self) -> None:
        if self._done:
            return
        self._remaining = float(self.duration_ms)
        self.start()

    def stop(self) -> None:
        self._done = True
        self._started_at = None
        self._expiry.stop()
        self._paint.stop()

    def _expire(self) -> None:
        if self._done:
            return
        self._remaining = 0.0
        self.stop()
        self.update()
        self.expired.emit()

    # -- Qt ------------------------------------------------------------------------------------
    def mousePressEvent(self, event):
        self.reset()
        event.accept()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        r = QRectF(self.rect())
        radius = min(2.0, r.height() / 2)
        p.setBrush(self.TRACK)
        p.drawRoundedRect(r, radius, radius)
        w = r.width() * self.fraction()
        if w > 0:
            p.setBrush(self.BAR)
            p.drawRect(QRectF(r.left(), r.top(), w, r.height()))
        p.end()


class DockCard(QWidget):
    """Base for the OS Assist cards. Subclasses add their content to `self.body`, keep any secret
    with hold_secret(), call `self.timer.reset()` on their button clicks, and end with dismiss()."""

    dismissed = Signal(str)           # reason: "close" | "timeout" | "browser_closed" | the caller's
    edit_requested = Signal()         # ✎ -> the SCK widget for this client and service (D3)

    BADGE = "⚡ Sera Assist"
    ACCENT = "#2E9B5F"

    def __init__(self, badge: Optional[str] = None, timeout_ms: Optional[int] = None,
                 locator=None, win: Optional[Win32] = None,
                 clock: Callable[[], float] = time.monotonic, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setFocusPolicy(Qt.NoFocus)

        self._locator = locator if locator is not None else WorkerLocator()
        self._win = win if win is not None else Win32()
        self._clock = clock
        self._secrets: dict = {}
        self._dismissed = False
        self._presented = False
        self._slid_in = False
        self.browser_hwnd = 0
        self.service = None
        self._host_ok = True          # the launcher found the window on this host; the first probe confirms
        self._page_inset: Optional[Rect] = None   # page rect relative to the window rect
        self._probe_job = None
        self._last_probe = float("-inf")
        self.hidden_reason = ""       # "" | "minimised" | "host"

        self._build(badge or self.BADGE, timeout_ms)

        self._tick = QTimer(self)
        self._tick.setInterval(TICK_MS)
        self._tick.timeout.connect(self._follow)

        self._slide = QPropertyAnimation(self.card, b"pos", self)
        self._slide.setDuration(SLIDE_MS)
        self._slide.setEasingCurve(slide_curve())
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(FADE_MS)
        self._fade.setEasingCurve(QEasingCurve.InOutQuad)
        self._slide_out_done = False

    # -- look ----------------------------------------------------------------------------------
    def _build(self, badge: str, timeout_ms: Optional[int]) -> None:
        self.card = QFrame(self)
        self.card.setObjectName("card")
        self.card.setStyleSheet(self.card_qss())
        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(css_px(18), css_px(16), css_px(18), css_px(16))
        lay.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(css_px(2))
        self.badge = QLabel(badge.upper(), self.card)          # QSS has no text-transform / letter-spacing
        self.badge.setObjectName("badge")
        f = self.badge.font()
        f.setLetterSpacing(QFont.AbsoluteSpacing, 0.7)
        self.badge.setFont(f)
        self.edit_btn = self._head_button("✎", "Enter a new password")
        self.close_btn = self._head_button("✕", "Dismiss")
        self.edit_btn.clicked.connect(self.edit_requested.emit)
        self.close_btn.clicked.connect(lambda: self.dismiss("close"))
        header.addWidget(self.badge)
        header.addStretch(1)
        header.addWidget(self.edit_btn)
        header.addWidget(self.close_btn)
        lay.addLayout(header)
        lay.addSpacing(css_px(8))

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(css_px(9))
        lay.addLayout(self.body)

        self.timer: Optional[TimerBar] = None
        if timeout_ms:
            lay.addSpacing(css_px(12))
            self.timer = TimerBar(timeout_ms, self.card, clock=self._clock)
            self.timer.BAR = QColor(self.ACCENT)
            self.timer.expired.connect(lambda: self.dismiss("timeout"))
            lay.addWidget(self.timer)

    def _head_button(self, text: str, tip: str) -> QPushButton:
        b = QPushButton(text, self.card)
        b.setObjectName("head")
        b.setToolTip(tip)
        b.setFocusPolicy(Qt.NoFocus)
        b.setCursor(Qt.PointingHandCursor)
        return b

    def card_qss(self) -> str:
        """The extension's `.card`, `.badge`, `.close-btn`/`.edit-btn` CSS. Cards may override."""
        px = css_px
        return f"""
            QFrame#card {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #121815, stop:1 #0B120E);
                border: {max(1, px(1.5))}px solid {self.ACCENT};
                border-radius: {px(14)}px;
                color: #FFFFFF;
                font-family: 'Segoe UI';
            }}
            QLabel#badge {{
                font-size: {px(11)}px; font-weight: 700;
                color: #4CF9B7; background: rgba(46, 155, 95, 0.22);
                border: 1px solid rgba(76, 249, 183, 0.35);
                padding: {px(3.5)}px {px(9)}px; border-radius: {px(6)}px;
            }}
            QPushButton#head {{
                background: transparent; border: none; color: #7E9388;
                font-size: {px(16)}px; padding: {px(2)}px {px(6)}px; border-radius: {px(4)}px;
            }}
            QPushButton#head:hover {{ color: #FFFFFF; background: rgba(255, 255, 255, 0.1); }}
        """

    def refit(self) -> None:
        """Size the window to the card (320-420 CSS px wide). Call after adding content."""
        self.card.adjustSize()
        hint = self.card.sizeHint()
        w = min(max(hint.width(), css_px(CARD_MIN_PX)), css_px(CARD_MAX_PX))
        h = self.card.heightForWidth(w) if self.card.hasHeightForWidth() else hint.height()
        self.card.resize(w, max(h, hint.height()))
        self.resize(self.card.size())
        if self._slide.state() != QAbstractAnimation.Running:
            self.card.move(0 if self._slid_in else self._off_x(), 0)

    def _off_x(self) -> int:
        return round(self.card.width() * 1.2)       # translateX(120%)

    # -- secrets -------------------------------------------------------------------------------
    def hold_secret(self, key: str, value) -> None:
        """Memory only, for the card's life (rule 4)."""
        if not self._dismissed:
            self._secrets[key] = value

    def secret(self, key: str):
        return self._secrets.get(key)

    def has_secrets(self) -> bool:
        return bool(self._secrets)

    def clear_secrets(self) -> None:
        for key in list(self._secrets):
            self._secrets[key] = None
        self._secrets.clear()

    # -- docking -------------------------------------------------------------------------------
    def attach(self, browser_hwnd: int, service) -> None:
        """Dock to that browser window for that service (a dict or object with login_page_link)."""
        self.browser_hwnd = int(browser_hwnd or 0)
        self.service = service
        self._apply_native()

    def _native_hwnd(self) -> int:
        if QGuiApplication.platformName() != "windows":
            return 0
        return int(self.winId())                     # creates the native window if needed

    def _apply_native(self) -> None:
        hwnd = self._native_hwnd()
        if not hwnd:
            return
        self._win.add_ex_style(hwnd, WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        if self.browser_hwnd:
            self._win.set_owner(hwnd, self.browser_hwnd)

    def present(self) -> None:
        """Start following the browser and slide in. attach() first."""
        if self._dismissed or self._presented:
            return
        self._presented = True
        self.refit()
        if self.timer is not None:
            self.timer.start()
        self._tick.start()
        self._follow()

    def _follow(self) -> None:
        if self._dismissed:
            return
        h = self.browser_hwnd
        if not self._win.is_window(h):
            self.dismiss("browser_closed")
            return
        if self._win.is_iconic(h):
            self._set_hidden("minimised")
            return
        wr = self._win.window_rect(h)
        self._ask_probe()
        self._collect_probe(wr)
        if not self._host_ok:
            self._set_hidden("host")
            return
        if wr is not None:
            self._dock_to(wr)
        self._set_hidden("")

    def _ask_probe(self) -> None:
        now = self._clock()
        if self._probe_job is not None or now - self._last_probe < PROBE_EVERY_S:
            return
        self._last_probe = now
        try:
            self._probe_job = self._locator.probe(self.browser_hwnd, self.service)
        except Exception:
            self._probe_job = None
            self._host_ok = False

    def _collect_probe(self, wr: Optional[Rect]) -> None:
        job = self._probe_job
        if job is None:
            return
        if hasattr(job, "done"):
            if not job.done():
                return
            try:
                host_ok, page = job.result()
            except Exception:
                host_ok, page = False, None
        else:
            host_ok, page = job
        self._probe_job = None
        self._host_ok = bool(host_ok)
        if page is not None and wr is not None:
            self._page_inset = (page[0] - wr[0], page[1] - wr[1], page[2] - wr[2], page[3] - wr[3])

    def _dock_to(self, wr: Rect) -> None:
        """Top-right of the page area: the page rect's last known place inside the window,
        moved with the window, so a drag is followed every tick, not every probe."""
        if self._page_inset is not None:
            top, right = wr[1] + self._page_inset[1], wr[2] + self._page_inset[2]
        else:
            top, right = wr[1], wr[2]
        tr = to_logical(right, top)
        pos = QPoint(tr.x() - css_px(RIGHT_PX) - self.width(), tr.y() + css_px(TOP_PX))
        if pos != self.pos():
            self.move(pos)

    def _set_hidden(self, reason: str) -> None:
        self.hidden_reason = reason
        if reason:
            if self.isVisible():
                self.hide()
            return
        if not self.isVisible():
            self.show()
            self._apply_native()
            if not self._slid_in:
                self._slide_in()

    # -- in and out ----------------------------------------------------------------------------
    def _slide_in(self) -> None:
        self._slid_in = True
        self._slide.stop()
        self._slide.setStartValue(QPoint(self._off_x(), 0))
        self._slide.setEndValue(QPoint(0, 0))
        self._slide.start()
        self._fade.stop()
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()

    def dismiss(self, reason: str = "close") -> None:
        """Drop every secret at once, emit `dismissed`, slide out and hide. Safe to call twice."""
        if self._dismissed:
            return
        self._dismissed = True
        self.clear_secrets()
        self._tick.stop()
        if self.timer is not None:
            self.timer.stop()
        self._probe_job = None
        self.dismissed.emit(reason)
        if not self.isVisible():
            return
        self._slide.stop()
        self._slide.setStartValue(self.card.pos())
        self._slide.setEndValue(QPoint(self._off_x(), 0))
        self._slide.finished.connect(self._after_slide_out)
        self._slide.start()
        self._fade.stop()
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _after_slide_out(self) -> None:
        if self._slide_out_done:
            return
        self._slide_out_done = True
        self.hide()

    @property
    def is_dismissed(self) -> bool:
        return self._dismissed

    # -- Qt ------------------------------------------------------------------------------------
    def showEvent(self, event):
        super().showEvent(event)
        self._apply_native()

    def enterEvent(self, event):
        if self.timer is not None:
            self.timer.pause()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self.timer is not None:
            self.timer.resume()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if self.timer is not None:
            self.timer.reset()
        event.accept()
