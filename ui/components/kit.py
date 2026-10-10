"""
ui/components/kit.py — the shared UI kit of the fleet redesign
================================================================
Plain Qt widgets drawn from ui/utils/tokens.py only (no colour, radius or size literal here).
Screens import these and compose them; they never restyle or subclass them to change the look.
K-7 turns this docstring into the full reference. Leaves (K-1):

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

Inputs (K-2):
FieldLabel(text, parent=None, *, required=False, for_widget=None)
    Upper-case label above a field; required adds a red *; for_widget becomes the buddy.
Input(parent=None, *, placeholder="", icon=None)
    QLineEdit, 34 px high. icon: leading "mdi.*" name. add_trailing(widget) puts a small
    IconButton (eye, copy) inside the right edge and moves the text clear of it.
Select(parent=None, items=())
    QComboBox with a drawn chevron; wheel changes it only while focused. set_disabled_reason.
TextArea(parent=None, *, rows=3, placeholder="")
    QPlainTextEdit; Tab leaves the field. set_rows(n).
Stepper(parent=None, *, value=0, minimum=0, maximum=100, step=1, unit="")
    One focus stop; Up/Right +step, Down/Left -step, Home/End. value(), setValue(), text(),
    signal valueChanged(int) only on a change. unit is appended as given ("s", " min").
Checkbox(text="", parent=None, *, checked=False)
    Lists and tables only; an immediate setting is a Switch. Elides, text becomes the tooltip.
Switch(parent=None, *, checked=False, tooltip="")
    38 x 22 on / off (sidebar ToggleSwitch promoted, no animation). toggled(bool); Space or
    Enter flips. No text: tooltip doubles as the accessible name.
Input, Select, Stepper, Checkbox and Switch all accept set_disabled_reason (Input and TextArea
only setEnabled). Focus is the mint edge, disabled is 40 %.

Containers (K-3):
Card(title="", parent=None, *, edge=None)
    Panel surface with an upper-case title and a right-hand slot. edge: None, "danger" (red edge and
    title; destructive group only) or "em" (mint). add_widget(w) puts w in the padded body;
    add_row(w) puts a SettingRow / EmptyState flush (full width, hairline between rows).
    add_action(w) adds to the title row's right slot. set_title(), title(), set_edge(). A Card never
    holds a Card: add_widget / add_row raise ValueError (group with a SectionLabel instead).
SettingRow(title, sub="", control=None, parent=None, *, width="fixed", danger=False)
    Title + muted sub-label left, control column right. width: "fixed" (230 px), "wide" (360),
    "auto" (as wide as the control). control is a widget or a list; add_control(w) adds more; Input,
    Select, TextArea and Stepper fill the column, everything else sits at its right edge.
    set_title(), set_sub(), set_compact(on) stacks the control under the text (COMPACT width).
SectionLabel(text, parent=None)
    The theme's existing SectionLabel (QSS class "SectionLabel"), upper-cased.
Banner(text, tone="i", parent=None, *, icon=None)
    Inline notice; tone w (warning), r (error), g (success), i (info). Wraps. add_action(w) on the
    right. set_text(), set_tone().
Toast(parent)
    The shell's alert, promoted: same API as SeraAlert (show_alert(message, level, duration_ms),
    show_message, hide_alert, hide_toast; level success / info / warning / error), centred at the
    bottom of its parent, no fade. duration_ms <= 0 stays until hide_alert.
EmptyState(title, text="", parent=None, *, icon="mdi.inbox-outline")
    Ring icon, one sentence, optional action: add_action(w). set_title(), set_text().

Bars (K-4):
Chip(text, parent=None, *, count=None, checked=False, menu=None, value_when_on=None, tooltip="", has_menu=False)
    Checkable button / filter pill (28 px high, promotes FilterChip). count: integer badge.
    value_when_on: text shown when checked ("Client: Unregistered"). menu: QMenu opened on click.
    set_count(), set_value_when_on(), set_value(), set_menu(), set_disabled_reason().
FilterChip = Chip
    Alias promoting Manage Clients' FilterChip.
ChipBar(parent=None, *, single_select=False, chips=())
    Horizontal row of chips; single_select enforces one checked chip (currentChanged, selectionChanged).
    add_chip(), chips(), chip(id), selected_chip(), selected_id(), set_selected().
    add_overflow_chip(menu) adds "More ▾" menu chip; set_compact(on) / apply_width_mode(mode).
StatusStrip(parent=None)
    One-line status panel (min 44 px). add_pill(), add_fact(bold, sub), add_text(), add_separator(),
    add_widget(), add_stretch().
SelectionBar(parent=None, *, hint=...)
    Fixed 44 px action bar above data grids. hint_label shown when empty; active container with
    who_label, client group, vertical dividers (add_divider), group labels (add_group_label),
    and NoFocus action buttons (add_action, add_danger_action). set_compact(on) makes buttons icon-only
    (Open keeps its label). set_who(), set_has_selection(), set_empty().
UnsavedBar(parent=None, *, reserved_space=False)
    Fixed 44 px amber warning bar: "N unsaved changes", Discard, Save changes.
    set_count(n): hides when 0 (or collapses/transparents in reserved_space mode).
    signals discard_clicked, save_clicked. count(), is_dirty().

Data grid (K-5):
DataGrid(rows=0, columns=0, parent=None, *, tone="white", list_mode=False)
    A QTableWidget (drop-in for today's grids: items, BackgroundRole / ForegroundRole cell formats and
    the search grid's highlight engine work unchanged) drawn by GridDelegate, never by widgets per
    row. tone: "white" (every data grid) or "dark" (Tracker Dump). Sticky caps header (sort arrow,
    elided names get a tooltip), zebra, hover row, mint selection with the emerald edge on the first
    selected cell of a run, mint cursor on the current cell in cell-selection grids. A cell's
    BackgroundRole fill always wins (highlight). list_mode: no header, 52 px two-line rows, row
    selection (Manage Clients list). set_tone(tone). Row height 40 (tokens); set_tall_rows(True)
    gives 52 px rows for two-line cells.
    Cell content by item role (set with item.setData(role, value)):
      ROLE_SERVICES [(name, colour), ...]  service dots; "+N" when they do not fit, names in the tooltip
      ROLE_PILL     (text, tone[, dot])    status pill; tones neutral emerald green amber red blue
                                           (dark / list also purple orange)
      ROLE_TAG      (text, tone)           mono tag (Tag tones)
      ROLE_ACTIONS  [CellAction, ...]      buttons drawn in the cell; a click emits
                                           actionTriggered(row, column, key); hover tooltip per button
      ROLE_SUB      "second line"          two-line cell: bold first line, muted mono second line
      Checkable items with a check state (item.setCheckState) draw a kit checkbox; a click on it or
      Space toggles CheckStateRole.
      Any other cell: DisplayRole text, elided, full text as tooltip when cut (ToolTipRole wins).
    set_column(col, *, kind="text", width=None, minimum=None, stretch=1, max_share=None,
               priority=0, compact_hidden=False)
        kind: text, num (muted), mono (PAN, GSTIN), strong (bold). width: fixed preferred px, None
        shares the rest by stretch (capped at max_share of the width). priority > 0: hides when even
        the minimums do not fit, highest first. compact_hidden: hidden in COMPACT.
    fit_columns(available=None) -> hidden columns  (runs on every viewport resize once a column has
        a spec; columns hidden by the screen stay hidden; widths the user dragged win until
        reset_manual_widths()). signal columnsFitted(list). hidden_by_fit(), manual_widths().
    set_compact(on) / apply_width_mode(mode) refit. fit_column_widths(...) is the pure function.
CellAction(key, icon, tooltip, *, primary=False, colour=None, enabled=True)
    One drawn 26 px button; primary is emerald (or `colour`, e.g. the service colour). tooltip required.
DarkListTable(rows=0, columns=0, parent=None)
    DataGrid in the "list" tone for dialogs and settings: muted caps header, hairline rows, row
    selection dark-mint with the emerald edge, tags / pills / checkboxes in cells.
GridFooter(parent=None, *, tone="white")
    30 px strip under a grid. set_count(text) (count_label), add_hint(keys, text="", *,
    compact_hide=False) key caps + sentence, add_widget(w) (pager), set_compact / apply_width_mode.
GridFrame(grid, footer=None, parent=None)
    The rounded surface around a grid and its footer; set_tone(tone) recolours all three;
    apply_width_mode passes on to both.

Frames (K-6):
IconTile(icon, tone="emerald", parent=None, *, large=False)
    The 34 px (40 large) rounded icon square of a dialog header; tones emerald, amber, blue.
DialogFrame(title, purpose="", parent=None, *, icon="mdi.cog", tone="emerald", full=False, scroll=False)
    Header (IconTile, title, one-line purpose, right slot) / body / footer. add_widget(w, stretch=0)
    fills the body (scroll=True wraps the body in a scroll area). The footer appears with its first
    add_action(text_or_widget, *, variant="secondary", icon=None, tooltip=""); a second primary raises
    ValueError. add_footer_spacer() splits left items (e.g. a danger action) from the right group.
    add_header_widget(w) fills the right slot of the header; add_close_button() adds the bordered
    "Close (Esc)" icon button. Esc anywhere inside emits closeRequested (a QDialog host connects it to
    reject). full=True: no border, no radius, fills its parent (set_full). set_title(), set_purpose().
Scrim(parent, dialog=None, *, desk=False)
    Dimmed layer over `parent` that centres a DialogFrame (a full one fills it). desk=True is the
    opaque desktop colour of the first-run screens. open() / dismiss(); a click outside the dialog
    and the dialog's own Esc both emit closeRequested. Follows the parent's size.
PageHeader(title="", parent=None, *, count="", lead="")
    Title row of a page: add_leading(w) (sidebar toggle, Back) before the title, a count pill
    ("16 clients"), a muted lead line under the title, add_action(w) on the right. set_title(),
    set_count(), set_lead(). set_compact(on) / apply_width_mode(mode): 17 px title, lead hidden.
Tile(value, label, parent=None, *, tone=None, checked=False, tooltip="")
    Summary tile (big number over a muted label), clickable and checkable (emerald edge when on);
    tone colours the number: None, emerald, amber, red, blue. Space / Enter click; put tiles in an
    exclusive QButtonGroup for one-of-n. set_value(), set_label(), set_tone(), set_compact(on).
LogView(parent=None, *, max_lines=500)
    Read-only monospace activity log. add_line(message, tag="", *, tone=None, ts=""): tags guard
    (amber), sync (emerald), err (red), peer (blue), net (purple), any other neutral; sticks to the
    bottom unless the user scrolled up. lines(), line_count(), clear_log().
SlidePanel (ui/shell/slide_panel.py) is restyled from the same tokens: 540 px, the whole width in
COMPACT (apply_width_mode / set_compact; derived from the parent's width until one is set).

Every clickable piece: hover, mint focus edge, disabled at 40 % (a disabled widget still shows
its tooltip). No timers, no animation. qtawesome is imported only when an icon is drawn.
"""

from functools import lru_cache

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap,
    QPolygonF, QRegion, QTextBlockFormat, QTextCharFormat, QTextCursor,
)
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QBoxLayout, QButtonGroup, QCheckBox, QComboBox, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea,
    QSizePolicy, QStyle, QStyledItemDelegate, QTableWidget, QToolButton, QToolTip, QVBoxLayout,
    QWidget,
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


# ---------------------------------------------------------------- inputs

def _field_qss(widget, extra=""):
    """Panel background, 1 px border, mint edge on focus; shared by Input, Select and TextArea."""
    pad = K["pad_inp"]
    return (
        f"{widget} {{ background: {T['panel']}; border: 1px solid {K['inp_line']};"
        f" border-radius: {T['r_m']}px; color: {T['text']}; font-size: {T['f_m']}px;"
        f" selection-background-color: {T['emline']}; selection-color: {T['text']};"
        f" padding: 0 {pad}px; {extra} }}"
        f"{widget}:focus {{ border: 1px solid {T['mint']}; }}"
        f"{widget}:disabled {{ background: {_rgba(T['panel'], _DISABLED_ALPHA)};"
        f" border-color: {_rgba(K['inp_line'], _DISABLED_ALPHA)}; color: {_rgba(T['text'], _DISABLED_ALPHA)}; }}"
    )


def _placeholder_palette(widget):
    pal = widget.palette()
    pal.setColor(QPalette.PlaceholderText, QColor(K["inp_ph"]))
    widget.setPalette(pal)


class FieldLabel(QLabel):
    """Small upper-case label above a field. required=True adds a red asterisk; for_widget makes
    it the buddy, so a screen reader reads it with the field. label_text is the plain text."""

    def __init__(self, text, parent=None, *, required=False, for_widget=None):
        super().__init__(parent)
        self.label_text = text
        self.required = required
        font = QFont(self.font())
        font.setPixelSize(T["f_xs"])
        font.setCapitalization(QFont.AllUppercase)
        font.setLetterSpacing(QFont.PercentageSpacing, 108)
        self.setFont(font)
        self.setStyleSheet(f"QLabel {{ color: {T['ink3']}; background: transparent; }}")
        if required:
            from html import escape
            self.setTextFormat(Qt.RichText)
            self.setText(f"{escape(text)} <span style=\"color:{T['dangertx']}\">*</span>")
        else:
            self.setTextFormat(Qt.PlainText)
            self.setText(text)
        if for_widget is not None:
            self.setBuddy(for_widget)


