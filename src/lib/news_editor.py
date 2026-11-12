"""
NewsEditor - the News & Updates page as an in-place rich-text editor (port of the WPF Compiler's NewsEditor).

Shows the page like a normal read-only browser. Double-click the text to edit it: a formatting toolbar appears
(font, size, colour, bold/italic/underline, alignment, lists, links). Save keeps the edited copy in
config-record/news-edited.html and leaves edit mode; Cancel throws the changes away; Reset brings back the text rendered
from News/news.md. The edited copy is purely local - it never touches news.md.
"""

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEvent, QPointF, QRectF, QUrl, Qt
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QIcon, QPainter, QPen, QPixmap, QTextCharFormat,
                           QTextCursor, QTextListFormat)
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPushButton,
                               QSizePolicy, QStyle, QStyledItemDelegate, QTextBrowser, QVBoxLayout, QWidget)

from .styles import MESSAGE_BOX_STYLE

_FONTS = ["Segoe UI", "Arial", "Calibri", "Cambria", "Consolas", "Courier New", "Georgia", "Tahoma",
          "Times New Roman", "Verdana"]
_SIZES = [8, 9, 10, 11, 12, 13, 14, 16, 18, 20, 24, 28, 32, 36]
_COLOURS = [("Black", "#000000"), ("Red", "#e00000"), ("Blue", "#2f5fd0"), ("Green", "#228b22"),
            ("Orange", "#e67e00"), ("Magenta", "#d000d0"), ("Gray", "#808080"), ("White", "#ffffff")]

_HINT_VIEW = "Double-click the text to edit it"
_HINT_EDIT = "Editing - Save keeps the text, Cancel throws the changes away."

