"""
SoftwareCard - Modern Bootstrap-style card for displaying software
"""

import json
import re
from pathlib import Path
from PySide6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QLabel, QWidget, QComboBox, QPushButton
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QIcon

from .clickable_label import ClickableLabel
from .folder_parser import parse_software_folder_name, format_software_name, format_version, format_author
from .styles import (
    CARD_STYLE, CARD_ICON_STYLE, CARD_ICON_FALLBACK_STYLE,
    CARD_INFO_STYLE, VERSION_LATEST_CONFIG, VERSION_OUTDATED_CONFIG,
    get_version_label_style, COMBOBOX_STYLE
)


class SoftwareCard(QFrame):
    """Modern Bootstrap-style card for displaying software"""
    clicked = Signal(str)
    version_clicked = Signal(str)  # Signal for version label click
    readme_clicked = Signal(str)   # Signal for readme button click
    folder_clicked = Signal(str)   # Signal for folder icon click
    update_clicked = Signal(str, str, str)  # Signal for update button click (software_name, version, file_id)
    delete_clicked = Signal(str)   # Signal for delete button click (folder_path)
    card_refresh_clicked = Signal(str, str)  # Emits (folder_name, folder_id)

    def __init__(self, name, lnk_path, folder_path, is_latest=True, record_path=None,
                 icon_path=None, folder_name=None, folder_id=None):
        super().__init__()
        self.folder_path = folder_path
        self.lnk_path = lnk_path
        self.icon_path = Path(icon_path) if icon_path else None
        self.record_path = Path(record_path) if record_path else Path("config-record/record.json")
        self.folder_name = folder_name or folder_path.name
        self.folder_id = folder_id or ""
        self.versions_data = []  # List of (version, file_id) tuples

        # Human-readable name used by the filter box
        _parsed = parse_software_folder_name(folder_path.name)
        self.display_name = format_software_name(_parsed)

        # Check if update is available by comparing versions
        self.is_latest = self._check_version_status()

        self.setFixedSize(320, 352)
        self.setStyleSheet(CARD_STYLE)

        # Main layout
        main_layout = QVBoxLayout(self)
        main_layout.setAlignment(Qt.AlignTop)
        main_layout.setSpacing(5)
        main_layout.setContentsMargins(10, 15, 10, 10)

        # Top row: left column (ReadMe + Delete) | stretch | right column (⟳ + >)
        top_row = QHBoxLayout()
        top_row.setSpacing(5)

        left_col = QVBoxLayout()
        left_col.setSpacing(4)
        left_col.setAlignment(Qt.AlignTop)

        readme_button = ClickableLabel("ReadMe")
        readme_button.setAlignment(Qt.AlignCenter)
        readme_button.setCursor(Qt.PointingHandCursor)
        readme_button.setFixedHeight(28)
        readme_button.setFixedWidth(70)
        readme_button.setStyleSheet(get_version_label_style(
            "#007bff",      # Blue text
            "#cfe2ff",      # Light blue background
            "#b6d4fe",      # Darker blue on hover
            "#0056b3"       # Darker blue text on hover
        ))
        readme_button.clicked.connect(lambda: self.readme_clicked.emit(str(self.folder_path)))
        left_col.addWidget(readme_button)

        delete_button = ClickableLabel("Delete")
        delete_button.setAlignment(Qt.AlignCenter)
        delete_button.setCursor(Qt.PointingHandCursor)
        delete_button.setFixedHeight(28)
        delete_button.setFixedWidth(70)
        delete_button.setStyleSheet(get_version_label_style(
            "#dc3545",      # Red text
            "#f8d7da",      # Light red background
            "#f5c2c7",      # Darker red on hover
            "#a71d2a"       # Darker red text on hover
        ))
        delete_button.clicked.connect(lambda: self.delete_clicked.emit(str(self.folder_path)))
        left_col.addWidget(delete_button)

        top_row.addLayout(left_col)
        top_row.addStretch()

        # Right column: ⟳ (refresh) on top, > (folder) on bottom
        right_col = QVBoxLayout()
        right_col.setSpacing(4)
        right_col.setAlignment(Qt.AlignTop)

        self.refresh_card_btn = ClickableLabel("⟳")
        self.refresh_card_btn.setAlignment(Qt.AlignCenter)
        self.refresh_card_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_card_btn.setFixedSize(30, 30)
        self.refresh_card_btn.setToolTip(f"Refresh {self.folder_name} from Box")
        self.refresh_card_btn.setStyleSheet("""
            QLabel {
                background-color: #e8f4fd;
                color: #0d6efd;
                border: 1.5px solid #90c6f5;
                border-radius: 15px;
                font-size: 15px;
                font-weight: bold;
            }
            QLabel:hover {
                background-color: #cfe2ff;
                border-color: #0d6efd;
                color: #0a58ca;
            }
        """)
        self.refresh_card_btn.clicked.connect(self._on_card_refresh_clicked)
        right_col.addWidget(self.refresh_card_btn)

        self.folder_button = ClickableLabel(">")
        self.folder_button.setAlignment(Qt.AlignCenter)
        self.folder_button.setCursor(Qt.PointingHandCursor)
        self.folder_button.setFixedSize(30, 30)
        self.folder_button.setToolTip("Directory Path")
        self.folder_button.setStyleSheet(get_version_label_style(
            "#A8A8A8",      # Grey text
            "#ffffff",      # White background
            "#d1d5db",      # Light grey border (hover background)
            "#6b7280"       # Darker grey text on hover
        ))
        self.folder_button.clicked.connect(lambda: self.folder_clicked.emit(str(self.folder_path)))
        right_col.addWidget(self.folder_button)

        top_row.addLayout(right_col)
        main_layout.addLayout(top_row)

        # Icon — stored as instance attribute so it can be updated after refresh
        self.icon_label = QLabel()
        icon_label = self.icon_label   # local alias
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setFixedSize(100, 100)
        icon_label.setStyleSheet(CARD_ICON_STYLE)
        icon_label.setToolTip("Click to Launch")
        icon_label.setCursor(Qt.PointingHandCursor)

        pixmap = self._load_icon(folder_path)
        if pixmap and not pixmap.isNull():
            scaled = pixmap.scaled(90, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            icon_label.setPixmap(scaled)
        else:
            icon_label.setText("📦")
            icon_label.setStyleSheet(CARD_ICON_FALLBACK_STYLE)

        main_layout.addWidget(icon_label, 0, Qt.AlignHCenter)
        
        # Add software info label below icon
        self._setup_info_label(main_layout, folder_path)
        
        # Load versions from record.json
        self._load_versions()
        
        # Add controls (ReadMe, ComboBox, Status)
        self._setup_controls(main_layout)
    
    def _load_icon(self, folder_path):
        """Load the card icon from the App_Store folder.

        Uses icon_path resolved by _get_flow_info() in main_controller:
        1. [Icon] Name= from Flow.txt (if Flag=True and file exists)
        2. Any .ico found in App_Store/<name>-<author>/ (fallback in _get_flow_info)
        Returns None if no icon is available (card shows 📦 emoji).
        """
        if not self.icon_path or not self.icon_path.exists():
            return None

        try:
            icon = QIcon(str(self.icon_path))
            sizes = icon.availableSizes()
            if sizes:
                largest = max(sizes, key=lambda s: s.width() * s.height())
                pixmap = icon.pixmap(largest)
                if pixmap and not pixmap.isNull():
                    return pixmap
            # Direct QPixmap load as fallback
            pixmap = QPixmap(str(self.icon_path))
            if pixmap and not pixmap.isNull():
                return pixmap
        except Exception:
            pass

        return None

    def _extract_icon_from_lnk(self, lnk_path, folder_path):
        """Extract icon from .lnk shortcut file - Safe method"""
        if not lnk_path or not lnk_path.exists():
            return None
        
        try:
            # Method 1: Try to get icon location from shortcut using win32com
            try:
                import win32com.client
                shell = win32com.client.Dispatch("WScript.Shell")
                shortcut = shell.CreateShortCut(str(lnk_path))
                icon_location = shortcut.IconLocation
                
                if icon_location:
                    # Parse icon location (format: "path,index" or just "path")
                    if ',' in icon_location:
                        icon_path_str = icon_location.split(',')[0].strip()
                    else:
                        icon_path_str = icon_location.strip()
                    
                    # Try to load the icon from the specified path
                    if icon_path_str and Path(icon_path_str).exists():
                        icon = QIcon(icon_path_str)
                        available_sizes = icon.availableSizes()
                        
                        if available_sizes:
                            # Get the largest size available
                            largest = max(available_sizes, key=lambda s: s.width() * s.height())
                            return icon.pixmap(largest)
                        else:
                            return QPixmap(icon_path_str)
                
                # If icon location is empty, try target path
                target_path = shortcut.TargetPath
                if target_path and Path(target_path).exists():
                    icon = QIcon(target_path)
                    available_sizes = icon.availableSizes()
                    
                    if available_sizes:
                        largest = max(available_sizes, key=lambda s: s.width() * s.height())
                        pixmap = icon.pixmap(largest)
                        if not pixmap.isNull():
                            return pixmap
                            
            except Exception:
                # Silently fall back to other methods if win32com fails
                pass
            
            # Method 2: Fallback - Try to load .ico file from folder
            icon_files = list(folder_path.glob("*.ico"))
            if icon_files:
                icon = QIcon(str(icon_files[0]))
                available_sizes = icon.availableSizes()
                
                if available_sizes:
                    largest = max(available_sizes, key=lambda s: s.width() * s.height())
                    return icon.pixmap(largest)
                else:
                    return QPixmap(str(icon_files[0]))
            
            # Method 3: Try to extract icon directly from .lnk using QIcon
            icon = QIcon(str(lnk_path))
            available_sizes = icon.availableSizes()
            
            if available_sizes:
                largest = max(available_sizes, key=lambda s: s.width() * s.height())
                pixmap = icon.pixmap(largest)
                if not pixmap.isNull():
                    return pixmap
                    
        except Exception:
            # Silently return None if icon extraction fails
            pass
        
        return None
    
    def _setup_info_label(self, layout, folder_path):
        """Setup the software info label using parsed folder name"""
        # Parse folder name to extract metadata
        parsed = parse_software_folder_name(folder_path.name)
        
        # Extract formatted values
        software_name = format_software_name(parsed)
        version = format_version(parsed)
        author = format_author(parsed)
        
        # Format: Name (bold), version and author (normal weight)
        # Use HTML for formatting
        software_info = f"<b>{software_name}</b><br>{version}<br>{author}"
        
        # Name and version label with transparent border
        info_label = QLabel(software_info)
        info_label.setAlignment(Qt.AlignCenter)
        info_label.setWordWrap(True)
        info_label.setFixedWidth(260)
        info_label.setFixedHeight(60)
        info_label.setTextFormat(Qt.RichText)
        info_label.setStyleSheet(CARD_INFO_STYLE)
        layout.addWidget(info_label, 0, Qt.AlignHCenter)
    
    def _check_version_status(self):
        """Check if the installed version is the latest by comparing with App_Store JSON"""
        try:
            # Parse folder name: BandMaster_V-1.0.0.0_A-SuetLi
            folder_name = self.folder_path.name
            
            # Extract software name, version, and author
            parsed = parse_software_folder_name(folder_name)
            software_name = format_software_name(parsed)
            author = format_author(parsed).replace("by ", "")
            installed_version = format_version(parsed).replace("v", "")  # e.g., "1.0.0.0"
            
            # Build App_Store JSON path
            app_store_folder = f"{software_name}-{author}"
            json_path = Path("App_Store") / app_store_folder / f"{app_store_folder}.json"
            
            if not json_path.exists():
                # If JSON doesn't exist, assume it's latest
                return True
            
            # Read JSON file
            with open(json_path, 'r', encoding='utf-8') as f:
                store_data = json.load(f)
            
            # Extract all version files
            version_pattern = re.compile(r'^v(\d+)\.(\d+)\.(\d+)\.(\d+)\.zip$')
            versions = []
            
            for file_item in store_data.get('files', []):
                match = version_pattern.match(file_item.get('name', ''))
                if match:
                    version_str = file_item['name'].replace('v', '').replace('.zip', '')
                    versions.append(version_str)
            
            if not versions:
                # No versions found, assume latest
                return True
            
            # Sort versions to find the latest
            def version_key(v):
                return [int(x) for x in v.split('.')]
            
            versions.sort(key=version_key, reverse=True)
            latest_version = versions[0]
            
            # Compare installed version with latest version
            installed_parts = [int(x) for x in installed_version.split('.')]
            latest_parts = [int(x) for x in latest_version.split('.')]
            
            # Return True if installed version >= latest version
            return installed_parts >= latest_parts
            
        except Exception as e:
            print(f"Error checking version status: {e}")
            # On error, assume it's latest
            return True
    
    def _load_versions(self):
        """Load available versions from record.json"""
        if not self.record_path.exists():
            return
        
        try:
            with open(self.record_path, 'r', encoding='utf-8') as f:
                record_data = json.load(f)
            
            # Parse folder name to get the software identifier
            parsed = parse_software_folder_name(self.folder_path.name)
            software_name = format_software_name(parsed)
            author = format_author(parsed).replace("by ", "")
            
            # Find matching folder in record.json
            folder_key = f"{software_name}-{author}"
            
            for item in record_data.get('items', []):
                if item.get('name') == folder_key and item.get('type') == 'folder':
                    contents = item.get('contents', {})
                    items = contents.get('items', [])
                    
                    # Extract version files (pattern: vX.X.X.X.zip)
                    version_pattern = re.compile(r'^v(\d+)\.(\d+)\.(\d+)\.(\d+)\.zip$')
                    
                    for file_item in items:
                        if file_item.get('type') == 'file':
                            match = version_pattern.match(file_item.get('name', ''))
                            if match:
                                version = file_item['name'].replace('.zip', '')
                                file_id = file_item['id']
                                self.versions_data.append((version, file_id))
                    
                    # Sort versions (newest first)
                    self.versions_data.sort(key=lambda x: [int(n) for n in x[0].replace('v', '').split('.')], reverse=True)
                    break
                    
        except Exception as e:
            print(f"Error loading versions: {e}")
    
    def _setup_controls(self, layout):
        """Setup the ComboBox and status button"""
        # Add spacer to push controls down
        layout.addStretch()
        
        # Bottom row: ComboBox and Status button
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(5)
        bottom_row.setContentsMargins(10, 5, 10, 0)
        bottom_row.setAlignment(Qt.AlignBottom)  # Align all widgets to bottom
        
        # Version ComboBox
        self.version_combo = QComboBox()
        self.version_combo.setFixedHeight(34)
        self.version_combo.setFixedWidth(100)  
        self.version_combo.setStyleSheet(COMBOBOX_STYLE)
        
        # Populate ComboBox with versions
        if self.versions_data:
            for version, file_id in self.versions_data:
                self.version_combo.addItem(version, file_id)
        else:
            self.version_combo.addItem("No versions")
            self.version_combo.setEnabled(False)
        
        bottom_row.addWidget(self.version_combo, stretch=1, alignment=Qt.AlignBottom)
        
        # Status button (Latest/Update) - Changed from label to button
        if self.is_latest:
            config = VERSION_LATEST_CONFIG
        else:
            config = VERSION_OUTDATED_CONFIG
        
        self.version_button = QPushButton(config["text"])
        self.version_button.setCursor(Qt.PointingHandCursor)
        self.version_button.setFixedHeight(34)
        self.version_button.setFixedWidth(150)
        self.version_button.setStyleSheet(get_version_label_style(
            config["color"], config["bg_color"], 
            config["hover_bg"], config["hover_color"]
        ))
        
        # Connect button click - both Latest and Update trigger download
        self.version_button.clicked.connect(self._on_update_button_clicked)
        
        bottom_row.addWidget(self.version_button, alignment=Qt.AlignBottom)
        
        layout.addLayout(bottom_row)
    
    def _on_update_button_clicked(self):
        """Handle update button click - download selected version from ComboBox"""
        # Get the currently selected version from the ComboBox
        selected_index = self.version_combo.currentIndex()
        
        if selected_index >= 0 and selected_index < len(self.versions_data):
            selected_version, selected_file_id = self.versions_data[selected_index]
            
            # Parse folder name to get software name and author
            parsed = parse_software_folder_name(self.folder_path.name)
            software_name = format_software_name(parsed)
            author = format_author(parsed).replace("by ", "")
            
            # Emit signal with software name, selected version, and file ID
            self.update_clicked.emit(software_name, selected_version, selected_file_id)
        else:
            # No versions available, fall back to version info
            self.version_clicked.emit(str(self.folder_path))
    
    def update_version_status(self, is_latest: bool):
        """Update the version button to show latest or update available"""
        if is_latest:
            config = VERSION_LATEST_CONFIG
        else:
            config = VERSION_OUTDATED_CONFIG
        
        self.version_button.setText(config["text"])
        self.version_button.setStyleSheet(get_version_label_style(
            config["color"], config["bg_color"], 
            config["hover_bg"], config["hover_color"]
        ))
    
    def _get_software_info(self, folder_path):
        """Extract software name and version from README.md"""
        readme_path = folder_path / "README.md"
        
        if readme_path.exists():
            try:
                with open(readme_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # Parse the README for name and version
                name = folder_path.name
                version = ""
                
                # Look for "Software Name = " pattern
                if "Software Name" in content:
                    for line in content.split('\n'):
                        if "Software Name" in line and "=" in line:
                            name = line.split('=', 1)[1].strip()
                        elif "Software Version" in line and "=" in line:
                            version = line.split('=', 1)[1].strip()
                
                # Format the info
                if version:
                    return f"{name}\n{version}"
                else:
                    return name
                    
            except Exception:
                # Return folder name if README can't be read
                return folder_path.name
        else:
            return folder_path.name
    
    def _on_card_refresh_clicked(self):
        """Handle per-card refresh button click."""
        self.card_refresh_clicked.emit(self.folder_name, self.folder_id)

    def set_refreshing(self, is_refreshing: bool):
        """Toggle visual state of the refresh button while syncing."""
        if is_refreshing:
            self.refresh_card_btn.setText("…")
            self.refresh_card_btn.setEnabled(False)
            self.refresh_card_btn.setStyleSheet("""
                QLabel {
                    background-color: #e9ecef;
                    color: #6c757d;
                    border: 1.5px solid #adb5bd;
                    border-radius: 15px;
                    font-size: 15px;
                    font-weight: bold;
                }
            """)
        else:
            self.refresh_card_btn.setText("⟳")
            self.refresh_card_btn.setEnabled(True)
            self.refresh_card_btn.setStyleSheet("""
                QLabel {
                    background-color: #e8f4fd;
                    color: #0d6efd;
                    border: 1.5px solid #90c6f5;
                    border-radius: 15px;
                    font-size: 15px;
                    font-weight: bold;
                }
                QLabel:hover {
                    background-color: #cfe2ff;
                    border-color: #0d6efd;
                    color: #0a58ca;
                }
            """)

    def refresh_versions_from_app_store(self, app_store_json_path):
        """Reload the version ComboBox from the App_Store JSON (updated by per-card refresh).

        The App_Store JSON is always updated in Pass 1 of SingleCardDownloadWorker,
        so this reflects the latest file list from Box without needing record.json.
        """
        try:
            with open(app_store_json_path, 'r', encoding='utf-8') as f:
                store_data = json.load(f)

            version_pattern = re.compile(r'^v(\d+\.\d+\.\d+\.\d+)\.zip$', re.IGNORECASE)
            new_versions = []
            for file_item in store_data.get('files', []):
                match = version_pattern.match(file_item.get('name', ''))
                if match and file_item.get('id'):
                    new_versions.append((f"v{match.group(1)}", file_item['id']))

            new_versions.sort(
                key=lambda x: [int(n) for n in x[0].replace('v', '').split('.')],
                reverse=True
            )

            if new_versions:
                self.versions_data = new_versions
                self.version_combo.clear()
                for version, file_id in new_versions:
                    self.version_combo.addItem(version, file_id)
                self.version_combo.setEnabled(True)
        except Exception as e:
            print(f"[CARD] Could not refresh versions from App_Store JSON: {e}")

    def refresh_icon(self, icon_path):
        """Reload the card icon from a new path after a per-card refresh."""
        try:
            icon = QIcon(str(icon_path))
            sizes = icon.availableSizes()
            if sizes:
                largest = max(sizes, key=lambda s: s.width() * s.height())
                pixmap = icon.pixmap(largest)
                if pixmap and not pixmap.isNull():
                    scaled = pixmap.scaled(90, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    self.icon_label.setPixmap(scaled)
                    self.icon_label.setStyleSheet(CARD_ICON_STYLE)
                    return
        except Exception:
            pass

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(str(self.folder_path))
