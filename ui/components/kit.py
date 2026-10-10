"""
ui/components/kit.py — the shared UI kit of the fleet redesign
================================================================
Plain Qt widgets drawn from ui/utils/tokens.py only (no colour, radius or size literal here).
Screens import these and compose them; they never restyle or subclass them to change the look.
K-7 turns this docstring into the full reference; the leaves so far (K-1):

LabelledAction(text, parent=None, *, variant="secondary", icon=None, sm=False, tooltip="")
    Icon + text button. variant: primary (one per screen), soft, secondary, ghost, danger
    (destructive only). icon: an "mdi.*" name or a QIcon.
    set_disabled_reason(reason)  disable with the reason as tooltip; None re-enables and puts the
                                 old tooltip back. set_icon_only(on)  hides the text (COMPACT bars),
                                 the text moves to the tooltip. set_swatch(colour)  Fill / Text colour
                                 square after the icon; None removes it. set_variant(variant).
IconButton(icon, tooltip, parent=None, *, variant="plain", sm=False)
    Square icon button; variant plain, bordered, primary. tooltip is required (ValueError).
    set_disabled_reason(reason) as above.
Segmented(options, parent=None, current=0)
    One-of-n switch; one focus stop, Left / Right move. current_index(), set_current(i),
    signal currentChanged(int) (also on set_current when the index changes).
Key(text, parent=None, *, light=False)
    Keyboard hint ("Ctrl+B"); light=True for white grid footers.
Pill(text, tone="neutral", parent=None, *, dot=False)
    Tones: neutral, emerald, amber, red, blue, purple, orange. dot=True adds a StatusDot in the
    matching tone. Long text elides; the full text is the tooltip and property "full_text".
Tag(text, tone="neutral", parent=None)
    Small mono label; tones neutral, emerald, amber, red, blue. Elides like Pill.
StatusDot(tone="ok", parent=None)
    8 px dot with a soft halo; tones ok, warn, bad, info, off.

Every clickable piece: hover, mint focus edge, disabled at 40 % (a disabled widget still shows
its tooltip). No timers, no animation. qtawesome is imported only when an icon is drawn.
"""

from functools import lru_cache

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QToolButton, QWidget,
)

from ui.utils.tokens import KIT, TOKENS

T = TOKENS
K = KIT
_DISABLED_ALPHA = 0.4
_MONO = T["mono"].split(",")[0].strip("'\"")


def _rgba(colour, alpha):
    if colour == "transparent":
        return colour
    c = QColor(colour)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


def _qcolor(colour, alpha=1.0):
    c = QColor(colour)
    c.setAlphaF(alpha)
    return c


def _icon(icon, colour):
    """An "mdi.*" name drawn in `colour` (dimmed when disabled), or a QIcon passed through."""
    if icon is None or isinstance(icon, QIcon):
        return icon
    try:
        import qtawesome as qta
        return qta.icon(icon, color=_qcolor(colour), color_disabled=_qcolor(colour, _DISABLED_ALPHA))
    except Exception:
        return QIcon()


class _ReasonMixin:
    """Disabled-with-reason: the reason replaces the tooltip until the widget is enabled again."""

    _enabled_tip = None
    disabled_reason = None

    def set_disabled_reason(self, reason):
        if reason:
            if self.disabled_reason is None:
                self._enabled_tip = self.toolTip()
            self.disabled_reason = reason
            self.setEnabled(False)
            self.setToolTip(reason)
        else:
            if self.disabled_reason is not None:
                self.setToolTip(self._enabled_tip or "")
            self.disabled_reason = None
            self.setEnabled(True)


# ---------------------------------------------------------------- LabelledAction

# variant: (background, border, text, hover background, hover border)
_ACTION_TONES = {
    "primary": (T["em"], T["em2"], K["white"], T["em2"], T["em2"]),
    "soft": (T["emdeep"], T["emline"], T["mint"], K["soft_hover"], T["emline"]),
    "secondary": (K["btn_bg"], T["line2"], T["text"], K["btn_hover"], T["line3"]),
    "ghost": ("transparent", "transparent", T["text2"], K["ghost_hover"], "transparent"),
    "danger": ("transparent", T["dangerline"], T["dangertx"], T["dangerdim"], T["dangerline"]),
}


