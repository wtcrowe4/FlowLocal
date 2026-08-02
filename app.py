"""
FlowLocal - local Wispr Flow clone.
Hold hotkey (or toggle) -> speak -> release -> text typed into active window.
100% local: faster-whisper for STT, optional Ollama for cleanup.
"""

import ctypes
import json
import os
import queue
import re
import sys
import threading
import time
from difflib import SequenceMatcher
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

# Every setting FlowLocal reads, with the value it falls back to, so a
# config.json only has to carry overrides. Without this merge a config written
# before a feature existed either raised KeyError on a CFG["..."] read or - far
# worse, because it fails silently - let CFG.get(...) hand back a False/empty
# default. That is exactly how the "hey flow" and "send it flow" triggers sat
# inert on a config that predated them, with nothing logged.
# Anything that opens the mic on its own or writes files defaults OFF here;
# config.example.json is where those get switched on deliberately.
DEFAULTS = {
    # input
    "hold_hotkey": "right ctrl",
    "toggle_hotkey": "ctrl+shift+space",
    "ask_hotkey": "ctrl+alt+space",
    "recording_mode": "dictate",
    # voice triggers
    "wake_trigger_enabled": False,
    "wake_trigger_phrase": "hey flow",
    "wake_check_interval_sec": 1.0,
    "wake_audio_window_sec": 3,
    "wake_debug": False,
    "wake_rms_threshold": 0.004,
    "submit_trigger_enabled": False,
    "submit_trigger_phrase": "send it flow",
    "voice_submit_trigger_enabled": True,
    "voice_submit_check_interval_sec": 1.5,
    "voice_submit_audio_window_sec": 8,
    # transcription
    "whisper_model": "distil-large-v3",
    "device": "auto",
    "language": "en",
    "beam_size": 3,
    "sample_rate": 16000,
    "min_recording_sec": 0.3,
    "max_recording_sec": 300,
    # cleanup
    "cleanup_enabled": True,
    "ollama_url": "http://localhost:11434",
    "ollama_model": "flowlocal-cleanup",
    "ollama_timeout_sec": 15,
    # ask mode
    "rag_urls": [],
    "rag_k": 5,
    "ask_model": "llama3.1:8b",
    "ask_timeout_sec": 30,
    # feedback
    "beep_feedback": True,
    "restore_clipboard": True,
    "tts_enabled": False,
    "tts_url": "http://127.0.0.1:8123/speak",
    "tts_max_chars": 1500,
    "tts_timeout_sec": 60,
    # opt-in side effects
    "vault_append_enabled": False,
    "vault_append_path": "",
    "save_training_data": False,
    # window chrome. pill_x/pill_y are deliberately absent - gui_web derives a
    # screen-aware position when they are unset, which a fixed default would
    # override and park the pill off a differently-sized monitor.
    "window_alpha": 1.0,
    "pill_alpha": 1.0,
    "acrylic": False,
}

_user_cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
CFG = {**DEFAULTS, **_user_cfg}
# Surfaced by setup_hotkeys() once log() exists, so config drift is visible
# instead of silently degrading a feature.
_CFG_DEFAULTED = sorted(k for k in DEFAULTS if k not in _user_cfg)

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

    def _open(self):
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()

    def start(self):
        with self._lock:
            self._chunks = []
        self._started_at = time.time()
        try:
            self._open()
        except Exception as e:
            # PortAudio enumerates devices once at import. A default mic that was
            # off at launch (e.g. wireless headset asleep) leaves a stale table and
            # fails with "Error querying device -1" forever. Re-enumerate and retry
            # so the mic recovers on the next keypress instead of needing a restart.
            log(f"Mic open failed ({e}); re-enumerating PortAudio and retrying")
            sd._terminate()
            sd._initialize()
            self._open()

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

    def snapshot(self, max_seconds: float) -> np.ndarray:
        """Return a recent audio copy without interrupting active recording."""
        with self._lock:
            if not self._chunks:
                return np.zeros(0, dtype=np.float32)
            audio = np.concatenate(self._chunks).flatten()
        max_samples = int(max_seconds * SAMPLE_RATE)
        return audio[-max_samples:] if max_samples > 0 else audio


recorder = Recorder()


