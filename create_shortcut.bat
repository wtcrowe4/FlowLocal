@echo off
REM Creates Desktop + Start Menu shortcuts with proper icon and app identity.
cd /d "%~dp0"
call venv\Scripts\activate.bat

pip show pywin32 >nul 2>nul || pip install pywin32 -q

python make_icon.py
python make_shortcut.py

ie4uinit.exe -show
pause
