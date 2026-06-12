"""
Folder Name Parser - Extracts software metadata from folder names
"""

import re
import json
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Any


def compare_versions(version1: str, version2: str) -> int:
    """
    Compare two version strings
    
    Args:
        version1: First version string (e.g., "1.0.0.0")
        version2: Second version string (e.g., "1.0.0.2")
        
    Returns:
        -1 if version1 < version2
         0 if version1 == version2
         1 if version1 > version2
         
    Example:
        compare_versions("1.0.0.0", "1.0.0.2") returns -1
        compare_versions("2.0.0.0", "1.0.0.2") returns 1
        compare_versions("1.0.0.0", "1.0.0.0") returns 0
    """
    # Split versions by dots and convert to integers
    try:
        v1_parts = [int(x) for x in version1.split('.')]
        v2_parts = [int(x) for x in version2.split('.')]
        
        # Pad shorter version with zeros
        max_len = max(len(v1_parts), len(v2_parts))
        v1_parts.extend([0] * (max_len - len(v1_parts)))
        v2_parts.extend([0] * (max_len - len(v2_parts)))
        
        # Compare each part
        for v1, v2 in zip(v1_parts, v2_parts):
            if v1 < v2:
                return -1
            elif v1 > v2:
                return 1
        
        return 0
    except (ValueError, AttributeError):
        # If parsing fails, treat as equal
        return 0


def parse_software_folder_name(folder_name: str) -> Dict[str, Optional[str]]:
    """
    Parse software folder name to extract name, version, and author
    
    Expected format: Name_V-Version_A-Author
    Examples:
        - BandMaster_V-1.0.0.0_A-SuetLi
        - QuickMi2e-V-7.1.0.0_A-RoeyYee
        - SbinValidation@master_V-2.0.0.0_A-RoeyJiea
    
    Args:
        folder_name: The folder name to parse
        
    Returns:
        Dictionary with keys:
        {
            'name': str or None,
            'version': str or None,
            'author': str or None,
            'raw_name': str  # Original folder name
        }
    """
    result = {
        'name': None,
        'version': None,
        'author': None,
        'raw_name': folder_name
    }
    
    # Pattern: Name_V-Version_A-Author or Name-V-Version_A-Author
    # Name can contain letters, numbers, hyphens, @, etc.
    # Version is after _V- or -V-
    # Author is after _A- or -A-
    # Support both underscore and hyphen as separators
    pattern = r'^(.+?)[-_]V-([^_-]+?)[-_]A-(.+)$'
    
    match = re.match(pattern, folder_name)
    if match:
        result['name'] = match.group(1)
        result['version'] = match.group(2)
        result['author'] = match.group(3)
    else:
        # Fallback: if pattern doesn't match, use the whole name
        result['name'] = folder_name
    
    return result


def format_software_name(parsed_info: Dict[str, Optional[str]]) -> str:
    """
    Format software name for display
    
    Args:
        parsed_info: Dictionary from parse_software_folder_name()
        
    Returns:
        Formatted name (e.g., "BandMaster" or "QuickMi2e")
    """
    return parsed_info.get('name') or parsed_info.get('raw_name', 'Unknown')


def format_version(parsed_info: Dict[str, Optional[str]]) -> str:
    """
    Format version for display
    
    Args:
        parsed_info: Dictionary from parse_software_folder_name()
        
    Returns:
        Formatted version (e.g., "v1.0.0.0" or "No version")
    """
    version = parsed_info.get('version')
    if version:
        return f"v{version}"
    return "No version"


def get_version_raw(parsed_info: Dict[str, Optional[str]]) -> str:
    """
    Get raw version without 'v' prefix
    
    Args:
        parsed_info: Dictionary from parse_software_folder_name()
        
    Returns:
        Raw version (e.g., "1.0.0.0" or "No version")
    """
    version = parsed_info.get('version')
    return version if version else "No version"


def format_author(parsed_info: Dict[str, Optional[str]]) -> str:
    """
    Format author for display
    
    Args:
        parsed_info: Dictionary from parse_software_folder_name()
        
    Returns:
        Formatted author (e.g., "by SuetLi" or "Unknown author")
    """
    author = parsed_info.get('author')
    if author:
        return f"by {author}"
    return "Unknown author"


def get_author_raw(parsed_info: Dict[str, Optional[str]]) -> str:
    """
    Get raw author name without 'by' prefix
    
    Args:
        parsed_info: Dictionary from parse_software_folder_name()
        
    Returns:
        Raw author name (e.g., "SuetLi" or "Unknown")
    """
    author = parsed_info.get('author')
    return author if author else "Unknown"


