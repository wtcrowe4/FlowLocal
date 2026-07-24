@echo off
REM Kills FlowLocal (GUI or headless). Safe: only targets processes running gui.py/app.py from this folder.
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*FlowLocal*gui.py*' -or $_.CommandLine -like '*FlowLocal*gui_web.py*' -or $_.CommandLine -like '*FlowLocal*app.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo FlowLocal stopped.
