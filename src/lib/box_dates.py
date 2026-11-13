"""
box_dates - who uploaded each Box file and when, kept for the Dev Profile page.

Every Box folder listing can carry created_at / modified_at / created_by / modified_by for its files (BoxFast: "dates": true).
absorb() takes those fields OFF the listing before anyone else sees it, so record.json and the per-app metadata JSON keep
exactly the structure they always had, and keeps them in App_Store/.box-dates.json instead:

    {"<box file id>": {"name": "v4.0.0.0.zip", "created": "2026-11-13T10:04:24+08:00",
                       "modified": "2026-11-13T10:04:24+08:00", "by": "Roey Yee Ong"}}

App_Store/ is not part of the repository and an update never touches it, so the file adds nothing to the commit. It is filled
by the normal Refresh (full, selected, new apps, or the per-card button); nothing extra has to be run.
"""

import json
import os
import re
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Optional

_FILE = Path(__file__).resolve().parent.parent.parent / "App_Store" / ".box-dates.json"
_DATE_KEYS = ("created_at", "modified_at", "created_by", "modified_by")
_lock = threading.Lock()
_cache: Optional[Dict[str, dict]] = None


def _loaded() -> Dict[str, dict]:
    global _cache
    if _cache is None:
        try:
            data = json.loads(_FILE.read_text(encoding="utf-8"))
            _cache = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _trim(stamp: Optional[str]) -> Optional[str]:
    """Box sends 7 fractional digits ("...:24.0000000+08:00"); the fraction is not needed."""
    return re.sub(r"\.\d+", "", stamp) if stamp else None


def absorb(listing: Optional[dict]) -> None:
    """Move the date fields of every file in a folder listing into the side file (and out of the listing)."""
    if not isinstance(listing, dict):
        return
    changed = False
    with _lock:
        known = _loaded()
        for item in listing.get("items") or []:
            if not isinstance(item, dict):
                continue
            fields = {k: item.pop(k) for k in _DATE_KEYS if k in item}      # always taken off, whatever happens next
            if item.get("type") != "file" or not item.get("id") or not fields:
                continue
            entry = {"name": item.get("name", ""), "created": _trim(fields.get("created_at")),
                     "modified": _trim(fields.get("modified_at")),
                     "by": fields.get("modified_by") or fields.get("created_by") or ""}
            if known.get(str(item["id"])) != entry:
                known[str(item["id"])] = entry
                changed = True
        if changed:
            try:
                _FILE.parent.mkdir(exist_ok=True)
                tmp = _FILE.with_name(_FILE.name + ".tmp")
                tmp.write_text(json.dumps(known, indent=1, ensure_ascii=False), encoding="utf-8")
                os.replace(tmp, _FILE)            # a reader (the Dev Profile page) sees the old or the new file, never half
            except OSError:
                pass


_read_memo = {"stamp": None, "data": {}}


def stamp() -> tuple:
    """Changes whenever the saved dates change (used to remember what was computed from them)."""
    try:
        st = _FILE.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return (0, 0)


def lookup() -> Dict[str, dict]:
    """Everything saved so far. The file is parsed again only when it changed on disk."""
    now = stamp()
    if _read_memo["stamp"] == now:
        return _read_memo["data"]
    try:
        data = json.loads(_FILE.read_text(encoding="utf-8"))
        data = data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return _read_memo["data"] if _read_memo["stamp"] is not None else {}      # keep the last good copy
    _read_memo["stamp"], _read_memo["data"] = now, data
    return data


def local_day(stamp: Optional[str]) -> Optional[date]:
    """The calendar day of a stored timestamp in this PC's time zone."""
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp).astimezone().date()
    except ValueError:
        return None
