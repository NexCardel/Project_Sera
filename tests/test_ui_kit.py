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
