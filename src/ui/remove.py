import os
import sys

def remove_import_resources(file_path):
    try:
        # Check if file exists first
        if not os.path.exists(file_path):
            print(f"Error: File not found at {file_path}")
            return

        # Read the content of the file with error handling for encoding
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                lines = file.readlines()
        except UnicodeDecodeError:
            with open(file_path, 'r', encoding='utf-8-sig') as file:
                lines = file.readlines()
        
        # Remove lines containing "import resource_rc"
        updated_lines = [line for line in lines if "import resource_rc" not in line]
        
        # Write the updated content back to the file
        with open(file_path, 'w', encoding='utf-8') as file:
            file.writelines(updated_lines)
        
        print(f"Success: Removed 'import resource_rc' from {file_path}")

    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    # Check if the batch file sent a file path
    if len(sys.argv) > 1:
        target_file = sys.argv[1]
        remove_import_resources(target_file)
    else:
        print("No file specified. Usage: python remove.py <path_to_file>")
