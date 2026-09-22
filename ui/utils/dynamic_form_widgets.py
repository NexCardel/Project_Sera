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

# An empty date is stored as "". The widget shows it as "Not set" by sitting on
# its minimum date, so a blank DOB no longer silently saves as today.
_EMPTY_DATE = QDate(1900, 1, 1)


class OptionalDateEdit(QDateEdit):
    """QDateEdit that can be blank. Opens its calendar on today when empty."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCalendarPopup(True)
        self.setDisplayFormat(DATE_FORMAT)
        self.setMinimumDate(_EMPTY_DATE)
        self.setSpecialValueText("Not set")
        self.setDate(_EMPTY_DATE)

    def is_empty(self) -> bool:
        return self.date() == self.minimumDate()

    def set_value(self, value: str):
        qdate = QDate.fromString(value, DATE_FORMAT) if value else None
        self.setDate(qdate if qdate and qdate.isValid() else _EMPTY_DATE)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if self.is_empty() and self.calendarWidget():
            today = QDate.currentDate()
            self.calendarWidget().setCurrentPage(today.year(), today.month())

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
        widget = OptionalDateEdit()
        widget.set_value(value)
        return widget

    return QLineEdit(value or "")

def read_input_widget(field: dict, widget) -> str:
    """Extracts a plain string value back out of a widget."""
    field_type = field.get("field_type", "text")
    if field_type == "dropdown":
        return widget.currentData() or ""
    if field_type == "date":
        if isinstance(widget, OptionalDateEdit) and widget.is_empty():
            return ""
        return widget.date().toString(DATE_FORMAT)
    return widget.text().strip() if field_type != "password" else widget.text()

def set_input_widget_value(field: dict, widget, value: str = ""):
    """Updates an existing widget's value in-place without rebuilding UI."""
    field_type = field.get("field_type", "text")
    if field_type == "id":
        widget.setText(value or "(Auto-Generated)")
    elif field_type == "dropdown":
        idx = widget.findData(value or "")
        widget.setCurrentIndex(max(idx, 0))
    elif field_type == "date":
        if isinstance(widget, OptionalDateEdit):
            widget.set_value(value)
        else:
            qdate = QDate.fromString(value, DATE_FORMAT) if value else None
            widget.setDate(qdate if qdate and qdate.isValid() else QDate.currentDate())
    else:
        widget.setText(value or "")