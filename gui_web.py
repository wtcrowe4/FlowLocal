"""
FlowLocal web GUI - Claude Design HUD on pywebview + standalone always-on-top
pill above the taskbar. True glass: transparent windows + Windows acrylic
blur-behind (same DWM effect as terminal transparency).
Requires: venv\\Scripts\\pip install pywebview
"""

import ctypes
import json
import os
import threading
import time
from ctypes import wintypes
from pathlib import Path

# Pill/HUD panel colors. Per-pixel WebView2 transparency is NOT achievable on
# this stack without WS_EX_LAYERED alpha, which crashes WebView2's GPU
# compositor (took Flow + the terminal down). Instead the host windows are
# opaque dark panels styled to read as glass; these are their form backgrounds.
HUD_BG = "#060C10"   # main window
PILL_BG = "#0A1016"  # pill

import app  # core engine (does NOT load model on import)

try:
    import webview
except ImportError:
    app.log("FATAL: pywebview not installed - run: venv\\Scripts\\pip install pywebview")
    raise

MODELS = ["distil-large-v3", "large-v3-turbo", "medium.en", "small.en"]

win_main = None
win_pill = None

PILL_W, PILL_H = 260, 22


def _single_instance():
    """Refuse to start twice - prevents duplicate pills after a restart
    where the old process never died."""
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW(None, False, "FlowLocal_SingleInstance")
    if k32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        app.log("FlowLocal already running - duplicate exiting (use stop.bat first)")
        os._exit(0)


def js(code, target=None):
    """Broadcast to both windows (each page ignores functions it lacks)."""
    for w in ([target] if target else [win_main, win_pill]):
        try:
            if w:
                w.evaluate_js(code)
        except Exception:
            pass


# ---------------------------------------------------------------- win32 helpers
def _hwnd(title):
    return ctypes.windll.user32.FindWindowW(None, title)


def _hide_from_taskbar(title):
    """Toolwindow style: keeps the pill off the taskbar and out of alt-tab.
    It's still its own window (that's how it floats independently) - just invisible
    as a taskbar entry."""
    try:
        hwnd = _hwnd(title)
        if not hwnd:
            return
        GWL_EXSTYLE = -20
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style = (style | 0x00000080) & ~0x00040000  # +WS_EX_TOOLWINDOW, -WS_EX_APPWINDOW
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                                          0x0001 | 0x0002 | 0x0004 | 0x0020)
    except Exception as e:
        app.log(f"toolwindow failed: {e}")


def _round_pill(title):
    """Clip the pill to a stadium (fully rounded ends) with a GDI region.
    SetWindowRgn is a plain clip - no WS_EX_LAYERED, no alpha blend, so it
    can't touch WebView2's GPU compositor (unlike the alpha path that crashed
    Flow + the terminal). Radius = window height for a lozenge shape."""
    try:
        hwnd = _hwnd(title)
        if not hwnd:
            return
        r = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        rgn = ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, h, h)
        ctypes.windll.user32.SetWindowRgn(hwnd, rgn, True)  # window owns the rgn now
        app.log(f"pill region set: {w}x{h}")
    except Exception as e:
        app.log(f"round pill failed: {e}")


def _round_corners(title, pref=2):  # DWMWCP_ROUND=2, DWMWCP_ROUNDSMALL=3
    """Win11 DWM rounded corners for the main window. DWM-only, no layered
    window - safe for WebView2."""
    try:
        hwnd = _hwnd(title)
        if not hwnd:
            return
        val = ctypes.c_int(pref)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(val),
                                                   ctypes.sizeof(val))
    except Exception as e:
        app.log(f"round corners failed: {e}")


def _apply_acrylic(title, tint=0x55070D10):  # AABBGGRR - light smoke, mostly glass
    """DWM acrylic blur-behind, the same effect terminals use for glass."""
    try:
        hwnd = _hwnd(title)
        if not hwnd:
            return

        class AccentPolicy(ctypes.Structure):
            _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                        ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_int)]

        class WinCompAttrData(ctypes.Structure):
            _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.c_void_p),
                        ("SizeOfData", ctypes.c_size_t)]

        accent = AccentPolicy(4, 2, tint, 0)  # 4 = ACCENT_ENABLE_ACRYLICBLURBEHIND
        data = WinCompAttrData(19, ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p),
                               ctypes.sizeof(accent))
        ctypes.windll.user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data))
        app.log(f"acrylic applied: {title}")
    except Exception as e:
        app.log(f"acrylic failed ({title}): {e}")


# ---------------------------------------------------------------- js api
# curated dropdown - Thomas runs many Ollama models for other projects,
# only these are relevant to dictation (edit ollama_model_choices in config.json)
OLLAMA_CHOICES = ["flowlocal-cleanup", "flowlocal-cleanup-8b",
                  "llama3.2:3b", "llama3.1:8b"]


