"""K-1: the kit primitives (ui/components/kit.py) — variants, sizes, states, signals, keys, elision."""

import os
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ui.components import kit
from ui.utils.tokens import KIT, TOKENS

KIT_FILE = Path(kit.__file__)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_kit_has_no_colour_literals():
    assert not re.findall(r"#[0-9A-Fa-f]{3,8}\b", KIT_FILE.read_text(encoding="utf-8"))


def test_kit_tokens_are_hex_or_px():
    for name, value in KIT.items():
        assert isinstance(value, int) or re.fullmatch(r"#[0-9A-F]{6}", value), name


def test_kit_import_does_not_load_qtawesome():
    import subprocess, sys
    code = "import sys; import ui.components.kit; print('qtawesome' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=str(KIT_FILE.parents[2]), env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
    assert out.stdout.strip() == "False", out.stderr


# ---------------------------------------------------------------- LabelledAction

@pytest.mark.parametrize("variant", ["primary", "soft", "secondary", "ghost", "danger"])
def test_labelled_action_variants_and_sizes(app, variant):
    b = kit.LabelledAction("Copy", variant=variant, icon="mdi.content-copy")
    assert b.variant == variant
    assert b.height() == TOKENS["h_ctl"]
    assert not b.icon().isNull()
    assert kit.LabelledAction("Copy", variant=variant, sm=True).height() == KIT["h_sm"]


def test_labelled_action_rejects_unknown_variant(app):
    with pytest.raises(ValueError):
        kit.LabelledAction("x", variant="loud")
    b = kit.LabelledAction("x")
    with pytest.raises(ValueError):
        b.set_variant("loud")


def test_danger_is_outlined_red_and_primary_is_emerald(app):
    assert TOKENS["dangertx"] in kit.LabelledAction("Delete", variant="danger").styleSheet()
    assert TOKENS["em"] in kit.LabelledAction("Save", variant="primary").styleSheet()


def test_disabled_with_reason_restores_tooltip(app):
    b = kit.LabelledAction("Delete", variant="danger", tooltip="Delete the selected clients")
    b.set_disabled_reason("Select a client first")
    assert not b.isEnabled()
    assert b.toolTip() == "Select a client first"
    b.set_disabled_reason("Select one client only")
    assert b.toolTip() == "Select one client only"
    b.set_disabled_reason(None)
    assert b.isEnabled()
    assert b.toolTip() == "Delete the selected clients"


def test_icon_only_moves_text_to_tooltip(app):
    b = kit.LabelledAction("Copy PAN", icon="mdi.content-copy")
    b.set_icon_only(True)
    assert b.text() == "" and b.toolTip() == "Copy PAN" and b.is_icon_only()
    assert b.width() == b.height()
    b.set_icon_only(False)
    assert b.text() == "Copy PAN" and b.toolTip() == ""


def test_swatch_widens_the_icon(app):
    b = kit.LabelledAction("Fill", icon="mdi.format-color-fill")
    plain = b.iconSize().width()
    b.set_swatch(TOKENS["warn"])
    assert b.iconSize().width() > plain
    b.set_swatch(None)
    assert b.iconSize().width() == plain


def test_labelled_action_space_clicks(app):
    b = kit.LabelledAction("Open", variant="primary")
    hits = []
    b.clicked.connect(lambda: hits.append(1))
    QTest.keyClick(b, Qt.Key_Space)
    assert hits == [1]


def test_disabled_action_does_not_click(app):
    b = kit.LabelledAction("Delete", variant="danger")
    hits = []
    b.clicked.connect(lambda: hits.append(1))
    b.set_disabled_reason("Nothing selected")
    QTest.mouseClick(b, Qt.LeftButton)
    assert hits == []


# ---------------------------------------------------------------- IconButton

def test_icon_button_needs_a_tooltip(app):
    with pytest.raises(ValueError):
        kit.IconButton("mdi.refresh", "")


