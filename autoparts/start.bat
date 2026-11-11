@echo off
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python не знайдено. Встановіть його з https://www.python.org/downloads/
  echo Під час встановлення поставте галочку "Add python.exe to PATH".
  pause
  exit /b 1
)

echo Встановлення бібліотек...
python -m pip install -q -r requirements.txt
if errorlevel 1 ( pause & exit /b 1 )

if not exist data\shop.db (
  echo Завантаження демо-даних...
  python seed_demo.py
)

if "%ADMIN_PASSWORD%"=="" set ADMIN_PASSWORD=admin
if "%SECRET_KEY%"=="" set SECRET_KEY=local-dev-key

echo.
echo Сайт:    http://localhost:8000
echo Адмінка: http://localhost:8000/admin   (пароль: %ADMIN_PASSWORD%)
echo Щоб зупинити — закрийте це вікно або натисніть Ctrl+C
echo.
start "" http://localhost:8000
python -m uvicorn app.main:app --port 8000
pause