@lru_cache(maxsize=None)
def _action_qss(variant, sm, icon_only):
    bg, bd, tx, hbg, hbd = _ACTION_TONES[variant]
    pad = 0 if icon_only else (10 if sm else 13)
    size = T["f_s"] if sm else K["f_btn"]
    return (
        f"QPushButton {{ background: {bg}; border: 1px solid {bd}; border-radius: {T['r_m']}px;"
        f" color: {tx}; font-size: {size}px; font-weight: 600; padding: 0 {pad}px; }}"
        f"QPushButton:hover, QPushButton:pressed {{ background: {hbg}; border-color: {hbd}; }}"
        f"QPushButton:focus {{ border: 2px solid {T['mint']}; padding: 0 {max(pad - 1, 0)}px; }}"
        f"QPushButton:disabled {{ background: {_rgba(bg, _DISABLED_ALPHA)};"
        f" border-color: {_rgba(bd, _DISABLED_ALPHA)}; color: {_rgba(tx, _DISABLED_ALPHA)}; }}"
    )


class LabelledAction(_ReasonMixin, QPushButton):
    VARIANTS = tuple(_ACTION_TONES)

    def __init__(self, text="", parent=None, *, variant="secondary", icon=None, sm=False, tooltip=""):
        super().__init__(text, parent)
        if variant not in _ACTION_TONES:
            raise ValueError(f"unknown LabelledAction variant: {variant}")
        self._text = text
        self._icon_spec = icon
        self._swatch = None
        self._sm = sm
        self._icon_only = False
        self.variant = variant
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(K["h_sm"] if sm else T["h_ctl"])
        self.setIconSize(QSize(14, 14))
        if tooltip:
            self.setToolTip(tooltip)
        self._restyle()

    def set_variant(self, variant):
        if variant not in _ACTION_TONES:
            raise ValueError(f"unknown LabelledAction variant: {variant}")
        self.variant = variant
        self._restyle()

    def set_icon_only(self, on):
        if on == self._icon_only:
            return
        self._icon_only = on
        if on:
            self._text = self.text()
            if not self.toolTip():
                self.setToolTip(self._text)
            self.setText("")
            self.setFixedWidth(self.height())
        else:
            if self.toolTip() == self._text:
                self.setToolTip("")
            self.setText(self._text)
            self.setMinimumWidth(0)
            self.setMaximumWidth(16777215)
        self._restyle()

    def is_icon_only(self):
        return self._icon_only

    def set_swatch(self, colour):
        self._swatch = colour
        self._apply_icon()

    def _restyle(self):
        self.setStyleSheet(_action_qss(self.variant, self._sm, self._icon_only))
        self._apply_icon()

    def _apply_icon(self):
        icon = _icon(self._icon_spec, _ACTION_TONES[self.variant][2])
        if icon is not None and icon.isNull():
            icon = None
        if self._swatch is None:
            self.setIcon(icon if icon is not None else QIcon())
            self.setIconSize(QSize(14, 14))
            return
        side, sw, gap = 14, 12, 4
        width = (side + gap if icon is not None else 0) + sw
        ratio = self.devicePixelRatioF() or 1.0
        pm = QPixmap(int(width * ratio), int(side * ratio))
        pm.setDevicePixelRatio(ratio)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        if icon is not None:
            icon.paint(p, 0, 0, side, side)
        p.setPen(_qcolor("black", 0.3))
        p.setBrush(QColor(self._swatch))
        p.drawRoundedRect(QRectF(width - sw + 0.5, (side - sw) / 2 + 0.5, sw - 1, sw - 1),
                          K["r_swatch"], K["r_swatch"])
        p.end()
        self.setIcon(QIcon(pm))
        self.setIconSize(QSize(width, side))


# ---------------------------------------------------------------- IconButton