@pytest.mark.parametrize("variant", ["plain", "bordered", "primary"])
def test_icon_button_variants(app, variant):
    b = kit.IconButton("mdi.refresh", "Refresh", variant=variant)
    assert b.size().width() == b.size().height() == TOKENS["h_ctl"]
    assert b.toolTip() == b.accessibleName() == "Refresh"
    assert not b.icon().isNull()
    assert kit.IconButton("mdi.refresh", "Refresh", variant=variant, sm=True).width() == KIT["h_ibtn_sm"]


def test_icon_button_disabled_reason_and_click(app):
    b = kit.IconButton("mdi.undo", "Undo", variant="bordered")
    hits = []
    b.clicked.connect(lambda: hits.append(1))
    QTest.mouseClick(b, Qt.LeftButton)
    b.set_disabled_reason("Nothing to undo")
    QTest.mouseClick(b, Qt.LeftButton)
    assert hits == [1]
    assert b.toolTip() == "Nothing to undo"
    b.set_disabled_reason(None)
    assert b.toolTip() == "Undo"


# ---------------------------------------------------------------- Segmented

def test_segmented_click_and_arrows(app):
    s = kit.Segmented(["All", "Pending", "Done"])
    seen = []
    s.currentChanged.connect(seen.append)
    assert s.current_index() == 0 and s.buttons[0].isChecked()
    QTest.mouseClick(s.buttons[2], Qt.LeftButton)
    assert s.current_index() == 2 and s.buttons[2].isChecked()
    QTest.keyClick(s, Qt.Key_Left)
    QTest.keyClick(s, Qt.Key_Left)
    QTest.keyClick(s, Qt.Key_Left)          # clamps at the first option
    assert s.current_index() == 0
    assert seen == [2, 1, 0]
    s.set_current(0)                         # unchanged: no signal
    assert seen == [2, 1, 0]


def test_segmented_is_one_focus_stop(app):
    s = kit.Segmented(["Cards", "List"], current=1)
    assert s.focusPolicy() == Qt.StrongFocus
    assert all(b.focusPolicy() == Qt.NoFocus for b in s.buttons)
    assert s.current_index() == 1
    assert s.height() == TOKENS["h_ctl"]


# ---------------------------------------------------------------- Key, StatusDot

def test_key_dark_and_light(app):
    dark, light = kit.Key("Ctrl+B"), kit.Key("Enter", light=True)
    assert dark.text() == "Ctrl+B" and dark.height() == KIT["h_key"]
    assert KIT["key_bg"] in dark.styleSheet() and KIT["key_light_bg"] in light.styleSheet()


def test_status_dot_tones(app):
    for tone in ("ok", "warn", "bad", "info", "off"):
        d = kit.StatusDot(tone)
        assert d.tone == tone
        assert not d.grab().isNull()
    with pytest.raises(ValueError):
        kit.StatusDot("green")


# ---------------------------------------------------------------- Pill and Tag

def test_pill_has_seven_tones(app):
    assert kit.Pill.TONES == ("neutral", "emerald", "amber", "red", "blue", "purple", "orange")
    for tone in kit.Pill.TONES:
        p = kit.Pill("Status", tone)
        assert p.tone == tone and p.height() == KIT["h_pill"]
    with pytest.raises(ValueError):
        kit.Pill("x", "pink")


def test_pill_dot_follows_tone(app):
    p = kit.Pill("Online", "emerald", dot=True)
    assert p.dot.tone == "ok"
    p.set_tone("red")
    assert p.dot.tone == "bad"
    assert kit.Pill("Plain", "amber").dot is None


def test_pill_elides_with_tooltip(app):
    p = kit.Pill("A long status that cannot fit in the space given", "amber")
    p.show()
    assert p.toolTip() == ""
    p.setFixedWidth(80)
    app.processEvents()
    assert p.full_text == "A long status that cannot fit in the space given"
    assert p.toolTip() == p.full_text
    p.setFixedWidth(600)
    app.processEvents()
    assert p.toolTip() == ""
    p.close()


def test_tag_tones_mono_and_elision(app):
    assert kit.Tag.TONES == ("neutral", "emerald", "amber", "red", "blue")
    t = kit.Tag("password [Secret]", "red")
    assert TOKENS["mono"].split(",")[0].strip("'") in t.styleSheet()
    assert t.height() == KIT["h_tag"]
    t.show()
    t.setFixedWidth(40)
    app.processEvents()
    assert t.toolTip() == t.full_text == "password [Secret]"
    t.close()
    with pytest.raises(ValueError):
        kit.Tag("x", "purple")