def get_folder_contents_by_name(software_name: str, record_json_path: Optional[Path] = None) -> Tuple[bool, Optional[List[Dict[str, Any]]], str]:
    """
    Search for software by name in record.json, get its folder ID, 
    and list its contents using BoxLink API
    
    Args:
        software_name: Name of the software to search for (e.g., "BandMaster", "SbinValidation-master")
        record_json_path: Optional path to record.json. If None, uses default location.
        
    Returns:
        Tuple of (success: bool, items: List[Dict] or None, error: str)
        - success: True if operation completed successfully
        - items: List of folder items (files/folders) or None if failed
        - error: Error message if any
        
    Example:
        success, items, error = get_folder_contents_by_name("BandMaster")
        if success:
            for item in items:
                print(f"{item['name']} - {item['type']}")
        else:
            print(f"Error: {error}")
    """
    try:
        # Import BoxLinkAPI here to avoid circular imports
        from .boxlink_api import BoxLinkAPI
        
        # Determine record.json path
        if record_json_path is None:
            # Default: config-record/record.json relative to this file
            record_json_path = Path(__file__).parent.parent.parent / "config-record" / "record.json"
        
        # Check if record.json exists
        if not record_json_path.exists():
            return False, None, f"record.json not found at: {record_json_path}"
        
        # Read and parse record.json
        try:
            with open(record_json_path, 'r', encoding='utf-8') as f:
                record_data = json.load(f)
        except json.JSONDecodeError as e:
            return False, None, f"Invalid JSON in record.json: {str(e)}"
        except Exception as e:
            return False, None, f"Error reading record.json: {str(e)}"
        
        # Search for the software by name in the items list
        items = record_data.get('items', [])
        folder_id = None
        
        for item in items:
            item_name = item.get('name', '')
            # Case-insensitive comparison
            if item_name.lower() == software_name.lower():
                folder_id = item.get('id')
                break
        
        if folder_id is None:
            return False, None, f"Software '{software_name}' not found in record.json"
        
        # Use BoxLink API to list folder contents
        try:
            api = BoxLinkAPI()
            success, data, error = api.list_folder_dict(folder_id)
            
            if not success:
                return False, None, f"BoxLink API error: {error}"
            
            # Extract items list from the parsed data
            folder_items = data.get('items', [])
            return True, folder_items, ""
            
        except FileNotFoundError as e:
            return False, None, f"BoxAutomate.exe not found: {str(e)}"
        except Exception as e:
            return False, None, f"Error calling BoxLink API: {str(e)}"
        
    except Exception as e:
        return False, None, f"Unexpected error: {str(e)}"


def check_version_is_latest(folder_name: str, record_json_path: Optional[Path] = None) -> Tuple[bool, Optional[bool], str, Optional[str]]:
    """
    Check if the local software version is the latest by comparing with Box folder contents
    
    Args:
        folder_name: Full folder name (e.g., "BandMaster_V-1.0.0.0_A-SuetLi")
        record_json_path: Optional path to record.json
        
    Returns:
        Tuple of (success: bool, is_latest: bool or None, message: str, latest_version: str or None)
        - success: True if check completed successfully
        - is_latest: True if local version is latest, False if update available, None if failed
        - message: Descriptive message about the result
        - latest_version: The latest version found on Box, or None if failed
        
    Example:
        success, is_latest, message, latest_version = check_version_is_latest("BandMaster_V-1.0.0.0_A-SuetLi")
        if success:
            if is_latest:
                print(f"Up to date! {message}")
            else:
                print(f"Update available: {latest_version}")
    """
    try:
        # Parse folder name to get software name and current version
        parsed = parse_software_folder_name(folder_name)
        software_name = format_software_name(parsed)
        current_version = parsed.get('version')
        
        if not current_version:
            return False, None, "Could not extract version from folder name", None
        
        # Get folder contents from Box
        success, items, error = get_folder_contents_by_name(software_name, record_json_path)
        
        if not success:
            return False, None, f"Failed to get Box folder contents: {error}", None
        
        if not items:
            return False, None, "Box folder is empty", None
        
        # Extract versions from all items (files and folders)
        versions = []
        for item in items:
            item_name = item.get('name', '')
            # Try to extract version from item name (look for V- or v- pattern)
            version_match = re.search(r'[Vv]-?(\d+\.\d+\.\d+\.\d+)', item_name)
            if version_match:
                versions.append(version_match.group(1))
        
        if not versions:
            return False, None, "No version information found in Box folder items", None
        
        # Find the latest version
        latest_version = versions[0]
        for version in versions[1:]:
            if compare_versions(version, latest_version) > 0:
                latest_version = version
        
        # Compare current version with latest version
        comparison = compare_versions(current_version, latest_version)
        
        if comparison < 0:
            # Current version is older
            return True, False, f"Update available: v{current_version} → v{latest_version}", latest_version
        elif comparison == 0:
            # Current version is latest
            return True, True, f"Up to date (v{current_version})", latest_version
        else:
            # Current version is newer (shouldn't happen normally)
            return True, True, f"Local version (v{current_version}) is newer than Box (v{latest_version})", latest_version
            
    except Exception as e:
        return False, None, f"Unexpected error: {str(e)}", None
