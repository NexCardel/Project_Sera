"""
tag_widget.py
-------------
Reusable PySide6 colored tag / badge component for DRS statuses and categories.
Supports: Submitted (Green), Pending (Red), In-Progress (Amber), Overdue (Dark Red),
Service (Blue), Filing Code (Purple), and Neutral.
"""

import typing

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel


class TagWidget(QLabel):
    PALETTE: typing.ClassVar = {
        "submitted": {
            "bg": "#2e7d32", "fg": "#ffffff", "border": "#1b5e20", "label": "Submitted"
        },
        "pending": {
            "bg": "#d32f2f", "fg": "#ffffff", "border": "#b71c1c", "label": "Pending"
        },
        "in_progress": {
            "bg": "#f57f17", "fg": "#ffffff", "border": "#e65100", "label": "In-Progress"
        },
        "overdue": {
            "bg": "#880e4f", "fg": "#ffffff", "border": "#4a148c", "label": "OVERDUE"
        },
        "service": {
            "bg": "#1565c0", "fg": "#ffffff", "border": "#0d47a1", "label": "Service"
        },
        "filing": {
            "bg": "#6a1b9a", "fg": "#ffffff", "border": "#4a148c", "label": "Filing"
        },
        "neutral": {
            "bg": "#616161", "fg": "#ffffff", "border": "#424242", "label": "Info"
        }
    }

    def __init__(self, text: str | None = None, tag_type: str = "neutral", parent=None):
        super().__init__(parent)
        self.set_tag(text, tag_type)

    def set_tag(self, text: str | None = None, tag_type: str = "neutral"):
        tag_key = (tag_type or "neutral").lower()
        style_info = self.PALETTE.get(tag_key, self.PALETTE["neutral"])

        display_text = text if text is not None else style_info["label"]
        self.setText(f"  {display_text}  ")
        self.setAlignment(Qt.AlignCenter)

        bg = style_info["bg"]
        fg = style_info["fg"]
        border = style_info["border"]

        self.setStyleSheet(f"""
            QLabel {{
                background-color: {bg};
                color: {fg};
                border: 1px solid {border};
                border-radius: 4px;
                font-weight: bold;
                font-size: 11px;
                padding: 2px 6px;
            }}
        """)
