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
- **Input mode toggle** — the UI switches normal hold/toggle recording between **Dictate** (cleaned transcript) and **Ask Agent** (RAG answer). The separate ask hotkey remains available as an explicit shortcut.
- **Voice wake trigger** — say **“Hey Flow”** to start normal recording without replacing the F24 toggle control.
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
3. Optional cleanup pass: install [Ollama](https://ollama.com), then build a cleanup model (see [Cleanup models](#cleanup-models))
4. Run `run.bat` (GUI) or `run_headless.bat` (console + tray). First launch downloads the Whisper model (~1.5 GB), one time only. If download fails, run `download_model.bat`.

## Use

- **Hold Right Ctrl**, speak, release → text appears where your cursor is
- **Ctrl+Shift+Space** toggles recording on/off (hands-free)
- **Ctrl+Alt+Space** ask mode → dictate a question, answer is pasted + spoken
- In the UI, click **INPUT.MODE** to switch normal recording between **DICTATE** and **ASK.AGENT**
- In **DICTATE** mode, say **“Send It Flow”** to stop recording, remove the phrase, paste the transcript, and press Enter
- Say **“Hey Flow”** while FlowLocal is idle to start recording hands-free
- Tray icon: gray = idle, red = recording, yellow = processing
- The **–** button hides the main window while FlowLocal keeps running; **✕** or tray **Quit** stops it completely
- Beeps confirm start/stop (disable in config)

## Config (`config.json`)

Copy `config.example.json` to `config.json` and adjust — `config.json` is machine-specific and untracked.

| Key | Default | Notes |
|---|---|---|
| `hold_hotkey` | `right ctrl` | Hold-to-talk key ([key names](https://github.com/boppreh/keyboard)) |
| `toggle_hotkey` | `ctrl+shift+space` | Toggle mode |
| `recording_mode` | `dictate` | Mode for normal hold/toggle recording: `dictate` or `ask` |
| `wake_trigger_enabled` | `true` | Listen locally for the idle wake phrase |
| `wake_trigger_phrase` | `hey flow` | Phrase that begins normal dictation |
| `submit_trigger_phrase` | `send it flow` | Final dictation phrase that is removed before submitting |
| `ask_hotkey` | `ctrl+alt+space` | Ask mode (RAG Q&A) |
| `whisper_model` | `distil-large-v3` | Smaller/faster: `small.en`, `base.en` |
| `device` | `auto` | `cuda`, `cpu`, or `auto` (GPU with CPU fallback) |
| `cleanup_enabled` | `true` | Ollama pass; auto-skipped if Ollama not running |
| `ollama_model` | `flowlocal-cleanup` | Any local model — see [Cleanup models](#cleanup-models) |
| `tts_enabled` | `true` | Speak ask-mode answers via tts-daemon |
| `tts_url` | `http://127.0.0.1:8123/speak` | tts-daemon endpoint |
| `restore_clipboard` | `true` | Puts old clipboard back after paste |

## Cleanup models

The cleanup pass strips filler words, fixes punctuation and capitalization, and turns
spoken commands (`comma`, `period`, `new line`) into real marks. Every result is checked
against the raw transcript first: the model may delete filler and stutters, but any
reworded, reordered, or added text is rejected and the raw transcript is pasted instead.
That guard is why the model can never quietly answer your dictation or flip `my` to `your`.

Build whichever tier fits your GPU, then pick it in the GUI:

```
ollama create flowlocal-cleanup       -f Modelfile          # 3B  — default
ollama create flowlocal-cleanup-8b    -f Modelfile.8b       # 8B
ollama create flowlocal-cleanup-gemma -f Modelfile.gemma    # best quality
```

Measured on an RTX 5080 over an 8-phrase dictation set — *accepted* is how often the
cleanup survived the guard, *polished* how often it also had correct capitalization
and end punctuation:

| Model | VRAM | Accepted | Polished | Median (warm) |
|---|---|---|---|---|
| `flowlocal-cleanup-gemma` | ~6 GB | 8/8 | 8/8 | 0.5 s |
| `flowlocal-cleanup-8b` | ~5 GB | 5/8 | 5/8 | 0.3 s |
| `flowlocal-cleanup` (3B) | ~2 GB | 5/8 | 5/8 | 0.3 s |

The gemma tier is the only one that reliably converts spoken punctuation, writes spoken
sizes as numerals, and refuses to answer a dictated question. It needs headroom beyond
Whisper's VRAM, so on an 8 GB GPU stay on the 3B default. `flowlocal-cleanup` remains the
shipped default so a fresh clone works everywhere.

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
