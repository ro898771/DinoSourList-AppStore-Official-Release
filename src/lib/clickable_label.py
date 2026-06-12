"""
ClickableLabel - A QLabel that emits a signal when clicked
"""

from PySide6.QtWidgets import QLabel, QToolTip
from PySide6.QtCore import Qt, Signal, QEvent


class ClickableLabel(QLabel):
    """Custom QLabel that emits a signal when clicked"""
    clicked = Signal()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()  # Stop event from propagating to parent
        else:
            super().mousePressEvent(event)

    def event(self, e):
        if e.type() == QEvent.Type.ToolTip:
            tip = self.toolTip()
            if tip:
                from PySide6.QtGui import QCursor
                QToolTip.showText(QCursor.pos(), tip, self)
            return True
        return super().event(e)
