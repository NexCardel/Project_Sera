"""Tabs and KeyValueGrid (kit additions for Sera Sync and the client panel, polish P-3 / P-9 / P-12/P-13)."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from ui.components import kit
from ui.utils.tokens import KIT, TOKENS


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_tabs_select_on_click_and_arrow_keys(app):
    tabs = kit.Tabs(["Sync status", "Devices", "Activity"])
    seen = []
    tabs.currentChanged.connect(seen.append)
    assert tabs.current_index() == 0
    tabs.tabs[2].click()
    assert tabs.current_index() == 2 and seen == [2]
    tabs.set_current(2)           # no change, no signal
    assert seen == [2]
    tabs.setFocus()
    QTest.keyClick(tabs, Qt.Key_Left)
    assert tabs.current_index() == 1
    QTest.keyClick(tabs, Qt.Key_Right)
    QTest.keyClick(tabs, Qt.Key_Right)
    assert tabs.current_index() == 2   # clamped at the end


def test_tabs_are_underline_height_and_badge_counts(app):
    tabs = kit.Tabs(["Sync status", "Devices"], current=1)
    assert tabs.height() == tabs.tabs[0].sizeHint().height()
    assert tabs.tabs[1].isChecked()
    before = tabs.tabs[0].sizeHint().width()
    tabs.set_count(0, 3, amber=True)
    assert tabs.tabs[0]._count == 3 and tabs.tabs[0]._amber
    assert tabs.tabs[0].sizeHint().width() > before
    tabs.grab()


def test_key_value_grid_two_columns_rules_and_not_set(app):
    kv = kit.KeyValueGrid()
    copied = []
    kv.add_field("PAN", "ABCDE1000F", mono=True, actions=[("mdi.content-copy", "Copy", lambda: copied.append(1))])
    kv.add_field("Phone", "98000 00001")
    kv.add_field("Email", "a@example.com")
    kv.set_not_set(["DOB", "TAN"])
    assert len(kv._cells) == 3
    # the first row has a hairline (another row follows), the last row does not
    assert kv._cells[0]._rule and kv._cells[1]._rule
    assert not kv._cells[2]._rule
    assert kv._notset.isVisible() is False or kv._notset.text().startswith("<span")
    assert "Not set:" in kv._notset.text() and "DOB, TAN" in kv._notset.text()
    kv.grab()


def test_key_value_grid_hides_not_set_when_nothing_is_missing(app):
    kv = kit.KeyValueGrid()
    kv.add_field("Phone", "98000 00001")
    kv.set_not_set([])
    assert not kv._notset.isVisible()
    assert kv._cells[0]._rule is False


def test_key_value_value_text_and_copy_button(app):
    kv = kit.KeyValueGrid()
    cell = kv.add_field("PAN", "ABCDE1000F", mono=True, actions=[("mdi.content-copy", "Copy", lambda: None)])
    labels = [w for w in cell.findChildren(QLabel)]
    assert any(l.text() == "ABCDE1000F" or l.text().startswith("ABCDE") for l in labels)
    assert TOKENS["muted"] in labels[0].styleSheet()
    assert KIT["f_kv_label"] == 11