class WakeListener:
    """Keep a short idle audio buffer for local voice-trigger recognition."""
    def __init__(self):
        self._chunks = []
        self._stream = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def _callback(self, indata, frames, t, status):
        with self._lock:
            self._chunks.append(indata.copy())
            max_chunks = int(CFG.get("wake_audio_window_sec", 3) * SAMPLE_RATE / frames) + 1
            if len(self._chunks) > max_chunks:
                del self._chunks[:-max_chunks]

    def snapshot(self) -> np.ndarray:
        with self._lock:
            if not self._chunks:
                return np.zeros(0, dtype=np.float32)
            return np.concatenate(self._chunks).flatten()

    def start(self):
        if (not CFG.get("wake_trigger_enabled", False) or self._stream
                or get_state() != State.IDLE):
            return
        with self._lock:
            self._chunks = []
        self._stop.clear()
        try:
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                callback=self._callback,
            )
            self._stream.start()
            self._thread = threading.Thread(target=self._monitor, daemon=True)
            self._thread.start()
            log(f"Wake trigger listening for {CFG.get('wake_trigger_phrase', 'hey flow')!r}")
        except Exception as e:
            self.stop()
            log(f"Wake trigger unavailable: {e}")

    def stop(self):
        self._stop.set()
        stream, self._stream = self._stream, None
        if stream:
            try:
                stream.stop()
                stream.close()
            except Exception as e:
                log(f"Wake trigger stream close failed: {e}")

    def _monitor(self):
        interval = CFG.get("wake_check_interval_sec", 1.0)
        # wake_debug logs every cycle: how much audio the idle stream holds, its
        # level, and what Whisper made of it. Without it a wake miss is totally
        # silent - there is no way to tell a dead mic stream from a mis-heard
        # phrase from a listener that never ran.
        debug = CFG.get("wake_debug", False)
        while not self._stop.wait(interval):
            if get_state() != State.IDLE:
                return
            if tts_is_speaking():
                if debug:
                    log("wake: skipped (tts speaking)")
                continue
            try:
                audio = self.snapshot()
                rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
                # Gate on loudness before spending a Whisper pass. Running the
                # model every idle second pinned the GPU continuously, and a
                # machine that busy stops scheduling the keyboard hook thread
                # inside its ~300ms deadline - so Windows evicted the hook and
                # every hotkey died. It also made Whisper hallucinate ("Thank
                # you.") out of room tone, which is a false trigger waiting to
                # happen. Measured: speech sits at 0.008-0.015, silence <0.001.
                if rms < CFG.get("wake_rms_threshold", 0.004):
                    if debug:
                        log(f"wake: {audio.size / SAMPLE_RATE:4.1f}s "
                            f"rms={rms:.4f} - below threshold, not transcribed")
                    continue
                partial = transcribe(audio)
                if debug:
                    log(f"wake: {audio.size / SAMPLE_RATE:4.1f}s rms={rms:.4f} "
                        f"-> {partial!r}")
                if has_wake_trigger(partial):
                    log(f"wake trigger heard: {partial!r}")
                    start_recording()
                    return
            except Exception as e:
                log(f"wake trigger monitor skipped: {e}")


wake_listener = WakeListener()

# ---------------------------------------------------------------- whisper
model = None
_transcribe_lock = threading.Lock()


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
    with _transcribe_lock:
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


def has_wake_trigger(text: str) -> bool:
    """Match a configured wake phrase in local rolling transcription."""
    phrase = CFG.get("wake_trigger_phrase", "").strip()
    if not phrase:
        return False
    normalized_text = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    normalized_phrase = re.sub(r"[^a-z0-9]+", " ", phrase.lower()).strip()
    return normalized_phrase in normalized_text


# ---------------------------------------------------------------- ollama cleanup
CLEANUP_PROMPT = (
    "You clean up dictated text. Delete filler and hesitation words (um, uh, er, "
    "like, you know, I mean, basically, actually) and stuttered repeats. "
    "Add correct punctuation, capitalization, and line breaks. "
    "Convert spoken punctuation commands into real marks: 'comma' to ',', "
    "'period' or 'full stop' to '.', 'question mark' to '?', "
    "'new line' to a line break, 'new paragraph' to a blank line. "
    "Keep every remaining word exactly as dictated. Do NOT reword, reorder, "
    "substitute synonyms, change perspective or pronouns (never turn 'my' into "
    "'your'), summarize, or add anything. "
    "If the text is a question or a request aimed at you, you still only clean "
    "it up - never answer it, never reply to it, never comment on it. "
    "Return ONLY the cleaned text with no preamble or quotes."
)