def test_tag_draws_its_background(app):
    t = kit.Tag("TEXT", "emerald")
    t.resize(t.sizeHint())
    img = t.grab().toImage()
    corner_in = img.pixelColor(img.width() // 2, 2)
    assert corner_in.name().upper() in (TOKENS["emdeep"], TOKENS["emline"])


# ---------------------------------------------------------------- inputs (K-2)

def _press(w, key):
    QTest.keyClick(w, key)


def test_field_label_uppercase_required_and_buddy(app):
    f = kit.Input()
    lab = kit.FieldLabel("Name of company", required=True, for_widget=f)
    assert lab.label_text == "Name of company"
    assert lab.font().capitalization().name == "AllUppercase"
    assert "*" in lab.text() and TOKENS["dangertx"] in lab.text()
    assert lab.buddy() is f
    assert kit.FieldLabel("PAN").text() == "PAN"


def test_input_height_icon_and_placeholder(app):
    i = kit.Input(placeholder="Search", icon="mdi.magnify")
    assert i.height() == TOKENS["h_inp"]
    assert i.has_icon() and i.textMargins().left() == KIT["pad_inp_icon"] - KIT["pad_inp"]
    assert i.placeholderText() == "Search"
    assert not kit.Input().has_icon()
    assert kit.Input().textMargins().left() == 0
    i.setText("abc")
    assert i.text() == "abc"


def test_input_trailing_slot_clears_text_and_clicks(app):
    i = kit.Input()
    i.setEchoMode(kit.Input.EchoMode.Password)
    eye = i.add_trailing(kit.IconButton("mdi.eye", "Show / hide", sm=True))
    copy = i.add_trailing(kit.IconButton("mdi.content-copy", "Copy", sm=True))
    i.resize(240, TOKENS["h_inp"])
    i.show()
    app.processEvents()
    assert i.textMargins().right() >= eye.width() + copy.width() + 2 + 6 - KIT["pad_inp"]
    assert eye.parentWidget().geometry().right() <= i.width() - 6
    hits = []
    eye.clicked.connect(lambda: hits.append(1))
    QTest.mouseClick(eye, Qt.LeftButton)
    assert hits == [1]
    i.close()


def test_input_disabled_dims_icon(app):
    i = kit.Input(icon="mdi.magnify")
    on = i._icon_label.pixmap().toImage()
    i.setEnabled(False)
    assert i._icon_label.pixmap().toImage() != on


def test_select_items_signal_and_wheel(app):
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QWheelEvent
    s = kit.Select(items=["Light", "Dark", "Auto"])
    assert s.height() == TOKENS["h_inp"] and s.count() == 3
    seen = []
    s.currentIndexChanged.connect(seen.append)
    s.setCurrentIndex(2)
    assert seen == [2]
    s.show()
    s.setCurrentIndex(0)
    seen.clear()
    ev = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120), Qt.NoButton,
                     Qt.NoModifier, Qt.NoScrollPhase, False)
    s.clearFocus()
    QApplication.sendEvent(s, ev)
    assert seen == []
    s.close()


def test_select_disabled_reason(app):
    s = kit.Select(items=["A"])
    s.setToolTip("Theme")
    s.set_disabled_reason("Admin only")
    assert not s.isEnabled() and s.toolTip() == "Admin only"
    s.set_disabled_reason(None)
    assert s.isEnabled() and s.toolTip() == "Theme"


def test_text_area_rows_and_tab_leaves(app):
    a = kit.TextArea(rows=3, placeholder="Notes")
    b = kit.TextArea(rows=6)
    assert b.minimumHeight() > a.minimumHeight() > 40
    assert a.placeholderText() == "Notes" and a.tabChangesFocus()
    a.setPlainText("x")
    assert a.toPlainText() == "x"


