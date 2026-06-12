"""
BoxLink API - Interface for calling BoxAutomate.exe
"""

import subprocess
import re
from pathlib import Path
from typing import Optional, Tuple, Dict, List, Any


class BoxLinkAPI:
    """Interface for interacting with BoxAutomate.exe"""
    
    def __init__(self, exe_path: Optional[Path] = None):
        """
        Initialize BoxLink API
        
        Args:
            exe_path: Path to BoxAutomate.exe. If None, uses default location.
        """
        if exe_path is None:
            # Default path: BoxLink-API/BoxAutomate.exe
            self.exe_path = Path(__file__).parent.parent.parent / "BoxLink-API" / "BoxAutomate.exe"
        else:
            self.exe_path = Path(exe_path)
        
        if not self.exe_path.exists():
            raise FileNotFoundError(f"BoxAutomate.exe not found at: {self.exe_path}")
    
    def call_command(self, command: str, *args, timeout: Optional[int] = 30) -> Tuple[bool, str, str]:
        """
        Call BoxAutomate.exe with a command and arguments
        
        Args:
            command: The command to execute (e.g., 'getInfoDefault')
            *args: Additional arguments to pass to the command
            timeout: Seconds before the command is killed (default 30).
                     Pass None to wait indefinitely (recommended for downloads).
            
        Returns:
            Tuple of (success: bool, stdout: str, stderr: str)
            - success: True if command executed successfully (exit code 0)
            - stdout: Standard output from the command
            - stderr: Standard error output from the command
            
        Example:
            success, output, error = api.call_command('getInfoDefault')
            if success:
                print(f"Output: {output}")
            else:
                print(f"Error: {error}")
        """
        try:
            # Build command list
            cmd_list = [str(self.exe_path), command]
            if args:
                cmd_list.extend(str(arg) for arg in args)
            
            # Execute command and capture output
            result = subprocess.run(
                cmd_list,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding='utf-8',
                errors='replace'  # Replace invalid characters
            )
            
            success = result.returncode == 0
            stdout = result.stdout.strip()
            stderr = result.stderr.strip()
            
            return success, stdout, stderr
            
        except subprocess.TimeoutExpired:
            limit = f"{timeout} seconds" if timeout else "unlimited"
            return False, "", f"Command timed out ({limit})"
        except Exception as e:
            return False, "", f"Error executing command: {str(e)}"
    
    def _parse_info_output(self, output: str) -> Dict[str, Any]:
        """
        Parse the output from getInfoDefault command into a structured dictionary
        
        Args:
            output: Raw text output from BoxAutomate.exe
            
        Returns:
            Dictionary containing parsed information:
            {
                'folder_id': str,
                'item_count': int,
                'items': [
                    {
                        'name': str,
                        'type': str,
                        'id': str,
                        'size': str,
                        'etag': str,
                        'sequence_id': str,
                        'created_at': str,
                        'modified_at': str,
                        'created_by': str,
                        'modified_by': str
                    },
                    ...
                ],
                'status': str
            }
        """
        result = {
            'folder_id': None,
            'item_count': 0,
            'items': [],
            'status': 'UNKNOWN'
        }
        
        lines = output.split('\n')
        current_item = None
        
        for line in lines:
            line = line.strip()
            
            # Extract status first (to avoid conflict with item parsing)
            if line.startswith('STATUS:'):
                parts = line.split(':', 1)
                if len(parts) == 2:
                    result['status'] = parts[1].strip()
                continue
            
            # Extract folder ID
            if 'Getting info from folder ID:' in line or 'Getting info from default folder ID:' in line:
                match = re.search(r'ID:\s*(\d+)', line)
                if match:
                    result['folder_id'] = match.group(1)
            
            # Extract item count
            elif 'Retrieved' in line and 'items from folder' in line:
                match = re.search(r'Retrieved\s+(\d+)\s+items', line)
                if match:
                    result['item_count'] = int(match.group(1))
            
            # Start of new item
            elif line.startswith('=== Item:'):
                if current_item:
                    result['items'].append(current_item)
                match = re.search(r'=== Item:\s*(.+?)\s*===', line)
                current_item = {
                    'name': match.group(1) if match else '',
                    'type': None,
                    'id': None,
                    'size': None,
                    'etag': None,
                    'sequence_id': None,
                    'created_at': None,
                    'modified_at': None,
                    'created_by': None,
                    'modified_by': None
                }
            
            # Parse item properties
            elif current_item and ':' in line:
                parts = line.split(':', 1)
                if len(parts) == 2:
                    key = parts[0].strip().lower().replace(' ', '_')
                    value = parts[1].strip()
                    
                    # Map keys to item properties
                    if key == 'type':
                        current_item['type'] = value
                    elif key == 'id':
                        current_item['id'] = value
                    elif key == 'name':
                        current_item['name'] = value
                    elif key == 'size':
                        current_item['size'] = None if value == 'N/A' else value
                    elif key == 'etag':
                        current_item['etag'] = value
                    elif key == 'sequenceid':
                        current_item['sequence_id'] = value
                    elif key == 'createdat':
                        current_item['created_at'] = None if value == 'N/A' else value
                    elif key == 'modifiedat':
                        current_item['modified_at'] = None if value == 'N/A' else value
                    elif key == 'createdby':
                        current_item['created_by'] = None if value == 'N/A' else value
                    elif key == 'modifiedby':
                        current_item['modified_by'] = None if value == 'N/A' else value
        
        # Add last item if exists
        if current_item:
            result['items'].append(current_item)
        
        return result
    
    def get_info_default(self) -> Tuple[bool, str, str]:
        """
        Call 'getInfoDefault' command (returns raw output)
        
        Returns:
            Tuple of (success: bool, output: str, error: str)
        """
        return self.call_command('getInfoDefault')
    
    def get_info_default_dict(self) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """
        Call 'getInfoDefault' command and return parsed dictionary
        
        Returns:
            Tuple of (success: bool, data: dict or None, error: str)
            - success: True if command executed successfully
            - data: Parsed dictionary with folder and item information, or None if failed
            - error: Error message if any
            
        Example:
            success, data, error = api.get_info_default_dict()
            if success:
                print(f"Folder ID: {data['folder_id']}")
                print(f"Found {data['item_count']} items")
                for item in data['items']:
                    print(f"  - {item['name']} ({item['type']})")
        """
        success, output, error = self.call_command('getInfoDefault')
        
        if not success:
            return False, None, error
        
        try:
            parsed_data = self._parse_info_output(output)
            return True, parsed_data, ""
        except Exception as e:
            return False, None, f"Error parsing output: {str(e)}"
    
    def get_info(self, folder_id: str) -> Tuple[bool, str, str]:
        """
        Call 'getInfo' command with a specific folder ID (returns raw output)
        
        Args:
            folder_id: The Box folder ID to get information for
            
        Returns:
            Tuple of (success: bool, output: str, error: str)
        """
        return self.call_command('getInfo', folder_id)
    
    def get_info_dict(self, folder_id: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """
        Call 'getInfo' command with a specific folder ID and return parsed dictionary
        
        Args:
            folder_id: The Box folder ID to get information for
            
        Returns:
            Tuple of (success: bool, data: dict or None, error: str)
            - success: True if command executed successfully
            - data: Parsed dictionary with folder and item information, or None if failed
            - error: Error message if any
            
        Example:
            success, data, error = api.get_info_dict('368894919788')
            if success:
                print(f"Folder ID: {data['folder_id']}")
                for item in data['items']:
                    print(f"  - {item['name']} ({item['type']})")
        """
        success, output, error = self.call_command('getInfo', folder_id)
        
        if not success:
            return False, None, error
        
        try:
            parsed_data = self._parse_info_output(output)
            return True, parsed_data, ""
        except Exception as e:
            return False, None, f"Error parsing output: {str(e)}"
    
    def _parse_list_output(self, output: str) -> Dict[str, Any]:
        """
        Parse the output from list command into a structured dictionary
        
        Args:
            output: Raw text output from BoxAutomate.exe list command
            
        Returns:
            Dictionary containing parsed information:
            {
                'folder_id': str,
                'total_items': int,
                'items': [
                    {
                        'type': str,
                        'id': str,
                        'name': str
                    },
                    ...
                ],
                'summary': {
                    'files': int,
                    'folders': int
                },
                'status': str
            }
        """
        result = {
            'folder_id': None,
            'total_items': 0,
            'items': [],
            'summary': {
                'files': 0,
                'folders': 0
            },
            'status': 'UNKNOWN'
        }
        
        lines = output.split('\n')
        in_items_section = False
        
        for line in lines:
            line = line.strip()
            
            # Extract status
            if line.startswith('STATUS:'):
                parts = line.split(':', 1)
                if len(parts) == 2:
                    result['status'] = parts[1].strip()
                continue
            
            # Extract folder ID
            if 'Fetching files from folder ID:' in line:
                match = re.search(r'ID:\s*(\d+)', line)
                if match:
                    result['folder_id'] = match.group(1)
            
            # Extract total items
            elif 'Total items in folder:' in line:
                match = re.search(r'Total items in folder:\s*(\d+)', line)
                if match:
                    result['total_items'] = int(match.group(1))
            
            # Start of items section
            elif line.startswith('=== Files and Folders ==='):
                in_items_section = True
            
            # End of items section
            elif line.startswith('=== Summary ==='):
                in_items_section = False
            
            # Parse items in the list format: Type: folder | ID: 123 | Name: FolderName
            elif in_items_section and line and '|' in line:
                parts = [p.strip() for p in line.split('|')]
                item = {
                    'type': None,
                    'id': None,
                    'name': None
                }
                
                for part in parts:
                    if ':' in part:
                        key, value = part.split(':', 1)
                        key = key.strip().lower()
                        value = value.strip()
                        
                        if key == 'type':
                            item['type'] = value
                        elif key == 'id':
                            item['id'] = value
                        elif key == 'name':
                            item['name'] = value
                
                if item['type'] and item['id']:
                    result['items'].append(item)
            
            # Parse summary
            elif 'Files:' in line and 'Folders:' in line:
                match = re.search(r'Files:\s*(\d+).*?Folders:\s*(\d+)', line)
                if match:
                    result['summary']['files'] = int(match.group(1))
                    result['summary']['folders'] = int(match.group(2))
        
        return result
    
    def list_folder(self, folder_id: str) -> Tuple[bool, str, str]:
        """
        Call 'list' command with a specific folder ID (returns raw output)
        
        Args:
            folder_id: The Box folder ID to list contents for
            
        Returns:
            Tuple of (success: bool, output: str, error: str)
        """
        return self.call_command('list', folder_id)
    
    def list_folder_dict(self, folder_id: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """
        Call 'list' command with a specific folder ID and return parsed dictionary
        
        Args:
            folder_id: The Box folder ID to list contents for
            
        Returns:
            Tuple of (success: bool, data: dict or None, error: str)
            - success: True if command executed successfully
            - data: Parsed dictionary with folder listing, or None if failed
            - error: Error message if any
            
        Example:
            success, data, error = api.list_folder_dict('368894919788')
            if success:
                print(f"Total items: {data['total_items']}")
                print(f"Files: {data['summary']['files']}, Folders: {data['summary']['folders']}")
                for item in data['items']:
                    print(f"  - {item['name']} ({item['type']})")
        """
        success, output, error = self.call_command('list', folder_id)
        
        if not success:
            return False, None, error
        
        try:
            parsed_data = self._parse_list_output(output)
            return True, parsed_data, ""
        except Exception as e:
            return False, None, f"Error parsing output: {str(e)}"
    
    def _parse_download_output(self, output: str) -> Dict[str, Any]:
        """
        Parse the output from download command into a structured dictionary
        
        Args:
            output: Raw text output from BoxAutomate.exe download command
            
        Returns:
            Dictionary containing parsed information:
            {
                'file_id': str,
                'destination': str,
                'attempts': [
                    {
                        'attempt_number': int,
                        'success': bool,
                        'error': str or None
                    },
                    ...
                ],
                'total_attempts': int,
                'status': str,
                'downloaded': bool
            }
        """
        result = {
            'file_id': None,
            'destination': None,
            'attempts': [],
            'total_attempts': 0,
            'status': 'UNKNOWN',
            'downloaded': False
        }
        
        lines = output.split('\n')
        current_attempt = None
        
        for line in lines:
            line = line.strip()
            
            # Extract status
            if line.startswith('STATUS:'):
                parts = line.split(':', 1)
                if len(parts) == 2:
                    status = parts[1].strip()
                    result['status'] = status
                    result['downloaded'] = (status == 'SUCCESS')
                continue
            
            # Start of new attempt
            if line.startswith('=== Download Attempt'):
                if current_attempt:
                    result['attempts'].append(current_attempt)
                
                match = re.search(r'Attempt\s+(\d+)/(\d+)', line)
                if match:
                    current_attempt = {
                        'attempt_number': int(match.group(1)),
                        'success': False,
                        'error': None
                    }
                    result['total_attempts'] = int(match.group(2))
            
            # Extract file ID
            elif line.startswith('File ID:'):
                parts = line.split(':', 1)
                if len(parts) == 2:
                    result['file_id'] = parts[1].strip()
            
            # Extract destination
            elif line.startswith('Destination:'):
                parts = line.split(':', 1)
                if len(parts) == 2:
                    result['destination'] = parts[1].strip()
            
            # Attempt success/failure
            elif current_attempt:
                if 'failed!' in line.lower():
                    current_attempt['success'] = False
                elif 'succeeded!' in line.lower() or 'success!' in line.lower():
                    current_attempt['success'] = True
                
                # Extract error message
                if line.startswith('Error:'):
                    parts = line.split(':', 1)
                    if len(parts) == 2:
                        current_attempt['error'] = parts[1].strip()
        
        # Add last attempt if exists
        if current_attempt:
            result['attempts'].append(current_attempt)
        
        return result
    
    def download(self, file_id: str, destination_path: str) -> Tuple[bool, str, str]:
        """
        Call 'download' command to download a file from Box (returns raw output)
        
        Args:
            file_id: The Box file ID to download
            destination_path: The local path where the file should be downloaded
            
        Returns:
            Tuple of (success: bool, output: str, error: str)
        """
        return self.call_command('download', file_id, destination_path, timeout=None)
    
    def download_dict(self, file_id: str, destination_path: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        r"""
        Call 'download' command and return parsed dictionary
        
        Args:
            file_id: The Box file ID to download
            destination_path: The local path where the file should be downloaded
            
        Returns:
            Tuple of (success: bool, data: dict or None, error: str)
            - success: True if command executed successfully (exit code 0)
            - data: Parsed dictionary with download information, or None if failed
            - error: Error message if any
            
        Example:
            success, data, error = api.download_dict('123456789', r'C:\Downloads')
            if success and data['downloaded']:
                print(f"Downloaded to: {data['destination']}")
            else:
                print(f"Download failed after {data['total_attempts']} attempts")
                for attempt in data['attempts']:
                    if attempt['error']:
                        print(f"  Attempt {attempt['attempt_number']}: {attempt['error']}")
        """
        success, output, error = self.call_command('download', file_id, destination_path, timeout=None)

        if not success and not output:
            return False, None, error
        
        try:
            parsed_data = self._parse_download_output(output)
            return True, parsed_data, ""
        except Exception as e:
            return False, None, f"Error parsing output: {str(e)}"
    
    def is_available(self) -> bool:
        """
        Check if BoxAutomate.exe is available and accessible
        
        Returns:
            bool: True if exe exists and is accessible
        """
        return self.exe_path.exists() and self.exe_path.is_file()


# Convenience functions for quick access
def get_info_default() -> Tuple[bool, str, str]:
    """
    Quick function to call getInfoDefault without creating an API instance
    
    Returns:
        Tuple of (success: bool, output: str, error: str)
        
    Example:
        success, output, error = get_info_default()
        if success:
            print(output)
    """
    try:
        api = BoxLinkAPI()
        return api.get_info_default()
    except FileNotFoundError as e:
        return False, "", str(e)


def get_info_default_dict() -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """
    Quick function to call getInfoDefault and get parsed dictionary
    
    Returns:
        Tuple of (success: bool, data: dict or None, error: str)
        
    Example:
        success, data, error = get_info_default_dict()
        if success:
            for item in data['items']:
                print(f"{item['name']}: {item['type']}")
    """
    try:
        api = BoxLinkAPI()
        return api.get_info_default_dict()
    except FileNotFoundError as e:
        return False, None, str(e)


def get_info_dict(folder_id: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """
    Quick function to call getInfo with folder ID and get parsed dictionary
    
    Args:
        folder_id: The Box folder ID to get information for
        
    Returns:
        Tuple of (success: bool, data: dict or None, error: str)
        
    Example:
        success, data, error = get_info_dict('368894919788')
        if success:
            for item in data['items']:
                print(f"{item['name']}: {item['type']}")
    """
    try:
        api = BoxLinkAPI()
        return api.get_info_dict(folder_id)
    except FileNotFoundError as e:
        return False, None, str(e)


def list_folder_dict(folder_id: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """
    Quick function to call list with folder ID and get parsed dictionary
    
    Args:
        folder_id: The Box folder ID to list contents for
        
    Returns:
        Tuple of (success: bool, data: dict or None, error: str)
        
    Example:
        success, data, error = list_folder_dict('368894919788')
        if success:
            print(f"Total: {data['total_items']} items")
            for item in data['items']:
                print(f"{item['name']}: {item['type']}")
    """
    try:
        api = BoxLinkAPI()
        return api.list_folder_dict(folder_id)
    except FileNotFoundError as e:
        return False, None, str(e)


def download_dict(file_id: str, destination_path: str) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    r"""
    Quick function to call download and get parsed dictionary
    
    Args:
        file_id: The Box file ID to download
        destination_path: The local path where the file should be downloaded
        
    Returns:
        Tuple of (success: bool, data: dict or None, error: str)
        
    Example:
        success, data, error = download_dict('123456789', r'C:\Downloads')
        if success and data['downloaded']:
            print(f"Downloaded successfully to: {data['destination']}")
        else:
            print(f"Download failed: {data['status']}")
    """
    try:
        api = BoxLinkAPI()
        return api.download_dict(file_id, destination_path)
    except FileNotFoundError as e:
        return False, None, str(e)
