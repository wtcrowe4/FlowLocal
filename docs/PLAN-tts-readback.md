# Plan — TTS Readback + Voice Loop (revised 2026-07-15)

Supersedes `plan-2-claude-code-tts-readback.md` and `plan-3-flowlocal-jarvis.md`
(Downloads/agentmail-jarvis-tts.zip). Those are still worth reading for context;
the corrections below are all **verified against the live install on Alienware**,
not from the model's memory.

**Problem being solved:** Thomas reads slowly. Claude Code's responses are the
bottleneck. Everything else in this doc is optional.

---

## The one-line summary

The fix is a **Claude Code Stop hook** + a **kokoro-onnx TTS daemon**, both
native Windows. It does **not** require changing FlowLocal at all. herdr is a
*helper* on top, not a dependency.

---

## Corrections to the original plans

| Original plan said | Reality (verified 2026-07-15) |
|---|---|
| `pip install kokoro` (hexgrad) | **Dormant.** Last push 2025-08-06, PyPI 0.9.4 (Apr 2025), 193 open issues, pinned `<3.13`. Use **`kokoro-onnx`** — same Kokoro-82M weights, MIT wrapper, v0.5.0 (Jan 2026), pushed 2026-07-05, no torch. |
| `sudo apt install espeak-ng`, WSL venv, `wslpath`, PowerShell playback round-trip | **All unnecessary.** `kokoro-onnx` pulls `espeakng-loader`, which ships prebuilt espeak-ng DLLs for Windows x86-64 and auto-wires them. Whole stack is native Windows. |
| TTS daemon on `localhost:8123` from WSL | Would have failed — Ollama notes confirm NAT mode (`OLLAMA_HOST=172.30.96.1:11434`), so `localhost` doesn't cross into WSL. **Moot now** — nothing is in WSL. |
| Sentence-chunk the stream yourself | `kokoro_onnx.create_stream()` is an async generator that chunks internally. Free. |
| Hardware fit table (4070 vs 5080 for TTS) | Irrelevant. 82M params, CPU-first. Skipping CUDA also skips onnxruntime version-matching pain. Add `[gpu]` only if measurement demands it. |
| Cap spoken text at 1500 chars | ~2 min of audio — *worse* than reading. Claude Code is instructed to lead with the outcome, so the **first 1–2 sentences are already the TLDR.** Speak those. |
| Phase 1 "core loop" = weekend | **Already built.** See "ask mode" below. |
| Piper as an alternative | `rhasspy/piper` (MIT) archived read-only Oct 2025. Moved to `OHF-Voice/piper1-gpl` = **GPL-3.0**, which reaches your code if you link it. Bad fit for two commercial brands. XTTS (CPML) and F5-TTS (CC-BY-NC) are non-commercial — also out. **Kokoro's Apache-2.0 model + MIT wrapper is the clean option.** |

### Not benchmarked — measure, don't trust
No verified realtime-factor numbers exist for these engines. Nearly every
specific figure online traces to AI-generated SEO content farms (one cited an
"M5 Max"). Chatterbox's "75ms" is a vendor claim with hardware unspecified.
**Benchmark on the actual box.**

---

## Architecture

```
Claude Code finishes
  └─► Stop hook (exact, gives transcript_path + session_id)
        └─► extract last assistant text  ── filter isSidechain! ──┐
                                                                  │
herdr (optional helper, native Windows 0.7.1-preview)             │
  ├─ $env:HERDR_PANE_ID  (free inside a pane)                     │
  ├─ pane focused? ──► speak now, or queue                        │
  └─ agent_status == "blocked" ──► "agent needs you" cue          │
                                                                  ▼
                                              kokoro-onnx daemon (localhost)
                                                        └─► speakers
```

**Division of labor — this is the key design decision:**
- **Stop hook = content.** It's exact and hands you `transcript_path` directly.
- **herdr = context.** Which agent, which pane, is it focused, is it blocked.

**Do NOT use herdr as the completion trigger.** Per herdr's own docs, Claude Code
is *screen-manifest detected*, not hook-authoritative — its `done` state is
inferred by scraping the pane buffer, "deliberately strict." The Stop hook is
exact. Also structural: the `pane.agent_status_changed` payload (pulled from the
live schema) is `{pane_id, workspace_id, agent_status, agent, custom_status,
display_agent, state_labels, title}` — **no response text, no transcript path.**
herdr can tell you *that* an agent finished, never *what it said*.

**DO use herdr for what the Stop hook can't do:** `blocked` status. Stop fires on
*finish*; `blocked` means *awaiting your approval/input*. Different, useful cue —
especially across multiple panes. Claude Code's Notification hook overlaps, but
herdr's covers all 15+ agents uniformly.

---

## Verified facts about the local environment

- **herdr 0.7.1-preview.2026-06-30** — native Windows, `C:\Users\wtcro\AppData\Local\Programs\Herdr\bin\herdr.exe`. Windows ships **preview-channel only**, no stable timeline.
- **herdr's Claude integration is already installed**: `~/.claude/hooks/herdr-agent-state.ps1` (HERDR_INTEGRATION_VERSION=7), registered in `~/.claude/settings.json` under **SessionStart only**. It reports `session_id ↔ pane_id` via `herdr pane report-agent-session`.
  - **The Stop slot is free.** No conflict.
  - That file is herdr-managed and gets overwritten on update — **add custom hooks beside it, never edit it.**
  - Note it already guards `SubagentStop` — copy that instinct.
