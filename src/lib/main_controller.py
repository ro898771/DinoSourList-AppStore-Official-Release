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
                                QLineEdit, QComboBox, QFrame, QTextEdit)
from PySide6.QtCore import Qt, QUrl, Signal, QObject, QTimer
from PySide6.QtGui import QFont, QMovie, QTextCursor

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
from .boxlink_api import BoxLinkAPI
from .app_controller import AppStoreDownloadWorker, SingleCardDownloadWorker
from .workers import WorkerSignals, RefreshWorker, SingleCardRefreshWorker, CheckWorker, DownloadInstallWorker, DeleteWorker
from .readme_viewer import ReadmeViewer
from .styles import (
    MAIN_WINDOW_STYLE, TITLE_STYLE, REFRESH_BUTTON_STYLE,
    STATUS_LABEL_STYLE, MESSAGE_BOX_STYLE, EXIT_DIALOG_STYLE,
    PAGINATION_NAV_BUTTON_STYLE, PAGINATION_PAGE_BUTTON_STYLE,
    PAGINATION_PAGE_BUTTON_ACTIVE_STYLE, PAGINATION_LABEL_STYLE,
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
        self.setWindowTitle("Software Launcher Dashboard")
        _ico = Path(__file__).parent.parent.parent / "IcoFolder" / "main.ico"
        if _ico.exists():
            from PySide6.QtGui import QIcon
            self.setWindowIcon(QIcon(str(_ico)))
        self.resize(1500, 1000)
        
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
        self._llm_worker = _LLMWorker()     # worker created at startup; init deferred until dialog opens

        # Page names
        self.page_names = [
            "Dashboard",
            "Store",
            "Useful Links",
            "News"
        ]
        
        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)
        
        # Header with title and loading indicator
        header_layout = QHBoxLayout()
        header_layout.setSpacing(10)
        
        # Title (dynamic based on page)
        self.title_label = QLabel("🚀 Software Dashboard")
        self.title_label.setStyleSheet(TITLE_STYLE)
        header_layout.addWidget(self.title_label)
        
        # Spacer to push right-side items to the edge
        header_layout.addStretch()

        # AI Assistant badge — GIF icon + text, visible only on the Store page (page index 1)
        self.ai_btn = QWidget()
        self.ai_btn.setCursor(Qt.PointingHandCursor)
        self.ai_btn.setStyleSheet("QWidget { background: transparent; }")
        _ai_row = QHBoxLayout(self.ai_btn)
        _ai_row.setContentsMargins(8, 4, 8, 4)
        _ai_row.setSpacing(6)

        # Animated dinosaur GIF
        _ai_gif_lbl = ClickableLabel()
        _dino_path = Path(__file__).parent.parent.parent / "Sw-icon" / "d2.gif"
        if _dino_path.exists():
            from PySide6.QtCore import QSize as _QSize
            self._dino_badge_movie = QMovie(str(_dino_path))
            self._dino_badge_movie.setScaledSize(_QSize(50, 50))
            _ai_gif_lbl.setMovie(self._dino_badge_movie)
            self._dino_badge_movie.start()
        else:
            _ai_gif_lbl.setText("🦕")
        _ai_gif_lbl.setCursor(Qt.PointingHandCursor)
        _ai_gif_lbl.clicked.connect(self._on_ai_assistant_clicked)
        _ai_row.addWidget(_ai_gif_lbl)

        # Text
        _ai_text_lbl = ClickableLabel("AI Assistant")
        _ai_text_lbl.setStyleSheet("""
            QLabel {
                font-size: 14px;
                font-weight: 600;
                color: #0a58ca;
                background-color: transparent;
                border: none;
                padding: 4px 0;
            }
            QLabel:hover { color: #084298; }
        """)
        _ai_text_lbl.clicked.connect(self._on_ai_assistant_clicked)
        _ai_row.addWidget(_ai_text_lbl)

        self.ai_btn.hide()
        header_layout.addWidget(self.ai_btn)
        header_layout.addSpacing(8)

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
        
        # Controls row: Refresh button + filter box (side by side)
        controls_row = QHBoxLayout()
        controls_row.setSpacing(10)
        controls_row.setContentsMargins(0, 0, 0, 0)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setMaximumWidth(100)
        self.refresh_btn.setStyleSheet(REFRESH_BUTTON_STYLE)
        self.refresh_btn.clicked.connect(self.refresh_data)
        controls_row.addWidget(self.refresh_btn)

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

        controls_row.addStretch()
        layout.addLayout(controls_row)
        
        # Scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(SCROLL_BAR_STYLE)
        self.cards_container = QWidget()
        self.cards_layout = QGridLayout(self.cards_container)
        self.cards_layout.setSpacing(25)
        self.cards_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.cards_layout.setContentsMargins(20, 20, 20, 20)
        scroll.setWidget(self.cards_container)
        layout.addWidget(scroll)
        
        # Pagination controls
        self._setup_pagination_controls(layout)
        
        # Status - Black text
        self.status_label = QLabel("")
        self.status_label.setStyleSheet(STATUS_LABEL_STYLE)
        layout.addWidget(self.status_label)
        
        # Load software
        self.software_path = Path(__file__).parent.parent.parent / "Software_Downloaded"
        self.config_path = Path(__file__).parent.parent.parent / "config-record"
        self.record_file = self.config_path / "record.json"
        self.load_software()
        # Apply correct visibility and placeholder for the initial page
        self._update_refresh_button_visibility()

    def _on_ai_assistant_clicked(self):
        """Open the AI Assistant dialog and wire it to the LLM worker."""

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

        dialog.exec()

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
    
    def _setup_pagination_controls(self, layout):
        """Setup pagination controls with left/right navigation and page buttons"""
        pagination_container = QWidget()
        pagination_layout = QHBoxLayout(pagination_container)
        pagination_layout.setContentsMargins(20, 10, 20, 10)
        pagination_layout.setSpacing(15)
        
        # Left arrow button
        self.btn_prev = QPushButton("◀ Previous")
        self.btn_prev.setStyleSheet(PAGINATION_NAV_BUTTON_STYLE)
        self.btn_prev.clicked.connect(self.go_to_previous_page)
        pagination_layout.addWidget(self.btn_prev)
        
        # Add spacer
        pagination_layout.addStretch()
        
        # Page buttons with numbers (1, 2, 3, 4, 5)
        self.page_buttons = []
        for i in range(self.total_pages):
            btn = QPushButton(str(i + 1))
            btn.setStyleSheet(PAGINATION_PAGE_BUTTON_STYLE)
            btn.clicked.connect(lambda checked, page=i: self.go_to_page(page))
            self.page_buttons.append(btn)
            pagination_layout.addWidget(btn)
        
        # Add spacer
        pagination_layout.addStretch()
        
        # Right arrow button
        self.btn_next = QPushButton("Next ▶")
        self.btn_next.setStyleSheet(PAGINATION_NAV_BUTTON_STYLE)
        self.btn_next.clicked.connect(self.go_to_next_page)
        pagination_layout.addWidget(self.btn_next)
        
        layout.addWidget(pagination_container)
        
        # Update button states
        self._update_pagination_buttons()
    
    def _update_pagination_buttons(self):
        """Update pagination button states based on current page"""
        # Circular navigation - always enable prev/next buttons
        self.btn_prev.setEnabled(True)
        self.btn_next.setEnabled(True)
        
        # Update page button styles
        for i, btn in enumerate(self.page_buttons):
            if i == self.current_page:
                btn.setStyleSheet(PAGINATION_PAGE_BUTTON_ACTIVE_STYLE)
            else:
                btn.setStyleSheet(PAGINATION_PAGE_BUTTON_STYLE)
    
    def go_to_page(self, page_number):
        """Navigate to specific page"""
        if 0 <= page_number < self.total_pages:
            self.current_page = page_number
            self._update_page_title()
            self._update_refresh_button_visibility()
            self._display_current_page()
            self._update_pagination_buttons()
    
    def _update_page_title(self):
        """Update the page title based on current page"""
        titles = {
            0: "🚀 Local Dashboard",
            1: "🏪 Software Store",
            2: "🔗 Useful Links",
            3: "📰 News & Updates"
        }
        self.title_label.setText(titles.get(self.current_page, "🚀 Software Dashboard"))
    
    def _update_refresh_button_visibility(self):
        """Show/hide refresh button, filter box, and header badges based on current page."""
        self.refresh_btn.setVisible(self.current_page in [0, 1])

        # Filter box: visible on Page 1 (Dashboard) and Page 2 (Store)
        on_filtered_page = self.current_page in [0, 1]
        self.filter_edit.setVisible(on_filtered_page)

        # AI Assistant badge: visible only on Page 2 (Store, index 1)
        self.ai_btn.setVisible(self.current_page == 1)

        # Clear filter silently on every page switch so each page starts fresh
        self.filter_edit.blockSignals(True)
        self.filter_edit.clear()
        self.filter_edit.blockSignals(False)

        # Update placeholder text to reflect which page is active
        if self.current_page == 0:
            self.filter_edit.setPlaceholderText("🔍  Search installed software…")
        else:
            self.filter_edit.setPlaceholderText("🔍  Search software by name…")
    
    def go_to_previous_page(self):
        """Navigate to previous page (circular - wraps to last page from first)"""
        self.current_page = (self.current_page - 1) % self.total_pages
        self._update_page_title()
        self._update_refresh_button_visibility()
        self._display_current_page()
        self._update_pagination_buttons()
    
    def go_to_next_page(self):
        """Navigate to next page (circular - wraps to first page from last)"""
        self.current_page = (self.current_page + 1) % self.total_pages
        self._update_page_title()
        self._update_refresh_button_visibility()
        self._display_current_page()
        self._update_pagination_buttons()
    
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
    
    def _display_dashboard_page(self):
        """Display Software Dashboard page (Page 1) - Scrollable for all cards"""
        from .folder_parser import parse_software_folder_name, format_software_name, get_author_raw

        # Clear card references
        self.card_references.clear()
        self.dashboard_folder_name_map = {}  # folder_name (App_Store) → folder_path str

        # Display ALL software cards (no limit, scrollable)
        row = col = 0
        for i, software_data in enumerate(self.all_software_data):
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

            card = SoftwareCard(
                software_data['name'],
                None,
                folder,
                software_data['is_latest'],
                icon_path=software_data.get('icon_path'),
                folder_name=app_store_folder_name,
                folder_id=dash_folder_id,
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
            if col >= 4:
                col = 0
                row += 1

        # Update status
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
            return
        
        # Display ALL cards in grid (4 columns, scrollable rows)
        row = 0
        col = 0
        self.store_card_references = {}  # folder_name → StoreCard

        for software in store_data:
            # Create store card
            card = StoreCard(
                software_name=software['name'],
                author_name=software['author'],
                icon_path=software.get('icon_path'),
                json_path=software.get('json_path'),
                folder_name=software.get('folder_name'),
                folder_id=software.get('folder_id', ''),
            )

            # Connect signals
            card.download_clicked.connect(self._on_store_download)
            card.guide_clicked.connect(self._on_store_guide_clicked)
            card.card_refresh_clicked.connect(self._on_card_refresh_clicked)

            self.store_card_references[software.get('folder_name', software['name'])] = card

            self.cards_layout.addWidget(card, row, col)
            
            col += 1
            if col >= 4:  # 4 columns
                col = 0
                row += 1
        
        # Update status
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
            if self._matches_filter(card.display_name, text):
                self.cards_layout.addWidget(card, row, col)
                card.show()
                matched += 1
                col += 1
                if col >= 4:
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

    @staticmethod
    def _matches_filter(name: str, text: str) -> bool:
        """Return True if *name* matches the filter *text*.

        Matching rules (case-insensitive):
          1. Empty search  → always match
          2. Substring     → search text appears anywhere in name
          3. Word match    → every space-separated word appears in name
        """
        if not text.strip():
            return True
        name_lower  = name.lower()
        search_lower = text.lower().strip()
        if search_lower in name_lower:
            return True
        return all(word in name_lower for word in search_lower.split())

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
            if self._matches_filter(card.software_name, text):
                self.cards_layout.addWidget(card, row, col)
                card.show()
                matched += 1
                col += 1
                if col >= 4:
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

        # ── HTML template with full-width styling ─────────────────────────────
        CSS = """
            * { box-sizing: border-box; }
            body {
                font-family: 'Segoe UI', Arial, sans-serif;
                font-size: 15px;
                color: #212529;
                line-height: 1.8;
                margin: 0;
                padding: 36px 56px;
                background: #f8f9fa;
            }
            h1 {
                font-size: 28px;
                color: #fd7e14;
                border-bottom: 3px solid #fd7e14;
                padding-bottom: 10px;
                margin-bottom: 20px;
            }
            h2 {
                font-size: 21px;
                color: #343a40;
                margin-top: 36px;
                margin-bottom: 12px;
                border-left: 4px solid #fd7e14;
                padding-left: 12px;
            }
            h3 {
                font-size: 16px;
                color: #495057;
                margin-top: 24px;
                margin-bottom: 8px;
            }
            ul {
                padding-left: 28px;
                margin-top: 4px;
            }
            li {
                margin-bottom: 8px;
            }
            a {
                color: #0d6efd;
                text-decoration: underline;
            }
            a:hover { color: #0a58ca; }
            hr {
                border: none;
                border-top: 1px solid #dee2e6;
                margin: 28px 0;
            }
            blockquote {
                background: #fff3cd;
                border-left: 5px solid #fd7e14;
                padding: 12px 20px;
                margin: 20px 0;
                border-radius: 6px;
                color: #856404;
            }
            p { margin: 10px 0; }
            code {
                background: #e9ecef;
                padding: 2px 6px;
                border-radius: 4px;
                font-family: Consolas, monospace;
                font-size: 13px;
            }
        """

        if MARKDOWN_AVAILABLE:
            html_body = markdown.markdown(combined_md, extensions=["extra", "nl2br"])
        else:
            # Minimal fallback: convert line breaks, but no real markdown parsing
            html_body = combined_md.replace("\n", "<br>")

        full_html = (
            f"<html><head><style>{CSS}</style></head>"
            f"<body>{html_body}</body></html>"
        )

        # ── QTextBrowser fills the outer wrapper ──────────────────────────────
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        browser.setStyleSheet("""
            QTextBrowser {
                background-color: #f8f9fa;
                border: none;
            }
            QScrollBar:vertical {
                width: 10px;
                background: #e9ecef;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical {
                background: #adb5bd;
                border-radius: 5px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover { background: #6c757d; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)
        browser.setHtml(full_html)

        outer_layout.addWidget(browser)
        self.cards_layout.addWidget(outer, 0, 0, 1, 4)
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
            self.status_label.setText(
                f"🔗 {self.page_names[self.current_page]} — No content"
            )
            return

        try:
            md_content = link_file.read_text(encoding="utf-8")
        except Exception as e:
            md_content = f"*(Error reading link.md: {e})*"

        CSS = """
            * { box-sizing: border-box; }
            body {
                font-family: 'Segoe UI', Arial, sans-serif;
                font-size: 15px;
                color: #212529;
                line-height: 1.8;
                margin: 0;
                padding: 36px 56px;
                background: #f8f9fa;
            }
            h1 {
                font-size: 28px;
                color: #0d6efd;
                border-bottom: 3px solid #0d6efd;
                padding-bottom: 10px;
                margin-bottom: 20px;
            }
            h2 {
                font-size: 21px;
                color: #343a40;
                margin-top: 36px;
                margin-bottom: 12px;
                border-left: 4px solid #0d6efd;
                padding-left: 12px;
            }
            h3 {
                font-size: 16px;
                color: #495057;
                margin-top: 24px;
                margin-bottom: 8px;
            }
            ul {
                padding-left: 28px;
                margin-top: 4px;
            }
            li {
                margin-bottom: 8px;
            }
            a {
                color: #0d6efd;
                text-decoration: underline;
            }
            a:hover { color: #0a58ca; }
            hr {
                border: none;
                border-top: 1px solid #dee2e6;
                margin: 28px 0;
            }
            blockquote {
                background: #e7f1ff;
                border-left: 5px solid #0d6efd;
                padding: 12px 20px;
                margin: 20px 0;
                border-radius: 6px;
                color: #084298;
            }
            p { margin: 10px 0; }
            code {
                background: #e9ecef;
                padding: 2px 6px;
                border-radius: 4px;
                font-family: Consolas, monospace;
                font-size: 13px;
            }
        """

        if MARKDOWN_AVAILABLE:
            html_body = markdown.markdown(md_content, extensions=["extra", "nl2br"])
        else:
            html_body = md_content.replace("\n", "<br>")

        full_html = (
            f"<html><head><style>{CSS}</style></head>"
            f"<body>{html_body}</body></html>"
        )

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        from PySide6.QtWidgets import QSizePolicy
        browser.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        browser.setStyleSheet("""
            QTextBrowser {
                background-color: #f8f9fa;
                border: none;
            }
            QScrollBar:vertical {
                width: 10px;
                background: #e9ecef;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical {
                background: #adb5bd;
                border-radius: 5px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover { background: #6c757d; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)
        browser.setHtml(full_html)

        outer_layout.addWidget(browser)
        self.cards_layout.addWidget(outer, 0, 0, 1, 4)
        self.status_label.setText(
            f"🔗 {self.page_names[self.current_page]} — link.md loaded"
        )

    def refresh_data(self):
        """Refresh data from BoxLink API and save to record.json"""
        # Show loading indicator
        self.show_loading()
        self.status_label.setText("🔄 Refreshing data from Box (scanning folders recursively)...")
        
        # Create worker
        worker = RefreshWorker(None, self.config_path, self.record_file)
        worker.finished.connect(self._on_refresh_complete)
        
        # Run in background thread
        thread = Thread(target=worker.run)
        thread.daemon = True
        thread.start()
    
    def _on_refresh_complete(self, result):
        """Handle refresh completion (runs on main thread)"""
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
                self.hide_loading()
        except Exception as e:
            self.status_label.setText(f"⚠️ Error refreshing data: {str(e)}")
            self.hide_loading()
    
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
                
                # Refresh the current page (stay on the page user is navigating)
                # This will update all cards with new ComboBox options and button states
                self._display_current_page()
            else:
                self.status_label.setText(f"⚠️ Metadata creation failed: {message}")
        except Exception as e:
            self.status_label.setText(f"⚠️ Error: {str(e)}")
        finally:
            # Hide loading indicator
            self.hide_loading()
    
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
            self._update_pagination_buttons()
    
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

    def _load_store_software(self):
        """Load software data from App_Store directory"""
        app_store_path = Path(__file__).parent.parent.parent / "App_Store"
        
        if not app_store_path.exists():
            return []
        
        store_data = []
        
        # Iterate through each folder in App_Store
        for folder in sorted(app_store_path.iterdir()):
            if not folder.is_dir():
                continue
            
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

            store_data.append({
                'name': software_name,
                'author': author_name,
                'icon_path': icon_path,  # already None if not found
                'json_path': json_path if json_path.exists() else None,
                'folder': folder,
                'folder_name': folder_name,
                'folder_id': folder_id,
            })
        
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
    
    def _on_store_guide_clicked(self, software_name):
        """Handle Details button click from store card.
        Opens the guide file whose name is read from Flow.txt [Guide] file=."""
        import os

        app_store_path = Path(__file__).parent.parent.parent / "App_Store"
        guide_path = None

        # Find the matching App_Store folder and read guide filename from Flow.txt
        for folder in app_store_path.iterdir():
            if not folder.is_dir() or not folder.name.startswith(software_name):
                continue

            flow_txt = folder / "Flow.txt"
            guide_filename = None

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
                            if current_section == 'guide' and '=' in line:
                                key, _, value = line.partition('=')
                                if key.strip().lower() == 'file':
                                    v = value.strip()
                                    if v and v.lower() not in ('none', 'false', ''):
                                        guide_filename = v
                                    break
                except Exception as e:
                    print(f"[GUIDE] Error reading Flow.txt for {folder.name}: {e}")

            if guide_filename:
                candidate = folder / guide_filename
                print(f"[GUIDE] [{folder.name}] Flow.txt guide={guide_filename}  exists={candidate.exists()}")
                if candidate.exists():
                    guide_path = candidate
                    break
            else:
                print(f"[GUIDE] [{folder.name}] No [Guide] file= in Flow.txt — skipping")

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
            self._finish_card_refresh(folder_name, success=False)
            return

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
            self._finish_dashboard_card_refresh(folder_name, success=False)
            return

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
        self._finish_dashboard_card_refresh(folder_name, success=success)

    def _finish_dashboard_card_refresh(self, folder_name, success=True):
        """Re-enable the card button and live-update icon + version badge."""
        self.hide_loading()

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
            
            # Auto-refresh software data to show newly installed software (without changing page)
            self.load_software(reset_page=False)
            
            # Refresh the current page display (stay on current page)
            self._display_current_page()
            
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("Installation Complete")
            msg_box.setIcon(QMessageBox.Information)
            msg_box.setText(f"{message}\n\nThe software has been installed to Software_Downloaded folder.\n\n"
                           f"Dashboard has been refreshed automatically.")
            msg_box.setStyleSheet(MESSAGE_BOX_STYLE)
            msg_box.exec()
        else:
            self.status_label.setText(f"❌ {message}")
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
                        QMessageBox.critical(
                            self, "Launch Error",
                            f"'{exec_path.name}' requires administrator privileges.\n\n"
                            f"UAC elevation failed:\n{str(e2)}"
                        )
                else:
                    QMessageBox.critical(self, "Launch Error", f"Failed to launch:\n{str(e)}")
        else:
            reason = "exec_path is None" if not exec_path else f"file not found: {exec_path}"
            print(f"[LAUNCH] ABORTED             : {reason}")
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
            QMessageBox.warning(self, "Delete Failed",
                                f"Folder not found:\n{folder}")
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
        self.status_label.setText(f"🗑️ Deleting '{folder.name}'…  Please wait.")
        self.refresh_btn.setEnabled(False)

        # ── Run deletion in background ────────────────────────────────────────
        worker = DeleteWorker(str(folder))
        worker.progress.connect(lambda msg: self.status_label.setText(f"🗑️ {msg}"))
        worker.finished.connect(self._on_delete_complete)

        thread = Thread(target=worker.run, daemon=True)
        thread.start()

    def _on_delete_complete(self, success: bool, message: str):
        """Called on the main thread when background deletion finishes."""
        self.hide_loading()
        self.refresh_btn.setEnabled(True)

        if success:
            folder_name = message  # worker emits folder.name on success
            self.status_label.setText(f"✅ Deleted '{folder_name}' successfully.")
            self.load_software(reset_page=False)
            self._display_current_page()
        else:
            self.status_label.setText("❌ Delete failed — see details below.")
            QMessageBox.critical(self, "Delete Failed", message)

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
            event.accept()
        else:
            event.ignore()
