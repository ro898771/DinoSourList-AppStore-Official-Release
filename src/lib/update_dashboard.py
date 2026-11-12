"""
UpdateDashboard - the start-up GitHub update window (GUI version of runtime.exe's console flow; port of the Compiler's
UpdateWindow).

It is the first thing on screen. The GitHub check was started before the window existed and the main window is built
behind it, so the dashboard costs no extra launch time: it says "up to date" for a moment and fades away. Only when there
is something to decide (an update, or GitHub unreachable) does it wait for the user.
"""

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QEventLoop, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QMovie
from PySide6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from . import app_log, updater
from .dashboard_widgets import AMBER, GREEN, INDIGO, RED, ProgressStrip

_GIF = Path(__file__).resolve().parent.parent.parent / "Sw-icon" / "d2.gif"

_BTN_PRIMARY = ("QPushButton { background: #4338ca; color: white; border: none; border-radius: 8px; padding: 0 18px; "
                "font-size: 13px; font-weight: 600; } QPushButton:hover { background: #3730a3; }")
_BTN_PLAIN = ("QPushButton { background: #f3f4f6; color: #374151; border: 1px solid #d1d5db; border-radius: 8px; "
              "padding: 0 18px; font-size: 13px; } QPushButton:hover { background: #e5e7eb; }")


class _ClickableLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()
            e.accept()
        else:
            super().mousePressEvent(e)


@dataclass
class Outcome:
    state: str = updater.UNREACHABLE
    applied: bool = False
    restarting: bool = False        # an update was applied and a new copy of the app is starting: do not open the window


