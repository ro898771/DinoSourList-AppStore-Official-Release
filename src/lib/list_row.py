"""
Compact list-row widgets for the Dashboard/Store "List View" toggle.

Unlike SoftwareCard/StoreCard (icon-centered squares for the grid), these
render as a single wide horizontal row with no icon -- just name/author/
version text plus the same action buttons, at a fraction of the height.
Each row mirrors the signal names/signatures of its card counterpart so
main_controller's existing signal wiring and per-card refresh handlers
(set_refreshing/refresh_icon/refresh_versions_from_app_store/
update_version_status) work unchanged regardless of which view is active.
"""

import json
import re
from pathlib import Path
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QComboBox
from PySide6.QtCore import Qt, Signal

from .clickable_label import ClickableLabel
from .bookmark_button import BookmarkButton
from .folder_parser import (
    parse_software_folder_name, format_software_name, format_version,
    format_author, get_author_raw,
)
from .styles import (
    get_version_label_style, DISABLED_ACTION_LABEL_STYLE,
    VERSION_LATEST_CONFIG, VERSION_OUTDATED_CONFIG, COMBOBOX_STYLE,
)

LIST_ROW_WIDTH = 900
LIST_ROW_HEIGHT = 64

# Deliberately no padding (unlike CARD_STYLE, which is sized for the big
# square icon cards) -- the QHBoxLayout's own contentsMargins fully control
# spacing here, so the exact content height is known and every child fits
# without being squeezed against the frame's fixed height.
_ROW_FRAME_STYLE = """
    QFrame {
        background-color: #ffffff;
        border: 1px solid #e0e0e0;
        border-radius: 10px;
    }
    QFrame:hover {
        background-color: #f8f9fa;
        border: 1px solid #90c6f5;
    }
"""

_ROW_INFO_STYLE = "QLabel { font-size: 13px; color: #212529; border: none; background: transparent; }"
_ROW_SEQ_STYLE = "QLabel { font-size: 12px; font-weight: 700; color: #868e96; border: none; background: transparent; }"
_ROW_REFRESH_STYLE = """
    QLabel {
        background-color: #e8f4fd;
        color: #0d6efd;
        border: 1.5px solid #90c6f5;
        border-radius: 14px;
        font-size: 13px;
        font-weight: bold;
    }
    QLabel:hover {
        background-color: #cfe2ff;
        border-color: #0d6efd;
        color: #0a58ca;
    }
"""
def _make_favourite_button(row):
    """Shared bookmark button for SoftwareListRow/StoreListRow -- both
    classes have identical favourite behavior, only the row-specific
    attributes (self.folder_name, self.is_favourite) differ.
    """
    btn = BookmarkButton(is_favourite=row.is_favourite, size=28)
    btn.clicked.connect(lambda: _on_favourite_clicked(row, btn))
    return btn


def _on_favourite_clicked(row, btn):
    row.is_favourite = btn.toggle()
    row.favourite_toggled.emit(row.folder_name, row.is_favourite)


