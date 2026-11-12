"""Software Launcher Dashboard - Entry Point"""

import sys
import time
from datetime import datetime
from pathlib import Path

import threading
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtWidgets import QApplication
from lib import app_log, updater
from lib.boxlink_api import BoxLinkAPI
from lib.main_controller import MainWindow
from lib.update_dashboard import UpdateDashboard

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "User-API"))


def _clean_log_data():
    """Wipe Log_data/ at the start of every launch so old sessions' logs don't pile up.

    Best-effort end to end -- a locked file, a missing/unwritable folder, or any
    other OS-level failure is swallowed rather than raised, since this must
    never stop the app from starting.
    """
    try:
        log_dir = _REPO_ROOT / "Log_data"
        log_dir.mkdir(exist_ok=True)
        for f in log_dir.iterdir():
            if f.is_file():
                try:
                    f.unlink()
                except OSError:
                    pass
    except OSError:
        pass


def _log_launch_health():
    """Check the Telemetry API's health and append the result to Log_data/ before the UI shows.

    Never blocks or raises -- if the API is unreachable, or Log_data itself
    can't be created/written, the failure is swallowed and startup continues
    normally; telemetry being down shouldn't stop the launcher from opening.
    """
    try:
        from health_client import HealthClient
        from local_identity import LocalIdentity

        identity = LocalIdentity()
        username = identity.get_current_username()
        ip = identity.get_local_ip()
        ok, detail = HealthClient().check()
        status = "OK" if ok else f"FAIL ({detail})"
    except Exception as exc:
        username = ip = "unknown"
        status = f"FAIL (unexpected error: {exc})"
    app_log.log(f"Telemetry API health: {status}  (user={username}, ip={ip})")

    try:
        log_dir = _REPO_ROOT / "Log_data"
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / f"launch_{datetime.now():%Y-%m-%d}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] user={username} ip={ip} health={status}\n")
    except OSError:
        pass


def main():
    # Under pythonw there is no console: send output to Log_data/app_output.log (Settings > Log shows it).
    t_start = time.perf_counter()
    app_log.install_log()
    app_log.log("=" * 60)
    app_log.log("Dinosaur-List starting")
    app_log.log(f"Python {sys.version.split()[0]}  ·  {sys.executable}")
    app_log.log(f"App folder: {_REPO_ROOT}")
    app_log.log(f"Output: {'Log_data/app_output.log (Settings > Log)' if app_log.managed() else 'terminal'}")
    if updater.disabled():
        app_log.log("GitHub update check: disabled (DINOSAURLIST_NO_UPDATE=1)")
    else:
        app_log.log(f"GitHub update source: {updater.repo()} ({updater.branch()})  ·  installed version: {updater.shorten(updater.read_local_sha())}")
    # Start the persistent Box helper and its login right away: it overlaps with building the UI, so the first
    # refresh/download does not wait for it.
    BoxLinkAPI.warm_up()

    def _log_box_ready():
        app_log.log("Box helper (BoxFast.exe): ready - fast downloads on" if BoxLinkAPI.fast_available()
                            else "Box helper (BoxFast.exe): not available - using the slower BoxAutomate.exe fallback")
    threading.Thread(target=_log_box_ready, daemon=True, name="BoxReadyLog").start()
    _clean_log_data()
    # The health check is a network call (5 s timeout) -- it must never hold up the first frame.
    threading.Thread(target=_log_launch_health, daemon=True, name="LaunchHealth").start()

    # Like runtime.exe, look at GitHub for a newer version -- but the check starts NOW, in the background, and the update
    # dashboard is the first thing on screen. The main window is built behind it while GitHub answers, so the dashboard
    # costs no extra launch time; the app only appears once the dashboard has said "up to date" (or the user has decided).
    check_future = None if updater.disabled() else ThreadPoolExecutor(max_workers=1).submit(updater.check, 5)

    app = QApplication(sys.argv)
    app.setApplicationName("Software Launcher")
    app.setOrganizationName("DinasourList")

    dashboard = None
    if check_future is not None:
        dashboard = UpdateDashboard()
        dashboard.center_on_screen()
        dashboard.show()
        app.processEvents()                 # let the dashboard paint before the heavy window build

    app_log.log("Building the main window...")
    t_ui = time.perf_counter()
    window = MainWindow()
    app_log.log(f"Main window built in {time.perf_counter() - t_ui:.1f}s")

    if dashboard is not None:
        outcome = dashboard.run(check_future)
        if outcome.restarting:              # an update was applied: a fresh copy of the app is starting
            app_log.log("Update applied - restarting the app")
            sys.exit(0)

    window.show()
    app_log.log(f"Ready - window shown {time.perf_counter() - t_start:.1f}s after start")
    if dashboard is not None:
        dashboard.fade_out()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
