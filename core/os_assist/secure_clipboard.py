"""Copy to the clipboard; secrets stay out of Win+V history and cloud sync (§4.5).

Call from the Qt main thread only. The text is never logged.
"""
from PySide6.QtCore import QByteArray, QMimeData, QTimer
from PySide6.QtWidgets import QApplication

_DWORD_ZERO = QByteArray(b"\x00\x00\x00\x00")


def _fmt(name: str) -> str:
    return f'application/x-qt-windows-mime;value="{name}"'


EXCLUDE_MONITOR = _fmt("ExcludeClipboardContentFromMonitorProcessing")
NO_HISTORY = _fmt("CanIncludeInClipboardHistory")
NO_CLOUD = _fmt("CanUploadToCloudClipboard")


def copy(text: str, secret: bool, clear_after_s: int | None = None) -> None:
    clipboard = QApplication.clipboard()
    mime = QMimeData()
    mime.setText(text)
    if secret:
        mime.setData(EXCLUDE_MONITOR, QByteArray())
        mime.setData(NO_HISTORY, _DWORD_ZERO)
        mime.setData(NO_CLOUD, _DWORD_ZERO)
    clipboard.setMimeData(mime)
    if clear_after_s:
        QTimer.singleShot(int(clear_after_s * 1000), lambda t=text: clear_if_unchanged(t))


def clear_if_unchanged(text: str) -> bool:
    clipboard = QApplication.clipboard()
    if clipboard.text() != text:
        return False
    clipboard.clear()
    return True
