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
        found = []
        for sp in site.getsitepackages():
            nvidia = Path(sp) / "nvidia"
            if nvidia.is_dir():
                for bin_dir in nvidia.glob("*/bin"):
                    found.append(str(bin_dir))
                    # add_dll_directory alone is NOT enough: ctranslate2 loads
                    # cublas/cudnn with plain LoadLibrary, which only searches PATH.
                    os.add_dll_directory(str(bin_dir))
        if found:
            os.environ["PATH"] = os.pathsep.join(found) + os.pathsep + os.environ.get("PATH", "")
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

# ---------------------------------------------------------------- logging
# print() CRASHES under pythonw (no stdout) - always use log() instead.
LOG_PATH = Path(__file__).parent / "flowlocal.log"


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

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
        import collections
        self.levels = collections.deque(maxlen=64)  # live RMS for GUI waveform

    def _callback(self, indata, frames, t, status):
        with self._lock:
            self._chunks.append(indata.copy())
        self.levels.append(float(np.sqrt((indata ** 2).mean())))

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


def load_model(log=log):
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


def warmup(log=log):
    """Dummy transcription so the first real dictation isn't slow (CUDA kernel warmup)."""
    try:
        t0 = time.time()
        list(model.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), beam_size=1)[0])
        log(f"warmup done in {time.time()-t0:.1f}s")
    except Exception as e:
        log(f"warmup failed: {e}")


# ---------------------------------------------------------------- vocabulary
VOCAB_PATH = Path(__file__).parent / "vocab.txt"
_vocab_cache = {"mtime": 0.0, "words": []}


def get_vocab():
    """Custom terms from vocab.txt, reloaded automatically when the file changes."""
    try:
        m = VOCAB_PATH.stat().st_mtime
        if m != _vocab_cache["mtime"]:
            words = [w.strip() for w in VOCAB_PATH.read_text(encoding="utf-8").splitlines()
                     if w.strip() and not w.strip().startswith("#")]
            _vocab_cache.update(mtime=m, words=words)
            log(f"vocab loaded: {len(words)} terms")
    except FileNotFoundError:
        _vocab_cache["words"] = []
    return _vocab_cache["words"]


