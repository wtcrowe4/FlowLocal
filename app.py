"""
FlowLocal - local Wispr Flow clone.
Hold hotkey (or toggle) -> speak -> release -> text typed into active window.
100% local: faster-whisper for STT, optional Ollama for cleanup.
"""

import json
import os
import sys
import threading
import time
from pathlib import Path

# Workarounds for flaky HuggingFace downloads on Windows (WinError 10054)
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "30")

# ---------------------------------------------------------------- DLL fix
# faster-whisper (ctranslate2) needs cuBLAS/cuDNN DLLs. If installed via
# pip (nvidia-cublas-cu12 / nvidia-cudnn-cu12), register their bin dirs.
def _register_nvidia_dlls():
    if sys.platform != "win32":
        return
    try:
        import site
        for sp in site.getsitepackages():
            nvidia = Path(sp) / "nvidia"
            if nvidia.is_dir():
                for bin_dir in nvidia.glob("*/bin"):
                    os.add_dll_directory(str(bin_dir))
    except Exception:
        pass

_register_nvidia_dlls()

import numpy as np
import sounddevice as sd
import keyboard
import pyperclip
import requests
import winsound
import pystray
from PIL import Image, ImageDraw
from faster_whisper import WhisperModel

CONFIG_PATH = Path(__file__).parent / "config.json"
CFG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

SAMPLE_RATE = CFG["sample_rate"]

# ---------------------------------------------------------------- state
class State:
    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"

_state = State.IDLE
_state_lock = threading.Lock()
_tray_icon = None

# Event hooks so a GUI can subscribe: on_event("state", fn) / on_event("transcript", fn)
_listeners = {"state": [], "transcript": []}


def on_event(kind, fn):
    _listeners[kind].append(fn)


def _emit(kind, *args):
    for fn in _listeners[kind]:
        try:
            fn(*args)
        except Exception:
            pass


def set_state(new):
    global _state
    with _state_lock:
        _state = new
    if _tray_icon:
        _tray_icon.icon = _make_icon(new)
        _tray_icon.title = f"FlowLocal - {new}"
    _emit("state", new)


def get_state():
    with _state_lock:
        return _state


# ---------------------------------------------------------------- audio
class Recorder:
    def __init__(self):
        self._chunks = []
        self._stream = None
        self._lock = threading.Lock()
        self._started_at = 0.0

    def _callback(self, indata, frames, t, status):
        with self._lock:
            self._chunks.append(indata.copy())

    def start(self):
        with self._lock:
            self._chunks = []
        self._started_at = time.time()
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()

    def stop(self):
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        with self._lock:
            if not self._chunks:
                return np.zeros(0, dtype=np.float32), 0.0
            audio = np.concatenate(self._chunks).flatten()
            self._chunks = []
        return audio, time.time() - self._started_at


recorder = Recorder()

# ---------------------------------------------------------------- whisper
model = None


def load_model(log=print):
    """Load Whisper. Call once at startup (GUI calls this in a thread)."""
    global model
    if model is not None:
        return
    log("Loading Whisper model (first run downloads it, please wait)...")
    _device = CFG["device"]
    if _device in ("auto", "cuda"):
        try:
            model = WhisperModel(CFG["whisper_model"], device="cuda", compute_type="float16")
            log(f"Model '{CFG['whisper_model']}' loaded on GPU.")
            return
        except Exception as e:
            log(f"GPU load failed ({e}); falling back to CPU.")
    model = WhisperModel(CFG["whisper_model"], device="cpu", compute_type="int8")
    log(f"Model '{CFG['whisper_model']}' loaded on CPU (int8).")


def transcribe(audio: np.ndarray) -> str:
    segments, _info = model.transcribe(
        audio,
        language=CFG["language"] or None,
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
    )
    return " ".join(s.text.strip() for s in segments).strip()


# ---------------------------------------------------------------- ollama cleanup
CLEANUP_PROMPT = (
    "You clean up dictated text. Remove filler words (um, uh, you know, like), "
    "fix punctuation and capitalization, and apply spoken commands such as "
    "'new line', 'new paragraph', 'comma', 'period' when clearly intended as commands. "
    "Do NOT change wording, do NOT summarize, do NOT add anything. "
    "Return ONLY the cleaned text with no preamble or quotes."
)


def cleanup(text: str) -> str:
    if not CFG["cleanup_enabled"] or not text:
        return text
    try:
        r = requests.post(
            f"{CFG['ollama_url']}/api/chat",
            json={
                "model": CFG["ollama_model"],
                "messages": [
                    {"role": "system", "content": CLEANUP_PROMPT},
                    {"role": "user", "content": text},
                ],
                "stream": False,
                "options": {"temperature": 0.1},
            },
            timeout=CFG["ollama_timeout_sec"],
        )
        r.raise_for_status()
        cleaned = r.json()["message"]["content"].strip()
        # Sanity: LLM went off the rails -> keep original
        if cleaned and 0.3 < len(cleaned) / max(len(text), 1) < 3.0:
            return cleaned
    except Exception as e:
        print(f"Ollama cleanup skipped: {e}")
    return text


