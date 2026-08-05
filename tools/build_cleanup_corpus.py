"""Bootstrap a raw/cleaned corpus for fine-tuning the cleanup model.

FlowLocal's `dataset/` holds ASR pairs - audio plus whisper output. A cleanup
fine-tune needs something different: whisper's raw text paired with the cleaned
text it should have become. This script generates the second half by running a
large teacher model over the raw transcripts and keeping only what
`is_cleanup_preserving()` accepts.

Why bother: a 1B prompted to clean dictation loses roughly half its outputs to
the guard, mostly by answering the dictation instead of cleaning it (see
docs/CLEANUP-TUNING.md). Wispr Flow's answer to the same problem is a
fine-tune, not a better prompt. This is the corpus for that.

Two honest limitations, worth knowing before trusting the output:

  * Guard-filtered teacher output can only teach the student what the guard
    already accepts. It cannot teach cleanups the guard would reject, however
    correct they might be.
  * `rejected` rows are the interesting ones. They are the cases the teacher
    itself got wrong, and they are exactly what wants hand-correction before
    any training run. They are written out for that reason, not as filler.

`status="accepted", edited=false` rows are not waste either - they teach the
model to return text untouched, which is precisely the behaviour small models
fail at.

Usage:
    venv\\Scripts\\python.exe tools\\build_cleanup_corpus.py
    venv\\Scripts\\python.exe tools\\build_cleanup_corpus.py --teacher gemma3:1b --force
"""
import argparse
import json
import pathlib
import sys
import time

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import app  # noqa: E402  - path shim has to run first

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATASET = ROOT / "dataset"
DEFAULT_OUT = ROOT / "dataset_cleanup" / "corpus.jsonl"
# The 7.5B gemma tier: 96% guard acceptance on the benchmark, the best teacher
# available locally. Overridable, but a weaker teacher makes a weaker corpus.
DEFAULT_TEACHER = "flowlocal-cleanup-gemma"


def load_done(out_path):
    """Sources already processed, so a re-run resumes instead of re-querying."""
    if not out_path.exists():
        return {}
    done = {}
    with out_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue  # tolerate a truncated final line from an aborted run
            done[rec["source"]] = rec
    return done


def ask_teacher(raw, teacher, url, timeout):
    r = requests.post(
        f"{url}/api/chat",
        json={
            "model": teacher,
            "messages": [
                {"role": "system", "content": app.CLEANUP_PROMPT},
                {"role": "user", "content": raw},
            ],
            "stream": False,
            "options": {"temperature": 0.1},
        },
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()["message"]["content"].strip()


def build_record(path, teacher, url, timeout):
    raw = path.read_text(encoding="utf-8").strip()
    rec = {
        "source": path.name,
        "raw": raw,
        "cleaned": None,
        "teacher": teacher,
        "status": "error",
        "edited": False,
        "needs_cleaning": app.needs_cleaning(raw),
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    try:
        cleaned = ask_teacher(raw, teacher, url, timeout)
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"
        return rec

    rec["cleaned"] = cleaned
    # Same admission test cleanup() applies at runtime, so the corpus contains
    # only pairs the shipped guard would actually have let through.
    ratio = len(cleaned) / max(len(raw), 1)
    ok = bool(cleaned) and 0.3 < ratio < 3.0 and app.is_cleanup_preserving(raw, cleaned)
    rec["status"] = "accepted" if ok else "rejected"
    rec["edited"] = cleaned.strip().lower() != raw.strip().lower()
    return rec


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--teacher", default=DEFAULT_TEACHER)
    ap.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    ap.add_argument("--limit", type=int, default=0, help="stop after N new samples")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--force", action="store_true", help="re-query sources already done")
    args = ap.parse_args()

    if not DATASET.exists():
        sys.exit(f"no dataset at {DATASET} - enable save_training_data and dictate first")

    sources = [p for p in sorted(DATASET.glob("*.txt"))
               if p.read_text(encoding="utf-8").strip()]
    if not sources:
        sys.exit(f"no non-empty transcripts in {DATASET}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    done = {} if args.force else load_done(args.out)
    todo = [p for p in sources if p.name not in done]
    if args.limit:
        todo = todo[:args.limit]

    url = app.CFG["ollama_url"]
    print(f"teacher {args.teacher} | {len(sources)} transcripts, "
          f"{len(done)} already done, {len(todo)} to run")

    mode = "w" if args.force else "a"
    tally = {"accepted": 0, "rejected": 0, "error": 0}
    edited = 0
    with args.out.open(mode, encoding="utf-8") as f:
        for i, path in enumerate(todo, 1):
            rec = build_record(path, args.teacher, url, args.timeout)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()  # a long run that dies partway keeps everything it earned
            tally[rec["status"]] += 1
            edited += bool(rec["edited"] and rec["status"] == "accepted")
            print(f"  [{i}/{len(todo)}] {path.name:20} {rec['status']}"
                  + (f"  {rec.get('error', '')}" if rec["status"] == "error" else ""))

    n = sum(tally.values())
    if not n:
        print("nothing new to do")
        return
    print(f"\n{args.out}")
    print(f"  accepted {tally['accepted']}/{n} ({tally['accepted']/n*100:.0f}%), "
          f"{edited} of them actually edited")
    print(f"  rejected {tally['rejected']}  <- hand-correct these before training")
    if tally["error"]:
        print(f"  errors   {tally['error']}  <- is Ollama up, and is the teacher pulled?")


if __name__ == "__main__":
    main()
