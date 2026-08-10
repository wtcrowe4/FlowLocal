"""Report how much of Whisper's hotword budget vocab.txt consumes.

faster-whisper truncates hotwords at `max_length // 2 - 1` tokens (448 // 2 - 1
= 223) inside WhisperModel.get_prompt. Terms past that point are dropped
silently - no warning, no log line - so a vocab list can quietly stop working
at the bottom as it grows. Run this after editing vocab.txt.
"""
import glob
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tokenizers import Tokenizer

import app as flowlocal

LIMIT = 448 // 2 - 1  # mirrors faster_whisper.transcribe get_prompt


def _tokenizer():
    pattern = os.path.expanduser(
        "~/.cache/huggingface/hub/**/models--Systran--faster-distil-whisper-large-v3/**/tokenizer.json"
    )
    hits = glob.glob(pattern, recursive=True) or glob.glob(
        os.path.expanduser("~/.cache/huggingface/hub/**/tokenizer.json"), recursive=True
    )
    if not hits:
        raise SystemExit("no Whisper tokenizer found in the HuggingFace cache")
    return Tokenizer.from_file(hits[0])


def main():
    terms = flowlocal.get_vocab()
    tok = _tokenizer()
    joined = " ".join(terms)
    total = len(tok.encode(" " + joined.strip()).ids)

    print(f"terms:  {len(terms)}")
    print(f"tokens: {total} / {LIMIT}  ({total / LIMIT:.0%} of budget)")

    if total > LIMIT:
        # Find the term where truncation bites, so the user knows what is lost.
        running, cut = 0, None
        for i, t in enumerate(terms):
            running = len(tok.encode(" " + " ".join(terms[: i + 1]).strip()).ids)
            if running > LIMIT:
                cut = i
                break
        print(f"\nOVER BUDGET - terms from #{cut + 1} ({terms[cut]!r}) onward are dropped:")
        for t in terms[cut:]:
            print(f"  - {t}")
        return 1

    print(f"headroom: {LIMIT - total} tokens")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
