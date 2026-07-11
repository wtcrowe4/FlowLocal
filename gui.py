"""
FlowLocal GUI - local dictation, Wispr Flow style.
Gunmetal/graphite theme, Alienware-blue accents, dark titlebar.
"""

import ctypes
import json
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

from PIL import ImageTk

import app  # core engine (does NOT load model on import)

# ---------------------------------------------------------------- theme
BG = "#17181b"        # near-black graphite
PANEL = "#1f2126"     # raised panel
CARD = "#24262b"      # cards / inputs
EDGE = "#3a3d45"      # metal edge lines
EDGE_HI = "#4d515b"   # lighter bevel
FG = "#dfe2e7"
MUTED = "#8b8f98"
ACCENT = "#00a8ff"    # Alienware/Logitech electric blue
ACCENT_DIM = "#0077b6"
RED = "#ff4d5a"
YELLOW = "#f0b83a"
GREEN = "#39d98a"

FONT = "Segoe UI"

MODELS = [
    "distil-large-v3",    # best english speed/accuracy balance
    "large-v3-turbo",     # multilingual, near large-v3 accuracy, 6x faster
    "large-v3",           # max accuracy, slowest
    "medium.en",
    "small.en",
    "base.en",
]

_events = queue.Queue()  # thread-safe bridge: engine threads -> Tk thread


def _dark_titlebar(win):
    """Windows 10/11 immersive dark titlebar."""
    if sys.platform != "win32":
        return
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        val = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), 4)
    except Exception:
        pass


