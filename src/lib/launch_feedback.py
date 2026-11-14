"""
Launch feedback - what the user sees between clicking an installed application and its window appearing.

Starting a program can take a few seconds, and a lot more for a large one. Nothing in Dinosaur-List moved during that time, so
people clicked again (and started several copies) or thought the launcher had hung. LaunchWatcher shows a small "Opening ..."
card over the launcher and closes it as soon as the program shows a window of its own.

How it notices the window (Windows only): a snapshot of the windows that exist before the launch is compared with the ones
that exist afterwards. A new visible window that is not the launcher's own, not a console and not hidden by Windows counts;
while the started program is known (its process and everything it started) only ITS windows count at first, and after a couple
of seconds any new window does - some programs start through helpers (cmd -> python, launch stubs), so their window belongs to
a process we could not follow.
"""

import ctypes
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import List, Optional, Set

from PySide6.QtCore import QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QLinearGradient, QMovie, QPainter
from PySide6.QtWidgets import QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QVBoxLayout, QWidget

_GIF = Path(__file__).resolve().parent.parent.parent / "Sw-icon" / "d2.gif"
_CONSOLE_CLASSES = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "PseudoConsoleWindow"}
_WIN = sys.platform == "win32"

_TEXT_WIDTH = 280             # pixels available for the title / note next to the logo
GIVE_UP_AFTER = 90.0          # seconds: the card never stays forever
SETTLE = 0.6                  # seconds the card stays after the window appeared (so "it opened" is seen)
FALLBACK_AFTER = 2.5          # seconds before ANY new window counts (see the module text)
EXIT_GRACE = 5.0              # seconds with nothing of the program left running before the card is dropped


# ── Windows helpers ──────────────────────────────────────────────────────────────────────────────────────

if _WIN:
    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
    _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    class _PROCESSENTRY32(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]


def visible_windows() -> List[dict]:
    """Every visible, real-looking top-level window: {hwnd, pid, cls, title}. Windows that Windows itself hides ("cloaked":
    suspended UWP frames) and tiny ones (tooltips, tray helpers) are left out."""
    out: List[dict] = []
    if not _WIN:
        return out

    def cb(hwnd, _):
        try:
            if not _user32.IsWindowVisible(hwnd):
                return True
            cloaked = ctypes.c_int(0)
            try:
                ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
            except OSError:
                pass
            if cloaked.value:
                return True
            rect = wintypes.RECT()
            _user32.GetWindowRect(hwnd, ctypes.byref(rect))
            if rect.right - rect.left < 100 or rect.bottom - rect.top < 60:
                return True
            pid = wintypes.DWORD(0)
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            cls = ctypes.create_unicode_buffer(256); _user32.GetClassNameW(hwnd, cls, 256)
            title = ctypes.create_unicode_buffer(256); _user32.GetWindowTextW(hwnd, title, 256)
            out.append({"hwnd": int(hwnd), "pid": int(pid.value), "cls": cls.value, "title": title.value})
        except Exception:
            pass
        return True

    _user32.EnumWindows(_WNDENUMPROC(cb), 0)
    return out


def process_tree(root: Optional[int]) -> Set[int]:
    """*root* and every process that was started by it, directly or not - even when a parent has already exited (a batch file
    that starts the program and ends). Only processes that are still running are in the answer."""
    if not _WIN or not root:
        return set()
    kernel32 = _kernel32
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    snap = kernel32.CreateToolhelp32Snapshot(2, 0)            # TH32CS_SNAPPROCESS
    parents, alive = {}, set()
    try:
        entry = _PROCESSENTRY32(); entry.dwSize = ctypes.sizeof(_PROCESSENTRY32)
        ok = kernel32.Process32FirstW(ctypes.c_void_p(snap), ctypes.byref(entry))
        while ok:
            parents[entry.th32ProcessID] = entry.th32ParentProcessID
            alive.add(entry.th32ProcessID)
            ok = kernel32.Process32NextW(ctypes.c_void_p(snap), ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(snap))
    family = {root}
    grew = True
    while grew:
        grew = False
        for pid, parent in parents.items():
            if parent in family and pid not in family:
                family.add(pid)
                grew = True
    return {p for p in family if p in alive}


def snapshot_windows() -> Set[int]:
    """The window handles that exist right now (take this just BEFORE starting the program)."""
    return {w["hwnd"] for w in visible_windows()}


# ── the card ─────────────────────────────────────────────────────────────────────────────────────────────

