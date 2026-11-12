"""
Desktop shortcut that opens the app WITHOUT a console flash.

The shortcut that env-Init.cmd / shortcut.py created pointed at run&install.cmd: double-clicking it starts cmd.exe, which
shows a black console window for a moment before the GUI appears. The shortcut should start pythonw.exe (no console at
all) with src\\main.py instead. New installs get that from shortcut.py; this module repairs the shortcut that older
installs already have (checked at every start, a no-op once it is right).

Only the shortcut of THIS install is touched: DinoSaurList.lnk on the desktop whose working folder is this app folder
(the WPF version's DinoSaurList-WPF.lnk and anything else is left alone).
"""

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent.parent
SHORTCUT_NAME = "DinoSaurList.lnk"


def _same_folder(a: str, b: Path) -> bool:
    try:
        return Path(a).resolve() == b.resolve()
    except OSError:
        return False


def fix_desktop_shortcut(desktop=None, app_root=None) -> str:
    """Returns what was done: "" (nothing to do / not possible) or "fixed". (*desktop*/*app_root*: for tests.)"""
    app_root = Path(app_root) if app_root else APP_ROOT
    if sys.platform != "win32":
        return ""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    main = app_root / "src" / "main.py"
    if not pythonw.exists() or not main.exists():
        return ""
    try:
        from win32com.client import Dispatch
        shell = Dispatch("WScript.Shell")
        lnk_path = Path(desktop or shell.SpecialFolders("Desktop")) / SHORTCUT_NAME
        if not lnk_path.exists():
            return ""
        lnk = shell.CreateShortcut(str(lnk_path))
        if not _same_folder(lnk.WorkingDirectory or "", app_root):
            return ""                                          # somebody else's / another install's shortcut
        if Path(lnk.TargetPath).name.lower() == "pythonw.exe":
            return ""                                          # already right
        lnk.TargetPath = str(pythonw)
        lnk.Arguments = f'"{main}"'
        lnk.WorkingDirectory = str(app_root)
        lnk.WindowStyle = 1
        lnk.Save()
        return "fixed"
    except Exception:
        return ""