class Input(QLineEdit):
    """Single-line field. icon: leading "mdi.*" name. add_trailing(widget) puts a small IconButton
    (eye, copy ...) inside the right edge and keeps the text clear of it. Everything else is
    QLineEdit (text, setEchoMode, textChanged, returnPressed, setPlaceholderText)."""

    def __init__(self, parent=None, *, placeholder="", icon=None):
        super().__init__(parent)
        self.setFixedHeight(T["h_inp"])
        self.setStyleSheet(_field_qss("QLineEdit"))
        _placeholder_palette(self)
        if placeholder:
            self.setPlaceholderText(placeholder)
        self._icon_spec = icon
        self._icon_label = None
        self._tail = None
        if icon is not None:
            self._icon_label = QLabel(self)
            self._icon_label.setAttribute(Qt.WA_TransparentForMouseEvents)
            self._icon_label.setStyleSheet("background: transparent; border: none;")
            self._icon_label.setFixedSize(14, 14)
            self._paint_icon()
        self._relayout()

    def has_icon(self):
        return self._icon_label is not None

    def add_trailing(self, widget):
        if self._tail is None:
            self._tail = QWidget(self)
            self._tail.setStyleSheet("background: transparent;")
            lay = QHBoxLayout(self._tail)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(2)
        self._tail.layout().addWidget(widget)
        self._tail.show()
        self._relayout()
        return widget

    def _paint_icon(self):
        icon = _icon(self._icon_spec, K["inp_icon"])
        if icon is not None:
            mode = QIcon.Normal if self.isEnabled() else QIcon.Disabled
            self._icon_label.setPixmap(icon.pixmap(14, 14, mode))

    def _relayout(self):
        left = K["pad_inp_icon"] - K["pad_inp"] if self._icon_label is not None else 0
        right = 0
        if self._tail is not None:
            self._tail.adjustSize()
            right = max(0, self._tail.width() + 6 - K["pad_inp"])
        self.setTextMargins(left, 0, right, 0)
        self._place()

    def _place(self):
        if self._icon_label is not None:
            self._icon_label.move(11, (self.height() - 14) // 2)
        if self._tail is not None:
            self._tail.move(self.width() - self._tail.width() - 6, (self.height() - self._tail.height()) // 2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == event.Type.EnabledChange and self._icon_label is not None:
            self._paint_icon()


class Select(_ReasonMixin, QComboBox):
    """Drop-down list (QComboBox: addItem, currentIndex, currentIndexChanged, currentData). The
    mouse wheel only changes the value while the list has focus, so scrolling a page never edits."""

    def __init__(self, parent=None, items=()):
        super().__init__(parent)
        self.setFixedHeight(T["h_inp"])
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            _field_qss("QComboBox", "padding-right: 30px;")
            + "QComboBox::drop-down { border: none; width: 0px; }"
            + "QComboBox::down-arrow { image: none; width: 0px; }"
            + f"QComboBox QAbstractItemView {{ background: {T['raise']}; border: 1px solid {T['line2']};"
              f" color: {T['text']}; outline: none; padding: 2px; selection-background-color: {T['emdim']};"
              f" selection-color: {T['text']}; }}"
        )
        if items:
            self.addItems(list(items))

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(_qcolor(K["inp_arrow"], 1.0 if self.isEnabled() else _DISABLED_ALPHA), 1.5)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        x, y = self.width() - 14.5, self.height() / 2
        p.drawPolyline([QPointF(x - 3, y - 1.5), QPointF(x, y + 1.5), QPointF(x + 3, y - 1.5)])
        p.end()


class TextArea(QPlainTextEdit):
    """Multi-line field; rows sets the visible height (it still scrolls). Plain text only."""

    def __init__(self, parent=None, *, rows=3, placeholder=""):
        super().__init__(parent)
        self.setStyleSheet(_field_qss("QPlainTextEdit", "padding: 8px 12px;"))
        _placeholder_palette(self)
        if placeholder:
            self.setPlaceholderText(placeholder)
        self.setTabChangesFocus(True)
        self.set_rows(rows)

    def set_rows(self, rows):
        fm = QFontMetrics(self.font())
        self.setMinimumHeight(round(fm.lineSpacing() * 1.5 * max(1, rows)) + 20)
        self.updateGeometry()

    def sizeHint(self):
        return QSize(super().sizeHint().width(), self.minimumHeight())


class Stepper(_ReasonMixin, QFrame):
    """− value +. One focus stop; Up / Right add step, Down / Left subtract, Home / End jump to the
    limits. unit is shown after the number ("30 s"). valueChanged(int) fires only on a change."""

    valueChanged = Signal(int)

    def __init__(self, parent=None, *, value=0, minimum=0, maximum=100, step=1, unit=""):
        super().__init__(parent)
        self.setObjectName("KitStepper")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFixedHeight(T["h_inp"])
        self._min, self._max, self._step, self._unit = minimum, maximum, step, unit
        self._value = max(minimum, min(maximum, value))
        self.setStyleSheet(
            f"QFrame#KitStepper {{ background: {T['panel']}; border: 1px solid {K['inp_line']};"
            f" border-radius: {T['r_m']}px; }}"
            f"QFrame#KitStepper:focus {{ border-color: {T['mint']}; }}"
            f"QFrame#KitStepper:disabled {{ background: {_rgba(T['panel'], _DISABLED_ALPHA)};"
            f" border-color: {_rgba(K['inp_line'], _DISABLED_ALPHA)}; }}"
            f"QToolButton {{ background: transparent; border: none; color: {T['text2']};"
            f" font-size: {T['f_xl']}px; }}"
            f"QToolButton:hover {{ color: {T['mint']}; }}"
            f"QToolButton:disabled {{ color: {_rgba(T['text2'], _DISABLED_ALPHA)}; }}"
            f"QLabel {{ background: transparent; color: {T['text']}; font-size: {T['f_m']}px; }}"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(1, 0, 1, 0)
        lay.setSpacing(0)
        self.minus = self._button("−", "Decrease", -1)
        self.plus = self._button("+", "Increase", 1)
        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.minus)
        lay.addWidget(self._label, 1)
        lay.addWidget(self.plus)
        self._refresh()

    def _button(self, text, tip, sign):
        b = QToolButton(self)
        b.setText(text)
        b.setToolTip(tip)
        b.setAccessibleName(tip)
        b.setFocusPolicy(Qt.NoFocus)
        b.setCursor(Qt.PointingHandCursor)
        b.setFixedSize(K["w_stepper_btn"], T["h_inp"] - 2)
        b.clicked.connect(lambda: self._nudge(sign, focus=True))
        return b

    def value(self):
        return self._value

    def minimum(self):
        return self._min

    def maximum(self):
        return self._max

    def setValue(self, value):
        value = max(self._min, min(self._max, int(value)))
        if value == self._value:
            return
        self._value = value
        self._refresh()
        self.valueChanged.emit(value)

    def text(self):
        return f"{self._value}{self._unit}"

    def _nudge(self, sign, focus=False):
        if focus:
            self.setFocus(Qt.MouseFocusReason)
        self.setValue(self._value + sign * self._step)

    def _refresh(self):
        self._label.setText(self.text())
        self.minus.setEnabled(self.isEnabled() and self._value > self._min)
        self.plus.setEnabled(self.isEnabled() and self._value < self._max)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == event.Type.EnabledChange:
            self._refresh()

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key_Up, Qt.Key_Right):
            self._nudge(1)
        elif key in (Qt.Key_Down, Qt.Key_Left):
            self._nudge(-1)
        elif key == Qt.Key_Home:
            self.setValue(self._min)
        elif key == Qt.Key_End:
            self.setValue(self._max)
        else:
            super().keyPressEvent(event)


class Checkbox(_ReasonMixin, QCheckBox):
    """Square tick box with a label, for lists and tables only (a setting that applies at once is
    a Switch). Text elides when squeezed and then becomes the tooltip. Space toggles."""

    _GAP = 8
    _own_tip = ""

    def __init__(self, text="", parent=None, *, checked=False):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setChecked(checked)

    def setToolTip(self, tip):
        self._own_tip = tip
        super().setToolTip(tip)

    def set_disabled_reason(self, reason):
        super().set_disabled_reason(reason)
        self._sync_tip()

    def _text_width(self):
        return max(0, self.width() - K["d_cb"] - self._GAP) if self.text() else 0

    def is_elided(self):
        return bool(self.text()) and QFontMetrics(self.font()).horizontalAdvance(self.text()) > self._text_width()

    def _sync_tip(self):
        if self.disabled_reason is None:
            QCheckBox.setToolTip(self, self._own_tip or (self.text() if self.is_elided() else ""))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_tip()

    def setText(self, text):
        super().setText(text)
        self._sync_tip()

    def sizeHint(self):
        fm = QFontMetrics(self.font())
        w = K["d_cb"] + (self._GAP + fm.horizontalAdvance(self.text()) if self.text() else 0)
        return QSize(w, max(K["d_cb"], fm.height()) + 4)

    def minimumSizeHint(self):
        fm = QFontMetrics(self.font())
        return QSize(K["d_cb"] + (self._GAP + fm.horizontalAdvance("…") if self.text() else 0), self.sizeHint().height())

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, event):
        d = K["d_cb"]
        on, enabled = self.isChecked(), self.isEnabled()
        alpha = 1.0 if enabled else _DISABLED_ALPHA
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        box = QRectF(0.5, (self.height() - d) / 2 + 0.5, d - 1, d - 1)
        edge = T["mint"] if self.hasFocus() else (T["em2"] if on else T["line3"])
        p.setBrush(_qcolor(T["em"] if on else K["btn_bg"], alpha))
        p.setPen(QPen(_qcolor(edge, alpha), 2 if self.hasFocus() else 1))
        p.drawRoundedRect(box, K["r_cb"], K["r_cb"])
        if on:
            pen = QPen(_qcolor(K["white"], alpha), 1.8)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            x, y = box.x(), box.y()
            p.drawPolyline([QPointF(x + 4, y + 8), QPointF(x + 6.8, y + 10.8), QPointF(x + 11.5, y + 5)])
        if self.text():
            p.setFont(self.font())
            p.setPen(_qcolor(T["text2"], alpha))
            r = QRectF(d + self._GAP, 0, self._text_width(), self.height())
            p.drawText(r, Qt.AlignVCenter | Qt.AlignLeft,
                       QFontMetrics(self.font()).elidedText(self.text(), Qt.ElideRight, int(r.width())))
        p.end()


class Switch(_ReasonMixin, QAbstractButton):
    """On / off setting that applies at once (the sidebar's ToggleSwitch, promoted). Checkable
    QAbstractButton: toggled(bool), isChecked(), setChecked(). Space or Enter flips it; the thumb
    jumps with no animation. Give it a tooltip or accessible name: it has no text of its own."""

    def __init__(self, parent=None, *, checked=False, tooltip=""):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(K["w_sw"], K["h_sw"])
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setChecked(checked)
        if tooltip:
            self.setToolTip(tooltip)
            self.setAccessibleName(tooltip)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not event.isAutoRepeat():
            self.click()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        on = self.isChecked()
        alpha = 1.0 if self.isEnabled() else _DISABLED_ALPHA
        h = self.height()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        edge = T["mint"] if self.hasFocus() else (T["em2"] if on else K["sw_off_line"])
        p.setBrush(_qcolor(T["em"] if on else K["sw_off"], alpha))
        p.setPen(QPen(_qcolor(edge, alpha), 2 if self.hasFocus() else 1))
        p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, h - 1), h / 2, h / 2)
        thumb = h - 6
        x = self.width() - thumb - 3 if on else 3
        p.setPen(Qt.NoPen)
        p.setBrush(_qcolor(K["white"] if on else K["sw_thumb"], alpha))
        p.drawEllipse(QRectF(x, 3, thumb, thumb))
        p.end()


# ---------------------------------------------------------------- containers

class SectionLabel(QLabel):
    """The theme's SectionLabel (QSS class "SectionLabel"), upper-cased here because Qt ignores
    text-transform. Unboxed; for grouping a page into sections, not for a Card title."""

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setProperty("class", "SectionLabel")
        font = QFont(self.font())
        font.setCapitalization(QFont.AllUppercase)
        self.setFont(font)


# edge: (background, border, title colour)
_CARD_EDGES = {
    None: (T["card"], T["line"], T["muted"]),
    "danger": (K["card_danger_bg"], K["card_danger_line"], T["dangertx"]),
    "em": (T["card"], T["emline"], T["mint"]),
}


@lru_cache(maxsize=None)
def _card_qss(edge):
    bg, bd, title = _CARD_EDGES[edge]
    return (
        f"QFrame#KitCard {{ background: {bg}; border: 1px solid {bd}; border-radius: {T['r_l']}px; }}"
        f"QWidget#KitCardHead, QWidget#KitCardBody {{ background: transparent; }}"
        f"QLabel#KitCardTitle {{ color: {title}; background: transparent; border: none;"
        f" font-size: {T['f_xs']}px; font-weight: 600; }}"
    )


class Card(QFrame):
    EDGES = tuple(_CARD_EDGES)

    def __init__(self, title="", parent=None, *, edge=None):
        super().__init__(parent)
        self.setObjectName("KitCard")
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)
        self._header = None
        self._body = None
        self.title_label = None
        self.edge = None
        self.set_edge(edge)
        if title:
            self.set_title(title)

    def _ensure_header(self):
        if self._header is None:
            self._header = QWidget(self)
            self._header.setObjectName("KitCardHead")
            lay = QHBoxLayout(self._header)
            lay.setContentsMargins(T["s_4"], T["s_3"], T["s_4"], 0)
            lay.setSpacing(T["s_3"])
            self.title_label = _ElideLabel("", self._header)
            self.title_label.setObjectName("KitCardTitle")
            font = QFont(self.title_label.font())
            font.setCapitalization(QFont.AllUppercase)
            font.setLetterSpacing(QFont.AbsoluteSpacing, 1.0)
            self.title_label.setFont(font)
            self.title_label.setMinimumHeight(K["h_card_head"])
            lay.addWidget(self.title_label)
            lay.addStretch(1)
            self._outer.insertWidget(0, self._header)
        return self._header

    def set_title(self, title):
        self._ensure_header()
        self.title_label.setText(title)

    def title(self):
        return self.title_label.text() if self.title_label else ""

    def set_edge(self, edge):
        if edge not in _CARD_EDGES:
            raise ValueError(f"unknown Card edge: {edge}")
        self.edge = edge
        self.setStyleSheet(_card_qss(edge))

    def add_action(self, widget):
        """Right-hand slot of the title row (a LabelledAction, Pill, Switch ...)."""
        self._ensure_header().layout().addWidget(widget)

    @staticmethod
    def _refuse_card(widget):
        if isinstance(widget, Card) or widget.findChildren(Card):
            raise ValueError("a Card never holds a Card")

    def add_widget(self, widget, stretch=0):
        self._refuse_card(widget)
        if self._body is None:
            body = QWidget(self)
            body.setObjectName("KitCardBody")
            self._body = QVBoxLayout(body)
            self._body.setContentsMargins(T["s_4"], T["s_3"], T["s_4"], K["pad_card_b"])
            self._body.setSpacing(T["s_3"])
            self._outer.addWidget(body)
        self._body.addWidget(widget, stretch)

    def add_row(self, widget):
        """Full-width row (SettingRow, EmptyState); the first one under the title has no rule above."""
        self._refuse_card(widget)
        if isinstance(widget, SettingRow):
            widget.set_first(self._outer.count() == (1 if self._header else 0))
        self._body = None
        self._outer.addWidget(widget)


@lru_cache(maxsize=None)
def _row_qss(first):
    top = "none" if first else f"1px solid {K['row_line']}"
    return (f"QFrame#KitSettingRow {{ background: transparent; border: none; border-top: {top}; }}"
            f"QWidget#KitSettingCtl {{ background: transparent; border: none; }}")


