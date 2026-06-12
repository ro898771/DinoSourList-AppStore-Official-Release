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

    def __init__(self, record_data, app_store_path):
        super().__init__()
        self.record_data = record_data
        self.app_store_path = Path(app_store_path)

    def _download_file(self, task):
        """Download a single file from Box. Used by ThreadPoolExecutor."""
        file_id, dest_path, file_name, folder_name = task
        try:
            api = BoxLinkAPI()
            success, download_data, error = api.download_dict(file_id, dest_path)
            ok = success and download_data and download_data.get('downloaded')
            return ok, file_name, folder_name, error
        except Exception as exc:
            return False, file_name, folder_name, str(exc)

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
                flow_file_id = None

                for item in items:
                    if item.get('type') == 'file':
                        file_name = item.get('name', '')
                        file_id = item.get('id', '')
                        if file_name and file_id:
                            files_metadata.append({'name': file_name, 'id': file_id})
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
            flow_tasks = [
                (info['flow_file_id'], str(info['folder_path']), 'Flow.txt', name)
                for name, info in software_info.items()
                if info['flow_file_id']
            ]

            if flow_tasks:
                self.progress.emit(f"Downloading {len(flow_tasks)} Flow.txt file(s)...")
                max_workers = min(len(flow_tasks), 6)
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = [executor.submit(self._download_file, t) for t in flow_tasks]
                    for future in as_completed(futures):
                        try:
                            ok, file_name, folder_name, error = future.result()
                            if ok:
                                self.progress.emit(f"  ✓ Downloaded Flow.txt for {folder_name}")
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
                        asset_tasks.append((file_id, str(folder_path), 'icon.ico', parent_name))
                    else:
                        self.progress.emit(
                            f"  ⚠ No Flow.txt and no icon.ico for {parent_name} — skipping"
                        )
                    continue

                icon_filename, guide_filename, guide_changed, readme_filename = \
                    self._parse_flow_txt(flow_path)

                # Queue icon — always re-download
                if icon_filename:
                    key = icon_filename.lower()
                    if key in files_by_name:
                        _, file_id = files_by_name[key]
                        asset_tasks.append((file_id, str(folder_path), icon_filename, parent_name))
                    else:
                        self.progress.emit(
                            f"  ⚠ {icon_filename} not found in Box for {parent_name}"
                        )

                # Queue guide — respect Changed= flag
                if guide_filename:
                    key = guide_filename.lower()
                    if key in files_by_name:
                        _, file_id = files_by_name[key]
                        dest = folder_path / guide_filename
                        if guide_changed:
                            asset_tasks.append((file_id, str(folder_path), guide_filename, parent_name))
                        elif dest.exists():
                            self.progress.emit(
                                f"  ⊘ {guide_filename} unchanged (Changed=False) — skipping"
                            )
                        else:
                            asset_tasks.append((file_id, str(folder_path), guide_filename, parent_name))
                    else:
                        self.progress.emit(
                            f"  ⚠ {guide_filename} not found in Box for {parent_name}"
                        )

                # Queue readme — always re-download when Flag=True
                if readme_filename:
                    key = readme_filename.lower()
                    if key in files_by_name:
                        _, file_id = files_by_name[key]
                        asset_tasks.append((file_id, str(folder_path), readme_filename, parent_name))
                    else:
                        self.progress.emit(
                            f"  ⚠ {readme_filename} not found in Box for {parent_name}"
                        )
                else:
                    # Safety fallback: if Flow.txt has no [ReadMe] section but
                    # README.md exists in Box, download it automatically so the
                    # AI assistant can index it.
                    if 'readme.md' in files_by_name:
                        _, file_id = files_by_name['readme.md']
                        asset_tasks.append((file_id, str(folder_path), 'README.md', parent_name))

            # ------------------------------------------------------------------
            # Pass 4: Download icon/guide files in parallel
            # ------------------------------------------------------------------
            if asset_tasks:
                self.progress.emit(f"Downloading {len(asset_tasks)} asset file(s) in parallel...")
                max_workers = min(len(asset_tasks), 6)
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = [executor.submit(self._download_file, t) for t in asset_tasks]
                    for future in as_completed(futures):
                        try:
                            ok, file_name, folder_name, error = future.result()
                            if ok:
                                self.progress.emit(f"  ✓ Downloaded {file_name} for {folder_name}")
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
            flow_file_id = None

            for item in items:
                if item.get('type') == 'file':
                    file_name = item.get('name', '')
                    file_id = item.get('id', '')
                    if file_name and file_id:
                        files_metadata.append({'name': file_name, 'id': file_id})
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

            # ── Pass 2: Re-download Flow.txt (always fresh) ───────────────────
            if flow_file_id:
                self.progress.emit(f"Downloading Flow.txt for {parent_name}…")
                ok, _, _, error = self._download_file(
                    (flow_file_id, str(parent_folder_path), 'Flow.txt', parent_name)
                )
                if ok:
                    self.progress.emit("✓ Flow.txt updated")
                    flow_downloaded += 1
                else:
                    self.progress.emit(f"⚠ Flow.txt failed: {error}")

            # ── Pass 3: Parse Flow.txt for icon, guide AND readme ─────────────
            flow_path = parent_folder_path / 'Flow.txt'
            icon_filename   = None
            guide_filename  = None
            readme_filename = None
            guide_changed   = True
            if flow_path.exists():
                icon_filename, guide_filename, guide_changed, readme_filename = \
                    self._parse_flow_txt(flow_path)

            # Fallback: icon.ico
            if not icon_filename and 'icon.ico' in files_by_name:
                icon_filename = 'icon.ico'

            # ── Pass 4: Download assets — icon always, guide + readme with rules ─
            asset_tasks = []

            # Icon: always re-download
            if icon_filename:
                key = icon_filename.lower()
                if key in files_by_name:
                    _, file_id = files_by_name[key]
                    asset_tasks.append((file_id, str(parent_folder_path), icon_filename, parent_name))
                else:
                    self.progress.emit(f"⚠ {icon_filename} not found in Box for {parent_name}")

            # Guide: skip entirely when skip_guide=True, else respect Changed= flag
            if guide_filename and self.skip_guide:
                self.progress.emit(f"⊘ {guide_filename} — guide skipped for Dashboard refresh")
            elif guide_filename:
                key = guide_filename.lower()
                if key in files_by_name:
                    _, file_id = files_by_name[key]
                    dest = parent_folder_path / guide_filename
                    if guide_changed:
                        asset_tasks.append((file_id, str(parent_folder_path), guide_filename, parent_name))
                    elif dest.exists():
                        self.progress.emit(f"⊘ {guide_filename} unchanged (Changed=False) — skipping")
                    else:
                        asset_tasks.append((file_id, str(parent_folder_path), guide_filename, parent_name))
                else:
                    self.progress.emit(f"⚠ {guide_filename} not found in Box for {parent_name}")

            # ReadMe: always re-download when Flag=True (both Page 1 and Page 2)
            if readme_filename:
                key = readme_filename.lower()
                if key in files_by_name:
                    _, file_id = files_by_name[key]
                    asset_tasks.append((file_id, str(parent_folder_path), readme_filename, parent_name))
                else:
                    self.progress.emit(f"⚠ {readme_filename} not found in Box for {parent_name}")

            # Download icon and guide in parallel (up to 2 workers)
            if asset_tasks:
                from concurrent.futures import ThreadPoolExecutor, as_completed
                self.progress.emit(f"Downloading {len(asset_tasks)} asset(s) for {parent_name}…")
                with ThreadPoolExecutor(max_workers=len(asset_tasks)) as executor:
                    futures = [executor.submit(self._download_file, t) for t in asset_tasks]
                    for future in as_completed(futures):
                        try:
                            ok, file_name, folder_name, error = future.result()
                            if ok:
                                self.progress.emit(f"✓ {file_name} downloaded")
                                if file_name.lower().endswith('.ico'):
                                    icon_downloaded += 1
                            else:
                                self.progress.emit(f"⚠ {file_name} failed: {error}")
                        except Exception:
                            pass

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
