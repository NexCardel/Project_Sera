"""
theme.py
--------
Modern QSS Design System for Project Sera (Light Mode & Dark Mode).
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

        # Try float/int comparison for numeric values
        try:
            return float(t1) < float(t2)
        except ValueError:
            pass

        return t1.lower() < t2.lower()

LIGHT_STYLESHEET = """

QMainWindow, QWidget {
    background-color: #F3ECDD;
    color: #241F1B;
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    font-size: 13px;
}

/* Shared application surface rules */
QDialog {
    background-color: #F3ECDD;
    color: #241F1B;
}
QDialog QLabel[class="DialogTitle"] {
    color: #241F1B;
    font-size: 18px;
    font-weight: 700;
}
QDialog QPushButton {
    min-height: 30px;
    border-radius: 7px;
    padding: 5px 12px;
}
QDialog QGroupBox {
    background-color: #EAE1CB;
    border: 1px solid #D8CDB4;
    border-radius: 9px;
    padding: 12px 10px 8px 10px;
}
QDialog QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 5px;
    color: #FF4D49;
    background-color: #EAE1CB;
    font-weight: 600;
}
QDialog#ToolDialog QTableWidget,
QWidget#ManageClientsPage QTableWidget {
    background-color: #FFFFFF;
    border: 1px solid #D8CDB4;
    border-radius: 8px;
    gridline-color: #E5DCC8;
}
QDialog#ToolDialog QLineEdit,
QDialog#ToolDialog QComboBox,
QDialog#ToolDialog QTextEdit,
QWidget#ManageClientsPage QLineEdit,
QWidget#ManageClientsPage QComboBox,
QWidget#ManageClientsPage QTextEdit {
    background-color: #FFFFFF;
    border: 1px solid #D8CDB4;
    border-radius: 7px;
    padding: 6px 9px;
}
QDialog#ToolDialog QHeaderView::section,
QWidget#ManageClientsPage QHeaderView::section {
    background-color: #EAE1CB;
    color: #241F1B;
    border: none;
    border-bottom: 1px solid #D8CDB4;
    padding: 7px;
    font-weight: 600;
}
QDialog#ToolDialog QPushButton,
QWidget#ManageClientsPage QPushButton {
    border-radius: 7px;
    padding: 6px 12px;
}
QLabel[class="PageTitle"] { color: #241F1B; font-size: 20px; font-weight: 700; }
QLabel[class="SectionTitle"] { color: #241F1B; font-size: 15px; font-weight: 600; }
QFrame[class="PageSeparator"] { border: none; border-top: 1px solid #D8CDB4; min-height: 1px; max-height: 1px; }

/* Base panels */
QGroupBox, QTableWidget, QListWidget, QTabWidget::pane {
    background-color: #EAE1CB;
    border: 1px solid #D8CDB4;
    border-radius: 8px;
    color: #241F1B;
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
    color: #217346;
}

QLineEdit, QTextEdit, QComboBox, QSpinBox {
    background-color: #FFFFFF;
    border: 1px solid #D8CDB4;
    border-radius: 6px;
    padding: 8px 12px;
    color: #241F1B;
    selection-background-color: #FF4D4D;
    selection-color: #FFFFFF;
}

QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus {
    border: 1.5px solid #FF4D4D;
}

QPushButton {
    background-color: #EAE1CB;
    border: 1px solid #D8CDB4;
    border-radius: 6px;
    padding: 6px 14px;
    font-weight: 500;
    color: #241F1B;
}

QPushButton:hover {
    background-color: #F3ECDD;
    border-color: #5C5347;
}

QPushButton:pressed {
    background-color: #D8CDB4;
}

/* Primary CTA button class we can use in code: btn.setProperty("class", "primary") */
QPushButton[class="primary"] {
    background-color: #FF4D4D;
    border-color: #FF4D4D;
    color: #FFFFFF;
    font-weight: bold;
}
QPushButton[class="primary"]:hover {
    background-color: #E63939;
}

QTableWidget, QListWidget {
    background-color: #FFFFFF;
    alternate-background-color: #FFFFFF;
    gridline-color: #D8CDB4;
    outline: none;
}

QTableWidget::item:selected, QListWidget::item:selected {
    background-color: #FF4D4D;
    color: #FFFFFF;
    font-weight: 600;
}

QTableWidget::item:focus, QListWidget::item:focus {
    border: 2px solid #241F1B;
}

QHeaderView::section {
    background-color: #EAE1CB;
    color: #5C5347;
    padding: 6px;
    font-weight: 600;
    border: none;
    border-bottom: 1px solid #D8CDB4;
}

QScrollBar:vertical {
    border: none;
    background: #EAE1CB;
    width: 8px;
    border-radius: 4px;
}

QScrollBar::handle:vertical {
    background: #D8CDB4;
    border-radius: 4px;
}

QScrollBar::handle:vertical:hover {
    background: #5C5347;
}

QTabBar::tab {
    background-color: #EAE1CB;
    color: #5C5347;
    border: 1px solid #D8CDB4;
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    padding: 6px 16px;
    margin-right: 2px;
}

QTabBar::tab:selected {
    background-color: #F3ECDD;
    color: #241F1B;
    font-weight: 600;
}

/* Dashboard Stat Cards */
QGroupBox[class="stat-card"] {
    background-color: #EAE1CB;
    border: 1px solid #D8CDB4;
    border-radius: 6px;
    margin-top: 0px;
    padding-top: 0px;
}
QLabel[class="stat-title"] {
    color: #5C5347;
    font-size: 12px;
    font-weight: 600;
}
QLabel#stat_val {
    font-size: 22px;
    font-weight: 700;
}

/* Sidebar Specific Styles */
#Sidebar {
    background-color: #2E9B5F;
}
/* Typography & Common */
QLabel[class="DialogTitle"] { font-size: 16px; font-weight: 600; }
QLabel[class="PageTitle"] { font-size: 20px; font-weight: 700; }
QLabel[class="InfoText"] { color: #555555; }
QLabel[class="HintText"] { color: #666666; font-size: 11px; }
QLabel[class="GuidanceText"] { color: #5C5347; font-size: 12px; padding: 2px 0 6px 0; }
QLabel[class="NoDataLabel"] { color: #777777; font-style: italic; padding: 20px; font-size: 13px; }
QLabel[class="SuccessText"] { color: #2e7d32; font-weight: 600; }
QLabel[class="LargeIdentityText"] { font-size: 20px; font-weight: 700; }
QFrame[class="Separator"] { border-top: 1px solid #D8CDB4; margin: 8px 0; }

/* Client detail design system */
QGroupBox#ClientDetailCard {
    background-color: #EAE1CB;
    border: 1px solid #D8CDB4;
    border-radius: 10px;
    margin-top: 14px;
    padding: 12px 10px 8px 10px;
}
QGroupBox#ClientDetailCard::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    padding: 0 6px;
    color: #2E9B5F;
    background-color: #EAE1CB;
    font-size: 14px;
    font-weight: 600;
}
QWidget#ClientDetailField {
    background-color: #F3ECDD;
    border: 1px solid #D8CDB4;
    border-radius: 7px;
}
QLabel#DetailFieldLabel { color: #5C5347; font-size: 11px; background: transparent; }
QLabel#DetailFieldValue { color: #241F1B; font-size: 14px; font-weight: 500; background: transparent; }
QLabel#DetailSectionLabel { color: #241F1B; font-size: 13px; font-weight: 600; background: transparent; }
QLabel#DetailSecretValue { color: #241F1B; background: #F3ECDD; border-radius: 5px; padding: 5px 8px; }
QPushButton#DetailActionButton { background: #EAE1CB; border: 1px solid #D8CDB4; border-radius: 7px; padding: 5px 10px; color: #241F1B; }
QPushButton#DetailActionButton:hover { border-color: #2E9B5F; background: #FFF8F1; }
QTextEdit#DetailNotes { background: #F3ECDD; border: 1px solid #D8CDB4; border-radius: 8px; padding: 7px; color: #241F1B; }

/* Toast & Sera Alert */
QFrame#ToastNotification, QFrame#SeraAlert { background-color: #241F1B; border-radius: 8px; padding: 10px 16px; border: 1px solid #5C5347; }
QFrame#SeraAlert[level="success"] { background-color: #E8F5E9; border: 1px solid #A5D6A7; }
QFrame#SeraAlert[level="success"] QLabel { color: #1B5E20; }
QFrame#SeraAlert[level="info"] { background-color: #E3F2FD; border: 1px solid #90CAF9; }
QFrame#SeraAlert[level="info"] QLabel { color: #0D47A1; }
QFrame#SeraAlert[level="warning"] { background-color: #FFF8E1; border: 1px solid #FFE082; }
QFrame#SeraAlert[level="warning"] QLabel { color: #E65100; }
QFrame#SeraAlert[level="error"] { background-color: #FFEBEE; border: 1px solid #EF9A9A; }
QFrame#SeraAlert[level="error"] QLabel { color: #B71C1C; }
QLabel[class="ToastLabel"], QLabel[class="SeraAlertLabel"] { color: #241F1B; font-weight: 600; font-size: 13px; background: transparent; }

/* Sidebar */
QLabel#SidebarTitle { font-size: 15px; font-weight: 700; color: #F8F5F2; background: transparent; }
QLabel#SidebarSection { font-size: 13px; font-weight: 500; color: #F8F5F2; margin: 0; background: transparent; }
QLabel#SidebarLogo { background: #164A68; border-radius: 5px; color: #F8F5F2; font-size: 11px; font-weight: 700; }
QLabel#SidebarProfile { background: #FF4D4D; border-radius: 13px; color: #FFFFFF; font-size: 18px; }
QFrame#SidebarDivider { border: none; border-top: 1px solid rgba(255, 255, 255, 120); min-height: 1px; max-height: 1px; margin: 2px 0; }
QWidget#SidebarAccordionHeader { background: transparent; border-radius: 4px; }
QWidget#SidebarAccordionHeader:hover { background: rgba(35, 121, 74, 90); }
QWidget#SidebarAccordionHeader[active="true"] { background: rgba(35, 121, 74, 110); }
QPushButton#SidebarButton { min-height: 21px; text-align: left; padding: 5px 8px; font-size: 13px; border: none; border-radius: 4px; background: transparent; font-weight: 500; color: #F8F5F2; }
QPushButton#SidebarButton:hover { background: rgba(35, 121, 74, 90); }
QPushButton#SidebarSubButton { min-height: 18px; text-align: left; padding: 3px 8px 3px 34px; font-size: 12px; color: #F8F5F2; border: none; border-radius: 4px; background: transparent; }
QPushButton#SidebarSubButton:hover { background: rgba(35, 121, 74, 90); }
QPushButton#SidebarSubButton[active="true"] { background: #FF4D4D; color: #FFFFFF; font-weight: 700; }

/* Slide Panel */
QFrame#SlidePanel { background-color: #EAE1CB; border-left: 1px solid #D8CDB4; }
QLabel[class="SlidePanelTitle"] { font-weight: bold; font-size: 18px; color: #5C5347; }
QPushButton[class="CloseButton"] { border: none; font-size: 16px; font-weight: bold; background: transparent; }
QPushButton[class="CloseButton"]:hover { color: #FF4D49; }

/* Dialog Components */
QFrame[class="ServiceCard"] { background: #F3ECDD; border: 1px solid #D8CDB4; border-radius: 6px; }
QLabel[class="ClientName"] { font-size: 16px; font-weight: 700; color: #241F1B; }

/* Admin Window */
QFrame[class="ConflictBanner"] { background-color: #ffebee; border: 1px solid #ef5350; border-radius: 6px; }
QLabel[class="ConflictLabel"] { font-weight: 600; color: #c62828; }
"""

DARK_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #292929;
    color: #f8fafc;
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    font-size: 13px;
}

QDialog { background-color: #292929; color: #f8fafc; }
QDialog QLabel[class="DialogTitle"] { color: #f8fafc; font-size: 18px; font-weight: 700; }
QDialog QPushButton { min-height: 30px; border-radius: 7px; padding: 5px 12px; }
QDialog QGroupBox { background-color: #333333; border: 1px solid #444444; border-radius: 9px; padding: 12px 10px 8px 10px; }
QDialog QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 12px; padding: 0 5px; color: #4CF9B7; background-color: #333333; font-weight: 600; }
QDialog#ToolDialog QTableWidget, QWidget#ManageClientsPage QTableWidget { background-color: #292929; border: 1px solid #444444; border-radius: 8px; gridline-color: #3d3d3d; }
QDialog#ToolDialog QLineEdit, QDialog#ToolDialog QComboBox, QDialog#ToolDialog QTextEdit, QWidget#ManageClientsPage QLineEdit, QWidget#ManageClientsPage QComboBox, QWidget#ManageClientsPage QTextEdit { background-color: #333333; border: 1px solid #444444; border-radius: 7px; padding: 6px 9px; }
QDialog#ToolDialog QHeaderView::section, QWidget#ManageClientsPage QHeaderView::section { background-color: #212121; color: #f8fafc; border: none; border-bottom: 1px solid #444444; padding: 7px; font-weight: 600; }
QDialog#ToolDialog QPushButton, QWidget#ManageClientsPage QPushButton { border-radius: 7px; padding: 6px 12px; }
QLabel[class="PageTitle"] { color: #f8fafc; font-size: 20px; font-weight: 700; }
QLabel[class="SectionTitle"] { color: #f8fafc; font-size: 15px; font-weight: 600; }
QFrame[class="PageSeparator"] { border: none; border-top: 1px solid #444444; min-height: 1px; max-height: 1px; }

QGroupBox {
    font-weight: 600;
    border: 1px solid #444444;
    border-radius: 8px;
    margin-top: 10px;
    padding-top: 14px;
    background-color: #333333;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 4px;
    color: #4CF9B7;
}

QLineEdit, QTextEdit, QComboBox, QSpinBox {
    background-color: #333333;
    border: 1px solid #444444;
    border-radius: 6px;
    padding: 6px 10px;
    color: #f8fafc;
    selection-background-color: #4CF9B7;
    selection-color: #292929;
}

QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus {
    border: 1.5px solid #4CF9B7;
}

QPushButton {
    background-color: #333333;
    border: 1px solid #444444;
    border-radius: 6px;
    padding: 6px 14px;
    font-weight: 500;
    color: #f8fafc;
}

QPushButton:hover {
    background-color: #3d3d3d;
    border-color: #555555;
}

QPushButton:pressed {
    background-color: #292929;
}

QPushButton[class="primary"] {
    background-color: #4CF9B7;
    border-color: #4CF9B7;
    color: #292929;
    font-weight: bold;
}
QPushButton[class="primary"]:hover {
    background-color: #3EE8A6;
}

QTableWidget, QListWidget {
    background-color: #292929;
    border: 1px solid #444444;
    border-radius: 8px;
    gridline-color: #3d3d3d;
    outline: none;
}

QTableWidget::item:selected, QListWidget::item:selected {
    background-color: #4CF9B7;
    color: #292929;
    font-weight: 600;
}

QTableWidget::item:focus, QListWidget::item:focus {
    border: 2px solid #4CF9B7;
    background-color: #333333;
}

QHeaderView::section {
    background-color: #212121;
    color: #d0d0d0;
    padding: 6px;
    font-weight: 600;
    border: none;
    border-bottom: 1px solid #444444;
}

QScrollBar:vertical {
    border: none;
    background: #292929;
    width: 8px;
    border-radius: 4px;
}

QScrollBar::handle:vertical {
    background: #444444;
    border-radius: 4px;
}

QScrollBar::handle:vertical:hover {
    background: #555555;
}

QTabWidget::pane {
    border: 1px solid #444444;
    border-radius: 4px;
    background-color: #333333;
}

QTabBar::tab {
    background-color: #212121;
    color: #d0d0d0;
    border: 1px solid #444444;
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    padding: 6px 16px;
    margin-right: 2px;
}

QTabBar::tab:selected {
    background-color: #333333;
    color: #4CF9B7;
    font-weight: 600;
}

/* Dashboard Stat Cards */
QGroupBox[class="stat-card"] {
    background-color: #333333;
    border: 1px solid #444444;
    border-radius: 6px;
    margin-top: 0px;
    padding-top: 0px;
}
QLabel[class="stat-title"] {
    color: #d0d0d0;
    font-size: 12px;
    font-weight: 600;
}
QLabel#stat_val {
    font-size: 22px;
    font-weight: 700;
}

/* Sidebar Specific Styles */
#Sidebar {
    background-color: #292929;
}

/* Typography & Common */
QLabel[class="DialogTitle"] { font-size: 16px; font-weight: 600; color: #f8fafc; }
QLabel[class="PageTitle"] { font-size: 20px; font-weight: 700; color: #f8fafc; }
QLabel[class="InfoText"] { color: #d0d0d0; }
QLabel[class="HintText"] { color: #a0a0a0; font-size: 11px; }
QLabel[class="GuidanceText"] { color: #d0d0d0; font-size: 12px; padding: 2px 0 6px 0; }
QLabel[class="NoDataLabel"] { color: #888888; font-style: italic; padding: 20px; font-size: 13px; }
QLabel[class="SuccessText"] { color: #4CF9B7; font-weight: 600; }
QLabel[class="LargeIdentityText"] { font-size: 20px; font-weight: 700; color: #f8fafc; }
QFrame[class="Separator"] { border-top: 1px solid #444444; margin: 8px 0; }

/* Toast & Sera Alert */
QFrame#ToastNotification, QFrame#SeraAlert { background-color: #212121; border-radius: 8px; padding: 10px 16px; border: 1px solid #444444; }
QFrame#SeraAlert[level="success"] { background-color: #1B382B; border: 1px solid #2E7D32; }
QFrame#SeraAlert[level="success"] QLabel { color: #A5D6A7; }
QFrame#SeraAlert[level="info"] { background-color: #1E2A4A; border: 1px solid #1565C0; }
QFrame#SeraAlert[level="info"] QLabel { color: #90CAF9; }
QFrame#SeraAlert[level="warning"] { background-color: #3E2B18; border: 1px solid #F57F17; }
QFrame#SeraAlert[level="warning"] QLabel { color: #FFE082; }
QFrame#SeraAlert[level="error"] { background-color: #3C191E; border: 1px solid #C62828; }
QFrame#SeraAlert[level="error"] QLabel { color: #EF9A9A; }
QLabel[class="ToastLabel"], QLabel[class="SeraAlertLabel"] { font-weight: 600; font-size: 13px; background: transparent; }

/* Sidebar */
QLabel#SidebarTitle { font-size: 15px; font-weight: 700; color: #f8fafc; background: transparent; }
QLabel#SidebarSection { font-size: 13px; font-weight: 500; color: #f8fafc; background: transparent; }
QPushButton#SidebarButton { text-align: left; padding: 5px 8px; font-size: 13px; border: none; border-radius: 4px; background: transparent; font-weight: 500; color: #f8fafc; }
QPushButton#SidebarButton:hover { background: #333333; }
QPushButton#SidebarSubButton { text-align: left; padding: 3px 8px 3px 34px; font-size: 12px; color: #cbd5e1; border: none; border-radius: 4px; background: transparent; }
QPushButton#SidebarSubButton:hover { background: #333333; }
QPushButton#SidebarSubButton[active="true"] { background: #4CF9B7; color: #292929; font-weight: 700; }

/* Slide Panel */
QFrame#SlidePanel { background-color: #333333; border-left: 1px solid #444444; }
QLabel[class="SlidePanelTitle"] { font-weight: bold; font-size: 18px; color: #f8fafc; }
QPushButton[class="CloseButton"] { border: none; font-size: 16px; font-weight: bold; background: transparent; color: #f8fafc; }
QPushButton[class="CloseButton"]:hover { color: #ef5350; }

/* Dialog Components */
QFrame[class="ServiceCard"] { background: #292929; border: 1px solid #444444; border-radius: 6px; }
QLabel[class="ClientName"] { font-size: 16px; font-weight: 700; color: #f8fafc; }

/* Admin Window */
QFrame[class="ConflictBanner"] { background-color: #7f1d1d; border: 1px solid #dc2626; border-radius: 6px; }
QLabel[class="ConflictLabel"] { font-weight: 600; color: #fca5a5; }
"""

def get_theme_stylesheet(theme_name: str) -> str:
    if theme_name == "dark":
        return DARK_STYLESHEET
    return LIGHT_STYLESHEET