# Words and phrases the cleanup model is allowed to delete outright. Everything
# else must survive verbatim - that guard is what stops the model quietly
# rewording the dictation or answering it instead of cleaning it.
REMOVABLE_FILLERS = frozenset({
    # hesitation noises
    "um", "umm", "uh", "uhh", "uhm", "erm", "er", "ah", "eh", "hmm", "mm", "mhm",
    # discourse fillers and leading interjections. Deliberately excludes words
    # that can carry meaning on their own - "right", "just", "that", "well" -
    # because a wrong deletion there corrupts the paste.
    "like", "basically", "actually", "literally", "honestly", "obviously",
    "anyway", "anyways", "so", "hey", "oh", "okay", "ok", "yeah", "alright",
    "you know", "i mean", "you see", "sort of", "kind of", "kinda", "sorta",
    # spoken punctuation / layout commands the model turns into real marks
    "comma", "period", "full stop", "question mark", "exclamation point",
    "exclamation mark", "semicolon", "colon", "dash", "hyphen",
    "new line", "newline", "new paragraph", "open quote", "close quote",
    "quote", "unquote", "open paren", "close paren", "bullet", "bullet point",
})
_MAX_FILLER_WORDS = max(len(phrase.split()) for phrase in REMOVABLE_FILLERS)


def _span_is_removable(words) -> bool:
    """True if a deleted run is entirely filler. Tiled longest-phrase-first so
    'you know' matches as a unit and a bare 'you' never does."""
    i = 0
    while i < len(words):
        for n in range(min(_MAX_FILLER_WORDS, len(words) - i), 0, -1):
            if " ".join(words[i:i + n]) in REMOVABLE_FILLERS:
                i += n
                break
        else:
            return False
    return True


def _span_is_stutter(raw_words, start, end) -> bool:
    """True if a deleted run just repeats its neighbour ('the the report')."""
    span = raw_words[start:end]
    return (raw_words[end:end + len(span)] == span
            or raw_words[max(0, start - len(span)):start] == span)


_NUM_UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
              "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
              "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
              "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
              "nineteen": 19}
_NUM_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
             "seventy": 70, "eighty": 80, "ninety": 90}
_NUM_SCALES = {"hundred": 100, "thousand": 1000, "million": 1000000}


def _fold_numbers(words):
    """Collapse spoken number runs into digits so 'thirty six' compares equal to
    '36'. Applied to both sides, so it only has to be consistent, not correct -
    dictation says sizes out loud and the model writes them as numerals."""
    out, i, n = [], 0, len(words)
    while i < n:
        if words[i] not in _NUM_UNITS and words[i] not in _NUM_TENS:
            out.append(words[i])
            i += 1
            continue
        total = current = 0
        while i < n:
            word = words[i]
            if word in _NUM_UNITS:
                current += _NUM_UNITS[word]
            elif word in _NUM_TENS:
                current += _NUM_TENS[word]
            elif word in _NUM_SCALES:
                scale = _NUM_SCALES[word]
                if scale == 100:
                    current = max(current, 1) * 100
                else:
                    total += max(current, 1) * scale
                    current = 0
            elif (word == "and" and i + 1 < n
                  and (words[i + 1] in _NUM_UNITS or words[i + 1] in _NUM_TENS)):
                pass  # "one hundred and twenty"
            else:
                break
            i += 1
        out.append(str(total + current))
    return out


def is_cleanup_preserving(raw: str, cleaned: str) -> bool:
    """Allow punctuation, filler removal, and stutter removal - but never word
    substitutions, reordering, or additions."""
    raw_words = _fold_numbers(re.findall(r"[a-z0-9]+", raw.lower()))
    cleaned_words = _fold_numbers(re.findall(r"[a-z0-9]+", cleaned.lower()))
    matcher = SequenceMatcher(None, raw_words, cleaned_words, autojunk=False)

    for tag, raw_start, raw_end, _, _ in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "delete" and (_span_is_removable(raw_words[raw_start:raw_end])
                                or _span_is_stutter(raw_words, raw_start, raw_end)):
            continue
        return False
    return True


def cleanup(text: str) -> str:
    if not CFG["cleanup_enabled"] or not text:
        return text
    # Send the contract with every request so it remains authoritative even
    # when an older custom Ollama model has a stale baked-in system prompt.
    msgs = [{"role": "system", "content": CLEANUP_PROMPT}]
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
        # Preserve dictation if the model begins answering instead of cleaning.
        if (cleaned and 0.3 < len(cleaned) / max(len(text), 1) < 3.0
                and is_cleanup_preserving(text, cleaned)):
            return cleaned
        log("cleanup rejected non-preserving model output")
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
_tts_speaking = threading.Event()


def tts_is_speaking() -> bool:
    return _tts_speaking.is_set()


