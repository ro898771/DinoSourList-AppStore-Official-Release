import os
import sys
from win32com.client import Dispatch

def create_shortcut():
    # Get the directory of the current script
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # Define paths for the target and icon files
    target_path = os.path.join(script_dir, "run&install.cmd")
    icon_path = os.path.join(script_dir, "IcoFolder", "last3.ico")

    # Get the desktop directory
    desktop = os.path.join(os.environ["USERPROFILE"], "Desktop")

    # Define the shortcut path
    shortcut_path = os.path.join(desktop, "DinoSaurList.lnk")

    # Create the shortcut
    shell = Dispatch("WScript.Shell")
    shortcut = shell.CreateShortcut(shortcut_path)
    # Start the app directly with pythonw.exe (no console window at all). Going through run&install.cmd made cmd.exe
    # flash a black window before the GUI appeared. The .cmd stays the fallback if pythonw is not there.
    pythonw = os.path.join(script_dir, ".venv", "Scripts", "pythonw.exe")
    main_py = os.path.join(script_dir, "src", "main.py")
    if os.path.exists(pythonw) and os.path.exists(main_py):
        shortcut.TargetPath = pythonw
        shortcut.Arguments = f'"{main_py}"'
    else:
        shortcut.TargetPath = target_path
    shortcut.IconLocation = icon_path
    shortcut.WorkingDirectory = script_dir
    shortcut.Save()

    print(f"Shortcut created successfully at: {shortcut_path}")

if __name__ == "__main__":
    create_shortcut()
