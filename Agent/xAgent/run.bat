@echo off
rem ============================================================
rem  xAgent one-click launcher
rem  First run: creates the virtual environment and installs
rem  dependencies; later runs start directly.
rem  Keep this file ASCII-only with CRLF line endings: cmd.exe
rem  mis-parses UTF-8 Chinese text in batch files.
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"

if not exist venv (
    echo [1/3] First run: creating virtual environment venv ...
    python -m venv venv
    if errorlevel 1 (
        echo Failed to create the virtual environment.
        echo Please install Python 3.10+ and check "Add to PATH".
        pause
        exit /b 1
    )
    echo [2/3] Installing dependencies openai / textual ^(Internet required^)...
    venv\Scripts\python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Failed to install dependencies. Please check your network and retry.
        pause
        exit /b 1
    )
) else (
    echo Virtual environment found, skipping setup.
)

rem Put venv\Scripts first on PATH:
rem 1. When the model runs "python" through RunCli it uses this project's venv Python;
rem 2. The venv python.exe is generated locally and avoids the Windows
rem    "revocation server offline" signature-check popup.
set PATH=%~dp0venv\Scripts;%PATH%

echo [3/3] Starting xAgent ^(make sure LM Studio is running^)...
echo.
venv\Scripts\python main.py
pause
