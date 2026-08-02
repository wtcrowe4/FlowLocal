@echo off
REM Kills FlowLocal (GUI or headless). Only target Python hosts, not this command's
REM own PowerShell process whose command line contains these script names.
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -in 'python.exe','pythonw.exe' -and ($_.CommandLine -like '*FlowLocal*gui.py*' -or $_.CommandLine -like '*FlowLocal*gui_web.py*' -or $_.CommandLine -like '*FlowLocal*app.py*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo FlowLocal stopped.