# variant: (background, border, icon colour, hover background, hover border)
_ICON_TONES = {
    "plain": ("transparent", "transparent", T["text2"], T["hover"], "transparent"),
    "bordered": (K["btn_bg"], T["line2"], T["text2"], K["btn_hover"], T["line3"]),
    "primary": (T["em"], T["em2"], K["white"], T["em2"], T["em2"]),
}


@lru_cache(maxsize=None)
def _icon_qss(variant):
    bg, bd, _ic, hbg, hbd = _ICON_TONES[variant]
    return (
        f"QToolButton {{ background: {bg}; border: 1px solid {bd}; border-radius: {T['r_m']}px;"
        f" padding: 0; }}"
        f"QToolButton:hover, QToolButton:pressed {{ background: {hbg}; border-color: {hbd}; }}"
        f"QToolButton:focus {{ border: 2px solid {T['mint']}; }}"
        f"QToolButton:disabled {{ background: {_rgba(bg, _DISABLED_ALPHA)};"
        f" border-color: {_rgba(bd, _DISABLED_ALPHA)}; }}"
    )


class IconButton(_ReasonMixin, QToolButton):
    VARIANTS = tuple(_ICON_TONES)

    def __init__(self, icon, tooltip, parent=None, *, variant="plain", sm=False):
        super().__init__(parent)
        if not tooltip:
            raise ValueError("IconButton needs a tooltip")
        if variant not in _ICON_TONES:
            raise ValueError(f"unknown IconButton variant: {variant}")
        self.variant = variant
        side = K["h_ibtn_sm"] if sm else T["h_ctl"]
        self.setFixedSize(side, side)
        self.setIconSize(QSize(14, 14) if sm else QSize(16, 16))
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setStyleSheet(_icon_qss(variant))
        icon = _icon(icon, _ICON_TONES[variant][2])
        self.setIcon(icon if icon is not None else QIcon())


# ---------------------------------------------------------------- Segmented

@lru_cache(maxsize=None)
def _segmented_qss():
    r = T["r_m"] - 1
    return (
        f"QFrame#KitSegmented {{ background: transparent; border: 1px solid {T['line2']};"
        f" border-radius: {T['r_m']}px; }}"
        f"QFrame#KitSegmented:focus {{ border-color: {T['mint']}; }}"
        f"QFrame#KitSegmented:disabled {{ border-color: {_rgba(T['line2'], _DISABLED_ALPHA)}; }}"
        f"QPushButton {{ background: transparent; border: none; border-right: 1px solid {T['line2']};"
        f" border-radius: 0; color: {T['text2']}; font-size: {K['f_btn']}px; font-weight: 600;"
        f" padding: 0 14px; }}"
        f"QPushButton[pos=\"first\"] {{ border-top-left-radius: {r}px; border-bottom-left-radius: {r}px; }}"
        f"QPushButton[pos=\"last\"] {{ border-right: none; border-top-right-radius: {r}px;"
        f" border-bottom-right-radius: {r}px; }}"
        f"QPushButton[pos=\"only\"] {{ border-right: none; border-radius: {r}px; }}"
        f"QPushButton:hover:!checked, QPushButton:pressed:!checked {{ background: {T['hover']}; }}"
        f"QPushButton:checked {{ background: {T['em']}; color: {K['white']}; }}"
        f"QPushButton:disabled {{ color: {_rgba(T['text2'], _DISABLED_ALPHA)}; }}"
        f"QPushButton:checked:disabled {{ background: {_rgba(T['em'], _DISABLED_ALPHA)}; }}"
    )


