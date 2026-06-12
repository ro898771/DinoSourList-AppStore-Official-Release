@echo off
setlocal

REM --- Get the directory of this batch file ---
set "BASE_DIR=%~dp0"

REM --- Create required folders if they do not exist ---
if not exist "%BASE_DIR%Software_Downloaded\" (
    mkdir "%BASE_DIR%Software_Downloaded"
    echo Created Software_Downloaded folder.
)
if not exist "%BASE_DIR%App_Store\" (
    mkdir "%BASE_DIR%App_Store"
    echo Created App_Store folder.
)

REM --- Auto-update: runs in this terminal, waits to fully complete before continuing ---
if exist "%BASE_DIR%runtime.exe" (
    echo Checking for updates...
    "%BASE_DIR%runtime.exe"
    echo Update check done. Launching app...
)

REM --- Define environment paths ---
set "ENV_DIR=%BASE_DIR%.venv"
set "ENV_PYTHON=%ENV_DIR%\Scripts\python.exe"

REM --- If venv still missing after updater ran, show error (runtime.exe should have handled this) ---
if not exist "%ENV_PYTHON%" (
    echo ERROR: Virtual environment not found. runtime.exe may have encountered an error above.
    echo Please check the log above, or run env-Init.cmd manually to set up the environment.
    pause
    exit /b 1
)

REM --- Launch app ---
set PYTHONDONTWRITEBYTECODE=1
"%ENV_PYTHON%" "%BASE_DIR%src\main.py"

echo.
echo Script complete. Press Enter to exit.
pause >nul
endlocal
