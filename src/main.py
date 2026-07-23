"""Software Launcher Dashboard - Entry Point"""

import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import QApplication
from lib.main_controller import MainWindow

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

    try:
        log_dir = _REPO_ROOT / "Log_data"
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / f"launch_{datetime.now():%Y-%m-%d}.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] user={username} ip={ip} health={status}\n")
    except OSError:
        pass


def main():
    _clean_log_data()
    _log_launch_health()

    app = QApplication(sys.argv)
    app.setApplicationName("Software Launcher")
    app.setOrganizationName("DinasourList")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