class Segmented(QFrame):
    currentChanged = Signal(int)

    def __init__(self, options, parent=None, current=0):
        super().__init__(parent)
        self.setObjectName("KitSegmented")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFixedHeight(T["h_ctl"])
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setStyleSheet(_segmented_qss())
        lay = QHBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons = []
        n = len(options)
        for i, label in enumerate(options):
            b = QPushButton(label, self)
            b.setCheckable(True)
            b.setFocusPolicy(Qt.NoFocus)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(T["h_ctl"] - 2)
            b.setProperty("pos", "only" if n == 1 else "first" if i == 0 else "last" if i == n - 1 else "mid")
            self._group.addButton(b, i)
            lay.addWidget(b)
            self.buttons.append(b)
        self._current = -1
        if n:
            self.set_current(current)
        self._group.idClicked.connect(self._on_clicked)

    def current_index(self):
        return self._current

    def set_current(self, index):
        if not 0 <= index < len(self.buttons) or index == self._current:
            return
        self._current = index
        self.buttons[index].setChecked(True)
        self.currentChanged.emit(index)

    def _on_clicked(self, index):
        self.setFocus(Qt.MouseFocusReason)
        self.set_current(index)

    def keyPressEvent(self, event):
        step = {Qt.Key_Left: -1, Qt.Key_Right: 1}.get(event.key())
        if step and self.buttons:
            self.set_current(max(0, min(len(self.buttons) - 1, self._current + step)))
            return
        super().keyPressEvent(event)


# ---------------------------------------------------------------- Key

@lru_cache(maxsize=None)
def _key_qss(light):
    bg, bd, tx = ((K["key_light_bg"], K["key_light_line"], K["key_light_tx"]) if light
                  else (K["key_bg"], K["tag_line"], K["key_tx"]))
    return (
        f"QLabel {{ background: {bg}; border: 1px solid {bd}; border-bottom-width: 2px;"
        f" border-radius: {K['r_xs']}px; color: {tx}; font-family: \"{_MONO}\";"
        f" font-size: {K['f_key']}px; font-weight: 600; padding: 0 5px; }}"
    )


class Key(QLabel):
    def __init__(self, text, parent=None, *, light=False):
        super().__init__(text, parent)
        self.setFixedHeight(K["h_key"])
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setStyleSheet(_key_qss(light))


# ---------------------------------------------------------------- StatusDot

# tone: (colour, halo alpha)
_DOT_TONES = {
    "ok": (T["em2"], 0.18),
    "warn": (T["warn"], 0.18),
    "bad": (T["danger"], 0.16),
    "info": (T["info"], 0.18),
    "off": (K["dot_off"], 0.0),
}


class StatusDot(QWidget):
    TONES = tuple(_DOT_TONES)
    _DOT, _HALO = 8, 3

    def __init__(self, tone="ok", parent=None):
        super().__init__(parent)
        side = self._DOT + 2 * self._HALO
        self.setFixedSize(side, side)
        self.tone = None
        self.set_tone(tone)

    def set_tone(self, tone):
        if tone not in _DOT_TONES:
            raise ValueError(f"unknown StatusDot tone: {tone}")
        if tone != self.tone:
            self.tone = tone
            self.update()

    def paintEvent(self, event):
        colour, halo = _DOT_TONES[self.tone]
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        if halo:
            p.setBrush(_qcolor(colour, halo))
            p.drawEllipse(QRectF(self.rect()))
        p.setBrush(QColor(colour))
        p.drawEllipse(QRectF(self._HALO, self._HALO, self._DOT, self._DOT))
        p.end()


# ---------------------------------------------------------------- Pill and Tag

