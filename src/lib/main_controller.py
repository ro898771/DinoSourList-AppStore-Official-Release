"""
MainWindow - Main application window controller
"""

import os
import sys
import json
import webbrowser
from pathlib import Path
from threading import Thread
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                                QLabel, QPushButton, QScrollArea, QGridLayout,
                                QMessageBox, QTextBrowser, QDialog, QApplication,
                                QLineEdit, QComboBox, QFrame, QTextEdit,
                                QSpacerItem, QSizePolicy, QCheckBox)
from PySide6.QtCore import (Qt, QUrl, Signal, QObject, QTimer, QSize, QPointF,
                             QVariantAnimation, QEasingCurve)
from PySide6.QtGui import QFont, QMovie, QTextCursor, QPixmap, QPainter, QPen, QColor, QIcon

# Try to import WebEngineView for embedded browser
try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    WEBENGINE_AVAILABLE = True
except ImportError:
    WEBENGINE_AVAILABLE = False

# Try to import markdown for rendering
try:
    import markdown
    MARKDOWN_AVAILABLE = True
except ImportError:
    MARKDOWN_AVAILABLE = False

from .software_card import SoftwareCard
from .store_card import StoreCard
from .list_row import SoftwareListRow, StoreListRow, LIST_ROW_WIDTH
from .boxlink_api import BoxLinkAPI, is_dotnet_missing_error, DOTNET_DOWNLOAD_URL
from .app_controller import AppStoreDownloadWorker, SingleCardDownloadWorker
from .workers import WorkerSignals, RefreshWorker, SingleCardRefreshWorker, CheckWorker, DownloadInstallWorker, DeleteWorker
from .readme_viewer import ReadmeViewer
from .styles import (
    MAIN_WINDOW_STYLE, TITLE_STYLE, REFRESH_BUTTON_STYLE,
    STATUS_LABEL_STYLE, MESSAGE_BOX_STYLE, EXIT_DIALOG_STYLE,
    SIDEBAR_STYLE, SIDEBAR_TITLE_STYLE, SIDEBAR_ITEM_STYLE, SIDEBAR_ITEM_ACTIVE_STYLE,
    SIDEBAR_ITEM_STYLE_COLLAPSED, SIDEBAR_ITEM_ACTIVE_STYLE_COLLAPSED,
    SIDEBAR_INFO_STYLE, SIDEBAR_LOGOUT_STYLE,
    SCROLL_BAR_STYLE, COMBOBOX_STYLE, get_version_label_style
)
from .clickable_label import ClickableLabel


class _LLMWorker(QObject):
    """Background worker that owns a DinosaurVectorBot instance.

    All heavy work (model loading, inference) is done in a plain Python thread
    so the Qt event loop — and therefore the UI — stays responsive.
    Signals are emitted from the background thread; Qt automatically queues
    them for delivery on the main thread.
    """

    # Emitted during initialisation
    init_done   = Signal()
    # Emitted on each streamed token fragment (main → background direction is via run())
    token_ready = Signal(str)
    # Emitted when a full response is available
    reply_done  = Signal(str, bool)   # (full_text, not_found)
    # Emitted when a re-index completes
    reload_done = Signal(str)         # message describing result
    # Emitted on any error
    error       = Signal(str)

    def __init__(self):
        super().__init__()
        self.assistant = None
        self._ready    = False

    # ── Called in background thread ──────────────────────────────────────────
    def do_init(self):
        try:
            from .LLMController import DinosaurVectorBot
            self.assistant = DinosaurVectorBot()   # paths auto-resolved to App_Store/
            self.assistant.load_and_index()        # build TF-IDF index from App_Store READMEs
            self._ready = True
            self.init_done.emit()
        except Exception as exc:
            self.error.emit(f"Initialisation failed: {exc}")

    def do_query(self, user_query: str):
        try:
            text, not_found = self.assistant.query(
                user_query,
                on_token=lambda t: self.token_ready.emit(t),
            )
            self.reply_done.emit(text, not_found)
        except Exception as exc:
            self.error.emit(f"Query failed: {exc}")

    def do_reload(self):
        """Re-scan App_Store READMEs and rebuild the TF-IDF index."""
        try:
            self.assistant.reload_and_index()
            count = len(self.assistant.chunks)
            self.reload_done.emit(f"Re-indexed {count} tool(s) from App_Store.")
        except Exception as exc:
            self.reload_done.emit(f"Reload failed: {exc}")

    @property
    def ready(self) -> bool:
        return self._ready


