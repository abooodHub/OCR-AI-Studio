@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo [ERROR] Virtual environment not found. Run setup_env.bat first.
    pause
    exit /b 1
)

start "OCR-AI Studio" ".venv\Scripts\pythonw.exe" "main.py"
exit /b 0