class SettingRow(QFrame):
    WIDTHS = ("fixed", "auto", "wide")

    def __init__(self, title, sub="", control=None, parent=None, *, width="fixed", danger=False):
        super().__init__(parent)
        if width not in self.WIDTHS:
            raise ValueError(f"unknown SettingRow width: {width}")
        self.setObjectName("KitSettingRow")
        self.width_mode = width
        self.danger = danger
        self.compact = False
        self.set_first(False)
        self._root = QBoxLayout(QBoxLayout.LeftToRight, self)
        self._root.setContentsMargins(T["s_4"], K["pad_row_v"], T["s_4"], K["pad_row_v"])
        self._root.setSpacing(T["s_4"])
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(1)
        self.title_label = QLabel(title, self)
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet(
            f"QLabel {{ color: {T['dangertx'] if danger else T['text']}; background: transparent;"
            f" font-size: {K['f_row_title']}px; font-weight: 600; }}")
        self.sub_label = QLabel(sub, self)
        self.sub_label.setWordWrap(True)
        self.sub_label.setStyleSheet(
            f"QLabel {{ color: {T['muted']}; background: transparent; font-size: {T['f_s']}px; }}")
        self.sub_label.setVisible(bool(sub))
        text.addWidget(self.title_label)
        text.addWidget(self.sub_label)
        self._root.addLayout(text, 1)
        self.column = QWidget(self)
        self.column.setObjectName("KitSettingCtl")
        self._ctl = QHBoxLayout(self.column)
        self._ctl.setContentsMargins(0, 0, 0, 0)
        self._ctl.setSpacing(T["s_2"])
        self._spacer = False
        self._has_fill = False
        self._root.addWidget(self.column, 0, Qt.AlignVCenter)
        self._size_column()
        if control is not None:
            for w in (control if isinstance(control, (list, tuple)) else [control]):
                self.add_control(w)

    def _size_column(self):
        if self.compact or self.width_mode == "auto":
            self.column.setMinimumWidth(0)
            self.column.setMaximumWidth(16777215)
        else:
            self.column.setFixedWidth(K["w_ctl_wide"] if self.width_mode == "wide" else T["w_ctl"])

    def set_first(self, first):
        self.first = bool(first)
        self.setStyleSheet(_row_qss(self.first))

    def set_title(self, title):
        self.title_label.setText(title)

    def set_sub(self, sub):
        self.sub_label.setText(sub)
        self.sub_label.setVisible(bool(sub))

    def add_control(self, widget):
        fill = isinstance(widget, (Input, Select, TextArea, Stepper))
        if fill:
            self._has_fill = True
            if self._spacer:
                self._ctl.takeAt(0)
                self._spacer = False
            self._ctl.addWidget(widget, 1)
        else:
            if not self._spacer and not self._has_fill:
                self._ctl.insertStretch(0, 1)
                self._spacer = True
            self._ctl.addWidget(widget)

    def set_compact(self, on):
        self.compact = bool(on)
        self._root.setDirection(QBoxLayout.TopToBottom if on else QBoxLayout.LeftToRight)
        self._size_column()


# tone: (background, border, text, icon)
_BANNER_TONES = {
    "w": (T["warndim"], T["warnline"], K["banner_w_tx"], "mdi.alert-outline"),
    "r": (K["banner_r_bg"], K["banner_r_line"], K["banner_r_tx"], "mdi.alert-circle-outline"),
    "g": (K["banner_g_bg"], K["banner_g_line"], K["banner_g_tx"], "mdi.check-circle-outline"),
    "i": (T["infodim"], T["infoline"], K["banner_i_tx"], "mdi.information-outline"),
}


@lru_cache(maxsize=None)
def _banner_qss(tone):
    bg, bd, tx, _ = _BANNER_TONES[tone]
    return (
        f"QFrame#KitBanner {{ background: {bg}; border: 1px solid {bd}; border-radius: {T['r_l'] - 2}px; }}"
        f"QFrame#KitBanner QLabel {{ background: transparent; border: none; color: {tx};"
        f" font-size: {K['f_banner']}px; }}"
    )


class Banner(QFrame):
    TONES = tuple(_BANNER_TONES)

    def __init__(self, text, tone="i", parent=None, *, icon=None):
        super().__init__(parent)
        self.setObjectName("KitBanner")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(K["pad_banner_h"], K["pad_banner_v"], K["pad_banner_h"], K["pad_banner_v"])
        lay.setSpacing(T["s_3"])
        self._icon_name = icon
        self.icon_label = QLabel(self)
        self.icon_label.setFixedSize(K["d_banner_icon"], K["d_banner_icon"])
        lay.addWidget(self.icon_label, 0, Qt.AlignVCenter)
        self.label = QLabel(text, self)
        self.label.setWordWrap(True)
        lay.addWidget(self.label, 1)
        self.tone = None
        self.set_tone(tone)

    def text(self):
        return self.label.text()

    def set_text(self, text):
        self.label.setText(text)

    def set_tone(self, tone):
        if tone not in _BANNER_TONES:
            raise ValueError(f"unknown Banner tone: {tone}")
        self.tone = tone
        self.setStyleSheet(_banner_qss(tone))
        ic = _icon(self._icon_name or _BANNER_TONES[tone][3], _BANNER_TONES[tone][2])
        side = K["d_banner_icon"]
        self.icon_label.setPixmap(ic.pixmap(side, side) if ic is not None else QPixmap())

    def add_action(self, widget):
        self.layout().addWidget(widget, 0, Qt.AlignVCenter)


# level: (background, border, icon colour, icon)
_TOAST_LEVELS = {
    "success": (K["toast_bg"], K["banner_g_line"], T["mint"], "mdi.check-circle-outline"),
    "info": (T["infodim"], T["infoline"], T["info"], "mdi.information-outline"),
    "warning": (T["warndim"], T["warnline"], T["warn"], "mdi.alert-outline"),
    "error": (K["banner_r_bg"], K["banner_r_line"], T["dangertx"], "mdi.alert-circle-outline"),
}


@lru_cache(maxsize=None)
def _toast_qss(level):
    bg, bd, _, _ = _TOAST_LEVELS[level]
    return (
        f"QFrame#KitToast {{ background: {bg}; border: 1px solid {bd}; border-radius: {T['r_l'] - 2}px; }}"
        f"QFrame#KitToast QLabel {{ background: transparent; border: none; color: {T['text']};"
        f" font-size: {K['f_banner']}px; }}"
    )


