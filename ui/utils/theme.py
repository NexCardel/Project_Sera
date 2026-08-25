"""
theme.py
--------
Modern QSS Design System for Project Sera.
"""

from PySide6.QtWidgets import QTableWidgetItem
from PySide6.QtCore import Qt

class SmartTableWidgetItem(QTableWidgetItem):
    """
    QTableWidgetItem with natural numeric and text sorting.
    Numbers sort numerically (1, 2, 10, 100), strings sort alphabetically.
    """
    def __lt__(self, other):
        if not isinstance(other, QTableWidgetItem):
            return super().__lt__(other)
            
        t1 = self.text().strip()
        t2 = other.text().strip()

        # Try float/int comparison for leading numeric tokens (e.g. "1001 (Viewed • 5m ago)")
        import re
        m1 = re.match(r'^([+-]?\d+(?:\.\d+)?)\b', t1)
        m2 = re.match(r'^([+-]?\d+(?:\.\d+)?)\b', t2)
        if m1 and m2:
            try:
                n1, n2 = float(m1.group(1)), float(m2.group(1))
                if n1 != n2:
                    return n1 < n2
            except ValueError:
                pass

        try:
            return float(t1) < float(t2)
        except ValueError:
            pass

        return t1.lower() < t2.lower()

LIGHT_STYLESHEET = """

QMainWindow, QWidget {
    background-color: #202020;
    color: #F8FAFC;
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    font-size: 13px;
}

/* ==========================================================================
   Shared 5 Core Primitives (Sera UI/UX Redesign System)
   ========================================================================== */

/* 1. SectionLabel: unboxed, muted, uppercase, letter-spaced section header */
QLabel[class="SectionLabel"], QLabel#SectionLabel {
    color: #8E8D88;
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    background: transparent;
    padding: 4px 0 2px 0;
}

/* 2. Row: grouped list row with hairline bottom divider */
QWidget[class="Row"], QWidget#Row, QFrame[class="Row"], QFrame#Row {
    background: transparent;
    border: none;
    border-bottom: 0.5px solid #232323;
}
QLabel[class="RowLabel"], QLabel#RowLabel {
    color: #8E8D88;
    font-size: 12px;
    font-weight: 500;
    background: transparent;
}
QLabel[class="RowValue"], QLabel#RowValue {
    color: #F8FAFC;
    font-size: 13.5px;
    font-weight: 500;
    background: transparent;
}

/* 3. Divider: hairline section separator */
QFrame[class="Divider"], QFrame#Divider {
    border: none;
    border-top: 0.5px solid #2A2A2A;
    min-height: 1px;
    max-height: 1px;
    margin: 8px 0;
}

/* 4. GhostIconButton: borderless neutral action button, brightens on hover */
QPushButton[class="GhostIconButton"], QPushButton#GhostIconButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 4px;
    color: #8E8D88;
}
QPushButton[class="GhostIconButton"]:hover, QPushButton#GhostIconButton:hover {
    background-color: #262626;
    border-color: #333333;
    color: #FFFFFF;
}
QPushButton[class="GhostIconButton"]:pressed, QPushButton#GhostIconButton:pressed {
    background-color: #1A1A1A;
}

/* 5. Badge: standardized rounded pill token */
QLabel[class="Badge"], QLabel#Badge {
    background-color: rgba(46, 155, 95, 0.18);
    color: #4CF9B7;
    border: 1px solid rgba(76, 249, 183, 0.3);
    font-size: 11px;
    font-weight: 700;
    padding: 3px 9px;
    border-radius: 5px;
}
QLabel[class="Badge"]:hover, QLabel#Badge:hover {
    background-color: rgba(46, 155, 95, 0.28);
}

/* ==========================================================================
   Shared Application Surface Rules
   ========================================================================== */
QDialog {
    background-color: #202020;
    color: #F8FAFC;
}
QDialog QLabel[class="DialogTitle"] {
    color: #F8FAFC;
    font-size: 17px;
    font-weight: 700;
}
QDialog QPushButton {
    min-height: 30px;
    border-radius: 7px;
    padding: 5px 12px;
    background-color: #171717;
    border: 1px solid #333333;
    color: #F8FAFC;
}
QDialog QPushButton:hover {
    background-color: #262626;
    border-color: #444444;
}
QDialog QGroupBox {
    background-color: #141414;
    border: 1px solid #262626;
    border-radius: 9px;
    padding: 12px 10px 8px 10px;
}
QDialog QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 5px;
    color: #2E9B5F;
    background-color: #141414;
    font-weight: 600;
}
QDialog#ToolDialog QTableWidget,
QWidget#ManageClientsPage QTableWidget,
QDialog QTableWidget {
    background-color: #FFFFFF;
    color: #241F1B;
    border: 1px solid #D8CDB4;
    border-radius: 8px;
    gridline-color: #D8CDB4;
}
QDialog#ToolDialog QLineEdit,
QDialog#ToolDialog QComboBox,
QDialog#ToolDialog QTextEdit,
QWidget#ManageClientsPage QLineEdit,
QWidget#ManageClientsPage QComboBox,
QWidget#ManageClientsPage QTextEdit {
    background-color: #171717;
    border: 1px solid #333333;
    border-radius: 7px;
    padding: 6px 9px;
    color: #F8FAFC;
}
QDialog#ToolDialog QHeaderView::section,
QWidget#ManageClientsPage QHeaderView::section,
QDialog QHeaderView::section {
    background-color: #141414;
    color: #FFFFFF;
    border: none;
    border-bottom: 1px solid #262626;
    border-right: 1px solid #262626;
    padding: 7px;
    font-weight: 600;
}
QDialog#ToolDialog QPushButton,
QWidget#ManageClientsPage QPushButton {
    border-radius: 7px;
    padding: 6px 12px;
}
QLabel[class="PageTitle"] { color: #F8FAFC; font-size: 20px; font-weight: 700; }
QLabel[class="SectionTitle"] { color: #F8FAFC; font-size: 15px; font-weight: 600; }
QFrame[class="PageSeparator"] { border: none; border-top: 0.5px solid #2A2A2A; min-height: 1px; max-height: 1px; }

/* Base panels */
QGroupBox, QListWidget, QTabWidget::pane {
    background-color: #141414;
    border: 1px solid #262626;
    border-radius: 8px;
    color: #F8FAFC;
}

QGroupBox {
    font-weight: 600;
    margin-top: 10px;
    padding-top: 14px;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 4px;
    color: #2E9B5F;
}

QLineEdit, QTextEdit, QComboBox, QSpinBox {
    background-color: #171717;
    border: 1px solid #333333;
    border-radius: 6px;
    padding: 8px 12px;
    color: #F8FAFC;
    selection-background-color: #2E9B5F;
    selection-color: #FFFFFF;
}

QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus {
    border: 1.5px solid #2E9B5F;
}

QPushButton {
    background-color: #171717;
    border: 1px solid #333333;
    border-radius: 6px;
    padding: 6px 14px;
    font-weight: 500;
    color: #F8FAFC;
}

QPushButton:hover {
    background-color: #262626;
    border-color: #555555;
}

QPushButton:pressed {
    background-color: #101010;
}

/* Primary CTA button class: btn.setProperty("class", "primary") */
QPushButton[class="primary"] {
    background-color: #2E9B5F;
    border-color: #34B76D;
    color: #FFFFFF;
    font-weight: bold;
}
QPushButton[class="primary"]:hover {
    background-color: #34B76D;
    border-color: #4CF9B7;
}

/* Destructive action button class: btn.setProperty("class", "danger") */
QPushButton[class="danger"] {
    background-color: #A82424;
    border-color: #C62828;
    color: #FFFFFF;
    font-weight: bold;
}
QPushButton[class="danger"]:hover {
    background-color: #C62828;
    border-color: #FF4D4D;
}

QTableWidget, QTableView, QTreeWidget {
    background-color: #FFFFFF;
    alternate-background-color: #FFFFFF;
    color: #241F1B;
    gridline-color: #D8CDB4;
    outline: none;
    border: none;
}

QTableWidget::item:selected, QTableView::item:selected, QTreeWidget::item:selected {
    background-color: #0078D7;
    color: #FFFFFF;
    font-weight: 600;
}

QTableWidget::item:focus, QTableView::item:focus, QTreeWidget::item:focus {
    border: none;
    outline: none;
}

QHeaderView::section {
    background-color: #141414;
    color: #FFFFFF;
    padding: 6px;
    font-weight: 600;
    border: none;
    border-bottom: 1px solid #262626;
    border-right: 1px solid #262626;
}

/* ==========================================================================
   Neutral Graphite Scrollbars (Recolored from overloaded red)
   ========================================================================== */
QScrollBar:vertical, QScrollBar:horizontal {
    border: none;
    background: #141414;
    width: 8px;
    height: 8px;
    border-radius: 4px;
}

QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: #3A3A3A;
    border: none;
    border-radius: 4px;
    min-height: 22px;
    min-width: 22px;
}

QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
    background: #4F4F4F;
}

QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {
    background: none;
    border: none;
}

QCornerWidget {
    background: #141414;
}

QTabBar::tab {
    background-color: #141414;
    color: #CBD5E1;
    border: 1px solid #262626;
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    padding: 6px 16px;
    margin-right: 2px;
}

QTabBar::tab:selected {
    background-color: #202020;
    color: #F8FAFC;
    font-weight: 600;
}

/* Dashboard Stat Cards */
QGroupBox[class="stat-card"] {
    background-color: #141414;
    border: 1px solid #262626;
    border-radius: 6px;
    margin-top: 0px;
    padding-top: 0px;
}
QLabel[class="stat-title"] {
    color: #8E8D88;
    font-size: 12px;
    font-weight: 600;
}
QLabel#stat_val {
    font-size: 22px;
    font-weight: 700;
}

/* Sidebar Specific Styles */
#Sidebar {
    background-color: #141414;
    border-right: 1px solid #1E1E1E;
}
/* Typography & Common */
QLabel[class="DialogTitle"] { font-size: 16px; font-weight: 600; }
QLabel[class="PageTitle"] { font-size: 20px; font-weight: 700; }
QLabel[class="InfoText"] { color: #CBD5E1; }
QLabel[class="HintText"] { color: #8E8D88; font-size: 11px; }
QLabel[class="GuidanceText"] { color: #CBD5E1; font-size: 12px; padding: 2px 0 6px 0; }
QLabel[class="NoDataLabel"] { color: #8E8D88; font-style: italic; padding: 20px; font-size: 13px; }
QLabel[class="SuccessText"] { color: #4CF9B7; font-weight: 600; }
QLabel[class="LargeIdentityText"] { font-size: 19px; font-weight: 700; }
QFrame[class="Separator"] { border-top: 0.5px solid #2A2A2A; margin: 8px 0; }

/* Client detail design system */
QGroupBox#ClientDetailCard {
    background-color: #141414;
    border: 1px solid #262626;
    border-radius: 10px;
    margin-top: 10px;
    padding: 10px;
}
QGroupBox#ClientDetailCard::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 5px;
    color: #2E9B5F;
    background-color: #141414;
    font-size: 13px;
    font-weight: 600;
}
QWidget#ClientDetailField {
    background-color: #171717;
    border: 1px solid #262626;
    border-radius: 7px;
}
QLabel#DetailFieldLabel { color: #8E8D88; font-size: 11px; background: transparent; }
QLabel#DetailFieldValue { color: #F8FAFC; font-size: 13.5px; font-weight: 500; background: transparent; }
QLabel#DetailSectionLabel { color: #F8FAFC; font-size: 13px; font-weight: 600; background: transparent; }
QLabel#DetailSecretValue { color: #F8FAFC; background: #171717; border-radius: 5px; padding: 5px 8px; }
QPushButton#DetailActionButton { background: #171717; border: 1px solid #333333; border-radius: 7px; padding: 5px 10px; color: #F8FAFC; }
QPushButton#DetailActionButton:hover { border-color: #2E9B5F; background: #262626; }
QTextEdit#DetailNotes { background: #171717; border: 1px solid #2C2C2C; border-radius: 8px; padding: 8px; color: #F8FAFC; }
QTextEdit#DetailNotes:focus { border-color: #2E9B5F; }

/* Toast & Sera Alert */
QFrame#ToastNotification, QFrame#SeraAlert { background-color: #171717; border-radius: 8px; padding: 10px 16px; border: 1px solid #333333; }
QFrame#SeraAlert[level="success"] { background-color: #1B382B; border: 1px solid #2E7D32; }
QFrame#SeraAlert[level="success"] QLabel { color: #A5D6A7; }
QFrame#SeraAlert[level="info"] { background-color: #1E2A4A; border: 1px solid #1565C0; }
QFrame#SeraAlert[level="info"] QLabel { color: #90CAF9; }
QFrame#SeraAlert[level="warning"] { background-color: #3E2B18; border: 1px solid #F57F17; }
QFrame#SeraAlert[level="warning"] QLabel { color: #FFE082; }
QFrame#SeraAlert[level="error"] { background-color: #3C191E; border: 1px solid #C62828; }
QFrame#SeraAlert[level="error"] QLabel { color: #EF9A9A; }
QLabel[class="ToastLabel"], QLabel[class="SeraAlertLabel"] { color: #F8FAFC; font-weight: 600; font-size: 13px; background: transparent; }

/* Sidebar */
QLabel#SidebarTitle { font-size: 14px; font-weight: 700; color: #FFFFFF; background: transparent; }
QLabel#SidebarSection { font-size: 12px; font-weight: 500; color: #8E8D88; margin: 0; background: transparent; }
QLabel#SidebarLogo { background: #164A68; border-radius: 5px; color: #FFFFFF; font-size: 11px; font-weight: 700; }
QLabel#SidebarProfile { background: #2E9B5F; border-radius: 13px; color: #FFFFFF; font-size: 18px; }
QFrame#SidebarDivider { border: none; border-top: 0.5px solid #222222; min-height: 1px; max-height: 1px; margin: 4px 0; }
QWidget#SidebarAccordionHeader { background: transparent; border-radius: 4px; }
QWidget#SidebarAccordionHeader:hover { background: #1E1E1E; }
QWidget#SidebarAccordionHeader[active="true"] { background: #1E1E1E; }
QPushButton#SidebarButton { min-height: 22px; text-align: left; padding: 5px 8px; font-size: 13px; border: none; border-radius: 4px; background: transparent; font-weight: 500; color: #8E8D88; }
QPushButton#SidebarButton:hover { background: #222222; color: #FFFFFF; }
QPushButton#SidebarSubButton { min-height: 20px; text-align: left; padding: 4px 8px 4px 34px; font-size: 12px; color: #8E8D88; border: none; border-radius: 4px; background: transparent; }
QPushButton#SidebarSubButton:hover { background: #222222; color: #FFFFFF; }
QPushButton#SidebarSubButton[active="true"] { background: #2E9B5F; color: #FFFFFF; font-weight: 600; }
QPushButton#SidebarCollapseButton { background: transparent; border: none; border-radius: 4px; padding: 3px; }
QPushButton#SidebarCollapseButton:hover { background-color: #222222; }
QPushButton#PageToggleSidebarButton { background: #171717; border: 1px solid #333333; border-radius: 6px; padding: 4px; }
QPushButton#PageToggleSidebarButton:hover { background-color: #262626; border-color: #2E9B5F; }

/* Slide Panel */
QFrame#SlidePanel { background-color: #141414; border-left: 1px solid #262626; }
QLabel[class="SlidePanelTitle"] { font-weight: bold; font-size: 18px; color: #F8FAFC; }
QPushButton[class="CloseButton"] { border: none; font-size: 16px; font-weight: bold; background: transparent; color: #8E8D88; }
QPushButton[class="CloseButton"]:hover { color: #FFFFFF; background: #262626; border-radius: 4px; }

/* Dialog Components */
QFrame[class="ServiceCard"] { background: #171717; border: 1px solid #262626; border-radius: 6px; }
QLabel[class="ClientName"] { font-size: 16px; font-weight: 700; color: #F8FAFC; }

/* Admin Window */
QFrame[class="ConflictBanner"] { background-color: #7f1d1d; border: 1px solid #dc2626; border-radius: 6px; }
QLabel[class="ConflictLabel"] { font-weight: 600; color: #fca5a5; }
"""

def get_theme_stylesheet(theme_name: str = "light") -> str:
    return LIGHT_STYLESHEET