class FlowGui:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("FlowLocal")
        self.root.geometry("560x680")
        self.root.configure(bg=BG)
        self.root.minsize(470, 560)
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        _dark_titlebar(self.root)

        # replace default Tk feather icon with our mic icon
        try:
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
            app.setup_hotkeys()
            _events.put(("ready", None))
        except Exception as e:
            _events.put(("log", f"Engine failed: {e}"))

    # ------------------------------------------------------------ styling
    def _style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        # combobox: dark field, dark dropdown list (fixes white-on-white)
        style.configure("TCombobox",
                        fieldbackground=CARD, background=PANEL, foreground=FG,
                        arrowcolor=ACCENT, bordercolor=EDGE, lightcolor=EDGE,
                        darkcolor=EDGE, insertcolor=FG, selectbackground=CARD,
                        selectforeground=FG)
        style.map("TCombobox",
                  fieldbackground=[("readonly", CARD), ("active", CARD)],
                  foreground=[("readonly", FG)],
                  bordercolor=[("focus", ACCENT)])
        self.root.option_add("*TCombobox*Listbox.background", CARD)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#0b0c0e")
        style.configure("TCheckbutton", background=CARD, foreground=FG,
                        focuscolor=CARD)
        style.map("TCheckbutton",
                  background=[("active", CARD)],
                  indicatorcolor=[("selected", ACCENT), ("!selected", EDGE)])

    def _metal_frame(self, parent):
        """Card with a subtle bevel edge (metal plate look)."""
        outer = tk.Frame(parent, bg=EDGE_HI)
        inner = tk.Frame(outer, bg=CARD)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        return outer, inner

    def _button(self, parent, text, cmd, primary=False, **kw):
        b = tk.Button(parent, text=text, command=cmd, relief="flat", bd=0,
                      bg=ACCENT if primary else PANEL,
                      fg="#0b0c0e" if primary else FG,
                      activebackground=ACCENT_DIM if primary else EDGE,
                      activeforeground="#ffffff",
                      font=(FONT, 10, "bold") if primary else (FONT, 9),
                      padx=14, pady=5, cursor="hand2", **kw)
        return b

    # ------------------------------------------------------------ ui
    def _build_ui(self):
        # ---- header bar
        header = tk.Frame(self.root, bg=PANEL)
        header.pack(fill="x")
        tk.Frame(self.root, bg=ACCENT, height=1).pack(fill="x")  # accent seam

        title = tk.Label(header, text="FLOW", bg=PANEL, fg=FG,
                         font=(FONT, 15, "bold"))
        title.pack(side="left", padx=(18, 0), pady=12)
        tk.Label(header, text="LOCAL", bg=PANEL, fg=ACCENT,
                 font=(FONT, 15, "bold")).pack(side="left")

        self.rec_btn = self._button(header, "●  START", self._toggle, primary=True,
                                    state="disabled")
        self.rec_btn.pack(side="right", padx=16, pady=10)

        # status pill
        self.status_frame = tk.Frame(header, bg=CARD, padx=10, pady=3)
        self.status_frame.pack(side="right", pady=10)
        self.dot = tk.Canvas(self.status_frame, width=10, height=10, bg=CARD,
                             highlightthickness=0)
        self._dot_id = self.dot.create_oval(1, 1, 9, 9, fill=MUTED, outline="")
        self.dot.pack(side="left")
        self.status_lbl = tk.Label(self.status_frame, text="LOADING",
                                   bg=CARD, fg=MUTED, font=(FONT, 8, "bold"))
        self.status_lbl.pack(side="left", padx=(6, 0))

        hint = tk.Label(self.root,
                        text=f"HOLD  [{app.CFG['hold_hotkey'].upper()}]  TO TALK      "
                             f"TOGGLE  [{app.CFG['toggle_hotkey'].upper()}]",
                        bg=BG, fg=MUTED, font=("Consolas", 8))
        hint.pack(anchor="w", padx=20, pady=(10, 0))

        # ---- history
        tk.Label(self.root, text="TRANSCRIPT HISTORY", bg=BG, fg=ACCENT,
                 font=(FONT, 8, "bold")).pack(anchor="w", padx=20, pady=(14, 4))
        hist_outer, hist_inner = self._metal_frame(self.root)
        hist_outer.pack(fill="both", expand=True, padx=18)
        self.history = tk.Text(hist_inner, bg=CARD, fg=FG, relief="flat", wrap="word",
                               font=(FONT, 10), padx=12, pady=10, state="disabled",
                               insertbackground=FG, selectbackground=ACCENT_DIM)
        scroll = tk.Scrollbar(hist_inner, command=self.history.yview,
                              troughcolor=CARD, bg=PANEL, activebackground=EDGE_HI,
                              relief="flat", width=10)
        self.history.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.history.pack(fill="both", expand=True)
        self.history.tag_configure("time", foreground=ACCENT, font=("Consolas", 8))

        btns = tk.Frame(self.root, bg=BG)
        btns.pack(fill="x", padx=18, pady=(6, 0))
        self._button(btns, "Copy last", self._copy_last).pack(side="left")
        self._button(btns, "Clear", self._clear_history).pack(side="left", padx=6)
        self.last_text = ""

        # ---- settings
        tk.Label(self.root, text="SETTINGS", bg=BG, fg=ACCENT,
                 font=(FONT, 8, "bold")).pack(anchor="w", padx=20, pady=(14, 4))
        s_outer, s = self._metal_frame(self.root)
        s_outer.pack(fill="x", padx=18, pady=(0, 6))
        s.columnconfigure(1, weight=1)

        def row(r, label):
            tk.Label(s, text=label, bg=CARD, fg=FG, font=(FONT, 9)).grid(
                row=r, column=0, sticky="w", padx=12, pady=5)

        def entry(var, width=22):
            return tk.Entry(s, textvariable=var, bg=PANEL, fg=FG, relief="flat",
                            insertbackground=ACCENT, width=width,
                            highlightthickness=1, highlightbackground=EDGE,
                            highlightcolor=ACCENT)

        row(0, "Whisper model")
        self.model_var = tk.StringVar(value=app.CFG["whisper_model"])
        cb = ttk.Combobox(s, textvariable=self.model_var, values=MODELS, width=24)
        cb.grid(row=0, column=1, sticky="e", padx=12)  # editable: paste any HF CT2 repo

        row(1, "Hold-to-talk key")
        self.hold_var = tk.StringVar(value=app.CFG["hold_hotkey"])
        entry(self.hold_var).grid(row=1, column=1, sticky="e", padx=12)

        row(2, "Toggle hotkey")
        self.toggle_var = tk.StringVar(value=app.CFG["toggle_hotkey"])
        entry(self.toggle_var).grid(row=2, column=1, sticky="e", padx=12)

        row(3, "AI cleanup (Ollama)")
        self.cleanup_var = tk.BooleanVar(value=app.CFG["cleanup_enabled"])
        ttk.Checkbutton(s, variable=self.cleanup_var,
                        command=self._apply_cleanup).grid(row=3, column=1,
                                                          sticky="e", padx=12)

        row(4, "Ollama model")
        self.ollama_var = tk.StringVar(value=app.CFG["ollama_model"])
        entry(self.ollama_var).grid(row=4, column=1, sticky="e", padx=12)

        self._button(s, "SAVE", self._save, primary=True).grid(
            row=5, column=1, sticky="e", padx=12, pady=10)

        self.footer = tk.Label(self.root, text="", bg=BG, fg=MUTED,
                               font=("Consolas", 8))
        self.footer.pack(anchor="w", padx=20, pady=(0, 8))

    # ------------------------------------------------------------ overlay pill
    def _build_overlay(self):
        self.overlay = tk.Toplevel(self.root)
        self.overlay.overrideredirect(True)
        self.overlay.attributes("-topmost", True)
        self.overlay.configure(bg=ACCENT)  # 1px blue border via padding
        inner = tk.Frame(self.overlay, bg="#0e0f11")
        inner.pack(padx=1, pady=1)
        self.ov_lbl = tk.Label(inner, text="●  LISTENING", bg="#0e0f11",
                               fg=RED, font=(FONT, 11, "bold"), padx=20, pady=7)
        self.ov_lbl.pack()
        sw = self.overlay.winfo_screenwidth()
        sh = self.overlay.winfo_screenheight()
        self.overlay.geometry(f"+{sw // 2 - 80}+{sh - 110}")
        self.overlay.withdraw()

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
        app._tray_icon = self.tray  # engine updates its color on state change
        self.tray.run_detached()

    def hide_to_tray(self):
        self.root.withdraw()

    # ------------------------------------------------------------ actions
    def _toggle(self):
        app._on_toggle()

    def _apply_cleanup(self):
        app.CFG["cleanup_enabled"] = self.cleanup_var.get()

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
        app.CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        if needs_restart:
            messagebox.showinfo("FlowLocal", "Saved. Restart the app to apply model/hotkey changes.")
        else:
            self.footer.config(text="Settings saved.")

    def _copy_last(self):
        if self.last_text:
            import pyperclip
            pyperclip.copy(self.last_text)
            self.footer.config(text="Copied.")

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
                    self.footer.config(text=str(payload)[:100])
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
            self.ov_lbl.config(text="●  LISTENING", fg=RED)
            self.overlay.deiconify()
        elif state == app.State.PROCESSING:
            self.dot.itemconfig(self._dot_id, fill=YELLOW)
            self.status_lbl.config(text="PROCESSING", fg=YELLOW)
            self.rec_btn.config(text="· · ·", bg=PANEL, fg=FG)
            self.ov_lbl.config(text="●  PROCESSING", fg=YELLOW)
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
        try:
            self.tray.stop()
        except Exception:
            pass
        self.root.destroy()
        import os
        os._exit(0)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    FlowGui().run()
