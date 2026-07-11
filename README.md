# FlowLocal

Local Wispr Flow clone. Dictate into any Windows app. Nothing leaves your machine.

## Pipeline

Hold hotkey → mic records → faster-whisper (GPU) transcribes → optional Ollama cleanup (fillers, punctuation) → text pasted into active window.

## Install

1. Run `install.bat` (needs Python 3.10+)
2. Optional cleanup pass: install [Ollama](https://ollama.com), then `ollama pull llama3.2:3b`
3. Run `run.bat` (GUI) or `run_headless.bat` (console + tray only). First launch downloads the Whisper model (~1.5 GB), one time only. If download fails, run `download_model.bat`.

## GUI

`run.bat` opens the desktop app: status indicator, Start/Stop button, transcript history with copy, and settings editor (model, hotkeys, cleanup toggle). A floating "Listening..." pill appears at the bottom of the screen while recording. Closing the window minimizes to tray; quit from tray menu.

## Use

- **Hold Right Ctrl**, speak, release → text appears where your cursor is
- **Ctrl+Shift+Space** toggles recording on/off (hands-free)
- Tray icon: gray = idle, red = recording, yellow = processing
- Beeps confirm start/stop (disable in config)

## Config (`config.json`)

| Key | Default | Notes |
|---|---|---|
| `hold_hotkey` | `right ctrl` | Hold-to-talk key ([key names](https://github.com/boppreh/keyboard)) |
| `toggle_hotkey` | `ctrl+shift+space` | Toggle mode |
| `whisper_model` | `distil-large-v3` | Smaller/faster: `small.en`, `base.en` |
| `device` | `auto` | `cuda`, `cpu`, or `auto` (GPU with CPU fallback) |
| `cleanup_enabled` | `true` | Ollama pass; auto-skipped if Ollama not running |
| `ollama_model` | `llama3.2:3b` | Any local model |
| `restore_clipboard` | `true` | Puts old clipboard back after paste |

## Troubleshooting

- **GPU not used** — console says "falling back to CPU". Check `nvidia-smi` works; cuBLAS/cuDNN wheels install via requirements.txt.
- **Hotkey does nothing in some apps** — apps running as Administrator need FlowLocal run as Administrator too.
- **Text pastes twice / not at all** — some apps block Ctrl+V briefly; try again or raise the `time.sleep` in `inject_text`.
- **Slow transcription** — switch `whisper_model` to `small.en`.

## Privacy

Whisper and Ollama both run on localhost. No network calls except model downloads on first run.
