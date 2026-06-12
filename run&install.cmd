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

REM --- Auto-update: waits for runtime.exe to fully complete before continuing ---
if exist "%BASE_DIR%runtime.exe" (
    echo Checking for updates...
    start /wait "" "%BASE_DIR%runtime.exe"
    echo Update check done. Launching app...
)

REM --- Define environment paths ---
set "ENV_DIR=%BASE_DIR%.venv"
set "ENV_PYTHON=%ENV_DIR%\Scripts\python.exe"

REM --- If venv still missing after updater ran, run install manually ---
if not exist "%ENV_PYTHON%" (
    echo Virtual environment not found. Running install...
    if exist "%BASE_DIR%env-Init.cmd" (
        call "%BASE_DIR%env-Init.cmd"
    ) else (
        echo ERROR: env-Init.cmd not found. Please run env-Init.cmd manually.
        pause
        exit /b 1
    )
)

REM --- Launch app ---
set PYTHONDONTWRITEBYTECODE=1
"%ENV_PYTHON%" "%BASE_DIR%src\main.py"

echo.
echo Script complete. Press Enter to exit.
pause >nul
endlocal
