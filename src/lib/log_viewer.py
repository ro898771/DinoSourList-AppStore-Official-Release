"""
LogPanel - Settings > Log: the app's output (Log_data/app_output.log) shown live, newest line at the bottom.

It follows the file while it is open (new lines appear within a second) and keeps following unless the user scrolls up to
read something. Under python.exe from a terminal there is no file - the output is in that terminal, and the panel says so.
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from . import app_log
from .dashboard_widgets import MODERN_SCROLLBAR_STYLE

_MAX_INITIAL = 1_000_000        # bytes: a huge old log is shown from its tail
_MAX_LINES = 5000

_STYLE = """
    QLabel { color: #000000; background: transparent; }
    QLabel#hint { color: #4b5563; font-size: 12px; }
    QPlainTextEdit { background: #f8f9fa; color: #24292f; border: 1px solid #e5e7eb; border-radius: 8px; padding: 6px; }
    QPushButton { background: #f3f4f6; color: #000000; border: 1px solid #d1d5db; border-radius: 8px; padding: 0 16px; font-size: 13px; }
    QPushButton:hover { background: #e5e7eb; }
""" + MODERN_SCROLLBAR_STYLE


class LogPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(_STYLE)
        self._pos = 0
        self._partial = b""

        self._hint = QLabel(); self._hint.setObjectName("hint"); self._hint.setWordWrap(True)
        self._view = QPlainTextEdit()
        self._view.setReadOnly(True)
        self._view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._view.setMaximumBlockCount(_MAX_LINES)
        self._view.setMinimumHeight(300)
        self._view.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        font = QFont("Consolas"); font.setStyleHint(QFont.Monospace); font.setPointSize(9)
        self._view.setFont(font)

        copy = QPushButton("Copy"); clear = QPushButton("Clear view")
        for b in (copy, clear):
            b.setFixedHeight(34); b.setMinimumWidth(100); b.setCursor(Qt.PointingHandCursor)
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self._view.toPlainText()))
        clear.clicked.connect(self._view.clear)
        row = QHBoxLayout(); row.addWidget(copy); row.addWidget(clear); row.addStretch(1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(10)
        lay.addWidget(self._hint); lay.addWidget(self._view, 1); lay.addLayout(row)

        if not app_log.managed():
            self._hint.setText("The app was started from a terminal, so its output is shown in that terminal window "
                               "(no log file is written). Start it with run.cmd to see the log here.")
            return
        self._hint.setText(f"Live output of this session  ·  {app_log.log_path()}")
        self._timer = QTimer(self)
        self._timer.setInterval(700)
        self._timer.timeout.connect(self._poll)
        self._poll()
        self._timer.start()

    def _poll(self) -> None:
        try:
            with open(app_log.log_path(), "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                if size < self._pos:                      # the file was recreated
                    self._pos, self._partial = 0, b""
                    self._view.clear()
                if self._pos == 0 and size > _MAX_INITIAL:
                    self._pos = size - _MAX_INITIAL
                if size == self._pos:
                    return
                f.seek(self._pos)
                data = f.read(size - self._pos)
                self._pos = size
        except OSError:
            return
        data = self._partial + data
        cut = data.rfind(b"\n")
        if cut < 0:                                       # no complete line yet
            self._partial = data
            return
        self._partial = data[cut + 1:]
        text = data[:cut].decode("utf-8", "replace").replace("\r", "")
        bar = self._view.verticalScrollBar()
        follow = bar.value() >= bar.maximum() - 4
        self._view.appendPlainText(text)
        if follow:
            bar.setValue(bar.maximum())
