# FlowLocal — Session Handoff

State of the project for any Claude Code / Cowork session picking this up.
Repo: `wtcrowe4/FlowLocal` (public, MIT). Local: `D:\Claude\Projects\wisprflow-clone`.

## What it is

Local Wispr Flow clone. Hold Right Ctrl → speak → release → text pasted into the
active window. Nothing leaves the machine. Built July 2026 for Thomas (wtcrowe4).

## Pipeline

```
hotkey (keyboard raw hook)
  → sounddevice 16kHz mono capture (+350ms tail after key release)
  → faster-whisper distil-large-v3, CUDA fp16, beam 3, VAD pad 400ms,
    hotwords from vocab.txt
  → optional Ollama cleanup (flowlocal-cleanup / flowlocal-cleanup-8b Modelfiles)
  → clipboard paste (old clipboard restored)
  → optional: append to Obsidian vault, save wav+txt training pair
```

Ask mode (`ctrl+alt+space` toggle): dictation is treated as a question →
POST to personal-rag `/query` → top chunks → local LLM answers → answer pasted.

## Files

| File | Role |
|---|---|
| `app.py` | Engine: recorder, whisper, cleanup, inject, hotkeys, tray, vault/dataset/ask integrations. Headless entry. |
| `gui.py` | tkinter GUI: PIL-rendered skin, waveform overlay pill, settings, vocab editor. Main entry (`run.bat`). |
| `config.json` | All settings. GUI writes it; hotkey/model changes need restart. |
| `vocab.txt` | Whisper hotwords, auto-reloads on change. |
| `Modelfile`, `Modelfile.8b` | Ollama cleanup model builds. |
| `make_icon.py` | Renders icon.ico/png (512px blue mic). |
| `create_shortcut.bat` | Icon + desktop shortcut (pythonw target). |
| `setup_autostart.bat` / `FlowLocal.vbs` / `stop.bat` | Startup / silent launch / kill (game-mode script calls stop.bat). |
| `dataset/` | Opt-in wav+txt pairs for fine-tuning. GITIGNORED — personal voice data, never push. |

## personal-rag endpoint (ask mode)

- URLs tried in order: `http://localhost:8787`, `http://TAILNET-HOST-1:8787`, `http://TAILNET-HOST-2:8787` (tailnet)
- `POST /query` JSON `{"query": "raw question", "k": 5}` — no instruction prefix, server adds it. k max 50.
- Response `{"results": [{file_path, heading_path, content, metadata, score}]}` — cosine 0–1, >0.6 strong, <0.4 noise (FlowLocal filters ≥0.4).
- Health: `GET /healthz`. Server: systemd user service `personal-rag-serve.service` (auto-restart, linger enabled — survives WSL restarts). Logs: `journalctl --user -u personal-rag-serve.service`.
- Full spec: `docs/API.md` in `wtcrowe4/personal-rag`.
- Vault ingest: `personal-rag-ingest.timer` runs `rag ingest --changed-only` every 15 min (Persistent=true). Dictation → searchable latency ≤15 min. Unit files in `deploy/` of personal-rag repo.
- Boot order: both need the pgvector Docker container → Docker Desktop must be running on Windows. Failed ingests self-heal on next timer fire.

## UI state (web GUI, gui_web.py + web/)

- Main HUD (hud.html): Thomas's Claude Design port. Frame fills window; drag = pointer
  deltas -> Api.move_by (native WM_NCLBUTTONDOWN hand-off does NOT work with WebView2).
- Pill (pill.html): separate always-on-top toolwindow, click=toggle, drag=move (pos saved).
- RESOLVED (2026-07-11): the "gray box" was two bugs. (1) pywebview's `min_size`
  defaults to (200,100), silently clamping the 18px pill up to 100px tall - fixed by
  passing `min_size=(PILL_W, PILL_H)`. (2) True per-pixel WebView2 transparency is NOT
  achievable on this stack: `transparent=True` composites correctly at the browser layer
  but the WinForms host form stays opaque, so every transparent pixel falls back onto the
  form's solid BackColor (proved: transparent CSS region samples (240,240,240); DWM
  acrylic only recolors it to (255,255,255), never reveals the desktop). The one path that
  DID produce see-through - WS_EX_LAYERED + LWA_ALPHA - is the one that crashed WebView2's
  GPU compositor (took Flow + terminal down), so it is permanently out.
- CURRENT APPROACH (crash-safe, no layered window): both windows are opaque dark panels
  (`background_color` HUD_BG/PILL_BG, `transparent=False`) styled to read as glass. The
  pill is clipped to a stadium via `SetWindowRgn(CreateRoundRectRgn(...))` (a plain clip,
  not alpha - cannot touch the GPU compositor); the main window gets Win11 DWM rounded
  corners via `DwmSetWindowAttribute(33, DWMWCP_ROUND)`. Pill glass look = CSS gradient +
  inset blue border + top highlight in pill.html. `window_alpha`/`pill_alpha`/`acrylic`
  config keys are now unused; `_apply_acrylic` is dead code kept for reference.
- If TRUE see-through glass is ever wanted: draw the pill as a native win32 layered window
  with UpdateLayeredWindow + a GDI+ bitmap (no WebView2 child, so no compositor crash) and
  redraw the waveform on a timer. Bigger build; the current opaque lozenge is the shipped MVP.

## Known issues / gotchas

- **Input lag**: global keyboard hook routes all keystrokes through Python; user saw mouse/system lag that stopped when FlowLocal quit. Parked. Candidate fixes: move hook to C-level filter, or dedicated thread priority.
- **Both Ctrl keys share scan code 29** — hold-key detection MUST match `event.name == "right ctrl"` via raw hook, never `on_press_key("right ctrl")`.
- **pythonw crashes on print()** — always use `app.log()`, writes to `flowlocal.log` (gitignored, contains transcripts).
- **HuggingFace downloads** need `HF_HUB_DISABLE_XET=1` (set in app.py; `download_model.bat` retries).
- **cuBLAS/cuDNN**: nvidia pip wheels' bin dirs must be prepended to PATH (done in `_register_nvidia_dlls`) — `add_dll_directory` alone fails.
- Wispr Flow may run simultaneously (trial ends soon); its hotkey is Ctrl+Win.

## Roadmap

0. **TTS readback** (next up) — see `docs/PLAN-tts-readback.md`. Claude Code Stop
   hook + kokoro-onnx daemon, native Windows. Solves Thomas's actual bottleneck
   (slow reading of Claude responses). Notably does NOT require changing this
   repo — FlowLocal is already the input half. herdr is an optional helper for
   focus-gating and "agent blocked" cues, not a dependency. That plan also
   documents corrections to the original Downloads plans (wrong kokoro package,
   unnecessary WSL) and notes that **ask mode at `app.py:418` is already the
   Plan 3 "core loop" minus a `speak()` call**.
1. **pywebview UI port** — Thomas is designing in Claude; port design as HTML/CSS over existing engine events (`app.on_event("state"|"transcript")`).
2. **whisper-lab** (separate repo/session) — Modal-based model eval + fine-tune. See `docs/whisper-lab-brief.md`.
3. Vault ingest automation lives in personal-rag (cron `--changed-only`).
4. Parked: input-lag investigation, AWCC lighting-color sync (no public API).

## User preferences (Thomas)

Caveman mode: concise, no filler, lead with answer. Research before answering.
Accent color: electric blue #00a8ff (Alienware/Logitech). No purple.
Log context to Obsidian vault at `D:/Personal Vault` when asked (work machine: `D:/Obsidian/calibrationwands`).
