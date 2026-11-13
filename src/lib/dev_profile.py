"""
Dev Profile - a GitHub-style page per developer: who they are, a pinned "tweet" they can set, the applications they own and a
year-long activity calendar of their updates.

DESIGN PHASE. What is real and what is a stand-in:
  * developers and their applications ... REAL, read from App_Store (folder "Name-Author", versions from the metadata JSON)
  * pinned message and photo ............. stored in Box, in ONE hidden folder in the Box root (".devprofile-data"): per developer a
                                           "<name>.json" (the message) and a "<name>.png" (the photo). BoxLinkAPI removes every
                                           name starting ".devprofile-" from the listings a refresh sees, so the Store, the Local
                                           list and Refresh never know the folder exists. This PC keeps a copy (config-record/
                                           dev_profile.json for messages, a cache folder for photos): shown at once, used when Box
                                           cannot be reached, and where an edit waits until it could be sent.
  * activity ............................. Box upload/edit dates of every file of every application (box_dates.py), plus anything
                                           written by hand in config-record/dev_activity.json; clearly labelled SAMPLE data only
                                           for a developer with neither.
"""

import json
import math
import os
import random
from concurrent.futures import ThreadPoolExecutor
import re
import threading
import time
import zlib
from urllib.parse import urlparse
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QBuffer, QIODevice, QPoint, QPointF, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QBrush, QColor, QDesktopServices, QFont, QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
                           QTextCursor)
from PySide6.QtWidgets import (QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
                               QScrollArea, QSizePolicy, QToolTip, QVBoxLayout, QWidget)

from .dashboard_widgets import MODERN_SCROLLBAR_STYLE
from .ui_icons import DEV_PROFILE_ICON

APP_ROOT = Path(__file__).resolve().parent.parent.parent
TWEET_LIMIT = 280
DATA_FOLDER = ".devprofile-data"        # the hidden Box folder (name must start with boxlink_api.HIDDEN_PREFIX)
PHOTO_SIZE = 256                        # photos are cropped to a square and stored at this size
MAX_PHOTO_FILE = 25 * 1024 * 1024       # larger pictures are refused before they are even opened
FIRST_DEVELOPER = "NPI DEV"             # always the first entry of the developer drop-down (and so the page's default)
MAX_LINKS = 20                          # Jira links per developer
_TICKET_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d{1,7}\b")   # a Jira issue key such as ABC-1234


def normalize_link(url: str, title: str = "") -> Optional[dict]:
    """A link typed by a user -> {"title", "url", "key"} or None when it is not a usable web link.
    Only http/https links are accepted (nothing that could start a program), and a missing scheme is added."""
    url = (url or "").strip()
    if not url or any(c.isspace() for c in url):
        return None
    if "://" not in url:
        # without https:// it must still look like an address (a dot, a slash or a port): "jira/browse/ABC-1" yes, "hello" no
        if not re.match(r"^[A-Za-z0-9.-]+(:\d+)?([/?#].*)?$", url) or not re.search(r"[./:]", url):
            return None
        url = "https://" + url
    try:
        parts = urlparse(url)
        parts.port                                                         # raises for a broken port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:        # an intranet host such as http://jira/ is fine
        return None
    match = _TICKET_RE.search(parts.path) or _TICKET_RE.search(parts.query) or _TICKET_RE.search(title or "")
    return {"title": (title or "").strip()[:80], "url": url[:500], "key": match.group(0) if match else ""}
_VERSION_RE = re.compile(r"^v(\d+(?:\.\d+){3})\.zip$", re.IGNORECASE)

INDIGO = "#4338ca"
# GitHub's light-theme contribution scale: none → most
LEVEL_COLOURS = ["#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"]


# ═════════════════════════════════════════════ data ═════════════════════════════════════════════

