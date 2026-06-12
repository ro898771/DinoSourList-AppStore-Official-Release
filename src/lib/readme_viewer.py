"""
README Viewer - GitHub-style README viewer dialog
"""

import re
from pathlib import Path
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                                QPushButton, QScrollArea, QTextBrowser, QWidget)
from PySide6.QtGui import QFont

# Try to import markdown for rendering
try:
    import markdown
    MARKDOWN_AVAILABLE = True
except ImportError:
    MARKDOWN_AVAILABLE = False


class ReadmeViewer(QDialog):
    """GitHub-style README viewer dialog"""
    
    def __init__(self, title, content, folder_path=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"📖 {title}")
        self.resize(900, 700)
        self.folder_path = folder_path
        
        # Main layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Header bar
        header = QWidget()
        header.setStyleSheet("""
            QWidget {
                background-color: #f6f8fa;
                border-bottom: 1px solid #d0d7de;
            }
        """)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 15, 20, 15)
        
        # Title label
        title_label = QLabel(f"📖 {title}")
        title_label.setStyleSheet("""
            QLabel {
                font-size: 18px;
                font-weight: bold;
                color: #24292f;
            }
        """)
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        
        # Close button
        close_btn = QPushButton("✕ Close")
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: #ffffff;
                color: #24292f;
                border: 1px solid #d0d7de;
                border-radius: 6px;
                padding: 6px 16px;
                font-size: 14px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #f6f8fa;
                border-color: #1b1f2326;
            }
        """)
        close_btn.clicked.connect(self.close)
        header_layout.addWidget(close_btn)
        
        layout.addWidget(header)
        
        # Content area with scroll
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("""
            QScrollArea {
                border: none;
                background-color: #ffffff;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 7px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background: rgba(0, 0, 0, 0.22);
                border-radius: 3px;
                min-height: 28px;
            }
            QScrollBar::handle:vertical:hover  { background: rgba(0, 0, 0, 0.38); }
            QScrollBar::handle:vertical:pressed { background: rgba(0, 0, 0, 0.55); }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical       { height: 0; }
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical       { background: none; }
            QScrollBar:horizontal {
                background: transparent;
                height: 7px;
                margin: 0;
            }
            QScrollBar::handle:horizontal {
                background: rgba(0, 0, 0, 0.22);
                border-radius: 3px;
                min-width: 28px;
            }
            QScrollBar::handle:horizontal:hover  { background: rgba(0, 0, 0, 0.38); }
            QScrollBar::handle:horizontal:pressed { background: rgba(0, 0, 0, 0.55); }
            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal      { width: 0; }
            QScrollBar::add-page:horizontal,
            QScrollBar::sub-page:horizontal      { background: none; }
        """)
        
        # Text browser for markdown content
        self.text_browser = QTextBrowser()
        self.text_browser.setOpenExternalLinks(True)
        
        # GitHub-like styling
        self.text_browser.setStyleSheet("""
            QTextBrowser {
                background-color: #ffffff;
                color: #24292f;
                border: none;
                padding: 30px 40px;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
                font-size: 14px;
                line-height: 1.6;
            }
        """)
        
        # Convert markdown to HTML if available
        if MARKDOWN_AVAILABLE:
            html_content = markdown.markdown(
                content,
                extensions=[
                    'fenced_code',
                    'tables',
                    'nl2br',
                    'sane_lists'
                ]
            )
            
            # Fix relative image paths to absolute file:// URLs
            if self.folder_path:
                # Find all img src attributes with relative paths
                def replace_img_src(match):
                    img_tag = match.group(0)
                    src_match = re.search(r'src=["\']([^"\']+)["\']', img_tag)
                    if src_match:
                        src = src_match.group(1)
                        # If it's a relative path (not http:// or https://)
                        if not src.startswith(('http://', 'https://', 'file://')):
                            # Convert to absolute file path
                            abs_path = (Path(self.folder_path) / src).resolve()
                            # Convert to file:// URL with forward slashes
                            file_url = abs_path.as_uri()
                            img_tag = img_tag.replace(src, file_url)
                    return img_tag
                
                html_content = re.sub(r'<img[^>]+>', replace_img_src, html_content)
            
            # Wrap in GitHub-style CSS
            styled_html = f"""
            <style>
                body {{
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
                    font-size: 14px;
                    line-height: 1.6;
                    color: #24292f;
                }}
                h1 {{
                    font-size: 2em;
                    font-weight: 600;
                    padding-bottom: 0.3em;
                    border-bottom: 1px solid #d0d7de;
                    margin-top: 24px;
                    margin-bottom: 16px;
                }}
                h2 {{
                    font-size: 1.5em;
                    font-weight: 600;
                    padding-bottom: 0.3em;
                    border-bottom: 1px solid #d0d7de;
                    margin-top: 24px;
                    margin-bottom: 16px;
                }}
                h3 {{
                    font-size: 1.25em;
                    font-weight: 600;
                    margin-top: 24px;
                    margin-bottom: 16px;
                }}
                h4, h5, h6 {{
                    font-size: 1em;
                    font-weight: 600;
                    margin-top: 24px;
                    margin-bottom: 16px;
                }}
                p {{
                    margin-top: 0;
                    margin-bottom: 16px;
                }}
                code {{
                    background-color: rgba(175, 184, 193, 0.2);
                    padding: 0.2em 0.4em;
                    border-radius: 6px;
                    font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
                    font-size: 85%;
                }}
                pre {{
                    background-color: #f6f8fa;
                    border-radius: 6px;
                    padding: 16px;
                    overflow: auto;
                    margin-bottom: 16px;
                }}
                pre code {{
                    background-color: transparent;
                    padding: 0;
                }}
                blockquote {{
                    padding: 0 1em;
                    color: #57606a;
                    border-left: 0.25em solid #d0d7de;
                    margin: 0 0 16px 0;
                }}
                ul, ol {{
                    padding-left: 2em;
                    margin-top: 0;
                    margin-bottom: 16px;
                }}
                li {{
                    margin-top: 0.25em;
                }}
                table {{
                    border-collapse: collapse;
                    border-spacing: 0;
                    margin-bottom: 16px;
                    width: 100%;
                }}
                table th {{
                    font-weight: 600;
                    padding: 6px 13px;
                    border: 1px solid #d0d7de;
                    background-color: #f6f8fa;
                }}
                table td {{
                    padding: 6px 13px;
                    border: 1px solid #d0d7de;
                }}
                a {{
                    color: #0969da;
                    text-decoration: none;
                }}
                a:hover {{
                    text-decoration: underline;
                }}
                hr {{
                    height: 0.25em;
                    padding: 0;
                    margin: 24px 0;
                    background-color: #d0d7de;
                    border: 0;
                }}
                img {{
                    max-width: 100%;
                    height: auto;
                    vertical-align: middle;
                    border-style: none;
                }}
            </style>
            {html_content}
            """
            self.text_browser.setHtml(styled_html)
        else:
            # Fallback: display as plain text with basic formatting
            self.text_browser.setPlainText(content)
            font = QFont("Consolas", 10)
            self.text_browser.setFont(font)
        
        scroll.setWidget(self.text_browser)
        layout.addWidget(scroll)
