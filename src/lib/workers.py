"""
Workers - Background worker classes for async operations
"""

import os
import sys
import shutil
import subprocess
import zipfile
import time
from pathlib import Path


def _lp(path):
    """Return a Windows long-path-safe string (\\?\ prefix) to bypass MAX_PATH (260 chars)."""
    if sys.platform != 'win32':
        return str(path)
    abs_path = os.path.abspath(str(path))
    if abs_path.startswith('\\\\'):
        return abs_path
    return '\\\\?\\' + abs_path
from concurrent.futures import ThreadPoolExecutor, as_completed
from PySide6.QtCore import QObject, Signal
from .boxlink_api import BoxLinkAPI


class WorkerSignals(QObject):
    """Signals for worker threads"""
    finished = Signal(object)  # Emits result data
    error = Signal(str)  # Emits error message


class RefreshWorker(QObject):
    """Worker for refresh operation"""
    finished = Signal(object)
    
    def __init__(self, api_func, config_path, record_file):
        super().__init__()
        self.api_func = api_func
        self.config_path = config_path
        self.record_file = record_file
    
    def _get_folder_contents_recursive(self, folder_id, depth=0, max_depth=3):
        """
        Recursively get folder contents with parallel sub-folder scanning.
        Each call creates its own BoxLinkAPI instance to be thread-safe.
        """
        if depth >= max_depth:
            return None
        
        try:
            api = BoxLinkAPI()
            success, data, error = api.list_folder_dict(folder_id)
            if not success or not data:
                return None
            
            # Collect sub-folders that need further scanning
            if 'items' in data:
                sub_folders = [
                    item for item in data['items']
                    if item.get('type') == 'folder' and item.get('id')
                ]
                
                if sub_folders:
                    # Fetch all sub-folder contents in parallel
                    max_workers = min(len(sub_folders), 10)
                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        future_to_item = {
                            executor.submit(
                                self._get_folder_contents_recursive,
                                item['id'], depth + 1, max_depth
                            ): item
                            for item in sub_folders
                        }
                        for future, item in future_to_item.items():
                            try:
                                nested_contents = future.result()
                                if nested_contents:
                                    item['contents'] = nested_contents
                            except Exception:
                                pass
            
            return data
        except Exception:
            return None
    
    def run(self):
        """Run the refresh operation with parallel recursive folder listing"""
        try:
            api = BoxLinkAPI()
            
            # Get top-level folder info
            success, data, error = api.get_info_default_dict()
            
            if not success:
                self.finished.emit((False, None, error))
                return
            
            # Fetch all top-level folder contents in parallel
            if data and 'items' in data:
                top_level_folders = [
                    item for item in data['items']
                    if item.get('type') == 'folder' and item.get('id')
                ]
                
                if top_level_folders:
                    max_workers = min(len(top_level_folders), 10)
                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        future_to_item = {
                            executor.submit(
                                self._get_folder_contents_recursive, item['id']
                            ): item
                            for item in top_level_folders
                        }
                        for future, item in future_to_item.items():
                            try:
                                contents = future.result()
                                if contents:
                                    item['contents'] = contents
                            except Exception:
                                pass
            
            self.finished.emit((success, data, error))
        except Exception as e:
            self.finished.emit((False, None, str(e)))


class SingleCardRefreshWorker(RefreshWorker):
    """Re-scan Box for a single software folder and return its fresh item data."""
    progress = Signal(str)

    def __init__(self, folder_id, folder_name, config_path, record_file):
        super().__init__(None, config_path, record_file)
        self.target_folder_id = folder_id
        self.target_folder_name = folder_name

    def run(self):
        try:
            self.progress.emit(f"Scanning Box for {self.target_folder_name}...")
            contents = self._get_folder_contents_recursive(self.target_folder_id)
            if not contents:
                self.finished.emit((False, None, f"Could not scan '{self.target_folder_name}' from Box"))
                return
            item = {
                'type': 'folder',
                'name': self.target_folder_name,
                'id': self.target_folder_id,
                'contents': contents,
            }
            self.finished.emit((True, item, None))
        except Exception as e:
            self.finished.emit((False, None, str(e)))


