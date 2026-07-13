"""Creates FlowLocal shortcuts (Desktop + Start Menu) with the AppUserModelID
baked in. This is what makes taskbar pinning keep the mic icon instead of
falling back to Python's - Windows matches the running window's AUMID to a
Start Menu shortcut with the same AUMID and uses ITS icon.
Requires pywin32. Run via create_shortcut.bat.
"""

import os
import sys
from pathlib import Path

import win32com.client
from win32com.propsys import propsys, pscon

AUMID = "wtcrowe4.FlowLocal"
ROOT = Path(__file__).parent.resolve()
PYTHONW = ROOT / "venv" / "Scripts" / "pythonw.exe"
ICON = ROOT / "icon.ico"


def make_lnk(lnk_path: str):
    ws = win32com.client.Dispatch("WScript.Shell")
    lnk = ws.CreateShortcut(lnk_path)
    lnk.TargetPath = str(PYTHONW)
    lnk.Arguments = f'"{ROOT / "gui_web.py"}"'
    lnk.WorkingDirectory = str(ROOT)
    lnk.IconLocation = str(ICON)
    lnk.Description = "FlowLocal - local dictation"
    lnk.Save()
    # bake the AppUserModelID into the shortcut's property store
    store = propsys.SHGetPropertyStoreFromParsingName(
        lnk_path, None, 2, propsys.IID_IPropertyStore)  # 2 = GPS_READWRITE
    store.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(AUMID))
    store.Commit()
    print(f"created: {lnk_path}")


if __name__ == "__main__":
    if not ICON.exists():
        sys.exit("icon.ico missing - run make_icon.py first")
    desktop = os.path.join(os.environ["USERPROFILE"], "Desktop", "FlowLocal.lnk")
    startmenu = os.path.join(os.environ["APPDATA"],
                             r"Microsoft\Windows\Start Menu\Programs", "FlowLocal.lnk")
    make_lnk(desktop)
    make_lnk(startmenu)
    print("Done. Unpin any old FlowLocal pin, restart the app, then pin again")
    print("(from the desktop shortcut or the running taskbar icon - both work now).")
