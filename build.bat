@echo off
REM ---------------------------------------------------------------------------
REM  Build dist\CS2ClipGenerator.exe with PyInstaller.
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Setting up the environment first...
    call run.bat --version
)

echo Installing build dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements-dev.txt
if errorlevel 1 goto :failed

echo Running the test suite...
".venv\Scripts\python.exe" -m pytest tests -q
if errorlevel 1 (
    echo.
    echo Tests failed. Fix them before shipping a build.
    goto :failed
)

echo Building CS2ClipGenerator.exe...
".venv\Scripts\python.exe" -m PyInstaller CS2ClipGenerator.spec --noconfirm --clean
if errorlevel 1 goto :failed

echo.
echo Done: dist\CS2ClipGenerator.exe
echo FFmpeg is not bundled; the app finds it on PATH or asks for it in Settings.
pause
endlocal
exit /b 0

:failed
echo.
echo Build failed.
pause
endlocal
exit /b 1
