"""
ProgressLogWindow - the Refresh dashboard (port of the Compiler's ProgressLogWindow).

A separate window shown while a refresh runs: current phase, progress bar, percent, elapsed time and a live log of
every step. Messages can arrive dozens of times per second, so lines and percent are queued and painted in one batch
about every 120 ms instead of repainting for each one (that stutter is what the batching avoids).
"""

import time
from collections import deque

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout)

from .dashboard_widgets import GREEN, INDIGO, RED, ProgressStrip

_BTN = ("QPushButton { background: #f3f4f6; color: #374151; border: 1px solid #d1d5db; border-radius: 8px; "
        "font-size: 13px; } QPushButton:hover { background: #e5e7eb; }")


# Modern slim scrollbars (same look as the News page): rounded thumb, no arrow buttons, subtle hover/pressed colours.
_LOG_STYLE = """
    QPlainTextEdit { background: #f8f9fa; border: 1px solid #e5e7eb; border-radius: 8px; padding: 6px; color: #24292f; }
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


class ProgressLogWindow(QDialog):
    def __init__(self, parent, title: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(False)
        self.resize(700, 500)
        self.setMinimumSize(520, 360)
        self.setStyleSheet("QDialog { background: white; }")
        self._finished = False
        self._start = time.monotonic()
        self._lines = deque()
        self._pending_fraction = None

        head = QLabel(f"🔄 {title}")
        head.setStyleSheet("font-size: 20px; font-weight: 800; color: #3730a3;")
        self._phase = QLabel("Starting…")
        self._phase.setWordWrap(True)
        self._phase.setStyleSheet("font-size: 15px; font-weight: 600; color: #1f2937;")
        self._percent = QLabel("")
        self._percent.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {INDIGO};")
        self._percent.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._bar = ProgressStrip()
        self._elapsed = QLabel("Elapsed 00:00")
        self._elapsed.setStyleSheet("font-size: 12px; color: #6b7280;")

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._log.setMaximumBlockCount(5000)
        mono = QFont("Consolas"); mono.setPointSize(9)
        self._log.setFont(mono)
        self._log.setStyleSheet(_LOG_STYLE)

        copy = QPushButton("Copy log"); close = QPushButton("Close")
        for b in (copy, close):
            b.setFixedSize(100, 34); b.setStyleSheet(_BTN); b.setCursor(Qt.PointingHandCursor)
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self._log.toPlainText()))
        close.clicked.connect(self.close)

        phase_row = QHBoxLayout()
        phase_row.addWidget(self._phase, 1); phase_row.addWidget(self._percent)
        buttons = QHBoxLayout()
        buttons.addWidget(copy); buttons.addStretch(1); buttons.addWidget(close)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(8)
        root.addWidget(head); root.addLayout(phase_row); root.addWidget(self._bar)
        root.addWidget(self._elapsed); root.addWidget(self._log, 1); root.addLayout(buttons)

        self._clock = QTimer(self); self._clock.setInterval(1000); self._clock.timeout.connect(self._update_elapsed)
        self._clock.start()
        self._drain_timer = QTimer(self); self._drain_timer.setInterval(120); self._drain_timer.timeout.connect(self._drain)
        self._bar.set_indeterminate()

    @property
    def finished_state(self) -> bool:
        return self._finished

    def _update_elapsed(self) -> None:
        s = int(time.monotonic() - self._start)
        self._elapsed.setText(f"Elapsed {s // 60:02d}:{s % 60:02d}")

    # ── API (call from the UI thread) ────────────────────────────────────────────

    def set_phase_text(self, text: str) -> None:
        if not self._finished:
            self._phase.setText(text)

    def set_phase(self, text: str, fraction=None) -> None:
        """Current step + progress (0..1); None = length unknown (sliding bar)."""
        self._phase.setText(text)
        if fraction is None:
            self._bar.set_indeterminate(); self._percent.setText("")
        else:
            self.set_percent(fraction)
        self._update_elapsed()

    def set_percent(self, fraction: float) -> None:
        if self._finished:
            return
        self._pending_fraction = fraction
        if not self._drain_timer.isActive():
            self._drain_timer.start()

    def log(self, line: str) -> None:
        self._lines.append(f"[{time.strftime('%H:%M:%S')}] {line.rstrip()}")
        if not self._drain_timer.isActive():
            self._drain_timer.start()

    def _drain(self) -> None:
        any_work = False
        if self._lines:
            batch = []
            while self._lines:
                batch.append(self._lines.popleft())
            self._log.appendPlainText("\n".join(batch))
            sb = self._log.verticalScrollBar()
            sb.setValue(sb.maximum())
            any_work = True
        if self._pending_fraction is not None and not self._finished:
            f = self._pending_fraction
            self._bar.set_fraction(f)
            self._percent.setText(f"{round(f * 100)}%")
            self._pending_fraction = None
            any_work = True
        if not any_work:
            self._drain_timer.stop()

    def finish(self, ok: bool, message: str) -> None:
        self._finished = True
        self._clock.stop(); self._update_elapsed()
        color = GREEN if ok else RED
        self._bar.set_brush(color); self._bar.set_fraction(1)
        self._percent.setText("100%" if ok else "")
        self._percent.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {color};")
        self._phase.setText(("✓ " if ok else "⚠ ") + message)
        self._phase.setStyleSheet(f"font-size: 15px; font-weight: 600; color: {color};")
        self.log(("DONE: " if ok else "FAILED: ") + message)
        self._drain()                        # show everything still queued right away

    def closeEvent(self, event) -> None:
        self._clock.stop(); self._drain_timer.stop()
        super().closeEvent(event)
