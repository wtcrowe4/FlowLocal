@echo off
REM Old tkinter GUI, kept as fallback.
cd /d "%~dp0"
call venv\Scripts\activate.bat
python gui.py
pause