def test_stepper_buttons_keys_limits_and_signal(app):
    s = kit.Stepper(value=30, minimum=10, maximum=33, step=2, unit=" s")
    assert s.text() == "30 s" and s.height() == TOKENS["h_inp"]
    seen = []
    s.valueChanged.connect(seen.append)
    s.show()
    QTest.mouseClick(s.plus, Qt.LeftButton)
    assert s.value() == 32 and seen == [32]
    _press(s, Qt.Key_Up)
    assert s.value() == 33 and not s.plus.isEnabled()
    _press(s, Qt.Key_Right)
    assert s.value() == 33 and seen == [32, 33]
    _press(s, Qt.Key_Home)
    assert s.value() == 10 and not s.minus.isEnabled()
    _press(s, Qt.Key_Down)
    assert s.value() == 10
    _press(s, Qt.Key_End)
    assert s.value() == 33
    s.setValue(500)
    assert s.value() == 33 and s._label.text() == "33 s"
    assert s.minus.focusPolicy() == Qt.NoFocus and s.focusPolicy() == Qt.StrongFocus
    s.close()


def test_stepper_disabled_reason_disables_buttons(app):
    s = kit.Stepper(value=5)
    s.set_disabled_reason("Locked by admin")
    assert not s.plus.isEnabled() and not s.minus.isEnabled() and s.toolTip() == "Locked by admin"
    s.set_disabled_reason(None)
    assert s.plus.isEnabled()


def test_checkbox_toggle_space_and_signal(app):
    c = kit.Checkbox("Select row")
    seen = []
    c.toggled.connect(seen.append)
    c.show()
    c.setFocus()
    _press(c, Qt.Key_Space)
    assert c.isChecked() and seen == [True]
    QTest.mouseClick(c, Qt.LeftButton)
    assert not c.isChecked() and seen == [True, False]
    c.close()


