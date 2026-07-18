# FlowLocal

**Local-first voice dictation for Windows — a self-hosted Wispr Flow.** Hold a key, speak, release: your words land in whatever app has focus. GPU Whisper transcription, optional LLM cleanup, voice Q&A over your own notes, and spoken answers — and nothing ever leaves your machine.

```
        hold hotkey                     release
             │                             │
             ▼                             ▼
   ┌─────────────┐   ┌──────────────┐   ┌─────────────┐   ┌──────────────┐
   │  mic record  │ → │ faster-whisper│ → │ LLM cleanup  │ → │ paste into   │
   │  (sounddevice)│  │ (GPU, distil) │   │ (Ollama)     │   │ active window │
   └─────────────┘   └──────────────┘   └─────────────┘   └──────────────┘

   ask mode (Ctrl+Alt+Space):
   question ─→ Whisper ─→ personal-rag (your notes) ─→ local LLM answer
                                                 │
                                     ┌───────────┴───────────┐
                                     ▼                       ▼
                              pasted at cursor      spoken aloud (tts-daemon)
```

## Why

Commercial dictation tools ship your voice to someone else's cloud and charge monthly for it. FlowLocal runs the same pipeline on your own GPU: faster than typing, private by construction, free after setup.

## Features

- **Hold-to-talk dictation** — hold Right Ctrl, speak, release; text appears at your cursor in any Windows app. Toggle mode (Ctrl+Shift+Space) for hands-free.
- **GPU transcription** — faster-whisper `distil-large-v3` with CUDA, CPU fallback. Custom vocabulary boosting for domain terms.
- **LLM cleanup pass** — a fine-tune-prompted Ollama model strips filler words and fixes punctuation before pasting. Auto-skipped when Ollama isn't running.
- **Ask mode** — dictate a question, get an answer synthesized from *your own notes* via a local RAG service, pasted and read aloud through [tts-daemon](https://github.com/wtcrowe4/tts-daemon) (local Kokoro neural TTS).
- **Obsidian voice inbox** — every dictation optionally appends to a vault note, timestamped.
- **Training data capture** — saves audio/transcript pairs locally for future Whisper fine-tuning on your own voice.
- **Desktop GUI + tray** — status indicator, transcript history, settings editor, floating "Listening…" pill; tray icon reflects idle/recording/processing.
- **Polite clipboard** — pastes via clipboard, then restores whatever you had copied.

## Stack

| Piece | Tech |
|---|---|
| STT | faster-whisper (CTranslate2, CUDA) |
| Cleanup / Q&A LLM | Ollama (local models) |
| RAG | personal-rag over Obsidian vault |
| TTS | tts-daemon (Kokoro-82M / Chatterbox) |
| Hotkeys / injection | `keyboard`, `pyperclip` |
| GUI | tkinter desktop app + system tray |

## Install

1. Run `install.bat` (needs Python 3.10+)
2. Copy `config.example.json` → `config.json` and adjust — `config.json` is machine-specific and untracked
3. Optional cleanup pass: install [Ollama](https://ollama.com), then `ollama pull llama3.2:3b`
4. Run `run.bat` (GUI) or `run_headless.bat` (console + tray). First launch downloads the Whisper model (~1.5 GB), one time only. If download fails, run `download_model.bat`.

## Use

- **Hold Right Ctrl**, speak, release → text appears where your cursor is
- **Ctrl+Shift+Space** toggles recording on/off (hands-free)
- **Ctrl+Alt+Space** ask mode → dictate a question, answer is pasted + spoken
- Tray icon: gray = idle, red = recording, yellow = processing
- Beeps confirm start/stop (disable in config)

## Config (`config.json`)

Copy `config.example.json` to `config.json` and adjust — `config.json` is machine-specific and untracked.

| Key | Default | Notes |
|---|---|---|
| `hold_hotkey` | `right ctrl` | Hold-to-talk key ([key names](https://github.com/boppreh/keyboard)) |
| `toggle_hotkey` | `ctrl+shift+space` | Toggle mode |
| `ask_hotkey` | `ctrl+alt+space` | Ask mode (RAG Q&A) |
| `whisper_model` | `distil-large-v3` | Smaller/faster: `small.en`, `base.en` |
| `device` | `auto` | `cuda`, `cpu`, or `auto` (GPU with CPU fallback) |
| `cleanup_enabled` | `true` | Ollama pass; auto-skipped if Ollama not running |
| `ollama_model` | `llama3.2:3b` | Any local model |
| `tts_enabled` | `true` | Speak ask-mode answers via tts-daemon |
| `tts_url` | `http://127.0.0.1:8123/speak` | tts-daemon endpoint |
| `restore_clipboard` | `true` | Puts old clipboard back after paste |

## Troubleshooting

- **GPU not used** — console says "falling back to CPU". Check `nvidia-smi` works; cuBLAS/cuDNN wheels install via requirements.txt.
- **Hotkey does nothing in some apps** — apps running as Administrator need FlowLocal run as Administrator too.
- **Text pastes twice / not at all** — some apps block Ctrl+V briefly; try again or raise the `time.sleep` in `inject_text`.
- **Slow transcription** — switch `whisper_model` to `small.en`.
- **No spoken answers** — tts-daemon not running, or under mirrored WSL networking it must bind `0.0.0.0` (see its README).

## Privacy

Everything — audio, transcripts, LLM calls, TTS — runs on localhost. No accounts, no telemetry, no network calls except model downloads on first run. Your voice recordings and training data are gitignored and never leave your disk.

## Related

- [tts-daemon](https://github.com/wtcrowe4/tts-daemon) — the local TTS service FlowLocal speaks through; also gives Claude Code spoken responses.
