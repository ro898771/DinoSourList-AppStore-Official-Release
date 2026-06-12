"""
Styling constants and configurations for the Software Launcher Dashboard
"""

# Main Window Styles
MAIN_WINDOW_STYLE = """
    QMainWindow {
        background-color: #ffffff;
    }
    QWidget {
        background-color: #ffffff;
    }
    QScrollArea {
        border: none;
        background-color: #ffffff;
        border-radius: 8px;
    }
"""

# Software Card Styles
CARD_STYLE = """
    QFrame {
        background-color: #ffffff;
        border: 1px solid #e0e0e0;
        border-radius: 12px;
        padding: 15px;
    }
    QFrame:hover {
        background-color: #f8f9fa;
        border: 2px solid #007bff;
    }
    QLabel {
        background-color: transparent;
        font-size: 12px;
        font-weight: 500;
        color: #212529;
    }
"""

# Card Icon Styles
CARD_ICON_STYLE = """
    QLabel {
        border: 2px solid #e0e0e0;
        border-radius: 8px;
        padding: 4px;
        background-color: #ffffff;
    }
"""

CARD_ICON_FALLBACK_STYLE = "font-size: 64px; border: 2px solid #e0e0e0; border-radius: 8px;"

# Card Info Label Styles
CARD_INFO_STYLE = """
    QLabel {
        font-size: 11px;
        font-weight: normal;
        color: #000000;
        line-height: 1.4;
        border: none;
        background-color: transparent;
        padding: 5px;
    }
"""

# Version Label Styles
VERSION_LATEST_CONFIG = {
    "text": "Latest Software",
    "color": "#ffffff",  # White text
    "bg_color": "#10b981",  # Modern emerald green
    "hover_bg": "#059669",  # Darker emerald on hover
    "hover_color": "#ffffff"  # White text on hover
}

VERSION_OUTDATED_CONFIG = {
    "text": "Update",
    "color": "#ffffff",  # White text
    "bg_color": "#ef4444",  # Warm red
    "hover_bg": "#dc2626",  # Darker warm red on hover
    "hover_color": "#ffffff"  # White text on hover
}

def get_version_label_style(color, bg_color, hover_bg, hover_color):
    """Generate version label/button stylesheet"""
    return f"""
        QLabel, QPushButton {{
            font-size: 10px;
            font-weight: 600;
            color: {color};
            background-color: {bg_color};
            border: 2px solid {hover_bg};
            border-radius: 4px;
            padding: 0px 8px;
            margin: 0px;
        }}
        QLabel:hover, QPushButton:hover {{
            background-color: {hover_bg};
            color: {hover_color};
            border: 2px solid {hover_bg};
        }}
    """

# Title Styles
TITLE_STYLE = """
    font-size: 28px;
    font-weight: bold;
    color: #212529;
    padding: 10px;
    background-color: #ffffff;
"""

# Refresh Button Styles
REFRESH_BUTTON_STYLE = """
    QPushButton {
        background-color: #007bff;
        color: white;
        border: none;
        border-radius: 6px;
        padding: 10px 20px;
        font-size: 14px;
        font-weight: 500;
    }
    QPushButton:hover {
        background-color: #0056b3;
    }
    QPushButton:pressed {
        background-color: #004085;
    }
"""

# Status Label Styles
STATUS_LABEL_STYLE = """
    color: #000000;
    font-size: 13px;
    font-weight: bold;
    padding: 10px;
    background-color: #ffffff;
"""

# Message Box Styles
MESSAGE_BOX_STYLE = """
    QMessageBox {
        background-color: #ffffff;
    }
    QMessageBox QLabel {
        color: #000000;
        font-size: 12px;
    }
    QPushButton {
        background-color: #007bff;
        color: white;
        border: none;
        border-radius: 4px;
        padding: 6px 16px;
        font-size: 12px;
        min-width: 60px;
    }
    QPushButton:hover {
        background-color: #0056b3;
    }
"""

# Exit Dialog Styles
EXIT_DIALOG_STYLE = """
    QMessageBox {
        background-color: #ffffff;
    }
    QMessageBox QLabel {
        color: #000000;
        font-size: 13px;
    }
    QPushButton {
        background-color: #007bff;
        color: white;
        border: none;
        border-radius: 4px;
        padding: 6px 16px;
        font-size: 12px;
        min-width: 60px;
    }
    QPushButton:hover {
        background-color: #0056b3;
    }
"""

# Pagination Button Styles
PAGINATION_NAV_BUTTON_STYLE = """
    QPushButton {
        background-color: #007bff;
        color: white;
        border: none;
        border-radius: 6px;
        padding: 10px 20px;
        font-size: 14px;
        font-weight: 500;
        min-width: 100px;
    }
    QPushButton:hover {
        background-color: #0056b3;
    }
    QPushButton:pressed {
        background-color: #004085;
    }
    QPushButton:disabled {
        background-color: #6c757d;
        color: #adb5bd;
    }
"""