def test_checkbox_draws_box_and_tick(app):
    c = kit.Checkbox("x", checked=True)
    c.resize(c.sizeHint())
    y = c.height() // 2 + 4
    assert c.grab().toImage().pixelColor(KIT["d_cb"] // 2, y).name().upper() == TOKENS["em"]
    c.setChecked(False)
    assert c.grab().toImage().pixelColor(KIT["d_cb"] // 2, y).name().upper() == KIT["btn_bg"]


def test_checkbox_elides_with_tooltip_and_reason(app):
    c = kit.Checkbox("A long list entry that does not fit")
    c.show()
    c.setFixedWidth(60)
    app.processEvents()
    assert c.is_elided() and c.toolTip() == "A long list entry that does not fit"
    c.setFixedWidth(400)
    app.processEvents()
    assert not c.is_elided() and c.toolTip() == ""
    c.set_disabled_reason("Archived")
    assert not c.isEnabled() and c.toolTip() == "Archived"
    c.set_disabled_reason(None)
    assert c.isEnabled() and c.toolTip() == ""
    c.close()


def test_switch_size_keys_signal_and_paint(app):
    sw = kit.Switch(tooltip="Admin mode")
    assert (sw.width(), sw.height()) == (KIT["w_sw"], KIT["h_sw"])
    assert sw.accessibleName() == "Admin mode" and sw.toolTip() == "Admin mode"
    seen = []
    sw.toggled.connect(seen.append)
    sw.show()
    sw.setFocus()
    _press(sw, Qt.Key_Space)
    assert sw.isChecked() and seen == [True]
    _press(sw, Qt.Key_Return)
    assert not sw.isChecked() and seen == [True, False]
    QTest.mouseClick(sw, Qt.LeftButton)
    assert sw.isChecked()
    img = sw.grab().toImage()
    assert img.pixelColor(img.width() // 2, 4).name().upper() == TOKENS["em"]
    sw.setChecked(False)
    img = sw.grab().toImage()
    assert img.pixelColor(img.width() // 2, 4).name().upper() == KIT["sw_off"]
    sw.close()


def test_switch_disabled_reason_blocks_toggle(app):
    sw = kit.Switch()
    sw.set_disabled_reason("Needs admin")
    sw.show()
    QTest.mouseClick(sw, Qt.LeftButton)
    _press(sw, Qt.Key_Space)
    assert not sw.isChecked() and sw.toolTip() == "Needs admin"
    sw.set_disabled_reason(None)
    assert sw.isEnabled()
    sw.close()


# ---------------------------------------------------------------- containers (K-3)

def test_card_title_is_caps_with_right_slot_and_edges(app):
    from PySide6.QtWidgets import QLabel
    c = kit.Card("Display")
    pill = kit.Pill("Saved", "emerald")
    c.add_action(pill)
    c.add_widget(QLabel("body"))
    assert c.title() == "Display"
    assert c.title_label.font().capitalization().name == "AllUppercase"
    assert pill.parentWidget() is c._header
    assert TOKENS["line"] in c.styleSheet()
    c.set_edge("danger")
    assert KIT["card_danger_line"] in c.styleSheet() and c.edge == "danger"
    c.set_edge("em")
    assert TOKENS["emline"] in c.styleSheet()
    with pytest.raises(ValueError):
        c.set_edge("loud")
    with pytest.raises(ValueError):
        kit.Card("x", edge="loud")


def test_card_never_holds_a_card(app):
    from PySide6.QtWidgets import QWidget, QVBoxLayout
    outer = kit.Card("Outer")
    with pytest.raises(ValueError):
        outer.add_widget(kit.Card("Inner"))
    with pytest.raises(ValueError):
        outer.add_row(kit.Card("Inner"))
    wrapper = QWidget()
    QVBoxLayout(wrapper).addWidget(kit.Card("Deep"))
    with pytest.raises(ValueError):
        outer.add_widget(wrapper)


def test_card_rows_only_first_has_no_rule(app):
    c = kit.Card("Rows")
    a, b = kit.SettingRow("A"), kit.SettingRow("B")
    c.add_row(a)
    c.add_row(b)
    assert a.first and not b.first
    assert "border-top: none" in a.styleSheet()
    assert KIT["row_line"] in b.styleSheet()
    untitled = kit.Card()
    x = kit.SettingRow("X")
    untitled.add_row(x)
    assert x.first and untitled.title() == ""


def test_setting_row_column_widths_and_compact(app):
    sel = kit.Select(items=["Dark"])
    fixed = kit.SettingRow("Theme", "sub", sel)
    wide = kit.SettingRow("Notes", "", kit.Input(), width="wide")
    auto = kit.SettingRow("Backup", "", kit.LabelledAction("Browse"), width="auto")
    assert fixed.column.minimumWidth() == fixed.column.maximumWidth() == TOKENS["w_ctl"] == 230
    assert wide.column.maximumWidth() == KIT["w_ctl_wide"]
    assert auto.column.minimumWidth() == 0 and auto.column.maximumWidth() > 10000
    assert not fixed.sub_label.isHidden() and wide.sub_label.isHidden()
    fixed.set_compact(True)
    assert fixed.column.maximumWidth() > 10000
    assert fixed._root.direction() == kit.QBoxLayout.TopToBottom
    fixed.set_compact(False)
    assert fixed.column.maximumWidth() == 230
    with pytest.raises(ValueError):
        kit.SettingRow("x", width="huge")


def test_setting_row_fills_input_and_pushes_buttons_right(app):
    row = kit.SettingRow("T", "", [kit.Input()])
    row.resize(600, row.sizeHint().height())
    row.show()
    inp = row.column.findChild(kit.Input)
    assert inp.width() == row.column.width()
    sw = kit.Switch()
    row2 = kit.SettingRow("S", "", sw)
    row2.resize(600, row2.sizeHint().height())
    row2.show()
    assert sw.x() + sw.width() == row2.column.width()
    row.close()
    row2.close()


def test_setting_row_danger_title_is_red(app):
    assert TOKENS["dangertx"] in kit.SettingRow("Purge", danger=True).title_label.styleSheet()
    assert TOKENS["text"] in kit.SettingRow("Keep").title_label.styleSheet()


@pytest.mark.parametrize("tone", ["w", "r", "g", "i"])
def test_banner_tones_icon_and_action(app, tone):
    b = kit.Banner("Heads up", tone)
    assert b.tone == tone and b.text() == "Heads up"
    assert not b.icon_label.pixmap().isNull()
    b.add_action(kit.LabelledAction("Fix", sm=True))
    b.set_text("Changed")
    assert b.label.text() == "Changed"
    b.set_tone("r")
    assert b.tone == "r"


def test_banner_rejects_unknown_tone_and_wraps(app):
    with pytest.raises(ValueError):
        kit.Banner("x", "loud")
    b = kit.Banner("word " * 60, "i")
    assert b.label.wordWrap()
    assert b.heightForWidth(300) > b.heightForWidth(900) or b.layout().totalHeightForWidth(300) > b.layout().totalHeightForWidth(900)


def test_toast_keeps_alert_api_and_sits_bottom_centre(app):
    from PySide6.QtWidgets import QWidget
    host = QWidget()
    host.resize(800, 400)
    host.show()
    t = kit.Toast(host)
    assert t.isHidden()
    for level in ("success", "info", "warning", "error"):
        t.show_alert("Saved", level, 0)
        assert t.level == level and t.isVisible() and not t.timer.isActive()
    t.show_alert("Copied", "bogus", 0)
    assert t.level == "success"
    assert t.accessibleName() == "Copied"
    assert abs((t.x() + t.width() / 2) - 400) <= 1
    assert t.y() + t.height() == 400 - KIT["gap_toast"]
    host.resize(300, 400)
    assert t.width() <= 300 - 2 * TOKENS["s_4"]
    t.show_message("Hello", 5000)
    assert t.level == "info" and t.timer.isActive()
    t.hide_toast()
    assert t.isHidden() and not t.timer.isActive()
    t.show_alert("Again", "error", 0)
    t.hide_alert()
    assert t.isHidden()
    host.close()


def test_toast_long_message_wraps_within_max_width(app):
    from PySide6.QtWidgets import QWidget
    host = QWidget()
    host.resize(1200, 400)
    host.show()
    t = kit.Toast(host)
    t.show_alert("word " * 80, "warning", 0)
    assert t.width() <= KIT["w_toast_max"]
    assert t.height() > KIT["pad_toast_v"] * 2 + 20
    host.close()


def test_empty_state_texts_and_action(app):
    e = kit.EmptyState("No clients", "Add one.", icon="mdi.account-group-outline")
    assert e.title_label.text() == "No clients" and not e.text_label.isHidden()
    e.set_text("")
    assert e.text_label.isHidden()
    e.set_title("Nothing")
    assert e.title_label.text() == "Nothing"
    btn = kit.LabelledAction("Refresh", variant="soft", sm=True)
    e.add_action(btn)
    assert btn.parentWidget() is e


def test_section_label_is_theme_class_and_upper(app):
    s = kit.SectionLabel("General")
    assert s.property("class") == "SectionLabel"
    assert s.font().capitalization().name == "AllUppercase"


# ---------------------------------------------------------------- Chip and ChipBar

def test_chip_basic_and_filter_chip_alias(app):
    c = kit.Chip("All", count=16)
    assert c.text() == "All"
    assert c.base_text() == "All"
    assert c.count() == 16
    assert c.height() == TOKENS["h_chip"]
    assert c.property("class") == "FilterChip"
    assert issubclass(kit.FilterChip, kit.Chip) or kit.FilterChip is kit.Chip


def test_chip_value_when_on(app):
    c = kit.Chip("Client", value_when_on="Client: Unregistered")
    assert c.text() == "Client"
    assert not c.isChecked()
    c.setChecked(True)
    assert c.text() == "Client: Unregistered"
    c.setChecked(False)
    assert c.text() == "Client"
    c.set_value("Client: Active")
    assert c.isChecked()
    assert c.text() == "Client: Active"


def test_chip_count_badge_and_size_hint(app):
    c1 = kit.Chip("All")
    c2 = kit.Chip("All", count=16)
    assert c2.sizeHint().width() > c1.sizeHint().width()
    c2.set_count(None)
    assert c2.sizeHint().width() == c1.sizeHint().width()


def test_chip_disabled_reason(app):
    c = kit.Chip("Archived", tooltip="Show archived")
    c.set_disabled_reason("Admin only")
    assert not c.isEnabled()
    assert c.toolTip() == "Admin only"
    c.set_disabled_reason(None)
    assert c.isEnabled()
    assert c.toolTip() == "Show archived"


def test_chip_menu_indicator_and_exec(app):
    from PySide6.QtWidgets import QMenu
    menu = QMenu()
    menu.addAction("Option 1")
    c = kit.Chip("More", menu=menu)
    assert c.has_menu()
    assert c.menu() is menu


def test_chip_paint_offscreen(app):
    c = kit.Chip("Active", count=5, checked=True)
    c.resize(c.sizeHint())
    pm = c.grab()
    assert not pm.isNull()


def test_chip_bar_single_select(app):
    bar = kit.ChipBar(single_select=True)
    c1 = bar.add_chip("All", id="all", checked=True)
    c2 = bar.add_chip("Active", id="active")
    c3 = bar.add_chip("Archived", id="archived")
    assert bar.selected_chip() is c1
    assert bar.selected_id() == "all"
    assert c1.isChecked() and not c2.isChecked()

    events = []
    bar.currentChanged.connect(lambda cid: events.append(cid))
    c2.setChecked(True)
    assert not c1.isChecked()
    assert c2.isChecked()
    assert bar.selected_id() == "active"
    assert events == ["active"]

    # In single select mode, unchecking the active chip keeps it checked
    c2.setChecked(False)
    assert c2.isChecked()


def test_chip_bar_overflow_menu(app):
    from PySide6.QtWidgets import QMenu
    bar = kit.ChipBar(single_select=True)
    c1 = bar.add_chip("All", checked=True)
    menu = QMenu()
    menu.addAction("Preset A")
    more = bar.add_overflow_chip(menu=menu, text="More")
    assert bar.chip("__overflow__") is more
    assert more.has_menu()

    # Selecting overflow value activates overflow chip and unchecks c1
    bar.select_overflow_value("Client: Unregistered", id="unregistered")
    assert more.isChecked()
    assert not c1.isChecked()
    assert more.text() == "Client: Unregistered"
    assert bar.selected_id() == "unregistered"

    # Switching back to c1 unchecks overflow chip and resets its text to base text
    c1.setChecked(True)
    assert c1.isChecked()
    assert not more.isChecked()
    assert more.text() == "More"


def test_chip_bar_compact_second_row_mode(app):
    bar = kit.ChipBar()
    c1 = bar.add_chip("Main")
    c2 = bar.add_chip("Secondary", compact_hide=True)
    bar.show()
    assert not bar.is_compact()
    assert c1.isVisible() and c2.isVisible()

    compact_events = []
    bar.compactChanged.connect(lambda on: compact_events.append(on))
    bar.set_compact(True)
    assert bar.is_compact()
    assert c1.isVisible()
    assert not c2.isVisible()
    assert compact_events == [True]

    bar.apply_width_mode("wide")
    assert not bar.is_compact()
    assert c2.isVisible()
    bar.close()


# ---------------------------------------------------------------- StatusStrip

def test_status_strip_items(app):
    strip = kit.StatusStrip()
    assert strip.minimumHeight() == KIT["h_strip"]
    p = strip.add_pill("Sync on", tone="emerald", dot=True)
    assert isinstance(p, kit.Pill)
    fact = strip.add_fact("1", "other PC online")
    assert "1" in fact.text() and "other PC online" in fact.text()
    sep = strip.add_separator()
    assert sep.width() == 1 and sep.height() == KIT["h_strip_sep"]
    txt = strip.add_text("This PC:", muted=True)
    assert txt.text() == "This PC:"
    strip.add_stretch()
    btn = kit.LabelledAction("Scan again", sm=True)
    strip.add_widget(btn)
    assert btn.parentWidget() is strip


def test_status_strip_clear(app):
    strip = kit.StatusStrip()
    strip.add_fact("1")
    strip.add_separator()
    assert strip.layout().count() == 2
    strip.clear()
    assert strip.layout().count() == 0


# ---------------------------------------------------------------- SelectionBar

def test_selection_bar_fixed_height_and_empty_hint(app):
    bar = kit.SelectionBar()
    assert bar.height() == KIT["h_selbar"]
    assert not bar.has_selection()
    assert not bar.hint_label.isHidden()
    assert bar.active_container.isHidden()
    assert "Select a client row" in bar.hint()


def test_selection_bar_active_selection_and_who(app):
    bar = kit.SelectionBar()
    bar.set_who("Acme Corp")
    assert bar.has_selection()
    assert bar.who() == "Acme Corp"
    assert bar.hint_label.isHidden()
    assert not bar.active_container.isHidden()
    assert KIT["selbar_has_bg"] in bar.styleSheet()

    bar.set_empty()
    assert not bar.has_selection()
    assert not bar.hint_label.isHidden()
    assert bar.active_container.isHidden()
    assert TOKENS["card"] in bar.styleSheet()


def test_selection_bar_buttons_have_no_focus(app):
    bar = kit.SelectionBar()
    b1 = bar.add_action("Open", variant="soft", icon="mdi.arrow-right")
    b2 = bar.add_action("Edit", icon="mdi.pencil")
    dng = bar.add_danger_action("Delete…")
    assert b1.focusPolicy() == Qt.NoFocus
    assert b2.focusPolicy() == Qt.NoFocus
    assert dng.focusPolicy() == Qt.NoFocus
    assert dng.variant == "danger"


def test_selection_bar_compact_icon_only_except_open(app):
    bar = kit.SelectionBar()
    open_btn = bar.add_action("Open", icon="mdi.arrow-right")
    edit_btn = bar.add_action("Edit", icon="mdi.pencil")
    archive_btn = bar.add_action("Archive", icon="mdi.archive")

    assert not open_btn.is_icon_only()
    assert not edit_btn.is_icon_only()

    bar.set_compact(True)
    assert not open_btn.is_icon_only()  # Open keeps its label!
    assert edit_btn.is_icon_only()
    assert archive_btn.is_icon_only()

    bar.set_compact(False)
    assert not open_btn.is_icon_only()
    assert not edit_btn.is_icon_only()
    assert not archive_btn.is_icon_only()


def test_selection_bar_groups_and_dividers(app):
    bar = kit.SelectionBar()
    vr = bar.add_divider()
    assert vr.width() == 1 and vr.height() == KIT["h_selbar_vr"]
    grp = bar.add_group_label("Cell")
    assert grp.text() == "CELL"


# ---------------------------------------------------------------- UnsavedBar

def test_unsaved_bar_hidden_when_clean(app):
    bar = kit.UnsavedBar()
    assert bar.height() == KIT["h_unsaved"]
    assert bar.count() == 0
    assert not bar.is_dirty()
    assert bar.isHidden()


def test_unsaved_bar_count_and_dirty_state(app):
    bar = kit.UnsavedBar()
    bar.set_count(1)
    assert bar.count() == 1
    assert bar.is_dirty()
    assert not bar.isHidden()
    assert "1 unsaved change" in bar.label.text()
    assert "changes" not in bar.label.text()

    bar.set_count(3)
    assert bar.count() == 3
    assert "3 unsaved changes" in bar.label.text()

    bar.set_count(0)
    assert not bar.is_dirty()
    assert bar.isHidden()


def test_unsaved_bar_signals(app):
    bar = kit.UnsavedBar()
    bar.set_count(2)
    discards = []
    saves = []
    bar.discard_clicked.connect(lambda: discards.append(1))
    bar.save_clicked.connect(lambda: saves.append(1))

    bar.discard_btn.click()
    assert discards == [1]

    bar.save_btn.click()
    assert saves == [1]


def test_unsaved_bar_reserved_space_mode(app):
    bar = kit.UnsavedBar(reserved_space=True)
    bar.show()
    assert bar.isVisible()
    assert bar.label.isHidden()  # contents hidden when clean
    assert bar.height() == KIT["h_unsaved"]

    bar.set_count(1)
    assert bar.isVisible()
    assert not bar.label.isHidden()
    assert bar.is_dirty()
