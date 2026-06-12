"""
StoreCard - Card for displaying software in the store with version selection
"""

import json
import re
from pathlib import Path
from PySide6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QLabel, QComboBox
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QIcon
from .styles import CARD_STYLE, CARD_ICON_STYLE, CARD_ICON_FALLBACK_STYLE, CARD_INFO_STYLE, COMBOBOX_STYLE, get_version_label_style
from .clickable_label import ClickableLabel


class StoreCard(QFrame):
    """Card for displaying software in the store with version selection and download"""
    download_clicked = Signal(str, str, str)  # Emits (software_name, version, file_id)
    guide_clicked = Signal(str)               # Emits software_name when guide is clicked
    card_refresh_clicked = Signal(str, str)   # Emits (folder_name, folder_id)

    def __init__(self, software_name, author_name, icon_path=None, json_path=None,
                 folder_name=None, folder_id=None):
        super().__init__()
        self.software_name = software_name
        self.author_name = author_name
        self.icon_path = Path(icon_path) if icon_path else None
        self.json_path = Path(json_path) if json_path else None
        self.folder_name = folder_name or f"{software_name}-{author_name}"
        self.folder_id = folder_id or ""
        self.versions_data = []  # List of (version, file_id) tuples

        self.setFixedSize(320, 280)
        self.setStyleSheet(CARD_STYLE)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignTop)
        layout.setSpacing(5)
        layout.setContentsMargins(10, 15, 10, 10)

        # Top row: Details label (left) + tiny refresh button (right)
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(4)

        self.guide_label = ClickableLabel("Details")
        self.guide_label.setAlignment(Qt.AlignCenter)
        self.guide_label.setCursor(Qt.PointingHandCursor)
        self.guide_label.setFixedHeight(28)
        self.guide_label.setFixedWidth(70)
        self.guide_label.setStyleSheet(get_version_label_style(
            "#6f42c1",      # Purple text
            "#e2d9f3",      # Light warm purple background
            "#d3c5e8",      # Darker purple on hover
            "#5a32a3"       # Darker purple text on hover
        ))
        self.guide_label.clicked.connect(self._on_guide_clicked)
        top_row.addWidget(self.guide_label)

        top_row.addStretch()

        # Tiny per-card refresh button (top-right)
        self.refresh_card_btn = ClickableLabel("⟳")
        self.refresh_card_btn.setAlignment(Qt.AlignCenter)
        self.refresh_card_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_card_btn.setFixedSize(26, 26)
        self.refresh_card_btn.setToolTip(f"Refresh {self.folder_name} from Box")
        self.refresh_card_btn.setStyleSheet("""
            QLabel {
                background-color: #e8f4fd;
                color: #0d6efd;
                border: 1.5px solid #90c6f5;
                border-radius: 13px;
                font-size: 14px;
                font-weight: bold;
            }
            QLabel:hover {
                background-color: #cfe2ff;
                border-color: #0d6efd;
                color: #0a58ca;
            }
        """)
        self.refresh_card_btn.clicked.connect(self._on_card_refresh_clicked)
        top_row.addWidget(self.refresh_card_btn)

        layout.addLayout(top_row)
        
        # Icon - Load with high quality like Page 1
        self.icon_label = QLabel()
        icon_label = self.icon_label  # local alias for readability below
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setFixedSize(100, 100)
        icon_label.setStyleSheet(CARD_ICON_STYLE)
        
        # Load icon from file with high quality scaling
        if self.icon_path and self.icon_path.exists():
            # Use QIcon for better multi-resolution support (handles .ico files better)
            icon = QIcon(str(self.icon_path))
            available_sizes = icon.availableSizes()
            
            if available_sizes:
                # Get the largest size available for best quality
                largest = max(available_sizes, key=lambda s: s.width() * s.height())
                pixmap = icon.pixmap(largest)
                
                if pixmap and not pixmap.isNull():
                    # Scale to fit within 90x90 with high quality
                    scaled = pixmap.scaled(90, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    icon_label.setPixmap(scaled)
                else:
                    icon_label.setText("📦")
                    icon_label.setStyleSheet(CARD_ICON_FALLBACK_STYLE)
            else:
                # Fallback to QPixmap if no sizes available
                pixmap = QPixmap(str(self.icon_path))
                if pixmap and not pixmap.isNull():
                    scaled = pixmap.scaled(90, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    icon_label.setPixmap(scaled)
                else:
                    icon_label.setText("📦")
                    icon_label.setStyleSheet(CARD_ICON_FALLBACK_STYLE)
        else:
            icon_label.setText("📦")
            icon_label.setStyleSheet(CARD_ICON_FALLBACK_STYLE)
        
        layout.addWidget(icon_label, 0, Qt.AlignHCenter)
        
        # Software info label - Match Page 1 style exactly
        # Format: Name (bold), then author on separate line
        software_info = f"<b>{software_name}</b><br>by {author_name}"
        
        info_label = QLabel(software_info)
        info_label.setAlignment(Qt.AlignCenter)
        info_label.setWordWrap(True)
        info_label.setFixedWidth(260)
        info_label.setFixedHeight(60)
        info_label.setTextFormat(Qt.RichText)
        info_label.setStyleSheet(CARD_INFO_STYLE)
        layout.addWidget(info_label, 0, Qt.AlignHCenter)
        
        # Add spacer
        layout.addStretch()
        
        # Load versions from JSON file
        self._load_versions()
        
        # Version selection ComboBox and Download button
        if self.versions_data:
            controls_layout = QHBoxLayout()
            controls_layout.setSpacing(5)
            controls_layout.setContentsMargins(10, 0, 10, 0)
            
            # ComboBox for version selection
            self.version_combo = QComboBox()
            self.version_combo.setStyleSheet(COMBOBOX_STYLE)
            self.version_combo.setFixedHeight(34)
            
            # Add versions to combo box
            for version, file_id in self.versions_data:
                self.version_combo.addItem(version, file_id)
            
            controls_layout.addWidget(self.version_combo)
            
            # Download button (using ClickableLabel) - Soft warm light green
            download_btn = ClickableLabel("Download")
            download_btn.setAlignment(Qt.AlignCenter)
            download_btn.setCursor(Qt.PointingHandCursor)
            download_btn.setFixedHeight(34)
            download_btn.setFixedWidth(90)
            download_btn.setStyleSheet("""
                QLabel {
                    background-color: #d4edda;
                    color: #155724;
                    border: 2px solid #28a745;
                    border-radius: 8px;
                    padding: 4px 8px;
                    font-size: 12px;
                    font-weight: 500;
                }
                QLabel:hover {
                    background-color: #c3e6cb;
                    border-color: #1e7e34;
                }
            """)
            download_btn.clicked.connect(self._on_download_clicked)
            controls_layout.addWidget(download_btn)
            
            layout.addLayout(controls_layout)
        else:
            # No versions available
            no_version_label = QLabel("No versions available")
            no_version_label.setAlignment(Qt.AlignCenter)
            no_version_label.setStyleSheet("""
                QLabel {
                    font-size: 10px;
                    color: #6c757d;
                    padding: 5px;
                }
            """)
            layout.addWidget(no_version_label, 0, Qt.AlignHCenter)
    
    def _load_versions(self):
        """Load version information from JSON file"""
        if not self.json_path or not self.json_path.exists():
            return
        
        try:
            with open(self.json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            files = data.get('files', [])
            
            # Filter and extract version files (pattern: v{number}.{number}.{number}.{number}.zip)
            version_pattern = re.compile(r'^v(\d+\.\d+\.\d+\.\d+)\.zip$', re.IGNORECASE)
            
            for file_info in files:
                file_name = file_info.get('name', '')
                file_id = file_info.get('id', '')
                
                match = version_pattern.match(file_name)
                if match and file_id:
                    version = match.group(1)  # Extract version number
                    self.versions_data.append((version, file_id))
            
            # Sort versions in descending order (newest first)
            self.versions_data.sort(key=lambda x: [int(n) for n in x[0].split('.')], reverse=True)
            
        except Exception as e:
            print(f"Error loading versions from {self.json_path}: {str(e)}")
    
    def _on_download_clicked(self):
        """Handle download button click"""
        if not self.versions_data:
            return
        
        # Get selected version
        current_index = self.version_combo.currentIndex()
        if current_index >= 0:
            version, file_id = self.versions_data[current_index]
            self.download_clicked.emit(self.software_name, version, file_id)
    
    def _on_guide_clicked(self):
        """Handle guide label click"""
        self.guide_clicked.emit(self.software_name)

    def _on_card_refresh_clicked(self):
        """Handle tiny per-card refresh button click"""
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
                    border-radius: 13px;
                    font-size: 14px;
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
                    border-radius: 13px;
                    font-size: 14px;
                    font-weight: bold;
                }
                QLabel:hover {
                    background-color: #cfe2ff;
                    border-color: #0d6efd;
                    color: #0a58ca;
                }
            """)
