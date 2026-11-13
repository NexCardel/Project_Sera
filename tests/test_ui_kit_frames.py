"""K-6: kit frames (ui/components/kit.py) and the restyled SlidePanel — DialogFrame, Scrim, PageHeader,
Tile, LogView, IconTile."""

import os
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QButtonGroup, QLabel, QWidget

from ui.components import kit
from ui.shell import slide_panel as slide_panel_module
from ui.shell.slide_panel import SlidePanel
from ui.utils.responsive import WidthMode
from ui.utils.tokens import KIT, TOKENS


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _pixel(widget, x, y):
    return QColor(widget.grab().toImage().pixel(x, y))


# ---------------------------------------------------------------- IconTile

def test_icon_tile_sizes_and_tones(app):
    assert kit.IconTile("mdi.cog").size().width() == KIT["d_dlg_ic"]
    assert kit.IconTile("mdi.cog", large=True).size().width() == KIT["d_dlg_ic_lg"]
    for tone in kit.IconTile.TONES:
        tile = kit.IconTile("mdi.cog", tone)
        assert tile.pixmap() is not None and not tile.pixmap().isNull()
    with pytest.raises(ValueError):
        kit.IconTile("mdi.cog", "red")


# ---------------------------------------------------------------- DialogFrame

def test_dialog_frame_header_texts(app):
    d = kit.DialogFrame("Settings", "Project Sera · this PC")
    assert d.title() == "Settings"
    assert d.purpose() == "Project Sera · this PC"
    assert not d.purpose_label.isHidden()
    d.set_purpose("")
    assert d.purpose_label.isHidden()
    d.set_title("Audit log")
    assert d.title_label.text() == "Audit log"


def test_dialog_footer_hidden_until_first_action(app):
    d = kit.DialogFrame("Export", "One line")
    assert d.footer.isHidden()
    d.add_action("Cancel")
    assert not d.footer.isHidden()


def test_dialog_footer_takes_one_primary(app):
    d = kit.DialogFrame("Export")
    save = d.add_action("Save", variant="primary", icon="mdi.check")
    assert d.primary_action() is save
    d.add_action("Cancel")
    d.add_action("Delete", variant="danger")
    with pytest.raises(ValueError):
        d.add_action("Also save", variant="primary")
    with pytest.raises(ValueError):
        d.add_action(kit.LabelledAction("Other", variant="primary"))


def test_dialog_footer_right_aligned_and_spacer_splits_groups(app):
    d = kit.DialogFrame("Export")
    d.resize(600, 300)
    cancel = d.add_action("Cancel")
    save = d.add_action("Save", variant="primary")
    d.show()
    app.processEvents()
    assert save.geometry().right() >= d.footer.width() - KIT["pad_dlg_h"] - 2
    assert cancel.x() < save.x()

    e = kit.DialogFrame("Export")
    e.resize(600, 300)
    delete = e.add_action("Delete…", variant="danger")
    e.add_footer_spacer()
    ok = e.add_action("Save", variant="primary")
    e.show()
    app.processEvents()
    assert delete.x() <= KIT["pad_dlg_h"] + 2
    assert ok.geometry().right() >= e.footer.width() - KIT["pad_dlg_h"] - 2


def test_dialog_body_fills_between_header_and_footer(app):
    d = kit.DialogFrame("Export")
    label = d.add_widget(QLabel("body"), 1)
    d.add_action("Close")
    d.resize(500, 400)
    d.show()
    app.processEvents()
    assert d.body.y() >= d.header.geometry().bottom()
    assert d.body.geometry().bottom() <= d.footer.y()
    assert label.parent() is d.body


def test_dialog_scroll_variant_scrolls_a_tall_body(app):
    d = kit.DialogFrame("Long", scroll=True)
    for i in range(40):
        d.add_widget(QLabel(f"row {i}"))
    d.resize(400, 240)
    d.show()
    app.processEvents()
    assert d.scroll_area is not None
    assert d.scroll_area.verticalScrollBar().maximum() > 0
    assert d.scroll_area.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff


def test_dialog_escape_emits_close_even_from_a_child(app):
    d = kit.DialogFrame("Export")
    field = d.add_widget(kit.Input())
    hits = []
    d.closeRequested.connect(lambda: hits.append(1))
    d.show()
    QTest.keyClick(d, Qt.Key_Escape)
    QTest.keyClick(field, Qt.Key_Escape)
    assert hits == [1, 1]


