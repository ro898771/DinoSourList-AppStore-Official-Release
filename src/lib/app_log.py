"""
The app's log.

run.cmd starts the app with pythonw.exe, so no terminal window opens. Everything the app prints (Python print() AND
Qt/C-level messages) goes to Log_data/app_output.log, a fresh file for every launch. Settings > Log shows it live.

Started with python.exe from a terminal, the output already goes to that terminal and no file is written.
"""

import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_LOG = _ROOT / "Log_data" / "app_output.log"

_log_file = None


def managed() -> bool:
    """True when the app runs without a console of its own (pythonw.exe), so its output lives in the log file."""
    return sys.platform == "win32" and os.path.basename(sys.executable).lower() == "pythonw.exe"


def log_path() -> Path:
    return _LOG


def install_log() -> None:
    """Call first thing at start-up: output goes to the log file instead of being thrown away (pythonw has no stdout)."""
    global _log_file
    if not managed() or _log_file is not None:
        return
    try:
        _LOG.parent.mkdir(exist_ok=True)
        try:    # a fresh log per launch, so Settings > Log starts with this session's init lines
            _log_file = open(_LOG, "w", encoding="utf-8", errors="replace", buffering=1)
        except OSError:    # still held by a previous copy of the app: append instead
            _log_file = open(_LOG, "a", encoding="utf-8", errors="replace", buffering=1)
        sys.stdout = sys.stderr = _log_file
        os.dup2(_log_file.fileno(), 1)     # Qt and other C-level code write through fd 1/2
        os.dup2(_log_file.fileno(), 2)
    except OSError:
        pass


def log(message: str, tag: str = "INIT") -> None:
    """One timestamped line, e.g. [10:04:12.381] [INIT] GitHub update check: up_to_date."""
    try:
        print(f"[{time.strftime('%H:%M:%S')}.{int(time.time() * 1000) % 1000:03d}] [{tag}] {message}", flush=True)
    except Exception:
        pass