class MainWindow(QMainWindow):
    """Main application window with modern Bootstrap-style interface"""
    
    def __init__(self):
        super().__init__()
        self.app_version_info = self._load_app_version()
        self.setWindowTitle(f"{self.app_version_info['app_name']}-{self.app_version_info['version']}")
        _ico = Path(__file__).parent.parent.parent / "IcoFolder" / "main.ico"
        if _ico.exists():
            from PySide6.QtGui import QIcon
            self.setWindowIcon(QIcon(str(_ico)))
        # Launch with 3 columns (sidebar expanded) -- fits comfortably on
        # smaller laptop screens. Collapsing the sidebar (see _toggle_sidebar)
        # switches to 4 columns and grows the window to match.
        self.resize(self._ideal_window_width(3, 220), 1000)
        
        # Apply modern styles
        self.setStyleSheet(MAIN_WINDOW_STYLE)
        
        # Pagination settings
        self.current_page = 0
        self.total_pages = 4
        self.cards_per_page = 8  # 2 rows x 4 columns
        self.all_software_data = []  # Store all software data
        self.card_references = {}  # Store card references by folder path
        self.store_card_references = {}     # folder_name → StoreCard  (Page 2)
        self.dashboard_folder_name_map = {} # folder_name → folder_path str (Page 1)
        self._store_data_cache = None       # cached result of _load_store_software(); None = stale
        self._llm_worker = _LLMWorker()     # worker created at startup; init deferred until dialog opens
        self._busy_count = 0                # >0 while a refresh/download/install is in flight
        self._status_owner_token = 0        # bumped by any user action that should pre-empt an in-flight async op's status text
        self._ai_dialog = None              # AI Assistant dialog instance (kept non-modal)
        self._list_view_enabled = False     # Dashboard/Store card grid: False = icon grid, True = single-column list

        # Page names
        self.page_names = [
            "Dashboard",
            "Store",
            "Useful Links",
            "News"
        ]
        
        # Central widget: sidebar (left) + main content (right)
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Left navigation sidebar (replaces the old bottom pagination bar)
        self._setup_sidebar(root_layout, central)

        # Main content area
        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)
        root_layout.addWidget(content_widget, 1)

        # content_widget is created (and thus stacked) *after* the floating
        # toggle button, so without this it silently paints over the button's
        # right half -- invisible exactly where the ">" chevron's tip sits
        # when collapsed, which is why only that direction looked broken.
        self.sidebar_toggle_btn.raise_()

        # Header with title and loading indicator
        header_layout = QHBoxLayout()
        header_layout.setSpacing(10)
        
        # Title (dynamic based on page)
        self.title_label = QLabel("🚀 Software Dashboard")
        self.title_label.setStyleSheet(TITLE_STYLE)
        header_layout.addWidget(self.title_label)
        
        # Spacer to push right-side items to the edge
        header_layout.addStretch()

        # Loading GIF indicator (top right)
        self.loading_label = QLabel()
        self.loading_label.setFixedSize(120, 80)
        self.loading_label.setAlignment(Qt.AlignCenter)
        
        # Load the GIF
        gif_path = Path(__file__).parent.parent.parent / "Sw-icon" / "loading.gif"
        if gif_path.exists():
            self.loading_movie = QMovie(str(gif_path))
            # Scale the movie to fit the label size with smooth transformation
            from PySide6.QtCore import QSize
            self.loading_movie.setScaledSize(QSize(120, 80))
            self.loading_label.setMovie(self.loading_movie)
        else:
            # Fallback if GIF not found
            self.loading_label.setText("⏳")
            self.loading_label.setStyleSheet("""
                QLabel {
                    font-size: 30px;
                    color: #007bff;
                }
            """)
        
        self.loading_label.hide()  # Hidden by default
        header_layout.addWidget(self.loading_label)
        
        layout.addLayout(header_layout)
        
        # Controls row: filter box
        controls_row = QHBoxLayout()
        controls_row.setSpacing(10)
        controls_row.setContentsMargins(0, 0, 0, 0)

        # Filter text box — visible on Page 2 (Store) only
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("🔍  Search software by name…")
        self.filter_edit.setMaximumWidth(320)
        self.filter_edit.setFixedHeight(30)
        self.filter_edit.setStyleSheet("""
            QLineEdit {
                background-color: #ffffff;
                color: #212529;
                border: 1.5px solid #ced4da;
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 13px;
            }
            QLineEdit:focus {
                border-color: #0d6efd;
                background-color: #f8f9ff;
            }
        """)
        self.filter_edit.textChanged.connect(self._on_filter_changed)
        controls_row.addWidget(self.filter_edit)

        # Spacer to push the Refresh button to the right, sharing this row's
        # Y-axis with the filter box instead of sitting up in the header.
        controls_row.addStretch()

        # List View checkbox -- checked stacks Dashboard/Store cards into a
        # single column (see _current_card_columns); unchecked returns to the
        # multi-column icon grid. Visible on Page 1/2 only (same pages as
        # Refresh/filter -- see _update_refresh_button_visibility).
        self.list_view_checkbox = QCheckBox("☰  List View")
        self.list_view_checkbox.setCursor(Qt.PointingHandCursor)
        self.list_view_checkbox.setStyleSheet("""
            QCheckBox {
                font-size: 13px;
                font-weight: 600;
                color: #495057;
                spacing: 6px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
            }
        """)
        self.list_view_checkbox.toggled.connect(self._on_list_view_toggled)
        controls_row.addWidget(self.list_view_checkbox)
        controls_row.addSpacing(12)

        # Refresh button -- aligned above the last (4th) card of the grid below,
        # not just floated to the window's edge.
        self.refresh_btn = QPushButton("⟳  Refresh")
        self.refresh_btn.setMaximumWidth(120)
        self.refresh_btn.setStyleSheet(REFRESH_BUTTON_STYLE)
        self.refresh_btn.clicked.connect(self.refresh_data)
        controls_row.addWidget(self.refresh_btn)

        # Trailing gap, sized by _sync_refresh_button_alignment() (called once
        # below and again on every resizeEvent/sidebar toggle) rather than a
        # fixed constant -- the grid is left-aligned so its position never
        # moves, but this button is positioned via the addStretch() above,
        # which DOES move with the window's width. A static gap would only
        # have been correct at one exact window size.
        self._refresh_align_spacer = QSpacerItem(0, 0, QSizePolicy.Fixed, QSizePolicy.Minimum)
        controls_row.addItem(self._refresh_align_spacer)
        self._refresh_align_layout = controls_row

        layout.addLayout(controls_row)
        
        # Scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(SCROLL_BAR_STYLE)
        self.cards_scroll_area = scroll
        self.cards_container = QWidget()
        self.cards_layout = QGridLayout(self.cards_container)
        self.cards_layout.setSpacing(25)
        self.cards_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        # 0 left/right margin -- lines up the first card's left edge with the
        # header/filter box's left edge (both otherwise governed solely by
        # content_widget's own 20px margin). Small top margin keeps a bit of
        # breathing room below the filter box without reintroducing a big gap.
        self.cards_layout.setContentsMargins(0, 8, 0, 20)
        scroll.setWidget(self.cards_container)

        # A-Z jump bar -- List View only. Each letter scrolls the list to the
        # first row at/after that letter (see _jump_to_letter); items are
        # sorted alphabetically in List View so this doubles as the numbering
        # order for each row's sequence number.
        scroll_row = QHBoxLayout()
        scroll_row.setSpacing(4)
        scroll_row.addWidget(scroll, 1)

        self.az_bar_widget = QWidget()
        az_bar_layout = QVBoxLayout(self.az_bar_widget)
        az_bar_layout.setContentsMargins(2, 4, 2, 4)
        az_bar_layout.setSpacing(0)
        az_bar_layout.setAlignment(Qt.AlignTop)
        self._az_default_style = """
            QLabel {
                font-size: 10px;
                font-weight: 700;
                color: #868e96;
                background-color: transparent;
                border: none;
            }
            QLabel:hover {
                color: #0d6efd;
            }
        """
        self._az_active_style = """
            QLabel {
                font-size: 11px;
                font-weight: 800;
                color: #0d6efd;
                background-color: #eef0ff;
                border-radius: 4px;
                border: none;
            }
        """
        self._az_letter_labels = {}
        import string
        for letter in string.ascii_uppercase:
            letter_label = ClickableLabel(letter)
            letter_label.setAlignment(Qt.AlignCenter)
            letter_label.setFixedSize(20, 14)
            letter_label.setCursor(Qt.PointingHandCursor)
            letter_label.setStyleSheet(self._az_default_style)
            letter_label.clicked.connect(lambda checked=False, ltr=letter: self._jump_to_letter(ltr))
            az_bar_layout.addWidget(letter_label)
            self._az_letter_labels[letter] = letter_label
        self.az_bar_widget.setVisible(False)
        scroll_row.addWidget(self.az_bar_widget)

        layout.addLayout(scroll_row)
        scroll.verticalScrollBar().valueChanged.connect(self._on_list_scrolled)

        # Status - Black text
        self.status_label = QLabel("")
        self.status_label.setStyleSheet(STATUS_LABEL_STYLE)
        # Selectable so the user can copy the text (e.g. an error message) for debugging
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status_label.setCursor(Qt.IBeamCursor)
        layout.addWidget(self.status_label)
        
        # Load software
        self.software_path = Path(__file__).parent.parent.parent / "Software_Downloaded"
        self.config_path = Path(__file__).parent.parent.parent / "config-record"
        self.record_file = self.config_path / "record.json"
        self.load_software()
        # Pre-warm the Store page's data cache now, before the window is shown,
        # so the first navigation to the Store tab doesn't pay for the App_Store
        # scan — it just reads the already-populated cache.
        self.sidebar_store_count_label.setText(f"🏪  Software Store: {len(self._load_store_software())}")
        self._sync_installed_tools("launch")
        self._register_app_user()
        # Apply correct visibility and placeholder for the initial page
        self._update_refresh_button_visibility()
        # Set the Refresh button's initial alignment gap (resizeEvent handles
        # every change after this, including maximize/restore/manual resize).
        self._sync_refresh_button_alignment()

    def resizeEvent(self, event):
        """Keep the Refresh button aligned with the grid's last card on any
        resize (maximize, restore from minimized, manual drag-resize)."""
        super().resizeEvent(event)
        self._sync_refresh_button_alignment()

    def _current_card_columns(self):
        """Number of card-grid columns for the Dashboard/Store pages.

        Fewer columns (3) while the sidebar is expanded, so the grid fits
        comfortably on smaller laptop screens; collapsing the sidebar to an
        icon-only rail frees enough width for a 4th column. Forced to 1 when
        List View is checked, which stacks every card into a single column --
        every grid-packing loop reads this, so flipping it here is all that's
        needed to switch views.
        """
        if getattr(self, "_list_view_enabled", False):
            return 1
        return 4 if getattr(self, "_sidebar_collapsed", False) else 3

    def _ideal_window_width(self, num_cols, sidebar_width):
        """Window width that comfortably fits *num_cols* card columns next to
        a sidebar of *sidebar_width*, with room for the scrollbar and a little
        breathing room on the right (see _sync_refresh_button_alignment for
        the matching per-column-count grid math)."""
        CARD_W, GAP, CONTENT_MARGIN, SCROLLBAR, BUFFER = 320, 25, 20, 16, 40
        grid_width = num_cols * CARD_W + (num_cols - 1) * GAP
        content_width = CONTENT_MARGIN * 2 + grid_width + SCROLLBAR + BUFFER
        return sidebar_width + content_width

    def _sync_refresh_button_alignment(self):
        """Keep the Refresh button's right edge aligned with the last card's
        right edge in the grid below.

        The grid is left-aligned, so its position never moves regardless of
        window size. This button, however, is positioned by a stretch that
        fills to the row's right edge (see controls_row above) -- which DOES
        move whenever the window is resized, maximized, or restored, or the
        sidebar is collapsed/expanded (which also changes the column count,
        see _current_card_columns). A single fixed gap can only be correct at
        one specific size/column-count, so this recomputes it from the
        *current* geometry every time instead.
        """
        if not hasattr(self, "sidebar_widget") or not hasattr(self, "_refresh_align_spacer"):
            return  # called once before the sidebar/spacer exist yet -- skip

        # Constant for a given column count regardless of window size:
        # content margin (20) + N cards (each card_w wide) + (N-1) gaps (25
        # each) -- see cards_layout's margins above for why this starts at
        # 20px. List View swaps in the wider LIST_ROW_WIDTH single column
        # instead of the icon grid's 320px cards.
        num_cols = self._current_card_columns()
        card_w = LIST_ROW_WIDTH if self._list_view_enabled else 320
        grid_right_edge = 20 + num_cols * card_w + (num_cols - 1) * 25

        content_width = self.width() - self.sidebar_widget.width()
        row_right_bound = content_width - 20  # content_widget's own right margin
        gap = max(row_right_bound - grid_right_edge, 0)

        self._refresh_align_spacer.changeSize(gap, 0, QSizePolicy.Fixed, QSizePolicy.Minimum)
        self._refresh_align_layout.invalidate()
        self._refresh_align_layout.activate()

    def _load_app_version(self):
        """Read {app_name, version} from config-record/version.json.

        Single source of truth for the window title and the News page's
        release heading, so they can't drift out of sync when the version
        bumps. Falls back to a hardcoded default if the file is missing/corrupt.
        """
        default = {"app_name": "Dinosaur-List", "version": "V1.0.0.3"}
        version_path = Path(__file__).parent.parent.parent / "config-record" / "version.json"
        try:
            with open(version_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return {
                "app_name": data.get("app_name", default["app_name"]),
                "version": data.get("version", default["version"]),
            }
        except Exception:
            return default

    def _on_ai_assistant_clicked(self):
        """Open the AI Assistant dialog and wire it to the LLM worker."""

        # If already open, just bring it to front instead of building a new one
        if self._ai_dialog is not None:
            self._ai_dialog.show()
            self._ai_dialog.raise_()
            self._ai_dialog.activateWindow()
            return

        # ── Build dialog ─────────────────────────────────────────────────────
        dialog = QDialog(self)
        dialog.setWindowTitle("🦕 AI Assistant")
        dialog.setMinimumSize(820, 600)
        dialog.setStyleSheet("""
            QDialog  { background-color: #f0f4ff; }
            QLabel#title_lbl {
                font-size: 22px; font-weight: 700;
                color: #0a58ca; padding: 4px 0;
            }
            QLabel#sub_lbl {
                font-size: 13px; color: #6c757d;
            }
            QTextEdit {
                background-color: #ffffff;
                border: 1.5px solid #b6d4fe;
                border-radius: 8px;
                padding: 10px;
                font-size: 14px;
                color: #212529;
            }

            /* ── Modern slim scrollbar ── */
            QScrollBar:vertical {
                background: transparent;
                width: 6px;
                margin: 4px 2px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background-color: #b6d4fe;
                border-radius: 3px;
                min-height: 24px;
            }
            QScrollBar::handle:vertical:hover  { background-color: #84b8fc; }
            QScrollBar::handle:vertical:pressed { background-color: #0d6efd; }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical       { height: 0px; }
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical       { background: none; }
            QScrollBar:horizontal {
                background: transparent;
                height: 6px;
                margin: 2px 4px;
                border-radius: 3px;
            }
            QScrollBar::handle:horizontal {
                background-color: #b6d4fe;
                border-radius: 3px;
                min-width: 24px;
            }
            QScrollBar::handle:horizontal:hover  { background-color: #84b8fc; }
            QScrollBar::handle:horizontal:pressed { background-color: #0d6efd; }
            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal      { width: 0px; }
            QScrollBar::add-page:horizontal,
            QScrollBar::sub-page:horizontal      { background: none; }

            QPushButton#send_btn {
                background-color: #0d6efd; color: #ffffff;
                border: none; border-radius: 6px;
                padding: 10px 24px; font-size: 14px; font-weight: 600;
            }
            QPushButton#send_btn:hover  { background-color: #0b5ed7; }
            QPushButton#send_btn:disabled {
                background-color: #adb5bd; color: #e9ecef;
            }
            QPushButton#close_btn {
                background-color: #6c757d; color: #ffffff;
                border: none; border-radius: 6px;
                padding: 10px 24px; font-size: 14px; font-weight: 600;
            }
            QPushButton#close_btn:hover { background-color: #5a6268; }
        """)

        root = QVBoxLayout(dialog)
        root.setSpacing(10)
        root.setContentsMargins(24, 18, 24, 18)

        # Dialog title row: animated GIF + text
        _dlg_title_w = QWidget()
        _dlg_title_w.setStyleSheet("QWidget { background: transparent; }")
        _dlg_title_row = QHBoxLayout(_dlg_title_w)
        _dlg_title_row.setContentsMargins(0, 0, 0, 0)
        _dlg_title_row.setSpacing(10)

        _dlg_gif_lbl = QLabel()
        _dino_path2 = Path(__file__).parent.parent.parent / "Sw-icon" / "d2.gif"
        if _dino_path2.exists():
            from PySide6.QtCore import QSize as _QSize2
            _dlg_dino_movie = QMovie(str(_dino_path2))
            _dlg_dino_movie.setScaledSize(_QSize2(45, 45))
            _dlg_gif_lbl.setMovie(_dlg_dino_movie)
            _dlg_dino_movie.start()
            self._dlg_dino_movie = _dlg_dino_movie   # prevent GC
        else:
            _dlg_gif_lbl.setText("🦕")
        _dlg_title_row.addWidget(_dlg_gif_lbl)

        title_lbl = QLabel("AI Assistant")
        title_lbl.setObjectName("title_lbl")
        _dlg_title_row.addWidget(title_lbl)
        _dlg_title_row.addStretch()
        root.addWidget(_dlg_title_w)

        sub_lbl = QLabel(
            "Ask me about any software in the Store — I'll read all the README files and help you."
        )
        sub_lbl.setObjectName("sub_lbl")
        sub_lbl.setWordWrap(True)
        root.addWidget(sub_lbl)

        chat_box = QTextEdit()
        chat_box.setReadOnly(True)
        chat_box.setMinimumHeight(300)
        root.addWidget(chat_box)

        # Thinking indicator — animated dots, shown only while LLM is running
        thinking_label = QLabel()
        thinking_label.setStyleSheet("""
            QLabel {
                color: #adb5bd;
                font-size: 13px;
                font-style: italic;
                padding: 2px 4px;
            }
        """)
        thinking_label.setAlignment(Qt.AlignLeft)
        thinking_label.hide()
        root.addWidget(thinking_label)

        _dot_frames = ["🦕  Thinking ·", "🦕  Thinking · ·", "🦕  Thinking · · ·"]
        _dot_index  = [0]

        _think_timer = QTimer(dialog)
        _think_timer.setInterval(420)

        def _tick():
            _dot_index[0] = (_dot_index[0] + 1) % len(_dot_frames)
            thinking_label.setText(_dot_frames[_dot_index[0]])

        _think_timer.timeout.connect(_tick)

        input_row = QHBoxLayout()
        input_row.setSpacing(10)

        # Left column: text input + reload button stacked vertically
        input_left = QVBoxLayout()
        input_left.setSpacing(4)
        msg_input = QTextEdit()
        msg_input.setFixedHeight(68)
        msg_input.setPlaceholderText("Ask me about the software tools…  (Enter to send, Shift+Enter for new line)")
        input_left.addWidget(msg_input)

        reload_btn = QPushButton("Reload README")
        reload_btn.setObjectName("reload_btn")
        reload_btn.setStyleSheet("""
            QPushButton#reload_btn {
                background-color: #e9ecef;
                color: #495057;
                border: 1px solid #ced4da;
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 12px;
            }
            QPushButton#reload_btn:hover  { background-color: #dee2e6; }
            QPushButton#reload_btn:pressed { background-color: #ced4da; }
            QPushButton#reload_btn:disabled { color: #adb5bd; }
        """)
        input_left.addWidget(reload_btn)
        input_row.addLayout(input_left)

        # Right column: Send + Close, same size, vertically centred
        _BTN_W, _BTN_H = 100, 36
        right_col = QVBoxLayout()
        right_col.setSpacing(6)
        right_col.setAlignment(Qt.AlignVCenter)

        send_btn = QPushButton("Send")
        send_btn.setObjectName("send_btn")
        send_btn.setFixedSize(_BTN_W, _BTN_H)
        send_btn.setEnabled(False)
        right_col.addWidget(send_btn)

        close_btn = QPushButton("Close")
        close_btn.setObjectName("close_btn")
        close_btn.setFixedSize(_BTN_W, _BTN_H)
        close_btn.clicked.connect(dialog.accept)
        right_col.addWidget(close_btn)

        input_row.addLayout(right_col)
        root.addLayout(input_row)

        # ── Helpers ──────────────────────────────────────────────────────────
        _streaming = [False]   # guard against parallel sends

        def _append_html(html: str):
            chat_box.moveCursor(QTextCursor.End)
            chat_box.insertHtml(html)
            chat_box.moveCursor(QTextCursor.End)
            chat_box.ensureCursorVisible()

        def _append_text(text: str):
            chat_box.moveCursor(QTextCursor.End)
            chat_box.insertPlainText(text)
            chat_box.moveCursor(QTextCursor.End)
            chat_box.ensureCursorVisible()

        def _set_busy(busy: bool):
            _streaming[0] = busy
            send_btn.setEnabled(not busy)
            msg_input.setReadOnly(busy)
            if busy:
                send_btn.setText("…")
                _dot_index[0] = 0
                thinking_label.setText(_dot_frames[0])
                thinking_label.show()
                _think_timer.start()
            else:
                _think_timer.stop()
                thinking_label.hide()
                send_btn.setText("Send")

        # ── LLM worker signals ───────────────────────────────────────────────
        worker = self._llm_worker

        def _on_token(tok: str):
            _append_text(tok)

        def _on_reply_done(full_text: str, not_found: bool):
            _append_html("<br>")
            _set_busy(False)

        def _on_error(msg: str):
            _append_html(
                f"<br><b style='color:#dc3545'>⚠ Error:</b> {msg}<br>"
            )
            _set_busy(False)

        worker.token_ready.connect(_on_token)
        worker.reply_done.connect(_on_reply_done)
        worker.error.connect(_on_error)

        # ── Reload README handler ─────────────────────────────────────────────
        def _on_reload_done(msg: str):
            reload_btn.setEnabled(True)
            reload_btn.setText("Reload README")
            _append_html(f"<br><b style='color:#198754'>✅ {msg}</b>")
            # Refresh keyword guide after re-index
            try:
                hint_plain = worker.assistant.welcome_message()
                hint_html  = (
                    hint_plain
                    .replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                    .replace("\n", "<br>")
                )
                _append_html(
                    f"<br><span style='color:#6c757d; font-size:12px;'>{hint_html}</span><br>"
                )
            except Exception:
                pass

        worker.reload_done.connect(_on_reload_done)

        def _on_reload_click():
            if _streaming[0]:
                return
            reload_btn.setEnabled(False)
            reload_btn.setText("Reloading…")
            _append_html("<br><i style='color:#6c757d'>Reloading READMEs from App_Store…</i>")
            from threading import Thread as _Thread
            self._reload_thread = _Thread(target=worker.do_reload, daemon=True)
            self._reload_thread.start()

        reload_btn.clicked.connect(_on_reload_click)

        # Disconnect all dialog-local slots when dialog closes
        def _cleanup():
            try: worker.token_ready.disconnect(_on_token)
            except Exception: pass
            try: worker.reply_done.disconnect(_on_reply_done)
            except Exception: pass
            try: worker.error.disconnect(_on_error)
            except Exception: pass
            try: worker.reload_done.disconnect(_on_reload_done)
            except Exception: pass

        dialog.finished.connect(_cleanup)

        # ── Send logic ───────────────────────────────────────────────────────
        def _send():
            if _streaming[0]:
                return
            text = msg_input.toPlainText().strip()
            if not text:
                return
            msg_input.clear()

            _append_html(
                f"<br><b style='color:#0a58ca'>You:</b>&nbsp;{text}<br>"
                f"<b style='color:#198754'>Assistant:</b>&nbsp;"
            )
            _set_busy(True)
            self._llm_thread = Thread(
                target=worker.do_query, args=(text,), daemon=True
            )
            self._llm_thread.start()

        send_btn.clicked.connect(_send)

        # Enter → send  |  Shift+Enter → newline
        def _msg_key_press(event):
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                if event.modifiers() & Qt.ShiftModifier:
                    # Shift+Enter: insert a real newline
                    QTextEdit.keyPressEvent(msg_input, event)
                else:
                    # Plain Enter: send the message
                    _send()
            else:
                QTextEdit.keyPressEvent(msg_input, event)

        msg_input.keyPressEvent = _msg_key_press

        # ── Initialise LLM if not done yet ───────────────────────────────────
        def _show_welcome():
            """Show the welcome banner + keyword guide. Called on every open."""
            try:
                hint_plain = worker.assistant.welcome_message()
                hint_html  = (
                    hint_plain
                    .replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                    .replace("\n", "<br>")
                )
                hint_block = (
                    f"<span style='color:#6c757d; font-size:12px;'>{hint_html}</span>"
                )
            except Exception:
                hint_block = (
                    "<i style='color:#6c757d'>Type 'list all tools' to browse, "
                    "or describe what you need.</i>"
                )
            _append_html(
                "<b style='color:#198754'>✅ Welcome to WSD NPI Penang Team's "
                "Dinosaur List AppStore!</b><br>"
                f"{hint_block}<br>"
            )

        if worker.ready:
            send_btn.setEnabled(True)
            _show_welcome()
        else:
            def _on_init_done():
                send_btn.setEnabled(True)
                _show_welcome()
                try: worker.init_done.disconnect(_on_init_done)
                except Exception: pass
                try: worker.error.disconnect(_on_init_error)
                except Exception: pass

            def _on_init_error(msg: str):
                _append_html(
                    f"<b style='color:#dc3545'>❌ Could not load AI: {msg}</b><br>"
                )
                try: worker.init_done.disconnect(_on_init_done)
                except Exception: pass
                try: worker.error.disconnect(_on_init_error)
                except Exception: pass

            worker.init_done.connect(_on_init_done)
            worker.error.connect(_on_init_error)

            chat_box.setPlaceholderText("")
            _append_html(
                "<i style='color:#6c757d'>"
                "⏳ Welcome to WSD NPI Penang Team's Dinosaur List AppStore! <br>"
                "Loading AI model and knowledge base — this may take a few seconds on first launch…</i><br>"
            )

            self._llm_init_thread = Thread(
                target=worker.do_init, daemon=True
            )
            self._llm_init_thread.start()

        # ── Show non-modally so the main window stays interactive ────────────
        self._ai_dialog = dialog
        dialog.setWindowModality(Qt.NonModal)
        dialog.finished.connect(lambda _: setattr(self, '_ai_dialog', None))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def show_loading(self):
        """Show loading GIF animation"""
        self.loading_label.show()
        if hasattr(self, 'loading_movie'):
            self.loading_movie.start()
        # Force UI to update immediately
        QApplication.processEvents()
    
    def hide_loading(self):
        """Hide loading GIF animation"""
        if hasattr(self, 'loading_movie'):
            self.loading_movie.stop()
        self.loading_label.hide()
        # Force UI to update immediately
        QApplication.processEvents()
    
    def _setup_sidebar(self, root_layout, central):
        """Build the left-side navigation panel: branding header (whose logo
        icon doubles as the AI Assistant trigger), page nav items, and a
        collapse/expand toggle that shrinks the panel to an icon-only rail.
        """
        self._sidebar_expanded_width = 220
        self._sidebar_collapsed_width = 80
        self._sidebar_collapsed = False
        self._sidebar_anim = None
        self._logo_size_expanded = 56
        self._logo_size_collapsed = 40

        sidebar = QWidget()
        sidebar.setFixedWidth(self._sidebar_expanded_width)
        sidebar.setStyleSheet(SIDEBAR_STYLE)
        self.sidebar_widget = sidebar

        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(16, 20, 16, 20)
        sidebar_layout.setSpacing(4)
        sidebar_layout.setAlignment(Qt.AlignTop)

        # Branding header repurposed as the AI Assistant trigger: animated dino
        # icon + "AI Assistant" label, both clickable. The label is hidden
        # when the sidebar collapses, leaving just the icon.
        header_row = QHBoxLayout()
        header_row.setSpacing(10)

        self.sidebar_logo_label = ClickableLabel()
        self.sidebar_logo_label.setFixedSize(self._logo_size_expanded, self._logo_size_expanded)
        self.sidebar_logo_label.setAlignment(Qt.AlignCenter)
        self.sidebar_logo_label.setCursor(Qt.PointingHandCursor)
        self.sidebar_logo_label.setToolTip("Open AI Assistant")
        self.sidebar_logo_label.setStyleSheet("""
            QLabel {
                background-color: #e6f7f0;
                border-radius: 12px;
            }
        """)
        dino_path = Path(__file__).parent.parent.parent / "Sw-icon" / "d2.gif"
        if dino_path.exists():
            self._sidebar_ai_movie = QMovie(str(dino_path))
            self._sidebar_ai_movie.setScaledSize(QSize(44, 44))
            self.sidebar_logo_label.setMovie(self._sidebar_ai_movie)
            self._sidebar_ai_movie.start()
        else:
            self.sidebar_logo_label.setText("🦕")
        self.sidebar_logo_label.clicked.connect(self._on_ai_assistant_clicked)
        header_row.addWidget(self.sidebar_logo_label)

        self.sidebar_title_label = ClickableLabel("AI Assistant")
        self.sidebar_title_label.setStyleSheet(SIDEBAR_TITLE_STYLE)
        self.sidebar_title_label.setWordWrap(True)
        self.sidebar_title_label.setCursor(Qt.PointingHandCursor)
        self.sidebar_title_label.setToolTip("Open AI Assistant")
        self.sidebar_title_label.clicked.connect(self._on_ai_assistant_clicked)
        header_row.addWidget(self.sidebar_title_label, 1)

        sidebar_layout.addLayout(header_row)
        sidebar_layout.addSpacing(24)

        # Page navigation items -- icon and label kept separate so the
        # collapsed rail can show icon-only text on the same buttons.
        nav_items = [
            (0, "🚀", "Local Dashboard"),
            (1, "🏪", "Software Store"),
            (2, "🔗", "Useful Links"),
            (3, "📰", "News & Updates"),
        ]

        self.sidebar_buttons = []
        self._sidebar_nav_icons = []
        self._sidebar_nav_labels = []
        for page_index, icon, label in nav_items:
            btn = QPushButton(f"{icon}  {label}")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(48)
            btn.clicked.connect(lambda checked, page=page_index: self.go_to_page(page))
            sidebar_layout.addWidget(btn)
            self.sidebar_buttons.append(btn)
            self._sidebar_nav_icons.append(icon)
            self._sidebar_nav_labels.append(label)

        sidebar_layout.addStretch()

        # Footer: current user/IP (via LocalIdentity) + Log Out, pinned to the
        # bottom below a divider, mirroring the reference layout's stats/logout
        # footer -- adapted to what's actually relevant for this app.
        footer_divider = QFrame()
        footer_divider.setFrameShape(QFrame.HLine)
        footer_divider.setStyleSheet(
            "QFrame { background-color: #e9ecef; max-height: 1px; border: none; }"
        )
        sidebar_layout.addWidget(footer_divider)
        sidebar_layout.addSpacing(12)

        self._ensure_user_api_on_path()
        try:
            from local_identity import LocalIdentity
            identity = LocalIdentity()
            username = identity.get_current_username()
            ip_address = identity.get_local_ip()
        except Exception as exc:
            username, ip_address = "Unknown", "Unknown"
            print(f"[SIDEBAR] Could not resolve local identity: {exc}")

        self.sidebar_user_label = QLabel(f"👤  {username}")
        self.sidebar_user_label.setStyleSheet(SIDEBAR_INFO_STYLE)
        sidebar_layout.addWidget(self.sidebar_user_label)

        self.sidebar_ip_label = QLabel(f"🌐  {ip_address}")
        self.sidebar_ip_label.setStyleSheet(SIDEBAR_INFO_STYLE)
        sidebar_layout.addWidget(self.sidebar_ip_label)

        self.sidebar_local_count_label = QLabel("📥  Local Downloads: 0")
        self.sidebar_local_count_label.setStyleSheet(SIDEBAR_INFO_STYLE)
        sidebar_layout.addWidget(self.sidebar_local_count_label)

        self.sidebar_store_count_label = QLabel("🏪  Software Store: 0")
        self.sidebar_store_count_label.setStyleSheet(SIDEBAR_INFO_STYLE)
        sidebar_layout.addWidget(self.sidebar_store_count_label)

        sidebar_layout.addSpacing(8)

        self.sidebar_logout_btn = QPushButton("🚪  Exit")
        self.sidebar_logout_btn.setCursor(Qt.PointingHandCursor)
        self.sidebar_logout_btn.setFixedHeight(44)
        self.sidebar_logout_btn.setStyleSheet(SIDEBAR_LOGOUT_STYLE)
        self.sidebar_logout_btn.setToolTip("Exit the app")
        self.sidebar_logout_btn.clicked.connect(self.close)
        sidebar_layout.addWidget(self.sidebar_logout_btn)

        root_layout.addWidget(sidebar)

        # Collapse/expand toggle -- floats over the sidebar/content border
        # (parented to the central widget, not either layout) so it stays in
        # the same spot regardless of which side "owns" that border. Uses a
        # real arrow icon (not a text glyph) sized up so it's easy to see.
        self.sidebar_toggle_btn = QPushButton(central)
        self.sidebar_toggle_btn.setFixedSize(36, 36)
        self.sidebar_toggle_btn.setIconSize(QSize(20, 20))
        self.sidebar_toggle_btn.setIcon(self._make_chevron_icon("left"))
        self.sidebar_toggle_btn.setCursor(Qt.PointingHandCursor)
        self.sidebar_toggle_btn.setToolTip("Collapse sidebar")
        self.sidebar_toggle_btn.setStyleSheet("""
            QPushButton {
                background-color: #ffffff;
                border: 1px solid #e5e7eb;
                border-radius: 18px;
            }
            QPushButton:hover { background-color: #f3f4f8; }
        """)
        self.sidebar_toggle_btn.clicked.connect(self._toggle_sidebar)
        self.sidebar_toggle_btn.raise_()
        self._position_sidebar_toggle()

        self._update_sidebar_buttons()

    def _position_sidebar_toggle(self):
        """Keep the collapse/expand toggle straddling the sidebar/content border."""
        x = self.sidebar_widget.width() - self.sidebar_toggle_btn.width() // 2
        self.sidebar_toggle_btn.move(x, 32)

    def _make_chevron_icon(self, direction="left", size=24, color="#6b7280", thickness=3):
        """Draw a bold chevron icon (not text) for the sidebar collapse toggle.

        Qt's built-in QStyle.SP_ArrowLeft/Right standard icons render very
        faint/thin on some platform styles -- easy to miss at small sizes.
        Drawing our own guarantees a crisp, high-contrast arrow regardless
        of the active OS theme.
        """
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(color))
        pen.setWidth(thickness)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)

        margin = size * 0.28
        mid_y = size / 2
        if direction == "left":
            tip, top, bottom = QPointF(margin, mid_y), QPointF(size - margin, margin), QPointF(size - margin, size - margin)
        else:
            tip, top, bottom = QPointF(size - margin, mid_y), QPointF(margin, margin), QPointF(margin, size - margin)

        painter.drawLine(top, tip)
        painter.drawLine(tip, bottom)
        painter.end()

        return QIcon(pixmap)

    def _set_sidebar_logo_size(self, size):
        """Resize the AI logo icon (and its movie) to *size*x*size*."""
        self.sidebar_logo_label.setFixedSize(size, size)
        if hasattr(self, "_sidebar_ai_movie"):
            movie_size = size - 12  # keep the same ~6px padding around the gif as before
            self._sidebar_ai_movie.setScaledSize(QSize(movie_size, movie_size))

    def _toggle_sidebar(self):
        """Collapse the sidebar to an icon-only rail, or expand it back, with
        a smooth width animation instead of an instant snap.

        Content changes (label text, title visibility, logo size) are timed
        asymmetrically around the animation: collapsing switches to icon-only
        immediately (short text never overflows, even at the still-wide
        starting size), while expanding waits until the animation finishes to
        reveal full labels (so they're never clipped mid-animation by a
        sidebar that hasn't reached full width yet).
        """
        if getattr(self, "_sidebar_anim", None) is not None:
            return  # ignore rapid re-clicks while an animation is in flight

        self._sidebar_collapsed = not self._sidebar_collapsed
        collapsed = self._sidebar_collapsed

        self.sidebar_toggle_btn.setIcon(self._make_chevron_icon("right" if collapsed else "left"))
        self.sidebar_toggle_btn.setToolTip("Expand sidebar" if collapsed else "Collapse sidebar")

        if collapsed:
            self._set_sidebar_logo_size(self._logo_size_collapsed)
            self.sidebar_title_label.setVisible(False)
            for btn, icon, label in zip(self.sidebar_buttons, self._sidebar_nav_icons, self._sidebar_nav_labels):
                btn.setText(icon)
                btn.setToolTip(label)
            # No room for arbitrary-length username/IP text in the narrow
            # rail -- hide them rather than show something clipped.
            self.sidebar_user_label.setVisible(False)
            self.sidebar_ip_label.setVisible(False)
            self.sidebar_local_count_label.setVisible(False)
            self.sidebar_store_count_label.setVisible(False)
            self.sidebar_logout_btn.setText("🚪")
            self.sidebar_logout_btn.setToolTip("Exit")
        self._update_sidebar_buttons()

        start_width = self.sidebar_widget.width()
        end_width = self._sidebar_collapsed_width if collapsed else self._sidebar_expanded_width

        # The window itself grows/shrinks in lockstep with the sidebar slide,
        # since 4 columns (collapsed) need noticeably more total width than 3
        # (expanded) even after accounting for the sidebar's own size change.
        start_win_width = self.width()
        end_win_width = self._ideal_window_width(4 if collapsed else 3, end_width)

        anim = QVariantAnimation(self)
        anim.setDuration(220)
        anim.setEasingCurve(QEasingCurve.InOutCubic)
        anim.setStartValue(start_width)
        anim.setEndValue(end_width)

        def _on_value_changed(width):
            self.sidebar_widget.setFixedWidth(width)
            self._position_sidebar_toggle()

            progress = (width - start_width) / (end_width - start_width) if end_width != start_width else 1
            self.resize(int(start_win_width + progress * (end_win_width - start_win_width)), self.height())

            # content_widget isn't fixed-width, so collapsing/expanding the
            # sidebar hands its freed/reclaimed width back to content_widget
            # on every frame -- keep the Refresh button tracking it smoothly.
            self._sync_refresh_button_alignment()

        def _on_finished():
            if not collapsed:
                self._set_sidebar_logo_size(self._logo_size_expanded)
                self.sidebar_title_label.setVisible(True)
                for btn, icon, label in zip(self.sidebar_buttons, self._sidebar_nav_icons, self._sidebar_nav_labels):
                    btn.setText(f"{icon}  {label}")
                    btn.setToolTip("")
                self.sidebar_user_label.setVisible(True)
                self.sidebar_ip_label.setVisible(True)
                self.sidebar_local_count_label.setVisible(True)
                self.sidebar_store_count_label.setVisible(True)
                self.sidebar_logout_btn.setText("🚪  Exit")
                self.sidebar_logout_btn.setToolTip("Exit the app")
                self._update_sidebar_buttons()
            # Reflow the card grid to the new column count only once the
            # window has actually reached its final size (both directions) --
            # doing it mid-slide risks a brief overflow/scrollbar flash before
            # the window catches up to the wider 4-column layout.
            self._display_current_page()
            self._position_sidebar_toggle()
            self._sync_refresh_button_alignment()
            self._sidebar_anim = None

        anim.valueChanged.connect(_on_value_changed)
        anim.finished.connect(_on_finished)
        self._sidebar_anim = anim  # keep a reference so it isn't garbage-collected mid-flight
        anim.start()

    def _update_sidebar_buttons(self):
        """Highlight the sidebar item matching the current page."""
        collapsed = self._sidebar_collapsed
        for i, btn in enumerate(self.sidebar_buttons):
            is_active = i == self.current_page
            if collapsed:
                btn.setStyleSheet(SIDEBAR_ITEM_ACTIVE_STYLE_COLLAPSED if is_active else SIDEBAR_ITEM_STYLE_COLLAPSED)
            else:
                btn.setStyleSheet(SIDEBAR_ITEM_ACTIVE_STYLE if is_active else SIDEBAR_ITEM_STYLE)

    def keyPressEvent(self, event):
        """Up/Down arrow keys move to the previous/next sidebar tab (wraps
        around). Only fires when no focused child widget (a combobox, a text
        field's own key handling, etc.) has already consumed the key first.
        """
        if event.key() == Qt.Key_Down:
            self.go_to_page((self.current_page + 1) % self.total_pages)
            event.accept()
        elif event.key() == Qt.Key_Up:
            self.go_to_page((self.current_page - 1) % self.total_pages)
            event.accept()
        else:
            super().keyPressEvent(event)

    def go_to_page(self, page_number):
        """Navigate to specific page"""
        if 0 <= page_number < self.total_pages:
            self.current_page = page_number
            self._update_page_title()
            self._update_refresh_button_visibility()
            self._display_current_page()
            self._update_sidebar_buttons()
            self._update_az_highlight()

    def _update_page_title(self):
        """Update the page title based on current page"""
        titles = {
            0: "🚀 Local Dashboard",
            1: "🏪 Software Store",
            2: "🔗 Useful Links",
            3: "📰 News & Updates"
        }
        self.title_label.setText(titles.get(self.current_page, "🚀 Software Dashboard"))
    
    def _on_list_view_toggled(self, checked):
        """Switch the Dashboard/Store card grid between the multi-column icon
        view and a single-column list view (see _current_card_columns)."""
        self._list_view_enabled = checked
        self.az_bar_widget.setVisible(checked and self.current_page in [0, 1])
        self._display_current_page()
        self._sync_refresh_button_alignment()
        self._update_az_highlight()

    def _current_list_refs(self):
        """(refs dict, name attribute) for whichever page's List View is active, or None."""
        if self.current_page == 0:
            return self.card_references, 'display_name'
        if self.current_page == 1:
            return self.store_card_references, 'software_name'
        return None, None

    def _jump_to_letter(self, letter):
        """A-Z jump bar: scroll the current List View to the first row at or
        after *letter* (falls through to the next available letter if none
        start exactly with it -- rows are sorted alphabetically in List View,
        so this is a simple linear scan)."""
        if not self._list_view_enabled:
            return

        refs, name_attr = self._current_list_refs()
        if refs is None:
            return

        target = letter.lower()
        for widget in refs.values():
            if not widget.isVisible():
                continue
            name = getattr(widget, name_attr, "") or ""
            if name[:1].lower() >= target:
                self.cards_scroll_area.ensureWidgetVisible(widget)
                self._update_az_highlight()
                return

    def _on_list_scrolled(self, value):
        """Keep the A-Z bar's highlighted letter in sync while the list scrolls."""
        self._update_az_highlight()

    def _update_az_highlight(self):
        """Highlight the A-Z bar letter matching the topmost visible row."""
        if not self._list_view_enabled or not self.az_bar_widget.isVisible():
            return

        refs, name_attr = self._current_list_refs()
        if refs is None:
            return

        scroll_y = self.cards_scroll_area.verticalScrollBar().value()
        current_letter = None
        best_y = -1
        for widget in refs.values():
            if not widget.isVisible():
                continue
            y = widget.y()
            if y <= scroll_y + 4 and y > best_y:
                best_y = y
                name = getattr(widget, name_attr, "") or ""
                current_letter = name[:1].upper() if name else None

        for letter, label in self._az_letter_labels.items():
            label.setStyleSheet(self._az_active_style if letter == current_letter else self._az_default_style)

    def _update_refresh_button_visibility(self):
        """Show/hide refresh button, filter box, and header badges based on current page."""
        self.refresh_btn.setVisible(self.current_page in [0, 1])

        # Filter box: visible on Page 1 (Dashboard) and Page 2 (Store)
        on_filtered_page = self.current_page in [0, 1]
        self.filter_edit.setVisible(on_filtered_page)

        # List View checkbox: same pages as the filter box/Refresh button
        self.list_view_checkbox.setVisible(on_filtered_page)

        # A-Z jump bar: List View only, and only on the pages that have a list view
        self.az_bar_widget.setVisible(self._list_view_enabled and on_filtered_page)

        # Clear filter silently on every page switch so each page starts fresh
        self.filter_edit.blockSignals(True)
        self.filter_edit.clear()
        self.filter_edit.blockSignals(False)

        # Update placeholder text to reflect which page is active
        if self.current_page == 0:
            self.filter_edit.setPlaceholderText("🔍  Search installed software…")
        else:
            self.filter_edit.setPlaceholderText("🔍  Search software by name…")
    
    @property
    def _is_busy(self):
        """True while a refresh/download/install worker is running in the background."""
        return self._busy_count > 0

    def _begin_busy_status(self):
        """Mark a long-running operation as started.

        While busy, tab navigation must not overwrite the live progress text
        in status_label with the tab's generic summary message.
        """
        self._busy_count += 1

    def _end_busy_status(self):
        """Mark a long-running operation as finished.

        Must be called after the operation's own completion handler has set
        its final status text (e.g. "✓ Complete!"), so that text is preserved
        instead of being immediately overwritten by a tab-summary refresh.
        Once this reaches zero, tab navigation resumes updating status.setText.
        """
        self._busy_count = max(0, self._busy_count - 1)

    def _claim_status_owner(self):
        """Claim the status bar for a fresh, user-initiated action.

        Returns a token. An async operation (e.g. delete) that captures a token when
        it starts should compare it against the current owner before writing its own
        progress/completion text — if a newer action (e.g. launching different software)
        has claimed the status bar since, the stale write is skipped so it can't stomp
        on what the user is looking at now.
        """
        self._status_owner_token += 1
        return self._status_owner_token

    def _display_current_page(self):
        """Display content for the current page"""
        # Clear existing cards
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Reset column stretches from previous news page
        for col in range(4):
            self.cards_layout.setColumnStretch(col, 0)

        # Display different content based on current page
        if self.current_page == 0:
            self.cards_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            self._display_dashboard_page()
        elif self.current_page == 1:
            self.cards_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            self._display_store_page()
        elif self.current_page == 2:
            # Useful Links page — let the layout fill full horizontal space
            self.cards_layout.setAlignment(Qt.AlignTop)
            self.cards_layout.setColumnStretch(0, 1)
            self._display_useful_links_page()
        elif self.current_page == 3:
            # News page — let the layout fill full horizontal space
            self.cards_layout.setAlignment(Qt.AlignTop)
            self.cards_layout.setColumnStretch(0, 1)
            self._display_news_page()

        self._update_az_highlight()

    def _display_dashboard_page(self):
        """Display Software Dashboard page (Page 1) - Scrollable for all cards"""
        from .folder_parser import parse_software_folder_name, format_software_name, get_author_raw

        # Clear card references
        self.card_references.clear()
        self.dashboard_folder_name_map = {}  # folder_name (App_Store) → folder_path str

        # List View sorts alphabetically by name so the sequence number and
        # the A-Z jump bar (see _jump_to_letter) both line up with what's on
        # screen; the icon grid keeps its normal (install/scan) order.
        software_list = self.all_software_data
        if self._list_view_enabled:
            software_list = sorted(
                software_list,
                key=lambda sw: format_software_name(parse_software_folder_name(sw['folder'].name)).lower()
            )

        # Display ALL software cards (no limit, scrollable)
        row = col = 0
        for i, software_data in enumerate(software_list):
            folder = software_data['folder']

            # Derive the App_Store folder name and folder_id for this installed software
            parsed = parse_software_folder_name(folder.name)
            sw_name = format_software_name(parsed)
            author  = get_author_raw(parsed)
            app_store_folder_name = f"{sw_name}-{author}"

            app_store_json = (
                self.software_path.parent / "App_Store"
                / app_store_folder_name / f"{app_store_folder_name}.json"
            )
            dash_folder_id = ""
            if app_store_json.exists():
                try:
                    with open(app_store_json, 'r', encoding='utf-8') as _f:
                        dash_folder_id = json.load(_f).get('folder_id', '')
                except Exception:
                    pass

            row_class = SoftwareListRow if self._list_view_enabled else SoftwareCard
            extra_kwargs = {'sequence_number': i + 1} if self._list_view_enabled else {}
            card = row_class(
                software_data['name'],
                None,
                folder,
                software_data['is_latest'],
                icon_path=software_data.get('icon_path'),
                folder_name=app_store_folder_name,
                folder_id=dash_folder_id,
                readme_available=self._dashboard_readme_exists(folder),
                **extra_kwargs,
            )
            card.clicked.connect(self.launch_software)
            card.version_clicked.connect(self.show_version_info)
            card.readme_clicked.connect(self.show_readme)
            card.folder_clicked.connect(self.open_folder_location)
            card.update_clicked.connect(self._on_update_download)
            card.delete_clicked.connect(self.delete_software)
            card.card_refresh_clicked.connect(self._on_dashboard_card_refresh_clicked)
            self.cards_layout.addWidget(card, row, col)

            # Store references
            self.card_references[str(folder)] = card
            self.dashboard_folder_name_map[app_store_folder_name] = str(folder)

            col += 1
            if col >= self._current_card_columns():
                col = 0
                row += 1

        self.sidebar_local_count_label.setText(f"📥  Local Downloads: {len(self.all_software_data)}")

        # Update status — skip while a refresh/download is in progress so we
        # don't stomp on its live progress text (it's re-applied when done)
        if not self._is_busy:
            total_count = len(self.all_software_data)
            self.status_label.setText(
                f"✓ Showing {total_count} software application(s) | {self.page_names[self.current_page]}"
            )

    def _display_store_page(self):
        """Display Software Store page (Page 2) with cards from App_Store"""
        # Load store software data
        store_data = self._load_store_software()
        
        if not store_data:
            # Show placeholder if no store data
            container = QWidget()
            container_layout = QVBoxLayout(container)
            container_layout.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
            container_layout.setSpacing(20)
            container_layout.setContentsMargins(50, 20, 50, 50)
            
            store_label = QLabel("🏪 Software Store")
            store_label.setStyleSheet("""
                QLabel {
                    font-size: 48px;
                    font-weight: bold;
                    color: #007bff;
                    padding: 80px 100px 20px 100px;
                }
            """)
            store_label.setAlignment(Qt.AlignCenter)
            container_layout.addWidget(store_label)
            
            info_label = QLabel("No software available. Click Refresh to load store data.")
            info_label.setStyleSheet("""
                QLabel {
                    font-size: 18px;
                    color: #6c757d;
                    padding: 20px;
                }
            """)
            info_label.setAlignment(Qt.AlignCenter)
            container_layout.addWidget(info_label)
            
            container_layout.addStretch()
            self.cards_layout.addWidget(container, 0, 0, 4, 4)
            self.status_label.setText(f"📦 {self.page_names[self.current_page]} - No data")
            self.sidebar_store_count_label.setText("🏪  Software Store: 0")
            return
        
        # List View sorts alphabetically by name so the sequence number and
        # the A-Z jump bar (see _jump_to_letter) line up with what's on
        # screen; sort a copy -- store_data is the cached list, don't mutate it.
        if self._list_view_enabled:
            store_data = sorted(store_data, key=lambda sw: sw['name'].lower())

        # Display ALL cards in grid (4 columns, scrollable rows)
        row = 0
        col = 0
        self.store_card_references = {}  # folder_name → StoreCard

        for i, software in enumerate(store_data):
            # Create store card (or list row, if List View is checked)
            row_class = StoreListRow if self._list_view_enabled else StoreCard
            extra_kwargs = {'sequence_number': i + 1} if self._list_view_enabled else {}
            card = row_class(
                software_name=software['name'],
                author_name=software['author'],
                icon_path=software.get('icon_path'),
                json_path=software.get('json_path'),
                folder_name=software.get('folder_name'),
                folder_id=software.get('folder_id', ''),
                guide_available=software.get('guide_available', True),
                readme_available=software.get('readme_available', True),
                **extra_kwargs,
            )

            # Connect signals
            card.download_clicked.connect(self._on_store_download)
            card.guide_clicked.connect(self._on_store_guide_clicked)
            card.readme_clicked.connect(self._on_store_readme_clicked)
            card.card_refresh_clicked.connect(self._on_card_refresh_clicked)

            self.store_card_references[software.get('folder_name', software['name'])] = card

            self.cards_layout.addWidget(card, row, col)

            col += 1
            if col >= self._current_card_columns():
                col = 0
                row += 1
        
        self.sidebar_store_count_label.setText(f"🏪  Software Store: {len(store_data)}")

        # Update status — skip while a refresh/download is in progress so we
        # don't stomp on its live progress text (it's re-applied when done)
        if not self._is_busy:
            total_software = len(store_data)
            self.status_label.setText(f"🏪 {self.page_names[self.current_page]} - {total_software} software available")
    
    def _on_filter_changed(self, text: str):
        """Dispatch filter to the correct page handler."""
        if self.current_page == 0:
            self._filter_dashboard_cards(text)
        elif self.current_page == 1:
            self._filter_store_cards(text)

    def _filter_dashboard_cards(self, text: str):
        """Show only Dashboard cards whose name matches *text*, re-packing the grid."""
        if not self.card_references:
            return

        # Remove all widgets from the grid without destroying them
        while self.cards_layout.count():
            self.cards_layout.takeAt(0)

        # Re-add only matching cards in a compact 4-column grid
        row = col = 0
        matched = 0
        for folder_path_str, card in self.card_references.items():
            if self._matches_filter(card.display_name, text, card.author_name):
                self.cards_layout.addWidget(card, row, col)
                card.show()
                matched += 1
                col += 1
                if col >= self._current_card_columns():
                    col = 0
                    row += 1
            else:
                card.hide()

        total = len(self.card_references)
        if text.strip():
            self.status_label.setText(
                f"🔍 '{text.strip()}' — {matched} of {total} installed software matched"
            )
        else:
            self.status_label.setText(
                f"✓ Showing {total} software application(s) | {self.page_names[0]}"
            )
        self._update_az_highlight()

    @staticmethod
    def _matches_filter(name: str, text: str, author: str = "") -> bool:
        """Return True if *name* OR *author* matches the filter *text*.

        Matching rules (case-insensitive), applied independently to name and
        author -- either one matching is enough:
          1. Empty search  → always match
          2. Substring     → search text appears anywhere in the value
          3. Word match    → every space-separated word appears in the value
        """
        if not text.strip():
            return True
        search_lower = text.lower().strip()

        def _matches_value(value: str) -> bool:
            value_lower = value.lower()
            if search_lower in value_lower:
                return True
            return all(word in value_lower for word in search_lower.split())

        return _matches_value(name) or (bool(author) and _matches_value(author))

    def _filter_store_cards(self, text: str):
        """Show only Store cards whose name matches *text*, re-packing the grid."""
        if not self.store_card_references:
            return

        # Remove all widgets from the grid layout (without destroying them)
        while self.cards_layout.count():
            self.cards_layout.takeAt(0)

        # Re-add only matching cards in a compact 4-column grid
        row = col = 0
        matched = 0
        for folder_name, card in self.store_card_references.items():
            if self._matches_filter(card.software_name, text, card.author_name):
                self.cards_layout.addWidget(card, row, col)
                card.show()
                matched += 1
                col += 1
                if col >= self._current_card_columns():
                    col = 0
                    row += 1
            else:
                card.hide()

        # Update status bar
        total = len(self.store_card_references)
        if text.strip():
            self.status_label.setText(
                f"🔍 '{text.strip()}' — {matched} of {total} software matched"
            )
        else:
            self.status_label.setText(
                f"🏪 {self.page_names[1]} — {total} software available"
            )
        self._update_az_highlight()

    def _build_doc_page_html(self, html_body, accent, tint):
        """Wrap rendered markdown HTML in a clean, modern web-page shell.

        Shared by the News and Useful Links pages so their layout can't drift
        out of sync -- only the accent/tint colors differ between them.
        A centered, readable-width column with card-style section headers,
        replacing the old flat grey full-bleed background.
        """
        css = f"""
            * {{ box-sizing: border-box; }}
            html, body {{ margin: 0; background: #ffffff; }}
            body {{
                font-family: 'Segoe UI', Arial, sans-serif;
                font-size: 15px;
                color: #24292f;
                line-height: 1.8;
            }}
            .page {{
                max-width: 900px;
                margin: 0 auto;
                padding: 44px 16px 60px 16px;
            }}
            h1 {{
                font-size: 30px;
                font-weight: 800;
                color: #1a1d29;
                margin: 0 0 24px 0;
            }}
            h2 {{
                font-size: 19px;
                font-weight: 700;
                color: #1a1d29;
                margin-top: 34px;
                margin-bottom: 14px;
            }}
            h3 {{
                font-size: 15px;
                font-weight: 700;
                color: {accent};
                margin-top: 22px;
                margin-bottom: 8px;
            }}
            ul {{ padding-left: 22px; margin-top: 6px; }}
            li {{ margin-bottom: 9px; }}
            a {{ color: #0969da; text-decoration: underline; font-weight: 600; }}
            a:hover {{ color: #0550ae; }}
            hr {{ border: none; border-top: 1px solid #edeff3; margin: 30px 0; }}
            blockquote {{
                background: {tint};
                border-left: 4px solid {accent};
                padding: 14px 20px;
                margin: 22px 0;
                border-radius: 8px;
                color: #40465a;
            }}
            p {{ margin: 10px 0; }}
            code {{
                background: #f1f2f6;
                padding: 2px 7px;
                border-radius: 5px;
                font-family: Consolas, monospace;
                font-size: 13px;
                color: #c2255c;
            }}
        """
        return f"<html><head><style>{css}</style></head><body><div class='page'>{html_body}</div></body></html>"

    def _display_news_page(self):
        """Display News page — reads all .md files from the News folder and renders them."""
        news_root = Path(__file__).parent.parent.parent / "News"

        # Read only news.md
        news_file = news_root / "news.md"
        md_files = [news_file] if news_file.exists() else []

        # ── Outer wrapper fills the entire grid cell ──────────────────────────
        outer = QWidget()
        outer.setSizePolicy(
            outer.sizePolicy().horizontalPolicy(),
            outer.sizePolicy().verticalPolicy(),
        )
        from PySide6.QtWidgets import QSizePolicy
        outer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        if not md_files:
            placeholder = QLabel("📰 No news files found in the News folder.")
            placeholder.setStyleSheet(
                "font-size: 20px; color: #6c757d; padding: 60px;"
            )
            placeholder.setAlignment(Qt.AlignCenter)
            outer_layout.addWidget(placeholder)
            self.cards_layout.addWidget(outer, 0, 0, 1, 4)
            if not self._is_busy:
                self.status_label.setText(
                    f"📢 {self.page_names[self.current_page]} — No content"
                )
            return

        # Combine all md files into one HTML body
        combined_md = ""
        for md_file in md_files:
            try:
                combined_md += md_file.read_text(encoding="utf-8") + "\n\n"
            except Exception as e:
                combined_md += f"*(Error reading {md_file.name}: {e})*\n\n"

        # Substitute the {{APP_VERSION}} placeholder with the version from
        # config-record/version.json so news.md's heading can't drift out of
        # sync with the window title when the version bumps.
        combined_md = combined_md.replace("{{APP_VERSION}}", self.app_version_info["version"])

        if MARKDOWN_AVAILABLE:
            html_body = markdown.markdown(combined_md, extensions=["extra", "nl2br"])
        else:
            # Minimal fallback: convert line breaks, but no real markdown parsing
            html_body = combined_md.replace("\n", "<br>")

        # Indigo accent -- matches the sidebar's active-item highlight for a
        # consistent look across the app instead of the old flat grey page.
        full_html = self._build_doc_page_html(html_body, accent="#4338ca", tint="#eef0ff")

        # ── QTextBrowser fills the outer wrapper ──────────────────────────────
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        browser.setStyleSheet("""
            QTextBrowser {
                background-color: #ffffff;
                border: none;
            }
            QScrollBar:vertical {
                width: 10px;
                background: #f1f2f6;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical {
                background: #c7cad3;
                border-radius: 5px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover { background: #9498a6; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)
        browser.setHtml(full_html)

        outer_layout.addWidget(browser)
        self.cards_layout.addWidget(outer, 0, 0, 1, 4)
        if not self._is_busy:
            self.status_label.setText(
                f"📢 {self.page_names[self.current_page]} — {len(md_files)} file(s) loaded"
            )

    def _display_useful_links_page(self):
        """Display Useful Links page — reads link.md from the project root and renders it."""
        link_file = Path(__file__).parent.parent.parent / "News" / "link.md"

        outer = QWidget()
        from PySide6.QtWidgets import QSizePolicy
        outer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        if not link_file.exists():
            placeholder = QLabel("🔗 No link.md file found in the News folder.")
            placeholder.setStyleSheet(
                "font-size: 20px; color: #6c757d; padding: 60px;"
            )
            placeholder.setAlignment(Qt.AlignCenter)
            outer_layout.addWidget(placeholder)
            self.cards_layout.addWidget(outer, 0, 0, 1, 4)
            if not self._is_busy:
                self.status_label.setText(
                    f"🔗 {self.page_names[self.current_page]} — No content"
                )
            return

        try:
            md_content = link_file.read_text(encoding="utf-8")
        except Exception as e:
            md_content = f"*(Error reading link.md: {e})*"

        if MARKDOWN_AVAILABLE:
            html_body = markdown.markdown(md_content, extensions=["extra", "nl2br"])
        else:
            html_body = md_content.replace("\n", "<br>")

        # Teal accent -- ties to the mint background behind the AI Assistant
        # icon, distinct from News' indigo without breaking the shared palette.
        full_html = self._build_doc_page_html(html_body, accent="#0d9488", tint="#e6f7f0")

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        from PySide6.QtWidgets import QSizePolicy
        browser.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        browser.setStyleSheet("""
            QTextBrowser {
                background-color: #ffffff;
                border: none;
            }
            QScrollBar:vertical {
                width: 10px;
                background: #f1f2f6;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical {
                background: #c7cad3;
                border-radius: 5px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover { background: #9498a6; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)
        browser.setHtml(full_html)

        outer_layout.addWidget(browser)
        self.cards_layout.addWidget(outer, 0, 0, 1, 4)
        if not self._is_busy:
            self.status_label.setText(
                f"🔗 {self.page_names[self.current_page]} — link.md loaded"
            )

    def refresh_data(self):
        """Refresh data from BoxLink API and save to record.json"""
        # Show loading indicator
        self.show_loading()
        self._begin_busy_status()
        token = self._claim_status_owner()
        self.status_label.setText("🔄 Refreshing data from Box (scanning folders recursively)...")

        # RefreshWorker has no per-item progress -- a large/nested Box folder
        # structure can sit on that one static line for a long time with
        # nothing changing, which looks identical to the app having hung.
        # Tick a heartbeat until _on_refresh_complete takes over (metadata
        # creation, the next phase, already has its own live per-step progress).
        self._start_progress_heartbeat(token, "🔄 Refreshing data from Box (scanning folders recursively)")

        # Create worker
        worker = RefreshWorker(None, self.config_path, self.record_file)
        worker.finished.connect(self._on_refresh_complete)

        # Run in background thread
        thread = Thread(target=worker.run)
        thread.daemon = True
        thread.start()

    def _start_progress_heartbeat(self, token, label):
        """Tick the status bar once a second with elapsed time while a
        long-running step with no progress signal of its own is in flight,
        so it doesn't read as a hang. Call _stop_progress_heartbeat() the
        moment that step finishes or hands off to a phase with its own
        progress updates.
        """
        self._stop_progress_heartbeat()
        self._heartbeat_elapsed_seconds = 0
        self._heartbeat_timer = QTimer(self)
        self._heartbeat_timer.setInterval(1000)
        self._heartbeat_timer.timeout.connect(lambda: self._on_progress_heartbeat_tick(token, label))
        self._heartbeat_timer.start()

    def _on_progress_heartbeat_tick(self, token, label):
        self._heartbeat_elapsed_seconds += 1
        dots = "." * ((self._heartbeat_elapsed_seconds % 3) + 1)
        self._set_status_if_current(token, f"{label}{dots}  ({self._heartbeat_elapsed_seconds}s)")

    def _stop_progress_heartbeat(self):
        if getattr(self, "_heartbeat_timer", None) is not None:
            self._heartbeat_timer.stop()
            self._heartbeat_timer = None
    
    def _show_dotnet_missing_dialog(self):
        """Prompt the user to install the .NET runtime required by BoxAutomate.exe."""
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle(".NET Runtime Required")
        msg_box.setIcon(QMessageBox.Warning)
        msg_box.setText(
            "BoxAutomate.exe could not start because the required .NET runtime "
            "is not installed on this computer.\n\n"
            "Click \"Open Download Page\" to install it, then try again."
        )
        install_btn = msg_box.addButton("Open Download Page", QMessageBox.AcceptRole)
        msg_box.addButton("Cancel", QMessageBox.RejectRole)
        msg_box.setStyleSheet(MESSAGE_BOX_STYLE)
        msg_box.exec()

        if msg_box.clickedButton() == install_btn:
            webbrowser.open(DOTNET_DOWNLOAD_URL)

    def _on_refresh_complete(self, result):
        """Handle refresh completion (runs on main thread)"""
        self._stop_progress_heartbeat()
        success, data, error = result

        try:
            if success:
                # Ensure config-record directory exists
                self.config_path.mkdir(exist_ok=True)
                
                # Save data to record.json
                with open(self.record_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2)
                
                # Count total folders scanned (including nested)
                def count_folders(items):
                    count = 0
                    for item in items:
                        if item.get('type') == 'folder':
                            count += 1
                            if 'contents' in item and 'items' in item['contents']:
                                count += count_folders(item['contents']['items'])
                    return count
                
                total_folders = count_folders(data.get('items', []))
                self.status_label.setText(
                    f"✓ Data refreshed! Now creating metadata files in App_Store..."
                )
                
                # Reload software data without changing the current page
                self.load_software(reset_page=False)
                
                # Start creating metadata files in App_Store
                self._download_to_app_store(data)
            else:
                self.status_label.setText(f"⚠️ Refresh failed: {error}")
                self._report_log(f"Refresh failed: {error}")
                self.hide_loading()
                self._end_busy_status()
                if is_dotnet_missing_error(error):
                    self._show_dotnet_missing_dialog()
        except Exception as e:
            self.status_label.setText(f"⚠️ Error refreshing data: {str(e)}")
            self._report_log(f"Error refreshing data: {e}")
            self.hide_loading()
            self._end_busy_status()

    def _download_to_app_store(self, data):
        """Create metadata JSON files in App_Store directory"""
        app_store_path = Path(__file__).parent.parent.parent / "App_Store"
        
        # Create worker
        worker = AppStoreDownloadWorker(data, app_store_path)
        worker.progress.connect(self._on_download_progress)
        worker.finished.connect(self._on_download_complete)
        
        # Run in background thread
        thread = Thread(target=worker.run)
        thread.daemon = True
        thread.start()
    

    def _on_download_progress(self, message):
        """Handle metadata creation progress updates"""
        self.status_label.setText(f"📝 {message}")
    
    def _cleanup_app_store(self):
        """Remove folders from App_Store that are not in record.json"""
        import shutil
        
        try:
            app_store_path = Path(__file__).parent.parent.parent / "App_Store"
            
            if not app_store_path.exists():
                return 0
            
            # Load record.json to get current software list
            if not self.record_file.exists():
                return 0
            
            with open(self.record_file, 'r', encoding='utf-8') as f:
                record_data = json.load(f)
            
            # Get list of valid folder names from record.json (exact names)
            valid_folders = set()
            if record_data and 'items' in record_data:
                for item in record_data['items']:
                    if item.get('type') == 'folder':
                        folder_name = item.get('name', '')
                        if folder_name:
                            valid_folders.add(folder_name)
            
            # Check each folder in App_Store
            removed_count = 0
            for folder in app_store_path.iterdir():
                if not folder.is_dir():
                    continue
                
                folder_name = folder.name
                
                # Check if folder name exists in valid folders (exact match)
                if folder_name not in valid_folders:
                    try:
                        shutil.rmtree(folder)
                        removed_count += 1
                        self.status_label.setText(f"🗑️ Removing old folder: {folder.name}")
                    except Exception as e:
                        print(f"Failed to remove {folder.name}: {str(e)}")
            
            return removed_count
            
        except Exception as e:
            print(f"Error during App_Store cleanup: {str(e)}")
            return 0
    
    def _on_download_complete(self, result):
        """Handle metadata creation completion"""
        success, message, created, failed, skipped = result
        
        try:
            if success:
                # Clean up App_Store folders that are no longer in record.json
                removed_count = self._cleanup_app_store()
                
                if message:
                    if removed_count > 0:
                        self.status_label.setText(f"✓ Complete! {message}, Removed: {removed_count} old folders")
                    else:
                        self.status_label.setText(f"✓ Complete! {message}")
                else:
                    if removed_count > 0:
                        self.status_label.setText(
                            f"✓ Complete! Created: {created} JSON files, Removed: {removed_count} old folders"
                        )
                    else:
                        self.status_label.setText(
                            f"✓ Complete! Created: {created} JSON files, Skipped: {skipped}, Failed: {failed}"
                        )
                
                # Reload software data to update ComboBoxes and buttons (without changing page)
                self.load_software(reset_page=False)

                # Invalidate store cache so the next visit rescans fresh App_Store folders
                self._store_data_cache = None

                # Refresh the current page (stay on the page user is navigating)
                # This will update all cards with new ComboBox options and button states
                self._display_current_page()
            else:
                self.status_label.setText(f"⚠️ Metadata creation failed: {message}")
                self._report_log(f"Metadata creation failed: {message}")
        except Exception as e:
            self.status_label.setText(f"⚠️ Error: {str(e)}")
            self._report_log(f"Metadata creation error: {e}")
        finally:
            # Hide loading indicator
            self.hide_loading()
            self._end_busy_status()

    def check_version_status(self, folder_path):
        """
        Check if software version is latest by calling API
        
        Args:
            folder_path: Path to software folder
            
        Returns:
            bool: True if latest version, False if update available
            
        TODO: Implement actual API call here
        Example:
            - Read current version from README.md
            - Call API with software name
            - Compare versions
            - Return True/False
        """
        # Placeholder logic - replace with actual API call
        # For now, this is just a demo
        return True
    
    def _get_flow_info(self, sw_folder):
        """Parse App_Store Flow.txt for a Software_Downloaded folder.

        Resolves the icon path (from [Icon] Name= if Flag=True) from the App_Store
        folder, and the execution file path (from [Execution] file=) from the
        Software_Downloaded folder itself.

        Returns:
            (icon_path: Path or None, exec_path: Path or None)
        """
        from .folder_parser import parse_software_folder_name, format_software_name, get_author_raw

        parsed = parse_software_folder_name(sw_folder.name)
        sw_name = format_software_name(parsed)
        author = get_author_raw(parsed)

        app_store_dir = self.software_path.parent / "App_Store" / f"{sw_name}-{author}"
        flow_txt = app_store_dir / "Flow.txt"

        icon_path = None
        exec_path = None

        if not flow_txt.exists():
            # No Flow.txt — fall back to icon.ico copied into Software_Downloaded by Pass 5
            fallback_icon = sw_folder / 'icon.ico'
            if fallback_icon.exists():
                icon_path = fallback_icon
            return icon_path, exec_path

        try:
            current_section = None
            icon_flag = False
            icon_name = None
            exec_name = None

            with open(flow_txt, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith('[') and line.endswith(']'):
                        current_section = line[1:-1].lower()
                        continue
                    if '=' in line:
                        key, _, value = line.partition('=')
                        key = key.strip().lower()
                        value = value.strip()

                        if current_section == 'icon':
                            if key == 'flag':
                                icon_flag = value.lower() == 'true'
                            elif key == 'name':
                                icon_name = value
                        elif current_section == 'execution':
                            if key == 'file':
                                exec_name = value

            # Icon is copied to the Software_Downloaded folder during refresh (Pass 5).
            # Look there first; if not yet copied, fall back to the App_Store copy.
            if icon_flag and icon_name:
                candidate = sw_folder / icon_name
                if candidate.exists():
                    icon_path = candidate
                else:
                    # Not yet copied — use App_Store copy directly
                    app_store_candidate = app_store_dir / icon_name
                    if app_store_candidate.exists():
                        icon_path = app_store_candidate
            else:
                # Flow.txt present but no [Icon] section — fall back to icon.ico
                for ico_name in ('icon.ico',):
                    for search_dir in (sw_folder, app_store_dir):
                        fallback = search_dir / ico_name
                        if fallback.exists():
                            icon_path = fallback
                            break
                    if icon_path:
                        break

            # Execution file lives inside the Software_Downloaded folder
            if exec_name:
                candidate = sw_folder / exec_name
                print(f"[FLOW]  [{sw_folder.name}] exec candidate: {candidate}  exists={candidate.exists()}")
                if candidate.exists():
                    exec_path = candidate

        except Exception as e:
            print(f"Warning: could not parse Flow.txt for {sw_folder.name}: {e}")

        print(f"[FLOW]  [{sw_folder.name}] icon_path={icon_path}  exec_path={exec_path}")
        return icon_path, exec_path

    def load_software(self, reset_page=True):
        """Load all software from Software_Downloaded folder.

        Args:
            reset_page: If True, resets to page 0 and displays. If False, only reloads data.
        """
        self.all_software_data = []

        if not self.software_path.exists():
            self.status_label.setText(f"⚠️ Software folder not found: {self.software_path}")
            return

        folders = [f for f in self.software_path.iterdir() if f.is_dir() and not f.name.startswith('.')]

        if not folders:
            self.status_label.setText("📂 No software found in Software_Downloaded folder")
            return

        count = 0
        for folder in sorted(folders):
            # Read Flow.txt to get icon and execution file
            icon_path, exec_path = self._get_flow_info(folder)

            # Show card only if Flow.txt defines an execution target
            if exec_path:
                is_latest = count != 0  # TODO: replace with real API check

                self.all_software_data.append({
                    'name': folder.name,
                    'exec_path': exec_path,   # resolved from Flow.txt [Execution]
                    'icon_path': icon_path,   # resolved from Flow.txt [Icon]
                    'folder': folder,
                    'is_latest': is_latest
                })
                count += 1

        if count == 0:
            self.status_label.setText("⚠️ No software with a valid Flow.txt found in Software_Downloaded")
            return

        if reset_page:
            self.current_page = 0
            self._display_current_page()
            self._update_sidebar_buttons()

    def _build_installed_tool_list(self):
        """Scan Software_Downloaded and return {software_name: version} for every folder,
        plus this app's own name/version from config-record/version.json.

        Folder names follow Name-V-Version_A-Author (see folder_parser); the same
        parser the Dashboard cards use resolves name/version so this list matches
        what's actually shown to the user. DinosaurList itself is included here
        (rather than only at launch) since UserToolsClient.create() fully replaces
        the stored tool_list on every sync -- adding it just at launch would have
        it dropped again by the very next close/delete sync.
        """
        from .folder_parser import parse_software_folder_name, format_software_name, get_version_raw

        tool_list = {}
        if self.software_path.exists():
            for folder in sorted(self.software_path.iterdir()):
                if not folder.is_dir():
                    continue
                parsed = parse_software_folder_name(folder.name)
                name = format_software_name(parsed)
                tool_list[name] = get_version_raw(parsed)

        tool_list[self.app_version_info["app_name"]] = self.app_version_info["version"]

        return tool_list

    def _ensure_user_api_on_path(self):
        """Make the top-level User-API folder importable (health_client, local_identity,
        info_details, the *_client modules) -- they aren't a package under src/."""
        import sys
        user_api_dir = str(Path(__file__).parent.parent.parent / "User-API")
        if user_api_dir not in sys.path:
            sys.path.insert(0, user_api_dir)

    def _append_launch_log(self, line):
        """Append one timestamped line to today's Log_data/launch_<date>.log.

        Best-effort -- logging failures must never surface to the caller.
        """
        try:
            from datetime import datetime
            log_dir = Path(__file__).parent.parent.parent / "Log_data"
            log_dir.mkdir(exist_ok=True)
            log_file = log_dir / f"launch_{datetime.now():%Y-%m-%d}.log"
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {line}\n")
        except OSError:
            pass

    def _register_app_user(self):
        """Make sure this machine's user is recorded via InfoInstalledClient (db0).

        Tracks who uses DinosaurList itself — distinct from _sync_installed_tools,
        which tracks which tools a user has installed. Looks the user up first
        (GET); if a record already exists, DELETEs it and POSTs a new one so
        both its IP and datetime reflect this launch (the backend only stamps
        datetime on create -- PUT leaves it unchanged), otherwise just POSTs a
        new record. Never raises -- an unreachable API must not block launch.
        """
        self._ensure_user_api_on_path()
        try:
            from local_identity import LocalIdentity
            from info_installed_client import InfoInstalledClient

            identity = LocalIdentity()
            username = identity.get_current_username()
            ip = identity.get_local_ip()

            client = InfoInstalledClient()
            ok, rows = client.list(user_name=username)
            already_registered = bool(ok and rows)

            if already_registered:
                record_id = rows[0].get("id")
                client.delete(record_id)
                post_ok, _ = client.create(user_name=username, ip_address=ip)
                self._append_launch_log(
                    f"INSTALLED-CHECK user={username} ip={ip} status=refreshed POST_ok={post_ok}"
                )
            else:
                post_ok, _ = client.create(user_name=username, ip_address=ip)
                self._append_launch_log(
                    f"INSTALLED-CHECK user={username} ip={ip} status=registered POST_ok={post_ok}"
                )
        except Exception as exc:
            self._append_launch_log(f"INSTALLED-CHECK failed: {exc}")

    def _report_tool_checkin(self, tool_name, version):
        """Report a successful download/update via POST /api/info/details/.

        Only call this once a download has actually succeeded — this feeds the
        Telemetry API's tool check-in stats, so a failed/cancelled download must
        not be recorded. Never raises -- an unreachable API shouldn't disrupt
        the already-completed install. Resolves user_name/ip_address via
        LocalIdentity directly (matching _register_app_user/_sync_installed_tools/
        _report_log) instead of going through the info_details.py wrapper.
        """
        self._ensure_user_api_on_path()
        try:
            from local_identity import LocalIdentity
            from info_details_client import InfoDetailsClient

            identity = LocalIdentity()
            ok, _ = InfoDetailsClient().create(
                tool_name=tool_name,
                version=version,
                user_name=identity.get_current_username(),
                ip_address=identity.get_local_ip(),
            )
            print(f"[INFO-DETAILS] {'OK' if ok else 'FAILED'}")
        except Exception as exc:
            print(f"[INFO-DETAILS] FAILED: {exc}")

    def _update_tool_list_entry(self, tool_name, version):
        """PUT the just-downloaded tool/version into the user's tool_list right away,
        via UserToolsClient.update(), instead of waiting for the next launch/close
        full re-scan (_sync_installed_tools) to pick it up.

        Mirrors the server's merged response into config-record/api.json so the
        local file matches what's actually stored. Never raises -- an unreachable
        API must not disrupt the already-completed install.
        """
        self._ensure_user_api_on_path()
        try:
            from local_identity import LocalIdentity
            from user_tools_client import UserToolsClient

            username = LocalIdentity().get_current_username()
            ok, data = UserToolsClient().update(username, {tool_name: version})
            print(f"[TOOL-SYNC:download] {'OK' if ok else 'FAILED'}")

            if ok and data:
                api_json_path = self.config_path / "api.json"
                with open(api_json_path, 'w', encoding='utf-8') as f:
                    json.dump(
                        {"user_name": data.get("user_name", username), "tool_list": data.get("tool_list", {})},
                        f, indent=2,
                    )
        except Exception as exc:
            print(f"[TOOL-SYNC:download] FAILED: {exc}")

    def _sync_installed_tools(self, event_label="launch"):
        """Write config-record/api.json and POST it via UserToolsClient.

        api.json mirrors the exact payload UserToolsClient.create() sends --
        {"user_name": ..., "tool_list": {...}} -- so it doubles as a local
        record of the last sync. Called on launch and again on close (re-scanned
        each time, since software may have been installed/removed mid-session).
        Never raises -- the Telemetry API being unreachable shouldn't block
        launch or exit.
        """
        self._ensure_user_api_on_path()

        tool_list = self._build_installed_tool_list()

        try:
            from local_identity import LocalIdentity
            from user_tools_client import UserToolsClient

            username = LocalIdentity().get_current_username()

            api_json_path = self.config_path / "api.json"
            with open(api_json_path, 'w', encoding='utf-8') as f:
                json.dump({"user_name": username, "tool_list": tool_list}, f, indent=2)

            ok, _ = UserToolsClient().create(username, tool_list)
            print(f"[TOOL-SYNC:{event_label}] {'OK' if ok else 'FAILED'}")
        except Exception as exc:
            print(f"[TOOL-SYNC:{event_label}] FAILED: {exc}")

    def _report_log(self, log_content, level="ERROR"):
        """POST a timestamped line to the Telemetry API's Info/Logs feature (db4).

        Centralizes visibility into app errors instead of leaving them only in
        each user's local console/Log_data file. The server also stamps its own
        "datetime" column on create, but the timestamp is embedded in log_content
        too so it reads clearly on its own when someone is just skimming raw log
        text. Never raises -- an unreachable API must not disrupt whatever was
        already happening when this was called.
        """
        self._ensure_user_api_on_path()
        try:
            from datetime import datetime
            from local_identity import LocalIdentity
            from info_logs_client import InfoLogsClient

            identity = LocalIdentity()
            stamped_content = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] [{level}] {log_content}"

            ok, _ = InfoLogsClient().create(
                tool_name=self.app_version_info["app_name"],
                version=self.app_version_info["version"],
                user_name=identity.get_current_username(),
                ip_address=identity.get_local_ip(),
                log_content=stamped_content,
            )
            print(f"[INFO-LOGS:{level}] {'OK' if ok else 'FAILED'}")
        except Exception as exc:
            print(f"[INFO-LOGS:{level}] FAILED: {exc}")

    def _resolve_app_store_icon(self, app_store_folder: Path):
        """Return the icon Path for an App_Store folder by reading Flow.txt [Icon].

        Priority:
          1. Flow.txt [Icon] Flag=True, Name=<filename> → <folder>/<filename>
          2. Fallback: <folder>/icon.ico (for software without Flow.txt)
          3. None if nothing found
        """
        flow_txt = app_store_folder / "Flow.txt"
        if flow_txt.exists():
            icon_flag = False
            icon_name = None
            current_section = None
            try:
                with open(flow_txt, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        if line.startswith('[') and line.endswith(']'):
                            current_section = line[1:-1].lower()
                            continue
                        if '=' in line and current_section == 'icon':
                            key, _, value = line.partition('=')
                            key = key.strip().lower()
                            value = value.strip()
                            if key == 'flag':
                                icon_flag = value.lower() == 'true'
                            elif key == 'name':
                                icon_name = value
            except Exception as e:
                print(f"[ICON] Could not parse Flow.txt in {app_store_folder.name}: {e}")

            if icon_flag and icon_name:
                candidate = app_store_folder / icon_name
                print(f"[ICON] [{app_store_folder.name}] Flow.txt icon={icon_name}  exists={candidate.exists()}")
                if candidate.exists():
                    return candidate

        # Fallback: icon.ico (covers software with no Flow.txt)
        fallback = app_store_folder / "icon.ico"
        if fallback.exists():
            print(f"[ICON] [{app_store_folder.name}] fallback icon.ico")
            return fallback

        print(f"[ICON] [{app_store_folder.name}] no icon found")
        return None

    def _build_store_entry(self, folder: Path):
        """Build one App_Store entry (name/author/icon/json). Pure I/O + data —
        safe to run off the UI thread, which is what lets _load_store_software
        fan this out across a thread pool instead of scanning folders one at a time.
        """
        # Parse folder name to get software name and author
        # Expected format: SoftwareName-Author or just SoftwareName
        folder_name = folder.name

        if '-' in folder_name:
            # Split by last hyphen to handle names with hyphens
            parts = folder_name.rsplit('-', 1)
            software_name = parts[0]
            author_name = parts[1] if len(parts) > 1 else "Unknown"
        elif '@' in folder_name:
            # Handle format like SbinValidation@master
            parts = folder_name.split('@')
            software_name = parts[0]
            author_name = parts[1] if len(parts) > 1 else "Unknown"
        else:
            software_name = folder_name
            author_name = "Unknown"

        # Resolve icon via Flow.txt [Icon] Name=, fallback to icon.ico
        icon_path = self._resolve_app_store_icon(folder)

        # Look for JSON metadata file and read folder_id from it
        json_path = folder / f"{folder_name}.json"
        folder_id = ""
        if json_path.exists():
            try:
                import json as _json
                with open(json_path, 'r', encoding='utf-8') as _f:
                    _meta = _json.load(_f)
                folder_id = _meta.get('folder_id', '')
            except Exception:
                pass

        return {
            'name': software_name,
            'author': author_name,
            'icon_path': icon_path,  # already None if not found
            'json_path': json_path if json_path.exists() else None,
            'folder': folder,
            'folder_name': folder_name,
            'folder_id': folder_id,
            'guide_available': self._find_store_guide_path(folder) is not None,
            'readme_available': self._find_store_readme_path(folder) is not None,
        }

    def _load_store_software(self):
        """Load software data from App_Store directory (result is cached until a refresh clears it)"""
        if self._store_data_cache is not None:
            return self._store_data_cache

        app_store_path = Path(__file__).parent.parent.parent / "App_Store"

        if not app_store_path.exists():
            return []

        folders = [f for f in sorted(app_store_path.iterdir()) if f.is_dir()]

        if not folders:
            self._store_data_cache = []
            return self._store_data_cache

        # Each folder's work is a handful of small file reads (Flow.txt, icon
        # existence checks, JSON) — I/O-bound, so a thread pool scans folders
        # concurrently instead of paying each folder's disk latency in series.
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(16, len(folders))) as pool:
            store_data = list(pool.map(self._build_store_entry, folders))

        self._store_data_cache = store_data
        return store_data
    
    def _on_update_download(self, software_name, version, file_id):
        """Handle update button click from Page 1 card"""
        # Get author name from App_Store
        app_store_path = Path("App_Store")
        author_name = "Unknown"
        
        for folder in app_store_path.iterdir():
            if folder.is_dir() and folder.name.startswith(software_name):
                parts = folder.name.split('-')
                if len(parts) >= 2:
                    author_name = '-'.join(parts[1:])
                break
        
        # Check if software is already installed and get current version
        software_path = Path("Software_Downloaded")
        current_version = None
        for folder in software_path.iterdir():
            if folder.is_dir() and folder.name.startswith(f"{software_name}_V-"):
                # Extract version from folder name (e.g., "BandMaster_V-1.0.0.0_A-SuetLi")
                parts = folder.name.split('_V-')
                if len(parts) >= 2:
                    version_part = parts[1].split('_A-')[0]
                    current_version = version_part
                break
        
        # Determine if this is an update or reinstall
        is_update = current_version and current_version != version
        
        # Call the download logic with context
        self._start_download(software_name, author_name, version, file_id, from_page1=True, is_update=is_update)
    
    def _on_store_download(self, software_name, version, file_id):
        """Handle download request from store card"""
        # Extract author name from software_name (format: "Name-Author" from store)
        # We need to get the author from the App_Store folder structure
        app_store_path = Path("App_Store")
        author_name = "Unknown"
        
        # Find the matching folder in App_Store
        for folder in app_store_path.iterdir():
            if folder.is_dir() and folder.name.startswith(software_name):
                # Extract author from folder name (e.g., "BandMaster-SuetLi")
                parts = folder.name.split('-')
                if len(parts) >= 2:
                    author_name = '-'.join(parts[1:])  # Handle names with hyphens
                break
        
        # Call the common download logic (from Page 2, always a new install/update)
        self._start_download(software_name, author_name, version, file_id, from_page1=False, is_update=False)
    
    def _find_store_guide_path(self, folder: Path):
        """Resolve the Store 'Details' guide file for *folder*, from Flow.txt
        [Guide] file=. Returns the Path if declared and present, else None.
        """
        flow_txt = folder / "Flow.txt"
        if not flow_txt.exists():
            return None

        guide_filename = None
        current_section = None
        try:
            with open(flow_txt, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith('[') and line.endswith(']'):
                        current_section = line[1:-1].lower()
                        continue
                    if current_section == 'guide' and '=' in line:
                        key, _, value = line.partition('=')
                        if key.strip().lower() == 'file':
                            v = value.strip()
                            if v and v.lower() not in ('none', 'false', ''):
                                guide_filename = v
                            break
        except Exception as e:
            print(f"[GUIDE] Error reading Flow.txt for {folder.name}: {e}")

        if not guide_filename:
            return None

        candidate = folder / guide_filename
        return candidate if candidate.exists() else None

    def _find_store_readme_path(self, folder: Path):
        """Resolve the Store 'ReadMe' file for *folder* (Flow.txt [ReadMe]
        Flag=/file=, falling back to a bare README.md). Returns the Path if
        present, else None.
        """
        flow_txt = folder / "Flow.txt"
        readme_flag = False
        readme_filename = None

        if flow_txt.exists():
            current_section = None
            try:
                with open(flow_txt, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        if line.startswith('[') and line.endswith(']'):
                            current_section = line[1:-1].lower()
                            continue
                        if current_section == 'readme' and '=' in line:
                            key, _, value = line.partition('=')
                            key = key.strip().lower()
                            value = value.strip()
                            if key == 'flag':
                                readme_flag = value.lower() == 'true'
                            elif key == 'file':
                                readme_filename = value
            except Exception as e:
                print(f"[README] Error reading Flow.txt for {folder.name}: {e}")

        if readme_flag and readme_filename:
            candidate = folder / readme_filename
            if candidate.exists():
                return candidate

        fallback = folder / "README.md"
        return fallback if fallback.exists() else None

    def _on_store_guide_clicked(self, software_name):
        """Handle Details button click from store card.
        Opens the guide file whose name is read from Flow.txt [Guide] file=."""
        import os

        app_store_path = Path(__file__).parent.parent.parent / "App_Store"
        guide_path = None

        # Find the matching App_Store folder and resolve its guide file
        for folder in app_store_path.iterdir():
            if not folder.is_dir() or not folder.name.startswith(software_name):
                continue
            guide_path = self._find_store_guide_path(folder)
            if guide_path:
                break

        if guide_path:
            try:
                os.startfile(str(guide_path))
                self.status_label.setText(f"📖 Opening guide for {software_name}...")
            except Exception as e:
                msg_box = QMessageBox(self)
                msg_box.setWindowTitle("Error Opening Guide")
                msg_box.setIcon(QMessageBox.Warning)
                msg_box.setText(f"Failed to open guide for {software_name}\n\nError: {str(e)}")
                msg_box.setStyleSheet(MESSAGE_BOX_STYLE)
                msg_box.exec()
        else:
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle(f"{software_name} - Guide Not Found")
            msg_box.setIcon(QMessageBox.Information)
            msg_box.setText(
                f"No guide found for {software_name}.\n\n"
                f"Make sure Flow.txt has a [Guide] file= entry and the file has been downloaded.\n"
                f"Click Refresh on Page 2 to download the latest files from Box."
            )
            msg_box.setStyleSheet(MESSAGE_BOX_STYLE)
            msg_box.exec()

    def _on_store_readme_clicked(self, software_name):
        """Handle ReadMe label click from a Store card.

        Opens the readme file (per Flow.txt [ReadMe] Flag=/file=, falling back
        to a bare README.md in the App_Store folder) in the same in-app
        GitHub-style viewer used by the Dashboard's ReadMe button. Store items
        may not be installed yet, so this only ever looks in App_Store --
        there's no Software_Downloaded folder to fall back to here.
        """
        app_store_path = Path(__file__).parent.parent.parent / "App_Store"
        readme_path = None
        base_folder = None

        for folder in app_store_path.iterdir():
            if not folder.is_dir() or not folder.name.startswith(software_name):
                continue
            found = self._find_store_readme_path(folder)
            if found:
                readme_path = found
                base_folder = folder
                break

        if readme_path and readme_path.exists():
            try:
                with open(readme_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                viewer = ReadmeViewer(
                    f"{software_name} - README",
                    content,
                    folder_path=str(base_folder),
                    parent=self
                )
                viewer.exec()
            except Exception as e:
                msg_box = QMessageBox(self)
                msg_box.setWindowTitle("Error")
                msg_box.setText(f"Could not read readme: {e}")
                msg_box.setIcon(QMessageBox.Warning)
                msg_box.setStyleSheet(MESSAGE_BOX_STYLE)
                msg_box.exec()
        else:
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("No README")
            msg_box.setText(
                f"No README found for {software_name}.\n\n"
                f"Make sure Flow.txt has a [ReadMe] section with Flag=True "
                f"and the file has been downloaded via Refresh."
            )
            msg_box.setIcon(QMessageBox.Information)
            msg_box.setStyleSheet(MESSAGE_BOX_STYLE)
            msg_box.exec()

    def _on_card_refresh_clicked(self, folder_name, folder_id):
        """Handle the tiny per-card refresh button on a Store card."""
        if not folder_id:
            self.status_label.setText(
                f"⚠️ No folder_id for '{folder_name}'. Run a full Refresh first."
            )
            return

        # Mark the card as refreshing
        card = getattr(self, 'store_card_references', {}).get(folder_name)
        if card:
            card.set_refreshing(True)

        self.show_loading()
        self._begin_busy_status()
        self.status_label.setText(f"🔄 Syncing '{folder_name}' from Box...")

        # Keep strong references so the workers are not garbage-collected
        # before the background thread delivers its finished signal.
        self._card_scan_worker = SingleCardRefreshWorker(
            folder_id, folder_name, self.config_path, self.record_file
        )
        self._card_scan_worker.finished.connect(
            lambda result, fn=folder_name: self._on_single_card_scan_complete(result, fn)
        )

        self._card_scan_thread = Thread(target=self._card_scan_worker.run)
        self._card_scan_thread.daemon = True
        self._card_scan_thread.start()

    def _on_single_card_scan_complete(self, result, folder_name):
        """Called when SingleCardRefreshWorker finishes scanning Box."""
        success, item, error = result

        if not success:
            self.status_label.setText(f"⚠️ Sync failed for '{folder_name}': {error}")
            self._report_log(f"Sync failed for '{folder_name}': {error}")
            self._finish_card_refresh(folder_name, success=False)
            if is_dotnet_missing_error(error):
                self._show_dotnet_missing_dialog()
            return

        self._update_record_json(item)
        self.status_label.setText(f"📝 Updating metadata for '{folder_name}'...")

        app_store_path = Path(__file__).parent.parent.parent / "App_Store"

        # Keep strong reference to prevent GC before thread signals back.
        self._card_download_worker = SingleCardDownloadWorker(item, app_store_path)
        self._card_download_worker.progress.connect(
            lambda msg: self.status_label.setText(f"📝 {msg}")
        )
        self._card_download_worker.finished.connect(
            lambda result, fn=folder_name: self._on_single_card_download_complete(result, fn)
        )

        self._card_download_thread = Thread(target=self._card_download_worker.run)
        self._card_download_thread.daemon = True
        self._card_download_thread.start()

    def _on_single_card_download_complete(self, result, folder_name):
        """Called when SingleCardDownloadWorker finishes."""
        success, message, created, failed, skipped = result

        try:
            if success:
                self.status_label.setText(
                    f"✓ '{folder_name}' synced! ({message})"
                )
            else:
                self.status_label.setText(
                    f"⚠️ Sync incomplete for '{folder_name}': {message}"
                )
        finally:
            self._finish_card_refresh(folder_name, success=success)

    def _finish_card_refresh(self, folder_name, success=True):
        """Re-enable the card's refresh button and refresh only that card's UI."""
        self.hide_loading()
        self._end_busy_status()

        card = getattr(self, 'store_card_references', {}).get(folder_name)
        if card:
            card.set_refreshing(False)

        # Reload only this card's data (icon + versions) without rebuilding the whole page
        app_store_path = Path(__file__).parent.parent.parent / "App_Store"
        folder_path = app_store_path / folder_name

        if card and folder_path.exists():
            # Refresh icon — directly use the stored icon_label reference on the card
            new_icon = self._resolve_app_store_icon(folder_path)
            if new_icon and new_icon.exists() and hasattr(card, 'icon_label'):
                from PySide6.QtGui import QIcon
                icon = QIcon(str(new_icon))
                sizes = icon.availableSizes()
                if sizes:
                    largest = max(sizes, key=lambda s: s.width() * s.height())
                    pixmap = icon.pixmap(largest)
                    if pixmap and not pixmap.isNull():
                        scaled = pixmap.scaled(90, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                        card.icon_label.setPixmap(scaled)

            # Refresh the versions combo by reloading the JSON
            json_path = folder_path / f"{folder_name}.json"
            if json_path.exists():
                card.json_path = json_path
                card.versions_data.clear()
                card._load_versions()
                if hasattr(card, 'version_combo'):
                    card.version_combo.clear()
                    for version, file_id in card.versions_data:
                        card.version_combo.addItem(version, file_id)

    def _update_record_json(self, item):
        """Update the entry for a single app in record.json with fresh Box data."""
        try:
            if not self.record_file.exists():
                return
            with open(self.record_file, 'r', encoding='utf-8') as f:
                record_data = json.load(f)

            folder_name = item.get('name')
            items = record_data.get('items', [])
            for i, existing in enumerate(items):
                if existing.get('name') == folder_name and existing.get('type') == 'folder':
                    # Preserve top-level metadata fields, only overwrite contents
                    items[i] = {**existing, **item}
                    break
            else:
                # App not in record.json yet — append it
                items.append(item)
                record_data['item_count'] = len(items)

            record_data['items'] = items
            with open(self.record_file, 'w', encoding='utf-8') as f:
                json.dump(record_data, f, indent=2)
        except Exception as e:
            print(f"[record.json] Failed to update entry for '{item.get('name')}': {e}")

    # ── Per-card refresh for Page 1 (Dashboard) ──────────────────────────────

    def _on_dashboard_card_refresh_clicked(self, folder_name, folder_id):
        """Handle the tiny ⟳ button on a Dashboard card."""
        if not folder_id:
            self.status_label.setText(
                f"⚠️ No folder_id for '{folder_name}'. Run a full Refresh first."
            )
            return

        folder_path_str = self.dashboard_folder_name_map.get(folder_name)
        card = self.card_references.get(folder_path_str) if folder_path_str else None
        if card:
            card.set_refreshing(True)

        self.show_loading()
        self._begin_busy_status()
        self.status_label.setText(f"🔄 Syncing '{folder_name}' from Box...")

        self._dash_scan_worker = SingleCardRefreshWorker(
            folder_id, folder_name, self.config_path, self.record_file
        )
        self._dash_scan_worker.finished.connect(
            lambda result, fn=folder_name: self._on_dashboard_card_scan_complete(result, fn)
        )
        self._dash_scan_thread = Thread(target=self._dash_scan_worker.run)
        self._dash_scan_thread.daemon = True
        self._dash_scan_thread.start()

    def _on_dashboard_card_scan_complete(self, result, folder_name):
        """Called when the Box scan finishes for a Dashboard card."""
        success, item, error = result

        if not success:
            self.status_label.setText(f"⚠️ Sync failed for '{folder_name}': {error}")
            self._report_log(f"Sync failed for '{folder_name}': {error}")
            self._finish_dashboard_card_refresh(folder_name, success=False)
            if is_dotnet_missing_error(error):
                self._show_dotnet_missing_dialog()
            return

        self._update_record_json(item)
        self.status_label.setText(f"📝 Updating metadata for '{folder_name}'...")

        app_store_path = Path(__file__).parent.parent.parent / "App_Store"
        self._dash_download_worker = SingleCardDownloadWorker(item, app_store_path, skip_guide=True)
        self._dash_download_worker.progress.connect(
            lambda msg: self.status_label.setText(f"📝 {msg}")
        )
        self._dash_download_worker.finished.connect(
            lambda result, fn=folder_name: self._on_dashboard_card_download_complete(result, fn)
        )
        self._dash_download_thread = Thread(target=self._dash_download_worker.run)
        self._dash_download_thread.daemon = True
        self._dash_download_thread.start()

    def _on_dashboard_card_download_complete(self, result, folder_name):
        """Called when the asset download finishes for a Dashboard card."""
        success, message, *_ = result
        if success:
            self.status_label.setText(f"✓ '{folder_name}' synced! ({message})")
        else:
            self.status_label.setText(f"⚠️ Sync incomplete for '{folder_name}': {message}")
            self._report_log(f"Sync incomplete for '{folder_name}': {message}")
        self._finish_dashboard_card_refresh(folder_name, success=success)

    def _finish_dashboard_card_refresh(self, folder_name, success=True):
        """Re-enable the card button and live-update icon + version badge."""
        self.hide_loading()
        self._end_busy_status()

        folder_path_str = self.dashboard_folder_name_map.get(folder_name)
        card = self.card_references.get(folder_path_str) if folder_path_str else None

        if card:
            card.set_refreshing(False)

        if card and success:
            app_store_path = Path(__file__).parent.parent.parent / "App_Store"
            app_store_folder = app_store_path / folder_name

            # Refresh icon from updated App_Store folder
            new_icon = self._resolve_app_store_icon(app_store_folder)
            if new_icon and new_icon.exists():
                card.refresh_icon(new_icon)

            # Reload version dropdown from the updated App_Store JSON
            app_store_json = app_store_folder / f"{folder_name}.json"
            if app_store_json.exists():
                card.refresh_versions_from_app_store(app_store_json)

            # Re-check version badge (reads App_Store JSON which was just updated)
            is_latest = card._check_version_status()
            card.update_version_status(is_latest)

    def _start_download(self, software_name, author_name, version, file_id, from_page1=False, is_update=False):
        """Common download logic for both Page 1 (Update) and Page 2 (Store) downloads"""
        # Confirm download with appropriate message
        msg_box = QMessageBox(self)
        
        if from_page1 and is_update:
            msg_box.setWindowTitle("Confirm Download")
            msg_box.setText(f"Download and install {software_name} version {version}?\n\n"
                           f"This will download the selected version from Box and install it.\n\n"
                           f"Existing files will be overwritten (shortcuts and virtual environments will be preserved).")
        elif from_page1 and not is_update:
            msg_box.setWindowTitle("Confirm Download")
            msg_box.setText(f"Download and reinstall {software_name} {version}?\n\n"
                           f"This will download the selected version from Box and reinstall it.\n\n"
                           f"Existing files will be overwritten (shortcuts and virtual environments will be preserved).")
        else:
            msg_box.setWindowTitle("Confirm Download")
            msg_box.setText(f"Download and install {software_name} {version}?\n\n"
                           f"This will download the software from Box and install it to Software_Downloaded folder.\n\n"
                           f"If already installed, files will be overwritten.")
        
        msg_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg_box.setDefaultButton(QMessageBox.No)
        msg_box.setStyleSheet(MESSAGE_BOX_STYLE)
        reply = msg_box.exec()
        
        if reply != QMessageBox.Yes:
            return
        
        # Start download in background thread
        self.status_label.setText(f"📥 Downloading {software_name} {version}...")

        # Show loading GIF
        self.show_loading()
        self._begin_busy_status()

        self.download_worker = DownloadInstallWorker(
            software_name=software_name,
            author_name=author_name,
            version=version,
            file_id=file_id,
            software_path=str(self.software_path)
        )
        
        # Connect signals
        self.download_worker.progress.connect(self._on_download_progress)
        self.download_worker.finished.connect(self._on_download_finished)
        
        # Run in thread
        self.download_thread = Thread(target=self.download_worker.run)
        self.download_thread.start()
    
    def _on_download_progress(self, message):
        """Update status with download progress"""
        self.status_label.setText(message)
    
    def _on_download_finished(self, success, message):
        """Handle download completion"""
        # Hide loading GIF
        self.hide_loading()
        
        if success:
            self.status_label.setText(f"✅ {message}")

            # Report the check-in and refresh the tool_list entry only now that the
            # download actually succeeded — a failed/cancelled download shouldn't be
            # recorded as tool usage. Covers all three trigger points (Store tab's
            # Download button, Dashboard's Update/Latest button) since they all funnel
            # through this same completion handler.
            self._report_tool_checkin(self.download_worker.software_name, self.download_worker.version)
            self._update_tool_list_entry(self.download_worker.software_name, self.download_worker.version)

            # Auto-refresh software data to show newly installed software (without changing page)
            self.load_software(reset_page=False)
            
            # Refresh the current page display (stay on current page)
            self._display_current_page()
            self._end_busy_status()

            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("Installation Complete")
            msg_box.setIcon(QMessageBox.Information)
            msg_box.setText(f"{message}\n\nThe software has been installed to Software_Downloaded folder.\n\n"
                           f"Dashboard has been refreshed automatically.")
            msg_box.setStyleSheet(MESSAGE_BOX_STYLE)
            msg_box.exec()
        else:
            self.status_label.setText(f"❌ {message}")
            self._report_log(f"Installation failed: {message}")
            self._end_busy_status()
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("Installation Failed")
            msg_box.setIcon(QMessageBox.Critical)
            msg_box.setText(f"{message}\n\nPlease try again or check the error log.")
            msg_box.setStyleSheet(MESSAGE_BOX_STYLE)
            msg_box.exec()
    
    def open_folder_location(self, folder_path):
        """Open folder location in Windows Explorer"""
        folder = Path(folder_path)
        
        if folder.exists():
            try:
                # Open folder in Windows Explorer
                os.startfile(str(folder))
                self.status_label.setText(f"📁 Opened folder: {folder.name}")
            except Exception as e:
                QMessageBox.warning(
                    self,
                    "Open Folder Error",
                    f"Failed to open folder:\n{str(e)}"
                )
        else:
            QMessageBox.warning(
                self,
                "Folder Not Found",
                f"Folder does not exist:\n{folder}"
            )
    
    def launch_software(self, folder_path):
        """Launch software using exec_path from Flow.txt [Execution] file=.

        Uses subprocess.Popen with cwd set to the software folder so that
        relative paths inside run.cmd (e.g. TP.json) resolve correctly —
        identical behaviour to double-clicking the file in Explorer.
        """
        import subprocess

        # Claim the status bar so a stale progress/completion callback from an
        # older background op (e.g. a delete still finishing up) can't overwrite
        # the launch feedback this action is about to show.
        self._claim_status_owner()

        print(f"\n{'='*60}")
        print(f"[LAUNCH] Requested folder_path : {folder_path}")
        print(f"[LAUNCH] all_software_data has {len(self.all_software_data)} entries:")
        for sw in self.all_software_data:
            print(f"         folder={sw['folder']}  exec={sw.get('exec_path')}  icon={sw.get('icon_path')}")

        exec_path = None
        matched_sw = None
        for sw in self.all_software_data:
            if str(sw['folder']) == folder_path:
                exec_path = sw.get('exec_path')
                matched_sw = sw
                break

        print(f"[LAUNCH] Matched entry        : {matched_sw}")
        print(f"[LAUNCH] exec_path resolved   : {exec_path}")
        if exec_path:
            print(f"[LAUNCH] exec_path.exists()  : {exec_path.exists()}")
            print(f"[LAUNCH] cwd (parent)        : {exec_path.parent}")

        if exec_path and exec_path.exists():
            try:
                cwd   = str(exec_path.parent)
                suffix = exec_path.suffix.lower()

                # Native executables and scripts are launched via subprocess so we
                # can set the working directory and get a PID back.
                # Anything else (e.g. .jmpaddin, .mlappinstall, .xlsx …) must be
                # opened through the Windows shell so that the OS hands it to the
                # correct registered application (JMP, MATLAB, Excel, …).
                NATIVE_TYPES = {'.exe', '.bat', '.cmd', '.ps1'}

                if suffix not in NATIVE_TYPES:
                    print(f"[LAUNCH] File type           : {suffix} → shell-open (non-native)")
                    import ctypes
                    ret = ctypes.windll.shell32.ShellExecuteW(
                        None, "open", str(exec_path), None, cwd, 1
                    )
                    if ret <= 32:
                        raise OSError(f"ShellExecuteW returned error code {ret} for '{exec_path.name}'")
                    print(f"[LAUNCH] Shell-open succeeded (ShellExecute ret={ret})")
                    self.status_label.setText(f"🚀 Opened {Path(folder_path).name}")

                else:
                    if suffix in ('.bat', '.cmd', '.ps1'):
                        cmd = ['cmd', '/c', str(exec_path)]
                    else:
                        cmd = [str(exec_path)]

                    print(f"[LAUNCH] File type           : {suffix}")
                    print(f"[LAUNCH] Running command     : {cmd}")
                    print(f"[LAUNCH] Working directory   : {cwd}")
                    proc = subprocess.Popen(
                        cmd,
                        cwd=cwd,
                        creationflags=subprocess.CREATE_NEW_CONSOLE,
                    )
                    print(f"[LAUNCH] Process started     : PID={proc.pid}")
                    self.status_label.setText(f"🚀 Launched {Path(folder_path).name}")

            except OSError as e:
                print(f"[LAUNCH] ERROR               : {e}")
                if getattr(e, 'winerror', None) == 740:
                    print(f"[LAUNCH] Elevation required — retrying with ShellExecute runas...")
                    try:
                        import ctypes
                        ret = ctypes.windll.shell32.ShellExecuteW(
                            None, "runas", str(exec_path), None, str(exec_path.parent), 1
                        )
                        if ret <= 32:
                            raise RuntimeError(f"ShellExecute returned error code {ret}")
                        print(f"[LAUNCH] Elevated launch succeeded (ShellExecute ret={ret})")
                        self.status_label.setText(f"🚀 Launched {Path(folder_path).name} (elevated)")
                    except Exception as e2:
                        print(f"[LAUNCH] Elevated launch failed: {e2}")
                        self._report_log(f"Elevated launch failed for '{folder_path}': {e2}")
                        QMessageBox.critical(
                            self, "Launch Error",
                            f"'{exec_path.name}' requires administrator privileges.\n\n"
                            f"UAC elevation failed:\n{str(e2)}"
                        )
                else:
                    self._report_log(f"Launch failed for '{folder_path}': {e}")
                    QMessageBox.critical(self, "Launch Error", f"Failed to launch:\n{str(e)}")
        else:
            reason = "exec_path is None" if not exec_path else f"file not found: {exec_path}"
            print(f"[LAUNCH] ABORTED             : {reason}")
            self._report_log(f"Launch aborted for '{folder_path}': {reason}")
            QMessageBox.warning(
                self,
                "Launch Error",
                f"Execution file not found for {Path(folder_path).name}.\n"
                f"Check Flow.txt [Execution] file= in the App_Store folder."
            )
        print(f"{'='*60}\n")
    
    def delete_software(self, folder_path):
        """Delete the software folder from Software_Downloaded after confirmation.

        The actual deletion runs in a background thread so the UI stays responsive.
        """
        folder = Path(folder_path)
        print(f"\n[DELETE] Requested path : {folder}")
        print(f"[DELETE] Folder exists  : {folder.exists()}")

        if not folder.exists():
            # Folder is already gone -- most likely deleted manually outside the
            # app (e.g. via Explorer). The stale card would otherwise sit on the
            # Dashboard forever (a warning dialog alone doesn't remove it), and
            # every future click on it would just repeat this same warning with
            # nothing visibly changing. Clean it up immediately instead.
            print(f"[DELETE] Folder already missing on disk — removing stale card: {folder}")
            self._remove_dashboard_card(str(folder))
            self._sync_installed_tools("delete")
            return

        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Confirm Delete")
        msg_box.setIcon(QMessageBox.Warning)
        msg_box.setText(
            f"Are you sure you want to delete <b>{folder.name}</b>?<br><br>"
            f"This will permanently remove all files inside:<br>"
            f"<code>{folder}</code>"
        )
        msg_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg_box.setDefaultButton(QMessageBox.No)
        msg_box.setStyleSheet(MESSAGE_BOX_STYLE)

        if msg_box.exec() != QMessageBox.Yes:
            print(f"[DELETE] Cancelled by user")
            return

        # ── Show immediate feedback and block further interactions ────────────
        self.show_loading()
        self._begin_busy_status()
        token = self._claim_status_owner()
        self.status_label.setText(f"🗑️ Deleting '{folder.name}'…  Please wait.")
        self.refresh_btn.setEnabled(False)

        # ── Run deletion in background ────────────────────────────────────────
        # Keep worker/thread on self -- local variables here would be the only
        # Python reference once this method returns, so the worker could be
        # garbage-collected right as it finishes, dropping the queued
        # cross-thread `finished` signal before the UI ever sees it (the
        # delete itself still succeeds on disk, but the GIF/status hang forever).
        self._delete_worker = DeleteWorker(str(folder))
        self._delete_worker.progress.connect(lambda msg: self._set_status_if_current(token, f"🗑️ {msg}"))
        self._delete_worker.finished.connect(
            lambda success, message: self._on_delete_complete(str(folder), success, message)
        )

        self._delete_thread = Thread(target=self._delete_worker.run, daemon=True)
        self._delete_thread.start()

        # shutil.rmtree gives no per-file progress, so a large folder can sit on
        # one status line for a long time with nothing visibly changing -- tick
        # the text every second so it doesn't look like the app has hung.
        self._delete_elapsed_seconds = 0
        self._delete_heartbeat_timer = QTimer(self)
        self._delete_heartbeat_timer.setInterval(1000)
        self._delete_heartbeat_timer.timeout.connect(
            lambda: self._on_delete_heartbeat(token, folder.name)
        )
        self._delete_heartbeat_timer.start()

    def _on_delete_heartbeat(self, token, folder_name):
        """Tick the status bar once a second while a delete is in progress."""
        self._delete_elapsed_seconds += 1
        dots = "." * ((self._delete_elapsed_seconds % 3) + 1)
        self._set_status_if_current(
            token, f"🗑️ Deleting '{folder_name}'{dots}  ({self._delete_elapsed_seconds}s)"
        )

    def _set_status_if_current(self, token, text):
        """Write to status_label only if *token* still owns the status bar.

        Prevents a stale async callback (e.g. delete progress) from overwriting
        text set by a newer action the user took in the meantime (e.g. launching
        a different app) — see _claim_status_owner.
        """
        if token == self._status_owner_token:
            self.status_label.setText(text)

    def _on_delete_complete(self, folder_path_str, success: bool, message: str):
        """Called on the main thread when background deletion finishes.

        The folder's existence on disk (not the worker's self-reported success
        flag) decides the UI update: if it's gone, the card is removed
        immediately without waiting on a full Software_Downloaded re-scan;
        otherwise a full reload runs so the dashboard still reflects reality.
        Busy status is released in a finally block so a failure while updating
        the UI can't leave it stuck for every action after this one.
        """
        self.hide_loading()
        self.refresh_btn.setEnabled(True)

        if getattr(self, "_delete_heartbeat_timer", None) is not None:
            self._delete_heartbeat_timer.stop()
            self._delete_heartbeat_timer = None

        folder_gone = not Path(folder_path_str).exists()
        folder_name = Path(folder_path_str).name

        if not success and not folder_gone:
            print(f"[DELETE] Non-fatal error reported: {message}")
            self._report_log(f"Delete failed for '{folder_name}': {message}")

        try:
            if folder_gone:
                self._remove_dashboard_card(folder_path_str)
            else:
                self.load_software(reset_page=False)
                self._display_current_page()
        except Exception as exc:
            print(f"[DELETE] UI refresh after delete failed: {exc}")
            self._report_log(f"UI refresh after delete failed for '{folder_name}': {exc}")
        finally:
            self._end_busy_status()

        if folder_gone:
            self.status_label.setText(f"✅ Deleted '{folder_name}' successfully.")
        else:
            self.status_label.setText(f"⚠️ Could not delete '{folder_name}' — see console log.")

        # Re-sync the tool_list from the current (post-delete) folder state --
        # UserToolsClient has no "remove one key" call, so a full re-scan + POST
        # replace is how a deletion gets reflected server-side. Run unconditionally
        # (not just on success) since the folder is confirmed gone either way.
        self._sync_installed_tools("delete")

    def _remove_dashboard_card(self, folder_path_str):
        """Remove a single card from the Dashboard grid without a full re-scan.

        Called once a deleted folder is confirmed gone from disk, so the card
        disappears immediately instead of waiting on _sync_installed_tools /
        a fresh load_software() pass. Keeps card_references, all_software_data,
        and dashboard_folder_name_map in sync so a later reload stays consistent.
        """
        card = self.card_references.pop(folder_path_str, None)

        stale_keys = [k for k, v in self.dashboard_folder_name_map.items() if v == folder_path_str]
        for k in stale_keys:
            del self.dashboard_folder_name_map[k]

        self.all_software_data = [
            sw for sw in self.all_software_data if str(sw['folder']) != folder_path_str
        ]

        if card is None:
            return

        self.cards_layout.removeWidget(card)
        card.setParent(None)
        card.deleteLater()

        if self.current_page != 0:
            return

        # Re-pack the remaining cards (respecting any active search filter)
        # into a compact 4-column grid.
        while self.cards_layout.count():
            self.cards_layout.takeAt(0)

        filter_text = self.filter_edit.text()
        row = col = 0
        matched = 0
        for remaining_card in self.card_references.values():
            if self._matches_filter(remaining_card.display_name, filter_text, remaining_card.author_name):
                self.cards_layout.addWidget(remaining_card, row, col)
                remaining_card.show()
                matched += 1
                col += 1
                if col >= self._current_card_columns():
                    col = 0
                    row += 1
            else:
                remaining_card.hide()

        if not self._is_busy:
            total = len(self.card_references)
            if filter_text.strip():
                self.status_label.setText(
                    f"🔍 '{filter_text.strip()}' — {matched} of {total} installed software matched"
                )
            else:
                self.status_label.setText(
                    f"✓ Showing {total} software application(s) | {self.page_names[0]}"
                )

    def show_version_info(self, folder_path):
        """Show version information from README.md (triggered by version label)"""
        folder = Path(folder_path)
        readme_path = folder / "README.md"
        
        if readme_path.exists():
            try:
                with open(readme_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                msg_box = QMessageBox(self)
                msg_box.setWindowTitle(f'{folder.name} - Version Information')
                msg_box.setText(content)
                msg_box.setIcon(QMessageBox.Information)
                msg_box.setStyleSheet(MESSAGE_BOX_STYLE)
                msg_box.exec()
            except Exception as e:
                msg_box = QMessageBox(self)
                msg_box.setWindowTitle("Error")
                msg_box.setText(f"Could not read README.md: {e}")
                msg_box.setIcon(QMessageBox.Warning)
                msg_box.setStyleSheet(MESSAGE_BOX_STYLE)
                msg_box.exec()
        else:
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("No Information")
            msg_box.setText(f"No README.md found for {folder.name}")
            msg_box.setIcon(QMessageBox.Information)
            msg_box.setStyleSheet(MESSAGE_BOX_STYLE)
            msg_box.exec()
    
    def _find_app_store_readme(self, sw_folder: Path):
        """Return (readme_path, base_folder) from App_Store via Flow.txt [ReadMe].

        Looks up App_Store/<name>-<author>/Flow.txt, reads [ReadMe] Flag= and file=,
        and returns the readme file path and its containing folder if found.
        Returns (None, None) if not available.
        """
        from .folder_parser import parse_software_folder_name, format_software_name, get_author_raw

        parsed   = parse_software_folder_name(sw_folder.name)
        sw_name  = format_software_name(parsed)
        author   = get_author_raw(parsed)

        app_store_dir = self.software_path.parent / "App_Store" / f"{sw_name}-{author}"
        flow_txt = app_store_dir / "Flow.txt"

        if not flow_txt.exists():
            return None, None

        readme_flag = False
        readme_filename = None
        current_section = None

        try:
            with open(flow_txt, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith('[') and line.endswith(']'):
                        current_section = line[1:-1].lower()
                        continue
                    if current_section == 'readme' and '=' in line:
                        key, _, value = line.partition('=')
                        key   = key.strip().lower()
                        value = value.strip()
                        if key == 'flag':
                            readme_flag = value.lower() == 'true'
                        elif key == 'file':
                            readme_filename = value
        except Exception as e:
            print(f"[README] Could not parse Flow.txt for {sw_folder.name}: {e}")
            return None, None

        if readme_flag and readme_filename:
            candidate = app_store_dir / readme_filename
            if candidate.exists():
                return candidate, app_store_dir

        return None, None

    def _dashboard_readme_exists(self, folder: Path) -> bool:
        """True if show_readme() would find a README for *folder* -- used to
        grey out the Dashboard ReadMe button upfront instead of only failing
        after the click.
        """
        readme_path, _ = self._find_app_store_readme(folder)
        if readme_path is None:
            readme_path = folder / "README.md"
        return readme_path.exists()

    def show_readme(self, folder_path):
        """Show README content in GitHub-style viewer (triggered by ReadMe button).

        Priority:
          1. App_Store/<name>/Flow.txt [ReadMe] Flag=True → open that file from App_Store
          2. Fallback: README.md inside the Software_Downloaded folder
        """
        folder = Path(folder_path)

        # Try App_Store first (new behaviour)
        readme_path, base_folder = self._find_app_store_readme(folder)

        # Fallback: README.md in the Software_Downloaded folder
        if readme_path is None:
            candidate = folder / "README.md"
            if candidate.exists():
                readme_path = candidate
                base_folder = folder

        if readme_path and readme_path.exists():
            try:
                with open(readme_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                viewer = ReadmeViewer(
                    f"{folder.name} - README",
                    content,
                    folder_path=str(base_folder),
                    parent=self
                )
                viewer.exec()
            except Exception as e:
                msg_box = QMessageBox(self)
                msg_box.setWindowTitle("Error")
                msg_box.setText(f"Could not read readme: {e}")
                msg_box.setIcon(QMessageBox.Warning)
                msg_box.setStyleSheet(MESSAGE_BOX_STYLE)
                msg_box.exec()
        else:
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("No README")
            msg_box.setText(
                f"No README found for {folder.name}.\n\n"
                f"Make sure Flow.txt has a [ReadMe] section with Flag=True "
                f"and the file has been downloaded via Refresh."
            )
            msg_box.setIcon(QMessageBox.Information)
            msg_box.setStyleSheet(MESSAGE_BOX_STYLE)
            msg_box.exec()
    
    def check_software(self, folder_path):
        """Check software version (triggered by Check button)"""
        from .folder_parser import format_software_name, parse_software_folder_name
        
        folder = Path(folder_path)
        
        # Parse folder name to get software name
        parsed = parse_software_folder_name(folder.name)
        software_name = format_software_name(parsed)
        current_version = parsed.get('version', 'Unknown')
        
        # Store context for callback
        self._check_context = {
            'folder_path': folder_path,
            'software_name': software_name,
            'current_version': current_version
        }
        
        # Show loading indicator
        self.show_loading()
        
        # Show loading message
        self.status_label.setText(f"🔍 Checking {software_name} version...")
        
        # Create worker
        worker = CheckWorker(folder.name)
        worker.finished.connect(self._on_check_complete)
        
        # Run in background thread
        thread = Thread(target=worker.run)
        thread.daemon = True
        thread.start()
    
    def _on_check_complete(self, result):
        """Handle check completion (runs on main thread)"""
        success, is_latest, message, latest_version = result
        
        # Retrieve context
        folder_path = self._check_context['folder_path']
        software_name = self._check_context['software_name']
        current_version = self._check_context['current_version']
        
        try:
            if success:
                # Update the card's version label
                if folder_path in self.card_references:
                    card = self.card_references[folder_path]
                    card.update_version_status(is_latest)
                
                if is_latest:
                    # Version is up to date - Green label
                    msg_box = QMessageBox(self)
                    msg_box.setWindowTitle(f'✓ {software_name} - Up to Date')
                    msg_box.setText(f"✓ {message}\n\nYour installed version is the latest available on Box.")
                    msg_box.setIcon(QMessageBox.Information)
                    msg_box.setStyleSheet(MESSAGE_BOX_STYLE)
                    msg_box.exec()
                    
                    self.status_label.setText(f"✓ {software_name} is up to date (v{current_version})")
                else:
                    # Update available - Orange label
                    msg_box = QMessageBox(self)
                    msg_box.setWindowTitle(f'⚠️ {software_name} - Update Available')
                    msg_box.setText(f"⚠️ {message}\n\nAn update is available on Box.\n\nCurrent: v{current_version}\nLatest: v{latest_version}")
                    msg_box.setIcon(QMessageBox.Warning)
                    msg_box.setStyleSheet(MESSAGE_BOX_STYLE)
                    msg_box.exec()
                    
                    self.status_label.setText(f"⚠️ {software_name} update available: v{current_version} → v{latest_version}")
            else:
                # Error occurred
                msg_box = QMessageBox(self)
                msg_box.setWindowTitle(f'❌ {software_name} - Check Failed')
                msg_box.setText(f"Failed to check version for '{software_name}'.\n\nError: {message}")
                msg_box.setIcon(QMessageBox.Critical)
                msg_box.setStyleSheet(MESSAGE_BOX_STYLE)
                msg_box.exec()

                self.status_label.setText(f"❌ Failed to check {software_name}")
                self._report_log(f"Version check failed for '{software_name}': {message}")
        finally:
            # Hide loading indicator
            self.hide_loading()
    
    def closeEvent(self, event):
        """Handle window close event"""
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle('Exit Application')
        msg_box.setText('Are you sure you want to exit?')
        msg_box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg_box.setDefaultButton(QMessageBox.No)
        msg_box.setIcon(QMessageBox.Question)
        
        # Set black text color for the message box
        msg_box.setStyleSheet(EXIT_DIALOG_STYLE)
        
        reply = msg_box.exec()
        
        if reply == QMessageBox.Yes:
            self._sync_installed_tools("close")
            event.accept()
        else:
            event.ignore()
