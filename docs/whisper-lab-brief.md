# whisper-lab — Project Brief (for new Claude Code session)

Standalone project: evaluate + fine-tune Whisper models on Thomas's own voice
using the FlowLocal dataset and the $30 Modal credit. Create as a NEW repo
(`wtcrowe4/whisper-lab`), separate folder, own Claude Code session.

## Inputs

- Dataset: `D:\Claude\Projects\wisprflow-clone\dataset\` — paired `YYYYMMDD_HHMMSS.wav`
  (16kHz mono int16) + `.txt` (raw Whisper transcript). Grows as Thomas dictates
  with the training checkbox on. PERSONAL VOICE DATA — keep the repo private or
  keep data out of git.
- Transcripts are machine-generated: treat as noisy labels. Phase 1 output
  includes a review step (correct the .txt files) before any fine-tune.
- Modal account with $30 credit (~27 A10G GPU-hours at ~$1.10/hr).

## Phases

**Phase 1 — Eval harness (do first, cheap: <$2)**
Run the eval set through candidate models on a Modal GPU, produce a WER/latency
table on HIS voice: distil-large-v3, large-v3-turbo, large-v3, small.en,
medium.en, + nvidia/parakeet-tdt-0.6b-v3 (different runtime — NeMo — which is
exactly why it goes on Modal, not on the Alienware). Output: markdown report,
pick the best default for FlowLocal.

**Phase 2 — Fine-tune (only if Phase 1 shows headroom, ~$5-15)**
LoRA fine-tune distil-large-v3 (HF transformers + peft) on corrected pairs.
Needs 200+ pairs minimum, 500+ better. Then convert to CTranslate2:
`ct2-transformers-converter --model <ft-dir> --output_dir flowlocal-ft --quantization float16`
FlowLocal consumes it by setting `whisper_model` to the local folder path —
faster-whisper accepts local CT2 dirs. Zero app changes.

**Phase 3 — Regression loop**
Re-run Phase 1 eval on the fine-tuned model; keep only if WER beats baseline.

## Proposed structure

```
whisper-lab/
├── README.md
├── pyproject.toml            # uv-managed
├── data/                     # gitignored; sync from FlowLocal dataset/
│   ├── manifest.jsonl        # {audio, text, duration, split}
│   └── raw/
├── src/whisper_lab/
│   ├── prepare.py            # build manifest, train/eval split, dedupe
│   ├── review.py             # CLI to play audio + correct transcript labels
│   ├── eval_modal.py         # Modal app: WER table across models
│   ├── finetune_modal.py     # Modal app: LoRA fine-tune
│   └── export_ct2.py         # HF -> CTranslate2 for faster-whisper
└── results/                  # WER reports, loss curves (committed)
```

## Notes for the session

- WER via `jiwer`, normalize text (lowercase, strip punctuation) before scoring.
- Modal: use `modal.Image` with torch+transformers+peft; mount data as Volume;
  A10G sufficient, T4 works for eval.
- Keep eval set frozen (e.g. every 5th pair) so Phase 1/3 numbers compare.
- FlowLocal context if needed: `D:\Claude\Projects\wisprflow-clone\docs\HANDOFF.md`.
