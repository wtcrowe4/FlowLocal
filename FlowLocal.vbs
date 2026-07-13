' Silent launcher - no console window. Double-click to start FlowLocal GUI.
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "D:\Claude\Projects\wisprflow-clone"
sh.Run """D:\Claude\Projects\wisprflow-clone\venv\Scripts\pythonw.exe"" ""D:\Claude\Projects\wisprflow-clone\gui_web.py""", 0, False
