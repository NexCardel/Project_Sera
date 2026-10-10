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
