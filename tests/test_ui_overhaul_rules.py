"""S-R: the overhaul's rules over every converted screen file, and the kit MessageBox that replaced QMessageBox.

Rule 11: screens import, they do not style - no setStyleSheet( with a literal. Rule 3: tokens, not hex.
Every prompt is a kit dialog. Out of the overhaul (their look stays): the SGT lab, the SDIS panels and
the legacy settings dialog (not live; F-1 removes it).
"""
import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from ui.components.kit import LabelledAction, MessageBox

ROOT = Path(__file__).resolve().parent.parent
OUT_OF_OVERHAUL = {"sgt_lab_dialog.py", "sdis_dialog.py", "sdis_containers_dialog.py", "settings_dialog.py"}
SCREEN_FILES = sorted(p for d in ("ui/windows", "ui/dialogs", "ui/shell") for p in (ROOT / d).glob("*.py")
                      if p.name not in OUT_OF_OVERHAUL and p.name != "__init__.py")
HEX = re.compile(r"#[0-9A-Fa-f]{6}\b|#[0-9A-Fa-f]{3}\b|rgba?\(")

# Colours that are data, not look: the user's cell-highlight palettes (stored in the database) and
# Tracker Dump's status / capture-source colours (other code and tests read them).
_ALLOWED_SPANS = {
    "search_window.py": [re.compile(r"\(\"[^\"]+\", \"#[0-9A-Fa-f]{6}\"\),")],
    "tracker_dump_window.py": [
        re.compile(r"^STATUS_THEMES = \{.*?^\}\n", re.S | re.M),
        re.compile(r"^def _get_status_theme\(.*?^\n\n", re.S | re.M),
        re.compile(r"^def _capture_method_color\(.*?^\n\n", re.S | re.M),
    ],
}


def _source(path):
    src = path.read_text(encoding="utf-8")
    for rx in _ALLOWED_SPANS.get(path.name, []):
        src = rx.sub("", src)
    return src


def test_the_screen_list_is_complete():
    names = {p.name for p in SCREEN_FILES}
    assert {"search_window.py", "unified_settings_dialog.py", "sera_sync_dialog.py", "app_shell.py"} <= names


@pytest.mark.parametrize("path", SCREEN_FILES, ids=lambda p: p.name)
def test_no_literal_stylesheet(path):
    src = path.read_text(encoding="utf-8")
    assert not re.search(r"setStyleSheet\(\s*f?[\"']", src)


@pytest.mark.parametrize("path", SCREEN_FILES, ids=lambda p: p.name)
def test_no_hex_colour_outside_tokens(path):
    found = HEX.findall(_source(path))
    assert found == []


@pytest.mark.parametrize("path", SCREEN_FILES + [ROOT / "main.py"], ids=lambda p: p.name)
def test_every_prompt_is_a_kit_dialog(path):
    src = path.read_text(encoding="utf-8")
    assert not re.search(r"QMessageBox\.(information|warning|critical|question|about)\(", src)
    assert not re.search(r"QMessageBox\(", src)


# ---- kit.MessageBox ------------------------------------------------------------------------

@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _press(label=None, key=None):
    """Once the modal is up, click the footer button called `label` (or press `key`)."""
    def go():
        dlg = QApplication.activeModalWidget()
        assert isinstance(dlg, QDialog)
        if key is not None:
            QTest.keyClick(dlg.focusWidget() or dlg, key)
            return
        from ui.components.kit import LabelledAction
        buttons = {b.text(): b for b in dlg.findChildren(LabelledAction)}
        buttons[label].click()
    QTimer.singleShot(0, go)


def _footer(dlg):
    from ui.components.kit import LabelledAction
    return [(b.text(), b.variant) for b in dlg.findChildren(LabelledAction)]


def test_question_returns_the_standard_button_clicked(app):
    from ui.components.kit import MessageBox
    _press("Yes")
    assert MessageBox.question(None, "Delete", "Delete it?") == QMessageBox.Yes
    _press("No")
    assert MessageBox.question(None, "Delete", "Delete it?") == QMessageBox.No


def test_esc_gives_no_or_cancel_and_ok_for_a_message(app):
    from ui.components.kit import MessageBox
    _press(key=Qt.Key_Escape)
    assert MessageBox.question(None, "Q", "Sure?") == QMessageBox.No
    _press(key=Qt.Key_Escape)
    assert MessageBox.warning(None, "Q", "Sure?", QMessageBox.Ok | QMessageBox.Cancel) == QMessageBox.Cancel
    _press(key=Qt.Key_Escape)
    assert MessageBox.information(None, "Done", "Saved.") == QMessageBox.Ok


def test_the_default_button_is_the_one_primary_and_danger_paints_it_red(app):
    from ui.components.kit import MessageBox
    seen = []

    def look():
        dlg = QApplication.activeModalWidget()
        seen.append(_footer(dlg))
        dlg.reject()
    QTimer.singleShot(0, look)
    MessageBox.question(None, "Q", "Text", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
    QTimer.singleShot(0, look)
    MessageBox.question(None, "Q", "Text", danger=True)
    assert seen[0] == [("No", "primary"), ("Yes", "secondary")]
    assert seen[1] == [("No", "secondary"), ("Yes", "danger")]


def test_pending_join_prompt_maps_its_three_choices(app, monkeypatch):
    import ui.dialogs.rejoin_office_dialog as rd
    shown = []

    def answer(index):
        def choose(parent, title, text, choices, **kwargs):
            shown.append([c[0] for c in choices])
            return index
        monkeypatch.setattr(MessageBox, "choose", choose)

    for index, expected in ((2, "finish"), (0, "undo"), (1, "quit"), (None, "quit")):
        answer(index)
        assert rd._ask_pending_join() == expected
    assert shown[0] == ["Undo rejoin", "Close Sera", "Finish now"]


def test_choose_returns_the_index_and_escape_on_esc(app):
    from ui.components.kit import MessageBox
    choices = [("Discard", "secondary"), ("Keep editing", "secondary"), ("Save", "primary")]
    _press("Discard")
    assert MessageBox.choose(None, "Unsaved", "2 changes", choices) == 0
    _press(key=Qt.Key_Escape)
    assert MessageBox.choose(None, "Unsaved", "2 changes", choices, escape=1) == 1
