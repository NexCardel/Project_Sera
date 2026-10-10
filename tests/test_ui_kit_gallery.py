"""K-7: the gallery covers the whole kit, and every interactive component answers to Tab / Space / Enter,
carries a tooltip when it needs one, and elides long text with the full text one hover away."""

import inspect
import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from ui.components import kit

ROOT = Path(kit.__file__).resolve().parents[2]
GALLERY = ROOT / "tools" / "ui_gallery.py"
SECTIONS = ("primitives", "inputs", "containers", "bars", "grid", "frames", "states")

# Not shown on their own: GridDelegate paints DataGrid cells, IconTile is the icon square inside DialogFrame,
# FilterChip is another name for Chip.
NOT_STANDALONE = {"GridDelegate", "IconTile", "FilterChip"}


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


# ---------------------------------------------------------------- the gallery

def _public_widgets():
    return sorted(name for name, obj in vars(kit).items()
                  if inspect.isclass(obj) and obj.__module__ == kit.__name__ and not name.startswith("_")
                  and name not in NOT_STANDALONE)


def test_gallery_shows_every_public_component():
    source = GALLERY.read_text(encoding="utf-8")
    missing = [n for n in _public_widgets() if f"kit.{n}" not in source]
    assert not missing, f"not in tools/ui_gallery.py: {missing}"


def test_gallery_states_page_covers_hover_focus_disabled():
    source = GALLERY.read_text(encoding="utf-8")
    assert 'STATES = ("normal", "hover", "focus", "disabled")' in source
    for name in ("LabelledAction", "IconButton", "Segmented", "Input", "Select", "TextArea", "Stepper",
                 "Checkbox", "Switch", "Chip", "Tile"):
        assert f"kit.{name}(" in source.split("def states_page")[1], name


