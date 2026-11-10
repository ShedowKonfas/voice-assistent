@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Запуск GLaDOS в режиме отладки (консоль + подробный лог)...
if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" "main.py" --debug %*
) else (
    python "main.py" --debug %*
)
pause
