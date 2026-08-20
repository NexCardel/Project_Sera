"""
dynamic_form_widgets.py
------------------------
Shared UI pieces for rendering dynamic fields at runtime.
"""

from PySide6.QtCore import QDate, QRegularExpression, Qt
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QLineEdit,
)

DATE_FORMAT = "yyyy-MM-dd"

def make_input_widget(field: dict, value: str = "", mask_password: bool = True):
    """Builds the right editable input widget for a field definition."""
    field_type = field.get("field_type", "text")

    if field_type == "id":
        widget = QLineEdit(value or "(Auto-Generated)")
        widget.setReadOnly(True)
        widget.setFocusPolicy(Qt.NoFocus)
        widget.setStyleSheet("background-color: #EFEFEF; color: #2E9B5F; font-weight: 700; border: 1px solid #CCCCCC;")
        return widget

    if field_type == "password":
        widget = QLineEdit(value or "")
        if mask_password:
            widget.setEchoMode(QLineEdit.Password)
        else:
            widget.setEchoMode(QLineEdit.Normal)
        return widget
        
    if field_type == "number":
        widget = QLineEdit(value or "")
        regex = QRegularExpression(r"^[0-9]*\.?[0-9]*$")
        validator = QRegularExpressionValidator(regex)
        widget.setValidator(validator)
        return widget

    if field_type == "alphanumeric":
        widget = QLineEdit(value or "")
        regex = QRegularExpression(r"^[A-Za-z0-9]*$")
        validator = QRegularExpressionValidator(regex)
        widget.setValidator(validator)
        # Codes like PAN/GSTIN are conventionally uppercase; auto-correct as typed.
        widget.textEdited.connect(lambda text, w=widget: w.setText(text.upper()))
        return widget

    if field_type == "dropdown":
        widget = QComboBox()
        widget.addItem("", "")
        for option in field.get("dropdown_options") or []:
            widget.addItem(option, option)
        idx = widget.findData(value or "")
        widget.setCurrentIndex(max(idx, 0))
        return widget

    if field_type == "date":
        widget = QDateEdit()
        widget.setCalendarPopup(True)
        widget.setDisplayFormat(DATE_FORMAT)
        qdate = QDate.fromString(value, DATE_FORMAT) if value else None
        widget.setDate(qdate if qdate and qdate.isValid() else QDate.currentDate())
        return widget

    return QLineEdit(value or "")

def read_input_widget(field: dict, widget) -> str:
    """Extracts a plain string value back out of a widget."""
    field_type = field.get("field_type", "text")
    if field_type == "dropdown":
        return widget.currentData() or ""
    if field_type == "date":
        return widget.date().toString(DATE_FORMAT)
    return widget.text().strip() if field_type != "password" else widget.text()