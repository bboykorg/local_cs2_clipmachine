@echo off
REM ---------------------------------------------------------------------------
REM  Run CS2 Clip Generator from source.
REM  Creates a virtual environment on first use, then starts the GUI.
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating a virtual environment...
    py -3.11 -m venv .venv 2>nul || python -m venv .venv
    if errorlevel 1 (
        echo.
        echo Could not create a virtual environment.
        echo Install Python 3.11 or newer from https://www.python.org/downloads/
        pause
        exit /b 1
    )
    echo Installing dependencies, this takes a minute...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Installing the dependencies failed. See the output above.
        pause
        exit /b 1
    )
)

REM Pass any argument straight through: a demo path, or "cli" for the CLI.
".venv\Scripts\python.exe" -m cs2_clip_generator %*
if errorlevel 1 pause
endlocal
