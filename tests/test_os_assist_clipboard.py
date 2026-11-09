import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from core.os_assist import secure_clipboard as sc

FAKE = "fictional-secret-123"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_secret_copy_sets_the_three_formats(app):
    sc.copy(FAKE, secret=True)
    mime = QApplication.clipboard().mimeData()
    assert mime.text() == FAKE
    assert mime.hasFormat(sc.EXCLUDE_MONITOR)
    assert bytes(mime.data(sc.EXCLUDE_MONITOR)) == b""
    assert bytes(mime.data(sc.NO_HISTORY)) == b"\x00\x00\x00\x00"
    assert bytes(mime.data(sc.NO_CLOUD)) == b"\x00\x00\x00\x00"


def test_plain_copy_has_no_secret_formats(app):
    sc.copy("hello", secret=False)
    mime = QApplication.clipboard().mimeData()
    assert mime.text() == "hello"
    for f in (sc.EXCLUDE_MONITOR, sc.NO_HISTORY, sc.NO_CLOUD):
        assert not mime.hasFormat(f)


def test_clear_only_when_unchanged(app):
    sc.copy(FAKE, secret=True)
    assert sc.clear_if_unchanged(FAKE) is True
    assert QApplication.clipboard().text() == ""
    sc.copy("something else", secret=False)
    assert sc.clear_if_unchanged(FAKE) is False
    assert QApplication.clipboard().text() == "something else"


def test_timer_clears_after_delay(app):
    from PySide6.QtCore import QEventLoop, QTimer
    sc.copy(FAKE, secret=True, clear_after_s=0.05)
    loop = QEventLoop()
    QTimer.singleShot(300, loop.quit)
    loop.exec()
    assert QApplication.clipboard().text() == ""
