"""
Settings dialog. The top combo box picks a settings *category* (currently
just Refresh Setting) and swaps the page below it -- using a combo box
instead of a static title means adding a future category is just one more
_register_category(label, page_widget) call, no layout redesign.

Refresh Setting offers 4 alternatives to the default full Refresh so the
user can trade off speed vs. completeness. Each option's button is "Save":
it only persists that mode as the Refresh button's active behavior --
actually running it is left entirely to the Refresh button itself. The
Refresh button's label ("Refresh(N)") reflects whichever mode is saved.
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QWidget,
    QScrollArea, QStackedWidget,
)
from .checkable_combobox import CheckableComboBox, TickMarkDelegate, ArrowComboBox
from .styles import SCROLL_BAR_STYLE

# Neutral -- the 🔄 bullet already carries color, so the text itself stays
# the same plain dark grey as any other combo box in the app.
_CATEGORY_COMBOBOX_STYLE = """
    QComboBox {
        padding: 0px 32px 0px 14px;
        border: 1.5px solid #ced4da;
        border-radius: 8px;
        font-size: 15px;
        font-weight: 800;
        background-color: #ffffff;
        color: #24292f;
    }
    QComboBox:hover {
        border-color: #4338ca;
    }
    QComboBox::drop-down {
        width: 28px;
        border: none;
    }
    QComboBox::down-arrow {
        image: none;
        width: 0px;
        height: 0px;
    }
    QComboBox QAbstractItemView {
        background-color: #ffffff;
        border: 1px solid #e5e7eb;
        outline: none;
    }
"""

# Same neutral card regardless of active state -- only the badge label below
# calls out which option is active, not the whole card.
_OPTION_CARD_STYLE = """
    QFrame {
        background-color: #f9fafb;
        border: 1px solid #e5e7eb;
        border-radius: 10px;
    }
"""

_OPTION_TITLE_STYLE = "QLabel { font-size: 14px; font-weight: 700; color: #1f2937; }"
_OPTION_DESC_STYLE = "QLabel { font-size: 12px; color: #6b7280; }"
_ACTIVE_BADGE_STYLE = "QLabel { font-size: 11px; font-weight: 700; color: #16a34a; }"

_RUN_BUTTON_STYLE = """
    QPushButton {
        background-color: #4338ca;
        color: white;
        border: none;
        border-radius: 8px;
        padding: 8px 18px;
        font-size: 13px;
        font-weight: 700;
    }
    QPushButton:hover {
        background-color: #372aa8;
    }
    QPushButton:disabled {
        background-color: #c7c9f5;
        color: #eef0ff;
    }
"""

_CLOSE_BUTTON_STYLE = """
    QPushButton {
        background-color: #ffffff;
        color: #24292f;
        border: 1px solid #d0d7de;
        border-radius: 6px;
        padding: 6px 16px;
        font-size: 14px;
        font-weight: 500;
    }
    QPushButton:hover {
        background-color: #f6f8fa;
    }
