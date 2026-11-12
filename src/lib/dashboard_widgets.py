"""Small shared widgets for the update dashboard and the refresh progress dashboard (port of the Compiler's ProgressStrip)."""

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

INDIGO, GREEN, AMBER, RED = "#4338ca", "#16a34a", "#f59e0b", "#dc2626"


class ProgressStrip(QWidget):
    """Slim rounded progress strip: determinate fill, or a sliding bar while the length is unknown."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(14)
        self._fraction = 0.0
        self._indeterminate = False
        self._brush = QColor(INDIGO)
        self._offset = 0.0                    # 0..1 position of the sliding bar
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def set_brush(self, hex_color: str) -> None:
        self._brush = QColor(hex_color)
        self.update()

    def set_fraction(self, fraction: float) -> None:
        self._fraction = max(0.0, min(1.0, fraction))
        if self._indeterminate:
            self._indeterminate = False
            self._timer.stop()
        self.update()

    def set_indeterminate(self) -> None:
        if self._indeterminate:
            return
        self._indeterminate = True
        self._timer.start()

    def _tick(self) -> None:
        self._offset += 0.012
        if self._offset > 1.0:
            self._offset = 0.0
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        track = QPainterPath()
        track.addRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
        p.fillPath(track, QColor("#e5e7eb"))
        p.setClipPath(track)
        if self._indeterminate:
            bar_w = max(20.0, w * 0.35)
            x = -bar_w + self._offset * (w + bar_w)
            rect = QRectF(x, 0, bar_w, h)
        else:
            rect = QRectF(0, 0, w * self._fraction, h)
        bar = QPainterPath()
        bar.addRoundedRect(rect, h / 2, h / 2)
        p.fillPath(bar, self._brush)
        p.end()


# Slim rounded scrollbars (no arrow buttons) - append to any widget's style sheet that scrolls.
MODERN_SCROLLBAR_STYLE = """
    QScrollBar:vertical { background: transparent; width: 12px; margin: 4px 2px 4px 0; }
    QScrollBar::handle:vertical { background: #c7cad3; border-radius: 4px; min-height: 36px; margin: 0 2px; }
    QScrollBar::handle:vertical:hover { background: #9aa0b0; }
    QScrollBar::handle:vertical:pressed { background: #4338ca; }
    QScrollBar:horizontal { background: transparent; height: 12px; margin: 0 4px 2px 4px; }
    QScrollBar::handle:horizontal { background: #c7cad3; border-radius: 4px; min-width: 36px; margin: 2px 0; }
    QScrollBar::handle:horizontal:hover { background: #9aa0b0; }
    QScrollBar::handle:horizontal:pressed { background: #4338ca; }
    QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: none; border: none; }
    QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
    QAbstractScrollArea::corner { background: transparent; }
"""