class Toast(QFrame):
    LEVELS = tuple(_TOAST_LEVELS)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("KitToast")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.level = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(T["s_4"], K["pad_toast_v"], T["s_4"], K["pad_toast_v"])
        lay.setSpacing(T["s_3"])
        self.icon_label = QLabel(self)
        self.icon_label.setFixedSize(K["d_banner_icon"], K["d_banner_icon"])
        lay.addWidget(self.icon_label, 0, Qt.AlignVCenter)
        self.label = QLabel("", self)
        self.label.setWordWrap(True)
        lay.addWidget(self.label, 1)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide_alert)
        if parent is not None:
            parent.installEventFilter(self)
        self.hide()

    def show_alert(self, message, level="success", duration_ms=3000):
        if level not in _TOAST_LEVELS:
            level = "success"
        if level != self.level:
            self.level = level
            self.setStyleSheet(_toast_qss(level))
            ic = _icon(_TOAST_LEVELS[level][3], _TOAST_LEVELS[level][2])
            side = K["d_banner_icon"]
            self.icon_label.setPixmap(ic.pixmap(side, side) if ic is not None else QPixmap())
        self.label.setText(message)
        self.setAccessibleName(message)
        self._place()
        self.show()
        self.raise_()
        self.timer.stop()
        if duration_ms > 0:
            self.timer.start(duration_ms)

    def show_message(self, message, duration_ms=3000, level="info"):
        self.show_alert(message, level=level, duration_ms=duration_ms)

    def hide_alert(self):
        self.timer.stop()
        self.hide()

    def hide_toast(self):
        self.hide_alert()

    def _place(self):
        parent = self.parentWidget()
        lay = self.layout()
        margins = lay.contentsMargins()
        natural = (margins.left() + margins.right() + 2 + K["d_banner_icon"] + lay.spacing()
                   + QFontMetrics(self.label.font()).horizontalAdvance(self.label.text()) + 2)
        limit = K["w_toast_max"]
        if parent is not None:
            limit = min(limit, max(parent.width() - 2 * T["s_4"], 0))
        width = max(min(natural, limit), 1)
        self.setFixedWidth(width)
        height = lay.totalHeightForWidth(width)
        self.setFixedHeight(height if height > 0 else self.sizeHint().height())
        if parent is not None:
            self.move((parent.width() - width) // 2, parent.height() - self.height() - K["gap_toast"])

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Resize and obj is self.parentWidget() and not self.isHidden():
            self._place()
        return False


class EmptyState(QFrame):
    def __init__(self, title, text="", parent=None, *, icon="mdi.inbox-outline"):
        super().__init__(parent)
        self.setObjectName("KitEmpty")
        self.setStyleSheet("QFrame#KitEmpty { background: transparent; border: none; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(T["s_5"], K["pad_empty_v"], T["s_5"], K["pad_empty_v"])
        lay.setSpacing(T["s_2"])
        lay.setAlignment(Qt.AlignHCenter)
        ring = QLabel(self)
        ring.setFixedSize(K["d_ring"], K["d_ring"])
        ring.setAlignment(Qt.AlignCenter)
        ring.setStyleSheet(
            f"QLabel {{ background: {K['ring_bg']}; border: 1px solid {K['ring_line']};"
            f" border-radius: {K['d_ring'] // 2}px; }}")
        ic = _icon(icon, K["ring_tx"])
        if ic is not None:
            ring.setPixmap(ic.pixmap(K["d_ring_icon"], K["d_ring_icon"]))
        lay.addWidget(ring, 0, Qt.AlignHCenter)
        self.title_label = QLabel(title, self)
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet(
            f"QLabel {{ color: {T['text']}; background: transparent; font-size: {T['f_l']}px;"
            f" font-weight: 600; }}")
        lay.addWidget(self.title_label)
        self.text_label = QLabel(text, self)
        self.text_label.setAlignment(Qt.AlignCenter)
        self.text_label.setWordWrap(True)
        self.text_label.setStyleSheet(
            f"QLabel {{ color: {T['muted']}; background: transparent; font-size: {T['f_m']}px; }}")
        self.text_label.setVisible(bool(text))
        lay.addWidget(self.text_label)
        self._actions = QHBoxLayout()
        self._actions.setContentsMargins(0, T["s_1"], 0, 0)
        self._actions.setSpacing(T["s_2"])
        self._actions.setAlignment(Qt.AlignHCenter)
        lay.addLayout(self._actions)

    def set_title(self, title):
        self.title_label.setText(title)

    def set_text(self, text):
        self.text_label.setText(text)
        self.text_label.setVisible(bool(text))

    def add_action(self, widget):
        self._actions.addWidget(widget)


# ---------------------------------------------------------------- Chip and ChipBar

class Chip(_ReasonMixin, QAbstractButton):
    def __init__(self, text="", parent=None, *, count=None, checked=False, menu=None,
                 value_when_on=None, tooltip="", has_menu=False):
        super().__init__(parent)
        self.setObjectName("KitChip")
        self.setProperty("class", "FilterChip")
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFixedHeight(T["h_chip"])
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._base_text = text
        self._count = count
        self._menu = menu
        self._has_menu = has_menu or (menu is not None)
        self._value_when_on = value_when_on
        self._compact_hide = False
        self._hover = False
        if tooltip:
            self.setToolTip(tooltip)

    def text(self):
        if self.isChecked() and self._value_when_on:
            return self._value_when_on
        return self._base_text

    def base_text(self):
        return self._base_text

    def setText(self, text):
        self._base_text = text
        self.updateGeometry()
        self.update()

    def set_text(self, text):
        self.setText(text)

    def count(self):
        return self._count

    def set_count(self, count):
        self._count = count
        self.updateGeometry()
        self.update()

    def value_when_on(self):
        return self._value_when_on

    def set_value_when_on(self, val):
        self._value_when_on = val
        self.updateGeometry()
        self.update()

    def set_value(self, val):
        self.set_value_when_on(val)
        self.setChecked(bool(val))

    def menu(self):
        return self._menu

    def set_menu(self, menu):
        self._menu = menu
        self._has_menu = menu is not None
        self.updateGeometry()
        self.update()

    def setMenu(self, menu):
        self.set_menu(menu)

    def set_has_menu(self, on):
        self._has_menu = bool(on)
        self.updateGeometry()
        self.update()

    def has_menu(self):
        return self._has_menu

    def compact_hide(self):
        return self._compact_hide

    def set_compact_hide(self, on):
        self._compact_hide = bool(on)

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._menu is not None and self.isEnabled():
            pos = self.mapToGlobal(QPoint(0, self.height() + 2))
            self._menu.exec(pos)
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            if self._menu is not None and self.isEnabled():
                pos = self.mapToGlobal(QPoint(0, self.height() + 2))
                self._menu.exec(pos)
                event.accept()
                return
        super().keyPressEvent(event)

    def sizeHint(self):
        font = QFont(T["ff"], K["f_chip"])
        font.setWeight(QFont.Medium)
        fm = QFontMetrics(font)
        display_text = self.text()
        w = K["pad_chip_h"] + fm.horizontalAdvance(display_text)
        if self._count is not None:
            b_font = QFont(T["ff"], K["f_badge"])
            b_font.setWeight(QFont.DemiBold)
            bfm = QFontMetrics(b_font)
            bw = bfm.horizontalAdvance(str(self._count)) + 2 * K["pad_badge_h"]
            w += K["gap_chip"] + bw
        if self._has_menu:
            w += K["gap_chip"] + 8
        w += K["pad_chip_h"]
        return QSize(max(w, 28), T["h_chip"])

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        enabled = self.isEnabled()
        checked = self.isChecked()
        focused = self.hasFocus()
        alpha = 1.0 if enabled else _DISABLED_ALPHA

        if checked:
            bg_col = _qcolor(T["emdeep"], alpha)
            line_col = _qcolor(T["em"], alpha)
            text_col = _qcolor(T["mint"], alpha)
            badge_bg = _qcolor(T["emline"], alpha)
            badge_tx = _qcolor(T["mint"], alpha)
            chevron_col = _qcolor(T["mint"], alpha)
        else:
            bg_col = _qcolor(K["chip_bg"], alpha)
            line_col = _qcolor(K["chip_hover_line"] if (self._hover and enabled) else K["chip_line"], alpha)
            text_col = _qcolor(T["text"] if (self._hover and enabled) else T["text2"], alpha)
            badge_bg = _qcolor(K["badge_bg"], alpha)
            badge_tx = _qcolor(K["badge_tx"], alpha)
            chevron_col = text_col

        # Background pill
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        radius = K["r_chip"]
        p.setPen(QPen(line_col, 1.0))
        p.setBrush(QBrush(bg_col))
        p.drawRoundedRect(r, radius, radius)

        # Focus ring
        if focused and enabled:
            p.setPen(QPen(QColor(T["mint"]), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5, radius - 0.5)

        # Draw text
        font = QFont(T["ff"], K["f_chip"])
        font.setWeight(QFont.Medium)
        p.setFont(font)
        p.setPen(text_col)
        fm = QFontMetrics(font)
        display_text = self.text()
        text_w = fm.horizontalAdvance(display_text)
        cur_x = float(K["pad_chip_h"])
        text_r = QRectF(cur_x, 0, text_w, self.height())
        p.drawText(text_r, Qt.AlignVCenter | Qt.AlignLeft, display_text)
        cur_x += text_w

        # Draw badge
        if self._count is not None:
            cur_x += float(K["gap_chip"])
            b_font = QFont(T["ff"], K["f_badge"])
            b_font.setWeight(QFont.DemiBold)
            bfm = QFontMetrics(b_font)
            badge_str = str(self._count)
            bw = float(bfm.horizontalAdvance(badge_str) + 2 * K["pad_badge_h"])
            bh = float(K["h_badge"])
            by = (self.height() - bh) / 2.0
            badge_r = QRectF(cur_x, by, bw, bh)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(badge_bg))
            p.drawRoundedRect(badge_r, K["r_badge"], K["r_badge"])
            p.setFont(b_font)
            p.setPen(badge_tx)
            p.drawText(badge_r, Qt.AlignCenter, badge_str)
            cur_x += bw

        # Draw chevron
        if self._has_menu:
            cur_x += float(K["gap_chip"])
            ch_w = 8.0
            ch_h = 5.0
            ch_y = (self.height() - ch_h) / 2.0
            poly = QPolygonF([
                QPointF(cur_x, ch_y),
                QPointF(cur_x + ch_w, ch_y),
                QPointF(cur_x + ch_w / 2.0, ch_y + ch_h),
            ])
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(chevron_col))
            p.drawPolygon(poly)

        p.end()


FilterChip = Chip


class ChipBar(QFrame):
    currentChanged = Signal(object)
    selectionChanged = Signal(object)
    compactChanged = Signal(bool)

    def __init__(self, parent=None, *, single_select=False, chips=()):
        super().__init__(parent)
        self.setObjectName("KitChipBar")
        self.setStyleSheet("QFrame#KitChipBar { background: transparent; border: none; }")
        self.setFixedHeight(T["h_chip"])
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._single_select = single_select
        self._compact = False
        self._chips = []
        self._chips_by_id = {}
        self._overflow_chip = None
        self._overflow_menu = None
        self._selected_id = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(K["gap_chip"])
        lay.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        for item in chips:
            self.add_chip(item)

    def add_chip(self, text_or_chip, *, id=None, count=None, checked=False, menu=None,
                 value_when_on=None, tooltip="", compact_hide=False):
        if isinstance(text_or_chip, Chip):
            chip = text_or_chip
            if chip.parentWidget() is not self:
                chip.setParent(self)
        else:
            chip = Chip(str(text_or_chip), self, count=count, checked=checked, menu=menu,
                        value_when_on=value_when_on, tooltip=tooltip)
        if id is None:
            id = text_or_chip if isinstance(text_or_chip, str) else chip.base_text()
        chip._chip_id = id
        chip.set_compact_hide(compact_hide)
        self._chips.append(chip)
        self._chips_by_id[id] = chip
        chip.toggled.connect(lambda on, c=chip: self._on_chip_toggled(c, on))
        self.layout().addWidget(chip)
        if checked:
            self.set_selected(id)
        return chip

    def _on_chip_toggled(self, chip, on):
        if self._single_select:
            if on:
                for other in self._chips:
                    if other is not chip and other.isChecked():
                        other.blockSignals(True)
                        other.setChecked(False)
                        if other is self._overflow_chip:
                            other.set_value_when_on(None)
                        other.blockSignals(False)
                        other.update()
                self._selected_id = getattr(chip, "_chip_id", None)
                self.currentChanged.emit(self._selected_id)
                self.selectionChanged.emit(chip)
            else:
                if not any(c.isChecked() for c in self._chips):
                    chip.blockSignals(True)
                    chip.setChecked(True)
                    chip.blockSignals(False)
                    chip.update()
        else:
            self.selectionChanged.emit(chip)

    def add_overflow_chip(self, menu=None, text="More", title="More filters"):
        chip = Chip(text, self, menu=menu, tooltip=title, has_menu=True)
        chip._chip_id = "__overflow__"
        self._overflow_chip = chip
        self._overflow_menu = menu
        self._chips.append(chip)
        self._chips_by_id["__overflow__"] = chip
        chip.toggled.connect(lambda on, c=chip: self._on_chip_toggled(c, on))
        self.layout().addWidget(chip)
        return chip

    def set_overflow_menu(self, menu, text="More"):
        if self._overflow_chip is None:
            return self.add_overflow_chip(menu, text=text)
        self._overflow_chip.set_menu(menu)
        self._overflow_menu = menu
        return self._overflow_chip

    def select_overflow_value(self, value_text, id=None):
        if self._overflow_chip is not None:
            self._overflow_chip.set_value(value_text)
            self._selected_id = id or value_text
            self.currentChanged.emit(self._selected_id)
            self.selectionChanged.emit(self._overflow_chip)

    def chips(self):
        return list(self._chips)

    def chip(self, id):
        return self._chips_by_id.get(id)

    def selected_chip(self):
        for c in self._chips:
            if c.isChecked():
                return c
        return None

    def selected_id(self):
        return self._selected_id

    def set_selected(self, id_or_chip):
        target = self.chip(id_or_chip) if not isinstance(id_or_chip, Chip) else id_or_chip
        if target is not None:
            if not target.isChecked():
                target.setChecked(True)
            else:
                self._on_chip_toggled(target, True)

    def is_compact(self):
        return self._compact

    def set_compact(self, on):
        self._compact = bool(on)
        for c in self._chips:
            if c.compact_hide():
                c.setVisible(not self._compact)
        self.compactChanged.emit(self._compact)

    def apply_width_mode(self, mode):
        val = getattr(mode, "value", mode)
        self.set_compact(str(val).lower() == "compact")


# ---------------------------------------------------------------- StatusStrip

class StatusStrip(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("KitStatusStrip")
        self.setStyleSheet(
            f"QFrame#KitStatusStrip {{ background: {T['card']}; border: 1px solid {T['line']};"
            f" border-radius: {T['r_l']}px; min-height: {K['h_strip']}px; }}"
        )
        self.setMinimumHeight(K["h_strip"])
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(K["pad_strip_h"], K["pad_strip_v"], K["pad_strip_h"], K["pad_strip_v"])
        self._lay.setSpacing(K["gap_strip"])
        self._lay.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

    def add_pill(self, text_or_pill, tone="neutral", *, dot=False):
        if isinstance(text_or_pill, Pill):
            p = text_or_pill
            if p.parentWidget() is not self:
                p.setParent(self)
        else:
            p = Pill(str(text_or_pill), tone=tone, parent=self, dot=dot)
        self._lay.addWidget(p)
        return p

    def add_fact(self, bold_text, sub_text=""):
        lbl = QLabel(self)
        lbl.setStyleSheet(f"QLabel {{ background: transparent; color: {T['text']}; font-size: {T['f_m']}px; }}")
        if sub_text:
            lbl.setText(f"<b>{bold_text}</b> {sub_text}")
        else:
            lbl.setText(f"<b>{bold_text}</b>")
        self._lay.addWidget(lbl)
        return lbl

    def add_text(self, text, *, bold=False, muted=False):
        lbl = QLabel(text, self)
        col = T["muted"] if muted else T["text"]
        weight = "font-weight: 600;" if bold else ""
        lbl.setStyleSheet(f"QLabel {{ background: transparent; color: {col}; font-size: {T['f_m']}px; {weight} }}")
        self._lay.addWidget(lbl)
        return lbl

    def add_separator(self):
        sep = QFrame(self)
        sep.setFixedSize(1, K["h_strip_sep"])
        sep.setStyleSheet(f"QFrame {{ background: {T['line2']}; border: none; }}")
        self._lay.addWidget(sep)
        return sep

    def add_widget(self, widget):
        self._lay.addWidget(widget)
        return widget

    def add_stretch(self, factor=1):
        self._lay.addStretch(factor)

    def clear(self):
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()


# ---------------------------------------------------------------- SelectionBar

class SelectionBar(QFrame):
    def __init__(self, parent=None, *, hint="Select a client row to open, edit or archive it. Select cells to colour them."):
        super().__init__(parent)
        self.setObjectName("KitSelectionBar")
        self.setFixedHeight(K["h_selbar"])
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._empty_hint = hint
        self._compact = False
        self._has_selection = False
        self._who_text = ""
        self._actions = []

        lay = QHBoxLayout(self)
        lay.setContentsMargins(K["pad_selbar_l"], 0, K["pad_selbar_r"], 0)
        lay.setSpacing(6)
        lay.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        # Empty hint
        self.hint_label = QLabel(self._empty_hint, self)
        self.hint_label.setObjectName("KitSelectionHint")
        self.hint_label.setStyleSheet(
            f"QLabel#KitSelectionHint {{ background: transparent; color: {T['muted']};"
            f" font-size: {T['f_s']}px; }}"
        )
        lay.addWidget(self.hint_label)

        # Active container
        self.active_container = QWidget(self)
        self.active_container.setObjectName("KitSelectionActive")
        self.active_container.setStyleSheet("QWidget#KitSelectionActive { background: transparent; }")
        self._act_lay = QHBoxLayout(self.active_container)
        self._act_lay.setContentsMargins(0, 0, 0, 0)
        self._act_lay.setSpacing(6)
        self._act_lay.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        lay.addWidget(self.active_container)

        self.who_label = QLabel("", self.active_container)
        self.who_label.setObjectName("KitSelectionWho")
        self.who_label.setStyleSheet(
            f"QLabel#KitSelectionWho {{ background: transparent; color: {T['text']};"
            f" font-weight: 600; font-size: {T['f_m']}px; max-width: 220px; }}"
        )
        self._act_lay.addWidget(self.who_label)

        self.set_has_selection(False)
        self._restyle()

    def _restyle(self):
        if self._has_selection:
            self.setStyleSheet(
                f"QFrame#KitSelectionBar {{ background: {K['selbar_has_bg']};"
                f" border: 1px solid {K['selbar_has_line']}; border-radius: {K['r_selbar']}px; }}"
            )
        else:
            self.setStyleSheet(
                f"QFrame#KitSelectionBar {{ background: {T['card']};"
                f" border: 1px solid {T['line']}; border-radius: {K['r_selbar']}px; }}"
            )

    def set_has_selection(self, has: bool):
        self._has_selection = bool(has)
        self.hint_label.setVisible(not self._has_selection)
        self.active_container.setVisible(self._has_selection)
        self._restyle()

    def has_selection(self):
        return self._has_selection

    def set_empty(self, empty: bool = True):
        self.set_has_selection(not empty)

    def set_who(self, who: str):
        self._who_text = who or ""
        self.who_label.setText(self._who_text)
        self.set_has_selection(bool(who))

    def who(self):
        return self._who_text

    def set_hint(self, hint: str):
        self._empty_hint = hint
        self.hint_label.setText(hint)

    def hint(self):
        return self._empty_hint

    def add_action(self, action_or_text, *, variant="secondary", icon=None, sm=True,
                   tooltip="", keep_label=False):
        if isinstance(action_or_text, (LabelledAction, IconButton, QAbstractButton)):
            btn = action_or_text
            if btn.parentWidget() is not self.active_container:
                btn.setParent(self.active_container)
        else:
            btn = LabelledAction(str(action_or_text), self.active_container, variant=variant,
                                 icon=icon, sm=sm, tooltip=tooltip)
        btn.setFocusPolicy(Qt.NoFocus)
        btn_text = btn.text() if hasattr(btn, "text") else ""
        btn._keep_label = keep_label or (btn_text == "Open")
        self._actions.append(btn)
        self._act_lay.addWidget(btn)
        return btn

    def add_divider(self):
        vr = QFrame(self.active_container)
        vr.setFixedSize(1, K["h_selbar_vr"])
        vr.setStyleSheet(f"QFrame {{ background: {K['selbar_vr']}; margin: 0 6px; border: none; }}")
        self._act_lay.addWidget(vr)
        return vr

    def add_group_label(self, text: str):
        lbl = QLabel(text.upper(), self.active_container)
        lbl.setStyleSheet(
            f"QLabel {{ background: transparent; color: {K['selbar_grp_tx']};"
            f" font-size: {K['f_selbar_grp']}px; font-weight: 600; margin-right: 2px; }}"
        )
        self._act_lay.addWidget(lbl)
        return lbl

    def add_spacer(self, factor=1):
        self._act_lay.addStretch(factor)

    def add_danger_action(self, text="Delete…", *, icon="mdi.delete-outline", sm=True, tooltip=""):
        return self.add_action(text, variant="danger", icon=icon, sm=sm, tooltip=tooltip)

    def is_compact(self):
        return self._compact

    def set_compact(self, on):
        self._compact = bool(on)
        for btn in self._actions:
            if isinstance(btn, LabelledAction):
                if getattr(btn, "_keep_label", False):
                    continue
                btn.set_icon_only(self._compact)

    def apply_width_mode(self, mode):
        val = getattr(mode, "value", mode)
        self.set_compact(str(val).lower() == "compact")


# ---------------------------------------------------------------- UnsavedBar

class UnsavedBar(QFrame):
    discard_clicked = Signal()
    save_clicked = Signal()

    def __init__(self, parent=None, *, reserved_space=False):
        super().__init__(parent)
        self.setObjectName("KitUnsavedBar")
        self.setFixedHeight(K["h_unsaved"])
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._count = 0
        self._reserved_space = reserved_space

        lay = QHBoxLayout(self)
        lay.setContentsMargins(K["pad_unsaved_l"], K["pad_unsaved_v"], K["pad_unsaved_r"], K["pad_unsaved_v"])
        lay.setSpacing(T["s_3"])
        lay.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.icon_label = QLabel(self)
        self.icon_label.setFixedSize(18, 18)
        self.icon_label.setStyleSheet("QLabel { background: transparent; }")
        ic = _icon("mdi.alert-outline", K["unsaved_tx"])
        if ic is not None:
            self.icon_label.setPixmap(ic.pixmap(18, 18))
        lay.addWidget(self.icon_label)

        self.label = QLabel("0 unsaved changes", self)
        self.label.setStyleSheet(
            f"QLabel {{ background: transparent; color: {K['unsaved_tx']};"
            f" font-weight: 600; font-size: {T['f_m']}px; }}"
        )
        lay.addWidget(self.label)

        lay.addStretch(1)

        self.discard_btn = LabelledAction("Discard", self, variant="secondary", sm=True)
        self.discard_btn.clicked.connect(self.discard_clicked)
        lay.addWidget(self.discard_btn)

        self.save_btn = LabelledAction("Save changes", self, variant="primary", icon="mdi.check", sm=True)
        self.save_btn.clicked.connect(self.save_clicked)
        lay.addWidget(self.save_btn)

        self.set_count(0)

    def count(self):
        return self._count

    def is_dirty(self):
        return self._count > 0

    def set_count(self, count: int):
        self._count = max(0, int(count))
        text = f"{self._count} unsaved change" if self._count == 1 else f"{self._count} unsaved changes"
        self.label.setText(text)
        is_dirty = self._count > 0
        if self._reserved_space:
            self.setVisible(True)
            self.icon_label.setVisible(is_dirty)
            self.label.setVisible(is_dirty)
            self.discard_btn.setVisible(is_dirty)
            self.save_btn.setVisible(is_dirty)
            if is_dirty:
                self.setStyleSheet(
                    f"QFrame#KitUnsavedBar {{ background: {K['unsaved_bg']}; border: 1px solid {T['warnline']};"
                    f" border-radius: {K['r_unsaved']}px; }}"
                )
            else:
                self.setStyleSheet("QFrame#KitUnsavedBar { background: transparent; border: none; }")
        else:
            self.setStyleSheet(
                f"QFrame#KitUnsavedBar {{ background: {K['unsaved_bg']}; border: 1px solid {T['warnline']};"
                f" border-radius: {K['r_unsaved']}px; }}"
            )
            self.setVisible(is_dirty)

    def set_reserved_space(self, on: bool):
        self._reserved_space = bool(on)
        self.set_count(self._count)

    def reserved_space(self):
        return self._reserved_space


# ---------------------------------------------------------------- DataGrid

# Item roles the grid delegate draws; well above the UserRole + n the screens use themselves.
ROLE_SERVICES = Qt.UserRole + 0x100   # [(name, colour), ...]: service dots, "+N" when they do not fit
ROLE_PILL = Qt.UserRole + 0x101       # (text, tone) or (text, tone, dot): status pill
ROLE_TAG = Qt.UserRole + 0x102        # (text, tone): mono tag (list tables)
ROLE_ACTIONS = Qt.UserRole + 0x103    # [CellAction, ...]: buttons drawn in the cell
ROLE_SUB = Qt.UserRole + 0x104        # muted mono second line: makes the cell a two-line "tall" cell

GRID_KINDS = ("text", "num", "mono", "strong")

# tone: surfaces and text of the three table looks (white grid, dark grid, dark list table)
_GRID_TONES = {
    "white": {
        "bg": T["g_bg"], "alt": T["g_alt"], "hover": T["g_hover"], "sel": T["g_sel"],
        "line": T["g_line"], "text": T["g_text"], "sel_text": K["grid_sel_tx"], "mute": T["g_mute"],
        "mono": K["grid_mono_tx"], "head": T["g_head"], "head_text": T["g_headtx"],
        "head_rule": K["grid_head_rule"], "head_line": None, "wrap": K["grid_wrap_line"],
        "foot": K["grid_foot_bg"], "foot_line": T["g_line"], "btn_bg": K["white"],
        "btn_line": K["grid_btn_line"], "btn_tx": K["grid_btn_tx"], "btn_hover": T["g_hover"],
        "svc_tx": K["grid_btn_tx"], "cb_bg": K["white"], "cb_line": K["grid_btn_line"],
    },
    "dark": {
        "bg": T["panel"], "alt": K["dgrid_alt"], "hover": K["dgrid_hover"], "sel": K["dgrid_sel"],
        "line": K["dgrid_line"], "text": T["text2"], "sel_text": T["text"], "mute": T["muted"],
        "mono": T["muted"], "head": K["dgrid_head"], "head_text": T["mint"],
        "head_rule": K["grid_head_rule"], "head_line": None, "wrap": T["line2"],
        "foot": T["panel"], "foot_line": T["line"], "btn_bg": K["btn_bg"], "btn_line": T["line2"],
        "btn_tx": T["text2"], "btn_hover": T["hover"], "svc_tx": T["text2"], "cb_bg": K["btn_bg"],
        "cb_line": T["line3"],
    },
    "list": {
        "bg": "transparent", "alt": None, "hover": K["list_hover"], "sel": K["list_sel"],
        "line": K["row_line"], "text": T["text"], "sel_text": T["text"], "mute": T["muted"],
        "mono": T["muted"], "head": "transparent", "head_text": T["muted"], "head_rule": None,
        "head_line": T["line"], "wrap": T["line"], "foot": T["card"], "foot_line": T["line"],
        "btn_bg": K["btn_bg"], "btn_line": T["line2"], "btn_tx": T["text2"], "btn_hover": T["hover"],
        "svc_tx": T["text2"], "cb_bg": K["btn_bg"], "cb_line": T["line3"],
    },
}

# status pills drawn in cells: tone -> (background, text, border); "green" is the tracker's softer emerald
_GRID_PILLS = {
    "white": {
        "neutral": (K["gpill_x_bg"], K["gpill_x_tx"], "transparent"),
        "emerald": (K["gpill_g_bg"], K["gpill_g_tx"], "transparent"),
        "green": (K["gpill_g_bg"], K["gpill_g_tx"], "transparent"),
        "amber": (K["gpill_w_bg"], K["gpill_w_tx"], "transparent"),
        "red": (K["gpill_r_bg"], K["gpill_r_tx"], "transparent"),
        "blue": (K["gpill_b_bg"], K["gpill_b_tx"], "transparent"),
    },
    "dark": {
        **_PILL_TONES,
        "emerald": (K["dpill_g_bg"], T["mint"], "transparent"),
        "green": (K["dpill_g2_bg"], K["dpill_g2_tx"], "transparent"),
        "red": (T["dangerdim"], T["dangertx"], "transparent"),
    },
    "list": {**_PILL_TONES, "green": _PILL_TONES["emerald"]},
}


def _as_brush(value):
    """A BackgroundRole / ForegroundRole value (QBrush, QColor or colour name) as a QBrush, or None."""
    if value is None:
        return None
    brush = value if isinstance(value, QBrush) else QBrush(QColor(value))
    return brush if brush.style() != Qt.NoBrush else None


def _is_checked(value):
    return getattr(value, "value", value) == Qt.Checked.value


@lru_cache(maxsize=256)
def _cell_icon(name, colour):
    return _icon(name, colour)


class CellAction:
    """One button the grid delegate draws in a cell (ROLE_ACTIONS); never a widget per row."""

    __slots__ = ("key", "icon", "tooltip", "primary", "colour", "enabled")

    def __init__(self, key, icon, tooltip, *, primary=False, colour=None, enabled=True):
        if not tooltip:
            raise ValueError("CellAction needs a tooltip")
        self.key = key
        self.icon = icon
        self.tooltip = tooltip
        self.primary = primary
        self.colour = colour
        self.enabled = enabled


class GridDelegate(QStyledItemDelegate):
    """Draws every cell of a DataGrid / DarkListTable: zebra, hover, mint selection with the emerald
    edge, the cell-format highlights (BackgroundRole / ForegroundRole win, as today), pills, tags,
    service dots, two-line cells, checkboxes and action buttons. Works on any QTableView."""

    def __init__(self, view, tone="white"):
        super().__init__(view)
        self._view = view
        self.tone = tone

    # ---- geometry shared by paint, clicks and tooltips
    def _pal(self):
        return _GRID_TONES[self.tone]

    def _pad(self):
        return K["pad_list_cell"] if self.tone == "list" else K["pad_cell"]

    @staticmethod
    def _has_check(index):
        """Checkable with a state set (QTableWidgetItem is user-checkable by default, like Qt itself)."""
        return bool(index.flags() & Qt.ItemIsUserCheckable) and index.data(Qt.CheckStateRole) is not None

    def _content_rect(self, rect, index):
        pad = self._pad()
        r = rect.adjusted(pad, 0, -pad, -1)
        if self._has_check(index):
            r.setLeft(r.left() + K["d_cell_cb"] + T["s_2"])
        return r

    def _check_rect(self, rect):
        d = K["d_cell_cb"]
        return QRect(rect.left() + self._pad(), rect.top() + (rect.height() - d) // 2, d, d)

    def action_rects(self, rect, actions):
        side, gap = K["h_cell_btn"], K["gap_cell_btn"]
        x, y = rect.left() + self._pad(), rect.top() + (rect.height() - side) // 2
        return [QRect(x + i * (side + gap), y, side, side) for i in range(len(actions))]

    def _action_at(self, rect, actions, pos):
        for action, r in zip(actions, self.action_rects(rect, actions)):
            if r.contains(pos):
                return action
        return None

    def _kind(self, index):
        get = getattr(self._view, "column_kind", None)
        return get(index.column()) if get else "text"

    def _text_font(self, option, index, selected=False):
        font = index.data(Qt.FontRole)
        if font is not None:
            return QFont(font)
        font = QFont(option.font)
        kind = self._kind(index)
        if kind == "mono":
            font.setFamily(_MONO)
            font.setPixelSize(T["f_s"])
        elif kind == "strong":
            font.setWeight(QFont.DemiBold)
        elif selected and self.tone == "white":
            font.setWeight(QFont.Medium)
        return font

    def _small_font(self, option, size, mono=False):
        font = QFont(option.font)
        if mono:
            font.setFamily(_MONO)
        font.setPixelSize(size)
        font.setWeight(QFont.DemiBold)
        return font

    def _pill_colours(self, tone):
        tones = _GRID_PILLS[self.tone]
        return tones.get(tone) or tones["neutral"]

    def _services_shown(self, fm, items, width):
        """How many services fit in `width` before the "+N" counter takes over."""
        x, dot, gap = 0, K["d_svc_dot"], K["gap_svc"]
        for i, (name, _colour) in enumerate(items):
            rest = len(items) - i - 1
            need = dot + T["s_1"] + fm.horizontalAdvance(name)
            if rest:
                need += gap + fm.horizontalAdvance(f"+{rest}")
            if i and x + need > width:
                return i
            x += dot + T["s_1"] + fm.horizontalAdvance(name) + gap
        return len(items)

    def _selection_starts(self, index):
        """True when the cell to the left (on screen) is not selected: that cell carries the edge."""
        view = self._view
        header = view.horizontalHeader()
        sm = view.selectionModel()
        for v in range(header.visualIndex(index.column()) - 1, -1, -1):
            col = header.logicalIndex(v)
            if not view.isColumnHidden(col):
                return not sm.isSelected(index.siblingAtColumn(col))
        return True

    # ---- painting
    def paint(self, painter, option, index):
        pal = self._pal()
        view = self._view
        rect = option.rect
        row = index.row()
        selected = bool(option.state & QStyle.State_Selected)
        painter.save()

        highlight = _as_brush(index.data(Qt.BackgroundRole))
        if highlight is not None:           # the cell-format engine's fill wins (.grid td.hl)
            painter.fillRect(rect, highlight)
        else:
            if selected:
                fill = pal["sel"]
            elif row == getattr(view, "_hover_row", -1):
                fill = pal["hover"]
            elif pal["alt"] and row % 2:
                fill = pal["alt"]
            else:
                fill = pal["bg"]
            if fill != "transparent":
                painter.fillRect(rect, QColor(fill))
        if not (self.tone == "list" and row == index.model().rowCount() - 1):
            painter.fillRect(QRect(rect.left(), rect.bottom(), rect.width(), 1), QColor(pal["line"]))
        if selected and self._selection_starts(index):
            painter.fillRect(QRect(rect.left(), rect.top(), K["w_sel_edge"], rect.height()), QColor(T["em"]))
        cell_cursor = (option.state & QStyle.State_HasFocus
                       and view.selectionBehavior() == QAbstractItemView.SelectItems)
        if (selected and highlight is not None) or cell_cursor:
            painter.setPen(QPen(QColor(T["em"])))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect.adjusted(0, 0, -1, -2))

        painter.setRenderHint(QPainter.Antialiasing)
        if self._has_check(index):
            self._paint_check(painter, self._check_rect(rect), _is_checked(index.data(Qt.CheckStateRole)),
                              bool(index.flags() & Qt.ItemIsEnabled))
        cr = self._content_rect(rect, index)

        actions = index.data(ROLE_ACTIONS)
        services = index.data(ROLE_SERVICES)
        pill = index.data(ROLE_PILL)
        tag = index.data(ROLE_TAG)
        if actions:
            self._paint_actions(painter, rect, actions)
        elif services:
            self._paint_services(painter, option, cr, services)
        elif pill:
            self._paint_pill(painter, option, cr, *pill)
        elif tag:
            self._paint_tag(painter, option, cr, *tag)
        else:
            self._paint_text(painter, option, index, cr, selected, highlight)
        painter.restore()

    def _text_colour(self, index, selected):
        fg = _as_brush(index.data(Qt.ForegroundRole))
        if fg is not None:
            return fg.color()
        pal = self._pal()
        if selected:
            return QColor(pal["sel_text"])
        kind = self._kind(index)
        return QColor(pal["mute"] if kind == "num" else pal["mono"] if kind == "mono" else pal["text"])

    def _paint_text(self, painter, option, index, cr, selected, highlight):
        text = str(index.data(Qt.DisplayRole) if index.data(Qt.DisplayRole) is not None else "")
        text = text.replace("\n", " ")
        font = self._text_font(option, index, selected)
        colour = self._text_colour(index, selected)
        if highlight is not None and _as_brush(index.data(Qt.ForegroundRole)) is None:
            # A fill without a text colour: dark ink on a light fill, light ink on a dark one.
            colour = QColor(T["g_text"] if highlight.color().lightness() > 140 else T["text"])
        sub = index.data(ROLE_SUB)
        align = index.data(Qt.TextAlignmentRole)
        if align is None:
            align = Qt.AlignLeft | Qt.AlignVCenter
        elif not isinstance(align, Qt.AlignmentFlag):
            align = Qt.AlignmentFlag(int(align))
        if not align & Qt.AlignVertical_Mask:
            align |= Qt.AlignVCenter
        if not sub:
            fm = QFontMetrics(font)
            painter.setFont(font)
            painter.setPen(colour)
            painter.drawText(cr, align, fm.elidedText(text, Qt.ElideRight, max(cr.width(), 0)))
            return
        top_font = QFont(font)
        top_font.setWeight(QFont.DemiBold)
        sub_font = self._small_font(option, T["f_xs"], mono=True)
        sub_font.setWeight(QFont.Normal)
        fm1, fm2 = QFontMetrics(top_font), QFontMetrics(sub_font)
        gap = 2
        y = cr.top() + (cr.height() - fm1.height() - fm2.height() - gap) // 2
        painter.setFont(top_font)
        painter.setPen(colour)
        painter.drawText(QRect(cr.left(), y, cr.width(), fm1.height()), Qt.AlignLeft | Qt.AlignVCenter,
                         fm1.elidedText(text, Qt.ElideRight, max(cr.width(), 0)))
        painter.setFont(sub_font)
        painter.setPen(QColor(self._pal()["mute"]))
        painter.drawText(QRect(cr.left(), y + fm1.height() + gap, cr.width(), fm2.height()),
                         Qt.AlignLeft | Qt.AlignVCenter,
                         fm2.elidedText(str(sub), Qt.ElideRight, max(cr.width(), 0)))

    def _paint_check(self, painter, r, checked, enabled):
        pal = self._pal()
        painter.setOpacity(1.0 if enabled else _DISABLED_ALPHA)
        rf = QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(QColor(T["em2"] if checked else pal["cb_line"]), 1))
        painter.setBrush(QColor(T["em"] if checked else pal["cb_bg"]))
        painter.drawRoundedRect(rf, K["r_cb"], K["r_cb"])
        if checked:
            painter.setPen(QPen(QColor(K["white"]), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.setBrush(Qt.NoBrush)
            x, y, s = rf.left(), rf.top(), rf.width()
            painter.drawPolyline(QPolygonF([QPointF(x + s * 0.24, y + s * 0.52),
                                            QPointF(x + s * 0.43, y + s * 0.70),
                                            QPointF(x + s * 0.76, y + s * 0.32)]))
        painter.setOpacity(1.0)

    def _paint_actions(self, painter, rect, actions):
        pal = self._pal()
        hover = getattr(self._view, "_hover_pos", None)
        for action, r in zip(actions, self.action_rects(rect, actions)):
            hot = action.enabled and hover is not None and r.contains(hover)
            if action.primary:
                base = QColor(action.colour or T["em"])
                bg = base.lighter(115) if (hot and action.colour) else QColor(T["em2"]) if hot else base
                line, ink = QColor(action.colour or T["em2"]), K["white"]
            else:
                bg = QColor(pal["btn_hover"] if hot else pal["btn_bg"])
                line, ink = QColor(pal["btn_line"]), pal["btn_tx"]
            painter.setOpacity(1.0 if action.enabled else _DISABLED_ALPHA)
            painter.setPen(QPen(line, 1))
            painter.setBrush(bg)
            painter.drawRoundedRect(QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5), T["r_s"], T["r_s"])
            icon = action.icon if isinstance(action.icon, QIcon) else _cell_icon(action.icon, ink)
            if icon is not None and not icon.isNull():
                side = 14
                icon.paint(painter, QRect(r.center().x() - side // 2 + 1, r.center().y() - side // 2 + 1,
                                          side, side))
        painter.setOpacity(1.0)

    def _paint_services(self, painter, option, cr, items):
        font = self._small_font(option, T["f_s"])
        fm = QFontMetrics(font)
        shown = self._services_shown(fm, items, cr.width())
        dot, gap = K["d_svc_dot"], K["gap_svc"]
        x, cy = cr.left(), cr.center().y()
        painter.setFont(font)
        for name, colour in items[:shown]:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(colour))
            painter.drawEllipse(QRectF(x, cy - dot / 2 + 1, dot, dot))
            x += dot + T["s_1"]
            painter.setPen(QColor(self._pal()["svc_tx"]))
            room = max(cr.right() - x, 0)
            label = fm.elidedText(name, Qt.ElideRight, room)
            painter.drawText(QRect(x, cr.top(), room, cr.height()), Qt.AlignLeft | Qt.AlignVCenter, label)
            x += fm.horizontalAdvance(label) + gap
        if shown < len(items):
            painter.setPen(QColor(self._pal()["mute"]))
            painter.drawText(QRect(x, cr.top(), max(cr.right() - x, 0), cr.height()),
                             Qt.AlignLeft | Qt.AlignVCenter, f"+{len(items) - shown}")

    def _pill_geometry(self, option, cr, text, dot):
        font = self._small_font(option, T["f_xs"])
        fm = QFontMetrics(font)
        pad = K["pad_gpill"]
        dot_w = K["d_pill_dot"] + T["s_1"] + 1 if dot else 0
        shown = fm.elidedText(text, Qt.ElideRight, max(cr.width() - 2 * pad - dot_w, 0))
        return font, shown, min(cr.width(), fm.horizontalAdvance(shown) + 2 * pad + dot_w)

    def _paint_pill(self, painter, option, cr, text, tone="neutral", dot=False):
        bg, tx, bd = self._pill_colours(tone)
        h = K["h_pill"] if self.tone == "list" else K["h_gpill"]
        font, shown, w = self._pill_geometry(option, cr, str(text), dot)
        r = QRectF(cr.left(), cr.top() + (cr.height() - h) / 2, w, h)
        painter.setPen(QPen(QColor(bd), 1) if bd != "transparent" else Qt.NoPen)
        painter.setBrush(QColor(bg))
        painter.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), h / 2, h / 2)
        x = r.left() + K["pad_gpill"]
        if dot:
            d = K["d_pill_dot"]
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(tx))
            painter.drawEllipse(QRectF(x, r.center().y() - d / 2, d, d))
            x += d + T["s_1"] + 1
        painter.setFont(font)
        painter.setPen(QColor(tx))
        painter.drawText(QRectF(x, r.top(), r.right() - x, h), Qt.AlignLeft | Qt.AlignVCenter, shown)

    def _paint_tag(self, painter, option, cr, text, tone="neutral"):
        bg, tx, bd = _TAG_TONES.get(tone) or _TAG_TONES["neutral"]
        font = self._small_font(option, T["f_xs"], mono=True)
        fm = QFontMetrics(font)
        pad, h = 7, K["h_tag"]
        shown = fm.elidedText(str(text), Qt.ElideRight, max(cr.width() - 2 * pad, 0))
        r = QRectF(cr.left(), cr.top() + (cr.height() - h) / 2,
                   min(cr.width(), fm.horizontalAdvance(shown) + 2 * pad), h)
        painter.setPen(QPen(QColor(bd), 1))
        painter.setBrush(QColor(bg))
        painter.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), K["r_xs"], K["r_xs"])
        painter.setFont(font)
        painter.setPen(QColor(tx))
        painter.drawText(r.adjusted(pad, 0, -pad, 0), Qt.AlignLeft | Qt.AlignVCenter, shown)

    # ---- clicks, keys and tooltips
    def editorEvent(self, event, model, option, index):
        kind = event.type()
        mouse = kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease, QEvent.MouseButtonDblClick)
        actions = index.data(ROLE_ACTIONS)
        if actions and mouse and event.button() == Qt.LeftButton:
            hit = self._action_at(option.rect, actions, event.position().toPoint())
            if hit is not None:
                if kind == QEvent.MouseButtonRelease and hit.enabled:
                    signal = getattr(self._view, "actionTriggered", None)
                    if signal is not None:
                        signal.emit(index.row(), index.column(), hit.key)
                return kind != QEvent.MouseButtonPress      # a press still selects the row
        flags = index.flags()
        if self._has_check(index) and flags & Qt.ItemIsEnabled:
            toggle = False
            if mouse and event.button() == Qt.LeftButton:
                inside = self._check_rect(option.rect).adjusted(-4, -4, 4, 4).contains(event.position().toPoint())
                if not inside:
                    return False
                toggle = kind == QEvent.MouseButtonRelease
            elif kind == QEvent.KeyPress and event.key() in (Qt.Key_Space, Qt.Key_Select):
                toggle = True
            else:
                return False
            if toggle:
                state = Qt.Unchecked if _is_checked(index.data(Qt.CheckStateRole)) else Qt.Checked
                model.setData(index, state, Qt.CheckStateRole)
            return True
        return super().editorEvent(event, model, option, index)

    def cell_tooltip(self, option, index, pos=None):
        """What the cell's tooltip says: the button under `pos`, else the full text when it is cut."""
        actions = index.data(ROLE_ACTIONS)
        if actions:
            hit = self._action_at(option.rect, actions, pos) if pos is not None else None
            return hit.tooltip if hit else ""
        cr = self._content_rect(option.rect, index)
        services = index.data(ROLE_SERVICES)
        if services:
            fm = QFontMetrics(self._small_font(option, T["f_s"]))
            if self._services_shown(fm, services, cr.width()) < len(services):
                return ", ".join(name for name, _c in services)
            return ""
        pill = index.data(ROLE_PILL)
        if pill:
            _font, shown, _w = self._pill_geometry(option, cr, str(pill[0]), len(pill) > 2 and pill[2])
            return str(pill[0]) if shown != str(pill[0]) else ""
        tag = index.data(ROLE_TAG)
        text = str(tag[0]) if tag else str(index.data(Qt.DisplayRole) or "").replace("\n", " ")
        font = self._small_font(option, T["f_xs"], mono=True) if tag else self._text_font(option, index)
        room = cr.width() - (14 if tag else 0)
        return text if QFontMetrics(font).horizontalAdvance(text) > room else ""

    def helpEvent(self, event, view, option, index):
        if event.type() != QEvent.ToolTip or not index.isValid():
            return super().helpEvent(event, view, option, index)
        tip = self.cell_tooltip(option, index, event.pos())
        if not tip and not index.data(ROLE_ACTIONS) and index.data(Qt.ToolTipRole):
            return super().helpEvent(event, view, option, index)
        if tip:
            QToolTip.showText(event.globalPos(), tip, view)
        else:
            QToolTip.hideText()
        return True


