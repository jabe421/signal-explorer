@echo off
title Signal Explorer

cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Python environment not found. Follow the Install Guide to create .venv.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m signal_explorer

pause
