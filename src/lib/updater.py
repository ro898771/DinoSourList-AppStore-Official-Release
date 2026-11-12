"""
GitHub updater - the in-app version of runtime.exe (port of the WPF Compiler's UpdateService).

Compares the latest commit SHA of the repository branch with the SHA recorded in .version; on request downloads that
commit's zip and lays it over the app folder. Same contract as runtime.exe: .version tracks what is installed, launcher
scripts (*.cmd) and the virtual environment are never replaced, and env-Init.cmd re-syncs dependencies when they change.

On top of that, the user's own data is never touched by an update: App_Store, Software_Downloaded, Log_data, and everything
in config-record except version.json (favourites, record.json, api.json, refresh setting, update.json, news-edited.html).

Where updates come from (first non-empty wins):
  1. environment variable DINOSAURLIST_REPO ("owner/name")
  2. config-record/update.json   {"repo": "owner/name", "branch": "main", "token": ""}
  3. FALLBACK_REPO below
A PUBLIC repository needs no token. A PRIVATE one answers "404 Not Found" to anonymous callers and needs a read-only
personal access token (fine-grained, Contents: read) in update.json, or in the environment variable
DINOSAURLIST_GITHUB_TOKEN. Set DINOSAURLIST_NO_UPDATE=1 to skip the start-up check entirely.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

import requests

APP_ROOT = Path(__file__).resolve().parent.parent.parent
FALLBACK_REPO = "ro898771/DinoSourList-AppStore-Official-Release"
DEFAULT_BRANCH = "main"
VERSION_FILE = ".version"
INSTALL_CMD = "env-Init.cmd"

# Never replaced by an update (matched against the first path component, case-insensitive).
_PROTECTED_TOP = {".venv", "__pycache__", ".git", "software_downloaded", "app_store", "log_data", "temp_download"}
_PROTECTED_FILES = {VERSION_FILE.lower(), "runtime.exe"}
_DEPENDENCY_FILES = ("pyproject.toml", "uv.lock")

UNREACHABLE, FIRST_INSTALL, UP_TO_DATE, AVAILABLE = "unreachable", "first_install", "up_to_date", "available"

ProgressFn = Callable[[str, Optional[float]], None]   # (message, fraction 0..1 or None for "busy")


@dataclass
class UpdateCheck:
    state: str
    local: Optional[str] = None
    remote: Optional[str] = None
    error: str = ""


@dataclass
class Commit:
    sha: str
    message: str
    date: str

    @property
    def title(self) -> str:
        return self.message.split("\n")[0].strip()


@dataclass
class ApplyResult:
    copied: int
    skipped: List[str]
    dependencies_changed: bool


def shorten(sha: Optional[str]) -> str:
    return sha[:7] if sha and len(sha) >= 7 else (sha or "none")


# ── configuration ────────────────────────────────────────────────────────────────

def _read_config() -> dict:
    try:
        with open(APP_ROOT / "config-record" / "update.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def repo() -> str:
    return (os.environ.get("DINOSAURLIST_REPO") or str(_read_config().get("repo", "")).strip() or FALLBACK_REPO).strip()


def branch() -> str:
    return str(_read_config().get("branch", "")).strip() or DEFAULT_BRANCH


def _token() -> str:
    return (os.environ.get("DINOSAURLIST_GITHUB_TOKEN") or str(_read_config().get("token", ""))).strip()


def disabled() -> bool:
    return os.environ.get("DINOSAURLIST_NO_UPDATE") == "1"


def _headers() -> dict:
    h = {"User-Agent": "DinosaurList-AppStore-Updater/1.0", "Accept": "application/vnd.github+json"}
    if _token():
        h["Authorization"] = f"Bearer {_token()}"
    return h


def _explain(status: int) -> str:
    if status == 404:
        return (f"GitHub says Not Found for {repo()}. " +
                ("The repository name/branch is wrong, or the token has no access to it." if _token() else
                 "The repository is private (or does not exist). Put a read-only token in config-record/update.json."))
    if status == 401:
        return "GitHub rejected the token (401). It is wrong or expired - create a new read-only token."
    if status == 403:
        return "GitHub refused the request (403): rate limit reached or the token lacks permission."
    return f"GitHub answered HTTP {status}."


# ── GitHub API ───────────────────────────────────────────────────────────────────

_last_error = ""


def last_error() -> str:
    return _last_error


def _get_json(url: str, timeout: float):
    """GET + parse JSON. A few quick retries absorb the company-DNS hiccup ('No such host is known' now, fine a second
    later); a real answer (404/401/403) is never retried. Returns None on failure (reason in last_error())."""
    global _last_error
    for attempt, delay in enumerate((0.3, 0.7, 1.2, None)):
        try:
            resp = requests.get(url, headers=_headers(), timeout=timeout)
            if not resp.ok:
                _last_error = _explain(resp.status_code)
                return None
            _last_error = ""
            return resp.json()
        except requests.RequestException as exc:
            if delay is None:
                text = str(exc)
                if "getaddrinfo" in text or "Name or service" in text or "NameResolution" in text:
                    _last_error = ("Cannot reach GitHub: this PC could not look up api.github.com (DNS). "
                                   "Check the network / VPN, then press Retry.")
                else:
                    _last_error = f"Cannot reach GitHub: {exc.__class__.__name__}"
                return None
            time.sleep(delay)
    return None


def read_local_sha() -> Optional[str]:
    try:
        text = (APP_ROOT / VERSION_FILE).read_text(encoding="utf-8").strip()
        return text or None
    except OSError:
        return None


def save_local_sha(sha: str) -> None:
    (APP_ROOT / VERSION_FILE).write_text(sha, encoding="utf-8")


def fetch_latest_sha(timeout: float = 5) -> Optional[str]:
    data = _get_json(f"https://api.github.com/repos/{repo()}/commits/{branch()}", timeout)
    return data.get("sha") if isinstance(data, dict) else None


def fetch_recent_commits(count: int = 3, timeout: float = 3) -> Optional[List[Commit]]:
    data = _get_json(f"https://api.github.com/repos/{repo()}/commits?sha={branch()}&per_page={count}", timeout)
    if not isinstance(data, list):
        return None
    return [Commit(e.get("sha", ""), e.get("commit", {}).get("message", ""),
                   e.get("commit", {}).get("author", {}).get("date", "")) for e in data]


def check(timeout: float = 5) -> UpdateCheck:
    """The start-up check: never waits much longer than *timeout*; a failure is just another state (UNREACHABLE)."""
    local = read_local_sha()
    remote = fetch_latest_sha(timeout)
    if remote is None:
        return UpdateCheck(UNREACHABLE, local, None, _last_error)
    if not local:
        return UpdateCheck(FIRST_INSTALL, local, remote)
    return UpdateCheck(UP_TO_DATE if local == remote else AVAILABLE, local, remote)


# ── applying an update ───────────────────────────────────────────────────────────

def _is_protected(rel: Path) -> bool:
    parts = [p.lower() for p in rel.parts]
    if not parts or ".." in parts:
        return True
    if parts[0] in _PROTECTED_TOP or "__pycache__" in parts:
        return True
    if len(parts) == 1:
        if parts[0] in _PROTECTED_FILES or parts[0].endswith(".cmd"):    # launcher scripts are never replaced
            return True
    if parts[0] == "config-record":                                       # the user's own settings and data
        return not (len(parts) == 2 and parts[1] == "version.json")
    return False


def download_zip(sha: str, dest: Path, progress: Optional[ProgressFn] = None) -> None:
    """Streams the repository zip for *sha* (any commit works). The API zipball endpoint works for public AND private
    repositories (github.com/archive does not accept a token)."""
    url = f"https://api.github.com/repos/{repo()}/zipball/{sha}"
    with requests.get(url, headers=_headers(), stream=True, timeout=30) as resp:
        if not resp.ok:
            raise RuntimeError(_explain(resp.status_code))
        total = int(resp.headers.get("Content-Length") or 0)
        got = 0
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(1 << 20):
                f.write(chunk)
                got += len(chunk)
                if progress:
                    if total:
                        progress(f"Downloading update... {got * 100 // total}%", got / total)
                    else:
                        progress(f"Downloading update... {got // 1024} KB", None)


def apply_zip(zip_path: Path, root: Path = APP_ROOT, progress: Optional[ProgressFn] = None) -> ApplyResult:
    """Extracts *zip_path* and lays its files over *root*, skipping everything protected. A file that is locked
    (in use) is skipped and reported, never fatal."""
    tmp = Path(tempfile.mkdtemp(prefix="dinosaurlist-update-"))
    try:
        if progress:
            progress("Extracting update...", None)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp)
        roots = [p for p in tmp.iterdir() if p.is_dir()]
        if not roots:
            raise RuntimeError("Downloaded zip was empty.")
        src_root = roots[0]

        before = {n: _read_bytes(root / n) for n in _DEPENDENCY_FILES}
        files = [p for p in src_root.rglob("*") if p.is_file()]
        copied, skipped = 0, []
        for i, src in enumerate(files, 1):
            rel = src.relative_to(src_root)
            if _is_protected(rel):
                continue
            dest = root / rel
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                copied += 1
            except OSError:
                skipped.append(str(rel))
            if progress and i % 25 == 0:
                progress("Applying files...", i / len(files))
        after = {n: _read_bytes(root / n) for n in _DEPENDENCY_FILES}
        return ApplyResult(copied, skipped, before != after)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _read_bytes(p: Path) -> Optional[bytes]:
    try:
        return p.read_bytes()
    except OSError:
        return None


def download_and_apply(sha: str, progress: Optional[ProgressFn] = None) -> ApplyResult:
    zip_path = Path(tempfile.gettempdir()) / f"dinosaurlist-update-{shorten(sha)}.zip"
    try:
        download_zip(sha, zip_path, progress)
        result = apply_zip(zip_path, APP_ROOT, progress)
        save_local_sha(sha)
        return result
    finally:
        try:
            zip_path.unlink()
        except OSError:
            pass


def venv_missing() -> bool:
    return not (APP_ROOT / ".venv" / "Scripts" / "python.exe").exists()


# ── restart ──────────────────────────────────────────────────────────────────────

def restart(run_install: bool) -> None:
    """Starts the app again in a new console after this process has exited, then the caller should quit.
    With *run_install* env-Init.cmd runs first (new/changed dependencies); it cannot run inside this process because the
    packages in use (Qt DLLs) are locked while we are alive."""
    main_script = Path(sys.argv[0]).resolve()
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else Path(sys.executable)        # no terminal window for the app itself
    launch = f'start "" "{exe}" "{main_script}"'
    steps = ["ping -n 3 127.0.0.1 >nul"]            # a couple of seconds: lets this process exit and release its files
    if run_install and (APP_ROOT / INSTALL_CMD).exists():
        steps.append(f'echo Updating dependencies... & call "{APP_ROOT / INSTALL_CMD}"')
    steps.append(launch)
    # A console window only while dependencies are being installed (so the user can see it work); otherwise none at all.
    flags = (getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if run_install and (APP_ROOT / INSTALL_CMD).exists()
             else getattr(subprocess, "CREATE_NO_WINDOW", 0))
    # The command line is passed as ONE string, wrapped in quotes the way cmd /c expects. As a list, Python escapes the
    # inner quotes as \" - which cmd does not understand ("'\"C:\\...\\env-Init.cmd\"' is not recognized").
    subprocess.Popen(f'cmd.exe /c "{" & ".join(steps)}"', cwd=str(APP_ROOT), creationflags=flags)