class UpdateDashboard(QWidget):
    _progress = Signal(str, float)  # message, fraction (-1 = busy / unknown)

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._width = 560
        self._choice: Optional[bool] = None
        self._choice_loop: Optional[QEventLoop] = None
        self._drag = None
        self._out = Outcome()
        self._hold = 0              # >0 while the revert picker is open: run() must not finish underneath it
        self._applying = False

        card = QFrame(self)
        card.setObjectName("card")
        card.setStyleSheet("QFrame#card { background: white; border: 1px solid #e5e7eb; border-radius: 16px; }")
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(28); shadow.setOffset(0, 4); shadow.setColor(Qt.black)
        card.setGraphicsEffect(shadow)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.addWidget(card)

        self._logo = _ClickableLabel()
        self._logo.setCursor(Qt.PointingHandCursor)
        self._logo.setToolTip("Click to revert to a previous version")
        self._logo.clicked.connect(self.open_revert)
        self._logo.setFixedSize(56, 56)
        self._logo.setAlignment(Qt.AlignCenter)
        self._logo.setStyleSheet("background: #e6f7f0; border-radius: 12px;")
        self._movie = None
        if _GIF.exists():
            self._movie = QMovie(str(_GIF))
            self._movie.setScaledSize(self._logo.size() * 0.8)
            self._logo.setMovie(self._movie)
            self._movie.start()

        self._title = QLabel("Checking for updates")
        self._title.setStyleSheet("font-size: 18px; font-weight: bold; color: #111827;")
        self._sub = QLabel(f"github.com/{updater.repo()} · {updater.branch()}")
        self._sub.setStyleSheet("font-size: 12px; color: #6b7280;")
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(self._title); titles.addWidget(self._sub)
        head = QHBoxLayout()
        head.setSpacing(16)
        head.addWidget(self._logo, 0, Qt.AlignTop)
        head.addLayout(titles, 1)

        self._bar = ProgressStrip()
        self._bar.set_indeterminate()
        self._status = QLabel("Contacting GitHub…")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("font-size: 13.5px; color: #374151;")
        self._details = QLabel("")
        self._details.setWordWrap(True)
        self._details.setStyleSheet("font-size: 12px; color: #6b7280;")

        self._yes = QPushButton(); self._no = QPushButton()
        for b, style in ((self._yes, _BTN_PRIMARY), (self._no, _BTN_PLAIN)):
            b.setFixedHeight(36); b.setMinimumWidth(120); b.setStyleSheet(style); b.setCursor(Qt.PointingHandCursor)
        self._yes.clicked.connect(lambda: self._decide(True))
        self._no.clicked.connect(lambda: self._decide(False))
        self._buttons = QWidget()
        brow = QHBoxLayout(self._buttons)
        brow.setContentsMargins(0, 0, 0, 0); brow.setSpacing(10)
        brow.addStretch(1); brow.addWidget(self._no); brow.addWidget(self._yes)
        self._buttons.setVisible(False)
        # The buttons appear and disappear: keep their room reserved so showing them never changes the window height.
        policy = self._buttons.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self._buttons.setSizePolicy(policy)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(28, 26, 28, 22)
        lay.addLayout(head)
        lay.addSpacing(18); lay.addWidget(self._bar); lay.addSpacing(10)
        lay.addWidget(self._status); lay.addSpacing(6); lay.addWidget(self._details)
        lay.addSpacing(14); lay.addWidget(self._buttons)

        self._progress.connect(self._on_progress)
        self._reserve_text_room()

    def _reserve_text_room(self) -> None:
        """Give the status (3 lines) and details (4 lines) text a FIXED height, measured with the real font on this
        screen's DPI. Wrapped labels otherwise change height with their text and with DPI rounding, so the window's
        minimum height moved after it was shown (Qt: "Unable to set geometry ... Resulting geometry ...")."""
        self.ensurePolished()
        for label, lines in ((self._status, 3), (self._details, 4)):
            label.ensurePolished()
            label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            label.setFixedHeight(label.fontMetrics().lineSpacing() * lines + 6)

    # ── helpers ──────────────────────────────────────────────────────────────────

    def _say(self, status: str, details: Optional[str] = None) -> None:
        self._status.setText(status)
        if details is not None:
            self._details.setText(details)

    def _on_progress(self, message: str, fraction: float) -> None:
        self._status.setText(message)
        if fraction < 0:
            self._bar.set_indeterminate()
        else:
            self._bar.set_fraction(fraction)

    def _decide(self, value: bool) -> None:
        self._choice = value
        if self._choice_loop is not None:
            self._choice_loop.quit()

    def _ask(self, yes: str, no: str, seconds: int = 0, on_tick=None) -> bool:
        """Show [no] [yes] and wait. With *seconds*, the answer is False when the countdown ends (on_tick(left) each second)."""
        self._yes.setText(yes); self._no.setText(no)
        self._buttons.setVisible(True)
        self._choice = None
        loop = self._choice_loop = QEventLoop()
        timer = None
        if seconds:
            left = [seconds]

            def tick():
                left[0] -= 1
                if on_tick:
                    on_tick(left[0])
                if left[0] <= 0:
                    loop.quit()
            timer = QTimer(self)
            timer.setInterval(1000)
            timer.timeout.connect(tick)
            timer.start()
        if self._choice is None:
            loop.exec()
        if timer:
            timer.stop()
        self._choice_loop = None
        self._buttons.setVisible(False)
        return bool(self._choice)

    @staticmethod
    def _wait(fn, timeout_poll_ms: int = 30):
        """Runs fn() on a worker thread and keeps the UI alive until it returns. Returns (result, exception)."""
        box = {}

        def work():
            try:
                box["r"] = fn()
            except Exception as exc:        # reported to the caller, never raised in the thread
                box["e"] = exc
        t = threading.Thread(target=work, daemon=True)
        t.start()
        loop = QEventLoop()
        poll = QTimer()
        poll.setInterval(timeout_poll_ms)
        poll.timeout.connect(lambda: None if t.is_alive() else loop.quit())
        poll.start()
        if t.is_alive():
            loop.exec()
        poll.stop()
        return box.get("r"), box.get("e")

    def _sleep(self, ms: int) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    # ── the flow ─────────────────────────────────────────────────────────────────

    def run(self, check_future) -> Outcome:
        """Drives the dashboard. *check_future* is a concurrent.futures.Future[UpdateCheck] started before the window was built."""
        shown = time.monotonic()
        out = self._out
        try:
            while not check_future.done():
                self._sleep(30)
            result = check_future.result()

            # GitHub unreachable (DNS hiccup, VPN off): say why, count down to "continue", let the user retry right away
            while result.state == updater.UNREACHABLE:
                self._title.setText("Update check skipped")
                self._bar.set_brush(AMBER); self._bar.set_fraction(1)
                self._say(f"ℹ️ {result.error}", "Starting with the installed version in 5…   Press Retry to check again.")
                retry = self._ask("Retry", "Continue", seconds=5,
                                  on_tick=lambda s: self._details.setText(
                                      f"Starting with the installed version in {max(s, 0)}…   Press Retry to check again."))
                if not retry:
                    break
                self._title.setText("Checking for updates")
                self._bar.set_brush(INDIGO); self._bar.set_indeterminate()
                self._say("Contacting GitHub…", "")
                result, _ = self._wait(lambda: updater.check(7))
            out.state = result.state
            app_log.log(f"GitHub update check: {result.state}  (installed {updater.shorten(result.local)}, "
                                f"latest {updater.shorten(result.remote)})" + (f"  - {result.error}" if result.error else ""))

            if result.state == updater.UP_TO_DATE:
                self._title.setText("You're up to date")
                self._bar.set_brush(GREEN); self._bar.set_fraction(1)
                self._say(f"✅ This copy is on the latest version ({updater.shorten(result.remote)}).", "Starting Dinosaur-List…")
            elif result.state == updater.UNREACHABLE:
                self._title.setText("Update check skipped")
                self._say(f"ℹ️ {result.error}", "Starting with the installed version…")
            else:                              # FIRST_INSTALL / AVAILABLE: always ask, never apply silently
                self._title.setText("Update available")
                self._bar.set_brush(INDIGO); self._bar.set_fraction(0)
                if result.state == updater.FIRST_INSTALL:
                    text = (f"🆕 No update has been recorded for this copy yet.\n"
                            f"Latest version: {updater.shorten(result.remote)}. Do you want to update now?")
                else:
                    text = (f"🆕 {updater.shorten(result.local)}  →  {updater.shorten(result.remote)}\n"
                            f"Do you want to update now?")
                self._say(text)
                commits, _ = self._wait(lambda: updater.fetch_recent_commits(3))
                if commits:
                    self._details.setText("What's new:\n" + "\n".join(f"• {c.title}" for c in commits))
                if self._ask("Update now", "Skip for now"):
                    app_log.log("Update accepted - downloading and applying")
                    self._apply(result.remote, out)
                else:
                    app_log.log("Update skipped by the user")
                    self._say("Update skipped — keeping the installed version.", "")
                    self._sleep(500)

            if not out.restarting:
                min_show = 1100 if result.state == updater.UP_TO_DATE else 0     # a verdict must not flash past
                left = min_show - int((time.monotonic() - shown) * 1000)
                if left > 0:
                    self._sleep(left)
            while self._hold or self._applying:      # the revert picker / a revert in progress is still being used
                self._sleep(50)
        except Exception as exc:
            try:
                self._say(f"⚠️ Update failed: {exc}")
                self._sleep(1500)
            except Exception:
                pass
        return out

    def open_revert(self) -> None:
        """The dinosaur was clicked: let the user pick an earlier version and go back to it."""
        if self._hold or self._applying:
            return
        from .revert_dialog import RevertDialog
        self._hold += 1
        try:
            dlg = RevertDialog(self)
            picked = dlg.exec() == RevertDialog.Accepted and dlg.chosen_sha
        finally:
            self._hold -= 1
        if picked:
            self.revert(dlg.chosen_sha)

    def revert(self, sha: str) -> Outcome:
        """Go back to *sha* (the same download-and-apply as an update). The app restarts afterwards (out.restarting)."""
        app_log.log(f"Reverting to version {updater.shorten(sha)}")
        self._apply(sha, self._out, "Reverting", "Reverted")
        if self._choice_loop is not None:          # the start-up question is still open: this answers it
            self._decide(False)
        return self._out

    def _apply(self, sha: str, out: Outcome, verb: str = "Updating", done: str = "Updated") -> None:
        self._applying = True
        try:
            self._apply_now(sha, out, verb, done)
        finally:
            self._applying = False

    def _apply_now(self, sha: str, out: Outcome, verb: str, done: str) -> None:
        self._title.setText(verb)
        self._bar.set_brush(INDIGO); self._bar.set_indeterminate()
        self._say("Preparing…", "")
        try:
            from .box_daemon import BoxDaemon
            BoxDaemon.instance().shutdown()      # BoxFast.exe may be replaced by the update: it must not be running
        except Exception:
            pass
        result, error = self._wait(lambda: updater.download_and_apply(
            sha, lambda m, f: self._progress.emit(m, -1.0 if f is None else f)))
        if error is not None or result is None:
            self._title.setText(f"{verb.replace('ing', '')} failed" if verb != "Updating" else "Update failed")
            self._bar.set_brush(RED); self._bar.set_fraction(1)
            self._say(f"⚠️ {error}\nThe app will start with the installed version.")
            self._sleep(2500)
            return
        out.applied = True
        run_install = result.dependencies_changed or updater.venv_missing()
        self._title.setText(done)
        self._bar.set_brush(GREEN); self._bar.set_fraction(1)
        note = f" ({len(result.skipped)} file(s) in use were skipped)" if result.skipped else ""
        self._say(f"✅ Now on version {updater.shorten(sha)}{note}.",
                  "Updating dependencies, then restarting…" if run_install else "Restarting…")
        self._sleep(1000)
        updater.restart(run_install)
        out.restarting = True

    # ── window behaviour ─────────────────────────────────────────────────────────

    def fade_out(self, ms: int = 260) -> None:
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(ms)
        anim.setStartValue(self.windowOpacity()); anim.setEndValue(0.0)
        loop = QEventLoop()
        anim.finished.connect(loop.quit)
        QTimer.singleShot(ms + 200, loop.quit)
        anim.start()
        loop.exec()
        if self._movie:
            self._movie.stop()
        self.hide()

    def center_on_screen(self) -> None:
        # Everything has a deterministic height now, so the window can be locked to exactly its own measured size.
        self.layout().activate()
        self.setFixedSize(self._width, self.layout().totalHeightForWidth(self._width))
        screen = self.screen().availableGeometry()
        self.move(screen.center().x() - self.width() // 2, screen.center().y() - self.height() // 2)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _e):
        self._drag = None