class _GridHeader(QHeaderView):
    """The sticky header: upper-case, letter-spaced, elided, with a small sort arrow."""

    def __init__(self, tone, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.setSectionsClickable(True)
        self.setHighlightSections(False)
        self.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.setMinimumSectionSize(K["w_col_min"] // 2)
        self.tone = None
        self.set_tone(tone)

    def set_tone(self, tone):
        self.tone = tone
        self.setFixedHeight(K["h_list_head"] if tone == "list" else K["h_grid_head"])
        self.setStyleSheet(f"QHeaderView {{ background: {_GRID_TONES[tone]['head']}; border: none; }}")
        self.viewport().update()

    def _font(self):
        font = QFont(self.font())
        font.setPixelSize(T["f_xs"])
        font.setWeight(QFont.DemiBold)
        font.setLetterSpacing(QFont.AbsoluteSpacing, 0.8)
        return font

    def _text_rect(self, rect, logical):
        pad = K["pad_list_cell"] if self.tone == "list" else K["pad_cell"]
        r = rect.adjusted(pad, 0, -pad, 0)
        if self.isSortIndicatorShown() and self.sortIndicatorSection() == logical:
            r.setRight(r.right() - 12)
        return r

    def section_text(self, logical):
        return str(self.model().headerData(logical, Qt.Horizontal, Qt.DisplayRole) or "").upper()

    def paintSection(self, painter, rect, logical):
        pal = _GRID_TONES[self.tone]
        painter.save()
        if pal["head"] != "transparent":
            painter.fillRect(rect, QColor(pal["head"]))
        if pal["head_rule"] and self.visualIndex(logical) > 0:
            painter.fillRect(QRect(rect.left(), rect.top(), 1, rect.height()), QColor(pal["head_rule"]))
        if pal["head_line"]:
            painter.fillRect(QRect(rect.left(), rect.bottom(), rect.width(), 1), QColor(pal["head_line"]))
        font = self._font()
        r = self._text_rect(rect, logical)
        painter.setFont(font)
        painter.setPen(QColor(pal["head_text"]))
        painter.drawText(r, Qt.AlignLeft | Qt.AlignVCenter,
                         QFontMetrics(font).elidedText(self.section_text(logical), Qt.ElideRight, max(r.width(), 0)))
        if self.isSortIndicatorShown() and self.sortIndicatorSection() == logical:
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(pal["head_text"]))
            x, y = r.right() + 6, rect.center().y()
            up = self.sortIndicatorOrder() == Qt.AscendingOrder
            pts = ([QPointF(x - 4, y + 2), QPointF(x + 4, y + 2), QPointF(x, y - 3)] if up else
                   [QPointF(x - 4, y - 2), QPointF(x + 4, y - 2), QPointF(x, y + 3)])
            painter.drawPolygon(QPolygonF(pts))
        painter.restore()

    def viewportEvent(self, event):
        if event.type() == QEvent.ToolTip:
            logical = self.logicalIndexAt(event.pos())
            if logical >= 0 and not self.model().headerData(logical, Qt.Horizontal, Qt.ToolTipRole):
                rect = QRect(self.sectionViewportPosition(logical), 0, self.sectionSize(logical), self.height())
                text = self.section_text(logical)
                if QFontMetrics(self._font()).horizontalAdvance(text) > self._text_rect(rect, logical).width():
                    QToolTip.showText(event.globalPos(), text, self)
                else:
                    QToolTip.hideText()
                return True
        return super().viewportEvent(event)


def fit_column_widths(available, specs, manual=None, user_hidden=(), compact=False):
    """Widths for the columns of a grid `available` px wide (the rule from IMPLEMENTATION 4.2 / 4.7).

    specs: {col: spec} in on-screen order; a spec is a dict as DataGrid.set_column stores it. A column
    with a `width` is fixed (shrinks toward its `minimum` only when space is short); a column without
    one shares what is left by `stretch`, never under its minimum and never over `max_share` of the
    whole width. In COMPACT, `compact_hidden` columns hide; then, while even the minimums do not fit,
    the column with the highest `priority` hides (0 never does; rightmost first on a tie). `manual`
    widths (the user dragged them) replace the preferred width. user_hidden columns stay hidden and
    are not counted. Returns ({col: width} for the visible columns, set of columns fitting hid).
    """
    manual = manual or {}
    order = [c for c in specs if c not in user_hidden]
    pos = {c: i for i, c in enumerate(order)}
    hidden = {c for c in order if compact and specs[c]["compact_hidden"]}

    def pref(c):
        return manual.get(c) or specs[c]["width"]

    def minimum(c):
        m = specs[c]["minimum"]
        m = K["w_col_min"] if m is None else m
        return min(m, pref(c)) if pref(c) else m

    def visible():
        return [c for c in order if c not in hidden]

    while sum(minimum(c) for c in visible()) > available:
        optional = [c for c in visible() if specs[c]["priority"] > 0]
        if not optional:
            break
        hidden.add(max(optional, key=lambda c: (specs[c]["priority"], pos[c])))

    cols = visible()
    fixed = [c for c in cols if pref(c)]
    flex = [c for c in cols if not pref(c)]
    widths = {c: pref(c) for c in fixed}
    deficit = sum(widths.values()) - (available - sum(minimum(c) for c in flex))
    room = {c: widths[c] - minimum(c) for c in fixed}
    total_room = sum(room.values())
    if deficit > 0 and total_room > 0:
        # Shrink in proportion to how much room each fixed column has above its minimum.
        cut = min(deficit, total_room)
        for c in fixed:
            widths[c] -= int(cut * room[c] / total_room)

    left = available - sum(widths.values())
    free = list(flex)
    while free:
        weight = sum(specs[c]["stretch"] for c in free) or 1
        clamped = []
        for c in free:
            share = left * specs[c]["stretch"] / weight
            top = specs[c]["max_share"] * available if specs[c]["max_share"] else None
            if share < minimum(c):
                widths[c] = minimum(c)
            elif top is not None and share > top:
                widths[c] = int(top)
            else:
                continue
            clamped.append(c)
        if not clamped:
            for c in free:
                widths[c] = int(left * specs[c]["stretch"] / weight)
            widths[free[-1]] += left - sum(widths[c] for c in free)
            break
        for c in clamped:
            left -= widths[c]
            free.remove(c)
    if not flex and cols:
        widths[cols[-1]] += max(0, available - sum(widths.values()))
    return widths, hidden


@lru_cache(maxsize=None)
def _grid_qss(tone):
    pal = _GRID_TONES[tone]
    return (
        f"QTableView {{ background: {pal['bg']}; border: none; outline: 0; color: {pal['text']};"
        f" font-size: {T['f_m']}px; gridline-color: {pal['line']};"
        f" selection-background-color: {pal['sel']}; selection-color: {pal['sel_text']}; }}"
        f"QTableView QTableCornerButton::section {{ background: {pal['head']}; border: none; }}"
    )


class DataGrid(QTableWidget):
    """A QTableWidget (so the items, and the search grid's cell-format engine on them, stay as they
    are) drawn by GridDelegate. See the module docstring for the API."""

    actionTriggered = Signal(int, int, str)     # row, column, CellAction.key
    columnsFitted = Signal(list)                # the columns fit_columns() hid for lack of width
    TONES = tuple(_GRID_TONES)

    def __init__(self, rows=0, columns=0, parent=None, *, tone="white", list_mode=False):
        super().__init__(rows, columns, parent)
        self._hover_row = -1
        self._hover_pos = None
        self._specs = {}
        self._fit_hidden = set()
        self._manual = {}
        self._fitting = False
        self.compact = False
        self.list_mode = list_mode
        self.setHorizontalHeader(_GridHeader(tone, self))
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.sectionResized.connect(self._on_section_resized)
        self.verticalHeader().hide()
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.delegate = GridDelegate(self, tone)
        self.setItemDelegate(self.delegate)
        if list_mode:
            header.hide()
            self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tone = None
        self.set_tone(tone)

    def set_tone(self, tone):
        if tone not in _GRID_TONES:
            raise ValueError(f"unknown DataGrid tone: {tone}")
        self.tone = tone
        self.delegate.tone = tone
        self.horizontalHeader().set_tone(tone)
        self.setStyleSheet(_grid_qss(tone))
        self._size_rows()
        self.viewport().update()

    def set_tall_rows(self, on):
        """52 px rows for two-line (ROLE_SUB) cells, as Tracker Dump's Client column."""
        self._tall = bool(on)
        self._size_rows()

    def _size_rows(self):
        rows = self.verticalHeader()
        tall = self.list_mode or getattr(self, "_tall", False)
        height = K["h_row_tall"] if tall else K["h_list_row"] if self.tone == "list" else T["h_row"]
        rows.setMinimumSectionSize(min(rows.minimumSectionSize(), height))
        rows.setDefaultSectionSize(height)

    # ---- columns
    def set_column(self, col, *, kind="text", width=None, minimum=None, stretch=1, max_share=None,
                   priority=0, compact_hidden=False):
        if kind not in GRID_KINDS:
            raise ValueError(f"unknown column kind: {kind}")
        self._specs[col] = {"kind": kind, "width": width, "minimum": minimum, "stretch": stretch,
                            "max_share": max_share, "priority": priority, "compact_hidden": compact_hidden}

    def column_kind(self, col):
        spec = self._specs.get(col)
        return spec["kind"] if spec else "text"

    def _spec(self, col):
        return self._specs.get(col) or {"kind": "text", "width": None, "minimum": None, "stretch": 1,
                                         "max_share": None, "priority": 0, "compact_hidden": False}

    def fit_columns(self, available=None):
        n = self.columnCount()
        if not n:
            return []
        if available is None:
            available = self.viewport().width()
        header = self.horizontalHeader()
        order = [header.logicalIndex(v) for v in range(n)]
        user_hidden = {c for c in order if self.isColumnHidden(c) and c not in self._fit_hidden}
        widths, hidden = fit_column_widths(available, {c: self._spec(c) for c in order}, self._manual,
                                           user_hidden, self.compact)
        self._fitting = True
        try:
            for c in order:
                if c in user_hidden:
                    continue
                self.setColumnHidden(c, c in hidden)
                if c in widths:
                    self.setColumnWidth(c, max(widths[c], header.minimumSectionSize()))
        finally:
            self._fitting = False
        changed = hidden != self._fit_hidden
        self._fit_hidden = set(hidden)
        if changed:
            self.columnsFitted.emit(sorted(hidden))
        return sorted(hidden)

    def hidden_by_fit(self):
        return sorted(self._fit_hidden)

    def reset_manual_widths(self):
        self._manual.clear()
        self.fit_columns()

    def manual_widths(self):
        return dict(self._manual)

    def _on_section_resized(self, col, old, new):
        if not self._fitting and old > 0 and new > 0:
            self._manual[col] = new

    def set_compact(self, on):
        self.compact = bool(on)
        self.fit_columns()

    def apply_width_mode(self, mode):
        self.set_compact(str(getattr(mode, "value", mode)).lower() == "compact")

    # ---- hover (the delegate paints the row under the mouse and the button under it)
    def _set_hover(self, row, pos):
        old = self._hover_row
        self._hover_pos = pos
        if row != old:
            self._hover_row = row
            self.viewport().update()
        elif row >= 0:
            self.viewport().update(QRect(0, self.rowViewportPosition(row), self.viewport().width(),
                                         self.rowHeight(row)))

    def viewportEvent(self, event):
        kind = event.type()
        if kind == QEvent.MouseMove:
            pos = event.position().toPoint()
            self._set_hover(self.rowAt(pos.y()), pos)
        elif kind == QEvent.Leave:
            self._set_hover(-1, None)
        elif kind == QEvent.Resize and self._specs:
            result = super().viewportEvent(event)
            self.fit_columns()
            return result
        return super().viewportEvent(event)


class DarkListTable(DataGrid):
    """Dark list table for dialogs and settings (table.dt): muted caps header, hairline rows, the
    selected row dark-mint with the emerald edge; tags, pills and checkboxes drawn in cells."""

    def __init__(self, rows=0, columns=0, parent=None):
        super().__init__(rows, columns, parent, tone="list")
        self.setSelectionBehavior(QAbstractItemView.SelectRows)


# ---------------------------------------------------------------- GridFooter and GridFrame

@lru_cache(maxsize=None)
def _footer_qss(tone):
    pal = _GRID_TONES[tone]
    return (
        f"QFrame#KitGridFooter {{ background: {pal['foot']}; border: none;"
        f" border-top: 1px solid {pal['foot_line']}; }}"
        f"QWidget#KitGridHint {{ background: transparent; }}"
        f"QLabel#KitGridFootText {{ background: transparent; color: {pal['mute']};"
        f" font-size: {K['f_grid_foot']}px; }}"
    )


class GridFooter(QFrame):
    """The 30 px strip under a grid: a count on the left, keyboard hints on the right."""

    def __init__(self, parent=None, *, tone="white"):
        super().__init__(parent)
        self.setObjectName("KitGridFooter")
        self.setFixedHeight(K["h_grid_foot"])
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(K["pad_cell"], 0, K["pad_cell"], 0)
        self._lay.setSpacing(K["gap_strip"])
        self.count_label = QLabel("", self)
        self.count_label.setObjectName("KitGridFootText")
        self._lay.addWidget(self.count_label)
        self._lay.addStretch(1)
        self._hints = []
        self._keys = []
        self.compact = False
        self.tone = None
        self.set_tone(tone)

    def set_tone(self, tone):
        if tone not in _GRID_TONES:
            raise ValueError(f"unknown GridFooter tone: {tone}")
        self.tone = tone
        self.setStyleSheet(_footer_qss(tone))
        for key in self._keys:
            key.setStyleSheet(_key_qss(tone == "white"))

    def set_count(self, text):
        self.count_label.setText(text)

    def add_hint(self, keys, text="", *, compact_hide=False):
        """Key caps then a muted sentence ("Enter opens the top result"); compact_hide drops it in COMPACT."""
        holder = QWidget(self)
        holder.setObjectName("KitGridHint")
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(T["s_1"])
        for k in [keys] if isinstance(keys, str) else keys:
            key = Key(k, holder, light=self.tone == "white")
            self._keys.append(key)
            lay.addWidget(key)
        if text:
            label = QLabel(text, holder)
            label.setObjectName("KitGridFootText")
            lay.addWidget(label)
        self._lay.addWidget(holder)
        self._hints.append((holder, compact_hide))
        holder.setVisible(not (compact_hide and self.compact))
        return holder

    def add_widget(self, widget):
        self._lay.addWidget(widget)
        return widget

    def set_compact(self, on):
        self.compact = bool(on)
        for holder, compact_hide in self._hints:
            holder.setVisible(not (compact_hide and self.compact))

    def apply_width_mode(self, mode):
        self.set_compact(str(getattr(mode, "value", mode)).lower() == "compact")


class GridFrame(QFrame):
    """The rounded surface (.gridwrap) that holds a DataGrid and, under it, its GridFooter."""

    def __init__(self, grid, footer=None, parent=None):
        super().__init__(parent)
        self.setObjectName("KitGridFrame")
        self.grid = grid
        self.footer = footer
        lay = QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)
        lay.addWidget(grid, 1)
        if footer is not None:
            lay.addWidget(footer)
        self.set_tone(grid.tone)

    def set_tone(self, tone):
        pal = _GRID_TONES[tone]
        self.grid.set_tone(tone)
        if self.footer is not None:
            self.footer.set_tone(tone)
        bg = pal["bg"] if pal["bg"] != "transparent" else T["card"]
        self.setStyleSheet(f"QFrame#KitGridFrame {{ background: {bg}; border: 1px solid {pal['wrap']};"
                           f" border-radius: {T['r_l']}px; }}")

    def apply_width_mode(self, mode):
        self.grid.apply_width_mode(mode)
        if self.footer is not None:
            self.footer.apply_width_mode(mode)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), T["r_l"], T["r_l"])
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))