PAGINATION_PAGE_BUTTON_STYLE = """
    QPushButton {
        background-color: #ffffff;
        color: #007bff;
        border: 2px solid #007bff;
        border-radius: 6px;
        padding: 5px 8px;
        font-size: 14px;
        font-weight: 500;
        min-width: 35px;
        min-height: 35px;
    }
    QPushButton:hover {
        background-color: #e7f3ff;
    }
    QPushButton:pressed {
        background-color: #cfe2ff;
    }
"""

PAGINATION_PAGE_BUTTON_ACTIVE_STYLE = """
    QPushButton {
        background-color: #007bff;
        color: white;
        border: 2px solid #007bff;
        border-radius: 6px;
        padding: 5px 8px;
        font-size: 14px;
        font-weight: bold;
        min-width: 35px;
        min-height: 35px;
    }
    QPushButton:hover {
        background-color: #0056b3;
    }
"""

PAGINATION_LABEL_STYLE = """
    QLabel {
        color: #6c757d;
        font-size: 14px;
        font-weight: 500;
        padding: 0px 10px;
    }
"""

# Scroll Bar Styles
SCROLL_BAR_STYLE = """
    QScrollArea {
        border: none;
        background-color: transparent;
    }
    QScrollBar:vertical {
        background-color: #f1f3f5;
        width: 12px;
        border-radius: 6px;
        margin: 0px;
    }
    QScrollBar::handle:vertical {
        background-color: #adb5bd;
        border-radius: 6px;
        min-height: 30px;
    }
    QScrollBar::handle:vertical:hover {
        background-color: #868e96;
    }
    QScrollBar::handle:vertical:pressed {
        background-color: #495057;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
        height: 0px;
    }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
        background: none;
    }
    QScrollBar:horizontal {
        background-color: #f1f3f5;
        height: 12px;
        border-radius: 6px;
        margin: 0px;
    }
    QScrollBar::handle:horizontal {
        background-color: #adb5bd;
        border-radius: 6px;
        min-width: 30px;
    }
    QScrollBar::handle:horizontal:hover {
        background-color: #868e96;
    }
    QScrollBar::handle:horizontal:pressed {
        background-color: #495057;
    }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
        width: 0px;
    }
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
        background: none;
    }
"""

# Form Container Styles
FORM_CONTAINER_STYLE = """
    QFrame {
        background-color: white;
        border: 2px solid #d0d7de;
        border-radius: 8px;
        padding: 30px;
    }
"""

FORM_CONTAINER_NO_BORDER_STYLE = """
    QFrame {
        background-color: white;
        border: 2px solid #d0d7de;
        border-radius: 8px;
        padding: 30px;
    }
"""

# Form Dimensions
FORM_MIN_WIDTH = 600
FORM_MAX_WIDTH = 650
FORM_SPACING = 15
FORM_MARGINS = (20, 20, 20, 20)  # left, top, right, bottom

# Form Title Styles
USER_TITLE_STYLE = """
    QLabel {
        font-size: 24px;
         border: none;
        font-weight: bold;
        color: #007bff;
        padding-bottom: 10px;
    }
"""

DEVELOPER_TITLE_STYLE = """
    QLabel {
        font-size: 24px;
        font-weight: bold;
        border: none;
        color: #28a745;
        padding-bottom: 10px;
    }
"""

STATUS_CONSOLE_TITLE_STYLE = """
    QLabel {
        font-size: 24px;
        font-weight: bold;
        color: #fd7e14;
        padding-bottom: 10px;
    }
"""

# Form Label Styles
FORM_LABEL_STYLE = """
    font-size: 12px;
    border: none;
    font-weight: bold;
    color: #24292f;
"""

# User Form Dimensions
USER_LABEL_WIDTH = 100
USER_INPUT_WIDTH = 350

# Developer Form Dimensions
DEV_LABEL_WIDTH = 130
DEV_INPUT_WIDTH = 320

# Input Field Styles
INPUT_FIELD_STYLE = """
    QLineEdit {
        padding: 8px;
        border: none;
        border-bottom: 2px solid #d0d7de;
        border-radius: 0px;
        font-size: 13px;
        background-color: transparent;
        color: #24292f;
    }
    QLineEdit:focus {
        border-bottom: 2px solid #007bff;
        outline: none;
    }
"""

INPUT_FIELD_STYLE_GREEN = """
    QLineEdit {
        padding: 8px;
        border: none;
        border-bottom: 2px solid #d0d7de;
        border-radius: 0px;
        font-size: 13px;
        background-color: transparent;
        color: #24292f;
    }
    QLineEdit:focus {
        border-bottom: 2px solid #28a745;
        outline: none;
    }
"""

# ComboBox Styles
COMBOBOX_STYLE = """
    QComboBox {
        padding: 2px 8px;
        border: 1px solid #ced4da;
        border-radius: 4px;
        font-size: 13px;
        background-color: #ffffff;
        color: #24292f;
    }
    QComboBox:focus {
        border: 1px solid #007bff;
    }
    QComboBox:hover {
        border: 1px solid #007bff;
    }
    QComboBox::drop-down {
        border: none;
        padding-right: 10px;
    }
    QComboBox::down-arrow {
        image: none;
        border-left: 5px solid transparent;
        border-right: 5px solid transparent;
        border-top: 5px solid #24292f;
        margin-right: 5px;
    }
    QComboBox QAbstractItemView {
        color: #24292f;
        background-color: #ffffff;
        selection-background-color: #007bff;
        selection-color: #ffffff;
    }
"""

