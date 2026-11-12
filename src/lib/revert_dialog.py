"""
Revert to a previous version: pick one of the most recent GitHub commits to go back to (port of the Compiler's
"Revert to a previous version").

RevertPanel is the picker itself (used inside Settings); RevertDialog wraps it in a window (used by the update
dashboard's dinosaur). Both only CHOOSE; whoever uses them applies the pick (UpdateDashboard.revert), because applying
restarts the app. The commit list is fetched on a worker thread, so the panel appears at once.
"""

import threading
from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QSizePolicy,
                               QPushButton, QVBoxLayout, QWidget)

from . import updater
from .dashboard_widgets import MODERN_SCROLLBAR_STYLE

_STYLE = """
    QLabel { color: #000000; background: transparent; }
    QLabel#title { font-size: 17px; font-weight: bold; }
    QLabel#hint { color: #4b5563; font-size: 12px; }
    QListWidget { background: #ffffff; color: #000000; border: 1px solid #d5d8e0; border-radius: 8px; font-size: 13px; outline: 0; }
    QListWidget::item { padding: 9px 10px; border-bottom: 1px solid #f0f1f5; }
    QListWidget::item:hover { background: #f3f4ff; color: #000000; }
    QListWidget::item:selected { background: #e0e3ff; color: #000000; }
    QPushButton { background: #f3f4f6; color: #000000; border: 1px solid #d1d5db; border-radius: 8px; padding: 0 18px; font-size: 13px; }
    QPushButton:hover { background: #e5e7eb; }
    QPushButton#revert { background: #4338ca; color: #ffffff; border: none; font-weight: 600; }
    QPushButton#revert:hover { background: #3730a3; }
    QPushButton#revert:disabled { background: #c7c9e8; color: #ffffff; }
""" + MODERN_SCROLLBAR_STYLE


class RevertPanel(QWidget):
    chosen = Signal(str)        # the commit sha the user wants to go back to
    cancelled = Signal()
    _loaded = Signal(object)    # list[Commit] | None

    def __init__(self, parent=None, show_title: bool = True, show_cancel: bool = True):
        super().__init__(parent)
        self.setStyleSheet(_STYLE)
        self._commits: List[updater.Commit] = []
        self._local = updater.read_local_sha()

        hint = QLabel(f"Pick one of the most recent versions of github.com/{updater.repo()} ({updater.branch()}). "
                      "The app restarts afterwards. Your downloaded software, favourites and settings are kept.")
        hint.setObjectName("hint"); hint.setWordWrap(True)
        self._list = QListWidget()
        self._list.setMinimumHeight(230)
        self._list.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)    # long titles are elided, never widen the page
        self._list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list.setTextElideMode(Qt.ElideRight)
        self._list.addItem("Loading recent versions…")
        self._list.setEnabled(False)
        self._status = QLabel(f"Installed version: {updater.shorten(self._local)}"); self._status.setObjectName("hint")

        self._revert = QPushButton("Revert"); self._revert.setObjectName("revert")
        self._revert.setFixedHeight(36); self._revert.setMinimumWidth(110); self._revert.setCursor(Qt.PointingHandCursor)
        self._revert.setEnabled(False)
        self._revert.clicked.connect(self._accept)
        self._list.itemSelectionChanged.connect(self._sync)
        self._list.itemDoubleClicked.connect(lambda _i: self._accept() if self._revert.isEnabled() else None)

        row = QHBoxLayout(); row.addWidget(self._status, 1)
        if show_cancel:
            cancel = QPushButton("Cancel")
            cancel.setFixedHeight(36); cancel.setMinimumWidth(110); cancel.setCursor(Qt.PointingHandCursor)
            cancel.clicked.connect(self.cancelled)
            row.addWidget(cancel)
        row.addWidget(self._revert)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(10)
        if show_title:
            title = QLabel("↩  Revert to a previous version"); title.setObjectName("title")
            lay.addWidget(title)
        lay.addWidget(hint); lay.addWidget(self._list, 1); lay.addLayout(row)

        self._loaded.connect(self._fill)
        threading.Thread(target=lambda: self._loaded.emit(updater.fetch_recent_commits(5, 6)),
                         daemon=True, name="RevertCommits").start()

    def _fill(self, commits) -> None:
        self._list.clear()
        if not commits:
            self._list.addItem(f"Could not fetch the version history. {updater.last_error()}".strip())
            return
        self._commits = list(commits)
        self._list.setEnabled(True)
        for c in self._commits:
            installed = bool(self._local) and c.sha == self._local
            text = f"{c.title}\n{updater.shorten(c.sha)}  ·  {c.date[:10]}" + ("   ·   ✓ installed now" if installed else "")
            item = QListWidgetItem(text)
            item.setToolTip(c.message.strip())
            self._list.addItem(item)
        self._list.setCurrentRow(1 if len(self._commits) > 1 and self._commits[0].sha == self._local else 0)

    def _sync(self) -> None:
        row = self._list.currentRow()
        self._revert.setEnabled(0 <= row < len(self._commits) and self._commits[row].sha != self._local)

    def _accept(self) -> None:
        row = self._list.currentRow()
        if 0 <= row < len(self._commits):
            self.chosen.emit(self._commits[row].sha)


class RevertDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Revert to a previous version")
        self.setModal(True)
        self.setFixedWidth(540)
        self.setStyleSheet("QDialog { background: #ffffff; }")
        self.chosen_sha: Optional[str] = None
        panel = RevertPanel(self)
        panel.chosen.connect(self._pick)
        panel.cancelled.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.addWidget(panel)

    def _pick(self, sha: str) -> None:
        self.chosen_sha = sha
        self.accept()