def test_dialog_close_button_emits_close_and_has_a_tooltip(app):
    d = kit.DialogFrame("Export")
    btn = d.add_close_button()
    assert btn.toolTip() == "Close (Esc)"
    hits = []
    d.closeRequested.connect(lambda: hits.append(1))
    btn.click()
    assert hits == [1]


def test_dialog_full_variant_has_no_border_radius_or_mask(app):
    d = kit.DialogFrame("Settings", full=True)
    d.resize(500, 300)
    d.show()
    assert d.is_full()
    assert "border-radius: 0px" in d.styleSheet()
    assert d.mask().isEmpty()
    d.set_full(False)
    d.resize(501, 300)
    assert not d.is_full()
    assert f"border-radius: {TOKENS['r_l']}px" in d.styleSheet()
    assert not d.mask().isEmpty()


def test_dialog_corner_is_masked_but_header_edge_is_not(app):
    d = kit.DialogFrame("Export")
    d.resize(420, 300)
    d.show()
    app.processEvents()
    assert not d.mask().contains(QPoint(0, 0))
    assert d.mask().contains(QPoint(d.width() // 2, 0))


# ---------------------------------------------------------------- Scrim

def _host(width=800, height=600):
    host = QWidget()
    host.setStyleSheet(f"background: {TOKENS['bg']};")
    host.resize(width, height)
    return host


def test_scrim_centres_a_dialog_and_covers_the_parent(app):
    host = _host()
    dlg = kit.DialogFrame("Export", "One line")
    dlg.add_widget(QLabel("body"))
    dlg.setFixedSize(400, 300)
    scrim = kit.Scrim(host, dlg)
    host.show()
    assert not scrim.isVisible()
    scrim.open()
    app.processEvents()
    assert scrim.geometry() == host.rect()
    assert dlg.geometry().center().x() == pytest.approx(host.width() // 2, abs=2)
    assert dlg.geometry().center().y() == pytest.approx(host.height() // 2, abs=2)
    host.resize(900, 700)
    app.processEvents()
    assert scrim.geometry() == host.rect()


def test_scrim_dims_what_is_under_it(app):
    host = _host(400, 300)
    dlg = kit.DialogFrame("Export")
    dlg.setFixedSize(200, 150)
    scrim = kit.Scrim(host, dlg)
    host.show()
    before = _pixel(host, 5, 5)
    scrim.open()
    app.processEvents()
    after = _pixel(host, 5, 5)
    assert after.red() < before.red() or after.green() < before.green()
    assert after.lightness() < before.lightness()
    scrim.dismiss()
    app.processEvents()
    assert _pixel(host, 5, 5) == before


def test_scrim_desk_is_opaque(app):
    host = _host(300, 200)
    dlg = kit.DialogFrame("Welcome")
    dlg.setFixedSize(160, 100)
    scrim = kit.Scrim(host, dlg, desk=True)
    host.show()
    scrim.open()
    app.processEvents()
    assert _pixel(host, 3, 3).name().upper() == KIT["scrim_desk"]


def test_scrim_click_outside_closes_click_inside_does_not(app):
    host = _host(500, 400)
    dlg = kit.DialogFrame("Export")
    dlg.setFixedSize(200, 150)
    scrim = kit.Scrim(host, dlg)
    host.show()
    scrim.open()
    app.processEvents()
    hits = []
    scrim.closeRequested.connect(lambda: hits.append(1))
    QTest.mouseClick(scrim, Qt.LeftButton, Qt.NoModifier, dlg.geometry().center())
    assert hits == []
    QTest.mouseClick(scrim, Qt.LeftButton, Qt.NoModifier, QPoint(5, 5))
    assert hits == [1]


def test_scrim_forwards_the_dialogs_escape(app):
    host = _host()
    dlg = kit.DialogFrame("Export")
    scrim = kit.Scrim(host, dlg)
    hits = []
    scrim.closeRequested.connect(lambda: hits.append(1))
    host.show()
    scrim.open()
    QTest.keyClick(dlg, Qt.Key_Escape)
    assert hits == [1]


def test_scrim_full_dialog_fills_it(app):
    host = _host(640, 480)
    dlg = kit.DialogFrame("Settings", full=True)
    scrim = kit.Scrim(host, dlg)
    host.show()
    scrim.open()
    app.processEvents()
    assert dlg.geometry() == scrim.rect()


# ---------------------------------------------------------------- PageHeader

def test_page_header_parts(app):
    h = kit.PageHeader("All Clients", count="16 clients", lead="Every client on this PC")
    assert (h.title(), h.count(), h.lead()) == ("All Clients", "16 clients", "Every client on this PC")
    assert not h.count_label.isHidden() and not h.lead_label.isHidden()
    h.set_count("")
    h.set_lead("")
    assert h.count_label.isHidden() and h.lead_label.isHidden()
    h.set_count(3)
    assert h.count() == "3"


def test_page_header_leading_and_actions_flank_the_title(app):
    h = kit.PageHeader("Manage Clients", count="16 clients")
    back = h.add_leading(kit.IconButton("mdi.arrow-left", "Back to Search (Esc)", variant="bordered"))
    new = h.add_action(kit.LabelledAction("New client", variant="primary", icon="mdi.plus"))
    h.resize(900, 40)
    h.show()
    app.processEvents()
    def left(w):
        return w.mapTo(h, w.rect().topLeft()).x()

    assert left(back) < left(h.title_label) < left(h.count_label) < left(new)
    assert new.geometry().right() >= h.width() - 2
    assert h.height() >= KIT["h_page_hd"]


def test_page_header_compact_hides_lead_and_shrinks_title(app):
    h = kit.PageHeader("Tracker Dump", lead="Filings captured by the browser extension")
    h.resize(900, 50)
    h.show()
    app.processEvents()
    assert not h.lead_label.isHidden()
    assert f"font-size: {TOKENS['f_xxl']}px" in h.styleSheet()
    h.apply_width_mode(WidthMode.COMPACT)
    assert h.is_compact() and h.lead_label.isHidden()
    assert f"font-size: {TOKENS['f_xl']}px" in h.styleSheet()
    h.apply_width_mode("wide")
    assert not h.is_compact() and not h.lead_label.isHidden()


def test_page_header_long_title_elides_with_a_tooltip(app):
    h = kit.PageHeader("A very long page title that cannot possibly fit in this narrow space at all")
    h.resize(220, 40)
    h.show()
    app.processEvents()
    assert h.title_label.is_elided()
    assert h.title_label.toolTip() == h.title()


# ---------------------------------------------------------------- Tile

def test_tile_sizes_follow_the_mode(app):
    t = kit.Tile("142", "Captured")
    assert t.height() >= KIT["h_tile"]
    big = t.height()
    t.apply_width_mode(WidthMode.COMPACT)
    assert t.is_compact()
    assert KIT["h_tile_sm"] <= t.height() < big
    t.set_compact(False)
    assert t.height() == big


def test_tile_value_label_and_tone(app):
    t = kit.Tile(7, "Failed", tone="red")
    assert (t.value(), t.label(), t.tone()) == ("7", "Failed", "red")
    assert t.accessibleName() == "7 Failed"
    t.set_value(8)
    t.set_label("Locked")
    t.set_tone(None)
    assert t.accessibleName() == "8 Locked"
    with pytest.raises(ValueError):
        kit.Tile("1", "x", tone="pink")
    with pytest.raises(ValueError):
        t.set_tone("pink")


def test_tile_click_toggles_and_emits(app):
    t = kit.Tile("5", "Today")
    t.resize(120, t.height())
    t.show()
    got, toggled = [], []
    t.clicked.connect(lambda: got.append(1))
    t.toggled.connect(toggled.append)
    QTest.mouseClick(t, Qt.LeftButton)
    assert t.isChecked() and got == [1] and toggled == [True]


@pytest.mark.parametrize("key", [Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter])
def test_tile_keyboard_activates(app, key):
    t = kit.Tile("5", "Today")
    t.resize(120, t.height())
    t.show()
    t.setFocus()
    got = []
    t.clicked.connect(lambda: got.append(1))
    QTest.keyClick(t, key)
    assert got == [1]
    assert t.focusPolicy() == Qt.StrongFocus


def test_tiles_in_an_exclusive_group_are_one_of_n(app):
    a, b = kit.Tile("1", "A"), kit.Tile("2", "B")
    group = QButtonGroup()
    group.setExclusive(True)
    group.addButton(a)
    group.addButton(b)
    a.click()
    b.click()
    assert b.isChecked() and not a.isChecked()


def test_tile_checked_edge_is_emerald(app):
    t = kit.Tile("12", "Worked")
    t.resize(140, t.height())
    t.setFocusPolicy(Qt.NoFocus)
    t.show()
    app.processEvents()
    off = _pixel(t, t.width() // 2, 0)
    t.setChecked(True)
    app.processEvents()
    on = _pixel(t, t.width() // 2, 0)
    assert on.name().upper() == TOKENS["em"]
    assert off.name().upper() in (TOKENS["line"], TOKENS["line2"])


def test_tile_long_label_elides_and_gets_a_tooltip(app):
    t = kit.Tile("4", "Portal wording not recognised by the reader")
    t.resize(110, t.height())
    t.show()
    app.processEvents()
    assert t.is_elided()
    assert t.toolTip() == "Portal wording not recognised by the reader"
    t.resize(400, t.height())
    app.processEvents()
    assert not t.is_elided() and t.toolTip() == ""
    named = kit.Tile("4", "x", tooltip="portal wording not recognised")
    assert named.toolTip() == "portal wording not recognised"


def test_tile_disabled_with_reason_keeps_the_reason(app):
    t = kit.Tile("0", "Parked", tooltip="Parked changes")
    t.set_disabled_reason("Nothing parked")
    assert not t.isEnabled() and t.toolTip() == "Nothing parked"
    t.resize(120, t.height())
    t.show()
    app.processEvents()
    assert t.toolTip() == "Nothing parked"
    t.set_disabled_reason(None)
    assert t.isEnabled() and t.toolTip() == "Parked changes"


# ---------------------------------------------------------------- LogView

def _formats(view, line=0):
    block = view.document().findBlockByNumber(line)
    text = block.text()
    return [(text[f.start:f.start + f.length], f.format.foreground().color().name().upper(), f.format.fontWeight())
            for f in block.textFormats()]


def test_log_view_is_read_only_monospace_no_wrap(app):
    v = kit.LogView()
    assert v.isReadOnly()
    assert v.lineWrapMode() == kit.QPlainTextEdit.NoWrap
    assert v.font().pixelSize() == KIT["f_log"]
    assert v.font().family().lower().startswith("consolas") or v.font().styleHint() == v.font().StyleHint.Monospace


def test_log_lines_keep_order_and_text(app):
    v = kit.LogView()
    assert v.lines() == [] and v.line_count() == 0
    v.add_line("Pushed 3 changes", "sync", ts="10:41:02")
    v.add_line("Host unreachable", "err", ts="10:41:09")
    v.add_line("plain line")
    assert v.line_count() == 3
    lines = v.lines()
    assert lines[0].startswith("10:41:02 sync")
    assert lines[0].endswith("Pushed 3 changes")
    assert lines[1].endswith("Host unreachable")
    assert lines[2] == "plain line"


@pytest.mark.parametrize("tag,colour", [
    ("guard", TOKENS["warn"]), ("sync", TOKENS["mint"]), ("err", TOKENS["dangertx"]),
    ("peer", KIT["blue_tx"]), ("net", TOKENS["purple"]), ("other", TOKENS["text2"]),
])
def test_log_tag_colours(app, tag, colour):
    v = kit.LogView()
    v.add_line("message", tag, ts="09:00:00")
    parts = _formats(v)
    ts_part = next(p for p in parts if p[0].startswith("09:00:00"))
    tag_part = next(p for p in parts if p[0].startswith(tag))
    assert ts_part[1] == KIT["log_ts"]
    assert tag_part[1] == colour.upper()
    assert tag_part[2] == 700


def test_log_tone_overrides_tag_and_rejects_unknown(app):
    v = kit.LogView()
    v.add_line("m", "sync", tone="red")
    assert _formats(v)[0][1] == TOKENS["dangertx"]
    with pytest.raises(ValueError):
        v.add_line("m", "sync", tone="pink")


def test_log_tag_column_is_aligned(app):
    v = kit.LogView()
    v.add_line("a", "err")
    v.add_line("b", "guard")
    first, second = v.lines()
    assert first.index("a") == second.index("b")


def test_log_keeps_only_the_newest_lines(app):
    v = kit.LogView(max_lines=5)
    for i in range(12):
        v.add_line(f"entry {i}", "sync")
    assert v.line_count() == 5
    assert v.lines()[0].endswith("entry 7") and v.lines()[-1].endswith("entry 11")


def test_log_sticks_to_the_bottom_unless_scrolled_up(app):
    v = kit.LogView()
    v.resize(300, 120)
    v.show()
    for i in range(60):
        v.add_line(f"entry {i}", "sync")
    app.processEvents()
    bar = v.verticalScrollBar()
    assert bar.maximum() > 0 and bar.value() == bar.maximum()
    bar.setValue(0)
    v.add_line("late entry", "sync")
    app.processEvents()
    assert bar.value() == 0


def test_log_clear_starts_over(app):
    v = kit.LogView()
    v.add_line("one")
    v.clear_log()
    assert v.lines() == []
    v.add_line("two")
    assert v.lines() == ["two"]


# ---------------------------------------------------------------- SlidePanel

def _panel_host(width):
    host = QWidget()
    host.resize(width, 700)
    panel = SlidePanel(host)
    panel.set_widget(QLabel("client"), "Client")
    return host, panel


def _open(app, panel):
    panel.slide_in()
    QTest.qWait(320)
    app.processEvents()


def test_slide_panel_is_540_wide_in_wide_mode(app):
    assert SlidePanel().target_width == KIT["w_panel"] == 540
    host, panel = _panel_host(1400)
    host.show()
    _open(app, panel)
    assert panel.width() == 540
    assert panel.geometry().right() + 1 == host.width()
    assert panel.height() == host.height()


def test_slide_panel_takes_the_whole_width_in_compact(app):
    host, panel = _panel_host(900)
    host.show()
    _open(app, panel)
    assert panel.is_compact()
    assert panel.width() == host.width() and panel.x() == 0
    assert "border-left: none" in panel.styleSheet()


def test_slide_panel_follows_the_parent_width_across_the_thresholds(app):
    host, panel = _panel_host(1400)
    host.show()
    _open(app, panel)
    host.resize(1000, 700)
    panel.update_position()
    assert panel.is_compact() and panel.width() == 1000
    host.resize(1100, 700)          # inside the hysteresis gap: stays COMPACT
    panel.update_position()
    assert panel.is_compact()
    host.resize(1200, 700)
    panel.update_position()
    assert not panel.is_compact() and panel.width() == 540


def test_slide_panel_explicit_mode_wins_over_the_parents_width(app):
    host, panel = _panel_host(1400)
    host.show()
    _open(app, panel)
    panel.apply_width_mode(WidthMode.COMPACT)
    assert panel.width() == host.width()
    host.resize(1500, 700)
    panel.update_position()
    assert panel.width() == host.width()
    panel.apply_width_mode("wide")
    assert panel.width() == 540
    assert f"border-left: 1px solid {TOKENS['line2']}" in panel.styleSheet()


def test_slide_panel_never_wider_than_its_parent(app):
    host, panel = _panel_host(400)
    panel.set_compact(False)
    assert panel._calc_panel_width(400) == 400


def test_slide_panel_header_and_buttons(app):
    host, panel = _panel_host(1400)
    assert panel.title_label.text() == "Client"
    assert panel.btn_back.toolTip() == "Back" and panel.btn_close.toolTip() == "Close"
    panel.set_widget(QLabel("own header"))
    assert panel.header.isHidden()


def test_slide_panel_opens_and_closes_with_signals(app):
    host, panel = _panel_host(1400)
    host.show()
    events = []
    panel.opened.connect(lambda: events.append("opened"))
    panel.closed.connect(lambda: events.append("closed"))
    _open(app, panel)
    assert panel.is_open and panel.isVisible()
    panel.btn_close.click()
    QTest.qWait(320)
    assert not panel.is_open and not panel.isVisible()
    assert events == ["opened", "closed"]


def test_slide_panel_file_has_no_colour_literals():
    src = Path(slide_panel_module.__file__).read_text(encoding="utf-8")
    assert not re.findall(r"#[0-9A-Fa-f]{3,8}\b", src)
    assert "qtawesome" not in src