- **Socket API is live and real.** `herdr api schema --json` is authoritative. Confirmed: `pane.agent_status_changed`, `pane.output_matched`, `events.subscribe`, `pane.list`, `session.snapshot`.
  - `AgentStatus` enum: `idle | working | blocked | done | unknown`
  - `PaneInfo` carries `focused: boolean` and `agent_session` → **focus gating without pywin32.**
- **Plugin system**: `herdr-plugin.toml` with `[[events]]` runs any executable, gets `HERDR_PLUGIN_EVENT_JSON`. `herdr plugin list` → currently none installed.
- `[ui.toast]` is **display-only** — no custom command hook. TTS goes via plugin `[[events]]` or `events.subscribe`.

---

## The Plan 3 shortcut nobody noticed

`app.py:418` — **ask mode already is Plan 3 Phase 1, minus TTS**:

```python
if _rec_mode == "ask":
    answer = ask_rag(raw_text)
    inject_text(answer)          # <- swap for speak(answer)
    _emit("transcript", f"Q: {raw_text}\nA: {answer}")
```

Speak -> whisper -> RAG -> Ollama -> answer. Built and working. It *pastes* the
answer instead of speaking it. The plan budgeted a weekend for a loop that is
~10 lines and a `speak()` away.

Event hooks for a cleaner injection point already exist: `on_event("transcript", fn)`
(`app.py:84-92`).

---

## Cut list (was in Plan 3, don't build)

- **Router (quick vs deep -> Ollama vs Claude).** You know which you want — that's
  what the two hotkeys are. And it routes *work* away from Claude to an 8B model
  to save money you aren't spending. Max plan.
- **Wake word (openWakeWord).** Right Ctrl works. Wake words false-fire. Worse:
  HANDOFF flags an unresolved input-lag bug from the global keyboard hook —
  an always-on listener makes it worse.
- **VAD / turn detection (Silero).** Push-to-talk is *more* precise, not less.

---

## Build order

### Phase 0 — Stop hook + daemon (the whole ask, ~30-45 min)
No herdr, no FlowLocal changes.

1. `pip install -U kokoro-onnx sounddevice` — no espeak-ng MSI, no WSL.
2. Fetch model files:
   - `kokoro-v1.0.onnx` and `voices-v1.0.bin` from
     `github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/`
3. Daemon: model loaded once, HTTP `POST /speak {text}` on localhost.
   Needs a **producer/consumer queue** — upstream's `sd.play()/sd.wait()` example
   blocks per chunk and gaps between them.
4. `~/.claude/hooks/tts_stop.ps1` (or .py) — Stop hook:
   - guard `stop_hook_active` (hook loops)
   - guard `CLAUDE_TTS_OFF` env kill switch
   - parse `transcript_path` JSONL -> last assistant text
   - **filter `isSidechain`** — subagent output otherwise gets spoken instead of
     Claude's actual answer
   - strip code blocks / markdown / URLs
   - **first 1-2 sentences only**, hard cap
   - POST to daemon
5. Register under `Stop` in `~/.claude/settings.json`, merging alongside herdr's
   SessionStart entry.

**Verify hook schema before wiring** — prerelease build, schema moves.

### Phase 1 — herdr helpers (only once Phase 0 works)
- Read `$env:HERDR_PANE_ID` in the Stop hook (free inside a pane).
- Ask herdr whether that pane is focused -> speak now, or queue. Kills Plan 2 v2's
  pywin32 `GetForegroundWindow` polling hack entirely.
- Subscribe `pane.agent_status_changed` -> on `blocked`, speak "<agent> needs you".
- Multi-agent: announce by pane label so parallel agents don't talk over each other.

### Phase 2 — optional, only if wanted
- FlowLocal ask mode: `inject_text(answer)` -> `speak(answer)`. Ten lines.

### Deferred
- Tailnet multi-machine daemon (`brotula-economy.ts.net`).
- Wake word / VAD — see cut list.

---

## Deploy to other machines

Hook script + settings merge belong in `wtcrowe4/claude-config`'s install script
-> Alienware, ROG, engraver-rig, Surface. Daemon needs the model files (~300MB,
~80MB int8) — don't commit those to git; fetch in the installer.

---

## Fallback

`NaturalVoiceSAPIAdapter` (MIT, v0.2.9, Jan 2026) registers Narrator's **local,
fully offline** natural voices as SAPI5 — reachable from PowerShell/pyttsx3.
Or expose Win11 OneCore voices to `System.Speech` via registry copy (Admin):

```
reg copy "HKLM\SOFTWARE\Microsoft\Speech_OneCore\Voices\Tokens" "HKLM\SOFTWARE\Microsoft\Speech\Voices\Tokens" /s /f
```

Zero-install fallback, and better than the robotic David/Zira default. Not the
primary — no streaming control, COM/registry fragility.
