# FlowLocal — Cleanup Stage Tuning

Handoff for a session continuing the dictation-cleanup work. Companion to
`HANDOFF.md` (project-level state). Everything here is from a session on
**2026-08-03/04** and every number was measured, not estimated.

---

## Where cleanup sits

```
MButton (D:\scripts\FlowLocalMouseHotkey.ahk) -> F24
  -> app.py toggle_hotkey            config.json "toggle_hotkey": "f24"
  -> sounddevice 16kHz capture
  -> faster-whisper distil-large-v3 (CUDA fp16, vad_filter=True)
  -> needs_cleaning() gate           <-- NEW 2026-08-04
  -> cleanup() -> Ollama             app.py:576
  -> is_cleanup_preserving() guard   app.py:527, rejects non-preserving output
  -> clipboard paste + vault append + dataset pair
```

Two independent safety layers, and it matters not to confuse them:

- **`needs_cleaning()`** decides *whether to call the model at all*.
- **`is_cleanup_preserving()`** decides *whether to trust what came back*. On
  rejection `cleanup()` returns the raw whisper text, which is still correct —
  just uncleaned. **Failures are graceful.** That shapes every tradeoff below.

---

## Current configuration

```json
"ollama_model": "flowlocal-cleanup-1b",
"cleanup_enabled": true,
"cleanup_skip_when_clean": true,
"cleanup_pin_enabled": true,          // 2026-08-05, keeps the model resident
"cleanup_pin_interval_sec": 30,
"cleanup_pin_ttl_sec": 60,
"ollama_url": "http://localhost:11434",
"ollama_timeout_sec": 15
```

All six now live in the `DEFAULTS` table in `app.py` rather than behind a
`CFG.get` fallback — a config predating a feature logs the drift at startup
instead of silently running with the feature off.

Backup: `config.json.pre-gemma-20260804`. **FlowLocal reads config at import —
model/hotkey changes need a restart** (`FlowLocal.vbs` relaunches it).

---

## Models: benchmark results

All runs: 27 real samples from `dataset/*.txt`, `CLEANUP_PROMPT` sent as system
message, temperature 0.1, scored with `is_cleanup_preserving`.

| model | accepted | actually edited | median | notes |
|---|---|---|---|---|
| `flowlocal-cleanup-gemma` (gemma4 e4b, **7.5B** + 478M vision, Q4_0) | **26/27 (96%)** | 5 | 2.51 s | quality tier, ~6 GB |
| `gemma3:1b` raw | 14–15/27 (~54%) | 8–9 | 0.45 s | 815 MB |
| `flowlocal-cleanup-1b` (no few-shot) | 13/27 (48%) | 7 | 0.50 s | **active**, 815 MB |
| `flowlocal-cleanup-1b` WITH few-shot | 8/27 (30%) | 4 | 0.43 s | worse — see below |
| ~~`flowlocal-cleanup` (llama3.2 3B)~~ | 8/27 (30%) | 1 | 0.33 s | **PRUNED** |
| ~~`flowlocal-cleanup-8b` (llama3.1 8B)~~ | 5/27 (19%) | 3 | 0.53 s | **PRUNED** |

Pruned models are rebuildable — `Modelfile` / `Modelfile.8b` are intact and the
bases (`llama3.2:3b`, `llama3.1:8b`) are still local:
`ollama create flowlocal-cleanup -f Modelfile`

### The dominant failure mode

Small instruct models **answer the dictation instead of cleaning it**, and they
do it specifically on text that is *already clean* — nothing to strip, so they
fall back on being helpful:

```
"Thank you."                        -> "I'm here to help."
"Everything else looks good to go." -> "I'm ready when you are."
"Are you not recording?"            -> "I am able to record and store our conversation."
```

