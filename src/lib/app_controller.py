"""
App Store Controller - Handles App_Store metadata and icon downloads
"""

import json
import shutil
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from PySide6.QtCore import QObject, Signal
from .boxlink_api import BoxLinkAPI


class AppStoreDownloadWorker(QObject):
    """Worker for creating metadata JSON files and downloading icons in App_Store"""
    finished = Signal(object)
    progress = Signal(str)
    percent = Signal(float)      # 0..1 overall progress of a full sync (drives the Refresh dashboard's bar)

    def __init__(self, record_data, app_store_path, skip_readme_and_guide=False):
        super().__init__()
        self.record_data = record_data
        self.app_store_path = Path(app_store_path)
        # True -> Refresh Setting's "icon + Flow.txt + metadata only" mode:
        # never queue a guide or readme, and never fall back to a bare
        # README.md either (see the Pass 3 fallback below).
        self.skip_readme_and_guide = skip_readme_and_guide

    @staticmethod
    def _pool_size(task_count, legacy_cap=6):
        """Thread-pool width for Box downloads. BoxFast serves many requests at once over one login, so go wide;
        the BoxAutomate.exe fallback starts a process per call and stays at its old cap."""
        wide = 32 if BoxLinkAPI.fast_available() else legacy_cap
        return max(1, min(task_count, wide))

    def _download_file(self, task):
        """Download a single small file from Box (icon, Flow.txt, ...). Used by ThreadPoolExecutor.

        task = (file_id, dest_dir, file_name, folder_name[, box_sha1]). When box_sha1 matches the file already on
        disk nothing is downloaded. Returns (ok, file_name, folder_name, error, unchanged).
        """
        file_id, dest_path, file_name, folder_name = task[:4]
        sha1 = task[4] if len(task) > 4 else None
        try:
            api = BoxLinkAPI()
            success, download_data, error = api.download_small_dict(file_id, dest_path, file_name, sha1)
            ok = bool(success and download_data and download_data.get('downloaded'))
            unchanged = bool(ok and download_data.get('unchanged'))
            return ok, file_name, folder_name, error, unchanged
        except Exception as exc:
            return False, file_name, folder_name, str(exc), False

    def _parse_flow_txt(self, flow_path):
        """Parse Flow.txt to extract icon, guide, and readme info.

        Reads:
          [Icon]   Flag=  Name=
          [Guide]  file=  Changed=
          [ReadMe] Flag=  file=

        [Guide] Changed= behaviour:
          True (or key absent) → always re-download guide from Box
          False                → skip re-download if file already exists locally;
                                 still download on first run (file missing)

        [ReadMe] Flag= behaviour:
          True  → pull the readme file into App_Store on every refresh
          False (or key absent) → do not download readme

        Returns:
            (icon_filename, guide_filename, guide_changed, readme_filename)
            - icon_filename  : str or None
            - guide_filename : str or None
            - guide_changed  : bool  (default True)
            - readme_filename: str or None
        """
        icon_filename   = None
        guide_filename  = None
        readme_filename = None
        icon_flag    = True
        readme_flag  = False
        guide_changed = True
        current_section = None

        try:
            with open(flow_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    if line.startswith('[') and line.endswith(']'):
                        current_section = line[1:-1].lower()
                        continue

                    if '=' in line:
                        key, _, value = line.partition('=')
                        key   = key.strip().lower()
                        value = value.strip()

                        if current_section == 'icon':
                            if key == 'flag':
                                icon_flag = value.lower() == 'true'
                            elif key == 'name':
                                icon_filename = value

                        elif current_section == 'guide':
                            if key == 'file':
                                guide_filename = value
                            elif key == 'changed':
                                guide_changed = value.lower() == 'true'

                        elif current_section == 'readme':
                            if key == 'flag':
                                readme_flag = value.lower() == 'true'
                            elif key == 'file':
                                readme_filename = value

        except Exception as e:
            print(f"Error parsing Flow.txt at {flow_path}: {e}")

        if not icon_flag:
            icon_filename = None
        if not readme_flag:
            readme_filename = None

        return icon_filename, guide_filename, guide_changed, readme_filename

    def run(self):
        """Download assets in 4 passes:
        1. Write JSON metadata and collect all Box file IDs per software
        2. Download Flow.txt first (always fresh) for every software in parallel
        3. Parse each Flow.txt to determine which icon/guide filenames to pull
        4. Download the icon/guide files in parallel (skip if already present)
        """
        try:
            created_count = 0
            skipped_count = 0
            failed_count = 0
            flow_downloaded = 0
            icon_downloaded = 0
            guide_downloaded = 0

            if not self.record_data or 'items' not in self.record_data:
                self.finished.emit((False, "No data to process", 0, 0, 0))
                return

            # ------------------------------------------------------------------
            # Pass 1: Write JSON metadata and build per-software file index
            # ------------------------------------------------------------------
            # software_info[parent_name] = {
            #     'folder_path': Path,
            #     'files_by_name': {lowercase_filename: (original_name, file_id)},
            #     'flow_file_id': str or None
            # }
            software_info = {}
            self.percent.emit(0.01)

            for parent_item in self.record_data['items']:
                if parent_item.get('type') != 'folder':
                    continue

                parent_name = parent_item.get('name', 'Unknown')
                parent_folder_path = self.app_store_path / parent_name
                parent_folder_path.mkdir(parents=True, exist_ok=True)

                contents = parent_item.get('contents', {})
                items = contents.get('items', [])

                json_filename = f"{parent_name}.json"
                json_filepath = parent_folder_path / json_filename

                files_metadata = []
                files_by_name = {}
                sha_by_name = {}
                flow_file_id = None

                for item in items:
                    if item.get('type') == 'file':
                        file_name = item.get('name', '')
                        file_id = item.get('id', '')
                        if file_name and file_id:
                            entry = {'name': file_name, 'id': file_id}
                            if item.get('sha1'):
                                entry['sha1'] = item['sha1']   # Box content hash: lets refresh skip unchanged files
                                sha_by_name[file_name.lower()] = item['sha1']
                            files_metadata.append(entry)
                            files_by_name[file_name.lower()] = (file_name, file_id)
                            if file_name.lower() == 'flow.txt':
                                flow_file_id = file_id

                if files_metadata:
                    try:
                        metadata_json = {
                            'folder_name': parent_name,
                            'folder_id': parent_item.get('id', ''),
                            'total_files': len(files_metadata),
                            'files': files_metadata
                        }
                        with open(json_filepath, 'w', encoding='utf-8') as f:
                            json.dump(metadata_json, f, indent=2)
                        self.progress.emit(f"  ✓ Created {json_filename} with {len(files_metadata)} files")
                        created_count += 1

                        software_info[parent_name] = {
                            'folder_path': parent_folder_path,
                            'files_by_name': files_by_name,
                            'sha_by_name': sha_by_name,
                            'flow_file_id': flow_file_id
                        }
                    except Exception as e:
                        self.progress.emit(f"  ✗ Failed to create {json_filename}: {str(e)}")
                        failed_count += 1
                else:
                    self.progress.emit(f"  ⊘ No files found in {parent_name}")
                    skipped_count += 1

            # ------------------------------------------------------------------
            # Pass 2: Download Flow.txt files in parallel (always re-download
            #         so icon/guide references are always up to date)
            # ------------------------------------------------------------------
            self.percent.emit(0.05)
            flow_tasks = [
                (info['flow_file_id'], str(info['folder_path']), 'Flow.txt', name,
                 info['sha_by_name'].get('flow.txt'))
                for name, info in software_info.items()
                if info['flow_file_id']
            ]

            if flow_tasks:
                self.progress.emit(f"Downloading {len(flow_tasks)} Flow.txt file(s)...")
                max_workers = self._pool_size(len(flow_tasks))
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = [executor.submit(self._download_file, t) for t in flow_tasks]
                    for done_count, future in enumerate(as_completed(futures), 1):
                        self.percent.emit(0.05 + 0.45 * done_count / len(flow_tasks))
                        try:
                            ok, file_name, folder_name, error, unchanged = future.result()
                            if ok:
                                self.progress.emit(
                                    f"  ✓ Flow.txt unchanged for {folder_name}" if unchanged
                                    else f"  ✓ Downloaded Flow.txt for {folder_name}"
                                )
                                flow_downloaded += 1
                            else:
                                self.progress.emit(
                                    f"  ⚠ Failed to download Flow.txt for {folder_name}: {error}"
                                )
                        except Exception:
                            pass

            # ------------------------------------------------------------------
            # Pass 3: Parse each Flow.txt to decide which icon/guide to pull
            # ------------------------------------------------------------------
            asset_tasks = []

            for parent_name, info in software_info.items():
                folder_path = info['folder_path']
                files_by_name = info['files_by_name']
                flow_path = folder_path / 'Flow.txt'

                if not flow_path.exists():
                    # No Flow.txt — fall back to icon.ico directly if on Box
                    if 'icon.ico' in files_by_name:
                        _, file_id = files_by_name['icon.ico']
                        asset_tasks.append((file_id, str(folder_path), 'icon.ico', parent_name,
                                            info['sha_by_name'].get('icon.ico')))
                    else:
                        self.progress.emit(
                            f"  ⚠ No Flow.txt and no icon.ico for {parent_name} — skipping"
                        )
                    continue

                icon_filename = self._parse_flow_txt(flow_path)[0]

                # README and the Details guide are NOT fetched during a refresh (guides can be tens of MB and are
                # rarely opened): they download the first time the user clicks them (see docs.py), and are
                # re-offered automatically when Box has a newer copy.
                if icon_filename:
                    key = icon_filename.lower()
                    if key in files_by_name:
                        _, file_id = files_by_name[key]
                        asset_tasks.append((file_id, str(folder_path), icon_filename, parent_name,
                                            info['sha_by_name'].get(key)))
                    else:
                        self.progress.emit(
                            f"  ⚠ {icon_filename} not found in Box for {parent_name}"
                        )

            # ------------------------------------------------------------------
            # Pass 4: Download icons in parallel (unchanged ones are skipped by SHA-1)
            # ------------------------------------------------------------------
            if asset_tasks:
                self.progress.emit(f"Downloading {len(asset_tasks)} asset file(s) in parallel...")
                max_workers = self._pool_size(len(asset_tasks))
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = [executor.submit(self._download_file, t) for t in asset_tasks]
                    for done_count, future in enumerate(as_completed(futures), 1):
                        self.percent.emit(0.5 + 0.45 * done_count / len(asset_tasks))
                        try:
                            ok, file_name, folder_name, error, unchanged = future.result()
                            if ok:
                                self.progress.emit(
                                    f"  ✓ {file_name} unchanged for {folder_name}" if unchanged
                                    else f"  ✓ Downloaded {file_name} for {folder_name}"
                                )
                                if file_name.lower().endswith('.ico'):
                                    icon_downloaded += 1
                                else:
                                    guide_downloaded += 1
                            else:
                                self.progress.emit(
                                    f"  ⚠ Failed to download {file_name} for {folder_name}: {error}"
                                )
                        except Exception:
                            pass

            # ------------------------------------------------------------------
            # Pass 5: Copy icon into every matching Software_Downloaded folder
            # so Page 1 cards can load it directly from there.
            # Matching rule: App_Store "QuickMi2e-RoeyYee"
            #   → Software_Downloaded "QuickMi2e-V-*_A-RoeyYee"
            # ------------------------------------------------------------------
            sw_downloaded_path = self.app_store_path.parent / "Software_Downloaded"
            if sw_downloaded_path.exists():
                self.progress.emit("Syncing icons to Software_Downloaded folders...")
                for parent_name, info in software_info.items():
                    flow_txt_path = info['folder_path'] / "Flow.txt"
                    if not flow_txt_path.exists():
                        continue

                    # Read [Icon] Flag and Name from Flow.txt
                    icon_flag = False
                    icon_name = None
                    current_section = None
                    try:
                        with open(flow_txt_path, 'r', encoding='utf-8') as f:
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
                    except Exception:
                        continue

                    if not icon_flag or not icon_name:
                        # Flow.txt absent or icon disabled — fall back to icon.ico if present
                        icon_src = info['folder_path'] / 'icon.ico'
                        if not icon_src.exists():
                            continue
                        icon_name = 'icon.ico'
                    else:
                        icon_src = info['folder_path'] / icon_name
                        if not icon_src.exists():
                            continue

                    # Split App_Store folder name into sw_name and author
                    # e.g. "QuickMi2e-RoeyYee" → sw_name="QuickMi2e", author="RoeyYee"
                    if '-' not in parent_name:
                        continue
                    sw_name_key, author_key = parent_name.rsplit('-', 1)

                    # Find matching Software_Downloaded folders
                    for sw_folder in sw_downloaded_path.iterdir():
                        if not sw_folder.is_dir():
                            continue
                        fname = sw_folder.name
                        if (
                            (fname.startswith(f"{sw_name_key}-V-") or
                             fname.startswith(f"{sw_name_key}_V-"))
                            and f"_A-{author_key}" in fname
                        ):
                            dest = sw_folder / icon_name
                            try:
                                shutil.copy2(str(icon_src), str(dest))
                                self.progress.emit(
                                    f"  ✓ Copied {icon_name} → {sw_folder.name}"
                                )
                            except Exception as e:
                                self.progress.emit(
                                    f"  ⚠ Could not copy icon to {sw_folder.name}: {e}"
                                )

            self.percent.emit(1.0)
            result_message = (
                f"JSON: {created_count}, "
                f"Flows: {flow_downloaded}, "
                f"Icons: {icon_downloaded}, "
                f"Guides: {guide_downloaded}"
            )
            self.finished.emit((True, result_message, created_count, failed_count, skipped_count))

        except Exception as e:
            self.finished.emit((False, str(e), 0, 0, 0))


class SingleCardDownloadWorker(AppStoreDownloadWorker):
    """Lean single-card refresh — skips guide download for speed.

    Runs a trimmed 5-pass pipeline for ONE software item only:
      Pass 1: Update JSON metadata
      Pass 2: Re-download Flow.txt (always fresh, small file)
      Pass 3: Parse Flow.txt for icon filename
      Pass 4: Download ICON only if not already cached (skip guide)
      Pass 5: Copy icon into matching Software_Downloaded folders

    Guide files (.pptx / .pdf) are intentionally skipped because they can be
    tens of MB and are rarely updated.  A full Refresh will pick them up.

    Accepts a single item dict in the same format that RefreshWorker emits:
        {
            'type': 'folder',
            'name': <folder_name>,
            'id':   <folder_id>,
            'contents': { 'items': [...] }
        }
    """

    def __init__(self, item, app_store_path, skip_guide=False):
        record_data = {'items': [item]}
        super().__init__(record_data, app_store_path)
        self.item = item
        self.skip_guide = skip_guide  # True → never download guide (Page 1 use-case)

    def run(self):
        """Lean pipeline: JSON + Flow.txt + icon (+ optional guide)."""
        try:
            created_count = 0
            failed_count = 0
            flow_downloaded = 0
            icon_downloaded = 0

            parent_item = self.item
            if parent_item.get('type') != 'folder':
                self.finished.emit((False, "Item is not a folder", 0, 0, 0))
                return

            parent_name = parent_item.get('name', 'Unknown')
            parent_folder_path = self.app_store_path / parent_name
            parent_folder_path.mkdir(parents=True, exist_ok=True)

            contents = parent_item.get('contents', {})
            items = contents.get('items', [])

            # ── Pass 1: Update JSON metadata ──────────────────────────────────
            json_filepath = parent_folder_path / f"{parent_name}.json"
            files_metadata = []
            files_by_name = {}
            sha_by_name = {}
            flow_file_id = None

            for item in items:
                if item.get('type') == 'file':
                    file_name = item.get('name', '')
                    file_id = item.get('id', '')
                    if file_name and file_id:
                        entry = {'name': file_name, 'id': file_id}
                        if item.get('sha1'):
                            entry['sha1'] = item['sha1']
                            sha_by_name[file_name.lower()] = item['sha1']
                        files_metadata.append(entry)
                        files_by_name[file_name.lower()] = (file_name, file_id)
                        if file_name.lower() == 'flow.txt':
                            flow_file_id = file_id

            if not files_metadata:
                self.finished.emit((False, f"No files found in {parent_name}", 0, 0, 0))
                return

            try:
                metadata_json = {
                    'folder_name': parent_name,
                    'folder_id': parent_item.get('id', ''),
                    'total_files': len(files_metadata),
                    'files': files_metadata,
                }
                with open(json_filepath, 'w', encoding='utf-8') as f:
                    json.dump(metadata_json, f, indent=2)
                self.progress.emit(f"✓ Updated {parent_name}.json")
                created_count += 1
            except Exception as e:
                self.finished.emit((False, f"Failed to write JSON: {e}", 0, 0, 0))
                return

            # ── Pass 2+3+4: Flow.txt and the icon, started at the SAME time ───
            # The icon's name lives in Flow.txt, but the Flow.txt already on disk almost always names the same
            # icon -- so fetch the icon while Flow.txt downloads instead of after it (saves a whole Box round
            # trip). If the new Flow.txt names a different icon, that one is fetched afterwards.
            # README / Details guide are not fetched here: they download on first click (see docs.py).
            flow_path = parent_folder_path / 'Flow.txt'

            def pick_icon():
                name = self._parse_flow_txt(flow_path)[0] if flow_path.exists() else None
                if not name and 'icon.ico' in files_by_name:
                    name = 'icon.ico'
                return name

            def icon_task(name):
                key = name.lower() if name else None
                if key and key in files_by_name:
                    _, file_id = files_by_name[key]
                    return (file_id, str(parent_folder_path), name, parent_name, sha_by_name.get(key))
                if name:
                    self.progress.emit(f"⚠ {name} not found in Box for {parent_name}")
                return None

            guess = pick_icon()
            self.progress.emit(f"Updating Flow.txt and icon for {parent_name}…")
            flow_future = icon_future = None
            with ThreadPoolExecutor(max_workers=2) as executor:
                if flow_file_id:
                    flow_future = executor.submit(
                        self._download_file,
                        (flow_file_id, str(parent_folder_path), 'Flow.txt', parent_name, sha_by_name.get('flow.txt'))
                    )
                first_icon = icon_task(guess)
                if first_icon:
                    icon_future = executor.submit(self._download_file, first_icon)

                if flow_future:
                    ok, _, _, error, unchanged = flow_future.result()
                    if ok:
                        self.progress.emit("✓ Flow.txt unchanged" if unchanged else "✓ Flow.txt updated")
                        flow_downloaded += 1
                    else:
                        self.progress.emit(f"⚠ Flow.txt failed: {error}")

                icon_filename = pick_icon()
                if icon_filename and icon_filename.lower() != (guess or '').lower():
                    # Flow.txt changed its icon: fetch the new one too
                    second_icon = icon_task(icon_filename)
                    icon_future = executor.submit(self._download_file, second_icon) if second_icon else icon_future

                if icon_future:
                    ok, file_name, _, error, unchanged = icon_future.result()
                    if ok:
                        self.progress.emit(f"✓ {file_name} unchanged" if unchanged else f"✓ {file_name} downloaded")
                        if file_name.lower().endswith('.ico'):
                            icon_downloaded += 1
                    else:
                        self.progress.emit(f"⚠ {file_name} failed: {error}")

            # ── Pass 5: Copy icon to matching Software_Downloaded folders ──────
            sw_downloaded_path = self.app_store_path.parent / "Software_Downloaded"
            if sw_downloaded_path.exists() and icon_filename and '-' in parent_name:
                icon_src = parent_folder_path / icon_filename
                if icon_src.exists():
                    sw_name_key, author_key = parent_name.rsplit('-', 1)
                    for sw_folder in sw_downloaded_path.iterdir():
                        if not sw_folder.is_dir():
                            continue
                        fname = sw_folder.name
                        if (
                            (fname.startswith(f"{sw_name_key}-V-") or
                             fname.startswith(f"{sw_name_key}_V-"))
                            and f"_A-{author_key}" in fname
                        ):
                            dest = sw_folder / icon_filename
                            try:
                                shutil.copy2(str(icon_src), str(dest))
                                self.progress.emit(f"✓ Icon synced → {sw_folder.name}")
                            except Exception as e:
                                self.progress.emit(f"⚠ Icon copy failed: {e}")

            result_message = (
                f"JSON: {created_count}, Flow: {flow_downloaded}, Icon: {icon_downloaded}"
            )
            self.finished.emit((True, result_message, created_count, failed_count, 0))

        except Exception as e:
            self.finished.emit((False, str(e), 0, 0, 0))