# ---------------------------------------------------------------- frames (K-6)

def _px_font(widget, px, weight=None):
    font = QFont(widget.font())
    font.setPixelSize(px)
    if weight is not None:
        font.setWeight(weight)
    return font


def _is_compact_mode(mode):
    return str(getattr(mode, "value", mode)).lower() == "compact"


# tone: (background, border, icon colour)
_ICON_TILE_TONES = {
    "emerald": (T["emdeep"], T["emline"], T["mint"]),
    "amber": (T["warndim"], T["warnline"], T["warn"]),
    "blue": (T["infodim"], T["infoline"], K["blue_tx"]),
}


class IconTile(QLabel):
    TONES = tuple(_ICON_TILE_TONES)

    def __init__(self, icon, tone="emerald", parent=None, *, large=False):
        super().__init__(parent)
        if tone not in _ICON_TILE_TONES:
            raise ValueError(f"unknown IconTile tone: {tone}")
        self.tone = tone
        bg, line, fg = _ICON_TILE_TONES[tone]
        side = K["d_dlg_ic_lg"] if large else K["d_dlg_ic"]
        self.setFixedSize(side, side)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(f"QLabel {{ background: {bg}; border: 1px solid {line};"
                           f" border-radius: {K['r_dlg_ic']}px; }}")
        ic = _icon(icon, fg)
        if ic is not None and not ic.isNull():
            self.setPixmap(ic.pixmap(side // 2, side // 2))


@lru_cache(maxsize=None)
def _dialog_qss(full):
    border = "none" if full else f"1px solid {K['dlg_line']}"
    radius = 0 if full else T["r_l"]
    return (
        f"QFrame#KitDialog {{ background: {T['bg']}; border: {border}; border-radius: {radius}px; }}"
        f"QFrame#KitDlgHead {{ background: transparent; border: none;"
        f" border-bottom: 1px solid {T['line']}; border-radius: 0; }}"
        f"QFrame#KitDlgFoot {{ background: {K['dlg_ft_bg']}; border: none;"
        f" border-top: 1px solid {T['line']}; border-radius: 0; }}"
        f"QWidget#KitDlgBody, QWidget#KitDlgText {{ background: transparent; }}"
        f"QScrollArea#KitDlgScroll {{ background: transparent; border: none; }}"
        f"QScrollArea#KitDlgScroll > QWidget > QWidget#KitDlgBody {{ background: transparent; }}"
        f"QLabel#KitDlgTitle {{ color: {T['text']}; background: transparent; border: none;"
        f" font-size: {T['f_xl']}px; font-weight: 700; }}"
        f"QLabel#KitDlgPurpose {{ color: {T['muted']}; background: transparent; border: none;"
        f" font-size: {K['f_dlg_purpose']}px; }}"
    )


class DialogFrame(QFrame):
    closeRequested = Signal()

    def __init__(self, title, purpose="", parent=None, *, icon="mdi.cog", tone="emerald",
                 full=False, scroll=False):
        super().__init__(parent)
        self.setObjectName("KitDialog")
        self._full = bool(full)
        self._primary = None
        self._footer_items = 0
        self._lead_stretch = None
        self.setSizePolicy(QSizePolicy.Expanding if full else QSizePolicy.Preferred, QSizePolicy.Expanding)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.header = QFrame(self)
        self.header.setObjectName("KitDlgHead")
        hl = QHBoxLayout(self.header)
        hl.setContentsMargins(K["pad_dlg_h"], K["pad_dlg_hd_v"], K["pad_dlg_h"], K["pad_dlg_hd_v"])
        hl.setSpacing(T["s_3"])
        self.icon_tile = IconTile(icon, tone, self.header)
        hl.addWidget(self.icon_tile)
        text = QWidget(self.header)
        text.setObjectName("KitDlgText")
        tl = QVBoxLayout(text)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(1)
        self.title_label = _ElideLabel(title, text)
        self.title_label.setObjectName("KitDlgTitle")
        tl.addWidget(self.title_label)
        self.purpose_label = _ElideLabel(purpose, text)
        self.purpose_label.setObjectName("KitDlgPurpose")
        self.purpose_label.setVisible(bool(purpose))
        tl.addWidget(self.purpose_label)
        hl.addWidget(text, 1)
        self._header_right = QHBoxLayout()
        self._header_right.setContentsMargins(0, 0, 0, 0)
        self._header_right.setSpacing(T["s_2"])
        hl.addLayout(self._header_right)
        outer.addWidget(self.header)

        self.body = QWidget()
        self.body.setObjectName("KitDlgBody")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(K["pad_dlg_h"], K["pad_dlg_bd_v"], K["pad_dlg_h"], K["pad_dlg_bd_v"])
        self.body_layout.setSpacing(T["s_3"])
        self.body_layout.setAlignment(Qt.AlignTop)
        self.scroll_area = None
        if scroll:
            self.scroll_area = QScrollArea(self)
            self.scroll_area.setObjectName("KitDlgScroll")
            self.scroll_area.setFrameShape(QFrame.NoFrame)
            self.scroll_area.setWidgetResizable(True)
            self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.scroll_area.setWidget(self.body)
            outer.addWidget(self.scroll_area, 1)
        else:
            self.body.setParent(self)
            outer.addWidget(self.body, 1)

        self.footer = QFrame(self)
        self.footer.setObjectName("KitDlgFoot")
        self._footer_layout = QHBoxLayout(self.footer)
        self._footer_layout.setContentsMargins(K["pad_dlg_h"], K["pad_dlg_ft_v"], K["pad_dlg_h"], K["pad_dlg_ft_v"])
        self._footer_layout.setSpacing(K["gap_dlg_ft"])
        self._footer_layout.addStretch(1)
        self.footer.hide()
        outer.addWidget(self.footer)

        self.set_full(full)

    def title(self):
        return self.title_label.text()

    def set_title(self, title):
        self.title_label.setText(title)

    def purpose(self):
        return self.purpose_label.text()

    def set_purpose(self, purpose):
        self.purpose_label.setText(purpose)
        self.purpose_label.setVisible(bool(purpose))

    def is_full(self):
        return self._full

    def set_full(self, on):
        self._full = bool(on)
        self.setStyleSheet(_dialog_qss(self._full))
        self.setSizePolicy(QSizePolicy.Expanding if self._full else QSizePolicy.Preferred, QSizePolicy.Expanding)
        self._update_mask()

    def add_widget(self, widget, stretch=0):
        self.body_layout.addWidget(widget, stretch)
        return widget

    def add_header_widget(self, widget):
        self._header_right.addWidget(widget)
        return widget

    def add_close_button(self, tooltip="Close (Esc)"):
        btn = IconButton("mdi.close", tooltip, self.header, variant="bordered")
        btn.clicked.connect(self.closeRequested)
        return self.add_header_widget(btn)

    def add_action(self, widget_or_text, *, variant="secondary", icon=None, tooltip=""):
        if isinstance(widget_or_text, str):
            widget = LabelledAction(widget_or_text, self.footer, variant=variant, icon=icon, tooltip=tooltip)
        else:
            widget = widget_or_text
        if isinstance(widget, LabelledAction) and widget.variant == "primary":
            if self._primary is not None:
                raise ValueError("a dialog footer has one primary action")
            self._primary = widget
        self._footer_layout.addWidget(widget)
        self._footer_items += 1
        self.footer.show()
        return widget

    def add_footer_spacer(self):
        """Left items go before this call; what is added after it sits at the right edge."""
        self._footer_layout.setStretch(0, 0)
        self._footer_layout.addStretch(1)

    def primary_action(self):
        return self._primary

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            event.accept()
            self.closeRequested.emit()
            return
        super().keyPressEvent(event)

    def _update_mask(self):
        if self._full or self.width() <= 0:
            self.clearMask()
            return
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), T["r_l"], T["r_l"])
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_mask()


