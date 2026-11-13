"""AppShell closes Client Detail on a click outside the slide panel, but only for clicks in its own
window: its filter is app-wide, and a click on another top-level window (an OS Assist card over the
browser) must reach that window, not be eaten to close the panel."""
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from ui.shell.app_shell import AppShell

APP = QApplication.instance() or QApplication([])


def _shell_with_detail_open():
    shell = AppShell()
    shell.resize(1300, 800)
    shell.show()
    shell.slide_panel.set_widget(QLabel("detail"), persistent=True)
    shell.slide_panel.slide_in()
    shell.dismiss_detail_on_outside = True
    APP.processEvents()
    return shell


def test_a_click_on_another_top_level_window_reaches_it_and_keeps_detail_open():
    shell = _shell_with_detail_open()
    other = QWidget(None, Qt.Tool | Qt.FramelessWindowHint)
    btn = QPushButton("Username", other)
    btn.resize(160, 40)
    other.resize(200, 60)
    other.move(shell.geometry().topLeft() + QPoint(5, 5))   # over the shell, outside the panel
    other.show()
    APP.processEvents()
    clicks = []
    btn.clicked.connect(lambda: clicks.append(1))
    try:
        QTest.mouseClick(btn, Qt.LeftButton)
        assert clicks == [1]                                  # the first click is not eaten
        assert shell.slide_panel.is_open
        assert shell.dismiss_detail_on_outside
    finally:
        other.close()
        shell.close()


def test_a_click_in_the_shell_outside_the_panel_still_closes_detail():
    shell = _shell_with_detail_open()
    try:
        QTest.mouseClick(shell.sidebar, Qt.LeftButton, Qt.NoModifier, QPoint(5, 5))
        assert not shell.slide_panel.is_open
        assert not shell.dismiss_detail_on_outside
    finally:
        shell.close()