def prepare_photo(path) -> Optional[bytes]:
    """A picture file -> PNG bytes of its centred square, PHOTO_SIZE x PHOTO_SIZE. None when it cannot be read."""
    try:
        if Path(path).stat().st_size > MAX_PHOTO_FILE:
            return None
    except OSError:
        return None
    img = QImage(str(path))
    if img.isNull():
        return None
    side = min(img.width(), img.height())
    img = img.copy((img.width() - side) // 2, (img.height() - side) // 2, side, side)
    img = img.scaled(PHOTO_SIZE, PHOTO_SIZE, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    return bytes(buf.data()) if img.save(buf, "PNG") else None


@dataclass
class AppInfo:
    folder: str                    # App_Store folder name, e.g. "GUQC-RoeyYee"
    name: str
    author: str
    version: str = ""              # newest vX.X.X.X.zip listed in the metadata
    versions: List[str] = field(default_factory=list)
    category: str = ""
    icon: Optional[Path] = None


@dataclass
class ActivityEvent:
    day: date
    app: str
    version: str
    kind: str = "update"
    who: str = ""                  # uploader, when Box tells us
    detail: str = ""               # "Added" / "Edited" for files other than version zips
    file: str = ""                 # the file name for kind "file"


def _flow_values(flow: Path, section: str) -> Dict[str, str]:
    values, current = {}, None
    try:
        for line in flow.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                current = line[1:-1].lower()
            elif current == section and "=" in line:
                k, _, v = line.partition("=")
                values[k.strip().lower()] = v.strip()
    except OSError:
        pass
    return values


def _vkey(v: str):
    return [int(x) for x in v.split(".")]


class ProfileStore:
    """Everything the page reads and writes. Swap the tweet/activity parts when the real storage is chosen."""

    def __init__(self, root: Path = APP_ROOT):
        self.root = Path(root)
        self.app_store = self.root / "App_Store"
        self.tweet_file = self.root / "config-record" / "dev_profile.json"
        self.activity_file = self.root / "config-record" / "dev_activity.json"
        self._apps: Optional[List[AppInfo]] = None
        self._scan_lock = threading.Lock()
        self._rescan_thread: Optional[threading.Thread] = None
        self._rescan_wanted = False
        self._memo: dict = {}                         # (author, ...) -> what activity() worked out
        self._folder_id: Optional[str] = None         # the hidden Box folder, once found (saves a root listing per call)
        self._ids: Dict[str, str] = {}                # file name (lower case) -> Box file id, for direct uploads
        self._cache_lock = threading.RLock()          # the local message/links caches are read-modify-written from threads
        self.version = 0                              # +1 whenever the application list was re-read
        # Photos are a cache of what is in Box, kept outside the repository folder (nothing for git to see).
        base = os.environ.get("LOCALAPPDATA")
        self.photo_dir = (Path(base) / "DinoSaurList" / "dev_photos") if base else (self.root / "config-record" / "dev_photos")

    # ── applications (real) ──────────────────────────────────────────────────────
    def all_apps(self) -> List[AppInfo]:
        """Every application. Read from disk once (about 160 small files); after that always answered from memory -
        a change (invalidate) is picked up by a background thread while the old list keeps being served."""
        if self._apps is None:
            with self._scan_lock:
                if self._apps is None:
                    self._apps = self._scan_apps()
        return self._apps

    def invalidate(self) -> None:
        """App_Store changed (a refresh). Re-read it in the background: nothing on the UI thread waits for the disk."""
        self._memo.clear()
        if self._apps is None:
            return                                    # never read yet: the first all_apps() will see the new state
        self._rescan_wanted = True
        if self._rescan_thread is None or not self._rescan_thread.is_alive():
            self._rescan_thread = threading.Thread(target=self._rescan, daemon=True, name="DevProfileRescan")
            self._rescan_thread.start()

    def _rescan(self) -> None:
        while self._rescan_wanted:
            self._rescan_wanted = False
            try:
                fresh = self._scan_apps()
            except Exception:
                continue
            self._apps = fresh
            self._memo.clear()
            self.version += 1                         # a page built from the old list notices this when it is shown again

    def _scan_apps(self) -> List[AppInfo]:
        apps = []
        if not self.app_store.is_dir():
            return apps
        for folder in sorted(p for p in self.app_store.iterdir() if p.is_dir()):
            if "-" in folder.name:
                name, author = folder.name.rsplit("-", 1)
            else:
                name, author = folder.name, "Unknown"
            versions = []
            try:
                meta = json.loads((folder / f"{folder.name}.json").read_text(encoding="utf-8"))
                for f in meta.get("files", []):
                    m = _VERSION_RE.match(f.get("name", ""))
                    if m:
                        versions.append(m.group(1))
            except (OSError, ValueError):
                pass
            versions.sort(key=_vkey, reverse=True)
            flow = folder / "Flow.txt"
            icon_name = _flow_values(flow, "icon").get("name") or "icon.ico"
            icon = folder / icon_name
            apps.append(AppInfo(folder.name, name, author.strip() or "Unknown", versions[0] if versions else "", versions,
                                _flow_values(flow, "category").get("cluster", ""), icon if icon.exists() else None))
        return apps

    def developers(self) -> List[str]:
        """Authors: FIRST_DEVELOPER (the team's own entry) on top, then everyone else A-Z."""
        authors = {a.author for a in self.all_apps()}
        return sorted(authors, key=lambda name: (name.lower() != FIRST_DEVELOPER.lower(), name.lower()))

    def apps_of(self, author: str) -> List[AppInfo]:
        return sorted((a for a in self.all_apps() if a.author == author), key=lambda a: a.name.lower())

    # ── pinned message + photo ───────────────────────────────────────────────────────────────────────────────
    # Box is the shared copy. Everything lives in ONE hidden folder of the Box root, DATA_FOLDER, one pair of files per
    # developer ("<name>.json", "<name>.png"): two developers saving at the same time never touch the same file.

    @staticmethod
    def safe_name(author: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", author)

    def box_file_name(self, author: str) -> str:
        return f"{self.safe_name(author)}.json"

    def photo_file_name(self, author: str) -> str:
        return f"{self.safe_name(author)}.png"

    def links_file_name(self, author: str) -> str:
        return f"{self.safe_name(author)}.links.json"

    @property
    def links_file(self) -> Path:
        """This PC's copy of everybody's Jira links (next to the photo cache: nothing for git to see)."""
        return self.photo_dir.parent / "dev_links.json"

    def _legacy_name(self, author: str) -> str:
        """Where the first version of this feature kept a message: a loose file in the Box root."""
        from .boxlink_api import HIDDEN_PREFIX
        return f"{HIDDEN_PREFIX}{self.safe_name(author)}.json"

    def _box_root(self) -> str:
        try:
            return str(json.loads((self.root / "config-record" / "record.json").read_text(encoding="utf-8"))["folder_id"])
        except (OSError, ValueError, KeyError):
            return "368728134757"

    def _api(self):
        from .boxlink_api import BoxLinkAPI
        return BoxLinkAPI()

    def _data_folder(self, api, create: bool = False):
        """(folder id or None, {legacy file name (lower case): file id}). Raises when Box cannot be reached.
        Once the folder is known it is remembered: no listing of the Box root is needed again in this session."""
        if self._folder_id:
            return self._folder_id, {}
        ok, data, err = api.list_folder_dict(self._box_root(), include_hidden=True)
        if not ok or not data:
            raise OSError(err or "Box could not be listed")
        items = data.get("items", [])
        folder = next((i for i in items if i.get("type") == "folder" and str(i.get("name", "")).lower() == DATA_FOLDER), None)
        legacy = {str(i["name"]).lower(): i["id"] for i in items
                  if i.get("type") == "file" and str(i.get("name", "")).lower().startswith(".devprofile-")}
        if folder is None and create:
            ok, made, err = api.ensure_folder(self._box_root(), DATA_FOLDER)
            if not ok or not made:
                raise OSError(err or "Box would not create the folder")
            self._folder_id = str(made["id"])
            return self._folder_id, legacy
        if folder:
            self._folder_id = str(folder["id"])
        return (str(folder["id"]) if folder else None), legacy

    def _remember(self, items=None, written=None) -> None:
        """Keep the Box file ids we have seen, so the next save goes straight to the file."""
        for i in items or []:
            if i.get("type") == "file" and i.get("id"):
                self._ids[str(i.get("name", "")).lower()] = str(i["id"])
        if isinstance(written, dict) and written.get("id") and written.get("name"):
            self._ids[str(written["name"]).lower()] = str(written["id"])

    # local copy of the message
    def _read_tweets(self) -> dict:
        try:
            data = json.loads(self.tweet_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_tweets(self, data: dict) -> None:
        self.tweet_file.parent.mkdir(exist_ok=True)
        self.tweet_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _when(entry: dict) -> Optional[datetime]:
        try:
            return datetime.fromisoformat(entry.get("updated", "")) if entry.get("updated") else None
        except ValueError:
            return None

    def tweet(self, author: str) -> Tuple[str, Optional[datetime]]:
        """This PC's copy (instant). sync_profile() brings Box's."""
        entry = self._read_tweets().get(author) or {}
        return str(entry.get("tweet", "")), self._when(entry)

    def set_tweet(self, author: str, text: str) -> None:
        """Save locally and mark it as waiting for Box (push_tweet sends it)."""
        with self._cache_lock:
            data = self._read_tweets()
            data[author] = {"tweet": text.strip()[:TWEET_LIMIT], "updated": datetime.now().isoformat(timespec="seconds"),
                            "pending": True}
            self._write_tweets(data)

    def push_tweet(self, author: str) -> Tuple[bool, str]:
        """Send this PC's waiting message to Box (create the file, or a new version of it). (ok, error)."""
        entry = self._read_tweets().get(author)
        if not entry:
            return True, ""
        try:
            api = self._api()
            folder, _legacy = self._data_folder(api, create=True)
            body = {"author": author, "tweet": entry.get("tweet", ""), "updated": entry.get("updated", "")}
            name = self.box_file_name(author)
            ok, written, err = api.write_text(folder, name, json.dumps(body, indent=2, ensure_ascii=False),
                                              file_id=self._ids.get(name.lower()))
        except Exception as exc:
            return False, str(exc)
        if ok:
            self._remember(written=written)
            with self._cache_lock:
                data = self._read_tweets()
                if data.get(author) == entry:                  # not edited again while it was being sent
                    entry.pop("pending", None)
                    data[author] = entry
                    self._write_tweets(data)
            return True, ""
        return False, err or "Box did not accept the file"

    # Jira links: a list of {"title", "url", "key"} per developer, "<name>.links.json" in the hidden folder
    def _read_links(self) -> dict:
        try:
            data = json.loads(self.links_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_links(self, data: dict) -> None:
        self.links_file.parent.mkdir(parents=True, exist_ok=True)
        self.links_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def links(self, author: str) -> List[dict]:
        return list((self._read_links().get(author) or {}).get("links", []))

    def set_links(self, author: str, links: List[dict]) -> None:
        """Save locally and mark as waiting for Box (push_links sends it)."""
        with self._cache_lock:
            data = self._read_links()
            data[author] = {"links": list(links)[:MAX_LINKS], "updated": datetime.now().isoformat(timespec="seconds"), "pending": True}
            self._write_links(data)

    def push_links(self, author: str) -> Tuple[bool, str]:
        entry = self._read_links().get(author)
        if not entry:
            return True, ""
        try:
            api = self._api()
            folder, _legacy = self._data_folder(api, create=True)
            body = {"author": author, "links": entry.get("links", []), "updated": entry.get("updated", "")}
            name = self.links_file_name(author)
            ok, written, err = api.write_text(folder, name, json.dumps(body, indent=2, ensure_ascii=False),
                                              file_id=self._ids.get(name.lower()))
        except Exception as exc:
            return False, str(exc)
        if ok:
            self._remember(written=written)
            with self._cache_lock:
                data = self._read_links()
                if data.get(author) == entry:
                    entry.pop("pending", None)
                    data[author] = entry
                    self._write_links(data)
            return True, ""
        return False, err or "Box did not accept the file"

    # local copy of the photo
    def photo_path(self, author: str) -> Optional[Path]:
        p = self.photo_dir / self.photo_file_name(author)
        return p if p.exists() else None

    def _photo_pending(self, author: str) -> Path:
        return self.photo_dir / f"{self.safe_name(author)}.pending"

    def set_photo(self, author: str, png: bytes) -> Path:
        """Keep the new photo on this PC and mark it as waiting for Box (push_photo sends it)."""
        self.photo_dir.mkdir(parents=True, exist_ok=True)
        path = self.photo_dir / self.photo_file_name(author)
        path.write_bytes(png)
        self._photo_pending(author).write_text("pending", encoding="utf-8")
        return path

    def push_photo(self, author: str) -> Tuple[bool, str]:
        path = self.photo_path(author)
        if path is None:
            return True, ""
        try:
            data = path.read_bytes()
            api = self._api()
            folder, _legacy = self._data_folder(api, create=True)
            name = self.photo_file_name(author)
            ok, written, err = api.write_bytes(folder, name, data, file_id=self._ids.get(name.lower()))
        except Exception as exc:
            return False, str(exc)
        if ok:
            self._remember(written=written)
            if path.read_bytes() == data:                      # not replaced again while it was being sent
                try:
                    self._photo_pending(author).unlink()
                except OSError:
                    pass
            return True, ""
        return False, err or "Box did not accept the file"

    # ── sync with Box ──────────────────────────────────────────────────────────────────────────────────────
    def sync_profile(self, author: str) -> dict:
        """Make this PC agree with Box for one developer. Returns
             {"state": ..., "tweet": text, "updated": iso, "photo": Path | None}
           state: "saved"   something that was waiting on this PC has just been sent
                  "synced"  Box's message/photo are now what is shown
                  "none"    nothing has been pinned in Box yet (a local-only message stays visible)
                  "offline" Box could not be reached: the local copy stays"""
        local = self._read_tweets().get(author) or {}
        out = {"state": "offline", "tweet": str(local.get("tweet", "")), "updated": str(local.get("updated", "")),
               "photo": self.photo_path(author), "links": self.links(author)}
        try:
            api = self._api()
            folder, legacy = self._data_folder(api)
        except Exception:
            return out

        sent = False
        if local.get("pending"):                                  # 1. what is waiting on this PC goes first
            if not self.push_tweet(author)[0]:
                return out
            sent = True
        if self._photo_pending(author).exists():
            if not self.push_photo(author)[0]:
                return out
            sent = True
        if (self._read_links().get(author) or {}).get("pending"):
            if not self.push_links(author)[0]:
                return out
            sent = True
        try:
            if sent:
                folder, legacy = self._data_folder(api)          # the folder may have just been created
            files = {}
            if folder:
                ok, data, _err = api.list_folder_dict(folder, include_hidden=True)
                if (not ok or not data) and self._folder_id:      # the remembered folder is gone? look from the root again
                    self._folder_id = None
                    folder, legacy = self._data_folder(api)
                    ok, data, _err = api.list_folder_dict(folder, include_hidden=True) if folder else (True, {"items": []}, "")
                if not ok or not data:
                    return out
                self._remember(data.get("items", []))
                files = {str(i.get("name", "")).lower(): i for i in data.get("items", []) if i.get("type") == "file"}

            # 2. the message (a loose file from the first version of this feature is moved into the folder)
            item = files.get(self.box_file_name(author).lower())
            old_id = legacy.get(self._legacy_name(author).lower())
            if item is None and old_id:
                ok, text, _err = api.read_text(old_id)
                old = json.loads(text) if ok else None
                if isinstance(old, dict) and str(old.get("tweet", "")).strip():
                    cache = self._read_tweets()
                    cache[author] = {"tweet": str(old["tweet"])[:TWEET_LIMIT], "updated": str(old.get("updated", "")), "pending": True}
                    self._write_tweets(cache)
                    if self.push_tweet(author)[0]:
                        api.delete_file(old_id)                  # only after the copy in the folder is safe
                        return self.sync_profile(author)          # the message is in the folder now: start again
            # the message, the Jira links and the photo are independent files: fetch them AT THE SAME TIME
            litem = files.get(self.links_file_name(author).lower())
            pitem = files.get(self.photo_file_name(author).lower())
            if pitem is not None:
                self.photo_dir.mkdir(parents=True, exist_ok=True)
            with ThreadPoolExecutor(max_workers=3) as pool:
                f_tweet = pool.submit(api.read_text, item["id"]) if item is not None else None
                f_links = pool.submit(api.read_text, litem["id"]) if litem is not None else None
                f_photo = (pool.submit(api.download_small_dict, pitem["id"], str(self.photo_dir), self.photo_file_name(author),
                                       pitem.get("sha1")) if pitem is not None else None)
                tweet_res = f_tweet.result() if f_tweet else None
                links_res = f_links.result() if f_links else None
                photo_res = f_photo.result() if f_photo else None

            # 2. the message
            if tweet_res is not None:
                ok, text, _err = tweet_res
                remote = json.loads(text) if ok else None
                if isinstance(remote, dict):
                    with self._cache_lock:
                        cache = self._read_tweets()
                        cache[author] = {"tweet": str(remote.get("tweet", ""))[:TWEET_LIMIT], "updated": str(remote.get("updated", ""))}
                        self._write_tweets(cache)
                    out["tweet"], out["updated"] = cache[author]["tweet"], cache[author]["updated"]
                    out["state"] = "synced"
            elif out["state"] == "offline":
                out["state"] = "none"

            # 2b. the Jira links
            if links_res is not None:
                ok, text, _err = links_res
                remote = json.loads(text) if ok else None
                if isinstance(remote, dict) and isinstance(remote.get("links"), list):
                    good = [l for l in (normalize_link(x.get("url", ""), x.get("title", "")) for x in remote["links"]
                                        if isinstance(x, dict)) if l][:MAX_LINKS]
                    with self._cache_lock:
                        cache = self._read_links()
                        cache[author] = {"links": good, "updated": str(remote.get("updated", ""))}
                        self._write_links(cache)
                    out["links"] = good
                    if out["state"] != "synced":
                        out["state"] = "synced"

            # 3. the photo (downloaded only when Box's copy differs from the cached one)
            if photo_res is not None and photo_res[0]:
                out["photo"] = self.photo_path(author)
                out["state"] = "synced"
        except Exception:
            return out
        if sent:
            out["state"] = "saved"
        return out

    def warm(self) -> None:
        """Fetch every developer's message, links and photo in the background (a few at a time) so that choosing a developer
        later shows them at once instead of after a Box round trip. The first call also finds the hidden folder; the rest
        then skip the root listing."""
        devs = self.developers()
        if not devs:
            return

        def one(dev):
            try:
                self.sync_profile(dev)
            except Exception:
                pass

        one(devs[0])
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(one, devs[1:]))

    def sync_tweet(self, author: str) -> Tuple[str, str, str]:
        """Message only: (state, text, updated_iso) - see sync_profile."""
        r = self.sync_profile(author)
        return r["state"], r["tweet"], r["updated"]

    # ── activity ─────────────────────────────────────────────────────────────────
    @staticmethod
    def _file_kind(name: str) -> str:
        low = name.lower()
        if "readme" in low:
            return "readme"
        if low == "flow.txt":
            return "settings"
        if low.endswith((".pptx", ".ppt", ".pdf", ".docx", ".doc")):
            return "guide"
        if low.endswith((".ico", ".png", ".jpg", ".jpeg", ".svg")):
            return "icon"
        return "file"

    def _box_events(self, apps: List[AppInfo]) -> List[ActivityEvent]:
        """Everything that happened to an application in Box, from the dates every Refresh saves in
        App_Store/.box-dates.json (see box_dates.py) - an app not refreshed since then simply has none yet:
          * every version zip: "Updated X to vN" on the day it was uploaded (+ "Re-uploaded" if its content was replaced later)
          * README, settings (Flow.txt), guide, icon and any other file: "Added" on its first day, "Edited" on later days
        Several files of the same kind changed by the same upload on the same day count once."""
        from . import box_dates
        saved = box_dates.lookup()
        if not saved:
            return []
        events, seen = [], set()

        def add(day, app, kind, who, version="", detail="", file=""):
            key = (day, app, kind, version, detail, file if kind == "file" else "")
            if day and key not in seen:
                seen.add(key)
                events.append(ActivityEvent(day, app, version, kind, who, detail, file))

        for app in apps:
            try:
                meta = json.loads((self.app_store / app.folder / f"{app.folder}.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for f in meta.get("files", []):
                name = f.get("name", "")
                info = saved.get(str(f.get("id", "")))
                if not info or not name:
                    continue
                created, modified = box_dates.local_day(info.get("created")), box_dates.local_day(info.get("modified"))
                created = created or modified
                who = info.get("by", "")
                m = _VERSION_RE.match(name)
                if m:
                    add(created, app.name, "release", who, version=m.group(1))
                    if modified and created and modified != created:
                        add(modified, app.name, "reupload", who, version=m.group(1))
                else:
                    kind = self._file_kind(name)
                    add(created, app.name, kind, who, detail="Added", file=name)
                    if modified and created and modified != created:
                        add(modified, app.name, kind, who, detail="Edited", file=name)
        return events

    def activity(self, author: str, apps: List[AppInfo]) -> Tuple[List[ActivityEvent], bool]:
        """(events, is_sample). Real events: every file's Box upload/edit date (see _box_events), plus anything
        listed by hand in config-record/dev_activity.json:
             {"events": [{"author": "RoeyYee", "date": "2026-11-05", "app": "GUQC", "version": "1.0.0.2", "kind": "update"}]}
        With neither for this developer, deterministic SAMPLE events are made up so the design can be judged."""
        from . import box_dates
        try:
            manual_stamp = self.activity_file.stat().st_mtime_ns
        except OSError:
            manual_stamp = 0
        key = (author, self.version, box_dates.stamp(), manual_stamp)
        hit = self._memo.get(key)
        if hit is not None:                           # switching back to a developer costs nothing
            return hit
        result = self._activity_now(author, apps)
        self._memo[key] = result
        return result

    def _activity_now(self, author: str, apps: List[AppInfo]) -> Tuple[List[ActivityEvent], bool]:
        events = self._box_events(apps)
        try:
            raw = json.loads(self.activity_file.read_text(encoding="utf-8")).get("events", [])
            for e in raw:
                if e.get("author") == author:
                    events.append(ActivityEvent(date.fromisoformat(e["date"]), e.get("app", ""), e.get("version", ""),
                                                e.get("kind", "update")))
        except (OSError, ValueError, KeyError, AttributeError):
            pass
        if events:
            return sorted(events, key=lambda e: e.day), False
        return self._sample_events(author, apps), True

    @staticmethod
    def _sample_events(author: str, apps: List[AppInfo]) -> List[ActivityEvent]:
        rnd = random.Random(zlib.crc32(author.encode("utf-8")))
        today = date.today()
        start = date(today.year - 2, 1, 1)
        names = [(a.name, a.versions) for a in apps] or [("App", [])]
        busy = max(0.12, min(0.5, 0.1 + 0.05 * len(apps)))
        sprints = [(start + timedelta(days=rnd.randrange(0, (today - start).days)), rnd.randrange(5, 20)) for _ in range(14)]
        events, d = [], start
        while d <= today:
            p = busy * (0.35 if d.weekday() < 5 else 0.05)
            if any(0 <= (d - s).days < n for s, n in sprints):
                p = min(0.85, p * 5 + 0.1) if d.weekday() < 5 else p * 2
            if rnd.random() < p:
                for _ in range(1 + int(rnd.random() ** 2 * 4)):
                    events.append(ActivityEvent(d, rnd.choice(names)[0], ""))
            d += timedelta(days=1)

        # Versions in time order: an app's versions only go up, and its newest event is its newest real version
        # (so the sample never contradicts the Applications cards, e.g. "Split Spread v4.0.0.0").
        newest_first = {name: versions for name, versions in names}
        by_app = {}
        for e in events:
            by_app.setdefault(e.app, []).append(e)
        for name, items in by_app.items():
            versions = sorted(newest_first.get(name) or ["1.0.0.0"], key=_vkey)      # oldest -> newest
            items.sort(key=lambda e: e.day)
            for i, e in enumerate(items):
                e.version = versions[min(len(versions) - 1, (i * len(versions)) // len(items) + (1 if i == len(items) - 1 else 0))]
            items[-1].version = versions[-1]
        return events


_SHARED: Optional[ProfileStore] = None


def shared_store() -> ProfileStore:
    """The one store the page uses, so the App_Store scan can be done in the background before the tab is opened."""
    global _SHARED
    if _SHARED is None:
        _SHARED = ProfileStore()
    return _SHARED


# ═════════════════════════════════════════════ widgets ═════════════════════════════════════════════

_CARD = "QFrame#card { background: #ffffff; border: 1px solid #e5e7eb; border-radius: 14px; }"


def _card() -> QFrame:
    f = QFrame()
    f.setObjectName("card")
    f.setStyleSheet(_CARD)
    return f


def _label(text="", css="", wrap=False) -> QLabel:
    l = QLabel(text)
    l.setStyleSheet(f"QLabel {{ background: transparent; border: none; {css} }}")
    l.setWordWrap(wrap)
    return l


class Avatar(QWidget):
    """Round avatar: the developer's photo when there is one, otherwise a gradient with their initials. Clicking it asks
    for a new photo (the page decides what to do with the click)."""
    clicked = Signal()
    _PALETTES = [("#6366f1", "#8b5cf6"), ("#06b6d4", "#3b82f6"), ("#10b981", "#06b6d4"), ("#f59e0b", "#ef4444"),
                 ("#ec4899", "#8b5cf6"), ("#14b8a6", "#22c55e")]

    def __init__(self, name: str, size: int = 84):
        super().__init__()
        self.setFixedSize(size, size)
        self._name = name
        self._photo: Optional[QPixmap] = None
        self._hover = False
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click to upload your photo")

    def set_photo(self, path) -> None:
        pm = QPixmap(str(path)) if path else QPixmap()
        self._photo = None if pm.isNull() else pm
        self.update()

    def has_photo(self) -> bool:
        return self._photo is not None

    def enterEvent(self, _e):
        self._hover = True; self.update()

    def leaveEvent(self, _e):
        self._hover = False; self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        clip = QPainterPath(); clip.addEllipse(rect)
        p.setClipPath(clip)
        if self._photo is not None:
            p.drawPixmap(rect.toRect(), self._photo)
        else:
            c1, c2 = self._PALETTES[zlib.crc32(self._name.encode("utf-8")) % len(self._PALETTES)]
            g = QLinearGradient(0, 0, self.width(), self.height())
            g.setColorAt(0, QColor(c1)); g.setColorAt(1, QColor(c2))
            p.fillRect(self.rect(), QBrush(g))
            words = re.findall(r"[A-Za-z0-9]+", self._name)
            initials = "".join(w[0] for w in words[:2]).upper() or "?"
            f = QFont(self.font()); f.setPixelSize(int(self.height() * 0.38)); f.setBold(True)
            p.setFont(f); p.setPen(QColor("white")); p.drawText(self.rect(), Qt.AlignCenter, initials)
        if self._hover:                                            # "change photo" hint over the picture
            p.fillRect(self.rect(), QColor(17, 24, 39, 120))
            f = QFont(self.font()); f.setPixelSize(int(self.height() * 0.17)); f.setBold(True)
            p.setFont(f); p.setPen(QColor("white"))
            p.drawText(self.rect(), Qt.AlignCenter, "📷\nChange" if self._photo is not None else "📷\nAdd photo")


class ContributionCalendar(QWidget):
    """GitHub-style year calendar: one square per day, weeks as columns (Sunday first), darker = more updates."""
    day_clicked = Signal(object)            # date, or None when the selected day is clicked again

    CELL, GAP, LEFT, TOP = 15, 4, 36, 24

    def __init__(self):
        super().__init__()
        self._counts: Dict[date, int] = {}
        self._year = date.today().year
        self._selected: Optional[date] = None
        self.setMouseTracking(True)
        self._columns = 53
        self.setMinimumSize(self.sizeHint())

    def set_data(self, counts: Dict[date, int], year: int) -> None:
        self._counts, self._year, self._selected = counts, year, None
        jan1 = date(year, 1, 1)
        self._columns = ((jan1.weekday() + 1) % 7 + (date(year, 12, 31) - jan1).days) // 7 + 1
        self.setMinimumSize(self.sizeHint())
        self.update()

    def sizeHint(self):
        return QSize(self.LEFT + self._columns * (self.CELL + self.GAP) + 4, self.TOP + 7 * (self.CELL + self.GAP) + 34)

    # geometry helpers
    def _first_sunday(self) -> date:
        jan1 = date(self._year, 1, 1)
        return jan1 - timedelta(days=(jan1.weekday() + 1) % 7)

    def _cell_rect(self, col: int, row: int) -> QRectF:
        step = self.CELL + self.GAP
        return QRectF(self.LEFT + col * step, self.TOP + row * step, self.CELL, self.CELL)

    def _day_at(self, pos) -> Optional[date]:
        step = self.CELL + self.GAP
        col, row = int((pos.x() - self.LEFT) // step), int((pos.y() - self.TOP) // step)
        if pos.x() < self.LEFT or pos.y() < self.TOP or not (0 <= row < 7) or not (0 <= col < self._columns):
            return None
        d = self._first_sunday() + timedelta(days=col * 7 + row)
        return d if d.year == self._year else None

    def _level(self, n: int) -> int:
        top = max(self._counts.values(), default=0)
        return 0 if n <= 0 or top == 0 else min(4, math.ceil(4 * n / top))

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        small = QFont(self.font()); small.setPixelSize(11)
        p.setFont(small)
        muted = QColor("#6b7280")
        p.setPen(muted)
        for row, text in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
            r = self._cell_rect(0, row)
            p.drawText(QRectF(0, r.top() - 2, self.LEFT - 8, self.CELL + 4), Qt.AlignRight | Qt.AlignVCenter, text)

        start, today, last_label_col = self._first_sunday(), date.today(), -9
        for col in range(self._columns):
            first_day = start + timedelta(days=col * 7)
            for row in range(7):
                d = first_day + timedelta(days=row)
                if d.year != self._year:
                    continue
                if d.day == 1 and col - last_label_col >= 3:        # month label above the column holding the 1st
                    p.setPen(muted)
                    p.drawText(QRectF(self._cell_rect(col, 0).left(), 2, 40, 16), Qt.AlignLeft | Qt.AlignVCenter,
                               d.strftime("%b"))
                    last_label_col = col
                n = self._counts.get(d, 0)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(LEVEL_COLOURS[self._level(n)]))
                rect = self._cell_rect(col, row)
                p.drawRoundedRect(rect, 3.5, 3.5)
                if d == self._selected:
                    p.setBrush(Qt.NoBrush); p.setPen(QPen(QColor(INDIGO), 1.6))
                    p.drawRoundedRect(rect.adjusted(-1, -1, 1, 1), 3.5, 3.5)
                elif d == today:
                    p.setBrush(Qt.NoBrush); p.setPen(QPen(QColor("#9ca3af"), 1))
                    p.drawRoundedRect(rect.adjusted(-0.5, -0.5, 0.5, 0.5), 3, 3)

        # legend
        y = self.TOP + 7 * (self.CELL + self.GAP) + 8
        p.setPen(muted)
        p.drawText(QRectF(self.LEFT, y, 260, 18), Qt.AlignLeft | Qt.AlignVCenter, "Each square is a day · click one to filter")
        x = self.width() - 4 - (5 * (self.CELL + 3)) - 74
        p.drawText(QRectF(x, y, 30, 18), Qt.AlignRight | Qt.AlignVCenter, "Less")
        x += 36
        for colour in LEVEL_COLOURS:
            p.setPen(Qt.NoPen); p.setBrush(QColor(colour)); p.drawRoundedRect(QRectF(x, y + 3, self.CELL, self.CELL), 3, 3)
            x += self.CELL + 3
        p.setPen(muted)
        p.drawText(QRectF(x + 4, y, 34, 18), Qt.AlignLeft | Qt.AlignVCenter, "More")

    def mouseMoveEvent(self, e):
        d = self._day_at(e.position())
        if d is None:
            QToolTip.hideText()
            self.setCursor(Qt.ArrowCursor)
            return
        self.setCursor(Qt.PointingHandCursor)
        n = self._counts.get(d, 0)
        text = f"{n} update{'s' if n != 1 else ''} on {d.strftime('%b %d, %Y').replace(' 0', ' ')}" if n else \
               f"No updates on {d.strftime('%b %d, %Y').replace(' 0', ' ')}"
        QToolTip.showText(e.globalPosition().toPoint(), text, self)

    def mousePressEvent(self, e):
        d = self._day_at(e.position())
        if d is None or e.button() != Qt.LeftButton:
            return
        self._selected = None if d == self._selected else d
        self.update()
        self.day_clicked.emit(self._selected)


class AppCard(QFrame):
    clicked = Signal(str)

    def __init__(self, app: AppInfo):
        super().__init__()
        self._folder = app.folder
        self.setObjectName("appcard")
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("QFrame#appcard { background: #ffffff; border: 1px solid #e5e7eb; border-radius: 12px; }"
                           "QFrame#appcard:hover { border-color: #4338ca; background: #fafaff; }")
        self.setToolTip(f"Open {app.name} in the Software Store")
        icon = QLabel(); icon.setFixedSize(48, 48); icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet("QLabel { background: #f3f4f6; border-radius: 10px; border: none; font-size: 22px; }")
        pm = None
        if app.icon:
            ic = QIcon(str(app.icon)); sizes = ic.availableSizes()
            if sizes:
                pm = ic.pixmap(max(sizes, key=lambda s: s.width() * s.height()))
        if pm is not None and not pm.isNull():
            icon.setPixmap(pm.scaled(36, 36, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            icon.setText("📦")
        name = _label(app.name, "font-size: 14px; font-weight: 700; color: #111827;")
        name.setWordWrap(False)
        meta = f"v{app.version}" if app.version else "no version yet"
        sub = _label(meta, "font-size: 12px; color: #6b7280;")
        chip = _label(app.category or "—", "font-size: 11px; font-weight: 600; color: #4338ca; background: #eef0ff; "
                                            "border-radius: 8px; padding: 1px 8px;")
        chip.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
        text = QVBoxLayout(); text.setSpacing(2)
        text.addWidget(name); text.addWidget(sub); text.addWidget(chip, 0, Qt.AlignLeft)
        row = QHBoxLayout(self); row.setContentsMargins(14, 12, 14, 12); row.setSpacing(12)
        row.addWidget(icon, 0, Qt.AlignTop); row.addLayout(text, 1)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self._folder)


_EMOJI = {
    "😀": "😀😃😄😁😆😅😂🤣😊😇🙂😉😍🥰😘😎🤩🥳🤔😴😬🙄😢😭😡🤯😱🤗😌😋🤓😏😜",
    "👍": "👍👎👏🙌🙏💪👋🤝✌️🤞👌👀🧠❤️🧡💛💚💙💜🖤💯🔥✨⭐🌟💥💫🎶💕",
    "💻": "💻🖥️⌨️🖱️🛠️🔧⚙️🧪🔬📊📈📉📌📎📝📅⏰🔔📣💡🔍🔒🔑🧩🚀🎯🏆🥇📦🗂️📁",
    "🎉": "🎉🎊🎁🎈☕🍕🍺🍰🌈☀️🌙⚡🌊🌱🌸🐱🐶🦕🐢🦄🐞🦋🍀🌍🏖️🎮🎵📷",
    "✅": "✅❌⚠️❓❗➕➖➡️⬆️⬇️🔄✔️💬🗣️🆕🆗🔴⭕🚫♻️🔗",
}


def _emoji_list(chars: str) -> List[str]:
    """Split a string of emoji into one entry per emoji (a variation selector belongs to the character before it)."""
    out = []
    for ch in chars:
        if ch == "\ufe0f" and out:
            out[-1] += ch
        else:
            out.append(ch)
    return out


class EmojiPicker(QFrame):
    """A small pop-up of common emoji in five groups. Click one to insert it; click anywhere else to close."""
    picked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("emoji")
        self.setStyleSheet("QFrame#emoji { background: #ffffff; border: 1px solid #d5d8e0; border-radius: 12px; }")
        self._grid_host = QWidget(); self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0); self._grid.setSpacing(2)
        tabs = QHBoxLayout(); tabs.setSpacing(2)
        self._tab_btns = {}
        for key in _EMOJI:
            b = QPushButton(key); b.setFixedSize(40, 32); b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self._show(k))
            tabs.addWidget(b); self._tab_btns[key] = b
        tabs.addStretch(1)
        lay = QVBoxLayout(self); lay.setContentsMargins(10, 8, 10, 10); lay.setSpacing(6)
        lay.addLayout(tabs); lay.addWidget(self._grid_host)
        self._show(next(iter(_EMOJI)))

    def _show(self, key: str) -> None:
        for k, b in self._tab_btns.items():
            b.setStyleSheet("QPushButton { background: %s; border: none; border-radius: 8px; font-size: 17px; font-family: 'Segoe UI Emoji'; }"
                            "QPushButton:hover { background: #eef0ff; }" % ("#e0e3ff" if k == key else "transparent"))
        while self._grid.count():
            w = self._grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        for i, emo in enumerate(_emoji_list(_EMOJI[key])):
            b = QPushButton(emo); b.setFixedSize(36, 34); b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet("QPushButton { background: transparent; border: none; border-radius: 8px; font-size: 20px; font-family: 'Segoe UI Emoji'; }"
                            "QPushButton:hover { background: #eef0ff; }")
            b.clicked.connect(lambda _=False, e=emo: self.picked.emit(e))
            self._grid.addWidget(b, i // 10, i % 10)

    def popup_below(self, widget: QWidget) -> None:
        self.adjustSize()
        self.move(widget.mapToGlobal(QPoint(0, widget.height() + 4)))
        self.show()


class TweetCard(QFrame):
    """The developer's pinned message. Edit → 280 characters, Save/Cancel."""
    saved = Signal(str)

    def __init__(self, text: str, when: Optional[datetime]):
        super().__init__()
        self.setObjectName("card")
        self.setStyleSheet("QFrame#card { background: #f8f9ff; border: 1px solid #e0e3ff; border-radius: 14px; }")
        self._text = text
        pin = _label("📌  Pinned message", "font-size: 12px; font-weight: 700; color: #4338ca;")
        self._edit_btn = QPushButton("✎  Edit")
        self._edit_btn.setCursor(Qt.PointingHandCursor); self._edit_btn.setFixedHeight(28)
        self._edit_btn.setToolTip("Write your message. It is saved to Box, so everyone who opens your profile sees it.")
        self._edit_btn.setStyleSheet("QPushButton { background: #ffffff; color: #000000; border: 1px solid #d5d8e0; border-radius: 8px;"
                                     " padding: 0 12px; font-size: 12px; } QPushButton:hover { border-color: #4338ca; background: #eef0ff; }")
        self._edit_btn.clicked.connect(self._start_edit)
        head = QHBoxLayout(); head.addWidget(pin); head.addStretch(1); head.addWidget(self._edit_btn)

        self._quote = _label("", "font-size: 17px; font-weight: 500; color: #111827; font-family: 'Segoe UI', 'Segoe UI Emoji';", wrap=True)
        self._quote.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._when = _label("", "font-size: 11px; color: #6b7280;")
        self._status = _label("", "font-size: 11px; color: #6b7280;")

        self._editor = QPlainTextEdit()
        self._editor.setFixedHeight(92)
        self._editor.setPlaceholderText("What do you want people to know? (shown at the top of your profile)")
        self._editor.setStyleSheet("QPlainTextEdit { background: #ffffff; color: #000000; border: 1px solid #c7cad3; border-radius: 10px;"
                                   " padding: 6px; font-size: 14px; font-family: 'Segoe UI', 'Segoe UI Emoji'; } QPlainTextEdit:focus { border-color: #4338ca; }" + MODERN_SCROLLBAR_STYLE)
        self._editor.textChanged.connect(self._limit)
        self._count = _label("", "font-size: 11px; color: #6b7280;")
        save = QPushButton("Save"); cancel = QPushButton("Cancel")
        for b in (save, cancel):
            b.setFixedHeight(30); b.setMinimumWidth(80); b.setCursor(Qt.PointingHandCursor)
        save.setStyleSheet("QPushButton { background: #4338ca; color: #ffffff; border: none; border-radius: 8px; font-weight: 600; }"
                           "QPushButton:hover { background: #3730a3; }")
        cancel.setStyleSheet("QPushButton { background: #f3f4f6; color: #000000; border: 1px solid #d1d5db; border-radius: 8px; }"
                             "QPushButton:hover { background: #e5e7eb; }")
        save.clicked.connect(self._save); cancel.clicked.connect(self._stop_edit)
        self._emoji_btn = QPushButton("😊"); self._emoji_btn.setFixedSize(40, 30); self._emoji_btn.setCursor(Qt.PointingHandCursor)
        self._emoji_btn.setToolTip("Add an emoji  (Windows key + . also works)")
        self._emoji_btn.setStyleSheet("QPushButton { background: #ffffff; border: 1px solid #d5d8e0; border-radius: 8px; font-size: 16px;"
                                      " font-family: 'Segoe UI Emoji'; } QPushButton:hover { border-color: #4338ca; background: #eef0ff; }")
        self._emoji_btn.clicked.connect(self._open_emoji)
        self._emoji = EmojiPicker(self)
        self._emoji.picked.connect(self._insert_emoji)
        self._edit_row = QWidget(); er = QHBoxLayout(self._edit_row); er.setContentsMargins(0, 0, 0, 0)
        er.addWidget(self._emoji_btn); er.addSpacing(8)
        er.addWidget(self._count); er.addStretch(1); er.addWidget(cancel); er.addWidget(save)
        self._editor.hide(); self._edit_row.hide()

        lay = QVBoxLayout(self); lay.setContentsMargins(20, 14, 20, 14); lay.setSpacing(8)
        lay.addLayout(head); lay.addWidget(self._quote); lay.addWidget(self._editor); lay.addWidget(self._edit_row)
        lay.addWidget(self._when); lay.addWidget(self._status)
        self.set_tweet(text, when)

    def _open_emoji(self):
        self._emoji.popup_below(self._emoji_btn)

    def _insert_emoji(self, emo: str):
        self._editor.insertPlainText(emo)              # at the cursor, or replacing the selection; _limit keeps 280
        self._editor.setFocus()

    def is_editing(self) -> bool:
        return not self._editor.isHidden()

    def set_status(self, text: str, kind: str = "info") -> None:
        colour = {"ok": "#15803d", "warn": "#b45309", "info": "#6b7280"}.get(kind, "#6b7280")
        self._status.setStyleSheet(f"QLabel {{ background: transparent; border: none; font-size: 11px; color: {colour}; }}")
        self._status.setText(text)

    def set_tweet(self, text: str, when: Optional[datetime]) -> None:
        self._text = text
        if text:
            self._quote.setText(f"“{text}”")
            self._quote.setStyleSheet("QLabel { background: transparent; border: none; font-size: 17px; font-weight: 500; color: #111827;"
                                      " font-family: 'Segoe UI', 'Segoe UI Emoji'; }")
            self._when.setText(f"Updated {self._ago(when)}" if when else "")
        else:
            self._quote.setText("No message yet — press Edit to pin one.")
            self._quote.setStyleSheet("QLabel { background: transparent; border: none; font-size: 15px; color: #9ca3af; font-style: italic; }")
            self._when.setText("")

    @staticmethod
    def _ago(when: datetime) -> str:
        secs = int((datetime.now() - when).total_seconds())
        for limit, unit, size in ((60, "second", 1), (3600, "minute", 60), (86400, "hour", 3600), (86400 * 30, "day", 86400),
                                  (86400 * 365, "month", 86400 * 30)):
            if secs < limit:
                n = max(1, secs // size)
                return f"{n} {unit}{'s' if n != 1 else ''} ago"
        n = secs // (86400 * 365)
        return f"{n} year{'s' if n != 1 else ''} ago"

    def _start_edit(self):
        self._editor.setPlainText(self._text)
        self._quote.hide(); self._when.hide(); self._edit_btn.hide()
        self._editor.show(); self._edit_row.show(); self._editor.setFocus()
        self._limit()

    def _stop_edit(self):
        self._editor.hide(); self._edit_row.hide()
        self._quote.show(); self._when.show(); self._edit_btn.show()

    def _limit(self):
        text = self._editor.toPlainText()
        if len(text) > TWEET_LIMIT:
            self._editor.blockSignals(True)
            self._editor.setPlainText(text[:TWEET_LIMIT])
            self._editor.moveCursor(QTextCursor.End)
            self._editor.blockSignals(False)
            text = text[:TWEET_LIMIT]
        left = TWEET_LIMIT - len(text)
        self._count.setText(f"{left} characters left")
        self._count.setStyleSheet("QLabel { background: transparent; border: none; font-size: 11px; color: %s; }"
                                  % ("#b91c1c" if left < 20 else "#6b7280"))

    def _save(self):
        text = self._editor.toPlainText().strip()
        self._stop_edit()
        self.saved.emit(text)


class JiraLinksCard(QFrame):
    """"Jira tickets": the links a developer wants people to look at (a board, a filter, a ticket). Click one to open it in
    the browser; "+ Add link" and the x on each row edit the list. Only http/https links are accepted."""
    changed = Signal(list)                 # the new list of {"title", "url", "key"}

    _INPUT = ("QLineEdit { background: #ffffff; color: #000000; border: 1px solid #c7cad3; border-radius: 8px; padding: 0 10px; font-size: 13px; }"
              "QLineEdit:focus { border-color: #4338ca; }")

    def __init__(self, links: List[dict]):
        super().__init__()
        self.setObjectName("card")
        self.setStyleSheet(_CARD)
        self._links: List[dict] = []
        self._title = _label("", "font-size: 15px; font-weight: 700; color: #111827;")
        self._add_btn = QPushButton("＋  Add link"); self._add_btn.setCursor(Qt.PointingHandCursor); self._add_btn.setStyleSheet(_LINK_BTN)
        self._add_btn.clicked.connect(self._show_form)
        self._status = _label("", "font-size: 11px; color: #6b7280;")
        head = QHBoxLayout(); head.addWidget(self._title); head.addStretch(1); head.addWidget(self._status); head.addWidget(self._add_btn)

        self._rows = QVBoxLayout(); self._rows.setSpacing(0)

        self._form = QWidget()
        self._name = QLineEdit(); self._name.setPlaceholderText("Title (optional)"); self._name.setFixedHeight(32)
        self._url = QLineEdit(); self._url.setPlaceholderText("Paste the Jira link, e.g. https://jira.company.com/browse/ABC-123"); self._url.setFixedHeight(32)
        for e in (self._name, self._url):
            e.setStyleSheet(self._INPUT)
        self._name.setFixedWidth(220)
        self._url.returnPressed.connect(self._add)
        ok = QPushButton("Add"); cancel = QPushButton("Cancel")
        for b in (ok, cancel):
            b.setFixedHeight(32); b.setMinimumWidth(70); b.setCursor(Qt.PointingHandCursor)
        ok.setStyleSheet("QPushButton { background: #4338ca; color: #ffffff; border: none; border-radius: 8px; font-weight: 600; }"
                         "QPushButton:hover { background: #3730a3; }")
        cancel.setStyleSheet("QPushButton { background: #f3f4f6; color: #000000; border: 1px solid #d1d5db; border-radius: 8px; }"
                             "QPushButton:hover { background: #e5e7eb; }")
        ok.clicked.connect(self._add); cancel.clicked.connect(self._hide_form)
        self._error = _label("", "font-size: 11px; color: #b91c1c;")
        frow = QHBoxLayout(); frow.setContentsMargins(0, 0, 0, 0); frow.setSpacing(8)
        frow.addWidget(self._name); frow.addWidget(self._url, 1); frow.addWidget(ok); frow.addWidget(cancel)
        fcol = QVBoxLayout(self._form); fcol.setContentsMargins(0, 6, 0, 0); fcol.setSpacing(4)
        fcol.addLayout(frow); fcol.addWidget(self._error)
        self._form.hide()

        self._empty = _label("No ticket links yet - add the Jira board, filter or tickets people should look at.",
                             "font-size: 13px; color: #9ca3af; padding: 6px 0;")
        lay = QVBoxLayout(self); lay.setContentsMargins(20, 14, 20, 14); lay.setSpacing(6)
        lay.addLayout(head); lay.addWidget(self._empty); lay.addLayout(self._rows); lay.addWidget(self._form)
        self.set_links(links)

    def set_status(self, text: str, kind: str = "info") -> None:
        colour = {"ok": "#15803d", "warn": "#b45309", "info": "#6b7280"}.get(kind, "#6b7280")
        self._status.setStyleSheet(f"QLabel {{ background: transparent; border: none; font-size: 11px; color: {colour}; }}")
        self._status.setText(text)

    def is_editing(self) -> bool:
        return not self._form.isHidden()

    def links(self) -> List[dict]:
        return list(self._links)

    def set_links(self, links: List[dict]) -> None:
        self._links = [dict(l) for l in links][:MAX_LINKS]
        self._title.setText(f"🎫  Jira tickets  ·  {len(self._links)}")
        self._empty.setVisible(not self._links)
        self._add_btn.setVisible(len(self._links) < MAX_LINKS)
        while self._rows.count():
            w = self._rows.takeAt(0).widget()
            if w:
                w.hide(); w.deleteLater()
        for i, link in enumerate(self._links):
            self._rows.addWidget(self._row(i, link))

    def _row(self, index: int, link: dict) -> QWidget:
        row = QFrame(); row.setObjectName("jrow"); row.setCursor(Qt.PointingHandCursor); row.setToolTip(link["url"])
        row.setStyleSheet("QFrame#jrow { background: transparent; border: none; border-bottom: 1px solid #f0f1f5; }"
                          "QFrame#jrow:hover { background: #f7f7ff; }")
        host = urlparse(link["url"]).netloc
        chip = _label(link.get("key") or "🎫", "font-size: 12px; font-weight: 700; color: #1d4ed8; background: #dbeafe; border-radius: 8px; padding: 2px 8px;")
        chip.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
        text = _label(link.get("title") or link.get("key") or host, "font-size: 13px; font-weight: 600; color: #111827;")
        sub = _label(host, "font-size: 11px; color: #6b7280;")
        col = QVBoxLayout(); col.setSpacing(0); col.addWidget(text); col.addWidget(sub)
        open_lbl = _label("Open ↗", "font-size: 12px; color: #4338ca; font-weight: 600;")
        remove = QPushButton("✕"); remove.setFixedSize(26, 26); remove.setCursor(Qt.PointingHandCursor); remove.setToolTip("Remove this link")
        remove.setStyleSheet("QPushButton { background: transparent; color: #9ca3af; border: none; border-radius: 13px; font-size: 13px; }"
                             "QPushButton:hover { background: #fee2e2; color: #b91c1c; }")
        remove.clicked.connect(lambda _=False, i=index: self._remove(i))
        r = QHBoxLayout(row); r.setContentsMargins(4, 8, 4, 8); r.setSpacing(12)
        r.addWidget(chip, 0, Qt.AlignVCenter); r.addLayout(col, 1); r.addWidget(open_lbl); r.addWidget(remove)
        row.mousePressEvent = lambda e, u=link["url"]: QDesktopServices.openUrl(QUrl(u)) if e.button() == Qt.LeftButton else None
        return row

    # editing
    def _show_form(self):
        self._error.setText(""); self._form.show(); self._url.setFocus()

    def _hide_form(self):
        self._name.clear(); self._url.clear(); self._error.setText(""); self._form.hide()

    def _add(self):
        link = normalize_link(self._url.text(), self._name.text())
        if link is None:
            self._error.setText("That is not a web link - paste the full address (it starts with https://).")
            return
        if any(l["url"] == link["url"] for l in self._links):
            self._error.setText("That link is already in the list.")
            return
        self._hide_form()
        self.set_links(self._links + [link])
        self.changed.emit(self.links())

    def _remove(self, index: int):
        if not (0 <= index < len(self._links)):
            return
        if QMessageBox.question(self, "Remove link", f"Remove this link?\n\n{self._links[index]['url']}") != QMessageBox.Yes:
            return
        self.set_links(self._links[:index] + self._links[index + 1:])
        self.changed.emit(self.links())


class StatChip(QFrame):
    def __init__(self, value: str, caption: str):
        super().__init__()
        self.setObjectName("card")
        self.setStyleSheet(_CARD)
        self._value = _label(value, "font-size: 24px; font-weight: 800; color: #111827;")
        lay = QVBoxLayout(self); lay.setContentsMargins(18, 12, 18, 12); lay.setSpacing(0)
        lay.addWidget(self._value); lay.addWidget(_label(caption, "font-size: 12px; color: #6b7280;"))


TOP_APPS = 6          # shown before "Show all"
PAGE_SIZE = 12        # applications per page once the list is open (4 rows of 3)
_LINK_BTN = ("QPushButton { background: transparent; color: #4338ca; border: none; font-size: 13px; font-weight: 600; padding: 0 6px; }"
             "QPushButton:hover { text-decoration: underline; } QPushButton:disabled { color: #c7c9d3; }")
_SEARCH = ("QLineEdit { background: #ffffff; color: #000000; border: 1px solid #d5d8e0; border-radius: 8px; padding: 0 10px; font-size: 12px; }"
           "QLineEdit:focus { border-color: #4338ca; }")

_YEAR_ON = ("QPushButton { background: #4338ca; color: #ffffff; border: none; border-radius: 8px; padding: 0 14px; font-weight: 600; }")
_YEAR_OFF = ("QPushButton { background: transparent; color: #4b5563; border: none; border-radius: 8px; padding: 0 14px; }"
             "QPushButton:hover { background: #f3f4f6; }")


class DevProfilePage(QWidget):
    """The whole page. *store* is a ProfileStore; open_app(folder_name) is called when an application card is clicked."""
    _tweet_synced = Signal(str, str, str, str, str)  # author, state, text, updated, photo path ("" = none)
    _tweet_pushed = Signal(str, bool, str)           # author, ok, error
    _photo_pushed = Signal(str, bool, str)           # author, ok, error
    _links_synced = Signal(str, str)                 # author, links as JSON
    _links_pushed = Signal(str, bool, str)           # author, ok, error

    def __init__(self, store: Optional[ProfileStore] = None, state: Optional[dict] = None, open_app=None):
        super().__init__()
        self._store = store or shared_store()
        self._state = state if state is not None else {}
        self._open_app = open_app
        self._last_sync = 0.0
        self._tweet_synced.connect(self._on_tweet_synced)
        self._tweet_pushed.connect(self._on_tweet_pushed)
        self._photo_pushed.connect(self._on_photo_pushed)
        self._links_synced.connect(self._on_links_synced)
        self._links_pushed.connect(self._on_links_pushed)
        self.setStyleSheet("QWidget#devpage { background: transparent; }")
        self.setObjectName("devpage")
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(4, 4, 18, 24)
        self._lay.setSpacing(16)
        self._devs = self._store.developers()
        if not self._devs:
            self._lay.addWidget(_label(f"{DEV_PROFILE_ICON} No developers found yet - refresh the Software Store first.",
                                       "font-size: 18px; color: #6c757d; padding: 60px;"))
            return
        self._author = self._state.get("author") if self._state.get("author") in self._devs else self._devs[0]
        self._build()

    # ── build ────────────────────────────────────────────────────────────────────
    def _clear(self):
        self._empty_layout(self._lay)

    @classmethod
    def _empty_layout(cls, layout):
        """Remove and delete everything in *layout*, INCLUDING the widgets inside nested layouts (the stat cards sit in a
        row layout: taking only top-level widgets left them behind, still visible, one set more per developer switch)."""
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
            elif item.layout() is not None:
                cls._empty_layout(item.layout())
                item.layout().deleteLater()

    def _build(self):
        """Build the page in three short steps. The top (name, photo, message, Jira links) appears at once; the stat cards
        and calendar, then the applications and the activity list, follow in the next turns of the event loop - so choosing a
        developer never freezes the window for the whole build, even while a refresh keeps the CPU busy."""
        self._clear()
        self._gen = getattr(self, "_gen", 0) + 1
        gen, author = self._gen, self._author
        self._state["author"] = author
        self._built_version = self._store.version
        apps = self._store.apps_of(author)
        versions = sum(len(a.versions) for a in apps)

        self._lay.addWidget(self._header(author, apps, versions))
        tweet, when = self._store.tweet(author)
        self._tweet = TweetCard(tweet, when)
        self._tweet.saved.connect(self._on_tweet_saved)
        self._lay.addWidget(self._tweet)
        self._jira = JiraLinksCard(self._store.links(author))
        self._jira.changed.connect(self._on_links_changed)
        self._lay.addWidget(self._jira)
        self._sync_tweet()
        QTimer.singleShot(0, lambda: self._build_middle(gen, author, apps, versions))

    def _build_middle(self, gen, author, apps, versions):
        if gen != self._gen:
            return                                    # another developer was chosen meanwhile
        self._events, self._sample = self._store.activity(author, apps)
        years = sorted({e.day.year for e in self._events}, reverse=True) or [date.today().year]
        self._years = years
        self._year = self._state.get("year") if self._state.get("year") in years else years[0]
        self._lay.addLayout(self._stats(apps, versions))
        self._lay.addWidget(self._calendar_card())
        QTimer.singleShot(0, lambda: self._build_tail(gen, apps))

    def _build_tail(self, gen, apps):
        if gen != self._gen:
            return
        self._lay.addWidget(self._apps_section(apps))
        self._lay.addWidget(self._timeline_card())
        self._lay.addStretch(1)
        self._refresh_year()

    def _header(self, author, apps, versions) -> QFrame:
        from .news_editor import ModernCombo
        card = _card()
        name = _label(author, "font-size: 26px; font-weight: 800; color: #111827;")
        sub = _label(f"Developer  ·  {len(apps)} application{'s' if len(apps) != 1 else ''}  ·  {versions} version{'s' if versions != 1 else ''} published",
                     "font-size: 13px; color: #6b7280;")
        text = QVBoxLayout(); text.setSpacing(3); text.addStretch(1); text.addWidget(name); text.addWidget(sub); text.addStretch(1)
        combo = ModernCombo(self._devs, 190)
        combo.setCurrentIndex(self._devs.index(author)); combo.setToolTip("Switch developer")
        combo.setFixedHeight(32)
        combo.activated.connect(lambda i: self._switch(self._devs[i]))
        picker = QVBoxLayout(); picker.addWidget(_label("Developer", "font-size: 11px; color: #6b7280;"), 0, Qt.AlignRight)
        picker.addWidget(combo, 0, Qt.AlignRight); picker.addStretch(1)
        row = QHBoxLayout(card); row.setContentsMargins(22, 18, 22, 18); row.setSpacing(18)
        self._avatar = Avatar(author)
        self._avatar.set_photo(self._store.photo_path(author))          # this PC's copy at once; Box's arrives in a moment
        self._avatar.clicked.connect(self._choose_photo)
        self._photo_note = _label("", "font-size: 11px; color: #6b7280;")
        text.insertWidget(text.count() - 1, self._photo_note)
        row.addWidget(self._avatar, 0, Qt.AlignVCenter); row.addLayout(text, 1); row.addLayout(picker)
        return card

    def _stats(self, apps, versions) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(12)
        self._stat_updates = StatChip("0", "updates this year")
        self._stat_days = StatChip("0", "active days")
        self._stat_streak = StatChip("0", "longest streak (days)")
        for chip in (StatChip(str(len(apps)), "applications"), StatChip(str(versions), "versions published"),
                     self._stat_updates, self._stat_days, self._stat_streak):
            row.addWidget(chip, 1)
        return row

    def _calendar_card(self) -> QFrame:
        card = _card()
        self._cal_title = _label("", "font-size: 15px; font-weight: 700; color: #111827;")
        self._sample_note = _label("SAMPLE DATA — the real source of updates is not connected yet", "font-size: 10px; font-weight: 700; color: #b45309;"
                                   " background: #fef3c7; border-radius: 8px; padding: 2px 8px;")
        self._sample_note.setVisible(self._sample)
        years = QHBoxLayout(); years.setSpacing(4)
        self._year_btns = {}
        for y in self._years:
            b = QPushButton(str(y)); b.setFixedHeight(28); b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, yy=y: self._set_year(yy))
            years.addWidget(b); self._year_btns[y] = b
        head = QHBoxLayout(); head.addWidget(self._cal_title); head.addSpacing(10); head.addWidget(self._sample_note)
        head.addStretch(1); head.addLayout(years)
        self._calendar = ContributionCalendar()
        self._calendar.day_clicked.connect(self._on_day)
        scroll = QScrollArea(); scroll.setWidgetResizable(False); scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(self._calendar)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; } QScrollArea > QWidget > QWidget { background: transparent; }"
                             + MODERN_SCROLLBAR_STYLE)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFixedHeight(self._calendar.sizeHint().height() + 14)
        lay = QVBoxLayout(card); lay.setContentsMargins(20, 16, 20, 12); lay.setSpacing(10)
        lay.addLayout(head); lay.addWidget(scroll)
        self._cal_scroll = scroll
        return card

    def _apps_section(self, apps) -> QWidget:
        """A developer can own 50+ applications, so the page never lists them all at once: the most active few first;
        "Show all" opens the full list, 12 per page, with a filter box."""
        self._apps = list(apps)
        self._app_query = ""
        self._app_page = 0
        self._apps_open = False
        box = QWidget(); box.setStyleSheet("QWidget { background: transparent; }")
        lay = QVBoxLayout(box); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(10)

        self._apps_title = _label("", "font-size: 15px; font-weight: 700; color: #111827;")
        self._apps_search = QLineEdit(); self._apps_search.setPlaceholderText("Filter applications…")
        self._apps_search.setFixedSize(220, 30); self._apps_search.setStyleSheet(_SEARCH)
        self._apps_search.textChanged.connect(self._on_apps_query)
        self._apps_toggle = QPushButton(); self._apps_toggle.setCursor(Qt.PointingHandCursor)
        self._apps_toggle.setStyleSheet(_LINK_BTN); self._apps_toggle.clicked.connect(self._toggle_apps)
        many = len(self._apps) > TOP_APPS
        self._apps_search.setVisible(many); self._apps_toggle.setVisible(many)
        head = QHBoxLayout(); head.setSpacing(8)
        head.addWidget(self._apps_title); head.addStretch(1); head.addWidget(self._apps_search); head.addWidget(self._apps_toggle)
        lay.addLayout(head)

        self._apps_grid_host = QWidget()
        self._apps_grid = QGridLayout(self._apps_grid_host)
        self._apps_grid.setContentsMargins(0, 0, 0, 0); self._apps_grid.setSpacing(12)
        for c in range(3):
            self._apps_grid.setColumnStretch(c, 1)
        lay.addWidget(self._apps_grid_host)

        self._prev = QPushButton("‹  Previous"); self._next = QPushButton("Next  ›")
        for b in (self._prev, self._next):
            b.setCursor(Qt.PointingHandCursor); b.setStyleSheet(_LINK_BTN)
        self._prev.clicked.connect(lambda: self._goto_apps_page(self._app_page - 1))
        self._next.clicked.connect(lambda: self._goto_apps_page(self._app_page + 1))
        self._pager_label = _label("", "font-size: 12px; color: #6b7280;")
        self._pager = QWidget()
        pl = QHBoxLayout(self._pager); pl.setContentsMargins(0, 0, 0, 0)
        pl.addStretch(1); pl.addWidget(self._prev); pl.addWidget(self._pager_label); pl.addWidget(self._next); pl.addStretch(1)
        lay.addWidget(self._pager)
        self._render_apps()
        return box

    def _ranked_apps(self):
        """Most updates in the chosen year first (then A-Z): what the collapsed list shows."""
        counts = Counter(e.app for e in self._events if e.day.year == getattr(self, "_year", None))
        return sorted(self._apps, key=lambda a: (-counts.get(a.name, 0), a.name.lower()))

    def _render_apps(self):
        total = len(self._apps)
        query = self._app_query.strip().lower()
        if query:
            pool = [a for a in sorted(self._apps, key=lambda a: a.name.lower()) if query in a.name.lower()]
        elif self._apps_open:
            pool = sorted(self._apps, key=lambda a: a.name.lower())
        else:
            pool = self._ranked_apps()[:TOP_APPS]
        paged = bool(query) or self._apps_open
        pages = max(1, math.ceil(len(pool) / PAGE_SIZE)) if paged else 1
        self._app_page = min(self._app_page, pages - 1)
        shown = pool[self._app_page * PAGE_SIZE:(self._app_page + 1) * PAGE_SIZE] if paged else pool

        while self._apps_grid.count():
            w = self._apps_grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        for i, app in enumerate(shown):
            card = AppCard(app); card.clicked.connect(self._on_app)
            self._apps_grid.addWidget(card, i // 3, i % 3)
        if not shown:
            self._apps_grid.addWidget(_label(f"No application matches '{self._app_query.strip()}'.",
                                             "font-size: 13px; color: #9ca3af; padding: 14px 4px;"), 0, 0, 1, 3)

        if query:
            self._apps_title.setText(f"Applications  ·  {len(pool)} of {total} match")
        elif self._apps_open or total <= TOP_APPS:
            self._apps_title.setText(f"Applications  ·  {total}")
        else:
            self._apps_title.setText(f"Applications  ·  top {len(shown)} of {total}")
        self._apps_toggle.setText("Show top %d" % TOP_APPS if self._apps_open else f"Show all {total}")
        self._apps_toggle.setVisible(total > TOP_APPS and not query)
        self._pager.setVisible(paged and pages > 1)
        first = self._app_page * PAGE_SIZE + 1
        self._pager_label.setText(f"Page {self._app_page + 1} of {pages}   ·   {first}–{first + len(shown) - 1} of {len(pool)}")
        self._prev.setEnabled(self._app_page > 0)
        self._next.setEnabled(self._app_page < pages - 1)

    def _toggle_apps(self):
        self._apps_open = not self._apps_open
        self._app_page = 0
        self._render_apps()

    def _on_apps_query(self, text):
        self._app_query = text
        self._app_page = 0
        self._render_apps()

    def _goto_apps_page(self, page):
        self._app_page = max(0, page)
        self._render_apps()

    def _timeline_card(self) -> QFrame:
        card = _card()
        self._tl_title = _label("", "font-size: 15px; font-weight: 700; color: #111827;")
        self._tl_box = QVBoxLayout(); self._tl_box.setSpacing(0)
        lay = QVBoxLayout(card); lay.setContentsMargins(20, 16, 20, 14); lay.setSpacing(8)
        lay.addWidget(self._tl_title); lay.addLayout(self._tl_box)
        return card

    # ── behaviour ────────────────────────────────────────────────────────────────
    def _switch(self, author):
        self._author = author
        self._state["year"] = None
        self._build()

    def _set_year(self, year):
        self._year = year
        self._state["year"] = year
        self._refresh_year()

    def _counts(self, year) -> Dict[date, int]:
        return dict(Counter(e.day for e in self._events if e.day.year == year))

    def _refresh_year(self):
        counts = self._counts(self._year)
        total = sum(counts.values())
        self._cal_title.setText(f"{total} update{'s' if total != 1 else ''} in {self._year}")
        for y, b in self._year_btns.items():
            b.setStyleSheet(_YEAR_ON if y == self._year else _YEAR_OFF)
        self._calendar.set_data(counts, self._year)
        self._cal_scroll.setFixedHeight(self._calendar.sizeHint().height() + 14)
        days = sorted(counts)
        longest = run = 0
        prev = None
        for d in days:
            run = run + 1 if prev and (d - prev).days == 1 else 1
            longest, prev = max(longest, run), d
        self._stat_updates._value.setText(str(total))
        self._stat_days._value.setText(str(len(days)))
        self._stat_streak._value.setText(str(longest))
        self._fill_timeline(None)
        if hasattr(self, "_apps_grid"):
            self._render_apps()

    def _on_day(self, day):
        self._fill_timeline(day)

    _KIND_COLOURS = {"release": "#40c463", "reupload": "#f59e0b", "readme": "#3b82f6", "settings": "#6366f1",
                     "guide": "#8b5cf6", "icon": "#ec4899", "file": "#9ca3af", "update": "#40c463"}

    @staticmethod
    def _describe(e: "ActivityEvent") -> str:
        who = f" <span style='color:#6b7280;'>by {e.who}</span>" if e.who else ""
        app = f"<b>{e.app}</b>"
        if e.kind == "reupload":
            return f"Re-uploaded <b>v{e.version}</b> of {app}{who}"
        if e.kind in ("release", "update"):
            return f"Updated {app}" + (f" to <b>v{e.version}</b>" if e.version else "") + who
        verb = e.detail or "Edited"
        target = {"readme": "the README of", "settings": "the settings (Flow.txt) of", "guide": "the guide of",
                  "icon": "the icon of"}.get(e.kind)
        if target:
            return f"{verb} {target} {app}{who}"
        return f"{verb} <b>{e.file}</b> of {app}{who}"

    def _fill_timeline(self, day: Optional[date]):
        events = [e for e in self._events if e.day.year == self._year]
        if day:
            events = [e for e in events if e.day == day]
            self._tl_title.setText(f"Activity on {day.strftime('%b %d, %Y').replace(' 0', ' ')}  ·  {len(events)}")
        else:
            self._tl_title.setText(f"Recent activity in {self._year}  ·  {len(events)}")
        self._tl_list = sorted(events, key=lambda e: e.day, reverse=True)
        self._tl_shown = 10
        self._render_timeline()

    def _render_timeline(self):
        while self._tl_box.count():
            w = self._tl_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        if not self._tl_list:
            self._tl_box.addWidget(_label("No activity.", "font-size: 13px; color: #9ca3af; padding: 8px 0;"))
            return
        for e in self._tl_list[:self._tl_shown]:
            row = QWidget(); row.setStyleSheet("QWidget { background: transparent; }")
            r = QHBoxLayout(row); r.setContentsMargins(0, 7, 0, 7); r.setSpacing(12)
            dot = _label("", f"background: {self._KIND_COLOURS.get(e.kind, '#9ca3af')}; border-radius: 5px;"); dot.setFixedSize(10, 10)
            text = _label(self._describe(e), "font-size: 13px; color: #111827;"); text.setTextFormat(Qt.RichText)
            when = _label(e.day.strftime("%b %d, %Y").replace(" 0", " "), "font-size: 12px; color: #6b7280;")
            r.addWidget(dot); r.addWidget(text, 1); r.addWidget(when)
            self._tl_box.addWidget(row)
        left = len(self._tl_list) - self._tl_shown
        if left > 0:
            more = QPushButton(f"Show {min(20, left)} more   ({left} not shown)")
            more.setCursor(Qt.PointingHandCursor); more.setStyleSheet(_LINK_BTN); more.setFixedHeight(32)
            more.clicked.connect(self._more_timeline)
            self._tl_box.addWidget(more, 0, Qt.AlignLeft)

    def _more_timeline(self):
        self._tl_shown += 20
        self._render_timeline()

    def _sync_tweet(self):
        """Fetch the developer's message from Box on a worker thread (the local copy is on screen meanwhile)."""
        author = self._author
        self._last_sync = time.monotonic()
        self._tweet.set_status("Checking Box…")

        def work():
            try:
                r = self._store.sync_profile(author)
                self._tweet_synced.emit(author, r["state"], r["tweet"], r["updated"], str(r["photo"] or ""))
                self._links_synced.emit(author, json.dumps(r.get("links", [])))
            except RuntimeError:
                pass                                   # the page was deleted while Box was answering

        threading.Thread(target=work, daemon=True, name="TweetSync").start()

    def showEvent(self, event):
        super().showEvent(event)
        # the page is kept between visits: look at Box again when it is shown after a while (others may have written)
        if hasattr(self, "_tweet") and self._store.version != getattr(self, "_built_version", self._store.version):
            self._build()                             # the application list was re-read since this page was built
        elif hasattr(self, "_tweet") and time.monotonic() - self._last_sync > 120:
            self._sync_tweet()

    def _on_tweet_synced(self, author, state, text, updated, photo):
        if author != self._author or not hasattr(self, "_tweet"):
            return
        if photo and hasattr(self, "_avatar"):
            self._avatar.set_photo(photo)
        if state in ("synced", "saved") and not self._tweet.is_editing():
            try:
                when = datetime.fromisoformat(updated) if updated else None
            except ValueError:
                when = None
            self._tweet.set_tweet(text, when)
        notes = {"synced": ("Synced with Box", "info"), "saved": ("Saved to Box ✓", "ok"),
                 "none": ("Not pinned in Box yet", "info"),
                 "offline": ("Box could not be reached - showing the copy on this PC; it is sent on the next visit", "warn")}
        self._tweet.set_status(*notes.get(state, ("", "info")))

    def _on_tweet_saved(self, text):
        author = self._author
        self._store.set_tweet(author, text)
        tweet, when = self._store.tweet(author)
        self._tweet.set_tweet(tweet, when)
        self._tweet.set_status("Saving to Box…")

        def work():
            try:
                ok, err = self._store.push_tweet(author)
                self._tweet_pushed.emit(author, ok, err)
            except RuntimeError:
                pass

        threading.Thread(target=work, daemon=True, name="TweetPush").start()

    def _choose_photo(self):
        """The avatar was clicked: pick a picture, crop it to a square, show it, and send it to Box."""
        path, _ = QFileDialog.getOpenFileName(self, "Choose your photo", "",
                                              "Pictures (*.png *.jpg *.jpeg *.bmp *.gif *.webp);;All files (*.*)")
        if not path:
            return
        png = prepare_photo(path)
        if png is None:
            self._photo_note.setText("That file could not be used as a photo (is it a picture under 25 MB?)")
            return
        author = self._author
        self._avatar.set_photo(self._store.set_photo(author, png))
        self._photo_note.setText("Saving your photo to Box…")

        def work():
            try:
                ok, err = self._store.push_photo(author)
                self._photo_pushed.emit(author, ok, err)
            except RuntimeError:
                pass

        threading.Thread(target=work, daemon=True, name="PhotoPush").start()

    def _on_photo_pushed(self, author, ok, err):
        if author != self._author or not hasattr(self, "_photo_note"):
            return
        self._photo_note.setText("Photo saved to Box ✓" if ok else
                                 "Could not reach Box - the photo is kept on this PC and sent on the next visit")

    def _on_links_synced(self, author, payload):
        if author != self._author or not hasattr(self, "_jira") or self._jira.is_editing():
            return
        try:
            links = json.loads(payload)
        except ValueError:
            return
        if links != self._jira.links():
            self._jira.set_links(links)

    def _on_links_changed(self, links):
        author = self._author
        self._store.set_links(author, links)
        self._jira.set_status("Saving to Box…")

        def work():
            try:
                ok, err = self._store.push_links(author)
                self._links_pushed.emit(author, ok, err)
            except RuntimeError:
                pass

        threading.Thread(target=work, daemon=True, name="LinksPush").start()

    def _on_links_pushed(self, author, ok, err):
        if author != self._author or not hasattr(self, "_jira"):
            return
        self._jira.set_status("Saved to Box ✓" if ok else "Could not reach Box - kept on this PC, sent on the next visit",
                              "ok" if ok else "warn")

    def _on_tweet_pushed(self, author, ok, err):
        if author != self._author or not hasattr(self, "_tweet"):
            return
        if ok:
            self._tweet.set_status("Saved to Box ✓ - everyone who opens your profile sees it", "ok")
        else:
            self._tweet.set_status("Could not reach Box - kept on this PC, it is sent on the next visit", "warn")

    def _on_app(self, folder):
        if self._open_app:
            self._open_app(folder)
