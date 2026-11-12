@echo off
setlocal

REM ─── CONFIG — change these if the entry point moves ──────────────────────────
set "MAIN_SCRIPT=src\main.py"
REM ─────────────────────────────────────────────────────────────────────────────

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

REM --- Updates: the app's own start-up dashboard checks GitHub and asks (no console prompt needed). ---
REM --- runtime.exe is only needed for a FIRST install, to download the app and build the .venv. ---
if not exist "%BASE_DIR%.venv\Scripts\python.exe" (
    if exist "%BASE_DIR%runtime.exe" (
        echo First install: downloading the app and setting up the environment...
        "%BASE_DIR%runtime.exe"
        echo Setup done. Launching app...
    )
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

REM --- Launch app WITHOUT a terminal window: pythonw.exe has no console. The app has a Show/Hide Console button, ---
REM --- and until the console is shown its output is written to Log_data\app_output.log. Falls back to python.exe. ---
set PYTHONDONTWRITEBYTECODE=1
if exist "%ENV_DIR%\Scripts\pythonw.exe" (
    start "" "%ENV_DIR%\Scripts\pythonw.exe" "%BASE_DIR%%MAIN_SCRIPT%"
    endlocal
    exit /b 0
)
"%ENV_PYTHON%" "%BASE_DIR%%MAIN_SCRIPT%"

echo.
echo Script complete. Press Enter to exit.
pause >nul
endlocal