class Scrim(QWidget):
    closeRequested = Signal()

    def __init__(self, parent, dialog=None, *, desk=False):
        super().__init__(parent)
        self.setObjectName("KitScrim")
        self.setStyleSheet("QWidget#KitScrim { background: transparent; }")
        self.setFocusPolicy(Qt.StrongFocus)
        self.desk = bool(desk)
        self._dialog = None
        self._lay = QVBoxLayout(self)
        self.hide()
        if parent is not None:
            parent.installEventFilter(self)
        if dialog is not None:
            self.set_dialog(dialog)

    def dialog(self):
        return self._dialog

    def set_dialog(self, dialog):
        if self._dialog is not None:
            self._lay.removeWidget(self._dialog)
            self._dialog.closeRequested.disconnect(self.closeRequested)
            self._dialog.setParent(None)
        self._dialog = dialog
        dialog.setParent(self)
        dialog.closeRequested.connect(self.closeRequested)
        if dialog.is_full():
            self._lay.setContentsMargins(0, 0, 0, 0)
            self._lay.addWidget(dialog)
        else:
            gap = K["gap_scrim"]
            self._lay.setContentsMargins(gap, gap, gap, gap)
            self._lay.addWidget(dialog, 0, Qt.AlignCenter)

    def cover_parent(self):
        if self.parentWidget() is not None:
            self.setGeometry(self.parentWidget().rect())

    def open(self):
        self.cover_parent()
        self.show()
        self.raise_()
        if self._dialog is not None:
            self._dialog.setFocus()

    def dismiss(self):
        self.hide()

    def eventFilter(self, obj, event):
        if obj is self.parentWidget() and event.type() == QEvent.Resize and self.isVisible():
            self.cover_parent()
        return super().eventFilter(obj, event)

    def paintEvent(self, event):
        p = QPainter(self)
        if self.desk:
            p.fillRect(self.rect(), QColor(K["scrim_desk"]))
        else:
            p.fillRect(self.rect(), QColor(0, 0, 0, K["scrim_alpha"]))
        p.end()

    def mousePressEvent(self, event):
        event.accept()
        if self._dialog is None or not self._dialog.geometry().contains(event.position().toPoint()):
            self.closeRequested.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            event.accept()
            self.closeRequested.emit()
            return
        super().keyPressEvent(event)