class CheckWorker(QObject):
    """Worker for version check operation"""
    finished = Signal(object)
    
    def __init__(self, folder_name):
        super().__init__()
        self.folder_name = folder_name
    
    def run(self):
        """Run the version check"""
        from .folder_parser import check_version_is_latest
        try:
            success, is_latest, message, latest_version = check_version_is_latest(self.folder_name)
            self.finished.emit((success, is_latest, message, latest_version))
        except Exception as e:
            self.finished.emit((False, None, str(e), None))


class DownloadInstallWorker(QObject):
    """Worker for downloading and installing software from store"""
    finished = Signal(bool, str)  # (success, message)
    progress = Signal(str)  # Progress message

    def __init__(self, software_name, author_name, version, file_id, software_path):
        super().__init__()
        self.software_name = software_name
        self.author_name = author_name
        self.version = version
        self.file_id = file_id
        self.software_path = Path(software_path)

    def _get_execution_file(self, target_folder):
        """Return the execution target Path for the shortcut.

        Priority:
        1. [Execution] file= entry in App_Store/<name>-<author>/Flow.txt
        2. run.cmd in the extracted folder
        3. setup.cmd in the extracted folder
        Returns None if nothing suitable is found.
        """
        # Try Flow.txt in App_Store first
        app_store_path = self.software_path.parent / "App_Store"
        flow_txt = app_store_path / f"{self.software_name}-{self.author_name}" / "Flow.txt"

        if flow_txt.exists():
            try:
                current_section = None
                with open(flow_txt, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith('[') and line.endswith(']'):
                            current_section = line[1:-1].lower()
                            continue
                        if current_section == 'execution' and '=' in line:
                            key, _, value = line.partition('=')
                            if key.strip().lower() == 'file':
                                candidate = target_folder / value.strip()
                                if candidate.exists():
                                    return candidate
            except Exception as e:
                print(f"Warning: could not parse Flow.txt for shortcut target: {e}")

        # Fallbacks
        for name in ('run.cmd', 'setup.cmd'):
            candidate = target_folder / name
            if candidate.exists():
                return candidate

        return None

    def _create_shortcut(self, target_folder):
        """Create a .lnk shortcut in target_folder if one does not already exist.

        Reads the execution target from Flow.txt ([Execution] file=), falls back
        to run.cmd / setup.cmd.  Uses the first .ico file found as the icon.
        Skips creation silently if no execution file is found.
        """
        # Skip if a shortcut already exists (preserved from a previous install)
        existing_lnk = list(target_folder.glob("*.lnk"))
        if existing_lnk:
            return

        exec_file = self._get_execution_file(target_folder)
        if exec_file is None:
            self.progress.emit(
                f"  ⚠ No execution file found for {self.software_name} — shortcut not created"
            )
            return

        try:
            from win32com.client import Dispatch

            # Use the first .ico file in the folder as the icon
            ico_files = list(target_folder.glob("*.ico"))
            icon_location = str(ico_files[0]) if ico_files else str(exec_file)

            shortcut_path = target_folder / f"{target_folder.name}.lnk"

            shell = Dispatch("WScript.Shell")
            shortcut = shell.CreateShortcut(str(shortcut_path))
            shortcut.TargetPath = str(exec_file)
            shortcut.IconLocation = icon_location
            shortcut.WorkingDirectory = str(target_folder)
            shortcut.Save()

            self.progress.emit(
                f"  ✓ Created shortcut → {exec_file.name}"
            )
        except ImportError:
            self.progress.emit(
                "  ⚠ pywin32 not available — shortcut not created"
            )
        except Exception as e:
            self.progress.emit(
                f"  ⚠ Failed to create shortcut: {e}"
            )
    
    def _copy_icon(self, target_folder):
        """Copy the icon from App_Store into target_folder based on Flow.txt [Icon].

        Reads [Icon] Flag= and Name= from App_Store/<name>-<author>/Flow.txt.
        Skips silently if Flag=False, no Flow.txt, or icon file missing in App_Store.
        """
        app_store_dir = self.software_path.parent / "App_Store" / \
            f"{self.software_name}-{self.author_name}"
        flow_txt = app_store_dir / "Flow.txt"

        if not flow_txt.exists():
            return

        icon_flag = False
        icon_name = None
        current_section = None

        try:
            with open(flow_txt, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('[') and line.endswith(']'):
                        current_section = line[1:-1].lower()
                        continue
                    if current_section == 'icon' and '=' in line:
                        key, _, value = line.partition('=')
                        key = key.strip().lower()
                        value = value.strip()
                        if key == 'flag':
                            icon_flag = value.lower() == 'true'
                        elif key == 'name':
                            icon_name = value
        except Exception as e:
            self.progress.emit(f"  ⚠ Could not read Flow.txt for icon: {e}")
            return

        if not icon_flag or not icon_name:
            return

        icon_src = app_store_dir / icon_name
        if not icon_src.exists():
            self.progress.emit(
                f"  ⚠ Icon '{icon_name}' not found in App_Store — skipping copy"
            )
            return

        dest = target_folder / icon_name
        try:
            shutil.copy2(str(icon_src), str(dest))
            self.progress.emit(f"  ✓ Copied icon {icon_name} to {target_folder.name}")
        except Exception as e:
            self.progress.emit(f"  ⚠ Failed to copy icon: {e}")

    def _get_installation_info(self):
        """Read [Installation] section from App_Store Flow.txt.

        Returns:
            (auto: bool, install_filename: str or None)
            auto is True only when Auto=True is explicitly set.
        """
        app_store_path = self.software_path.parent / "App_Store"
        flow_txt = app_store_path / f"{self.software_name}-{self.author_name}" / "Flow.txt"

        auto = False
        install_filename = None

        if not flow_txt.exists():
            return auto, install_filename

        try:
            current_section = None
            with open(flow_txt, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith('[') and line.endswith(']'):
                        current_section = line[1:-1].lower()
                        continue
                    if current_section == 'installation' and '=' in line:
                        key, _, value = line.partition('=')
                        key = key.strip().lower()
                        value = value.strip()
                        if key == 'auto':
                            auto = value.lower() == 'true'
                        elif key == 'file':
                            install_filename = value
        except Exception as e:
            print(f"Warning: could not parse Flow.txt [Installation] for {self.software_name}: {e}")

        return auto, install_filename

    def _run_installation(self, target_folder):
        """Run the installation script defined in Flow.txt [Installation] if Auto=True.

        Opens a visible terminal window so the user can watch the progress.
        Blocks until the script finishes before the caller continues to create
        the shortcut and refresh the dashboard.
        """
        auto, install_filename = self._get_installation_info()

        if not auto:
            return  # Auto install not requested — skip silently

        if not install_filename:
            self.progress.emit(
                "  ⚠ [Installation] Auto=True but no file= defined in Flow.txt — skipping"
            )
            return

        install_file = target_folder / install_filename

        if not install_file.exists():
            self.progress.emit(
                f"  ⚠ Installation file '{install_filename}' not found in {target_folder.name} — skipping"
            )
            return

        self.progress.emit(
            f"Running installation: {install_filename} "
            f"(terminal window opened — waiting for completion...)"
        )

        try:
            # CREATE_NEW_CONSOLE opens a visible cmd window so the user can
            # watch the installation output in real time.
            proc = subprocess.Popen(
                ['cmd', '/c', str(install_file)],
                cwd=str(target_folder),
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )

            # Block until the installer exits — shortcut creation and dashboard
            # refresh happen only after this completes.
            proc.wait()

            if proc.returncode == 0:
                self.progress.emit(
                    f"  ✓ Installation of {self.software_name} completed successfully"
                )
            else:
                self.progress.emit(
                    f"  ⚠ Installation of {self.software_name} "
                    f"exited with code {proc.returncode}"
                )

        except Exception as e:
            self.progress.emit(f"  ⚠ Failed to start installation: {e}")

    def run(self):
        """Download, extract, and install software"""
        try:
            # Initialize API
            self.progress.emit(f"Initializing download for {self.software_name} {self.version}...")
            api = BoxLinkAPI()
            
            # Create temp directory for download
            temp_dir = Path("temp_download")
            temp_dir.mkdir(exist_ok=True)
            
            # Download zip file
            self.progress.emit(f"Downloading {self.software_name} {self.version}...")
            
            # Download to temp_download folder
            # BoxAutomate will save the file with its original name from Box
            success, data, error = api.download_dict(self.file_id, str(temp_dir))
            
            if not success:
                self.finished.emit(False, f"Download failed: {error}")
                return
            
            # The file should be named v{num}.{num}.{num}.{num}.zip
            # Ensure version format is correct
            if not self.version.startswith('v'):
                zip_filename = f"v{self.version}.zip"
            else:
                zip_filename = f"{self.version}.zip"
            
            zip_path = temp_dir / zip_filename
            
            # Check if zip file exists
            if not zip_path.exists():
                # Try to find any .zip file in temp_download as fallback
                zip_files = list(temp_dir.glob("*.zip"))
                if zip_files:
                    zip_path = zip_files[0]  # Use the first zip file found
                else:
                    self.finished.emit(False, f"Downloaded file not found: {zip_filename}")
                    return
            
            # Find existing folder with same software name and author
            # Pattern: SoftwareName-V-*_A-AuthorName (note: hyphen before V)
            existing_folder = None
            
            for folder in self.software_path.iterdir():
                if folder.is_dir():
                    # Check if folder matches pattern: Name-V-version_A-Author
                    folder_parts = folder.name.split('-V-')
                    if len(folder_parts) == 2:
                        name_part = folder_parts[0]
                        version_author_part = folder_parts[1]
                        if name_part == self.software_name and f"_A-{self.author_name}" in version_author_part:
                            existing_folder = folder
                            break
            
            # Create new target folder name: Name-V-version_A-Author
            # Example: QuickMi2e-V-6.1.0.0_A-RoeyYee (hyphen before V)
            version_clean = self.version.replace('v', '')  # Remove 'v' prefix
            new_folder_name = f"{self.software_name}-V-{version_clean}_A-{self.author_name}"
            target_folder = self.software_path / new_folder_name
            
            # Extract zip file
            self.progress.emit(f"Extracting {self.software_name}...")
            
            # If existing folder found with different version, rename it
            if existing_folder and existing_folder != target_folder:
                self.progress.emit(f"Renaming existing folder from {existing_folder.name} to {new_folder_name}...")
                
                # If target folder already exists (shouldn't happen, but just in case), remove it
                if target_folder.exists():
                    shutil.rmtree(target_folder)
                
                # Rename existing folder to new version name
                existing_folder.rename(target_folder)
                
                # Remove all contents from the renamed folder (except protected env folders)
                self.progress.emit(f"Clearing old files...")
                protected_folders = {'myenv', 'venv', '.venv', 'env', '.env', 'node_modules', '.git'}

                for item in target_folder.iterdir():
                    if item.name.lower() in protected_folders:
                        continue
                    if item.is_file():
                        item.unlink()
                    elif item.is_dir():
                        shutil.rmtree(item)
            
            # If target folder doesn't exist yet, create it
            if not target_folder.exists():
                target_folder.mkdir(parents=True, exist_ok=True)
            
            # Extract zip contents to target folder
            self.progress.emit(f"Installing {self.software_name} {self.version}...")
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                # Check if ZIP has a root folder (like v6.1.0.0/)
                zip_contents = zip_ref.namelist()

                # Check if all files are in a single root folder
                root_folders = set()
                for name in zip_contents:
                    parts = name.split('/')
                    if len(parts) > 1:
                        root_folders.add(parts[0])

                # If all files are in a single root folder, extract and flatten
                if len(root_folders) == 1:
                    root_folder = list(root_folders)[0]
                    self.progress.emit(f"Extracting files from {root_folder}...")

                    for member in zip_ref.namelist():
                        # Skip the root folder itself
                        if member == root_folder or member == root_folder + '/':
                            continue

                        # Remove root folder from path
                        if member.startswith(root_folder + '/'):
                            target_path = member[len(root_folder) + 1:]

                            # Skip empty paths
                            if not target_path:
                                continue

                            target_file = target_folder / target_path

                            # If this is a directory entry (ends with /), create directory
                            if member.endswith('/'):
                                os.makedirs(_lp(target_file), exist_ok=True)
                            else:
                                # Create parent directories if needed
                                os.makedirs(_lp(target_file.parent), exist_ok=True)

                                # Remove existing file/folder if it exists (overwrite)
                                lp_target = _lp(target_file)
                                if os.path.exists(lp_target):
                                    if os.path.isdir(lp_target):
                                        shutil.rmtree(lp_target)
                                    else:
                                        try:
                                            os.chmod(lp_target, 0o777)
                                        except Exception:
                                            pass
                                        os.remove(lp_target)

                                # Write file with long-path support
                                with zip_ref.open(member) as source:
                                    with open(lp_target, 'wb') as f:
                                        f.write(source.read())
                else:
                    # No root folder or multiple root folders — extract with long-path support
                    for member in zip_ref.namelist():
                        target_file = target_folder / member
                        if member.endswith('/'):
                            os.makedirs(_lp(target_file), exist_ok=True)
                        else:
                            os.makedirs(_lp(target_file.parent), exist_ok=True)
                            with zip_ref.open(member) as source:
                                with open(_lp(target_file), 'wb') as f:
                                    f.write(source.read())
            
            # Clean up temp files
            self.progress.emit(f"Cleaning up...")
            
            # Wait a moment to ensure all file handles are released
            time.sleep(0.5)
            
            # Try to delete zip file with retry logic
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    if zip_path.exists():
                        zip_path.unlink()
                    break
                except PermissionError:
                    if attempt < max_retries - 1:
                        time.sleep(1)  # Wait and retry
                    else:
                        # If still can't delete, just leave it (not critical)
                        print(f"Warning: Could not delete temp file {zip_path}")
            
            # Clean up temp directory if empty
            try:
                if temp_dir.exists() and not list(temp_dir.iterdir()):
                    temp_dir.rmdir()
            except:
                pass  # Ignore if can't remove temp dir

            # Copy icon from App_Store into the installed folder
            self.progress.emit(f"Copying icon for {self.software_name}...")
            self._copy_icon(target_folder)

            # Run auto-installation script if defined in Flow.txt [Installation]
            self._run_installation(target_folder)

            self.finished.emit(True, f"Successfully installed {self.software_name} {self.version}")
            
        except zipfile.BadZipFile as e:
            self.finished.emit(False, f"Invalid zip file for {self.software_name}: {str(e)}")
        except PermissionError as e:
            self.finished.emit(False, f"Permission denied: {str(e)}")
        except FileExistsError as e:
            self.finished.emit(False, f"File already exists: {str(e)}")
        except Exception as e:
            import traceback
            error_detail = traceback.format_exc()
            print(f"Installation error:\n{error_detail}")
            self.finished.emit(False, f"Installation failed: {str(e)}")


class DeleteWorker(QObject):
    """Delete a software folder in a background thread.

    Signals
    -------
    finished(success: bool, message: str)
        Emitted when deletion completes.  ``success`` is False on any error.
    progress(message: str)
        Emitted with intermediate status messages during deletion.
    """

    finished = Signal(bool, str)
    progress = Signal(str)

    def __init__(self, folder_path: str):
        super().__init__()
        self.folder_path = Path(folder_path)

    # ------------------------------------------------------------------
    def run(self):
        import stat

        folder = self.folder_path
        self.progress.emit(f"Scanning '{folder.name}' for deletion…")
        print(f"\n[DELETE] Starting background deletion: {folder}")

        if not folder.exists():
            self.finished.emit(False, f"Folder not found:\n{folder}")
            return

        def _force_remove(func, path, _exc_info):
            """Strip read-only flag then retry — handles .NET published files."""
            try:
                os.chmod(path, stat.S_IWRITE)
                func(path)
                print(f"[DELETE] Force-removed read-only: {path}")
            except Exception as inner:
                print(f"[DELETE] Still failed after chmod: {path} → {inner}")

        def _force_delete_windows(target: Path):
            """Last-resort: delegate to Windows 'rd /s /q' which bypasses most locks."""
            import subprocess, time
            print(f"[DELETE] Falling back to rd /s /q: {target}")
            subprocess.run(
                ["cmd", "/c", "rd", "/s", "/q", str(target)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(0.5)

        try:
            self.progress.emit(f"Deleting '{folder.name}'…")
            shutil.rmtree(str(folder), onerror=_force_remove)
            print(f"[DELETE] rmtree complete. Folder exists after: {folder.exists()}")

            # If rmtree left locked files behind, escalate to Windows rd /s /q
            if folder.exists():
                self.progress.emit(f"Some files locked — forcing deletion…")
                _force_delete_windows(folder)

            if folder.exists():
                raise OSError(
                    f"Folder still exists after forced deletion — files may be in use by another process."
                )

            self.finished.emit(True, folder.name)

        except PermissionError as e:
            print(f"[DELETE] PermissionError: {e}")
            self.finished.emit(
                False,
                f"Could not delete '{folder.name}':\n{e}\n\n"
                f"Make sure the software is not currently running.",
            )
        except Exception as e:
            print(f"[DELETE] Error: {e}")
            self.finished.emit(False, f"Could not delete '{folder.name}':\n{e}")
