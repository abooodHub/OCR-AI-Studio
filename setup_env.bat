@echo off
chcp 65001 >nul
setlocal
title OCR-AI Studio - Setup
cd /d "%~dp0"

echo ========================================================
echo                 OCR-AI Studio Setup
echo ========================================================

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python was not found. Install Python 3.10 or newer.
    pause
    exit /b 1
)

python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
    echo [ERROR] Python 3.10 or newer is required.
    pause
    exit /b 1
)

where ffmpeg >nul 2>&1
if errorlevel 1 echo [WARNING] FFmpeg is not available in PATH.
where ffprobe >nul 2>&1
if errorlevel 1 echo [WARNING] FFprobe is not available in PATH.

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 goto :failed
)

echo [2/3] Updating pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :failed

echo [3/3] Installing application dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed

echo.
echo Setup completed successfully. Run run_app.bat to start.
pause
exit /b 0

:failed
echo.
echo [ERROR] Setup failed. Review the output above.
pause
exit /b 1
