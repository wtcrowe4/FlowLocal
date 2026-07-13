@echo off
cd /d "%~dp0"
call venv\Scripts\activate.bat
pip show pywebview >nul 2>nul || pip install pywebview -q
python gui_web.py
pause
