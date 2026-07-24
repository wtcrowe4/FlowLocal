' Silent launcher - no console window. Double-click to start FlowLocal GUI.
' Resolves paths relative to this script's own folder (portable across machines).
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = root
sh.Run """" & root & "\venv\Scripts\pythonw.exe"" """ & root & "\gui_web.py""", 0, False
