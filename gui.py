"""
FlowLocal GUI - local dictation, Wispr Flow style.
Rendered skin: PIL-generated brushed-metal background, panel shadows,
blue glow seams. Waveform overlay pill. Alienware-blue accents.
"""

import ctypes
import json
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageTk

import app  # core engine (does NOT load model on import)

# ---------------------------------------------------------------- theme
W, H = 560, 800
BG = "#15161a"
HEADER = "#1e2026"     # must match header fill in _render_bg
CARD = "#202228"       # must match panel fill in _render_bg
PANEL = "#1a1c21"
EDGE = "#3a3d45"
FG = "#dfe2e7"
MUTED = "#8b8f98"
ACCENT = "#00a8ff"
ACCENT_RGB = (0, 168, 255)
ACCENT_DIM = "#0077b6"
RED = "#ff4d5a"
YELLOW = "#f0b83a"
GREEN = "#39d98a"
FONT = "Segoe UI"

MODELS = [
    "distil-large-v3",
    "large-v3-turbo",
    "large-v3",
    "medium.en",
    "small.en",
    "base.en",
]

_events = queue.Queue()  # thread-safe bridge: engine threads -> Tk thread


def _dark_titlebar(win):
    if sys.platform != "win32":
        return
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        val = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), 4)
    except Exception:
        pass


# ---------------------------------------------------------------- rendered skin
def _render_bg(hold, toggle, ask=""):
    """Brushed gunmetal background with floating panels, shadows, glow seams."""
    rng = np.random.default_rng(7)
    small = rng.random((H, max(2, W // 14))).astype(np.float32)
    noise = np.asarray(
        Image.fromarray((small * 255).astype(np.uint8)).resize((W, H), Image.BILINEAR),
        dtype=np.float32) / 255.0
    yy = np.linspace(0, 1, H)[:, None]
    r = 16 + 10 * yy + (noise - 0.5) * 9
    g = 17 + 11 * yy + (noise - 0.5) * 9
    b = 21 + 15 * yy + (noise - 0.5) * 12
    xs = np.linspace(-1, 1, W)[None, :]
    ys = np.linspace(-1, 1, H)[:, None]
    vig = 1 - 0.24 * ((xs ** 2 + ys ** 2) / 2) ** 1.2
    arr = np.clip(np.stack([r * vig, g * vig, b * vig], -1), 0, 255).astype(np.uint8)
    img = Image.fromarray(arr, "RGB").convert("RGBA")

    # header strip + glowing accent seam
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 64], fill=(30, 32, 38, 255))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).line([(0, 65), (W, 65)], fill=ACCENT_RGB + (235,), width=2)
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(3)))

    def panel(x1, y1, x2, y2):
        # drop shadow
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle([x1 + 3, y1 + 6, x2 + 3, y2 + 6],
                                             radius=14, fill=(0, 0, 0, 150))
        img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(8)))
        # soft blue rim glow
        gl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(gl).rounded_rectangle([x1, y1, x2, y2], radius=14,
                                             outline=ACCENT_RGB + (90,), width=2)
        img.alpha_composite(gl.filter(ImageFilter.GaussianBlur(5)))
        # plate
        pd = ImageDraw.Draw(img)
        pd.rounded_rectangle([x1, y1, x2, y2], radius=14,
                             fill=(32, 34, 40, 255), outline=(58, 61, 69, 255), width=1)
        pd.line([(x1 + 14, y1 + 1), (x2 - 14, y1 + 1)], fill=(80, 84, 96, 255), width=1)

    panel(18, 108, 542, 486)   # history
    panel(18, 548, 542, 764)   # settings

    try:
        f_lbl = ImageFont.truetype("C:/Windows/Fonts/seguisb.ttf", 12)
        f_hint = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 11)
    except Exception:
        f_lbl = f_hint = ImageFont.load_default()
    d = ImageDraw.Draw(img)
    ask_hint = ask.upper() if ask else f"2x TAP {hold.upper()}"
    d.text((26, 74), f"HOLD [{hold.upper()}] TALK   TOGGLE [{toggle.upper()}]   ASK [{ask_hint}]",
           font=f_hint, fill=(139, 143, 152, 255))
    d.text((26, 88), "TRANSCRIPT HISTORY", font=f_lbl, fill=ACCENT_RGB + (255,))
    d.text((26, 526), "SETTINGS", font=f_lbl, fill=ACCENT_RGB + (255,))
    return img