def transcribe(audio: np.ndarray) -> str:
    vocab = get_vocab()
    segments, _info = model.transcribe(
        audio,
        language=CFG["language"] or None,
        beam_size=CFG.get("beam_size", 2),
        vad_filter=True,
        # pad around detected speech so quiet word starts/ends aren't clipped
        vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 400,
                        "threshold": 0.35},
        hotwords=" ".join(vocab) if vocab else None,
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
    # custom Modelfile builds (flowlocal-*) have the system prompt baked in
    msgs = ([] if CFG["ollama_model"].startswith("flowlocal")
            else [{"role": "system", "content": CLEANUP_PROMPT}])
    msgs.append({"role": "user", "content": text})
    try:
        r = requests.post(
            f"{CFG['ollama_url']}/api/chat",
            json={
                "model": CFG["ollama_model"],
                "messages": msgs,
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
        log(f"Ollama cleanup skipped: {e}")
    return text


# ---------------------------------------------------------------- integrations
def append_to_vault(text: str):
    """Opt-in: append transcript to Obsidian vault so personal-rag indexes it."""
    if not CFG.get("vault_append_enabled") or not text:
        return
    try:
        p = Path(CFG["vault_append_path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"- **{time.strftime('%Y-%m-%d %H:%M')}** {text}\n")
        log("vault: appended")
    except Exception as e:
        log(f"vault append failed: {e}")


def save_training_pair(audio: np.ndarray, raw_text: str):
    """Opt-in: save wav + raw transcript pairs to dataset/ for future fine-tuning."""
    if not CFG.get("save_training_data") or not raw_text:
        return
    try:
        import wave
        ddir = Path(__file__).parent / "dataset"
        ddir.mkdir(exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        with wave.open(str(ddir / f"{ts}.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
        (ddir / f"{ts}.txt").write_text(raw_text, encoding="utf-8")
        log(f"dataset: saved pair {ts}")
    except Exception as e:
        log(f"dataset save failed: {e}")


# ---------------------------------------------------------------- ask mode (personal-rag)
def rag_query(q: str):
    """POST the raw question to personal-rag. Tries localhost, then tailnet IPs."""
    for base in CFG.get("rag_urls", []):
        try:
            r = requests.post(base.rstrip("/") + "/query",
                              json={"query": q, "k": CFG.get("rag_k", 5)}, timeout=6)
            r.raise_for_status()
            return r.json().get("results", [])
        except Exception as e:
            log(f"rag: {base} failed: {e}")
    return None


def ask_rag(q: str) -> str:
    """Dictated question -> personal-rag chunks -> local LLM answer."""
    results = rag_query(q)
    if results is None:
        return "[FlowLocal] personal-rag unreachable - is `uv run rag serve` running?"
    chunks = [c for c in results if c.get("score", 0) >= 0.4]
    if not chunks:
        return "[FlowLocal] no relevant notes found."
    context = "\n\n".join(
        f"[{c.get('file_path', '?')} > {c.get('heading_path', '')}]\n{c.get('content', '')[:1200]}"
        for c in chunks[:5])
    try:
        r = requests.post(
            f"{CFG['ollama_url']}/api/chat",
            json={
                "model": CFG.get("ask_model", CFG["ollama_model"]),
                "messages": [
                    {"role": "system",
                     "content": "Answer the question using ONLY the provided notes. "
                                "Be concise. If the notes don't contain the answer, say so."},
                    {"role": "user", "content": f"NOTES:\n{context}\n\nQUESTION: {q}"},
                ],
                "stream": False,
                "options": {"temperature": 0.2},
            },
            timeout=CFG.get("ask_timeout_sec", 30),
        )
        r.raise_for_status()
        return r.json()["message"]["content"].strip()
    except Exception as e:
        log(f"ask: answer generation failed: {e}")
        return "[FlowLocal] answer generation failed - see flowlocal.log."


# ---------------------------------------------------------------- tts (tts-daemon)
def tts_speak(text: str):
    """Speak text via the shared tts-daemon (Kokoro, WSL localhost:8123).
    Fire-and-forget: daemon down or disabled -> silent no-op."""
    if not CFG.get("tts_enabled", True) or not text:
        return

    def _post():
        try:
            requests.post(
                CFG.get("tts_url", "http://127.0.0.1:8123/speak"),
                json={"text": text[:CFG.get("tts_max_chars", 1500)]},
                timeout=CFG.get("tts_timeout_sec", 60),
            )
        except Exception as e:
            log(f"tts: daemon unreachable, skipped: {e}")

    threading.Thread(target=_post, daemon=True).start()


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
_rec_mode = "dictate"  # or "ask"


def start_recording(mode="dictate"):
    global _rec_mode
    if get_state() != State.IDLE:
        return
    _rec_mode = mode
    set_state(State.RECORDING)
    beep("start")
    try:
        recorder.start()
        log("recording started")
    except Exception as e:
        log(f"Mic error: {e}")
        beep("error")
        set_state(State.IDLE)


def stop_and_process():
    if get_state() != State.RECORDING:
        return
    set_state(State.PROCESSING)
    beep("stop")

    def _work():
        try:
            # tail capture: user releases the key while still finishing the last
            # word - keep the mic open a beat so it isn't clipped
            time.sleep(0.35)
            audio, duration = recorder.stop()
            log(f"pipeline: {duration:.1f}s audio, {audio.size} samples")
            if duration < CFG["min_recording_sec"] or audio.size == 0:
                log("pipeline: too short, skipped")
                return
            t0 = time.time()
            raw_text = transcribe(audio)
            log(f"pipeline: whisper done in {time.time()-t0:.1f}s -> {raw_text!r}")
            if not raw_text:
                return
            if _rec_mode == "ask":
                answer = ask_rag(raw_text)
                inject_text(answer)
                tts_speak(answer)
                _emit("transcript", f"Q: {raw_text}\nA: {answer}")
                log("pipeline: ask answered")
                return
            save_training_pair(audio, raw_text)
            t1 = time.time()
            text = cleanup(raw_text)
            log(f"pipeline: cleanup done in {time.time()-t1:.1f}s -> {text!r}")
            inject_text(text)
            log("pipeline: injected")
            _emit("transcript", text)
            append_to_vault(text)
        except Exception as e:
            log(f"Pipeline ERROR: {type(e).__name__}: {e}")
            beep("error")
        finally:
            set_state(State.IDLE)

    threading.Thread(target=_work, daemon=True).start()


# ---------------------------------------------------------------- hotkeys
_hold_down = False


def _on_hold_press(_e):
    """Right Ctrl = pure hold-to-talk dictation. Ask mode is NOT overloaded onto
    this key - a double-tap used to flip into ask mode and was firing by accident
    during normal dictation (speech got answered instead of transcribed). Ask now
    lives on its own hotkey (`ask_hotkey`, default ctrl+alt+space)."""
    global _hold_down
    if _hold_down:  # key auto-repeat
        return
    _hold_down = True
    start_recording()


def _on_hold_release(_e):
    global _hold_down
    _hold_down = False
    stop_and_process()  # too-short recordings are dropped by min_recording_sec


def _on_toggle():
    if get_state() == State.RECORDING:
        stop_and_process()
    else:
        start_recording()


def _on_ask_toggle():
    """Press once, ask your question out loud, press again -> answer typed at cursor."""
    if get_state() == State.RECORDING:
        stop_and_process()
    else:
        start_recording("ask")


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
        log(f"Hold-to-talk: {hold}")
    tog = CFG["toggle_hotkey"]
    if tog:
        keyboard.add_hotkey(tog, _on_toggle)
        log(f"Toggle: {tog}")
    ask = CFG.get("ask_hotkey")
    if ask:
        keyboard.add_hotkey(ask, _on_ask_toggle)
        log(f"Ask (personal-rag): {ask}")


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
    warmup()
    setup_hotkeys()
    log("FlowLocal ready. Dictate into any app. Ctrl+C or tray > Quit to exit.")
    run_tray()


if __name__ == "__main__":
    main()