"""


class SettingsDialog(QDialog):
    """App settings. Emits one signal per Refresh Setting action; the caller
    (MainWindow) owns what each action actually does -- including persisting
    it as the Refresh button's active mode. This dialog only reflects
    whichever mode is currently active (passed in via active_mode).
    """

    refresh_now_requested = Signal()
    refresh_select_requested = Signal(list)   # selected App_Store folder names
    refresh_no_readme_guide_requested = Signal()
    refresh_new_apps_requested = Signal()

    def __init__(self, app_names, parent=None, active_mode=1, active_selected_apps=None):
        super().__init__(parent)
        self.setWindowTitle("⚙️ Settings")
        self.resize(560, 560)
        self._active_mode = active_mode

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Category selector -- a combo box rather than a static "Settings"
        # title, so a future settings group only needs one more
        # _register_category() call instead of a layout redesign. It sits
        # directly in the header, replacing the static title entirely.
        self._stack = QStackedWidget()
        self._category_combo = ArrowComboBox()
        self._category_combo.setStyleSheet(_CATEGORY_COMBOBOX_STYLE)
        self._category_combo.setCursor(Qt.PointingHandCursor)
        # Same plain-label-left / checkmark-right dropdown style as the
        # Refresh Select combo, just single-select (checked == current row).
        self._category_combo.setItemDelegate(
            TickMarkDelegate(lambda idx: idx.row() == self._category_combo.currentIndex(), self._category_combo)
        )
        self._category_combo.view().setMouseTracking(True)
        self._category_combo.currentIndexChanged.connect(self._stack.setCurrentIndex)

        self._register_category(
            "🔄  Refresh Setting",
            self._build_refresh_setting_page(app_names, active_selected_apps or []),
        )
        # Future settings groups go here, e.g.:
        #   self._register_category("🎨  Display Setting", self._build_display_setting_page())

        layout.addWidget(self._build_header())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(SCROLL_BAR_STYLE)
        scroll.setWidget(self._stack)
        layout.addWidget(scroll, 1)

    def _register_category(self, label, page_widget):
        self._category_combo.addItem(label)
        self._stack.addWidget(page_widget)

    # ------------------------------------------------------------------
    def _build_header(self):
        header = QWidget()
        header.setStyleSheet("QWidget { background-color: #f6f8fa; border-bottom: 1px solid #d0d7de; }")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 15, 20, 15)

        header_layout.addWidget(self._category_combo, 1)

        close_btn = QPushButton("✕ Close")
        close_btn.setStyleSheet(_CLOSE_BUTTON_STYLE)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.close)
        header_layout.addWidget(close_btn)

        return header

    def _build_refresh_setting_page(self, app_names, active_selected_apps):
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(24, 4, 24, 20)
        page_layout.setSpacing(14)
        page_layout.setAlignment(Qt.AlignTop)

        hint_label = QLabel(
            "\"Save\" sets what the Refresh button runs next time -- click Save here, then use the Refresh button to actually run it."
        )
        hint_label.setStyleSheet(_OPTION_DESC_STYLE)
        hint_label.setWordWrap(True)
        page_layout.addWidget(hint_label)

        page_layout.addWidget(self._build_refresh_now_card())
        page_layout.addWidget(self._build_refresh_select_card(app_names, active_selected_apps))
        page_layout.addWidget(self._build_no_readme_guide_card())
        page_layout.addWidget(self._build_new_apps_card())

        return page

    def _option_card(self, mode, title, description):
        """A bordered card with a title/description on top; caller adds its
        own controls (combo box, Save button, ...) via the returned
        layout. The card itself always looks the same -- only the small
        green badge below marks which option is currently active.
        """
        is_active = mode == self._active_mode

        card = QFrame()
        card.setStyleSheet(_OPTION_CARD_STYLE)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(8)

        title_row = QHBoxLayout()
        title_label = QLabel(title)
        title_label.setStyleSheet(_OPTION_TITLE_STYLE)
        title_row.addWidget(title_label)
        title_row.addStretch()
        if is_active:
            badge = QLabel("✓ Currently Active")
            badge.setStyleSheet(_ACTIVE_BADGE_STYLE)
            title_row.addWidget(badge)
        card_layout.addLayout(title_row)

        desc_label = QLabel(description)
        desc_label.setStyleSheet(_OPTION_DESC_STYLE)
        desc_label.setWordWrap(True)
        card_layout.addWidget(desc_label)

        return card, card_layout

    def _run_button(self, text="Save"):
        btn = QPushButton(text)
        btn.setStyleSheet(_RUN_BUTTON_STYLE)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedWidth(110)
        return btn

    # ------------------------------------------------------------------
    def _build_refresh_now_card(self):
        card, card_layout = self._option_card(
            1, "1) Refresh Now",
            "Current behavior — icon + README + Guide, for every app in the store.",
        )
        row = QHBoxLayout()
        row.addStretch()
        run_btn = self._run_button()
        run_btn.clicked.connect(self._on_refresh_now_clicked)
        row.addWidget(run_btn)
        card_layout.addLayout(row)
        return card

    def _build_refresh_select_card(self, app_names, active_selected_apps):
        card, card_layout = self._option_card(
            2, "2) Refresh Select",
            "Current behavior (icon + README + Guide), but only for the app(s) you tick below. Tick \"Select All\" to cover every app through this mode instead.",
        )

        combo = CheckableComboBox(items=app_names, placeholder="Select app(s)...")
        if self._active_mode == 2 and active_selected_apps:
            combo.set_checked(active_selected_apps)
        card_layout.addWidget(combo)

        row = QHBoxLayout()
        row.addStretch()
        run_btn = self._run_button()
        run_btn.setEnabled(bool(combo.checked_items()))
        combo.selection_changed.connect(lambda checked: run_btn.setEnabled(bool(checked)))
        run_btn.clicked.connect(lambda: self._on_refresh_select_clicked(combo.checked_items()))
        row.addWidget(run_btn)
        card_layout.addLayout(row)
        return card

    def _build_no_readme_guide_card(self):
        card, card_layout = self._option_card(
            3, "3) Refresh without README + Guide",
            "Icon + Flow.txt + metadata only, for every app. Skips README and Guide downloads entirely — the fastest full refresh.",
        )
        row = QHBoxLayout()
        row.addStretch()
        run_btn = self._run_button()
        run_btn.clicked.connect(self._on_no_readme_guide_clicked)
        row.addWidget(run_btn)
        card_layout.addLayout(row)
        return card

    def _build_new_apps_card(self):
        card, card_layout = self._option_card(
            4, "4) Refresh for new uploaded tool only",
            "Checks Box for apps that aren't in App_Store yet, and only captures those — existing apps are left untouched.",
        )
        row = QHBoxLayout()
        row.addStretch()
        run_btn = self._run_button()
        run_btn.clicked.connect(self._on_new_apps_clicked)
        row.addWidget(run_btn)
        card_layout.addLayout(row)
        return card

    # ------------------------------------------------------------------
    def _on_refresh_now_clicked(self):
        self.refresh_now_requested.emit()
        self.close()

    def _on_refresh_select_clicked(self, selected_names):
        if not selected_names:
            return
        self.refresh_select_requested.emit(selected_names)
        self.close()

    def _on_no_readme_guide_clicked(self):
        self.refresh_no_readme_guide_requested.emit()
        self.close()

    def _on_new_apps_clicked(self):
        self.refresh_new_apps_requested.emit()
        self.close()
