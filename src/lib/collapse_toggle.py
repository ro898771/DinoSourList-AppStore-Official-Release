"""
CollapseToggleButton - a small circular chevron button for expand/collapse
section headers (e.g. the Favourites page's "Installed"/"Available in
Store" groups). The chevron is hand-drawn via QPainter rather than a text
glyph -- the same lesson learned earlier in this app with ✕/★/»: some
Unicode arrow characters render fine in some environments and come up
completely blank in others, while drawn shapes always render.
"""
from PySide6.QtCore import Qt, Signal, QPointF
from PySide6.QtGui import QPainter, QPen, QColor
from PySide6.QtWidgets import QLabel


class CollapseToggleButton(QLabel):
    clicked = Signal()

    def __init__(self, expanded=True, size=28, parent=None):
        super().__init__(parent)
        self._expanded = expanded
        self._hovered = False
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self._update_tooltip()

    def _update_tooltip(self):
        self.setToolTip("Click to hide" if self._expanded else "Click to show")

    def set_expanded(self, expanded):
        self._expanded = expanded
        self._update_tooltip()
        self.update()

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

        bg = QColor("#e9ecef") if self._hovered else QColor("#f1f3f5")
        border = QColor("#adb5bd") if self._hovered else QColor("#dee2e6")
        painter.setBrush(bg)
        painter.setPen(QPen(border, 1.4))
        painter.drawEllipse(1, 1, self.width() - 2, self.height() - 2)

        pen = QPen(QColor("#495057"))
        pen.setWidthF(max(1.6, self.width() * 0.08))
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)

        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        half_w = w * 0.18
        chevron_h = h * 0.12

        if self._expanded:
            painter.drawPolyline([
                QPointF(cx - half_w, cy + chevron_h / 2),
                QPointF(cx, cy - chevron_h / 2),
                QPointF(cx + half_w, cy + chevron_h / 2),
            ])
        else:
            painter.drawPolyline([
                QPointF(cx - half_w, cy - chevron_h / 2),
                QPointF(cx, cy + chevron_h / 2),
                QPointF(cx + half_w, cy - chevron_h / 2),
            ])
        painter.end()