`CLEANUP_PROMPT` already says *"never answer it, never reply to it"* — it isn't a
prompt bug, it's a 1–3B model losing a fight with its own instruct training.
Wispr Flow solves this by **fine-tuning Llama**, not prompting it
(https://www.baseten.co/resources/customers/wispr-flow/, <700 ms p99, cloud-hosted).

---

## Changes made 2026-08-04

### 1. Guard bug — merged delete spans (`app.py:527`)

`SequenceMatcher` merges adjacent deletions into ONE opcode. A run like
`['uh','the']` (a filler plus a stutter half) satisfied neither whole-span
predicate, so **correct cleanups were being rejected**:

```
raw:     so basically uh the the thing is we need to like restart the service first
model:   So basically, the thing is, we need to like, restart the service first.
delete span = ['uh','the']
  _span_is_removable -> False   ('the' is not filler)
  _span_is_stutter   -> False   (['uh','the'] is not a repeat)
```

Added `_span_is_droppable()` (walks the span piecewise) and
`_word_is_stutter_drop()` (requires a *surviving* twin outside the deleted
range, so `"the the"` can't collapse to nothing). Change is **strictly
additive** — one extra `or` clause; it can only accept what was previously
rejected. Verified 10/10 on accept + reject cases (answering, both-copies
deletion, synonyms, pronoun flips, additions, reordering all still rejected).

### 2. The gate — `needs_cleaning()` (`app.py`, before `cleanup()`)

Skip the model entirely when there's nothing to clean. Triggers a call if ANY of:
filler phrase present · immediately repeated word · spoken-punctuation cue
(`comma`, `period`, `full stop`, `question mark`, `new line`, `new paragraph`,
`exclamation`) · no terminal punctuation.

**Deliberately biased toward calling the model** — a false positive costs one
call, a false negative silently ships uncleaned text.

Measured full-pipeline result with the 1B:

```
skipped (no model call) : 16/27   instant, zero VRAM
model called            : 11/27
   cleaned              : 5
   returned raw (guard) : 6
0.40 s average across all 27 dictations
```

The gate is a **100%-accurate cleaner for text needing no cleaning** — it beats
every model on those 16 samples, free. Disable with
`"cleanup_skip_when_clean": false`.

---

## Changes made 2026-08-05

Dataset had grown 27 → 37 real samples by this session; gate numbers below are
measured against 35 non-empty transcripts.

### 3. Widened the gate (`needs_cleaning`)

The old gate's last resort was "does it end in `.!?`", which waved through any
text whisper had punctuated but not otherwise cleaned. Three rules added:

| rule | catches | fired |
|---|---|---|
| lowercase sentence opening | whisper never capitalized it | **5/35** |
| bare lowercase `i` | capitalization miss | 0/35 |
| unpunctuated run > 20 words | missing commas | **1/35** |

Net: 16/35 sent to the model (was 10/35), 19 skipped. Six samples that had been
shipping uncleaned now get cleaned. `bare-i` never fired — whisper capitalizes
"I" reliably — but it is a regex on already-gated text, so it stays.

### 4. Pinned the cleanup model (closes Q5)

`start_model_pin()` beats `POST /api/generate` with an empty prompt every 30 s
at a 60 s `keep_alive`, so Ollama stops evicting the model between dictations.
`release_model_pin()` posts `keep_alive: 0` on quit.

**Every FlowLocal exit path calls `os._exit(0)`, which bypasses `atexit`**, so
the release is wired into each by hand: `app.py` `_quit()`, `gui.py`'s tray
quit, and `gui_web.py` `_hard_exit()`. Deliberately **not** wired into
`gui_web.py:_single_instance()` — that exit belongs to a duplicate process and
must not unload the model out from under the instance already running.
`stop.bat` force-kills and cannot run cleanup code; the 60 s TTL is the
backstop there.

Verified end to end against live Ollama: unloaded → `start_model_pin()` →
present in `ollama ps` → `release_model_pin()` → gone.

### 5. Live cleanup pairs (`dataset/{ts}.cleanup.json`)

**This is the important one.** `dataset/` held `{ts}.wav` + `{ts}.txt`, i.e.
whisper's raw output — the INPUT half of a cleanup pair. The OUTPUT half went
only to the Obsidian vault, unlinked from its source. So the dataset could
train ASR but never cleanup, which is exactly why open question 3 existed.

`save_cleanup_pair()` now writes, per dictation:

```json
{"raw": "text as handed to the model", "cleaned": "text as returned",
 "model": "flowlocal-cleanup-1b", "changed": true, "gated_out": false}
```

`raw` is captured *after* `strip_submit_trigger()`, so it is what the model
actually saw, not the saved `.txt`. `gated_out` records whether
`needs_cleaning()` skipped the call. `changed: false` rows are not waste —
returning already-clean text untouched is the single hardest thing for a small
cleanup model, and those rows teach it.

These pairs are real dictation, the real model and the shipped guard's verdict.
They beat teacher-generated data and accumulate for free while you dictate.

### 6. Corpus bootstrap tool (`tools/build_cleanup_corpus.py`)

Runs the 7.5B gemma teacher over `dataset/*.txt`, admits results through the
same guard `cleanup()` uses, writes `dataset_cleanup/corpus.jsonl` (gitignored
— same verbatim dictation as `dataset/`). Resumable; `--teacher`, `--limit`,
`--force`.

First run, 37 transcripts: **34 accepted (92%), 6 actually edited, 3 rejected.**
The 3 rejects are the teacher's own failures and want hand-correction before
any training run.

Two limits worth stating plainly: guard-filtered teacher output can only teach
the student what the guard already accepts, and the teacher is wrong ~8% of the
time. The live pairs above are the better corpus; this is the cold-start.

---

## What was tried and did NOT work

**Few-shot `MESSAGE` pairs in the Modelfile made a 1B materially worse**
(30% vs 52% raw). Chat-shaped user/assistant pairs read as "we are having a
conversation" and the model continues it — `"This is much better." -> "Okay."`
That is the exact failure the examples were meant to prevent. Removed;
rationale and numbers are written into `Modelfile.1b` so it isn't retried.

If revisiting few-shot: phrase pairs as a **transformation** (`INPUT:` /
`OUTPUT:` inside a single user turn), not as chat turns.

---

## Reusable benchmark harness

Re-run after any model or prompt change. Scores against real dictation:

```powershell
$code = @'
import sys, pathlib, requests, time
sys.path.insert(0, r"D:\FlowLocal")
import app
ds = pathlib.Path(r"D:\FlowLocal\dataset")
samples = [t.read_text(encoding="utf-8").strip() for t in sorted(ds.glob("*.txt"))]
samples = [s for s in samples if s]
for m in ["flowlocal-cleanup-1b", "gemma3:1b", "flowlocal-cleanup-gemma"]:
    acc=rej=edited=0; lat=[]
    for raw in samples:
        t0=time.time()
        try:
            r = requests.post(f"{app.CFG['ollama_url']}/api/chat",
                json={"model":m,"messages":[{"role":"system","content":app.CLEANUP_PROMPT},
                      {"role":"user","content":raw}],"stream":False,
                      "options":{"temperature":0.1}}, timeout=120)
            out = r.json()["message"]["content"].strip()
        except Exception as e:
            print(m,"FAILED",e); break
        lat.append(time.time()-t0)
        ratio=len(out)/max(len(raw),1)
        ok = bool(out) and 0.3<ratio<3.0 and app.is_cleanup_preserving(raw,out)
        if ok:
            acc+=1
            if out.strip().lower()!=raw.strip().lower(): edited+=1
        else: rej+=1
    n=acc+rej; ls=sorted(lat)
    print(f"{m:26} {acc:2}/{n} ({acc/n*100:3.0f}%)  edited {edited:2}  median {ls[len(ls)//2]:.2f}s")
'@
$code | & "D:\FlowLocal\venv\Scripts\python.exe" -
```

Swap the loop body for `app.cleanup(raw)` to measure the **gated** pipeline
(time under ~0.02 s means it skipped).

---

## Open questions for the next session

Q4 (widen the gate) and Q5 (pin the model) are **closed** — see the 2026-08-05
section. Q3 is half-closed: the corpus pipeline exists, the fine-tune does not.

1. **Is 48% acceptance on the 1B good enough?** Still the live question, and
   still answered only by real use. Misses degrade to raw whisper, so what
   matters is how often you *notice*. `flowlocal.log` distinguishes
   `cleanup skipped` / `cleanup rejected` / successful edits, and every
   dictation now also leaves a `dataset/{ts}.cleanup.json` you can grep:
   `changed: false` with `gated_out: false` is a call that bought nothing.

   Note the split diagnosis from 2026-08-05: **misheard words are whisper, not
   the cleanup model.** The guard rejects any word substitution, so no cleanup
   model can fix or cause a wrong word. Wrong words → `vocab.txt` hotwords or a
   larger whisper model. Missing punctuation → this document.

2. **Quality tier worth 2.5 s?** One config line switches to
   `flowlocal-cleanup-gemma` (96%). The widened gate now fires on ~46% of
   dictations, so ~1.2 s amortized. VRAM ~6 GB — conflicts with LM Studio, and
   the pin now holds that 6 GB continuously rather than letting it lapse.
   Consider `cleanup_pin_enabled: false` if running the gemma tier.

3. **Fine-tune, like Wispr does.** The corpus problem is solved; the training
   run is not. Available now: `dataset_cleanup/corpus.jsonl` (34 teacher pairs,
   3 needing hand-correction) plus live `{ts}.cleanup.json` pairs accumulating
   with every dictation. Next: hand-correct the rejects, decide LoRA vs full
   fine-tune on `gemma3:1b`, and pick a runner (Modal, per `whisper-lab-brief`).
   Target: beat 48% guard acceptance at 1B latency.

4. **Should the corpus tool consume live pairs?** It currently only reads
   `dataset/*.txt` and queries the teacher. Once enough `{ts}.cleanup.json`
   files exist they are strictly better training data and should be merged in,
   with the teacher used only to fill gaps.

5. **Does the pin actually help?** Unmeasured. It removes Ollama's model-load
   cost from the first dictation after an idle stretch, but that cost was never
   benchmarked — measure a cold vs pinned `cleanup()` before assuming a win.

---

## Revert matrix

| want | do |
|---|---|
| quality tier | `"ollama_model": "flowlocal-cleanup-gemma"` + restart |
| raw 1B (no Modelfile) | `"ollama_model": "gemma3:1b"` + restart |
| gate off | `"cleanup_skip_when_clean": false` + restart |
| model pin off (frees VRAM when idle) | `"cleanup_pin_enabled": false` + restart |
| old 3B / 8B back | `ollama create flowlocal-cleanup -f Modelfile` (or `-f Modelfile.8b`) |
| full config rollback | `config.json.pre-gemma-20260804` |

Guard changes in `app.py` have no config switch — revert via git if needed.