# ---------------------------------------------------------------- inject
def inject_text(text: str):
    if not text:
        return
    old_clip = None
    if CFG["restore_clipboard"]:
        try:
            old_clip = pyperclip.paste()
        except Exception:
            pass
    pyperclip.copy(text)
    time.sleep(0.05)
    keyboard.send("ctrl+v")
    if old_clip is not None:
        def _restore():
            time.sleep(1.0)
            try:
                pyperclip.copy(old_clip)
            except Exception:
                pass
        threading.Thread(target=_restore, daemon=True).start()


# ---------------------------------------------------------------- feedback
def beep(kind: str):
    if not CFG["beep_feedback"]:
        return
    freq = {"start": 880, "stop": 660, "error": 220}.get(kind, 440)
    threading.Thread(
        target=lambda: winsound.Beep(freq, 120), daemon=True
    ).start()


# ---------------------------------------------------------------- pipeline
def start_recording():
    if get_state() != State.IDLE:
        return
    set_state(State.RECORDING)
    beep("start")
    try:
        recorder.start()
    except Exception as e:
        print(f"Mic error: {e}")
        beep("error")
        set_state(State.IDLE)


def stop_and_process():
    if get_state() != State.RECORDING:
        return
    set_state(State.PROCESSING)
    beep("stop")
    audio, duration = recorder.stop()

    def _work():
        try:
            if duration < CFG["min_recording_sec"] or audio.size == 0:
                return
            t0 = time.time()
            text = transcribe(audio)
            print(f"[whisper {time.time()-t0:.1f}s] {text}")
            if not text:
                return
            text = cleanup(text)
            inject_text(text)
            _emit("transcript", text)
        except Exception as e:
            print(f"Pipeline error: {e}")
            beep("error")
        finally:
            set_state(State.IDLE)

    threading.Thread(target=_work, daemon=True).start()


# ---------------------------------------------------------------- hotkeys
_hold_down = False


def _on_hold_press(_e):
    global _hold_down
    if _hold_down:  # key auto-repeat
        return
    _hold_down = True
    start_recording()


def _on_hold_release(_e):
    global _hold_down
    _hold_down = False
    stop_and_process()


def _on_toggle():
    if get_state() == State.RECORDING:
        stop_and_process()
    else:
        start_recording()


def setup_hotkeys():
    hold = CFG["hold_hotkey"]
    if hold:
        # Raw hook with exact name match. on_press_key("right ctrl") resolves
        # to scan code 29, shared by BOTH ctrl keys - it would fire on left ctrl
        # too (breaking ctrl+c etc). Event names distinguish left/right.
        def _hold_hook(e):
            if e.name != hold:
                return
            if e.event_type == "down":
                _on_hold_press(e)
            elif e.event_type == "up":
                _on_hold_release(e)

        keyboard.hook(_hold_hook)
        print(f"Hold-to-talk: {hold}")
    tog = CFG["toggle_hotkey"]
    if tog:
        keyboard.add_hotkey(tog, _on_toggle)
        print(f"Toggle: {tog}")


# ---------------------------------------------------------------- tray
def _make_icon(state):
    color = {
        State.IDLE: (90, 90, 90),
        State.RECORDING: (220, 50, 50),
        State.PROCESSING: (240, 180, 30),
    }[state]
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([8, 8, 56, 56], fill=color)
    # mic glyph
    d.rounded_rectangle([26, 16, 38, 38], radius=6, fill=(255, 255, 255))
    d.line([32, 40, 32, 48], fill=(255, 255, 255), width=3)
    d.line([24, 48, 40, 48], fill=(255, 255, 255), width=3)
    return img


def _quit(icon, _item):
    icon.stop()
    os._exit(0)


def run_tray():
    global _tray_icon
    _tray_icon = pystray.Icon(
        "FlowLocal",
        _make_icon(State.IDLE),
        "FlowLocal - idle",
        menu=pystray.Menu(
            pystray.MenuItem("Toggle recording", lambda i, m: _on_toggle()),
            pystray.MenuItem("Quit", _quit),
        ),
    )
    _tray_icon.run()  # blocks


# ---------------------------------------------------------------- main
def main():
    load_model()
    setup_hotkeys()
    print("FlowLocal ready. Dictate into any app. Ctrl+C or tray > Quit to exit.")
    run_tray()


if __name__ == "__main__":
    main()