@pytest.mark.parametrize("scale", [1.0, 1.25])
def test_gallery_renders_every_section(tmp_path, scale):
    out = subprocess.run([sys.executable, str(GALLERY), str(tmp_path), "--scale", str(scale)],
                         capture_output=True, text=True, cwd=str(ROOT), timeout=240,
                         env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
    assert out.returncode == 0, out.stderr[-2000:]
    tag = int(round(scale * 100))
    for section in SECTIONS:
        png = tmp_path / f"{section}@{tag}.png"
        assert png.is_file(), png.name
        img = QImage(str(png))
        assert img.width() > 200 and img.height() > 100, png.name
    big = QImage(str(tmp_path / f"states@{tag}.png"))
    assert big.height() > 800        # every state row is there, not an empty page


# ---------------------------------------------------------------- Tab order

def _focus_window(app, widgets):
    win = QWidget()
    lay = QVBoxLayout(win)
    for w in widgets:
        lay.addWidget(w)
    win.show()
    win.activateWindow()
    QApplication.processEvents()
    return win


def test_tab_walks_every_interactive_control_once_in_order(app):
    controls = [kit.LabelledAction("Save"), kit.IconButton("mdi.refresh", "Refresh"),
                kit.Chip("Active"), kit.Tile("3", "Open"), kit.Switch(tooltip="Mask"),
                kit.Checkbox("Row"), kit.Segmented(["A", "B", "C"]), kit.Stepper(value=3, maximum=9),
                kit.Select(items=["x", "y"]), kit.Input()]
    win = _focus_window(app, controls)
    controls[0].setFocus(Qt.TabFocusReason)
    seen = [QApplication.focusWidget()]
    for _ in range(len(controls) - 1):
        QTest.keyClick(QApplication.focusWidget(), Qt.Key_Tab)
        seen.append(QApplication.focusWidget())
    assert seen == controls
    win.close()


def test_shift_tab_walks_back(app):
    controls = [kit.LabelledAction("One"), kit.Chip("Two"), kit.Switch(tooltip="Three")]
    win = _focus_window(app, controls)
    controls[2].setFocus(Qt.TabFocusReason)
    for expected in (controls[1], controls[0]):
        QTest.keyClick(QApplication.focusWidget(), Qt.Key_Tab, Qt.ShiftModifier)
        assert QApplication.focusWidget() is expected
    win.close()


def test_disabled_controls_are_skipped_by_tab(app):
    a, b, c = kit.LabelledAction("A"), kit.LabelledAction("B"), kit.LabelledAction("C")
    b.set_disabled_reason("Not yet")
    win = _focus_window(app, [a, b, c])
    a.setFocus(Qt.TabFocusReason)
    QTest.keyClick(a, Qt.Key_Tab)
    assert QApplication.focusWidget() is c
    win.close()


# ---------------------------------------------------------------- Space / Enter

def _chip():
    return kit.Chip("Active")


def _tile():
    return kit.Tile("3", "Open")


def _switch():
    return kit.Switch(tooltip="Mask passwords")


SPACE = {
    "LabelledAction": lambda: kit.LabelledAction("Save"),
    "IconButton": lambda: kit.IconButton("mdi.refresh", "Refresh"),
    "Chip": _chip, "Tile": _tile, "Switch": _switch,
    "Checkbox": lambda: kit.Checkbox("Row"),
}
ENTER = {"Chip": _chip, "Tile": _tile, "Switch": _switch}


@pytest.mark.parametrize("name", SPACE)
def test_space_activates_a_focused_control(app, name):
    w = SPACE[name]()
    win = _focus_window(app, [w])
    w.setFocus(Qt.TabFocusReason)
    spy = QSignalSpy(w.clicked)
    QTest.keyClick(w, Qt.Key_Space)
    assert spy.count() == 1
    win.close()


@pytest.mark.parametrize("name", ENTER)
def test_enter_activates_chip_tile_and_switch(app, name):
    w = ENTER[name]()
    win = _focus_window(app, [w])
    w.setFocus(Qt.TabFocusReason)
    spy = QSignalSpy(w.clicked)
    QTest.keyClick(w, Qt.Key_Return)
    assert spy.count() == 1
    QTest.keyClick(w, Qt.Key_Enter)
    assert spy.count() == 2
    win.close()


@pytest.mark.parametrize("name", SPACE)
def test_disabled_control_ignores_space_and_keeps_its_reason_tooltip(app, name):
    w = SPACE[name]()
    win = _focus_window(app, [w])
    w.set_disabled_reason("Select a client first")
    spy = QSignalSpy(w.clicked)
    w.setFocus(Qt.TabFocusReason)
    QTest.keyClick(w, Qt.Key_Space)
    QTest.keyClick(w, Qt.Key_Return)
    assert spy.count() == 0 and not w.isEnabled()
    assert w.toolTip() == "Select a client first"
    w.set_disabled_reason(None)
    assert w.isEnabled() and w.toolTip() != "Select a client first"
    win.close()


def test_checkable_controls_report_state_through_toggled(app):
    for make in (_chip, _tile, _switch, lambda: kit.Checkbox("Row")):
        w = make()
        win = _focus_window(app, [w])
        w.setFocus(Qt.TabFocusReason)
        spy = QSignalSpy(w.toggled)
        QTest.keyClick(w, Qt.Key_Space)
        assert spy.count() == 1 and w.isChecked() and spy.at(0)[0] is True
        QTest.keyClick(w, Qt.Key_Space)
        assert spy.count() == 2 and not w.isChecked()
        win.close()


def test_segmented_and_stepper_answer_arrow_keys_with_their_signals(app):
    seg = kit.Segmented(["A", "B", "C"])
    step = kit.Stepper(value=3, minimum=0, maximum=5)
    win = _focus_window(app, [seg, step])
    seg.setFocus(Qt.TabFocusReason)
    seg_spy = QSignalSpy(seg.currentChanged)
    QTest.keyClick(seg, Qt.Key_Right)
    assert seg.current_index() == 1 and seg_spy.count() == 1
    step.setFocus(Qt.TabFocusReason)
    step_spy = QSignalSpy(step.valueChanged)
    QTest.keyClick(step, Qt.Key_Up)
    assert step.value() == 4 and step_spy.count() == 1
    QTest.keyClick(step, Qt.Key_Home)
    assert step.value() == 0
    QTest.keyClick(step, Qt.Key_Down)                    # at the minimum: no change, no signal
    assert step.value() == 0 and step_spy.count() == 2
    win.close()


# ---------------------------------------------------------------- tooltips

def test_icon_only_controls_cannot_exist_without_a_tooltip(app):
    with pytest.raises(ValueError):
        kit.IconButton("mdi.refresh", "")
    with pytest.raises(ValueError):
        kit.CellAction("view", "mdi.eye-outline", "")
    assert kit.IconButton("mdi.refresh", "Refresh").toolTip() == "Refresh"
    assert kit.Switch(tooltip="Mask passwords").toolTip() == "Mask passwords"
    assert kit.Switch(tooltip="Mask passwords").accessibleName() == "Mask passwords"


def test_icon_only_labelled_action_keeps_its_text_as_tooltip(app):
    b = kit.LabelledAction("Archive", icon="mdi.archive")
    b.set_icon_only(True)
    assert b.toolTip() == "Archive"


@pytest.mark.parametrize("make", [
    lambda: kit.LabelledAction("Save"), lambda: kit.IconButton("mdi.refresh", "Refresh"),
    lambda: kit.Select(items=["a"]), lambda: kit.Stepper(value=1), lambda: kit.Checkbox("Row"),
    lambda: kit.Switch(tooltip="Mask"), lambda: kit.Chip("Active"), lambda: kit.Tile("1", "Open"),
])
def test_disabled_reason_is_the_tooltip_and_is_restored(app, make):
    w = make()
    before = w.toolTip()
    w.set_disabled_reason("Admin only")
    assert not w.isEnabled() and w.toolTip() == "Admin only" and w.disabled_reason == "Admin only"
    w.set_disabled_reason(None)
    assert w.isEnabled() and w.toolTip() == before and w.disabled_reason is None


# ---------------------------------------------------------------- elision

LONG = "A very long label that cannot possibly fit into this narrow space"


def test_pill_tag_checkbox_and_tile_elide_with_the_full_text_as_tooltip(app):
    pill = kit.Pill(LONG, "amber")
    tag = kit.Tag(LONG, "red")
    box = kit.Checkbox(LONG)
    tile = kit.Tile("9", LONG)
    for w in (pill, tag, box, tile):
        w.setFixedWidth(110)
    win = _focus_window(app, [pill, tag, box, tile])
    for w in (pill, tag, box, tile):
        assert w.toolTip() == LONG, type(w).__name__
    win.close()


def test_short_text_is_not_elided_and_has_no_stray_tooltip(app):
    pill = kit.Pill("Online", "emerald")
    box = kit.Checkbox("Row")
    tile = kit.Tile("9", "Open")
    win = _focus_window(app, [pill, box, tile])
    assert tile.toolTip() == "" and not tile.is_elided()
    assert pill.toolTip() in ("", "Online")
    assert box.toolTip() in ("", "Row")
    win.close()


def test_chip_keeps_full_text_in_tooltip_when_narrow(app):
    chip = kit.Chip(LONG)
    chip.setMaximumWidth(120)
    win = _focus_window(app, [chip])
    assert chip.width() <= 120
    assert LONG in (chip.toolTip(), chip.text())
    win.close()


def test_empty_states_render_without_content(app):
    es = kit.EmptyState("Nothing here")
    grid = kit.DataGrid(0, 3)
    log = kit.LogView()
    bar = kit.SelectionBar()
    win = _focus_window(app, [es, grid, log, bar])
    assert grid.rowCount() == 0 and log.line_count() == 0
    assert bar.hint_label.isVisibleTo(bar)
    win.close()


# ---------------------------------------------------------------- K-R review fixes

def test_chip_text_is_sized_in_pixels_not_points(app):
    from PySide6.QtGui import QFont, QFontMetrics
    from ui.utils.tokens import KIT
    chip = kit.Chip("Has services")
    font = QFont(chip.font())
    font.setPixelSize(KIT["f_chip"])
    font.setWeight(QFont.Medium)
    text = QFontMetrics(font).horizontalAdvance("Has services")
    assert chip.sizeHint().width() == max(2 * KIT["pad_chip_h"] + text, 28)


def test_selection_bar_name_elides_with_a_tooltip(app):
    bar = kit.SelectionBar()
    bar.set_who("Roy Pharma Distributors Private Limited")
    for text in ("Open", "Edit", "Services", "Archive"):
        bar.add_action(text)
    bar.resize(380, bar.height())
    bar.show()
    app.processEvents()
    assert bar.who_label.is_elided() and bar.who_label.toolTip() == bar.who()
    bar.close()


def test_tile_remeasures_when_the_app_font_arrives(app, monkeypatch):
    from PySide6.QtGui import QFont
    t = kit.Tile("12", "Worked")
    calls = []
    monkeypatch.setattr(t, "_refresh", lambda: calls.append(1))
    font = QFont(t.font())
    font.setFamily("Courier New")
    t.setFont(font)                       # what the app sheet's font does at polish
    assert calls


def test_icon_only_action_is_wide_enough_for_its_swatch(app):
    a = kit.LabelledAction("Fill", icon="mdi.format-color-fill", sm=True)
    a.set_swatch("#E3B341")
    a.set_icon_only(True)
    assert a.width() >= a.iconSize().width() + 12 > a.height()


def test_a_plain_label_in_a_card_body_sits_on_the_card(app):
    card = kit.Card("Engine")
    card.add_widget(kit.QLabel("Body"))
    assert "QWidget#KitCardBody > QLabel { background: transparent; }" in card.styleSheet()
