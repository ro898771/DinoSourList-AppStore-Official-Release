"""
BookmarkButton - a small ribbon/bookmark-shaped favourite toggle, drawn by
hand (QPainterPath) instead of a text glyph, matching the classic "add to
favourites" ribbon icon (outline when empty, filled when favourited).
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPainter, QPainterPath, QPen, QColor
from PySide6.QtWidgets import QLabel

_FILLED_FILL = QColor("#FACC15")    # yellow -- matches the ⭐ star color used elsewhere
_FILLED_STROKE = QColor("#CA8A04")
_EMPTY_STROKE = QColor("#A8A8A8")
_HOVER_STROKE = QColor("#CA8A04")
_HOVER_FILL = QColor("#FEF9C3")


class BookmarkButton(QLabel):
    clicked = Signal()

    def __init__(self, is_favourite=False, size=28, parent=None):
        super().__init__(parent)
        self.is_favourite = is_favourite
        self._hovered = False
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        # No circular badge, no frame -- just the ribbon shape drawn in
        # paintEvent. Explicit rather than relying on QLabel's unstyled
        # default, since native OS styles can otherwise paint their own
        # hover/focus background behind a hoverable label.
        self.setStyleSheet("QLabel { background-color: transparent; border: none; }")
        self._update_tooltip()

    def _update_tooltip(self):
        self.setToolTip("Remove from Favourites" if self.is_favourite else "Add to Favourites")

    def set_favourite(self, is_fav):
        self.is_favourite = is_fav
        self._update_tooltip()
        self.update()

    def toggle(self):
        self.set_favourite(not self.is_favourite)
        return self.is_favourite

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()
        else:
            super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        margin = self.width() * 0.2
        w = self.width() - 2 * margin
        h = self.height() - 2 * margin
        notch = h * 0.32
        r = w * 0.22

        path = QPainterPath()
        path.moveTo(0, r)
        path.quadTo(0, 0, r, 0)
        path.lineTo(w - r, 0)
        path.quadTo(w, 0, w, r)
        path.lineTo(w, h)
        path.lineTo(w / 2, h - notch)
        path.lineTo(0, h)
        path.closeSubpath()
        path.translate(margin, margin)

        if self.is_favourite:
            painter.setBrush(_HOVER_FILL if self._hovered else _FILLED_FILL)
            painter.setPen(QPen(_FILLED_STROKE, 1.6))
        else:
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(_HOVER_STROKE if self._hovered else _EMPTY_STROKE, 1.8))

        painter.drawPath(path)
        painter.end()