class SoftwareListRow(QFrame):
    """Local Dashboard list-view row: name/version/author text + actions, no icon."""
    clicked = Signal(str)
    version_clicked = Signal(str)
    readme_clicked = Signal(str)
    folder_clicked = Signal(str)
    update_clicked = Signal(str, str, str)
    delete_clicked = Signal(str)
    card_refresh_clicked = Signal(str, str)
    favourite_toggled = Signal(str, bool)

    def __init__(self, name, lnk_path, folder_path, is_latest=True, record_path=None,
                 icon_path=None, folder_name=None, folder_id=None, readme_available=True,
                 sequence_number=None, exec_valid=True, is_favourite=False):
        super().__init__()
        self.folder_path = folder_path
        self.folder_name = folder_name or folder_path.name
        self.folder_id = folder_id or ""
        self.is_favourite = is_favourite
        self.versions_data = []
        self.is_latest = is_latest
        self.exec_valid = exec_valid

        parsed = parse_software_folder_name(folder_path.name)
        self.display_name = format_software_name(parsed)
        self.author_name = get_author_raw(parsed)
        version = format_version(parsed)

        self.setFixedWidth(LIST_ROW_WIDTH)
        self.setFixedHeight(LIST_ROW_HEIGHT)
        self.setStyleSheet(_ROW_FRAME_STYLE)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(
            "Click to Launch" if exec_valid
            else "⚠ Unrecognized execution format — click for details"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 4, 12, 4)
        layout.setSpacing(10)
        # Every child below has a fixed height smaller than the row -- center
        # them as a group instead of letting them default to the top/bottom.
        layout.setAlignment(Qt.AlignVCenter)

        layout.addWidget(_make_favourite_button(self), 0, Qt.AlignVCenter)

        seq_label = QLabel(f"{sequence_number}." if sequence_number else "")
        seq_label.setFixedSize(30, 28)
        seq_label.setAlignment(Qt.AlignCenter)
        seq_label.setStyleSheet(_ROW_SEQ_STYLE)
        layout.addWidget(seq_label, 0, Qt.AlignVCenter)

        name_html = f"<b>{self.display_name}</b>, {self.author_name} &nbsp; <span style='color:#868e96;'>{version}</span>"
        if not exec_valid:
            name_html = f"⚠️ {name_html}"
        info_label = QLabel(name_html)
        info_label.setTextFormat(Qt.RichText)
        info_label.setFixedHeight(28)
        info_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        info_label.setStyleSheet(_ROW_INFO_STYLE)
        layout.addWidget(info_label, 1, Qt.AlignVCenter)

        self._load_versions()

        readme_button = ClickableLabel("ReadMe")
        readme_button.setAlignment(Qt.AlignCenter)
        readme_button.setFixedHeight(28)
        readme_button.setFixedWidth(70)
        if readme_available:
            readme_button.setCursor(Qt.PointingHandCursor)
            readme_button.setStyleSheet(get_version_label_style(
                "#007bff", "#cfe2ff", "#b6d4fe", "#0056b3"
            ))
            readme_button.clicked.connect(lambda: self.readme_clicked.emit(str(self.folder_path)))
        else:
            readme_button.setStyleSheet(DISABLED_ACTION_LABEL_STYLE)
            readme_button.setEnabled(False)
        layout.addWidget(readme_button, 0, Qt.AlignVCenter)

        delete_button = ClickableLabel("Delete")
        delete_button.setAlignment(Qt.AlignCenter)
        delete_button.setCursor(Qt.PointingHandCursor)
        delete_button.setFixedHeight(28)
        delete_button.setFixedWidth(70)
        delete_button.setStyleSheet(get_version_label_style(
            "#dc3545", "#f8d7da", "#f5c2c7", "#a71d2a"
        ))
        delete_button.clicked.connect(lambda: self.delete_clicked.emit(str(self.folder_path)))
        layout.addWidget(delete_button, 0, Qt.AlignVCenter)

        self.folder_button = ClickableLabel("»")
        self.folder_button.setAlignment(Qt.AlignCenter)
        self.folder_button.setCursor(Qt.PointingHandCursor)
        self.folder_button.setFixedSize(28, 28)
        self.folder_button.setToolTip("Directory Path")
        self.folder_button.setStyleSheet(get_version_label_style(
            "#A8A8A8", "#ffffff", "#d1d5db", "#6b7280"
        ))
        self.folder_button.clicked.connect(lambda: self.folder_clicked.emit(str(self.folder_path)))
        layout.addWidget(self.folder_button, 0, Qt.AlignVCenter)

        self.refresh_card_btn = ClickableLabel("⟳")
        self.refresh_card_btn.setAlignment(Qt.AlignCenter)
        self.refresh_card_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_card_btn.setFixedSize(28, 28)
        self.refresh_card_btn.setToolTip(f"Refresh {self.folder_name} from Box")
        self.refresh_card_btn.setStyleSheet(_ROW_REFRESH_STYLE)
        self.refresh_card_btn.clicked.connect(self._on_card_refresh_clicked)
        layout.addWidget(self.refresh_card_btn, 0, Qt.AlignVCenter)

        config = VERSION_LATEST_CONFIG if self.is_latest else VERSION_OUTDATED_CONFIG
        self.version_button = ClickableLabel(config["text"])
        self.version_button.setAlignment(Qt.AlignCenter)
        self.version_button.setCursor(Qt.PointingHandCursor)
        self.version_button.setFixedHeight(28)
        self.version_button.setFixedWidth(110)
        self.version_button.setStyleSheet(get_version_label_style(
            config["color"], config["bg_color"], config["hover_bg"], config["hover_color"]
        ))
        self.version_button.clicked.connect(self._on_update_clicked)
        layout.addWidget(self.version_button, 0, Qt.AlignVCenter)

    def _load_versions(self):
        """Load available versions from App_Store JSON (same source as SoftwareCard)."""
        try:
            parsed = parse_software_folder_name(self.folder_path.name)
            software_name = format_software_name(parsed)
            author = format_author(parsed).replace("by ", "")

            app_store_folder = f"{software_name}-{author}"
            json_path = Path("App_Store") / app_store_folder / f"{app_store_folder}.json"

            if not json_path.exists():
                return

            with open(json_path, 'r', encoding='utf-8') as f:
                store_data = json.load(f)

            version_pattern = re.compile(r'^v(\d+\.\d+\.\d+\.\d+)\.zip$', re.IGNORECASE)
            for file_item in store_data.get('files', []):
                match = version_pattern.match(file_item.get('name', ''))
                if match and file_item.get('id'):
                    self.versions_data.append((match.group(1), file_item['id']))

            self.versions_data.sort(key=lambda x: [int(n) for n in x[0].split('.')], reverse=True)
        except Exception as e:
            print(f"[LIST ROW] Error loading versions: {e}")

    def _on_update_clicked(self):
        """Download the newest available version, or fall back to version info if none."""
        if self.versions_data:
            version, file_id = self.versions_data[0]
            parsed = parse_software_folder_name(self.folder_path.name)
            software_name = format_software_name(parsed)
            author = format_author(parsed).replace("by ", "")
            self.update_clicked.emit(software_name, version, file_id)
        else:
            self.version_clicked.emit(str(self.folder_path))

    def _on_card_refresh_clicked(self):
        self.card_refresh_clicked.emit(self.folder_name, self.folder_id)

    def set_refreshing(self, is_refreshing: bool):
        """Toggle visual state of the refresh button while syncing."""
        if is_refreshing:
            self.refresh_card_btn.setText("…")
            self.refresh_card_btn.setEnabled(False)
        else:
            self.refresh_card_btn.setText("⟳")
            self.refresh_card_btn.setEnabled(True)

    def _check_version_status(self):
        """Check if the installed version is the latest by comparing with
        App_Store JSON (same logic as SoftwareCard._check_version_status,
        called directly by main_controller after a per-card refresh)."""
        try:
            folder_name = self.folder_path.name
            parsed = parse_software_folder_name(folder_name)
            software_name = format_software_name(parsed)
            author = format_author(parsed).replace("by ", "")
            installed_version = format_version(parsed).replace("v", "")

            app_store_folder = f"{software_name}-{author}"
            json_path = Path("App_Store") / app_store_folder / f"{app_store_folder}.json"

            if not json_path.exists():
                return True

            with open(json_path, 'r', encoding='utf-8') as f:
                store_data = json.load(f)

            version_pattern = re.compile(r'^v(\d+\.\d+\.\d+\.\d+)\.zip$', re.IGNORECASE)
            versions = []
            for file_item in store_data.get('files', []):
                match = version_pattern.match(file_item.get('name', ''))
                if match:
                    versions.append(match.group(1))

            if not versions:
                return True

            def version_key(v):
                return [int(x) for x in v.split('.')]

            versions.sort(key=version_key, reverse=True)
            latest_version = versions[0]

            installed_parts = [int(x) for x in installed_version.split('.')]
            latest_parts = [int(x) for x in latest_version.split('.')]
            return installed_parts >= latest_parts
        except Exception as e:
            print(f"[LIST ROW] Error checking version status: {e}")
            return True

    def update_version_status(self, is_latest: bool):
        """Update the version pill to show latest or update available."""
        self.is_latest = is_latest
        config = VERSION_LATEST_CONFIG if is_latest else VERSION_OUTDATED_CONFIG
        self.version_button.setText(config["text"])
        self.version_button.setStyleSheet(get_version_label_style(
            config["color"], config["bg_color"], config["hover_bg"], config["hover_color"]
        ))

    def refresh_versions_from_app_store(self, app_store_json_path):
        """Reload version data from the App_Store JSON (updated by per-card refresh)."""
        try:
            with open(app_store_json_path, 'r', encoding='utf-8') as f:
                store_data = json.load(f)

            version_pattern = re.compile(r'^v(\d+\.\d+\.\d+\.\d+)\.zip$', re.IGNORECASE)
            new_versions = []
            for file_item in store_data.get('files', []):
                match = version_pattern.match(file_item.get('name', ''))
                if match and file_item.get('id'):
                    new_versions.append((match.group(1), file_item['id']))

            new_versions.sort(key=lambda x: [int(n) for n in x[0].split('.')], reverse=True)
            if new_versions:
                self.versions_data = new_versions
        except Exception as e:
            print(f"[LIST ROW] Could not refresh versions from App_Store JSON: {e}")

    def refresh_icon(self, icon_path):
        """No-op -- list rows don't render an icon."""

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(str(self.folder_path))
        super().mousePressEvent(event)


