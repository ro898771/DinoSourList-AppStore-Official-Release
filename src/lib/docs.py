"""
README / Details-guide handling for App_Store folders (port of the WPF Compiler's Docs).

Refresh no longer downloads READMEs and guides (some are tens of MB and rarely opened); they are fetched the first time the
user clicks them. Each doc is therefore in one of three states:
  NONE   - the app does not offer it
  REMOTE - listed on Box but not on disk yet (or the copy on disk is out of date)
  LOCAL  - on disk and current

"Out of date" is tracked in App_Store/<app>/.docs.json: the Box SHA-1 and the app version recorded when the doc was last
downloaded. A different SHA-1 on Box, or a newer app version, turns a downloaded doc back to REMOTE.
"""

import itertools
import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from .folder_parser import compare_versions

NONE, REMOTE, LOCAL = "none", "remote", "local"

_ZIP_VERSION = re.compile(r"^v?(\d+(?:\.\d+)*)\.zip$", re.IGNORECASE)


@dataclass(frozen=True)
class DocInfo:
    state: str
    file_name: Optional[str] = None
    file_id: Optional[str] = None
    local_path: Optional[Path] = None


MISSING = DocInfo(NONE)


# ── Flow.txt ─────────────────────────────────────────────────────────────────────

def _read_flow(store_folder: Path) -> Dict[str, object]:
    """Pull [Guide] file= and [ReadMe] Flag=/file= out of Flow.txt (empty values when absent)."""
    out: Dict[str, object] = {"guide": None, "readme_flag": False, "readme_file": None}
    flow = store_folder / "Flow.txt"
    if not flow.exists():
        return out
    section = None
    try:
        with open(flow, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if line.startswith("[") and line.endswith("]"):
                    section = line[1:-1].lower()
                    continue
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip().lower(), value.strip()
                if section == "guide" and key == "file" and out["guide"] is None:
                    if value and value.lower() not in ("none", "false"):
                        out["guide"] = value
                elif section == "readme":
                    if key == "flag":
                        out["readme_flag"] = value.lower() == "true"
                    elif key == "file":
                        out["readme_file"] = value
    except Exception as exc:
        print(f"[DOCS] Error reading Flow.txt for {store_folder.name}: {exc}")
    return out


# ── what Box lists / what we downloaded ──────────────────────────────────────────

def _json_path(store_folder: Path) -> Path:
    return store_folder / f"{store_folder.name}.json"


def _remote_files(store_folder: Path) -> Dict[str, Dict[str, str]]:
    """{lower-case file name -> {name, id, sha1}} from App_Store/<app>/<app>.json (written by refresh)."""
    try:
        with open(_json_path(store_folder), "r", encoding="utf-8") as f:
            files = json.load(f).get("files", [])
    except Exception:
        return {}
    return {
        f["name"].lower(): {"name": f["name"], "id": f["id"], "sha1": f.get("sha1") or ""}
        for f in files if f.get("name") and f.get("id")
    }


def _latest_version(store_folder: Path) -> str:
    best = ""
    for name in _remote_files(store_folder):
        m = _ZIP_VERSION.match(name)
        if m and (not best or compare_versions(m.group(1), best) > 0):
            best = m.group(1)
    return best


def _manifest_path(store_folder: Path) -> Path:
    return store_folder / ".docs.json"


def _load_manifest(store_folder: Path) -> Dict[str, Dict[str, str]]:
    try:
        with open(_manifest_path(store_folder), "r", encoding="utf-8") as f:
            return json.load(f).get("files", {})
    except Exception:
        return {}


def mark_fresh(store_folder: Path, file_name: str) -> None:
    """Remember that *file_name* was just downloaded: its Box hash and the app version it belongs to."""
    try:
        files = _load_manifest(store_folder)
        remote = _remote_files(store_folder).get(file_name.lower())
        files[file_name.lower()] = {"sha1": (remote or {}).get("sha1", ""), "version": _latest_version(store_folder)}
        with open(_manifest_path(store_folder), "w", encoding="utf-8") as f:
            json.dump({"files": files}, f, indent=2)
    except Exception:
        pass  # freshness tracking is best effort


def _is_stale(store_folder: Path, remote: Optional[Dict[str, str]], file_name: str) -> bool:
    """Box copy changed since download, or the app has a newer version. No record (hand-copied file) counts as fresh."""
    entry = _load_manifest(store_folder).get(file_name.lower())
    if not entry:
        return False
    old_sha, now_sha = entry.get("sha1", ""), (remote or {}).get("sha1", "")
    if now_sha and old_sha and now_sha.lower() != old_sha.lower():
        return True
    old_ver, now_ver = entry.get("version", ""), _latest_version(store_folder)
    return bool(old_ver and now_ver and compare_versions(now_ver, old_ver) > 0)


def _resolve(store_folder: Path, candidates: Iterable[str]) -> DocInfo:
    remote = _remote_files(store_folder)
    first_remote: Optional[DocInfo] = None
    for name in candidates:
        r = remote.get(name.lower())
        local = store_folder / name
        if local.exists():
            if r and _is_stale(store_folder, r, name):
                return DocInfo(REMOTE, r["name"], r["id"], local)   # downloaded copy is out of date
            return DocInfo(LOCAL, name, None, local)
        if first_remote is None and r:
            first_remote = DocInfo(REMOTE, r["name"], r["id"], None)
    return first_remote or MISSING


# ── public API ───────────────────────────────────────────────────────────────────

def guide(store_folder: Path) -> DocInfo:
    name = _read_flow(store_folder)["guide"]
    return _resolve(store_folder, [name]) if name else MISSING


def readme(store_folder: Path) -> DocInfo:
    flow = _read_flow(store_folder)
    names: List[str] = []
    if flow["readme_flag"] and flow["readme_file"]:
        names.append(str(flow["readme_file"]))
    names.append("README.md")
    return _resolve(store_folder, names)


def download(info: DocInfo, store_folder: Path) -> Path:
    """Make a REMOTE doc local (one Box call) and return its path. LOCAL docs are returned as they are."""
    if info.state == LOCAL:
        return info.local_path
    if info.state != REMOTE:
        raise FileNotFoundError("No such file is listed on Box for this app.")
    from .boxlink_api import BoxLinkAPI   # local import: boxlink_api -> box_daemon, keep this module light
    ok, _, error = BoxLinkAPI().download_small_dict(info.file_id, str(store_folder), info.file_name)
    if not ok:
        raise RuntimeError(error or f"Could not download {info.file_name}")
    mark_fresh(store_folder, info.file_name)
    return store_folder / info.file_name


def fetch_missing_readmes(app_store_dir: Path, progress: Optional[Callable[[str], None]] = None,
                          fetched: Optional[list] = None) -> int:
    """
    Download every README that is listed on Box but missing (or out of date) locally, all at once, one Box call each.
    The AI assistant indexes App_Store READMEs, so it calls this before building its index. Returns how many were fetched.
    """
    if not app_store_dir.exists():
        return 0
    todo = []
    for d in app_store_dir.iterdir():
        if d.is_dir():
            info = readme(d)
            if info.state == REMOTE:
                todo.append((d, info))
    if not todo:
        return 0
    from .boxlink_api import BoxLinkAPI
    api = BoxLinkAPI()
    counter = itertools.count(1)

    def one(item) -> bool:
        d, info = item
        ok = False
        try:
            ok, _, _ = api.download_small_dict(info.file_id, str(d), info.file_name)
            if ok:
                mark_fresh(d, info.file_name)
                if fetched is not None:
                    fetched.append(d.name)           # lets the caller recolour the README buttons of exactly these apps
        except Exception:
            pass   # skip: indexed from what is local
        if progress:
            progress(f"Downloading README files: {next(counter)}/{len(todo)}")
        return bool(ok)

    with ThreadPoolExecutor(max_workers=min(len(todo), 32)) as ex:
        return sum(ex.map(one, todo))
