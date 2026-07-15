# Plan 3 — FlowLocal → Jarvis (Local Voice Assistant)

**Goal:** Extend FlowLocal (D:\Claude\Projects\wisprflow-clone) from dictation tool into full voice loop: speak → transcribe → route to LLM → spoken reply. All local, no cloud API cost.

**Effort:** Weekend project after Plans 1-2 land. Plan 2's TTS daemon is a prerequisite/shared component.

---

## Current state vs target

| Piece | Have | Add |
|---|---|---|
| STT | distil-large-v3 (FlowLocal) | — |
| LLM | Ollama llama3.2:3b | Router: quick vs deep queries |
| TTS | none | Kokoro daemon (built in Plan 2 v1) |
| Wake word | none | openWakeWord (optional, v2) |
| Turn detection | push-to-talk? | Silero VAD (v2) |

## Architecture

```
Mic ─► FlowLocal STT (distil-large-v3)
         │ transcript
         ▼
      Router (dead simple, v1: keyword/length heuristic)
         ├─ quick Q&A / commands ──► Ollama (qwen3:8b or llama3.2:3b)
         └─ real work ("code...", "in my project...") ──► claude -p "..." (headless Claude Code)
         │ response text
         ▼
      Kokoro TTS daemon (localhost:8123) ─► speakers
```

## Build phases

### Phase 1 — Dictate → LLM → speak (core loop)
1. Stand up Kokoro daemon from Plan 2 (FastAPI, model loaded once, `POST /speak {text}` → plays audio). Single shared TTS service for both Claude Code hook and FlowLocal
2. FlowLocal: add "assistant mode" toggle (vs pure dictation mode). Hotkey to switch
3. Assistant mode: on transcription final → POST to Ollama `/api/chat` (qwen3:8b better than llama3.2:3b for assistant duty; you have both, 3b fallback for speed) → response → POST to TTS daemon
4. System prompt: terse voice-assistant persona, answers ≤3 sentences unless asked. Speech is slow — verbosity is pain here
5. Streaming optimization: sentence-chunk Ollama's stream, send each completed sentence to TTS immediately. Cuts perceived latency from ~5s to ~1s

### Phase 2 — Claude Code integration (real Jarvis)
- Router detects work intents → shells `claude -p "<transcript>" --output-format text` in target project dir (headless mode)
- Or simpler: assistant mode types transcript into focused cca terminal (FlowLocal already does keystroke injection — reuse) and Plan 2's Stop hook speaks reply. Zero new plumbing — **this is the cheapest path to voice-in/voice-out Claude Code**
- Voice commands: "switch to sawgrass", "check the PO inbox" → mapped to cd + prompt templates

### Phase 3 — Hands-free (optional polish)
- **Wake word:** openWakeWord (`pip install openwakeword`) — pretrained "hey jarvis" model ships with it. Runs on CPU, always-listening
- **VAD:** Silero VAD to auto-detect end of speech instead of push-to-talk
- **Barge-in:** mic input while TTS playing → stop playback, listen
- **Multi-machine:** daemon endpoints over personal tailnet (brotula-economy.ts.net) — speak to laptop, inference on Alienware 5080

## Hardware fit
- 4070 8GB (laptop): distil-large-v3 + qwen3:8b + Kokoro concurrent = tight but OK (Kokoro is CPU-fine, STT ~1.5GB, qwen3:8b Q4 ~5GB)
- 5080 16GB (Alienware): everything comfortably, or bigger model (qwen3:14b)

## Verify before building (research tasks for cca day-of)
1. Current Kokoro pip API (github.com/hexgrad/kokoro) — API churns
2. openWakeWord pretrained model list + current install
3. `claude -p` headless flags on your prerelease build (`claude --help`)
4. FlowLocal code review: where transcription-final event fires → cleanest injection point for assistant-mode branch

## Order of operations (whole stack)
1. **Tomorrow (work):** Plan 1 — AgentMail PO webhook on TSH
2. **This week (30 min):** Plan 2 v0 — SAPI Stop hook. Immediate QoL win
3. **This week (1 hr):** Plan 2 v1 — Kokoro daemon, natural voice
4. **Weekend:** Plan 3 Phase 1-2 — FlowLocal assistant mode + Claude Code voice loop
5. **Later:** Phase 3 hands-free, AgentMail inbox 2/3 (TSH alerts, job-search triage)
