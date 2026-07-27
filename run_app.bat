@echo off
chcp 65001 >nul
title OCR-AI
cd /d "%~dp0"

if exist .venv\Scripts\activate.bat (
    call .venv\Scripts\activate.bat
)

:: Launch main.pyw directly
start "" main.pyw
exit