class _ElideLabel(QLabel):
    """Keeps the full text; paints it elided when squeezed and then shows it as the tooltip."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    def minimumSizeHint(self):
        fm = QFontMetrics(self.font())
        m = self.contentsMargins()
        return QSize(fm.horizontalAdvance("…") + m.left() + m.right() + 2, super().minimumSizeHint().height())

    def is_elided(self):
        return QFontMetrics(self.font()).horizontalAdvance(self.text()) > self.contentsRect().width()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_tip()

    def setText(self, text):
        super().setText(text)
        self._sync_tip()

    def _sync_tip(self):
        owner = self.parentWidget() if isinstance(self.parentWidget(), Pill) else self
        owner.setToolTip(self.text() if self.is_elided() else "")

    def paintEvent(self, event):
        QFrame.paintEvent(self, event)      # stylesheet box (background, border); text below
        p = QPainter(self)
        p.setPen(self.palette().color(QPalette.WindowText))
        p.setFont(self.font())
        r = self.contentsRect()
        p.drawText(r, Qt.AlignVCenter | Qt.AlignLeft,
                   QFontMetrics(self.font()).elidedText(self.text(), Qt.ElideRight, r.width()))
        p.end()


# tone: (background, text, border)
_PILL_TONES = {
    "neutral": (T["hover"], K["pill_tx"], "transparent"),
    "emerald": (T["emdeep"], T["mint"], T["emline"]),
    "amber": (T["warndim"], T["warn"], "transparent"),
    "red": (T["dangerdim"], K["red_tx"], "transparent"),
    "blue": (T["infodim"], K["blue_tx"], "transparent"),
    "purple": (T["purpledim"], T["purple"], "transparent"),
    "orange": (K["orangedim"], K["orange"], "transparent"),
}
_PILL_DOT = {"emerald": "ok", "amber": "warn", "red": "bad", "blue": "info"}


@lru_cache(maxsize=None)
def _pill_qss(tone):
    bg, tx, bd = _PILL_TONES[tone]
    radius = K["h_pill"] // 2
    return (
        f"QFrame#KitPill {{ background: {bg}; border: 1px solid {bd}; border-radius: {radius}px; }}"
        f"QLabel {{ background: transparent; border: none; color: {tx};"
        f" font-size: {T['f_xs']}px; font-weight: 600; }}"
    )


class Pill(QFrame):
    TONES = tuple(_PILL_TONES)

    def __init__(self, text, tone="neutral", parent=None, *, dot=False):
        super().__init__(parent)
        self.setObjectName("KitPill")
        self.setFixedHeight(K["h_pill"])
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(9, 0, 9, 0)
        lay.setSpacing(6)
        self.dot = StatusDot(_PILL_DOT.get(tone, "off"), self) if dot else None
        if self.dot:
            lay.addWidget(self.dot)
        self.label = _ElideLabel(text, self)
        lay.addWidget(self.label)
        self.tone = None
        self.set_tone(tone)

    def text(self):
        return self.label.text()

    def set_text(self, text):
        self.label.setText(text)
        self.updateGeometry()

    def set_tone(self, tone):
        if tone not in _PILL_TONES:
            raise ValueError(f"unknown Pill tone: {tone}")
        self.tone = tone
        self.setStyleSheet(_pill_qss(tone))
        if self.dot:
            self.dot.set_tone(_PILL_DOT.get(tone, "off"))

    @property
    def full_text(self):
        return self.label.text()


# tone: (background, text, border)
_TAG_TONES = {
    "neutral": (K["tag_bg"], K["pill_tx"], K["tag_line"]),
    "emerald": (T["emdeep"], T["mint"], T["emline"]),
    "amber": (T["warndim"], T["warn"], T["warnline"]),
    "red": (T["dangerdim"], K["red_tx"], K["tag_r_line"]),
    "blue": (T["infodim"], K["blue_tx"], T["infoline"]),
}


@lru_cache(maxsize=None)
def _tag_qss(tone):
    bg, tx, bd = _TAG_TONES[tone]
    return (
        f"QLabel {{ background: {bg}; border: 1px solid {bd}; border-radius: {K['r_xs']}px;"
        f" color: {tx}; font-family: \"{_MONO}\"; font-size: {T['f_xs']}px; font-weight: 600;"
        f" padding: 0 7px; }}"
    )


class Tag(_ElideLabel):
    TONES = tuple(_TAG_TONES)

    def __init__(self, text, tone="neutral", parent=None):
        super().__init__(text, parent)
        self.setFixedHeight(K["h_tag"])
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.tone = None
        self.set_tone(tone)

    def set_tone(self, tone):
        if tone not in _TAG_TONES:
            raise ValueError(f"unknown Tag tone: {tone}")
        self.tone = tone
        self.setStyleSheet(_tag_qss(tone))

    @property
    def full_text(self):
        return self.text()
