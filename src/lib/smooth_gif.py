"""
SmoothGif - plays an animated GIF on a QLabel, sharp at any size and on any display scaling.

QMovie.setScaledSize() looks soft for two reasons: it shrinks every frame with a rough method (a 200 px picture drawn at 44 px
is noticeably jagged), and the size is in "logical" pixels, so on a screen set to 150-250 % Windows then stretches that small
picture back up. SmoothGif shrinks each frame once, with Qt's smooth filter, to the size in REAL screen pixels, and tells Qt
the picture's pixel ratio so it is drawn 1:1. The shrunk frames are cached, so the several places that show the dinosaur
share the work.
"""

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, QSize, Qt, QTimer
from PySide6.QtGui import QMovie, QPixmap
from PySide6.QtWidgets import QLabel

_RAW: Dict[str, Tuple[list, List[int]]] = {}                 # path -> (frames at the file's own size, delays in ms)
_SCALED: Dict[Tuple[str, int], List[QPixmap]] = {}           # (path, device pixels) -> shrunk frames


def _decode(path: str):
    """Every frame of the GIF at its own size, with the time each is shown. (QMovie composes the frames properly.)"""
    if path not in _RAW:
        movie = QMovie(path)
        movie.setCacheMode(QMovie.CacheAll)
        frames, delays = [], []
        for i in range(max(1, movie.frameCount())):
            if not movie.jumpToFrame(i):
                break
            frames.append(movie.currentImage().copy())
            delays.append(max(20, movie.nextFrameDelay() or 100))
        _RAW[path] = (frames, delays)
    return _RAW[path]


class SmoothGif(QObject):
    def __init__(self, label: QLabel, path, size_px: int):
        super().__init__(label)
        self._label = label
        self._path = str(path)
        self._size = int(size_px)
        self._dpr = 1.0
        self._pixmaps: List[QPixmap] = []
        self._delays: List[int] = []
        self._i = 0
        self._running = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._advance)
        self._render()

    # ── QMovie-like surface, so call sites change very little ────────────────────────────────────────
    def setScaledSize(self, size: QSize) -> None:
        self.set_size(size.width())

    def set_size(self, size_px: int) -> None:
        if int(size_px) != self._size:
            self._size = int(size_px)
            self._render()

    def start(self) -> None:
        if not self._pixmaps:
            return
        self._running = True
        self._show_frame()
        self._timer.start(self._delays[self._i])

    def stop(self) -> None:
        self._running = False
        self._timer.stop()

    # ── internals ───────────────────────────────────────────────────────────────────────────────────────
    def _current_dpr(self) -> float:
        try:
            return float(self._label.devicePixelRatioF()) or 1.0
        except RuntimeError:
            return 1.0

    def _render(self) -> None:
        """(Re)build the frames for the current size and screen scaling."""
        if not Path(self._path).exists():
            return
        self._dpr = self._current_dpr()
        device_px = max(1, round(self._size * self._dpr))
        key = (self._path, device_px)
        if key not in _SCALED:
            frames, _ = _decode(self._path)
            pixmaps = []
            for image in frames:
                pm = QPixmap.fromImage(image.scaled(device_px, device_px, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                pm.setDevicePixelRatio(self._dpr)
                pixmaps.append(pm)
            _SCALED[key] = pixmaps
        self._pixmaps = _SCALED[key]
        self._delays = _decode(self._path)[1]
        self._i = min(self._i, len(self._pixmaps) - 1) if self._pixmaps else 0
        self._show_frame()

    def _show_frame(self) -> None:
        if self._pixmaps:
            self._label.setPixmap(self._pixmaps[self._i])

    def _advance(self) -> None:
        if not self._running or not self._pixmaps:
            return
        if abs(self._current_dpr() - self._dpr) > 0.01:      # the window moved to a screen with another scaling
            self._render()
        self._i = (self._i + 1) % len(self._pixmaps)
        self._show_frame()
        self._timer.start(self._delays[self._i])
