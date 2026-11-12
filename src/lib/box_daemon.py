"""
BoxFast daemon client - talks to one long-lived BoxFast.exe over JSON lines.

BoxAutomate.exe is started (and logs in to Box) again for every single call. BoxFast.exe stays running with one
authenticated Box client, serves many requests at the same time and downloads big files in parallel segments,
the same approach the WPF Compiler takes in-process. Everything here degrades gracefully: if BoxFast.exe is missing,
cannot start, or a call fails, callers (see BoxLinkAPI) fall back to BoxAutomate.exe.
"""

import atexit
import itertools
import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

_EXE = Path(__file__).parent.parent.parent / "BoxLink-API" / "BoxFast.exe"
_START_TIMEOUT = 20      # seconds to wait for the helper's "ready" line
_MAX_START_FAILURES = 3  # after this many failed starts the daemon is left disabled for the session


class BoxDaemon:
    """Process-wide singleton. Thread safe: any number of threads may call request() at once."""

    _instance: Optional["BoxDaemon"] = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls) -> "BoxDaemon":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self):
        self._proc: Optional[subprocess.Popen] = None
        self._start_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._pending: Dict[int, Tuple[threading.Event, list]] = {}
        self._progress_cbs: Dict[int, Any] = {}      # request id -> callback(done_bytes, total_bytes)
        self._pending_lock = threading.Lock()
        self._ids = itertools.count(1)
        self._start_failures = 0
        atexit.register(self.shutdown)

    # ── lifecycle ────────────────────────────────────────────────────────────────

    @staticmethod
    def exe_exists() -> bool:
        return _EXE.is_file()

    def available(self) -> bool:
        return self.exe_exists() and self._start_failures < _MAX_START_FAILURES

    def ready(self) -> bool:
        """True only when the helper is running (starting it now if needed, up to the start timeout)."""
        return self._ensure_started()

    def _alive(self) -> bool:
        p = self._proc
        return p is not None and p.poll() is None

    def _ensure_started(self) -> bool:
        if self._alive():
            return True
        if not self.available():
            return False
        with self._start_lock:
            if self._alive():
                return True
            try:
                flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                proc = subprocess.Popen(
                    [str(_EXE)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                    creationflags=flags,
                )
                ready: list = []
                ready_evt = threading.Event()

                def first_line():
                    try:
                        ready.append(proc.stdout.readline())
                    except Exception:
                        pass
                    ready_evt.set()

                threading.Thread(target=first_line, daemon=True).start()
                if not ready_evt.wait(_START_TIMEOUT) or not ready or '"ready"' not in ready[0]:
                    proc.kill()
                    self._start_failures += 1
                    return False
                self._proc = proc
                self._start_failures = 0
                threading.Thread(target=self._reader, args=(proc,), daemon=True).start()
                return True
            except Exception:
                self._start_failures += 1
                return False

    def _reader(self, proc: subprocess.Popen):
        """Routes each response line to the thread waiting for its id; on exit fails everything still pending."""
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if "progress" in msg:                      # live byte progress of a running download
                    cb = self._progress_cbs.get(msg.get("id"))
                    if cb:
                        try:
                            cb(int(msg["progress"].get("done", 0)), int(msg["progress"].get("total", 0)))
                        except Exception:
                            pass
                    continue
                with self._pending_lock:
                    waiter = self._pending.pop(msg.get("id"), None)
                if waiter:
                    waiter[1].append(msg)
                    waiter[0].set()
        except Exception:
            pass
        finally:
            with self._pending_lock:
                orphans = list(self._pending.values())
                self._pending.clear()
            for evt, box in orphans:
                box.append({"ok": False, "error": "BoxFast helper stopped unexpectedly"})
                evt.set()

    def shutdown(self):
        p = self._proc
        self._proc = None
        if p is None:
            return
        try:
            p.stdin.close()      # EOF = the helper exits by itself
            p.wait(timeout=2)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass

    # ── requests ─────────────────────────────────────────────────────────────────

    def request(self, cmd: str, timeout: Optional[float] = 120, on_progress=None, **fields: Any) -> Tuple[bool, Any, str]:
        """Sends one request and waits for its response. Returns (ok, data, error).
        With *on_progress* the helper also reports bytes as they arrive: on_progress(done, total) (reader thread)."""
        if not self._ensure_started():
            return False, None, "BoxFast helper unavailable"
        rid = next(self._ids)
        evt = threading.Event()
        box: list = []
        with self._pending_lock:
            self._pending[rid] = (evt, box)
        if on_progress:
            self._progress_cbs[rid] = on_progress
            fields["progress"] = True
        payload = json.dumps({"id": rid, "cmd": cmd, **fields}, ensure_ascii=False) + "\n"
        try:
            with self._write_lock:
                self._proc.stdin.write(payload)
                self._proc.stdin.flush()
        except Exception as exc:
            with self._pending_lock:
                self._pending.pop(rid, None)
            self._progress_cbs.pop(rid, None)
            return False, None, f"BoxFast helper write failed: {exc}"
        finished = evt.wait(timeout)
        self._progress_cbs.pop(rid, None)
        if not finished:
            with self._pending_lock:
                self._pending.pop(rid, None)
            return False, None, f"BoxFast request timed out ({timeout}s)"
        msg = box[0]
        if msg.get("ok"):
            return True, msg.get("data"), ""
        return False, None, str(msg.get("error") or "BoxFast request failed")

    def warm_up(self):
        """Start the helper and log in to Box in the background, so the first real call does not pay for it."""
        def _go():
            if self._ensure_started():
                self.request("warmup", timeout=60)
        threading.Thread(target=_go, daemon=True, name="BoxFastWarmup").start()
