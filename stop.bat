@echo off
REM Kills FlowLocal (GUI or headless). Safe: only targets processes running gui.py/app.py from this folder.
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*wisprflow-clone*gui.py*' -or $_.CommandLine -like '*wisprflow-clone*app.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo FlowLocal stopped.