_VIEW_STYLE = """
    QTextBrowser { background-color: #ffffff; border: none; }
    QScrollBar:vertical { width: 10px; background: #f1f2f6; border-radius: 5px; }
    QScrollBar::handle:vertical { background: #c7cad3; border-radius: 5px; min-height: 30px; }
    QScrollBar::handle:vertical:hover { background: #9498a6; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""
_EDIT_STYLE = _VIEW_STYLE.replace("background-color: #ffffff", "background-color: #fffff4")
_TOOL_STYLE = """
    QPushButton { background: #ffffff; color: #000000; border: 1px solid #d5d8e0; border-radius: 6px; padding: 0 8px; font-size: 12px; }
    QPushButton:hover { background: #eef0ff; color: #000000; border-color: #4338ca; }
    QPushButton:checked { background: #e0e3ff; color: #000000; border-color: #4338ca; }
"""

# Modern drop-down: soft rounded border, one slim chevron, indigo on hover/open, rounded popup with a tick on the current row.
_COMBO_STYLE = """
    QComboBox { border: 1px solid #d5d8e0; border-radius: 8px; padding: 0 28px 0 10px; font-size: 12px;
                background: #ffffff; color: #000000; }
    QComboBox:hover, QComboBox:on { border-color: #4338ca; background: #f7f7ff; }
    QComboBox::drop-down { border: none; width: 26px; }
    QComboBox::down-arrow { image: none; width: 0; height: 0; }
    QComboBox QAbstractItemView { background: #ffffff; color: #000000; border: 1px solid #e0e3ee; border-radius: 10px;
                                  padding: 4px; outline: none; selection-background-color: transparent; }
"""


class _ComboDelegate(QStyledItemDelegate):
    """Popup rows: rounded hover/current highlight, optional colour swatch, each font name in its own font, a tick on the
    current row."""

    def __init__(self, combo, swatches=None, preview_fonts=False):
        super().__init__(combo)
        self._combo = combo
        self._swatches = swatches
        self._preview_fonts = preview_fonts

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(option.rect).adjusted(2, 1, -2, -1)
        current = index.row() == self._combo.currentIndex()
        if option.state & QStyle.State_MouseOver:
            painter.setPen(Qt.NoPen); painter.setBrush(QColor("#eef0ff")); painter.drawRoundedRect(rect, 6, 6)
        elif current:
            painter.setPen(Qt.NoPen); painter.setBrush(QColor("#f3f4f6")); painter.drawRoundedRect(rect, 6, 6)
        x = rect.left() + 10
        if self._swatches:
            painter.setPen(QPen(QColor("#d1d5db"), 1)); painter.setBrush(QColor(self._swatches[index.row()]))
            painter.drawEllipse(QPointF(x + 6, rect.center().y()), 6, 6)
            x += 20
        font = QFont(option.font)
        if self._preview_fonts:
            font.setFamily(index.data(Qt.DisplayRole))
        painter.setFont(font)
        painter.setPen(QColor("#000000"))
        painter.drawText(QRectF(x, rect.top(), rect.right() - x - 24, rect.height()), Qt.AlignVCenter | Qt.AlignLeft,
                         index.data(Qt.DisplayRole) or "")
        if current:
            pen = QPen(QColor("#4338ca"), 1.8); pen.setCapStyle(Qt.RoundCap); pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            cx, cy = rect.right() - 14, rect.center().y()
            painter.drawPolyline([QPointF(cx - 4, cy), QPointF(cx - 1, cy + 3), QPointF(cx + 4, cy - 3)])
        painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setHeight(max(size.height(), 30))
        return size


class ModernCombo(QComboBox):
    """Flat rounded drop-down with a single slim chevron (instead of the platform's heavy boxed arrow)."""

    def __init__(self, items, width, swatches=None, preview_fonts=False, parent=None):
        super().__init__(parent)
        self.setFixedSize(width, 28)
        self.setFocusPolicy(Qt.NoFocus)
        self.setStyleSheet(_COMBO_STYLE)
        self.addItems(items)
        if swatches:                         # the closed box shows the colour too
            for i, hex_ in enumerate(swatches):
                pm = QPixmap(14, 14); pm.fill(Qt.transparent)
                p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing)
                p.setPen(QPen(QColor("#d1d5db"), 1)); p.setBrush(QColor(hex_)); p.drawEllipse(1, 1, 12, 12); p.end()
                self.setItemIcon(i, QIcon(pm))
        self.setItemDelegate(_ComboDelegate(self, swatches, preview_fonts))
        self.setMaxVisibleItems(10)
        view = self.view()
        view.setMouseTracking(True)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        view.setMinimumWidth(max(width, 160))        # the list is never narrower than its longest name
        popup = view.window()               # the popup container: frameless + transparent so the list's rounded corners show
        popup.setWindowFlags(popup.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        popup.setAttribute(Qt.WA_TranslucentBackground)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor("#4338ca") if self.underMouse() else QColor("#6b7280"), 1.6)
        pen.setCapStyle(Qt.RoundCap); pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        cx, cy = self.width() - 15, self.height() / 2
        painter.drawPolyline([QPointF(cx - 4, cy - 2), QPointF(cx, cy + 2), QPointF(cx + 4, cy - 2)])
        painter.end()


class NewsEditor(QWidget):
    def __init__(self, default_html: Callable[[], str], file_path: Path, source_name: str = "News/news.md",
                 parent=None):
        super().__init__(parent)
        self._default_html = default_html
        self._file = Path(file_path)
        self._source_name = source_name
        self._editing = False
        self._syncing = False

        self.edit = QTextBrowser()
        self.edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.edit.setOpenLinks(False)
        self.edit.setOpenExternalLinks(False)
        self.edit.setStyleSheet(_VIEW_STYLE)
        self.edit.anchorClicked.connect(self._open_link)
        self.edit.viewport().installEventFilter(self)
        self.edit.cursorPositionChanged.connect(self._sync_bar)
        self.edit.currentCharFormatChanged.connect(lambda *_: self._sync_bar())

        self._bar = self._build_bar()
        self._bar.setVisible(False)
        self._hint = QLabel(_HINT_VIEW)
        self._hint.setStyleSheet("color: #9ca3af; font-size: 11px; padding: 4px 0 0 0;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._bar)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self._hint)

        self._load()

    # ── toolbar ──────────────────────────────────────────────────────────────────

    def _tool(self, text, tip, slot, width=None, checkable=False) -> QPushButton:
        b = QPushButton(text)
        b.setToolTip(tip)
        b.setFixedHeight(28)
        b.setCheckable(checkable)
        b.setFocusPolicy(Qt.NoFocus)      # keep the text cursor/selection in the editor while clicking
        if width:
            b.setFixedWidth(width)
        b.clicked.connect(lambda _=False: slot())
        return b

    def _build_bar(self) -> QFrame:
        bar = QFrame()
        bar.setStyleSheet(_TOOL_STYLE + "QFrame#newsbar { border-bottom: 1px solid #e5e7eb; }")
        bar.setObjectName("newsbar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 8)
        row.setSpacing(4)

        self._font = ModernCombo(_FONTS, 140, preview_fonts=True); self._font.setToolTip("Text type")
        self._size = ModernCombo([str(s) for s in _SIZES], 66); self._size.setToolTip("Text size")
        self._color = ModernCombo([n for n, _h in _COLOURS], 124, swatches=[h for _n, h in _COLOURS])
        self._color.setToolTip("Text colour")
        self._font.activated.connect(lambda i: self._apply(lambda f: f.setFontFamilies([_FONTS[i]])))
        self._size.activated.connect(lambda i: self._apply(lambda f: f.setFontPointSize(float(_SIZES[i]))))
        self._color.activated.connect(lambda i: self._apply(lambda f: f.setForeground(QColor(_COLOURS[i][1]))))
        for w in (self._font, self._size, self._color):
            row.addWidget(w)

        self._bold = self._tool("B", "Bold (Ctrl+B)", self._toggle_bold, 32, True)
        self._bold.setStyleSheet("font-weight: bold;")
        self._italic = self._tool("I", "Italic (Ctrl+I)", self._toggle_italic, 32, True)
        self._italic.setStyleSheet("font-style: italic;")
        self._underline = self._tool("U", "Underline (Ctrl+U)", self._toggle_underline, 32, True)
        self._underline.setStyleSheet("text-decoration: underline;")
        for w in (self._bold, self._italic, self._underline):
            row.addWidget(w)
        row.addWidget(self._tool("A+", "Larger text", lambda: self._bump_size(+2), 36))
        row.addWidget(self._tool("A-", "Smaller text", lambda: self._bump_size(-2), 36))
        row.addWidget(self._tool("Left", "Align left", lambda: self.edit.setAlignment(Qt.AlignLeft), 46))
        row.addWidget(self._tool("Center", "Align centre", lambda: self.edit.setAlignment(Qt.AlignHCenter), 58))
        row.addWidget(self._tool("Right", "Align right", lambda: self.edit.setAlignment(Qt.AlignRight), 50))
        row.addWidget(self._tool("• List", "Bullet list", lambda: self._toggle_list(QTextListFormat.ListDisc), 60))
        row.addWidget(self._tool("1. List", "Numbered list", lambda: self._toggle_list(QTextListFormat.ListDecimal), 64))
        row.addWidget(self._tool("Link...", "Make the selected text a link (or insert a new one)", self._insert_link, 66))
        row.addWidget(self._tool("Unlink", "Remove the link under the cursor", self._remove_link, 62))
        row.addStretch(1)
        row.addWidget(self._tool("Save", "Save the text and leave the editor", self._save_and_stop, 60))
        row.addWidget(self._tool("Cancel", "Leave the editor without saving", self._cancel, 62))
        row.addWidget(self._tool("Reset", f"Bring back the original text ({self._source_name})", self._reset, 60))
        return bar

    # ── formatting ───────────────────────────────────────────────────────────────

    def _apply(self, mutate: Callable[[QTextCharFormat], None]) -> None:
        if not self._editing:
            return
        fmt = QTextCharFormat()
        mutate(fmt)
        self.edit.mergeCurrentCharFormat(fmt)
        self.edit.setFocus()

    def _toggle_bold(self):
        bold = self.edit.fontWeight() >= QFont.Bold
        self._apply(lambda f: f.setFontWeight(QFont.Normal if bold else QFont.Bold))

    def _toggle_italic(self):
        italic = self.edit.fontItalic()
        self._apply(lambda f: f.setFontItalic(not italic))

    def _toggle_underline(self):
        under = self.edit.fontUnderline()
        self._apply(lambda f: f.setFontUnderline(not under))

    def _bump_size(self, delta: int):
        size = self.edit.fontPointSize() or self.edit.font().pointSizeF() or 11.0
        self._apply(lambda f: f.setFontPointSize(max(6.0, min(72.0, size + delta))))

    def _toggle_list(self, style):
        if not self._editing:
            return
        cur = self.edit.textCursor()
        lst = cur.currentList()
        if lst is not None and lst.format().style() == style:
            cur.beginEditBlock()
            start, end = cur.selectionStart(), cur.selectionEnd()
            block = self.edit.document().findBlock(start)
            while block.isValid() and block.position() <= end:
                if block.textList() is lst or (block.textList() and block.textList().format().style() == style):
                    block.textList().remove(block)
                    bc = QTextCursor(block)
                    bf = bc.blockFormat()
                    bf.setIndent(0)
                    bc.setBlockFormat(bf)
                block = block.next()
            cur.endEditBlock()
        else:
            cur.createList(style)
        self.edit.setFocus()

    def _sync_bar(self):
        if not self._editing:
            return
        self._syncing = True
        try:
            self._bold.setChecked(self.edit.fontWeight() >= QFont.Bold)
            self._italic.setChecked(self.edit.fontItalic())
            self._underline.setChecked(self.edit.fontUnderline())
            fam = self.edit.currentFont().family()
            i = next((k for k, n in enumerate(_FONTS) if n.lower() == fam.lower()), -1)
            self._font.setCurrentIndex(i)
            pt = self.edit.fontPointSize() or self.edit.currentFont().pointSizeF()
            # the closest listed size (a heading's 18.7 pt shows as 18) - never an empty box
            j = min(range(len(_SIZES)), key=lambda k: abs(_SIZES[k] - pt)) if pt > 0 else -1
            self._size.setCurrentIndex(j)
        finally:
            self._syncing = False

    # ── links ────────────────────────────────────────────────────────────────────

    def _open_link(self, url: QUrl):
        if not self._editing and url.isValid():
            QDesktopServices.openUrl(url)

    def _warn(self, title: str, text: str, icon=QMessageBox.Warning):
        box = QMessageBox(self.window())
        box.setWindowTitle(title)
        box.setIcon(icon)
        box.setText(text)
        box.setStyleSheet(MESSAGE_BOX_STYLE)
        box.exec()

    def _insert_link(self):
        if not self._editing:
            return
        url, ok = QInputDialog.getText(self.window(), "Link", "Link address (https://...)", text="https://")
        url = url.strip()
        if not ok or url in ("", "https://"):
            return
        qurl = QUrl(url)
        if not qurl.isValid() or not qurl.scheme():
            self._warn("Link", "That is not a valid address.")
            return
        cur = self.edit.textCursor()
        fmt = QTextCharFormat()
        fmt.setAnchor(True)
        fmt.setAnchorHref(qurl.toString())
        fmt.setForeground(QColor("#0969da"))
        fmt.setFontUnderline(True)
        if not cur.hasSelection():
            text, ok = QInputDialog.getText(self.window(), "Link", "Text of the link", text=qurl.host() or url)
            if not ok or not text:
                return
            cur.insertText(text, fmt)
        else:
            cur.mergeCharFormat(fmt)
        self.edit.setTextCursor(cur)

    def _remove_link(self):
        if not self._editing:
            return
        cur = self.edit.textCursor()
        pos = cur.position()
        block = cur.block()
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            if frag.isValid() and frag.charFormat().isAnchor() and frag.position() <= pos <= frag.position() + frag.length():
                sel = QTextCursor(self.edit.document())
                sel.setPosition(frag.position())
                sel.setPosition(frag.position() + frag.length(), QTextCursor.KeepAnchor)
                plain = QTextCharFormat()
                plain.setAnchor(False)
                plain.setAnchorHref("")
                plain.setFontUnderline(False)
                plain.setForeground(self.edit.palette().text())
                sel.mergeCharFormat(plain)
                return
            it += 1

    # ── edit mode / storage ──────────────────────────────────────────────────────

    def eventFilter(self, obj, event):
        if obj is self.edit.viewport() and event.type() == QEvent.MouseButtonDblClick and not self._editing:
            self._start_edit()
            return True
        return super().eventFilter(obj, event)

    def _start_edit(self):
        self._editing = True
        self.edit.setReadOnly(False)
        self.edit.setTextInteractionFlags(Qt.TextEditorInteraction)
        self.edit.setStyleSheet(_EDIT_STYLE)
        self._bar.setVisible(True)
        self._hint.setText(_HINT_EDIT)
        self.edit.setFocus()
        self._sync_bar()

    def _stop_edit(self):
        self._editing = False
        self.edit.setReadOnly(True)
        self.edit.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.edit.setStyleSheet(_VIEW_STYLE)
        self._bar.setVisible(False)
        self._hint.setText(_HINT_VIEW)

    def _save_and_stop(self):
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            self._file.write_text(self.edit.toHtml(), encoding="utf-8")
        except Exception as exc:
            self._warn("News & Updates", f"Could not save the text: {exc}", QMessageBox.Critical)
            return
        self._stop_edit()

    def _cancel(self):
        self._load()
        self._stop_edit()

    def _load(self):
        try:
            if self._file.exists():
                self.edit.setHtml(self._file.read_text(encoding="utf-8"))
                return
        except Exception:
            pass                      # fall through to the original text
        self.edit.setHtml(self._default_html())

    def _reset(self):
        box = QMessageBox(self.window())
        box.setWindowTitle("Reset")
        box.setIcon(QMessageBox.Question)
        box.setText(f"Bring back the original text from {self._source_name}?\n\nYour edited version will be discarded.")
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        box.setStyleSheet(MESSAGE_BOX_STYLE)
        if box.exec() != QMessageBox.Yes:
            return
        try:
            if self._file.exists():
                self._file.unlink()
        except Exception:
            pass
        self.edit.setHtml(self._default_html())