class StoreListRow(QFrame):
    """Software Store list-view row: name/author text + actions, no icon."""
    download_clicked = Signal(str, str, str)
    guide_clicked = Signal(str)
    readme_clicked = Signal(str)
    card_refresh_clicked = Signal(str, str)
    favourite_toggled = Signal(str, bool)

    def __init__(self, software_name, author_name, icon_path=None, json_path=None,
                 folder_name=None, folder_id=None, guide_available=True, readme_available=True,
                 sequence_number=None, is_favourite=False):
        super().__init__()
        self.software_name = software_name
        self.author_name = author_name
        self.json_path = Path(json_path) if json_path else None
        self.folder_name = folder_name or f"{software_name}-{author_name}"
        self.folder_id = folder_id or ""
        self.is_favourite = is_favourite
        self.versions_data = []

        self.setFixedWidth(LIST_ROW_WIDTH)
        self.setFixedHeight(LIST_ROW_HEIGHT)
        self.setStyleSheet(_ROW_FRAME_STYLE)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 4, 12, 4)
        layout.setSpacing(10)
        # Every child below has a fixed height smaller than the row -- center
        # them as a group instead of letting them default to the top/bottom.
        layout.setAlignment(Qt.AlignVCenter)

        layout.addWidget(_make_favourite_button(self), 0, Qt.AlignVCenter)

        seq_label = QLabel(f"{sequence_number}." if sequence_number else "")
        seq_label.setFixedSize(30, 28)
        seq_label.setAlignment(Qt.AlignCenter)
        seq_label.setStyleSheet(_ROW_SEQ_STYLE)
        layout.addWidget(seq_label, 0, Qt.AlignVCenter)

        info_label = QLabel(f"<b>{software_name}</b>, {author_name}")
        info_label.setTextFormat(Qt.RichText)
        info_label.setFixedHeight(28)
        info_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        info_label.setStyleSheet(_ROW_INFO_STYLE)
        layout.addWidget(info_label, 1, Qt.AlignVCenter)

        self._load_versions()

        self.guide_label = ClickableLabel("Details")
        self.guide_label.setAlignment(Qt.AlignCenter)
        self.guide_label.setFixedHeight(28)
        self.guide_label.setFixedWidth(70)
        if guide_available:
            self.guide_label.setCursor(Qt.PointingHandCursor)
            self.guide_label.setStyleSheet(get_version_label_style(
                "#6f42c1", "#e2d9f3", "#d3c5e8", "#5a32a3"
            ))
            self.guide_label.clicked.connect(self._on_guide_clicked)
        else:
            self.guide_label.setStyleSheet(DISABLED_ACTION_LABEL_STYLE)
            self.guide_label.setEnabled(False)
        layout.addWidget(self.guide_label, 0, Qt.AlignVCenter)

        self.readme_label = ClickableLabel("ReadMe")
        self.readme_label.setAlignment(Qt.AlignCenter)
        self.readme_label.setFixedHeight(28)
        self.readme_label.setFixedWidth(70)
        if readme_available:
            self.readme_label.setCursor(Qt.PointingHandCursor)
            self.readme_label.setStyleSheet(get_version_label_style(
                "#007bff", "#cfe2ff", "#b6d4fe", "#0056b3"
            ))
            self.readme_label.clicked.connect(self._on_readme_clicked)
        else:
            self.readme_label.setStyleSheet(DISABLED_ACTION_LABEL_STYLE)
            self.readme_label.setEnabled(False)
        layout.addWidget(self.readme_label, 0, Qt.AlignVCenter)

        self.refresh_card_btn = ClickableLabel("⟳")
        self.refresh_card_btn.setAlignment(Qt.AlignCenter)
        self.refresh_card_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_card_btn.setFixedSize(28, 28)
        self.refresh_card_btn.setToolTip(f"Refresh {self.folder_name} from Box")
        self.refresh_card_btn.setStyleSheet(_ROW_REFRESH_STYLE)
        self.refresh_card_btn.clicked.connect(self._on_card_refresh_clicked)
        layout.addWidget(self.refresh_card_btn, 0, Qt.AlignVCenter)

        if self.versions_data:
            self.version_combo = QComboBox()
            self.version_combo.setStyleSheet(COMBOBOX_STYLE)
            self.version_combo.setFixedHeight(28)
            self.version_combo.setFixedWidth(90)
            for version, file_id in self.versions_data:
                self.version_combo.addItem(version, file_id)
            layout.addWidget(self.version_combo, 0, Qt.AlignVCenter)

            download_btn = ClickableLabel("Download")
            download_btn.setAlignment(Qt.AlignCenter)
            download_btn.setCursor(Qt.PointingHandCursor)
            download_btn.setFixedHeight(28)
            download_btn.setFixedWidth(80)
            download_btn.setStyleSheet("""
                QLabel {
                    background-color: #d4edda;
                    color: #155724;
                    border: 2px solid #28a745;
                    border-radius: 8px;
                    padding: 2px 6px;
                    font-size: 11px;
                    font-weight: 500;
                }
                QLabel:hover {
                    background-color: #c3e6cb;
                    border-color: #1e7e34;
                }
            """)
            download_btn.clicked.connect(self._on_download_clicked)
            layout.addWidget(download_btn, 0, Qt.AlignVCenter)
        else:
            no_version_label = QLabel("No versions")
            no_version_label.setStyleSheet("QLabel { font-size: 10px; color: #6c757d; }")
            layout.addWidget(no_version_label, 0, Qt.AlignVCenter)

    def _load_versions(self):
        """Load version information from JSON file (same source as StoreCard)."""
        if not self.json_path or not self.json_path.exists():
            return

        try:
            with open(self.json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            version_pattern = re.compile(r'^v(\d+\.\d+\.\d+\.\d+)\.zip$', re.IGNORECASE)
            for file_info in data.get('files', []):
                file_name = file_info.get('name', '')
                file_id = file_info.get('id', '')
                match = version_pattern.match(file_name)
                if match and file_id:
                    self.versions_data.append((match.group(1), file_id))

            self.versions_data.sort(key=lambda x: [int(n) for n in x[0].split('.')], reverse=True)
        except Exception as e:
            print(f"[LIST ROW] Error loading versions from {self.json_path}: {e}")

    def _on_download_clicked(self):
        if not self.versions_data:
            return
        current_index = self.version_combo.currentIndex()
        if current_index >= 0:
            version, file_id = self.versions_data[current_index]
            self.download_clicked.emit(self.software_name, version, file_id)

    def _on_guide_clicked(self):
        self.guide_clicked.emit(self.software_name)

    def _on_readme_clicked(self):
        self.readme_clicked.emit(self.software_name)

    def _on_card_refresh_clicked(self):
        self.card_refresh_clicked.emit(self.folder_name, self.folder_id)

    def set_refreshing(self, is_refreshing: bool):
        """Toggle visual state of the refresh button while syncing."""
        if is_refreshing:
            self.refresh_card_btn.setText("…")
            self.refresh_card_btn.setEnabled(False)
        else:
            self.refresh_card_btn.setText("⟳")
            self.refresh_card_btn.setEnabled(True)
