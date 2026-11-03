@echo off
chcp 65001 >nul
cd /d "%~dp0"

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw main.py
    exit /b 0
)

where python >nul 2>nul
if %errorlevel%==0 (
    python main.py
    exit /b 0
)

echo Не найден Python. Установите Python 3.10+ и запустите снова, либо используйте VleyzLauncher.exe
pause