def tts_speak(text: str):
    """Speak text via the shared tts-daemon (Kokoro, WSL localhost:8123).
    Fire-and-forget: daemon down or disabled -> silent no-op."""
    if not CFG.get("tts_enabled", True) or not text:
        return

    def _post():
        _tts_speaking.set()
        try:
            requests.post(
                CFG.get("tts_url", "http://127.0.0.1:8123/speak"),
                json={"text": text[:CFG.get("tts_max_chars", 1500)]},
                timeout=CFG.get("tts_timeout_sec", 60),
            )
        except Exception as e:
            log(f"tts: daemon unreachable, skipped: {e}")
        finally:
            _tts_speaking.clear()

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


# ---------------------------------------------------------------- submit trigger
def strip_submit_trigger(text: str) -> tuple[str, bool]:
    """Remove a spoken final submit phrase and report whether to press Enter."""
    phrase = CFG.get("submit_trigger_phrase", "").strip()
    if not CFG.get("submit_trigger_enabled", False) or not phrase:
        return text, False
    match = re.search(
        rf"(?:\s*[,;:\-]?\s*){re.escape(phrase)}\s*[.!?]*\s*$",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return text, False
    return text[:match.start()].rstrip(), True


def has_submit_trigger(text: str) -> bool:
    """Check partial speech recognition for the configured final phrase."""
    phrase = CFG.get("submit_trigger_phrase", "").strip()
    if not phrase:
        return False
    normalized_text = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    normalized_phrase = re.sub(r"[^a-z0-9]+", " ", phrase.lower()).strip()
    return normalized_phrase in normalized_text


# ---------------------------------------------------------------- feedback
def beep(kind: str):
    if not CFG["beep_feedback"]:
        return
    freq = {"start": 880, "stop": 660, "error": 220}.get(kind, 440)
    threading.Thread(
        target=lambda: winsound.Beep(freq, 120), daemon=True
    ).start()


# ---------------------------------------------------------------- pipeline
_rec_mode = "dictate"  # mode captured when recording begins
_voice_submit_stop = None


def _monitor_voice_submit(stop_event: threading.Event):
    """Stop normal dictation after Whisper hears the final submit phrase."""
    interval = CFG.get("voice_submit_check_interval_sec", 1.5)
    window_sec = CFG.get("voice_submit_audio_window_sec", 8)
    while not stop_event.wait(interval):
        if get_state() != State.RECORDING:
            return
        try:
            partial = transcribe(recorder.snapshot(window_sec))
            if has_submit_trigger(partial):
                log(f"voice submit trigger heard: {partial!r}")
                stop_and_process()
                return
        except Exception as e:
            log(f"voice submit monitor skipped: {e}")


def start_recording(mode=None):
    global _rec_mode, _voice_submit_stop
    if get_state() != State.IDLE:
        return
    _rec_mode = mode or CFG.get("recording_mode", "dictate")
    if _rec_mode not in ("dictate", "ask"):
        _rec_mode = "dictate"
    wake_listener.stop()
    set_state(State.RECORDING)
    beep("start")
    try:
        recorder.start()
        log("recording started")
        if (_rec_mode == "dictate" and CFG.get("voice_submit_trigger_enabled", True)):
            _voice_submit_stop = threading.Event()
            threading.Thread(
                target=_monitor_voice_submit, args=(_voice_submit_stop,), daemon=True
            ).start()
    except Exception as e:
        log(f"Mic error: {e}")
        beep("error")
        set_state(State.IDLE)


def stop_and_process():
    global _voice_submit_stop
    if get_state() != State.RECORDING:
        return
    if _voice_submit_stop is not None:
        _voice_submit_stop.set()
        _voice_submit_stop = None
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
            text, should_submit = strip_submit_trigger(raw_text)
            if not text:
                log("pipeline: submit trigger without dictation, skipped")
                return
            t1 = time.time()
            text = cleanup(text)
            log(f"pipeline: cleanup done in {time.time()-t1:.1f}s -> {text!r}")
            inject_text(text)
            log("pipeline: injected")
            if should_submit:
                # Let the destination application receive the clipboard paste
                # before submitting its input with Enter.
                time.sleep(0.1)
                keyboard.send("enter")
                log("pipeline: submitted")
            _emit("transcript", text)
            append_to_vault(text)
        except Exception as e:
            log(f"Pipeline ERROR: {type(e).__name__}: {e}")
            beep("error")
        finally:
            set_state(State.IDLE)
            wake_listener.start()

    threading.Thread(target=_work, daemon=True).start()


# ---------------------------------------------------------------- hotkeys
_hold_down = False

# These callbacks run ON the low-level keyboard hook thread. Windows silently
# evicts a WH_KEYBOARD_LL hook whose callback overruns LowLevelHooksTimeout
# (~300ms by default), and once it does EVERY hotkey dies - F24 toggle and
# right-ctrl hold alike - with no error, until the process restarts. Opening the
# mic and stopping the wake listener are both slow enough to trip that, so the
# hook must never do the work itself; it only queues it. A single worker keeps
# press/release strictly ordered, which a thread-per-event would not.
_hotkey_q = queue.Queue()


def _hotkey_worker():
    while True:
        fn = _hotkey_q.get()
        try:
            fn()
        except Exception as e:
            log(f"hotkey handler ERROR: {type(e).__name__}: {e}")


threading.Thread(target=_hotkey_worker, daemon=True).start()


def _dispatch(fn):
    """Hand hotkey work off the hook thread. Must stay O(microseconds)."""
    _hotkey_q.put(fn)


def _on_hold_press(_e):
    """Right Ctrl = pure hold-to-talk dictation. Ask mode is NOT overloaded onto
    this key - a double-tap used to flip into ask mode and was firing by accident
    during normal dictation (speech got answered instead of transcribed). Ask now
    lives on its own hotkey (`ask_hotkey`, default ctrl+alt+space)."""
    global _hold_down
    if _hold_down:  # key auto-repeat
        return
    _hold_down = True
    _dispatch(start_recording)


def _on_hold_release(_e):
    global _hold_down
    if not _hold_down:  # release without a matching press we handled
        return
    _hold_down = False
    # too-short recordings are dropped by min_recording_sec
    _dispatch(stop_and_process)


def _on_toggle():
    _dispatch(_toggle_work)


def _toggle_work():
    if get_state() == State.RECORDING:
        stop_and_process()
    else:
        start_recording()


def _on_ask_toggle():
    """Press once, ask your question out loud, press again -> answer typed at cursor."""
    _dispatch(_ask_toggle_work)


def _ask_toggle_work():
    if get_state() == State.RECORDING:
        stop_and_process()
    else:
        start_recording("ask")


_last_hook_event = 0.0


def _note_hook_event(_e=None):
    """Liveness probe. Runs on the hook thread, so it must stay trivial."""
    global _last_hook_event
    _last_hook_event = time.time()


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def _system_idle_sec() -> float:
    """Seconds since Windows last saw any real user input, hook or not."""
    info = _LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    return max(0.0, (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000.0)


def _install_key_hooks():
    """(Re)install every keyboard hook. Safe to call repeatedly - the watchdog
    calls it to recover a hook Windows threw away."""
    keyboard.unhook_all()
    keyboard.hook(_note_hook_event)
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
    tog = CFG["toggle_hotkey"]
    if tog:
        keyboard.add_hotkey(tog, _on_toggle)
    ask = CFG.get("ask_hotkey")
    if ask:
        keyboard.add_hotkey(ask, _on_ask_toggle)
    _note_hook_event()


def _hook_watchdog():
    """Windows evicts a low-level keyboard hook whose callback misses its
    ~300ms deadline - including when the machine is merely too busy for the
    hook thread to be scheduled. Nothing raises and nothing is logged; every
    hotkey simply stops working until restart. This is how the F24 toggle kept
    "detaching".

    There is no API to ask whether our hook is still installed, so infer it:
    if Windows says the user is actively typing/clicking but our hook has seen
    nothing for a while, the hook is gone. Reinstall it."""
    while True:
        time.sleep(30)
        try:
            active = _system_idle_sec() < 25
            silent_for = time.time() - _last_hook_event
            if active and silent_for > 90:
                log(f"keyboard hook evicted (no events in {silent_for:.0f}s "
                    f"while user active) - reinstalling")
                _install_key_hooks()
        except Exception as e:
            log(f"hook watchdog error: {type(e).__name__}: {e}")


def setup_hotkeys():
    if _CFG_DEFAULTED:
        log(f"config: {len(_CFG_DEFAULTED)} key(s) absent from config.json, "
            f"using defaults: {', '.join(_CFG_DEFAULTED)}")
    _install_key_hooks()
    if CFG["hold_hotkey"]:
        log(f"Hold-to-talk: {CFG['hold_hotkey']}")
    if CFG["toggle_hotkey"]:
        log(f"Toggle: {CFG['toggle_hotkey']}")
    if CFG.get("ask_hotkey"):
        log(f"Ask (personal-rag): {CFG['ask_hotkey']}")
    threading.Thread(target=_hook_watchdog, daemon=True).start()
    log("hook watchdog armed")
    wake_listener.start()


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