class Api:
    def init(self):
        try:
            vocab = app.VOCAB_PATH.read_text(encoding="utf-8")
        except FileNotFoundError:
            vocab = ""
        return {"config": app.CFG, "models": MODELS, "vocab": vocab,
                "ollama_models": app.CFG.get("ollama_model_choices", OLLAMA_CHOICES)}

    def toggle(self):
        app._on_toggle()

    def move_by(self, which, dx, dy):
        """Drag: JS streams pointer deltas, we move the window. Native drag
        hand-off (WM_NCLBUTTONDOWN) doesn't work - WebView2 child owns the mouse."""
        w = win_main if which == "main" else win_pill
        try:
            w.move(w.x + int(dx), w.y + int(dy))
        except Exception:
            pass

    def save_pill_pos(self):
        try:
            app.CFG["pill_x"], app.CFG["pill_y"] = win_pill.x, win_pill.y
            app.CONFIG_PATH.write_text(json.dumps(app.CFG, indent=2), encoding="utf-8")
        except Exception:
            pass

    def set_flag(self, key, value):
        """Toggles persist immediately - no SAVE click needed, survive restarts."""
        if key in ("cleanup_enabled", "vault_append_enabled", "save_training_data"):
            app.CFG[key] = bool(value)
            app.CONFIG_PATH.write_text(json.dumps(app.CFG, indent=2), encoding="utf-8")

    def save_config(self, cfg):
        restart = (cfg["whisper_model"] != app.CFG["whisper_model"]
                   or cfg["hold_hotkey"] != app.CFG["hold_hotkey"]
                   or cfg["toggle_hotkey"] != app.CFG["toggle_hotkey"])
        app.CFG.update(cfg)
        app.CONFIG_PATH.write_text(json.dumps(app.CFG, indent=2), encoding="utf-8")
        return {"restart": restart}

    def save_vocab(self, text):
        app.VOCAB_PATH.write_text(text.strip() + "\n", encoding="utf-8")

    def copy_text(self, text):
        import pyperclip
        pyperclip.copy(text)

    def hide(self):
        win_main.hide()  # pill stays - that's the point

    def quit(self):
        _hard_exit()


def _hard_exit():
    try:
        import keyboard
        keyboard.unhook_all()
    except Exception:
        pass
    os._exit(0)


# ---------------------------------------------------------------- workers
def _boot():
    app.load_model(log=lambda m: js(f"onLog({json.dumps(str(m))})", win_main))
    app.warmup(log=lambda m: js(f"onLog({json.dumps(str(m))})", win_main))
    app.setup_hotkeys()
    js("onReady()", win_main)


def _level_pump():
    while True:
        if app.get_state() == app.State.RECORDING:
            lv = [round(x, 4) for x in list(app.recorder.levels)[-14:]]
            js(f"onLevels({json.dumps(lv)})")
        time.sleep(0.05)


def _start_tray():
    import pystray
    icon = pystray.Icon(
        "FlowLocal", app._make_icon(app.State.IDLE), "FlowLocal",
        menu=pystray.Menu(
            pystray.MenuItem("Open", lambda i, m: win_main.show(), default=True),
            pystray.MenuItem("Quit", lambda i, m: _hard_exit()),
        ),
    )
    app._tray_icon = icon
    icon.run_detached()


# ---------------------------------------------------------------- main
def main():
    global win_main, win_pill
    _single_instance()
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("wtcrowe4.FlowLocal")
    except Exception:
        pass

    app.on_event("state", lambda s: js(f"onState({json.dumps(s)})"))
    app.on_event("transcript", lambda t: js(f"onTranscript({json.dumps(t)})"))  # both windows

    web = Path(__file__).parent / "web"
    api = Api()

    win_main = webview.create_window(
        "FlowLocal", str(web / "hud.html"),
        width=566, height=840, frameless=True, easy_drag=False,
        resizable=False, background_color=HUD_BG, js_api=api,
    )

    # pill default: bottom-left, next to the weather widget (draggable, remembered).
    # min_size MUST be passed - pywebview defaults it to (200,100) and clamps the
    # 18px-tall pill up to 100px, which was most of the "gray box".
    sh = ctypes.windll.user32.GetSystemMetrics(1)
    px = app.CFG.get("pill_x") or 240
    py = app.CFG.get("pill_y") or (sh - PILL_H - 3)  # vertically centered in taskbar
    win_pill = webview.create_window(
        "FlowLocal Pill", str(web / "pill.html"),
        width=PILL_W, height=PILL_H, x=px, y=py, min_size=(PILL_W, PILL_H),
        frameless=True, easy_drag=False, resizable=False,
        on_top=True, background_color=PILL_BG, js_api=api,
    )

    def on_start():
        time.sleep(0.6)  # let both windows materialize before touching hwnds
        _round_corners("FlowLocal")
        _round_pill("FlowLocal Pill")
        _hide_from_taskbar("FlowLocal Pill")
        _start_tray()
        threading.Thread(target=_boot, daemon=True).start()
        threading.Thread(target=_level_pump, daemon=True).start()

    webview.start(on_start)
    _hard_exit()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # pythonw is silent - make crashes visible in the log
        app.log(f"FATAL: {type(e).__name__}: {e}")
        raise
