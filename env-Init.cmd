@echo off
setlocal

REM ─── Define Environment Paths Relative to Script Location ────────────────────
set "BASE_DIR=%~dp0"
set "ENV_DIR=%BASE_DIR%.venv"
set "ENV_PATH=%ENV_DIR%\Scripts\activate"
set "ENV_PYTHON=%ENV_DIR%\Scripts\python.exe"

REM ─── Check if UV Is Installed ─────────────────────────────────────────────────
where uv >nul 2>nul
if errorlevel 1 (
    echo UV not found. Installing UV...
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

    REM ─── Refresh Environment Variables After UV Installation ─────────────────
    echo Refreshing environment variables...
    call refreshenv >nul 2>nul

    REM ─── Add UV to PATH for current session ──────────────────────────────────
    set "PATH=%USERPROFILE%\.local\bin;%USERPROFILE%\.cargo\bin;%PATH%"

    REM ─── Verify UV Installation ───────────────────────────────────────────────
    where uv >nul 2>nul
    if errorlevel 1 (
        echo ERROR: UV installation failed or UV is not in PATH.
        exit /b 1
    ) else (
        echo UV installed successfully and is now available.
    )
) else (
    echo UV is already installed.
)

REM ─── Check if Virtual Environment Exists ──────────────────────────────────────
if not exist "%ENV_DIR%" (
    echo Virtual environment not found. Creating virtual environment...
    uv venv "%ENV_DIR%" --python 3.12.1 --index-url https://usw2p-edge.packages.broadcom.net/artifactory/api/pypi/platform-pypi/simple
)

REM ─── Verify Creation ──────────────────────────────────────────────────────────
if exist "%ENV_PYTHON%" (
    echo Virtual environment ready.

    REM ─── Install/Sync Requirements from project root ──────────────────────────
    echo Syncing dependencies...
    pushd "%BASE_DIR%"
    uv sync
    popd

    echo Install complete.
) else (
    echo ERROR: Failed to create virtual environment.
    exit /b 1
)

REM ─── Verify Creation ─────────────────────────────────────────────────────────
if exist "%ENV_PYTHON%" (
    echo Virtual environment created successfully.

    REM ─── Install Requirements Using uv pip ────────────────────────────────────
    echo Installing requirements...
    pushd "%ENV_DIR%"
	uv sync
	popd

    REM ─── Run Python Script ───────────────────────────────────────────────────
    echo Running shortcut.py...
    "%ENV_PYTHON%" "%BASE_DIR%shortcut.py"
) else (
    echo Failed to create virtual environment. Please check for errors.
)

endlocal
exit /b 0
