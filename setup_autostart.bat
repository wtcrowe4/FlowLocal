@echo off
REM Adds FlowLocal to Windows startup (current user). Run once.
copy /Y "%~dp0FlowLocal.vbs" "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\FlowLocal.vbs"
if %errorlevel%==0 (echo FlowLocal will now start with Windows.) else (echo Copy failed.)
echo To undo: delete FlowLocal.vbs from shell:startup
pause