class FlowGui:
    def __init__(self):
        # own taskbar identity (otherwise groups under python.exe icon when pinned)
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("wtcrowe4.FlowLocal")
        except Exception:
            pass

        self.root = tk.Tk()
        self.root.title("FlowLocal")
        self.root.geometry(f"{W}x{H}")
        self.root.resizable(False, False)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        _dark_titlebar(self.root)

        try:
            ico = app.CONFIG_PATH.parent / "icon.ico"
            if ico.exists():
                self.root.iconbitmap(str(ico))
            else:
                self._icon_img = ImageTk.PhotoImage(app._make_icon(app.State.IDLE))
                self.root.iconphoto(True, self._icon_img)
        except Exception:
            pass

        self.ready = False
        self._style()
        self._build_ui()
        self._build_overlay()
        self._start_tray()

        app.on_event("state", lambda s: _events.put(("state", s)))
        app.on_event("transcript", lambda t: _events.put(("transcript", t)))

        threading.Thread(target=self._boot_engine, daemon=True).start()
        self.root.after(80, self._poll_events)

    # ------------------------------------------------------------ engine
    def _boot_engine(self):
        try:
            app.load_model(log=lambda m: _events.put(("log", m)))
            app.warmup(log=lambda m: _events.put(("log", m)))
            app.setup_hotkeys()
            _events.put(("ready", None))
        except Exception as e:
            _events.put(("log", f"Engine failed: {e}"))

    # ------------------------------------------------------------ styling
    def _style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TCombobox",
                        fieldbackground=PANEL, background=CARD, foreground=FG,
                        arrowcolor=ACCENT, bordercolor=EDGE, lightcolor=EDGE,
                        darkcolor=EDGE, insertcolor=FG, selectbackground=PANEL,
                        selectforeground=FG)
        style.map("TCombobox",
                  fieldbackground=[("readonly", PANEL), ("active", PANEL)],
                  foreground=[("readonly", FG)],
                  bordercolor=[("focus", ACCENT)])
        self.root.option_add("*TCombobox*Listbox.background", PANEL)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#0b0c0e")
        style.configure("TCheckbutton", background=CARD, foreground=FG, focuscolor=CARD)
        style.map("TCheckbutton",
                  background=[("active", CARD)],
                  indicatorcolor=[("selected", ACCENT), ("!selected", EDGE)])

    def _button(self, parent, text, cmd, primary=False, **kw):
        return tk.Button(parent, text=text, command=cmd, relief="flat", bd=0,
                         bg=ACCENT if primary else PANEL,
                         fg="#0b0c0e" if primary else FG,
                         activebackground=ACCENT_DIM if primary else EDGE,
                         activeforeground="#ffffff",
                         font=(FONT, 10, "bold") if primary else (FONT, 9),
                         padx=14, pady=5, cursor="hand2", **kw)

    # ------------------------------------------------------------ ui
    def _build_ui(self):
        # rendered background
        self._bg_img = ImageTk.PhotoImage(
            _render_bg(app.CFG["hold_hotkey"], app.CFG["toggle_hotkey"],
                       app.CFG.get("ask_hotkey", "")))
        self.canvas = tk.Canvas(self.root, width=W, height=H, highlightthickness=0, bd=0)
        self.canvas.place(x=0, y=0)
        self.canvas.create_image(0, 0, anchor="nw", image=self._bg_img)
        self.footer_id = self.canvas.create_text(
            26, 780, anchor="w", fill=MUTED, font=("Consolas", 8), text="")

        # ---- header (flat strip drawn in bg)
        header = tk.Frame(self.root, bg=HEADER)
        header.place(x=0, y=0, width=W, height=64)
        tk.Label(header, text="FLOW", bg=HEADER, fg=FG,
                 font=(FONT, 15, "bold")).pack(side="left", padx=(20, 0), pady=12)
        tk.Label(header, text="LOCAL", bg=HEADER, fg=ACCENT,
                 font=(FONT, 15, "bold")).pack(side="left")

        self.rec_btn = self._button(header, "●  START", self._toggle, primary=True,
                                    state="disabled")
        self.rec_btn.pack(side="right", padx=16, pady=10)

        self.status_frame = tk.Frame(header, bg=PANEL, padx=10, pady=3)
        self.status_frame.pack(side="right", pady=10)
        self.dot = tk.Canvas(self.status_frame, width=10, height=10, bg=PANEL,
                             highlightthickness=0)
        self._dot_id = self.dot.create_oval(1, 1, 9, 9, fill=MUTED, outline="")
        self.dot.pack(side="left")
        self.status_lbl = tk.Label(self.status_frame, text="LOADING", bg=PANEL,
                                   fg=MUTED, font=(FONT, 8, "bold"))
        self.status_lbl.pack(side="left", padx=(6, 0))

        # ---- history panel interior
        hist = tk.Frame(self.root, bg=CARD)
        hist.place(x=28, y=118, width=504, height=358)
        bar = tk.Frame(hist, bg=CARD)
        bar.pack(side="bottom", fill="x", pady=(4, 2))
        self._button(bar, "Copy last", self._copy_last).pack(side="left", padx=(6, 0))
        self._button(bar, "Clear", self._clear_history).pack(side="left", padx=6)
        self.history = tk.Text(hist, bg=CARD, fg=FG, relief="flat", wrap="word",
                               font=(FONT, 10), padx=10, pady=8, state="disabled",
                               insertbackground=FG, selectbackground=ACCENT_DIM)
        scroll = tk.Scrollbar(hist, command=self.history.yview, troughcolor=CARD,
                              bg=PANEL, activebackground=EDGE, relief="flat", width=10)
        self.history.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.history.pack(fill="both", expand=True)
        self.history.tag_configure("time", foreground=ACCENT, font=("Consolas", 8))
        self.last_text = ""

        # ---- settings panel interior
        s = tk.Frame(self.root, bg=CARD)
        s.place(x=28, y=558, width=504, height=196)
        s.columnconfigure(1, weight=1)

        def row(r, label):
            tk.Label(s, text=label, bg=CARD, fg=FG, font=(FONT, 9)).grid(
                row=r, column=0, sticky="w", padx=12, pady=3)

        def entry(var, width=24):
            return tk.Entry(s, textvariable=var, bg=PANEL, fg=FG, relief="flat",
                            insertbackground=ACCENT, width=width,
                            highlightthickness=1, highlightbackground=EDGE,
                            highlightcolor=ACCENT)

        row(0, "Whisper model")
        self.model_var = tk.StringVar(value=app.CFG["whisper_model"])
        ttk.Combobox(s, textvariable=self.model_var, values=MODELS, width=25).grid(
            row=0, column=1, sticky="e", padx=12)

        row(1, "Hold-to-talk key")
        self.hold_var = tk.StringVar(value=app.CFG["hold_hotkey"])
        entry(self.hold_var).grid(row=1, column=1, sticky="e", padx=12)

        row(2, "Toggle hotkey")
        self.toggle_var = tk.StringVar(value=app.CFG["toggle_hotkey"])
        entry(self.toggle_var).grid(row=2, column=1, sticky="e", padx=12)

        row(3, "AI cleanup (Ollama)")
        self.cleanup_var = tk.BooleanVar(value=app.CFG["cleanup_enabled"])
        ttk.Checkbutton(s, variable=self.cleanup_var, command=self._apply_cleanup).grid(
            row=3, column=1, sticky="e", padx=12)

        row(4, "Ollama model")
        self.ollama_var = tk.StringVar(value=app.CFG["ollama_model"])
        entry(self.ollama_var).grid(row=4, column=1, sticky="e", padx=12)

        row(5, "Vault log  /  training data")
        misc = tk.Frame(s, bg=CARD)
        misc.grid(row=5, column=1, sticky="e", padx=12)
        self.vault_var = tk.BooleanVar(value=app.CFG.get("vault_append_enabled", False))
        ttk.Checkbutton(misc, variable=self.vault_var,
                        command=self._apply_toggles).pack(side="left", padx=(0, 14))
        self.train_var = tk.BooleanVar(value=app.CFG.get("save_training_data", False))
        ttk.Checkbutton(misc, variable=self.train_var,
                        command=self._apply_toggles).pack(side="left")

        bot = tk.Frame(s, bg=CARD)
        bot.grid(row=6, column=0, columnspan=2, sticky="ew", padx=12, pady=6)
        self._button(bot, "VOCABULARY", self._edit_vocab).pack(side="left")
        self._button(bot, "SAVE", self._save, primary=True).pack(side="right")

    def _set_footer(self, text):
        self.canvas.itemconfig(self.footer_id, text=str(text)[:110])

    # ------------------------------------------------------------ overlay pill
    OV_W, OV_H = 240, 56
    N_BARS = 22

    def _build_overlay(self):
        """Wispr-style floating pill: rounded transparent window, live sound bars."""
        self.overlay = tk.Toplevel(self.root)
        self.overlay.overrideredirect(True)
        self.overlay.attributes("-topmost", True)
        TRANS = "#ff00fe"
        try:
            self.overlay.attributes("-transparentcolor", TRANS)
        except tk.TclError:
            TRANS = "#0e0f11"
        self.ov_canvas = tk.Canvas(self.overlay, width=self.OV_W, height=self.OV_H,
                                   bg=TRANS, highlightthickness=0)
        self.ov_canvas.pack()
        self._round_rect(self.ov_canvas, 2, 2, self.OV_W - 2, self.OV_H - 2,
                         r=self.OV_H // 2 - 2, fill="#0e0f11", outline=ACCENT, width=1)
        sw = self.overlay.winfo_screenwidth()
        sh = self.overlay.winfo_screenheight()
        self.overlay.geometry(
            f"{self.OV_W}x{self.OV_H}+{sw // 2 - self.OV_W // 2}+{sh - 130}")
        self.overlay.withdraw()
        self._bar_phase = 0.0
        self.root.after(50, self._animate_overlay)

    @staticmethod
    def _round_rect(c, x1, y1, x2, y2, r, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
               x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return c.create_polygon(pts, smooth=True, **kw)

    def _animate_overlay(self):
        state = app.get_state()
        if state != app.State.IDLE:
            c = self.ov_canvas
            c.delete("bars")
            pad_x = 26
            span = self.OV_W - pad_x * 2
            step = span / (self.N_BARS - 1)
            mid = self.OV_H / 2
            if state == app.State.RECORDING:
                levels = list(app.recorder.levels)[-self.N_BARS:]
                levels = [0.0] * (self.N_BARS - len(levels)) + levels
                for i, rms in enumerate(levels):
                    h = max(2.5, min(1.0, (rms * 7) ** 0.6) * (mid - 8))
                    x = pad_x + i * step
                    c.create_line(x, mid - h, x, mid + h, fill=ACCENT, width=3,
                                  capstyle="round", tags="bars")
            else:  # PROCESSING: yellow sine ripple
                import math
                self._bar_phase += 0.45
                for i in range(self.N_BARS):
                    h = 3 + 6 * (1 + math.sin(self._bar_phase - i * 0.55)) / 2
                    x = pad_x + i * step
                    c.create_line(x, mid - h, x, mid + h, fill=YELLOW, width=3,
                                  capstyle="round", tags="bars")
        self.root.after(50, self._animate_overlay)

    # ------------------------------------------------------------ tray
    def _start_tray(self):
        import pystray
        self.tray = pystray.Icon(
            "FlowLocal", app._make_icon(app.State.IDLE), "FlowLocal",
            menu=pystray.Menu(
                pystray.MenuItem("Open", lambda i, m: _events.put(("show", None)), default=True),
                pystray.MenuItem("Quit", lambda i, m: _events.put(("quit", None))),
            ),
        )
        app._tray_icon = self.tray
        self.tray.run_detached()

    def hide_to_tray(self):
        self.root.withdraw()

    # ------------------------------------------------------------ actions
    def _toggle(self):
        app._on_toggle()

    def _apply_cleanup(self):
        app.CFG["cleanup_enabled"] = self.cleanup_var.get()

    def _apply_toggles(self):
        app.CFG["vault_append_enabled"] = self.vault_var.get()
        app.CFG["save_training_data"] = self.train_var.get()

    def _save(self):
        cfg = app.CFG
        needs_restart = (cfg["whisper_model"] != self.model_var.get().strip()
                         or cfg["hold_hotkey"] != self.hold_var.get().strip()
                         or cfg["toggle_hotkey"] != self.toggle_var.get().strip())
        cfg["whisper_model"] = self.model_var.get().strip()
        cfg["hold_hotkey"] = self.hold_var.get().strip()
        cfg["toggle_hotkey"] = self.toggle_var.get().strip()
        cfg["cleanup_enabled"] = self.cleanup_var.get()
        cfg["ollama_model"] = self.ollama_var.get().strip()
        cfg["vault_append_enabled"] = self.vault_var.get()
        cfg["save_training_data"] = self.train_var.get()
        app.CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        if needs_restart:
            messagebox.showinfo("FlowLocal",
                                "Saved. Restart the app to apply model/hotkey changes.")
        else:
            self._set_footer("Settings saved.")

    def _edit_vocab(self):
        win = tk.Toplevel(self.root)
        win.title("Vocabulary")
        win.geometry("380x460")
        win.configure(bg=BG)
        _dark_titlebar(win)
        tk.Label(win, text="CUSTOM VOCABULARY", bg=BG, fg=ACCENT,
                 font=(FONT, 8, "bold")).pack(anchor="w", padx=14, pady=(12, 2))
        tk.Label(win, text="One term per line. Names, jargon, acronyms.\n"
                           "Applies on next dictation - no restart needed.",
                 bg=BG, fg=MUTED, font=(FONT, 8), justify="left").pack(anchor="w", padx=14)
        txt = tk.Text(win, bg=CARD, fg=FG, relief="flat", font=("Consolas", 10),
                      padx=10, pady=8, insertbackground=ACCENT)
        txt.pack(fill="both", expand=True, padx=14, pady=8)
        try:
            txt.insert("1.0", app.VOCAB_PATH.read_text(encoding="utf-8"))
        except FileNotFoundError:
            pass

        def save():
            app.VOCAB_PATH.write_text(txt.get("1.0", "end").strip() + "\n",
                                      encoding="utf-8")
            win.destroy()
            self._set_footer("Vocabulary saved.")

        self._button(win, "SAVE", save, primary=True).pack(pady=(0, 12))

    def _copy_last(self):
        if self.last_text:
            import pyperclip
            pyperclip.copy(self.last_text)
            self._set_footer("Copied.")

    def _clear_history(self):
        self.history.config(state="normal")
        self.history.delete("1.0", "end")
        self.history.config(state="disabled")

    # ------------------------------------------------------------ event pump
    def _poll_events(self):
        try:
            while True:
                kind, payload = _events.get_nowait()
                if kind == "state":
                    self._on_state(payload)
                elif kind == "transcript":
                    self._on_transcript(payload)
                elif kind == "log":
                    self._set_footer(payload)
                elif kind == "ready":
                    self.ready = True
                    self._on_state(app.State.IDLE)
                    self.rec_btn.config(state="normal")
                elif kind == "show":
                    self.root.deiconify()
                    self.root.lift()
                elif kind == "quit":
                    self._quit()
        except queue.Empty:
            pass
        self.root.after(80, self._poll_events)

    def _on_state(self, state):
        if state == app.State.RECORDING:
            self.dot.itemconfig(self._dot_id, fill=RED)
            self.status_lbl.config(text="LISTENING", fg=RED)
            self.rec_btn.config(text="■  STOP", bg=RED, fg="#ffffff")
            self.overlay.deiconify()
        elif state == app.State.PROCESSING:
            self.dot.itemconfig(self._dot_id, fill=YELLOW)
            self.status_lbl.config(text="PROCESSING", fg=YELLOW)
            self.rec_btn.config(text="· · ·", bg=PANEL, fg=FG)
        else:
            ready = self.ready
            self.dot.itemconfig(self._dot_id, fill=GREEN if ready else MUTED)
            self.status_lbl.config(text="READY" if ready else "LOADING",
                                   fg=GREEN if ready else MUTED)
            self.rec_btn.config(text="●  START", bg=ACCENT, fg="#0b0c0e")
            self.overlay.withdraw()

    def _on_transcript(self, text):
        self.last_text = text
        self.history.config(state="normal")
        self.history.insert("1.0", text + "\n\n")
        self.history.insert("1.0", time.strftime("%H:%M:%S") + "\n", "time")
        self.history.config(state="disabled")

    def _quit(self):
        # Hard exit: keyboard hooks + pystray + sounddevice threads can deadlock
        # a graceful shutdown ("not responding"). Nothing needs flushing.
        try:
            import keyboard
            keyboard.unhook_all()
        except Exception:
            pass
        try:
            self.tray.stop()
        except Exception:
            pass
        import os
        os._exit(0)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    FlowGui().run()
