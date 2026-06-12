"""Library modules for Software Launcher Dashboard"""

from .clickable_label import ClickableLabel
from .software_card import SoftwareCard
from .main_controller import MainWindow
from .boxlink_api import (
    BoxLinkAPI, 
    get_info_default, 
    get_info_default_dict,
    get_info_dict,
    list_folder_dict,
    download_dict
)
from .folder_parser import (
    parse_software_folder_name,
    format_software_name,
    format_version,
    format_author
)

__all__ = [
    'ClickableLabel', 
    'SoftwareCard', 
    'MainWindow', 
    'BoxLinkAPI', 
    'get_info_default', 
    'get_info_default_dict',
    'get_info_dict',
    'list_folder_dict',
    'download_dict',
    'parse_software_folder_name',
    'format_software_name',
    'format_version',
    'format_author'
]