class _Bar(QWidget):
    """A thin indeterminate bar: an indigo block sliding along a grey track."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(6)
        self._pos = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self):
        self._pos = (self._pos + 0.012) % 1.4
        self.update()

    def stop(self):
        self._timer.stop()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#e5e7eb"))
        p.drawRoundedRect(r, 3, 3)
        block = r.width() * 0.35
        x = (self._pos - 0.35) * r.width()
        g = QLinearGradient(x, 0, x + block, 0)
        g.setColorAt(0, QColor(67, 56, 202, 0)); g.setColorAt(0.5, QColor("#4338ca")); g.setColorAt(1, QColor(67, 56, 202, 0))
        p.setClipRect(r)
        p.setBrush(g)
        p.drawRoundedRect(QRectF(x, 0, block, r.height()), 3, 3)


class LaunchOverlay(QWidget):
    """The "Opening <name> ..." card. It never takes the keyboard focus (the program that is starting should have it) and
    sits above the launcher, centred over its window."""

    def __init__(self, host: Optional[QWidget], name: str):
        super().__init__(None)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._host = host

        card = QFrame(self)
        card.setObjectName("card")
        card.setStyleSheet("QFrame#card { background: white; border: 1px solid #e5e7eb; border-radius: 16px; }")
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(28); shadow.setOffset(0, 4); shadow.setColor(Qt.black)
        card.setGraphicsEffect(shadow)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.addWidget(card)

        logo = QLabel()
        logo.setFixedSize(56, 56)
        logo.setAlignment(Qt.AlignCenter)
        logo.setStyleSheet("background: #e6f7f0; border-radius: 12px;")
        self._movie = None
        if _GIF.exists():
            self._movie = QMovie(str(_GIF))
            self._movie.setScaledSize(logo.size() * 0.8)
            logo.setMovie(self._movie)
            self._movie.start()
        else:
            logo.setText("🦕")

        self._title = QLabel()
        self._title.setStyleSheet("font-size: 17px; font-weight: bold; color: #111827; background: transparent;")
        self._title.setFixedWidth(_TEXT_WIDTH)
        self._sub = QLabel("This can take a moment for larger applications.")
        self._sub.setWordWrap(True)
        self._sub.setStyleSheet("font-size: 12px; color: #6b7280; background: transparent;")
        self._sub.setFixedWidth(_TEXT_WIDTH)
        self._sub.ensurePolished()
        self._sub.setFixedHeight(self._sub.fontMetrics().lineSpacing() * 2 + 4)      # room for two lines, always: no jumping
        self.set_title(f"Opening {name}")
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addStretch(1); titles.addWidget(self._title); titles.addWidget(self._sub); titles.addStretch(1)
        head = QHBoxLayout()
        head.setSpacing(16)
        head.addWidget(logo); head.addLayout(titles, 1)

        self._bar = _Bar()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.addLayout(head)
        lay.addSpacing(14); lay.addWidget(self._bar)

        self.setFixedWidth(420)
        self.layout().activate()
        self.setFixedSize(420, self.layout().totalHeightForWidth(420))

    def set_note(self, text: str) -> None:
        self._sub.setText(text)

    def set_title(self, text: str) -> None:
        """One line: a long application name is shortened with ... (the whole name is in the tooltip)."""
        self._title.ensurePolished()
        self._title.setText(self._title.fontMetrics().elidedText(text, Qt.ElideRight, _TEXT_WIDTH))
        self._title.setToolTip(text)

    def popup(self) -> None:
        try:
            geo = self._host.frameGeometry() if self._host is not None and self._host.isVisible() else self.screen().availableGeometry()
        except Exception:
            geo = self.screen().availableGeometry()
        self.move(geo.center().x() - self.width() // 2, geo.center().y() - self.height() // 2)
        self.show()
        self.raise_()

    def close(self):                       # noqa: A003 - Qt's name
        self._bar.stop()
        if self._movie:
            self._movie.stop()
        super().close()


# ── the watcher ──────────────────────────────────────────────────────────────────────────────────────────

class LaunchWatcher(QObject):
    """Shows the card for one launch and closes it when the program's window is up. finished(reason) is emitted exactly once:
         "opened"   a window of the program appeared
         "exited"   the program (and everything it started) ended without showing a window
         "timeout"  GIVE_UP_AFTER seconds passed"""
    finished = Signal(str)

    def __init__(self, host: Optional[QWidget], name: str, pid: Optional[int] = None, before: Optional[Set[int]] = None,
                 give_up_after: float = GIVE_UP_AFTER):
        super().__init__(host)
        self._pid = pid
        self._before = before if before is not None else set()
        self._own_pid = os.getpid()
        self._t0 = time.monotonic()
        self._last_alive = self._t0
        self._give_up = give_up_after
        self._done = False
        self._found_at: Optional[float] = None
        self.overlay = LaunchOverlay(host, name)
        self.overlay.popup()
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

    def raise_overlay(self) -> None:
        if not self._done:
            self.overlay.popup()

    def _qualifies(self, w: dict, family: Set[int], elapsed: float) -> bool:
        if w["hwnd"] in self._before or w["pid"] == self._own_pid or w["cls"] in _CONSOLE_CLASSES:
            return False
        if w["pid"] in family:
            return True
        return elapsed > FALLBACK_AFTER                       # a helper started it: any new window will do

    def _poll(self):
        if self._done:
            return
        now = time.monotonic()
        elapsed = now - self._t0
        if self._found_at is not None:
            if now - self._found_at >= SETTLE:
                self._finish("opened")
            return
        family = process_tree(self._pid) if self._pid else set()
        if family:
            self._last_alive = now
        if any(self._qualifies(w, family, elapsed) for w in visible_windows()):
            self._found_at = now
            self.overlay.set_title("Opened")
            self.overlay.set_note("The application is ready.")
            return
        if self._pid and not family and now - self._last_alive > EXIT_GRACE:
            self._finish("exited")
            return
        if elapsed > self._give_up:
            self._finish("timeout")
            return
        if elapsed > 10:
            self.overlay.set_note("Still starting - larger applications can take 30 seconds or more.")

    def _finish(self, reason: str) -> None:
        if self._done:
            return
        self._done = True
        self._timer.stop()
        self.overlay.close()
        self.overlay.deleteLater()
        self.finished.emit(reason)
        self.deleteLater()
