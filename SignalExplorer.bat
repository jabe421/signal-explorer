@echo off
title Signal Explorer

cd /d "C:\Users\Jabe\Documents\Signal Explorer"

call ".venv\Scripts\activate.bat"

python -m signal_explorer

pause
