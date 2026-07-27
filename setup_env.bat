@echo off
chcp 65001 >nul
title Sub-AI Master Pro - تثبيت المتطلبات
echo ========================================================
echo       Sub-AI Master Pro - جاري تثبيت المتطلبات
echo ========================================================
echo.

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [خطأ] لم يتم العثور على Python مثبت في جهازك!
    echo يرجى تثبيت Python 3.10 أو أحدث وتفعيله في المتغيرات البيئية (PATH).
    pause
    exit /b 1
)

if not exist .venv (
    echo [+] جاري إنشاء البيئة الافتراضية (.venv)...
    python -m venv .venv
)

echo [+] تفعيل البيئة الافتراضية وترقية pip...
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip

echo [+] جاري تثبيت الحزم المطلوبة من requirements.txt...
pip install -r requirements.txt

echo.
echo ========================================================
echo   ✅ اكتمل التثبيت بنجاح! يمكنك الآن تشغيل run_app.bat
echo ========================================================
pause
