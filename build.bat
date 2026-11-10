@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Сборка Glados.exe...
if not exist "venv\Scripts\pyinstaller.exe" (
    echo Не найден pyinstaller в venv. Установите зависимости: pip install -r requirements.txt
    pause
    exit /b 1
)
"venv\Scripts\pyinstaller.exe" --noconfirm --clean "Glados.spec"
pause
