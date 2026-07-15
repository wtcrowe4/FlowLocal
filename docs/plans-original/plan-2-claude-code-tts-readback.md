# Plan 2 — Claude Code Reads Responses Aloud (TTS via Stop Hook)

**Goal:** Every finished Claude Code response spoken aloud automatically. Kills the slow-reading bottleneck. Works in every `cca` session once configured.

**Effort:** v0 (Windows SAPI voice) ~30 min. v1 (Kokoro neural voice) ~1 hr.

---

## How it works

Claude Code fires a **Stop hook** when it finishes responding. Hook receives JSON on stdin including `transcript_path` (session JSONL). Script extracts last assistant message → strips code/markdown → speaks it.

```
Claude finishes → Stop hook → tts_stop.py
  → parse transcript JSONL → last assistant text
  → strip code blocks, markdown, URLs
  → speak (v0: Windows SAPI | v1: Kokoro WAV → playback)
```

## v0 — Windows SAPI (do this first, zero deps)

Robotic voice but works in 30 min, validates the whole pipeline. WSL can shell out to Windows.

### 1. Hook script

`~/.claude/hooks/tts_stop.py`:

```python
#!/usr/bin/env python3
"""Claude Code Stop hook: speak last assistant response."""
import json, re, subprocess, sys

def clean(text: str) -> str:
    text = re.sub(r'```.*?```', ' code block omitted. ', text, flags=re.DOTALL)
    text = re.sub(r'`[^`]+`', '', text)
    text = re.sub(r'https?://\S+', ' link ', text)
    text = re.sub(r'[#*_>|\-]{2,}', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:1500]  # cap ~90s of speech

def last_assistant_text(transcript_path: str) -> str:
    text = ''
    with open(transcript_path) as f:
        for line in f:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get('type') == 'assistant':
                parts = entry.get('message', {}).get('content', [])
                chunk = ' '.join(p.get('text', '') for p in parts
                                 if isinstance(p, dict) and p.get('type') == 'text')
                if chunk.strip():
                    text = chunk  # keep overwriting → ends with last one
    return text

def speak_sapi(text: str):
    # WSL → Windows SAPI. Escape single quotes for PS.
    ps = ("Add-Type -AssemblyName System.Speech; "
          "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          "$s.Rate = 2; "
          f"$s.Speak('{text.replace(chr(39), chr(39)*2)}')")
    subprocess.Popen(['powershell.exe', '-NoProfile', '-Command', ps],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def main():
    data = json.load(sys.stdin)
    if data.get('stop_hook_active'):   # guard against hook loops
        sys.exit(0)
    tp = data.get('transcript_path')
    if not tp:
        sys.exit(0)
    text = clean(last_assistant_text(tp))
    if text:
        speak_sapi(text)
    sys.exit(0)

if __name__ == '__main__':
    main()
```

`chmod +x ~/.claude/hooks/tts_stop.py`

### 2. Register hook

`~/.claude/settings.json` (merge into existing):

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.claude/hooks/tts_stop.py",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

> Hook schema evolves between Claude Code versions — you run prerelease. Before wiring, verify current format: `claude --help` / official hooks docs, or just ask cca "show me current Stop hook settings.json schema" and let it check its own docs.

### 3. Test
Open `cca`, ask anything short. Response should speak when it finishes. Then verify: long response gets truncated at cap, code-heavy response says "code block omitted."

### Kill switch
`export CLAUDE_TTS_OFF=1` — add guard at top of script:
```python
import os
if os.environ.get('CLAUDE_TTS_OFF'): sys.exit(0)
```

## v1 — Kokoro-82M (natural voice)

Kokoro-82M: 82M-param open TTS, Apache-licensed weights, faster-than-realtime on CPU, trivial on 4070/5080. Big quality jump over SAPI.

### Setup (WSL Ubuntu)
```bash
sudo apt install espeak-ng          # phonemizer dep
pip install kokoro soundfile        # in a venv under ~/llm/ (Linux fs, not /mnt/d)
```

### Swap speak function
```python
def speak_kokoro(text: str):
    from kokoro import KPipeline
    import soundfile as sf, tempfile, subprocess, os
    pipe = KPipeline(lang_code='a')            # American English
    wav_path = tempfile.mktemp(suffix='.wav')
    chunks = [audio for _, _, audio in pipe(text, voice='af_heart')]
    import numpy as np
    sf.write(wav_path, np.concatenate(chunks), 24000)
    win_path = subprocess.check_output(['wslpath', '-w', wav_path]).decode().strip()
    ps = f"(New-Object Media.SoundPlayer '{win_path}').PlaySync()"
    subprocess.run(['powershell.exe', '-NoProfile', '-Command', ps],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.unlink(wav_path)
```

**Perf note:** `KPipeline` init per-invocation adds ~2-5s. Fix: run a tiny persistent TTS daemon (FastAPI on localhost:8123, model loaded once); hook POSTs text to it. Do this if v1 latency annoys. Daemon also becomes the shared TTS service FlowLocal uses (Plan 3).

Verify current Kokoro pip API before coding — it's moved fast; check https://github.com/hexgrad/kokoro README (or have cca do it).

### Playback alternative
WSLg has PulseAudio — `paplay out.wav` may work directly in WSL, skipping powershell round-trip. Test; use whichever is reliable per machine.

## v2 — Focus-triggered queue (only if auto-speak too noisy)

- Hook writes WAV + entry to `~/.claude/tts-queue/` instead of playing
- Windows-side watcher (pywin32: `win32gui.GetForegroundWindow` + `GetWindowText`) polls 500ms; when foreground window title matches terminal running Claude → play unplayed queue items, mark done
- Per-session queues keyed by `session_id` (in hook stdin) if multiple cca sessions
- **Skip unless needed.** Focus detection across Windows Terminal/WSL is finicky; auto-speak probably fine

## Claude Desktop / claude.ai
No hook system. Not worth hacking (browser extension territory). You're consolidating on Claude Code anyway — TTS lives there.

## Deploy to all machines
Add hook script + settings merge to `claude-config` repo (wtcrowe4/claude-config) install script → Alienware, ROG, Surface, engraver-rig all get it.