# Button Styles
BUTTON_GREEN_STYLE = """
    QPushButton {
        background-color: #28a745;
        color: white;
        border: none;
        border-radius: 6px;
        padding: 8px 20px;
        font-size: 13px;
        font-weight: bold;
    }
    QPushButton:hover {
        background-color: #218838;
    }
    QPushButton:pressed {
        background-color: #1e7e34;
    }
"""

BUTTON_BLUE_STYLE = """
    QPushButton {
        background-color: #007bff;
        color: white;
        border: none;
        border-radius: 6px;
        padding: 8px 20px;
        font-size: 13px;
        font-weight: bold;
    }
    QPushButton:hover {
        background-color: #0056b3;
    }
    QPushButton:pressed {
        background-color: #004085;
    }
"""

BUTTON_RED_STYLE = """
    QPushButton {
        background-color: #dc3545;
        color: white;
        border: none;
        border-radius: 6px;
        padding: 8px 20px;
        font-size: 13px;
        font-weight: bold;
    }
    QPushButton:hover {
        background-color: #c82333;
    }
    QPushButton:pressed {
        background-color: #bd2130;
    }
"""

BUTTON_PURPLE_STYLE = """
    QPushButton {
        background-color: #6f42c1;
        color: white;
        border: none;
        border-radius: 6px;
        padding: 8px 20px;
        font-size: 13px;
        font-weight: bold;
    }
    QPushButton:hover {
        background-color: #5a32a3;
    }
    QPushButton:pressed {
        background-color: #4c2a8a;
    }
"""

BUTTON_GRAY_STYLE = """
    QPushButton {
        background-color: #6c757d;
        color: white;
        border: none;
        border-radius: 6px;
        padding: 8px 20px;
        font-size: 13px;
        font-weight: bold;
    }
    QPushButton:hover {
        background-color: #5a6268;
    }
    QPushButton:pressed {
        background-color: #545b62;
    }
"""

# User Button Styles (smaller padding)
USER_BUTTON_GREEN_STYLE = """
    QPushButton {
        background-color: #28a745;
        color: white;
        border: none;
        border-radius: 6px;
        padding: 8px 15px;
        font-size: 13px;
        font-weight: bold;
    }
    QPushButton:hover {
        background-color: #218838;
    }
    QPushButton:pressed {
        background-color: #1e7e34;
    }
"""

USER_BUTTON_BLUE_STYLE = """
    QPushButton {
        background-color: #007bff;
        color: white;
        border: none;
        border-radius: 6px;
        padding: 8px 15px;
        font-size: 13px;
        font-weight: bold;
    }
    QPushButton:hover {
        background-color: #0056b3;
    }
    QPushButton:pressed {
        background-color: #004085;
    }
"""

USER_BUTTON_RED_STYLE = """
    QPushButton {
        background-color: #dc3545;
        color: white;
        border: none;
        border-radius: 6px;
        padding: 8px 15px;
        font-size: 13px;
        font-weight: bold;
    }
    QPushButton:hover {
        background-color: #c82333;
    }
    QPushButton:pressed {
        background-color: #bd2130;
    }
"""

# Status Label Styles
USER_STATUS_LABEL_DEFAULT = """
    QLabel {
        font-size: 12px;
        color: #6c757d;
        padding: 12px;
        background-color: #f8f9fa;
        border-radius: 6px;
        border: 1px solid #dee2e6;
    }
"""

USER_STATUS_LABEL_REGISTERED = """
    QLabel {
        font-size: 12px;
        color: #28a745;
        padding: 12px;
        background-color: #d4edda;
        border-radius: 6px;
        border: 1px solid #c3e6cb;
    }
"""

USER_STATUS_LABEL_LOGGED_IN = """
    QLabel {
        font-size: 12px;
        color: #155724;
        padding: 12px;
        background-color: #d4edda;
        border-radius: 6px;
        border: 1px solid #c3e6cb;
    }
"""

USER_STATUS_LABEL_LOGGED_OUT = """
    QLabel {
        font-size: 12px;
        color: #856404;
        padding: 12px;
        background-color: #fff3cd;
        border-radius: 6px;
        border: 1px solid #ffeeba;
    }
"""

# Status Console Styles
STATUS_CONSOLE_TEXT_STYLE = """
    QTextEdit {
        padding: 10px;
        border: 1px solid #d0d7de;
        border-radius: 6px;
        font-size: 12px;
        font-family: 'Consolas', 'Courier New', monospace;
        background-color: #f8f9fa;
        color: #24292f;
        line-height: 1.5;
    }
"""

STATUS_CONSOLE_MIN_HEIGHT = 400