@lru_cache(maxsize=None)
def _page_hd_qss(compact):
    size = T["f_xl"] if compact else T["f_xxl"]
    return (
        "QFrame#KitPageHeader, QWidget#KitPageHeadText { background: transparent; border: none; }"
        f"QLabel#KitPageTitle {{ color: {T['text']}; background: transparent; border: none;"
        f" font-size: {size}px; font-weight: 700; }}"
        f"QLabel#KitPageCount {{ color: {T['muted']}; background: {T['card']};"
        f" border: 1px solid {T['line']}; border-radius: {K['r_count']}px;"
        f" padding: 1px {K['pad_count_h']}px; font-size: {T['f_s']}px; }}"
        f"QLabel#KitPageLead {{ color: {T['muted']}; background: transparent; border: none;"
        f" font-size: {T['f_s']}px; }}"
    )


class PageHeader(QFrame):
    def __init__(self, title="", parent=None, *, count="", lead=""):
        super().__init__(parent)
        self.setObjectName("KitPageHeader")
        self.setMinimumHeight(K["h_page_hd"])
        self._compact = False
        self._leading = 0
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(K["gap_page_hd"])
        text = QWidget(self)
        text.setObjectName("KitPageHeadText")
        col = QVBoxLayout(text)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(K["gap_page_hd"])
        self.title_label = _ElideLabel(title, text)
        self.title_label.setObjectName("KitPageTitle")
        row.addWidget(self.title_label)
        self.count_label = QLabel(str(count), text)
        self.count_label.setObjectName("KitPageCount")
        self.count_label.setVisible(bool(count))
        row.addWidget(self.count_label)
        row.addStretch(1)
        col.addLayout(row)
        self.lead_label = _ElideLabel(lead, text)
        self.lead_label.setObjectName("KitPageLead")
        self.lead_label.setVisible(bool(lead))
        col.addWidget(self.lead_label)
        self._lay.addWidget(text, 1)
        self.setStyleSheet(_page_hd_qss(False))

    def title(self):
        return self.title_label.text()

    def set_title(self, title):
        self.title_label.setText(title)

    def count(self):
        return self.count_label.text()

    def set_count(self, count):
        self.count_label.setText(str(count) if count not in (None, "") else "")
        self.count_label.setVisible(count not in (None, ""))

    def lead(self):
        return self.lead_label.text()

    def set_lead(self, lead):
        self.lead_label.setText(lead)
        self.lead_label.setVisible(bool(lead) and not self._compact)

    def add_leading(self, widget):
        self._lay.insertWidget(self._leading, widget)
        self._leading += 1
        return widget

    def add_action(self, widget):
        self._lay.addWidget(widget)
        return widget

    def is_compact(self):
        return self._compact

    def set_compact(self, on):
        self._compact = bool(on)
        self.setStyleSheet(_page_hd_qss(self._compact))
        self.lead_label.setVisible(bool(self.lead_label.text()) and not self._compact)

    def apply_width_mode(self, mode):
        self.set_compact(_is_compact_mode(mode))


_TILE_TONES = {
    None: T["text"],
    "emerald": T["mint"],
    "amber": T["warn"],
    "red": T["dangertx"],
    "blue": K["blue_tx"],
}


class Tile(_ReasonMixin, QAbstractButton):
    TONES = tuple(_TILE_TONES)

    def __init__(self, value="", label="", parent=None, *, tone=None, checked=False, tooltip=""):
        super().__init__(parent)
        if tone not in _TILE_TONES:
            raise ValueError(f"unknown Tile tone: {tone}")
        self._value = str(value)
        self._label = label
        self._tone = tone
        self._tip = tooltip
        self._compact = False
        self._hover = False
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._refresh()

    def value(self):
        return self._value

    def set_value(self, value):
        self._value = str(value)
        self._refresh()

    def label(self):
        return self._label

    def set_label(self, label):
        self._label = label
        self._refresh()

    def tone(self):
        return self._tone

    def set_tone(self, tone):
        if tone not in _TILE_TONES:
            raise ValueError(f"unknown Tile tone: {tone}")
        self._tone = tone
        self.update()

    def is_compact(self):
        return self._compact

    def set_compact(self, on):
        self._compact = bool(on)
        self._refresh()

    def apply_width_mode(self, mode):
        self.set_compact(_is_compact_mode(mode))

    def is_elided(self):
        return QFontMetrics(self._label_font()).horizontalAdvance(self._label) > self._text_width()

    def _value_font(self):
        return _px_font(self, K["f_tile_num_sm"] if self._compact else K["f_tile_num"], QFont.Bold)

    def _label_font(self):
        return _px_font(self, T["f_s"], QFont.Normal)

    def _pad(self):
        return ((K["pad_tile_h_sm"], K["pad_tile_v_sm"]) if self._compact
                else (K["pad_tile_h"], K["pad_tile_v"]))

    def _text_width(self):
        return max(0, self.width() - 2 * self._pad()[0])

    def _value_height(self):
        return round(QFontMetrics(self._value_font()).height() * 1.1)

    def _height(self):
        floor = K["h_tile_sm"] if self._compact else K["h_tile"]
        need = 2 * self._pad()[1] + self._value_height() + QFontMetrics(self._label_font()).height()
        return max(floor, need)

    def _refresh(self):
        self.setFixedHeight(self._height())
        self.setAccessibleName(f"{self._value} {self._label}".strip())
        self._sync_tip()
        self.updateGeometry()
        self.update()

    def _sync_tip(self):
        if self.disabled_reason:
            return
        self.setToolTip(self._tip or (self._label if self.is_elided() else ""))

    def sizeHint(self):
        w = max(QFontMetrics(self._value_font()).horizontalAdvance(self._value),
                QFontMetrics(self._label_font()).horizontalAdvance(self._label))
        return QSize(w + 2 * self._pad()[0], self._height())

    def minimumSizeHint(self):
        return QSize(2 * self._pad()[0] + 24, self._height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_tip()

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.click()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        alpha = 1.0 if self.isEnabled() else _DISABLED_ALPHA
        line = T["em"] if self.isChecked() else (T["line2"] if self._hover and self.isEnabled() else T["line"])
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        p.setPen(QPen(_qcolor(line, alpha), 1.0))
        p.setBrush(QBrush(_qcolor(T["card"], alpha)))
        p.drawRoundedRect(r, K["r_tile"], K["r_tile"])
        if self.hasFocus() and self.isEnabled():
            p.setPen(QPen(QColor(T["mint"]), 2.0))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), K["r_tile"] - 1, K["r_tile"] - 1)
        pad_h, pad_v = self._pad()
        width = self._text_width()
        vfont = self._value_font()
        p.setFont(vfont)
        p.setPen(_qcolor(_TILE_TONES[self._tone], alpha))
        vh = self._value_height()
        p.drawText(QRectF(pad_h, pad_v, width, vh), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(vfont).elidedText(self._value, Qt.ElideRight, width))
        lfont = self._label_font()
        p.setFont(lfont)
        p.setPen(_qcolor(T["muted"], alpha))
        p.drawText(QRectF(pad_h, pad_v + vh, width, QFontMetrics(lfont).height()),
                   Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(lfont).elidedText(self._label, Qt.ElideRight, width))
        p.end()


_LOG_TAGS = {"guard": "amber", "sync": "emerald", "err": "red", "peer": "blue", "net": "purple"}
_LOG_TONES = {
    "neutral": T["text2"],
    "amber": T["warn"],
    "emerald": T["mint"],
    "red": T["dangertx"],
    "blue": K["blue_tx"],
    "purple": T["purple"],
}


class LogView(QPlainTextEdit):
    TONES = tuple(_LOG_TONES)

    def __init__(self, parent=None, *, max_lines=500):
        super().__init__(parent)
        self.setObjectName("KitLog")
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.document().setMaximumBlockCount(max_lines)
        font = QFont(_MONO)
        font.setStyleHint(QFont.Monospace)
        font.setPixelSize(K["f_log"])
        self.setFont(font)
        self.setStyleSheet(
            f"QPlainTextEdit#KitLog {{ background: {K['log_bg']}; color: {T['text2']};"
            f" border: 1px solid {K['log_line']}; border-radius: {K['r_log']}px;"
            f" padding: {K['pad_log_v']}px {K['pad_log_h']}px; selection-background-color: {T['emdim']}; }}")
        self._started = False
        self._stick = False
        self.verticalScrollBar().rangeChanged.connect(self._follow_range)
        self.verticalScrollBar().valueChanged.connect(self._track_value)
        self._block = QTextBlockFormat()
        self._block.setLineHeight(165.0, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)

    def _fmt(self, colour, bold=False):
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(colour))
        fmt.setFontWeight(QFont.Bold if bold else QFont.Normal)
        return fmt

    def add_line(self, message, tag="", *, tone=None, ts=""):
        if tone is not None and tone not in _LOG_TONES:
            raise ValueError(f"unknown LogView tone: {tone}")
        bar = self.verticalScrollBar()
        self._stick = bar.value() >= bar.maximum() - 2
        cur = QTextCursor(self.document())
        cur.movePosition(QTextCursor.End)
        if self._started:
            cur.insertBlock(self._block)
        else:
            cur.setBlockFormat(self._block)
            self._started = True
        if ts:
            cur.insertText(ts + " ", self._fmt(K["log_ts"]))
        if tag:
            tone = tone or _LOG_TAGS.get(tag.lower(), "neutral")
            cur.insertText(tag.ljust(K["w_log_tag"]) + " ", self._fmt(_LOG_TONES[tone], bold=True))
        cur.insertText(message, self._fmt(T["text2"]))
        if self._stick:
            bar.setValue(bar.maximum())

    def _follow_range(self, _low, high):
        # the scroll range grows after the layout catches up with the new line, not inside add_line
        if self._stick:
            self.verticalScrollBar().setValue(high)

    def _track_value(self, value):
        self._stick = value >= self.verticalScrollBar().maximum() - 2

    def lines(self):
        return self.toPlainText().split("\n") if self._started else []

    def line_count(self):
        return len(self.lines())

    def clear_log(self):
        self.clear()
        self._started = False
